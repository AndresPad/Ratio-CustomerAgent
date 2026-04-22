/**
 * ChaTheatrePage — the "Investigation Theatre".
 *
 * A single animated page that shows the full Signal → Evaluation → Hypothesis
 * → Scoring → Selection → Tool Execution → Summary flow as it streams in from
 * the backend `/api/run` SSE endpoint.
 *
 * Design goals (from the demo brief):
 *   • Represent the workflow as a sequence of "major events" — not every
 *     intermediate tool invocation.
 *   • Animate transitions between the Signal, Hypothesis, Evidence, and
 *     Action stages.
 *   • Always expose the "why": confidence, score, verdict, contributing
 *     factors.
 *   • Show progress indicators (%) while evidence is being gathered.
 *   • Surface the summarized list of tools invoked.
 *
 * Data flow:
 *   POST /customer-agent-api/api/run  →  SSE text/event-stream
 *       │
 *       └─ JSON frames with either:
 *             type=pipeline_started|signal_evaluation_complete|…
 *             OR  EventName=SignalEvaluationStart|SignalTypeEvaluated|
 *                           CompoundEvaluated|SignalDecision|
 *                           HypothesisScoring|HypothesisSelected|
 *                           HypothesisTransition|PhaseTransition|
 *                           AgentResponse|SpeakerSelected|ToolCall|
 *                           MCPCollectionCall|InvestigationComplete|…
 *
 * We reduce those raw events into a `TheatreState` and render it as a
 * four-column stage (Signals · Hypotheses · Evidence · Actions) with a
 * running "executive ticker" of important milestones at the top.
 */
import { useEffect, useMemo, useRef, useState, useCallback } from 'react';

// ─── Types ────────────────────────────────────────────────────────────────
type RawEvent = Record<string, unknown> & {
  type?: string;
  EventName?: string;
};

type StageKey =
  | 'signal_start'
  | 'signal_eval'
  | 'compound'
  | 'decision'
  | 'triage'
  | 'hypothesis_score'
  | 'hypothesis_select'
  | 'evidence'
  | 'reasoning'
  | 'action'
  | 'summary';

interface StageDef {
  key: StageKey;
  label: string;
  icon: string;
  color: string;
}

const STAGES: StageDef[] = [
  { key: 'signal_start',      label: 'Signal Start',         icon: 'fa-satellite-dish', color: '#17a2b8' },
  { key: 'signal_eval',       label: 'Signal Evaluation',    icon: 'fa-wave-square',    color: '#4fa8ff' },
  { key: 'compound',          label: 'Compound',             icon: 'fa-layer-group',    color: '#6c5ce7' },
  { key: 'decision',          label: 'Decision',             icon: 'fa-flag-checkered', color: '#0984e3' },
  { key: 'triage',            label: 'Triage',               icon: 'fa-stethoscope',    color: '#00b894' },
  { key: 'hypothesis_score',  label: 'Hypothesis Scoring',   icon: 'fa-calculator',     color: '#e17055' },
  { key: 'hypothesis_select', label: 'Hypothesis Selection', icon: 'fa-check-double',   color: '#d35400' },
  { key: 'evidence',          label: 'Evidence Collection',  icon: 'fa-search',         color: '#16a085' },
  { key: 'reasoning',         label: 'Reasoning',            icon: 'fa-brain',          color: '#d63031' },
  { key: 'action',            label: 'Action Planning',      icon: 'fa-bolt',           color: '#e84393' },
  { key: 'summary',           label: 'Summary',              icon: 'fa-flag',           color: '#28a745' },
];

interface SignalCard {
  id: string;
  name: string;
  strength: number;   // 0–5
  confidence: string;
  activated: boolean;
  rowCount?: number;
  when: number;
}

interface HypothesisCard {
  id: string;
  statement: string;
  score: number;      // 0–5 match_score
  rank?: number;
  status: 'scored' | 'active' | 'confirmed' | 'contributing' | 'refuted';
  confidence: number; // 0–1
  reasoning?: string;
  evidenceNeeded: string[];
  evidenceCollected: string[];
}

interface EvidenceCard {
  id: string;            // ER-* or tool name
  tool: string;
  summary: string;
  rows?: number;
  durationMs?: number;
  status: 'in_progress' | 'done' | 'error';
  when: number;
}

interface ActionCard {
  id: string;
  name: string;
  tier: string;
  justification: string;
}

interface ToolInvocation {
  tool: string;
  count: number;
  lastCall: number;
}

interface TickerEntry {
  id: number;
  stage: StageKey;
  icon: string;
  color: string;
  title: string;
  detail: string;
  ts: number;
}

