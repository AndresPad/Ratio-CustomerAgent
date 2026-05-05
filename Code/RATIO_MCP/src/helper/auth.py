# Moved from src/auth.py
from __future__ import annotations
import os, json, time, logging, ssl
from typing import Callable
from urllib.request import urlopen
from urllib.error import URLError
import jwt
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, CertificateCredential
from azure.keyvault.secrets import SecretClient
from helper.mcp_logger import MCPLogger, get_current_xcv

logger = logging.getLogger("ratio_mcp.auth")

# ── Shared Identity Configuration ────────────────────────────────────────────
_USER_ASSIGNED_CLIENT_ID = os.getenv("USER_ASSIGNED_CLIENT_ID")
_KEY_VAULT_NAME = os.getenv("KEY_VAULT_NAME")
_CERT_NAME = os.getenv("CERT_NAME")
_AUTH_CLIENT_ID = os.getenv("AUTH_CLIENT_ID")
_AUTH_TENANT_ID = os.getenv("AUTH_TENANT_ID")

_cached_credential = None
_cached_cert_credentials: dict[str, CertificateCredential] = {}


def get_credential():
    """Return the best available TokenCredential (cached).
    Priority: ManagedIdentityCredential → DefaultAzureCredential.
    """
    global _cached_credential
    if _cached_credential is None:
        if _USER_ASSIGNED_CLIENT_ID:
            _cached_credential = ManagedIdentityCredential(client_id=_USER_ASSIGNED_CLIENT_ID)
            logger.info("Auth: using ManagedIdentityCredential (client_id=%s)", _USER_ASSIGNED_CLIENT_ID)
        else:
            _cached_credential = DefaultAzureCredential()
            logger.info("Auth: using DefaultAzureCredential")
    return _cached_credential


def get_cert_credential(*, client_id: str | None = None):
    """Fetch certificate from Key Vault and return CertificateCredential (cached).
    Returns None if required env vars are missing or Key Vault access fails.
    """
    if not _KEY_VAULT_NAME or not _CERT_NAME or not _AUTH_TENANT_ID:
        return None
    cid = client_id or _AUTH_CLIENT_ID
    if not cid:
        return None
    if cid in _cached_cert_credentials:
        return _cached_cert_credentials[cid]
    try:
        vault_cred = get_credential()
        vault_url = f"https://{_KEY_VAULT_NAME}.vault.azure.net"
        secret_client = SecretClient(vault_url=vault_url, credential=vault_cred)
        pem_data = secret_client.get_secret(_CERT_NAME).value
        cert_cred = CertificateCredential(
            tenant_id=_AUTH_TENANT_ID,
            client_id=cid,
            certificate_data=pem_data.encode("utf-8"),
            send_certificate_chain=True,
        )
        _cached_cert_credentials[cid] = cert_cred
        logger.info("Auth: built CertificateCredential (client_id=%s)", cid)
        return cert_cred
    except Exception as e:
        logger.warning("Failed to build CertificateCredential: %s", e)
        return None


def get_token(scope: str, *, cert_client_id: str | None = None) -> str:
    """Get an access token using the full auth chain.

    When *cert_client_id* is provided the CertificateCredential is tried
    **first** so that the caller-specified identity is preferred over the
    environment's default (e.g. a Container-App system-assigned MI that may
    lack access to the target resource).

    Priority when cert_client_id is set:
        CertificateCredential → ManagedIdentity → DefaultAzureCredential
    Priority otherwise:
        ManagedIdentity → DefaultAzureCredential → CertificateCredential
    """
    # ── When an explicit client ID is requested, try cert first ──────────
    if cert_client_id:
        cert_cred = get_cert_credential(client_id=cert_client_id)
        if cert_cred:
            try:
                return cert_cred.get_token(scope).token
            except Exception as e:
                logger.warning("Auth (Cert-first, client_id=%s) failed for '%s': %s",
                               cert_client_id, scope, e)

    # 1. Managed Identity
    if _USER_ASSIGNED_CLIENT_ID:
        try:
            cred = ManagedIdentityCredential(client_id=_USER_ASSIGNED_CLIENT_ID)
            return cred.get_token(scope).token
        except Exception as e:
            logger.warning("Auth step 1 (MI) failed for '%s': %s", scope, e)

    # 2. DefaultAzureCredential
    try:
        cred = DefaultAzureCredential()
        return cred.get_token(scope).token
    except Exception as e:
        logger.warning("Auth step 2 (Default) failed for '%s': %s", scope, e)

    # 3. CertificateCredential (fallback — only if not already tried above)
    if not cert_client_id:
        cert_cred = get_cert_credential(client_id=cert_client_id)
        if cert_cred:
            try:
                return cert_cred.get_token(scope).token
            except Exception as e:
                logger.warning("Auth step 3 (Cert) failed for '%s': %s", scope, e)

    raise ConnectionError(f"All auth methods failed for scope '{scope}'.")


def get_token_provider(scope: str):
    """Return a callable that provides bearer tokens for the given scope.
    Used by Azure OpenAI clients. Priority: MI → DefaultAzureCredential.
    """
    from azure.identity import get_bearer_token_provider as _azure_provider
    return _azure_provider(get_credential(), scope)


OPENID_TEMPLATE = "https://login.microsoftonline.com/{tenant}/v2.0/.well-known/openid-configuration"

JWKS_CACHE: dict[str, dict] = {}
CONFIG_CACHE: dict[str, dict] = {}
CACHE_TTL_SECONDS = 3600


def _normalize_aud(val: str) -> str:
    """Strip the api:// prefix so 'api://GUID' matches bare 'GUID'."""
    return val.removeprefix("api://") if val else val


