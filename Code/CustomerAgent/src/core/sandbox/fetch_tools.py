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
import time
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
    """Write JSON data to sandbox at the given path. Creates parent dirs. Returns row count."""
    client = _get_sandbox_client()
    json_str = json.dumps(data, default=str)

    code = (
        "import json, os\n"
        "from pathlib import Path\n"
        f"data = json.loads({repr(json_str)})\n"
        f"Path('{filepath}').parent.mkdir(parents=True, exist_ok=True)\n"
        f"Path('{filepath}').write_text(json.dumps(data, indent=2))\n"
        f"print(f'Written {{len(data.get(\"rows\", []))}} rows to {filepath}')\n"
    )

    safe_name = filepath.replace("/", "_").replace(".", "_")
    result = await client.execute(code=code, filename=f"write_{safe_name}.py")
    if not result.success:
        logger.error("Failed to write %s to sandbox: %s", filepath, result.stderr)
        raise RuntimeError(f"Sandbox write failed for {filepath}: {result.stderr}")

    return len(data.get("rows", []))


def _extract_schema(rows: list[dict]) -> list[str]:
    """Extract column names from the first row."""
    if rows:
        return list(rows[0].keys())
    return []


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
    evidence_subdir = config.get("subdirs", {}).get("evidence", "evidence")

    t0 = time.monotonic()
    manifest_entries = []

    for call_spec in mcp_calls:
        mcp_tool_name = call_spec["tool"]
        output_file = call_spec["output_file"]
        filepath = f"/mnt/data/{xcv}/{evidence_subdir}/{output_file}"
        param_names = call_spec["params"]

        # Build params dict — "?" suffix means optional (skip if empty/falsy)
        mcp_params: dict[str, Any] = {}
        for p in param_names:
            optional = p.endswith("?")
            key = p.rstrip("?")
            value = caller_params.get(key, "")
            if optional and not value:
                continue
            mcp_params[key] = value

        logger.info("[%s] Calling %s", tool_name, mcp_tool_name)
        result = await _call_mcp_tool(mcp_tool_name, mcp_params)

        if "error" not in result:
            rows_written = await _write_to_sandbox(filepath, result)
            schema = _extract_schema(result.get("rows", []))
            manifest_entries.append({
                "path": filepath,
                "tool": mcp_tool_name,
                "rows": rows_written,
                "schema": schema,
            })
        else:
            manifest_entries.append({
                "path": filepath,
                "tool": mcp_tool_name,
                "rows": 0,
                "error": result["error"],
            })

    elapsed = round((time.monotonic() - t0) * 1000, 1)
    logger.info("[%s] Completed in %.1fms", tool_name, elapsed)

    if xcv:
        AgentLogger.get_instance()._emit("data_fetch_complete", xcv, {
            "tool": tool_name,
            "datasets": manifest_entries,
            "duration_ms": elapsed,
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
ALL_FETCH_TOOLS = [fetch_sli_data, fetch_incident_data, fetch_support_data]
