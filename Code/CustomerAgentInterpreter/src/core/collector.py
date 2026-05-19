"""
Collector — listens to a session-enabled Service Bus subscription for
outcome notifications, decodes them, and forwards to the correlator.

Pattern:
- N parallel worker tasks (configurable via INTERPRETER_MAX_CONCURRENT_SESSIONS).
- Each worker loops: accept_next_session() → drain messages until idle → release.
- Session id == customer_name (set by the publisher), so all of one customer's
  outcomes are processed by a single worker at a time (preserves per-customer ordering).
- Poison messages (JSON / Pydantic errors) → dead-letter.
- Transient failures (Cosmos hiccup, agent error) → abandon for redelivery.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os

from azure.core.exceptions import ServiceRequestError
from azure.servicebus import NEXT_AVAILABLE_SESSION
from azure.servicebus.aio import AutoLockRenewer
from azure.servicebus.exceptions import (
    OperationTimeoutError,
    ServiceBusError,
    SessionLockLostError,
)
from pydantic import ValidationError

from helper.agent_logger import (
    AgentLogger,
    set_current_correlation_id,
    set_current_customer_name,
)
from helper.azure_clients import get_servicebus_client
from models.schemas import OutcomeMessage
from core.correlator import Correlator

logger = logging.getLogger(__name__)
tracker = AgentLogger.get_instance()


class OutcomeCollector:
    """Consumes outcome notifications from a session-enabled SB subscription."""

    def __init__(self, correlator: Correlator) -> None:
        self._correlator = correlator
        self._stopped = asyncio.Event()

    async def listen(self) -> None:
        """Run N session-receiver workers in parallel until cancelled."""
        n = max(1, int(os.getenv("INTERPRETER_MAX_CONCURRENT_SESSIONS", "5")))
        topic = os.getenv("INTERPRETER_SERVICEBUS_TOPIC", "")
        subscription = os.getenv("INTERPRETER_SERVICEBUS_SUBSCRIPTION", "")
        if not topic or not subscription:
            raise RuntimeError(
                "INTERPRETER_SERVICEBUS_TOPIC and INTERPRETER_SERVICEBUS_SUBSCRIPTION must be set"
            )
        logger.info(
            "Collector starting: %d worker(s) on %s/%s",
            n, topic, subscription,
        )
        try:
            await asyncio.gather(*[self._worker(i, topic, subscription) for i in range(n)])
        except asyncio.CancelledError:
            logger.info("Collector cancelled; shutting down workers")
            self._stopped.set()
            raise

    # ── Worker loop ───────────────────────────────────────────

    async def _worker(self, worker_id: int, topic: str, subscription: str) -> None:
        """One worker: keep accepting sessions, draining, repeating."""
        backoff = 1.0
        while not self._stopped.is_set():
            try:
                sb = await get_servicebus_client()
                backoff = 1.0  # reset after a successful client open
                while not self._stopped.is_set():
                    try:
                        # azure-servicebus async API: get a subscription receiver
                        # bound to NEXT_AVAILABLE_SESSION; the underlying AMQP
                        # session is acquired lazily on first receive_messages().
                        session_receiver = sb.get_subscription_receiver(
                            topic_name=topic,
                            subscription_name=subscription,
                            session_id=NEXT_AVAILABLE_SESSION,
                            max_wait_time=int(os.getenv("INTERPRETER_SESSION_IDLE_TIMEOUT_SECONDS", "30")),
                        )
                    except OperationTimeoutError:
                        # No sessions available right now — loop and try again
                        continue

                    async with session_receiver:
                        # Reset per-task logging contextvars so any log lines
                        # emitted before the first message is processed do
                        # not carry the previous session's correlation_id /
                        # customer name from this worker task.
                        set_current_correlation_id(None)
                        set_current_customer_name(None)
                        try:
                            customer = session_receiver.session.session_id or "<unknown>"
                        except Exception:
                            customer = "<unknown>"
                        # Keep the session lock alive while we drain. The default
                        # session lock duration (~30s) is shorter than a single
                        # flush of the correlator + action_composer pipeline,
                        # so without auto-renewal we hit SessionLockLostError
                        # at complete_message time and the message gets
                        # redelivered. Renew up to ``max_lock_renewal_duration``
                        # seconds (configurable; default 10 minutes).
                        renewer = AutoLockRenewer()
                        try:
                            renewer.register(
                                session_receiver,
                                session_receiver.session,
                                max_lock_renewal_duration=float(
                                    os.getenv("INTERPRETER_SESSION_MAX_LOCK_RENEWAL_SECONDS", "600")
                                ),
                            )
                        except Exception:
                            logger.exception(
                                "[worker-%d] failed to register session auto-renew (session=%s)",
                                worker_id, customer,
                            )
                        logger.info(
                            "[worker-%d] accepted session for customer=%s",
                            worker_id, customer,
                        )
                        try:
                            await self._drain_session(session_receiver, customer)
                        finally:
                            try:
                                await renewer.close()
                            except Exception:
                                logger.debug(
                                    "[worker-%d] auto-renewer close failed (session=%s)",
                                    worker_id, customer,
                                )

            except asyncio.CancelledError:
                raise
            except (ServiceBusError, ServiceRequestError) as exc:
                logger.warning(
                    "[worker-%d] SB transport error (%s); retrying in %.1fs",
                    worker_id, exc, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            except Exception:
                logger.exception(
                    "[worker-%d] unexpected error; restarting in %.1fs",
                    worker_id, backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)

    # ── Per-session message loop ───────────────────────────────────

    async def _drain_session(self, receiver, customer: str) -> None:
        """Pull messages from a session until it goes idle, then release it."""
        idle_seconds = 0.0
        batch_size = int(os.getenv("INTERPRETER_RECEIVE_BATCH_SIZE", "10"))
        max_wait = int(os.getenv("INTERPRETER_RECEIVE_MAX_WAIT_SECONDS", "5"))
        idle_timeout = int(os.getenv("INTERPRETER_SESSION_IDLE_TIMEOUT_SECONDS", "30"))
        while not self._stopped.is_set():
            try:
                msgs = await receiver.receive_messages(
                    max_message_count=batch_size,
                    max_wait_time=max_wait,
                )
            except SessionLockLostError:
                logger.warning("[session=%s] lock lost; releasing", customer)
                return
            except ServiceBusError as exc:
                logger.warning("[session=%s] receive failed: %s", customer, exc)
                return

            if not msgs:
                idle_seconds += max_wait
                if idle_seconds >= idle_timeout:
                    logger.info("[session=%s] idle; releasing", customer)
                    return
                continue

            idle_seconds = 0.0
            for msg in msgs:
                await self._process_message(receiver, msg, customer)

    # ── Per-message handling ──────────────────────────────────────

    async def _process_message(self, receiver, msg, customer: str) -> None:
        """Parse and dispatch a single message; settle on the broker accordingly."""
        # Make sure customer-name context is set even before we know the
        # window correlation_id.
        set_current_customer_name(customer)
        # 1. Decode body (it may arrive as a generator of bytes chunks)
        try:
            body_bytes = b"".join(
                chunk if isinstance(chunk, (bytes, bytearray)) else str(chunk).encode("utf-8")
                for chunk in msg.body
            )
            body_text = body_bytes.decode("utf-8")
            payload = json.loads(body_text)
            outcome = OutcomeMessage(**payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValidationError) as exc:
            # Malformed/unschema'd → dead-letter, no point retrying
            logger.error(
                "[session=%s] poison message (msg_id=%s): %s",
                customer, getattr(msg, "message_id", "?"), exc,
            )
            try:
                await receiver.dead_letter_message(
                    msg,
                    reason="parse_error",
                    error_description=f"{type(exc).__name__}: {exc}"[:1000],
                )
            except Exception:
                logger.exception("[session=%s] failed to dead-letter poison msg", customer)
            return

        # Pin the window correlation_id to this task's context so every
        # downstream log (correlator buffer-write, agent calls, cosmos
        # upserts, ADLS writes) carries the same id. The correlator hands
        # out one id per customer-window and reuses it across the N
        # messages of that batch.
        cid = self._correlator.get_or_create_window_correlation_id(outcome.customer_name)
        set_current_correlation_id(cid)
        outcome.correlation_id = cid
        tracker.log_message_received(
            outcome.customer_name, outcome.xcv,
            outcome.service_name or outcome.service_tree_id or "?",
        )

        # 2. Hand off to correlator (may flush immediately if all services received)
        try:
            await self._correlator.ingest(outcome)
        except Exception as exc:
            # ALWAYS log the underlying error with traceback — without this
            # poison messages get DLQ'd silently and we never see the cause.
            logger.exception(
                "[session=%s] correlator.ingest failed for xcv=%s: %s",
                customer, outcome.xcv, exc,
            )
            # Transient failure path: abandon for redelivery so SB / our
            # downstream (Cosmos, agent) can recover. But if the message has
            # already been redelivered ``max_redeliveries`` times we treat it
            # as poison and DLQ explicitly so it doesn't loop forever just
            # to be killed silently by the broker's max-delivery-count.
            delivery_count = getattr(msg, "delivery_count", 0) or 0
            max_redeliveries = int(os.getenv("INTERPRETER_MAX_REDELIVERIES", "5"))
            if delivery_count >= max_redeliveries:
                logger.error(
                    "[session=%s] xcv=%s repeatedly failed (delivery_count=%d); dead-lettering. Underlying error: %s",
                    customer, outcome.xcv, delivery_count, exc,
                    exc_info=True,
                )
                try:
                    await receiver.dead_letter_message(
                        msg,
                        reason="max_redeliveries_exceeded",
                        error_description=f"{type(exc).__name__}: {exc}"[:1000],
                    )
                except Exception:
                    logger.exception("[session=%s] failed to DLQ exhausted msg", customer)
                return

            logger.warning(
                "[session=%s] transient failure ingesting xcv=%s (delivery_count=%d); abandoning for retry: %s",
                customer, outcome.xcv, delivery_count, exc,
            )
            try:
                await receiver.abandon_message(msg)
            except Exception:
                logger.exception("[session=%s] abandon failed", customer)
            return

        # 3. Settle on the broker
        try:
            await receiver.complete_message(msg)
        except Exception:
            logger.exception(
                "[session=%s] failed to complete message xcv=%s (will redeliver)",
                customer, outcome.xcv,
            )
        finally:
            # If correlator.ingest triggered a flush, the window cid for
            # this customer has been retired. Clear the per-task contextvar
            # so the NEXT message in this drain (which starts a new batch)
            # does not log under the just-completed batch's id before
            # _process_message re-pins it.
            try:
                if not self._correlator.has_open_window(outcome.customer_name):
                    set_current_correlation_id(None)
            except Exception:
                pass
