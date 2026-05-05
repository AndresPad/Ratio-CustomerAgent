/**
 * Sidebar Component
 *
 * Handles view tab switching and exposes helpers for updating
 * the stats panel and narration panel.
 */

let _narrationCount = 0;
let _hasReceivedNarration = false; // flips true after first narrator chunk

/**
 * Initialize sidebar view tab switching.
 * Clicking a tab shows the corresponding view and hides others.
 */
export function initSidebar() {
    const tabs = document.querySelectorAll('.view-tab');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            // Update active tab
            tabs.forEach(t => t.classList.remove('active'));
            tab.classList.add('active');

            // Deactivate config nav items
            document.querySelectorAll('.config-nav-item').forEach(b => b.classList.remove('active'));

            // Show corresponding view
            const viewId = tab.dataset.view;
            document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
            const target = document.getElementById(`view-${viewId}`);
            if (target) target.classList.add('active');

            // Restore stats bar visibility
            const statsBar = document.getElementById('stats-bar');
            if (statsBar) statsBar.style.display = '';
        });
    });

    // Initialize context panel toggle (collapse/expand)
    document.querySelectorAll('.ctx-header').forEach(header => {
        header.addEventListener('click', () => {
            const bodyId = header.dataset.toggle;
            const body = document.getElementById(bodyId);
            if (body) body.classList.toggle('collapsed');
        });
    });

    // Initialize sidebar section toggles (Configuration collapse/expand)
    document.querySelectorAll('.sidebar-section-toggle').forEach(toggle => {
        toggle.addEventListener('click', () => {
            const targetId = toggle.dataset.toggle;
            const target = document.getElementById(targetId);
            if (target) {
                target.classList.toggle('hidden');
                toggle.classList.toggle('collapsed');
            }
        });
    });

    // Initialize sidebar collapse toggle
    const collapseBtn = document.getElementById('sidebar-collapse');
    if (collapseBtn) {
        collapseBtn.addEventListener('click', () => {
            const sidebar = document.getElementById('sidebar');
            if (!sidebar) return;
            sidebar.classList.toggle('collapsed');
            collapseBtn.textContent = sidebar.classList.contains('collapsed') ? '▶' : '◀';
            collapseBtn.title = sidebar.classList.contains('collapsed') ? 'Expand sidebar' : 'Collapse sidebar';
        });
    }

    // Initialize narration panel toggle
    const narrationToggle = document.getElementById('narration-toggle');
    if (narrationToggle) {
        narrationToggle.addEventListener('click', () => {
            const body = document.getElementById('narration-body');
            const chevron = document.getElementById('narration-chevron');
            if (body) body.classList.toggle('collapsed');
            if (chevron) chevron.classList.toggle('collapsed');
        });
    }

}

/**
 * Update a single stat value in the stats panel.
 * @param {string} key — stat key (events, signals, compounds, etc.)
 * @param {number|string} value
 */
export function updateStat(key, value) {
    const el = document.getElementById(`stat-${key}`);
    if (el) el.textContent = value;
}

/**
 * Reset all stats to zero.
 */
export function resetStats() {
    const keys = ['events', 'signals', 'compounds', 'symptoms', 'hypotheses', 'evidence', 'actions'];
    keys.forEach(k => updateStat(k, 0));
    resetNarration();
}

let _currentNarratorAgent = null; // tracks current stage label

/**
 * Handle a narrator streaming event.
 * - "investigation_narrator_chunk" — append text tokens to the stream
 * - "investigation_narrator_done"  — finish the current segment
 * @param {Object} event — SSE event
 */
export function addNarration(event) {
    const stream = document.getElementById('narration-stream');
    const empty = document.getElementById('narration-empty');
    const cursor = document.getElementById('narration-cursor');
    if (!stream) return;

    if (empty) empty.style.display = 'none';
    if (cursor) cursor.classList.remove('hidden');

    const agent = event.narrated_agent || event.phase || '?';

    const thinking = document.getElementById('narration-thinking');

    if (event.type === 'investigation_milestone') {
        // Skip milestone rendering to keep the panel compact;
        // just ensure thinking/waiting indicator is visible.
        if (thinking) {
            thinking.textContent = _hasReceivedNarration ? 'Thinking\u2026' : 'Waiting\u2026';
            thinking.classList.remove('hidden');
        }
        return;
    }

    if (event.type === 'investigation_narrator_chunk') {
        // First narrator output — switch label from Waiting to Thinking
        _hasReceivedNarration = true;
        // Hide thinking indicator — narration is arriving
        if (thinking) thinking.classList.add('hidden');

        // New stage? Insert label
        if (agent !== _currentNarratorAgent) {
            // Space between segments
            if (_currentNarratorAgent !== null) {
                stream.appendChild(document.createTextNode(' '));
            }
            const label = document.createElement('span');
            label.className = 'narration-stage';
            label.textContent = `[${agent}] `;
            stream.appendChild(label);
            _currentNarratorAgent = agent;
        }

        // Append the chunk text directly — real LLM streaming
        const textNode = document.createTextNode(event.text || '');
        stream.appendChild(textNode);

        // Auto-scroll
        const body = document.getElementById('narration-body');
        if (body) body.scrollTop = body.scrollHeight;

    } else if (event.type === 'investigation_narrator_done') {
        // Segment finished
        _narrationCount++;
        const countEl = document.getElementById('narration-count');
        if (countEl) countEl.textContent = _narrationCount;
        _currentNarratorAgent = null;
        if (cursor) cursor.classList.add('hidden');
        // Show thinking indicator until next narration arrives
        if (thinking) {
            thinking.textContent = 'Thinking\u2026';
            thinking.classList.remove('hidden');
        }
        // Auto-scroll so thinking indicator is visible
        const body = document.getElementById('narration-body');
        if (body) body.scrollTop = body.scrollHeight;
    }
}

/**
 * Reset narration panel to empty state.
 */
export function resetNarration() {
    _narrationCount = 0;
    _currentNarratorAgent = null;
    _hasReceivedNarration = false;
    const stream = document.getElementById('narration-stream');
    const empty = document.getElementById('narration-empty');
    const countEl = document.getElementById('narration-count');
    const cursor = document.getElementById('narration-cursor');
    const thinking = document.getElementById('narration-thinking');
    if (stream) stream.innerHTML = '';
    if (empty) empty.style.display = '';
    if (countEl) countEl.textContent = '0';
    if (cursor) cursor.classList.add('hidden');
    if (thinking) thinking.classList.add('hidden');
}

/**
 * Hide the narration thinking indicator (e.g. on pipeline complete).
 */
export function hideNarrationThinking() {
    const thinking = document.getElementById('narration-thinking');
    if (thinking) thinking.classList.add('hidden');
}
