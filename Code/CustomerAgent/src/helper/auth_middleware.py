"""Azure AD bearer-token authentication for inbound CustomerAgent requests.

Ported from RATIO_MCP/src/helper/auth.py (server-side validation half).
Validates bearer tokens issued by Microsoft Entra ID against the configured
tenant/audience/allowed-clients using JWKS (no shared secrets).

Auth modes:
    * Off (default): set CUSTOMERAGENT_AUTH_ENABLED=false (or unset).
    * On (allowlist mode): set CUSTOMERAGENT_AUTH_ENABLED=true and list
      protected paths in CUSTOMERAGENT_AUTH_PROTECTED_PATHS. Only those
      exact paths require a valid bearer token. All other paths pass
      through unauthenticated. Default protected list = ``/api/run/services``.
    * On (deny-by-default mode): set CUSTOMERAGENT_AUTH_ENABLED=true and
      explicitly set CUSTOMERAGENT_AUTH_PROTECTED_PATHS= (empty). Every
      request requires auth except those listed in
      CUSTOMERAGENT_AUTH_BYPASS_PATHS.

Required env vars when enabled:
    AUTH_TENANT_ID                          Entra tenant GUID
    CUSTOMERAGENT_AUTH_AUDIENCE             App ID URI / GUID accepted as `aud`
    CUSTOMERAGENT_AUTH_ALLOWED_CLIENT_IDS   Comma-separated accepted callers

Optional:
    CUSTOMERAGENT_AUTH_PROTECTED_PATHS      Allowlist (default: /api/run/services)
    CUSTOMERAGENT_AUTH_BYPASS_PATHS         Bypass list when no allowlist

Caller authentication patterns:
    * Managed identity  -- caller acquires a token for `audience` from IMDS;
      its system/user-assigned MI app id is the `appid` claim. Add that MI
      object's app id to CUSTOMERAGENT_AUTH_ALLOWED_CLIENT_IDS.
    * App + certificate -- caller uses ConfidentialClientApplication or
      CertificateCredential against the same audience; its registered
      app id appears in `appid`/`azp`. Add it to the allowed list.
"""
from __future__ import annotations

import json
import logging
import os
import ssl
import time
from typing import Callable
from urllib.request import urlopen

import jwt

logger = logging.getLogger(__name__)

OPENID_TEMPLATE = (
    "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"
)
CACHE_TTL_SECONDS = 3600
JWKS_CACHE: dict[str, dict] = {}
CONFIG_CACHE: dict[str, dict] = {}


def _normalize_aud(val: str) -> str:
    """Strip the api:// prefix so 'api://GUID' matches bare 'GUID'."""
    return val.removeprefix("api://") if val else val


class AzureAuthMiddleware:
    """ASGI middleware that validates Microsoft Entra bearer tokens.

    See module docstring for the two enforcement modes (allowlist vs
    deny-by-default).
    """

    def __init__(
        self,
        app: Callable,
        *,
        tenant_id: str | None,
        audience: str | None,
        allowed_client_ids: set[str],
        protected_paths: set[str],
        bypass_paths: set[str],
    ):
        self.app = app
        self.tenant_id = tenant_id
        self.audience = audience
        self.allowed_client_ids = allowed_client_ids
        self.protected_paths = protected_paths
        self.bypass_paths = bypass_paths

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        path = scope.get("path", "")

        # Mode 1: explicit allowlist -- only listed paths require auth.
        if self.protected_paths:
            if path not in self.protected_paths:
                return await self.app(scope, receive, send)
        # Mode 2: deny-by-default -- bypass only listed paths.
        elif path in self.bypass_paths:
            return await self.app(scope, receive, send)

        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization")

        if not auth or not auth.lower().startswith("bearer "):
            return await self._reject(send, path=path, reason="missing bearer token")
        token = auth.split(" ", 1)[1].strip()

        try:
            claims = self._validate_token(token)
        except Exception as e:
            logger.warning("Token validation failed for %s: %s", path, e)
            return await self._reject(send, path=path, reason="invalid token")

        if self.audience:
            token_aud = str(claims.get("aud", ""))
            if _normalize_aud(token_aud) != _normalize_aud(self.audience):
                logger.debug(
                    "Audience mismatch on %s: token_aud=%s configured=%s",
                    path, token_aud, self.audience,
                )
                return await self._reject(send, path=path, reason="audience mismatch")

        if self.tenant_id and str(claims.get("tid")) != self.tenant_id:
            return await self._reject(send, path=path, reason="tenant mismatch")

        if self.allowed_client_ids:
            candidate = str(claims.get("azp") or claims.get("appid") or "")
            if candidate not in self.allowed_client_ids:
                logger.warning(
                    "Caller client_id=%s not in allowed list for %s",
                    candidate, path,
                )
                return await self._reject(send, path=path, reason="client not allowed")

        scope["auth_claims"] = claims
        return await self.app(scope, receive, send)

    def __getattr__(self, name):
        try:
            return getattr(self.app, name)
        except AttributeError:
            raise AttributeError(
                f"'AzureAuthMiddleware' object has no attribute '{name}'"
            )

    async def _reject(self, send, *, path: str = "", reason: str):
        body = json.dumps({"error": "unauthorized", "reason": reason}).encode()
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})

    def _validate_token(self, token: str) -> dict:
        if not self.tenant_id:
            raise ValueError(
                "AUTH_TENANT_ID must be configured for token validation."
            )
        tenant = self.tenant_id

        jwks = _get_jwks_for_tenant(tenant)
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = None
        for k in jwks.get("keys", []):
            if k.get("kid") == kid:
                from jwt.algorithms import RSAAlgorithm
                key = RSAAlgorithm.from_jwk(json.dumps(k))
                break
        if key is None:
            raise ValueError("matching jwk not found")

        expected_issuer = f"https://login.microsoftonline.com/{tenant}/v2.0"

        if self.audience:
            bare = _normalize_aud(self.audience)
            accepted_audiences = [bare, f"api://{bare}"]
        else:
            accepted_audiences = None

        return jwt.decode(
            token,
            key=key,
            algorithms=["RS256"],
            audience=accepted_audiences,
            issuer=expected_issuer,
            options={
                "verify_aud": bool(self.audience),
                "verify_iss": True,
            },
        )


