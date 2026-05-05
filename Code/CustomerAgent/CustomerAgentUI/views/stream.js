/**
 * Stream View
 *
 * Default view with a split layout:
 *   Top half:  Evaluation Progress (narrator) | Orchestration Graph (animated flow)
 *   Bottom half: chronological feed of color-coded event cards.
 *
 * Uses the backend `seq` / `created_at` for correct chronological ordering.
 */

import { renderCard } from '/components/cards.js?v=2';
import { initOrchestration, handleOrchEvent, resetOrchestration } from '/views/orchestration.js';

let _container = null;       // #view-stream
let _cardsContainer = null;  // #sv-cards (bottom half)
let _autoScroll = true;

/* ── Narrator state (stream-view instance) ─────────────────────────────── */
let _narratorStream = null;
let _narratorBody = null;
let _narratorEmpty = null;
let _narratorCursor = null;
let _narratorThinking = null;
let _narratorCountEl = null;
let _currentNarratorAgent = null;
let _narrationCount = 0;
let _hasReceivedNarration = false;

/**
 * Initialize the stream view with split layout.
 */
export function initStreamView() {
    _container = document.getElementById('view-stream');
    if (!_container) return;

    _container.innerHTML = `
        <div class="stream-top">
            <div class="stream-narrator">
                <div class="stream-narrator-hdr">
                    <span>Evaluation Progress</span>
                    <span class="narration-count" id="sv-narr-count">0</span>
                </div>
                <div class="stream-narrator-body" id="sv-narr-body">
                    <div class="narration-empty" id="sv-narr-empty">Run Investigation to see the progress</div>
                    <div class="narration-stream" id="sv-narr-stream"></div>
                    <span class="narration-cursor hidden" id="sv-narr-cursor"></span>
                    <div class="narration-thinking hidden" id="sv-narr-thinking">Waiting…</div>
                </div>
            </div>
            <div class="stream-orch" id="sv-orchestration"></div>
        </div>
        <div class="stream-bottom" id="sv-cards">
            <div class="empty-state">
                <div class="icon">📡</div>
                <div>Press <strong>▶ Investigate</strong> to begin</div>
            </div>
        </div>
    `;

    _cardsContainer = document.getElementById('sv-cards');
    _narratorStream = document.getElementById('sv-narr-stream');
    _narratorBody = document.getElementById('sv-narr-body');
    _narratorEmpty = document.getElementById('sv-narr-empty');
    _narratorCursor = document.getElementById('sv-narr-cursor');
    _narratorThinking = document.getElementById('sv-narr-thinking');
    _narratorCountEl = document.getElementById('sv-narr-count');

    // Initialize orchestration graph
    initOrchestration(document.getElementById('sv-orchestration'));

    // Pause auto-scroll when user scrolls up in cards section
    _cardsContainer.addEventListener('scroll', () => {
        const atBottom = _cardsContainer.scrollHeight - _cardsContainer.scrollTop - _cardsContainer.clientHeight < 50;
        _autoScroll = atBottom;
    });
}

/**
 * Add an event card to the stream cards in correct chronological order.
 *
 * Uses `created_at` as the primary sort key, `seq` for tie-breaking.
 * @param {Object} event — parsed SSE event
 */
export function addStreamEvent(event) {
    if (!_cardsContainer) return;

    // Remove empty state on first event
    const empty = _cardsContainer.querySelector('.empty-state');
    if (empty) empty.remove();

    // Render the card
    const html = renderCard(event);
    if (!html) return;  // Suppressed event (e.g. sandbox_execution_started)
    const wrapper = document.createElement('div');
    wrapper.innerHTML = html;
    const card = wrapper.firstElementChild;
    if (!card) return;  // Safety: empty/unparseable HTML

    // Stamp ordering data onto the DOM element for future comparisons
    const seq = event.seq ?? null;
    const createdAt = event.created_at ?? 0;
    card.dataset.seq = seq ?? '';
    card.dataset.createdAt = createdAt;

    // Find the correct insertion point (walk backwards — most arrivals
    // are already in order so we almost always stop immediately).
    const children = _cardsContainer.children;
    let insertBefore = null;

    for (let i = children.length - 1; i >= 0; i--) {
        const child = children[i];
        const childCreatedAt = Number(child.dataset.createdAt || 0);
        const childSeq = child.dataset.seq ? Number(child.dataset.seq) : null;

        // Primary: compare by created_at (true occurrence time)
        if (createdAt > 0 && childCreatedAt > 0) {
            if (childCreatedAt > createdAt) {
                insertBefore = child;
                continue; // keep walking back
            } else if (childCreatedAt < createdAt) {
                break; // child is older, insert after it
            }
            // Same created_at — tie-break by seq
            if (seq !== null && childSeq !== null) {
                if (childSeq > seq) {
                    insertBefore = child;
                } else {
                    break;
                }
            } else {
                break; // no seq to break tie, insert after
            }
        } else if (seq !== null && childSeq !== null) {
            // Fallback: compare by seq when created_at is missing
            if (childSeq > seq) {
                insertBefore = child;
            } else {
                break;
            }
        } else {
            break; // no ordering info, append
        }
    }

    if (insertBefore) {
        _cardsContainer.insertBefore(card, insertBefore);
    } else {
        _cardsContainer.appendChild(card);
    }

    // Auto-scroll to bottom
    if (_autoScroll) {
        _cardsContainer.scrollTop = _cardsContainer.scrollHeight;
    }
}

