/**
 * ChaFlowExecPage -- Live Investigation Reasoning Flow (Neural Canvas v1).
 *
 * Auto-plays the investigation animation. Users can switch the data
 * source between Mock / Polling / New Run via the shared toggle to
 * match the Neural Canvas v2 controls.
 */
import { useCallback, useEffect, useMemo, useState, type CSSProperties } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  WorkflowCanvas,
  N8nWorkflowGraph,
  StatusBar,
  SignalHeader,
  ReasoningPanel,
  HypothesisPanel,
  RootCauseSection,
  useFlow,
  INVESTIGATION_STAGES,
  MOCK_NODE_COUNTS,
  MOCK_ROOT_CAUSE,
  MOCK_HYPOTHESES,
  MOCK_TRACE,
  MOCK_SIGNAL,
  ACTIVITY_BAR as S,
  type TraceLine,
  type Hypothesis,
  type RootCause,
  type InvestigationStage,
} from './ChaInvestigationFlowPage';
import { useLiveInvestigation, type Stage, STAGES } from '../../hooks/useLiveInvestigation';
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

export default function ChaFlowExecPage() {
  const navigate = useNavigate();
  const [view, setView] = useState<'pipeline' | 'graph'>('graph');

  // ─── Data source (mock | replay | live) ───────────────────────
  const [mode, setMode] = useState<OrchestrationMode>(() => loadStoredMode());
  const [xcv, setXcv] = useState<string>(() => loadStoredXcv());
  const [agentFilter, setAgentFilter] = useState<string>(() => loadStoredAgentFilter());
  const [pollPacingMs, setPollPacingMs] = useState<number>(() => loadStoredPacing(300));

  useEffect(() => { persistMode(mode); }, [mode]);
  useEffect(() => { persistXcv(xcv); }, [xcv]);
  useEffect(() => { persistAgentFilter(agentFilter); }, [agentFilter]);
  useEffect(() => { persistPacing(pollPacingMs); }, [pollPacingMs]);

  // Mock animation (always drives the visuals).
  const { stage, reached, traceCount, running: mockRunning, elapsed, start: startMock } = useFlow();

  // Live / replay stream — drives stopwatch + event count + panel data in non-mock modes.
  const live = useLiveInvestigation();
  const liveRunning = live.state.running;
  const liveEventCount = live.state.events.length;

  const running = mockRunning || liveRunning;
  const complete = reached.length === INVESTIGATION_STAGES.length && !running;

  // ─── Map live events → TraceLine[] for the ReasoningPanel ─────
  const STAGE_MAP: Record<Stage, InvestigationStage> = {
    signal: 'signal',
    evaluation: 'symptom',
    hypothesis: 'hypothesis',
    scoring: 'scoring',
    selection: 'reasoning',
    tool_execution: 'evidence',
    summary: 'result',
  };

  const liveTraceLines = useMemo<TraceLine[]>(() => {
    if (mode === 'mock') return MOCK_TRACE;
    // Only show narrator agent's llm_response_text
    return live.state.events
      .filter((ev) => {
        const props = (ev.Properties ?? {}) as Record<string, unknown>;
        const agent = String(ev.AgentName ?? ev.Agent ?? props.AgentName ?? props.Agent ?? ev.agent_name ?? '').toLowerCase();
        const llmText = String(ev.llm_response_text ?? ev.ResponseText ?? props.ResponseText ?? ev.text ?? '');
        return agent.includes('narrator') && llmText.trim().length > 0;
      })
      .map((ev) => {
        const props = (ev.Properties ?? {}) as Record<string, unknown>;
        const pick = (k: string): string => String(ev[k] ?? props[k] ?? '');
        const llmText = pick('llm_response_text') || pick('ResponseText') || pick('text');
        const phase = pick('Phase') || pick('phase') || pick('to_phase') || '';

        // Map phase to investigation stage
        let mappedStage: InvestigationStage = 'reasoning';
        const pl = phase.toLowerCase();
        if (pl.includes('signal') || pl.includes('triage')) mappedStage = 'signal';
        else if (pl.includes('symptom') || pl.includes('evaluat')) mappedStage = 'symptom';
        else if (pl.includes('hypothes')) mappedStage = 'hypothesis';
        else if (pl.includes('collect') || pl.includes('evidence') || pl.includes('acting')) mappedStage = 'evidence';
        else if (pl.includes('scor') || pl.includes('confidence')) mappedStage = 'scoring';
        else if (pl.includes('reason') || pl.includes('plan')) mappedStage = 'reasoning';
        else if (pl.includes('complete') || pl.includes('summary') || pl.includes('notif')) mappedStage = 'result';

        // Determine line type from content
        const lower = llmText.toLowerCase();
        let type: TraceLine['type'] = 'normal';
        if (lower.includes('complete') || lower.includes('root cause') || lower.includes('conclusion')) type = 'result';
        else if (lower.includes('error') || lower.includes('fail') || lower.includes('refuted')) type = 'fail';
        else if (lower.includes('confirmed') || lower.includes('supported') || lower.includes('success')) type = 'success';
        else if (lower.includes('hypothesis') || lower.includes('symptom') || lower.includes('evidence')) type = 'highlight';

        // Icon based on stage
        let icon = '\u{1f7e2}';
        if (mappedStage === 'signal') icon = '\u{1f535}';
        else if (mappedStage === 'symptom') icon = '\u{1f7e1}';
        else if (mappedStage === 'hypothesis') icon = '\u{1f536}';
        else if (mappedStage === 'evidence') icon = '\u{1f7e3}';
        else if (mappedStage === 'scoring') icon = '\u{1f4ca}';
        else if (mappedStage === 'result') icon = '\u2705';

        const clip = llmText.length > 400 ? llmText.substring(0, 400) + '\u2026' : llmText;
        return { stage: mappedStage, text: clip, type, icon };
      });
  }, [mode, live.state.events]);

  const liveHypotheses = useMemo<Hypothesis[]>(() => {
    if (mode === 'mock') return MOCK_HYPOTHESES;
    return live.state.hypotheses.map((h) => {
      let badgeColor = '#e67e22';
      if (h.hypothesis_id.startsWith('HYP-DEP')) badgeColor = '#e74c3c';
      else if (h.hypothesis_id.startsWith('HYP-SLI')) badgeColor = '#3498db';
      return {
        id: h.hypothesis_id,
        description: h.statement || h.hypothesis_id,
        score: Math.round(h.confidence * 100) || h.match_score || 50,
        status: h.status === 'SUPPORTED' ? 'supported' as const : h.status === 'REFUTED' ? 'refuted' as const : 'uncertain' as const,
        badgeColor,
      };
    });
  }, [mode, live.state.hypotheses]);

  const liveRootCause = useMemo<RootCause | null>(() => {
    if (mode === 'mock') return MOCK_ROOT_CAUSE;
    if (!live.state.summary) return null;
    const s = live.state.summary;
    const topHyp = live.state.hypotheses.find(h => h.selected);
    return {
      title: 'Root Cause Identified',
      description: topHyp?.statement || 'Root cause determined.',
      confidence: Math.round((topHyp?.confidence ?? 0) * 100),
      summary: `Investigated ${s.symptoms_count} symptoms \u2192 ${s.hypotheses_count} hypotheses \u2192 ${s.evidence_count} evidence items \u2192 ${s.actions_count} actions (${s.duration_seconds}s)`,
    };
  }, [mode, live.state.summary, live.state.hypotheses]);

  // Visible count: in mock mode use the animated count, in live mode show all events as they arrive
  const effectiveTraceCount = mode === 'mock' ? traceCount : liveTraceLines.length;
  const effectiveComplete = mode === 'mock' ? complete : live.state.done;
  const effectiveSignalTitle = mode === 'mock' ? MOCK_SIGNAL.title : (live.state.customer_name || 'Investigation');

  // ─── Map live stages to graph stages ──────────────────────────
  const liveReached = useMemo<InvestigationStage[]>(() => {
    if (mode === 'mock') return reached;
    return live.state.stagesReached.map((s) => STAGE_MAP[s]).filter(Boolean);
  }, [mode, reached, live.state.stagesReached]);

  const liveActiveStage = useMemo<InvestigationStage | null>(() => {
    if (mode === 'mock') return stage;
    return STAGE_MAP[live.state.stage] ?? null;
  }, [mode, stage, live.state.stage]);

  const liveNodeCounts = useMemo(() => {
    if (mode === 'mock') return MOCK_NODE_COUNTS;
    const st = live.state;
    const topScore = st.hypotheses.length > 0
      ? Math.round(Math.max(...st.hypotheses.map(h => h.confidence)) * 100)
      : 0;
    return {
      signal: st.signalTypes.length || st.events.filter(e => e.kind?.includes('Signal')).length || 0,
      symptom: st.compounds.length || 0,
      hypothesis: st.hypotheses.length || 0,
      evidence: st.toolCalls.length || 0,
      scoring: st.hypotheses.length || 0,
      reasoning: st.agentTurns.length || 0,
      result: st.done ? `${topScore}%` : '\u2014',
    };
  }, [mode, live.state]);

  const startLabel = useMemo(() => {
    if (running) return 'Running…';
    if (mode === 'mock') return 'Mock';
    if (mode === 'replay') return 'Polling';
    return 'New Run';
  }, [running, mode]);

  const handleStart = useCallback(() => {
    // Kick the mock animation so the canvas + panels animate regardless of mode.
    startMock();
    if (mode === 'replay') {
      live.start({
        mode: 'replay',
        xcv: xcv || undefined,
        // Don't pass agentFilter to backend — the reducer needs ALL event
        // kinds (SignalEvaluationStart, HypothesisScoring, etc.) to advance
        // stages. Narrator-only filtering happens client-side in liveTraceLines.
        agentFilter: undefined,
        pollPacingMs,
        repollIntervalMs: 5000, // re-poll every 5s until RequestEnd
      });
    } else if (mode === 'live') {
      live.start({ mode: 'live' });
    }
  }, [mode, xcv, agentFilter, pollPacingMs, startMock, live]);

  return (
    <div style={{
      height: 'calc(100vh - 52px)',
      margin: '0 -24px -24px',
      position: 'relative',
      zIndex: 11,
      overflowY: 'auto',
      background: '#fafafa',
    }}>
      {/* View toggle */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 20px 0', fontSize: 11 }}>
        <button
          onClick={() => setView('pipeline')}
          style={{
            padding: '4px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer',
            border: view === 'pipeline' ? '1px solid #00bfa5' : '1px solid #ddd',
            background: view === 'pipeline' ? '#e0f7fa' : '#fff',
            color: view === 'pipeline' ? '#00796b' : '#888',
          }}
        >
          <i className="fas fa-stream" /> Pipeline
        </button>
        <button
          onClick={() => setView('graph')}
          style={{
            padding: '4px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer',
            border: view === 'graph' ? '1px solid #845ec2' : '1px solid #ddd',
            background: view === 'graph' ? '#f3e5f5' : '#fff',
            color: view === 'graph' ? '#6a1b9a' : '#888',
          }}
        >
          <i className="fas fa-project-diagram" /> n8n Graph
        </button>
      </div>

      {/* Pipeline or n8n Graph */}
      {view === 'pipeline' ? (
        <WorkflowCanvas reached={liveReached} active={liveActiveStage} counts={liveNodeCounts} />
      ) : (
        <N8nWorkflowGraph reached={liveReached} active={liveActiveStage} counts={liveNodeCounts} />
      )}

      {/* Status */}
      <StatusBar
        agentName="Summary Writer"
        statusText={running ? 'Replaying\u2026' : 'Investigation complete'}
        complete={complete}
        elapsed={elapsed}
      />

      {/* Source toggle + polling stopwatch */}
      <div style={{ padding: '8px 20px 0', display: 'flex', flexDirection: 'column', gap: 8 }}>
        <DataSourceToggle
          mode={mode}
          xcv={xcv}
          onModeChange={setMode}
          onXcvChange={setXcv}
          agentFilter={agentFilter}
          onAgentFilterChange={setAgentFilter}
        />
        {mode === 'replay' && (
          <PollingStopwatch
            running={liveRunning}
            eventCount={liveEventCount}
            pacingMs={pollPacingMs}
            onPacingChange={setPollPacingMs}
          />
        )}
      </div>

      {/* Control Bar */}
      <div style={S.controlBar as CSSProperties}>
        <span style={{ fontSize: 12, fontWeight: 600, color: '#00bfa5', display: 'flex', alignItems: 'center', gap: 6 }}>
          <i className="fas fa-broadcast-tower" /> LIVE
        </span>

        <button
          style={{
            ...S.modeBtn,
            background: 'rgba(124,58,237,0.08)', color: '#7c3aed', border: '1px solid rgba(124,58,237,0.3)',
          }}
          onClick={() => navigate('/customer-agent/investigation-flow/detail')}
        >
          <i className="fas fa-satellite-dish" /> LIVE (XCV)
        </button>

        <button
          style={{
            ...S.modeBtn,
            background: 'rgba(79,107,237,0.08)', color: '#4f6bed', border: '1px solid rgba(79,107,237,0.3)',
          }}
          onClick={() => navigate('/customer-agent/investigation-flow/deep-dive')}
        >
          <i className="fas fa-microscope" /> Deep Dive
        </button>

        <span style={S.spacer as CSSProperties} />

        <button style={S.actionBtnPrimary} onClick={handleStart} disabled={running}>
          <i className={`fas ${running ? 'fa-spinner fa-spin' : 'fa-play'}`} /> {startLabel}
        </button>
      </div>

      {/* Signal title */}
      <SignalHeader title={effectiveSignalTitle} status={MOCK_SIGNAL.status} />

      {/* Two-column: Agent Reasoning + Hypothesis Verdict */}
      <div style={S.panelRow}>
        <ReasoningPanel
          traceLines={liveTraceLines}
          visibleCount={effectiveTraceCount}
          complete={effectiveComplete}
        />
        <HypothesisPanel hypotheses={liveHypotheses} />
      </div>

      {/* Root Cause + Confidence + Summary */}
      <RootCauseSection rootCause={liveRootCause ?? undefined} visible={effectiveComplete && liveRootCause != null} />
    </div>
  );
}
