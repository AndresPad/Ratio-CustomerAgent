"""Config-driven tool registration engine.

Tools are defined in tools_config.json. Supported types:
  - tsql:   Parameterized T-SQL query against Fabric lakehouse.
  - kusto:  Parameterized KQL query against Azure Data Explorer.
  - plugin: Custom Python module with async entry function.

To add a new tool:
  1. Add an entry to tools_config.json.
  2. For kusto/tsql: optionally add a .kql query file in src/queries/.
  3. For plugin: drop a Python module in src/plugins/ with an async entry function.
No changes to this file required.
"""
from __future__ import annotations
import json, logging, os, importlib, inspect, contextvars, time as _time
from typing import Optional, Any
from datetime import datetime, date, time, timedelta, timezone
from decimal import Decimal
from core.mcp_app import mcpserver, LOCAL_DATASETS_DIR, logger
from helper.mcp_logger import MCPLogger, get_current_xcv, generate_xcv

# Context variable for passing user bearer token from the API layer
# to tool handlers without adding it to the MCP tool parameter schema.
user_token_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("user_token", default=None)

# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------
_SRC_DIR = os.path.dirname(os.path.dirname(__file__))
_CONFIG_PATH = os.path.join(_SRC_DIR, "config", "tools_config.json")
_QUERIES_DIR = os.path.join(_SRC_DIR, "queries")


def _load_tools_config() -> list[dict]:
    """Load tool definitions from tools_config.json."""
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["tools"]


# ---------------------------------------------------------------------------
# Sanitization helpers (shared by tsql & kusto handlers)
# ---------------------------------------------------------------------------

def _sanitize_value(v):
    if isinstance(v, (datetime, date, time)):
        return v.isoformat()
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, bytes):
        try:
            return v.decode('utf-8')
        except UnicodeDecodeError:
            return v.hex()
    return v


def _sanitize_rows(rows):
    sanitized = []
    for r in rows:
        if isinstance(r, dict):
            sanitized.append({k: _sanitize_value(v) for k, v in r.items()})
        else:
            sanitized.append(_sanitize_value(r))
    return sanitized


# ---------------------------------------------------------------------------
# Handler factories
# ---------------------------------------------------------------------------

def _load_kql_query(filename: str) -> str:
    """Load a KQL query template from the queries directory."""
    path = os.path.join(_QUERIES_DIR, filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _make_tsql_handler(entry: dict):
    """Build an async handler for a T-SQL tool config entry.

    Supports optional endpoint_env / database_env in config to target
    different SQL endpoints (e.g. Fabric lakehouse vs Synapse).
    """
    blocked_prefixes = tuple(
        p.lower() + " " for p in entry.get("blocked_prefixes", [])
    )
    params = entry.get("parameters", {})
    default_max_rows = params.get("max_rows", {}).get("default", 100)

    # Resolve endpoint/database from env vars specified in config (or None → defaults)
    endpoint_env = entry.get("endpoint_env")
    database_env = entry.get("database_env")
    sql_endpoint = os.getenv(endpoint_env) if endpoint_env else None
    sql_database = os.getenv(database_env) if database_env else None

    async def handler(query: str, max_rows: Optional[int] = default_max_rows) -> str:
        import helper.lakehouse as lakehouse
        logger.debug("%s invoked", entry["name"])
        if not query or not query.strip():
            return json.dumps({"error": "Query string is required."})
        lowered = query.strip().lower()
        if blocked_prefixes and lowered.startswith(blocked_prefixes):
            return json.dumps({"error": f"Destructive statements are blocked by {entry['name']}."})
        xcv = get_current_xcv() or generate_xcv()
        mcp_log = MCPLogger.get_instance()
        mcp_log.log_tool_call_start(xcv, entry["name"], {"query": query, "max_rows": max_rows})
        _t0 = _time.monotonic()
        try:
            token = user_token_var.get(None)
            logger.info("SQL tool %s: user_token_var is %s",
                        entry["name"], f"SET (len={len(token)})" if token else "NONE")
            rows = lakehouse.run_tsql_query(
                query, user_token=token,
                endpoint=sql_endpoint, database=sql_database,
            )
            if max_rows is not None and isinstance(max_rows, int) and max_rows >= 0:
                rows = rows[:max_rows]
            rows = _sanitize_rows(rows)
            elapsed = (_time.monotonic() - _t0) * 1000
            result = json.dumps({"rows": rows, "count": len(rows)}, ensure_ascii=False)
            mcp_log.log_query_executed(xcv, entry["name"], "tsql", query, row_count=len(rows), duration_ms=elapsed)
            mcp_log.log_tool_call_end(xcv, entry["name"], result=result, duration_ms=elapsed)
            return result
        except Exception as e:
            elapsed = (_time.monotonic() - _t0) * 1000
            logger.error("%s failed: %s", entry["name"], e, exc_info=True)
            mcp_log.log_query_executed(xcv, entry["name"], "tsql", query, error=str(e), duration_ms=elapsed)
            mcp_log.log_tool_call_end(xcv, entry["name"], error=str(e), duration_ms=elapsed)
            return json.dumps({"error": str(e)})

    return handler


# Type mapping for building dynamic function signatures from config
_TYPE_MAP = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "object": dict,
    "array": list,
}


