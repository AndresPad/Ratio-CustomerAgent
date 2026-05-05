/**
 * Learning View — Two-Column Layout
 *
 * Pipeline graph → status bar → source/pace controls → scenario card →
 * two-column (Agent Reasoning | Agent Rewards) → result banner.
 *
 * Exports: initLearningView(), addLearningEvent(event), clearLearning()
 */

import { startLearning, stopLearning } from '/lib/learning-sse.js';

/* ── State ──────────────────────────────────────────────────────────────── */
let _container = null;
let _state = 'idle'; // idle | running | complete | error
let _startTime = null;
let _timerInterval = null;
let _pace = 'normal'; // fast | normal | slow
let _source = 'demo'; // demo | live
let _eventCount = 0;

// Phase tracking
let _currentPhase = null;

// Pipeline node counts
let _pipelineCounts = { signal: 0, symptom: 0, hypothesis: 0, evidence: 0, reasoning: 0, learn: 0 };
let _pipelineActive = null; // which node is currently active

// Scenario
let _scenario = {};

// Investigation state
let _stages = [];
let _totalReward = null;
let _perAgentRewards = {};

// Learning state
let _learningRounds = [];
let _totalLearningRounds = 0;
let _scoreDeltas = {};
let _improvedPrompts = {};

// Re-investigation state
let _reinvestigationStages = [];
let _finalImprovements = {};

// Narration log entries: [{color, text}]
let _narrationLog = [];

// Reward items: [{rank, agent, summary, pct, badge, badgeColor}]
let _rewardItems = [];

/* ── Pipeline Config ────────────────────────────────────────────────────── */
const PIPELINE_NODES = [
    { key: 'signal',     label: 'Signal',     color: '#10b981' },
    { key: 'symptom',    label: 'Symptom',    color: '#ef4444' },
    { key: 'hypothesis', label: 'Hypothesis', color: '#f59e0b' },
    { key: 'evidence',   label: 'Evidence',   color: '#6366f1' },
    { key: 'reasoning',  label: 'Reasoning',  color: '#8b5cf6' },
    { key: 'learn',      label: 'Learn',      color: '#0ea5e9' },
];

const STAGE_TO_PIPELINE = { 1: 'signal', 2: 'symptom', 3: 'hypothesis', 4: 'evidence', 5: 'reasoning' };

/* ── Initialization ─────────────────────────────────────────────────────── */

export function initLearningView() {
    _container = document.getElementById('view-learning');
    if (!_container) return;
    _injectStyles();
    _render();
    _bindLifecycle();
}

export function addLearningEvent(event) {
    if (!_container) return;

    switch (event.type) {
        case 'learning_started':       _handleStarted(event); break;
        case 'investigation_stage':    _handleInvestigationStage(event); break;
        case 'agent_reward':           _handleAgentReward(event); break;
        case 'investigation_complete': _handleInvestigationComplete(event); break;
        case 'learning_phase_started': _handleLearningPhaseStarted(event); break;
        case 'learning_round_complete':_handleLearningRoundComplete(event); break;
        case 'learning_complete':      _handleLearningComplete(event); break;
        case 'reinvestigation_stage':  _handleReinvestigationStage(event); break;
        case 'reinvestigation_complete':_handleReinvestigationComplete(event); break;
        case 'learning_narration':     _handleNarration(event); break;
        case 'learning_event_count':   _handleEventCount(event); break;
        case 'learning_error':         _handleError(event); break;
        default:
            console.log('[Learning] Unhandled event:', event.type, event);
    }
}

/**
 * Trigger a learning run programmatically (called by app.js when Run Pipeline
 * is clicked while the Learning tab is active).
 * @param {Object} [params] — optional overrides { customer_name, service_tree_id, start_time, end_time }
 */
export async function startLearningRun(params = {}) {
    await _handleStartClick(params);
}

