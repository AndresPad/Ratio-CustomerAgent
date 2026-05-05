"""Learning API for the CustomerAgentUI.

Provides SSE-streaming investigation → learn → re-investigate cycle
for the Learning Demo view (F23).

Usage: imported by server.py for /api/learning/start endpoint.
"""

import json
import os
import random
import threading
import time
import queue


_event_queue = None
_learning_thread = None
_learning_start_time = None
_event_counter = 0

_PACE_MULTIPLIERS = {
    "fast": 0.3,
    "normal": 1.0,
    "slow": 2.0,
}


def _emit(event_type: str, data: dict):
    """Push an SSE event to the queue."""
    global _event_counter
    if _event_queue is not None:
        data["type"] = event_type
        _event_queue.put(data)
        _event_counter += 1


def _narrate(color: str, text: str):
    """Emit a learning_narration event."""
    _emit("learning_narration", {"color": color, "text": text})


def _emit_count():
    """Emit current event count."""
    _emit("learning_event_count", {"count": _event_counter})


def _sleep(seconds: float, multiplier: float):
    """Sleep scaled by pace multiplier."""
    time.sleep(seconds * multiplier)


# ---------------------------------------------------------------------------
# Narration text per agent (Phase 1)
# ---------------------------------------------------------------------------

_AGENT_NARRATIONS = {
    "entity_extractor": (
        "Entity Extractor normalized 3 entities with {score:.0%} accuracy: "
        "services=[Azure Storage], regions=[East US], customers=[Contoso Ltd]. "
        "Proceeding to symptom analysis."
    ),
    "outage_analyst": (
        "Outage Analyst identified INC-2026-04001 with {score:.0%} confidence. "
        "SLI breach detected: 3 storage accounts exceeded 5-minute downtime "
        "threshold in East US between 14:00\u201316:30 UTC."
    ),
    "airo_analyst": (
        "AIRO Analyst scored {score:.0%} \u2014 SQL query completeness "
        "({completeness:.2f}) is below threshold. The analyst is missing key "
        "correlation fields in its queries."
    ),
    "customer_insights": (
        "Customer Insights correlated 12 support requests to the outage window "
        "with {score:.0%} confidence. Contoso Ltd filed 3 Sev-A cases during "
        "14:15\u201315:00 UTC."
    ),
    "reasoner": (
        "Reasoner evaluated hypothesis HYP-DEP-001: 'Azure Storage control plane "
        "outage is causing cascading failures in East US'. Verdict: CONFIRMED at "
        "{score:.0%} confidence. All three evidence sources converge."
    ),
    "summarizer": (
        "Summarizer produced the final investigation report at {score:.0%} quality. "
        "Sev-1 Azure Storage outage in East US impacted Contoso Ltd. TTM: 2h30m. "
        "12 SRs correlated to root cause."
    ),
}

_REWARD_NARRATIONS = {
    "entity_extractor": (
        "Entity Extractor reward: {reward:.0%}. Entity accuracy ({entity_accuracy:.2f}) "
        "is strong but relevancy ({relevancy:.2f}) leaves room for improvement."
    ),
    "outage_analyst": (
        "Outage Analyst reward: {reward:.0%}. SQL quality ({sql_quality:.2f}) is solid, "
        "completeness ({completeness:.2f}) and relevancy ({relevancy:.2f}) are moderate."
    ),
    "airo_analyst": (
        "AIRO Analyst reward: {reward:.0%}. SQL completeness ({completeness:.2f}) is "
        "the weakest metric \u2014 this agent needs prompt refinement."
    ),
    "customer_insights": (
        "Customer Insights reward: {reward:.0%}. SQL quality ({sql_quality:.2f}) is "
        "acceptable but completeness ({completeness:.2f}) drags the composite down."
    ),
    "reasoner": (
        "Reasoner reward: {reward:.0%}. Verdict accuracy is perfect (1.0) but "
        "reasoning quality ({reasoning_quality:.2f}) is the main bottleneck."
    ),
    "summarizer": (
        "Summarizer reward: {reward:.0%}. Structure ({structure:.2f}) is adequate, "
        "coverage ({coverage:.2f}) needs improvement for comprehensive reports."
    ),
}


# ---------------------------------------------------------------------------
# Demo scenario data — Azure Storage outage in East US
# ---------------------------------------------------------------------------

