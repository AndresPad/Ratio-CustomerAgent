/**
 * Context Panels Component
 *
 * Manages the right-hand context panels: Signals, Symptoms, Hypotheses,
 * Evidence, and Actions.  Each panel accumulates items as events arrive
 * and displays them in collapsible sections.
 */

/** Internal state for accumulated items */
const _state = {
    signals: [],       // { name, type, activated, value }
    symptoms: [],      // { id, description, source }
    hypotheses: [],    // { id, title, confidence, status }
    evidence: [],      // { id, hypothesis_id, description, type }
    actions: [],       // { id, type, description, status }
};

/**
 * Process an incoming event and update context panels if relevant.
 * @param {Object} event — parsed SSE event
 */
export function updatePanels(event) {
    const type = event.type;

    switch (type) {
        case 'SignalTypeEvaluated':
            _addSignal(event);
            break;
        case 'CompoundEvaluated':
            _addCompound(event);
            break;
        case 'SymptomTemplatesLoaded':
            _updateSymptoms(event);
            break;
        case 'investigation_agent_response':
            _updateFromInvestigation(event);
            break;
        case 'HypothesisScoring':
            _updateHypothesisScore(event);
            break;
        case 'HypothesisTransition':
            _updateHypothesisTransition(event);
            break;
        case 'EvidenceCycle':
            _addEvidenceCycle(event);
            break;
        case 'investigation_complete':
            _updateCompletionCounts(event);
            break;
    }
}

/**
 * Reset all panels to empty state.
 */
export function resetPanels() {
    _state.signals = [];
    _state.symptoms = [];
    _state.hypotheses = [];
    _state.evidence = [];
    _state.actions = [];
    _renderAll();
}

/* ── Private: Add/update items ──────────────────────────────────────────── */

function _addSignal(event) {
    const activated = event.ActivatedCount || event.activated_count || 0;
    if (activated === 0) return;  // Only show activated signals
    _state.signals.push({
        name: event.SignalName || event.signal_name || event.SignalType || event.signal_type || 'Unknown',
        signalTypeId: event.SignalTypeId || event.signal_type_id || '',
        type: 'signal',
        activated: activated,
        total: event.RowCount || event.row_count || event.TotalEvaluated || event.total_evaluated || 0,
        strength: event.MaxStrength || event.max_strength || 0,
        confidence: event.BestConfidence || event.best_confidence || '',
    });
    _renderSignals();
}

function _addCompound(event) {
    const activated = event.Activated ? 1 : 0;
    if (activated === 0) return;  // Only show activated compounds
    _state.signals.push({
        name: event.CompoundName || event.compound_name || 'Compound',
        type: 'compound',
        activated: activated,
        strength: event.Strength ?? event.strength ?? 0,
        confidence: event.Confidence || event.confidence || '',
        contributing: event.ContributingTypes || event.contributing_types || '',
    });
    _renderSignals();
}

function _updateSymptoms(event) {
    // SymptomTemplatesLoaded only has counts — real symptom data arrives
    // via investigation_agent_response.symptoms array; nothing to do here.
}

function _updateFromInvestigation(event) {
    // Replace panel data with real arrays from the backend
    console.debug('[panels] _updateFromInvestigation agent=%s, inv=%s, sid=%s, hyp_statuses=%s',
        event.agent, event.investigation_id, event.service_tree_id || event.ServiceTreeId || '(none)',
        (event.hypotheses || []).map(h => `${h.id}:${h.status}`).join(', '));
    if (Array.isArray(event.symptoms)) {
        _state.symptoms = event.symptoms.map(s => ({
            id: s.id || '',
            text: s.text || '',
            category: s.category || '',
        }));
    }
    if (Array.isArray(event.hypotheses)) {
        _state.hypotheses = event.hypotheses.map(h => ({
            id: h.id || '',
            statement: h.statement || '',
            status: h.status || 'active',
            confidence: h.confidence || 0,
            match_score: h.match_score || 0,
        }));
    }
    if (Array.isArray(event.evidence)) {
        _state.evidence = event.evidence.map(e => ({
            id: e.id || '',
            er_id: e.er_id || '',
            summary: e.summary || '',
            verdict: e.verdict || '',
            preliminary_verdict: e.preliminary_verdict || '',
        }));
    }
    if (Array.isArray(event.actions)) {
        _state.actions = event.actions.map(a => ({
            action_id: a.action_id || '',
            display_name: a.display_name || '',
            tier: a.tier || '',
            priority: a.priority || 0,
        }));
    }
    _renderAll();
}

