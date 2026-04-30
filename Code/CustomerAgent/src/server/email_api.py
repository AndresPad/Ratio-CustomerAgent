"""
Email opt-in notifications for the CustomerAgent live XCV view.

Subscribers can sign up for a specific XCV via
``POST /api/email/subscribe/{xcv}``. They immediately receive an
"investigation started" email; when the investigation resolves the UI
calls ``POST /api/email/notify-resolved/{xcv}`` and a follow-up email is
sent to every subscriber.

Both emails include a deep link back to the live XCV UI and (optionally)
a link to the Teams channel for the same XCV.

Configuration (env vars, all optional except ``EMAIL_SENDER``):

* ``EMAIL_TENANT_ID``     — fallback ``TEAMS_TENANT_ID``
* ``EMAIL_CLIENT_ID``     — fallback ``TEAMS_CLIENT_ID``
* ``EMAIL_CLIENT_SECRET`` — fallback ``TEAMS_CLIENT_SECRET``
* ``EMAIL_SENDER``        — UPN/mailbox used as the From address
                              (the app reg must have ``Mail.Send`` granted
                              for this user)

If any required value is missing the endpoints return ``{enabled: false}``
so the UI can render a graceful disabled state.

State is in-process (``_SUBSCRIBERS`` / ``_RESOLVED_NOTIFIED``). A future
change can move both to Cosmos so multiple replicas share the list.
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

router = APIRouter(prefix="/api/email", tags=["email"])

_GRAPH = "https://graph.microsoft.com/v1.0"

# xcv -> { email -> {subscribed_at, customer_name, service_name, signal_title} }
_SUBSCRIBERS: dict[str, dict[str, dict[str, Any]]] = {}
_RESOLVED_NOTIFIED: set[str] = set()
_STATE_LOCK = asyncio.Lock()

_TOKEN_CACHE: dict[str, Any] = {"access_token": None, "exp": 0.0}
_TOKEN_LOCK = asyncio.Lock()

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _config() -> dict[str, str]:
    return {
        "tenant_id": (os.getenv("EMAIL_TENANT_ID") or os.getenv("TEAMS_TENANT_ID") or "").strip(),
        "client_id": (os.getenv("EMAIL_CLIENT_ID") or os.getenv("TEAMS_CLIENT_ID") or "").strip(),
        "client_secret": (
            os.getenv("EMAIL_CLIENT_SECRET") or os.getenv("TEAMS_CLIENT_SECRET") or ""
        ).strip(),
        "sender": (os.getenv("EMAIL_SENDER") or "").strip(),
    }


def _missing_config(cfg: dict[str, str]) -> list[str]:
    return [k for k, v in cfg.items() if not v]


async def _get_token(cfg: dict[str, str]) -> str:
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
            logger.warning("Email token request failed: %s %s", resp.status_code, resp.text)
            raise HTTPException(502, f"Email token request failed ({resp.status_code})")
        body = resp.json()
        access_token = body.get("access_token") or ""
        if not access_token:
            raise HTTPException(502, "Email token response missing access_token")
        _TOKEN_CACHE["access_token"] = access_token
        _TOKEN_CACHE["exp"] = time.time() + float(body.get("expires_in") or 3500)
        return access_token


async def _send_mail(
    cfg: dict[str, str],
    *,
    to_addresses: list[str],
    subject: str,
    html_body: str,
) -> None:
    """Send an HTML email via Graph ``users/{sender}/sendMail``."""
    if not to_addresses:
        return
    token = await _get_token(cfg)
    payload = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in to_addresses],
        },
        "saveToSentItems": False,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{_GRAPH}/users/{cfg['sender']}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code not in (200, 202):
        logger.warning("sendMail failed: %s %s", resp.status_code, resp.text)
        raise HTTPException(502, f"sendMail failed ({resp.status_code})")


def _link_button(label: str, url: str, color: str = "#2d6cdf") -> str:
    if not url:
        return ""
    return (
        f'<a href="{url}" style="display:inline-block;padding:10px 18px;'
        f"margin:6px 8px 6px 0;background:{color};color:#ffffff;"
        f'text-decoration:none;border-radius:6px;font-weight:600;font-family:Segoe UI,Arial,sans-serif">'
        f"{label}</a>"
    )


def _email_shell(title: str, intro_html: str, buttons_html: str, footer: str) -> str:
    return f"""<!doctype html>
