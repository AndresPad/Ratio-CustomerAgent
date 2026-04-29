/**
 * traceClient — fetch Log-Analytics trace events for a given XCV.
 */

export interface TraceEvent {
  TimeGenerated: string;
  EventName: string;
  AgentName?: string;
  Content?: string;
  ToolName?: string;
  QueryText?: string;
  Duration?: number;
  XCV?: string;
  SessionId?: string;
  HypothesisId?: string;
  HypothesisText?: string;
  Confidence?: number;
  Status?: string;
  SignalTitle?: string;
  RootCause?: string;
  Summary?: string;
  /** Flattened from Properties.llm_response_text by the backend. The raw
   *  LLM reply for this agent turn — typically the most readable
   *  representation of what the agent "said" or "reasoned". */
  llm_response_text?: string;
  [key: string]: unknown;
}

const BASE = '/cha-live-api';

export async function fetchTraceEvents(xcv: string): Promise<TraceEvent[]> {
  const res = await fetch(`${BASE}/api/traces/${encodeURIComponent(xcv)}`);
  if (!res.ok) throw new Error(`Trace fetch failed: ${res.status}`);
  const data = await res.json();
  return (data.events ?? data) as TraceEvent[];
}
