"""Reward function library for Agent Lightning prompt optimization.

Computes composite reward signals for the reasoner agent by combining
verdict accuracy (exact match) with reasoning quality (DeepEval).
Used by AGL rollout wrappers (F07) during prompt optimization training.
"""
from __future__ import annotations

import logging
import os

from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from deepeval.metrics import AnswerRelevancyMetric
from deepeval.models import AzureOpenAIModel
from deepeval.test_case import LLMTestCase

logger = logging.getLogger(__name__)


def _create_deepeval_model() -> AzureOpenAIModel:
    """Create an AzureOpenAIModel for DeepEval metrics.

    Uses APO_ env vars from .env for endpoint, API version, and model.
    """
    credential = DefaultAzureCredential()
    token_provider = get_bearer_token_provider(
        credential, "https://cognitiveservices.azure.com/.default"
    )
    deployment = os.environ["APO_MODEL"]
    endpoint = os.environ["APO_AZURE_OPENAI_ENDPOINT"]
    api_version = os.environ["APO_AZURE_OPENAI_API_VERSION"]
    return AzureOpenAIModel(
        model=deployment,
        deployment_name=deployment,
        azure_ad_token_provider=token_provider,
        base_url=endpoint,
        api_version=api_version,
    )

VALID_VERDICTS = {"CONFIRMED", "CONTRIBUTING", "REFUTED", "needs_more_evidence"}

_VERDICT_WEIGHT = 0.6
_REASONING_WEIGHT = 0.4


def _check_verdict(agent_output: str, expected_verdict: str) -> float:
    """Return 1.0 if expected verdict appears in agent output, 0.0 otherwise."""
    output_lower = agent_output.lower()
    expected_lower = expected_verdict.lower()

    # Handle needs_more_evidence with underscore/space variants
    if expected_lower == "needs_more_evidence":
        variants = ["needs_more_evidence", "needs more evidence"]
        return 1.0 if any(v in output_lower for v in variants) else 0.0

    return 1.0 if expected_lower in output_lower else 0.0


def _evaluate_reasoning(
    hypothesis: str,
    evidence: str,
    agent_output: str,
    expected_output: str,
) -> float:
    """Score reasoning quality via DeepEval AnswerRelevancyMetric (0.0–1.0)."""
    test_case = LLMTestCase(
        input=f"Hypothesis: {hypothesis}\nEvidence: {evidence}",
        actual_output=agent_output,
        expected_output=expected_output,
    )
    model = _create_deepeval_model()
    metric = AnswerRelevancyMetric(threshold=0.5, model=model)
    metric.measure(test_case)
    return max(0.0, min(1.0, metric.score))


def compute_reasoner_reward(
    hypothesis: str,
    evidence: str,
    agent_output: str,
    expected_verdict: str,
    expected_reasoning: str | None = None,
) -> float:
    """Compute a composite reward for reasoner agent output.

    Args:
        hypothesis: The hypothesis being evaluated.
        evidence: Collected evidence.
        agent_output: The agent's full response.
        expected_verdict: The correct verdict (one of VALID_VERDICTS).
        expected_reasoning: Optional reference reasoning for quality scoring.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 on any failure.
    """
    try:
        verdict_score = _check_verdict(agent_output, expected_verdict)

        try:
            reference = expected_reasoning if expected_reasoning else expected_verdict
            reasoning_score = _evaluate_reasoning(
                hypothesis, evidence, agent_output, reference
            )
        except Exception:
            logger.warning(
                "DeepEval reasoning evaluation failed; using 0.0 for reasoning score"
            )
            reasoning_score = 0.0

        reward = (_VERDICT_WEIGHT * verdict_score) + (_REASONING_WEIGHT * reasoning_score)
        return max(0.0, min(1.0, reward))
    except Exception:
        logger.warning("Reward computation failed; returning 0.0")
        return 0.0


# ── Entity Extractor Reward (F10) ────────────────────────────

_ENTITY_ACCURACY_WEIGHT = 0.7
_ENTITY_RELEVANCY_WEIGHT = 0.3


def _compute_entity_accuracy(agent_output: str, expected_entities: dict) -> float:
    """Return fraction of expected entities found in agent output."""
    output_lower = agent_output.lower()
    total = 0
    matched = 0
    for category in ("services", "regions", "customers"):
        for entity in expected_entities.get(category, []):
            total += 1
            if entity.lower() in output_lower:
                matched += 1
    if total == 0:
        return 1.0
    return matched / total


def compute_entity_extractor_reward(
    query: str,
    agent_output: str,
    expected_entities: dict,
    expected_output: str | None = None,
) -> float:
    """Compute a composite reward for entity extractor output.

    Scoring: 70% entity extraction accuracy, 30% DeepEval relevancy.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 on any failure.
    """
    try:
        accuracy_score = _compute_entity_accuracy(agent_output, expected_entities)

        try:
            reference = expected_output if expected_output else str(expected_entities)
            relevancy_score = _evaluate_reasoning(
                query, str(expected_entities), agent_output, reference
            )
        except Exception:
            logger.warning(
                "DeepEval relevancy evaluation failed for entity extractor; using 0.0"
            )
            relevancy_score = 0.0

        reward = (_ENTITY_ACCURACY_WEIGHT * accuracy_score) + (_ENTITY_RELEVANCY_WEIGHT * relevancy_score)
        return max(0.0, min(1.0, reward))
    except Exception:
        logger.warning("Entity extractor reward computation failed; returning 0.0")
        return 0.0


# ── Analyst Reward (F11 — shared by AIRO, Outage, Customer Insights) ─

_SQL_QUALITY_WEIGHT = 0.5
_ANSWER_COMPLETENESS_WEIGHT = 0.2
_ANALYST_RELEVANCY_WEIGHT = 0.3

