/**
 * ChaFlowDetailPage -- LIVE multi-service investigation view.
 *
 * Continuously polls /api/run/services for impacted customer services and
 * renders one independent replay panel per service. A tab bar at the top
 * lets the operator flip between services; every panel keeps polling Log
 * Analytics in the background so progress for all services advances in
 * parallel.
 *
 * Each ServicePanel owns its own useReplayFlow hook so its agent reasoning,
 * hypothesis verdicts, and stage progress are isolated and persisted while
 * the user looks at another service. ServicePanel reports a compact
 * progress snapshot up to the parent so the tab bar can show a live
 * progress fill per service (X% \u00b7 currently on stage Y).
 */
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  WorkflowCanvas,
  N8nWorkflowGraph,
  StatusBar,
  SignalHeader,
  ReasoningPanel,
  HypothesisPanel,
  RootCauseSection,
  INVESTIGATION_STAGES,
  STAGE_DISPLAY,
  STAGE_COLOR,
  STAGE_ICON,
  ACTIVITY_BAR as S,
} from './ChaInvestigationFlowPage';
import type { InvestigationStage, TraceLine } from './ChaInvestigationFlowPage';
import { useReplayFlow } from '../../hooks/useReplayFlow';
import type { SandboxRun } from '../../hooks/useReplayFlow';
import Prism from 'prismjs';
import 'prismjs/components/prism-python';
import 'prismjs/themes/prism-tomorrow.css';
import {
  getReplayServices,
  type ReplayServiceOption,
} from '../../api/orchestrationSource';
import { ensureTeamsChannel, type TeamsChannelInfo } from '../../api/teamsChannel';
import {
  notifyResolved,
  subscribeEmail,
  type SubscribeResponse,
} from '../../api/emailNotifications';

const DEFAULT_CUSTOMER = 'BlackRock, Inc';
const SERVICE_REFRESH_MS = 30_000;

// Static query window with known data in the deployed workspace.
// /api/run/services returns the most recent XCV per service for this
// customer + window. Polling this window keeps the service tabs fresh
// without requiring the user to pick start/end times.
const REPLAY_WINDOW_START = '2026-04-16T01:00:00Z';
const REPLAY_WINDOW_END = '2026-04-16T02:00:00Z';

type ViewMode = 'pipeline' | 'graph';

interface ServiceProgress {
  reachedCount: number;
  totalStages: number;
  stage: InvestigationStage;
  narration: string;
  running: boolean;
  complete: boolean;
}

export default function ChaFlowDetailV4Page() {
  const { xcv: paramXcv } = useParams<{ xcv: string }>();
  const navigate = useNavigate();

  const [view, setView] = useState<ViewMode>('graph');
  const [serviceOptions, setServiceOptions] = useState<ReplayServiceOption[]>([]);
  const [activeServiceId, setActiveServiceId] = useState('');
  const [servicesLoading, setServicesLoading] = useState(false);
  const [servicesError, setServicesError] = useState<string | null>(null);

  // Progress snapshot per service, keyed by service_tree_id. ServicePanel
  // pushes updates here so the tab bar can render a live fill per service
  // (and so we can show a side-by-side overview at a glance).
  const [progressMap, setProgressMap] = useState<Record<string, ServiceProgress>>({});

  const handleProgress = useCallback((svcId: string, prog: ServiceProgress) => {
    setProgressMap((prev) => {
      const old = prev[svcId];
      if (
        old &&
        old.reachedCount === prog.reachedCount &&
        old.stage === prog.stage &&
        old.running === prog.running &&
        old.complete === prog.complete &&
        old.narration === prog.narration
      ) {
        return prev;
      }
      return { ...prev, [svcId]: prog };
    });
  }, []);

  // Periodically refresh the service list. Keep latest XCV per service
  // in a stable map so each ServicePanel's effect detects xcv changes
  // and starts a fresh replay.
  useEffect(() => {
    let alive = true;

    const refresh = async () => {
      setServicesLoading(true);
      setServicesError(null);
      try {
        const rows = await getReplayServices({
          customer_name: DEFAULT_CUSTOMER,
          start_time: REPLAY_WINDOW_START,
          end_time: REPLAY_WINDOW_END,
        });
        if (!alive) return;
        setServiceOptions(rows);
      } catch (err) {
        if (!alive) return;
        setServicesError(err instanceof Error ? err.message : String(err));
      } finally {
        if (alive) setServicesLoading(false);
      }
    };

    refresh();
    const t = window.setInterval(refresh, SERVICE_REFRESH_MS);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, []);

  // Pick a default active service when the list first arrives or when the
  // current selection disappears. Prefer the URL xcv param if it matches
  // any discovered service.
  useEffect(() => {
    if (serviceOptions.length === 0) return;
    const stillThere = serviceOptions.some((s) => s.service_tree_id === activeServiceId);
    if (stillThere) return;

    const match = paramXcv
      ? serviceOptions.find((s) => s.xcv === paramXcv)
      : null;
    setActiveServiceId((match ?? serviceOptions[0]).service_tree_id);
  }, [serviceOptions, activeServiceId, paramXcv]);

  const handleBack = () => navigate('/customer-agent/investigation-flow');

  return (
    <div
      style={{
        height: 'calc(100vh - 52px)',
        margin: '0 -24px -24px',
        position: 'relative',
        zIndex: 11,
        overflowY: 'auto',
        background: '#fafafa',
      }}
    >
      {/* Top toolbar: back, view toggle */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 6,
          padding: '8px 20px 0',
          fontSize: 11,
        }}
      >
        <button
          onClick={handleBack}
          style={{
            padding: '4px 12px',
            borderRadius: 6,
            fontSize: 11,
            fontWeight: 600,
            cursor: 'pointer',
            border: '1px solid #ddd',
            background: '#fff',
            color: '#666',
            display: 'flex',
            alignItems: 'center',
            gap: 4,
          }}
        >
          <i className="fas fa-arrow-left" /> Back to Live
        </button>
        <div style={{ width: 1, height: 20, background: '#ddd', margin: '0 4px' }} />
        <button
          onClick={() => setView('pipeline')}
          style={viewBtn(view === 'pipeline', '#00bfa5', '#e0f7fa', '#00796b')}
        >
          <i className="fas fa-stream" /> Pipeline
        </button>
        <button
          onClick={() => setView('graph')}
          style={viewBtn(view === 'graph', '#845ec2', '#f3e5f5', '#6a1b9a')}
        >
          <i className="fas fa-project-diagram" /> n8n Graph
        </button>

        <span style={{ flex: 1 }} />

        <span style={{ fontSize: 10, color: '#999' }}>
          {servicesLoading
            ? 'Refreshing services\u2026'
            : serviceOptions.length > 0
              ? `${serviceOptions.length} service${serviceOptions.length === 1 ? '' : 's'} \u00b7 polled every ${SERVICE_REFRESH_MS / 1000}s`
              : 'No services discovered yet'}
        </span>

        {servicesError && (
          <span
            style={{ fontSize: 10, color: '#e53935', marginLeft: 8 }}
            title={servicesError}
          >
            <i className="fas fa-triangle-exclamation" /> services lookup failed
          </span>
        )}
      </div>

      {/* Service tab bar */}
      <ServiceTabs
        services={serviceOptions}
        activeId={activeServiceId}
        progressMap={progressMap}
        onSelect={setActiveServiceId}
      />

      {/* Render every service in parallel; toggle visibility so all keep
          polling Log Analytics even when the operator looks at one. */}
      {serviceOptions.length === 0 && (
        <div
          style={{
            padding: '40px 20px',
            color: '#666',
            fontSize: 13,
            textAlign: 'center',
          }}
        >
          {servicesLoading
            ? 'Looking up impacted services\u2026'
            : 'No impacted services in the current window.'}
        </div>
      )}
      {serviceOptions.map((svc) => (
        <ServicePanel
          key={svc.service_tree_id}
          service={svc}
          view={view}
          isActive={svc.service_tree_id === activeServiceId}
          onProgress={handleProgress}
        />
      ))}
    </div>
  );
}

function viewBtn(
  selected: boolean,
  border: string,
  bg: string,
  fg: string,
): CSSProperties {
  return {
    padding: '4px 12px',
    borderRadius: 6,
    fontSize: 11,
    fontWeight: 600,
    cursor: 'pointer',
    border: selected ? `1px solid ${border}` : '1px solid #ddd',
    background: selected ? bg : '#fff',
    color: selected ? fg : '#888',
  };
}

/* ── Service tab bar ───────────────────────────────────────────── */

interface ServiceTabsProps {
  services: ReplayServiceOption[];
  activeId: string;
  progressMap: Record<string, ServiceProgress>;
  onSelect: (id: string) => void;
}

function ServiceTabs({ services, activeId, progressMap, onSelect }: ServiceTabsProps) {
  if (services.length === 0) return null;
  return (
    <div
      style={{
        display: 'flex',
        flexWrap: 'wrap',
        gap: 10,
        padding: '12px 20px 0',
        borderBottom: '1px solid #e8e8e8',
      }}
    >
      {services.map((svc) => {
        const active = svc.service_tree_id === activeId;
        const prog = progressMap[svc.service_tree_id];
        const reached = prog?.reachedCount ?? 0;
        const total = prog?.totalStages ?? INVESTIGATION_STAGES.length;
        const pct = total > 0 ? Math.round((reached / total) * 100) : 0;
        const stageColor = prog ? STAGE_COLOR[prog.stage] : '#00bfa5';
        const fillColor = prog?.complete
          ? '#00c853'
          : prog?.running
            ? stageColor
            : '#bdbdbd';
        const stageLabel = prog ? STAGE_DISPLAY[prog.stage] : 'Idle';
        return (
          <button
            key={svc.service_tree_id}
            onClick={() => onSelect(svc.service_tree_id)}
            style={tabBtnStyle(active)}
            title={`XCV ${svc.xcv}\n${stageLabel} \u00b7 ${reached}/${total} stages`}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                marginBottom: 4,
              }}
            >
              <i className="fas fa-server" style={{ opacity: 0.7 }} />
              <span style={{ fontWeight: 600 }}>{svc.service_name}</span>
              <span
                style={{
                  fontFamily: 'ui-monospace, monospace',
                  fontSize: 10,
                  opacity: 0.7,
                }}
              >
                {svc.xcv.slice(0, 8)}
              </span>
              {prog?.complete && (
                <i
                  className="fas fa-check-circle"
                  style={{ color: '#00c853', fontSize: 10 }}
                />
              )}
              {prog?.running && (
                <span
                  style={{
                    width: 6,
                    height: 6,
                    borderRadius: '50%',
                    background: stageColor,
                    boxShadow: `0 0 6px ${stageColor}`,
                    animation: 'cha-pulse 1.2s ease-in-out infinite',
                  }}
                />
              )}
            </div>
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 6,
                fontSize: 10,
              }}
            >
              <div
                style={{
                  flex: 1,
                  height: 4,
                  borderRadius: 2,
                  background: '#eceff1',
                  overflow: 'hidden',
                  minWidth: 90,
                }}
              >
                <div
                  style={{
                    height: '100%',
                    width: `${pct}%`,
                    background: fillColor,
                    transition: 'width .35s ease, background .25s ease',
                  }}
                />
              </div>
              <span
                style={{
                  fontVariantNumeric: 'tabular-nums',
                  color: active ? '#00796b' : '#777',
                  minWidth: 28,
                  textAlign: 'right',
                }}
              >
                {pct}%
              </span>
              <span
                style={{
                  color: '#999',
                  fontSize: 10,
                  textTransform: 'uppercase',
                  letterSpacing: 0.4,
                }}
              >
                {stageLabel}
              </span>
            </div>
          </button>
        );
      })}
      {/* Inline keyframes once for the pulse dot. */}
      <style>{`
        @keyframes cha-pulse {
          0%,100% { transform: scale(1);   opacity: 1; }
          50%     { transform: scale(1.4); opacity: .55; }
        }
        @keyframes cha-stage-pop {
          0%   { transform: scale(.7) translateY(8px); opacity: 0; }
          60%  { transform: scale(1.06); opacity: 1; }
          100% { transform: scale(1) translateY(0);    opacity: 1; }
        }
        @keyframes cha-narration-fade {
          from { opacity: 0; transform: translateY(6px); }
          to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes cha-typing {
          0%, 60%, 100% { transform: translateY(0);   opacity: .35; }
          30%           { transform: translateY(-3px); opacity: 1;  }
        }
      `}</style>
    </div>
  );
}

