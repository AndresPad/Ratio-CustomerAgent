"""UI-facing read-only endpoints + SSE translator for ratio_ui_web.

These endpoints back the React pages under `Code/CustomerAgent/ratio_ui_web`
(Scenarios, Agents, Config, Data, Knowledge, Active Investigation).

They are thin wrappers over:
  - the JSON config files in `src/config/*`
  - the knowledge directory `src/knowledge/`
  - the existing `/api/run` signal-builder → investigation pipeline

The translator (`/api/investigate`) converts the rich event stream produced by
`/api/run` (AgentLogger events + internal investigation events) into the
normalized `InvestigationEvent` shape expected by the UI:

    {
      "event_type": "investigation_started" | "phase_change" | "agent_turn"
                  | "evidence_collected" | "investigation_complete"
                  | "error" | "done",
      "agent_name": str,
      "phase": str,
      "content": str,
      "data": dict,
      "timestamp": ISO-8601 str
    }
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterator, Callable

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ── Paths ────────────────────────────────────────────────────────────────────
_SRC_DIR = Path(__file__).resolve().parent.parent      # Code/CustomerAgent/src
_CONFIG_DIR = _SRC_DIR / "config"
_KNOWLEDGE_DIR = _SRC_DIR / "knowledge"
_DATAFILES_DIR = _SRC_DIR / "knowledge"                 # same as knowledge for now


# ── Helpers ──────────────────────────────────────────────────────────────────
def _load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_all_in(dir_path: Path) -> list[dict]:
    """Load every *.json in a directory and flatten list-valued top-level keys."""
    items: list[dict] = []
    if not dir_path.exists():
        return items
    for p in sorted(dir_path.glob("*.json")):
        try:
            data = _load_json(p)
        except Exception as exc:
            logger.warning("Failed to parse %s: %s", p, exc)
            continue
        if isinstance(data, list):
            items.extend(data)
        elif isinstance(data, dict):
            # Flatten first list-valued key (templates / hypotheses / actions / evidence)
            for v in data.values():
                if isinstance(v, list):
                    items.extend(v)
                    break
            else:
                items.append(data)
    return items


# ── Scenarios (synthesized for the demo UI) ──────────────────────────────────
#
# The production pipeline evaluates ALL monitoring_context targets every run.
# For the demo UI we expose a curated list of "scenarios" that each map to a
# (customer, service_tree_id) pair.  Picking a scenario simply scopes the
# run to that customer/service.  Extra synthesised edge-case scenarios give
# the reviewer something to click that isn't duplicated data.

def _load_monitoring_scenarios() -> list[dict]:
    """Derive demo scenarios from monitoring_context.json."""
    scenarios: list[dict] = []
    ctx_path = _CONFIG_DIR / "monitoring_context.json"
    if not ctx_path.exists():
        return scenarios
    ctx = _load_json(ctx_path)
    for t_idx, target in enumerate(ctx.get("targets", []) or []):
        cust = target.get("customer_name", "")
        for s_idx, svc in enumerate(target.get("service_tree_ids", []) or []):
            scenarios.append({
                "id": f"SC-LIVE-{t_idx+1}-{s_idx+1}",
                "name": f"Live health sweep — {cust} / {svc.get('name') or svc.get('id','')}",
                "description": (
                    f"Poll live telemetry for {cust} on service "
                    f"'{svc.get('name','')}' (service_tree_id={svc.get('id','')}). "
                    "Runs the full signal-builder → triage → hypothesis "
                    "→ evidence → action pipeline."
                ),
                "category": "live",
                "signal_count": 4,
                "expected_outcome": "Signals evaluated and investigation triggered if actionable",
                "expected_root_cause": "Determined by hypothesis reasoner",
                "customer_name": cust,
                "service_tree_id": svc.get("id", ""),
            })
    return scenarios


_SYNTHETIC_SCENARIOS: list[dict] = [
    {
        "id": "SC-DEMO-SLI",
        "name": "SLI Breach — single subscription/region",
        "description": (
            "Single-scope SLI breach for a BlackRock scale-set. "
            "Demonstrates triage → HYP-SLI-001 scoring → SLI evidence collection → verdict."
        ),
        "category": "singular",
        "signal_count": 1,
        "expected_outcome": "HYP-SLI-001 (SLI breach due to workload spike) CONFIRMED or CONTRIBUTING",
        "expected_root_cause": "Availability SLI dropped below SLO due to backend saturation",
    },
    {
        "id": "SC-DEMO-COMPOUND",
        "name": "Compound — SLI breach + active outage",
        "description": (
            "SLI breach co-occurring with an active IcM incident. Triggers the compound "
            "signal and HYP-SLI-003 (Outage caused SLI breach) with cross-source evidence."
        ),
        "category": "composite",
        "signal_count": 2,
        "expected_outcome": "Compound signal activates, HYP-SLI-003 CONFIRMED",
        "expected_root_cause": "Active platform outage causing downstream SLI degradation",
    },
    {
        "id": "SC-DEMO-DEPENDENCY",
        "name": "Cascading — dependency degradation",
        "description": (
            "Upstream dependency service (e.g. xStore / Azure Allocator) degrading while "
            "the primary service's own SLIs remain normal. Exercises SIG-TYPE-4 and "
            "HYP-DEP-* hypotheses."
        ),
        "category": "edge_case",
        "signal_count": 1,
        "expected_outcome": "HYP-DEP-001 (Dependency degradation) CONFIRMED or CONTRIBUTING",
        "expected_root_cause": "Upstream dependency failure propagating to customer workload",
    },
]


def list_scenarios_data() -> list[dict]:
    live = _load_monitoring_scenarios()
    return live + _SYNTHETIC_SCENARIOS


def get_scenario_data(scenario_id: str) -> dict | None:
    for s in list_scenarios_data():
        if s["id"] == scenario_id:
            return s
    return None


# ── Agents ───────────────────────────────────────────────────────────────────
def list_agents_data() -> list[dict]:
    agents_path = _CONFIG_DIR / "agents" / "agents_config.json"
    if not agents_path.exists():
        return []
    cfg = _load_json(agents_path)
    out = []
    for a in cfg.get("agents", []) or []:
        out.append({
            "name": a.get("name", ""),
            "display_name": a.get("name", "").replace("_", " ").title(),
            "description": a.get("description", ""),
            "role": a.get("description", "").split(".")[0][:120],
            "objective": a.get("description", ""),
            "model": a.get("model", ""),
            "temperature": a.get("temperature", 1.0),
            "technology_tags": ["Microsoft Agent Framework", "Azure OpenAI"],
            "tool_names": list(a.get("mcp_tools", []) or []),
        })
    return out


# ── Config tabs ──────────────────────────────────────────────────────────────
_CONFIG_TABS = {
    "signals":           ("signals",      "signal_types"),
    "symptoms":          ("symptoms",     "symptom_templates"),
    "hypotheses":        ("hypotheses",   "hypotheses"),
    "evidence":          ("evidence",     "evidence_requirements"),
    "actions":           ("actions",      "actions"),
    "dependency-services": ("dependency_services", "dependencies"),
}


def get_config_tab_data(tab: str) -> dict[str, list[dict]]:
    if tab not in _CONFIG_TABS:
        return {tab: []}
    folder, key = _CONFIG_TABS[tab]
    items = _load_all_in(_CONFIG_DIR / folder)
    return {key: items}


# ── Data files ───────────────────────────────────────────────────────────────
def list_datafiles_data() -> list[dict]:
    files: list[dict] = []
    for pattern in ("*.json", "*.csv", "*.jsonl"):
        for p in _DATAFILES_DIR.glob(pattern):
            try:
                size_kb = f"{p.stat().st_size / 1024:.1f} KB"
            except OSError:
                size_kb = "?"
            files.append({
                "name": p.name,
                "path": p.name,
                "size": size_kb,
                "record_count": None,
                "columns": [],
            })
    return files


def get_datafile_data(path: str) -> dict:
    p = _DATAFILES_DIR / Path(path).name  # defuse traversal
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"File not found: {path}")
    if p.suffix == ".json":
        try:
            data = _load_json(p)
            records = data if isinstance(data, list) else [data]
        except Exception as exc:
            raise HTTPException(500, f"Failed to parse: {exc}")
    else:
        text = p.read_text(encoding="utf-8", errors="replace")
        records = [{"line": i + 1, "content": line} for i, line in enumerate(text.splitlines())]
    return {"name": p.name, "records": records}


# ── Knowledge ────────────────────────────────────────────────────────────────
def list_knowledge_data() -> list[dict]:
    out: list[dict] = []
    if not _KNOWLEDGE_DIR.exists():
        return out
    for p in sorted(_KNOWLEDGE_DIR.glob("*.md")):
        try:
            preview = p.read_text(encoding="utf-8", errors="replace")[:300]
            size = f"{p.stat().st_size / 1024:.1f} KB"
        except OSError:
            preview = ""
            size = "?"
        out.append({
            "name": p.name,
            "title": p.stem.replace("_", " ").title(),
            "preview": preview,
            "size": size,
        })
    return out


def get_knowledge_content_data(name: str) -> dict:
    p = _KNOWLEDGE_DIR / Path(name).name
    if not p.exists():
        raise HTTPException(404, f"Knowledge file not found: {name}")
    return {
        "title": p.stem.replace("_", " ").title(),
        "content": p.read_text(encoding="utf-8", errors="replace"),
    }


# ── /api/investigate event translator ────────────────────────────────────────
#
# Maps raw pipeline events (emitted by /api/run) to the InvestigationEvent shape
# the React UI consumes.  The UI expects one of:
#     investigation_started | phase_change | agent_turn | evidence_collected
#     | investigation_complete | error | done
#
# We keep all original fields under `data` so the UI (or a future custom view)
# can still render rich details.

_AGENT_EVENT_NAMES = {"AgentResponse", "SpeakerSelected", "AgentInvoked"}
_PHASE_EVENT_NAMES = {"PhaseTransition", "InvestigationCreated", "WorkflowStarted"}
_EVIDENCE_EVENT_NAMES = {"ToolCall", "MCPCollectionCall", "EvidenceCycle"}
_COMPLETE_EVENT_NAMES = {"InvestigationComplete", "pipeline_complete"}
_ERROR_EVENT_NAMES = {"InvestigationError", "pipeline_error"}
_START_EVENT_NAMES = {"pipeline_started", "SignalEvaluationStart"}


def _shorten(text: str, n: int = 600) -> str:
    if not isinstance(text, str):
        text = str(text)
    return text if len(text) <= n else text[:n] + "…"


def _translate(raw: dict) -> dict:
    """Translate one raw event to InvestigationEvent shape."""
    event_name = raw.get("type") or raw.get("EventName") or ""
    agent_name = (
        raw.get("Agent")
        or raw.get("agent")
        or raw.get("NextSpeaker")
        or raw.get("agent_name")
        or ""
    )
    phase = (
        raw.get("ToPhase")
        or raw.get("phase")
        or raw.get("Phase")
        or ""
    )

    # Classify
    if event_name in _START_EVENT_NAMES or event_name == "investigation_started":
        etype = "investigation_started"
    elif event_name in _COMPLETE_EVENT_NAMES:
        etype = "investigation_complete"
    elif event_name in _ERROR_EVENT_NAMES or event_name == "investigation_error":
        etype = "error"
    elif event_name in _PHASE_EVENT_NAMES or event_name == "investigation_agent_start":
        etype = "phase_change"
    elif event_name in _EVIDENCE_EVENT_NAMES:
        etype = "evidence_collected"
    elif event_name in _AGENT_EVENT_NAMES or event_name == "investigation_agent_response":
        etype = "agent_turn"
    elif event_name in {"SignalTypeEvaluated", "CompoundEvaluated", "SignalDecision",
                        "HypothesisScoring", "HypothesisSelected", "HypothesisTransition",
                        "signal_evaluation_complete", "hypothesis_evaluation_started"}:
        etype = "phase_change"
    else:
        etype = "agent_turn"

    content = (
        raw.get("text")
        or raw.get("content")
        or raw.get("ResponseText")
        or raw.get("Message")
        or raw.get("message")
        or raw.get("error")
        or ""
    )
    if not content:
        # Compose a compact summary for classification events
        if event_name == "SignalTypeEvaluated":
            content = (
                f"{raw.get('SignalName','?')} · {raw.get('ActivatedCount',0)} activated · "
                f"strength {raw.get('MaxStrength', 0)} ({raw.get('BestConfidence','-')})"
            )
        elif event_name == "CompoundEvaluated":
            content = (
                f"{raw.get('CompoundName','?')} · activated={raw.get('Activated', False)} · "
                f"strength {raw.get('Strength', 0)}"
            )
        elif event_name == "SignalDecision":
            content = f"Decision: {raw.get('Action','?')} ({raw.get('SignalCount',0)} signals, {raw.get('CompoundCount',0)} compounds)"
        elif event_name == "HypothesisScoring":
            content = (
                f"Top: {raw.get('TopHypothesisId','-')} @ {raw.get('TopScore',0)} "
                f"(from {raw.get('OutputHypothesisCount',0)} qualifying hypotheses)"
            )
        elif event_name == "HypothesisSelected":
            content = (
                f"Evaluating {raw.get('HypothesisId','?')} (rank {raw.get('Rank','?')}/{raw.get('TotalHypotheses','?')}) — "
                f"{_shorten(str(raw.get('Statement','')), 180)}"
            )
        elif event_name == "PhaseTransition":
            content = f"{raw.get('FromPhase','?')} → {raw.get('ToPhase','?')}"
        elif event_name == "MCPCollectionCall":
            content = (
                f"{raw.get('Tool','?')}({_shorten(str(raw.get('Parameters','')), 120)}) "
                f"→ {raw.get('RowCount',0)} rows in {raw.get('DurationMs',0)}ms"
            )
        elif event_name == "ToolCall":
            content = f"{raw.get('Tool','?')} invoked"
        else:
            content = event_name

    return {
        "event_type": etype,
        "agent_name": agent_name or event_name,
        "phase": phase,
        "content": _shorten(str(content), 1200),
        "data": raw,
        "timestamp": _iso_now(),
    }


# ── FastAPI registration ─────────────────────────────────────────────────────
class InvestigateRequest(BaseModel):
    scenario_id: str


def register_ui_routes(app: FastAPI, run_pipeline_fn: Callable) -> None:
    """Attach all UI-specific read-only + streaming endpoints to the app.

    `run_pipeline_fn` is the existing `/api/run` handler coroutine from
    `app.py`; we call it to produce the raw SSE stream and then re-wrap it
    with UI-shaped events.
    """

    @app.get("/api/scenarios")
    async def _list_scenarios():
        return {"scenarios": list_scenarios_data()}

    @app.get("/api/scenarios/{scenario_id}")
    async def _get_scenario(scenario_id: str):
        s = get_scenario_data(scenario_id)
        if not s:
            raise HTTPException(404, f"Unknown scenario: {scenario_id}")
        return s

    @app.get("/api/agents")
    async def _list_agents():
        return {"agents": list_agents_data()}

    @app.get("/api/investigations")
    async def _list_investigations():
        # Past investigations persistence is out of scope for the UI server.
        # Return an empty list so the History page renders gracefully.
        return {"investigations": []}

    @app.get("/api/investigations/{inv_id}")
    async def _get_investigation(inv_id: str):
        raise HTTPException(404, f"Investigation {inv_id} not found (no persistence layer configured)")

    @app.get("/api/config/{tab}")
    async def _get_config(tab: str):
        return get_config_tab_data(tab)

    @app.get("/api/datafiles")
    async def _list_datafiles():
        return {"files": list_datafiles_data()}

    @app.get("/api/datafiles/{path:path}")
    async def _get_datafile(path: str):
        return get_datafile_data(path)

    @app.get("/api/knowledge")
    async def _list_knowledge():
        return {"files": list_knowledge_data()}

    @app.get("/api/knowledge/{name}")
    async def _get_knowledge(name: str):
        return get_knowledge_content_data(name)

    # ── /api/investigate — UI-shaped SSE stream ─────────────────────────
    @app.post("/api/investigate")
    async def _investigate(req: InvestigateRequest):
        """Start an investigation for a scenario and stream normalized events."""
        scenario = get_scenario_data(req.scenario_id)
        if not scenario:
            raise HTTPException(404, f"Unknown scenario: {req.scenario_id}")

        # Build the underlying RunRequest from scenario metadata.
        from server.app import RunRequest  # late import to avoid circular deps
        underlying = RunRequest(
            customer_name=scenario.get("customer_name") or None,
            service_tree_id=scenario.get("service_tree_id") or None,
        )

        # Call run_pipeline, which returns a StreamingResponse.
        raw_response: StreamingResponse = await run_pipeline_fn(underlying)
        raw_iter: AsyncIterator[bytes] = raw_response.body_iterator  # type: ignore[attr-defined]

        async def translated() -> AsyncIterator[bytes]:
            # Immediate investigation_started event so the UI can reset
            yield (
                "data: " + json.dumps({
                    "event_type": "investigation_started",
                    "agent_name": "signal_builder",
                    "phase": "initializing",
                    "content": f"Starting scenario {scenario['id']} — {scenario['name']}",
                    "data": {"scenario_id": scenario["id"], "scenario": scenario},
                    "timestamp": _iso_now(),
                }) + "\n\n"
            ).encode()

            buffer = ""
            async for chunk in raw_iter:
                if isinstance(chunk, (bytes, bytearray)):
                    buffer += chunk.decode("utf-8", errors="replace")
                else:
                    buffer += str(chunk)

                # Split on SSE frame separator
                while "\n\n" in buffer:
                    frame, buffer = buffer.split("\n\n", 1)
                    for line in frame.split("\n"):
                        if not line.startswith("data: "):
                            continue
                        payload = line[6:]
                        if payload.strip() == "[DONE]":
                            yield (
                                "data: " + json.dumps({
                                    "event_type": "done",
                                    "agent_name": "",
                                    "phase": "complete",
                                    "content": "Investigation stream complete",
                                    "data": {},
                                    "timestamp": _iso_now(),
                                }) + "\n\n"
                            ).encode()
                            return
                        try:
                            raw = json.loads(payload)
                        except Exception:
                            continue
                        translated_evt = _translate(raw)
                        yield ("data: " + json.dumps(translated_evt, default=str) + "\n\n").encode()

        return StreamingResponse(
            translated(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    logger.info("Registered UI routes (/api/scenarios, /api/agents, /api/config/*, /api/datafiles, /api/knowledge, /api/investigate)")
