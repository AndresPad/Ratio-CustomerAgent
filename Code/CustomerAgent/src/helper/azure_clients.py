"""
Async Azure credential and client helpers for publisher services.

Provides context-managed async clients for Blob Storage, Cosmos DB,
and Service Bus — centralising credential lifecycle management.

Also provides sync helpers for the sandbox layer (ADLS Gen2 + Dynamic
Sessions). Those use sync credentials because the sandbox HTTP wrapper
is sync (urllib) and the ADLS data-plane SDK is sync.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from azure.identity.aio import DefaultAzureCredential
from azure.storage.blob.aio import BlobServiceClient
from azure.storage.filedatalake.aio import DataLakeServiceClient as AsyncDataLakeServiceClient
from azure.cosmos.aio import CosmosClient
from azure.servicebus.aio import ServiceBusClient

logger = logging.getLogger(__name__)

# ── Configuration ────────────────────────────────────────────────────────────

_BLOB_ACCOUNT_URL = os.getenv(
    "PUBLISHER_BLOB_ACCOUNT_URL",
    "https://ratioaidev.blob.core.windows.net",
)

_COSMOS_ENDPOINT = os.getenv(
    "PUBLISHER_COSMOS_ENDPOINT",
    "https://cosmos-ratio-ai-dev.documents.azure.com:443/",
)

_SERVICEBUS_FQNS = os.getenv(
    "PUBLISHER_SERVICEBUS_FQNS",
    "sbn-ratio-ai-dev.servicebus.windows.net",
)

# ADLS Gen2 (shared with sandbox)
_ADLS_ACCOUNT = os.getenv("ADLS_ACCOUNT", "")
_ADLS_FILESYSTEM = os.getenv("ADLS_FILESYSTEM", "")


# ── Async client context managers ────────────────────────────────────────────


@asynccontextmanager
async def get_blob_client() -> AsyncGenerator[BlobServiceClient, None]:
    """Yield an authenticated async BlobServiceClient."""
    credential = DefaultAzureCredential()
    try:
        async with BlobServiceClient(
            account_url=_BLOB_ACCOUNT_URL, credential=credential
        ) as client:
            yield client
    finally:
        await credential.close()


@asynccontextmanager
async def get_datalake_filesystem(
    account: str | None = None,
    filesystem: str | None = None,
) -> AsyncGenerator[Any, None]:
    """Yield an authenticated async ADLS Gen2 ``FileSystemClient``.

    Defaults pulled from ``ADLS_ACCOUNT`` and ``ADLS_FILESYSTEM`` env vars.
    """
    account = account or _ADLS_ACCOUNT
    filesystem = filesystem or _ADLS_FILESYSTEM
    if not account:
        raise RuntimeError("ADLS_ACCOUNT env var is not set")
    if not filesystem:
        raise RuntimeError("ADLS_FILESYSTEM env var is not set")

    credential = DefaultAzureCredential()
    try:
        async with AsyncDataLakeServiceClient(
            account_url=f"https://{account}.dfs.core.windows.net",
            credential=credential,
        ) as service:
            yield service.get_file_system_client(filesystem)
    finally:
        await credential.close()


@asynccontextmanager
async def get_cosmos_client() -> AsyncGenerator[CosmosClient, None]:
    """Yield an authenticated async CosmosClient."""
    credential = DefaultAzureCredential()
    try:
        async with CosmosClient(
            url=_COSMOS_ENDPOINT, credential=credential
        ) as client:
            yield client
    finally:
        await credential.close()


@asynccontextmanager
async def get_servicebus_client() -> AsyncGenerator[ServiceBusClient, None]:
    """Yield an authenticated async ServiceBusClient."""
    credential = DefaultAzureCredential()
    try:
        async with ServiceBusClient(
            fully_qualified_namespace=_SERVICEBUS_FQNS, credential=credential
        ) as client:
            yield client
    finally:
        await credential.close()


# ── Sync helpers for the sandbox layer (ADLS Gen2 + Dynamic Sessions) ───────
# These intentionally use sync credentials because the sandbox HTTP wrapper
# (core/sandbox/client.py) is sync (urllib + run_in_executor) and the ADLS
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

    from azure.identity import AzureCliCredential, DefaultAzureCredential as SyncDefaultAzureCredential

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

    Cached per-account. ``account`` defaults to ``ADLS_ACCOUNT`` env var.
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

