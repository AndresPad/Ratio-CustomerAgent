"""SignalBuilder – deterministic signal detection pipeline.

Periodically calls MCP collection tools, evaluates activation rules
against returned data, computes signal strengths, evaluates compound
signals, and decides whether to invoke the GroupChat.

This is NOT an LLM agent — all logic is programmatic.
"""
from __future__ import annotations

import ast
import asyncio
import json
import logging
import math
import operator
import os
import re as _re
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, AsyncIterator, Awaitable, Callable

from .sources.kusto_signal_source import KustoSignalSource
from .signal_models import (
    ActivatedSignal,
    CompoundSignalResult,
    SignalBuilderResult,
    TypeSignalResult,
)
from helper.errors import (
    PipelineError, NetworkError, AuthError, ToolError, ConfigError,
    classify_exception,
)
from helper.agent_logger import AgentLogger, get_current_xcv, set_current_xcv, generate_xcv, set_current_service_tree_id, set_current_customer_name

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "config"))

# ── ADLS persistence (in-memory pipeline) ─────────────────────────
# Lazy singleton SandboxClient used only for ADLS uploads from the
# non-sandboxed evaluators. Created on first use so that environments
# without ADLS configured can skip persistence silently.
_PERSIST_CLIENT: Any = None
_PERSIST_DISABLED = False


def _get_persist_client():
    """Return a SandboxClient for ADLS uploads, or None if unavailable."""
    global _PERSIST_CLIENT, _PERSIST_DISABLED
    if _PERSIST_DISABLED:
        return None
    if _PERSIST_CLIENT is not None:
        return _PERSIST_CLIENT
    if not (os.getenv("ADLS_ACCOUNT") and os.getenv("ADLS_FILESYSTEM")):
        _PERSIST_DISABLED = True
        logger.warning(
            "ADLS_ACCOUNT/ADLS_FILESYSTEM not set; signal persistence disabled "
            "(ADLS_ACCOUNT=%r ADLS_FILESYSTEM=%r)",
            os.getenv("ADLS_ACCOUNT"), os.getenv("ADLS_FILESYSTEM"),
        )
        return None
    try:
        from core.sandbox.client import SandboxClient
        _PERSIST_CLIENT = SandboxClient()
        logger.info("ADLS persistence client initialised (account=%s fs=%s)",
                    os.getenv("ADLS_ACCOUNT"), os.getenv("ADLS_FILESYSTEM"))
    except Exception:
        _PERSIST_DISABLED = True
        logger.exception("Could not initialise SandboxClient for ADLS persistence")
        return None
    return _PERSIST_CLIENT


async def _persist_result_to_adls(
    context: dict[str, Any],
    type_results_list: list[TypeSignalResult],
    result: SignalBuilderResult,
) -> None:
    """Best-effort write of raw-matched rows + aggregated per-type + final result to ADLS.

    Layout (controlled by env ``SIGNAL_PERSIST_LAYOUT``; default ``per_grain``):

    ``flat`` (legacy)::

        {ADLS_BASE_PATH}/{xcv}/signals/raw/{type_id}.json         — matched rows (all grains pooled)
        {ADLS_BASE_PATH}/{xcv}/signals/aggregated/{type_id}.json  — per-type aggregate

    ``per_grain``::

        {ADLS_BASE_PATH}/{xcv}/signals/raw/{type_id}/{granularity}.json         — one raw file per activated grain
        {ADLS_BASE_PATH}/{xcv}/signals/aggregated/{type_id}/{granularity}.json  — one aggregated file per activated grain

    Common::

        {ADLS_BASE_PATH}/{xcv}/signals/result.json    — full SignalBuilderResult
        {ADLS_BASE_PATH}/{xcv}/signals/manifest.json  — index of all artifacts
    """
    client = _get_persist_client()
    if client is None:
        logger.info("ADLS persist skipped (client unavailable)")
        return

    xcv = result.xcv or get_current_xcv()
    if not xcv:
        logger.warning("ADLS persist skipped (no XCV in scope)")
        return

    base = os.getenv("ADLS_BASE_PATH", "customeragent").strip("/")
    prefix = f"{base}/{xcv}/signals"
    layout = os.getenv("SIGNAL_PERSIST_LAYOUT", "per_grain").strip().lower()
    if layout not in ("flat", "per_grain"):
        logger.warning("Unknown SIGNAL_PERSIST_LAYOUT=%r, defaulting to 'per_grain'", layout)
        layout = "per_grain"
    t0 = time.monotonic()
    logger.info(
        "ADLS persist starting prefix=%s types=%d layout=%s",
        prefix, len(type_results_list), layout,
    )

    # Per-type manifest entries built alongside writes so we can record
    # the exact files emitted for each granularity.
    manifest_signal_types: list[dict[str, Any]] = []
    files_written: list[str] = []
    try:
        # Per-type raw matched rows + aggregated record
        for tr in type_results_list:
            grain_entries: list[dict[str, Any]] = []

            if layout == "per_grain":
                # One raw + one aggregated file per (type, granularity), using
                # the exact grain name from the signal template as filename.
                for sig in tr.activated_signals:
                    grain_rows: list[dict[str, Any]] = []
                    for row in sig.matched_rows:
                        enriched = dict(row)
                        enriched.setdefault("_signal_type_id", tr.signal_type_id)
                        enriched.setdefault("_granularity", sig.granularity)
                        grain_rows.append(enriched)
                    raw_rel = f"raw/{tr.signal_type_id}/{sig.granularity}.json"
                    agg_rel = f"aggregated/{tr.signal_type_id}/{sig.granularity}.json"
                    raw_path = f"{prefix}/{raw_rel}"
                    agg_path = f"{prefix}/{agg_rel}"
                    await client.upload_file(raw_path, json.dumps(grain_rows, default=str, indent=2))
                    await client.upload_file(agg_path, json.dumps(sig.to_dict(), default=str, indent=2))
                    files_written.extend([raw_path, agg_path])
                    grain_entries.append({
                        "granularity": sig.granularity,
                        "raw_file": raw_rel,
                        "aggregated_file": agg_rel,
                        "row_count": len(grain_rows),
                        "confidence": sig.confidence,
                        "strength": round(sig.strength, 4),
                        "raw_strength": round(sig.raw_strength, 4),
                    })
                primary_raw_file: Any = None
                primary_agg_file: Any = None
            else:
                # Legacy: one pooled raw + one pooled aggregated file per type.
                agg_rel = f"aggregated/{tr.signal_type_id}.json"
                agg_path = f"{prefix}/{agg_rel}"
                await client.upload_file(agg_path, json.dumps(tr.to_dict(), default=str, indent=2))
                files_written.append(agg_path)

                matched_rows: list[dict[str, Any]] = []
                for sig in tr.activated_signals:
                    for row in sig.matched_rows:
                        enriched = dict(row)
                        enriched.setdefault("_signal_type_id", tr.signal_type_id)
                        enriched.setdefault("_granularity", sig.granularity)
                        matched_rows.append(enriched)
                raw_rel = f"raw/{tr.signal_type_id}.json"
                raw_path = f"{prefix}/{raw_rel}"
                await client.upload_file(raw_path, json.dumps(matched_rows, default=str, indent=2))
                files_written.append(raw_path)
                grain_entries = [
                    {"granularity": s.granularity} for s in tr.activated_signals
                ]
                primary_raw_file = raw_rel
                primary_agg_file = agg_rel

            manifest_signal_types.append({
                "id": tr.signal_type_id,
                "name": tr.signal_name,
                "raw_file": primary_raw_file,
                "aggregated_file": primary_agg_file,
                "row_count": tr.row_count,
                "has_data": tr.has_data,
                "max_strength": round(tr.max_strength, 4),
                "best_confidence": tr.best_confidence,
                "activated_granularities": grain_entries,
            })

        # Final result envelope (mirrors SignalBuilderResult.to_dict)
        result_path = f"{prefix}/result.json"
        await client.upload_file(
            result_path,
            json.dumps(result.to_dict(), default=str, indent=2),
        )
        files_written.append(result_path)

        # Manifest (mirrors sandboxed-pipeline DataFetchManifest schema so
        # downstream consumers can use one contract regardless of which
        # path produced the artifacts).
        manifest = {
            "xcv": xcv,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "customer_name": result.customer_name,
            "service_tree_id": result.service_tree_id,
            "service_name": result.service_name,
            "start_time": result.start_time,
            "end_time": result.end_time,
            "output_dir": prefix,
            "source": "in_memory_pipeline",
            "layout": layout,
            "signal_types": manifest_signal_types,
            "compound_results": [
                {
                    "id": c.compound_id,
                    "name": c.compound_name,
                    "activated": c.activated,
                    "confidence": c.confidence,
                    "strength": round(c.strength, 4),
                }
                for c in result.compound_results
            ],
            "action": result.action,
            "result_file": "result.json",
        }
        manifest_path = f"{prefix}/manifest.json"
        await client.upload_file(manifest_path, json.dumps(manifest, default=str, indent=2))
        files_written.append(manifest_path)

        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        logger.info(
            "ADLS persist complete prefix=%s types=%d files=%d duration_ms=%.1f layout=%s",
            prefix, len(type_results_list), len(files_written), elapsed_ms, layout,
        )

        # Emit App Insights event (same pattern as data_fetch_complete in fetch_tools.py)
        try:
            AgentLogger.get_instance()._emit("signal_persist_complete", xcv, {
                "AdlsAccount": os.getenv("ADLS_ACCOUNT", ""),
                "AdlsFilesystem": os.getenv("ADLS_FILESYSTEM", ""),
                "Prefix": prefix,
                "Layout": layout,
                "TypeCount": len(type_results_list),
                "FileCount": len(files_written),
                "DurationMs": elapsed_ms,
                "Action": result.action,
                "ServiceTreeId": result.service_tree_id,
                "CustomerName": result.customer_name,
            })
        except Exception:
            logger.debug("signal_persist_complete telemetry emit failed", exc_info=True)
    except Exception as exc:
        logger.exception("ADLS persistence failed for xcv=%s prefix=%s", xcv, prefix)
        try:
            AgentLogger.get_instance()._emit("signal_persist_failed", xcv, {
                "AdlsAccount": os.getenv("ADLS_ACCOUNT", ""),
                "AdlsFilesystem": os.getenv("ADLS_FILESYSTEM", ""),
                "Prefix": prefix,
                "TypeCount": len(type_results_list),
                "FilesWritten": len(files_written),
                "Error": str(exc),
                "ErrorType": type(exc).__name__,
            })
        except Exception:
            logger.debug("signal_persist_failed telemetry emit failed", exc_info=True)


# ── Config loaders ────────────────────────────────────────────────