/**
 * Handle a narrator event for the stream view's Evaluation Progress panel.
 * Mirrors the sidebar narration logic but targets the stream-local elements.
 * @param {Object} event — narrator SSE event
 */
export function addStreamNarration(event) {
    if (!_narratorStream) return;

    if (_narratorEmpty) _narratorEmpty.style.display = 'none';
    if (_narratorCursor) _narratorCursor.classList.remove('hidden');

    const agent = event.narrated_agent || event.phase || '?';

    if (event.type === 'investigation_milestone') {
        if (_narratorThinking) {
            _narratorThinking.textContent = _hasReceivedNarration ? 'Thinking\u2026' : 'Waiting\u2026';
            _narratorThinking.classList.remove('hidden');
        }
        return;
    }

    if (event.type === 'investigation_narrator_chunk') {
        _hasReceivedNarration = true;
        if (_narratorThinking) _narratorThinking.classList.add('hidden');

        if (agent !== _currentNarratorAgent) {
            if (_currentNarratorAgent !== null) {
                _narratorStream.appendChild(document.createTextNode(' '));
            }
            const label = document.createElement('span');
            label.className = 'narration-stage';
            label.textContent = `[${agent}] `;
            _narratorStream.appendChild(label);
            _currentNarratorAgent = agent;
        }

        _narratorStream.appendChild(document.createTextNode(event.text || ''));
        if (_narratorBody) _narratorBody.scrollTop = _narratorBody.scrollHeight;

    } else if (event.type === 'investigation_narrator_done') {
        _narrationCount++;
        if (_narratorCountEl) _narratorCountEl.textContent = _narrationCount;
        _currentNarratorAgent = null;
        if (_narratorCursor) _narratorCursor.classList.add('hidden');
        if (_narratorThinking) {
            _narratorThinking.textContent = 'Thinking\u2026';
            _narratorThinking.classList.remove('hidden');
        }
        if (_narratorBody) _narratorBody.scrollTop = _narratorBody.scrollHeight;
    }
}

/**
 * Forward an event to the orchestration graph for node activation.
 * @param {Object} event
 */
export function updateStreamOrchestration(event) {
    handleOrchEvent(event);
}

/**
 * Hide the stream view's narrator thinking indicator (e.g. on pipeline complete).
 */
export function hideStreamNarrationThinking() {
    if (_narratorThinking) _narratorThinking.classList.add('hidden');
}

/**
 * Clear the stream view (cards + narrator + orchestration).
 */
export function clearStream() {
    // Reset cards
    if (_cardsContainer) {
        _cardsContainer.innerHTML = `
            <div class="empty-state">
                <div class="icon">📡</div>
                <div>Press <strong>▶ Investigate</strong> to begin</div>
            </div>
        `;
    }
    _autoScroll = true;

    // Reset narrator
    _narrationCount = 0;
    _currentNarratorAgent = null;
    _hasReceivedNarration = false;
    if (_narratorStream) _narratorStream.innerHTML = '';
    if (_narratorEmpty) _narratorEmpty.style.display = '';
    if (_narratorCountEl) _narratorCountEl.textContent = '0';
    if (_narratorCursor) _narratorCursor.classList.add('hidden');
    if (_narratorThinking) _narratorThinking.classList.add('hidden');

    // Reset orchestration graph
    resetOrchestration();
}
