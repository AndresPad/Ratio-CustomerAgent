"""Config schema for the investigation phase pipeline.

Defines the ordered list of phases and their execution properties so the
investigation runner can be driven by config rather than hard-coded control
flow.  Each phase specifies its execution *mode* (standalone LLM call,
GroupChat loop, programmatic logic, or auto-complete sentinel) and the
agent(s) responsible for it.

The ``legal_transitions`` map is consumed by the state machine on
``Investigation`` to validate every phase change at runtime.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class PhaseConfig(BaseModel, extra="allow"):
    """Execution descriptor for a single investigation phase."""

    name: str = Field(..., min_length=1, description="Phase name matching InvestigationPhase enum value.")
    mode: Literal["standalone", "groupchat", "programmatic", "auto_complete"] = Field(
        ...,
        description=(
            "standalone  — single agent invoked outside GroupChat (retryable).\n"
            "groupchat   — agents collaborate inside a GroupChat loop.\n"
            "programmatic— deterministic logic, no LLM (e.g. hypothesis scoring).\n"
            "auto_complete — sentinel phase that resolves the investigation."
        ),
    )
    agent: str | None = Field(
        default=None,
        description="Agent name for standalone phases.",
    )
    agents: list[str] | None = Field(
        default=None,
        description="Participant agent names for groupchat phases.",
    )
    sub_phases: list[str] | None = Field(
        default=None,
        description="Internal sub-phases that cycle within a groupchat block (e.g. planning→collecting→reasoning).",
    )
    max_cycles: int = Field(
        default=1,
        ge=1,
        description="Max evidence cycles allowed within a groupchat phase block.",
    )
    retryable: bool = Field(
        default=False,
        description="Whether the phase supports retry on failure.",
    )


class PhasePipelineConfig(BaseModel, extra="allow"):
    """Full phase pipeline definition, nested under ``investigation_workflow``."""

    phases: list[PhaseConfig] = Field(
        ...,
        min_length=1,
        description="Ordered list of phases from initializing to complete.",
    )
    legal_transitions: dict[str, list[str]] = Field(
        default_factory=dict,
        description=(
            "Override map of legal phase transitions.  Keys are source phase names, "
            "values are lists of allowed target phase names.  If empty, the state "
            "machine uses its built-in defaults."
        ),
    )
