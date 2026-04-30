/**
 * ChaInvestigationFlowPage -- shared types, constants, mock data,
 * styles, and reusable components for the Investigation Reasoning Flow.
 *
 * Both ChaFlowExecPage and any future detail pages import from here.
 */
import {
  useState,
  useEffect,
  useCallback,
  useRef,
  type CSSProperties,
} from 'react';

/* ================================================================
   TYPES
   ================================================================ */

export type InvestigationStage =
  | 'signal'
  | 'symptom'
  | 'hypothesis'
  | 'evidence'
  | 'scoring'
  | 'reasoning'
  | 'result'
  | 'action_plan';

export interface TraceLine {
  stage: InvestigationStage;
  text: string;
  type: 'normal' | 'highlight' | 'success' | 'fail' | 'result';
  icon?: string;
  /** Speaker (e.g. 'narrator', 'reasoner', 'triage_agent'). Optional — used
   *  by the chat-style transcript view to attribute messages to agents. */
  agent?: string;
  /** Tool invoked, when this line represents a tool call. */
  tool?: string;
  /** Was this line produced from a real LLM reply (llm_response_text)?
   *  Chat UI uses this to decide whether to render the message as a
   *  full agent utterance vs a brief structural log line. */
  isLlm?: boolean;
}

export interface ConfidenceScore {
  id: string;
  label: string;
  score: number;
  badgeColor: string;
}

export interface Hypothesis {
  id: string;
  description: string;
  score: number;
  status: 'supported' | 'refuted' | 'uncertain';
  badgeColor: string;
}

export interface EvidenceItem {
  title: string;
  detail: string;
  status: 'success' | 'neutral' | 'failure';
}

export interface Symptom {
  title: string;
  hypotheses: Hypothesis[];
  evidence: EvidenceItem[];
}

export interface RootCause {
  title: string;
  description: string;
  confidence: number;
  summary: string;
}

export interface NodeCounts {
  signal: number;
  symptom: number;
  hypothesis: number;
  evidence: number;
  scoring: number;
  reasoning: number;
  result: string;
  action_plan: number | string;
}

/* ================================================================
   CONSTANTS- neeraj
   ================================================================ */

export const INVESTIGATION_STAGES: InvestigationStage[] = [
  'signal', 'symptom', 'hypothesis', 'evidence', 'scoring', 'reasoning', 'result', 'action_plan',
];

export const STAGE_DISPLAY: Record<InvestigationStage, string> = {
  signal: 'Signal',
  symptom: 'Symptom',
  hypothesis: 'Hypothesis',
  evidence: 'Evidence Coll.',
  scoring: 'Confidence Sc.',
  reasoning: 'Reasoning',
  result: 'Result',
  action_plan: 'Action Plan',
};

export const STAGE_ICON: Record<InvestigationStage, string> = {
  signal: 'fa-bolt',
  symptom: 'fa-stethoscope',
  hypothesis: 'fa-lightbulb',
  evidence: 'fa-search',
  scoring: 'fa-chart-bar',
  reasoning: 'fa-brain',
  result: 'fa-check-circle',
  action_plan: 'fa-list-check',
};

export const STAGE_COLOR: Record<InvestigationStage, string> = {
  signal: '#00bfa5',
  symptom: '#ff6b6b',
  hypothesis: '#ffd93d',
  evidence: '#6bcb77',
  scoring: '#4d96ff',
  reasoning: '#845ec2',
  result: '#00bfa5',
  action_plan: '#ff9a76',
};

const EDGE_LABELS: Record<string, string> = {
  signal: '1 signal',
  symptom: '3 symptoms',
  hypothesis: '3 hypotheses',
  evidence: 'evidence item',
  scoring: '3 scored',
  reasoning: 'root cause',
  result: 'remediation',
};

export const STAGE_DURATION: Record<InvestigationStage, number> = {
  signal: 1200,
  symptom: 2000,
  hypothesis: 2000,
  evidence: 2500,
  scoring: 1500,
  reasoning: 3000,
  result: 1000,
  action_plan: 1500,
};

/* ================================================================
   MOCK DATA
   ================================================================ */

export const MOCK_SIGNAL = {
  title: 'BlackRock, Inc -- ScaleSet Platform and Solution',
  status: 'Resolved',
};

export const MOCK_HYPOTHESES: Hypothesis[] = [
  { id: 'HYP-DEP-001', description: "Dependency service 'Xstore' is degraded in region 'westeurope' where the primary customer has resources.", score: 89, status: 'supported', badgeColor: '#e74c3c' },
  { id: 'HYP-OUT-002', description: "Incident '784501920' by 'WACAP' is compounding a pre-existing issue -- the customer has multiple active incidents.", score: 69, status: 'supported', badgeColor: '#e67e22' },
  { id: 'HYP-OUT-005', description: "Incident '784501920' (Severity 2, Status: ACTIVE) by 'WACAP' is NOT impacting the customer's primary workloads.", score: 67, status: 'supported', badgeColor: '#e67e22' },
  { id: 'HYP-SLI-001', description: "The SLI breach on 'Virtual Machine Scale Set.Reliability for Delete VMSS.Signal' for subscription is caused by infrastructure degradation.", score: 60, status: 'supported', badgeColor: '#3498db' },
  { id: 'HYP-OUT-001', description: "Incident '784501920' (Severity 2) by 'WACAP' is directly causing SLI degradation for the customer.", score: 54, status: 'supported', badgeColor: '#e67e22' },
];

