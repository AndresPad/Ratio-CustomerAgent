/**
 * ChaLivePage — "Live Orchestration" view.
 *
 * Consumes the real Customer Agent pipeline SSE stream
 * (`POST /cha-live-api/api/run`) and visualises the seven executive-visible
 * stages in motion:
 *
 *   Signal → Evaluation → Hypothesis → Scoring → Selection → Tool Execution → Summary
 *
 * The page deliberately follows Manik's design principle — each of those
 * seven events is treated as a first-class milestone, while everything
 * in between (tool invocations, speaker selections, intermediate LLM
 * chatter) becomes "progress" that flows into an animated activity feed
 * and progress bars.
 */
import { useMemo, useRef, useEffect, useState, Fragment, type CSSProperties } from 'react';
import {
  useLiveInvestigation,
  STAGES,
  STAGE_LABELS,
  STAGE_ICONS,
  type HypothesisRow,
  type ToolCallRow,
  type SignalTypeRow,
  type CompoundRow,
  type Stage,
} from '../../hooks/useLiveInvestigation';
import type { LiveEvent } from '../../api/liveOrchestrationClient';

/* ─────────────────────────────────────────────────────────────── */
/* Small presentational building blocks                            */
/* ─────────────────────────────────────────────────────────────── */

function PipelineBar({
  stage,
  stagesReached,
  stageProgress,
}: {
  stage: Stage;
  stagesReached: Stage[];
  stageProgress: number;
}) {
  return (
    <div className="cha-pipeline" role="progressbar" aria-valuenow={stageProgress} aria-valuemin={0} aria-valuemax={100}>
      {STAGES.map((s, i) => {
        const isActive = s === stage;
        const isDone = stagesReached.includes(s) && !isActive;
        const fill = isDone ? 100 : isActive ? stageProgress : 0;
        return (
          <Fragment key={s}>
            <div
              className={`cha-pipeline-stage${isActive ? ' active' : ''}${isDone ? ' done' : ''}`}
              title={STAGE_LABELS[s]}
            >
              <div className="icon">
                <i className={`fas ${STAGE_ICONS[s]}`} />
              </div>
              <div className="label">{STAGE_LABELS[s]}</div>
              <div className="progress-track">
                <div className="progress-fill" style={{ width: `${fill}%` }} />
              </div>
            </div>
            {i < STAGES.length - 1 && (
              <div className="cha-pipeline-arrow">
                <i className="fas fa-chevron-right" />
              </div>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  accent,
}: {
  label: string;
  value: number | string;
  sub?: string;
  accent?: string;
}) {
  return (
    <div className="cha-metric" style={accent ? ({ ['--accent' as string]: accent } as CSSProperties) : undefined}>
      <div className="label">{label}</div>
      <div className="value">{value}</div>
      {sub && <div className="sub">{sub}</div>}
    </div>
  );
}

function SignalTypeChip({ row }: { row: SignalTypeRow }) {
  const pct = Math.min(100, Math.round(row.max_strength * 100));
  const activated = row.activated_count > 0;
  return (
    <span className={`cha-sig-chip${activated ? ' activated' : ''}`} title={`${row.signal_name} (${row.row_count} rows, ${row.activated_count} activated)`}>
      <strong style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10 }}>{row.signal_type_id}</strong>
      <span>{row.signal_name}</span>
      <span className="bar"><span style={{ width: `${pct}%` }} /></span>
      <span style={{ color: activated ? '#b45700' : '#6a78a6', fontWeight: 600 }}>{pct}%</span>
    </span>
  );
}

function CompoundChip({ row }: { row: CompoundRow }) {
  const pct = Math.min(100, Math.round(row.strength * 100));
  return (
    <span className={`cha-sig-chip${row.activated ? ' activated' : ''}`} title={row.rationale}>
      <strong style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10 }}>{row.compound_id}</strong>
      <span>{row.compound_name}</span>
      <span className="bar"><span style={{ width: `${pct}%` }} /></span>
      <span style={{ fontWeight: 600 }}>{row.confidence || `${pct}%`}</span>
    </span>
  );
}

function HypothesisCard({ h }: { h: HypothesisRow }) {
  const status = (h.status || 'ACTIVE').toUpperCase();
  const isSupported = /SUPPORT|CONFIRMED|CONTRIBUT/.test(status);
  const isRefuted = /REFUT|UNSUPPORT/.test(status);
  const confPct = Math.round((h.confidence || 0) * 100);
  const scorePct = Math.round((h.match_score || 0) * 100);
  const scoreColor = scorePct >= 70 ? '#28a745' : scorePct >= 40 ? '#f0ad4e' : '#8a8aaa';
  return (
    <div
      className={`cha-hyp-card${h.selected ? ' selected' : ''}${isSupported ? ' supported' : ''}${isRefuted ? ' refuted' : ''}`}
    >
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 6 }}>
        <span className="hid">{h.hypothesis_id}</span>
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          {h.rank > 0 && <span className="rank-badge">Rank #{h.rank}</span>}
          <span
            className={`verdict-chip verdict-${
              isSupported ? 'supported' : isRefuted ? 'refuted' : 'active'
            }`}
          >
            {status}
          </span>
        </div>
      </div>
      <div className="statement">{h.statement || '(statement pending)'}</div>
      <div className="score-row">
        <span style={{ minWidth: 72, fontWeight: 600 }}>Score {scorePct}%</span>
        <div className="score-bar">
          <div className="score-fill" style={{ width: `${scorePct}%`, background: scoreColor }} />
        </div>
      </div>
      {(h.confidence > 0 || h.selected) && (
        <div className="conf-row">
          <span style={{ minWidth: 72, fontWeight: 600, color: isSupported ? '#28a745' : isRefuted ? '#dc3545' : 'var(--cha-primary)' }}>
            Confidence {confPct}%
          </span>
          <div className="score-bar">
            <div
              className="score-fill"
              style={{
                width: `${confPct}%`,
                background: isSupported ? '#28a745' : isRefuted ? '#dc3545' : 'var(--cha-primary)',
              }}
            />
          </div>
        </div>
      )}
      {h.matched_symptoms && (
        <div style={{ fontSize: 10, color: 'var(--cha-text-muted)', marginTop: 4 }}>
          <i className="fas fa-stethoscope" />&nbsp;{h.matched_symptoms}
        </div>
      )}
    </div>
  );
}

function ToolCallList({ calls }: { calls: ToolCallRow[] }) {
  if (calls.length === 0) {
    return <div className="cha-live-empty">No tool calls yet…</div>;
  }
  // Most recent 40 first.
  const shown = calls.slice(-40).reverse();
  return (
    <div>
      {shown.map((c, i) => (
        <div key={`${c.ts}-${i}`} className={`cha-tool-row${c.error ? ' err' : ''}`}>
          <span className="t-agent">{c.agent || 'tool'}</span>
          <span className="t-tool" title={c.parameters || c.query}>
            <i className="fas fa-terminal" style={{ marginRight: 4, color: 'var(--cha-text-muted)' }} />
            {c.tool}
            {c.query && <span style={{ color: 'var(--cha-text-muted)' }}> — {c.query.slice(0, 80)}</span>}
          </span>
          <span className="t-meta">
            {c.row_count > 0 && <>{c.row_count} rows&nbsp;·&nbsp;</>}
            {c.duration_ms > 0 && <>{c.duration_ms.toFixed(0)}ms</>}
            {c.error && <span style={{ color: '#dc3545' }}>&nbsp;·&nbsp;error</span>}
          </span>
        </div>
      ))}
    </div>
  );
}

function feedCategory(evt: LiveEvent): string {
  const k = evt.kind;
  if (/Signal(Type)?Evaluated?|SignalEvaluationStart|MCPCollectionCall|pipeline_started/.test(k)) return 'signal';
  if (/Compound|SignalDecision|signal_evaluation_complete/.test(k)) return 'evaluation';
  if (/HypothesisScoring/.test(k)) return 'scoring';
  if (/HypothesisSelected|hypothesis_evaluation_started/.test(k)) return 'selection';
  if (/Hypothesis(Transition)?|InvestigationCreated|WorkflowStarted|investigations?_started|investigations?_starting/.test(k)) return 'hypothesis';
  if (/ToolCall|EvidenceCycle/.test(k)) return 'tool';
  if (/PhaseTransition/.test(k)) return 'phase';
  if (/SpeakerSelected/.test(k)) return 'speaker';
  if (/AgentResponse|investigation_agent_response/.test(k)) return 'agent';
  if (/InvestigationComplete|pipeline_complete/.test(k)) return 'summary';
  if (/error/i.test(k)) return 'error';
  return '';
}

function summarizeEvent(evt: LiveEvent): { title: string; body?: string } {
  const p = (evt.Properties ?? {}) as Record<string, unknown>;
  const pick = (k: string): unknown => evt[k] ?? p[k];
  switch (evt.kind) {
    case 'pipeline_started':
      return { title: 'Pipeline started', body: `xcv=${String(evt.xcv || '').slice(0, 8)}…` };
    case 'SignalEvaluationStart':
      return { title: 'Signal evaluation started', body: `${pick('CustomerName')} · ${pick('ServiceTreeId')}` };
    case 'SignalTypeEvaluated':
      return { title: `Evaluated ${pick('SignalName')}`, body: `${pick('ActivatedCount')} activated / ${pick('RowCount')} rows · strength ${Number(pick('MaxStrength') ?? 0).toFixed(2)}` };
    case 'CompoundEvaluated':
      return { title: `Compound ${pick('CompoundName')}`, body: `${pick('Activated') ? 'ACTIVATED' : 'not activated'} · ${pick('Rationale')}` };
    case 'SignalDecision':
      return { title: `Signal decision: ${pick('Action')}`, body: `${pick('SignalCount')} signals · ${pick('CompoundCount')} compounds` };
    case 'signal_evaluation_complete':
      return { title: 'Signal evaluation complete' };
    case 'investigations_starting':
      return { title: `Starting ${evt.count ?? '?'} investigation(s)` };
    case 'investigation_started':
    case 'InvestigationCreated':
      return { title: `Investigation created`, body: `${evt.investigation_id ?? pick('InvestigationId')}` };
    case 'WorkflowStarted':
      return { title: 'Investigation workflow started', body: String(pick('Participants') ?? '') };
    case 'PhaseTransition':
      return { title: `Phase: ${pick('FromPhase')} → ${pick('ToPhase')}`, body: `agent=${pick('Agent') ?? ''}` };
    case 'HypothesisScoring':
      return {
        title: 'Hypothesis scoring',
        body: `${pick('InputSymptomCount')} symptoms → ${pick('OutputHypothesisCount')} hypotheses · top=${pick('TopHypothesisId')} (${Number(pick('TopScore') ?? 0).toFixed(2)})`,
      };
    case 'HypothesisSelected':
      return { title: `Hypothesis selected: ${pick('HypothesisId')}`, body: `rank ${pick('Rank')}/${pick('TotalHypotheses')} · score ${Number(pick('MatchScore') ?? 0).toFixed(2)}` };
    case 'hypothesis_evaluation_started':
      return { title: `Evaluating ${evt.hypothesis_id}`, body: String(evt.statement ?? '').slice(0, 120) };
    case 'HypothesisTransition':
      return { title: `${pick('HypothesisId')}: ${pick('OldStatus')} → ${pick('NewStatus')}`, body: `confidence ${Number(pick('Confidence') ?? 0).toFixed(2)}` };
    case 'SpeakerSelected':
      return { title: `Next speaker: ${pick('NextSpeaker')}`, body: `reason: ${String(pick('Reason') ?? '').slice(0, 120)}` };
    case 'EvidenceCycle':
      return { title: `Evidence cycle ${pick('CycleNumber')}`, body: `ERs: ${pick('ERIds')}` };
    case 'ToolCall':
    case 'MCPCollectionCall':
      return {
        title: `Tool: ${pick('Tool')}`,
        body: `${pick('Parameters') ?? pick('Arguments') ?? ''}${pick('RowCount') ? ` · ${pick('RowCount')} rows` : ''}`,
      };
    case 'AgentResponse':
    case 'investigation_agent_response':
      return { title: `${evt.agent ?? pick('AgentName')} responded`, body: String(evt.text ?? pick('ResponseText') ?? '').slice(0, 160) };
    case 'InvestigationComplete':
      return {
        title: 'Investigation complete',
        body: `symptoms=${pick('SymptomsCount')} · hypotheses=${pick('HypothesesCount')} · evidence=${pick('EvidenceCount')} · actions=${pick('ActionsCount')} · ${pick('DurationSeconds')}s`,
      };
    case 'pipeline_complete':
      return { title: 'Pipeline complete', body: `${evt.investigation_count ?? 0} investigation(s)` };
    case 'pipeline_error':
    case 'investigation_error':
    case 'investigation_workflow_error':
      return { title: 'Error', body: String(evt.error ?? '') };
    default:
      return { title: evt.kind };
  }
}

function ActivityFeed({ events }: { events: LiveEvent[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    ref.current?.scrollTo({ top: ref.current.scrollHeight, behavior: 'smooth' });
  }, [events.length]);
  // Show the last ~80 meaningful events only.
  const HIDE = new Set(['LLMCall', 'investigation_agent_chunk', 'investigation_stall_warning', 'investigation_agent_start', 'SymptomTemplatesLoaded', 'OutputParsed']);
  const shown = events.filter(e => !HIDE.has(e.kind)).slice(-80);
  if (shown.length === 0) {
    return <div className="cha-live-empty">No activity yet — click <strong>Start Investigation</strong> to begin.</div>;
  }
  return (
    <div ref={ref} style={{ maxHeight: 520, overflowY: 'auto' }}>
      {shown.map((evt, i) => {
        const { title, body } = summarizeEvent(evt);
        const cat = feedCategory(evt);
        const time = new Date(evt.receivedAt).toLocaleTimeString();
        return (
          <div key={`${evt.receivedAt}-${i}`} className={`cha-feed-item ${cat}`}>
            <div className="fi-header">
              <span className="fi-title">{title}</span>
              <span className="fi-kind">{evt.kind} · {time}</span>
            </div>
            {body && <div className="fi-body">{body}</div>}
          </div>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── */
/* Main page                                                        */
/* ─────────────────────────────────────────────────────────────── */

export default function ChaLivePage() {
  const { state, start, stop, reset } = useLiveInvestigation();
  const [customer, setCustomer] = useState('');
  const [serviceTreeId, setServiceTreeId] = useState('');

  const selected = useMemo(
    () => state.hypotheses.find(h => h.hypothesis_id === state.selectedHypothesisId),
    [state.hypotheses, state.selectedHypothesisId],
  );

  const activatedSignalTypes = state.signalTypes.filter(s => s.activated_count > 0).length;
  const activatedCompounds = state.compounds.filter(c => c.activated).length;

  const topConfidencePct = useMemo(() => {
    if (state.hypotheses.length === 0) return 0;
    let max = 0;
    for (const h of state.hypotheses) {
      if (h.confidence > max) max = h.confidence;
    }
    return Math.round(max * 100);
  }, [state.hypotheses]);

  const phasesProgress = useMemo(() => {
    // Map investigation phases to a 0..100 value for the hero strip.
    const total = ['triage', 'hypothesizing', 'planning', 'collecting', 'reasoning', 'acting', 'notifying', 'complete'];
    const idx = total.indexOf(state.currentPhase);
    if (idx < 0) return 0;
    return Math.round(((idx + 1) / total.length) * 100);
  }, [state.currentPhase]);

  return (
    <div>
      {/* ═══ HERO ═══ */}
      <div className="cha-live-hero">
        <div style={{ flex: 1, minWidth: 0 }}>
          <h2>
            <i className="fas fa-satellite-dish" />&nbsp; Live Agent Orchestration
          </h2>
          <div className="subtitle">
            Signal&nbsp;→&nbsp;Evaluation&nbsp;→&nbsp;Hypothesis&nbsp;→&nbsp;Scoring&nbsp;→&nbsp;Selection&nbsp;→&nbsp;Tool Execution&nbsp;→&nbsp;Summary — streaming in real time from <code>POST /api/run</code>.
          </div>
          <div className="meta">
            {state.customer_name && <span><i className="fas fa-user" /> {state.customer_name}</span>}
            {state.service_tree_id && <span><i className="fas fa-sitemap" /> {state.service_tree_id.slice(0, 8)}…</span>}
            {state.xcv && <span><i className="fas fa-fingerprint" /> xcv={state.xcv.slice(0, 8)}…</span>}
            {state.currentPhase && <span><i className="fas fa-layer-group" /> phase: {state.currentPhase}</span>}
            {state.currentSpeaker && (
              <span className="cha-speaker">
                <span className="dot" /> {state.currentSpeaker}
              </span>
            )}
          </div>
        </div>
        <div className="cha-live-controls">
          <input
            value={customer}
            onChange={e => setCustomer(e.target.value)}
            placeholder="customer (optional)"
            disabled={state.running}
          />
          <input
            value={serviceTreeId}
            onChange={e => setServiceTreeId(e.target.value)}
            placeholder="service_tree_id (optional)"
            disabled={state.running}
          />
          {!state.running ? (
            <button
              className="btn-go"
              onClick={() =>
                start({
                  customer_name: customer.trim() || undefined,
                  service_tree_id: serviceTreeId.trim() || undefined,
                })
              }
            >
              <i className="fas fa-play" /> Start Investigation
            </button>
          ) : (
            <button className="btn-stop" onClick={stop}>
              <i className="fas fa-stop" /> Stop
            </button>
          )}
          <button className="btn-reset" onClick={reset} disabled={state.running}>
            <i className="fas fa-redo" /> Reset
          </button>
        </div>
      </div>

      {/* ═══ PIPELINE BAR ═══ */}
      <PipelineBar stage={state.stage} stagesReached={state.stagesReached} stageProgress={state.stageProgress} />

      {/* ═══ ERROR BANNER ═══ */}
      {state.error && (
        <div
          style={{
            background: '#fff4f4',
            border: '1px solid var(--cha-danger)',
            borderRadius: 8,
            padding: '10px 14px',
            color: 'var(--cha-danger)',
            fontSize: 12,
            marginBottom: 16,
          }}
        >
          <i className="fas fa-exclamation-triangle" />&nbsp;&nbsp;{state.error}
        </div>
      )}

      {/* ═══ METRIC STRIP ═══ */}
      <div className="cha-metrics">
        <Metric label="Signal Types Activated" value={activatedSignalTypes} sub={`${state.signalTypes.length} evaluated`} accent="#6c5ce7" />
        <Metric label="Compound Signals" value={activatedCompounds} sub={`${state.compounds.length} evaluated`} accent="#17a2b8" />
        <Metric
          label="Hypotheses"
          value={state.hypotheses.length}
          sub={state.hypothesisScoring ? `${state.hypothesisScoring.output_hypothesis_count}/${state.hypothesisScoring.input_symptom_count} scored` : 'pending'}
          accent="#e17055"
        />
        <Metric
          label="Evidence Progress"
          value={`${Math.round(state.evidenceProgress * 100)}%`}
          sub={`${state.evidenceCycles} cycle(s) · ${state.toolCalls.length} tool calls`}
          accent="#00b894"
        />
        <Metric
          label="Top Confidence"
          value={`${topConfidencePct}%`}
          sub={selected ? `for ${selected.hypothesis_id}` : '—'}
          accent="#fdcb6e"
        />
        <Metric label="Investigation Phase" value={state.currentPhase || '—'} sub={`${phasesProgress}% through`} accent="#4f6bed" />
      </div>

      {/* ═══ TWO-COLUMN BODY ═══ */}
      <div className="cha-live-grid">
        {/* LEFT column — Signals · Hypotheses · Tools */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {/* Signals */}
          <div className="cha-panel">
            <div className="cha-panel-header">
              <span><i className="fas fa-satellite-dish" />&nbsp;Signals &amp; Compound Evaluation</span>
              {state.signalDecision && (
                <span className="pill" style={{ background: state.signalDecision.action === 'invoke_group_chat' ? '#28a745' : '#8a8aaa' }}>
                  {state.signalDecision.action}
                </span>
              )}
            </div>
            <div className="cha-panel-body">
              {state.signalTypes.length === 0 && state.compounds.length === 0 ? (
                <div className="cha-live-empty">Waiting for signal evaluation…</div>
              ) : (
                <>
                  <div style={{ marginBottom: 10 }}>
                    <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--cha-text-muted)', textTransform: 'uppercase', marginBottom: 4 }}>Signal types</div>
                    {state.signalTypes.map(s => <SignalTypeChip key={s.signal_type_id} row={s} />)}
                  </div>
                  {state.compounds.length > 0 && (
                    <div>
                      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--cha-text-muted)', textTransform: 'uppercase', marginBottom: 4 }}>Compound signals</div>
                      {state.compounds.map(c => <CompoundChip key={c.compound_id} row={c} />)}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>

          {/* Hypotheses */}
          <div className="cha-panel">
            <div className="cha-panel-header">
              <span>
                <i className="fas fa-lightbulb" />&nbsp;Hypotheses — Scoring &amp; Selection
              </span>
              {state.hypothesisScoring && (
                <span className="pill" style={{ background: '#e17055' }}>
                  {state.hypothesisScoring.output_hypothesis_count} / {state.hypothesisScoring.input_symptom_count} scored
                </span>
              )}
            </div>
            <div className="cha-panel-body">
              {state.hypotheses.length === 0 ? (
                <div className="cha-live-empty">No hypotheses yet…</div>
              ) : (
                [...state.hypotheses]
                  .sort((a, b) => (b.match_score || 0) - (a.match_score || 0))
                  .map(h => <HypothesisCard key={h.hypothesis_id} h={h} />)
              )}
            </div>
          </div>

          {/* Tool execution */}
          <div className="cha-panel">
            <div className="cha-panel-header">
              <span><i className="fas fa-cogs" />&nbsp;Tool Execution</span>
              <span className="pill" style={{ background: '#00b894' }}>{state.toolCalls.length} calls</span>
            </div>
            <div className="cha-panel-body" style={{ maxHeight: 340 }}>
              <ToolCallList calls={state.toolCalls} />
            </div>
          </div>
        </div>

        {/* RIGHT column — Activity feed + Summary */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div className="cha-panel">
            <div className="cha-panel-header">
              <span>
                <i className="fas fa-stream" />&nbsp;Live Activity Feed
                {state.running && (
                  <i className="fas fa-circle-notch" style={{ marginLeft: 8, color: 'var(--cha-primary)', animation: 'cha-spin 1.2s linear infinite' }} />
                )}
              </span>
              <span className="pill" style={{ background: 'var(--cha-primary)' }}>{state.events.length}</span>
            </div>
            <div className="cha-panel-body" style={{ paddingRight: 6 }}>
              <ActivityFeed events={state.events} />
            </div>
          </div>

          {/* Summary */}
          {state.summary && (
            <div className="cha-resolution" style={{ marginTop: 0 }}>
              <h3><i className="fas fa-flag-checkered" /> Investigation Summary</h3>

              <div className="rp-label">INVESTIGATION ID</div>
              <div className="rp-value" style={{ fontFamily: 'ui-monospace, monospace' }}>{state.summary.investigation_id}</div>

              {selected && (
                <>
                  <div className="rp-label">SELECTED HYPOTHESIS</div>
                  <div className="rp-value">
                    <strong>{selected.hypothesis_id}</strong> — {selected.statement}
                    <br />
                    Final confidence: <strong>{Math.round((selected.confidence || 0) * 100)}%</strong> · Status: <strong>{selected.status}</strong>
                  </div>
                </>
              )}

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(110px, 1fr))', gap: 8, marginTop: 12 }}>
                <Metric label="Symptoms" value={state.summary.symptoms_count} accent="#0984e3" />
                <Metric label="Hypotheses" value={state.summary.hypotheses_count} accent="#e17055" />
                <Metric label="Evidence" value={state.summary.evidence_count} accent="#00b894" />
                <Metric label="Actions" value={state.summary.actions_count} accent="#e84393" />
                <Metric label="Evidence Cycles" value={state.summary.evidence_cycles} accent="#6c5ce7" />
                <Metric label="Duration (s)" value={state.summary.duration_seconds.toFixed(1)} accent="#4f6bed" />
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