interface TheatreState {
  xcv: string;
  startedAt: number | null;
  completedAt: number | null;
  currentStage: StageKey;
  currentPhase: string;
  currentAgent: string;
  stagesTouched: Set<StageKey>;
  decision: string;
  signals: Record<string, SignalCard>;
  hypotheses: Record<string, HypothesisCard>;
  evidence: EvidenceCard[];
  actions: ActionCard[];
  tools: Record<string, ToolInvocation>;
  ticker: TickerEntry[];
  stall: string;
  error: string;
  // Progress counters
  expectedEvidence: number;
  collectedEvidence: number;
}

function emptyState(): TheatreState {
  return {
    xcv: '',
    startedAt: null,
    completedAt: null,
    currentStage: 'signal_start',
    currentPhase: 'initializing',
    currentAgent: '',
    stagesTouched: new Set(),
    decision: '',
    signals: {},
    hypotheses: {},
    evidence: [],
    actions: [],
    tools: {},
    ticker: [],
    stall: '',
    error: '',
    expectedEvidence: 0,
    collectedEvidence: 0,
  };
}

// ─── Reducer ──────────────────────────────────────────────────────────────
let _tickId = 0;

function stageFor(ev: RawEvent): StageKey | null {
  const t = String(ev.type || ev.EventName || '');
  if (t === 'pipeline_started' || t === 'SignalEvaluationStart') return 'signal_start';
  if (t === 'SignalTypeEvaluated' || t === 'MCPCollectionCall')  return 'signal_eval';
  if (t === 'CompoundEvaluated')                                 return 'compound';
  if (t === 'SignalDecision' || t === 'signal_evaluation_complete') return 'decision';
  if (t === 'HypothesisScoring')                                 return 'hypothesis_score';
  if (t === 'HypothesisSelected' || t === 'hypothesis_evaluation_started') return 'hypothesis_select';
  if (t === 'HypothesisTransition')                              return 'hypothesis_select';
  if (t === 'EvidenceCycle' || t === 'ToolCall')                 return 'evidence';
  if (t === 'AgentResponse' || t === 'investigation_agent_response') {
    const phase = String((ev as RawEvent).ToPhase || (ev as RawEvent).phase || '').toLowerCase();
    if (phase.includes('triage'))        return 'triage';
    if (phase.includes('hypothes'))      return 'hypothesis_select';
    if (phase.includes('plan') || phase.includes('collect')) return 'evidence';
    if (phase.includes('reason'))        return 'reasoning';
    if (phase.includes('act'))           return 'action';
    if (phase.includes('notif') || phase.includes('complete')) return 'summary';
    return null;
  }
  if (t === 'PhaseTransition') {
    const to = String(ev.ToPhase || '').toLowerCase();
    if (to === 'triage')       return 'triage';
    if (to === 'hypothesizing') return 'hypothesis_score';
    if (to === 'planning' || to === 'collecting') return 'evidence';
    if (to === 'reasoning')    return 'reasoning';
    if (to === 'acting')       return 'action';
    if (to === 'notifying' || to === 'complete') return 'summary';
    return null;
  }
  if (t === 'InvestigationComplete' || t === 'pipeline_complete') return 'summary';
  return null;
}

