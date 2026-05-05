/**
 * SSE Client for Investigation Learning Demo.
 *
 * Connects to POST /api/learning/start and dispatches every server-sent
 * event through the global event bus (window.dispatchEvent).
 *
 * Usage:
 *   import { startLearning, stopLearning } from '/lib/learning-sse.js';
 *   startLearning({});
 *
 * Each event is dispatched as:
 *   new CustomEvent('agent-event', { detail: { type, ...data } })
 *
 * Special lifecycle events:
 *   'learning-connected'  — stream opened
 *   'learning-done'       — stream ended normally
 *   'learning-error'      — stream error / disconnect
 */

/** @type {AbortController|null} Active connection controller */
let _controller = null;

/**
 * Start a learning SSE stream.
 *
 * @param {Object} params — optional parameters for the learning run
 * @returns {Promise<void>} resolves when stream ends
 */
export async function startLearning(params = {}) {
    // Abort any existing stream
    if (_controller) {
        _controller.abort();
    }
    _controller = new AbortController();

    window.dispatchEvent(new CustomEvent('learning-connected'));

    try {
        const resp = await fetch('/api/learning/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(params),
            signal: _controller.signal,
        });

        if (!resp.ok) {
            const errorText = await resp.text();
            throw new Error(`HTTP ${resp.status}: ${errorText}`);
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });

            // Parse SSE frames: each line starts with "data: "
            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete line in buffer

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || !trimmed.startsWith('data: ')) continue;

                const payload = trimmed.slice(6);

                if (payload === '[DONE]') {
                    window.dispatchEvent(new CustomEvent('learning-done'));
                    _controller = null;
                    return;
                }

                try {
                    const event = JSON.parse(payload);
                    window.dispatchEvent(
                        new CustomEvent('agent-event', { detail: event })
                    );
                } catch (parseErr) {
                    console.warn('Learning SSE parse error:', parseErr, 'payload:', payload);
                }
            }
        }

        // Stream ended without [DONE]
        window.dispatchEvent(new CustomEvent('learning-done'));
    } catch (err) {
        if (err.name === 'AbortError') {
            window.dispatchEvent(new CustomEvent('learning-done'));
        } else {
            console.error('Learning SSE error:', err);
            window.dispatchEvent(
                new CustomEvent('learning-error', { detail: { error: err.message } })
            );
        }
    } finally {
        _controller = null;
    }
}

/**
 * Stop the active learning stream.
 */
export function stopLearning() {
    if (_controller) {
        _controller.abort();
        _controller = null;
    }
}

/**
 * Check if learning is currently running.
 * @returns {boolean}
 */
export function isLearning() {
    return _controller !== null;
}
