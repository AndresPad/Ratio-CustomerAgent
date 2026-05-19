/**
 * CustomerAgentUI — Main Application
 *
 * Orchestrates all components: SSE client, phase bar, sidebar,
 * context panels, and the four views (stream, graph, agentflow, timeline).
 *
 * Event flow:
 *   SSE stream → window 'agent-event' → dispatch to:
 *     - Phase bar (updatePhase)
 *     - Active view (addEvent)
 *     - Context panels (updatePanels)
 *     - Stats counter
 */

import { startPipeline, stopPipeline } from '/lib/sse.js';
import { initPhaseBar, updatePhase, resetPhaseBar } from '/components/phases.js';
import { initSidebar, updateStat, resetStats } from '/components/sidebar.js';
import { updatePanels, resetPanels, getPanelCounts } from '/components/panels.js';
import { renderCard } from '/components/cards.js?v=2';
import { initStreamView, addStreamEvent, clearStream, addStreamNarration, updateStreamOrchestration, hideStreamNarrationThinking } from '/views/stream.js';
import { initGraphView, addGraphEvent, clearGraph } from '/views/graph.js';
import { initAgentFlowView, addAgentFlowEvent, clearAgentFlow } from '/views/agentflow.js';
import { initTimelineView, addTimelineEvent, clearTimeline } from '/views/timeline.js';
import { initLearningView, addLearningEvent, clearLearning, startLearningRun } from '/views/learning.js';
import { initSandboxView, addSandboxEvent, clearSandbox } from '/views/sandbox.js';
import { initConfigView } from '/views/config.js';

/* ── State ──────────────────────────────────────────────────────────────── */
let _running = false;
let _eventCount = 0;
let _startTime = null;
let _timerInterval = null;let _backendXcv = null;  // XCV actually used by the backend for App Insights
let _allEvents = [];           // all SSE events (unfiltered)
let _activeServiceFilter = '__all__';  // current service_tree_id filter
let _serviceMap = {};          // service_tree_id → { service_name, service_xcv }
let _xcvToServiceMap = {};     // xcv → service_tree_id (reverse lookup for filtering)
let _investigationServiceMap = {};  // investigation_id → service_tree_id (for narrator filtering)
/* ── DOM refs ───────────────────────────────────────────────────────────── */
const btnRun = document.getElementById('btn-run');
const btnStop = document.getElementById('btn-stop');
const statusBadge = document.getElementById('status-badge');
const elapsedEl = document.getElementById('elapsed-time');
const xcvDisplay = document.getElementById('xcv-display');
const customerInput = document.getElementById('customer-input');
const serviceInput = document.getElementById('service-input');
const startTimeInput = document.getElementById('start-time-input');
const endTimeInput = document.getElementById('end-time-input');
const serviceFilterBar = document.getElementById('service-filter-bar');
const serviceFilterSelect = document.getElementById('service-filter');
const serviceXcvDisplay = document.getElementById('service-xcv-display');

/* ── Initialization ─────────────────────────────────────────────────────── */
document.addEventListener('DOMContentLoaded', () => {
    initPhaseBar();
    initSidebar();
    initStreamView();
    initGraphView();
    initTimelineView();
    initAgentFlowView();
    initLearningView();
    initSandboxView();
    initConfigView();
    _bindControls();
    _bindSSEEvents();
    _bindServiceFilter();
    _setDefaultDatetimes();
});

/* ── Control Bindings ───────────────────────────────────────────────────── */

function _bindControls() {
    btnRun.addEventListener('click', _handleRun);
    btnStop.addEventListener('click', _handleStop);
}

/** Pre-fill Start/End time: now−4h → now, formatted for datetime-local inputs */
function _setDefaultDatetimes() {
    const now = new Date();
    const fourHoursAgo = new Date(now.getTime() - 4 * 60 * 60 * 1000);
    // datetime-local needs "YYYY-MM-DDTHH:MM:SS" (no timezone suffix)
    const fmt = (d) => {
        const pad = (n, len = 2) => String(n).padStart(len, '0');
        return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
    };
    startTimeInput.value = fmt(fourHoursAgo);
    endTimeInput.value = fmt(now);
}

