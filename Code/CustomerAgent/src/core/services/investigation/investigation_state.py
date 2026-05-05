"""Data models for the investigation lifecycle.

Adapted from OpenAgent's investigation_state.py for the MAF investigation
GroupChat pipeline. Tracks phases, symptoms, hypotheses, evidence, and
actions through the full investigation cycle.

Includes an embedded state machine that validates every phase transition
against a legal-transitions map, logs transitions, and maintains an
auditable history.
"""

from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_logger = logging.getLogger(__name__)


class InvestigationPhase(str, enum.Enum):
    """Investigation lifecycle phases.

    Sequence: initializing → triage → hypothesizing → planning → collecting
              → reasoning → acting → notifying → complete

    Cycle support: reasoning can backtrack to planning when needs_more_evidence
    is signaled (max _MAX_EVIDENCE_CYCLES times).
    """
    INITIALIZING = "initializing"
    TRIAGE = "triage"
    HYPOTHESIZING = "hypothesizing"
    PLANNING = "planning"
    COLLECTING = "collecting"
    REASONING = "reasoning"
    ACTING = "acting"
    NOTIFYING = "notifying"
    COMPLETE = "complete"


class HypothesisStatus(str, enum.Enum):
    ACTIVE = "active"
    CONFIRMED = "confirmed"
    REFUTED = "refuted"
    CONTRIBUTING = "resolved_as_contributing"


class EvidenceVerdict(str, enum.Enum):
    STRONGLY_SUPPORTS = "strongly_supports"
    SUPPORTS = "supports"
    PARTIALLY_SUPPORTS = "partially_supports"
    INCONCLUSIVE = "inconclusive"
    REFUTES = "refutes"
    STRONGLY_REFUTES = "strongly_refutes"


class SymptomVerdict(str, enum.Enum):
    """Per-symptom verdict assigned by the reasoner for a specific hypothesis."""
    SATISFIED = "satisfied"
    NOT_SATISFIED = "not_satisfied"
    INCONCLUSIVE = "inconclusive"


@dataclass
class Symptom:
    id: str
    template_id: str
    text: str
    category: str
    entities: dict[str, Any] = field(default_factory=dict)
    source_signal_type: str = ""
    weight: int = 1
    severity: str = ""
    signal_strength: float = 0.0
    confirmed: bool = False


@dataclass
class Hypothesis:
    id: str
    template_id: str
    statement: str
    category: str
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    confidence: float = 0.0
    expected_symptoms: list[str] = field(default_factory=list)
    matched_symptoms: list[str] = field(default_factory=list)
    match_score: float = 0.0
    min_symptoms_for_match: int = 2
    evidence_needed: list[str] = field(default_factory=list)
    evidence_collected: list[str] = field(default_factory=list)
    evidence_delta: list[str] = field(default_factory=list)
    verdicts: dict[str, EvidenceVerdict] = field(default_factory=dict)
    symptom_verdicts: dict[str, SymptomVerdict] = field(default_factory=dict)
    determination: str = ""
    sandbox_coder_output: str = ""


@dataclass
class EvidenceItem:
    id: str
    er_id: str
    hypothesis_ids: list[str]
    agent_name: str
    tool_name: str
    raw_data: Any = None
    summary: str = ""
    preliminary_verdict: str = ""
    final_verdict: EvidenceVerdict | None = None
    collected_at: str = ""


@dataclass
class EvidenceRequirement:
    er_id: str
    description: str
    technology_tag: str
    tool_name: str
    parameters: dict[str, str] = field(default_factory=dict)
    hypothesis_ids: list[str] = field(default_factory=list)
    status: str = "pending"


@dataclass
class InvestigationContext:
    """Shared context built during triage, extended during evidence collection."""
    customer_name: str = ""
    service_tree_id: str = ""
    region: str = ""
    subscription_id: str = ""
    sli_id: str = ""
    incident_id: str = ""
    ticket_ids: list[str] = field(default_factory=list)
    severity: str = ""
    start_time: str = ""
    end_time: str = ""
    owning_tenant_names: list[str] = field(default_factory=list)
    support_product_names: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


# ── State machine infrastructure ──────────────────────────────────


class InvalidPhaseTransition(Exception):
    """Raised when a phase transition violates the state machine rules."""


@dataclass
class PhaseTransitionRecord:
    """Auditable record of a single phase transition."""

    from_phase: InvestigationPhase
    to_phase: InvestigationPhase
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    source: str = ""
    forced: bool = False


# Default legal transitions.  Can be overridden via phase_pipeline config.
_LEGAL_TRANSITIONS: dict[InvestigationPhase, frozenset[InvestigationPhase]] = {
    InvestigationPhase.INITIALIZING: frozenset({InvestigationPhase.TRIAGE}),
    InvestigationPhase.TRIAGE: frozenset({InvestigationPhase.HYPOTHESIZING}),
    InvestigationPhase.HYPOTHESIZING: frozenset({InvestigationPhase.PLANNING}),
    InvestigationPhase.PLANNING: frozenset({InvestigationPhase.COLLECTING}),
    InvestigationPhase.COLLECTING: frozenset({InvestigationPhase.REASONING}),
    InvestigationPhase.REASONING: frozenset({
        InvestigationPhase.PLANNING,    # evidence cycle backtrack
        InvestigationPhase.ACTING,
        InvestigationPhase.NOTIFYING,
        InvestigationPhase.COMPLETE,
    }),
    InvestigationPhase.ACTING: frozenset({
        InvestigationPhase.NOTIFYING,
        InvestigationPhase.COMPLETE,
    }),
    InvestigationPhase.NOTIFYING: frozenset({InvestigationPhase.COMPLETE}),
    InvestigationPhase.COMPLETE: frozenset({
        InvestigationPhase.ACTING,  # post-GroupChat action planning when hypotheses are confirmed/contributing
    }),
}