function reduce(state: TheatreState, ev: RawEvent): TheatreState {
  const type = String(ev.type || ev.EventName || '');
  const next: TheatreState = {
    ...state,
    signals: { ...state.signals },
    hypotheses: { ...state.hypotheses },
    tools: { ...state.tools },
    evidence: state.evidence.slice(),
    actions: state.actions.slice(),
    ticker: state.ticker,           // keep reference; may append below
    stagesTouched: new Set(state.stagesTouched),
  };

  const stg = stageFor(ev);
  if (stg) {
    next.currentStage = stg;
    next.stagesTouched.add(stg);
  }

  // Phase tracking
  const phaseFromEv = String(ev.ToPhase || ev.phase || '') || next.currentPhase;
  if (phaseFromEv) next.currentPhase = phaseFromEv;

  const agentFromEv = String(ev.Agent || ev.agent || ev.NextSpeaker || '');
  if (agentFromEv) next.currentAgent = agentFromEv;

  let tickerTitle = '';
  let tickerDetail = '';

  switch (type) {
    case 'pipeline_started':
      next.startedAt = Date.now();
      next.xcv = String(ev.xcv || '');
      tickerTitle = 'Pipeline started';
      tickerDetail = `XCV ${next.xcv.slice(0, 8)}`;
      break;

    case 'SignalEvaluationStart':
      tickerTitle = 'Signal evaluation starting';
      tickerDetail = `${ev.CustomerName || ''} · ${ev.ServiceName || ev.ServiceTreeId || ''}`;
      break;

    case 'MCPCollectionCall': {
      const tool = String(ev.Tool || 'mcp_tool');
      const prev = next.tools[tool] || { tool, count: 0, lastCall: 0 };
      next.tools[tool] = { ...prev, count: prev.count + 1, lastCall: Date.now() };
      // Treat the first calls as ambient (signal building); only add to
      // evidence list when a tool fires during collection phases.
      if (next.currentPhase === 'collecting' || next.currentPhase === 'planning') {
        next.evidence.push({
          id: `EV-${next.evidence.length + 1}`,
          tool,
          summary: `${tool} → ${ev.RowCount ?? 0} rows`,
          rows: Number(ev.RowCount || 0),
          durationMs: Number(ev.DurationMs || 0),
          status: ev.Error ? 'error' : 'done',
          when: Date.now(),
        });
        next.collectedEvidence += 1;
      }
      tickerTitle = `MCP · ${tool}`;
      tickerDetail = `${ev.RowCount ?? 0} rows in ${ev.DurationMs ?? 0}ms`;
      break;
    }

    case 'SignalTypeEvaluated': {
      const id = String(ev.SignalTypeId || ev.SignalName || 'SIG');
      next.signals[id] = {
        id,
        name: String(ev.SignalName || id),
        strength: Number(ev.MaxStrength || 0),
        confidence: String(ev.BestConfidence || ''),
        activated: Number(ev.ActivatedCount || 0) > 0,
        rowCount: Number(ev.RowCount || 0),
        when: Date.now(),
      };
      tickerTitle = `Signal: ${ev.SignalName || id}`;
      tickerDetail = `${ev.ActivatedCount || 0} activated, strength ${Number(ev.MaxStrength || 0).toFixed(2)}`;
      break;
    }

    case 'CompoundEvaluated': {
      const id = String(ev.CompoundId || 'COMPOUND');
      if (ev.Activated) {
        next.signals[id] = {
          id,
          name: String(ev.CompoundName || id),
          strength: Number(ev.Strength || 0),
          confidence: String(ev.Confidence || ''),
          activated: true,
          when: Date.now(),
        };
      }
      tickerTitle = `Compound: ${ev.CompoundName || id}`;
      tickerDetail = ev.Activated
        ? `activated · strength ${Number(ev.Strength || 0).toFixed(2)}`
        : 'not activated';
      break;
    }

    case 'SignalDecision':
      next.decision = String(ev.Action || '');
      tickerTitle = `Decision: ${next.decision}`;
      tickerDetail = `${ev.SignalCount || 0} signals, ${ev.CompoundCount || 0} compounds`;
      break;

    case 'signal_evaluation_complete':
      tickerTitle = 'Signal evaluation complete';
      tickerDetail = `${(ev.results as unknown[] | undefined)?.length || 0} targets`;
      break;

    case 'HypothesisScoring': {
      tickerTitle = 'Hypotheses scored';
      const topId = String(ev.TopHypothesisId || '');
      const topScore = Number(ev.TopScore || 0);
      tickerDetail = `${ev.OutputHypothesisCount || 0} qualify · top ${topId} @ ${topScore.toFixed(2)}`;
      // Seed hypotheses from AllScores string if present (format: "ID=score, ID=score")
      const allScores = String(ev.AllScores || '');
      for (const tok of allScores.split(/[,\n]/)) {
        const m = tok.trim().match(/([A-Z0-9-]+)\s*[=:]\s*([\d.]+)/);
        if (!m) continue;
        const hid = m[1];
        const sc = Number(m[2]);
        if (!next.hypotheses[hid]) {
          next.hypotheses[hid] = {
            id: hid,
            statement: '',
            score: sc,
            status: 'scored',
            confidence: 0,
            evidenceNeeded: [],
            evidenceCollected: [],
          };
        } else {
          next.hypotheses[hid].score = sc;
        }
      }
      break;
    }

    case 'HypothesisSelected':
    case 'hypothesis_evaluation_started': {
      const hid = String(ev.HypothesisId || ev.hypothesis_id || '');
      if (!hid) break;
      const prev = next.hypotheses[hid] || {
        id: hid,
        statement: '',
        score: 0,
        status: 'scored' as const,
        confidence: 0,
        evidenceNeeded: [],
        evidenceCollected: [],
      };
      const needed = String(ev.EvidenceNeeded || '').split(',').map(s => s.trim()).filter(Boolean);
      next.hypotheses[hid] = {
        ...prev,
        statement: String(ev.Statement || ev.statement || prev.statement || ''),
        score: Number(ev.MatchScore || ev.match_score || prev.score || 0),
        rank: Number(ev.Rank || ev.rank || prev.rank || 0),
        status: 'active',
        evidenceNeeded: needed.length ? needed : prev.evidenceNeeded,
      };
      next.expectedEvidence = needed.length || next.expectedEvidence;
      next.collectedEvidence = 0;
      tickerTitle = `Evaluating ${hid}`;
      tickerDetail = `rank ${ev.Rank || ev.rank || '?'} / ${ev.TotalHypotheses || ev.total_hypotheses || '?'} · score ${Number(ev.MatchScore || ev.match_score || 0).toFixed(2)}`;
      break;
    }

    case 'HypothesisTransition': {
      const hid = String(ev.HypothesisId || '');
      if (!hid || !next.hypotheses[hid]) break;
      const status = String(ev.NewStatus || '').toLowerCase();
      const verdict: HypothesisCard['status'] =
        status.includes('confirm') ? 'confirmed' :
        status.includes('refut')   ? 'refuted' :
        status.includes('contrib') ? 'contributing' : 'active';
      next.hypotheses[hid] = {
        ...next.hypotheses[hid],
        status: verdict,
        confidence: Number(ev.Confidence || next.hypotheses[hid].confidence || 0),
      };
      tickerTitle = `${hid} → ${verdict}`;
      tickerDetail = `confidence ${Math.round(Number(ev.Confidence || 0) * 100)}%`;
      break;
    }

    case 'PhaseTransition':
      tickerTitle = `Phase · ${ev.FromPhase || '?'} → ${ev.ToPhase || '?'}`;
      tickerDetail = String(ev.Agent || '');
      break;

    case 'ToolCall': {
      const tool = String(ev.Tool || 'tool');
      const prev = next.tools[tool] || { tool, count: 0, lastCall: 0 };
      next.tools[tool] = { ...prev, count: prev.count + 1, lastCall: Date.now() };
      next.evidence.push({
        id: `EV-${next.evidence.length + 1}`,
        tool,
        summary: String(ev.Result || ev.ResponseText || tool),
        status: ev.Error ? 'error' : 'done',
        when: Date.now(),
      });
      next.collectedEvidence += 1;
      tickerTitle = `Tool · ${tool}`;
      tickerDetail = String(ev.Agent || '');
      break;
    }

    case 'SpeakerSelected':
      tickerTitle = `Speaker · ${ev.NextSpeaker || '?'}`;
      tickerDetail = String(ev.Reason || '');
      break;

    case 'investigation_agent_response': {
      const agent = String(ev.agent || '');
      // Try to extract actions from parsed structured output
      const tc = ev.tool_calls as Array<{ tool?: string; agent?: string }> | undefined;
      if (tc) {
        for (const c of tc) {
          const tool = String(c.tool || '');
          if (!tool) continue;
          const prev = next.tools[tool] || { tool, count: 0, lastCall: 0 };
          next.tools[tool] = { ...prev, count: prev.count + 1, lastCall: Date.now() };
        }
      }
      tickerTitle = `${agent || 'agent'} responded`;
      tickerDetail = `phase ${ev.phase || '?'} · symptoms ${ev.symptoms_count ?? 0} · hyp ${ev.hypotheses_count ?? 0} · ev ${ev.evidence_count ?? 0}`;
      break;
    }

    case 'InvestigationComplete':
    case 'pipeline_complete':
      next.completedAt = Date.now();
      tickerTitle = 'Investigation complete';
      tickerDetail = `symptoms ${ev.SymptomsCount ?? ''} · hyp ${ev.HypothesesCount ?? ''} · ev ${ev.EvidenceCount ?? ''}`;
      break;

    case 'investigation_stall_warning':
      next.stall = String(ev.agent || '') + ' (' + (ev.wait_seconds ?? '?') + 's)';
      break;

    case 'pipeline_error':
    case 'investigation_error':
    case 'investigation_workflow_error':
      next.error = String(ev.error || 'unknown error');
      break;

    default:
      break;
  }

  if (tickerTitle) {
    const entry: TickerEntry = {
      id: ++_tickId,
      stage: next.currentStage,
      icon: STAGES.find(s => s.key === next.currentStage)?.icon || 'fa-circle',
      color: STAGES.find(s => s.key === next.currentStage)?.color || '#4f6bed',
      title: tickerTitle,
      detail: tickerDetail,
      ts: Date.now(),
    };
    next.ticker = [entry, ...state.ticker].slice(0, 40);
  }

  return next;
}

