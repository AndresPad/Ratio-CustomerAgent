"""
Pydantic models for outcome publisher output.

These are the canonical schemas for data published to:
- Azure Blob Storage (outcome.json, activated_signals.json, hypotheses.json)
- Azure Cosmos DB (outcome document)
- Azure Service Bus (notification message)

They also serve as the contract between CustomerAgent (publisher)
and CustomerAgentInterpreter (consumer).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OutcomeHypothesis(BaseModel):
    """Hypothesis summary as published in the outcome document."""

    id: str
    title: str
    status: str
    confidence: float | None = None
    root_cause: str | None = None


class OutcomeDocument(BaseModel):
    """Full outcome document written to Cosmos DB and Blob Storage.

    This is the primary output of the publisher — it summarizes a completed
    investigation and is consumed by the Interpreter service for cross-
    investigation correlation and action composition.
    """

    id: str
    customer_name: str
    service_tree_id: str = ""
    service_name: str = ""
    xcv: str
    investigation_id: str
    timestamp: str
    phase: str
    # Outcome status — lets the Interpreter distinguish a fully-completed
    # investigation from one that exited early because nothing actionable was
    # detected. Allowed values: "completed" | "no_signal" | "no_hypotheses" |
    # "error". Required so every call site is forced to declare its terminal
    # state explicitly (no silent defaults).
    status: str
    reason: str
    hypotheses: list[OutcomeHypothesis] = Field(default_factory=list)
    symptoms_count: int = 0
    hypotheses_count: int = 0
    evidence_count: int = 0
    activated_signals_count: int = 0
    activated_compounds_count: int = 0
    activated_signals: list[dict[str, Any]] = Field(default_factory=list)
    activated_compounds: list[dict[str, Any]] = Field(default_factory=list)


class OutcomeNotification(BaseModel):
    """Service Bus message payload sent to the investigation-outcomes topic.

    session_id on the SB message = customer_name (enables session-based
    consumption in the Interpreter's collector).

    service_tree_id and service_name let the Interpreter know which service
    this outcome belongs to, so it can decide when all expected services for
    the customer have reported and the correlation window can be flushed early.
    """

    customer_name: str
    xcv: str
    timestamp: str
    service_tree_id: str = ""
    service_name: str = ""
    # Mirrors OutcomeDocument.status so the collector can short-circuit Cosmos
    # fetches for no-signal outcomes. Required — no silent defaults.
    status: str
    reason: str


class InvestigationNotification(BaseModel):
    """Service Bus message payload sent to the investigation-invocation topic.

    Emitted from POST /api/run/services as soon as the pipeline is kicked off,
    one message per (service_tree_id, xcv). All messages from a single API
    call share the same `timestamp` so downstream consumers can correlate the
    fan-out.

    session_id on the SB message = customer_name (parity with OutcomeNotification).
    """

    customer_name: str
    xcv: str
    timestamp: str
    service_tree_id: str
    service_name: str = ""


class ActivatedSignal(BaseModel):
    """A single activated signal blob entry."""

    signal_id: str | None = None
    signal_name: str | None = None
    category: str | None = None
    severity: str | None = None
    activated_at: str | None = None
    metric_value: float | None = None
    threshold: float | None = None
    resource_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class BlobManifest(BaseModel):
    """Describes the set of blobs published for one outcome under {xcv}/."""

    xcv: str
    outcome_blob: str
    activated_signals_blob: str | None = None
    hypotheses_blob: str | None = None
