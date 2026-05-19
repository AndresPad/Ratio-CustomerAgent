import InvestigationHistoryTable from '../../components/InvestigationHistoryTable';
import LiveInvestigationsPanel from '../../components/LiveInvestigationsPanel';

/**
 * ChaHistoryPage — past investigations.
 *
 * Two panels, both Cosmos-backed (`customeragentdb / customer_agent`):
 *  - LiveInvestigationsPanel: real-time feed of new investigations as
 *    they land via Cosmos change-feed SSE (last 24h).
 *  - InvestigationHistoryTable: paged browse with filters (customer,
 *    decision, min confidence, lookback).
 */
export default function ChaHistoryPage() {
  return (
    <div style={{ padding: '20px 16px', maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>
          Investigation History
        </h2>
        <p
          style={{
            margin: '4px 0 0',
            fontSize: 13,
            color: 'var(--cha-text-muted, #9ca3af)',
          }}
        >
          Past investigations from Cosmos. The live panel updates in
          real time as new runs finish; the table below lets you filter
          and browse older runs.
        </p>
      </div>

      <LiveInvestigationsPanel />
      <InvestigationHistoryTable defaultLookbackDays={5} />
    </div>
  );
}
