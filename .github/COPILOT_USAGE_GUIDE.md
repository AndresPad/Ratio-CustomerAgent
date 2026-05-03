# Copilot Usage Guide for Ratio-CustomerAgent Contributors

This guide explains **when to use GitHub Copilot Chat in VS Code** vs **GitHub Copilot CLI (terminal)** while working on the **Ratio-CustomerAgent** repository — a multi-agent customer-investigation system built on **Microsoft Agent Framework** (Python/FastAPI), backed by an **MCP server** (`RATIO_MCP`) and surfaced through a **React 18 + Vite + TypeScript** debugging UI (`ratio_ui_web`).

Both tools share the same Copilot brain. The harness around them is what differs. **For this repository, VS Code is the right default for ~90% of day-to-day work**, and this guide makes the case for why — then tells you the narrow, well-defined places where dropping to the CLI actually pays off.

> 📌 Repo-wide Copilot rules live in [.github/copilot-instructions.md](copilot-instructions.md). Path-scoped rules live in [.github/instructions/](instructions/) and load automatically based on the file you touch. Reusable workflows are in [.github/skills/](skills/), and specialist subagents are in [.github/agents/](agents/) (`engineer`, `frontend`, `qa`). All of these are read by VS Code Copilot **and** the CLI — but VS Code surfaces them more visibly (skill picker, agent picker, instruction match indicators).

---

## TL;DR — Default to VS Code

| If you are… | Use |
|---|---|
| Iterating on a **Python agent**, **prompt**, or **`@tool`** function | **VS Code** |
| Building or styling a **React page / component** in `ratio_ui_web` | **VS Code** |
| Running the **investigation flow end-to-end** with the running services (`start_all.ps1`) and watching it in the UI | **VS Code** |
| **Debugging** a FastAPI route, an agent turn, or a failing Pytest with breakpoints | **VS Code** |
| Editing **one file** you're already looking at | **VS Code** |
| Running a **headless, repo-wide refactor** with no UI feedback (e.g., rename a Pydantic field across 40 files) | CLI |
| **Delegating** a self-contained task to a cloud agent (`/delegate`) so you can keep working | CLI |
| Driving **CI-style pipelines** from a non-developer machine (no VS Code installed) | CLI |

**The short version:** this repo is a *live system* — three services, hot-reloaded UI, streamed agent traces, browser-based DevUI. VS Code is the cockpit. The CLI is a useful side tool for batch jobs.

---

## ✅ Why VS Code wins for Ratio-CustomerAgent

The CLI is great at "edit many files, run a build, repeat." This repo's loop is different: **edit a prompt → service auto-reloads → trigger an investigation in the React UI → watch the streamed agent turns → look at OpenTelemetry traces → adjust.** That loop is editor-native, browser-native, and debugger-native — exactly what VS Code is built for.

### 1. The dev loop is interactive, not batch

- `Code/scripts/start_all.ps1` brings up `ratio-mcp` (8000), `customer-agent` (8503), and `ratio-ui-web` (3010) and **tails all three logs in one terminal** with `[ratio-mcp]` / `[customer-agent]` / `[ratio-ui-web]` prefixes. VS Code's integrated terminal keeps that visible while you edit; the CLI replaces your terminal with the agent harness.
- **Vite HMR** updates the React UI in milliseconds when you save a `.tsx`. There is no equivalent feedback in a CLI session — the CLI works best when the only signal is the exit code of `npm run build`, which is the wrong loop here.
- **FastAPI auto-reload** plus VS Code's "Python: Attach" gives you breakpoints inside agent turns. The CLI can run Pytest, but it cannot stop execution mid-turn so you can inspect `ChatHistory`, MCP tool payloads, or `AgentSession` state.

### 2. Agent and prompt iteration is selection-aware

- A large fraction of changes here are **prompt edits** (`Code/CustomerAgent/src/prompts/maf_*.txt`, `investigation_*.txt`) and small Python tweaks. Inline Chat (`Ctrl+I`) on a selection is faster than describing the file to the CLI.
- VS Code Copilot resolves `#file:`, `#sym:`, and `@workspace` against the open Python project. With more than a dozen agent prompts and **16 prompt files** in this repo, that semantic context matters — vague CLI prompts pull the wrong examples.
- "Fix with Copilot" on a **Pydantic v2 validation error** or a **Pylance squiggle** is a one-click action in the editor. The CLI requires re-running the failing command and pasting output back.

### 3. The React debugging UI is the primary artifact