_INVESTIGATION_STAGES = [
    {
        "stage": 1,
        "label": "Signals",
        "agents": [
            {
                "agent": "entity_extractor",
                "output_summary": (
                    "Normalized entities: services=[Azure Storage], "
                    "regions=[East US], customers=[Contoso Ltd]"
                ),
                "reward": 0.82,
                "breakdown": {"entity_accuracy": 0.90, "relevancy": 0.65},
            },
        ],
    },
    {
        "stage": 2,
        "label": "Symptoms",
        "agents": [
            {
                "agent": "outage_analyst",
                "output_summary": (
                    "Found INC-2026-04001 (Storage control plane outage, "
                    "14:00\u201316:30 UTC). SLI breach: 3 storage accounts "
                    "with >5min downtime."
                ),
                "reward": 0.74,
                "breakdown": {"sql_quality": 0.80, "completeness": 0.62, "relevancy": 0.72},
            },
            {
                "agent": "airo_analyst",
                "output_summary": (
                    "Found INC-2026-04001 (Storage control plane outage, "
                    "14:00\u201316:30 UTC). SLI breach: 3 storage accounts "
                    "with >5min downtime."
                ),
                "reward": 0.68,
                "breakdown": {"sql_quality": 0.72, "completeness": 0.58, "relevancy": 0.70},
            },
        ],
    },
    {
        "stage": 3,
        "label": "Hypotheses",
        "agents": [
            {
                "agent": "customer_insights",
                "output_summary": (
                    "Correlated 12 support requests to outage window. "
                    "Customer Contoso has 3 Sev-A cases filed during "
                    "14:15\u201315:00 UTC."
                ),
                "reward": 0.71,
                "breakdown": {"sql_quality": 0.76, "completeness": 0.60, "relevancy": 0.68},
            },
        ],
    },
    {
        "stage": 4,
        "label": "Evidence",
        "agents": [
            {
                "agent": "reasoner",
                "output_summary": (
                    "Verdict: CONFIRMED. All three evidence sources (SLI breach, "
                    "incident INC-2026-04001, 12 support requests) converge on "
                    "Azure Storage in East US during the outage window."
                ),
                "reward": 0.78,
                "breakdown": {"verdict_accuracy": 1.0, "reasoning_quality": 0.45},
            },
        ],
    },
    {
        "stage": 5,
        "label": "Actions",
        "agents": [
            {
                "agent": "summarizer",
                "output_summary": (
                    "Investigation Report: Sev-1 Azure Storage outage in East US "
                    "impacted Contoso Ltd. TTM: 2h30m. Root cause: storage control "
                    "plane failure. 12 SRs correlated."
                ),
                "reward": 0.70,
                "breakdown": {"structure": 0.75, "coverage": 0.62, "relevancy": 0.68},
            },
        ],
    },
]


def _narration_color(score: float) -> str:
    """Return narration color based on score threshold."""
    if score >= 0.75:
        return "green"
    if score >= 0.60:
        return "orange"
    return "red"


