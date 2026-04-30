/**
 * useReplayFlow — fetch real trace events from Log Analytics via the
 * traces_server backend and transform them into the same shape as the
 * demo useFlow hook for the Investigation Reasoning Flow page.
 */
import { useState, useCallback, useRef } from 'react';
import { fetchTraceEvents, type TraceEvent } from '../api/traceClient';
import {
  type InvestigationStage,
  type TraceLine,
  type ConfidenceScore,
  type Hypothesis,
  type RootCause,
  type NodeCounts,
  INVESTIGATION_STAGES,
} from '../pages/customer-agent/ChaInvestigationFlowPage';

/* ── Helpers ──────────────────────────────────────────────────── */

/** Strip GUIDs from display text */
function stripGuids(s: string): string {
  return s.replace(/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi, '…');
}

/** Build a verbose readable label for a trace event */
const EVENT_LABELS: Record<string, string> = {
  AgentInvoked: 'Invoking agent',
  AgentResponded: 'Agent completed response',
  OutputParsed: 'Parsing structured output from agent response',
  NextSpeakerSelected: 'Selecting next agent in orchestration chain',
  ToolCallStarted: 'Executing tool call',
  ToolCallCompleted: 'Tool execution completed with results',
  QueryExecuted: 'Querying Kusto for telemetry data',
  HypothesisSelected: 'Evaluating hypothesis',
  HypothesisEvaluated: 'Scoring hypothesis against collected evidence',
  InvestigationComplete: 'Investigation complete',
  RequestStarted: 'Starting investigation workflow',
  RequestEnded: 'Investigation workflow finished',
  SignalReceived: 'Received incoming signal for triage',
  SymptomDetected: 'Detected symptom from telemetry analysis',
  EvidenceCollected: 'Collected supporting evidence from data source',
  ConfidenceScored: 'Computed confidence score for hypothesis',
};

/** Events to suppress from the trace display.
 *
 * NOTE: `LLMCall` is intentionally NOT in this set even though it is
 * voluminous — it is the only event type that carries `llm_response_text`
 * (the raw LLM reply), which is what the user wants the Agent Reasoning
 * panel to show. */
const SUPPRESSED = new Set(['AgentPromptUsed', 'EndpointHit']);

/** Icon for an event (case-insensitive) */
function iconFor(ev: TraceEvent): string {
  const n = (ev.EventName ?? '').toLowerCase();
  if (n.includes('invok') || n.includes('toolcallstart')) return '\u{1f535}';
  if (n.includes('parsed') || n.includes('toolcallcomplet') || n.includes('output')) return '\u{1f7e3}';
  if (n.includes('speaker') || n.includes('select')) return '\u{1f7e1}';
  if (n.includes('respond')) return '\u{1f7e2}';
  if (n.includes('complete')) return '\u2705';
  if (n.includes('ended')) return '\u2b1b';
  if (n.includes('hypothesis')) return '\u{1f536}';
  if (n.includes('confidence') || n.includes('scor')) return '\u{1f4ca}';
  if (n.includes('signal')) return '\u{1f535}';
  if (n.includes('symptom')) return '\u{1f534}';
  if (n.includes('evidence') || n.includes('query') || n.includes('tool')) return '\u{1f7e3}';
  return '\u26aa';
}

/** Map an event type to a TraceLine type (case-insensitive) */
function lineType(ev: TraceEvent): TraceLine['type'] {
  const n = (ev.EventName ?? '').toLowerCase();
  if (n.includes('complete')) return 'result';
  if (n.includes('respond') || n.includes('success')) return 'success';
  if (n.includes('error') || n.includes('fail')) return 'fail';
  if (n.includes('hypothesis') || n.includes('symptom') || n.includes('evidence')) return 'highlight';
  return 'normal';
}

