"""Production reward signals collector for continuous prompt improvement.

Collects reward signals from live production traffic (user feedback,
tool results, response latency, DeepEval scores) and aggregates them
into composite rewards for AGL prompt optimization.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict

logger = logging.getLogger(__name__)

# ── Composite reward weights ─────────────────────────────────

SIGNAL_WEIGHTS = {
    "user_feedback": 0.4,
    "tool_success": 0.2,
    "latency": 0.15,
    "deepeval": 0.25,
}

# ── Latency decay constants ──────────────────────────────────

_LATENCY_FLOOR = 5.0   # seconds — full reward at or below
_LATENCY_CEIL = 30.0   # seconds — zero reward at or above


def latency_to_reward(seconds: float) -> float:
    """Convert response latency to a reward score via linear decay.

    Returns 1.0 for latencies <= 5s, 0.0 for latencies >= 30s,
    and linearly interpolates between.

    Args:
        seconds: Response latency in seconds.

    Returns:
        Float between 0.0 and 1.0.
    """
    if seconds <= _LATENCY_FLOOR:
        return 1.0
    if seconds >= _LATENCY_CEIL:
        return 0.0
    return 1.0 - (seconds - _LATENCY_FLOOR) / (_LATENCY_CEIL - _LATENCY_FLOOR)


class ProductionRewardCollector:
    """Thread-safe collector for production reward signals.

    Stores signals keyed by xcv_id (correlation vector ID) and computes
    composite rewards as a weighted average of available signals.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, dict] = defaultdict(dict)

    def record_user_feedback(self, xcv_id: str, thumbs_up: bool) -> None:
        """Record user thumbs-up/down feedback.

        Args:
            xcv_id: Correlation vector ID for the request.
            thumbs_up: True for positive feedback, False for negative.
        """
        with self._lock:
            self._store[xcv_id]["user_feedback"] = 1.0 if thumbs_up else 0.0
        logger.debug("Recorded user feedback for %s: %s", xcv_id, thumbs_up)

    def record_tool_result(self, xcv_id: str, tool_name: str, success: bool) -> None:
        """Record MCP tool execution success/failure.

        When multiple tools run for the same request, stores the average
        success rate across all recorded tool results.

        Args:
            xcv_id: Correlation vector ID for the request.
            tool_name: Name of the MCP tool invoked.
            success: True if the tool executed without error.
        """
        score = 0.8 if success else 0.0
        with self._lock:
            entry = self._store[xcv_id]
            tools = entry.setdefault("_tool_results", [])
            tools.append({"tool": tool_name, "score": score})
            entry["tool_success"] = sum(t["score"] for t in tools) / len(tools)
        logger.debug(
            "Recorded tool result for %s: %s=%s", xcv_id, tool_name, success
        )

    def record_latency(self, xcv_id: str, latency_seconds: float) -> None:
        """Record response latency with linear decay reward mapping.

        Args:
            xcv_id: Correlation vector ID for the request.
            latency_seconds: End-to-end response time in seconds.
        """
        reward = latency_to_reward(latency_seconds)
        with self._lock:
            self._store[xcv_id]["latency"] = reward
        logger.debug(
            "Recorded latency for %s: %.2fs -> reward %.3f",
            xcv_id,
            latency_seconds,
            reward,
        )

    def record_deepeval_score(
        self, xcv_id: str, metric_name: str, score: float
    ) -> None:
        """Record a DeepEval metric score.

        When multiple metrics are recorded for the same request, stores
        the average across all recorded metrics.

        Args:
            xcv_id: Correlation vector ID for the request.
            metric_name: Name of the DeepEval metric.
            score: Metric score between 0.0 and 1.0.
        """
        clamped = max(0.0, min(1.0, score))
        with self._lock:
            entry = self._store[xcv_id]
            metrics = entry.setdefault("_deepeval_metrics", [])
            metrics.append({"metric": metric_name, "score": clamped})
            entry["deepeval"] = sum(m["score"] for m in metrics) / len(metrics)
        logger.debug(
            "Recorded deepeval score for %s: %s=%.3f", xcv_id, metric_name, clamped
        )

    @staticmethod
    def _compute_composite(entry: dict) -> float:
        """Compute weighted average reward from an entry dict (no locking)."""
        total_weight = 0.0
        weighted_sum = 0.0
        for signal, weight in SIGNAL_WEIGHTS.items():
            if signal in entry:
                total_weight += weight
                weighted_sum += weight * entry[signal]
        if total_weight == 0.0:
            return 0.0
        return max(0.0, min(1.0, weighted_sum / total_weight))

    def get_composite_reward(self, xcv_id: str) -> float:
        """Aggregate all available signals into a composite reward.

        Uses weighted average with re-normalization — only signals that
        have been recorded contribute. If no signals exist, returns 0.0.

        Args:
            xcv_id: Correlation vector ID for the request.

        Returns:
            Float between 0.0 and 1.0.
        """
        with self._lock:
            entry = self._store.get(xcv_id)
            if not entry:
                return 0.0
            return self._compute_composite(entry)

    def get_all_rewards(self) -> list[dict]:
        """Return all recorded rewards for batch export.

        Returns:
            List of dicts with xcv_id, individual signals, and composite reward.
        """
        with self._lock:
            results = []
            for xcv_id, entry in self._store.items():
                record = {"xcv_id": xcv_id}
                for signal in SIGNAL_WEIGHTS:
                    if signal in entry:
                        record[signal] = entry[signal]
                record["composite_reward"] = self._compute_composite(entry)
                results.append(record)
        return results

    def clear(self) -> None:
        """Clear all recorded signals."""
        with self._lock:
            self._store.clear()
        logger.info("Production reward collector cleared")


# ── Module-level singleton ────────────────────────────────────

collector = ProductionRewardCollector()
