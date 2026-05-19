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
import contextvars
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone

# Path setup
_SRC_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(_SRC_DIR, "..", ".env"))

# Apply framework compatibility patches early (before any agent code runs)
import core.compat  # noqa: F401, E402

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
    set_current_customer_name,
    set_current_service_tree_id,
    set_current_tool_stage,
    stamp_event,
    subscribe_events,
    unsubscribe_events,
)
from a2a.registry import register_a2a_routes
from server.config_api import router as config_router
from server.investigations_api import register_investigations_routes
from server.investigations_stream import register_investigations_stream_routes
from server.traces_api import register_traces_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI(title="MAF GroupChat Autonomous Agent", version="1.0.0")

# ── Config CRUD API ──────────────────────────────────────────────────────────
app.include_router(config_router)

# ── Investigations read API (powers ratio_ui_web Active / History pages) ─────
# Stream router MUST be registered before the main router. The main router
# has a parametric route `/{xcv}` that would otherwise shadow `/stream`
# (matching it as xcv="stream").
register_investigations_stream_routes(app)
register_investigations_routes(app)

# ── Traces replay API (powers the Neural Canvas live LA stream) ──────────────
# Same routes are also exposed by server/traces_server.py (a lighter-weight
# standalone process). Mounting them here means the main app at port 8503
# can serve them too, so Neural Canvas works under start_all.ps1.
register_traces_routes(app)

# ── CORS — allow external callers to reach API endpoints ─────────────────────
_allowed_origins = [
    o.strip()
    for o in os.getenv("ALLOWED_ORIGINS", "").split(",")
    if o.strip()
]
if _allowed_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

# ── Entra bearer-token auth (gated by CUSTOMERAGENT_AUTH_ENABLED) ────────────
# By default protects only /api/run/services. See helper/auth_middleware.py.
from helper.auth_middleware import wrap_app_if_enabled  # noqa: E402

wrap_app_if_enabled(app)

# ── Lazy-initialized globals ─────────────────────────────────────────────────
_workflow = None
_agents = None
_config = None
_capture_middleware = None
_eval_middleware = None
_injection_middleware = None
_tool_injection_middleware = None
_llm_logging_middleware = None
_agent_prompts: dict[str, str] = {}
_init_lock = asyncio.Lock()
_a2a_registered = False


async def _get_workflow():
    """Lazy-init: load config, create agents, build workflow, register A2A routes."""
    global _workflow, _agents, _config, _capture_middleware, _eval_middleware, _injection_middleware, _tool_injection_middleware, _llm_logging_middleware, _agent_prompts, _a2a_registered
    async with _init_lock:
        if _workflow is not None:
            return _workflow
        logger.info("Initializing MAF GroupChat workflow...")
        _config = load_config()
        _agents, _capture_middleware, _eval_middleware, _injection_middleware, _tool_injection_middleware, _llm_logging_middleware, _agent_prompts = await create_agents(_config)
        _workflow = build_group_chat_workflow(_agents, _config, _capture_middleware, _eval_middleware, _injection_middleware, _tool_injection_middleware, _llm_logging_middleware, _agent_prompts)
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
    """Eagerly initialize agents + A2A routes on server start.

    Also kick off the SignalBuilder self-trigger loop in the background when
    ``CUSTOMERAGENT_ENABLE_SELF_TRIGGER`` is truthy. The loop fires
    ``run_pipeline_once`` every ``poll_interval_minutes`` (set in
    monitoring_context.json — default 60 = hourly).
    """
    await _get_workflow()

    if os.getenv("CUSTOMERAGENT_ENABLE_SELF_TRIGGER", "").lower() in ("1", "true", "yes"):
        import asyncio as _asyncio
        from core.services.signals.signal_builder import run_signal_builder_loop

        async def _loop_task():
            try:
                await run_signal_builder_loop()
            except _asyncio.CancelledError:
                logger.info("SignalBuilder self-trigger loop cancelled on shutdown")
                raise
            except Exception:
                logger.exception("SignalBuilder self-trigger loop crashed")

        app.state._self_trigger_task = _asyncio.create_task(_loop_task())
        logger.info("SignalBuilder self-trigger loop scheduled (env CUSTOMERAGENT_ENABLE_SELF_TRIGGER=1)")


@app.on_event("shutdown")
async def _shutdown():
    task = getattr(app.state, "_self_trigger_task", None)
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except Exception:
            pass


