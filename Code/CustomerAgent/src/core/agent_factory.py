"""
Config-driven MAF Agent factory.

Creates Agent instances from agents_config.json. Each agent gets:
  - A shared AzureOpenAIChatClient
  - Instructions loaded from its prompt file
  - MCP tools resolved via the tool-mode plugin registry
  - Middleware stack assembled from the middleware registry (config-driven order)
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Callable

from agent_framework import Agent
from agent_framework.openai import OpenAIChatOptions

from .middleware.tool_capture_middleware import ToolCallCaptureMiddleware
from .middleware.eval_middleware import OutputEvaluationMiddleware, EVAL_ENABLED
from .middleware.prompt_injection_middleware import PromptInjectionMiddleware, INJECTION_ENABLED
from .middleware.tool_injection_middleware import ToolOutputInjectionMiddleware, TOOL_INJECTION_ENABLED
from .middleware.llm_logging_middleware import LLMLoggingMiddleware, LLM_LOGGING_ENABLED
from .mcp_integration import create_filtered_mcp_tool, create_mcp_tool, validate_mcp_tool_references
from .prompt_loader import load_all_prompts

logger = logging.getLogger(__name__)

_CONFIG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config"))


# ── Tool Mode Plugin Registry ────────────────────────────────────
# Each handler: (agent_cfg, context) → list[tool]
# ``context`` is a dict with shared state (e.g. {"shared_mcp": <MCP instance>}).
# Register custom tool modes via ``register_tool_mode(name, handler)``.

ToolModeHandler = Callable[[dict[str, Any], dict[str, Any]], list]


def _tool_mode_none(agent_cfg: dict[str, Any], ctx: dict[str, Any]) -> list:
    return []


def _tool_mode_filtered(agent_cfg: dict[str, Any], ctx: dict[str, Any]) -> list:
    mcp_tools_list = agent_cfg.get("mcp_tools", [])
    if not mcp_tools_list:
        return []
    name = agent_cfg["name"]
    return [create_filtered_mcp_tool(name, mcp_tools_list)]


def _tool_mode_all(agent_cfg: dict[str, Any], ctx: dict[str, Any]) -> list:
    if ctx.get("shared_mcp") is None:
        ctx["shared_mcp"] = create_mcp_tool(name="ratio-mcp-shared")
    return [ctx["shared_mcp"]]


def _tool_mode_sandbox(agent_cfg: dict[str, Any], ctx: dict[str, Any]) -> list:
    from core.sandbox.tools import (
        execute_python_in_sandbox,
        list_sandbox_files,
        read_sandbox_manifest,
    )
    return [execute_python_in_sandbox, list_sandbox_files, read_sandbox_manifest]


def _tool_mode_fetch_tools(agent_cfg: dict[str, Any], ctx: dict[str, Any]) -> list:
    from core.sandbox.fetch_tools import ALL_FETCH_TOOLS
    return list(ALL_FETCH_TOOLS)


TOOL_MODE_HANDLERS: dict[str, ToolModeHandler] = {
    "none": _tool_mode_none,
    "filtered": _tool_mode_filtered,
    "all": _tool_mode_all,
    "sandbox": _tool_mode_sandbox,
    "fetch_tools": _tool_mode_fetch_tools,
    # "agent_tools" is handled in the post-wiring phase (sub-agent as_tool)
}


def register_tool_mode(name: str, handler: ToolModeHandler) -> None:
    """Register a custom tool-mode handler.

    The handler receives ``(agent_cfg, context)`` and returns a list of tools.
    ``context`` is a mutable dict shared across all agents during creation,
    useful for caching shared resources (e.g. MCP client instances).
    """
    if name in TOOL_MODE_HANDLERS:
        logger.warning("Overwriting existing tool-mode handler '%s'", name)
    TOOL_MODE_HANDLERS[name] = handler
    logger.info("Registered tool-mode handler '%s'", name)


# ── Middleware Registry ───────────────────────────────────────────
# Maps config names to factory functions.
# Factory: (agent_name, shared_instances) → middleware instance | None
# ``shared_instances`` carries singleton middleware objects.

_DEFAULT_MIDDLEWARE_ORDER = [
    "prompt_injection",
    "output_format",
    "tool_capture",
    "tool_injection",
    "eval",
    "llm_logging",
]

MiddlewareFactory = Callable[[str, dict[str, Any], dict[str, Any]], Any | None]


def _mw_prompt_injection(
    agent_name: str, agent_cfg: dict[str, Any], shared: dict[str, Any],
) -> Any | None:
    mw = shared.get("injection_middleware")
    if mw and agent_cfg.get("prompt_injection", False):
        return mw
    return None


def _mw_tool_capture(
    agent_name: str, agent_cfg: dict[str, Any], shared: dict[str, Any],
) -> Any | None:
    # Only attach when the agent has tools (resolved after tool-mode handling)
    if shared.get("_agent_has_tools"):
        return shared.get("capture_middleware")
    return None


def _mw_tool_injection(
    agent_name: str, agent_cfg: dict[str, Any], shared: dict[str, Any],
) -> Any | None:
    # Only attach when the agent has tools and tool injection scanning is enabled
    if shared.get("_agent_has_tools") and agent_cfg.get("prompt_injection", False):
        return shared.get("tool_injection_middleware")
    return None


def _mw_eval(
    agent_name: str, agent_cfg: dict[str, Any], shared: dict[str, Any],
) -> Any | None:
    mw = shared.get("eval_middleware")
    if mw and agent_cfg.get("evaluate", False):
        return mw
    return None


def _mw_llm_logging(
    agent_name: str, agent_cfg: dict[str, Any], shared: dict[str, Any],
) -> Any | None:
    if shared.get("llm_logging_sentinel") and agent_cfg.get("llm_logging", True):
        return LLMLoggingMiddleware(agent_name=agent_name)
    return None


def _mw_output_format(
    agent_name: str, agent_cfg: dict[str, Any], shared: dict[str, Any],
) -> Any | None:
    from .middleware.output_format_middleware import OutputFormatMiddleware
    return OutputFormatMiddleware(agent_name=agent_name)


MIDDLEWARE_REGISTRY: dict[str, MiddlewareFactory] = {
    "prompt_injection": _mw_prompt_injection,
    "output_format": _mw_output_format,
    "tool_capture": _mw_tool_capture,
    "tool_injection": _mw_tool_injection,
    "eval": _mw_eval,
    "llm_logging": _mw_llm_logging,
}


def register_middleware(name: str, factory: MiddlewareFactory) -> None:
    """Register a custom middleware factory.

    The factory receives ``(agent_name, agent_cfg, shared_instances)``
    and returns a middleware instance or ``None`` to skip.
    """
    if name in MIDDLEWARE_REGISTRY:
        logger.warning("Overwriting existing middleware factory '%s'", name)
    MIDDLEWARE_REGISTRY[name] = factory
    logger.info("Registered middleware factory '%s'", name)


def _build_middleware_stack(
    agent_name: str,
    agent_cfg: dict[str, Any],
    shared: dict[str, Any],
) -> list:
    """Assemble the middleware list for a single agent.

    Order comes from the agent's ``"middleware"`` config key (a list of
    registry names).  Falls back to ``_DEFAULT_MIDDLEWARE_ORDER`` when the
    key is absent.
    """
    order = agent_cfg.get("middleware", _DEFAULT_MIDDLEWARE_ORDER)
    mw_list: list = []
    for mw_name in order:
        factory = MIDDLEWARE_REGISTRY.get(mw_name)
        if factory is None:
            logger.warning(
                "Unknown middleware '%s' for agent '%s' — skipping",
                mw_name, agent_name,
            )
            continue
        instance = factory(agent_name, agent_cfg, shared)
        if instance is not None:
            mw_list.append(instance)
    return mw_list


def _topological_sort(agents_cfg: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort agent configs so that sub-agents are created before their coordinators.

    Agents with ``tool_mode: "agent_tools"`` depend on their ``sub_agents``.
    All other agents have no dependencies and appear first.
    If the input already satisfies this ordering (common case), it is returned as-is.
    """
    name_to_cfg = {cfg["name"]: cfg for cfg in agents_cfg}
    # Collect dependency edges: coordinator → sub-agent
    deps: dict[str, list[str]] = {}
    for cfg in agents_cfg:
        if cfg.get("tool_mode") == "agent_tools":
            deps[cfg["name"]] = cfg.get("sub_agents", [])
        else:
            deps[cfg["name"]] = []

    # Kahn's algorithm — we want sub-agents BEFORE coordinators.
    # For each coordinator C with sub-agent S, C depends on S.
    in_degree: dict[str, int] = {name: 0 for name in name_to_cfg}
    for name, subs in deps.items():
        for sub in subs:
            if sub in in_degree:
                in_degree[name] += 1  # C can't be created until S is done

    # Reverse adjacency: sub → list[coordinators that depend on it]
    reverse_adj: dict[str, list[str]] = {name: [] for name in name_to_cfg}
    for name, subs in deps.items():
        for sub in subs:
            if sub in reverse_adj:
                reverse_adj[sub].append(name)

    queue = [name for name, deg in in_degree.items() if deg == 0]
    # Preserve original relative order among zero-degree nodes
    original_order = {cfg["name"]: i for i, cfg in enumerate(agents_cfg)}
    queue.sort(key=lambda n: original_order.get(n, 0))

    ordered: list[str] = []
    while queue:
        node = queue.pop(0)
        ordered.append(node)
        for dependent in reverse_adj[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)
                queue.sort(key=lambda n: original_order.get(n, 0))

    if len(ordered) != len(name_to_cfg):
        # Cycle detected — fall back to original order
        logger.warning(
            "Cycle detected in sub_agents dependencies — using original config order"
        )
        return agents_cfg

    return [name_to_cfg[name] for name in ordered]


