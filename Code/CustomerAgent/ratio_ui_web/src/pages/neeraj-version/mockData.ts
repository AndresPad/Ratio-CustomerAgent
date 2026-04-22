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
  // Signal stage
  { text: 'Ingesting signal: API latency anomaly detected', type: 'normal', stage: 'signal' },
  // Symptom stage
  { text: 'Correlating 3 symptoms within the 5-minute window', type: 'normal', stage: 'symptom' },
  // Hypothesis stage
  { text: 'Generating hypotheses from symptom patterns...', type: 'highlight', stage: 'hypothesis' },
  { text: 'H1: Slow query → pool exhaustion (prior: 0.40)', indent: true, type: 'normal', stage: 'hypothesis' },
  { text: 'H2: Memory leak in search service (prior: 0.35)', indent: true, type: 'normal', stage: 'hypothesis' },
  { text: 'H3: Upstream dependency failure (prior: 0.25)', indent: true, type: 'normal', stage: 'hypothesis' },
  // Evidence collection stage
  { text: 'Collecting evidence for H1...', type: 'highlight', stage: 'evidence' },
  { text: '✓ Query plan shows sequential scan on 2.1M rows', indent: true, type: 'success', stage: 'evidence' },
  { text: '✓ Connection wait time 360x above baseline', indent: true, type: 'success', stage: 'evidence' },
  { text: '✓ Index dropped in deploy #4821 — matches timeline', indent: true, type: 'success', stage: 'evidence' },
  { text: '→ H1 confidence updated: 0.40 → 0.92', indent: true, type: 'result', stage: 'evidence' },
  { text: 'Collecting evidence for H2...', type: 'highlight', stage: 'evidence' },
  { text: '✗ No significant heap object retention found', indent: true, type: 'fail', stage: 'evidence' },
  { text: '✗ No OOM kills in 24h window', indent: true, type: 'fail', stage: 'evidence' },
  { text: '→ H2 confidence updated: 0.35 → 0.15', indent: true, type: 'result', stage: 'evidence' },
  { text: 'Collecting evidence for H3...', type: 'highlight', stage: 'evidence' },
  { text: '✗ All upstream services reporting healthy', indent: true, type: 'fail', stage: 'evidence' },
  { text: '✗ Inter-service network latency normal', indent: true, type: 'fail', stage: 'evidence' },
  { text: '→ H3 confidence updated: 0.25 → 0.08', indent: true, type: 'result', stage: 'evidence' },
  // Scoring stage
  { text: 'Confidence scoring complete. Winner: H1 (0.92)', type: 'highlight', stage: 'scoring' },
  // Reasoning stage
  { text: 'Root cause: Missing index after migration #4821', type: 'result', stage: 'reasoning' },
  { text: 'Recommended fix: CREATE INDEX CONCURRENTLY ...', type: 'success', stage: 'reasoning' },
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
