"""Data models for the SignalBuilder pipeline.

Defines the output structures produced by signal evaluation:
- ActivatedSignal: one granularity that passed activation_rules
- TypeSignalResult: aggregated result for one signal type
- CompoundSignalResult: fusion result across multiple types
- SignalBuilderResult: top-level output of a single poll cycle
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ── Activated granularity ─────────────────────────────────────────

@dataclass
class ActivatedSignal:
    """A single granularity that passed its activation_rules."""

    signal_type_id: str
    signal_name: str
    granularity: str
    confidence: str
    strength: float
    raw_strength: float = 0.0
    activation_summary: str = ""
    matched_rows: list[dict[str, Any]] = field(default_factory=list)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type_id": self.signal_type_id,
            "signal_name": self.signal_name,
            "granularity": self.granularity,
            "confidence": self.confidence,
            "strength": round(self.strength, 1),
            "raw_strength": round(self.raw_strength, 4),
            "activation_summary": self.activation_summary,
            "matched_row_count": len(self.matched_rows),
            "timestamp": self.timestamp.isoformat(),
        }

    def to_model(self):
        from core.models.signals.activated_signal import ActivatedSignalModel
        return ActivatedSignalModel(
            signal_type_id=self.signal_type_id,
            signal_name=self.signal_name,
            granularity=self.granularity,
            confidence=self.confidence,
            strength=self.strength,
            raw_strength=self.raw_strength,
            activation_summary=self.activation_summary,
            matched_row_count=len(self.matched_rows),
            timestamp=self.timestamp,
        )


# ── Per-type aggregate ────────────────────────────────────────────

@dataclass
class TypeSignalResult:
    """Aggregated result for one signal type after evaluating all granularities."""

    signal_type_id: str
    signal_name: str
    has_data: bool
    row_count: int
    activated_signals: list[ActivatedSignal]
    max_strength: float = 0.0
    raw_max_strength: float = 0.0
    best_confidence: str = "Low"

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type_id": self.signal_type_id,
            "signal_name": self.signal_name,
            "has_data": self.has_data,
            "row_count": self.row_count,
            "max_strength": round(self.max_strength, 1),
            "raw_max_strength": round(self.raw_max_strength, 4),
            "best_confidence": self.best_confidence,
            "activated_granularities": [s.to_dict() for s in self.activated_signals],
        }


# ── Compound signal ───────────────────────────────────────────────

@dataclass
class CompoundSignalResult:
    """Result of evaluating a compound signal rule."""

    compound_id: str
    compound_name: str
    activated: bool
    confidence: str
    strength: float
    raw_strength: float = 0.0
    contributing_types: list[str] = field(default_factory=list)
    rationale: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "compound_id": self.compound_id,
            "compound_name": self.compound_name,
            "activated": self.activated,
            "confidence": self.confidence,
            "strength": round(self.strength, 1),
            "raw_strength": round(self.raw_strength, 4),
            "contributing_types": self.contributing_types,
            "rationale": self.rationale,
        }


# ── Top-level poll result ────────────────────────────────────────

@dataclass
class SignalBuilderResult:
    """Output of a single SignalBuilder poll cycle for one customer × service_tree_id."""

    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    type_results: list[TypeSignalResult] = field(default_factory=list)
    compound_results: list[CompoundSignalResult] = field(default_factory=list)
    action: str = "quiet"  # "invoke_group_chat" | "watchlist" | "quiet"
    customer_name: str = ""
    service_tree_id: str = ""
    service_name: str = ""
    xcv: str = ""
    start_time: str = ""
    end_time: str = ""
    owning_tenant_names: list[str] = field(default_factory=list)
    support_product_names: list[str] = field(default_factory=list)

    @property
    def all_activated_signals(self) -> list[ActivatedSignal]:
        """Flat list of all activated individual signals."""
        return [s for tr in self.type_results for s in tr.activated_signals]

    @property
    def activated_compounds(self) -> list[CompoundSignalResult]:
        return [c for c in self.compound_results if c.activated]

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "action": self.action,
            "customer_name": self.customer_name,
            "service_tree_id": self.service_tree_id,
            "service_name": self.service_name,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "owning_tenant_names": self.owning_tenant_names,
            "support_product_names": self.support_product_names,
            "type_results": [t.to_dict() for t in self.type_results],
            "compound_results": [c.to_dict() for c in self.compound_results],
        }
