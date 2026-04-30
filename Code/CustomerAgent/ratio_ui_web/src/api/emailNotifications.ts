/**
 * Email opt-in client. Talks to the local CustomerAgent backend via the
 * `/cha-live-api/api/email` Vite proxy (port 8503).
 */

export interface SubscribeResponse {
  enabled: boolean;
  xcv: string;
  email: string | null;
  subscribed: boolean;
  already_subscribed: boolean;
  started_email_sent: boolean;
  subscriber_count: number;
  message: string | null;
}

export interface NotifyResolvedResponse {
  enabled: boolean;
  xcv: string;
  already_notified?: boolean;
  sent_to: number;
  subscriber_count?: number;
  message?: string;
}

export interface EmailSubscribeArgs {
  xcv: string;
  email: string;
  customer_name?: string;
  service_name?: string;
  signal_title?: string;
  ui_url?: string;
  teams_web_url?: string;
}

export interface EmailNotifyResolvedArgs {
  xcv: string;
  customer_name?: string;
  service_name?: string;
  summary?: string;
  ui_url?: string;
  teams_web_url?: string;
}

const BASE = '/cha-live-api/api/email';

async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const j = await res.clone().json();
      if (j?.detail) detail = String(j.detail);
    } catch {
      /* noop */
    }
    throw new Error(detail);
  }
  return (await res.json()) as T;
}

export async function subscribeEmail(args: EmailSubscribeArgs): Promise<SubscribeResponse> {
  const xcv = (args.xcv || '').trim();
  if (!xcv) throw new Error('xcv required');
  return postJSON<SubscribeResponse>(`${BASE}/subscribe/${encodeURIComponent(xcv)}`, {
    email: args.email,
    customer_name: args.customer_name ?? null,
    service_name: args.service_name ?? null,
    signal_title: args.signal_title ?? null,
    ui_url: args.ui_url ?? null,
    teams_web_url: args.teams_web_url ?? null,
  });
}

export async function unsubscribeEmail(xcv: string, email: string): Promise<unknown> {
  return postJSON(`${BASE}/unsubscribe/${encodeURIComponent(xcv)}`, { email });
}

export async function notifyResolved(
  args: EmailNotifyResolvedArgs,
): Promise<NotifyResolvedResponse> {
  const xcv = (args.xcv || '').trim();
  if (!xcv) throw new Error('xcv required');
  return postJSON<NotifyResolvedResponse>(
    `${BASE}/notify-resolved/${encodeURIComponent(xcv)}`,
    {
      customer_name: args.customer_name ?? null,
      service_name: args.service_name ?? null,
      summary: args.summary ?? null,
      ui_url: args.ui_url ?? null,
      teams_web_url: args.teams_web_url ?? null,
    },
  );
}
