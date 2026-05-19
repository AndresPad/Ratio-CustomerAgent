"""
Dedup Engine — fingerprint-based deduplication against the action_ledger.

For each potential action, computes a SHA256 fingerprint from:
    action_type + sorted(services) + root_cause_category + affected_region

Then queries action_ledger in Cosmos:
    • Not exists                          → NEW (create active entry)
    • Exists & last_seen_at within TTL    → SKIP (still active — update last_seen_at, cycle_count++)
    • Exists & last_seen_at beyond TTL    → NEW (went inactive, now recurred — reset to active)

TTL default: 12 hours (configurable via INTERPRETER_DEDUP_TTL_HOURS).
Returns: net-new actions list.
"""
from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from helper.azure_clients import query_ledger_entry, upsert_ledger_entry
from models.schemas import ActionLedgerEntry, CorrelationGroup

logger = logging.getLogger(__name__)


def _norm(s: Any) -> str:
    """Lower-case + strip for identity fields. Avoids fingerprint drift from
    casing/whitespace variations the LLM may emit (e.g. ``WestUS`` vs ``westus``).
    """
    return str(s or "").strip().lower()


def _compute_fingerprint(action: dict[str, Any], customer_name: str) -> str:
    """SHA256 fingerprint for dedup identity.

    Preferred identity (catalog-mapped actions, post-composer):
        customer | action_id | service_name

    Legacy fallback (raw CustomerAgent actions before catalog mapping):
        customer | action_type | sorted(services) | root_cause_category | region

    All identity fields are normalised so cosmetic differences (case,
    whitespace, ordering) don't bypass dedup.
    """
    customer = _norm(customer_name)
    action_id = _norm(action.get("action_id"))
    if action_id:
        service_name = _norm(action.get("service_name"))
        raw = f"{customer}|{action_id}|{service_name}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # Legacy shape
    action_type = _norm(action.get("action_type"))
    services = sorted(_norm(s) for s in action.get("services", []) if s)
    root_cause = _norm(action.get("root_cause_category"))
    region = _norm(action.get("affected_region"))
    raw = f"{customer}|{action_type}|{'|'.join(services)}|{root_cause}|{region}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_within_ttl(last_seen_at: str, ttl_hours: int) -> bool:
    """Check if last_seen_at is within the TTL window."""
    try:
        last_seen = datetime.fromisoformat(last_seen_at)
        cutoff = datetime.now(timezone.utc) - timedelta(hours=ttl_hours)
        return last_seen >= cutoff
    except (ValueError, TypeError):
        return False


async def deduplicate_actions(
    group: CorrelationGroup,
) -> list[dict[str, Any]]:
    """
    Deduplicate actions against the action_ledger in Cosmos DB.

    Returns only net-new actions that should proceed to the composer.
    Actions seen within the TTL window are skipped (ledger updated).
    Actions last seen beyond the TTL are treated as recurrences (new cycle).
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    ttl_hours = int(os.getenv("INTERPRETER_DEDUP_TTL_HOURS", "12"))
    storage_ttl_seconds = int(os.getenv("INTERPRETER_LEDGER_TTL_DAYS", "30")) * 86400
    net_new: list[dict[str, Any]] = []

    # Collect all actions from all outcomes in this correlation group
    all_actions: list[dict[str, Any]] = []
    for outcome in group.outcomes:
        for action in outcome.actions:
            all_actions.append(action)

    # Local dedup first — same fingerprint within this batch
    seen_fingerprints: dict[str, dict[str, Any]] = {}
    for action in all_actions:
        fp = _compute_fingerprint(action, group.customer_name)
        if fp not in seen_fingerprints:
            seen_fingerprints[fp] = action

    # Check each unique action against the ledger
    for fingerprint, action in seen_fingerprints.items():
        existing = await query_ledger_entry(fingerprint, group.customer_name)

        if existing is None:
            # New action — create ledger entry, include in output
            entry = ActionLedgerEntry(
                id=fingerprint,
                customer_name=group.customer_name,
                action_id=action.get("action_id", ""),
                action_type=action.get("action_type", ""),
                title=action.get("title", ""),
                service_name=action.get("service_name", ""),
                services=sorted(action.get("services", [])),
                root_cause_category=action.get("root_cause_category", ""),
                affected_region=action.get("affected_region", ""),
                status="active",
                source_xcvs=[o.xcv for o in group.outcomes],
                correlation_ids=[group.correlation_id],
                first_seen_at=now_iso,
                last_seen_at=now_iso,
                cycle_count=1,
                ttl=storage_ttl_seconds,
            )
            await upsert_ledger_entry(entry)
            net_new.append(action)

        elif _is_within_ttl(existing.last_seen_at, ttl_hours):
            # Still active (within TTL) — skip but update tracking
            existing.last_seen_at = now_iso
            existing.cycle_count += 1
            existing.ttl = storage_ttl_seconds
            if group.correlation_id not in existing.correlation_ids:
                existing.correlation_ids.append(group.correlation_id)
            for o in group.outcomes:
                if o.xcv not in existing.source_xcvs:
                    existing.source_xcvs.append(o.xcv)
            await upsert_ledger_entry(existing)
            logger.info(
                "Skipping active action %s (cycle %d, last seen %s) for %s",
                fingerprint[:12], existing.cycle_count, existing.last_seen_at, group.customer_name,
            )

        else:
            # Beyond TTL (inactive) — recurrence, treat as new
            existing.status = "active"
            existing.last_seen_at = now_iso
            existing.cycle_count += 1
            existing.ttl = storage_ttl_seconds
            if group.correlation_id not in existing.correlation_ids:
                existing.correlation_ids.append(group.correlation_id)
            for o in group.outcomes:
                if o.xcv not in existing.source_xcvs:
                    existing.source_xcvs.append(o.xcv)
            await upsert_ledger_entry(existing)
            net_new.append(action)
            logger.info(
                "Recurrence after inactivity for %s (cycle %d) for %s",
                fingerprint[:12], existing.cycle_count, group.customer_name,
            )

    logger.info(
        "Dedup: %d raw actions → %d unique fingerprints → %d net-new for %s",
        len(all_actions),
        len(seen_fingerprints),
        len(net_new),
        group.correlation_id,
    )
    return net_new