- `ratio_ui_web` exists *to visualize agent investigations*. You verify it by **opening it in a browser**, clicking through scenarios, and reading the streamed orchestrator → analyst → summarizer turns. VS Code's **Simple Browser** plus the **JavaScript Debug Terminal** keep that within the IDE; the CLI cannot click a button or read a rendered chart.
- TypeScript IntelliSense, ESLint quick-fixes, and CSS-module rename refactors are all editor features. The CLI sees `.tsx` as text.

### 4. Local debugging beats log-grepping

- **Pytest with breakpoints** (`@pytest.mark.asyncio` tests in `Code/CustomerAgent/tests/`) — VS Code Test Explorer lets you debug a single failing test. The CLI runs `pytest -v` and parses the output.
- **Agent Framework DevUI** (when enabled) and **App Insights traces** are clicked through, not greped. You're looking at a tree of agent turns, not a stack trace.
- A failing investigation is debugged by attaching to the `customer-agent` process on port 8503 — that is a `launch.json` workflow.

### 5. Subagents and skills surface naturally in chat

- This repo ships **three subagents** ([engineer](agents/engineer.md), [frontend](agents/frontend.md), [qa](agents/qa.md)) and **four skills** ([new-agent](skills/new-agent/), [new-agent-tool](skills/new-agent-tool/), [new-api-endpoint](skills/new-api-endpoint/), [new-react-page](skills/new-react-page/)). VS Code's chat picker lists them; you invoke `engineer` for a Python task and `frontend` for a React task without typing slash commands. The CLI exposes the same files, but the picker UI is missing.
- Path-scoped instructions show as match indicators in VS Code, so you know which rules are active. For example, [react-ui.instructions.md](instructions/react-ui.instructions.md) auto-applies when you edit React files, and [agents.instructions.md](instructions/agents.instructions.md) auto-applies in `Code/CustomerAgent/**`.

### 6. Source control is one panel away

- Commit-message generation, PR descriptions, and inline diff review live in the **Source Control** panel. The CLI's `/pr` and `/review` work, but for normal feature commits you'd be context-switching out of the editor for no gain.

---

## ✅ Use **VS Code Copilot Chat** for…

| Scenario in this repo | Why VS Code wins |
|---|---|
| Writing or tweaking an agent (e.g., adding a new investigation collector — typically a new `investigation_*_prompt.txt` plus wiring in `Code/CustomerAgent/src/core/orchestrator.py`) | Inline Chat + Pylance + auto-loaded [agents.instructions.md](instructions/agents.instructions.md). |
| Authoring a new MCP `@tool` in `Code/RATIO_MCP/src/plugins/` | Selection-aware refactors; FastMCP type errors caught immediately. |
| Editing a prompt in `Code/CustomerAgent/src/prompts/` and re-running an investigation | Save → service auto-reloads → click in UI. CLI cannot click. |
| Building a new React page (e.g., another flow detail view) | Hot reload, JSX IntelliSense, CSS-module support, browser preview. Use the [frontend](agents/frontend.md) subagent. |
| Debugging an `httpx.AsyncClient` 429, an `azure.identity` credential failure, or a `ChatHistory` shape mismatch mid-run | Breakpoints, watch expressions, call stack — VS Code only. |
| Generating a single Pytest case for an agent or tool | Right-click → Copilot → Generate Tests. |
| Asking *"why is the orchestrator routing to summarizer too early?"* with a prompt file open | `#file:` + `#selection` ground the answer in the actual prompt. |
| Adding a Pydantic `BaseModel` for a new request schema in `Code/CustomerAgent/src/server/` | Pylance + Copilot collaborate on field types. |
| Reviewing a small diff before committing | Source Control panel + Copilot commit-message. |
| Iterating on Vite/Tailwind/CSS until something looks right | Editor + browser, not terminal. |

---

## 🛠 Use **Copilot CLI** for…

There are real wins for the CLI — they're just narrower in this repo. Pick the CLI when **the task is fully describable up front, has no UI feedback, and benefits from autonomous loops**.