function tabBtnStyle(active: boolean): CSSProperties {
  return {
    padding: '8px 14px 8px',
    fontSize: 12,
    cursor: 'pointer',
    border: active ? '1px solid #00bfa5' : '1px solid #d8d8d8',
    background: active ? '#e0f7fa' : '#fff',
    color: active ? '#00796b' : '#555',
    borderRadius: '6px 6px 0 0',
    borderBottom: active ? '2px solid #00bfa5' : '1px solid #e8e8e8',
    transition: 'background 0.15s ease, color 0.15s ease',
    display: 'flex',
    flexDirection: 'column',
    minWidth: 220,
    textAlign: 'left',
  };
}

/* ── Per-service replay panel ─────────────────────────────────── */

interface ServicePanelProps {
  service: ReplayServiceOption;
  view: ViewMode;
  isActive: boolean;
  onProgress: (svcId: string, prog: ServiceProgress) => void;
}

function ServicePanel({ service, view, isActive, onProgress }: ServicePanelProps) {
  const live = useReplayFlow();
  const lastXcv = useRef('');
  const lastTeamsXcv = useRef('');
  const [teamsChannel, setTeamsChannel] = useState<TeamsChannelInfo | null>(null);
  const [teamsLoading, setTeamsLoading] = useState(false);
  const [emailSubscriberCount, setEmailSubscriberCount] = useState<number>(0);
  const lastResolvedNotifiedXcv = useRef('');

  // Auto-start a fresh replay whenever the service's XCV changes (the
  // services endpoint surfaces the latest XCV per service every 30s).
  useEffect(() => {
    if (!service.xcv) return;
    if (lastXcv.current === service.xcv) return;
    lastXcv.current = service.xcv;
    live.start(service.xcv);
  }, [service.xcv, live]);

  // Lazily ensure a Teams channel exists for this XCV. Backend creates
  // one on the first call and caches it; subsequent loads are cheap.
  useEffect(() => {
    if (!service.xcv) return;
    if (lastTeamsXcv.current === service.xcv) return;
    lastTeamsXcv.current = service.xcv;
    setTeamsChannel(null);
    setTeamsLoading(true);
    setEmailSubscriberCount(0);
    lastResolvedNotifiedXcv.current = '';
    ensureTeamsChannel({
      xcv: service.xcv,
      customer_name: DEFAULT_CUSTOMER,
      service_name: service.service_name,
      signal_title: live.signalTitle || `${service.service_name} \u2014 Investigation`,
    })
      .then((info) => setTeamsChannel(info))
      .catch(() =>
        setTeamsChannel({
          enabled: false,
          xcv: service.xcv,
          channel_id: null,
          web_url: null,
          display_name: null,
          created: false,
          message: 'Teams integration unavailable',
        }),
      )
      .finally(() => setTeamsLoading(false));
  }, [service.xcv, service.service_name, live.signalTitle]);

  const reached = live.reached;
  const active = live.stage;
  const running = live.running || live.loading;
  const elapsed = live.elapsed;
  const complete = reached.length === INVESTIGATION_STAGES.length && !running;
  const traceLines = live.traceLines;
  const hypotheses = live.hypotheses;
  const rootCause = live.rootCause;
  const counts = live.nodeCounts;
  const signalTitle = live.signalTitle || `${service.service_name} \u2014 Investigation`;
  const mapped = traceLines.length;

  // Distinct evidence items collected during the investigation \u2014 we
  // pick stage==='evidence' lines that name a tool, dedup by tool name,
  // and cap at 12 so the tree stays readable.
  const evidenceItems = useMemo(() => {
    const seen = new Set<string>();
    const out: { label: string; tool: string }[] = [];
    for (const ln of traceLines) {
      if (ln.stage !== 'evidence') continue;
      const tool = (ln.tool || '').trim();
      if (!tool) continue;
      const key = tool.toLowerCase();
      if (seen.has(key)) continue;
      seen.add(key);
      out.push({ label: tool, tool });
      if (out.length >= 12) break;
    }
    return out;
  }, [traceLines]);

  const symptomItems = live.symptoms;

  // The most recent narration line that's actually been revealed,
  // condensed to one tight sentence so the per-service progress bar
  // stays demo-friendly and readable at a glance.
  const visibleCount = live.traceCount;
  const latestNarration = useMemo(() => {
    for (let i = Math.min(visibleCount, traceLines.length) - 1; i >= 0; i--) {
      const ln = traceLines[i];
      const t = ln?.text;
      if (!t || !t.trim()) continue;
      // Prefer LLM narrator output; fall back to any non-empty line.
      if (ln && (ln.isLlm || (ln.agent || '').toLowerCase() === 'narrator')) {
        return summarizeNarratorText(t);
      }
    }
    for (let i = Math.min(visibleCount, traceLines.length) - 1; i >= 0; i--) {
      const t = traceLines[i]?.text;
      if (t && t.trim()) return summarizeNarratorText(t);
    }
    return '';
  }, [traceLines, visibleCount]);

  // Push compact progress to the parent so the tab bar can render a
  // live fill per service and the operator can compare side-by-side.
  useEffect(() => {
    onProgress(service.service_tree_id, {
      reachedCount: reached.length,
      totalStages: INVESTIGATION_STAGES.length,
      stage: active,
      narration: latestNarration,
      running,
      complete,
    });
  }, [
    onProgress,
    service.service_tree_id,
    reached.length,
    active,
    latestNarration,
    running,
    complete,
  ]);

  // Fire the "investigation resolved" email exactly once per XCV when
  // the replay reaches the final stage. The backend dedups by xcv so
  // duplicate calls (re-renders, multiple tabs) are safe.
  useEffect(() => {
    if (!complete) return;
    if (!service.xcv) return;
    if (emailSubscriberCount <= 0) return;
    if (lastResolvedNotifiedXcv.current === service.xcv) return;
    lastResolvedNotifiedXcv.current = service.xcv;
    const summaryText =
      (rootCause?.text || '').trim() ||
      latestNarration ||
      `Investigation completed across ${reached.length} stages.`;
    notifyResolved({
      xcv: service.xcv,
      customer_name: DEFAULT_CUSTOMER,
      service_name: service.service_name,
      summary: summaryText,
      ui_url: typeof window !== 'undefined' ? window.location.href : undefined,
      teams_web_url: teamsChannel?.web_url ?? undefined,
    }).catch(() => {
      // best-effort; reset so the user can retry on next reload
      lastResolvedNotifiedXcv.current = '';
    });
  }, [
    complete,
    service.xcv,
    service.service_name,
    emailSubscriberCount,
    rootCause?.text,
    latestNarration,
    reached.length,
    teamsChannel?.web_url,
  ]);

  const handleReload = () => live.start(service.xcv);

  // Render but hide non-active so they keep polling.
  const wrapperStyle: CSSProperties = useMemo(
    () => ({ display: isActive ? 'block' : 'none' }),
    [isActive],
  );

  return (
    <div style={wrapperStyle}>
      {/* HERO: cinematic dark stage that proves "this is a conversation,
          not a pipeline". The agents talk to each other in a group-chat
          transcript on the left; a circular topology of the active agent
          cast orbits a glowing core on the right. The linear workflow
          and the hypothesis/root-cause panels are demoted below this. */}
      <ConversationHero
        serviceName={service.service_name}
        signalTitle={signalTitle}
        reached={reached}
        active={active}
        running={running}
        complete={complete}
        traceLines={traceLines}
        visibleCount={visibleCount}
        sandboxRuns={live.sandboxRuns}
      />

      {/* Control bar: service info + reload */}
      <div style={S.controlBar as CSSProperties}>
        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: '#00bfa5',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <i className="fas fa-satellite-dish" /> LIVE
        </span>

        <span
          style={{
            fontSize: 12,
            fontWeight: 600,
            color: '#333',
            padding: '4px 10px',
            background: '#f5f5f5',
            borderRadius: 4,
          }}
        >
          {service.service_name}
        </span>

        <span style={{ ...S.eventStats, marginLeft: 0 }} title={service.xcv}>
          XCV: {service.xcv}
        </span>

        <button style={S.loadBtn} onClick={handleReload} disabled={running}>
          <i className={`fas ${running ? 'fa-spinner fa-spin' : 'fa-redo'}`} /> Reload
        </button>

        <TeamsChannelButton info={teamsChannel} loading={teamsLoading} />

        <EmailOptInButton
          xcv={service.xcv}
          customerName={DEFAULT_CUSTOMER}
          serviceName={service.service_name}
          signalTitle={signalTitle}
          teamsWebUrl={teamsChannel?.web_url ?? null}
          subscriberCount={emailSubscriberCount}
          onSubscribed={(resp) => {
            if (typeof resp.subscriber_count === 'number') {
              setEmailSubscriberCount(resp.subscriber_count);
            }
          }}
        />

        {live.eventCount > 0 && (
          <span style={S.eventStats}>
            {'\u{1f4e6}'} {live.eventCount} events {'\u00b7'} {mapped} mapped {'\u00b7'}{' '}
            {reached.length}/{INVESTIGATION_STAGES.length} stages
          </span>
        )}

        {live.error && (
          <span style={{ color: '#e53935', fontSize: 12, marginLeft: 8 }}>
            {'\u26a0'} {live.error}
          </span>
        )}

        <span style={S.spacer as CSSProperties} />

        <span style={{ fontSize: 11, color: '#999' }}>
          {running
            ? live.loading
              ? 'Loading trace\u2026'
              : 'Replaying\u2026'
            : complete
              ? 'Investigation complete'
              : 'Waiting for new events\u2026'}
        </span>
        <span style={{ fontSize: 11, color: '#999' }}>{elapsed.toFixed(1)}s</span>
      </div>

      {/* Hypothesis verdict (kept full-width below the hero) */}
      <div style={{ padding: '16px 20px' }}>
        <HypothesisPanel hypotheses={hypotheses} />
      </div>

      {/* Root Cause + Confidence + Summary */}
      <RootCauseSection rootCause={rootCause ?? undefined} visible={rootCause != null} />

      {/* Pipeline / graph view collapsed into a small details strip at the
          bottom \u2014 the user explicitly asked to de-emphasise the linear
          workflow. We still show it for operators who want the structural
          view, but it's no longer the centerpiece. */}
      <details
        style={{
          margin: '0 20px 20px',
          background: '#fff',
          border: '1px solid #e8e8e8',
          borderRadius: 10,
        }}
      >
        <summary
          style={{
            cursor: 'pointer',
            padding: '10px 14px',
            fontSize: 12,
            fontWeight: 600,
            color: '#666',
            display: 'flex',
            alignItems: 'center',
            gap: 6,
          }}
        >
          <i className="fas fa-project-diagram" />
          Workflow topology &mdash; {view === 'pipeline' ? 'pipeline' : 'graph'} view
          <span style={{ marginLeft: 'auto', fontSize: 11, color: '#999' }}>
            {reached.length}/{INVESTIGATION_STAGES.length} stages reached
          </span>
        </summary>
        <div style={{ padding: '8px 0' }}>
          {view === 'pipeline' ? (
            <WorkflowCanvas reached={reached} active={active} counts={counts} />
          ) : (
            <N8nWorkflowGraph reached={reached} active={active} counts={counts} />
          )}
          <StatusBar
            agentName="Summary Writer"
            statusText={
              running
                ? live.loading
                  ? 'Loading trace\u2026'
                  : 'Replaying\u2026'
                : complete
                  ? 'Investigation complete'
                  : 'Waiting for new events\u2026'
            }
            complete={complete}
            elapsed={elapsed}
          />
          <SignalHeader title={signalTitle} status={complete ? 'Resolved' : 'In Progress'} />
        </div>
      </details>

      {/* Relationship tree \u2014 Symptom \u2192 Hypothesis \u2192 Evidence */}
      <RelationshipTree
        signalTitle={signalTitle}
        symptoms={symptomItems}
        hypotheses={hypotheses}
        evidence={evidenceItems}
      />
    </div>
  );
}

