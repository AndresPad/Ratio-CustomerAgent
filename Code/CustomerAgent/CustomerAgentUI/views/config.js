/**
 * Config View — CRUD editor for all config JSON files.
 *
 * Renders in #view-config. Sub-views:
 *  - list   — table of items for an entity
 *  - edit   — form/JSON editor for a single item
 *  - mapping — read-only cross-reference table
 *
 * Exports: initConfigView()
 */

/* ── State ──────────────────────────────────────────────────────────────── */
let _container = null;
let _currentEntity = null;   // e.g. "signals", "symptoms"
let _currentMapping = null;  // e.g. "signal-symptom"
let _mode = 'list';          // list | edit | mapping

/* Entity display metadata */
const ENTITY_META = {
    signals:            { label: 'Signals',       icon: '📡', columns: ['id', 'name', 'data_source'] },
    symptoms:           { label: 'Symptoms',      icon: '🩺', columns: ['id', 'name', 'weight', '_source_file'] },
    hypotheses:         { label: 'Hypotheses',     icon: '🧠', columns: null },  // JSON editor
    evidence:           { label: 'Evidence',       icon: '🔍', columns: ['id', 'description', 'category_tag', 'tool_name'] },
    actions:            { label: 'Actions',        icon: '⚡', columns: ['id', 'display_name', 'type', 'tier'] },
    dependencies:       { label: 'Dependencies',   icon: '🔗', columns: ['name', 'service_tree_id', 'category', '_source_file'] },
    monitoring_context: { label: 'Monitoring',     icon: '📊', columns: null },  // JSON editor
    scoring_config:     { label: 'Scoring',        icon: '📐', columns: null },  // JSON editor
};

const MAPPING_META = {
    'signal-symptom':     { label: 'Signal → Symptom',    from: 'Signal', to: 'Symptom' },
    'symptom-hypothesis': { label: 'Symptom → Hypothesis', from: 'Symptom', to: 'Hypothesis' },
    'evidence-hypothesis':{ label: 'Evidence → Hypothesis', from: 'Evidence', to: 'Hypothesis' },
    'action-hypothesis':  { label: 'Action → Hypothesis',  from: 'Action', to: 'Hypothesis' },
};

/* ── API helpers ────────────────────────────────────────────────────────── */

async function apiGet(path) {
    const resp = await fetch(`/api/config/${path}`);
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail?.message || err.detail || resp.statusText);
    }
    return resp.json();
}

async function apiPost(path, body) {
    const resp = await fetch(`/api/config/${path}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail?.message || err.detail || resp.statusText);
    }
    return resp.json();
}

async function apiPut(path, body) {
    const resp = await fetch(`/api/config/${path}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
    });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail?.message || err.detail || resp.statusText);
    }
    return resp.json();
}

async function apiDelete(path) {
    const resp = await fetch(`/api/config/${path}`, { method: 'DELETE' });
    if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error(err.detail?.message || err.detail || resp.statusText);
    }
    return resp.json();
}

/* ── Init ───────────────────────────────────────────────────────────────── */

export function initConfigView() {
    _container = document.getElementById('view-config');
    if (!_container) return;

    // Wire sidebar config nav clicks
    document.querySelectorAll('.config-nav-item[data-config]').forEach(btn => {
        btn.addEventListener('click', () => {
            _setActiveNav(btn);
            _currentMapping = null;
            _currentEntity = btn.dataset.config;
            _mode = 'list';
            _switchToConfigView();
            _loadEntity(_currentEntity);
        });
    });

    document.querySelectorAll('.config-nav-item[data-mapping]').forEach(btn => {
        btn.addEventListener('click', () => {
            _setActiveNav(btn);
            _currentEntity = null;
            _currentMapping = btn.dataset.mapping;
            _mode = 'mapping';
            _switchToConfigView();
            _loadMapping(_currentMapping);
        });
    });

    // Load entity counts on startup
    _loadCounts();
}

/* ── Navigation helpers ─────────────────────────────────────────────────── */

function _setActiveNav(activeBtn) {
    document.querySelectorAll('.config-nav-item').forEach(b => b.classList.remove('active'));
    activeBtn.classList.add('active');
}

