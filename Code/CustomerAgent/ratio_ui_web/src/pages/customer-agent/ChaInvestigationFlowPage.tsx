/**
 * ChaInvestigationFlowPage — "Investigation Reasoning Flow"
 *
 * Flagship investigation visualisation that auto-plays through
 * seven stages with animated transitions, streaming reasoning trace,
 * relationship graph between hypotheses/evidence, and a polished
 * result panel.
 *
 * Flow:
 *   Signal → Symptom → Hypothesis → Evidence Collection →
 *   Confidence Scoring → Reasoning Animation → Result
 *
 * Integrates into ChaLayout (sidebar, top bar) via cha-theme.css
 * variables + Font Awesome icons (matching other CHA pages).
 */
import {
  Fragment,
  useState,
  useEffect,
  useCallback,
  useRef,
  useMemo,
  type CSSProperties,
} from 'react';

/* ═══════════════════════════════════════════════════════════════ */
/*  Types & constants                                              */
/* ═══════════════════════════════════════════════════════════════ */

type Stage =
  | 'signal'
  | 'symptom'
  | 'hypothesis'
  | 'evidence'
  | 'scoring'
  | 'reasoning'
  | 'result';

const STAGES: Stage[] = [
  'signal',
  'symptom',
  'hypothesis',
  'evidence',
  'scoring',
  'reasoning',
  'result',
];

const STAGE_META: Record<Stage, { label: string; icon: string; color: string }> = {
  signal:     { label: 'Signal',              icon: 'fa-bolt',            color: '#4f6bed' },
  symptom:    { label: 'Symptom',             icon: 'fa-exclamation-triangle', color: '#e17055' },
  hypothesis: { label: 'Hypothesis',          icon: 'fa-lightbulb',       color: '#0984e3' },
  evidence:   { label: 'Evidence Collection', icon: 'fa-search',          color: '#6c5ce7' },
  scoring:    { label: 'Confidence Scoring',  icon: 'fa-chart-bar',       color: '#a29bfe' },
  reasoning:  { label: 'Reasoning',           icon: 'fa-brain',           color: '#00b894' },
  result:     { label: 'Result',              icon: 'fa-check-circle',    color: '#00b894' },
};

const STAGE_DURATION: Record<Stage, number> = {
  signal: 1200,
  symptom: 1500,
  hypothesis: 1800,
  evidence: 2800,
  scoring: 2000,
  reasoning: 3200,
  result: 0,
};

/** Simulated agent activity per stage */
const STAGE_AGENT: Record<Stage, { agent: string; action: string }> = {
  signal:     { agent: 'Signal Detector',       action: 'Ingesting telemetry signals' },
  symptom:    { agent: 'Symptom Correlator',     action: 'Correlating symptom patterns' },
  hypothesis: { agent: 'Hypothesis Generator',   action: 'Generating candidate hypotheses' },
  evidence:   { agent: 'Evidence Collector',     action: 'Querying data sources for evidence' },
  scoring:    { agent: 'Confidence Scorer',      action: 'Computing Bayesian confidence scores' },
  reasoning:  { agent: 'Reasoning Engine',       action: 'Synthesising root-cause explanation' },
  result:     { agent: 'Summary Writer',         action: 'Investigation complete' },
};

/** n8n-style data labels on connectors between stages */
const CONNECTOR_LABELS: Record<Stage, string> = {
  signal: '', symptom: '1 signal', hypothesis: '3 symptoms',
  evidence: '3 hypotheses', scoring: '8 evidence items',
  reasoning: '3 scored', result: 'root cause',
};

const PHASE_COLORS: Record<string, string> = {
  initializing: '#17a2b8', triage: '#0984e3', hypothesizing: '#e17055',
  planning: '#fdcb6e', collecting: '#00b894', reasoning: '#d63031',
  acting: '#e84393', notifying: '#6c5ce7', complete: '#28a745',
};

/* ── Environment context (from .env.example) ────────────────── */

interface EnvEntry { label: string; value: string; icon: string; color: string; category: string }

const ENV_CONFIG: EnvEntry[] = [
  // Azure OpenAI
  { label: 'OpenAI Endpoint',   value: 'openai-primods-dev-eastus.openai.azure.com', icon: 'fa-brain',         color: '#0984e3', category: 'Azure OpenAI' },
  { label: 'Model Deployment',  value: 'gpt-4o',                                      icon: 'fa-microchip',     color: '#0984e3', category: 'Azure OpenAI' },
  { label: 'API Version',       value: '2024-12-01-preview',                           icon: 'fa-code-branch',   color: '#0984e3', category: 'Azure OpenAI' },
  // MCP Server
  { label: 'MCP Server',        value: 'localhost:8000/mcp',                           icon: 'fa-server',        color: '#6c5ce7', category: 'MCP' },
  { label: 'MCP Auth Audience', value: 'de5f2e0f-ac6d-418e-a64c-e38dbbd116e5',        icon: 'fa-key',           color: '#6c5ce7', category: 'MCP' },
  // Observability
  { label: 'Log Analytics',     value: '321fc84a-8346-40f4-acf6-a505a7f7dd90',        icon: 'fa-chart-line',    color: '#00b894', category: 'Observability' },
  { label: 'App Insights',      value: 'a90c4a2c-5356-4ec5-9e99-a388a844695b',        icon: 'fa-satellite-dish', color: '#00b894', category: 'Observability' },
  { label: 'Lookback Window',   value: '7 days',                                       icon: 'fa-calendar-alt',  color: '#00b894', category: 'Observability' },
  { label: 'Foundry Tracing',   value: 'Enabled',                                      icon: 'fa-wave-square',   color: '#00b894', category: 'Observability' },
  // Auth / SSO
  { label: 'SSO',               value: 'Enabled (Microsoft Entra)',                    icon: 'fa-shield-alt',    color: '#e17055', category: 'Auth' },
  { label: 'Tenant ID',         value: '72f988bf-86f1-41af-91ab-2d7cd011db47',        icon: 'fa-id-badge',      color: '#e17055', category: 'Auth' },
  { label: 'Key Vault',         value: 'kv-ratio-ai-dev',                              icon: 'fa-lock',          color: '#e17055', category: 'Auth' },
  // Orchestration
  { label: 'Max Turns',         value: '15',                                            icon: 'fa-redo',          color: '#fdcb6e', category: 'Orchestration' },
  { label: 'Agent Logging',     value: 'Enabled (content + LLM)',                      icon: 'fa-file-alt',      color: '#fdcb6e', category: 'Orchestration' },
  // Middleware
  { label: 'Eval Middleware',   value: 'Disabled (port 9000)',                          icon: 'fa-vial',          color: '#d63031', category: 'Middleware' },
  { label: 'Prompt Injection',  value: 'Disabled (port 9001)',                          icon: 'fa-user-shield',   color: '#d63031', category: 'Middleware' },
];

/** Simulated agent turns for the Agent Flow view */
const AGENT_TURNS: { agent: string; phase: string; icon: string; detail: string }[] = [
  { agent: 'Orchestrator',         phase: 'initializing', icon: 'fa-sitemap',              detail: 'Pipeline started, routing signal' },
  { agent: 'Signal Detector',      phase: 'triage',       icon: 'fa-bolt',                 detail: 'Ingesting API latency anomaly' },
  { agent: 'Symptom Correlator',   phase: 'triage',       icon: 'fa-stethoscope',          detail: 'Correlating 3 symptoms in window' },
  { agent: 'Hypothesis Generator', phase: 'hypothesizing',icon: 'fa-lightbulb',            detail: 'Generating 3 candidate hypotheses' },
  { agent: 'Hypothesis Scorer',    phase: 'hypothesizing',icon: 'fa-chart-bar',            detail: 'Scoring HYP-1, HYP-2, HYP-3' },
  { agent: 'Evidence Planner',     phase: 'planning',     icon: 'fa-clipboard-list',       detail: 'Planning 8 evidence queries' },
  { agent: 'Telemetry Agent',      phase: 'collecting',   icon: 'fa-chart-line',           detail: 'Query plan + connection pool analysis' },
  { agent: 'Resource Agent',       phase: 'collecting',   icon: 'fa-server',               detail: 'Heap snapshot + OOM kill check' },
  { agent: 'Outage Agent',         phase: 'collecting',   icon: 'fa-exclamation-triangle', detail: 'External API health + latency check' },
  { agent: 'Reasoner',             phase: 'reasoning',    icon: 'fa-brain',                detail: 'Bayesian update → HYP-1 wins at 92%' },
  { agent: 'Action Planner',       phase: 'acting',       icon: 'fa-tasks',                detail: 'CREATE INDEX CONCURRENTLY …' },
  { agent: 'Notification Agent',   phase: 'notifying',    icon: 'fa-bell',                 detail: 'Sending remediation report' },
];

interface EvidenceItem {
  title: string;
  detail: string;
  status: 'success' | 'failure' | 'neutral';
}

interface Hypothesis {
  id: string;
  label: string;
  prior: number;
  confidence: number;
}

interface Symptom {
  title: string;
  hypothesis: Hypothesis;
  evidence: EvidenceItem[];
}

