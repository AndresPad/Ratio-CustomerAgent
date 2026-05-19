/**
 * StartInvestigationCard — primary CTA on the Active page.
 *
 * Renders a prominent card that explains what "start an investigation"
 * does and surfaces the same modal `LiveInvestigationsPanel` used to
 * embed. This is intentionally a *page-level* component now — the
 * Active page is about kicking off live runs; finished/landed results
 * belong on Investigation History.
 */
import { useState } from 'react';
import StartInvestigationModal from './StartInvestigationModal';

export default function StartInvestigationCard(): JSX.Element {
  const [open, setOpen] = useState(false);

  return (
    <div
      style={{
        background:
          'linear-gradient(135deg, rgba(251,191,36,0.10), rgba(99,102,241,0.10))',
        border: '1px solid var(--cha-border, #374151)',
        borderRadius: 10,
        padding: 18,
        marginBottom: 16,
        display: 'flex',
        alignItems: 'center',
        gap: 18,
        flexWrap: 'wrap',
      }}
    >
      <div
        style={{
          width: 48,
          height: 48,
          borderRadius: 12,
          background: '#fbbf24',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          flexShrink: 0,
        }}
      >
        <i
          className="fas fa-play"
          style={{ fontSize: 20, color: '#1f2937' }}
        />
      </div>
      <div style={{ flex: 1, minWidth: 240 }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 700 }}>
          Start a new investigation
        </h3>
        <p
          style={{
            margin: '4px 0 0',
            fontSize: 12,
            color: 'var(--cha-text-muted, #9ca3af)',
            lineHeight: 1.45,
          }}
        >
          Kick off the CustomerAgent pipeline for a customer + time window.
          Returns one xcv per impacted service. Use <strong>Tail</strong> or{' '}
          <strong>Canvas</strong> on the response to watch the agent stream
          its reasoning from Log Analytics in real time.
        </p>
      </div>
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="btn btn-primary"
        style={{ padding: '10px 22px', fontSize: 14, fontWeight: 600 }}
      >
        <i className="fas fa-play" /> Start Investigation
      </button>

      <StartInvestigationModal open={open} onClose={() => setOpen(false)} />
    </div>
  );
}
