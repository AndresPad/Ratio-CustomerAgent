# CustomerAgent Merge Plan

**Date:** 2026-04-21
**Repo:** RATIO-AI
**Authors:** jmarrocco (plan), rbhuyan (vibe-coded implementation)

---

## Part 1 — Differences: Dev Plan vs. Vibe-Coded Implementation

### 1.1 Summary

The dev plan (`docs/signal-symptom-hypothesis-dev-plan.md`) described a **10-feature incremental build** inside `Code/Servers/agents/` — extending the existing agent service with new models, services, tools, agents, and a workflow. The vibe-coded `Code/CustomerAgent/` is a **standalone application** with its own server, UI, agent factory, middleware, config system, and investigation pipeline. The two share domain intent (Signal → Symptom → Hypothesis) but diverge significantly in architecture, patterns, and scope.

### 1.2 Structural Comparison

| Aspect | Dev Plan (`Code/Servers/agents/`) | Vibe-Coded (`Code/CustomerAgent/`) |
|--------|-----------------------------------|-------------------------------------|
| **Models** | Pydantic `BaseModel` subclasses in `models/` (Signal, Symptom, Hypothesis, CompoundSignal) — simple, flat schemas | `dataclass`-based models in `core/signals/signal_models.py` and `core/investigation/investigation_state.py` — much richer: `ActivatedSignal`, `TypeSignalResult`, `CompoundSignalResult`, `SignalBuilderResult`, `Investigation`, `InvestigationContext`, `EvidenceItem`, `EvidenceRequirement`, `StreamEvent`, plus enums (`InvestigationPhase`, `HypothesisStatus`, `EvidenceVerdict`, `SymptomVerdict`) |
| **Signal Fetching** | `SignalSource` ABC → `KustoSignalSource` using `Code.Shared.clients.kusto_client.query_kql_async` | `signal_builder.py` calls MCP tools directly via `MCPStreamableHTTPTool` — no abstract source layer, no use of `Code.Shared` |
| **Config** | Planned: Pydantic config classes in `config/` loading 3 JSON files from `config/s2h_mappings/` | Implemented: JSON files under `src/config/` organized by domain (`signals/`, `symptoms/`, `hypotheses/`, `actions/`, `agents/`, `evidence/`, `dependency_services/`) — much broader config surface with monitoring context, agent definitions, and action catalogs |
| **Signal → Symptom** | Planned: `SignalSymptomMapper` service with rule evaluation (operators: `>=`, `<=`, etc.) | Implemented: Hybrid model — `symptom_matcher.py` loads templates and formats them for an **LLM triage agent** to match (not programmatic rule eval) |
| **Hypothesis Scoring** | Planned: `SymptomHypothesisMapper` + `WeightedCoverageRanker` in `services/` | Implemented: `hypothesis_scorer.py` with weighted overlap × signal strength scoring — **programmatic** (no LLM), closer to plan but with richer scoring (configurable aggregation: avg/max/min, min_symptoms_for_match) |
| **Agents** | Planned: 3 new agents (SignalIngestion, SymptomMapping, HypothesisRanking) extending `BaseAgent`, registered in factory, using `@tool` functions | Implemented: Config-driven MAF `Agent` instances created by `core/agent_factory.py` from `agents_config.json` — includes triage, planner, collectors (SLI, incident, support), reasoner, action planner, orchestrator — **much broader scope** |
| **Orchestration** | Planned: `SequentialBuilder` chaining 3 agents | Implemented: MAF `GroupChatBuilder` with orchestrator-managed speaker selection, termination conditions, streaming — full multi-agent GroupChat |
| **Server** | Planned: Add routes to existing `app_kernel.py` (port 8000) | Implemented: Separate FastAPI app in `src/server/app.py` (port 8503) with `/chat`, `/chat/stream`, `/health`, plus A2A protocol routes |
| **UI** | Planned: Use existing `Code/Frontend/` (React) | Implemented: **Two UIs** — Streamlit chat UI (`src/UI/app.py`) and a vanilla JS/HTML investigation dashboard (`CustomerAgentUI/`) |
| **Middleware** | Planned: Reuse `Code/Shared/middleware/` stack | Implemented: Own middleware in `core/middleware/` (eval, prompt injection, LLM logging, tool capture) — reimplements what exists in `Code/Shared/middleware/` |
| **Auth / LLM** | Planned: Reuse `Code/Shared/clients/chat_client.py` | Implemented: Own `helper/llm.py` (creates `OpenAIChatCompletionClient`) and `helper/auth.py` (token management, MCP bearer tokens, SSO) — reimplements shared utilities |
| **Logging** | Planned: Use `logging.getLogger(__name__)` per convention | Implemented: `helper/agent_logger.py` — elaborate singleton with App Insights, OTel spans, per-agent log overrides, real-time UI event queue, XCV correlation |
| **MCP** | Planned: Not explicitly in the dev plan | Implemented: `core/mcp_integration.py` creates `MCPStreamableHTTPTool` instances with auth — signal builder calls MCP tools directly |
| **A2A** | Not in dev plan | Implemented: Full Google A2A protocol support (`a2a/` — agent cards, JSON-RPC executor, registry, route registration) |
| **Investigation Pipeline** | Not in dev plan (S2H was just Signal→Symptom→Hypothesis) | Implemented: Full 7-phase investigation lifecycle (triage → hypothesizing → planning → collecting → reasoning → acting → notifying) with evidence collection, hypothesis confirmation/refutation, and action catalogs |
| **Tests** | Planned: 21 test files (one per source file) in `Code/Tests/` | Implemented: 2 test files in `Code/Tests/` (signal source only) — investigation pipeline untested |
| **Entry Points** | Planned: API endpoint only | Implemented: `run_signal_builder.py` (one-shot), `run_signal_builder_loop.py` (timer loop), server, Streamlit UI |

