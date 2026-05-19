"""
MAF @tool functions for sandbox code execution in the Interpreter service.

These tools allow agents (correlator, action_composer) to:
- Execute Python code with ADLS_TOKEN injected per-call (mirrors the
  CustomerAgent sandbox_coder pattern)
- Stage / read JSON data on ADLS Gen2 (NOT /mnt/data; the sandbox runtime
  is stateless across calls and shares storage with CustomerAgent)
- List files on ADLS for the current correlation/xcv

ADLS coordinates (ADLS_ACCOUNT, ADLS_FILESYSTEM, ADLS_BASE_PATH) and the
per-call ADLS_TOKEN + ADLS_TOKEN_EXPIRES_ON are injected as module-level
constants by `SandboxClient.execute()`. See the prompt boilerplate for
the `_StaticTokenCredential` helper used inside generated scripts.
"""
from __future__ import annotations

import ast
import json
import logging
import os

from agent_framework import tool

from helper.agent_logger import (
    AgentLogger,
    get_current_correlation_id,
    get_current_outcome_xcvs,
)
from sandbox.client import SandboxClient

logger = logging.getLogger(__name__)

# ─── Preamble injected into every sandbox script ─────────────────────────────
# Sets up numpy/pandas-aware json.dumps default + ADLS Gen2 helpers. The
# helpers reference the per-call ADLS_TOKEN / ADLS_TOKEN_EXPIRES_ON /
# ADLS_ACCOUNT / ADLS_FILESYSTEM constants which the runtime injects into
# the script's globals. Pre-injecting them here saves the LLM from
# re-emitting ~1.5 KB of identical boilerplate in every script — that
# bytes saving is critical because gpt-4o's tool-call ``arguments`` JSON
# starts mis-escaping backslashes at ~10 KB script size, producing
# "Argument parsing failed" errors that abort the whole composition.
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
    try:
        if pd.isna(obj): return None
    except (TypeError, ValueError, RecursionError):
        pass
    return str(obj)

import json as _orig_json
_orig_json_dumps = _orig_json.dumps
def _patched_json_dumps(*args, **kwargs):
    kwargs.setdefault("default", _safe)
    try:
        return _orig_json_dumps(*args, **kwargs)
    except (ValueError, TypeError, RecursionError):
        kwargs["default"] = str
        return _orig_json_dumps(*args, **kwargs)
_orig_json.dumps = _patched_json_dumps

# ── ADLS helpers — pre-injected so scripts don't have to redefine them ──
from azure.core.credentials import AccessToken, TokenCredential
from azure.storage.filedatalake import DataLakeServiceClient

class _StaticTokenCredential(TokenCredential):
    def __init__(self, token, expires_on):
        self._t = AccessToken(token, int(expires_on))
    def get_token(self, *scopes, **kw):
        return self._t

_FS = DataLakeServiceClient(
    account_url=f"https://{ADLS_ACCOUNT}.dfs.core.windows.net",
    credential=_StaticTokenCredential(ADLS_TOKEN, ADLS_TOKEN_EXPIRES_ON),
).get_file_system_client(ADLS_FILESYSTEM)

def adls_exists(path):
    try:
        _FS.get_file_client(path.lstrip("/")).get_file_properties()
        return True
    except Exception:
        return False

def adls_read_text(path):
    return _FS.get_file_client(path.lstrip("/")).download_file().readall().decode("utf-8")

def adls_write_text(path, content):
    _FS.get_file_client(path.lstrip("/")).upload_data(content.encode("utf-8"), overwrite=True)

def adls_list(prefix):
    try:
        return [p.name for p in _FS.get_paths(path=prefix.lstrip("/"), recursive=True) if not p.is_directory]
    except Exception:
        return []
