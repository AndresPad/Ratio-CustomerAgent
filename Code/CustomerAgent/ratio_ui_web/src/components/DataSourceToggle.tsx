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
  { id: 'mock',   label: 'Mock',   icon: 'fa-flask',          accent: '#7c3aed', title: 'Scripted demo fixture (no network)' },
  { id: 'replay', label: 'Replay', icon: 'fa-backward',       accent: '#0984e3', title: 'Replay a past run from App Insights by xcv' },
  { id: 'live',   label: 'Live',   icon: 'fa-satellite-dish', accent: '#28a745', title: 'Run the real agent pipeline now' },
];

export function DataSourceToggle(props: DataSourceToggleProps) {
  const { mode, xcv, disabled, onModeChange, onXcvChange } = props;
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
          const disabledOpt = Boolean(disabled) || (isReplay && replayAvailable === false);
          return (
            <button
              key={opt.id}
              role="radio"
              aria-checked={selected}
              title={disabledOpt && isReplay
                ? 'Replay unavailable — set LOG_ANALYTICS_WORKSPACE_ID and run `az login`.'
                : opt.title}
              disabled={disabledOpt}
              onClick={() => onModeChange(opt.id)}
              style={{
                ...BUTTON_BASE,
                borderLeft: i === 0 ? BUTTON_BASE.border : 'none',
                borderRadius: 0,
                background: selected ? opt.accent : BUTTON_BASE.background,
                color: selected ? '#fff' : BUTTON_BASE.color,
                opacity: disabledOpt ? 0.45 : 1,
                cursor: disabledOpt ? 'not-allowed' : 'pointer',
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
          disabled={disabled || replayAvailable === false}
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