export function clearLearning() {
    _state = 'idle';
    _currentPhase = null;
    _scenario = {};
    _stages = [];
    _totalReward = null;
    _perAgentRewards = {};
    _learningRounds = [];
    _totalLearningRounds = 0;
    _scoreDeltas = {};
    _improvedPrompts = {};
    _reinvestigationStages = [];
    _finalImprovements = {};
    _narrationLog = [];
    _rewardItems = [];
    _pipelineCounts = { signal: 0, symptom: 0, hypothesis: 0, evidence: 0, reasoning: 0, learn: 0 };
    _pipelineActive = null;
    _eventCount = 0;
    _startTime = null;
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    _render();
}

/* ── Lifecycle Bindings ─────────────────────────────────────────────────── */

function _bindLifecycle() {
    window.addEventListener('learning-connected', () => {
        _state = 'running';
        _updateStatusBar();
    });
    window.addEventListener('learning-done', () => {
        if (_state === 'running') {
            _state = 'complete';
            _updateStatusBar();
        }
    });
    window.addEventListener('learning-error', () => {
        _state = 'error';
        _updateStatusBar();
    });
}

/* ── Event Handlers ─────────────────────────────────────────────────────── */

function _handleStarted(event) {
    _scenario = {
        title: event.scenario || 'Investigation',
        description: event.description || '',
    };
    _currentPhase = 'investigation';
    _state = 'running';
    _startTime = Date.now();
    _timerInterval = setInterval(_updateElapsed, 100);
    _updateStatusBar();
    _updateScenarioCard();
    _updatePipelineGraph();
}

function _handleInvestigationStage(event) {
    _stages.push({
        stage: event.stage,
        agent: event.agent,
        label: event.label,
        output_summary: event.output_summary,
        reward: null,
        breakdown: null,
    });

    const pKey = STAGE_TO_PIPELINE[event.stage];
    if (pKey) {
        _pipelineCounts[pKey]++;
        _pipelineActive = pKey;
    }
    _updatePipelineGraph();
}

function _handleAgentReward(event) {
    const stage = _stages.find(s => s.stage === event.stage && s.agent === event.agent);
    if (stage) {
        stage.reward = event.reward;
        stage.breakdown = event.breakdown;
    }
    _perAgentRewards[event.agent] = event.reward;

    const pct = Math.round(event.reward * 100);
    const { label: badgeLabel, color: badgeColor } = _rewardBadge(pct);
    _rewardItems.push({
        rank: _rewardItems.length + 1,
        agent: event.agent,
        summary: event.output_summary || stage?.output_summary || '',
        pct,
        badge: badgeLabel,
        badgeColor,
    });
    _renderRewardsPanel();
}

function _handleInvestigationComplete(event) {
    _totalReward = event.total_reward;
    _perAgentRewards = event.per_agent_rewards || _perAgentRewards;
    _renderRewardsPanel();
}

function _handleLearningPhaseStarted(event) {
    _currentPhase = 'learning';
    _totalLearningRounds = event.total_rounds || 0;
    _pipelineActive = 'learn';
    _updatePipelineGraph();
    _updateStatusBar();
}

function _handleLearningRoundComplete(event) {
    _learningRounds.push({
        round: event.round,
        agent_scores: event.agent_scores || {},
    });
    _pipelineCounts.learn = _learningRounds.length;
    _updatePipelineGraph();
}

function _handleLearningComplete(event) {
    _scoreDeltas = event.score_deltas || {};
    _improvedPrompts = event.improved_prompts || {};
}

function _handleReinvestigationStage(event) {
    if (_currentPhase !== 'reinvestigation') {
        _currentPhase = 'reinvestigation';
    }
    _reinvestigationStages.push({
        stage: event.stage,
        agent: event.agent,
        label: event.label,
        output_summary: event.output_summary,
        old_score: event.old_score,
        new_score: event.new_score,
    });
}

function _handleReinvestigationComplete(event) {
    _finalImprovements = event.improvements || {};
    _state = 'complete';
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    _updateStatusBar();
    _renderResultBanner();
}

function _handleNarration(event) {
    const color = ['green', 'orange', 'red'].includes(event.color) ? event.color : 'green';
    _narrationLog.push({ color, text: event.text || '' });
    _appendNarrationEntry(color, event.text || '');
}

function _handleEventCount(event) {
    _eventCount = event.count || 0;
    const el = _container?.querySelector('#learning-event-count');
    if (el) el.textContent = `🟢 ${_source.toUpperCase()} · ${_eventCount} events`;
}

