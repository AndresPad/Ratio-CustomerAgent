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
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const stage: Stage | null = idx >= 0 && idx < STAGES.length ? STAGES[idx] : null;
  const reached = STAGES.slice(0, idx + 1);

  const clear = useCallback(() => { timers.current.forEach(clearTimeout); timers.current = []; }, []);

  const start = useCallback(() => {
    clear();
    setIdx(-1);
    setTraceCount(0);
    setRunning(true);

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
    timers.current.push(setTimeout(() => setRunning(false), t));
  }, [clear]);

  useEffect(() => { start(); return clear; }, [start, clear]);

  return { stage, reached, traceCount, running, start };
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

/** Right sidebar — streaming reasoning trace */
function TraceSidebar({ count }: { count: number }) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [count]);

  return (
    <div style={SIDEBAR}>
      <div style={SIDEBAR_HEADER}>
        <i className="fas fa-terminal" style={{ color: '#4f6bed' }} />
        <span>Reasoning Trace</span>
      </div>
      <div style={TRACE_BODY}>
        {TRACE.slice(0, count).map((l, i) => (
          <div
            key={i}
            style={{ paddingLeft: l.indent ? 16 : 0, marginBottom: 2, animation: 'cha-fade-in .2s ease both' }}
          >
            <span style={{ color: '#8a8faa', marginRight: 4 }}>•</span>
            <span style={{ color: traceColor(l.type), fontWeight: l.type === 'highlight' ? 600 : 400 }}>{l.text}</span>
          </div>
        ))}
        {count < TRACE.length && (
          <div style={{ display: 'flex', gap: 4, padding: '6px 0' }}>
            {[0, 1, 2].map(n => (
              <span
                key={n}
                style={{
                  width: 6, height: 6, borderRadius: '50%', background: '#4f6bed',
                  animation: `cha-pulse 1.2s infinite ease-in-out ${n * 0.15}s`,
                }}
              />
            ))}
          </div>
        )}
        <div ref={endRef} />
      </div>
      {/* Confidence scores in sidebar */}
      {CONFIDENCE.length > 0 && count >= TRACE.filter(l => l.stage === 'scoring').length + TRACE.filter(l => l.stage !== 'scoring' && l.stage !== 'reasoning' && l.stage !== 'result').length && (
        <div style={{ padding: '12px 16px', borderTop: '1px solid #e2e5f1' }}>
          <div style={{ fontSize: 10, fontWeight: 700, color: '#8a8faa', textTransform: 'uppercase', letterSpacing: '.08em', marginBottom: 10 }}>Confidence Scores</div>
          {CONFIDENCE.map(c => {
            const good = c.score >= 50;
            return (
              <div key={c.id} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 6 }}>
                <span style={{ ...HYP_BADGE, fontSize: 9 }}>{c.id}</span>
                <span style={{ flex: 1, fontSize: 11, color: '#5c6370', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.label}</span>
                <span style={{ fontSize: 11, fontWeight: 700, color: good ? '#1a9a4a' : '#d1242f' }}>{c.score}%</span>
              </div>
            );
          })}
        </div>
      )}
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
  boxShadow: '-2px 0 8px rgba(0,0,0,.03)',
};

const SIDEBAR_HEADER: CSSProperties = {
  display: 'flex', alignItems: 'center', gap: 8, padding: '14px 16px',
  borderBottom: '1px solid #e2e5f1', fontSize: 13, fontWeight: 700, color: '#1a1a2e',
};

const TRACE_BODY: CSSProperties = {
  flex: 1, overflowY: 'auto', padding: '14px 16px',
  fontFamily: "'Cascadia Code','Fira Code','Consolas',monospace",
  fontSize: 11.5, lineHeight: 1.7, color: '#5c6370',
};

/* ═══════════════════════════════════════════════════════════════ */
/*  Main page export                                               */
/* ═══════════════════════════════════════════════════════════════ */

export default function ChaInvestigationFlowPage() {
  const { stage, reached, traceCount, running, start } = useFlow();
  const has = (s: Stage) => reached.includes(s);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', background: '#f5f6fa', overflow: 'hidden' }}>
      {/* Stage rail */}
      <StageRail current={stage} reached={reached} />

      {/* Stats strip */}
      <StatStrip reached={reached} />

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

          {/* Result */}
          <ResultPanel visible={has('result')} />
        </div>

        {/* Right sidebar — trace stream */}
        <TraceSidebar count={traceCount} />
      </div>
    </div>
  );
}
