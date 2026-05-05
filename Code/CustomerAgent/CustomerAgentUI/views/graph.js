/**
 * Graph View
 *
 * Renders a relationship pipeline graph showing the flow from
 * Signals → Compounds → Decision → Investigation → Agents → Actions.
 *
 * Nodes light up and gain connections as events arrive.
 * Uses inline SVG for rendering.
 */

let _container = null;

/** Node categories and their positions in the SVG */
const NODE_DEFS = [
    { id: 'signals',       label: 'Signals',       x: 80,  y: 60,  w: 100, h: 36, color: 'var(--color-signal)' },
    { id: 'mcp',           label: 'MCP Collect',   x: 80,  y: 130, w: 100, h: 36, color: 'var(--color-mcp)' },
    { id: 'compounds',     label: 'Compounds',     x: 260, y: 60,  w: 100, h: 36, color: 'var(--color-compound)' },
    { id: 'decision',      label: 'Decision',      x: 440, y: 60,  w: 100, h: 36, color: 'var(--color-decision)' },
    { id: 'investigation', label: 'Investigation', x: 620, y: 60,  w: 110, h: 36, color: 'var(--color-agent)' },
    { id: 'triage',        label: 'Triage',        x: 620, y: 130, w: 90,  h: 36, color: 'var(--color-phase)' },
    { id: 'hypotheses',    label: 'Hypotheses',    x: 780, y: 60,  w: 100, h: 36, color: 'var(--color-hypothesis)' },
    { id: 'evidence',      label: 'Evidence',      x: 780, y: 130, w: 90,  h: 36, color: 'var(--color-evidence)' },
    { id: 'actions',       label: 'Actions',       x: 940, y: 60,  w: 90,  h: 36, color: 'var(--color-action)' },
];

/** Edges between nodes (from → to) */
const EDGES = [
    ['signals', 'compounds'],
    ['signals', 'mcp'],
    ['mcp', 'compounds'],
    ['compounds', 'decision'],
    ['decision', 'investigation'],
    ['investigation', 'triage'],
    ['investigation', 'hypotheses'],
    ['triage', 'hypotheses'],
    ['hypotheses', 'evidence'],
    ['evidence', 'hypotheses'],  // feedback loop
    ['hypotheses', 'actions'],
];

/** Track which nodes are active */
const _activeNodes = new Set();

/** Map event types to node IDs */
const EVENT_NODE_MAP = {
    'pipeline_started': null,
    'SignalEvaluationStart': 'signals',
    'SignalTypeEvaluated': 'signals',
    'MCPCollectionCall': 'mcp',
    'CompoundEvaluated': 'compounds',
    'SignalDecision': 'decision',
    'signal_evaluation_complete': 'decision',
    'investigation_started': 'investigation',
    'PhaseTransition': 'investigation',
    'investigation_agent_start': 'investigation',
    'investigation_agent_response': 'investigation',
    'HypothesisScoring': 'hypotheses',
    'HypothesisTransition': 'hypotheses',
    'EvidenceCycle': 'evidence',
    'investigation_complete': 'actions',
    'pipeline_complete': 'actions',
};

/**
 * Initialize the graph view with the SVG layout.
 */
export function initGraphView() {
    _container = document.getElementById('view-graph');
    if (!_container) return;
    _render();
}

/**
 * Process an event and highlight the corresponding node.
 * @param {Object} event
 */
export function addGraphEvent(event) {
    const nodeId = EVENT_NODE_MAP[event.type];
    if (!nodeId || _activeNodes.has(nodeId)) return;
    _activeNodes.add(nodeId);
    _highlightNode(nodeId);
}

/**
 * Reset the graph to its initial state.
 */
export function clearGraph() {
    _activeNodes.clear();
    _render();
}

/* ── Rendering ──────────────────────────────────────────────────────────── */

function _render() {
    if (!_container) return;

    const svgParts = [
        '<svg viewBox="0 0 1080 200" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;min-height:200px">',
        '<defs><marker id="arrowhead" markerWidth="8" markerHeight="6" refX="8" refY="3" orient="auto"><polygon points="0 0, 8 3, 0 6" fill="var(--border)"/></marker></defs>',
    ];

    // Draw edges
    for (const [fromId, toId] of EDGES) {
        const from = NODE_DEFS.find(n => n.id === fromId);
        const to = NODE_DEFS.find(n => n.id === toId);
        if (!from || !to) continue;
        const x1 = from.x + from.w / 2;
        const y1 = from.y + from.h / 2;
        const x2 = to.x + to.w / 2;
        const y2 = to.y + to.h / 2;
        svgParts.push(`<line class="graph-edge" x1="${x1}" y1="${y1}" x2="${x2}" y2="${y2}" />`);
    }

    // Draw nodes
    for (const node of NODE_DEFS) {
        const active = _activeNodes.has(node.id);
        const fill = active ? node.color : 'var(--bg-card)';
        const stroke = active ? node.color : 'var(--border)';
        const textFill = active ? '#fff' : 'var(--text-secondary)';
        svgParts.push(`
            <g class="graph-node" data-id="${node.id}">
                <rect x="${node.x}" y="${node.y}" width="${node.w}" height="${node.h}"
                      fill="${fill}" stroke="${stroke}" rx="6" ry="6" />
                <text x="${node.x + node.w / 2}" y="${node.y + node.h / 2}"
                      fill="${textFill}" font-size="11" text-anchor="middle"
                      dominant-baseline="central">${node.label}</text>
            </g>
        `);
    }

    svgParts.push('</svg>');
    _container.innerHTML = svgParts.join('');
}

function _highlightNode(nodeId) {
    const el = _container?.querySelector(`[data-id="${nodeId}"]`);
    if (!el) return;
    const node = NODE_DEFS.find(n => n.id === nodeId);
    if (!node) return;
    const rect = el.querySelector('rect');
    const text = el.querySelector('text');
    if (rect) {
        rect.setAttribute('fill', node.color);
        rect.setAttribute('stroke', node.color);
    }
    if (text) {
        text.setAttribute('fill', '#fff');
    }
}