def _get_openid_config(tenant: str) -> dict:
    now = time.time()
    cached = CONFIG_CACHE.get(tenant)
    if cached and now - cached.get("_cached", 0) < CACHE_TTL_SECONDS:
        return cached
    data = _fetch_json(OPENID_TEMPLATE.format(tenant=tenant))
    data["_cached"] = now
    CONFIG_CACHE[tenant] = data
    return data


def _get_jwks_for_tenant(tenant: str) -> dict:
    now = time.time()
    cached = JWKS_CACHE.get(tenant)
    if cached and now - cached.get("_cached", 0) < CACHE_TTL_SECONDS:
        return cached
    cfg = _get_openid_config(tenant)
    jwks_uri = cfg.get("jwks_uri")
    if not jwks_uri:
        raise ValueError("jwks_uri missing in openid config")
    jwks = _fetch_json(jwks_uri)
    jwks["_cached"] = now
    JWKS_CACHE[tenant] = jwks
    return jwks


def _fetch_json(url: str) -> dict:
    ctx = ssl.create_default_context()
    with urlopen(url, context=ctx) as resp:  # noqa: S310 -- fixed Microsoft URL
        return json.loads(resp.read().decode())


def wrap_app_if_enabled(app):
    """Attach AzureAuthMiddleware to *app* when CUSTOMERAGENT_AUTH_ENABLED=true.

    Returns the (possibly wrapped) app. Safe to call unconditionally during
    FastAPI app setup.
    """
    if os.getenv("CUSTOMERAGENT_AUTH_ENABLED", "false").lower() != "true":
        logger.info("Auth middleware disabled (CUSTOMERAGENT_AUTH_ENABLED!=true).")
        return app

    tenant_id = os.getenv("AUTH_TENANT_ID")
    audience = os.getenv("CUSTOMERAGENT_AUTH_AUDIENCE")
    allowed_client_ids = {
        c.strip()
        for c in os.getenv("CUSTOMERAGENT_AUTH_ALLOWED_CLIENT_IDS", "").split(",")
        if c.strip()
    }
    protected_paths = {
        p.strip()
        for p in os.getenv(
            "CUSTOMERAGENT_AUTH_PROTECTED_PATHS",
            "/api/run/services",
        ).split(",")
        if p.strip()
    }
    bypass_paths = {
        p.strip()
        for p in os.getenv(
            "CUSTOMERAGENT_AUTH_BYPASS_PATHS",
            "/health,/metrics",
        ).split(",")
        if p.strip()
    }

    if not tenant_id or not audience:
        raise RuntimeError(
            "CUSTOMERAGENT_AUTH_ENABLED=true requires AUTH_TENANT_ID and "
            "CUSTOMERAGENT_AUTH_AUDIENCE.",
        )

    logger.info(
        "Auth middleware enabled. audience=%s tenant=%s allowed_client_ids=%s "
        "protected=%s bypass=%s",
        audience, tenant_id, allowed_client_ids, protected_paths, bypass_paths,
    )

    try:
        if hasattr(app, "add_middleware"):
            app.add_middleware(
                AzureAuthMiddleware,
                tenant_id=tenant_id,
                audience=audience,
                allowed_client_ids=allowed_client_ids,
                protected_paths=protected_paths,
                bypass_paths=bypass_paths,
            )
            return app
    except Exception as e:
        logger.debug("Falling back to direct ASGI wrapping for auth: %s", e)

    return AzureAuthMiddleware(
        app,
        tenant_id=tenant_id,
        audience=audience,
        allowed_client_ids=allowed_client_ids,
        protected_paths=protected_paths,
        bypass_paths=bypass_paths,
    )


__all__ = ["AzureAuthMiddleware", "wrap_app_if_enabled"]
