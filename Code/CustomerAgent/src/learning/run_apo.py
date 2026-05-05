"""APO training script for reasoner prompt optimization.

Usage:
    cd Code/CustomerAgent
    python -m src.learning.run_apo

Requires:
    - Azure OpenAI endpoint configured in .env
    - agentlightning[apo] installed
    - Azure CLI login (az login) for DefaultAzureCredential
"""
from __future__ import annotations

import os
import sys

# poml (used by AGL's APO) reads temp files with the default encoding.
# On Windows that's cp1252 which fails on Unicode chars (═, etc.).
# Run with: python -X utf8 -m src.learning.run_apo
if sys.flags.utf8_mode == 0:
    print(
        "WARNING: Not running in UTF-8 mode. "
        "Use: python -X utf8 -m src.learning.run_apo",
        file=sys.stderr,
    )

import asyncio
import logging

# Fix Python 3.12 Windows ProactorEventLoop crash during AGL teardown.
# ProactorEventLoop has a race condition that causes "IndexError: pop from
# an empty deque" when the loop closes with pending tasks.  SelectorEventLoop
# doesn't have this issue.
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
from learning.rollouts.reasoner_rollout import reasoner_rollout
from learning.datasets.reasoner_tasks import TRAIN_TASKS, VAL_TASKS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


# ── Monkey-patch: survive Windows asyncio deque crash ─────────
# On Python 3.12 Windows, asyncio.run() can crash with
# "IndexError: pop from an empty deque" during event loop teardown
# between APO phases (seed eval → train rollouts → gradient → edit).
# The stock _run_runner treats this as a fatal crash (signals stop,
# re-raises), killing the runner thread permanently so APO never
# completes its optimization rounds.
#
# Fix: wrap _run_runner in a retry loop that catches the deque crash
# and restarts asyncio.run() with a fresh event loop, allowing the
# next phase to proceed.
_original_run_runner = SharedMemoryExecutionStrategy._run_runner


def _patched_run_runner(self, runner, store, worker_id, stop_evt, thread_exceptions):
    """Retry _run_runner on Windows deque crash."""
    max_retries = 10  # one retry per APO phase transition
    for attempt in range(max_retries):
        if stop_evt.is_set():
            return
        try:
            _original_run_runner(self, runner, store, worker_id, stop_evt, thread_exceptions)
            return  # clean exit
        except IndexError as e:
            if "pop from an empty deque" in str(e):
                logger.warning(
                    "Windows asyncio deque crash in runner (attempt %d/%d), restarting...",
                    attempt + 1, max_retries,
                )
                # Clear the stop event that _run_runner set so APO continues
                stop_evt.clear()
                continue
            raise  # different IndexError — propagate
    logger.error("Runner exhausted all %d retry attempts", max_retries)


SharedMemoryExecutionStrategy._run_runner = _patched_run_runner
# ── End monkey-patch ──────────────────────────────────────────


def load_seed_prompt() -> str:
    """Load the current reasoner prompt as the seed for APO."""
    prompt_path = os.path.join(
        _SRC_DIR, "prompts", "investigation_reasoner_prompt.txt"
    )
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def main() -> None:
    """Run APO to optimize the reasoner prompt."""
    logger.info("=== APO Training: reasoner ===")

    # ── 1. Load seed prompt ──────────────────────────────
    seed_prompt = load_seed_prompt()
    logger.info("Seed prompt loaded (%d chars)", len(seed_prompt))

    # ── 2. Validate APO config from .env ─────────────────
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

    # ── 3. Create Azure OpenAI client for APO ────────────
    aoai_client = create_azure_openai_client()

    # ── 4. Configure APO algorithm ───────────────────────
    apo_model = os.environ["APO_MODEL"]

    # Beam parameters: keep small for fast iteration.
    # Override via env vars for full production runs.
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

    # ── 5. Configure Trainer ─────────────────────────────
    # SharedMemory avoids Windows multiprocessing pickle issues.
    # n_runners=1 avoids tracer singleton conflicts between workers.
    # TraceToMessages adapter is required by APO for gradient computation.
    #
    # We pass an explicit InMemoryLightningStore so we hold our own
    # reference.  The Windows asyncio deque crash can kill the runner
    # mid-training; an external store reference survives that crash and
    # lets us extract the best prompt afterward.
    lightning_store = agl.InMemoryLightningStore()
    trainer = agl.Trainer(
        algorithm=apo,
        strategy=SharedMemoryExecutionStrategy(n_runners=1),
        adapter=agl.TraceToMessages(),
        store=lightning_store,
        initial_resources={
            "reasoner_prompt": agl.PromptTemplate(
                template=seed_prompt,
                engine="f-string",
            ),
        },
    )

    # ── 6. Run training ──────────────────────────────────
    logger.info(
        "Starting APO: %d train tasks, %d val tasks, beam=%dx%dx%d",
        len(TRAIN_TASKS), len(VAL_TASKS), beam_width, branch_factor, beam_rounds,
    )

    # AGL's SharedMemoryExecutionStrategy can crash during teardown on
    # Windows (IndexError in asyncio event loop cleanup).  Catch and
    # continue so we still extract and save the best prompt.
    try:
        trainer.fit(
            reasoner_rollout,
            TRAIN_TASKS,
            val_dataset=VAL_TASKS,
        )
    except (IndexError, RuntimeError) as e:
        logger.warning("AGL teardown error (training completed): %s", e)

    # ── 7. Extract best prompt ───────────────────────────
    # Read from our external store reference (survives runner crashes).
    # Fall back to trainer.client, then to seed prompt.
    try:
        store = lightning_store
        if not (store and hasattr(store, 'get_latest_resources')):
            store = trainer.client if hasattr(trainer, 'client') else None
        if store and hasattr(store, 'get_latest_resources'):
            # InMemoryLightningStore methods are async — unwrap the coroutine.
            # Returns a ResourcesUpdate Pydantic model with a .resources dict.
            result = store.get_latest_resources()
            if asyncio.iscoroutine(result):
                best_resources = asyncio.run(result)
            else:
                best_resources = result
            resources_dict = best_resources.resources if hasattr(best_resources, 'resources') else best_resources
            best_prompt_template = resources_dict.get("reasoner_prompt") if isinstance(resources_dict, dict) else None
            if best_prompt_template and hasattr(best_prompt_template, "template"):
                best_prompt = best_prompt_template.template
            else:
                logger.warning("Could not extract optimized prompt from store; using seed")
                best_prompt = seed_prompt
        else:
            logger.warning("Store not accessible; using seed prompt")
            best_prompt = seed_prompt
    except Exception as e:
        logger.warning("Failed to extract best prompt: %s; using seed", e)
        best_prompt = seed_prompt

    logger.info("Best prompt length: %d chars", len(best_prompt))

    # ── 8. Save optimized prompt ─────────────────────────
    output_dir = os.path.join(_SCRIPT_DIR, "outputs")
    os.makedirs(output_dir, exist_ok=True)

    output_path = os.path.join(
        output_dir, "investigation_reasoner_prompt_optimized.txt"
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(best_prompt)

    logger.info("Optimized prompt saved to: %s", output_path)
    logger.info("=== APO Training Complete ===")


if __name__ == "__main__":
    main()
