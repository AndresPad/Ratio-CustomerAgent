# Agent Lightning Integration — Architecture Diagrams

## Overview

These diagrams illustrate how **Agent Lightning (AGL)** integrates into the RATIO-AI **CustomerAgent** multi-agent investigation system. AGL adds automatic prompt optimization (APO) on top of the existing Microsoft Agent Framework (MAF) — the 15+ agent GroupChat system continues to run unchanged while AGL wraps each agent in a `@rollout` decorator, scores outputs via DeepEval, and uses textual gradients + beam search to iteratively improve prompts.

---

## Simplified Overview

How Agent Lightning improves our AI agents — no code knowledge required.

```mermaid
graph LR
    subgraph today["Today — Manual Prompt Tuning"]
        direction LR
        DEV["👩‍💻 Engineer"]
        DEV -->|"writes & edits<br/>prompt by hand"| PROMPT["📝 Agent<br/>Instructions"]
        PROMPT -->|"runs"| AGENT["🤖 AI Agent"]
        AGENT -->|"produces"| OUT["📊 Investigation<br/>Results"]
        OUT -->|"engineer reviews,<br/>tweaks prompt,<br/>repeats..."| DEV
    end


    style today fill:#fff3e0,stroke:#E65100,stroke-width:2px
```

```mermaid
graph LR
    subgraph tomorrow["With Agent Lightning — Automatic Optimization"]
        direction LR
        SEED["📝 Starting<br/>Instructions"] -->|"fed into"| AGL["⚡ Agent Lightning<br/><b>Automatic Optimizer</b>"]
        AGL -->|"tries many<br/>variations"| AGENT2["🤖 AI Agent"]
        AGENT2 -->|"scored by"| SCORE["📏 Quality<br/>Scorer"]
        SCORE -->|"feedback<br/>loop"| AGL
        AGL -->|"picks the<br/>best one"| BETTER["✅ Improved<br/>Instructions"]
    end

    style tomorrow fill:#e8f5e9,stroke:#2E7D32,stroke-width:2px
```

### What changes?

| | Before | After |
|---|---|---|
| **Who tunes prompts?** | Engineer, manually | Agent Lightning, automatically |
| **How long?** | Hours of trial & error | Minutes of automated optimization |
| **How do we know it's better?** | Gut feel + spot checks | Measured score improvement on test cases |
| **Risk of breaking things?** | High — no safety net | Low — old prompt backed up, score compared before deploying |

### The core idea in one sentence

> **Agent Lightning automatically rewrites AI agent instructions to make them better at their job, using the same "try → score → improve" loop that humans do — just faster and more systematic.**

---

## Diagram 1: High-Level Architecture

The left side is the existing MAF agent system (unchanged). The right side is the AGL training layer that wraps agents, computes rewards, and produces optimized prompts.

```mermaid
graph TB
    subgraph existing["Existing System (unchanged)"]
        direction TB
        GC["🎯 GroupChat Orchestrator<br/><i>orchestrator.py</i>"]
        GC --> EE["Entity Extractor"]
        GC --> OA["Outage Analyst"]
        GC --> AA["AIRO Analyst"]
        GC --> CI["Customer Insights"]
        GC --> SU["Summarizer"]
        GC --> OTH["10+ Other Agents<br/><i>(SLI, Incident, Support collectors, etc.)</i>"]
        EE & OA & AA & CI & SU --> MCP["MCP Tools<br/><i>Kusto, IcM, SLI</i>"]
        PROMPTS["📄 Prompt .txt Files<br/><i>src/prompts/*.txt</i>"]
        PROMPTS -.->|"loaded at startup"| EE & OA & AA & CI & SU
    end

    subgraph agl["Agent Lightning Layer (new)"]
        direction TB
        RO["@rollout Wrappers<br/><i>training/rollouts/*_rollout.py</i>"]
        REWARD["Reward Functions<br/><i>training/rewards.py</i><br/>DeepEval + Verdict Match"]
        DS["Training Datasets<br/><i>training/datasets/*_tasks.py</i><br/>TRAIN_TASKS + VAL_TASKS"]
        APO["APO Algorithm<br/><i>Textual Gradients + Beam Search</i>"]
        TRAINER["AGL Trainer<br/><i>SharedMemoryExecutionStrategy</i>"]
        STORE["InMemoryLightningStore<br/><i>Prompt versions + scores</i>"]
        ADAPTER["TraceToMessages Adapter"]
    end

    subgraph output["Output"]
        OPT["✅ Optimized Prompts<br/><i>training/outputs/*_optimized.txt</i>"]
    end

    subgraph ui["CustomerAgentUI"]
        DASH["🖥️ Training Dashboard<br/><i>training_api.py → SSE events</i><br/>Reward chart · Prompt diff · Deploy"]
    end

    subgraph infra["Docker Infrastructure"]
        LS_SVC["LightningStore Service<br/><i>:8094 — SQLite persistence</i>"]
        DB_SVC["AGL Dashboard<br/><i>:8095 — reward curves, versions</i>"]
    end

    DS -->|"tasks"| TRAINER
    RO -->|"reward float"| TRAINER
    TRAINER -->|"orchestrates"| APO
    APO -->|"candidate prompts"| RO
    RO -->|"calls agent LLM"| AOAI["Azure OpenAI<br/><i>APO_MODEL endpoint</i>"]
    AOAI -->|"agent output"| RO
    RO -->|"output"| REWARD
    REWARD -->|"score 0.0–1.0"| RO
    TRAINER --> STORE
    STORE --> ADAPTER
    APO -->|"best prompt"| OPT
    OPT -.->|"deployed back to"| PROMPTS

    TRAINER -.->|"events"| DASH
    STORE -.->|"persisted to"| LS_SVC
    LS_SVC -.->|"visualized by"| DB_SVC

    style existing fill:#e8f5e9,stroke:#4CAF50,stroke-width:2px
    style agl fill:#e3f2fd,stroke:#2196F3,stroke-width:2px
    style output fill:#fff3e0,stroke:#FF9800,stroke-width:2px
    style ui fill:#f3e5f5,stroke:#9C27B0,stroke-width:2px
    style infra fill:#eceff1,stroke:#607D8B,stroke-width:2px
```

