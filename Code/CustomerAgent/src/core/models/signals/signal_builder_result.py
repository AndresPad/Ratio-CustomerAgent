"""Pydantic interface model for SignalBuilderResult."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from core.models.signals.compound_signal_result import CompoundSignalResultModel
from core.models.signals.type_signal_result import TypeSignalResultModel


class SignalBuilderResultModel(BaseModel):
    timestamp: datetime
    action: str = "quiet"
    customer_name: str = ""
    service_tree_id: str = ""
    service_name: str = ""
    xcv: str = ""
    start_time: str = ""
    end_time: str = ""
    type_results: list[TypeSignalResultModel] = Field(default_factory=list)
    compound_results: list[CompoundSignalResultModel] = Field(default_factory=list)
