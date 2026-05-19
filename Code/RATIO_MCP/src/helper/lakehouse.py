# Moved from src/lakehouse.py
from __future__ import annotations
from dotenv import load_dotenv
import os, struct, logging
from itertools import chain, repeat
import pyodbc
from helper.auth import get_token

load_dotenv()
logger = logging.getLogger("ratio_mcp")

FABRIC_SQL_ENDPOINT = os.getenv("FABRIC_SQL_ENDPOINT")
FABRIC_SQL_DATABASE = os.getenv("FABRIC_SQL_DATABASE")
FABRIC_APP_ID = os.getenv("FABRIC_APP_ID")

# SQL Server scope for token-based auth
_SQL_SCOPE = "https://database.windows.net/.default"


def _build_token_struct(access_token: str) -> bytes:
    """Convert an access token string into the ODBC driver token struct."""
    token_bytes = bytes(access_token, "UTF-8")
    encoded_bytes = bytes(chain.from_iterable(zip(token_bytes, repeat(0))))
    return struct.pack("<i", len(encoded_bytes)) + encoded_bytes


def _connect_with_token(
    access_token: str,
    endpoint: str | None = None,
    database: str | None = None,
) -> pyodbc.Connection:
    """Connect to a SQL endpoint using a pre-obtained access token.

    Args:
        access_token: Bearer token for SQL auth.
        endpoint: SQL server hostname. Falls back to FABRIC_SQL_ENDPOINT env var.
        database: Database name. Falls back to FABRIC_SQL_DATABASE env var.
    """
    endpoint = endpoint or FABRIC_SQL_ENDPOINT
    database = database or FABRIC_SQL_DATABASE
    if not endpoint or not database:
        raise ValueError(
            "SQL endpoint and database must be provided or set via "
            "FABRIC_SQL_ENDPOINT / FABRIC_SQL_DATABASE env vars."
        )
    connection_string = (
        f"Driver={{ODBC Driver 18 for SQL Server}};"
        f"Server={endpoint};"
        f"Database={database};"
        f"Encrypt=Yes;TrustServerCertificate=No;"
    )
    attrs_before = {1256: _build_token_struct(access_token)}
    return pyodbc.connect(connection_string, attrs_before=attrs_before)


def connect(
    user_token: str | None = None,
    endpoint: str | None = None,
    database: str | None = None,
    app_id: str | None = None,
) -> pyodbc.Connection:
    """Connect to a SQL endpoint with flexible auth.

    Args:
        user_token: Optional bearer token from the API caller.
        endpoint: SQL server hostname override (defaults to FABRIC_SQL_ENDPOINT).
        database: Database name override (defaults to FABRIC_SQL_DATABASE).
        app_id: Optional service-principal client ID used for the
            CertificateCredential fallback. Defaults to FABRIC_APP_ID.
            Pass a Synapse-specific SP here so Synapse endpoints don't
            piggy-back on the Fabric identity.

    Resolution order:
      1. user_token — if a bearer token is passed from the API caller, use it directly.
      2. ManagedIdentity → DefaultAzureCredential → CertificateCredential (via auth.get_token).

    Raises on failure instead of returning None.
    """
    # 1. User-provided token (passthrough from API request)
    if user_token:
        logger.info("SQL auth: using user-provided token (len=%d) to %s/%s",
                     len(user_token), endpoint or FABRIC_SQL_ENDPOINT, database or FABRIC_SQL_DATABASE)
        try:
            return _connect_with_token(user_token, endpoint=endpoint, database=database)
        except Exception as e:
            logger.warning("SQL auth user token failed: %s; falling through", e)

    # 2. MI → Default → Cert (via centralized auth).
    # The cert fallback uses *app_id* if supplied (e.g. a Synapse-specific SP),
    # otherwise the Fabric SP. Scope https://database.windows.net/.default is
    # correct for Azure SQL DB, Fabric SQL, and Synapse SQL (serverless + dedicated).
    cert_app_id = app_id or FABRIC_APP_ID
    try:
        token = get_token(_SQL_SCOPE, cert_client_id=cert_app_id)
        return _connect_with_token(token, endpoint=endpoint, database=database)
    except ConnectionError:
        raise ConnectionError(
            "All SQL auth methods failed. "
            "Provide a user token, configure ManagedIdentity/DefaultAzureCredential, "
            "or set CERT_NAME + KEY_VAULT_NAME for certificate auth."
        )


def run_tsql_query(
    query: str,
    user_token: str | None = None,
    endpoint: str | None = None,
    database: str | None = None,
    app_id: str | None = None,
) -> list:
    """Execute a T-SQL query with flexible auth.

    Args:
        query: The SQL query to execute.
        user_token: Optional bearer token from the API caller for passthrough auth.
        endpoint: SQL server hostname override (defaults to FABRIC_SQL_ENDPOINT).
        database: Database name override (defaults to FABRIC_SQL_DATABASE).
        app_id: Optional SP client ID for the cert fallback (per-endpoint identity).
    """
    conn = connect(user_token=user_token, endpoint=endpoint, database=database, app_id=app_id)
    cursor = conn.cursor()
    try:
        logger.debug("Executing query: %s", query[:200])
        cursor.execute(query)
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as e:
        logger.error("Error running query: %s", e)
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


__all__ = ["run_tsql_query", "connect"]
