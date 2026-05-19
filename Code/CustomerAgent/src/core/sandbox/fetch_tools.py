"""
Data fetch tools — config-driven wrappers around MCP collection tools.

Each @tool function:
1. Calls the MCP endpoint(s) programmatically (bypassing LLM context)
2. Writes the full raw JSON result to sandbox /mnt/data/
3. Returns only a lightweight manifest entry to the LLM

Config-driven: MCP tool names, output filenames, and parameter mappings
are defined in config/fetch_tools_config.json. The shared _execute_fetch_plan()
handles all orchestration logic.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_framework import tool

from core.mcp_integration import create_filtered_mcp_tool
from core.sandbox.client import SandboxClient
from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)

# ─── Config loading ───────────────────────────────────────────────────────────
_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "fetch_tools_config.json"
_config: dict[str, Any] | None = None


def _get_config() -> dict[str, Any]:
    global _config
    if _config is None:
        _config = json.loads(_CONFIG_PATH.read_text(encoding="utf-8"))
    return _config


# ─── Sandbox client (lazy-init) ──────────────────────────────────────────────
_sandbox_client: SandboxClient | None = None


def _get_sandbox_client() -> SandboxClient:
    global _sandbox_client
    if _sandbox_client is None:
        _sandbox_client = SandboxClient()
    return _sandbox_client


# ─── Shared helpers ──────────────────────────────────────────────────────────

async def _call_mcp_tool(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Call an MCP tool programmatically and return the parsed JSON result."""
    from helper.auth import get_mcp_bearer_token, get_user_token

    # Verify auth tokens are available before making the call
    bearer = get_mcp_bearer_token()
    user_token = get_user_token()

    if not bearer:
        logger.warning("MCP call %s: bearer token is NONE — request will be unauthenticated", tool_name)

    mcp_tool = create_filtered_mcp_tool("data_fetcher", [tool_name])
    try:
        await mcp_tool.connect()
        try:
            result = await mcp_tool.call_tool(tool_name, **params)
        finally:
            await mcp_tool.close()

        if isinstance(result, list):
            text = "".join(c.text for c in result if hasattr(c, "text"))
        else:
            text = str(result)

        return json.loads(text)
    except Exception as exc:
        logger.exception("MCP tool %s call failed", tool_name)
        return {"error": str(exc), "rows": [], "count": 0}


async def _write_to_sandbox(filepath: str, data: dict[str, Any]) -> int:
    """Write JSON data to ADLS at the given path. Returns row count.

    ``filepath`` is an ADLS path under the configured filesystem (e.g.
    ``runs/<xcv>/evidence/foo.json``). /mnt/data is no longer used.
    """
    client = _get_sandbox_client()
    json_str = json.dumps(data, default=str, indent=2)

    try:
        await client.upload_file(filepath, json_str)
    except Exception as exc:
        logger.error("Failed to write %s to ADLS: %s", filepath, exc)
        raise RuntimeError(f"ADLS write failed for {filepath}: {exc}") from exc

    return len(data.get("rows", []))


