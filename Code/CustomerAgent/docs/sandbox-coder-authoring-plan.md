# Sandbox Coder: Code Authoring Upgrade — Implementation Plan

**Date:** 2026-04-29
**Status:** Draft
**Scope:** RATIO-AI / Code/CustomerAgent

---

## 1. Executive Summary

The `sandbox_coder` agent is currently a **pure code executor** — it receives code or a task and immediately pipes it to `execute_python_in_sandbox`. We are upgrading it to be a **code author AND executor**: given a reasoner's question and collected evidence data, it will **write** a Python analysis script, **execute** it in the sandbox, and **return** structured analytical results.

This is a single-agent change — no new agents are created. The upgrade touches 4 files:

| File | Change Type |
|------|-------------|
| `maf_sandbox_coder_prompt.txt` | **Rewrite** — new dual-role prompt |
| `investigation_evidence_planner_prompt.txt` | **Major edit** — remove hello-world warmup, add post-collection analysis step |
| `maf_orchestrator_prompt.txt` | **Minor edit** — update sandbox_coder description |
| `agents_config.json` | **Minor edit** — update description field |

No changes needed to: `agent_factory.py`, `investigation_orchestrator_prompt.txt`, tool definitions, or workflow configuration.

---

## 2. Architecture Change

### Before (Current)

```
                        evidence_planner
                       ┌─────────────────────────────────────┐
                       │                                     │
  orchestrator ───►    │  Step 0: sandbox_coder (hello-world) │
                       │  Step 1: sli_collector               │
                       │  Step 2: incident_collector           │
                       │  Step 3: support_collector            │
                       │  Step 4: Consolidate → JSON output    │
                       └─────────────────────────────────────┘
                                      │
                                      ▼
                                   reasoner
                                      │
                       (reasoner analyzes raw evidence text)
```

- sandbox_coder runs a **hardcoded hello-world script** before any collectors
- Its only purpose is to verify the sandbox is alive
- Collected evidence is returned as raw text — no statistical analysis
- The reasoner must interpret raw data with no computed metrics

### After (Proposed)

```
                        evidence_planner
                       ┌──────────────────────────────────────────┐
                       │                                          │
  orchestrator ───►    │  Step 1: sli_collector      ┐            │
                       │  Step 2: incident_collector  ├─ parallel │
                       │  Step 3: support_collector  ┘            │
                       │  Step 4: sandbox_coder (ANALYSIS)        │
                       │          ├─ receives: question + evidence │
                       │          ├─ writes: Python analysis code  │
                       │          ├─ executes: in sandbox          │
                       │          └─ returns: stats + CSV + answer │
                       │  Step 5: Consolidate → JSON output       │
                       └──────────────────────────────────────────┘
                                      │
                                      ▼
                                   reasoner
                                      │
                       (reasoner gets computed stats + answer)
```

- Hello-world warmup is **removed**
- Collectors run first (in parallel, as before)
- sandbox_coder runs **after** all collectors return
- sandbox_coder receives the reasoner's question + all collected evidence
- It **writes** a Python script that analyzes the evidence, **executes** it, and returns:
  - A 2-5 sentence analytical answer (stdout)
  - CSV data files in `/mnt/data/`
  - A manifest file at `/mnt/data/_manifest.json`

---

## 3. File-by-File Change Specification

### 3.1 `maf_sandbox_coder_prompt.txt`

**Path:** `Code/CustomerAgent/src/prompts/maf_sandbox_coder_prompt.txt`
**Change type:** Full rewrite (30 lines → ~120 lines)

#### Before (current — full content)

