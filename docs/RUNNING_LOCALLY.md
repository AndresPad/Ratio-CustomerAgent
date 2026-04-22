# Running the Customer Agent Health Analysis Stack Locally

> **Goal:** get every backend service and the React UI running on your dev box
> so that you can open the browser, click **Start Investigation**, and watch
> the seven-stage pipeline — **Signal → Evaluation → Hypothesis → Scoring →
> Selection → Tool Execution → Summary** — animate in real time.
>
> The new live view lives at **`http://localhost:3010/customer-agent/live`**.

---

## 1. What runs where

| Service                       | Port  | Process                                                                                          | Health                                |
| ----------------------------- | ----- | ------------------------------------------------------------------------------------------------ | ------------------------------------- |
| **RATIO MCP Server**          | 8000  | `python Code/RATIO_MCP/src/server.py`                                                            | `GET http://127.0.0.1:8000/health`    |
| **Customer Agent API**        | 8503  | `uvicorn server.app:app --host 127.0.0.1 --port 8503` inside `Code/CustomerAgent/src`            | `GET http://127.0.0.1:8503/health`    |
| **Ratio UI (Vite dev)**       | 3010  | `npm run dev` inside `Code/CustomerAgent/ratio_ui_web`                                           | `GET http://127.0.0.1:3010`           |

The Vite dev server proxies `/cha-live-api/*` → `http://127.0.0.1:8503/*`
(see `ratio_ui_web/vite.config.ts`). So the React page calls the real
FastAPI backend with zero CORS friction.

---

## 2. Prerequisites

* **Python 3.11+**
* **Node.js 18+** (for Vite 5)
* A working **`.env`** at the repo root. Copy `.env.example` → `.env` and
  fill in at minimum:
  * `AZURE_OPENAI_ENDPOINT`
  * `AZURE_OPENAI_DEPLOYMENT`
  * `AZURE_OPENAI_API_VERSION`
  * `AZURE_TENANT_ID`
  * `AZURE_SUBSCRIPTION_ID`
  * (optional) `APPLICATIONINSIGHTS_CONNECTION_STRING` — enables the rich
    event logging the Live Orchestration view consumes.
  * (optional) `KUSTO_ICM_CLUSTER_URI` / `KUSTO_ICM_DATABASE` — required
    if you want real MCP tool calls; otherwise mocked data is used.
* Azure auth via `DefaultAzureCredential` (run `az login` once).

Create and activate a venv:

```bash
python -m venv .venv
# Linux/macOS
source .venv/bin/activate
# Windows PowerShell
.\.venv\Scripts\Activate.ps1

pip install -r requirements.txt
pip install -r Code/CustomerAgent/requirements.txt
```

---

## 3. One-shot start (Windows / PowerShell)

```powershell
.\Code\scripts\start_all.ps1
```

This boots all three services, waits for their health endpoints, and
tails their logs. `Ctrl+C` stops them. Flags:

* `-SkipFrontend` — start only backends
* `-StopOnly` — just kill anything holding the three ports

---

## 4. Manual start (macOS / Linux)

Open three terminals (or use `tmux`):

**Terminal 1 — MCP server**
```bash
cd Code/RATIO_MCP/src
export PYTHONPATH="$PWD"
python server.py
```

**Terminal 2 — Customer Agent API**
```bash
cd Code/CustomerAgent/src
export PYTHONPATH="$PWD"
python -m uvicorn server.app:app --host 127.0.0.1 --port 8503 --reload
```

**Terminal 3 — Ratio UI**
```bash
cd Code/CustomerAgent/ratio_ui_web
npm install        # first time only
npm run dev
```

Browse to **http://127.0.0.1:3010/customer-agent/live**.

---

## 5. Verifying each service

```bash
curl http://127.0.0.1:8000/health   # {"status":"ok"}
curl http://127.0.0.1:8503/health   # {"status":"ok","service":"MAF GroupChat Agent"}
```

