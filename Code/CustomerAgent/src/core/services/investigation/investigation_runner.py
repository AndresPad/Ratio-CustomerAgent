"""Investigation runner — wires SignalBuilderResult → Investigation GroupChat.

Hybrid model:
  Stage 1 (LLM — triage agent): Runs STANDALONE before GroupChat.
          Matches raw signals against symptom templates to confirm symptoms.
  Stage 2 (programmatic):       HypothesisScorer ranks hypotheses after triage
  Stage 3 (GroupChat):          Sequential hypothesis evaluation with evidence
                                collection and reasoning (no triage_agent, no action_planner)
  Stage 4 (LLM — action planner): Runs STANDALONE after GroupChat.
          Plans actions for all confirmed/contributing hypotheses with deduplication.

Triage runs outside the GroupChat for reliability:
  - Parse failures can be retried without consuming GroupChat turns
  - Hypothesis scoring is validated before the loop begins
  - GroupChat starts at HYPOTHESIZING with pre-populated hypotheses

Action planning runs outside the GroupChat for deduplication:
  - Sees ALL confirmed/contributing hypotheses at once
  - Deduplicates actions across hypotheses (same action_id → merged)
  - Decoupled from hypothesis cycling

Scoring config is loaded directly by hypothesis_scorer from
config/hypotheses/scoring_config.json.

This is the `on_group_chat` callback for signal_builder.py.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, AsyncIterator

from agent_framework import Agent, AgentResponse, AgentResponseUpdate, Message, WorkflowEvent
from agent_framework.orchestrations import GroupChatBuilder

from helper.errors import (
    PipelineError, NetworkError, AuthError, LLMError, ParseError,
    ConfigError, ToolError, classify_exception,
)

from ...agent_factory import load_config, create_agents
from .investigation_state import Investigation, InvestigationContext, InvestigationPhase, HypothesisStatus, EvidenceItem
from .investigation_output_parser import parse_agent_output, apply_to_investigation, extract_json_block, deduplicate_actions, ParsedAgentOutput
from .investigation_speaker_selector import create_investigation_speaker_selector, _format_hypothesis_summary
from ..signals.signal_models import SignalBuilderResult
from ..signals.signal_builder import load_signal_template
from ..signals.symptom_matcher import load_symptom_templates, format_templates_for_prompt, filter_templates_by_signal_types
from .hypothesis_scorer import score_hypotheses
from .investigation_narrator import narrate_agent_turn, narrate_stage
from .investigation_folding_strategy import InvestigationFoldingStrategy, FOLDING_ENABLED
from core.sandbox.fetch_tools import clear_fetch_cache
from helper.agent_logger import AgentLogger, get_current_xcv, set_current_xcv, set_current_tool_stage, generate_xcv, set_current_service_tree_id

logger = logging.getLogger(__name__)

# ── Feature flag: speaker selector ───────────────────────────────────────────
# Resolution order: ENABLE_SPEAKER_SELECTOR env var  →  config use_speaker_selector  →  default True
_SPEAKER_SELECTOR_ENV = os.getenv("ENABLE_SPEAKER_SELECTOR")


def _resolve_speaker_selector_flag(inv_workflow_cfg: dict[str, Any]) -> bool:
    """Resolve speaker-selector flag: env var wins, then config, then True."""
    if _SPEAKER_SELECTOR_ENV is not None:
        return _SPEAKER_SELECTOR_ENV.strip().lower() in ("true", "1", "yes")
    return inv_workflow_cfg.get("use_speaker_selector", True)

_CONFIG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))


def _get_agent_timeout(agent_name: str, timeout_cfg: dict[str, Any] | None) -> float:
    """Return the timeout in seconds for a given agent from config.

    Falls back to ``timeout_cfg["default"]`` then to 120s.
    """
    if not timeout_cfg:
        return 120.0
    return float(timeout_cfg.get(agent_name, timeout_cfg.get("default", 120)))


def _get_retry_policy(agent_name: str, retry_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Return the retry policy for a given agent from config.

    Falls back to ``retry_cfg["default"]`` then to no-retry defaults.
    """
    _NO_RETRY = {"max_retries": 0, "backoff": "none", "backoff_base_seconds": 0}
    if not retry_cfg:
        return _NO_RETRY
    policy = retry_cfg.get(agent_name, retry_cfg.get("default", _NO_RETRY))
    return {
        "max_retries": int(policy.get("max_retries", 0)),
        "backoff": policy.get("backoff", "none"),
        "backoff_base_seconds": float(policy.get("backoff_base_seconds", 0)),
    }


async def _backoff_sleep(policy: dict[str, Any], attempt: int) -> float:
    """Sleep according to the backoff strategy. Returns seconds waited."""
    base = policy.get("backoff_base_seconds", 0)
    strategy = policy.get("backoff", "none")
    if strategy == "none" or base <= 0:
        return 0.0
    if strategy == "linear":
        wait = base * attempt
    elif strategy == "exponential":
        wait = base * (2 ** (attempt - 1))
    else:
        wait = 0.0
    if wait > 0:
        await asyncio.sleep(wait)
    return wait





def _enrich_fallback_symptoms(
    parsed: ParsedAgentOutput,
    signal_builder_result: SignalBuilderResult,
) -> None:
    """Enrich markdown-fallback symptoms with template weights and signal strengths.

    When JSON parsing fails and symptoms are extracted from prose via the markdown
    fallback, they have minimal data (weight=1, signal_strength=0.0).  This function
    populates them with correct weights from symptom templates and signal_strength
    from the activated signals so that hypothesis scoring produces meaningful scores.
    """
    if parsed.is_json_parsed or not parsed.symptoms:
        return

    # Check if symptoms need enrichment (all have signal_strength == 0)
    needs_enrichment = all(
        float(s.get("signal_strength", 0)) == 0.0
        for s in parsed.symptoms
    )
    if not needs_enrichment:
        return

    # Load symptom templates for weight and signal_sources
    templates = load_symptom_templates()
    template_map = {t["id"]: t for t in templates}

    # Build signal_type → max_strength lookup from signal builder results
    signal_strengths: dict[str, float] = {}
    for tr in signal_builder_result.type_results:
        if tr.activated_signals:
            signal_strengths[tr.signal_type_id] = tr.max_strength

    enriched_count = 0
    for sym in parsed.symptoms:
        tid = sym.get("template_id", "")
        tmpl = template_map.get(tid)
        if not tmpl:
            continue

        sym["weight"] = tmpl.get("weight", 1)
        sources = tmpl.get("signal_sources", [])
        best_strength = 0.0
        best_source = ""
        for src in sources:
            if src in signal_strengths and signal_strengths[src] > best_strength:
                best_strength = signal_strengths[src]
                best_source = src
        if best_strength > 0:
            sym["signal_strength"] = best_strength
            sym["source_signal_type"] = best_source
            enriched_count += 1

    logger.info(
        "Enriched %d/%d fallback symptoms with template weights and signal strengths",
        enriched_count, len(parsed.symptoms),
    )


# ── Deterministic symptom coverage enforcement ───────────────────────────────

def _evaluate_filters(filters: dict[str, Any], rows: list[dict[str, Any]]) -> bool:
    """Evaluate a template's filters against signal data rows.

    Supported filter patterns (all must pass for the template to match):
      - min_row_count: N          → len(rows) >= N
      - min_<field>: N            → any row has numeric field >= N
      - max_<field>: N            → any row has numeric field <= N
      - is_<field>: true          → any row has truthy field value
      - <field>_present: true     → any row has non-empty string field

    Returns True if ALL filter conditions are satisfied.
    """
    if not filters:
        return False  # No filters = not deterministically confirmable

    for key, threshold in filters.items():
        # min_row_count: special — checks total row count, not per-row field
        if key == "min_row_count":
            if len(rows) < threshold:
                return False
            continue

        # min_<field>: N — any row has field >= N
        if key.startswith("min_"):
            field = key[4:]  # strip "min_"
            if not any(_numeric_val(row, field) >= threshold for row in rows):
                return False
            continue

        # max_<field>: N — any row has field <= N
        if key.startswith("max_"):
            field = key[4:]  # strip "max_"
            if not any(_numeric_val(row, field) <= threshold for row in rows):
                return False
            continue

        # is_<field>: true — any row has truthy field
        if key.startswith("is_") and threshold is True:
            field = key[3:]  # strip "is_"
            if not any(_truthy(row, field, key) for row in rows):
                return False
            continue

        # <field>_present: true — any row has non-empty field
        if key.endswith("_present") and threshold is True:
            field = key[:-8]  # strip "_present"
            if not any(_non_empty(row, field) for row in rows):
                return False
            continue

        # Unknown filter pattern — cannot evaluate deterministically
        logger.debug("Filter key '%s' not evaluable deterministically, skipping template", key)
        return False

    return True


def _numeric_val(row: dict[str, Any], field: str) -> float:
    """Extract numeric value from row, trying both snake_case and original key."""
    for key in (field, field.replace("_", "")):
        val = row.get(key)
        if val is not None:
            try:
                return float(val)
            except (ValueError, TypeError):
                pass
    # Also try case-insensitive match
    field_lower = field.lower()
    for k, v in row.items():
        if k.lower() == field_lower or k.lower().replace("_", "") == field_lower.replace("_", ""):
            try:
                return float(v)
            except (ValueError, TypeError):
                pass
    return 0.0


def _truthy(row: dict[str, Any], field: str, original_key: str) -> bool:
    """Check if a boolean-like field is truthy in the row."""
    # Try the field name directly, and the original full key (e.g., "is_outage")
    for key in (field, original_key, field.replace("_", ""), original_key.replace("_", "")):
        val = row.get(key)
        if val is not None:
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.lower() in ("true", "1", "yes")
            return bool(val)
    # Case-insensitive fallback
    for k, v in row.items():
        if k.lower() in (field.lower(), original_key.lower()):
            if isinstance(v, bool):
                return v
            if isinstance(v, str):
                return v.lower() in ("true", "1", "yes")
            return bool(v)
    return False


def _non_empty(row: dict[str, Any], field: str) -> bool:
    """Check if a field has a non-empty string value."""
    for key in (field, field.replace("_", "")):
        val = row.get(key)
        if val is not None:
            return bool(str(val).strip())
    # Case-insensitive fallback
    field_lower = field.lower()
    for k, v in row.items():
        if k.lower() == field_lower or k.lower().replace("_", "") == field_lower.replace("_", ""):
            return bool(str(v).strip())
    return False


def _ensure_signal_type_coverage(
    parsed: ParsedAgentOutput,
    signal_builder_result: SignalBuilderResult,
) -> None:
    """Auto-confirm symptoms whose filters pass against actual signal data rows.

    Design: each symptom template declares ``filters`` that define deterministic
    criteria.  This function evaluates those filters against the activated signal
    data.  If a template's signal_sources are activated, its filters pass, AND
    no symptom from the same signal_sources is already confirmed — the symptom
    is auto-injected.

    No special flags needed.  The ``filters`` field IS the contract.
    To add a new deterministically confirmable symptom, just define its filters.
    """
    # 1. Build activated signal types → (rows, max_strength)
    activated_types: dict[str, tuple[list[dict[str, Any]], float]] = {}
    for tr in signal_builder_result.type_results:
        if tr.activated_signals:
            all_rows = [row for sig in tr.activated_signals for row in sig.matched_rows]
            if all_rows:
                activated_types[tr.signal_type_id] = (all_rows, tr.max_strength)

    if not activated_types:
        return

    # 2. Determine which signal_sources already have a confirmed symptom
    templates = load_symptom_templates()
    template_map = {t["id"]: t for t in templates}

    confirmed_ids = {sym.get("template_id") for sym in parsed.symptoms}
    covered_signal_sources: set[str] = set()
    for sym in parsed.symptoms:
        tmpl = template_map.get(sym.get("template_id", ""))
        if tmpl:
            covered_signal_sources.update(tmpl.get("signal_sources", []))

    # 3. Evaluate each template's filters against actual rows
    injected = 0
    for tmpl in templates:
        tmpl_id = tmpl["id"]
        if tmpl_id in confirmed_ids:
            continue

        filters = tmpl.get("filters")
        if not filters:
            continue

        tmpl_sources = set(tmpl.get("signal_sources", []))
        if not tmpl_sources:
            continue

        # Check if this template's signal_sources are activated
        matching_sources = tmpl_sources & set(activated_types.keys())
        if not matching_sources:
            continue

        # Skip if those sources are already covered by a confirmed symptom
        if matching_sources <= covered_signal_sources:
            continue

        # Collect rows from all matching signal types
        all_rows: list[dict[str, Any]] = []
        max_strength = 0.0
        for src in matching_sources:
            rows, strength = activated_types[src]
            all_rows.extend(rows)
            max_strength = max(max_strength, strength)

        # Evaluate filters against the actual data
        if not _evaluate_filters(filters, all_rows):
            continue

        logger.info(
            "Auto-confirming %s ('%s'): filters %s passed against %d rows from %s",
            tmpl_id, tmpl.get("name", ""), filters, len(all_rows), sorted(matching_sources),
        )
        parsed.symptoms.append({
            "template_id": tmpl_id,
            "status": "confirmed",
            "weight": tmpl.get("weight", 1),
            "signal_strength": max_strength,
            "source_signal_type": sorted(matching_sources)[0],
            "text": (
                f"[Auto-confirmed] {tmpl.get('name', tmpl_id)}: "
                f"filters passed against {len(all_rows)} rows from "
                f"{', '.join(sorted(matching_sources))}."
            ),
            "enrichments": {},
            "category": tmpl.get("_source_file", "").replace("_exposure.json", "").replace(".json", ""),
        })
        covered_signal_sources.update(matching_sources)
        confirmed_ids.add(tmpl_id)
        injected += 1

    if injected:
        logger.info(
            "Coverage enforcement: auto-confirmed %d symptoms via filter evaluation",
            injected,
        )


