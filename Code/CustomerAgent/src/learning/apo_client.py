"""Azure OpenAI client factory for APO training.

Creates an AsyncAzureOpenAI client authenticated via DefaultAzureCredential
for use with AgentLightning's APO optimizer.

Uses a pre-fetched token instead of a token provider callback to avoid
KeyboardInterrupt issues when AGL's threaded execution invokes az CLI
subprocesses for token refresh.
"""

from __future__ import annotations

import logging
import os

from azure.identity import DefaultAzureCredential
from openai import AsyncAzureOpenAI

logger = logging.getLogger(__name__)


def create_azure_openai_client() -> AsyncAzureOpenAI:
    """Create an AsyncAzureOpenAI client using a pre-fetched AD token.

    Pre-fetches the token before returning the client so that AGL's
    threaded/async execution never needs to spawn az CLI subprocesses
    for token refresh (which get killed by KeyboardInterrupt signals).

    Environment variables (all required in .env):
        APO_AZURE_OPENAI_ENDPOINT: Azure OpenAI endpoint for all APO calls.
        APO_AZURE_OPENAI_API_VERSION: API version.
        APO_MODEL: Deployment name for gradient/edit models (used by run_apo.py).
    """
    credential = DefaultAzureCredential()

    # Pre-fetch the token now (in the main thread, before AGL spawns workers)
    scope = "https://cognitiveservices.azure.com/.default"
    token = credential.get_token(scope)
    logger.info("Pre-fetched Azure AD token (expires in %d seconds)", token.expires_on - __import__('time').time())

    endpoint = os.environ["APO_AZURE_OPENAI_ENDPOINT"]
    api_version = os.environ["APO_AZURE_OPENAI_API_VERSION"]

    return AsyncAzureOpenAI(
        azure_endpoint=endpoint,
        azure_ad_token=token.token,
        api_version=api_version,
    )
