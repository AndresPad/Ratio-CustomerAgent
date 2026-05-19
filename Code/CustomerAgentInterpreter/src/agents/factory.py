"""
Interpreter Agent Factory — creates MAF Agent instances from agents_config.json.

Mirrors the config-driven pattern from CustomerAgent's agent_factory.py:
  - Loads agent definitions from config/agents/agents_config.json
  - Resolves prompt files from src/prompts/
  - Creates per-agent LLM clients with model overrides
  - Applies OpenAIChatOptions (temperature, response_format, max_completion_tokens)
  - Resolves tool_mode → tool list (e.g. "sandbox" → sandbox tools)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from agent_framework import Agent
from agent_framework.openai import OpenAIChatOptions

from helper.llm import create_chat_client
from sandbox.tools import ALL_SANDBOX_TOOLS

logger = logging.getLogger(__name__)

_CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "agents"
_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


# ─── Tool mode handlers ───────────────────────────────────────────

def _tool_mode_none(agent_cfg: dict) -> list:
    return []


def _tool_mode_sandbox(agent_cfg: dict) -> list:
    return list(ALL_SANDBOX_TOOLS)


TOOL_MODE_HANDLERS: dict[str, Any] = {
    "none": _tool_mode_none,
    "sandbox": _tool_mode_sandbox,
}


def load_config() -> dict[str, Any]:
    """Load and return the agents_config.json."""
    config_path = _CONFIG_DIR / "agents_config.json"
    if not config_path.exists():
        raise FileNotFoundError(f"Agent config not found: {config_path}")
    return json.loads(config_path.read_text(encoding="utf-8"))


def _load_prompt(filename: str) -> str:
    """Load a prompt file from the prompts directory."""
    prompt_path = _PROMPTS_DIR / filename
    if not prompt_path.exists():
        logger.warning("Prompt file not found: %s", prompt_path)
        return ""
    return prompt_path.read_text(encoding="utf-8")


def create_interpreter_agents(config: dict[str, Any] | None = None) -> dict[str, Agent]:
    """Create all Interpreter service agents from config.

    Args:
        config: Optional pre-loaded config dict. If None, loads from disk.

    Returns:
        Dict mapping agent name → Agent instance.
    """
    if config is None:
        config = load_config()

    agents_cfg = config["agents"]

    # Shared default client + per-model cache
    default_client = create_chat_client()
    _client_cache: dict[str, Any] = {}

    agents: dict[str, Agent] = {}

    for agent_cfg in agents_cfg:
        name = agent_cfg["name"]
        description = agent_cfg.get("description", "")
        prompt_file = agent_cfg.get("prompt_file", "")
        instructions = _load_prompt(prompt_file) if prompt_file else f"You are {name}."

        # ── Resolve LLM client (per-agent model or shared default) ──
        agent_model = agent_cfg.get("model")
        if agent_model:
            if agent_model not in _client_cache:
                _client_cache[agent_model] = create_chat_client(model=agent_model)
                logger.info("Created LLM client for model '%s'", agent_model)
            client = _client_cache[agent_model]
        else:
            client = default_client

        # ── Build default_options from config ────────────────────
        options_kwargs: dict[str, Any] = {}
        temperature = agent_cfg.get("temperature")
        if temperature is not None:
            options_kwargs["temperature"] = temperature
        max_completion_tokens = agent_cfg.get("max_completion_tokens")
        if max_completion_tokens is not None:
            options_kwargs["max_completion_tokens"] = max_completion_tokens
        response_format = agent_cfg.get("response_format")
        if response_format:
            options_kwargs["response_format"] = {"type": response_format}

        default_options = OpenAIChatOptions(**options_kwargs) if options_kwargs else None

        # ── Resolve tools from tool_mode ────────────────────────
        tool_mode = agent_cfg.get("tool_mode", "none")
        handler = TOOL_MODE_HANDLERS.get(tool_mode, _tool_mode_none)
        tools = handler(agent_cfg)

        # ── Create Agent instance ────────────────────────────────
        agent_kwargs: dict[str, Any] = {
            "client": client,
            "name": name,
            "description": description,
            "instructions": instructions,
        }
        if default_options:
            agent_kwargs["default_options"] = default_options
        if tools:
            agent_kwargs["tools"] = tools

        agent = Agent(**agent_kwargs)

        agents[name] = agent
        logger.info(
            "Created agent '%s' (model=%s, temperature=%s, response_format=%s, tool_mode=%s, tools=%d)",
            name,
            agent_model or "default",
            temperature,
            response_format or "text",
            tool_mode,
            len(tools),
        )

    logger.info("Interpreter agent factory created %d agents: %s", len(agents), list(agents.keys()))
    return agents
