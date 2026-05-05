"""Output-parsing middleware for the investigation GroupChat.

Single extraction point: parses raw agent text into ParsedAgentOutput,
then apply_to_investigation mutates the Investigation state.

Every agent emits a ```json block with:
  {"structured_output": {...}, "signals": {"phase_complete", "next_agent", ...}}

If no valid JSON is found, falls back to legacy ---SIGNALS--- parsing.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)

# ── Regex patterns ────────────────────────────────────────────────

_JSON_BLOCK_RE = re.compile(r"```json\s*(.*?)```", re.DOTALL)
_SIGNALS_KV_RE = re.compile(r"^([A-Z_]+)\s*:\s*(.+)$")
_SYM_ID_RE = re.compile(r"\b(SYM-[A-Z]+-\d{3})\b")


# ── Parsed result dataclasses ────────────────────────────────────

@dataclass
class ParsedSignals:
    """Routing / lifecycle signals extracted from agent output."""
    phase_complete: str | None = None
    next_agent: str | None = None
    evidence_collected: list[str] = field(default_factory=list)
    investigation_resolved: bool = False
    needs_more_evidence: bool = False
    hypothesis_refuted: bool = False


@dataclass
class ParsedAgentOutput:
    """Fully parsed result of a single agent turn."""

    agent_name: str = ""
    raw_text: str = ""
    is_json_parsed: bool = False
    structured_output: dict[str, Any] = field(default_factory=dict)
    signals: ParsedSignals = field(default_factory=ParsedSignals)

    # Convenience accessors populated from structured_output
    symptoms: list[dict[str, Any]] = field(default_factory=list)
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    evaluations: list[dict[str, Any]] = field(default_factory=list)
    evidence_items: list[dict[str, Any]] = field(default_factory=list)
    preliminary_verdicts: list[dict[str, Any]] = field(default_factory=list)
    evidence_plan: list[dict[str, Any]] = field(default_factory=list)
    actions: list[dict[str, Any]] = field(default_factory=list)
    report: dict[str, Any] = field(default_factory=dict)

    display_text: str = ""
    is_garbled: bool = False
    garbled_reason: str = ""


# ── Extraction helpers ────────────────────────────────────────────

def _ensure_dict_list(val: Any) -> list[dict]:
    """Coerce val into a list of dicts."""
    if not isinstance(val, list):
        return [val] if isinstance(val, dict) else []
    result = []
    for item in val:
        if isinstance(item, dict):
            result.append(item)
        elif isinstance(item, str):
            result.append({"id": item, "text": item})
    return result


def _sanitize_json_string(raw: str) -> str:
    """Remove control characters that break json.loads.

    Preserves \\n (0x0a), \\r (0x0d), and \\t (0x09) which are valid in JSON
    whitespace.  Inside JSON string values, literal newlines are invalid — we
    escape them to ``\\n`` so ``json.loads`` succeeds without data loss.
    """
    # Step 1: strip truly harmful control chars (NUL, BEL, BS, VT, FF, etc.)
    cleaned = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', '', raw)

    # Step 2: escape literal newlines/tabs that appear inside JSON string values.
    # A quick heuristic: replace unescaped \n / \r / \t that sit between a pair
    # of unescaped double-quotes (i.e. inside a JSON string literal).
    def _escape_inside_strings(text: str) -> str:
        parts: list[str] = []
        in_string = False
        i = 0
        while i < len(text):
            ch = text[i]
            if ch == '\\' and in_string and i + 1 < len(text):
                # Escaped character — keep as-is
                parts.append(text[i:i + 2])
                i += 2
                continue
            if ch == '"':
                in_string = not in_string
                parts.append(ch)
                i += 1
                continue
            if in_string:
                if ch == '\n':
                    parts.append('\\n')
                elif ch == '\r':
                    parts.append('\\r')
                elif ch == '\t':
                    parts.append('\\t')
                else:
                    parts.append(ch)
            else:
                parts.append(ch)
            i += 1
        return ''.join(parts)

    return _escape_inside_strings(cleaned)


def _extract_raw_structured_json(text: str) -> dict | None:
    """Fallback: find a JSON object with 'structured_output' in raw text.

    Walks backwards from the marker to find the opening brace, then
    forward to find the matching closing brace, and tries to parse.
    """
    marker = '"structured_output"'
    idx = text.find(marker)
    if idx < 0:
        return None
    brace_start = text.rfind("{", 0, idx)
    if brace_start < 0:
        return None
    depth = 0
    for i in range(brace_start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                raw = text[brace_start : i + 1]
                try:
                    return json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    sanitized = _sanitize_json_string(raw)
                    try:
                        return json.loads(sanitized)
                    except (json.JSONDecodeError, ValueError):
                        logger.warning("Raw JSON fallback found object but failed to parse")
                        return None
    return None


def _matches_expected_schema(obj: dict) -> bool:
    """Return True if a parsed JSON dict looks like a valid agent output block.

    Expected top-level keys: ``structured_output`` and/or ``signals``.
    This distinguishes real agent output from example/template JSON that
    agents may include in their prose.
    """
    return isinstance(obj, dict) and bool(
        obj.get("structured_output") is not None or obj.get("signals") is not None
    )


def _try_parse_json(raw_json: str) -> dict | None:
    """Attempt to parse a JSON string, with sanitization fallback."""
    raw_json = raw_json.strip()
    if not raw_json:
        return None
    try:
        obj = json.loads(raw_json)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        pass
    sanitized = _sanitize_json_string(raw_json)
    try:
        obj = json.loads(sanitized)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def extract_json_block(text: str) -> dict | None:
    """Extract and parse the best ```json fenced block.

    Strategy:
      1. Parse ALL fenced ``json`` blocks.
      2. Prefer the block whose parsed dict matches the expected schema
         (contains ``structured_output`` or ``signals`` keys).  Among
         schema-matching blocks, the *last* one wins (agents tend to refine
         their answer towards the end of the response).
      3. If no block matches the schema, fall back to the last parseable block
         (backward-compat with the previous "last wins" behaviour).
      4. If no fenced blocks parse, try a raw JSON fallback for unfenced output.
    """
    matches = _JSON_BLOCK_RE.findall(text)
    if matches:
        schema_match: dict | None = None
        any_match: dict | None = None

        # Walk all matches; keep track of the last schema-valid and last-any.
        for raw_json in matches:
            parsed = _try_parse_json(raw_json)
            if parsed is None:
                continue
            any_match = parsed
            if _matches_expected_schema(parsed):
                schema_match = parsed

        if schema_match is not None:
            return schema_match
        if any_match is not None:
            logger.debug(
                "No JSON block matched expected schema (structured_output/signals); "
                "using last parseable block",
            )
            return any_match

        logger.warning(
            "JSON block(s) found (%d) but none could be parsed", len(matches),
        )

    # Fallback: find a raw JSON object containing "structured_output".
    # Handles cases where the agent outputs JSON without proper fencing
    # or where all fenced blocks are malformed.
    parsed = _extract_raw_structured_json(text)
    if parsed is not None:
        return parsed
    return None


def _strip_json_block(text: str) -> str:
    return _JSON_BLOCK_RE.sub("", text).strip()


def _strip_signals_block(text: str) -> str:
    idx = text.find("---SIGNALS---")
    return text[:idx].strip() if idx >= 0 else text


def _extract_symptoms_from_markdown(text: str) -> list[dict[str, Any]]:
    """Fallback: extract confirmed symptom IDs from markdown prose.

    When the LLM ignores the JSON output format and returns only markdown,
    we scan for SYM-*-NNN patterns that appear near "confirmed" context.
    Each extracted symptom gets minimal fields so hypothesis scoring can proceed.
    """
    found: list[tuple[str, str]] = []  # (sym_id, description)
    seen: set[str] = set()
    for match in _SYM_ID_RE.finditer(text):
        sym_id = match.group(1)
        if sym_id not in seen:
            # Try to grab description after "SYM-XXX-NNN: <text>" or "SYM-XXX-NNN — <text>"
            after = text[match.end():]
            desc = ""
            colon_match = re.match(r"\s*[:—–\-]\s*(.+)", after)
            if colon_match:
                desc = colon_match.group(1).split("\n")[0].strip()
            found.append((sym_id, desc))
            seen.add(sym_id)
    if not found:
        return []
    symptoms = []
    for sym_id, desc in found:
        cat = ""
        if "-" in sym_id:
            parts = sym_id.split("-")
            if len(parts) >= 2:
                cat = parts[1].lower()
        symptoms.append({
            "template_id": sym_id,
            "status": "confirmed",
            "weight": 1,
            "severity": "",
            "signal_strength": 0.0,
            "source_signal_type": "",
            "text": desc or sym_id,
            "enrichments": {},
            "category": cat,
        })
    logger.warning(
        "Markdown fallback: extracted %d symptom IDs from prose: %s",
        len(symptoms), [s["template_id"] for s in symptoms],
    )
    return symptoms


def _parse_legacy_signals(text: str) -> ParsedSignals:
    """Parse ---SIGNALS--- block into ParsedSignals."""
    signals = ParsedSignals()
    in_block = False
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped == "---SIGNALS---":
            in_block = True
            continue
        if in_block and stripped:
            m = _SIGNALS_KV_RE.match(stripped)
            if m:
                key, val = m.group(1), m.group(2).strip()
                if key == "PHASE_COMPLETE":
                    signals.phase_complete = val.lower()
                elif key == "NEXT_AGENT":
                    signals.next_agent = val
                elif key == "EVIDENCE_COLLECTED":
                    signals.evidence_collected = [
                        v.strip() for v in val.strip("[]").split(",") if v.strip()
                    ]
                elif key == "NEEDS_MORE_EVIDENCE":
                    signals.needs_more_evidence = val.lower() in ("true", "yes", "1")
            elif "INVESTIGATION_RESOLVED" in stripped.upper():
                signals.investigation_resolved = True
            elif stripped.startswith("---"):
                break
    return signals


def _parse_json_signals(raw: dict) -> ParsedSignals:
    """Convert the signals dict inside a JSON block to ParsedSignals."""
    signals = ParsedSignals()
    if not isinstance(raw, dict):
        return signals
    if raw.get("phase_complete"):
        signals.phase_complete = str(raw["phase_complete"]).lower()
    if raw.get("next_agent"):
        signals.next_agent = str(raw["next_agent"])
    ec = raw.get("evidence_collected")
    if ec:
        signals.evidence_collected = list(ec) if isinstance(ec, list) else [str(ec)]
    ir = raw.get("investigation_resolved")
    if ir is True or (isinstance(ir, str) and ir.lower() in ("true", "yes", "1")):
        signals.investigation_resolved = True
    nme = raw.get("needs_more_evidence")
    if nme is True or (isinstance(nme, str) and nme.lower() in ("true", "yes", "1")):
        signals.needs_more_evidence = True
    hr = raw.get("hypothesis_refuted")
    if hr is True or (isinstance(hr, str) and hr.lower() in ("true", "yes", "1")):
        signals.hypothesis_refuted = True
    return signals


# ── Main parse function ──────────────────────────────────────────

def parse_agent_output(raw_text: str, agent_name: str = "") -> ParsedAgentOutput:
    """Parse a complete agent turn into ParsedAgentOutput.

    Single entry-point: tries JSON block first, falls back to legacy SIGNALS.
    """
    result = ParsedAgentOutput(agent_name=agent_name, raw_text=raw_text)

    # 1. Try JSON extraction
    json_block = extract_json_block(raw_text)
    if json_block and isinstance(json_block, dict):
        # Schema validation: warn if the block lacks expected top-level keys.
        if not _matches_expected_schema(json_block):
            logger.warning(
                "Parsed JSON block from %s lacks expected keys "
                "(structured_output / signals); treating as non-JSON output",
                agent_name,
            )
            json_block = None

    if json_block and isinstance(json_block, dict):
        result.is_json_parsed = True
        so_raw = json_block.get("structured_output", {})
        result.structured_output = so_raw if isinstance(so_raw, dict) else {}
        sig_raw = json_block.get("signals", {})
        result.signals = _parse_json_signals(sig_raw if isinstance(sig_raw, dict) else {})
    else:
        result.is_json_parsed = False
        result.signals = _parse_legacy_signals(raw_text)

    # 2. Populate convenience fields from structured_output
    so = result.structured_output
    if so and isinstance(so, dict):
        result.symptoms = _ensure_dict_list(so.get("symptoms", so.get("validated_symptoms", [])))
        result.hypotheses = _ensure_dict_list(so.get("hypotheses", []))
        result.evaluations = _ensure_dict_list(so.get("evaluations", []))
        result.evidence_items = _ensure_dict_list(
            so.get("evidence_items", so.get("evidence_collected", []))
        )
        result.preliminary_verdicts = _ensure_dict_list(so.get("preliminary_verdicts", []))
        result.evidence_plan = _ensure_dict_list(so.get("evidence_plan", []))
        result.actions = _ensure_dict_list(so.get("actions", []))
        report = so.get("report", {})
        result.report = report if isinstance(report, dict) else {}

    # 2b. Markdown fallback for triage: extract symptoms from prose
    if not result.symptoms and not result.is_json_parsed and agent_name in ("triage_agent", "triage"):
        md_symptoms = _extract_symptoms_from_markdown(raw_text)
        if md_symptoms:
            result.symptoms = md_symptoms
            # Set phase_complete so downstream scoring runs
            if not result.signals.phase_complete:
                result.signals.phase_complete = "triage"

    # 3. Build display_text
    display = _strip_json_block(raw_text)
    display = _strip_signals_block(display)
    result.display_text = display.strip()

    # ── Log parse result to AgentLogger ────────────────────────────────
    xcv = get_current_xcv()
    if xcv:
        AgentLogger.get_instance().log_output_parsed(
            xcv=xcv,
            agent_name=agent_name,
            is_json_parsed=result.is_json_parsed,
            phase_complete=result.signals.phase_complete or "",
            next_agent=result.signals.next_agent or "",
            investigation_resolved=result.signals.investigation_resolved,
            needs_more_evidence=result.signals.needs_more_evidence,
            hypothesis_refuted=result.signals.hypothesis_refuted,
            symptoms_count=len(result.symptoms),
            hypotheses_count=len(result.hypotheses),
            evaluations_count=len(result.evaluations),
            evidence_items_count=len(result.evidence_items),
            actions_count=len(result.actions),
            raw_output=raw_text,
        )

    # 4. Garbled / degenerated output detection
    result.is_garbled, result.garbled_reason = _detect_garbled_output(result)
    if result.is_garbled:
        logger.warning(
            "Garbled output detected from %s: %s (json_parsed=%s, text_len=%d)",
            agent_name, result.garbled_reason, result.is_json_parsed, len(raw_text),
        )
        if xcv:
            AgentLogger.get_instance().log_investigation_error(
                xcv=xcv,
                investigation_id="",
                error=f"Garbled output from {agent_name}: {result.garbled_reason}",
                phase="",
            )

    return result


# ── Garbled / degenerated output detection ────────────────────────

# Agents that MUST produce a valid JSON block with specific content.
# If they don't, their output is considered garbled/degenerated.
_AGENT_REQUIRED_FIELDS: dict[str, list[str]] = {
    "reasoner": ["evaluations"],
    "evidence_planner": ["evidence_items"],
    "action_planner": ["actions"],
    "triage_agent": ["symptoms"],
}

# Heuristic: high ratio of non-ASCII/symbol characters in the display text
# indicates LLM degeneration (repetitive tokens, hallucinated markup, etc.)
_GARBLE_PATTERN = re.compile(r"[<>\*\{\}\[\]`]{3,}")
_PLACEHOLDER_PATTERN = re.compile(
    r"placeholder|lorem ipsum|TODO|FIXME|<\*>|<>",
    re.IGNORECASE,
)


