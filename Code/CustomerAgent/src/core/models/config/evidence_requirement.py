"""Config schema for evidence requirements (config/evidence/evidence_requirements.json)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class EvidenceRequirementConfig(BaseModel):
    """Schema for a single evidence requirement entry."""
    id: str = Field(..., min_length=1, pattern=r"^ER-")
    description: str = Field(..., min_length=1)
    tool_name: str = Field(..., min_length=1)
    parameters: dict[str, str] = Field(default_factory=dict)
    category_tag: str = ""
    output_fields: list[str] = Field(default_factory=list)
    note: str | None = None


class EvidenceFileConfig(BaseModel):
    """Top-level schema for evidence_requirements.json."""
    evidence_requirements: list[EvidenceRequirementConfig]
