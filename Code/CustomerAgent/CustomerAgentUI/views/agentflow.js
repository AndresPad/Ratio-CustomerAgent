/**
 * Agent Flow View (Horizontal Swimlane)
 *
 * Shows each agent as a horizontal swim lane.  Events appear as
 * colored chips in the lane of the agent that produced them,
 * ordered left-to-right by arrival time.
 */

let _container = null;

/** Known agents — lanes are created on-the-fly as agents appear */
const _lanes = new Map();  // agentName → { el, events[] }

/** Pipeline-level lane for non-agent events */
const PIPELINE_LANE = '__pipeline__';

/**
 * Initialize the agent flow view.
 */
export function initAgentFlowView() {
    _container = document.getElementById('view-agentflow');
    if (!_container) return;
    _container.innerHTML = '<div class="swimlane-container" id="swimlane-root"></div>';
}

/**
 * Add an event chip to the appropriate agent lane.
 * @param {Object} event
 */
export function addAgentFlowEvent(event) {
    if (!_container) return;

    const agentName = _extractAgent(event);
    if (!agentName) return;  // Skip events without a clear agent

    // Create lane if needed
    if (!_lanes.has(agentName)) {
        _createLane(agentName);
    }

    const lane = _lanes.get(agentName);
    const chip = _createChip(event);
    lane.track.appendChild(chip);
    lane.events.push(event);

    // Scroll track to show latest
    lane.track.scrollLeft = lane.track.scrollWidth;
}

/**
 * Clear all lanes.
 */
export function clearAgentFlow() {
    _lanes.clear();
    const root = document.getElementById('swimlane-root');
    if (root) root.innerHTML = '';
}

/* ── Private ────────────────────────────────────────────────────────────── */

function _extractAgent(event) {
    const t = event.type;

    // Pipeline-level events
    if (['pipeline_started', 'pipeline_complete', 'pipeline_error',
         'signal_evaluation_complete', 'investigations_starting'].includes(t)) {
        return PIPELINE_LANE;
    }

    // Signal evaluation events → "Signal Builder" pseudo-lane
    if (['SignalEvaluationStart', 'SignalTypeEvaluated', 'MCPCollectionCall',
         'CompoundEvaluated', 'SignalDecision', 'SymptomTemplatesLoaded'].includes(t)) {
        return 'Signal Builder';
    }

    // Agent-specific events
    if (event.agent) return event.agent;
    if (event.Agent) return event.Agent;

    // Investigation events
    if (t === 'investigation_started' || t === 'investigation_complete' ||
        t === 'investigation_error') {
        return 'Orchestrator';
    }

    // Phase transitions
    if (t === 'PhaseTransition') return event.agent_name || event.Agent || 'Orchestrator';

    // Speaker selection
    if (t === 'SpeakerSelected') return event.speaker || event.Agent || 'Orchestrator';

    return null;
}

function _createLane(name) {
    const root = document.getElementById('swimlane-root');
    if (!root) return;

    const lane = document.createElement('div');
    lane.className = 'swimlane';

    const label = document.createElement('div');
    label.className = 'swimlane-label';
    label.textContent = name === PIPELINE_LANE ? 'Pipeline' : name;

    const track = document.createElement('div');
    track.className = 'swimlane-track';

    lane.appendChild(label);
    lane.appendChild(track);
    root.appendChild(lane);

    _lanes.set(name, { el: lane, track, events: [] });
}

function _createChip(event) {
    const chip = document.createElement('div');
    chip.className = 'swimlane-event';

    const color = _chipColor(event.type);
    chip.style.background = color;
    chip.style.color = '#fff';
    chip.textContent = _chipLabel(event);
    const tooltipLimit = event.type === 'ContextFolding' ? 5000 : 300;
    chip.title = `${event.type}\n${JSON.stringify(event, null, 2).substring(0, tooltipLimit)}`;

    return chip;
}

function _chipColor(type) {
    if (type.includes('Signal') || type === 'SignalEvaluationStart') return 'var(--color-signal)';
    if (type.includes('MCP')) return 'var(--color-mcp)';
    if (type.includes('Compound')) return 'var(--color-compound)';
    if (type.includes('Decision') || type === 'signal_evaluation_complete') return 'var(--color-decision)';
    if (type.includes('Phase')) return 'var(--color-phase)';
    if (type.includes('Hypothesis')) return 'var(--color-hypothesis)';
    if (type.includes('Evidence')) return 'var(--color-evidence)';
    if (type.includes('Tool')) return 'var(--color-tool)';
    if (type.includes('agent') || type.includes('Agent')) return 'var(--color-agent)';
    if (type.includes('error') || type.includes('Error')) return 'var(--color-error)';
    if (type.includes('complete') || type.includes('Complete')) return 'var(--color-complete)';
    return 'var(--border)';
}

function _chipLabel(event) {
    const t = event.type;
    // Short labels for common events
    if (t === 'SignalTypeEvaluated') return event.SignalType || 'Signal';
    if (t === 'MCPCollectionCall') return event.Tool || 'MCP';
    if (t === 'CompoundEvaluated') return event.CompoundName || 'Compound';
    if (t === 'SignalDecision') return event.Action || 'Decision';
    if (t === 'PhaseTransition') return event.to_phase || event.ToPhase || 'Phase';
    if (t === 'investigation_agent_start') return 'Start';
    if (t === 'investigation_agent_response') return event.phase || 'Response';
    if (t === 'HypothesisScoring') return 'Score';
    if (t === 'EvidenceCycle') return `Cycle ${event.Cycle || ''}`;
    if (t === 'ToolCall') return event.Tool || 'Tool';
    if (t === 'LLMCall') return 'LLM';
    if (t === 'pipeline_started') return 'Start';
    if (t === 'pipeline_complete') return 'Done';
    if (t === 'investigation_started') return 'Start';
    if (t === 'investigation_complete') return 'Done';

    // Fallback: first word of type
    return t.split(/[_A-Z]/).filter(Boolean)[0] || t.substring(0, 8);
}