```
You are a Python code execution specialist. You MUST use tools to execute code.
You NEVER respond with code as text — you ALWAYS call the execute_python_in_sandbox tool.

CRITICAL RULE: Every response MUST contain a tool_call to execute_python_in_sandbox.
If you respond with text instead of a tool_call, your response is INVALID.

## Tools Available

- **execute_python_in_sandbox**: Run Python code and get stdout/stderr/files back.
  YOU MUST CALL THIS TOOL. Do not just write code as text.
- **list_sandbox_files**: See what files exist in /mnt/data.

## Execution Flow

1. Read the task you receive.
2. IMMEDIATELY call execute_python_in_sandbox with the Python code.
   - If the task contains code to execute, pass it directly to the tool.
   - If the task describes what to compute, write the code and pass it to the tool.
3. Return the tool result.

## Guidelines

1. Write complete, self-contained Python scripts (the sandbox has no prior state).
2. Available packages: pandas, numpy, matplotlib, plotly, scikit-learn, requests.
3. Save output files to /mnt/data/ (e.g. /mnt/data/chart.png).
4. Print results to stdout for the conversation — the orchestrator sees your output.
5. If execution fails, read the stderr, fix the code, and retry (max 2 retries).
6. For visualizations, prefer plotly for interactive charts or matplotlib for static.
7. Always use matplotlib.use('Agg') before importing pyplot for non-interactive rendering.
8. Always print a summary of what was produced at the end of execution.
```

#### After (new prompt — complete content)

```
ROLE
You are the Sandbox Coder — a Python Code Creator and Executor in the RATIO
investigation system. You receive a question from the reasoner and collected
evidence data from the collectors, then WRITE and EXECUTE a Python analysis
script that answers the question with statistical rigor.

You are NOT a code relay. You AUTHOR the code yourself based on the evidence.

═══════════════════════════════════════════════════════════════════
TOOLS AVAILABLE
═══════════════════════════════════════════════════════════════════

- **execute_python_in_sandbox**: Run Python code and get stdout/stderr/files.
  YOU MUST CALL THIS TOOL. Do not respond with code as text.
- **list_sandbox_files**: List files in /mnt/data.

═══════════════════════════════════════════════════════════════════
CRITICAL RULES
═══════════════════════════════════════════════════════════════════

1. Every response MUST contain a tool_call to execute_python_in_sandbox.
   If you respond with text instead of a tool_call, your response is INVALID.

2. You WRITE the Python code. The task description gives you:
   (a) The reasoner's QUESTION — what needs to be answered
   (b) EVIDENCE DATA — raw collected data with schema (from collectors)
   (c) PRIOR CONTEXT — previous analysis or investigation state (if any)
   Your job is to write a script that transforms (b) to answer (a).

3. Your code MUST produce THREE outputs:
   (a) stdout: A direct analytical answer (2-5 sentences) to the reasoner's
       question. This is what the reasoner will read. Be specific — include
       numbers, percentages, and statistical measures.
   (b) CSV files: Save detailed data to /mnt/data/*.csv for downstream use.
   (c) Manifest: Append file metadata to /mnt/data/_manifest.json.

4. NO VISUALIZATIONS. Do not generate charts, plots, or images.
   Produce only statistics, computations, and data files.

═══════════════════════════════════════════════════════════════════
EXECUTION FLOW
═══════════════════════════════════════════════════════════════════

1. READ the task string. Parse out:
   - The QUESTION to answer
   - The EVIDENCE block (raw data, schemas, summaries from collectors)
   - Any PRIOR CONTEXT from earlier analysis passes

2. DESIGN the analysis:
   - What statistical operations answer the question?
   - What data transformations are needed?
   - What metrics should be computed?

3. WRITE a complete, self-contained Python script that:
   - Embeds the evidence data directly (as dicts/lists — no external files)
   - Loads it into pandas DataFrames
   - Performs the analysis
   - Prints the answer to stdout
   - Saves CSV output to /mnt/data/
   - Updates /mnt/data/_manifest.json

4. CALL execute_python_in_sandbox with the script.

5. If execution FAILS:
   - Read the stderr
   - Fix the code
   - Retry (max 2 retries, 3 attempts total)
   - If still failing after 3 attempts, return the error with explanation

6. RETURN the execution result.

═══════════════════════════════════════════════════════════════════
CODE GUIDELINES
═══════════════════════════════════════════════════════════════════

1. Scripts must be COMPLETE and SELF-CONTAINED.
   The sandbox has no prior state — every run starts fresh.

2. EMBED evidence data directly in the script as Python literals.
   Do NOT assume any files exist. Do NOT call external APIs.
   ```python
   data = [
       {"timestamp": "2026-04-15T10:00:00Z", "resource": "vm-001", "metric": "availability", "value": 0.92},
       {"timestamp": "2026-04-15T10:05:00Z", "resource": "vm-001", "metric": "availability", "value": 0.87},
   ]
   df = pd.DataFrame(data)
   ```

3. Available packages: pandas, numpy, scikit-learn, requests, scipy.

4. ANSWER THE QUESTION. Do not just summarize or reformat the data.
   Apply statistical analysis:
   - Aggregations: mean, median, p50/p75/p90/p95/p99, std dev
   - Correlations: time-based trends, cross-metric correlation
   - Comparisons: before/after, baseline vs incident period
   - Distributions: histogram bins, outlier detection (IQR or z-score)
   - Counts: affected resources, breach frequency, unique customers

5. stdout format — print the answer clearly:
   ```
   === ANALYSIS RESULT ===
   [2-5 sentence answer to the question, with specific numbers]
   === END RESULT ===
   ```

6. CSV output — save detailed data:
   ```python
   df_result.to_csv("/mnt/data/analysis_result.csv", index=False)
   ```

7. Manifest — update /mnt/data/_manifest.json:
   ```python
   import json
   from pathlib import Path

   manifest_path = Path("/mnt/data/_manifest.json")
   manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {"files": []}
   manifest["files"].append({
       "path": "/mnt/data/analysis_result.csv",
       "description": "SLI breach analysis by resource and time bucket",
       "rows": len(df_result),
       "columns": list(df_result.columns)
   })
   manifest_path.write_text(json.dumps(manifest, indent=2))
   ```

8. Handle edge cases:
   - If evidence data is empty, print "No data available for analysis" and
     explain what data was expected.
   - If the question cannot be answered with the available evidence, print
     what IS available and what is MISSING.
   - Always validate DataFrame shapes before operations.

═══════════════════════════════════════════════════════════════════
NEGATIVE EXAMPLES
═══════════════════════════════════════════════════════════════════

BAD — Just reformatting data without analysis:
  print(df.to_string())
  WHY BAD: No statistical analysis, no answer to the question.

BAD — Generating a chart:
  plt.savefig("/mnt/data/chart.png")
  WHY BAD: No visualizations. Produce statistics and CSV only.

BAD — Responding with code as text instead of calling the tool:
  "Here is the Python code you should run: ```python ..."
  WHY BAD: You must CALL execute_python_in_sandbox, not paste code as text.