// ─── SSE helper ──────────────────────────────────────────────────────────
async function* streamRunPipeline(
  body: { customer_name?: string | null; service_tree_id?: string | null },
  signal: AbortSignal,
): AsyncGenerator<RawEvent> {
  const res = await fetch('/customer-agent-api/api/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      while (buf.includes('\n\n')) {
        const idx = buf.indexOf('\n\n');
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6);
          if (payload.trim() === '[DONE]') return;
          try {
            yield JSON.parse(payload) as RawEvent;
          } catch { /* ignore */ }
        }
      }
    }
  } finally {
    reader.releaseLock();
  }
}

// ─── UI subcomponents ────────────────────────────────────────────────────
function StageRail({ state }: { state: TheatreState }) {
  const stages = STAGES;
  const currentIdx = stages.findIndex(s => s.key === state.currentStage);
  return (
    <div style={{
      display: 'flex', alignItems: 'stretch', gap: 0,
      background: 'var(--cha-bg-white)', border: '1px solid var(--cha-border)',
      borderRadius: 12, padding: '14px 18px', marginBottom: 16,
      boxShadow: 'var(--cha-shadow-sm)', overflowX: 'auto',
    }}>
      {stages.map((s, i) => {
        const touched = state.stagesTouched.has(s.key);
        const active = i === currentIdx;
        const done = i < currentIdx || (touched && !active);
        return (
          <div key={s.key} style={{ display: 'flex', alignItems: 'center', flex: 1, minWidth: 120 }}>
            <div style={{ textAlign: 'center', flex: 1 }}>
              <div style={{
                width: 38, height: 38, borderRadius: '50%', margin: '0 auto 6px',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: active ? s.color : done ? s.color : '#e2e5f1',
                color: active || done ? '#fff' : '#8a8aaa',
                boxShadow: active ? `0 0 0 6px ${s.color}33, 0 0 20px ${s.color}66` : 'none',
                transition: 'all 0.5s ease',
                animation: active ? 'chaPulse 1.4s ease-in-out infinite' : 'none',
                fontSize: 14,
              }}>
                <i className={`fas ${done && !active ? 'fa-check' : s.icon}`} />
              </div>
              <div style={{
                fontSize: 10, fontWeight: 700, letterSpacing: 0.3,
                color: active ? s.color : done ? 'var(--cha-text-primary)' : 'var(--cha-text-muted)',
                textTransform: 'uppercase',
              }}>{s.label}</div>
            </div>
            {i < stages.length - 1 && (
              <div style={{
                flex: '0 0 20px', height: 2,
                background: done ? s.color : '#e2e5f1',
                alignSelf: 'center', borderRadius: 1,
                transition: 'background 0.4s ease',
              }} />
            )}
          </div>
        );
      })}
      <style>{`
        @keyframes chaPulse {
          0%,100% { transform: scale(1); }
          50%     { transform: scale(1.1); }
        }
        @keyframes chaShimmer {
          0%   { background-position: -200% 0; }
          100% { background-position: 200% 0; }
        }
        @keyframes chaSlideIn {
          from { opacity: 0; transform: translateY(-6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
      `}</style>
    </div>
  );
}