def _detect_garbled_output(parsed: ParsedAgentOutput) -> tuple[bool, str]:
    """Detect garbled / degenerated / truncated agent output.

    Returns (is_garbled, reason) tuple.
    """
    agent = parsed.agent_name
    text = parsed.display_text or parsed.raw_text

    # Check 1: Empty or near-empty response
    if len(text.strip()) < 20:
        return True, "response_too_short"

    # Check 2: Agent requires JSON block but none was parsed
    required_fields = _AGENT_REQUIRED_FIELDS.get(agent)
    if required_fields and not parsed.is_json_parsed:
        return True, f"missing_json_block (expected fields: {', '.join(required_fields)})"

    # Check 3: JSON was parsed but required fields are empty
    if required_fields and parsed.is_json_parsed:
        missing = []
        for field_name in required_fields:
            val = getattr(parsed, field_name, None)
            if not val:
                missing.append(field_name)
        if missing:
            return True, f"json_parsed_but_empty_required_fields: {', '.join(missing)}"

    # Check 4: Text degeneration — repeated garble patterns or placeholders
    garble_matches = _GARBLE_PATTERN.findall(text)
    if len(garble_matches) >= 5:
        return True, "text_degeneration (excessive symbol sequences)"

    placeholder_matches = _PLACEHOLDER_PATTERN.findall(text)
    if len(placeholder_matches) >= 3:
        return True, "text_degeneration (placeholder patterns)"

    # Check 5: Truncation — text ends mid-sentence without JSON block
    # (agents are instructed to always end with a JSON block)
    if required_fields and not parsed.is_json_parsed:
        # Already caught by Check 2, but double-check truncation
        stripped = text.rstrip()
        if stripped and stripped[-1] not in ".!?)]\"}":
            return True, "likely_truncated (no terminal punctuation, no JSON block)"

    return False, ""