### 1.3 What the Vibe-Coded Version Has That the Plan Didn't

1. **Full investigation lifecycle** — 7-phase pipeline with evidence collection, reasoning cycles, action planning
2. **Config-driven agent roster** — agents_config.json with per-agent model overrides, tool modes, temperature, logging flags
3. **A2A protocol** — agent discovery and standalone invocation
4. **MCP integration** — direct MCP tool calls for signal collection (Kusto queries go through MCP server, not direct Kusto SDK)
5. **Streamlit UI** — full chat interface with SSO, agent badges, streaming, dataset rendering
6. **CustomerAgentUI** — vanilla JS investigation dashboard (timeline, graph, phases views)
7. **AgentLogger** — enterprise-grade observability with App Insights, OTel, XCV correlation, real-time UI events
8. **Prompt system** — 16 `.txt` prompt files for each agent role
9. **Action catalog** — `config/actions/action_catalog.json` with remediation actions
10. **Evidence system** — `config/evidence/evidence_requirements.json` defining what data to collect per hypothesis
11. **Dependency service configs** — 5 JSON files modeling Azure service dependencies
12. **Monitoring context** — customer/service targeting with poll intervals

### 1.4 What Was Already Built in `Code/Servers/agents/` (Plan's F1 + F3)

| File | Status | Content |
|------|--------|---------|
| `models/signal.py` | ✅ Done | Pydantic `Signal` model |
| `models/symptom.py` | ✅ Done | Pydantic `Symptom` model |
| `models/hypothesis.py` | ✅ Done | Pydantic `Hypothesis` model |
| `models/compound_signal.py` | ✅ Done | Pydantic `CompoundSignal` model |
| `models/__init__.py` | ✅ Done | Re-exports all models |
| `services/signalSource/signal_source.py` | ✅ Done | `SignalSource` ABC |
| `services/signalSource/kusto_signal_source.py` | ✅ Done | `KustoSignalSource` using `Code.Shared` |
| `services/signalSource/signal_source_factory.py` | ✅ Done | `SignalSourceFactory` |
| `services/__init__.py` | ✅ Done | Re-exports |
| `Code/Tests/test_kusto_signal_source.py` | ✅ Done | Unit tests |
| `Code/Tests/test_signal_source_factory.py` | ✅ Done | Unit tests |

### 1.5 Key Conflicts / Overlaps

| Area | `Code/Servers/agents/` | `Code/CustomerAgent/src/` | Conflict Level |
|------|------------------------|---------------------------|----------------|
| **Signal model** | Pydantic `Signal` (simple) | Dataclass `ActivatedSignal`, `TypeSignalResult`, `CompoundSignalResult`, `SignalBuilderResult` (rich) | **HIGH** — different class hierarchies, different patterns (Pydantic vs dataclass) |
| **Hypothesis model** | Pydantic `Hypothesis` (simple) | Dataclass `Hypothesis` in `investigation_state.py` (rich: evidence, verdicts, symptoms) | **HIGH** — same name, different fields |
| **Symptom model** | Pydantic `Symptom` (template-based) | Dataclass `Symptom` in `investigation_state.py` (weight, severity, signal_strength) | **HIGH** — same name, different fields |
| **Agent factory** | `BaseAgent` + `@register_agent` decorator | Config-driven `Agent()` instantiation from JSON | **MEDIUM** — different paradigms |
| **Middleware** | `Code/Shared/middleware/` (EvalMiddleware, PromptInjectionMiddleware) | `core/middleware/` (OutputEvaluationMiddleware, PromptInjectionMiddleware) | **HIGH** — parallel implementations |
| **LLM client** | `Code/Shared/clients/chat_client.py` | `helper/llm.py` | **MEDIUM** — duplicate functionality |
| **Kusto access** | `Code/Shared/clients/kusto_client.py` via `SignalSource` | MCP tools (goes through RATIO_MCP server) | **LOW** — different patterns, can coexist |
| **Auth** | Part of shared middleware | `helper/auth.py` (richer: SSO, MCP bearer, managed identity) | **MEDIUM** — CustomerAgent's is more complete |

