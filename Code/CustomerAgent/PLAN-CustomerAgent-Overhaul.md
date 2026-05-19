# CustomerAgent Pipeline Overhaul — Execution Plan

**Owner:** Rasmi  
**Created:** 2026-05-06  
**Scope:** 4 tasks spanning signals, symptoms, hypotheses, SLI category mapping, and scoring  
**Repos:** RATIO-AI (`Code/RATIO_MCP` + `Code/CustomerAgent`)

---

## Table of Contents

- [Architecture Overview](#architecture-overview)
- [End-to-End Data Flow](#end-to-end-data-flow)
- [File Inventory](#file-inventory)
- [Phase 1: Signal Cleaning & Kusto Query Correction](#phase-1-signal-cleaning--kusto-query-correction)
- [Phase 2: Rewire Symptoms to Signals](#phase-2-rewire-symptoms-to-signals)
- [Phase 3: Rewire Hypotheses to Symptoms](#phase-3-rewire-hypotheses-to-symptoms)
- [Phase 4: Enrich SLI Category Mapping](#phase-4-enrich-sli-category-mapping)
- [Phase 5: Hypothesis Scoring Improvement](#phase-5-hypothesis-scoring-improvement)
- [Cross-Phase Dependencies](#cross-phase-dependencies)
- [Known Bugs & Issues](#known-bugs--issues)
- [Master Checklist](#master-checklist)

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                         RATIO_MCP Server                                │
│  src/queries/*.kql          → Kusto queries (raw data fetching)         │
│  src/config/tools_config.json → MCP tool registration (params, clusters)│
│  src/registry/tools.py      → Dynamic tool handler factory              │
└──────────────────────────┬───────────────────────────────────────────────┘
                           │ MCP tool calls
                           ▼
┌──────────────────────────────────────────────────────────────────────────┐
│                      CustomerAgent Pipeline                             │
│                                                                         │
│  ┌─────────────────┐   ┌──────────────────┐   ┌──────────────────────┐ │
│  │ Signal Template  │   │ Symptom Templates │   │ Hypothesis Templates │ │
│  │ signal_template  │──▶│ symptoms/*.json   │──▶│ hypotheses/*.json   │ │
│  │ .json            │   │                  │   │                      │ │
│  └────────┬─────────┘   └────────┬─────────┘   └──────────┬───────────┘ │
│           │                      │                         │             │
│  ┌────────▼─────────┐   ┌────────▼─────────┐   ┌──────────▼───────────┐ │
│  │ Signal Builder   │   │ Symptom Matcher  │   │ Hypothesis Scorer    │ │
│  │ + Data Fetcher   │   │                  │   │ + Category Boost     │ │
│  │ + Aggregation    │   │                  │   │                      │ │
│  └──────────────────┘   └──────────────────┘   └──────────────────────┘ │
│                                                         │               │
│                                               ┌─────────▼────────────┐  │
│                                               │ Dependency Services  │  │
│                                               │ dependency_services/ │  │
│                                               │ *.json               │  │
│                                               └──────────────────────┘  │
└──────────────────────────────────────────────────────────────────────────┘
```

---

## End-to-End Data Flow

```
Step 1: DATA FETCHING
  KQL Query (.kql)
    ↓ executed by
  MCP Tool (tools_config.json → tools.py handler)
    ↓ called by
  data_fetcher.py → fetch_and_persist()
    ↓ writes raw JSON
  {output_dir}/{signal_type_id}.json

Step 2: AGGREGATION
  aggregation_script_builder.py → build_aggregation_script()
    ↓ generates Python script
  Sandbox executes script
    ↓ groups rows, computes aggregates
  {output_dir}/{signal_type_id}_aggregated.json

Step 3: SIGNAL ACTIVATION
  signal_builder.py → _check_activation()
    ↓ evaluates activation_rules per granularity
  ActivatedSignal instances (strength, confidence)
    ↓ compound signal evaluation
  SignalBuilderResult (type_results + compound_results)

Step 4: TRIAGE (LLM)
  investigation_runner.py → on_group_chat() Stage 1
    ↓ triage agent confirms symptoms from signals
  symptom_matcher.py → filter_templates_by_signal_types()
    ↓ delta filtering + suppress_when
  Confirmed symptoms list

Step 5: HYPOTHESIS SCORING (Programmatic — no LLM)
  hypothesis_scorer.py → score_hypotheses()
    ↓ load templates, compute match_score, apply category_boost
  Ranked Hypothesis list

Step 6: INVESTIGATION (Multi-agent LLM GroupChat)
  investigation_runner.py → on_group_chat() Stage 3
    ↓ planner → collector → reasoner loop
  Evidence collection + hypothesis verdict

Step 7: ACTION PLANNING (LLM)
  investigation_runner.py → on_group_chat() Stage 4
    ↓ deduplicated action plan
  Final Investigation output
```

---

## File Inventory

### RATIO_MCP Server (`Code/RATIO_MCP/`)

| File | Type | Purpose |
|------|------|---------|
| `src/queries/impacted_resource_customer.kql` | KQL | SLI breach data filtered by customer (SIG-TYPE-1) |
| `src/queries/impacted_resource_multicustomer.kql` | KQL | SLI breach data across all customers (SIG-TYPE-1 + SIG-TYPE-4) |
| `src/queries/support_request.kql` | KQL | Support cases for a customer (SIG-TYPE-2) |
| `src/queries/support_request_multicustomer.kql` | KQL | Aggregated multi-customer support cases (SIG-TYPE-2) |
| `src/queries/Incident_details.kql` | KQL | IcM incidents (SIG-TYPE-3) |
| `src/queries/customer_region.kql` | KQL | Customer region discovery (SIG-TYPE-4 dependency_scan) |
| `src/queries/impacted_resource.kql` | KQL | Generic impacted resource query (not used by signals) |
| `src/queries/incident_root_cause.kql` | KQL | Root cause data (not used by signals) |
| `src/config/tools_config.json` | JSON | MCP tool registration — maps tool names to queries, clusters, params |
| `src/registry/tools.py` | Python | Dynamic tool handler factory — reads tools_config.json, builds handlers |

### CustomerAgent — Config (`Code/CustomerAgent/src/config/`)

| File | Type | Contents |
|------|------|----------|
| **Signals** | | |
| `signals/signal_template.json` | JSON | 4 signal types (SIG-TYPE-1 to 4), 6 compound signals, scoring/decision rules |
| **Symptoms** | | |
| `symptoms/sli_breach.json` | JSON | 7 symptoms: SYM-SLI-001 to SYM-SLI-007 |
| `symptoms/support_tickets.json` | JSON | 6 symptoms: SYM-SUP-001 to SYM-SUP-006 |
| `symptoms/outage_exposure.json` | JSON | 5 symptoms: SYM-OUT-001 to SYM-OUT-005 |
| `symptoms/dependency_degradation.json` | JSON | 4 symptoms: SYM-DEP-001 to SYM-DEP-004 |
| **Hypotheses** | | |
| `hypotheses/sli_hypotheses.json` | JSON | 3 hypotheses: HYP-SLI-001 to HYP-SLI-003 |
| `hypotheses/outage_hypotheses.json` | JSON | 4 hypotheses: HYP-OUT-001 to HYP-OUT-004 |
| `hypotheses/support_hypotheses.json` | JSON | 4 hypotheses: HYP-SUP-001 to HYP-SUP-004 |
| `hypotheses/dependency_hypotheses.json` | JSON | 3 hypotheses: HYP-DEP-001 to HYP-DEP-003 |
| `hypotheses/risk_hypotheses.json` | JSON | **Empty** (`[]`) |
| `hypotheses/scoring_config.json` | JSON | Scoring parameters (weights, thresholds, boosts) |
| **Dependency Services** | | |
| `dependency_services/azure_allocator.json` | JSON | Azure Allocator SLI→symptom mapping |
| `dependency_services/azure_capacity_infrastructure_service.json` | JSON | Capacity Infra SLI mapping |
| `dependency_services/dependency_mappings.json` | JSON | Primary service → dependency service map |
| `dependency_services/regional_network_manager.json` | JSON | Regional Network Manager SLI mapping |
| `dependency_services/sql_availability.json` | JSON | SQL Availability SLI mapping |
| `dependency_services/sql_connectivity.json` | JSON | SQL Connectivity SLI mapping |
| `dependency_services/sql_control_plane.json` | JSON | SQL Control Plane SLI mapping |
| `dependency_services/xstore.json` | JSON | XStore SLI mapping |
| **Other** | | |
| `monitoring_context.json` | JSON | Monitoring targets, service trees |
| `fetch_tools_config.json` | JSON | Fetch tool configuration |

### CustomerAgent — Code (`Code/CustomerAgent/src/core/`)

| File | Key Functions/Classes | Touched in Phase |
|------|----------------------|------------------|
| **Signal pipeline** | | |
| `services/signals/data_fetcher.py` | `fetch_and_persist()`, `_fetch_standard_type()`, `_fetch_dependency_type()` | 1, 4 |
| `services/signals/signal_builder.py` | `_check_activation()`, `_compute_groups()`, `_compute_aggregate()`, rule evaluators | 1 |
| `services/signals/aggregation_script_builder.py` | `build_aggregation_script()` | 1 |
| `services/signals/symptom_matcher.py` | `load_symptom_templates()`, `filter_templates_by_signal_types()`, `format_templates_for_prompt()` | 2 |
| `services/signals/signal_models.py` | `ActivatedSignal`, `TypeSignalResult`, `CompoundSignalResult`, `SignalBuilderResult` | 1 |
| `services/signals/sources/kusto_signal_source.py` | `KustoSignalSource.fetch_signals()` | 1 |
| **Investigation pipeline** | | |
| `services/investigation/hypothesis_scorer.py` | `score_hypotheses()`, `_compute_match_score()`, `_compute_category_boost()`, `load_hypothesis_templates()` | 3, 4, 5 |
| `services/investigation/investigation_runner.py` | `on_group_chat()` — orchestrates triage → scoring → groupchat → action | 5 |
| `services/investigation/investigation_state.py` | `Hypothesis` dataclass | 5 |
| `services/investigation/investigation_output_parser.py` | `extract_json_block()`, `apply_to_investigation()` | — |
| **Models — Config schemas** | | |
| `models/config/signal_template.py` | `SignalTypeConfig`, `GranularityConfig`, `CompoundSignalConfig` | 1 |
| `models/config/symptom_template.py` | `SymptomTemplateConfig`, `SymptomFileConfig` | 2 |
| `models/config/hypothesis_template.py` | `HypothesisTemplateConfig`, `HypothesisFileConfig` | 3 |
| `models/config/dependency_service.py` | `SliSymptomConfig`, `DependencyServiceFileConfig` | 4 |
| **Models — Domain** | | |
| `models/investigation/hypothesis.py` | `HypothesisModel` (Pydantic) | 5 |
| `models/investigation/symptoms.py` | `SymptomModel` (Pydantic) | 2 |

---

## Phase 1: Signal Cleaning & Kusto Query Correction

**Goal:** Fix all 6 KQL queries, verify MCP tool registrations, clean signal_template.json.

### Step 1.1 — KQL Query Corrections

#### 1.1.1 `impacted_resource_customer.kql`

- **Path:** `Code/RATIO_MCP/src/queries/impacted_resource_customer.kql`
- **Cluster:** `SLI_KUSTO_CLUSTER` (bdsair.centralus → bdssli.westcentralus)
- **Params:** `serviceTreeId:string`, `customerName:string`, `startTime:datetime`, `endTime:datetime`
- **Feeds:** SIG-TYPE-1 granularities: `region_slicategory`, `cross_region`, `multi_sli_region`
- **Output columns:** `CustomerName`, `SubscriptionId`, `Region`, `SLO_SliId`, `SliCategory`, `ImpactedResources`, `TotalImpactDurationMin`, `EarliestImpactStart`, `LatestImpactEnd`, `AvgValueAcrossWindows`, `MinValueAcrossWindows`
- **Current logic:**
  - Reads SLI table mapping from `bdsair.Airogreen.External_SliIdToSliTableMapping`
  - Filters `Category !contains "Latency"` (intentional?)
  - Joins customer subscriptions from `icmanalytics.Analytics.Dim_C360CustomerSubscriptions`
  - Dynamic `evaluate execute_query` to union SLI tables
  - Island detection for impact windows using `prev()` + `row_cumsum()`
  - Final `summarize` by CustomerName, SubscriptionId, Region, SLO_SliId, SliCategory
- **Verify/fix:**
  - [ ] `Category !contains "Latency"` — is this still desired?
  - [ ] 1h time clamp — appropriate?
  - [ ] `prev()` island detection — correct for overlapping windows?
  - [ ] Output columns match `data_fields` in signal_template.json SIG-TYPE-1
  - [ ] Cross-cluster references still valid (bdsair, bdssli, icmanalytics)

#### 1.1.2 `impacted_resource_multicustomer.kql`

- **Path:** `Code/RATIO_MCP/src/queries/impacted_resource_multicustomer.kql`
- **Cluster:** `SLI_KUSTO_CLUSTER`
- **Params:** `serviceTreeId:string`, `startTime:datetime`, `endTime:datetime` (no customerName)
- **Feeds:** SIG-TYPE-1 `cross_customer_region` + SIG-TYPE-4 dependency_scan (reused)
- **Output columns:** `Region`, `SLO_SliId`, `SliCategory`, `ImpactedResources`, `ImpactedSubscriptions`, `TotalImpactDurationMin`, `EarliestImpactStart`, `LatestImpactEnd`, `AvgValueAcrossWindows`, `MinValueAcrossWindows`
- **Differences from customer version:**
  - No customer filter (no subscription join)
  - Output groups by Region, SLO_SliId, SliCategory (no CustomerName)
  - Adds `ImpactedSubscriptions = dcount(SubscriptionId)`
- **Verify/fix:**
  - [ ] Same Latency filter and time clamp checks as 1.1.1
  - [ ] Output columns match what SIG-TYPE-4 `dependency_tool` expects
  - [ ] When reused by SIG-TYPE-4, `data_fetcher._fetch_dependency_type()` enriches rows with `DependencyServiceName`, `DependencyCategory` — make sure base columns are compatible

#### 1.1.3 `support_request.kql`

- **Path:** `Code/RATIO_MCP/src/queries/support_request.kql`
- **Cluster:** `SUPPORT_KUSTO_CLUSTER` (supportrptwus3prod)
- **Params:** `customerName:string=""`, `startTime:datetime`, `endTime:datetime`, `supportProductNames:string="[]"`
- **Feeds:** SIG-TYPE-2 granularities: `single_case`, `crit_sit`, `escalated`, `multi_case_same_product`
- **Output columns:** `CaseNumber`, `Title`, `State`, `IsCritSit`, `SupportProductName`, `Region`, `CreatedDateTime`, `IsEscalated`, `InitialSeverity`, `Severity`, `AzureSubscriptionId`, `Customer_CloudCustomerName`
- **Current logic:**
  - Queries `Product360M365.AllCloudsSupportIncidentWithReferenceModelVNext_AllSources`
  - Left joins `AceHubSupportData.MSaaSSupportCases` for `Location` (Region)
  - 6h time window expansion when window < 6h
  - Optional customer/product filters
- **Verify/fix:**
  - [ ] `CaseNumber` is actually `IncidentId` (aliased) — verify downstream consumers expect this
  - [ ] `Region = Location` from MSaaSSupportCases join — nullable if no match
  - [ ] Time expansion logic correct
  - [ ] Output columns match SIG-TYPE-2 `data_fields`

#### 1.1.4 `support_request_multicustomer.kql`

- **Path:** `Code/RATIO_MCP/src/queries/support_request_multicustomer.kql`
- **Cluster:** `SUPPORT_KUSTO_CLUSTER`
- **Params:** `startTime:datetime`, `endTime:datetime`, `supportProductNames:string="[]"`
- **Feeds:** SIG-TYPE-2 granularities: `multi_customer_same_product`, `multi_customer_crit_sit`
- **Output columns:** `SupportProductName`, `TotalCaseCount`, `DistinctCustomerCount`, `DistinctCritSitCustomerCount`, `MaxSeverity`, `CritSitCount`, `CustomerList`, `CritSitCustomerList`, `RegionList`, `CritSitRegionList`, `CaseNumbers`, `CritSitCaseNumbers`, `EarliestCase`, `LatestCase`
- **Current logic:**
  - Pre-aggregated per `SupportProductName` — `WHERE DistinctCustomerCount >= 2`
  - Same base table + MSaaSSupportCases join as single-customer query
- **Verify/fix:**
  - [ ] Pre-aggregated fields match what signal_template `aggregates` section expects (uses `pre_aggregated:FieldName` pattern)
  - [ ] `MaxSeverity = min(Severity)` — using min() because Sev A < Sev C numerically
  - [ ] `DistinctCustomerCount >= 2` filter — should this be configurable?

#### 1.1.5 `Incident_details.kql`

- **Path:** `Code/RATIO_MCP/src/queries/Incident_details.kql`
- **Cluster:** `ICM_KUSTO_CLUSTER` (icmanalytics.centralus)
- **Params:** `startTime:datetime`, `endTime:datetime`, `owningTenantNames:string="[]"`
- **Feeds:** SIG-TYPE-3 all 5 granularities
- **Output columns:** `IncidentId`, `Severity`, `IsOutage`, `Title`, `Status`, `CreateDate`, `ImpactStartDate`, `ChildCount`, `OwningTenantName`, `SupportTicketId`
- **Current logic:**
  - Queries `IcmDataWarehouse.IncidentsSnapshotV2()`
  - Filters `Severity <= 2 or IsOutage == true`
  - 6h time expansion when window < 6h
  - Optional tenant name filter
- **Verify/fix:**
  - [ ] `Severity <= 2` — only Sev 0, 1, 2? Should Sev 3 with outage be included? (currently yes via OR)
  - [ ] `ImpactStartDate` filter vs `CreateDate` — which is the right anchor?
  - [ ] `ChildCount` — is this actually populated in IncidentsSnapshotV2?
  - [ ] Output columns match SIG-TYPE-3 `data_fields`

#### 1.1.6 `customer_region.kql`

- **Path:** `Code/RATIO_MCP/src/queries/customer_region.kql`
- **Cluster:** `SLI_KUSTO_CLUSTER`
- **Params:** `serviceTreeId:string`, `customerName:string`, `startTime:datetime`, `endTime:datetime`
- **Feeds:** SIG-TYPE-4 region discovery for `dependency_scan` strategy
- **Output columns:** `CustomerName`, `Region`
- **Current logic:**
  - Queries ALL monitored resources (no `Value < Target` filter)
  - Joins customer subscriptions
  - Returns distinct CustomerName, Region pairs
- **Verify/fix:**
  - [ ] Should this query ALL SLI tables or just production ones?
  - [ ] 1h time clamp — appropriate for region discovery?
  - [ ] Missing `SliCategory` in output (intentional — region discovery only)

### Step 1.2 — MCP Tool Registration Verification

**File:** `Code/RATIO_MCP/src/config/tools_config.json`

For each tool, verify this mapping chain is consistent:

```
tools_config.json                    .kql file
  parameters.{name}                  declare query_parameters ({name}:{type})
    ↕ linked via                       ↕
  kusto_params.{KustoParam}          used as {KustoParam} in query body
    .source = {name}
```

| Tool Name | Query File | Parameters | Kusto Params |
|-----------|-----------|------------|-------------|
| `collect_impacted_resource_customer_tool` | `impacted_resource_customer.kql` | service_tree_id, customer_name, start_time, end_time | serviceTreeId, customerName, startTime, endTime |
| `collect_impacted_resource_multicustomer_tool` | `impacted_resource_multicustomer.kql` | service_tree_id, start_time, end_time | serviceTreeId, startTime, endTime |
| `collect_support_request_tool` | `support_request.kql` | customer_name, start_time, end_time, support_product_names | customerName, startTime, endTime, supportProductNames |
| `collect_support_request_multicustomer_tool` | `support_request_multicustomer.kql` | start_time, end_time, support_product_names | startTime, endTime, supportProductNames |
| `collect_incident_details_tool` | `Incident_details.kql` | start_time, end_time, owning_tenant_names | startTime, endTime, owningTenantNames |
| `collect_customer_region_tool` | `customer_region.kql` | service_tree_id, customer_name, start_time, end_time | serviceTreeId, customerName, startTime, endTime |

**Per tool checklist:**
- [ ] `parameters` → Python function signature types match
- [ ] `kusto_params` → `source` field matches parameter name exactly
- [ ] `kusto_params` → key names match `declare query_parameters` in KQL
- [ ] `description` → accurately describes what the query returns
- [ ] `validation.time_range` → `max_days` and `max_age_days` values are appropriate
- [ ] `cluster_env` / `database_env` → correct env var names
- [ ] `cert_client_id_env` → correct auth client ID

### Step 1.3 — Signal Template Cleanup

**File:** `Code/CustomerAgent/src/config/signals/signal_template.json`

#### Signal Type Verification Matrix

| Signal Type | collection_tools[].tool_name | Granularities | Compounds |
|-------------|------------------------------|---------------|-----------|
| SIG-TYPE-1 | `collect_impacted_resource_customer_tool` (→ region_slicategory, cross_region, multi_sli_region), `collect_impacted_resource_multicustomer_tool` (→ cross_customer_region) | 4 granularities | COMPOUND-001,002,003,005 |
| SIG-TYPE-2 | `collect_support_request_tool` (→ single_case, crit_sit, escalated, multi_case_same_product), `collect_support_request_multicustomer_tool` (→ multi_customer_same_product, multi_customer_crit_sit) | 6 granularities | COMPOUND-001,002,004,006 |
| SIG-TYPE-3 | `collect_incident_details_tool` (→ all 5) | 5 granularities | COMPOUND-001,003,004 |
| SIG-TYPE-4 | `collect_customer_region_tool` (region_tool) + `collect_impacted_resource_multicustomer_tool` (dependency_tool) | 3 granularities | COMPOUND-005,006 |

**Per granularity checklist:**
- [ ] `group_by` fields exist in raw data or computed aggregates
- [ ] `aggregates` expressions use supported functions (count_distinct, sum, avg, min, max, mean, collect, collect_distinct, count_where, pre_aggregated)
- [ ] `activation_rules` field names match `{aggregate_name}_{suffix}` pattern (e.g., `impacted_resources_min`, `is_crit_sit`, `case_number_present`)
- [ ] `strength_formula` references only fields available in the group dict
- [ ] `max_raw_strength` is reasonable
- [ ] `confidence` level is appropriate

**Compound signal checklist:**
- [ ] `required_signal_types` reference valid SIG-TYPE-* IDs
- [ ] `activation_rules.min_types_activated` is correct
- [ ] `strength_formula` and `correlation_multiplier` are reasonable

---

## Phase 2: Rewire Symptoms to Signals

**Goal:** Ensure every symptom template correctly maps to signal granularities and field names from Phase 1.

### Step 2.1 — SLI Breach Symptoms

**File:** `Code/CustomerAgent/src/config/symptoms/sli_breach.json`

| ID | Name | Signal | Granularity | Weight | suppress_when |
|----|------|--------|-------------|--------|---------------|
| SYM-SLI-001 | SLI Category Degraded in Region | SIG-TYPE-1 | region_slicategory | 1 | cross_region (matching: customer_name, sli_category) |
| SYM-SLI-002 | SLI Category Degraded Across Regions | SIG-TYPE-1 | cross_region | 2 | — |
| SYM-SLI-003 | Severe SLI Category Degradation Across Regions | SIG-TYPE-1 | cross_region | 3 | filters: max_avg_value=25.0 |
| SYM-SLI-004 | Multiple SLI Categories Breached in Region | SIG-TYPE-1 | multi_sli_region | 2 | — |
| SYM-SLI-005 | Broad Regional SLI Failure | SIG-TYPE-1 | multi_sli_region | 3 | filters: min_distinct_sli_count=4 |
| SYM-SLI-006 | Cross-Customer SLI Degradation in Region | SIG-TYPE-1 | cross_customer_region | 2 | — |
| SYM-SLI-007 | Severe Cross-Customer SLI Degradation | SIG-TYPE-1 | cross_customer_region | ? | (need to read full file) |

**Per symptom checklist:**
- [ ] `signal_sources` → valid SIG-TYPE-* ID
- [ ] `granularity` → exists in signal_template.json for that signal type
- [ ] `fields.from_data` → field names match aggregated output for that granularity
- [ ] `template` → placeholder names match `fields.from_data` keys
- [ ] `suppress_when.granularity_activated` → valid granularity name
- [ ] `suppress_when.matching_fields` → fields exist in both this symptom and the suppressing granularity
- [ ] `weight` → appropriate relative to other symptoms

### Step 2.2 — Support Ticket Symptoms

**File:** `Code/CustomerAgent/src/config/symptoms/support_tickets.json`

| ID | Name | Signal | Granularity | Weight | suppress_when |
|----|------|--------|-------------|--------|---------------|
| SYM-SUP-001 | Customer Filed Support Case | SIG-TYPE-2 | single_case | 2 | multi_case_same_product (matching: customer_name, support_product_name) |
| SYM-SUP-002 | Customer Raised CritSit | SIG-TYPE-2 | crit_sit | 3 | — |
| SYM-SUP-003 | Customer Escalated Support Case | SIG-TYPE-2 | escalated | 2 | — |
| SYM-SUP-004 | Multiple Cases Same Product | SIG-TYPE-2 | multi_case_same_product | 2 | — |
| SYM-SUP-005 | Multiple Customers Filed Support Requests | SIG-TYPE-2 | multi_customer_same_product | 3 | — |
| SYM-SUP-006 | Multiple Customer CritSits | SIG-TYPE-2 | multi_customer_crit_sit | 4 | — |

### Step 2.3 — Outage/Incident Symptoms

**File:** `Code/CustomerAgent/src/config/symptoms/outage_exposure.json`

| ID | Name | Signal | Granularity | Weight | suppress_when |
|----|------|--------|-------------|--------|---------------|
| SYM-OUT-001 | Incident Detected | SIG-TYPE-3 | one_or_more_incident | 1 | multi_incident (matching: owning_tenant_name) |
| SYM-OUT-002 | Confirmed Outage | SIG-TYPE-3 | outage_confirmed | 3 | — |
| SYM-OUT-003 | Widespread Confirmed Outage with Child Incidents | SIG-TYPE-3 | with_child_incidents | 3 | — |
| SYM-OUT-004 | Customer-Correlated Confirmed Outage | SIG-TYPE-3 | customer_correlated | 3 | — |
| SYM-OUT-005 | Multiple Confirmed Outages Detected | SIG-TYPE-3 | multi_incident | 3 | — |

### Step 2.4 — Dependency Symptoms

**File:** `Code/CustomerAgent/src/config/symptoms/dependency_degradation.json`

| ID | Name | Signal | Granularity | Weight | suppress_when |
|----|------|--------|-------------|--------|---------------|
| SYM-DEP-001 | Dependency SLI Breach in Customer Region | SIG-TYPE-4 | dep_region_sli | 2 | **dep_cross_region_sli** ⚠️ |
| SYM-DEP-002 | Dependency SLI Breached Across Multiple Regions | SIG-TYPE-4 | **dep_cross_region_sli** ⚠️ | 3 | dep_multi_sli_region |
| SYM-DEP-003 | Multiple SLIs Breached for Dependency Service | SIG-TYPE-4 | dep_multi_sli_region | 3 | — |
| SYM-DEP-004 | Multiple Dependency Services Degraded in Region | SIG-TYPE-4 | dep_multi_service_region | 3 | — |

**⚠️ BUG:** SYM-DEP-001 `suppress_when` references `dep_cross_region_sli` and SYM-DEP-002 `granularity` is `dep_cross_region_sli` — but **this granularity does not exist** in signal_template.json SIG-TYPE-4. The actual granularities are: `dep_region_sli`, `dep_multi_sli_region`, `dep_multi_service_region`. **Fix required:** either add `dep_cross_region_sli` granularity to signal_template.json, or remap SYM-DEP-002 to an existing granularity and update SYM-DEP-001's suppress_when.

### Step 2.5 — Code Changes (if needed)

| File | Function | When to touch |
|------|----------|---------------|
| `services/signals/symptom_matcher.py` | `load_symptom_templates()` | If new symptom JSON file added |
| `services/signals/symptom_matcher.py` | `filter_templates_by_signal_types()` | If new suppress_when pattern needed |
| `models/config/symptom_template.py` | `SymptomTemplateConfig` | If new fields added to symptom JSON schema |

---

## Phase 3: Rewire Hypotheses to Symptoms

**Goal:** Update hypothesis templates to reference correct symptoms from Phase 2.

### Step 3.1 — SLI Hypotheses

**File:** `Code/CustomerAgent/src/config/hypotheses/sli_hypotheses.json`

| ID | Name | required_symptoms | excluding_symptoms | min_match |
|----|------|-------------------|-------------------|-----------|
| HYP-SLI-001 | SLI Spike with Full Corroboration | SYM-SLI-001, SYM-SUP-005, SYM-OUT-001 | SYM-SUP-001 | 3 |
| HYP-SLI-002 | SLI Spike with Partial Corroboration | SYM-SLI-001 | SYM-SUP-001 | 2 |
| HYP-SLI-003 | SLI Spike with No Corroboration | SYM-SLI-001 | SYM-SUP-001, SYM-SUP-005, SYM-OUT-001 | 1 |

**Logic:** Tiered from full corroboration (SLI+SR+incident) → partial (SLI + one) → none (SLI only). HYP-SLI-003 excludes customer SR (SYM-SUP-001) + multi-customer SR (SYM-SUP-005) + incidents (SYM-OUT-001) to ensure it only fires for isolated SLI anomalies.

### Step 3.2 — Outage Hypotheses

**File:** `Code/CustomerAgent/src/config/hypotheses/outage_hypotheses.json`

| ID | Name | required_symptoms | excluding_symptoms | min_match |
|----|------|-------------------|-------------------|-----------|
| HYP-OUT-001 | Incident Confirmed Impacting Customer | SYM-OUT-001, SYM-SLI-001, SYM-SUP-005 | SYM-SUP-001 | 3 |
| HYP-OUT-002 | Incident Possibly Affecting Customer | SYM-OUT-001 | SYM-SUP-001 | 2 |
| HYP-OUT-003 | Incident — No Customer Impact Evidence | SYM-OUT-001 | SYM-SUP-001, SYM-SLI-001, SYM-SUP-005 | 1 |
| HYP-OUT-004 | Incident Not Impacting Customer | SYM-OUT-001 | (none listed) | 1 |

### Step 3.3 — Support Hypotheses

**File:** `Code/CustomerAgent/src/config/hypotheses/support_hypotheses.json`

| ID | Name | required_symptoms | excluding_symptoms | min_match |
|----|------|-------------------|-------------------|-----------|
| HYP-SUP-001 | Customer SR with Full Corroboration | SYM-SUP-001 | — | 1 |
| HYP-SUP-002 | Customer SR Corroborated by SLI | SYM-SUP-001, SYM-SLI-001 | — | 2 |
| HYP-SUP-003 | Customer SR with Platform Evidence | SYM-SUP-001 | SYM-SLI-001 | 2 |
| HYP-SUP-004 | Customer SR with No Corroboration | SYM-SUP-001 | (multiple) | 1 |

### Step 3.4 — Dependency Hypotheses

**File:** `Code/CustomerAgent/src/config/hypotheses/dependency_hypotheses.json`

| ID | Name | required_symptoms | excluding_symptoms | min_match |
|----|------|-------------------|-------------------|-----------|
| HYP-DEP-001 | Dependency Cascading to Customer | SYM-DEP-001, SYM-SLI-001, SYM-SUP-005 | SYM-SUP-001 | 3 |
| HYP-DEP-002 | Dependency with Partial Cascade | SYM-DEP-001 | SYM-SUP-001 | 2 |
| HYP-DEP-003 | Dependency — No Cascade Evidence | SYM-DEP-001 | SYM-SLI-001, SYM-SUP-001, SYM-SUP-005 | 1 |

### Step 3.5 — Risk Hypotheses

**File:** `Code/CustomerAgent/src/config/hypotheses/risk_hypotheses.json`

Currently **empty** (`[]`). Decision needed: populate or leave empty.

### Step 3.6 — Hypothesis-to-Symptom Validation

For every hypothesis, confirm:
- [ ] Every ID in `expected_symptoms` exists in a symptom JSON file from Phase 2
- [ ] Every ID in `required_symptoms` also appears in `expected_symptoms`
- [ ] Every ID in `excluding_symptoms` exists in a symptom JSON file
- [ ] `evidence_needed` IDs reference valid entries in `config/evidence/evidence_requirements.json`
- [ ] `relevant_sli_categories` or `relevant_categories` values are valid (used by `_compute_category_boost()`)

### Step 3.7 — Code Changes (if needed)

| File | Function | When to touch |
|------|----------|---------------|
| `models/config/hypothesis_template.py` | `HypothesisTemplateConfig` | If new fields added to hypothesis JSON |
| `services/investigation/hypothesis_scorer.py` | `load_hypothesis_templates()` | If new hypothesis file added |

---

## Phase 4: Enrich SLI Category Mapping

**Goal:** Add detail to dependency service configs; update category boost logic; optionally move to Kusto.

### Step 4.1 — Enrich Dependency Service JSON Files

**Directory:** `Code/CustomerAgent/src/config/dependency_services/`

| File | Service | What to add |
|------|---------|-------------|
| `azure_allocator.json` | Azure Allocator | `sli_symptoms[].sli_category`, richer `impact_description` |
| `azure_capacity_infrastructure_service.json` | Capacity Infra | Same |
| `regional_network_manager.json` | Regional Network Manager | Same |
| `sql_availability.json` | SQL Availability | Same |
| `sql_connectivity.json` | SQL Connectivity | Same |
| `sql_control_plane.json` | SQL Control Plane | Same |
| `xstore.json` | XStore | Same |

**Schema for each file:**
```json
{
  "name": "Service Name",
  "service_tree_id": "uuid",
  "category": "Storage|Compute|Network|Database|...",
  "impact_description": "How failure impacts primary service",
  "sli_symptoms": [
    {
      "sliId": "SLI-XXX",
      "sli_category": "Availability|Latency|Capacity|...",
      "symptoms": ["SYM-DEP-001", "SYM-DEP-002"]
    }
  ]
}
```

**Per file checklist:**
- [ ] `sli_symptoms[].symptoms[]` reference valid SYM-DEP-* IDs from Phase 2
- [ ] `sli_symptoms[].sli_category` values align with what `_compute_category_boost()` checks
- [ ] `category` value aligns with hypothesis `relevant_categories` values
- [ ] `impact_description` is meaningful for investigation narration

### Step 4.2 — Update Dependency Mappings

**File:** `Code/CustomerAgent/src/config/dependency_services/dependency_mappings.json`

This maps primary service → list of dependency services. Verify all dependency services referenced here have corresponding JSON files in Step 4.1.

### Step 4.3 — Schema Update (if needed)

**File:** `Code/CustomerAgent/src/core/models/config/dependency_service.py`

Current schema:
```python
class SliSymptomConfig(BaseModel):
    sliId: str
    sli_category: str
    symptoms: list[str]

class DependencyServiceFileConfig(BaseModel):
    name: str
    service_tree_id: str
    category: str
    impact_description: str
    sli_symptoms: list[SliSymptomConfig]
```

If adding new fields (e.g., severity tiers, SLI thresholds), update these Pydantic models.

### Step 4.4 — Update Category Boost Logic (if categories changed)

**File:** `Code/CustomerAgent/src/core/services/investigation/hypothesis_scorer.py`
**Function:** `_compute_category_boost()` (lines ~162-241)

Current logic:
1. Extract `dependency_category` and `sli_category` from matched symptom entities
2. Compare against hypothesis' `relevant_categories` (dependency) and `relevant_sli_categories` (SLI)
3. Three outcomes: match (1.5x), unknown (0.8x), mismatch (0.5x)

If you add new category dimensions or change category naming, this function needs updating.

### Step 4.5 — (Optional) Move to Kusto

If moving dependency service mappings from JSON files to a Kusto table:

| # | Action | File |
|---|--------|------|
| 1 | Design `SliCategoryMapping` table schema in ADX | ADX cluster (ratioadxwus3prod?) |
| 2 | Write ingestion/seeding script | New script |
| 3 | Write `.kql` query | `Code/RATIO_MCP/src/queries/sli_category_mapping.kql` (new) |
| 4 | Register MCP tool | `Code/RATIO_MCP/src/config/tools_config.json` (add entry) |
| 5 | Update data fetcher | `Code/CustomerAgent/src/core/services/signals/data_fetcher.py` → `_fetch_dependency_type()` — replace JSON file reads with MCP tool call |
| 6 | Keep JSON files as fallback | In case Kusto is unavailable |

---

## Phase 5: Hypothesis Scoring Improvement

**Goal:** Improve the scoring algorithm for better hypothesis ranking.

### Step 5.1 — Tune Scoring Config

**File:** `Code/CustomerAgent/src/config/hypotheses/scoring_config.json`

Current values:
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

**Parameters to consider tuning:**
- `strength_aggregation` — `avg` vs `max` vs `min` (how to combine signal strengths)
- `default_weight` — fallback weight for symptoms without explicit weight
- `min_score_threshold` — discard hypotheses below this score
- `category_boost_factor` — multiplier when SLI/dependency category matches
- `category_mismatch_penalty` — multiplier when category explicitly mismatches
- `category_unknown_modifier` — multiplier when no category data available
- `max_score` — cap on final hypothesis score

### Step 5.2 — Improve Match Score Formula

**File:** `Code/CustomerAgent/src/core/services/investigation/hypothesis_scorer.py`
**Function:** `_compute_match_score()` (lines ~92-159)

Current formula:
```
weighted_matched = sum(weight for matched expected symptoms)
weighted_total = sum(weight for all expected symptoms)
overlap_ratio = weighted_matched / weighted_total

agg_signal_strength = weight-proportional aggregation of matched symptom strengths
  (using strength_aggregation: avg|max|min)

match_score = overlap_ratio × agg_signal_strength
```

**Potential improvements:**
- Non-linear scaling (e.g., log, sigmoid)
- Recency weighting (symptoms from more recent signals score higher)
- Diminishing returns for many low-weight symptoms
- Minimum strength threshold per symptom

### Step 5.3 — Improve Category Boost Logic

**Same file,** `_compute_category_boost()` (lines ~162-241)

Current: flat multipliers (1.5x/0.8x/0.5x). Potential improvements:
- Multi-level matching (exact category → parent category → unknown)
- Weighted average across multiple matched symptoms' categories
- Configurable per-hypothesis boost factors

### Step 5.4 — Improve Filtering Logic

**Same file,** `score_hypotheses()` (lines ~254-342)

Current filters:
1. `min_symptoms_for_match` — hard threshold
2. `required_symptoms` — ALL must be present (AND)
3. `excluding_symptoms` — ANY present → disqualify (OR)
4. `min_score_threshold` — score cutoff
5. Cap at `max_score`

**Potential improvements:**
- Weighted required_symptoms (some more required than others)
- Partial excluding (reduce score instead of disqualify)
- Dynamic min_symptoms_for_match based on available signal types
- Confidence band output (not just point score)

### Step 5.5 — Add New Scoring Fields to Models (if needed)

| File | Class | Potential new fields |
|------|-------|---------------------|
| `services/investigation/investigation_state.py` | `Hypothesis` dataclass | `raw_score_before_boost`, `category_boost_applied`, `confidence_band`, `matched_symptom_details` |
| `models/investigation/hypothesis.py` | `HypothesisModel` (Pydantic) | Mirror same fields |

### Step 5.6 — Update Investigation Runner (if scoring output changes)

**File:** `Code/CustomerAgent/src/core/services/investigation/investigation_runner.py`
**Function:** `on_group_chat()` — Stage 2 passes hypothesis list to Stage 3 GroupChat

If hypothesis model changes, verify GroupChat agents can consume the new fields.

---

## Cross-Phase Dependencies

```
Phase 1 (Signal cleaning)
  └─► Phase 2 (Symptom rewiring)
        │   If KQL column names changed → update fields.from_data
        │   If granularities renamed → update symptom granularity refs
        └─► Phase 3 (Hypothesis rewiring)
              │   If symptom IDs changed → update expected/required/excluding
              └─► Phase 5 (Scoring improvement)
                    │   Uses symptom weights from Phase 2
                    │   Uses hypothesis structure from Phase 3

Phase 4 (SLI category mapping)
  └─► Phase 5 (Scoring improvement)
        Uses category data in _compute_category_boost()
```

**Safe parallelization:**
- Phase 1 and Phase 4 are independent (can work simultaneously)
- Phase 2 depends on Phase 1
- Phase 3 depends on Phase 2
- Phase 5 depends on Phase 3 + Phase 4

---

## Known Bugs & Issues

| # | Issue | Location | Severity | Fix Phase |
|---|-------|----------|----------|-----------|
| 1 | **SYM-DEP-002 references non-existent granularity `dep_cross_region_sli`** — this granularity is not defined in signal_template.json SIG-TYPE-4. The symptom can never fire. SYM-DEP-001's `suppress_when` also references this phantom granularity. | `symptoms/dependency_degradation.json` + `signals/signal_template.json` | **High** — broken dependency symptom chain | Phase 1 or 2 |
| 2 | `risk_hypotheses.json` is empty — no risk hypotheses exist | `hypotheses/risk_hypotheses.json` | Low — may be intentional | Phase 3 |
| 3 | `impacted_resource_customer.kql` filters `Category !contains "Latency"` — Latency SLIs excluded from all SIG-TYPE-1 signals | `RATIO_MCP/src/queries/impacted_resource_customer.kql` | Medium — intentional? | Phase 1 |
| 4 | `support_request.kql` aliases `IncidentId` as `CaseNumber` — potential confusion downstream | `RATIO_MCP/src/queries/support_request.kql` | Low | Phase 1 |

---

## Master Checklist

### Phase 1: Signal Cleaning
- [ ] 1.1.1 Review/fix `impacted_resource_customer.kql`
- [ ] 1.1.2 Review/fix `impacted_resource_multicustomer.kql`
- [ ] 1.1.3 Review/fix `support_request.kql`
- [ ] 1.1.4 Review/fix `support_request_multicustomer.kql`
- [ ] 1.1.5 Review/fix `Incident_details.kql`
- [ ] 1.1.6 Review/fix `customer_region.kql`
- [ ] 1.2 Verify/update `tools_config.json` (6 tools)
- [ ] 1.3 Clean `signal_template.json` (4 types, 18 granularities, 6 compounds)

### Phase 2: Rewire Symptoms
- [ ] 2.1 Verify/update `sli_breach.json` (7 symptoms)
- [ ] 2.2 Verify/update `support_tickets.json` (6 symptoms)
- [ ] 2.3 Verify/update `outage_exposure.json` (5 symptoms)
- [ ] 2.4 Verify/update `dependency_degradation.json` (4 symptoms)
- [ ] 2.4 **FIX BUG:** SYM-DEP-002 `dep_cross_region_sli` granularity does not exist
- [ ] 2.5 Update code (`symptom_matcher.py`, `symptom_template.py`) if schema changed

### Phase 3: Rewire Hypotheses
- [ ] 3.1 Verify/update `sli_hypotheses.json` (3 hypotheses)
- [ ] 3.2 Verify/update `outage_hypotheses.json` (4 hypotheses)
- [ ] 3.3 Verify/update `support_hypotheses.json` (4 hypotheses)
- [ ] 3.4 Verify/update `dependency_hypotheses.json` (3 hypotheses)
- [ ] 3.5 Decide on `risk_hypotheses.json`
- [ ] 3.6 Validate all symptom/evidence IDs exist
- [ ] 3.7 Update code (`hypothesis_template.py`, `hypothesis_scorer.py`) if schema changed

### Phase 4: SLI Category Mapping
- [ ] 4.1 Enrich 7 dependency service JSON files
- [ ] 4.2 Update `dependency_mappings.json`
- [ ] 4.3 Update `dependency_service.py` schema if needed
- [ ] 4.4 Update `_compute_category_boost()` if categories changed
- [ ] 4.5 (Optional) Design and implement Kusto-based mapping

### Phase 5: Scoring Improvement
- [ ] 5.1 Tune `scoring_config.json` parameters
- [ ] 5.2 Improve `_compute_match_score()` formula
- [ ] 5.3 Improve `_compute_category_boost()` logic
- [ ] 5.4 Improve `score_hypotheses()` filtering
- [ ] 5.5 Add new fields to `Hypothesis` dataclass + `HypothesisModel` if needed
- [ ] 5.6 Verify `investigation_runner.py` consumes updated hypothesis format
