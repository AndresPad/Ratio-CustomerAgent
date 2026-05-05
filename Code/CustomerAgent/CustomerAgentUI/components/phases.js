/**
 * Phase Progress Bar Component
 *
 * Renders a 12-step progress bar mapping to the CustomerAgent pipeline stages.
 * Each phase lights up as events arrive, providing at-a-glance pipeline status.
 */

/** The 13 pipeline phases in execution order */
const PHASES = [
    { id: 'initializing',    label: 'Init',         color: 'var(--phase-init)' },
    { id: 'signal_eval',     label: 'Signal Eval',  color: 'var(--phase-signal-eval)' },
    { id: 'mcp_collect',     label: 'MCP Collect',  color: 'var(--phase-mcp-collect)' },
    { id: 'compound_eval',   label: 'Compounds',    color: 'var(--phase-compound)' },
    { id: 'decision',        label: 'Decision',      color: 'var(--phase-decision)' },
    { id: 'triage',          label: 'Triage',        color: 'var(--phase-triage)' },
    { id: 'hypothesizing',   label: 'Hypothesize',  color: 'var(--phase-hypothesizing)' },
    { id: 'planning',        label: 'Planning',      color: 'var(--phase-planning)' },
    { id: 'collecting',      label: 'Collecting',    color: 'var(--phase-collecting)' },
    { id: 'reasoning',       label: 'Reasoning',    color: 'var(--phase-reasoning)' },
    { id: 'acting',          label: 'Acting',        color: 'var(--phase-acting)' },
    { id: 'notifying',       label: 'Notifying',    color: 'var(--phase-notifying, var(--phase-acting))' },
    { id: 'complete',        label: 'Complete',      color: 'var(--phase-complete)' },
];

/**
 * Map AgentLogger event types to phase IDs.
 * When an event of a given type arrives, the corresponding phase is activated.
 */
const EVENT_TO_PHASE = {
    'pipeline_started':              'initializing',
    'SignalEvaluationStart':         'signal_eval',
    'SignalTypeEvaluated':           'signal_eval',
    'MCPCollectionCall':             'mcp_collect',
    'CompoundEvaluated':             'compound_eval',
    'SignalDecision':                'decision',
    'signal_evaluation_complete':    'decision',
    'investigation_started':         'triage',
    'PhaseTransition':               null,  // handled dynamically
    'HypothesisScoring':             'hypothesizing',
    'EvidenceCycle':                  'collecting',
    'HypothesisTransition':          'reasoning',
    'investigation_complete':        'complete',
    'pipeline_complete':             'complete',
};

/** Map investigation phase names to our phase IDs */
const INV_PHASE_MAP = {
    'INITIALIZING':   'triage',
    'TRIAGE':         'triage',
    'HYPOTHESIZING':  'hypothesizing',
    'PLANNING':       'planning',
    'COLLECTING':     'collecting',
    'REASONING':      'reasoning',
    'ACTING':         'acting',
    'NOTIFYING':      'notifying',
    'COMPLETE':       'complete',
};

let _activePhaseIdx = -1;
let _phaseEls = [];

/**
 * Initialize the phase bar by rendering all 12 steps into the container.
 */
export function initPhaseBar() {
    const container = document.getElementById('phase-bar');
    if (!container) return;
    container.innerHTML = '';
    _phaseEls = [];
    _activePhaseIdx = -1;

    PHASES.forEach((phase, idx) => {
        const el = document.createElement('div');
        el.className = 'phase-step';
        el.textContent = phase.label;
        el.dataset.phaseId = phase.id;
        el.dataset.idx = idx;
        container.appendChild(el);
        _phaseEls.push(el);
    });
}

/**
 * Update the phase bar based on an incoming event.
 * @param {Object} event — the parsed SSE event
 */
export function updatePhase(event) {
    const type = event.type;
    let targetId = null;

    // PhaseTransition events carry the phase name directly
    if (type === 'PhaseTransition') {
        const toPhase = event.to_phase || event.ToPhase || '';
        targetId = INV_PHASE_MAP[toPhase.toUpperCase()] || null;
    } else if (type === 'investigation_agent_response') {
        // Use the phase from investigation events
        const phase = (event.phase || '').toUpperCase();
        targetId = INV_PHASE_MAP[phase] || null;
    } else {
        targetId = EVENT_TO_PHASE[type] || null;
    }

    if (!targetId) return;

    const targetIdx = PHASES.findIndex(p => p.id === targetId);
    if (targetIdx < 0) return;

    // Allow backtracking (e.g. complete → acting for post-GroupChat action planning)
    if (targetIdx < _activePhaseIdx) {
        // Reset phases after the new target back to inactive
        for (let i = targetIdx + 1; i <= _activePhaseIdx; i++) {
            const el = _phaseEls[i];
            el.classList.remove('done', 'active');
            el.style.background = '';
            el.style.color = '';
        }
    }
    if (targetIdx === _activePhaseIdx) return;

    // Mark all phases up to target as done, target as active
    for (let i = 0; i <= targetIdx; i++) {
        const el = _phaseEls[i];
        if (i < targetIdx) {
            el.classList.add('done');
            el.classList.remove('active');
            el.style.background = PHASES[i].color;
            el.style.color = '#fff';
        } else {
            el.classList.add('active');
            el.classList.remove('done');
            el.style.background = PHASES[i].color;
            el.style.color = '#fff';
        }
    }
    _activePhaseIdx = targetIdx;
}

/**
 * Reset the phase bar to its initial state.
 */
export function resetPhaseBar() {
    _activePhaseIdx = -1;
    _phaseEls.forEach(el => {
        el.classList.remove('active', 'done');
        el.style.background = '';
        el.style.color = '';
    });
}