# ── Investigation state updater ──────────────────────────────────

def apply_to_investigation(
    parsed: ParsedAgentOutput,
    investigation: "Investigation",
) -> None:
    """Mutate investigation in-place based on parsed agent output.

    Called once per agent turn, immediately after parse_agent_output.
    """
    try:
        _apply_inner(parsed, investigation)
    except Exception as exc:
        logger.exception(
            "Failed to apply parsed output from %s: %s", parsed.agent_name, exc,
        )
        # Surface to App Insights so it's visible in telemetry
        xcv = get_current_xcv()
        if xcv:
            AgentLogger.get_instance().log_investigation_error(
                xcv=xcv,
                investigation_id=investigation.id,
                error=f"apply_to_investigation failed ({parsed.agent_name}): {exc}",
                phase=investigation.phase.value if hasattr(investigation.phase, 'value') else str(investigation.phase),
            )


def _apply_inner(parsed: ParsedAgentOutput, investigation: "Investigation") -> None:
    from .investigation_state import (
        InvestigationPhase,
        Symptom,
        Hypothesis,
        HypothesisStatus,
        EvidenceItem,
        EvidenceVerdict,
        SymptomVerdict,
    )

    sig = parsed.signals

    # ── Phase transition ─────────────────────────────────────
    # phase_complete means "this phase is DONE" → advance to the next phase.
    # Guard: the investigation must actually BE in the declared phase;
    # agents sometimes emit stale or wrong phase_complete values.
    _PHASE_ORDER = list(InvestigationPhase)
    if sig.phase_complete:
        for i, p in enumerate(_PHASE_ORDER):
            if sig.phase_complete == p.value and i + 1 < len(_PHASE_ORDER):
                if investigation.phase != p:
                    logger.warning(
                        "Ignoring phase_complete='%s' from %s — "
                        "investigation is in '%s', not '%s'",
                        sig.phase_complete, parsed.agent_name,
                        investigation.phase.value, p.value,
                    )
                    sig.phase_complete = None
                    break
                next_phase = _PHASE_ORDER[i + 1]
                logger.info(
                    "Phase complete '%s' → advancing to '%s' (agent=%s)",
                    sig.phase_complete, next_phase.value, parsed.agent_name,
                )
                investigation.transition_to(
                    next_phase,
                    source=f"output_parser:phase_complete({sig.phase_complete})",
                )
                break

    # Auto-advance to COLLECTING when evidence arrives during PLANNING
    if sig.evidence_collected and investigation.phase == InvestigationPhase.PLANNING:
        investigation.transition_to(
            InvestigationPhase.COLLECTING,
            source="output_parser:evidence_auto_advance",
        )

    if sig.investigation_resolved:
        investigation.transition_to(
            InvestigationPhase.COMPLETE,
            source="output_parser:investigation_resolved",
            force=True,
        )

    # ── Programmatic hypothesis refutation ────────────────────
    # IMPORTANT: Process refutation BEFORE updating current_hypothesis_id,
    # because the orchestrator output often contains both hypothesis_refuted=true
    # AND current_hypothesis="HYP-NEXT-xxx" in the same turn.  We must refute
    # the OLD current hypothesis, not the newly selected one.
    if sig.hypothesis_refuted and investigation._current_hypothesis_id:
        hyp_map = {h.id: h for h in investigation.hypotheses}
        current = hyp_map.get(investigation._current_hypothesis_id)
        if current and current.status == HypothesisStatus.ACTIVE:
            current.status = HypothesisStatus.REFUTED
            logger.info(
                "Programmatic refutation: %s → REFUTED (triggered by hypothesis_refuted signal from %s)",
                current.id, parsed.agent_name,
            )
            xcv = get_current_xcv()
            if xcv:
                AgentLogger.get_instance().log_hypothesis_transition(
                    xcv=xcv,
                    investigation_id=investigation.id,
                    hypothesis_id=current.id,
                    old_status="active",
                    new_status="refuted",
                    confidence=current.confidence,
                    statement=current.statement,
                )

    # ── Track current hypothesis from orchestrator's structured_output ─
    so = parsed.structured_output or {}
    current_hyp_id = so.get("current_hypothesis")
    if current_hyp_id and isinstance(current_hyp_id, str) and current_hyp_id.startswith("HYP-"):
        if investigation._current_hypothesis_id != current_hyp_id:
            logger.info(
                "Current hypothesis updated: %s → %s (agent=%s)",
                investigation._current_hypothesis_id, current_hyp_id, parsed.agent_name,
            )
            investigation._current_hypothesis_id = current_hyp_id

    # ── Symptoms ─────────────────────────────────────────────
    # Triage agent outputs confirmed symptoms with full fields from LLM matching.
    existing_sym_ids = {s.template_id for s in investigation.symptoms}
    for s in parsed.symptoms:
        tid = s.get("template_id", s.get("id", ""))
        if not tid or tid in existing_sym_ids:
            continue
        # Only include confirmed symptoms
        if s.get("status", "confirmed") != "confirmed":
            continue
        cat = s.get("category", "")
        if not cat and "-" in tid:
            cat = tid.split("-")[1].lower()  # SYM-SLI-001 → "sli"
        investigation.symptoms.append(Symptom(
            id=tid,
            template_id=tid,
            text=s.get("text", ""),
            category=cat,
            entities=s.get("enrichments", {}),
            source_signal_type=s.get("source_signal_type", ""),
            weight=int(s.get("weight", 1)),
            severity=s.get("severity", ""),
            signal_strength=float(s.get("signal_strength", 0.0)),
            confirmed=True,
        ))
        existing_sym_ids.add(tid)

    # ── Post-triage hypothesis scoring (programmatic Stage 2) ─
    if (
        sig.phase_complete == "triage"
        and not investigation.hypotheses
        and not investigation._scoring_attempted
    ):
        _run_post_triage_scoring(investigation)

    # ── Hypotheses ────────────────────────────────────────────
    existing_hyp_ids = {h.id for h in investigation.hypotheses}
    for h in parsed.hypotheses:
        hid = h.get("id", "")
        if hid and hid not in existing_hyp_ids:
            investigation.hypotheses.append(Hypothesis(
                id=hid,
                template_id=hid,
                statement=h.get("statement", ""),
                category=h.get("category", ""),
                confidence=float(h.get("confidence", 0)),
                evidence_needed=h.get("evidence_needed", []),
            ))
            existing_hyp_ids.add(hid)

    # ── Hypothesis evaluations (from reasoner) ────────────────
    hyp_map = {h.id: h for h in investigation.hypotheses}
    for ev in parsed.evaluations:
        hid = ev.get("hypothesis_id", "")
        if hid in hyp_map:
            hyp = hyp_map[hid]
            old_status = hyp.status.value if hasattr(hyp.status, 'value') else str(hyp.status)
            hyp.confidence = float(ev.get("confidence", hyp.confidence))
            status_str = (ev.get("status", "")).upper()
            if status_str == "CONFIRMED":
                hyp.status = HypothesisStatus.CONFIRMED
            elif status_str == "REFUTED":
                hyp.status = HypothesisStatus.REFUTED
            elif status_str == "CONTRIBUTING":
                hyp.status = HypothesisStatus.CONTRIBUTING
            new_status = hyp.status.value if hasattr(hyp.status, 'value') else str(hyp.status)
            # Log hypothesis transition
            if old_status != new_status:
                xcv = get_current_xcv()
                if xcv:
                    AgentLogger.get_instance().log_hypothesis_transition(
                        xcv=xcv,
                        investigation_id=investigation.id,
                        hypothesis_id=hid,
                        old_status=old_status,
                        new_status=new_status,
                        confidence=hyp.confidence,
                        statement=hyp.statement,
                    )
            for eev in ev.get("evidence", []):
                eid = eev.get("evidence_id", "")
                verdict_str = eev.get("verdict", "")
                if eid and verdict_str:
                    try:
                        hyp.verdicts[eid] = EvidenceVerdict(verdict_str.lower())
                    except ValueError:
                        pass

            # ── Symptom verdicts (per-symptom verification from reasoner) ─
            for sv in ev.get("symptom_verdicts", []):
                sid = sv.get("symptom_id", "")
                verdict_str = sv.get("verdict", "")
                if sid and verdict_str:
                    try:
                        hyp.symptom_verdicts[sid] = SymptomVerdict(verdict_str.lower())
                    except ValueError:
                        pass

    # ── Evidence items ────────────────────────────────────────
    # Deduplicate by er_id (not by synthetic id). When an ER was already
    # pre-populated from signal data (id="ev-sig-…") and the evidence_planner
    # later produces a richer entry for the same er_id, replace the earlier one.
    existing_er_id_idx: dict[str, int] = {}
    for idx, e in enumerate(investigation.evidence):
        if e.er_id:
            existing_er_id_idx[e.er_id] = idx

    for ei in parsed.evidence_items:
        eid = ei.get("id", ei.get("er_id", ""))
        er_id = ei.get("er_id", eid)
        if not eid:
            continue

        # If an entry with the same er_id already exists, replace it with the
        # newer (typically richer) entry — unless the new one is just a
        # "reused" / "signal_sourced" stub (those shouldn't overwrite real data).
        new_agent = ei.get("agent_name", parsed.agent_name)
        is_stub = new_agent in ("reused", "signal_sourced", "signal_builder")

        if er_id in existing_er_id_idx:
            existing_idx = existing_er_id_idx[er_id]
            existing = investigation.evidence[existing_idx]
            existing_is_stub = existing.agent_name in ("reused", "signal_sourced", "signal_builder")
            if existing_is_stub and not is_stub:
                # Replace signal-sourced stub with richer collector evidence
                investigation.evidence[existing_idx] = EvidenceItem(
                    id=er_id,
                    er_id=er_id,
                    hypothesis_ids=ei.get("hypothesis_ids", []),
                    agent_name=new_agent,
                    tool_name=ei.get("tool_name", ""),
                    summary=ei.get("summary", ""),
                    preliminary_verdict=ei.get("preliminary_verdict", ""),
                )
            # else: keep the existing (richer) entry, skip the new stub
        else:
            investigation.evidence.append(EvidenceItem(
                id=er_id,
                er_id=er_id,
                hypothesis_ids=ei.get("hypothesis_ids", []),
                agent_name=new_agent,
                tool_name=ei.get("tool_name", ""),
                summary=ei.get("summary", ""),
                preliminary_verdict=ei.get("preliminary_verdict", ""),
            ))
            existing_er_id_idx[er_id] = len(investigation.evidence) - 1

    # ── Preliminary verdicts ──────────────────────────────────
    for pv in parsed.preliminary_verdicts:
        hid = pv.get("hypothesis_id", "")
        if hid in hyp_map:
            verdict_str = pv.get("verdict", "")
            if verdict_str:
                try:
                    hyp_map[hid].verdicts[f"prelim_{parsed.agent_name}"] = \
                        EvidenceVerdict(verdict_str.lower())
                except ValueError:
                    pass

    # ── Actions ───────────────────────────────────────────────
    for act in parsed.actions:
        action_entry = {
            "action_id": act.get("action_id", act.get("id", "")),
            "display_name": act.get("display_name", ""),
            "tier": act.get("tier", ""),
            "priority": act.get("priority", 0),
            "justification": act.get("justification", ""),
            "target_hypotheses": act.get("target_hypotheses", []),
        }
        if action_entry["action_id"]:
            investigation.actions.append(action_entry)

    # ── Recompute evidence_delta for all hypotheses ───────────
    collected = investigation.collected_er_ids
    for hyp in investigation.hypotheses:
        hyp.evidence_delta = [er for er in hyp.evidence_needed if er not in collected]