---

## Part 2 — Merge Plan

### 2.1 Target Structure

```
Code/
├── CustomerAgent/
│   ├── frontend/              ← Move Code/CustomerAgent/CustomerAgentUI here
│   ├── streamlit/             ← Move Code/CustomerAgent/src/UI here
│   ├── src/
│   │   ├── server/            ← FastAPI app (existing app.py)
│   │   ├── core/              ← Orchestrator, agent factory, MCP, prompt loader
│   │   │   ├── signals/       ← Signal builder pipeline
│   │   │   ├── investigation/ ← Investigation runner, hypothesis scorer, state
│   │   │   └── middleware/    ← Thin wrappers → delegates to shared
│   │   ├── a2a/               ← A2A protocol (keep as-is)
│   │   ├── helper/            ← DEPRECATED — migrate to shared, then remove
│   │   ├── config/            ← JSON configs (signals, symptoms, hypotheses, etc.)
│   │   ├── prompts/           ← Agent prompt .txt files
│   │   ├── knowledge/         ← Reference docs
│   │   └── __init__.py
│   ├── shared/                ← Symlink or copy of shared utilities needed
│   │   ├── clients/           ← kusto_client.py, chat_client.py (from Code/Shared/clients)
│   │   ├── middleware/        ← eval, PI, logging, error, security (from Code/Shared/middleware)
│   │   ├── config/            ← settings.py (from Code/Shared/config)
│   │   └── api/               ← response_utils.py (from Code/Shared/api)
│   ├── eval/                  ← Evaluation adapters/engines needed by CustomerAgent
│   │   ├── engines/           ← deepeval_engine.py (from Code/Shared/evaluation/engines)
│   │   └── interfaces/        ← base_evaluator.py (from Code/Shared/evaluation/interfaces)
│   ├── run_signal_builder.py
│   ├── run_signal_builder_loop.py
│   ├── requirements.txt
│   └── CUSTOMER_AGENT_END_TO_END_README.md
├── RATIO_MCP/                 ← Unchanged
```

### 2.2 Merge Phases (Ordered to Minimize Conflicts)

The merge is structured to touch the **fewest files at each step** and keep the system runnable between phases. Each phase is independently committable.

---

#### Phase 1: Relocate UIs (No Code Changes — Just Moves)

**Goal:** Get to the target folder structure for frontend code. No import changes needed.

| Step | Action | Source | Destination |
|------|--------|--------|-------------|
| 1.1 | Move vanilla JS UI | `Code/CustomerAgent/CustomerAgentUI/` | `Code/CustomerAgent/frontend/` |
| 1.2 | Move Streamlit UI | `Code/CustomerAgent/src/UI/` | `Code/CustomerAgent/streamlit/` |
| 1.3 | Update Streamlit imports | `from UI.sso import ...` → `from streamlit.sso import ...` | In `streamlit/app.py` only |
| 1.4 | Update `src/server/app.py` | If it references `UI/` paths, update to `../streamlit/` | Minimal |

**Files touched:** ~5 files (path adjustments only)
**Conflict risk:** NONE — moves only, no logic changes

---

#### Phase 2: Create `Code/CustomerAgent/shared/` (Copy, Not Move)

**Goal:** Bring shared utilities into CustomerAgent's tree so `helper/` can be phased out.

| Step | Action | Source → Destination |
|------|--------|----------------------|
| 2.1 | Copy Kusto client | `Code/Shared/clients/kusto_client.py` → `Code/CustomerAgent/shared/clients/kusto_client.py` |
| 2.2 | Copy chat client | `Code/Shared/clients/chat_client.py` → `Code/CustomerAgent/shared/clients/chat_client.py` |
| 2.3 | Copy Cosmos client | `Code/Shared/clients/cosmos_client.py` → `Code/CustomerAgent/shared/clients/cosmos_client.py` (if needed) |
| 2.4 | Copy config settings | `Code/Shared/config/settings.py` → `Code/CustomerAgent/shared/config/settings.py` |
| 2.5 | Copy API utils | `Code/Shared/api/response_utils.py` → `Code/CustomerAgent/shared/api/response_utils.py` |
| 2.6 | Copy middleware stack | `Code/Shared/middleware/*.py` → `Code/CustomerAgent/shared/middleware/*.py` |
| 2.7 | Add `__init__.py` files | For each new package under `shared/` |