/* ── Conversation hero (chat transcript + circular agent ring) ── */

interface ConversationHeroProps {
  serviceName: string;
  signalTitle: string;
  reached: InvestigationStage[];
  active: InvestigationStage;
  running: boolean;
  complete: boolean;
  traceLines: TraceLine[];
  visibleCount: number;
  sandboxRuns: SandboxRun[];
}

/** Agent display metadata: label, role description, color seed, icon. */
const AGENT_META: Record<string, { label: string; role: string; color: string; icon: string }> = {
  narrator:                          { label: 'Narrator',           role: 'Tells the story',         color: '#6366f1', icon: 'fa-solid fa-bullhorn' },
  triage_agent:                      { label: 'Triage',             role: 'Classifies signals',      color: '#ef4444', icon: 'fa-solid fa-arrows-split-up-and-left' },
  reasoner:                          { label: 'Reasoner',           role: 'Forms hypotheses',        color: '#f59e0b', icon: 'fa-solid fa-lightbulb' },
  evidence_planner:                  { label: 'Evidence Planner',   role: 'Plans data pulls',        color: '#10b981', icon: 'fa-solid fa-folder-tree' },
  investigation_orchestrator:        { label: 'Orchestrator',       role: 'Coordinates the team',    color: '#8b5cf6', icon: 'fa-solid fa-music' },
  incident_collector:                { label: 'Incident Collector', role: 'Pulls IcM context',       color: '#14b8a6', icon: 'fa-solid fa-circle-exclamation' },
  sli_collector:                     { label: 'SLI Collector',      role: 'Pulls telemetry',         color: '#0ea5e9', icon: 'fa-solid fa-gauge-high' },
  support_collector:                 { label: 'Support Collector',  role: 'Pulls support tickets',   color: '#f97316', icon: 'fa-solid fa-headset' },
  runner:                            { label: 'Runner',             role: 'Executes tools / queries',color: '#eab308', icon: 'fa-solid fa-person-running' },
  action_planner:                    { label: 'Action Planner',     role: 'Drafts remediation steps',color: '#ec4899', icon: 'fa-solid fa-list-check' },
  sandbox_coder:                     { label: 'Sandbox Coder',      role: 'Writes Python on the fly',color: '#06b6d4', icon: 'fa-solid fa-code' },
  code_generator:                    { label: 'Sandbox Coder',      role: 'Writes Python on the fly',color: '#06b6d4', icon: 'fa-solid fa-code' },
  python_runner:                     { label: 'Python Sandbox',     role: 'Executes Python in a sandbox', color: '#3b82f6', icon: 'fa-brands fa-python' },
  python_executor:                   { label: 'Python Sandbox',     role: 'Executes Python in a sandbox', color: '#3b82f6', icon: 'fa-brands fa-python' },
  run_python_in_sandbox_tool:        { label: 'Python Sandbox',     role: 'Executes Python in a sandbox', color: '#3b82f6', icon: 'fa-brands fa-python' },
  execute_python_in_sandbox_tool:    { label: 'Python Sandbox',     role: 'Executes Python in a sandbox', color: '#3b82f6', icon: 'fa-brands fa-python' },
  collect_impacted_resource_customer:      { label: 'Impacted Customers', role: 'Maps blast radius to customers', color: '#f43f5e', icon: 'fa-solid fa-bullseye' },
  collect_impacted_resource_customer_tool: { label: 'Impacted Customers', role: 'Maps blast radius to customers', color: '#f43f5e', icon: 'fa-solid fa-bullseye' },
};

/** Pick a sensible icon for any agent (known or unknown). */
function iconForAgent(name: string): string {
  if (AGENT_META[name]) return AGENT_META[name].icon;
  const n = name.toLowerCase();
  if (n.includes('python'))   return 'fa-brands fa-python';
  if (n.includes('sandbox'))  return 'fa-solid fa-code';
  if (n.includes('impacted')) return 'fa-solid fa-bullseye';
  if (n.includes('reason'))   return 'fa-solid fa-lightbulb';
  if (n.includes('triage'))   return 'fa-solid fa-arrows-split-up-and-left';
  if (n.includes('orchestr')) return 'fa-solid fa-music';
  if (n.includes('evidence')) return 'fa-solid fa-folder-tree';
  if (n.includes('runner') || n.includes('execute')) return 'fa-solid fa-person-running';
  if (n.includes('sli') || n.includes('telemetry')) return 'fa-solid fa-gauge-high';
  if (name.endsWith('_tool')) return 'fa-solid fa-wrench';
  return 'fa-solid fa-robot';
}

/** Hash a string into one of a few stable demo colors, for unknown agents. */
function colorForAgent(name: string): string {
  if (AGENT_META[name]) return AGENT_META[name].color;
  const palette = ['#7e57c2', '#26a69a', '#ec407a', '#ffa726', '#42a5f5', '#66bb6a'];
  let h = 0;
  for (let i = 0; i < name.length; i++) h = (h * 31 + name.charCodeAt(i)) >>> 0;
  return palette[h % palette.length];
}

function labelForAgent(name: string): string {
  if (AGENT_META[name]) return AGENT_META[name].label;
  // Default: title-case underscored names ("collect_incident_details_tool" -> "Collect Incident Details").
  const base = name.replace(/_tool$/, '').replace(/_/g, ' ');
  return base.replace(/\b\w/g, (c) => c.toUpperCase());
}

function roleForAgent(name: string): string {
  if (AGENT_META[name]) return AGENT_META[name].role;
  if (name.endsWith('_tool')) return 'Tool';
  return 'Agent';
}

