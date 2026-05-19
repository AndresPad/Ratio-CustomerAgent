/**
 * Fixture data for offline UI dev. Activated when VITE_USE_FIXTURES=true in
 * .env.local — see investigationsClient.ts.
 */
import type { Investigation } from '../types/investigations';

export const mockInvestigations: Investigation[] = [
  {
    id: '8db16085-3d88-42ca-9f81-248bd55bdc0d',
    xcv: '8db16085-3d88-42ca-9f81-248bd55bdc0d',
    investigation_id: '9ff36f31',
    customer_name: 'BlackRock, Inc',
    service_tree_id: 'db348eb2-16db-44b3-b867-f60f7cfb87d4',
    service_name: 'SQL Connectivity',
    timestamp: '2026-05-13T16:46:51.792958+00:00',
    phase: 'complete',
    counts: {
      symptoms: 4,
      hypotheses: 1,
      evidence: 4,
      activated_signals: 2,
      activated_compounds: 0,
    },
    hypotheses: [
      {
        id: 'HYP-SUP-003',
        title:
          "Customer 'BlackRock, Inc' raised a support request. No SLI degradation detected for this customer, but other customers are reporting issues.",
        status: 'resolved_as_contributing',
        confidence: 0.65,
        root_cause: null,
      },
    ],
    _ts: 1778690818,
  },
  {
    id: 'fixture-active-001',
    xcv: 'fixture-active-001',
    investigation_id: 'inv-active-001',
    customer_name: 'Contoso Capital',
    service_tree_id: 'f1d1800e-d38e-41f2-b63c-72d59ecaf9c0',
    service_name: 'Azure Kubernetes Service',
    timestamp: new Date(Date.now() - 5 * 60 * 1000).toISOString(),
    phase: 'reasoning',
    counts: {
      symptoms: 2,
      hypotheses: 0,
      evidence: 1,
      activated_signals: 1,
      activated_compounds: 0,
    },
    hypotheses: [],
    _ts: Math.floor(Date.now() / 1000) - 300,
  },
  {
    id: 'fixture-active-002',
    xcv: 'fixture-active-002',
    investigation_id: 'inv-active-002',
    customer_name: 'Fabrikam Bank',
    service_tree_id: 'db348eb2-16db-44b3-b867-f60f7cfb87d4',
    service_name: 'SQL Connectivity',
    timestamp: new Date(Date.now() - 60 * 1000).toISOString(),
    phase: 'signal_building',
    counts: {
      symptoms: 0,
      hypotheses: 0,
      evidence: 0,
      activated_signals: 0,
      activated_compounds: 0,
    },
    hypotheses: [],
    _ts: Math.floor(Date.now() / 1000) - 60,
  },
];
