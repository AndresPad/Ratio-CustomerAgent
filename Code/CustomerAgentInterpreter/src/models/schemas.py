"""Pydantic schemas for the Interpreter pipeline."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class OutcomeMessage(BaseModel):
    """Notification received from Service Bus.

    Also persisted to the **buffer container** while a correlation window is
    open, so a restart can resume in-flight work:

        Container: ``INTERPRETER_COSMOS_BUFFER_CONTAINER``
        Partition key: ``/customer_name``
        Document id: ``xcv``
    """

    customer_name: str
    xcv: str
    timestamp: str
    service_tree_id: str = ""
    service_name: str = ""
    # Outcome status emitted by the publisher. Allowed values:
    #   "completed"      — full investigation ran with hypotheses
    #   "no_signal"      — signal builder produced nothing actionable
    #   "no_hypotheses"  — triage produced symptoms but zero hypotheses
    #   "error"          — investigation aborted on an unrecoverable error
    # Required field — no silent default — so the producer must always be
    # explicit and the correlator can flush windows promptly.
    status: str
    reason: str
    # Set when the message is buffered (early-bound at first message of a
    # window). Identifies the correlation window this message belongs to so
    # the buffered doc can be traced end-to-end and a restart can resume
    # the same window with the same logging key.
    correlation_id: str = ""


class OutcomeDocument(BaseModel):
    """Full outcome doc from Cosmos DB.

        Container: ``INTERPRETER_COSMOS_OUTCOMES_CONTAINER``
        Partition key: ``/xcv``
        Document id: ``id`` (set by the producing investigation; usually equals xcv)

    Read-only from this service's perspective — produced by CustomerAgent.
    """

    id: str
    customer_name: str
    xcv: str
    investigation_id: str
    timestamp: str
    phase: str
    # Outcome status mirrored from the publisher — required, no default.
    status: str
    reason: str
    hypotheses: list[dict[str, Any]] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    symptoms_count: int = 0
    hypotheses_count: int = 0
    evidence_count: int = 0
    actions_count: int = 0
    activated_signals_count: int = 0


class CorrelationGroup(BaseModel):
    """A group of correlated outcomes for the same customer within a time window.

    In-memory only — not persisted directly. The downstream ``InterpreterRun``,
    ``ActionPlan`` and ``CorrelationIndexEntry`` documents reference it via
    ``correlation_id``.
    """

    customer_name: str
    outcomes: list[OutcomeDocument]
    window_start: datetime
    window_end: datetime
    correlation_id: str


class ActionPlan(BaseModel):
    """Composed action plan output from action_composer.

        Container: ``INTERPRETER_COSMOS_ACTIONS_CONTAINER``
        Partition key: ``/customer_name``
        Document id: ``correlation_id`` (one plan per correlation window)
    """

    correlation_id: str
    customer_name: str
    actions: list[dict[str, Any]] = Field(default_factory=list)
    affected_resources: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""
    created_at: str = ""


class ActionPlanLLMResponse(BaseModel):
    """Strict shape we expect back from the action_composer LLM call.

    Validates top-level types only (actions/affected_resources must be
    lists-of-objects, summary must be a string). Prevents the orchestrator
    from persisting garbage like ``{"actions": "some string"}``.

    Each entry in ``actions`` SHOULD additionally contain (validated
    permissively here; canonical validation lives in the catalog matcher):
      - ``action_id`` (str)         — catalog action_id this maps to
      - ``service_name`` (str)      — grain key for per_service actions
      - ``impact`` (dict)           — regions/subscription_count/resource_count + optional impacted_subscriptions / impacted_resources
      - ``structured_evidence`` (list[dict]) — [{xcv, hypothesis_id, er_id}, ...]
      - ``catalog_match_confidence`` (float in [0,1])
    """

    actions: list[dict[str, Any]] = Field(default_factory=list)
    affected_resources: list[dict[str, Any]] = Field(default_factory=list)
    summary: str = ""


class ActionCatalogEntry(BaseModel):
    """Single entry in ``src/config/action_catalog.json``.

    Free-form dict-of-dict fields (``applies_when``, ``impact_fields``,
    ``payload_template``) are deliberately typed as ``dict[str, Any]`` so the
    catalog file can evolve without churning the schema. Use the loader in
    ``helper.action_catalog`` for read-time validation.
    """

    action_id: str
    display_name: str
    action_type: str
    grain: str = "per_correlation"  # per_service | per_correlation
    tier: str = "auto"              # auto | gated
    description: str = ""
    applies_when: dict[str, Any] = Field(default_factory=dict)
    evidence_required: list[str] = Field(default_factory=list)
    impact_fields: dict[str, Any] = Field(default_factory=dict)
    payload_template: dict[str, Any] = Field(default_factory=dict)


class ActionLedgerEntry(BaseModel):
    """Individual action tracked in the action_ledger for dedup.

    Partition key: /customer_name
    Document id: fingerprint (sha256 of action_type + services + root_cause_category + region)

    Status is TTL-based: if last_seen_at is older than dedup_ttl_hours (12h default),
    the action is considered inactive. On next occurrence it becomes active again (recurrence).

    Storage TTL: the ``ttl`` field is a per-item Cosmos TTL (seconds since
    last write). The container must have ``defaultTtl`` enabled (any non-null
    value, e.g. ``-1``) for per-item ``ttl`` to take effect. Default keeps an
    entry for ``INTERPRETER_LEDGER_TTL_DAYS`` days (30) past the last
    occurrence; every recurrence upserts the doc and resets the clock.
    """

    id: str = Field(description="SHA256 fingerprint of the action")
    customer_name: str
    action_id: str = ""  # catalog action_id (preferred dedup identity)
    action_type: str = ""
    title: str = ""
    service_name: str = ""  # grain key for per_service actions
    services: list[str] = Field(default_factory=list)
    root_cause_category: str = ""
    affected_region: str = ""
    status: str = "active"  # active | inactive (TTL-based)
    source_xcvs: list[str] = Field(default_factory=list)
    correlation_ids: list[str] = Field(default_factory=list)
    first_seen_at: str = ""
    last_seen_at: str = ""
    cycle_count: int = 1
    # Optional manual-close marker (future feature). When set to an ISO8601
    # timestamp, dedup short-circuits branch 2 and treats the next occurrence
    # as a fresh recurrence regardless of the TTL window. Left empty until the
    # acknowledgement API is implemented.
    closed_at: str = ""
    ttl: int | None = None  # Cosmos per-item TTL in seconds; set by dedup writer


class InterpreterRun(BaseModel):
    """Audit trail of a single Interpreter pipeline execution.

        Container: ``INTERPRETER_COSMOS_RUNS_CONTAINER``
        Partition key: ``/customer_name``
        Document id: ``correlation_id`` (one run record per pipeline execution;
        upserted at each stage transition: started → correlator_done → composed | failed)
    """

    id: str = Field(description="correlation_id used as document ID")
    correlation_id: str
    customer_name: str
    xcvs: list[str] = Field(default_factory=list)
    window_start: str = ""
    window_end: str = ""
    status: str = "started"  # started | correlator_done | composed | failed | skipped_no_signal
    correlator_duration_ms: float | None = None
    composer_duration_ms: float | None = None
    outcomes_count: int = 0
    actions_count: int = 0
    error: str | None = None
    started_at: str = ""
    completed_at: str | None = None


class CorrelationIndexEntry(BaseModel):
    """Cross-service pattern match discovered by the correlator agent.

        Container: ``INTERPRETER_COSMOS_CORRELATION_INDEX_CONTAINER``
        Partition key: ``/customer_name``
        Document id: ``id`` (UUID generated per pattern; one correlation_id may
        produce multiple entries, one per detected pattern)
    """

    id: str = Field(description="Unique ID for this pattern entry")
    correlation_id: str
    customer_name: str
    pattern_type: str = ""  # temporal_overlap | shared_resource | causal_chain | common_symptom
    description: str = ""
    confidence: str = "medium"  # high | medium | low
    related_xcvs: list[str] = Field(default_factory=list)
    shared_resources: list[str] = Field(default_factory=list)
    statistical_evidence: str = ""
    window_start: str = ""
    window_end: str = ""
    created_at: str = ""
