"""
Config CRUD API — read/write/delete configuration entities.

Mounted on the main FastAPI app at /api/config/*.
All changes are persisted to disk in the original JSON files.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["config"])

# ── Paths ────────────────────────────────────────────────────────────────────
_SRC_DIR = Path(__file__).resolve().parent.parent          # …/src
_CONFIG_DIR = _SRC_DIR / "config"

# ── Entity registry ─────────────────────────────────────────────────────────
# Maps entity key → (file_path_or_dir, root_key, is_multi_file)
_ENTITY_MAP: dict[str, dict[str, Any]] = {
    "signals": {
        "path": _CONFIG_DIR / "signals" / "signal_template.json",
        "root_key": "signal_types",
        "id_field": "id",
        "multi_file": False,
    },
    "symptoms": {
        "path": _CONFIG_DIR / "symptoms",
        "root_key": "templates",
        "id_field": "id",
        "multi_file": True,
    },
    "hypotheses": {
        "path": _CONFIG_DIR / "hypotheses",
        "root_key": "scenarios",
        "id_field": None,  # special handling — nested structure
        "multi_file": True,
        "exclude_files": ["scoring_config.json"],
    },
    "evidence": {
        "path": _CONFIG_DIR / "evidence" / "evidence_requirements.json",
        "root_key": "evidence_requirements",
        "id_field": "id",
        "multi_file": False,
    },
    "actions": {
        "path": _CONFIG_DIR / "actions" / "action_catalog.json",
        "root_key": "actions",
        "id_field": "id",
        "multi_file": False,
    },
    "dependencies": {
        "path": _CONFIG_DIR / "dependency_services",
        "root_key": None,
        "id_field": None,
        "multi_file": True,
        "exclude_files": ["dependency_mappings.json"],
    },
    "dependency_mappings": {
        "path": _CONFIG_DIR / "dependency_services" / "dependency_mappings.json",
        "root_key": "mappings",
        "id_field": None,
        "multi_file": False,
    },
    "monitoring_context": {
        "path": _CONFIG_DIR / "monitoring_context.json",
        "root_key": None,
        "id_field": None,
        "multi_file": False,
    },
    "scoring_config": {
        "path": _CONFIG_DIR / "hypotheses" / "scoring_config.json",
        "root_key": None,
        "id_field": None,
        "multi_file": False,
    },
}


# ── Helpers ──────────────────────────────────────────────────────────────────

def _read_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info("Config written: %s", path)


def _list_json_files(directory: Path, exclude: list[str] | None = None) -> list[Path]:
    exclude = exclude or []
    return sorted(
        p for p in directory.glob("*.json")
        if p.name not in exclude and not p.name.startswith("_")
    )


def _collect_all_ids(entity: str) -> set[str]:
    """Collect all IDs for a given entity type (for reference validation)."""
    items = _get_entity_items(entity)
    ids = set()
    for item in items:
        if isinstance(item, dict) and "id" in item:
            ids.add(item["id"])
    return ids


def _get_entity_items(entity: str) -> list[dict]:
    """Get all items for a flat entity (signals, symptoms, evidence, actions)."""
    meta = _ENTITY_MAP.get(entity)
    if not meta:
        return []
    if meta["multi_file"]:
        items = []
        exclude = meta.get("exclude_files", [])
        for fp in _list_json_files(meta["path"], exclude):
            data = _read_json(fp)
            root_key = meta["root_key"]
            if root_key and root_key in data:
                for item in data[root_key]:
                    item["_source_file"] = fp.name
                    items.append(item)
            elif not root_key:
                data["_source_file"] = fp.name
                items.append(data)
        return items
    else:
        data = _read_json(meta["path"])
        root_key = meta["root_key"]
        if root_key:
            return data.get(root_key, [])
        return [data]


def _find_references(entity: str, item_id: str) -> list[dict]:
    """Find all cross-references to an item for delete validation.

    Returns a list of {entity, id, field} dicts describing who references it.
    """
    refs = []

    if entity == "signals":
        # Symptoms reference signals via signal_sources
        for sym in _get_entity_items("symptoms"):
            if item_id in sym.get("signal_sources", []):
                refs.append({"entity": "symptoms", "id": sym.get("id", "?"), "field": "signal_sources"})

    elif entity == "symptoms":
        # Hypotheses reference symptoms via symptom_triggers
        for hyp_file in _list_json_files(_CONFIG_DIR / "hypotheses", ["scoring_config.json"]):
            data = _read_json(hyp_file)
            for scenario in data.get("scenarios", []):
                if item_id in scenario.get("trigger_signals", []):
                    refs.append({"entity": "hypotheses", "id": scenario.get("id", "?"), "field": "trigger_signals"})
                for hyp in scenario.get("hypotheses", []):
                    if item_id in hyp.get("symptom_triggers", []):
                        refs.append({"entity": "hypotheses", "id": hyp.get("id", "?"), "field": "symptom_triggers"})

    elif entity == "evidence":
        # Hypotheses reference evidence via evidence_required
        for hyp_file in _list_json_files(_CONFIG_DIR / "hypotheses", ["scoring_config.json"]):
            data = _read_json(hyp_file)
            for scenario in data.get("scenarios", []):
                for hyp in scenario.get("hypotheses", []):
                    if item_id in hyp.get("evidence_required", []):
                        refs.append({"entity": "hypotheses", "id": hyp.get("id", "?"), "field": "evidence_required"})

    elif entity.startswith("hypothes"):
        # Actions reference hypotheses via applicable_hypotheses
        actions_data = _read_json(_CONFIG_DIR / "actions" / "action_catalog.json")
        for act in actions_data.get("actions", []):
            if item_id in act.get("applicable_hypotheses", []):
                refs.append({"entity": "actions", "id": act.get("id", "?"), "field": "applicable_hypotheses"})

    return refs


# ── Request/Response models ──────────────────────────────────────────────────

class ConfigItemRequest(BaseModel):
    item: dict


class DeleteResponse(BaseModel):
    deleted: bool
    id: str


class ReferencesResponse(BaseModel):
    references: list[dict]
    can_delete: bool


# ── Routes ───────────────────────────────────────────────────────────────────

# NOTE: Specific routes MUST come before /{entity} catch-all routes.

@router.get("/entities")
async def list_entities():
    """List all available config entities with counts."""
    result = []
    for key, meta in _ENTITY_MAP.items():
        try:
            items = _get_entity_items(key)
            count = len(items)
        except Exception:
            count = 0
        result.append({"key": key, "count": count})
    return result


# ── Mapping endpoints (must precede /{entity} catch-all) ─────────────────────

@router.get("/mappings/signal-symptom")
async def get_signal_symptom_mappings():
    """Get signal → symptom mappings."""
    signals = _get_entity_items("signals")
    symptoms = _get_entity_items("symptoms")
    mappings = []
    for sig in signals:
        sig_id = sig.get("id", "")
        linked = [s for s in symptoms if sig_id in s.get("signal_sources", [])]
        mappings.append({
            "signal_id": sig_id,
            "signal_name": sig.get("name", ""),
            "symptoms": [{"id": s.get("id"), "name": s.get("name")} for s in linked],
        })
    return {"mappings": mappings}


@router.get("/mappings/symptom-hypothesis")
async def get_symptom_hypothesis_mappings():
    """Get symptom → hypothesis mappings."""
    symptoms = _get_entity_items("symptoms")
    mappings = []
    for sym in symptoms:
        sym_id = sym.get("id", "")
        linked_hyps = []
        for fp in _list_json_files(_CONFIG_DIR / "hypotheses", ["scoring_config.json"]):
            data = _read_json(fp)
            for scenario in data.get("scenarios", []):
                for hyp in scenario.get("hypotheses", []):
                    if sym_id in hyp.get("symptom_triggers", []):
                        linked_hyps.append({"id": hyp["id"], "name": hyp.get("name", "")})
        mappings.append({
            "symptom_id": sym_id,
            "symptom_name": sym.get("name", ""),
            "hypotheses": linked_hyps,
        })
    return {"mappings": mappings}


@router.get("/mappings/evidence-hypothesis")
async def get_evidence_hypothesis_mappings():
    """Get evidence → hypothesis mappings."""
    evidence = _get_entity_items("evidence")
    mappings = []
    for ev in evidence:
        ev_id = ev.get("id", "")
        linked_hyps = []
        for fp in _list_json_files(_CONFIG_DIR / "hypotheses", ["scoring_config.json"]):
            data = _read_json(fp)
            for scenario in data.get("scenarios", []):
                for hyp in scenario.get("hypotheses", []):
                    if ev_id in hyp.get("evidence_required", []):
                        linked_hyps.append({"id": hyp["id"], "name": hyp.get("name", "")})
        mappings.append({
            "evidence_id": ev_id,
            "evidence_name": ev.get("description", "")[:60],
            "hypotheses": linked_hyps,
        })
    return {"mappings": mappings}


@router.get("/mappings/action-hypothesis")
async def get_action_hypothesis_mappings():
    """Get action → hypothesis mappings."""
    actions = _get_entity_items("actions")
    mappings = []
    for act in actions:
        act_id = act.get("id", "")
        mappings.append({
            "action_id": act_id,
            "action_name": act.get("display_name", ""),
            "hypotheses": [{"id": h} for h in act.get("applicable_hypotheses", [])],
        })
    return {"mappings": mappings}


@router.put("/hypotheses/file/{filename}")
async def update_hypothesis_file(filename: str, req: ConfigItemRequest):
    """Save an entire hypothesis file by filename."""
    fp = _CONFIG_DIR / "hypotheses" / filename
    if not fp.exists():
        raise HTTPException(404, f"File {filename} not found")
    _write_json(fp, req.item)
    return {"updated": True, "file": filename}


# ── Catch-all entity routes ──────────────────────────────────────────────────

@router.get("/{entity}")
async def get_entity(entity: str):
    """Get all items for a config entity."""
    if entity not in _ENTITY_MAP:
        raise HTTPException(404, f"Unknown entity: {entity}")
    meta = _ENTITY_MAP[entity]

    # Special handling for full-file entities (monitoring_context, scoring_config)
    if meta["root_key"] is None and not meta["multi_file"]:
        data = _read_json(meta["path"])
        return {"entity": entity, "data": data, "items": []}

    # Special handling for hypotheses (nested scenarios+hypotheses)
    if entity == "hypotheses":
        all_data = []
        exclude = meta.get("exclude_files", [])
        for fp in _list_json_files(meta["path"], exclude):
            data = _read_json(fp)
            all_data.append({"file": fp.name, "data": data})
        return {"entity": entity, "files": all_data}

    # Special handling for dependency services
    if entity == "dependencies":
        items = []
        exclude = meta.get("exclude_files", [])
        for fp in _list_json_files(meta["path"], exclude):
            data = _read_json(fp)
            data["_source_file"] = fp.name
            items.append(data)
        return {"entity": entity, "items": items}

    if entity == "dependency_mappings":
        data = _read_json(meta["path"])
        return {"entity": entity, "data": data}

    items = _get_entity_items(entity)
    return {"entity": entity, "items": items}


@router.get("/{entity}/{item_id}")
async def get_item(entity: str, item_id: str):
    """Get a single config item by ID."""
    if entity not in _ENTITY_MAP:
        raise HTTPException(404, f"Unknown entity: {entity}")
    items = _get_entity_items(entity)
    id_field = _ENTITY_MAP[entity].get("id_field", "id")
    if not id_field:
        raise HTTPException(400, f"Entity {entity} does not support item-level access")
    for item in items:
        if item.get(id_field) == item_id:
            return item
    raise HTTPException(404, f"Item {item_id} not found in {entity}")


@router.get("/{entity}/{item_id}/references")
async def get_references(entity: str, item_id: str):
    """Check what references an item before deletion."""
    refs = _find_references(entity, item_id)
    return ReferencesResponse(references=refs, can_delete=len(refs) == 0)


@router.put("/{entity}/{item_id}")
async def update_item(entity: str, item_id: str, req: ConfigItemRequest):
    """Update an existing config item (persisted to disk)."""
    if entity not in _ENTITY_MAP:
        raise HTTPException(404, f"Unknown entity: {entity}")
    meta = _ENTITY_MAP[entity]

    # Full-file write (JSON editor for scoring_config, monitoring_context, etc.)
    if item_id == "_all":
        _write_json(meta["path"], req.item)
        return {"updated": True, "entity": entity}

    # Full-file entities (monitoring_context, scoring_config, dependency_mappings)
    if meta["root_key"] is None and not meta["multi_file"]:
        _write_json(meta["path"], req.item)
        return {"updated": True, "entity": entity}

    # Dependency_mappings — write full mappings object
    if entity == "dependency_mappings":
        data = _read_json(meta["path"])
        data["mappings"] = req.item.get("mappings", req.item)
        _write_json(meta["path"], data)
        return {"updated": True, "entity": entity}

    id_field = meta.get("id_field", "id")

    if meta["multi_file"]:
        # Find which file contains this item
        exclude = meta.get("exclude_files", [])
        for fp in _list_json_files(meta["path"], exclude):
            data = _read_json(fp)
            root_key = meta["root_key"]
            items_list = data.get(root_key, []) if root_key else [data]
            for i, item in enumerate(items_list):
                if item.get(id_field) == item_id:
                    # Remove internal metadata before saving
                    new_item = {k: v for k, v in req.item.items() if not k.startswith("_")}
                    if root_key:
                        data[root_key][i] = new_item
                    else:
                        data = new_item
                    _write_json(fp, data)
                    return {"updated": True, "id": item_id}
        raise HTTPException(404, f"Item {item_id} not found")
    else:
        data = _read_json(meta["path"])
        items_list = data.get(meta["root_key"], [])
        for i, item in enumerate(items_list):
            if item.get(id_field) == item_id:
                new_item = {k: v for k, v in req.item.items() if not k.startswith("_")}
                items_list[i] = new_item
                data[meta["root_key"]] = items_list
                _write_json(meta["path"], data)
                return {"updated": True, "id": item_id}
        raise HTTPException(404, f"Item {item_id} not found")


@router.post("/{entity}")
async def create_item(entity: str, req: ConfigItemRequest):
    """Create a new config item (persisted to disk)."""
    if entity not in _ENTITY_MAP:
        raise HTTPException(404, f"Unknown entity: {entity}")
    meta = _ENTITY_MAP[entity]
    id_field = meta.get("id_field", "id")
    new_item = {k: v for k, v in req.item.items() if not k.startswith("_")}

    # Dependencies — create new file
    if entity == "dependencies":
        filename = new_item.get("_filename") or new_item.get("name", "new").lower().replace(" ", "_") + ".json"
        new_item.pop("_filename", None)
        fp = meta["path"] / filename
        if fp.exists():
            raise HTTPException(409, f"File {filename} already exists")
        _write_json(fp, new_item)
        return {"created": True, "file": filename}

    if meta["multi_file"]:
        # Add to the first file or specified _source_file
        source_file = req.item.get("_source_file")
        exclude = meta.get("exclude_files", [])
        files = _list_json_files(meta["path"], exclude)
        if source_file:
            fp = meta["path"] / source_file
        elif files:
            fp = files[0]
        else:
            raise HTTPException(400, "No config files found")

        if not fp.exists():
            raise HTTPException(404, f"Source file {fp.name} not found")
        data = _read_json(fp)
        root_key = meta["root_key"]
        if root_key:
            # Check duplicate ID
            if id_field and any(item.get(id_field) == new_item.get(id_field) for item in data.get(root_key, [])):
                raise HTTPException(409, f"Item {new_item.get(id_field)} already exists")
            data.setdefault(root_key, []).append(new_item)
        _write_json(fp, data)
        return {"created": True, "id": new_item.get(id_field, "")}
    else:
        data = _read_json(meta["path"])
        root_key = meta["root_key"]
        if root_key:
            if id_field and any(item.get(id_field) == new_item.get(id_field) for item in data.get(root_key, [])):
                raise HTTPException(409, f"Item {new_item.get(id_field)} already exists")
            data.setdefault(root_key, []).append(new_item)
        _write_json(meta["path"], data)
        return {"created": True, "id": new_item.get(id_field, "")}


@router.delete("/{entity}/{item_id}")
async def delete_item(entity: str, item_id: str, force: bool = False):
    """Delete a config item. Checks references first unless force=True."""
    if entity not in _ENTITY_MAP:
        raise HTTPException(404, f"Unknown entity: {entity}")

    # Reference check
    if not force:
        refs = _find_references(entity, item_id)
        if refs:
            raise HTTPException(
                409,
                detail={
                    "message": f"Cannot delete {item_id}: referenced by {len(refs)} item(s)",
                    "references": refs,
                },
            )

    meta = _ENTITY_MAP[entity]
    id_field = meta.get("id_field", "id")

    if meta["multi_file"]:
        exclude = meta.get("exclude_files", [])
        for fp in _list_json_files(meta["path"], exclude):
            data = _read_json(fp)
            root_key = meta["root_key"]
            if root_key:
                original_len = len(data.get(root_key, []))
                data[root_key] = [
                    item for item in data.get(root_key, [])
                    if item.get(id_field) != item_id
                ]
                if len(data[root_key]) < original_len:
                    _write_json(fp, data)
                    return DeleteResponse(deleted=True, id=item_id)
        raise HTTPException(404, f"Item {item_id} not found")
    else:
        data = _read_json(meta["path"])
        root_key = meta["root_key"]
        if root_key:
            original_len = len(data.get(root_key, []))
            data[root_key] = [
                item for item in data.get(root_key, [])
                if item.get(id_field) != item_id
            ]
            if len(data[root_key]) < original_len:
                _write_json(meta["path"], data)
                return DeleteResponse(deleted=True, id=item_id)
        raise HTTPException(404, f"Item {item_id} not found")
