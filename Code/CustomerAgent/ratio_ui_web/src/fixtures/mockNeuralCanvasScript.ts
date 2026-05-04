/**
 * Mock investigation scripts for the Neural Canvas demo recording.
 *
 * Each script describes a complete investigation playthrough for a single
 * service: chat lines that reveal in order, the symptoms / hypotheses /
 * evidence the relationship graph fills in, the sandbox runs, and the
 * final root cause + action plan.
 *
 * The SQL script is the lead — its copy aligns with Rashmi's recording
 * narration (85% supported "localized service issue", 25% refuted
 * "capacity exhaustion"). AKS / VM / OpenAI run in parallel with their
 * own concise stories so all four service tabs complete and the demo
 * audience sees all of them resolve.
 *
 * The `useMockReplayFlow` hook turns these scripts into a paced replay
 * (setTimeout-driven) that produces the same `ReplayFlowResult` shape
 * the live `useReplayFlow` returns, so the page renders identically.
 */
import type { TraceLine, InvestigationStage, Hypothesis, RootCause } from '../pages/customer-agent/ChaInvestigationFlowPage';
import type { LiveSymptom, SandboxRun } from '../hooks/useReplayFlow';
import type { ReplayServiceOption } from '../api/orchestrationSource';

export interface MockTraceLine extends TraceLine {
  /** Cumulative ms offset from start when this line should reveal. */
  atMs: number;
}

export interface MockServiceScript {
  service: ReplayServiceOption;
  signalTitle: string;
  symptoms: LiveSymptom[];
  hypotheses: Hypothesis[];
  sandboxRuns: SandboxRun[];
  rootCause: RootCause;
  traceLines: MockTraceLine[];
  stageTimeline: { stage: InvestigationStage; atMs: number }[];
  /** Total duration of this scripted playback (ms). After this, running flips to false and the action plan appears. */
  totalDurationMs: number;
}

/* ── Hypothesis colors, matching extractHypotheses in useReplayFlow.ts ── */
const HYP_BLUE = '#3498db';   // HYP-SLI
const HYP_RED = '#e74c3c';    // HYP-DEP

/* ── Helpers ── */

function tl(
  atMs: number,
  stage: InvestigationStage,
  agent: string,
  text: string,
  opts: { tool?: string; isLlm?: boolean; type?: TraceLine['type'] } = {},
): MockTraceLine {
  return {
    atMs,
    stage,
    agent,
    text,
    isLlm: opts.isLlm ?? true,
    type: opts.type ?? 'normal',
    tool: opts.tool,
  };
}

function sandboxRun(
  id: string,
  agent: string,
  code: string,
  stdout: string,
  generatedAtMs: number,
  completedAtMs: number,
  durationSeconds: number,
): SandboxRun {
  return {
    id,
    agent,
    language: 'python',
    code,
    generatedAtMs,
    completedAtMs,
    stdout,
    stderr: '',
    success: true,
    durationSeconds,
    error: null,
  };
}

/* ── SQL — the canonical demo (matches Rashmi's narration) ── */

const SQL_SERVICE_TREE_ID = '92df60d8-b6e8-42c4-a3e1-7d09fbbd8564';
const SQL_XCV = '92df60d8-b6e8-42c4-a3e1-7d09fbbd8564';