def deduplicate_actions(actions: list[dict]) -> list[dict]:
    """Deduplicate actions by action_id, merging target_hypotheses lists.

    When action_planner runs standalone after all hypotheses are evaluated,
    multiple hypotheses may recommend the same action.  This merges them
    into a single entry per action_id, combining target_hypotheses and
    keeping the highest priority.
    """
    seen: dict[str, dict] = {}
    for act in actions:
        aid = act.get("action_id", "")
        if not aid:
            continue
        if aid in seen:
            existing = seen[aid]
            # Merge target_hypotheses
            existing_targets = set(existing.get("target_hypotheses", []))
            new_targets = set(act.get("target_hypotheses", []))
            existing["target_hypotheses"] = sorted(existing_targets | new_targets)
            # Keep higher priority
            if act.get("priority", 0) > existing.get("priority", 0):
                existing["priority"] = act["priority"]
            # Append justification from additional hypothesis
            if act.get("justification"):
                existing["justification"] = (
                    existing.get("justification", "") +
                    f" | {act['justification']}"
                )
        else:
            seen[aid] = dict(act)  # shallow copy to avoid mutating input
    return list(seen.values())


# ── PascalCase / mixed-case → snake_case normalisation ────────────
_CAMEL_RE1 = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_RE2 = re.compile(r"([a-z0-9])([A-Z])")