BAD — Not embedding data:
  df = pd.read_csv("/mnt/data/evidence.csv")
  WHY BAD: No files exist in the sandbox. Embed data as Python literals.

GOOD — Complete analysis with embedded data:
  data = [{"resource": "vm-001", "availability": 0.92}, ...]
  df = pd.DataFrame(data)
  breach_count = (df["availability"] < 0.999).sum()
  mean_avail = df["availability"].mean()
  print(f"=== ANALYSIS RESULT ===")
  print(f"{breach_count} of {len(df)} resources breached SLA. Mean availability: {mean_avail:.4f}")
  print(f"=== END RESULT ===")
  df.to_csv("/mnt/data/sli_analysis.csv", index=False)
```

#### Why this change

The current prompt treats sandbox_coder as a dumb executor that just runs whatever code is given. The new prompt makes it an **analytical code author** that:
- Understands its inputs (question + evidence)
- Designs appropriate statistical analysis
- Writes complete self-contained scripts
- Produces structured outputs (answer + CSV + manifest)
- Handles errors and edge cases

---

### 3.2 `investigation_evidence_planner_prompt.txt`

**Path:** `Code/CustomerAgent/src/prompts/investigation_evidence_planner_prompt.txt`
**Change type:** Major edit — 4 specific changes

#### Change A: Remove hello-world warmup from CRITICAL EXECUTION RULES

**Location:** Lines 21-23

**Before:**
```
2. YOUR FIRST TOOL CALL MUST BE sandbox_coder. Before calling ANY collector,
   you MUST call sandbox_coder with the hello-world script. This is non-negotiable.
   If your tool_calls do not include sandbox_coder, the turn is REJECTED.
