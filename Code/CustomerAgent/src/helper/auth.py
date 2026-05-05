"""
Token management and auth helpers for MAF GroupChat.

Includes:
  - User-token pass-through (ContextVar for SQL-scoped tokens)
  - Middleware service-to-service token acquisition
    Priority: User-assigned Managed Identity → DefaultAzureCredential
  - MCP bearer token acquisition via CertificateCredential (Key Vault cert)
    Fallback: DefaultAzureCredential

Migration note: shared/config/settings.py provides BaseSettings-based config.
This file is richer (SSO, MCP bearer, managed identity) and stays as-is.
Generic credential logic could move to shared in future.
"""
from __future__ import annotations

import logging
import os
import time
from contextvars import ContextVar
from typing import Any, Optional

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

logger = logging.getLogger(__name__)

# ── User token ContextVar ────────────────────────────────────────────────────
# The SQL-scoped user token is set by the UI before invoking the workflow.
# MCP tool calls include this token as X-User-Token header for SQL auth.
user_token_var: ContextVar[Optional[str]] = ContextVar("user_token_var", default=None)


def set_user_token(token: str | None) -> None:
    """Store the SQL-scoped user token for the current async context."""
    user_token_var.set(token)


def get_user_token() -> str | None:
    """Retrieve the SQL-scoped user token from the current async context."""
    return user_token_var.get()


# ── Service-to-service credential (Managed Identity / DefaultAzureCredential) ─
_raw_mi_client_id = os.getenv("MIDDLEWARE_AUTH_CLIENT_ID", "").strip()
# Guard against placeholder values that were never replaced
_MANAGED_IDENTITY_CLIENT_ID = (
    _raw_mi_client_id
    if _raw_mi_client_id and not _raw_mi_client_id.startswith("<")
    else ""
)
if _raw_mi_client_id and not _MANAGED_IDENTITY_CLIENT_ID:
    logging.getLogger(__name__).warning(
        "MIDDLEWARE_AUTH_CLIENT_ID looks like a placeholder ('%s') — ignoring. "
        "Will fall back to DefaultAzureCredential.",
        _raw_mi_client_id,
    )

# Lazy-initialised credential (created on first call, reused afterwards).
_credential = None


def _get_credential():
    """Return a cached credential instance, creating it on first call."""
    global _credential
    if _credential is not None:
        return _credential

    if _MANAGED_IDENTITY_CLIENT_ID:
        logger.info(
            "Middleware auth: using User-assigned Managed Identity (client_id=%s…)",
            _MANAGED_IDENTITY_CLIENT_ID[:8],
        )
        _credential = ManagedIdentityCredential(client_id=_MANAGED_IDENTITY_CLIENT_ID)
    else:
        logger.info("Middleware auth: using DefaultAzureCredential")
        _credential = DefaultAzureCredential()

    return _credential


def get_auth_token(scope: str) -> str | None:
    """Acquire a Bearer token for *scope*.

    Returns the token string on success, or ``None`` if credentials are
    unavailable so the caller can proceed without an Authorization header.
    """
    if not scope:
        return None

    try:
        credential = _get_credential()
        token = credential.get_token(scope)
        return token.token
    except Exception as exc:
        logger.warning("Middleware auth: failed to acquire token for %s — %s", scope, exc)
        return None


# ── MCP bearer token (CertificateCredential → DefaultAzureCredential) ────────
MCP_AUTH_AUDIENCE = os.getenv("MCP_AUTH_AUDIENCE", "")

_MCP_TOKEN_CACHE: dict[str, Any] = {}
_MCP_TOKEN_SLACK_SECONDS = 120