def _run_demo_learning(pace: str = "normal"):
    """Simulate investigation → learn → re-investigate with realistic pacing."""
    global _event_counter
    _event_counter = 0

    mul = _PACE_MULTIPLIERS.get(pace, 1.0)

    # ------------------------------------------------------------------
    # Phase 1 — Investigation
    # ------------------------------------------------------------------
    _emit("learning_started", {
        "scenario": "Azure Storage outage in East US",
        "description": (
            "Investigating a Sev-1 Azure Storage control plane outage "
            "in East US that impacted Contoso Ltd between 14:00-16:30 UTC."
        ),
    })

    _narrate(
        "green",
        "I've begun investigating issues for Contoso Ltd on Azure Storage "
        "in East US. So far, I've collected 22 signals and identified 7 "
        "symptoms including multiple instances of severe SLI breaches, "
        "impacting up to 3 storage accounts in the same region.",
    )

    perAgentRewards = {}

    for stage_def in _INVESTIGATION_STAGES:
        _sleep(2.0 + random.uniform(0, 0.5), mul)

        combinedSummary = "; ".join(
            a["output_summary"] for a in stage_def["agents"]
        )
        _emit("investigation_stage", {
            "stage": stage_def["stage"],
            "agent": stage_def["agents"][0]["agent"],
            "label": stage_def["label"],
            "output_summary": combinedSummary if len(stage_def["agents"]) > 1 else stage_def["agents"][0]["output_summary"],
            "duration_ms": int((2.0 + random.uniform(0, 0.5)) * 1000),
        })

        _sleep(0.3, mul)

        for agentDef in stage_def["agents"]:
            agentName = agentDef["agent"]
            score = agentDef["reward"]

            # Narration after agent invocation
            template = _AGENT_NARRATIONS.get(agentName)
            if template:
                color = _narration_color(score)
                formatArgs = {"score": score}
                formatArgs.update(agentDef["breakdown"])
                _narrate(color, template.format(**formatArgs))

            _sleep(0.3, mul)

            _emit("agent_reward", {
                "stage": stage_def["stage"],
                "agent": agentName,
                "reward": score,
                "breakdown": agentDef["breakdown"],
            })
            perAgentRewards[agentName] = score

            # Narration after reward scored
            rewardTemplate = _REWARD_NARRATIONS.get(agentName)
            if rewardTemplate:
                color = _narration_color(score)
                formatArgs = {"reward": score}
                formatArgs.update(agentDef["breakdown"])
                _narrate(color, rewardTemplate.format(**formatArgs))

            _sleep(0.2, mul)

        _emit_count()

    allRewards = list(perAgentRewards.values())
    totalReward = round(sum(allRewards) / len(allRewards), 4) if allRewards else 0

    _emit("investigation_complete", {
        "total_reward": totalReward,
        "per_agent_rewards": {k: round(v, 4) for k, v in perAgentRewards.items()},
    })

    _narrate(
        "green",
        f"Investigation complete. Composite reward across {len(allRewards)} "
        f"agents: {totalReward:.0%}. All agent outputs captured and scored.",
    )

    # ------------------------------------------------------------------
    # Phase 2 — Learning
    # ------------------------------------------------------------------
    _sleep(1.0, mul)

    agentNames = list(perAgentRewards.keys())
    totalRounds = 2

    _emit("learning_phase_started", {
        "agents": agentNames,
        "total_rounds": totalRounds,
    })

    _narrate(
        "green",
        f"All {len(agentNames)} agent rewards collected. Starting APO prompt "
        f"optimization \u2014 Round 1 of {totalRounds}.",
    )

    currentScores = dict(perAgentRewards)

    for roundNum in range(1, totalRounds + 1):
        _sleep(3.0 + random.uniform(0, 1.0), mul)

        if roundNum == 1:
            improvementRange = (0.05, 0.10)
        else:
            improvementRange = (0.03, 0.06)

        previousScores = dict(currentScores)
        for agent in agentNames:
            delta = random.uniform(*improvementRange)
            currentScores[agent] = min(currentScores[agent] + delta, 0.99)

        _emit("learning_round_complete", {
            "round": roundNum,
            "agent_scores": {k: round(v, 4) for k, v in currentScores.items()},
        })

        # Build per-agent improvement narration
        parts = []
        smallestAgent = None
        smallestDelta = 1.0
        for agent in agentNames:
            oldPct = previousScores[agent]
            newPct = currentScores[agent]
            deltaPct = newPct - oldPct
            pctChange = round(deltaPct / oldPct * 100) if oldPct > 0 else 0
            friendlyName = agent.replace("_", " ").title()
            parts.append(f"{friendlyName}: {oldPct:.2f} \u2192 {newPct:.2f} (+{pctChange}%)")
            if deltaPct < smallestDelta:
                smallestDelta = deltaPct
                smallestAgent = agent

        _narrate(
            "green",
            f"APO Round {roundNum} complete. Textual gradients applied. "
            + ". ".join(parts) + ".",
        )

        if smallestAgent and smallestDelta < 0.06:
            friendlyName = smallestAgent.replace("_", " ").title()
            _narrate(
                "orange",
                f"{friendlyName} showed smallest improvement "
                f"(+{round(smallestDelta / previousScores[smallestAgent] * 100)}%). "
                "Reasoning quality metric remains the bottleneck.",
            )

        _emit_count()

    scoreDeltas = {
        agent: round(currentScores[agent] - perAgentRewards[agent], 4)
        for agent in agentNames
    }

    _emit("learning_complete", {
        "improved_prompts": {
            agent: {
                "original_len": 280 + random.randint(-20, 40),
                "optimized_len": 520 + random.randint(-30, 60),
            }
            for agent in agentNames
        },
        "score_deltas": scoreDeltas,
    })

    _narrate(
        "green",
        "Prompt optimization complete. All agent prompts have been refined "
        "with textual gradient feedback. Preparing re-investigation.",
    )

    # ------------------------------------------------------------------
    # Phase 3 — Re-Investigation
    # ------------------------------------------------------------------
    _sleep(1.0, mul)

    _narrate(
        "green",
        "Re-running investigation with optimized prompts. Same scenario: "
        "Contoso Ltd, Azure Storage, East US.",
    )

    reInvestigationScores = {}
    for stage_def in _INVESTIGATION_STAGES:
        _sleep(1.5 + random.uniform(0, 0.5), mul)

        for agentDef in stage_def["agents"]:
            agentName = agentDef["agent"]
            oldScore = agentDef["reward"]
            improvement = random.uniform(0.10, 0.20)
            newScore = min(oldScore + improvement, 0.99)
            reInvestigationScores[agentName] = newScore

            _emit("reinvestigation_stage", {
                "stage": stage_def["stage"],
                "agent": agentName,
                "label": stage_def["label"],
                "output_summary": agentDef["output_summary"],
                "old_score": round(oldScore, 4),
                "new_score": round(newScore, 4),
            })

            friendlyName = agentName.replace("_", " ").title()
            color = _narration_color(newScore)
            _narrate(
                color,
                f"{friendlyName} re-scored: {oldScore:.0%} \u2192 {newScore:.0%} "
                f"(+{round((newScore - oldScore) / oldScore * 100)}%). "
                f"{'Exceeds target.' if newScore >= 0.85 else 'Improved but still below 85% target.'}",
            )

        _emit_count()

    _sleep(0.5, mul)

    improvements = {}
    for stage_def in _INVESTIGATION_STAGES:
        for agentDef in stage_def["agents"]:
            agentName = agentDef["agent"]
            before = agentDef["reward"]
            after = reInvestigationScores.get(agentName, min(before + random.uniform(0.10, 0.20), 0.99))
            improvements[agentName] = {
                "before": round(before, 4),
                "after": round(after, 4),
                "delta": round(after - before, 4),
            }

    _emit("reinvestigation_complete", {
        "improvements": improvements,
    })

    # Composite improvement narration
    deltas = [v["delta"] for v in improvements.values()]
    compositePct = round(sum(deltas) / len(deltas) * 100) if deltas else 0
    detailParts = []
    for agent, vals in improvements.items():
        friendlyName = agent.replace("_", " ").title()
        detailParts.append(f"{friendlyName}: {vals['before']:.0%} \u2192 {vals['after']:.0%}")
    _narrate(
        "green",
        f"Re-investigation complete. Composite improvement: +{compositePct}%. "
        + ". ".join(detailParts) + ".",
    )

    _emit_count()
    _event_queue.put(None)


