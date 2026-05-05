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
from pydantic import BaseModel, Field

from server.display_names import display_service_name

from core.agent_factory import create_agents, load_config
from core.orchestrator import build_group_chat_workflow, run_workflow_streaming
from core.models import SignalBuilderResultModel
from helper.auth import set_user_token
from helper.agent_logger import (
    AgentLogger,
    generate_xcv,
    get_current_xcv,
    set_current_xcv,
    set_current_service_tree_id,
    set_current_tool_stage,
    stamp_event,
    subscribe_events,
    unsubscribe_events,
)
from a2a.registry import register_a2a_routes
from server.config_api import router as config_router

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

# ── Config CRUD API ──────────────────────────────────────────────────────────
app.include_router(config_router)

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
        (
            _agents,
            _capture_middleware,
            _eval_middleware,
            _injection_middleware,
            _tool_injection_middleware,
            _llm_logging_middleware,
            _agent_prompts,
        ) = await create_agents(_config)
        _workflow = build_group_chat_workflow(
            _agents,
            _config,
            _capture_middleware,
            _eval_middleware,
            _injection_middleware,
            _tool_injection_middleware,
            _llm_logging_middleware,
            _agent_prompts,
        )
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

    Failures here are logged but do not abort startup so that endpoints
    not requiring the LLM workflow (e.g. /health, /api/traces/*,
    /api/run/services) remain available for local development without
    an Azure OpenAI endpoint configured.
    """
    try:
        await _get_workflow()
    except Exception as exc:  # pragma: no cover - startup degradation
        logger.warning(
            "Workflow init failed (LLM endpoints will be unavailable): %s", exc
        )


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
    start_time: str | None = None
    end_time: str | None = None


class RunServicesRequest(BaseModel):
    """Lookup request for replayable customer services."""

    customer_name: str = Field(..., min_length=1, max_length=256)
    start_time: str = Field(..., min_length=1, max_length=64)
    end_time: str = Field(..., min_length=1, max_length=64)


class RunServiceOption(BaseModel):
    """Replayable service entry for UI service dropdowns."""

    service_tree_id: str
    service_name: str
    xcv: str


def _parse_utc_iso(value: str, field_name: str) -> datetime:
    """Parse ISO-8601 input into a UTC datetime."""

    normalized = value.strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise HTTPException(400, f"Invalid {field_name}; expected ISO-8601 timestamp") from exc

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@app.post("/api/run/services", response_model=list[RunServiceOption])
async def run_services(req: RunServicesRequest):
    """Return recent services with their latest XCV for a customer.

    This is used by the UI replay flow so users can select service names
    instead of manually entering correlation ids.
    """

    start_dt = _parse_utc_iso(req.start_time, "start_time")
    end_dt = _parse_utc_iso(req.end_time, "end_time")
    if end_dt <= start_dt:
        raise HTTPException(400, "end_time must be after start_time")

    workspace_id = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    if not workspace_id:
        # Demo fallback so the UI service dropdown is populated locally
        # without a Log Analytics workspace. Real XCV replay will still
        # fail until LOG_ANALYTICS_WORKSPACE_ID is configured.
        logger.info("/api/run/services: workspace unconfigured, returning demo data")
        demo = [
            ("00000000-0000-0000-0000-000000000001", "Aladdin Trading",     "demo-xcv-aladdin-001"),
            ("00000000-0000-0000-0000-000000000002", "Aladdin Risk",        "demo-xcv-risk-002"),
            ("00000000-0000-0000-0000-000000000003", "Aladdin Compliance",  "demo-xcv-compliance-003"),
            ("00000000-0000-0000-0000-000000000004", "Aladdin Reporting",   "demo-xcv-reporting-004"),
        ]
        return [
            RunServiceOption(service_tree_id=stid, service_name=name, xcv=xcv)
            for (stid, name, xcv) in demo
        ]

    customer = req.customer_name.replace("'", "''")
    start_iso = start_dt.isoformat()
    end_iso = end_dt.isoformat()
    query = f"""
AppTraces
| where TimeGenerated between (datetime('{start_iso}') .. datetime('{end_iso}'))
| where tostring(Properties.CustomerName) == '{customer}'
| extend service_tree_id = tostring(Properties.ServiceTreeId),
         service_name = tostring(Properties.ServiceName),
         xcv = tostring(Properties.xcv)
| where isnotempty(service_tree_id) and isnotempty(xcv)
| summarize arg_max(TimeGenerated, service_name, xcv) by service_tree_id
| project service_tree_id, service_name, xcv, TimeGenerated
| order by TimeGenerated desc
"""

    def _run() -> list[RunServiceOption]:
        try:
            from azure.monitor.query import LogsQueryClient, LogsQueryStatus
            from azure.identity import DefaultAzureCredential
        except ImportError as exc:  # pragma: no cover
            raise HTTPException(
                503,
                f"azure-monitor-query is not installed: {exc}. Run "
                "'pip install -r Code/CustomerAgent/requirements.txt'",
            )

        cred = DefaultAzureCredential(
            exclude_managed_identity_credential=True,
            exclude_workload_identity_credential=True,
            exclude_interactive_browser_credential=False,
        )
        client = LogsQueryClient(cred)

        try:
            resp = client.query_workspace(
                workspace_id=workspace_id,
                query=query,
                timespan=(start_dt, end_dt),
            )
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                cred.close()
            except Exception:
                pass

        if resp.status == LogsQueryStatus.FAILURE:
            msg = getattr(resp, "partial_error", None) or getattr(resp, "message", "unknown")
            raise HTTPException(502, f"Log Analytics query failed: {msg}")

        out: list[RunServiceOption] = []
        for table in getattr(resp, "tables", None) or []:
            cols = [c.name if hasattr(c, "name") else str(c) for c in table.columns]
            for row in table.rows:
                rowd = dict(zip(cols, row))
                service_tree_id = str(rowd.get("service_tree_id") or "").strip()
                service_name = str(rowd.get("service_name") or "").strip() or service_tree_id
                service_name = display_service_name(service_name) or service_tree_id
                xcv = str(rowd.get("xcv") or "").strip()
                if not service_tree_id or not xcv:
                    continue
                out.append(
                    RunServiceOption(
                        service_tree_id=service_tree_id,
                        service_name=service_name,
                        xcv=xcv,
                    )
                )
        return out

    return await asyncio.to_thread(_run)


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
    from core.services.signals.signal_builder import (
        evaluate_signals_stream,
        load_monitoring_context,
    )
    from core.services.investigation.investigation_runner import run_investigation

    # ── Generate XCV and subscribe to AgentLogger events ─────────────
    xcv = generate_xcv()
    set_current_xcv(xcv)
    event_queue = subscribe_events(xcv)

    # ── Build monitoring context override if customer provided ───────
    # Inherit the matching entry from monitoring_context.json so we keep
    # support_product_names / owning_tenant_names / lookbacks. Falling back
    # to a bare {customer_name, service_tree_ids} object only when the
    # customer is not present in the file.
    monitoring_context = None
    if req.customer_name:
        base_ctx = load_monitoring_context()
        matched_targets = [
            t for t in base_ctx.get("targets", [])
            if t.get("customer_name", "").lower() == req.customer_name.lower()
        ]

        if req.service_tree_id:
            narrowed = []
            for t in matched_targets:
                entries = [
                    e for e in t.get("service_tree_ids", [])
                    if (isinstance(e, dict) and e.get("id") == req.service_tree_id)
                    or (isinstance(e, str) and e == req.service_tree_id)
                ]
                if entries:
                    narrowed.append({**t, "service_tree_ids": entries})
            matched_targets = narrowed or [
                {"customer_name": req.customer_name,
                 "service_tree_ids": [{"id": req.service_tree_id, "name": ""}]}
            ]

        if matched_targets:
            monitoring_context = {**base_ctx, "targets": matched_targets}
        else:
            target: dict = {"customer_name": req.customer_name}
            if req.service_tree_id:
                target["service_tree_ids"] = [{"id": req.service_tree_id, "name": ""}]
            monitoring_context = {"targets": [target]}

    # Inject explicit time window if provided
    if req.start_time or req.end_time:
        if monitoring_context is None:
            monitoring_context = load_monitoring_context()
        if req.start_time:
            monitoring_context["start_time"] = req.start_time
        if req.end_time:
            monitoring_context["end_time"] = req.end_time

    async def pipeline_generator():
        """Run the pipeline and yield AgentLogger events plus investigation
        events as SSE frames.

        Streaming architecture:
          1. Signal evaluation runs all services in parallel and yields each
             SignalBuilderResult as soon as it completes.
          2. As each actionable result arrives, its investigation starts
             immediately — no waiting for the slowest service.
          3. AgentLogger events are drained continuously via a background
             task. Everything is multiplexed through a single output queue.
        """
        def _stamp(event: dict) -> str:
            """Format *event* as an SSE data frame.

            Preserve seq/created_at if already stamped at creation; otherwise
            stamp now (for ad-hoc pipeline-level events).
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

        # ── Event filtering ──────────────────────────────────────────
        # Verbose events that pollute the UI stream but stay in App Insights.
        _DROP_LOGGER_TYPES = {"LLMCall", "InvestigationError", "OutputParsed"}
        _DROP_NARRATOR_TYPES = {"AgentInvoked", "AgentResponse", "AgentPromptUsed"}
        _DROP_INV_TYPES = {
            "investigation_agent_chunk",
            "investigation_stall_warning",
            "investigation_error",
        }

        def _should_drop_logger(event: dict) -> bool:
            etype = event.get("type") or event.get("EventName")
            if etype in _DROP_LOGGER_TYPES:
                return True
            if event.get("Agent") == "narrator" and etype in _DROP_NARRATOR_TYPES:
                return True
            return False

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
                    inv_event['service_name'] = getattr(r, 'service_name', '')
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
                    'service_name': getattr(r, 'service_name', ''),
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
                    await asyncio.sleep(0)

        tracker = AgentLogger.get_instance()
        eval_error_msg: str | None = None

        try:
            set_current_xcv(xcv)
            tracker.start_request_span(xcv, query="")
            yield _stamp({'type': 'pipeline_started', 'xcv': xcv})
            yield _stamp({'type': 'investigation_milestone', 'text': 'Starting signal evaluation…', 'icon': 'search'})

            # Start background producers
            eval_task = asyncio.create_task(_signal_eval_producer())
            drain_task = asyncio.create_task(_logger_drain())

            eval_complete_emitted = False
            investigations_started = False
            investigation_count = 0

            while True:
                try:
                    msg_type, payload = await asyncio.wait_for(output_queue.get(), timeout=0.25)
                except asyncio.TimeoutError:
                    if eval_finished and active_investigations == 0:
                        # Final drain
                        while not output_queue.empty():
                            msg_type, payload = output_queue.get_nowait()
                            if msg_type == _LOGGER_EVENT:
                                if _should_drop_logger(payload):
                                    continue
                                yield _stamp(payload)
                            elif msg_type == _INVESTIGATION_EVENT:
                                if payload.get("type") in _DROP_INV_TYPES:
                                    continue
                                yield _stamp(payload)
                        break
                    continue

                if msg_type == _LOGGER_EVENT:
                    if _should_drop_logger(payload):
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
                    if payload.get("type") in _DROP_INV_TYPES:
                        continue
                    yield _stamp(payload)

                elif msg_type == _INV_ERROR:
                    payload.setdefault('xcv', payload.get('service_xcv', ''))
                    yield _stamp(payload)

                elif msg_type == _INV_DONE:
                    pass  # tracked via active_investigations counter

                elif msg_type == _EVAL_DONE:
                    if not eval_complete_emitted:
                        eval_complete_emitted = True
                        result_summaries = []
                        for r in all_results:
                            result_summaries.append({
                                "customer_name": r.customer_name,
                                "service_tree_id": r.service_tree_id,
                                "service_name": getattr(r, 'service_name', ''),
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

            # Final drain of logger events that arrived after cancellation
            while not event_queue.empty():
                event = event_queue.get_nowait()
                if _should_drop_logger(event):
                    continue
                yield _stamp(event)

            yield _stamp({'type': 'pipeline_complete', 'xcv': xcv, 'investigation_count': investigation_count})
            yield "data: [DONE]\n\n"

        except Exception as exc:
            logger.exception("Pipeline generator failed: %s", exc)
            eval_error_msg = eval_error_msg or str(exc)
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

#---- UI Specific ─────────────────────────────────────────────────
#
# The ratio_ui_web React app (Code/CustomerAgent/ratio_ui_web) expects a set
# of read-only "browse" endpoints plus an /api/investigate SSE endpoint that
# emits events in a normalized `InvestigationEvent` shape.  These endpoints
# are thin read-only wrappers around the config/knowledge directories and a
# translator on top of the existing /api/run pipeline.

from server.ui_api import register_ui_routes  # noqa: E402

register_ui_routes(app, run_pipeline)

# ── Traces replay API (App Insights -> SSE) ──────────────────────────────────
# Exposes GET /api/traces/{xcv}[/stream] so the UI can replay a past
# investigation by correlation id. Fails gracefully if LOG_ANALYTICS_WORKSPACE_ID
# is not set — the endpoints return 503 and the UI falls back to Mock mode.
try:
    from server.traces_api import register_traces_routes  # noqa: E402

    register_traces_routes(app)
except Exception as _traces_exc:  # pragma: no cover - optional dep
    logger.warning("Traces replay API not registered: %s", _traces_exc)


# \u2500\u2500 Teams channel API (Graph) \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500
# Lazily creates a Teams channel per XCV so the UI can offer "Join Teams
# channel". Disabled gracefully when env vars (TEAMS_TENANT_ID,
# TEAMS_CLIENT_ID, TEAMS_CLIENT_SECRET, TEAMS_TEAM_ID) are not set.
try:
    from server.teams_api import register_teams_routes  # noqa: E402

    register_teams_routes(app)
except Exception as _teams_exc:  # pragma: no cover - optional dep
    logger.warning("Teams channel API not registered: %s", _teams_exc)


# ── Email notifications API (Graph sendMail) ────────────────────────────────
# Lets users opt-in to "investigation started"/"investigation resolved"
# emails for a given XCV. Disabled gracefully when env vars are missing.
try:
    from server.email_api import register_email_routes  # noqa: E402

    register_email_routes(app)
except Exception as _email_exc:  # pragma: no cover - optional dep
    logger.warning("Email notification API not registered: %s", _email_exc)


# ── Main ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8503"))
    logger.info("Starting MAF GroupChat server on port %d", port)
    uvicorn.run(app, host="0.0.0.0", port=port)
