/**
 * ChaTraceTailPage — minimal "tail this xcv" view that polls
 * `/api/traces/{xcv}` and renders new events as Log Analytics ingests them.
 *
 * Why not use ChaNeuralCanvasPage? Because that page is hardcoded to a
 * static April-16 demo replay window and ignores the URL xcv. This page is
 * the simplest thing that works for following a freshly minted xcv from
 * `/api/run/services` until LA traces start landing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { fetchTraceEvents, type TraceEvent } from '../../api/traceClient';
import { openInvestigationLogs } from '../../components/investigationCommon';

const POLL_MS = 5000;
const MAX_BACKOFF_MS = 60_000;

type TailStatus = 'idle' | 'polling' | 'error' | 'stopped';

function eventKey(ev: TraceEvent, idx: number): string {
  const t = String(ev.TimeGenerated ?? '');
  const name = String(ev.EventName ?? '');
  const agent = String(ev.AgentName ?? '');
  return `${t}|${name}|${agent}|${idx}`;
}

function fmtTime(s: string): string {
  if (!s) return '—';
  const d = new Date(s);
  return Number.isNaN(d.getTime()) ? s : d.toLocaleTimeString();
}

function preview(ev: TraceEvent): string {
  const candidate =
    ev.llm_response_text ||
    ev.Content ||
    ev.Summary ||
    ev.HypothesisText ||
    ev.QueryText ||
    '';
  return String(candidate).slice(0, 240);
}

export default function ChaTraceTailPage(): JSX.Element {
  const { xcv = '' } = useParams<{ xcv: string }>();
  const navigate = useNavigate();
  const [events, setEvents] = useState<TraceEvent[]>([]);
  const [seenKeys] = useState<Set<string>>(() => new Set());
  const [status, setStatus] = useState<TailStatus>('idle');
  const [error, setError] = useState<string | null>(null);
  const [lastPoll, setLastPoll] = useState<number>(0);
  const [pollCount, setPollCount] = useState<number>(0);
  const stoppedRef = useRef(false);
  const backoffRef = useRef(POLL_MS);

  const poll = useCallback(async () => {
    if (stoppedRef.current) return;
    setStatus('polling');
    try {
      const rows = await fetchTraceEvents(xcv);
      if (stoppedRef.current) return;
      setLastPoll(Date.now());
      setPollCount((n) => n + 1);
      const fresh: TraceEvent[] = [];
      rows.forEach((ev, i) => {
        const key = eventKey(ev, i);
        if (!seenKeys.has(key)) {
          seenKeys.add(key);
          fresh.push(ev);
        }
      });
      if (fresh.length) {
        setEvents((prev) => [...prev, ...fresh]);
      }
      setError(null);
      backoffRef.current = POLL_MS;
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus('error');
      backoffRef.current = Math.min(backoffRef.current * 2, MAX_BACKOFF_MS);
    }
  }, [xcv, seenKeys]);

  useEffect(() => {
    if (!xcv) return;
    stoppedRef.current = false;
    void poll();
    const id = window.setInterval(() => {
      void poll();
    }, POLL_MS);
    return () => {
      stoppedRef.current = true;
      window.clearInterval(id);
    };
  }, [xcv, poll]);

  const elapsed = useMemo(() => {
    if (!lastPoll) return null;
    const s = Math.round((Date.now() - lastPoll) / 1000);
    return `${s}s ago`;
  }, [lastPoll, pollCount]);

  const stop = () => {
    stoppedRef.current = true;
    setStatus('stopped');
  };

  return (
    <div style={{ padding: 16, fontFamily: 'system-ui, sans-serif' }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 16,
          flexWrap: 'wrap',
        }}
      >
        <i className="fas fa-bolt" style={{ color: '#fbbf24', fontSize: 18 }} />
        <h2 style={{ margin: 0, fontSize: 18, fontWeight: 700 }}>
          Live Trace Tail
        </h2>
        <code
          style={{
            background: 'var(--cha-panel-bg, #111827)',
            color: 'var(--cha-text-muted, #9ca3af)',
            padding: '2px 8px',
            borderRadius: 4,
            fontSize: 12,
          }}
        >
          {xcv}
        </code>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 11,
            background:
              status === 'polling'
                ? '#28a745'
                : status === 'error'
                  ? '#dc3545'
                  : status === 'stopped'
                    ? '#6c757d'
                    : '#0984e3',
            color: 'white',
            padding: '2px 8px',
            borderRadius: 10,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
          }}
        >
          {status}
        </span>
        <button
          type="button"
          className="btn btn-sm btn-outline-light"
          onClick={() => void poll()}
          disabled={status === 'polling'}
          style={{ fontSize: 11 }}
        >
          <i className="fas fa-arrows-rotate" /> Refresh
        </button>
        {status !== 'stopped' && (
          <button
            type="button"
            onClick={stop}
            className="btn btn-sm btn-outline-light"
            style={{ fontSize: 11 }}
          >
            Stop
          </button>
        )}
        <button
          type="button"
          onClick={() => xcv && void openInvestigationLogs(xcv)}
          className="btn btn-sm btn-outline-light"
          style={{ fontSize: 11 }}
        >
          Open in Log Analytics
        </button>
        <button
          type="button"
          onClick={() => navigate('/customer-agent/active')}
          className="btn btn-sm btn-link"
          style={{ fontSize: 11 }}
        >
          ← Back to Active
        </button>
      </div>

      <div
        style={{
          fontSize: 12,
          color: 'var(--cha-text-muted, #9ca3af)',
          marginBottom: 10,
        }}
      >
        Polls <code>/api/traces/{'{xcv}'}</code> every {POLL_MS / 1000}s. New
        AppTraces rows appended below as Log Analytics ingests them (typical
        ingestion lag is 30–90s for a fresh investigation).{' '}
        {lastPoll > 0 && (
          <>
            · Last poll: <strong>{elapsed}</strong> · Polls:{' '}
            <strong>{pollCount}</strong>
          </>
        )}
      </div>

      {error && (
        <div
          style={{
            background: '#fef2f2',
            color: '#991b1b',
            border: '1px solid #fecaca',
            padding: '8px 12px',
            borderRadius: 6,
            fontSize: 12,
            marginBottom: 12,
          }}
        >
          {error}
        </div>
      )}

      {events.length === 0 ? (
        <div
          style={{
            textAlign: 'center',
            padding: '60px 20px',
            color: 'var(--cha-text-muted, #9ca3af)',
            border: '1px dashed var(--cha-border, #374151)',
            borderRadius: 8,
          }}
        >
          <i
            className="fas fa-satellite-dish"
            style={{ fontSize: 36, marginBottom: 12, display: 'block', opacity: 0.6 }}
          />
          <div style={{ fontSize: 14, marginBottom: 4 }}>
            Waiting for events from Log Analytics…
          </div>
          <div style={{ fontSize: 12 }}>
            If nothing appears in 2–3 minutes the cloud agent may have errored.{' '}
            <Link
              to="/customer-agent/history"
              style={{ textDecoration: 'underline' }}
            >
              Check History
            </Link>{' '}
            or open the run in Log Analytics directly.
          </div>
        </div>
      ) : (
        <table
          style={{
            width: '100%',
            borderCollapse: 'collapse',
            fontSize: 12,
          }}
        >
          <thead>
            <tr
              style={{
                textAlign: 'left',
                color: 'var(--cha-text-muted, #9ca3af)',
                borderBottom: '1px solid var(--cha-border, #374151)',
              }}
            >
              <th style={{ padding: '6px 8px', width: 110 }}>Time</th>
              <th style={{ padding: '6px 8px', width: 180 }}>Event</th>
              <th style={{ padding: '6px 8px', width: 140 }}>Agent</th>
              <th style={{ padding: '6px 8px' }}>Preview</th>
            </tr>
          </thead>
          <tbody>
            {events.map((ev, i) => (
              <tr
                key={eventKey(ev, i)}
                style={{ borderBottom: '1px solid var(--cha-border, #1f2937)' }}
              >
                <td
                  style={{
                    padding: '6px 8px',
                    color: 'var(--cha-text-muted, #9ca3af)',
                    fontFamily: 'monospace',
                  }}
                >
                  {fmtTime(String(ev.TimeGenerated))}
                </td>
                <td style={{ padding: '6px 8px', fontWeight: 600 }}>
                  {String(ev.EventName ?? '—')}
                </td>
                <td
                  style={{
                    padding: '6px 8px',
                    color: 'var(--cha-text-muted, #9ca3af)',
                  }}
                >
                  {String(ev.AgentName ?? '—')}
                </td>
                <td
                  style={{
                    padding: '6px 8px',
                    whiteSpace: 'pre-wrap',
                    fontFamily: 'monospace',
                    fontSize: 11,
                  }}
                >
                  {preview(ev)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