**Why copy, not reference?** CustomerAgent needs to be self-contained for deployment. The `Code/Shared/` originals stay untouched — this is a one-way copy. Future changes to shared can be synced later via a script.

**What from `Code/Shared` goes to `Code/CustomerAgent/shared`:**

| Shared Module | Needed? | Why |
|---------------|---------|-----|
| `clients/kusto_client.py` | YES | Signal builder could optionally use direct Kusto (bypassing MCP) for dev/test |
| `clients/chat_client.py` | YES | Replace `helper/llm.py` (identical purpose, Pydantic-settings based) |
| `clients/cosmos_client.py` | MAYBE | Only if investigation results will be persisted to Cosmos |
| `config/settings.py` | YES | Centralized config management, replaces scattered `os.getenv()` |
| `api/response_utils.py` | YES | Standardized API responses for `server/app.py` |
| `middleware/eval_middleware.py` | YES | Replace `core/middleware/eval_middleware.py` |
| `middleware/prompt_injection_middleware.py` | YES | Replace `core/middleware/prompt_injection_middleware.py` |
| `middleware/logging_middleware.py` | YES | Complement `helper/agent_logger.py` |
| `middleware/error_middleware.py` | YES | Standardized error handling |
| `middleware/security_middleware.py` | YES | CORS, auth header validation |
| `middleware/__init__.py` | YES | `build_default_middleware()` convenience |

**Files touched:** ~12 new files (copies), 0 existing files modified
**Conflict risk:** NONE — additive only

---

#### Phase 3: Create `Code/CustomerAgent/eval/` (Copy)

**Goal:** Bring evaluation framework into CustomerAgent's tree.

| Step | Action | Source → Destination |
|------|--------|----------------------|
| 3.1 | Copy eval engines | `Code/Shared/evaluation/engines/*.py` → `Code/CustomerAgent/eval/engines/` |
| 3.2 | Copy eval interfaces | `Code/Shared/evaluation/interfaces/*.py` → `Code/CustomerAgent/eval/interfaces/` |
| 3.3 | Add `__init__.py` files | For `eval/`, `eval/engines/`, `eval/interfaces/` |

**What from `Code/Servers/eval` goes to `Code/CustomerAgent/eval`:**

The `Code/Servers/eval` service is a standalone FastAPI sidecar (port 8011) — it does NOT get merged into CustomerAgent. Instead:

| Eval Component | Goes to CustomerAgent? | Reason |
|----------------|----------------------|--------|
| `Code/Servers/eval/` (entire service) | NO | Stays as independent sidecar — CustomerAgent calls it via HTTP |
| `Code/Shared/evaluation/engines/deepeval_engine.py` | YES → `eval/engines/` | Needed if CustomerAgent runs inline evaluations |
| `Code/Shared/evaluation/interfaces/base_evaluator.py` | YES → `eval/interfaces/` | Interface for evaluation engines |
| `Code/Servers/eval/adapters/telemetry.py` | MAYBE | Only if CustomerAgent sends eval telemetry directly |
| `Code/Servers/eval/core/runner.py` | NO | CustomerAgent calls the eval sidecar, doesn't run its own |
| `Code/Servers/eval/models/` | NO | Request/response models for the sidecar API |

**Files touched:** ~4 new files, 0 existing files modified
**Conflict risk:** NONE — additive only

---

#### Phase 4: Migrate `helper/` → `shared/` (Import Rewiring)

**Goal:** Replace CustomerAgent's custom helpers with shared utilities. This is the **highest-risk phase** — it touches many files.

| Step | File | Change |
|------|------|--------|
| 4.1 | `helper/llm.py` → DELETE | Replace all `from helper.llm import create_chat_client` with `from shared.clients.chat_client import ...` **OR** keep `helper/llm.py` as a thin re-export wrapper (lower risk) |
| 4.2 | `helper/auth.py` → KEEP (partially) | CustomerAgent's auth is **richer** than shared (SSO, MCP bearer, managed identity). Keep the SSO and MCP-specific parts. Move generic `DefaultAzureCredential` token acquisition to use `shared/config/settings.py` for config |
| 4.3 | `helper/agent_logger.py` → KEEP | This is CustomerAgent-specific (App Insights, OTel, XCV, real-time UI queue). Not a candidate for shared. Keep as-is but have it read config from `shared/config/settings.py` instead of raw `os.getenv()` |
| 4.4 | `core/middleware/eval_middleware.py` → THIN WRAPPER | Replace implementation with delegation to `shared/middleware/eval_middleware.py`. Keep the CustomerAgent-specific `drain()` / `reset()` interface if needed, but route the actual API call through shared |
| 4.5 | `core/middleware/prompt_injection_middleware.py` → THIN WRAPPER | Same pattern as eval — delegate to shared, keep CustomerAgent-specific drain/reset |
| 4.6 | `core/middleware/llm_logging_middleware.py` → KEEP | CustomerAgent-specific (per-agent log config from agents_config.json) — no shared equivalent |
| 4.7 | `core/middleware/tool_capture_middleware.py` → KEEP | CustomerAgent-specific (tool call capture for UI streaming) — no shared equivalent |

