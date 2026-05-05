"""
Azure Container Apps Dynamic Sessions (PythonCustomPool) client.

Provides execute/download/list operations against an isolated Python
runtime container. Auth via helper/auth.py DefaultAzureCredential chain.
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

from azure.identity import AzureCliCredential, DefaultAzureCredential

logger = logging.getLogger(__name__)

# ── Sandbox auth (separate from middleware auth) ─────────────────
# Uses AzureCliCredential locally, falls back to DefaultAzureCredential.
# This avoids the middleware's ManagedIdentityCredential which requires IMDS.
_sandbox_credential = None

def _get_sandbox_credential():
    global _sandbox_credential
    if _sandbox_credential is not None:
        return _sandbox_credential
    try:
        cred = AzureCliCredential()
        cred.get_token("https://dynamicsessions.io/.default")
        _sandbox_credential = cred
        logger.info("Sandbox auth: using AzureCliCredential")
    except Exception:
        _sandbox_credential = DefaultAzureCredential()
        logger.info("Sandbox auth: using DefaultAzureCredential")
    return _sandbox_credential

# ── Settings (from environment) ──────────────────────────────────
SANDBOX_POOL_ENDPOINT = os.getenv("PYTHON_CUSTOM_POOL_ENDPOINT", "").rstrip("/")
SANDBOX_SESSION_ID = os.getenv("PYTHON_CUSTOM_POOL_SESSION_ID", "customeragent-default")
SANDBOX_TIMEOUT_SECONDS = int(os.getenv("PYTHON_CUSTOM_POOL_TIMEOUT_SECONDS", "180"))
SANDBOX_DOWNLOAD_DIR = os.getenv("PYTHON_CUSTOM_POOL_DOWNLOAD_DIR", "downloads")
SANDBOX_TOKEN_SCOPE = "https://dynamicsessions.io/.default"


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

    def _get_token(self) -> str:
        cred = _get_sandbox_credential()
        token = cred.get_token(SANDBOX_TOKEN_SCOPE)
        return token.token

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
    ) -> SandboxResult:
        sid = session_id or self.session_id
        timeout = timeout_seconds or self.timeout_seconds
        payload = {
            "code": code,
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

    async def download_file(
        self,
        remote_path: str,
        local_path: str | None = None,
        session_id: str | None = None,
    ) -> str:
        script = (
            "import base64\n"
            "from pathlib import Path\n"
            f"p = Path({repr(remote_path)})\n"
            "if not p.exists():\n"
            "    raise FileNotFoundError(f'Sandbox file not found: {p}')\n"
            "data = p.read_bytes()\n"
            "print('__DOWNLOAD_START__')\n"
            "print(base64.b64encode(data).decode('ascii'))\n"
            "print('__DOWNLOAD_END__')\n"
        )
        result = await self.execute(
            code=script,
            filename="download_artifact.py",
            session_id=session_id,
        )
        if not result.success:
            raise RuntimeError(f"Sandbox download failed:\n{result.stderr}")

        raw_b64 = self._extract_b64(result.stdout)
        blob = base64.b64decode(raw_b64.encode("ascii"))

        if local_path is None:
            dl_dir = Path(SANDBOX_DOWNLOAD_DIR)
            dl_dir.mkdir(parents=True, exist_ok=True)
            local_path = str(dl_dir / Path(remote_path).name)

        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob)
        return str(target.resolve())

    async def read_file(
        self,
        remote_path: str,
        session_id: str | None = None,
    ) -> str:
        """Read a text file from the sandbox filesystem and return its content."""
        script = (
            "from pathlib import Path\n"
            f"p = Path({repr(remote_path)})\n"
            "if not p.exists():\n"
            "    raise FileNotFoundError(f'Sandbox file not found: {p}')\n"
            "print(p.read_text(encoding='utf-8'))\n"
        )
        result = await self.execute(
            code=script,
            filename="read_file.py",
            session_id=session_id,
        )
        if not result.success:
            raise RuntimeError(f"Sandbox read_file failed for {remote_path}:\n{result.stderr}")
        return result.stdout

    async def list_files(self, session_id: str | None = None) -> list[str]:
        result = await self.execute(
            code=(
                "import os\n"
                "for name in sorted(os.listdir('/mnt/data')):\n"
                "    print(name)"
            ),
            filename="list_files.py",
            session_id=session_id,
        )
        if not result.success:
            raise RuntimeError(f"List files failed:\n{result.stderr}")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    @staticmethod
    def _extract_b64(stdout: str) -> str:
        match = re.search(
            r"__DOWNLOAD_START__\s*(.*?)\s*__DOWNLOAD_END__",
            stdout,
            flags=re.DOTALL,
        )
        if not match:
            raise ValueError("Download markers not found in stdout")
        return match.group(1).strip()
