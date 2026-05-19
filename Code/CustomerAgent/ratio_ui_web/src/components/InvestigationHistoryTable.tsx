/**
 * InvestigationHistoryTable — Cosmos-backed list of past investigations with
 * client-tunable filters. Drives `GET /api/investigations` directly; rows are
 * click-navigable to Neural Canvas and have an "Open Logs" button per row.
 *
 * This is the "past runs" view from the plan — not to be confused with the
 * existing scenario-test-runs section in ChaHistoryPage, which lives on top
 * of /customer-agent-api/scenarios.
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { investigationsClient } from '../api/investigationsClient';
import type { Investigation, InvestigationFilters } from '../types/investigations';
import { openInvestigationLogs, phaseBadge } from './investigationCommon';

const DECISION_OPTIONS: { value: string; label: string }[] = [
  { value: '', label: 'Any decision' },
  { value: 'resolved_as_root_cause', label: 'Root cause' },
  { value: 'resolved_as_contributing', label: 'Contributing' },
  { value: 'resolved_as_not_root_cause', label: 'Not root cause' },
  { value: 'unresolved', label: 'Unresolved' },
];

function isoDaysAgo(days: number): string {
  return new Date(Date.now() - days * 24 * 60 * 60 * 1000).toISOString();
}

function topHypothesis(inv: Investigation): {
  title: string;
  confidence: number | null;
  status: string;
} | null {
  if (!inv.hypotheses?.length) return null;
  const ranked = [...inv.hypotheses].sort(
    (a, b) => (b.confidence ?? 0) - (a.confidence ?? 0),
  );
  const top = ranked[0];
  return {
    title: top.title || top.id || '',
    confidence: top.confidence,
    status: top.status,
  };
}

function formatConfidence(c: number | null | undefined): string {
  if (c == null) return '—';
  return `${Math.round(c * 100)}%`;
}

function formatDate(s: string): string {
  if (!s) return '—';
  const d = new Date(s);
  if (Number.isNaN(d.getTime())) return s;
  return d.toLocaleString();
}

export interface InvestigationHistoryTableProps {
  /** Default days to look back. UI lets the user override. */
  defaultLookbackDays?: number;
  /** Override the title; pass empty string to hide the header. */
  title?: string;
}

