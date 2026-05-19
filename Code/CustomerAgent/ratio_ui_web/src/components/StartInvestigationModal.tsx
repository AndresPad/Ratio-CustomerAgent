/**
 * StartInvestigationModal — collects customer_name + optional time window
 * and POSTs to `/cha-cloud-api/api/run/services`. On success, the results
 * are displayed with **Tail** and **Canvas** buttons that open in NEW
 * TABS so the user stays on the Active page where they started.
 */
import { useState } from 'react';
import { investigationsClient } from '../api/investigationsClient';
import type { RunServiceResult } from '../api/investigationsClient';
import { openNeuralCanvasLive, openTraceTail } from './investigationCommon';

const OVERLAY: React.CSSProperties = {
  position: 'fixed',
  inset: 0,
  background: 'rgba(0,0,0,0.5)',
  display: 'flex',
  alignItems: 'center',
  justifyContent: 'center',
  zIndex: 9999,
};

const PANEL: React.CSSProperties = {
  background: 'var(--cha-panel-bg, #1f2937)',
  color: 'var(--cha-text, #f3f4f6)',
  border: '1px solid var(--cha-border, #374151)',
  borderRadius: 10,
  padding: 20,
  width: 'min(560px, 92vw)',
  boxShadow: '0 10px 40px rgba(0,0,0,0.4)',
};

const INPUT: React.CSSProperties = {
  padding: '6px 8px',
  borderRadius: 4,
  border: '1px solid var(--cha-border, #374151)',
  background: 'rgba(255,255,255,0.05)',
  color: 'inherit',
  fontSize: 13,
  width: '100%',
};

function toLocalDatetimeInputValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

/**
 * Format a local `<input type="datetime-local">` value (interpreted in the
 * browser's local TZ) as a human-readable UTC string for display below the
 * input. Returns an empty string if the value is missing or unparseable.
 */