**Key insight:** AGL is a pure training-time addition. The MAF agents, GroupChat orchestration, and MCP tools are completely unchanged. AGL wraps each target agent in a `@rollout` function that calls the LLM directly (bypassing the full MAF factory for speed), scores the output, and feeds the reward back to APO for gradient-based prompt improvement.

---

## Diagram 2: APO Training Loop (Single Agent)

Traces one complete APO optimization cycle for a single agent. Shows how the seed prompt is iteratively improved through beam search with textual gradients.

```mermaid
flowchart TD
    START(["▶ Start: python -m training.run_apo"])

    subgraph init["1 · Initialization"]
        LOAD["Load seed prompt<br/><i>src/prompts/investigation_reasoner_prompt.txt</i>"]
        CLIENT["Create Azure OpenAI client<br/><i>DefaultAzureCredential → APO endpoint</i>"]
        CONF["Configure APO<br/><i>beam_width=2, branch_factor=2,<br/>beam_rounds=1, gradient_batch=4</i>"]
        MKSTORE["Create InMemoryLightningStore<br/><i>External reference survives crashes</i>"]
        MKTRAINER["Create Trainer<br/><i>algorithm=APO, strategy=SharedMemory,<br/>adapter=TraceToMessages</i>"]
    end

    subgraph fit["2 · trainer.fit() — APO Beam Search"]
        direction TB
        SEED["Seed Evaluation<br/><i>Run rollout on all train tasks<br/>with current prompt</i>"]
        GRAD["Compute Textual Gradients<br/><i>APO samples gradient_batch_size tasks,<br/>asks LLM: 'how should this prompt change<br/>to get better results?'</i>"]
        EDIT["Apply Edits → Branch Candidates<br/><i>APO generates branch_factor variants<br/>per beam position</i>"]
        SCORE["Score Candidates<br/><i>Run each candidate prompt through<br/>rollout on validation tasks</i>"]
        SELECT["Select Top-K<br/><i>Keep beam_width best prompts<br/>for next round</i>"]
        REPEAT{"More<br/>rounds?"}
    end

    subgraph rollout["3 · @rollout Wrapper (per task)"]
        direction TB
        RENDER["Render prompt template<br/><i>PromptTemplate.template → system message</i>"]
        CALL["Call Azure OpenAI<br/><i>Sync AzureOpenAI.chat.completions.create()</i>"]
        PARSE["Parse agent output<br/><i>Extract verdict + reasoning text</i>"]
        RWRD["Compute Reward<br/><i>rewards.compute_reasoner_reward()</i>"]
    end

    subgraph reward["4 · Reward Computation"]
        direction LR
        VERD["Verdict Match<br/><i>60% weight</i><br/>Expected verdict ∈ output?"]
        DEEP["DeepEval Relevancy<br/><i>40% weight</i><br/>AnswerRelevancyMetric"]
        COMPOSITE["Composite Score<br/><i>0.6 × verdict + 0.4 × relevancy</i>"]
    end

    subgraph extract["5 · Extract & Save"]
        BEST["Extract best prompt<br/><i>store.get_latest_resources()</i>"]
        SAVE["Save to disk<br/><i>training/outputs/*_optimized.txt</i>"]
    end

    DONE(["✅ APO Training Complete"])

    START --> LOAD --> CLIENT --> CONF --> MKSTORE --> MKTRAINER
    MKTRAINER --> SEED
    SEED --> GRAD --> EDIT --> SCORE --> SELECT
    SELECT --> REPEAT
    REPEAT -->|"Yes"| GRAD
    REPEAT -->|"No"| BEST

    SCORE -.->|"each candidate"| RENDER
    RENDER --> CALL --> PARSE --> RWRD
    RWRD --> VERD & DEEP
    VERD & DEEP --> COMPOSITE
    COMPOSITE -.->|"float ∈ [0,1]"| SCORE

    BEST --> SAVE --> DONE

    style init fill:#e8f5e9,stroke:#388E3C
    style fit fill:#e3f2fd,stroke:#1976D2
    style rollout fill:#fff8e1,stroke:#F9A825
    style reward fill:#fce4ec,stroke:#C62828
    style extract fill:#f3e5f5,stroke:#7B1FA2
```