# ── Field name mappings: snake_case keys the LLM might use → canonical from_data keys ──
_SLI_ID_KEYS = ("slo_sli_id", "sli_id", "SLO_SliId")
_CUSTOMER_NAME_KEYS = ("customer_name", "CustomerName")
_SUBSCRIPTION_ID_KEYS = ("subscription_id", "SubscriptionId")
_REGION_KEYS = ("region", "Region")


def _enrich_symptom_categories(
    parsed: ParsedAgentOutput,
    signal_builder_result: SignalBuilderResult,
) -> None:
    """Post-fill sli_category and missing from_data fields in symptom enrichments.

    SliCategory is now returned directly by the MCP tool response. This function
    back-fills sli_category from signal data rows when the triage agent omits it,
    along with customer_name, subscription_id, region, and slo_sli_id.
    """
    if not parsed.symptoms:
        return

    # Build a flat lookup of first-seen field values from signal data rows
    # for fields the LLM commonly omits.
    default_fields: dict[str, Any] = {}
    for tr in signal_builder_result.type_results:
        for sig in (tr.activated_signals or []):
            for row in (sig.matched_rows or []):
                for key in ("CustomerName", "SubscriptionId", "Region", "SLO_SliId",
                            "SliCategory",
                            "EarliestImpactStart", "LatestImpactEnd",
                            "TotalImpactDurationMin", "AvgValueAcrossWindows",
                            "MinValueAcrossWindows", "ImpactedResources"):
                    if key not in default_fields and row.get(key) is not None:
                        default_fields[key] = row[key]

    enriched_count = 0
    for sym in parsed.symptoms:
        enrichments = sym.get("enrichments") or {}
        changed = False

        # --- Resolve sli_category from signal row data ---
        if not enrichments.get("sli_category"):
            # Check if it's already in enrichments under alternate keys
            sli_cat = enrichments.get("SliCategory") or enrichments.get("sli_category")
            if not sli_cat:
                sli_cat = default_fields.get("SliCategory")
            if sli_cat:
                enrichments["sli_category"] = sli_cat
                changed = True

        # --- Back-fill missing from_data fields from signal rows ---
        _BACKFILL = {
            "customer_name": ("CustomerName", _CUSTOMER_NAME_KEYS),
            "subscription_id": ("SubscriptionId", _SUBSCRIPTION_ID_KEYS),
            "region": ("Region", _REGION_KEYS),
            "slo_sli_id": ("SLO_SliId", _SLI_ID_KEYS),
        }
        for canonical, (row_key, alt_keys) in _BACKFILL.items():
            # Already present under the canonical key?
            if enrichments.get(canonical):
                continue
            # Present under an alternate key? Copy to canonical.
            found = False
            for ak in alt_keys:
                if ak != canonical and enrichments.get(ak):
                    enrichments[canonical] = enrichments[ak]
                    found = True
                    changed = True
                    break
            if found:
                continue
            # Fall back to signal data default
            if row_key in default_fields:
                enrichments[canonical] = default_fields[row_key]
                changed = True

        if changed:
            sym["enrichments"] = enrichments
            enriched_count += 1

    if enriched_count:
        logger.info(
            "Post-filled sli_category and missing fields for %d/%d symptoms",
            enriched_count, len(parsed.symptoms),
        )


def _aggregate_signal_rows(rows: list[dict[str, Any]], keep_fields: list[str]) -> dict[str, Any]:
    """Compute aggregate stats over all rows so the LLM can evaluate aggregate symptoms
    (e.g. SYM-SUP-004 multi-case, SYM-SUP-005 multi-customer) even when rows are truncated."""
    if not rows:
        return {}
    agg: dict[str, Any] = {"total_rows": len(rows)}
    # Distinct customers
    customers = {r.get("Customer_CloudCustomerName") or r.get("customer_name") for r in rows}
    customers.discard(None)
    if customers:
        agg["distinct_customers"] = len(customers)
        agg["customer_list"] = sorted(customers)
    # Distinct products
    products = {r.get("SupportProductName") or r.get("support_product_name") for r in rows}
    products.discard(None)
    if products:
        agg["distinct_products"] = len(products)
        agg["product_list"] = sorted(products)
    # Severity summary
    sev_order = {"A": 0, "B": 1, "C": 2, "D": 3}
    severities = [r.get("Severity") or r.get("severity") for r in rows]
    severities = [s for s in severities if s in sev_order]
    if severities:
        agg["max_severity"] = min(severities, key=lambda s: sev_order.get(s, 99))
    # CritSit / Escalation counts
    critsit_count = sum(1 for r in rows if str(r.get("IsCritSit") or r.get("is_crit_sit", "")).lower() == "true")
    if critsit_count:
        agg["critsit_count"] = critsit_count
    escalated_count = sum(1 for r in rows if str(r.get("IsEscalated") or r.get("is_escalated", "")).lower() == "true")
    if escalated_count:
        agg["escalated_count"] = escalated_count
    return agg


def _build_task_message(investigation: Investigation, max_rows_per_grain: int = 3) -> str:
    """Build the initial investigation task message with raw signals and symptom templates.

    The triage agent (LLM) uses the symptom templates as reference material to
    match raw signals to confirmed symptoms.  Hypothesis scoring happens
    programmatically AFTER triage completes.
    """
    result = investigation.signal_builder_result

    # -- Activated signals --
    signals_summary = []
    for sig in result.all_activated_signals:
        signals_summary.append(
            f"- [{sig.signal_type_id}] {sig.signal_name} "
            f"(granularity={sig.granularity}, confidence={sig.confidence}, "
            f"strength={sig.strength:.3f}): {sig.activation_summary}"
        )

    compounds_summary = []
    for comp in result.activated_compounds:
        compounds_summary.append(
            f"- [{comp.compound_id}] {comp.compound_name} "
            f"(confidence={comp.confidence}, strength={comp.strength:.3f}): {comp.rationale}"
        )

    # -- Signal data rows (compact, deduplicated per signal type) --
    # Build data_fields lookup from signal template so we only include
    # the columns that matter (dropping duplicate snake_case aliases).
    # Granularity-level data_fields override the signal-type-level data_fields.
    sig_template = load_signal_template()
    _fields_by_type: dict[str, list[str]] = {}
    _fields_by_granularity: dict[str, list[str]] = {}
    for st in sig_template.get("signal_types", []):
        _fields_by_type[st["id"]] = st.get("data_fields", [])
        for gran_cfg in st.get("granularities", []):
            if "data_fields" in gran_cfg:
                _fields_by_granularity[gran_cfg["granularity"]] = gran_cfg["data_fields"]

    signal_data_lines = []
    for tr in result.type_results:
        if not tr.activated_signals:
            continue
        type_fields = _fields_by_type.get(tr.signal_type_id, [])
        signal_data_lines.append(f"  Signal Type: {tr.signal_type_id} (strength={tr.max_strength:.3f})")

        # Group rows per granularity, deduplicate within each, cap per config
        for sig in tr.activated_signals:
            grain = sig.granularity if hasattr(sig, "granularity") else "unknown"
            # Use granularity-level data_fields if defined, else fall back to type-level
            keep_fields = _fields_by_granularity.get(grain, type_fields)
            seen_rows: set[str] = set()
            unique_rows: list[dict[str, Any]] = []
            for row in sig.matched_rows:
                filtered = {k: row[k] for k in keep_fields if k in row} if keep_fields else row
                key = json.dumps(filtered, sort_keys=True, default=str)
                if key not in seen_rows:
                    seen_rows.add(key)
                    unique_rows.append(filtered)

            if unique_rows:
                signal_data_lines.append(f"    Granularity: {grain} ({len(unique_rows)} rows, columns: {', '.join(keep_fields or ['all'])})")
                for i, row in enumerate(unique_rows[:max_rows_per_grain]):
                    signal_data_lines.append(f"      [{i}] {json.dumps(row, default=str)}")
                if len(unique_rows) > max_rows_per_grain:
                    signal_data_lines.append(f"      ... ({len(unique_rows) - max_rows_per_grain} more rows omitted)")
                    # Aggregate summary so LLM can evaluate multi-case/multi-customer symptoms
                    agg = _aggregate_signal_rows(unique_rows, keep_fields)
                    if agg:
                        signal_data_lines.append(f"      AGGREGATE: {json.dumps(agg, default=str)}")
        signal_data_lines.append("")

    # -- Symptom templates (filtered to activated signal types only) --
    all_templates = load_symptom_templates()
    activated_type_ids = {
        tr.signal_type_id
        for tr in result.type_results
        if tr.activated_signals
    }
    templates, skipped_ids = filter_templates_by_signal_types(
        all_templates, activated_type_ids,
    )
    template_ref = format_templates_for_prompt(templates, skipped_ids or None)

    parts = [
        f"INVESTIGATION TRIGGERED for customer '{result.customer_name}' "
        f"(service_tree_id: {result.service_tree_id})",
        f"Decision: {result.action}",
        f"Timestamp: {result.timestamp.isoformat()}",
        "",
        "== Activated Signals ==",
    ]
    parts.extend(signals_summary if signals_summary else ["(none)"])
    parts.append("")
    parts.append("== Activated Compound Signals ==")
    parts.extend(compounds_summary if compounds_summary else ["(none)"])
    parts.append("")
    parts.append("== Signal Data Rows ==")
    parts.extend(signal_data_lines if signal_data_lines else ["(no data rows)"])
    parts.append("")
    parts.append("== Symptom Templates (Reference Material) ==")
    parts.append("Match these templates against the signal data above.")
    parts.append("A symptom is CONFIRMED when its criteria are met by the data.")
    parts.append("")
    parts.append(template_ref)
    parts.append("")
    parts.append(
        "TRIAGE AGENT: You MUST evaluate ALL symptom templates against ALL activated signal types. "
        "Do NOT only match SLI symptoms — if SIG-TYPE-2 (support cases) is activated, match SYM-SUP-* templates too. "
        "For each template whose criteria are met, confirm the symptom and populate ALL enrichment_fields from the data rows. "
        "Compute any llm_derived fields by reasoning over the data rows. "
        "Evaluate cross-source correlations (e.g., time overlap for SYM-OUT-003). "
        "Then assign the investigation category and severity."
    )
    return "\n".join(parts)


def _create_investigation(
    result: SignalBuilderResult,
) -> Investigation:
    """Create an Investigation instance — no pre-computed stages.

    Hybrid model:
      Stage 1 (LLM):          Triage agent matches signals → symptoms during GroupChat
      Stage 2 (programmatic): HypothesisScorer runs AFTER triage completes
                              (wired through output_parser)

    Scoring config is loaded directly by hypothesis_scorer from
    config/hypotheses/scoring_config.json.
    """
    investigation = Investigation(
        phase=InvestigationPhase.INITIALIZING,
        context=InvestigationContext(
            customer_name=result.customer_name,
            service_tree_id=result.service_tree_id,
            start_time=result.start_time,
            end_time=result.end_time,
            owning_tenant_names=result.owning_tenant_names,
            support_product_names=result.support_product_names,
        ),
        signal_builder_result=result,
    )

    logger.info(
        "Investigation created (%d activated signals, %d compounds). "
        "Triage agent will perform symptom matching.",
        len(result.all_activated_signals),
        len(result.activated_compounds),
    )

    return investigation