**Recommended approach:** Create `helper/__init__.py` re-exports that point to the new shared locations. This avoids changing every import in every file at once:

```python
# helper/llm.py (becomes a re-export shim)
from shared.clients.chat_client import create_chat_client  # noqa: F401
```

**Files touched:** ~7 files modified (helper modules + middleware wrappers)
**Conflict risk:** MEDIUM — import chain changes, but shim approach limits blast radius

---

#### Phase 5: Extract Base Interfaces from Domain Models into `core/models/`

**Goal:** Extract clean Pydantic `BaseModel` interfaces from the rich dataclass domain models in `Code/CustomerAgent/src/core/`. The domain models stay where they are (they are the runtime workhorses); the new `core/models/` package provides serializable, validated contracts used by the API layer, persistence, and cross-boundary communication.

**Strategy:** For each major domain dataclass, create a matching Pydantic interface model with only the **core identity + state fields** (no lifecycle methods, no internal bookkeeping). The domain dataclasses then gain a `to_model()` → Pydantic and `from_model()` → dataclass pair for boundary conversion.

`Code/Servers/agents/models/` is **not touched** — it stays as the general-purpose agent service's API surface. CustomerAgent owns its own model interfaces.

##### New package: `Code/CustomerAgent/src/core/models/`

```
core/models/
├── __init__.py                  ← re-exports all interface models + enums
├── signal_models.py             ← ActivatedSignalModel, TypeSignalResultModel,
│                                   CompoundSignalResultModel, SignalBuilderResultModel
├── symptoms.py                  ← SymptomModel
├── hypothesis.py                ← HypothesisModel, EvidenceItemModel,
│                                   EvidenceRequirementModel, InvestigationContextModel,
│                                   StreamEventModel
├── investigation.py             ← InvestigationModel (imports from symptoms.py & hypothesis.py)
└── enums.py                     ← InvestigationPhase, HypothesisStatus, EvidenceVerdict,
                                    SymptomVerdict (shared by both layers)
```

##### Extraction mapping

| Source (dataclass) | Location | Interface (Pydantic) | New File |
|--------------------|----------|----------------------|----------|
| `ActivatedSignal` | `core/signals/signal_models.py` | `ActivatedSignalModel` | `core/models/signal_models.py` |
| `TypeSignalResult` | `core/signals/signal_models.py` | `TypeSignalResultModel` | `core/models/signal_models.py` |
| `CompoundSignalResult` | `core/signals/signal_models.py` | `CompoundSignalResultModel` | `core/models/signal_models.py` |
| `SignalBuilderResult` | `core/signals/signal_models.py` | `SignalBuilderResultModel` | `core/models/signal_models.py` |
| `Symptom` | `core/investigation/investigation_state.py` | `SymptomModel` | `core/models/symptoms.py` |
| `Hypothesis` | `core/investigation/investigation_state.py` | `HypothesisModel` | `core/models/hypothesis.py` |
| `EvidenceItem` | `core/investigation/investigation_state.py` | `EvidenceItemModel` | `core/models/hypothesis.py` |
| `EvidenceRequirement` | `core/investigation/investigation_state.py` | `EvidenceRequirementModel` | `core/models/hypothesis.py` |
| `InvestigationContext` | `core/investigation/investigation_state.py` | `InvestigationContextModel` | `core/models/hypothesis.py` |
| `Investigation` | `core/investigation/investigation_state.py` | `InvestigationModel` | `core/models/investigation.py` |
| `StreamEvent` | `core/investigation/investigation_state.py` | `StreamEventModel` | `core/models/hypothesis.py` |
| `InvestigationPhase` | `core/investigation/investigation_state.py` | (move to shared) | `core/models/enums.py` |
| `HypothesisStatus` | `core/investigation/investigation_state.py` | (move to shared) | `core/models/enums.py` |
| `EvidenceVerdict` | `core/investigation/investigation_state.py` | (move to shared) | `core/models/enums.py` |
| `SymptomVerdict` | `core/investigation/investigation_state.py` | (move to shared) | `core/models/enums.py` |

##### What the interface models look like (example)

```python
# core/models/symptoms.py
from pydantic import BaseModel, Field
from typing import Any

class SymptomModel(BaseModel):
    id: str
    template_id: str
    text: str
    category: str
    source_signal_type: str = ""
    weight: int = 1
    severity: str = ""
    signal_strength: float = 0.0
    confirmed: bool = False
    entities: dict[str, Any] = Field(default_factory=dict)
```

