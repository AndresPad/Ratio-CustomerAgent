"""Config schema for dependency service JSON files (config/dependency_services/*.json)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class SliSymptomConfig(BaseModel, extra="allow"):
    """A single SLI-symptom mapping within a dependency service."""

    sliId: str = Field(..., min_length=1)
    sli_category: str = Field(default="")
    symptoms: list[str] = Field(default_factory=list)


class DependencyServiceFileConfig(BaseModel, extra="allow"):
    """Top-level schema for a single dependency service JSON file."""

    name: str = Field(..., min_length=1)
    service_tree_id: str = Field(default="")
    category: str = Field(default="unknown")
    impact_description: str = Field(default="")
    sli_symptoms: list[SliSymptomConfig] = Field(default_factory=list)
