/**
 * ChaHomePage — landing page for the customer-agent area.
 *
 * Lightweight overview + navigation cards. Intentionally does NOT depend
 * on the legacy `listScenarios()` / `listAgents()` endpoints that 404 on
 * the current backend — those calls used to live here and silently caught
 * their errors, leaving the page mostly empty anyway.
 */
import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { listAgents, type AgentInfo } from '../../api/customerAgentClient';

interface QuickCard {
  to: string;
  icon: string;
  iconColor: string;
  title: string;
  description: string;
}

const QUICK_CARDS: QuickCard[] = [
  {
    to: '/customer-agent/active',
    icon: 'fa-play-circle',
    iconColor: '#fbbf24',
    title: 'Active Investigation',
    description:
      'Trigger a fresh investigation on the CustomerAgent service. Returns one xcv per impacted service.',
  },
  {
    to: '/customer-agent/history',
    icon: 'fa-history',
    iconColor: '#60a5fa',
    title: 'Investigation History',
    description:
      'Browse past investigations from Cosmos. Filter by customer, decision, and confidence.',
  },
  {
    to: '/customer-agent/neural-canvas-live',
    icon: 'fa-circle-nodes',
    iconColor: '#a78bfa',
    title: 'Neural Canvas — Live',
    description:
      'Watch agent topology, hypotheses, and sandbox execution as the agent works through real Log Analytics traces.',
  },
  {
    to: '/customer-agent/agents',
    icon: 'fa-robot',
    iconColor: '#34d399',
    title: 'Agent Registry',
    description: 'Inspect the agents in the Microsoft Agent Framework workflow.',
  },
  {
    to: '/customer-agent/knowledge',
    icon: 'fa-book',
    iconColor: '#f472b6',
    title: 'Knowledge Base',
    description: 'Hypothesis catalog and supporting context the reasoner draws from.',
  },
  {
    to: '/customer-agent/data',
    icon: 'fa-database',
    iconColor: '#22d3ee',
    title: 'Data Files',
    description: 'Reference signal/symptom/evidence data used by the investigation runner.',
  },
];

export default function ChaHomePage(): JSX.Element {
  const [agents, setAgents] = useState<AgentInfo[] | null>(null);
  const navigate = useNavigate();

  useEffect(() => {
    listAgents()
      .then(setAgents)
      .catch(() => setAgents(null));
  }, []);

  return (
    <div style={{ padding: '20px 16px', maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ marginBottom: 24 }}>
        <h2 style={{ margin: 0, fontSize: 22, fontWeight: 700 }}>
          <i
            className="fas fa-brain"
            style={{ color: '#a78bfa', marginRight: 10 }}
          />
          Customer Agent
        </h2>
        <p
          style={{
            margin: '6px 0 0',
            fontSize: 13,
            color: 'var(--cha-text-muted, #9ca3af)',
            maxWidth: 760,
          }}
        >
          A multi-agent investigation pipeline built on the Microsoft Agent
          Framework. Trigger live investigations against the cloud
          CustomerAgent service, browse historical runs from Cosmos, and
          watch reasoning unfold via Log Analytics in the Neural Canvas.
        </p>
        {agents && (
          <div
            style={{
              marginTop: 10,
              fontSize: 12,
              color: 'var(--cha-text-muted, #9ca3af)',
            }}
          >
            <i className="fas fa-circle-check" style={{ color: '#22c55e', marginRight: 4 }} />
            {agents.length} agents registered
          </div>
        )}
      </div>

      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))',
          gap: 14,
        }}
      >
        {QUICK_CARDS.map((card) => (
          <button
            type="button"
            key={card.to}
            onClick={() => navigate(card.to)}
            style={{
              textAlign: 'left',
              background: 'var(--cha-panel-bg, #1f2937)',
              color: 'var(--cha-text, #f3f4f6)',
              border: '1px solid var(--cha-border, #374151)',
              borderRadius: 10,
              padding: 16,
              cursor: 'pointer',
              transition: 'transform 120ms ease, border-color 120ms ease',
              outline: 'none',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-2px)';
              e.currentTarget.style.borderColor = '#6b7280';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)';
              e.currentTarget.style.borderColor = 'var(--cha-border, #374151)';
            }}
          >
            <div
              style={{
                display: 'flex',
                alignItems: 'center',
                marginBottom: 8,
                gap: 10,
              }}
            >
              <i
                className={`fas ${card.icon}`}
                style={{ color: card.iconColor, fontSize: 18 }}
              />
              <span style={{ fontSize: 14, fontWeight: 600 }}>{card.title}</span>
            </div>
            <div
              style={{
                fontSize: 12,
                color: 'var(--cha-text-muted, #9ca3af)',
                lineHeight: 1.45,
              }}
            >
              {card.description}
            </div>
          </button>
        ))}
      </div>
    </div>
  );
}