class AzureAuthMiddleware:
    
    def __init__(self, app: Callable, *, tenant_id: str | None, audience: str | None, allowed_client_ids: set[str], bypass_paths: set[str]):
        self.app = app; self.tenant_id = tenant_id; self.audience = audience; self.allowed_client_ids = allowed_client_ids; self.bypass_paths = bypass_paths
    
    async def __call__(self, scope, receive, send):

        if scope.get("type") != "http":
            return await self.app(scope, receive, send)
        path = scope.get("path", "")
        if path in self.bypass_paths:
            return await self.app(scope, receive, send)
        headers = {k.decode(): v.decode() for k, v in scope.get("headers", [])}
        auth = headers.get("authorization")

        if not auth or not auth.lower().startswith("bearer "):
            return await self._reject(send, path=path, reason="missing bearer token")
        token = auth.split(" ", 1)[1].strip()
        try:
            claims = self._validate_token(token)
        except Exception as e:
            logger.warning("Token validation failed: %s", e)
            return await self._reject(send, path=path, reason="invalid token")
        
        if self.audience:
            token_aud = str(claims.get("aud", ""))
            if _normalize_aud(token_aud) != _normalize_aud(self.audience):
                logger.debug("Audience mismatch: token_aud=%s configured=%s", token_aud, self.audience)
                return await self._reject(send, path=path, reason="audience mismatch")
        if self.tenant_id and str(claims.get("tid")) != self.tenant_id:
            return await self._reject(send, path=path, reason="tenant mismatch")
        if self.allowed_client_ids:
            candidate = str(claims.get("azp") or claims.get("appid") or "")
            if candidate not in self.allowed_client_ids:
                return await self._reject(send, path=path, reason="client not allowed")
        scope["auth_claims"] = claims
        # ── Log successful auth ──
        xcv = get_current_xcv() or ""
        if xcv:
            MCPLogger.get_instance().log_auth(xcv, path, success=True)
        return await self.app(scope, receive, send)
    
    # Delegate attribute access to the wrapped app when present (helps when middleware
    # is used by wrapping instead of app.add_middleware and downstream expects FastAPI attrs).
    def __getattr__(self, name):
        try:
            return getattr(self.app, name)
        except AttributeError:
            raise AttributeError(f"'AzureAuthMiddleware' object has no attribute '{name}'")
    

    async def _reject(self, send, *, path: str = "", reason: str):
        # ── Log failed auth ──
        xcv = get_current_xcv() or ""
        if xcv:
            MCPLogger.get_instance().log_auth(xcv, path, success=False, reason=reason)

        body = json.dumps({"error": "unauthorized", "reason": reason}).encode()
        await send({"type": "http.response.start", "status": 401, "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": body})


    def _validate_token(self, token: str) -> dict:

        if not self.tenant_id:
            raise ValueError(
                "AUTH_TENANT_ID must be configured for token validation. "
                "Deriving tenant from an unverified token is unsafe."
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

        # Accept both bare GUID and api://GUID audience formats so that
        # tokens acquired with either scope variant pass jwt.decode verification.
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

    now = time.time(); cached = CONFIG_CACHE.get(tenant)
    if cached and now - cached.get("_cached", 0) < CACHE_TTL_SECONDS: return cached
    url = OPENID_TEMPLATE.format(tenant=tenant); data = _fetch_json(url); data["_cached"] = now; CONFIG_CACHE[tenant] = data; return data

def _get_jwks_for_tenant(tenant: str) -> dict:

    now = time.time(); cached = JWKS_CACHE.get(tenant)
    if cached and now - cached.get("_cached", 0) < CACHE_TTL_SECONDS: return cached
    cfg = _get_openid_config(tenant); jwks_uri = cfg.get("jwks_uri")
    if not jwks_uri: raise ValueError("jwks_uri missing in openid config")
    jwks = _fetch_json(jwks_uri); jwks["_cached"] = now; JWKS_CACHE[tenant] = jwks; return jwks

def _fetch_json(url: str) -> dict:

    ctx = ssl.create_default_context()
    with urlopen(url, context=ctx) as resp:
        return json.loads(resp.read().decode())

def wrap_app_if_enabled(app):

    if os.getenv("AUTH_ENABLED", "false").lower() != "true":
        logger.info("Auth middleware disabled (AUTH_ENABLED not true).")
        return app
    tenant_id = os.getenv("AUTH_TENANT_ID")
    audience = os.getenv("MCP_AUTH_AUDIENCE")
    allowed_client_ids = {c.strip() for c in os.getenv("AUTH_ALLOWED_CLIENT_IDS", "").split(",") if c.strip()}
    bypass_paths = {p.strip() for p in os.getenv("AUTH_BYPASS_PATHS", "").split(",") if p.strip()}
    logger.info("Auth middleware enabled. audience=%s tenant=%s allowed_client_ids=%s bypass=%s", audience, tenant_id, allowed_client_ids, bypass_paths)
    
    # Prefer integrating via Starlette/FastAPI middleware API to preserve app attributes like `.routes`.
    try:
        if hasattr(app, "add_middleware") and callable(getattr(app, "add_middleware")):
            app.add_middleware(
                AzureAuthMiddleware,
                tenant_id=tenant_id,
                audience=audience,
                allowed_client_ids=allowed_client_ids,
                bypass_paths=bypass_paths,
            )
            return app
    except Exception as e:
        logger.debug("Falling back to direct wrapping for auth middleware: %s", e)
    
    # Fallback: wrap the ASGI app directly.
    return AzureAuthMiddleware(app, tenant_id=tenant_id, audience=audience, allowed_client_ids=allowed_client_ids, bypass_paths=bypass_paths)

__all__ = [
    "AzureAuthMiddleware", "wrap_app_if_enabled",
    "get_credential", "get_cert_credential", "get_token", "get_token_provider",
]
