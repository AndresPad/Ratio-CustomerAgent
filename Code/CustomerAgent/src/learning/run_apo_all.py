"""Multi-agent APO training orchestrator.

Runs APO sequentially for all target agents in dependency order,
using previously-optimized prompts for upstream agents.

Usage:
    cd Code/CustomerAgent
    python -X utf8 -m src.learning.run_apo_all
    python -X utf8 -m src.learning.run_apo_all --agents entity_extractor,summarizer
    python -X utf8 -m src.learning.run_apo_all --skip-to customer_insights
"""
from __future__ import annotations

import argparse
import asyncio
import importlib
import logging
import os
import sys
import time

# poml (used by AGL's APO) reads temp files with the default encoding.
# On Windows that's cp1252 which fails on Unicode chars.
# Run with: python -X utf8 -m src.learning.run_apo_all
if sys.flags.utf8_mode == 0:
    print(
        "WARNING: Not running in UTF-8 mode. "
        "Use: python -X utf8 -m src.learning.run_apo_all",
        file=sys.stderr,
    )

# Fix Python 3.12 Windows ProactorEventLoop crash during AGL teardown.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from dotenv import load_dotenv
import agentlightning as agl
from agentlightning.execution import SharedMemoryExecutionStrategy

# Add project paths for service-local imports
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_CUSTOMER_AGENT_DIR = os.path.normpath(os.path.join(_SCRIPT_DIR, "..", ".."))
_SRC_DIR = os.path.join(_CUSTOMER_AGENT_DIR, "src")

if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
if _CUSTOMER_AGENT_DIR not in sys.path:
    sys.path.insert(0, _CUSTOMER_AGENT_DIR)

# Load .env from Code/CustomerAgent/.env
load_dotenv(os.path.join(_CUSTOMER_AGENT_DIR, ".env"))

from learning.apo_client import create_azure_openai_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Monkey-patch: survive Windows asyncio deque crash ─────────
_original_run_runner = SharedMemoryExecutionStrategy._run_runner


def _patched_run_runner(self, runner, store, worker_id, stop_evt, thread_exceptions):
    """Retry _run_runner on Windows deque crash."""
    max_retries = 10
    for attempt in range(max_retries):
        if stop_evt.is_set():
            return
        try:
            _original_run_runner(self, runner, store, worker_id, stop_evt, thread_exceptions)
            return
        except IndexError as e:
            if "pop from an empty deque" in str(e):
                logger.warning(
                    "Windows asyncio deque crash in runner (attempt %d/%d), restarting...",
                    attempt + 1, max_retries,
                )
                stop_evt.clear()
                continue
            raise
    logger.error("Runner exhausted all %d retry attempts", max_retries)


SharedMemoryExecutionStrategy._run_runner = _patched_run_runner
# ── End monkey-patch ──────────────────────────────────────────


# ── Agent optimization order and config ───────────────────────

OPTIMIZATION_ORDER = [
    "entity_extractor",
    "outage_analyst",
    "airo_analyst",
    "customer_insights",
    "summarizer",
]

AGENT_CONFIG = {
    "entity_extractor": {
        "prompt_file": "maf_entity_extractor_prompt.txt",
        "rollout_module": "learning.rollouts.entity_extractor_rollout",
        "rollout_func": "entity_extractor_rollout",
        "dataset_module": "learning.datasets.entity_extractor_tasks",
        "resource_key": "entity_extractor_prompt",
    },
    "outage_analyst": {
        "prompt_file": "maf_outage_analyst_prompt.txt",
        "rollout_module": "learning.rollouts.outage_analyst_rollout",
        "rollout_func": "outage_analyst_rollout",
        "dataset_module": "learning.datasets.outage_analyst_tasks",
        "resource_key": "outage_analyst_prompt",
    },
    "airo_analyst": {
        "prompt_file": "maf_airo_analyst_prompt.txt",
        "rollout_module": "learning.rollouts.airo_analyst_rollout",
        "rollout_func": "airo_analyst_rollout",
        "dataset_module": "learning.datasets.airo_analyst_tasks",
        "resource_key": "airo_analyst_prompt",
    },
    "customer_insights": {
        "prompt_file": "maf_customer_insights_prompt.txt",
        "rollout_module": "learning.rollouts.customer_insights_rollout",
        "rollout_func": "customer_insights_rollout",
        "dataset_module": "learning.datasets.customer_insights_tasks",
        "resource_key": "customer_insights_prompt",
    },
    "summarizer": {
        "prompt_file": "maf_summarizer_prompt.txt",
        "rollout_module": "learning.rollouts.summarizer_rollout",
        "rollout_func": "summarizer_rollout",
        "dataset_module": "learning.datasets.summarizer_tasks",
        "resource_key": "summarizer_prompt",
    },
}


