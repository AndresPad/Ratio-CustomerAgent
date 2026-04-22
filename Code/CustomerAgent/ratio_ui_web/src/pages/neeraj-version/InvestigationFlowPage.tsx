/**
 * InvestigationFlowPage — animated "Investigation Reasoning Flow" view.
 *
 * Auto-plays through stages with timed progression:
 *   Signal → Symptom → Hypothesis → Evidence Collection →
 *   Confidence Scoring → Reasoning Animation → Result
 *
 * Each stage reveals its section in the left panel and streams
 * matching trace lines into the right sidebar. The "Re-run" button
 * resets and replays from the beginning.
 */
import {
  Fragment,
  useState,
  useEffect,
  useCallback,
  useRef,
  type CSSProperties,
} from 'react';
import {
  INVESTIGATION_STAGES,
  STAGE_DISPLAY,
  STAGE_DURATION,
  STAGE_ICON,
  STAGE_COLOR,
  type InvestigationStage,
  type Symptom,
  type TraceLine,
  type ConfidenceScore,
} from './investigationTypes';
import {
  MOCK_SIGNAL,
  MOCK_TRACE,
  MOCK_CONFIDENCE,
  MOCK_ROOT_CAUSE,
} from './mockData';
import './investigation-theme.css';

/* ─────────────────────────────────────────────────────────────── */
/*  Custom hook: auto-play through stages                          */
/* ─────────────────────────────────────────────────────────────── */

function useInvestigationFlow() {
  const [stageIdx, setStageIdx] = useState(-1); // -1 = not started
  const [visibleTraceCount, setVisibleTraceCount] = useState(0);
  const [running, setRunning] = useState(false);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);

  const currentStage: InvestigationStage | null =
    stageIdx >= 0 && stageIdx < INVESTIGATION_STAGES.length
      ? INVESTIGATION_STAGES[stageIdx]
      : null;

  const reachedStages = INVESTIGATION_STAGES.slice(0, stageIdx + 1);

  /** Clear all pending timers */
  const clearTimers = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
  }, []);

  /** Start (or restart) the animated flow */
  const start = useCallback(() => {
    clearTimers();
    setStageIdx(-1);
    setVisibleTraceCount(0);
    setRunning(true);

    let elapsed = 400; // small initial delay

    INVESTIGATION_STAGES.forEach((stage, i) => {
      // Advance to this stage
      const t1 = setTimeout(() => setStageIdx(i), elapsed);
      timers.current.push(t1);

      // Stream trace lines belonging to this stage one-by-one
      const linesForStage = MOCK_TRACE.filter((l) => l.stage === stage);
      const lineDelay = linesForStage.length > 0
        ? Math.min(300, STAGE_DURATION[stage] / (linesForStage.length + 1))
        : 0;

      let lineOffset = 200; // slight pause after entering stage
      linesForStage.forEach(() => {
        const t2 = setTimeout(
          () => setVisibleTraceCount((c) => c + 1),
          elapsed + lineOffset,
        );
        timers.current.push(t2);
        lineOffset += lineDelay;
      });

      elapsed += STAGE_DURATION[stage];
    });

    // Mark as done
    const tEnd = setTimeout(() => setRunning(false), elapsed);
    timers.current.push(tEnd);
  }, [clearTimers]);

  /** Stop & reset */
  const reset = useCallback(() => {
    clearTimers();
    setRunning(false);
    setStageIdx(-1);
    setVisibleTraceCount(0);
  }, [clearTimers]);

  // Auto-start on mount
  useEffect(() => {
    start();
    return clearTimers;
  }, [start, clearTimers]);

  return { currentStage, reachedStages, visibleTraceCount, running, start, reset };
}

/* ─────────────────────────────────────────────────────────────── */
/*  Pipeline stage bar                                              */
/* ─────────────────────────────────────────────────────────────── */

