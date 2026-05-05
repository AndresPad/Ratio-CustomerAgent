# Sandbox Integration Plan — CustomerAgent

## 1. Executive Summary

This plan describes how to integrate Azure Container Apps Dynamic Sessions (PythonCustomPool) into the CustomerAgent system. The integration adds a **sandbox_coder** agent that can write, execute, and retrieve results from Python code running in an isolated container — enabling live data analysis, visualization generation, and ad-hoc computation within the existing GroupChat workflow.

The design follows the **config-driven agent factory** pattern already established in CustomerAgent: one new tool-mode handler, one new agent config entry, and a small sandbox module. The frontend gets a new "Sandbox" view tab for live code execution visualization. Total new files: ~8. Modified files: ~5.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    CustomerAgentUI (port 5020)               │
│  ┌──────┐ ┌──────┐ ┌──────────┐ ┌────────┐ ┌───────────┐   │
│  │Stream│ │Graph │ │AgentFlow │ │Timeline│ │  Sandbox  │   │
│  └──────┘ └──────┘ └──────────┘ └────────┘ └─────┬─────┘   │
│                                                   │ SSE      │
└───────────────────────────────────────────────────┼─────────┘
                                                    │
┌───────────────────────────────────────────────────┼─────────┐
│              CustomerAgent Backend (port 8020)    │         │
│                                                   │         │
│  agents_config.json                               ▼         │
│  ┌─────────────┐    ┌──────────────────────────────────┐    │
│  │ orchestrator │───►│ sandbox_coder                    │    │
│  │ (GroupChat)  │    │  tool_mode: "sandbox"            │    │
│  └──────┬──────┘    │  tools: execute_python_in_sandbox│    │
│         │           │         download_sandbox_file    │    │
│         │           │         list_sandbox_files       │    │
│         ▼           └──────────────┬───────────────────┘    │
│  [other agents]                    │                        │
│                                    ▼                        │
│                    ┌───────────────────────────┐            │
│                    │  core/sandbox/client.py   │            │
│                    │  SandboxClient            │            │
│                    │  - execute()              │            │
│                    │  - download_file()        │            │
│                    │  - list_files()           │            │
│                    └───────────┬───────────────┘            │
│                                │ HTTPS + Bearer token       │
└────────────────────────────────┼────────────────────────────┘
                                 │
                                 ▼
                  ┌──────────────────────────────┐
                  │  Azure Container Apps         │
                  │  PythonCustomPool              │
                  │  /execute  /files              │
                  │  (isolated Python runtime)     │
                  └──────────────────────────────┘
```

The sandbox_coder agent participates in the existing GroupChat workflow. The orchestrator can delegate code-writing/execution tasks to it just like it delegates SQL queries to outage_analyst. The agent writes Python code, executes it via the PythonCustomPool API, and returns results (stdout, stderr, generated files) to the conversation.

---

## 3. Backend Changes

### 3.1 New Files — Sandbox Module

Create `src/core/sandbox/` with three files:

#### 3.1.1 `src/core/sandbox/__init__.py`

Empty init file for the sandbox package.

```python
# Sandbox module — Azure Container Apps Dynamic Sessions integration.
```

#### 3.1.2 `src/core/sandbox/client.py`

HTTP client for PythonCustomPool. Adapted from the RATIO-OpenAgent `sandbox_tool.py`, but structured as a class using the CustomerAgent auth pattern from `helper/auth.py`.

**Key design decisions:**
- Uses `helper/auth.py` → `get_auth_token("https://dynamicsessions.io/.default")` for token acquisition (reuses the existing `DefaultAzureCredential` / `ManagedIdentityCredential` chain — no separate `auth.py` needed)
- Uses `urllib.request` with `asyncio.run_in_executor()` for async (same pattern as RATIO-OpenAgent — avoids adding httpx/aiohttp dependency for this single use case)
- Returns a `SandboxResult` dataclass

```python
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
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from helper.auth import get_auth_token

