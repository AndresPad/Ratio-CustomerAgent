/**
 * useLiveInvestigation — reducer-backed hook that consumes the
 * `/api/run` SSE stream and derives an animated UI state:
 *
 *   Signal → Evaluation → Hypothesis → Scoring → Selection
 *          → Tool Execution → Summary
 *
 * The reducer is intentionally tolerant: the backend emits a mix of
 * pipeline-level events (type: pipeline_started, ...) and AgentLogger
 * events keyed by EventName (SignalEvaluationStart, HypothesisScoring,
 * HypothesisSelected, PhaseTransition, SpeakerSelected, ToolCall, ...)
 * plus investigation_runner events (hypothesis_evaluation_started,
 * investigation_agent_response, investigation_complete).
 *
 * We map all of those into a small, UI-shaped state object.
 */
import { useCallback, useEffect, useReducer, useRef } from 'react';
import {
  kindOf,
  type LiveEvent,
  type RawLiveEvent,
  type RunPipelineRequest,
} from '../api/liveOrchestrationClient';
import {
  streamOrchestration,
  type OrchestrationMode,
} from '../api/orchestrationSource';

/** The 7 executive-visible stages of the orchestration. */
export const STAGES = [
  'signal',
  'evaluation',
  'hypothesis',
  'scoring',
  'selection',
  'tool_execution',
  'summary',
] as const;
export type Stage = (typeof STAGES)[number];

export const STAGE_LABELS: Record<Stage, string> = {
  signal: 'Signal',
  evaluation: 'Evaluation',
  hypothesis: 'Hypothesis',
  scoring: 'Scoring',
  selection: 'Selection',
  tool_execution: 'Tool Execution',
  summary: 'Summary',
};

export const STAGE_ICONS: Record<Stage, string> = {
  signal: 'fa-satellite-dish',
  evaluation: 'fa-filter',
  hypothesis: 'fa-lightbulb',
  scoring: 'fa-chart-bar',
  selection: 'fa-check-double',
  tool_execution: 'fa-cogs',
  summary: 'fa-flag-checkered',
};

export interface SignalTypeRow {
  signal_type_id: string;
  signal_name: string;
  has_data: boolean;
  row_count: number;
  activated_count: number;
  max_strength: number;
  best_confidence: string;
  activated_slis: string;
}

export interface CompoundRow {
  compound_id: string;
  compound_name: string;
  activated: boolean;
  strength: number;
  contributing_types: string;
  confidence: string;
  rationale: string;
}

export interface HypothesisRow {
  hypothesis_id: string;
  statement: string;
  match_score: number;
  rank: number;
  matched_symptoms: string;
  evidence_needed: string;
  status: string; // ACTIVE, SUPPORTED, REFUTED, etc.
  confidence: number; // 0..1
  selected: boolean;
}

export interface ToolCallRow {
  tool: string;
  parameters: string;
  row_count: number;
  duration_ms: number;
  error: string;
  agent?: string;
  query?: string;
  ts: number;
}

export interface AgentResponseRow {
  agent: string;
  text: string;
  phase: string;
  symptoms_count: number;
  hypotheses_count: number;
  evidence_count: number;
  ts: number;
}

export interface SummaryRow {
  investigation_id: string;
  symptoms_count: number;
  hypotheses_count: number;
  evidence_count: number;
  actions_count: number;
  evidence_cycles: number;
  duration_seconds: number;
}

export interface LiveState {
  running: boolean;
  done: boolean;
  error: string | null;
  xcv: string | null;
  stage: Stage;
  stagesReached: Stage[];
  /** Progress percent within the currently active stage (0..100). */
  stageProgress: number;

  // Pipeline context
  customer_name: string;
  service_tree_id: string;

  // Signal stage
  signalTypes: SignalTypeRow[];
  compounds: CompoundRow[];
  signalDecision?: {
    action: string;
    signal_count: number;
    compound_count: number;
  };

  // Investigation stage
  investigationId: string;
  currentPhase: string;
  phaseHistory: string[];
  currentSpeaker: string;
  speakerReason: string;
  evidenceCycles: number;
  /** Derived 0..1 progress for collecting evidence. */
  evidenceProgress: number;