function _updateHypothesisScore(event) {
    const hypId = event.HypothesisId || event.hypothesis_id || '';
    const score = event.Score || event.score || event.Confidence || 0;
    const name = event.HypothesisTitle || event.hypothesis_title || hypId;
    let found = _state.hypotheses.find(h => h.id === hypId);
    if (!found) {
        found = { id: hypId, title: name, confidence: 0, status: 'active' };
        _state.hypotheses.push(found);
    }
    found.confidence = Math.round(score * 100) / 100;
    found.title = name || found.title;
    _renderHypotheses();
}

function _updateHypothesisTransition(event) {
    const hypId = event.HypothesisId || event.hypothesis_id || '';
    const newStatus = event.NewStatus || event.new_status || event.ToStatus || event.to_status || 'unknown';
    const statement = event.Statement || event.statement || '';
    const confidence = event.Confidence || event.confidence || 0;
    console.debug('[panels] HypothesisTransition: %s → %s (sid=%s)',
        hypId, newStatus, event.service_tree_id || event.ServiceTreeId || '(none)');
    let found = _state.hypotheses.find(h => h.id === hypId);
    if (found) {
        found.status = newStatus;
        if (statement) found.statement = statement;
        if (confidence) found.confidence = confidence;
    } else {
        console.debug('[panels] HypothesisTransition: hypothesis %s NOT FOUND in _state, pushing new', hypId);
        _state.hypotheses.push({
            id: hypId,
            statement: statement || hypId,
            status: newStatus,
            confidence: confidence,
            match_score: 0,
        });
    }
    _renderHypotheses();
}

function _addEvidenceCycle(event) {
    _state.evidence.push({
        id: `ev-${_state.evidence.length}`,
        cycle: event.Cycle || event.cycle || _state.evidence.length + 1,
        description: `Evidence cycle ${event.Cycle || _state.evidence.length}`,
        hypothesis_id: event.HypothesisId || event.hypothesis_id || '',
    });
    _renderEvidence();
}

function _updateCompletionCounts(event) {
    // investigation_complete carries the final state of all arrays —
    // apply them so that replay after filter change shows final statuses.
    console.debug('[panels] investigation_complete: inv=%s, sid=%s, hyp_statuses=%s',
        event.investigation_id, event.service_tree_id || event.ServiceTreeId || '(none)',
        (event.hypotheses || []).map(h => `${h.id}:${h.status}`).join(', '));
    if (Array.isArray(event.hypotheses)) {
        _state.hypotheses = event.hypotheses.map(h => ({
            id: h.id || '',
            statement: h.statement || '',
            status: h.status || 'active',
            confidence: h.confidence || 0,
            match_score: h.match_score || 0,
        }));
    }
    if (Array.isArray(event.evidence)) {
        _state.evidence = event.evidence.map(e => ({
            id: e.id || '',
            er_id: e.er_id || '',
            summary: e.summary || '',
            verdict: e.verdict || '',
            preliminary_verdict: e.preliminary_verdict || '',
        }));
    }
    if (Array.isArray(event.actions)) {
        _state.actions = event.actions.map(a => ({
            action_id: a.action_id || '',
            display_name: a.display_name || '',
            tier: a.tier || '',
            priority: a.priority || 0,
        }));
    }
    _renderAll();
}

/* ── Private: Rendering ─────────────────────────────────────────────────── */

function _renderAll() {
    _renderSignals();
    _renderSymptoms();
    _renderHypotheses();
    _renderEvidence();
    _renderActions();
}

