"""Config schema for agents_config.json (config/agents/agents_config.json)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .phase_pipeline import PhasePipelineConfig


# ── Agent-level schema ─────────────────────────────────────────────

class AgentConfig(BaseModel, extra="allow"):
    """Schema for a single agent entry in the ``agents`` array."""

    name: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    prompt_file: str = Field(..., min_length=1)
    model: str = Field(default="gpt-4o")
    temperature: float = Field(default=1.0, ge=0.0, le=2.0)
    max_completion_tokens: int | None = None
    tool_mode: Literal["none", "filtered", "all", "agent_tools", "sandbox", "fetch_tools"] = "none"
    mcp_tools: list[str] = Field(default_factory=list)
    sub_agents: list[str] = Field(default_factory=list)
    knowledge: list[str] = Field(default_factory=list)
    middleware: list[str] = Field(default_factory=list)
    evaluate: bool = False
    prompt_injection: bool = False
    context_folding: bool = True
    log_input: bool = True
    log_output: bool = True
    llm_logging: bool = True


# ── Workflow schemas ───────────────────────────────────────────────

class RetryPolicyConfig(BaseModel, extra="allow"):
    """Retry policy for a single agent or the ``default`` key."""

    max_retries: int = Field(default=0, ge=0)
    backoff: Literal["none", "linear", "exponential"] = "none"
    backoff_base_seconds: int = Field(default=0, ge=0)


class CycleDetectionConfig(BaseModel, extra="allow"):
    """Cycle-detection parameters inside ``investigation_workflow``."""

    history_window: int = Field(default=6, ge=1)
    max_repeated_pattern: int = Field(default=3, ge=1)
    max_identical_messages: int = Field(default=2, ge=1)


class AgentTimeoutsConfig(BaseModel, extra="allow"):
    """Per-agent timeout overrides (seconds)."""

    default: int = Field(default=120, ge=1)

    class Config:
        extra = "allow"


class WorkflowConfig(BaseModel, extra="allow"):
    """Schema for the ``workflow`` top-level key."""

    type: str = Field(default="group_chat")
    orchestrator_agent: str = Field(..., min_length=1)
    participants: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=15, ge=1)
    intermediate_outputs: bool = True
    termination_keyword: str | None = None


class InvestigationWorkflowConfig(BaseModel, extra="allow"):
    """Schema for the ``investigation_workflow`` top-level key."""

    type: str = Field(default="group_chat")
    orchestrator_agent: str = Field(..., min_length=1)
    participants: list[str] = Field(default_factory=list)
    action_agent: str | None = None
    max_turns: int = Field(default=40, ge=1)
    max_evidence_cycles: int = Field(default=2, ge=1)
    intermediate_outputs: bool = True
    termination_signal: str | None = None
    narrator_enabled: bool = False
    agent_timeout_seconds: dict[str, int] = Field(default_factory=dict)
    retry_policy: dict[str, RetryPolicyConfig] = Field(default_factory=dict)
    phase_transitions: dict[str, str] = Field(default_factory=dict)
    agent_roles: dict[str, str] = Field(default_factory=dict)
    cycle_detection: CycleDetectionConfig = Field(default_factory=CycleDetectionConfig)
    max_eval_hypotheses: int = Field(default=4, ge=1)
    max_rows_per_grain: int = Field(default=5, ge=1)
    phase_pipeline: PhasePipelineConfig | None = Field(
        default=None,
        description="Config-driven phase pipeline defining execution order and modes.",
    )


# ── Top-level file schema ─────────────────────────────────────────

class AgentsFileConfig(BaseModel, extra="allow"):
    """Top-level schema for ``agents_config.json``."""

    agents: list[AgentConfig]
    workflow: WorkflowConfig
    investigation_workflow: InvestigationWorkflowConfig | None = None