def _load_seed_prompt(prompt_file: str) -> str:
    """Load a seed prompt from src/prompts/."""
    prompt_path = os.path.join(_SRC_DIR, "prompts", prompt_file)
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def _extract_best_prompt(
    lightning_store: agl.InMemoryLightningStore,
    trainer: agl.Trainer,
    resource_key: str,
    seed_prompt: str,
) -> str:
    """Extract the best prompt from the store, falling back to seed."""
    try:
        store = lightning_store
        if not (store and hasattr(store, "get_latest_resources")):
            store = trainer.client if hasattr(trainer, "client") else None
        if store and hasattr(store, "get_latest_resources"):
            result = store.get_latest_resources()
            if asyncio.iscoroutine(result):
                best_resources = asyncio.run(result)
            else:
                best_resources = result
            resources_dict = (
                best_resources.resources
                if hasattr(best_resources, "resources")
                else best_resources
            )
            best_template = (
                resources_dict.get(resource_key)
                if isinstance(resources_dict, dict)
                else None
            )
            if best_template and hasattr(best_template, "template"):
                return best_template.template
            logger.warning("Could not extract optimized prompt for %s; using seed", resource_key)
            return seed_prompt
        logger.warning("Store not accessible for %s; using seed", resource_key)
        return seed_prompt
    except Exception as e:
        logger.warning("Failed to extract best prompt for %s: %s; using seed", resource_key, e)
        return seed_prompt


def _optimize_agent(agent_name: str, config: dict) -> dict:
    """Run APO for a single agent. Returns a result dict."""
    logger.info("=" * 60)
    logger.info("=== APO Training: %s ===", agent_name)
    logger.info("=" * 60)

    start_time = time.time()

    # 1. Load seed prompt
    seed_prompt = _load_seed_prompt(config["prompt_file"])
    logger.info("[%s] Seed prompt loaded (%d chars)", agent_name, len(seed_prompt))

    # 2. Dynamically import rollout function and dataset
    rollout_mod = importlib.import_module(config["rollout_module"])
    rollout_func = getattr(rollout_mod, config["rollout_func"])

    dataset_mod = importlib.import_module(config["dataset_module"])
    train_tasks = dataset_mod.TRAIN_TASKS
    val_tasks = dataset_mod.VAL_TASKS

    logger.info(
        "[%s] Loaded %d train tasks, %d val tasks",
        agent_name, len(train_tasks), len(val_tasks),
    )

    # 3. Create Azure OpenAI client
    aoai_client = create_azure_openai_client()

    # 4. Configure APO
    apo_model = os.environ["APO_MODEL"]
    beam_width = int(os.getenv("APO_BEAM_WIDTH", "2"))
    branch_factor = int(os.getenv("APO_BRANCH_FACTOR", "2"))
    beam_rounds = int(os.getenv("APO_BEAM_ROUNDS", "1"))
    gradient_batch_size = int(os.getenv("APO_GRADIENT_BATCH_SIZE", "4"))
    val_batch_size = int(os.getenv("APO_VAL_BATCH_SIZE", "4"))

    apo = agl.APO(
        async_openai_client=aoai_client,
        gradient_model=apo_model,
        apply_edit_model=apo_model,
        beam_width=beam_width,
        branch_factor=branch_factor,
        beam_rounds=beam_rounds,
        gradient_batch_size=gradient_batch_size,
        val_batch_size=val_batch_size,
        rollout_batch_timeout=600.0,
    )

    # 5. Configure Trainer with external InMemoryLightningStore
    resource_key = config["resource_key"]
    lightning_store = agl.InMemoryLightningStore()
    trainer = agl.Trainer(
        algorithm=apo,
        strategy=SharedMemoryExecutionStrategy(n_runners=1),
        adapter=agl.TraceToMessages(),
        store=lightning_store,
        initial_resources={
            resource_key: agl.PromptTemplate(
                template=seed_prompt,
                engine="f-string",
            ),
        },
    )

    # 6. Run training
    logger.info(
        "[%s] Starting APO: beam=%dx%dx%d",
        agent_name, beam_width, branch_factor, beam_rounds,
    )
    try:
        trainer.fit(rollout_func, train_tasks, val_dataset=val_tasks)
    except (IndexError, RuntimeError) as e:
        logger.warning("[%s] AGL teardown error (training completed): %s", agent_name, e)

    # 7. Extract best prompt
    best_prompt = _extract_best_prompt(lightning_store, trainer, resource_key, seed_prompt)
    logger.info("[%s] Best prompt length: %d chars", agent_name, len(best_prompt))

    # 8. Save optimized prompt
    output_dir = os.path.join(_SCRIPT_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)
    prompt_base = os.path.splitext(config["prompt_file"])[0]
    output_path = os.path.join(output_dir, f"{prompt_base}_optimized.txt")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(best_prompt)
    logger.info("[%s] Optimized prompt saved to: %s", agent_name, output_path)

    elapsed = time.time() - start_time
    changed = best_prompt != seed_prompt

    return {
        "agent": agent_name,
        "seed_len": len(seed_prompt),
        "optimized_len": len(best_prompt),
        "changed": changed,
        "output_path": output_path,
        "elapsed_s": elapsed,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run APO sequentially for multiple agents in dependency order.",
    )
    parser.add_argument(
        "--agents",
        type=str,
        default=None,
        help="Comma-separated list of agents to optimize (default: all in order)",
    )
    parser.add_argument(
        "--skip-to",
        type=str,
        default=None,
        help="Skip agents before this one and resume from it",
    )
    return parser.parse_args()


