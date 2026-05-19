"""Action catalog loader.

The catalog is the master list of action recipes the composer maps each
recommended action to. Authoritative source is
``src/config/action_catalog.json`` (PR-reviewed, in repo). Loaded once at
process start and cached.
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# config/action_catalog.json sits two levels up from this file:
#   src/helper/action_catalog.py  -> src/config/action_catalog.json
_DEFAULT_CATALOG_PATH = Path(__file__).resolve().parent.parent / "config" / "action_catalog.json"


def _catalog_path() -> Path:
    """Resolve the catalog path. Allow override via env for tests."""
    override = os.getenv("INTERPRETER_ACTION_CATALOG_PATH")
    if override:
        return Path(override)
    return _DEFAULT_CATALOG_PATH


@lru_cache(maxsize=1)
def load_action_catalog() -> dict[str, Any]:
    """Load and cache the action catalog JSON.

    Returns the full catalog document (with ``version`` and ``actions``).
    Raises ``FileNotFoundError`` if the catalog is missing — this is a
    deployment error and the service should fail fast.
    """
    path = _catalog_path()
    if not path.exists():
        raise FileNotFoundError(f"Action catalog not found at {path}")
    with path.open("r", encoding="utf-8") as f:
        catalog = json.load(f)
    actions = catalog.get("actions", [])
    if not isinstance(actions, list) or not actions:
        raise ValueError(f"Action catalog at {path} has no actions")
    logger.info(
        "Loaded action catalog v%s with %d action(s) from %s",
        catalog.get("version", "?"), len(actions), path,
    )
    return catalog


def get_catalog_action_ids() -> set[str]:
    """Return the set of valid action_id values from the catalog."""
    return {a["action_id"] for a in load_action_catalog().get("actions", []) if a.get("action_id")}