def load_config() -> dict[str, Any]:
    """Load and validate agents_config.json."""
    from pydantic import ValidationError
    from core.models.config.agents import AgentsFileConfig

    path = os.path.join(_CONFIG_DIR, "agents", "agents_config.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    try:
        AgentsFileConfig.model_validate(data)
    except ValidationError as exc:
        logger.error("agents_config.json validation failed: %s", exc)
        raise ValueError(
            f"Invalid agents config 'agents_config.json': {exc}"
        ) from exc
    return data


async def create_agents(
    config: dict[str, Any] | None = None,
) -> tuple[dict[str, Agent], ToolCallCaptureMiddleware, OutputEvaluationMiddleware]:
    """Create all MAF Agent instances from config.

    Agents are created in topological order so that coordinators
    (``tool_mode: "agent_tools"``) are created *after* their sub-agents,
    eliminating the need for a recreation pass.

    Args:
        config: Parsed agents_config.json dict. If None, loads from disk.

    Returns:
        Tuple of (agent name → Agent instance dict, shared capture middleware, eval middleware).
    """
    if config is None:
        config = load_config()

    # ── Create chat clients (shared default + per-agent overrides) ──
    from helper.llm import create_chat_client
    default_client = create_chat_client()
    _client_cache: dict[str, Any] = {}  # model name → client, avoids duplicates

    # ── Load prompts ─────────────────────────────────────────
    agents_cfg = config["agents"]
    prompts = load_all_prompts(agents_cfg)

    # ── Validate MCP tool references (C) ─────────────────────
    await validate_mcp_tool_references(agents_cfg)

    # ── Topological sort: sub-agents before coordinators (D) ─
    sorted_cfg = _topological_sort(agents_cfg)

    # ── Tool-mode context dict — shared across all handler invocations ──
    tool_ctx: dict[str, Any] = {"shared_mcp": None}

    # ── Shared middleware for tool-call capture ───────────────
    capture_middleware = ToolCallCaptureMiddleware()

    # ── Shared middleware for output evaluation ───────────────
    eval_middleware = OutputEvaluationMiddleware() if EVAL_ENABLED else None

    # ── Shared middleware for prompt injection detection ──────
    injection_middleware = PromptInjectionMiddleware() if INJECTION_ENABLED else None

    # ── Shared middleware for tool output injection scanning ──
    tool_injection_middleware = ToolOutputInjectionMiddleware() if TOOL_INJECTION_ENABLED else None

    # ── Shared middleware for LLM call logging ────────────────
    # A sentinel instance used for drain()/reset() calls.
    # Per-agent instances (with agent_name) are created by the registry.
    llm_logging_sentinel = LLMLoggingMiddleware() if LLM_LOGGING_ENABLED else None

    # Shared state for the middleware registry builders.
    mw_shared: dict[str, Any] = {
        "capture_middleware": capture_middleware,
        "eval_middleware": eval_middleware,
        "injection_middleware": injection_middleware,
        "tool_injection_middleware": tool_injection_middleware,
        "llm_logging_sentinel": llm_logging_sentinel,
    }

    # ── Build agents (single pass, topologically ordered) ────
    agents: dict[str, Agent] = {}

    for agent_cfg in sorted_cfg:
        name = agent_cfg["name"]
        description = agent_cfg.get("description", "")
        instructions = prompts.get(name, f"You are {name}.")
        if "_prompt_override" in agent_cfg:
            instructions = agent_cfg["_prompt_override"]
        tool_mode = agent_cfg.get("tool_mode", "none")

        # ── Resolve LLM client (per-agent model or shared default) ──
        agent_model = agent_cfg.get("model")
        if agent_model:
            if agent_model not in _client_cache:
                _client_cache[agent_model] = create_chat_client(model=agent_model)
                logger.info("Created LLM client for model '%s'", agent_model)
            client = _client_cache[agent_model]
        else:
            client = default_client

        # ── Build tools list via tool-mode registry ──────────
        if tool_mode == "agent_tools":
            # Sub-agents are already created (topological order guarantees it).
            # Wire them as tools directly — no second pass needed.
            tools = _build_sub_agent_tools(agent_cfg, agents)
        else:
            handler = TOOL_MODE_HANDLERS.get(tool_mode)
            if handler is not None:
                tools = handler(agent_cfg, tool_ctx)
            else:
                logger.warning("Unknown tool_mode '%s' for agent '%s' — defaulting to none", tool_mode, name)
                tools = []

        # ── Build middleware via registry ─────────────────────
        mw_shared["_agent_has_tools"] = bool(tools)
        mw_list = _build_middleware_stack(name, agent_cfg, mw_shared)

        # ── Build default_options from config (temperature, max_completion_tokens, etc.) ──
        default_options = None
        temperature = agent_cfg.get("temperature")
        max_completion_tokens = agent_cfg.get("max_completion_tokens")
        if temperature is not None or max_completion_tokens is not None:
            kwargs = {}
            if temperature is not None:
                kwargs["temperature"] = temperature
            if max_completion_tokens is not None:
                kwargs["max_completion_tokens"] = max_completion_tokens
            default_options = OpenAIChatOptions(**kwargs)

        # GroupChat AgentExecutor manages conversation context via its cache;
        # per-service-call history persistence can cause orphaned tool_calls
        # when a tool execution partially fails between API calls.
        history_persistence = agent_cfg.get("history_persistence", False)

        agent = Agent(
            client=client,
            name=name,
            description=description,
            instructions=instructions,
            tools=tools if tools else None,
            default_options=default_options,
            middleware=mw_list if mw_list else None,
            require_per_service_call_history_persistence=history_persistence,
        )

        agents[name] = agent
        logger.info(
            "Created agent '%s' (tool_mode=%s, tools=%d, middleware=%d)",
            name, tool_mode, len(tools), len(mw_list),
        )

    logger.info("Created %d agents: %s", len(agents), list(agents.keys()))

    return agents, capture_middleware, eval_middleware, injection_middleware, tool_injection_middleware, llm_logging_sentinel, prompts


def _build_sub_agent_tools(
    agent_cfg: dict[str, Any],
    agents: dict[str, Agent],
) -> list:
    """Convert an agent_tools coordinator's sub-agents into FunctionTools via ``as_tool()``.

    Called during the single-pass agent creation loop.  Because agents are
    created in topological order, all sub-agents are guaranteed to exist.
    """
    name = agent_cfg["name"]
    sub_agent_names = agent_cfg.get("sub_agents", [])
    if not sub_agent_names:
        logger.warning("agent_tools agent '%s' has no sub_agents configured", name)
        return []

    tools = []
    for sub_name in sub_agent_names:
        sub_agent = agents.get(sub_name)
        if sub_agent is None:
            logger.warning("Sub-agent '%s' not found for '%s'", sub_name, name)
            continue
        tool = sub_agent.as_tool(
            name=sub_name,
            description=sub_agent.description or f"Run {sub_name} agent",
            arg_name="task",
            arg_description=(
                "A plain natural-language string describing what evidence "
                "to collect. Include ER-IDs, hypothesis context, and "
                "relevant identifiers (service_tree_id, customer_name, etc). "
                "Do NOT pass JSON objects or multiple named parameters."
            ),
            propagate_session=False,
        )
        tools.append(tool)
        logger.info("Attached sub-agent '%s' as tool to '%s'", sub_name, name)

    return tools