| Scenario | Why CLI wins |
|---|---|
| **Repo-wide rename** (e.g., renaming a config field across `Code/CustomerAgent/src/config/**` and all consuming agents) | Bulk edits + `pytest` verification loop without human gating each file. |
| **Mass test triage** — *"run the full pytest suite and fix any non-flaky failures"* | Run → parse → fix → re-run, with no UI in the loop. |
| **Dependency or version bumps** in `requirements.txt` / `package.json` followed by `pip install` and `npm ci` and re-running tests | Shell-native, no editor needed. |
| **`/delegate`** a well-scoped task to a cloud Copilot coding agent (e.g., *"implement a new collector agent per the [new-agent](skills/new-agent/) skill"*) | CLI-only feature; opens a PR while you keep building. |
| **`/plan` or `/research`** before a large multi-service change (e.g., adding a new investigation phase) | Persistent `plan.md`, multi-turn research mode. |
| **CI-like local runs** — `pytest`, `npm run build`, `docker compose build`, all in sequence, with auto-fix attempts | Approval-gated shell loop is purpose-built for this. |
| **Updating multiple instruction files** in `.github/instructions/` consistently (e.g., adding a new common pitfall to all four) | One pattern, four files, one CLI turn. |
| **Working from a machine without VS Code** (jump box, devbox, codespace shell) | CLI is the only option. |

When you do use the CLI here, follow these rules:

1. **Approve shell commands deliberately.** `start_all.ps1` kills processes on ports 8000/8503/3010 — don't let an agent run it during an active debug session.
2. **Don't `npm run dev` or start FastAPI inside the CLI.** Long-running servers don't fit the request/response loop. Run them in a normal terminal (or via `start_all.ps1`) and let the CLI run *Pytest* and *builds*.
3. **Never paste secrets.** Azure credentials come from `DefaultAzureCredential`; OpenAI keys come from environment variables. Confirm `.env` is gitignored.
4. **Use `/rewind`** if a multi-turn change starts touching files outside the intended scope.

---

## 🎯 Concrete Ratio-CustomerAgent Playbook

| Task | Tool |
|---|---|
| "Add a new investigation collector agent (`network_collector`) following the `sli_collector` / `incident_collector` pattern — new `investigation_network_collector_prompt.txt` + wiring in `core/orchestrator.py`" | **VS Code** with [engineer](agents/engineer.md) + [new-agent](skills/new-agent/) |
| "Add a new `@tool` to RATIO_MCP that queries Kusto for tenant health" | **VS Code** with [new-agent-tool](skills/new-agent-tool/) |
| "Rename `signal_id` → `symptom_id` everywhere in `Code/CustomerAgent/src/config/**` and update tests" | **CLI** |
| "Why is the `analyst_coordinator` calling `airo_analyst` twice?" while watching the streamed turns in the UI | **VS Code** Chat with the prompt and orchestrator log open |
| "Run the full Pytest suite and fix all failures" | **CLI** |
| "Build a new React page that visualizes hypothesis scoring" | **VS Code** with [frontend](agents/frontend.md) + [new-react-page](skills/new-react-page/) |
| "Tweak `maf_orchestrator_prompt.txt` until the orchestrator stops generating SQL" | **VS Code** — edit, save, re-run an investigation in the UI |
| "Fix a failing test about A2A registration" | **VS Code** debugger (set a breakpoint, run *Debug Test*) |
| "Bump `agent-framework` to v1.0.2 and resolve any breaking changes" | **CLI** |
| "Add a new FastAPI endpoint under `/api/investigate/replay`" | **VS Code** + [new-api-endpoint](skills/new-api-endpoint/) |
| "Mermaid diagram the orchestrator → analyst → summarizer flow for the docs" | **VS Code** (Mermaid preview) |
| "Generate a commit message for the staged changes" | **VS Code** Source Control panel |
| "Delegate: write a deepeval test suite for the `reasoner` agent's hypothesis selection" | **CLI** `/delegate` |
| "Why is `DefaultAzureCredential` returning a `ManagedIdentityCredential` token locally?" with the running process attached | **VS Code** debugger |
| "Refactor all `print()` calls to `logging.getLogger(__name__)` across `Code/CustomerAgent/src/**`" | **CLI** |

---

## How the dev loop actually looks in VS Code

A typical session — and why each step would be worse in the CLI:

1. **`./Code/scripts/start_all.ps1`** in an integrated terminal. All three services boot; logs interleave with `[ratio-mcp]`, `[customer-agent]`, `[ratio-ui-web]` prefixes.
2. **Open the Simple Browser** to `http://127.0.0.1:3010` side-by-side with the editor.
3. **Edit a prompt** (`maf_orchestrator_prompt.txt`). FastAPI auto-reloads; the next investigation in the UI uses the new prompt. *(CLI cannot drive the browser.)*
4. **Click an investigation** and watch the streamed turns. *(CLI cannot click.)*
5. **Notice the orchestrator picked the wrong analyst.** Use Inline Chat (`Ctrl+I`) on the routing rule in the prompt, ask Copilot to tighten it, save. *(CLI would require describing the file from scratch.)*
6. **Set a breakpoint** in `core/orchestrator.py` and re-run; inspect `ChatHistory` live. *(CLI has no debugger.)*
7. **Update a Pytest** for the new behaviour, run it via Test Explorer with the debugger. *(CLI runs the test but cannot stop it.)*
8. **Stage and commit** from the Source Control panel; let Copilot draft the message.

