"""
CustomerAgentInterpreter — FastAPI entry point.

Listens to Service Bus for investigation outcome messages,
runs the interpretation pipeline (collect → correlate → dedup → compose),
and writes action plans back to Cosmos DB.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

from fastapi import FastAPI
from contextlib import asynccontextmanager

_SRC_DIR = os.path.dirname(os.path.abspath(__file__))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

# Load .env from the service root (one level up from src/) before any module
# reads environment variables.
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(_SRC_DIR), ".env"))

from helper.monitoring_context import MonitoringContext  # noqa: E402
from core.collector import OutcomeCollector  # noqa: E402
from core.correlator import Correlator  # noqa: E402

logger = logging.getLogger(__name__)

_DEFAULT_MONITORING_PATH = os.path.join(_SRC_DIR, "config", "monitoring_context.json")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create agents, start Service Bus listener on startup, stop on shutdown."""
    # Re-assert logging config here. uvicorn calls dictConfig during startup
    # which can replace the root logger handlers we set up in __main__,
    # silently dropping every `logger.info(...)` call in this service.
    log_level = os.getenv("INTERPRETER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [cid=%(correlation_id)s] %(name)s: %(message)s",
        force=True,
    )
    # Install the correlation_id-stamping filter on the root logger so EVERY
    # log record (azure SDK, uvicorn, our code) gets the current
    # correlation_id attribute populated. Without this the format string
    # above would crash with KeyError on records that don't pass through
    # AgentLogger.
    from helper.agent_logger import CorrelationIdLogFilter
    _root = logging.getLogger()
    if not any(isinstance(f, CorrelationIdLogFilter) for f in _root.filters):
        _root.addFilter(CorrelationIdLogFilter())
    for h in _root.handlers:
        if not any(isinstance(f, CorrelationIdLogFilter) for f in h.filters):
            h.addFilter(CorrelationIdLogFilter())
    for noisy in ("azure", "uamqp", "azure.identity",
                  "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logger.info("Lifespan starting — logging reconfigured (level=%s)", log_level)

    from agents import create_interpreter_agents
    from helper.azure_clients import (
        assert_cosmos_partition_keys,
        close_clients,
        validate_required_env,
    )

    # Fail fast if any required env var is missing — better than silently
    # connecting to wrong/empty Cosmos containers or SB topics.
    logger.info("Validating required environment variables...")
    validate_required_env()
    logger.info("Environment validation OK")

    # Verify Cosmos containers were provisioned with the partition keys this
    # service expects. Fails fast on a misconfigured container instead of
    # surfacing as cryptic 400s on first upsert.
    logger.info("Asserting Cosmos partition keys (this requires an AAD token)...")
    try:
        await assert_cosmos_partition_keys()
    except Exception:
        logger.exception("Cosmos partition-key verification failed")
        raise
    logger.info("Cosmos partition-key check OK")

    # Multi-replica with the current in-memory timer ownership leads to
    # split-brain (each replica would also flush). The Cosmos buffer is
    # shared, but timer ownership is not. Warn loudly until distributed
    # leasing is added.
    replicas = int(os.getenv("INTERPRETER_REPLICA_COUNT", "1"))
    if replicas > 1:
        logger.warning(
            "INTERPRETER_REPLICA_COUNT=%d > 1 is unsupported: correlation "
            "timers are owned per-replica and will fire on every replica. "
            "Run as a single replica until distributed leasing is added.",
            replicas,
        )

    agents = create_interpreter_agents()
    logger.info("Created %d interpreter agents: %s", len(agents), list(agents.keys()))

    monitoring_path = os.getenv("INTERPRETER_MONITORING_CONTEXT_PATH") or _DEFAULT_MONITORING_PATH
    monitoring = MonitoringContext(monitoring_path)
    correlator = Correlator(agents, monitoring)
    # Reload any in-flight correlation windows from the Cosmos buffer so a
    # restart doesn't lose work.
    logger.info("Restoring correlator state from Cosmos buffer...")
    await correlator.restore_from_cosmos()
    logger.info("Correlator restore complete")
    collector = OutcomeCollector(correlator)
    task = asyncio.create_task(collector.listen())
    logger.info("Interpreter pipeline started — listening for outcomes")
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await close_clients()
        logger.info("Interpreter pipeline stopped")


app = FastAPI(
    title="CustomerAgentInterpreter",
    version="0.1.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    # Configure root logging BEFORE uvicorn starts so app loggers are visible.
    # Without this, `logger.info(...)` calls in lifespan/collector/etc are
    # silently dropped and the service appears hung after uvicorn's
    # "Waiting for application startup." line.
    log_level = os.getenv("INTERPRETER_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [cid=%(correlation_id)s] %(name)s: %(message)s",
        force=True,
    )
    # Install correlation_id filter at root so the format string above
    # always finds the attribute on every record.
    from helper.agent_logger import CorrelationIdLogFilter
    _root = logging.getLogger()
    if not any(isinstance(f, CorrelationIdLogFilter) for f in _root.filters):
        _root.addFilter(CorrelationIdLogFilter())
    for h in _root.handlers:
        if not any(isinstance(f, CorrelationIdLogFilter) for f in h.filters):
            h.addFilter(CorrelationIdLogFilter())
    # Quiet down chatty Azure SDK loggers unless explicitly raised.
    for noisy in ("azure", "uamqp", "azure.identity", "azure.core.pipeline.policies.http_logging_policy"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    port = int(os.getenv("PORT", "8012"))
    logger.info("Starting CustomerAgentInterpreter on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level=log_level.lower())
