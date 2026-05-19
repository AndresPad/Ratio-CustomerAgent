"""
Agent Logger for the Interpreter service.

Structured logging + Application Insights telemetry for agent invocations.
Reads per-agent log_input / log_output flags from agents_config.json.
"""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from contextvars import ContextVar
from typing import Any

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logger = logging.getLogger(__name__)

# ── Feature flags ────────────────────────────────────────────────────────────
_LOGGING_ENABLED = os.getenv("ENABLE_AGENT_LOGGING", "true").strip().lower() in ("true", "1", "yes")
_LOG_CONTENT = os.getenv("LOG_AGENT_CONTENT", "true").strip().lower() in ("true", "1", "yes")
_REDACTED = "[REDACTED]"
_LOG_MAX_CHARS = int(os.getenv("LOG_MAX_CHARS", "0"))

# ── Per-agent content logging overrides ──────────────────────────────────────
_AGENT_LOG_OVERRIDES: dict[str, dict[str, bool]] = {}


def _load_agent_log_config() -> None:
    """Load per-agent log_input / log_output flags from agents_config.json."""
    global _AGENT_LOG_OVERRIDES
    # __file__ lives in src/helper/, config lives in src/config/agents/.
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "agents", "agents_config.json",
    )
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
        for agent in data.get("agents", []):
            name = agent.get("name", "")
            if name:
                _AGENT_LOG_OVERRIDES[name] = {
                    "log_input": agent.get("log_input", _LOG_CONTENT),
                    "log_output": agent.get("log_output", _LOG_CONTENT),
                }
        logger.info("Loaded per-agent log config for %d agents", len(_AGENT_LOG_OVERRIDES))
    except Exception as exc:
        logger.warning("Could not load agents_config.json for log config: %s", exc)


_load_agent_log_config()

# ── Context variables ────────────────────────────────────────────────────────
# correlation_id — a UUID minted per correlation WINDOW (per customer
# batch) at the moment the FIRST message of the window is received.
# Carried through every collector receive, correlator buffer-write, agent
# call, dedup, composer, Cosmos upsert and ADLS write so that filtering
# App Insights by a single correlation_id yields the end-to-end execution
# trace for that batch. Also flows into ADLS paths and is persisted on
# every Cosmos document for cross-store joining.
_current_correlation_id: ContextVar[str | None] = ContextVar("current_correlation_id", default=None)
_current_customer_name: ContextVar[str | None] = ContextVar("current_customer_name", default=None)
_current_outcome_xcvs: ContextVar[list[str]] = ContextVar("current_outcome_xcvs", default=[])


def generate_correlation_id() -> str:
    """Mint a fresh correlation_id UUID. One per correlation window."""
    return uuid.uuid4().hex


def get_current_correlation_id() -> str | None:
    return _current_correlation_id.get()


def set_current_correlation_id(cid: str | None) -> None:
    _current_correlation_id.set(cid)


def get_current_outcome_xcvs() -> list[str]:
    return _current_outcome_xcvs.get()


def set_current_outcome_xcvs(xcvs: list[str]) -> None:
    _current_outcome_xcvs.set(xcvs)


def get_current_customer_name() -> str | None:
    return _current_customer_name.get()


def set_current_customer_name(name: str | None) -> None:
    _current_customer_name.set(name)


