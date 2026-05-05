/**
 * Sandbox View — Learning-Style Layout
 *
 * Pipeline graph → status bar → scenario card →
 * two-column (Code | Output) → file list.
 *
 * Passive view: runs the normal pipeline (▶ Run Pipeline) and captures
 * any sandbox_* events emitted when agents trigger code execution.
 * The sandbox_coder agent's @tool calls emit events via AgentLogger,
 * which flow through the existing SSE pipeline and are routed here.
 *
 * Exports: initSandboxView(), addSandboxEvent(), clearSandbox()
 */

/* ── State ──────────────────────────────────────────────────────────────── */
let _container = null;
let _state = 'idle'; // idle | running | complete | error
let _startTime = null;
let _timerInterval = null;
let _code = '';
let _stdout = '';
let _stderr = '';
let _files = [];
let _duration = null;
let _filename = '';
let _executionCount = 0;

/* ── Pipeline Nodes ─────────────────────────────────────────────────────── */
const PIPELINE_NODES = [
    { key: 'write',   label: 'Write',   icon: '✏️', color: '#6366f1' },
    { key: 'execute', label: 'Execute', icon: '⚡', color: '#f59e0b' },
    { key: 'result',  label: 'Result',  icon: '📊', color: '#10b981' },
];

let _activeNode = null;
let _doneNodes = new Set();

/* ── Initialization ─────────────────────────────────────────────────────── */

export function initSandboxView() {
    _container = document.getElementById('view-sandbox');
    if (!_container) return;
    _injectStyles();
    _render();
}

export function addSandboxEvent(event) {
    console.log('[SandboxView] Received event:', event.type, event);
    if (!_container) {
        console.warn('[SandboxView] No container!');
        return;
    }

    // Auto-start timer on first sandbox event
    if (_state === 'idle') {
        _state = 'running';
        _startTime = Date.now();
        _timerInterval = setInterval(_updateElapsed, 100);
        _updateStatusBar();
        _updateScenarioCard('Sandbox agent is writing code…');
    }

    switch (event.type) {
        case 'sandbox_code_generated':
            _code = event.code || '';
            _filename = event.filename || 'agent_script.py';
            _activeNode = 'write';
            _doneNodes.add('write');
            _updatePipeline();
            _updateCodePanel();
            break;

        case 'sandbox_execution_started':
            _activeNode = 'execute';
            _state = 'running';
            _updatePipeline();
            _updateStatusBar();
            break;

        case 'sandbox_execution_complete':
            _stdout = event.stdout || '';
            _stderr = event.stderr || '';
            _files = event.files || [];
            _duration = event.duration_seconds;
            _doneNodes.add('execute');
            _doneNodes.add('result');
            _activeNode = 'result';
            _state = event.success ? 'complete' : 'error';
            if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
            _updatePipeline();
            _updateStatusBar();
            _updateOutputPanel();
            _updateFilesBar();
            break;

        case 'sandbox_file_downloaded':
            _markFileDownloaded(event.remote_path);
            break;

        case 'sandbox_error':
            _stderr = event.error || 'Unknown error';
            _state = 'error';
            _activeNode = 'execute';
            if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
            _updatePipeline();
            _updateStatusBar();
            _updateOutputPanel();
            break;
    }
}

export function clearSandbox() {
    _state = 'idle';
    _code = '';
    _stdout = '';
    _stderr = '';
    _files = [];
    _duration = null;
    _filename = '';
    _executionCount = 0;
    _activeNode = null;
    _doneNodes = new Set();
    _startTime = null;
    if (_timerInterval) { clearInterval(_timerInterval); _timerInterval = null; }
    _render();
}

/* ── Rendering: Full Layout ─────────────────────────────────────────────── */