```python
# core/models/hypothesis.py
from pydantic import BaseModel, Field
from typing import Any
from core.models.enums import HypothesisStatus, EvidenceVerdict, SymptomVerdict
from core.models.symptoms import SymptomModel

class HypothesisModel(BaseModel):
    id: str
    template_id: str
    statement: str
    category: str
    status: HypothesisStatus = HypothesisStatus.ACTIVE
    confidence: float = 0.0
    match_score: float = 0.0
    expected_symptoms: list[str] = Field(default_factory=list)
    matched_symptoms: list[str] = Field(default_factory=list)
    evidence_needed: list[str] = Field(default_factory=list)
    evidence_collected: list[str] = Field(default_factory=list)
    verdicts: dict[str, EvidenceVerdict] = Field(default_factory=dict)
    symptom_verdicts: dict[str, SymptomVerdict] = Field(default_factory=dict)
    determination: str = ""

```

```python
# core/models/investigation.py
from pydantic import BaseModel, Field
from typing import Any
from core.models.enums import InvestigationPhase
from core.models.symptoms import SymptomModel
from core.models.hypothesis import (
    HypothesisModel,
    EvidenceItemModel,
    EvidenceRequirementModel,
    InvestigationContextModel,
)

class InvestigationModel(BaseModel):
    id: str
    phase: InvestigationPhase = InvestigationPhase.INITIALIZING
    context: InvestigationContextModel = Field(default_factory=InvestigationContextModel)
    symptoms: list[SymptomModel] = Field(default_factory=list)
    hypotheses: list[HypothesisModel] = Field(default_factory=list)
    evidence_plan: list[EvidenceRequirementModel] = Field(default_factory=list)
    evidence: list[EvidenceItemModel] = Field(default_factory=list)
    findings: list[str] = Field(default_factory=list)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    evidence_cycles: int = 0
    started_at: str = ""
    completed_at: str = ""
```

##### Steps

| Step | Action |
|------|--------|
| 5.1 | Create `core/models/enums.py` — **move** (not copy) the 4 enum classes (`InvestigationPhase`, `HypothesisStatus`, `EvidenceVerdict`, `SymptomVerdict`) here. Update `investigation_state.py` to import from `core.models.enums` instead of defining them locally. This eliminates duplication and gives both layers a single source of truth for enum values |
| 5.2 | Create `core/models/signal_models.py` — Pydantic `BaseModel` interfaces extracted from `core/signals/signal_models.py`. Include only the data fields (no `to_dict()` methods — Pydantic handles serialization). Keep the dataclass originals untouched |
| 5.3 | Create `core/models/symptoms.py` — Pydantic `BaseModel` interface for `SymptomModel`, extracted from `core/investigation/investigation_state.py`. Standalone file so symptoms can be imported independently without pulling in the full investigation model graph |
| 5.4 | Create `core/models/hypothesis.py` — Pydantic `BaseModel` interfaces for `HypothesisModel`, `EvidenceItemModel`, `EvidenceRequirementModel`, `InvestigationContextModel`, `StreamEventModel`. Imports `SymptomModel` from `core.models.symptoms` for composition |
| 5.5 | Create `core/models/investigation.py` — Pydantic `BaseModel` for `InvestigationModel` only. Imports `SymptomModel` from `symptoms.py`, `HypothesisModel` / `EvidenceItemModel` / `EvidenceRequirementModel` / `InvestigationContextModel` from `hypothesis.py`. This is the top-level aggregate that composes all other models, so it lives in its own file to keep the dependency graph clean |
| 5.6 | Create `core/models/__init__.py` — re-export all models and enums for convenient imports |
| 5.7 | Add `to_model()` methods to the domain dataclasses that return the corresponding Pydantic interface (e.g., `Symptom.to_model() → SymptomModel`). Add `@classmethod from_model()` for the reverse direction |
| 5.8 | Update `server/app.py` response schemas to use the new Pydantic models from `core/models/` instead of raw dicts |

**Files created:** 6 new files (`core/models/__init__.py`, `enums.py`, `signal_models.py`, `symptoms.py`, `hypothesis.py`, `investigation.py`)
**Files modified:** 2 files (`core/investigation/investigation_state.py` — enum imports; `core/signals/signal_models.py` — add `to_model()`)
**Files in `Code/Servers/agents/models/`:** NOT TOUCHED
**Conflict risk:** LOW — new package is additive; only change to existing files is moving enum definitions to a shared location and adding conversion methods

---

#### Phase 6: Rewrite Signal Sources to Use SignalSourceFactory → KustoSignalSource → MCP

**Goal:** Replace the current ad-hoc MCP tool calls in `signal_builder.py` with a structured `SignalSourceFactory → KustoSignalSource` pipeline where `KustoSignalSource` itself executes queries **through MCP** (not direct Kusto SDK). This gives us the clean abstraction layer from the dev plan while keeping MCP as the sole data-fetching mechanism.

