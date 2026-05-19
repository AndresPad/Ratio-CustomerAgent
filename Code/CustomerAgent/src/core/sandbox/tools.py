"""
MAF @tool functions for sandbox code execution.

Each function wraps SandboxClient methods and emits AgentLogger events
for UI visualization (sandbox_* SSE events).
"""
from __future__ import annotations

import json
import logging

from agent_framework import tool

from helper.agent_logger import AgentLogger, get_current_xcv
from .client import SandboxClient

logger = logging.getLogger(__name__)

# ─── Preamble injected into every sandbox script ─────────────────────────────
_SANDBOX_PREAMBLE = '''\
import json as _json_mod
import numpy as np
import pandas as pd

def _safe(obj):
    """Convert numpy/pandas types to native Python for json.dumps()."""
    if isinstance(obj, (np.integer,)): return int(obj)
    if isinstance(obj, (np.floating,)): return float(obj)
    if isinstance(obj, (np.bool_,)): return bool(obj)
    if isinstance(obj, (np.ndarray,)): return obj.tolist()
    if isinstance(obj, (pd.Timestamp,)): return obj.isoformat()
    if isinstance(obj, (dict, list, tuple)):
        return obj  # natively serializable — let encoder recurse normally
    try:
        if pd.isna(obj): return None
    except (TypeError, ValueError, RecursionError):
        pass
    return str(obj)

def _json_dumps(obj, **kw):
    """Safe json.dumps: handles numpy/pandas types AND circular references."""
    kw.setdefault("default", _safe)
    kw.setdefault("indent", 2)
    try:
        return _json_mod.dumps(obj, **kw)
    except (ValueError, TypeError):
        def _break_cycles(o, _seen=None):
            if _seen is None: _seen = set()
            oid = id(o)
            if isinstance(o, dict):
                if oid in _seen: return "{...circular...}"
                _seen.add(oid)
                return {k: _break_cycles(v, _seen) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                if oid in _seen: return "[...circular...]"
                _seen.add(oid)
                return [_break_cycles(v, _seen) for v in o]
            if hasattr(o, '__dict__'):
                if oid in _seen: return f"<circular {type(o).__name__}>"
                _seen.add(oid)
                return _break_cycles(vars(o), _seen)
            return o
        kw["check_circular"] = False
        return _json_mod.dumps(_break_cycles(obj), **kw)

# Monkey-patch json.dumps so any script using json.dumps(...) gets safety for free
import json as _orig_json
_orig_json_dumps = _orig_json.dumps
def _patched_json_dumps(*args, **kwargs):
    # Always force our _safe as default — even if caller passes their own broken one
    kwargs["default"] = _safe
    try:
        return _orig_json_dumps(*args, **kwargs)
    except (ValueError, TypeError, RecursionError):
        # Circular reference — rebuild without cycles
        obj = args[0] if args else kwargs.get("obj")
        def _break_cycles(o, _seen=None):
            if _seen is None: _seen = set()
            oid = id(o)
            if isinstance(o, dict):
                if oid in _seen: return "{...circular...}"
                _seen.add(oid)
                return {k: _break_cycles(v, _seen) for k, v in o.items()}
            if isinstance(o, (list, tuple)):
                if oid in _seen: return "[...circular...]"
                _seen.add(oid)
                return [_break_cycles(v, _seen) for v in o]
            if hasattr(o, '__dict__'):
                if oid in _seen: return f"<circular {type(o).__name__}>"
                _seen.add(oid)
                return _break_cycles(vars(o), _seen)
            return o
        cleaned = _break_cycles(obj)
        new_args = (cleaned,) + args[1:]
        kwargs["check_circular"] = False
        kwargs["default"] = str
        return _orig_json_dumps(*new_args, **kwargs)
_orig_json.dumps = _patched_json_dumps
'''

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

    tracker = AgentLogger.get_instance()
    xcv = get_current_xcv() or "sandbox"

    # Prepend safety preamble (handles numpy/pandas types + circular refs).
    # XCV (and ADLS_* coordinates + per-call ADLS_TOKEN) are injected as
    # module-level constants by SandboxClient.execute.
    full_code = _SANDBOX_PREAMBLE + code

    logger.info("[SANDBOX] execute_python_in_sandbox called! code_len=%d, filename=%s, xcv=%s",
                len(code), filename, xcv)

    logger.info("[SANDBOX] Emitting sandbox_code_generated event")
    tracker._emit("sandbox_code_generated", xcv, {
        "code": code,
        "script_filename": filename,
    })
    logger.info("[SANDBOX] Emitting sandbox_execution_started event")
    tracker._emit("sandbox_execution_started", xcv, {
        "script_filename": filename,
    })

    client = _get_client()
    try:
        logger.info("[SANDBOX] Calling client.execute...")

        result = await client.execute(
            code=full_code,
            filename=filename,
            extra_constants={"XCV": xcv},
        )
        logger.info("[SANDBOX] Execution complete: returncode=%d, stdout_len=%d, stderr_len=%d, duration=%.2fs",
                    result.returncode, len(result.stdout), len(result.stderr), result.duration_seconds)
        tracker._emit("sandbox_execution_complete", xcv, {
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
        tracker._emit("sandbox_error", xcv, {
            "error": str(exc),
            "script_filename": filename,
        })
        return json.dumps({"success": False, "error": str(exc)})


@tool(name="list_sandbox_files")
async def list_sandbox_files() -> str:
    """List all files for the current investigation in ADLS.

    Lists everything under {ADLS_BASE_PATH}/{XCV}/ in the configured ADLS
    Gen2 filesystem. /mnt/data is no longer used.

    Returns:
        Newline-separated list of ADLS paths.
    """
    import os
    xcv = get_current_xcv() or "sandbox"
    base = os.getenv("ADLS_BASE_PATH", "runs").strip("/")
    prefix = f"{base}/{xcv}"
    client = _get_client()
    files = await client.list_files(adls_path=prefix, recursive=True)
    return "\n".join(files) if files else "(no files)"


@tool(name="read_sandbox_manifest")
async def read_sandbox_manifest() -> str:
    """Read the evidence manifest for the current investigation from ADLS.

    Reads {ADLS_BASE_PATH}/{XCV}/_manifest.json which lists all evidence files
    deposited by data_fetcher (paths, row counts, schemas, descriptions).
    Call this BEFORE writing analysis code to discover available evidence.

    Returns:
        JSON string with the manifest content, or an error envelope if missing.
    """
    import os
    xcv = get_current_xcv() or "sandbox"
    base = os.getenv("ADLS_BASE_PATH", "runs").strip("/")
    manifest_path = f"{base}/{xcv}/_manifest.json"

    client = _get_client()
    try:
        content = await client.read_file(manifest_path)
        logger.info("[SANDBOX] Read manifest: %d chars from adls:%s", len(content), manifest_path)
        return content.strip()
    except Exception as exc:
        logger.warning("[SANDBOX] Manifest not found at adls:%s: %s", manifest_path, exc)
        return json.dumps({
            "error": f"Manifest not found at adls://{manifest_path}",
            "suggestion": "The data_fetcher may not have deposited evidence yet. "
                          "Use list_sandbox_files to check what files exist.",
        })