const SQL_SCRIPT: MockServiceScript = {
  service: {
    service_tree_id: SQL_SERVICE_TREE_ID,
    service_name: 'SQL',
    xcv: SQL_XCV,
  },
  signalTitle: 'BlackRock — Azure SQL DB SLI Breach (West US)',
  symptoms: [
    { title: 'SLI impact detected across multiple regions including West US and Central India' },
    { title: 'Severe SLI degradation in West US' },
    { title: 'Support case filed by BlackRock for Azure SQL DB' },
  ],
  hypotheses: [
    {
      id: 'HYP-SLI-006',
      description: 'Localized service issue — possibly a misconfiguration, code defect, or resource limit hit',
      score: 85,
      status: 'supported',
      badgeColor: HYP_BLUE,
    },
    {
      id: 'HYP-SLI-003',
      description: 'Capacity exhaustion across the affected regions',
      score: 25,
      status: 'refuted',
      badgeColor: HYP_BLUE,
    },
  ],
  sandboxRuns: [
    sandboxRun(
      'mock-sql-sbx-1',
      'reasoner',
      `# AIR-O/D severity score for the affected resources
import pandas as pd
df = airo_d.load(window=("2026-04-16T01:00", "2026-04-16T02:00"), customer="BlackRock")
score = df.groupby("resource_id")["impact_norm"].mean().sort_values(ascending=False)
print(score.head(5).to_string())`,
      'resource_id              impact_norm\nsql-westus-prod-01       0.91\nsql-westus-prod-04       0.78\nsql-centralindia-prod-02 0.66\nsql-westus-prod-07       0.42\nsql-eastus-prod-03       0.04',
      14_000,
      16_000,
      2.1,
    ),
    sandboxRun(
      'mock-sql-sbx-2',
      'reasoner',
      `# Hypothesis test: capacity exhaustion?
import scipy.stats as st
t, p = st.ttest_ind(impacted_dtu, baseline_dtu, equal_var=False)
print(f"DTU t-stat={t:.2f}  p={p:.4f}")
print(f"Sustained pegged DTU windows: {pegged}")
`,
      'DTU t-stat=-0.31  p=0.7549\nSustained pegged DTU windows: 0',
      18_500,
      20_500,
      1.8,
    ),
  ],
  rootCause: {
    title: 'Localized SQL service issue confirmed',
    description:
      'The Reasoner confirmed Hypothesis HYP-SLI-006 at 85% confidence. Evidence aligned: AIR-O/D normalized impact scores are heavily concentrated on a small West US cluster while DTU and connection metrics did not exhibit capacity-exhaustion signatures (p=0.75). Capacity exhaustion was actively refuted at 25% confidence.',
    confidence: 85,
    summary: 'Investigated 3 symptoms → 2 hypotheses → 7 evidence items → 3 actions',
  },
  totalDurationMs: 30_000,
  stageTimeline: [
    { stage: 'signal',     atMs: 0 },
    { stage: 'symptom',    atMs: 4_000 },
    { stage: 'hypothesis', atMs: 9_500 },
    { stage: 'evidence',   atMs: 13_500 },
    { stage: 'scoring',    atMs: 19_000 },
    { stage: 'reasoning',  atMs: 22_000 },
    { stage: 'result',     atMs: 27_500 },
    { stage: 'action_plan',atMs: 28_500 },
  ],
  traceLines: [
    tl(    400, 'signal',     'narrator',                   'AHE detected a SQL Database SLI breach for BlackRock in West US.'),
    tl(  1_400, 'signal',     'triage_agent',               'Compound signal: SLI breach + correlated regional latency in West US — pushing past investigation threshold.'),
    tl(  2_400, 'signal',     'investigation_orchestrator', 'Investigation triggered. Routing to evidence planning.'),
    tl(  4_400, 'symptom',    'triage_agent',               'Symptom 1: SLI impact detected across West US and Central India.'),
    tl(  6_000, 'symptom',    'triage_agent',               'Symptom 2: Severe SLI degradation localized to West US.'),
    tl(  7_600, 'symptom',    'triage_agent',               'Symptom 3: Support case filed by BlackRock for Azure SQL DB.'),
    tl(  9_700, 'hypothesis', 'reasoner',                   'Two hypotheses cleared the threshold. Generating HYP-SLI-006: localized service issue.', { tool: 'hypothesis_library' }),
    tl( 11_400, 'hypothesis', 'reasoner',                   'Generating HYP-SLI-003 as alternative: capacity exhaustion across the affected regions.', { tool: 'hypothesis_library' }),
    tl( 13_700, 'evidence',   'evidence_planner',           'Dispatching collectors for both hypotheses.'),
    tl( 14_400, 'evidence',   'sli_collector',              'Pulled SLI metrics — DTU, latency, error rate per region.', { tool: 'sli_collector' }),
    tl( 15_300, 'evidence',   'incident_collector',         'Pulled IcM incident details for the affected scope.', { tool: 'collect_incident_details_tool' }),
    tl( 16_200, 'evidence',   'support_collector',          'Pulled support tickets — BlackRock filed one matching the window.', { tool: 'collect_support_request_tool' }),
    tl( 17_100, 'evidence',   'collect_impacted_resource_customer', 'Mapped blast radius to BlackRock customer resources.', { tool: 'collect_impacted_resource_customer_tool' }),
    tl( 18_000, 'evidence',   'collect_customer_region',    'Resolved customer region context.', { tool: 'collect_customer_region_tool' }),
    tl( 19_200, 'scoring',    'sandbox_coder',              'Writing AIR-O/D analysis code on the fly to score impacted resources.'),
    tl( 20_700, 'scoring',    'python_runner',              'Sandbox Run 1 complete — top resources concentrated in a small West US cluster.', { tool: 'execute_python_in_sandbox_tool' }),
    tl( 22_200, 'reasoning',  'reasoner',                   'Hypothesis HYP-SLI-006 evidence supports a localized issue: impact normalized to a small West US cluster.'),
    tl( 23_200, 'reasoning',  'sandbox_coder',              'Writing capacity-exhaustion test — DTU saturation + sustained pegging windows.'),
    tl( 24_600, 'reasoning',  'python_runner',              'Sandbox Run 2 complete — DTU t-stat -0.31, p=0.75. No sustained pegged DTU windows.', { tool: 'execute_python_in_sandbox_tool' }),
    tl( 26_000, 'reasoning',  'reasoner',                   'Capacity exhaustion is not supported — refuting HYP-SLI-003 at 25% confidence.'),
    tl( 27_700, 'result',     'reasoner',                   'Investigation complete. Root cause: localized SQL service issue. HYP-SLI-006 confirmed at 85% confidence; HYP-SLI-003 refuted at 25%.'),
    tl( 28_700, 'action_plan','action_planner',             'Action 1: Create an IcM ticket for the SQL West US service team.'),
    tl( 29_100, 'action_plan','action_planner',             'Action 2: Schedule a follow-up monitoring check in 30 minutes.'),
    tl( 29_600, 'action_plan','action_planner',             'Action 3: Notify the AED team via email with the investigation summary.'),
  ],
};