<html><body style="margin:0;padding:0;background:#f4f6fb;font-family:Segoe UI,Arial,sans-serif;color:#1f2a3a">
  <div style="max-width:620px;margin:24px auto;background:#ffffff;border:1px solid #e3e8f0;border-radius:12px;overflow:hidden">
    <div style="padding:18px 22px;background:linear-gradient(90deg,#0b3d91,#1463d1);color:#ffffff">
      <div style="font-size:12px;letter-spacing:2px;text-transform:uppercase;opacity:.85">RATIO Customer Agent</div>
      <div style="font-size:20px;font-weight:700;margin-top:4px">{title}</div>
    </div>
    <div style="padding:22px">
      {intro_html}
      <div style="margin-top:18px">{buttons_html}</div>
    </div>
    <div style="padding:14px 22px;background:#f7f9fc;color:#6e7c91;font-size:12px;border-top:1px solid #e3e8f0">
      {footer}
    </div>
  </div>
</body></html>"""


def _validate_email(email: str) -> str:
    e = (email or "").strip().lower()
    if not _EMAIL_RE.match(e):
        raise HTTPException(400, "invalid email address")
    return e


# ─── Schemas ────────────────────────────────────────────────────────────────


class SubscribeRequest(BaseModel):
    email: str
    customer_name: str | None = None
    service_name: str | None = None
    signal_title: str | None = None
    ui_url: str | None = Field(default=None, description="Deep link to the live XCV UI")
    teams_web_url: str | None = Field(default=None, description="Optional Teams channel link")


class UnsubscribeRequest(BaseModel):
    email: str


class NotifyResolvedRequest(BaseModel):
    customer_name: str | None = None
    service_name: str | None = None
    summary: str | None = None
    ui_url: str | None = None
    teams_web_url: str | None = None


class SubscribeResponse(BaseModel):
    enabled: bool = True
    xcv: str
    email: str | None = None
    subscribed: bool = False
    already_subscribed: bool = False
    started_email_sent: bool = False
    subscriber_count: int = 0
    message: str | None = None


# ─── Routes ─────────────────────────────────────────────────────────────────


@router.get("/health")
async def email_health() -> dict[str, Any]:
    cfg = _config()
    missing = _missing_config(cfg)
    return {
        "enabled": not missing,
        "missing": missing,
        "sender_configured": bool(cfg["sender"]),
    }


@router.get("/subscribers/{xcv}")
async def list_subscribers(xcv: str) -> dict[str, Any]:
    xcv = (xcv or "").strip()
    async with _STATE_LOCK:
        subs = list((_SUBSCRIBERS.get(xcv) or {}).keys())
        notified = xcv in _RESOLVED_NOTIFIED
    return {"xcv": xcv, "count": len(subs), "emails": subs, "resolved_notified": notified}


@router.post("/subscribe/{xcv}", response_model=SubscribeResponse)
async def subscribe(xcv: str, body: SubscribeRequest) -> SubscribeResponse:
    """Add an email subscriber for an XCV and send the start email."""
    xcv = (xcv or "").strip()
    if not xcv:
        raise HTTPException(400, "xcv required")
    email = _validate_email(str(body.email))

    cfg = _config()
    missing = _missing_config(cfg)
    if missing:
        return SubscribeResponse(
            enabled=False,
            xcv=xcv,
            email=email,
            message=f"Email integration disabled (missing env: {', '.join(missing)}).",
        )

    async with _STATE_LOCK:
        bucket = _SUBSCRIBERS.setdefault(xcv, {})
        already = email in bucket
        bucket[email] = {
            "subscribed_at": time.time(),
            "customer_name": body.customer_name,
            "service_name": body.service_name,
            "signal_title": body.signal_title,
        }
        count = len(bucket)

    started_sent = False
    if not already:
        try:
            html = _email_shell(
                title="Investigation started",
                intro_html=(
                    f"<p>You\u2019re now subscribed to updates for an active RATIO investigation.</p>"
                    f"<table style='font-size:14px;border-collapse:collapse'>"
                    f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>Customer</td>"
                    f"<td style='padding:4px 0'><b>{body.customer_name or '\u2014'}</b></td></tr>"
                    f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>Service</td>"
                    f"<td style='padding:4px 0'><b>{body.service_name or '\u2014'}</b></td></tr>"
                    f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>Signal</td>"
                    f"<td style='padding:4px 0'>{body.signal_title or '\u2014'}</td></tr>"
                    f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>XCV</td>"
                    f"<td style='padding:4px 0'><code>{xcv}</code></td></tr>"
                    f"</table>"
                    f"<p style='margin-top:14px'>You will receive a follow-up email when the investigation resolves.</p>"
                ),
                buttons_html=(
                    _link_button("Open live investigation", body.ui_url or "")
                    + _link_button(
                        "Join Teams channel", body.teams_web_url or "", color="#5a4ed1"
                    )
                ),
                footer=(
                    "You\u2019re receiving this because you opted in to RATIO investigation updates."
                ),
            )
            await _send_mail(
                cfg,
                to_addresses=[email],
                subject=f"[RATIO] Investigation started \u2014 {body.service_name or 'service'} ({xcv[:8]})",
                html_body=html,
            )
            started_sent = True
        except HTTPException:
            raise
        except Exception as exc:  # pragma: no cover
            logger.warning("Start email failed for xcv=%s email=%s: %s", xcv, email, exc)

    return SubscribeResponse(
        enabled=True,
        xcv=xcv,
        email=email,
        subscribed=True,
        already_subscribed=already,
        started_email_sent=started_sent,
        subscriber_count=count,
    )


@router.post("/unsubscribe/{xcv}")
async def unsubscribe(xcv: str, body: UnsubscribeRequest) -> dict[str, Any]:
    xcv = (xcv or "").strip()
    email = _validate_email(str(body.email))
    async with _STATE_LOCK:
        bucket = _SUBSCRIBERS.get(xcv) or {}
        removed = bucket.pop(email, None) is not None
        count = len(bucket)
    return {"xcv": xcv, "email": email, "removed": removed, "subscriber_count": count}


@router.post("/notify-resolved/{xcv}")
async def notify_resolved(xcv: str, body: NotifyResolvedRequest) -> dict[str, Any]:
    """Send the resolution email to every subscriber for this XCV.

    Idempotent: a second call returns ``already_notified=true`` without
    re-sending so the UI can fire this on every page load when the
    investigation is in a resolved state.
    """
    xcv = (xcv or "").strip()
    if not xcv:
        raise HTTPException(400, "xcv required")

    cfg = _config()
    missing = _missing_config(cfg)
    if missing:
        return {
            "enabled": False,
            "xcv": xcv,
            "sent_to": 0,
            "message": f"Email integration disabled (missing: {', '.join(missing)}).",
        }

    async with _STATE_LOCK:
        if xcv in _RESOLVED_NOTIFIED:
            recipients_count = len((_SUBSCRIBERS.get(xcv) or {}))
            return {
                "enabled": True,
                "xcv": xcv,
                "already_notified": True,
                "sent_to": 0,
                "subscriber_count": recipients_count,
            }
        recipients = list((_SUBSCRIBERS.get(xcv) or {}).keys())
        if not recipients:
            # Mark as notified anyway to avoid polling loops on an empty list.
            _RESOLVED_NOTIFIED.add(xcv)
            return {
                "enabled": True,
                "xcv": xcv,
                "already_notified": False,
                "sent_to": 0,
                "subscriber_count": 0,
                "message": "no subscribers",
            }
        _RESOLVED_NOTIFIED.add(xcv)

    summary_html = ""
    if body.summary:
        # Light escape — collapse newlines to <br/>; we trust no HTML in summary.
        safe = (
            body.summary.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace("\n", "<br/>")
        )
        summary_html = (
            f"<div style='margin-top:14px;padding:12px 14px;background:#f0f6ff;"
            f"border-left:3px solid #1463d1;border-radius:4px;font-size:13px;line-height:1.5'>"
            f"{safe}</div>"
        )

    html = _email_shell(
        title="Investigation resolved",
        intro_html=(
            f"<p>The RATIO investigation you subscribed to has been <b>resolved</b>.</p>"
            f"<table style='font-size:14px;border-collapse:collapse'>"
            f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>Customer</td>"
            f"<td style='padding:4px 0'><b>{body.customer_name or '\u2014'}</b></td></tr>"
            f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>Service</td>"
            f"<td style='padding:4px 0'><b>{body.service_name or '\u2014'}</b></td></tr>"
            f"<tr><td style='padding:4px 12px 4px 0;color:#6e7c91'>XCV</td>"
            f"<td style='padding:4px 0'><code>{xcv}</code></td></tr>"
            f"</table>"
            f"{summary_html}"
        ),
        buttons_html=(
            _link_button("Review investigation", body.ui_url or "", color="#1f9b6e")
            + _link_button("Open Teams channel", body.teams_web_url or "", color="#5a4ed1")
        ),
        footer=(
            "This is the final automatic update for this investigation. "
            "You can unsubscribe at any time from the live XCV view."
        ),
    )

    sent = 0
    try:
        await _send_mail(
            cfg,
            to_addresses=recipients,
            subject=f"[RATIO] Investigation resolved \u2014 {body.service_name or 'service'} ({xcv[:8]})",
            html_body=html,
        )
        sent = len(recipients)
    except Exception as exc:  # pragma: no cover
        logger.warning("Resolved email failed for xcv=%s: %s", xcv, exc)
        # Allow a future retry by clearing the flag.
        async with _STATE_LOCK:
            _RESOLVED_NOTIFIED.discard(xcv)
        raise

    return {
        "enabled": True,
        "xcv": xcv,
        "already_notified": False,
        "sent_to": sent,
        "subscriber_count": len(recipients),
    }


def register_email_routes(app: FastAPI) -> None:
    """Attach the email router to a FastAPI app."""
    app.include_router(router)
    logger.info("Email notification routes registered at /api/email")
