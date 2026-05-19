/**
 * SchedulerMonitorPanel — health view for the `caj-customeragent-scheduler`
 * Container Apps Job.
 *
 * Shows the recent cron ticks as a list of rows (default 15-min windows ×
 * 8 rows = 2 hours of history). Each row:
 *   • Time of the cron tick (e.g., "11:30 AM" with "12m ago" subtitle)
 *   • Count of investigations that landed in Cosmos for the configured
 *     customer during that window
 *   • Expandable list of the actual xcvs, each with quick links to the
 *     trace tail / Neural Canvas
 *
 * The data is a *proxy* signal: counts come from completed Cosmos docs.
 * Ticks that succeeded but returned 0 services from the cloud are
 * invisible here — the **Executions** button opens the authoritative
 * Container Apps Job execution history in the Azure portal.
 *
 * Limitations spelled out in the panel body:
 *  - Doc counts include any investigation for the default customer in
 *    that window — ad-hoc manual runs against the same customer also
 *    count as scheduler ticks.
 *  - If you change `CUSTOMER_NAME` on the Job, also set
 *    `VITE_SCHEDULER_CUSTOMER_NAME` to match (or the panel will look
 *    silent for the new customer).
 */
import { useCallback, useEffect, useMemo, useState } from 'react';
import { investigationsClient } from '../api/investigationsClient';
import type { InFlightInvestigation, Investigation } from '../types/investigations';
import {
  openInvestigationLogs,
  openNeuralCanvasLive,
  openTraceTail,
} from './investigationCommon';

// ── Config (overridable via Vite env) ──────────────────────────────

const DEFAULTS = {
  customer: 'BlackRock, Inc',
  cronMinutes: 60,
  windowCount: 8,
  subscriptionId: '01819f01-7af1-4dd8-9354-9dccc163ceae',
  resourceGroup: 'rg-ratio-ai-dev',
  jobName: 'caj-customeragent-scheduler',
};

function envOr(name: string, fallback: string): string {
  const raw = (import.meta.env as Record<string, unknown>)[name];
  if (typeof raw === 'string' && raw.trim()) return raw.trim();
  return fallback;
}
function envOrInt(name: string, fallback: number): number {
  const raw = (import.meta.env as Record<string, unknown>)[name];
  const n = typeof raw === 'string' ? parseInt(raw, 10) : NaN;
  return Number.isFinite(n) ? n : fallback;
}

// ── Helpers ─────────────────────────────────────────────────────────