  // Hypotheses
  hypothesisScoring?: {
    input_symptom_count: number;
    output_hypothesis_count: number;
    top_hypothesis_id: string;
    top_score: number;
  };
  hypotheses: HypothesisRow[];
  selectedHypothesisId: string;

  // Tools + agent turns
  toolCalls: ToolCallRow[];
  agentTurns: AgentResponseRow[];

  // Summary
  summary: SummaryRow | null;

  // Raw feed — bounded for sanity.
  events: LiveEvent[];
}

const INITIAL_STATE: LiveState = {
  running: false,
  done: false,
  error: null,
  xcv: null,
  stage: 'signal',
  stagesReached: [],
  stageProgress: 0,
  customer_name: '',
  service_tree_id: '',
  signalTypes: [],
  compounds: [],
  investigationId: '',
  currentPhase: '',
  phaseHistory: [],
  currentSpeaker: '',
  speakerReason: '',
  evidenceCycles: 0,
  evidenceProgress: 0,
  hypotheses: [],
  selectedHypothesisId: '',
  toolCalls: [],
  agentTurns: [],
  summary: null,
  events: [],
};

/** Agents whose activity implies we're in the Tool Execution stage.
 *  Matched loosely (substring, case-insensitive) against `NextSpeaker`. */
const TOOL_EXECUTION_AGENTS = [
  'sli_collector',
  'incident_collector',
  'support_collector',
  'reasoner',
  'action_planner',
] as const;

const MAX_EVENTS = 400;

/** Map a phase value from the backend (`triage`, `collecting`, ...) to our
 *  executive stage. Returns undefined for phases we don't surface. */
function stageForPhase(phase: string | undefined): Stage | undefined {
  switch (phase) {
    case 'triage':
    case 'hypothesizing':
      return 'scoring';
    case 'planning':
      return 'selection';
    case 'collecting':
    case 'reasoning':
    case 'acting':
      return 'tool_execution';
    case 'notifying':
    case 'complete':
      return 'summary';
    default:
      return undefined;
  }
}

function advanceStage(state: LiveState, next: Stage, progress = 100): LiveState {
  const nextIdx = STAGES.indexOf(next);
  const curIdx = STAGES.indexOf(state.stage);
  // Never move backwards.
  const stage = nextIdx >= curIdx ? next : state.stage;
  const stagesReached = [...state.stagesReached];
  for (let i = 0; i <= STAGES.indexOf(stage); i++) {
    if (!stagesReached.includes(STAGES[i])) stagesReached.push(STAGES[i]);
  }
  return { ...state, stage, stagesReached, stageProgress: progress };
}

function num(x: unknown, def = 0): number {
  if (typeof x === 'number') return x;
  if (typeof x === 'string') {
    const n = parseFloat(x);
    return isNaN(n) ? def : n;
  }
  return def;
}

function str(x: unknown, def = ''): string {
  if (x === null || x === undefined) return def;
  return String(x);
}

/** Main reducer. */
function reduce(state: LiveState, action: Action): LiveState {
  switch (action.type) {
    case 'reset':
      return { ...INITIAL_STATE, running: true };
    case 'error':
      return { ...state, running: false, error: action.error, done: true };
    case 'finish':
      return {
        ...state,
        running: false,
        done: true,
        stage: 'summary',
        stagesReached: STAGES.slice(),
        stageProgress: 100,
      };
    case 'event':
      return reduceEvent(state, action.event);
    default:
      return state;
  }
}

type Action =
  | { type: 'reset' }
  | { type: 'event'; event: LiveEvent }
  | { type: 'error'; error: string }
  | { type: 'finish' };