_SQL_STRUCTURE_KEYWORDS = {"select", "from", "where", "group by", "order by", "join", "having", "count", "avg", "sum"}


def _compute_sql_quality(agent_output: str, expected_sql: str) -> float:
    """Score SQL quality by checking expected table/column names and structure."""
    output_lower = agent_output.lower()
    expected_lower = expected_sql.lower()

    # Extract expected table and column names (words after FROM, JOIN, SELECT)
    expected_tokens = set()
    for word in expected_lower.split():
        cleaned = word.strip("(),;'\"")
        if len(cleaned) > 2 and cleaned not in _SQL_STRUCTURE_KEYWORDS and not cleaned.startswith("dateadd"):
            expected_tokens.add(cleaned)

    if not expected_tokens:
        return 0.5

    matched = sum(1 for token in expected_tokens if token in output_lower)
    token_score = matched / len(expected_tokens)

    # Check SQL structure indicators
    structure_matches = sum(1 for kw in _SQL_STRUCTURE_KEYWORDS if kw in output_lower)
    structure_score = min(1.0, structure_matches / 3.0)

    return 0.7 * token_score + 0.3 * structure_score


def _compute_answer_completeness(agent_output: str, expected_output: str) -> float:
    """Score answer completeness by checking key terms from expected output."""
    if not expected_output:
        return 0.5

    output_lower = agent_output.lower()
    # Extract meaningful words (4+ chars) from expected output
    expected_words = set()
    for word in expected_output.lower().split():
        cleaned = word.strip("(),;:.%'\"")
        if len(cleaned) >= 4 and not cleaned.isdigit():
            expected_words.add(cleaned)

    if not expected_words:
        return 0.5

    matched = sum(1 for w in expected_words if w in output_lower)
    return matched / len(expected_words)


def compute_analyst_reward(
    query: str,
    agent_output: str,
    expected_sql: str,
    expected_output: str | None = None,
) -> float:
    """Compute a composite reward for analyst agent output.

    Shared by AIRO analyst, outage analyst, and customer insights agents.
    Scoring: 50% SQL quality, 20% answer completeness, 30% DeepEval relevancy.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 on any failure.
    """
    try:
        sql_score = _compute_sql_quality(agent_output, expected_sql)
        completeness_score = _compute_answer_completeness(agent_output, expected_output or "")

        try:
            reference = expected_output if expected_output else expected_sql
            relevancy_score = _evaluate_reasoning(
                query, expected_sql, agent_output, reference
            )
        except Exception:
            logger.warning(
                "DeepEval relevancy evaluation failed for analyst; using 0.0"
            )
            relevancy_score = 0.0

        reward = (
            (_SQL_QUALITY_WEIGHT * sql_score)
            + (_ANSWER_COMPLETENESS_WEIGHT * completeness_score)
            + (_ANALYST_RELEVANCY_WEIGHT * relevancy_score)
        )
        return max(0.0, min(1.0, reward))
    except Exception:
        logger.warning("Analyst reward computation failed; returning 0.0")
        return 0.0


# ── Summarizer Reward (F13) ──────────────────────────────────

_STRUCTURAL_WEIGHT = 0.4
_INFORMATION_COVERAGE_WEIGHT = 0.3
_SUMMARIZER_RELEVANCY_WEIGHT = 0.3

_STRUCTURAL_MARKERS = ["headline", "key findings", "##", "- ", "datasets"]


def _compute_structural_completeness(agent_output: str) -> float:
    """Check for headline, bullets, summary sections."""
    output_lower = agent_output.lower()
    matched = sum(1 for marker in _STRUCTURAL_MARKERS if marker in output_lower)
    return matched / len(_STRUCTURAL_MARKERS)


def _compute_information_coverage(agent_output: str, analyst_input: str) -> float:
    """Check key findings from input are mentioned in output."""
    output_lower = agent_output.lower()
    # Extract key data points from analyst input (numbers, service names, percentages)
    key_terms = set()
    for word in analyst_input.lower().split():
        cleaned = word.strip("(),;:.\"'[]")
        if len(cleaned) >= 4 and not cleaned.startswith("[") and cleaned not in ("from", "with", "that", "this", "have", "been", "were", "also"):
            key_terms.add(cleaned)

    if not key_terms:
        return 0.5

    # Sample up to 20 terms to keep scoring fast
    sampled = list(key_terms)[:20]
    matched = sum(1 for t in sampled if t in output_lower)
    return matched / len(sampled)


def compute_summarizer_reward(
    analyst_input: str,
    agent_output: str,
    expected_output: str | None = None,
) -> float:
    """Compute a composite reward for summarizer agent output.

    Scoring: 40% structural completeness, 30% information coverage, 30% DeepEval relevancy.

    Returns:
        Float between 0.0 and 1.0. Returns 0.0 on any failure.
    """
    try:
        structural_score = _compute_structural_completeness(agent_output)
        coverage_score = _compute_information_coverage(agent_output, analyst_input)

        try:
            reference = expected_output if expected_output else analyst_input
            relevancy_score = _evaluate_reasoning(
                analyst_input, "", agent_output, reference
            )
        except Exception:
            logger.warning(
                "DeepEval relevancy evaluation failed for summarizer; using 0.0"
            )
            relevancy_score = 0.0

        reward = (
            (_STRUCTURAL_WEIGHT * structural_score)
            + (_INFORMATION_COVERAGE_WEIGHT * coverage_score)
            + (_SUMMARIZER_RELEVANCY_WEIGHT * relevancy_score)
        )
        return max(0.0, min(1.0, reward))
    except Exception:
        logger.warning("Summarizer reward computation failed; returning 0.0")
        return 0.0
