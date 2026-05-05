"""Stage 2 — Hypothesis Scorer.

Scores hypotheses by measuring overlap between confirmed symptoms and each
hypothesis's expected_symptoms, weighted by signal strength.  This is purely
programmatic — no LLM is involved.

Hybrid model flow:
  [Triage Agent / LLM] → Confirmed Symptoms
                       → [HypothesisScorer / programmatic] → Ranked Hypotheses → Evidence

Scoring parameters are loaded from config/hypotheses/scoring_config.json.
Supported options:
  strength_aggregation:      avg | max | min  (default: avg)
      "avg" uses weight-proportional aggregation so low-strength
      symptoms with low weight don't drag the aggregate down.
  default_weight:            fallback weight for symptoms not in template (default: 1)
  min_score_threshold:       discard hypotheses below this score (default: 0.0)
  max_score:                 cap final score at this value (default: 7.5)
  category_boost_factor:     multiplier for category match (default: 1.5)
  category_mismatch_penalty: multiplier for explicit category mismatch (default: 0.5)
  category_unknown_modifier: multiplier when category data is missing (default: 0.8)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .investigation_state import Hypothesis, HypothesisStatus, Symptom
from pydantic import ValidationError
from core.models.config.hypothesis_template import HypothesisFileConfig
from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))
_HYPOTHESES_DIR = os.path.join(_CONFIG_DIR, "hypotheses")


# ── Config loading ────────────────────────────────────────────────

def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_hypothesis_templates() -> list[dict[str, Any]]:
    """Load all hypothesis templates from config/hypotheses/*.json."""
    templates: list[dict[str, Any]] = []
    if not os.path.isdir(_HYPOTHESES_DIR):
        logger.warning("Hypotheses config directory not found: %s", _HYPOTHESES_DIR)
        return templates
    # Skip non-hypothesis files: scoring_config is tuning params,
    # investigation_hypotheses is a reference doc with a different schema.
    _skip = {"scoring_config.json", "investigation_hypotheses.json"}
    for filename in os.listdir(_HYPOTHESES_DIR):
        if not filename.endswith(".json") or filename in _skip:
            continue
        data = _load_json(os.path.join(_HYPOTHESES_DIR, filename))
        # Validate schema at load time — fail fast with clear error
        try:
            HypothesisFileConfig.model_validate(data)
        except ValidationError as exc:
            logger.error(
                "Hypothesis config validation failed for %s: %s", filename, exc,
            )
            raise ValueError(
                f"Invalid hypothesis config '{filename}': {exc}"
            ) from exc
        for h in data.get("hypotheses", []):
            # Skip pending hypotheses (e.g., risk hypotheses awaiting signal types)
            if h.get("status") == "pending":
                logger.debug("Skipping pending hypothesis: %s", h["id"])
                continue
            h["_source_file"] = filename
            templates.append(h)
    return templates


# ── Scoring ───────────────────────────────────────────────────────

def _compute_match_score(
    expected: list[str],
    confirmed_ids: set[str],
    symptom_lookup: dict[str, Symptom],
    scoring_config: dict[str, Any] | None = None,
) -> tuple[float, list[str]]:
    """Compute signal-proportional match score.

    Formula:
        match_score = (weighted_matched / weighted_total) × agg_signal_strength

    Where:
        weighted_matched = sum(weight for each expected symptom that is confirmed)
        weighted_total   = sum(weight for all expected symptoms, using default_weight
                           for symptoms not in lookup)
        agg_signal_strength = aggregated signal_strength of matched symptoms.
            For "avg" (default), uses **weight-proportional** aggregation:
            sum(weight_i × strength_i) / sum(weight_i) so that higher-weighted
            symptoms contribute more and low-strength symptoms with low weight
            don't drag the aggregate down disproportionately.
            "max" and "min" are unweighted (pick extreme value).

    Returns (score, list_of_matched_template_ids).
    """
    if not expected:
        return 0.0, []

    cfg = scoring_config or {}
    default_weight = cfg.get("default_weight", 1)
    strength_agg = cfg.get("strength_aggregation", "avg")

    matched_ids: list[str] = []
    weighted_matched = 0.0
    weighted_total = 0.0
    # Store (weight, strength) pairs for weighted aggregation
    matched_weight_strength: list[tuple[float, float]] = []

    for sym_id in expected:
        sym = symptom_lookup.get(sym_id)
        weight = sym.weight if sym else default_weight
        weighted_total += weight

        if sym_id in confirmed_ids:
            matched_ids.append(sym_id)
            weighted_matched += weight
            if sym:
                matched_weight_strength.append((weight, sym.signal_strength))

    if weighted_total == 0:
        return 0.0, matched_ids

    overlap_ratio = weighted_matched / weighted_total

    if matched_weight_strength:
        if strength_agg == "max":
            agg_strength = max(s for _, s in matched_weight_strength)
        elif strength_agg == "min":
            agg_strength = min(s for _, s in matched_weight_strength)
        else:  # avg (default) — weight-proportional
            total_w = sum(w for w, _ in matched_weight_strength)
            agg_strength = (
                sum(w * s for w, s in matched_weight_strength) / total_w
                if total_w > 0 else 0.0
            )
    else:
        agg_strength = 0.0

    score = overlap_ratio * agg_strength
    return round(score, 4), matched_ids


def _compute_category_boost(
    hypothesis_template: dict[str, Any],
    matched_symptom_ids: list[str],
    symptom_lookup: dict[str, Symptom],
    scoring_config: dict[str, Any] | None = None,
) -> float:
    """Compute category alignment boost for hypothesis.

    If hypothesis specifies relevant_categories (for dependency symptoms) or
    relevant_sli_categories (for SLI symptoms) and matched symptoms have
    category entities that align, apply a boost multiplier.

    Three outcomes:
        - Match:    return category_boost_factor   (default 1.5)
        - Unknown:  return category_unknown_modifier (default 0.8)
          Symptoms have no category data — cannot confirm or deny alignment.
        - Mismatch: return category_mismatch_penalty (default 0.5)
          Symptoms have category data that explicitly conflicts.

    Returns:
        category_modifier: multiplier applied to the base match score.
    """
    cfg = scoring_config or {}
    category_boost_factor = cfg.get("category_boost_factor", 1.5)
    category_mismatch_penalty = cfg.get("category_mismatch_penalty", 0.5)
    category_unknown_modifier = cfg.get("category_unknown_modifier", 0.8)
    
    # Check both dependency and SLI category fields
    relevant_cats = hypothesis_template.get("relevant_categories", [])
    relevant_sli_cats = hypothesis_template.get("relevant_sli_categories", [])
    
    # If both are empty or both contain "any", no category filtering
    if (not relevant_cats and not relevant_sli_cats) or \
       ("any" in relevant_cats and "any" in relevant_sli_cats) or \
       (relevant_cats == ["any"] and not relevant_sli_cats) or \
       (relevant_sli_cats == ["any"] and not relevant_cats):
        return 1.0

    # Extract categories from matched symptoms (both dependency and SLI categories)
    symptom_categories: set[str] = set()
    for sym_id in matched_symptom_ids:
        sym = symptom_lookup.get(sym_id)
        if sym and sym.entities:
            # Check dependency category
            dep_cat = sym.entities.get("dependency_category") or sym.entities.get("DependencyCategory")
            if dep_cat and isinstance(dep_cat, str):
                symptom_categories.add(dep_cat.lower())
            
            # Check SLI category
            sli_cat = sym.entities.get("sli_category") or sym.entities.get("SliCategory")
            if sli_cat and isinstance(sli_cat, str):
                symptom_categories.add(sli_cat.lower())

    # Build the set of required categories from hypothesis template
    all_relevant_cats = set()
    if relevant_cats and "any" not in relevant_cats:
        all_relevant_cats.update(cat.lower() for cat in relevant_cats)
    if relevant_sli_cats and "any" not in relevant_sli_cats:
        all_relevant_cats.update(cat.lower() for cat in relevant_sli_cats)

    # If "any" in either list (all_relevant_cats empty after filtering), accept any category
    if not all_relevant_cats:
        return 1.0

    if not symptom_categories:
        # No category info in symptoms but hypothesis requires specific categories —
        # apply unknown modifier (softer than mismatch) because we cannot confirm
        # or deny alignment.
        logger.debug(
            "No sli_category/dependency_category in symptoms for hypothesis %s "
            "(requires %s) — applying category_unknown_modifier=%.2f",
            hypothesis_template["id"], all_relevant_cats, category_unknown_modifier,
        )
        return category_unknown_modifier

    category_match = bool(symptom_categories & all_relevant_cats)

    if category_match:
        logger.debug(
            "Category match for hypothesis %s: symptom_categories=%s matched relevant_categories=%s",
            hypothesis_template["id"], symptom_categories, all_relevant_cats,
        )
        return category_boost_factor
    else:
        # Category mismatch — apply penalty (reduce score) to deprioritize this hypothesis
        logger.debug(
            "Category MISMATCH for hypothesis %s: symptom_categories=%s vs relevant_categories=%s "
            "— applying category_mismatch_penalty=%.2f",
            hypothesis_template["id"], symptom_categories, all_relevant_cats,
            category_mismatch_penalty,
        )
        return category_mismatch_penalty  # explicit mismatch penalty

_SCORING_CONFIG_PATH = os.path.join(_CONFIG_DIR, "hypotheses", "scoring_config.json")


def _load_scoring_config() -> dict[str, Any]:
    """Load scoring parameters from config/hypotheses/scoring_config.json."""
    if not os.path.isfile(_SCORING_CONFIG_PATH):
        logger.warning("Scoring config not found: %s — using defaults", _SCORING_CONFIG_PATH)
        return {}
    return _load_json(_SCORING_CONFIG_PATH)


# ── Main scorer entry point ──────────────────────────────────────

def score_hypotheses(
    confirmed_symptoms: list[Symptom],
) -> list[Hypothesis]:
    """Stage 2: Score and rank hypotheses by symptom overlap × signal strength.

    Scoring parameters are loaded from config/hypotheses/scoring_config.json.

    For each hypothesis template:
    1. Count which expected_symptoms are confirmed
    2. If matched >= min_symptoms_for_match, compute match_score
    3. Create ranked Hypothesis instances

    Args:
        confirmed_symptoms: Symptoms confirmed by the triage agent.

    Returns hypotheses sorted by match_score descending.
    Only hypotheses meeting min_symptoms_for_match are included.
    """
    templates = load_hypothesis_templates()
    cfg = _load_scoring_config()
    min_score = cfg.get("min_score_threshold", 0.0)
    max_score = cfg.get("max_score", 7.5)

    # Build lookup: template_id → Symptom
    confirmed_ids: set[str] = set()
    symptom_lookup: dict[str, Symptom] = {}
    for sym in confirmed_symptoms:
        confirmed_ids.add(sym.template_id)
        symptom_lookup[sym.template_id] = sym

    logger.info(
        "Hypothesis scoring input: %d confirmed symptom IDs=%s, "
        "%d hypothesis templates loaded, min_score_threshold=%.4f",
        len(confirmed_ids), sorted(confirmed_ids),
        len(templates), min_score,
    )

    candidates: list[Hypothesis] = []

    for tmpl in templates:
        hyp_id = tmpl["id"]
        expected = tmpl.get("expected_symptoms", [])
        min_match = tmpl.get("min_symptoms_for_match", 2)
        required = tmpl.get("required_symptoms", [])
        excluding = tmpl.get("excluding_symptoms", [])

        score, matched_ids = _compute_match_score(
            expected, confirmed_ids, symptom_lookup, scoring_config=cfg,
        )
        matched_count = len(matched_ids)

        # Check excluding symptoms (ANY present → disqualify)
        if excluding:
            present_excluders = [e for e in excluding if e in confirmed_ids]
            if present_excluders:
                logger.info(
                    "Hypothesis %s: excluding symptoms present %s, skipping",
                    hyp_id, present_excluders,
                )
                continue

        # Check required symptoms (ALL must be present)
        if required:
            missing = [r for r in required if r not in confirmed_ids]
            if missing:
                logger.info(
                    "Hypothesis %s: required symptoms missing %s (need ALL of %s), skipping",
                    hyp_id, missing, required,
                )
                continue

        if matched_count < min_match:
            logger.info(
                "Hypothesis %s: matched %d/%d (need %d), skipping — "
                "expected=%s, confirmed_overlap=%s",
                hyp_id, matched_count, len(expected), min_match,
                expected, matched_ids,
            )
            continue

        # Apply category boost if relevant_categories specified
        category_boost = _compute_category_boost(
            tmpl, matched_ids, symptom_lookup, scoring_config=cfg,
        )
        boosted_score = min(score * category_boost, max_score)
        
        logger.info(
            "Hypothesis %s: base_score=%.4f category_boost=%.2fx final_score=%.4f (max=%.2f)",
            hyp_id, score, category_boost, boosted_score, max_score,
        )

        if boosted_score < min_score:
            logger.info(
                "Hypothesis %s: boosted_score=%.4f below threshold %.4f, skipping",
                hyp_id, boosted_score, min_score,
            )
            continue

        hypothesis = Hypothesis(
            id=hyp_id,
            template_id=hyp_id,
            statement=tmpl.get("statement", ""),
            category=tmpl.get("category", ""),
            status=HypothesisStatus.ACTIVE,
            expected_symptoms=expected,
            matched_symptoms=matched_ids,
            match_score=boosted_score,
            min_symptoms_for_match=min_match,
            evidence_needed=tmpl.get("evidence_needed", []),
        )
        candidates.append(hypothesis)
        logger.info(
            "Hypothesis %s (%s): final_score=%.4f matched=%d/%d",
            hyp_id, tmpl["name"], boosted_score, matched_count, len(expected),
        )

    # Sort by match_score descending — highest signal affinity first
    candidates.sort(key=lambda h: h.match_score, reverse=True)

    # Log scoring results
    xcv = get_current_xcv()
    if xcv:
        scores_str = "; ".join(f"{h.id}={h.match_score:.4f}" for h in candidates[:5])
        AgentLogger.get_instance().log_hypothesis_scoring(
            xcv=xcv,
            input_symptom_count=len(confirmed_symptoms),
            output_hypothesis_count=len(candidates),
            top_hypothesis_id=candidates[0].id if candidates else "",
            top_score=candidates[0].match_score if candidates else 0.0,
            all_scores=scores_str,
        )

    return candidates
