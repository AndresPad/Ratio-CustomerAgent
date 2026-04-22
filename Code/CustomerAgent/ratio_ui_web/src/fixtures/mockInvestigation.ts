/**
 * Mock investigation fixture — a scripted event timeline reduced from a
 * real trace captured in Application Insights.
 *
 * Used by orchestrationSource.ts when the user selects "Mock" on the
 * DataSourceToggle. Emits frames in the same flat shape as the live
 * pipeline ({ type | EventName, ...Properties }) so the Theatre and
 * Live Orchestration reducers consume it unchanged.
 */
export interface MockEvent {
  // Arbitrary flat fields; typed as any-shape on purpose so the reducer
  // sees it identical to a replay / live frame.
  [key: string]: unknown;
}

/** Reference investigation: BlackRock, Inc / ScaleSet platform triage.
 *  Mirrors xcv 8b27bf8e-… captured on 2026-04-21. */
export const MOCK_XCV = 'mock-8b27bf8e-blackrock-scaleset';

export const MOCK_EVENTS: MockEvent[] = [
  { type: 'pipeline_started', xcv: MOCK_XCV, source: 'mock' },
  {
    EventName: 'SignalEvaluationStart',
    Service: 'AGENT_SERVER',
    xcv: MOCK_XCV,
    CustomerName: 'BlackRock, Inc',
    ServiceTreeId: '49c39e84-285c-45e1-9008-ac6b217161e2',
  },
  {
    EventName: 'SignalTypeEvaluated',
    Service: 'AGENT_SERVER',
    xcv: MOCK_XCV,
    SignalTypeId: 'SIG-TYPE-1',
    SignalName: 'Customer resource SLI breach',
    HasData: true,
    RowCount: 33,
    ActivatedCount: 12,
    MaxStrength: 0.92,
    BestConfidence: 'HIGH',
    ActivatedSLIs: 'availability, latency',
  },
  {
    EventName: 'SignalTypeEvaluated',
    Service: 'AGENT_SERVER',
    xcv: MOCK_XCV,
    SignalTypeId: 'SIG-TYPE-4',
    SignalName: 'Dependency service SLI breach',
    HasData: true,
    RowCount: 1970,
    ActivatedCount: 491,
    MaxStrength: 0.88,
    BestConfidence: 'HIGH',
    ActivatedSLIs: 'error-rate, saturation',
  },
  {
    EventName: 'CompoundEvaluated',
    xcv: MOCK_XCV,
    CompoundId: 'CMP-HEALTH-DEGRADE',
    CompoundName: 'Customer health degradation (multi-signal)',
    Activated: true,
    Strength: 0.9,
    ContributingTypes: 'SIG-TYPE-1, SIG-TYPE-4',
    Confidence: 'HIGH',
    Rationale: 'Multiple SLI breaches + dependency impact co-occur within monitoring window.',
  },
  {
    EventName: 'SignalDecision',
    xcv: MOCK_XCV,
    Action: 'investigate',
    SignalCount: 2,
    CompoundCount: 1,
  },
  { type: 'signal_evaluation_complete', xcv: MOCK_XCV },
  { type: 'investigations_starting', xcv: MOCK_XCV, count: 1 },
  { type: 'investigation_started', xcv: MOCK_XCV, investigation_id: 'INV-001', customer: 'BlackRock, Inc' },
  { EventName: 'InvestigationCreated', xcv: MOCK_XCV, investigation_id: 'INV-001' },
  { EventName: 'WorkflowStarted', xcv: MOCK_XCV, Participants: 'triage_agent, investigation_orchestrator, evidence_planner, reasoner, sli_collector, incident_collector, support_collector' },
  { EventName: 'SymptomTemplatesLoaded', xcv: MOCK_XCV, count: 12 },

  // ── Triage ──
  { EventName: 'PhaseTransition', xcv: MOCK_XCV, AgentName: 'triage_agent', FromPhase: 'initializing', ToPhase: 'triage' },
  { EventName: 'SpeakerSelected', xcv: MOCK_XCV, NextSpeaker: 'triage_agent' },
  { EventName: 'LLMCall', xcv: MOCK_XCV, AgentName: 'triage_agent' },
  {
    EventName: 'AgentResponse',
    xcv: MOCK_XCV,
    AgentName: 'triage_agent',
    ToPhase: 'triage',
    ResponseText: 'Confirmed symptoms: SYM-SLI-001, SYM-SLI-002, SYM-SLI-005, SYM-DEP-001, SYM-DEP-002. Severity: CRITICAL.',
  },

  // ── Hypothesizing ──
  { EventName: 'PhaseTransition', xcv: MOCK_XCV, FromPhase: 'triage', ToPhase: 'hypothesizing' },
  {
    EventName: 'HypothesisScoring',
    xcv: MOCK_XCV,
    input_symptom_count: 5,
    output_hypothesis_count: 6,
    top_hypothesis_id: 'HYP-DEP-001',
    top_score: 4.2,
  },
  { EventName: 'HypothesisSelected', xcv: MOCK_XCV, HypothesisId: 'HYP-DEP-001', hypothesis_id: 'HYP-DEP-001', statement: 'Dependency services are causing customer impact', match_score: 4.2, rank: 1 },
  { EventName: 'HypothesisSelected', xcv: MOCK_XCV, HypothesisId: 'HYP-SLI-002', hypothesis_id: 'HYP-SLI-002', statement: 'Isolated workload overload for customer resources', match_score: 3.5, rank: 2 },
  { EventName: 'HypothesisSelected', xcv: MOCK_XCV, HypothesisId: 'HYP-SLI-003', hypothesis_id: 'HYP-SLI-003', statement: 'Multi-SLI systemic impact', match_score: 3.1, rank: 3 },

  // ── Evidence collection + refute loop (compressed) ──
  ...(['HYP-DEP-001', 'HYP-SLI-002', 'HYP-SLI-003'].flatMap((hid, idx) => [
    { EventName: 'HypothesisTransition', xcv: MOCK_XCV, HypothesisId: hid, OldStatus: 'ACTIVE', NewStatus: 'REFUTED', Confidence: 0.1 },
    { EventName: 'PhaseTransition', xcv: MOCK_XCV, FromPhase: idx === 0 ? 'hypothesizing' : 'reasoning', ToPhase: 'acting' },
    { EventName: 'SpeakerSelected', xcv: MOCK_XCV, NextSpeaker: 'evidence_planner' },
    { EventName: 'AgentResponse', xcv: MOCK_XCV, AgentName: 'evidence_planner', ResponseText: `Plan evidence for ${hid}: sli_collector, incident_collector, support_collector` },
    { EventName: 'ToolCall', xcv: MOCK_XCV, AgentName: 'sli_collector', Tool: 'collect_impacted_resource_customer_tool' },
    { EventName: 'QueryExecuted', xcv: MOCK_XCV, Tool: 'collect_impacted_resource_customer_tool', RowCount: 0, DurationMs: 9856 },
    { EventName: 'ToolCallEnd', xcv: MOCK_XCV, Tool: 'collect_impacted_resource_customer_tool' },
    { EventName: 'ToolCall', xcv: MOCK_XCV, AgentName: 'support_collector', Tool: 'collect_support_request_tool' },
    { EventName: 'QueryExecuted', xcv: MOCK_XCV, Tool: 'collect_support_request_tool', RowCount: 0, DurationMs: 2400 },
    { EventName: 'ToolCallEnd', xcv: MOCK_XCV, Tool: 'collect_support_request_tool' },
    { EventName: 'PhaseTransition', xcv: MOCK_XCV, FromPhase: 'acting', ToPhase: 'reasoning' },
    { EventName: 'SpeakerSelected', xcv: MOCK_XCV, NextSpeaker: 'reasoner' },
    {
      EventName: 'AgentResponse',
      xcv: MOCK_XCV,
      AgentName: 'reasoner',
      ResponseText: `${hid} REFUTED — no supporting evidence found across SLI / incident / support data.`,
      verdict: 'refuted',
      confidence: 0.1,
    },
  ])),

  // ── Completion ──
  { EventName: 'PhaseTransition', xcv: MOCK_XCV, FromPhase: 'reasoning', ToPhase: 'complete' },
  { EventName: 'SpeakerSelected', xcv: MOCK_XCV, NextSpeaker: 'investigation_orchestrator' },
  {
    EventName: 'AgentResponse',
    xcv: MOCK_XCV,
    AgentName: 'investigation_orchestrator',
    ResponseText: 'No hypotheses confirmed. Investigation inconclusive.',
  },
  { EventName: 'InvestigationComplete', xcv: MOCK_XCV, resolution: 'inconclusive',
    InvestigationId: 'INV-001',
    SymptomsCount: 5,
    HypothesesCount: 3,
    EvidenceCount: 6,
    ActionsCount: 0,
    EvidenceCycles: 3,
    DurationSeconds: 42.7,
  },
  { EventName: 'RequestEnd', xcv: MOCK_XCV },
  { type: 'pipeline_complete', xcv: MOCK_XCV },
];