async function _handleRun() {
    if (_running) return;

    // Check if Learning tab is active — route to learning flow instead
    const activeTab = document.querySelector('.view-tab.active');
    if (activeTab && activeTab.dataset.view === 'learning') {
        const params = {};
        if (customerInput.value.trim()) params.customer_name = customerInput.value.trim();
        if (serviceInput.value.trim()) params.service_tree_id = serviceInput.value.trim();
        if (startTimeInput.value) params.start_time = new Date(startTimeInput.value).toISOString();
        if (endTimeInput.value) params.end_time = new Date(endTimeInput.value).toISOString();
        startLearningRun(params);
        return;
    }

    _setRunning(true);
    _resetAll();

    const params = {};
    if (customerInput.value.trim()) params.customer_name = customerInput.value.trim();
    if (serviceInput.value.trim()) params.service_tree_id = serviceInput.value.trim();
    // datetime-local values have no timezone suffix; append 'Z' so JS parses as UTC
    if (startTimeInput.value) params.start_time = new Date(startTimeInput.value + 'Z').toISOString();
    if (endTimeInput.value) params.end_time = new Date(endTimeInput.value + 'Z').toISOString();

    try {
        await startPipeline(params);
    } catch (err) {
        console.error('Pipeline failed:', err);
    }
    // SSE lifecycle events will handle state transitions
}

function _handleStop() {
    stopPipeline();
    _setRunning(false);
    _setStatus('idle', 'Stopped');
}

/* ── SSE Event Handling ─────────────────────────────────────────────────── */

function _bindSSEEvents() {
    // Stream connected
    window.addEventListener('sse-connected', () => {
        _setStatus('running', 'Running');
    });

    // Stream ended normally
    window.addEventListener('sse-done', () => {
        _setRunning(false);
        _setStatus('complete', 'Complete');
        hideStreamNarrationThinking();
    });

    // Stream error
    window.addEventListener('sse-error', (e) => {
        _setRunning(false);
        _setStatus('error', 'Error');
        hideStreamNarrationThinking();
        console.error('SSE error:', e.detail?.error);
    });

    // Individual pipeline events
    window.addEventListener('agent-event', (e) => {
        const event = e.detail;

        // Skip startup-only events that aren't part of the pipeline
        if (event.type === 'PromptLoaded') return;

        // Sandbox events go to the sandbox view
        if (event.type?.startsWith('sandbox_')) {
            console.log('[app.js] Routing sandbox event:', event.type, event);
            addSandboxEvent(event);
            addStreamEvent(event);
            return;
        }

        // Learning-only events go exclusively to the learning view
        if (event.type?.startsWith('learning_') || event.type?.startsWith('agent_reward') || event.type?.startsWith('reinvestigation_')) {
            addLearningEvent(event);
            return;
        }

        // investigation_* events are dual-routed: learning view gets a copy,
        // but they also flow through to the main pipeline (panels, stats, views).
        if (event.type?.startsWith('investigation_')) {
            addLearningEvent(event);
            // fall through — do NOT return
        }
        // Store main pipeline events for replay on filter change
        _allEvents.push(event); 

        _eventCount++;
        updateStat('events', _eventCount);

        // Capture the backend XCV (the one App Insights uses)
        // source_xcv comes from AgentLogger._emit(); prefer it over xcv
        const backendXcv = event.source_xcv || event.xcv;
        if (backendXcv && !_backendXcv) {
            _backendXcv = backendXcv;
            _showXcv(backendXcv);
        }

        // Track investigation → service mapping for narrator filtering
        if (event.type === 'investigation_started' && event.investigation_id) {
            const sid = event.service_tree_id || event.ServiceTreeId || '';
            if (sid) _investigationServiceMap[event.investigation_id] = sid;
        }

        // Incrementally add services to the filter as signal eval starts
        if (event.type === 'SignalEvaluationStart') {
            _addServiceToFilter(event);
        }
        // Update service metadata (xcv, counts) when signal eval completes
        if (event.type === 'signal_evaluation_complete' && Array.isArray(event.results)) {
            _updateServiceMetadata(event.results);
        }

        // Narrator streaming events go to stream view narrator panel (filtered by service)
        if (event.type === 'investigation_narrator_chunk' || event.type === 'investigation_narrator_done' || event.type === 'investigation_milestone') {
            if (_matchesNarratorFilter(event)) addStreamNarration(event);
            // Phase bar & orchestration still need service filtering below
        }

        // Apply service filter — skip events that don't match
        if (!_matchesServiceFilter(event)) {
            // Log blocked hypothesis/investigation events during live stream
            if (event.type === 'investigation_agent_response' || event.type === 'investigation_complete' ||
                event.type === 'HypothesisTransition' || event.type === 'HypothesisScoring') {
                console.warn('[live] BLOCKED by filter:', event.type,
                    'sid=', event.service_tree_id || event.ServiceTreeId || '(none)',
                    'filter=', _activeServiceFilter);
            }
            return;
        }

        // Update phase bar and orchestration graph (after service filter)
        updatePhase(event);
        updateStreamOrchestration(event);

        // Narrator events already handled above — skip remaining view updates
        if (event.type === 'investigation_narrator_chunk' || event.type === 'investigation_narrator_done' || event.type === 'investigation_milestone') {
            return;
        }

        // Update all views (each view decides if the event is relevant)
        addStreamEvent(event);
        addGraphEvent(event);
        addAgentFlowEvent(event);
        addTimelineEvent(event);

        // Update context panels
        updatePanels(event);

        // Update stats from panel counts
        const counts = getPanelCounts();
        updateStat('signals', counts.signals);
        updateStat('compounds', counts.signals); // compounds are in signals array
        updateStat('symptoms', counts.symptoms);
        updateStat('hypotheses', counts.hypotheses);
        updateStat('evidence', counts.evidence);
        updateStat('actions', counts.actions);
    });
}