interface TraceLine {
  text: string;
  type: 'normal' | 'highlight' | 'success' | 'fail' | 'result';
  indent?: boolean;
  stage: Stage;
}

interface ConfidenceScore {
  id: string;
  label: string;
  score: number;
}

interface RootCause {
  description: string;
  recommendedAction: string;
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Mock data                                                      */
/* ═══════════════════════════════════════════════════════════════ */

const SIGNAL = {
  title: 'Anomalous Spike in API Latency',
  symptoms: [
    {
      title: 'Database connection pool exhaustion',
      hypothesis: { id: 'HYP-1', label: 'Slow query causing pool exhaustion', prior: 40, confidence: 92 },
      evidence: [
        { title: 'Query plan analysis', detail: 'Sequential scan on users table (2.1M rows)', status: 'success' as const },
        { title: 'Connection wait time', detail: 'Avg wait: 1.8s (normally <5ms)', status: 'success' as const },
        { title: 'Recent schema migration', detail: 'Index dropped during deploy #4821', status: 'success' as const },
      ],
    },
    {
      title: 'Memory pressure on pod-web-3',
      hypothesis: { id: 'HYP-2', label: 'Memory leak in search service', prior: 35, confidence: 15 },
      evidence: [
        { title: 'Heap snapshot diff', detail: 'No significant object retention', status: 'failure' as const },
        { title: 'Pod restart history', detail: 'No OOM kills in last 24h', status: 'failure' as const },
      ],
    },
    {
      title: 'Increased error rate on /api/search',
      hypothesis: { id: 'HYP-3', label: 'Upstream dependency degradation', prior: 25, confidence: 8 },
      evidence: [
        { title: 'External API health', detail: 'All upstreams healthy', status: 'failure' as const },
        { title: 'Network latency check', detail: 'Inter-service latency normal (<2ms)', status: 'failure' as const },
      ],
    },
  ],
};

const TRACE: TraceLine[] = [
  { text: 'Ingesting signal: API latency anomaly detected', type: 'normal', stage: 'signal' },
  { text: 'Correlating 3 symptoms within the 5-minute window', type: 'normal', stage: 'symptom' },
  { text: 'Generating hypotheses from symptom patterns…', type: 'highlight', stage: 'hypothesis' },
  { text: 'H1: Slow query → pool exhaustion (prior: 0.40)', indent: true, type: 'normal', stage: 'hypothesis' },
  { text: 'H2: Memory leak in search service (prior: 0.35)', indent: true, type: 'normal', stage: 'hypothesis' },
  { text: 'H3: Upstream dependency failure (prior: 0.25)', indent: true, type: 'normal', stage: 'hypothesis' },
  { text: 'Collecting evidence for H1…', type: 'highlight', stage: 'evidence' },
  { text: '✓ Query plan shows sequential scan on 2.1M rows', indent: true, type: 'success', stage: 'evidence' },
  { text: '✓ Connection wait time 360x above baseline', indent: true, type: 'success', stage: 'evidence' },
  { text: '✓ Index dropped in deploy #4821 — matches timeline', indent: true, type: 'success', stage: 'evidence' },
  { text: '→ H1 confidence updated: 0.40 → 0.92', indent: true, type: 'result', stage: 'evidence' },
  { text: 'Collecting evidence for H2…', type: 'highlight', stage: 'evidence' },
  { text: '✗ No significant heap object retention found', indent: true, type: 'fail', stage: 'evidence' },
  { text: '✗ No OOM kills in 24h window', indent: true, type: 'fail', stage: 'evidence' },
  { text: '→ H2 confidence updated: 0.35 → 0.15', indent: true, type: 'result', stage: 'evidence' },
  { text: 'Collecting evidence for H3…', type: 'highlight', stage: 'evidence' },
  { text: '✗ All upstream services reporting healthy', indent: true, type: 'fail', stage: 'evidence' },
  { text: '✗ Inter-service network latency normal', indent: true, type: 'fail', stage: 'evidence' },
  { text: '→ H3 confidence updated: 0.25 → 0.08', indent: true, type: 'result', stage: 'evidence' },
  { text: 'Confidence scoring complete. Winner: H1 (0.92)', type: 'highlight', stage: 'scoring' },
  { text: 'Root cause: Missing index after migration #4821', type: 'result', stage: 'reasoning' },
  { text: 'Recommended fix: CREATE INDEX CONCURRENTLY …', type: 'success', stage: 'reasoning' },
];

const CONFIDENCE: ConfidenceScore[] = [
  { id: 'HYP-1', label: 'Slow query causing pool exhaustion', score: 92 },
  { id: 'HYP-2', label: 'Memory leak in search service', score: 15 },
  { id: 'HYP-3', label: 'Upstream dependency degradation', score: 8 },
];

const ROOT_CAUSE: RootCause = {
  description:
    'Missing index on users.email_normalized after migration #4821 caused full table scans, exhausting the DB connection pool.',
  recommendedAction:
    'CREATE INDEX CONCURRENTLY idx_users_email_norm ON users (email_normalized);',
};

/* ═══════════════════════════════════════════════════════════════ */
/*  Auto-play hook                                                 */
/* ═══════════════════════════════════════════════════════════════ */

function useFlow() {
  const [idx, setIdx] = useState(-1);
  const [traceCount, setTraceCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const tickRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const stage: Stage | null = idx >= 0 && idx < STAGES.length ? STAGES[idx] : null;
  const reached = STAGES.slice(0, idx + 1);

  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
  }, []);

  const start = useCallback(() => {
    clear();
    setIdx(-1);
    setTraceCount(0);
    setRunning(true);
    setElapsed(0);

    const t0 = Date.now();
    tickRef.current = setInterval(() => setElapsed(Math.round((Date.now() - t0) / 100) / 10), 100);

    let t = 400;
    STAGES.forEach((s, i) => {
      timers.current.push(setTimeout(() => setIdx(i), t));
      const lines = TRACE.filter(l => l.stage === s);
      const delay = lines.length > 0 ? Math.min(300, STAGE_DURATION[s] / (lines.length + 1)) : 0;
      let off = 200;
      lines.forEach(() => {
        timers.current.push(setTimeout(() => setTraceCount(c => c + 1), t + off));
        off += delay;
      });
      t += STAGE_DURATION[s];
    });
    timers.current.push(setTimeout(() => {
      setRunning(false);
      if (tickRef.current) { clearInterval(tickRef.current); tickRef.current = null; }
    }, t));
  }, [clear]);

  useEffect(() => { start(); return clear; }, [start, clear]);

