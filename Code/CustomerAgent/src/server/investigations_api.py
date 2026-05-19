"""
Investigations API — read-only views over the Cosmos ``customer_agent`` container.

Powers the ratio_ui_web "Active" and "History" pages and the per-xcv "Open Logs"
deep-link. SSE stream and Cosmos change-feed processor are implemented separately
in ``investigation_change_feed.py`` (be-change-feed todo).

Endpoints:
  GET  /api/investigations                — list with filters
  GET  /api/investigations/active         — phase != "complete", last 24h
  GET  /api/investigations/in-flight      — xcvs with events in LA but no complete event
  GET  /api/investigations/{xcv}          — single doc
  GET  /api/investigations/{xcv}/logs     — Log Analytics portal deep-link URL

Env (defaults reuse the publisher's vars so a single .env covers both):
  PUBLISHER_COSMOS_ENDPOINT    — Cosmos account endpoint
  PUBLISHER_COSMOS_DATABASE    — default: customeragentdb
  PUBLISHER_COSMOS_CONTAINER   — default: customer_agent
  LOG_ANALYTICS_WORKSPACE_ID
  LOG_ANALYTICS_SUBSCRIPTION_ID
  LOG_ANALYTICS_RESOURCE_GROUP

Auth: uses ``helper.azure_clients.get_cosmos_client()`` (``DefaultAzureCredential``),
so ``az login`` locally is enough provided the signed-in user has Cosmos
Built-in Data Reader on the account.
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from helper.azure_clients import get_cosmos_client

logger = logging.getLogger(__name__)

_COSMOS_DATABASE = os.getenv("PUBLISHER_COSMOS_DATABASE", "customeragentdb")
_COSMOS_CONTAINER = os.getenv("PUBLISHER_COSMOS_CONTAINER", "customer_agent")


# ── Response models ─────────────────────────────────────────────────────────


class HypothesisSummary(BaseModel):
    id: str = ""
    title: str = ""
    status: str = ""
    confidence: float | None = None
    root_cause: str | None = None


class InvestigationCounts(BaseModel):
    symptoms: int = 0
    hypotheses: int = 0
    evidence: int = 0
    activated_signals: int = 0
    activated_compounds: int = 0


class InvestigationSummary(BaseModel):
    """Projection of a Cosmos ``customer_agent`` doc for list / detail views."""

    id: str
    xcv: str
    investigation_id: str = ""
    customer_name: str = ""
    service_tree_id: str = ""
    service_name: str = ""
    timestamp: str = ""
    phase: str = ""
    counts: InvestigationCounts = Field(default_factory=InvestigationCounts)
    hypotheses: list[HypothesisSummary] = Field(default_factory=list)
    # Cosmos epoch (seconds). Useful for client-side sorting.
    _ts: int | None = None


class LogsLinkResponse(BaseModel):
    xcv: str
    url: str
    workspace_id: str
    note: str = ""


class InFlightInvestigation(BaseModel):
    """One xcv that has events in Log Analytics but no terminal event yet.

    "In flight" means the cloud agent is mid-pipeline: it has emitted at
    least one AppTraces row for the xcv, but none of the recognised
    completion events (`InvestigationComplete`, `publish_outcome_complete`,
    `RequestEnded`) have landed yet.
    """

    xcv: str
    service_tree_id: str = ""
    service_name: str = ""
    customer_name: str = ""
    started_at: str = ""           # ISO timestamp of first event seen for this xcv
    last_event_at: str = ""        # ISO timestamp of most recent event
    age_seconds: int = 0           # how long since started_at
    event_count: int = 0


# ── Helpers ─────────────────────────────────────────────────────────────────


def _project(doc: dict[str, Any]) -> InvestigationSummary:
    """Map a raw Cosmos doc to the API-facing projection."""
    hyps = doc.get("hypotheses") or []
    return InvestigationSummary(
        id=str(doc.get("id", "")),
        xcv=str(doc.get("xcv", doc.get("id", ""))),
        investigation_id=str(doc.get("investigation_id", "")),
        customer_name=str(doc.get("customer_name", "")),
        service_tree_id=str(doc.get("service_tree_id", "")),
        service_name=str(doc.get("service_name", "")),
        timestamp=str(doc.get("timestamp", "")),
        phase=str(doc.get("phase", "")),
        counts=InvestigationCounts(
            symptoms=int(doc.get("symptoms_count", 0) or 0),
            hypotheses=int(doc.get("hypotheses_count", 0) or 0),
            evidence=int(doc.get("evidence_count", 0) or 0),
            activated_signals=int(doc.get("activated_signals_count", 0) or 0),
            activated_compounds=int(doc.get("activated_compounds_count", 0) or 0),
        ),
        hypotheses=[
            HypothesisSummary(
                id=str(h.get("id", "")),
                title=str(h.get("title", "")),
                status=str(h.get("status", "")),
                confidence=(
                    float(h["confidence"])
                    if isinstance(h.get("confidence"), (int, float))
                    else None
                ),
                root_cause=h.get("root_cause"),
            )
            for h in hyps
            if isinstance(h, dict)
        ],
        _ts=doc.get("_ts"),
    )


async def _query(sql: str, parameters: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    """Run a parameterised SQL query against the customer_agent container."""
    async with get_cosmos_client() as cosmos_client:
        database = cosmos_client.get_database_client(_COSMOS_DATABASE)
        container = database.get_container_client(_COSMOS_CONTAINER)
        items: list[dict[str, Any]] = []
        async for doc in container.query_items(
            query=sql,
            parameters=parameters or [],
        ):
            items.append(doc)
        return items


def _encode_la_query(kql: str) -> str:
    """Encode a KQL string as Microsoft's documented "share query" payload.

    The new Log Analytics portal blade (`Microsoft_OperationsManagementSuite_Workspace/Logs.ReactView`)
    expects the query parameter as base64(gzip(kql)) at the `/q/` URL slot.
    The old `query/<urlencoded>/isQueryBase64Compressed/false` format that
    `Microsoft_Azure_Monitoring_Logs/LogsBlade` accepted is no longer
    honoured after the portal redirects you to ReactView — that's why
    those links land on "Resource ID: Not available" with no query loaded.

    The output is base64 of gzip-compressed UTF-8 bytes; we then URL-encode
    it (slashes etc.) in the caller.
    """
    compressed = gzip.compress(kql.encode("utf-8"))
    return base64.b64encode(compressed).decode("ascii")


def _build_logs_url(xcv: str) -> tuple[str, str]:
    """Return ``(url, note)`` for the Log Analytics portal deep-link for *xcv*.

    v1 is a portal deep-link — opens the LA Logs blade with the xcv
    pre-filtered into the KQL. v2 (future) would execute the KQL
    server-side.

    Uses the canonical `Microsoft_OperationsManagementSuite_Workspace/Logs.ReactView`
    blade with the query packed as base64-gzip into `q/`, per Microsoft's
    documented "share query as URL" format. The older
    `Microsoft_Azure_Monitoring_Logs/LogsBlade` blade is being deprecated
    and gets redirected to ReactView, which drops the query payload —
    don't go back to that format.

    Env vars are read at call time (not import time) so a redeploy that
    flips LOG_ANALYTICS_* values on the running container takes effect
    without a restart. Sensible dev-environment defaults are baked in.
    """
    la_tenant_id = os.getenv(
        "LOG_ANALYTICS_TENANT_ID",
        "72f988bf-86f1-41af-91ab-2d7cd011db47",  # Microsoft tenant
    )
    la_subscription_id = os.getenv(
        "LOG_ANALYTICS_SUBSCRIPTION_ID",
        "01819f01-7af1-4dd8-9354-9dccc163ceae",  # Azure CXP Data Science - Public Dev
    )
    la_resource_group = os.getenv(
        "LOG_ANALYTICS_RESOURCE_GROUP", "rg-ratio-ai-dev"
    )
    la_workspace_name = os.getenv(
        "LOG_ANALYTICS_WORKSPACE_NAME", "log-ratioai-dev"
    )

    resource_id = (
        f"/subscriptions/{la_subscription_id}"
        f"/resourceGroups/{la_resource_group}"
        f"/providers/Microsoft.OperationalInsights/workspaces/{la_workspace_name}"
    )
    kql = (
        "AppTraces\n"
        f'| where Properties.xcv == "{xcv}" or Properties.CorrelationId == "{xcv}"\n'
        "| order by TimeGenerated asc\n"
        "| take 500"
    )
    encoded_query = _encode_la_query(kql)
    # `#@<tenant>/blade/.../q/<b64gz>` — the format Logs.ReactView honours.
    url = (
        f"https://portal.azure.com/#@{la_tenant_id}"
        "/blade/Microsoft_OperationsManagementSuite_Workspace/Logs.ReactView"
        f"/resourceId/{quote(resource_id, safe='')}"
        "/source/LogsBlade.AnalyticsShareLinkToQuery"
        f"/q/{quote(encoded_query, safe='')}"
        "/timespan/P1D"
    )
    return url, ""


# ── Router ──────────────────────────────────────────────────────────────────

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


@router.get("", response_model=list[InvestigationSummary])
async def list_investigations(
    customer_name: str | None = Query(None, description="Exact match on customer_name"),
    since: str | None = Query(None, description="ISO-8601 lower bound on timestamp"),
    until: str | None = Query(None, description="ISO-8601 upper bound on timestamp"),
    min_confidence: float | None = Query(None, ge=0.0, le=1.0),
    decision: str | None = Query(
        None,
        description="Filter on hypotheses[].status (e.g. resolved_as_root_cause)",
    ),
    phase: str | None = Query(None, description="Filter on top-level phase"),
    limit: int = Query(50, ge=1, le=500),
) -> list[InvestigationSummary]:
    """List investigations, newest first.

    Filters compose with AND. ``min_confidence`` and ``decision`` filter on
    ``hypotheses[]`` membership: a doc matches if *any* hypothesis satisfies
    the predicate.
    """
    clauses: list[str] = []
    parameters: list[dict[str, Any]] = []

    if customer_name:
        clauses.append("c.customer_name = @customer_name")
        parameters.append({"name": "@customer_name", "value": customer_name})
    if since:
        clauses.append("c.timestamp >= @since")
        parameters.append({"name": "@since", "value": since})
    if until:
        clauses.append("c.timestamp <= @until")
        parameters.append({"name": "@until", "value": until})
    if phase:
        clauses.append("c.phase = @phase")
        parameters.append({"name": "@phase", "value": phase})
    if min_confidence is not None:
        clauses.append(
            "EXISTS(SELECT VALUE h FROM h IN c.hypotheses "
            "WHERE IS_NUMBER(h.confidence) AND h.confidence >= @min_conf)"
        )
        parameters.append({"name": "@min_conf", "value": min_confidence})
    if decision:
        clauses.append(
            "EXISTS(SELECT VALUE h FROM h IN c.hypotheses WHERE h.status = @decision)"
        )
        parameters.append({"name": "@decision", "value": decision})

    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    # NOTE: Cosmos does not support parameterised TOP — must be a literal int.
    # ``limit`` is bounded by FastAPI's Query(ge=1, le=500), so cast is safe.
    sql = (
        f"SELECT TOP {int(limit)} * FROM c{where} "
        "ORDER BY c._ts DESC"
    )

    try:
        docs = await _query(sql, parameters)
    except Exception:
        logger.exception("list_investigations: Cosmos query failed sql=%s", sql)
        raise HTTPException(status_code=502, detail="Cosmos query failed")
    return [_project(d) for d in docs]


@router.get("/active", response_model=list[InvestigationSummary])
async def list_active(
    lookback_hours: int = Query(24, ge=1, le=168),
    limit: int = Query(100, ge=1, le=500),
) -> list[InvestigationSummary]:
    """In-flight investigations: ``phase != "complete"`` within the lookback window."""
    since = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
    # NOTE: Cosmos does not support parameterised TOP — must be a literal int.
    sql = (
        f"SELECT TOP {int(limit)} * FROM c "
        "WHERE (NOT IS_DEFINED(c.phase) OR c.phase != 'complete') "
        "AND c.timestamp >= @since "
        "ORDER BY c._ts DESC"
    )
    params = [
        {"name": "@since", "value": since},
    ]
    try:
        docs = await _query(sql, params)
    except Exception:
        logger.exception("list_active: Cosmos query failed")
        raise HTTPException(status_code=502, detail="Cosmos query failed")
    return [_project(d) for d in docs]


@router.get("/in-flight", response_model=list[InFlightInvestigation])
async def list_in_flight(
    customer_name: str = Query(..., min_length=1, max_length=256),
    lookback_hours: int = Query(1, ge=1, le=24),
    max_age_minutes: int = Query(
        15, ge=1, le=120,
        description="Hard cap on age — anything older than this is dropped "
                    "as 'probably stuck/abandoned' rather than in-flight.",
    ),
) -> list[InFlightInvestigation]:
    """Return xcvs for *customer_name* that have events in Log Analytics
    but no completed doc in Cosmos yet.

    "In flight" = the cloud agent is mid-pipeline. Useful for showing
    the gap between "scheduler fired" and "outcome_publisher wrote to
    Cosmos" (typically 30 s to a few minutes).

    Strategy:
      1. KQL — list xcvs for the customer with events in the last
         ``lookback_hours``, capturing start_time / last_event / count.
      2. Cosmos cross-check — query the ``customer_agent`` container
         for those xcvs; any xcv with a doc is *already complete* and
         drops out.
      3. Filter by ``max_age_minutes`` — anything older than this is
         likely stuck or errored; we'd rather under-report than show
         stale-looking "in flight" rows.

    This avoids the brittle "what's the exact completion event name"
    question — Cosmos is the source of truth for completion.
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
    # 1) Single-stage KQL — distinct xcvs for this customer in the lookback,
    #    with first/last event time + count. `Properties.CustomerName` only
    #    lives on the bootstrap event, so we look it up per-xcv via any().
    kql = f"""
AppTraces
| where TimeGenerated > ago({lookback_hours}h)
| extend xcv             = tostring(Properties.xcv),
         customer_name   = tostring(Properties.CustomerName),
         service_tree_id = tostring(Properties.ServiceTreeId),
         service_name    = tostring(Properties.ServiceName)
| where isnotempty(xcv)
| summarize started_at        = min(TimeGenerated),
            last_event_at     = max(TimeGenerated),
            event_count       = count(),
            customer_name     = anyif(customer_name, isnotempty(customer_name)),
            service_tree_id   = anyif(service_tree_id, isnotempty(service_tree_id)),
            service_name      = anyif(service_name, isnotempty(service_name))
            by xcv
| where customer_name == '{safe_customer}'
| where started_at > ago({max_age_minutes}m)
| order by started_at desc
"""

    def _run_la() -> list[dict[str, Any]]:
        cred = DefaultAzureCredential(
            exclude_managed_identity_credential=True,
            exclude_workload_identity_credential=True,
            exclude_interactive_browser_credential=False,
        )
        client = LogsQueryClient(cred)
        try:
            try:
                resp = client.query_workspace(
                    workspace_id=workspace_id,
                    query=kql,
                    timespan=timedelta(hours=lookback_hours),
                )
            except Exception as exc:
                logger.warning(
                    "in-flight LA query error: %s: %s",
                    type(exc).__name__,
                    str(exc)[:200],
                )
                return []
            if resp.status == LogsQueryStatus.FAILURE:
                msg = getattr(resp, "partial_error", None) or getattr(
                    resp, "message", "unknown"
                )
                logger.warning("in-flight LA query failed: %s", msg)
                return []
            out: list[dict[str, Any]] = []
            for table in getattr(resp, "tables", None) or []:
                cols = [
                    c.name if hasattr(c, "name") else str(c)
                    for c in table.columns
                ]
                for row in table.rows:
                    out.append(dict(zip(cols, row)))
            return out
        finally:
            try:
                client.close()
            except Exception:
                pass
            try:
                cred.close()
            except Exception:
                pass

    la_rows = await asyncio.to_thread(_run_la)
    if not la_rows:
        return []

    la_xcvs = [str(r.get("xcv") or "").strip() for r in la_rows]
    la_xcvs = [x for x in la_xcvs if x]

    # 2) Cosmos cross-check — which of these xcvs already have a doc?
    completed_xcvs: set[str] = set()
    if la_xcvs:
        # Cosmos SQL parameter arrays aren't directly supported; we build
        # an IN clause with positional @xcvN params. Bound the size to be
        # safe (max_age_minutes + lookback_hours keep this small in practice).
        max_in = 200
        for chunk_start in range(0, len(la_xcvs), max_in):
            chunk = la_xcvs[chunk_start : chunk_start + max_in]
            placeholders = ", ".join(f"@xcv{i}" for i in range(len(chunk)))
            sql = f"SELECT c.xcv FROM c WHERE c.xcv IN ({placeholders})"
            params = [{"name": f"@xcv{i}", "value": x} for i, x in enumerate(chunk)]
            try:
                docs = await _query(sql, params)
            except Exception:
                logger.exception(
                    "in-flight: Cosmos cross-check failed; assuming "
                    "%d xcvs are still in flight",
                    len(chunk),
                )
                continue
            for d in docs:
                xv = str(d.get("xcv") or "").strip()
                if xv:
                    completed_xcvs.add(xv)

    # 3) Build response — anything in LA but NOT in Cosmos is in-flight.
    now = datetime.now(timezone.utc)
    results: list[InFlightInvestigation] = []
    for r in la_rows:
        xcv = str(r.get("xcv") or "").strip()
        if not xcv or xcv in completed_xcvs:
            continue
        started_raw = r.get("started_at")
        last_raw = r.get("last_event_at")
        started_iso = ""
        last_iso = ""
        age = 0
        if started_raw is not None:
            try:
                started_dt = (
                    started_raw
                    if isinstance(started_raw, datetime)
                    else datetime.fromisoformat(str(started_raw).replace("Z", "+00:00"))
                )
                if started_dt.tzinfo is None:
                    started_dt = started_dt.replace(tzinfo=timezone.utc)
                started_iso = started_dt.isoformat()
                age = max(0, int((now - started_dt).total_seconds()))
            except Exception:
                started_iso = str(started_raw)
        if last_raw is not None:
            try:
                last_dt = (
                    last_raw
                    if isinstance(last_raw, datetime)
                    else datetime.fromisoformat(str(last_raw).replace("Z", "+00:00"))
                )
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                last_iso = last_dt.isoformat()
            except Exception:
                last_iso = str(last_raw)
        results.append(
            InFlightInvestigation(
                xcv=xcv,
                service_tree_id=str(r.get("service_tree_id") or ""),
                service_name=str(r.get("service_name") or ""),
                customer_name=str(r.get("customer_name") or customer_name),
                started_at=started_iso,
                last_event_at=last_iso,
                age_seconds=age,
                event_count=int(r.get("event_count") or 0),
            )
        )
    return results