If the **Live Orchestration** page top-bar shows **Connected** (green dot),
the UI is talking to the Customer Agent API correctly.

Test the pipeline end-to-end without the UI:

```bash
curl -N -X POST http://127.0.0.1:8503/api/run \
     -H "Content-Type: application/json" \
     -d '{}'
```

You should see a stream of `data: {...}` frames ending in `data: [DONE]`.

---

## 6. Using the Live Orchestration page

1. Navigate to **Customer Agent → Live Orchestration** in the left nav
   (route: `/customer-agent/live`).
2. Optionally type a **customer name** and **service_tree_id** override.
   Leave both blank to use the default targets from
   `Code/CustomerAgent/src/config/monitoring_context.json`.
3. Click **Start Investigation**.
4. Watch the **seven-stage pipeline bar** at the top light up from left
   to right:
   * **Signal** — `SignalEvaluationStart`, `MCPCollectionCall`, `SignalTypeEvaluated`
   * **Evaluation** — `CompoundEvaluated`, `SignalDecision`
   * **Hypothesis** — `InvestigationCreated`, `WorkflowStarted`
   * **Scoring** — `HypothesisScoring` (rolls up "N evaluated, M qualified")
   * **Selection** — `HypothesisSelected`, `hypothesis_evaluation_started`
   * **Tool Execution** — `ToolCall`, `EvidenceCycle`, phase transitions through `collecting → reasoning → acting`
   * **Summary** — `InvestigationComplete`, `pipeline_complete`
5. In parallel you'll see:
   * Hypothesis cards with **Rank**, animated **Score** and **Confidence** bars,
     and a verdict chip that flips to **SUPPORTED** / **REFUTED** as evidence lands.
   * A **Tool Execution** ticker with tool name, query fragment, row count,
     and duration.
   * A **Live Activity Feed** (colour-coded per category) with every
     meaningful event and a monotonic tick.
   * Metrics strip showing **Signal Types Activated**, **Compound
     Signals**, **Hypotheses**, **Evidence Progress (%)**, **Top
     Confidence**, and the current **investigation phase**.
6. When the run completes an **Investigation Summary** card renders at
   the bottom right with root cause, counts, and duration.

---

## 7. Event model — what the UI consumes

The backend streams **two kinds** of SSE frames on `POST /api/run`:

1. **Pipeline meta-events** (`type: …`) — framing events emitted by
   `server/app.py::run_pipeline`:
   `pipeline_started`, `signal_evaluation_complete`,
   `investigations_starting`, `pipeline_complete`, `pipeline_error`.
2. **AgentLogger events** (`EventName: …`) — emitted by
   `helper/agent_logger.py`:
   `SignalEvaluationStart`, `MCPCollectionCall`, `SignalTypeEvaluated`,
   `CompoundEvaluated`, `SignalDecision`, `WorkflowStarted`,
   `PhaseTransition`, `HypothesisScoring`, `HypothesisSelected`,
   `HypothesisTransition`, `SpeakerSelected`, `EvidenceCycle`,
   `ToolCall`, `AgentResponse`, `InvestigationCreated`, `InvestigationComplete`.

Both shapes are normalised into a single `LiveEvent` by
`src/api/liveOrchestrationClient.ts` with a canonical `kind` string. The
stateful reducer in `src/hooks/useLiveInvestigation.ts` maps them into
UI shape (signals, compounds, hypotheses, tool calls, summary, current
speaker, stage progress).

---

## 8. Correlating with Kusto logs

Every run carries an `xcv` correlation id which is shown in the hero
strip. Paste it into the query Manik shared to see the same events in
App Insights. **Replace the cluster URI, subscription id, resource
group and workspace with your own environment's values** — the ones
below are the Ratio dev workspace:

