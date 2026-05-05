"""AGL rollout wrapper for the entity extractor agent.

Uses a synchronous AzureOpenAI client directly (not the MAF agent factory).
"""
from __future__ import annotations

import logging
import os
import sys

import agentlightning as agl
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from learning.rewards import compute_entity_extractor_reward

logger = logging.getLogger(__name__)

_client: AzureOpenAI | None = None
_model: str = ""


def _get_client() -> tuple[AzureOpenAI, str]:
    global _client, _model
    if _client is None:
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        endpoint = os.environ["APO_AZURE_OPENAI_ENDPOINT"]
        api_version = os.environ["APO_AZURE_OPENAI_API_VERSION"]
        _model = os.environ["APO_MODEL"]
        _client = AzureOpenAI(
            azure_endpoint=endpoint,
            azure_ad_token_provider=token_provider,
            api_version=api_version,
        )
        logger.info("Entity extractor rollout client: endpoint=%s, model=%s", endpoint, _model)
    return _client, _model


@agl.rollout
def entity_extractor_rollout(
    task: dict,
    prompt_template: agl.PromptTemplate,
) -> float:
    """Execute the entity extractor on a query and return a reward.

    Fully synchronous — no async, no threads.

    Args:
        task: Dict with 'input', 'expected_entities', 'expected_output' keys.
        prompt_template: The prompt being optimized by APO.

    Returns:
        Reward float in [0.0, 1.0].
    """
    try:
        client, model = _get_client()

        user_text = (
            f"Extract all entities from the following query.\n\n"
            f"Query: {task['input']}\n\n"
            f"Identify and normalize: service names, Azure regions, and customer names."
        )

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": prompt_template.template},
                {"role": "user", "content": user_text},
            ],
            temperature=1.0,
        )
        agent_output = response.choices[0].message.content or ""

        reward = compute_entity_extractor_reward(
            query=task["input"],
            agent_output=agent_output,
            expected_entities=task["expected_entities"],
            expected_output=task.get("expected_output"),
        )
        logger.info("Entity extractor rollout reward: %.3f", reward)
        return reward

    except Exception as e:
        logger.error("Entity extractor rollout failed: %s", e)
        return 0.0
