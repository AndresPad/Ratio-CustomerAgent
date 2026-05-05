"""Config schema for action catalog (config/actions/action_catalog.json)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ActionPayloadTemplate(BaseModel, extra="allow"):
    """Payload template — keys vary by action type, so extra fields allowed."""


class ActionConfig(BaseModel):
    """Schema for a single action catalog entry."""
    id: str = Field(..., min_length=1, pattern=r"^ACT-")
    display_name: str = Field(..., min_length=1)
    type: str = Field(..., min_length=1)
    tier: str = Field(..., pattern=r"^(auto|gated)$")
    description: str = Field(..., min_length=1)
    applicable_hypotheses: list[str] = Field(default_factory=list)
    applicable_categories: list[str] = Field(default_factory=list)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    payload_template: ActionPayloadTemplate = Field(default_factory=ActionPayloadTemplate)


class ActionCatalogFileConfig(BaseModel):
    """Top-level schema for action_catalog.json."""
    actions: list[ActionConfig]