logger = logging.getLogger(__name__)

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
        token = get_auth_token(SANDBOX_TOKEN_SCOPE)
        if not token:
            raise RuntimeError("Failed to acquire token for sandbox scope")
        return token

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
        import time
        t0 = time.monotonic()
        data = await loop.run_in_executor(
            None,
            lambda: self._post_json(
                f"/execute?identifier={sid}", payload
            ),
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
        # Execute a script in the sandbox that reads + base64-encodes the file
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
            raise RuntimeError(
                f"Sandbox download failed:\n{result.stderr}"
            )
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

    async def list_files(
        self, session_id: str | None = None
    ) -> list[str]:
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
            raise RuntimeError(
                f"List files failed:\n{result.stderr}"
            )
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

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
```

#### 3.1.3 `src/core/sandbox/tools.py`

MAF `@tool` functions that wrap the `SandboxClient`. These are the tools the `sandbox_coder` agent invokes.

```python
"""
MAF @tool functions for sandbox code execution.

Each function wraps SandboxClient methods and emits AgentLogger events
for UI visualization (sandbox_* SSE events).
"""
from __future__ import annotations

import logging

from agent_framework import tool

from helper.agent_logger import AgentLogger, get_current_xcv
from .client import SandboxClient

logger = logging.getLogger(__name__)

# Module-level client instance (lazy-init)
_client: SandboxClient | None = None


def _get_client() -> SandboxClient:
    global _client
    if _client is None:
        _client = SandboxClient()
    return _client


@tool(name="execute_python_in_sandbox")
async def execute_python_in_sandbox(
    code: str,
    filename: str = "agent_script.py",
) -> str:
    """Execute Python code in a secure sandbox container and return the result.

    Args:
        code: Python source code to execute.
        filename: Name for the script file (default: agent_script.py).

    Returns:
        JSON string with returncode, stdout, stderr, files, and duration.
    """
    import json

    tracker = AgentLogger.get_instance()
    xcv = get_current_xcv()

    # Emit code-generated event for UI
    tracker.emit(xcv, "sandbox_code_generated", {
        "code": code,
        "filename": filename,
    })
    tracker.emit(xcv, "sandbox_execution_started", {
        "filename": filename,
    })

    client = _get_client()
    try:
        result = await client.execute(code=code, filename=filename)
        tracker.emit(xcv, "sandbox_execution_complete", {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "files": result.files,
            "duration_seconds": result.duration_seconds,
            "success": result.success,
        })
        return json.dumps({
            "success": result.success,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "files": result.files,
            "duration_seconds": result.duration_seconds,
        })
    except Exception as exc:
        tracker.emit(xcv, "sandbox_error", {
            "error": str(exc),
            "filename": filename,
        })
        return json.dumps({
            "success": False,
            "error": str(exc),
        })


@tool(name="download_sandbox_file")
async def download_sandbox_file(remote_path: str) -> str:
    """Download a file from the sandbox container to the local filesystem.

    Args:
        remote_path: Path to the file inside the sandbox (e.g. /mnt/data/chart.png).

    Returns:
        Local file path where the file was saved.
    """
    tracker = AgentLogger.get_instance()
    xcv = get_current_xcv()

    client = _get_client()
    local_path = await client.download_file(remote_path)

    tracker.emit(xcv, "sandbox_file_downloaded", {
        "remote_path": remote_path,
        "local_path": local_path,
    })

    return f"File downloaded to: {local_path}"


@tool(name="list_sandbox_files")
async def list_sandbox_files() -> str:
    """List all files in the sandbox /mnt/data directory.

    Returns:
        Newline-separated list of filenames.
    """
    client = _get_client()
    files = await client.list_files()
    return "\n".join(files) if files else "(no files)"
```

### 3.2 Config Changes

#### 3.2.1 New Agent in `agents_config.json`

Add a `sandbox_coder` entry to the `agents` array in `src/config/agents/agents_config.json`:

```json
{
    "name": "sandbox_coder",
    "description": "Writes and executes Python code in a secure sandbox container. Can generate visualizations, run data analysis, and produce downloadable artifacts.",
    "prompt_file": "maf_sandbox_coder_prompt.txt",
    "model": "gpt-4o",
    "temperature": 0.3,
    "tool_mode": "sandbox",
    "mcp_tools": [],
    "evaluate": false,
    "prompt_injection": true,
    "log_input": true,
    "log_output": true
}
```

Add `"sandbox_coder"` to the `workflow.participants` array so the orchestrator can delegate to it.

#### 3.2.2 New Prompt File

Create `src/prompts/maf_sandbox_coder_prompt.txt`:

```text
You are a Python code execution specialist. When asked to analyze data,
create visualizations, or run computations, you write clean Python code
and execute it in a secure sandbox container.

## Tools Available

- **execute_python_in_sandbox**: Run Python code and get stdout/stderr/files back.
- **download_sandbox_file**: Download a generated file from the sandbox.
- **list_sandbox_files**: See what files exist in /mnt/data.

## Guidelines

1. Write complete, self-contained Python scripts (the sandbox has no prior state).
2. Available packages: pandas, numpy, matplotlib, plotly, scikit-learn, requests.
3. Save output files to /mnt/data/ (e.g. /mnt/data/chart.png).
4. Print results to stdout for the conversation — the orchestrator sees your output.
5. If execution fails, read the stderr, fix the code, and retry (max 2 retries).
6. For visualizations, prefer plotly for interactive charts or matplotlib for static.
7. Always print a summary of what was produced at the end of execution.
```

#### 3.2.3 New Tool-Mode Handler

Register a `"sandbox"` tool-mode handler in `src/core/agent_factory.py`. This keeps sandbox tool resolution consistent with the existing plugin registry pattern.

Add to the `TOOL_MODE_HANDLERS` dict and its corresponding handler function:

```python
def _tool_mode_sandbox(agent_cfg: dict[str, Any], ctx: dict[str, Any]) -> list:
    """Return sandbox @tool functions for agents with tool_mode='sandbox'."""
    from core.sandbox.tools import (
        execute_python_in_sandbox,
        download_sandbox_file,
        list_sandbox_files,
    )
    return [execute_python_in_sandbox, download_sandbox_file, list_sandbox_files]
```

And register it:

```python
TOOL_MODE_HANDLERS: dict[str, ToolModeHandler] = {
    "none": _tool_mode_none,
    "filtered": _tool_mode_filtered,
    "all": _tool_mode_all,
    "sandbox": _tool_mode_sandbox,   # ← NEW
}
```

### 3.3 Integration Points

#### 3.3.1 Agent Factory (`src/core/agent_factory.py`)

**One change:** Add the `_tool_mode_sandbox` handler function and its entry in `TOOL_MODE_HANDLERS`. No other changes — the existing `create_agents()` loop already handles arbitrary tool modes via the registry.

The topological sort, middleware stack, and LLM client resolution all work unchanged because `sandbox_coder` has no `sub_agents` and uses standard middleware flags.

#### 3.3.2 Orchestrator (`src/core/orchestrator.py`)

**No changes required.** The orchestrator is config-driven — adding `"sandbox_coder"` to `workflow.participants` in `agents_config.json` is sufficient. The orchestrator agent's prompt may need a line about when to delegate to the sandbox agent, but that's a prompt edit, not a code change.

#### 3.3.3 Server (`src/server/app.py`)

**No changes required.** The SSE streaming pipeline already emits all `AgentLogger` events. The new `sandbox_*` events from `tools.py` will flow through the existing event bus automatically.

#### 3.3.4 AgentLogger (`src/helper/agent_logger.py`)

**No changes required.** The `emit()` method already accepts arbitrary event types. The sandbox tools call `tracker.emit(xcv, "sandbox_*", {...})` which works out of the box.

#### 3.3.5 Environment Variables

Add to `.env`:

```env
# ── Sandbox (Azure Container Apps Dynamic Sessions) ──────────
PYTHON_CUSTOM_POOL_ENDPOINT=https://pythoncustompool.redwave-7f0e0561.westus3.azurecontainerapps.io
PYTHON_CUSTOM_POOL_SESSION_ID=customeragent-default
PYTHON_CUSTOM_POOL_TIMEOUT_SECONDS=180
PYTHON_CUSTOM_POOL_DOWNLOAD_DIR=downloads
```

---

## 4. Frontend Changes

### 4.1 New View — `views/sandbox.js`

Create `CustomerAgentUI/views/sandbox.js` — a new tab view that visualizes sandbox code execution.

**Layout:**

```
┌─────────────────────────────────────────────────────────────┐
│  Sandbox Executions                                         │
├─────────────────────────────────────────────────────────────┤
│  ┌─ Execution #1 ────────────────────────────────────────┐  │
│  │ Status: ✅ Success (2.3s)                             │  │
│  │ ┌─ Code ────────────────────────────────────────────┐ │  │
│  │ │ import pandas as pd                               │ │  │
│  │ │ df = pd.read_csv(...)                             │ │  │
│  │ │ print(df.describe())                              │ │  │
│  │ └──────────────────────────────────────────────────┘ │  │
│  │ ┌─ stdout ─────────────────────────────────────────┐ │  │
│  │ │ count    1000                                     │ │  │
│  │ │ mean     42.3                                     │ │  │
│  │ └──────────────────────────────────────────────────┘ │  │
│  │ ┌─ stderr ─────────────────────────────────────────┐ │  │
│  │ │ (empty)                                           │ │  │
│  │ └──────────────────────────────────────────────────┘ │  │
│  │ Files: chart.png, results.csv                        │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌─ Execution #2 ────────────────────────────────────────┐  │
│  │ Status: ⏳ Running...                                 │  │
│  │ ┌─ Code ────────────────────────────────────────────┐ │  │
│  │ │ ...                                               │ │  │
│  │ └──────────────────────────────────────────────────┘ │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

**Features:**
- Code block with monospace font and syntax-highlighted keywords (simple regex-based highlighting — no external lib needed)
- Collapsible stdout/stderr sections
- Status badge: ⏳ Running → ✅ Success / ❌ Failed
- Duration display
- File list with names of generated artifacts
- Auto-scroll to latest execution

**Event handlers:**
- `sandbox_code_generated` → create new execution card with code block
- `sandbox_execution_started` → set status to Running
- `sandbox_execution_complete` → populate stdout/stderr/files, set final status
- `sandbox_file_downloaded` → add download indicator to files list
- `sandbox_error` → show error state with message

**Exports:**
```javascript
export function initSandboxView() { ... }
export function addSandboxEvent(event) { ... }
export function clearSandbox() { ... }
```

### 4.2 Existing File Changes

#### 4.2.1 `CustomerAgentUI/app.js`

**Changes:**
1. Add import for the sandbox view module:
   ```javascript
   import { initSandboxView, addSandboxEvent, clearSandbox } from '/views/sandbox.js';
   ```

2. Add `initSandboxView()` call in the `DOMContentLoaded` handler (alongside existing `initStreamView()`, etc.)

3. Add `clearSandbox()` call in the `_resetAll()` function

4. Add sandbox event routing in the `_bindSSEEvents()` → `agent-event` handler:
   ```javascript
   // Sandbox events go to the sandbox view
   if (event.type?.startsWith('sandbox_')) {
       addSandboxEvent(event);
       // Also add to stream view for the unified event log
       addStreamEvent(event);
       return;
   }
   ```

#### 4.2.2 `CustomerAgentUI/index.html`

**Changes:**
1. Add sandbox tab button in the `.view-tabs` section:
   ```html
   <button class="view-tab" data-view="sandbox">🔬 Sandbox</button>
   ```

2. Add sandbox view container in the `<main>` section:
   ```html
   <div id="view-sandbox" class="view"></div>
   ```

#### 4.2.3 `CustomerAgentUI/styles.css`

**Add sandbox-specific styles:**

```css
/* ── Sandbox View ──────────────────────────────────────── */
.sandbox-execution {
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 12px;
    overflow: hidden;
}
.sandbox-execution .sandbox-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 8px 12px;
    background: var(--surface-raised);
    border-bottom: 1px solid var(--border);
    font-size: 0.85rem;
}
.sandbox-execution .sandbox-status {
    font-weight: 600;
}
.sandbox-execution .sandbox-status.running { color: var(--warning); }
.sandbox-execution .sandbox-status.success { color: var(--success); }
.sandbox-execution .sandbox-status.error   { color: var(--danger); }
.sandbox-code-block {
    background: #1e1e1e;
    color: #d4d4d4;
    padding: 12px;
    font-family: 'Cascadia Code', 'Fira Code', 'Consolas', monospace;
    font-size: 0.8rem;
    line-height: 1.5;
    overflow-x: auto;
    white-space: pre;
    max-height: 300px;
    overflow-y: auto;
}
.sandbox-output {
    padding: 8px 12px;
    font-family: monospace;
    font-size: 0.8rem;
    white-space: pre-wrap;
    max-height: 200px;
    overflow-y: auto;
    background: var(--surface);
}
.sandbox-output.stderr {
    color: var(--danger);
    background: rgba(255, 0, 0, 0.05);
}
.sandbox-files {
    padding: 8px 12px;
    font-size: 0.8rem;
    border-top: 1px solid var(--border);
}
.sandbox-files .file-tag {
    display: inline-block;
    background: var(--accent-bg);
    color: var(--accent);
    padding: 2px 8px;
    border-radius: 4px;
    margin: 2px 4px 2px 0;
    font-size: 0.75rem;
}
```

---

## 5. Event Contract

SSE events emitted by the sandbox tools via `AgentLogger.emit()`. All events follow the existing pattern: `{ type, timestamp, xcv, ...payload }`.

| Event Type | Payload | Emitted By | UI Action |
|---|---|---|---|
| `sandbox_code_generated` | `{ code: str, filename: str }` | `execute_python_in_sandbox` (before execution) | Create execution card, render code block |
| `sandbox_execution_started` | `{ filename: str }` | `execute_python_in_sandbox` | Set status to "Running" |
| `sandbox_execution_complete` | `{ returncode: int, stdout: str, stderr: str, files: list[str], duration_seconds: float, success: bool }` | `execute_python_in_sandbox` | Populate output sections, set final status |
| `sandbox_file_downloaded` | `{ remote_path: str, local_path: str }` | `download_sandbox_file` | Mark file as downloaded in files list |
| `sandbox_error` | `{ error: str, filename: str }` | `execute_python_in_sandbox` (on exception) | Show error state |

These events are automatically picked up by the SSE stream because they flow through the existing `AgentLogger` → `subscribe_events` → SSE pipeline. No changes to the SSE infrastructure needed.

---

## 6. Environment & Dependencies

### 6.1 New Environment Variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `PYTHON_CUSTOM_POOL_ENDPOINT` | Yes | (none) | PythonCustomPool container URL |
| `PYTHON_CUSTOM_POOL_SESSION_ID` | No | `customeragent-default` | Default session identifier |
| `PYTHON_CUSTOM_POOL_TIMEOUT_SECONDS` | No | `180` | Script execution timeout |
| `PYTHON_CUSTOM_POOL_DOWNLOAD_DIR` | No | `downloads` | Local dir for downloaded artifacts |

### 6.2 Python Dependencies

**No new pip dependencies required.** The sandbox client uses:
- `urllib.request` (stdlib) — HTTP calls to PythonCustomPool
- `azure.identity` (already in `requirements.txt`) — token acquisition via `helper/auth.py`
- `agent_framework` (already installed) — `@tool` decorator

### 6.3 Docker / docker-compose

**Optional:** Add `PYTHON_CUSTOM_POOL_ENDPOINT` to the `environment` section of the `customer-agent` service in `docker-compose.yml`. Not strictly required — the `.env` file is already loaded.

### 6.4 `.env.example`

Add the sandbox variables to the `.env.example` template (if one exists) so new developers know to configure them.

---

## 7. Implementation Sequence

| Step | Task | Files | Complexity | Dependencies |
|---|---|---|---|---|
| 1 | Create `src/core/sandbox/__init__.py` | New file | Low | None |
| 2 | Create `src/core/sandbox/client.py` — SandboxClient class | New file | Medium | Step 1 |
| 3 | Create `src/core/sandbox/tools.py` — @tool functions | New file | Medium | Step 2 |
| 4 | Register `"sandbox"` tool-mode handler in `agent_factory.py` | Edit `agent_factory.py` | Low | Step 3 |
| 5 | Create `src/prompts/maf_sandbox_coder_prompt.txt` | New file | Low | None |
| 6 | Add `sandbox_coder` agent config to `agents_config.json` | Edit `agents_config.json` | Low | Steps 4, 5 |
| 7 | Add sandbox env vars to `.env` | Edit `.env` | Low | None |
| 8 | Add `views/sandbox.js` frontend view | New file | Medium | None |
| 9 | Wire sandbox view into `app.js` (imports, init, event routing) | Edit `app.js` | Low | Step 8 |
| 10 | Add sandbox tab + view container to `index.html` | Edit `index.html` | Low | Step 8 |
| 11 | Add sandbox CSS styles to `styles.css` | Edit `styles.css` | Low | Step 8 |
| 12 | End-to-end test: run pipeline, verify sandbox agent executes code | Manual test | Medium | All above |

**Recommended order:** Steps 1–4 (backend sandbox module), then 5–7 (config), then 8–11 (frontend), then 12 (test).

Steps 1–4 and 5–7 can be parallelized. Steps 8–11 can also be done in parallel with backend work since they only depend on the event contract (Section 5).

---

## 8. Files Changed Summary

| File | Action | Description |
|---|---|---|
| `src/core/sandbox/__init__.py` | **New** | Package init for sandbox module |
| `src/core/sandbox/client.py` | **New** | `SandboxClient` — HTTP wrapper for PythonCustomPool API |
| `src/core/sandbox/tools.py` | **New** | `@tool` functions: `execute_python_in_sandbox`, `download_sandbox_file`, `list_sandbox_files` |
| `src/core/agent_factory.py` | **Edit** | Add `_tool_mode_sandbox()` handler + register in `TOOL_MODE_HANDLERS` dict (~8 lines) |
| `src/prompts/maf_sandbox_coder_prompt.txt` | **New** | System prompt for the sandbox_coder agent |
| `src/config/agents/agents_config.json` | **Edit** | Add `sandbox_coder` agent entry + add to `workflow.participants` |
| `.env` | **Edit** | Add `PYTHON_CUSTOM_POOL_*` environment variables |
| `CustomerAgentUI/views/sandbox.js` | **New** | Sandbox execution visualization view |
| `CustomerAgentUI/app.js` | **Edit** | Import sandbox view, add init/clear/event routing (~10 lines) |
| `CustomerAgentUI/index.html` | **Edit** | Add sandbox tab button + view container (~2 lines) |
| `CustomerAgentUI/styles.css` | **Edit** | Add sandbox view styles (~50 lines) |

**Totals:** 5 new files, 6 modified files. ~400 lines of new code (backend: ~250, frontend: ~150).