def _build_action_task(
    investigation: Investigation,
    actionable_hypotheses: list,
) -> str:
    """Build the task message for standalone action_planner invocation.

    Provides all confirmed/contributing hypotheses, their evidence verdicts,
    and symptom verdicts so the action_planner can plan and deduplicate actions
    across hypotheses in a single pass.
    """
    ctx = investigation.context
    parts = [
        "ACTION PLANNING TASK",
        f"Customer: {ctx.customer_name} (service_tree_id: {ctx.service_tree_id})",
        f"Severity: {ctx.severity or 'unknown'}",
        "",
        f"Total hypotheses evaluated: {len(investigation.hypotheses)}",
        f"Actionable hypotheses: {len(actionable_hypotheses)}",
        "",
        "═══ ACTIONABLE HYPOTHESES ═══",
        "",
    ]

    for i, hyp in enumerate(actionable_hypotheses, 1):
        parts.append(f"--- Hypothesis #{i}: {hyp.id} ---")
        parts.append(f"  Statement: {hyp.statement}")
        parts.append(f"  Category: {hyp.category}")
        parts.append(f"  Status: {hyp.status.value}")
        parts.append(f"  Confidence: {hyp.confidence:.2f}")
        parts.append(f"  Match Score: {hyp.match_score:.2f}")

        if hyp.matched_symptoms:
            parts.append(f"  Matched Symptoms: {', '.join(hyp.matched_symptoms)}")

        if hyp.verdicts:
            parts.append("  Evidence Verdicts:")
            for er_id, verdict in hyp.verdicts.items():
                parts.append(f"    {er_id}: {verdict.value if hasattr(verdict, 'value') else verdict}")

        if hyp.symptom_verdicts:
            parts.append("  Symptom Verdicts:")
            for sym_id, sv in hyp.symptom_verdicts.items():
                parts.append(f"    {sym_id}: {sv.value if hasattr(sv, 'value') else sv}")

        if hyp.determination:
            parts.append(f"  Determination: {hyp.determination}")

        parts.append("")

    # Include evidence summaries
    if investigation.evidence:
        parts.append("═══ COLLECTED EVIDENCE ═══")
        parts.append("")
        for ev in investigation.evidence:
            if ev.summary:
                parts.append(f"  [{ev.er_id}] ({ev.agent_name}): {ev.summary[:300]}")
        parts.append("")

    # Include confirmed symptoms for context
    confirmed_symptoms = [s for s in investigation.symptoms if s.confirmed]
    if confirmed_symptoms:
        parts.append("═══ CONFIRMED SYMPTOMS ═══")
        parts.append("")
        for s in confirmed_symptoms:
            parts.append(f"  [{s.template_id}] {s.text}")
        parts.append("")

    parts.append(
        "═══ DEDUPLICATION INSTRUCTIONS ═══\n"
        "Multiple hypotheses may warrant the same action (e.g., both HYP-SLI-001 "
        "and HYP-SLI-004 could recommend ACT-ICM-001). Emit each unique action_id "
        "ONCE. In target_hypotheses, list ALL hypothesis IDs that justify this action. "
        "In justification, reference all supporting hypotheses.\n"
    )
    parts.append(
        "Plan actions for ALL actionable hypotheses above. "
        "Set phase_complete=\"acting\" in your signals."
    )

    return "\n".join(parts)


