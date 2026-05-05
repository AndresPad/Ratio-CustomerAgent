"""Pydantic models for config-file schema validation.

These models validate the JSON config files at load time so that structural
errors (missing fields, wrong types) surface immediately with clear messages
instead of causing runtime crashes deep in the pipeline.
"""
from core.models.config.symptom_template import SymptomFieldsConfig, SymptomTemplateConfig, SymptomFileConfig  # noqa: F401
from core.models.config.hypothesis_template import HypothesisTemplateConfig, HypothesisFileConfig  # noqa: F401
from core.models.config.evidence_requirement import EvidenceRequirementConfig, EvidenceFileConfig  # noqa: F401
from core.models.config.action_catalog import ActionPayloadTemplate, ActionConfig, ActionCatalogFileConfig  # noqa: F401
from core.models.config.agents import AgentConfig, AgentsFileConfig, WorkflowConfig, InvestigationWorkflowConfig  # noqa: F401
from core.models.config.phase_pipeline import PhaseConfig, PhasePipelineConfig  # noqa: F401
from core.models.config.signal_template import SignalTemplateFileConfig, SignalTypeConfig, CompoundSignalConfig  # noqa: F401
from core.models.config.monitoring_context import MonitoringContextFileConfig, MonitoringTargetConfig  # noqa: F401
from core.models.config.dependency_service import DependencyServiceFileConfig, SliSymptomConfig  # noqa: F401

__all__ = [
    "SymptomFieldsConfig",
    "SymptomTemplateConfig",
    "SymptomFileConfig",
    "HypothesisTemplateConfig",
    "HypothesisFileConfig",
    "EvidenceRequirementConfig",
    "EvidenceFileConfig",
    "ActionPayloadTemplate",
    "ActionConfig",
    "ActionCatalogFileConfig",
    "AgentConfig",
    "AgentsFileConfig",
    "WorkflowConfig",
    "InvestigationWorkflowConfig",
    "PhaseConfig",
    "PhasePipelineConfig",
    "SignalTemplateFileConfig",
    "SignalTypeConfig",
    "CompoundSignalConfig",
    "MonitoringContextFileConfig",
    "MonitoringTargetConfig",
    "DependencyServiceFileConfig",
    "SliSymptomConfig",
]
