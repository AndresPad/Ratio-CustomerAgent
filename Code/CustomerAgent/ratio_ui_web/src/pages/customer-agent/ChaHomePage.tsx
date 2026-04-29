import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listScenarios, listAgents, type Scenario } from '../../api/customerAgentClient';

export default function ChaHomePage() {
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [agentCount, setAgentCount] = useState(0);
  const navigate = useNavigate();

  useEffect(() => {
    listScenarios().then(setScenarios).catch(() => {});
    listAgents().then(a => setAgentCount(a.length)).catch(() => {});
  }, []);

  const now = new Date();
  const hour = now.getHours();
  const greeting = hour < 12 ? 'Good morning' : hour < 17 ? 'Good afternoon' : 'Good evening';
  const days = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
  const months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

  // Build the time portion explicitly (12-hour clock with AM/PM) so the
  // string is guaranteed non-empty regardless of how the runtime resolves
  // toLocaleTimeString options. A previous version using
  //   now.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' })
  // could render blank in some environments.
  const rawHour = now.getHours();
  const ampm = rawHour >= 12 ? 'PM' : 'AM';
  const displayHour = rawHour % 12 === 0 ? 12 : rawHour % 12;
  const minutes = String(now.getMinutes()).padStart(2, '0');
  const timeStr = `${displayHour}:${minutes} ${ampm}`;

  const dateStr = `${days[now.getDay()]}, ${months[now.getMonth()]} ${now.getDate()}, ${now.getFullYear()} \u00b7 ${timeStr}`;

  const featured = scenarios.slice(0, 3);

  return (
    <>
      {/* Greeting */}
      <div className="cha-greeting">
        <div>
          <h2>{greeting}</h2>
          <p className="cha-greeting-date">{dateStr}</p>
        </div>
        <button className="cha-btn-primary" onClick={() => navigate('/customer-agent/scenarios')}>
          <i className="fas fa-play" /> Quick Start — Run Scenario
        </button>
      </div>

      {/* Status badges */}
      <div style={{ display: 'flex', gap: 10, padding: '12px 0 16px', flexWrap: 'wrap' }}>
        <span className="cha-badge cha-badge-info">{scenarios.length} scenarios available</span>
        <span className="cha-badge cha-badge-watching">{agentCount} agents configured</span>
        <span className="cha-badge cha-badge-handled">Simulation mode</span>
      </div>

      {/* Architecture overview */}
      <div className="cha-insights">
        <div className="cha-insight">
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            <strong>Agent Architecture: </strong>
            Multi-agent GroupChat orchestration using Microsoft Agent Framework. Abductive reasoning loop: Signal → Symptom → Hypothesis → Evidence → Reasoning → Action → Verification.
          </div>
          <div className="cha-actions">
            <button className="cha-insight-link" onClick={() => navigate('/customer-agent/scenarios')}>Browse scenarios ›</button>
            <span className="cha-status-tag watching"><i className="fas fa-robot" style={{ marginRight: 4 }} /> GPT-4o + o1 (Reasoner)</span>
          </div>
        </div>
        <div className="cha-insight">
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            <strong>Agents: </strong>
            Orchestrator · Triage · Hypothesis Selector · Evidence Planner · Telemetry Agent · Outage Agent · Support Agent · Advisor Agent · Resource Agent · Reasoner (o1) · Action Planner · Notification Agent
          </div>
          <div className="cha-actions">
            <span className="cha-status-tag success"><i className="fas fa-check" style={{ marginRight: 4 }} /> All agents loaded from YAML config</span>
          </div>
        </div>
        <div className="cha-insight">
          <div style={{ fontSize: 13, lineHeight: 1.6 }}>
            <strong>Reasoning Loop: </strong>
            Three checkpoints drive iterative deepening. Checkpoint 1: new questions after evidence? Checkpoint 2: all hypotheses resolved? Checkpoint 3: verification passed? Loop-backs expand the investigation when new information emerges.
          </div>
        </div>
      </div>

      {/* Featured Scenarios */}
      <div style={{ padding: '8px 0 24px' }}>
        <div className="cha-section-header">
          <div className="cha-section-title"><span className="cha-live-dot" /> FEATURED SCENARIOS</div>
          <button className="cha-insight-link" onClick={() => navigate('/customer-agent/scenarios')}>View all scenarios</button>
        </div>
        <div className="cha-card-grid">
          {featured.map(s => (
            <div key={s.id} className="cha-card" onClick={() => navigate('/customer-agent/scenarios')}>
              <div className="cha-card-header">
                <div className="cha-card-title">
                  <span className="cha-card-dot" style={{ background: '#f0ad4e' }} />
                  {s.name}
                </div>
              </div>
              <div className="cha-card-body">
                <div style={{ marginBottom: 8 }}>
                  <span className="cha-metric singular">{s.category}</span>
                </div>
                <div className="cha-card-desc">{s.description.slice(0, 120)}…</div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </>
  );
}
