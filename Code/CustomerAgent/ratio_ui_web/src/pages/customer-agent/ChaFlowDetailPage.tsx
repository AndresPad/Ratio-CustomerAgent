/**
 * ChaFlowDetailPage -- LIVE (XCV) detail view.
 *
 * Navigated to from ChaFlowExecPage via /customer-agent/investigation-flow/:xcv
 * Shows the full investigation replay for a specific XCV trace from Log Analytics.
 */
import { useState, type CSSProperties } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  WorkflowCanvas,
  N8nWorkflowGraph,
  StatusBar,
  SignalHeader,
  ReasoningPanel,
  HypothesisPanel,
  RootCauseSection,
  INVESTIGATION_STAGES,
  ACTIVITY_BAR as S,
} from './ChaInvestigationFlowPage';
import { useReplayFlow } from '../../hooks/useReplayFlow';

const DEFAULT_XCV = '21b43d61-d4f8-44d8-82fe-c05ba40e1fea';

export default function ChaFlowDetailPage() {
  const { xcv: paramXcv } = useParams<{ xcv: string }>();
  const navigate = useNavigate();
  const [xcv, setXcv] = useState(paramXcv || DEFAULT_XCV);
  const [view, setView] = useState<'pipeline' | 'graph'>('graph');

  const live = useReplayFlow();

  const reached = live.reached;
  const active = live.stage;
  const running = live.running || live.loading;
  const elapsed = live.elapsed;
  const complete = reached.length === INVESTIGATION_STAGES.length && !running;
  const traceLines = live.traceLines;
  const hypotheses = live.hypotheses;
  const rootCause = live.rootCause;
  const counts = live.nodeCounts;
  const signalTitle = live.signalTitle || 'Investigation';
  const mapped = traceLines.length;

  const handleLoad = () => live.start(xcv);
  const handleBack = () => navigate('/customer-agent/investigation-flow');

  return (
    <div style={{
      height: 'calc(100vh - 52px)',
      margin: '0 -24px -24px',
      position: 'relative',
      zIndex: 11,
      overflowY: 'auto',
      background: '#fafafa',
    }}>
      {/* View toggle + Back button */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '8px 20px 0', fontSize: 11 }}>
        <button
          onClick={handleBack}
          style={{
            padding: '4px 12px', borderRadius: 6, fontSize: 11, fontWeight: 600, cursor: 'pointer',
            border: '1px solid #ddd', background: '#fff', color: '#666',
            display: 'flex', alignItems: 'center', gap: 4,
          }}
        >
          <i className="fas fa-arrow-left" /> Back to Live
        </button>
        <div style={{ width: 1, height: 20, background: '#ddd', margin: '0 4px' }} />
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
        <WorkflowCanvas reached={reached} active={active} counts={counts} />
      ) : (
        <N8nWorkflowGraph reached={reached} active={active} counts={counts} />
      )}

      {/* Status */}
      <StatusBar
        agentName="Summary Writer"
        statusText={running ? (live.loading ? 'Loading trace\u2026' : 'Replaying\u2026') : (complete ? 'Investigation complete' : 'Enter XCV and click Load')}
        complete={complete}
        elapsed={elapsed}
      />

      {/* Control Bar: XCV input + Load */}
      <div style={S.controlBar as CSSProperties}>
        <span style={{ fontSize: 12, fontWeight: 600, color: '#00bfa5', display: 'flex', alignItems: 'center', gap: 6 }}>
          <i className="fas fa-satellite-dish" /> LIVE (XCV)
        </span>

        <input
          style={S.xcvInput}
          value={xcv}
          onChange={(e) => setXcv(e.target.value)}
          placeholder="Enter XCV\u2026"
        />
        <button style={S.loadBtn} onClick={handleLoad} disabled={running}>
          <i className={`fas ${running ? 'fa-spinner fa-spin' : 'fa-search'}`} /> Load
        </button>

        {live.eventCount > 0 && (
          <span style={S.eventStats}>
            {'\u{1f4e6}'} {live.eventCount} events loaded {'\u00b7'} {mapped} mapped to stages
          </span>
        )}

        {live.error && (
          <span style={{ color: '#e53935', fontSize: 12, marginLeft: 8 }}>
            {'\u26a0'} {live.error}
          </span>
        )}

        <span style={S.spacer as CSSProperties} />

        <button style={S.actionBtnPrimary} onClick={handleLoad} disabled={running}>
          <i className={`fas ${running ? 'fa-spinner fa-spin' : 'fa-redo'}`} /> Re-run
        </button>
      </div>

      {/* Signal title */}
      <SignalHeader title={signalTitle} status="Resolved" />

      {/* Two-column: Agent Reasoning + Hypothesis Verdict */}
      <div style={S.panelRow}>
        <ReasoningPanel
          traceLines={traceLines}
          visibleCount={live.traceCount}
          complete={complete}
        />
        <HypothesisPanel hypotheses={hypotheses} />
      </div>

      {/* Root Cause + Confidence + Summary */}
      <RootCauseSection rootCause={rootCause ?? undefined} visible={rootCause != null} />
    </div>
  );
}