**Strategy:** MCP-only — no direct Kusto SDK, no feature flags. The service layer becomes a structured wrapper around MCP calls.

##### Architecture

```
signal_builder.py
    │
    ▼
SignalSourceFactory.create(config)     ← creates the right source from JSON config
    │
    ▼
KustoSignalSource.fetch_signals()      ← structured interface (ABC contract)
    │
    ▼
MCPStreamableHTTPTool.call_tool()      ← actual data fetch via MCP server
    │
    ▼
RATIO MCP Server → Kusto cluster       ← MCP server handles auth + query execution
```

##### Steps

| Step | Action |
|------|--------|
| 6.1 | **Copy** `SignalSource` ABC, `KustoSignalSource`, and `SignalSourceFactory` from `Code/Servers/agents/services/signalSource/` into `Code/CustomerAgent/src/core/services/signalSource/` |
| 6.2 | **Rewrite `KustoSignalSource.fetch_signals()`** — replace the `query_kql_async` call (direct Kusto SDK) with a call to `core.mcp_integration.create_filtered_mcp_tool()` → `call_tool()`. The MCP tool name and parameters come from the signal source config. This keeps the `SignalSource` ABC contract intact while routing all queries through MCP |
| 6.3 | **Update `SignalSourceFactory`** — inject the MCP server URL (from env/config) when constructing `KustoSignalSource` instances, so each source knows how to reach the MCP server |
| 6.4 | **Refactor `signal_builder.py`** — replace the inline `_call_collection_tool()` function and per-signal-type MCP calls with `SignalSourceFactory.create_all(signal_sources_config)` → loop over sources → `source.fetch_signals(params)`. The activation rule evaluation, strength computation, and compound signal logic stay unchanged |
| 6.5 | **Move signal source JSON config** — the MCP tool names, parameters, and field mappings currently scattered in `config/signals/signal_template.json` should map cleanly into the `SignalSourceFactory` config format. Update the JSON structure if needed, or add a thin adapter |
| 6.6 | Add `__init__.py` for `core/services/` and `core/services/signalSource/` packages |

##### Example: Rewritten KustoSignalSource

```python
# core/services/signalSource/kusto_signal_source.py
class KustoSignalSource(SignalSource):
    def __init__(self, tool_name: str, params: dict, field_mappings: dict, ...):
        self.tool_name = tool_name       # MCP tool name (e.g. "query_sr_volume")
        self.params = params             # default query parameters
        self.field_mappings = field_mappings
        self._mcp_tool = None            # lazy-initialized

    async def fetch_signals(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        merged = {**self.params, **params}
        mcp_tool = create_filtered_mcp_tool("signal_builder", [self.tool_name])
        await mcp_tool.connect()
        try:
            result = await mcp_tool.call_tool(self.tool_name, **merged)
            rows = _parse_mcp_result(result)
            return rows
        finally:
            await mcp_tool.close()
```

##### What from `Code/Servers/agents` gets copied into `Code/CustomerAgent/src`

| Component | Action | Reason |
|-----------|--------|--------|
| `services/signalSource/signal_source.py` | COPY → `core/services/signalSource/` | ABC contract — keeps the interface clean |
| `services/signalSource/kusto_signal_source.py` | COPY + REWRITE | Replace `query_kql_async` internals with MCP calls |
| `services/signalSource/signal_source_factory.py` | COPY + UPDATE | Inject MCP config when creating sources |
| `agents/*` | NO | Not applicable — CustomerAgent has its own agent system |
| `models/*` | NO | CustomerAgent has its own models (Phase 5) |
| `tools/*` | NO | Replaced by the MCP-backed SignalSource layer |
| `workflows/*`, `app_kernel.py`, `providers/` | NO | Not applicable |

**Files created:** 4 new files (`core/services/__init__.py`, `core/services/signalSource/__init__.py`, `signal_source.py`, `kusto_signal_source.py`, `signal_source_factory.py`)
**Files modified:** 1 file (`core/signals/signal_builder.py` — replace inline MCP calls with factory pattern)
**Conflict risk:** MEDIUM — `signal_builder.py` is the core pipeline file, but the change is a clean refactor: same MCP calls, structured behind an abstraction layer

---

#### Phase 7: Update `Code/scripts/start_all.ps1`

**Goal:** Add CustomerAgent's FastAPI server and frontend to the dev startup script.

| Step | Action |
|------|--------|
| 7.1 | Add CustomerAgent server job (port 8503) |
| 7.2 | Add CustomerAgent frontend dev server (if applicable — vanilla JS may just be static files) |
| 7.3 | Add Streamlit UI job (port 8501) |
| 7.4 | Add port 8503 and 8501 to the stop-existing-services section |