When you finally hit *"now refactor every collector agent's logging format"* — that's when you switch to the CLI.

---

## Best Practices

### When using **VS Code Copilot Chat** here

1. **Trust the path-scoped instructions.** Files under `Code/CustomerAgent/**` automatically pull in [agents.instructions.md](instructions/agents.instructions.md); React files pull in [react-ui.instructions.md](instructions/react-ui.instructions.md). Don't paste rules into chat — point at the files.
2. **Use the right subagent.** [engineer](agents/engineer.md) for Python/FastAPI/agents, [frontend](agents/frontend.md) for React/Vite/TS, [qa](agents/qa.md) for tests/lint/review. Switching subagents changes the model's priors.
3. **Use skills for scaffolding.** [new-agent](skills/new-agent/), [new-agent-tool](skills/new-agent-tool/), [new-api-endpoint](skills/new-api-endpoint/), [new-react-page](skills/new-react-page/) encode the canonical patterns. They beat ad-hoc prompts.
4. **Reference the canonical examples.** For a new investigation collector, point Chat at `sli_collector` + `incident_collector`. For a new analyst, point at `outage_analyst`. For a React page, point at `ChaFlowDetailV4Page.tsx`.
5. **Inline Chat (`Ctrl+I`)** for surgical edits. Side-panel Chat for "explain" / "plan" / "review."
6. **Never let Chat run `start_all.ps1` during an active debug.** It kills the very process you're attached to.
7. **Don't ask Chat to drive multi-step builds.** Use the CLI for that.

### When using **Copilot CLI** here

1. **Plan before implementing** non-trivial work — `/plan` or *"plan first."*
2. **Verify with the repo's real commands**: `pytest`, `npm run build`, `npm run lint`, `docker compose build`. Every code change ends with at least `pytest`.
3. **Don't run long-lived servers in the CLI.** They block the loop. Use a normal terminal.
4. **Use `/delegate`** for parallel work that doesn't need your eyes.
5. **Approve every shell command.** Especially anything that writes to `.env`, `requirements.txt`, or `package.json`.
6. **Never paste secrets.** Use `DefaultAzureCredential` and env vars per [.github/copilot-instructions.md](copilot-instructions.md).

---

## Why the CLI's strengths matter less in this repo

The CLI's headline advantages — agentic loops, parallel tool calls, persistent plans — are real, but each one assumes the work is **headless, deterministic, and verifiable by exit codes**. Ratio-CustomerAgent is the opposite:

| CLI strength | Why it's blunted here |
|---|---|
| Autonomous shell loops | Most defects only show up in the rendered UI or in streamed agent turns — neither is an exit code. |
| Parallel file edits | Useful, but the *correctness* of an agent change is judged by running an investigation, not by a build. |
| Persistent `plan.md` | Plans go stale fast against a live LLM-driven system; a real session is "edit, observe, adjust" — pure VS Code. |
| Repo-wide context | VS Code's `@workspace` + the path-scoped instructions already give you that, with the editor's symbol index. |
| Approval-gated shell | Real benefit, but most of our shell work is `start_all.ps1` (run once) + `pytest` (one command). |
| `/delegate` to cloud agent | Genuinely useful for self-contained backlog items; keep using it. |

**In short:** use the CLI for batch jobs and delegation. Use VS Code for everything else — which, in this repo, is most things.

---

## Related References

- [.github/copilot-instructions.md](copilot-instructions.md) — repo-wide Copilot rules
- [.github/instructions/agents.instructions.md](instructions/agents.instructions.md) — Microsoft Agent Framework + orchestration rules (`Code/CustomerAgent/**`)
- [.github/instructions/python-services.instructions.md](instructions/python-services.instructions.md) — FastAPI / Pydantic / async rules
- [.github/instructions/react-ui.instructions.md](instructions/react-ui.instructions.md) — React 18 / Vite / TypeScript rules
- [.github/instructions/evaluation.instructions.md](instructions/evaluation.instructions.md) — evaluation engines
- [.github/skills/](skills/) — `new-agent`, `new-agent-tool`, `new-api-endpoint`, `new-react-page`
- [.github/agents/](agents/) — `engineer`, `frontend`, `qa` subagent definitions
- [Code/scripts/start_all.ps1](../Code/scripts/start_all.ps1) — local dev startup (ports 8000 / 8503 / 3010)
