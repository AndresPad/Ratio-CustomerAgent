/**
 * Display-name overrides for service names surfaced in the React UI.
 *
 * Some raw service names returned by upstream services (Log Analytics
 * `AppTraces.Properties.ServiceName`, the cloud Container App's
 * `/api/run/services`, etc.) have long, internal-sounding labels that we
 * don't want users to see. This module is the single client-side source
 * of truth for "what should we render?" overrides and is applied at the
 * API boundary so every consumer (tabs, panel headers, channel names,
 * email subjects derived from these values) gets the normalized form.
 *
 * Keep this map small and authoritative and in sync with the server-side
 * map at `Code/CustomerAgent/src/server/display_names.py`. Synonyms /
 * aliases for entity normalization belong in
 * `Code/RATIO_MCP/src/datasets/ServiceNameSynonyms.json`, NOT here.
 */
const SERVICE_NAME_DISPLAY_OVERRIDES: Record<string, string> = {
  'SQL Connectivity': 'SQL',
};

export function displayServiceName(name: string | null | undefined): string {
  if (!name) return '';
  const stripped = name.trim();
  return SERVICE_NAME_DISPLAY_OVERRIDES[stripped] ?? stripped;
}