function _handleError(event) {
    _state = 'error';
    _updateStatusBar();
    console.error('[Learning] Error:', event.error);

    // Show error in the narration log
    _narrationLog.push({ color: 'red', text: `ERROR: ${event.error || 'Unknown error'}` });
    _appendNarrationEntry('red', `ERROR: ${event.error || 'Unknown error'}`);

    // Also show in scenario card
    const card = _container?.querySelector('#learning-scenario-card');
    if (card) {
        card.innerHTML = `
            <span class="lrn-scenario-icon">❌</span>
            <span class="lrn-scenario-text" style="color:#ef4444">${_esc(event.error || 'Learning failed')}</span>
        `;
    }
}

/* ── Rendering: Full Layout ─────────────────────────────────────────────── */

function _render() {
    if (!_container) return;

    _container.innerHTML = `
        <!-- Pipeline Graph -->
        <div class="lrn-pipeline-graph" id="learning-pipeline-graph"></div>

        <!-- Status Bar -->
        <div class="lrn-status-bar" id="learning-status-bar">
            <div class="lrn-status-left">
                <span class="lrn-status-label">Summary Writer: Learning...</span>
            </div>
            <div class="lrn-status-right">
                <span class="lrn-status-badge-pill badge-idle" id="learning-status-pill">IDLE</span>
                <span class="lrn-elapsed" id="learning-elapsed">—</span>
            </div>
        </div>

        <!-- Source Controls -->
        <div class="lrn-source-controls">
            <div class="lrn-source-toggle">
                <button class="lrn-src-btn${_source === 'demo' ? ' active' : ''}" data-src="demo">DEMO</button>
                <button class="lrn-src-btn${_source === 'live' ? ' active' : ''}" data-src="live">LIVE</button>
            </div>
            <span class="lrn-xcv" id="learning-xcv">xcv-a1b2c3d4-e5f6-7890</span>
            <div class="lrn-action-btns">
                <button class="btn lrn-btn-action" id="btn-rerun">🔄 Re-run</button>
            </div>
        </div>

        <!-- Pace Controls -->
        <div class="lrn-pace-bar">
            <span class="lrn-event-count" id="learning-event-count">🟢 ${_source.toUpperCase()} · ${_eventCount} events</span>
            <div class="lrn-pace-toggle">
                <span class="lrn-pace-label">pace</span>
                <button class="lrn-pace-btn${_pace === 'fast' ? ' active' : ''}" data-pace="fast">FAST</button>
                <button class="lrn-pace-btn${_pace === 'normal' ? ' active' : ''}" data-pace="normal">NORMAL</button>
                <button class="lrn-pace-btn${_pace === 'slow' ? ' active' : ''}" data-pace="slow">SLOW</button>
            </div>
        </div>

        <!-- Scenario Card -->
        <div class="lrn-scenario-card" id="learning-scenario-card">
            <span class="lrn-scenario-icon">⚡</span>
            <span class="lrn-scenario-text">Waiting for scenario...</span>
        </div>

        <!-- Two-Column Main Content -->
        <div class="lrn-two-col">
            <div class="lrn-col-left" id="learning-agent-log">
                <div class="lrn-col-header">Agent Reasoning</div>
            </div>
            <div class="lrn-col-right" id="learning-rewards-panel">
                <div class="lrn-col-header-light">Agent Rewards</div>
            </div>
        </div>

        <!-- Result Banner -->
        <div class="lrn-result-banner" id="learning-result-banner" style="display:none;"></div>
    `;

    _bindControls();
    _updatePipelineGraph();
}

function _bindControls() {
    // Source toggle
    _container.querySelectorAll('.lrn-src-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            _source = btn.dataset.src;
            _container.querySelectorAll('.lrn-src-btn').forEach(b => b.classList.toggle('active', b.dataset.src === _source));
            const el = _container.querySelector('#learning-event-count');
            if (el) el.textContent = `🟢 ${_source.toUpperCase()} · ${_eventCount} events`;
        });
    });

    // Pace toggle
    _container.querySelectorAll('.lrn-pace-btn').forEach(btn => {
        btn.addEventListener('click', () => {
            _pace = btn.dataset.pace;
            _container.querySelectorAll('.lrn-pace-btn').forEach(b => b.classList.toggle('active', b.dataset.pace === _pace));
        });
    });

    // Re-run
    const btnRerun = _container.querySelector('#btn-rerun');
    if (btnRerun) btnRerun.addEventListener('click', () => _handleStartClick());
}

