"""Drain ALL messages from the Interpreter's Service Bus subscription.

Drains both the active queue and the dead-letter sub-queue using
RECEIVE_AND_DELETE (no settle step, fastest). Handles session-enabled
subscriptions via NEXT_AVAILABLE_SESSION until no more sessions remain.

Usage:
    python scripts/purge_servicebus.py

Env vars (read from process environment / .env):
    PUBLISHER_SERVICEBUS_FQNS       (e.g. sbn-ratio-ai-dev.servicebus.windows.net)
    INTERPRETER_SERVICEBUS_TOPIC    (e.g. customeragent-outcome)
    INTERPRETER_SERVICEBUS_SUBSCRIPTION (e.g. interpreter-sub)
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path

from azure.identity.aio import DefaultAzureCredential
from azure.servicebus import NEXT_AVAILABLE_SESSION, ServiceBusReceiveMode, ServiceBusSubQueue
from azure.servicebus.aio import ServiceBusClient
from azure.servicebus.exceptions import OperationTimeoutError, ServiceBusError

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
# Silence noisy AMQP / identity / pipeline logs — keep only our own log lines.
for noisy in ("azure", "azure.servicebus", "azure.identity", "azure.core", "uamqp"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
log = logging.getLogger("purge_sb")

FQNS = os.getenv("PUBLISHER_SERVICEBUS_FQNS", "sbn-ratio-ai-dev.servicebus.windows.net")
TOPIC = os.getenv("INTERPRETER_SERVICEBUS_TOPIC", "customeragent-outcome")
SUB = os.getenv("INTERPRETER_SERVICEBUS_SUBSCRIPTION", "interpreter-sub")

BATCH = 100
WAIT = 5


async def _drain_sessioned(client: ServiceBusClient, sub_queue: ServiceBusSubQueue | None) -> int:
    """Drain a session-enabled (sub-)queue. Returns count of messages drained."""
    total = 0
    label = sub_queue.value if sub_queue else "active"
    while True:
        try:
            receiver = client.get_subscription_receiver(
                topic_name=TOPIC,
                subscription_name=SUB,
                session_id=NEXT_AVAILABLE_SESSION,
                receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE,
                sub_queue=sub_queue,
                max_wait_time=WAIT,
            )
        except OperationTimeoutError:
            log.info("[%s] no more sessions", label)
            break
        try:
            async with receiver:
                sid = receiver.session.session_id if receiver.session else "?"
                session_total = 0
                while True:
                    msgs = await receiver.receive_messages(max_message_count=BATCH, max_wait_time=WAIT)
                    if not msgs:
                        break
                    session_total += len(msgs)
                if session_total:
                    log.info("[%s] session=%s drained=%d", label, sid, session_total)
                total += session_total
        except OperationTimeoutError:
            log.info("[%s] no more sessions", label)
            break
        except ServiceBusError as e:
            # Some DLQ entities aren't sessioned even when the parent is — fall through to non-sessioned.
            log.warning("[%s] session receive failed (%s); will try non-sessioned", label, e)
            return -1
    return total


async def _drain_nonsessioned(client: ServiceBusClient, sub_queue: ServiceBusSubQueue | None) -> int:
    total = 0
    label = sub_queue.value if sub_queue else "active"
    async with client.get_subscription_receiver(
        topic_name=TOPIC,
        subscription_name=SUB,
        receive_mode=ServiceBusReceiveMode.RECEIVE_AND_DELETE,
        sub_queue=sub_queue,
        max_wait_time=WAIT,
    ) as receiver:
        while True:
            msgs = await receiver.receive_messages(max_message_count=BATCH, max_wait_time=WAIT)
            if not msgs:
                break
            total += len(msgs)
            log.info("[%s] drained batch=%d total=%d", label, len(msgs), total)
    return total


async def main() -> int:
    log.info("Purging  fqns=%s  topic=%s  sub=%s", FQNS, TOPIC, SUB)
    # Skip the slow IMDS probe when running locally — fall straight to az CLI / VS Code.
    cred = DefaultAzureCredential(exclude_managed_identity_credential=True)
    grand_total = 0
    try:
        async with ServiceBusClient(FQNS, cred) as client:
            # Active queue is sessioned. DLQ is always non-sessioned, even when
            # the parent subscription requires sessions.
            active = await _drain_sessioned(client, None)
            if active == -1:
                active = await _drain_nonsessioned(client, None)
            log.info("[active] DONE drained=%d", active)
            dlq = await _drain_nonsessioned(client, ServiceBusSubQueue.DEAD_LETTER)
            log.info("[deadletter] DONE drained=%d", dlq)
            grand_total = active + dlq
    finally:
        await cred.close()
    log.info("PURGE COMPLETE  total messages deleted=%d", grand_total)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