function _render() {
    if (!_container) return;

    _container.innerHTML = `
        <!-- Pipeline Graph -->
        <div class="sbx-pipeline-graph" id="sbx-pipeline-graph"></div>

        <!-- Status Bar -->
        <div class="sbx-status-bar" id="sbx-status-bar">
            <div class="sbx-status-left">
                <span class="sbx-status-label">Sandbox Code Runner</span>
            </div>
            <div class="sbx-status-right">
                <button class="sbx-replay-btn" id="sbx-replay-btn" title="Re-run the pipeline">🔄 Replay</button>
                <span class="sbx-badge badge-idle" id="sbx-badge">IDLE</span>
                <span class="sbx-elapsed" id="sbx-elapsed">—</span>
            </div>
        </div>

        <!-- Scenario Card -->
        <div class="sbx-scenario-card" id="sbx-scenario-card">
            <span class="sbx-scenario-icon">🔬</span>
            <span class="sbx-scenario-text">Press <strong>▶ Investigate</strong> — any code execution by agents will appear here</span>
        </div>

        <!-- Two-Column: Code | Output -->
        <div class="sbx-two-col">
            <div class="sbx-col-left" id="sbx-code-panel">
                <div class="sbx-col-header">✏️ Code</div>
                <pre class="sbx-code-content" id="sbx-code-content"><span class="sbx-waiting">No code yet</span></pre>
            </div>
            <div class="sbx-col-right" id="sbx-output-panel">
                <div class="sbx-col-header-light">📤 Output</div>
                <pre class="sbx-output-content" id="sbx-output-content"><span class="sbx-waiting">No output yet</span></pre>
            </div>
        </div>

        <!-- Files Bar -->
        <div class="sbx-files-section" id="sbx-files-section">
            <div class="sbx-files-header">📁 Generated Files</div>
            <div class="sbx-files-bar" id="sbx-files-bar"></div>
        </div>
    `;

    _updatePipeline();
    _bindReplayButton();
}

/* ── Replay Button ───────────────────────────────────────────────────────── */

function _bindReplayButton() {
    const btn = _container?.querySelector('#sbx-replay-btn');
    if (!btn) return;
    btn.addEventListener('click', () => {
        const runBtn = document.getElementById('btn-run');
        if (runBtn && !runBtn.disabled) runBtn.click();
    });
}

/* ── Pipeline Graph ─────────────────────────────────────────────────────── */

function _updatePipeline() {
    const el = _container?.querySelector('#sbx-pipeline-graph');
    if (!el) return;

    let html = '';
    PIPELINE_NODES.forEach((node, i) => {
        const isActive = _activeNode === node.key;
        const isDone = _doneNodes.has(node.key);
        const cls = isActive ? 'active' : (isDone ? 'done' : '');

        html += `
            <div class="sbx-pipe-node ${cls}">
                <div class="sbx-node-icon" style="background:${node.color}">${node.icon}</div>
                <span class="sbx-node-label">${node.label}</span>
            </div>
        `;
        if (i < PIPELINE_NODES.length - 1) {
            html += `
                <div class="sbx-pipe-edge">
                    <span class="sbx-edge-arrow">──────→</span>
                </div>
            `;
        }
    });
    el.innerHTML = html;
}

/* ── Status Bar ─────────────────────────────────────────────────────────── */

function _updateStatusBar() {
    const badge = _container?.querySelector('#sbx-badge');
    if (badge) {
        const map = {
            idle:     ['IDLE', 'badge-idle'],
            running:  ['RUNNING', 'badge-running'],
            complete: ['COMPLETE', 'badge-complete'],
            error:    ['ERROR', 'badge-error'],
        };
        const [text, cls] = map[_state] || map.idle;
        badge.className = `sbx-badge ${cls}`;
        badge.textContent = text;
    }
}

function _updateElapsed() {
    if (!_startTime) return;
    const el = _container?.querySelector('#sbx-elapsed');
    if (!el) return;
    const s = (Date.now() - _startTime) / 1000;
    const mins = Math.floor(s / 60);
    const secs = Math.floor(s % 60);
    const ms = Math.floor((s % 1) * 10);
    el.textContent = `${String(mins).padStart(2, '0')}:${String(secs).padStart(2, '0')}.${ms}`;
}

/* ── Scenario Card ──────────────────────────────────────────────────────── */

function _updateScenarioCard(text) {
    const card = _container?.querySelector('#sbx-scenario-card');
    if (!card) return;
    const icon = _state === 'complete' ? '✅' : _state === 'error' ? '❌' : '⚡';
    card.innerHTML = `
        <span class="sbx-scenario-icon">${icon}</span>
        <span class="sbx-scenario-text">${text || ''}</span>
    `;
}

/* ── Code Panel ─────────────────────────────────────────────────────────── */

function _updateCodePanel() {
    const el = _container?.querySelector('#sbx-code-content');
    if (!el) return;
    el.innerHTML = _highlightPython(_code);
    _updateScenarioCard(`Executing <strong>${_esc(_filename)}</strong>…`);
}

/* ── Output Panel ───────────────────────────────────────────────────────── */