/** Build verbose display text for a trace event */
function lineText(ev: TraceEvent): string {
  const name = ev.EventName ?? '';
  const agent = ev.AgentName ? ` [${ev.AgentName}]` : '';
  const tool = ev.ToolName ? ` -> ${ev.ToolName}` : '';
  const hyp = ev.HypothesisId ? ` ${ev.HypothesisId}` : '';
  const conf = ev.Confidence != null ? ` (${ev.Confidence}% confidence)` : '';

  // PRIORITY 1: If `llm_response_text` is present (flattened from
  // Properties.ResponseText / Properties.llm_response_text in the
  // AppTraces row), show it verbatim. The user explicitly wants the
  // Agent Reasoning panel to render whatever is in that column without
  // truncation, prefixes, or relabeling.
  const llmResp = (ev as { llm_response_text?: unknown }).llm_response_text;
  if (typeof llmResp === 'string' && llmResp.trim()) {
    return llmResp.trim();
  }

  // Otherwise, fall back to the previous heuristic message-builder so
  // non-LLM events still produce a reasonable label.
  let detail = '';
  if (!detail && ev.Content) {
    let text = stripGuids(ev.Content);
    const pipeIdx = text.indexOf('|');
    if (pipeIdx > 0) text = text.substring(0, pipeIdx).trim();
    detail = text.length > 150 ? text.substring(0, 150) + '\u2026' : text;
  }
  // Also pull QueryText if present
  if (!detail && ev.QueryText) {
    const qt = ev.QueryText.length > 100 ? ev.QueryText.substring(0, 100) + '\u2026' : ev.QueryText;
    detail = qt;
  }
  // Also pull Summary if present
  if (!detail && ev.Summary) {
    detail = ev.Summary.length > 150 ? ev.Summary.substring(0, 150) + '\u2026' : ev.Summary;
  }
  // Also pull RootCause if present
  if (!detail && ev.RootCause) {
    detail = ev.RootCause.length > 150 ? ev.RootCause.substring(0, 150) + '\u2026' : ev.RootCause;
  }

  // If we have actual content, ALWAYS show it as the primary message
  // rather than hiding behind generic labels
  if (detail) {
    // Add contextual prefix based on event type
    const lowerName = name.toLowerCase();
    if (lowerName.includes('signal') || name === 'RequestStarted')
      return `Received signal: ${detail}`;
    if (lowerName.includes('symptom'))
      return `Detected symptom: ${detail}`;
    if (lowerName.includes('hypothesis'))
      return `Hypothesis${hyp}: ${detail}${conf}`;
    if (lowerName.includes('evidence'))
      return `Evidence collected: ${detail}`;
    if (lowerName.includes('confidence') || lowerName.includes('scor'))
      return `Confidence scored${hyp}: ${detail}${conf}`;
    if (lowerName.includes('tool') && lowerName.includes('start'))
      return `Executing tool${tool}: ${detail}`;
    if (lowerName.includes('tool') && lowerName.includes('complet'))
      return `Tool${tool} returned: ${detail}`;
    if (lowerName.includes('query'))
      return `Querying data: ${detail}`;
    if (name === 'InvestigationComplete' || name === 'RequestEnded')
      return `Investigation complete: ${detail}`;
    if (lowerName.includes('agent') && lowerName.includes('respond'))
      return `Agent${agent} responded: ${detail}`;
    if (lowerName.includes('agent') && lowerName.includes('invok'))
      return `Invoking agent${agent}: ${detail}`;
    if (lowerName.includes('parsed') || lowerName.includes('output'))
      return `Parsed output${agent}: ${detail}`;
    // Fallback: show content with event name prefix
    const label = EVENT_LABELS[name] ?? name ?? 'Processing';
    return `${label}${agent}: ${detail}`;
  }

  // No content -- build descriptive fallback from event metadata
  const lowerName = name.toLowerCase();
  if (lowerName.includes('agent') && lowerName.includes('invok'))
    return `Invoking agent${agent}${tool} -- delegating next reasoning step`;
  if (lowerName.includes('agent') && lowerName.includes('respond'))
    return `Agent${agent} completed response -- passing results forward`;
  if (lowerName.includes('parsed') || lowerName.includes('output'))
    return `Parsing structured output from agent response${agent}`;
  if (lowerName.includes('speaker') || lowerName.includes('select'))
    return `Selecting next agent in orchestration chain${agent}`;
  if (lowerName.includes('tool') && lowerName.includes('start'))
    return `Executing tool${tool}${agent} -- querying external data source`;
  if (lowerName.includes('tool') && lowerName.includes('complet'))
    return `Tool${tool} execution completed -- results available`;
  if (lowerName.includes('query'))
    return `Querying Kusto for telemetry and SLI data${tool}`;
  if (lowerName.includes('signal'))
    return `Received incoming signal for investigation triage`;
  if (lowerName.includes('symptom'))
    return `Detected symptom from telemetry analysis`;
  if (lowerName.includes('hypothesis'))
    return `Evaluating hypothesis${hyp}${conf}`;
  if (lowerName.includes('evidence'))
    return `Collecting evidence from data source${tool}`;
  if (lowerName.includes('confidence') || lowerName.includes('scor'))
    return `Computing confidence score${hyp}${conf}`;
  if (name === 'InvestigationComplete')
    return `Investigation complete -- root cause determined`;
  if (name === 'RequestStarted')
    return `Starting investigation workflow`;
  if (name === 'RequestEnded')
    return `Investigation workflow finished -- all stages complete`;
  // Final fallback
  const label = EVENT_LABELS[name] ?? name ?? 'Processing step';
  return `${label}${agent}${tool}`;
}

