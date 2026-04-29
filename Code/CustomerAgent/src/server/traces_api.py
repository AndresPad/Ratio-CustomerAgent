"""
Traces Replay API — stream historical investigation runs from Log Analytics.

Queries the AppTraces table in the configured Log Analytics workspace by
correlation id (xcv) and streams each row as a normalized SSE frame whose
shape matches what the CustomerAgent `/api/run` live pipeline emits. The
Theatre and Live Orchestration UIs can therefore consume replay streams
through their existing reducers without any changes.

Endpoints:
  GET /api/traces/{xcv}            -> JSON list of normalized events
  GET /api/traces/{xcv}/stream     -> SSE stream of normalized events
  GET /api/traces/health           -> workspace config probe

Env:
  LOG_ANALYTICS_WORKSPACE_ID  (required — workspace GUID, not ARM id)
  LOG_ANALYTICS_LOOKBACK_DAYS (optional, default 7)

Auth: uses DefaultAzureCredential, so `az login` in the shell that starts
the agents server is enough locally.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from datetime import timedelta
from typing import Any, Iterable

from fastapi import APIRouter, FastAPI, HTTPException, Query
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

# Ensure LOG_ANALYTICS_* env vars are loaded regardless of whether the
# ambient server.app.py happened to find a `.env`. We probe candidate
# locations (repo root and Code/CustomerAgent/) and merge anything we find
# without overriding existing shell values.
try:
    from dotenv import load_dotenv as _load_dotenv  # type: ignore

    _HERE = os.path.dirname(os.path.abspath(__file__))
    for _candidate in (
        os.path.join(_HERE, "..", "..", ".env"),                     # Code/CustomerAgent/.env
        os.path.join(_HERE, "..", "..", "..", "..", ".env"),         # repo root .env
    ):
        _abs = os.path.abspath(_candidate)
        if os.path.isfile(_abs):
            _load_dotenv(_abs, override=False)
except Exception:  # pragma: no cover - dotenv is optional
    pass

# KQL query — loaded from config/queries/trace_replay.kql so it can be tweaked
# without touching Python. Placeholders ({lookback_days}, {xcv}) are substituted
# via str.format() in _query_trace(). The .kql file is the single source of
# truth; if it's missing the server fails loudly at import time.
_QUERIES_DIR = os.path.abspath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "config", "queries")
)
_TRACE_REPLAY_KQL_PATH = os.path.join(_QUERIES_DIR, "trace_replay.kql")


def _load_kql(path: str) -> str:
    """Load a .kql file, stripping `//` comment lines so they don't get
    sent to Log Analytics."""
    with open(path, "r", encoding="utf-8") as f:
        raw = f.read()
    lines = [ln for ln in raw.splitlines() if not ln.lstrip().startswith("//")]
    return "\n".join(lines).strip() + "\n"


_KQL_TEMPLATE = _load_kql(_TRACE_REPLAY_KQL_PATH)

# ── Cached credential + LogsQueryClient singleton ──────────────────────
# Creating a new DefaultAzureCredential per request causes an interactive
# browser login every time the token expires.  Instead we lazily create a
# single credential and LogsQueryClient, reuse them across requests, and
# let the SDK's internal token cache handle refresh.
_la_lock = threading.Lock()
_la_cred: Any = None
_la_client: Any = None


def _get_logs_client() -> Any:
    """Return a cached (credential, LogsQueryClient) pair, creating them
    lazily on first call."""
    global _la_cred, _la_client
    if _la_client is not None:
        return _la_client
    with _la_lock:
        if _la_client is not None:
            return _la_client
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient
        _la_cred = DefaultAzureCredential(
            exclude_managed_identity_credential=True,
            exclude_workload_identity_credential=True,
            exclude_interactive_browser_credential=False,
        )
        _la_client = LogsQueryClient(_la_cred)
        return _la_client


def _flatten_row(row: Any) -> dict[str, Any]:
    """Turn an AppTraces row into the flat event shape consumed by the UI
    reducers.

    Backend AgentLogger currently emits `{EventName, xcv, Service, ...fields}`
    on the live SSE stream — every *custom property* is already a top-level
    key. AppTraces nests those under a `Properties` dict, so we flatten it
    back out. The KQL in config/queries/trace_replay.kql also projects
    renamed convenience columns (event_timestamp, message, event_name,
    agent_name, …) which we surface alongside the flattened Properties.
    """
    # LogsQueryClient returns rows as either list-of-values or dict-like
    # objects depending on version. _query_trace() always passes a dict
    # (built from the column names), so this is the hot path; the list
    # branch is defensive only.
    if isinstance(row, dict):
        rowd = row
    else:
        rowd = {f"col_{i}": v for i, v in enumerate(row)}

    # Accept both the renamed projection (event_timestamp, message) and the
    # raw projection (TimeGenerated, Message) so the flattener stays
    # compatible if the KQL is edited.
    ts = rowd.get("event_timestamp") or rowd.get("TimeGenerated")
    message = rowd.get("message") or rowd.get("Message")
    severity = rowd.get("SeverityLevel")
    props = rowd.get("Properties")

    if isinstance(props, str):
        try:
            props = json.loads(props)
        except Exception:
            props = {"_raw": props}
    elif props is None:
        props = {}

    event: dict[str, Any] = {}
    # Flatten Properties first — EventName, Service, xcv, Tool, ToPhase etc.
    if isinstance(props, dict):
        for k, v in props.items():
            if k in event:
                continue
            event[k] = v

    # Surface any other renamed columns from the projection as top-level
    # fields (event_name, agent_name, hypothesis_selected, …). These are
    # typically duplicates of what's already in Properties, but having them
    # at the top level makes UI reducers and log readers simpler.
    _reserved = {"event_timestamp", "TimeGenerated", "message", "Message",
                 "SeverityLevel", "Properties"}
    for k, v in rowd.items():
        if k in _reserved or k in event or v is None:
            continue
        event[k] = v

    # Preserve top-level metadata.
    if ts is not None:
        event["TimeGenerated"] = str(ts)
    if message and "Message" not in event:
        event["Message"] = message
    if severity is not None and "SeverityLevel" not in event:
        event["SeverityLevel"] = severity

    # Tag as a replay frame so the UI can show it differently if desired.
    event.setdefault("source", "replay")
    return event


def _build_agent_filter(agent: str | None) -> str:
    """Return a KQL line that filters by agent name, or an empty string.

    The agent value is sanitized (alnum + '_' + '-') before being inlined
    into the query so it cannot break out of the string literal.
    """
    if not agent:
        return ""
    safe_agent = "".join(ch for ch in agent if ch.isalnum() or ch in "-_")
    if not safe_agent:
        raise HTTPException(400, "Invalid agent filter.")
    return f"| where tostring(Properties.Agent) == '{safe_agent}'"


async def _query_trace(xcv: str, agent: str | None = None) -> list[dict[str, Any]]:
    """Run the KQL query against Log Analytics in a thread and return the
    flattened rows."""
    workspace_id = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    if not workspace_id:
        raise HTTPException(
            503,
            "LOG_ANALYTICS_WORKSPACE_ID is not configured. Set the Log Analytics "
            "workspace GUID in .env to enable replay.",
        )
    lookback_days = int(os.getenv("LOG_ANALYTICS_LOOKBACK_DAYS", "7") or "7")

    # Lazy import check — surface a clear error if deps are missing.
    try:
        from azure.monitor.query import LogsQueryStatus  # noqa: F401
    except ImportError as exc:  # pragma: no cover - dep missing surfaced to user
        raise HTTPException(
            503,
            f"azure-monitor-query is not installed: {exc}. Run "
            "'pip install -r Code/CustomerAgent/requirements.txt'",
        )

    # Basic guard against KQL injection — xcv values are GUIDs or simple ids.
    safe_xcv = "".join(ch for ch in xcv if ch.isalnum() or ch in "-_")
    if not safe_xcv:
        raise HTTPException(400, "Invalid xcv.")

    query = _KQL_TEMPLATE.format(
        lookback_days=lookback_days,
        xcv=safe_xcv,
        agent_filter=_build_agent_filter(agent),
    )

    def _run() -> list[dict[str, Any]]:
        # Reuse the cached credential + client so the SDK's internal token
        # cache handles refresh — no more interactive browser prompts on
        # every poll.
        client = _get_logs_client()
        resp = client.query_workspace(
            workspace_id=workspace_id,
            query=query,
            timespan=timedelta(days=lookback_days),
        )

        if resp.status == LogsQueryStatus.FAILURE:
            # resp has .partial_error / .message depending on SDK version
            msg = getattr(resp, "partial_error", None) or getattr(resp, "message", "unknown")
            raise HTTPException(502, f"Log Analytics query failed: {msg}")

        tables: Iterable[Any] = getattr(resp, "tables", None) or []
        out: list[dict[str, Any]] = []
        for table in tables:
            cols = [c.name if hasattr(c, "name") else str(c) for c in table.columns]
            for row in table.rows:
                # Build dict using column names for robust flattening.
                rowd = dict(zip(cols, row))
                out.append(_flatten_row(rowd))
        # The KQL orders desc for portal-friendly reading; re-sort ascending
        # here so the replay stream plays oldest → newest.
        out.sort(key=lambda e: _parse_ts_ms(e.get("TimeGenerated")) or 0.0)
        return out

    return await asyncio.to_thread(_run)


def _parse_ts_ms(value: Any) -> float | None:
    """Parse TimeGenerated (ISO string or datetime) to epoch ms."""
    if value is None:
        return None
    if hasattr(value, "timestamp"):
        try:
            return float(value.timestamp()) * 1000.0
        except Exception:
            return None
    s = str(value)
    try:
        # datetime.fromisoformat handles `YYYY-MM-DDTHH:MM:SS(.fff)(+00:00)`;
        # App Insights uses `Z` suffix which fromisoformat doesn't like on
        # older Pythons — strip it.
        from datetime import datetime

        s2 = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s2).timestamp() * 1000.0
    except Exception:
        return None


def _pacing_delays(events: list[dict[str, Any]], speed: str, compress_seconds: float) -> list[float]:
    """Return a list of inter-event sleep durations (seconds) based on the
    requested pacing mode.

    Modes:
        instant     — no delay (default)
        real        — honour the deltas between consecutive TimeGenerated values
        compressed  — rescale real deltas so the whole replay lasts
                      `compress_seconds` seconds total
    """
    n = len(events)
    if n <= 1 or speed == "instant":
        return [0.0] * n

    stamps = [_parse_ts_ms(e.get("TimeGenerated")) for e in events]
    # Fill gaps linearly so we always have monotonic stamps.
    last = None
    for i, v in enumerate(stamps):
        if v is None:
            stamps[i] = last
        else:
            last = v
    if stamps[0] is None:
        return [0.0] * n

    deltas = [0.0]
    for i in range(1, n):
        prev = stamps[i - 1] or stamps[i]
        cur = stamps[i] or prev
        d = max(0.0, ((cur or 0) - (prev or 0)) / 1000.0)
        deltas.append(d)

    if speed == "real":
        return deltas

    # compressed
    total = sum(deltas)
    if total <= 0 or compress_seconds <= 0:
        return [0.0] * n
    scale = compress_seconds / total
    # Cap any single gap to 2s to avoid feel-dead pauses from clock-skew rows.
    return [min(2.0, d * scale) for d in deltas]


router = APIRouter(prefix="/api/traces", tags=["traces"])


@router.get("/health")
async def traces_health() -> dict[str, Any]:
    workspace = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    return {
        "status": "ok" if workspace else "unconfigured",
        "workspace_configured": bool(workspace),
        "lookback_days": int(os.getenv("LOG_ANALYTICS_LOOKBACK_DAYS", "7") or "7"),
    }


@router.get("/services")
async def list_services_with_traces(
    customer_name: str = Query(..., min_length=1, max_length=256),
    lookback_hours: int = Query(48, ge=1, le=720),
    min_events: int = Query(1, ge=1, le=10_000),
) -> list[dict[str, Any]]:
    """Return services for a customer that have **real** trace data in the
    configured Log Analytics workspace, with the latest XCV per service.

    Unlike the cloud `/api/run/services` (which surfaces freshly-spawned
    XCVs that may not yet be ingested), this endpoint scans `AppTraces`
    directly so the UI only ever shows services it can actually replay.
    """
    workspace_id = os.getenv("LOG_ANALYTICS_WORKSPACE_ID", "").strip()
    if not workspace_id:
        raise HTTPException(503, "LOG_ANALYTICS_WORKSPACE_ID is not configured.")

    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
        from azure.identity import DefaultAzureCredential
    except ImportError as exc:  # pragma: no cover
        raise HTTPException(503, f"azure-monitor-query is not installed: {exc}")

    safe_customer = customer_name.replace("'", "''")
    # `CustomerName` and `ServiceTreeId` are typically set only on the FIRST
    # event of an investigation (e.g. `SignalEvaluationStart`); subsequent
    # rows for the same xcv don't carry those properties. So the row-level
    # filter "where CustomerName == X" would discard ~99% of trace events
    # and leave each xcv looking nearly empty.
    #
    # Instead we run a 2-stage KQL: first identify the set of xcvs whose
    # bootstrap event matches the requested customer, then count ALL rows
    # for those xcvs (across all event types) so we can pick the richest.
    kql = f"""
