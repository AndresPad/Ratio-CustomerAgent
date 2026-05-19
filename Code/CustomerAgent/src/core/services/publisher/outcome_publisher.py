"""
Outcome Publisher — fires after action_planner completes.

Publishes differential investigation data to three destinations:
1. Azure Data Lake Storage Gen2 — activated signals, reasoner output, hypothesis details
2. Azure Cosmos DB — outcome document (summary + actions)
3. Azure Service Bus — outcome notification (session_id = customer_name)
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from azure.servicebus import ServiceBusMessage

from core.models.publisher import (
    ActivatedSignal,
    InvestigationNotification,
    OutcomeDocument,
    OutcomeHypothesis,
    OutcomeNotification,
)
from helper.azure_clients import (
    get_cosmos_client,
    get_datalake_filesystem,
    get_servicebus_client,
)
from helper.agent_logger import AgentLogger

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

# Prefix under the ADLS filesystem where outcomes are written, e.g.
#   adls://<filesystem>/outcomes/<xcv>/outcome.json
# ADLS_FILESYSTEM is shared with the sandbox layer.
_OUTCOMES_BASE_PATH = os.getenv("PUBLISHER_OUTCOMES_BASE_PATH", "customeragent").strip("/")
_COSMOS_DATABASE = os.getenv("PUBLISHER_COSMOS_DATABASE", "customeragentdb")
_COSMOS_CONTAINER = os.getenv("PUBLISHER_COSMOS_CONTAINER", "customer_agent")
_SERVICEBUS_TOPIC = os.getenv("PUBLISHER_SERVICEBUS_TOPIC", "customeragent-outcome")
_INVESTIGATION_TOPIC = os.getenv(
    "PUBLISHER_SERVICEBUS_INVESTIGATION_TOPIC", "customeragent-investigation",
)


# ── Public API ───────────────────────────────────────────────────────────────


async def publish_outcome(
    customer_name: str,
    xcv: str,
    investigation: Any,
    activated_signals: list[dict] | None = None,
    activated_compounds: list[dict] | None = None,
    *,
    status: str,
    reason: str = "",
    service_tree_id: str = "",
    service_name: str = "",
    investigation_id: str = "",
    phase: str = "",
) -> None:
    """
    Publish investigation outcome in parallel to Blob, Cosmos, and Service Bus.

    Args:
        customer_name: Customer identifier (used as blob prefix + SB session).
        xcv: Correlation vector for this investigation.
        investigation: The Investigation state object (from investigation_state.py).
            May be ``None`` for no-signal / early-exit outcomes where no
            investigation was ever created. In that case the caller must supply
            ``service_tree_id`` / ``service_name`` / ``investigation_id`` /
            ``phase`` explicitly.
        activated_signals: Optional list of activated signal dicts from signal_builder.
        activated_compounds: Optional list of activated compound signal dicts.
        status: REQUIRED terminal outcome status. One of ``"completed"`` /
            ``"no_signal"`` / ``"no_hypotheses"`` / ``"error"``. No default
            — every call site must be explicit.
        reason: Free-text reason associated with non-completed statuses.
        service_tree_id / service_name / investigation_id / phase: Required only
            when ``investigation`` is None; otherwise derived from it.
    """
    import asyncio
    import time as _time

    # If caller didn't supply signals/compounds, derive them from
    # ``investigation.signal_builder_result`` (set by the runner). This keeps
    # backward compat with older call sites that passed only ``investigation``.
    sbr = getattr(investigation, "signal_builder_result", None)
    if activated_signals is None and sbr is not None:
        try:
            activated_signals = [s.to_dict() for s in sbr.all_activated_signals]
        except Exception:
            logger.exception("publish_outcome: failed to extract activated_signals from signal_builder_result")
            activated_signals = []
    if activated_compounds is None and sbr is not None:
        try:
            activated_compounds = [c.to_dict() for c in sbr.activated_compounds]
        except Exception:
            logger.exception("publish_outcome: failed to extract activated_compounds from signal_builder_result")
            activated_compounds = []
    activated_signals = activated_signals or []
    activated_compounds = activated_compounds or []

    timestamp = datetime.now(timezone.utc).isoformat()
    _log = AgentLogger.get_instance()
    _log._emit("publish_outcome_start", xcv, {
        "CustomerName": customer_name,
        "InvestigationId": getattr(investigation, "id", ""),
        "AdlsAccount": os.getenv("ADLS_ACCOUNT", ""),
        "AdlsFilesystem": os.getenv("ADLS_FILESYSTEM", ""),
        "OutcomesBasePath": _OUTCOMES_BASE_PATH,
        "CosmosEndpoint": os.getenv("PUBLISHER_COSMOS_ENDPOINT", ""),
        "CosmosDatabase": _COSMOS_DATABASE,
        "CosmosContainer": _COSMOS_CONTAINER,
        "ServiceBusFqns": os.getenv("PUBLISHER_SERVICEBUS_FQNS", ""),
        "ServiceBusTopic": _SERVICEBUS_TOPIC,
        "ActivatedSignalsCount": len(activated_signals),
        "ActivatedCompoundsCount": len(activated_compounds),
    })
    logger.info(
        "publish_outcome: starting customer=%s xcv=%s adls=%s/%s cosmos=%s/%s sb=%s/%s",
        customer_name, xcv,
        os.getenv("ADLS_ACCOUNT", ""), os.getenv("ADLS_FILESYSTEM", ""),
        _COSMOS_DATABASE, _COSMOS_CONTAINER,
        os.getenv("PUBLISHER_SERVICEBUS_FQNS", ""), _SERVICEBUS_TOPIC,
    )

    try:
        outcome_doc = _build_outcome_document(
            customer_name=customer_name,
            xcv=xcv,
            investigation=investigation,
            activated_signals=activated_signals,
            activated_compounds=activated_compounds,
            timestamp=timestamp,
            status=status,
            reason=reason,
            service_tree_id=service_tree_id,
            service_name=service_name,
            investigation_id=investigation_id,
            phase=phase,
        )
    except Exception as exc:
        logger.exception("publish_outcome: failed to build outcome document")
        _log._emit("publish_outcome_failed", xcv, {
            "Stage": "build_document",
            "Error": str(exc),
            "ErrorType": type(exc).__name__,
        })
        return

    async def _run(label: str, coro_factory) -> tuple[str, bool, str]:
        t0 = _time.monotonic()
        try:
            await coro_factory()
            elapsed = round((_time.monotonic() - t0) * 1000, 1)
            logger.info("publish_outcome[%s]: success in %.1fms", label, elapsed)
            _log._emit(f"publish_outcome_{label}_success", xcv, {
                "DurationMs": elapsed,
                "CustomerName": customer_name,
            })
            return (label, True, "")
        except Exception as exc:
            elapsed = round((_time.monotonic() - t0) * 1000, 1)
            logger.exception("publish_outcome[%s]: FAILED after %.1fms", label, elapsed)
            _log._emit(f"publish_outcome_{label}_failed", xcv, {
                "DurationMs": elapsed,
                "Error": str(exc),
                "ErrorType": type(exc).__name__,
                "CustomerName": customer_name,
            })
            return (label, False, f"{type(exc).__name__}: {exc}")

    results = await asyncio.gather(
        _run("adls", lambda: _publish_to_adls(customer_name, xcv, outcome_doc, activated_signals, activated_compounds)),
        _run("cosmos", lambda: _publish_to_cosmos(outcome_doc)),
        _run("servicebus", lambda: _publish_to_servicebus(
            customer_name=customer_name,
            xcv=xcv,
            timestamp=timestamp,
            service_tree_id=outcome_doc.service_tree_id,
            service_name=outcome_doc.service_name,
            status=outcome_doc.status,
            reason=outcome_doc.reason,
        )),
    )
    successes = [r[0] for r in results if r[1]]
    failures = [(r[0], r[2]) for r in results if not r[1]]
    _log._emit("publish_outcome_complete", xcv, {
        "CustomerName": customer_name,
        "Successes": ",".join(successes),
        "FailureCount": len(failures),
        "Failures": "; ".join(f"{n}={e}" for n, e in failures),
    })
    if failures:
        logger.warning(
            "publish_outcome: %d/3 destinations failed for %s/%s: %s",
            len(failures), customer_name, xcv,
            "; ".join(f"{n}={e}" for n, e in failures),
        )
    else:
        logger.info("publish_outcome: all destinations succeeded for %s/%s", customer_name, xcv)


# ── Internal ─────────────────────────────────────────────────────────────────


def _build_outcome_document(
    *,
    customer_name: str,
    xcv: str,
    investigation: Any,
    activated_signals: list[dict] | None,
    activated_compounds: list[dict] | None,
    timestamp: str,
    status: str,
    reason: str = "",
    service_tree_id: str = "",
    service_name: str = "",
    investigation_id: str = "",
    phase: str = "",
) -> OutcomeDocument:
    """Build the Cosmos outcome document using Pydantic model.

    ``investigation`` may be ``None`` for early-exit / no-signal outcomes
    (status != "completed"). In that case the resulting document is a minimal
    stub with zero counts and the explicit fields supplied by the caller.
    """
    hypotheses: list[OutcomeHypothesis] = []
    symptoms_count = 0
    hypotheses_count = 0
    evidence_count = 0
    resolved_investigation_id = investigation_id
    resolved_phase = phase
    resolved_service_tree_id = service_tree_id
    resolved_service_name = service_name

    if investigation is not None:
        for h in investigation.hypotheses:
            # Map dataclass `Hypothesis` (statement/determination) → publisher
            # contract (title/root_cause). Fall back across possible field names
            # for forward/backward compatibility.
            title = (
                getattr(h, "title", None)
                or getattr(h, "statement", None)
                or getattr(h, "name", None)
                or h.id
            )
            root_cause = (
                getattr(h, "root_cause", None)
                or getattr(h, "determination", None)
                or None
            )
            hypotheses.append(OutcomeHypothesis(
                id=h.id,
                title=str(title),
                status=h.status.value if hasattr(h.status, "value") else str(h.status),
                confidence=getattr(h, "confidence", None),
                root_cause=root_cause,
            ))

        # Pull service identity from the investigation context (set by the runner)
        # or fall back to the signal_builder_result. Both come from the original
        # SignalBuilder poll cycle.
        ctx = getattr(investigation, "context", None)
        sbr = getattr(investigation, "signal_builder_result", None)
        resolved_service_tree_id = (
            resolved_service_tree_id
            or getattr(ctx, "service_tree_id", "")
            or getattr(sbr, "service_tree_id", "")
            or ""
        )
        resolved_service_name = (
            resolved_service_name
            or getattr(ctx, "service_name", "")
            or getattr(sbr, "service_name", "")
            or ""
        )
        resolved_investigation_id = resolved_investigation_id or investigation.id
        resolved_phase = (
            resolved_phase
            or (investigation.phase.value if hasattr(investigation.phase, "value") else str(investigation.phase))
        )
        symptoms_count = len(investigation.symptoms)
        hypotheses_count = len(investigation.hypotheses)
        evidence_count = len(investigation.evidence)

    return OutcomeDocument(
        id=xcv,
        customer_name=customer_name,
        service_tree_id=resolved_service_tree_id,
        service_name=resolved_service_name,
        xcv=xcv,
        investigation_id=resolved_investigation_id or xcv,
        timestamp=timestamp,
        phase=resolved_phase or "n/a",
        status=status,
        reason=reason,
        hypotheses=hypotheses,
        symptoms_count=symptoms_count,
        hypotheses_count=hypotheses_count,
        evidence_count=evidence_count,
        activated_signals_count=len(activated_signals) if activated_signals else 0,
        activated_compounds_count=len(activated_compounds) if activated_compounds else 0,
        activated_signals=list(activated_signals or []),
        activated_compounds=list(activated_compounds or []),
    )


async def _publish_to_adls(
    customer_name: str,
    xcv: str,
    outcome_doc: OutcomeDocument,
    activated_signals: list[dict] | None,
    activated_compounds: list[dict] | None,
) -> None:
    """Write differential outcome files under ``{base}/{xcv}/`` in ADLS Gen2."""
    async with get_datalake_filesystem() as fs:
        prefix = f"{_OUTCOMES_BASE_PATH}/{xcv}"

        async def _upload(name: str, data: str) -> None:
            file_client = fs.get_file_client(f"{prefix}/{name}")
            await file_client.upload_data(data, overwrite=True)

        # Outcome summary (now embeds activated_signals + activated_compounds)
        await _upload("outcome.json", outcome_doc.model_dump_json(indent=2))

        # Activated signals (full detail, separate file for downstream consumers)
        if activated_signals:
            await _upload(
                "activated_signals.json",
                json.dumps(activated_signals, default=str, indent=2),
            )

        # Activated compound signals
        if activated_compounds:
            await _upload(
                "activated_compounds.json",
                json.dumps(activated_compounds, default=str, indent=2),
            )

        # Hypothesis details
        if outcome_doc.hypotheses:
            await _upload(
                "hypotheses.json",
                json.dumps(
                    [h.model_dump() for h in outcome_doc.hypotheses],
                    default=str,
                    indent=2,
                ),
            )


async def _publish_to_cosmos(outcome_doc: OutcomeDocument) -> None:
    """Upsert outcome document to Cosmos DB."""
    async with get_cosmos_client() as cosmos_client:
        database = cosmos_client.get_database_client(_COSMOS_DATABASE)
        container = database.get_container_client(_COSMOS_CONTAINER)
        await container.upsert_item(outcome_doc.model_dump())


async def _publish_to_servicebus(
    customer_name: str,
    xcv: str,
    timestamp: str,
    status: str,
    reason: str = "",
    service_tree_id: str = "",
    service_name: str = "",
) -> None:
    """Send outcome notification to Service Bus topic with session_id = customer_name."""
    async with get_servicebus_client() as sb_client:
        sender = sb_client.get_topic_sender(topic_name=_SERVICEBUS_TOPIC)
        async with sender:
            notification = OutcomeNotification(
                customer_name=customer_name,
                xcv=xcv,
                timestamp=timestamp,
                service_tree_id=service_tree_id,
                service_name=service_name,
                status=status,
                reason=reason,
            )
            message = ServiceBusMessage(
                body=notification.model_dump_json(),
                session_id=customer_name,
                content_type="application/json",
            )
            await sender.send_messages(message)


async def publish_investigation_invocation(
    customer_name: str,
    xcv: str,
    timestamp: str,
    service_tree_id: str,
    service_name: str = "",
) -> None:
    """Publish a single investigation-invocation message to Service Bus.

    Sent from POST /api/run/services as soon as the pipeline is dispatched,
    one message per service in the request. Same `timestamp` is shared across
    all messages from a single API call so consumers can correlate the fan-out.
    """
    notification = InvestigationNotification(
        customer_name=customer_name,
        xcv=xcv,
        timestamp=timestamp,
        service_tree_id=service_tree_id,
        service_name=service_name,
    )
    async with get_servicebus_client() as sb_client:
        sender = sb_client.get_topic_sender(topic_name=_INVESTIGATION_TOPIC)
        async with sender:
            message = ServiceBusMessage(
                body=notification.model_dump_json(),
                session_id=customer_name,
                content_type="application/json",
            )
            await sender.send_messages(message)
