"""Run the signal builder once and trigger investigation for activated signals.

Usage:
    python run_signal_builder.py [--customer "BlackRock, Inc"] [--service-tree-id "49c39e84-..."]
                                 [--start-time "2024-01-15T10:00:00Z"] [--end-time "2024-01-15T14:00:00Z"]

If no arguments are given, uses targets from config/monitoring_context.json.
If --start-time / --end-time are omitted, defaults to now-4h / now.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("run_signal_builder")


async def main(customer: str | None, service_tree_id: str | None, start_time: str | None = None, end_time: str | None = None, sandbox: bool = False) -> None:
    from core.services.signals.signal_builder import evaluate_signals, evaluate_signals_sandboxed, load_monitoring_context
    from core.services.investigation.investigation_runner import run_investigation

    # Build a custom monitoring_context if CLI args provided
    monitoring_context = None
    if customer:
        target = {"customer_name": customer}
        if service_tree_id:
            target["service_tree_ids"] = [{"id": service_tree_id, "name": ""}]
        monitoring_context = {"targets": [target]}
        logger.info("Using CLI target: %s", target)

    # Inject explicit time window into monitoring_context
    if start_time or end_time:
        if monitoring_context is None:
            monitoring_context = load_monitoring_context()
        if start_time:
            monitoring_context["start_time"] = start_time
        if end_time:
            monitoring_context["end_time"] = end_time
        logger.info("Time window: %s → %s", monitoring_context.get("start_time", "default"), monitoring_context.get("end_time", "default"))

    results = await (evaluate_signals_sandboxed if sandbox else evaluate_signals)(monitoring_context=monitoring_context)

    if not results:
        logger.info("No signal results returned.")
        return

    for r in results:
        logger.info(
            "Result: %s/%s — action=%s, signals=%d, compounds=%d",
            r.customer_name, r.service_tree_id, r.action,
            len(r.all_activated_signals), len(r.activated_compounds),
        )
        for s in r.all_activated_signals:
            logger.info(
                "  [%s] %s — strength=%.3f, confidence=%s",
                s.signal_type_id, s.signal_name, s.strength, s.confidence,
            )

    # ── Run actionable investigations in parallel ──────────────────
    actionable = [r for r in results if r.action == "invoke_group_chat"]
    if not actionable:
        logger.info("No investigations to run.")
        return

    max_concurrent = 5
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _run_one(r: object) -> None:
        async with semaphore:
            logger.info("Running investigation for %s/%s ...", r.customer_name, r.service_tree_id)
            async for event in run_investigation(r):
                etype = event.get("type", "unknown")
                if etype == "investigation_started":
                    logger.info("  [%s] Investigation started", event["investigation_id"])
                elif etype == "investigation_agent_response":
                    logger.info(
                        "  [%s] %s responded (phase=%s)",
                        event["investigation_id"], event["agent"], event["phase"],
                    )
                elif etype == "investigation_complete":
                    logger.info(
                        "  [%s] Investigation complete: %d symptoms, %d hypotheses, "
                        "%d evidence, %d actions (%.1fs)",
                        event["investigation_id"],
                        event["symptoms_count"], event["hypotheses_count"],
                        event["evidence_count"], event["actions_count"],
                        event["duration_seconds"],
                    )
                elif etype == "investigation_error":
                    logger.error("  [%s] Investigation error: %s",
                                 event.get("investigation_id", "?"), event.get("error"))

    logger.info("Launching %d investigations (max_concurrent=%d)", len(actionable), max_concurrent)
    async with asyncio.TaskGroup() as tg:
        for r in actionable:
            tg.create_task(_run_one(r))

    for r in results:
        if r.action != "invoke_group_chat":
            logger.info("  %s/%s — action=%s, no investigation.", r.customer_name, r.service_tree_id, r.action)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run signal builder + investigation pipeline")
    parser.add_argument("--customer", type=str, default=None, help="Customer name override")
    parser.add_argument("--service-tree-id", type=str, default=None, help="Service tree ID override")
    parser.add_argument("--start-time", type=str, default=None, help="Investigation start time (ISO8601 UTC, e.g. 2024-01-15T10:00:00Z)")
    parser.add_argument("--end-time", type=str, default=None, help="Investigation end time (ISO8601 UTC, e.g. 2024-01-15T14:00:00Z)")
    parser.add_argument("--sandbox", action="store_true", default=False, help="Use sandboxed 3-stage evaluation pipeline")
    args = parser.parse_args()

    asyncio.run(main(args.customer, args.service_tree_id, args.start_time, args.end_time, sandbox=args.sandbox))
