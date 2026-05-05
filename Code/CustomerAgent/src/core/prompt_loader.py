"""
Prompt loader for MAF GroupChat agents.

Loads agent instruction prompts from local text files in the prompts/ directory.
Optionally appends shared knowledge documents from the knowledge/ directory.
"""
from __future__ import annotations

import json
import logging
import os

from pydantic import ValidationError

from helper.agent_logger import AgentLogger

logger = logging.getLogger(__name__)

_PROMPTS_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "prompts"))
_KNOWLEDGE_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "knowledge"))
_CONFIG_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "config"))


def load_prompt(prompt_file: str) -> str:
    """Load a prompt text file from the prompts directory.

    Args:
        prompt_file: Filename (e.g., 'maf_orchestrator_prompt.txt').

    Returns:
        Prompt text content.

    Raises:
        FileNotFoundError: If prompt file doesn't exist.
    """
    path = os.path.join(_PROMPTS_DIR, prompt_file)
    if not os.path.isfile(path):
        raise FileNotFoundError(f"Prompt file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        text = f.read().strip()

    logger.info("Loaded prompt '%s' (%d chars)", prompt_file, len(text))
    return text


def _load_knowledge(filenames: list[str]) -> str:
    """Load and concatenate knowledge files from the knowledge/ directory."""
    parts: list[str] = []
    for name in filenames:
        path = os.path.join(_KNOWLEDGE_DIR, name)
        if not os.path.isfile(path):
            logger.warning("Knowledge file not found: %s", path)
            continue
        with open(path, "r", encoding="utf-8") as f:
            parts.append(f.read().strip())
        logger.info("Loaded knowledge '%s'", name)
    return "\n\n".join(parts)


def _resolve_template_vars(prompt_text: str) -> str:
    """Replace known {{VARIABLE}} placeholders with loaded config data."""
    if "{{ACTION_CATALOG}}" in prompt_text:
        from core.models.config.action_catalog import ActionCatalogFileConfig

        catalog_path = os.path.join(_CONFIG_DIR, "actions", "action_catalog.json")
        if os.path.isfile(catalog_path):
            with open(catalog_path, "r", encoding="utf-8") as f:
                catalog = json.load(f)
            # Validate schema at load time
            try:
                ActionCatalogFileConfig.model_validate(catalog)
            except ValidationError as exc:
                logger.error("Action catalog config validation failed: %s", exc)
                raise ValueError(
                    f"Invalid action catalog config: {exc}"
                ) from exc
            prompt_text = prompt_text.replace(
                "{{ACTION_CATALOG}}", json.dumps(catalog, indent=2)
            )
            logger.info("Injected ACTION_CATALOG (%d actions)", len(catalog.get("actions", [])))
        else:
            logger.warning("Action catalog not found: %s", catalog_path)

    if "{{VALID_HYPOTHESIS_IDS}}" in prompt_text:
        prompt_text = prompt_text.replace(
            "{{VALID_HYPOTHESIS_IDS}}", _load_valid_hypothesis_ids()
        )

    if "{{EVIDENCE_REQUIREMENTS_REFERENCE}}" in prompt_text:
        prompt_text = prompt_text.replace(
            "{{EVIDENCE_REQUIREMENTS_REFERENCE}}", _load_evidence_requirements_reference()
        )

    if "{{FETCH_TOOLS_REFERENCE}}" in prompt_text:
        prompt_text = prompt_text.replace(
            "{{FETCH_TOOLS_REFERENCE}}", _load_fetch_tools_reference()
        )

    return prompt_text


def _load_valid_hypothesis_ids() -> str:
    """Load all hypothesis IDs from the hypothesis catalog JSON files.

    Scans config/hypotheses/*.json and builds a formatted list grouped by
    category. This keeps prompts in sync with the catalog automatically —
    no manual updates when hypotheses are added or removed.
    """
    hyp_dir = os.path.join(_CONFIG_DIR, "hypotheses")
    if not os.path.isdir(hyp_dir):
        logger.warning("Hypotheses directory not found: %s", hyp_dir)
        return "(hypothesis catalog not found)"

    by_category: dict[str, list[str]] = {}
    total = 0
    # Skip non-hypothesis files: scoring_config is tuning params,
    # investigation_hypotheses is a reference doc with a different schema.
    _SKIP_FILES = {"scoring_config.json", "investigation_hypotheses.json"}
    for filename in sorted(os.listdir(hyp_dir)):
        if not filename.endswith(".json") or filename in _SKIP_FILES:
            continue
        filepath = os.path.join(hyp_dir, filename)
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Validate schema at load time
            from core.models.config.hypothesis_template import HypothesisFileConfig
            try:
                HypothesisFileConfig.model_validate(data)
            except ValidationError as exc:
                logger.error(
                    "Hypothesis config validation failed for %s: %s", filename, exc,
                )
                raise ValueError(
                    f"Invalid hypothesis config '{filename}': {exc}"
                ) from exc
            for hyp in data.get("hypotheses", []):
                hid = hyp.get("id", "")
                cat = hyp.get("category", "unknown")
                if hid:
                    by_category.setdefault(cat, []).append(hid)
                    total += 1
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load hypothesis file %s: %s", filename, exc)

    # Format one ID per line to reduce LLM hallucination risk
    # (comma-separated lists on a single line get silently truncated)
    lines: list[str] = []
    for cat in sorted(by_category):
        lines.append(f"  [{cat.upper()}]")
        for hid in by_category[cat]:
            lines.append(f"    - {hid}")
    lines.append(f"  TOTAL: {total} hypothesis IDs. This list is EXHAUSTIVE — every ID above is valid.")

    logger.info("Injected VALID_HYPOTHESIS_IDS (%d hypotheses from %s)",
                total, hyp_dir)
    return "\n".join(lines)


def _load_fetch_tools_reference() -> str:
    """Generate the TOOLS section for data_fetcher from fetch_tools_config.json.

    Produces a numbered list of fetch tools with their signatures, output files,
    and an ER-ID → tool mapping table — all derived from the config.
    """
    config_path = os.path.join(_CONFIG_DIR, "fetch_tools_config.json")
    if not os.path.isfile(config_path):
        logger.warning("fetch_tools_config.json not found: %s", config_path)
        return "(fetch tools config not found)"

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load fetch_tools_config.json: %s", exc)
        return "(fetch tools config failed to load)"

    fetch_tools = data.get("fetch_tools", {})
    lines: list[str] = []
    er_mapping_lines: list[str] = []

    for idx, (tool_name, tool_cfg) in enumerate(fetch_tools.items(), start=1):
        mcp_calls = tool_cfg.get("mcp_calls", [])
        # Build parameter list (union of all mcp_call params, strip '?' suffix for display)
        all_params: list[str] = []
        for call in mcp_calls:
            for p in call.get("params", []):
                clean = p.rstrip("?")
                if clean not in all_params:
                    all_params.append(clean)
        signature = f"{tool_name}({', '.join(all_params)})"

        # Build output file list
        evidence_subdir = data.get("subdirs", {}).get("evidence", "evidence")
        output_files = [f"/mnt/data/{{xcv}}/{evidence_subdir}/{call['output_file']}" for call in mcp_calls]

        lines.append(f"{idx}. {signature}")
        lines.append(f"   → Writes: {', '.join(output_files)}")
        lines.append(f"   → Returns: manifest with paths, row counts, schemas")
        lines.append("")

        # ER-ID mapping
        er_patterns = tool_cfg.get("er_patterns", [])
        if er_patterns:
            er_mapping_lines.append(
                f"   - {', '.join(er_patterns):20s} → {tool_name}"
            )

    # Append ER-ID → tool mapping
    if er_mapping_lines:
        lines.append("Map ER-IDs to tools:")
        lines.extend(er_mapping_lines)

    logger.info("Injected FETCH_TOOLS_REFERENCE (%d tools)", len(fetch_tools))
    return "\n".join(lines)


def _load_evidence_requirements_reference() -> str:
    """Load evidence requirements from evidence_requirements.json.

    Builds a formatted reference listing all available ERs with short descriptions.
    This keeps the evidence_planner prompt in sync with the catalog automatically.
    """
    er_path = os.path.join(_CONFIG_DIR, "evidence", "evidence_requirements.json")
    if not os.path.isfile(er_path):
        logger.warning("Evidence requirements file not found: %s", er_path)
        return "(evidence requirements catalog not found)"

    try:
        with open(er_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to load evidence requirements: %s", exc)
        return "(evidence requirements failed to load)"

    # Validate schema at load time
    from core.models.config.evidence_requirement import EvidenceFileConfig
    try:
        EvidenceFileConfig.model_validate(data)
    except ValidationError as exc:
        logger.error("Evidence requirements config validation failed: %s", exc)
        raise ValueError(
            f"Invalid evidence config 'evidence_requirements.json': {exc}"
        ) from exc

    lines: list[str] = []
    lines.append("Available Evidence Requirements:")

    for er in data.get("evidence_requirements", []):
        er_id = er.get("id", "")
        desc = er.get("description", "")
        # Extract first sentence as short description
        short_desc = desc.split(".")[0] if desc else ""
        if short_desc and len(short_desc) > 60:
            short_desc = short_desc[:57] + "..."
        category = er.get("category_tag", "")
        lines.append(f"  {er_id:13s} [{category:10s}] {short_desc}")

    logger.info(
        "Injected EVIDENCE_REQUIREMENTS_REFERENCE (%d ERs)",
        len(data.get("evidence_requirements", [])),
    )
    return "\n".join(lines)


def load_all_prompts(agents_config: list[dict]) -> dict[str, str]:
    """Load prompts for all agents defined in config.

    Args:
        agents_config: List of agent dicts from agents_config.json.

    Returns:
        Dict mapping agent name → prompt text.
    """
    prompts: dict[str, str] = {}
    for agent_cfg in agents_config:
        name = agent_cfg["name"]
        prompt_file = agent_cfg.get("prompt_file", "")
        if prompt_file:
            prompt_text = load_prompt(prompt_file)
            prompt_text = _resolve_template_vars(prompt_text)
        else:
            logger.warning("Agent '%s' has no prompt_file configured", name)
            prompt_text = f"You are {name}."

        # Append shared guideline documents if configured
        knowledge_files = agent_cfg.get("knowledge", [])
        if knowledge_files:
            knowledge_text = _load_knowledge(knowledge_files)
            if knowledge_text:
                prompt_text = f"{prompt_text}\n\n{knowledge_text}"

        prompts[name] = prompt_text

        # Log prompt to Application Insights
        tracker = AgentLogger.get_instance()
        tracker.log_prompt_loaded(name, prompt_file or "(default)", prompt_text)

    # Flush startup events so PromptLoaded records reach App Insights immediately
    tracker.flush()

    return prompts