function relativeTime(ms: number): string {
  const diff = Date.now() - ms;
  const s = Math.round(diff / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  return `${h}h ago`;
}

function shortTime(ms: number): string {
  return new Date(ms).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
}

interface TickRow {
  startMs: number;       // window start (inclusive)
  endMs: number;         // window end (exclusive)
  investigations: Investigation[];
}

function bucketize(
  rows: Investigation[],
  cronMinutes: number,
  windowCount: number,
): TickRow[] {
  const widthMs = cronMinutes * 60 * 1000;
  const nowMs = Date.now();
  // Snap to the most recent cron tick boundary so buckets align with the
  // schedule's frame (e.g. every :00, :15, :30, :45 for a 15-min cron).
  const lastTickStartMs = Math.floor(nowMs / widthMs) * widthMs;
  // Most-recent first.
  const buckets: TickRow[] = [];
  for (let i = 0; i < windowCount; i += 1) {
    const start = lastTickStartMs - i * widthMs;
    buckets.push({ startMs: start, endMs: start + widthMs, investigations: [] });
  }
  for (const r of rows) {
    const t = new Date(r.timestamp).getTime();
    if (!Number.isFinite(t)) continue;
    for (const b of buckets) {
      if (t >= b.startMs && t < b.endMs) {
        b.investigations.push(r);
        break;
      }
    }
  }
  // Sort each bucket's investigations by timestamp ascending so the
  // order reflects what the agent ran first / second / third.
  for (const b of buckets) {
    b.investigations.sort(
      (a, c) => new Date(a.timestamp).getTime() - new Date(c.timestamp).getTime(),
    );
  }
  return buckets;
}

// ── Component ──────────────────────────────────────────────────────

export default function SchedulerMonitorPanel(): JSX.Element {
  const customer = envOr('VITE_SCHEDULER_CUSTOMER_NAME', DEFAULTS.customer);
  const cronMinutes = envOrInt('VITE_SCHEDULER_CRON_MINUTES', DEFAULTS.cronMinutes);
  const windowCount = envOrInt('VITE_SCHEDULER_WINDOWS', DEFAULTS.windowCount);
  const subscriptionId = envOr(
    'VITE_AZURE_SUBSCRIPTION_ID',
    DEFAULTS.subscriptionId,
  );
  const resourceGroup = envOr(
    'VITE_AZURE_RESOURCE_GROUP',
    DEFAULTS.resourceGroup,
  );
  const jobName = envOr('VITE_SCHEDULER_JOB_NAME', DEFAULTS.jobName);

  const [rows, setRows] = useState<Investigation[]>([]);
  const [inFlight, setInFlight] = useState<InFlightInvestigation[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<Record<number, boolean>>({});

  const lookbackMinutes = cronMinutes * windowCount;

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const since = new Date(
        Date.now() - lookbackMinutes * 60 * 1000,
      ).toISOString();
      const [completed, live] = await Promise.all([
        investigationsClient.list({
          customer_name: customer,
          since,
          limit: 200,
        }),
        // In-flight goes broader (events trickle for minutes, not seconds)
        // — 1 hour of LA lookback is a sane default.
        investigationsClient.listInFlight(customer, 1).catch(() => []),
      ]);
      setRows(completed);
      setInFlight(live);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [customer, lookbackMinutes]);

  useEffect(() => {
    void refresh();
    // Faster refresh than the 60s used before — in-flight detection is the
    // headline now and should feel responsive (15s = 1 tick of the cron).
    const id = window.setInterval(() => {
      void refresh();
    }, 15_000);
    return () => window.clearInterval(id);
  }, [refresh]);

  const ticks = useMemo(
    () => bucketize(rows, cronMinutes, windowCount),
    [rows, cronMinutes, windowCount],
  );

  const lastSeenMs = useMemo(() => {
    if (rows.length === 0) return undefined;
    let max = 0;
    for (const r of rows) {
      const t = new Date(r.timestamp).getTime();
      if (t > max) max = t;
    }
    return max || undefined;
  }, [rows]);

  const status = useMemo(() => {
    if (loading && rows.length === 0 && inFlight.length === 0) return 'connecting';
    if (error) return 'error';
    // If anything is in flight RIGHT NOW, scheduler is clearly healthy.
    if (inFlight.length > 0) return 'healthy';
    if (!lastSeenMs) return 'idle';
    const widthMs = cronMinutes * 60 * 1000;
    const gap = Date.now() - lastSeenMs;
    if (gap < widthMs * 2) return 'healthy';
    if (gap < widthMs * 4) return 'stale';
    return 'dead';
  }, [loading, rows.length, inFlight.length, error, lastSeenMs, cronMinutes]);

  const STATUS_LABEL: Record<string, string> = {
    connecting: 'Loading…',
    healthy: 'Healthy',
    stale: 'Slow',
    dead: 'No recent activity',
    idle: 'Idle',
    error: 'Error',
  };
  const STATUS_COLOR: Record<string, string> = {
    connecting: '#0984e3',
    healthy: '#28a745',
    stale: '#fdcb6e',
    dead: '#dc3545',
    idle: '#6c757d',
    error: '#dc3545',
  };

  const portalExecutionsUrl =
    `https://portal.azure.com/#@/resource/subscriptions/` +
    `${encodeURIComponent(subscriptionId)}/resourceGroups/` +
    `${encodeURIComponent(resourceGroup)}/providers/Microsoft.App/jobs/` +
    `${encodeURIComponent(jobName)}/executions`;

  const totalCount = rows.length;

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
      {/* Header */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 12,
          marginBottom: 6,
          flexWrap: 'wrap',
        }}
      >
        <i className="fas fa-clock" style={{ color: '#60a5fa' }} />
        <h4 style={{ margin: 0, fontSize: 14, fontWeight: 600 }}>
          Scheduler — recent ticks
        </h4>
        <span
          style={{
            fontSize: 11,
            background: STATUS_COLOR[status],
            color: 'white',
            padding: '2px 8px',
            borderRadius: 10,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: 0.4,
          }}
          title={error ?? undefined}
        >
          {STATUS_LABEL[status]}
        </span>
        <span style={{ flex: 1 }} />
        <button
          type="button"
          onClick={() => void refresh()}
          disabled={loading}
          className="btn btn-sm btn-outline-light"
          style={{ fontSize: 11 }}
        >
          <i className="fas fa-arrows-rotate" /> Refresh
        </button>
        <a
          href={portalExecutionsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-sm btn-outline-light"
          style={{ fontSize: 11 }}
          title="Open Container Apps Job execution history in the Azure portal"
        >
          <i className="fas fa-up-right-from-square" /> Executions
        </a>
      </div>

      <div
        style={{
          fontSize: 11,
          color: 'var(--cha-text-muted, #9ca3af)',
          marginBottom: 10,
          lineHeight: 1.45,
        }}
      >
        <code>{jobName}</code> · runs every <strong>{cronMinutes} min</strong> ·
        default customer <strong>{customer}</strong>.<br />
        Each row is one cron window. Counts are investigations that finished and
        landed in Cosmos during that window — open a row to see the xcvs.
        Ticks that succeeded but returned 0 services from the cloud are invisible
        here; the <strong>Executions</strong> button is the authoritative source.
      </div>

      {error && (
        <div style={{ fontSize: 12, color: '#fca5a5', marginBottom: 8 }}>
          {error}
        </div>
      )}

      {/* In-flight section — top priority, always rendered when non-empty */}
      {inFlight.length > 0 && (
        <div
          style={{
            border: '1px solid rgba(40,167,69,0.35)',
            borderRadius: 6,
            background: 'rgba(40,167,69,0.07)',
            padding: '8px 12px',
            marginBottom: 12,
          }}
        >
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              marginBottom: 6,
            }}
          >
            <i
              className="fas fa-bolt"
              style={{ color: '#fbbf24', fontSize: 12 }}
            />
            <strong style={{ fontSize: 13 }}>
              In flight right now ({inFlight.length})
            </strong>
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: '50%',
                background: '#22c55e',
                animation: 'cha-pulse 1.4s infinite',
              }}
            />
            <span
              style={{
                fontSize: 11,
                color: 'var(--cha-text-muted, #9ca3af)',
              }}
            >
              from Log Analytics — events seen, completion pending
            </span>
          </div>
          {inFlight.map((inf) => (
            <div
              key={inf.xcv}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 10,
                padding: '4px 0',
                fontSize: 12,
                flexWrap: 'wrap',
                borderTop: '1px dashed rgba(40,167,69,0.25)',
              }}
            >
              <span style={{ fontWeight: 600 }}>
                {inf.service_name || inf.service_tree_id || 'Unknown service'}
              </span>
              <span
                style={{
                  fontFamily: 'monospace',
                  color: 'var(--cha-text-muted, #9ca3af)',
                  fontSize: 11,
                }}
                title={inf.xcv}
              >
                xcv={inf.xcv.slice(0, 8)}…
              </span>
              <span
                style={{
                  fontSize: 11,
                  color: 'var(--cha-text-muted, #9ca3af)',
                }}
              >
                started {inf.age_seconds < 60
                  ? `${inf.age_seconds}s`
                  : `${Math.round(inf.age_seconds / 60)}m`}{' '}
                ago · {inf.event_count} event{inf.event_count === 1 ? '' : 's'}
              </span>
              <span style={{ flex: 1 }} />
              <button
                type="button"
                className="btn btn-sm btn-primary"
                style={{ fontSize: 10, padding: '1px 8px' }}
                onClick={() => openTraceTail(inf.xcv)}
                title="Open the live trace tail in a new tab"
              >
                Tail
              </button>
              <button
                type="button"
                className="btn btn-sm btn-outline-light"
                style={{ fontSize: 10, padding: '1px 8px' }}
                onClick={() =>
                  openNeuralCanvasLive({
                    xcv: inf.xcv,
                    service_name: inf.service_name,
                    service_tree_id: inf.service_tree_id,
                    customer_name: inf.customer_name,
                  })
                }
                title="Open the Neural Canvas live view in a new tab"
              >
                Canvas
              </button>
              <button
                type="button"
                className="btn btn-sm btn-outline-light"
                style={{ fontSize: 10, padding: '1px 8px' }}
                onClick={() => void openInvestigationLogs(inf.xcv)}
                title="Open Log Analytics filtered on this xcv in a new tab"
              >
                Logs
              </button>
            </div>
          ))}
          <style>{`@keyframes cha-pulse { 0%,100% { opacity:1 } 50% { opacity:0.3 } }`}</style>
        </div>
      )}

      {/* Tick list */}
      <div
        style={{
          border: '1px solid var(--cha-border, #374151)',
          borderRadius: 6,
          overflow: 'hidden',
        }}
      >
        {ticks.map((t, i) => {
          const isOpen = !!expanded[t.startMs];
          const hasItems = t.investigations.length > 0;
          const isLatest = i === 0;
          return (
            <div
              key={t.startMs}
              style={{
                borderTop: i === 0 ? 'none' : '1px solid var(--cha-border, #1f2937)',
                background: isLatest
                  ? 'rgba(40,167,69,0.06)'
                  : 'transparent',
              }}
            >
              <button
                type="button"
                onClick={() =>
                  setExpanded((prev) => ({ ...prev, [t.startMs]: !prev[t.startMs] }))
                }
                disabled={!hasItems}
                style={{
                  width: '100%',
                  display: 'grid',
                  gridTemplateColumns: '90px 1fr auto auto',
                  alignItems: 'center',
                  gap: 12,
                  padding: '8px 12px',
                  background: 'transparent',
                  border: 'none',
                  color: 'inherit',
                  cursor: hasItems ? 'pointer' : 'default',
                  fontSize: 13,
                  textAlign: 'left',
                }}
              >
                <span style={{ fontFamily: 'monospace', fontSize: 12 }}>
                  {shortTime(t.startMs)}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    color: 'var(--cha-text-muted, #9ca3af)',
                  }}
                >
                  {relativeTime(t.startMs)}
                </span>
                <span
                  style={{
                    fontSize: 11,
                    padding: '2px 8px',
                    borderRadius: 10,
                    background: hasItems ? '#28a745' : 'rgba(255,255,255,0.08)',
                    color: hasItems ? 'white' : 'var(--cha-text-muted, #9ca3af)',
                    fontWeight: 600,
                    minWidth: 90,
                    textAlign: 'center',
                  }}
                >
                  {hasItems
                    ? `${t.investigations.length} investigation${t.investigations.length === 1 ? '' : 's'}`
                    : 'no activity'}
                </span>
                <span
                  style={{
                    width: 18,
                    color: 'var(--cha-text-muted, #9ca3af)',
                    opacity: hasItems ? 1 : 0.3,
                  }}
                >
                  <i className={`fas fa-chevron-${isOpen ? 'down' : 'right'}`} />
                </span>
              </button>

              {isOpen && hasItems && (
                <div style={{ padding: '4px 12px 10px 102px' }}>
                  {t.investigations.map((inv) => (
                    <div
                      key={inv.xcv}
                      style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: 10,
                        padding: '4px 0',
                        fontSize: 12,
                        flexWrap: 'wrap',
                      }}
                    >
                      <span style={{ fontWeight: 600 }}>
                        {inv.service_name || inv.service_tree_id}
                      </span>
                      <span
                        style={{
                          fontFamily: 'monospace',
                          color: 'var(--cha-text-muted, #9ca3af)',
                          fontSize: 11,
                        }}
                        title={inv.xcv}
                      >
                        xcv={inv.xcv.slice(0, 8)}…
                      </span>
                      <span style={{ flex: 1 }} />
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-light"
                        style={{ fontSize: 10, padding: '1px 8px' }}
                        onClick={() => openTraceTail(inv.xcv)}
                        title="Open the live trace-tail view in a new tab"
                      >
                        Tail
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-light"
                        style={{ fontSize: 10, padding: '1px 8px' }}
                        onClick={() =>
                          openNeuralCanvasLive({
                            xcv: inv.xcv,
                            service_name: inv.service_name,
                            service_tree_id: inv.service_tree_id,
                            customer_name: inv.customer_name,
                          })
                        }
                        title="Open the Neural Canvas live view in a new tab"
                      >
                        Canvas
                      </button>
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-light"
                        style={{ fontSize: 10, padding: '1px 8px' }}
                        onClick={() => void openInvestigationLogs(inv.xcv)}
                        title="Open Log Analytics filtered on this xcv in a new tab"
                      >
                        Logs
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>

      {/* Stats footer */}
      <div
        style={{
          display: 'flex',
          gap: 16,
          fontSize: 11,
          color: 'var(--cha-text-muted, #9ca3af)',
          flexWrap: 'wrap',
          marginTop: 8,
        }}
      >
        <span>
          Last investigation:{' '}
          <strong>{lastSeenMs ? relativeTime(lastSeenMs) : 'never'}</strong>
        </span>
        <span>
          Total in last {lookbackMinutes}m: <strong>{totalCount}</strong>
        </span>
      </div>
    </div>
  );
}

