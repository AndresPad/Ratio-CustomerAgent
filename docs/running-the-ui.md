# Running the Customer Agent Health-Analysis UI End-to-End

This guide walks through getting the **ratio_ui_web** React app wired up to
the Customer Agent pipeline (signal builder → triage → hypothesis →
evidence → action) plus the new **Investigation Theatre** animated view.

---

## 1. Architecture — what talks to what

```
  Browser (http://127.0.0.1:3010)
        │
        │  /customer-agent-api/*         (Vite proxy)
        ▼
  Vite dev server (port 3010)
        │
        │  strips "/customer-agent-api"
        ▼
  CustomerAgent FastAPI  (port 8503)
        │   ├─ /health
        │   ├─ /chat  ·  /chat/stream           (GroupChat LLM agents)
        │   ├─ /api/run                         (full signal → investigation SSE)
        │   ├─ /api/scenarios  ·  /api/agents   (UI catalog)
        │   ├─ /api/config/*  ·  /api/datafiles
        │   ├─ /api/knowledge/*                 (docs)
        │   └─ /api/investigate                 (UI-shaped SSE translator)
        │
        │   MCP HTTP/SSE + auth
        ▼
  RATIO_MCP server        (port 8000)
        │
        ▼
  Azure Kusto / IcM / Azure OpenAI
```

Ports come from [`Code/scripts/start_all.ps1`](../Code/scripts/start_all.ps1)
and are the single source of truth.

| Service | Port | URL |
|---|---|---|
| `ratio-mcp`       | 8000 | http://127.0.0.1:8000 |
| `customer-agent`  | 8503 | http://127.0.0.1:8503 |
| `ratio-ui-web`    | 3010 | http://127.0.0.1:3010 |

> The Vite proxy `/customer-agent-api` → **8503** is configured in
> [`vite.config.ts`](../Code/CustomerAgent/ratio_ui_web/vite.config.ts) —
> previously it pointed at 8020 which does not match `start_all.ps1`.

---

## 2. One-time setup

### Prerequisites
- Python 3.11+
- Node 18+
- Azure CLI (for `DefaultAzureCredential` + `az login`)

### Clone and create the Python venv

```powershell
cd c:\Git\Primo\Ratio-CustomerAgent\Ratio-CustomerAgent
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r Code\CustomerAgent\requirements.txt
pip install -r Code\RATIO_MCP\requirements.txt
```

### Configure `.env`

Copy `.env.example` → `.env` at the repo root and populate:

```env
AZURE_OPENAI_ENDPOINT=https://<your-endpoint>.openai.azure.com/
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_TENANT_ID=<tenant-guid>
KUSTO_ICM_CLUSTER_URI=https://<cluster>.kusto.windows.net
KUSTO_ICM_DATABASE=Common
APP_ENV=development
# Optional — additional origins allowed by CORS
CUSTOMER_AGENT_CORS_ORIGINS=http://127.0.0.1:3010,http://localhost:3010
```

Then sign in so `DefaultAzureCredential` can pick up a token:

```powershell
az login --tenant <your-tenant>
```

### Install frontend packages

```powershell
cd Code\CustomerAgent\ratio_ui_web
npm install
cd ..\..\..
```

---

## 3. Start everything

```powershell
.\Code\scripts\start_all.ps1
```

The script launches three background jobs and prints a health summary:

```
ratio-mcp       http://127.0.0.1:8000
customer-agent  http://127.0.0.1:8503
ratio-ui-web   http://127.0.0.1:3010
```

Press `Ctrl+C` to stop all three.

### Smoke-test the backend

```powershell
Invoke-RestMethod http://127.0.0.1:8503/health
Invoke-RestMethod http://127.0.0.1:8503/api/scenarios
```

You should see a `scenarios` array with at least one `SC-LIVE-*` plus three
demo scenarios (`SC-DEMO-SLI`, `SC-DEMO-COMPOUND`, `SC-DEMO-DEPENDENCY`).

