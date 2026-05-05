"""
FunctionMiddleware for scanning MCP tool outputs for prompt injection.

Runs AFTER each tool execution.  Sends the tool's return value to
the RATIO prompt-injection orchestration API (``/v1/moderate``).
If injection is detected in the tool output, the result is replaced
with a safe sentinel so poisoned external data (Kusto rows, IcM incidents,
support tickets) never reaches the agent's reasoning loop.

Feature flag: set ENABLE_TOOL_OUTPUT_INJECTION_SCAN=true to activate (default: false).
Uses the same PI API endpoint and auth as PromptInjectionMiddleware.

Environment variables:
    ENABLE_TOOL_OUTPUT_INJECTION_SCAN - "true" to enable (default: "false")
    PROMPT_INJECTION_API_URL          - Full URL (shared with PromptInjectionMiddleware)
    PROMPT_INJECTION_API_TIMEOUT      - Seconds (shared)
    PROMPT_INJECTION_MODE             - Detection mode (shared)
    PROMPT_INJECTION_API_SCOPE        - AAD scope (shared)
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

import httpx

from agent_framework import FunctionInvocationContext, FunctionMiddleware

from helper.agent_logger import AgentLogger, get_current_xcv
from helper.auth import get_auth_token

logger = logging.getLogger(__name__)

_TOOL_INJECTION_ENABLED = os.getenv(
    "ENABLE_TOOL_OUTPUT_INJECTION_SCAN", "false",
).strip().lower() in ("true", "1", "yes")
TOOL_INJECTION_ENABLED = _TOOL_INJECTION_ENABLED  # public alias

# Reuse same PI API config as the agent-level middleware
_INJECTION_API_URL = os.getenv("PROMPT_INJECTION_API_URL", "http://localhost:9001/v1/moderate")
_INJECTION_API_TIMEOUT = float(os.getenv("PROMPT_INJECTION_API_TIMEOUT", "5"))
_INJECTION_MODE = os.getenv("PROMPT_INJECTION_MODE", "fast").strip()
_INJECTION_API_SCOPE = os.getenv("PROMPT_INJECTION_API_SCOPE", "").strip()


def _serialize_result(result: Any) -> str:
    """Convert a tool-call result to scannable text."""
    if isinstance(result, list):
        parts: list[str] = []
        for item in result:
            if hasattr(item, "to_dict"):
                d = item.to_dict()
                if d.get("type") == "text" and "text" in d:
                    parts.append(d["text"])
                else:
                    parts.append(json.dumps(d, default=str))
            else:
                parts.append(str(item))
        return "\n".join(parts) if parts else ""
    if hasattr(result, "to_dict"):
        d = result.to_dict()
        if d.get("type") == "text" and "text" in d:
            return d["text"]
        return json.dumps(d, default=str)
    return str(result) if result is not None else ""


class ToolOutputInjectionMiddleware(FunctionMiddleware):
    """Scans MCP tool outputs for prompt injection before the agent sees them.

    Usage:
        mw = ToolOutputInjectionMiddleware()
        agent = Agent(client=client, ..., middleware=[mw])

    Call ``drain()`` after each agent turn to retrieve detection records.
    """

    def __init__(self) -> None:
        self._detections: list[dict[str, Any]] = []

    def reset(self) -> None:
        """Clear stored detections (call at start of each request)."""
        self._detections.clear()

    def drain(self) -> list[dict[str, Any]]:
        """Return and remove all accumulated detections since last drain."""
        result = list(self._detections)
        self._detections.clear()
        return result

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next,
    ) -> None:
        # Always let the tool execute first
        await call_next()

        if not _TOOL_INJECTION_ENABLED:
            return

        # Extract tool result as scannable text
        result_text = _serialize_result(context.result)
        if not result_text or len(result_text.strip()) < 10:
            return

        tool_name = context.function.name if context.function else "unknown"

        # Derive agent name from metadata or function plugin
        agent_name = ""
        if context.metadata:
            agent_name = context.metadata.get("agent_name", "")
        if not agent_name and context.function:
            agent_name = getattr(context.function, "plugin_name", "") or ""
        if not agent_name:
            agent_name = tool_name

        detection = await self._call_injection_api(agent_name, tool_name, result_text)
        self._detections.append(detection)

        is_injection = detection.get("finalVerdict") == "INJECTION"
        reasons = detection.get("reasons", [])

        xcv = get_current_xcv()
        if xcv:
            AgentLogger.get_instance()._emit(
                "PromptInjectionDetected",
                xcv,
                {
                    "Agent": agent_name,
                    "ScanDirection": "tool_output",
                    "ToolName": tool_name,
                    "IsInjection": is_injection,
                    "FinalVerdict": detection.get("finalVerdict", "UNKNOWN"),
                    "Reasons": reasons,
                    "Detectors": detection.get("detectors", {}),
                    "ApiLatencyMs": detection.get("api_latency_ms", 0),
                    "DurationMs": detection.get("duration_ms", 0),
                },
            )

        if is_injection:
            reasons_str = ", ".join(reasons) if reasons else "unknown"
            logger.warning(
                "[%s] Prompt injection DETECTED in tool output from '%s' "
                "(verdict=%s, reasons=%s). Replacing result.",
                agent_name, tool_name,
                detection.get("finalVerdict"), reasons_str,
            )
            # Replace the tool result so the agent never sees the poisoned data.
            # Use a list with a single text Content-like object to match the
            # framework's expected tool result format.
            from agent_framework import Content

            context.result = [
                Content.from_text(
                    f"[Tool output blocked by prompt injection shield: "
                    f"tool={tool_name}, reasons=[{reasons_str}]]"
                )
            ]
        else:
            logger.info(
                "[%s] Tool output injection check for '%s': SAFE (latency=%.0fms)",
                agent_name, tool_name, detection.get("api_latency_ms", 0),
            )

    async def _call_injection_api(
        self,
        agent_name: str,
        tool_name: str,
        result_text: str,
    ) -> dict[str, Any]:
        """POST tool output text to the /v1/moderate endpoint."""
        payload = {
            "userPrompt": result_text,
            "mode": _INJECTION_MODE,
        }

        xcv = get_current_xcv()
        headers = {"Content-Type": "application/json"}
        if xcv:
            headers["X-XCV"] = xcv

        token = get_auth_token(_INJECTION_API_SCOPE)
        if token:
            headers["Authorization"] = f"Bearer {token}"

        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    _INJECTION_API_URL,
                    json=payload,
                    headers=headers,
                    timeout=_INJECTION_API_TIMEOUT,
                )
                resp.raise_for_status()
                result = resp.json()
                elapsed = round((time.monotonic() - t0) * 1000, 1)

                final_verdict = result.get("finalVerdict", "SAFE")
                reasons = result.get("reasons", [])
                detectors = result.get("detectors", {})
                api_latency = result.get("latency_ms", {}).get("end_to_end", 0)

                detection = {
                    "agent_name": agent_name,
                    "tool_name": tool_name,
                    "scan_direction": "tool_output",
                    "finalVerdict": final_verdict,
                    "reasons": reasons,
                    "detectors": detectors,
                    "api_latency_ms": api_latency,
                    "duration_ms": elapsed,
                }

                if xcv:
                    AgentLogger.get_instance().log_injection_api_call(
                        xcv=xcv,
                        agent_name=agent_name,
                        api_url=_INJECTION_API_URL,
                        input_text=f"[tool:{tool_name}] {result_text[:500]}",
                        http_status=resp.status_code,
                        response_body=resp.text,
                        final_verdict=final_verdict,
                        reasons=reasons,
                        api_latency_ms=api_latency,
                        duration_ms=elapsed,
                    )

                return detection
        except Exception as exc:
            elapsed = round((time.monotonic() - t0) * 1000, 1)
            logger.warning(
                "[%s] Tool output injection API failed for '%s': %s — allowing result",
                agent_name, tool_name, exc,
            )

            if xcv:
                AgentLogger.get_instance().log_injection_api_call(
                    xcv=xcv,
                    agent_name=agent_name,
                    api_url=_INJECTION_API_URL,
                    input_text=f"[tool:{tool_name}] {result_text[:500]}",
                    duration_ms=elapsed,
                    error=str(exc),
                )

            # Fail-open: allow execution if PI API is unreachable
            return {
                "agent_name": agent_name,
                "tool_name": tool_name,
                "scan_direction": "tool_output",
                "finalVerdict": "SAFE",
                "reasons": [],
                "detectors": {},
                "api_latency_ms": 0,
                "error": str(exc),
                "duration_ms": elapsed,
            }
