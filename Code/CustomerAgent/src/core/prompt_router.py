"""
A/B testing prompt router for agent prompt variants.

Routes traffic between production and candidate prompts based on
configurable weights, using deterministic hashing for consistent
assignment within a request (xcv_id).
"""
from __future__ import annotations

import hashlib
import logging
import math
import random
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VariantConfig:
    """Configuration for a single prompt variant."""

    variant_id: str
    prompt: str
    weight: float


class PromptRouter:
    """Routes requests to prompt variants for A/B testing."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._variants: dict[str, list[VariantConfig]] = {}
        self._outcomes: dict[str, dict[str, list[float]]] = {}

    def register_variant(
        self, agent_name: str, variant_id: str, prompt: str, weight: float
    ) -> None:
        """Register a prompt variant with a traffic weight."""
        with self._lock:
            if agent_name not in self._variants:
                self._variants[agent_name] = []
                self._outcomes[agent_name] = {}
            self._variants[agent_name].append(
                VariantConfig(variant_id=variant_id, prompt=prompt, weight=weight)
            )
            self._outcomes[agent_name].setdefault(variant_id, [])
            logger.info(
                "Registered variant '%s' for agent '%s' (weight=%.2f)",
                variant_id,
                agent_name,
                weight,
            )

    def select_variant(
        self, agent_name: str, xcv_id: str | None = None
    ) -> tuple[str, str]:
        """Select a prompt variant for a request.

        Uses a deterministic hash of xcv_id so the same request always
        gets the same variant. Falls back to random if no xcv_id.

        Returns:
            (variant_id, prompt_text)

        Raises:
            KeyError: If no variants registered for agent_name.
        """
        with self._lock:
            variants = self._variants.get(agent_name)
            if not variants:
                raise KeyError(f"No variants registered for agent '{agent_name}'")
            if len(variants) == 1:
                v = variants[0]
                return v.variant_id, v.prompt
            total_weight = sum(v.weight for v in variants)
            if total_weight <= 0:
                raise ValueError(f"Total weight for agent '{agent_name}' is <= 0")
            if xcv_id:
                digest = hashlib.md5(xcv_id.encode()).hexdigest()  # noqa: S324
                bucket = int(digest, 16) / (16**32)
            else:
                bucket = random.random()  # noqa: S311
            cumulative = 0.0
            for v in variants:
                cumulative += v.weight / total_weight
                if bucket < cumulative:
                    return v.variant_id, v.prompt
            last = variants[-1]
            return last.variant_id, last.prompt

    def record_outcome(
        self, agent_name: str, variant_id: str, xcv_id: str, reward: float
    ) -> None:
        """Record a reward outcome for a variant."""
        with self._lock:
            agent_outcomes = self._outcomes.get(agent_name)
            if agent_outcomes is None:
                logger.warning(
                    "No variants registered for agent '%s', ignoring outcome",
                    agent_name,
                )
                return
            if variant_id not in agent_outcomes:
                agent_outcomes[variant_id] = []
            agent_outcomes[variant_id].append(reward)

    def get_metrics(self, agent_name: str) -> dict:
        """Return per-variant metrics: count, avg_reward, std_reward."""
        with self._lock:
            agent_outcomes = self._outcomes.get(agent_name, {})
            metrics: dict[str, dict] = {}
            for variant_id, rewards in agent_outcomes.items():
                n = len(rewards)
                if n == 0:
                    metrics[variant_id] = {
                        "count": 0,
                        "avg_reward": 0.0,
                        "std_reward": 0.0,
                    }
                    continue
                mean = sum(rewards) / n
                variance = sum((r - mean) ** 2 for r in rewards) / n
                metrics[variant_id] = {
                    "count": n,
                    "avg_reward": mean,
                    "std_reward": math.sqrt(variance),
                }
            return metrics

    def should_promote(
        self,
        agent_name: str,
        candidate_variant: str,
        min_samples: int = 50,
        significance_threshold: float = 0.05,
    ) -> bool:
        """Check if candidate significantly outperforms production (z-test).

        Returns True if candidate mean reward is higher AND the p-value
        is below significance_threshold.
        """
        with self._lock:
            agent_outcomes = self._outcomes.get(agent_name, {})
            variants = self._variants.get(agent_name, [])
            production_ids = [
                v.variant_id for v in variants if v.variant_id != candidate_variant
            ]
            if not production_ids:
                return False
            production_id = production_ids[0]
            prod_rewards = agent_outcomes.get(production_id, [])
            cand_rewards = agent_outcomes.get(candidate_variant, [])
            n_prod = len(prod_rewards)
            n_cand = len(cand_rewards)
            if n_prod < min_samples or n_cand < min_samples:
                return False
            mean_prod = sum(prod_rewards) / n_prod
            mean_cand = sum(cand_rewards) / n_cand
            if mean_cand <= mean_prod:
                return False
            var_prod = sum((r - mean_prod) ** 2 for r in prod_rewards) / n_prod
            var_cand = sum((r - mean_cand) ** 2 for r in cand_rewards) / n_cand
            pooled_se = math.sqrt(var_prod / n_prod + var_cand / n_cand)
            if pooled_se == 0:
                return mean_cand > mean_prod
            z = (mean_cand - mean_prod) / pooled_se
            p_value = 1.0 - _normal_cdf(z)
            logger.info(
                "Promotion check for '%s' vs '%s': z=%.3f, p=%.4f (threshold=%.4f)",
                candidate_variant,
                production_id,
                z,
                p_value,
                significance_threshold,
            )
            return p_value < significance_threshold

    def set_split(
        self, agent_name: str, production_weight: float, candidate_weight: float
    ) -> None:
        """Configure traffic split between production and candidate.

        Assumes first registered variant is production, second is candidate.
        """
        with self._lock:
            variants = self._variants.get(agent_name, [])
            if len(variants) < 2:
                logger.warning(
                    "Cannot set split for agent '%s': need at least 2 variants",
                    agent_name,
                )
                return
            variants[0].weight = production_weight
            variants[1].weight = candidate_weight
            logger.info(
                "Set split for '%s': production=%.2f, candidate=%.2f",
                agent_name,
                production_weight,
                candidate_weight,
            )

    def get_active_variants(self, agent_name: str) -> list[dict]:
        """Return list of registered variants with their weights."""
        with self._lock:
            variants = self._variants.get(agent_name, [])
            return [
                {
                    "variant_id": v.variant_id,
                    "weight": v.weight,
                    "prompt_length": len(v.prompt),
                }
                for v in variants
            ]

    def clear(self, agent_name: str | None = None) -> None:
        """Clear variants for an agent, or all if agent_name is None."""
        with self._lock:
            if agent_name is None:
                self._variants.clear()
                self._outcomes.clear()
                logger.info("Cleared all prompt variants")
            else:
                self._variants.pop(agent_name, None)
                self._outcomes.pop(agent_name, None)
                logger.info("Cleared prompt variants for agent '%s'", agent_name)


def _normal_cdf(z: float) -> float:
    """Approximate the standard normal CDF using the error function."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


router = PromptRouter()