function _renderSignals() {
    const body = document.getElementById('signals-body');
    const count = document.querySelector('#ctx-signals .ctx-count');
    if (!body) return;
    if (count) count.textContent = _state.signals.length;
    body.innerHTML = _state.signals.map(s => {
        if (s.type === 'compound') {
            return `<div class="signal-card">
                <div class="signal-card-header">
                    <span class="signal-type-id compound">${_esc(s.name)}</span>
                    <span class="signal-badge activated">ACTIVATED</span>
                </div>
                <div class="signal-card-title">${_esc(s.name)}</div>
                <div class="signal-card-detail">strength ${Number(s.strength).toFixed(2)} · ${_esc(String(s.confidence).toUpperCase())}${s.contributing ? ` · signals: ${_esc(s.contributing)}` : ''}</div>
            </div>`;
        }
        const conf = s.confidence ? String(s.confidence).toUpperCase() : '';
        return `<div class="signal-card">
            <div class="signal-card-header">
                <span class="signal-type-id">${_esc(s.signalTypeId || s.name)}</span>
                <span class="signal-badge activated">ACTIVATED</span>
            </div>
            <div class="signal-card-title">${_esc(s.name)}</div>
            <div class="signal-card-detail">strength ${Number(s.strength).toFixed(2)} · ${conf}${s.total ? ` · ${s.total} rows` : ''}</div>
        </div>`;
    }).join('');
}

function _renderSymptoms() {
    const body = document.getElementById('symptoms-body');
    const count = document.querySelector('#ctx-symptoms .ctx-count');
    if (!body) return;
    if (count) count.textContent = _state.symptoms.length;
    body.innerHTML = _state.symptoms.map(s => {
        const cat = (s.category || '').toLowerCase();
        return `<div class="sym-card">
            <div class="sym-card-header">
                <span class="sym-id">${_esc(s.id)}</span>
                ${s.category ? `<span class="sym-badge">${_esc(s.category)}</span>` : ''}
            </div>
            <div class="sym-card-text">${_esc(s.text)}</div>
        </div>`;
    }).join('');
}

function _renderHypotheses() {
    const body = document.getElementById('hypotheses-body');
    const count = document.querySelector('#ctx-hypotheses .ctx-count');
    if (!body) return;
    if (count) count.textContent = _state.hypotheses.length;
    body.innerHTML = _state.hypotheses.map((h, idx) => {
        const confPct = Math.min(100, Math.max(0, h.confidence * 100));
        const matchPct = Math.min(100, Math.max(0, (h.match_score || 0) * 100));
        const score = ((h.match_score || 0) * 5).toFixed(2);
        const rank = idx + 1;
        const statusLower = (h.status || 'active').toLowerCase();
        const isContributing = statusLower === 'resolved_as_contributing' || statusLower === 'contributing';
        const badgeClass = statusLower === 'refuted' || statusLower === 'rejected' ? 'refuted'
            : statusLower === 'confirmed' ? 'confirmed'
            : isContributing ? 'confirmed'
            : 'evaluating';
        const badgeLabel = statusLower === 'refuted' || statusLower === 'rejected' ? 'REFUTED'
            : statusLower === 'confirmed' ? 'CONFIRMED'
            : isContributing ? 'CONTRIBUTING'
            : 'EVALUATING';
        const matchColor = statusLower === 'refuted' || statusLower === 'rejected' ? 'var(--color-error)'
            : statusLower === 'confirmed' || isContributing ? 'var(--color-complete)'
            : 'var(--color-decision)';
        return `<div class="hyp-card">
            <div class="hyp-card-header">
                <span class="hyp-id">${_esc(h.id)}</span>
                <span class="hyp-badge ${badgeClass}">${badgeLabel}</span>
            </div>
            <div class="hyp-card-title">${_esc(h.statement || h.id)}</div>
            <div class="hyp-card-detail">rank #${rank} · confidence ${confPct.toFixed(0)}%</div>
            <div class="hyp-bar-row">
                <span class="hyp-bar-label">confidence</span>
                <div class="hyp-bar"><div class="hyp-bar-fill" style="width:${confPct}%;background:var(--color-evidence)"></div></div>
                <span class="hyp-bar-value">${confPct.toFixed(0)}%</span>
            </div>
        </div>`;
    }).join('');
}