export const MOCK_ROOT_CAUSE: RootCause = {
  title: 'Root Cause Identified',
  description: "Dependency service 'Xstore' is degraded in region 'westeurope' where the primary customer has resources. SLI breaches on dependency services (Allocator, CIS, Regional Network Manager, Xstore, etc.) in customer regions indicate resource exhaustion or infrastructure failure that will cascade into customer-facing impact.",
  confidence: 89,
  summary: 'Investigated 7 symptoms -> 4 hypotheses -> 7 evidence items -> 4 actions (346s)',
};

export const MOCK_NODE_COUNTS: NodeCounts = {
  signal: 22,
  symptom: 7,
  hypothesis: 4,
  evidence: 7,
  scoring: 4,
  reasoning: 78,
  result: '89%',
  action_plan: 4,
};

export const MOCK_TRACE: TraceLine[] = [
  // Signal
  { stage: 'signal', text: 'Received IcM signal #784501920 (Sev2) for BlackRock, Inc -- ScaleSet Platform', type: 'normal', icon: '\u{1f535}' },
  { stage: 'signal', text: 'Extracting customer context: subscription=3a7e-..., region=westeurope', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'signal', text: 'Signal classified as ACTIVE outage -- escalating to symptom detection', type: 'highlight', icon: '\u{1f7e1}' },
  // Symptom
  { stage: 'symptom', text: 'Querying Kusto: SLI breaches for subscription in last 4h', type: 'normal', icon: '\u{1f535}' },
  { stage: 'symptom', text: 'Found 7 SLI breaches across Xstore, Allocator, CIS, Regional Network Mgr', type: 'success', icon: '\u{1f7e2}' },
  { stage: 'symptom', text: 'Checking dependency health: Xstore degraded in westeurope (p99 latency 4200ms)', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'symptom', text: 'Correlating active incidents: 2 overlapping outages found in region', type: 'highlight', icon: '\u{1f7e1}' },
  // Hypothesis
  { stage: 'hypothesis', text: 'Generating hypothesis HYP-DEP-001: Xstore degradation cascading to customer VMs', type: 'normal', icon: '\u{1f535}' },
  { stage: 'hypothesis', text: 'Generating hypothesis HYP-OUT-002: Compounding effect from WACAP incident #784501920', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'hypothesis', text: 'Generating hypothesis HYP-SLI-001: Infrastructure degradation causing SLI breach on Delete VMSS', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'hypothesis', text: '4 hypotheses generated -- proceeding to evidence collection', type: 'success', icon: '\u{1f7e2}' },
  // Evidence
  { stage: 'evidence', text: 'Querying Kusto: Xstore availability metrics for westeurope (last 6h)', type: 'normal', icon: '\u{1f535}' },
  { stage: 'evidence', text: 'Retrieved 312 telemetry records -- Xstore success rate dropped to 94.2%', type: 'highlight', icon: '\u{1f7e3}' },
  { stage: 'evidence', text: 'Fetching IcM timeline for incident #784501920: 3 updates, last at 14:32 UTC', type: 'normal', icon: '\u{1f535}' },
  { stage: 'evidence', text: 'Cross-referencing SLI breach windows with dependency outage timestamps', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'evidence', text: 'Collected 7 evidence items across 3 data sources', type: 'success', icon: '\u{1f7e2}' },
  // Scoring
  { stage: 'scoring', text: 'Scoring HYP-DEP-001: strong temporal correlation (r=0.91) with Xstore latency spike', type: 'normal', icon: '\u{1f535}' },
  { stage: 'scoring', text: 'Scoring HYP-OUT-002: moderate overlap -- WACAP incident covers 60% of breach window', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'scoring', text: 'Scoring HYP-SLI-001: weak direct evidence -- SLI breach may be secondary effect', type: 'normal', icon: '\u{1f7e1}' },
  { stage: 'scoring', text: 'Confidence assigned: DEP-001=89%, OUT-002=69%, SLI-001=60%, OUT-001=54%', type: 'success', icon: '\u{1f7e2}' },
  // Reasoning
  { stage: 'reasoning', text: 'Evaluating causal chain: Xstore degradation -> resource exhaustion -> VMSS delete failures', type: 'normal', icon: '\u{1f535}' },
  { stage: 'reasoning', text: 'HYP-DEP-001 selected as primary root cause at 89% confidence', type: 'highlight', icon: '\u{1f7e3}' },
  { stage: 'reasoning', text: 'Ruling out HYP-OUT-001: WACAP incident is correlated but not causal (54%)', type: 'normal', icon: '\u{1f7e1}' },
  { stage: 'reasoning', text: 'Generating recommended actions: escalate Xstore team, apply regional failover', type: 'normal', icon: '\u{1f535}' },
  { stage: 'reasoning', text: 'Building investigation summary with full evidence chain', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'reasoning', text: 'Root cause determination complete -- writing final report', type: 'success', icon: '\u{1f7e2}' },
  // Result
  { stage: 'result', text: 'Investigation complete: 7 symptoms -> 4 hypotheses -> 7 evidence items -> 4 actions (346s)', type: 'result', icon: '\u2705' },
  { stage: 'result', text: 'Root cause: Xstore degradation in westeurope (89% confidence)', type: 'result', icon: '\u2705' },
];

