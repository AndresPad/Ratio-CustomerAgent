"""Display-name overrides for service names returned to the UI.

Some raw service names emitted by Log Analytics (`AppTraces.Properties.ServiceName`)
have long, internal-sounding labels (e.g., "SQL Connectivity") that are not the
shorter, customer-facing names we want surfaced in the React UI. This module
centralises the mapping so every API endpoint that returns a service name can
apply the same override.

Keep this map small and authoritative. Synonyms / aliases do NOT belong here —
this is the canonical "what should the UI show?" source. Synonyms used during
entity normalization live in
`Code/RATIO_MCP/src/datasets/ServiceNameSynonyms.json`.
"""
from __future__ import annotations

# Map of raw upstream value -> display value to surface in the UI.
SERVICE_NAME_DISPLAY_OVERRIDES: dict[str, str] = {
    "SQL Connectivity": "SQL",
}


def display_service_name(name: str | None) -> str:
    """Return the UI display name for a raw upstream service name.

    Unknown names are returned unchanged (after stripping). Empty / None
    inputs return "".
    """
    if not name:
        return ""
    stripped = name.strip()
    return SERVICE_NAME_DISPLAY_OVERRIDES.get(stripped, stripped)