export default function InvestigationHistoryTable({
  defaultLookbackDays = 5,
  title = 'Recent Investigations',
}: InvestigationHistoryTableProps): JSX.Element {
  const navigate = useNavigate();

  const [lookbackDays, setLookbackDays] = useState<number>(defaultLookbackDays);
  const [customer, setCustomer] = useState<string>('');
  const [decision, setDecision] = useState<string>('');
  const [minConf, setMinConf] = useState<number>(0);
  const [limit, setLimit] = useState<number>(50);

  const [rows, setRows] = useState<Investigation[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const filters: InvestigationFilters = useMemo(
    () => ({
      since: isoDaysAgo(lookbackDays),
      customer_name: customer.trim() || undefined,
      decision: decision || undefined,
      min_confidence: minConf > 0 ? minConf : undefined,
      limit,
    }),
    [lookbackDays, customer, decision, minConf, limit],
  );

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await investigationsClient.list(filters);
      setRows(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [filters]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

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
      {title && (
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 10,
            marginBottom: 10,
          }}
        >
          <i className="fas fa-history" style={{ color: '#60a5fa' }} />
          <h4 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>{title}</h4>
          <span style={{ flex: 1 }} />
          <span style={{ fontSize: 11, color: 'var(--cha-text-muted, #9ca3af)' }}>
            {loading ? 'Loading…' : `${rows.length} result${rows.length === 1 ? '' : 's'}`}
          </span>
          <button
            type="button"
            onClick={() => void refresh()}
            className="btn btn-sm btn-outline-light"
            style={{ fontSize: 11 }}
            disabled={loading}
          >
            <i className="fas fa-arrows-rotate" /> Refresh
          </button>
        </div>
      )}

      {/* Filter row */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(5, minmax(0, 1fr))',
          gap: 8,
          marginBottom: 10,
          fontSize: 12,
        }}
      >
        <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ color: 'var(--cha-text-muted, #9ca3af)' }}>Lookback</span>
          <select
            value={lookbackDays}
            onChange={(e) => setLookbackDays(Number(e.target.value))}
            style={{ padding: '4px 6px', borderRadius: 4 }}
          >
            {[1, 2, 5, 7, 14, 30].map((d) => (
              <option key={d} value={d}>
                Last {d} day{d > 1 ? 's' : ''}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ color: 'var(--cha-text-muted, #9ca3af)' }}>Customer</span>
          <input
            type="text"
            placeholder="exact match"
            value={customer}
            onChange={(e) => setCustomer(e.target.value)}
            style={{ padding: '4px 6px', borderRadius: 4 }}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ color: 'var(--cha-text-muted, #9ca3af)' }}>Decision</span>
          <select
            value={decision}
            onChange={(e) => setDecision(e.target.value)}
            style={{ padding: '4px 6px', borderRadius: 4 }}
          >
            {DECISION_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ color: 'var(--cha-text-muted, #9ca3af)' }}>
            Min confidence: {Math.round(minConf * 100)}%
          </span>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={minConf}
            onChange={(e) => setMinConf(Number(e.target.value))}
          />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <span style={{ color: 'var(--cha-text-muted, #9ca3af)' }}>Limit</span>
          <select
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            style={{ padding: '4px 6px', borderRadius: 4 }}
          >
            {[25, 50, 100, 200, 500].map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && (
        <div style={{ fontSize: 12, color: '#fca5a5', marginBottom: 8 }}>{error}</div>
      )}

      {rows.length === 0 && !loading ? (
        <div
          style={{
            fontSize: 12,
            color: 'var(--cha-text-muted, #9ca3af)',
            padding: '8px 4px',
          }}
        >
          No investigations match the current filters.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', fontSize: 12, borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--cha-text-muted, #9ca3af)', textAlign: 'left' }}>
                <th style={{ padding: '4px 6px' }}>When</th>
                <th style={{ padding: '4px 6px' }}>Customer</th>
                <th style={{ padding: '4px 6px' }}>Service</th>
                <th style={{ padding: '4px 6px' }}>Phase</th>
                <th style={{ padding: '4px 6px' }}>Top hypothesis</th>
                <th style={{ padding: '4px 6px' }}>Conf.</th>
                <th style={{ padding: '4px 6px' }} />
              </tr>
            </thead>
            <tbody>
              {rows.map((inv) => {
                const top = topHypothesis(inv);
                return (
                  <tr
                    key={inv.xcv}
                    style={{
                      borderTop: '1px solid var(--cha-border, #374151)',
                      cursor: 'pointer',
                    }}
                    onClick={() =>
                      navigate(`/customer-agent/neural-canvas/${inv.xcv}`)
                    }
                  >
                    <td
                      style={{
                        padding: '6px',
                        color: 'var(--cha-text-muted, #9ca3af)',
                        whiteSpace: 'nowrap',
                      }}
                    >
                      {formatDate(inv.timestamp)}
                    </td>
                    <td style={{ padding: '6px' }}>{inv.customer_name || '—'}</td>
                    <td style={{ padding: '6px' }}>{inv.service_name || '—'}</td>
                    <td style={{ padding: '6px' }}>{phaseBadge(inv.phase)}</td>
                    <td
                      style={{
                        padding: '6px',
                        maxWidth: 380,
                        overflow: 'hidden',
                        textOverflow: 'ellipsis',
                        whiteSpace: 'nowrap',
                      }}
                      title={top?.title ?? ''}
                    >
                      {top ? top.title : <em style={{ color: '#9ca3af' }}>—</em>}
                    </td>
                    <td style={{ padding: '6px' }}>{formatConfidence(top?.confidence)}</td>
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
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
