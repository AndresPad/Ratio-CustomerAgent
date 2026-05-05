/**
 * Timeline / Gantt View
 *
 * Renders a Gantt-style chart with horizontal bars for each agent/phase.
 * Bars grow in real time as events arrive, showing duration and overlap.
 *
 * Rows:
 *   - Pipeline (overall)
 *   - Signal Builder
 *   - Each investigation agent (triage, evidence_planner, reasoner, etc.)
 *   - Each investigation phase (TRIAGE, HYPOTHESIZING, etc.)
 */

let _container = null;

/** Global timeline start (epoch ms) */
let _timelineStart = null;

/** Scale: pixels per second (configurable) */
const PX_PER_SEC = 20;

/** Timeline width grows dynamically */
let _timelineWidth = 800;

/**
 * Row state: each row has { label, bars: [{ start, end?, color, label }] }
 * Stored in insertion order.
 */
const _rows = new Map();

/**
 * Initialize the timeline view container.
 */
export function initTimelineView() {
    _container = document.getElementById('view-timeline');
    if (!_container) return;
    _container.innerHTML = '<div class="gantt-container" id="gantt-root"></div>';
}

/**
 * Process an event and update/create Gantt bars.
 * @param {Object} event
 */
export function addTimelineEvent(event) {
    if (!_container) return;

    const now = event.timestamp ? event.timestamp * 1000 : Date.now();
    if (!_timelineStart) _timelineStart = now;

    const t = event.type;

    // Pipeline bar
    if (t === 'pipeline_started') {
        _startBar('Pipeline', now, 'var(--color-agent)', 'Pipeline');
    } else if (t === 'pipeline_complete' || t === 'pipeline_error') {
        _endBar('Pipeline', now);
    }

    // Signal Builder bar
    if (t === 'SignalEvaluationStart') {
        _startBar('Signal Builder', now, 'var(--color-signal)', 'Eval');
    } else if (t === 'signal_evaluation_complete' || t === 'SignalDecision') {
        _endBar('Signal Builder', now);
    }

    // MCP collection bars
    if (t === 'MCPCollectionCall') {
        const tool = event.Tool || 'mcp';
        _startBar('MCP', now, 'var(--color-mcp)', tool);
        // MCP calls are instant; close immediately
        _endBar('MCP', now + 100);
    }

    // Investigation bar
    if (t === 'investigation_started') {
        _startBar('Investigation', now, 'var(--color-agent)', event.investigation_id?.substring(0, 8) || 'inv');
    } else if (t === 'investigation_complete' || t === 'investigation_error') {
        _endBar('Investigation', now);
    }

    // Phase bars
    if (t === 'PhaseTransition') {
        const fromPhase = event.from_phase || event.FromPhase || '';
        const toPhase = event.to_phase || event.ToPhase || '';
        if (fromPhase) _endBar(`Phase: ${fromPhase}`, now);
        if (toPhase) _startBar(`Phase: ${toPhase}`, now, _phaseColor(toPhase), toPhase);
    }

    // Agent bars
    if (t === 'investigation_agent_start') {
        const agent = event.agent || 'agent';
        _startBar(`Agent: ${agent}`, now, 'var(--color-agent)', agent);
    } else if (t === 'investigation_agent_response') {
        const agent = event.agent || 'agent';
        _endBar(`Agent: ${agent}`, now);
    }

    // Tool call bars (instant)
    if (t === 'ToolCall') {
        const tool = event.Tool || 'tool';
        const duration = event.DurationMs || 100;
        _startBar(`Tool: ${tool}`, now - duration, 'var(--color-tool)', tool);
        _endBar(`Tool: ${tool}`, now);
    }

    _render();
}

/**
 * Clear the timeline.
 */
export function clearTimeline() {
    _rows.clear();
    _timelineStart = null;
    _timelineWidth = 800;
    const root = document.getElementById('gantt-root');
    if (root) root.innerHTML = '';
}

/* ── Bar Management ─────────────────────────────────────────────────────── */

function _startBar(rowKey, startMs, color, label) {
    if (!_rows.has(rowKey)) {
        _rows.set(rowKey, { label: rowKey, bars: [] });
    }
    const row = _rows.get(rowKey);
    // Close any open bar first
    const openBar = row.bars.find(b => !b.end);
    if (openBar) openBar.end = startMs;
    // Start new bar
    row.bars.push({ start: startMs, end: null, color, label });
}

function _endBar(rowKey, endMs) {
    const row = _rows.get(rowKey);
    if (!row) return;
    const openBar = row.bars.find(b => !b.end);
    if (openBar) openBar.end = endMs;
}

/* ── Rendering ──────────────────────────────────────────────────────────── */

function _render() {
    const root = document.getElementById('gantt-root');
    if (!root) return;

    const now = Date.now();
    const elapsed = (now - (_timelineStart || now)) / 1000;
    _timelineWidth = Math.max(800, elapsed * PX_PER_SEC + 200);

    let html = '';

    // Header with time ticks
    html += '<div class="gantt-header">';
    html += '<div class="gantt-header-label">Component</div>';
    html += `<div class="gantt-header-timeline" style="width:${_timelineWidth}px">`;
    const tickInterval = elapsed > 120 ? 30 : elapsed > 60 ? 15 : elapsed > 20 ? 5 : 2;
    for (let t = 0; t <= elapsed + tickInterval; t += tickInterval) {
        const x = t * PX_PER_SEC;
        html += `<span class="gantt-tick" style="left:${x}px">${_fmtSec(t)}</span>`;
    }
    html += '</div></div>';

    // Rows
    for (const [key, row] of _rows) {
        html += '<div class="gantt-row">';
        html += `<div class="gantt-row-label">${_esc(row.label)}</div>`;
        html += `<div class="gantt-row-bars" style="width:${_timelineWidth}px">`;

        for (const bar of row.bars) {
            const startSec = (bar.start - _timelineStart) / 1000;
            const endSec = bar.end ? (bar.end - _timelineStart) / 1000 : elapsed;
            const left = startSec * PX_PER_SEC;
            const width = Math.max(4, (endSec - startSec) * PX_PER_SEC);

            html += `<div class="gantt-bar" style="left:${left}px;width:${width}px;background:${bar.color}" title="${_esc(bar.label)}: ${(endSec - startSec).toFixed(1)}s">`;
            if (width > 30) {
                html += `<span class="gantt-bar-label">${_esc(bar.label)}</span>`;
            }
            html += '</div>';
        }

        html += '</div></div>';
    }

    root.innerHTML = html;
}

/* ── Helpers ─────────────────────────────────────────────────────────────── */

function _phaseColor(phase) {
    const map = {
        'TRIAGE': 'var(--phase-triage)',
        'HYPOTHESIZING': 'var(--phase-hypothesizing)',
        'PLANNING': 'var(--phase-planning)',
        'COLLECTING': 'var(--phase-collecting)',
        'REASONING': 'var(--phase-reasoning)',
        'ACTING': 'var(--phase-acting)',
        'NOTIFYING': 'var(--phase-notifying)',
        'COMPLETE': 'var(--phase-complete)',
    };
    return map[phase?.toUpperCase()] || 'var(--border)';
}

function _fmtSec(sec) {
    if (sec < 60) return `${sec}s`;
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}m${s > 0 ? s + 's' : ''}`;
}

function _esc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}
