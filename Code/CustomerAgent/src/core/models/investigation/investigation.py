"""Pydantic model for the top-level Investigation aggregate.

This is the canonical Investigation class used throughout the pipeline.
It is a Pydantic BaseModel (not a dataclass) so it can be serialized
directly for API responses and persistence.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field

from core.models.enums import HypothesisStatus, InvestigationPhase
from core.models.investigation.evidence_item import EvidenceItemModel
from core.models.investigation.evidence_requirement import EvidenceRequirementModel
from core.models.investigation.hypothesis import HypothesisModel
from core.models.investigation.investigation_context import InvestigationContextModel
from core.models.investigation.symptoms import SymptomModel


def _short_uuid() -> str:
    return str(uuid.uuid4())[:8]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class InvestigationModel(BaseModel):
    """Full investigation state — mutated in-place by output_parser.apply_to_investigation."""

    id: str = Field(default_factory=_short_uuid)
    phase: InvestigationPhase = InvestigationPhase.INITIALIZING
    context: InvestigationContextModel = Field(default_factory=InvestigationContextModel)
    symptoms: list[SymptomModel] = Field(default_factory=list)
    hypotheses: list[HypothesisModel] = Field(default_factory=list)
    evidence_plan: list[EvidenceRequirementModel] = Field(default_factory=list)
    evidence: list[EvidenceItemModel] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_cycles: int = 0
    started_at: str = Field(default_factory=_utc_now_iso)
    completed_at: str = ""

    # Link back to the SignalBuilderResult that triggered this investigation
    signal_builder_result: Any = None

    @property
    def collected_er_ids(self) -> set[str]:
        """ER-IDs already collected across all hypothesis cycles."""
        return {ei.er_id for ei in self.evidence if ei.er_id}

    def active_hypotheses(self) -> list[HypothesisModel]:
        return [h for h in self.hypotheses if h.status == HypothesisStatus.ACTIVE]

    def pending_evidence(self) -> list[EvidenceRequirementModel]:
        return [er for er in self.evidence_plan if er.status == "pending"]

    def confirmed_hypotheses(self) -> list[HypothesisModel]:
        return [h for h in self.hypotheses if h.status == HypothesisStatus.CONFIRMED]
