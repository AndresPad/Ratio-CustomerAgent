"""
MCP Server Logger — end-to-end traceability for RATIO MCP server activities.

Every incoming request can carry an XCV (cross-correlation vector) from the
calling agent.  All activities—endpoint hits, authentication, tool calls,
prompt fetches, resource fetches, SQL/Kusto queries—are logged to Azure
Application Insights as custom events with the XCV as the shared correlation
key, enabling full end-to-end tracing from agent → MCP → data sources.

Usage:
    from helper.mcp_logger import MCPLogger, get_current_xcv, set_current_xcv

    xcv = get_current_xcv()
    tracker = MCPLogger.get_instance()
    tracker.log_endpoint_hit(xcv, "POST", "/api/tools/run_tsql_query_tool")
    tracker.log_tool_call(xcv, "run_tsql_query_tool", {"query": "..."})
    ...
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ── Feature flag: set ENABLE_MCP_LOGGING=false to disable all logging ────────
_LOGGING_ENABLED = os.getenv("ENABLE_MCP_LOGGING", "true").strip().lower() in ("true", "1", "yes")

# ── Global feature flag: set LOG_MCP_CONTENT=false to redact tool/query/result content
_LOG_CONTENT = os.getenv("LOG_MCP_CONTENT", "true").strip().lower() in ("true", "1", "yes")
_REDACTED = "[REDACTED]"
# Max chars for logged content. 0 = no truncation.
_LOG_MAX_CHARS = int(os.getenv("LOG_MAX_CHARS", "0"))

# ── Per-item content logging overrides loaded from config files ──────────────
# Each entry: { "log_input": bool, "log_output": bool }
_ITEM_LOG_OVERRIDES: dict[str, dict[str, bool]] = {}


def _load_item_log_config() -> None:
    """Load per-item log_input / log_output flags from tools, prompts, and resources configs."""
    global _ITEM_LOG_OVERRIDES
    config_dir = os.path.join(os.path.dirname(__file__), "..", "config")
    config_files = {
        "tools": "tools_config.json",
        "prompts": "prompts_config.json",
        "resources": "resources_config.json",
    }
    for section, filename in config_files.items():
        path = os.path.join(config_dir, filename)
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            for item in data.get(section, []):
                name = item.get("name", "")
                if name:
                    _ITEM_LOG_OVERRIDES[name] = {
                        "log_input": item.get("log_input", _LOG_CONTENT),
                        "log_output": item.get("log_output", _LOG_CONTENT),
                    }
        except Exception as exc:
            logger.warning("Could not load %s for log config: %s", filename, exc)
    logger.info("Loaded per-item log config for %d items", len(_ITEM_LOG_OVERRIDES))


_load_item_log_config()

# ── Context variable for per-request XCV propagation ─────────────────────────
_current_xcv: ContextVar[str | None] = ContextVar(
    "current_xcv", default=None
)


def get_current_xcv() -> str | None:
    """Return the XCV bound to the current async context."""
    return _current_xcv.get()


def set_current_xcv(xcv: str):
    """Bind an XCV to the current async context. Returns the ContextVar Token for reset."""
    return _current_xcv.set(xcv)


def generate_xcv() -> str:
    """Generate a new unique XCV (UUID4)."""
    return str(uuid.uuid4())


class MCPLogger:
    """Singleton logger that emits structured events to Application Insights."""

    _instance: "MCPLogger | None" = None

    def __init__(self) -> None:
        self._ai_logger: logging.Logger | None = None
        self._init_app_insights()

    @classmethod
    def get_instance(cls) -> "MCPLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_app_insights(self) -> None:
        """Set up the dedicated App Insights logger.

        The global OpenTelemetry pipeline is configured by mcp_app.py via
        ``configure_azure_monitor()``, which auto-instruments Python logging.
        We simply create a named logger here; the global OTel LoggingHandler
        will intercept its log records and export them to App Insights.
        """
        connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
        if not connection_string:
            logger.warning(
                "APPLICATIONINSIGHTS_CONNECTION_STRING not set; "
                "MCP logging will log to Python logger only."
            )
            return

        # Use a dedicated logger name so we can identify MCP events in App Insights.
        # The global OTel handler (from configure_azure_monitor) will pick this up.
        ai_logger = logging.getLogger("mcp_logger.appinsights")
        ai_logger.setLevel(logging.INFO)
        self._ai_logger = ai_logger
        logger.info("MCP Application Insights logger initialized (via global OTel pipeline)")

    def _emit(self, event_name: str, xcv: str, properties: dict[str, Any]) -> None:
        """Emit a structured event to App Insights and to the Python logger."""
        if not _LOGGING_ENABLED:
            return

        props = {
            "xcv": xcv,
            "EventName": event_name,
            "Service": "RATIO_MCP",
            **properties,
        }

        logger.info("[%s] %s | %s | %s", xcv[:8], "RATIO_MCP", event_name, _safe_summary(props))

        if self._ai_logger:
            # Build message: "EventName | Service | [Tool/Prompt/Resource/Function] | XCV"
            entity = (
                properties.get("Tool")
                or properties.get("Function")
                or properties.get("Prompt")
                or properties.get("Resource")
                or ""
            )
            if entity:
                msg = "%s | %s | %s | %s"
                args = (event_name, "RATIO_MCP", entity, xcv)
            else:
                msg = "%s | %s | %s"
                args = (event_name, "RATIO_MCP", xcv)
            self._ai_logger.info(msg, *args, extra=props)

    # ── Endpoint / request events ────────────────────────────────────────

    def log_endpoint_hit(
        self, xcv: str, method: str, path: str, caller_ip: str = "", caller_sub: str = "",
    ) -> None:
        self._emit("EndpointHit", xcv, {
            "Method": method,
            "Path": path,
            "CallerIP": caller_ip,
            "CallerSub": caller_sub,
        })

    def log_auth(
        self, xcv: str, path: str, success: bool, reason: str = "",
    ) -> None:
        self._emit("Authentication", xcv, {
            "Path": path,
            "Success": success,
            "Reason": reason,
        })

    # ── Tool events ──────────────────────────────────────────────────────

    def log_tool_call_start(
        self, xcv: str, tool_name: str, arguments: dict[str, Any], mode: str = "sync",
    ) -> None:
        li = _should_log_input(tool_name)
        self._emit("ToolCallStart", xcv, {
            "Tool": tool_name,
            "Arguments": _redact(str(arguments), log_content=li),
            "Mode": mode,
        })

    def log_tool_call_end(
        self,
        xcv: str,
        tool_name: str,
        result: str | None = None,
        error: str | None = None,
        duration_ms: float = 0,
    ) -> None:
        lo = _should_log_output(tool_name)
        self._emit("ToolCallEnd", xcv, {
            "Tool": tool_name,
            "Result": _redact(result or "", log_content=lo),
            "Error": error or "",
            "DurationMs": round(duration_ms, 1),
        })

    # ── Query events (SQL / Kusto) ───────────────────────────────────────

    def log_query_executed(
        self,
        xcv: str,
        tool_name: str,
        query_type: str,
        query_text: str,
        row_count: int = 0,
        error: str | None = None,
        duration_ms: float = 0,
    ) -> None:
        li = _should_log_input(tool_name)
        self._emit("QueryExecuted", xcv, {
            "Tool": tool_name,
            "QueryType": query_type,
            "QueryText": _redact(query_text, log_content=li),
            "RowCount": row_count,
            "Error": error or "",
            "DurationMs": round(duration_ms, 1),
        })

    # ── Prompt events ────────────────────────────────────────────────────

    def log_prompt_served(
        self, xcv: str, prompt_name: str, length: int = 0,
    ) -> None:
        self._emit("PromptServed", xcv, {
            "Prompt": prompt_name,
            "Length": length,
        })

    # ── Resource events ──────────────────────────────────────────────────

    def log_resource_served(
        self, xcv: str, resource_name: str, source: str = "local",
    ) -> None:
        self._emit("ResourceServed", xcv, {
            "Resource": resource_name,
            "Source": source,
        })

    # ── Plugin / function events ─────────────────────────────────────────

    def log_function_call(
        self,
        xcv: str,
        function_name: str,
        arguments: dict[str, Any] | None = None,
        result: str | None = None,
        error: str | None = None,
        duration_ms: float = 0,
    ) -> None:
        li = _should_log_input(function_name)
        lo = _should_log_output(function_name)
        self._emit("FunctionCall", xcv, {
            "Function": function_name,
            "Arguments": _redact(str(arguments or {}), log_content=li),
            "Result": _redact(result or "", log_content=lo),
            "Error": error or "",
            "DurationMs": round(duration_ms, 1),
        })

    # ── Request lifecycle ────────────────────────────────────────────────

    def log_request_end(
        self, xcv: str, status: str = "complete", error: str = "",
    ) -> None:
        self._emit("RequestEnd", xcv, {
            "Status": status,
            "Error": error,
        })


# ── Helpers ──────────────────────────────────────────────────────────────────

def _redact(text: str, max_len: int | None = None, log_content: bool | None = None) -> str:
    """Return truncated text if content logging is enabled, else '[REDACTED]'.

    Args:
        max_len: Override char limit. None → use LOG_MAX_CHARS env (0 = no limit).
        log_content: Per-field override. None → fall back to global LOG_MCP_CONTENT.
    """
    should_log = log_content if log_content is not None else _LOG_CONTENT
    if not should_log:
        return _REDACTED
    limit = max_len if max_len is not None else _LOG_MAX_CHARS
    if limit <= 0:
        return text
    return _truncate(text, limit)


def _should_log_input(item_name: str | None = None) -> bool:
    """Resolve input-logging flag: per-item config → global env var."""
    if item_name and item_name in _ITEM_LOG_OVERRIDES:
        return _ITEM_LOG_OVERRIDES[item_name]["log_input"]
    return _LOG_CONTENT


def _should_log_output(item_name: str | None = None) -> bool:
    """Resolve output-logging flag: per-item config → global env var."""
    if item_name and item_name in _ITEM_LOG_OVERRIDES:
        return _ITEM_LOG_OVERRIDES[item_name]["log_output"]
    return _LOG_CONTENT


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len] + f"... ({len(text)} chars)"


def _safe_summary(props: dict) -> str:
    """One-line summary for local logging."""
    parts = []
    for k, v in props.items():
        if k in ("xcv", "EventName", "Service"):
            continue
        sv = str(v)
        if len(sv) > 100:
            sv = sv[:100] + "..."
        parts.append(f"{k}={sv}")
    return ", ".join(parts)