import re as _re

_KQL_TIMESPAN_RE = _re.compile(
    r"^(\d+)\s*(d|h|m|min|s)$", _re.IGNORECASE,
)

def _parse_kql_timespan_hours(value: str) -> float | None:
    """Parse a KQL-style timespan (e.g. '4h', '1d', '30m') into hours.

    Returns None if the format is not recognised.
    """
    if not value:
        return None
    m = _KQL_TIMESPAN_RE.match(value.strip())
    if not m:
        return None
    n, unit = float(m.group(1)), m.group(2).lower()
    if unit == "d":
        return n * 24
    if unit == "h":
        return n
    if unit in ("m", "min"):
        return n / 60
    if unit == "s":
        return n / 3600
    return None


def _build_signature(params_spec: dict) -> inspect.Signature:
    """Build an inspect.Signature from a tool config parameters spec.

    This allows FastMCP to generate correct JSON Schema for the MCP tool,
    so clients (MCP Inspector, Agent Builder, mcp_client.py) know what
    parameters the tool expects.
    """
    params = []
    for pname, pspec in params_spec.items():
        annotation = _TYPE_MAP.get(pspec.get("type", "string"), str)
        default = inspect.Parameter.empty
        if not pspec.get("required", False):
            default = pspec.get("default", inspect.Parameter.empty)
        params.append(inspect.Parameter(
            pname,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            default=default,
            annotation=annotation,
        ))
    return inspect.Signature(params, return_annotation=str)


def _make_kusto_handler(entry: dict):
    """Build an async handler for a Kusto tool config entry."""
    cluster_env = entry["cluster_env"]
    database_env = entry["database_env"]
    query_file = entry["query_file"]
    kusto_params_spec = entry.get("kusto_params", {})
    validation = entry.get("validation", {})
    cert_client_id_env = entry.get("cert_client_id_env")
    default_max_rows = entry.get("max_rows", 200)  # cap Kusto results to avoid token explosion

    async def handler(**kwargs) -> str:
        from helper.kusto_auth import get_kusto_client
        from azure.kusto.data import ClientRequestProperties

        logger.debug("%s invoked with %s", entry["name"], kwargs)

        # Validate required params
        for pname, pspec in entry.get("parameters", {}).items():
            if pspec.get("required") and (pname not in kwargs or not kwargs[pname]):
                return json.dumps({"error": f"{pname} is required"})

        # Time range validation if configured
        time_val = validation.get("time_range")
        if time_val:
            start_str = kwargs.get(time_val["start_param"], "")
            end_str = kwargs.get(time_val["end_param"], "")

            def _parse_ts(ts: str):
                try:
                    ts = ts.strip()
                    if ts.endswith("Z"):
                        ts = ts[:-1] + "+00:00"
                    dt = datetime.fromisoformat(ts)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.astimezone(timezone.utc)
                except Exception:
                    return None

            start_dt = _parse_ts(start_str)
            end_dt = _parse_ts(end_str)
            if not start_dt or not end_dt:
                return json.dumps({"error": "Invalid timestamp format. Use ISO8601 e.g. 2025-10-20T12:34:56Z"})
            if end_dt < start_dt:
                return json.dumps({"error": "end_time must be after start_time"})
            now_utc = datetime.utcnow().replace(tzinfo=timezone.utc)
            max_days = timedelta(days=time_val.get("max_days", 30))
            max_age = timedelta(days=time_val.get("max_age_days", 30))
            if start_dt < (now_utc - max_age) or (end_dt - start_dt) > max_days:
                return json.dumps({"error": f"querying only last {time_val.get('max_age_days', 30)}days data is supported and start time can not be older than {time_val.get('max_age_days', 30)}days from today"})

        cluster = os.getenv(cluster_env)
        database = os.getenv(database_env)
        if not database:
            return json.dumps({"error": f"{database_env} environment variable is required."})

        # Build Kusto client request properties
        crp = ClientRequestProperties()
        for kparam_name, kparam_spec in kusto_params_spec.items():
            source = kparam_spec["source"]
            value = kwargs.get(source)
            # For optional params, use default from config when value is None/empty
            if value is None or value == "":
                param_spec = entry.get("parameters", {}).get(source, {})
                if not param_spec.get("required", False):
                    value = param_spec.get("default", "")
                    crp.set_parameter(kparam_name, str(value))
                    continue
            cast = kparam_spec.get("cast")
            if cast == "int":
                try:
                    value = int(value)
                except (ValueError, TypeError):
                    return json.dumps({"error": f"Invalid {source} (not numeric): {value}"})
            crp.set_parameter(kparam_name, value)

        xcv = get_current_xcv() or generate_xcv()
        mcp_log = MCPLogger.get_instance()
        mcp_log.log_tool_call_start(xcv, entry["name"], kwargs)
        _t0 = _time.monotonic()
        try:
            kql = _load_kql_query(query_file)
            cert_client_id = os.getenv(cert_client_id_env) if cert_client_id_env else None
            client = get_kusto_client(cluster, cert_client_id=cert_client_id)
            response = client.execute(database, kql, crp)
            primary = response.primary_results[0]
            columns = [c.column_name for c in primary.columns]
            rows = [{columns[i]: row[i] for i in range(len(columns))} for row in primary.rows]
            rows = _sanitize_rows(rows)
            total_rows = len(rows)
            if default_max_rows and len(rows) > default_max_rows:
                rows = rows[:default_max_rows]
                logger.info("%s: truncated %d rows → %d (max_rows=%d)", entry["name"], total_rows, len(rows), default_max_rows)
            elapsed = (_time.monotonic() - _t0) * 1000
            result = json.dumps({"rows": rows, "count": len(rows), "total_count": total_rows}, ensure_ascii=False)
            mcp_log.log_query_executed(xcv, entry["name"], "kusto", kql, row_count=len(rows), duration_ms=elapsed)
            mcp_log.log_tool_call_end(xcv, entry["name"], result=result, duration_ms=elapsed)
            return result
        except Exception as e:
            elapsed = (_time.monotonic() - _t0) * 1000
            logger.error("%s failed: %s", entry["name"], e, exc_info=True)
            mcp_log.log_query_executed(xcv, entry["name"], "kusto", query_file, error=str(e), duration_ms=elapsed)
            mcp_log.log_tool_call_end(xcv, entry["name"], error=str(e), duration_ms=elapsed)
            return json.dumps({"error": str(e)})

    # Set proper __signature__ and __annotations__ so FastMCP/Pydantic
    # generates correct input schema.  Pydantic uses get_type_hints()
    # which reads __annotations__, not just inspect.signature().
    sig = _build_signature(entry.get("parameters", {}))
    handler.__signature__ = sig
    handler.__annotations__ = {
        p.name: p.annotation
        for p in sig.parameters.values()
        if p.annotation is not inspect.Parameter.empty
    }
    handler.__annotations__["return"] = str
    return handler