@router.get("/{xcv}", response_model=InvestigationSummary)
async def get_investigation(xcv: str) -> InvestigationSummary:
    """Fetch a single doc by xcv (matches either ``c.xcv`` or ``c.id``)."""
    sql = "SELECT TOP 1 * FROM c WHERE c.xcv = @xcv OR c.id = @xcv"
    try:
        docs = await _query(sql, [{"name": "@xcv", "value": xcv}])
    except Exception:
        logger.exception("get_investigation: Cosmos query failed xcv=%s", xcv)
        raise HTTPException(status_code=502, detail="Cosmos query failed")
    if not docs:
        raise HTTPException(status_code=404, detail=f"No investigation for xcv={xcv}")
    return _project(docs[0])


@router.get("/{xcv}/logs", response_model=LogsLinkResponse)
async def get_logs_link(xcv: str) -> LogsLinkResponse:
    """Return a Log Analytics portal deep-link URL pre-filtered on *xcv*.

    v1 only — server-side KQL execution is a future enhancement (see plan.md).
    """
    url, note = _build_logs_url(xcv)
    return LogsLinkResponse(
        xcv=xcv,
        url=url,
        workspace_id=os.getenv("LOG_ANALYTICS_WORKSPACE_ID", ""),
        note=note,
    )


# ── Registration helper ─────────────────────────────────────────────────────


def register_investigations_routes(app: FastAPI) -> None:
    """Attach the investigations router to a FastAPI app."""
    app.include_router(router)
    logger.info(
        "Investigations API registered at /api/investigations "
        "(db=%s container=%s)",
        _COSMOS_DATABASE,
        _COSMOS_CONTAINER,
    )
