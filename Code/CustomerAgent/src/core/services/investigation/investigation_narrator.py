"""Investigation narrator — LLM-powered first-person narration of investigation flow.

Runs OUTSIDE the GroupChat as a post-processing step after each agent turn.
Reads the agent's output + investigation state and produces human-readable
narration that is:
  - Streamed token-by-token as ``investigation_narrator_chunk`` SSE events
  - Finalized with an ``investigation_narrator_done`` SSE event
  - Logged to Application Insights via AgentLogger

Controlled by ``narrator_enabled`` flag in ``investigation_workflow`` config.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, AsyncGenerator

from agent_framework import Agent

from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)

_NARRATOR_AGENT_NAME = "narrator"

# ── Configurable limits (override via set_narrator_limits) ────────
_AGENT_OUTPUT_MAX_CHARS = 4000
_STATE_MAX_HYPOTHESES = 5
_STATE_MAX_SYMPTOMS = 5
_STATE_MAX_SIGNALS = 5
_LOG_INPUT_MAX_CHARS = 500
_STREAM_TIMEOUT_SECONDS = 60


def set_narrator_limits(
    *,
    agent_output_max_chars: int | None = None,
    state_max_hypotheses: int | None = None,
    state_max_symptoms: int | None = None,
    state_max_signals: int | None = None,
    log_input_max_chars: int | None = None,
    stream_timeout_seconds: int | None = None,
) -> None:
    """Override narrator truncation / streaming limits at startup."""
    global _AGENT_OUTPUT_MAX_CHARS, _STATE_MAX_HYPOTHESES, _STATE_MAX_SYMPTOMS
    global _STATE_MAX_SIGNALS, _LOG_INPUT_MAX_CHARS, _STREAM_TIMEOUT_SECONDS
    if agent_output_max_chars is not None:
        _AGENT_OUTPUT_MAX_CHARS = agent_output_max_chars
    if state_max_hypotheses is not None:
        _STATE_MAX_HYPOTHESES = state_max_hypotheses
    if state_max_symptoms is not None:
        _STATE_MAX_SYMPTOMS = state_max_symptoms
    if state_max_signals is not None:
        _STATE_MAX_SIGNALS = state_max_signals
    if log_input_max_chars is not None:
        _LOG_INPUT_MAX_CHARS = log_input_max_chars
    if stream_timeout_seconds is not None:
        _STREAM_TIMEOUT_SECONDS = stream_timeout_seconds


def _truncate(text: str, limit: int) -> str:
    """Truncate *text* to *limit* chars, appending a marker if trimmed."""
    if len(text) <= limit:
        return text
    remaining = len(text) - limit
    return text[:limit] + f"\n[truncated: {remaining} chars remaining]"


async def narrate_agent_turn(
    narrator_agent: Agent,
    agent_name: str,
    agent_output: str,
    phase: str,
    investigation: Any,
    xcv: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream narration chunks for an agent's completed turn.

    Yields:
        ``investigation_narrator_chunk`` dicts with partial ``text``,
        followed by one ``investigation_narrator_done`` dict.
    """
    xcv = xcv or get_current_xcv() or ""
    tracker = AgentLogger.get_instance()

    state_summary = _build_state_summary(investigation)

    narrator_input = (
        f"AGENT: {agent_name}\n"
        f"PHASE: {phase}\n\n"
        f"AGENT_OUTPUT:\n{_truncate(agent_output, _AGENT_OUTPUT_MAX_CHARS)}\n\n"
        f"INVESTIGATION_STATE:\n{state_summary}"
    )

    tracker.log_agent_invoked(xcv, _NARRATOR_AGENT_NAME, _truncate(narrator_input, _LOG_INPUT_MAX_CHARS))

    async for event in _stream_narrator(
        narrator_agent, narrator_input, agent_name, phase, investigation, xcv, tracker,
    ):
        yield event


