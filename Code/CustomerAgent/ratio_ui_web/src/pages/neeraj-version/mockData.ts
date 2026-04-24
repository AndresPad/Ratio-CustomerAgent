/**
 * Mock data for the Investigation Reasoning Flow.
 *
 * Each trace line is tagged with a `stage` so the animation knows
 * when to reveal it during the auto-play sequence.
 */
import type {
  InvestigationSignal,
  TraceLine,
  ConfidenceScore,
  RootCause,
} from './investigationTypes';

export const MOCK_SIGNAL: InvestigationSignal = {
  title: 'Anomalous Spike in API Latency',
  symptoms: [
    {
      title: 'Database connection pool exhaustion',
      hypothesis: { id: 'HYP-1', label: 'Slow query causing pool exhaustion', prior: 40, confidence: 92 },
      evidence: [
        { title: 'Query plan analysis', detail: 'Sequential scan on users table (2.1M rows)', status: 'success' },
        { title: 'Connection wait time', detail: 'Avg wait: 1.8s (normally <5ms)', status: 'success' },
        { title: 'Recent schema migration', detail: 'Index dropped during deploy #4821', status: 'success' },
      ],
    },
    {
      title: 'Memory pressure on pod-web-3',
      hypothesis: { id: 'HYP-2', label: 'Memory leak in search service', prior: 35, confidence: 15 },
      evidence: [
        { title: 'Heap snapshot diff', detail: 'No significant object retention', status: 'failure' },
        { title: 'Pod restart history', detail: 'No OOM kills in last 24h', status: 'failure' },
      ],
    },
    {
      title: 'Increased error rate on /api/search',
      hypothesis: { id: 'HYP-3', label: 'Upstream dependency degradation', prior: 25, confidence: 8 },
      evidence: [
        { title: 'External API health', detail: 'All upstreams healthy', status: 'failure' },
        { title: 'Network latency check', detail: 'Inter-service latency normal (<2ms)', status: 'failure' },
      ],
    },
  ],
};

export const MOCK_TRACE: TraceLine[] = [
  // Signal
  { stage: 'signal', text: 'Received IcM signal #784501920 (Sev2) for BlackRock, Inc -- ScaleSet Platform', type: 'normal', icon: '\u{1f535}' },
  { stage: 'signal', text: 'Extracting customer context: subscription=3a7e-..., region=westeurope', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'signal', text: 'Signal classified as ACTIVE outage -- escalating to symptom detection', type: 'highlight', icon: '\u{1f7e1}' },
  // Symptom
  { stage: 'symptom', text: 'Querying Kusto: SLI breaches for subscription in last 4h', type: 'normal', icon: '\u{1f535}' },
  { stage: 'symptom', text: 'Found 7 SLI breaches across Xstore, Allocator, CIS, Regional Network Mgr', type: 'success', icon: '\u{1f7e2}' },
  { stage: 'symptom', text: 'Checking dependency health: Xstore degraded in westeurope (p99 latency 4200ms)', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'symptom', text: 'Correlating active incidents: 2 overlapping outages found in region', type: 'highlight', icon: '\u{1f7e1}' },
  // Hypothesis
  { stage: 'hypothesis', text: 'Generating hypothesis HYP-DEP-001: Xstore degradation cascading to customer VMs', type: 'normal', icon: '\u{1f535}' },
  { stage: 'hypothesis', text: 'Generating hypothesis HYP-OUT-002: Compounding effect from WACAP incident #784501920', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'hypothesis', text: 'Generating hypothesis HYP-SLI-001: Infrastructure degradation causing SLI breach on Delete VMSS', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'hypothesis', text: '4 hypotheses generated -- proceeding to evidence collection', type: 'success', icon: '\u{1f7e2}' },
  // Evidence
  { stage: 'evidence', text: 'Querying Kusto: Xstore availability metrics for westeurope (last 6h)', type: 'normal', icon: '\u{1f535}' },
  { stage: 'evidence', text: 'Retrieved 312 telemetry records -- Xstore success rate dropped to 94.2%', type: 'highlight', icon: '\u{1f7e3}' },
  { stage: 'evidence', text: 'Fetching IcM timeline for incident #784501920: 3 updates, last at 14:32 UTC', type: 'normal', icon: '\u{1f535}' },
  { stage: 'evidence', text: 'Cross-referencing SLI breach windows with dependency outage timestamps', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'evidence', text: 'Collected 7 evidence items across 3 data sources', type: 'success', icon: '\u{1f7e2}' },
  // Scoring
  { stage: 'scoring', text: 'Scoring HYP-DEP-001: strong temporal correlation (r=0.91) with Xstore latency spike', type: 'normal', icon: '\u{1f535}' },
  { stage: 'scoring', text: 'Scoring HYP-OUT-002: moderate overlap -- WACAP incident covers 60% of breach window', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'scoring', text: 'Scoring HYP-SLI-001: weak direct evidence -- SLI breach may be secondary effect', type: 'normal', icon: '\u{1f7e1}' },
  { stage: 'scoring', text: 'Confidence assigned: DEP-001=89%, OUT-002=69%, SLI-001=60%, OUT-001=54%', type: 'success', icon: '\u{1f7e2}' },
  // Reasoning
  { stage: 'reasoning', text: 'Evaluating causal chain: Xstore degradation -> resource exhaustion -> VMSS delete failures', type: 'normal', icon: '\u{1f535}' },
  { stage: 'reasoning', text: 'HYP-DEP-001 selected as primary root cause at 89% confidence', type: 'highlight', icon: '\u{1f7e3}' },
  { stage: 'reasoning', text: 'Ruling out HYP-OUT-001: WACAP incident is correlated but not causal (54%)', type: 'normal', icon: '\u{1f7e1}' },
  { stage: 'reasoning', text: 'Generating recommended actions: escalate Xstore team, apply regional failover', type: 'normal', icon: '\u{1f535}' },
  { stage: 'reasoning', text: 'Building investigation summary with full evidence chain', type: 'normal', icon: '\u{1f7e3}' },
  { stage: 'reasoning', text: 'Root cause determination complete -- writing final report', type: 'success', icon: '\u{1f7e2}' },
  // Result
  { stage: 'result', text: 'Investigation complete: 7 symptoms -> 4 hypotheses -> 7 evidence items -> 4 actions (346s)', type: 'result', icon: '\u2705' },
  { stage: 'result', text: 'Root cause: Xstore degradation in westeurope (89% confidence)', type: 'result', icon: '\u2705' },
];

export const MOCK_CONFIDENCE: ConfidenceScore[] = [
  { id: 'HYP-1', label: 'Slow query causing pool exhau...', badgeClass: 'hyp1', score: 92 },
  { id: 'HYP-2', label: 'Memory leak in search service', badgeClass: 'hyp2', score: 15 },
  { id: 'HYP-3', label: 'Upstream dependency degrada...', badgeClass: 'hyp3', score: 8 },
];

export const MOCK_ROOT_CAUSE: RootCause = {
  description:
    'Missing index on users.email_normalized after migration #4821 caused full table scans, exhausting the DB connection pool.',
  recommendedAction:
    'CREATE INDEX CONCURRENTLY idx_users_email_norm ON users (email_normalized);',
};