/* ================================================================
   STYLES
   ================================================================ */

const S: Record<string, CSSProperties> = {
  canvas: {
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    gap: 0, padding: '24px 16px 20px', overflowX: 'auto',
  },
  stageNode: {
    display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6,
    minWidth: 100, padding: '14px 10px 12px', borderRadius: 14,
    border: '1.5px solid #e0e0e6', background: '#fff', position: 'relative', transition: 'all .3s',
  },
  stageNodeActive: { border: '2px solid #00bfa5', boxShadow: '0 0 18px rgba(0,191,165,.35)', background: '#f0fffe' },
  stageCircle: {
    width: 44, height: 44, borderRadius: '50%', display: 'flex', alignItems: 'center',
    justifyContent: 'center', fontSize: 18, fontWeight: 700, color: '#fff', transition: 'all .3s',
  },
  stageLabel: { fontSize: 12, fontWeight: 600, color: '#555', marginTop: 2 },
  stageCount: { fontSize: 14, fontWeight: 700 },
  edgeWrap: { display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 2, minWidth: 50, flex: '0 0 auto' },
  edgeLabel: { fontSize: 9, color: '#888', whiteSpace: 'nowrap' },
  edgeLine: { width: 40, height: 0, borderTop: '2px dashed #b0bec5', position: 'relative' },
  edgeCheck: { position: 'absolute', top: -8, left: '50%', transform: 'translateX(-50%)', fontSize: 10, color: '#00c853' },
  completeBadge: { position: 'absolute', top: 4, right: 6, fontSize: 10, color: '#00c853' },

  statusBar: { display: 'flex', alignItems: 'center', padding: '8px 20px', gap: 10, fontSize: 13, borderBottom: '1px solid #eee' },
  statusDot: { width: 8, height: 8, borderRadius: '50%', background: '#00c853', flexShrink: 0 },
  statusAgent: { fontWeight: 600, color: '#00c853' },
  statusText: { color: '#666' },
  statusRight: { marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 },
  completeBadgeStatus: { background: '#e8f5e9', color: '#2e7d32', padding: '2px 10px', borderRadius: 12, fontSize: 11, fontWeight: 600, display: 'flex', alignItems: 'center', gap: 4 },
  elapsed: { fontSize: 12, color: '#999' },

  controlBar: { display: 'flex', alignItems: 'center', padding: '10px 20px', gap: 10, flexWrap: 'wrap', borderBottom: '1px solid #eee' },
  modeBtn: { padding: '5px 14px', fontSize: 12, fontWeight: 600, border: '1px solid #ccc', borderRadius: 6, cursor: 'pointer', background: '#fff', color: '#666', transition: 'all .2s' },
  modeBtnActive: { background: '#00bfa5', color: '#fff', border: '1px solid #00bfa5' },
  xcvInput: { fontFamily: 'monospace', fontSize: 12, padding: '5px 10px', border: '1px solid #ccc', borderRadius: 6, width: 320, color: '#333' },
  loadBtn: { padding: '5px 16px', fontSize: 12, fontWeight: 600, border: 'none', borderRadius: 6, background: '#00bfa5', color: '#fff', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6 },
  eventStats: { fontSize: 11, color: '#999', marginLeft: 4 },
  actionBtn: { padding: '6px 18px', fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, border: '1.5px solid #00bfa5', background: '#fff', color: '#00bfa5', transition: 'all .2s' },
  actionBtnPrimary: { padding: '6px 18px', fontSize: 12, fontWeight: 600, borderRadius: 8, cursor: 'pointer', display: 'flex', alignItems: 'center', gap: 6, border: 'none', background: '#00bfa5', color: '#fff', transition: 'all .2s' },
  spacer: { flex: 1 },

  signalHeader: { display: 'flex', alignItems: 'center', padding: '14px 20px', gap: 10, borderBottom: '1px solid #eee' },
  signalIcon: { fontSize: 20, color: '#ffd93d' },
  signalTitle: { fontSize: 16, fontWeight: 700, color: '#222' },
  resolvedBadge: { marginLeft: 'auto', background: '#e8f5e9', color: '#2e7d32', padding: '3px 14px', borderRadius: 14, fontSize: 12, fontWeight: 600 },

  panelRow: { display: 'flex', gap: 16, padding: '16px 20px', minHeight: 200 },
  reasoningPanel: { flex: 1, background: '#0d1b2a', borderRadius: 10, borderLeft: '4px solid #4d96ff', padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
  reasoningHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderBottom: '1px solid #1a2a3a' },
  reasoningTitle: { fontSize: 13, fontWeight: 600, color: '#e0e0e0', display: 'flex', alignItems: 'center', gap: 6 },
  reasoningBadge: { background: '#1a3a2a', color: '#4caf50', fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 8 },
  reasoningBody: { flex: 1, overflowY: 'auto', padding: '8px 14px', maxHeight: 220 },
  traceLine: { fontSize: 12, fontFamily: "'Cascadia Code', 'Fira Code', monospace", color: '#b0bec5', padding: '2px 0', display: 'flex', gap: 6, alignItems: 'flex-start' },
  traceIcon: { flexShrink: 0, fontSize: 11 },
  traceSuccess: { color: '#66bb6a' },
  traceResult: { color: '#00e676', fontWeight: 600 },
  traceFail: { color: '#ef5350' },
  traceHighlight: { color: '#42a5f5' },

  hypothesisPanel: { flex: 1, background: '#fff', borderRadius: 10, border: '1px solid #e8e8e8', padding: 0, overflow: 'hidden', display: 'flex', flexDirection: 'column' },
  hypHeader: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 14px', borderBottom: '1px solid #eee' },
  hypTitle: { fontSize: 13, fontWeight: 600, color: '#333', display: 'flex', alignItems: 'center', gap: 6 },
  hypCountBadge: { background: '#e3f2fd', color: '#1565c0', fontSize: 10, fontWeight: 600, padding: '2px 8px', borderRadius: 8 },
  hypRow: { display: 'flex', alignItems: 'center', padding: '8px 14px', gap: 8, borderBottom: '1px solid #f5f5f5', fontSize: 12 },
  hypNum: { color: '#999', fontWeight: 600, fontSize: 11, minWidth: 18 },
  hypBadge: { fontSize: 10, fontWeight: 700, padding: '2px 8px', borderRadius: 6, color: '#fff', whiteSpace: 'nowrap', flexShrink: 0 },
  hypDesc: { flex: 1, color: '#444', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  hypBar: { width: 80, height: 4, borderRadius: 2, background: '#e0e0e0', flexShrink: 0, overflow: 'hidden' },
  hypBarFill: { height: '100%', borderRadius: 2 },
  hypPct: { fontWeight: 700, fontSize: 12, minWidth: 34, textAlign: 'right' },
  hypStatus: { fontSize: 9, fontWeight: 700, padding: '2px 8px', borderRadius: 6, background: '#e8f5e9', color: '#2e7d32', whiteSpace: 'nowrap', flexShrink: 0 },
  hypFooter: { padding: '8px 14px', fontSize: 11, color: '#666', display: 'flex', alignItems: 'center', gap: 6 },
  hypFooterDot: { width: 8, height: 8, borderRadius: '50%', background: '#00c853' },

  rootCause: { margin: '0 20px 16px', borderRadius: 10, border: '1px solid #a5d6a7', borderLeft: '5px solid #4caf50', padding: '16px 20px', background: '#fafffe' },
  rootCauseHeader: { display: 'flex', alignItems: 'center', gap: 8, fontSize: 16, fontWeight: 700, color: '#2e7d32', marginBottom: 10 },
  rootCauseDesc: { fontSize: 13, color: '#444', lineHeight: 1.6, marginBottom: 14 },
  confLabel: { fontSize: 10, fontWeight: 700, color: '#888', letterSpacing: 1, marginBottom: 4 },
  confRow: { display: 'flex', alignItems: 'center', gap: 16, marginBottom: 14 },
  confPct: { fontSize: 32, fontWeight: 800, color: '#2e7d32' },
  confBar: { flex: 1, height: 10, borderRadius: 5, background: '#e0e0e0', overflow: 'hidden' },
  confBarFill: { height: '100%', borderRadius: 5, background: '#4caf50' },
  summaryLabel: { fontSize: 10, fontWeight: 700, color: '#888', letterSpacing: 1, marginBottom: 4 },
  summaryBox: { background: '#e8f5e9', borderRadius: 8, padding: '10px 14px', fontSize: 13, fontFamily: "'Cascadia Code', 'Fira Code', monospace", color: '#2e7d32' },
};

/* ================================================================
   useFlow -- auto-play through demo stages
   ================================================================ */

export interface FlowState {
  stage: InvestigationStage | null;
  reached: InvestigationStage[];
  traceCount: number;
  running: boolean;
  elapsed: number;
}

export function useFlow(): FlowState & { start: () => void } {
  const [stageIdx, setStageIdx] = useState(-1);
  const [traceCount, setTraceCount] = useState(0);
  const [running, setRunning] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const t0 = useRef(0);
  const raf = useRef(0);

  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    cancelAnimationFrame(raf.current);
  }, []);

  const start = useCallback(() => {
    clear();
    setStageIdx(-1);
    setTraceCount(0);
    setRunning(true);
    setElapsed(0);
    t0.current = Date.now();

    const tick = () => {
      setElapsed((Date.now() - t0.current) / 1000);
      raf.current = requestAnimationFrame(tick);
    };
    raf.current = requestAnimationFrame(tick);

    let cum = 400;
    INVESTIGATION_STAGES.forEach((stage, i) => {
      timers.current.push(setTimeout(() => setStageIdx(i), cum));
      const lines = MOCK_TRACE.filter((l) => l.stage === stage);
      let d = 200;
      const step = lines.length > 0 ? Math.min(300, STAGE_DURATION[stage] / (lines.length + 1)) : 0;
      lines.forEach(() => {
        timers.current.push(setTimeout(() => setTraceCount((c) => c + 1), cum + d));
        d += step;
      });
      cum += STAGE_DURATION[stage];
    });
    timers.current.push(setTimeout(() => { setRunning(false); cancelAnimationFrame(raf.current); }, cum));
  }, [clear]);

  useEffect(() => { start(); return clear; }, [start, clear]);

  const stage = stageIdx >= 0 ? INVESTIGATION_STAGES[stageIdx] : null;
  const reached = INVESTIGATION_STAGES.slice(0, stageIdx + 1);
  return { stage, reached, traceCount, running, elapsed, start };
}

