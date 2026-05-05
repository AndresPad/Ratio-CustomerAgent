"""Config schema for monitoring_context.json (config/monitoring_context.json)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ServiceTreeIdConfig(BaseModel, extra="allow"):
    """A service-tree ID entry within a monitoring target."""

    id: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)
    support_product_names: list[str] = Field(default_factory=list)
    owning_tenant_names: list[str] = Field(default_factory=list)


class MonitoringTargetConfig(BaseModel, extra="allow"):
    """A single customer target in the ``targets`` array."""

    customer_name: str = Field(..., min_length=1)
    service_tree_ids: list[ServiceTreeIdConfig] = Field(default_factory=list)


class MonitoringContextFileConfig(BaseModel, extra="allow"):
    """Top-level schema for ``monitoring_context.json``."""

    poll_interval_minutes: int = Field(default=10, ge=1)
    max_concurrent_investigations: int = Field(default=5, ge=1)
    default_lookback_hours: int = Field(default=4, ge=1)
    targets: list[MonitoringTargetConfig] = Field(default_factory=list)
