"""
Correlator — groups outcomes by customer within a time window.

Flush triggers (whichever comes first):
  1. All services listed in monitoring_context.json for the customer have reported.
  2. Max-wait timer (correlation_window_minutes) elapses since the first message in the buffer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time as _time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from agent_framework import Agent

from helper.monitoring_context import MonitoringContext
from helper.agent_logger import (
    AgentLogger,
    generate_correlation_id,
    set_current_correlation_id,
    set_current_customer_name,
    set_current_outcome_xcvs,
)
from helper.azure_clients import (
    delete_buffered_outcomes,
    fetch_outcomes,
    list_buffered_customers,
    list_buffered_outcomes,
    upsert_buffered_outcome,
    upsert_correlation_index,
    upsert_interpreter_run,
)
from models.schemas import (
    CorrelationGroup,
    CorrelationIndexEntry,
    InterpreterRun,
    OutcomeMessage,
)
from core.composer import ActionComposer
from core.dedup import deduplicate_actions

logger = logging.getLogger(__name__)
tracker = AgentLogger.get_instance()


def _extract_json_object(text: str) -> dict[str, Any]:
    """Tolerantly extract a single JSON object from an LLM response.

    Handles three common failure modes the strict ``json.loads`` chokes on:
    1. ```json ... ``` markdown fences around the JSON.
    2. Leading/trailing prose ("Here is the result: { ... } Hope this helps").
    3. Extra trailing tokens after a valid object (the "Extra data: line N
       column 1" error). We use ``raw_decode`` to consume only the first
       valid JSON value and ignore the tail.
    """
    if text is None:
        raise ValueError("Empty response from agent")
    s = text.strip()
    # Strip code fences if present.
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s[3:]
        if s.endswith("```"):
            s = s[:-3]
        s = s.strip()
    # Find the first '{' — anything before it is prose.
    start = s.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in response (preview: {text[:200]!r})")
    decoder = json.JSONDecoder()
    try:
        obj, _end = decoder.raw_decode(s[start:])
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"Could not parse JSON from agent response at offset {start} "
            f"(error={exc}; preview={text[:200]!r})"
        ) from exc
    if not isinstance(obj, dict):
        raise ValueError(f"Expected JSON object, got {type(obj).__name__}")
    return obj


class Correlator:
    """Per-customer correlation buffer with smart-flush."""

    def __init__(
        self,
        agents: dict[str, Agent],
        monitoring: MonitoringContext,
    ) -> None:
        self._monitoring = monitoring
        self._buffer: dict[str, list[OutcomeMessage]] = defaultdict(list)
        self._timers: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        # Per-customer window correlation_id. Minted when the first message
        # of a new window arrives; reused by every subsequent message in the
        # same window; cleared on flush so the next window gets a fresh id.
        # This SAME id becomes the CorrelationGroup.correlation_id at flush
        # time, so a single value identifies the window end-to-end (logs,
        # Cosmos docs, ADLS paths).
        self._window_correlation_id: dict[str, str] = {}
        self._correlator_agent = agents["correlator"]
        self._composer = ActionComposer(agents)
        # Bound concurrent flushes so a single high-volume customer (or a
        # restart-time restore burst) cannot starve agent capacity for the rest.
        max_flushes = max(1, int(os.getenv("INTERPRETER_MAX_CONCURRENT_FLUSHES", "3")))
        self._flush_semaphore = asyncio.Semaphore(max_flushes)
        # Strong-refs to background flush tasks so the GC does not collect
        # them mid-pipeline. Tasks self-discard via add_done_callback.
        self._background_flushes: set[asyncio.Task] = set()
        logger.info("Correlator flush concurrency limit: %d", max_flushes)

    async def restore_from_cosmos(self) -> None:
        """Reload any buffered outcomes from the Cosmos buffer container.

        Called once at startup so a restart (or a fresh replica scaling up)
        does not lose in-flight correlation windows. Re-arms the max-wait
        timer for each customer that has buffered outcomes; if all expected
        services are already present, flushes immediately.
        """
        try:
            customers = await list_buffered_customers()
        except Exception:
            logger.exception("restore_from_cosmos: failed to list buffered customers")
            return
        if not customers:
            logger.info("restore_from_cosmos: no buffered outcomes to restore")
            return
        logger.info("restore_from_cosmos: restoring buffer for %d customer(s)", len(customers))
        for customer in customers:
            try:
                msgs = await list_buffered_outcomes(customer)
            except Exception:
                logger.exception("restore_from_cosmos: failed to load buffer for %s", customer)
                continue
            if not msgs:
                continue
            # Reuse a correlation_id from a buffered message if present
            # (preserves the original window identity across restarts);
            # otherwise mint a fresh one for the restored window.
            restored_cid = next((m.correlation_id for m in msgs if m.correlation_id), "") or generate_correlation_id()
            async with self._locks[customer]:
                self._buffer[customer].extend(msgs)
                self._window_correlation_id.setdefault(customer, restored_cid)
                if customer not in self._timers or self._timers[customer].done():
                    self._timers[customer] = asyncio.create_task(
                        self._flush_after_window(customer)
                    )
            logger.info(
                "restore_from_cosmos: customer=%s restored=%d cid=%s",
                customer, len(msgs), restored_cid,
            )

    def has_open_window(self, customer_name: str) -> bool:
        """Return True if a correlation window is currently open for this
        customer (i.e. a window cid has been minted and not yet retired by
        ``_flush``). Used by the collector to decide whether to clear the
        per-task correlation_id contextvar after settling a message that
        triggered a flush.
        """
        return bool(self._window_correlation_id.get(customer_name))

    def get_or_create_window_correlation_id(self, customer_name: str) -> str:
        """Return the current window's correlation_id for ``customer_name``,
        minting one if no window is currently open. Called by the collector
        BEFORE ingesting a message so all per-message logs carry the right
        correlation_id and the same id flows through to the eventual
        ``CorrelationGroup``, ``InterpreterRun`` and ``ActionPlan``.
        """
        cid = self._window_correlation_id.get(customer_name)
        if not cid:
            cid = generate_correlation_id()
            self._window_correlation_id[customer_name] = cid
            tracker.log_window_opened(customer_name, cid)
        return cid

    async def ingest(self, msg: OutcomeMessage) -> None:
        """Add an outcome message to the buffer; flush if all expected services arrived."""
        customer = msg.customer_name
        # Make sure the buffered doc carries the window correlation_id so a
        # restart can recover it.
        if not msg.correlation_id:
            msg.correlation_id = self.get_or_create_window_correlation_id(customer)
        flush_now = False
        # Persist FIRST (durable buffer, survives restarts; visible across replicas).
        # If this fails, we surface the error to the caller — the SB message will be
        # abandoned so it can be redelivered.
        await upsert_buffered_outcome(msg)
        async with self._locks[customer]:
            self._buffer[customer].append(msg)

            received_ids = {m.service_tree_id for m in self._buffer[customer] if m.service_tree_id}
            received_names = {m.service_name for m in self._buffer[customer] if m.service_name}

            tracked = self._monitoring.is_tracked(customer)
            all_in = (
                tracked
                and self._monitoring.all_expected_received(customer, received_ids, received_names)
            )

            tracker.log_message_buffered(customer, msg.xcv, len(self._buffer[customer]), all_in)
            logger.info(
                "ingest customer=%s xcv=%s service=%s buffered=%d tracked=%s all_received=%s",
                customer, msg.xcv, msg.service_name or msg.service_tree_id or "?",
                len(self._buffer[customer]), tracked, all_in,
            )

            if all_in:
                # Cancel pending max-wait timer; we'll flush right after releasing the lock
                timer = self._timers.pop(customer, None)
                if timer and not timer.done():
                    timer.cancel()
                flush_now = True
            else:
                # Start (do NOT reset) the max-wait timer
                if customer not in self._timers or self._timers[customer].done():
                    self._timers[customer] = asyncio.create_task(
                        self._flush_after_window(customer)
                    )

        if flush_now:
            # Run the flush as a detached background task so the caller
            # (collector → SB session) is NOT blocked by the multi-minute
            # correlator + action_composer pipeline. The message is already
            # durable in Cosmos via ``upsert_buffered_outcome`` above, so a
            # crash mid-flush is recoverable: ``restore_from_cosmos`` rebuilds
            # the buffer on the next startup. Keeping the flush inline here
            # was the root cause of ``SessionLockLostError`` at
            # ``complete_message`` for high-fan-out customers.
            task = asyncio.create_task(
                self._flush(customer, reason="all_services_received")
            )
            self._background_flushes.add(task)
            task.add_done_callback(self._background_flushes.discard)

    async def _flush_after_window(self, customer: str) -> None:
        """Wait for the correlation window, then flush whatever is buffered."""
        try:
            await asyncio.sleep(int(os.getenv("INTERPRETER_CORRELATION_WINDOW_MINUTES", "30")) * 60)
        except asyncio.CancelledError:
            return
        await self._flush(customer, reason="max_wait_elapsed")

    async def _flush(self, customer: str, *, reason: str = "unspecified") -> None:
        """Process all buffered outcomes for a customer."""
        async with self._locks[customer]:
            messages = self._buffer.pop(customer, [])
            timer = self._timers.pop(customer, None)
            if timer and not timer.done():
                timer.cancel()
            # Pop the window correlation_id together with the messages so
            # the next window for this customer gets a fresh one. Fall back
            # to a newly minted id if none was set (defensive).
            window_cid = self._window_correlation_id.pop(customer, "") or generate_correlation_id()

        if not messages:
            return

        # Pin the window correlation_id on the current task's context so
        # every log line emitted by the pipeline (correlator agent, dedup,
        # composer, cosmos upserts, ADLS writes) carries the same id.
        set_current_correlation_id(window_cid)
        set_current_customer_name(customer)
        tracker.log_window_flushing(customer, reason, len(messages))

        # Backpressure: bound the number of concurrent agent pipelines so a
        # single noisy customer cannot starve everyone else.
        async with self._flush_semaphore:
            await self._run_pipeline(customer, messages, reason, window_cid)

    async def _run_pipeline(
        self, customer: str, messages: list[OutcomeMessage], reason: str, window_cid: str
    ) -> None:
        """Run the correlator + dedup + composer pipeline for one flush batch."""
        # Re-pin in case this coroutine resumed on a different task.
        set_current_correlation_id(window_cid)
        set_current_customer_name(customer)
        logger.info(
            "Flushing %d outcome(s) for customer=%s cid=%s (reason=%s)",
            len(messages), customer, window_cid, reason,
        )

        outcomes = await fetch_outcomes([m.xcv for m in messages])
        # Drop persisted buffer entries now that we've taken ownership of them.
        # Do this before the (potentially long) agent pipeline so a subsequent
        # restart doesn't double-process. If the pipeline below fails, the
        # outcomes are still safe in the outcomes container; we just won't
        # auto-retry them.
        try:
            await delete_buffered_outcomes(customer, [m.xcv for m in messages])
        except Exception:
            logger.exception("Failed to clear buffered outcomes for customer=%s", customer)

        now = datetime.now(timezone.utc)

        # ── Short-circuit: window contains only non-actionable outcomes ──
        # If every outcome in this window reported a terminal non-actionable
        # status (no_signal / no_hypotheses / error), there is nothing for the
        # correlator + composer LLMs to reason about. Record an audit trail
        # entry and skip the agent pipeline entirely to save tokens/latency.
        _NON_ACTIONABLE_STATUSES = {"no_signal", "no_hypotheses", "error"}
        if outcomes and all(
            (getattr(o, "status", "") or "") in _NON_ACTIONABLE_STATUSES
            for o in outcomes
        ):
            window_start_iso = (
                now - timedelta(minutes=int(os.getenv("INTERPRETER_CORRELATION_WINDOW_MINUTES", "30")))
            ).isoformat()
            status_counts: dict[str, int] = {}
            for o in outcomes:
                s = getattr(o, "status", "") or "unknown"
                status_counts[s] = status_counts.get(s, 0) + 1
            skip_reason = "all_outcomes_non_actionable:" + ",".join(
                f"{k}={v}" for k, v in sorted(status_counts.items())
            )
            logger.info(
                "Skipping correlator+composer pipeline for customer=%s cid=%s: %s",
                customer, window_cid, skip_reason,
            )
            skipped_run = InterpreterRun(
                id=window_cid,
                correlation_id=window_cid,
                customer_name=customer,
                xcvs=[o.xcv for o in outcomes],
                window_start=window_start_iso,
                window_end=now.isoformat(),
                status="skipped_no_signal",
                outcomes_count=len(outcomes),
                actions_count=0,
                error=skip_reason,
                started_at=now.isoformat(),
                completed_at=now.isoformat(),
            )
            try:
                await upsert_interpreter_run(skipped_run)
            except Exception:
                logger.exception(
                    "Failed to persist skipped_no_signal run for customer=%s cid=%s",
                    customer, window_cid,
                )
            return

        group = CorrelationGroup(
            customer_name=customer,
            outcomes=outcomes,
            window_start=now - timedelta(minutes=int(os.getenv("INTERPRETER_CORRELATION_WINDOW_MINUTES", "30"))),
            window_end=now,
            correlation_id=window_cid,
        )

        # Set context for logging (cid is already pinned above; this is just
        # the outcome xcvs for the per-task ContextVar).
        set_current_outcome_xcvs([o.xcv for o in outcomes])
        tracker.log_correlation_started(group.correlation_id, group.customer_name, len(outcomes))

        # Create audit trail record
        run = InterpreterRun(
            id=group.correlation_id,
            correlation_id=group.correlation_id,
            customer_name=group.customer_name,
            xcvs=[o.xcv for o in outcomes],
            window_start=group.window_start.isoformat(),
            window_end=group.window_end.isoformat(),
            status="started",
            outcomes_count=len(outcomes),
            started_at=now.isoformat(),
        )
        await upsert_interpreter_run(run)

        # Track which stage we're currently in and which timings have been
        # recorded successfully so the failure record reports them accurately
        # without mutating ``run`` mid-flight.
        current_stage = "correlator"
        correlator_ms_done: float | None = None
        composer_ms_done: float | None = None

        try:
            # Run correlator agent for cross-investigation reasoning
            t0 = _time.perf_counter()
            correlations = await self._run_correlator_agent(group)
            correlator_ms = (_time.perf_counter() - t0) * 1000
            correlator_ms_done = correlator_ms

            # Update audit trail: correlator done
            run.status = "correlator_done"
            run.correlator_duration_ms = correlator_ms
            await upsert_interpreter_run(run)

            # Index correlation patterns
            current_stage = "index"
            await self._index_correlations(group, correlations, upsert_correlation_index)

            # Dedup → Compose (pass correlations to composer)
            current_stage = "dedup"
            deduped = await deduplicate_actions(group)
            current_stage = "composer"
            t1 = _time.perf_counter()
            # OutcomeDocument has no service_name; the buffered OutcomeMessages
            # do. Build a {xcv: service_name} map so per_service actions get
            # labelled with the customer's real primary service instead of a
            # placeholder string.
            xcv_service_names = {
                m.xcv: m.service_name for m in messages if m.service_name
            }
            plan = await self._composer.compose(
                group, deduped, correlations, xcv_service_names=xcv_service_names,
            )
            composer_ms = (_time.perf_counter() - t1) * 1000
            composer_ms_done = composer_ms

            # Final audit trail update
            run.status = "composed"
            run.composer_duration_ms = composer_ms
            run.actions_count = len(plan.actions) if plan else 0
            run.completed_at = datetime.now(timezone.utc).isoformat()
            await upsert_interpreter_run(run)

        except Exception as exc:
            logger.error(
                "Pipeline failed for %s at stage=%s: %s",
                group.correlation_id, current_stage, exc,
            )
            # Build a fresh failed record so the audit trail clearly shows the
            # failure without any partial state from later (un-run) stages
            # leaking into the document.
            failed_run = InterpreterRun(
                id=group.correlation_id,
                correlation_id=group.correlation_id,
                customer_name=group.customer_name,
                xcvs=run.xcvs,
                window_start=run.window_start,
                window_end=run.window_end,
                status="failed",
                correlator_duration_ms=correlator_ms_done,
                composer_duration_ms=composer_ms_done,
                outcomes_count=run.outcomes_count,
                actions_count=0,
                error=f"[{current_stage}] {exc}"[:2000],
                started_at=run.started_at,
                completed_at=datetime.now(timezone.utc).isoformat(),
            )
            await upsert_interpreter_run(failed_run)

    async def _index_correlations(
        self,
        group: CorrelationGroup,
        correlations: dict[str, Any],
        upsert_fn,
    ) -> None:
        """Write each discovered correlation pattern to the correlation_index container."""
        now_iso = datetime.now(timezone.utc).isoformat()
        for i, corr in enumerate(correlations.get("correlations", [])):
            entry = CorrelationIndexEntry(
                id=f"{group.correlation_id}_{i}",
                correlation_id=group.correlation_id,
                customer_name=group.customer_name,
                pattern_type=corr.get("pattern_type", "unknown"),
                description=corr.get("description", ""),
                confidence=corr.get("confidence", "medium"),
                related_xcvs=corr.get("related_xcvs", []),
                shared_resources=corr.get("common_resources", []),
                statistical_evidence=corr.get("statistical_evidence", ""),
                window_start=group.window_start.isoformat(),
                window_end=group.window_end.isoformat(),
                created_at=now_iso,
            )
            await upsert_fn(entry)

    async def _run_correlator_agent(self, group: CorrelationGroup) -> dict[str, Any]:
        """Run the correlator agent over the group to identify correlations."""
        context = {
            "customer_name": group.customer_name,
            "correlation_id": group.correlation_id,
            "window_start": group.window_start.isoformat(),
            "window_end": group.window_end.isoformat(),
            "outcomes": [o.model_dump() for o in group.outcomes],
        }

        user_message = json.dumps(context, default=str)
        tracker.log_agent_invoked("correlator", user_message)

        t0 = _time.perf_counter()
        try:
            response = await self._correlator_agent.run(user_message)
            duration_ms = (_time.perf_counter() - t0) * 1000
            result = _extract_json_object(response.text)
            tracker.log_agent_completed("correlator", response.text, duration_ms)
            logger.info(
                "Correlator agent found %d correlations for %s",
                len(result.get("correlations", [])),
                group.correlation_id,
            )
            return result
        except Exception as exc:
            tracker.log_agent_error("correlator", str(exc))
            logger.error("Correlator agent failed for %s: %s", group.correlation_id, exc)
            return {"correlations": [], "themes": [], "recommended_grouping": "single_plan", "reasoning": "Agent error — defaulting to single plan."}