function _updateOutputPanel() {
    const el = _container?.querySelector('#sbx-output-content');
    if (!el) return;

    let html = '';
    if (_stdout) {
        html += `<div class="sbx-output-stdout">${_esc(_stdout)}</div>`;
    }
    if (_stderr) {
        html += `<div class="sbx-output-stderr">${_esc(_stderr)}</div>`;
    }
    if (!_stdout && !_stderr) {
        html = '<span class="sbx-waiting">No output</span>';
    }

    el.innerHTML = html;

    if (_state === 'complete') {
        const dur = _duration ? ` in ${_duration}s` : '';
        _updateScenarioCard(`✅ <strong>${_esc(_filename)}</strong> completed successfully${dur}`);
    } else if (_state === 'error') {
        _updateScenarioCard(`❌ <strong>${_esc(_filename)}</strong> failed`);
    }
}

/* ── Files Bar ──────────────────────────────────────────────────────────── */

function _updateFilesBar() {
    const el = _container?.querySelector('#sbx-files-bar');
    if (!el) return;
    if (!_files.length) {
        el.innerHTML = '<span class="sbx-no-files">No files generated</span>';
        return;
    }
    el.innerHTML = _files.map(f =>
        `<span class="sbx-file-tag" data-filename="${_esc(f)}">${_esc(f)}</span>`
    ).join('');
}

function _markFileDownloaded(remotePath) {
    const filename = remotePath ? remotePath.split('/').pop() : '';
    if (!filename) return;
    const tags = _container?.querySelectorAll('.sbx-file-tag');
    tags?.forEach(tag => {
        if (tag.dataset.filename === filename) {
            tag.classList.add('downloaded');
            tag.textContent = `✅ ${filename}`;
        }
    });
}

/* ── Utilities ──────────────────────────────────────────────────────────── */

function _esc(str) {
    const div = document.createElement('div');
    div.textContent = str || '';
    return div.innerHTML;
}