def _load_plugin(module_path: str, entry_function: str):
    """Import a plugin module and return the entry function."""
    mod = importlib.import_module(module_path)
    func = getattr(mod, entry_function)
    if not callable(func):
        raise ValueError(f"Plugin {module_path}.{entry_function} is not callable")
    return func


def _wrap_plugin_handler(raw_func, tool_name: str):
    """Wrap a plugin entry function with MCPLogger instrumentation."""
    import functools

    @functools.wraps(raw_func)
    async def wrapper(**kwargs) -> str:
        xcv = get_current_xcv() or generate_xcv()
        mcp_log = MCPLogger.get_instance()
        mcp_log.log_tool_call_start(xcv, tool_name, kwargs, mode="plugin")
        _t0 = _time.monotonic()
        try:
            result = await raw_func(**kwargs)
            elapsed = (_time.monotonic() - _t0) * 1000
            mcp_log.log_function_call(xcv, tool_name, arguments=kwargs, result=result, duration_ms=elapsed)
            mcp_log.log_tool_call_end(xcv, tool_name, result=result, duration_ms=elapsed)
            return result
        except Exception as e:
            elapsed = (_time.monotonic() - _t0) * 1000
            mcp_log.log_function_call(xcv, tool_name, arguments=kwargs, error=str(e), duration_ms=elapsed)
            mcp_log.log_tool_call_end(xcv, tool_name, error=str(e), duration_ms=elapsed)
            raise

    # Preserve original signature for FastMCP schema generation
    wrapper.__signature__ = inspect.signature(raw_func)
    wrapper.__annotations__ = getattr(raw_func, '__annotations__', {})
    return wrapper


# ---------------------------------------------------------------------------
# Dynamic tool registration from config
# ---------------------------------------------------------------------------

# Registry of tool handler functions keyed by name (used by api_routes)
_tool_registry: dict[str, Any] = {}


def get_tool_handler(name: str):
    """Return the handler function for a named tool, or None."""
    return _tool_registry.get(name)


def get_all_tool_names() -> list[str]:
    """Return list of all registered tool names."""
    return list(_tool_registry.keys())


def _register_tools() -> list[str]:
    """Read tools_config.json and register each entry with FastMCP.

    Returns list of registered tool names.
    """
    entries = _load_tools_config()
    registered: list[str] = []
    for entry in entries:
        tool_type = entry["type"]
        name = entry["name"]
        desc = entry.get("description", "")

        if tool_type == "tsql":
            handler = _make_tsql_handler(entry)
        elif tool_type == "kusto":
            handler = _make_kusto_handler(entry)
        elif tool_type == "plugin":
            handler = _wrap_plugin_handler(
                _load_plugin(entry["module"], entry["entry_function"]), name,
            )
        else:
            logger.error("Unknown tool type '%s' for tool '%s'; skipping", tool_type, name)
            continue

        handler.__name__ = name
        handler.__qualname__ = name
        handler.__doc__ = desc

        mcpserver.tool()(handler)
        _tool_registry[name] = handler
        registered.append(name)

    logger.info("Registered %d tools from config: %s", len(registered), registered)
    return registered


_registered_names = _register_tools()

__all__ = _registered_names + ["get_tool_handler", "get_all_tool_names"]
