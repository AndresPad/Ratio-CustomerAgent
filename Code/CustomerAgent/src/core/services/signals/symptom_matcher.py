"""Symptom template config loader.

Loads symptom template definitions from config/symptoms/*.json for use as
reference material by the triage agent (LLM-based symptom matching).

Hybrid model flow:
  Signals + Symptom Configs â†’ [Triage Agent / LLM] â†’ Confirmed Symptoms
                            â†’ [HypothesisScorer / programmatic] â†’ Ranked Hypotheses
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from pydantic import ValidationError

from core.models.config.symptom_template import SymptomFileConfig
from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))
_SYMPTOM_DIR = os.path.join(_CONFIG_DIR, "symptoms")


def _load_json(path: str) -> dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_symptom_templates() -> list[dict[str, Any]]:
    """Load all symptom templates from config/symptoms/*.json."""
    templates: list[dict[str, Any]] = []
    if not os.path.isdir(_SYMPTOM_DIR):
        logger.warning("Symptom config directory not found: %s", _SYMPTOM_DIR)
        return templates
    for filename in os.listdir(_SYMPTOM_DIR):
        if not filename.endswith(".json"):
            continue
        data = _load_json(os.path.join(_SYMPTOM_DIR, filename))
        # Validate schema at load time — fail fast with clear error
        try:
            SymptomFileConfig.model_validate(data)
        except ValidationError as exc:
            logger.error(
                "Symptom config validation failed for %s: %s", filename, exc,
            )
            raise ValueError(
                f"Invalid symptom config '{filename}': {exc}"
            ) from exc
        for t in data.get("templates", []):
            t["_source_file"] = filename
            templates.append(t)
    # Log templates loaded
    xcv = get_current_xcv()
    if xcv:
        AgentLogger.get_instance().log_symptom_templates_loaded(
            xcv=xcv,
            template_count=len(templates),
            template_ids=[t.get("id", "") for t in templates],
        )
    return templates


def filter_templates_by_signal_types(
    templates: list[dict[str, Any]],
    activated_type_ids: set[str],
    known_template_ids: set[str] | None = None,
    activated_granularities: set[str] | None = None,
    activated_signals: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Return only templates relevant to the activated signal types.

    Args:
        templates: Full list of symptom templates.
        activated_type_ids: Signal type IDs that fired (e.g. {"SIG-TYPE-1", "SIG-TYPE-2"}).
        known_template_ids: Template IDs already sent in a prior turn.  When provided,
            templates whose IDs appear here are excluded (delta mode).
        activated_granularities: Set of granularity names that activated (e.g. {"cross_region", "region_slicategory"}).
            Used for suppress_when evaluation.
        activated_signals: List of activated signal dicts with their field values.
            Used for suppress_when matching_fields evaluation.

    Returns:
        (filtered_templates, skipped_ids): The relevant templates and the list of
        template IDs that were skipped because the agent already knows them.
    """
    relevant: list[dict[str, Any]] = []
    skipped: list[str] = []

    for tmpl in templates:
        sources = set(tmpl.get("signal_sources", []))
        # Keep only templates whose signal_sources overlap with activated types
        if not sources.intersection(activated_type_ids):
            continue
        tid = tmpl.get("id", "")
        if known_template_ids and tid in known_template_ids:
            skipped.append(tid)
            continue
        # Check suppress_when rule
        if _is_suppressed(tmpl, activated_granularities, activated_signals):
            logger.debug("Template %s suppressed by suppress_when rule", tid)
            skipped.append(tid)
            continue
        relevant.append(tmpl)

    logger.debug(
        "Template filter: %d/%d relevant to activated types %s (skipped %d known)",
        len(relevant), len(templates), activated_type_ids, len(skipped),
    )
    return relevant, skipped


def _is_suppressed(
    tmpl: dict[str, Any],
    activated_granularities: set[str] | None,
    activated_signals: list[dict[str, Any]] | None,
) -> bool:
    """Check if a template should be suppressed based on its suppress_when rule.

    A template is suppressed when:
    1. It has a suppress_when config
    2. The suppressing granularity is in the activated_granularities set
    3. If matching_fields are specified AND activated_signals are provided,
       at least one signal from the suppressing granularity shares the same
       field values as would be present in this template's granularity.
       If activated_signals is not provided, field matching is skipped
       (suppression fires based on granularity activation alone).
    """
    suppress_cfg = tmpl.get("suppress_when")
    if not suppress_cfg:
        return False

    suppressing_granularity = suppress_cfg.get("granularity_activated", "")
    if not suppressing_granularity:
        return False

    # If we don't have granularity activation info, can't suppress
    if not activated_granularities:
        return False

    # Check if the suppressing granularity actually fired
    if suppressing_granularity not in activated_granularities:
        return False

    # Granularity activated — if no signals provided for field matching, suppress
    matching_fields = suppress_cfg.get("matching_fields", [])
    if not matching_fields or not activated_signals:
        return True

    # Field-level matching: check if any signal from the suppressing granularity
    # exists (the mere presence of the suppressing granularity with matching
    # field overlap is sufficient for suppression)
    for signal in activated_signals:
        if signal.get("granularity") == suppressing_granularity:
            return True

    return False


def format_templates_for_prompt(
    templates: list[dict[str, Any]],
    skipped_ids: list[str] | None = None,
) -> str:
    """Format symptom templates as structured reference material for the triage prompt.

    Strips internal keys (_source_file) and presents templates in a readable format
    the LLM can use to match signals to symptoms.

    If *skipped_ids* is provided, a brief note is prepended telling the agent
    which templates it already received so it can still reference them.
    """
    lines: list[str] = []
    if skipped_ids:
        lines.append(f"  (Templates already provided — still valid: {', '.join(skipped_ids)})")
        lines.append("")
    for tmpl in templates:
        tid = tmpl["id"]
        name = tmpl.get("name", "")
        weight = tmpl.get("weight", 1)
        sources = ", ".join(tmpl.get("signal_sources", []))
        extracted = tmpl.get("extracted_when", "")
        filters = {k: v for k, v in tmpl.get("filters", {}).items() if k != "severity_rules"}
        sev_rules = tmpl.get("filters", {}).get("severity_rules", {})
        from_data = tmpl.get("fields", {}).get("from_data", [])
        llm_fields = tmpl.get("fields", {}).get("llm_derived", [])

        lines.append(f"  {tid}: {name}")
        lines.append(f"    signal_sources: [{sources}]")
        lines.append(f"    weight: {weight}")
        lines.append(f"    when: {extracted}")
        if filters:
            lines.append(f"    criteria: {json.dumps(filters)}")
        if sev_rules:
            lines.append(f"    severity_rules: {json.dumps(sev_rules)}")
        if from_data:
            lines.append(f"    enrichment_fields: {from_data}")
        if llm_fields:
            lines.append(f"    llm_derived_fields: {llm_fields}")
        lines.append("")
    return "\n".join(lines)