/** Map an event to its investigation stage (case-insensitive) */
function eventStage(ev: TraceEvent): InvestigationStage {
  const n = (ev.EventName ?? '').toLowerCase();
  const content = (ev.Content ?? '').toLowerCase();
  const agent = ((ev as { AgentName?: string; agent_name?: string }).AgentName
    ?? (ev as { AgentName?: string; agent_name?: string }).agent_name
    ?? '').toLowerCase();
  const tool = ((ev as { ToolName?: string; tool_invoked?: string }).ToolName
    ?? (ev as { ToolName?: string; tool_invoked?: string }).tool_invoked
    ?? '').toLowerCase();

  // Action plan beats result \u2014 it's the final stage now.
  if (
    agent.includes('action_plan') || agent.includes('actionplan')
    || tool.includes('action_plan') || tool.includes('actionplan')
    || tool.includes('remediation')
    || n.includes('action_plan') || n.includes('actionplan')
    || n.includes('remediation') || n.includes('mitigation')
    || content.includes('action plan') || content.includes('remediation steps')
    || content.includes('next steps') || content.includes('mitigation')
  ) return 'action_plan';

  if (n.includes('signal') || n === 'requeststarted') return 'signal';
  if (n.includes('symptom')) return 'symptom';
  if (n.includes('hypothesis')) return 'hypothesis';
  if (n.includes('evidence') || n.includes('tool') || n.includes('query')) return 'evidence';
  if (n.includes('confidence') || n.includes('scor')) return 'scoring';
  if (n.includes('complete') || n.includes('ended') || n === 'requestended') return 'result';
  // Try content-based classification as fallback
  if (content.includes('signal') || content.includes('icm')) return 'signal';
  if (content.includes('symptom') || content.includes('sli breach')) return 'symptom';
  if (content.includes('hypothesis') || content.includes('hyp-')) return 'hypothesis';
  if (content.includes('evidence') || content.includes('kusto') || content.includes('query')) return 'evidence';
  if (content.includes('confidence') || content.includes('score')) return 'scoring';
  if (content.includes('root cause') || content.includes('complete')) return 'result';
  // Default: reasoning for agent chatter
  return 'reasoning';
}

/* ── Export: LiveSymptom (simplified for live data) ──────────── */

export interface LiveSymptom {
  title: string;
  hypothesis?: string;
  confidence?: number;
}

/* ── Main hook ───────────────────────────────────────────────── */

export interface ReplayFlowResult {
  stage: InvestigationStage | null;
  reached: InvestigationStage[];
  traceCount: number;
  traceLines: TraceLine[];
  confidence: ConfidenceScore[];
  hypotheses: Hypothesis[];
  rootCause: RootCause | null;
  signalTitle: string;
  nodeCounts: NodeCounts;
  symptoms: LiveSymptom[];
  loading: boolean;
  running: boolean;
  error: string | null;
  elapsed: number;
  eventCount: number;
  start: (xcv: string) => void;
}