function _switchToConfigView() {
    // Deactivate all view tabs and views, activate config
    document.querySelectorAll('.view-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
    const configView = document.getElementById('view-config');
    if (configView) configView.classList.add('active');
    // Hide stats bar and service filter when in config view
    const statsBar = document.getElementById('stats-bar');
    const filterBar = document.getElementById('service-filter-bar');
    if (statsBar) statsBar.style.display = 'none';
    if (filterBar) filterBar.style.display = 'none';
}

function _restoreNonConfigView() {
    const statsBar = document.getElementById('stats-bar');
    if (statsBar) statsBar.style.display = '';
}

/* ── Load entity counts ─────────────────────────────────────────────────── */

async function _loadCounts() {
    try {
        const entities = await apiGet('entities');
        for (const ent of entities) {
            const el = document.getElementById(`cfg-count-${ent.key}`);
            if (el) el.textContent = ent.count;
        }
    } catch (e) {
        console.warn('Failed to load config counts:', e);
    }
}

/* ── Entity List View ───────────────────────────────────────────────────── */

async function _loadEntity(entity) {
    _container.innerHTML = '<div style="padding:20px;color:var(--text-muted)">Loading…</div>';
    try {
        const resp = await apiGet(entity);
        const meta = ENTITY_META[entity];

        // Full-file JSON editor entities
        if (meta.columns === null) {
            if (entity === 'hypotheses') {
                _renderHypothesesView(resp);
            } else {
                _renderJsonEditor(entity, resp.data || resp);
            }
            return;
        }

        const items = resp.items || [];
        _renderTable(entity, items, meta);
    } catch (e) {
        _container.innerHTML = `<div style="padding:20px;color:#ef4444">Error: ${_esc(e.message)}</div>`;
    }
}

function _renderTable(entity, items, meta) {
    const cols = meta.columns;
    const canAdd = !['hypotheses', 'monitoring_context', 'scoring_config'].includes(entity);

    let html = `
        <div class="config-toolbar">
            <h2>${meta.icon} ${meta.label} (${items.length})</h2>
            ${canAdd ? `<button class="btn-add" id="cfg-btn-add">+ Add New</button>` : ''}
        </div>
        <table class="config-table">
            <thead><tr>
                ${cols.map(c => `<th>${_colLabel(c)}</th>`).join('')}
                <th>Actions</th>
            </tr></thead>
            <tbody>
    `;

    for (const item of items) {
        html += '<tr>';
        for (const col of cols) {
            const val = item[col];
            const cls = col === 'id' ? ' class="id-cell"' : '';
            if (Array.isArray(val)) {
                html += `<td${cls}>${val.length} items</td>`;
            } else {
                html += `<td${cls}>${_esc(String(val ?? ''))}</td>`;
            }
        }
        const itemId = item.id || item.name || '';
        html += `<td class="actions-cell">
            <button class="btn-edit" data-id="${_esc(itemId)}">Edit</button>
            <button class="btn-delete" data-id="${_esc(itemId)}">Delete</button>
        </td>`;
        html += '</tr>';
    }

    html += '</tbody></table>';
    _container.innerHTML = html;

    // Wire edit buttons
    _container.querySelectorAll('.btn-edit').forEach(btn => {
        btn.addEventListener('click', () => _loadEditForm(entity, btn.dataset.id, items));
    });

    // Wire delete buttons
    _container.querySelectorAll('.btn-delete').forEach(btn => {
        btn.addEventListener('click', () => _confirmDelete(entity, btn.dataset.id));
    });

    // Wire add button
    const addBtn = document.getElementById('cfg-btn-add');
    if (addBtn) {
        addBtn.addEventListener('click', () => _loadEditForm(entity, null, items));
    }
}

/* ── Hypotheses special view ────────────────────────────────────────────── */

function _renderHypothesesView(resp) {
    const files = resp.files || [];
    let html = `
        <div class="config-toolbar">
            <h2>🧠 Hypotheses (${files.length} files)</h2>
        </div>
    `;

    for (const f of files) {
        const fileName = f.file;
        html += `
            <div style="margin-bottom:16px">
                <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
                    <strong style="color:var(--text-primary);font-size:0.9rem">${_esc(fileName)}</strong>
                    <button class="btn-hyp-edit" data-file="${_esc(fileName)}" style="padding:3px 8px;border:1px solid var(--border);background:var(--bg-card);border-radius:3px;cursor:pointer;font-size:0.75rem;color:var(--text-secondary)">Edit JSON</button>
                </div>
            </div>
        `;
    }
    _container.innerHTML = html;

    // Wire edit buttons for each hypothesis file
    _container.querySelectorAll('.btn-hyp-edit').forEach(btn => {
        btn.addEventListener('click', async () => {
            const fileName = btn.dataset.file;
            const fileData = files.find(f => f.file === fileName);
            if (fileData) {
                _renderJsonEditorRaw(`hypotheses/${fileName}`, fileData.data);
            }
        });
    });
}

/* ── JSON Editor (full-file entities) ───────────────────────────────────── */

function _renderJsonEditor(entity, data) {
    const meta = ENTITY_META[entity] || { label: entity, icon: '📄' };
    const jsonStr = JSON.stringify(data, null, 2);

    _container.innerHTML = `
        <div class="config-toolbar">
            <h2>${meta.icon} ${meta.label}</h2>
            <button class="btn-add" id="cfg-json-save">Save</button>
        </div>
        <textarea class="config-json-editor" id="cfg-json-textarea">${_esc(jsonStr)}</textarea>
        <div id="cfg-json-status" style="margin-top:8px;font-size:0.8rem"></div>
    `;

    document.getElementById('cfg-json-save').addEventListener('click', async () => {
        const textarea = document.getElementById('cfg-json-textarea');
        const status = document.getElementById('cfg-json-status');
        try {
            const parsed = JSON.parse(textarea.value);
            await apiPut(`${entity}/_all`, { item: parsed });
            status.innerHTML = '<span style="color:#10b981">✓ Saved successfully</span>';
            _loadCounts();
        } catch (e) {
            status.innerHTML = `<span style="color:#ef4444">✗ ${_esc(e.message)}</span>`;
        }
    });
}

function _renderJsonEditorRaw(path, data) {
    const jsonStr = JSON.stringify(data, null, 2);
    const parts = path.split('/');
    const fileName = parts[parts.length - 1];
    _container.innerHTML = `
        <div class="config-toolbar">
            <button class="btn-back" id="cfg-json-back">← Back</button>
            <h2>📄 ${_esc(fileName)}</h2>
            <button class="btn-add" id="cfg-json-save">Save</button>
        </div>
        <textarea class="config-json-editor" id="cfg-json-textarea">${_esc(jsonStr)}</textarea>
        <div id="cfg-json-status" style="margin-top:8px;font-size:0.8rem"></div>
    `;

    document.getElementById('cfg-json-back').addEventListener('click', () => {
        const baseEntity = parts[0];
        _loadEntity(baseEntity);
    });

    document.getElementById('cfg-json-save').addEventListener('click', async () => {
        const textarea = document.getElementById('cfg-json-textarea');
        const status = document.getElementById('cfg-json-status');
        try {
            const parsed = JSON.parse(textarea.value);
            // Use the file-specific save endpoint
            await apiPut(`hypotheses/file/${encodeURIComponent(fileName)}`, { item: parsed });
            status.innerHTML = '<span style="color:#10b981">✓ Saved successfully</span>';
            _loadCounts();
        } catch (e) {
            status.innerHTML = `<span style="color:#ef4444">✗ ${_esc(e.message)}</span>`;
        }
    });
}

/* ── Edit Form ──────────────────────────────────────────────────────────── */

function _loadEditForm(entity, itemId, items) {
    const meta = ENTITY_META[entity];
    const isNew = !itemId;
    const item = isNew ? {} : items.find(i => (i.id || i.name) === itemId) || {};

    // Build field list from existing item keys or columns
    const fields = meta.columns || Object.keys(item).filter(k => !k.startsWith('_'));

    let html = `
        <div class="config-toolbar">
            <button class="btn-back" id="cfg-form-back">← Back to list</button>
            <h2>${meta.icon} ${isNew ? 'New' : 'Edit'} ${meta.label.replace(/s$/, '')}</h2>
        </div>
        <div class="config-form" id="cfg-form">
    `;

    for (const field of fields) {
        const val = item[field];
        if (Array.isArray(val)) {
            html += `
                <div class="form-group">
                    <label>${_colLabel(field)}</label>
                    <textarea name="${field}" rows="3">${_esc(JSON.stringify(val, null, 2))}</textarea>
                    <div class="form-hint">JSON array — one entry per line</div>
                </div>
            `;
        } else if (typeof val === 'object' && val !== null) {
            html += `
                <div class="form-group">
                    <label>${_colLabel(field)}</label>
                    <textarea name="${field}" rows="4">${_esc(JSON.stringify(val, null, 2))}</textarea>
                    <div class="form-hint">JSON object</div>
                </div>
            `;
        } else if (typeof val === 'number') {
            html += `
                <div class="form-group">
                    <label>${_colLabel(field)}</label>
                    <input type="number" name="${field}" value="${val ?? ''}" step="any">
                </div>
            `;
        } else {
            const inputVal = val ?? '';
            const isLong = String(inputVal).length > 100;
            if (isLong) {
                html += `
                    <div class="form-group">
                        <label>${_colLabel(field)}</label>
                        <textarea name="${field}" rows="3">${_esc(String(inputVal))}</textarea>
                    </div>
                `;
            } else {
                html += `
                    <div class="form-group">
                        <label>${_colLabel(field)}</label>
                        <input type="text" name="${field}" value="${_esc(String(inputVal))}">
                    </div>
                `;
            }
        }
    }

    // For new items, if there are additional common fields not in columns, add them
    if (isNew) {
        const allFields = new Set(fields);
        // Add any fields from the first existing item that weren't in columns
        if (items.length > 0) {
            for (const key of Object.keys(items[0])) {
                if (!key.startsWith('_') && !allFields.has(key)) {
                    html += `
                        <div class="form-group">
                            <label>${_colLabel(key)}</label>
                            <input type="text" name="${key}" value="">
                        </div>
                    `;
                }
            }
        }
    }

    // Show hidden fields for reference
    if (item._source_file) {
        html += `<input type="hidden" name="_source_file" value="${_esc(item._source_file)}">`;
    }

    html += `
            <div class="form-actions">
                <button class="btn-save" id="cfg-form-save">${isNew ? 'Create' : 'Save Changes'}</button>
                <button class="btn-cancel" id="cfg-form-cancel">Cancel</button>
            </div>
            <div id="cfg-form-status" style="margin-top:8px;font-size:0.8rem"></div>
        </div>
    `;

    _container.innerHTML = html;

    // Wire back/cancel
    const goBack = () => _loadEntity(entity);
    document.getElementById('cfg-form-back').addEventListener('click', goBack);
    document.getElementById('cfg-form-cancel').addEventListener('click', goBack);

    // Wire save
    document.getElementById('cfg-form-save').addEventListener('click', async () => {
        const form = document.getElementById('cfg-form');
        const status = document.getElementById('cfg-form-status');
        const newItem = _collectFormData(form);

        try {
            if (isNew) {
                await apiPost(entity, { item: newItem });
                status.innerHTML = '<span style="color:#10b981">✓ Created successfully</span>';
            } else {
                await apiPut(`${entity}/${encodeURIComponent(itemId)}`, { item: newItem });
                status.innerHTML = '<span style="color:#10b981">✓ Saved successfully</span>';
            }
            _loadCounts();
            setTimeout(goBack, 800);
        } catch (e) {
            status.innerHTML = `<span style="color:#ef4444">✗ ${_esc(e.message)}</span>`;
        }
    });
}

function _collectFormData(form) {
    const data = {};
    form.querySelectorAll('input[name], textarea[name], select[name]').forEach(el => {
        const name = el.name;
        let val = el.value.trim();
        if (el.type === 'number') {
            data[name] = val ? parseFloat(val) : 0;
        } else if (val.startsWith('[') || val.startsWith('{')) {
            try { data[name] = JSON.parse(val); } catch { data[name] = val; }
        } else {
            data[name] = val;
        }
    });
    return data;
}

/* ── Delete with validation ─────────────────────────────────────────────── */

async function _confirmDelete(entity, itemId) {
    try {
        const refs = await apiGet(`${entity}/${encodeURIComponent(itemId)}/references`);
        if (!refs.can_delete && refs.references.length > 0) {
            _showDeleteDialog(entity, itemId, refs.references);
        } else {
            _showDeleteDialog(entity, itemId, []);
        }
    } catch {
        _showDeleteDialog(entity, itemId, []);
    }
}

function _showDeleteDialog(entity, itemId, refs) {
    const hasRefs = refs.length > 0;
    const overlay = document.createElement('div');
    overlay.className = 'config-delete-dialog';

    let refHtml = '';
    if (hasRefs) {
        refHtml = `
            <div class="ref-list">
                <strong>Referenced by:</strong><br>
                ${refs.map(r => `• ${r.entity} / ${r.id} (${r.field})`).join('<br>')}
            </div>
        `;
    }

    overlay.innerHTML = `
        <div class="dialog-content">
            <h3>Delete ${_esc(itemId)}?</h3>
            <p>${hasRefs
                ? 'This item is referenced by other config items. Deleting it may break relationships.'
                : 'This action cannot be undone.'
            }</p>
            ${refHtml}
            <div class="dialog-actions">
                <button class="btn-cancel-delete">Cancel</button>
                <button class="btn-confirm-delete">${hasRefs ? 'Force Delete' : 'Delete'}</button>
            </div>
        </div>
    `;

    document.body.appendChild(overlay);

    overlay.querySelector('.btn-cancel-delete').addEventListener('click', () => overlay.remove());
    overlay.querySelector('.btn-confirm-delete').addEventListener('click', async () => {
        try {
            const path = hasRefs
                ? `${entity}/${encodeURIComponent(itemId)}?force=true`
                : `${entity}/${encodeURIComponent(itemId)}`;
            await apiDelete(path);
            overlay.remove();
            _loadEntity(entity);
            _loadCounts();
        } catch (e) {
            alert('Delete failed: ' + e.message);
            overlay.remove();
        }
    });
}

/* ── Mapping View ───────────────────────────────────────────────────────── */

async function _loadMapping(mappingKey) {
    _container.innerHTML = '<div style="padding:20px;color:var(--text-muted)">Loading…</div>';
    const meta = MAPPING_META[mappingKey];
    try {
        const resp = await apiGet(`mappings/${mappingKey}`);
        const mappings = resp.mappings || [];

        let html = `
            <div class="config-toolbar">
                <h2>${meta.label} (${mappings.length})</h2>
            </div>
            <table class="mapping-table">
                <thead><tr>
                    <th>${meta.from}</th>
                    <th>${meta.to}(s)</th>
                </tr></thead>
                <tbody>
        `;

        for (const m of mappings) {
            const fromId = m.signal_id || m.symptom_id || m.evidence_id || m.action_id || '';
            const fromName = m.signal_name || m.symptom_name || m.evidence_name || m.action_name || '';
            const linked = m.symptoms || m.hypotheses || [];

            html += `<tr>`;
            html += `<td><span class="id-cell">${_esc(fromId)}</span><br><span style="font-size:0.78rem;color:var(--text-secondary)">${_esc(fromName)}</span></td>`;
            html += `<td>`;
            if (linked.length === 0) {
                html += `<span class="mapping-tag empty">none</span>`;
            } else {
                for (const l of linked) {
                    html += `<span class="mapping-tag">${_esc(l.id || '')} ${l.name ? '— ' + _esc(l.name) : ''}</span> `;
                }
            }
            html += `</td></tr>`;
        }

        html += '</tbody></table>';
        _container.innerHTML = html;
    } catch (e) {
        _container.innerHTML = `<div style="padding:20px;color:#ef4444">Error: ${_esc(e.message)}</div>`;
    }
}

/* ── Utilities ──────────────────────────────────────────────────────────── */

function _esc(str) {
    const div = document.createElement('div');
    div.textContent = str;
    return div.innerHTML;
}

function _colLabel(col) {
    return col.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).replace(/^_/, '');
}
