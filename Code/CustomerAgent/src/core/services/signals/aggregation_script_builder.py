"""Stage 2: Aggregation Script Builder — generates deterministic Python scripts for sandbox.

Reads the signal_template.json granularity config and produces a self-contained
Python script that:
1. Reads raw JSON from Stage 1 files
2. Groups rows by granularity.group_by
3. Computes aggregates (count_distinct, sum, avg, etc.)
4. Writes one aggregated JSON file per granularity
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from core.models.signals.data_fetch_manifest import DataFetchManifest

logger = logging.getLogger(__name__)


def build_aggregation_script(
    manifest: DataFetchManifest,
    template: dict[str, Any],
) -> str:
    """Generate a standalone Python script that aggregates raw signal data.

    The generated script uses only stdlib (json, os, collections) so it can
    run safely in the sandbox without additional dependencies.

    Args:
        manifest: Output of Stage 1 (data_fetcher).
        template: Parsed signal_template.json.

    Returns:
        A string containing the complete Python script.
    """
    output_dir = manifest.output_dir
    signal_types = template.get("signal_types", [])

    # Build the script piecewise
    parts: list[str] = []
    parts.append(_script_header(output_dir))
    parts.append(_aggregate_functions())

    for sig_type in signal_types:
        type_id = sig_type["id"]
        # Find corresponding manifest entry
        entry = next((e for e in manifest.signal_types if e.id == type_id), None)
        if not entry or entry.row_count == 0:
            continue

        strategy = sig_type.get("collection_strategy", "standard")
        if strategy == "dependency_scan":
            parts.append(_dependency_type_block(sig_type, entry, output_dir))
        else:
            parts.append(_standard_type_block(sig_type, entry, output_dir))

    parts.append(_script_footer())
    script = "\n".join(parts)
    logger.debug("Generated aggregation script: %d lines", script.count("\n"))
    return script


def _script_header(output_dir: str) -> str:
    """Import block and output directory setup."""
    return f'''"""Auto-generated aggregation script for signal builder Stage 2."""
import json
import os
from collections import defaultdict

OUTPUT_DIR = {repr(output_dir)}
AGGREGATED_DIR = os.path.join(OUTPUT_DIR, "aggregated")
os.makedirs(AGGREGATED_DIR, exist_ok=True)


def _snake_case(name):
    """Convert PascalCase/camelCase to snake_case."""
    import re
    s1 = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\\1_\\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\\1_\\2", s1).lower()


def _get(row, key):
    """Get value from row with snake_case fallback."""
    val = row.get(key)
    if val is None:
        val = row.get(_snake_case(key))
    return val

'''


def _aggregate_functions() -> str:
    """Built-in aggregate function implementations for the sandbox script."""
    return '''
def agg_count_distinct(field, rows):
    return len({_get(r, field) for r in rows if _get(r, field) is not None})

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
    return sorted({str(_get(r, field)) for r in rows if _get(r, field) is not None})

def agg_pre_aggregated(field, rows):
    """Read a pre-aggregated value from the first row."""
    if not rows:
        return None
    val = _get(rows[0], field)
    if isinstance(val, str) and val.isdigit():
        return int(val)
    return val

AGG_FUNCTIONS = {
    "count_distinct": agg_count_distinct,
    "count": agg_count,
    "sum": agg_sum,
    "avg": agg_avg,
    "mean": agg_mean,
    "min": agg_min,
    "max": agg_max,
    "collect_distinct": agg_collect_distinct,
}


def compute_aggregate(expr, rows):
    """Parse func(field) and dispatch to aggregate function."""
    if expr.startswith("pre_aggregated:"):
        field = expr[len("pre_aggregated:"):]
        return agg_pre_aggregated(field, rows)
    import re
    m = re.match(r"^(\\w+)\\((.+)\\)$", expr.strip())
    if not m:
        raise ValueError(f"Malformed aggregate expression: {expr!r}")
    func_name, field = m.group(1), m.group(2)
    fn = AGG_FUNCTIONS.get(func_name)
    if fn is None:
        raise ValueError(f"Unknown aggregate function: {func_name}")
    return fn(field, rows)


def group_and_aggregate(rows, group_by, aggregates, feeds_filter=None):
    """Group rows by keys, compute aggregates, return list of group records."""
    # Filter by feeds_granularities if specified
    if feeds_filter:
        rows = [r for r in rows if feeds_filter in r.get("_feeds_granularities", [])]

    if not rows:
        return []

    if not aggregates:
        # No aggregation — each row is its own group
        return rows

    # Group rows by composite key
    groups = defaultdict(list)
    for row in rows:
        key = tuple(_get(row, k) for k in group_by)
        groups[key].append(row)

    results = []
    for key_vals, group_rows in groups.items():
        record = {}
        # Carry forward group_by values
        for k, v in zip(group_by, key_vals):
            record[k] = v
            record[_snake_case(k)] = v

        # Compute aggregates
        for agg_name, agg_expr in aggregates.items():
            record[agg_name] = compute_aggregate(agg_expr, group_rows)

        record["_row_count"] = len(group_rows)
        results.append(record)

    return results

'''


def _standard_type_block(
    sig_type: dict[str, Any],
    entry: Any,
    output_dir: str,
) -> str:
    """Generate aggregation block for a standard signal type."""
    type_id = sig_type["id"]
    file_path = os.path.join(output_dir, entry.file)
    type_out_dir = os.path.join(output_dir, "aggregated", type_id)

    lines = [
        f'# ── {type_id}: {sig_type.get("name", "")} ──',
        f'print("Processing {type_id}...")',
        f'_type_dir = os.path.join(AGGREGATED_DIR, {repr(type_id)})',
        f'os.makedirs(_type_dir, exist_ok=True)',
        f'with open({repr(file_path)}, "r") as f:',
        f'    _rows_{type_id.replace("-", "_")} = json.load(f)',
        f'print(f"  Loaded {{len(_rows_{type_id.replace("-", "_")})}} rows")',
        '',
    ]

    var_name = f"_rows_{type_id.replace('-', '_')}"

    for gran_cfg in sig_type.get("granularities", []):
        gran_name = gran_cfg["granularity"]
        group_by = gran_cfg.get("group_by", [])
        aggregates = gran_cfg.get("aggregates", {})

        lines.append(f'# Granularity: {gran_name}')
        lines.append(f'_groups = group_and_aggregate(')
        lines.append(f'    {var_name},')
        lines.append(f'    group_by={json.dumps(group_by)},')
        lines.append(f'    aggregates={json.dumps(aggregates)},')
        lines.append(f'    feeds_filter={repr(gran_name)},')
        lines.append(f')')
        out_file = os.path.join(type_out_dir, f"{gran_name}.json")
        lines.append(f'with open(os.path.join(_type_dir, {repr(gran_name + ".json")}), "w") as f:')
        lines.append(f'    json.dump(_groups, f, default=str)')
        lines.append(f'print(f"  {gran_name}: {{len(_groups)}} groups")')
        lines.append('')

    return "\n".join(lines) + "\n"


def _dependency_type_block(
    sig_type: dict[str, Any],
    entry: Any,
    output_dir: str,
) -> str:
    """Generate aggregation block for the dependency_scan signal type."""
    type_id = sig_type["id"]
    dep_dir = os.path.join(output_dir, type_id)
    type_out_dir = os.path.join(output_dir, "aggregated", type_id)

    lines = [
        f'# ── {type_id}: {sig_type.get("name", "")} (dependency_scan) ──',
        f'print("Processing {type_id} (dependency scan)...")',
        f'_dep_dir = {repr(dep_dir)}',
        f'_type_dir = os.path.join(AGGREGATED_DIR, {repr(type_id)})',
        f'os.makedirs(_type_dir, exist_ok=True)',
        '',
        '# Load all dependency files and merge rows',
        f'_all_dep_rows = []',
        f'for _fname in os.listdir(_dep_dir):',
        f'    if _fname.startswith("dep_") and _fname.endswith(".json"):',
        f'        with open(os.path.join(_dep_dir, _fname), "r") as f:',
        f'            _all_dep_rows.extend(json.load(f))',
        f'print(f"  Loaded {{len(_all_dep_rows)}} total dependency rows")',
        '',
    ]

    for gran_cfg in sig_type.get("granularities", []):
        gran_name = gran_cfg["granularity"]
        group_by = gran_cfg.get("group_by", [])
        aggregates = gran_cfg.get("aggregates", {})

        lines.append(f'# Granularity: {gran_name}')
        lines.append(f'_groups = group_and_aggregate(')
        lines.append(f'    _all_dep_rows,')
        lines.append(f'    group_by={json.dumps(group_by)},')
        lines.append(f'    aggregates={json.dumps(aggregates)},')
        lines.append(f')')
        lines.append(f'with open(os.path.join(_type_dir, {repr(gran_name + ".json")}), "w") as f:')
        lines.append(f'    json.dump(_groups, f, default=str)')
        lines.append(f'print(f"  {gran_name}: {{len(_groups)}} groups")')
        lines.append('')

    return "\n".join(lines) + "\n"


def _script_footer() -> str:
    """Final print statement."""
    return '''
print("\\nAggregation complete.")
'''