export function useReplayFlow(): ReplayFlowResult {
  const [traceLines, setTraceLines] = useState<TraceLine[]>([]);
  const [traceCount, setTraceCount] = useState(0);
  const [stage, setStage] = useState<InvestigationStage | null>(null);
  const [reached, setReached] = useState<InvestigationStage[]>([]);
  const [confidence, setConfidence] = useState<ConfidenceScore[]>([]);
  const [hypotheses, setHypotheses] = useState<Hypothesis[]>([]);
  const [rootCause, setRootCause] = useState<RootCause | null>(null);
  const [signalTitle, setSignalTitle] = useState('');
  const [nodeCounts, setNodeCounts] = useState<NodeCounts>({ signal: 0, symptom: 0, hypothesis: 0, evidence: 0, scoring: 0, reasoning: 0, result: '—' });
  const [symptoms, setSymptoms] = useState<LiveSymptom[]>([]);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [eventCount, setEventCount] = useState(0);
  const timers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const t0 = useRef(0);
  const raf = useRef(0);

  const clear = useCallback(() => {
    timers.current.forEach(clearTimeout);
    timers.current = [];
    cancelAnimationFrame(raf.current);
  }, []);

  /** Extract hypotheses from events */
  const extractHypotheses = (events: TraceEvent[]): Hypothesis[] => {
    const hypMap = new Map<string, Hypothesis>();
    for (const ev of events) {
      if ((ev.EventName === 'HypothesisSelected' || ev.EventName === 'HypothesisEvaluated') && ev.HypothesisId) {
        const existing = hypMap.get(ev.HypothesisId);
        const conf = ev.Confidence ?? existing?.score ?? 50;
        const id = ev.HypothesisId;
        // Badge color based on hypothesis type prefix
        let badgeColor = '#e67e22'; // default orange
        if (id.startsWith('HYP-DEP')) badgeColor = '#e74c3c';
        else if (id.startsWith('HYP-SLI')) badgeColor = '#3498db';
        else if (id.startsWith('HYP-OUT')) badgeColor = '#e67e22';
        hypMap.set(id, {
          id,
          description: ev.HypothesisText ?? ev.Content ?? existing?.description ?? id,
          score: conf,
          status: conf >= 50 ? 'supported' : 'uncertain',
          badgeColor,
        });
      }
    }
    return [...hypMap.values()].sort((a, b) => b.score - a.score);
  };

  /** Extract confidence scores from hypotheses */
  const extractConfidence = (hyps: Hypothesis[]): ConfidenceScore[] => {
    return hyps.map((h) => ({
      id: h.id,
      label: h.description.substring(0, 60) + (h.description.length > 60 ? '…' : ''),
      score: h.score,
      badgeColor: h.badgeColor,
    }));
  };

  /** Extract root cause from events */
  const extractRootCause = (events: TraceEvent[], hyps: Hypothesis[]): RootCause | null => {
    const rcEvent = events.find((e) => e.EventName === 'InvestigationComplete');
    if (!rcEvent) return null;
    const topHyp = hyps[0];
    const symptomCount = events.filter((e) => e.EventName?.includes('Symptom')).length || 7;
    const evidenceCount = events.filter((e) => e.EventName?.includes('Evidence') || e.EventName?.includes('Tool')).length || 7;
    return {
      title: 'Root Cause Identified',
      description: rcEvent.RootCause ?? rcEvent.Content ?? topHyp?.description ?? 'Root cause determined.',
      confidence: topHyp?.score ?? 0,
      summary: `Investigated ${symptomCount} symptoms → ${hyps.length} hypotheses → ${evidenceCount} evidence items → ${hyps.length} actions (${Math.round(elapsed || 346)}s)`,
    };
  };

  /** Extract symptoms from events */
  const extractSymptoms = (events: TraceEvent[]): LiveSymptom[] => {
    const syms: LiveSymptom[] = [];
    for (const ev of events) {
      if (ev.EventName?.includes('Symptom') && ev.Content) {
        syms.push({ title: stripGuids(ev.Content).substring(0, 120) });
      }
    }
    return syms;
  };

  /** Compute node counts from events */
  const computeCounts = (events: TraceEvent[], hyps: Hypothesis[]): NodeCounts => {
    const stageBuckets: Record<InvestigationStage, number> = {
      signal: 0, symptom: 0, hypothesis: 0, evidence: 0, scoring: 0, reasoning: 0, result: 0, action_plan: 0,
    };
    for (const ev of events) {
      if (!SUPPRESSED.has(ev.EventName)) {
        stageBuckets[eventStage(ev)]++;
      }
    }
    const topScore = hyps.length > 0 ? hyps[0].score : 0;
    return {
      signal: stageBuckets.signal || events.length,
      symptom: stageBuckets.symptom || 7,
      hypothesis: hyps.length || stageBuckets.hypothesis,
      evidence: stageBuckets.evidence,
      scoring: stageBuckets.scoring || hyps.length,
      reasoning: stageBuckets.reasoning,
      result: `${topScore}%`,
      action_plan: stageBuckets.action_plan,
    };
  };

  const start = useCallback((xcv: string) => {
    clear();
    setLoading(true);
    setRunning(false);
    setError(null);
    setTraceLines([]);
    setTraceCount(0);
    setStage(null);
    setReached([]);
    setConfidence([]);
    setHypotheses([]);
    setRootCause(null);
    setSignalTitle('');
    setNodeCounts({ signal: 0, symptom: 0, hypothesis: 0, evidence: 0, scoring: 0, reasoning: 0, result: '—' });
    setSymptoms([]);
    setEventCount(0);
    setElapsed(0);

    fetchTraceEvents(xcv)
      .then((events) => {
        setLoading(false);
        setEventCount(events.length);

        // Extract signal title
        const sigEvent = events.find((e) => e.SignalTitle || e.EventName === 'SignalReceived');
        setSignalTitle(sigEvent?.SignalTitle ?? sigEvent?.Content ?? 'Investigation');

        // Build trace lines (suppress noise)
        const lines: TraceLine[] = [];
        for (const ev of events) {
          if (SUPPRESSED.has(ev.EventName)) continue;
          const agent =
            (typeof ev.AgentName === 'string' && ev.AgentName) ||
            (typeof (ev as { agent_name?: unknown }).agent_name === 'string'
              ? (ev as { agent_name?: string }).agent_name
              : undefined) ||
            undefined;
          const tool =
            (typeof ev.ToolName === 'string' && ev.ToolName) ||
            (typeof (ev as { tool_invoked?: unknown }).tool_invoked === 'string'
              ? (ev as { tool_invoked?: string }).tool_invoked
              : undefined) ||
            undefined;
          const llmResp = (ev as { llm_response_text?: unknown }).llm_response_text;
          const isLlm = typeof llmResp === 'string' && llmResp.trim().length > 0;
          lines.push({
            stage: eventStage(ev),
            text: lineText(ev),
            type: lineType(ev),
            icon: iconFor(ev),
            agent,
            tool,
            isLlm,
          });
        }
        setTraceLines(lines);

        // Extract structured data
        const hyps = extractHypotheses(events);
        setHypotheses(hyps);
        setConfidence(extractConfidence(hyps));
        setSymptoms(extractSymptoms(events));

        const counts = computeCounts(events, hyps);
        setNodeCounts(counts);

        // Replay: stream lines with timing
        setRunning(true);
        t0.current = Date.now();
        const tick = () => {
          setElapsed((Date.now() - t0.current) / 1000);
          raf.current = requestAnimationFrame(tick);
        };
        raf.current = requestAnimationFrame(tick);

        // Map stages to line ranges for progressive reveal
        const stageOrder = [...new Set(lines.map((l) => l.stage))];
        let lineIdx = 0;
        let delay = 300;
        const totalDuration = 12000; // 12s replay
        const perLine = Math.max(20, totalDuration / (lines.length + 1));

        stageOrder.forEach((s) => {
          const stageLines = lines.filter((l) => l.stage === s);
          // Advance to this stage
          timers.current.push(setTimeout(() => {
            const stageI = INVESTIGATION_STAGES.indexOf(s);
            if (stageI >= 0) {
              setStage(s);
              setReached(INVESTIGATION_STAGES.slice(0, stageI + 1));
            }
          }, delay));

          stageLines.forEach(() => {
            const idx = lineIdx + 1;
            timers.current.push(setTimeout(() => setTraceCount(idx), delay));
            lineIdx++;
            delay += perLine;
          });
        });

        // Extract root cause after replay
        timers.current.push(setTimeout(() => {
          const rc = extractRootCause(events, hyps);
          setRootCause(rc);
          setStage('result');
          setReached([...INVESTIGATION_STAGES]);
          setTraceCount(lines.length);
          setRunning(false);
          cancelAnimationFrame(raf.current);
        }, delay + 200));
      })
      .catch((err) => {
        setLoading(false);
        setError(err.message);
      });
  }, [clear]);

  return {
    stage,
    reached,
    traceCount,
    traceLines,
    confidence,
    hypotheses,
    rootCause,
    signalTitle,
    nodeCounts,
    symptoms,
    loading,
    running,
    error,
    elapsed,
    eventCount,
    start,
  };
}