async function _handleStartClick(extraParams = {}) {
    if (_state === 'running') return;
    clearLearning();
    _state = 'running';
    _updateStatusBar();

    try {
        await startLearning({ pace: _pace, mode: _source, ...extraParams });
    } catch (err) {
        console.error('Learning failed:', err);
    }
}

/* ── Pipeline Graph ─────────────────────────────────────────────────────── */

function _updatePipelineGraph() {
    const el = _container?.querySelector('#learning-pipeline-graph');
    if (!el) return;

    const html = PIPELINE_NODES.map((node, i) => {
        const count = _pipelineCounts[node.key];
        const isActive = _pipelineActive === node.key;
        const isDone = count > 0 && !isActive;
        const iconContent = isDone ? '✓' : (isActive ? '●' : '');
        const nodeClass = `lrn-pipe-node${isActive ? ' active' : ''}${isDone ? ' done' : ''}`;

        let edgeHtml = '';
        if (i < PIPELINE_NODES.length - 1) {
            const edgeLabel = count > 0 ? `${count} ${node.label.toLowerCase()}` : '';
            edgeHtml = `
                <div class="lrn-pipe-edge">
                    <span class="lrn-edge-label">${edgeLabel}</span>
                    <div class="lrn-edge-arrow">───→</div>
                </div>
            `;
        }

        return `
            <div class="${nodeClass}">
                <div class="lrn-node-icon" style="background:${node.color}">${iconContent}</div>
                <div class="lrn-node-label">${node.label}</div>
                <div class="lrn-node-count">${count}</div>
            </div>
            ${edgeHtml}
        `;
    }).join('');

    el.innerHTML = html;
}

/* ── Status Bar ─────────────────────────────────────────────────────────── */

function _updateStatusBar() {
    const label = _container?.querySelector('.lrn-status-label');
    const pill = _container?.querySelector('#learning-status-pill');
    if (!label || !pill) return;

    const phaseLabel = _currentPhase === 'learning' ? 'Learning' : _currentPhase === 'reinvestigation' ? 'Re-Investigation' : 'Investigation';

    const map = {
        idle:     { cls: 'badge-idle',     text: 'IDLE' },
        running:  { cls: 'badge-running',  text: 'RUNNING' },
        complete: { cls: 'badge-complete', text: 'COMPLETE' },
        error:    { cls: 'badge-error',    text: 'ERROR' },
    };
    const s = map[_state] || map.idle;
    pill.className = `lrn-status-badge-pill ${s.cls}`;
    pill.textContent = s.text;

    label.textContent = _state === 'running' ? `Summary Writer: ${phaseLabel}...` : _state === 'complete' ? 'Summary Writer: Done' : 'Summary Writer: Ready';
}