function Ticker({ entries, running }: { entries: TickerEntry[]; running: boolean }) {
  return (
    <div style={{
      background: 'linear-gradient(135deg, #1e3c72 0%, #2a5298 100%)',
      color: '#fff', padding: '10px 16px', borderRadius: 10, marginBottom: 14,
      display: 'flex', alignItems: 'center', gap: 12, minHeight: 48,
    }}>
      <i className={`fas ${running ? 'fa-satellite-dish fa-fade' : 'fa-broadcast-tower'}`}
         style={{ fontSize: 16, color: '#8da4ff' }} />
      <div style={{
        display: 'flex', flexDirection: 'column', flex: 1, minWidth: 0,
        overflow: 'hidden',
      }}>
        {entries.slice(0, 3).map((e, i) => (
          <div key={e.id} style={{
            display: 'flex', alignItems: 'center', gap: 8, minHeight: 18,
            opacity: 1 - i * 0.35,
            fontSize: i === 0 ? 13 : 11,
            fontWeight: i === 0 ? 600 : 400,
            animation: i === 0 ? 'chaSlideIn 0.35s ease' : 'none',
            color: i === 0 ? '#fff' : '#cbd5ff',
          }}>
            <i className={`fas ${e.icon}`} style={{ color: e.color, fontSize: 11 }} />
            <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
              <strong>{e.title}</strong>
              {e.detail ? <span style={{ marginLeft: 10, color: '#a8b5e8' }}>{e.detail}</span> : null}
            </span>
          </div>
        ))}
        {entries.length === 0 && (
          <div style={{ fontSize: 12, color: '#a8b5e8' }}>
            Waiting for pipeline events…
          </div>
        )}
      </div>
    </div>
  );
}

function SignalColumn({ signals, decision }: { signals: Record<string, SignalCard>; decision: string }) {
  const list = Object.values(signals).sort((a, b) => b.strength - a.strength);
  return (
    <ColumnShell icon="fa-satellite-dish" title="Signals" color="#17a2b8"
                 count={list.length} footer={decision ? `Decision: ${decision}` : ''}>
      {list.map(s => {
        const pct = Math.min(100, (s.strength / 5) * 100);
        return (
          <div key={s.id} style={{ ...cardStyle, borderLeft: `3px solid ${s.activated ? '#17a2b8' : '#e2e5f1'}` }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 11, color: '#17a2b8' }}>{s.id}</span>
              <span style={{ fontSize: 10, fontWeight: 700, color: s.activated ? '#28a745' : '#8a8aaa' }}>
                {s.activated ? 'ACTIVATED' : 'idle'}
              </span>
            </div>
            <div style={{ fontSize: 12, color: 'var(--cha-text-primary)', marginTop: 2 }}>{s.name}</div>
            <div style={{ display: 'flex', gap: 8, fontSize: 10, color: 'var(--cha-text-muted)', marginTop: 4 }}>
              <span>strength {s.strength.toFixed(2)}</span>
              {s.confidence && <span>· {s.confidence}</span>}
              {typeof s.rowCount === 'number' && <span>· {s.rowCount} rows</span>}
            </div>
            <ProgressBar pct={pct} color="#17a2b8" />
          </div>
        );
      })}
    </ColumnShell>
  );
}

