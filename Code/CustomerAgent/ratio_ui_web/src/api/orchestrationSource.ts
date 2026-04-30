/**
 * Shared orchestration event source.
 *
 * Produces a normalized AsyncGenerator of event frames for the Theatre
 * and Live Orchestration pages. Three modes:
 *
 *   live     — POST /cha-live-api/api/run  (current agent pipeline, SSE)
 *   replay   — GET  /cha-live-api/api/traces/{xcv}/stream  (App Insights)
 *   mock     — local scripted fixture (mockInvestigation.ts)
 *
 * All three yield the same flat { type | EventName, ...fields } shape that
 * the existing reducers already consume, so pages pluggably swap sources
 * without changing their reducer logic.
 */
import { MOCK_EVENTS } from '../fixtures/mockInvestigation';

export type OrchestrationMode = 'live' | 'replay' | 'mock';

export interface OrchestrationOptions {
  mode: OrchestrationMode;
  /** For `live` mode. */
  customer_name?: string | null;
  service_tree_id?: string | null;
  /** For `replay` mode — correlation id to fetch from App Insights. */
  xcv?: string;
  /** Replay pacing. Defaults to 'instant'. */
  replaySpeed?: 'instant' | 'compressed' | 'real';
  /** Optional agent-name filter for replay (e.g. 'narrator'). Blank/undefined = all agents. */
  agentFilter?: string;
  /** For `mock` mode — milliseconds between frames. Default 180. */
  mockIntervalMs?: number;
  /**
   * Client-side pacing for `replay` mode — milliseconds to wait between
   * yielded frames, so past events trickle into the UI like a live stream
   * instead of arriving all at once. Default 0 (no pacing); set to e.g. 250
   * for a demo-friendly drip feed.
   */
  pollPacingMs?: number;
}

export interface RawFrame {
  type?: string;
  EventName?: string;
  xcv?: string;
  source?: string;
  [key: string]: unknown;
}

export interface ReplayServicesRequest {
  customer_name: string;
  start_time: string;
  end_time: string;
}

export interface ReplayServiceOption {
  service_tree_id: string;
  service_name: string;
  xcv: string;
}

// Both proxies route to the same CustomerAgent FastAPI (port 8503). Using
// `/cha-live-api` consistently keeps all orchestration traffic on one
// dev-proxy entry.
const API_PREFIX = '/cha-live-api';

/** Parse an SSE body reader into frames, yielding each parsed data payload. */
async function* parseSSE(
  reader: ReadableStreamDefaultReader<Uint8Array>,
): AsyncGenerator<RawFrame> {
  const decoder = new TextDecoder();
  let buf = '';
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      while (buf.includes('\n\n')) {
        const idx = buf.indexOf('\n\n');
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        for (const line of chunk.split('\n')) {
          if (!line.startsWith('data:')) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          if (payload === '[DONE]') return;
          try {
            yield JSON.parse(payload) as RawFrame;
          } catch {
            // Ignore malformed frames — keep stream alive.
          }
        }
      }
    }
  } finally {
    try {
      reader.releaseLock();
    } catch {
      /* noop */
    }
  }
}

/** Live pipeline — POST /api/run SSE. */
async function* streamLive(
  opts: OrchestrationOptions,
  signal: AbortSignal,
): AsyncGenerator<RawFrame> {
  const body: Record<string, unknown> = {};
  if (opts.customer_name) body.customer_name = opts.customer_name;
  if (opts.service_tree_id) body.service_tree_id = opts.service_tree_id;

  const res = await fetch(`${API_PREFIX}/api/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
    signal,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Live pipeline failed: ${res.status} ${res.statusText}`);
  }
  yield* parseSSE(res.body.getReader());
}

/** Replay — GET /api/traces/{xcv}/stream SSE. */
async function* streamReplay(
  opts: OrchestrationOptions,
  signal: AbortSignal,
): AsyncGenerator<RawFrame> {
  if (!opts.xcv) throw new Error('Replay mode requires an xcv.');
  const speed = opts.replaySpeed || 'instant';
  const params = new URLSearchParams({ speed });
  const agent = (opts.agentFilter || '').trim();
  if (agent) params.set('agent', agent);
  const url = `${API_PREFIX}/api/traces/${encodeURIComponent(opts.xcv)}/stream?${params.toString()}`;
  const res = await fetch(url, { signal });
  if (!res.ok || !res.body) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.clone().json();
      if (j?.detail) detail = j.detail;
    } catch { /* noop */ }
    throw new Error(`Replay failed: ${detail}`);
  }
  const pacing = Math.max(0, opts.pollPacingMs ?? 0);
  for await (const frame of parseSSE(res.body.getReader())) {
    if (signal.aborted) return;
    if (pacing > 0) {
      await new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, pacing);
        const onAbort = () => {
          clearTimeout(t);
          reject(new DOMException('Aborted', 'AbortError'));
        };
        signal.addEventListener('abort', onAbort, { once: true });
      }).catch((err) => {
        if ((err as Error).name === 'AbortError') return;
        throw err;
      });
      if (signal.aborted) return;
    }
    yield frame;
  }
}

