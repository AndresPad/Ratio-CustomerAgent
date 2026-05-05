"""Config schema for hypothesis template JSON files (config/hypotheses/*.json)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class HypothesisTemplateConfig(BaseModel):
    """Schema for a single hypothesis template entry."""
    id: str = Field(..., min_length=1, pattern=r"^HYP-")
    name: str = Field(..., min_length=1)
    statement: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    scope: str | None = None
    expected_symptoms: list[str] = Field(default_factory=list)
    min_symptoms_for_match: int = Field(default=2, ge=1)
    required_symptoms: list[str] = Field(default_factory=list)
    excluding_symptoms: list[str] = Field(default_factory=list)
    evidence_needed: list[str] = Field(default_factory=list)
    supporting_signals: str = ""
    # sli_hypotheses use relevant_sli_categories, dependency use relevant_categories
    relevant_sli_categories: list[str] = Field(default_factory=list)
    relevant_categories: list[str] = Field(default_factory=list)
    status: str | None = None


class HypothesisFileConfig(BaseModel):
    """Top-level schema for a hypothesis config file."""
    hypotheses: list[HypothesisTemplateConfig]
