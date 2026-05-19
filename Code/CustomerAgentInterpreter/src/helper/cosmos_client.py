"""Cosmos DB client helpers for the Interpreter service."""
from __future__ import annotations

import logging
from typing import Any

from helper import env
from helper.azure_clients import get_cosmos_client
from models.schemas import ActionLedgerEntry, ActionPlan, CorrelationIndexEntry, InterpreterRun, OutcomeDocument

logger = logging.getLogger(__name__)


async def fetch_outcomes(xcvs: list[str]) -> list[OutcomeDocument]:
    """Fetch outcome documents by XCV from Cosmos DB.

    The outcomes container is partitioned on /xcv, with id = xcv.
    """
    results: list[OutcomeDocument] = []
    async with get_cosmos_client() as client:
        container = (
            client.get_database_client(env.COSMOS_DATABASE)
            .get_container_client(env.COSMOS_OUTCOMES_CONTAINER)
        )
        for xcv in xcvs:
            try:
                item = await container.read_item(item=xcv, partition_key=xcv)
                results.append(OutcomeDocument(**item))
            except Exception:
                logger.warning("Could not fetch outcome for xcv=%s", xcv)
    return results


async def upsert_action_plan(plan: ActionPlan) -> None:
    """Upsert an action plan to the action_ledger container."""
    async with get_cosmos_client() as client:
        container = (
            client.get_database_client(env.COSMOS_DATABASE)
            .get_container_client(env.COSMOS_ACTIONS_CONTAINER)
        )
        doc = plan.model_dump()
        doc["id"] = plan.correlation_id
        await container.upsert_item(doc)


async def query_ledger_entry(
    fingerprint: str, customer_name: str
) -> ActionLedgerEntry | None:
    """Look up a single action ledger entry by fingerprint (id) and customer_name (partition key)."""
    async with get_cosmos_client() as client:
        container = (
            client.get_database_client(env.COSMOS_DATABASE)
            .get_container_client(env.COSMOS_ACTIONS_CONTAINER)
        )
        try:
            item = await container.read_item(item=fingerprint, partition_key=customer_name)
            return ActionLedgerEntry(**item)
        except Exception:
            return None


async def upsert_ledger_entry(entry: ActionLedgerEntry) -> None:
    """Upsert an action ledger entry (partition key: customer_name)."""
    async with get_cosmos_client() as client:
        container = (
            client.get_database_client(env.COSMOS_DATABASE)
            .get_container_client(env.COSMOS_ACTIONS_CONTAINER)
        )
        await container.upsert_item(entry.model_dump())


async def upsert_interpreter_run(run: InterpreterRun) -> None:
    """Upsert an interpreter run audit record (partition key: customer_name)."""
    async with get_cosmos_client() as client:
        container = (
            client.get_database_client(env.COSMOS_DATABASE)
            .get_container_client(env.COSMOS_RUNS_CONTAINER)
        )
        doc = run.model_dump()
        doc["id"] = run.correlation_id
        await container.upsert_item(doc)


async def upsert_correlation_index(entry: CorrelationIndexEntry) -> None:
    """Upsert a correlation index entry (partition key: customer_name)."""
    async with get_cosmos_client() as client:
        container = (
            client.get_database_client(env.COSMOS_DATABASE)
            .get_container_client(env.COSMOS_CORRELATION_INDEX_CONTAINER)
        )
        await container.upsert_item(entry.model_dump())
