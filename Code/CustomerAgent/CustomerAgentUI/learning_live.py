"""Live Investigation Learning Orchestrator (F24).

Runs the actual investigation pipeline, computes real rewards,
runs real APO optimization, and re-investigates with optimized prompts.
All events are streamed via the provided emit_fn callback.

Usage: imported by learning_api.py for live mode.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── sys.path setup (same pattern as learning scripts) ────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CUSTOMER_AGENT_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, ".."))
_SRC_DIR = os.path.join(_CUSTOMER_AGENT_DIR, "src")

if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
if _CUSTOMER_AGENT_DIR not in sys.path:
    sys.path.insert(0, _CUSTOMER_AGENT_DIR)

# ── Agent-to-stage mapping for reward computation ────────────

AGENT_REWARD_MAP = {
    "entity_extractor": {
        "stage": 1,
        "label": "Signals",
        "reward_fn": "compute_entity_extractor_reward",
    },
    "outage_analyst": {
        "stage": 2,
        "label": "Symptoms",
        "reward_fn": "compute_analyst_reward",
    },
    "airo_analyst": {
        "stage": 2,
        "label": "Symptoms",
        "reward_fn": "compute_analyst_reward",
    },
    "customer_insights": {
        "stage": 3,
        "label": "Hypotheses",
        "reward_fn": "compute_analyst_reward",
    },
    "reasoner": {
        "stage": 4,
        "label": "Evidence",
        "reward_fn": "compute_reasoner_reward",
    },
    "summarizer": {
        "stage": 5,
        "label": "Actions",
        "reward_fn": "compute_summarizer_reward",
    },
}

# Agents that the orchestrator controls (skip orchestrator itself)
_ORCHESTRATOR_AGENT = "orchestrator"


def _build_query(params: dict) -> str:
    """Construct investigation query from params."""
    customer = params.get("customer_name", "")
    service_tree = params.get("service_tree_id", "")
    start = params.get("start_time", "")
    end = params.get("end_time", "")

    parts = ["Investigate"]
    if customer:
        parts.append(f"customer {customer}")
    if service_tree:
        parts.append(f"for service tree {service_tree}")
    if start and end:
        parts.append(f"between {start} and {end}")
    elif start:
        parts.append(f"from {start}")

    if len(parts) == 1:
        return "Investigate recent outage activity and customer impact"
    return " ".join(parts)


def _compute_reward(agent_name: str, query: str, output: str) -> tuple[float, dict]:
    """Compute reward for an agent's output using the appropriate reward function.

    Since live mode has no ground truth, we pass empty expected values
    and score based on structural quality of the output.

    Returns:
        (reward_score, breakdown_dict)
    """
    from learning.rewards import (
        compute_analyst_reward,
        compute_entity_extractor_reward,
        compute_reasoner_reward,
        compute_summarizer_reward,
    )

    try:
        mapping = AGENT_REWARD_MAP.get(agent_name)
        if not mapping:
            return 0.0, {}

        fn_name = mapping["reward_fn"]

        if fn_name == "compute_entity_extractor_reward":
            reward = compute_entity_extractor_reward(
                query=query,
                agent_output=output,
                expected_entities={},
                expected_output="",
            )
            return reward, {"entity_accuracy": reward, "relevancy": 0.0}

        elif fn_name == "compute_analyst_reward":
            reward = compute_analyst_reward(
                query=query,
                agent_output=output,
                expected_sql="",
                expected_output="",
            )
            return reward, {"sql_quality": reward, "completeness": 0.0, "relevancy": 0.0}

        elif fn_name == "compute_reasoner_reward":
            reward = compute_reasoner_reward(
                hypothesis=query,
                evidence=output,
                agent_output=output,
                expected_verdict="",
                expected_reasoning="",
            )
            return reward, {"verdict_accuracy": 0.0, "reasoning_quality": reward}

        elif fn_name == "compute_summarizer_reward":
            reward = compute_summarizer_reward(
                analyst_input=query,
                agent_output=output,
                expected_output="",
            )
            return reward, {"structure": reward, "coverage": 0.0, "relevancy": 0.0}

        return 0.0, {}

    except Exception as exc:
        logger.warning("Reward computation failed for %s: %s", agent_name, exc)
        return 0.0, {}


def _narration_color(score: float) -> str:
    """Return narration color based on score threshold."""
    if score >= 0.75:
        return "green"
    if score >= 0.60:
        return "orange"
    return "red"


def _friendly_name(agent_name: str) -> str:
    return agent_name.replace("_", " ").title()


# ── Phase 1: Live Investigation ──────────────────────────────

async def _run_investigation(
    emit_fn: Callable[[str, dict], None],
    query: str,
    config_override: dict | None = None,
) -> tuple[dict[str, float], dict[str, str]]:
    """Run the GroupChat investigation pipeline.

    Returns:
        (per_agent_rewards, per_agent_outputs) dicts.
    """
    from core.agent_factory import create_agents, load_config
    from core.orchestrator import build_group_chat_workflow

    config = config_override if config_override else load_config()
    agents, capture_mw, eval_mw, injection_mw, llm_log_mw, prompts = await create_agents(config)

    workflow_result = build_group_chat_workflow(
        agents, config, capture_mw, eval_mw, injection_mw, llm_log_mw, prompts,
    )
    workflow = workflow_result[0] if isinstance(workflow_result, tuple) else workflow_result

    per_agent_rewards: dict[str, float] = {}
    per_agent_outputs: dict[str, str] = {}
    current_agent: str | None = None
    current_text_parts: list[str] = []

    def _flush_agent():
        """Score the current agent and emit events."""
        nonlocal current_agent, current_text_parts
        if not current_agent or current_agent == _ORCHESTRATOR_AGENT:
            current_text_parts = []
            return

        full_text = "\n".join(current_text_parts)
        per_agent_outputs[current_agent] = full_text

        mapping = AGENT_REWARD_MAP.get(current_agent)
        if mapping:
            reward, breakdown = _compute_reward(current_agent, query, full_text)
            per_agent_rewards[current_agent] = reward

            emit_fn("investigation_stage", {
                "stage": mapping["stage"],
                "agent": current_agent,
                "label": mapping["label"],
                "output_summary": full_text[:500],
                "duration_ms": 0,
            })

            emit_fn("agent_reward", {
                "stage": mapping["stage"],
                "agent": current_agent,
                "reward": round(reward, 4),
                "breakdown": {k: round(v, 4) for k, v in breakdown.items()},
            })

            color = _narration_color(reward)
            emit_fn("learning_narration", {
                "color": color,
                "text": (
                    f"{_friendly_name(current_agent)} completed with "
                    f"reward {reward:.0%}."
                ),
            })

        current_text_parts = []

    from agent_framework import AgentResponse, AgentResponseUpdate, WorkflowEvent

    async for event in workflow.run(query, stream=True):
        event: WorkflowEvent

        if event.type == "output":
            data = event.data

            if isinstance(data, AgentResponseUpdate):
                agent_name = event.executor_id or "unknown"
                if agent_name != current_agent:
                    _flush_agent()
                    current_agent = agent_name
                text = data.text if hasattr(data, "text") and data.text else ""
                if text:
                    current_text_parts.append(text)

            elif isinstance(data, AgentResponse):
                agent_name = event.executor_id or "unknown"
                if agent_name != current_agent:
                    _flush_agent()
                    current_agent = agent_name
                for msg in data.messages:
                    if msg.text:
                        current_text_parts.append(msg.text)

    # Flush final agent
    _flush_agent()

    return per_agent_rewards, per_agent_outputs


# ── Phase 2: APO Optimization ────────────────────────────────

def _run_apo_optimization(
    emit_fn: Callable[[str, dict], None],
) -> dict[str, dict]:
    """Run APO for all agents in optimization order.

    Returns:
        Dict of agent_name -> optimization result dict.
    """
    from learning.run_apo_all import AGENT_CONFIG, OPTIMIZATION_ORDER, _optimize_agent

    results: dict[str, dict] = {}

    for agent_name in OPTIMIZATION_ORDER:
        if agent_name not in AGENT_CONFIG:
            continue

        emit_fn("learning_narration", {
            "color": "green",
            "text": f"Starting APO optimization for {_friendly_name(agent_name)}...",
        })

        try:
            result = _optimize_agent(agent_name, AGENT_CONFIG[agent_name])
            results[agent_name] = result

            emit_fn("learning_round_complete", {
                "round": len(results),
                "agent_scores": {
                    name: 1.0 if r.get("changed", False) else 0.0
                    for name, r in results.items()
                },
            })

            status = "improved" if result.get("changed") else "unchanged"
            emit_fn("learning_narration", {
                "color": "green" if result.get("changed") else "orange",
                "text": (
                    f"{_friendly_name(agent_name)} optimization complete: "
                    f"{status}. Prompt {result.get('seed_len', 0)} → "
                    f"{result.get('optimized_len', 0)} chars "
                    f"({result.get('elapsed_s', 0):.0f}s)."
                ),
            })

        except Exception as exc:
            logger.warning("APO optimization failed for %s: %s", agent_name, exc)
            results[agent_name] = {
                "agent": agent_name,
                "changed": False,
                "error": str(exc),
            }
            emit_fn("learning_narration", {
                "color": "red",
                "text": f"{_friendly_name(agent_name)} optimization failed: {exc}",
            })

    return results


# ── Phase 3: Re-Investigation with optimized prompts ─────────

def _load_optimized_prompts() -> dict[str, str]:
    """Load optimized prompts from src/learning/outputs/ directory."""
    from learning.run_apo_all import AGENT_CONFIG

    outputs_dir = os.path.join(_CUSTOMER_AGENT_DIR, "src", "learning", "outputs")
    overrides: dict[str, str] = {}

    for agent_name, cfg in AGENT_CONFIG.items():
        prompt_base = os.path.splitext(cfg["prompt_file"])[0]
        optimized_path = os.path.join(outputs_dir, f"{prompt_base}_optimized.txt")
        if os.path.isfile(optimized_path):
            try:
                with open(optimized_path, "r", encoding="utf-8") as f:
                    overrides[agent_name] = f.read().strip()
                logger.info(
                    "Loaded optimized prompt for %s (%d chars)",
                    agent_name, len(overrides[agent_name]),
                )
            except Exception as exc:
                logger.warning("Failed to load optimized prompt for %s: %s", agent_name, exc)

    return overrides


def _inject_prompt_overrides(config: dict, overrides: dict[str, str]) -> dict:
    """Inject _prompt_override into agent configs."""
    import copy
    config = copy.deepcopy(config)
    for agent_cfg in config.get("agents", []):
        name = agent_cfg.get("name", "")
        if name in overrides:
            agent_cfg["_prompt_override"] = overrides[name]
    return config


# ── Main entry point ─────────────────────────────────────────

async def _run_live_pipeline(
    emit_fn: Callable[[str, dict], None],
    params: dict,
):
    """Async inner function that runs all three phases."""
    query = _build_query(params)

    # ── Phase 1: Live Investigation ──────────────────────────
    emit_fn("learning_started", {
        "scenario": "Live Investigation",
        "description": f"Running live investigation: {query}",
    })

    emit_fn("learning_narration", {
        "color": "green",
        "text": f"Starting live investigation pipeline: {query}",
    })

    try:
        per_agent_rewards, per_agent_outputs = await _run_investigation(emit_fn, query)
    except Exception as exc:
        logger.exception("Phase 1 (investigation) failed")
        emit_fn("learning_error", {"error": f"Investigation failed: {exc}"})
        return

    if not per_agent_rewards:
        emit_fn("learning_error", {"error": "No agent rewards collected during investigation"})
        return

    all_rewards = list(per_agent_rewards.values())
    total_reward = round(sum(all_rewards) / len(all_rewards), 4)

    emit_fn("investigation_complete", {
        "total_reward": total_reward,
        "per_agent_rewards": {k: round(v, 4) for k, v in per_agent_rewards.items()},
    })

    emit_fn("learning_narration", {
        "color": "green",
        "text": (
            f"Investigation complete. Composite reward: {total_reward:.0%} "
            f"across {len(all_rewards)} agents."
        ),
    })

    # ── Phase 2: APO Optimization ────────────────────────────
    agent_names = list(per_agent_rewards.keys())

    emit_fn("learning_phase_started", {
        "agents": agent_names,
        "total_rounds": len(agent_names),
    })

    emit_fn("learning_narration", {
        "color": "green",
        "text": (
            f"Starting APO prompt optimization for {len(agent_names)} agents."
        ),
    })

    try:
        apo_results = _run_apo_optimization(emit_fn)
    except Exception as exc:
        logger.exception("Phase 2 (APO optimization) failed")
        emit_fn("learning_error", {"error": f"APO optimization failed: {exc}"})
        return

    score_deltas = {
        name: {
            "original_len": r.get("seed_len", 0),
            "optimized_len": r.get("optimized_len", 0),
        }
        for name, r in apo_results.items()
    }

    emit_fn("learning_complete", {
        "improved_prompts": score_deltas,
        "score_deltas": {
            name: 1.0 if r.get("changed") else 0.0
            for name, r in apo_results.items()
        },
    })

    emit_fn("learning_narration", {
        "color": "green",
        "text": "Prompt optimization complete. Preparing re-investigation.",
    })

    # ── Phase 3: Re-Investigation ────────────────────────────
    emit_fn("learning_narration", {
        "color": "green",
        "text": "Re-running investigation with optimized prompts.",
    })

    try:
        overrides = _load_optimized_prompts()
        if not overrides:
            emit_fn("learning_narration", {
                "color": "orange",
                "text": "No optimized prompts found. Re-investigation will use original prompts.",
            })

        from core.agent_factory import load_config
        config = load_config()
        config = _inject_prompt_overrides(config, overrides)

        new_rewards, _ = await _run_investigation(emit_fn, query, config_override=config)
    except Exception as exc:
        logger.exception("Phase 3 (re-investigation) failed")
        emit_fn("learning_error", {"error": f"Re-investigation failed: {exc}"})
        return

    improvements: dict[str, dict[str, Any]] = {}
    for agent_name in per_agent_rewards:
        before = per_agent_rewards[agent_name]
        after = new_rewards.get(agent_name, before)
        improvements[agent_name] = {
            "before": round(before, 4),
            "after": round(after, 4),
            "delta": round(after - before, 4),
        }

        emit_fn("reinvestigation_stage", {
            "stage": AGENT_REWARD_MAP.get(agent_name, {}).get("stage", 0),
            "agent": agent_name,
            "label": AGENT_REWARD_MAP.get(agent_name, {}).get("label", ""),
            "output_summary": "",
            "old_score": round(before, 4),
            "new_score": round(after, 4),
        })

        color = _narration_color(after)
        emit_fn("learning_narration", {
            "color": color,
            "text": (
                f"{_friendly_name(agent_name)} re-scored: "
                f"{before:.0%} → {after:.0%} "
                f"({'improved' if after > before else 'unchanged'})."
            ),
        })

    emit_fn("reinvestigation_complete", {
        "improvements": improvements,
    })

    deltas = [v["delta"] for v in improvements.values()]
    composite_pct = round(sum(deltas) / len(deltas) * 100) if deltas else 0
    emit_fn("learning_narration", {
        "color": "green",
        "text": f"Re-investigation complete. Composite improvement: +{composite_pct}%.",
    })


def run_live_learning(
    emit_fn: Callable[[str, dict], None],
    params: dict,
):
    """Run the full live learning pipeline (investigation → APO → re-investigation).

    This function is called from a background thread. It uses asyncio.run()
    to drive the async pipeline, then returns. The caller (learning_api.py)
    is responsible for putting None in the queue to signal completion.

    Args:
        emit_fn: Callback to emit SSE events (same signature as _emit in learning_api.py).
        params: Dict with optional customer_name, service_tree_id, start_time, end_time.
    """
    from dotenv import load_dotenv
    load_dotenv(os.path.join(_CUSTOMER_AGENT_DIR, ".env"))

    # Windows asyncio fix
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    try:
        asyncio.run(_run_live_pipeline(emit_fn, params))
    except Exception as exc:
        logger.exception("Live learning pipeline failed")
        emit_fn("learning_error", {"error": f"Pipeline failed: {exc}"})