function _renderEvidence() {
    const body = document.getElementById('evidence-body');
    const count = document.querySelector('#ctx-evidence .ctx-count');
    if (!body) return;
    if (count) count.textContent = _state.evidence.length;

    const total = _state.evidence.length;
    const _isResolved = e => e.verdict || e.preliminary_verdict === 'from_signal' || e.preliminary_verdict === 'collected' || e.preliminary_verdict === 'already_collected' || e.preliminary_verdict === 'not_available' || /no data|not available/i.test(e.summary);
    const resolved = _state.evidence.filter(_isResolved).length;
    const pct = total > 0 ? Math.round((resolved / total) * 100) : 0;

    const progressBar = total > 0 ? `
        <div class="ev-progress">
            <span class="ev-progress-label">${pct}% complete</span>
            <span class="ev-progress-value">${pct}%</span>
        </div>
        <div class="ev-progress-bar"><div class="ev-progress-fill" style="width:${pct}%"></div></div>` : '';

    const cards = _state.evidence.map(e => {
        const isDone = !!e.verdict;
        const isFromSignal = e.preliminary_verdict === 'from_signal';
        const isCollected = e.preliminary_verdict === 'collected' || e.preliminary_verdict === 'already_collected';
        const isNoData = e.preliminary_verdict === 'not_available' || /no data|not available/i.test(e.summary);
        const badgeClass = isDone ? 'done' : isFromSignal ? 'provided' : isCollected ? 'done' : isNoData ? 'nodata' : 'pending';
        const badgeLabel = isDone ? 'DONE' : isFromSignal ? 'PROVIDED' : isCollected ? 'COLLECTED' : isNoData ? 'NOT AVAILABLE' : 'PENDING';
        return `<div class="ev-card${isDone ? ' ev-done' : isFromSignal ? ' ev-provided' : isCollected ? ' ev-done' : isNoData ? ' ev-nodata' : ''}">
            <div class="ev-card-row">
                <span class="ev-tool-name">${_esc(e.summary || e.er_id || e.id)}</span>
                <span class="ev-badge ${badgeClass}">${badgeLabel}</span>
            </div>
            <div class="ev-sub-tool">${_esc(e.er_id || e.id)}</div>
        </div>`;
    }).join('');

    body.innerHTML = progressBar + cards;
}

function _renderActions() {
    const body = document.getElementById('actions-body');
    const count = document.querySelector('#ctx-actions .ctx-count');
    if (!body) return;
    if (count) count.textContent = _state.actions.length;
    body.innerHTML = _state.actions.map(a => {
        const tierLower = (a.tier || '').toLowerCase();
        const tierClass = tierLower === 'critical' ? 'critical'
            : tierLower === 'high' ? 'high'
            : tierLower === 'medium' ? 'medium' : 'low';
        return `<div class="act-card">
            <div class="act-card-header">
                <span class="act-name">${_esc(a.display_name || a.action_id)}</span>
                ${a.tier ? `<span class="act-tier ${tierClass}">${_esc(a.tier)}</span>` : ''}
            </div>
            <div class="act-card-detail">
                <span class="act-id">${_esc(a.action_id)}</span>
                ${a.priority ? `<span class="act-priority">P${a.priority}</span>` : ''}
            </div>
        </div>`;
    }).join('');
}

/** Escape HTML to prevent XSS in rendered content */
function _esc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/**
 * Return current panel state (for stats).
 */
export function getPanelCounts() {
    return {
        signals: _state.signals.length,
        symptoms: _state.symptoms.length,
        hypotheses: _state.hypotheses.length,
        evidence: _state.evidence.length,
        actions: _state.actions.length,
    };
}
