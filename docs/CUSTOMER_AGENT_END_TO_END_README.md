# RATIO Customer Agent — Technical Design Document

> **Proactive customer health monitoring and automated investigation pipeline**
> built on Microsoft Agent Framework (MAF).

---

## Table of Contents

1. [System Overview](#system-overview)
2. [Architecture at a Glance](#architecture-at-a-glance)
3. [End-to-End Pipeline Flow](#end-to-end-pipeline-flow)
4. [Stage 0 — Monitoring Context & Configuration](#stage-0--monitoring-context--configuration)
5. [Stage 1 — Signal Builder (Deterministic)](#stage-1--signal-builder-deterministic)
6. [Stage 2 — Triage Agent (LLM)](#stage-2--triage-agent-llm)
7. [Stage 3 — Hypothesis Scoring (Deterministic)](#stage-3--hypothesis-scoring-deterministic)
8. [Stage 4 — Investigation GroupChat (LLM Multi-Agent)](#stage-4--investigation-groupchat-llm-multi-agent)
9. [Stage 5 — Action Planning & Notification](#stage-5--action-planning--notification)
10. [Configuration-Driven Design](#configuration-driven-design)
11. [Agent Roster](#agent-roster)
12. [Middleware Stack](#middleware-stack)
13. [MCP Integration](#mcp-integration)
14. [Streaming Pipeline & SSE Architecture](#streaming-pipeline--sse-architecture)
15. [Debug UI & Service Filter](#debug-ui--service-filter)
16. [Observability & Telemetry](#observability--telemetry)
17. [Entry Points](#entry-points)
18. [Project Structure](#project-structure)
19. [GroupChat Quality Improvements](#groupchat-quality-improvements)
    - [Phase 1 — Config Tuning & Instruction Accumulation Fix](#phase-1--config-tuning--instruction-accumulation-fix-rc-1-rc-2)
    - [Phase 2 — Structured Collector Output & Negative Examples](#phase-2--structured-collector-output--negative-examples-rc-4-rc-5)
    - [Phase 3 — OutputFormatMiddleware Pre-Emission Validation](#phase-3--outputformatmiddleware-pre-emission-validation-rc-3)
    - [Phase 4 — Context Folding (CompactionStrategy)](#phase-4--context-folding-compactionstrategy-rc-1)
    - [Phase 5 — Post-Implementation Fixes](#phase-5--post-implementation-fixes)

---

## System Overview

The Customer Agent is a **hybrid AI pipeline** that combines deterministic signal
processing with LLM-powered multi-agent investigation to proactively detect,
diagnose, and act on customer health issues — before the customer notices.

**Key design principle:** Deterministic logic handles what can be computed
exactly (signal activation, hypothesis scoring); LLMs handle what requires
reasoning (symptom matching, evidence evaluation, root-cause determination).

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        RATIO Customer Agent                             │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │   Signal      │───▶│   Triage     │───▶│  Hypothesis Scoring     │  │
│  │   Builder     │    │   (LLM)      │    │  (Deterministic)        │  │
│  │(Deterministic)│    └──────────────┘    └────────────┬─────────────┘  │
│  └──────────────┘                                      │               │
│        ▲                                               ▼               │
│   MCP Tools                              ┌──────────────────────────┐  │
│   (Kusto)                                │  Investigation GroupChat │  │
│                                          │  (LLM Multi-Agent)       │  │
│                                          │  ┌──────┐ ┌──────────┐  │  │
│                                          │  │Planner│ │Collectors│  │  │
│                                          │  └──┬───┘ └────┬─────┘  │  │
│                                          │     │          │        │  │
│                                          │  ┌──▼──────────▼─────┐  │  │
│                                          │  │     Reasoner      │  │  │
│                                          │  └────────┬──────────┘  │  │
│                                          │           │             │  │
│                                          │  ┌────────▼──────────┐  │  │
│                                          │  │  Action Planner   │  │  │
│                                          │  └───────────────────┘  │  │
│                                          └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Architecture at a Glance

```
                    ┌──────────────────────────────────────────────┐
                    │           Monitoring Context                  │
                    │  (customer, service_tree_id, lookback)       │
                    └────────────────────┬─────────────────────────┘
                                         │
                    ┌────────────────────▼─────────────────────────┐
                    │         SIGNAL BUILDER (Deterministic)        │
                    │                                               │
                    │  For each signal type:                        │
                    │    1. Call MCP collection tools (Kusto)       │
                    │    2. Normalize rows (PascalCase → snake_case)│
                    │    3. Group by granularity dimensions         │
                    │    4. Evaluate activation rules               │
                    │    5. Compute signal strength (formula)       │
                    │  Then:                                        │
                    │    6. Evaluate compound signals               │
                    │    7. Decision: quiet / watchlist / invoke    │
                    └────────────────────┬─────────────────────────┘
                                         │
                          action = "invoke_group_chat"
                                         │
                    ┌────────────────────▼─────────────────────────┐
                    │      INVESTIGATION PIPELINE (Hybrid)          │
                    │                                               │
                    │  Phase 1: TRIAGE ──────────────── (LLM)      │
                    │    ├─ Match signals → symptom templates       │
                    │    └─ Confirm symptoms, assign severity       │
                    │                                               │
                    │  Phase 2: HYPOTHESIZING ────── (Deterministic)│
                    │    ├─ Score hypotheses by symptom overlap     │
                    │    └─ Rank by match_score (weighted strength) │
                    │                                               │
                    │  Phase 3: PLANNING ────────────── (LLM)      │
                    │    └─ Map evidence_needed → collector agents  │
                    │                                               │
                    │  Phase 4: COLLECTING ─────────── (LLM+Tools) │
                    │    ├─ SLI Collector    → MCP tools            │
                    │    ├─ Incident Collector → MCP tools          │
                    │    └─ Support Collector → MCP tools           │
                    │                                               │
                    │  Phase 5: REASONING ──────────── (LLM)       │
                    │    ├─ Evaluate evidence vs hypothesis         │
                    │    └─ Verdict: CONFIRMED / CONTRIBUTING /     │
                    │       REFUTED / needs_more_evidence           │
                    │                                               │
                    │  Phase 6: ACTING ─────────────── (LLM)       │
                    │    └─ Select actions from Action Catalog      │
                    │                                               │
                    │  Phase 7: NOTIFYING ──────────── (LLM)       │
                    │    └─ investigation_resolved signal           │
                    └──────────────────────────────────────────────┘
```

---

## End-to-End Pipeline Flow

The complete pipeline is a **7-stage process** from raw telemetry data to
actionable remediation. This section walks through every stage with the
decision points and data transformations at each boundary.

```
┌────────────┐     ┌────────────┐     ┌──────────────┐     ┌───────────┐
│ Monitoring │────▶│  Signal    │────▶│  Triage      │────▶│ Hypothesis│
│ Context    │     │  Builder   │     │  Agent (LLM) │     │ Scorer    │
│            │     │            │     │              │     │           │
│ customer,  │     │ MCP calls, │     │ signals →    │     │ symptoms ×│
│ service,   │     │ activation │     │ symptoms     │     │ templates │
│ lookback   │     │ rules,     │     │              │     │ = ranked  │
│            │     │ strength   │     │              │     │ hypotheses│
└────────────┘     └──────┬─────┘     └──────────────┘     └─────┬─────┘
                          │                                       │
               ┌──────────▼──────────┐                           │
               │ Decision Gate       │                           │
               │                     │                           │
               │ invoke_group_chat ──┼───────────────────────────┘
               │ watchlist ──────────┼──▶ (log only, no investigation)
               │ quiet ──────────────┼──▶ (no action)
               └─────────────────────┘

         ┌───────────────────────────────────────────────────────────┐
         │              INVESTIGATION GROUPCHAT                       │
         │                                                           │
         │  ┌──────────────┐     ┌──────────────┐                   │
         │  │  Evidence     │────▶│  Collector   │──── MCP Tools    │
         │  │  Planner      │     │  Sub-Agents  │     (Kusto)      │
         │  └──────────────┘     └──────┬───────┘                   │
         │                              │                            │
         │                     ┌────────▼────────┐                   │
         │                     │    Reasoner     │                   │
         │                     │                 │                   │
         │                     │ CONFIRMED ──────┼──▶ Action Planner│
         │                     │ CONTRIBUTING ───┼──▶ Action Planner│
         │                     │ REFUTED ────────┼──▶ Next Hypothesis│
         │                     │ needs_more ─────┼──▶ Evidence Planner│
         │                     └─────────────────┘   (max 2 cycles) │
         └───────────────────────────────────────────────────────────┘
```

---

## Stage 0 — Monitoring Context & Configuration

Before any processing begins, the system loads its configuration from JSON
files that define **what to monitor**, **what signals to look for**, and
**what constitutes a problem**.

### Monitoring Context (`config/monitoring_context.json`)

Defines the monitoring targets — which customers and services to watch:

```json
{
  "poll_interval_minutes": 10,
  "max_concurrent_investigations": 5,
  "lookback_hours": "4h",
  "targets": [
    {
      "customer_name": "BlackRock, Inc",
      "service_tree_ids": [
        {
          "id": "49c39e84-...",
          "name": "ScaleSet Platform and Solution",
          "support_product_names": ["Azure Virtual Machine - Linux", ...],
          "owning_tenant_names": ["ScaleSet Platform and Solution", "WACAP"]
        }
      ]
    }
  ]
}
```

Each target generates an independent evaluation cycle. Multiple targets run
in parallel with bounded concurrency (`max_concurrent_investigations`).

### Configuration Hierarchy

```
config/
├── monitoring_context.json        ← WHO to monitor (customers, services)
├── agents/
│   └── agents_config.json         ← Agent definitions (names, models, tools, prompts)
├── signals/
│   └── signal_template.json       ← WHAT to detect (signal types, activation rules)
├── symptoms/
│   ├── sli_breach.json            ← Symptom templates for SLI signals
│   ├── outage_exposure.json       ← Symptom templates for outage signals
│   ├── support_tickets.json       ← Symptom templates for support signals
│   └── dependency_degradation.json← Symptom templates for dependency signals
├── hypotheses/
│   ├── scoring_config.json        ← Scoring formula parameters (weights, thresholds, modifiers)
│   ├── sli_hypotheses.json        ← Hypothesis templates for SLI root causes
│   ├── outage_hypotheses.json     ← Hypothesis templates for outage root causes
│   ├── dependency_hypotheses.json ← Hypothesis templates for dependency failures
│   └── risk_hypotheses.json       ← Hypothesis templates for proactive risks
├── evidence/
│   └── evidence_requirements.json ← Evidence needed to confirm/refute hypotheses
├── actions/
│   └── action_catalog.json        ← Available remediation actions
└── dependency_services/
    ├── dependency_mappings.json   ← Primary → dependency service relationships
    ├── xstore.json                ← Dependency service definitions
    ├── azure_allocator.json
    └── ...
```

---

## Stage 1 — Signal Builder (Deterministic)

**File:** `src/core/services/signals/signal_builder.py`
**Nature:** Fully deterministic — no LLM involved.

The Signal Builder is a programmatic pipeline that polls telemetry data via
MCP tools, evaluates activation rules, and decides whether an investigation
is warranted. It runs either as a **one-shot** (`run_signal_builder.py`) or
on a **continuous timer** (`run_signal_builder_loop.py`).

**Parallelism:** Signal type evaluation, dependency-service scans, and
individual MCP collection-tool calls all run concurrently via
`asyncio.gather()`. A global `asyncio.Semaphore` (configurable via
`max_concurrent_mcp_calls` in `signal_template.json`, default **5**) caps
the number of simultaneous MCP/Kusto calls to avoid overwhelming the
source system. Failures in any single tool or signal type are logged and
gracefully degraded — the remaining results are still processed.

### Signal Types

Each signal type represents a category of health degradation:

| Signal Type | Name | Data Source | What It Detects |
|-------------|------|-------------|-----------------|
| `SIG-TYPE-1` | SLI Breach Detected | SLI monitoring (Kusto) | Service Level Indicator violations |
| `SIG-TYPE-2` | Support Ticket Surge | Support cases (Kusto) | Abnormal support case patterns |
| `SIG-TYPE-3` | Outage/Incident Exposure | IcM incidents (Kusto) | Active outages affecting customer |
| `SIG-TYPE-4` | Dependency Service Degradation | Multi-service scan (Kusto) | Upstream dependency failures |

### Processing Pipeline Per Signal Type

```
┌────────────────────────────────────────────────────────────────────┐
│                    Per Signal Type Evaluation                       │
│                                                                    │
│  1. COLLECT DATA                                                   │
│     ├─ Call MCP collection tools with monitoring context params     │
│     ├─ Parse JSON response → rows                                  │
│     └─ Normalize field names (PascalCase → snake_case + original)  │
│                                                                    │
│  2. GROUP BY GRANULARITY                                           │
│     ├─ subscription_region: per-subscription + region              │
│     ├─ cross_region: same SLI across ≥2 regions                   │
│     ├─ cross_subscription: same SLI across ≥2 subscriptions       │
│     ├─ multi_sli: ≥2 distinct SLIs in same subscription + region  │
│     └─ (signal type 4): per-dependency service, filtered to       │
│        customer regions                                            │
│                                                                    │
│  3. EVALUATE ACTIVATION RULES (pluggable registry)                 │
│     ├─ {field}_min: N        → value >= N                          │
│     ├─ {field}_max: N        → value <= N                          │
│     ├─ {field}_present: true → value is non-empty                  │
│     ├─ {field}_regex: pattern→ regex match against string value    │
│     ├─ {field}_in_range: [lo,hi] → lo <= value <= hi               │
│     ├─ {field}: true/false   → boolean / equality match            │
│     ├─ {field}_or_severity_increased → escalation detection        │
│     ├─ any: [...rules]       → OR composite (at least one passes)  │
│     ├─ all: [...rules]       → AND composite (all must pass)       │
│     └─ Custom: register_rule_evaluator(suffix, fn)                 │
│                                                                    │
│  4. COMPUTE RAW STRENGTH (per activated granularity)               │
│     └─ Formula evaluation with safe math:                          │
│        Example: impacted_resources × log2(1 + duration/5) ×       │
│                 (100 - avg_value) / 100                            │
│                                                                    │
│  5. NORMALIZE TO 0–5 SCALE                                        │
│     └─ strength = min(raw / max_raw_strength, 1.0) × 5.0          │
│        Activated signals get a floor of 0.5 (always registers)    │
│        max_raw_strength is defined per granularity in config       │
│                                                                    │
│  6. AGGREGATE                                                      │
│     ├─ Standard: max_strength across all granularities (now 0–5)   │
│     ├─ best_confidence (Low → Medium → High → Highest)             │
│     └─ pre_aggregated:<Field>: reads value directly from first     │
│        row (used when Kusto query already returns aggregated data) │
│        e.g. "pre_aggregated:DistinctCustomerCount"                 │
└────────────────────────────────────────────────────────────────────┘
```

### Granularity System

Signals are evaluated at multiple **granularity levels** to detect patterns
at different scopes. Higher granularities carry higher confidence because
they indicate broader, more systemic issues:

```
                          Confidence
                              ▲
              Highest ────────┤  multi_customer_same_region_sli
                              │  (same SLI failing across multiple customers)
                              │
                 High ────────┤  cross_region, cross_subscription, multi_sli
                              │  (patterns across boundaries)
                              │
               Medium ────────┤  subscription_region
                              │  (single scope — could be noise)
                              │
                  Low ────────┤  (no activation)
                              └──────────────────────────────────▶ Scope
```

### Compound Signal Evaluation

After individual signal types are evaluated, **compound signals** detect
correlations across signal types (e.g., SLI breach + active outage):

```
┌──────────────────────────────────────────────────────────────┐
│                  Compound Signal Evaluation                    │
│                                                              │
│  Input: TypeSignalResult per signal type                     │
│                                                              │
│  Rule: "If ≥2 of [SIG-TYPE-1, SIG-TYPE-3, SIG-TYPE-4]      │
│          have data, activate compound signal"                │
│                                                              │
│  Strength = min(avg(type strengths) × multiplier, 5.0)       │
│                                                              │
│  Example:                                                    │
│    SIG-TYPE-1 (SLI breach) strength=3.8 (High)              │
│    SIG-TYPE-3 (outage)     strength=2.5 (Moderate)          │
│    Compound = min(avg(3.8, 2.5) × 1.5, 5.0)                │
│            = min(3.15 × 1.5, 5.0) = 4.7 (Critical)         │
└──────────────────────────────────────────────────────────────┘
```

### Decision Gate (Config-Driven)

The final decision is deterministic, driven by an ordered array of
`decision_rules` in `signal_template.json`. Rules are evaluated **top-down,
first match wins**. If no rule matches, the action defaults to `"quiet"`.

```json
"decision_rules": [
  {
    "condition": {"any_signal_strength_gte": 2.5},
    "action": "invoke_group_chat",
    "description": "Any signal type with strength >= 2.5 triggers full investigation."
  },
  {
    "condition": {"any_compound_activated": true},
    "action": "invoke_group_chat",
    "description": "Any activated compound signal triggers full investigation."
  },
  {
    "condition": {"any_activated_signals": true},
    "action": "watchlist",
    "description": "Weak signals present but below thresholds — add to watchlist."
  }
]
```

```
                    ┌──────────────────────────┐
                    │    decision_rules[]       │
                    │    (first match wins)     │
                    └────────────┬─────────────┘
                                 │
              ┌──────────────────┼──────────────────┐
              │                  │                   │
     ┌────────▼──────┐  ┌───────▼───────┐  ┌───────▼───────┐
     │ invoke_group_ │  │   watchlist   │  │     quiet     │
     │ chat          │  │              │  │               │
     │               │  │ Weak signals │  │ No rule       │
     │ strength≥2.5  │  │ activated    │  │ matched       │
     │ OR compound   │  │              │  │               │
     │ activated     │  │              │  │               │
     └───────┬───────┘  └──────────────┘  └───────────────┘
             │
             ▼
    Trigger Investigation Pipeline
```

#### Supported Condition Types

| Condition Key | Value | Meaning |
|---------------|-------|--------|
| `any_signal_strength_gte` | float | Any signal type has max_strength ≥ value |
| `any_compound_activated` | bool | Any compound signal is activated |
| `any_activated_signals` | bool | Any signal type has activated signals |
| `signal_type_strength_gte` | `{"signal_type_id": "...", "min_strength": N}` | Specific signal type has strength ≥ N |
| `min_activated_types` | int | At least N distinct signal types activated |
| `all` | `[...conditions]` | AND composite — all sub-conditions must be true |
| `any` | `[...conditions]` | OR composite — at least one sub-condition must be true |

New condition types can be added by extending `_match_decision_condition()`
in `signal_builder.py`.

### Score Normalization (0–5 Scale)

All signal and hypothesis scores are normalized to a **unified 0–5 scale**,
providing consistent, human-interpretable severity across all pipeline stages.

#### Semantic Labels

| Score | Label | Meaning |
|-------|-------|--------|
| 0 | None | No signal detected |
| 1 | Low | Minimal impact, likely noise |
| 2 | Moderate | Noticeable impact, monitor closely |
| 3 | Significant | Clear issue, investigation warranted |
| 4 | High | Major impact, immediate attention needed |
| 5 | Critical | Severe, widespread impact |

#### How Normalization Works

```
┌──────────────────────────────────────────────────────────────────────┐
│                  Signal Strength Normalization                        │
│                                                                      │
│  Each granularity in signal_template.json defines a max_raw_strength │
│  representing the p95 expected raw value for that formula.           │
│                                                                      │
│  Formula:                                                            │
│    normalized = min(raw_strength / max_raw_strength, 1.0) × 5.0     │
│    if normalized > 0: normalized = max(normalized, 0.5)  ← floor    │
│                                                                      │
│  Examples:                                                           │
│    SLI breach (subscription_region): max_raw=30                      │
│      raw=15  → min(15/30, 1) × 5 = 2.5 (Moderate)                  │
│      raw=30  → min(30/30, 1) × 5 = 5.0 (Critical)                  │
│      raw=60  → min(60/30, 1) × 5 = 5.0 (Critical — capped)        │
│      raw=0.5 → min(0.5/30, 1) × 5 = 0.5 (Low — floored)           │
│                                                                      │
│    Support case (single_case): max_raw=9                             │
│      raw=3 (Sev A) → min(3/9, 1) × 5 = 1.7 (Moderate)             │
│      raw=9 (Sev A + CritSit) → 5.0 (Critical)                     │
│                                                                      │
│  Compound signals:                                                   │
│    strength = min(avg(contributing_type_strengths) × multiplier, 5)  │
│                                                                      │
│  Hypothesis scores:                                                  │
│    match_score = overlap_ratio (0–1) × weighted_avg_strength (0–5)   │
│    final_score = min(match_score × category_modifier, max_score)     │
│    Configurable via config/hypotheses/scoring_config.json            │
└──────────────────────────────────────────────────────────────────────┘
```

#### `max_raw_strength` Reference

Defined per granularity in `config/signals/signal_template.json`:

| Signal Type | Granularity | max_raw_strength | Rationale |
|-------------|-------------|------------------|----------|
| SIG-TYPE-1 | subscription_region | 30 | ~10 resources × log2(13) × 80% |
| SIG-TYPE-1 | cross_region | 40 | 3 regions × 15 resources × 80% |
| SIG-TYPE-1 | cross_subscription | 40 | Same shape as cross_region |
| SIG-TYPE-1 | multi_sli | 40 | 4 SLIs × 10 resources |
| SIG-TYPE-1 | multi_customer_same_region_sli | 80 | 5 subs × 20 resources × 80% |
| SIG-TYPE-1 | multi_sli_region_wide | 200 | 3 SLIs × 5 subs × 20 resources |
| SIG-TYPE-2 | single_case | 9 | Sev A (3) × CritSit (3) |
| SIG-TYPE-2 | crit_sit | 9 | Same ceiling |
| SIG-TYPE-2 | escalated | 6 | Sev A (3) × 2 |
| SIG-TYPE-2 | multi_case_same_product | 15 | 5 cases × Sev A (3) |
| SIG-TYPE-2 | multi_customer_same_product | 40 | 3 custs × 5 cases × Sev factor |
| SIG-TYPE-3 | single_incident | 6 | (4−1) × 2 |
| SIG-TYPE-3 | outage_confirmed | 6 | (4−1) × 2 |
| SIG-TYPE-3 | with_child_incidents | 50 | 20 children × (4−1) |
| SIG-TYPE-3 | customer_correlated | 4.5 | (4−1) × 1.5 |
| SIG-TYPE-4 | dep_sli_breach_in_customer_region | 40 | 50 resources × 80% |
| SIG-TYPE-4 | multi_dep_sli_breach_in_region | 200 | Multi-dimensional product |
| SIG-TYPE-4 | multi_dep_service_breach_in_region | 100 | 3 deps × 4 SLIs × 10 subs |

#### Telemetry

Both `raw_strength` (original formula output) and `strength` (normalized 0–5)
are stored in every `ActivatedSignal` and `CompoundSignalResult` for
debugging and calibration. The `to_dict()` methods include both fields.

---

## Stage 2 — Triage Agent (LLM, Standalone)

**File:** `src/core/services/investigation/investigation_runner.py` (orchestration) +
`src/prompts/investigation_triage_prompt.txt`
**Nature:** LLM-powered reasoning · **Runs standalone** (outside GroupChat)

When the Signal Builder triggers an investigation, the **Triage Agent** is
the first LLM agent to process the activated signals. It runs **standalone**
(outside the GroupChat) so that retries do not consume GroupChat turns.
Its job is to match raw signal data against **symptom templates** and
confirm which symptoms are present.

### What the Triage Agent Receives

The task message sent to the triage agent contains:

1. **Activated signals** — type, granularity, confidence, strength (0–5 with label), summary
2. **Activated compound signals** — cross-type correlations
3. **Signal data rows** — up to 3 deduplicated rows per granularity (for filter evaluation)
   - Rows are filtered to **granularity-level `data_fields`** when defined, otherwise fall back to signal-type-level `data_fields`
   - Pre-aggregated granularities (e.g., `multi_customer_same_product`) show aggregated columns directly
   - When rows exceed the per-granularity cap, an `AGGREGATE:` summary line is appended with computed totals
4. **Symptom templates** — structured reference material from `config/symptoms/`

### Symptom Template Matching

Symptom templates define the conditions under which a symptom is "confirmed":

```
Symptom Template Example (SYM-SLI-002):
  ┌────────────────────────────────────────────────────────┐
  │  Name: Severe SLI Degradation                          │
  │  Source: SIG-TYPE-1                                    │
  │  Weight: 3 (high severity indicator)                   │
  │  Filters:                                              │
  │    max_min_value: 1.0                                  │
  │    severity_rules:                                     │
  │      CRITICAL: min_value == 0 AND avg_value < 1.0     │
  │      HIGH:     min_value == 0 OR  avg_value < 10.0    │
  │      WARNING:  avg_value < 50.0                        │
  │  LLM-derived fields: [severity]                        │
  └────────────────────────────────────────────────────────┘
```

The triage agent **reasons over the data rows** against each template:
- Evaluates filter criteria (min thresholds, value ranges)
- Computes LLM-derived fields (e.g., severity classification)
- Evaluates cross-source correlations (e.g., time overlap between incidents and SLI breaches)
- Assigns investigation category and severity

### Triage Output

```json
{
  "structured_output": {
    "symptoms": [
      {
        "template_id": "SYM-SLI-001",
        "status": "confirmed",
        "text": "SLI 'availability_sli' has 12 impacted resources...",
        "weight": 1,
        "signal_strength": 3.8,
        "severity": "HIGH"
      }
    ]
  },
  "signals": {
    "phase_complete": "triage"
  }
}
```

### Retry & Markdown Fallback

The investigation pipeline uses a **config-driven retry policy** for all agent
invocations. Retry settings are defined per agent in
`agents_config.json → investigation_workflow → retry_policy`:

```json
"retry_policy": {
  "triage_agent": { "max_retries": 2, "backoff": "linear", "backoff_base_seconds": 1 },
  "evidence_planner": { "max_retries": 1, "backoff": "none", "backoff_base_seconds": 0 },
  "reasoner": { "max_retries": 1, "backoff": "none", "backoff_base_seconds": 0 },
  "action_planner": { "max_retries": 1, "backoff": "linear", "backoff_base_seconds": 1 },
  "default": { "max_retries": 0, "backoff": "none", "backoff_base_seconds": 0 }
}
```

| Parameter | Values | Description |
|-----------|--------|-------------|
| `max_retries` | `0`+ | Maximum retry attempts (0 = no retry, runs once) |
| `backoff` | `none`, `linear`, `exponential` | Wait strategy between retries |
| `backoff_base_seconds` | `0`+ | Base delay; linear = base × attempt, exponential = base × 2^(attempt-1) |

**Retry scopes by agent type:**

| Agent | Invocation | Retry Trigger | Retry Mechanism |
|-------|------------|---------------|------------------|
| `triage_agent` | Standalone (Stage 1) | Timeout, exception, empty response, 0 hypotheses | Full re-invocation with enhanced prompt |
| `evidence_planner` | GroupChat (Stage 3) | Empty response | Standalone re-invocation outside GroupChat |
| `reasoner` | GroupChat (Stage 3) | Empty response | Standalone re-invocation outside GroupChat |
| `action_planner` | Standalone (Stage 4) | Timeout, exception, empty response | Full re-invocation |

All retry attempts are tracked in telemetry via `AgentRetry` events with
attempt count, reason, and backoff duration.

### Error Taxonomy

**File:** `src/helper/errors.py`

All pipeline `except Exception` blocks use `classify_exception(exc)` to map
raw exceptions into a structured error hierarchy. Each error class carries a
`retryable` flag that the retry loop consults — non-retryable errors
(e.g. `AuthError`) trigger immediate fail-fast instead of wasting retry
attempts.

```
PipelineError (retryable=False)          ← Base class for all pipeline errors
├── NetworkError (retryable=True)        ← ConnectionError, OSError, TimeoutError
├── AuthError (retryable=False)          ← 401/403, credential failures → fail-fast
├── LLMError (retryable=conditional)     ← Rate limits (retryable), content filter (not)
├── ParseError (retryable=False)         ← JSON decode, schema validation failures
├── ConfigError (retryable=False)        ← Missing files, bad config → operator attention
└── ToolError (retryable=conditional)    ← MCP tool failures (tool_name tracked)
```

`classify_exception(exc)` inspects the exception type and message to return
the most specific subclass. All error yields include an `error_category`
field (e.g. `"AuthError"`, `"NetworkError"`) for downstream filtering.

The triage agent additionally has a **two-layer resilience mechanism**:

1. **Retry with JSON reminder** (config-driven attempts): If an attempt
   produces 0 symptoms and 0 hypotheses, the runner re-invokes the triage
   agent with the original task message **plus** the previous response and
   an explicit instruction to emit the required ```` ```json ```` block.

2. **Markdown fallback parser** (`investigation_output_parser.py`): If the
   LLM returns analysis in prose but omits the JSON block entirely, a regex
   scanner (`_SYM_ID_RE`) extracts `SYM-*-NNN` IDs from the markdown text.
   Each extracted ID is treated as a confirmed symptom with minimal fields
   so that hypothesis scoring can still proceed.

```
Attempt 1 → LLM returns markdown without JSON block
  │  parse_agent_output() → 0 symptoms from JSON
  │  _extract_symptoms_from_markdown() → 3 symptom IDs found
  │  → Hypothesis scoring proceeds with extracted symptoms
  │
Attempt 1 → LLM returns empty/irrelevant response
  │  0 symptoms, 0 hypotheses
  └─▶ Retry with enhanced prompt (includes previous response + JSON format reminder)
      Attempt 2 → LLM returns proper JSON → normal flow
```

### Signal-Sourced Evidence Pre-Population

Before the GroupChat begins, the runner pre-populates evidence items from
signal data already collected during the Signal Builder phase. The mapping
from signal type to evidence requirement (`SIG-TYPE-1` → `ER-SLI-001`, etc.)
is loaded dynamically from `config/evidence/evidence_requirements.json`
(via the `signal_source` field on each ER entry). This avoids redundant
MCP tool calls during evidence collection.

```
Pre-Stage: Signal-Sourced Evidence
  For each activated signal type:
    1. Look up ER-ID via signal_source mapping (from evidence_requirements.json)
    2. Create EvidenceItem with summary from signal data
    3. Mark as collected → excluded from evidence_delta
  Result: Evidence planner skips already-available data
```

---

## Stage 3 — Hypothesis Scoring (Deterministic)

**File:** `src/core/services/investigation/hypothesis_scorer.py`
**Nature:** Fully deterministic — no LLM involved.

Immediately after triage completes (detected via `phase_complete: "triage"`),
the **hypothesis scorer** runs programmatically. This is Stage 2 of the
hybrid pipeline — it bridges LLM triage and LLM investigation.

### Scoring Formula

Hypothesis scores use a **configurable formula** with parameters loaded from
`config/hypotheses/scoring_config.json` at runtime — no code changes needed
to tune scoring behaviour.

```
match_score = overlap_ratio × agg_signal_strength
final_score = min(match_score × category_modifier, max_score)

Where:
  overlap_ratio       = weighted_matched / weighted_total  (0.0 – 1.0)
  weighted_matched    = Σ weight(symptom) for each expected symptom that is confirmed
  weighted_total      = Σ weight(symptom) for ALL expected symptoms
  agg_signal_strength = weight-proportional aggregation of matched symptom strengths:
                        Σ(weight_i × strength_i) / Σ(weight_i)
                        This prevents low-strength, low-weight symptoms from
                        disproportionately dragging the aggregate down.
  category_modifier   = category_boost_factor   (default 1.5) if categories match
                        category_mismatch_penalty (default 0.5) if categories mismatch
                        category_unknown_modifier (default 0.8) if no category data
                        1.0 (neutral) if hypothesis specifies "any" or no categories
  max_score           = cap on final score (default 7.5)

Result: 0.0 – max_score (default 7.5)
```

### Scoring Configuration (`config/hypotheses/scoring_config.json`)

All scoring parameters are externalized into a dedicated config file:

```json
{
  "strength_aggregation": "avg",
  "default_weight": 1,
  "min_score_threshold": 0.0,
  "category_boost_factor": 1.5,
  "category_mismatch_penalty": 0.5,
  "category_unknown_modifier": 0.8,
  "max_score": 7.5
}
```

| Parameter | Type | Default | Purpose |
|-----------|------|---------|---------|
| `strength_aggregation` | `avg` \| `max` \| `min` | `avg` | How to aggregate signal strengths across matched symptoms |
| `default_weight` | int | 1 | Fallback weight for symptoms not found in lookup |
| `min_score_threshold` | float | 0.0 | Discard hypotheses scoring below this value |
| `category_boost_factor` | float | 1.5 | Multiplier when symptom categories match hypothesis |
| `category_mismatch_penalty` | float | 0.5 | Multiplier when categories explicitly conflict |
| `category_unknown_modifier` | float | 0.8 | Multiplier when symptom lacks category data |
| `max_score` | float | 7.5 | Hard cap on final scored value |

**Category Matching (three-tier):**

| Condition | Modifier | Effect |
|-----------|----------|--------|
| Symptom `sli_category` or `dependency_category` matches hypothesis `relevant_sli_categories` / `relevant_categories` | `category_boost_factor` (1.5×) | Prioritize matching root causes |
| Symptom has category data that conflicts with hypothesis | `category_mismatch_penalty` (0.5×) | Deprioritize misaligned hypotheses |
| Symptom has no category data (cannot confirm or deny) | `category_unknown_modifier` (0.8×) | Soft penalty — less aggressive than mismatch |
| Hypothesis specifies `["any"]` or no categories | 1.0× (neutral) | No category filtering applied |

Hypothesis scores displayed to agents include the semantic label:
`score=3.2 (Significant)  status=active`

### How It Works

```
┌────────────────────────────────────────────────────────────────────┐
│                   Hypothesis Scoring Pipeline                       │
│                                                                    │
│  Input: Confirmed Symptoms from Triage Agent                       │
│         Hypothesis Templates from config/hypotheses/               │
│                                                                    │
│  For each hypothesis template:                                     │
│    1. Check required_symptoms (CRITICAL GATE):                     │
│       - If required_symptoms is defined, ALL must be present       │
│         in confirmed symptoms (AND logic)                          │
│       - If any missing → SKIP this hypothesis (cannot match)       │
│       - This prevents false matches (e.g., outage hypotheses       │
│         matching when no incident data present)                    │
│                                                                    │
│    2. Count overlap: expected_symptoms ∩ confirmed_symptoms        │
│                                                                    │
│    3. Check threshold: matched_count ≥ min_symptoms_for_match?     │
│                                                                    │
│    4. Compute weighted match_score using:                          │
│       - Symptom weights (weight-proportional aggregation)          │
│       - Normalized signal strengths                                │
│       - scoring_config.json parameters                             │
│                                                                    │
│    5. Apply category modifier (boost / unknown / mismatch / 1.0)  │
│       final_score = min(match_score × category_modifier, max_score)│
│                                                                    │
│    6. Filter by min_score_threshold                                │
│                                                                    │
│  Output: Ranked list of qualifying hypotheses (highest score first)│
│         Scores 0–max_score with semantic labels                    │
│         (max_score configurable, default 7.5)                      │
│                                                                    │
│  Example 1: MATCH                                                  │
│    HYP-SLI-001 (Outage Caused SLI Breach)                         │
│      expected: [SYM-SLI-001, SYM-SLI-002, SYM-OUT-001, ...]     │
│      required: [SYM-OUT-001, SYM-OUT-002]                         │
│      matched:  [SYM-SLI-001, SYM-SLI-002, SYM-OUT-001, SYM-OUT-002]│
│      required_check: ALL required present ✓                        │
│      matched_count = 4 ≥ min(3) ✓                                 │
│      match_score = 3.2 (Significant)                               │
│                                                                    │
│  Example 2: SKIP (required_symptoms not met — AND logic)           │
│    HYP-OUT-002 (Outage Compounding Pre-Existing Issue)            │
│      required: [SYM-OUT-001, SYM-OUT-003]                         │
│      confirmed: [SYM-SLI-001, SYM-SLI-002, SYM-OUT-001]          │
│      required_check: SYM-OUT-003 missing ✗ (need ALL)             │
│      → SKIPPED (cannot match without ALL required symptoms)       │
│                                                                    │
│  Example 3: SKIP (category mismatch + penalty)                     │
│    HYP-SLI-003 (Capacity Exhaustion)                               │
│      relevant_sli_categories: ["capacity", "availability"]         │
│      matched symptoms have sli_category: "connectivity"            │
│      category_modifier: 0.5× (mismatch penalty)                   │
│      match_score = 1.0 (after penalty, below threshold)            │
│      → Deprioritized or filtered out                              │
│                                                                    │
│  Example 4: UNKNOWN CATEGORY (soft penalty)                        │
│    HYP-SLI-003 (Capacity Exhaustion)                               │
│      relevant_sli_categories: ["capacity", "availability"]         │
│      matched symptoms have NO sli_category data                    │
│      category_modifier: 0.8× (unknown — softer than mismatch)     │
│      → Slight score reduction, not aggressively deprioritized      │
└────────────────────────────────────────────────────────────────────┘
```

### Hypothesis Template Structure

Each hypothesis template in `config/hypotheses/` encodes domain knowledge:

| Field | Purpose | Example |
|-------|---------|---------|
| `id` | Unique identifier | `HYP-SLI-001` |
| `name` | Human-readable name | "Outage Caused SLI Breach" |
| `statement` | Parameterized description | "The SLI breach on '{slo_sli_id}'..." |
| `expected_symptoms` | Which symptoms support this hypothesis | `["SYM-SLI-001", "SYM-OUT-002"]` |
| `min_symptoms_for_match` | Minimum overlap to qualify | `3` |
| `required_symptoms` | **CRITICAL GATE**: ALL must be present to match (AND logic) | `["SYM-OUT-001", "SYM-OUT-003"]` |
| `relevant_sli_categories` | SLI categories that align with this hypothesis | `["capacity", "availability"]` |
| `evidence_needed` | What data to collect to verify | `["ER-OUT-001", "ER-SLI-001"]` |
| `supporting_signals` | Expert guidance for the reasoner | "Strongest when SYM-OUT-003 co-occurs..." |

**Field Usage Guidelines:**

- **`required_symptoms`**: Use for hypotheses that MUST have specific signal types (AND logic — ALL listed symptoms must be present):
  - **Outage hypotheses** (HYP-OUT-*): Require `["SYM-OUT-001", "SYM-OUT-002"]` — both outage symptoms must be confirmed
  - **Dependency hypotheses** (HYP-SLI-004): Require `["SYM-DEP-001", "SYM-DEP-002"]` — both dependency symptoms must be confirmed
  - **Support-driven hypotheses** (HYP-SLI-006): Require `["SYM-SUP-001"]` — support case must be present
  - **Purpose**: Prevents false matches when critical signal types are missing. Since this is AND logic, list only the symptoms that are truly mandatory for the hypothesis to be valid

- **`relevant_sli_categories`**: Use for SLI-specific hypotheses:
  - **Capacity hypotheses**: `["capacity", "availability", "performance"]` — should NOT match connectivity/latency SLIs
  - **Generic hypotheses**: `["any"]` — accepts all SLI categories
  - **Purpose**: Applies 1.5× boost for category match, 0.5× penalty for mismatch

---

## Stage 4 — Investigation GroupChat (LLM Multi-Agent)

**File:** `src/core/services/investigation/investigation_runner.py`
**Framework:** Microsoft Agent Framework `GroupChatBuilder`
**Config:** `investigation_workflow` section in `agents_config.json`
(`max_turns=40`, `max_evidence_cycles=2`)

Once hypotheses are scored and ranked, the investigation enters a **multi-agent
GroupChat**. By default, a **deterministic speaker selector** controls turn order
based on the current phase and investigation state. This can be disabled via the
`ENABLE_SPEAKER_SELECTOR` feature flag (or `use_speaker_selector` in config),
falling back to the framework's LLM-based auto-selection.

> **Important:** The triage agent and action planner run **standalone**
> (outside the GroupChat). Only `evidence_planner` and `reasoner` are
> GroupChat participants.

### End-to-End Investigation Flow

```
┌──────────────────────────────────────────────────────────────────────┐
│              Full Investigation Pipeline                              │
│                                                                      │
│  ┌──────────────┐   STANDALONE (pre-GroupChat)                       │
│  │ triage_agent │──▶ signals → symptoms (with retry + fallback)     │
│  └──────┬───────┘                                                    │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────┐  PROGRAMMATIC                                  │
│  │ HypothesisScorer │──▶ symptoms → ranked hypotheses (0–5 scale)   │
│  └──────┬───────────┘                                                │
│         │                                                            │
│         ▼                                                            │
│  ┌──────────────────────────────────────────────────────────────┐    │
│  │  GroupChat (evidence_planner + reasoner)                     │    │
│  │                                                              │    │
│  │  Orchestrator (investigation_orchestrator)                   │    │
│  │    │                                                         │    │
│  │    ├──▶ evidence_planner ──── Phase: PLANNING/COLLECTING    │    │
│  │    │      ├──▶ sli_collector (sub-agent via agent_tools)    │    │
│  │    │      ├──▶ incident_collector (sub-agent)               │    │
│  │    │      └──▶ support_collector (sub-agent)                │    │
│  │    │                                                         │    │
│  │    └──▶ reasoner ────────────── Phase: REASONING            │    │
│  │           ├─ Per-symptom verdicts (satisfied/not_satisfied)  │    │
│  │           └─ Hypothesis verdict:                             │    │
│  │                CONFIRMED → exit GroupChat                    │    │
│  │                CONTRIBUTING → exit GroupChat                 │    │
│  │                REFUTED → next hypothesis in ranked order     │    │
│  │                needs_more_evidence → back to evidence_planner│    │
│  │                                    (max 2 cycles)           │    │
│  └──────────────────────────────────────────────────────────────┘    │
│         │                                                            │
│         ▼                                                            │
│  ┌────────────────┐  STANDALONE (post-GroupChat)                     │
│  │ action_planner │──▶ ALL confirmed/contributing hypotheses         │
│  │                │   → deduplicated action plan                     │
│  └────────────────┘                                                  │
└──────────────────────────────────────────────────────────────────────┘
```

### Investigation Phase Lifecycle

```
                     STANDALONE                        GROUPCHAT
 ┌──────────┐   ┌────────┐   ┌──────────────┐   ┌──────────┐   ┌────────────┐
 │INITIALIZ-│──▶│ TRIAGE │──▶│ HYPOTHESIZING│──▶│ PLANNING │──▶│ COLLECTING │
 │ING       │   │(solo)  │   │              │   │          │   │            │
 └──────────┘   └────────┘   └──────────────┘   └──────────┘   └─────┬──────┘
       │            │                                │              │
       └────────────┴────────────────────────────────┴──────────────┴──────┐
                                                                            │
                                          NARRATOR (optional, observes all) │
                                          Streams first-person narration ◀──┘
                                          after each phase/agent turn
                                                                      │
      ┌───────────────────────────────────────────────────────────────┘
      │
      ▼                                        STANDALONE
 ┌──────────┐   ┌────────┐   ┌──────────┐   ┌──────────┐
 │REASONING │──▶│ ACTING │──▶│NOTIFYING │──▶│ COMPLETE │
 │          │   │(solo)  │   │          │   │          │
 └────┬─────┘   └────────┘   └──────────┘   └──────────┘
      │            │              │
      └────────────┴──────────────┴───▶ NARRATOR observes & narrates
      │
      │  needs_more_evidence?  ──▶ Back to PLANNING (max 2 cycles)
      │  hypothesis_refuted?   ──▶ Next hypothesis in ranked order
      │
```

> **Note:** The **narrator agent** (optional, enabled via `narrator_enabled: true`) runs **outside
> the GroupChat** after each phase/agent turn. It observes investigation progress and streams
> human-readable first-person narration via SSE events (`investigation_narrator_chunk`,
> `investigation_narrator_done`). Narrator errors are non-fatal — investigation continues normally
> if narration fails. See [Investigation Narrator](#investigation-narrator-optional) for details.

### GroupChat Participants

Only two agents participate in the GroupChat loop. The orchestrator
controls turn order via a deterministic speaker selector (when enabled):

**Speaker Selector Feature Flag:**

| Source | Key | Default | Description |
|--------|-----|---------|-------------|
| Env var | `ENABLE_SPEAKER_SELECTOR` | _(not set)_ | Overrides config when set (`true`/`false`) |
| Config | `investigation_workflow.use_speaker_selector` | `true` | Config-level toggle |
| Hard-coded | — | `true` | Fallback when neither env var nor config is set |

Resolution order: **env var → config → default `true`**.

| Flag State | Selection Method | Who Routes? | Instruction Injection? |
|------------|-----------------|-------------|------------------------|
| **enabled** (default) | Custom `selection_func` closure | Deterministic rules (phase transitions, cycle detection, signal parsing) | Yes — `_inject_*` helpers modify agent instructions at runtime |
| **disabled** | Framework auto-selection | LLM-based (orchestrator agent decides via prompt) | No — agents use static instructions only |

When disabled, a `FeatureFlagOverride` telemetry event is emitted (visible in
both App Insights and the Debug UI SSE stream) recording the flag name, state,
and fallback routing method.

> **Note:** Context folding (`ENABLE_CONTEXT_FOLDING`) is **independent** of
> speaker selection. When the speaker selector is disabled, folding still
> operates normally — it is attached directly to the agent's
> `compaction_strategy` and runs regardless of how the agent was selected.

| Agent | Role in GroupChat | Phase |
|-------|-------------------|-------|
| `investigation_orchestrator` | Routes turns between evidence_planner and reasoner | All |
| `evidence_planner` | Dispatches sub-agent collectors, aggregates evidence | PLANNING → COLLECTING |
| `reasoner` | Evaluates evidence, issues hypothesis verdicts | REASONING |

**Config-Driven Phase Transitions:**

Phase-to-agent routing is defined in `agents_config.json → investigation_workflow → phase_transitions`:

```json
"phase_transitions": {
  "hypothesizing": "evidence_planner",
  "planning": "investigation_orchestrator",
  "collecting": "reasoner",
  "reasoning": "investigation_orchestrator",
  "notifying": "investigation_orchestrator"
}
```

Each key is a phase name emitted by an agent's `phase_complete` signal; the value
is the next agent to receive the turn. Adding a new phase requires only a config
change — no code modification. Targets are validated against GroupChat participants
at startup; invalid targets fall back to the orchestrator with a warning.

**Agent-Based Phase Auto-Advance:**

GroupChat agents (evidence_planner, reasoner) may not reliably emit `phase_complete`
signals in their structured output. To ensure every phase transition is recorded in
Application Insights, the investigation runner (`_finalize_agent_response`) infers
the correct phase from **which agent spoke** and auto-advances through the legal
transition chain:

```
_FORWARD_CHAIN = [HYPOTHESIZING → PLANNING → COLLECTING → REASONING]
```

| Agent Spoke | Current Phase | Auto-Advance Action |
|-------------|---------------|---------------------|
| `evidence_planner` | HYPOTHESIZING | Step forward to PLANNING |
| `evidence_planner` | REASONING | Backtrack to PLANNING (evidence cycle) |
| `reasoner` | HYPOTHESIZING, PLANNING, or COLLECTING | Step forward through chain to REASONING |

The `_step_forward_to()` helper walks through each intermediate phase in
`_FORWARD_CHAIN`, calling `investigation.transition_to()` at each step to
ensure no phases are skipped. Each transition emits a `PhaseTransition` event
to Application Insights with `source=auto_advance:agent=<name>`.

A duplicate-transition guard prevents redundant `PhaseTransition` logs: the
runner only calls `tracker.log_phase_transition()` when the phase actually
changed (`investigation.phase.value != prev_phase`). This applies both to
the GroupChat loop (Stage 4) and the pre-GroupChat hypothesis injection
(Stage 2, where `transition_to(HYPOTHESIZING)` is guarded against logging
when triage already advanced to that phase).

Agent role names are resolved via `agent_roles` from config — no hardcoded
strings.

**Cycle Detection & Stuck-Loop Prevention:**

The speaker selector tracks recent selections and message content to detect
oscillation (two agents ping-ponging) and stalled progress (identical agent
output). Settings are in `agents_config.json → investigation_workflow → cycle_detection`:

```json
"cycle_detection": {
  "history_window": 6,
  "max_repeated_pattern": 3,
  "max_identical_messages": 2
}
```

| Setting | Default | Description |
|---------|---------|-------------|
| `history_window` | `6` | Number of recent speaker selections tracked |
| `max_repeated_pattern` | `3` | Trigger when a 2-agent pair repeats this many times |
| `max_identical_messages` | `2` | Trigger when an agent produces identical output N times in a row |

**Two-stage intervention:**

1. **First detection** → injects a warning into the orchestrator's instructions
   ("You MUST advance to a different phase or signal `investigation_resolved`"),
   logs `OscillationDetected` with `intervention=context_injection`, and routes
   to the orchestrator.
2. **Second detection** → forces `investigation.phase = COMPLETE`, logs
   `OscillationDetected` with `intervention=force_resolve`, and terminates the
   GroupChat gracefully.

│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

**Agent Name Registry (Config-Driven Role Mapping):**

All agent name references in the speaker selector and investigation runner are
resolved through a single `agent_roles` dict loaded from
`agents_config.json → investigation_workflow → agent_roles`:

```json
"agent_roles": {
  "orchestrator": "investigation_orchestrator",
  "evidence_planner": "evidence_planner",
  "reasoner": "reasoner",
  "triage": "triage_agent",
  "action_planner": "action_planner",
  "narrator": "narrator"
}
```

Every component uses role keys (e.g. `agent_roles["triage"]`) instead of
hardcoded name strings. Renaming an agent requires only a config change — no
code modification. The speaker selector raises `ValueError` if `agent_roles` is
not provided, ensuring the registry is always explicitly wired.

**No Keyword-Based Routing:**

The speaker selector relies **exclusively** on structured `next_agent` signals
parsed from agent JSON output. There is no keyword/substring fallback — if an
agent's output doesn't contain a valid `next_agent` signal, routing falls through
the priority chain (phase transitions → evidence-collected heuristic →
post-specialist return) to the orchestrator. This eliminates fragile text-pattern
matching that could misroute turns based on incidental mentions.

**Evidence Dispatch Counting:**

Evidence collection dispatches are counted **per hypothesis**, with the counter
resetting to 0 each time a new hypothesis begins evaluation (refuted → next, or
confirmed → next). The `max_evidence_cycles` setting (default: `2`) represents
the **total allowed dispatches** including the first — there is no "free" initial
dispatch. Once the limit is reached, the speaker selector blocks further routing
to the evidence planner and instead routes to the reasoner with an
`evidence_exhausted` context injection, forcing a final determination on
available data.

| Dispatch | Counted? | Behaviour |
|----------|----------|-----------|
| 1st dispatch (initial collection) | Yes | Allowed if count < limit |
| 2nd dispatch (re-collection after `needs_more_evidence`) | Yes | Allowed if count < limit |
| Exceeds limit | — | Blocked; reasoner receives "make a final determination" |

Counter resets on: hypothesis refuted → next hypothesis, hypothesis confirmed → next hypothesis.

**Premature Resolution Guard:**

The `investigation_resolved` signal is validated before acceptance. Two
conditions must both be satisfied:

1. **Terminal hypothesis required** — at least one hypothesis must have reached
   a terminal status (`CONFIRMED`, `CONTRIBUTING`, or `REFUTED`). If all
   hypotheses are still `ACTIVE`, the signal is blocked regardless of sender.
   This prevents the orchestrator from hallucinating resolution before any
   hypothesis evaluation has completed.
2. **No remaining active hypotheses** — if active hypotheses still exist, the
   signal is blocked and the orchestrator receives a queue reminder injection
   listing the remaining hypotheses to evaluate.

**Speculative stale-state adjustment:** The speaker selector runs *before*
`apply_to_investigation`, so hypothesis statuses from the current message are
not yet reflected in the investigation state. The guard speculatively accounts
for two sources of pending status changes in the current message:

- `hypothesis_refuted` signal → the current hypothesis is about to be refuted
- `evaluations` in `structured_output` → hypotheses with CONFIRMED / REFUTED /
  CONTRIBUTING status are about to be updated

These pending terminal IDs are excluded from `remaining_active` and counted as
terminal, preventing the guard from incorrectly blocking a legitimate
resolution that includes a hypothesis verdict in the same message.

| Condition | Signal Source | Result |
|-----------|-------------|--------|
| No terminal hypothesis | Any agent | Blocked |
| Active hypotheses remain | Non-orchestrator | Blocked |
| Active hypotheses remain | Orchestrator | Blocked + queue reminder injected |
| ≥1 terminal, 0 active | Orchestrator | Allowed |
| hypothesis_refuted + investigation_resolved in same msg | Orchestrator | Allowed (speculative adjustment) |

**JSON Output Parsing Robustness:**

Agent output is parsed by `investigation_output_parser.py` into a
`ParsedAgentOutput` dataclass. Three robustness improvements harden the
extraction pipeline:

1. **Schema-aware block selection** — When an agent response contains multiple
   ` ```json ` fenced blocks (e.g., an example followed by the actual answer),
   the parser no longer blindly takes the last parseable block.  Instead it
   scans *all* blocks and prefers the one whose top-level keys match the
   expected schema (`structured_output` and/or `signals`).  Among
   schema-matching blocks the last one wins; if no block matches the schema,
   the last parseable block is used as a fallback.

2. **Safe control-character sanitization** — The sanitizer removes truly
   harmful control characters (`\x00–\x08`, `\x0b`, `\x0c`, `\x0e–\x1f`)
   but now properly handles literal newlines, carriage returns, and tabs that
   appear *inside* JSON string values.  These are escaped to their JSON
   equivalents (`\n`, `\r`, `\t`) instead of being stripped, preserving data
   fidelity while keeping `json.loads` happy.

3. **Schema validation gate** — After extracting a JSON block, the parser
   validates that the dict contains at least one of the expected top-level keys
   (`structured_output` or `signals`).  If validation fails, the block is
   discarded and the parser falls back to legacy `---SIGNALS---` parsing,
   preventing example or template JSON from being misinterpreted as agent
   output.

| Scenario | Old Behaviour | New Behaviour |
|----------|--------------|---------------|
| Example JSON + answer JSON | Example parsed (last wins) | Answer parsed (schema match) |
| Literal newlines in JSON strings | `json.loads` failure or data loss | Escaped to `\n`, data preserved |
| JSON block without `structured_output`/`signals` | Treated as valid output | Discarded; falls back to legacy |

### Evidence Collection Architecture

The **Evidence Planner** is a coordinator agent with sub-agents. It dispatches
domain-specific collectors in parallel, each specialized for a data source:

```
┌──────────────────────────────────────────────────────────────────┐
│                    Evidence Collection                             │
│                                                                  │
│  Evidence Planner (agent_tools mode — calls sub-agents)          │
│    │                                                             │
│    ├──▶ SLI Collector                                           │
│    │      Tools: collect_impacted_resource_customer_tool          │
│    │             collect_impacted_resource_multicustomer_tool     │
│    │      Data:  Per-subscription SLI breach aggregates          │
│    │             Cross-customer impact patterns                  │
│    │                                                             │
│    ├──▶ Incident Collector                                      │
│    │      Tools: collect_incident_details_tool                   │
│    │      Data:  IcM incidents, severity, outage status          │
│    │             Impact timeline, child incident count           │
│    │                                                             │
│    └──▶ Support Collector                                       │
│           Tools: collect_support_request_tool                    │
│                  collect_support_request_multicustomer_tool       │
│           Data:  Customer support cases, CritSits               │
│                  Cross-customer support patterns (pre-aggregated)│
│                  Escalation patterns, severity changes           │
│                                                                  │
│  Each collector:                                                 │
│    1. Calls MCP tools with investigation context parameters      │
│    2. Synthesizes raw data into evidence items                   │
│    3. Assigns preliminary verdicts (supports/refutes)            │
│    4. Returns structured evidence_items to evidence_planner      │
└──────────────────────────────────────────────────────────────────┘
```

### Hypothesis Evaluation Loop

The investigation supports **sequential hypothesis evaluation** and
**evidence cycling**:

```
Hypotheses ranked by score: [HYP-001 (3.2 Significant), HYP-002 (2.1 Moderate), HYP-003 (1.5 Low)]

Iteration 1: Evaluate HYP-001
  ├─ Evidence Planner collects ER-OUT-001, ER-SLI-001, ER-SLI-002
  ├─ Reasoner evaluates:
  │    SYM-SLI-001: satisfied ✓
  │    SYM-SLI-002: satisfied ✓
  │    SYM-OUT-002: not_satisfied ✗ (no matching outage found)
  │    Verdict: CONTRIBUTING (0.65 confidence)
  └─ → Proceed to Action Planner

  (If REFUTED instead):
  └─ → Orchestrator advances to HYP-002

  (If needs_more_evidence):
  └─ → Evidence Planner collects additional data (cycle 2 of max 2)
```

### Evidence Requirement Mapping

Evidence requirements (`config/evidence/evidence_requirements.json`) define
**what data** to collect and **which tools** to call:

| ER ID | Description | Tool | Category |
|-------|-------------|------|----------|
| `ER-OUT-001` | Recent high-severity IcM incidents | `collect_incident_details_tool` | IcM |
| `ER-SLI-001` | Customer-specific SLI breach data | `collect_impacted_resource_customer_tool` | SLI |
| `ER-SLI-002` | Multi-customer SLI impact | `collect_impacted_resource_multicustomer_tool` | SLI |
| `ER-TKT-001` | Customer support cases | `collect_support_request_tool` | Support |
| `ER-TKT-002` | Cross-customer support patterns | `collect_support_request_multicustomer_tool` | Support |
| `ER-DEP-002` | Dependency service SLI data | `collect_impacted_resource_multicustomer_tool` | Dependency |
| `ER-LOAD-001` | Workload spike detection | `collect_impacted_resource_customer_tool` | SLI |

---

## Stage 5 — Action Planning & Notification (Standalone)

**File:** `src/core/services/investigation/investigation_runner.py` (orchestration) +
`src/prompts/investigation_action_planner_prompt.txt`
**Nature:** LLM-powered selection from a deterministic catalog · **Runs standalone** (after GroupChat)

The **Action Planner** runs **standalone after the GroupChat completes**,
receiving ALL confirmed and contributing hypotheses in a single task message.
This design enables cross-hypothesis action deduplication — if two hypotheses
both warrant `ACT-ICM-001`, the action planner emits it once with both
hypothesis IDs in `target_hypotheses`.

The task message (`_build_action_task()`) includes:
- All actionable hypotheses with their verdicts, confidence scores, and matched symptoms
- Collected evidence summaries
- Confirmed symptoms for context
- Explicit deduplication instructions

The action planner selects remediation actions from a pre-defined
**Action Catalog** (`config/actions/action_catalog.json`).

### Action Catalog Structure

Actions are tiered by automation level:

| Tier | Meaning | Example |
|------|---------|---------|
| `auto` | Can be executed automatically | Create IcM ticket, send email notification |
| `gated` | Requires human approval | Create ticket for external dependency team |
| `monitor` | Schedule follow-up check | Re-run SLI check in 30 minutes |
| `recommendation` | Suggest to human operator | Scale up resources, review configuration |

### Action Selection Flow

```
┌──────────────────────────────────────────────────────────────┐
│                   Action Selection Logic                      │
│                                                              │
│  For confirmed/contributing hypothesis:                       │
│    1. Filter action_catalog by applicable_hypotheses          │
│    2. Filter by applicable_categories                         │
│    3. Check min_confidence threshold against hypothesis       │
│       confidence                                             │
│    4. Prioritize by tier: auto > gated > monitor >           │
│       recommendation                                         │
│    5. Output structured action plan with justifications       │
│                                                              │
│  Example output:                                             │
│    Action 1: ACT-ICM-001 (auto) — Create IcM ticket         │
│      Priority: HIGH, Confidence: 0.65                        │
│      Justification: "SLI breach confirmed with outage        │
│      correlation..."                                         │
│    Action 2: ACT-EMAIL-001 (auto) — Notify AED team         │
│    Action 3: ACT-MONITOR-001 (auto) — 30-min follow-up      │
└──────────────────────────────────────────────────────────────┘
```

---

## Configuration-Driven Design

The system is designed so that **domain experts can extend detection
capabilities without changing code**. New signal types, symptoms,
hypotheses, and actions are added by editing JSON configuration files.

### Extension Points

| To Add | Edit This File | No Code Change Needed |
|--------|---------------|----------------------|
| New signal type | `config/signals/signal_template.json` | ✓ (if MCP tool exists) |
| New symptom template | `config/symptoms/<category>.json` | ✓ |
| New hypothesis | `config/hypotheses/<category>.json` | ✓ |
| New evidence requirement | `config/evidence/evidence_requirements.json` | ✓ |
| New remediation action | `config/actions/action_catalog.json` | ✓ |
| New dependency service | `config/dependency_services/<service>.json` | ✓ |
| New monitoring target | `config/monitoring_context.json` | ✓ |
| Scoring formula tuning | `config/hypotheses/scoring_config.json` | ✓ |
| Agent timeout tuning | `config/agents/agents_config.json` → `agent_timeout_seconds` | ✓ |
| Agent retry policy | `config/agents/agents_config.json` → `retry_policy` | ✓ |
| Phase transitions | `config/agents/agents_config.json` → `phase_transitions` | ✓ |
| Cycle detection | `config/agents/agents_config.json` → `cycle_detection` | ✓ |
| Max eval hypotheses | `config/agents/agents_config.json` → `max_eval_hypotheses` | ✓ |
| Max rows per grain | `config/agents/agents_config.json` → `max_rows_per_grain` | ✓ |
| Agent name registry | `config/agents/agents_config.json` → `agent_roles` | ✓ |
| Speaker selector toggle | `config/agents/agents_config.json` → `use_speaker_selector` | ✓ |
| Phase pipeline | `config/agents/agents_config.json` → `phase_pipeline` | ✓ |
| MCP concurrency limit | `config/signals/signal_template.json` → `max_concurrent_mcp_calls` | ✓ |

### Adding a New Hypothesis (Example)

To add a hypothesis for detecting a new root cause pattern:

```json
{
  "id": "HYP-SLI-006",
  "name": "DNS Resolution Failure Caused SLI Breach",
  "statement": "The SLI breach was caused by DNS resolution failures...",
  "category": "sli",
  "relevant_sli_categories": ["connectivity", "availability"],
  "expected_symptoms": ["SYM-SLI-001", "SYM-SLI-005", "SYM-DEP-001"],
  "min_symptoms_for_match": 2,
  "required_symptoms": ["SYM-DEP-001"],
  "evidence_needed": ["ER-DEP-002", "ER-SLI-001"],
  "supporting_signals": "Strongest when cross-region pattern appears..."
}
```

**Key Fields:**
- **`required_symptoms`**: Enforces critical gate with AND logic — hypothesis cannot match unless ALL listed symptoms are present (prevents false matches when signal type is missing)
- **`relevant_sli_categories`**: Category matching (1.5× boost if match, 0.5× penalty if mismatch)

No code changes required. The hypothesis scorer will automatically include
it in the next evaluation cycle, and the `prompt_loader` will inject the
updated hypothesis ID list into agent prompts via the
`{{VALID_HYPOTHESIS_IDS}}` template variable (see below).

### Prompt Template Variables

`prompt_loader.py` resolves template placeholders at startup so that
prompts stay in sync with configuration files:

| Variable | Source | Purpose |
|----------|--------|---------|
| `{{ACTION_CATALOG}}` | `config/actions/action_catalog.json` | Full action catalog JSON injected into the action-planner prompt |
| `{{VALID_HYPOTHESIS_IDS}}` | `config/hypotheses/*.json` | Auto-generated list of all valid hypothesis IDs, grouped by category, injected into reasoner & action-planner prompts |

This means adding or removing a hypothesis JSON entry is the **only** step
needed — no prompt files or code need to be edited.

### Config Schema Validation (Pydantic)

All JSON config files are **validated at load time** using Pydantic v2 models
in `core/models/config/`. If a config file has missing fields, wrong types,
or invalid ID patterns, the system fails fast with a clear error message
instead of crashing deep in the pipeline.

| Config Category | Pydantic Model | Validated At | ID Pattern |
|----------------|----------------|-------------|------------|
| Symptom templates (`config/symptoms/*.json`) | `SymptomFileConfig` | `symptom_matcher.load_symptom_templates()` | `^SYM-` |
| Hypothesis templates (`config/hypotheses/*.json`) | `HypothesisFileConfig` | `hypothesis_scorer.load_hypothesis_templates()` + `prompt_loader._load_valid_hypothesis_ids()` | `^HYP-` |
| Evidence requirements (`config/evidence/*.json`) | `EvidenceFileConfig` | `investigation_runner._load_signal_type_to_er_mapping()` + `prompt_loader._load_evidence_requirements_reference()` | `^ER-` |
| Action catalog (`config/actions/*.json`) | `ActionCatalogFileConfig` | `prompt_loader._resolve_template_vars()` | `^ACT-`, tier `^(auto\|gated)$` |

```
core/models/config/
├── __init__.py                      ← Re-exports all models
├── symptom_template.py              ← SymptomFieldsConfig, SymptomTemplateConfig, SymptomFileConfig
├── hypothesis_template.py           ← HypothesisTemplateConfig, HypothesisFileConfig
├── evidence_requirement.py          ← EvidenceRequirementConfig, EvidenceFileConfig
├── action_catalog.py                ← ActionPayloadTemplate, ActionConfig, ActionCatalogFileConfig
├── agents.py                        ← AgentConfig, AgentsFileConfig, InvestigationWorkflowConfig
├── phase_pipeline.py                ← PhaseConfig, PhasePipelineConfig
├── signal_template.py               ← SignalTemplateFileConfig, SignalTypeConfig, CompoundSignalConfig
├── monitoring_context.py            ← MonitoringContextFileConfig, MonitoringTargetConfig
└── dependency_service.py            ← DependencyServiceFileConfig, SliSymptomConfig
```

**Validation is lazy-imported** at each call site via `from pydantic import ValidationError`
to avoid circular dependencies and keep startup fast. Each loader wraps validation
in `try/except ValidationError` and raises `ValueError` with the filename and
specific field errors, making misconfiguration immediately obvious.

**Example error output:**
```
ValueError: Invalid hypothesis config 'sli_hypotheses.json': 1 validation error for HypothesisFileConfig
hypotheses -> 0 -> id
  String should match pattern '^HYP-' [type=string_pattern_mismatch]
```

---

## Agent Roster

### Analysis Agents (User-Interactive GroupChat)

| Agent | Role | Tools | Model |
|-------|------|-------|-------|
| `orchestrator` | Routes queries to specialist agents | None | gpt-4o |
| `entity_extractor` | Extracts and normalizes entities | `normalize_entity_mapping_tool` | gpt-4o |
| `outage_analyst` | Outage/incident T-SQL analysis | `run_tsql_query_tool`, `collect_root_cause_tool` | gpt-4o |
| `airo_analyst` | AIRO impact metrics analysis | `run_tsql_query_tool` | gpt-4o |
| `customer_insights` | Customer impact analysis | `run_tsql_query_tool` | gpt-4o |
| `analyst_coordinator` | Parallel analyst dispatch | Sub-agents (outage, airo, customer) | gpt-4o |
| `visualizer` | Generates Streamlit visualization code | None | gpt-4o |
| `summarizer` | Consolidates analysis into structured response | None | gpt-4o |

### Investigation Agents (Automated Pipeline)

| Agent | Role | Tools | Model |
|-------|------|-------|-------|
| `investigation_orchestrator` | Phase routing and turn management | None | gpt-4o |
| `triage_agent` | Signal → symptom matching (standalone, pre-GroupChat) | `collect_impacted_resource_customer_tool` | gpt-4o |
| `evidence_planner` | Evidence collection coordination | Sub-agents (collectors) | gpt-4o |
| `sli_collector` | SLI breach data collection | `collect_impacted_resource_*_tool` | gpt-4o |
| `incident_collector` | IcM incident data collection | `collect_incident_details_tool` | gpt-4o |
| `support_collector` | Support case data collection | `collect_support_request_tool`, `collect_support_request_multicustomer_tool` | gpt-4o |
| `reasoner` | Evidence evaluation and verdict | None (pure reasoning) | gpt-4o |
| `action_planner` | Remediation action selection (standalone, post-GroupChat) | None (pure reasoning) | gpt-4o |
| `narrator` | Human-readable narration of investigation flow (optional) | None (pure reasoning) | gpt-4o |

---

## Middleware Stack

Every agent in the pipeline is wrapped with a configurable middleware stack
that provides cross-cutting concerns:

```
┌──────────────────────────────────────────────────────────────┐
│                    Middleware Stack                            │
│                                                              │
│  ┌────────────────────────────────┐                          │
│  │ Prompt Injection Detection     │ ← Pre-execution guard    │
│  │ (RATIO /v1/moderate API)       │    Short-circuits on     │
│  │  • Input scan (Phase 1)        │    detected injection    │
│  └──────────────┬─────────────────┘                          │
│                 │                                             │
│  ┌──────────────▼─────────────────┐                          │
│  │ LLM Call Logging               │ ← Captures model,       │
│  │                                │    duration, errors      │
│  └──────────────┬─────────────────┘                          │
│                 │                                             │
│  ┌──────────────▼─────────────────┐                          │
│  │ Tool Call Capture              │ ← Records tool name,     │
│  │                                │    arguments, result,    │
│  │                                │    timing, agent         │
│  └──────────────┬─────────────────┘                          │
│                 │                                             │
│  ┌──────────────▼─────────────────┐                          │
│  │ Tool Output Injection Scan     │ ← Post-tool guard        │
│  │ (FunctionMiddleware)           │    Replaces poisoned     │
│  │                                │    tool results          │
│  └──────────────┬─────────────────┘                          │
│                 │                                             │
│  ┌──────────────▼─────────────────┐                          │
│  │ Prompt Injection Detection     │ ← Post-execution guard   │
│  │  • Output scan (Phase 3)       │    Replaces poisoned     │
│  │                                │    agent output before   │
│  │                                │    GroupChat pool         │
│  └──────────────┬─────────────────┘                          │
│                 │                                             │
│  ┌──────────────▼─────────────────┐                          │
│  │ Output Evaluation              │ ← Sends agent output to  │
│  │ (External eval API)            │    evaluation endpoint   │
│  └────────────────────────────────┘                          │
└──────────────────────────────────────────────────────────────┘
```

| Middleware | Type | Feature Flag | Per-Agent Toggle |
|-----------|------|-------------|-----------------|
| `PromptInjectionMiddleware` | `AgentMiddleware` | `ENABLE_PROMPT_INJECTION` (master) + `ENABLE_OUTPUT_INJECTION_SCAN` (output phase) | `"prompt_injection": true` |
| `ToolOutputInjectionMiddleware` | `FunctionMiddleware` | `ENABLE_TOOL_OUTPUT_INJECTION_SCAN` | `"prompt_injection": true` + agent has tools |
| `LLMLoggingMiddleware` | `AgentMiddleware` | `ENABLE_LLM_LOGGING` | Always on when enabled |
| `ToolCallCaptureMiddleware` | `FunctionMiddleware` | Always on | Always on |
| `OutputEvaluationMiddleware` | `AgentMiddleware` | `ENABLE_AGENT_EVALUATION` | `"evaluate": true` |

### Prompt Injection Detection

**Files:**
- `src/core/middleware/prompt_injection_middleware.py` — Agent input + output scanning (`AgentMiddleware`)
- `src/core/middleware/tool_injection_middleware.py` — Tool output scanning (`FunctionMiddleware`)

**API Reference:** `docs/PROMPT_INJECTION_API_GUIDE.md`

The prompt injection protection operates in **three phases** to cover all
attack surfaces in the agent pipeline:

```
 User Input                          Agent Output
     │                                    │
     ▼                                    ▼
┌─────────────┐   ┌─────────┐   ┌──────────────────┐   ┌──────────────┐
│ Phase 1:    │──▶│  Agent  │──▶│ Phase 3:          │──▶│  GroupChat   │
│ Input Scan  │   │ Execute │   │ Output Scan       │   │  Pool        │
│ (pre-exec)  │   │         │   │ (post-exec)       │   │              │
└─────────────┘   └────┬────┘   └──────────────────┘   └──────────────┘
                       │
                  ┌────▼────┐
                  │  Tool   │
                  │  Call   │
                  └────┬────┘
                       │
                  ┌────▼─────────────┐
                  │ Phase 2:          │
                  │ Tool Output Scan  │
                  │ (post-tool)       │
                  └──────────────────┘
```

#### Phase 1 — Input Scanning (Pre-execution)

The `PromptInjectionMiddleware` extracts the latest user message and sends
it to the RATIO prompt-injection orchestration API (`/v1/moderate`) **before**
the agent's LLM call. If the API returns `finalVerdict: "INJECTION"`, the
middleware raises `MiddlewareTermination` — the agent never executes.

#### Phase 2 — Tool Output Scanning (Post-tool)

The `ToolOutputInjectionMiddleware` (`FunctionMiddleware`) runs **after**
each MCP tool call completes. It serializes the tool result and sends it to
the same `/v1/moderate` API. If injection is detected in a tool's response
(e.g., a poisoned database result or MCP response), the tool result is
**replaced** with a safe sentinel message before the agent sees it. This
prevents indirect prompt injection via external data sources.

- Skips results shorter than 10 characters (unlikely to contain injection)
- Controlled by a separate environment variable (`ENABLE_TOOL_OUTPUT_INJECTION_SCAN`)
- Only attached to agents that have tools **and** `prompt_injection: true`

#### Phase 3 — Output Scanning (Post-execution)

After the agent executes (`await call_next()`), the `PromptInjectionMiddleware`
scans the agent's **output** text through the same `/v1/moderate` API. If
injection is detected in the output:

- **Non-streaming:** The agent's result is replaced with a safe sentinel
  message before it returns to the caller (and before it enters the
  GroupChat conversation pool).
- **Streaming:** A `stream_result_hook` is registered that scans the
  assembled response once the stream completes, and replaces it if needed.

This prevents **conversation pool poisoning** — where one agent's compromised
output could inject malicious instructions into subsequent agents in a
GroupChat workflow.

#### Request / Response contract

All three phases use the same API:
```json
{"userPrompt": "<text to scan>", "mode": "fast"}
```

The API returns:
```json
{
  "finalVerdict": "INJECTION" | "SAFE",
  "reasons": ["acs_prompt_shield", "stage1_residual"],
  "detectors": { ... },
  "latency_ms": {"end_to_end": 267.5}
}
```

The middleware acts **only** on `finalVerdict`. Detector-level detail is
logged for audit and visible in the debug UI.

#### Detection modes

| `mode` | Pipeline | When to use |
|--------|----------|-------------|
| `fast` | ACS + Stage-1 in parallel | Default — good for most cases (~300ms) |
| `standard` | ACS + Stage-1 → SLM arbiter | Higher confidence (~500ms) |
| `fast_query` | ACS + Stage-1 + SQL/KQL detector | Input may contain database queries |
| `standard_query` | ACS + Stage-1 + SQL/KQL → SLM | RAG pipelines with structured data |

#### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_PROMPT_INJECTION` | `false` | **Master switch** — enables/disables the entire `PromptInjectionMiddleware`. When `false`, no input or output scanning occurs. |
| `ENABLE_OUTPUT_INJECTION_SCAN` | `true` | **Output scan sub-flag** — controls Phase 3 (agent output scanning). Only relevant when `ENABLE_PROMPT_INJECTION=true`. When `false`, input scanning (Phase 1) still runs but output scanning is skipped. |
| `ENABLE_TOOL_OUTPUT_INJECTION_SCAN` | `false` | **Tool output scan flag** — controls Phase 2 (`ToolOutputInjectionMiddleware`). Independent of the other flags. When `true`, MCP tool results are scanned for injection before the agent processes them. |
| `PROMPT_INJECTION_API_URL` | `http://localhost:9001/v1/moderate` | Full URL of the `/v1/moderate` endpoint |
| `PROMPT_INJECTION_API_TIMEOUT` | `5` | HTTP timeout in seconds |
| `PROMPT_INJECTION_MODE` | `fast` | Detection mode (see table above) |
| `PROMPT_INJECTION_API_SCOPE` | _(empty)_ | AAD scope for Bearer token (optional) |

#### Behavior matrix

| `ENABLE_PROMPT_INJECTION` | `ENABLE_OUTPUT_INJECTION_SCAN` | `ENABLE_TOOL_OUTPUT_INJECTION_SCAN` | Input Scan | Output Scan | Tool Output Scan |
|---|---|---|---|---|---|
| `false` | _(ignored)_ | `false` | No | No | No |
| `false` | _(ignored)_ | `true` | No | No | **Yes** |
| `true` | `false` | `false` | **Yes** | No | No |
| `true` | `true` | `false` | **Yes** | **Yes** | No |
| `true` | `true` | `true` | **Yes** | **Yes** | **Yes** |

#### What happens when injection is detected

**Phase 1 (input):**
1. **Block:** `MiddlewareTermination` is raised — the agent's LLM call
   never executes and the malicious prompt never reaches the model.
2. **GroupChat:** The agent turn is aborted. The orchestrator receives no
   response from the blocked agent and continues with remaining agents.

**Phase 2 (tool output):**
1. **Replace:** The tool's return value is replaced with a safe sentinel
   message (`"[Tool result blocked — prompt injection detected]"`).
2. **Agent continues:** The agent still executes but sees the sanitized
   tool result instead of the poisoned one.

**Phase 3 (agent output):**
1. **Replace:** The agent's response is replaced with a safe sentinel
   message before it enters the GroupChat conversation pool.
2. **GroupChat:** Other agents see the sanitized message, preventing
   conversation pool poisoning.

**All phases:**
- **Telemetry:** Detection events are emitted with `scan_direction`
  (`"input"`, `"output"`, or `"tool_output"`) for audit differentiation.
- **UI:** Events flow through the SSE pipeline and render in the debug UI.
- **Logging:** `WARNING` log with agent name, verdict, scan direction, and reasons.

#### Fail-open behaviour

If the injection API is unreachable or returns a non-200 status, all three
middleware phases log a warning and **allow execution** (fail-open). The
telemetry events will show the error so operators can detect the degraded
state. This preserves availability when the safety sidecar is down.

---

## Performance and Scalability Optimizations

The investigation pipeline includes several optimizations to ensure reliable
performance at scale, enforce wall-clock guardrails on LLM calls, and
maximise throughput during signal evaluation.

### Agent Invocation Timeouts

**Problem:** An unresponsive LLM or a stalled GroupChat can block the entire
pipeline indefinitely.

**Solution:** Per-agent `asyncio.wait_for()` timeouts, configured in
`agents_config.json → investigation_workflow → agent_timeout_seconds`:

```json
"agent_timeout_seconds": {
  "triage_agent": 120,
  "action_planner": 120,
  "group_chat": 600,
  "default": 120
}
```

Enforcement points in `investigation_runner.py`:

| Agent / Stage | Mechanism | On Timeout |
|---------------|-----------|------------|
| Triage agent (standalone) | `asyncio.wait_for(agent.run(), timeout)` | Retries per `retry_policy` with backoff + enhanced prompt |
| GroupChat workflow | Elapsed-time check each event-loop iteration | Flushes accumulated chunks, yields error event, breaks loop |
| GroupChat agents (evidence_planner, reasoner) | Empty response detection in event loop | Standalone re-invocation per `retry_policy` |
| Action planner (standalone) | `asyncio.wait_for(agent.run(), timeout)` | Retries per `retry_policy` with backoff |

Fallback priority: per-agent value → `default` key → hard-coded 120 s.

### Structured Retry Policy

**Problem:** Only triage had retry (2 attempts, hardcoded). Evidence planner,
reasoner, and action planner had no retry at all. Agent failures in the
GroupChat went unrecovered.

**Solution:** Config-driven `retry_policy` in `investigation_workflow` with
per-agent `max_retries`, `backoff` strategy, and `backoff_base_seconds`.
All retry attempts emit `AgentRetry` telemetry events with attempt count,
reason, backoff duration, and investigation context.

Implementation in `investigation_runner.py`:

```python
# Config-driven retry lookup
_triage_retry = _get_retry_policy("triage_agent", retry_cfg)
_TRIAGE_MAX_RETRIES = max(_triage_retry["max_retries"], 1)

# Backoff between retries (linear, exponential, or none)
_waited = await _backoff_sleep(_triage_retry, attempt)

# Telemetry tracking per retry
tracker.log_agent_retry(
    xcv=xcv, agent_name="triage_agent", attempt=attempt,
    max_retries=_TRIAGE_MAX_RETRIES, reason="timeout after 120s",
    investigation_id=investigation.id, phase=investigation.phase.value,
)
```

### Parallel MCP Tool Calls (Signal Builder)

**Problem:** Sequential MCP/Kusto calls during signal evaluation create
unnecessary end-to-end latency — each poll cycle could issue 10+ Kusto
queries one at a time.

**Solution:** Three levels of `asyncio.gather()` parallelism with a shared
concurrency semaphore:

```
Level 1 — Signal types (SIG-TYPE-1 … SIG-TYPE-4) run in parallel
  └─ Level 2 — Collection tools within each type run in parallel
       └─ Level 3 — Dependency services within SIG-TYPE-4 run in parallel
            └─ All actual MCP calls go through _call_collection_tool()
                 which acquires a global asyncio.Semaphore
```

**Concurrency limit** (`signal_template.json → max_concurrent_mcp_calls`,
default **5**): Bounds the number of MCP/Kusto calls in flight at any
instant. Set to `1` to restore fully sequential behaviour.

**Graceful degradation** — all `gather()` calls use `return_exceptions=True`:

| Failure Level | Behaviour |
|---------------|-----------|
| Single tool call | Logged + skipped; remaining tools still processed |
| Dependency service | Logged + skipped; other dependencies still processed |
| Entire signal type | Logged; replaced with a no-data placeholder `TypeSignalResult` |

### Many-to-Many Signal → Evidence Requirement Mapping

**Problem:** The pre-population step in `investigation_runner.py` used a
`dict[str, str]` for the signal-type → evidence-requirement mapping.  Because
a Python dict can only hold one value per key, when a signal type satisfies
**multiple** evidence requirements (e.g. SIG-TYPE-4 → ER-DEP-002 *and*
ER-REGION-001) only the last one survived — the rest were silently dropped.

**Solution:** `_load_signal_type_to_er_mapping()` now returns
`dict[str, list[str]]`, and the pre-population loop iterates the full list of
ER-IDs for every activated signal type.  Evidence item IDs include the ER-ID
suffix (`ev-sig-sig_type_4_er_dep_002`) to guarantee uniqueness.

```
SIG-TYPE-4 ──┬── ER-DEP-002   (was dropped before)
             └── ER-REGION-001
```

**Config driven:** All mappings come from the `signal_source` field in
`evidence_requirements.json` — no code changes are needed when adding new
many-to-many relationships.

### Collection Strategy Pattern (Signal Builder)

**Problem:** Signal type evaluation routing was hard-coded as an `if/else`
chain inside `_evaluate_for_context()`.  Adding a new collection strategy
(e.g. REST API, Event Hub) required editing the routing function every time.

**Solution:** A **strategy registry** (`_COLLECTION_STRATEGIES`) maps
strategy names to async evaluation functions.  Two built-in strategies are
registered at module load:

| Strategy Name | Function | Used By |
|---------------|----------|---------|
| `standard` | `_evaluate_signal_type()` | SIG-TYPE-1, 2, 3 |
| `dependency_scan` | `_evaluate_dependency_signal_type()` | SIG-TYPE-4 |

**Extending with a new strategy:**

```python
from core.services.signals import register_collection_strategy

async def _my_custom_strategy(sig_type, context):
    ...  # custom collection logic
    return TypeSignalResult(...)

register_collection_strategy("my_custom", _my_custom_strategy)
```

Then in `signal_template.json`, set `"collection_strategy": "my_custom"` on the
relevant signal type.  Signal types without an explicit `collection_strategy`
default to `"standard"`.

An unknown strategy name raises `ValueError` with a list of registered
strategies, making misconfiguration immediately obvious.

### Token Management

**Problem:** Large-scale investigations with hundreds of signal data rows can overwhelm LLM context windows, causing incomplete or corrupted JSON outputs.

**Solution:** Intelligent row filtering in `investigation_runner.py`, configured
via `agents_config.json → investigation_workflow`:

```json
"max_rows_per_grain": 5,
"max_eval_hypotheses": 4
```

| Setting | Default | Purpose |
|---------|---------|----------|
| `max_rows_per_grain` | `3` | Max rows per granularity dimension in the triage task message |
| `max_eval_hypotheses` | `4` | Max hypotheses sent to the GroupChat for evaluation (ranked by `match_score`) |

```python
# Example: 20 granularities × 5 rows = 100 rows (may hit token limit)
# Adjust max_rows_per_grain to balance detail vs. token budget
```

**Granularity-Level `data_fields` Filtering:**

Each granularity can optionally define its own `data_fields` in `signal_template.json`.
When building the task message, `_build_task_message()` resolves columns per signal:

```python
# Granularity-level data_fields override signal-type-level data_fields
keep_fields = _fields_by_granularity.get(grain, type_fields)
```

This ensures pre-aggregated granularities (e.g., `multi_customer_same_product`) show
aggregated columns (`TotalCaseCount`, `DistinctCustomerCount`, `CustomerList`, etc.)
while other granularities in the same signal type still show individual-row columns.

**Aggregate Summary Fallback:**

When rows exceed `MAX_ROWS_PER_GRAIN`, an `AGGREGATE:` summary line is appended
with computed totals (total_rows, distinct_customers, distinct_products, max_severity,
critsit_count, escalated_count) so the LLM can reason over the full scope even with
truncated row data.

**Pre-Aggregated Queries:**

For granularities where the LLM needs to reason over counts that are difficult to
derive from individual rows (e.g., "how many distinct customers filed cases for the
same product"), dedicated **pre-aggregated Kusto queries** return summary-level data
directly. The `pre_aggregated:<FieldName>` aggregate type in `signal_template.json`
reads the value from the first row instead of computing across rows:

```json
"aggregates": {
  "customer_count": "pre_aggregated:DistinctCustomerCount",
  "total_case_count": "pre_aggregated:TotalCaseCount",
  "max_severity": "pre_aggregated:MaxSeverity"
}
```

**Triage Agent Token Limits** (`agents_config.json`):
- `max_completion_tokens: 16000` (stays within gpt-4o's 16,384 output token limit)
- Ensures complete JSON output even with complex symptom lists

### Hypothesis Scoring Precision

**Problem 1 — False Matches:** Hypotheses requiring specific signal types (e.g., outages, dependencies) were matching even when those signals were absent.

**Solution:** `required_symptoms` enforcement with AND logic in `hypothesis_scorer.py`:

```python
# Critical gate before scoring — ALL required symptoms must be present
if required:
    missing = [r for r in required if r not in confirmed_ids]
    if missing:
        # Skip this hypothesis — cannot match without ALL required symptoms
        continue
```

**Use Cases:**
- **Outage hypotheses** (HYP-OUT-*): Require ALL `SYM-OUT-*` symptoms → prevent matching when any outage signal is absent
- **Dependency hypotheses** (HYP-SLI-004): Require ALL `SYM-DEP-*` symptoms → prevent matching when any dependency signal is absent
- **Support-driven hypotheses** (HYP-SLI-006): Require `SYM-SUP-*` symptom → prevent matching when no support cases

**Problem 2 — Category Mismatches:** Capacity-specific hypotheses were matching against connectivity/latency SLIs.

**Solution:** Category-aware scoring with `relevant_sli_categories`:

```python
# Category modifier in scoring formula
category_modifier = 1.5  # Boost if symptom categories match hypothesis
                  = 0.5  # Penalty if categories mismatch
                  = 1.0  # Neutral if "any" or no category specified
```

**Example:**
- HYP-SLI-003 (Capacity Exhaustion): `relevant_sli_categories: ["capacity", "availability", "performance"]`
- If symptom has `sli_category: "connectivity"` → **0.5× penalty** (deprioritized)
- If symptom has `sli_category: "capacity"` → **1.5× boost** (prioritized)

### Scoring Formula (Complete)

```
match_score = (weighted_matched / weighted_total) × weighted_avg_signal_strength
final_score = min(match_score × category_modifier, max_score)

where:
  weighted_avg_signal_strength = Σ(weight_i × strength_i) / Σ(weight_i)
  category_modifier = 1.5 (match) | 0.8 (unknown) | 0.5 (mismatch) | 1.0 (neutral)
  max_score = 7.5 (configurable via scoring_config.json)
```

All scoring parameters (aggregation strategy, weight defaults, category modifiers,
score cap) are externalized to `config/hypotheses/scoring_config.json` — no code
changes needed to tune scoring behaviour.

This ensures hypotheses are **accurately matched** to the correct root cause scenarios while **preventing false positives** from missing signal types or category mismatches.

---

## MCP Integration

The system uses the **Model Context Protocol (MCP)** to access external data
sources. All data collection happens through MCP tools served by the RATIO
MCP Server.

**File:** `src/core/mcp_integration.py`

> **Note:** The `signal_source` → `ER-ID` mapping used for pre-populating
> signal-sourced evidence is loaded dynamically from
> `config/evidence/evidence_requirements.json` (no longer hardcoded).

```
┌─────────────────────────────────────────────────────────────────┐
│                    MCP Architecture                               │
│                                                                  │
│  Customer Agent                          RATIO MCP Server        │
│  ┌───────────────┐                      ┌───────────────────┐   │
│  │ Agent / Signal │──── HTTP/SSE ───────▶│  MCP Endpoint     │   │
│  │ Builder        │     + Auth headers   │  /mcp             │   │
│  │                │     + X-User-Token   │                   │   │
│  │ MCPStreamable- │     + X-XCV          │  ┌─────────────┐ │   │
│  │ HTTPTool       │                      │  │ Kusto Query │ │   │
│  └───────────────┘                      │  │ Engine      │ │   │
│                                          │  └─────────────┘ │   │
│  Auth: DefaultAzureCredential            │  ┌─────────────┐ │   │
│        + CertificateCredential (KV)      │  │ IcM API     │ │   │
│        + User token pass-through         │  └─────────────┘ │   │
│                                          └───────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Tool Modes

Agents are configured with different tool access levels via the **tool-mode plugin registry** (`TOOL_MODE_HANDLERS` in `agent_factory.py`):

| Mode | Behavior | Example Agents |
|------|----------|---------------|
| `none` | No MCP tools | orchestrator, reasoner, action_planner |
| `filtered` | Only specified MCP tools | sli_collector, incident_collector |
| `all` | All available MCP tools | (not currently used) |
| `agent_tools` | Sub-agent invocation (no MCP) | evidence_planner, analyst_coordinator |

**Custom tool modes** can be registered at startup:

```python
from core.agent_factory import register_tool_mode

def _my_custom_mode(agent_cfg, ctx):
    # Return a list of tool instances
    return [my_custom_tool]

register_tool_mode("custom", _my_custom_mode)
```

### Middleware Registry

Per-agent middleware is assembled from the **middleware registry** (`MIDDLEWARE_REGISTRY` in `agent_factory.py`). The order is controlled by the agent's `"middleware"` config key (falls back to the default: `["prompt_injection", "tool_capture", "tool_injection", "eval", "llm_logging"]`).

| Registry Name | Middleware Class | Condition |
|---------------|-----------------|-----------|
| `prompt_injection` | `PromptInjectionMiddleware` | `prompt_injection: true` in config + globally enabled |
| `tool_capture` | `ToolCallCaptureMiddleware` | Agent has tools |
| `tool_injection` | `ToolOutputInjectionMiddleware` | `prompt_injection: true` in config + agent has tools + globally enabled |
| `eval` | `OutputEvaluationMiddleware` | `evaluate: true` in config + globally enabled |
| `llm_logging` | `LLMLoggingMiddleware` | `llm_logging: true` (default) + globally enabled |

**Custom middleware** can be registered at startup:

```python
from core.agent_factory import register_middleware

def _my_mw_factory(agent_name, agent_cfg, shared):
    if agent_cfg.get("my_feature", False):
        return MyMiddleware(agent_name=agent_name)
    return None

register_middleware("my_feature", _my_mw_factory)
```

Then add `"my_feature"` to the agent's `"middleware"` list in `agents_config.json`.

### MCP Tool Validation

At factory startup, `create_agents()` calls `validate_mcp_tool_references()` which queries the MCP server's `tools/list` JSON-RPC endpoint to discover available tools. All `mcp_tools` references in agent configs are validated against this list. Mismatches produce a WARNING log with the full list of available tools.

If the MCP server is unreachable, validation is skipped gracefully and agents are still created — misconfigured tool names will only fail at runtime.

### Agent Creation Order (Topological Sort)

Agents with `tool_mode: "agent_tools"` depend on their `sub_agents` being available for `as_tool()` wiring. The factory uses `_topological_sort()` (Kahn's algorithm) to order agent creation so sub-agents are always created before their coordinators. This eliminates the previous two-pass approach where coordinator agents were created without tools and then recreated.

If a cycle is detected in `sub_agents` dependencies, the factory falls back to the original config order with a warning.

### MCP Auth Robustness

The MCP integration layer (`mcp_integration.py`) and auth helper (`helper/auth.py`) include several robustness measures:

| Scenario | Behavior |
|----------|----------|
| Bearer token is `None` | WARNING logged on every request (both `_inject_auth_headers` and `_header_provider`) |
| Token expired | INFO log on refresh with `expires_on` vs current time |
| All credential methods fail | WARNING with checklist: MCP_AUTH_AUDIENCE, managed identity, certificate config |
| MCP server returns 401 | `discover_mcp_tools()` logs specific 401 warning with guidance |

### Config Schema Validation

All JSON config files are validated at load time using Pydantic models in `core/models/config/`. Structural errors (missing fields, wrong types) surface immediately with clear messages instead of causing runtime crashes deep in the pipeline.

| Config File | Pydantic Model | Load Behavior |
|-------------|---------------|---------------|
| `agents_config.json` | `AgentsFileConfig` | Hard fail — `ValueError` on invalid schema |
| `signal_template.json` | `SignalTemplateFileConfig` | Hard fail — `ValueError` |
| `monitoring_context.json` | `MonitoringContextFileConfig` | Hard fail — `ValueError` |
| `evidence_requirements.json` | `EvidenceFileConfig` | Hard fail — `ValueError` |
| `symptoms/*.json` | `SymptomFileConfig` | Hard fail — `ValueError` |
| `hypotheses/*.json` | `HypothesisFileConfig` | Hard fail — `ValueError` |
| `actions/*.json` | `ActionCatalogFileConfig` | Hard fail — `ValueError` |
| `dependency_services/*.json` | `DependencyServiceFileConfig` | Warn + continue (per-file graceful) |

All models use `extra="allow"` so new config keys can be added without breaking validation.

---

## Investigation Narrator (Optional)

**Files:** `src/core/services/investigation/investigation_narrator.py` + `src/prompts/investigation_narrator_prompt.txt`  
**Feature Flag:** `narrator_enabled: true` in `investigation_workflow` config  
**Model:** gpt-4o (temperature: 0.7)  
**Runs:** Standalone (outside GroupChat, after each agent turn)

The **narrator agent** is an optional LLM-powered component that observes the investigation pipeline and produces **first-person human-readable narration** of what's happening at each stage. It translates technical agent outputs into natural language storytelling for improved user experience.

### How It Works

```
┌────────────────────────────────────────────────────────────────┐
│                  Narrator Pipeline                               │
│                                                                │
│  Investigation Stage (signal_builder, triage, reasoner, etc.)  │
│          │                                                      │
│          │ completes turn                                       │
│          ▼                                                      │
│  ┌──────────────────┐                                           │
│  │ investigation_   │  Receives:                                │
│  │ runner           │  • Agent name                             │
│  │                  │  • Agent output (truncated to              │
│  │                  │    _AGENT_OUTPUT_MAX_CHARS, default 4000)  │
│  │                  │  • Investigation state summary (capped to  │
│  │                  │    _STATE_MAX_HYPOTHESES/SYMPTOMS/SIGNALS) │
│  │                  │  • Current phase                          │
│  └────────┬─────────┘                                           │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────┐                                           │
│  │ narrator_agent   │  Generates first-person narrative:        │
│  │ (gpt-4o)         │  "I'm starting my investigation..."      │
│  │                  │  "I've confirmed 4 symptoms..."          │
│  │                  │  "Based on the evidence collected..."    │
│  └────────┬─────────┘                                           │
│           │                                                     │
│           ▼                                                     │
│  ┌──────────────────┐  SSE Events:                             │
│  │ _timeout_wrapper  │  Per-chunk asyncio.wait_for              │
│  │ Token-by-token   │  (_STREAM_TIMEOUT_SECONDS, default 60)   │
│  │ streaming        │  • investigation_narrator_chunk          │
│  │                  │  • investigation_narrator_done           │
│  └────────┬─────────┘                                           │
│           │                                                     │
│           ▼                                                     │
│  UI Sidebar (real-time narration panel)                        │
└────────────────────────────────────────────────────────────────┘
```

### Narration Style

The narrator speaks in **first person** as if it is performing the investigation:
- "I'm starting my investigation for customer Contoso..."
- "I identified 4 confirmed symptoms including SLI breach patterns..."
- "After reviewing all the collected evidence, I'm confirming that 'Service Degradation due to Regional Capacity' is indeed contributing..."
- "I've now identified the actions to address the confirmed issues..."

### When Narration Occurs

Narration is generated after:
1. **Signal Builder** completes — "I found 6 activated signals across availability, latency, and support ticket categories..."
2. **Triage Agent** completes — "I identified 4 confirmed symptoms... I've scored 3 candidate hypotheses..."
3. **Each GroupChat Turn** (evidence_planner, reasoner) — "I'm planning evidence collection..." or "I'm confirming this hypothesis..."
4. **Action Planner** completes — "I'm recommending a capacity scale-out and priority escalation..."

### Configuration

Enable/disable via `agents_config.json`:

```json
{
  "investigation_workflow": {
    "narrator_enabled": true,
    "max_turns": 40,
    "max_evidence_cycles": 2
  }
}
```

The narrator agent configuration:

```json
{
  "name": "narrator",
  "description": "Observes investigation agent outputs and produces first-person human-readable narration of the investigation flow. Runs outside the GroupChat after each agent turn.",
  "prompt_file": "investigation_narrator_prompt.txt",
  "model": "gpt-4o",
  "temperature": 0.7,
  "tool_mode": "none",
  "evaluate": false,
  "prompt_injection": true
}
```

### SSE Events

The narrator streams narration via Server-Sent Events:

| Event Type | Payload | Purpose |
|------------|---------|------|
| `investigation_narrator_chunk` | `{"agent": "signal_builder", "phase": "initializing", "text": "I'm starting..."}` | Partial narration text (streaming) |
| `investigation_narrator_done` | `{"agent": "signal_builder", "phase": "initializing"}` | Narration complete for this turn |
| `investigation_milestone` | `{"phase": "triage", "message": "Symptoms identified"}` | Phase transition markers |

### Resilience & Tuning

The narrator has several safeguards to prevent stalls and runaway token usage:

| Control | Default | Override via |
|---------|---------|-------------|
| Agent output truncation | 4 000 chars | `set_narrator_limits(agent_output_max_chars=...)` |
| Max hypotheses in state | 5 | `set_narrator_limits(state_max_hypotheses=...)` |
| Max symptoms in state | 5 | `set_narrator_limits(state_max_symptoms=...)` |
| Max signals in state | 5 | `set_narrator_limits(state_max_signals=...)` |
| Log input truncation | 500 chars | `set_narrator_limits(log_input_max_chars=...)` |
| Per-chunk stream timeout | 60 s | `set_narrator_limits(stream_timeout_seconds=...)` |

**Error handling:** The narrator classifies exceptions into `TimeoutError`,
`ConnectionError`/`OSError`, and generic `Exception`. Empty-chunk sequences
are tracked and narration is terminated early if the stream stalls. All
narrator failures are non-fatal — the investigation pipeline continues
normally.

### Error Handling

Narrator errors are **non-fatal** — if narration fails, the investigation continues normally:

```python
try:
    async for narrator_event in narrate_agent_turn(...):
        yield narrator_event
except Exception as narr_exc:
    logger.warning("Narrator error (non-fatal): %s", narr_exc)
    # Investigation continues
```

### Use Cases

- **User-facing dashboards**: Real-time investigation progress in plain language
- **Audit trails**: Human-readable summaries of agent reasoning
- **Debugging**: Understanding investigation flow without parsing JSON
- **Stakeholder communication**: Translating technical output for non-technical audiences

---

## Streaming Pipeline & SSE Architecture

**File:** `src/server/app.py`

The `/api/run` POST endpoint implements a **multiplexed SSE streaming
architecture** that runs signal evaluation and investigations concurrently,
delivering events to the client in real time as they occur.

### Event Multiplexing

Three async producer tasks feed a central `asyncio.Queue` (`output_queue`),
which the main event loop drains and serialises as SSE frames:

```
POST /api/run
  │
  ├─ Subscribe to AgentLogger events (event_queue)
  │
  ├─ Producer 1: _signal_eval_producer()
  │    └─ evaluate_signals_stream() → yields SignalBuilderResult
  │         └─ For each actionable result → spawn _run_one_investigation() task
  │
  ├─ Producer 2: _logger_drain()
  │    └─ Drains AgentLogger subscriber queue (0.15s timeout)
  │         └─ MCP calls, agent invocations, phase transitions, etc.
  │
  ├─ Producer 3: _run_one_investigation() (one task per service)
  │    └─ run_investigation() → yields investigation events
  │         └─ Tagged with service_xcv, service_tree_id, service_name
  │
  └─ Main Loop:
       ├─ output_queue.get() with 0.25s timeout
       ├─ _stamp() adds seq, timestamp, pipeline_xcv
       ├─ Route by sentinel type → SSE frame
       └─ Yield to client
```

**Sentinel event types** used for internal routing:

| Sentinel | Source | Meaning |
|----------|--------|---------|
| `_SIGNAL_RESULT` | Signal eval producer | A `SignalBuilderResult` completed |
| `_INVESTIGATION_EVENT` | Investigation task | An investigation yielded an event |
| `_LOGGER_EVENT` | Logger drain | An `AgentLogger._emit()` event arrived |
| `_EVAL_DONE` / `_EVAL_ERROR` | Signal eval producer | Signal evaluation finished/failed |
| `_INV_DONE` / `_INV_ERROR` | Investigation task | Investigation finished/failed |

### Streaming Signal Evaluation

The signal builder provides two evaluation modes:

| Function | Mode | Mechanism | Use Case |
|----------|------|-----------|----------|
| `evaluate_signals()` | Batch | `asyncio.gather()` — returns all results at once | One-shot CLI runs |
| `evaluate_signals_stream()` | Streaming | `asyncio.wait(FIRST_COMPLETED)` — yields each result as it finishes | API server (real-time SSE) |

Both modes run per-service evaluations in parallel via `asyncio.create_task()`.
The streaming variant allows investigations to **start immediately** for
fast-evaluating services without waiting for all services to complete:

```
┌──────────────────────────────────────────────────────────────────────┐
│         Streaming Pipeline (evaluate_signals_stream)                  │
│                                                                      │
│  Time ──────────────────────────────────────────────────────────▶    │
│                                                                      │
│  Service A: ████ eval ████ → yield → investigation starts            │
│  Service B: ████████ eval ████████ → yield → investigation starts    │
│  Service C: ████ eval ████ → yield → investigation starts            │
│                                                                      │
│  vs. Batch (evaluate_signals):                                       │
│  Service A: ████ eval ████ ─┐                                        │
│  Service B: ████████ eval ──┼─ gather ──▶ all investigations start   │
│  Service C: ████ eval ████ ─┘                                        │
└──────────────────────────────────────────────────────────────────────┘
```

### Per-Service Context Isolation

Each service evaluation runs in its own `asyncio.create_task()`, which
copies the current `contextvars.Context`. This provides automatic isolation
of per-service state:

```python
# Inside evaluate_signals_stream() — per-service task
async def _evaluate_one(ctx):
    xcv = generate_xcv()              # Fresh XCV per service
    set_current_xcv(xcv)              # Bind to this task's context
    set_current_service_tree_id(ctx["service_tree_id"])  # Bind service
    # All downstream logging inherits both ContextVars
    return await _evaluate_for_context(template, ctx)
```

The same pattern is used in `_run_one_investigation()` (app.py) before
launching the investigation pipeline:

```python
async def _run_one_investigation(r):
    set_current_xcv(service_xcv)
    set_current_service_tree_id(r.service_tree_id)
    async for inv_event in run_investigation(r):
        inv_event['service_xcv'] = service_xcv
        inv_event['service_tree_id'] = r.service_tree_id
        inv_event['service_name'] = r.service_name
        await output_queue.put((_INVESTIGATION_EVENT, inv_event))
```

### `_stamp()` Function

Every SSE event passes through `_stamp()` before serialisation:

```python
def _stamp(event: dict) -> str:
    _seq += 1
    event["seq"] = _seq                    # Monotonic sequence number
    event.setdefault("timestamp", time.time())  # Wall-clock time
    event["pipeline_xcv"] = xcv            # Parent pipeline XCV
    return f"data: {json.dumps(event, default=str)}\n\n"
```

| Field | Type | Description |
|-------|------|-------------|
| `seq` | `int` | Monotonically increasing; primary sort key for UI rendering |
| `timestamp` | `float` | `time.time()` — may be out of order across async producers |
| `pipeline_xcv` | `str` | Parent XCV for the entire `/api/run` invocation |

---

## Debug UI & Service Filter

**Files:** `CustomerAgentUI/index.html`, `CustomerAgentUI/app.js`, `CustomerAgentUI/styles.css`

The debug UI is a **vanilla JavaScript** (ES6 modules) single-page application
served from `CustomerAgentUI/` by `server.py` (port 5020, proxies `/api/*`
to the backend on port 8503).

### Service Filter Dropdown

When monitoring multiple services for a customer, the UI provides a
**per-service filter dropdown** that isolates events to a single
`service_tree_id`. The filter appears automatically once 2+ services
are detected.

```
┌─────────────────────────────────────────────────────────────────┐
│  Service: [All Services ▾]  XCV: CA-20260420-143022-a1b2c3d4   │
├─────────────────────────────────────────────────────────────────┤
│  (event cards filtered to selected service)                     │
└─────────────────────────────────────────────────────────────────┘
```

**State variables:**

| Variable | Type | Purpose |
|----------|------|---------|
| `_activeServiceFilter` | `string` | Currently selected `service_tree_id` (or `'__all__'`) |
| `_serviceMap` | `object` | `service_tree_id → { service_name, service_xcv }` |
| `_xcvToServiceMap` | `object` | `xcv → service_tree_id` (reverse lookup for events that only carry XCV) |

**Population:** The filter is populated from `SignalEvaluationStart` events
(which carry `ServiceTreeId` and `ServiceName`). As each service begins
evaluation, `_addServiceToFilter()` registers it.

### Event Filtering Logic (`_matchesServiceFilter`)

When a service is selected, events are matched using a three-tier strategy:

```
1. Direct match:   event.service_tree_id === selectedFilter  → pass/reject
2. XCV lookup:     _xcvToServiceMap[event.source_xcv] → service_tree_id → match
3. Global types:   pipeline_started, pipeline_complete, etc. → always pass
```

This handles all event sources:
- **Signal events**: Carry `service_tree_id` directly (from ContextVar auto-enrichment)
- **Investigation events**: Carry `service_tree_id` (tagged by `_run_one_investigation()`)
- **Logger events**: Carry `service_tree_id` (auto-injected by `_emit()`) or `source_xcv` (fallback via reverse lookup)
- **Pipeline-level events**: Always shown regardless of filter

### Filter Interaction

- Changing the filter re-renders the active view by replaying `_allEvents[]`
  through `_matchesServiceFilter()`
- The selected service's XCV is displayed next to the dropdown for tracing
- Filter state is preserved across view switches (stream ↔ agent flow)

---

## Observability & Telemetry

**File:** `src/helper/agent_logger.py`

The `AgentLogger` singleton provides comprehensive telemetry across the
entire pipeline, publishing events to **Azure Application Insights** and
optionally to a **UI event queue** for real-time streaming.

### Event Categories

| Category | Events | Purpose |
|----------|--------|---------|
| **Signal Building** | `SignalEvaluationStart`, `MCPCollectionCall`, `SignalTypeEvaluated`, `CompoundEvaluated`, `SignalDecision` | Track data collection and activation decisions |
| **Investigation Lifecycle** | `InvestigationCreated`, `PhaseTransition`, `WorkflowStarted` | Track investigation state machine (see below) |
| **Hypothesis Tracking** | `HypothesisScoring`, `HypothesisSelected`, `HypothesisTransition` | Track hypothesis evaluation progress |
| **Agent Activity** | `AgentInvoked`, `AgentResponse`, `OutputParsed`, `SpeakerSelected` | Track individual agent turns |
| **Narration** | `investigation_narrator_chunk`, `investigation_narrator_done`, `investigation_milestone` | Stream human-readable narration (optional) |
| **Evidence** | `EvidenceCycle`, `ToolCall` | Track data collection during investigation |
| **Telemetry Completeness** | `DataTruncation`, `TokenBudget`, `EvidenceCycleCount`, `HypothesisCycleCount`, `ColumnDrop`, `CycleReset` | Track truncation, token usage, and cycle progression |
| **Error Classification** | `error_category` field on all error yields | Structured error type from `classify_exception()` |
| **Security** | `PromptInjectionDetected`, `InjectionApiCall` | Track prompt injection attempts and API audit trail |

### PhaseTransition Events

The `PhaseTransition` event is emitted to Application Insights every time the
investigation state machine advances to a new phase. Each event includes:

| Field | Description | Example |
|-------|-------------|---------|
| `InvestigationId` | Investigation identifier | `INV-20260420-143022` |
| `FromPhase` | Previous phase | `HYPOTHESIZING` |
| `ToPhase` | New phase | `PLANNING` |
| `Agent` | Agent that triggered the transition | `evidence_planner` |

**Sources of phase transitions:**

| Source | Mechanism | Phases Covered |
|--------|-----------|----------------|
| `investigation_runner` Stage 1 | Explicit `transition_to()` | INITIALIZING → TRIAGE |
| `investigation_output_parser` | `phase_complete` signal from agent output | Any (if agent emits it) |
| `investigation_runner` Stage 2 | Explicit `transition_to()` | TRIAGE → HYPOTHESIZING |
| `investigation_runner` auto-advance | Inferred from which agent spoke | HYPOTHESIZING → PLANNING → COLLECTING → REASONING |
| `investigation_runner` auto-backtrack | Evidence cycle detected | REASONING → PLANNING |
| `investigation_runner` Stage 5 | Explicit `transition_to()` | → ACTING → NOTIFYING → COMPLETE |

The auto-advance mechanism ensures all intermediate GroupChat phases appear in
Application Insights even when agents omit `phase_complete` signals. See
[Agent-Based Phase Auto-Advance](#agent-based-phase-auto-advance) in Stage 4.

**Kusto query to verify phase transitions for a run:**

```kql
customEvents
| where customDimensions.EventName == "PhaseTransition"
| where customDimensions.xcv == "<your-xcv>"
| project
    timestamp,
    FromPhase = tostring(customDimensions.FromPhase),
    ToPhase   = tostring(customDimensions.ToPhase),
    Agent     = tostring(customDimensions.Agent)
| order by timestamp asc
```

A healthy investigation should show 7–9 transitions covering the full lifecycle
(INITIALIZING through COMPLETE).

### SSE Event Ordering

The pipeline SSE generator (`app.py → pipeline_generator()`) stamps every event
with a monotonic **`seq`** (sequence number) before sending it to the client.
This provides an authoritative ordering for UI rendering, since events from
multiple async sources (AgentLogger queue, investigation runner generator,
narrator coroutines) can arrive with out-of-order timestamps.

| Field | Type | Description |
|-------|------|-------------|
| `seq` | `int` | Monotonically increasing, assigned at the SSE serialization point |
| `timestamp` | `float` | `time.time()` from the event producer (may be out of order) |
| `pipeline_xcv` | `str` | Parent XCV for the entire pipeline run |

The UI stream view (`views/stream.js`) inserts cards at the correct DOM position
using `seq` as the primary sort key (falling back to `timestamp` when `seq` is
unavailable). This ensures cards always appear in chronological order regardless
of SSE arrival order.

### Correlation (XCV) — Per-Service Isolation

The pipeline uses a **two-level XCV hierarchy** for correlation:

1. **Pipeline XCV** — A single XCV for the entire `/api/run` invocation,
   stamped on every SSE event as `pipeline_xcv`.
2. **Service XCV** — A fresh XCV generated per `(customer, service_tree_id)`
   context. Each signal evaluation and investigation runs under its own
   service XCV for fine-grained tracing.

```
Pipeline XCV: CA-20260420-143022-a1b2c3d4  (stamped on all SSE events)
  │
  ├─ Service A XCV: CA-20260420-143025-e5f6g7h8
  │     SignalEvaluationStart    ──┐
  │     MCPCollectionCall (×4)   ──┤  All share Service A XCV
  │     SignalTypeEvaluated (×4) ──┤  + service_tree_id = "49c39e84-..."
  │     SignalDecision           ──┤
  │     InvestigationCreated     ──┤
  │     PhaseTransition (×7)     ──┤
  │     AgentInvoked (×6)        ──┤
  │     ToolCall (×8)            ──┘
  │
  └─ Service B XCV: CA-20260420-143026-i9j0k1l2
        SignalEvaluationStart    ──┐
        MCPCollectionCall (×3)   ──┤  All share Service B XCV
        SignalTypeEvaluated (×3) ──┤  + service_tree_id = "7a2b3c4d-..."
        SignalDecision           ──┘
        (no investigation — action=quiet)
```

**XCV Resolution in Investigation Runner:**

The investigation runner resolves XCV via a priority chain:

| Priority | Source | When Used |
|----------|--------|-----------|
| 1 (highest) | ContextVar (`get_current_xcv()`) | Set by `app.py → _run_one_investigation()` |
| 2 | `result.xcv` from `SignalBuilderResult` | Fallback when ContextVar is empty |
| 3 (lowest) | `generate_xcv()` | Fresh generation when neither is available |

### Service Tree ID ContextVar Propagation

The `_current_service_tree_id` ContextVar (`agent_logger.py`) provides
**automatic service attribution** for every event in the pipeline without
requiring explicit parameter passing through all function signatures.

**ContextVar lifecycle:**

```
┌──────────────────────────────────────────────────────────────────────┐
│                service_tree_id ContextVar Flow                        │
│                                                                      │
│  Set at entry points:                                                │
│    signal_builder.py  → set_current_service_tree_id(ctx["..."])     │
│    app.py             → set_current_service_tree_id(r.service_...)  │
│    investigation_runner.py → set_current_service_tree_id(result...) │
│                                                                      │
│  Inherited automatically by:                                         │
│    asyncio.create_task() copies ContextVar state per task           │
│    → All MCP tool calls                                             │
│    → All agent invocations (triage, evidence_planner, reasoner)     │
│    → All middleware (prompt injection, eval, tool capture)           │
│    → All logging calls                                              │
│                                                                      │
│  Read at emission:                                                   │
│    agent_logger._emit()  → get_current_service_tree_id()            │
│      → App Insights: props["ServiceTreeId"] = svc_id               │
│      → UI events:    event["service_tree_id"] = svc_id             │
│                                                                      │
│  Result: Every event carries service_tree_id without any caller     │
│          needing to pass it explicitly                               │
└──────────────────────────────────────────────────────────────────────┘
```

**ContextVars defined in `agent_logger.py`:**

| ContextVar | Purpose | Set By |
|------------|---------|--------|
| `_current_xcv` | Per-request/per-service correlation ID | `signal_builder`, `app.py`, `investigation_runner` |
| `_current_service_tree_id` | Which service is being evaluated/investigated | `signal_builder`, `app.py`, `investigation_runner` |
| `_current_tool_stage` | Pipeline phase (e.g. `"signal_building"`, `"investigation:collecting:HYP-001"`) | Various pipeline stages |

**Auto-enrichment in `_emit()`:**

The `_emit()` method reads `get_current_service_tree_id()` and injects
`ServiceTreeId` into App Insights properties and `service_tree_id` into
UI events — only if not already present (explicit values from callers win):

```python
svc_id = get_current_service_tree_id()
if svc_id and "ServiceTreeId" not in props:
    props["ServiceTreeId"] = svc_id        # App Insights
if svc_id:
    ui_event.setdefault("service_tree_id", svc_id)  # UI SSE events
```

**Kusto query to filter events by service:**

```kql
customEvents
| where customDimensions.ServiceTreeId == "<service-tree-id>"
| where customDimensions.xcv startswith "CA-"
| project timestamp, EventName=tostring(customDimensions.EventName),
    xcv=tostring(customDimensions.xcv),
    ServiceTreeId=tostring(customDimensions.ServiceTreeId)
| order by timestamp asc
```

---

## Entry Points

### One-Shot Run

```bash
python run_signal_builder.py [--customer "BlackRock, Inc"] [--service-tree-id "49c39e84-..."]
```

Evaluates signals once and runs investigations for any actionable results.
Uses CLI arguments or falls back to `config/monitoring_context.json`.

### Continuous Loop

```bash
python run_signal_builder_loop.py [--interval 60]
```

Polls on a timer (default: `poll_interval_minutes` from monitoring context).
Automatically triggers investigations when signals activate. Press Ctrl+C to stop.

### API Server

```bash
python src/server/app.py
```

FastAPI server on port 8503 with SSE streaming for real-time investigation
event delivery.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/run` | POST | Run signal evaluation + investigation pipeline with SSE streaming |
| `/api/stream` | POST | SSE endpoint for streaming workflow events |
| `/health` | GET | Health check (`{"status": "ok"}`) |
| `/a2a/agents` | GET | List all A2A agent cards |
| `/a2a/{agent}/agent-card` | GET | A2A agent discovery |
| `/a2a/{agent}/` | POST | Invoke agent independently (A2A JSON-RPC) |

The `/api/run` endpoint uses the streaming pipeline architecture
(see [Streaming Pipeline & SSE Architecture](#streaming-pipeline--sse-architecture))
to deliver events in real time as signal evaluation and investigations proceed
concurrently.

### Debug UI Server

```bash
cd CustomerAgentUI && python server.py
```

Static file server on port 5020 serving the debug UI. Proxies `/api/*`
requests to the backend on port 8503. Includes SPA fallback routing.

---

## Project Structure

```
Code/CustomerAgent/
├── run_signal_builder.py           ← One-shot entry point
├── run_signal_builder_loop.py      ← Continuous polling entry point
├── requirements.txt                ← Python dependencies
├── .env                            ← Environment variables (not committed)
│
├── src/
│   ├── server/
│   │   └── app.py                  ← FastAPI server (SSE streaming)
│   │
│   ├── core/
│   │   ├── services/
│   │   │   ├── signals/
│   │   │   │   ├── signal_builder.py   ← Deterministic signal evaluation engine
│   │   │   │   ├── signal_models.py    ← Data models (ActivatedSignal, CompoundSignal, etc.)
│   │   │   │   └── symptom_matcher.py  ← Symptom template loader and formatter
│   │   │   │
│   │   │   └── investigation/
│   │   │       ├── investigation_runner.py       ← Standalone triage + GroupChat + standalone action planning
│   │   │       ├── investigation_narrator.py     ← LLM narrator with configurable truncation + timeout wrapper
│   │   │       ├── investigation_state.py        ← Data models (Investigation, Hypothesis, etc.)
│   │   │       ├── investigation_output_parser.py← Agent output parsing + markdown fallback symptom extractor
│   │   │       ├── hypothesis_scorer.py          ← Programmatic hypothesis scoring (config via scoring_config.json)
│   │   │       └── investigation_speaker_selector.py ← Deterministic speaker selection
│   │   │
│   │   ├── models/
│   │   │   └── config/
│   │   │       ├── __init__.py                  ← Re-exports all config validation models
│   │   │       ├── symptom_template.py           ← SymptomFileConfig (Pydantic v2)
│   │   │       ├── hypothesis_template.py        ← HypothesisFileConfig (Pydantic v2)
│   │   │       ├── evidence_requirement.py       ← EvidenceFileConfig (Pydantic v2)
│   │   │       ├── action_catalog.py             ← ActionCatalogFileConfig (Pydantic v2)
│   │   │       ├── agents.py                     ← AgentsFileConfig, InvestigationWorkflowConfig
│   │   │       ├── phase_pipeline.py             ← PhaseConfig, PhasePipelineConfig
│   │   │       ├── signal_template.py            ← SignalTemplateFileConfig (Pydantic v2)
│   │   │       ├── monitoring_context.py         ← MonitoringContextFileConfig (Pydantic v2)
│   │   │       └── dependency_service.py         ← DependencyServiceFileConfig (Pydantic v2)
│   │   │
│   │   ├── agent_factory.py        ← Config-driven agent creation
│   │   ├── mcp_integration.py      ← MCP tool creation with auth
│   │   ├── prompt_loader.py        ← Prompt file loading + template variable injection ({{ACTION_CATALOG}}, {{VALID_HYPOTHESIS_IDS}})
│   │   └── orchestrator.py         ← User-interactive GroupChat orchestrator
│   │   │
│   │   └── middleware/
│   │       ├── tool_capture_middleware.py      ← MCP tool call recording
│   │       ├── eval_middleware.py              ← Output evaluation API integration
│   │       ├── prompt_injection_middleware.py  ← Prompt injection detection
│   │       └── llm_logging_middleware.py       ← LLM call diagnostics
│   │
│   ├── helper/
│   │   ├── agent_logger.py         ← Telemetry (App Insights + UI event queue)
│   │   ├── errors.py               ← Error taxonomy (PipelineError hierarchy + classify_exception)
│   │   ├── auth.py                 ← Azure auth (DefaultAzureCredential, MCP bearer)
│   │   └── llm.py                  ← LLM client factory (Azure OpenAI)
│   │
│   ├── config/                     ← All configuration (see Configuration Hierarchy)
│   ├── prompts/                    ← Agent instruction prompts (.txt files)
│   ├── knowledge/                  ← Shared knowledge docs appended to prompts
│   ├── a2a/                        ← Google A2A protocol support (discovery)
│   └── UI/                         ← Web UI components (cards, etc.)
```

---

## Appendix: Investigation State Machine

The `Investigation` dataclass (`investigation_state.py`) is the central
mutable state object that flows through the entire pipeline:

```
Investigation
├── id: str                        ← Unique investigation ID
├── phase: InvestigationPhase      ← Current lifecycle phase
├── context: InvestigationContext   ← Customer, service, region, severity
├── symptoms: List[Symptom]        ← Confirmed symptoms (from triage)
├── hypotheses: List[Hypothesis]   ← Ranked hypotheses (from scorer)
│   └── Hypothesis
│       ├── status: HypothesisStatus  ← ACTIVE / CONFIRMED / REFUTED / CONTRIBUTING
│       ├── match_score: float        ← 0–5 normalized score from hypothesis scorer
│       ├── confidence: float         ← Updated by reasoner
│       ├── matched_symptoms: List    ← Symptoms supporting this hypothesis
│       ├── evidence_needed: List     ← ER-IDs required for verification
│       ├── evidence_collected: List  ← ER-IDs already collected
│       ├── evidence_delta: List      ← ER-IDs still needed
│       ├── verdicts: Dict            ← Evidence verdicts per ER-ID
│       └── symptom_verdicts: Dict    ← Per-symptom verdict from reasoner
├── evidence_plan: List[ER]        ← Evidence requirements
├── evidence: List[EvidenceItem]   ← Collected evidence items
├── actions: List[Dict]            ← Selected remediation actions
├── evidence_cycles: int           ← How many collect→reason cycles
├── phase_history: List[Record]    ← Auditable phase transition log
└── signal_builder_result          ← Link to triggering signals
```

### Validated Phase Transitions (State Machine)

All phase mutations go through `investigation.transition_to(target, source=..., force=...)` which:
1. Validates the transition against `_LEGAL_TRANSITIONS` (or config overrides)
2. Records a `PhaseTransitionRecord` with from/to/timestamp/source/forced
3. Logs the transition for observability
4. Raises `InvalidPhaseTransition` if the transition is illegal (unless `force=True` for emergency → COMPLETE)

**Legal Transitions Map:**

```
INITIALIZING → {TRIAGE}
TRIAGE       → {HYPOTHESIZING}
HYPOTHESIZING→ {PLANNING}
PLANNING     → {COLLECTING}
COLLECTING   → {REASONING}
REASONING    → {PLANNING, ACTING, NOTIFYING, COMPLETE}  ← PLANNING = evidence cycle backtrack
ACTING       → {NOTIFYING, COMPLETE}
NOTIFYING    → {COMPLETE}
COMPLETE     → {}  (terminal)
```

**Emergency overrides** (`force=True`): The speaker selector forces ANY → COMPLETE on oscillation
detection and identical-message stalls. The runner uses `force=True` for final completion to handle
any intermediate phase gracefully.

### Config-Driven Phase Pipeline

The `phase_pipeline` field in `agents_config.json → investigation_workflow` defines the execution
order and mode for each phase:

```json
"phase_pipeline": {
  "phases": [
    {"name": "triage",      "agent": "triage_agent", "mode": "standalone", "retryable": true},
    {"name": "hypothesizing","mode": "programmatic"},
    {"name": "evidence_loop","agents": ["evidence_planner","reasoner"], "mode": "groupchat", "max_cycles": 2},
    {"name": "acting",       "agent": "action_planner", "mode": "standalone", "retryable": true},
    {"name": "notifying",    "mode": "auto_complete"}
  ]
}
```

| Mode | Description |
|------|-------------|
| `standalone` | Single agent invoked outside GroupChat (retryable) |
| `groupchat` | Agents collaborate inside GroupChat with speaker selector |
| `programmatic` | Deterministic logic, no LLM (e.g. hypothesis scoring) |
| `auto_complete` | Sentinel phase that resolves the investigation |

Config models: `PhaseConfig` and `PhasePipelineConfig` in
`core/models/config/phase_pipeline.py`.

### Status Transitions

```
                    ┌──────────┐
                    │  ACTIVE  │ ← Initial state after scoring
                    └────┬─────┘
                         │
              ┌──────────┼──────────┐
              │          │          │
     ┌────────▼───┐ ┌───▼──────┐ ┌▼───────────┐
     │ CONFIRMED  │ │ REFUTED  │ │CONTRIBUTING│
     │            │ │          │ │            │
     │ Evidence   │ │ Evidence │ │ Partial    │
     │ strongly   │ │ refutes  │ │ evidence   │
     │ supports   │ │ this     │ │ supports   │
     └────────────┘ └──────────┘ └────────────┘
```

---

## GroupChat Quality Improvements

The Investigation GroupChat exhibited several quality problems during initial
runs: agents producing garbled or half-formed output, missing JSON blocks,
violating role boundaries (e.g. reasoner trying to collect evidence), and
context inflation causing progressive degradation over long investigations.

A root-cause analysis identified five contributing factors:

| ID | Root Cause | Description |
|----|-----------|-------------|
| RC-1 | Unbounded context growth | Orchestrator instructions accumulate injections without cleanup; no conversation trimming |
| RC-2 | High temperature on structured-output agents | All investigation agents except triage ran at temperature 1.0 |
| RC-3 | No pre-emission format enforcement | All output validation is post-hoc (detect-and-retry) |
| RC-4 | Collector free-text output | Collectors produced unstructured prose, making evidence_planner consolidation unreliable |
| RC-5 | Prompt boundary leakage | No negative examples showing agents what NOT to produce |

Fixes were implemented in four phases.

### Phase 1 — Config Tuning & Instruction Accumulation Fix (RC-1, RC-2)

**Files changed:**
- `src/config/agents/agents_config.json`
- `src/core/services/investigation/investigation_speaker_selector.py`
- `src/core/services/investigation/investigation_runner.py`

#### 1a. Temperature reduction and token caps

Lowered LLM temperature for all structured-output investigation agents to
reduce randomness in JSON generation. Added `max_completion_tokens` caps
to prevent runaway generation.

| Agent | Temperature (before → after) | Token cap |
|-------|-----------------------------|-----------|
| `investigation_orchestrator` | 1.0 → 0.3 | — |
| `evidence_planner` | 1.0 → 0.5 | 8000 (new) |
| `reasoner` | 1.0 → 0.3 | 8000 (new) |
| `action_planner` | 1.0 → 0.3 | — |

Evidence planner uses 0.5 (not 0.3) because it needs flexibility in
composing collector task strings for diverse hypothesis types.

#### 1b. Instruction accumulation fix

Four injection sites were appending text to agent instructions without
removing previous injections, causing unbounded growth:

| Injection site | File | Fix |
|---------------|------|-----|
| Hypothesis summary (`═══ STAGE 2 COMPLETE ═══`) | `investigation_runner.py` | `re.sub()` strips previous block before append |
| Hypothesis queue update (`═══ HYPOTHESIS QUEUE UPDATE`) | `investigation_speaker_selector.py` → `_inject_hypothesis_queue_update()` | `re.sub()` strips previous block before append |
| Oscillation warning (`WARNING: Routing loop detected`) | `investigation_speaker_selector.py` → selection closure | `re.sub()` strips previous warning before append |
| Identical-message warning (`WARNING: Agent '...` ) | `investigation_speaker_selector.py` → selection closure | `re.sub()` strips previous warning before append |

The pattern follows the existing model used by `_inject_evidence_context()`
and `_inject_evidence_exhausted()`, which already had `re.sub()` cleanup.
Each injection now uses a regex anchored to its delimiter markers to remove
the previous instance before appending the new one.

### Phase 2 — Structured Collector Output & Negative Examples (RC-4, RC-5)

**Files changed:**
- `src/prompts/investigation_sli_collector_prompt.txt`
- `src/prompts/investigation_incident_collector_prompt.txt`
- `src/prompts/investigation_support_collector_prompt.txt`
- `src/prompts/investigation_evidence_planner_prompt.txt`
- `src/prompts/investigation_reasoner_prompt.txt`

#### 2a. Structured JSON schema for collector agents (RC-4)

All three collector prompts were updated from free-text "OUTPUT FORMAT"
sections to require a ```json block with a typed schema. Each collector
uses a shared envelope structure:

```json
{
  "er_results": [
    {
      "er_id": "ER-XXX-NNN",
      "status": "collected | from_signal | unavailable",
      "source": "tool_name or signal_data",
      "findings": { "...domain-specific fields..." },
      "summary": "One-line finding"
    }
  ],
  "preliminary_relevance": "supports | inconclusive | weakens",
  "relevance_reasoning": "One sentence"
}
```

Domain-specific `findings` fields per collector:

| Collector | Key findings fields |
|-----------|--------------------|
| `sli_collector` | `severity`, `impacted_resources`, `duration_minutes`, `regions_affected`, `subscriptions_affected`, `pattern`, `load_assessment` |
| `incident_collector` | `incident_count` (active/recent/historical), `incidents[]` with `severity`, `status`, `root_cause.failure_domain`, `blast_radius` |
| `support_collector` | `ticket_counts` (critsit/high/moderate/low), `scope`, `dominant_category`, `ticket_pattern`, `critsit_alerts[]` |

This ensures the evidence_planner receives machine-parseable, consistent
input from all collectors regardless of LLM variation.

#### 2b. Few-shot negative examples (RC-5)

Added "NEGATIVE EXAMPLES — DO NOT PRODUCE OUTPUT LIKE THIS" sections to
the evidence_planner and reasoner prompts. Each example shows a concrete
bad output and explains WHY it violates the agent's role:

**Evidence planner** (4 negative examples):
1. Acknowledgment-only turn with no tool calls or JSON
2. Re-evaluating the reasoner's verdict (role boundary violation)
3. Setting wrong signals (`phase_complete="reasoning"` instead of `"collecting"`)
4. Response without the required ```json block

**Reasoner** (5 negative examples):
1. Dispatching collector tools (reasoner has NO tools)
2. Requesting more evidence when EVIDENCE COLLECTION EXHAUSTED notice is present
3. Using invented/placeholder hypothesis IDs (`HYP-12345`)
4. Setting wrong signals (`phase_complete="collecting"` instead of `"reasoning"`)
5. Response without the required ```json block

Negative examples are more effective than positive-only instructions because
they define the boundary explicitly — the LLM sees both what to do and what
specifically to avoid, reducing cross-agent role confusion.

### Phase 3 — OutputFormatMiddleware Pre-Emission Validation (RC-3)

**Files changed:**
- `src/core/middleware/output_format_middleware.py` (new)
- `src/core/agent_factory.py`

#### 3a. OutputFormatMiddleware

New `AgentMiddleware` that validates agent output format **before** it
reaches the GroupChat message stream. Applies to agents with required
JSON fields: `reasoner`, `evidence_planner`, `action_planner`,
`triage_agent`.

**Validation checks** (mirrors `_detect_garbled_output()` in the output
parser):
1. Response too short (< 20 chars)
2. Missing ```json block when agent requires structured output
3. JSON parsed but required fields empty
4. Text degeneration (garble/placeholder patterns)

**Execution paths:**

| Mode | Behavior |
|------|----------|
| Non-streaming | Validates inline → on failure injects correction message → retries LLM once → accepts result |
| Streaming (GroupChat) | Registers a `stream_result_hook` → fires after stream consumption → flags invalid output in `context.metadata` for logging |

The middleware cannot retry in streaming mode because the `ResponseStream`
is consumed exactly once by the GroupChat iteration loop. For streaming,
the existing speaker-selector garbled-retry mechanism (Phase 1) serves as
the retry layer.

**Retry budget:**

The middleware and speaker selector operate on mutually exclusive paths:
- Streaming (GroupChat): middleware flags only → speaker selector retries (max 1)
- Non-streaming (standalone): middleware retries once → no speaker selector

Total retries per agent turn: **max 1** in both paths. No double-counting.

#### 3b. Message constructor compatibility

The Agent Framework SDK `Message` class does **not** accept `text=` as a
constructor keyword. `msg.text` is a **read-only property** that
concatenates `.contents`. The middleware's correction-message injection
must use `contents=[...]`:

```python
# ✅ Correct — SDK Message constructor
context.messages.append(Message(
    role="user",
    contents=[_CORRECTION_TEMPLATE.format(reason=reason)],
))

# ❌ Wrong — TypeError at runtime
Message(role="user", text="...")
```

#### 3c. Middleware registration

Position in the default middleware stack:
```
prompt_injection → output_format → tool_capture → eval → llm_logging
```

Runs after prompt injection (input is safe) but before eval/logging (only
valid output gets evaluated and recorded). Registered via the existing
config-driven middleware registry in `agent_factory.py`:

```python
_DEFAULT_MIDDLEWARE_ORDER = [
    "prompt_injection",
    "output_format",   # ← new
    "tool_capture",
    "eval",
    "llm_logging",
]
```

No changes to `agents_config.json` required — agents using the default
middleware order inherit `output_format` automatically.

### Phase 4 — Context Folding (CompactionStrategy) (RC-1)

**Files changed:**
- `src/core/services/investigation/investigation_folding_strategy.py` (new)
- `src/helper/agent_logger.py`
- `src/core/models/config/agents.py`
- `src/core/services/investigation/investigation_runner.py`
- `CustomerAgentUI/components/cards.js`
- `CustomerAgentUI/views/agentflow.js`
- `CustomerAgentUI/styles.css`

While Phase 1 fixed instruction accumulation, the GroupChat's
`_full_conversation` and per-agent `AgentExecutor._cache` still grow
unboundedly across rounds. Over long investigations (20+ rounds), the
conversation exceeds the model's effective context window, causing output
quality degradation, garbled responses, and incomplete JSON.

Phase 4 addresses this by implementing a **CompactionStrategy** that folds
old conversation turns into structured state-summary messages, keeping the
working context within a manageable size.

#### 4a. InvestigationFoldingStrategy

A new `CompactionStrategy` implementation
(`investigation_folding_strategy.py`) that the Agent Framework SDK calls
automatically when an agent's message history grows.

**Protocol:** `async def __call__(self, messages: list[Message]) -> bool`
— mutates the message list in place, returns `True` if compaction occurred.

**Folding triggers** (any one is sufficient):
1. **First invocation** — fold once to baseline the context
2. **Phase boundary** — fold when the investigation phase changes
3. **Threshold exceeded** — fold when message count exceeds
   `CONTEXT_FOLDING_MIN_MESSAGES` (default 10)

**Preservation rules** (messages never folded):
- System messages (role = `system`)
- The first user message (original task/context)
- The last N messages (tail, default 4 via `CONTEXT_FOLDING_PRESERVE_TAIL`)
- Existing folding summary messages (prevents re-folding summaries)

**Summary content:** The folded messages are replaced by a single
`role="user"` message containing a structured JSON state summary built
from the live `Investigation` object:

```json
{
  "investigation_id": "INV-...",
  "current_phase": "collecting",
  "symptoms": [ { "id": "SYM-001", "name": "...", "severity": "..." } ],
  "hypotheses": [ { "id": "HYP-001", "status": "...", "confidence": 0.7, "statement": "..." } ],
  "evidence_collected": [ { "er_id": "ER-SLI-001", "status": "collected", "summary": "..." } ],
  "key_findings": [ "..." ],
  "fold_number": 1
}
```

The summary message is prefixed with `[CONTEXT FOLDING — Investigation
state summary...]` so downstream components can identify it.

#### 4b. Feature flag and configuration

| Environment variable | Default | Description |
|---------------------|---------|-------------|
| `ENABLE_CONTEXT_FOLDING` | `false` | Master on/off switch |
| `CONTEXT_FOLDING_MIN_MESSAGES` | `10` | Minimum messages before threshold folding triggers |
| `CONTEXT_FOLDING_PRESERVE_TAIL` | `4` | Number of recent messages always preserved |

Per-agent override via `agents_config.json`:
```json
{
  "name": "evidence_planner",
  "context_folding": true
}
```

Set `"context_folding": false` on any agent to exclude it from folding
(e.g. the orchestrator, which has minimal history). The field defaults
to `true` in the `AgentConfig` Pydantic model.

#### 4c. Closure capture wiring pattern

The strategy is **not** created in `agent_factory.py`. Instead, it is
attached in `investigation_runner.py` using a closure capture pattern,
because the strategy needs a reference to the live `Investigation` object
which only exists at investigation runtime:

```python
if FOLDING_ENABLED:
    for agent in [evidence_planner, reasoner]:
        strategy = InvestigationFoldingStrategy(
            investigation=investigation,  # live object — closure captures it
            agent_name=agent.name,
        )
        agent.compaction_strategy = strategy
```

This leverages the SDK's mutable `Agent.compaction_strategy` attribute
(set after construction). Only `evidence_planner` and `reasoner` get
folding — these are the agents with the longest conversation histories
in typical investigations.

#### 4d. Evidence deduplication in state summary

The `_build_state_summary()` helper deduplicates evidence by **`er_id`**
(not by synthetic `id`). When the same evidence requirement is satisfied by
both a signal-sourced pre-populated item and a later collector-produced
item, only the richer entry survives in the summary.

- **Reverse iteration** — the list is traversed in reverse so later (richer)
  entries win over earlier stubs
- **Key selection** — `er_id` is preferred; falls back to `id` when `er_id`
  is absent
- **Order restoration** — after dedup the list is reversed again to restore
  chronological order

This mirrors the deduplication logic in `apply_to_investigation()` (see
[Phase 5c — Evidence Deduplication](#5c-evidence-deduplication-by-er_id))
and ensures the folded context accurately reflects the investigation's
current evidence set without duplicates inflating token count.

#### 4e. Application Insights telemetry

A new `log_context_folding()` method on `AgentLogger` emits a
`ContextFolding` event through the standard `_emit()` pipeline
(App Insights + Python logger + SSE UI subscribers):

| Field | Description |
|-------|-------------|
| `Agent` | Agent name (e.g. `evidence_planner`) |
| `InvestigationId` | Investigation ID |
| `Phase` | Investigation phase when fold occurred |
| `MessagesFolded` | Number of messages removed |
| `OriginalTokens` | Estimated token count before fold |
| `FoldedTokens` | Estimated token count after fold |
| `TokenReduction` | Tokens saved |
| `FoldNumber` | Cumulative fold count for this agent |
| `SummaryContent` | Full JSON state summary (not redacted) |

`SummaryContent` is always logged in full — it is operational/diagnostic
data, not agent output subject to per-agent redaction rules.

#### 4f. UI rendering

The `ContextFolding` event has a dedicated card renderer in the debug UI:
- **Event card** (`cards.js`): Shows agent, phase, fold number, token
  reduction stats with percentage, and a collapsible `<details>` block
  containing the full summary JSON (no truncation)
- **Agent flow tooltip** (`agentflow.js`): Extended to 5000 chars for
  `ContextFolding` events (vs 300 for other event types)
- **CSS** (`styles.css`): `.folding-summary` class removes the default
  `max-height: 200px` cap on `<pre>` blocks and enables `pre-wrap` for
  full content display

### Phase 5 — Post-Implementation Fixes

After deploying Phases 1–4, several follow-up issues were discovered during
live investigations. These fixes address edge cases in evidence handling,
prompt robustness, and UI state propagation.

#### 5a. Evidence planner prompt hardening (empty evidence_items)

**File changed:** `src/prompts/investigation_evidence_planner_prompt.txt`

**Problem:** The evidence planner occasionally produced turns with an empty
`evidence_items: []` array — typically when a collector returned "no data
found" or when the planner acknowledged a previous turn without dispatching
collectors. The downstream output parser treated this as a valid (but empty)
evidence collection, advancing the investigation without actually collecting
anything.

**Fix:** Added explicit rules and negative examples to the evidence planner
prompt:

1. **Critical execution rule #5:** "evidence_items MUST NEVER be empty —
   every turn must produce at least one evidence item"

2. **"CRITICAL: evidence_items MUST NEVER BE EMPTY" section** with:
   - Outcome-to-action table mapping collector results to required actions
   - Four hard rules (always ≥1 item, use `unavailable` status, never empty
     array, re-dispatch on ambiguous results)
   - **BAD EXAMPLE 5** — empty evidence_items with excuse ("Collector found
     no data" → wrong because it should use `unavailable` status)
   - **BAD EXAMPLE 6** — acknowledgment-only turn with no evidence items
   - **GOOD EXAMPLE** — correct handling with `unavailable` status items

| Collector Outcome | Required Action |
|-------------------|----------------|
| Returns data | Create evidence_item with `collected` status |
| Returns "no results" | Create evidence_item with `unavailable` status + reason |
| Returns error | Create evidence_item with `unavailable` status + error detail |
| Not dispatched yet | Dispatch collector first, then process results |

#### 5b. Garbled event investigation state propagation

**File changed:** `src/core/services/investigation/investigation_runner.py`

**Problem:** When an agent's output was detected as garbled, the
`investigation_agent_response` event was emitted with metadata fields
(`phase`, `is_garbled`, `garbled_reason`) but **omitted** the full
investigation state arrays: `hypotheses`, `evidence`, `symptoms`,
`actions`. The UI's `_updateFromInvestigation()` handler checks
`Array.isArray(event.hypotheses)` before replacing the panel — with
`undefined`, the check fails and the UI retains **stale** hypothesis
status and confidence values from the previous successful event.

**Symptom:** After a reasoner garbled-retry cycle, hypothesis status
showed "EVALUATING" with confidence 0.55 (from the first evaluation
cycle) instead of the correct "CONFIRMED" with confidence 0.85 that
had been applied before the garbled turn.

**Fix:** The garbled event now includes the same serialized investigation
state as non-garbled events:

```python
if parsed.is_garbled:
    events.append({
        "type": "investigation_agent_response",
        "agent": agent_name,
        "is_garbled": True,
        "garbled_reason": parsed.garbled_reason,
        # ↓ NEW: full state so UI panels stay current ↓
        "investigation_id": investigation.id,
        "symptoms_count": len(investigation.symptoms),
        "hypotheses_count": len(investigation.hypotheses),
        "evidence_count": len(investigation.evidence),
        "evidence_cycle_count": evidence_cycle_count,
        "symptoms": _serialize_symptoms(investigation),
        "hypotheses": _serialize_hypotheses(investigation),
        "evidence": _serialize_evidence(investigation),
        "actions": _serialize_actions(investigation),
        ...
    })
```

Since `apply_to_investigation()` is correctly skipped for garbled output,
the serialized state reflects the **last valid** investigation state — which
is exactly what the UI should display.

#### 5c. Evidence deduplication by er_id

**Files changed:**
- `src/core/services/investigation/investigation_output_parser.py`
- `src/core/services/investigation/investigation_folding_strategy.py`

**Problem:** Evidence items were deduplicated by their synthetic `id` field
(e.g. `ev-sig-sig_type_1`), but the same evidence requirement could appear
with different `id` values across different sources:

| Source | id | er_id |
|--------|----|-------|
| Signal pre-population | `ev-sig-sig_type_1` | `ER-SLI-001` |
| SLI collector | `ER-SLI-001` | `ER-SLI-001` |

Because the `id` values differ, the old dedup logic treated these as two
separate evidence items. The investigation accumulated duplicate entries,
inflating context tokens and confusing the reasoner.

**Fix:** Both `apply_to_investigation()` (output parser) and
`_build_state_summary()` (folding strategy) now deduplicate by **`er_id`**:

```python
# investigation_output_parser.py — apply_to_investigation()
existing_er_id_idx: dict[str, int] = {}
for idx, e in enumerate(investigation.evidence):
    if e.er_id:
        existing_er_id_idx[e.er_id] = idx
```

**Stub detection:** Signal-sourced evidence items (agent_name in
`"reused"`, `"signal_sourced"`, `"signal_builder"`) are treated as stubs.
When a real collector produces evidence for the same `er_id`, the stub is
**replaced** in-place. Stubs never overwrite richer collector evidence.

| Scenario | Old Behaviour | New Behaviour |
|----------|--------------|---------------|
| Signal pre-pop + collector for same ER | Both kept (duplicate) | Collector replaces signal stub |
| Two collector entries for same ER | Both kept (duplicate) | First collector entry kept |
| Stub re-submitted for existing real entry | Both kept (duplicate) | Stub silently dropped |

#### 5d. UI cache busting and truncation fixes

**Files changed:**
- `CustomerAgentUI/components/cards.js`
- `CustomerAgentUI/views/agentflow.js`
- `CustomerAgentUI/views/stream.js`

**Problem 1 — Browser caching:** ES module imports in the debug UI were
cached aggressively by browsers. After deploying code changes, users saw
stale UI behaviour until they manually cleared their cache.

**Fix:** Added `?v=2` cache-busting query parameters to all ES module
imports:

```javascript
import { renderCard } from './components/cards.js?v=2';
```

**Problem 2 — Agent output truncation:** Three independent truncation
points were clipping agent output too aggressively, preventing users from
seeing full investigation context:

| Truncation Point | Location | Old Limit | New Limit |
|------------------|----------|-----------|-----------|
| Backend `_redact()` | `investigation_runner.py` | 300 chars | Preserved (operational) |
| Card renderer | `cards.js` handler | 300 chars | Full content (expandable) |
| Agent flow tooltip | `agentflow.js` | 300 chars | 5000 chars for ContextFolding events |

The `cards.js` fallback handler now renders full agent output with an
expandable `<details>` wrapper instead of hard-truncating at 300 characters.

---

*This document describes the design as implemented. For contribution guidelines,
see `docs/contributing-to-customer-agent.md`.*