function initialsFor(name: string): string {
  const lbl = labelForAgent(name);
  const parts = lbl.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '??';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

/** Try to infer which agent a narrator line is actually talking about,
 *  so the chat bubble can be attributed to (e.g.) the Reasoner instead of
 *  always to the Narrator. Falls back to 'narrator'. */
function inferAgentFromText(text: string): string {
  if (!text) return 'narrator';
  const t = text.toLowerCase();
  // Exact agent keys win first (e.g. "triage_agent").
  for (const key of Object.keys(AGENT_META)) {
    if (key === 'narrator') continue;
    if (t.includes(key)) return key;
  }
  // Then try the human label (e.g. "reasoner", "evidence planner").
  const labelToKey: [string, string][] = Object.entries(AGENT_META)
    .filter(([k]) => k !== 'narrator')
    .map(([k, m]) => [m.label.toLowerCase(), k] as [string, string]);
  // Sort longer first so "evidence planner" matches before "planner".
  labelToKey.sort((a, b) => b[0].length - a[0].length);
  for (const [lbl, key] of labelToKey) {
    if (t.includes(lbl)) return key;
  }
  // Common verbal cues. The narrator paraphrases what other agents do,
  // so we map the most common phrasings to the agent they describe.
  if (/\bhypothes/i.test(text)) return 'reasoner';
  if (/\btriag/i.test(text)) return 'triage_agent';
  if (/\bevidence|\bplan(ned|ning)?\b/i.test(text)) return 'evidence_planner';
  if (/\bincident|\bicm\b/i.test(text)) return 'incident_collector';
  if (/\bsli\b|telemetr|metric/i.test(text)) return 'sli_collector';
  if (/\bsupport|ticket/i.test(text)) return 'support_collector';
  if (/\borchestrat|coordinat|delegat|hand(ed|ing)? off/i.test(text)) return 'investigation_orchestrator';
  if (/\baction\s*plan|remediation|next steps?|mitigation|recommend/i.test(text)) return 'action_planner';
  if (/\brun(ning|ner)?\b|execut(ing|ed)|kusto|query result|invoking/i.test(text)) return 'runner';
  return 'narrator';
}

/** Condense long narrator text into a single tight summary sentence. */
function summarizeNarratorText(text: string): string {
  if (!text) return '';
  const cleaned = text
    .replace(/\r/g, '')
    .replace(/^\s*\*+\s*/, '')
    .replace(/\s+/g, ' ')
    .trim();
  // Prefer the first sentence (up to ~200 chars).
  const m = cleaned.match(/^(.*?[.!?])\s/);
  const first = m ? m[1] : cleaned;
  return first.length > 220 ? first.slice(0, 220).trimEnd() + '\u2026' : first;
}

function ConversationHero({
  serviceName,
  signalTitle,
  reached,
  active,
  running,
  complete,
  traceLines,
  visibleCount,
  sandboxRuns,
}: ConversationHeroProps) {
  // Lines that have been "spoken" so far (drives both the chat and which
  // agents on the ring have already participated).
  const visible = useMemo(
    () => traceLines.slice(0, Math.min(visibleCount, traceLines.length)),
    [traceLines, visibleCount],
  );

  // Build the chat transcript: prefer LLM utterances; collapse runs of
  // structural events from the same agent into a small "*thinking*" line
  // so the conversation reads naturally.
  const chat = useMemo(() => buildChatTurns(visible), [visible]);

  // Throttle chat reveal to one bubble at a time so the conversation
  // reads like a real back-and-forth instead of dumping everything at
  // once. The agent topology ring is driven by the same `revealedChat`
  // array, so the highlighted speaker on the ring stays in sync with
  // whichever bubble was just typed into the chat.
  const PER_BUBBLE_MS = 1500;
  const [revealedCount, setRevealedCount] = useState(0);
  // Reset when a new investigation kicks off (chat goes empty).
  useEffect(() => {
    if (chat.length === 0) setRevealedCount(0);
  }, [chat.length]);
  useEffect(() => {
    if (revealedCount >= chat.length) return;
    // Always reveal one bubble at a time \u2014 even if the investigation
    // already finished by the time the user opened this view, so the
    // back-and-forth conversation never "snaps in" all at once.
    const id = window.setTimeout(() => {
      setRevealedCount((n) => Math.min(n + 1, chat.length));
    }, PER_BUBBLE_MS);
    return () => window.clearTimeout(id);
  }, [revealedCount, chat.length]);
  const revealedChat = useMemo(
    () => chat.slice(0, Math.min(revealedCount, chat.length)),
    [chat, revealedCount],
  );

  // Real Action Plan agent utterances (LLM responses where AgentName ===
  // 'action_plan_agent'). These are shown in a separate collapsible
  // section at the bottom of the chat panel \u2014 the action plan is the
  // *output* of reasoning, not part of the back-and-forth.
  const actionPlanItems = useMemo(() => {
    const out: { text: string; stage: InvestigationStage }[] = [];
    for (const ln of visible) {
      const a = (ln.agent || '').toLowerCase();
      if (a !== 'action_planner' && a !== 'action_plan_agent') continue;
      if (!ln.isLlm) continue;
      const t = (ln.text || '').trim();
      if (!t) continue;
      out.push({ text: t, stage: ln.stage });
    }
    return out;
  }, [visible]);

  // Distinct agents that have spoken (in order of first appearance) — used
  // to lay out the circular topology.
  const cast = useMemo(() => {
    const order: string[] = [];
    const seen = new Set<string>();
    for (const ln of traceLines) {
      const a = ln.agent;
      if (!a || seen.has(a)) continue;
      seen.add(a);
      order.push(a);
    }
    // Fallback to a representative cast so the ring isn't empty before
    // any LLM events have been revealed. Narrator is intentionally
    // excluded from the ring — they describe the action, they aren't
    // a participant in it.
    if (order.length < 3) {
      const fallback = ['triage_agent', 'reasoner', 'evidence_planner', 'investigation_orchestrator', 'incident_collector', 'sli_collector', 'support_collector', 'runner', 'action_planner'];
      for (const f of fallback) {
        if (!seen.has(f)) order.push(f);
      }
    }
    return order.filter((a) => a !== 'narrator');
  }, [traceLines]);

  // Drive the agent topology from the *revealed* chat so the ring
  // lights up the agent that the just-typed bubble is talking about,
  // AND from any real agent_name that has actually emitted an event
  // in `visible` so far. The narrator paraphrases activity, so
  // `inferAgentFromText` can't catch every speaker (notably the
  // orchestrator, runner, and action_planner) — using the underlying
  // event stream as a second source guarantees those nodes light up
  // when their agents actually do work.
  const speakingAgents = useMemo(() => {
    const set = new Set<string>();
    for (const t of revealedChat) {
      const a = inferAgentFromText(t.text);
      if (a && a !== 'narrator') set.add(a);
    }
    for (const ln of visible) {
      const raw = (ln.agent || '').toLowerCase();
      if (!raw || raw === 'narrator') continue;
      // Canonicalise *_tool collectors back to their parent collector
      // name so the ring node lights up rather than rendering an
      // unknown free-floating tool.
      const canon =
        raw === 'action_plan_agent'
          ? 'action_planner'
          : raw.endsWith('_tool')
            ? raw.includes('incident')
              ? 'incident_collector'
              : raw.includes('sli')
                ? 'sli_collector'
                : raw.includes('support')
                  ? 'support_collector'
                  : 'runner'
            : raw;
      set.add(canon);
    }
    return set;
  }, [revealedChat, visible]);

  // The most recent two distinct non-narrator speakers \u2014 used to draw a
  // single ephemeral line from the previous speaker to the current one,
  // staying in lockstep with the chat reveal. Falls back to the most
  // recent real LLM event in `visible` when narrator text inference
  // yields nothing (orchestrator/runner/action_planner phases).
  const { currentSpeaker, previousSpeaker } = useMemo(() => {
    let curr: string | null = null;
    let prev: string | null = null;
    for (let i = revealedChat.length - 1; i >= 0; i--) {
      const a = inferAgentFromText(revealedChat[i].text);
      if (!a || a === 'narrator') continue;
      if (curr === null) {
        curr = a;
        continue;
      }
      if (a !== curr) {
        prev = a;
        break;
      }
    }
    if (curr == null) {
      // No narrator inference — fall back to the most recent revealed
      // LLM event with a real agent_name.
      for (let i = visible.length - 1; i >= 0; i--) {
        const ln = visible[i];
        if (!ln.isLlm) continue;
        const raw = (ln.agent || '').toLowerCase();
        if (!raw || raw === 'narrator') continue;
        const canon =
          raw === 'action_plan_agent'
            ? 'action_planner'
            : raw.endsWith('_tool')
              ? 'runner'
              : raw;
        if (curr === null) {
          curr = canon;
          continue;
        }
        if (canon !== curr) {
          prev = canon;
          break;
        }
      }
    }
    return { currentSpeaker: curr, previousSpeaker: prev };
  }, [revealedChat, visible]);

  const reachedSet = useMemo(() => new Set(reached), [reached]);
  const activeColor = STAGE_COLOR[active];

  // Auto-scroll the chat as new turns arrive.
  const chatEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }, [revealedChat.length]);

  return (
    <div
      style={{
        margin: '12px 20px 4px',
        padding: '18px 22px 22px',
        background:
          'radial-gradient(circle at 15% -10%, #18243a 0%, #0a121f 55%, #04070d 100%)',
        borderRadius: 16,
        border: '1px solid #1c2c44',
        boxShadow: 'none',
        color: '#e3eaf3',
        position: 'relative',
        overflow: 'hidden',
      }}
    >
      {/* Header strip */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 10,
          marginBottom: 12,
          fontSize: 11,
          letterSpacing: 0.6,
          textTransform: 'uppercase',
          color: '#7d92ad',
        }}
      >
        <span
          style={{
            width: 8,
            height: 8,
            borderRadius: '50%',
            background: complete ? '#00c853' : running ? activeColor : '#607d8b',
            boxShadow: running && !complete ? `0 0 10px ${activeColor}` : 'none',
            animation:
              running && !complete ? 'cha-pulse 1.2s ease-in-out infinite' : 'none',
          }}
        />
        <span style={{ fontWeight: 700, color: '#cfd8e3' }}>
          <i className="fa-solid fa-wave-square" style={{ marginRight: 6 }} />
          Investigation Reasoning
        </span>
        <span style={{ color: '#5d6f87' }}>{'\u00b7'}</span>
        <span style={{ color: '#9fb1c7' }}>{serviceName}</span>
        <span style={{ color: '#5d6f87' }}>{'\u00b7'}</span>
        <span
          style={{
            color: complete ? '#00c853' : activeColor,
            fontWeight: 700,
          }}
        >
          {complete ? 'Resolved' : running ? 'Reasoning' : 'Standing by'}
        </span>
        <span style={{ flex: 1 }} />
        <span
          style={{
            background: 'rgba(255,255,255,.04)',
            border: '1px solid #1a2a44',
            padding: '3px 10px',
            borderRadius: 999,
            color: '#9fb1c7',
            textTransform: 'none',
            letterSpacing: 0,
            fontWeight: 500,
          }}
          title={signalTitle}
        >
          <i className="fas fa-bolt" style={{ marginRight: 6, color: '#ffd93d' }} />
          {truncate(signalTitle || 'Investigation', 80)}
        </span>
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'minmax(0, 1.7fr) minmax(0, 1fr)',
          gap: 18,
        }}
      >
        {/* ── LEFT: chat transcript (the centerpiece) ─────────── */}
        <div
          style={{
            background: 'rgba(7,13,22,.65)',
            border: '1px solid #1a2a44',
            borderRadius: 12,
            display: 'flex',
            flexDirection: 'column',
            minHeight: 460,
            maxHeight: 520,
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              padding: '10px 14px',
              borderBottom: '1px solid #1a2a44',
              display: 'flex',
              alignItems: 'center',
              gap: 8,
              fontSize: 12,
              color: '#cfd8e3',
            }}
          >
            <i className="fas fa-brain" style={{ color: activeColor }} />
            <span style={{ fontWeight: 700 }}>Agent Reasoning</span>
            <span style={{ color: '#6e8197', fontWeight: 400 }}>
              {chat.length} message{chat.length === 1 ? '' : 's'}
            </span>
            <span style={{ flex: 1 }} />
            <span style={{ color: '#6e8197', fontSize: 11 }}>
              {STAGE_DISPLAY[active]}
              {currentSpeaker && (
                <>
                  <span style={{ margin: '0 6px' }}>{'\u00b7'}</span>
                  <span style={{ color: colorForAgent(currentSpeaker) }}>
                    {labelForAgent(currentSpeaker)} talking
                  </span>
                </>
              )}
            </span>
          </div>

          <div
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '14px 14px 0',
              display: 'flex',
              flexDirection: 'column',
              gap: 10,
            }}
          >
            {chat.length === 0 ? (
              <div
                style={{
                  color: '#6e8197',
                  fontStyle: 'italic',
                  fontSize: 13,
                  padding: '24px 0',
                  textAlign: 'center',
                }}
              >
                {running
                  ? 'Waiting for the first agent to chime in\u2026'
                  : complete
                    ? 'No agent messages captured for this XCV.'
                    : 'Standing by\u2026'}
              </div>
            ) : (
              revealedChat.map((turn, i) => (
                <ChatBubble key={`${turn.agent}-${i}`} turn={turn} />
              ))
            )}
            {/* Typing indicator: shows for the *upcoming* speaker while
                the next bubble is being prepared, falling back to the
                latest revealed speaker once the chat catches up. */}
            {(running || revealedCount < chat.length) &&
              (revealedCount < chat.length
                ? (
                    <ChatTyping
                      agent={inferAgentFromText(chat[revealedCount].text)}
                    />
                  )
                : currentSpeaker && <ChatTyping agent={currentSpeaker} />)}
            <div ref={chatEndRef} />
          </div>

          {/* Action plan strip \u2014 collapsed by default; opens to show what
              the dedicated Action Plan agent produced AFTER reasoning. */}
          <ActionPlanStrip items={actionPlanItems} />

          {/* Sandbox code execution strip \u2014 surfaces sandbox_code_generated
              + sandbox_execution_complete events. Auto-shows while a run is
              in flight; auto-hides 5s after success. */}
          <SandboxStrip runs={sandboxRuns} />
        </div>

        {/* ── RIGHT: circular agent ring (non-linear topology) ── */}
        <AgentRing
          cast={cast}
          speakingAgents={speakingAgents}
          currentSpeaker={currentSpeaker}
          previousSpeaker={previousSpeaker}
          activeColor={activeColor}
          stage={active}
          reachedSet={reachedSet}
          running={running}
          complete={complete}
        />
      </div>
    </div>
  );
}