function _updateElapsed() {
    if (!_startTime) return;
    const el = _container?.querySelector('#learning-elapsed');
    if (!el) return;
    const elapsed = (Date.now() - _startTime) / 1000;
    const mins = Math.floor(elapsed / 60);
    const secs = Math.floor(elapsed % 60);
    el.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}`;
}

/* ── Scenario Card ──────────────────────────────────────────────────────── */

function _updateScenarioCard() {
    const el = _container?.querySelector('#learning-scenario-card');
    if (!el) return;

    const badge = _state === 'complete' ? '<span class="lrn-scenario-badge resolved">Resolved</span>' : '<span class="lrn-scenario-badge active">Active</span>';
    el.innerHTML = `
        <span class="lrn-scenario-icon">⚡</span>
        <span class="lrn-scenario-text">${_esc(_scenario.title)}${_scenario.description ? ' — ' + _esc(_scenario.description) : ''}</span>
        ${badge}
    `;
}

/* ── Agent Reasoning (left column) ──────────────────────────────────────── */

function _appendNarrationEntry(color, text) {
    const log = _container?.querySelector('#learning-agent-log');
    if (!log) return;

    const entry = document.createElement('div');
    entry.className = 'lrn-narration-entry';
    entry.innerHTML = `<span class="lrn-narration-dot ${color}"></span><span class="lrn-narration-text">${_esc(text)}</span>`;
    log.appendChild(entry);

    // Auto-scroll
    log.scrollTop = log.scrollHeight;
}

/* ── Agent Rewards (right column) ───────────────────────────────────────── */

function _renderRewardsPanel() {
    const panel = _container?.querySelector('#learning-rewards-panel');
    if (!panel) return;

    let html = '<div class="lrn-col-header-light">Agent Rewards</div>';

    html += _rewardItems.map(item => `
        <div class="lrn-reward-item">
            <span class="lrn-reward-rank">#${item.rank}</span>
            <span class="lrn-reward-agent-id">${_esc(item.agent)}</span>
            <span class="lrn-reward-summary">${_esc(item.summary)}</span>
            <span class="lrn-reward-dash">───</span>
            <span class="lrn-reward-pct">${item.pct}%</span>
            <span class="lrn-reward-badge ${item.badgeColor}">${item.badge}</span>
        </div>
    `).join('');

    // Best agent summary
    if (_rewardItems.length > 0) {
        const sorted = [..._rewardItems].sort((a, b) => b.pct - a.pct);
        const best = sorted[0];
        const needImprovement = sorted.filter(r => r.pct < 80).length;
        html += `<div class="lrn-reward-best">● Best agent: ${_esc(best.agent)} at ${best.pct}% — ${needImprovement} need improvement</div>`;
    }

    panel.innerHTML = html;
}

/* ── Result Banner ──────────────────────────────────────────────────────── */

function _renderResultBanner() {
    const el = _container?.querySelector('#learning-result-banner');
    if (!el || Object.keys(_finalImprovements).length === 0) return;

    const agents = Object.entries(_finalImprovements);
    const avgBefore = agents.reduce((s, [, d]) => s + d.before, 0) / agents.length;
    const avgAfter = agents.reduce((s, [, d]) => s + d.after, 0) / agents.length;
    const compositePct = Math.round((avgAfter - avgBefore) * 100);
    const compositePctDisplay = compositePct > 0 ? `+${compositePct}%` : `${compositePct}%`;
    const barWidth = Math.min(100, Math.max(0, Math.round(avgAfter * 100)));

    const agentRows = agents.map(([agent, data]) => {
        const beforePct = Math.round(data.before * 100);
        const afterPct = Math.round(data.after * 100);
        const deltaPct = Math.round(data.delta * 100);
        return `
            <div class="lrn-result-agent-row">
                <span class="lrn-result-agent-name">${_esc(agent)}</span>
                <span class="lrn-result-before">${beforePct}%</span>
                <span class="lrn-result-arrow">→</span>
                <span class="lrn-result-after">${afterPct}%</span>
                <span class="lrn-result-delta">(+${deltaPct}%)</span>
                <div class="lrn-result-minibar"><div class="lrn-result-minibar-fill" style="width:${afterPct}%"></div></div>
            </div>
        `;
    }).join('');

    el.style.display = 'block';
    el.innerHTML = `
        <div class="lrn-result-headline">✅ Prompts Improved</div>
        <div class="lrn-result-desc">All agent prompts were optimized using APO. Re-investigation with improved prompts showed measurable gains.</div>
        <div class="lrn-result-composite">
            <div class="lrn-result-composite-label">COMPOSITE IMPROVEMENT</div>
            <div class="lrn-result-composite-value">${compositePctDisplay}</div>
            <div class="lrn-result-composite-bar"><div class="lrn-result-composite-bar-fill" style="width:${barWidth}%"></div></div>
        </div>
        <div class="lrn-result-agents-header">PER AGENT</div>
        <div class="lrn-result-agents">${agentRows}</div>
        <div class="lrn-result-summary-line">SUMMARY: ${agents.length} agents improved, composite score ${Math.round(avgBefore * 100)}% → ${Math.round(avgAfter * 100)}%</div>
    `;
}

/* ── Helpers ────────────────────────────────────────────────────────────── */

function _esc(str) {
    if (!str) return '';
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function _rewardBadge(pct) {
    if (pct >= 80) return { label: 'STRONG', color: 'green' };
    if (pct >= 60) return { label: 'UNCERTAIN', color: 'orange' };
    return { label: 'WEAK', color: 'red' };
}

/* ── Injected Styles ────────────────────────────────────────────────────── */

function _injectStyles() {
    if (document.getElementById('lrn-styles')) return;
    const style = document.createElement('style');
    style.id = 'lrn-styles';
    style.textContent = `