/** Per-event reducer. */
function reduceEvent(state: LiveState, evt: LiveEvent): LiveState {
  // Always append to the raw feed (bounded).
  const events = state.events.length >= MAX_EVENTS
    ? [...state.events.slice(-MAX_EVENTS + 1), evt]
    : [...state.events, evt];
  let next: LiveState = { ...state, events };
  const props = (evt.Properties ?? {}) as Record<string, unknown>;

  const pick = <T = unknown>(k: string): T | undefined =>
    (evt[k] ?? props[k]) as T | undefined;

  switch (evt.kind) {
    case 'pipeline_started': {
      const xcv = str(evt.xcv);
      next = { ...next, xcv, running: true };
      next = advanceStage(next, 'signal', 10);
      return next;
    }

    case 'SignalEvaluationStart': {
      next = {
        ...next,
        customer_name: str(pick('CustomerName')),
        service_tree_id: str(pick('ServiceTreeId')),
      };
      return advanceStage(next, 'signal', 30);
    }

    case 'MCPCollectionCall': {
      const call: ToolCallRow = {
        tool: str(pick('Tool')),
        parameters: str(pick('Parameters')),
        row_count: num(pick('RowCount')),
        duration_ms: num(pick('DurationMs')),
        error: str(pick('Error')),
        agent: 'signal_builder',
        ts: evt.receivedAt,
      };
      next = { ...next, toolCalls: [...next.toolCalls, call] };
      // Still in signal gathering — nudge stage progress.
      return advanceStage(next, 'signal', Math.min(70, 40 + next.toolCalls.length * 5));
    }

    case 'SignalTypeEvaluated': {
      const row: SignalTypeRow = {
        signal_type_id: str(pick('SignalTypeId')),
        signal_name: str(pick('SignalName')),
        has_data: Boolean(pick('HasData')),
        row_count: num(pick('RowCount')),
        activated_count: num(pick('ActivatedCount')),
        max_strength: num(pick('MaxStrength')),
        best_confidence: str(pick('BestConfidence')),
        activated_slis: str(pick('ActivatedSLIs')),
      };
      const signalTypes = next.signalTypes.filter(s => s.signal_type_id !== row.signal_type_id);
      signalTypes.push(row);
      return { ...next, signalTypes };
    }

    case 'CompoundEvaluated': {
      const row: CompoundRow = {
        compound_id: str(pick('CompoundId')),
        compound_name: str(pick('CompoundName')),
        activated: Boolean(pick('Activated')),
        strength: num(pick('Strength')),
        contributing_types: str(pick('ContributingTypes')),
        confidence: str(pick('Confidence')),
        rationale: str(pick('Rationale')),
      };
      const compounds = next.compounds.filter(c => c.compound_id !== row.compound_id);
      compounds.push(row);
      next = { ...next, compounds };
      return advanceStage(next, 'evaluation', 60);
    }

    case 'SignalDecision': {
      const decision = {
        action: str(pick('Action')),
        signal_count: num(pick('SignalCount')),
        compound_count: num(pick('CompoundCount')),
      };
      next = { ...next, signalDecision: decision };
      return advanceStage(next, 'evaluation', 100);
    }

    case 'signal_evaluation_complete': {
      return advanceStage(next, 'evaluation', 100);
    }

    case 'investigations_starting':
    case 'investigation_started':
    case 'InvestigationCreated':
    case 'WorkflowStarted': {
      if (evt.kind === 'investigation_started') {
        next = {
          ...next,
          investigationId: str(evt.investigation_id),
          customer_name: str(evt.customer_name) || next.customer_name,
          service_tree_id: str(evt.service_tree_id) || next.service_tree_id,
        };
      }
      return advanceStage(next, 'hypothesis', 20);
    }

    case 'PhaseTransition': {
      const to = str(pick('ToPhase'));
      const from = str(pick('FromPhase'));
      const phaseHistory = [...next.phaseHistory];
      if (to && !phaseHistory.includes(to)) phaseHistory.push(to);
      next = { ...next, currentPhase: to || next.currentPhase, phaseHistory };
      const st = stageForPhase(to) ?? stageForPhase(from);
      if (st) return advanceStage(next, st, 50);
      return next;
    }

    case 'HypothesisScoring': {
      const scoring = {
        input_symptom_count: num(pick('InputSymptomCount')),
        output_hypothesis_count: num(pick('OutputHypothesisCount')),
        top_hypothesis_id: str(pick('TopHypothesisId')),
        top_score: num(pick('TopScore')),
      };
      next = { ...next, hypothesisScoring: scoring };
      return advanceStage(next, 'scoring', 70);
    }

    case 'HypothesisSelected': {
      const id = str(pick('HypothesisId'));
      const row: HypothesisRow = {
        hypothesis_id: id,
        statement: str(pick('Statement')),
        match_score: num(pick('MatchScore')),
        rank: num(pick('Rank')),
        matched_symptoms: str(pick('MatchedSymptoms')),
        evidence_needed: str(pick('EvidenceNeeded')),
        status: 'ACTIVE',
        confidence: 0,
        selected: true,
      };
      const hypotheses = next.hypotheses.filter(h => h.hypothesis_id !== id);
      hypotheses.push(row);
      next = { ...next, hypotheses, selectedHypothesisId: id };
      return advanceStage(next, 'selection', 80);
    }

    case 'hypothesis_evaluation_started': {
      const id = str(evt.hypothesis_id);
      if (!id) return next;
      const existing = next.hypotheses.find(h => h.hypothesis_id === id);
      const row: HypothesisRow = existing ?? {
        hypothesis_id: id,
        statement: str(evt.statement),
        match_score: num(evt.match_score),
        rank: num(evt.rank),
        matched_symptoms: '',
        evidence_needed: '',
        status: 'ACTIVE',
        confidence: 0,
        selected: true,
      };
      row.selected = true;
      row.statement = row.statement || str(evt.statement);
      row.match_score = row.match_score || num(evt.match_score);
      row.rank = row.rank || num(evt.rank);
      const hypotheses = [...next.hypotheses.filter(h => h.hypothesis_id !== id), row];
      next = { ...next, hypotheses, selectedHypothesisId: id };
      return advanceStage(next, 'selection', 90);
    }

    case 'HypothesisTransition': {
      const id = str(pick('HypothesisId'));
      const newStatus = str(pick('NewStatus'));
      const conf = num(pick('Confidence'));
      const hypotheses = next.hypotheses.map(h =>
        h.hypothesis_id === id ? { ...h, status: newStatus || h.status, confidence: conf || h.confidence } : h,
      );
      next = { ...next, hypotheses };
      return next;
    }

    case 'SpeakerSelected': {
      const last = str(pick('LastSpeaker'));
      const nextSpeaker = str(pick('NextSpeaker'));
      next = {
        ...next,
        currentSpeaker: nextSpeaker || next.currentSpeaker,
        speakerReason: str(pick('Reason')),
      };
      // Roughly: if collectors/reasoner are speaking, we're in tool_execution.
      if (TOOL_EXECUTION_AGENTS.some(a => nextSpeaker.toLowerCase().includes(a))) {
        return advanceStage(next, 'tool_execution', 40);
      }
      if (last) {
        // small boost to existing progress
        return { ...next, stageProgress: Math.min(95, next.stageProgress + 2) };
      }
      return next;
    }

    case 'EvidenceCycle': {
      const cycle = num(pick('CycleNumber'));
      const erIds = str(pick('ERIds')).split(',').map(s => s.trim()).filter(Boolean);
      // Assume at most ~2 cycles, target 100% when cycle 2 completes.
      const evidenceProgress = Math.min(1, cycle / 2);
      next = {
        ...next,
        evidenceCycles: Math.max(next.evidenceCycles, cycle),
        evidenceProgress,
      };
      // Touch tool calls with the er ids as markers.
      const marker: ToolCallRow = {
        tool: `evidence_cycle_${cycle}`,
        parameters: `ers=${erIds.join(', ')}`,
        row_count: erIds.length,
        duration_ms: 0,
        error: '',
        agent: 'evidence_planner',
        ts: evt.receivedAt,
      };
      next = { ...next, toolCalls: [...next.toolCalls, marker] };
      return advanceStage(next, 'tool_execution', 60 + cycle * 15);
    }

    case 'ToolCall': {
      const call: ToolCallRow = {
        tool: str(pick('Tool')),
        parameters: str(pick('Arguments') ?? pick('Parameters')),
        row_count: num(pick('RowCount')),
        duration_ms: num(pick('DurationMs')),
        error: str(pick('Error')),
        agent: str(pick('Agent') ?? pick('AgentName') ?? next.currentSpeaker),
        query: str(pick('QueryText') ?? pick('Query')),
        ts: evt.receivedAt,
      };
      next = { ...next, toolCalls: [...next.toolCalls, call] };
      return advanceStage(next, 'tool_execution', Math.min(90, 50 + next.toolCalls.length * 2));
    }

    case 'investigation_agent_response':
    case 'AgentResponse': {
      const agent = str(evt.agent ?? pick('AgentName') ?? pick('Agent'));
      const text = str(evt.text ?? pick('ResponseText'));
      const phase = str(evt.phase ?? pick('Phase'));
      const turn: AgentResponseRow = {
        agent,
        text,
        phase,
        symptoms_count: num(evt.symptoms_count),
        hypotheses_count: num(evt.hypotheses_count),
        evidence_count: num(evt.evidence_count),
        ts: evt.receivedAt,
      };
      next = { ...next, agentTurns: [...next.agentTurns, turn] };
      const st = stageForPhase(phase);
      if (st) return advanceStage(next, st, Math.min(95, next.stageProgress + 3));
      return next;
    }

    case 'InvestigationComplete': {
      const summary: SummaryRow = {
        investigation_id: str(pick('InvestigationId')),
        symptoms_count: num(pick('SymptomsCount')),
        hypotheses_count: num(pick('HypothesesCount')),
        evidence_count: num(pick('EvidenceCount')),
        actions_count: num(pick('ActionsCount')),
        evidence_cycles: num(pick('EvidenceCycles')),
        duration_seconds: num(pick('DurationSeconds')),
      };
      next = {
        ...next,
        summary,
        evidenceProgress: 1,
      };
      return advanceStage(next, 'summary', 100);
    }

    case 'pipeline_complete': {
      return {
        ...next,
        running: false,
        done: true,
        stage: 'summary',
        stagesReached: STAGES.slice(),
        stageProgress: 100,
      };
    }

    case 'pipeline_error':
    case 'investigation_error':
    case 'investigation_workflow_error': {
      return {
        ...next,
        running: false,
        done: true,
        error: str(evt.error) || 'Pipeline error',
      };
    }

    default:
      return next;
  }
}

