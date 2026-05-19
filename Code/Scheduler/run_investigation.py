"""
CustomerAgent investigation scheduler.

Runs as a Container Apps Job (`caj-customeragent-scheduler`) on a cron
schedule (default every 5 minutes). For each tick it acquires an Entra
token via DefaultAzureCredential, then POSTs to the CustomerAgent
cloud endpoint to kick off an investigation for the configured
customer + rolling time window. The returned xcvs are logged so the
job's execution history surfaces them in the Container Apps portal.

This service is *intentionally* its own process / image / resource,
fully separate from `Code/CustomerAgent/src/` (Manik's domain). The
only contract with that service is the HTTP POST below.

Env vars (all optional except where noted):
  CUSTOMER_NAME     default: "BlackRock, Inc"
  LOOKBACK_MINUTES  default: 60      (end = utcnow; start = end - minutes)
  ENDPOINT_URL      default: cloud Container App URL
  AUDIENCE_SCOPE    default: de5f2e0f-…/.default
  HTTP_TIMEOUT      default: 120     (seconds)
  LOG_LEVEL         default: INFO
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from azure.identity import DefaultAzureCredential

# ── Configuration ────────────────────────────────────────────────────────────

DEFAULT_ENDPOINT = (
    "https://ca-ratio-customeragent-dev.graywater-ed11bb19.centralus."
    "azurecontainerapps.io/api/run/services"
)
DEFAULT_AUDIENCE = "de5f2e0f-ac6d-418e-a64c-e38dbbd116e5/.default"
DEFAULT_CUSTOMER = "BlackRock, Inc"
DEFAULT_LOOKBACK_MINUTES = 60
DEFAULT_HTTP_TIMEOUT = 120.0


def _env(name: str, default: str) -> str:
    val = os.getenv(name)
    return val.strip() if val and val.strip() else default


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        return float(raw.strip())
    except ValueError:
        return default


# ── Logging ──────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("scheduler")


# ── Token acquisition ────────────────────────────────────────────────────────


def acquire_token(audience: str) -> str | None:
    """Try to acquire an Entra bearer token for *audience*.

    Returns the token string on success, or `None` if the credential
    chain can't acquire one. Today Manik's endpoint is unauthenticated
    (he said: "Applicable when I enable the authentication on
    CustomerAgent service"), so we let the script proceed without an
    Authorization header. Once auth flips on, the cloud will start
    returning 401 — which we log as an HTTP failure with a clear hint.

    In Azure (Container Apps Job): resolves to the Job's system-assigned MI.
    Locally: falls back to `az login` via AzureCliCredential — provided
    the signed-in user has consented to the audience. To consent locally:
      az logout
      az login --tenant <tenant> --scope <audience>
    """
    try:
        cred = DefaultAzureCredential(
            exclude_interactive_browser_credential=True,
        )
        token = cred.get_token(audience)
        return token.token
    except Exception as exc:
        logger.warning(
            "scheduler.token.unavailable audience=%s err=%s "
            "(this is OK while Manik's endpoint is unauthenticated; "
            "fix by granting MI access or running "
            "`az login --scope %s` locally)",
            audience,
            exc,
            audience,
        )
        return None


# ── Investigation trigger ────────────────────────────────────────────────────


def build_payload(
    customer_name: str, lookback_minutes: int
) -> tuple[dict[str, str], str, str]:
    """Return (payload, start_iso, end_iso). End = utcnow; Start = end - lookback."""
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(minutes=lookback_minutes)
    start_iso = start.isoformat()
    end_iso = end.isoformat()
    payload = {
        "customer_name": customer_name,
        "start_time": start_iso,
        "end_time": end_iso,
    }
    return payload, start_iso, end_iso


def trigger_investigation(
    endpoint: str,
    payload: dict[str, str],
    token: str | None,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    """POST to the CustomerAgent endpoint. Returns the list of services + xcvs.

    If *token* is None we send no Authorization header — fine while Manik's
    endpoint is unauthenticated. Once he enables auth the cloud will return
    401 and we surface that as an HTTPStatusError.
    """
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with httpx.Client(timeout=timeout_seconds) as client:
        resp = client.post(endpoint, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    if not isinstance(data, list):
        raise RuntimeError(
            f"Unexpected response shape from {endpoint}: "
            f"expected list, got {type(data).__name__}: {data!r}"
        )
    return data


# ── Entry point ──────────────────────────────────────────────────────────────


def main() -> int:
    customer_name = _env("CUSTOMER_NAME", DEFAULT_CUSTOMER)
    lookback_minutes = _env_int("LOOKBACK_MINUTES", DEFAULT_LOOKBACK_MINUTES)
    endpoint = _env("ENDPOINT_URL", DEFAULT_ENDPOINT)
    audience = _env("AUDIENCE_SCOPE", DEFAULT_AUDIENCE)
    timeout = _env_float("HTTP_TIMEOUT", DEFAULT_HTTP_TIMEOUT)

    payload, start_iso, end_iso = build_payload(customer_name, lookback_minutes)

    logger.info(
        "scheduler.run.start customer=%r lookback_min=%d window=[%s, %s] endpoint=%s",
        customer_name,
        lookback_minutes,
        start_iso,
        end_iso,
        endpoint,
    )

    try:
        token = acquire_token(audience)
    except Exception as exc:  # pragma: no cover - very defensive
        # acquire_token swallows credential errors itself; this catches
        # anything truly unexpected (e.g. SDK API breakage).
        logger.exception(
            "scheduler.token.unexpected audience=%s err=%s", audience, exc
        )
        token = None

    try:
        results = trigger_investigation(endpoint, payload, token, timeout)
    except httpx.HTTPStatusError as exc:
        body = ""
        try:
            body = exc.response.text[:512]
        except Exception:
            pass
        logger.error(
            "scheduler.http.failed status=%s url=%s body=%s",
            exc.response.status_code,
            endpoint,
            body,
        )
        return 3
    except httpx.HTTPError as exc:
        logger.exception("scheduler.http.error url=%s err=%s", endpoint, exc)
        return 4
    except Exception as exc:
        logger.exception("scheduler.unexpected err=%s", exc)
        return 5

    summary = [
        {
            "service_name": r.get("service_name"),
            "service_tree_id": r.get("service_tree_id"),
            "xcv": r.get("xcv"),
            "timestamp": r.get("timestamp"),
        }
        for r in results
    ]
    logger.info(
        "scheduler.run.success customer=%r services=%d results=%s",
        customer_name,
        len(results),
        json.dumps(summary, default=str),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
