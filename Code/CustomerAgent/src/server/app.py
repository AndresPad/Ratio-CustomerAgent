"""
FastAPI server for MAF GroupChat Autonomous Agent.

Provides:
  - POST /chat         — run a user query through the GroupChat workflow
  - POST /chat/stream  — SSE endpoint streaming workflow events
  - GET  /health       — health check
  - A2A protocol routes:
    - GET  /a2a/agents                — list all agent cards
    - GET  /a2a/{agent}/agent-card    — agent discovery
    - POST /a2a/{agent}/              — invoke agent independently (A2A JSON-RPC)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys

# Path setup
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(_SRC_DIR, "..", ".env"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from core.agent_factory import create_agents, load_config
from core.orchestrator import build_group_chat_workflow, run_workflow_streaming
from core.models import SignalBuilderResultModel
from helper.auth import set_user_token
from helper.agent_logger import (
    AgentLogger,
    generate_xcv,
    get_current_xcv,
    set_current_xcv,
    set_current_tool_stage,
    subscribe_events,
    unsubscribe_events,
)
from a2a.registry import register_a2a_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="MAF GroupChat Autonomous Agent", version="1.0.0")

# ── CORS ─────────────────────────────────────────────────────────────────────
# The React dev server runs on http://127.0.0.1:3010 (Vite).
# In development Vite proxies /customer-agent-api → this server, so requests
# arrive same-origin; however when the UI is served from a different origin
# (e.g. the Docker image at port 3000, or a production nginx) we need CORS.
# Origins are explicit — never "*".
_CORS_ORIGINS_ENV = os.getenv("CUSTOMER_AGENT_CORS_ORIGINS", "")
_DEFAULT_ORIGINS = [
    "http://127.0.0.1:3010",
    "http://localhost:3010",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
]
_cors_origins = [o.strip() for o in _CORS_ORIGINS_ENV.split(",") if o.strip()] or _DEFAULT_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Lazy-initialized globals ─────────────────────────────────────────────────
_workflow = None
_agents = None
_config = None
_capture_middleware = None
_eval_middleware = None
_injection_middleware = None
_llm_logging_middleware = None
_agent_prompts: dict[str, str] = {}
_init_lock = asyncio.Lock()
_a2a_registered = False


async def _get_workflow():
    """Lazy-init: load config, create agents, build workflow, register A2A routes."""
    global _workflow, _agents, _config, _capture_middleware, _eval_middleware, _injection_middleware, _llm_logging_middleware, _agent_prompts, _a2a_registered
    async with _init_lock:
        if _workflow is not None:
            return _workflow
        logger.info("Initializing MAF GroupChat workflow...")
        _config = load_config()
        _agents, _capture_middleware, _eval_middleware, _injection_middleware, _llm_logging_middleware, _agent_prompts = await create_agents(_config)
        _workflow = build_group_chat_workflow(_agents, _config, _capture_middleware, _eval_middleware, _injection_middleware, _llm_logging_middleware, _agent_prompts)
        logger.info("Workflow initialized with %d agents", len(_agents))

        # Register A2A routes for each agent (once)
        if not _a2a_registered:
            register_a2a_routes(
                app,
                _agents,
                _config["agents"],
                _capture_middleware,
            )
            _a2a_registered = True
            logger.info("A2A routes registered")

        return _workflow


@app.on_event("startup")
async def _startup():
    """Eagerly initialize agents + A2A routes on server start."""
    await _get_workflow()


# ── Request/Response models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    user_token: str | None = None
    xcv: str | None = None


class ChatResponse(BaseModel):
    status: str
    agent_outputs: list[dict]
    conversation: list[dict]


# ── Endpoints ────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "service": "MAF GroupChat Agent"}


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest):
    """Run a query through the GroupChat workflow (non-streaming)."""
    if not req.query.strip():
        raise HTTPException(400, "Empty query")

    # ── Agent logging ──────────────────────────────────────────
    inv_id = req.xcv or generate_xcv()
    set_current_xcv(inv_id)
    tracker = AgentLogger.get_instance()
    tracker.log_request_start(inv_id, req.query)
    tracker.log_agents_loaded(inv_id, list(_agents.keys()) if _agents else [])

    # Set user token for MCP SQL passthrough
    if req.user_token:
        set_user_token(req.user_token)

    workflow = await _get_workflow()

    agent_outputs = []
    conversation = []

    async for event in run_workflow_streaming(workflow, req.query):
        etype = event.get("type")
        if etype == "agent_response":
            agent_outputs.append({
                "agent": event["agent"],
                "text": event["text"],
            })
        elif etype == "final":
            conversation = event.get("conversation", [])
            if not agent_outputs:
                agent_outputs = event.get("agent_outputs", [])

    tracker.log_request_end(inv_id, status="complete")

    return ChatResponse(
        status="complete",
        agent_outputs=agent_outputs,
        conversation=conversation,
    )


@app.post("/chat/stream")
async def chat_stream(request: Request):
    """Stream GroupChat workflow events via SSE."""
    body = await request.json()
    query = body.get("query", "").strip()
    user_token = body.get("user_token")
    xcv = body.get("xcv")

    if not query:
        raise HTTPException(400, "Empty query")

    # ── Agent logging ──────────────────────────────────────────
    inv_id = xcv or generate_xcv()
    set_current_xcv(inv_id)
    tracker = AgentLogger.get_instance()
    tracker.log_request_start(inv_id, query)
    tracker.log_agents_loaded(inv_id, list(_agents.keys()) if _agents else [])

    if user_token:
        set_user_token(user_token)

    workflow = await _get_workflow()

    async def event_generator():
        async for event in run_workflow_streaming(workflow, query):
            # Include xcv in every SSE event
            event["xcv"] = inv_id
            yield f"data: {json.dumps(event)}\n\n"
        tracker.log_request_end(inv_id, status="complete")
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Signal Builder + Investigation — full pipeline SSE endpoint ──────────────
#
# POST /api/run
#
# Triggers the complete signal-builder pipeline:
#   1. Evaluate signals (data collection + activation rules + compound logic)
#   2. For each actionable result, run the investigation GroupChat
#
# All AgentLogger events are streamed as SSE to the UI in real time via the
# subscriber queue mechanism (see agent_logger.subscribe_events).
#
# The UI receives fine-grained events like MCPCollectionCall, SignalTypeEvaluated,
# PhaseTransition, AgentResponse, ToolCall, etc. — enough to render a live
# investigation dashboard.

class RunRequest(BaseModel):
    """Request body for the /api/run endpoint.

    Accepts either explicit customer/service_tree_id overrides or falls back
    to monitoring_context.json targets (same as run_signal_builder.py CLI).
    """
    customer_name: str | None = None
    service_tree_id: str | None = None


@app.post("/api/run")
async def run_pipeline(req: RunRequest):
    """Run the full signal-builder → investigation pipeline, streaming all
    AgentLogger events as SSE.

    This is the primary endpoint for the CustomerAgentUI.  It mirrors what
    `python run_signal_builder.py` does but exposes every internal event
    (signal evaluation, MCP calls, agent invocations, phase transitions, etc.)
    as a real-time SSE stream so the UI can render live progress.

    Returns:
        StreamingResponse (text/event-stream) with JSON event frames.
        Final frame is "data: [DONE]\\n\\n".
    """
    # ── Lazy import to avoid circular deps at module load ────────────
    from core.services.signals.signal_builder import evaluate_signals
    from core.services.investigation.investigation_runner import run_investigation

    # ── Generate XCV and subscribe to AgentLogger events ─────────────
    xcv = generate_xcv()
    set_current_xcv(xcv)
    event_queue = subscribe_events(xcv)

    # ── Build monitoring context override if customer provided ───────
    monitoring_context = None
    if req.customer_name:
        target: dict = {"customer_name": req.customer_name}
        if req.service_tree_id:
            target["service_tree_ids"] = [{"id": req.service_tree_id, "name": ""}]
        monitoring_context = {"targets": [target]}

    async def pipeline_generator():
        """Run the pipeline in the background and yield AgentLogger events
        plus investigation-level events as SSE frames.

        The generator:
          1. Sends a 'pipeline_started' event immediately
          2. Kicks off evaluate_signals() as a background task
          3. Drains the AgentLogger subscriber queue in real time
          4. When signal evaluation finishes, streams investigation events
          5. Sends 'pipeline_complete' or 'pipeline_error' at the end
          6. Always cleans up the subscriber queue
        """
        try:
            # ── Ensure the pipeline XCV is set in this generator's context ──
            # Starlette may iterate the async generator in a context that
            # doesn't inherit the ContextVar set in run_pipeline().
            set_current_xcv(xcv)

            # ── Emit pipeline start ──────────────────────────────────
            yield f"data: {json.dumps({'type': 'pipeline_started', 'xcv': xcv})}\n\n"

            # ── Stage 1: Signal evaluation ───────────────────────────
            # Run in a background task so we can drain the event queue
            # concurrently as AgentLogger emits events.
            signal_results = []
            eval_done = asyncio.Event()
            eval_error: list[str] = []

            async def _evaluate():
                """Background task: run evaluate_signals and store results."""
                try:
                    # Explicitly propagate the parent XCV into this task.
                    # asyncio.create_task() copies the context, but in some
                    # Starlette/uvicorn scenarios the ContextVar may not
                    # survive into the new task.  Setting it here guarantees
                    # that every _emit() / get_current_xcv() call inside
                    # evaluate_signals() uses the same XCV the UI displays.
                    set_current_xcv(xcv)
                    set_current_tool_stage("signal_building")
                    nonlocal signal_results
                    signal_results = await evaluate_signals(
                        monitoring_context=monitoring_context
                    )
                except Exception as exc:
                    eval_error.append(str(exc))
                    logger.exception("Signal evaluation failed: %s", exc)
                finally:
                    set_current_tool_stage(None)
                    eval_done.set()

            eval_task = asyncio.create_task(_evaluate())

            # ── Drain AgentLogger events while signal eval runs ──────
            while not eval_done.is_set():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.2)
                    event["pipeline_xcv"] = xcv
                    yield f"data: {json.dumps(event, default=str)}\n\n"
                except asyncio.TimeoutError:
                    continue

            # Drain any remaining events after eval completes
            while not event_queue.empty():
                event = event_queue.get_nowait()
                event["pipeline_xcv"] = xcv
                yield f"data: {json.dumps(event, default=str)}\n\n"

            await eval_task  # Ensure task is fully done

            if eval_error:
                yield f"data: {json.dumps({'type': 'pipeline_error', 'xcv': xcv, 'error': eval_error[0]})}\n\n"
                yield "data: [DONE]\n\n"
                return

            # ── Emit signal evaluation summary ───────────────────────
            result_summaries = []
            for r in (signal_results or []):
                result_summaries.append({
                    "customer_name": r.customer_name,
                    "service_tree_id": r.service_tree_id,
                    "action": r.action,
                    "signal_count": len(r.all_activated_signals),
                    "compound_count": len(r.activated_compounds),
                })
            yield f"data: {json.dumps({'type': 'signal_evaluation_complete', 'xcv': xcv, 'results': result_summaries})}\n\n"

            # ── Stage 2: Run investigations for actionable results ───
            actionable = [r for r in (signal_results or []) if r.action == "invoke_group_chat"]

            if not actionable:
                yield f"data: {json.dumps({'type': 'pipeline_complete', 'xcv': xcv, 'message': 'No investigations triggered', 'investigation_count': 0})}\n\n"
                yield "data: [DONE]\n\n"
                return

            yield f"data: {json.dumps({'type': 'investigations_starting', 'xcv': xcv, 'count': len(actionable)})}\n\n"

            # Run investigations sequentially, all under the parent XCV
            for r in actionable:
                # Ensure the parent XCV is propagated into the investigation.
                # The signal builder result should already carry it, but
                # stamp it explicitly so investigation_runner never falls
                # back to generate_xcv().
                set_current_xcv(xcv)
                if not r.xcv:
                    r.xcv = xcv
                logger.info(
                    "Pre-investigation XCV check: pipeline_xcv=%s, result.xcv=%s, contextvar=%s",
                    xcv, r.xcv, get_current_xcv(),
                )
                # Investigation runner yields its own events; forward them
                try:
                    async for inv_event in run_investigation(r):
                        # Skip verbose chunk events from UI stream
                        if inv_event.get("type") == "investigation_agent_chunk":
                            continue
                        # Filter verbose events from UI stream (still logged to App Insights)
                        if inv_event.get("type") in ("investigation_stall_warning", "investigation_error"):
                            continue
                        inv_event["pipeline_xcv"] = xcv
                        yield f"data: {json.dumps(inv_event, default=str)}\n\n"

                        # Also drain any AgentLogger events that accumulated
                        while not event_queue.empty():
                            logger_event = event_queue.get_nowait()
                            # Filter verbose events from UI stream
                            if logger_event.get("EventName") in ("LLMCall", "InvestigationError"):
                                continue
                            logger_event["pipeline_xcv"] = xcv
                            yield f"data: {json.dumps(logger_event, default=str)}\n\n"
                except Exception as inv_exc:
                    logger.exception("Investigation generator raised for %s: %s", r.customer_name, inv_exc)
                    AgentLogger.get_instance().log_investigation_error(
                        xcv=xcv,
                        investigation_id=getattr(r, 'investigation_id', ''),
                        error=str(inv_exc),
                    )
                    yield f"data: {json.dumps({'type': 'investigation_error', 'xcv': xcv, 'error': str(inv_exc)}, default=str)}\n\n"

            # ── Final drain of any remaining logger events ───────────
            while not event_queue.empty():
                event = event_queue.get_nowait()
                if event.get("EventName") in ("LLMCall", "InvestigationError"):
                    continue
                event["pipeline_xcv"] = xcv
                yield f"data: {json.dumps(event, default=str)}\n\n"

            yield f"data: {json.dumps({'type': 'pipeline_complete', 'xcv': xcv, 'investigation_count': len(actionable)})}\n\n"
            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.exception("Pipeline generator failed: %s", exc)
            yield f"data: {json.dumps({'type': 'pipeline_error', 'xcv': xcv, 'error': str(exc)})}\n\n"
            yield "data: [DONE]\n\n"

        finally:
            # ── Flush pending telemetry so logs appear in App Insights ─
            try:
                AgentLogger.get_instance().flush()
            except Exception:
                logger.debug("Non-critical: flush failed", exc_info=True)
            # ── Always clean up the subscriber queue ─────────────────
            unsubscribe_events(xcv)

    return StreamingResponse(
        pipeline_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

#---- UI Specific ─────────────────────────────────────────────────
#
# The ratio_ui_web React app (Code/CustomerAgent/ratio_ui_web) expects a set
# of read-only "browse" endpoints plus an /api/investigate SSE endpoint that
# emits events in a normalized `InvestigationEvent` shape.  These endpoints
# are thin read-only wrappers around the config/knowledge directories and a
# translator on top of the existing /api/run pipeline.

from server.ui_api import register_ui_routes  # noqa: E402

register_ui_routes(app, run_pipeline)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8503"))
    logger.info("Starting MAF GroupChat server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
