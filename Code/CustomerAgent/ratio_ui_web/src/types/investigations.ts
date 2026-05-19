/**
 * Types mirroring the FastAPI response models in
 * Code/CustomerAgent/src/server/investigations_api.py.
 */

export interface HypothesisSummary {
  id: string;
  title: string;
  status: string;
  confidence: number | null;
  root_cause: string | null;
}

export interface InvestigationCounts {
  symptoms: number;
  hypotheses: number;
  evidence: number;
  activated_signals: number;
  activated_compounds: number;
}

export interface Investigation {
  id: string;
  xcv: string;
  investigation_id: string;
  customer_name: string;
  service_tree_id: string;
  service_name: string;
  timestamp: string;
  phase: string;
  counts: InvestigationCounts;
  hypotheses: HypothesisSummary[];
  /** Cosmos epoch seconds (server-stamped). */
  _ts?: number | null;
}

export interface InvestigationFilters {
  customer_name?: string;
  /** ISO-8601 lower bound on timestamp. */
  since?: string;
  /** ISO-8601 upper bound on timestamp. */
  until?: string;
  phase?: string;
  decision?: string;
  min_confidence?: number;
  limit?: number;
}

export interface LogsLink {
  xcv: string;
  url: string;
  workspace_id: string;
  note?: string;
}

export interface InFlightInvestigation {
  xcv: string;
  service_tree_id: string;
  service_name: string;
  customer_name: string;
  started_at: string;
  last_event_at: string;
  age_seconds: number;
  event_count: number;
}
