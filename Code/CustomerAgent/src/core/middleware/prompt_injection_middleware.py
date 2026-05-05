"""
AgentMiddleware for prompt injection detection.

Runs BEFORE and AFTER each agent execution.  Sends the latest user input
(pre-execution) and the agent's generated output (post-execution) to the
RATIO prompt-injection orchestration API (``/v1/moderate``).

Pre-execution (input scan):
  If an injection attempt is detected (``finalVerdict == "INJECTION"``),
  the middleware short-circuits execution by raising ``MiddlewareTermination``
  so the agent never sees the malicious input.

Post-execution (output scan):
  If the agent's response contains injected content, the middleware replaces
  the response with a safe sentinel so the poisoned text never enters the
  GroupChat conversation pool or reaches downstream agents.

For streaming invocations (GroupChat ``stream=True``), a ``stream_result_hook``
is registered to scan the fully-assembled response after the stream completes.

Feature flag: set ENABLE_PROMPT_INJECTION=true to activate (default: false).
Per-agent toggle: ``"prompt_injection": true`` in agents_config.json.

API reference: docs/PROMPT_INJECTION_API_GUIDE.md

Environment variables:
    ENABLE_PROMPT_INJECTION   - "true" to enable (default: "false")
    PROMPT_INJECTION_API_URL  - Full URL (default: http://localhost:9001/v1/moderate)
    PROMPT_INJECTION_API_TIMEOUT - Seconds (default: 5)
    PROMPT_INJECTION_MODE     - "fast", "standard", "fast_query", "standard_query" (default: "fast")
    PROMPT_INJECTION_API_SCOPE - AAD scope for Bearer token (optional)
    ENABLE_OUTPUT_INJECTION_SCAN - "true" to enable output scanning (default: "true")
    SCAN_ORIGINAL_PROMPT_ONLY - "true" to scan only the first user message (original
                                human prompt) and skip inter-agent conversation traffic
                                in GroupChat (default: "false")
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any, Sequence

import httpx

from agent_framework import AgentMiddleware, AgentContext, AgentResponse, AgentResponseUpdate, Message, MiddlewareTermination
from agent_framework._types import ResponseStream

from helper.agent_logger import AgentLogger, get_current_xcv
from helper.auth import get_auth_token

logger = logging.getLogger(__name__)

_INJECTION_ENABLED = os.getenv("ENABLE_PROMPT_INJECTION", "false").strip().lower() in ("true", "1", "yes")
INJECTION_ENABLED = _INJECTION_ENABLED  # public alias for agent_factory import
_OUTPUT_SCAN_ENABLED = os.getenv("ENABLE_OUTPUT_INJECTION_SCAN", "true").strip().lower() in ("true", "1", "yes")
_SCAN_ORIGINAL_PROMPT_ONLY = os.getenv("SCAN_ORIGINAL_PROMPT_ONLY", "false").strip().lower() in ("true", "1", "yes")
_INJECTION_API_URL = os.getenv("PROMPT_INJECTION_API_URL", "http://localhost:9001/v1/moderate")
_INJECTION_API_TIMEOUT = float(os.getenv("PROMPT_INJECTION_API_TIMEOUT", "5"))
_INJECTION_MODE = os.getenv("PROMPT_INJECTION_MODE", "fast").strip()
_INJECTION_API_SCOPE = os.getenv("PROMPT_INJECTION_API_SCOPE", "").strip()


class PromptInjectionMiddleware(AgentMiddleware):
    """Checks agent input for prompt injection before execution.

    Usage:
        injection_mw = PromptInjectionMiddleware()
        agent = Agent(client=client, ..., middleware=[injection_mw])

    After each workflow run, call ``injection_mw.drain()`` to retrieve detections.
    """

    def __init__(self) -> None:
        self._detections: list[dict[str, Any]] = []
        self._scanned_inputs: set[str] = set()  # dedup: avoid re-scanning same text

    def reset(self) -> None:
        """Clear stored detections (call at start of each request)."""
        self._detections.clear()
        self._scanned_inputs.clear()

    def drain(self) -> list[dict[str, Any]]:
        """Return and remove all accumulated detections since last drain."""
        result = list(self._detections)
        self._detections.clear()
        return result

    async def process(self, context: AgentContext, call_next) -> None:
        if not _INJECTION_ENABLED:
            await call_next()
            return

        agent_name = context.agent.name

        # ── Phase 1: Input scanning (pre-execution) ──────────────
        # Extract the user input to scan.
        # When SCAN_ORIGINAL_PROMPT_ONLY is enabled, scan only the FIRST user
        # message (the original human prompt), ignoring inter-agent conversation
        # traffic that appears as subsequent user messages in GroupChat.
        input_text = ""
        if _SCAN_ORIGINAL_PROMPT_ONLY:
            for msg in context.messages:
                if msg.role == "user" and msg.text:
                    input_text = msg.text
                    break
        else:
            for msg in reversed(context.messages):
                if msg.role == "user" and msg.text:
                    input_text = msg.text
                    break

        # Deduplication: skip if we already scanned this exact text in this request
        if input_text and input_text in self._scanned_inputs:
            logger.debug(
                "[%s] Skipping input scan — already scanned this text.", agent_name
            )
            input_text = ""

        if input_text:
            self._scanned_inputs.add(input_text)
            # Call prompt injection detection API
            detection = await self._call_injection_api(agent_name, input_text, scan_direction="input")

            # Store every detection result (including safe ones) for audit trail
            self._detections.append(detection)

            # Log to agent logger — use "PromptInjectionDetected" event type
            # so the UI card renderer picks it up.
            xcv = get_current_xcv()
            is_injection = detection.get("finalVerdict") == "INJECTION"
            reasons = detection.get("reasons", [])

            if xcv:
                AgentLogger.get_instance()._emit(
                    "PromptInjectionDetected",
                    xcv,
                    {
                        "Agent": agent_name,
                        "ScanDirection": "input",
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
                    "[%s] Prompt injection DETECTED on INPUT (verdict=%s, reasons=%s). Blocking execution.",
                    agent_name,
                    detection.get("finalVerdict"),
                    reasons_str,
                )

                # Streaming mode: MiddlewareTermination creates an empty
                # ResponseStream with no finalizer in the framework's
                # _execute_stream() fallback. get_final_response() then returns
                # a raw list instead of AgentResponse, crashing the executor at
                # _agent_executor.py:412 with "'list' object has no attribute
                # 'user_input_requests'".
                # Fix: set context.result to a valid ResponseStream that
                # finalizes to a blocked AgentResponse and return early.
                if context.stream:
                    blocked_msg = (
                        f"[Input blocked by prompt injection shield: "
                        f"reasons=[{reasons_str}]]"
                    )
                    blocked_response = AgentResponse(messages=[
                        Message(role="assistant", contents=[blocked_msg])
                    ])

                    async def _empty_blocked_stream():
                        """Yield nothing — finalizer handles the response."""
                        return
                        yield  # noqa: unreachable — makes this an async generator

                    context.result = ResponseStream(
                        _empty_blocked_stream(),
                        finalizer=lambda _updates: blocked_response,
                    )
                    context.metadata["input_injection_blocked"] = True
                    context.metadata["input_injection_reasons"] = reasons
                    return  # Skip call_next — agent never executes

                # Non-streaming mode: terminate the pipeline outright
                raise MiddlewareTermination(
                    f"Prompt injection detected on input for agent '{agent_name}': "
                    f"reasons=[{reasons_str}]"
                )

            logger.info(
                "[%s] Prompt injection input check: SAFE (latency=%.0fms)",
                agent_name, detection.get("api_latency_ms", 0),
            )

        # ── Phase 2: Agent execution ─────────────────────────────
        await call_next()

        # ── Phase 3: Output scanning (post-execution) ────────────
        if not _OUTPUT_SCAN_ENABLED:
            return

        # Streaming path — register a hook to scan the assembled response
        if context.stream and isinstance(context.result, ResponseStream):
            self._register_output_stream_hook(context, agent_name)
            return

        # Non-streaming path — scan the agent's response immediately
        output_text = self._extract_output_text(context.result)
        if output_text:
            await self._scan_output(context, agent_name, output_text)

    async def _call_injection_api(
        self,
        agent_name: str,
        input_text: str,
        scan_direction: str = "input",
    ) -> dict[str, Any]:
        """POST input text to the /v1/moderate endpoint.

        Args:
            agent_name: Name of the agent being scanned.
            input_text: Text to scan for injection.
            scan_direction: "input" for pre-execution, "output" for post-execution.

        Request:  {"userPrompt": "...", "mode": "fast"}
        Response: {"finalVerdict": "INJECTION"|"SAFE", "reasons": [...],
                   "detectors": {...}, "latency_ms": {"end_to_end": ...}}
        """
        payload = {
            "userPrompt": input_text,
            "mode": _INJECTION_MODE,
        }

        xcv = get_current_xcv()
        headers = {"Content-Type": "application/json"}
        if xcv:
            headers["X-XCV"] = xcv

        # Attach Bearer token when a scope is configured
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

                # Normalise into our internal shape
                final_verdict = result.get("finalVerdict", "SAFE")
                reasons = result.get("reasons", [])
                detectors = result.get("detectors", {})
                api_latency = result.get("latency_ms", {}).get("end_to_end", 0)

                detection = {
                    "agent_name": agent_name,
                    "scan_direction": scan_direction,
                    "finalVerdict": final_verdict,
                    "reasons": reasons,
                    "detectors": detectors,
                    "api_latency_ms": api_latency,
                    "duration_ms": elapsed,
                }

                # Log API call details
                if xcv:
                    AgentLogger.get_instance().log_injection_api_call(
                        xcv=xcv,
                        agent_name=agent_name,
                        api_url=_INJECTION_API_URL,
                        input_text=input_text,
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
            logger.warning("[%s] Prompt injection API failed: %s — allowing execution", agent_name, exc)

            # Log failed API call
            if xcv:
                AgentLogger.get_instance().log_injection_api_call(
                    xcv=xcv,
                    agent_name=agent_name,
                    api_url=_INJECTION_API_URL,
                    input_text=input_text,
                    duration_ms=elapsed,
                    error=str(exc),
                )

            # Fail-open: if the injection API is unreachable, allow execution
            return {
                "agent_name": agent_name,
                "scan_direction": scan_direction,
                "finalVerdict": "SAFE",
                "reasons": [],
                "detectors": {},
                "api_latency_ms": 0,
                "error": str(exc),
                "duration_ms": elapsed,
            }

    # ── Output scanning helpers ──────────────────────────────────

    @staticmethod
    def _extract_output_text(result: Any) -> str:
        """Extract plain text from an AgentResponse."""
        if isinstance(result, AgentResponse):
            parts = [m.text for m in result.messages if m.text]
            return "\n".join(parts)
        return ""

    async def _scan_output(
        self,
        context: AgentContext,
        agent_name: str,
        output_text: str,
    ) -> None:
        """Scan agent output text and replace the response if injection is detected."""
        detection = await self._call_injection_api(agent_name, output_text, scan_direction="output")
        self._detections.append(detection)

        xcv = get_current_xcv()
        is_injection = detection.get("finalVerdict") == "INJECTION"
        reasons = detection.get("reasons", [])

        if xcv:
            AgentLogger.get_instance()._emit(
                "PromptInjectionDetected",
                xcv,
                {
                    "Agent": agent_name,
                    "ScanDirection": "output",
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
                "[%s] Prompt injection DETECTED on OUTPUT (verdict=%s, reasons=%s). "
                "Replacing response with safe sentinel.",
                agent_name,
                detection.get("finalVerdict"),
                reasons_str,
            )
            # Replace the agent's response so poisoned text never enters
            # the GroupChat conversation pool or reaches downstream agents.
            context.result = AgentResponse(messages=[
                Message(
                    role="assistant",
                    contents=[
                        f"[Output blocked by prompt injection shield: "
                        f"reasons=[{reasons_str}]]"
                    ],
                )
            ])
            context.metadata["output_injection_blocked"] = True
            context.metadata["output_injection_reasons"] = reasons
        else:
            logger.info(
                "[%s] Prompt injection output check: SAFE (latency=%.0fms)",
                agent_name, detection.get("api_latency_ms", 0),
            )

    def _register_output_stream_hook(self, context: AgentContext, agent_name: str) -> None:
        """Register a stream_result_hook to scan assembled output after streaming."""

        async def _scan_stream_result(response: AgentResponse) -> AgentResponse:
            output_text = "\n".join(m.text for m in response.messages if m.text)
            if not output_text:
                return response

            detection = await self._call_injection_api(
                agent_name, output_text, scan_direction="output",
            )
            self._detections.append(detection)

            xcv = get_current_xcv()
            is_injection = detection.get("finalVerdict") == "INJECTION"
            reasons = detection.get("reasons", [])

            if xcv:
                AgentLogger.get_instance()._emit(
                    "PromptInjectionDetected",
                    xcv,
                    {
                        "Agent": agent_name,
                        "ScanDirection": "output",
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
                    "[%s] Prompt injection DETECTED on streamed OUTPUT "
                    "(verdict=%s, reasons=%s). Replacing response.",
                    agent_name,
                    detection.get("finalVerdict"),
                    reasons_str,
                )
                context.metadata["output_injection_blocked"] = True
                context.metadata["output_injection_reasons"] = reasons
                return AgentResponse(messages=[
                    Message(
                        role="assistant",
                        contents=[
                            f"[Output blocked by prompt injection shield: "
                            f"reasons=[{reasons_str}]]"
                        ],
                    )
                ])

            logger.info(
                "[%s] Prompt injection streamed output check: SAFE (latency=%.0fms)",
                agent_name, detection.get("api_latency_ms", 0),
            )
            return response

        context.stream_result_hooks.append(_scan_stream_result)