---

## 4. Open the UI

Navigate to [`http://127.0.0.1:3010/customer-agent`](http://127.0.0.1:3010/customer-agent).

Left-hand nav:

| Route | Page |
|---|---|
| `/customer-agent`               | Home |
| `/customer-agent/scenarios`     | Simulation Scenarios |
| `/customer-agent/active`        | Active Investigation (existing stream/graph/flow views) |
| `/customer-agent/theatre`       | **Investigation Theatre (new animated view)** |
| `/customer-agent/history`       | History |
| `/customer-agent/agents`        | Agent Registry |
| `/customer-agent/config`        | Configuration |
| `/customer-agent/data`          | Data Files |
| `/customer-agent/knowledge`     | Knowledge Base |

---

## 5. The new Investigation Theatre

Open the **Investigation Theatre** tab and press **Run Pipeline**. The page
subscribes directly to `POST /customer-agent-api/api/run` (SSE) and reduces
the raw AgentLogger events into an animated dashboard with these elements:

| Region | What it shows |
|---|---|
| **Executive ticker** (top) | Rolling list of the last three "big" milestones — signal evaluated, compound activated, hypothesis selected, verdict, tool invoked. Old entries fade. |
| **Stage rail** | Eleven circles that animate as the pipeline moves Signal Start → Signal Evaluation → Compound → Decision → Triage → Hypothesis Scoring → Hypothesis Selection → Evidence → Reasoning → Action → Summary. Active node pulses; completed nodes show a checkmark. |
| **Signals column** | Every SIG-TYPE-* and compound signal with strength bar (0–5) and confidence label. Activation badge shows when a signal fires. |
| **Hypotheses column** | Every scored hypothesis with a match bar (score / 5) **and** a confidence bar. Status badge flips from `SCORED` → `EVALUATING` → `CONFIRMED` / `CONTRIBUTING` / `REFUTED` as verdicts arrive. |
| **Evidence column** | Overall evidence-collection progress bar (`N / M gathered`), then a rolling list of tool calls with row counts and durations. |
| **Tools · Actions column** | Summary of every MCP/agent tool invoked with counts + final action cards once the action planner fires. |

Animations are intentionally lightweight (CSS-only `chaPulse`, `chaShimmer`,
`chaSlideIn`) so there are no extra dependencies.

### Events mapped

The reducer in
[`ChaTheatrePage.tsx`](../Code/CustomerAgent/ratio_ui_web/src/pages/customer-agent/ChaTheatrePage.tsx)
understands the full AgentLogger + investigation-runner vocabulary:

```
pipeline_started · SignalEvaluationStart · MCPCollectionCall
SignalTypeEvaluated · CompoundEvaluated · SignalDecision
signal_evaluation_complete · HypothesisScoring · HypothesisSelected
HypothesisTransition · PhaseTransition · ToolCall · SpeakerSelected
investigation_agent_response · InvestigationComplete · pipeline_complete
investigation_stall_warning · investigation_error · pipeline_error
```

If the pipeline emits new event names later, add a case to the `reduce`
function — no other wiring required.

---

## 6. How the pieces actually connect

### Backend (new in this change)

- **`Code/CustomerAgent/src/server/ui_api.py`** — read-only catalog
  endpoints (`/api/scenarios`, `/api/agents`, `/api/config/*`,
  `/api/datafiles`, `/api/knowledge`) and the `/api/investigate` SSE
  translator that normalizes raw pipeline frames into the
  `InvestigationEvent` shape the existing `ChaActivePage` expects.
- **`Code/CustomerAgent/src/server/app.py`** — registers
  `ui_api.register_ui_routes(app, run_pipeline)` and adds CORS so the
  React app can hit `:8503` directly if you ever serve it from a
  non-proxy origin (e.g. docker nginx on `:3000`). Controlled by
  `CUSTOMER_AGENT_CORS_ORIGINS` (comma-separated, no `*`).

### Frontend (new in this change)

- **`ChaTheatrePage.tsx`** — the animated demo view; connects directly to
  `/api/run` SSE so it receives the richest possible event stream.
- **`vite.config.ts`** — proxy fixed to point at 8503 (matches
  `start_all.ps1`).
- **`App.tsx` + `ChaLayout.tsx`** — new `/customer-agent/theatre` route
  and sidebar entry.

### Unchanged / already existed

- `ChaActivePage.tsx` — three-view investigation dashboard (Stream /
  Relationship Graph / Agent Flow). Keeps using the typed
  `customerAgentClient` that now has a real backend.
- `run_signal_builder.py` + `run_signal_builder_loop.py` — CLI entry
  points still work; the UI is an alternative surface onto the same
  pipeline.

---

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `/api/scenarios 404` | Stale backend still on old app.py | Restart `customer-agent` job — the new UI routes are registered at startup. |
| Browser shows CORS error | UI served from a non-proxy origin not in allow-list | Set `CUSTOMER_AGENT_CORS_ORIGINS=http://yourhost:port` in `.env` and restart. |
| `EventSource` freezes 1–2 min | `investigation_stall_warning` — LLM call pending, not an error | Stall banners appear automatically; events resume when the LLM responds. Increase timeout via `stall_warn_interval_seconds` in `agents_config.json → investigation_workflow`. |
| `run_pipeline` returns empty | No monitoring targets configured | Edit `Code/CustomerAgent/src/config/monitoring_context.json`, or pass `customer_name` / `service_tree_id` in the Theatre dropdown. |
| Theatre renders no ticker entries | Backend is up but not authenticated | Check the backend logs for `DefaultAzureCredential` errors; run `az login`. |
| `/api/investigate` returns 500 | Circular import on cold start | Let the server finish initializing (first call to `/api/run` lazy-loads agents). Hit `/health` first. |

### Useful Kusto diagnostic query

Every pipeline run stamps a correlation vector (XCV). Use it to pull the
full event trail from App Insights:

```kql
cluster('https://ade.loganalytics.io/subscriptions/<sub-id>/resourceGroups/rg-ratio-ai-dev/providers/Microsoft.OperationalInsights/workspaces/<workspace-name>')
.database('<workspace-name>').AppTraces
| where TimeGenerated > ago(24h)
| where Properties.xcv == '<XCV-from-UI>'
| project event_timestamp=TimeGenerated,
          message=Message,
          event_name=Properties.EventName,
          hypothesis_selected=Properties.HypothesisId,
          speaker_change=Properties.NextSpeaker,
          from_phase=Properties.FromPhase,
          to_phase=Properties.ToPhase,
          llm_response_text=Properties.ResponseText,
          tool_invoked=Properties.Tool,
          query_text=Properties.QueryText,
          agent_name=Properties.AgentName,
          compound_signal_rationale=Properties.Rationale,
          signal_type=Properties.ContributingTypes,
          signal_confidence=Properties.Confidence,
          tool_or_agent_result=Properties.Result,
          error=Properties.Error
| where Properties.EventName !in ('EndpointHit','MCPCollectionCall','ToolCallStart','OutputParsed')
| order by event_timestamp desc
```

(Same structure as the Demo working-doc query — substitute your
subscription id and workspace name.)

---

## 8. Extending the Theatre

Three common tweaks:

1. **New scenario preset** — add an entry to `SCENARIO_PRESETS` at the
   bottom of `ChaTheatrePage.tsx`. The `body` object is passed directly
   to `/api/run` (`customer_name` / `service_tree_id`).
2. **New stage in the rail** — add to the `STAGES` array and teach
   `stageFor` which events map to it.
3. **New KPI column** — add a `ColumnShell` block and hang a new slice
   off `TheatreState`.

All state updates go through the pure `reduce` function, so it is easy
to unit-test and easy to port to Redux later if desired.