  return { stage, reached, traceCount, running, start, elapsed };
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Sub-components                                                 */
/* ═══════════════════════════════════════════════════════════════ */

/** Horizontal stage rail (like ChaTheatrePage but with unique colors/icons per stage) */
function StageRail({ current, reached }: { current: Stage | null; reached: Stage[] }) {
  return (
    <div style={RAIL}>
      {STAGES.map((s, i) => {
        const m = STAGE_META[s];
        const done = reached.includes(s) && s !== current;
        const active = s === current;
        const future = !done && !active;
        return (
          <Fragment key={s}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <div
                style={{
                  width: 30, height: 30, borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 13,
                  background: active ? m.color : done ? m.color : '#e9ecef',
                  color: active || done ? '#fff' : '#adb5bd',
                  transition: 'all .3s',
                  boxShadow: active ? `0 0 0 4px ${m.color}33` : 'none',
                  animation: active ? 'cha-pulse 1.6s ease-in-out infinite' : 'none',
                }}
              >
                {done ? <i className="fas fa-check" style={{ fontSize: 11 }} /> : <i className={`fas ${m.icon}`} />}
              </div>
              <span
                style={{
                  fontSize: 11, fontWeight: 600,
                  color: active ? m.color : done ? '#495057' : '#adb5bd',
                  whiteSpace: 'nowrap',
                }}
              >
                {m.label}
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <i
                className="fas fa-chevron-right"
                style={{ fontSize: 9, color: done ? '#adb5bd' : '#dee2e6', margin: '0 2px' }}
              />
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

/** Stats strip (mini KPIs) */
function StatStrip({ reached }: { reached: Stage[] }) {
  const has = (s: Stage) => reached.includes(s);
  const stats = [
    { icon: 'fa-bolt', label: 'Signal', value: has('signal') ? '1' : '—', color: STAGE_META.signal.color },
    { icon: 'fa-exclamation-triangle', label: 'Symptoms', value: has('symptom') ? '3' : '—', color: STAGE_META.symptom.color },
    { icon: 'fa-lightbulb', label: 'Hypotheses', value: has('hypothesis') ? '3' : '—', color: STAGE_META.hypothesis.color },
    { icon: 'fa-search', label: 'Evidence', value: has('evidence') ? '8' : '—', color: STAGE_META.evidence.color },
    { icon: 'fa-check-circle', label: 'Confidence', value: has('scoring') ? '92%' : '—', color: STAGE_META.result.color },
  ];
  return (
    <div style={STATS_ROW}>
      {stats.map(s => (
        <div key={s.label} style={STAT_CARD}>
          <i className={`fas ${s.icon}`} style={{ fontSize: 14, color: s.color }} />
          <div>
            <div style={{ fontSize: 18, fontWeight: 700, color: '#1a1a2e' }}>{s.value}</div>
            <div style={{ fontSize: 10, color: '#8a8faa', fontWeight: 500 }}>{s.label}</div>
          </div>
        </div>
      ))}
    </div>
  );
}

/** Symptom card with nested hypothesis + evidence */
function SymptomCard({
  sym, showHyp, showEv, showFinal,
}: {
  sym: Symptom; showHyp: boolean; showEv: boolean; showFinal: boolean;
}) {
  const h = sym.hypothesis;
  const pct = showFinal ? h.confidence : h.prior;
  const good = pct >= 50;
  return (
    <div style={{ ...CARD, borderLeft: `3px solid ${STAGE_META.symptom.color}`, animation: 'cha-fade-in .3s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: showHyp ? 10 : 0 }}>
        <i className="fas fa-exclamation-triangle" style={{ color: STAGE_META.symptom.color, fontSize: 13 }} />
        <span style={{ fontWeight: 600, fontSize: 13, color: '#1a1a2e' }}>{sym.title}</span>
      </div>
      {showHyp && (
        <div style={{ background: '#f8f9fb', borderRadius: 6, padding: '8px 12px', marginBottom: showEv ? 8 : 0, animation: 'cha-fade-in .25s ease both' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
            <i className="fas fa-lightbulb" style={{ color: STAGE_META.hypothesis.color, fontSize: 12 }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: '#1a1a2e', flex: 1 }}>{h.label}</span>
            <span style={{ fontSize: 11, fontWeight: 700, color: good ? '#1a9a4a' : '#d1242f' }}>{pct}%</span>
          </div>
          <div style={{ height: 4, borderRadius: 2, background: '#e2e5f1', overflow: 'hidden' }}>
            <div style={{ height: '100%', borderRadius: 2, width: `${pct}%`, background: good ? '#1a9a4a' : '#d1242f', transition: 'width .8s ease' }} />
          </div>
        </div>
      )}
      {showEv && (
        <div style={{ paddingLeft: 12, animation: 'cha-fade-in .25s ease both' }}>
          {sym.evidence.map((ev, i) => (
            <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 0', borderTop: i > 0 ? '1px solid #eef0f4' : 'none' }}>
              <i
                className={`fas ${ev.status === 'success' ? 'fa-check-circle' : ev.status === 'failure' ? 'fa-times-circle' : 'fa-minus-circle'}`}
                style={{ fontSize: 12, marginTop: 2, color: ev.status === 'success' ? '#1a9a4a' : ev.status === 'failure' ? '#d1242f' : '#8a8faa' }}
              />
              <div>
                <div style={{ fontSize: 12, fontWeight: 600, color: '#1a1a2e' }}>{ev.title}</div>
                <div style={{ fontSize: 11, color: '#5c6370' }}>{ev.detail}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/** Confidence scoring panel */
function ScoringPanel({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div style={{ ...CARD, borderLeft: `3px solid ${STAGE_META.scoring.color}`, animation: 'cha-fade-in .35s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <i className="fas fa-chart-bar" style={{ color: STAGE_META.scoring.color, fontSize: 14 }} />
        <span style={{ fontWeight: 700, fontSize: 13, color: '#1a1a2e' }}>Confidence Scoring</span>
      </div>
      {CONFIDENCE.map(c => {
        const good = c.score >= 50;
        return (
          <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 8 }}>
            <span style={HYP_BADGE}>{c.id}</span>
            <span style={{ flex: 1, fontSize: 12, color: '#5c6370', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.label}</span>
            <div style={{ width: 80, height: 5, borderRadius: 3, background: '#e2e5f1', overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 3, width: `${c.score}%`, background: good ? '#1a9a4a' : '#d1242f', transition: 'width 1s ease' }} />
            </div>
            <span style={{ fontSize: 12, fontWeight: 700, minWidth: 32, textAlign: 'right', color: good ? '#1a9a4a' : '#d1242f' }}>{c.score}%</span>
          </div>
        );
      })}
    </div>
  );
}

/** Reasoning animation panel — shows trace lines typing in */
function ReasoningPanel({ visible }: { visible: boolean }) {
  if (!visible) return null;
  const lines = TRACE.filter(l => l.stage === 'scoring' || l.stage === 'reasoning');
  return (
    <div style={{ ...CARD, borderLeft: `3px solid ${STAGE_META.reasoning.color}`, animation: 'cha-fade-in .35s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10 }}>
        <i className="fas fa-brain" style={{ color: STAGE_META.reasoning.color, fontSize: 14 }} />
        <span style={{ fontWeight: 700, fontSize: 13, color: '#1a1a2e' }}>Reasoning</span>
      </div>
      <div style={{ fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace", fontSize: 11.5, lineHeight: 1.7, color: '#5c6370' }}>
        {lines.map((l, i) => (
          <div
            key={i}
            style={{ paddingLeft: l.indent ? 16 : 0, marginBottom: 2, animation: `cha-fade-in .25s ease ${i * 120}ms both` }}
          >
            <span style={{ color: '#8a8faa', marginRight: 4 }}>•</span>
            <span style={{ color: traceColor(l.type), fontWeight: l.type === 'highlight' ? 600 : 400 }}>{l.text}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

/** Relationship graph — SVG connectors between symptoms, hypotheses, evidence */
function RelationshipGraph({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div style={{ ...CARD, borderLeft: `3px solid ${STAGE_META.hypothesis.color}`, padding: '16px 12px', animation: 'cha-fade-in .35s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 14 }}>
        <i className="fas fa-project-diagram" style={{ color: STAGE_META.hypothesis.color, fontSize: 14 }} />
        <span style={{ fontWeight: 700, fontSize: 13, color: '#1a1a2e' }}>Relationship Graph</span>
      </div>
      <div style={{ display: 'flex', gap: 16, position: 'relative', minHeight: 180 }}>
        {/* Symptoms column */}
        <div style={{ flex: 1 }}>
          <div style={COL_HEADER}>Symptoms</div>
          {SIGNAL.symptoms.map((s, i) => (
            <div key={i} style={{ ...GRAPH_NODE, borderLeftColor: STAGE_META.symptom.color }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: '#1a1a2e' }}>{s.title}</span>
            </div>
          ))}
        </div>
        {/* Hypotheses column */}
        <div style={{ flex: 1 }}>
          <div style={COL_HEADER}>Hypotheses</div>
          {SIGNAL.symptoms.map((s, i) => {
            const good = s.hypothesis.confidence >= 50;
            return (
              <div key={i} style={{ ...GRAPH_NODE, borderLeftColor: STAGE_META.hypothesis.color }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                  <span style={{ fontSize: 9, fontWeight: 700, background: good ? '#d1fae5' : '#fee2e2', color: good ? '#059669' : '#dc2626', padding: '1px 6px', borderRadius: 4 }}>
                    {good ? 'SUPPORTED' : 'REFUTED'}
                  </span>
                </div>
                <span style={{ fontSize: 11, fontWeight: 600, color: '#1a1a2e', marginTop: 4, display: 'block' }}>{s.hypothesis.label}</span>
                <span style={{ fontSize: 10, color: '#5c6370' }}>{s.hypothesis.confidence}%</span>
              </div>
            );
          })}
        </div>
        {/* Evidence column */}
        <div style={{ flex: 1 }}>
          <div style={COL_HEADER}>Evidence</div>
          {SIGNAL.symptoms.map((s, i) => (
            <div key={i} style={{ ...GRAPH_NODE, borderLeftColor: STAGE_META.evidence.color }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: '#1a1a2e' }}>{s.evidence.length} items</span>
              <span style={{ fontSize: 10, color: '#5c6370' }}>
                {s.evidence.filter(e => e.status === 'success').length} supporting · {s.evidence.filter(e => e.status === 'failure').length} refuting
              </span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

/** Result / root cause panel */
function ResultPanel({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div style={{ ...CARD, borderLeft: `3px solid ${STAGE_META.result.color}`, background: '#f0fdf4', animation: 'cha-slide-in .4s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
        <i className="fas fa-check-circle" style={{ color: STAGE_META.result.color, fontSize: 18 }} />
        <span style={{ fontWeight: 700, fontSize: 15, color: STAGE_META.result.color }}>Root Cause Identified</span>
      </div>
      <p style={{ fontSize: 13, color: '#374151', lineHeight: 1.7, margin: '0 0 14px' }}>{ROOT_CAUSE.description}</p>

      {/* Contributing factors */}
      <div style={{ marginBottom: 14 }}>
        <div style={SECTION_LABEL}>Contributing Factors</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
          <span style={FACTOR_TAG}><i className="fas fa-database" style={{ marginRight: 4 }} />Missing Index</span>
          <span style={FACTOR_TAG}><i className="fas fa-clock" style={{ marginRight: 4 }} />Deploy #4821</span>
          <span style={FACTOR_TAG}><i className="fas fa-layer-group" style={{ marginRight: 4 }} />2.1M Row Scan</span>
        </div>
      </div>

      {/* Confidence breakdown */}
      <div style={{ marginBottom: 14 }}>
        <div style={SECTION_LABEL}>Final Confidence</div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ fontSize: 32, fontWeight: 800, color: STAGE_META.result.color }}>92%</div>
          <div style={{ flex: 1 }}>
            <div style={{ height: 8, borderRadius: 4, background: '#dcfce7', overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 4, width: '92%', background: STAGE_META.result.color, transition: 'width 1.2s ease' }} />
            </div>
          </div>
        </div>
      </div>

      {/* Recommended action */}
      <div style={SECTION_LABEL}>Recommended Action</div>
      <div style={{
        background: '#1a1a2e', borderRadius: 6, padding: '12px 14px',
        fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace",
        fontSize: 12, color: '#4ade80', lineHeight: 1.5,
      }}>
        {ROOT_CAUSE.recommendedAction}
      </div>

      {/* Timeline */}
      <div style={{ marginTop: 14, display: 'flex', gap: 20 }}>
        <div style={{ fontSize: 11, color: '#5c6370' }}><i className="fas fa-clock" style={{ marginRight: 4 }} />Duration: 12.6s</div>
        <div style={{ fontSize: 11, color: '#5c6370' }}><i className="fas fa-exchange-alt" style={{ marginRight: 4 }} />7 stages</div>
        <div style={{ fontSize: 11, color: '#5c6370' }}><i className="fas fa-search" style={{ marginRight: 4 }} />8 evidence items</div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Environment Context panel (from .env.example)                  */
/* ═══════════════════════════════════════════════════════════════ */

function EnvironmentContext({ visible }: { visible: boolean }) {
  const [expanded, setExpanded] = useState(false);

  const categories = useMemo(() => {
    const map = new Map<string, EnvEntry[]>();
    ENV_CONFIG.forEach(e => {
      if (!map.has(e.category)) map.set(e.category, []);
      map.get(e.category)!.push(e);
    });
    return Array.from(map.entries());
  }, []);

  if (!visible) return null;

  const preview = categories.slice(0, 2);
  const shown = expanded ? categories : preview;

  return (
    <div style={{ ...CARD, borderLeft: '3px solid #4f6bed', animation: 'cha-fade-in .3s ease both', padding: 0, overflow: 'hidden' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '12px 16px', borderBottom: '1px solid #e2e5f1', background: '#f8f9fb' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <i className="fas fa-cogs" style={{ color: '#4f6bed', fontSize: 14 }} />
          <span style={{ fontWeight: 700, fontSize: 13, color: '#1a1a2e' }}>Environment Context</span>
          <span style={{ fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 10, background: '#eef0ff', color: '#4f6bed' }}>
            .env
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <span style={{ fontSize: 10, color: '#8a8faa' }}>{ENV_CONFIG.length} settings · {categories.length} groups</span>
          <button
            onClick={() => setExpanded(!expanded)}
            style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '3px 10px', fontSize: 10, fontWeight: 600, borderRadius: 4, border: '1px solid #e2e5f1', background: '#fff', cursor: 'pointer', color: '#4f6bed', transition: 'all .15s' }}
          >
            <i className={`fas ${expanded ? 'fa-chevron-up' : 'fa-chevron-down'}`} style={{ fontSize: 8 }} />
            {expanded ? 'Collapse' : 'Show All'}
          </button>
        </div>
      </div>
      {/* Category grid */}
      <div style={{ padding: '12px 16px', display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))', gap: 12 }}>
        {shown.map(([cat, entries]) => (
          <div key={cat} style={{ background: '#f8f9fb', borderRadius: 8, border: '1px solid #eef0f4', overflow: 'hidden' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 12px', borderBottom: '1px solid #eef0f4' }}>
              <i className={`fas ${entries[0].icon}`} style={{ color: entries[0].color, fontSize: 11 }} />
              <span style={{ fontSize: 11, fontWeight: 700, color: entries[0].color, textTransform: 'uppercase' as const, letterSpacing: '.04em' }}>{cat}</span>
              <span style={{ fontSize: 9, color: '#adb5bd', marginLeft: 'auto' }}>{entries.length} items</span>
            </div>
            <div style={{ padding: '6px 0' }}>
              {entries.map((e, i) => (
                <div key={i} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '5px 12px' }}>
                  <i className={`fas ${e.icon}`} style={{ color: e.color, fontSize: 10, width: 14, textAlign: 'center' as const, flexShrink: 0 }} />
                  <span style={{ fontSize: 11, color: '#5c6370', minWidth: 110, flexShrink: 0 }}>{e.label}</span>
                  <span style={{ fontSize: 11, fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace", color: '#1a1a2e', fontWeight: 500, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }} title={e.value}>{e.value}</span>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
      {/* Status bar */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 16px', borderTop: '1px solid #eef0f4', background: '#f0fdf4' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ width: 8, height: 8, borderRadius: '50%', background: '#00b894', display: 'inline-block' }} />
          <span style={{ fontSize: 10, fontWeight: 600, color: '#059669' }}>Services connected</span>
        </div>
        <div style={{ display: 'flex', gap: 12, fontSize: 10, color: '#8a8faa' }}>
          <span><i className="fas fa-brain" style={{ marginRight: 4, fontSize: 9 }} />GPT-4o</span>
          <span><i className="fas fa-server" style={{ marginRight: 4, fontSize: 9 }} />MCP @ :8000</span>
          <span><i className="fas fa-chart-line" style={{ marginRight: 4, fontSize: 9 }} />Log Analytics</span>
          <span><i className="fas fa-shield-alt" style={{ marginRight: 4, fontSize: 9 }} />SSO Active</span>
        </div>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  n8n-style Workflow Canvas                                      */
/* ═══════════════════════════════════════════════════════════════ */

function WorkflowCanvas({ current, reached }: { current: Stage | null; reached: Stage[] }) {
  const NODE_W = 120, NODE_H = 64, GAP = 56, PAD_X = 24, PAD_Y = 16;
  const TOTAL_W = STAGES.length * NODE_W + (STAGES.length - 1) * GAP + PAD_X * 2;
  const TOTAL_H = NODE_H + PAD_Y * 2 + 24;
  const CY = PAD_Y + NODE_H / 2;

  return (
    <div style={{ background: '#fff', borderBottom: '1px solid #e2e5f1', flexShrink: 0, overflowX: 'auto', overflowY: 'hidden' }}>
      <style>{`
        @keyframes n8n-flow { to { stroke-dashoffset: -24; } }
      `}</style>
      <svg width={TOTAL_W} height={TOTAL_H} viewBox={`0 0 ${TOTAL_W} ${TOTAL_H}`} style={{ display: 'block', minWidth: TOTAL_W }}>
        {/* Connectors */}
        {STAGES.map((s, i) => {
          if (i === 0) return null;
          const prev = STAGES[i - 1];
          const prevDone = reached.includes(prev);
          const flowing = prevDone && (reached.includes(s) || s === current);
          const x1 = PAD_X + (i - 1) * (NODE_W + GAP) + NODE_W;
          const x2 = PAD_X + i * (NODE_W + GAP);
          const mx = (x1 + x2) / 2;
          const connColor = flowing ? STAGE_META[prev].color : '#dee2e6';
          return (
            <g key={`conn-${s}`}>
              <path d={`M ${x1} ${CY} C ${x1 + 18} ${CY}, ${x2 - 18} ${CY}, ${x2} ${CY}`} fill="none" stroke="#eef0f4" strokeWidth={3} strokeLinecap="round" />
              <path d={`M ${x1} ${CY} C ${x1 + 18} ${CY}, ${x2 - 18} ${CY}, ${x2} ${CY}`} fill="none" stroke={connColor} strokeWidth={flowing ? 3 : 2} strokeLinecap="round" strokeDasharray={flowing ? '6 6' : 'none'} style={flowing ? { animation: 'n8n-flow .6s linear infinite' } : undefined} opacity={flowing ? 1 : 0.35} />
              {flowing && CONNECTOR_LABELS[s] && (
                <g>
                  <rect x={mx - 36} y={CY - 22} width={72} height={16} rx={8} fill={STAGE_META[prev].color} opacity={0.12} />
                  <text x={mx} y={CY - 11} textAnchor="middle" fontSize={9} fontWeight={600} fill={STAGE_META[prev].color} style={{ fontFamily: "'Segoe UI', system-ui, sans-serif" }}>{CONNECTOR_LABELS[s]}</text>
                </g>
              )}
              {flowing && (
                <circle r={4} fill={STAGE_META[prev].color} opacity={0.85}>
                  <animateMotion dur="1.2s" repeatCount="indefinite" path={`M ${x1} ${CY} C ${x1 + 18} ${CY}, ${x2 - 18} ${CY}, ${x2} ${CY}`} />
                </circle>
              )}
            </g>
          );
        })}
        {/* Nodes */}
        {STAGES.map((s, i) => {
          const m = STAGE_META[s];
          const x = PAD_X + i * (NODE_W + GAP);
          const y = PAD_Y;
          const done = reached.includes(s) && s !== current;
          const active = s === current;
          return (
            <g key={s}>
              {active && <rect x={x - 4} y={y - 4} width={NODE_W + 8} height={NODE_H + 8} rx={14} fill="none" stroke={m.color} strokeWidth={2} opacity={0.25} style={{ animation: 'cha-pulse 1.8s ease-in-out infinite' }} />}
              <rect x={x} y={y} width={NODE_W} height={NODE_H} rx={10} fill={active ? m.color : done ? '#fff' : '#f8f9fb'} stroke={done ? m.color : active ? m.color : '#e2e5f1'} strokeWidth={done || active ? 2 : 1.5} style={{ transition: 'all .3s' }} />
              <circle cx={x + NODE_W / 2} cy={y + 20} r={12} fill={active ? 'rgba(255,255,255,0.25)' : done ? `${m.color}15` : '#eef0f4'} />
              <foreignObject x={x + NODE_W / 2 - 8} y={y + 12} width={16} height={16}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 16, height: 16, color: active ? '#fff' : done ? m.color : '#adb5bd', fontSize: 11 }}>
                  {done ? <i className="fas fa-check" /> : <i className={`fas ${m.icon}`} />}
                </div>
              </foreignObject>
              <text x={x + NODE_W / 2} y={y + NODE_H - 10} textAnchor="middle" fontSize={10} fontWeight={600} fill={active ? '#fff' : done ? '#1a1a2e' : '#adb5bd'} style={{ fontFamily: "'Segoe UI', system-ui, sans-serif" }}>
                {m.label.length > 14 ? m.label.slice(0, 13) + '…' : m.label}
              </text>
              {done && (
                <g><circle cx={x + NODE_W - 4} cy={y + 4} r={7} fill="#fff" /><circle cx={x + NODE_W - 4} cy={y + 4} r={6} fill="#1a9a4a" />
                  <foreignObject x={x + NODE_W - 10} y={y - 2} width={12} height={12}><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 12, height: 12, color: '#fff', fontSize: 7 }}><i className="fas fa-check" /></div></foreignObject>
                </g>
              )}
              {active && (
                <foreignObject x={x + NODE_W - 12} y={y + NODE_H - 12} width={14} height={14}><div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', width: 14, height: 14, color: '#fff', fontSize: 9 }}><i className="fas fa-circle-notch fa-spin" /></div></foreignObject>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Activity Bar (Live-page style)                                 */
/* ═══════════════════════════════════════════════════════════════ */

function ActivityBar({ stage, running, elapsed }: { stage: Stage | null; running: boolean; elapsed: number }) {
  const info = stage ? STAGE_AGENT[stage] : null;
  const meta = stage ? STAGE_META[stage] : null;
  const done = stage === 'result' && !running;
  return (
    <div style={ACTIVITY_BAR}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flex: 1, minWidth: 0 }}>
        {running && <span style={{ width: 10, height: 10, borderRadius: '50%', background: meta?.color ?? '#4f6bed', animation: 'cha-pulse 1.4s ease-in-out infinite', flexShrink: 0 }} />}
        {done && <i className="fas fa-check-circle" style={{ color: '#00b894', fontSize: 14, flexShrink: 0 }} />}
        {!running && !done && <i className="fas fa-pause-circle" style={{ color: '#adb5bd', fontSize: 14, flexShrink: 0 }} />}
        {info ? (
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span style={{ fontSize: 11, fontWeight: 700, padding: '2px 10px', borderRadius: 4, background: `${meta!.color}18`, color: meta!.color, whiteSpace: 'nowrap' }}>
              <i className={`fas ${meta!.icon}`} style={{ marginRight: 5, fontSize: 10 }} />{info.agent}
            </span>
            <span style={{ fontSize: 12, color: '#5c6370', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{info.action}</span>
          </div>
        ) : (
          <span style={{ fontSize: 12, color: '#adb5bd' }}>Idle — click Re-run to start</span>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexShrink: 0 }}>
        {stage && (
          <span style={{ fontSize: 10, fontWeight: 700, padding: '3px 10px', borderRadius: 12, background: running ? `${meta!.color}15` : '#f0fdf4', color: running ? meta!.color : '#059669', textTransform: 'uppercase', letterSpacing: '.05em' }}>
            <i className="fas fa-layer-group" style={{ marginRight: 4, fontSize: 9 }} />{done ? 'Complete' : STAGE_META[stage].label}
          </span>
        )}
        <span style={{ fontSize: 11, color: '#8a8faa', fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace" }}>
          <i className="fas fa-clock" style={{ marginRight: 4, fontSize: 10 }} />{elapsed.toFixed(1)}s
        </span>
        {running && <span style={{ fontSize: 11, color: '#8a8faa' }}><i className="fas fa-circle-notch fa-spin" style={{ marginRight: 4, fontSize: 10, color: '#4f6bed' }} />Processing</span>}
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Investigation Views (3-tab panel from ChaActivePage)           */
/* ═══════════════════════════════════════════════════════════════ */

function InvestigationViews({ visible }: { visible: boolean }) {
  const [view, setView] = useState<'graph' | 'flow' | 'stream'>('graph');
  if (!visible) return null;
  const tabs: { key: typeof view; icon: string; label: string }[] = [
    { key: 'graph', icon: 'fa-project-diagram', label: 'Relationship Graph' },
    { key: 'flow', icon: 'fa-route', label: 'Agent Flow' },
    { key: 'stream', icon: 'fa-stream', label: 'Activity Stream' },
  ];
  return (
    <div style={{ ...CARD, padding: 0, overflow: 'hidden', animation: 'cha-fade-in .35s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 0, borderBottom: '1px solid #e2e5f1', background: '#f8f9fb' }}>
        {tabs.map(t => (
          <button key={t.key} onClick={() => setView(t.key)} style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '10px 18px', fontSize: 12, fontWeight: 600, border: 'none', borderBottom: view === t.key ? '2px solid #4f6bed' : '2px solid transparent', background: 'transparent', cursor: 'pointer', color: view === t.key ? '#4f6bed' : '#8a8faa', transition: 'all .15s' }}>
            <i className={`fas ${t.icon}`} style={{ fontSize: 11 }} />{t.label}
          </button>
        ))}
      </div>
      {view === 'graph' && <InvGraphView />}
      {view === 'flow' && <InvFlowView />}
      {view === 'stream' && <InvStreamView />}
    </div>
  );
}

function InvGraphView() {
  const symptoms = SIGNAL.symptoms;
  const cardH = 76, cardGap = 10, nodeStep = cardH + cardGap;
  const svgH = Math.max(symptoms.length, 1) * nodeStep + 20;
  const centerY = (i: number) => i * nodeStep + cardH / 2;
  return (
    <div style={{ padding: 20 }}>
      <div style={{ display: 'flex', gap: 16, marginBottom: 16, fontSize: 10, color: '#8a8faa' }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: STAGE_META.symptom.color, display: 'inline-block' }} />Symptom</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: STAGE_META.hypothesis.color, display: 'inline-block' }} />Hypothesis</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: STAGE_META.evidence.color, display: 'inline-block' }} />Evidence</span>
        <span style={{ color: '#e2e5f1' }}>|</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 16, height: 3, borderRadius: 2, background: '#1a9a4a', display: 'inline-block' }} />Supports</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 16, height: 3, borderRadius: 2, background: '#d1242f', display: 'inline-block' }} />Refutes</span>
      </div>
      <div style={{ display: 'flex', gap: 0, alignItems: 'flex-start' }}>
        {/* Symptoms */}
        <div style={{ flex: 1, padding: '0 12px' }}>
          <div style={{ height: 28, fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: 0.8, color: STAGE_META.symptom.color, paddingBottom: 6, borderBottom: `2px solid ${STAGE_META.symptom.color}`, display: 'flex', alignItems: 'center', gap: 6 }}><i className="fas fa-exclamation-triangle" /> Symptoms</div>
          {symptoms.map((s, i) => (
            <div key={i} style={{ height: cardH, border: '1px solid #e2e5f1', borderLeft: `4px solid ${STAGE_META.symptom.color}`, borderRadius: 8, padding: '10px 12px', marginBottom: cardGap, background: '#fff', fontSize: 11, boxSizing: 'border-box' as const }}>
              <div style={{ fontFamily: 'monospace', fontSize: 10, fontWeight: 700, color: STAGE_META.symptom.color, marginBottom: 3 }}>SYM-{i + 1}</div>
              <div style={{ color: '#5c6370', lineHeight: 1.3 }}>{s.title}</div>
            </div>
          ))}
        </div>
        <div style={{ width: 48, flexShrink: 0 }}><div style={{ height: 28 }} /><svg width="48" height={svgH} style={{ display: 'block' }}>{symptoms.map((_, si) => (<path key={`sh${si}`} d={`M 0,${centerY(si)} C 24,${centerY(si)} 24,${centerY(si)} 48,${centerY(si)}`} fill="none" stroke={STAGE_META.symptom.color} strokeWidth="2" opacity="0.4" />))}</svg></div>
        {/* Hypotheses */}
        <div style={{ flex: 1, padding: '0 12px' }}>
          <div style={{ height: 28, fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: 0.8, color: STAGE_META.hypothesis.color, paddingBottom: 6, borderBottom: `2px solid ${STAGE_META.hypothesis.color}`, display: 'flex', alignItems: 'center', gap: 6 }}><i className="fas fa-lightbulb" /> Hypotheses</div>
          {symptoms.map((s, i) => {
            const good = s.hypothesis.confidence >= 50;
            return (
              <div key={i} style={{ height: cardH, border: '1px solid #e2e5f1', borderLeft: `4px solid ${STAGE_META.hypothesis.color}`, borderRadius: 8, padding: '10px 12px', marginBottom: cardGap, background: '#fff', fontSize: 11, position: 'relative' as const, boxSizing: 'border-box' as const }}>
                <span style={{ position: 'absolute' as const, top: -8, right: 8, fontSize: 9, fontWeight: 700, padding: '1px 7px', borderRadius: 8, color: '#fff', background: good ? '#1a9a4a' : '#d1242f' }}>{good ? 'SUPPORTED' : 'REFUTED'}</span>
                <div style={{ fontFamily: 'monospace', fontSize: 10, fontWeight: 700, color: STAGE_META.hypothesis.color, marginBottom: 3 }}>{s.hypothesis.id}</div>
                <div style={{ color: '#5c6370', lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis' }}>{s.hypothesis.label}</div>
                <div style={{ fontSize: 10, fontWeight: 700, marginTop: 2, color: good ? '#1a9a4a' : '#d1242f' }}>{s.hypothesis.confidence}% confidence</div>
              </div>
            );
          })}
        </div>
        <div style={{ width: 48, flexShrink: 0 }}><div style={{ height: 28 }} /><svg width="48" height={svgH} style={{ display: 'block' }}>{symptoms.map((s, si) => { const good = s.hypothesis.confidence >= 50; return (<path key={`he${si}`} d={`M 0,${centerY(si)} C 24,${centerY(si)} 24,${centerY(si)} 48,${centerY(si)}`} fill="none" stroke={good ? '#1a9a4a' : '#d1242f'} strokeWidth="2" opacity="0.5" strokeDasharray={good ? undefined : '4 3'} />); })}</svg></div>
        {/* Evidence */}
        <div style={{ flex: 1, padding: '0 12px' }}>
          <div style={{ height: 28, fontSize: 10, fontWeight: 700, textTransform: 'uppercase' as const, letterSpacing: 0.8, color: STAGE_META.evidence.color, paddingBottom: 6, borderBottom: `2px solid ${STAGE_META.evidence.color}`, display: 'flex', alignItems: 'center', gap: 6 }}><i className="fas fa-search" /> Evidence</div>
          {symptoms.map((s, i) => {
            const supp = s.evidence.filter(e => e.status === 'success').length;
            const ref = s.evidence.filter(e => e.status === 'failure').length;
            return (
              <div key={i} style={{ height: cardH, border: '1px solid #e2e5f1', borderLeft: `4px solid ${STAGE_META.evidence.color}`, borderRadius: 8, padding: '10px 12px', marginBottom: cardGap, background: '#fff', fontSize: 11, boxSizing: 'border-box' as const }}>
                <div style={{ fontFamily: 'monospace', fontSize: 10, fontWeight: 700, color: STAGE_META.evidence.color, marginBottom: 3 }}>{s.evidence.length} items</div>
                <div style={{ color: '#5c6370', lineHeight: 1.3 }}>{supp} supporting · {ref} refuting</div>
                <div style={{ display: 'flex', gap: 4, marginTop: 4 }}>
                  {s.evidence.map((ev, j) => (<span key={j} title={ev.title} style={{ width: 14, height: 14, borderRadius: '50%', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', fontSize: 8, background: ev.status === 'success' ? '#d1fae5' : '#fee2e2', color: ev.status === 'success' ? '#059669' : '#dc2626' }}><i className={`fas ${ev.status === 'success' ? 'fa-check' : 'fa-times'}`} /></span>))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function InvFlowView() {
  let lastPhase = '';
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '10px 16px', gap: 10, fontSize: 10, color: '#8a8faa', borderBottom: '1px solid #e2e5f1' }}>
        {['triage', 'hypothesizing', 'collecting', 'reasoning', 'acting', 'notifying'].map(p => (
          <span key={p} style={{ display: 'flex', alignItems: 'center', gap: 4 }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: PHASE_COLORS[p], display: 'inline-block' }} />{p.charAt(0).toUpperCase() + p.slice(1)}</span>
        ))}
      </div>
      <div style={{ padding: '32px 24px', overflowX: 'auto' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 0 }}>
          {AGENT_TURNS.map((turn, i) => {
            const phaseColor = PHASE_COLORS[turn.phase] || '#17a2b8';
            const showSep = lastPhase !== '' && turn.phase !== lastPhase;
            lastPhase = turn.phase;
            return (
              <div key={i} style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
                {showSep && (<div style={{ width: 2, minHeight: 80, background: '#e2e5f1', margin: '0 8px', borderRadius: 1, position: 'relative' as const, flexShrink: 0 }}><span style={{ position: 'absolute' as const, top: -16, left: '50%', transform: 'translateX(-50%)', fontSize: 8, fontWeight: 700, textTransform: 'uppercase' as const, color: '#8a8faa', whiteSpace: 'nowrap' as const, background: '#fff', padding: '0 4px' }}>{turn.phase.toUpperCase()}</span></div>)}
                {i > 0 && !showSep && (<svg width="32" height="20" style={{ flexShrink: 0 }}><path d="M0,10 L24,10" stroke="#e2e5f1" strokeWidth="2" fill="none" /><polygon points="24,5 32,10 24,15" fill="#adb5bd" /></svg>)}
                <div style={{ minWidth: 130, maxWidth: 160, padding: '12px 14px', borderRadius: 10, border: `2px solid ${phaseColor}`, background: '#fff', textAlign: 'center' as const, position: 'relative' as const }}>
                  <span style={{ position: 'absolute' as const, top: -9, right: -6, fontSize: 8, fontWeight: 700, background: '#1a1a2e', color: '#fff', padding: '2px 6px', borderRadius: 10, zIndex: 2 }}>#{i + 1}</span>
                  <div style={{ width: 30, height: 30, borderRadius: '50%', background: phaseColor, color: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', margin: '-22px auto 6px', fontSize: 13, border: '3px solid #fff' }}><i className={`fas ${turn.icon}`} /></div>
                  <div style={{ fontSize: 11, fontWeight: 700, whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' }}>{turn.agent}</div>
                  <span style={{ display: 'inline-block', fontSize: 8, fontWeight: 600, textTransform: 'uppercase' as const, marginTop: 3, padding: '2px 8px', borderRadius: 4, background: phaseColor, color: '#fff', letterSpacing: 0.5 }}>{turn.phase}</span>
                  <div style={{ fontSize: 8, color: '#8a8faa', marginTop: 4, whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' }}>{turn.detail}</div>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      <div style={{ display: 'flex', justifyContent: 'space-between', padding: '10px 16px', borderTop: '1px solid #e2e5f1', background: '#f8f9fb', fontSize: 11, color: '#5c6370' }}>
        <div style={{ display: 'flex', gap: 16 }}><span><strong>{AGENT_TURNS.length}</strong> total turns</span><span><strong>{new Set(AGENT_TURNS.map(t => t.agent)).size}</strong> agents used</span><span><strong>{new Set(AGENT_TURNS.map(t => t.phase)).size}</strong> phase transitions</span></div>
      </div>
    </div>
  );
}

function InvStreamView() {
  return (
    <div style={{ maxHeight: 400, overflowY: 'auto', padding: '12px 16px' }}>
      {TRACE.map((line, i) => {
        const meta = STAGE_META[line.stage];
        return (
          <div key={i} style={{ display: 'flex', alignItems: 'flex-start', gap: 10, padding: '8px 12px', borderLeft: `3px solid ${meta.color}`, marginBottom: 4, borderRadius: '0 6px 6px 0', background: '#f8f9fb' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 120, flexShrink: 0 }}>
              <i className={`fas ${meta.icon}`} style={{ color: meta.color, fontSize: 11 }} />
              <span style={{ fontSize: 10, fontWeight: 700, color: meta.color, textTransform: 'uppercase' as const }}>{meta.label}</span>
            </div>
            <span style={{ fontSize: 12, color: traceColor(line.type), fontWeight: line.type === 'highlight' ? 600 : 400, fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace" }}>{line.text}</span>
            <span style={{ marginLeft: 'auto', fontSize: 9, color: '#adb5bd', flexShrink: 0 }}>#{i + 1}</span>
          </div>
        );
      })}
    </div>
  );
}

/** Right sidebar — Activity Details (Theatre-page style) */
function ActivityDetailsSidebar({ stage, reached, running, elapsed }: {
  stage: Stage | null; reached: Stage[]; running: boolean; elapsed: number;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({
    progress: true, agents: true, skills: true, activity: true,
  });
  const toggle = (k: string) => setOpen(prev => ({ ...prev, [k]: !prev[k] }));
  const endRef = useRef<HTMLDivElement>(null);

  const completed = stage === 'result' && !running;
  const currentIdx = stage ? STAGES.indexOf(stage) : -1;

  // Derive agent list from STAGE_AGENT keyed by reached stages
  const agents = useMemo(() => {
    return reached.map(s => ({
      name: STAGE_AGENT[s].agent,
      action: STAGE_AGENT[s].action,
      stage: s,
      color: STAGE_META[s].color,
      icon: STAGE_META[s].icon,
      active: s === stage && running,
    }));
  }, [reached, stage, running]);

  // Derive tool/skill list from AGENT_TURNS that have been "reached"
  const skills = useMemo(() => {
    const reachedSet = new Set(reached);
    const phaseToStage: Record<string, Stage> = {
      initializing: 'signal', triage: 'symptom', hypothesizing: 'hypothesis',
      planning: 'evidence', collecting: 'evidence', reasoning: 'reasoning',
      acting: 'result', notifying: 'result',
    };
    return AGENT_TURNS.filter(t => {
      const s = phaseToStage[t.phase];
      return s && reachedSet.has(s);
    });
  }, [reached]);

  // Activity feed — trace lines up to current reached stage
  const activity = useMemo(() => {
    const reachedSet = new Set(reached);
    return TRACE.filter(l => reachedSet.has(l.stage));
  }, [reached]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [activity.length]);

  return (
    <div style={SIDEBAR}>
      {/* Header */}
      <div style={{
        padding: '12px 16px', borderBottom: '1px solid #e2e5f1',
        display: 'flex', alignItems: 'center', gap: 8,
        background: 'linear-gradient(135deg, #f9fafc 0%, #eef1f8 100%)',
      }}>
        <i className={`fas ${running ? 'fa-wave-square' : completed ? 'fa-check-circle' : 'fa-list-ul'}`}
           style={{ color: completed ? '#28a745' : '#4f6bed', fontSize: 15, animation: running ? 'cha-pulse 1.4s ease-in-out infinite' : 'none' }} />
        <span style={{ fontWeight: 700, fontSize: 13, color: '#1a1a2e' }}>Details</span>
        <div style={{ flex: 1 }} />
        {running && (
          <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: '#e3f2fd', color: '#1565c0', textTransform: 'uppercase' as const, letterSpacing: 0.5 }}>
            <i className="fas fa-circle" style={{ fontSize: 6, marginRight: 4, color: '#2196f3', animation: 'cha-pulse 1s ease-in-out infinite' }} />Live
          </span>
        )}
        {completed && (
          <span style={{ fontSize: 9, fontWeight: 700, padding: '2px 8px', borderRadius: 4, background: '#e8f5e9', color: '#2e7d32', textTransform: 'uppercase' as const, letterSpacing: 0.5 }}>Done</span>
        )}
        <span style={{ fontSize: 10, color: '#8a8faa', fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace" }}>
          {elapsed.toFixed(1)}s
        </span>
      </div>

      <div style={{ flex: 1, overflowY: 'auto' }}>
        {/* ── Progress ─────────────────────────────────────────── */}
        <SidebarSection id="progress" icon="fa-tasks" title="Progress" count={`${reached.length}/${STAGES.length}`} open={open.progress} onToggle={toggle}>
          {STAGES.map((s, i) => {
            const m = STAGE_META[s];
            const isCurrent = s === stage && running;
            const isDone = reached.includes(s) && (completed || i < currentIdx);
            const status: 'done' | 'current' | 'pending' = isCurrent ? 'current' : isDone || (completed && reached.includes(s)) ? 'done' : 'pending';
            return (
              <div key={s} style={{
                display: 'flex', alignItems: 'center', gap: 10, padding: '5px 14px', fontSize: 12,
                opacity: status === 'pending' ? 0.4 : 1,
                background: status === 'current' ? `${m.color}0D` : 'transparent',
                borderLeft: status === 'current' ? `3px solid ${m.color}` : '3px solid transparent',
              }}>
                <div style={{
                  width: 18, height: 18, borderRadius: '50%', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontSize: 9, color: '#fff',
                  background: status === 'done' ? '#28a745' : status === 'current' ? m.color : '#d0d7e2',
                  animation: status === 'current' ? 'cha-pulse 1.8s ease-in-out infinite' : 'none',
                }}>
                  <i className={`fas ${status === 'done' ? 'fa-check' : m.icon}`} />
                </div>
                <span style={{ flex: 1, fontWeight: status === 'current' ? 600 : 400, color: status === 'current' ? m.color : '#1a1a2e' }}>{m.label}</span>
                {status === 'current' && (
                  <span style={{ fontSize: 9, color: '#8a8faa' }}><i className="fas fa-circle-notch fa-spin" style={{ fontSize: 8, marginRight: 3 }} />active</span>
                )}
              </div>
            );
          })}
        </SidebarSection>

        {/* ── Agents ───────────────────────────────────────────── */}
        <SidebarSection id="agents" icon="fa-user-astronaut" title="Agents" count={String(agents.length)} open={open.agents} onToggle={toggle}>
          {agents.length === 0 ? (
            <div style={{ padding: '6px 14px 10px', fontSize: 11, color: '#8a8faa', fontStyle: 'italic' }}>No agents active yet.</div>
          ) : agents.map((a, i) => (
            <div key={i} style={{
              display: 'flex', alignItems: 'center', gap: 10, padding: '5px 14px', fontSize: 12,
              background: a.active ? `${a.color}0D` : 'transparent',
            }}>
              <div style={{
                width: 24, height: 24, borderRadius: '50%',
                background: a.active ? a.color : `${a.color}20`,
                color: a.active ? '#fff' : a.color,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 10, fontWeight: 700, flexShrink: 0,
                animation: a.active ? 'cha-pulse 1.8s ease-in-out infinite' : 'none',
              }}>
                <i className={`fas ${a.icon}`} style={{ fontSize: 10 }} />
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontWeight: a.active ? 600 : 500, whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {a.name}
                  {a.active && (
                    <span style={{ marginLeft: 6, fontSize: 9, color: a.color, fontWeight: 700 }}>
                      <i className="fas fa-circle" style={{ fontSize: 5, marginRight: 3, animation: 'cha-pulse 1s ease-in-out infinite' }} />ACTIVE
                    </span>
                  )}
                </div>
                <div style={{ fontSize: 10, color: '#8a8faa', whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' }}>{a.action}</div>
              </div>
            </div>
          ))}
        </SidebarSection>

        {/* ── Skills / Tools ────────────────────────────────────── */}
        <SidebarSection id="skills" icon="fa-toolbox" title="Skills" count={String(skills.length)} open={open.skills} onToggle={toggle}>
          {skills.length === 0 ? (
            <div style={{ padding: '6px 14px 10px', fontSize: 11, color: '#8a8faa', fontStyle: 'italic' }}>No skills invoked yet.</div>
          ) : (
            <div style={{ padding: '4px 14px 10px', display: 'flex', flexWrap: 'wrap' as const, gap: 6 }}>
              {skills.map((t, i) => {
                const phaseColor = PHASE_COLORS[t.phase] || '#17a2b8';
                return (
                  <span key={i} style={{
                    display: 'inline-flex', alignItems: 'center', gap: 5,
                    padding: '4px 8px', fontSize: 10, fontWeight: 500,
                    background: `${phaseColor}12`, color: phaseColor,
                    border: `1px solid ${phaseColor}30`, borderRadius: 12,
                  }}>
                    <i className={`fas ${t.icon}`} style={{ fontSize: 9 }} />
                    {t.agent}
                  </span>
                );
              })}
            </div>
          )}
          {/* Evidence progress bar */}
          {reached.includes('evidence') && (
            <div style={{ padding: '0 14px 10px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: 10, color: '#8a8faa', marginBottom: 3 }}>
                <span>Evidence collected</span>
                <span><strong>{SIGNAL.symptoms.reduce((a, s) => a + s.evidence.length, 0)}</strong> / 8</span>
              </div>
              <div style={{ height: 4, background: '#eef0f4', borderRadius: 2, overflow: 'hidden' }}>
                <div style={{
                  width: completed || reached.includes('scoring') ? '100%' : '60%',
                  height: '100%', background: 'linear-gradient(90deg, #4f6bed 0%, #16a085 100%)',
                  transition: 'width 0.6s ease',
                }} />
              </div>
            </div>
          )}
        </SidebarSection>

        {/* ── Activity timeline ───────────────────────────────── */}
        <SidebarSection id="activity" icon="fa-stream" title="Activity" count={String(activity.length)} open={open.activity} onToggle={toggle} last>
          {activity.length === 0 ? (
            <div style={{ padding: '6px 14px 10px', fontSize: 11, color: '#8a8faa', fontStyle: 'italic' }}>Waiting for investigation events…</div>
          ) : activity.map((line, i) => {
            const meta = STAGE_META[line.stage];
            const isLast = i === activity.length - 1;
            return (
              <div key={i} style={{
                display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 14px',
                borderLeft: `3px solid ${meta.color}`,
                background: isLast && running ? `${meta.color}08` : 'transparent',
                animation: isLast ? 'cha-fade-in .3s ease' : 'none',
              }}>
                <i className={`fas ${meta.icon}`} style={{ color: meta.color, fontSize: 10, marginTop: 3, width: 14, textAlign: 'center' as const, flexShrink: 0 }} />
                <div style={{ flex: 1, minWidth: 0 }}>
                  <span style={{
                    fontSize: 11, color: traceColor(line.type), fontWeight: line.type === 'highlight' ? 600 : 400,
                    fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace",
                  }}>{line.text}</span>
                </div>
                <span style={{ fontSize: 9, color: '#adb5bd', flexShrink: 0, marginTop: 2 }}>#{i + 1}</span>
              </div>
            );
          })}
          {running && (
            <div style={{ display: 'flex', gap: 4, padding: '6px 14px' }}>
              {[0, 1, 2].map(n => (
                <span key={n} style={{ width: 5, height: 5, borderRadius: '50%', background: '#4f6bed', animation: `cha-pulse 1.2s infinite ease-in-out ${n * 0.15}s` }} />
              ))}
            </div>
          )}
          <div ref={endRef} />
        </SidebarSection>

        {/* ── Confidence Scores ───────────────────────────────── */}
        {reached.includes('scoring') && (
          <div style={{ padding: '12px 16px', borderTop: '1px solid #e2e5f1' }}>
            <div style={{ fontSize: 10, fontWeight: 700, color: '#8a8faa', textTransform: 'uppercase' as const, letterSpacing: '.08em', marginBottom: 10 }}>Confidence Scores</div>
            {CONFIDENCE.map(c => {
              const good = c.score >= 50;
              return (
                <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                  <span style={{ ...HYP_BADGE, fontSize: 9 }}>{c.id}</span>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 11, color: '#5c6370', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>{c.label}</div>
                    <div style={{ height: 3, borderRadius: 2, background: '#eef0f4', overflow: 'hidden', marginTop: 2 }}>
                      <div style={{ height: '100%', borderRadius: 2, width: `${c.score}%`, background: good ? '#1a9a4a' : '#d1242f', transition: 'width 0.8s ease' }} />
                    </div>
                  </div>
                  <span style={{ fontSize: 11, fontWeight: 700, color: good ? '#1a9a4a' : '#d1242f', minWidth: 28, textAlign: 'right' as const }}>{c.score}%</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}

/** Collapsible section for the sidebar (matches Theatre FeedSection) */
function SidebarSection({
  id, icon, title, count, open, onToggle, last, children,
}: {
  id: string; icon: string; title: string; count?: string;
  open: boolean; onToggle: (id: string) => void; last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ borderBottom: last ? 'none' : '1px solid #e2e5f1' }}>
      <button
        type="button"
        onClick={() => onToggle(id)}
        style={{
          width: '100%', padding: '10px 14px', display: 'flex', alignItems: 'center', gap: 8,
          background: 'transparent', border: 'none', cursor: 'pointer',
          fontSize: 12, fontWeight: 700, color: '#1a1a2e', textAlign: 'left' as const,
        }}
      >
        <i className={`fas ${icon}`} style={{ color: '#4f6bed', fontSize: 12, width: 14, textAlign: 'center' as const }} />
        <span style={{ flex: 1 }}>{title}</span>
        {count !== undefined && (
          <span style={{ fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 10, background: '#eef0ff', color: '#4f6bed' }}>{count}</span>
        )}
        <i className={`fas fa-chevron-${open ? 'up' : 'down'}`} style={{ fontSize: 10, color: '#8a8faa' }} />
      </button>
      {open && <div style={{ paddingBottom: 6 }}>{children}</div>}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Helpers                                                        */
/* ═══════════════════════════════════════════════════════════════ */

function traceColor(type: TraceLine['type']): string {
  switch (type) {
    case 'highlight': return '#4f6bed';
    case 'success':   return '#1a9a4a';
    case 'fail':      return '#d1242f';
    case 'result':    return '#b45700';
    default:          return '#5c6370';
  }
}

/* ═══════════════════════════════════════════════════════════════ */
/*  Style constants (CSSProperties objects — matches cha-theme)    */
/* ═══════════════════════════════════════════════════════════════ */

const RAIL: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 4, padding: '14px 20px',
  background: '#fff', borderBottom: '1px solid #e2e5f1', overflowX: 'auto', flexShrink: 0,
};

const STATS_ROW: CSSProperties = {
  display: 'flex', gap: 12, padding: '12px 20px',
  background: '#fff', borderBottom: '1px solid #e2e5f1', flexShrink: 0,
};

const ACTIVITY_BAR: CSSProperties = {
  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
  gap: 12, padding: '10px 20px',
  background: '#fff', borderBottom: '1px solid #e2e5f1', flexShrink: 0,
};

const STAT_CARD: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 10, padding: '8px 14px',
  background: '#f8f9fb', borderRadius: 8, border: '1px solid #e2e5f1', flex: 1,
};

const CARD: CSSProperties = {
  background: '#fff', borderRadius: 8, padding: '14px 16px',
  border: '1px solid #e2e5f1', marginBottom: 12,
  boxShadow: '0 1px 3px rgba(0,0,0,.05)',
};

const GRAPH_NODE: CSSProperties = {
  background: '#f8f9fb', borderRadius: 6, padding: '8px 10px',
  borderLeft: '3px solid', marginBottom: 8,
};

const COL_HEADER: CSSProperties = {
  fontSize: 10, fontWeight: 700, color: '#8a8faa', textTransform: 'uppercase',
  letterSpacing: '.06em', marginBottom: 8,
};

const HYP_BADGE: CSSProperties = {
  fontSize: 10, fontWeight: 700, background: '#eef0ff', color: '#4f6bed',
  padding: '2px 8px', borderRadius: 4, whiteSpace: 'nowrap',
};

const SECTION_LABEL: CSSProperties = {
  fontSize: 10, fontWeight: 700, color: '#8a8faa', textTransform: 'uppercase',
  letterSpacing: '.08em', marginBottom: 8,
};

const FACTOR_TAG: CSSProperties = {
  fontSize: 11, fontWeight: 500, padding: '3px 10px', borderRadius: 12,
  background: '#ecfdf5', color: '#059669', display: 'inline-flex', alignItems: 'center',
};

const SIDEBAR: CSSProperties = {
  width: 380, minWidth: 340, background: '#fff',
  borderLeft: '1px solid #e2e5f1', display: 'flex', flexDirection: 'column',
  boxShadow: '-2px 0 8px rgba(0,0,0,.03)', overflow: 'hidden',
};

/* ═══════════════════════════════════════════════════════════════ */
/*  Main page export                                               */
/* ═══════════════════════════════════════════════════════════════ */

export default function ChaInvestigationFlowPage() {
  const { stage, reached, traceCount, running, start, elapsed } = useFlow();
  const has = (s: Stage) => reached.includes(s);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#f5f6fa', overflow: 'hidden' }}>
      {/* n8n-style workflow canvas */}
      <WorkflowCanvas current={stage} reached={reached} />

      {/* Stage rail */}
      <StageRail current={stage} reached={reached} />

      {/* Stats strip */}
      <StatStrip reached={reached} />

      {/* Activity bar (agent + phase + elapsed) */}
      <ActivityBar stage={stage} running={running} elapsed={elapsed} />

      {/* Re-run bar */}
      <div style={{ display: 'flex', justifyContent: 'flex-end', padding: '8px 20px 0', flexShrink: 0 }}>
        <button
          onClick={start}
          disabled={running}
          className="cha-btn-primary"
          style={{
            display: 'flex', alignItems: 'center', gap: 6,
            padding: '6px 18px', fontSize: 12, fontWeight: 600, borderRadius: 6,
            border: 'none', cursor: running ? 'not-allowed' : 'pointer',
            background: running ? '#adb5bd' : '#4f6bed', color: '#fff',
            transition: 'background .15s',
          }}
        >
          <i className={`fas ${running ? 'fa-spinner fa-spin' : 'fa-redo'}`} />
          {running ? 'Running…' : 'Re-run'}
        </button>
      </div>

      {/* Main body */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        {/* Left panel — progressive reveal */}
        <div style={{ flex: 1, overflowY: 'auto', padding: '16px 20px 20px' }}>
          {/* Signal */}
          {has('signal') && (
            <div style={{ ...CARD, borderLeft: `3px solid ${STAGE_META.signal.color}`, animation: 'cha-fade-in .3s ease both' }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                <i className="fas fa-bolt" style={{ color: STAGE_META.signal.color, fontSize: 16 }} />
                <span style={{ fontWeight: 700, fontSize: 15, color: '#1a1a2e' }}>{SIGNAL.title}</span>
              </div>
            </div>
          )}

          {/* Environment context from .env.example */}
          <EnvironmentContext visible={has('signal')} />

          {/* Symptom cards */}
          {has('symptom') && SIGNAL.symptoms.map((sym, i) => (
            <SymptomCard
              key={i}
              sym={sym}
              showHyp={has('hypothesis')}
              showEv={has('evidence')}
              showFinal={has('scoring')}
            />
          ))}

          {/* Relationship graph — after evidence */}
          <RelationshipGraph visible={has('evidence')} />

          {/* Confidence scoring */}
          <ScoringPanel visible={has('scoring')} />

          {/* Reasoning */}
          <ReasoningPanel visible={has('reasoning')} />

          {/* Investigation views (graph, agent flow, activity stream) */}
          <InvestigationViews visible={has('evidence')} />

          {/* Result */}
          <ResultPanel visible={has('result')} />
        </div>

        {/* Right sidebar — activity details */}
        <ActivityDetailsSidebar stage={stage} reached={reached} running={running} elapsed={elapsed} />
      </div>
    </div>
  );
}