function HypothesisColumn({ state }: { state: TheatreState }) {
  const list = Object.values(state.hypotheses).sort((a, b) => b.score - a.score);
  return (
    <ColumnShell icon="fa-lightbulb" title="Hypotheses" color="#e17055" count={list.length}
                 footer={list.length ? `${list.filter(h => h.status !== 'scored').length} evaluated` : ''}>
      {list.map(h => {
        const conf = Math.round((h.confidence || 0) * 100);
        const score = Number(h.score || 0);
        const badge =
          h.status === 'confirmed'   ? { bg: '#28a745', label: 'CONFIRMED' } :
          h.status === 'contributing'? { bg: '#f0ad4e', label: 'CONTRIBUTING' } :
          h.status === 'refuted'     ? { bg: '#dc3545', label: 'REFUTED' } :
          h.status === 'active'      ? { bg: '#0984e3', label: 'EVALUATING' } :
                                        { bg: '#8a8aaa', label: 'SCORED' };
        return (
          <div key={h.id} style={{ ...cardStyle, borderLeft: `3px solid #e17055`, position: 'relative' }}>
            <span style={{
              position: 'absolute', top: -8, right: 8, fontSize: 9, fontWeight: 700,
              padding: '2px 7px', borderRadius: 8, color: '#fff', background: badge.bg,
            }}>{badge.label}</span>
            <div style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 11, color: '#e17055' }}>{h.id}</div>
            {h.statement && (
              <div style={{ fontSize: 12, color: 'var(--cha-text-primary)', marginTop: 2, lineHeight: 1.35 }}>
                {h.statement.length > 140 ? h.statement.slice(0, 140) + '…' : h.statement}
              </div>
            )}
            <div style={{ display: 'flex', gap: 8, fontSize: 10, color: 'var(--cha-text-muted)', marginTop: 4 }}>
              <span>score {score.toFixed(2)}/5</span>
              {h.rank ? <span>· rank #{h.rank}</span> : null}
              <span>· confidence {conf}%</span>
            </div>
            <ProgressBar pct={Math.min(100, (score / 5) * 100)} color="#e17055" label="match" />
            <ProgressBar pct={conf} color={badge.bg} label="confidence" />
          </div>
        );
      })}
    </ColumnShell>
  );
}

function EvidenceColumn({ state }: { state: TheatreState }) {
  const total = Math.max(state.expectedEvidence, state.collectedEvidence, 1);
  const pct = Math.min(100, (state.collectedEvidence / total) * 100);
  const label = state.expectedEvidence > 0
    ? `${state.collectedEvidence} / ${state.expectedEvidence} gathered`
    : `${state.collectedEvidence} gathered`;
  return (
    <ColumnShell icon="fa-search" title="Evidence" color="#16a085" count={state.evidence.length}
                 footer={label}>
      <div style={{ marginBottom: 8 }}>
        <ProgressBar pct={pct} color="#16a085" label={`${Math.round(pct)}% complete`} tall />
      </div>
      {state.evidence.slice(-12).reverse().map(ev => (
        <div key={ev.id} style={{ ...cardStyle, borderLeft: `3px solid #16a085` }}>
          <div style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span style={{ fontFamily: 'monospace', fontSize: 10, fontWeight: 700, color: '#16a085' }}>{ev.tool}</span>
            <span style={{ fontSize: 9, fontWeight: 700, color:
              ev.status === 'error' ? '#dc3545' :
              ev.status === 'done' ? '#28a745' : '#f0ad4e' }}>{ev.status.toUpperCase()}</span>
          </div>
          <div style={{ fontSize: 11, color: 'var(--cha-text-secondary)', marginTop: 2 }}>
            {ev.summary.length > 90 ? ev.summary.slice(0, 90) + '…' : ev.summary}
          </div>
          {(ev.rows !== undefined || ev.durationMs !== undefined) && (
            <div style={{ fontSize: 9, color: 'var(--cha-text-muted)', marginTop: 2 }}>
              {ev.rows !== undefined && <>rows {ev.rows}</>}
              {ev.durationMs !== undefined && <> · {ev.durationMs}ms</>}
            </div>
          )}
        </div>
      ))}
    </ColumnShell>
  );
}