interface ChatTurn {
  agent: string;
  text: string;
  isLlm: boolean;
  stage: InvestigationStage;
  tools: string[];
  /** "thinking" turns are the small dim "*reasoning ...*" rows that
   *  collapse runs of structural events between LLM utterances. */
  thinking: boolean;
}

/** Reduce a flat list of revealed TraceLines into a chat transcript:
 *  - each LLM line becomes a full bubble attributed to its agent
 *  - runs of non-LLM structural events from the same agent become a
 *    single "thinking" sub-line so the conversation stays readable */
function buildChatTurns(lines: TraceLine[]): ChatTurn[] {
  // Only surface narrator LLM responses (llm_response_text where
  // agent === 'narrator'). Each line stays attributed to the narrator
  // and is shown verbatim \u2014 no agent inference, no condensing.
  const out: ChatTurn[] = [];
  for (const ln of lines) {
    const agent = (ln.agent || '').toLowerCase();
    if (agent !== 'narrator') continue;
    if (!ln.isLlm) continue;
    const text = (ln.text || '').trim();
    if (!text) continue;
    out.push({
      agent: 'narrator',
      text,
      isLlm: true,
      stage: ln.stage,
      tools: [],
      thinking: false,
    });
  }
  return out;
}

function truncate(s: string, n: number): string {
  if (!s) return '';
  return s.length > n ? s.slice(0, n).trimEnd() + '\u2026' : s;
}

/* ── Chat bubble ───────────────────────────────────────────────── */

function ChatBubble({ turn }: { turn: ChatTurn }) {
  const color = colorForAgent(turn.agent);
  const stageColor = STAGE_COLOR[turn.stage];
  return (
    <div
      style={{
        display: 'flex',
        gap: 10,
        animation: 'cha-narration-fade .35s ease both',
      }}
    >
      {/* Avatar */}
      <div
        style={{
          flexShrink: 0,
          width: 36,
          height: 36,
          borderRadius: '50%',
          background: `linear-gradient(135deg, ${color}, ${color}99)`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#0a121f',
          fontSize: 14,
          boxShadow: `0 0 0 2px #0a121f, 0 0 12px ${color}66`,
        }}
        title={`${labelForAgent(turn.agent)} \u2014 ${roleForAgent(turn.agent)}`}
      >
        <i className={iconForAgent(turn.agent)} />
      </div>

      {/* Bubble body */}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            display: 'flex',
            alignItems: 'baseline',
            gap: 8,
            marginBottom: 3,
            fontSize: 11,
          }}
        >
          <span style={{ fontWeight: 700, color }}>
            {labelForAgent(turn.agent)}
          </span>
          <span style={{ color: '#6e8197', fontSize: 10 }}>
            {roleForAgent(turn.agent)}
          </span>
          <span style={{ flex: 1 }} />
          <span
            style={{
              fontSize: 9,
              padding: '2px 8px',
              borderRadius: 999,
              background: `${stageColor}22`,
              color: stageColor,
              border: `1px solid ${stageColor}55`,
              textTransform: 'uppercase',
              letterSpacing: 0.5,
              fontWeight: 600,
            }}
          >
            {STAGE_DISPLAY[turn.stage]}
          </span>
        </div>
        {turn.thinking ? (
          <div
            style={{
              fontSize: 12,
              color: '#9fb1c7',
              fontStyle: 'italic',
              padding: '8px 12px',
              background: 'rgba(255,255,255,.025)',
              border: '1px dashed #1a2a44',
              borderRadius: 10,
              display: 'flex',
              alignItems: 'center',
              gap: 8,
            }}
          >
            <i className="fas fa-cog fa-spin" style={{ color: '#6e8197' }} />
            <span>{turn.text}</span>
            {turn.tools.length > 0 && (
              <span
                style={{
                  marginLeft: 'auto',
                  fontSize: 10,
                  color: '#6e8197',
                  fontStyle: 'normal',
                }}
              >
                <i className="fas fa-wrench" style={{ marginRight: 4 }} />
                {turn.tools.join(', ')}
              </span>
            )}
          </div>
        ) : (
          <div
            style={{
              fontSize: 13.5,
              lineHeight: 1.55,
              color: '#eaf2fb',
              padding: '10px 14px',
              background: 'rgba(255,255,255,.04)',
              border: `1px solid ${color}33`,
              borderLeft: `3px solid ${color}`,
              borderRadius: '10px 12px 12px 4px',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {turn.text}
          </div>
        )}
      </div>
    </div>
  );
}

function ChatTyping({ agent }: { agent: string }) {
  const color = colorForAgent(agent);
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', opacity: 0.85 }}>
      <div
        style={{
          flexShrink: 0,
          width: 36,
          height: 36,
          borderRadius: '50%',
          background: `linear-gradient(135deg, ${color}, ${color}99)`,
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          color: '#0a121f',
          fontSize: 14,
          boxShadow: `0 0 0 2px #0a121f, 0 0 12px ${color}66`,
        }}
      >
        <i className={iconForAgent(agent)} />
      </div>
      <div
        style={{
          padding: '8px 14px',
          background: 'rgba(255,255,255,.04)',
          border: `1px solid ${color}33`,
          borderRadius: '10px 12px 12px 4px',
          display: 'flex',
          gap: 4,
          alignItems: 'center',
          fontSize: 12,
          color: color,
        }}
      >
        <span style={{ marginRight: 8, color: '#9fb1c7' }}>
          {labelForAgent(agent)} is thinking
        </span>
        <span style={{ ...typingDot, animationDelay: '0s', background: color }} />
        <span style={{ ...typingDot, animationDelay: '.2s', background: color }} />
        <span style={{ ...typingDot, animationDelay: '.4s', background: color }} />
      </div>
    </div>
  );
}

const typingDot: CSSProperties = {
  width: 5,
  height: 5,
  borderRadius: '50%',
  display: 'inline-block',
  animation: 'cha-typing 1.1s ease-in-out infinite',
};

/* ── Relationship graph (Symptom → Hypothesis → Evidence) ────
   3-column SVG layout with bezier connectors, mirroring the look of
   Neural Canvas v2's RelationshipGraph but driven by live trace data. */

interface RelationshipTreeProps {
  signalTitle: string;
  symptoms: { title: string; hypothesis?: string; confidence?: number }[];
  hypotheses: Hypothesis[];
  evidence: { label: string; tool: string }[];
}

const REL_COL_W = 230;
const REL_NODE_H = 56;
const REL_NODE_GAP = 12;
const REL_PAD_Y = 20;
const REL_COL_GAP = 70;
const REL_PAD_X = 20;
const REL_COL_HEADER_H = 28;