**New entries in `start_all.ps1`:**

```powershell
# 5. CustomerAgent server (port 8503)
$jobs += Start-Job -Name "customer-agent" -ScriptBlock {
    param($root, $venv)
    Set-Location (Join-Path $root "Code\CustomerAgent\src")
    & $venv
    $env:PYTHONPATH = (Join-Path $root "Code\CustomerAgent\src")
    python -m uvicorn server.app:app --host 127.0.0.1 --port 8503 2>&1
} -ArgumentList $ROOT, $VENV

# 6. CustomerAgent Streamlit UI (port 8501)
$jobs += Start-Job -Name "customer-agent-ui" -ScriptBlock {
    param($root, $venv)
    Set-Location (Join-Path $root "Code\CustomerAgent")
    & $venv
    $env:PYTHONPATH = (Join-Path $root "Code\CustomerAgent\src")
    python -m streamlit run streamlit/app.py --server.port 8501 2>&1
} -ArgumentList $ROOT, $VENV
```

**Files touched:** 1 file modified (`start_all.ps1`)
**Conflict risk:** LOW — additive changes at end of script

---

#### Phase 8: Cleanup & Verification

| Step | Action |
|------|--------|
| 8.1 | Delete `helper/llm.py` if fully replaced by shim in Phase 4 |
| 8.2 | Run all existing tests: `pytest Code/Tests/ -v` |
| 8.3 | Run CustomerAgent server: `python -m uvicorn server.app:app` and verify `/health` |
| 8.4 | Run signal builder: `python run_signal_builder.py` and verify it completes |
| 8.5 | Run Streamlit UI and verify SSO + chat flow works |
| 8.6 | Verify `start_all.ps1` launches all services |

---

### 2.3 Phase Execution Order (Conflict-Minimizing)

```
Phase 1 (UI moves)          ← 0 conflicts, safe to commit alone
    │
Phase 2 (shared/ copy)      ← 0 conflicts, additive only
    │
Phase 3 (eval/ copy)        ← 0 conflicts, additive only
    │
Phase 4 (helper → shared)   ← MEDIUM risk, import rewiring
    │
Phase 5 (model alignment)   ← LOW risk, additive fields
    │
Phase 6 (services wire-up)  ← LOW risk, feature-flagged
    │
Phase 7 (start_all.ps1)     ← LOW risk, additive
    │
Phase 8 (cleanup + verify)  ← Validation only
```

**Phases 1-3 are zero-conflict** and can be done as a single PR.
**Phase 4** is the riskiest — consider a separate PR for review.
**Phases 5-7** are low-risk and can be batched into one PR.
**Phase 8** is validation only.

### 2.4 What NOT to Merge

These items from `Code/Servers/agents/` should **not** be merged into CustomerAgent — they belong to the general-purpose agent service:

1. `agents/manager_agent.py`, `planner_agent.py`, `generic_agent.py`, `data_analyst_agent.py`, `human_agent.py` — these are the original RATIO-AI agents, not related to CustomerAgent
2. `agents/base_agent.py` and `agents/agent_factory.py` — CustomerAgent uses a fundamentally different agent creation pattern (config-driven MAF `Agent` vs. `BaseAgent` subclasses)
3. `tools/kusto_tools.py` and `tools/general_tools.py` — CustomerAgent uses MCP tools
4. `workflows/workflows.py` — CustomerAgent has its own orchestration
5. `providers/` — CustomerAgent has its own provider setup
6. `app_kernel.py` and `app_config.py` — CustomerAgent has its own server
7. The Pydantic models in `models/` — they stay in Servers as API contracts; CustomerAgent uses its own richer dataclass models internally

### 2.5 Risk Summary

| Phase | Risk | Mitigation |
|-------|------|------------|
| 1 — UI moves | None | Pure file moves |
| 2 — shared copy | None | Additive only |
| 3 — eval copy | None | Additive only |
| 4 — helper migration | **Medium** | Use re-export shims to limit blast radius; keep `agent_logger.py` unchanged |
| 5 — model alignment | Low | Additive optional fields only |
| 6 — services wire-up | Low | Feature-flagged, opt-in |
| 7 — start_all.ps1 | Low | Additive entries |
| 8 — cleanup | None | Validation only |

### 2.6 Suggested PR Strategy

| PR | Phases | Description |
|----|--------|-------------|
| **PR 1** | 1 + 2 + 3 | "CustomerAgent: restructure folders and add shared/eval copies" |
| **PR 2** | 4 | "CustomerAgent: migrate helpers to shared utilities" |
| **PR 3** | 5 + 6 + 7 | "CustomerAgent: align models, wire services, update start script" |
| **PR 4** | 8 | "CustomerAgent: cleanup and verification" |