class CorrelationIdLogFilter(logging.Filter):
    """Stamp the current correlation_id onto every log record.

    Install once on the root logger AND on every handler (see ``app.py``).
    After installation, every plain ``logger.info(...)`` call across the
    service automatically carries ``record.correlation_id`` so the
    App Insights exporter and the local console formatter can include it
    without each callsite having to remember.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401
        record.correlation_id = get_current_correlation_id() or "-"
        record.customer_name = get_current_customer_name() or "-"
        return True


# ── Content redaction helpers ────────────────────────────────────────────────

def _should_log_input(agent_name: str) -> bool:
    overrides = _AGENT_LOG_OVERRIDES.get(agent_name)
    if overrides:
        return overrides.get("log_input", _LOG_CONTENT)
    return _LOG_CONTENT


def _should_log_output(agent_name: str) -> bool:
    overrides = _AGENT_LOG_OVERRIDES.get(agent_name)
    if overrides:
        return overrides.get("log_output", _LOG_CONTENT)
    return _LOG_CONTENT


def _redact(text: str, log_content: bool = True) -> str:
    if not log_content:
        return _REDACTED
    if _LOG_MAX_CHARS and len(text) > _LOG_MAX_CHARS:
        return text[:_LOG_MAX_CHARS] + f"... [truncated at {_LOG_MAX_CHARS} chars]"
    return text


# ── AgentLogger singleton ────────────────────────────────────────────────────

class AgentLogger:
    """Structured logger for Interpreter agent events → App Insights."""

    _instance: "AgentLogger | None" = None

    def __init__(self) -> None:
        self._tc = None
        self._init_app_insights()

    @classmethod
    def get_instance(cls) -> "AgentLogger":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _init_app_insights(self) -> None:
        connection_string = os.getenv("APPLICATIONINSIGHTS_CONNECTION_STRING", "")
        if not connection_string:
            logger.warning(
                "APPLICATIONINSIGHTS_CONNECTION_STRING not set; "
                "agent logging will use Python logger only."
            )
            return

        try:
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
            from azure.monitor.opentelemetry.exporter import AzureMonitorLogExporter

            ai_logger = logging.getLogger("interpreter_agent_logger.appinsights")
            ai_logger.setLevel(logging.INFO)

            if not any(isinstance(h, LoggingHandler) for h in ai_logger.handlers):
                exporter = AzureMonitorLogExporter(connection_string=connection_string)
                provider = LoggerProvider()
                provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
                handler = LoggingHandler(level=logging.INFO, logger_provider=provider)
                ai_logger.addHandler(handler)
                self._provider = provider

            self._tc = ai_logger
            logger.info("App Insights logger initialized for Interpreter")
        except ImportError:
            logger.warning(
                "opentelemetry/azure-monitor packages not installed; "
                "install azure-monitor-opentelemetry-exporter for App Insights."
            )
        except Exception as exc:
            logger.warning("Failed to initialize App Insights: %s", exc)

    def _emit(self, event_name: str, properties: dict[str, Any], component: str = "INTERPRETER") -> None:
        """Emit a structured event.

        Wire format (matches CustomerAgent's convention):
            "event | component | correlation_id"

        ``customer_name`` and the event-specific fields are attached as
        ``extra`` properties so they remain queryable in App Insights but
        the message body stays grep-friendly.
        """
        if not _LOGGING_ENABLED:
            return

        correlation_id = get_current_correlation_id() or "-"
        customer = get_current_customer_name() or "-"

        props = {
            "correlation_id": correlation_id,
            "customer_name": customer,
            "EventName": event_name,
            "Service": component,
            "timestamp": time.time(),
            **properties,
        }

        # Local console: short cid prefix + component + event + summary.
        logger.info(
            "[cid=%s] %s | %s | %s",
            correlation_id[:8] if correlation_id != "-" else "--------",
            component, event_name, _safe_summary(props),
        )

        if self._tc:
            self._tc.info(
                "%s | %s | %s",
                event_name, component, correlation_id,
                extra=props,
            )

    # ── Event methods ────────────────────────────────────────────────────

    def log_agent_invoked(self, agent_name: str, input_text: str) -> None:
        li = _should_log_input(agent_name)
        self._emit("AgentInvoked", {
            "Agent": agent_name,
            "InputText": _redact(input_text, log_content=li),
        }, component=agent_name)

    def log_agent_completed(self, agent_name: str, output_text: str, duration_ms: float = 0) -> None:
        lo = _should_log_output(agent_name)
        self._emit("AgentCompleted", {
            "Agent": agent_name,
            "OutputText": _redact(output_text, log_content=lo),
            "DurationMs": duration_ms,
        }, component=agent_name)

    def log_agent_error(self, agent_name: str, error: str) -> None:
        self._emit("AgentError", {
            "Agent": agent_name,
            "Error": error[:1000],
        }, component=agent_name)

    def log_correlation_started(self, correlation_id: str, customer_name: str, outcomes_count: int) -> None:
        self._emit("CorrelationStarted", {
            "correlation_id": correlation_id,
            "customer_name": customer_name,
            "OutcomesCount": outcomes_count,
        }, component="CORRELATOR")

    def log_action_plan_composed(self, correlation_id: str, actions_count: int) -> None:
        self._emit("ActionPlanComposed", {
            "correlation_id": correlation_id,
            "ActionsCount": actions_count,
        }, component="COMPOSER")

    # ── Pipeline lifecycle events ──────────────────────────────────────────
    def log_message_received(self, customer_name: str, xcv: str, service_name: str) -> None:
        self._emit("MessageReceived", {
            "customer_name": customer_name,
            "xcv": xcv,
            "service_name": service_name,
        }, component="COLLECTOR")

    def log_message_buffered(self, customer_name: str, xcv: str, buffered_count: int, all_received: bool) -> None:
        self._emit("MessageBuffered", {
            "customer_name": customer_name,
            "xcv": xcv,
            "buffered_count": buffered_count,
            "all_received": all_received,
        }, component="CORRELATOR")

    def log_window_opened(self, customer_name: str, correlation_id: str) -> None:
        self._emit("WindowOpened", {
            "customer_name": customer_name,
            "correlation_id": correlation_id,
        }, component="CORRELATOR")

    def log_window_flushing(self, customer_name: str, reason: str, message_count: int) -> None:
        self._emit("WindowFlushing", {
            "customer_name": customer_name,
            "reason": reason,
            "message_count": message_count,
        }, component="CORRELATOR")

    def log_pipeline_completed(self, correlation_id: str, customer_name: str, status: str, total_duration_ms: float) -> None:
        self._emit("PipelineCompleted", {
            "correlation_id": correlation_id,
            "customer_name": customer_name,
            "status": status,
            "total_duration_ms": total_duration_ms,
        }, component="INTERPRETER")

    def log_cosmos_write(self, container: str, doc_id: str, partition_key: str) -> None:
        self._emit("CosmosWrite", {
            "container": container,
            "doc_id": doc_id,
            "partition_key": partition_key,
        }, component="COSMOS")

    def log_adls_write(self, adls_path: str, byte_count: int) -> None:
        self._emit("AdlsWrite", {
            "adls_path": adls_path,
            "byte_count": byte_count,
        }, component="ADLS")

    def flush(self, timeout_millis: int = 5000) -> None:
        if hasattr(self, "_provider") and self._provider:
            self._provider.force_flush(timeout_millis)


def _safe_summary(props: dict[str, Any], max_len: int = 200) -> str:
    """Return a truncated summary string for local logging."""
    parts = []
    for k, v in props.items():
        if k in ("EventName", "Service", "timestamp"):
            continue
        sv = str(v)
        if len(sv) > 80:
            sv = sv[:80] + "..."
        parts.append(f"{k}={sv}")
    summary = " | ".join(parts)
    return summary[:max_len] if max_len else summary