# ── Request/Response models ──────────────────────────────────────────────────

class ChatRequest(BaseModel):
    query: str
    user_token: str | None = None
    xcv: str | None = None


class ChatResponse(BaseModel):
    status: str
    agent_outputs: list[dict]
    conversation: list[dict]


class PipelineServicesRequest(BaseModel):
    """Request body for /api/run/services."""
    customer_name: str
    start_time: str | None = None
    end_time: str | None = None


class ServiceXcvEntry(BaseModel):
    """One service with its generated XCV and the dispatch timestamp."""
    service_tree_id: str
    service_name: str
    xcv: str
    timestamp: str


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
    start_time: str | None = None
    end_time: str | None = None


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
    from core.services.signals.signal_builder import evaluate_signals_stream
    from core.services.investigation.investigation_runner import run_investigation

    # ── Generate XCV and subscribe to AgentLogger events ─────────────
    xcv = generate_xcv()
    set_current_xcv(xcv)
    event_queue = subscribe_events(xcv)

    # ── Build monitoring context override if customer provided ───────
    monitoring_context = None
    if req.customer_name:
        from core.services.signals.signal_builder import load_monitoring_context
        base_ctx = load_monitoring_context()

        # Look up matching targets from monitoring_context.json so we inherit
        # service_tree_ids, support_product_names, owning_tenant_names, etc.
        matched_targets = [
            t for t in base_ctx.get("targets", [])
            if t.get("customer_name", "").lower() == req.customer_name.lower()
            and t.get("enabled", True) is not False
        ]

        if req.service_tree_id:
            # Further narrow to the specific service tree if provided
            narrowed = []
            for t in matched_targets:
                entries = [
                    e for e in t.get("service_tree_ids", [])
                    if (
                        (isinstance(e, dict) and e.get("id") == req.service_tree_id)
                        or (isinstance(e, str) and e == req.service_tree_id)
                    )
                ]
                # Reject if the requested service is explicitly disabled
                for e in entries:
                    if isinstance(e, dict) and e.get("enabled", True) is False:
                        raise HTTPException(
                            status_code=409,
                            detail=(
                                f"service '{req.service_tree_id}' is disabled in "
                                "monitoring_context.json"
                            ),
                        )
                if entries:
                    narrowed.append({**t, "service_tree_ids": entries})
            matched_targets = narrowed or [
                {"customer_name": req.customer_name,
                 "service_tree_ids": [{"id": req.service_tree_id, "name": ""}]}
            ]

        if matched_targets:
            monitoring_context = {**base_ctx, "targets": matched_targets}
        else:
            # Customer not in monitoring_context.json — bare fallback
            target: dict = {"customer_name": req.customer_name}
            if req.service_tree_id:
                target["service_tree_ids"] = [{"id": req.service_tree_id, "name": ""}]
            monitoring_context = {"targets": [target]}

    # Inject explicit time window if provided
    if req.start_time or req.end_time:
        if monitoring_context is None:
            from core.services.signals.signal_builder import load_monitoring_context
            monitoring_context = load_monitoring_context()
        if req.start_time:
            monitoring_context["start_time"] = req.start_time
        if req.end_time:
            monitoring_context["end_time"] = req.end_time

    async def pipeline_generator():
        """Run the pipeline and yield AgentLogger events plus investigation
        events as SSE frames.

        The generator uses a streaming architecture:
          1. Signal evaluation runs all services in parallel
          2. As each service's signal eval completes, its investigation
             starts immediately — no waiting for other services
          3. AgentLogger events are drained continuously throughout
          4. Everything is multiplexed through a single output queue
        """
        def _stamp(event: dict) -> str:
            """Format *event* as an SSE data frame.

            If the event was already stamped (by stamp_event at its
            creation site), preserve the original seq/created_at.
            Otherwise stamp it now (for ad-hoc pipeline-level events).
            """
            if "seq" not in event:
                stamp_event(event)
            event["pipeline_xcv"] = xcv
            return f"data: {json.dumps(event, default=str)}\n\n"

        # Sentinel types for the output queue
        _SIGNAL_RESULT = "_signal_result"
        _INVESTIGATION_EVENT = "_inv_event"
        _LOGGER_EVENT = "_logger_event"
        _EVAL_DONE = "_eval_done"
        _EVAL_ERROR = "_eval_error"
        _INV_DONE = "_inv_done"
        _INV_ERROR = "_inv_error"

        output_queue: asyncio.Queue = asyncio.Queue()
        active_investigations = 0
        all_results: list = []
        eval_finished = False

        async def _signal_eval_producer():
            """Stream signal eval results and spawn investigations."""
            nonlocal active_investigations, eval_finished
            try:
                set_current_xcv(xcv)
                set_current_tool_stage("signal_building")
                async for result in evaluate_signals_stream(
                    monitoring_context=monitoring_context
                ):
                    all_results.append(result)
                    await output_queue.put((_SIGNAL_RESULT, result))

                    if result.action == "invoke_group_chat":
                        active_investigations += 1
                        asyncio.create_task(_run_one_investigation(result))
            except Exception as exc:
                logger.exception("Signal evaluation failed: %s", exc)
                await output_queue.put((_EVAL_ERROR, str(exc)))
                return
            finally:
                set_current_tool_stage(None)
                eval_finished = True
                await output_queue.put((_EVAL_DONE, None))

        async def _run_one_investigation(r):
            """Run a single investigation and push events to output_queue."""
            nonlocal active_investigations
            service_xcv = r.xcv or xcv
            try:
                set_current_xcv(service_xcv)
                set_current_service_tree_id(r.service_tree_id)
                logger.info(
                    "Pre-investigation XCV check: pipeline_xcv=%s, result.xcv=%s, contextvar=%s",
                    xcv, r.xcv, get_current_xcv(),
                )
                async for inv_event in run_investigation(r):
                    inv_event['service_xcv'] = service_xcv
                    inv_event['service_tree_id'] = r.service_tree_id
                    inv_event['service_name'] = r.service_name
                    stamp_event(inv_event)
                    await output_queue.put((_INVESTIGATION_EVENT, inv_event))
            except Exception as inv_exc:
                logger.exception("Investigation failed for %s: %s", r.customer_name, inv_exc)
                AgentLogger.get_instance().log_investigation_error(
                    xcv=service_xcv,
                    investigation_id=getattr(r, 'investigation_id', ''),
                    error=str(inv_exc),
                )
                await output_queue.put((_INV_ERROR, stamp_event({
                    'type': 'investigation_error',
                    'service_xcv': service_xcv,
                    'service_tree_id': r.service_tree_id,
                    'service_name': r.service_name,
                    'error': str(inv_exc),
                })))
            finally:
                active_investigations -= 1
                await output_queue.put((_INV_DONE, r))

        async def _logger_drain():
            """Continuously drain AgentLogger events until pipeline ends."""
            while True:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.15)
                    await output_queue.put((_LOGGER_EVENT, event))
                except asyncio.TimeoutError:
                    # Yield control; we'll be cancelled when pipeline ends
                    await asyncio.sleep(0)

        try:
            set_current_xcv(xcv)
            tracker = AgentLogger.get_instance()
            tracker.start_request_span(xcv, query=req.query if hasattr(req, 'query') else "")
            yield _stamp({'type': 'pipeline_started', 'xcv': xcv})
            yield _stamp({'type': 'investigation_milestone', 'text': 'Starting signal evaluation\u2026', 'icon': 'search'})

            # Start background producers
            eval_task = asyncio.create_task(_signal_eval_producer())
            drain_task = asyncio.create_task(_logger_drain())

            eval_error_msg = None
            eval_complete_emitted = False
            investigations_started = False
            investigation_count = 0

            # Main event loop — process multiplexed events
            while True:
                try:
                    msg_type, payload = await asyncio.wait_for(output_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    # Check if everything is done
                    if eval_finished and active_investigations == 0:
                        # Final drain
                        while not output_queue.empty():
                            msg_type, payload = output_queue.get_nowait()
                            # Process remaining events (see handlers below)
                            if msg_type == _LOGGER_EVENT:
                                if payload.get("type") in ("LLMCall", "InvestigationError", "OutputParsed"):
                                    continue
                                if payload.get("Agent") == "narrator" and payload.get("type") in (
                                    "AgentInvoked", "AgentResponse", "AgentPromptUsed",
                                ):
                                    continue
                                yield _stamp(payload)
                            elif msg_type == _INVESTIGATION_EVENT:
                                if payload.get("type") in (
                                    "investigation_agent_chunk",
                                    "investigation_stall_warning",
                                    "investigation_error",
                                ):
                                    continue
                                yield _stamp(payload)
                        break
                    continue

                if msg_type == _LOGGER_EVENT:
                    if payload.get("type") in ("LLMCall", "InvestigationError", "OutputParsed"):
                        continue
                    if payload.get("Agent") == "narrator" and payload.get("type") in (
                        "AgentInvoked", "AgentResponse", "AgentPromptUsed",
                    ):
                        continue
                    yield _stamp(payload)

                elif msg_type == _SIGNAL_RESULT:
                    result = payload
                    if result.action == "invoke_group_chat":
                        investigation_count += 1
                        if not investigations_started:
                            investigations_started = True
                            yield _stamp({'type': 'investigations_starting', 'xcv': xcv, 'count': '(streaming)'})

                elif msg_type == _INVESTIGATION_EVENT:
                    if payload.get("type") in (
                        "investigation_agent_chunk",
                        "investigation_stall_warning",
                        "investigation_error",
                    ):
                        continue
                    yield _stamp(payload)

                elif msg_type == _INV_ERROR:
                    # payload is already stamp_event()-ed with seq/created_at
                    payload.setdefault('xcv', payload.get('service_xcv', ''))
                    yield _stamp(payload)

                elif msg_type == _INV_DONE:
                    pass  # tracked via active_investigations counter

                elif msg_type == _EVAL_DONE:
                    # All signal evaluations complete — emit summary
                    if not eval_complete_emitted:
                        eval_complete_emitted = True
                        result_summaries = []
                        for r in all_results:
                            result_summaries.append({
                                "customer_name": r.customer_name,
                                "service_tree_id": r.service_tree_id,
                                "service_name": r.service_name,
                                "service_xcv": r.xcv,
                                "action": r.action,
                                "signal_count": len(r.all_activated_signals),
                                "compound_count": len(r.activated_compounds),
                            })
                        yield _stamp({'type': 'signal_evaluation_complete', 'xcv': xcv, 'results': result_summaries})
                        total_signals = sum(s.get('signal_count', 0) for s in result_summaries)
                        total_compounds = sum(s.get('compound_count', 0) for s in result_summaries)
                        yield _stamp({'type': 'investigation_milestone', 'text': f'Collected {total_signals} signals, {total_compounds} compound patterns', 'icon': 'check'})

                        if investigation_count == 0 and active_investigations == 0:
                            yield _stamp({'type': 'pipeline_complete', 'xcv': xcv, 'message': 'No investigations triggered', 'investigation_count': 0})
                            yield "data: [DONE]\n\n"
                            return

                elif msg_type == _EVAL_ERROR:
                    eval_error_msg = payload
                    yield _stamp({'type': 'pipeline_error', 'xcv': xcv, 'error': eval_error_msg})
                    yield "data: [DONE]\n\n"
                    return

            # Ensure eval task completed
            await eval_task

            # Cancel the logger drain
            drain_task.cancel()
            try:
                await drain_task
            except asyncio.CancelledError:
                pass

            # Final drain of logger events
            while not event_queue.empty():
                event = event_queue.get_nowait()
                if event.get("type") in ("LLMCall", "InvestigationError", "OutputParsed"):
                    continue
                if event.get("Agent") == "narrator" and event.get("type") in (
                    "AgentInvoked", "AgentResponse", "AgentPromptUsed",
                ):
                    continue
                yield _stamp(event)

            yield _stamp({'type': 'pipeline_complete', 'xcv': xcv, 'investigation_count': investigation_count})
            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.exception("Pipeline generator failed: %s", exc)
            yield _stamp({'type': 'pipeline_error', 'xcv': xcv, 'error': str(exc)})
            yield "data: [DONE]\n\n"

        finally:
            try:
                tracker.end_request_span(
                    status="error" if eval_error_msg else "complete",
                    error=eval_error_msg or "",
                )
                tracker.flush()
            except Exception:
                logger.debug("Non-critical: flush failed", exc_info=True)
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

# ── Pipeline XCV lookup ──────────────────────────────────────────────────────

@app.post(
    "/api/run/services",
    response_model=list[ServiceXcvEntry],
    summary="Run pipeline and return service XCV mapping",
    description=(
        "Runs the full signal-builder → investigation pipeline for the given "
        "customer and returns one {service_tree_id, service_name, xcv} entry "
        "per invoked service."
    ),
    tags=["Pipeline"],
)
async def run_pipeline_services(req: PipelineServicesRequest) -> list[ServiceXcvEntry]:
    """Invoke the same pipeline as the UI 'Run Pipeline' button and return
    the XCV allocated to each service.

    Returns the XCV list immediately — the pipeline runs in the background.
    """
    from core.services.signals.signal_builder import load_monitoring_context

    # ── Look up customer services from monitoring_context.json ────────
    base_ctx = load_monitoring_context()
    matched_targets = [
        t for t in base_ctx.get("targets", [])
        if t.get("customer_name", "").lower() == req.customer_name.strip().lower()
        and t.get("enabled", True) is not False
    ]
    if not matched_targets:
        raise HTTPException(
            status_code=404,
            detail=f"Customer '{req.customer_name}' not found in monitoring_context.json",
        )

    # ── Generate XCV per service immediately ─────────────────────────
    dispatch_timestamp = datetime.now(timezone.utc).isoformat()
    entries: list[ServiceXcvEntry] = []
    for target in matched_targets:
        for entry in target.get("service_tree_ids", []):
            if isinstance(entry, dict):
                if entry.get("enabled", True) is False:
                    continue
                sid = entry.get("id", "")
                sname = entry.get("name", "")
            else:
                sid = str(entry)
                sname = ""
            entries.append(ServiceXcvEntry(
                service_tree_id=sid,
                service_name=sname,
                xcv=generate_xcv(),
                timestamp=dispatch_timestamp,
            ))

    # ── Publish one Service Bus message per service (fire-and-log) ──
    # Failure to publish must NOT fail the API call — the pipeline still
    # runs and downstream consumers can recover from the OutcomeNotification.
    from core.services.publisher.outcome_publisher import publish_investigation_invocation
    for e in entries:
        try:
            await publish_investigation_invocation(
                customer_name=req.customer_name,
                xcv=e.xcv,
                timestamp=e.timestamp,
                service_tree_id=e.service_tree_id,
                service_name=e.service_name,
            )
        except Exception:
            logger.exception(
                "Failed to publish investigation-invocation for %s / %s (xcv=%s)",
                req.customer_name, e.service_tree_id, e.xcv,
            )

    # ── Fire-and-forget: kick off the full pipeline in background ────
    monitoring_context = {**base_ctx, "targets": matched_targets}
    if req.start_time:
        monitoring_context["start_time"] = req.start_time
    if req.end_time:
        monitoring_context["end_time"] = req.end_time

    # Build service_tree_id → xcv mapping so the pipeline uses the same XCVs
    xcv_map = {e.service_tree_id: e.xcv for e in entries}
    asyncio.create_task(_run_pipeline_bg(monitoring_context, xcv_map))

    return entries


async def _run_pipeline_bg(monitoring_context: dict, xcv_map: dict[str, str] | None = None) -> None:
    """Run the full signal-builder → investigation pipeline in the background."""
    from core.services.signals.signal_builder import evaluate_signals_stream

    pipeline_xcv = generate_xcv()
    set_current_xcv(pipeline_xcv)
    tracker = AgentLogger.get_instance()
    tracker.start_request_span(pipeline_xcv, query="background_pipeline")

    try:
        async for result in evaluate_signals_stream(
            monitoring_context=monitoring_context,
            xcv_map=xcv_map,
        ):
            if result.action == "invoke_group_chat":
                asyncio.create_task(_run_investigation_bg(result, pipeline_xcv))
    except Exception:
        logger.exception("Background pipeline failed")
        tracker.end_request_span(status="error", error="background pipeline failed")
        return
    tracker.end_request_span(status="complete")


async def _run_investigation_bg(result, pipeline_xcv: str) -> None:
    """Run a single investigation in the background (fire-and-collect)."""
    from core.services.investigation.investigation_runner import run_investigation

    service_xcv = result.xcv or pipeline_xcv
    try:
        set_current_xcv(service_xcv)
        set_current_service_tree_id(result.service_tree_id)
        set_current_customer_name(result.customer_name)
        async for _ in run_investigation(result):
            pass  # consume the async generator; events logged via AgentLogger
    except Exception:
        logger.exception(
            "Investigation failed for %s / %s",
            result.customer_name,
            result.service_tree_id,
        )


#---- UI Specific ─────────────────────────────────────────────────

# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8503"))
    logger.info("Starting MAF GroupChat server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
