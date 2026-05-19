/**
 * LiveInvestigationsPanel — compact list of in-flight investigations driven by
 * the Cosmos change-feed SSE stream (or polling fallback). Drops into any
 * customer-agent page; clicking a row navigates to the Neural Canvas xcv view.
 */
import { useNavigate } from 'react-router-dom';
import { useInvestigationStream } from '../hooks/useInvestigationStream';
import type { Investigation } from '../types/investigations';
import { openInvestigationLogs, phaseBadge } from './investigationCommon';

const STATUS_LABEL: Record<string, string> = {
  connecting: 'Connecting…',
  live: 'Connected',
  polling: 'Polling',
  error: 'Reconnecting…',
  closed: 'Closed',
};

const STATUS_COLOR: Record<string, string> = {
  connecting: '#fdcb6e',
  live: '#28a745',
  polling: '#0984e3',
  error: '#dc3545',
  closed: '#6c757d',
};

export interface LiveInvestigationsPanelProps {
  /** When true, hide the header bar. Useful when embedded under another title. */
  compact?: boolean;
  /** Cap rows shown. Defaults to all. */
  maxRows?: number;
}

export default function LiveInvestigationsPanel({
  compact = false,
  maxRows,
}: LiveInvestigationsPanelProps): JSX.Element {
  const { investigations, status, error, reconnect } = useInvestigationStream(24);
  const navigate = useNavigate();
  const rows: Investigation[] = maxRows
    ? investigations.slice(0, maxRows)
    : investigations;

  return (
    <div
      style={{
        background: 'var(--cha-panel-bg, #1f2937)',
        color: 'var(--cha-text, #f3f4f6)',
        border: '1px solid var(--cha-border, #374151)',
        borderRadius: 8,
        padding: 12,
        marginBottom: 16,
      }}
    >
      {!compact && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 12,
            marginBottom: 4,
          }}
        >
          <i className="fas fa-bolt" style={{ color: '#fbbf24' }} />
          <h4 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
            Live activity (last 24h)
          </h4>
          <span style={{ flex: 1 }} />
          <span
            style={{
              display: 'inline-flex',
              alignItems: 'center',
              gap: 6,
              fontSize: 11,
              padding: '2px 8px',
              borderRadius: 10,
              background: STATUS_COLOR[status] ?? '#6c757d',
              color: 'white',
              fontWeight: 600,
            }}
            title={error ?? undefined}
          >
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: 'white',
                opacity: status === 'live' ? 1 : 0.6,
                animation:
                  status === 'live' ? 'cha-live-pulse 1.4s infinite' : undefined,
              }}
            />
            {STATUS_LABEL[status] ?? status}
          </span>
          {status === 'error' && (
            <button
              type="button"
              onClick={reconnect}
              className="btn btn-sm btn-outline-light"
              style={{ fontSize: 11 }}
            >
              Reconnect
            </button>
          )}
        </div>
      )}

      {!compact && (
        <div
          style={{
            fontSize: 11,
            color: 'var(--cha-text-muted, #9ca3af)',
            marginBottom: 10,
          }}
        >
          New investigations appear here in real time as they finish and
          land in Cosmos (via change-feed SSE). To browse older runs with
          filters, see the table below.
        </div>
      )}

      {error && status !== 'error' && (
        <div style={{ fontSize: 12, color: '#fca5a5', marginBottom: 8 }}>
          {error}
        </div>
      )}

      {rows.length === 0 ? (
        <div
          style={{
            fontSize: 12,
            color: 'var(--cha-text-muted, #9ca3af)',
            padding: '8px 4px',
          }}
        >
          No recently completed investigations in the last 24 hours.
        </div>
      ) : (
        <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
          <thead>
            <tr style={{ color: 'var(--cha-text-muted, #9ca3af)', textAlign: 'left' }}>
              <th style={{ padding: '4px 6px' }}>Customer</th>
              <th style={{ padding: '4px 6px' }}>Service</th>
              <th style={{ padding: '4px 6px' }}>Phase</th>
              <th style={{ padding: '4px 6px' }}>Hypotheses</th>
              <th style={{ padding: '4px 6px' }}>Updated</th>
              <th style={{ padding: '4px 6px' }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((inv) => (
              <tr
                key={inv.xcv}
                style={{
                  borderTop: '1px solid var(--cha-border, #374151)',
                  cursor: 'pointer',
                }}
                onClick={() => navigate(`/customer-agent/neural-canvas/${inv.xcv}`)}
              >
                <td style={{ padding: '6px' }}>{inv.customer_name || '—'}</td>
                <td style={{ padding: '6px' }}>{inv.service_name || '—'}</td>
                <td style={{ padding: '6px' }}>{phaseBadge(inv.phase)}</td>
                <td style={{ padding: '6px' }}>{inv.counts?.hypotheses ?? 0}</td>
                <td
                  style={{
                    padding: '6px',
                    color: 'var(--cha-text-muted, #9ca3af)',
                  }}
                >
                  {inv.timestamp
                    ? new Date(inv.timestamp).toLocaleTimeString()
                    : '—'}
                </td>
                <td style={{ padding: '6px', textAlign: 'right' }}>
                  <button
                    type="button"
                    className="btn btn-sm btn-outline-light"
                    style={{ fontSize: 11, padding: '1px 8px' }}
                    onClick={(e) => {
                      e.stopPropagation();
                      void openInvestigationLogs(inv.xcv);
                    }}
                    title="Open Log Analytics for this xcv"
                  >
                    Logs
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <style>{`@keyframes cha-live-pulse { 0%,100% { opacity: 1 } 50% { opacity: 0.3 } }`}</style>
    </div>
  );
}
