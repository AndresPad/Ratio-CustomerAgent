/**
 * API client for the CustomerAgent investigation service (local backend, port 8503).
 *
 * In dev mode Vite proxies `/customer-agent-api` → `http://127.0.0.1:8503`
 * (configured in vite.config.ts).
 *
 * This file is the "legacy" client that drove the old scenario-fixture-runner
 * UI (Active page picker, scenario history, etc). Several functions in here
 * have been retired now that the page surfaces are driven by the cloud
 * /api/run/services endpoint and the new investigations + traces REST routes.
 *
 * If you're adding new investigation-related calls, prefer:
 *   • `investigationsClient.ts`  — Cosmos-backed REST + SSE for the new UI
 *   • `orchestrationSource.ts`   — replay/live event streams
 *   • `traceClient.ts`           — Log Analytics trace events
 */

const PREFIX = '/customer-agent-api';

async function oaFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${PREFIX}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`CustomerAgent API ${res.status}: ${text}`);
  }
  return res.json() as Promise<T>;
}

/* ── Types ─────────────────────────────────────────────── */

export interface Scenario {
  id: string;
  name: string;
  description: string;
  category: string;
  signal_count: number;
  expected_outcome: string;
  expected_root_cause: string;
}

export interface AgentInfo {
  name: string;
  display_name: string;
  description: string;
  role: string;
  objective?: string;
  model: string;
  temperature?: number;
  technology_tags?: string[];
  tool_names: string[];
}

export interface ConfigItem {
  [key: string]: unknown;
}

export interface DataFileInfo {
  name: string;
  path: string;
  record_count?: number;
  columns?: string[];
  size?: string;
}

export interface KnowledgeFile {
  name: string;
  title: string;
  preview: string;
  size: string;
}

/* ── API calls ─────────────────────────────────────────── */

export async function listScenarios(): Promise<Scenario[]> {
  const data = await oaFetch<{ scenarios: Scenario[] }>('/api/scenarios');
  return data.scenarios;
}

export async function listAgents(): Promise<AgentInfo[]> {
  const data = await oaFetch<{ agents: AgentInfo[] }>('/api/agents');
  return data.agents;
}

export async function getConfigTab(tab: string): Promise<ConfigItem[]> {
  const data = await oaFetch<Record<string, ConfigItem[]>>(`/api/config/${tab}`);
  // The API returns different keys per tab, grab the first array value
  const values = Object.values(data);
  for (const v of values) {
    if (Array.isArray(v)) return v;
  }
  return [];
}

export async function listDatafiles(): Promise<DataFileInfo[]> {
  const data = await oaFetch<{ files: DataFileInfo[] }>('/api/datafiles');
  return data.files;
}

export async function getDatafile(path: string): Promise<{ name: string; records: Record<string, unknown>[] }> {
  return oaFetch(`/api/datafiles/${encodeURIComponent(path)}`);
}

export async function listKnowledge(): Promise<KnowledgeFile[]> {
  const data = await oaFetch<{ files: KnowledgeFile[] }>('/api/knowledge');
  return data.files;
}

export async function getKnowledgeContent(name: string): Promise<{ title: string; content: string }> {
  return oaFetch(`/api/knowledge/${encodeURIComponent(name)}`);
}
