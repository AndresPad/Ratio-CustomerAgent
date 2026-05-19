"""
LLM client factory for Interpreter agents.

Creates Azure OpenAI chat clients compatible with agent_framework.Agent.
Simplified version of CustomerAgent's helper/llm.py — uses
DefaultAzureCredential for local dev and Managed Identity in Azure.
"""
from __future__ import annotations

import logging
import os

from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider
from openai import AsyncAzureOpenAI
from agent_framework.openai import OpenAIChatCompletionClient

logger = logging.getLogger(__name__)

_credential = None


def _get_credential():
    """Get or create Azure credential (MI in Azure, DefaultAzureCredential locally)."""
    global _credential
    if _credential is not None:
        return _credential

    mi_client_id = os.getenv("MIDDLEWARE_AUTH_CLIENT_ID", "").strip()
    has_mi = bool(os.getenv("IDENTITY_ENDPOINT"))

    if has_mi and mi_client_id:
        _credential = ManagedIdentityCredential(client_id=mi_client_id)
        logger.info("LLM credential: User-assigned Managed Identity")
    elif has_mi:
        _credential = ManagedIdentityCredential()
        logger.info("LLM credential: System Managed Identity")
    else:
        _credential = DefaultAzureCredential()
        logger.info("LLM credential: DefaultAzureCredential (local dev)")

    return _credential


def create_chat_client(model: str | None = None) -> OpenAIChatCompletionClient:
    """Create an OpenAIChatCompletionClient for MAF agents.

    Args:
        model: Optional model deployment name override.

    Returns:
        OpenAIChatCompletionClient ready for use with agent_framework.Agent.
    """
    endpoint = os.getenv("INTERPRETER_AZURE_OPENAI_ENDPOINT", os.getenv("AZURE_OPENAI_ENDPOINT", ""))
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
    deployment = model or os.getenv("INTERPRETER_AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

    credential = _get_credential()
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

    logger.info("Created chat client → %s / %s", endpoint, deployment)
    return client