/* ── AKS — parallel story (cleaner, shorter) ── */

const AKS_SCRIPT: MockServiceScript = {
  service: {
    service_tree_id: '2ba1f288-b9b9-4fbc-af09-c2066e5ecce4',
    service_name: 'Azure Kubernetes Service',
    xcv: '2ba1f288-b9b9-4fbc-af09-c2066e5ecce4',
  },
  signalTitle: 'BlackRock — AKS Node Pool Degradation (East US 2)',
  symptoms: [
    { title: 'Pod restart storm in BlackRock AKS cluster (East US 2)' },
    { title: 'Node CPU saturation on agent pool 02' },
    { title: 'Internal LB latency spike on default service' },
  ],
  hypotheses: [
    {
      id: 'HYP-DEP-014',
      description: 'Bad workload deploy saturating one agent pool',
      score: 78,
      status: 'supported',
      badgeColor: HYP_RED,
    },
    {
      id: 'HYP-DEP-021',
      description: 'Underlying VM SKU regression',
      score: 22,
      status: 'refuted',
      badgeColor: HYP_RED,
    },
  ],
  sandboxRuns: [],
  rootCause: {
    title: 'AKS pool over-scheduled by recent deploy',
    description:
      'Reasoner confirmed HYP-DEP-014 at 78% — pod density on agentpool-02 spiked at the deploy timestamp; VM SKU regression refuted at 22%.',
    confidence: 78,
    summary: 'Investigated 3 symptoms → 2 hypotheses → 5 evidence items → 2 actions',
  },
  totalDurationMs: 26_000,
  stageTimeline: [
    { stage: 'signal',     atMs: 0 },
    { stage: 'symptom',    atMs: 3_200 },
    { stage: 'hypothesis', atMs: 8_500 },
    { stage: 'evidence',   atMs: 12_500 },
    { stage: 'scoring',    atMs: 17_000 },
    { stage: 'reasoning',  atMs: 19_500 },
    { stage: 'result',     atMs: 23_500 },
    { stage: 'action_plan',atMs: 24_800 },
  ],
  traceLines: [
    tl(    600, 'signal',     'narrator',                   'AHE detected pod restart storm on BlackRock AKS cluster in East US 2.'),
    tl(  1_600, 'signal',     'triage_agent',               'Compound signal — pod restarts + CPU saturation + LB latency above baseline.'),
    tl(  2_400, 'signal',     'investigation_orchestrator', 'Investigation triggered.'),
    tl(  3_300, 'symptom',    'triage_agent',               'Symptom 1: Pod restart storm.'),
    tl(  4_500, 'symptom',    'triage_agent',               'Symptom 2: Node CPU saturation on agentpool-02.'),
    tl(  5_700, 'symptom',    'triage_agent',               'Symptom 3: Internal LB latency spike.'),
    tl(  8_600, 'hypothesis', 'reasoner',                   'HYP-DEP-014: bad workload deploy saturating one agent pool.'),
    tl( 10_400, 'hypothesis', 'reasoner',                   'HYP-DEP-021: underlying VM SKU regression — alternative.'),
    tl( 12_700, 'evidence',   'evidence_planner',           'Dispatching collectors.'),
    tl( 13_500, 'evidence',   'sli_collector',              'AKS SLI pulled — pod restarts + CPU.', { tool: 'sli_collector' }),
    tl( 14_400, 'evidence',   'incident_collector',         'IcM context attached.', { tool: 'collect_incident_details_tool' }),
    tl( 15_300, 'evidence',   'collect_impacted_resource_customer', 'Blast radius mapped.', { tool: 'collect_impacted_resource_customer_tool' }),
    tl( 17_200, 'scoring',    'reasoner',                   'Scoring hypotheses against the evidence.'),
    tl( 19_700, 'reasoning',  'reasoner',                   'Pod density spiked at deploy timestamp on agentpool-02 — supports HYP-DEP-014.'),
    tl( 21_300, 'reasoning',  'reasoner',                   'VM SKU baseline metrics nominal across the pool — refutes HYP-DEP-021.'),
    tl( 23_700, 'result',     'reasoner',                   'Root cause: bad deploy. HYP-DEP-014 confirmed at 78%; HYP-DEP-021 refuted at 22%.'),
    tl( 25_000, 'action_plan','action_planner',             'Action 1: Roll back the offending deploy.'),
    tl( 25_500, 'action_plan','action_planner',             'Action 2: Schedule a follow-up health check in 15 minutes.'),
  ],
};

