"""
MonitoringContext — defines the set of services per customer that the
Interpreter waits for before flushing the correlation buffer.

The file format mirrors CustomerAgent's monitoring_context.json so both
services can be configured in tandem (and eventually merged).
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class MonitoringContext:
    """In-memory view of expected services per customer."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._by_customer: dict[str, dict[str, set[str]]] = {}
        self._max_wait_minutes: int | None = None
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            logger.warning(
                "monitoring_context.json not found at %s; "
                "Interpreter will fall back to time-window flushing only.",
                self._path,
            )
            return

        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.error("Failed to parse monitoring_context.json: %s", exc)
            return

        self._max_wait_minutes = data.get("max_wait_minutes")

        for entry in data.get("customers", []):
            name = (entry.get("customer_name") or "").strip()
            if not name:
                continue
            if entry.get("enabled", True) is False:
                logger.info(
                    "MonitoringContext: skipping disabled customer '%s'", name,
                )
                continue
            ids: set[str] = set()
            names: set[str] = set()
            for svc in entry.get("expected_services", []):
                if svc.get("enabled", True) is False:
                    logger.info(
                        "MonitoringContext: skipping disabled service for customer='%s' service_tree_id=%s service_name=%s",
                        name,
                        svc.get("service_tree_id"),
                        svc.get("service_name"),
                    )
                    continue
                stid = (svc.get("service_tree_id") or "").strip().lower()
                sname = (svc.get("service_name") or "").strip().lower()
                if stid:
                    ids.add(stid)
                if sname:
                    names.add(sname)
            self._by_customer[name.lower()] = {
                "ids": ids,
                "names": names,
            }

        logger.info(
            "MonitoringContext loaded: %d customers, max_wait_minutes=%s",
            len(self._by_customer),
            self._max_wait_minutes,
        )

    @property
    def max_wait_minutes(self) -> int | None:
        return self._max_wait_minutes

    def expected_service_ids(self, customer_name: str) -> set[str]:
        return self._by_customer.get(customer_name.lower(), {}).get("ids", set())

    def expected_service_names(self, customer_name: str) -> set[str]:
        return self._by_customer.get(customer_name.lower(), {}).get("names", set())

    def is_tracked(self, customer_name: str) -> bool:
        return customer_name.lower() in self._by_customer

    def all_expected_received(
        self,
        customer_name: str,
        received_service_tree_ids: set[str],
        received_service_names: set[str],
    ) -> bool:
        """Return True if every expected service for this customer has reported.

        Match by service_tree_id when available; fall back to service_name.
        For untracked customers, returns False (use time-window fallback).
        """
        cust = self._by_customer.get(customer_name.lower())
        if not cust:
            return False

        expected_ids = cust["ids"]
        expected_names = cust["names"]

        recv_ids = {x.lower() for x in received_service_tree_ids if x}
        recv_names = {x.lower() for x in received_service_names if x}

        if expected_ids:
            if not expected_ids.issubset(recv_ids):
                return False
        if expected_names:
            if not expected_names.issubset(recv_names | recv_ids):
                # allow service_tree_id matches to satisfy name expectations
                # only if names weren't provided — but they were, so require name match
                if not expected_names.issubset(recv_names):
                    return False
        return bool(expected_ids or expected_names)
