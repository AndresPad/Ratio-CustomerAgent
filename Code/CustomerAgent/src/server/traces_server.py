"""
Standalone trace server — serves Log Analytics trace events for the
Investigation Reasoning Flow UI.

Run:
    python -m uvicorn server.traces_server:app --port 8503 --reload

Or:
    cd Code/CustomerAgent/src
    python -m uvicorn server.traces_server:app --port 8503
"""
import logging
import os
from datetime import timedelta

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

logger = logging.getLogger(__name__)

app = FastAPI(title="Traces Server", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3010", "http://localhost:3010"],
    allow_methods=["*"],
    allow_headers=["*"],
)

WORKSPACE_ID = os.environ.get(
    "LOG_ANALYTICS_WORKSPACE_ID",
    "321fc84a-8346-40f4-acf6-a505a7f7dd90",
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/api/traces/{xcv}")
async def get_traces(xcv: str):
    """Fetch trace events for a given XCV from Log Analytics."""
    try:
        from azure.identity import DefaultAzureCredential
        from azure.monitor.query import LogsQueryClient
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Azure SDK not installed: {exc}",
        )

    try:
        credential = DefaultAzureCredential()
        client = LogsQueryClient(credential)

        query = f"""
        AppTraces
        | where Properties has "{xcv}"
        | project
            TimeGenerated,
            EventName    = tostring(Properties.EventName),
            AgentName    = tostring(Properties.AgentName),
            Content      = tostring(Properties.Content),
            ToolName     = tostring(Properties.ToolName),
            QueryText    = tostring(Properties.QueryText),
            Duration     = todouble(Properties.Duration),
            XCV          = tostring(Properties.XCV),
            SessionId    = tostring(Properties.SessionId),
            HypothesisId = tostring(Properties.HypothesisId),
            HypothesisText = tostring(Properties.HypothesisText),
            Confidence   = todouble(Properties.Confidence),
            Status       = tostring(Properties.Status),
            SignalTitle  = tostring(Properties.SignalTitle),
            RootCause    = tostring(Properties.RootCause),
            Summary      = tostring(Properties.Summary)
        | order by TimeGenerated asc
        """

        response = client.query_workspace(
            workspace_id=WORKSPACE_ID,
            query=query,
            timespan=timedelta(days=7),
        )

        events = []
        if response.tables:
            table = response.tables[0]
            columns = [c.name for c in table.columns]
            for row in table.rows:
                event = {}
                for col_name, value in zip(columns, row):
                    if value is not None and value != "":
                        event[col_name] = value
                events.append(event)

        logger.info("XCV %s: %d events", xcv, len(events))
        return {"events": events, "count": len(events)}

    except Exception as exc:
        logger.exception("Failed to query traces for XCV %s", xcv)
        raise HTTPException(status_code=500, detail=str(exc))
