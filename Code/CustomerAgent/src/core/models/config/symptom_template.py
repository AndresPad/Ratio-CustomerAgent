"""Config schema for symptom template JSON files (config/symptoms/*.json)."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SymptomFieldsConfig(BaseModel):
    """Fields section of a symptom template."""
    from_data: list[str] = Field(default_factory=list)
    llm_derived: list[str] = Field(default_factory=list)


class SuppressWhenConfig(BaseModel):
    """Suppression rule: skip this symptom when a higher-priority granularity activated."""
    granularity_activated: str = Field(
        ..., min_length=1,
        description="The granularity name that, when activated, suppresses this symptom.",
    )
    matching_fields: list[str] = Field(
        ..., min_length=1,
        description="Fields that must match between the suppressed and suppressing signals.",
    )


class SymptomTemplateConfig(BaseModel):
    """Schema for a single symptom template entry."""
    id: str = Field(..., min_length=1, pattern=r"^SYM-")
    name: str = Field(..., min_length=1)
    signal_sources: list[str] = Field(..., min_length=1)
    granularity: str | None = Field(
        default=None,
        description="Signal granularity this symptom maps to.",
    )
    weight: int = Field(default=1, ge=0)
    template: str = Field(..., min_length=1)
    extracted_when: str = Field(..., min_length=1)
    fields: SymptomFieldsConfig = Field(default_factory=SymptomFieldsConfig)
    filters: dict[str, Any] = Field(default_factory=dict)
    suppress_when: SuppressWhenConfig | None = Field(
        default=None,
        description="If set, this symptom is suppressed when the specified granularity also activated with matching field values.",
    )


class SymptomFileConfig(BaseModel):
    """Top-level schema for a symptom config file."""
    templates: list[SymptomTemplateConfig]
