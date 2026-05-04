/**
 * useMockReplayFlow — paced replay of a scripted demo investigation.
 *
 * Returns the same `ReplayFlowResult` shape as `useReplayFlow` so the
 * Neural Canvas page can swap data sources without changing any
 * rendering code. The data is deterministic, driven by the per-service
 * scripts in `fixtures/mockNeuralCanvasScript.ts`, and paced via
 * `setTimeout` so the chat / relationship graph / sandbox strip /
 * action plan all animate in lockstep with the recorded narration.
 *
 * Usage in `ServicePanel`:
 *
 *   const live = useNeuralCanvasMode() === 'mock'
 *     ? useMockReplayFlow()
 *     : useReplayFlow();
 *
 * Then call `live.start(xcv)` exactly as you would the live hook.
 *
 * Behavioural notes:
 *  - Symptoms / hypotheses / sandbox runs reveal *with their stage*,
 *    not all at once. This makes the relationship graph build column
 *    by column to match the script.
 *  - `traceCount` advances per scripted line `atMs` offset, so the
 *    chat reveals at the same cadence as the rest of the UI.
 *  - When the script ends, `running` flips to false. The page then
 *    surfaces the action plan + "Resolved" marker (already gated on
 *    `complete` in Phase 1).
 */
import { useCallback, useRef, useState } from 'react';
import type { ReplayFlowResult, LiveSymptom, SandboxRun } from './useReplayFlow';
import type { TraceLine, InvestigationStage, Hypothesis, RootCause } from '../pages/customer-agent/ChaInvestigationFlowPage';
import { INVESTIGATION_STAGES } from '../pages/customer-agent/ChaInvestigationFlowPage';
import { getMockScriptByXcv, type MockServiceScript } from '../fixtures/mockNeuralCanvasScript';

const EMPTY_NODE_COUNTS = {
  signal: 0,
  symptom: 0,
  hypothesis: 0,
  evidence: 0,
  scoring: 0,
  reasoning: 0,
  result: '—',
  action_plan: 0,
} as const;

