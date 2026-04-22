/**
 * Live Orchestration API client.
 *
 * Talks to the real Customer Agent FastAPI server (Code/CustomerAgent/src/server/app.py)
 * which runs on port 8503. In dev, Vite proxies `/cha-live-api` → `http://127.0.0.1:8503`
 * (see vite.config.ts).
 *
 * The primary endpoint is `POST /api/run`, which kicks off the full
 * Signal Builder → Investigation GroupChat pipeline and streams every
 * internal event as Server-Sent Events. This client normalises those
 * SSE frames into a single `LiveEvent` discriminated union so the UI
 * can reason about them uniformly.
 *
 * Event taxonomy (informed by the AgentLogger event names used by
 * the backend — see helper/agent_logger.py):
 *
 *   Pipeline meta:
 *     pipeline_started, signal_evaluation_complete,
 *     investigations_starting, pipeline_complete, pipeline_error
 *
 *   Signal stage (AgentLogger EventName):
 *     SignalEvaluationStart, SignalTypeEvaluated,
 *     CompoundEvaluated, SignalDecision,
 *     MCPCollectionCall
 *
 *   Investigation stage (runner + AgentLogger):
 *     investigation_started, hypothesis_evaluation_started,
 *     investigation_agent_response, investigation_complete,
 *     investigation_workflow_error,
 *     WorkflowStarted, PhaseTransition, HypothesisScoring,
 *     HypothesisSelected, HypothesisTransition, SpeakerSelected,
 *     EvidenceCycle, OutputParsed, AgentResponse, ToolCall,
 *     InvestigationComplete, InvestigationCreated, LLMCall
 */

const PREFIX = '/cha-live-api';

/** Raw frame as it comes from the SSE stream — values are mostly unknown. */
export interface RawLiveEvent {
  /** Pipeline-level events set `type`, e.g. `pipeline_started`. */
  type?: string;
  /** AgentLogger events set `EventName`, e.g. `SignalEvaluationStart`. */
  EventName?: string;
  /** Service (always "AGENT_SERVER" for logger events). */
  Service?: string;
  /** Shared correlation id. */
  xcv?: string;
  pipeline_xcv?: string;
  /** Everything else. */
  [key: string]: unknown;
}

/** A normalised live event. `kind` is derived from `type` or `EventName`. */
export interface LiveEvent extends RawLiveEvent {
  /** Our canonical discriminator — see LIVE_EVENT_KINDS below. */
  kind: string;
  /** Receiving timestamp (ms since epoch). */
  receivedAt: number;
}

/** Request body for POST /api/run. Both fields optional — backend falls back
 *  to monitoring_context.json targets. */
export interface RunPipelineRequest {
  customer_name?: string;
  service_tree_id?: string;
}

/** Derive a single canonical `kind` string from a raw SSE frame. */
export function kindOf(evt: RawLiveEvent): string {
  if (typeof evt.type === 'string' && evt.type.length > 0) return evt.type;
  if (typeof evt.EventName === 'string' && evt.EventName.length > 0) return evt.EventName;
  return 'unknown';
}

/**
 * Start the full pipeline and stream events back.
 *
 * Yields one normalised LiveEvent per SSE data frame until the server
 * emits `data: [DONE]`. Honours `AbortSignal` so the caller can cancel
 * a running stream cleanly.
 */
export async function* streamPipeline(
  req: RunPipelineRequest = {},
  signal?: AbortSignal,
): AsyncGenerator<LiveEvent> {
  const res = await fetch(`${PREFIX}/api/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Failed to start pipeline: ${res.status} ${res.statusText}`);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // SSE frames are separated by a blank line ("\n\n").
      while (buffer.includes('\n\n')) {
        const sepIdx = buffer.indexOf('\n\n');
        const chunk = buffer.slice(0, sepIdx);
        buffer = buffer.slice(sepIdx + 2);

        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          if (payload === '[DONE]') return;
          try {
            const raw = JSON.parse(payload) as RawLiveEvent;
            yield { ...raw, kind: kindOf(raw), receivedAt: Date.now() };
          } catch (err) {
            // Malformed SSE frame. Keep streaming, but surface the error
            // in dev so it's discoverable; stay silent in production
            // builds so we don't spam the console.
            const meta = import.meta as unknown as { env?: { DEV?: boolean } };
            if (meta.env?.DEV) {
              // eslint-disable-next-line no-console
              console.warn('[liveOrchestrationClient] dropped malformed SSE frame', {
                error: err instanceof Error ? err.message : String(err),
                payload: payload.slice(0, 200),
              });
            }
          }
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      // noop
    }
  }
}

/** Health check on the CustomerAgent server. */
export async function getLiveHealth(): Promise<{ status: string }> {
  const res = await fetch(`${PREFIX}/health`);
  if (!res.ok) throw new Error(`Health ${res.status}`);
  return res.json() as Promise<{ status: string }>;
}
