import { useState, useEffect } from 'react';
import { NavLink, Outlet, useNavigate, useLocation } from 'react-router-dom';
import { getLiveHealth } from '../../api/liveOrchestrationClient';
import './cha-theme.css';

const PAGE_TITLES: Record<string, string> = {
  '': 'Home',
  'scenarios': 'Simulation Scenarios',
  'live': 'Live Agent Orchestration',
  'active': 'Active Investigation',
  'theatre': 'Investigation Theatre',
  'investigation-flow': 'Investigation Reasoning Flow',
  'history': 'History',
  'agents': 'Agent Registry',
  'config': 'Configuration',
  'data': 'Data Files',
  'knowledge': 'Knowledge Base',
};

const NAV = [
  { section: 'INVESTIGATIONS' },
  { to: 'live', icon: 'fa-satellite-dish', label: 'Live Orchestration' },
  { to: 'scenarios', icon: 'fa-flask', label: 'Simulation Scenarios' },
  { to: 'active', icon: 'fa-play-circle', label: 'Active Investigation' },
  { to: 'theatre', icon: 'fa-theater-masks', label: 'Investigation Theatre' },
  { to: 'investigation-flow', icon: 'fa-project-diagram', label: 'Reasoning Flow' },
  { to: 'history', icon: 'fa-history', label: 'History' },
  { section: 'AGENT FRAMEWORK' },
  { to: 'agents', icon: 'fa-robot', label: 'Agent Registry' },
  { to: 'config', icon: 'fa-sliders-h', label: 'Configuration' },
  { section: 'DATA' },
  { to: 'data', icon: 'fa-database', label: 'Data Files' },
  { to: 'knowledge', icon: 'fa-book', label: 'Knowledge Base' },
] as const;

export default function ChaLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const [connected, setConnected] = useState<boolean | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const segment = location.pathname.replace('/customer-agent', '').replace(/^\//, '');
  const pageTitle = PAGE_TITLES[segment] || 'Home';

  useEffect(() => {
    getLiveHealth()
      .then(h => setConnected(h.status === 'healthy' || h.status === 'ok'))
      .catch(() => setConnected(false));
  }, []);

  return (
    <div className="cha-root">
      {/* Icon Strip */}
      <div className="cha-icon-strip">
        <button className="cha-strip-icon" title="Toggle Sidebar" onClick={() => setCollapsed(c => !c)}>
          <i className="fas fa-bars" />
        </button>
        <button className="cha-strip-icon" title="Home" onClick={() => navigate('/customer-agent')}>
          <i className="fas fa-home" />
        </button>
        <button className="cha-strip-icon" title="Scenarios" onClick={() => navigate('/customer-agent/scenarios')}>
          <i className="fas fa-flask" />
        </button>
        <button className="cha-strip-icon" title="Back to Ratio AI" onClick={() => navigate('/')}>
          <i className="fas fa-arrow-left" />
        </button>
        <div className="cha-strip-spacer" />
        <button className="cha-strip-icon" title="Settings"><i className="fas fa-cog" /></button>
      </div>

      {/* Sidebar */}
      <aside className={`cha-sidebar${collapsed ? ' collapsed' : ''}`}>
        <div className="cha-sidebar-header" style={{ display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
          <img
            src="/RATIO-W.svg"
            alt="Ratio AI"
            style={{ height: 28, width: 'auto', cursor: 'pointer' }}
            onClick={() => navigate('/')}
            title="Back to Ratio AI"
          />
        </div>
        <nav style={{ padding: '8px 0', flex: 1 }}>
          {/* Customer Agent home link */}
          <NavLink
            to="/customer-agent"
            end
            className={({ isActive }) => `cha-nav-item${isActive ? ' active' : ''}`}
            style={{ whiteSpace: 'nowrap' }}
          >
            <i className="fas fa-brain" />
            <span style={{ fontSize: 13 }}>Customer Agent</span>
            <span style={{ marginLeft: 'auto', fontSize: 8, background: 'rgba(0,0,0,0.35)', color: '#b388ff', padding: '1px 5px', borderRadius: 8, fontWeight: 500, flexShrink: 0 }}>preview</span>
          </NavLink>
          {NAV.map((item, i) => {
            if ('section' in item) {
              return <div key={i} className="cha-nav-section">{item.section}</div>;
            }
            return (
              <NavLink
                key={item.to}
                to={`/customer-agent/${item.to}`}
                className={({ isActive }) => `cha-nav-item${isActive ? ' active' : ''}`}
              >
                <i className={`fas ${item.icon}`} />
                <span>{item.label}</span>
              </NavLink>
            );
          })}
        </nav>
      </aside>

      {/* Main Content */}
      <main className="cha-main">
        <div className="cha-top-bar">
          <h1 className="cha-page-title">{pageTitle}</h1>
          <div className="cha-top-actions">
            <div className="cha-conn-status">
              <span className={`cha-status-dot ${connected ? 'green' : 'red'}`} />
              <span>{connected === null ? 'Connecting…' : connected ? 'Connected' : 'Disconnected'}</span>
            </div>
            <div className="cha-user-profile">
              <div className="cha-user-avatar">CH</div>
              <div className="cha-user-info">
                <span className="cha-user-name">Customer Agent</span>
                <span className="cha-user-role">Microsoft Agent Framework</span>
              </div>
            </div>
          </div>
        </div>
        <Outlet />
      </main>
    </div>
  );
}
