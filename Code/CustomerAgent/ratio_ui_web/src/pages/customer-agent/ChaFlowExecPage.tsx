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
} from './ChaInvestigationFlowPage';
import { useLiveInvestigation } from '../../hooks/useLiveInvestigation';
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

  // Live / replay stream — only used to drive the stopwatch + event count.
  const live = useLiveInvestigation();
  const liveRunning = live.state.running;
  const liveEventCount = live.state.events.length;

  const running = mockRunning || liveRunning;
  const complete = reached.length === INVESTIGATION_STAGES.length && !running;

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
        agentFilter: agentFilter || undefined,
        pollPacingMs,
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
        <WorkflowCanvas reached={reached} active={stage} counts={MOCK_NODE_COUNTS} />
      ) : (
        <N8nWorkflowGraph reached={reached} active={stage} counts={MOCK_NODE_COUNTS} />
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
          disabled={running}
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
      <SignalHeader title={MOCK_SIGNAL.title} status={MOCK_SIGNAL.status} />

      {/* Two-column: Agent Reasoning + Hypothesis Verdict */}
      <div style={S.panelRow}>
        <ReasoningPanel
          traceLines={MOCK_TRACE}
          visibleCount={traceCount}
          complete={complete}
        />
        <HypothesisPanel hypotheses={MOCK_HYPOTHESES} />
      </div>

      {/* Root Cause + Confidence + Summary */}
      <RootCauseSection rootCause={MOCK_ROOT_CAUSE} visible={complete} />
    </div>
  );
}
