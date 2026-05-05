"""Pydantic interface model for InvestigationContext."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class InvestigationContextModel(BaseModel):
    customer_name: str = ""
    service_tree_id: str = ""
    region: str = ""
    subscription_id: str = ""
    sli_id: str = ""
    incident_id: str = ""
    ticket_ids: list[str] = Field(default_factory=list)
    severity: str = ""
    start_time: str = ""
    end_time: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
