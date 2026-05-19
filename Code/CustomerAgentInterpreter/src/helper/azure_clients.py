"""
Azure client helpers for the Interpreter service.

Low-level context managers for Service Bus / Cosmos / Blob, plus the
domain-level Cosmos helpers (fetch outcomes, upsert ledger / runs / index,
buffer outcomes for in-flight correlation windows).

Also provides sync helpers for the sandbox layer (ADLS Gen2 + Dynamic
Sessions). Those use sync credentials because the sandbox HTTP wrapper
(sandbox/client.py) is sync (urllib + run_in_executor) and the ADLS
data-plane SDK is sync.

Cosmos container partition-key contract (REQUIRED — verified at startup
by ``assert_cosmos_partition_keys``):

    INTERPRETER_COSMOS_OUTCOMES_CONTAINER          → /xcv
    INTERPRETER_COSMOS_ACTIONS_CONTAINER           → /customer_name
    INTERPRETER_COSMOS_RUNS_CONTAINER              → /customer_name
    INTERPRETER_COSMOS_CORRELATION_INDEX_CONTAINER → /customer_name
    INTERPRETER_COSMOS_BUFFER_CONTAINER            → /customer_name

If any container is provisioned with a different partition path, the
upsert/read calls below will fail with HTTP 400 BadRequest. The startup
assertion catches that early.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from azure.identity.aio import DefaultAzureCredential, AzureCliCredential
from azure.cosmos.aio import CosmosClient
from azure.servicebus.aio import ServiceBusClient
from azure.storage.blob.aio import BlobServiceClient

from models.schemas import (
    ActionLedgerEntry,
    ActionPlan,
    CorrelationIndexEntry,
    InterpreterRun,
    OutcomeDocument,
    OutcomeMessage,
)

logger = logging.getLogger(__name__)


def _tracker():
    """Lazy AgentLogger import to avoid circular import at module load."""
    from helper.agent_logger import AgentLogger
    return AgentLogger.get_instance()


# ── Required env vars (validated once at startup) ──────────────────────────
REQUIRED_ENV_VARS: tuple[str, ...] = (
    "INTERPRETER_COSMOS_ENDPOINT",
    "INTERPRETER_COSMOS_DATABASE",
    "INTERPRETER_COSMOS_OUTCOMES_CONTAINER",
    "INTERPRETER_COSMOS_ACTIONS_CONTAINER",
    "INTERPRETER_COSMOS_RUNS_CONTAINER",
    "INTERPRETER_COSMOS_CORRELATION_INDEX_CONTAINER",
    "INTERPRETER_COSMOS_BUFFER_CONTAINER",
    "INTERPRETER_SERVICEBUS_FQNS",
    "INTERPRETER_SERVICEBUS_TOPIC",
    "INTERPRETER_SERVICEBUS_SUBSCRIPTION",
)


def validate_required_env() -> None:
    """Raise RuntimeError if any required env var is unset/empty.

    Called at startup so a misconfigured deployment fails fast instead of
    silently connecting to wrong (or empty) Cosmos containers / SB topics.
    """
    missing = [k for k in REQUIRED_ENV_VARS if not os.getenv(k)]
    if missing:
        raise RuntimeError(
            "Missing required environment variables: " + ", ".join(missing)
        )


# ── Singleton async clients ────────────────────────────────────────────────
# Each helper opens many small ops per second; creating fresh CosmosClient /
# DefaultAzureCredential per call dominates latency and burns the token cache.
# We hold one of each at module level, lazily initialised, and close them on
# service shutdown via ``close_clients()``.

_credential: DefaultAzureCredential | None = None
_cosmos_client: CosmosClient | None = None
_sb_client: ServiceBusClient | None = None
_blob_client: BlobServiceClient | None = None
_clients_lock = asyncio.Lock()
_credential_lock = asyncio.Lock()


async def _get_credential():
    global _credential
    if _credential is None:
        async with _credential_lock:
            if _credential is None:
                # IDENTITY_ENDPOINT is auto-injected by Azure Container Apps /
                # App Service when system-assigned MI is enabled.
                # Locally, DefaultAzureCredential probes ~8 credentials
                # (Env, WorkloadIdentity, SharedTokenCache, VisualStudio,
                # VisualStudioCode, AzureCli, AzurePowerShell, AzureDeveloperCli),
                # several of which can block multiple seconds each on Windows
                # before falling back to az CLI. Skip the chain entirely and
                # use AzureCliCredential directly when local.
                running_in_azure = bool(os.getenv("IDENTITY_ENDPOINT")) or bool(os.getenv("MSI_ENDPOINT"))
                if running_in_azure:
                    _credential = DefaultAzureCredential()
                    logger.info("Credential: DefaultAzureCredential (Azure-hosted)")
                else:
                    _credential = AzureCliCredential()
                    logger.info("Credential: AzureCliCredential (local dev)")
    return _credential


async def get_cosmos_client() -> CosmosClient:
    """Return the process-wide singleton CosmosClient (lazy-init)."""
    global _cosmos_client
    if _cosmos_client is None:
        async with _clients_lock:
            if _cosmos_client is None:
                endpoint = os.getenv("INTERPRETER_COSMOS_ENDPOINT", "")
                logger.info("get_cosmos_client: endpoint=%r", endpoint)
                if not endpoint:
                    raise RuntimeError("INTERPRETER_COSMOS_ENDPOINT is not set")
                logger.info("get_cosmos_client: acquiring credential...")
                cred = await _get_credential()
                logger.info("get_cosmos_client: credential ready, building CosmosClient...")
                _cosmos_client = CosmosClient(url=endpoint, credential=cred)
                logger.info("CosmosClient singleton initialised (%s)", endpoint)
    return _cosmos_client


async def get_servicebus_client() -> ServiceBusClient:
    """Return the process-wide singleton ServiceBusClient (lazy-init)."""
    global _sb_client
    if _sb_client is None:
        async with _clients_lock:
            if _sb_client is None:
                fqns = os.getenv("INTERPRETER_SERVICEBUS_FQNS", "")
                if not fqns:
                    raise RuntimeError("INTERPRETER_SERVICEBUS_FQNS is not set")
                cred = await _get_credential()
                _sb_client = ServiceBusClient(
                    fully_qualified_namespace=fqns,
                    credential=cred,
                )
                logger.info("ServiceBusClient singleton initialised (%s)", fqns)
    return _sb_client


async def get_blob_client() -> BlobServiceClient:
    """Return the process-wide singleton BlobServiceClient (lazy-init)."""
    global _blob_client
    if _blob_client is None:
        async with _clients_lock:
            if _blob_client is None:
                account_url = os.getenv("INTERPRETER_BLOB_ACCOUNT_URL", "")
                if not account_url:
                    raise RuntimeError("INTERPRETER_BLOB_ACCOUNT_URL is not set")
                cred = await _get_credential()
                _blob_client = BlobServiceClient(
                    account_url=account_url,
                    credential=cred,
                )
                logger.info("BlobServiceClient singleton initialised (%s)", account_url)
    return _blob_client


async def close_clients() -> None:
    """Close singleton clients on shutdown. Safe to call multiple times."""
    global _credential, _cosmos_client, _sb_client, _blob_client
    for name, client in (
        ("CosmosClient", _cosmos_client),
        ("ServiceBusClient", _sb_client),
        ("BlobServiceClient", _blob_client),
    ):
        if client is not None:
            try:
                await client.close()
            except Exception:
                logger.exception("Error closing %s", name)
    if _credential is not None:
        try:
            await _credential.close()
        except Exception:
            logger.exception("Error closing DefaultAzureCredential")
    _cosmos_client = None
    _sb_client = None
    _blob_client = None
    _credential = None


# ── Cosmos helpers ───────────────────────────────────────────────


def _container(client: CosmosClient, container_env_var: str):
    """Resolve a container client by reading database + container names from env."""
    db_name = os.getenv("INTERPRETER_COSMOS_DATABASE", "")
    container_name = os.getenv(container_env_var, "")
    return client.get_database_client(db_name).get_container_client(container_name)


async def fetch_outcomes(xcvs: list[str]) -> list[OutcomeDocument]:
    """Fetch outcome documents by XCV from the outcomes container.

    The outcomes container is partitioned on /xcv, with id = xcv.
    """
    results: list[OutcomeDocument] = []
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_OUTCOMES_CONTAINER")
    for xcv in xcvs:
        try:
            item = await container.read_item(item=xcv, partition_key=xcv)
            results.append(OutcomeDocument(**item))
        except Exception:
            logger.warning("Could not fetch outcome for xcv=%s", xcv)
    return results


async def upsert_action_plan(plan: ActionPlan) -> None:
    """Upsert an action plan to the action_ledger container."""
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_ACTIONS_CONTAINER")
    doc = plan.model_dump()
    doc["id"] = plan.correlation_id
    await container.upsert_item(doc)
    _tracker().log_cosmos_write("actions", doc["id"], plan.customer_name)


async def query_ledger_entry(
    fingerprint: str, customer_name: str
) -> ActionLedgerEntry | None:
    """Look up a single action ledger entry by fingerprint (id) and customer_name (partition key)."""
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_ACTIONS_CONTAINER")
    try:
        item = await container.read_item(item=fingerprint, partition_key=customer_name)
        return ActionLedgerEntry(**item)
    except Exception:
        return None


async def upsert_ledger_entry(entry: ActionLedgerEntry) -> None:
    """Upsert an action ledger entry (partition key: customer_name)."""
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_ACTIONS_CONTAINER")
    # NOTE: Cosmos derives the partition key from the document body's
    # /customer_name path. Passing partition_key= kwarg would leak through
    # to aiohttp and raise TypeError (azure-cosmos 4.15 bug).
    await container.upsert_item(entry.model_dump())
    _tracker().log_cosmos_write("actions", entry.id, entry.customer_name)


async def upsert_interpreter_run(run: InterpreterRun) -> None:
    """Upsert an interpreter run audit record (partition key: customer_name)."""
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_RUNS_CONTAINER")
    doc = run.model_dump()
    doc["id"] = run.correlation_id
    await container.upsert_item(doc)
    _tracker().log_cosmos_write("runs", doc["id"], run.customer_name)


async def upsert_correlation_index(entry: CorrelationIndexEntry) -> None:
    """Upsert a correlation index entry (partition key: customer_name)."""
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_CORRELATION_INDEX_CONTAINER")
    await container.upsert_item(entry.model_dump())
    _tracker().log_cosmos_write("correlation_index", entry.id, entry.customer_name)


# ── Correlation buffer (Cosmos-backed; survives restarts / multi-replica) ───────────────────────────────────────────────────────────────────────────────────────────────
# Buffered outcome messages live in INTERPRETER_COSMOS_BUFFER_CONTAINER
# (partition key /customer_name, id = xcv). They are written on ingest and
# deleted on flush so the working set survives a restart and is visible
# across replicas.


async def upsert_buffered_outcome(msg: OutcomeMessage) -> None:
    """Persist a single buffered outcome message (idempotent on xcv)."""
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_BUFFER_CONTAINER")
    doc = msg.model_dump()
    doc["id"] = msg.xcv
    await container.upsert_item(doc)
    _tracker().log_cosmos_write("buffer", doc["id"], msg.customer_name)


async def list_buffered_outcomes(customer_name: str) -> list[OutcomeMessage]:
    """Return all buffered outcomes for a customer (single partition query)."""
    results: list[OutcomeMessage] = []
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_BUFFER_CONTAINER")
    query = "SELECT * FROM c WHERE c.customer_name = @c"
    params = [{"name": "@c", "value": customer_name}]
    async for item in container.query_items(
        query=query,
        parameters=params,
        partition_key=customer_name,
    ):
        try:
            results.append(OutcomeMessage(**{k: v for k, v in item.items() if not k.startswith("_")}))
        except Exception:
            logger.warning("Skipping malformed buffered outcome id=%s", item.get("id"))
    return results


async def list_buffered_customers() -> list[str]:
    """Return distinct customer names that currently have buffered outcomes."""
    customers: set[str] = set()
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_BUFFER_CONTAINER")
    async for item in container.query_items(
        query="SELECT DISTINCT VALUE c.customer_name FROM c",
    ):
        if isinstance(item, str):
            customers.add(item)
    return sorted(customers)


async def delete_buffered_outcomes(customer_name: str, xcvs: list[str]) -> None:
    """Delete buffered outcomes for a customer by xcv (best-effort)."""
    if not xcvs:
        return
    client = await get_cosmos_client()
    container = _container(client, "INTERPRETER_COSMOS_BUFFER_CONTAINER")
    for xcv in xcvs:
        try:
            await container.delete_item(item=xcv, partition_key=customer_name)
        except Exception:
            logger.debug("Buffered outcome already gone: customer=%s xcv=%s", customer_name, xcv)


# ── Startup: verify partition-key contract ──────────────────────────────────────────────────────────────────────────────────────────
COSMOS_PARTITION_KEY_CONTRACT: dict[str, str] = {
    "INTERPRETER_COSMOS_OUTCOMES_CONTAINER": "/xcv",
    "INTERPRETER_COSMOS_ACTIONS_CONTAINER": "/customer_name",
    "INTERPRETER_COSMOS_RUNS_CONTAINER": "/customer_name",
    "INTERPRETER_COSMOS_CORRELATION_INDEX_CONTAINER": "/customer_name",
    "INTERPRETER_COSMOS_BUFFER_CONTAINER": "/customer_name",
}


async def assert_cosmos_partition_keys() -> None:
    """Read each container's properties and verify its partition path matches the contract.

    Raises ``RuntimeError`` if a container has the wrong partition path —
    upserts would otherwise fail with HTTP 400 at runtime.
    Logs a warning (non-fatal) if a container is missing or its env var is unset.
    """
    logger.info("assert_cosmos_partition_keys: acquiring Cosmos client...")
    client = await get_cosmos_client()
    logger.info("assert_cosmos_partition_keys: Cosmos client ready, checking %d containers", len(COSMOS_PARTITION_KEY_CONTRACT))
    for env_var, expected in COSMOS_PARTITION_KEY_CONTRACT.items():
        name = os.getenv(env_var, "")
        if not name:
            logger.warning("Cosmos partition-key check skipped: %s not set", env_var)
            continue
        logger.info("Reading container properties: %s = %s", env_var, name)
        try:
            container = _container(client, env_var)
            props = await container.read()
        except Exception as exc:
            logger.warning(
                "Cosmos partition-key check skipped for %s (%s): %s",
                env_var, name, exc,
            )
            continue
        paths = (props.get("partitionKey") or {}).get("paths") or []
        if expected not in paths:
            raise RuntimeError(
                f"Cosmos container {name!r} has partition key paths {paths!r}; "
                f"expected {expected!r}. Recreate the container with the correct partition key "
                f"or update COSMOS_PARTITION_KEY_CONTRACT."
            )
        logger.info("Cosmos partition-key OK: %s (%s) → %s", env_var, name, expected)

        # Per-item TTL on the action_ledger only takes effect if the container
        # has defaultTtl enabled (any non-null value, e.g. -1). Warn loudly so
        # operators know the ledger will grow unbounded if they forgot to set it.
        if env_var == "INTERPRETER_COSMOS_ACTIONS_CONTAINER":
            default_ttl = props.get("defaultTtl")
            if default_ttl is None:
                logger.warning(
                    "Cosmos container %r has defaultTtl disabled; per-item TTL "
                    "on action_ledger entries will be ignored and the ledger will "
                    "grow forever. Enable container TTL (set defaultTtl=-1) so "
                    "per-item ttl seconds (INTERPRETER_LEDGER_TTL_DAYS) take effect.",
                    name,
                )
            else:
                logger.info(
                    "Cosmos container %r defaultTtl=%s (per-item ttl active)",
                    name, default_ttl,
                )


# ── Sync helpers for the sandbox layer (ADLS Gen2 + Dynamic Sessions) ───────
# These intentionally use sync credentials because the sandbox HTTP wrapper
# (sandbox/client.py) is sync (urllib + run_in_executor) and the ADLS
# data-plane SDK is sync.

# Token scopes
SANDBOX_DYNAMIC_SESSIONS_SCOPE = "https://dynamicsessions.io/.default"
SANDBOX_STORAGE_SCOPE = "https://storage.azure.com/.default"

_sync_sandbox_credential = None
_datalake_service_clients: dict[str, Any] = {}


def get_sandbox_credential():
    """Sync credential chain for the sandbox layer.

    Tries `AzureCliCredential` first (works for local dev without IMDS), falls
    back to `DefaultAzureCredential`. Cached at module scope.
    """
    global _sync_sandbox_credential
    if _sync_sandbox_credential is not None:
        return _sync_sandbox_credential

    from azure.identity import (
        AzureCliCredential,
        DefaultAzureCredential as SyncDefaultAzureCredential,
    )

    try:
        cred = AzureCliCredential()
        cred.get_token(SANDBOX_DYNAMIC_SESSIONS_SCOPE)
        _sync_sandbox_credential = cred
        logger.info("Sandbox auth: using AzureCliCredential")
    except Exception:
        _sync_sandbox_credential = SyncDefaultAzureCredential()
        logger.info("Sandbox auth: using DefaultAzureCredential")
    return _sync_sandbox_credential


def get_sandbox_token(scope: str = SANDBOX_DYNAMIC_SESSIONS_SCOPE):
    """Mint an `AccessToken` for ``scope`` using the sandbox credential chain."""
    return get_sandbox_credential().get_token(scope)


def get_datalake_service_client(account: str | None = None):
    """Return a sync ``DataLakeServiceClient`` for the given ADLS account.

    Cached per-account. ``account`` defaults to the ``ADLS_ACCOUNT`` env var.
    Requires ``azure-storage-file-datalake``.
    """
    account = account or os.getenv("ADLS_ACCOUNT", "")
    if not account:
        raise RuntimeError("ADLS_ACCOUNT env var is not set")

    cached = _datalake_service_clients.get(account)
    if cached is not None:
        return cached

    try:
        from azure.storage.filedatalake import DataLakeServiceClient
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "azure-storage-file-datalake is required for ADLS I/O"
        ) from exc

    client = DataLakeServiceClient(
        account_url=f"https://{account}.dfs.core.windows.net",
        credential=get_sandbox_credential(),
    )
    _datalake_service_clients[account] = client
    return client


def get_filesystem_client(account: str | None = None, filesystem: str | None = None):
    """Return a sync ``FileSystemClient`` for the given ADLS filesystem.

    Defaults pulled from ``ADLS_ACCOUNT`` and ``ADLS_FILESYSTEM`` env vars.
    """
    filesystem = filesystem or os.getenv("ADLS_FILESYSTEM", "")
    if not filesystem:
        raise RuntimeError("ADLS_FILESYSTEM env var is not set")
    return get_datalake_service_client(account).get_file_system_client(filesystem)