function _highlightPython(code) {
    let escaped = _esc(code);
    const keywords = ['import', 'from', 'def', 'class', 'return', 'if', 'else', 'elif',
        'for', 'while', 'in', 'not', 'and', 'or', 'try', 'except', 'finally',
        'with', 'as', 'yield', 'raise', 'pass', 'break', 'continue', 'True',
        'False', 'None', 'async', 'await', 'print', 'lambda'];

    // Strings
    escaped = escaped.replace(/(&#39;.*?&#39;|&quot;.*?&quot;|'.*?'|".*?")/g,
        '<span style="color:#ce9178">$1</span>');
    // Comments
    escaped = escaped.replace(/(#.*?)$/gm,
        '<span style="color:#6a9955">$1</span>');
    // Keywords
    keywords.forEach(kw => {
        escaped = escaped.replace(new RegExp(`\\b(${kw})\\b`, 'g'),
            '<span style="color:#569cd6">$1</span>');
    });
    // Numbers
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g,
        '<span style="color:#b5cea8">$1</span>');

    return escaped;
}

/* ── Styles (self-contained like learning view) ─────────────────────────── */

function _injectStyles() {
    if (document.getElementById('sbx-styles')) return;
    const style = document.createElement('style');
    style.id = 'sbx-styles';
    style.textContent = `
/* ── Pipeline Graph ─────────────────────────────────────────────────── */
.sbx-pipeline-graph {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 0;
    padding: 14px 12px;
    margin-bottom: 8px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
}
.sbx-pipe-node {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    min-width: 70px;
}
.sbx-node-icon {
    width: 36px; height: 36px;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 16px;
    opacity: 0.35;
    transition: opacity 0.3s, box-shadow 0.3s;
}
.sbx-pipe-node.active .sbx-node-icon {
    opacity: 1;
    box-shadow: 0 0 0 4px rgba(255,255,255,0.2), 0 0 12px rgba(0,0,0,0.15);
    animation: sbxPulse 1.5s ease-in-out infinite;
}
.sbx-pipe-node.done .sbx-node-icon { opacity: 1; }
.sbx-node-label {
    font-size: 10px; font-weight: 600;
    color: var(--text-secondary);
    text-transform: uppercase;
    letter-spacing: 0.3px;
}
.sbx-pipe-edge {
    display: flex; align-items: center;
    margin: 0 6px;
}
.sbx-edge-arrow {
    font-family: var(--font-mono);
    font-size: 11px;
    color: var(--border);
    letter-spacing: -1px;
}
@keyframes sbxPulse {
    0%, 100% { box-shadow: 0 0 0 4px rgba(255,255,255,0.2), 0 0 12px rgba(0,0,0,0.1); }
    50%      { box-shadow: 0 0 0 6px rgba(255,255,255,0.3), 0 0 18px rgba(0,0,0,0.2); }
}

/* ── Status Bar ─────────────────────────────────────────────────────── */
.sbx-status-bar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 14px;
    background: var(--bg-secondary);
    border: 1px solid var(--border);
    border-radius: 6px;
    margin-bottom: 8px;
}
.sbx-status-left { display: flex; align-items: center; gap: 8px; }
.sbx-status-label {
    font-size: 12px; font-weight: 600;
    color: var(--text-primary);
}
.sbx-status-right { display: flex; align-items: center; gap: 10px; }
.sbx-replay-btn {
    padding: 4px 14px;
    font-size: 11px; font-weight: 700;
    border: 1px solid var(--border);
    border-radius: 6px;
    background: var(--bg-card);
    color: var(--text-primary);
    cursor: pointer;
    transition: all 0.15s;
}
.sbx-replay-btn:hover { background: var(--bg-hover); border-color: #6366f1; color: #6366f1; }
.sbx-badge {
    font-size: 10px; font-weight: 700;
    padding: 2px 10px;
    border-radius: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}
.sbx-badge.badge-idle     { background: #e5e7eb; color: #6b7280; }
.sbx-badge.badge-running  { background: #dbeafe; color: #2563eb; }
.sbx-badge.badge-complete { background: #d1fae5; color: #047857; }
.sbx-badge.badge-error    { background: #fee2e2; color: #dc2626; }
.sbx-elapsed {
    font-family: var(--font-mono); font-size: 12px;
    color: var(--text-secondary);
}

/* ── Scenario Card ──────────────────────────────────────────────────── */
.sbx-scenario-card {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 10px 14px;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-left: 3px solid #6366f1;
    border-radius: 8px;
    margin-bottom: 10px;
}
.sbx-scenario-icon { font-size: 16px; }
.sbx-scenario-text {
    flex: 1;
    font-size: 13px; font-weight: 600;
    color: var(--text-primary);
}

/* ── Two-Column Layout ──────────────────────────────────────────────── */
.sbx-two-col {
    display: flex;
    gap: 10px;
    margin-bottom: 10px;
    min-height: 280px;
    max-height: calc(100vh - 380px);
}
.sbx-col-left {
    flex: 0 0 50%;
    background: #1e1e1e;
    border-radius: 8px;
    padding: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
.sbx-col-right {
    flex: 1;
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 0;
    overflow: hidden;
    display: flex;
    flex-direction: column;
}
.sbx-col-header {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #64748b;
    padding: 10px 14px 6px;
    border-bottom: 1px solid rgba(255,255,255,0.08);
    background: #1e1e1e;
}
.sbx-col-header-light {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-secondary);
    padding: 10px 14px 6px;
    border-bottom: 1px solid var(--border);
}
.sbx-code-content {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    color: #d4d4d4;
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
    background: #1e1e1e;
}
.sbx-output-content {
    flex: 1;
    overflow-y: auto;
    padding: 12px 14px;
    font-family: var(--font-mono);
    font-size: 12px;
    line-height: 1.6;
    color: var(--text-primary);
    white-space: pre-wrap;
    word-break: break-word;
    margin: 0;
}
.sbx-output-stdout {
    color: var(--text-primary);
    margin-bottom: 8px;
}
.sbx-output-stderr {
    color: #ef4444;
    border-top: 1px solid var(--border);
    padding-top: 8px;
    margin-top: 4px;
}
.sbx-waiting {
    color: #64748b;
    font-style: italic;
}

/* ── Files Section ──────────────────────────────────────────────────── */
.sbx-files-section {
    background: var(--bg-card);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 10px 14px;
}
.sbx-files-header {
    font-size: 11px; font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: var(--text-secondary);
    margin-bottom: 8px;
}
.sbx-files-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
    min-height: 24px;
}
.sbx-file-tag {
    display: inline-block;
    background: #dbeafe;
    color: #2563eb;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-family: var(--font-mono);
    font-weight: 600;
}
.sbx-file-tag.downloaded {
    background: #d1fae5;
    color: #059669;
}
.sbx-no-files {
    font-size: 12px;
    color: var(--text-muted);
    font-style: italic;
}
`;
    document.head.appendChild(style);
}