**Key details:**
- The rollout wrapper uses a **sync `AzureOpenAI` client** (not the full MAF agent factory) for ~10x faster iteration — no need to spin up all 17 agents and MCP connections
- The Windows asyncio deque crash is mitigated by a monkey-patch on `SharedMemoryExecutionStrategy._run_runner` that retries on `IndexError`
- The `InMemoryLightningStore` is held as an external reference so prompt data survives runner thread crashes

---

## Diagram 3: Multi-Agent Optimization Pipeline

Sequential optimization order from `run_apo_all.py`. Entity extraction is the most upstream task; summarizer runs last because it synthesizes outputs from all analysts.

```mermaid
flowchart LR
    START(["▶ run_apo_all.py"])

    subgraph pipeline["Sequential Optimization Order"]
        direction LR
        EE["1️⃣ Entity Extractor<br/><i>maf_entity_extractor_prompt.txt</i><br/>Extracts services, regions, customers"]
        OA["2️⃣ Outage Analyst<br/><i>maf_outage_analyst_prompt.txt</i><br/>Analyzes outage signals"]
        AA["3️⃣ AIRO Analyst<br/><i>maf_airo_analyst_prompt.txt</i><br/>Analyzes AIRO data"]
        CI["4️⃣ Customer Insights<br/><i>maf_customer_insights_prompt.txt</i><br/>Analyzes customer impact"]
        SU["5️⃣ Summarizer<br/><i>maf_summarizer_prompt.txt</i><br/>Produces final summary"]
    end

    START --> EE
    EE -->|"✅ optimized prompt<br/>feeds downstream"| OA
    OA -->|"✅ optimized prompt"| AA
    AA -->|"✅ optimized prompt"| CI
    CI -->|"✅ optimized prompt"| SU

    subgraph per_agent["Per Agent: _optimize_agent()"]
        direction TB
        LOAD2["Load seed from src/prompts/"]
        IMPORT["Dynamic import:<br/>rollout module + dataset module"]
        APO2["Configure APO + Trainer"]
        FIT2["trainer.fit(rollout, train, val)"]
        EXTRACT2["Extract best prompt"]
        SAVE2["Save to training/outputs/"]
    end

    EE -.-> per_agent

    subgraph outputs["Optimized Outputs"]
        O1["entity_extractor_prompt_optimized.txt"]
        O2["outage_analyst_prompt_optimized.txt"]
        O3["airo_analyst_prompt_optimized.txt"]
        O4["customer_insights_prompt_optimized.txt"]
        O5["summarizer_prompt_optimized.txt"]
    end

    SU --> SUMMARY(["📊 Summary Table<br/><i>Agent | Seed | Optimized | Changed | Time</i>"])

    EE -.-> O1
    OA -.-> O2
    AA -.-> O3
    CI -.-> O4
    SU -.-> O5

    subgraph cli["CLI Options"]
        direction TB
        ALL["--agents entity_extractor,summarizer<br/><i>Run subset in order</i>"]
        SKIP["--skip-to customer_insights<br/><i>Resume from a specific agent</i>"]
    end

    style pipeline fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
    style per_agent fill:#f1f8e9,stroke:#558B2F
    style outputs fill:#fff3e0,stroke:#E65100
    style cli fill:#eceff1,stroke:#455A64
```