def get_legal_transitions() -> dict[InvestigationPhase, frozenset[InvestigationPhase]]:
    """Return the current legal-transitions map (read-only snapshot)."""
    return dict(_LEGAL_TRANSITIONS)


def set_legal_transitions(overrides: dict[str, list[str]]) -> None:
    """Replace legal transitions from config (phase_pipeline.legal_transitions).

    Keys and values are phase name strings that are resolved to
    ``InvestigationPhase`` enum members.  Unknown names are silently skipped.
    """
    global _LEGAL_TRANSITIONS
    phase_map = {p.value: p for p in InvestigationPhase}
    new: dict[InvestigationPhase, frozenset[InvestigationPhase]] = {}
    for src_name, targets in overrides.items():
        src = phase_map.get(src_name)
        if src is None:
            _logger.warning("set_legal_transitions: unknown source phase %r — skipped", src_name)
            continue
        resolved = frozenset(phase_map[t] for t in targets if t in phase_map)
        new[src] = resolved
    # Merge: overrides replace matched keys, keep defaults for unmentioned phases.
    merged = dict(_LEGAL_TRANSITIONS)
    merged.update(new)
    _LEGAL_TRANSITIONS = merged
    _logger.info("Legal transitions updated from config (%d phases overridden)", len(new))


@dataclass
class Investigation:
    """Full investigation state with embedded state machine.

    Phase changes MUST go through ``transition_to()`` which validates
    the transition against ``_LEGAL_TRANSITIONS``, logs it, and records
    an auditable ``PhaseTransitionRecord`` in ``phase_history``.
    """

    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    phase: InvestigationPhase = InvestigationPhase.INITIALIZING
    context: InvestigationContext = field(default_factory=InvestigationContext)
    symptoms: list[Symptom] = field(default_factory=list)
    hypotheses: list[Hypothesis] = field(default_factory=list)
    evidence_plan: list[EvidenceRequirement] = field(default_factory=list)
    evidence: list[EvidenceItem] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    evidence_cycles: int = 0
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: str = ""
    _scoring_attempted: bool = field(default=False, repr=False)
    _current_hypothesis_id: str | None = field(default=None, repr=False)

    # Auditable phase transition history
    phase_history: list[PhaseTransitionRecord] = field(default_factory=list, repr=False)

    # Link back to the SignalBuilderResult that triggered this investigation
    signal_builder_result: Any = None

    # ── State machine ─────────────────────────────────────────

    def transition_to(
        self,
        target: InvestigationPhase,
        *,
        source: str = "",
        force: bool = False,
    ) -> InvestigationPhase:
        """Validate and execute a phase transition.

        Args:
            target: The phase to transition to.
            source: Label for who/what triggered this (for logging/audit).
            force:  If ``True``, allow ANY → COMPLETE even when not in the
                    legal-transitions map (used for emergency resolution
                    like oscillation detection).

        Returns:
            The *previous* phase.

        Raises:
            InvalidPhaseTransition: If the transition is illegal and
                ``force`` is ``False``.
        """
        old = self.phase
        if old == target:
            return old  # no-op

        legal = _LEGAL_TRANSITIONS.get(old, frozenset())
        is_emergency = force and target == InvestigationPhase.COMPLETE

        if target not in legal and not is_emergency:
            raise InvalidPhaseTransition(
                f"Illegal transition: {old.value} → {target.value} "
                f"(source={source!r}). Legal targets: "
                f"{sorted(p.value for p in legal)}"
            )

        self.phase = target
        record = PhaseTransitionRecord(
            from_phase=old,
            to_phase=target,
            source=source,
            forced=is_emergency,
        )
        self.phase_history.append(record)
        _logger.info(
            "Phase transition: %s → %s (source=%s, forced=%s, id=%s)",
            old.value, target.value, source, is_emergency, self.id,
        )
        return old

    @property
    def collected_er_ids(self) -> set[str]:
        """ER-IDs already collected across all hypothesis cycles."""
        return {ei.er_id for ei in self.evidence if ei.er_id}

    def active_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status == HypothesisStatus.ACTIVE]

    def pending_evidence(self) -> list[EvidenceRequirement]:
        return [er for er in self.evidence_plan if er.status == "pending"]

    def confirmed_hypotheses(self) -> list[Hypothesis]:
        return [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]

    def next_active_hypothesis(self) -> Hypothesis | None:
        """Return the highest-scored ACTIVE hypothesis (excluding current)."""
        active = [
            h for h in self.hypotheses
            if h.status == HypothesisStatus.ACTIVE and h.id != self._current_hypothesis_id
        ]
        if not active:
            return None
        return max(active, key=lambda h: h.match_score)

    def refute_current_hypothesis(self) -> Hypothesis | None:
        """Mark the current hypothesis as REFUTED and advance to the next.

        Returns the new current hypothesis, or None if the queue is exhausted.
        """
        if self._current_hypothesis_id:
            hyp_map = {h.id: h for h in self.hypotheses}
            current = hyp_map.get(self._current_hypothesis_id)
            if current and current.status == HypothesisStatus.ACTIVE:
                current.status = HypothesisStatus.REFUTED
        nxt = self.next_active_hypothesis()
        self._current_hypothesis_id = nxt.id if nxt else None
        return nxt