export function useMockReplayFlow(): ReplayFlowResult {
  const [traceLines, setTraceLines] = useState<TraceLine[]>([]);
  const [traceCount, setTraceCount] = useState(0);
  const [stage, setStage] = useState<InvestigationStage | null>(null);
  const [reached, setReached] = useState<InvestigationStage[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [symptoms, setSymptoms] = useState<LiveSymptom[]>([]);
  const [sandboxRuns, setSandboxRuns] = useState<SandboxRun[]>([]);
  const [rootCause, setRootCause] = useState<RootCause | null>(null);
  const [signalTitle, setSignalTitle] = useState('');
  const [running, setRunning] = useState(false);
  const [loading, setLoading] = useState(false);
  const [elapsed, setElapsed] = useState(0);

  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const raf = useRef(0);
  const t0 = useRef(0);

  const clearAll = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    if (raf.current) {
      cancelAnimationFrame(raf.current);
      raf.current = 0;
    }
  }, []);

  // NOTE: deliberately no `useEffect(() => () => clearAll(), [...])` here.
  // React StrictMode double-mounts in dev: the cleanup would fire between
  // the two mounts and wipe all pending timers, while the consumer's
  // auto-start effect then sees `lastXcv.current === service.xcv` on the
  // second mount and refuses to re-arm them — leaving the demo frozen.
  // `start()` already calls `clearAll()` before scheduling, so a fresh
  // run cleanly supersedes any in-flight one. Pending setTimeouts left
  // dangling at unmount are short-lived (≤30s) and self-clean; React
  // will only warn about a "set state on unmounted component" if you
  // route away mid-replay, which is acceptable for a demo page.

  const start = useCallback(
    (xcv: string) => {
      clearAll();
      const script: MockServiceScript | undefined = getMockScriptByXcv(xcv);
      if (!script) {
        // Unknown xcv in mock mode — surface as an idle empty replay
        // so the page renders without errors. Real live mode owns the
        // "no data" UX; mock mode should not.
        setTraceLines([]);
        setTraceCount(0);
        setStage(null);
        setReached([]);
        setHypotheses([]);
        setSymptoms([]);
        setSandboxRuns([]);
        setRootCause(null);
        setSignalTitle('');
        setRunning(false);
        setLoading(false);
        setElapsed(0);
        return;
      }

      // Reset to the start of the script.
      setTraceLines(script.traceLines);
      setTraceCount(0);
      setStage(null);
      setReached([]);
      setHypotheses([]);
      setSymptoms([]);
      setSandboxRuns([]);
      setRootCause(null);
      setSignalTitle(script.signalTitle);
      setLoading(true);
      setRunning(true);
      setElapsed(0);
      t0.current = Date.now();

      // Promote loading -> running quickly so the UI matches live mode.
      timers.current.push(setTimeout(() => setLoading(false), 200));

      // Drive elapsed counter via rAF.
      const tick = () => {
        setElapsed((Date.now() - t0.current) / 1000);
        raf.current = requestAnimationFrame(tick);
      };
      raf.current = requestAnimationFrame(tick);

      // ── Schedule stage transitions ──
      script.stageTimeline.forEach(({ stage: s, atMs }) => {
        timers.current.push(
          setTimeout(() => {
            const stageI = INVESTIGATION_STAGES.indexOf(s);
            if (stageI >= 0) {
              setStage(s);
              setReached(INVESTIGATION_STAGES.slice(0, stageI + 1));
            }

            // Reveal collateral as the stage opens up so the relationship
            // graph builds column by column. Symptoms first (with their
            // stage), then hypotheses (one at a time), then sandbox runs
            // (during scoring/reasoning).
            if (s === 'symptom') {
              // Reveal symptoms one per beat, slightly staggered so the
              // graph doesn't paint all three at once.
              script.symptoms.forEach((sym, i) => {
                timers.current.push(
                  setTimeout(() => {
                    setSymptoms((prev) => [...prev, sym]);
                  }, i * 1_400),
                );
              });
            }
            if (s === 'hypothesis') {
              script.hypotheses.forEach((h, i) => {
                timers.current.push(
                  setTimeout(() => {
                    setHypotheses((prev) => {
                      // Insert in original order; the page already dedupes by id.
                      if (prev.some((p) => p.id === h.id)) return prev;
                      return [...prev, h];
                    });
                  }, i * 1_700),
                );
              });
            }
            if (s === 'scoring' || s === 'reasoning') {
              // Sandbox runs surface during scoring/reasoning. Reveal
              // each run right after its `generatedAtMs` so the strip
              // doesn't dump everything on first paint.
              script.sandboxRuns.forEach((run) => {
                if (run.generatedAtMs == null) return;
                const runRevealAt = Math.max(0, run.generatedAtMs - atMs);
                timers.current.push(
                  setTimeout(() => {
                    setSandboxRuns((prev) => {
                      if (prev.some((p) => p.id === run.id)) return prev;
                      return [...prev, run];
                    });
                  }, runRevealAt),
                );
              });
            }
          }, atMs),
        );
      });

      // ── Schedule trace-line reveals ──
      script.traceLines.forEach((ln, i) => {
        timers.current.push(
          setTimeout(() => {
            setTraceCount(i + 1);
          }, ln.atMs),
        );
      });

      // ── Final wrap-up ──
      timers.current.push(
        setTimeout(() => {
          // Make sure every collateral is fully revealed at the end.
          setSymptoms(script.symptoms);
          setHypotheses(script.hypotheses);
          setSandboxRuns(script.sandboxRuns);
          setReached([...INVESTIGATION_STAGES]);
          setStage('action_plan');
          setTraceCount(script.traceLines.length);
          setRootCause(script.rootCause);
          setRunning(false);
          if (raf.current) {
            cancelAnimationFrame(raf.current);
            raf.current = 0;
          }
        }, script.totalDurationMs),
      );
    },
    [clearAll],
  );

  return {
    stage,
    reached,
    traceCount,
    traceLines,
    confidence: hypotheses.map((h) => ({
      id: h.id,
      label: h.description.length > 60 ? h.description.slice(0, 60) + '…' : h.description,
      score: h.score,
      badgeColor: h.badgeColor,
    })),
    hypotheses,
    rootCause,
    signalTitle,
    nodeCounts: { ...EMPTY_NODE_COUNTS },
    symptoms,
    sandboxRuns,
    loading,
    running,
    error: null,
    elapsed,
    eventCount: traceLines.length,
    start,
  };
}
