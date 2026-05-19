"""Stage 2: Aggregation Script Builder — generates a deterministic, pure-compute Python script.

The generated script does NO IO. It receives raw rows via the injected
``RAW_DATA`` constant (a ``dict[type_id, list[row]]``) and emits the
aggregated result as a single line to stdout, framed by sentinel markers
so the host can parse it deterministically:

    __AGG_BEGIN__{"<type_id>": {"<granularity>": [...]}}__AGG_END__

This mirrors the same pattern used for evidence collection: ALL ADLS IO
happens host-side using ``DefaultAzureCredential``; the sandbox container
only runs untrusted compute. As a result the container image needs only
the Python stdlib — no ``azure-storage-file-datalake``, no token shim,
no extra RBAC.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.models.signals.data_fetch_manifest import DataFetchManifest

logger = logging.getLogger(__name__)

# Sentinel markers the host uses to extract the aggregated payload
# from the script's stdout. Keep in sync with
# ``signal_builder._parse_sandbox_aggregation_output``.
RESULT_BEGIN = "__AGG_BEGIN__"
RESULT_END = "__AGG_END__"


def build_aggregation_script(
    manifest: DataFetchManifest,
    template: dict[str, Any],
) -> str:
    """Generate a self-contained, IO-free aggregation script.

    Args:
        manifest: Output of Stage 1 (data_fetcher). Used to determine which
            signal types have data; ``manifest.output_dir`` is **no longer
            referenced** by the generated script.
        template: Parsed signal_template.json.

    Returns:
        A string containing the complete Python script. The script reads
        ``RAW_DATA`` (injected by ``SandboxClient.execute`` via
        ``extra_constants``), computes per-granularity aggregates, and
        prints ``__AGG_BEGIN__<json>__AGG_END__`` to stdout.
    """
    signal_types = template.get("signal_types", [])

    # Pre-compute the aggregation plan as plain data so the script doesn't
    # have to know about templates — it's a pure compute kernel driven by
    # a static plan embedded in the script.
    plan: list[dict[str, Any]] = []
    for sig_type in signal_types:
        type_id = sig_type["id"]
        entry = next((e for e in manifest.signal_types if e.id == type_id), None)
        if not entry or entry.row_count == 0:
            continue
        plan.append({
            "type_id": type_id,
            "granularities": [
                {
                    "granularity": g["granularity"],
                    "group_by": g.get("group_by", []),
                    "aggregates": g.get("aggregates", {}),
                    # Standard types tag rows with _feeds_granularities;
                    # dependency_scan does not — represented as None.
                    "feeds_filter": (
                        g["granularity"]
                        if sig_type.get("collection_strategy", "standard") != "dependency_scan"
                        else None
                    ),
                }
                for g in sig_type.get("granularities", [])
            ],
        })

    return _SCRIPT_TEMPLATE.format(
        plan=repr(plan),
        result_begin=RESULT_BEGIN,
        result_end=RESULT_END,
    )


# ── Script template ──────────────────────────────────────────────────────────
# The generated script is self-contained and depends only on the Python
# stdlib. ``RAW_DATA`` is injected as a module-level constant by
# ``SandboxClient.execute``.

_SCRIPT_TEMPLATE = '''"""Auto-generated Stage 2 signal aggregation kernel.

Inputs (injected as module-level constants by SandboxClient.execute):
    RAW_DATA: dict[type_id, list[row]]
    XCV:      str

Output: single line on stdout framed by {result_begin!r} / {result_end!r}
        containing JSON: dict[type_id, dict[granularity, list[group_record]]]
"""
import json
import re
import sys
from collections import defaultdict


PLAN = {plan}


# ── Aggregate functions ─────────────────────────────────────────────────────

def _snake_case(name):
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\\1_\\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", s1).lower()


def _get(row, key):
    val = row.get(key)
    if val is None:
        val = row.get(_snake_case(key))
    return val


def agg_count_distinct(field, rows):
    return len({{_get(r, field) for r in rows if _get(r, field) is not None}})

def agg_count(field, rows):
    return len(rows)

def agg_sum(field, rows):
    return sum(float(_get(r, field) or 0) for r in rows)

def agg_avg(field, rows):
    vals = [float(_get(r, field)) for r in rows if _get(r, field) is not None]
    return sum(vals) / len(vals) if vals else 0.0

agg_mean = agg_avg

def agg_min(field, rows):
    vals = [_get(r, field) for r in rows if _get(r, field) is not None]
    if not vals:
        return None
    try:
        return min(float(v) for v in vals)
    except (TypeError, ValueError):
        return min(vals)

def agg_max(field, rows):
    vals = [_get(r, field) for r in rows if _get(r, field) is not None]
    if not vals:
        return None
    try:
        return max(float(v) for v in vals)
    except (TypeError, ValueError):
        return max(vals)

def agg_collect_distinct(field, rows):
    return sorted({{str(_get(r, field)) for r in rows if _get(r, field) is not None}})

def agg_pre_aggregated(field, rows):
    if not rows:
        return None
    val = _get(rows[0], field)
    if isinstance(val, str) and val.isdigit():
        return int(val)
    return val


AGG_FUNCTIONS = {{
    "count_distinct": agg_count_distinct,
    "count": agg_count,
    "sum": agg_sum,
    "avg": agg_avg,
    "mean": agg_mean,
    "min": agg_min,
    "max": agg_max,
    "collect_distinct": agg_collect_distinct,
}}


def compute_aggregate(expr, rows):
    if expr.startswith("pre_aggregated:"):
        return agg_pre_aggregated(expr[len("pre_aggregated:"):], rows)
    m = re.match(r"^(\\w+)\\((.+)\\)$", expr.strip())
    if not m:
        raise ValueError("Malformed aggregate expression: " + repr(expr))
    func_name, field = m.group(1), m.group(2)
    fn = AGG_FUNCTIONS.get(func_name)
    if fn is None:
        raise ValueError("Unknown aggregate function: " + func_name)
    return fn(field, rows)


def group_and_aggregate(rows, group_by, aggregates, feeds_filter=None):
    if feeds_filter is not None:
        rows = [r for r in rows if feeds_filter in (r.get("_feeds_granularities") or [])]
    if not rows:
        return []
    if not aggregates:
        return rows
    groups = defaultdict(list)
    for row in rows:
        key = tuple(_get(row, k) for k in group_by)
        groups[key].append(row)
    out = []
    for key_vals, group_rows in groups.items():
        record = {{}}
        for k, v in zip(group_by, key_vals):
            record[k] = v
            record[_snake_case(k)] = v
        for agg_name, agg_expr in aggregates.items():
            record[agg_name] = compute_aggregate(agg_expr, group_rows)
        record["_row_count"] = len(group_rows)
        out.append(record)
    return out


# ── Drive the plan ──────────────────────────────────────────────────────────

result = {{}}
for entry in PLAN:
    type_id = entry["type_id"]
    rows = RAW_DATA.get(type_id) or []
    per_grain = {{}}
    for g in entry["granularities"]:
        per_grain[g["granularity"]] = group_and_aggregate(
            rows,
            group_by=g["group_by"],
            aggregates=g["aggregates"],
            feeds_filter=g["feeds_filter"],
        )
    result[type_id] = per_grain
    print("[agg] {{0}}: {{1}} grains, {{2}} input rows".format(
        type_id, len(per_grain), len(rows)),
        file=sys.stderr,
    )

# Frame the JSON payload so the host can extract it deterministically
# even if other prints are interleaved.
print({result_begin!r} + json.dumps(result, default=str) + {result_end!r})
'''
