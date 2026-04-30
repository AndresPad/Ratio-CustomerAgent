/**
 * Teams channel client. Talks to the local CustomerAgent backend via the
 * `/cha-live-api/api/teams` Vite proxy (port 8503).
 */

export interface TeamsChannelInfo {
  enabled: boolean;
  xcv: string;
  channel_id: string | null;
  web_url: string | null;
  display_name: string | null;
  created: boolean;
  message: string | null;
}

export interface EnsureChannelArgs {
  xcv: string;
  customer_name?: string;
  service_name?: string;
  signal_title?: string;
}

const BASE = '/cha-live-api/api/teams';

/**
 * Returns the Teams channel info for an XCV, creating the channel on
 * first call. Resolves with `enabled: false` (no throw) when the
 * backend env vars aren't configured so the UI can render a "disabled"
 * state inline.
 */
export async function ensureTeamsChannel(args: EnsureChannelArgs): Promise<TeamsChannelInfo> {
  const xcv = (args.xcv || '').trim();
  if (!xcv) {
    return {
      enabled: false,
      xcv: '',
      channel_id: null,
      web_url: null,
      display_name: null,
      created: false,
      message: 'No XCV',
    };
  }
  const res = await fetch(`${BASE}/channel/${encodeURIComponent(xcv)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      customer_name: args.customer_name ?? null,
      service_name: args.service_name ?? null,
      signal_title: args.signal_title ?? null,
    }),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.clone().json();
      if (j?.detail) detail = String(j.detail);
    } catch {
      /* noop */
    }
    return {
      enabled: false,
      xcv,
      channel_id: null,
      web_url: null,
      display_name: null,
      created: false,
      message: detail,
    };
  }
  return (await res.json()) as TeamsChannelInfo;
}

/** Best-effort fire-and-forget channel update. */
export async function postTeamsMessage(
  xcv: string,
  text: string,
  html = false,
): Promise<boolean> {
  const trimmed = (text || '').trim();
  if (!trimmed || !xcv) return false;
  try {
    const res = await fetch(`${BASE}/channel/${encodeURIComponent(xcv)}/message`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text: trimmed, html }),
    });
    return res.ok;
  } catch {
    return false;
  }
}