async def _upsert_evidence_manifest(
    xcv: str,
    tool_name: str,
    new_entries: list[dict[str, Any]],
) -> str | None:
    """Read–merge–write the investigation evidence manifest in ADLS.

    Path: ``{ADLS_BASE_PATH}/{xcv}/_manifest.json`` (consumed by sandbox_coder
    via the ``read_sandbox_manifest`` tool).

    Entries are merged by ``path`` so multiple fetch_* tool invocations
    accumulate into a single manifest per investigation. Returns the manifest
    path on success, or ``None`` if persistence is disabled / fails.
    """
    if not xcv or xcv == "unknown":
        return None
    base = os.getenv("ADLS_BASE_PATH", "runs").strip("/")
    manifest_path = f"{base}/{xcv}/_manifest.json"
    client = _get_sandbox_client()

    try:
        try:
            existing_text = await client.read_file(manifest_path)
            manifest = json.loads(existing_text) if existing_text else {}
        except Exception:
            manifest = {}

        files: list[dict[str, Any]] = list(manifest.get("files", []))
        by_path = {f.get("path"): i for i, f in enumerate(files) if f.get("path")}

        for entry in new_entries:
            stamped = dict(entry)
            stamped["fetch_tool"] = tool_name
            stamped["written_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            p = stamped.get("path")
            if p in by_path:
                files[by_path[p]] = stamped
            else:
                by_path[p] = len(files)
                files.append(stamped)

        manifest["xcv"] = xcv
        manifest["files"] = files
        manifest["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        manifest.setdefault("source", "data_fetcher")

        await client.upload_file(
            manifest_path, json.dumps(manifest, default=str, indent=2)
        )
        return manifest_path
    except Exception:
        logger.exception("Failed to upsert evidence manifest at adls:%s", manifest_path)
        return None


def _extract_schema(rows: list[dict]) -> list[str]:
    """Extract column names from the first row."""
    if rows:
        return list(rows[0].keys())
    return []


# ─── Service-name path scoping ────────────────────────────────────────────────
# To prevent multiple fetch calls from overwriting the same evidence file when
# they target different services (primary vs each dependency), the engine can
# scope the output path by the service's friendly name (slugified). The mapping
# from service_tree_id → name is resolved from dependency_mappings.json plus
# the per-dependency files in config/dependency_services/.

_DEPENDENCY_SERVICES_DIR = (
    Path(__file__).resolve().parents[2] / "config" / "dependency_services"
)


def _slugify_service_name(name: str) -> str:
    """Lower-case, strip special chars, replace whitespace/dashes with '_'."""
    if not name:
        return ""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9_\s-]", "", s)
    s = re.sub(r"[\s\-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


@lru_cache(maxsize=1)
def _load_dependency_mappings() -> dict[str, Any]:
    fp = _DEPENDENCY_SERVICES_DIR / "dependency_mappings.json"
    if not fp.is_file():
        return {}
    try:
        return json.loads(fp.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("Failed to load dependency_mappings.json")
        return {}


@lru_cache(maxsize=1)
def _load_dependency_service_files() -> dict[str, dict[str, Any]]:
    """Return { service_tree_id: dep_service_dict } for every file in the dir."""
    out: dict[str, dict[str, Any]] = {}
    if not _DEPENDENCY_SERVICES_DIR.is_dir():
        return out
    for fp in _DEPENDENCY_SERVICES_DIR.glob("*.json"):
        if fp.name == "dependency_mappings.json":
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load dependency service file %s", fp)
            continue
        stid = data.get("service_tree_id", "")
        if stid and not stid.startswith("<"):
            out[stid] = data
    return out


def _resolve_service_name(service_tree_id: str) -> str | None:
    """Look up a friendly service name for a service_tree_id.

    Searches the primary mappings first, then the per-dependency files.
    """
    if not service_tree_id:
        return None
    mappings = _load_dependency_mappings().get("mappings", {})
    if service_tree_id in mappings:
        name = mappings[service_tree_id].get("name")
        if name:
            return name
    dep = _load_dependency_service_files().get(service_tree_id)
    if dep:
        return dep.get("name")
    return None


def _resolve_dependency_services(primary_service_tree_id: str) -> list[dict[str, Any]]:
    """Return the list of dependency service dicts for a given primary stid."""
    mappings = _load_dependency_mappings().get("mappings", {})
    entry = mappings.get(primary_service_tree_id)
    if not entry:
        return []
    dep_keys = entry.get("dependencies", [])
    out: list[dict[str, Any]] = []
    for key in dep_keys:
        fp = _DEPENDENCY_SERVICES_DIR / f"{key}.json"
        if not fp.is_file():
            logger.warning("Dependency service file not found: %s", fp)
            continue
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            logger.exception("Failed to load dependency service file %s", fp)
            continue
        stid = data.get("service_tree_id", "")
        if not stid or stid.startswith("<"):
            logger.info("Skipping dependency '%s' — no concrete service_tree_id", key)
            continue
        out.append(data)
    return out


def _compute_path_scope(call_spec: dict[str, Any], mcp_params: dict[str, Any]) -> str:
    """Return a slugified sub-folder for an mcp_call, or '' if no scope."""
    scope = call_spec.get("path_scope")
    if not scope:
        return ""
    raw = mcp_params.get(scope, "")
    if not raw:
        return ""
    if scope == "service_tree_id":
        name = _resolve_service_name(raw) or raw
    else:
        name = str(raw)
    return _slugify_service_name(name)


# ─── Call deduplication cache ─────────────────────────────────────────────────
# Prevents redundant MCP calls when the LLM invokes the same fetch tool
# multiple times with identical parameters within a single investigation.
# Keyed on (xcv, tool_name, frozen_params) → cached manifest JSON.
_fetch_cache: dict[tuple[str, str, str], str] = {}


def clear_fetch_cache() -> None:
    """Clear the fetch deduplication cache (call between investigations)."""
    _fetch_cache.clear()


# ─── Shared executor (config-driven) ─────────────────────────────────────────

async def _execute_fetch_plan(tool_name: str, caller_params: dict[str, Any]) -> str:
    """Execute all MCP calls defined in config for a given fetch tool.

    Reads fetch_tools_config.json, iterates mcp_calls, maps parameters,
    calls each MCP tool, writes results to sandbox, and returns manifest JSON.

    Args:
        tool_name: Key in fetch_tools_config.json (e.g. "fetch_sli_data").
        caller_params: The locals() dict from the @tool stub (all input params).

    Returns:
        JSON string with datasets manifest and duration.
    """
    xcv = get_current_xcv() or "unknown"

    # ── Deduplication: return cached result if same call already executed ──
    # Build a stable cache key from tool_name + sorted params (excluding internal keys)
    _cache_params = {k: v for k, v in caller_params.items() if not k.startswith("_")}
    _cache_key = (xcv, tool_name, json.dumps(_cache_params, sort_keys=True, default=str))
    if _cache_key in _fetch_cache:
        logger.info("[%s] Cache HIT — returning previous result (dedup)", tool_name)
        return _fetch_cache[_cache_key]

    config = _get_config()
    tool_config = config["fetch_tools"][tool_name]
    mcp_calls = tool_config["mcp_calls"]
    iterate_deps = bool(tool_config.get("iterate_dependencies", False))
    evidence_subdir = config.get("subdirs", {}).get("evidence", "evidence")
    adls_base = os.getenv("ADLS_BASE_PATH", "runs").strip("/")

    t0 = time.monotonic()
    manifest_entries: list[dict[str, Any]] = []

    # Build the list of "iteration contexts" — each is a (label, params_override)
    # pair. For non-dependency tools there's a single context with no overrides.
    iteration_contexts: list[tuple[str, dict[str, Any]]]
    if iterate_deps:
        primary_stid = caller_params.get("service_tree_id", "")
        deps = _resolve_dependency_services(primary_stid)
        if not deps:
            logger.warning(
                "[%s] iterate_dependencies=true but no dependencies resolved for primary service_tree_id=%s",
                tool_name, primary_stid,
            )
        iteration_contexts = [
            (
                dep.get("name", dep.get("service_tree_id", "unknown")),
                {"service_tree_id": dep["service_tree_id"]},
            )
            for dep in deps
        ]
    else:
        iteration_contexts = [("__primary__", {})]

    for iter_label, param_override in iteration_contexts:
        iter_caller_params = {**caller_params, **param_override}
        for call_spec in mcp_calls:
            mcp_tool_name = call_spec["tool"]
            output_file = call_spec["output_file"]
            param_names = call_spec["params"]
            er_id = call_spec.get("er_id")  # canonical ER ID from registry

            # Build params dict — "?" suffix means optional (skip if empty/falsy)
            mcp_params: dict[str, Any] = {}
            for p in param_names:
                optional = p.endswith("?")
                key = p.rstrip("?")
                value = iter_caller_params.get(key, "")
                if optional and not value:
                    continue
                mcp_params[key] = value

            # Compute path: optionally scoped by a slugified service name so
            # primary vs each dependency write to distinct files.
            scope_sub = _compute_path_scope(call_spec, mcp_params)
            if scope_sub:
                filepath = f"{adls_base}/{xcv}/{evidence_subdir}/{scope_sub}/{output_file}"
            else:
                filepath = f"{adls_base}/{xcv}/{evidence_subdir}/{output_file}"

            logger.info("[%s] Calling %s (iter=%s, scope=%s)",
                        tool_name, mcp_tool_name, iter_label, scope_sub or "-")
            result = await _call_mcp_tool(mcp_tool_name, mcp_params)

            if "error" not in result:
                rows_written = await _write_to_sandbox(filepath, result)
                schema = _extract_schema(result.get("rows", []))
                entry = {
                    "path": filepath,
                    "tool": mcp_tool_name,
                    "rows": rows_written,
                    "schema": schema,
                }
            else:
                entry = {
                    "path": filepath,
                    "tool": mcp_tool_name,
                    "rows": 0,
                    "error": result["error"],
                }
            if er_id:
                entry["er_id"] = er_id
            if scope_sub:
                entry["service_scope"] = scope_sub
            manifest_entries.append(entry)

    elapsed = round((time.monotonic() - t0) * 1000, 1)
    logger.info("[%s] Completed in %.1fms", tool_name, elapsed)

    # Persist (upsert) the cumulative evidence manifest in ADLS so
    # sandbox_coder's read_sandbox_manifest tool can find it.
    manifest_adls_path = await _upsert_evidence_manifest(xcv, tool_name, manifest_entries)

    if xcv:
        AgentLogger.get_instance()._emit("data_fetch_complete", xcv, {
            "tool": tool_name,
            "datasets": manifest_entries,
            "duration_ms": elapsed,
            "manifest_path": manifest_adls_path or "",
        })

    result_json = json.dumps({"datasets": manifest_entries, "duration_ms": elapsed})
    _fetch_cache[_cache_key] = result_json
    return result_json


# ─── Tool stubs (typed signatures for Agent Framework) ────────────────────────

@tool(name="fetch_sli_data")
async def fetch_sli_data(
    service_tree_id: str,
    customer_name: str,
    start_time: str,
    end_time: str,
) -> str:
    """Fetch customer-specific SLI breach data and store in sandbox for analysis.

    Args:
        service_tree_id: The service tree ID to query SLI data for.
        customer_name: The customer name to filter SLI breaches.
        start_time: ISO8601 start time for the query window.
        end_time: ISO8601 end time for the query window.

    Returns:
        JSON manifest with file paths, row counts, and schemas.
    """
    return await _execute_fetch_plan("fetch_sli_data", locals())


@tool(name="fetch_dependency_sli_data")
async def fetch_dependency_sli_data(
    service_tree_id: str,
    start_time: str,
    end_time: str,
) -> str:
    """Fetch SLI breach data for every dependency of a primary service.

    Reads ``dependency_mappings.json`` to find the dependencies of the given
    primary ``service_tree_id``, then calls ``collect_impacted_resource_multicustomer_tool``
    once per dependency. Each dependency's result is written to its own folder
    under ``evidence/{slugified_dep_service_name}/sli_multicustomer.json`` so
    results never overwrite each other.

    Args:
        service_tree_id: The PRIMARY service tree ID (used to look up deps).
        start_time: ISO8601 start time for the query window.
        end_time: ISO8601 end time for the query window.

    Returns:
        JSON manifest listing one file per dependency with row counts and schemas.
    """
    return await _execute_fetch_plan("fetch_dependency_sli_data", locals())


@tool(name="fetch_incident_data")
async def fetch_incident_data(
    start_time: str,
    end_time: str,
    owning_tenant_names: str = "",
) -> str:
    """Fetch incident/outage data and store in sandbox for analysis.

    Args:
        start_time: ISO8601 start time for the query window.
        end_time: ISO8601 end time for the query window.
        owning_tenant_names: JSON array string of owning tenant names (e.g. '["ScaleSet Platform"]').

    Returns:
        JSON manifest with file path, row count, and schema.
    """
    return await _execute_fetch_plan("fetch_incident_data", locals())


@tool(name="fetch_support_data")
async def fetch_support_data(
    customer_name: str,
    start_time: str,
    end_time: str,
    support_product_names: str = "",
) -> str:
    """Fetch support ticket data and store in sandbox for analysis.

    Args:
        customer_name: Customer name to filter support tickets.
        start_time: ISO8601 start time for the query window.
        end_time: ISO8601 end time for the query window.
        support_product_names: JSON array string of product names to filter (optional).

    Returns:
        JSON manifest with file paths, row counts, and schemas.
    """
    return await _execute_fetch_plan("fetch_support_data", locals())


# Export list for agent registration
ALL_FETCH_TOOLS = [fetch_sli_data, fetch_dependency_sli_data, fetch_incident_data, fetch_support_data]