/* ================================================================
   COMPONENTS
   ================================================================ */

/* -- WorkflowCanvas (linear pipeline) ----------------------------- */

interface CanvasProps {
  reached: InvestigationStage[];
  active: InvestigationStage | null;
  counts?: NodeCounts;
}

export function WorkflowCanvas({ reached, active, counts }: CanvasProps) {
  const nc = counts ?? MOCK_NODE_COUNTS;
  return (
    <div style={S.canvas}>
      {INVESTIGATION_STAGES.map((s, i) => {
        const isReached = reached.includes(s);
        const isActive = s === active;
        const nodeStyle: CSSProperties = {
          ...S.stageNode,
          ...(isActive ? S.stageNodeActive : {}),
          ...(isReached && !isActive ? { borderColor: '#c8e6c9' } : {}),
        };
        const circleStyle: CSSProperties = {
          ...S.stageCircle,
          background: isReached || isActive ? STAGE_COLOR[s] : '#ccc',
        };
        const count = nc[s];
        return (
          <div key={s} style={{ display: 'flex', alignItems: 'center' }}>
            <div style={nodeStyle}>
              {isReached && !isActive && <i className="fas fa-check" style={S.completeBadge as CSSProperties} />}
              <div style={circleStyle}>
                {isReached || isActive
                  ? <i className={`fas ${STAGE_ICON[s]}`} />
                  : <i className={`fas ${STAGE_ICON[s]}`} style={{ opacity: 0.5 }} />}
              </div>
              <span style={S.stageLabel}>{STAGE_DISPLAY[s]}</span>
              <span style={{ ...S.stageCount, color: isReached || isActive ? STAGE_COLOR[s] : '#bbb' }}>
                {count}
              </span>
            </div>
            {i < INVESTIGATION_STAGES.length - 1 && (
              <div style={S.edgeWrap as CSSProperties}>
                <span style={S.edgeLabel as CSSProperties}>{EDGE_LABELS[s] ?? ''}</span>
                <div style={S.edgeLine as CSSProperties}>
                  {isReached && <i className="fas fa-check" style={S.edgeCheck as CSSProperties} />}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* -- StatusBar ---------------------------------------------------- */

interface StatusProps {
  agentName?: string;
  statusText?: string;
  complete?: boolean;
  elapsed?: number;
}

export function StatusBar({ agentName = 'Summary Writer', statusText = 'Investigation complete', complete, elapsed }: StatusProps) {
  return (
    <div style={S.statusBar}>
      <div style={S.statusDot} />
      <span style={S.statusAgent}>{agentName}</span>
      <span style={S.statusText}>{statusText}</span>
      <div style={S.statusRight as CSSProperties}>
        {complete && (
          <span style={S.completeBadgeStatus}>
            <i className="fas fa-cog" style={{ fontSize: 10 }} /> COMPLETE
          </span>
        )}
        {elapsed != null && (
          <span style={S.elapsed}>{'\u23f1'} {elapsed.toFixed(1)}s</span>
        )}
      </div>
    </div>
  );
}

/* -- SignalHeader -------------------------------------------------- */

interface SignalProps {
  title?: string;
  status?: string;
}

export function SignalHeader({ title = MOCK_SIGNAL.title, status = MOCK_SIGNAL.status }: SignalProps) {
  return (
    <div style={S.signalHeader}>
      <i className="fas fa-bolt" style={S.signalIcon} />
      <span style={S.signalTitle}>{title}</span>
      <span style={S.resolvedBadge}>{status}</span>
    </div>
  );
}

/* -- ReasoningPanel ----------------------------------------------- */

interface ReasoningProps {
  traceLines?: TraceLine[];
  visibleCount?: number;
  complete?: boolean;
}

export function ReasoningPanel({ traceLines, visibleCount, complete }: ReasoningProps) {
  const lines = traceLines ?? MOCK_TRACE;
  const count = visibleCount ?? lines.length;
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' }); }, [count]);

  const colorFor = (t: TraceLine['type']): CSSProperties => {
    switch (t) {
      case 'success': return S.traceSuccess;
      case 'result': return S.traceResult;
      case 'fail': return S.traceFail;
      case 'highlight': return S.traceHighlight;
      default: return {};
    }
  };

  return (
    <div style={S.reasoningPanel as CSSProperties}>
      <div style={S.reasoningHeader}>
        <span style={S.reasoningTitle}>
          <i className="fas fa-brain" /> Agent Reasoning
        </span>
        {complete && <span style={S.reasoningBadge}>Complete</span>}
      </div>
      <div style={S.reasoningBody as CSSProperties}>
        {lines.slice(0, count).map((ln, i) => (
          <div key={i} style={S.traceLine}>
            <span style={S.traceIcon}>{ln.icon ?? '\u2022'}</span>
            <span style={colorFor(ln.type)}>{ln.text}</span>
          </div>
        ))}
        <div ref={endRef} />
      </div>
    </div>
  );
}

/* -- HypothesisPanel ---------------------------------------------- */

interface HypPanelProps {
  hypotheses?: Hypothesis[];
}

export function HypothesisPanel({ hypotheses }: HypPanelProps) {
  const hyps = hypotheses ?? MOCK_HYPOTHESES;
  const winner = hyps.length > 0 ? hyps[0] : null;
  return (
    <div style={S.hypothesisPanel as CSSProperties}>
      <div style={S.hypHeader}>
        <span style={S.hypTitle}>
          <i className="fas fa-gavel" /> Hypothesis Verdict
        </span>
        <span style={S.hypCountBadge}>{hyps.length} evaluated</span>
      </div>
      <div style={{ flex: 1, overflowY: 'auto' }}>
        {hyps.map((h, i) => {
          const barColor = h.score >= 70 ? '#4caf50' : h.score >= 50 ? '#ff9800' : '#e0e0e0';
          return (
            <div key={h.id} style={S.hypRow}>
              <span style={S.hypNum}>#{i + 1}</span>
              <span style={{ ...S.hypBadge, background: h.badgeColor }}>{h.id}</span>
              <span style={S.hypDesc as CSSProperties}>{h.description}</span>
              <div style={S.hypBar}>
                <div style={{ ...S.hypBarFill, width: `${h.score}%`, background: barColor }} />
              </div>
              <span style={{ ...S.hypPct, color: barColor } as CSSProperties}>{h.score}%</span>
              <span style={S.hypStatus as CSSProperties}>
                {h.status === 'supported' ? 'SUPPORTED' : h.status === 'refuted' ? 'REFUTED' : 'UNCERTAIN'}
              </span>
            </div>
          );
        })}
      </div>
      {winner && (
        <div style={S.hypFooter}>
          <div style={S.hypFooterDot} />
          <span>{winner.id} selected at {winner.score}% -- {hyps.length - 1} ruled out.</span>
        </div>
      )}
    </div>
  );
}

/* -- RootCauseSection --------------------------------------------- */

interface RootCauseProps {
  rootCause?: RootCause;
  visible?: boolean;
}

export function RootCauseSection({ rootCause, visible = true }: RootCauseProps) {
  if (!visible) return null;
  const rc = rootCause ?? MOCK_ROOT_CAUSE;
  return (
    <div style={S.rootCause}>
      <div style={S.rootCauseHeader}>
        <i className="fas fa-check-circle" /> {rc.title}
      </div>
      <div style={S.rootCauseDesc}>{rc.description}</div>
      <div style={S.confLabel}>FINAL CONFIDENCE</div>
      <div style={S.confRow}>
        <span style={S.confPct}>{rc.confidence}%</span>
        <div style={S.confBar}>
          <div style={{ ...S.confBarFill, width: `${rc.confidence}%` }} />
        </div>
      </div>
      <div style={S.summaryLabel}>SUMMARY</div>
      <div style={S.summaryBox}>{rc.summary}</div>
    </div>
  );
}

/* ================================================================
   N8N-STYLE INTERACTIVE WORKFLOW GRAPH
   ================================================================ */

interface NodePosition { x: number; y: number; }

const NODE_W = 180;
const NODE_H = 90;
const NODE_GAP_X = 60;
const GRAPH_PAD = 40;

function defaultPositions(): Record<InvestigationStage, NodePosition> {
  const positions: Record<string, NodePosition> = {};
  INVESTIGATION_STAGES.forEach((s, i) => {
    const yOffset = i % 2 === 0 ? 0 : 50;
    positions[s] = { x: GRAPH_PAD + i * (NODE_W + NODE_GAP_X), y: GRAPH_PAD + 40 + yOffset };
  });
  return positions as Record<InvestigationStage, NodePosition>;
}

const N8N_EDGE_LABELS: Record<string, string> = {
  'signal->symptom': '1 signal',
  'symptom->hypothesis': '3 symptoms',
  'hypothesis->evidence': '3 hypotheses',
  'evidence->scoring': 'evidence items',
  'scoring->reasoning': '3 scored',
  'reasoning->result': 'root cause',
  'result->action_plan': 'remediation',
};

interface N8nGraphProps {
  reached: InvestigationStage[];
  active: InvestigationStage | null;
  counts?: NodeCounts;
  onNodeClick?: (stage: InvestigationStage) => void;
}

export function N8nWorkflowGraph({ reached, active, counts, onNodeClick }: N8nGraphProps) {
  const nc = counts ?? MOCK_NODE_COUNTS;
  const [positions, setPositions] = useState<Record<InvestigationStage, NodePosition>>(defaultPositions);
  const [dragging, setDragging] = useState<InvestigationStage | null>(null);
  const [dragOffset, setDragOffset] = useState({ x: 0, y: 0 });
  const svgRef = useRef<SVGSVGElement>(null);

  const allX = INVESTIGATION_STAGES.map(s => positions[s].x);
  const allY = INVESTIGATION_STAGES.map(s => positions[s].y);
  const svgW = Math.max(...allX) + NODE_W + GRAPH_PAD * 2;
  const svgH = Math.max(...allY) + NODE_H + GRAPH_PAD * 2;

  const onMouseDown = useCallback((stage: InvestigationStage, e: React.MouseEvent) => {
    e.preventDefault();
    const svgEl = svgRef.current;
    if (!svgEl) return;
    const rect = svgEl.getBoundingClientRect();
    const scale = svgW / rect.width;
    setDragging(stage);
    setDragOffset({
      x: (e.clientX - rect.left) * scale - positions[stage].x,
      y: (e.clientY - rect.top) * scale - positions[stage].y,
    });
  }, [positions, svgW]);

  const onMouseMove = useCallback((e: React.MouseEvent) => {
    if (!dragging || !svgRef.current) return;
    const rect = svgRef.current.getBoundingClientRect();
    const scale = svgW / rect.width;
    const newX = Math.max(0, (e.clientX - rect.left) * scale - dragOffset.x);
    const newY = Math.max(0, (e.clientY - rect.top) * scale - dragOffset.y);
    setPositions(prev => ({ ...prev, [dragging]: { x: newX, y: newY } }));
  }, [dragging, dragOffset, svgW]);

  const onMouseUp = useCallback(() => setDragging(null), []);

  const edgePath = (from: InvestigationStage, to: InvestigationStage): string => {
    const p1 = positions[from]; const p2 = positions[to];
    const x1 = p1.x + NODE_W; const y1 = p1.y + NODE_H / 2;
    const x2 = p2.x; const y2 = p2.y + NODE_H / 2;
    const cx = (x1 + x2) / 2;
    return `M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`;
  };

  const edgeMid = (from: InvestigationStage, to: InvestigationStage) => {
    const p1 = positions[from]; const p2 = positions[to];
    return { x: (p1.x + NODE_W + p2.x) / 2, y: (p1.y + NODE_H / 2 + p2.y + NODE_H / 2) / 2 - 10 };
  };

  const faGlyph: Record<InvestigationStage, string> = {
    signal: '\uf0e7', symptom: '\uf0f1', hypothesis: '\uf0eb',
    evidence: '\uf002', scoring: '\uf080', reasoning: '\uf5dc', result: '\uf058',
    action_plan: '\uf0ae',
  };

  return (
    <div style={{ background: '#ffffff', borderRadius: 12, margin: '16px 20px', overflow: 'hidden', border: '1px solid #e0e0e6' }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 16px', borderBottom: '1px solid #e8e8ee', background: '#f8f9fb' }}>
        <span style={{ fontSize: 12, fontWeight: 600, color: '#555', display: 'flex', alignItems: 'center', gap: 8 }}>
          <i className="fas fa-project-diagram" style={{ color: '#00bfa5' }} />
          Investigation Workflow Graph
          <span style={{ fontSize: 9, background: '#eef0f4', color: '#888', padding: '2px 8px', borderRadius: 8 }}>drag nodes to reposition</span>
        </span>
        <button onClick={() => setPositions(defaultPositions())} style={{ background: 'none', border: '1px solid #ccc', color: '#888', fontSize: 10, padding: '3px 10px', borderRadius: 6, cursor: 'pointer' }}>
          <i className="fas fa-undo" /> Reset Layout
        </button>
      </div>

      {/* SVG */}
      <svg ref={svgRef} viewBox={`0 0 ${svgW} ${svgH}`}
        style={{ width: '100%', height: Math.max(260, svgH), cursor: dragging ? 'grabbing' : 'default', userSelect: 'none' }}
        onMouseMove={onMouseMove} onMouseUp={onMouseUp} onMouseLeave={onMouseUp}>
        <defs>
          <filter id="n8n-glow" x="-50%" y="-50%" width="200%" height="200%">
            <feGaussianBlur stdDeviation="6" result="blur" />
            <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
          </filter>
          <marker id="n8n-arrow" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 Z" fill="#b0bec5" />
          </marker>
          <marker id="n8n-arrow-active" viewBox="0 0 10 10" refX="10" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">
            <path d="M 0 0 L 10 5 L 0 10 Z" fill="#00bfa5" />
          </marker>
        </defs>

        {/* Grid dots */}
        {Array.from({ length: Math.ceil(svgW / 30) * Math.ceil(svgH / 30) }).map((_, i) => {
          const col = i % Math.ceil(svgW / 30);
          const row = Math.floor(i / Math.ceil(svgW / 30));
          return <circle key={i} cx={col * 30 + 15} cy={row * 30 + 15} r={0.8} fill="#dde0e6" />;
        })}

        {/* Edges */}
        {INVESTIGATION_STAGES.slice(0, -1).map((s, i) => {
          const next = INVESTIGATION_STAGES[i + 1];
          const isReached = reached.includes(s) && reached.includes(next);
          const edgeKey = `${s}->${next}`;
          const mid = edgeMid(s, next);
          return (
            <g key={edgeKey}>
              <path d={edgePath(s, next)} fill="none"
                stroke={isReached ? '#00bfa5' : '#b0bec5'}
                strokeWidth={isReached ? 2.5 : 1.5}
                strokeDasharray={isReached ? 'none' : '6,4'}
                markerEnd={isReached ? 'url(#n8n-arrow-active)' : 'url(#n8n-arrow)'}
                style={{ transition: 'stroke 0.4s, stroke-width 0.4s' }}
              />
              {N8N_EDGE_LABELS[edgeKey] && (
                <text x={mid.x} y={mid.y} textAnchor="middle" fontSize={9} fill={isReached ? '#00897b' : '#999'} fontWeight={500}>
                  {N8N_EDGE_LABELS[edgeKey]}
                </text>
              )}
              {isReached && <text x={mid.x} y={mid.y + 14} textAnchor="middle" fontSize={10} fill="#00c853">{'\u2713'}</text>}
            </g>
          );
        })}

        {/* Nodes */}
        {INVESTIGATION_STAGES.map((s) => {
          const pos = positions[s];
          const isReached = reached.includes(s);
          const isActive = s === active;
          const color = STAGE_COLOR[s];
          const count = nc[s];
          return (
            <g key={s} transform={`translate(${pos.x}, ${pos.y})`}
              onMouseDown={(e) => onMouseDown(s, e)}
              onClick={() => onNodeClick?.(s)}
              style={{ cursor: dragging === s ? 'grabbing' : 'grab' }}
              filter={isActive ? 'url(#n8n-glow)' : undefined}>
              {/* Body */}
              <rect width={NODE_W} height={NODE_H} rx={12} ry={12}
                fill={isActive ? '#f0fffe' : '#fff'}
                stroke={isActive ? color : isReached ? '#a5d6a7' : '#e0e0e6'}
                strokeWidth={isActive ? 2.5 : 1.5} style={{ transition: 'all 0.3s' }} />
              {/* Left accent */}
              <rect x={0} y={0} width={4} height={NODE_H} rx={2}
                fill={isReached || isActive ? color : '#ccc'} style={{ transition: 'fill 0.3s' }} />
              {/* Icon circle */}
              <circle cx={32} cy={NODE_H / 2} r={16}
                fill={isReached || isActive ? color : '#e0e0e0'}
                opacity={isReached || isActive ? 1 : 0.5} style={{ transition: 'all 0.3s' }} />
              <text x={32} y={NODE_H / 2 + 1} textAnchor="middle" dominantBaseline="central"
                fontSize={12} fill="#fff" fontFamily="'Font Awesome 6 Free'" fontWeight={900}>
                {faGlyph[s]}
              </text>
              {/* Label */}
              <text x={58} y={30} fontSize={12} fontWeight={600}
                fill={isActive ? '#00796b' : isReached ? '#333' : '#999'} style={{ transition: 'fill 0.3s' }}>
                {STAGE_DISPLAY[s]}
              </text>
              {/* Count */}
              <text x={58} y={52} fontSize={18} fontWeight={800}
                fill={isReached || isActive ? color : '#bbb'} style={{ transition: 'fill 0.3s' }}>
                {count}
              </text>
              {/* Reached badge */}
              {isReached && !isActive && (
                <g><circle cx={NODE_W - 14} cy={14} r={8} fill="#e8f5e9" />
                  <text x={NODE_W - 14} y={15} textAnchor="middle" dominantBaseline="central" fontSize={9} fill="#00c853">{'\u2713'}</text></g>
              )}
              {/* Active pulse */}
              {isActive && (
                <g>
                  <circle cx={NODE_W - 14} cy={14} r={8} fill={color} opacity={0.3}>
                    <animate attributeName="opacity" values="0.3;0.8;0.3" dur="1.5s" repeatCount="indefinite" />
                  </circle>
                  <circle cx={NODE_W - 14} cy={14} r={4} fill={color}>
                    <animate attributeName="r" values="3;5;3" dur="1.5s" repeatCount="indefinite" />
                  </circle>
                </g>
              )}
              {/* Ports */}
              <circle cx={0} cy={NODE_H / 2} r={4} fill={isReached ? '#00bfa5' : '#ccc'} stroke="#fff" strokeWidth={1.5} />
              <circle cx={NODE_W} cy={NODE_H / 2} r={4} fill={isReached ? '#00bfa5' : '#ccc'} stroke="#fff" strokeWidth={1.5} />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

/* -- Activity bar styles (exported for pages) --------------------- */

export const ACTIVITY_BAR = S;

/* -- Page default export (standalone) ----------------------------- */

export default function ChaInvestigationFlowPage() {
  const { stage, reached, traceCount, running, elapsed } = useFlow();
  const complete = reached.length === INVESTIGATION_STAGES.length && !running;

  return (
    <div style={{ height: '100%', overflowY: 'auto', background: '#fafafa' }}>
      <WorkflowCanvas reached={reached} active={stage} />
      <StatusBar complete={complete} elapsed={elapsed} />
      <SignalHeader />
      <div style={S.panelRow}>
        <ReasoningPanel visibleCount={traceCount} complete={complete} />
        <HypothesisPanel />
      </div>
      <RootCauseSection visible={complete} />
    </div>
  );
}