'''

# Module-level client instance (lazy-init)
_client: SandboxClient | None = None


def _get_client() -> SandboxClient:
    global _client
    if _client is None:
        _client = SandboxClient()
    return _client


def _adls_base() -> str:
    """Write root for THIS service (Interpreter outputs)."""
    return os.getenv("ADLS_BASE_PATH", "runs").strip("/")


def _adls_source_base() -> str:
    """Read root for upstream data (CustomerAgent outputs).

    Falls back to ``ADLS_BASE_PATH`` when ``ADLS_SOURCE_BASE_PATH`` is unset,
    so single-folder deployments keep working.
    """
    return (
        os.getenv("ADLS_SOURCE_BASE_PATH", "").strip("/") or _adls_base()
    )


@tool(name="execute_python_in_sandbox")
async def execute_python_in_sandbox(
    code: str | None = None,
    filename: str = "interpreter_script.py",
    script_path: str | None = None,
) -> str:
    """Execute Python code in a secure sandbox container and return the result.

    The sandbox has numpy, pandas, scipy, scikit-learn, and the
    `azure-storage-file-datalake` SDK pre-installed. ADLS coordinates and a
    fresh per-call ADLS bearer token are injected as module-level constants
    (see prompt boilerplate). There is NO `/mnt/data` — all I/O goes through
    ADLS Gen2.

    You MUST provide exactly one of ``code`` or ``script_path``:

    Args:
        code: Python source code to execute. Must print results to stdout.
            Use this for short scripts (< 4 KB). For longer scripts, prefer
            ``script_path`` to avoid JSON-arg escape failures at scale.
        filename: Name for the script file (default: interpreter_script.py).
        script_path: ADLS path to a pre-staged Python script. When provided,
            the file is downloaded from ADLS and executed in place of
            ``code``. Use this when the orchestrator has pre-staged the
            canonical script for you (e.g. composer template) — it avoids
            embedding 10 KB+ source into the tool-call arguments JSON which
            gpt-4o frequently corrupts via mis-escaped backslashes.

    Returns:
        JSON string with returncode, stdout, stderr, files, and duration.
    """
    tracker = AgentLogger.get_instance()
    correlation_id = get_current_correlation_id() or "interpreter"
    outcome_xcvs = get_current_outcome_xcvs() or []

    # Resolve script source: explicit code takes precedence, then script_path.
    if code is None and script_path:
        try:
            client = _get_client()
            code = await client.read_file(script_path)
            logger.info(
                "[SANDBOX] Loaded staged script from %s (%d chars)",
                script_path, len(code),
            )
        except Exception as exc:
            logger.exception(
                "[SANDBOX] Failed to load staged script from %s", script_path,
            )
            return json.dumps({
                "success": False,
                "error": f"Failed to load script_path '{script_path}': {exc}",
            })
    if not code:
        return json.dumps({
            "success": False,
            "error": "Must provide either 'code' or 'script_path'.",
        })

    full_code = _SANDBOX_PREAMBLE + code

    logger.info(
        "[SANDBOX] execute_python_in_sandbox: code_len=%d, filename=%s, cid=%s",
        len(code), filename, correlation_id,
    )
    # Persist the exact code the LLM passed in to ADLS so we can diff
    # against what actually executed when "argument parsing failures"
    # bubble up. Best-effort — never raises.
    try:
        adls_base = _adls_base()
        # Include a monotonic counter via timestamp-ms so multiple calls in
        # the same correlation don't overwrite each other.
        import time as _time
        ts = int(_time.time() * 1000)
        debug_path = f"{adls_base}/{correlation_id}/composer/debug/sandbox_call_{ts}_{filename}"
        await _get_client().upload_file(debug_path, code)
        logger.info("[SANDBOX] Persisted received code to %s", debug_path)
    except Exception:
        logger.exception("[SANDBOX] Failed to persist received code")
    tracker.log_agent_invoked("sandbox_execute", f"[{filename}] {code[:500]}")

    client = _get_client()
    try:
        result = await client.execute(
            code=full_code,
            filename=filename,
            extra_constants={
                "CORRELATION_ID": correlation_id,
                "OUTCOME_XCVS": outcome_xcvs,
            },
            inject_adls_token=True,
        )
        logger.info(
            "[SANDBOX] Execution complete: returncode=%d, duration=%.2fs",
            result.returncode, result.duration_seconds,
        )
        tracker.log_agent_completed(
            "sandbox_execute",
            f"rc={result.returncode} stdout_len={len(result.stdout)}",
            result.duration_seconds * 1000,
        )
        return json.dumps({
            "success": result.success,
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "files": result.files,
            "duration_seconds": result.duration_seconds,
        })
    except Exception as exc:
        tracker.log_agent_error("sandbox_execute", str(exc))
        return json.dumps({"success": False, "error": str(exc)})


def _try_merge_concatenated_json(s: str) -> dict | list | None:
    """Recover from LLM passing multiple JSON documents in one string.

    Uses ``json.JSONDecoder.raw_decode`` to scan top-level documents one at
    a time. If all documents are dicts, returns a merged dict (later keys
    win). If any are lists, returns the concatenated list. Returns ``None``
    when parsing fails or yields a single document (caller should fall back).

    The decoder is created with ``strict=False`` so raw control characters
    inside strings (the most common LLM-emitted JSON defect, e.g. literal
    newlines in templated text) do not abort the scan before we even see
    the second document.
    """
    decoder = json.JSONDecoder(strict=False)
    docs: list = []
    idx = 0
    n = len(s)
    while idx < n:
        # Skip whitespace between documents.
        while idx < n and s[idx].isspace():
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(s, idx)
        except json.JSONDecodeError:
            return None
        docs.append(obj)
        idx = end
    if len(docs) < 2:
        return None
    if all(isinstance(d, dict) for d in docs):
        merged: dict = {}
        for d in docs:
            merged.update(d)
        return merged
    if all(isinstance(d, list) for d in docs):
        out: list = []
        for d in docs:
            out.extend(d)
        return out
    # Mixed types — wrap as a list so nothing is lost.
    return docs


@tool(name="write_data_to_sandbox")
async def write_data_to_sandbox(
    filepath: str,
    data_json: str,
) -> str:
    """Write JSON data to ADLS at ``{ADLS_BASE_PATH}/{CORRELATION_ID}/{filepath}``.

    The CORRELATION_ID path segment isolates each Interpreter window's
    artifacts so multiple runs (and the same correlation re-processed on a
    different window) do not collide. Use this to stage outcomes / deduped
    actions / correlator output that subsequent sandbox scripts read via
    the ADLS helpers.

    Args:
        filepath: Path under the current window directory
                  (e.g. "outcomes.json" or "composer/input/actions.json").
                  Leading slashes are stripped.
        data_json: JSON string to write.

    Returns:
        Confirmation message (with full ADLS path) or error JSON.
    """
    tracker = AgentLogger.get_instance()
    correlation_id = get_current_correlation_id() or "interpreter"
    relative = filepath.lstrip("/")
    adls_path = f"{_adls_base()}/{correlation_id}/{relative}"
    client = _get_client()
    try:
        # Validate / coerce JSON. Accept four forms the LLM commonly emits:
        #   1. Strict JSON (fast path).
        #   2. JSON with raw control chars in strings (newlines/tabs) — allow
        #      via strict=False and re-serialise to canonical JSON.
        #   3. Two or more JSON documents concatenated (``{...}{...}``) —
        #      common LLM bug when staging multiple inputs in one call. We
        #      merge top-level dicts (later keys win) or concat top-level
        #      lists. This is the failure shown by the
        #      ``Extra data: line 1 column N`` decoder error.
        #   4. Python-dict literal (single quotes, unquoted keys, True/None) —
        #      parse with ast.literal_eval and re-serialise.
        # In every branch we re-serialise with ``indent=2`` so the JSON
        # files persisted to ADLS stay human-readable in Storage Explorer.
        try:
            parsed = json.loads(data_json)
            payload = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
        except json.JSONDecodeError as strict_err:
            try:
                parsed = json.loads(data_json, strict=False)
                payload = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
                logger.warning(
                    "write_data_to_sandbox: input had control chars in strings; "
                    "re-serialised (path=%s)", adls_path,
                )
            except json.JSONDecodeError:
                # Try concatenated-JSON recovery before falling back to ast.
                merged = _try_merge_concatenated_json(data_json)
                if merged is not None:
                    parsed = merged
                    payload = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
                    logger.warning(
                        "write_data_to_sandbox: input had multiple concatenated "
                        "JSON documents; merged (path=%s)", adls_path,
                    )
                else:
                    try:
                        parsed = ast.literal_eval(data_json)
                    except (ValueError, SyntaxError):
                        raise strict_err
                    if not isinstance(parsed, (dict, list)):
                        raise strict_err
                    payload = json.dumps(parsed, ensure_ascii=False, indent=2, default=str)
                    logger.warning(
                        "write_data_to_sandbox: input was Python-dict literal; "
                        "coerced to JSON (path=%s)", adls_path,
                    )
        await client.upload_file(adls_path, payload)
        tracker.log_adls_write(adls_path, len(payload))
        return json.dumps({"success": True, "path": adls_path})
    except Exception as exc:
        logger.exception("write_data_to_sandbox failed for %s", adls_path)
        return json.dumps({"success": False, "error": str(exc), "path": adls_path})


@tool(name="read_sandbox_file")
async def read_sandbox_file(adls_path: str) -> str:
    """Read a text file from ADLS Gen2 and return its content.

    Args:
        adls_path: Path under the configured ADLS filesystem
                   (e.g. "runs/<xcv>/evidence/sli_customer.json"
                   or "runs/<correlation_id>/outcomes.json").

    Returns:
        File content as string, or JSON error.
    """
    client = _get_client()
    try:
        content = await client.read_file(adls_path)
        return content
    except Exception as exc:
        logger.warning("read_sandbox_file failed for %s: %s", adls_path, exc)
        return json.dumps({"success": False, "error": str(exc), "path": adls_path})


@tool(name="list_sandbox_files")
async def list_sandbox_files(adls_path: str = "") -> str:
    """List files at an ADLS prefix.

    If ``adls_path`` is empty, lists files under
    ``{ADLS_BASE_PATH}/{CORRELATION_ID}/`` for the current correlation.

    Args:
        adls_path: Optional ADLS prefix. Defaults to the current correlation dir.

    Returns:
        JSON list of file paths.
    """
    if not adls_path:
        correlation_id = get_current_correlation_id() or "interpreter"
        adls_path = f"{_adls_base()}/{correlation_id}"
    client = _get_client()
    try:
        files = await client.list_files(adls_path=adls_path, recursive=True)
        return json.dumps(files)
    except Exception as exc:
        logger.warning("list_sandbox_files failed for %s: %s", adls_path, exc)
        return json.dumps({"success": False, "error": str(exc), "path": adls_path})


@tool(name="read_sandbox_manifest")
async def read_sandbox_manifest(xcv_or_correlation_id: str = "") -> str:
    """Read the evidence manifest for a CustomerAgent investigation or
    correlation batch.

    Resolution order:
    1. Try ``{ADLS_SOURCE_BASE_PATH}/{id}/_manifest.json`` (CustomerAgent root).
    2. Fall back to ``{ADLS_BASE_PATH}/{id}/_manifest.json`` (Interpreter root)
       so manifests written by this service are still discoverable.

    If no id is supplied, defaults to the current CORRELATION_ID.

    Args:
        xcv_or_correlation_id: An XCV (CustomerAgent investigation id) to
            read its evidence manifest, OR a correlation_id for the
            interpreter batch manifest. Defaults to the current correlation_id.

    Returns:
        Manifest JSON content as string, or JSON error.
    """
    target = xcv_or_correlation_id or get_current_correlation_id() or "interpreter"
    source_path = f"{_adls_source_base()}/{target}/_manifest.json"
    fallback_path = f"{_adls_base()}/{target}/_manifest.json"
    client = _get_client()
    paths_tried: list[str] = [source_path]
    try:
        content = await client.read_file(source_path)
        logger.info("[SANDBOX] Read manifest: %d chars from adls:%s", len(content), source_path)
        return content
    except Exception as source_exc:
        if fallback_path == source_path:
            logger.warning("[SANDBOX] Manifest not found at adls:%s: %s", source_path, source_exc)
            return json.dumps({
                "success": False,
                "error": str(source_exc),
                "path": source_path,
                "hint": "Use list_sandbox_files to see what files exist.",
            })
        paths_tried.append(fallback_path)
        try:
            content = await client.read_file(fallback_path)
            logger.info(
                "[SANDBOX] Read manifest from fallback: %d chars from adls:%s",
                len(content), fallback_path,
            )
            return content
        except Exception as fallback_exc:
            logger.warning(
                "[SANDBOX] Manifest not found at adls:%s nor adls:%s: %s / %s",
                source_path, fallback_path, source_exc, fallback_exc,
            )
            return json.dumps({
                "success": False,
                "error": str(fallback_exc),
                "paths_tried": paths_tried,
                "hint": "Use list_sandbox_files to see what files exist.",
            })


# ── Tool registry (for factory tool_mode resolution) ─────────────────────────
ALL_SANDBOX_TOOLS = [
    execute_python_in_sandbox,
    write_data_to_sandbox,
    read_sandbox_file,
    list_sandbox_files,
    read_sandbox_manifest,
]