def get_mcp_bearer_token() -> str | None:
    """Acquire a bearer token for the MCP server.

    Priority:
      1. User-assigned Managed Identity (via _get_credential)
      2. DefaultAzureCredential (via _get_credential)
      3. CertificateCredential (Key Vault cert)
    """
    aud = MCP_AUTH_AUDIENCE
    if not aud:
        logger.warning("MCP_AUTH_AUDIENCE not set; skipping bearer token.")
        return None

    scope = f"{aud}/.default" if not aud.endswith("/.default") else aud
    now = time.time()

    # Return cached token if still valid
    if (_MCP_TOKEN_CACHE.get("token")
            and now < _MCP_TOKEN_CACHE.get("expires_on", 0) - _MCP_TOKEN_SLACK_SECONDS):
        return _MCP_TOKEN_CACHE["token"]

    # Log when refreshing an expired token (vs first acquisition)
    if _MCP_TOKEN_CACHE.get("token"):
        logger.info(
            "MCP bearer token expired (expires_on=%.0f, now=%.0f) — refreshing",
            _MCP_TOKEN_CACHE.get("expires_on", 0), now,
        )

    # ── 1 & 2. Managed Identity / DefaultAzureCredential ────────────────────
    # Build scope candidates: bare GUID/.default and api://GUID/.default
    scope_candidates = [scope]
    if "://" not in aud:
        scope_candidates.append(f"api://{aud}/.default")

    credential = _get_credential()
    for sc in scope_candidates:
        try:
            token_obj = credential.get_token(sc)
            if token_obj and token_obj.token:
                expires_on = getattr(token_obj, "expires_on", None) or (now + 3600)
                _MCP_TOKEN_CACHE.update({"token": token_obj.token, "expires_on": expires_on})
                logger.info("MCP bearer token acquired via %s (scope=%s)", type(credential).__name__, sc)
                return token_obj.token
        except Exception as e:
            logger.debug("%s failed for MCP scope %s: %s", type(credential).__name__, sc, e)

    # ── 3. Certificate credential (client-credentials grant via KV cert) ─────
    tenant_id = os.getenv("AUTH_TENANT_ID")
    client_id = os.getenv("AUTH_CLIENT_ID")
    key_vault_name = os.getenv("KEY_VAULT_NAME")
    cert_name = os.getenv("CERT_NAME")

    if all([tenant_id, client_id, key_vault_name, cert_name]):
        try:
            from azure.identity import CertificateCredential
            from azure.keyvault.secrets import SecretClient

            kv_url = f"https://{key_vault_name}.vault.azure.net/"
            kv_cred = DefaultAzureCredential()
            secret_client = SecretClient(vault_url=kv_url, credential=kv_cred)
            secret_value = secret_client.get_secret(cert_name).value

            if secret_value:
                if "BEGIN CERTIFICATE" in secret_value:
                    cert_bytes = secret_value.encode("utf-8")
                else:
                    from base64 import b64decode
                    try:
                        cert_bytes = b64decode(secret_value)
                    except Exception:
                        cert_bytes = secret_value.encode("utf-8")

                cert_cred = CertificateCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    certificate_data=cert_bytes,
                    send_certificate_chain=True,
                )

                # Try scope variants
                scope_candidates = [scope]
                if "://" not in aud:
                    scope_candidates.append(f"api://{aud}/.default")

                for sc in scope_candidates:
                    try:
                        token_obj = cert_cred.get_token(sc)
                        if token_obj and token_obj.token:
                            expires_on = getattr(token_obj, "expires_on", None) or (now + 3600)
                            _MCP_TOKEN_CACHE.update({
                                "token": token_obj.token,
                                "expires_on": expires_on,
                            })
                            logger.info("MCP bearer token acquired via CertificateCredential (scope=%s)", sc)
                            return token_obj.token
                    except Exception as e:
                        logger.debug("CertificateCredential failed for scope %s: %s", sc, e)
        except Exception as e:
            logger.warning("Certificate credential flow failed: %s", e)

    logger.warning(
        "All MCP bearer token acquisition methods failed — "
        "MCP requests will be unauthenticated and may return 401. "
        "Check MCP_AUTH_AUDIENCE, managed identity, and certificate config."
    )
    return None