/* ── Pipeline Graph ─────────────────────────────────────────────────── */
.lrn-pipeline-graph {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0;
    padding: 14px 12px;
    margin-bottom: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow-x: auto;
}
.lrn-pipe-node {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    min-width: 60px;
}
.lrn-node-icon {
    width: 32px; height: 32px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    color: #fff;
    font-size: 14px; font-weight: 700;
    opacity: 0.4;
    transition: opacity 0.3s, box-shadow 0.3s;
}
.lrn-pipe-node.active .lrn-node-icon {
    opacity: 1;
    box-shadow: 0 0 0 4px rgba(255,255,255,0.2), 0 0 12px rgba(0,0,0,0.15);
    animation: lrnPulse 1.5s ease-in-out infinite;
}
.lrn-pipe-node.done .lrn-node-icon { opacity: 1; }
.lrn-node-label {
    font-size: 10px; font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.lrn-node-count {
    font-family: var(--font-mono);
    font-size: 11px; font-weight: 700;
    color: var(--text-primary);
}
.lrn-pipe-edge {
    display: flex;
    flex-direction: column;
    align-items: center;
    margin: 0 4px;
    min-width: 50px;
}
.lrn-edge-label {
    font-size: 9px;
    color: var(--text-muted);
    white-space: nowrap;
    margin-bottom: 2px;
}
.lrn-edge-arrow {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--border);
    letter-spacing: -1px;
}
@keyframes lrnPulse {
    0%, 100% { box-shadow: 0 0 0 4px rgba(255,255,255,0.2), 0 0 12px rgba(0,0,0,0.1); }
    50% { box-shadow: 0 0 0 6px rgba(255,255,255,0.3), 0 0 18px rgba(0,0,0,0.2); }
}

