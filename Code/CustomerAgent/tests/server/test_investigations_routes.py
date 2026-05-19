"""Tests for /api/investigations REST routes.

Uses an in-process FastAPI app with `server.investigations_api`
mounted on it (NOT `server.app:app` — that boots the full MAF GroupChat
workflow which is way too heavy for unit tests). Cosmos is stubbed at
the `helper.azure_clients.get_cosmos_client` seam so we never touch
live Azure.

Covered:
  * GET /api/investigations               — list filters + projection
  * GET /api/investigations/active        — phase != complete filter
  * GET /api/investigations/{xcv}         — happy path + 404
  * GET /api/investigations/{xcv}/logs    — deep-link URL shape (rich + fallback)

Intentionally NOT covered here:
  * GET /api/investigations/stream — the SSE broker's generator awaits
    asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS), and httpx
    ASGITransport doesn't deliver `http.disconnect` until the generator
    next iterates. That makes the route awkward to unit-test in-process
    without flakiness on Windows. The route is exercised by the live
    smoke test (uvicorn + curl) we ran during development; the broker's
    standalone reader-task behaviour is also separately validated by
    `Code/CustomerAgent/src/server/investigations_stream.py` integration
    runs against real Cosmos.
"""
from __future__ import annotations

import contextlib
import os
import sys
import types
from typing import Any, AsyncIterator
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


# ── In-memory canned docs ────────────────────────────────────────────


SAMPLE_DOCS: list[dict[str, Any]] = [
    {
        "id": "doc-1",
        "xcv": "8db16085-3d88-42ca-9f81-248bd55bdc0d",
        "investigation_id": "9ff36f31",
        "customer_name": "BlackRock, Inc",
        "service_tree_id": "db348eb2-16db-44b3-b867-f60f7cfb87d4",
        "service_name": "SQL Connectivity",
        "timestamp": "2026-05-13T16:46:51.792958+00:00",
        "phase": "complete",
        "symptoms_count": 4,
        "hypotheses_count": 1,
        "evidence_count": 4,
        "activated_signals_count": 2,
        "activated_compounds_count": 0,
        "hypotheses": [
            {
                "id": "HYP-SUP-003",
                "title": "BlackRock support request hypothesis",
                "status": "resolved_as_contributing",
                "confidence": 0.65,
                "root_cause": None,
            }
        ],
        "_ts": 1778690818,
    },
    {
        "id": "doc-2",
        "xcv": "fixture-active-002",
        "investigation_id": "inv-active-002",
        "customer_name": "Fabrikam Bank",
        "service_tree_id": "f1d1800e-d38e-41f2-b63c-72d59ecaf9c0",
        "service_name": "Azure Kubernetes Service",
        "timestamp": "2026-05-14T10:00:00+00:00",
        "phase": "reasoning",
        "symptoms_count": 1,
        "hypotheses_count": 0,
        "evidence_count": 0,
        "activated_signals_count": 1,
        "activated_compounds_count": 0,
        "hypotheses": [],
        "_ts": 1778800000,
    },
]


# ── Fake Cosmos client ──────────────────────────────────────────────