let target_xcvs = AppTraces
    | where TimeGenerated > ago({lookback_hours}h)
    | where tostring(Properties.CustomerName) == '{safe_customer}'
    | extend xcv             = tostring(Properties.xcv),
             service_tree_id = tostring(Properties.ServiceTreeId),
             service_name    = tostring(Properties.ServiceName)
    | where isnotempty(xcv) and isnotempty(service_tree_id)
    | summarize service_tree_id = any(service_tree_id),
                service_name    = any(service_name)
                by xcv;
AppTraces
| where TimeGenerated > ago({lookback_hours}h)
| extend xcv = tostring(Properties.xcv)
| where isnotempty(xcv)
| join kind=inner (target_xcvs) on xcv
| summarize event_count = count(),
            last_seen   = max(TimeGenerated)
            by xcv, service_tree_id, service_name
| where event_count >= {min_events}
| summarize arg_max(event_count, xcv, last_seen, service_name) by service_tree_id
| project service_tree_id, service_name, xcv, event_count, last_seen
| order by event_count desc
"""

    def _run() -> list[dict[str, Any]]:
        cred = DefaultAzureCredential(
            exclude_managed_identity_credential=True,
            exclude_workload_identity_credential=True,
            exclude_interactive_browser_credential=False,
        )
        client = LogsQueryClient(cred)
        try:
            resp = client.query_workspace(
                workspace_id=workspace_id,
                query=kql,
                timespan=timedelta(hours=lookback_hours),
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

        out: list[dict[str, Any]] = []
        for table in getattr(resp, "tables", None) or []:
            cols = [c.name if hasattr(c, "name") else str(c) for c in table.columns]
            for row in table.rows:
                rowd = dict(zip(cols, row))
                stid = str(rowd.get("service_tree_id") or "").strip()
                xcv = str(rowd.get("xcv") or "").strip()
                if not stid or not xcv:
                    continue
                out.append(
                    {
                        "service_tree_id": stid,
                        "service_name": str(rowd.get("service_name") or stid).strip() or stid,
                        "xcv": xcv,
                        "event_count": int(rowd.get("event_count") or 0),
                        "last_seen": str(rowd.get("last_seen") or ""),
                    }
                )
        return out

    return await asyncio.to_thread(_run)


@router.get("/{xcv}")
async def get_trace(
    xcv: str,
    agent: str | None = Query(None, max_length=64, description="Optional agent name filter (e.g. 'narrator')."),
) -> dict[str, Any]:
    """Return the normalized event list for a past investigation."""
    events = await _query_trace(xcv, agent=agent)
    return {"xcv": xcv, "agent": agent, "count": len(events), "events": events}


@router.get("/{xcv}/stream")
async def stream_trace(
    xcv: str,
    speed: str = Query("instant", pattern="^(instant|real|compressed)$"),
    compress_seconds: float = Query(30.0, ge=1.0, le=600.0),
    agent: str | None = Query(None, max_length=64, description="Optional agent name filter (e.g. 'narrator')."),
) -> StreamingResponse:
    """Stream a past investigation as SSE frames using the same shape the
    live pipeline emits, so Theatre/Live reducers consume them unchanged."""
    events = await _query_trace(xcv, agent=agent)
    delays = _pacing_delays(events, speed, compress_seconds)

    async def generator():
        # Announce start so UIs can reset their state.
        start_frame = {
            "type": "pipeline_started",
            "source": "replay",
            "xcv": xcv,
            "replay_event_count": len(events),
        }
        yield f"data: {json.dumps(start_frame)}\n\n"

        for i, ev in enumerate(events):
            if delays[i] > 0:
                await asyncio.sleep(delays[i])
            yield f"data: {json.dumps(ev, default=str)}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def register_traces_routes(app: FastAPI) -> None:
    """Attach the traces router to a FastAPI app."""
    app.include_router(router)
    logger.info("Traces replay routes registered at /api/traces")