```

**After:**
```
2. After all collectors return, you MUST call sandbox_coder to analyze the
   collected evidence and answer the reasoner's question. sandbox_coder writes
   and executes a Python analysis script — pass it the question and evidence data.
   If you skip sandbox_coder after collection, your turn is INCOMPLETE.
```

**Why:** sandbox_coder should run after collectors, not before. The hello-world warmup wastes a tool call and adds latency. The sandbox container is started by the infrastructure — no warmup needed.

#### Change B: Update COLLECTOR TOOLS section — sandbox_coder entry

**Location:** Lines 102-109 (tool #1 definition)

**Before:**
```
1. sandbox_coder(task: str)  *** MUST BE CALLED FIRST ON EVERY TURN ***
   - Writes and executes Python code in a secure sandbox container
   - You MUST call this tool on EVERY turn, BEFORE or IN PARALLEL with collectors
   - Pass EXACTLY this task string (copy-paste verbatim):

   {"task": "Execute this exact Python script:\n\nimport sys\nimport datetime\n\nprint('=' * 50)\nprint('  SANDBOX HELLO WORLD')\nprint('=' * 50)\n...[hardcoded hello-world script]..."}

   If you do NOT call sandbox_coder, your turn is INCOMPLETE and will be REJECTED.
```

**After:**
```
1. sandbox_coder(task: str)  *** MUST BE CALLED AFTER COLLECTORS RETURN ***
   - Writes and executes Python code to analyze collected evidence
   - You MUST call this tool AFTER all collectors return their findings
   - Pass a task string containing:
     (a) The QUESTION from the reasoner/hypothesis that needs answering
     (b) The EVIDENCE DATA returned by collectors (raw data, schemas, key metrics)
     (c) Any PRIOR CONTEXT from previous analysis passes

   Task string format:
   {"task": "QUESTION: <the reasoner's question or hypothesis to evaluate>\n\nEVIDENCE:\n<collector findings with data — include raw numbers, schemas, timestamps>\n\nPRIOR CONTEXT:\n<any relevant context from prior passes, or 'None'>"}

   sandbox_coder will write a Python script, execute it, and return:
   - A statistical answer to the question (stdout)
   - CSV data files in /mnt/data/
   - A manifest at /mnt/data/_manifest.json
```

**Why:** Replace the hardcoded hello-world invocation with a dynamic analysis call that passes collected evidence to sandbox_coder for statistical processing.

#### Change C: Update PLANNING LOGIC — Step 0

**Location:** Lines 139-153 (Step 0)

**Before:**
```
0. Call sandbox_coder. Pass EXACTLY this task string (copy-paste verbatim):
   {"task": "Execute this exact Python script:\n\nimport sys\nimport datetime\n\nprint('=' * 50)\nprint('  SANDBOX HELLO WORLD')\nprint('=' * 50)\n...[hardcoded hello-world script]..."}

   If you do NOT call sandbox_coder, your turn is INCOMPLETE and will be REJECT
