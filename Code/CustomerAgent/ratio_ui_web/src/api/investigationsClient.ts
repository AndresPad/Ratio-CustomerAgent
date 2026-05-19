/**
 * Typed client for the CustomerAgent Investigations API.
 *
 * Backend routes are defined in
 *   Code/CustomerAgent/src/server/investigations_api.py
 * and mounted on the CustomerAgent FastAPI (port 8503 locally,
 * ca-ratio-customeragent-dev in Azure).
 *
 * The Vite dev server proxies `/customer-agent-api/*` → `http://127.0.0.1:8503/*`
 * (see vite.config.ts). In Azure, nginx in front of ratio_ui_web does the same.
 *
 * Set `VITE_USE_FIXTURES=true` in .env.local to bypass the network and read
 * from src/fixtures/mockInvestigations.ts — useful for pure-frontend dev.
 */
import type {
  InFlightInvestigation,
  Investigation,
  InvestigationFilters,
  LogsLink,
} from '../types/investigations';

const PREFIX = '/customer-agent-api/api/investigations';

const USE_FIXTURES =
  (import.meta.env.VITE_USE_FIXTURES as string | undefined)?.toLowerCase() === 'true';

async function inv<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${PREFIX}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Investigations API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

function buildQuery(filters?: InvestigationFilters): string {
  if (!filters) return '';
  const params = new URLSearchParams();
  if (filters.customer_name) params.set('customer_name', filters.customer_name);
  if (filters.since) params.set('since', filters.since);
  if (filters.until) params.set('until', filters.until);
  if (filters.phase) params.set('phase', filters.phase);
  if (filters.decision) params.set('decision', filters.decision);
  if (filters.min_confidence != null)
    params.set('min_confidence', String(filters.min_confidence));
  if (filters.limit != null) params.set('limit', String(filters.limit));
  const qs = params.toString();
  return qs ? `?${qs}` : '';
}

async function fromFixtures(): Promise<Investigation[]> {
  const mod = await import('../fixtures/mockInvestigations');
  return mod.mockInvestigations;
}

/** GET /api/investigations — filtered list, newest first. */
export async function listInvestigations(
  filters?: InvestigationFilters,
): Promise<Investigation[]> {
  if (USE_FIXTURES) return fromFixtures();
  return inv<Investigation[]>(buildQuery(filters));
}

/** GET /api/investigations/active — in-flight (phase != "complete"). */
export async function listActiveInvestigations(
  lookbackHours = 24,
  limit = 100,
): Promise<Investigation[]> {
  if (USE_FIXTURES) {
    const all = await fromFixtures();
    return all.filter((i) => i.phase !== 'complete');
  }
  return inv<Investigation[]>(
    `/active?lookback_hours=${lookbackHours}&limit=${limit}`,
  );
}

/**
 * GET /api/investigations/in-flight — xcvs from Log Analytics with events
 * but no terminal completion event. Fills the gap between "scheduler fired"
 * and "outcome_publisher wrote to Cosmos".
 */
export async function listInFlightInvestigations(
  customerName: string,
  lookbackHours = 1,
): Promise<InFlightInvestigation[]> {
  if (USE_FIXTURES) return [];
  const params = new URLSearchParams({
    customer_name: customerName,
    lookback_hours: String(lookbackHours),
  });
  return inv<InFlightInvestigation[]>(`/in-flight?${params.toString()}`);
}

/** GET /api/investigations/{xcv} — single document. */
export async function getInvestigation(xcv: string): Promise<Investigation> {
  if (USE_FIXTURES) {
    const all = await fromFixtures();
    const hit = all.find((i) => i.xcv === xcv || i.id === xcv);
    if (!hit) throw new Error(`No fixture investigation for xcv=${xcv}`);
    return hit;
  }
  return inv<Investigation>(`/${encodeURIComponent(xcv)}`);
}

/** GET /api/investigations/{xcv}/logs — Log Analytics portal deep-link. */
export async function getInvestigationLogsLink(xcv: string): Promise<LogsLink> {
  if (USE_FIXTURES) {
    return {
      xcv,
      url: `https://portal.azure.com/#blade/Microsoft_Azure_Monitoring_Logs/LogsBlade/?fixture=${encodeURIComponent(xcv)}`,
      workspace_id: 'fixture',
      note: 'fixture mode',
    };
  }
  return inv<LogsLink>(`/${encodeURIComponent(xcv)}/logs`);
}

export interface RunServiceResult {
  service_tree_id: string;
  service_name: string;
  xcv: string;
  timestamp: string;
}

export interface StartInvestigationInput {
  customer_name: string;
  start_time?: string;
  end_time?: string;
}

/**
 * POST /api/run/services — kicks off a real-time investigation for
 * (customer_name, [start_time, end_time]). Returns one row per impacted
 * service with its newly minted xcv. The live event stream for each xcv
 * is then fetched from Log Analytics (existing `/api/traces/{xcv}/stream`
 * route, already mounted on the LOCAL customer-agent backend).
 *
 * Endpoint routing (matches Manik's spec):
 *   - The investigation MUST be triggered on the cloud Container App
 *     `ca-ratio-customeragent-dev`. The Vite dev proxy and the production
 *     nginx config both surface that as `/cha-cloud-api/api/run/services`
 *     (see vite.config.ts + nginx.conf.template).
 *   - In production, the same URL is served by nginx and forwards Easy
 *     Auth headers (`Authorization`, `X-MS-CLIENT-PRINCIPAL*`).
 */
export async function startInvestigation(
  input: StartInvestigationInput,
): Promise<RunServiceResult[]> {
  const url = `/cha-cloud-api/api/run/services`;
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`Start investigation failed (${res.status}): ${text}`);
  }
  const data = (await res.json()) as RunServiceResult[];
  return Array.isArray(data) ? data : [];
}

/** Default export collects the four operations for convenience. */
export const investigationsClient = {
  list: listInvestigations,
  listActive: listActiveInvestigations,
  listInFlight: listInFlightInvestigations,
  get: getInvestigation,
  getLogsLink: getInvestigationLogsLink,
  start: startInvestigation,
};
