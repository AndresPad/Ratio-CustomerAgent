/**
 * Types for the Investigation Reasoning Flow page.
 *
 * Flow: Signal → Symptom → Hypothesis → Evidence Collection →
 *       Confidence Scoring → Reasoning Animation → Result
 */

export interface EvidenceItem {
  title: string;
  detail: string;
  status: 'success' | 'neutral' | 'failure';
}

export interface Hypothesis {
  id: string;
  label: string;
  /** Prior confidence before evidence (shown during hypothesis stage) */
  prior: number;
  /** Final confidence after evidence (revealed during scoring) */
  confidence: number;
}

export interface Symptom {
  title: string;
  hypothesis: Hypothesis;
  evidence: EvidenceItem[];
}

export interface InvestigationSignal {
  title: string;
  symptoms: Symptom[];
}

export interface TraceLine {
  text: string;
  type: 'normal' | 'highlight' | 'success' | 'fail' | 'result';
  indent?: boolean;
  /** Which stage this trace line belongs to — controls when it appears */
  stage: InvestigationStage;
}

export interface ConfidenceScore {
  id: string;
  label: string;
  badgeClass: string;
  score: number;
}

export interface RootCause {
  description: string;
  recommendedAction: string;
}

export type InvestigationStage =
  | 'signal'
  | 'symptom'
  | 'hypothesis'
  | 'evidence'
  | 'scoring'
  | 'reasoning'
  | 'result';

export const INVESTIGATION_STAGES: InvestigationStage[] = [
  'signal',
  'symptom',
  'hypothesis',
  'evidence',
  'scoring',
  'reasoning',
  'result',
];

export const STAGE_DISPLAY: Record<InvestigationStage, string> = {
  signal: 'Signal',
  symptom: 'Symptom',
  hypothesis: 'Hypothesis',
  evidence: 'Evidence Collection',
  scoring: 'Confidence Scoring',
  reasoning: 'Reasoning',
  result: 'Result',
};

/** Bootstrap icon class for each stage */
export const STAGE_ICON: Record<InvestigationStage, string> = {
  signal: 'bi-lightning-charge-fill',
  symptom: 'bi-exclamation-circle-fill',
  hypothesis: 'bi-lightbulb-fill',
  evidence: 'bi-clipboard-check',
  scoring: 'bi-bar-chart-fill',
  reasoning: 'bi-braces-asterisk',
  result: 'bi-check-circle-fill',
};

/** CSS color class suffix applied to stage icons and active pills */
export const STAGE_COLOR: Record<InvestigationStage, string> = {
  signal: 'primary',
  symptom: 'warning',
  hypothesis: 'cyan',
  evidence: 'purple',
  scoring: 'purple',
  reasoning: 'primary',
  result: 'success',
};

/** Milliseconds to spend on each stage during auto-play */
export const STAGE_DURATION: Record<InvestigationStage, number> = {
  signal: 1200,
  symptom: 1500,
  hypothesis: 1800,
  evidence: 2800,
  scoring: 2000,
  reasoning: 3200,
  result: 0,        // terminal — stays forever
};