```kusto
cluster('https://ade.loganalytics.io/subscriptions/01819f01-7af1-4dd8-9354-9dccc163ceae/resourceGroups/rg-ratio-ai-dev/providers/Microsoft.OperationalInsights/workspaces/log-ratioai-dev').database('log-ratioai-dev').AppTraces
| where TimeGenerated > ago(24h)
| where Properties.xcv == '<paste xcv from hero>'
| extend error = Properties.Error
| project event_timestamp = TimeGenerated,
          message = Message,
          event_name = Properties.EventName,
          hypothesis_selected = Properties.HypothesisId,
          speaker_change = Properties.NextSpeaker,
          from_phase = Properties.FromPhase,
          to_phase = Properties.ToPhase,
          llm_response_text = Properties.ResponseText,
          tool_invoked = Properties.Tool,
          query_text = Properties.QueryText,
          agent_name = Properties.AgentName,
          compound_signal_rationale = Properties.Rationale,
          signal_type = Properties.ContributingTypes,
          signal_confidence = Properties.Confidence,
          tool_or_agent_result = Properties.Result,
          error = Properties.Error,
          Properties
| where Properties.EventName !in ('EndpointHit', 'MCPCollectionCall', 'ToolCallStart', 'OutputParsed')
| order by event_timestamp desc
```

---

## 9. Troubleshooting

| Symptom                                              | Likely cause                                       | Fix                                                                                      |
| ---------------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| Top-bar shows **Disconnected**                       | Customer Agent backend not running on 8503         | Start it (see §3–4) and refresh                                                          |
| `POST /api/run` returns 500 immediately              | Missing Azure OpenAI creds                         | Fill `.env` and restart the backend                                                      |
| UI spins forever at **Signal** stage                 | MCP tools can't reach Kusto                        | Check `KUSTO_ICM_CLUSTER_URI`, run `az login`, or use mocked data mode                   |
| No hypotheses appear                                 | Signal decision returned `quiet` / `watchlist`     | Expected — only `invoke_group_chat` kicks the investigation. Try another customer target |
| Live feed empty despite events streaming             | Browser blocked EventSource on HTTP; use HTTPS/dev | Keep running via Vite proxy at `/cha-live-api` (works over HTTP)                         |
| Pre-existing `npm run build` TS errors in unrelated files | `ChaActivePage.tsx`, `ChaHistoryPage.tsx` have unused-var warnings | Unrelated to the live page — use `npx vite build` to produce a production bundle         |

---

## 10. File-by-file map of the Live Orchestration feature

| Path                                                                                         | Purpose                                                                 |
| -------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------- |
| `Code/CustomerAgent/src/server/app.py`                                                       | Backend `POST /api/run` SSE endpoint (unchanged)                        |
| `Code/CustomerAgent/src/helper/agent_logger.py`                                              | Emits the events the UI consumes (unchanged)                            |
| `Code/CustomerAgent/ratio_ui_web/vite.config.ts`                                             | Added proxy `/cha-live-api` → `127.0.0.1:8503`                          |
| `Code/CustomerAgent/ratio_ui_web/src/api/liveOrchestrationClient.ts`                         | Typed SSE client + event normaliser                                     |
| `Code/CustomerAgent/ratio_ui_web/src/hooks/useLiveInvestigation.ts`                          | Reducer + `start/stop/reset` hook driving the animated state            |
| `Code/CustomerAgent/ratio_ui_web/src/pages/customer-agent/ChaLivePage.tsx`                   | The page itself (hero, pipeline bar, metrics, hypotheses, tools, feed, summary) |
| `Code/CustomerAgent/ratio_ui_web/src/pages/customer-agent/cha-theme.css`                     | New keyframes, pipeline/hero/panel/hypothesis/tool/feed styles          |
| `Code/CustomerAgent/ratio_ui_web/src/pages/customer-agent/ChaLayout.tsx`                     | Added **Live Orchestration** nav entry, swapped health check to 8503    |
| `Code/CustomerAgent/ratio_ui_web/src/App.tsx`                                                | New route `/customer-agent/live`                                        |
