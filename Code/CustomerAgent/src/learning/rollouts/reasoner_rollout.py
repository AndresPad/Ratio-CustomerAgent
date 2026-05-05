"""AGL rollout wrapper for the reasoner agent.

Uses a synchronous AzureOpenAI client directly (not the MAF agent factory)
for speed and reliability.  The full factory creates all 17 agents + MCP tool
connections just to call one tool-less agent; direct chat completion is ~10x
faster and avoids dependence on the agent-server endpoint.

Targets the APO endpoint (APO_AZURE_OPENAI_ENDPOINT) which is guaranteed
to have working model deployments.
"""
from __future__ import annotations

import logging
import os
import sys

import agentlightning as agl
from openai import AzureOpenAI
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

# Add src to path for prompt loading
_SRC_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "src"))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from learning.rewards import compute_reasoner_reward

logger = logging.getLogger(__name__)

# ── Lazy-init shared sync client ─────────────────────────────
_client: AzureOpenAI | None = None
_model: str = ""


def _get_client() -> tuple[AzureOpenAI, str]:
    """Return (sync_client, model) for rollout LLM calls.

    Uses APO_ env vars from .env for endpoint, API version, and model.
    """
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
        logger.info("Rollout client: endpoint=%s, model=%s", endpoint, _model)
    return _client, _model


@agl.rollout
def reasoner_rollout(
    task: dict,
    prompt_template: agl.PromptTemplate,
) -> float:
    """Execute the reasoner on a hypothesis-evaluation task and return a reward.

    Fully synchronous — no async, no threads.  AGL's SharedMemoryExecutionStrategy
    calls this in a worker thread, so blocking is fine.

    Args:
        task: Dict with 'hypothesis', 'evidence', 'expected_verdict',
              'expected_reasoning' keys.
        prompt_template: The prompt being optimized by APO.

    Returns:
        Reward float in [0.0, 1.0].
    """
    try:
        client, model = _get_client()

        user_text = (
            f"## Hypothesis\n{task['hypothesis']}\n\n"
            f"## Collected Evidence\n{task['evidence']}\n\n"
            "Evaluate the evidence against the hypothesis. "
            "Return your verdict: CONFIRMED, CONTRIBUTING, REFUTED, or needs_more_evidence. "
            "Provide your reasoning."
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

        reward = compute_reasoner_reward(
            hypothesis=task["hypothesis"],
            evidence=task["evidence"],
            agent_output=agent_output,
            expected_verdict=task["expected_verdict"],
            expected_reasoning=task.get("expected_reasoning"),
        )
        logger.info("Rollout reward: %.3f (verdict in output: %s)", reward, task["expected_verdict"])
        return reward

    except Exception as e:
        logger.error("Rollout failed: %s", e)
        return 0.0
