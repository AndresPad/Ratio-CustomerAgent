"""
LLM client factory for MAF agents.

Creates Azure OpenAI chat clients used by MAF Agent instances.

Authentication priority:
  1. User-assigned Managed Identity (in Azure — IDENTITY_ENDPOINT + MIDDLEWARE_AUTH_CLIENT_ID)
  2. System Managed Identity (in Azure Container Apps — auto-detected via IDENTITY_ENDPOINT)
  3. CertificateCredential (Key Vault cert — requires AUTH_TENANT_ID, AUTH_CLIENT_ID,
     KEY_VAULT_NAME, CERT_NAME)
  4. DefaultAzureCredential (local dev — az login)

Migration note: shared/clients/chat_client.py provides a FoundryChatClient-based
implementation. This file uses OpenAIChatCompletionClient from agent_framework.openai,
which is the MAF-native pattern. Migrate when shared client supports the same interface.
"""
from __future__ import annotations

import logging
import os

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

logger = logging.getLogger(__name__)

# Cached credential — created once, reused for all chat clients.
_credential = None


def _create_credential():
    """Return Azure credential using a 4-tier priority chain.

    1. User-assigned Managed Identity — if IDENTITY_ENDPOINT + MIDDLEWARE_AUTH_CLIENT_ID are set
    2. System Managed Identity        — if IDENTITY_ENDPOINT is set (Azure runtime)
    3. CertificateCredential          — if Key Vault cert config is present
    4. DefaultAzureCredential         — fallback (local dev via az login)
    """
    global _credential
    if _credential is not None:
        return _credential

    # LLM_AUTH_METHOD override: "mi" (default), "cert", "default"
    # Use to bypass Managed Identity when MI token is rejected by the target service.
    auth_method = os.getenv("LLM_AUTH_METHOD", "").strip().lower()

    has_mi_env = bool(os.getenv("IDENTITY_ENDPOINT"))
    mi_client_id = os.getenv("MIDDLEWARE_AUTH_CLIENT_ID", "").strip()
    has_user_mi = mi_client_id and not mi_client_id.startswith("<")

    # ── 1. User-assigned Managed Identity ────────────────────────────────────
    if auth_method not in ("cert", "default") and has_mi_env and has_user_mi:
        logger.info("LLM credential: User-assigned Managed Identity (client_id=%s…)", mi_client_id[:8])
        _credential = ManagedIdentityCredential(client_id=mi_client_id)
        return _credential

    # ── 2. System Managed Identity (auto-injected by Container Apps) ─────────
    if auth_method not in ("cert", "default") and has_mi_env:
        logger.info("LLM credential: System Managed Identity")
        _credential = ManagedIdentityCredential()
        return _credential

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

                _credential = CertificateCredential(
                    tenant_id=tenant_id,
                    client_id=client_id,
                    certificate_data=cert_bytes,
                    send_certificate_chain=True,
                )
                logger.info(
                    "LLM credential: CertificateCredential (tenant=%s…, client=%s…, cert=%s)",
                    tenant_id[:8], client_id[:8], cert_name,
                )
                return _credential
        except Exception as exc:
            logger.warning("LLM credential: CertificateCredential failed — %s. Falling back.", exc)

    # ── 3. DefaultAzureCredential (local dev: az login) ──────────────────────
    logger.info("LLM credential: DefaultAzureCredential (local dev)")
    _credential = DefaultAzureCredential()
    return _credential


def create_chat_client(model: str | None = None):
    """Create an AzureOpenAI-compatible chat client for MAF agents.

    Auth is handled directly via azure-identity + openai SDK, bypassing
    MAF's internal credential resolution. A pre-authenticated AsyncAzureOpenAI
    client is passed to OpenAIChatCompletionClient.

    Args:
        model: Optional model deployment name override. When provided, uses
               this instead of the AZURE_OPENAI_GPT_MODEL_DEPLOYMENT_NAME env var.

    Returns:
        An OpenAIChatCompletionClient instance for use with MAF Agent.
    """
    from azure.identity import get_bearer_token_provider
    from openai import AsyncAzureOpenAI

    from agent_framework.openai import OpenAIChatCompletionClient

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    deployment = model or os.getenv("AZURE_OPENAI_GPT_MODEL_DEPLOYMENT_NAME", "gpt-4o")

    credential = _create_credential()

    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )

    azure_client = AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        api_version=api_version,
        azure_deployment=deployment,
        azure_ad_token_provider=token_provider,
    )

    client = OpenAIChatCompletionClient(
        async_client=azure_client,
        model=deployment,
    )

    logger.info("Created OpenAIChatCompletionClient (pre-auth) → %s / %s", endpoint, deployment)
    return client