async def narrate_stage(
    narrator_agent: Agent,
    stage_name: str,
    stage_output: str,
    phase: str,
    investigation: Any,
    signal_builder_result: Any | None = None,
    xcv: str | None = None,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream narration chunks for a non-GroupChat stage.

    Yields:
        ``investigation_narrator_chunk`` dicts with partial ``text``,
        followed by one ``investigation_narrator_done`` dict.
    """
    xcv = xcv or get_current_xcv() or ""
    tracker = AgentLogger.get_instance()

    if signal_builder_result and stage_name == "signal_builder":
        state_summary = _build_signal_builder_summary(signal_builder_result)
    else:
        state_summary = _build_state_summary(investigation)

    narrator_input = (
        f"AGENT: {stage_name}\n"
        f"PHASE: {phase}\n\n"
        f"AGENT_OUTPUT:\n{_truncate(stage_output, _AGENT_OUTPUT_MAX_CHARS)}\n\n"
        f"INVESTIGATION_STATE:\n{state_summary}"
    )

    tracker.log_agent_invoked(xcv, _NARRATOR_AGENT_NAME, _truncate(narrator_input, _LOG_INPUT_MAX_CHARS))

    async for event in _stream_narrator(
        narrator_agent, narrator_input, stage_name, phase, investigation, xcv, tracker,
    ):
        yield event


async def _stream_narrator(
    narrator_agent: Agent,
    narrator_input: str,
    agent_name: str,
    phase: str,
    investigation: Any,
    xcv: str,
    tracker: AgentLogger,
) -> AsyncGenerator[dict[str, Any], None]:
    """Core streaming loop shared by narrate_agent_turn and narrate_stage."""
    start_time = time.monotonic()
    full_text_parts: list[str] = []
    empty_chunk_count = 0

    try:
        stream = narrator_agent.run(narrator_input, stream=True)
        async for chunk in _timeout_wrapper(stream, _STREAM_TIMEOUT_SECONDS):
            chunk_text = getattr(chunk, "text", None) or ""
            if not chunk_text:
                empty_chunk_count += 1
                continue
            full_text_parts.append(chunk_text)
            yield {
                "type": "investigation_narrator_chunk",
                "investigation_id": investigation.id,
                "narrated_agent": agent_name,
                "phase": phase,
                "text": chunk_text,
            }

        duration_ms = (time.monotonic() - start_time) * 1000
        full_text = "".join(full_text_parts).strip()

        if empty_chunk_count > 0:
            logger.debug(
                "Narrator stream: %d empty chunks skipped for agent=%s",
                empty_chunk_count, agent_name,
            )

        if full_text:
            tracker.log_agent_response(
                xcv=xcv,
                agent_name=_NARRATOR_AGENT_NAME,
                output_text=full_text,
                duration_ms=duration_ms,
            )
            logger.info(
                "Narrator streamed narration for agent=%s phase=%s (%d chars, %.0fms)",
                agent_name, phase, len(full_text), duration_ms,
            )
        else:
            logger.warning("Narrator returned empty response for agent=%s", agent_name)

        yield {
            "type": "investigation_narrator_done",
            "investigation_id": investigation.id,
            "narrated_agent": agent_name,
            "phase": phase,
            "text": full_text,
            "duration_ms": round(duration_ms, 1),
            "empty_chunks": empty_chunk_count,
        }

    except asyncio.TimeoutError:
        duration_ms = (time.monotonic() - start_time) * 1000
        partial_text = "".join(full_text_parts).strip()
        logger.warning(
            "Narrator timed out after %.0fs for agent=%s (partial: %d chars)",
            _STREAM_TIMEOUT_SECONDS, agent_name, len(partial_text),
        )
        tracker.log_investigation_error(
            xcv=xcv,
            investigation_id=investigation.id,
            error=f"Narrator timeout ({agent_name}) after {_STREAM_TIMEOUT_SECONDS}s",
            phase=phase,
        )
        # Yield partial result so the client still gets something
        if partial_text:
            yield {
                "type": "investigation_narrator_done",
                "investigation_id": investigation.id,
                "narrated_agent": agent_name,
                "phase": phase,
                "text": partial_text,
                "duration_ms": round(duration_ms, 1),
                "empty_chunks": empty_chunk_count,
                "timed_out": True,
            }

    except (ConnectionError, OSError) as exc:
        duration_ms = (time.monotonic() - start_time) * 1000
        logger.warning(
            "Narrator network error for agent=%s: %s (%.0fms) — retryable",
            agent_name, exc, duration_ms,
        )
        tracker.log_investigation_error(
            xcv=xcv,
            investigation_id=investigation.id,
            error=f"Narrator network error ({agent_name}): {exc}",
            phase=phase,
        )

    except Exception as exc:
        duration_ms = (time.monotonic() - start_time) * 1000
        logger.warning(
            "Narrator content error for agent=%s: %s (%.0fms)",
            agent_name, exc, duration_ms,
            exc_info=True,
        )
        tracker.log_investigation_error(
            xcv=xcv,
            investigation_id=investigation.id,
            error=f"Narrator error ({agent_name}): {exc}",
            phase=phase,
        )


async def _timeout_wrapper(
    stream: Any,
    timeout_seconds: int,
) -> AsyncGenerator[Any, None]:
    """Wrap an async iterable with a per-iteration timeout."""
    aiter = stream.__aiter__()
    while True:
        try:
            chunk = await asyncio.wait_for(aiter.__anext__(), timeout=timeout_seconds)
            yield chunk
        except StopAsyncIteration:
            break


def _build_state_summary(investigation: Any) -> str:
    """Build a compact text summary of the investigation state for the narrator."""
    parts = []

    parts.append(f"Investigation ID: {investigation.id}")
    parts.append(f"Phase: {investigation.phase.value}")

    # Customer context
    ctx = getattr(investigation, "context", None)
    if ctx:
        parts.append(f"Customer: {getattr(ctx, 'customer_name', '?')}")
        parts.append(f"Service: {getattr(ctx, 'service_tree_id', '?')}")

    # Symptoms
    if investigation.symptoms:
        confirmed = [s for s in investigation.symptoms if getattr(s, "confirmed", False)]
        parts.append(f"Symptoms: {len(investigation.symptoms)} total, {len(confirmed)} confirmed")
        shown = confirmed[:_STATE_MAX_SYMPTOMS]
        for s in shown:
            sid = getattr(s, "symptom_id", getattr(s, "id", "?"))
            parts.append(f"  - {sid}")
        remaining = len(confirmed) - len(shown)
        if remaining > 0:
            parts.append(f"  [truncated: {remaining} more confirmed symptoms]")
    else:
        parts.append("Symptoms: none yet")

    # Hypotheses
    if investigation.hypotheses:
        parts.append(f"Hypotheses: {len(investigation.hypotheses)}")
        shown = investigation.hypotheses[:_STATE_MAX_HYPOTHESES]
        for h in shown:
            status = getattr(h, "status", "?")
            status_val = status.value if hasattr(status, "value") else str(status)
            parts.append(
                f"  - {h.id} (score={getattr(h, 'match_score', '?')}, "
                f"status={status_val}): {getattr(h, 'statement', '')[:80]}"
            )
        remaining = len(investigation.hypotheses) - len(shown)
        if remaining > 0:
            parts.append(f"  [truncated: {remaining} more hypotheses]")
    else:
        parts.append("Hypotheses: none yet")

    # Evidence
    if investigation.evidence:
        parts.append(f"Evidence items: {len(investigation.evidence)}")
    else:
        parts.append("Evidence: none collected yet")

    # Active hypothesis
    from .investigation_state import HypothesisStatus
    active = next(
        (h for h in investigation.hypotheses if h.status == HypothesisStatus.ACTIVE),
        None,
    )
    if active:
        parts.append(f"Active hypothesis: {active.id} — {getattr(active, 'statement', '')[:100]}")

    return "\n".join(parts)


def _build_signal_builder_summary(result: Any) -> str:
    """Build a compact text summary of the signal builder output for the narrator."""
    parts = []

    customer = getattr(result, "customer_name", "?")
    service = getattr(result, "service_tree_id", "?")
    parts.append(f"Customer: {customer}")
    parts.append(f"Service: {service}")

    activated = getattr(result, "all_activated_signals", [])
    compounds = getattr(result, "activated_compounds", [])
    parts.append(f"Activated signals: {len(activated)}")
    parts.append(f"Compound patterns: {len(compounds)}")

    # Signal types activated
    sig_types = set()
    for sig in activated:
        sig_types.add(getattr(sig, "signal_type_id", "?"))
    if sig_types:
        parts.append(f"Signal types: {', '.join(sorted(sig_types))}")

    # Top signals by strength
    sorted_sigs = sorted(activated, key=lambda s: getattr(s, "strength", 0), reverse=True)
    shown = sorted_sigs[:_STATE_MAX_SIGNALS]
    for sig in shown:
        name = getattr(sig, "signal_name", getattr(sig, "signal_type_id", "?"))
        strength = getattr(sig, "strength", 0)
        summary = getattr(sig, "activation_summary", "")[:100]
        parts.append(f"  - {name} (strength={strength:.3f}): {summary}")
    remaining = len(sorted_sigs) - len(shown)
    if remaining > 0:
        parts.append(f"  [truncated: {remaining} more signals]")

    return "\n".join(parts)
