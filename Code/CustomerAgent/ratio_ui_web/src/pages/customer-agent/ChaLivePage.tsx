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
  type LiveState,
} from '../../hooks/useLiveInvestigation';
import type { LiveEvent } from '../../api/liveOrchestrationClient';
import type { OrchestrationMode } from '../../api/orchestrationSource';
import {
  DataSourceToggle,
  loadStoredMode,
  loadStoredXcv,
  loadStoredAgentFilter,
  persistMode,
  persistXcv,
  persistAgentFilter,
} from '../../components/DataSourceToggle';
import {
  PollingStopwatch,
  loadStoredPacing,
  persistPacing,
} from '../../components/PollingStopwatch';

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

function ActivityFeed({ state }: { state: LiveState }) {
  const [open, setOpen] = useState<Record<string, boolean>>({
    progress: true, agents: true, skills: true, activity: true,
  });
  const toggle = (k: string) => setOpen(prev => ({ ...prev, [k]: !prev[k] }));

  const running = state.running;
  const completed = state.done;

  // Hidden noisy kinds — same list we used before.
  const HIDE = useMemo(
    () => new Set(['LLMCall', 'investigation_agent_chunk', 'investigation_stall_warning', 'investigation_agent_start', 'SymptomTemplatesLoaded', 'OutputParsed']),
    [],
  );
  const visible = useMemo(() => state.events.filter(e => !HIDE.has(e.kind)), [state.events, HIDE]);
  const timeline = useMemo(() => [...visible].reverse().slice(0, 30), [visible]);

  // Aggregate agent participation from agentTurns + speaker reasons.
  const agents = useMemo(() => {
    const map = new Map<string, { name: string; count: number; lastTs: number; lastAction: string }>();
    for (const t of state.agentTurns) {
      if (!t.agent) continue;
      const prev = map.get(t.agent);
      const action = t.phase ? `responded in ${t.phase}` : 'responded';
      if (prev) {
        prev.count += 1;
        if (t.ts > prev.lastTs) { prev.lastTs = t.ts; prev.lastAction = action; }
      } else {
        map.set(t.agent, { name: t.agent, count: 1, lastTs: t.ts, lastAction: action });
      }
    }
    for (const c of state.toolCalls) {
      if (!c.agent) continue;
      const prev = map.get(c.agent);
      const action = `used ${c.tool}`;
      if (prev) {
        if (c.ts > prev.lastTs) { prev.lastTs = c.ts; prev.lastAction = action; }
      } else {
        map.set(c.agent, { name: c.agent, count: 1, lastTs: c.ts, lastAction: action });
      }
    }
    if (state.currentSpeaker && !map.has(state.currentSpeaker)) {
      map.set(state.currentSpeaker, { name: state.currentSpeaker, count: 0, lastTs: Date.now(), lastAction: 'selected as speaker' });
    }
    return [...map.values()].sort((a, b) => b.lastTs - a.lastTs);
  }, [state.agentTurns, state.toolCalls, state.currentSpeaker]);

  // Unique skills/tools with invocation counts.
  const skills = useMemo(() => {
    const map = new Map<string, { tool: string; count: number; errors: number; lastTs: number }>();
    for (const c of state.toolCalls) {
      if (!c.tool) continue;
      const prev = map.get(c.tool);
      if (prev) {
        prev.count += 1;
        if (c.error) prev.errors += 1;
        if (c.ts > prev.lastTs) prev.lastTs = c.ts;
      } else {
        map.set(c.tool, { tool: c.tool, count: 1, errors: c.error ? 1 : 0, lastTs: c.ts });
      }
    }
    return [...map.values()].sort((a, b) => b.lastTs - a.lastTs);
  }, [state.toolCalls]);

  const fmtTime = (ts: number) => {
    const d = new Date(ts);
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`;
  };

  const currentIdx = STAGES.indexOf(state.stage);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', minHeight: 400 }}>
      {/* Header pill row */}
      <div style={{
        padding: '4px 0 10px', display: 'flex', alignItems: 'center', gap: 8,
        borderBottom: '1px solid var(--cha-border)', marginBottom: 6,
      }}>
        {running && (
          <span style={{
            fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
            background: '#e3f2fd', color: '#1565c0', textTransform: 'uppercase', letterSpacing: 0.5,
          }}>
            <i className="fas fa-circle" style={{ fontSize: 6, marginRight: 4, color: '#2196f3' }} />Live
          </span>
        )}
        {completed && (
          <span style={{
            fontSize: 9, fontWeight: 700, padding: '2px 7px', borderRadius: 4,
            background: '#e8f5e9', color: '#2e7d32', textTransform: 'uppercase', letterSpacing: 0.5,
          }}>Done</span>
        )}
        {state.currentPhase && (
          <span style={{ fontSize: 11, color: 'var(--cha-text-muted)' }}>
            Phase: <strong style={{ color: 'var(--cha-text-primary)' }}>{state.currentPhase}</strong>
          </span>
        )}
        <div style={{ flex: 1 }} />
        {state.xcv && (
          <span style={{ fontFamily: 'ui-monospace, monospace', fontSize: 10, color: 'var(--cha-text-muted)' }}>
            XCV {state.xcv.slice(0, 8)}
          </span>
        )}
      </div>

      {/* ── Progress ─────────────────────────────────────────── */}
      <FeedSection
        id="progress"
        icon="fa-tasks"
        title="Progress"
        count={`${state.stagesReached.length}/${STAGES.length}`}
        open={open.progress}
        onToggle={toggle}
      >
        {STAGES.map((s, i) => {
          const isCurrent = state.stage === s && running;
          const isDone = state.stagesReached.includes(s) && (completed || i < currentIdx);
          const status: 'done' | 'current' | 'pending' =
            isCurrent ? 'current'
            : isDone || (completed && state.stagesReached.includes(s)) ? 'done'
            : 'pending';
          return (
            <div key={s} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '6px 2px', fontSize: 12,
              opacity: status === 'pending' ? 0.45 : 1,
              background: status === 'current' ? 'rgba(79, 107, 237, 0.08)' : 'transparent',
              borderLeft: status === 'current' ? '3px solid var(--cha-primary)' : '3px solid transparent',
              paddingLeft: 8,
            }}>
              <div style={{
                width: 18, height: 18, borderRadius: '50%', flexShrink: 0,
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 9, color: '#fff',
                background: status === 'done' ? '#28a745'
                          : status === 'current' ? 'var(--cha-primary)'
                          : '#d0d7e2',
                animation: status === 'current' ? 'chaPulseLive 1.8s ease-in-out infinite' : 'none',
              }}>
                <i className={`fas ${status === 'done' ? 'fa-check' : STAGE_ICONS[s]}`} />
              </div>
              <span style={{ flex: 1, fontWeight: status === 'current' ? 600 : 400 }}>
                {STAGE_LABELS[s]}
              </span>
              {status === 'current' && state.stageProgress > 0 && (
                <span style={{ fontSize: 10, color: 'var(--cha-text-muted)' }}>
                  {Math.round(state.stageProgress)}%
                </span>
              )}
            </div>
          );
        })}
        {state.signalDecision && (
          <div style={{
            margin: '8px 0 4px', padding: '6px 10px',
            background: '#f1f8e9', border: '1px solid #c5e1a5', borderRadius: 6,
            fontSize: 11, color: '#33691e',
          }}>
            <i className="fas fa-flag-checkered" style={{ marginRight: 6 }} />
            Signal decision: <strong>{state.signalDecision.action}</strong>
            &nbsp;· {state.signalDecision.signal_count} signals, {state.signalDecision.compound_count} compounds
          </div>
        )}
      </FeedSection>

      {/* ── Agents ──────────────────────────────────────────── */}
      <FeedSection
        id="agents"
        icon="fa-user-astronaut"
        title="Agents"
        count={String(agents.length)}
        open={open.agents}
        onToggle={toggle}
      >
        {agents.length === 0 ? (
          <div style={{ padding: '4px 2px 8px', fontSize: 11, color: 'var(--cha-text-muted)', fontStyle: 'italic' }}>
            No agents have spoken yet.
          </div>
        ) : agents.map(a => {
          const isActive = a.name === state.currentSpeaker && running;
          return (
            <div key={a.name} style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '6px 2px', fontSize: 12,
              background: isActive ? 'rgba(79, 107, 237, 0.06)' : 'transparent',
              paddingLeft: 8,
            }}>
              <div style={{
                width: 24, height: 24, borderRadius: '50%',
                background: isActive ? 'var(--cha-primary)' : '#e0e7ff',
                color: isActive ? '#fff' : 'var(--cha-primary)',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: 10, fontWeight: 700, flexShrink: 0,
                animation: isActive ? 'chaPulseLive 1.8s ease-in-out infinite' : 'none',
              }}>
                {a.name.slice(0, 2).toUpperCase()}
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontWeight: isActive ? 600 : 500,
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  {a.name}
                  {isActive && (
                    <span style={{ marginLeft: 6, fontSize: 9, color: 'var(--cha-primary)', fontWeight: 700 }}>
                      <i className="fas fa-circle fa-fade" style={{ fontSize: 6, marginRight: 3 }} />ACTIVE
                    </span>
                  )}
                </div>
                <div style={{
                  fontSize: 10, color: 'var(--cha-text-muted)',
                  whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                }}>
                  {a.lastAction}
                </div>
              </div>
              {a.count > 0 && (
                <span style={{
                  fontSize: 10, fontWeight: 700, padding: '2px 6px', borderRadius: 10,
                  background: '#eef1f8', color: 'var(--cha-primary)', flexShrink: 0,
                }}>
                  ×{a.count}
                </span>
              )}
            </div>
          );
        })}
      </FeedSection>

      {/* ── Skills ──────────────────────────────────────────── */}
      <FeedSection
        id="skills"
        icon="fa-toolbox"
        title="Skills"
        count={String(skills.length)}
        open={open.skills}
        onToggle={toggle}
      >
        {skills.length === 0 ? (
          <div style={{ padding: '4px 2px 8px', fontSize: 11, color: 'var(--cha-text-muted)', fontStyle: 'italic' }}>
            No skills invoked yet.
          </div>
        ) : (
          <div style={{ padding: '2px 0 8px', display: 'flex', flexWrap: 'wrap', gap: 6 }}>
            {skills.map(t => (
              <span key={t.tool} style={{
                display: 'inline-flex', alignItems: 'center', gap: 5,
                padding: '4px 8px', fontSize: 11, fontWeight: 500,
                background: t.errors ? '#ffebee' : '#eef5ff',
                color: t.errors ? '#c62828' : '#1565c0',
                border: `1px solid ${t.errors ? '#ffcdd2' : '#bbdefb'}`,
                borderRadius: 12,
              }}>
                <i className={`fas ${t.errors ? 'fa-triangle-exclamation' : 'fa-wrench'}`} style={{ fontSize: 9 }} />
                {t.tool}
                {t.count > 1 && (
                  <span style={{
                    fontSize: 9, fontWeight: 700, padding: '1px 5px', borderRadius: 8,
                    background: t.errors ? '#c62828' : '#1565c0', color: '#fff',
                  }}>
                    {t.count}
                  </span>
                )}
              </span>
            ))}
          </div>
        )}
        {state.evidenceCycles > 0 && (
          <div style={{ padding: '4px 0 6px' }}>
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              fontSize: 10, color: 'var(--cha-text-muted)', marginBottom: 3,
            }}>
              <span>Evidence progress</span>
              <span><strong>{state.evidenceCycles}</strong> cycle{state.evidenceCycles === 1 ? '' : 's'}</span>
            </div>
            <div style={{ height: 4, background: '#eef1f8', borderRadius: 2, overflow: 'hidden' }}>
              <div style={{
                width: `${Math.min(100, state.evidenceProgress * 100)}%`,
                height: '100%', background: 'linear-gradient(90deg, var(--cha-primary) 0%, #16a085 100%)',
                transition: 'width 0.3s ease',
              }} />
            </div>
          </div>
        )}
      </FeedSection>

      {/* ── Activity timeline ───────────────────────────────── */}
      <FeedSection
        id="activity"
        icon="fa-stream"
        title="Activity"
        count={String(visible.length)}
        open={open.activity}
        onToggle={toggle}
        last
      >
        {timeline.length === 0 ? (
          <div style={{ padding: '4px 2px 8px', fontSize: 11, color: 'var(--cha-text-muted)', fontStyle: 'italic' }}>
            No activity yet — click <strong>Start Investigation</strong> to begin.
          </div>
        ) : timeline.map((evt, i) => {
          const { title, body } = summarizeEvent(evt);
          const cat = feedCategory(evt);
          return (
            <div
              key={`${evt.receivedAt}-${i}`}
              className={`cha-feed-item ${cat}`}
              style={{
                margin: '4px 0', padding: '6px 8px',
                background: i === 0 && running ? 'rgba(79, 107, 237, 0.05)' : undefined,
                animation: i === 0 ? 'chaSlideInLive 0.3s ease' : undefined,
              }}
            >
              <div className="fi-header" style={{ fontSize: 11 }}>
                <span className="fi-title" style={{ fontWeight: 600 }}>{title}</span>
                <span className="fi-kind" style={{ fontSize: 9 }}>{fmtTime(evt.receivedAt)}</span>
              </div>
              {body && <div className="fi-body" style={{ fontSize: 10 }}>{body}</div>}
            </div>
          );
        })}
        {visible.length > 30 && (
          <div style={{ padding: '4px 2px', fontSize: 10, color: 'var(--cha-text-muted)', textAlign: 'center' }}>
            +{visible.length - 30} earlier events
          </div>
        )}
      </FeedSection>

      <style>{`
        @keyframes chaPulseLive {
          0%, 100% { transform: scale(1); opacity: 1; }
          50%      { transform: scale(1.15); opacity: 0.85; }
        }
        @keyframes chaSlideInLive {
          from { opacity: 0; transform: translateX(-4px); }
          to   { opacity: 1; transform: translateX(0); }
        }
      `}</style>
    </div>
  );
}

function FeedSection({
  id, icon, title, count, open, onToggle, last, children,
}: {
  id: string;
  icon: string;
  title: string;
  count?: string;
  open: boolean;
  onToggle: (id: string) => void;
  last?: boolean;
  children: React.ReactNode;
}) {
  return (
    <div style={{ borderBottom: last ? 'none' : '1px solid var(--cha-border)' }}>
      <button
        type="button"
        onClick={() => onToggle(id)}
        style={{
          width: '100%', padding: '10px 2px',
          display: 'flex', alignItems: 'center', gap: 8,
          background: 'transparent', border: 'none', cursor: 'pointer',
          fontSize: 12, fontWeight: 700, color: 'var(--cha-text-primary)',
          textAlign: 'left',
        }}
      >
        <i className={`fas ${icon}`} style={{ color: 'var(--cha-primary)', fontSize: 12, width: 14, textAlign: 'center' }} />
        <span style={{ flex: 1 }}>{title}</span>
        {count !== undefined && (
          <span style={{
            fontSize: 10, fontWeight: 700, padding: '1px 7px', borderRadius: 10,
            background: '#eef1f8', color: 'var(--cha-primary)',
          }}>
            {count}
          </span>
        )}
        <i className={`fas fa-chevron-${open ? 'up' : 'down'}`}
           style={{ fontSize: 10, color: 'var(--cha-text-muted)' }} />
      </button>
      {open && <div style={{ paddingBottom: 8 }}>{children}</div>}
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
  const [mode, setMode] = useState<OrchestrationMode>(() => loadStoredMode('mock'));
  const [xcv, setXcv] = useState<string>(() => loadStoredXcv());
  const [agentFilter, setAgentFilter] = useState<string>(() => loadStoredAgentFilter());
  const [pollPacingMs, setPollPacingMs] = useState<number>(() => loadStoredPacing(300));
  const [summaryOpen, setSummaryOpen] = useState<boolean>(() => {
    try { return localStorage.getItem('cha.live.summaryOpen') !== '0'; }
    catch { return true; }
  });

  useEffect(() => { persistMode(mode); }, [mode]);
  useEffect(() => { persistXcv(xcv); }, [xcv]);
  useEffect(() => { persistAgentFilter(agentFilter); }, [agentFilter]);
  useEffect(() => { persistPacing(pollPacingMs); }, [pollPacingMs]);
  useEffect(() => {
    try { localStorage.setItem('cha.live.summaryOpen', summaryOpen ? '1' : '0'); }
    catch { /* noop */ }
  }, [summaryOpen]);

  // Track elapsed time while waiting for the first event, so "New Run" mode
  // doesn't look silently stuck (cold-start pipeline can take 10–30s).
  const [waitElapsedMs, setWaitElapsedMs] = useState(0);
  const waitingForFirst = state.running && state.events.length === 0;
  useEffect(() => {
    if (!waitingForFirst) { setWaitElapsedMs(0); return; }
    const started = Date.now();
    const id = setInterval(() => setWaitElapsedMs(Date.now() - started), 250);
    return () => clearInterval(id);
  }, [waitingForFirst]);

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
          <DataSourceToggle
            mode={mode}
            xcv={xcv}
            disabled={state.running}
            onModeChange={setMode}
            onXcvChange={setXcv}
          />
          {mode === 'live' && (
            <>
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
            </>
          )}
          {!state.running ? (
            <button
              className="btn-go"
              onClick={() =>
                start({
                  mode,
                  xcv: xcv || undefined,
                  customer_name: mode === 'live' ? (customer.trim() || undefined) : undefined,
                  service_tree_id: mode === 'live' ? (serviceTreeId.trim() || undefined) : undefined,
                  agentFilter: undefined,
                  pollPacingMs: mode === 'replay' ? pollPacingMs : undefined,
                })
              }
              disabled={mode === 'replay' && !xcv}
              title={mode === 'replay' && !xcv ? 'Paste an xcv to replay' : undefined}
            >
              <i className="fas fa-play" />
              {mode === 'live' ? ' New Run' : mode === 'replay' ? ' Polling' : ' Mock'}
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

      {/* ═══ POLLING STOPWATCH ═══ */}
      {mode === 'replay' && (
        <div style={{ marginBottom: 10 }}>
          <PollingStopwatch
            running={state.running}
            eventCount={state.events.length}
            pacingMs={pollPacingMs}
            onPacingChange={setPollPacingMs}
          />
        </div>
      )}

      {/* ═══ PIPELINE BAR ═══ */}
      <PipelineBar stage={state.stage} stagesReached={state.stagesReached} stageProgress={state.stageProgress} />

      {/* ═══ WAITING BANNER ═══ */}
      {waitingForFirst && (
        <div
          style={{
            background: '#f4f8ff',
            border: '1px solid #b6ccff',
            borderRadius: 8,
            padding: '10px 14px',
            color: '#1a2a6b',
            fontSize: 12,
            marginBottom: 12,
            display: 'flex',
            alignItems: 'center',
            gap: 10,
          }}
        >
          <i className="fas fa-spinner fa-spin" />
          <span>
            {mode === 'live' ? (
              <>
                Starting new pipeline run — waiting for first event… ({(waitElapsedMs / 1000).toFixed(1)}s)
                {waitElapsedMs > 15000 && (
                  <span style={{ color: '#b26a00', marginLeft: 8 }}>
                    <i className="fas fa-triangle-exclamation" /> Still nothing? Check the uvicorn terminal for errors (MCP :8000 / Azure OpenAI auth).
                  </span>
                )}
              </>
            ) : mode === 'replay' ? (
              <>Fetching past-run events from App Insights… ({(waitElapsedMs / 1000).toFixed(1)}s)</>
            ) : (
              <>Loading demo fixture… ({(waitElapsedMs / 1000).toFixed(1)}s)</>
            )}
          </span>
        </div>
      )}

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
                <i className="fas fa-satellite-dish" />&nbsp;Details
                {state.running && (
                  <i className="fas fa-circle-notch" style={{ marginLeft: 8, color: 'var(--cha-primary)', animation: 'cha-spin 1.2s linear infinite' }} />
                )}
              </span>
              <span className="pill" style={{ background: 'var(--cha-primary)' }}>{state.events.length}</span>
            </div>
            <div className="cha-panel-body" style={{ paddingRight: 6 }}>
              <ActivityFeed state={state} />
            </div>
          </div>

          {/* Summary */}
          {state.summary && (
            <div className="cha-resolution" style={{ marginTop: 0 }}>
              <button
                type="button"
                onClick={() => setSummaryOpen(v => !v)}
                aria-expanded={summaryOpen}
                style={{
                  width: '100%', padding: 0, display: 'flex', alignItems: 'center', gap: 8,
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  textAlign: 'left', color: 'inherit', font: 'inherit',
                }}
              >
                <h3 style={{ flex: 1, margin: 0 }}>
                  <i className="fas fa-flag-checkered" /> Investigation Summary
                </h3>
                <i className={`fas fa-chevron-${summaryOpen ? 'up' : 'down'}`}
                   style={{ fontSize: 12, color: 'var(--cha-text-muted)' }} />
              </button>

              {summaryOpen && (
                <>
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
                </>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