**Why this order?** Entity extraction is the most upstream task — its output (identified services, regions, customers) feeds into all analyst agents. The summarizer runs last because it synthesizes outputs from all analysts. Optimizing upstream prompts first ensures downstream agents train against the best available upstream quality.

---

## Diagram 4: Service Topology

Docker Compose service topology showing ports, data flows, and dependencies.

```mermaid
graph TB
    subgraph user["User / Browser"]
        BROWSER["🌐 Browser"]
    end

    subgraph docker["Docker Compose Services"]
        subgraph app["Application Layer"]
            BE["🔧 customer-agent<br/><b>FastAPI Backend</b><br/>Port 8503<br/><i>Agent orchestration,<br/>GroupChat, MCP tools</i>"]
            UI["🖥️ customer-agent-frontend<br/><b>CustomerAgentUI</b><br/>Port 5020<br/><i>Investigation UI +<br/>Training Dashboard</i>"]
            ST["📊 customer-agent-ui<br/><b>Streamlit UI</b><br/>Port 8501<br/><i>Legacy investigation UI</i>"]
        end

        subgraph agl_infra["AGL Infrastructure"]
            LS["💾 agl-lightningstore<br/><b>LightningStore</b><br/>Port 8094<br/><i>SQLite persistence<br/>Rollout traces, prompt versions,<br/>reward history</i>"]
            DB["📈 agl-dashboard<br/><b>AGL Dashboard</b><br/>Port 8095<br/><i>Reward curves,<br/>prompt version comparison,<br/>training run history</i>"]
        end

        subgraph volumes["Volumes"]
            VOL[("agl-store-data<br/><i>lightningstore.db</i>")]
        end
    end

    subgraph external["External Services"]
        AOAI["☁️ Azure OpenAI<br/><i>APO_AZURE_OPENAI_ENDPOINT</i><br/>Chat completions"]
        DEVAL["🔍 DeepEval<br/><i>Answer Relevancy scoring</i><br/>via Azure OpenAI"]
    end

    BROWSER -->|"Investigation queries<br/>POST /investigate"| BE
    BROWSER -->|"Training dashboard<br/>HTML + JS"| UI
    BROWSER -->|"Reward curves"| DB
    BROWSER -->|"Legacy UI"| ST

    UI -->|"POST /api/training/start"| UI
    UI -.->|"SSE: training_started,<br/>training_step,<br/>training_round_complete,<br/>training_complete"| BROWSER

    LS -->|"prompt versions<br/>+ scores"| DB
    VOL ---|"mounted"| LS

    ST -->|"depends_on: healthy"| BE
    UI -->|"depends_on: healthy"| BE
    DB -->|"depends_on"| LS

    BE -->|"agent LLM calls"| AOAI
    AOAI -.->|"rollout completions<br/>(training time)"| BE
    BE -.->|"reward scoring"| DEVAL

    style app fill:#e8f5e9,stroke:#2E7D32,stroke-width:2px
    style agl_infra fill:#e3f2fd,stroke:#1565C0,stroke-width:2px
    style external fill:#fce4ec,stroke:#C62828,stroke-width:1px
    style volumes fill:#f5f5f5,stroke:#9E9E9E
```

### Port Summary

| Port | Service | Purpose |
|------|---------|---------|
| **5020** | CustomerAgentUI | Training dashboard + investigation UI |
| **8094** | LightningStore | Persistent prompt/trace storage (SQLite) |
| **8095** | AGL Dashboard | Training visualization (reward curves, prompt diffs) |
| **8501** | Streamlit UI | Legacy investigation interface |
| **8503** | CustomerAgent | FastAPI backend (GroupChat orchestration, agents, MCP) |

### Data Flow Summary

| Flow | Protocol | Description |
|------|----------|-------------|
| UI → Training API | `POST /api/training/start` | Kicks off APO training (demo or live mode) |
| Training API → Browser | **SSE** (`text/event-stream`) | Real-time progress: round starts, gradient steps, scores, completion |
| Trainer → LightningStore | Internal SDK | Writes prompt versions, rollout traces, reward history |
| LightningStore → Dashboard | HTTP read | Dashboard reads store for visualization |
| Rollout → Azure OpenAI | HTTPS | Chat completion calls during training (sync client) |
| Reward → DeepEval → Azure OpenAI | HTTPS | Answer Relevancy scoring via the APO model endpoint |