async def _async_iter(items: list[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
    for item in items:
        yield item


class _FakeContainer:
    """Stand-in for ContainerProxy.

    `query_items` returns an async iterator over canned docs (we ignore
    the SQL — sufficient for testing the route's projection + filter
    plumbing). `query_items_change_feed` returns an empty iterator so
    the SSE broker's reader task doesn't spam.
    """

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = list(docs)

    def query_items(self, *_args: Any, **_kwargs: Any) -> AsyncIterator[dict[str, Any]]:
        return _async_iter(self._docs)

    def query_items_change_feed(
        self, *_args: Any, **_kwargs: Any
    ) -> AsyncIterator[dict[str, Any]]:
        return _async_iter([])


class _FakeDatabase:
    def __init__(self, container: _FakeContainer) -> None:
        self._container = container

    def get_container_client(self, _name: str) -> _FakeContainer:
        return self._container


class _FakeCosmosClient:
    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._database = _FakeDatabase(_FakeContainer(docs))
        self.client_connection = MagicMock()
        self.client_connection.last_response_headers = {}

    def get_database_client(self, _name: str) -> _FakeDatabase:
        return self._database


# ── App fixture ─────────────────────────────────────────────────────


@pytest.fixture
def app(monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    """Build a minimal FastAPI app with the investigations routers mounted
    and `get_cosmos_client` stubbed to yield a fake."""
    monkeypatch.setenv(
        "LOG_ANALYTICS_SUBSCRIPTION_ID", "00000000-0000-0000-0000-000000000000"
    )
    monkeypatch.setenv("LOG_ANALYTICS_RESOURCE_GROUP", "rg-test")
    monkeypatch.setenv("LOG_ANALYTICS_WORKSPACE_NAME", "log-test")
    monkeypatch.setenv("LOG_ANALYTICS_WORKSPACE_ID", "test-workspace-id")
    # Keep the SSE broker's poll and heartbeat short so any streaming
    # test can close the connection without waiting on the default 15 s
    # heartbeat timeout. ASGITransport doesn't deliver `http.disconnect`
    # until the generator next iterates, so a short heartbeat = fast
    # teardown.
    monkeypatch.setenv("INVESTIGATION_STREAM_POLL_SECONDS", "0.1")
    monkeypatch.setenv("INVESTIGATION_STREAM_HEARTBEAT_SECONDS", "0.2")

    fake_client = _FakeCosmosClient(SAMPLE_DOCS)

    @contextlib.asynccontextmanager
    async def fake_get_cosmos_client():
        yield fake_client

    # Stub `helper.azure_clients` BEFORE importing the route modules so they
    # bind to the stub (and so they don't try to pull in
    # azure-storage-file-datalake, which isn't required for these routes).
    fake_module = types.ModuleType("helper.azure_clients")
    fake_module.get_cosmos_client = fake_get_cosmos_client  # type: ignore[attr-defined]
    sys.modules.setdefault("helper", types.ModuleType("helper"))
    sys.modules["helper.azure_clients"] = fake_module

    # Force re-import in case a previous test cached the real module.
    for mod in ("server.investigations_api",):
        sys.modules.pop(mod, None)

    src_dir = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "src")
    )
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)

    from server.investigations_api import register_investigations_routes

    a = FastAPI()
    register_investigations_routes(a)
    return a


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ── REST tests ──────────────────────────────────────────────────────


async def test_list_returns_projected_docs(client: AsyncClient) -> None:
    res = await client.get("/api/investigations?limit=10")
    assert res.status_code == 200
    body = res.json()
    assert isinstance(body, list)
    assert len(body) == 2

    first = body[0]
    for key in (
        "id",
        "xcv",
        "investigation_id",
        "customer_name",
        "service_tree_id",
        "service_name",
        "timestamp",
        "phase",
        "counts",
        "hypotheses",
    ):
        assert key in first, f"missing {key} in projected doc"
    assert first["counts"]["hypotheses"] == 1
    assert first["counts"]["symptoms"] == 4
    assert first["hypotheses"][0]["confidence"] == 0.65
    assert first["hypotheses"][0]["status"] == "resolved_as_contributing"


