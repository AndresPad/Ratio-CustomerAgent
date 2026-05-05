"""Context folding strategy for investigation GroupChat.

Implements the ``CompactionStrategy`` protocol to fold old conversation turns
into a structured state-summary message at phase boundaries.  This keeps the
agent context window manageable during long investigations while preserving
all semantically critical information.

Feature flag: ``ENABLE_CONTEXT_FOLDING`` (env var, default ``false``).

The strategy is instantiated **inside** ``investigation_runner.py`` (closure
capture pattern) so it has a live reference to the ``Investigation`` object.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from agent_framework import Message

from .investigation_state import (
    Investigation,
    InvestigationPhase,
    Hypothesis,
    HypothesisStatus,
)
from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)

# ── Feature flag ─────────────────────────────────────────────────────────────
_FOLDING_ENABLED = os.getenv(
    "ENABLE_CONTEXT_FOLDING", "false"
).strip().lower() in ("true", "1", "yes")
FOLDING_ENABLED = _FOLDING_ENABLED  # public alias for import

# Minimum messages before folding is attempted.  Prevents folding during the
# first few turns when there isn't enough context to summarise.
_MIN_MESSAGES_TO_FOLD = int(os.getenv("CONTEXT_FOLDING_MIN_MESSAGES", "10"))

# Number of most-recent messages to preserve (never folded).
_PRESERVE_TAIL = int(os.getenv("CONTEXT_FOLDING_PRESERVE_TAIL", "4"))


# ── Heuristic token estimator ───────────────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough 4-chars-per-token estimate (same heuristic as SDK's CharacterEstimatorTokenizer)."""
    return max(1, len(text) // 4)


def _message_text(msg: Message) -> str:
    """Extract text payload from a Message."""
    return msg.text or ""


# ── Summary builder ──────────────────────────────────────────────────────────

def _build_state_summary(investigation: Investigation) -> str:
    """Build a structured JSON summary from the live Investigation state.

    This replaces the folded messages with a compact representation that
    retains all semantically important information:
      - Current phase & context
      - Confirmed symptoms
      - Hypothesis queue with statuses/scores
      - Evidence plan progress
      - Key evidence verdicts
      - Findings so far
    """
    hyp_summaries = []
    for h in investigation.hypotheses:
        hyp_summaries.append({
            "id": h.id,
            "statement": h.statement,
            "status": h.status.value if isinstance(h.status, HypothesisStatus) else str(h.status),
            "confidence": h.confidence,
            "match_score": h.match_score,
            "evidence_collected": len(h.evidence_collected),
            "evidence_needed": len(h.evidence_needed),
            "determination": h.determination or "",
        })

    evidence_summary = []
    seen_er_ids: set[str] = set()
    # Iterate in reverse so later (richer) entries win over earlier stubs
    for ei in reversed(investigation.evidence):
        key = ei.er_id or ei.id
        if key in seen_er_ids:
            continue
        seen_er_ids.add(key)
        evidence_summary.append({
            "id": key,
            "er_id": ei.er_id,
            "agent": ei.agent_name,
            "tool": ei.tool_name,
            "verdict": ei.final_verdict.value if ei.final_verdict else ei.preliminary_verdict,
            "summary": ei.summary[:200] if ei.summary else "",
        })
    evidence_summary.reverse()  # restore chronological order

    plan_summary = []
    for er in investigation.evidence_plan:
        plan_summary.append({
            "er_id": er.er_id,
            "description": er.description,
            "status": er.status,
            "hypothesis_ids": er.hypothesis_ids,
        })

    symptom_texts = [
        {"id": s.id, "text": s.text, "category": s.category, "confirmed": s.confirmed}
        for s in investigation.symptoms
    ]

    summary = {
        "type": "context_folding_summary",
        "investigation_id": investigation.id,
        "phase": investigation.phase.value,
        "evidence_cycles": investigation.evidence_cycles,
        "context": {
            "customer": investigation.context.customer_name,
            "service_tree_id": investigation.context.service_tree_id,
            "region": investigation.context.region,
            "severity": investigation.context.severity,
        },
        "symptoms": symptom_texts,
        "hypotheses": hyp_summaries,
        "evidence_plan": plan_summary,
        "evidence_collected": evidence_summary,
        "findings": investigation.findings,
    }
    return json.dumps(summary, indent=2)


# ── Folding strategy ─────────────────────────────────────────────────────────

class InvestigationFoldingStrategy:
    """CompactionStrategy that folds old turns into a state-summary message.

    Follows the ``CompactionStrategy`` protocol:
        ``async def __call__(self, messages: list[Message]) -> bool``

    The strategy is stateful: it tracks the last phase at which folding
    occurred to avoid re-folding on every agent turn (only folds at phase
    boundaries or when the message count exceeds a threshold).

    Args:
        investigation: Live ``Investigation`` object (closure-captured).
        agent_name: Name of the agent this strategy is attached to.
        fold_threshold: Fold when message count exceeds this, regardless
            of phase boundary.  0 = only fold at phase boundaries.
    """

    def __init__(
        self,
        investigation: Investigation,
        agent_name: str = "",
        fold_threshold: int = 0,
    ) -> None:
        self._investigation = investigation
        self._agent_name = agent_name
        self._fold_threshold = fold_threshold
        self._last_folded_phase: InvestigationPhase | None = None
        self._fold_count: int = 0

    async def __call__(self, messages: list[Message]) -> bool:
        """Mutate ``messages`` in-place: fold old turns into a summary.

        Returns ``True`` if messages were changed, ``False`` otherwise.
        """
        if not _FOLDING_ENABLED:
            return False

        # Not enough messages to warrant folding
        if len(messages) < _MIN_MESSAGES_TO_FOLD:
            return False

        current_phase = self._investigation.phase

        # Decide whether to fold: phase boundary or threshold exceeded
        phase_changed = (
            self._last_folded_phase is not None
            and current_phase != self._last_folded_phase
        )
        threshold_exceeded = (
            self._fold_threshold > 0
            and len(messages) > self._fold_threshold
        )

        # First invocation: always fold if we have enough messages
        first_fold = self._last_folded_phase is None

        if not (phase_changed or threshold_exceeded or first_fold):
            return False

        # ── Identify what to preserve ────────────────────────────────
        # Always keep: system messages (index 0 usually), last N messages
        preserve_indices: set[int] = set()

        # Preserve system messages
        for i, msg in enumerate(messages):
            if msg.role == "system":
                preserve_indices.add(i)

        # Preserve the original task (first user message)
        for i, msg in enumerate(messages):
            if msg.role == "user":
                preserve_indices.add(i)
                break

        # Preserve tail messages
        tail_start = max(0, len(messages) - _PRESERVE_TAIL)
        for i in range(tail_start, len(messages)):
            preserve_indices.add(i)

        # Preserve any existing folding summary messages
        for i, msg in enumerate(messages):
            if msg.role == "user" and _message_text(msg).startswith('{"type": "context_folding_summary"'):
                preserve_indices.add(i)

        # Identify foldable messages (everything not preserved)
        foldable_indices = [
            i for i in range(len(messages)) if i not in preserve_indices
        ]

        if not foldable_indices:
            return False

        # ── Build summary ────────────────────────────────────────────
        original_token_estimate = sum(
            _estimate_tokens(_message_text(messages[i])) for i in foldable_indices
        )

        summary_text = _build_state_summary(self._investigation)
        summary_token_estimate = _estimate_tokens(summary_text)

        # Only fold if it actually saves tokens
        if summary_token_estimate >= original_token_estimate:
            logger.debug(
                "Folding skipped for %s: summary (%d tokens) >= original (%d tokens)",
                self._agent_name, summary_token_estimate, original_token_estimate,
            )
            return False

        # ── Apply fold: remove old messages, insert summary ──────────
        messages_folded = len(foldable_indices)

        # Remove foldable messages in reverse order to preserve indices
        for i in sorted(foldable_indices, reverse=True):
            messages.pop(i)

        # Find insertion point: after system messages and first user message,
        # before the preserved tail.
        insert_at = 0
        for i, msg in enumerate(messages):
            if msg.role == "system":
                insert_at = i + 1
            elif msg.role == "user":
                insert_at = i + 1
                break

        # Remove any previous folding summary (replace, not accumulate)
        for i in range(len(messages) - 1, -1, -1):
            if messages[i].role == "user" and _message_text(messages[i]).startswith('{"type": "context_folding_summary"'):
                messages.pop(i)
                if i < insert_at:
                    insert_at -= 1

        summary_message = Message(
            role="user",
            contents=[
                f"[CONTEXT FOLDING — Investigation state summary as of phase "
                f"'{current_phase.value}', replacing {messages_folded} earlier messages]\n\n"
                f"{summary_text}"
            ],
        )
        messages.insert(insert_at, summary_message)

        # ── Update state ─────────────────────────────────────────────
        self._last_folded_phase = current_phase
        self._fold_count += 1

        token_reduction = original_token_estimate - summary_token_estimate

        logger.info(
            "Context folding applied for agent '%s' at phase '%s': "
            "folded %d messages, ~%d→%d tokens (saved ~%d)",
            self._agent_name,
            current_phase.value,
            messages_folded,
            original_token_estimate,
            summary_token_estimate,
            token_reduction,
        )

        # ── Log to Application Insights ──────────────────────────────
        try:
            tracker = AgentLogger.get_instance()
            tracker.log_context_folding(
                xcv=get_current_xcv() or "",
                agent_name=self._agent_name,
                phase=current_phase.value,
                investigation_id=self._investigation.id,
                messages_folded=messages_folded,
                original_tokens=original_token_estimate,
                folded_tokens=summary_token_estimate,
                fold_number=self._fold_count,
                summary_content=summary_text,
            )
        except Exception:
            logger.debug("Failed to log context folding event", exc_info=True)

        return True