def _load_json(filename: str) -> dict[str, Any]:
    path = os.path.join(_CONFIG_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_signal_template() -> dict[str, Any]:
    from pydantic import ValidationError
    from core.models.config.signal_template import SignalTemplateFileConfig

    data = _load_json("signals/signal_template.json")
    try:
        SignalTemplateFileConfig.model_validate(data)
    except ValidationError as exc:
        logger.error("signal_template.json validation failed: %s", exc)
        raise ValueError(
            f"Invalid signal template config 'signal_template.json': {exc}"
        ) from exc
    return data


def load_monitoring_context() -> dict[str, Any]:
    from pydantic import ValidationError
    from core.models.config.monitoring_context import MonitoringContextFileConfig

    data = _load_json("monitoring_context.json")
    try:
        MonitoringContextFileConfig.model_validate(data)
    except ValidationError as exc:
        logger.error("monitoring_context.json validation failed: %s", exc)
        raise ValueError(
            f"Invalid monitoring context config 'monitoring_context.json': {exc}"
        ) from exc
    return data




# ── Collection strategy registry ─────────────────────────────────
# Maps a collection_strategy name (from signal_template.json) to an
# async evaluation function with signature:
#   (sig_type: dict, context: dict) -> TypeSignalResult
#
# The "standard" strategy (no explicit collection_strategy in config)
# and "dependency_scan" are registered after their functions are defined,
# at the bottom of this section.  External code can register additional
# strategies via register_collection_strategy().

CollectionStrategyFn = Callable[
    [dict[str, Any], dict[str, Any]],
    Awaitable["TypeSignalResult"],
]

_COLLECTION_STRATEGIES: dict[str, CollectionStrategyFn] = {}


def register_collection_strategy(
    name: str,
    fn: CollectionStrategyFn,
) -> None:
    """Register a named collection strategy for use in signal_template.json.

    Each signal type's ``collection_strategy`` field is looked up in this
    registry at evaluation time.  If absent, falls back to ``"standard"``.

    Args:
        name: Strategy key referenced from signal_template.json.
        fn:   Async callable ``(sig_type_cfg, context) -> TypeSignalResult``.
    """
    _COLLECTION_STRATEGIES[name] = fn
    logger.info("Registered collection strategy: %s", name)


# Global semaphore for MCP call concurrency control (P0-B).
# Limits how many parallel MCP/Kusto calls run at once to avoid
# overwhelming the source system.  Initialised lazily by
# init_mcp_semaphore() before the first evaluation cycle.
_MCP_SEMAPHORE: asyncio.Semaphore | None = None


def init_mcp_semaphore(max_concurrent: int = 5) -> None:
    """Initialise the global MCP concurrency semaphore.

    Called once at the start of an evaluation cycle with the value from
    ``signal_template.json  →  max_concurrent_mcp_calls`` (default 5).
    Safe to call multiple times — subsequent calls are no-ops.
    """
    global _MCP_SEMAPHORE
    if _MCP_SEMAPHORE is None:
        _MCP_SEMAPHORE = asyncio.Semaphore(max_concurrent)
        logger.info("MCP concurrency semaphore initialised: max_concurrent=%d", max_concurrent)


# ── MCP tool caller ──────────────────────────────────────────────

async def _call_collection_tool(
    tool_name: str,
    params: dict[str, str],
    service_name: str = "",
) -> list[dict[str, Any]]:
    """Call an MCP collection tool and return parsed rows.

    Acquires the global semaphore to bound concurrency, then delegates
    to KustoSignalSource for the actual MCP call.
    """
    # Lazy fallback — ensures a semaphore always exists even if
    # init_mcp_semaphore() was never called (e.g. unit tests).
    if _MCP_SEMAPHORE is None:
        init_mcp_semaphore()
    import time as _time
    source = KustoSignalSource(
        tool_name=tool_name,
        params={},
        field_mappings={},
        source_type="kusto",
        signal_type="collection",
    )
    logger.debug("Calling MCP tool %s (service=%s)", tool_name, service_name)
    t0 = _time.monotonic()
    try:
        async with _MCP_SEMAPHORE:
            rows = await source.fetch_signals(params)
        elapsed = round((_time.monotonic() - t0) * 1000, 1)
        logger.debug("MCP tool %s returned %d rows in %.1fms", tool_name, len(rows), elapsed)
        xcv = get_current_xcv()
        if xcv:
            AgentLogger.get_instance().log_mcp_collection_call(
                xcv=xcv, tool_name=tool_name, parameters=params,
                row_count=len(rows), duration_ms=elapsed,
                service_name=service_name,
            )
        return rows
    except Exception as exc:
        classified = classify_exception(exc)
        logger.exception(
            "Failed to call tool %s [%s]", tool_name, type(classified).__name__,
        )
        elapsed = round((_time.monotonic() - t0) * 1000, 1)
        xcv = get_current_xcv()
        if xcv:
            AgentLogger.get_instance().log_mcp_collection_call(
                xcv=xcv, tool_name=tool_name, parameters=params,
                row_count=0, duration_ms=elapsed,
                error=type(classified).__name__,
                service_name=service_name,
            )
        return []


# ── Data-field normaliser ─────────────────────────────────────────

@lru_cache(maxsize=2048)
def _snake_case(name: str) -> str:
    """Convert PascalCase / camelCase to snake_case.  Results are cached."""
    s1 = _re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", name)
    return _re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()


def _normalise_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with snake_case keys + original keys."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        out[k] = v
        sk = _snake_case(k)
        if sk != k:
            out[sk] = v
    return out


# ── Activation-rule evaluator registry ─────────────────────────────
# Each rule evaluator is a callable:
#   (field_name: str, value: Any, threshold: Any, row: dict) -> bool
# where field_name is the base field extracted from the rule key,
# value is row[field_name], threshold is the config value, and row
# is the full row/group dict (for rules that need multiple fields).

RuleEvaluatorFn = Callable[[str, Any, Any, dict[str, Any]], bool]

_RULE_EVALUATORS: dict[str, RuleEvaluatorFn] = {}


def register_rule_evaluator(suffix: str, fn: RuleEvaluatorFn) -> None:
    """Register a named rule evaluator for use in activation_rules.

    Rule keys in config are ``{field}_{suffix}`` (e.g. ``count_min``).
    The evaluator receives ``(field_name, row_value, threshold, full_row)``.
    """
    _RULE_EVALUATORS[suffix] = fn
    logger.info("Registered rule evaluator: %s", suffix)


# ── Built-in rule evaluators ──────────────────────────────────────

def _rule_min(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``{field}_min: N`` → value >= N"""
    try:
        return float(value or 0) >= float(threshold)
    except (TypeError, ValueError):
        return False


def _rule_max(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``{field}_max: N`` → value <= N"""
    try:
        return float(value or 0) <= float(threshold)
    except (TypeError, ValueError):
        return False


def _rule_present(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``{field}_present: true`` → value is truthy and non-empty"""
    if not value or (isinstance(value, str) and not value.strip()):
        return False
    return True


def _rule_regex(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``{field}_regex: "pattern"`` → re.search(pattern, str(value))"""
    if value is None:
        return False
    return bool(_re.search(str(threshold), str(value)))


def _rule_in_range(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``{field}_in_range: [lo, hi]`` → lo <= value <= hi"""
    try:
        lo, hi = threshold
        return float(lo) <= float(value or 0) <= float(hi)
    except (TypeError, ValueError):
        return False


def _rule_or_severity_increased(
    field: str, value: Any, threshold: Any, row: dict[str, Any],
) -> bool:
    """``{field}_or_severity_increased: true``

    Special composite: is_escalated == True OR severity > initial_severity.
    The field prefix names the escalation boolean; severity fields are read
    from ``severity`` and ``initial_severity`` in the row.
    """
    is_esc = row.get("is_escalated", False)
    if is_esc is True or str(is_esc).lower() == "true":
        return True
    sev = row.get("severity", "")
    init_sev = row.get("initial_severity", "")
    # Severity order: A > B > C (string compare is reversed)
    return bool(sev and init_sev and sev < init_sev)


def _rule_any(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``any: [{rule}, {rule}, ...]`` → OR composite — at least one sub-rule passes."""
    if not isinstance(threshold, list):
        return False
    return any(_evaluate_single_rule(sub_rule, row) for sub_rule in threshold)


def _rule_all(field: str, value: Any, threshold: Any, row: dict[str, Any]) -> bool:
    """``all: [{rule}, {rule}, ...]`` → AND composite — every sub-rule passes."""
    if not isinstance(threshold, list):
        return False
    return all(_evaluate_single_rule(sub_rule, row) for sub_rule in threshold)


# Register built-in evaluators
register_rule_evaluator("min", _rule_min)
register_rule_evaluator("max", _rule_max)
register_rule_evaluator("present", _rule_present)
register_rule_evaluator("regex", _rule_regex)
register_rule_evaluator("in_range", _rule_in_range)
register_rule_evaluator("or_severity_increased", _rule_or_severity_increased)
register_rule_evaluator("any", _rule_any)
register_rule_evaluator("all", _rule_all)


def _evaluate_single_rule(rule: dict[str, Any], row: dict[str, Any]) -> bool:
    """Evaluate one rule dict (single key→value pair) against a row."""
    for key, threshold in rule.items():
        if key == "min_types_with_data":
            continue  # compound-level, not row-level
        if key == "min_types_activated":
            continue  # compound-level, not row-level

        # Composite operators (no field prefix)
        if key in ("any", "all"):
            evaluator = _RULE_EVALUATORS[key]
            if not evaluator("", None, threshold, row):
                return False
            continue

        # Try each registered suffix (longest first to avoid partial matches)
        matched = False
        for suffix in sorted(_RULE_EVALUATORS, key=len, reverse=True):
            tag = f"_{suffix}"
            if key.endswith(tag):
                field_name = key[: -len(tag)]
                val = row.get(field_name, row.get(_snake_case(field_name)))
                evaluator = _RULE_EVALUATORS[suffix]
                if not evaluator(field_name, val, threshold, row):
                    return False
                matched = True
                break

        if not matched:
            # Fallback: boolean equality or direct equality (no suffix)
            val = row.get(key, False)
            if isinstance(threshold, bool):
                bool_val = val is True or str(val).lower() == "true"
                if bool_val != threshold:
                    return False
            else:
                if val != threshold:
                    return False

    return True


def _check_activation(
    rules: list[dict[str, Any]],
    row_or_group: dict[str, Any],
) -> bool:
    """Evaluate activation_rules against a row or group aggregate.

    Each rule dict in the list is AND-ed: all must pass for activation.
    Within a rule dict, each key→value pair is also AND-ed.

    Rule key patterns are dispatched through the rule evaluator registry.
    Built-in types: _min, _max, _present, _regex, _in_range,
    _or_severity_increased.  Composite: ``any`` / ``all`` (OR/AND arrays).
    Boolean and exact-equality fallback for keys without a registered suffix.
    """
    return all(_evaluate_single_rule(rule, row_or_group) for rule in rules)


# ── Grouping + aggregation ────────────────────────────────────────

def _compute_groups(
    rows: list[dict[str, Any]],
    granularity_cfg: dict[str, Any],
) -> list[dict[str, Any]]:
    """Group rows by group_by keys and compute aggregates.

    If no explicit aggregates are defined, each row is its own group.
    """
    group_by = granularity_cfg.get("group_by", [])
    aggregates_cfg = granularity_cfg.get("aggregates")

    if not aggregates_cfg:
        # Per-row evaluation (e.g. subscription_region, single_case)
        return rows

    # Group rows
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = tuple(row.get(k, row.get(_snake_case(k))) for k in group_by)
        groups[key].append(row)

    results = []
    for _key, group_rows in groups.items():
        group_record: dict[str, Any] = {}
        # Carry forward group_by values from first row
        for k in group_by:
            group_record[k] = group_rows[0].get(k, group_rows[0].get(_snake_case(k)))
            group_record[_snake_case(k)] = group_record[k]

        # Compute aggregates
        for agg_name, agg_expr in aggregates_cfg.items():
            group_record[agg_name] = _compute_aggregate(agg_expr, group_rows)

        # Also store raw row count
        group_record["_row_count"] = len(group_rows)
        group_record["_rows"] = group_rows
        results.append(group_record)

    return results


def _compute_aggregate(expr: str, rows: list[dict[str, Any]]) -> Any:
    """Compute aggregate value from expression like count_distinct(Region).

    Supports ``pre_aggregated:<FieldName>`` to read a value directly from the
    first row (used when the Kusto query already returns aggregated data).

    Aggregate functions are looked up from the ``_AGGREGATE_FUNCTIONS``
    registry.  Use :func:`register_aggregate_function` to add custom ones.
    """
    if expr.startswith("pre_aggregated:"):
        field = expr[len("pre_aggregated:"):]
        val = rows[0].get(field, rows[0].get(_snake_case(field))) if rows else None
        if isinstance(val, str) and val.isdigit():
            return int(val)
        return val

    func_name, field = _parse_aggregate_expr(expr)
    agg_fn = _AGGREGATE_FUNCTIONS.get(func_name)
    if agg_fn is None:
        raise ValueError(
            f"Unknown aggregate function '{func_name}'. "
            f"Registered: {sorted(_AGGREGATE_FUNCTIONS)}"
        )
    return agg_fn(field, rows)


# ── Aggregate expression parser ──────────────────────────────────

_AGG_EXPR_RE = _re.compile(r"^(\w+)\((.+)\)$")


def _parse_aggregate_expr(expr: str) -> tuple[str, str]:
    """Parse ``func(field)`` → ``(func, field)``.  Raises on malformed input."""
    m = _AGG_EXPR_RE.match(expr.strip())
    if not m:
        raise ValueError(f"Malformed aggregate expression: {expr!r}")
    return m.group(1), m.group(2)


# ── Aggregate function helpers ───────────────────────────────────

def _agg_values(field: str, rows: list[dict[str, Any]], *, numeric: bool = False) -> list[Any]:
    """Extract non-None values for *field* from rows (snake_case fallback)."""
    vals = [
        r.get(field, r.get(_snake_case(field)))
        for r in rows
    ]
    vals = [v for v in vals if v is not None]
    if numeric:
        vals = [float(v) for v in vals]
    return vals


def _agg_count_distinct(field: str, rows: list[dict[str, Any]]) -> int:
    return len({r.get(field, r.get(_snake_case(field))) for r in rows})

def _agg_count(_field: str, rows: list[dict[str, Any]]) -> int:
    return len(rows)

def _agg_sum(field: str, rows: list[dict[str, Any]]) -> float:
    return sum(float(r.get(field, r.get(_snake_case(field), 0)) or 0) for r in rows)

def _agg_avg(field: str, rows: list[dict[str, Any]]) -> float:
    vals = _agg_values(field, rows, numeric=True)
    return sum(vals) / len(vals) if vals else 0.0

def _agg_min(field: str, rows: list[dict[str, Any]]) -> Any:
    vals = _agg_values(field, rows)
    return min(vals) if vals else None

def _agg_max(field: str, rows: list[dict[str, Any]]) -> Any:
    vals = _agg_values(field, rows)
    return max(vals) if vals else None

def _agg_median(field: str, rows: list[dict[str, Any]]) -> float:
    vals = sorted(_agg_values(field, rows, numeric=True))
    if not vals:
        return 0.0
    mid = len(vals) // 2
    if len(vals) % 2 == 0:
        return (vals[mid - 1] + vals[mid]) / 2.0
    return vals[mid]

def _agg_stddev(field: str, rows: list[dict[str, Any]]) -> float:
    vals = _agg_values(field, rows, numeric=True)
    if len(vals) < 2:
        return 0.0
    mean = sum(vals) / len(vals)
    return (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5

def _agg_percentile(field: str, rows: list[dict[str, Any]]) -> float:
    """percentile(Field, 95) → 95th percentile of Field values."""
    # field is e.g. "ImpactedResources, 95"
    parts = [p.strip() for p in field.split(",")]
    if len(parts) != 2:
        raise ValueError(f"percentile requires (field, pct): got {field!r}")
    actual_field, pct_str = parts
    pct = float(pct_str)
    vals = sorted(_agg_values(actual_field, rows, numeric=True))
    if not vals:
        return 0.0
    k = (pct / 100.0) * (len(vals) - 1)
    lo = int(k)
    hi = min(lo + 1, len(vals) - 1)
    frac = k - lo
    return vals[lo] + frac * (vals[hi] - vals[lo])


def _agg_count_where(field: str, rows: list[dict[str, Any]]) -> int:
    """count_where(Field == value) → count rows where condition is true.

    Supports ``Field == value`` conditions.  Boolean ``true``/``false``
    are compared case-insensitively.
    """
    # Parse "Field == value"
    m = _re.match(r"^(\S+)\s*==\s*(.+)$", field.strip())
    if not m:
        raise ValueError(f"count_where requires 'Field == value': got {field!r}")
    col, expected = m.group(1).strip(), m.group(2).strip()
    count = 0
    for row in rows:
        val = row.get(col, row.get(_snake_case(col)))
        if expected.lower() in ("true", "false"):
            row_bool = val is True or str(val).lower() == "true"
            expected_bool = expected.lower() == "true"
            if row_bool == expected_bool:
                count += 1
        else:
            if str(val) == expected:
                count += 1
    return count


def _agg_collect(field: str, rows: list[dict[str, Any]]) -> list[Any]:
    """collect(Field) → list of all non-None values for Field."""
    return [
        v for r in rows
        for v in [r.get(field, r.get(_snake_case(field)))]
        if v is not None
    ]


# ── Aggregate function registry ──────────────────────────────────

AggregateFn = Callable[[str, list[dict[str, Any]]], Any]

_AGGREGATE_FUNCTIONS: dict[str, AggregateFn] = {
    "count_distinct": _agg_count_distinct,
    "count":          _agg_count,
    "count_where":    _agg_count_where,
    "sum":            _agg_sum,
    "avg":            _agg_avg,
    "mean":           _agg_avg,  # alias for avg
    "min":            _agg_min,
    "max":            _agg_max,
    "median":         _agg_median,
    "stddev":         _agg_stddev,
    "percentile":     _agg_percentile,
    "collect":        _agg_collect,
}


def register_aggregate_function(name: str, fn: AggregateFn) -> None:
    """Register a custom aggregate function.

    The function signature is ``(field: str, rows: list[dict]) -> Any``.
    """
    _AGGREGATE_FUNCTIONS[name] = fn
    logger.info("Registered aggregate function: %s", name)


# ── Per-type evaluation ──────────────────────────────────────────

def _build_activation_summary(
    granularity: str,
    group: dict[str, Any],
) -> str:
    """Build a human-readable summary of what activated."""
    parts = [f"granularity={granularity}"]
    # Include relevant aggregate fields
    for k, v in group.items():
        if k.startswith("_") or k in ("_rows", "_row_count"):
            continue
        if isinstance(v, (int, float)) and k.startswith("distinct"):
            parts.append(f"{k}={v}")
        elif isinstance(v, (int, float)) and "impacted" in k.lower():
            parts.append(f"{k}={v}")
        elif isinstance(v, (int, float)) and "count" in k.lower():
            parts.append(f"{k}={v}")
    return "; ".join(parts)


def _evaluate_granularities(
    sig_type: dict[str, Any],
    all_rows: list[dict[str, Any]],
    granularity_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> TypeSignalResult:
    """Shared granularity evaluation logic used by all collection strategies.

    Iterates over the signal type's granularities, groups rows, checks
    activation rules, computes strengths, and returns a TypeSignalResult.

    Args:
        sig_type: Signal type configuration dict from signal_template.json.
        all_rows: All collected & enriched rows for this signal type.
        granularity_rows: Optional per-granularity row mapping.  When provided,
            each granularity uses its own subset; otherwise ``all_rows`` is used
            for every granularity.
    """
    type_id = sig_type["id"]
    type_name = sig_type["name"]
    has_data = len(all_rows) > 0
    activated: list[ActivatedSignal] = []
    logger.debug("Evaluating %d granularities for %s (%d rows)", len(sig_type.get("granularities", [])), type_id, len(all_rows))

    for gran_cfg in sig_type.get("granularities", []):
        gran_name = gran_cfg["granularity"]
        rows_for_gran = (
            granularity_rows.get(gran_name, all_rows)
            if granularity_rows
            else all_rows
        )

        if not rows_for_gran:
            logger.debug("Skipping granularity %s/%s — no rows", type_id, gran_name)
            continue

        try:
            groups = _compute_groups(rows_for_gran, gran_cfg)
        except Exception:
            logger.exception("Failed to compute groups for %s/%s — skipping granularity", type_id, gran_name)
            continue

        for group in groups:
            if not _check_activation(gran_cfg.get("activation_rules", []), group):
                logger.debug("Activation rules not met for %s/%s", type_id, gran_name)
                continue

            try:
                raw_strength = evaluate_strength(gran_cfg["strength_formula"], group)
            except ValueError:
                logger.warning(
                    "Strength formula failed for %s/%s, defaulting to 1.0",
                    type_id, gran_name, exc_info=True,
                )
                raw_strength = 1.0

            max_raw = gran_cfg.get("max_raw_strength", raw_strength)
            strength = normalize_strength(raw_strength, max_raw)

            summary = _build_activation_summary(gran_name, group)
            matched = group.get("_rows", [group])

            logger.debug("Signal activated: %s/%s strength=%.2f (raw=%.4f)", type_id, gran_name, strength, raw_strength)
            activated.append(ActivatedSignal(
                signal_type_id=type_id,
                signal_name=type_name,
                granularity=gran_name,
                confidence=gran_cfg.get("confidence", "Medium"),
                strength=strength,
                raw_strength=raw_strength,
                activation_summary=summary,
                matched_rows=matched,
            ))

    max_strength = max((s.strength for s in activated), default=0.0)
    raw_max_strength = max((s.raw_strength for s in activated), default=0.0)
    best_confidence = "Low"
    if activated:
        confidence_order = ["Low", "Medium", "Medium-High", "High", "Highest"]
        best_confidence = max(
            (s.confidence for s in activated),
            key=lambda c: confidence_order.index(c) if c in confidence_order else 0,
        )

    return TypeSignalResult(
        signal_type_id=type_id,
        signal_name=type_name,
        has_data=has_data,
        row_count=len(all_rows),
        activated_signals=activated,
        max_strength=max_strength,
        raw_max_strength=raw_max_strength,
        best_confidence=best_confidence,
    )


async def _evaluate_dependency_signal_type(
    sig_type: dict[str, Any],
    context: dict[str, Any],
) -> TypeSignalResult:
    """Evaluate SIG-TYPE-4: dependency service degradation via dependency_scan strategy.

    Flow:
    1. Call region tool to discover customer regions
    2. Load dependency_services.json for dependency service_tree_ids
    3. For each dependency, call multicustomer tool
    4. Filter results to customer regions only
    5. Enrich rows with DependencyServiceName
    6. Evaluate granularities as usual
    """
    type_id = sig_type["id"]
    type_name = sig_type["name"]

    # Step 1: Get customer regions
    region_cfg = sig_type["region_tool"]
    region_params = {}
    for param_name, context_key in region_cfg.get("parameters_from_context", {}).items():
        val = context.get(context_key, "")
        if val:
            region_params[param_name] = val

    region_rows = await _call_collection_tool(region_cfg["tool_name"], region_params, service_name=context.get("service_name", ""))
    customer_regions: set[str] = set()
    for row in region_rows:
        norm = _normalise_row(row)
        region = norm.get("region", norm.get("Region", ""))
        if region:
            customer_regions.add(region.lower())

    if not customer_regions:
        logger.info("SIG-TYPE-4: No customer regions found — skipping dependency scan")
        return TypeSignalResult(
            signal_type_id=type_id,
            signal_name=type_name,
            has_data=False,
            row_count=0,
            activated_signals=[],
            max_strength=0.0,
            best_confidence="Low",
        )

    logger.info("SIG-TYPE-4: Customer regions discovered: %s", customer_regions)

    # Persist customer regions to ADLS for traceability/debugging.
    persist_client = _get_persist_client()
    xcv_for_persist = get_current_xcv() or ""
    adls_base_for_persist = os.getenv("ADLS_BASE_PATH", "runs").strip("/")
    dep_raw_prefix = (
        f"{adls_base_for_persist}/{xcv_for_persist}/signals/raw/{type_id}"
        if persist_client and xcv_for_persist
        else None
    )

    if dep_raw_prefix:
        regions_path = f"{dep_raw_prefix}/customer_regions.json"
        regions_payload = {
            "service_tree_id": context.get("service_tree_id", ""),
            "customer_name": context.get("customer_name", ""),
            "tool": region_cfg["tool_name"],
            "row_count": len(region_rows),
            "customer_regions": sorted(customer_regions),
            "raw_rows": region_rows,
        }
        try:
            await persist_client.upload_file(
                regions_path, json.dumps(regions_payload, default=str, indent=2)
            )
            logger.info(
                "%s: wrote customer regions file (%d regions, %d raw rows) → adls:%s",
                type_id, len(customer_regions), len(region_rows), regions_path,
            )
        except Exception:
            logger.exception("Failed to write customer regions file %s", regions_path)

    # Step 2: Load dependency mappings → resolve dep files for this primary service
    dep_mappings = _load_json("dependency_services/dependency_mappings.json")
    primary_stid = context.get("service_tree_id", "")
    mappings = dep_mappings.get("mappings", {})

    if primary_stid not in mappings:
        logger.info(
            "SIG-TYPE-4: No dependency mapping for primary service_tree_id=%s — skipping",
            primary_stid,
        )
        return TypeSignalResult(
            signal_type_id=type_id,
            signal_name=type_name,
            has_data=False,
            row_count=0,
            activated_signals=[],
            max_strength=0.0,
            best_confidence="Low",
        )

    dep_keys = mappings[primary_stid].get("dependencies", [])
    dep_services: list[dict[str, Any]] = []
    dep_services_dir = os.path.join(_CONFIG_DIR, "dependency_services")
    for dep_key in dep_keys:
        dep_file = os.path.join(dep_services_dir, f"{dep_key}.json")
        if not os.path.isfile(dep_file):
            logger.warning("Dependency file not found: %s — skipping", dep_file)
            continue
        with open(dep_file, "r", encoding="utf-8") as f:
            dep_svc_data = json.load(f)
        from core.models.config.dependency_service import DependencyServiceFileConfig
        try:
            DependencyServiceFileConfig.model_validate(dep_svc_data)
        except Exception as ve:
            logger.warning("Dependency service file %s validation failed: %s", dep_file, ve)
        dep_services.append(dep_svc_data)

    # Step 3: Call multicustomer tool for each dependency
    dep_tool_cfg = sig_type["dependency_tool"]
    dep_tool_name = dep_tool_cfg["tool_name"]
    dep_param_field = dep_tool_cfg["parameter_field"]

    # Build extra params (start_time/end_time) from context for dependency tool
    dep_extra = {}
    for param_name, ctx_key in dep_tool_cfg.get("extra_params_from_context", {}).items():
        val = context.get(ctx_key, "")
        if val:
            dep_extra[param_name] = val

    all_rows: list[dict[str, Any]] = []

    # P0-B: Parallel MCP tool calls — all dependency service calls are independent
    async def _fetch_dep(dep_svc: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
        dep_name = dep_svc["name"]
        dep_stid = dep_svc.get("service_tree_id", "")
        dep_category = dep_svc.get("category", "unknown")
        if not dep_stid or dep_stid.startswith("<TBD"):
            return dep_name, dep_category, []
        dep_params = {dep_param_field: dep_stid, **dep_extra}
        rows = await _call_collection_tool(dep_tool_name, dep_params, service_name=dep_name)
        return dep_name, dep_category, rows

    logger.debug("Launching %d parallel dependency tool calls for %s", len(dep_services), type_id)
    dep_results = await asyncio.gather(
        *(_fetch_dep(ds) for ds in dep_services),
        return_exceptions=True,
    )

    # Per-dependency raw responses are written to the same prefix used for
    # customer_regions.json above (dep_raw_prefix).

    for result in dep_results:
        if isinstance(result, BaseException):
            logger.error("Parallel dependency tool call failed for %s: %s", type_id, result, exc_info=result)
            continue
        dep_name, dep_category, rows = result

        # Step 4: Filter to customer regions and enrich with dependency name + category
        dep_filtered: list[dict[str, Any]] = []
        regions_seen: set[str] = set()
        for row in rows:
            norm = _normalise_row(row)
            row_region = (norm.get("region", norm.get("Region", "")) or "").lower()
            if row_region:
                regions_seen.add(row_region)
            if row_region in customer_regions:
                norm["DependencyServiceName"] = dep_name
                norm["dependency_service_name"] = dep_name
                norm["DependencyCategory"] = dep_category
                norm["dependency_category"] = dep_category
                dep_filtered.append(norm)

        if rows and not dep_filtered:
            logger.warning(
                "%s [%s]: %d raw rows returned but 0 matched customer regions "
                "(regions_seen=%s, customer_regions=%s)",
                type_id, dep_name, len(rows), sorted(regions_seen), sorted(customer_regions),
            )

        all_rows.extend(dep_filtered)

        # Write per-dependency raw file to ADLS for evidence/debugging.
        if dep_raw_prefix and rows:
            safe_name = (
                dep_name.replace(" ", "_").replace("/", "_").lower() or "unknown"
            )
            dep_path = f"{dep_raw_prefix}/dep_{safe_name}.json"
            payload = {
                "dependency_service_name": dep_name,
                "dependency_category": dep_category,
                "customer_regions": sorted(customer_regions),
                "regions_seen": sorted(regions_seen),
                "raw_row_count": len(rows),
                "matched_row_count": len(dep_filtered),
                "raw_rows": rows,
                "matched_rows": dep_filtered,
            }
            try:
                await persist_client.upload_file(
                    dep_path, json.dumps(payload, default=str, indent=2)
                )
                logger.info(
                    "%s [%s]: wrote per-dep raw file (%d raw, %d matched) → adls:%s",
                    type_id, dep_name, len(rows), len(dep_filtered), dep_path,
                )
            except Exception:
                logger.exception("Failed to write per-dep raw file %s", dep_path)

    # Step 5: Evaluate granularities via shared helper
    return _evaluate_granularities(sig_type, all_rows)


async def _evaluate_signal_type(
    sig_type: dict[str, Any],
    context: dict[str, Any],
) -> TypeSignalResult:
    """Evaluate all granularities for one signal type."""
    type_id = sig_type["id"]
    type_name = sig_type["name"]
    collection_tools = sig_type.get("collection_tools", [])

    # Collect data from all collection tools for this type
    all_rows: list[dict[str, Any]] = []
    # Track which granularities are fed by which tool call
    granularity_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)

    # P0-B: Parallel MCP tool calls — all collection tools are independent
    async def _fetch_tool(tool_cfg: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tool_name = tool_cfg["tool_name"]
        params = {}
        for param_name, context_key in tool_cfg.get("parameters_from_context", {}).items():
            val = context.get(context_key, "")
            if val:
                params[param_name] = val
        rows = await _call_collection_tool(tool_name, params, service_name=context.get("service_name", ""))
        return tool_cfg, [_normalise_row(r) for r in rows]

    logger.debug("Launching %d parallel collection tool calls for %s", len(collection_tools), type_id)
    tool_results = await asyncio.gather(
        *(_fetch_tool(tc) for tc in collection_tools),
        return_exceptions=True,
    )

    for result in tool_results:
        if isinstance(result, BaseException):
            logger.error("Parallel tool call failed for %s: %s", type_id, result, exc_info=result)
            continue
        tool_cfg, normalised = result

        for gran_name in tool_cfg.get("feeds_granularities", []):
            granularity_rows[gran_name].extend(normalised)

        all_rows.extend(normalised)

    # Evaluate granularities via shared helper (with per-granularity row mapping)
    return _evaluate_granularities(sig_type, all_rows, granularity_rows or None)


# ── Compound evaluation ──────────────────────────────────────────

def _evaluate_compounds(
    compound_cfgs: list[dict[str, Any]],
    type_results: dict[str, TypeSignalResult],
) -> list[CompoundSignalResult]:
    """Evaluate compound signal rules against type-level results.

    Supports optional config fields for richer expressiveness:
        weights_by_type:  dict mapping type_id → weight (default 1.0 each).
                          Used for weighted-average strength instead of uniform avg.
        exclude_if_below: float — exclude contributing types whose max_strength
                          is below this threshold (filters noise).
        strength_formula: str — AST formula evaluated with variables:
                          ``sum_strengths``, ``avg_strength``, ``weighted_avg``,
                          ``max_strength``, ``min_strength``, ``type_count``.
                          Falls back to ``avg_strength * correlation_multiplier``.
    """
    results = []
    logger.debug("Evaluating %d compound signal configs", len(compound_cfgs))

    for cfg in compound_cfgs:
        required_types = cfg["required_signal_types"]
        rules = cfg.get("activation_rules", [])
        multiplier = cfg.get("correlation_multiplier", 1.5)
        weights = cfg.get("weights_by_type", {})
        exclude_below = cfg.get("exclude_if_below", 0.0)

        # Which required types have activated signals?
        # A type must have at least one activated granularity signal to
        # meaningfully contribute to a compound — data presence alone
        # (has_data) is insufficient because the rows may not meet any
        # activation thresholds.
        types_activated = [
            tid for tid in required_types
            if tid in type_results and type_results[tid].activated_signals
        ]

        # Apply exclude_if_below filter
        if exclude_below > 0:
            types_activated = [
                tid for tid in types_activated
                if type_results[tid].max_strength >= exclude_below
            ]

        # Check compound activation — support both legacy
        # ``min_types_with_data`` key and new ``min_types_activated``.
        min_needed = 2  # default
        for rule in rules:
            if "min_types_activated" in rule:
                min_needed = rule["min_types_activated"]
            elif "min_types_with_data" in rule:
                min_needed = rule["min_types_with_data"]

        activated = len(types_activated) >= min_needed

        # Compute compound strength
        raw_strength = 0.0
        strength = 0.0
        if activated and types_activated:
            strengths = [type_results[tid].max_strength for tid in types_activated]
            sum_strengths = sum(strengths)
            avg_strength = sum_strengths / len(strengths)

            # Weighted average (falls back to uniform if no weights specified)
            w_vals = [weights.get(tid, 1.0) for tid in types_activated]
            w_total = sum(w_vals)
            weighted_avg = (
                sum(type_results[tid].max_strength * weights.get(tid, 1.0) for tid in types_activated)
                / w_total
            ) if w_total > 0 else avg_strength

            formula = cfg.get("strength_formula")
            if formula:
                try:
                    raw_strength = evaluate_strength(formula, {
                        "sum_strengths": sum_strengths,
                        "avg_strength": avg_strength,
                        "weighted_avg": weighted_avg,
                        "max_strength": max(strengths),
                        "min_strength": min(strengths),
                        "type_count": len(types_activated),
                        "type_max_strengths": strengths,
                        "multiplier": multiplier,
                    })
                except ValueError:
                    logger.warning(
                        "Compound %s strength_formula failed, falling back to avg*multiplier",
                        cfg["id"], exc_info=True,
                    )
                    raw_strength = avg_strength * multiplier
            else:
                raw_strength = avg_strength * multiplier

            strength = min(raw_strength, 5.0)

        if not activated:
            logger.debug("Compound %s not activated: %d/%d required types activated", cfg["id"], len(types_activated), min_needed)

        results.append(CompoundSignalResult(
            compound_id=cfg["id"],
            compound_name=cfg["name"],
            activated=activated,
            confidence=cfg.get("confidence", "Medium-High"),
            strength=strength,
            raw_strength=raw_strength,
            contributing_types=types_activated,
            rationale=cfg.get("rationale", ""),
        ))

    return results


# ── Register built-in collection strategies ──────────────────────
register_collection_strategy("standard", _evaluate_signal_type)
register_collection_strategy("dependency_scan", _evaluate_dependency_signal_type)


# ── Config-driven decision gate ───────────────────────────────────

def _evaluate_decision_rules(
    template: dict[str, Any],
    type_results: list[TypeSignalResult],
    compound_results: list[CompoundSignalResult],
) -> str:
    """Evaluate decision rules from config and return the action string.

    Reads ``decision_rules`` from the template — an ordered list of
    ``{"condition": {...}, "action": "..."}`` pairs evaluated top-down.
    The first matching condition wins.  Falls back to legacy
    ``decision_thresholds`` logic, then to ``"quiet"``.

    Supported condition keys:
        any_signal_strength_gte: N
            True if any signal type's max_strength >= N.
        any_compound_activated: true
            True if any compound signal activated.
        any_activated_signals: true
            True if any signal type has at least one activated signal.
        signal_type_strength_gte: {"type_id": "SIG-TYPE-1", "strength": N}
            True if the named signal type's max_strength >= N.
        min_activated_types: N
            True if N or more signal types have activated signals.
        all: [...conditions...]
            AND composite — all sub-conditions must be true.
        any: [...conditions...]
            OR composite — at least one sub-condition must be true.
    """
    decision_rules = template.get("decision_rules", [])
    logger.debug("Evaluating %d decision rules", len(decision_rules))
    type_map = {tr.signal_type_id: tr for tr in type_results}
    for rule in decision_rules:
        condition = rule.get("condition", {})
        if _match_decision_condition(condition, type_results, compound_results, type_map):
            action = rule["action"]
            logger.debug("Decision rule matched: %s → %s", condition, action)
            return action
    logger.debug("No decision rule matched — defaulting to 'quiet'")
    return "quiet"


def _match_decision_condition(
    condition: dict[str, Any],
    type_results: list[TypeSignalResult],
    compound_results: list[CompoundSignalResult],
    type_map: dict[str, TypeSignalResult],
) -> bool:
    """Check whether a single decision condition dict is satisfied."""
    for key, value in condition.items():
        if key == "any_signal_strength_gte":
            if not any(tr.max_strength >= float(value) for tr in type_results):
                return False

        elif key == "any_compound_activated":
            if not any(cr.activated for cr in compound_results):
                return False

        elif key == "any_activated_signals":
            if not any(tr.activated_signals for tr in type_results):
                return False

        elif key == "signal_type_strength_gte":
            tid = value["type_id"]
            min_str = float(value["strength"])
            tr = type_map.get(tid)
            if not tr or tr.max_strength < min_str:
                return False

        elif key == "min_activated_types":
            count = sum(1 for tr in type_results if tr.activated_signals)
            if count < int(value):
                return False

        elif key == "all":
            if not all(
                _match_decision_condition(sub, type_results, compound_results, type_map)
                for sub in value
            ):
                return False

        elif key == "any":
            if not any(
                _match_decision_condition(sub, type_results, compound_results, type_map)
                for sub in value
            ):
                return False

        else:
            logger.warning("Unknown decision condition key: %s", key)
            return False

    return True


# ── Main evaluation entry point ──────────────────────────────────

async def _evaluate_for_context(
    template: dict[str, Any],
    context: dict[str, Any],
) -> SignalBuilderResult:
    """Run one evaluation cycle for a single customer + service_tree_id pair."""

    # P0-B: Parallel signal type evaluations — all types are independent
    async def _eval_one_type(sig_type: dict[str, Any]) -> TypeSignalResult:
        strategy_name = sig_type.get("collection_strategy", "standard")
        strategy_fn = _COLLECTION_STRATEGIES.get(strategy_name)
        if strategy_fn is None:
            raise ValueError(
                f"Unknown collection_strategy '{strategy_name}' "
                f"for signal type {sig_type.get('id')}. "
                f"Registered strategies: {sorted(_COLLECTION_STRATEGIES)}"
            )
        return await strategy_fn(sig_type, context)

    signal_types = template.get("signal_types", [])
    logger.debug("Launching parallel evaluation of %d signal types", len(signal_types))
    gathered = await asyncio.gather(
        *(_eval_one_type(st) for st in signal_types),
        return_exceptions=True,
    )

    failures = sum(1 for r in gathered if isinstance(r, BaseException))
    if failures:
        logger.warning("Signal type gather: %d succeeded, %d failed", len(gathered) - failures, failures)

    type_results_list: list[TypeSignalResult] = []
    for i, result in enumerate(gathered):
        if isinstance(result, BaseException):
            st_id = signal_types[i].get("id", "?")
            logger.error("Parallel signal type evaluation failed for %s: %s", st_id, result, exc_info=result)
            # Create a no-data placeholder so downstream logic isn't broken
            type_results_list.append(TypeSignalResult(
                signal_type_id=st_id,
                signal_name=signal_types[i].get("name", ""),
                has_data=False,
                row_count=0,
                activated_signals=[],
                max_strength=0.0,
                best_confidence="Low",
            ))
            continue
        type_results_list.append(result)
        logger.info(
            "Signal type %s [%s/%s]: has_data=%s, activated=%d, max_strength=%.2f",
            result.signal_type_id,
            context.get("customer_name", "?"),
            context.get("service_tree_id", "?"),
            result.has_data,
            len(result.activated_signals), result.max_strength,
        )
        xcv = get_current_xcv()
        if xcv:
            # Extract distinct SLI names from activated signals' matched rows
            sli_names: set[str] = set()
            for sig in result.activated_signals:
                for row in sig.matched_rows:
                    sli = row.get("slo_sli_id") or row.get("SLO_SliId") or ""
                    if sli:
                        sli_names.add(sli)

            AgentLogger.get_instance().log_signal_type_evaluated(
                xcv=xcv,
                signal_type_id=result.signal_type_id,
                signal_name=result.signal_name,
                has_data=result.has_data,
                row_count=result.row_count,
                activated_count=len(result.activated_signals),
                max_strength=result.max_strength,
                best_confidence=result.best_confidence,
                activated_slis=sorted(sli_names),
            )

    type_results_map = {tr.signal_type_id: tr for tr in type_results_list}

    # Evaluate compound signals
    compound_cfgs = template.get("compound_signals", [])
    compound_results = _evaluate_compounds(compound_cfgs, type_results_map)

    for cr in compound_results:
        xcv = get_current_xcv()
        if xcv:
            AgentLogger.get_instance().log_compound_evaluated(
                xcv=xcv,
                compound_id=cr.compound_id,
                compound_name=cr.compound_name,
                activated=cr.activated,
                strength=cr.strength,
                contributing_types=cr.contributing_types,
                confidence=cr.confidence,
                rationale=cr.rationale,
            )
        if cr.activated:
            logger.info(
                "Compound %s activated: strength=%.2f, types=%s",
                cr.compound_id, cr.strength, cr.contributing_types,
            )

    # Decide action via config-driven decision gate
    action = _evaluate_decision_rules(template, type_results_list, compound_results)

    logger.info(
        "SignalBuilder decision for %s/%s: %s",
        context.get("customer_name", "?"),
        context.get("service_tree_id", "?"),
        action,
    )
    xcv = get_current_xcv()
    if xcv:
        all_activated = [s for tr in type_results_list for s in tr.activated_signals]
        activated_compounds = [c for c in compound_results if c.activated]
        AgentLogger.get_instance().log_signal_decision(
            xcv=xcv,
            customer_name=context.get("customer_name", ""),
            service_tree_id=context.get("service_tree_id", ""),
            action=action,
            signal_count=len(all_activated),
            compound_count=len(activated_compounds),
        )

    result = SignalBuilderResult(
        type_results=type_results_list,
        compound_results=compound_results,
        action=action,
        customer_name=context.get("customer_name", ""),
        service_tree_id=context.get("service_tree_id", ""),
        service_name=context.get("service_name", ""),
        xcv=get_current_xcv() or "",
        start_time=context.get("start_time", ""),
        end_time=context.get("end_time", ""),
        owning_tenant_names=json.loads(context.get("owning_tenant_names", "[]")),
        support_product_names=json.loads(context.get("support_product_names", "[]")),
    )

    # Best-effort persist to ADLS (raw matched rows + per-type aggregate + result).
    # Failures are swallowed inside _persist_result_to_adls so the pipeline is
    # never blocked by storage hiccups or missing config.
    await _persist_result_to_adls(context, type_results_list, result)

    return result


async def evaluate_signals(
    template: dict[str, Any] | None = None,
    monitoring_context: dict[str, Any] | None = None,
) -> list[SignalBuilderResult]:
    """Run one poll cycle across all monitoring targets.

    Args:
        template: Parsed signal_template.json.  Loaded from disk if None.
        monitoring_context: Parsed monitoring_context.json.  Loaded from disk if None.

    Returns:
        A list of SignalBuilderResult — one per customer × service_tree_id.
    """
    if template is None:
        template = load_signal_template()
    if monitoring_context is None:
        monitoring_context = load_monitoring_context()

    # Initialise MCP concurrency limiter from config
    init_mcp_semaphore(template.get("max_concurrent_mcp_calls", 5))

    results: list[SignalBuilderResult] = []

    # Compute start_time / end_time: prefer explicit ISO8601 values from
    # monitoring_context; fall back to now - default_lookback_hours / now.
    end_time_str = monitoring_context.get("end_time")
    start_time_str = monitoring_context.get("start_time")
    if not end_time_str or not start_time_str:
        default_lookback = monitoring_context.get("default_lookback_hours", 4)
        now_utc = datetime.now(timezone.utc)
        end_time_str = end_time_str or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        start_time_str = start_time_str or (now_utc - timedelta(hours=default_lookback)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for target in monitoring_context.get("targets", []):
        customer_name = target["customer_name"]
        service_tree_ids = target.get("service_tree_ids", [])

        contexts = []
        if not service_tree_ids:
            contexts.append({"customer_name": customer_name, "service_tree_id": "", "service_name": "", "start_time": start_time_str, "end_time": end_time_str, "support_product_names": "[]", "owning_tenant_names": "[]"})
        else:
            for entry in service_tree_ids:
                # Support both {id, name} objects and plain string IDs
                if isinstance(entry, dict):
                    sid = entry["id"]
                    sname = entry.get("name", "")
                else:
                    sid = entry
                    sname = ""
                support_products = entry.get("support_product_names", [])
                owning_tenants = entry.get("owning_tenant_names", [])
                contexts.append({"customer_name": customer_name, "service_tree_id": sid, "service_name": sname, "start_time": start_time_str, "end_time": end_time_str, "support_product_names": json.dumps(support_products), "owning_tenant_names": json.dumps(owning_tenants)})

        async def _evaluate_one_context(ctx: dict[str, Any]) -> SignalBuilderResult:
            """Evaluate a single context with its own XCV."""
            xcv = generate_xcv()
            set_current_xcv(xcv)
            set_current_service_tree_id(ctx["service_tree_id"])
            tracker = AgentLogger.get_instance()
            tracker.log_signal_evaluation_start(
                xcv=xcv,
                customer_name=ctx["customer_name"],
                service_tree_id=ctx["service_tree_id"],
                service_name=ctx.get("service_name", ""),
            )
            return await _evaluate_for_context(template, ctx)

        # Run all service contexts in parallel using create_task so each
        # task gets its own copy of the ContextVar state (XCV isolation).
        tasks = [asyncio.create_task(_evaluate_one_context(ctx)) for ctx in contexts]
        if tasks:
            gathered = await asyncio.gather(*tasks, return_exceptions=True)
            for i, result in enumerate(gathered):
                if isinstance(result, BaseException):
                    ctx = contexts[i]
                    logger.error(
                        "Signal evaluation failed for %s/%s: %s",
                        ctx.get("customer_name", "?"),
                        ctx.get("service_tree_id", "?"),
                        result,
                        exc_info=result,
                    )
                    continue
                results.append(result)

    return results


async def evaluate_signals_stream(
    template: dict[str, Any] | None = None,
    monitoring_context: dict[str, Any] | None = None,
    xcv_map: dict[str, str] | None = None,
) -> AsyncIterator[SignalBuilderResult]:
    """Yield SignalBuilderResults as each service context completes.

    Unlike evaluate_signals() which waits for all services before returning,
    this generator yields each result as soon as it finishes — allowing the
    caller to start downstream work (e.g. investigations) immediately.
    """
    if template is None:
        template = load_signal_template()
    if monitoring_context is None:
        monitoring_context = load_monitoring_context()

    init_mcp_semaphore(template.get("max_concurrent_mcp_calls", 5))

    end_time_str = monitoring_context.get("end_time")
    start_time_str = monitoring_context.get("start_time")
    if not end_time_str or not start_time_str:
        default_lookback = monitoring_context.get("default_lookback_hours", 4)
        now_utc = datetime.now(timezone.utc)
        end_time_str = end_time_str or now_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
        start_time_str = start_time_str or (now_utc - timedelta(hours=default_lookback)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Collect all contexts across all targets
    all_contexts: list[dict[str, Any]] = []
    for target in monitoring_context.get("targets", []):
        if target.get("enabled", True) is False:
            logger.info(
                "signal_builder: skipping disabled target customer=%s",
                target.get("customer_name"),
            )
            continue
        customer_name = target["customer_name"]
        service_tree_ids = [
            e for e in target.get("service_tree_ids", [])
            if not (isinstance(e, dict) and e.get("enabled", True) is False)
        ]
        for skipped in target.get("service_tree_ids", []):
            if isinstance(skipped, dict) and skipped.get("enabled", True) is False:
                logger.info(
                    "signal_builder: skipping disabled service customer=%s service_tree_id=%s name=%s",
                    customer_name, skipped.get("id"), skipped.get("name"),
                )
        if not service_tree_ids:
            all_contexts.append({"customer_name": customer_name, "service_tree_id": "", "service_name": "", "start_time": start_time_str, "end_time": end_time_str, "support_product_names": "[]", "owning_tenant_names": "[]"})
        else:
            for entry in service_tree_ids:
                if isinstance(entry, dict):
                    sid = entry["id"]
                    sname = entry.get("name", "")
                else:
                    sid = entry
                    sname = ""
                support_products = entry.get("support_product_names", [])
                owning_tenants = entry.get("owning_tenant_names", [])
                all_contexts.append({"customer_name": customer_name, "service_tree_id": sid, "service_name": sname, "start_time": start_time_str, "end_time": end_time_str, "support_product_names": json.dumps(support_products), "owning_tenant_names": json.dumps(owning_tenants)})

    if not all_contexts:
        return

    async def _evaluate_one(ctx: dict[str, Any]) -> SignalBuilderResult:
        sid = ctx["service_tree_id"]
        xcv = (xcv_map or {}).get(sid) or generate_xcv()
        set_current_xcv(xcv)
        set_current_service_tree_id(ctx["service_tree_id"])
        AgentLogger.get_instance().log_signal_evaluation_start(
            xcv=xcv,
            customer_name=ctx["customer_name"],
            service_tree_id=ctx["service_tree_id"],
            service_name=ctx.get("service_name", ""),
        )
        return await _evaluate_for_context(template, ctx)

    # Launch all tasks in parallel; yield results as each completes
    tasks = [asyncio.create_task(_evaluate_one(ctx)) for ctx in all_contexts]
    pending = set(tasks)
    while pending:
        done, pending = await asyncio.wait(pending, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            try:
                yield task.result()
            except Exception as exc:
                logger.error("Signal evaluation task failed: %s", exc, exc_info=exc)


# ── Parallel investigation runner ────────────────────────────────

async def _run_investigations(
    results: list[SignalBuilderResult],
    on_group_chat: Any,
    max_concurrent: int = 5,
) -> None:
    """Run investigations in parallel with bounded concurrency.

    Each investigation gets its own XCV and runs inside a semaphore-guarded
    asyncio Task.  Failures in one investigation do not affect others.

    Args:
        results: All signal builder results (filtered to actionable here).
        on_group_chat: Async callback that receives a SignalBuilderResult.
        max_concurrent: Maximum number of concurrent investigations.
    """
    actionable = [r for r in results if r.action == "invoke_group_chat"]
    non_actionable = [r for r in results if r.action != "invoke_group_chat"]

    # Always publish a terminal outcome for results that won't trigger an
    # investigation, so the Interpreter's correlator can flush its window
    # promptly instead of waiting for max_wait_minutes. Best-effort: failures
    # here must not block actionable investigations.
    if non_actionable:
        try:
            from core.services.publisher.outcome_publisher import publish_outcome
        except Exception:
            publish_outcome = None  # type: ignore[assignment]
            logger.exception(
                "Failed to import publish_outcome; skipping no-signal notifications"
            )
        if publish_outcome is not None:
            for r in non_actionable:
                xcv = r.xcv or generate_xcv()
                set_current_xcv(xcv)
                try:
                    asyncio.ensure_future(publish_outcome(
                        customer_name=r.customer_name,
                        xcv=xcv,
                        investigation=None,
                        activated_signals=[],
                        activated_compounds=[],
                        status="no_signal",
                        reason=f"action={r.action}",
                        service_tree_id=r.service_tree_id,
                        service_name=r.service_name,
                        investigation_id=xcv,
                        phase="signal_builder",
                    ))
                except Exception:
                    logger.exception(
                        "Failed to schedule no-signal publish for %s/%s",
                        r.customer_name, r.service_tree_id,
                    )

    if not actionable:
        return

    semaphore = asyncio.Semaphore(max_concurrent)

    async def _guarded(result: SignalBuilderResult) -> None:
        xcv = result.xcv or generate_xcv()
        set_current_xcv(xcv)
        async with semaphore:
            logger.info(
                "Invoking GroupChat for %s/%s (xcv=%s, %d signals, %d compounds)",
                result.customer_name, result.service_tree_id, xcv,
                len(result.all_activated_signals),
                len(result.activated_compounds),
            )
            try:
                await on_group_chat(result)
            except Exception as exc:
                classified = classify_exception(exc)
                logger.exception(
                    "Investigation failed for %s/%s (xcv=%s) [%s]",
                    result.customer_name, result.service_tree_id, xcv,
                    type(classified).__name__,
                )

    logger.info(
        "Launching %d investigations (max_concurrent=%d)",
        len(actionable), max_concurrent,
    )

    async with asyncio.TaskGroup() as tg:
        for result in actionable:
            tg.create_task(_guarded(result))


# ── Timer loop ───────────────────────────────────────────────────

async def run_signal_builder_loop(
    on_group_chat: Any = None,
    poll_override_seconds: int | None = None,
):
    """Run SignalBuilder on a timer loop.

    Args:
        on_group_chat: Async callback invoked per target whose action == "invoke_group_chat".
            Receives the SignalBuilderResult as argument.
        poll_override_seconds: Override poll interval (for testing).
    """
    monitoring_ctx = load_monitoring_context()
    interval = poll_override_seconds or monitoring_ctx.get("poll_interval_minutes", 10) * 60
    max_concurrent = monitoring_ctx.get("max_concurrent_investigations", 5)

    pipeline_mode = os.getenv("SIGNAL_PIPELINE", "in_memory").strip().lower()
    if pipeline_mode not in ("in_memory", "sandboxed"):
        logger.warning(
            "Unknown SIGNAL_PIPELINE=%r — defaulting to 'in_memory'", pipeline_mode,
        )
        pipeline_mode = "in_memory"

    logger.info(
        "SignalBuilder loop starting (pipeline=%s, interval=%ds, targets=%d, max_concurrent=%d)",
        pipeline_mode, interval, len(monitoring_ctx.get("targets", [])), max_concurrent,
    )

    while True:
        try:
            if pipeline_mode == "sandboxed":
                results = await evaluate_signals_sandboxed(monitoring_context=monitoring_ctx)
            else:
                results = await evaluate_signals(monitoring_context=monitoring_ctx)

            if on_group_chat is not None:
                await _run_investigations(results, on_group_chat, max_concurrent)

        except Exception as exc:
            classified = classify_exception(exc)
            logger.exception(
                "SignalBuilder poll cycle failed [%s]", type(classified).__name__,
            )

        await asyncio.sleep(interval)


# ── Sandboxed 3-stage pipeline ───────────────────────────────────

async def _evaluate_signal_type_from_aggregated(
    sig_type: dict[str, Any],
    aggregated_dir: str,
    client: Any | None = None,
    aggregated_payload: dict[str, list[dict[str, Any]]] | None = None,
) -> TypeSignalResult:
    """Evaluate a signal type from pre-aggregated Stage 2 output.

    Two input modes:

    * ``aggregated_payload`` — already in memory (preferred fast path,
      avoids an ADLS round-trip). Keys are granularity names, values are
      the per-group records produced by Stage 2.
    * ``client`` + ``aggregated_dir`` — read aggregated JSON from ADLS
      (legacy / fallback path).

    Args:
        sig_type: Signal type config from signal_template.json.
        aggregated_dir: ADLS prefix to the aggregated output directory.
            Only used when ``aggregated_payload`` is None.
        client: SandboxClient used to read aggregated JSON from ADLS when
            ``aggregated_payload`` is None.
        aggregated_payload: Optional in-memory ``{granularity: [rows]}``
            dict produced by the host after Stage 2 stdout parse.
    """
    type_id = sig_type["id"]
    all_rows: list[dict[str, Any]] = []
    granularity_rows: dict[str, list[dict[str, Any]]] = {}

    if aggregated_payload is not None:
        for gran_cfg in sig_type.get("granularities", []):
            gran_name = gran_cfg["granularity"]
            groups = aggregated_payload.get(gran_name) or []
            granularity_rows[gran_name] = groups
            all_rows.extend(groups)
    else:
        if client is None:
            raise ValueError(
                "_evaluate_signal_type_from_aggregated requires either "
                "aggregated_payload or a SandboxClient"
            )
        for gran_cfg in sig_type.get("granularities", []):
            gran_name = gran_cfg["granularity"]
            gran_path = f"{aggregated_dir.rstrip('/')}/{gran_name}.json"
            try:
                content = await client.read_file(gran_path)
            except Exception as exc:
                logger.debug(
                    "Aggregated file missing for %s/%s at adls:%s (%s) — skipping",
                    type_id, gran_name, gran_path, exc,
                )
                granularity_rows[gran_name] = []
                continue
            groups = json.loads(content)
            granularity_rows[gran_name] = groups
            all_rows.extend(groups)

    return _evaluate_granularities(sig_type, all_rows, granularity_rows or None)


async def evaluate_signals_sandboxed(
    template: dict[str, Any] | None = None,
    monitoring_context: dict[str, Any] | None = None,
) -> list[SignalBuilderResult]:
    """Sandboxed 3-stage signal evaluation pipeline (host-IO pattern).

    Stage 1: Fetch raw data via MCP tools → persist to ADLS (host-side).
    Stage 2: Read raw rows back, send them to a Dynamic Session as the
             ``RAW_DATA`` constant; the sandbox runs a pure-compute kernel
             (no IO) and returns the aggregated payload via stdout. Host
             then writes ``aggregated/{type_id}/{grain}.json`` to ADLS.
    Stage 3: Evaluate activation rules in-process from the in-memory dict.

    Falls back to in-memory ``evaluate_signals()`` on sandbox failure.
    """
    from core.services.signals.data_fetcher import fetch_and_persist
    from core.services.signals.aggregation_script_builder import (
        build_aggregation_script,
        RESULT_BEGIN,
        RESULT_END,
    )
    from core.sandbox.client import SandboxClient

    if template is None:
        template = load_signal_template()
    if monitoring_context is None:
        monitoring_context = load_monitoring_context()

    results: list[SignalBuilderResult] = []

    all_contexts = _resolve_monitoring_contexts(template, monitoring_context)
    if not all_contexts:
        return results

    sandbox = SandboxClient()
    adls_base = os.getenv("ADLS_BASE_PATH", "customeragent").strip("/")

    for context in all_contexts:
        xcv = generate_xcv()
        set_current_xcv(xcv)
        set_current_service_tree_id(context["service_tree_id"])
        customer_name = context.get("customer_name", "unknown")
        set_current_customer_name(customer_name)

        output_dir = f"{adls_base}/{xcv}/signals"

        try:
            # ── Stage 1: Fetch raw → ADLS (host-side) ───────────────────
            t_stage1 = time.monotonic()
            manifest = await fetch_and_persist(
                template, context, output_dir, client=sandbox,
            )
            stage1_ms = round((time.monotonic() - t_stage1) * 1000, 1)
            types_with_data = sum(1 for e in manifest.signal_types if e.row_count > 0)
            logger.info(
                "Stage 1 complete for %s/%s: %d types with data (%.1fms)",
                customer_name, context.get("service_tree_id", "?"),
                types_with_data, stage1_ms,
            )

            if types_with_data == 0:
                logger.info(
                    "No raw data fetched for %s/%s — skipping sandbox aggregation",
                    customer_name, context.get("service_tree_id", "?"),
                )
                # Build an empty result so symptom mapping still runs.
                empty = SignalBuilderResult(
                    type_results=[],
                    compound_results=[],
                    action="no_action",
                    customer_name=customer_name,
                    service_tree_id=context.get("service_tree_id", ""),
                    service_name=context.get("service_name", ""),
                    xcv=xcv,
                    start_time=context.get("start_time", ""),
                    end_time=context.get("end_time", ""),
                    owning_tenant_names=json.loads(context.get("owning_tenant_names", "[]")),
                    support_product_names=json.loads(context.get("support_product_names", "[]")),
                )
                results.append(empty)
                continue

            # ── Stage 2a: Read raw rows host-side and assemble RAW_DATA ─
            raw_data: dict[str, list[dict[str, Any]]] = {}
            for entry in manifest.signal_types:
                if entry.row_count == 0:
                    continue
                sig_type = next(
                    (st for st in template.get("signal_types", []) if st["id"] == entry.id),
                    None,
                )
                if sig_type is None:
                    continue
                strategy = sig_type.get("collection_strategy", "standard")
                try:
                    if strategy == "dependency_scan":
                        # Stage 1 wrote per-dep files under
                        # ``signals/{type_id}/dep_*.json``. Merge them all.
                        dep_dir = f"{output_dir}/{entry.id}"
                        merged: list[dict[str, Any]] = []
                        for path in await sandbox.list_files(dep_dir, recursive=False):
                            base = path.rsplit("/", 1)[-1]
                            if not (base.startswith("dep_") and base.endswith(".json")):
                                continue
                            content = await sandbox.read_file(path)
                            merged.extend(json.loads(content))
                        raw_data[entry.id] = merged
                    else:
                        # Standard types live at ``signals/{type_id}.json``.
                        path = f"{output_dir}/{entry.file or entry.id + '.json'}"
                        content = await sandbox.read_file(path)
                        raw_data[entry.id] = json.loads(content)
                except Exception as exc:
                    logger.warning(
                        "Failed to read raw rows for %s from ADLS: %s — using empty",
                        entry.id, exc,
                    )
                    raw_data[entry.id] = []

            # ── Stage 2b: Run pure-compute kernel in sandbox ────────────
            t_stage2 = time.monotonic()
            script = build_aggregation_script(manifest, template)
            sandbox_result = await sandbox.execute(
                code=script,
                filename="aggregate_signals.py",
                session_id=xcv,
                extra_constants={"XCV": xcv, "RAW_DATA": raw_data},
                inject_adls_token=False,  # script does no IO; no token needed
            )
            stage2_ms = round((time.monotonic() - t_stage2) * 1000, 1)
            if not sandbox_result.success:
                logger.error(
                    "Sandbox aggregation failed for %s/%s (%.1fms):\nstderr=%s\nstdout=%s",
                    customer_name, context.get("service_tree_id", "?"),
                    stage2_ms, sandbox_result.stderr, sandbox_result.stdout,
                )
                logger.warning("Falling back to in-memory evaluation")
                result = await _evaluate_for_context(template, context)
                results.append(result)
                continue

            aggregated_by_type = _parse_sandbox_aggregation_output(
                sandbox_result.stdout, RESULT_BEGIN, RESULT_END,
            )
            if aggregated_by_type is None:
                logger.error(
                    "Could not extract aggregation payload from sandbox stdout "
                    "for %s/%s — falling back to in-memory",
                    customer_name, context.get("service_tree_id", "?"),
                )
                result = await _evaluate_for_context(template, context)
                results.append(result)
                continue

            logger.info(
                "Stage 2 complete for %s/%s (sandbox %.2fs / wall %.1fms, %d types aggregated)",
                customer_name, context.get("service_tree_id", "?"),
                sandbox_result.duration_seconds, stage2_ms, len(aggregated_by_type),
            )

            # ── Stage 2c: Persist aggregated files to ADLS host-side ────
            for type_id, per_grain in aggregated_by_type.items():
                for grain_name, rows in per_grain.items():
                    agg_path = f"{output_dir}/aggregated/{type_id}/{grain_name}.json"
                    await sandbox.upload_file(
                        agg_path, json.dumps(rows, default=str, indent=2),
                    )

            # ── Stage 3: Activation/strength in-process from in-memory dict ─
            t_stage3 = time.monotonic()
            signal_types = template.get("signal_types", [])
            type_results_list: list[TypeSignalResult] = []
            for sig_type in signal_types:
                type_id = sig_type["id"]
                payload = aggregated_by_type.get(type_id)
                type_result = await _evaluate_signal_type_from_aggregated(
                    sig_type,
                    aggregated_dir="",  # unused when payload is provided
                    aggregated_payload=payload,
                )
                type_results_list.append(type_result)
                logger.info(
                    "Stage 3 %s: has_data=%s, activated=%d, max_strength=%.2f",
                    type_id, type_result.has_data,
                    len(type_result.activated_signals), type_result.max_strength,
                )

            type_results_map = {tr.signal_type_id: tr for tr in type_results_list}
            compound_cfgs = template.get("compound_signals", [])
            compound_results = _evaluate_compounds(compound_cfgs, type_results_map)
            action = _evaluate_decision_rules(template, type_results_list, compound_results)
            stage3_ms = round((time.monotonic() - t_stage3) * 1000, 1)

            sb_result = SignalBuilderResult(
                type_results=type_results_list,
                compound_results=compound_results,
                action=action,
                customer_name=customer_name,
                service_tree_id=context.get("service_tree_id", ""),
                service_name=context.get("service_name", ""),
                xcv=xcv,
                start_time=context.get("start_time", ""),
                end_time=context.get("end_time", ""),
                owning_tenant_names=json.loads(context.get("owning_tenant_names", "[]")),
                support_product_names=json.loads(context.get("support_product_names", "[]")),
            )
            results.append(sb_result)

            logger.info(
                "Sandboxed pipeline summary for %s/%s: stage1=%.1fms stage2=%.1fms stage3=%.1fms action=%s",
                customer_name, context.get("service_tree_id", "?"),
                stage1_ms, stage2_ms, stage3_ms, action,
            )

            # Persist final result envelope + per-grain evidence files for
            # downstream symptom mapping / investigations. Reuses the same
            # writer as the in-memory pipeline so artifacts share one schema.
            try:
                await _persist_result_to_adls(context, type_results_list, sb_result)
            except Exception as persist_exc:
                logger.warning(
                    "ADLS result persist failed for %s/%s: %s",
                    customer_name, context.get("service_tree_id", "?"),
                    persist_exc,
                )

        except Exception as exc:
            classified = classify_exception(exc)
            logger.exception(
                "Sandboxed evaluation failed for %s/%s [%s] — falling back",
                customer_name, context.get("service_tree_id", "?"),
                type(classified).__name__,
            )
            try:
                result = await _evaluate_for_context(template, context)
                results.append(result)
            except Exception as fallback_exc:
                logger.exception("Fallback also failed: %s", fallback_exc)

    return results


def _parse_sandbox_aggregation_output(
    stdout: str, begin: str, end: str,
) -> dict[str, dict[str, list[dict[str, Any]]]] | None:
    """Extract the JSON payload framed by ``begin``/``end`` markers in stdout.

    Returns ``None`` if the markers are missing or the payload is malformed.
    """
    if not stdout:
        return None
    start = stdout.find(begin)
    if start < 0:
        return None
    start += len(begin)
    stop = stdout.find(end, start)
    if stop < 0:
        return None
    payload = stdout[start:stop].strip()
    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        logger.error("Aggregation stdout JSON decode failed: %s", exc)
        return None


def _resolve_monitoring_contexts(
    template: dict[str, Any],
    monitoring_context: dict[str, Any],
) -> list[dict[str, Any]]:
    """Resolve monitoring context targets into individual context dicts.

    Extracted helper shared by ``evaluate_signals``, ``evaluate_signals_stream``,
    and ``evaluate_signals_sandboxed``.
    """
    targets = monitoring_context.get("targets", [])
    if not targets:
        return []

    now = datetime.now(tz=timezone.utc)
    lookback = monitoring_context.get("lookback_hours", 4)
    start_time_str = (now - timedelta(hours=lookback)).strftime("%Y-%m-%dT%H:%M:%SZ")
    end_time_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")

    all_contexts: list[dict[str, Any]] = []
    for target in targets:
        customer_name = target.get("customer_name", "")
        services = target.get("services", [])
        for entry in services:
            if isinstance(entry, dict):
                sid = entry["id"]
                sname = entry.get("name", "")
            else:
                sid = entry
                sname = ""
            support_products = entry.get("support_product_names", []) if isinstance(entry, dict) else []
            owning_tenants = entry.get("owning_tenant_names", []) if isinstance(entry, dict) else []
            all_contexts.append({
                "customer_name": customer_name,
                "service_tree_id": sid,
                "service_name": sname,
                "start_time": start_time_str,
                "end_time": end_time_str,
                "support_product_names": json.dumps(support_products),
                "owning_tenant_names": json.dumps(owning_tenants),
            })

    return all_contexts


# ── AST-based strength formula evaluator ─────────────────────────

# Allowed callable names in formulas
_SAFE_CALLABLES: dict[str, Any] = {
    "log2": math.log2,
    "log": math.log,
    "sqrt": math.sqrt,
    "abs": abs,
    "min": min,
    "max": max,
    "sum": sum,
}

# Allowed AST node types for the formula walker
_ALLOWED_NODES = (
    ast.Expression, ast.Module,
    ast.BinOp, ast.UnaryOp, ast.Compare, ast.IfExp, ast.BoolOp,
    ast.Constant, ast.Name, ast.Call, ast.Load,
    # Operators
    ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
    ast.USub, ast.UAdd,
    ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE,
    ast.And, ast.Or,
)

_BINOP_MAP = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARYOP_MAP = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_CMPOP_MAP = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
}


@lru_cache(maxsize=512)
def _compile_formula(formula: str) -> ast.Expression:
    """Parse, validate, and cache the AST for a formula string.

    Only nodes in ``_ALLOWED_NODES`` are permitted.  This prevents
    attribute access, subscripts, imports, comprehensions, and any
    other construct that could escape the sandbox.
    """
    py_expr = _rewrite_ternaries(formula)
    py_expr = py_expr.replace("true", "True").replace("false", "False")

    try:
        tree = ast.parse(py_expr, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"Invalid formula syntax: {formula!r}") from exc

    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED_NODES):
            raise ValueError(
                f"Disallowed construct {type(node).__name__} in formula: {formula!r}"
            )
        # Restrict Call targets to known safe names
        if isinstance(node, ast.Call):
            if not isinstance(node.func, ast.Name) or node.func.id not in _SAFE_CALLABLES:
                target = ast.dump(node.func) if not isinstance(node.func, ast.Name) else node.func.id
                raise ValueError(
                    f"Disallowed function call '{target}' in formula: {formula!r}"
                )

    return tree


def _eval_node(node: ast.AST, ns: dict[str, Any]) -> Any:
    """Recursively evaluate a validated AST node against *ns*."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, ns)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        try:
            return ns[node.id]
        except KeyError:
            raise ValueError(f"Undefined variable: {node.id!r}")

    if isinstance(node, ast.BinOp):
        left = _eval_node(node.left, ns)
        right = _eval_node(node.right, ns)
        op_fn = _BINOP_MAP.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_fn(left, right)

    if isinstance(node, ast.UnaryOp):
        operand = _eval_node(node.operand, ns)
        op_fn = _UNARYOP_MAP.get(type(node.op))
        if op_fn is None:
            raise ValueError(f"Unsupported unary op: {type(node.op).__name__}")
        return op_fn(operand)

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, ns)
        for op, comparator in zip(node.ops, node.comparators):
            right = _eval_node(comparator, ns)
            cmp_fn = _CMPOP_MAP.get(type(op))
            if cmp_fn is None:
                raise ValueError(f"Unsupported compare op: {type(op).__name__}")
            if not cmp_fn(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        if isinstance(node.op, ast.And):
            return all(_eval_node(v, ns) for v in node.values)
        if isinstance(node.op, ast.Or):
            return any(_eval_node(v, ns) for v in node.values)

    if isinstance(node, ast.IfExp):
        return (
            _eval_node(node.body, ns)
            if _eval_node(node.test, ns)
            else _eval_node(node.orelse, ns)
        )

    if isinstance(node, ast.Call):
        func = _SAFE_CALLABLES[node.func.id]
        args = [_eval_node(a, ns) for a in node.args]
        return func(*args)

    raise ValueError(f"Unexpected AST node: {type(node).__name__}")


def evaluate_strength(formula: str, variables: dict[str, Any]) -> float:
    """Safely evaluate a strength_formula string using AST walking.

    Only arithmetic, comparisons, ternaries, and a small set of math
    functions (log2, log, sqrt, abs, min, max) are permitted.  No
    attribute access, imports, or arbitrary code execution is possible.

    Parsed ASTs are cached (LRU, 512 entries) so repeated evaluation of
    the same formula across thousands of rows is fast.
    """
    tree = _compile_formula(formula)
    ns = {**_SAFE_CALLABLES, **variables}
    try:
        result = _eval_node(tree, ns)
        logger.debug("Formula '%s' → %.4f", formula, float(result))
        return float(result)
    except Exception as exc:
        logger.error("Strength formula evaluation failed: '%s' — %s", formula, exc)
        raise ValueError(f"Failed to evaluate '{formula}': {exc}") from exc


def _rewrite_ternaries(expr: str) -> str:
    """Rewrite C-style ternary ``cond ? a : b`` to Python ``(a if cond else b)``.

    Handles nested and chained ternaries (right-associative) such as::

        severity == 'A' ? 3 : severity == 'B' ? 2 : 1
        →  (3 if severity == 'A' else (2 if severity == 'B' else 1))

    Works by recursively processing parenthesised sub-expressions first,
    then rewriting bare ternaries at the current level.
    """
    if "?" not in expr:
        return expr

    # Step 1: recursively process contents of each parenthesised group
    parts: list[str] = []
    i = 0
    while i < len(expr):
        if expr[i] == "(":
            # find matching close-paren
            depth = 1
            j = i + 1
            while j < len(expr) and depth > 0:
                if expr[j] == "(":
                    depth += 1
                elif expr[j] == ")":
                    depth -= 1
                j += 1
            inner = _rewrite_ternaries(expr[i + 1 : j - 1])
            parts.append("(")
            parts.append(inner)
            parts.append(")")
            i = j
        else:
            parts.append(expr[i])
            i += 1

    flat = "".join(parts)

    # Step 2: rewrite bare ternaries at the current (depth-0) level.
    # Find the first '?' at paren-depth 0 — that starts the ternary.
    q_pos = _find_at_depth0(flat, "?")
    if q_pos == -1:
        return flat  # no ternary at this level

    # Find the matching ':' at depth 0 after the '?'
    c_pos = _find_at_depth0(flat, ":", start=q_pos + 1)
    if c_pos == -1:
        return flat  # malformed — leave unchanged

    cond = flat[:q_pos].strip()
    true_val = flat[q_pos + 1 : c_pos].strip()
    # false branch may itself be a chained ternary — recurse
    false_val = _rewrite_ternaries(flat[c_pos + 1 :].strip())

    return f"({true_val} if {cond} else {false_val})"


def _find_at_depth0(text: str, char: str, *, start: int = 0) -> int:
    """Return the index of the first *char* at parenthesis depth 0, or -1."""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
        elif text[i] == char and depth == 0:
            return i
    return -1


# ── Scoring normalisation helpers ─────────────────────────────────

_SCORE_LABELS = {
    0: "None",
    1: "Low",
    2: "Moderate",
    3: "Significant",
    4: "High",
    5: "Critical",
}


def normalize_strength(
    raw: float,
    max_raw: float,
    *,
    scale_max: float = 5.0,
    floor: float = 0.5,
) -> float:
    """Normalise a raw strength value to the 0-*scale_max* range."""
    if raw <= 0 or max_raw <= 0:
        return 0.0
    normalised = min(raw / max_raw, 1.0) * scale_max
    return max(normalised, floor)


def strength_label(value: float) -> str:
    """Return a human-readable label for a normalised 0-5 strength value."""
    bucket = max(0, min(5, round(value)))
    return _SCORE_LABELS.get(bucket, "Unknown")