export interface LiveStartOptions extends RunPipelineRequest {
  /** Event source — 'live' (default), 'replay', or 'mock'. */
  mode?: OrchestrationMode;
  /** Correlation id required when mode is 'replay'. */
  xcv?: string;
  /** Optional replay agent-name filter (e.g. 'narrator'). */
  agentFilter?: string;
  /** Client-side pacing for replay mode (ms between frames). Default 0. */
  pollPacingMs?: number;
}

export interface UseLiveInvestigation {
  state: LiveState;
  start: (opts?: LiveStartOptions) => Promise<void>;
  stop: () => void;
  reset: () => void;
}

export function useLiveInvestigation(): UseLiveInvestigation {
  const [state, dispatch] = useReducer(reduce, INITIAL_STATE);
  const abortRef = useRef<AbortController | null>(null);

  const reset = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    dispatch({ type: 'finish' });
  }, []);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  const start = useCallback(async (opts: LiveStartOptions = {}) => {
    abortRef.current?.abort();
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    dispatch({ type: 'reset' });

    const { mode = 'live', xcv, customer_name, service_tree_id, agentFilter, pollPacingMs } = opts;

    try {
      for await (const evt of streamOrchestration(
        { mode, xcv, customer_name, service_tree_id, agentFilter, pollPacingMs },
        ctrl.signal,
      )) {
        const raw = evt as RawLiveEvent;
        const normalized: LiveEvent = { ...raw, kind: kindOf(raw), receivedAt: Date.now() };
        dispatch({ type: 'event', event: normalized });
      }
      dispatch({ type: 'finish' });
    } catch (err: unknown) {
      if (err instanceof Error && err.name === 'AbortError') {
        dispatch({ type: 'finish' });
        return;
      }
      dispatch({ type: 'error', error: err instanceof Error ? err.message : String(err) });
    } finally {
      if (abortRef.current === ctrl) abortRef.current = null;
    }
  }, []);

  // Abort on unmount.
  useEffect(() => () => {
    abortRef.current?.abort();
    abortRef.current = null;
  }, []);

  return { state, start, stop, reset };
}