/* ── UI State Helpers ───────────────────────────────────────────────────── */

function _setRunning(running) {
    _running = running;
    btnRun.disabled = running;
    btnStop.disabled = !running;

    if (running) {
        _startTime = Date.now();
        _timerInterval = setInterval(_updateElapsed, 100);
    } else {
        if (_timerInterval) clearInterval(_timerInterval);
        _timerInterval = null;
    }
}

function _setStatus(state, text) {
    statusBadge.className = `badge badge-${state}`;
    statusBadge.textContent = text;
}

function _showXcv(xcv) {
    if (!xcvDisplay) return;
    const short = xcv.length > 12 ? xcv.substring(0, 8) + '...' : xcv;
    xcvDisplay.textContent = `XCV: ${short}`;
    xcvDisplay.title = `${xcv} (click to copy)`;
    xcvDisplay.onclick = () => {
        navigator.clipboard.writeText(xcv).then(() => {
            xcvDisplay.textContent = 'Copied!';
            setTimeout(() => { xcvDisplay.textContent = `XCV: ${short}`; }, 1200);
        });
    };
}

function _updateElapsed() {
    if (!_startTime) return;
    const elapsed = (Date.now() - _startTime) / 1000;
    const mins = Math.floor(elapsed / 60);
    const secs = Math.floor(elapsed % 60);
    const ms = Math.floor((elapsed % 1) * 10);
    elapsedEl.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${ms}`;
}

function _resetAll() {
    _eventCount = 0;
    _startTime = null;
    _backendXcv = null;
    _allEvents = [];
    _activeServiceFilter = '__all__';
    _serviceMap = {};
    _xcvToServiceMap = {};
    _investigationServiceMap = {};
    elapsedEl.textContent = '—';
    if (xcvDisplay) xcvDisplay.textContent = '';
    if (serviceFilterBar) serviceFilterBar.style.display = 'none';
    if (serviceFilterSelect) {
        serviceFilterSelect.innerHTML = '<option value="__all__">All Services</option>';
    }
    if (serviceXcvDisplay) serviceXcvDisplay.textContent = '';
    resetPhaseBar();
    resetStats();
    resetPanels();
    clearStream();
    clearGraph();
    clearAgentFlow();
    clearTimeline();
    clearLearning();
    clearSandbox();
}

/* ── Service Filter ─────────────────────────────────────────────────────── */

function _bindServiceFilter() {
    if (!serviceFilterSelect) return;
    serviceFilterSelect.addEventListener('change', () => {
        _activeServiceFilter = serviceFilterSelect.value;
        _updateServiceXcvDisplay();
        _replayFilteredEvents();
    });
}

/**
 * Incrementally add a service to the filter dropdown when
 * SignalEvaluationStart fires (one per service_tree_id).
 */
function _addServiceToFilter(event) {
    if (!serviceFilterSelect || !serviceFilterBar) return;
    const sid = event.ServiceTreeId || event.service_tree_id || '';
    if (!sid) return;

    const name = event.ServiceName || event.service_name || sid;
    const xcv = event.source_xcv || event.xcv || '';
    console.debug('[service-filter] _addServiceToFilter:', sid, name, 'xcv=', xcv);

    // Already known — just update XCV if it was missing
    if (_serviceMap[sid]) {
        if (!_serviceMap[sid].service_xcv && xcv) {
            _serviceMap[sid].service_xcv = xcv;
            _xcvToServiceMap[xcv] = sid;
        }
        return;
    }

    _serviceMap[sid] = { service_name: name, service_xcv: xcv };
    if (xcv) _xcvToServiceMap[xcv] = sid;

    // Show the bar once we have 2+ services
    if (Object.keys(_serviceMap).length >= 2) {
        // Rebuild select to ensure all options are present
        serviceFilterSelect.innerHTML = '<option value="__all__">All Services</option>';
        for (const [id, info] of Object.entries(_serviceMap)) {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = info.service_name || id;
            serviceFilterSelect.appendChild(opt);
        }
        serviceFilterBar.style.display = '';
    }
}

/**
 * Update service metadata (XCV, counts) from signal_evaluation_complete.
 * Acts as a fallback / enrichment pass after all evals finish.
 */
function _updateServiceMetadata(results) {
    if (!serviceFilterSelect || !serviceFilterBar) return;
    let added = false;
    results.forEach(r => {
        const sid = r.service_tree_id;
        if (!sid) return;
        if (!_serviceMap[sid]) {
            _serviceMap[sid] = {
                service_name: r.service_name || sid,
                service_xcv: r.service_xcv || '',
            };
            if (r.service_xcv) _xcvToServiceMap[r.service_xcv] = sid;
            added = true;
        } else {
            // Enrich with definitive xcv from results
            if (r.service_xcv) {
                _serviceMap[sid].service_xcv = r.service_xcv;
                _xcvToServiceMap[r.service_xcv] = sid;
            }
            if (r.service_name) _serviceMap[sid].service_name = r.service_name;
        }
    });

    // Rebuild dropdown if new services were added here
    if (added && Object.keys(_serviceMap).length >= 2) {
        const current = serviceFilterSelect.value;
        serviceFilterSelect.innerHTML = '<option value="__all__">All Services</option>';
        for (const [id, info] of Object.entries(_serviceMap)) {
            const opt = document.createElement('option');
            opt.value = id;
            opt.textContent = info.service_name || id;
            serviceFilterSelect.appendChild(opt);
        }
        serviceFilterSelect.value = current;
        serviceFilterBar.style.display = '';
    }

    // Update XCV display if a service is currently selected
    _updateServiceXcvDisplay();
}

/**
 * Check whether a narrator event matches the active service filter.
 * Uses investigation_id → service_tree_id mapping.
 */
function _matchesNarratorFilter(event) {
    if (_activeServiceFilter === '__all__') return true;
    const invId = event.investigation_id;
    if (!invId) return true;  // no investigation_id — show it
    const sid = _investigationServiceMap[invId];
    if (!sid) return true;  // unknown mapping — show it
    return sid === _activeServiceFilter;
}

/**
 * Check whether an event matches the active service filter.
 * Events without a service_tree_id (global/pipeline events) always pass.
 */
function _matchesServiceFilter(event) {
    if (_activeServiceFilter === '__all__') return true;
    // Events tagged with service_tree_id by the backend (both naming conventions)
    const sid = event.service_tree_id || event.ServiceTreeId;
    if (sid) {
        const match = sid === _activeServiceFilter;
        if (!match) console.debug('[filter] BLOCKED by sid:', event.type, sid, '!==', _activeServiceFilter);
        return match;
    }
    // Fallback: use source_xcv → reverse lookup to find the owning service
    const eventXcv = event.source_xcv || event.service_xcv;
    if (eventXcv) {
        const mappedSid = _xcvToServiceMap[eventXcv];
        if (mappedSid) {
            const match = mappedSid === _activeServiceFilter;
            if (!match) console.debug('[filter] BLOCKED by xcv:', event.type, 'xcv=', eventXcv, 'mapped=', mappedSid, '!==', _activeServiceFilter);
            return match;
        }
        console.debug('[filter] NO MAPPING for xcv:', event.type, 'xcv=', eventXcv, 'map=', JSON.stringify(_xcvToServiceMap));
    }
    // Pipeline-level events (pipeline_started, pipeline_complete, etc.)
    // with no service association — let them through
    const globalTypes = new Set([
        'pipeline_started', 'pipeline_complete', 'pipeline_error',
        'signal_evaluation_complete', 'investigations_starting',
        'investigation_milestone',
    ]);
    const isGlobal = globalTypes.has(event.type);
    if (!isGlobal) console.debug('[filter] BLOCKED unmatched non-global:', event.type, 'source_xcv=', event.source_xcv, 'service_xcv=', event.service_xcv);
    return isGlobal;
}

/**
 * Show the XCV for the currently-selected service in the filter bar.
 */
function _updateServiceXcvDisplay() {
    if (!serviceXcvDisplay) return;
    if (_activeServiceFilter === '__all__') {
        serviceXcvDisplay.textContent = '';
        serviceXcvDisplay.title = '';
        serviceXcvDisplay.onclick = null;
        return;
    }
    const info = _serviceMap[_activeServiceFilter];
    const xcv = info?.service_xcv || '';
    if (!xcv) { serviceXcvDisplay.textContent = ''; return; }
    const short = xcv.length > 12 ? xcv.substring(0, 8) + '...' : xcv;
    serviceXcvDisplay.textContent = `XCV: ${short}`;
    serviceXcvDisplay.title = `${xcv} (click to copy)`;
    serviceXcvDisplay.onclick = () => {
        navigator.clipboard.writeText(xcv).then(() => {
            serviceXcvDisplay.textContent = 'Copied!';
            setTimeout(() => { serviceXcvDisplay.textContent = `XCV: ${short}`; }, 1200);
        });
    };
}

/**
 * Replay all stored events through the filtered views after a filter change.
 */
function _replayFilteredEvents() {
    console.log('[replay] Starting replay for filter:', _activeServiceFilter,
        'total events:', _allEvents.length);
    // Clear views and panels, then replay matching events
    resetPanels();
    resetPhaseBar();
    clearStream();
    clearGraph();
    clearAgentFlow();
    clearTimeline();

    let lastMatchedType = null;
    let matchCount = 0;
    let blockCount = 0;
    let lastInvResponseHyp = null;
    let lastInvCompleteHyp = null;

    for (const event of _allEvents) {
        try {
        // Rebuild investigation → service mapping on replay
        if (event.type === 'investigation_started' && event.investigation_id) {
            const sid = event.service_tree_id || event.ServiceTreeId || '';
            if (sid) _investigationServiceMap[event.investigation_id] = sid;
        }

        // Narrator events go to stream view narrator (filtered by service)
        if (event.type === 'investigation_narrator_chunk' || event.type === 'investigation_narrator_done' || event.type === 'investigation_milestone') {
            if (_matchesNarratorFilter(event)) addStreamNarration(event);
            // Phase bar & orchestration still need service filtering below
        }
        if (!_matchesServiceFilter(event)) {
            blockCount++;
            continue;
        }

        matchCount++;
        lastMatchedType = event.type;

        // Phase bar and orchestration graph (after service filter)
        updatePhase(event);
        updateStreamOrchestration(event);

        // Narrator events already handled above — skip remaining view updates
        if (event.type === 'investigation_narrator_chunk' || event.type === 'investigation_narrator_done' || event.type === 'investigation_milestone') {
            continue;
        }

        // Track last hypothesis state from investigation events
        if (event.type === 'investigation_agent_response' && Array.isArray(event.hypotheses)) {
            lastInvResponseHyp = event.hypotheses.map(h => `${h.id}:${h.status}`).join(', ');
        }
        if (event.type === 'investigation_complete' && Array.isArray(event.hypotheses)) {
            lastInvCompleteHyp = event.hypotheses.map(h => `${h.id}:${h.status}`).join(', ');
        }

        addStreamEvent(event);
        addGraphEvent(event);
        addAgentFlowEvent(event);
        addTimelineEvent(event);
        updatePanels(event);
        } catch (err) {
            console.error('[replay] Error processing event:', event.type, err);
        }
    }

    console.log('[replay] Done: matched=%d, blocked=%d, last=%s',
        matchCount, blockCount, lastMatchedType);
    console.log('[replay] Final hyp from inv_response:', lastInvResponseHyp);
    console.log('[replay] Final hyp from inv_complete:', lastInvCompleteHyp);
    const debugCounts = getPanelCounts();
    console.log('[replay] Panel state after replay: hyp=%d, evidence=%d, actions=%d',
        debugCounts.hypotheses, debugCounts.evidence, debugCounts.actions);

    // Derive status badge from replayed events
    const hasPipelineComplete = _allEvents.some(e => e.type === 'pipeline_complete');
    const hasPipelineError = _allEvents.some(e => e.type === 'pipeline_error');
    if (hasPipelineError) {
        _setStatus('error', 'Error');
    } else if (hasPipelineComplete) {
        _setStatus('complete', 'Complete');
    } else if (_running) {
        _setStatus('running', 'Running');
    }

    // Update stats
    const counts = getPanelCounts();
    updateStat('events', matchCount);
    updateStat('signals', counts.signals);
    updateStat('compounds', counts.signals);
    updateStat('symptoms', counts.symptoms);
    updateStat('hypotheses', counts.hypotheses);
    updateStat('evidence', counts.evidence);
    updateStat('actions', counts.actions);
}
