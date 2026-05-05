"""
GroupChat orchestrator for MAF Autonomous Agent.

Builds a GroupChatBuilder workflow from config-driven agents.
The orchestrator agent decides who speaks next; all participants
share the full conversation history.
"""
from __future__ import annotations

import logging
from typing import Any

from agent_framework import Agent, AgentResponse, AgentResponseUpdate, Message, WorkflowEvent, WorkflowRunState
from agent_framework.orchestrations import GroupChatBuilder

from .middleware.tool_capture_middleware import ToolCallCaptureMiddleware
from .middleware.eval_middleware import OutputEvaluationMiddleware
from .middleware.prompt_injection_middleware import PromptInjectionMiddleware
from .middleware.llm_logging_middleware import LLMLoggingMiddleware
from helper.agent_logger import AgentLogger, get_current_xcv

logger = logging.getLogger(__name__)


def _drain_tool_calls(
    capture_mw: ToolCallCaptureMiddleware | None,
    agent_name: str,
) -> list[dict[str, Any]]:
    """Drain middleware captures and return as a list of tool_call dicts."""
    if not capture_mw:
        return []
    new_captures = capture_mw.drain()
    if not new_captures:
        return []
    tool_calls = []
    for cap in new_captures:
        tool_calls.append({
            "tool": cap["tool"],
            "query": cap.get("query", ""),
            "arguments": cap.get("arguments", {}),
            "result": cap.get("result"),
            "error": cap.get("error"),
            "duration_ms": cap.get("duration_ms", 0),
            "agent": cap.get("agent", agent_name),
        })
    logger.info(
        "[%s] middleware captures drained: %d", agent_name, len(new_captures)
    )
    return tool_calls


def build_group_chat_workflow(
    agents: dict[str, Agent],
    config: dict[str, Any],
    capture_middleware: ToolCallCaptureMiddleware | None = None,
    eval_middleware: OutputEvaluationMiddleware | None = None,
    injection_middleware: PromptInjectionMiddleware | None = None,
    tool_injection_middleware=None,
    llm_logging_middleware: LLMLoggingMiddleware | None = None,
    agent_prompts: dict[str, str] | None = None,
):
    """Build a GroupChatBuilder workflow from agents and config.

    Args:
        agents: Dict of agent name → Agent instance.
        config: Full agents_config.json dict.

    Returns:
        A Workflow instance ready for .run().
    """
    workflow_cfg = config["workflow"]
    orchestrator_name = workflow_cfg["orchestrator_agent"]
    max_turns = workflow_cfg.get("max_turns", 15)
    termination_keyword = workflow_cfg.get("termination_keyword", "WORKFLOW_COMPLETE")

    # ── Get orchestrator and participants ─────────────────────
    orchestrator = agents[orchestrator_name]

    participant_names = workflow_cfg.get("participants")
    if participant_names:
        # Config-driven: only include explicitly listed agents
        missing = [n for n in participant_names if n not in agents]
        if missing:
            logger.warning("Configured participants not found in agents: %s", missing)
        participants = [
            agents[name] for name in participant_names
            if name in agents and name != orchestrator_name
        ]
    else:
        # Fallback: all agents except orchestrator
        participants = [agent for name, agent in agents.items() if name != orchestrator_name]

    logger.info(
        "Building GroupChat workflow (orchestrator=%s, participants=%s, max_turns=%d)",
        orchestrator_name,
        [a.name for a in participants],
        max_turns,
    )

    # ── Termination condition ────────────────────────────────
    # Stop when:
    # 1. Orchestrator or any agent says WORKFLOW_COMPLETE, OR
    # 2. Total assistant messages exceed max_turns
    def termination_condition(messages: list[Message]) -> bool:
        # Check for termination keyword in last message
        if messages:
            last = messages[-1]
            text = last.text or ""
            if termination_keyword in text:
                logger.info("Termination keyword '%s' detected", termination_keyword)
                return True

        # Check max turns
        assistant_count = sum(1 for m in messages if m.role == "assistant")
        if assistant_count >= max_turns:
            logger.info("Max turns (%d) reached", max_turns)
            return True

        return False

    # ── Build workflow ────────────────────────────────────────
    workflow = (
        GroupChatBuilder(
            participants=participants,
            orchestrator_agent=orchestrator,
            termination_condition=termination_condition,
            intermediate_outputs=True,  # stream each response as it happens
        )
        .build()
    )

    logger.info("GroupChat workflow built successfully")
    return workflow, capture_middleware, eval_middleware, injection_middleware, tool_injection_middleware, llm_logging_middleware, agent_prompts