/* ── Status Bar ─────────────────────────────────────────────────────── */
.lrn-status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 14px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 8px;
}
.lrn-status-left { display: flex; align-items: center; gap: 8px; }
.lrn-status-label {
    font-size: 12px; font-weight: 600;
    color: var(--text-primary);
}
.lrn-status-right { display: flex; align-items: center; gap: 10px; }
.lrn-status-badge-pill {
    font-size: 10px; font-weight: 700;
    padding: 2px 10px;
    border-radius: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.lrn-status-badge-pill.badge-idle     { background: #e5e7eb; color: #6b7280; }
.lrn-status-badge-pill.badge-running  { background: #dbeafe; color: #2563eb; }
.lrn-status-badge-pill.badge-complete { background: #d1fae5; color: #047857; }
.lrn-status-badge-pill.badge-error    { background: #fee2e2; color: #dc2626; }
.lrn-elapsed {
    font-family: var(--font-mono); font-size: 12px;
    color: var(--text-secondary);
}

/* ── Source Controls ────────────────────────────────────────────────── */
.lrn-source-controls {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 14px;
    margin-bottom: 6px;
    gap: 12px;
}
.lrn-source-toggle {
    display: flex;
    border: 1px solid var(--border);
    border-radius: 6px;
    overflow: hidden;
}
.lrn-src-btn {
    padding: 4px 14px;
    font-size: 11px; font-weight: 700;
    border: none; cursor: pointer;
    background: var(--bg-secondary);
    color: var(--text-secondary);
    transition: all 0.15s;
}
.lrn-src-btn.active {
    background: #0d9488; color: #fff;
}
.lrn-src-btn:hover:not(.active) { background: var(--bg-hover); }
.lrn-xcv {
    font-family: var(--font-mono); font-size: 11px;
    color: var(--text-muted);
    flex: 1;
    text-align: center;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.lrn-action-btns { display: flex; gap: 6px; }
.lrn-btn-start {
    padding: 6px 18px;
    font-size: 12px; font-weight: 700;
    border: none;
    border-radius: 6px;
    background: #00897b;
    color: #fff;
    cursor: pointer;
    white-space: nowrap;
}
.lrn-btn-start:hover:not(:disabled) { background: #00796b; }
.lrn-btn-start:disabled { opacity: 0.5; cursor: not-allowed; }
.lrn-btn-action {
    padding: 4px 12px;
    font-size: 11px; font-weight: 600;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-card);
    color: var(--text-primary);
    cursor: pointer;
}
.lrn-btn-action:hover { background: var(--bg-hover); }

/* ── Pace Controls ──────────────────────────────────────────────────── */
.lrn-pace-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 6px 14px;
    margin-bottom: 8px;
}
.lrn-event-count {
    font-size: 11px; font-weight: 600;
    color: var(--text-secondary);
}
.lrn-pace-toggle {
    display: flex;
    align-items: center;
    gap: 6px;
}
.lrn-pace-label {
    font-size: 10px; font-weight: 600;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.lrn-pace-btn {
    padding: 3px 10px;
    font-size: 10px; font-weight: 700;
    border: 1px solid var(--border);
    border-radius: 4px;
    background: var(--bg-secondary);
    color: var(--text-secondary);
    cursor: pointer;
    transition: all 0.15s;
}
.lrn-pace-btn.active {
    background: #0d9488; color: #fff; border-color: #0d9488;
}
.lrn-pace-btn:hover:not(.active) { background: var(--bg-hover); }

/* ── Scenario Card ──────────────────────────────────────────────────── */
.lrn-scenario-card {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 3px solid #f59e0b;
    border-radius: 8px;
    margin-bottom: 10px;
}
.lrn-scenario-icon { font-size: 16px; }
.lrn-scenario-text {
    flex: 1;
    font-size: 13px; font-weight: 600;
    color: var(--text-primary);
}
.lrn-scenario-badge {
    font-size: 10px; font-weight: 700;
    padding: 2px 10px;
    border-radius: 10px;
    text-transform: uppercase;
}
.lrn-scenario-badge.resolved { background: #d1fae5; color: #047857; }
.lrn-scenario-badge.active   { background: #dbeafe; color: #2563eb; }

/* ── Two-Column Layout ──────────────────────────────────────────────── */
.lrn-two-col {
    display: flex;
    gap: 10px;
    margin-bottom: 10px;
    min-height: 320px;
    max-height: calc(100vh - 420px);
}
.lrn-col-left {
    flex: 0 0 45%;
    background: #1a2138;
    border-radius: 8px;
    padding: 10px 14px;
    overflow-y: auto;
    font-family: var(--font-mono);
    font-size: 11px;
    color: #c8d0e0;
    line-height: 1.7;
}
.lrn-col-right {
    flex: 1;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 14px;
    overflow-y: auto;
}
.lrn-col-header {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #64748b;
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
}
.lrn-col-header-light {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-secondary);
    margin-bottom: 10px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
}

/* ── Narration Entries ──────────────────────────────────────────────── */
.lrn-narration-entry {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-bottom: 8px;
    animation: lrnFadeIn 0.2s ease-out;
}
.lrn-narration-dot {
    width: 8px; height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
    margin-top: 5px;
}
.lrn-narration-dot.green  { background: #10b981; }
.lrn-narration-dot.orange { background: #f59e0b; }
.lrn-narration-dot.red    { background: #ef4444; }
.lrn-narration-text {
    color: #c8d0e0;
    word-break: break-word;
}
@keyframes lrnFadeIn {
    from { opacity: 0; transform: translateY(4px); }
    to   { opacity: 1; transform: translateY(0); }
}

/* ── Reward Items ───────────────────────────────────────────────────── */
.lrn-reward-item {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 6px 0;
    border-bottom: 1px dashed var(--border-light);
    font-size: 12px;
    animation: lrnFadeIn 0.2s ease-out;
}
.lrn-reward-rank {
    font-family: var(--font-mono); font-weight: 700;
    color: var(--text-muted);
    min-width: 24px;
}
.lrn-reward-agent-id {
    font-weight: 700;
    color: var(--text-primary);
    min-width: 120px;
    white-space: nowrap;
}
.lrn-reward-summary {
    flex: 1;
    color: var(--text-secondary);
    font-size: 11px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
}
.lrn-reward-dash {
    color: var(--border);
    font-family: var(--font-mono);
    font-size: 10px;
    letter-spacing: -1px;
}
.lrn-reward-pct {
    font-family: var(--font-mono); font-weight: 700;
    color: var(--text-primary);
    min-width: 36px;
    text-align: right;
}
.lrn-reward-badge {
    font-size: 9px; font-weight: 700;
    padding: 1px 8px;
    border-radius: 8px;
    text-transform: uppercase;
}
.lrn-reward-badge.green  { background: #d1fae5; color: #047857; }
.lrn-reward-badge.orange { background: #fef3c7; color: #92400e; }
.lrn-reward-badge.red    { background: #fee2e2; color: #dc2626; }
.lrn-reward-best {
    margin-top: 10px;
    padding: 8px 10px;
    font-size: 11px; font-weight: 600;
    color: #047857;
    background: #ecfdf5;
    border-radius: 6px;
    border: 1px solid #a7f3d0;
}

/* ── Result Banner ──────────────────────────────────────────────────── */
.lrn-result-banner {
    background: linear-gradient(135deg, #047857 0%, #059669 100%);
    border-radius: 10px;
    padding: 16px 20px;
    color: #fff;
    animation: lrnFadeIn 0.4s ease-out;
}
.lrn-result-headline {
    font-size: 16px; font-weight: 700;
    margin-bottom: 6px;
}
.lrn-result-desc {
    font-size: 12px;
    opacity: 0.9;
    margin-bottom: 14px;
    line-height: 1.5;
}
.lrn-result-composite {
    margin-bottom: 14px;
}
.lrn-result-composite-label {
    font-size: 10px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    opacity: 0.8;
    margin-bottom: 4px;
}
.lrn-result-composite-value {
    font-size: 28px; font-weight: 800;
    font-family: var(--font-mono);
    margin-bottom: 6px;
}
.lrn-result-composite-bar {
    height: 6px;
    background: rgba(255,255,255,0.2);
    border-radius: 3px;
    overflow: hidden;
}
.lrn-result-composite-bar-fill {
    height: 100%;
    background: #fff;
    border-radius: 3px;
    transition: width 0.5s ease-out;
}
.lrn-result-agents-header {
    font-size: 10px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    opacity: 0.8;
    margin-bottom: 6px;
}
.lrn-result-agents {
    display: flex;
    flex-direction: column;
    gap: 4px;
    margin-bottom: 12px;
}
.lrn-result-agent-row {
    display: flex;
    align-items: center;
    gap: 8px;
    font-size: 12px;
}
.lrn-result-agent-name {
    min-width: 130px; font-weight: 600;
}
.lrn-result-before {
    font-family: var(--font-mono);
    opacity: 0.7;
    min-width: 36px; text-align: right;
}
.lrn-result-arrow { opacity: 0.5; }
.lrn-result-after {
    font-family: var(--font-mono); font-weight: 700;
    min-width: 36px; text-align: right;
}
.lrn-result-delta {
    font-family: var(--font-mono);
    font-size: 11px;
    opacity: 0.8;
    min-width: 50px;
}
.lrn-result-minibar {
    flex: 1;
    height: 4px;
    background: rgba(255,255,255,0.15);
    border-radius: 2px;
    overflow: hidden;
}
.lrn-result-minibar-fill {
    height: 100%;
    background: rgba(255,255,255,0.7);
    border-radius: 2px;
}
.lrn-result-summary-line {
    font-family: var(--font-mono);
    font-size: 11px;
    padding: 8px 10px;
    background: rgba(255,255,255,0.12);
    border-radius: 6px;
    line-height: 1.5;
}
    `;
    document.head.appendChild(style);
}