/** Mock — scripted fixture with configurable pacing. */
async function* streamMock(
  opts: OrchestrationOptions,
  signal: AbortSignal,
): AsyncGenerator<RawFrame> {
  const interval = Math.max(0, opts.mockIntervalMs ?? 180);
  for (const ev of MOCK_EVENTS) {
    if (signal.aborted) return;
    if (interval > 0) {
      await new Promise<void>((resolve, reject) => {
        const t = setTimeout(resolve, interval);
        const onAbort = () => {
          clearTimeout(t);
          reject(new DOMException('Aborted', 'AbortError'));
        };
        signal.addEventListener('abort', onAbort, { once: true });
      }).catch((err) => {
        if ((err as Error).name === 'AbortError') return;
        throw err;
      });
      if (signal.aborted) return;
    }
    yield ev as RawFrame;
  }
}

/** Dispatch to the right source based on mode. */
export async function* streamOrchestration(
  opts: OrchestrationOptions,
  signal: AbortSignal,
): AsyncGenerator<RawFrame> {
  switch (opts.mode) {
    case 'live':
      yield* streamLive(opts, signal);
      return;
    case 'replay':
      yield* streamReplay(opts, signal);
      return;
    case 'mock':
      yield* streamMock(opts, signal);
      return;
    default: {
      const _exhaustive: never = opts.mode;
      throw new Error(`Unknown mode: ${String(_exhaustive)}`);
    }
  }
}

/** Check whether the replay endpoint is configured on the backend. */
export async function getReplayHealth(): Promise<{
  status: string;
  workspace_configured: boolean;
}> {
  try {
    const res = await fetch(`${API_PREFIX}/api/traces/health`);
    if (!res.ok) return { status: 'error', workspace_configured: false };
    return (await res.json()) as { status: string; workspace_configured: boolean };
  } catch {
    return { status: 'error', workspace_configured: false };
  }
}

/**
 * Return replayable services for a customer + time window.
 *
 * Hybrid strategy:
 *   1. Cloud `POST /api/run/services` is the source of truth for WHICH
 *      services are impacted in the requested window.
 *   2. The XCVs cloud returns are freshly spawned and rarely have any
 *      AppTraces ingested yet \u2014 so the reasoning panel would be empty.
 *      We swap each cloud XCV for the *richest already-ingested* XCV
 *      per service from the local /api/traces/services endpoint, which
 *      scans Log Analytics for XCVs with the most events.
 *   3. If the cloud service is not present locally (no ingested
 *      history), we keep the cloud XCV \u2014 the chat will be empty but
 *      the service still appears.
 */
export async function getReplayServices(req: ReplayServicesRequest): Promise<ReplayServiceOption[]> {
  // 1) Cloud: which services are impacted in this window.
  const cloudUrl = `/cha-cloud-api/api/run/services`;
  const cloudRes = await fetch(cloudUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_name: req.customer_name,
      start_time: req.start_time,
      end_time: req.end_time,
    }),
  });
  if (!cloudRes.ok) {
    let detail = `${cloudRes.status} ${cloudRes.statusText}`;
    try {
      const j = await cloudRes.clone().json();
      if (j?.detail) detail = String(j.detail);
    } catch {
      // noop
    }
    throw new Error(`Replay services failed: ${detail}`);
  }
  const cloudRows = (await cloudRes.json()) as ReplayServiceOption[];
  const cloud = Array.isArray(cloudRows) ? cloudRows : [];

  // 2) Local: best ingested XCV per service for the same customer.
  //    Lookback is generous so we cover the requested window even when
  //    AppTraces ingestion lags.
  let localByServiceId = new Map<string, ReplayServiceOption>();
  try {
    const localUrl =
      `/cha-live-api/api/traces/services` +
      `?customer_name=${encodeURIComponent(req.customer_name)}` +
      `&lookback_hours=720`;
    const localRes = await fetch(localUrl, { method: 'GET' });
    if (localRes.ok) {
      const localRows = (await localRes.json()) as ReplayServiceOption[];
      if (Array.isArray(localRows)) {
        for (const r of localRows) {
          if (r?.service_tree_id) localByServiceId.set(r.service_tree_id, r);
        }
      }
    }
  } catch {
    // If the local endpoint is unreachable, fall back to cloud-only XCVs.
    localByServiceId = new Map();
  }

  // 3) Merge: keep cloud's service list & display name, swap the XCV
  //    for the richest local one when available.
  return cloud.map((c) => {
    const local = localByServiceId.get(c.service_tree_id);
    if (local && local.xcv) {
      return {
        service_tree_id: c.service_tree_id,
        service_name: c.service_name || local.service_name,
        xcv: local.xcv,
      };
    }
    return c;
  });
}
