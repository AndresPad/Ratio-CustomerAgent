"""Stage 1: Data Fetcher — calls MCP collection tools and persists raw JSON.

Extracts the fetch + normalise logic from signal_builder.py into a standalone
stage that writes raw rows to the sandbox filesystem for subsequent aggregation.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from typing import Any

from core.models.signals.data_fetch_manifest import DataFetchManifest, ManifestEntry
from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)


async def fetch_and_persist(
    template: dict[str, Any],
    context: dict[str, Any],
    output_dir: str,
) -> DataFetchManifest:
    """Fetch all signal type data via MCP tools and write raw JSON to output_dir.

    Args:
        template: Parsed signal_template.json.
        context: Evaluation context (customer_name, service_tree_id, etc.).
        output_dir: Directory to write raw JSON files (e.g. /mnt/data/{xcv}/signals/).

    Returns:
        DataFetchManifest describing all written files.
    """
    os.makedirs(output_dir, exist_ok=True)

    manifest = DataFetchManifest(
        xcv=get_current_xcv() or "",
        customer_name=context.get("customer_name", ""),
        service_tree_id=context.get("service_tree_id", ""),
        service_name=context.get("service_name", ""),
        start_time=context.get("start_time", ""),
        end_time=context.get("end_time", ""),
        output_dir=output_dir,
    )

    signal_types = template.get("signal_types", [])

    # Dispatch each signal type fetch in parallel
    async def _fetch_one(sig_type: dict[str, Any]) -> ManifestEntry:
        strategy = sig_type.get("collection_strategy", "standard")
        if strategy == "dependency_scan":
            return await _fetch_dependency_type(sig_type, context, output_dir, manifest)
        return await _fetch_standard_type(sig_type, context, output_dir)

    results = await asyncio.gather(
        *(_fetch_one(st) for st in signal_types),
        return_exceptions=True,
    )

    for i, result in enumerate(results):
        if isinstance(result, BaseException):
            st_id = signal_types[i].get("id", "?")
            logger.error("Data fetch failed for %s: %s", st_id, result, exc_info=result)
            manifest.signal_types.append(ManifestEntry(
                id=st_id, file="", row_count=0, collection_duration_ms=0.0,
            ))
        else:
            manifest.signal_types.append(result)

    # Write manifest
    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write(manifest.model_dump_json(indent=2))

    logger.info(
        "Data fetch complete: %d signal types, output_dir=%s",
        len(manifest.signal_types), output_dir,
    )
    return manifest


async def _fetch_standard_type(
    sig_type: dict[str, Any],
    context: dict[str, Any],
    output_dir: str,
) -> ManifestEntry:
    """Fetch a standard signal type's data and write to {type_id}.json."""
    from core.services.signals.signal_builder import _call_collection_tool, _normalise_row

    type_id = sig_type["id"]
    collection_tools = sig_type.get("collection_tools", [])
    t0 = time.monotonic()

    # Collect all tool calls in parallel
    async def _fetch_tool(tool_cfg: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        tool_name = tool_cfg["tool_name"]
        params = {}
        for param_name, context_key in tool_cfg.get("parameters_from_context", {}).items():
            val = context.get(context_key, "")
            if val:
                params[param_name] = val
        rows = await _call_collection_tool(
            tool_name, params, service_name=context.get("service_name", ""),
        )
        return tool_cfg, [_normalise_row(r) for r in rows]

    tool_results = await asyncio.gather(
        *(_fetch_tool(tc) for tc in collection_tools),
        return_exceptions=True,
    )

    # Assemble rows with feed tags
    all_rows: list[dict[str, Any]] = []
    feeds_map: dict[str, list[str]] = {}

    for result in tool_results:
        if isinstance(result, BaseException):
            logger.error("Tool fetch failed for %s: %s", type_id, result, exc_info=result)
            continue
        tool_cfg, normalised = result
        feed_grans = tool_cfg.get("feeds_granularities", [])
        for row in normalised:
            row["_feeds_granularities"] = feed_grans
        all_rows.extend(normalised)
        feeds_map[tool_cfg["tool_name"]] = feed_grans

    # Write raw rows
    file_name = f"{type_id}.json"
    file_path = os.path.join(output_dir, file_name)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(all_rows, f, default=str)

    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    logger.info("Fetched %s: %d rows in %.1fms", type_id, len(all_rows), elapsed_ms)

    return ManifestEntry(
        id=type_id,
        file=file_name,
        row_count=len(all_rows),
        collection_duration_ms=elapsed_ms,
        feeds_granularities=feeds_map,
    )


async def _fetch_dependency_type(
    sig_type: dict[str, Any],
    context: dict[str, Any],
    output_dir: str,
    manifest: DataFetchManifest,
) -> ManifestEntry:
    """Fetch dependency_scan signal type data.

    Steps:
    1. Call region tool to discover customer regions
    2. Load dependency mappings for this primary service
    3. For each dependency, call multicustomer tool
    4. Filter to customer regions, enrich, write per-dep JSON files
    """
    from core.services.signals.signal_builder import (
        _call_collection_tool, _normalise_row, _load_json, _CONFIG_DIR,
    )

    type_id = sig_type["id"]
    t0 = time.monotonic()

    # Step 1: Discover customer regions
    region_cfg = sig_type["region_tool"]
    region_params = {}
    for param_name, context_key in region_cfg.get("parameters_from_context", {}).items():
        val = context.get(context_key, "")
        if val:
            region_params[param_name] = val

    region_rows = await _call_collection_tool(
        region_cfg["tool_name"], region_params,
        service_name=context.get("service_name", ""),
    )
    customer_regions: set[str] = set()
    for row in region_rows:
        norm = _normalise_row(row)
        region = norm.get("region", norm.get("Region", ""))
        if region:
            customer_regions.add(region.lower())

    manifest.customer_regions = sorted(customer_regions)

    if not customer_regions:
        logger.info("SIG-TYPE-4: No customer regions found — skipping dependency scan")
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        return ManifestEntry(id=type_id, file="", row_count=0, collection_duration_ms=elapsed_ms)

    # Write regions file
    dep_dir = os.path.join(output_dir, type_id)
    os.makedirs(dep_dir, exist_ok=True)
    with open(os.path.join(dep_dir, "regions.json"), "w", encoding="utf-8") as f:
        json.dump(sorted(customer_regions), f)

    # Step 2: Load dependency mappings
    dep_mappings = _load_json("dependency_services/dependency_mappings.json")
    primary_stid = context.get("service_tree_id", "")
    mappings = dep_mappings.get("mappings", {})

    if primary_stid not in mappings:
        logger.info("SIG-TYPE-4: No dependency mapping for %s", primary_stid)
        elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
        return ManifestEntry(id=type_id, file=type_id, row_count=0, collection_duration_ms=elapsed_ms)

    dep_keys = mappings[primary_stid].get("dependencies", [])
    dep_services: list[dict[str, Any]] = []
    dep_services_dir = os.path.join(_CONFIG_DIR, "dependency_services")
    for dep_key in dep_keys:
        dep_file = os.path.join(dep_services_dir, f"{dep_key}.json")
        if not os.path.isfile(dep_file):
            logger.warning("Dependency file not found: %s", dep_file)
            continue
        with open(dep_file, "r", encoding="utf-8") as f:
            dep_services.append(json.load(f))

    # Step 3: Call multicustomer tool for each dependency in parallel
    dep_tool_cfg = sig_type["dependency_tool"]
    dep_tool_name = dep_tool_cfg["tool_name"]
    dep_param_field = dep_tool_cfg["parameter_field"]

    dep_extra = {}
    for param_name, ctx_key in dep_tool_cfg.get("extra_params_from_context", {}).items():
        val = context.get(ctx_key, "")
        if val:
            dep_extra[param_name] = val

    async def _fetch_dep(dep_svc: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
        dep_name = dep_svc["name"]
        dep_stid = dep_svc.get("service_tree_id", "")
        dep_category = dep_svc.get("category", "unknown")
        if not dep_stid or dep_stid.startswith("<TBD"):
            return dep_name, dep_category, []
        dep_params = {dep_param_field: dep_stid, **dep_extra}
        rows = await _call_collection_tool(dep_tool_name, dep_params, service_name=dep_name)
        return dep_name, dep_category, rows

    dep_results = await asyncio.gather(
        *(_fetch_dep(ds) for ds in dep_services),
        return_exceptions=True,
    )

    # Step 4: Filter to customer regions, enrich, write per-dep files
    total_rows = 0
    dep_names: list[str] = []

    for result in dep_results:
        if isinstance(result, BaseException):
            logger.error("Dependency fetch failed: %s", result, exc_info=result)
            continue
        dep_name, dep_category, rows = result

        filtered: list[dict[str, Any]] = []
        for row in rows:
            norm = _normalise_row(row)
            row_region = (norm.get("region", norm.get("Region", "")) or "").lower()
            if row_region in customer_regions:
                norm["DependencyServiceName"] = dep_name
                norm["dependency_service_name"] = dep_name
                norm["DependencyCategory"] = dep_category
                norm["dependency_category"] = dep_category
                filtered.append(norm)

        if filtered:
            safe_name = dep_name.replace(" ", "_").replace("/", "_").lower()
            dep_file_name = f"dep_{safe_name}.json"
            with open(os.path.join(dep_dir, dep_file_name), "w", encoding="utf-8") as f:
                json.dump(filtered, f, default=str)
            dep_names.append(dep_name)
            total_rows += len(filtered)

    manifest.dependency_services = dep_names

    elapsed_ms = round((time.monotonic() - t0) * 1000, 1)
    logger.info("Fetched %s: %d dep services, %d rows in %.1fms", type_id, len(dep_names), total_rows, elapsed_ms)

    return ManifestEntry(
        id=type_id,
        file=type_id,  # directory name
        row_count=total_rows,
        collection_duration_ms=elapsed_ms,
    )