async def run_workflow_streaming(workflow_and_middleware, query: str):
    """Run the workflow and yield structured events.

    Yields dicts with event information suitable for the UI.

    Args:
        workflow: The built GroupChat Workflow.
        query: User's question.

    Yields:
        Dict events: started, agent_response, handoff, status, final, error.
    """
    # Unpack workflow and middleware
    prompts: dict[str, str] = {}
    tool_injection_mw = None
    if isinstance(workflow_and_middleware, tuple):
        if len(workflow_and_middleware) == 7:
            workflow, capture_mw, eval_mw, injection_mw, tool_injection_mw, llm_log_mw, prompts_dict = workflow_and_middleware
            prompts = prompts_dict or {}
        elif len(workflow_and_middleware) == 6:
            workflow, capture_mw, eval_mw, injection_mw, llm_log_mw, prompts_dict = workflow_and_middleware
            prompts = prompts_dict or {}
        elif len(workflow_and_middleware) == 5:
            workflow, capture_mw, eval_mw, injection_mw, llm_log_mw = workflow_and_middleware
        elif len(workflow_and_middleware) == 4:
            workflow, capture_mw, eval_mw, injection_mw = workflow_and_middleware
            llm_log_mw = None
        elif len(workflow_and_middleware) == 3:
            workflow, capture_mw, eval_mw = workflow_and_middleware
            injection_mw = None
            llm_log_mw = None
        else:
            workflow, capture_mw = workflow_and_middleware
            eval_mw = None
            injection_mw = None
            llm_log_mw = None
    else:
        workflow, capture_mw, eval_mw, injection_mw, tool_injection_mw, llm_log_mw = workflow_and_middleware, None, None, None, None, None

    yield {"type": "started", "query": query}

    # ── Agent logging setup ────────────────────────────────
    xcv = get_current_xcv()
    tracker = AgentLogger.get_instance() if xcv else None

    if tracker and xcv:
        tracker.log_workflow_started(xcv, "GroupChat", list(prompts.keys()) if prompts else [])

    # Reset middleware captures for this request
    if capture_mw:
        capture_mw.reset()
    if eval_mw:
        eval_mw.reset()
    if injection_mw:
        injection_mw.reset()
    if tool_injection_mw:
        tool_injection_mw.reset()
    if llm_log_mw:
        llm_log_mw.reset()

    current_executor = None
    all_outputs: list[dict] = []

    try:
        async for event in workflow.run(query, stream=True):
            event: WorkflowEvent

            if event.type == "output":
                data = event.data

                # Streaming chunk — partial text from an agent
                if isinstance(data, AgentResponseUpdate):
                    agent_name = event.executor_id or "unknown"
                    text = ""
                    if hasattr(data, "text") and data.text:
                        text = data.text

                    # Detect executor change (new agent speaking)
                    if agent_name != current_executor:
                        # ── Drain middleware captures from the PREVIOUS agent ──
                        prev_tool_calls = _drain_tool_calls(
                            capture_mw, current_executor or "unknown"
                        )

                        # ── Drain shield detections from PREVIOUS agent ──
                        prev_injection_detections = injection_mw.drain() if injection_mw else []
                        prev_injection_detections += tool_injection_mw.drain() if tool_injection_mw else []

                        current_executor = agent_name

                        # ── Track agent invocation ──
                        if tracker and xcv:
                            tracker.log_agent_invoked(xcv, agent_name, query)
                            if agent_name in prompts:
                                tracker.log_agent_prompt_used(xcv, agent_name, prompts[agent_name])

                        yield {
                            "type": "agent_start",
                            "agent": agent_name,
                            "previous_tool_calls": prev_tool_calls,
                            **({
                                "previous_prompt_injection": prev_injection_detections
                            } if prev_injection_detections else {}),
                        }

                    if text:
                        yield {
                            "type": "agent_chunk",
                            "agent": agent_name,
                            "text": text,
                        }

                # Full agent response (non-streaming or final)
                elif isinstance(data, AgentResponse):
                    agent_name = event.executor_id or "unknown"
                    messages_text = []

                    # ── Track agent invocation if not already seen ──
                    if agent_name != current_executor:
                        current_executor = agent_name
                        if tracker and xcv:
                            tracker.log_agent_invoked(xcv, agent_name, query)
                            if agent_name in prompts:
                                tracker.log_agent_prompt_used(xcv, agent_name, prompts[agent_name])

                    for msg in data.messages:
                        if msg.text:
                            messages_text.append(msg.text)

                    # ── Drain middleware captures ──
                    tool_calls = _drain_tool_calls(capture_mw, agent_name)

                    logger.info(
                        "[%s] total tool_calls to emit: %d",
                        agent_name, len(tool_calls),
                    )

                    full_text = "\n".join(messages_text)
                    if full_text:
                        output_entry = {
                            "agent": agent_name,
                            "text": full_text,
                        }
                        if tool_calls:
                            output_entry["tool_calls"] = tool_calls

                        # ── Drain evaluation results ──
                        evaluations = eval_mw.drain() if eval_mw else []
                        if evaluations:
                            output_entry["evaluations"] = evaluations

                        # ── Drain injection detections ──
                        injection_detections = injection_mw.drain() if injection_mw else []
                        injection_detections += tool_injection_mw.drain() if tool_injection_mw else []
                        if injection_detections:
                            output_entry["prompt_injection"] = injection_detections

                        # ── Drain LLM call logs ──
                        llm_calls = llm_log_mw.drain() if llm_log_mw else []
                        if llm_calls:
                            output_entry["llm_calls"] = llm_calls

                        all_outputs.append(output_entry)

                        # ── Track agent response ──
                        if tracker and xcv:
                            tracker.log_agent_response(xcv, agent_name, full_text)

                        event_data = {
                            "type": "agent_response",
                            "agent": agent_name,
                            "text": full_text,
                        }
                        if tool_calls:
                            event_data["tool_calls"] = tool_calls
                        if evaluations:
                            event_data["evaluations"] = evaluations
                        if injection_detections:
                            event_data["prompt_injection"] = injection_detections
                        if llm_calls:
                            event_data["llm_calls"] = llm_calls
                        yield event_data

                # Final conversation output (list of Messages)
                elif isinstance(data, list):
                    # ── Drain any remaining middleware captures ──
                    remaining_tool_calls = _drain_tool_calls(
                        capture_mw, current_executor or "unknown"
                    )

                    # GroupChat emits the full conversation as final output
                    final_messages = []
                    for msg in data:
                        if isinstance(msg, Message) and msg.text:
                            speaker = msg.author_name or msg.role
                            final_messages.append({
                                "speaker": speaker,
                                "text": msg.text,
                            })

                    final_event = {
                        "type": "final",
                        "conversation": final_messages,
                        "agent_outputs": all_outputs,
                    }
                    if remaining_tool_calls:
                        final_event["remaining_tool_calls"] = remaining_tool_calls

                    # ── Track final response ──
                    if tracker and xcv:
                        summary = final_messages[-1]["text"] if final_messages else ""
                        tracker.log_final_response(xcv, len(all_outputs), summary)

                    yield final_event

            elif event.type == "status":
                state = event.state
                yield {
                    "type": "status",
                    "state": str(state),
                    "is_idle": state in {
                        WorkflowRunState.IDLE,
                        WorkflowRunState.IDLE_WITH_PENDING_REQUESTS,
                    },
                }

            elif event.type == "request_info":
                # GroupChat orchestrator requesting user input (shouldn't happen
                # in our autonomous flow, but handle gracefully)
                yield {
                    "type": "request_info",
                    "data": str(event.data),
                }

    except Exception as exc:
        logger.exception("Workflow error: %s", exc)
        if tracker and xcv:
            tracker.log_request_end(xcv, status="error", error=str(exc))
        yield {"type": "error", "error": str(exc)}
