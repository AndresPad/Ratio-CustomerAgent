"""
Azure Container Apps Dynamic Sessions (PythonCustomPool) client.

Provides execute/download/list operations against an isolated Python
runtime container. Auth + ADLS plumbing live in `helper.azure_clients`.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helper.azure_clients import (
    SANDBOX_DYNAMIC_SESSIONS_SCOPE,
    SANDBOX_STORAGE_SCOPE,
    get_filesystem_client,
    get_sandbox_token,
)

logger = logging.getLogger(__name__)

# ── Settings (from environment) ──────────────────────────────────
SANDBOX_POOL_ENDPOINT = os.getenv("PYTHON_CUSTOM_POOL_ENDPOINT", "").rstrip("/")
SANDBOX_SESSION_ID = os.getenv("PYTHON_CUSTOM_POOL_SESSION_ID", "customeragent-default")
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("PYTHON_CUSTOM_POOL_TIMEOUT_SECONDS", "180"))
SANDBOX_DOWNLOAD_DIR = os.getenv("PYTHON_CUSTOM_POOL_DOWNLOAD_DIR", "downloads")
# Back-compat re-export (was previously defined here).
SANDBOX_TOKEN_SCOPE = SANDBOX_DYNAMIC_SESSIONS_SCOPE

# ── ADLS Gen2 (sole storage backend; /mnt/data is no longer used) ───────────
ADLS_ACCOUNT = os.getenv("ADLS_ACCOUNT", "")
ADLS_FILESYSTEM = os.getenv("ADLS_FILESYSTEM", "")
ADLS_BASE_PATH = os.getenv("ADLS_BASE_PATH", "customeragent").strip("/")


@dataclass
class SandboxResult:
    script_path: str
    returncode: int
    stdout: str
    stderr: str
    files: list[str]
    duration_seconds: float
    raw: dict[str, Any]

    @property
    def success(self) -> bool:
        return self.returncode == 0


class SandboxClient:
    """HTTP wrapper around the PythonCustomPool /execute endpoint."""

    def __init__(
        self,
        endpoint: str | None = None,
        session_id: str | None = None,
        timeout_seconds: int | None = None,
    ):
        self.endpoint = endpoint or SANDBOX_POOL_ENDPOINT
        self.session_id = session_id or SANDBOX_SESSION_ID
        self.timeout_seconds = timeout_seconds or SANDBOX_TIMEOUT_SECONDS

        if not self.endpoint:
            raise ValueError(
                "PYTHON_CUSTOM_POOL_ENDPOINT must be set in environment"
            )

        # Constants injected as module-level globals into every executed
        # script. ADLS_TOKEN is refreshed per-call (see execute()).
        self._constants: dict[str, Any] = {}
        self._storage_token: str | None = None
        self._storage_token_exp: float = 0.0

        # Auto-seed ADLS coordinates from env so user code can use them as
        # `ADLS_ACCOUNT`, `ADLS_FILESYSTEM`, `ADLS_BASE_PATH` constants.
        if ADLS_ACCOUNT and ADLS_FILESYSTEM:
            self._constants.update({
                "ADLS_ACCOUNT": ADLS_ACCOUNT,
                "ADLS_FILESYSTEM": ADLS_FILESYSTEM,
                "ADLS_BASE_PATH": ADLS_BASE_PATH,
            })

    # ── Constants injection ──────────────────────────────────────────
    def set_constants(self, constants: dict[str, Any]) -> None:
        """Replace the dict of constants injected into every executed script."""
        self._constants = dict(constants)

    def update_constants(self, **constants: Any) -> None:
        """Merge new constants into the existing set."""
        self._constants.update(constants)

    @staticmethod
    def _format_constants(d: dict[str, Any]) -> str:
        if not d:
            return ""
        lines = ["# \u2500\u2500 Injected constants (ADLS, XCV, \u2026) \u2500\u2500"]
        for k, v in d.items():
            if not k.isidentifier() or k.startswith("_"):
                raise ValueError(f"bad constant name: {k!r}")
            lines.append(f"{k} = {v!r}")
        lines.append("")
        return "\n".join(lines)

    def _get_token(self) -> str:
        return get_sandbox_token(SANDBOX_DYNAMIC_SESSIONS_SCOPE).token

    def _get_storage_token(self) -> str:
        """Bearer for ADLS (storage.azure.com). Cached with 60 s buffer."""
        now = time.time()
        if self._storage_token and now < self._storage_token_exp - 60:
            return self._storage_token
        tok = get_sandbox_token(SANDBOX_STORAGE_SCOPE)
        self._storage_token = tok.token
        self._storage_token_exp = float(tok.expires_on)
        return self._storage_token

    def _filesystem_client(self):
        return get_filesystem_client(ADLS_ACCOUNT or None, ADLS_FILESYSTEM or None)

    def _post_json(self, path: str, payload: dict) -> dict:
        token = self._get_token()
        url = f"{self.endpoint}{path}"
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url=url,
            data=data,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(
            req, timeout=self.timeout_seconds + 60
        ) as resp:
            return json.loads(resp.read().decode("utf-8"))

    async def execute(
        self,
        code: str,
        filename: str = "agent_script.py",
        session_id: str | None = None,
        timeout_seconds: int | None = None,
        extra_constants: dict[str, Any] | None = None,
        inject_adls_token: bool = True,
    ) -> SandboxResult:
        sid = session_id or self.session_id
        timeout = timeout_seconds or self.timeout_seconds

        # Build per-call constants: base + extras + (optional) fresh ADLS token.
        consts: dict[str, Any] = dict(self._constants)
        if extra_constants:
            consts.update(extra_constants)
        if inject_adls_token and "ADLS_ACCOUNT" in consts:
            # Refresh storage token (DefaultAzureCredential / AzureCliCredential
            # chain on the host). Inject both the bearer + its expiry so the
            # generated script can wrap it in a TokenCredential shim.
            consts["ADLS_TOKEN"] = self._get_storage_token()
            consts["ADLS_TOKEN_EXPIRES_ON"] = int(self._storage_token_exp)

        full_code = self._format_constants(consts) + "\n" + code

        payload = {
            "code": full_code,
            "filename": filename,
            "timeout_seconds": timeout,
        }
        loop = asyncio.get_running_loop()
        t0 = time.monotonic()
        data = await loop.run_in_executor(
            None,
            lambda: self._post_json(f"/execute?identifier={sid}", payload),
        )
        duration = time.monotonic() - t0
        return SandboxResult(
            script_path=data.get("script_path", ""),
            returncode=int(data.get("returncode", 1)),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            files=list(data.get("files", [])),
            duration_seconds=round(duration, 2),
            raw=data,
        )

    # ── ADLS Gen2 file ops (host-side; uses same credential chain) ──────────
    # All paths are ADLS paths under ADLS_FILESYSTEM, e.g.
    #   "runs/<xcv>/evidence/foo.json"
    # /mnt/data is intentionally not used.
    # ──────────────────────────────────────────────────────────────────────────────────────
    @staticmethod
    def _normalize_adls_path(path: str) -> str:
        return path.lstrip("/")

    async def download_file(
        self,
        adls_path: str,
        local_path: str | None = None,
        session_id: str | None = None,  # accepted for backward-compat; unused
    ) -> str:
        """Download an ADLS file to local disk and return the local path."""
        adls_path = self._normalize_adls_path(adls_path)
        loop = asyncio.get_running_loop()

        def _do() -> bytes:
            fc = self._filesystem_client().get_file_client(adls_path)
            return fc.download_file().readall()

        blob = await loop.run_in_executor(None, _do)

        if local_path is None:
            dl_dir = Path(SANDBOX_DOWNLOAD_DIR)
            dl_dir.mkdir(parents=True, exist_ok=True)
            local_path = str(dl_dir / Path(adls_path).name)

        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        return str(target.resolve())

    async def read_file(
        self,
        adls_path: str,
        session_id: str | None = None,  # backward-compat; unused
        encoding: str = "utf-8",
    ) -> str:
        """Read a text file from ADLS and return its decoded content."""
        adls_path = self._normalize_adls_path(adls_path)
        loop = asyncio.get_running_loop()

        def _do() -> bytes:
            fc = self._filesystem_client().get_file_client(adls_path)
            return fc.download_file().readall()

        blob = await loop.run_in_executor(None, _do)
        return blob.decode(encoding)

    async def upload_file(
        self,
        adls_path: str,
        data: str | bytes,
        session_id: str | None = None,  # backward-compat; unused
        overwrite: bool = True,
    ) -> str:
        """Upload bytes/text to ADLS at ``adls_path``. Returns the path written."""
        adls_path = self._normalize_adls_path(adls_path)
        payload = data.encode("utf-8") if isinstance(data, str) else data
        loop = asyncio.get_running_loop()

        def _do() -> None:
            fc = self._filesystem_client().get_file_client(adls_path)
            fc.upload_data(payload, overwrite=overwrite)

        await loop.run_in_executor(None, _do)
        return adls_path

    async def list_files(
        self,
        adls_path: str = "",
        recursive: bool = True,
        session_id: str | None = None,  # backward-compat; unused
    ) -> list[str]:
        """List files at ``adls_path`` (prefix) under the configured filesystem."""
        adls_path = self._normalize_adls_path(adls_path)
        loop = asyncio.get_running_loop()

        def _do() -> list[str]:
            fs = self._filesystem_client()
            paths = fs.get_paths(path=adls_path or None, recursive=recursive)
            return sorted(p.name for p in paths if not p.is_directory)

        return await loop.run_in_executor(None, _do)
