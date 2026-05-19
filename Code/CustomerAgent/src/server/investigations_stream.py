"""
Cosmos change-feed broker + Server-Sent-Events stream for investigations.

Powers ``GET /api/investigations/stream``. A single background task polls the
``customer_agent`` change feed and fans out new/updated docs to per-connection
``asyncio.Queue``s. Each SSE client also receives a periodic heartbeat so
intermediate proxies (nginx, Container Apps ingress) don't close idle streams.

v1 design notes (documented intentionally):

* Starts from "Now" on process start — we do **not** replay history. Old
  investigations are fetched via the REST list/get endpoints; SSE is purely
  for live in-flight updates.
* Continuation tokens are kept in-process. The ``leases`` container is
  provisioned (Bicep + dev-Cosmos) for v2 scale-out via the official Cosmos
  Change Feed Processor; v1 does not persist leases. This is good enough as
  long as the backend has ``minReplicas >= 1`` (matches the Container App
  scale rule).
* The broker is started lazily on first ``subscribe()`` so unit tests that
  don't touch SSE don't open Cosmos sockets.

Env (defaults reuse the publisher's vars):
  PUBLISHER_COSMOS_DATABASE      — default: customeragentdb
  PUBLISHER_COSMOS_CONTAINER     — default: customer_agent
  INVESTIGATION_STREAM_POLL_SECONDS  — default: 3
  INVESTIGATION_STREAM_HEARTBEAT_SECONDS — default: 15
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import StreamingResponse

from helper.azure_clients import get_cosmos_client
from server.investigations_api import _project  # reuse the projection helper

logger = logging.getLogger(__name__)

_COSMOS_DATABASE = os.getenv("PUBLISHER_COSMOS_DATABASE", "customeragentdb")
_COSMOS_CONTAINER = os.getenv("PUBLISHER_COSMOS_CONTAINER", "customer_agent")
_POLL_SECONDS = float(os.getenv("INVESTIGATION_STREAM_POLL_SECONDS", "3"))
_HEARTBEAT_SECONDS = float(os.getenv("INVESTIGATION_STREAM_HEARTBEAT_SECONDS", "15"))
_QUEUE_MAXSIZE = int(os.getenv("INVESTIGATION_STREAM_QUEUE_MAXSIZE", "200"))


# ── Broker ──────────────────────────────────────────────────────────────────


class _ChangeFeedBroker:
    """Singleton broker fanning Cosmos change-feed docs to SSE subscribers."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()
        self._stop = asyncio.Event()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        """Register a new subscriber queue and ensure the reader task is running."""
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        async with self._lock:
            self._subscribers.add(q)
            if self._task is None or self._task.done():
                self._stop.clear()
                self._task = asyncio.create_task(
                    self._run(), name="investigation-change-feed"
                )
                logger.info("ChangeFeedBroker: reader task started")
        return q

    async def unsubscribe(self, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(q)
            # Don't stop the reader on the last unsubscribe — the next
            # subscriber will reuse it, avoiding cold-start latency. The task
            # is cheap (one Cosmos poll every few seconds) and naturally dies
            # if the process is recycled.

    def _publish(self, doc: dict[str, Any]) -> int:
        """Push *doc* to every subscriber queue. Returns drop count."""
        dropped = 0
        for q in list(self._subscribers):
            try:
                q.put_nowait(doc)
            except asyncio.QueueFull:
                dropped += 1
                logger.warning(
                    "ChangeFeedBroker: queue full, dropping doc id=%s for one subscriber",
                    doc.get("id"),
                )
        return dropped

    async def _run(self) -> None:
        """Poll the Cosmos change feed forever and fan out new docs."""
        # Lazy import so this module doesn't fail at import time if Cosmos
        # SDK extras are missing (e.g. unit-test environments).
        backoff = _POLL_SECONDS
        try:
            async with get_cosmos_client() as cosmos:
                database = cosmos.get_database_client(_COSMOS_DATABASE)
                container = database.get_container_client(_COSMOS_CONTAINER)
                # Start from "Now": we only want LIVE changes, not replay.
                kwargs: dict[str, Any] = {"start_time": "Now"}
                while not self._stop.is_set():
                    try:
                        feed = container.query_items_change_feed(**kwargs)
                        emitted = 0
                        async for doc in feed:
                            self._publish(doc)
                            emitted += 1
                        # After draining, refresh continuation from response
                        # headers so the next iteration only sees newer docs.
                        headers = getattr(container.client_connection, "last_response_headers", {}) or {}
                        cont = headers.get("etag") or headers.get("Etag")
                        if cont:
                            kwargs = {"continuation": cont}
                        if emitted:
                            logger.debug("ChangeFeedBroker: emitted %d doc(s)", emitted)
                        backoff = _POLL_SECONDS
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception(
                            "ChangeFeedBroker: poll failed; backing off %.1fs",
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 60.0)
                        continue
                    await asyncio.sleep(_POLL_SECONDS)
        except asyncio.CancelledError:
            logger.info("ChangeFeedBroker: reader task cancelled")
            raise
        except Exception:
            logger.exception("ChangeFeedBroker: reader task crashed")


_BROKER = _ChangeFeedBroker()


# ── SSE endpoint ────────────────────────────────────────────────────────────


def _sse_frame(event: str, payload: Any) -> bytes:
    """Format an SSE frame as bytes."""
    data = json.dumps(payload, default=str, separators=(",", ":"))
    return f"event: {event}\ndata: {data}\n\n".encode("utf-8")


async def _event_generator(request: Request) -> AsyncGenerator[bytes, None]:
    """Per-connection generator: emits hello → docs → heartbeats."""
    q = await _BROKER.subscribe()
    try:
        yield _sse_frame("hello", {"ok": True, "poll_seconds": _POLL_SECONDS})
        while True:
            if await request.is_disconnected():
                break
            try:
                doc = await asyncio.wait_for(q.get(), timeout=_HEARTBEAT_SECONDS)
            except asyncio.TimeoutError:
                yield _sse_frame("heartbeat", {"ts": asyncio.get_event_loop().time()})
                continue
            try:
                projected = _project(doc).model_dump()
            except Exception:
                logger.exception("SSE: projection failed; emitting raw doc")
                projected = doc
            yield _sse_frame("investigation", projected)
    finally:
        await _BROKER.unsubscribe(q)


router = APIRouter(prefix="/api/investigations", tags=["investigations"])


@router.get("/stream")
async def investigations_stream(request: Request) -> StreamingResponse:
    """Server-Sent Events stream of new/updated investigation documents."""
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # Disable response buffering on nginx / Container Apps front door.
            "X-Accel-Buffering": "no",
        },
    )


def register_investigations_stream_routes(app: FastAPI) -> None:
    """Attach the SSE router to a FastAPI app."""
    app.include_router(router)
    logger.info(
        "Investigations SSE stream registered at /api/investigations/stream "
        "(db=%s container=%s)",
        _COSMOS_DATABASE,
        _COSMOS_CONTAINER,
    )
