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

# KQL query — parameterized by xcv. Mirrors the query users run in the
# Log Analytics portal. Rows are returned ordered ascending by TimeGenerated
# so the client can replay them in the original order.
_KQL_TEMPLATE = """
AppTraces
| where TimeGenerated >= ago({lookback_days}d)
| where tostring(Properties["xcv"]) == "{xcv}"
| order by TimeGenerated asc
| project TimeGenerated, Message, SeverityLevel, Properties
"""


def _flatten_row(row: Any) -> dict[str, Any]:
    """Turn an AppTraces row into the flat event shape consumed by the UI
    reducers.

    Backend AgentLogger currently emits `{EventName, xcv, Service, ...fields}`
    on the live SSE stream — every *custom property* is already a top-level
    key. AppTraces nests those under a `Properties` dict, so we flatten it
    back out. Known well-known fields (`TimeGenerated`, `Message`) are
    preserved alongside.
    """
    # LogsQueryClient returns rows as either list-of-values or dict-like
    # objects depending on version; handle both defensively.
    if isinstance(row, dict):
        ts = row.get("TimeGenerated")
        message = row.get("Message")
        props = row.get("Properties")
        severity = row.get("SeverityLevel")
    else:
        # Sequence aligned with the projection in _KQL_TEMPLATE:
        # TimeGenerated, Message, SeverityLevel, Properties
        ts, message, severity, props = (
            row[0] if len(row) > 0 else None,
            row[1] if len(row) > 1 else None,
            row[2] if len(row) > 2 else None,
            row[3] if len(row) > 3 else None,
        )

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


async def _query_trace(xcv: str) -> list[dict[str, Any]]:
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

    # Lazy import to avoid cost when replay isn't used.
    try:
        from azure.monitor.query import LogsQueryClient, LogsQueryStatus
        from azure.identity import DefaultAzureCredential
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

    query = _KQL_TEMPLATE.format(lookback_days=lookback_days, xcv=safe_xcv)

    def _run() -> list[dict[str, Any]]:
        # Exclude cloud credential sources that probe non-routable metadata
        # endpoints on developer machines (IMDS hangs ~30s per attempt).
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
                timespan=timedelta(days=lookback_days),
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


@router.get("/{xcv}")
async def get_trace(xcv: str) -> dict[str, Any]:
    """Return the normalized event list for a past investigation."""
    events = await _query_trace(xcv)
    return {"xcv": xcv, "count": len(events), "events": events}


@router.get("/{xcv}/stream")
async def stream_trace(
    xcv: str,
    speed: str = Query("instant", pattern="^(instant|real|compressed)$"),
    compress_seconds: float = Query(30.0, ge=1.0, le=600.0),
) -> StreamingResponse:
    """Stream a past investigation as SSE frames using the same shape the
    live pipeline emits, so Theatre/Live reducers consume them unchanged."""
    events = await _query_trace(xcv)
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
