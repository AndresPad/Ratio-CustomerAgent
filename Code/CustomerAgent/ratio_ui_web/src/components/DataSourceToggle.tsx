/**
 * DataSourceToggle — segmented control + optional xcv input used by the
 * Theatre and Live Orchestration pages to pick where their event stream
 * comes from:
 *
 *   Mock    — local scripted fixture (deterministic demos)
 *   Replay  — a past investigation fetched from App Insights by xcv
 *   Live    — the current agent pipeline (POST /api/run SSE)
 *
 * The toggle is a self-contained presentational component; the parent
 * page owns the selected mode/xcv state and passes them to
 * `streamOrchestration()` in `api/orchestrationSource.ts`.
 */
import { useEffect, useState, type CSSProperties } from 'react';
import type { OrchestrationMode } from '../api/orchestrationSource';
import { getReplayHealth } from '../api/orchestrationSource';

export interface DataSourceToggleProps {
  mode: OrchestrationMode;
  xcv: string;
  disabled?: boolean;
  onModeChange: (mode: OrchestrationMode) => void;
  onXcvChange: (xcv: string) => void;
  /** Optional replay agent-name filter (e.g. 'narrator'). Only rendered if
   *  both the change handler and current value are provided by the parent. */
  agentFilter?: string;
  onAgentFilterChange?: (agent: string) => void;
}

const BUTTON_BASE: CSSProperties = {
  padding: '6px 12px',
  fontSize: 11,
  fontWeight: 600,
  letterSpacing: 0.3,
  textTransform: 'uppercase',
  border: '1px solid var(--cha-border)',
  background: 'var(--cha-bg-white)',
  color: 'var(--cha-text-primary)',
  cursor: 'pointer',
  transition: 'all 0.15s ease',
};

const OPTIONS: { id: OrchestrationMode; label: string; icon: string; accent: string; title: string }[] = [
  { id: 'mock',   label: 'Mock',    icon: 'fa-flask',          accent: '#7c3aed', title: 'Scripted demo fixture (no network, deterministic)' },
  { id: 'replay', label: 'Polling', icon: 'fa-tower-broadcast',  accent: '#0984e3', title: 'Poll App Insights via KQL for a past investigation by xcv' },
  { id: 'live',   label: 'New Run', icon: 'fa-satellite-dish', accent: '#28a745', title: 'Kick off a brand-new agent pipeline now (POST /api/run; requires MCP + Azure OpenAI; first event can take 10–30s)' },
];

export function DataSourceToggle(props: DataSourceToggleProps) {
  const { mode, xcv, disabled, onModeChange, onXcvChange, agentFilter, onAgentFilterChange } = props;
  const [replayAvailable, setReplayAvailable] = useState<boolean | null>(null);

  useEffect(() => {
    let alive = true;
    getReplayHealth()
      .then((h) => { if (alive) setReplayAvailable(h.workspace_configured); })
      .catch(() => { if (alive) setReplayAvailable(false); });
    return () => { alive = false; };
  }, []);

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span style={{ fontSize: 10, color: 'var(--cha-text-muted)', fontWeight: 700, letterSpacing: 0.4 }}>
        SOURCE
      </span>
      <div role="radiogroup" aria-label="Event source" style={{ display: 'inline-flex', borderRadius: 6, overflow: 'hidden' }}>
        {OPTIONS.map((opt, i) => {
          const selected = mode === opt.id;
          const isReplay = opt.id === 'replay';
          // Allow button to be clicked, but show warning in tooltip if not available
          const buttonDisabled = Boolean(disabled);
          return (
            <button
              key={opt.id}
              role="radio"
              aria-checked={selected}
              title={
                buttonDisabled ? 'Toggle disabled' 
                : isReplay && replayAvailable === false
                ? 'Replay may not be fully configured — set LOG_ANALYTICS_WORKSPACE_ID and run `az login`. Clicking will attempt connection.'
                : opt.title
              }
              disabled={buttonDisabled}
              onClick={() => onModeChange(opt.id)}
              style={{
                ...BUTTON_BASE,
                borderLeft: i === 0 ? BUTTON_BASE.border : 'none',
                borderRadius: 0,
                background: selected ? opt.accent : BUTTON_BASE.background,
                color: selected ? '#fff' : BUTTON_BASE.color,
                opacity: buttonDisabled ? 0.45 : 1,
                cursor: buttonDisabled ? 'not-allowed' : 'pointer',
              }}
            >
              <i className={`fas ${opt.icon}`} style={{ marginRight: 6 }} />
              {opt.label}
            </button>
          );
        })}
      </div>
      {mode === 'replay' && (
        <input
          type="text"
          placeholder="xcv (e.g. 8b27bf8e-457c-…)"
          value={xcv}
          onChange={(e) => onXcvChange(e.target.value.trim())}
          disabled={disabled}
          spellCheck={false}
          style={{
            fontFamily: 'ui-monospace, monospace',
            fontSize: 11,
            padding: '5px 8px',
            minWidth: 260,
            border: '1px solid var(--cha-border)',
            borderRadius: 6,
          }}
        />
      )}
      {mode === 'replay' && onAgentFilterChange && (
        <>
          <input
            list="cha-agent-filter-suggestions"
            type="text"
            placeholder="agent (blank = all)"
            value={agentFilter ?? ''}
            onChange={(e) => onAgentFilterChange(e.target.value.trim())}
            disabled={disabled}
            spellCheck={false}
            title="Filter replay events by agent name. Leave blank to see everything."
            style={{
              fontFamily: 'ui-monospace, monospace',
              fontSize: 11,
              padding: '5px 8px',
              minWidth: 160,
              border: '1px solid var(--cha-border)',
              borderRadius: 6,
            }}
          />
          <datalist id="cha-agent-filter-suggestions">
            <option value="narrator" />
            <option value="orchestrator" />
            <option value="investigation_orchestrator" />
            <option value="triage_agent" />
            <option value="reasoner" />
            <option value="action_planner" />
            <option value="evidence_planner" />
            <option value="entity_extractor" />
          </datalist>
        </>
      )}
      {mode === 'replay' && replayAvailable === false && (
        <span style={{ fontSize: 10, color: '#b26a00' }}>
          <i className="fas fa-triangle-exclamation" /> Workspace not configured
        </span>
      )}
    </div>
  );
}

/** localStorage helpers so the selection persists across reloads. */
const LS_MODE = 'cha.orchestration.mode';
const LS_XCV = 'cha.orchestration.xcv';
const LS_AGENT = 'cha.orchestration.agentFilter';

export function loadStoredMode(def: OrchestrationMode = 'mock'): OrchestrationMode {
  try {
    const v = localStorage.getItem(LS_MODE);
    if (v === 'mock' || v === 'replay' || v === 'live') return v;
  } catch { /* noop */ }
  return def;
}

export function loadStoredXcv(): string {
  try {
    return localStorage.getItem(LS_XCV) || '';
  } catch {
    return '';
  }
}

export function persistMode(mode: OrchestrationMode): void {
  try { localStorage.setItem(LS_MODE, mode); } catch { /* noop */ }
}

export function persistXcv(xcv: string): void {
  try { localStorage.setItem(LS_XCV, xcv); } catch { /* noop */ }
}

export function loadStoredAgentFilter(): string {
  try {
    return localStorage.getItem(LS_AGENT) || '';
  } catch {
    return '';
  }
}

export function persistAgentFilter(agent: string): void {
  try { localStorage.setItem(LS_AGENT, agent); } catch { /* noop */ }
}