function PipelineStages({
  currentStage,
  reachedStages,
}: {
  currentStage: InvestigationStage | null;
  reachedStages: InvestigationStage[];
}) {
  return (
    <div className="inv-pipeline">
      {INVESTIGATION_STAGES.map((s, i) => {
        const isActive = s === currentStage;
        const isReached = reachedStages.includes(s) && !isActive;
        const stateCls = isActive ? 'active' : isReached ? 'reached' : '';
        const colorCls = (isActive || isReached) ? `clr-${STAGE_COLOR[s]}` : '';
        return (
          <Fragment key={s}>
            <div className={`inv-stage ${stateCls} ${colorCls}`}>
              <i className={`bi ${STAGE_ICON[s]} inv-stage-icon`} />
              {STAGE_DISPLAY[s]}
            </div>
            {i < INVESTIGATION_STAGES.length - 1 && (
              <span className="inv-stage-arrow">›</span>
            )}
          </Fragment>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── */
/*  Symptom block (with optional hypothesis & evidence hiding)     */
/* ─────────────────────────────────────────────────────────────── */

function SymptomBlock({
  symptom,
  showHypothesis,
  showEvidence,
  showFinalScore,
}: {
  symptom: Symptom;
  showHypothesis: boolean;
  showEvidence: boolean;
  showFinalScore: boolean;
}) {
  const h = symptom.hypothesis;
  const score = showFinalScore ? h.confidence : h.prior;
  const level = score >= 50 ? 'high' : 'low';

  return (
    <div className="inv-symptom">
      {/* Symptom header */}
      <div className="inv-symptom-header">
        <div className="inv-symptom-icon warning">
          <i className="bi bi-exclamation-circle-fill" />
        </div>
        <span className="symptom-title">{symptom.title}</span>
      </div>

      {/* Hypothesis row — revealed at hypothesis stage */}
      {showHypothesis && (
        <div className="inv-hypothesis inv-animate-in">
          <div className={`inv-hyp-indicator ${level}`}>
            {level === 'high' ? (
              <i className="bi bi-gear-fill" />
            ) : (
              <i className="bi bi-x-lg" />
            )}
          </div>
          <span className="inv-hyp-label">{h.label}</span>
          <div className="inv-hyp-bar-wrap">
            <div className="inv-hyp-bar">
              <div
                className={`inv-hyp-bar-fill ${level}`}
                style={{ width: `${score}%` }}
              />
            </div>
            <span className={`inv-hyp-pct ${level}`}>{score}%</span>
          </div>
        </div>
      )}

      {/* Evidence list — revealed at evidence stage */}
      {showEvidence && (
        <div className="inv-evidence-list inv-animate-in">
          {symptom.evidence.map((ev, i) => (
            <div className="inv-evidence-item" key={i}>
              <div className={`inv-evidence-icon ${ev.status}`}>
                {ev.status === 'success' && <i className="bi bi-clipboard-check" />}
                {ev.status === 'neutral' && <i className="bi bi-clipboard" />}
                {ev.status === 'failure' && <i className="bi bi-clipboard-x" />}
              </div>
              <div className="inv-evidence-text">
                <div className="evidence-title">{ev.title}</div>
                <div className="evidence-detail">{ev.detail}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── */
/*  Reasoning Trace (right sidebar) — streams line by line          */
/* ─────────────────────────────────────────────────────────────── */

function traceClass(type: TraceLine['type']): string {
  switch (type) {
    case 'highlight': return 'trace-highlight';
    case 'success':   return 'trace-success';
    case 'fail':      return 'trace-fail';
    case 'result':    return 'trace-result';
    default:          return '';
  }
}

function ReasoningTrace({
  lines,
  visibleCount,
}: {
  lines: TraceLine[];
  visibleCount: number;
}) {
  const endRef = useRef<HTMLDivElement>(null);

  // Auto-scroll to bottom as new lines appear
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [visibleCount]);

  return (
    <div className="inv-trace">
      {lines.slice(0, visibleCount).map((line, i) => (
        <div
          key={i}
          className={`trace-line inv-trace-animate ${line.indent ? 'trace-indent' : ''}`}
        >
          <span className="trace-bullet">•</span>{' '}
          <span className={traceClass(line.type)}>{line.text}</span>
        </div>
      ))}
      {visibleCount < lines.length && (
        <div className="inv-typing-indicator">
          <span /><span /><span />
        </div>
      )}
      <div ref={endRef} />
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── */
/*  Confidence scores panel — animates from 0                       */
/* ─────────────────────────────────────────────────────────────── */

function ConfidencePanel({
  scores,
  visible,
}: {
  scores: ConfidenceScore[];
  visible: boolean;
}) {
  if (!visible) return null;
  return (
    <div className="inv-confidence-panel inv-animate-in">
      <div className="inv-confidence-title">Confidence Scores</div>
      {scores.map((s) => {
        const level = s.score >= 50 ? 'high' : 'low';
        return (
          <div className="inv-conf-row" key={s.id}>
            <span className={`inv-conf-badge ${s.badgeClass}`}>{s.id}</span>
            <span className="inv-conf-label">{s.label}</span>
            <div className="inv-conf-bar">
              <div
                className={`inv-conf-bar-fill ${level}`}
                style={{ width: `${s.score}%` }}
              />
            </div>
            <span className={`inv-conf-pct ${level}`}>{s.score}%</span>
          </div>
        );
      })}
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── */
/*  Root cause + recommended action                                 */
/* ─────────────────────────────────────────────────────────────── */

function RootCausePanel({
  description,
  action,
  visible,
}: {
  description: string;
  action: string;
  visible: boolean;
}) {
  if (!visible) return null;
  return (
    <div className="inv-rootcause inv-animate-in">
      <div className="inv-rootcause-header">
        <i className="bi bi-check-circle-fill" />
        <span>Root Cause Identified</span>
      </div>
      <p>{description}</p>
      <div className="inv-action-title">Recommended Action</div>
      <div className="inv-action-code">{action}</div>
    </div>
  );
}

/* ─────────────────────────────────────────────────────────────── */
/*  Helper: has a stage been reached yet?                           */
/* ─────────────────────────────────────────────────────────────── */

function stageReached(
  reached: InvestigationStage[],
  stage: InvestigationStage,
): boolean {
  return reached.includes(stage);
}

/* ─────────────────────────────────────────────────────────────── */
/*  Main page                                                       */
/* ─────────────────────────────────────────────────────────────── */

export function InvestigationFlowPage() {
  const [activeTab, setActiveTab] = useState<'flow' | 'chat'>('flow');
  const {
    currentStage,
    reachedStages,
    visibleTraceCount,
    running,
    start,
  } = useInvestigationFlow();

  const reached = (s: InvestigationStage) => stageReached(reachedStages, s);

  return (
    <div className="inv-root">
      {/* Top tab bar */}
      <div className="inv-topbar">
        <button
          className={`inv-tab ${activeTab === 'flow' ? 'active' : ''}`}
          onClick={() => setActiveTab('flow')}
        >
          Investigation Flow
        </button>
        <button
          className={`inv-tab ${activeTab === 'chat' ? 'active' : ''}`}
          onClick={() => setActiveTab('chat')}
        >
          Chat Agent
        </button>
      </div>

      {/* Header */}
      <div className="inv-header">
        <div className="inv-header-left">
          <div className="inv-header-icon">
            <i className="bi bi-stars" />
          </div>
          <div>
            <h1>Investigation Reasoning Flow</h1>
            <div className="subtitle">
              Signal → Symptom → Hypothesis → Evidence → Confidence → Result
            </div>
          </div>
        </div>
        <button
          className="inv-rerun-btn"
          onClick={start}
          disabled={running}
          style={running ? { opacity: 0.5, cursor: 'not-allowed' } as CSSProperties : undefined}
        >
          <i className={`bi ${running ? 'bi-hourglass-split inv-spin' : 'bi-arrow-clockwise'}`} />
          {running ? 'Running…' : 'Re-run'}
        </button>
      </div>

      {/* Pipeline stages */}
      <PipelineStages currentStage={currentStage} reachedStages={reachedStages} />

      {/* Body: main content + sidebar */}
      <div className="inv-body">
        {/* Left: progressively revealed sections */}
        <div className="inv-main">
          {/* Signal card — stage ≥ signal */}
          {reached('signal') && (
            <div className="inv-signal-card inv-animate-in">
              <div className="inv-signal-icon">
                <i className="bi bi-lightning-charge-fill" />
              </div>
              <span className="signal-title">{MOCK_SIGNAL.title}</span>
            </div>
          )}

          {/* Symptom blocks — each reveals progressively */}
          {reached('symptom') &&
            MOCK_SIGNAL.symptoms.map((sym, i) => (
              <SymptomBlock
                key={i}
                symptom={sym}
                showHypothesis={reached('hypothesis')}
                showEvidence={reached('evidence')}
                showFinalScore={reached('scoring')}
              />
            ))}

          {/* Confidence scoring — inline in tree view */}
          {reached('scoring') && (
            <div className="inv-tree-section inv-animate-in">
              <div className="inv-tree-section-header">
                <div className="inv-tree-section-icon scoring">
                  <i className="bi bi-bar-chart-fill" />
                </div>
                <span className="inv-tree-section-title">Confidence Scoring</span>
              </div>
              <div className="inv-tree-section-body">
                {MOCK_CONFIDENCE.map((s) => {
                  const level = s.score >= 50 ? 'high' : 'low';
                  return (
                    <div className="inv-tree-score-row" key={s.id}>
                      <span className={`inv-conf-badge ${s.badgeClass}`}>{s.id}</span>
                      <span className="inv-tree-score-label">{s.label}</span>
                      <div className="inv-tree-score-bar">
                        <div
                          className={`inv-tree-score-bar-fill ${level}`}
                          style={{ width: `${s.score}%` }}
                        />
                      </div>
                      <span className={`inv-tree-score-pct ${level}`}>{s.score}%</span>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Reasoning animation — inline in tree view */}
          {reached('reasoning') && (
            <div className="inv-tree-section inv-animate-in">
              <div className="inv-tree-section-header">
                <div className="inv-tree-section-icon reasoning">
                  <i className="bi bi-braces-asterisk" />
                </div>
                <span className="inv-tree-section-title">Reasoning</span>
              </div>
              <div className="inv-tree-reasoning-body">
                {MOCK_TRACE.filter(
                  (l) => l.stage === 'scoring' || l.stage === 'reasoning',
                ).map((line, i) => (
                  <div
                    key={i}
                    className={`inv-tree-reason-line inv-trace-animate ${line.indent ? 'trace-indent' : ''}`}
                    style={{ animationDelay: `${i * 120}ms` }}
                  >
                    <span className="trace-bullet">•</span>{' '}
                    <span className={traceClass(line.type)}>{line.text}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Result — inline in tree view */}
          {reached('result') && (
            <div className="inv-tree-section inv-animate-in">
              <div className="inv-tree-section-header">
                <div className="inv-tree-section-icon result">
                  <i className="bi bi-check-circle-fill" />
                </div>
                <span className="inv-tree-section-title result">Root Cause Identified</span>
              </div>
              <div className="inv-tree-section-body">
                <p className="inv-tree-result-desc">{MOCK_ROOT_CAUSE.description}</p>
                <div className="inv-tree-result-action-label">Recommended Action</div>
                <div className="inv-action-code">{MOCK_ROOT_CAUSE.recommendedAction}</div>
              </div>
            </div>
          )}
        </div>

        {/* Right sidebar */}
        <div className="inv-sidebar">
          <div className="inv-sidebar-header">
            <i className="bi bi-braces" />
            Reasoning Trace
          </div>
          <ReasoningTrace lines={MOCK_TRACE} visibleCount={visibleTraceCount} />
          <ConfidencePanel scores={MOCK_CONFIDENCE} visible={reached('scoring')} />
          <RootCausePanel
            description={MOCK_ROOT_CAUSE.description}
            action={MOCK_ROOT_CAUSE.recommendedAction}
            visible={reached('result')}
          />
        </div>
      </div>
    </div>
  );
}
