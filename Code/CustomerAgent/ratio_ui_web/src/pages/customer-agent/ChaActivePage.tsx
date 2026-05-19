/**
 * ChaActivePage — kick off live investigations and monitor the scheduler.
 *
 * Intentionally contains NO "recently completed" / Cosmos-landing feed.
 * That belongs on the History page (those investigations are, by
 * definition, finished). Active is purely about *doing*:
 *
 *   1. Start a new investigation (StartInvestigationCard).
 *   2. Watch the scheduler tick (SchedulerMonitorPanel).
 *
 * Anything that has already finished and landed in Cosmos is one click
 * away on `/customer-agent/history`.
 */
import { Link } from 'react-router-dom';
import StartInvestigationCard from '../../components/StartInvestigationCard';
import SchedulerMonitorPanel from '../../components/SchedulerMonitorPanel';

export default function ChaActivePage(): JSX.Element {
  return (
    <div style={{ padding: '20px 16px', maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ marginBottom: 12 }}>
        <h2 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>
          Active Investigations
        </h2>
        <p
          style={{
            margin: '4px 0 0',
            fontSize: 13,
            color: 'var(--cha-text-muted, #9ca3af)',
          }}
        >
          Trigger a fresh investigation on the cloud CustomerAgent service,
          or monitor the every-15-min scheduler that does it automatically.
          Looking for finished runs?{' '}
          <Link to="/customer-agent/history">View Investigation History →</Link>
        </p>
      </div>

      <StartInvestigationCard />
      <SchedulerMonitorPanel />
    </div>
  );
}