function formatUtcEquivalent(localValue: string): string {
  if (!localValue) return '';
  const d = new Date(localValue);
  if (Number.isNaN(d.getTime())) return '';
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ` +
    `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`
  );
}

const UTC_HINT: React.CSSProperties = {
  fontSize: 11,
  color: 'var(--cha-text-muted, #9ca3af)',
  marginTop: 3,
  display: 'block',
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
};

export interface StartInvestigationModalProps {
  open: boolean;
  onClose: () => void;
  /** Optional: notify parent (e.g. to scroll the active list). */
  onStarted?: (results: RunServiceResult[]) => void;
}

export default function StartInvestigationModal({
  open,
  onClose,
  onStarted,
}: StartInvestigationModalProps): JSX.Element | null {
  const now = new Date();
  const oneHourAgo = new Date(now.getTime() - 60 * 60 * 1000);
  const [customer, setCustomer] = useState('');
  const [startTime, setStartTime] = useState(toLocalDatetimeInputValue(oneHourAgo));
  const [endTime, setEndTime] = useState(toLocalDatetimeInputValue(now));
  const [useTimeWindow, setUseTimeWindow] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [results, setResults] = useState<RunServiceResult[] | null>(null);

  if (!open) return null;

  function close(): void {
    if (busy) return;
    setError(null);
    setResults(null);
    onClose();
  }

  async function submit(): Promise<void> {
    if (!customer.trim()) {
      setError('Customer name is required.');
      return;
    }
    setBusy(true);
    setError(null);
    setResults(null);
    try {
      const payload = useTimeWindow
        ? {
            customer_name: customer.trim(),
            start_time: new Date(startTime).toISOString(),
            end_time: new Date(endTime).toISOString(),
          }
        : { customer_name: customer.trim() };
      const res = await investigationsClient.start(payload);
      setResults(res);
      onStarted?.(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={OVERLAY} onClick={close} role="dialog" aria-modal>
      <div style={PANEL} onClick={(e) => e.stopPropagation()}>
        <div style={{ display: 'flex', alignItems: 'center', marginBottom: 12 }}>
          <i className="fas fa-play-circle" style={{ color: '#fbbf24', marginRight: 8 }} />
          <h4 style={{ margin: 0, fontSize: 15, fontWeight: 600, flex: 1 }}>
            Start Investigation
          </h4>
          <button
            type="button"
            onClick={close}
            disabled={busy}
            className="btn btn-sm btn-link"
            style={{ color: 'var(--cha-text-muted, #9ca3af)' }}
            aria-label="Close"
          >
            ×
          </button>
        </div>

        {!results ? (
          <>
            <label style={{ display: 'block', marginBottom: 10 }}>
              <span
                style={{
                  fontSize: 12,
                  color: 'var(--cha-text-muted, #9ca3af)',
                  marginBottom: 4,
                  display: 'block',
                }}
              >
                Customer name (required)
              </span>
              <input
                type="text"
                value={customer}
                onChange={(e) => setCustomer(e.target.value)}
                placeholder="e.g. BlackRock, Inc"
                style={INPUT}
                autoFocus
              />
            </label>

            <label
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                fontSize: 13,
                marginBottom: 6,
              }}
            >
              <input
                type="checkbox"
                checked={useTimeWindow}
                onChange={(e) => setUseTimeWindow(e.target.checked)}
              />
              Restrict to a time window
            </label>

            {useTimeWindow && (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 8,
                  marginBottom: 10,
                }}
              >
                <label>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--cha-text-muted, #9ca3af)',
                      marginBottom: 4,
                      display: 'block',
                    }}
                  >
                    Start (local time)
                  </span>
                  <input
                    type="datetime-local"
                    value={startTime}
                    onChange={(e) => setStartTime(e.target.value)}
                    style={INPUT}
                  />
                  <span style={UTC_HINT}>{formatUtcEquivalent(startTime) || '—'}</span>
                </label>
                <label>
                  <span
                    style={{
                      fontSize: 12,
                      color: 'var(--cha-text-muted, #9ca3af)',
                      marginBottom: 4,
                      display: 'block',
                    }}
                  >
                    End (local time)
                  </span>
                  <input
                    type="datetime-local"
                    value={endTime}
                    onChange={(e) => setEndTime(e.target.value)}
                    style={INPUT}
                  />
                  <span style={UTC_HINT}>{formatUtcEquivalent(endTime) || '—'}</span>
                </label>
              </div>
            )}

            {error && (
              <div style={{ color: '#fca5a5', fontSize: 12, marginBottom: 10 }}>
                {error}
              </div>
            )}

            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                onClick={close}
                disabled={busy}
                className="btn btn-sm btn-outline-light"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void submit()}
                disabled={busy || !customer.trim()}
                className="btn btn-sm btn-primary"
              >
                {busy ? (
                  <>
                    <i className="fas fa-spinner fa-spin" /> Starting…
                  </>
                ) : (
                  <>
                    <i className="fas fa-play" /> Start
                  </>
                )}
              </button>
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize: 13, marginBottom: 10 }}>
              Kicked off <strong>{results.length}</strong> investigation
              {results.length === 1 ? '' : 's'} for{' '}
              <strong>{customer.trim()}</strong>. Open each to watch the
              agent stream its reasoning live from Log Analytics:
            </div>
            {results.length > 0 && (
              <ul
                style={{
                  fontSize: 12,
                  listStyle: 'none',
                  padding: 0,
                  margin: '0 0 12px',
                  display: 'flex',
                  flexDirection: 'column',
                  gap: 6,
                }}
              >
                {results.map((r) => (
                  <li
                    key={r.xcv}
                    style={{
                      display: 'flex',
                      alignItems: 'center',
                      gap: 8,
                      flexWrap: 'wrap',
                    }}
                  >
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      style={{ padding: '2px 10px', fontSize: 12, flexShrink: 0 }}
                      onClick={() => openTraceTail(r.xcv)}
                      title="Open the live trace tail in a new tab"
                    >
                      <i className="fas fa-play" /> Tail
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-outline-light"
                      style={{ padding: '2px 10px', fontSize: 12, flexShrink: 0 }}
                      onClick={() =>
                        openNeuralCanvasLive({
                          xcv: r.xcv,
                          service_name: r.service_name,
                          service_tree_id: r.service_tree_id,
                          customer_name: customer.trim(),
                        })
                      }
                      title="Open the Neural Canvas live view in a new tab"
                    >
                      <i className="fas fa-diagram-project" /> Canvas
                    </button>
                    <span style={{ fontWeight: 600 }}>
                      {r.service_name || r.service_tree_id}
                    </span>
                    <span
                      style={{
                        color: 'var(--cha-text-muted, #9ca3af)',
                        fontFamily: 'monospace',
                      }}
                    >
                      xcv={r.xcv.slice(0, 8)}…
                    </span>
                  </li>
                ))}
              </ul>
            )}
            <div style={{ display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                onClick={close}
                className="btn btn-sm btn-outline-light"
              >
                Close
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