/* ── VM — progress-only shell (no detailed investigation) ── */

const VM_SCRIPT: MockServiceScript = {
  service: {
    service_tree_id: '7c3e9a44-d28f-4f10-93b1-58a2c4e9d217',
    service_name: 'Virtual Machines',
    xcv: '7c3e9a44-d28f-4f10-93b1-58a2c4e9d217',
  },
  signalTitle: 'BlackRock — Virtual Machines health check',
  symptoms: [],
  hypotheses: [],
  sandboxRuns: [],
  rootCause: {
    title: 'No customer-impacting issue detected',
    description: 'Virtual Machines health check completed with no anomalies above threshold.',
    confidence: 100,
    summary: 'Investigation cleared all gates.',
  },
  totalDurationMs: 22_000,
  stageTimeline: [
    { stage: 'signal',     atMs: 0 },
    { stage: 'symptom',    atMs: 3_000 },
    { stage: 'hypothesis', atMs: 6_500 },
    { stage: 'evidence',   atMs: 10_000 },
    { stage: 'scoring',    atMs: 13_500 },
    { stage: 'reasoning',  atMs: 16_500 },
    { stage: 'result',     atMs: 19_500 },
    { stage: 'action_plan',atMs: 21_000 },
  ],
  traceLines: [
    tl(    400, 'signal',     'narrator',                   'Running scheduled health check for Virtual Machines.'),
    tl(  3_200, 'symptom',    'triage_agent',               'No symptoms detected — VM provisioning latency, allocation success rate within SLO.'),
    tl(  6_700, 'hypothesis', 'reasoner',                   'No active hypotheses — service is operating nominally.'),
    tl( 10_200, 'evidence',   'evidence_planner',           'Evidence sweep complete — no anomalies.'),
    tl( 13_700, 'scoring',    'reasoner',                   'Scoring complete — all metrics within bounds.'),
    tl( 16_700, 'reasoning',  'reasoner',                   'Investigation cleared.'),
    tl( 19_700, 'result',     'reasoner',                   'No customer-impacting issue detected on Virtual Machines.'),
    tl( 21_200, 'action_plan','action_planner',             'No action required.'),
  ],
};

