"""Config schema for signal_template.json (config/signals/signal_template.json)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Collection tool spec inside a signal type ──────────────────────

class CollectionToolConfig(BaseModel, extra="allow"):
    """A tool reference within a signal type's ``collection_tools``."""

    tool_name: str = Field(..., min_length=1)
    parameters_from_context: dict[str, str] = Field(default_factory=dict)
    feeds_granularities: list[str] = Field(default_factory=list)


# ── Granularity definition ─────────────────────────────────────────

class GranularityConfig(BaseModel, extra="allow"):
    """One granularity level inside a signal type."""

    granularity: str = Field(..., min_length=1)
    group_by: list[str] = Field(default_factory=list)
    aggregates: dict[str, str] = Field(default_factory=dict)
    confidence: str = Field(default="Medium")
    rationale: str = Field(default="")
    inputs: list[str] = Field(default_factory=list)
    activation: str = Field(..., min_length=1)
    activation_rules: list[dict[str, Any]] = Field(default_factory=list)
    strength_formula: str = Field(..., min_length=1)
    max_raw_strength: float = Field(default=30.0, ge=0.0)


# ── Signal type ────────────────────────────────────────────────────

class SignalTypeConfig(BaseModel, extra="allow"):
    """Schema for a single entry in the ``signal_types`` array."""

    id: str = Field(..., min_length=1, pattern=r"^SIG-TYPE-")
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    data_source: str = Field(default="")
    data_fields: list[str] = Field(default_factory=list)
    collection_tools: list[CollectionToolConfig] = Field(default_factory=list)
    granularities: list[GranularityConfig] = Field(default_factory=list)
    # SIG-TYPE-4 specific keys
    collection_strategy: str | None = None
    dependency_mappings: str | None = None
    region_tool: dict[str, Any] | None = None
    dependency_tool: dict[str, Any] | None = None


# ── Compound signal ────────────────────────────────────────────────

class CompoundSignalConfig(BaseModel, extra="allow"):
    """Schema for a single entry in the ``compound_signals`` array."""

    id: str = Field(..., min_length=1, pattern=r"^COMPOUND-")
    name: str = Field(..., min_length=1)
    description: str = Field(default="")
    required_signal_types: list[str] = Field(..., min_length=1)
    activation_rules: list[dict[str, Any]] = Field(default_factory=list)
    confidence: str = Field(default="Medium")
    strength_formula: str = Field(..., min_length=1)
    correlation_multiplier: float = Field(default=1.0, ge=0.0)
    rationale: str = Field(default="")


# ── Scoring ────────────────────────────────────────────────────────

class ScoringConfig(BaseModel, extra="allow"):
    """Scoring section of the signal template."""

    scale_max: int = Field(default=5, ge=1)
    min_activated_floor: float = Field(default=0.5, ge=0.0)
    labels: dict[str, str] = Field(default_factory=dict)


# ── Decision rule ──────────────────────────────────────────────────

class DecisionRuleConfig(BaseModel, extra="allow"):
    """A single entry in the ``decision_rules`` array."""

    condition: dict[str, Any]
    action: str = Field(..., min_length=1)
    description: str = Field(default="")


# ── Top-level file schema ─────────────────────────────────────────

class SignalTemplateFileConfig(BaseModel, extra="allow"):
    """Top-level schema for ``signal_template.json``."""

    signal_types: list[SignalTypeConfig]
    compound_signals: list[CompoundSignalConfig] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    max_concurrent_mcp_calls: int = Field(default=5, ge=1)
    decision_rules: list[DecisionRuleConfig] = Field(default_factory=list)
    confidence_levels: list[str] = Field(default_factory=list)
    confidence_principle: str = Field(default="")
    adaptive_thresholds_note: str = Field(default="")
    customer_context_note: str = Field(default="")
