"""Pydantic model for the data fetcher manifest — output of Stage 1."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


class ManifestEntry(BaseModel):
    """Metadata for one signal type's fetched data."""

    id: str
    file: str
    row_count: int = 0
    collection_duration_ms: float = 0.0
    feeds_granularities: dict[str, list[str]] = Field(default_factory=dict)


class DataFetchManifest(BaseModel):
    """Top-level manifest written by data_fetcher after Stage 1."""

    xcv: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    customer_name: str = ""
    service_tree_id: str = ""
    service_name: str = ""
    start_time: str = ""
    end_time: str = ""
    output_dir: str = ""
    signal_types: list[ManifestEntry] = Field(default_factory=list)
    dependency_services: list[str] = Field(default_factory=list)
    customer_regions: list[str] = Field(default_factory=list)