def handle_learning_request(handler, body: bytes):
    """Handle POST /api/learning/start — stream SSE events.

    Params (JSON body):
        mode: "demo" (default) or "live"
        pace: "fast", "normal" (default), "slow" — only used in demo mode
        customer_name: str — used in live mode
        service_tree_id: str — used in live mode
        start_time: str — used in live mode
        end_time: str — used in live mode
    """
    global _event_queue, _learning_thread, _learning_start_time, _event_counter

    try:
        params = json.loads(body) if body else {}
    except json.JSONDecodeError:
        params = {}

    mode = params.get("mode", "demo")
    pace = params.get("pace", "normal")
    if pace not in _PACE_MULTIPLIERS:
        pace = "normal"

    _event_queue = queue.Queue()
    _event_counter = 0
    _learning_start_time = time.time()

    if mode == "live":
        from learning_live import run_live_learning
        target = lambda: _run_live_wrapper(run_live_learning, params)
    else:
        target = lambda: _run_demo_learning(pace)

    _learning_thread = threading.Thread(
        target=target,
        daemon=True,
    )
    _learning_thread.start()

    _stream_sse(handler)


def _run_live_wrapper(run_live_fn, params):
    """Wrapper that calls run_live_learning and puts None sentinel when done."""
    try:
        run_live_fn(_emit, params)
    except Exception as e:
        _emit("learning_error", {"error": f"Live learning failed: {e}"})
    finally:
        if _event_queue is not None:
            _event_queue.put(None)


def _stream_sse(handler):
    """Stream SSE events from the queue to the HTTP response."""
    handler.send_response(200)
    handler.send_header("Content-Type", "text/event-stream")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Accel-Buffering", "no")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type")
    handler.end_headers()

    try:
        while True:
            try:
                event = _event_queue.get(timeout=30)
            except queue.Empty:
                handler.wfile.write(b": keepalive\n\n")
                handler.wfile.flush()
                continue

            if event is None:
                handler.wfile.write(b"data: [DONE]\n\n")
                handler.wfile.flush()
                break

            line = f"data: {json.dumps(event)}\n\n"
            handler.wfile.write(line.encode())
            handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        pass
