/**
 * Orchestration Graph Component
 *
 * Renders an animated flow diagram of the pipeline orchestration.
 * Active nodes pulse with a green ring; completed nodes dim.
 *
 * Layout:
 *   Row 1:  [Signal] → [Triage] → [Hypothesis]
 *                       |
 *   Loop:          [Ev. Planner]        ← top
 *            [Reasoner]  ↻  [Data Fetcher]
 *              [Code Gen]               ← bottom
 *                       |
 *   Row 3:  [Notifying] → [Complete]
 */

/* ── Node definitions ──────────────────────────────────────────────────── */
const NODES = [
    { id: 'signal',     label: 'Signal',        color: '#60a5fa' },
    { id: 'triage',     label: 'Triage',        color: '#fb923c' },
    { id: 'hypothesis', label: 'Hypothesis',    color: '#34d399' },
    { id: 'inv_orch',   label: 'Orchestrator',  color: '#f472b6', sub: true },
    { id: 'planner',    label: 'Ev. Planner',   color: '#c4b5fd', sub: true },
    { id: 'fetcher',    label: 'Data Fetcher',  color: '#67e8f9', sub: true },
    { id: 'codegen',    label: 'Code Gen',      color: '#818cf8', sub: true },
    { id: 'reasoner',   label: 'Reasoner',      color: '#a78bfa', sub: true },
    { id: 'notifying',  label: 'Notifying',     color: '#22d3ee' },
    { id: 'complete',   label: 'Complete',       color: '#4ade80' },
];

/* ── Event type → node ID mapping ──────────────────────────────────────── */
const EVENT_TO_NODE = {
    'pipeline_started':           'signal',
    'SignalEvaluationStart':      'signal',
    'SignalTypeEvaluated':        'signal',
    'MCPCollectionCall':          'signal',
    'CompoundEvaluated':          'signal',
    'SignalDecision':             'signal',
    'signal_evaluation_complete': 'signal',
    'investigations_starting':    'triage',
    'investigation_started':      'triage',
    'HypothesisScoring':          'hypothesis',
    'HypothesisTransition':       'hypothesis',
    'investigation_complete':     'complete',
    'pipeline_complete':          'complete',
};

/* ── Agent name → node ID mapping ──────────────────────────────────────── */
const AGENT_TO_NODE = {
    'investigation_orchestrator': 'inv_orch',
    'evidence_planner':          'planner',
    'data_fetcher':              'fetcher',
    'sandbox_coder':             'codegen',
    'reasoner':                  'reasoner',
    'triage_agent':              'triage',
    'narrator':                  null,       // ignore narrator
    'action_planner':            'notifying',
};

/* ── Investigation phase name → node ID ────────────────────────────────── */
const PHASE_TO_NODE = {
    'INITIALIZING':   'triage',
    'TRIAGE':         'triage',
    'HYPOTHESIZING':  'hypothesis',
    'PLANNING':       'planner',
    'COLLECTING':     'fetcher',
    'REASONING':      'reasoner',
    'ACTING':         'notifying',
    'NOTIFYING':      'notifying',
    'COMPLETE':       'complete',
};

let _container = null;
let _nodeEls = {};      // nodeId → DOM element
let _activeNode = null;

/**
 * Initialize the orchestration graph into the given container.
 * @param {HTMLElement} container
 */
export function initOrchestration(container) {
    if (!container) return;
    _container = container;
    _nodeEls = {};
    _activeNode = null;
    _render();
}

/**
 * Handle an SSE event — activate the corresponding graph node.
 * @param {Object} event — parsed SSE event
 */
export function handleOrchEvent(event) {
    if (!_container) return;
    const nodeId = _resolveNode(event);
    if (!nodeId) return;
    _activateNode(nodeId);
}

/**
 * Reset all nodes to their initial (inactive) state.
 */
export function resetOrchestration() {
    _activeNode = null;
    for (const el of Object.values(_nodeEls)) {
        el.classList.remove('orch-active', 'orch-done');
    }
}

/* ── Private helpers ────────────────────────────────────────────────────── */

/**
 * Map an SSE event to the orchestration node it activates.
 * Returns null if the event doesn't map to any node.
 */
