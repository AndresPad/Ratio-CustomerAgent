/**
 * PollingStopwatch — small presentational badge shown while Polling mode
 * is active. Displays elapsed time + event count so the audience can see
 * events trickle in as if they were streaming live from Log Analytics.
 *
 * Purely cosmetic: the actual pacing is applied inside
 * `streamReplay()` via the `pollPacingMs` option.
 */
import { useEffect, useState, type CSSProperties } from 'react';

export interface PollingStopwatchProps {
  running: boolean;
  eventCount: number;
  pacingMs: number;
  onPacingChange?: (ms: number) => void;
  style?: CSSProperties;
}

const PRESETS: { label: string; ms: number }[] = [
  { label: 'Fast',   ms: 120 },
  { label: 'Normal', ms: 300 },
  { label: 'Slow',   ms: 700 },
];

function formatElapsed(ms: number): string {
  const totalSeconds = ms / 1000;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds - minutes * 60;
  return `${String(minutes).padStart(2, '0')}:${seconds.toFixed(1).padStart(4, '0')}`;
}

export function PollingStopwatch({
  running,
  eventCount,
  pacingMs,
  onPacingChange,
  style,
}: PollingStopwatchProps) {
  const [elapsedMs, setElapsedMs] = useState(0);

  useEffect(() => {
    if (!running) { setElapsedMs(0); return; }
    const started = Date.now();
    const id = setInterval(() => setElapsedMs(Date.now() - started), 100);
    return () => clearInterval(id);
  }, [running]);

  return (
    <div
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 10,
        padding: '6px 12px',
        borderRadius: 999,
        border: '1px solid #b6ccff',
        background: running ? '#eaf2ff' : '#f5f6fa',
        color: '#1a2a6b',
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: 0.3,
        fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
        ...style,
      }}
      title="Polling App Insights — events are paced client-side to simulate a live stream"
    >
      <i
        className={`fas fa-arrows-rotate${running ? ' fa-spin' : ''}`}
        style={{ color: '#0984e3', fontSize: 12 }}
      />
      <span style={{ textTransform: 'uppercase' }}>Polling</span>
      <span style={{ color: '#4f6bed' }}>{formatElapsed(elapsedMs)}</span>
      <span style={{ color: '#5c6370' }}>·</span>
      <span>{eventCount} events</span>
      {onPacingChange && (
        <>
          <span style={{ color: '#5c6370' }}>·</span>
          <span style={{ color: '#5c6370', fontWeight: 500 }}>pace</span>
          {PRESETS.map((p) => (
            <button
              key={p.ms}
              type="button"
              onClick={() => onPacingChange(p.ms)}
              disabled={running}
              style={{
                padding: '2px 8px',
                fontSize: 10,
                fontWeight: 600,
                borderRadius: 6,
                border: '1px solid',
                borderColor: pacingMs === p.ms ? '#0984e3' : 'transparent',
                background: pacingMs === p.ms ? '#0984e3' : 'transparent',
                color: pacingMs === p.ms ? '#fff' : '#1a2a6b',
                cursor: running ? 'not-allowed' : 'pointer',
                opacity: running ? 0.6 : 1,
                fontFamily: 'inherit',
                letterSpacing: 0.3,
                textTransform: 'uppercase',
              }}
              title={`${p.ms} ms between frames`}
            >
              {p.label}
            </button>
          ))}
        </>
      )}
    </div>
  );
}

/** localStorage helpers for pacing preference. */
const LS_PACING = 'cha.orchestration.pollPacingMs';

export function loadStoredPacing(def: number = 300): number {
  try {
    const v = localStorage.getItem(LS_PACING);
    if (v) {
      const n = Number(v);
      if (Number.isFinite(n) && n >= 0 && n <= 5000) return n;
    }
  } catch { /* noop */ }
  return def;
}

export function persistPacing(ms: number): void {
  try { localStorage.setItem(LS_PACING, String(ms)); } catch { /* noop */ }
}
