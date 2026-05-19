"""Speaker selector for the investigation GroupChat.

Three-stage model routing:
  Stage 1 & 2 are pre-computed OUTSIDE the GroupChat:
    triage (standalone agent run) + hypothesis scoring (programmatic).
  Stage 3 is the GroupChat:
    evidence for top hypothesis → reasoning →
      if confirmed: acting → next hypothesis or complete
      if refuted: advance to next hypothesis → evidence (reuse + delta)

Cycle support:
  - needs_more_evidence: backtrack to evidence_planner (max N total dispatches per hypothesis)
  - hypothesis_refuted: advance to next ranked hypothesis via orchestrator

Compatible with GroupChatBuilder's selection_func API:
  selection_func(GroupChatState) → str (next participant name)
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, TYPE_CHECKING

from .investigation_output_parser import ParsedAgentOutput, parse_agent_output
from helper.agent_logger import AgentLogger, get_current_xcv

if TYPE_CHECKING:
    from agent_framework import Agent
    from .investigation_state import Investigation

from .investigation_state import HypothesisStatus

logger = logging.getLogger(__name__)

_MAX_EVIDENCE_CYCLES = 2
_MAX_HYPOTHESIS_CYCLES = 10  # safety limit for total hypothesis evaluation rounds
_MAX_GARBLED_RETRIES = 1  # max re-routes per agent for garbled output


def _get_last_message_text(state: Any) -> str:
    """Get the text of the last message in conversation."""
    conversation = state.conversation
    if not conversation:
        return ""
    last = conversation[-1]
    if hasattr(last, "text") and last.text:
        return last.text
    if hasattr(last, "content"):
        contents = last.content if isinstance(last.content, list) else [last.content]
        parts = []
        for c in contents:
            if hasattr(c, "text"):
                parts.append(c.text)
            elif isinstance(c, str):
                parts.append(c)
        return " ".join(parts)
    return str(last)


def _get_last_speaker(state: Any) -> str | None:
    """Get the author/name of the last message."""
    conversation = state.conversation
    if not conversation:
        return None
    last = conversation[-1]
    return getattr(last, "author_name", None) or getattr(last, "name", None)


def _format_hypothesis_summary(investigation: "Investigation", evidence_planner_name: str = "evidence_planner") -> str:
    """Build a compact hypothesis reference for the orchestrator's system prompt.

    The summary is deliberately terse — it provides just enough data for
    routing decisions.  The orchestrator MUST NOT restate this content in
    its response; it should only reference hypothesis IDs.
    """
    hyps = investigation.hypotheses
    if not hyps:
        return ""
    lines = [
        "═══ STAGE 2 COMPLETE ═══",
        "DO NOT restate the data below. Do NOT list ER-IDs or evidence deltas.",
        f"Respond with 1 sentence: pick hypothesis #1, route to {evidence_planner_name}.",
        "",
    ]
    for i, h in enumerate(hyps, 1):
        delta = getattr(h, "evidence_delta", h.evidence_needed) or []
        lines.append(
            f"#{i} {h.id} score={h.match_score:.2f}"
            f" evidence_delta=[{', '.join(delta)}]"
        )
    lines.append("")
    lines.append(
        f'ACTION: select #1, route to {evidence_planner_name}. '
        f'Set signals {{ "next_agent": "{evidence_planner_name}" }}. '
        'Do NOT set phase_complete. Do NOT mention ER-IDs in your response.'
    )
    return "\n".join(lines)


def _inject_hypothesis_queue_update(
    orchestrator_agent: "Agent",
    investigation: "Investigation",
    next_hyp: "Hypothesis | None",
    remaining: list["Hypothesis"],
) -> None:
    """Append a hypothesis queue update to the orchestrator's system prompt.

    This ensures the LLM knows exactly which hypotheses are still pending
    after an evaluation cycle, rather than relying on it to track the queue
    from conversation history (which it does poorly).
    """
    evaluated = [
        h for h in investigation.hypotheses
        if h.status != HypothesisStatus.ACTIVE
    ]
    collected = sorted(investigation.collected_er_ids) if investigation else []
    lines = [
        "",
        f"═══ HYPOTHESIS QUEUE UPDATE (evaluated {len(evaluated)}/{len(investigation.hypotheses)}) ═══",
        f"Evaluated hypotheses: {', '.join(f'{h.id}={h.status.value}' for h in evaluated)}",
        f"Already collected ER-IDs (reusable): {', '.join(collected) or 'none'}",
        f"Remaining ACTIVE hypotheses ({len(remaining)}):",
    ]
    for i, h in enumerate(sorted(remaining, key=lambda x: -x.match_score), 1):
        delta = getattr(h, "evidence_delta", h.evidence_needed) or []
        lines.append(
            f"  #{i} {h.id} score={h.match_score:.2f}"
            f" evidence_delta=[{', '.join(delta)}]"
        )
    if next_hyp:
        lines.append(f"ACTION: Evaluate {next_hyp.id} next. Route to evidence_planner.")
    else:
        lines.append("ACTION: All hypotheses evaluated. Route to action_planner.")
    lines.append("")

    try:
        import re as _re
        current_instructions = orchestrator_agent.default_options.get("instructions", "") or ""
        # Remove any previous hypothesis queue update to avoid accumulation
        current_instructions = _re.sub(
            r"\n═══ HYPOTHESIS QUEUE UPDATE.*?(?=\n═══|$)",
            "",
            current_instructions,
            flags=_re.DOTALL,
        )
        orchestrator_agent.default_options["instructions"] = (
            current_instructions + "\n".join(lines)
        )
        logger.info(
            "Injected hypothesis queue update: remaining=%d, next=%s, collected_ers=%s",
            len(remaining), next_hyp.id if next_hyp else "NONE", collected,
        )
    except Exception as exc:
        logger.warning("Failed to inject hypothesis queue update: %s", exc)


def _get_evidence_file_paths(collected_er_ids: list[str]) -> list[str]:
    """Derive sandbox file paths for already-collected ERs from fetch_tools_config.

    Maps ER-IDs → fetch tool → output files using the er_patterns in config.
    Returns ADLS paths (under ADLS_BASE_PATH) with the real xcv value substituted.
    """
    import fnmatch
    import json as _json
    import os as _os
    from pathlib import Path

    def _adls_base() -> str:
        return _os.getenv("ADLS_BASE_PATH", "runs").strip("/")

    xcv = get_current_xcv() or "unknown"

    config_path = Path(__file__).resolve().parents[3] / "config" / "fetch_tools_config.json"
    try:
        with open(config_path, "r") as f:
            config = _json.load(f)
    except (FileNotFoundError, _json.JSONDecodeError) as exc:
        logger.warning("Could not load fetch_tools_config.json for file path derivation: %s", exc)
        return []

    evidence_subdir = config.get("subdirs", {}).get("evidence", "evidence")
    paths: list[str] = []

    for _tool_name, tool_config in config.get("fetch_tools", {}).items():
        er_patterns = tool_config.get("er_patterns", [])
        # Check if any collected ER matches this tool's patterns
        matched = any(
            fnmatch.fnmatch(er_id, pat)
            for er_id in collected_er_ids
            for pat in er_patterns
        )
        if matched:
            for call_spec in tool_config.get("mcp_calls", []):
                output_file = call_spec.get("output_file", "")
                if output_file:
                    paths.append(f"{_adls_base()}/{xcv}/{evidence_subdir}/{output_file}")

    return sorted(set(paths))


def _inject_evidence_context(
    evidence_planner_agent: "Agent",
    investigation: "Investigation",
    hypothesis: "Hypothesis",
) -> None:
    """Inject evidence collection context into evidence_planner's instructions.

    This provides the evidence_planner with PROGRAMMATIC knowledge of what's
    already collected and what still needs collection (evidence_delta),
    eliminating reliance on the LLM reading conversation history.

    Also injects evidence_file_paths so evidence_planner can pass them to
    sandbox_coder for re-analysis when evidence is reused across hypotheses.
    """
    import re as _re

    collected = sorted(investigation.collected_er_ids)
    delta = hypothesis.evidence_delta or [
        er for er in hypothesis.evidence_needed if er not in investigation.collected_er_ids
    ]

    # Build evidence file paths for already-collected ERs from config
    evidence_file_paths = _get_evidence_file_paths(collected)

    lines = [
        "",
        f"═══ EVIDENCE CONTEXT FOR {hypothesis.id} ═══",
        f"Hypothesis: {hypothesis.statement}",
        f"evidence_needed: [{', '.join(hypothesis.evidence_needed)}]",
        f"already_collected (REUSE — do NOT re-collect): [{', '.join(collected) or 'none'}]",
        f"evidence_delta (COLLECT ONLY THESE): [{', '.join(delta) or 'none — all evidence available'}]",
        f"evidence_file_paths (files already in sandbox for reuse): {evidence_file_paths or 'none'}",
        f"start_time: {investigation.context.start_time}",
        f"end_time: {investigation.context.end_time}",
        "IMPORTANT: Use the EXACT start_time and end_time above in ALL collector task strings. Do NOT adjust, widen, narrow, or recalculate these timestamps.",
        f"owning_tenant_names: {investigation.context.owning_tenant_names}",
        f"support_product_names: {investigation.context.support_product_names}",
        "IMPORTANT: owning_tenant_names is NOT the customer name — it is the IcM tenant that owns incidents for this service.",
        "Pass owning_tenant_names to data_fetcher. Pass support_product_names to data_fetcher. Do NOT confuse these with customer_name.",
        "IMPORTANT: When evidence_delta is empty, skip data_fetcher but STILL call sandbox_coder with the current hypothesis question + evidence_file_paths listed above.",
    ]
    lines.append("═══════════════════════════════════════════════════════════")
    lines.append("")

    block = "\n".join(lines)

    try:
        current = evidence_planner_agent.default_options.get("instructions", "") or ""
        # Remove any previous evidence context block to avoid accumulation
        current = _re.sub(
            r"\n═══ EVIDENCE CONTEXT FOR .*?═══════════════════════════════════════════════════════════\n",
            "",
            current,
            flags=_re.DOTALL,
        )
        evidence_planner_agent.default_options["instructions"] = current + block
        logger.info(
            "Injected evidence context for %s: delta=%s, collected=%s, file_paths=%s",
            hypothesis.id, delta, collected, evidence_file_paths,
        )
    except Exception as exc:
        logger.warning("Failed to inject evidence context: %s", exc)


def _inject_evidence_exhausted(
    reasoner_agent: "Agent | None",
    investigation: "Investigation | None",
) -> None:
    """Inject 'evidence collection exhausted' notice into the reasoner's instructions.

    Called when the evidence_planner cycle limit is reached while the reasoner
    still signals ``needs_more_evidence``.  This forces the reasoner to stop
    requesting more evidence and make a final CONFIRMED / CONTRIBUTING / REFUTED
    determination with whatever evidence is available.
    """
    if reasoner_agent is None:
        return
    import re as _re

    hyp_id = (
        investigation._current_hypothesis_id
        if investigation else "current hypothesis"
    )

    block = (
        "\n\n═══ EVIDENCE COLLECTION EXHAUSTED ═══\n"
        f"The evidence collection cycle limit has been reached for {hyp_id}.\n"
        "All available evidence has already been collected — no further collection\n"
        "attempts will be made.\n\n"
        "YOU MUST make a FINAL determination NOW using only the evidence already\n"
        "provided. Choose one of:\n"
        "  • CONFIRMED  (confidence >= 0.7)\n"
        "  • CONTRIBUTING (confidence 0.4–0.69)\n"
        "  • REFUTED    (confidence < 0.4)\n\n"
        "Do NOT set needs_more_evidence=true. Do NOT request additional ER-IDs.\n"
        "Treat any missing evidence as inconclusive and proceed.\n"
        "═══════════════════════════════════════════════════════════\n"
    )

    try:
        current = reasoner_agent.default_options.get("instructions", "") or ""
        # Remove any previous exhausted block to avoid accumulation
        current = _re.sub(
            r"\n\n═══ EVIDENCE COLLECTION EXHAUSTED ═══.*?═══════════════════════════════════════════════════════════\n",
            "",
            current,
            flags=_re.DOTALL,
        )
        reasoner_agent.default_options["instructions"] = current + block
        logger.info(
            "Injected evidence-exhausted notice into reasoner for %s", hyp_id,
        )
    except Exception as exc:
        logger.warning("Failed to inject evidence-exhausted notice: %s", exc)


def _inject_sandbox_analysis(
    reasoner_agent: "Agent | None",
    investigation: "Investigation | None",
) -> None:
    """Inject sandbox_coder's raw analysis output into the reasoner's instructions.

    Called when routing from evidence_planner to reasoner.  Provides the
    reasoner with the full analytical output (computed metrics, data patterns,
    correlations) from sandbox_coder for the current hypothesis, so it can
    make better-informed determinations.
    """
    if reasoner_agent is None or investigation is None:
        return
    import re as _re

    hyp = next(
        (h for h in investigation.hypotheses
         if h.id == investigation._current_hypothesis_id),
        None,
    )
    if not hyp or not hyp.sandbox_coder_output:
        return

    block = (
        f"\n\n═══ RAW SANDBOX ANALYSIS FOR {hyp.id} ═══\n"
        "The following is the verbatim analytical output produced by sandbox_coder.\n"
        "Use this data as primary evidence for your reasoning — it contains computed\n"
        "metrics, data patterns, and correlations from the raw evidence files.\n\n"
        f"{hyp.sandbox_coder_output}\n"
        "═══ END RAW SANDBOX ANALYSIS ═══\n"
    )

    try:
        current = reasoner_agent.default_options.get("instructions", "") or ""
        # Remove any previous sandbox analysis block to avoid accumulation
        current = _re.sub(
            r"\n\n═══ RAW SANDBOX ANALYSIS FOR .*?═══ END RAW SANDBOX ANALYSIS ═══\n",
            "",
            current,
            flags=_re.DOTALL,
        )
        reasoner_agent.default_options["instructions"] = current + block
        logger.info(
            "Injected sandbox_coder analysis (%d chars) into reasoner for %s",
            len(hyp.sandbox_coder_output), hyp.id,
        )
    except Exception as exc:
        logger.warning("Failed to inject sandbox analysis into reasoner: %s", exc)


def _inject_output_correction(
    agent: "Agent | None",
    agent_name: str,
    garbled_reason: str,
) -> None:
    """Inject a corrective notice into an agent's instructions after garbled output.

    Tells the agent that its previous response was malformed and it must
    re-emit a valid response with the required JSON block.
    """
    if agent is None:
        return
    import re as _re

    block = (
        "\n\n═══ OUTPUT FORMAT CORRECTION REQUIRED ═══\n"
        f"Your previous response was MALFORMED and could not be processed.\n"
        f"Reason: {garbled_reason}\n\n"
        "You MUST re-emit your complete response with a valid ```json block\n"
        "at the end containing both 'structured_output' and 'signals' keys.\n"
        "Do NOT reference your previous attempt — produce a fresh, complete response.\n"
        "═════════════════════════════════════════════════════════════\n"
    )

    try:
        current = agent.default_options.get("instructions", "") or ""
        # Remove any previous correction block to avoid accumulation
        current = _re.sub(
            r"\n\n═══ OUTPUT FORMAT CORRECTION REQUIRED ═══.*?"
            r"═════════════════════════════════════════════════════════════\n",
            "",
            current,
            flags=_re.DOTALL,
        )
        agent.default_options["instructions"] = current + block
        logger.info(
            "Injected output-correction notice into %s (reason: %s)",
            agent_name, garbled_reason,
        )
    except Exception as exc:
        logger.warning("Failed to inject output-correction notice into %s: %s", agent_name, exc)


def create_investigation_speaker_selector(
    participant_names: list[str],
    orchestrator_name: str = "investigation_orchestrator",
    parsed_cache: dict[str, Any] | None = None,
    investigation: "Investigation | None" = None,
    orchestrator_agent: "Agent | None" = None,
    evidence_planner_agent: "Agent | None" = None,
    reasoner_agent: "Agent | None" = None,
    phase_transitions_cfg: dict[str, str] | None = None,
    cycle_detection_cfg: dict[str, int] | None = None,
    agent_roles: dict[str, str] | None = None,
):
    """Create the speaker selection function for the investigation GroupChat.

    Args:
        participant_names: List of valid participant names from config.
        orchestrator_name: Name of the orchestrator agent.
        parsed_cache: Optional shared dict for caching parsed agent output.
            The speaker selector populates this after parsing each turn;
            the runner reads it in _finalize_agent_response to avoid
            double-parsing.  Keys: "agent" (str), "parsed" (ParsedAgentOutput).
        investigation: The Investigation state object.  When provided, the
            selector uses it for hypothesis cycling and premature-resolution
            guards.
        orchestrator_agent: The Agent instance for the orchestrator.  When
            provided together with ``investigation``, the selector can inject
            hypothesis queue updates into the orchestrator's instructions
            during hypothesis cycling.
        evidence_planner_agent: The Agent instance for the evidence planner.
            When provided together with ``investigation``, the selector injects
            evidence context (evidence_delta, collected_er_ids) into the
            evidence planner's instructions before each routing.
        reasoner_agent: The Agent instance for the reasoner.  When provided,
            the selector can inject "evidence exhausted" instructions when
            the evidence collection cycle limit is reached, forcing the
            reasoner to make a final determination.
        phase_transitions_cfg: Phase-to-agent mapping from
            ``agents_config.json → investigation_workflow → phase_transitions``.
            Required — raises ValueError if not provided.
        cycle_detection_cfg: Oscillation / stuck-loop detection settings from
            ``agents_config.json → investigation_workflow → cycle_detection``.
            Keys: ``history_window``, ``max_repeated_pattern``,
            ``max_identical_messages``.  Disabled when None.
        agent_roles: Role-to-name mapping from
            ``agents_config.json → investigation_workflow → agent_roles``.
            Required — raises ValueError if not provided.
            Keys: ``orchestrator``, ``evidence_planner``, ``reasoner``.

    Returns:
        A function: GroupChatState → str (next participant name).
    """
    if not phase_transitions_cfg:
        raise ValueError(
            "phase_transitions_cfg is required — add 'phase_transitions' to "
            "investigation_workflow in agents_config.json"
        )
    if not agent_roles:
        raise ValueError(
            "agent_roles is required — add 'agent_roles' to "
            "investigation_workflow in agents_config.json"
        )

    # Derive canonical names from role registry
    _ep_name = agent_roles["evidence_planner"]
    _reasoner_name = agent_roles["reasoner"]

    valid_names = set(participant_names)
    evidence_cycle_count = 0
    hypothesis_cycle_count = 0  # track how many hypotheses we've evaluated
    _ep_dispatched_for_hyp = False  # has evidence_planner been dispatched for current hypothesis?

    # Resolve phase transitions against actual participant names.
    # Config values referencing "orchestrator" are resolved to the actual orchestrator name.
    # Agents not in participants fall back to orchestrator.
    # NOTE: triage is handled OUTSIDE the GroupChat — no transition needed.
    # NOTE: action_planner runs STANDALONE after GroupChat — no transition needed.
    phase_transitions: dict[str, str] = {}
    for _phase, _target in phase_transitions_cfg.items():
        resolved_target = orchestrator_name if _target == "orchestrator" else _target
        if resolved_target in valid_names:
            phase_transitions[_phase] = resolved_target
        else:
            logger.warning(
                "Phase transition target '%s' (phase=%s) not in participants %s, "
                "falling back to orchestrator '%s'",
                _target, _phase, list(valid_names), orchestrator_name,
            )
            phase_transitions[_phase] = orchestrator_name

    # ── Cycle detection state ──────────────────────────────────────
    _cd_enabled = bool(cycle_detection_cfg)
    _cd_history_window = (cycle_detection_cfg or {}).get("history_window", 6)
    _cd_max_pattern = (cycle_detection_cfg or {}).get("max_repeated_pattern", 3)
    _cd_max_identical = (cycle_detection_cfg or {}).get("max_identical_messages", 2)
    _speaker_history: list[str] = []
    _message_hashes: list[tuple[str, str]] = []   # (speaker, content_hash)
    _oscillation_warned = False  # first detection → context injection; second → force resolve

    # ── Garbled output retry state ─────────────────────────────────
    # Tracks how many times each agent has been re-routed for garbled output.
    # When an agent produces garbled output and hasn't exhausted retries,
    # we inject a correction notice and re-route to the same agent.
    _garbled_retry_counts: dict[str, int] = {}  # agent_name → retry count

    # Map agent names to their Agent instances for injection
    _agent_instances: dict[str, "Agent | None"] = {
        _ep_name: evidence_planner_agent,
        _reasoner_name: reasoner_agent,
        orchestrator_name: orchestrator_agent,
    }

    def _content_hash(text: str) -> str:
        """Hash first 500 chars of message for dedup detection."""
        return hashlib.md5(text[:500].encode()).hexdigest()

    def _detect_oscillation() -> tuple[bool, str]:
        """Check if last entries form a repeating 2-agent pair.

        Returns (detected, pattern_str).
        """
        need = _cd_max_pattern * 2  # e.g. 3 repeats of AB = 6 entries
        if len(_speaker_history) < need:
            return False, ""
        recent = _speaker_history[-need:]
        a, b = recent[0], recent[1]
        if a == b:
            return False, ""
        expected = [a, b] * _cd_max_pattern
        if recent == expected:
            return True, f"{a},{b}"
        return False, ""

    def _detect_identical_messages() -> tuple[bool, str]:
        """Check if the same agent produced identical content N times in a row."""
        if len(_message_hashes) < _cd_max_identical:
            return False, ""
        tail = _message_hashes[-_cd_max_identical:]
        first = tail[0]
        if all(entry == first for entry in tail):
            return True, first[0]
        return False, ""

    def _resolve(candidate: str) -> str | None:
        """Resolve a candidate name, return it if valid."""
        if candidate in valid_names:
            return candidate
        return None

    def _log_selection(last: str, next_: str, reason: str) -> None:
        xcv = get_current_xcv()
        if xcv:
            AgentLogger.get_instance().log_speaker_selected(
                xcv=xcv,
                last_speaker=last,
                next_speaker=next_,
                reason=reason,
                evidence_cycle=evidence_cycle_count,
                hypothesis_cycle=hypothesis_cycle_count,
            )

    def select_next_speaker(state: Any) -> str:
        nonlocal evidence_cycle_count, hypothesis_cycle_count, _ep_dispatched_for_hyp
        nonlocal _oscillation_warned

        # ── Cycle detection: track history & check for stuck loops ────
        if _cd_enabled:
            last_text_cd = _get_last_message_text(state)
            last_speaker_cd = _get_last_speaker(state)
            if last_speaker_cd:
                _speaker_history.append(last_speaker_cd)
                if len(_speaker_history) > _cd_history_window:
                    _speaker_history[:] = _speaker_history[-_cd_history_window:]
                _message_hashes.append((last_speaker_cd, _content_hash(last_text_cd)))
                if len(_message_hashes) > _cd_history_window:
                    _message_hashes[:] = _message_hashes[-_cd_history_window:]

            osc_detected, osc_pattern = _detect_oscillation()
            ident_detected, ident_agent = _detect_identical_messages()

            if osc_detected or ident_detected:
                _inv_id = investigation.id if investigation else ""
                _inv_phase = investigation.phase.value if investigation else ""
                xcv = get_current_xcv()

                if osc_detected and not _oscillation_warned:
                    # First detection → inject warning into orchestrator
                    _oscillation_warned = True
                    logger.warning(
                        "Oscillation detected: pattern [%s] repeated %d times — injecting context",
                        osc_pattern, _cd_max_pattern,
                    )
                    if orchestrator_agent is not None:
                        import re as _re_osc
                        # Remove any previous oscillation/stall warning to avoid accumulation
                        _osc_current = orchestrator_agent.default_options.get("instructions", "") or ""
                        _osc_current = _re_osc.sub(
                            r"\n\nWARNING: (Routing loop detected|Agent ').*?investigation_resolved\.",
                            "",
                            _osc_current,
                            flags=_re_osc.DOTALL,
                        )
                        orchestrator_agent.default_options["instructions"] = _osc_current + (
                            f"\n\nWARNING: Routing loop detected between "
                            f"{osc_pattern.replace(',', ' and ')} for {_cd_max_pattern} "
                            f"rounds with no phase change. You MUST advance to a "
                            f"different phase or signal investigation_resolved."
                        )
                    if xcv:
                        AgentLogger.get_instance().log_oscillation_detected(
                            xcv=xcv, pattern=osc_pattern,
                            repeat_count=_cd_max_pattern,
                            intervention="context_injection",
                            investigation_id=_inv_id, phase=_inv_phase,
                        )
                    _speaker_history.clear()
                    participant_keys = list(state.participants.keys())
                    default = orchestrator_name if orchestrator_name in participant_keys else participant_keys[0]
                    _log_selection(last_speaker_cd or "", default, f"oscillation_intervention_warn={osc_pattern}")
                    return default

                elif osc_detected and _oscillation_warned:
                    # Second detection → force resolve
                    logger.warning(
                        "Oscillation persists after intervention: pattern [%s] — forcing resolution",
                        osc_pattern,
                    )
                    if investigation is not None:
                        from .investigation_state import InvestigationPhase
                        investigation.transition_to(
                            InvestigationPhase.COMPLETE,
                            source="speaker_selector:oscillation_force_resolve",
                            force=True,
                        )
                    if xcv:
                        AgentLogger.get_instance().log_oscillation_detected(
                            xcv=xcv, pattern=osc_pattern,
                            repeat_count=_cd_max_pattern,
                            intervention="force_resolve",
                            investigation_id=_inv_id, phase=_inv_phase,
                        )
                    _speaker_history.clear()
                    participant_keys = list(state.participants.keys())
                    default = orchestrator_name if orchestrator_name in participant_keys else participant_keys[0]
                    _log_selection(last_speaker_cd or "", default, f"oscillation_force_resolve={osc_pattern}")
                    return default

                elif ident_detected:
                    # Identical messages → same escalation path as oscillation
                    intervention = "context_injection" if not _oscillation_warned else "force_resolve"
                    logger.warning(
                        "Identical message detected from '%s' (%d times) — intervention=%s",
                        ident_agent, _cd_max_identical, intervention,
                    )
                    if xcv:
                        AgentLogger.get_instance().log_oscillation_detected(
                            xcv=xcv, pattern=f"identical:{ident_agent}",
                            repeat_count=_cd_max_identical,
                            intervention=intervention,
                            investigation_id=_inv_id, phase=_inv_phase,
                        )
                    if not _oscillation_warned:
                        _oscillation_warned = True
                        if orchestrator_agent is not None:
                            import re as _re_ident
                            # Remove any previous oscillation/stall warning to avoid accumulation
                            _ident_current = orchestrator_agent.default_options.get("instructions", "") or ""
                            _ident_current = _re_ident.sub(
                                r"\n\nWARNING: (Routing loop detected|Agent ').*?investigation_resolved\.",
                                "",
                                _ident_current,
                                flags=_re_ident.DOTALL,
                            )
                            orchestrator_agent.default_options["instructions"] = _ident_current + (
                                f"\n\nWARNING: Agent '{ident_agent}' has produced "
                                f"identical output {_cd_max_identical} times in a row. "
                                f"The investigation is stalled. You MUST advance to a "
                                f"different phase or signal investigation_resolved."
                            )
                        _message_hashes.clear()
                        participant_keys = list(state.participants.keys())
                        default = orchestrator_name if orchestrator_name in participant_keys else participant_keys[0]
                        _log_selection(last_speaker_cd or "", default, f"identical_msg_intervention={ident_agent}")
                        return default
                    else:
                        if investigation is not None:
                            from .investigation_state import InvestigationPhase
                            investigation.transition_to(
                                InvestigationPhase.COMPLETE,
                                source="speaker_selector:identical_msg_force_resolve",
                                force=True,
                            )
                        _message_hashes.clear()
                        participant_keys = list(state.participants.keys())
                        default = orchestrator_name if orchestrator_name in participant_keys else participant_keys[0]
                        _log_selection(last_speaker_cd or "", default, f"identical_msg_force_resolve={ident_agent}")
                        return default

        def _maybe_inject_evidence_context() -> None:
            """Inject evidence context into evidence_planner before routing to it."""
            if evidence_planner_agent is None or investigation is None:
                return
            hyp = next(
                (h for h in investigation.hypotheses
                 if h.id == investigation._current_hypothesis_id),
                None,
            )
            if hyp:
                _inject_evidence_context(evidence_planner_agent, investigation, hyp)

        def _maybe_inject_sandbox_analysis() -> None:
            """Inject sandbox_coder raw analysis into reasoner before routing to it."""
            _inject_sandbox_analysis(reasoner_agent, investigation)

        def _allow_evidence_planner(reason: str) -> bool:
            """Centralized gate: check cycle limit before routing to evidence_planner.

            ALL dispatches count (including the first).  ``_MAX_EVIDENCE_CYCLES``
            is the total number of allowed dispatches per hypothesis.  The counter
            is reset to 0 when a new hypothesis begins evaluation.

            Also blocks if evidence_delta is empty — no new data to collect means
            re-dispatching evidence_planner would only re-call the same tools.
            """
            nonlocal evidence_cycle_count, _ep_dispatched_for_hyp
            if evidence_cycle_count >= _MAX_EVIDENCE_CYCLES:
                logger.warning(
                    "Blocking evidence_planner routing (%s): "
                    "dispatch limit %d/%d reached for current hypothesis",
                    reason, evidence_cycle_count, _MAX_EVIDENCE_CYCLES,
                )
                return False

            # Block if all evidence is already collected (no new data to fetch)
            if investigation and evidence_cycle_count > 0:
                hyp = next(
                    (h for h in investigation.hypotheses
                     if h.id == investigation._current_hypothesis_id),
                    None,
                )
                if hyp:
                    delta = [
                        er for er in hyp.evidence_needed
                        if er not in investigation.collected_er_ids
                    ]
                    if not delta:
                        logger.info(
                            "Blocking evidence_planner re-dispatch (%s): "
                            "evidence_delta is empty — all ERs already collected",
                            reason,
                        )
                        return False

            evidence_cycle_count += 1
            _ep_dispatched_for_hyp = True
            logger.info(
                "Evidence dispatch %d/%d (%s)",
                evidence_cycle_count, _MAX_EVIDENCE_CYCLES, reason,
            )
            _maybe_inject_evidence_context()
            return True

        conversation = state.conversation
        participant_keys = list(state.participants.keys())
        default = orchestrator_name if orchestrator_name in participant_keys else participant_keys[0]

        # Safety limit
        if state.current_round >= 40:
            logger.warning("Max rounds (%d) reached, returning orchestrator.", state.current_round)
            return default

        # First turn → orchestrator
        if not conversation or len(conversation) <= 1:
            return default

        last_text = _get_last_message_text(state)
        last_speaker = _get_last_speaker(state)

        # Parse via output_parser — single extraction point
        parsed = parse_agent_output(last_text, agent_name=last_speaker or "")

        # Cache for runner to reuse (avoids double-parsing in _finalize_agent_response)
        if parsed_cache is not None:
            parsed_cache["agent"] = last_speaker or ""
            parsed_cache["parsed"] = parsed

        sig = parsed.signals

        # ── Garbled output re-route ───────────────────────────────
        # If a critical agent produced garbled/truncated output, re-route
        # back to the same agent with a corrective instruction injection.
        # This consumes a GroupChat turn but is cheaper than a wrong verdict.
        if parsed.is_garbled and last_speaker and last_speaker != orchestrator_name:
            retry_count = _garbled_retry_counts.get(last_speaker, 0)
            if retry_count < _MAX_GARBLED_RETRIES:
                _garbled_retry_counts[last_speaker] = retry_count + 1
                agent_instance = _agent_instances.get(last_speaker)
                _inject_output_correction(
                    agent_instance, last_speaker, parsed.garbled_reason,
                )
                target = _resolve(last_speaker)
                if target:
                    logger.warning(
                        "Re-routing to %s after garbled output (retry %d/%d, reason: %s)",
                        last_speaker, retry_count + 1, _MAX_GARBLED_RETRIES,
                        parsed.garbled_reason,
                    )
                    _log_selection(
                        last_speaker, target,
                        f"garbled_output_retry={retry_count + 1}_reason={parsed.garbled_reason}",
                    )
                    return target
            else:
                logger.warning(
                    "Garbled output from %s but retry limit exhausted (%d/%d), "
                    "falling through to normal routing",
                    last_speaker, retry_count, _MAX_GARBLED_RETRIES,
                )

        # Priority 0: If investigation is already in notifying/complete phase
        # and the orchestrator just spoke, force completion.  There is no
        # notification_agent participant — the orchestrator handles this.
        if investigation is not None:
            from .investigation_state import InvestigationPhase
            if investigation.phase in (InvestigationPhase.NOTIFYING, InvestigationPhase.COMPLETE):
                if last_speaker == default:
                    # Orchestrator already spoke in notifying phase — mark resolved
                    investigation.transition_to(
                        InvestigationPhase.COMPLETE,
                        source="speaker_selector:notifying_auto_complete",
                        force=True,
                    )
                    _log_selection(last_speaker or "", default, "notifying_phase_auto_complete")
                    return default

        # Priority 1: Investigation resolved → orchestrator to wrap up
        # BUT: Guard against premature resolution. Only honor this signal
        # when it comes from the orchestrator itself (meaning the full
        # pipeline has completed) or when all hypotheses have been evaluated
        # AND action_planner has run.
        if sig.investigation_resolved:
            _allow_resolve = True
            if investigation is not None:
                remaining_active = [
                    h for h in investigation.hypotheses
                    if h.status == HypothesisStatus.ACTIVE
                ]
                _terminal_statuses = {
                    HypothesisStatus.CONFIRMED,
                    HypothesisStatus.CONTRIBUTING,
                    HypothesisStatus.REFUTED,
                }
                has_terminal = any(
                    h.status in _terminal_statuses
                    for h in investigation.hypotheses
                )

                # ── Speculative update for stale state ──────────────
                # The speaker selector runs BEFORE apply_to_investigation,
                # so hypothesis statuses from the CURRENT message are not
                # yet reflected in the investigation state.  We account for:
                #   a) hypothesis_refuted signal → current hypothesis is
                #      about to be refuted by apply_to_investigation
                #   b) evaluations in structured_output → hypotheses are
                #      about to change status
                _pending_terminal_ids: set[str] = set()

                if sig.hypothesis_refuted and investigation._current_hypothesis_id:
                    _pending_terminal_ids.add(investigation._current_hypothesis_id)

                for _ev in parsed.evaluations:
                    _hid = _ev.get("hypothesis_id", "")
                    _st = (_ev.get("status", "")).upper()
                    if _hid and _st in ("CONFIRMED", "REFUTED", "CONTRIBUTING"):
                        _pending_terminal_ids.add(_hid)

                if _pending_terminal_ids:
                    remaining_active = [
                        h for h in remaining_active
                        if h.id not in _pending_terminal_ids
                    ]
                    has_terminal = True
                    logger.info(
                        "Speculative stale-state adjustment: %d hypothesis(es) "
                        "pending terminal status in current message (%s)",
                        len(_pending_terminal_ids),
                        ", ".join(sorted(_pending_terminal_ids)),
                    )

                # Guard F: require at least one hypothesis with a terminal
                # verdict before accepting resolution.  Without this, the
                # orchestrator could hallucinate investigation_resolved
                # before any hypothesis has been evaluated.
                if not has_terminal:
                    logger.warning(
                        "Blocking investigation_resolved from %s: "
                        "no hypothesis has a terminal status yet "
                        "(total=%d, all ACTIVE)",
                        last_speaker, len(investigation.hypotheses),
                    )
                    _allow_resolve = False
                # Block premature resolution from non-orchestrator agents
                elif last_speaker != default:
                    if remaining_active:
                        logger.warning(
                            "Blocking investigation_resolved from %s: "
                            "%d active hypotheses remain (evaluated=%d)",
                            last_speaker, len(remaining_active),
                            len(investigation.hypotheses) - len(remaining_active),
                        )
                        _allow_resolve = False
                else:
                    # Orchestrator itself wants to resolve — still block if
                    # ACTIVE hypotheses remain (LLM lost track of queue)
                    if remaining_active:
                        logger.warning(
                            "Blocking premature investigation_resolved from orchestrator: "
                            "%d active hypotheses remain — injecting queue reminder",
                            len(remaining_active),
                        )
                        _allow_resolve = False
                        # Re-inject remaining queue so orchestrator doesn't repeat the mistake
                        if orchestrator_agent is not None:
                            nxt = investigation.next_active_hypothesis()
                            _inject_hypothesis_queue_update(
                                orchestrator_agent, investigation, nxt, remaining_active
                            )

            if _allow_resolve:
                _log_selection(last_speaker or "", default, "investigation_resolved")
                return default
            # Fall through to normal routing if resolution was blocked

        # Priority 2: Explicit next_agent signal
        if sig.next_agent:
            resolved = _resolve(sig.next_agent)
            if resolved:
                if resolved == _ep_name:
                    if not _allow_evidence_planner(f"explicit_next_agent from {last_speaker}"):
                        # Cycle limit hit — route to reasoner with exhausted notice
                        reasoner_fallback = _resolve(_reasoner_name)
                        if reasoner_fallback:
                            _inject_evidence_exhausted(reasoner_agent, investigation)
                            _maybe_inject_sandbox_analysis()
                            _log_selection(
                                last_speaker or "", reasoner_fallback,
                                f"evidence_exhausted_explicit_redirect={evidence_cycle_count}",
                            )
                            return reasoner_fallback
                        _log_selection(
                            last_speaker or "", default,
                            f"evidence_cycle_limit_blocked_explicit={evidence_cycle_count}",
                        )
                        return default
                _log_selection(last_speaker or "", resolved, f"explicit_next_agent={sig.next_agent}")
                return resolved

        # Priority 3: Phase transition (with cycle + hypothesis-refute support)
        if sig.phase_complete:
            # Cycle support: reasoning + needs_more_evidence → back to evidence_planner
            if sig.phase_complete == "reasoning" and sig.needs_more_evidence:
                target = _resolve(_ep_name)
                if target and _allow_evidence_planner("reasoning_needs_more_evidence"):
                    _log_selection(last_speaker or "", target, f"evidence_cycle={evidence_cycle_count}")
                    return target
                # Cycle limit reached — tell reasoner to make a final determination
                # instead of falling through to orchestrator (which loops back).
                reasoner_target = _resolve(_reasoner_name)
                if reasoner_target:
                    _inject_evidence_exhausted(reasoner_agent, investigation)
                    _maybe_inject_sandbox_analysis()
                    _log_selection(
                        last_speaker or "", reasoner_target,
                        f"evidence_exhausted_force_determination_cycle={evidence_cycle_count}",
                    )
                    return reasoner_target
                # No reasoner in participants — fall through

            # Hypothesis refuted → advance to next hypothesis
            if sig.phase_complete == "reasoning" and getattr(sig, "hypothesis_refuted", False):
                hypothesis_cycle_count += 1
                evidence_cycle_count = 0  # reset evidence cycles for new hypothesis
                _ep_dispatched_for_hyp = False  # reset for new hypothesis

                if investigation is not None:
                    remaining = investigation.active_hypotheses()
                    nxt = investigation.next_active_hypothesis()
                    if nxt:
                        investigation._current_hypothesis_id = nxt.id

                    remaining = investigation.active_hypotheses()
                    logger.info(
                        "Hypothesis refuted, cycle #%d complete, next=%s, remaining=%d",
                        hypothesis_cycle_count,
                        nxt.id if nxt else "NONE",
                        len(remaining),
                    )

                    if remaining and nxt and hypothesis_cycle_count < _MAX_HYPOTHESIS_CYCLES:
                        # Route directly to evidence_planner for next hypothesis
                        target = _resolve(_ep_name)
                        if target:
                            if orchestrator_agent is not None:
                                _inject_hypothesis_queue_update(
                                    orchestrator_agent, investigation, nxt, remaining
                                )
                            _allow_evidence_planner(f"hypothesis_refuted_next={nxt.id}")
                            _log_selection(last_speaker or "", target, f"hypothesis_refuted_cycle={hypothesis_cycle_count}_next={nxt.id}")
                            return target

                    # No more active hypotheses — go to orchestrator for resolution
                    _log_selection(last_speaker or "", default, "all_hypotheses_exhausted_after_refute")
                    return default
                else:
                    logger.info(
                        "Hypothesis #%d refuted (no investigation ref), routing to orchestrator",
                        hypothesis_cycle_count,
                    )
                    _log_selection(last_speaker or "", default, f"hypothesis_refuted_cycle={hypothesis_cycle_count}")
                    return default

            # Hypothesis confirmed → advance to next hypothesis or finish GroupChat
            if sig.phase_complete == "reasoning" and not getattr(sig, "hypothesis_refuted", False) and not sig.needs_more_evidence:
                hypothesis_cycle_count += 1
                evidence_cycle_count = 0
                _ep_dispatched_for_hyp = False  # reset for new hypothesis

                if investigation is not None:
                    remaining = investigation.active_hypotheses()
                    nxt = investigation.next_active_hypothesis()
                    if nxt:
                        investigation._current_hypothesis_id = nxt.id

                    remaining = investigation.active_hypotheses()
                    logger.info(
                        "Hypothesis confirmed/evaluated, cycle #%d complete, next=%s, remaining=%d",
                        hypothesis_cycle_count,
                        nxt.id if nxt else "NONE",
                        len(remaining),
                    )

                    if remaining and nxt and hypothesis_cycle_count < _MAX_HYPOTHESIS_CYCLES:
                        target = _resolve(_ep_name)
                        if target:
                            if orchestrator_agent is not None:
                                _inject_hypothesis_queue_update(
                                    orchestrator_agent, investigation, nxt, remaining
                                )
                            _allow_evidence_planner(f"post_reasoning_next={nxt.id}")
                            _log_selection(last_speaker or "", target, f"post_reasoning_cycle={hypothesis_cycle_count}_next={nxt.id}")
                            return target

                    # No more active hypotheses — route to orchestrator for resolution
                    # (action_planner runs standalone AFTER GroupChat)
                    logger.info(
                        "All hypotheses evaluated after %d cycles, routing to orchestrator for resolution",
                        hypothesis_cycle_count,
                    )
                    _log_selection(last_speaker or "", default, "all_hypotheses_evaluated")
                    return default

            next_agent = phase_transitions.get(sig.phase_complete)
            if next_agent:
                resolved = _resolve(next_agent)
                if resolved:
                    if resolved == _ep_name:
                        if not _allow_evidence_planner(f"phase_transition_{sig.phase_complete}"):
                            _log_selection(
                                last_speaker or "", default,
                                f"evidence_cycle_limit_blocked_phase_transition={sig.phase_complete}",
                            )
                            return default
                    if resolved == _reasoner_name:
                        _maybe_inject_sandbox_analysis()
                    logger.info("Phase transition: %s → %s", sig.phase_complete, resolved)
                    _log_selection(last_speaker or "", resolved, f"phase_transition={sig.phase_complete}")
                    return resolved

        # Priority 4: Evidence collected → orchestrator decides
        if sig.evidence_collected:
            return default

        # Priority 5: After specialist → orchestrator
        if last_speaker and last_speaker != default:
            return default

        return default

    return select_next_speaker