function _resolveNode(event) {
    const type = event.type;

    // Direct mapping
    if (EVENT_TO_NODE[type]) return EVENT_TO_NODE[type];

    // Agent-name mapping (investigation_agent_response carries agent name)
    if (type === 'investigation_agent_response') {
        const agent = event.agent || '';
        if (AGENT_TO_NODE[agent] !== undefined) return AGENT_TO_NODE[agent];
        // Fallback: try phase
        const phase = (event.phase || '').toUpperCase();
        return PHASE_TO_NODE[phase] || null;
    }

    // PhaseTransition carries to_phase
    if (type === 'PhaseTransition') {
        const to = (event.to_phase || event.ToPhase || '').toUpperCase();
        return PHASE_TO_NODE[to] || null;
    }

    // investigation_tool_call — code-related tools → codegen, else fetcher
    if (type === 'investigation_tool_call') {
        const tool = (event.tool || '').toLowerCase();
        if (tool.includes('code') || tool.includes('execute') || tool.includes('python') || tool.includes('sandbox')) {
            return 'codegen';
        }
        return 'fetcher';
    }

    // ToolCall (AgentLogger) — route by tool name
    if (type === 'ToolCall') {
        const tool = (event.Tool || '').toLowerCase();
        if (tool.includes('sandbox') || tool.includes('execute_python')) {
            return 'codegen';
        }
        if (tool.startsWith('fetch_')) {
            return 'fetcher';
        }
        return null;
    }

    // EvidenceCycle → fetcher (collecting evidence)
    if (type === 'EvidenceCycle') return 'fetcher';

    return null;
}

/**
 * Activate a node: deactivate the previous one (mark done), light up the new one.
 */
function _activateNode(nodeId) {
    if (nodeId === _activeNode) return;

    // Mark previous as done
    if (_activeNode && _nodeEls[_activeNode]) {
        _nodeEls[_activeNode].classList.remove('orch-active');
        _nodeEls[_activeNode].classList.add('orch-done');
    }

    // Activate new
    _activeNode = nodeId;
    const el = _nodeEls[nodeId];
    if (el) {
        el.classList.remove('orch-done');
        el.classList.add('orch-active');
    }
}

/**
 * Render the full orchestration graph DOM.
 */
function _render() {
    const row1 = NODES.filter(n => ['signal', 'triage', 'hypothesis'].includes(n.id));
    const row3 = NODES.filter(n => ['notifying', 'complete'].includes(n.id));

    // Build a quick lookup for satellite nodes
    const nodeMap = {};
    NODES.forEach(n => { nodeMap[n.id] = n; });

    _container.innerHTML = `
        <div class="orch-graph">
            <div class="orch-row">
                ${row1.map((n, i) =>
                    (i > 0 ? '<span class="orch-arrow">→</span>' : '') + _nodeHtml(n)
                ).join('')}
            </div>
            <div class="orch-vline"></div>
            <div class="orch-loop">
                <div class="orch-loop-label">Investigation<br>Loop ↻</div>
                <div class="orch-sat orch-sat-top">${_nodeHtml(nodeMap.inv_orch)}</div>
                <div class="orch-sat orch-sat-topright">${_nodeHtml(nodeMap.planner)}</div>
                <div class="orch-sat orch-sat-botright">${_nodeHtml(nodeMap.fetcher)}</div>
                <div class="orch-sat orch-sat-bottom">${_nodeHtml(nodeMap.codegen)}</div>
                <div class="orch-sat orch-sat-left">${_nodeHtml(nodeMap.reasoner)}</div>
            </div>
            <div class="orch-vline"></div>
            <div class="orch-row">
                ${row3.map((n, i) =>
                    (i > 0 ? '<span class="orch-arrow">→</span>' : '') + _nodeHtml(n)
                ).join('')}
            </div>
        </div>
    `;

    // Cache node elements
    NODES.forEach(n => {
        _nodeEls[n.id] = _container.querySelector(`[data-node="${n.id}"]`);
    });
}

/**
 * Generate HTML for a single graph node.
 */
function _nodeHtml(node) {
    return `<div class="orch-node${node.sub ? ' orch-sub' : ''}" data-node="${node.id}" style="--node-color: ${node.color}">` +
        `<div class="orch-circle"></div>` +
        `<span class="orch-label">${node.label}</span>` +
        `</div>`;
}