function ToolsActionsColumn({ state }: { state: TheatreState }) {
  const tools = Object.values(state.tools).sort((a, b) => b.count - a.count);
  return (
    <ColumnShell icon="fa-bolt" title="Tools · Actions" color="#e84393"
                 count={tools.length}
                 footer={state.actions.length ? `${state.actions.length} actions` : ''}>
      <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                    color: '#8a8aaa', letterSpacing: 0.8, marginBottom: 4 }}>
        Tools invoked
      </div>
      {tools.map(t => (
        <div key={t.tool} style={{
          display: 'flex', justifyContent: 'space-between', alignItems: 'center',
          padding: '5px 8px', marginBottom: 4, borderRadius: 6,
          background: '#f5f6fa', fontSize: 11,
        }}>
          <span style={{ fontFamily: 'monospace', color: 'var(--cha-text-primary)',
                         overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {t.tool}
          </span>
          <span style={{ fontWeight: 700, color: '#e84393' }}>×{t.count}</span>
        </div>
      ))}
      {tools.length === 0 && <div style={{ color: 'var(--cha-text-muted)', fontSize: 11 }}>No tools invoked yet</div>}
      {state.actions.length > 0 && (
        <>
          <div style={{ fontSize: 10, fontWeight: 700, textTransform: 'uppercase',
                        color: '#8a8aaa', letterSpacing: 0.8, margin: '12px 0 4px' }}>
            Recommended actions
          </div>
          {state.actions.map((a, i) => (
            <div key={i} style={{ ...cardStyle, borderLeft: `3px solid #e84393` }}>
              <div style={{ fontFamily: 'monospace', fontWeight: 700, fontSize: 11, color: '#e84393' }}>
                {a.id}
              </div>
              <div style={{ fontSize: 11, color: 'var(--cha-text-primary)' }}>{a.name}</div>
              {a.tier && <div style={{ fontSize: 9, fontWeight: 700, color: '#8a8aaa', marginTop: 2 }}>
                TIER · {a.tier.toUpperCase()}
              </div>}
            </div>
          ))}
        </>
      )}
    </ColumnShell>
  );
}

// Column shell
const cardStyle: React.CSSProperties = {
  background: '#fff', padding: '8px 10px',
  border: '1px solid #e2e5f1', borderRadius: 8,
  marginBottom: 8, boxShadow: '0 1px 2px rgba(0,0,0,0.04)',
  animation: 'chaSlideIn 0.35s ease',
};

function ColumnShell({
  icon, title, color, count, footer, children,
}: {
  icon: string; title: string; color: string; count: number; footer?: string; children: React.ReactNode;
}) {
  return (
    <div style={{
      flex: 1, minWidth: 240, background: 'var(--cha-bg-white)',
      border: '1px solid var(--cha-border)', borderRadius: 10,
      padding: 12, display: 'flex', flexDirection: 'column',
      maxHeight: 'calc(100vh - 320px)', overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        paddingBottom: 8, borderBottom: `2px solid ${color}`, marginBottom: 8,
      }}>
        <i className={`fas ${icon}`} style={{ color, fontSize: 14 }} />
        <span style={{ fontWeight: 700, fontSize: 12, textTransform: 'uppercase', letterSpacing: 0.6 }}>{title}</span>
        <span style={{
          marginLeft: 'auto', fontSize: 10, fontWeight: 700, color: '#fff',
          background: color, padding: '2px 8px', borderRadius: 10,
        }}>{count}</span>
      </div>
      <div style={{ overflowY: 'auto', flex: 1, paddingRight: 4 }}>{children}</div>
      {footer && <div style={{
        fontSize: 10, color: 'var(--cha-text-muted)', paddingTop: 6,
        borderTop: '1px solid var(--cha-border)', marginTop: 6,
        textTransform: 'uppercase', fontWeight: 600, letterSpacing: 0.5,
      }}>{footer}</div>}
    </div>
  );
}