```

**After:**
```
0. [REMOVED — sandbox_coder now runs AFTER collectors, not before]
```

The step numbering shifts: current steps 1-7 become steps 1-7 (step 0 is deleted). A new step is inserted after collectors return (between current steps 6 and 7).

**Why:** Eliminate the pre-collection warmup entirely.

#### Change D: Insert new step — call sandbox_coder after collectors

**Location:** Between current step 6 (CALL collectors) and step 7 (CONSOLIDATE)

**Insert new step after collectors return:**

```
7. CALL sandbox_coder with the collected evidence:
   After all collector results have returned, call sandbox_coder(task) with a
   task string that contains:
   (a) The hypothesis being evaluated and the question to answer
   (b) ALL collected evidence data from steps above — include raw numbers,
       resource lists, timestamps, breach counts, and schemas
   (c) Any prior context from earlier analysis passes

   Format the task as:
   {"task": "QUESTION: Does the evidence support hypothesis HYP-XXX-NNN: '<hypothesis description>'?\n\nEVIDENCE:\n### SLI Data\n<paste sli_collector findings with raw data>\n\n### Incident Data\n<paste incident_collector findings with raw data>\n\n### Support Data\n<paste support_collector findings with raw data>\n\nPRIOR CONTEXT:\n<any prior analysis or 'First pass — no prior context'>"}

   sandbox_coder will return a statistical analysis answering the question.
   Include the analysis result in your consolidated evidence output.

   CRITICAL: Pass the RAW DATA from collectors, not just summaries. sandbox_coder
   needs numbers, timestamps, resource IDs, and metric values to compute statistics.
   If a collector returned tabular data, pass the full table.
```

Current step 7 (CONSOLIDATE) becomes step 8.

**Why:** This is the core behavioral change — sandbox_coder now processes collected evidence to produce computed analysis rather than just forwarding raw text.

---

### 3.3 `maf_orchestrator_prompt.txt`

**Path:** `Code/CustomerAgent/src/prompts/maf_orchestrator_prompt.txt`
**Change type:** Minor edit — update specialist #8 description

**Location:** Lines 38-42 (specialist #8 listing)

**Before:**
```
8. sandbox_coder
   - Writes and executes Python code in a secure sandbox container
   - Can generate visualizations, run computations, validate data, or produce artifacts
   - Call when the user asks to "run code", "execute a script", "write Python",
     "compute", "calculate", or when analysis would benefit from live code execution
   - Also call for verification/validation tasks: confirm a hypothesis with a quick computation,
     generate a sample dataset, or prove a concept with a hello-world test
```

**After:**
```
8. sandbox_coder
   - Writes and executes Python analysis code in a secure sandbox container
   - Given a question and data, it authors a statistical analysis script, executes it,
     and returns computed results with CSV artifacts
   - Call when the user asks to "run code", "execute a script", "write Python",
     "compute", "calculate", "analyze data", or when evidence needs statistical processing
   - Produces: stdout analytical answer, CSV data files, manifest
   - Does NOT generate visualizations — statistics and data files only