def main() -> None:
    """Run APO for all target agents in dependency order."""

    # Validate APO config
    required_vars = ["APO_AZURE_OPENAI_ENDPOINT", "APO_AZURE_OPENAI_API_VERSION", "APO_MODEL"]
    missing = [v for v in required_vars if not os.getenv(v)]
    if missing:
        raise SystemExit(f"Missing required .env variables: {', '.join(missing)}")

    logger.info(
        "APO config: endpoint=%s, model=%s, api_version=%s",
        os.environ["APO_AZURE_OPENAI_ENDPOINT"],
        os.environ["APO_MODEL"],
        os.environ["APO_AZURE_OPENAI_API_VERSION"],
    )

    args = _parse_args()

    # Determine which agents to optimize
    if args.agents:
        requested = [a.strip() for a in args.agents.split(",")]
        invalid = [a for a in requested if a not in AGENT_CONFIG]
        if invalid:
            raise SystemExit(f"Unknown agent(s): {', '.join(invalid)}. Valid: {', '.join(OPTIMIZATION_ORDER)}")
        agents_to_run = [a for a in OPTIMIZATION_ORDER if a in requested]
    else:
        agents_to_run = list(OPTIMIZATION_ORDER)

    # Apply --skip-to
    if args.skip_to:
        if args.skip_to not in AGENT_CONFIG:
            raise SystemExit(f"Unknown agent: {args.skip_to}. Valid: {', '.join(OPTIMIZATION_ORDER)}")
        try:
            skip_idx = agents_to_run.index(args.skip_to)
            skipped = agents_to_run[:skip_idx]
            agents_to_run = agents_to_run[skip_idx:]
            if skipped:
                logger.info("Skipping agents: %s", ", ".join(skipped))
        except ValueError:
            raise SystemExit(f"Agent '{args.skip_to}' is not in the current run list")

    logger.info("=" * 60)
    logger.info("=== Multi-Agent APO Orchestrator ===")
    logger.info("Agents to optimize: %s", " -> ".join(agents_to_run))
    logger.info("=" * 60)

    total_start = time.time()
    results: list[dict] = []

    for agent_name in agents_to_run:
        config = AGENT_CONFIG[agent_name]
        result = _optimize_agent(agent_name, config)
        results.append(result)
        logger.info(
            "[%s] Completed in %.1fs | changed=%s | %d -> %d chars",
            agent_name,
            result["elapsed_s"],
            result["changed"],
            result["seed_len"],
            result["optimized_len"],
        )

    total_elapsed = time.time() - total_start

    # Print summary table
    print("\n" + "=" * 72)
    print("  MULTI-AGENT APO OPTIMIZATION SUMMARY")
    print("=" * 72)
    print(f"  {'Agent':<22} {'Seed':<8} {'Optimized':<10} {'Changed':<9} {'Time':<8}")
    print("-" * 72)
    for r in results:
        mins = int(r["elapsed_s"] // 60)
        secs = int(r["elapsed_s"] % 60)
        print(
            f"  {r['agent']:<22} {r['seed_len']:<8} {r['optimized_len']:<10} "
            f"{'YES' if r['changed'] else 'no':<9} {mins}m{secs:02d}s"
        )
    print("-" * 72)
    total_mins = int(total_elapsed // 60)
    total_secs = int(total_elapsed % 60)
    changed_count = sum(1 for r in results if r["changed"])
    print(f"  Total: {len(results)} agents | {changed_count} improved | {total_mins}m{total_secs:02d}s")
    print("=" * 72)

    logger.info("=== Multi-Agent APO Training Complete ===")


if __name__ == "__main__":
    main()
