"""
Microsoft Teams channel integration for the CustomerAgent live XCV view.

For every XCV the UI loads we lazily create a dedicated channel inside a
preconfigured Team. The user can click "Join Teams channel" in the UI to
hop into the channel; an initial post summarising the signal is added
when the channel is first created, and a follow-up post can be triggered
on completion via ``POST /api/teams/channel/{xcv}/post-summary``.

Configuration (env vars):

* ``TEAMS_TENANT_ID``       — AAD tenant for the app registration
* ``TEAMS_CLIENT_ID``       — App registration client id
* ``TEAMS_CLIENT_SECRET``   — App registration client secret (or use cert)
* ``TEAMS_TEAM_ID``         — Target Team (Group) id where channels are created

Required application permissions (admin-consented):

* ``Channel.Create``
* ``ChannelMessage.Send``
* ``Group.ReadWrite.All`` (or ``ChannelSettings.ReadWrite.All`` + RSC)

If any required env var is missing the endpoints return ``503`` with a
``{ "enabled": false, ... }`` payload so the UI can show a graceful
"Teams integration disabled" badge instead of a hard error.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from typing import Any

import httpx
from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/teams", tags=["teams"])


# In-process cache. xcv → { channel_id, web_url, display_name, created_at }.
# A future change can promote this to Cosmos so multiple replicas share it.
_CHANNEL_CACHE: dict[str, dict[str, Any]] = {}
_CACHE_LOCK = asyncio.Lock()

_TOKEN_CACHE: dict[str, Any] = {"access_token": None, "exp": 0.0}
_TOKEN_LOCK = asyncio.Lock()

_GRAPH = "https://graph.microsoft.com/v1.0"


def _config() -> dict[str, str]:
    return {
        "tenant_id": (os.getenv("TEAMS_TENANT_ID") or "").strip(),
        "client_id": (os.getenv("TEAMS_CLIENT_ID") or "").strip(),
        "client_secret": (os.getenv("TEAMS_CLIENT_SECRET") or "").strip(),
        "team_id": (os.getenv("TEAMS_TEAM_ID") or "").strip(),
    }


def _missing_config(cfg: dict[str, str]) -> list[str]:
    return [k for k, v in cfg.items() if not v]


async def _get_app_token(cfg: dict[str, str]) -> str:
    """Fetch an app-only Graph token via client-credentials flow.

    The token is cached in-process until 60 s before its expiry so we
    don't hammer AAD on every channel/message call.
    """
    async with _TOKEN_LOCK:
        if _TOKEN_CACHE["access_token"] and _TOKEN_CACHE["exp"] > time.time() + 60:
            return _TOKEN_CACHE["access_token"]

        token_url = f"https://login.microsoftonline.com/{cfg['tenant_id']}/oauth2/v2.0/token"
        data = {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        }
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(token_url, data=data)
        if resp.status_code != 200:
            logger.warning("Teams token request failed: %s %s", resp.status_code, resp.text)
            raise HTTPException(502, f"Teams token request failed ({resp.status_code})")
        body = resp.json()
        access_token = body.get("access_token") or ""
        if not access_token:
            raise HTTPException(502, "Teams token response missing access_token")
        _TOKEN_CACHE["access_token"] = access_token
        _TOKEN_CACHE["exp"] = time.time() + float(body.get("expires_in") or 3500)
        return access_token


def _channel_display_name(xcv: str, service_name: str | None) -> str:
    """Channel display names are limited to 50 chars and a small allowed
    character set; we strip anything punctuation-y."""
    short = (xcv or "")[:8]
    base = (service_name or "Investigation").strip()
    base = re.sub(r"[^A-Za-z0-9 _-]+", "", base)
    base = re.sub(r"\s+", " ", base).strip() or "Investigation"
    name = f"RATIO {base} {short}".strip()
    return name[:50]


async def _graph_post(token: str, path: str, json_body: dict[str, Any]) -> httpx.Response:
    async with httpx.AsyncClient(timeout=30) as client:
        return await client.post(
            f"{_GRAPH}{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=json_body,
        )


class EnsureChannelRequest(BaseModel):
    customer_name: str | None = None
    service_name: str | None = None
    signal_title: str | None = None


class ChannelInfo(BaseModel):
    enabled: bool = True
    xcv: str
    channel_id: str | None = None
    web_url: str | None = None
    display_name: str | None = None
    created: bool = False
    message: str | None = None


@router.get("/health")
async def teams_health() -> dict[str, Any]:
    cfg = _config()
    missing = _missing_config(cfg)
    return {
        "enabled": not missing,
        "missing": missing,
        "team_id_configured": bool(cfg["team_id"]),
    }


@router.post("/channel/{xcv}", response_model=ChannelInfo)
async def ensure_channel(xcv: str, body: EnsureChannelRequest) -> ChannelInfo:
    """Return the Teams channel for this XCV, creating it on first call.

    On creation we also post a small intro message so anyone joining
    immediately sees the context for the investigation.
    """
    xcv = (xcv or "").strip()
    if not xcv:
        raise HTTPException(400, "xcv required")

    cfg = _config()
    missing = _missing_config(cfg)
    if missing:
        return ChannelInfo(
            enabled=False,
            xcv=xcv,
            message=f"Teams integration disabled (missing env: {', '.join(missing)}).",
        )

    async with _CACHE_LOCK:
        cached = _CHANNEL_CACHE.get(xcv)
    if cached:
        return ChannelInfo(
            enabled=True,
            xcv=xcv,
            channel_id=cached["channel_id"],
            web_url=cached["web_url"],
            display_name=cached["display_name"],
            created=False,
        )

    token = await _get_app_token(cfg)
    display_name = _channel_display_name(xcv, body.service_name)
    description_lines = [
        f"RATIO investigation channel for XCV `{xcv}`.",
        f"Customer: {body.customer_name or 'unknown'}",
        f"Service: {body.service_name or 'unknown'}",
        f"Signal: {body.signal_title or '\u2014'}",
    ]
    create_body = {
        "displayName": display_name,
        "description": "\n".join(description_lines),
        "membershipType": "standard",
    }

    resp = await _graph_post(token, f"/teams/{cfg['team_id']}/channels", create_body)
    if resp.status_code in (409,):
        # Conflict — channel already exists. Look it up.
        async with httpx.AsyncClient(timeout=20) as client:
            list_resp = await client.get(
                f"{_GRAPH}/teams/{cfg['team_id']}/channels",
                headers={"Authorization": f"Bearer {token}"},
                params={"$filter": f"displayName eq '{display_name}'"},
            )
        if list_resp.status_code != 200:
            raise HTTPException(502, f"Channel lookup after 409 failed: {list_resp.status_code}")
        items = list_resp.json().get("value") or []
        if not items:
            raise HTTPException(502, "Channel 409 conflict but lookup returned no results")
        channel = items[0]
    elif resp.status_code in (200, 201):
        channel = resp.json()
    else:
        logger.warning("Teams create-channel failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(502, f"Teams create-channel failed ({resp.status_code})")

    channel_id = channel.get("id") or ""
    web_url = channel.get("webUrl") or ""

    # Best-effort intro message; don't fail channel creation if posting fails.
    try:
        intro_html = (
            f"<h3>{display_name}</h3>"
            f"<p>Investigation kicked off for "
            f"<b>{body.service_name or 'service'}</b> "
            f"(customer <b>{body.customer_name or 'unknown'}</b>).</p>"
            f"<p>Signal: {body.signal_title or '\u2014'}<br/>"
            f"XCV: <code>{xcv}</code></p>"
            f"<p><i>Updates will be posted here as the agents work.</i></p>"
        )
        await _graph_post(
            token,
            f"/teams/{cfg['team_id']}/channels/{channel_id}/messages",
            {"body": {"contentType": "html", "content": intro_html}},
        )
    except Exception as exc:  # pragma: no cover
        logger.warning("Teams intro message failed for xcv=%s: %s", xcv, exc)

    info = {
        "channel_id": channel_id,
        "web_url": web_url,
        "display_name": display_name,
        "created_at": time.time(),
    }
    async with _CACHE_LOCK:
        _CHANNEL_CACHE[xcv] = info

    return ChannelInfo(
        enabled=True,
        xcv=xcv,
        channel_id=channel_id,
        web_url=web_url,
        display_name=display_name,
        created=True,
    )


class PostMessageRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=20_000)
    html: bool = False


@router.post("/channel/{xcv}/message")
async def post_channel_message(xcv: str, body: PostMessageRequest) -> dict[str, Any]:
    """Post a free-form update to the channel for this XCV. Caller must
    have created the channel already (or it will 404)."""
    xcv = (xcv or "").strip()
    cfg = _config()
    missing = _missing_config(cfg)
    if missing:
        raise HTTPException(503, f"Teams disabled (missing: {', '.join(missing)})")

    async with _CACHE_LOCK:
        cached = _CHANNEL_CACHE.get(xcv)
    if not cached:
        raise HTTPException(404, "channel not yet created for this xcv")

    token = await _get_app_token(cfg)
    content_type = "html" if body.html else "text"
    resp = await _graph_post(
        token,
        f"/teams/{cfg['team_id']}/channels/{cached['channel_id']}/messages",
        {"body": {"contentType": content_type, "content": body.text}},
    )
    if resp.status_code not in (200, 201):
        raise HTTPException(502, f"Teams message post failed ({resp.status_code})")
    return {"ok": True}


def register_teams_routes(app: FastAPI) -> None:
    """Attach the teams router to a FastAPI app."""
    app.include_router(router)
    logger.info("Teams channel routes registered at /api/teams")