```

**Why:** Reflect that sandbox_coder is now an analyst, not just an executor. Remove visualization reference (now stats-only). Add context about its output format.

---

### 3.4 `agents_config.json`

**Path:** `Code/CustomerAgent/src/config/agents/agents_config.json`
**Change type:** Minor edit — update description field

**Location:** sandbox_coder agent entry (around line 268)

**Before:**
```json
{
  "name": "sandbox_coder",
  "description": "Writes and executes Python code in a secure sandbox container. Can generate visualizations, run data analysis, and produce downloadable artifacts.",
  "prompt_file": "maf_sandbox_coder_prompt.txt",
  "model": "gpt-4o",
  "temperature": 0.3,
  "tool_mode": "sandbox",
  ...
}
```

**After:**
```json
{
  "name": "sandbox_coder",
  "description": "Authors and executes Python analysis code in a secure sandbox. Given a question and collected evidence data, writes a statistical analysis script, executes it, and returns computed results (stdout answer + CSV files + manifest).",
  "prompt_file": "maf_sandbox_coder_prompt.txt",
  "model": "gpt-4o",
  "temperature": 0.3,
  "tool_mode": "sandbox",
  ...
}
```

**Why:** Description should match the new dual-role behavior. No other fields change — `tool_mode: "sandbox"`, model, temperature, sub_agent relationship all stay the same.

---

## 4. New Prompt Design

The complete new `maf_sandbox_coder_prompt.txt` content is specified in Section 3.1 above (the "After" block). Key design decisions:

| Decision | Rationale |
|----------|-----------|
| **Keep `execute_python_in_sandbox` as the tool** | sandbox_coder IS the agent; `execute_python_in_sandbox` is its tool. No change to the MAF framework. |
| **Embed data as Python literals** | The sandbox starts fresh every run — no shared filesystem. Data must be in the script. |
| **No visualizations** | The reasoner needs numbers, not charts. Charts add complexity and latency. |
| **Manifest file** | Downstream agents can discover what was produced without parsing filenames. |
| **2-5 sentence answer format** | Forces concise, actionable output that the reasoner can directly consume. |
| **Max 2 retries** | Matches current behavior — prevents infinite retry loops. |
| **`=== ANALYSIS RESULT ===` delimiters** | Makes it easy for downstream parsers to extract the analytical answer from stdout. |
| **scipy added to package list** | Statistical tests (t-test, chi-square, correlation) need scipy. |
| **matplotlib/plotly removed from package list** | No visualizations — removing them prevents accidental chart generation. |

---

## 5. Evidence Planner Changes — Detailed Spec

### What changes

| Aspect | Before | After |
|--------|--------|-------|
| sandbox_coder call timing | BEFORE collectors (Step 0) | AFTER collectors (new Step 7) |
| sandbox_coder task content | Hardcoded hello-world script | Dynamic: question + evidence + context |
| sandbox_coder purpose | Verify sandbox is alive | Analyze collected evidence statistically |
| Collector parallelism | sandbox_coder in parallel with collectors | Collectors parallel, then sandbox_coder sequential |
| Turn structure | sandbox_coder + collectors → consolidate | Collectors → sandbox_coder → consolidate |

### New call flow (evidence_planner's perspective)

```
Turn 1: evidence_planner receives hypothesis
  │
  ├─ tool_call: sli_collector(task="Collect ER-SLI-001...")
  ├─ tool_call: incident_collector(task="Collect ER-OUT-001...")
  └─ tool_call: support_collector(task="Collect ER-TKT-001...")
      │
      ▼ (all collectors return)

