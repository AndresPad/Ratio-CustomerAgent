"""
AgentMiddleware that validates agent output format before it reaches the GroupChat.

Intercepts after the agent produces a response and checks:
  1. Response is not too short (< 20 chars)
  2. Agents in REQUIRED_FIELDS map have a valid JSON block
  3. Required fields in the JSON block are non-empty
  4. No text degeneration (garble/placeholder patterns)

On failure, injects a corrective message and retries the LLM **once**.
Sets ``context.metadata["output_format_retried"] = True`` so the downstream
speaker-selector garbled-retry mechanism counts this attempt and avoids
redundant retries.

For streaming invocations (GroupChat ``stream=True``), the middleware registers
a ``stream_result_hook`` that flags invalid output in metadata for the speaker
selector to handle, since the stream cannot be re-wound.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from agent_framework import AgentMiddleware, AgentContext, AgentResponse, Message
from agent_framework._types import ResponseStream

logger = logging.getLogger(__name__)

# Reuse validation constants from the output parser to stay in sync.
_AGENT_REQUIRED_FIELDS: dict[str, list[str]] = {
    "reasoner": ["evaluations"],
    "evidence_planner": ["evidence_items"],
    "action_planner": ["actions"],
    "triage_agent": ["symptoms"],
}

# Field aliases: primary_name → list of alternative keys accepted by the output parser.
# Must stay in sync with investigation_output_parser.py field extraction logic.
_FIELD_ALIASES: dict[str, list[str]] = {
    "symptoms": ["validated_symptoms"],
    "evidence_items": ["evidence_collected"],
}

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_GARBLE_PATTERN = re.compile(r"[<>\*\{\}\[\]`]{3,}")
_PLACEHOLDER_PATTERN = re.compile(
    r"placeholder|lorem ipsum|TODO|FIXME|<\*>|<>",
    re.IGNORECASE,
)

# ── Correction prompt injected on validation failure ──────────────
_CORRECTION_TEMPLATE = (
    "Your previous response was malformed ({reason}). "
    "You MUST produce ONLY a valid ```json block containing "
    '"structured_output" and "signals" keys. '
    "Do NOT include any other text outside the JSON block."
)


class OutputFormatMiddleware(AgentMiddleware):
    """Pre-emission JSON format validator with 1-retry for investigation agents."""

    def __init__(self, *, agent_name: str) -> None:
        self._agent_name = agent_name

    # ── AgentMiddleware entry point ───────────────────────────────
    async def process(self, context: AgentContext, call_next) -> None:
        await call_next()

        if not self._should_validate():
            return

        # Streaming path — can't re-invoke, register a post-stream hook instead
        if context.stream and isinstance(context.result, ResponseStream):
            self._register_stream_hook(context)
            return

        # Non-streaming path — validate and optionally retry once
        text = self._extract_text(context.result)
        is_valid, reason = self._validate(text)

        if is_valid:
            return

        logger.warning(
            "[%s] OutputFormatMiddleware: invalid output (%s), retrying once",
            self._agent_name, reason,
        )
        context.messages.append(Message(
            role="user",
            contents=[_CORRECTION_TEMPLATE.format(reason=reason)],
        ))
        context.result = None
        await call_next()

        # Mark that we used our retry — speaker selector should count this
        context.metadata["output_format_retried"] = True

        # Accept whatever comes back (no infinite loop)
        retry_text = self._extract_text(context.result)
        retry_valid, retry_reason = self._validate(retry_text)
        if not retry_valid:
            logger.warning(
                "[%s] OutputFormatMiddleware: still invalid after retry (%s), "
                "passing through for speaker-selector fallback",
                self._agent_name, retry_reason,
            )

    # ── Streaming hook ────────────────────────────────────────────
    def _register_stream_hook(self, context: AgentContext) -> None:
        """Register a stream_result_hook to validate the final assembled response."""
        agent_name = self._agent_name

        async def _validate_final(response: AgentResponse) -> AgentResponse:
            text = "\n".join(m.text for m in response.messages if m.text)
            is_valid, reason = self._validate(text)
            if not is_valid:
                context.metadata["output_format_invalid"] = True
                context.metadata["output_format_reason"] = reason
                logger.warning(
                    "[%s] OutputFormatMiddleware (stream hook): invalid output (%s)",
                    agent_name, reason,
                )
            return response

        context.stream_result_hooks.append(_validate_final)

    # ── Helpers ───────────────────────────────────────────────────
    def _should_validate(self) -> bool:
        return self._agent_name in _AGENT_REQUIRED_FIELDS

    def _extract_text(self, result: Any) -> str:
        if isinstance(result, AgentResponse):
            return "\n".join(m.text for m in result.messages if m.text)
        return ""

    def _validate(self, text: str) -> tuple[bool, str]:
        """Validate agent output. Returns (is_valid, reason_if_invalid)."""
        agent = self._agent_name
        required_fields = _AGENT_REQUIRED_FIELDS.get(agent)
        if not required_fields:
            return True, ""

        # Check 1: Too short
        if len(text.strip()) < 20:
            return False, "response_too_short"

        # Check 2: Missing JSON block
        import json
        json_matches = _JSON_BLOCK_RE.findall(text)
        parsed_json: dict | None = None
        for raw_json in json_matches:
            try:
                candidate = json.loads(raw_json)
                if isinstance(candidate, dict):
                    parsed_json = candidate
            except (json.JSONDecodeError, ValueError):
                continue

        if parsed_json is None:
            return False, f"missing_json_block (expected fields: {', '.join(required_fields)})"

        # Check 3: Required fields empty in structured_output
        so = parsed_json.get("structured_output", parsed_json)
        if isinstance(so, dict):
            missing = []
            for f in required_fields:
                # Check the primary field name and any known aliases
                val = so.get(f)
                if not val:
                    aliases = _FIELD_ALIASES.get(f, [])
                    val = next((so.get(a) for a in aliases if so.get(a)), None)
                if not val:
                    missing.append(f)
            if missing:
                return False, f"empty_required_fields: {', '.join(missing)}"

        # Check 4: Text degeneration
        garble_hits = _GARBLE_PATTERN.findall(text)
        if len(garble_hits) >= 5:
            return False, "text_degeneration"

        placeholder_hits = _PLACEHOLDER_PATTERN.findall(text)
        if len(placeholder_hits) >= 3:
            return False, "placeholder_patterns"

        return True, ""