def _to_snake(name: str) -> str:
    """Convert PascalCase / camelCase / mixed names to snake_case.

    Examples: SubscriptionId → subscription_id, SLO_SliId → slo_sli_id
    """
    s = name.replace("-", "_")
    s = _CAMEL_RE1.sub(r"\1_\2", s)
    s = _CAMEL_RE2.sub(r"\1_\2", s)
    return s.lower()


class _SafeFormatMap(dict):
    """dict subclass for str.format_map — returns '{key}' for missing keys."""

    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fill_hypothesis_statements(investigation: "Investigation") -> None:
    """Fill {placeholder} variables in hypothesis statement templates.

    Builds a context dict from:
      1. InvestigationContext fields (customer_name, region, subscription_id, …)
      2. Signal data rows from signal_builder_result (flattened, snake_case)
    Then applies str.format_map with a safe fallback that leaves unfilled
    placeholders as-is.
    """
    result = investigation.signal_builder_result
    if not result:
        return

    # 1. Start with investigation context fields
    ctx = investigation.context
    template_vars: dict[str, str] = {
        "customer_name": ctx.customer_name or "",
        "region": ctx.region or "",
        "subscription_id": ctx.subscription_id or "",
        "slo_sli_id": ctx.sli_id or "",
        "incident_id": ctx.incident_id or "",
        "severity": ctx.severity or "",
    }

    # 2. Extract fields from signal data rows (first non-empty value wins)
    #    Signal rows use PascalCase (SubscriptionId) — normalise to snake_case.
    for tr in result.type_results:
        for sig in tr.activated_signals:
            for row in sig.matched_rows:
                for key, value in row.items():
                    snake_key = _to_snake(key)
                    if snake_key not in template_vars or not template_vars[snake_key]:
                        template_vars[snake_key] = str(value) if value is not None else ""

    # 3. Fill each hypothesis statement
    safe_ctx = _SafeFormatMap(template_vars)
    filled_count = 0
    for hyp in investigation.hypotheses:
        if "{" not in hyp.statement:
            continue
        original = hyp.statement
        try:
            hyp.statement = original.format_map(safe_ctx)
            if hyp.statement != original:
                filled_count += 1
        except (KeyError, ValueError, IndexError):
            logger.debug("Failed to fill hypothesis template %s, keeping original", hyp.id)

    if filled_count:
        logger.info(
            "Filled hypothesis statement templates: %d/%d hypotheses updated "
            "(%d context vars available)",
            filled_count, len(investigation.hypotheses), len(template_vars),
        )


