/**
 * Helpers shared by the Active-page action buttons (scheduler panel,
 * start-investigation modal, etc).
 *
 * IMPORTANT — these helpers all use `window.open(url, '_blank', 'noopener')`
 * because the Active page is meant to be the user's *home base*: they
 * shouldn't be navigated away when they click a tail / canvas / logs
 * button. New tab semantics keeps the Start CTA + Scheduler panel right
 * where they were.
 *
 * The Canvas link encodes the service info as URL query params (instead
 * of React Router `state`) so the new tab can still bypass the static
 * April-16 demo window in ChaNeuralCanvasPage. See `freshFromStart`
 * derivation there.
 */
import { investigationsClient } from '../api/investigationsClient';

export const PHASE_COLORS: Record<string, string> = {
  initializing: '#17a2b8',
  triage: '#0984e3',
  hypothesizing: '#e17055',
  planning: '#fdcb6e',
  collecting: '#00b894',
  reasoning: '#d63031',
  acting: '#e84393',
  notifying: '#6c5ce7',
  signal_building: '#17a2b8',
  complete: '#28a745',
};

export function phaseBadge(phase: string): JSX.Element {
  const bg = PHASE_COLORS[phase] ?? '#6c757d';
  return (
    <span
      style={{
        background: bg,
        color: 'white',
        padding: '2px 8px',
        borderRadius: 10,
        fontSize: 11,
        fontWeight: 600,
        textTransform: 'uppercase',
        letterSpacing: 0.4,
      }}
    >
      {phase || 'unknown'}
    </span>
  );
}

/**
 * Resolves the Log Analytics deep-link for *xcv* via the backend and opens
 * it in a new tab. Backend route: GET /api/investigations/{xcv}/logs.
 */
export async function openInvestigationLogs(xcv: string): Promise<void> {
  try {
    const { url } = await investigationsClient.getLogsLink(xcv);
    window.open(url, '_blank', 'noopener');
  } catch (e) {
    console.error('openInvestigationLogs failed', e);
  }
}

/** Open the in-app live trace tail in a new tab. */
export function openTraceTail(xcv: string): void {
  window.open(
    `/customer-agent/trace-tail/${encodeURIComponent(xcv)}`,
    '_blank',
    'noopener',
  );
}

/**
 * Open the Neural Canvas Live view in a new tab.
 *
 * Encodes the service info as URL query params so the canvas can bypass
 * its static April-16 demo window — `ChaNeuralCanvasPage` reads both
 * `location.state` (for same-tab navigation) and `?service=…&svcId=…&customer=…`
 * (for new-tab navigation triggered by `window.open`).
 */
export function openNeuralCanvasLive(opts: {
  xcv: string;
  service_name?: string | null;
  service_tree_id?: string | null;
  customer_name?: string | null;
}): void {
  const params = new URLSearchParams();
  if (opts.service_name) params.set('service', opts.service_name);
  if (opts.service_tree_id) params.set('svcId', opts.service_tree_id);
  if (opts.customer_name) params.set('customer', opts.customer_name);
  const qs = params.toString();
  const url =
    `/customer-agent/neural-canvas-live/${encodeURIComponent(opts.xcv)}` +
    (qs ? `?${qs}` : '');
  window.open(url, '_blank', 'noopener');
}