async def test_list_accepts_filter_params(client: AsyncClient) -> None:
    # We don't assert the fake honors the SQL — but we DO assert the route
    # accepts the params without raising (filter SQL builder doesn't crash).
    res = await client.get(
        "/api/investigations"
        "?customer_name=BlackRock,%20Inc"
        "&since=2026-05-01T00:00:00Z"
        "&until=2026-05-31T23:59:59Z"
        "&min_confidence=0.5"
        "&decision=resolved_as_contributing"
        "&phase=complete"
        "&limit=25"
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


async def test_list_rejects_out_of_range_confidence(client: AsyncClient) -> None:
    res = await client.get("/api/investigations?min_confidence=1.5")
    assert res.status_code == 422


async def test_active_endpoint_returns_only_non_complete(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    """Swap the Cosmos client to one that returns only in-flight docs
    (simulating the SQL filter executing in Cosmos)."""
    in_flight_docs = [d for d in SAMPLE_DOCS if d["phase"] != "complete"]

    fake_client = _FakeCosmosClient(in_flight_docs)

    @contextlib.asynccontextmanager
    async def fake_get_cosmos_client():
        yield fake_client

    import server.investigations_api as inv_api

    monkeypatch.setattr(inv_api, "get_cosmos_client", fake_get_cosmos_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get("/api/investigations/active?lookback_hours=24")
        assert res.status_code == 200
        body = res.json()
        assert len(body) == 1
        assert body[0]["phase"] == "reasoning"


async def test_get_by_xcv_happy_path(client: AsyncClient) -> None:
    xcv = SAMPLE_DOCS[0]["xcv"]
    res = await client.get(f"/api/investigations/{xcv}")
    assert res.status_code == 200
    body = res.json()
    assert body["xcv"] == xcv
    assert body["customer_name"] == "BlackRock, Inc"


async def test_get_by_xcv_404_on_unknown(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    fake_client = _FakeCosmosClient([])

    @contextlib.asynccontextmanager
    async def fake_get_cosmos_client():
        yield fake_client

    import server.investigations_api as inv_api

    monkeypatch.setattr(inv_api, "get_cosmos_client", fake_get_cosmos_client)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get("/api/investigations/does-not-exist")
        assert res.status_code == 404
        assert "does-not-exist" in res.json().get("detail", "")


async def test_logs_deep_link_url_shape(client: AsyncClient) -> None:
    xcv = "8db16085-3d88-42ca-9f81-248bd55bdc0d"
    res = await client.get(f"/api/investigations/{xcv}/logs")
    assert res.status_code == 200
    body = res.json()
    assert body["xcv"] == xcv
    url = body["url"]
    assert "portal.azure.com" in url
    # Tenant prefix is part of the documented portal URL format.
    assert "#@" in url
    # New Logs.ReactView blade (the old LogsBlade gets redirected and
    # drops the query — never go back to it).
    assert "Logs.ReactView" in url
    assert "log-test" in url
    assert "rg-test" in url
    # The query is base64-gzip-encoded (not URL-encoded plain text), so
    # the xcv won't appear literally — verify by round-tripping the
    # `q/` payload back to KQL.
    import base64
    import gzip
    from urllib.parse import unquote

    # Pull the q/<encoded> segment and decode.
    q_marker = "/q/"
    idx = url.find(q_marker)
    assert idx >= 0
    rest = url[idx + len(q_marker) :]
    encoded = rest.split("/", 1)[0]
    decoded_kql = gzip.decompress(base64.b64decode(unquote(encoded))).decode("utf-8")
    assert xcv in decoded_kql
    assert "AppTraces" in decoded_kql


async def test_logs_deep_link_uses_defaults_when_env_unset(
    monkeypatch: pytest.MonkeyPatch, app: FastAPI
) -> None:
    """When LA_* env vars are unset, the helper falls back to the baked-in
    dev defaults. The URL still resolves to a valid Logs.ReactView deep-link."""
    monkeypatch.delenv("LOG_ANALYTICS_WORKSPACE_NAME", raising=False)
    monkeypatch.delenv("LOG_ANALYTICS_SUBSCRIPTION_ID", raising=False)
    monkeypatch.delenv("LOG_ANALYTICS_RESOURCE_GROUP", raising=False)
    monkeypatch.delenv("LOG_ANALYTICS_TENANT_ID", raising=False)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        res = await c.get("/api/investigations/abc/logs")
        assert res.status_code == 200
        body = res.json()
        url = body["url"]
        assert "log-ratioai-dev" in url
        assert "rg-ratio-ai-dev" in url
        assert "72f988bf-86f1-41af-91ab-2d7cd011db47" in url
        assert "Logs.ReactView" in url
