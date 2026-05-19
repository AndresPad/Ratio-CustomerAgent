/**
 * useInvestigationStream — subscribes to `/api/investigations/stream` (SSE)
 * and keeps a live, de-duplicated list of in-flight investigations.
 *
 * Backend: Code/CustomerAgent/src/server/investigations_stream.py
 *
 * Behaviour:
 *  - On mount: fetches the current active set from
 *    `GET /api/investigations/active` (so the UI isn't empty before the
 *    first change-feed event arrives).
 *  - Then opens an EventSource. Each `investigation` frame is merged into
 *    the list by xcv; complete docs are removed from the active view.
 *  - Auto-reconnect with exponential backoff on error.
 *  - Feature flag: VITE_ENABLE_INVESTIGATION_STREAM=false falls back to
 *    short polling (5s) of `/active` instead of SSE — lets prod kill the
 *    live stream without redeploying.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { investigationsClient } from '../api/investigationsClient';
import type { Investigation } from '../types/investigations';

export type StreamStatus = 'connecting' | 'live' | 'polling' | 'error' | 'closed';

const SSE_URL = '/customer-agent-api/api/investigations/stream';
const POLL_FALLBACK_MS = 5000;
const MAX_BACKOFF_MS = 30_000;

function streamEnabled(): boolean {
  const flag = (import.meta.env.VITE_ENABLE_INVESTIGATION_STREAM ?? 'true')
    .toString()
    .toLowerCase();
  return flag !== 'false' && flag !== '0';
}

function mergeByXcv(prev: Investigation[], next: Investigation): Investigation[] {
  const idx = prev.findIndex((p) => p.xcv === next.xcv || p.id === next.id);
  // Drop completed docs from the "active" list — keeps the UI focused on
  // in-flight work. Callers that want history should hit /api/investigations.
  if (next.phase === 'complete') {
    return idx === -1 ? prev : prev.filter((_, i) => i !== idx);
  }
  if (idx === -1) return [next, ...prev];
  const copy = prev.slice();
  copy[idx] = next;
  return copy;
}

export interface UseInvestigationStreamResult {
  investigations: Investigation[];
  status: StreamStatus;
  error: string | null;
  reconnect: () => void;
}

export function useInvestigationStream(
  lookbackHours = 24,
): UseInvestigationStreamResult {
  const [investigations, setInvestigations] = useState<Investigation[]>([]);
  const [status, setStatus] = useState<StreamStatus>('connecting');
  const [error, setError] = useState<string | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const pollRef = useRef<number | null>(null);
  const backoffRef = useRef<number>(1000);
  const reconnectKey = useRef<number>(0);

  const refreshFromRest = useCallback(async () => {
    try {
      const active = await investigationsClient.listActive(lookbackHours);
      setInvestigations(active);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [lookbackHours]);

  const startPolling = useCallback(() => {
    setStatus('polling');
    void refreshFromRest();
    if (pollRef.current != null) window.clearInterval(pollRef.current);
    pollRef.current = window.setInterval(() => {
      void refreshFromRest();
    }, POLL_FALLBACK_MS);
  }, [refreshFromRest]);

  const stopPolling = useCallback(() => {
    if (pollRef.current != null) {
      window.clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }, []);

  const reconnect = useCallback(() => {
    reconnectKey.current += 1;
    setStatus('connecting');
    setError(null);
  }, []);

  useEffect(() => {
    let cancelled = false;

    // Always seed from REST so the panel isn't blank before the first
    // change-feed event lands.
    void refreshFromRest();

    if (!streamEnabled()) {
      startPolling();
      return () => {
        cancelled = true;
        stopPolling();
      };
    }

    function openStream(): void {
      if (cancelled) return;
      try {
        const es = new EventSource(SSE_URL);
        esRef.current = es;

        es.addEventListener('hello', () => {
          if (cancelled) return;
          setStatus('live');
          backoffRef.current = 1000;
        });

        es.addEventListener('investigation', (ev) => {
          if (cancelled) return;
          try {
            const doc = JSON.parse((ev as MessageEvent).data) as Investigation;
            setInvestigations((prev) => mergeByXcv(prev, doc));
          } catch (parseErr) {
            console.warn('useInvestigationStream: bad SSE payload', parseErr);
          }
        });

        // Heartbeats arrive every ~15s; no-op handler keeps the listener live.
        es.addEventListener('heartbeat', () => undefined);

        es.onerror = () => {
          if (cancelled) return;
          es.close();
          esRef.current = null;
          setStatus('error');
          const delay = backoffRef.current;
          backoffRef.current = Math.min(delay * 2, MAX_BACKOFF_MS);
          window.setTimeout(openStream, delay);
        };
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
        setStatus('error');
        // Fall back to polling so the panel still works.
        startPolling();
      }
    }

    openStream();
    return () => {
      cancelled = true;
      stopPolling();
      esRef.current?.close();
      esRef.current = null;
      setStatus('closed');
    };
    // reconnectKey changes when the caller forces a reconnect.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [refreshFromRest, startPolling, stopPolling, reconnectKey.current]);

  return { investigations, status, error, reconnect };
}