function RelationshipTree({
  signalTitle,
  symptoms,
  hypotheses,
  evidence,
}: RelationshipTreeProps) {
  const hasContent =
    symptoms.length > 0 || hypotheses.length > 0 || evidence.length > 0;

  // Node sets used for rendering. If symptoms haven't surfaced yet but
  // hypotheses have, fall back to a single synthetic symptom so the
  // graph still has a meaningful left column.
  const symptomNodes = useMemo(() => {
    if (symptoms.length > 0) return symptoms.map((s, i) => ({ id: `S${i}`, title: s.title }));
    if (hypotheses.length > 0 || evidence.length > 0) {
      return [{ id: 'S0', title: signalTitle || 'Detected symptoms' }];
    }
    return [];
  }, [symptoms, hypotheses, evidence, signalTitle]);

  // Edges: symptom -> hypothesis (round-robin), hypothesis -> evidence (round-robin).
  const symHypEdges = useMemo(() => {
    const out: { from: string; to: string }[] = [];
    if (symptomNodes.length === 0) return out;
    hypotheses.forEach((h, i) => {
      out.push({ from: symptomNodes[i % symptomNodes.length].id, to: h.id });
    });
    return out;
  }, [symptomNodes, hypotheses]);

  const hypEvEdges = useMemo(() => {
    const out: { from: string; to: string }[] = [];
    if (hypotheses.length === 0) return out;
    evidence.forEach((e, i) => {
      out.push({ from: hypotheses[i % hypotheses.length].id, to: `E${i}` });
    });
    return out;
  }, [hypotheses, evidence]);

  const rows = Math.max(symptomNodes.length, hypotheses.length, evidence.length, 1);
  const totalW = REL_PAD_X * 2 + REL_COL_W * 3 + REL_COL_GAP * 2;
  const totalH =
    REL_PAD_Y * 2 +
    REL_COL_HEADER_H +
    rows * REL_NODE_H +
    Math.max(0, rows - 1) * REL_NODE_GAP;

  // Helper: y-center of a node at index `i` in a column.
  const nodeCY = (i: number) =>
    REL_PAD_Y + REL_COL_HEADER_H + i * (REL_NODE_H + REL_NODE_GAP) + REL_NODE_H / 2;
  const colX = (col: 0 | 1 | 2) => REL_PAD_X + col * (REL_COL_W + REL_COL_GAP);

  const sympIdxById = new Map(symptomNodes.map((s, i) => [s.id, i]));
  const hypIdxById = new Map(hypotheses.map((h, i) => [h.id, i]));

  return (
    <div
      style={{
        margin: '0 20px 20px',
        background: '#fff',
        border: '1px solid #e8e8e8',
        borderRadius: 10,
        padding: '14px 18px 18px',
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          marginBottom: 12,
          fontSize: 12,
          fontWeight: 700,
          color: '#444',
          textTransform: 'uppercase',
          letterSpacing: 0.6,
        }}
      >
        <i className="fas fa-project-diagram" style={{ color: '#3aa0ff' }} />
        Relationship graph
        <span
          style={{
            fontSize: 10,
            fontWeight: 500,
            color: '#888',
            textTransform: 'none',
            letterSpacing: 0,
            marginLeft: 6,
          }}
        >
          symptom &rarr; hypothesis &rarr; evidence
        </span>
      </div>

      {!hasContent ? (
        <div
          style={{
            color: '#999',
            fontStyle: 'italic',
            fontSize: 12.5,
            padding: '12px 0',
          }}
        >
          Waiting for the agent to surface symptoms, hypotheses, and evidence&hellip;
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <svg
            width={totalW}
            height={totalH}
            viewBox={`0 0 ${totalW} ${totalH}`}
            style={{ display: 'block', minWidth: totalW }}
          >
            <defs>
              <marker id="rel-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
                <path d="M 0 0 L 8 4 L 0 8 Z" fill="#b8c2d4" />
              </marker>
            </defs>

            {/* Column headers */}
            <RelColHeader x={colX(0)} label="Symptoms"   color="#e67e22" icon="\uf21e" />
            <RelColHeader x={colX(1)} label="Hypotheses" color="#9b59b6" icon="\uf0eb" />
            <RelColHeader x={colX(2)} label="Evidence"   color="#3498db" icon="\uf0c3" />

            {/* Edges: symptom -> hypothesis */}
            {symHypEdges.map((e, i) => {
              const sIdx = sympIdxById.get(e.from);
              const hIdx = hypIdxById.get(e.to);
              if (sIdx == null || hIdx == null) return null;
              const x1 = colX(0) + REL_COL_W;
              const y1 = nodeCY(sIdx);
              const x2 = colX(1);
              const y2 = nodeCY(hIdx);
              const cx = (x1 + x2) / 2;
              return (
                <path
                  key={`sh-${i}`}
                  d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="#d8a6e8"
                  strokeWidth={1.4}
                  opacity={0.75}
                  markerEnd="url(#rel-arrow)"
                />
              );
            })}

            {/* Edges: hypothesis -> evidence */}
            {hypEvEdges.map((e, i) => {
              const hIdx = hypIdxById.get(e.from);
              if (hIdx == null) return null;
              const eIdx = parseInt(e.to.slice(1), 10);
              const x1 = colX(1) + REL_COL_W;
              const y1 = nodeCY(hIdx);
              const x2 = colX(2);
              const y2 = nodeCY(eIdx);
              const cx = (x1 + x2) / 2;
              return (
                <path
                  key={`he-${i}`}
                  d={`M ${x1} ${y1} C ${cx} ${y1}, ${cx} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="#a6c5e8"
                  strokeWidth={1.4}
                  opacity={0.75}
                  markerEnd="url(#rel-arrow)"
                />
              );
            })}

            {/* Symptom nodes */}
            {symptomNodes.map((s, i) => (
              <RelNode
                key={`sn-${s.id}`}
                x={colX(0)}
                y={nodeCY(i) - REL_NODE_H / 2}
                w={REL_COL_W}
                h={REL_NODE_H}
                fill="#fff7ed"
                stroke="#fdd9b5"
                accent="#e67e22"
                title={s.title}
                subtitle="Symptom"
              />
            ))}

            {/* Hypothesis nodes */}
            {hypotheses.map((h, i) => (
              <RelNode
                key={`hn-${h.id}-${i}`}
                x={colX(1)}
                y={nodeCY(i) - REL_NODE_H / 2}
                w={REL_COL_W}
                h={REL_NODE_H}
                fill="#f6f1ff"
                stroke="#d9c5fb"
                accent={h.badgeColor}
                badge={h.id}
                title={h.description}
                subtitle={`${h.score}% confidence`}
              />
            ))}

            {/* Evidence nodes */}
            {evidence.map((e, i) => (
              <RelNode
                key={`en-${i}`}
                x={colX(2)}
                y={nodeCY(i) - REL_NODE_H / 2}
                w={REL_COL_W}
                h={REL_NODE_H}
                fill="#eaf3ff"
                stroke="#c9defc"
                accent="#3498db"
                title={e.label}
                subtitle="Tool / Evidence"
              />
            ))}
          </svg>
        </div>
      )}
    </div>
  );
}

function RelColHeader({ x, label, color, icon }: { x: number; label: string; color: string; icon: string }) {
  return (
    <g>
      <text
        x={x}
        y={REL_PAD_Y + 14}
        fontSize={11}
        fontWeight={700}
        fill={color}
        style={{ textTransform: 'uppercase', letterSpacing: 0.6 }}
      >
        <tspan fontFamily='"Font Awesome 6 Free"' fontWeight={900} dx={0}>{icon}</tspan>
        <tspan dx={6}>{label}</tspan>
      </text>
    </g>
  );
}

interface RelNodeProps {
  x: number;
  y: number;
  w: number;
  h: number;
  fill: string;
  stroke: string;
  accent: string;
  title: string;
  subtitle?: string;
  badge?: string;
}
function RelNode({ x, y, w, h, fill, stroke, accent, title, subtitle, badge }: RelNodeProps) {
  // Truncate the title to fit within the node width.
  const maxChars = badge ? 26 : 32;
  const display = title.length > maxChars ? title.slice(0, maxChars - 1) + '\u2026' : title;
  return (
    <g>
      <rect x={x} y={y} width={w} height={h} rx={8} ry={8} fill={fill} stroke={stroke} strokeWidth={1} />
      <rect x={x} y={y} width={4} height={h} rx={2} ry={2} fill={accent} />
      {badge ? (
        <>
          <rect x={x + 12} y={y + 9} width={56} height={16} rx={3} ry={3} fill={accent} />
          <text x={x + 40} y={y + 21} fontSize={10} fontWeight={700} fill="#fff" textAnchor="middle">{badge}</text>
          <text x={x + 76} y={y + 22} fontSize={11.5} fontWeight={600} fill="#1a1a2e">
            <title>{title}</title>
            {display}
          </text>
        </>
      ) : (
        <text x={x + 12} y={y + 22} fontSize={11.5} fontWeight={600} fill="#1a1a2e">
          <title>{title}</title>
          {display}
        </text>
      )}
      {subtitle && (
        <text x={x + 12} y={y + h - 12} fontSize={10} fill="#666">
          {subtitle}
        </text>
      )}
    </g>
  );
}

/* ── Action plan strip (output of the action_plan_agent, after reasoning) ── */

function TeamsChannelButton({
  info,
  loading,
}: {
  info: TeamsChannelInfo | null;
  loading: boolean;
}) {
  if (loading && !info) {
    return (
      <button
        style={{ ...S.loadBtn, background: '#eee', color: '#555', cursor: 'wait' } as CSSProperties}
        disabled
      >
        <i className="fas fa-spinner fa-spin" /> Teams{'\u2026'}
      </button>
    );
  }
  if (info?.enabled && info.web_url) {
    return (
      <a
        href={info.web_url}
        target="_blank"
        rel="noopener noreferrer"
        style={
          {
            ...S.loadBtn,
            background: '#4b53bc',
            color: '#fff',
            textDecoration: 'none',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
          } as CSSProperties
        }
        title={
          info.created
            ? `New channel created: ${info.display_name || ''}`
            : `Open channel: ${info.display_name || ''}`
        }
      >
        <i className="fas fa-users" /> Join Teams channel
      </a>
    );
  }
  return (
    <button
      style={
        {
          ...S.loadBtn,
          background: '#1f9b6e',
          color: '#fff',
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
        } as CSSProperties
      }
      title={info?.message || 'Teams integration not configured (demo)'}
      onClick={(e) => e.preventDefault()}
    >
      <i className="fas fa-users" /> Join Teams channel
    </button>
  );
}

/* ── Email opt-in button ──────────────────────────────────────────────
 * Lets a user supply their email and subscribe for "investigation
 * started" + "investigation resolved" emails for this XCV. Backend
 * sends the start email immediately and the resolved email is fired
 * automatically by ServicePanel when the replay reaches its final stage.
 */
interface EmailOptInButtonProps {
  xcv: string;
  customerName: string;
  serviceName: string;
  signalTitle: string;
  teamsWebUrl: string | null;
  subscriberCount: number;
  onSubscribed: (resp: SubscribeResponse) => void;
}

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;
const EMAIL_STORAGE_KEY = 'cha-email-optin';

function EmailOptInButton({
  xcv,
  customerName,
  serviceName,
  signalTitle,
  teamsWebUrl,
  subscriberCount,
  onSubscribed,
}: EmailOptInButtonProps) {
  const [open, setOpen] = useState(false);
  const [email, setEmail] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<string | null>(null);

  // Pre-fill from prior session so users don't retype.
  useEffect(() => {
    if (typeof window === 'undefined') return;
    try {
      const v = window.localStorage.getItem(EMAIL_STORAGE_KEY);
      if (v) setEmail(v);
    } catch {
      /* noop */
    }
  }, []);

  // Reset transient feedback when the XCV changes.
  useEffect(() => {
    setStatus(null);
    setError(null);
  }, [xcv]);

  const submit = async () => {
    const cleaned = email.trim().toLowerCase();
    if (!EMAIL_RE.test(cleaned)) {
      setError('Please enter a valid email address.');
      return;
    }
    setSubmitting(true);
    setError(null);
    setStatus(null);
    try {
      const resp = await subscribeEmail({
        xcv,
        email: cleaned,
        customer_name: customerName,
        service_name: serviceName,
        signal_title: signalTitle,
        ui_url: typeof window !== 'undefined' ? window.location.href : undefined,
        teams_web_url: teamsWebUrl ?? undefined,
      });
      try {
        window.localStorage.setItem(EMAIL_STORAGE_KEY, cleaned);
      } catch {
        /* noop */
      }
      onSubscribed(resp);
      if (!resp.enabled) {
        setError(resp.message || 'Email integration is disabled on the server.');
      } else if (resp.already_subscribed) {
        setStatus('You are already subscribed for this XCV.');
      } else {
        setStatus(
          resp.started_email_sent
            ? 'Subscribed \u2014 a confirmation email is on the way.'
            : 'Subscribed.',
        );
      }
    } catch (err) {
      setError((err as Error)?.message || 'Failed to subscribe');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ position: 'relative', display: 'inline-block' }}>
      <button
        style={
          {
            ...S.loadBtn,
            background: '#1f9b6e',
            color: '#fff',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
          } as CSSProperties
        }
        onClick={() => setOpen((v) => !v)}
        title="Subscribe to email updates for this investigation"
      >
        <i className="fas fa-envelope" /> Email me updates
        {subscriberCount > 0 && (
          <span
            style={{
              background: 'rgba(255,255,255,.18)',
              padding: '1px 6px',
              borderRadius: 999,
              fontSize: 10,
              fontWeight: 700,
            }}
          >
            {subscriberCount}
          </span>
        )}
      </button>
      {open && (
        <div
          style={{
            position: 'absolute',
            top: 'calc(100% + 6px)',
            right: 0,
            zIndex: 50,
            background: '#ffffff',
            border: '1px solid #d6dde8',
            borderRadius: 10,
            boxShadow: '0 8px 24px rgba(15,30,55,.18)',
            padding: 14,
            width: 320,
            color: '#1f2a3a',
          }}
        >
          <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 4 }}>
            Subscribe to investigation updates
          </div>
          <div style={{ fontSize: 12, color: '#6e7c91', marginBottom: 10 }}>
            We’ll email you now and again when this investigation resolves.
          </div>
          <input
            type="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') submit();
            }}
            disabled={submitting}
            style={{
              width: '100%',
              boxSizing: 'border-box',
              padding: '8px 10px',
              border: '1px solid #c7d0de',
              borderRadius: 6,
              fontSize: 13,
              outline: 'none',
            }}
          />
          {error && (
            <div style={{ marginTop: 8, color: '#c0392b', fontSize: 12 }}>{error}</div>
          )}
          {status && (
            <div style={{ marginTop: 8, color: '#1f9b6e', fontSize: 12 }}>{status}</div>
          )}
          <div style={{ marginTop: 12, display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
            <button
              style={
                {
                  ...S.loadBtn,
                  background: '#eef1f6',
                  color: '#1f2a3a',
                } as CSSProperties
              }
              onClick={() => setOpen(false)}
              disabled={submitting}
            >
              Close
            </button>
            <button
              style={
                {
                  ...S.loadBtn,
                  background: '#1f9b6e',
                  color: '#fff',
                  cursor: submitting ? 'wait' : 'pointer',
                } as CSSProperties
              }
              onClick={submit}
              disabled={submitting}
            >
              {submitting ? (
                <>
                  <i className="fas fa-spinner fa-spin" /> Subscribing…
                </>
              ) : (
                <>
                  <i className="fas fa-paper-plane" /> Subscribe
                </>
              )}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

function ActionPlanStrip({ items }: { items: { text: string; stage: InvestigationStage }[] }) {
  const meta = AGENT_META.action_planner;
  const color = meta.color;
  const hasItems = items.length > 0;
  return (
    <details
      open={hasItems}
      style={{
        borderTop: '1px solid #1a2a44',
        background: hasItems
          ? `linear-gradient(180deg, rgba(240,98,146,.06), rgba(7,13,22,.65))`
          : 'rgba(7,13,22,.65)',
      }}
    >
      <summary
        style={{
          listStyle: 'none',
          cursor: 'pointer',
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12,
          color: '#cfd8e3',
          userSelect: 'none',
        }}
      >
        <i className={meta.icon} style={{ color }} />
        <span style={{ fontWeight: 700 }}>Action Plan</span>
        <span style={{ color: '#6e8197', fontWeight: 400 }}>
          {hasItems
            ? `${items.length} step${items.length === 1 ? '' : 's'} drafted after reasoning`
            : 'pending \u2014 will appear after reasoning completes'}
        </span>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 10,
            color,
            border: `1px solid ${color}55`,
            background: `${color}15`,
            padding: '2px 8px',
            borderRadius: 999,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            fontWeight: 700,
          }}
        >
          Action Plan Agent
        </span>
        <i className="fa-solid fa-chevron-down" style={{ color: '#6e8197', fontSize: 10 }} />
      </summary>
      <div
        style={{
          padding: '4px 14px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxHeight: 460,
          overflowY: 'auto',
        }}
      >
        {!hasItems ? (
          <div
            style={{
              color: '#6e8197',
              fontSize: 12,
              fontStyle: 'italic',
              padding: '8px 4px',
            }}
          >
            The Action Plan agent runs once reasoning completes and posts its
            remediation steps here.
          </div>
        ) : (
          items.map((it, i) => {
            const stageColor = STAGE_COLOR[it.stage];
            return (
              <div
                key={i}
                style={{
                  display: 'flex',
                  gap: 10,
                  padding: '8px 10px',
                  background: 'rgba(255,255,255,.035)',
                  border: `1px solid ${color}33`,
                  borderLeft: `3px solid ${color}`,
                  borderRadius: 8,
                  fontSize: 13,
                  lineHeight: 1.5,
                  color: '#eaf2fb',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  animation: 'cha-narration-fade .35s ease both',
                }}
              >
                <span
                  style={{
                    flexShrink: 0,
                    width: 22,
                    height: 22,
                    borderRadius: '50%',
                    background: color,
                    color: '#0a121f',
                    fontSize: 11,
                    fontWeight: 800,
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                  }}
                >
                  {i + 1}
                </span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div
                    style={{
                      fontSize: 9,
                      color: stageColor,
                      letterSpacing: 0.5,
                      textTransform: 'uppercase',
                      fontWeight: 700,
                      marginBottom: 2,
                    }}
                  >
                    {STAGE_DISPLAY[it.stage]}
                  </div>
                  {it.text}
                </div>
              </div>
            );
          })
        )}
      </div>
    </details>
  );
}

/* ── Sandbox code-execution strip ──────────────────────────────────────
 * Surfaces sandbox_code_generated + sandbox_execution_complete events
 * (the agent's "I'm executing some Python" trace). Renders the full run
 * history so it remains visible after the investigation resolves. The
 * latest run is expanded by default; older runs collapse to a header and
 * can be expanded on click.
 */

interface SandboxStripProps {
  runs: SandboxRun[];
}

function SandboxStrip({ runs }: SandboxStripProps) {
  if (!runs || runs.length === 0) return null;

  // Newest run first so the operator sees the most recent execution at top.
  const ordered = [...runs].reverse();
  const latestId = ordered[0]?.id;
  const anyFailed = runs.some((r) => r.success === false);
  const anyInFlight = runs.some((r) => r.completedAtMs == null);
  const headerAccent = anyFailed ? '#e74c3c' : anyInFlight ? '#fdcb6e' : '#27ae60';

  return (
    <details
      style={{
        borderTop: '1px solid #1a2a44',
        background: 'rgba(7,13,22,.65)',
      }}
    >
      <summary
        style={{
          listStyle: 'none',
          cursor: 'pointer',
          padding: '10px 14px',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12,
          color: '#cfd8e3',
          userSelect: 'none',
        }}
      >
        <i className="fa-solid fa-flask" style={{ color: headerAccent }} />
        <span style={{ fontWeight: 700 }}>Sandbox Executions</span>
        <span style={{ color: '#6e8197', fontWeight: 400 }}>
          {runs.length} run{runs.length === 1 ? '' : 's'}
          {anyFailed ? ' \u2014 contains failures' : anyInFlight ? ' \u2014 in progress' : ''}
        </span>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 10,
            color: headerAccent,
            border: `1px solid ${headerAccent}55`,
            background: `${headerAccent}15`,
            padding: '2px 8px',
            borderRadius: 999,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            fontWeight: 700,
          }}
        >
          Sandbox Coder
        </span>
        <i className="fa-solid fa-chevron-down" style={{ color: '#6e8197', fontSize: 10 }} />
      </summary>
      <div
        style={{
          padding: '4px 14px 14px',
          display: 'flex',
          flexDirection: 'column',
          gap: 8,
          maxHeight: 560,
          overflowY: 'auto',
        }}
      >
        {ordered.map((run) => (
          <SandboxRunCard key={run.id} run={run} defaultOpen={run.id === latestId} />
        ))}
      </div>
    </details>
  );
}

interface SandboxRunCardProps {
  run: SandboxRun;
  defaultOpen: boolean;
}

function SandboxRunCard({ run, defaultOpen }: SandboxRunCardProps) {
  const [open, setOpen] = useState(defaultOpen);
  const inFlight = run.completedAtMs == null;
  const failed = run.success === false;
  const succeeded = run.success === true;
  const accent = failed ? '#e74c3c' : succeeded ? '#27ae60' : '#fdcb6e';

  return (
    <div
      style={{
        border: `1px solid ${accent}33`,
        background: `linear-gradient(180deg, ${accent}10, rgba(7,13,22,.85))`,
        borderRadius: 10,
        animation: 'cha-narration-fade .3s ease both',
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        style={{
          width: '100%',
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          padding: '8px 12px',
          background: 'transparent',
          border: 'none',
          color: '#cfd8e3',
          fontSize: 12,
          cursor: 'pointer',
          textAlign: 'left',
        }}
      >
        <i
          className={
            inFlight
              ? 'fa-solid fa-spinner fa-spin'
              : failed
                ? 'fa-solid fa-circle-xmark'
                : 'fa-solid fa-circle-check'
          }
          style={{ color: accent }}
        />
        <span style={{ fontWeight: 700 }}>{run.agent || 'sandbox'}</span>
        <span style={{ color: '#6e8197', fontWeight: 400 }}>
          {inFlight
            ? `executing ${run.language} code\u2026`
            : failed
              ? `failed${run.error ? `: ${run.error}` : ''}`
              : `completed in ${
                  run.durationSeconds != null ? `${run.durationSeconds.toFixed(2)}s` : '\u2014'
                }`}
        </span>
        <span style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 10,
            color: accent,
            border: `1px solid ${accent}55`,
            background: `${accent}15`,
            padding: '2px 8px',
            borderRadius: 999,
            textTransform: 'uppercase',
            letterSpacing: 0.5,
            fontWeight: 700,
          }}
        >
          {inFlight ? 'Running' : failed ? 'Failed' : 'Success'}
        </span>
        <i
          className={`fa-solid ${open ? 'fa-chevron-up' : 'fa-chevron-down'}`}
          style={{ color: '#6e8197', fontSize: 11 }}
        />
      </button>
      {open && (
        <div style={{ padding: '0 12px 12px' }}>
          <SandboxCodeBlock code={run.code} language={run.language || 'python'} />
          {!inFlight && (run.stdout || run.stderr) && (
            <SandboxOutputBlock stdout={run.stdout} stderr={run.stderr} accent={accent} />
          )}
        </div>
      )}
    </div>
  );
}

function SandboxCodeBlock({ code, language }: { code: string; language: string }) {
  const langKey = (language || 'python').toLowerCase();
  const grammar = Prism.languages[langKey] || Prism.languages.python;
  const html = useMemo(
    () => Prism.highlight(code || '', grammar, langKey),
    [code, grammar, langKey],
  );
  return (
    <pre
      style={{
        margin: 0,
        padding: '10px 12px',
        background: '#0b1220',
        border: '1px solid #1c2c44',
        borderRadius: 8,
        fontSize: 12,
        lineHeight: 1.5,
        maxHeight: 380,
        overflow: 'auto',
        color: '#eaf2fb',
        fontFamily:
          '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
      }}
    >
      <code
        className={`language-${langKey}`}
        // Prism produces token markup; this is the standard Prism pattern.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </pre>
  );
}

function SandboxOutputBlock({
  stdout,
  stderr,
  accent,
}: {
  stdout: string;
  stderr: string;
  accent: string;
}) {
  const text = stderr ? stderr : stdout;
  const isErr = !!stderr;
  return (
    <pre
      style={{
        margin: '8px 0 0',
        padding: '8px 12px',
        background: 'rgba(0,0,0,.45)',
        border: `1px solid ${isErr ? '#e74c3c55' : `${accent}33`}`,
        borderLeft: `3px solid ${isErr ? '#e74c3c' : accent}`,
        borderRadius: 6,
        fontSize: 12,
        lineHeight: 1.5,
        maxHeight: 200,
        overflow: 'auto',
        color: isErr ? '#ffb3a8' : '#cfeacd',
        fontFamily:
          '"JetBrains Mono", "Fira Code", "Consolas", "Monaco", monospace',
        whiteSpace: 'pre-wrap',
        wordBreak: 'break-word',
      }}
    >
      <span style={{ color: '#6e8197', fontSize: 10, letterSpacing: 0.5, fontWeight: 700 }}>
        {isErr ? 'STDERR' : 'STDOUT'}
      </span>
      {'\n'}
      {text}
    </pre>
  );
}

/* \u2500\u2500 Circular agent topology \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500 */

interface AgentRingProps {
  cast: string[];
  speakingAgents: Set<string>;
  currentSpeaker: string | null;
  previousSpeaker: string | null;
  activeColor: string;
  stage: InvestigationStage;
  reachedSet: Set<InvestigationStage>;
  running: boolean;
  complete: boolean;
}

function AgentRing({
  cast,
  speakingAgents,
  currentSpeaker,
  previousSpeaker,
  activeColor,
  stage,
  reachedSet,
  running,
  complete,
}: AgentRingProps) {
  const SIZE = 360;
  const cx = SIZE / 2;
  const cy = SIZE / 2;
  const r = SIZE * 0.36;

  const positions = useMemo(() => {
    const n = Math.max(cast.length, 1);
    return cast.map((agent, i) => {
      // Start at top, sweep clockwise.
      const theta = -Math.PI / 2 + (i / n) * Math.PI * 2;
      return {
        agent,
        x: cx + r * Math.cos(theta),
        y: cy + r * Math.sin(theta),
      };
    });
  }, [cast, cx, cy, r]);

  return (
    <div
      style={{
        background: 'rgba(7,13,22,.65)',
        border: '1px solid #1a2a44',
        borderRadius: 12,
        padding: '12px 12px 14px',
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 8,
          fontSize: 12,
          color: '#cfd8e3',
        }}
      >
        <i className="fas fa-circle-nodes" style={{ color: activeColor }} />
        <span style={{ fontWeight: 700 }}>Agent Topology</span>
        <span style={{ color: '#6e8197', fontWeight: 400 }}>
          {cast.length} agent{cast.length === 1 ? '' : 's'} {'\u00b7'} non-linear
        </span>
      </div>

      <div style={{ position: 'relative', width: '100%', aspectRatio: '1 / 1' }}>
        <svg
          viewBox={`0 0 ${SIZE} ${SIZE}`}
          style={{ width: '100%', height: '100%', display: 'block' }}
        >
          <defs>
            <radialGradient id="cha-ring-core" cx="50%" cy="50%" r="50%">
              <stop offset="0%" stopColor={activeColor} stopOpacity="0.85" />
              <stop offset="60%" stopColor={activeColor} stopOpacity="0.18" />
              <stop offset="100%" stopColor={activeColor} stopOpacity="0" />
            </radialGradient>
          </defs>

          {/* Orbit circle */}
          <circle
            cx={cx}
            cy={cy}
            r={r}
            fill="none"
            stroke="#1a2a44"
            strokeDasharray="2 5"
            strokeWidth={1}
          />
          {/* Single ephemeral line: previous speaker → current speaker.
              No permanent cross-talk web; the line moves with the
              conversation so the audience always sees "who handed off
              to whom" in this very moment. */}
          {(() => {
            if (!currentSpeaker || !previousSpeaker) return null;
            const a = positions.find((p) => p.agent === previousSpeaker);
            const b = positions.find((p) => p.agent === currentSpeaker);
            if (!a || !b) return null;
            const mx = (a.x + b.x) / 2;
            const my = (a.y + b.y) / 2;
            // Arrow tip at the current speaker.
            return (
              <g key={`${previousSpeaker}->${currentSpeaker}`}>
                <line
                  x1={a.x}
                  y1={a.y}
                  x2={b.x}
                  y2={b.y}
                  stroke={activeColor}
                  strokeWidth={2}
                  strokeLinecap="round"
                  opacity={0.85}
                  pathLength={1}
                  strokeDasharray="1 1"
                  style={{ filter: `drop-shadow(0 0 6px ${activeColor})` }}
                >
                  <animate
                    attributeName="stroke-dashoffset"
                    from="1"
                    to="0"
                    dur="0.6s"
                    fill="freeze"
                  />
                </line>
                <circle cx={mx} cy={my} r={3} fill={activeColor} opacity={0.9}>
                  <animate
                    attributeName="r"
                    values="2;5;2"
                    dur="1.2s"
                    repeatCount="indefinite"
                  />
                </circle>
              </g>
            );
          })()}
          {/* Center stage circle (no outward glow) */}
          <circle
            cx={cx}
            cy={cy}
            r={20}
            fill={complete ? '#00c853' : activeColor}
            opacity={0.85}
          />
          <text
            x={cx}
            y={cy + 4}
            textAnchor="middle"
            fontSize={11}
            fontWeight={700}
            fill="#0a121f"
          >
            {STAGE_DISPLAY[stage]}
          </text>
        </svg>

        {/* Agent nodes positioned absolutely on top of the SVG */}
        {positions.map((p) => {
          const speaking = speakingAgents.has(p.agent);
          const isCurrent = currentSpeaker === p.agent;
          const color = colorForAgent(p.agent);
          const left = `${(p.x / SIZE) * 100}%`;
          const top = `${(p.y / SIZE) * 100}%`;
          // Active speaker is amplified — bigger node, brighter glow,
          // bolder label — so the audience instantly sees who's talking.
          const nodeSize = isCurrent ? 50 : 36;
          return (
            <div
              key={p.agent}
              title={`${labelForAgent(p.agent)} \u2014 ${roleForAgent(p.agent)}`}
              style={{
                position: 'absolute',
                left,
                top,
                transform: 'translate(-50%, -50%)',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                gap: 4,
                pointerEvents: 'none',
                zIndex: isCurrent ? 2 : 1,
              }}
            >
              <div
                style={{
                  width: nodeSize,
                  height: nodeSize,
                  borderRadius: '50%',
                  background: isCurrent
                    ? color
                    : speaking
                      ? `${color}cc`
                      : '#0f1c30',
                  border: isCurrent
                    ? `2px solid ${color}`
                    : speaking
                      ? `1.5px solid ${color}aa`
                      : '1px solid #2a3c5a',
                  color: speaking || isCurrent ? '#0a121f' : '#8a9bb3',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: isCurrent ? 18 : 14,
                  boxShadow: isCurrent
                    ? `0 0 0 3px #0a121f, 0 0 0 4px ${color}66`
                    : '0 0 0 2px #0a121f',
                  animation: isCurrent ? 'cha-pulse 1.4s ease-in-out infinite' : 'none',
                  transition: 'width .25s ease, height .25s ease, border .2s ease, background .2s ease, box-shadow .25s ease',
                }}
              >
                <i className={iconForAgent(p.agent)} />
              </div>
              <div
                style={{
                  fontSize: isCurrent ? 11 : 9.5,
                  color: isCurrent ? '#ffffff' : speaking ? '#cfd8e3' : '#6e8197',
                  whiteSpace: 'nowrap',
                  textShadow: '0 1px 2px rgba(0,0,0,.7)',
                  fontWeight: isCurrent ? 700 : 600,
                }}
              >
                {labelForAgent(p.agent)}
              </div>
            </div>
          );
        })}
      </div>

      {/* Stage progress chips along the bottom \u2014 still here for context,
          but visually subordinate to the chat. */}
      <div
        style={{
          display: 'flex',
          flexWrap: 'wrap',
          gap: 4,
          fontSize: 10,
        }}
      >
        {INVESTIGATION_STAGES.map((s) => {
          const r = reachedSet.has(s);
          const a = s === stage && running && !complete;
          const c = STAGE_COLOR[s];
          return (
            <span
              key={s}
              style={{
                padding: '3px 8px',
                borderRadius: 999,
                background: a ? `${c}33` : r ? '#0f1c30' : '#0a1322',
                border: a ? `1px solid ${c}` : r ? `1px solid ${c}55` : '1px solid #1d2c45',
                color: r || a ? '#cfd8e3' : '#5d6f87',
                fontWeight: 600,
                letterSpacing: 0.3,
              }}
            >
              {STAGE_DISPLAY[s]}
            </span>
          );
        })}
      </div>
    </div>
  );
}