function ProgressBar({ pct, color, label, tall }: { pct: number; color: string; label?: string; tall?: boolean }) {
  return (
    <div style={{ marginTop: 4 }}>
      {label && (
        <div style={{ display: 'flex', justifyContent: 'space-between',
                      fontSize: 9, color: 'var(--cha-text-muted)', marginBottom: 2 }}>
          <span>{label}</span><span>{Math.round(pct)}%</span>
        </div>
      )}
      <div style={{ height: tall ? 8 : 4, borderRadius: 4, background: '#eef0f7', overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${pct}%`, borderRadius: 4,
          background: `linear-gradient(90deg, ${color} 0%, ${color}CC 40%, ${color}AA 60%, ${color} 100%)`,
          backgroundSize: '200% 100%',
          animation: pct > 0 && pct < 100 ? 'chaShimmer 2s linear infinite' : 'none',
          transition: 'width 0.6s ease',
        }} />
      </div>
    </div>
  );
}

// ─── Main page ────────────────────────────────────────────────────────────
const SCENARIO_PRESETS = [
  { id: 'live',         label: 'Live sweep (default)',     body: {} },
  { id: 'blackrock',    label: 'BlackRock ScaleSet',       body: { customer_name: 'BlackRock, Inc', service_tree_id: '49c39e84-285c-45e1-9008-ac6b217161e2' } },
] as const;

export default function ChaTheatrePage() {
  const [state, setState] = useState<TheatreState>(emptyState());
  const [running, setRunning] = useState(false);
  const [preset, setPreset] = useState<typeof SCENARIO_PRESETS[number]['id']>('live');
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => () => abortRef.current?.abort(), []);

  const start = useCallback(async () => {
    if (running) return;
    setState(emptyState());
    setRunning(true);
    const ctrl = new AbortController();
    abortRef.current?.abort();
    abortRef.current = ctrl;
    const body = SCENARIO_PRESETS.find(s => s.id === preset)?.body || {};

    try {
      for await (const ev of streamRunPipeline(body, ctrl.signal)) {
        setState(prev => reduce(prev, ev));
      }
    } catch (err) {
      if ((err as Error).name !== 'AbortError') {
        setState(prev => ({ ...prev, error: (err as Error).message }));
      }
    } finally {
      setRunning(false);
    }
  }, [running, preset]);

  const stop = useCallback(() => { abortRef.current?.abort(); abortRef.current = null; }, []);

  const durationSecs = useMemo(() => {
    if (!state.startedAt) return 0;
    const end = state.completedAt || Date.now();
    return Math.max(0, Math.round((end - state.startedAt) / 1000));
  }, [state.startedAt, state.completedAt, state.ticker.length]);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', overflow: 'hidden' }}>
      {/* Toolbar */}
      <div style={{
        display: 'flex', gap: 12, alignItems: 'center', marginBottom: 12,
        padding: '10px 14px', background: 'var(--cha-bg-white)',
        border: '1px solid var(--cha-border)', borderRadius: 10,
      }}>
        <i className="fas fa-theater-masks" style={{ color: '#7c3aed', fontSize: 18 }} />
        <div style={{ fontWeight: 700, fontSize: 14 }}>Investigation Theatre</div>
        <div style={{ fontSize: 11, color: 'var(--cha-text-muted)' }}>
          Live Signal → Hypothesis → Evidence → Action
        </div>
        <div style={{ flex: 1 }} />
        <select
          value={preset}
          onChange={e => setPreset(e.target.value as typeof preset)}
          disabled={running}
          style={{ padding: '6px 10px', fontSize: 12, borderRadius: 6,
                   border: '1px solid var(--cha-border)' }}
        >
          {SCENARIO_PRESETS.map(p => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        {state.xcv && (
          <span style={{ fontFamily: 'monospace', fontSize: 10, color: 'var(--cha-text-muted)' }}>
            XCV {state.xcv.slice(0, 8)}
          </span>
        )}
        <span style={{ fontSize: 11, color: 'var(--cha-text-muted)' }}>
          <i className="fas fa-clock" /> {durationSecs}s
        </span>
        {running ? (
          <button onClick={stop} className="cha-btn-run"
                  style={{ background: 'var(--cha-danger)', color: '#fff' }}>
            <i className="fas fa-stop" /> Stop
          </button>
        ) : (
          <button onClick={start} className="cha-btn-primary">
            <i className="fas fa-play" /> Run Pipeline
          </button>
        )}
      </div>

      {/* Ticker */}
      <Ticker entries={state.ticker} running={running} />

      {/* Stage rail */}
      <StageRail state={state} />

      {/* Error / stall banners */}
      {state.error && (
        <div style={{
          padding: '8px 12px', background: '#fde8ea', color: '#dc3545',
          border: '1px solid #f5c2c7', borderRadius: 8, marginBottom: 10, fontSize: 12,
        }}>
          <i className="fas fa-exclamation-triangle" /> {state.error}
        </div>
      )}
      {!state.error && state.stall && running && (
        <div style={{
          padding: '6px 12px', background: '#fef3e0', color: '#b26a00',
          border: '1px solid #f0ad4e', borderRadius: 8, marginBottom: 10, fontSize: 11,
        }}>
          <i className="fas fa-hourglass-half" /> Waiting for {state.stall}
        </div>
      )}

      {/* Four-column stage */}
      <div style={{ display: 'flex', gap: 12, flex: 1, minHeight: 0 }}>
        <SignalColumn signals={state.signals} decision={state.decision} />
        <HypothesisColumn state={state} />
        <EvidenceColumn state={state} />
        <ToolsActionsColumn state={state} />
      </div>
    </div>
  );
}