/* ── OpenAI — progress-only shell (no detailed investigation) ── */

const OPENAI_SCRIPT: MockServiceScript = {
  service: {
    service_tree_id: '4f81b62e-7a3c-4abc-9d4e-13b6ca7058f9',
    service_name: 'Azure OpenAI',
    xcv: '4f81b62e-7a3c-4abc-9d4e-13b6ca7058f9',
  },
  signalTitle: 'BlackRock — Azure OpenAI health check',
  symptoms: [],
  hypotheses: [],
  sandboxRuns: [],
  rootCause: {
    title: 'No customer-impacting issue detected',
    description: 'Azure OpenAI health check completed with no anomalies above threshold.',
    confidence: 100,
    summary: 'Investigation cleared all gates.',
  },
  totalDurationMs: 24_000,
  stageTimeline: [
    { stage: 'signal',     atMs: 0 },
    { stage: 'symptom',    atMs: 3_500 },
    { stage: 'hypothesis', atMs: 7_000 },
    { stage: 'evidence',   atMs: 11_000 },
    { stage: 'scoring',    atMs: 14_500 },
    { stage: 'reasoning',  atMs: 17_500 },
    { stage: 'result',     atMs: 21_000 },
    { stage: 'action_plan',atMs: 22_500 },
  ],
  traceLines: [
    tl(    400, 'signal',     'narrator',                   'Running scheduled health check for Azure OpenAI.'),
    tl(  3_700, 'symptom',    'triage_agent',               'No symptoms detected — request rate, error rate, P99 latency within SLO.'),
    tl(  7_200, 'hypothesis', 'reasoner',                   'No active hypotheses — service is operating nominally.'),
    tl( 11_200, 'evidence',   'evidence_planner',           'Evidence sweep complete — no anomalies.'),
    tl( 14_700, 'scoring',    'reasoner',                   'Scoring complete — all metrics within bounds.'),
    tl( 17_700, 'reasoning',  'reasoner',                   'Investigation cleared.'),
    tl( 21_200, 'result',     'reasoner',                   'No customer-impacting issue detected on Azure OpenAI.'),
    tl( 22_700, 'action_plan','action_planner',             'No action required.'),
  ],
};

/* ── Public exports ── */

export const MOCK_SERVICE_SCRIPTS: MockServiceScript[] = [
  SQL_SCRIPT,
  AKS_SCRIPT,
  VM_SCRIPT,
  OPENAI_SCRIPT,
];

export const MOCK_SERVICE_OPTIONS: ReplayServiceOption[] = MOCK_SERVICE_SCRIPTS.map(
  (s) => s.service,
);

export function getMockScriptByXcv(xcv: string): MockServiceScript | undefined {
  return MOCK_SERVICE_SCRIPTS.find((s) => s.service.xcv === xcv);
}

export function getMockScriptByServiceTreeId(stid: string): MockServiceScript | undefined {
  return MOCK_SERVICE_SCRIPTS.find((s) => s.service.service_tree_id === stid);
}
