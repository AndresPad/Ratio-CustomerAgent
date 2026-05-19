---
name: prompt-deploy
description: "Deploy an APO-optimized prompt into production. Validates the optimized prompt against the held-out test set, backs up the original, deploys, and commits with score comparison. Use when asked to deploy, promote, or release an optimized prompt from src/learning/outputs/."
---

# Skill: Deploy an APO-Optimized Prompt

## When to Use

Use this skill when:
- APO training (F08 `run_apo.py`) has completed and produced an optimized prompt in `src/learning/outputs/`
- The user asks to deploy, promote, ship, or release an optimized prompt
- The user asks to compare an optimized prompt against the current production prompt

## Prerequisites

- APO training has completed (`src/learning/outputs/<agent>_prompt_optimized.txt` exists)
- The current production prompt exists in `src/prompts/`
- Azure OpenAI credentials are available (for validation rollouts)

## Steps

### Step 1 — Identify the Prompt Pair

Determine which agent's prompt is being deployed:

| Agent | Production Prompt | Optimized Prompt |
|-------|------------------|------------------|
| reasoner | `src/prompts/investigation_reasoner_prompt.txt` | `src/learning/outputs/investigation_reasoner_prompt_optimized.txt` |

Verify the optimized prompt file exists:

```bash
ls src/learning/outputs/investigation_reasoner_prompt_optimized.txt
```

If it doesn't exist, tell the user to run APO training first:
```bash
cd Code/CustomerAgent
python -m src.learning.run_apo
```

### Step 2 — Review the Diff

Show the user the differences between the current and optimized prompts:

```bash
diff src/prompts/investigation_reasoner_prompt.txt src/learning/outputs/investigation_reasoner_prompt_optimized.txt
```

**Review checklist** — flag any of these issues to the user:
- [ ] **Removed template variables** — `{variable}` placeholders must be preserved (APO Risk #5)
- [ ] **Hallucinated instructions** — references to tools or capabilities the agent doesn't have
- [ ] **Excessive length increase** — if optimized prompt is >2x the original length, warn about token cost
- [ ] **Lost structure** — if the original had sections/headers that the optimized version removed

If any issues are found, **stop and report** — do not proceed with deployment.

### Step 3 — Validate on Held-Out Test Set

Run the rollout on 5 validation tasks with both the old and new prompts to compare scores.

```python
# Run from Code/CustomerAgent/
import os, sys
sys.path.insert(0, 'src')
from dotenv import load_dotenv
load_dotenv('.env')

import agentlightning as agl
from learning.rollouts.reasoner_rollout import reasoner_rollout
from learning.datasets.reasoner_tasks import VAL_TASKS

# Score with CURRENT prompt
current_prompt = open('src/prompts/investigation_reasoner_prompt.txt', encoding='utf-8').read()
current_pt = agl.PromptTemplate(template=current_prompt, engine='f-string')

current_scores = []
for task in VAL_TASKS[:5]:
    score = reasoner_rollout(task=task, prompt_template=current_pt)
    current_scores.append(score)

# Score with OPTIMIZED prompt
optimized_prompt = open('src/learning/outputs/investigation_reasoner_prompt_optimized.txt', encoding='utf-8').read()
optimized_pt = agl.PromptTemplate(template=optimized_prompt, engine='f-string')

optimized_scores = []
for task in VAL_TASKS[:5]:
    score = reasoner_rollout(task=task, prompt_template=optimized_pt)
    optimized_scores.append(score)

current_avg = sum(current_scores) / len(current_scores)
optimized_avg = sum(optimized_scores) / len(optimized_scores)
delta = optimized_avg - current_avg

print(f'Current avg:   {current_avg:.3f}')
print(f'Optimized avg: {optimized_avg:.3f}')
print(f'Delta:         {delta:+.3f}')
print(f'Verdict:       {"DEPLOY" if delta > 0 else "REJECT"}')
```

**Decision gate:**
- If `optimized_avg > current_avg` → proceed to Step 4
- If `optimized_avg <= current_avg` → **stop** and report that the optimized prompt did not improve. Do NOT deploy.

### Step 4 — Backup the Current Prompt

```bash
cp src/prompts/investigation_reasoner_prompt.txt src/prompts/investigation_reasoner_prompt.txt.bak
```

The `.bak` file enables rollback without git operations.

### Step 5 — Deploy the Optimized Prompt

```bash
cp src/learning/outputs/investigation_reasoner_prompt_optimized.txt src/prompts/investigation_reasoner_prompt.txt
```

### Step 6 — Commit with Score Reference

Include the validation scores in the commit message so the improvement is traceable:

```bash
git add src/prompts/investigation_reasoner_prompt.txt
git commit -m "Deploy APO-optimized reasoner prompt (avg reward: X.XXX → Y.YYY, +Z.ZZZ)"
```

Replace `X.XXX`, `Y.YYY`, `Z.ZZZ` with the actual scores from Step 3.

### Step 7 — Post-Deployment Smoke Test

Run one more rollout to confirm the deployed prompt works in-place:

```python
from learning.rollouts.reasoner_rollout import reasoner_rollout
from learning.datasets.reasoner_tasks import VAL_TASKS
import agentlightning as agl

# This loads the DEPLOYED prompt (now in src/prompts/)
seed = open('src/prompts/investigation_reasoner_prompt.txt', encoding='utf-8').read()
pt = agl.PromptTemplate(template=seed, engine='f-string')
score = reasoner_rollout(task=VAL_TASKS[0], prompt_template=pt)
print(f'Post-deploy score: {score:.3f}')
assert score > 0.0, 'Post-deploy smoke test FAILED'
print('Smoke test PASSED')
```

## Rollback Procedure

If the deployed prompt causes issues:

```bash
# Option A: Restore from .bak file
cp src/prompts/investigation_reasoner_prompt.txt.bak src/prompts/investigation_reasoner_prompt.txt
git add src/prompts/investigation_reasoner_prompt.txt
git commit -m "Rollback reasoner prompt to pre-APO version"

# Option B: Restore from git
git checkout HEAD~1 -- src/prompts/investigation_reasoner_prompt.txt
git add src/prompts/investigation_reasoner_prompt.txt
git commit -m "Rollback reasoner prompt to pre-APO version"
```

## Extending to Other Agents

When optimizing additional agents (P1: entity_extractor, outage_analyst, etc.), add rows to the Step 1 table:

| Agent | Production Prompt | Optimized Prompt |
|-------|------------------|------------------|
| entity_extractor | `src/prompts/maf_entity_extractor_prompt.txt` | `src/learning/outputs/maf_entity_extractor_prompt_optimized.txt` |
| outage_analyst | `src/prompts/maf_outage_analyst_prompt.txt` | `src/learning/outputs/maf_outage_analyst_prompt_optimized.txt` |

The workflow (diff → validate → backup → deploy → commit → smoke test) is identical for every agent.