def _run_post_triage_scoring(investigation: "Investigation") -> None:
    """Run programmatic hypothesis scoring after the triage agent completes.

    Stage 2 of the hybrid pipeline: scores hypotheses by measuring overlap
    between confirmed symptoms (from LLM triage) and each hypothesis's
    expected_symptoms, weighted by signal strength.

    Scoring config is loaded from config/hypotheses/scoring_config.json
    by the hypothesis_scorer itself.
    """
    from .hypothesis_scorer import score_hypotheses

    investigation._scoring_attempted = True

    confirmed = [s for s in investigation.symptoms if s.confirmed]
    if not confirmed:
        logger.warning("Post-triage scoring: no confirmed symptoms (total=%d), skipping",
                       len(investigation.symptoms))
        return

    logger.info(
        "Post-triage scoring: %d confirmed symptoms, running hypothesis scorer",
        len(confirmed),
    )
    ranked = score_hypotheses(confirmed)
    investigation.hypotheses = ranked

    # ── Fill hypothesis statement templates with signal data ──
    _fill_hypothesis_statements(investigation)

    # score_hypotheses() already emits HypothesisScoring via AgentLogger,
    # so we only emit per-hypothesis selection events here.
    xcv = get_current_xcv()

    if ranked:
        logger.info(
            "Post-triage scoring complete: %d hypotheses, top=%s (score=%.4f)",
            len(ranked), ranked[0].id, ranked[0].match_score,
        )
        # Emit per-hypothesis selection events so the UI shows each candidate
        for rank_idx, hyp in enumerate(ranked, start=1):
            AgentLogger.get_instance().log_hypothesis_selected(
                xcv=xcv,
                investigation_id=investigation.id,
                hypothesis_id=hyp.id,
                statement=hyp.statement,
                match_score=hyp.match_score,
                matched_symptoms=", ".join(hyp.matched_symptoms),
                evidence_needed=", ".join(hyp.evidence_needed),
                rank=rank_idx,
                total_hypotheses=len(ranked),
            )
    else:
        logger.warning("Post-triage scoring: no hypotheses met threshold")