Turn 2: evidence_planner consolidates collector results
  │
  └─ tool_call: sandbox_coder(task="QUESTION: Does evidence support HYP-SLI-001...
                                    EVIDENCE: [all collector data]...
                                    PRIOR CONTEXT: First pass...")
      │
      ▼ (sandbox_coder returns analysis)

Turn 3: evidence_planner produces final JSON output
  │
  └─ ```json { "structured_output": { "evidence_plan": [...], "evidence_items": [...] }, "signals": {...} } ```
```

**Important:** This changes the turn count. Currently, collectors + sandbox_coder fire in parallel in Turn 1. Now, Turn 1 fires collectors, Turn 2 fires sandbox_coder with results. The evidence_planner may need 2 tool-calling turns instead of 1. This is acceptable because the analysis quality improves significantly.

### Task string format for sandbox_coder

The evidence_planner should construct the task string dynamically:

```
QUESTION: Does the evidence support hypothesis HYP-SLI-001: "SLI availability
breach on Azure Virtual Machines for customer Contoso caused by regional
infrastructure degradation"?

EVIDENCE:
### SLI Data (from sli_collector)
- 12 impacted resources found
- Resources: vm-001 (availability: 0.92), vm-002 (0.87), vm-003 (0.95), ...
- Time range: 2026-04-15T08:00Z to 2026-04-15T14:00Z
- Breach threshold: 0.999
- Schema: [resource_id, metric_name, metric_value, timestamp, region, subscription_id]

### Incident Data (from incident_collector)
- 2 active incidents: INC-12345 (Sev 2, Azure Compute - West US 2), INC-12346 (Sev 3, Azure Networking)
- INC-12345: Declared 2026-04-15T09:15Z, Mitigated 2026-04-15T12:30Z
- Root cause: Memory pressure on host nodes in cluster WUS2-C04

### Support Data (from support_collector)
- 5 support tickets filed by Contoso in the time window
- SR-001: "VMs unresponsive", filed 2026-04-15T09:45Z
- SR-002: "High latency on SQL DB", filed 2026-04-15T10:10Z
- ...

PRIOR CONTEXT:
First pass — no prior context.
```

### How evidence_planner consolidates sandbox_coder output

After sandbox_coder returns, the evidence_planner includes its analysis in the `evidence_items` array:

```json
{
  "er_id": "ER-ANALYSIS-001",
  "hypothesis_ids": ["HYP-SLI-001"],
  "agent_name": "sandbox_coder",
  "tool_name": "execute_python_in_sandbox",
  "summary": "Statistical analysis: 10 of 12 resources (83%) breached SLA during incident window. Mean availability dropped from 0.9995 (baseline) to 0.9187 (incident). Strong temporal correlation (r=0.94) between INC-12345 timeline and SLI degradation.",
  "preliminary_verdict": "supports"
}
```

---

## 6. Orchestrator Changes

Only the specialist description in `maf_orchestrator_prompt.txt` needs updating (see Section 3.3). No routing logic changes because:

- The orchestrator already routes to sandbox_coder for code/compute tasks (STEP 4)
- The investigation flow doesn't go through the orchestrator — it uses `investigation_orchestrator`
- The `investigation_orchestrator` only routes to `evidence_planner` and `reasoner` — sandbox_coder is a sub_agent of evidence_planner, invisible to the investigation orchestrator

---

## 7. agents_config.json Changes

Only the `description` field changes (see Section 3.4). All other fields remain:

```json
"name": "sandbox_coder"           // unchanged
"prompt_file": "maf_sandbox_coder_prompt.txt"  // unchanged (same file, new content)
"model": "gpt-4o"                 // unchanged
"temperature": 0.3                // unchanged
"tool_mode": "sandbox"            // unchanged — still maps to execute_python_in_sandbox + download + list
"mcp_tools": []                   // unchanged
"evaluate": false                 // unchanged
"prompt_injection": true          // unchanged
```

The `sub_agents` array in `evidence_planner` also remains unchanged:
```json
"sub_agents": ["sandbox_coder", "sli_collector", "incident_collector", "support_collector"]
```

The `investigation_workflow.participants` array remains unchanged:
```json
"participants": ["evidence_planner", "reasoner"]
```

sandbox_coder is NOT a direct participant — it's a sub_agent of evidence_planner, which is correct.

---

## 8. Verification Checklist

### Acceptance Criterion 1: Only one agent for code — "Sandbox_Coder"

- [ ] `agents_config.json` has exactly ONE agent named `sandbox_coder`
- [ ] No new agent entries added to `agents_config.json`
- [ ] No new prompt files created for code execution/authoring
- [ ] `sandbox_coder` still has `tool_mode: "sandbox"`

### Acceptance Criterion 2: `maf_sandbox_coder_prompt.txt` updated with dual-role

- [ ] Prompt includes ROLE section describing code author + executor
- [ ] Prompt describes the 3 required outputs (stdout answer, CSV, manifest)
- [ ] Prompt includes code guidelines with embedded data pattern
- [ ] Prompt includes negative examples
- [ ] Prompt does NOT reference hello-world
- [ ] Prompt does NOT mention visualizations/charts
- [ ] `execute_python_in_sandbox` is still the primary tool call

### Acceptance Criterion 3: Evidence planner calls sandbox_coder AFTER data collection

- [ ] Hello-world warmup removed from CRITICAL EXECUTION RULES (line 21-23)
- [ ] Hello-world warmup removed from COLLECTOR TOOLS section (lines 102-109)
- [ ] Hello-world warmup removed from PLANNING LOGIC Step 0 (lines 139-153)
- [ ] New step added AFTER collectors: call sandbox_coder with question + evidence
- [ ] Task string format documented with QUESTION/EVIDENCE/PRIOR CONTEXT sections
- [ ] sandbox_coder called AFTER collectors return, not in parallel with them

### Acceptance Criterion 4: All other prompts updated

- [ ] `maf_orchestrator_prompt.txt` specialist #8 description updated
- [ ] `agents_config.json` description field updated
- [ ] No stale references to "hello-world" or "warmup" in any prompt file
- [ ] `investigation_orchestrator_prompt.txt` confirmed unchanged (no sandbox_coder refs)

### Functional Verification

- [ ] Run investigation workflow end-to-end with a test signal
- [ ] Verify collectors complete before sandbox_coder is called
- [ ] Verify sandbox_coder receives evidence data in its task string
- [ ] Verify sandbox_coder writes AND executes Python code (not just executes given code)
- [ ] Verify stdout contains `=== ANALYSIS RESULT ===` delimited answer
- [ ] Verify CSV files created in `/mnt/data/`
- [ ] Verify `_manifest.json` created/updated in `/mnt/data/`
- [ ] Verify evidence_planner includes sandbox_coder results in `evidence_items`
- [ ] Verify the non-investigation workflow (maf_orchestrator) still routes to sandbox_coder correctly

---

## 9. Risks & Rollback

### Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| **Evidence planner needs 2 turns instead of 1** | High | Medium | The MAF group_chat supports multi-turn agents. evidence_planner already handles collector results across turns. Monitor `max_turns` budget (currently 40). |
| **sandbox_coder fails to write valid code from evidence** | Medium | Medium | The prompt includes retry logic (max 2 retries). Edge case handling instructs it to report what's missing. Model temperature is 0.3 (low creativity, high reliability). |
| **Evidence data too large to embed in script** | Low | High | Evidence is already condensed by collectors (they synthesize, not dump raw). If a collector returns >50 rows, evidence_planner should summarize and pass a representative sample. Add a size check in the task string construction. |
| **sandbox_coder output not parsed by evidence_planner** | Medium | Medium | The `=== ANALYSIS RESULT ===` delimiters make extraction deterministic. evidence_planner should extract text between delimiters for the evidence_item summary. |
| **Removing hello-world breaks sandbox container startup** | Low | Low | The sandbox container is started by infrastructure (Docker/ACI), not by the hello-world script. The script only verified the sandbox was alive — it never initialized it. |
| **Non-investigation workflow (orchestrator) regression** | Low | Low | The orchestrator prompt change is description-only. Routing logic (STEP 4) already routes to sandbox_coder for code tasks — no behavioral change needed. |

### Rollback Plan

All changes are prompt-level (no code changes). Rollback is straightforward:

1. **Full rollback:** `git revert <commit>` — restores all 4 files to current state
2. **Partial rollback (keep new prompt, revert planner):** Restore only `investigation_evidence_planner_prompt.txt` — sandbox_coder gets the new prompt but is still called as a warmup with hello-world (it will just ignore the hello-world and try to analyze it, which is harmless)
3. **Feature flag approach:** Add a toggle to `investigation_evidence_planner_prompt.txt` that checks a signal context variable (e.g., `sandbox_analysis_enabled`) and conditionally calls sandbox_coder for analysis vs. hello-world. This allows A/B testing.

### Files to back up before implementation

```
Code/CustomerAgent/src/prompts/maf_sandbox_coder_prompt.txt
Code/CustomerAgent/src/prompts/investigation_evidence_planner_prompt.txt
Code/CustomerAgent/src/prompts/maf_orchestrator_prompt.txt
Code/CustomerAgent/src/config/agents/agents_config.json
```

---

## 10. Implementation Order

Execute changes in this order to minimize broken states:

1. **`maf_sandbox_coder_prompt.txt`** — Rewrite prompt (standalone change, no dependencies)
2. **`agents_config.json`** — Update description (standalone change)
3. **`maf_orchestrator_prompt.txt`** — Update description (standalone change)
4. **`investigation_evidence_planner_prompt.txt`** — Remove hello-world, add post-collection analysis step (this is the behavioral change — do last so the new sandbox_coder prompt is already in place)

Steps 1-3 can be done in parallel. Step 4 depends on Step 1 being complete.