async def run_investigation(
    result: SignalBuilderResult,
    config: dict[str, Any] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the investigation GroupChat for a SignalBuilderResult.

    This is the main entry point — pass as on_group_chat callback to
    run_signal_builder_loop, or call directly for testing.

    Yields structured events compatible with the existing orchestrator streaming format.
    """
    if config is None:
        config = load_config()

    inv_workflow_cfg = config.get("investigation_workflow")
    if not inv_workflow_cfg:
        logger.error("No 'investigation_workflow' section in agents_config.json")
        return

    # Create investigation state
    investigation = _create_investigation(result)

    # Create agents (reuses agent_factory — creates all agents, we filter)
    all_agents_dict, capture_mw, eval_mw, injection_mw, tool_injection_mw, llm_log_mw, prompts = await create_agents(config)

    async def _close_all_agents() -> None:
        """Close MCP connections on all agents to avoid cross-task cancel scope errors."""
        for agent in all_agents_dict.values():
            mcp_tools = getattr(agent, "mcp_tools", [])
            for mcp_tool in mcp_tools:
                try:
                    if getattr(mcp_tool, "is_connected", False):
                        await mcp_tool.close()
                except Exception:
                    logger.debug("Non-critical: failed to close MCP tool on agent %s", agent.name)
            # Also close the agent's exit stack (covers tools entered during run)
            try:
                exit_stack = getattr(agent, "_async_exit_stack", None)
                if exit_stack is not None:
                    await exit_stack.aclose()
            except RuntimeError as e:
                if "cancel scope" in str(e).lower():
                    logger.debug("Suppressed cancel scope error closing agent %s", agent.name)
                else:
                    logger.debug("Non-critical: failed to close exit stack on agent %s", agent.name, exc_info=True)
            except Exception:
                logger.debug("Non-critical: failed to close exit stack on agent %s", agent.name, exc_info=True)

    # ── XCV propagation: always prefer the ContextVar (set by app.py) ──
    # The parent pipeline sets the XCV before calling run_investigation().
    # Using get_current_xcv() ensures we always inherit the parent XCV,
    # even if result.xcv was populated at a different time.
    contextvar_xcv = get_current_xcv()
    result_xcv = result.xcv if hasattr(result, 'xcv') and result.xcv else None
    xcv = contextvar_xcv or result_xcv or generate_xcv()
    logger.info(
        "Investigation XCV resolution: contextvar=%s, result.xcv=%s, chosen=%s",
        contextvar_xcv, result_xcv, xcv,
    )
    set_current_xcv(xcv)
    set_current_service_tree_id(result.service_tree_id)
    clear_fetch_cache()  # Reset dedup cache for new investigation
    investigation.context.extra["xcv"] = xcv
    tracker = AgentLogger.get_instance()
    tracker.log_investigation_created(
        xcv=xcv,
        investigation_id=investigation.id,
        customer_name=result.customer_name,
        service_tree_id=result.service_tree_id,
        signal_count=len(result.all_activated_signals),
        compound_count=len(result.activated_compounds),
    )
    tracker.start_investigation_span(
        investigation_id=investigation.id,
        customer_name=result.customer_name,
        service_tree_id=result.service_tree_id,
    )

    # Extract investigation agents
    orchestrator_name = inv_workflow_cfg["orchestrator_agent"]
    participant_names = inv_workflow_cfg.get("participants", [])

    tracker.log_workflow_started(
        xcv=xcv,
        workflow_type="InvestigationGroupChat",
        participants=participant_names,
    )
    max_turns = inv_workflow_cfg.get("max_turns", 30)

    # Agent timeout configuration (P0-A: configurable per-agent timeouts)
    timeout_cfg = inv_workflow_cfg.get("agent_timeout_seconds")

    # Retry policy configuration (D: config-driven retry per agent)
    retry_cfg = inv_workflow_cfg.get("retry_policy")

    # Pipeline constants (F: config-driven, formerly hardcoded)
    max_eval_hypotheses = inv_workflow_cfg.get("max_eval_hypotheses", 4)
    max_rows_per_grain = inv_workflow_cfg.get("max_rows_per_grain", 3)

    # Narrator: LLM-powered narration of the investigation flow (optional)
    narrator_enabled = inv_workflow_cfg.get("narrator_enabled", False)
    agent_roles = inv_workflow_cfg.get("agent_roles", {})
    narrator_agent = all_agents_dict.get(agent_roles.get("narrator", "narrator")) if narrator_enabled else None
    if narrator_enabled and narrator_agent is None:
        logger.warning("narrator_enabled=True but 'narrator' agent not found in agents — narration disabled")
        narrator_enabled = False
    if narrator_enabled:
        logger.info("Investigation narrator is ENABLED")

    if orchestrator_name not in all_agents_dict:
        logger.error("Investigation orchestrator '%s' not found in agents", orchestrator_name)
        return

    orchestrator = all_agents_dict[orchestrator_name]

    # ══════════════════════════════════════════════════════════════
    # STAGE 1: Run triage_agent STANDALONE (outside GroupChat)
    # ══════════════════════════════════════════════════════════════
    # Triage runs once, maps signals → symptoms. Running it outside the
    # GroupChat avoids fragile JSON-parse fallbacks and turn-budget waste.
    # On failure we can retry without consuming GroupChat turns.

    triage_task_message = _build_task_message(investigation, max_rows_per_grain=max_rows_per_grain)
    investigation.transition_to(InvestigationPhase.TRIAGE, source="runner:triage_start")
    tracker.log_phase_transition(
        xcv=xcv,
        investigation_id=investigation.id,
        from_phase="initializing",
        to_phase=investigation.phase.value,
        agent_name="runner",
    )
    set_current_tool_stage(f"investigation:{investigation.phase.value}")

    yield {
        "type": "investigation_started",
        "investigation_id": investigation.id,
        "customer_name": result.customer_name,
        "service_tree_id": result.service_tree_id,
        "signal_count": len(result.all_activated_signals),
        "compound_count": len(result.activated_compounds),
    }

    # Narrator: narrate the signal_builder stage (signals collected)
    if narrator_enabled and narrator_agent:
        sig_summary = (
            f"Collected {len(result.all_activated_signals)} activated signals and "
            f"{len(result.activated_compounds)} compound patterns for customer "
            f"'{result.customer_name}' on service '{result.service_tree_id}'."
        )
        try:
            async for narr_ev in narrate_stage(
                narrator_agent=narrator_agent,
                stage_name="signal_builder",
                stage_output=sig_summary,
                phase="initializing",
                investigation=investigation,
                signal_builder_result=result,
                xcv=xcv,
            ):
                yield narr_ev
        except Exception as narr_exc:
            logger.warning("Narrator error (non-fatal) for signal_builder: %s", narr_exc)

    # Reset middleware captures before triage
    if capture_mw:
        capture_mw.reset()
    if eval_mw:
        eval_mw.reset()
    if injection_mw:
        injection_mw.reset()
    if tool_injection_mw:
        tool_injection_mw.reset()
    if llm_log_mw:
        llm_log_mw.reset()

    triage_agent_name = agent_roles.get("triage", "triage_agent")
    triage_agent = all_agents_dict.get(triage_agent_name)
    if not triage_agent:
        logger.error("%s not found in agents — cannot run investigation", triage_agent_name)
        yield {
            "type": "investigation_error",
            "investigation_id": investigation.id,
            "error": f"{triage_agent_name} not found in agents_config.json",
            "phase": investigation.phase.value,
            "last_agent": "none",
        }
        await _close_all_agents()
        return

    _triage_retry = _get_retry_policy(triage_agent_name, retry_cfg)
    _TRIAGE_MAX_RETRIES = max(_triage_retry["max_retries"], 1)
    triage_text = ""
    triage_parsed = None
    for attempt in range(1, _TRIAGE_MAX_RETRIES + 1):
        logger.info("Running triage_agent standalone (attempt %d/%d)", attempt, _TRIAGE_MAX_RETRIES)
        tracker.log_agent_invoked(xcv, triage_agent_name, triage_task_message[:500])
        _triage_timeout = _get_agent_timeout(triage_agent_name, timeout_cfg)
        try:
            triage_response = await asyncio.wait_for(
                triage_agent.run(triage_task_message),
                timeout=_triage_timeout,
            )
            triage_text = triage_response.text or ""
        except asyncio.TimeoutError:
            logger.error(
                "Triage agent timed out after %.0fs (attempt %d/%d)",
                _triage_timeout, attempt, _TRIAGE_MAX_RETRIES,
            )
            if attempt == _TRIAGE_MAX_RETRIES:
                yield {
                    "type": "investigation_error",
                    "investigation_id": investigation.id,
                    "error": f"Triage agent timed out after {_triage_timeout}s ({_TRIAGE_MAX_RETRIES} attempts)",
                    "phase": investigation.phase.value,
                    "last_agent": triage_agent_name,
                }
                await _close_all_agents()
                return
            tracker.log_agent_retry(
                xcv=xcv, agent_name=triage_agent_name, attempt=attempt,
                max_retries=_TRIAGE_MAX_RETRIES, reason=f"timeout after {_triage_timeout}s",
                investigation_id=investigation.id, phase=investigation.phase.value,
            )
            _waited = await _backoff_sleep(_triage_retry, attempt)
            if _waited:
                logger.info("Triage backoff: %.1fs before attempt %d", _waited, attempt + 1)
            continue
        except Exception as exc:
            classified = classify_exception(exc)
            logger.error(
                "Triage agent failed (attempt %d): [%s] %s",
                attempt, type(classified).__name__, exc,
            )
            if isinstance(classified, AuthError) or (not classified.retryable and attempt == 1):
                yield {
                    "type": "investigation_error",
                    "investigation_id": investigation.id,
                    "error": f"Triage agent failed ({type(classified).__name__}): {exc}",
                    "error_category": type(classified).__name__,
                    "phase": investigation.phase.value,
                    "last_agent": triage_agent_name,
                }
                await _close_all_agents()
                return
            if attempt == _TRIAGE_MAX_RETRIES:
                yield {
                    "type": "investigation_error",
                    "investigation_id": investigation.id,
                    "error": f"Triage agent failed after {_TRIAGE_MAX_RETRIES} attempts ({type(classified).__name__}): {exc}",
                    "error_category": type(classified).__name__,
                    "phase": investigation.phase.value,
                    "last_agent": triage_agent_name,
                }
                await _close_all_agents()
                return
            tracker.log_agent_retry(
                xcv=xcv, agent_name=triage_agent_name, attempt=attempt,
                max_retries=_TRIAGE_MAX_RETRIES, reason=f"{type(classified).__name__}: {exc}",
                investigation_id=investigation.id, phase=investigation.phase.value,
            )
            _waited = await _backoff_sleep(_triage_retry, attempt)
            if _waited:
                logger.info("Triage backoff: %.1fs before attempt %d", _waited, attempt + 1)
            continue

        if not triage_text.strip():
            logger.warning("Triage agent returned empty response (attempt %d)", attempt)
            if attempt == _TRIAGE_MAX_RETRIES:
                break
            tracker.log_agent_retry(
                xcv=xcv, agent_name=triage_agent_name, attempt=attempt,
                max_retries=_TRIAGE_MAX_RETRIES, reason="empty response",
                investigation_id=investigation.id, phase=investigation.phase.value,
            )
            _waited = await _backoff_sleep(_triage_retry, attempt)
            if _waited:
                logger.info("Triage backoff: %.1fs before attempt %d", _waited, attempt + 1)
            continue

        tracker.log_agent_response(xcv, triage_agent_name, triage_text)

        # Parse triage output and apply to investigation state
        triage_parsed = parse_agent_output(triage_text, agent_name=triage_agent_name)

        # Enrich markdown-fallback symptoms with template weights and signal
        # strengths so that hypothesis scoring produces meaningful scores even
        # when the LLM fails to emit a valid JSON block.
        _enrich_fallback_symptoms(triage_parsed, result)

        # Post-fill sli_category from signal data rows and missing from_data fields.
        _enrich_symptom_categories(triage_parsed, result)

        # Ensure every activated signal type has at least one confirmed symptom.
        # The LLM sometimes skips entire families (e.g., SYM-OUT-* from SIG-TYPE-3).
        _ensure_signal_type_coverage(triage_parsed, result)

        # Force phase_complete="triage" since we know triage is done
        triage_parsed.signals.phase_complete = "triage"
        apply_to_investigation(triage_parsed, investigation)

        # Drain triage tool calls for the event
        triage_tool_calls = []
        if capture_mw:
            triage_tool_calls = [
                {"tool": c["tool"], "query": c.get("query", ""),
                 "arguments": c.get("arguments", {}), "result": c.get("result"),
                 "error": c.get("error"), "duration_ms": c.get("duration_ms", 0),
                 "agent": c.get("agent", triage_agent_name)}
                for c in capture_mw.drain()
            ]

        yield {
            "type": "investigation_agent_response",
            "agent": triage_agent_name,
            "text": triage_text,
            "phase": investigation.phase.value,
            "investigation_id": investigation.id,
            "parsed_signals": {
                "phase_complete": "triage",
                "next_agent": None,
                "investigation_resolved": False,
                "needs_more_evidence": False,
            },
            "symptoms_count": len(investigation.symptoms),
            "hypotheses_count": len(investigation.hypotheses),
            "evidence_count": len(investigation.evidence),
            "evidence_cycle_count": 0,
            "symptoms": _serialize_symptoms(investigation),
            "hypotheses": _serialize_hypotheses(investigation),
            "evidence": _serialize_evidence(investigation),
            "actions": _serialize_actions(investigation),
            "symptom_verdicts_summary": _symptom_verdicts_summary(investigation),
            **({"tool_calls": triage_tool_calls} if triage_tool_calls else {}),
        }

        # Narrator: narrate the triage stage
        if narrator_enabled and narrator_agent and triage_text.strip():
            try:
                async for narr_ev in narrate_stage(
                    narrator_agent=narrator_agent,
                    stage_name=triage_agent_name,
                    stage_output=triage_text,
                    phase=investigation.phase.value,
                    investigation=investigation,
                    xcv=xcv,
                ):
                    yield narr_ev
            except Exception as narr_exc:
                logger.warning("Narrator error (non-fatal) for %s: %s", triage_agent_name, narr_exc)

        if investigation.hypotheses:
            logger.info(
                "Triage complete: %d symptoms, %d hypotheses scored (attempt %d)",
                len(investigation.symptoms), len(investigation.hypotheses), attempt,
            )
            break
        elif investigation.symptoms and attempt < _TRIAGE_MAX_RETRIES:
            # Symptoms exist but 0 hypotheses — retry with enhanced prompt.
            # This happens when the LLM output degenerates (garbled JSON) and
            # the markdown fallback extracts symptom IDs but with insufficient
            # data for hypothesis scoring.
            logger.warning(
                "Triage parsed %d symptoms but 0 hypotheses (attempt %d) — retrying",
                len(investigation.symptoms), attempt,
            )
            # Clear degraded state so retry can re-populate cleanly
            investigation.symptoms.clear()
            investigation.hypotheses.clear()
            investigation._scoring_attempted = False
            triage_task_message = (
                triage_task_message
                + "\n\n--- PREVIOUS RESPONSE (incomplete structured output) ---\n"
                + triage_text
                + "\n--- END PREVIOUS RESPONSE ---\n\n"
                "Your previous response identified symptoms but the JSON block was "
                "malformed or truncated. You MUST end your message with a complete "
                "```json block containing {\"structured_output\": "
                "{\"validated_symptoms\": [...]}, \"signals\": "
                "{\"phase_complete\": \"triage\"}}. "
                "Include ALL confirmed symptoms with their correct weight, "
                "signal_strength, severity, and enrichments. "
                "Re-emit the structured JSON block based on your analysis above."
            )
            tracker.log_agent_retry(
                xcv=xcv, agent_name=triage_agent_name, attempt=attempt,
                max_retries=_TRIAGE_MAX_RETRIES, reason="symptoms found but 0 hypotheses (malformed JSON)",
                investigation_id=investigation.id, phase=investigation.phase.value,
            )
            _waited = await _backoff_sleep(_triage_retry, attempt)
            if _waited:
                logger.info("Triage backoff: %.1fs before attempt %d", _waited, attempt + 1)
            continue
        elif investigation.symptoms:
            logger.warning(
                "Triage parsed %d symptoms but 0 hypotheses (final attempt %d)",
                len(investigation.symptoms), attempt,
            )
            break
        else:
            logger.warning("Triage produced 0 symptoms, 0 hypotheses (attempt %d)", attempt)
            if attempt < _TRIAGE_MAX_RETRIES:
                # Enhance the retry message: include original response and
                # explicitly request the required JSON output block.
                triage_task_message = (
                    triage_task_message
                    + "\n\n--- PREVIOUS RESPONSE (no structured output detected) ---\n"
                    + triage_text
                    + "\n--- END PREVIOUS RESPONSE ---\n\n"
                    "Your previous response contained analysis but was missing the "
                    "required ```json block. You MUST end your message with a "
                    "```json block containing {\"structured_output\": "
                    "{\"validated_symptoms\": [...]}, \"signals\": "
                    "{\"phase_complete\": \"triage\"}}. "
                    "Re-emit the structured JSON block based on your analysis above."
                )
                tracker.log_agent_retry(
                    xcv=xcv, agent_name=triage_agent_name, attempt=attempt,
                    max_retries=_TRIAGE_MAX_RETRIES, reason="0 symptoms, 0 hypotheses",
                    investigation_id=investigation.id, phase=investigation.phase.value,
                )
                _waited = await _backoff_sleep(_triage_retry, attempt)
                if _waited:
                    logger.info("Triage backoff: %.1fs before attempt %d", _waited, attempt + 1)
                continue
            break

    # ── Validate triage results ──────────────────────────────────
    if not investigation.hypotheses:
        logger.error(
            "Triage completed but no hypotheses were scored "
            "(symptoms=%d, scoring_attempted=%s). Investigation cannot proceed.",
            len(investigation.symptoms), investigation._scoring_attempted,
        )
        yield {
            "type": "investigation_error",
            "investigation_id": investigation.id,
            "error": (
                f"Triage produced {len(investigation.symptoms)} symptoms but "
                f"0 hypotheses — investigation cannot proceed"
            ),
            "phase": investigation.phase.value,
            "last_agent": triage_agent_name,
        }
        await _close_all_agents()
        return

    # ══════════════════════════════════════════════════════════════
    # Limit to top N hypotheses for evaluation (ranked by match_score)
    # ══════════════════════════════════════════════════════════════
    if len(investigation.hypotheses) > max_eval_hypotheses:
        investigation.hypotheses.sort(key=lambda h: h.match_score, reverse=True)
        trimmed = investigation.hypotheses[max_eval_hypotheses:]
        investigation.hypotheses = investigation.hypotheses[:max_eval_hypotheses]
        logger.info(
            "Trimmed hypotheses from %d to top %d by match_score. "
            "Dropped: %s",
            len(investigation.hypotheses) + len(trimmed),
            max_eval_hypotheses,
            [h.id for h in trimmed],
        )

    # ══════════════════════════════════════════════════════════════
    # STAGE 2: Inject hypothesis context into orchestrator
    # ══════════════════════════════════════════════════════════════
    # Set the initial current hypothesis and advance to HYPOTHESIZING.
    top_hyp = max(investigation.hypotheses, key=lambda h: h.match_score)
    investigation._current_hypothesis_id = top_hyp.id
    tracker.start_hypothesis_span(top_hyp.id, statement=top_hyp.statement)
    _prev_phase = investigation.phase.value
    investigation.transition_to(InvestigationPhase.HYPOTHESIZING, source="runner:hypothesis_injection")
    if investigation.phase.value != _prev_phase:
        tracker.log_phase_transition(
            xcv=xcv,
            investigation_id=investigation.id,
            from_phase=_prev_phase,
            to_phase=investigation.phase.value,
            agent_name="runner",
        )
    set_current_tool_stage(f"investigation:{investigation.phase.value}")

    # Inject hypothesis summary into orchestrator instructions
    hyp_summary = _format_hypothesis_summary(
        investigation,
        evidence_planner_name=agent_roles.get("evidence_planner", "evidence_planner"),
    )
    import re as _re_hyp
    current_instructions = orchestrator.default_options.get("instructions", "") or ""
    # Remove any previous hypothesis summary to avoid accumulation
    current_instructions = _re_hyp.sub(
        r"\n\n═══ STAGE 2 COMPLETE ═══.*",
        "",
        current_instructions,
        flags=_re_hyp.DOTALL,
    )
    orchestrator.default_options["instructions"] = (
        current_instructions + "\n\n" + hyp_summary
    )
    logger.info(
        "Injected hypothesis summary (%d hypotheses, top=%s score=%.2f) "
        "into orchestrator instructions. Phase → HYPOTHESIZING.",
        len(investigation.hypotheses), top_hyp.id, top_hyp.match_score,
    )

    yield {
        "type": "hypothesis_scoring_complete",
        "investigation_id": investigation.id,
        "hypotheses_count": len(investigation.hypotheses),
        "top_hypothesis": top_hyp.id,
        "top_score": top_hyp.match_score,
    }

    # ══════════════════════════════════════════════════════════════
    # STAGE 3: Build GroupChat (without triage_agent)
    # ══════════════════════════════════════════════════════════════
    # Filter out triage_agent from participants — it already ran.
    groupchat_participant_names = [n for n in participant_names if n != triage_agent_name]

    # Resolve speaker-selector feature flag early — needed to decide
    # whether the orchestrator goes into participants or orchestrator_agent.
    _parsed_cache: dict[str, Any] = {}
    _evidence_planner = all_agents_dict.get(agent_roles.get("evidence_planner", "evidence_planner"))
    _reasoner = all_agents_dict.get(agent_roles.get("reasoner", "reasoner"))
    phase_transitions_cfg = inv_workflow_cfg.get("phase_transitions")
    cycle_detection_cfg = inv_workflow_cfg.get("cycle_detection")
    speaker_selector_enabled = _resolve_speaker_selector_flag(inv_workflow_cfg)

    non_orchestrator_participants = [
        all_agents_dict[name]
        for name in groupchat_participant_names
        if name in all_agents_dict and name != orchestrator_name
    ]
    # When speaker_selector is enabled the orchestrator participates as a
    # normal member (selection_func handles routing).  When disabled the
    # framework uses orchestrator_agent for LLM-based routing — adding it
    # to participants as well would cause a "Duplicate executor ID" error.
    if speaker_selector_enabled:
        participants = [orchestrator] + non_orchestrator_participants
    else:
        participants = non_orchestrator_participants
    all_participant_names = [a.name for a in participants]

    missing = [n for n in groupchat_participant_names if n not in all_agents_dict]
    if missing:
        logger.warning("Investigation participants not found: %s", missing)

    # Build deterministic speaker selector (replaces LLM-based orchestrator routing)
    # Shared cache: speaker selector populates after parse_agent_output(),
    # runner reads in _finalize_agent_response to avoid double-parsing.

    if speaker_selector_enabled:
        speaker_selector = create_investigation_speaker_selector(
            participant_names=all_participant_names,
            orchestrator_name=orchestrator_name,
            parsed_cache=_parsed_cache,
            investigation=investigation,
            orchestrator_agent=orchestrator,
            evidence_planner_agent=_evidence_planner,
            reasoner_agent=_reasoner,
            phase_transitions_cfg=phase_transitions_cfg,
            cycle_detection_cfg=cycle_detection_cfg,
            agent_roles=agent_roles,
        )
    else:
        speaker_selector = None
        logger.info("Speaker selector disabled (ENABLE_SPEAKER_SELECTOR != true) — using LLM-based routing")
        tracker.emit_feature_flag_event(
            xcv=xcv,
            flag_name="ENABLE_SPEAKER_SELECTOR",
            enabled=False,
            fallback="LLM-based orchestrator routing",
        )

    # ── Attach context folding strategy (closure capture) ─────────
    if FOLDING_ENABLED:
        _agents_cfg_map = {a["name"]: a for a in config.get("agents", [])}
        _folding_agents = [
            (name, all_agents_dict[name])
            for name in [
                agent_roles.get("evidence_planner", "evidence_planner"),
                agent_roles.get("reasoner", "reasoner"),
            ]
            if name in all_agents_dict
        ]
        for _fname, _fagent in _folding_agents:
            _per_agent_flag = _agents_cfg_map.get(_fname, {}).get("context_folding", True)
            if _per_agent_flag:
                _fagent.compaction_strategy = InvestigationFoldingStrategy(
                    investigation=investigation,
                    agent_name=_fname,
                )
                logger.info("Context folding attached to agent '%s'", _fname)
            else:
                logger.info("Context folding disabled for agent '%s' (per-agent config)", _fname)
    else:
        logger.info("Context folding globally disabled (ENABLE_CONTEXT_FOLDING != true)")

    logger.info(
        "Building investigation GroupChat (selection=%s, orchestrator=%s, participants=%s, max_turns=%d)",
        "custom" if speaker_selector_enabled else "llm-auto",
        orchestrator_name, all_participant_names, max_turns,
    )

    # Termination condition: investigation_resolved signal or max turns.
    def termination_condition(messages: list[Message]) -> bool:
        assistant_count = sum(1 for m in messages if m.role == "assistant")
        if messages:
            last_text = messages[-1].text or ""
            last_name = getattr(messages[-1], "name", None) or "unknown"
            last_role = getattr(messages[-1], "role", "?")
            logger.info(
                "Termination check: msg_count=%d, assistant_count=%d, "
                "last_agent=%s, last_role=%s, text_len=%d",
                len(messages), assistant_count, last_name, last_role,
                len(last_text),
            )

            def _has_resolved_signal(text: str) -> bool:
                json_block = extract_json_block(text)
                if json_block and isinstance(json_block, dict):
                    sig_raw = json_block.get("signals", {})
                    if isinstance(sig_raw, dict):
                        ir = sig_raw.get("investigation_resolved")
                        if ir is True or (isinstance(ir, str) and ir.lower() in ("true", "yes", "1")):
                            return True
                if "---SIGNALS---" in text and "INVESTIGATION_RESOLVED" in text.upper():
                    return True
                return False

            if _has_resolved_signal(last_text):
                if last_name == orchestrator_name:
                    logger.info(
                        "Investigation resolved signal from orchestrator "
                        "(agent=%s, assistant_count=%d) → terminating",
                        last_name, assistant_count,
                    )
                    return True
                else:
                    logger.warning(
                        "Ignoring investigation_resolved from non-orchestrator "
                        "agent %s — only the orchestrator may terminate the investigation",
                        last_name,
                    )

        if assistant_count >= max_turns:
            logger.info("Investigation max turns (%d) reached", max_turns)
            return True

        return False

    workflow = (
        GroupChatBuilder(
            participants=participants,
            selection_func=speaker_selector,
            orchestrator_name=orchestrator_name,
            orchestrator_agent=None if speaker_selector_enabled else orchestrator,
            termination_condition=termination_condition,
            max_rounds=max_turns,
            intermediate_outputs=True,
        )
        .build()
    )

    # Build GroupChat task message — includes triage results context
    # so the orchestrator knows symptoms are confirmed and hypotheses ranked.
    task = (
        f"Investigation for customer '{result.customer_name}' "
        f"(service_tree_id: {result.service_tree_id}).\n\n"
        f"TRIAGE COMPLETE: {len(investigation.symptoms)} symptoms confirmed, "
        f"{len(investigation.hypotheses)} hypotheses scored.\n"
        f"Top hypothesis: {top_hyp.id} (score={top_hyp.match_score:.2f}) — "
        f"{top_hyp.statement}\n\n"
        f"The STAGE 2 COMPLETE block has been injected into your instructions "
        f"with the full ranked list. Select hypothesis #1 and route to evidence_planner."
    )

    # Reset middleware captures for GroupChat phase
    if capture_mw:
        capture_mw.reset()
    if eval_mw:
        eval_mw.reset()
    if injection_mw:
        injection_mw.reset()
    if tool_injection_mw:
        tool_injection_mw.reset()
    if llm_log_mw:
        llm_log_mw.reset()

    # Run workflow and process events
    current_agent = orchestrator_name  # orchestrator runs first (speaker selection)
    agent_response_count = 0
    evidence_cycle_count = 0
    active_hypothesis_id = ""  # Track which hypothesis is being evaluated
    # Stall detection: warn every N seconds while waiting for a workflow event
    stall_warn_interval = inv_workflow_cfg.get("stall_warn_interval_seconds", 60)
    last_event_time = time.monotonic()
    workflow_start_time = time.monotonic()
    stall_warn_count = 0

    def _drain_tool_calls(agent_name: str) -> list[dict]:
        if not capture_mw:
            return []
        new = capture_mw.drain()
        return [{"tool": c["tool"], "query": c.get("query", ""),
                 "arguments": c.get("arguments", {}), "result": c.get("result"),
                 "error": c.get("error"), "duration_ms": c.get("duration_ms", 0),
                 "agent": c.get("agent", agent_name)} for c in new]

    # Streaming accumulation: collect chunks per agent, finalize on executor_completed.
    # In streaming mode the framework emits AgentResponseUpdate chunks — never
    # a final AgentResponse event.  We accumulate here and process the complete text
    # when the framework fires executor_completed for that agent.
    # NOTE: chunk events are NOT forwarded to the UI — only the narrator streams
    # to the client.  Agent responses are sent as complete cards via _finalize_agent_response.
    _accumulated_chunks: list[str] = []
    _accumulating_agent: str = ""

    def _finalize_agent_response(agent_name: str, accumulated: list[str]) -> list[dict]:
        """Process accumulated streaming chunks as a complete agent response.

        Returns a list of events to yield.  Updates investigation state via closure.
        """
        nonlocal agent_response_count, evidence_cycle_count, active_hypothesis_id

        full_text = "".join(accumulated)
        if not full_text.strip():
            return []

        events: list[dict] = []
        agent_response_count += 1

        # Track agent response
        tracker.log_agent_response(xcv, agent_name, full_text)

        # Drain middleware
        tool_calls = _drain_tool_calls(agent_name)
        llm_calls = llm_log_mw.drain() if llm_log_mw else []
        injection_detections = injection_mw.drain() if injection_mw else []
        injection_detections += tool_injection_mw.drain() if tool_injection_mw else []

        # ── Capture sandbox_coder output per hypothesis ──────────────
        # When evidence_planner finishes, extract the sandbox_coder sub-agent
        # tool result and store it on the current hypothesis for reasoner injection.
        _ep_name_for_capture = agent_roles.get("evidence_planner", "evidence_planner")
        if agent_name == _ep_name_for_capture and tool_calls:
            _sandbox_coder_name = "sandbox_coder"
            _sandbox_results = [
                tc.get("result", "") for tc in tool_calls
                if tc.get("tool") == _sandbox_coder_name and tc.get("result")
            ]
            if _sandbox_results and investigation._current_hypothesis_id:
                _current_hyp = next(
                    (h for h in investigation.hypotheses
                     if h.id == investigation._current_hypothesis_id),
                    None,
                )
                if _current_hyp:
                    _current_hyp.sandbox_coder_output = "\n---\n".join(_sandbox_results)
                    logger.info(
                        "Captured sandbox_coder output (%d chars) for hypothesis %s",
                        len(_current_hyp.sandbox_coder_output),
                        _current_hyp.id,
                    )

        # Track phase before parsing
        prev_phase = investigation.phase.value

        # Use cached parse from speaker selector if available (avoids double-parsing).
        # The speaker selector populates _parsed_cache after parsing each agent turn;
        # see create_investigation_speaker_selector for details.
        if (
            _parsed_cache
            and _parsed_cache.get("agent") == agent_name
            and _parsed_cache.get("parsed") is not None
        ):
            parsed = _parsed_cache["parsed"]
            _parsed_cache.clear()
        else:
            parsed = parse_agent_output(full_text, agent_name=agent_name)

        # Guard: garbled output — do not apply to investigation state.
        # The speaker selector already re-routed to the same agent for
        # a retry; applying garbled signals would corrupt state.
        if parsed.is_garbled:
            logger.warning(
                "Skipping apply_to_investigation for garbled output from %s "
                "(reason: %s)",
                agent_name, parsed.garbled_reason,
            )
            events.append({
                "type": "investigation_agent_response",
                "agent": agent_name,
                "text": parsed.display_text or full_text[:500],
                "is_json_parsed": False,
                "is_garbled": True,
                "garbled_reason": parsed.garbled_reason,
                "phase": investigation.phase.value,
                "investigation_id": investigation.id,
                "symptoms_count": len(investigation.symptoms),
                "hypotheses_count": len(investigation.hypotheses),
                "evidence_count": len(investigation.evidence),
                "evidence_cycle_count": evidence_cycle_count,
                "symptoms": _serialize_symptoms(investigation),
                "hypotheses": _serialize_hypotheses(investigation),
                "evidence": _serialize_evidence(investigation),
                "actions": _serialize_actions(investigation),
                "tool_calls": tool_calls,
                "llm_calls": llm_calls,
            })
            return events

        # Guard: only the orchestrator may mark the investigation resolved.
        # Other agents (especially the reasoner) sometimes set this erroneously
        # after evaluating a single hypothesis.
        if parsed.signals.investigation_resolved and agent_name != orchestrator_name:
            logger.warning(
                "Stripping investigation_resolved from %s — "
                "only the orchestrator may resolve the investigation",
                agent_name,
            )
            parsed.signals.investigation_resolved = False

        # Guard: the orchestrator must NEVER set phase_complete.
        # Phase transitions are driven by specialist agents (triage, reasoner,
        # action_planner, etc.).  The orchestrator only routes via next_agent.
        # If the LLM emits phase_complete despite the prompt, strip it here to
        # prevent corrupting the investigation phase lifecycle.
        if parsed.signals.phase_complete and agent_name == orchestrator_name:
            logger.warning(
                "Stripping phase_complete='%s' from orchestrator — "
                "only specialist agents may advance phases",
                parsed.signals.phase_complete,
            )
            parsed.signals.phase_complete = None

        # Guard: the orchestrator's hypothesis_refuted signal must NOT
        # override the reasoner's actual evaluation.  The reasoner is the
        # authority on hypothesis verdicts (via evaluations[].status).
        # If the reasoner already set a non-REFUTED status (CONFIRMED or
        # CONTRIBUTING), the orchestrator misread the output — strip it.
        # Also strip if the reasoner never evaluated the hypothesis at all
        # (garbled/truncated output), since the orchestrator has no basis
        # for a refutation verdict.
        if parsed.signals.hypothesis_refuted and agent_name == orchestrator_name:
            current_hyp_id = investigation._current_hypothesis_id
            hyp_map = {h.id: h for h in investigation.hypotheses}
            current_hyp = hyp_map.get(current_hyp_id) if current_hyp_id else None
            if current_hyp and current_hyp.status in (
                HypothesisStatus.CONFIRMED,
                HypothesisStatus.CONTRIBUTING,
            ):
                logger.warning(
                    "Stripping hypothesis_refuted from orchestrator — "
                    "reasoner already set %s to %s, orchestrator cannot override",
                    current_hyp_id, current_hyp.status.value,
                )
                parsed.signals.hypothesis_refuted = False
            elif current_hyp and current_hyp.status == HypothesisStatus.ACTIVE:
                # Reasoner never produced a valid evaluation (garbled output).
                # The orchestrator cannot unilaterally refute without evidence.
                logger.warning(
                    "Stripping hypothesis_refuted from orchestrator — "
                    "reasoner never evaluated %s (status still ACTIVE), "
                    "orchestrator cannot refute without reasoner verdict",
                    current_hyp_id,
                )
                parsed.signals.hypothesis_refuted = False

        apply_to_investigation(parsed, investigation)

        # ── Agent-based phase auto-advance ──────────────────────────
        # GroupChat agents may not reliably emit phase_complete signals.
        # Infer the correct investigation phase from which agent spoke.
        # This ensures PLANNING, COLLECTING, and REASONING phases are
        # recorded even when the LLM omits phase_complete from its output.
        _ep_role = agent_roles.get("evidence_planner", "evidence_planner")
        _rr_role = agent_roles.get("reasoner", "reasoner")
        _FORWARD_CHAIN = [
            InvestigationPhase.HYPOTHESIZING,
            InvestigationPhase.PLANNING,
            InvestigationPhase.COLLECTING,
            InvestigationPhase.REASONING,
        ]

        _step_forward_logged = False

        def _step_forward_to(target: InvestigationPhase, reason: str) -> None:
            """Step through legal forward transitions to reach *target*,
            emitting a PhaseTransition event for each intermediate step so
            the UI can highlight Data Fetcher / Code Gen nodes in real time.
            """
            nonlocal _step_forward_logged
            try:
                tgt_idx = _FORWARD_CHAIN.index(target)
            except ValueError:
                return
            for step in _FORWARD_CHAIN:
                if investigation.phase == target:
                    break
                try:
                    cur_idx = _FORWARD_CHAIN.index(investigation.phase)
                except ValueError:
                    break
                if cur_idx < tgt_idx:
                    _from = investigation.phase.value
                    investigation.transition_to(
                        _FORWARD_CHAIN[cur_idx + 1], source=reason,
                    )
                    tracker.log_phase_transition(
                        xcv=xcv,
                        investigation_id=investigation.id,
                        from_phase=_from,
                        to_phase=investigation.phase.value,
                        agent_name=agent_name,
                    )
                    _step_forward_logged = True

        if agent_name == _ep_role:
            if investigation.phase == InvestigationPhase.HYPOTHESIZING:
                _step_forward_to(
                    InvestigationPhase.PLANNING,
                    f"auto_advance:agent={agent_name}",
                )
            elif investigation.phase == InvestigationPhase.REASONING:
                # Evidence cycle backtrack: reasoner requested more evidence
                investigation.transition_to(
                    InvestigationPhase.PLANNING,
                    source=f"auto_backtrack:evidence_cycle_agent={agent_name}",
                )
        elif agent_name == _rr_role:
            if investigation.phase in (
                InvestigationPhase.HYPOTHESIZING,
                InvestigationPhase.PLANNING,
                InvestigationPhase.COLLECTING,
            ):
                _step_forward_to(
                    InvestigationPhase.REASONING,
                    f"auto_advance:agent={agent_name}",
                )

        # Track evidence cycles: reasoner requesting more evidence
        if parsed.signals.needs_more_evidence:
            evidence_cycle_count += 1
            tracker.log_evidence_cycle(
                xcv=xcv,
                investigation_id=investigation.id,
                cycle_number=evidence_cycle_count,
                er_ids=[er.id for er in investigation.evidence if hasattr(er, 'id')],
            )
            logger.info(
                "Evidence cycle %d detected (agent=%s, investigation=%s)",
                evidence_cycle_count, agent_name, investigation.id,
            )

        # Log phase transition(s) if changed and not already logged by _step_forward_to
        if investigation.phase.value != prev_phase and not _step_forward_logged:
            tracker.log_phase_transition(
                xcv=xcv,
                investigation_id=investigation.id,
                from_phase=prev_phase,
                to_phase=investigation.phase.value,
                agent_name=agent_name,
            )
        _step_forward_logged = False

        # Detect active hypothesis: first ACTIVE hypothesis in ranked order
        _active_hyp = next(
            (h for h in investigation.hypotheses
             if h.status == HypothesisStatus.ACTIVE),
            None,
        )
        _active_hyp_id = _active_hyp.id if _active_hyp else ""
        if _active_hyp_id and _active_hyp_id != active_hypothesis_id:
            active_hypothesis_id = _active_hyp_id
            # Start a new hypothesis span (auto-ends previous one)
            tracker.start_hypothesis_span(
                active_hypothesis_id, statement=_active_hyp.statement,
            )
            _rank = next(
                (i for i, h in enumerate(investigation.hypotheses, 1)
                 if h.id == active_hypothesis_id),
                0,
            )
            tracker.log_hypothesis_selected(
                xcv=xcv,
                investigation_id=investigation.id,
                hypothesis_id=active_hypothesis_id,
                statement=_active_hyp.statement,
                match_score=_active_hyp.match_score,
                matched_symptoms=", ".join(getattr(_active_hyp, "matched_symptoms", []) or []),
                evidence_needed=", ".join(getattr(_active_hyp, "evidence_needed", []) or []),
                rank=_rank,
                total_hypotheses=len(investigation.hypotheses),
            )
            events.append({
                "type": "hypothesis_evaluation_started",
                "investigation_id": investigation.id,
                "hypothesis_id": active_hypothesis_id,
                "statement": _active_hyp.statement,
                "match_score": _active_hyp.match_score,
                "rank": _rank,
                "total_hypotheses": len(investigation.hypotheses),
            })

        _stage = f"investigation:{investigation.phase.value}"
        if investigation.hypotheses and investigation.phase in (
            InvestigationPhase.PLANNING,
            InvestigationPhase.COLLECTING,
            InvestigationPhase.REASONING,
        ):
            _stage += f":{active_hypothesis_id or investigation.hypotheses[0].id}"
        set_current_tool_stage(_stage)

        events.append({
            "type": "investigation_agent_response",
            "agent": agent_name,
            "text": full_text,
            "phase": investigation.phase.value,
            "investigation_id": investigation.id,
            "parsed_signals": {
                "phase_complete": parsed.signals.phase_complete,
                "next_agent": parsed.signals.next_agent,
                "investigation_resolved": parsed.signals.investigation_resolved,
                "needs_more_evidence": parsed.signals.needs_more_evidence,
            },
            "symptoms_count": len(investigation.symptoms),
            "hypotheses_count": len(investigation.hypotheses),
            "evidence_count": len(investigation.evidence),
            "evidence_cycle_count": evidence_cycle_count,
            "symptoms": _serialize_symptoms(investigation),
            "hypotheses": _serialize_hypotheses(investigation),
            "evidence": _serialize_evidence(investigation),
            "actions": _serialize_actions(investigation),
            "symptom_verdicts_summary": _symptom_verdicts_summary(investigation),
            **({"tool_calls": tool_calls} if tool_calls else {}),
            **({"llm_calls": llm_calls} if llm_calls else {}),
            **({"prompt_injection": injection_detections} if injection_detections else {}),
        })

        return events

    # ── Queue-based event loop ──────────────────────────────────────
    # Read workflow events in a background task so the main loop can
    # yield stall warnings in real-time during long LLM calls.
    # Without this, the async generator blocks on __anext__() and
    # cannot yield anything — causing the SSE proxy to timeout.
    _event_q: asyncio.Queue = asyncio.Queue()
    _SENTINEL = object()

    async def _feed_workflow_events():
        """Background: iterate the GroupChat workflow and enqueue events."""
        try:
            async for evt in workflow.run(task, stream=True):
                await _event_q.put(evt)
        except Exception as exc:
            await _event_q.put(exc)
        finally:
            await _event_q.put(_SENTINEL)

    feeder_task = asyncio.create_task(_feed_workflow_events())

    # Workflow-level timeout (P0-A): terminate entire GroupChat if exceeded
    _groupchat_timeout = _get_agent_timeout("group_chat", timeout_cfg)

    try:
        event_count = 0
        while True:
            # Check workflow-level timeout
            elapsed_workflow = time.monotonic() - workflow_start_time
            if elapsed_workflow >= _groupchat_timeout:
                logger.error(
                    "GroupChat workflow timed out after %.0fs (limit=%.0fs, events=%d, "
                    "agent_responses=%d, last_agent=%s)",
                    elapsed_workflow, _groupchat_timeout, event_count,
                    agent_response_count, current_agent or "none",
                )
                # Flush any accumulated chunks before exiting
                if _accumulated_chunks and _accumulating_agent:
                    for _ev in _finalize_agent_response(_accumulating_agent, _accumulated_chunks):
                        yield _ev
                    _accumulated_chunks.clear()
                    _accumulating_agent = ""
                tracker.log_investigation_error(
                    xcv=xcv,
                    investigation_id=investigation.id,
                    error=f"GroupChat workflow timed out after {elapsed_workflow:.0f}s",
                    phase=investigation.phase.value,
                )
                yield {
                    "type": "investigation_workflow_error",
                    "investigation_id": investigation.id,
                    "agent": current_agent or "unknown",
                    "error": f"GroupChat workflow timed out after {elapsed_workflow:.0f}s",
                    "phase": investigation.phase.value,
                }
                break

            # Poll the queue with a timeout for stall detection.
            # On timeout, yield a stall warning immediately (keeps SSE alive).
            try:
                logger.debug(
                    "Waiting for workflow event #%d (agent=%s)",
                    event_count + 1, current_agent or "none",
                )
                item = await asyncio.wait_for(
                    _event_q.get(), timeout=stall_warn_interval,
                )
            except asyncio.TimeoutError:
                # No event for stall_warn_interval — yield warning NOW
                stall_warn_count += 1
                wait_secs = round(time.monotonic() - last_event_time, 1)
                stall_agent = current_agent or "unknown"
                llm_snapshot = llm_log_mw.drain() if llm_log_mw else []
                llm_detail = ""
                if llm_snapshot:
                    last_llm = llm_snapshot[-1]
                    llm_detail = (
                        f" LLM: model={last_llm.get('model', '?')}, "
                        f"error={last_llm.get('error', 'none')}, "
                        f"duration_ms={last_llm.get('duration_ms', '?')}"
                    )
                stall_msg = (
                    f"Investigation waiting: no workflow event for {wait_secs}s "
                    f"(warn #{stall_warn_count}). Active agent: {stall_agent}. "
                    f"Phase: {investigation.phase.value}.{llm_detail}"
                )
                logger.warning(stall_msg)
                tracker.log_investigation_error(
                    xcv=xcv,
                    investigation_id=investigation.id,
                    error=stall_msg,
                    phase=investigation.phase.value,
                )
                yield {
                    "type": "investigation_stall_warning",
                    "investigation_id": investigation.id,
                    "wait_seconds": wait_secs,
                    "warn_count": stall_warn_count,
                    "agent": stall_agent,
                    "phase": investigation.phase.value,
                    "llm_detail": llm_detail.strip(),
                }
                continue

            # Sentinel → workflow finished
            if item is _SENTINEL:
                # Flush any remaining accumulated streaming chunks
                if _accumulated_chunks and _accumulating_agent:
                    for _ev in _finalize_agent_response(_accumulating_agent, _accumulated_chunks):
                        yield _ev
                    _accumulated_chunks.clear()
                    _accumulating_agent = ""
                logger.info(
                    "Workflow iterator exhausted: total_events=%d, agent_responses=%d, "
                    "last_agent=%s, elapsed=%.1fs",
                    event_count, agent_response_count, current_agent or "none",
                    time.monotonic() - workflow_start_time,
                )
                break
            # Exception from the feeder task
            if isinstance(item, Exception):
                raise item

            last_event_time = time.monotonic()
            stall_warn_count = 0
            event_count += 1
            event: WorkflowEvent = item

            # Log every event for diagnostics (output/streaming chunks at DEBUG to avoid noise)
            evt_type = getattr(event, "type", "?")
            evt_executor = getattr(event, "executor_id", None) or ""
            # Demote all framework plumbing events to DEBUG;
            # only agent responses & errors are logged at INFO.
            logger.debug(
                "Workflow event #%d: type=%s, executor=%s",
                event_count, evt_type, evt_executor or "(none)",
            )

            # Track current agent from every event that carries executor_id,
            # so stall warnings show the real agent even before a response arrives.
            if hasattr(event, "executor_id") and event.executor_id:
                current_agent = event.executor_id

            # Handle executor lifecycle events (framework status events)
            if evt_type == "executor_invoked":
                logger.debug("Agent invoked by framework: %s", evt_executor)
                continue
            if evt_type == "executor_completed":
                logger.debug("Agent completed by framework: %s", evt_executor)
                # Flush accumulated streaming chunks as a complete agent response
                _completed_agent_name = _accumulating_agent
                _completed_agent_text = "".join(_accumulated_chunks)

                # ── GroupChat agent retry on empty response ──────────────
                # If the agent produced no text and the retry policy allows,
                # re-invoke it standalone once.  This covers evidence_planner
                # and reasoner failures without disrupting the GroupChat flow.
                if _completed_agent_name and not _completed_agent_text.strip():
                    _gc_retry = _get_retry_policy(_completed_agent_name, retry_cfg)
                    _gc_max = _gc_retry["max_retries"]
                    _gc_agent = all_agents_dict.get(_completed_agent_name)
                    if _gc_max > 0 and _gc_agent:
                        logger.warning(
                            "GroupChat agent '%s' produced empty response — "
                            "retrying standalone (policy: max_retries=%d)",
                            _completed_agent_name, _gc_max,
                        )
                        _gc_timeout = _get_agent_timeout(_completed_agent_name, timeout_cfg)
                        for _gc_attempt in range(1, _gc_max + 1):
                            tracker.log_agent_retry(
                                xcv=xcv, agent_name=_completed_agent_name,
                                attempt=_gc_attempt, max_retries=_gc_max,
                                reason="empty response in GroupChat",
                                investigation_id=investigation.id,
                                phase=investigation.phase.value,
                            )
                            _waited = await _backoff_sleep(_gc_retry, _gc_attempt)
                            if _waited:
                                logger.info(
                                    "GroupChat retry backoff: %.1fs before attempt %d for %s",
                                    _waited, _gc_attempt, _completed_agent_name,
                                )
                            try:
                                _gc_resp = await asyncio.wait_for(
                                    _gc_agent.run(task), timeout=_gc_timeout,
                                )
                                _gc_text = _gc_resp.text or "" if hasattr(_gc_resp, "text") else str(_gc_resp)
                                if _gc_text.strip():
                                    logger.info(
                                        "GroupChat retry succeeded for '%s' on attempt %d",
                                        _completed_agent_name, _gc_attempt,
                                    )
                                    _accumulated_chunks.clear()
                                    _accumulated_chunks.append(_gc_text)
                                    _completed_agent_text = _gc_text
                                    break
                                logger.warning(
                                    "GroupChat retry for '%s' attempt %d still empty",
                                    _completed_agent_name, _gc_attempt,
                                )
                            except (asyncio.TimeoutError, Exception) as _gc_exc:
                                logger.warning(
                                    "GroupChat retry for '%s' attempt %d failed: %s",
                                    _completed_agent_name, _gc_attempt, _gc_exc,
                                )

                if _accumulated_chunks and _accumulating_agent:
                    for _ev in _finalize_agent_response(_accumulating_agent, _accumulated_chunks):
                        yield _ev
                    _accumulated_chunks.clear()
                    _accumulating_agent = ""

                # Narrator: generate narration for this agent turn (non-blocking)
                if narrator_enabled and narrator_agent and _completed_agent_name and _completed_agent_text:
                    try:
                        async for narr_ev in narrate_agent_turn(
                            narrator_agent=narrator_agent,
                            agent_name=_completed_agent_name,
                            agent_output=_completed_agent_text,
                            phase=investigation.phase.value,
                            investigation=investigation,
                            xcv=xcv,
                        ):
                            yield narr_ev
                    except Exception as narr_exc:
                        logger.warning(
                            "Narrator error (non-fatal) for agent=%s: %s",
                            _completed_agent_name, narr_exc,
                        )

                # Early termination: if investigation reached COMPLETE, stop the loop.
                # The framework's termination_condition may not fire reliably, so we
                # check here after every agent turn.
                if investigation.phase == InvestigationPhase.COMPLETE:
                    logger.info(
                        "Investigation reached COMPLETE phase after agent=%s "
                        "(responses=%d) → ending workflow loop",
                        evt_executor, agent_response_count,
                    )
                    break
                continue

            if event.type == "output":
                data = event.data

                if isinstance(data, AgentResponseUpdate):
                    # Streaming chunk — accumulate silently, do NOT yield to UI.
                    # Complete response is sent via _finalize_agent_response
                    # when executor_completed fires.
                    agent_name = event.executor_id or "unknown"
                    _accumulating_agent = agent_name

                    if agent_name != current_agent:
                        _drain_tool_calls(current_agent or "unknown")
                        current_agent = agent_name
                        tracker.log_agent_invoked(xcv, agent_name, task[:500])
                        if prompts and agent_name in prompts:
                            tracker.log_agent_prompt_used(xcv, agent_name, prompts[agent_name])

                        yield {
                            "type": "investigation_agent_start",
                            "agent": agent_name,
                            "phase": investigation.phase.value,
                            "investigation_id": investigation.id,
                        }

                    text = ""
                    if hasattr(data, "text") and data.text:
                        text = data.text
                    if text:
                        _accumulated_chunks.append(text)

                elif isinstance(data, AgentResponse):
                    # Non-streaming fallback: extract full text and accumulate
                    agent_name = event.executor_id or "unknown"
                    _accumulating_agent = agent_name

                    if agent_name != current_agent:
                        _drain_tool_calls(current_agent or "unknown")
                        current_agent = agent_name
                        tracker.log_agent_invoked(xcv, agent_name, task[:500])
                        if prompts and agent_name in prompts:
                            tracker.log_agent_prompt_used(xcv, agent_name, prompts[agent_name])

                        yield {
                            "type": "investigation_agent_start",
                            "agent": agent_name,
                            "phase": investigation.phase.value,
                            "investigation_id": investigation.id,
                        }

                    messages_text = []
                    for msg in data.messages:
                        if msg.text:
                            messages_text.append(msg.text)
                    full_text = "\n".join(messages_text)
                    if full_text:
                        _accumulated_chunks.append(full_text)

                elif isinstance(data, list):
                    # Final conversation messages
                    pass

                else:
                    logger.debug(
                        "Unhandled output data type: %s (executor=%s)",
                        type(data).__name__, evt_executor,
                    )

            elif evt_type == "error":
                # Framework-level error event — log and surface to UI
                error_data = getattr(event, "data", None)
                error_msg = str(error_data) if error_data else "Unknown workflow error"
                logger.error(
                    "Workflow error event: executor=%s, error=%s",
                    evt_executor, error_msg,
                )
                tracker.log_investigation_error(
                    xcv=xcv,
                    investigation_id=investigation.id,
                    error=f"Workflow error: {error_msg}",
                    phase=investigation.phase.value,
                )
                yield {
                    "type": "investigation_workflow_error",
                    "investigation_id": investigation.id,
                    "agent": evt_executor or current_agent or "unknown",
                    "error": error_msg,
                    "phase": investigation.phase.value,
                }

            else:
                # Log any other event types we don't handle yet
                logger.debug(
                    "Unhandled workflow event type=%s, executor=%s",
                    evt_type, evt_executor,
                )

    except Exception as exc:
        classified = classify_exception(exc)
        error_detail = str(exc)
        logger.exception(
            "Investigation workflow failed for %s/%s: [%s] %s",
            result.customer_name, result.service_tree_id,
            type(classified).__name__, error_detail,
        )
        tracker.log_investigation_error(
            xcv=xcv,
            investigation_id=investigation.id,
            error=f"[{type(classified).__name__}] {error_detail}",
            phase=investigation.phase.value,
        )
        tracker.end_investigation_span(error=error_detail)
        tracker.log_request_end(xcv, status="error", error=error_detail)
        yield {
            "type": "investigation_error",
            "investigation_id": investigation.id,
            "error": f"Investigation workflow failed: {error_detail}",
            "error_category": type(classified).__name__,
            "phase": investigation.phase.value,
            "last_agent": current_agent or "unknown",
        }
        if not feeder_task.done():
            feeder_task.cancel()
        await _close_all_agents()
        return

    # ── Post-workflow diagnostics ──────────────────────────────
    total_workflow_dur = round(time.monotonic() - workflow_start_time, 1)

    # Drain LLM middleware to capture any errors from the final (or only) LLM call
    final_llm_calls = llm_log_mw.drain() if llm_log_mw else []
    llm_errors = [c for c in final_llm_calls if c.get("error")]
    if llm_errors:
        for lc in llm_errors:
            logger.error(
                "LLM call error detected: agent=%s, model=%s, error=%s, duration_ms=%s",
                lc.get("agent", "?"), lc.get("model", "?"),
                lc.get("error", "?"), lc.get("duration_ms", "?"),
            )

    logger.info(
        "Investigation workflow loop ended: agent_responses=%d, events=%d, "
        "phase=%s, last_agent=%s, total_time=%.1fs, llm_calls=%d, llm_errors=%d",
        agent_response_count, event_count, investigation.phase.value,
        current_agent or "none", total_workflow_dur,
        len(final_llm_calls), len(llm_errors),
    )

    # Detect silent workflow failure: the framework may swallow LLM errors
    # and terminate the GroupChat without raising.  If zero agent responses
    # were produced, something went wrong.
    if agent_response_count == 0:
        # Build a diagnostic message from whatever the LLM middleware captured
        diag_parts = [
            "Investigation workflow produced 0 agent responses "
            f"(total_time={total_workflow_dur}s, events={event_count})."
        ]
        if llm_errors:
            for lc in llm_errors:
                diag_parts.append(
                    f"  LLM error: agent={lc.get('agent')}, model={lc.get('model')}, "
                    f"error={lc.get('error')}, duration_ms={lc.get('duration_ms')}"
                )
        elif final_llm_calls:
            # LLM call succeeded but framework didn't produce a response
            for lc in final_llm_calls:
                diag_parts.append(
                    f"  LLM call: agent={lc.get('agent')}, model={lc.get('model')}, "
                    f"finish_reason={lc.get('finish_reason')}, "
                    f"duration_ms={lc.get('duration_ms')}, "
                    f"tokens={lc.get('total_tokens')}"
                )
        else:
            diag_parts.append(
                "  No LLM calls captured — the framework may not have dispatched any."
            )

        warning = "\n".join(diag_parts)
        logger.warning(warning)
        tracker.log_investigation_error(
            xcv=xcv,
            investigation_id=investigation.id,
            error=warning,
            phase=investigation.phase.value,
        )
        tracker.end_investigation_span(error=warning)
        tracker.log_request_end(xcv, status="error", error=warning)
        yield {
            "type": "investigation_error",
            "investigation_id": investigation.id,
            "error": warning,
        }
        await _close_all_agents()
        return

    # ── Mark evidence collection status ─────────────────────────
    # After the GroupChat finishes, reconcile preliminary_verdict for every
    # evidence item so the UI never shows stale "PENDING" badges:
    #   - Has summary → data was collected (even negative findings count)
    #   - No summary → never collected → "not_available"
    for ei in investigation.evidence:
        if ei.final_verdict:
            continue  # already has a definitive verdict — leave it
        if ei.preliminary_verdict:
            continue  # already classified by collector/planner — leave it
        if ei.summary:
            ei.preliminary_verdict = "collected"
        else:
            ei.preliminary_verdict = "not_available"

    # Also create placeholder items for ER-IDs that were never even attempted
    _collected = investigation.collected_er_ids
    for hyp in investigation.hypotheses:
        for er_id in (hyp.evidence_needed or []):
            if er_id not in _collected:
                investigation.evidence.append(EvidenceItem(
                    id=f"ev-na-{er_id.lower().replace('-', '_')}",
                    er_id=er_id,
                    hypothesis_ids=[hyp.id],
                    agent_name="system",
                    tool_name="",
                    summary="No data available",
                    preliminary_verdict="not_available",
                ))
                _collected = investigation.collected_er_ids  # refresh

    # ══════════════════════════════════════════════════════════════
    # STAGE 4: Run action_planner STANDALONE (outside GroupChat)
    # ══════════════════════════════════════════════════════════════
    # Action planning runs after ALL hypotheses are evaluated so it can:
    #   - See every confirmed/contributing hypothesis at once
    #   - Deduplicate actions that apply to multiple hypotheses
    #   - Plan actions without consuming GroupChat turns

    confirmed = investigation.confirmed_hypotheses()
    contributing = [
        h for h in investigation.hypotheses
        if h.status == HypothesisStatus.CONTRIBUTING
    ]
    actionable = confirmed + contributing

    action_agent_name = agent_roles.get("action_planner", inv_workflow_cfg.get("action_agent", "action_planner"))
    action_agent = all_agents_dict.get(action_agent_name)

    if actionable and action_agent:
        _prev_phase_act = investigation.phase.value
        investigation.transition_to(InvestigationPhase.ACTING, source="runner:action_planning")
        tracker.log_phase_transition(
            xcv=xcv,
            investigation_id=investigation.id,
            from_phase=_prev_phase_act,
            to_phase=investigation.phase.value,
            agent_name="runner",
        )
        set_current_tool_stage(f"investigation:{investigation.phase.value}")

        action_task = _build_action_task(investigation, actionable)
        logger.info(
            "Running action_planner standalone for %d actionable hypotheses "
            "(%d confirmed, %d contributing)",
            len(actionable), len(confirmed), len(contributing),
        )

        yield {
            "type": "investigation_agent_start",
            "agent": action_agent_name,
            "phase": investigation.phase.value,
            "investigation_id": investigation.id,
        }

        tracker.log_agent_invoked(xcv, action_agent_name, action_task[:500])
        if prompts and action_agent_name in prompts:
            tracker.log_agent_prompt_used(xcv, action_agent_name, prompts[action_agent_name])

        _action_retry = _get_retry_policy(action_agent_name, retry_cfg)
        _ACTION_MAX_ATTEMPTS = max(_action_retry["max_retries"], 1)
        action_text = ""
        for _act_attempt in range(1, _ACTION_MAX_ATTEMPTS + 1):
            logger.info("Running action_planner standalone (attempt %d/%d)", _act_attempt, _ACTION_MAX_ATTEMPTS)
            try:
                if capture_mw:
                    capture_mw.reset()
                _action_timeout = _get_agent_timeout(action_agent_name, timeout_cfg)
                action_response = await asyncio.wait_for(
                    action_agent.run(action_task),
                    timeout=_action_timeout,
                )
                action_text = action_response.text if hasattr(action_response, "text") else str(action_response)
            except asyncio.TimeoutError:
                action_text = ""
                logger.error(
                    "Action planner timed out after %.0fs (attempt %d/%d)",
                    _action_timeout, _act_attempt, _ACTION_MAX_ATTEMPTS,
                )
                if _act_attempt == _ACTION_MAX_ATTEMPTS:
                    tracker.log_investigation_error(
                        xcv=xcv,
                        investigation_id=investigation.id,
                        error=f"Action planner timed out after {_action_timeout}s ({_ACTION_MAX_ATTEMPTS} attempts)",
                        phase=investigation.phase.value,
                    )
                    yield {
                        "type": "investigation_workflow_error",
                        "investigation_id": investigation.id,
                        "agent": action_agent_name,
                        "error": f"Action planner timed out after {_action_timeout}s ({_ACTION_MAX_ATTEMPTS} attempts)",
                        "phase": investigation.phase.value,
                    }
                else:
                    tracker.log_agent_retry(
                        xcv=xcv, agent_name=action_agent_name, attempt=_act_attempt,
                        max_retries=_ACTION_MAX_ATTEMPTS, reason=f"timeout after {_action_timeout}s",
                        investigation_id=investigation.id, phase=investigation.phase.value,
                    )
                    _waited = await _backoff_sleep(_action_retry, _act_attempt)
                    if _waited:
                        logger.info("Action planner backoff: %.1fs before attempt %d", _waited, _act_attempt + 1)
                continue
            except Exception as exc:
                classified = classify_exception(exc)
                action_text = ""
                logger.error(
                    "Action planner standalone failed (attempt %d): [%s] %s",
                    _act_attempt, type(classified).__name__, exc, exc_info=True,
                )
                if isinstance(classified, AuthError) or not classified.retryable:
                    tracker.log_investigation_error(
                        xcv=xcv,
                        investigation_id=investigation.id,
                        error=f"Action planner failed ({type(classified).__name__}): {exc}",
                        phase=investigation.phase.value,
                    )
                    yield {
                        "type": "investigation_workflow_error",
                        "investigation_id": investigation.id,
                        "agent": action_agent_name,
                        "error": f"Action planner failed ({type(classified).__name__}): {exc}",
                        "error_category": type(classified).__name__,
                        "phase": investigation.phase.value,
                    }
                    break
                if _act_attempt == _ACTION_MAX_ATTEMPTS:
                    tracker.log_investigation_error(
                        xcv=xcv,
                        investigation_id=investigation.id,
                        error=f"Action planner failed after {_ACTION_MAX_ATTEMPTS} attempts ({type(classified).__name__}): {exc}",
                        phase=investigation.phase.value,
                    )
                    yield {
                        "type": "investigation_workflow_error",
                        "investigation_id": investigation.id,
                        "agent": action_agent_name,
                        "error": f"Action planner failed after {_ACTION_MAX_ATTEMPTS} attempts ({type(classified).__name__}): {exc}",
                        "error_category": type(classified).__name__,
                        "phase": investigation.phase.value,
                    }
                else:
                    tracker.log_agent_retry(
                        xcv=xcv, agent_name=action_agent_name, attempt=_act_attempt,
                        max_retries=_ACTION_MAX_ATTEMPTS, reason=f"{type(classified).__name__}: {exc}",
                        investigation_id=investigation.id, phase=investigation.phase.value,
                    )
                    _waited = await _backoff_sleep(_action_retry, _act_attempt)
                    if _waited:
                        logger.info("Action planner backoff: %.1fs before attempt %d", _waited, _act_attempt + 1)
                continue

            if action_text.strip():
                break  # success

            # Empty response — retry if allowed
            if _act_attempt < _ACTION_MAX_ATTEMPTS:
                logger.warning("Action planner returned empty response (attempt %d)", _act_attempt)
                tracker.log_agent_retry(
                    xcv=xcv, agent_name=action_agent_name, attempt=_act_attempt,
                    max_retries=_ACTION_MAX_ATTEMPTS, reason="empty response",
                    investigation_id=investigation.id, phase=investigation.phase.value,
                )
                _waited = await _backoff_sleep(_action_retry, _act_attempt)
                if _waited:
                    logger.info("Action planner backoff: %.1fs before attempt %d", _waited, _act_attempt + 1)

        if action_text.strip():
            tracker.log_agent_response(xcv, action_agent_name, action_text)

            parsed_action = parse_agent_output(action_text, agent_name=action_agent_name)
            apply_to_investigation(parsed_action, investigation)

            # Deduplicate actions across hypotheses
            if investigation.actions:
                investigation.actions = deduplicate_actions(investigation.actions)
                logger.info(
                    "Action planner produced %d deduplicated actions",
                    len(investigation.actions),
                )

            tool_calls = _drain_tool_calls(action_agent_name)
            llm_calls = llm_log_mw.drain() if llm_log_mw else []

            yield {
                "type": "investigation_agent_response",
                "agent": action_agent_name,
                "text": action_text,
                "phase": investigation.phase.value,
                "investigation_id": investigation.id,
                "parsed_signals": {
                    "phase_complete": parsed_action.signals.phase_complete,
                    "next_agent": parsed_action.signals.next_agent,
                    "investigation_resolved": False,
                    "needs_more_evidence": False,
                },
                "symptoms_count": len(investigation.symptoms),
                "hypotheses_count": len(investigation.hypotheses),
                "evidence_count": len(investigation.evidence),
                "actions_count": len(investigation.actions),
                "evidence_cycle_count": 0,
                "symptoms": _serialize_symptoms(investigation),
                "hypotheses": _serialize_hypotheses(investigation),
                "evidence": _serialize_evidence(investigation),
                "actions": _serialize_actions(investigation),
                "symptom_verdicts_summary": _symptom_verdicts_summary(investigation),
                **({"tool_calls": tool_calls} if tool_calls else {}),
                **({"llm_calls": llm_calls} if llm_calls else {}),
            }

            # Narrator: narrate the action_planner stage
            if narrator_enabled and narrator_agent and action_text.strip():
                try:
                    async for narr_ev in narrate_stage(
                        narrator_agent=narrator_agent,
                        stage_name=action_agent_name,
                        stage_output=action_text,
                        phase=investigation.phase.value,
                        investigation=investigation,
                        xcv=xcv,
                    ):
                        yield narr_ev
                except Exception as narr_exc:
                    logger.warning("Narrator error (non-fatal) for action_planner: %s", narr_exc)

    elif actionable and not action_agent:
        logger.warning(
            "Action agent '%s' not found in agents — skipping action planning",
            action_agent_name,
        )
    else:
        logger.info(
            "No confirmed/contributing hypotheses — skipping action planning "
            "(confirmed=%d, contributing=%d, refuted=%d)",
            len(confirmed), len(contributing),
            sum(1 for h in investigation.hypotheses if h.status == HypothesisStatus.REFUTED),
        )

    # Mark complete
    if investigation.phase != InvestigationPhase.COMPLETE:
        _prev_phase_fin = investigation.phase.value
        investigation.transition_to(InvestigationPhase.COMPLETE, source="runner:finalize", force=True)
        tracker.log_phase_transition(
            xcv=xcv,
            investigation_id=investigation.id,
            from_phase=_prev_phase_fin,
            to_phase=investigation.phase.value,
            agent_name="runner",
        )
    investigation.completed_at = datetime.now(timezone.utc).isoformat()

    # Clear tool stage context — investigation is done
    set_current_tool_stage(None)

    yield {
        "type": "investigation_complete",
        "investigation_id": investigation.id,
        "phase": investigation.phase.value,
        "symptoms_count": len(investigation.symptoms),
        "hypotheses_count": len(investigation.hypotheses),
        "evidence_count": len(investigation.evidence),
        "actions_count": len(investigation.actions),
        "evidence_cycles": investigation.evidence_cycles,
        "duration_seconds": _duration_seconds(investigation),
        "symptoms": _serialize_symptoms(investigation),
        "hypotheses": _serialize_hypotheses(investigation),
        "evidence": _serialize_evidence(investigation),
        "actions": _serialize_actions(investigation),
        "symptom_verdicts_summary": _symptom_verdicts_summary(investigation),
    }

    # Log investigation complete
    tracker.log_investigation_complete(
        xcv=xcv,
        investigation_id=investigation.id,
        symptoms_count=len(investigation.symptoms),
        hypotheses_count=len(investigation.hypotheses),
        evidence_count=len(investigation.evidence),
        actions_count=len(investigation.actions),
        evidence_cycles=investigation.evidence_cycles,
        duration_seconds=_duration_seconds(investigation),
    )
    tracker.end_investigation_span()
    tracker.log_request_end(xcv, status="complete")

    # ── Clean up feeder task and MCP connections ─────────────────────
    if not feeder_task.done():
        feeder_task.cancel()
    await _close_all_agents()


def _duration_seconds(investigation: Investigation) -> float:
    """Calculate investigation duration in seconds."""
    try:
        start = datetime.fromisoformat(investigation.started_at)
        end = datetime.fromisoformat(investigation.completed_at) if investigation.completed_at else datetime.now(timezone.utc)
        return (end - start).total_seconds()
    except (ValueError, TypeError):
        return 0.0


def _serialize_symptoms(investigation: Investigation) -> list[dict]:
    """Serialize symptoms linked to at least one hypothesis for UI panels."""
    linked_ids = set()
    for h in investigation.hypotheses:
        linked_ids.update(h.matched_symptoms)
    return [
        {"id": s.id, "text": s.text, "category": s.category}
        for s in investigation.symptoms
        if s.id in linked_ids
    ]


def _serialize_hypotheses(investigation: Investigation) -> list[dict]:
    """Serialize all hypotheses with current status for UI panels."""
    return [
        {
            "id": h.id,
            "statement": h.statement,
            "status": h.status.value if hasattr(h.status, "value") else str(h.status),
            "confidence": h.confidence,
            "match_score": h.match_score,
        }
        for h in investigation.hypotheses
    ]


def _serialize_evidence(investigation: Investigation) -> list[dict]:
    """Serialize collected evidence items for UI panels."""
    return [
        {
            "id": ei.id,
            "er_id": ei.er_id,
            "summary": ei.summary or ei.tool_name,
            "verdict": ei.final_verdict.value if ei.final_verdict else "",
            "preliminary_verdict": ei.preliminary_verdict or (
                "collected" if (ei.summary or ei.raw_data) else ""
            ),
        }
        for ei in investigation.evidence
    ]


def _serialize_actions(investigation: Investigation) -> list[dict]:
    """Serialize planned actions for UI panels."""
    return [
        {
            "action_id": a.get("action_id", ""),
            "display_name": a.get("display_name", ""),
            "tier": a.get("tier", ""),
            "priority": a.get("priority", 0),
        }
        for a in investigation.actions
    ]


def _symptom_verdicts_summary(investigation: Investigation) -> dict:
    """Aggregate symptom verdict counts across all hypotheses."""
    from .investigation_state import SymptomVerdict

    totals: dict[str, int] = {v.value: 0 for v in SymptomVerdict}
    per_hyp: dict[str, dict[str, int]] = {}
    for hyp in investigation.hypotheses:
        if not hyp.symptom_verdicts:
            continue
        counts = {v.value: 0 for v in SymptomVerdict}
        for sv in hyp.symptom_verdicts.values():
            counts[sv.value] = counts.get(sv.value, 0) + 1
            totals[sv.value] = totals.get(sv.value, 0) + 1
        per_hyp[hyp.id] = counts
    return {"totals": totals, "per_hypothesis": per_hyp}


async def on_group_chat_callback(result: SignalBuilderResult) -> None:
    """Convenience callback for signal_builder's run_signal_builder_loop.

    Consumes the async iterator from run_investigation and logs events.
    In production, this would be replaced with an SSE/websocket emitter.
    """
    async for event in run_investigation(result):
        event_type = event.get("type", "unknown")

        if event_type == "investigation_started":
            logger.info(
                "Investigation %s started for %s/%s (%d signals, %d compounds)",
                event["investigation_id"],
                event["customer_name"],
                event["service_tree_id"],
                event["signal_count"],
                event["compound_count"],
            )

        elif event_type == "investigation_agent_response":
            logger.info(
                "[%s] %s responded (phase=%s, signals=%s)",
                event["investigation_id"],
                event["agent"],
                event["phase"],
                event["parsed_signals"],
            )

        elif event_type == "investigation_complete":
            logger.info(
                "Investigation %s complete: %d symptoms, %d hypotheses, %d evidence, %d actions (%.1fs)",
                event["investigation_id"],
                event["symptoms_count"],
                event["hypotheses_count"],
                event["evidence_count"],
                event["actions_count"],
                event["duration_seconds"],
            )

        elif event_type == "investigation_error":
            logger.error(
                "Investigation %s error: %s",
                event["investigation_id"],
                event.get("error"),
            )
