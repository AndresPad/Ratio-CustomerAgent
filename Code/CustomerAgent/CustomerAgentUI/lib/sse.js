/**
 * SSE Client for the CustomerAgent pipeline.
 *
 * Connects to POST /api/run and dispatches every server-sent event
 * through the global event bus (window.dispatchEvent).
 *
 * Usage:
 *   import { startPipeline, stopPipeline } from '/lib/sse.js';
 *   startPipeline({ customer_name: 'Contoso' });
 *
 * Each event is dispatched as:
 *   new CustomEvent('agent-event', { detail: { type, timestamp, ...data } })
 *
 * Special lifecycle events:
 *   'sse-connected'   — stream opened
 *   'sse-done'        — stream ended normally
 *   'sse-error'       — stream error / disconnect
 */

/** @type {AbortController|null} Active connection controller */
let _controller = null;

/**
 * Start the pipeline SSE stream.
 *
 * @param {Object} params
 * @param {string} [params.customer_name]   — optional customer filter
 * @param {string} [params.service_tree_id] — optional service tree ID
 * @param {string} [params.start_time]      — optional ISO8601 UTC start time
 * @param {string} [params.end_time]        — optional ISO8601 UTC end time
 * @returns {Promise<void>} resolves when stream ends
 */
export async function startPipeline(params = {}) {
    // Abort any existing stream
    if (_controller) {
        _controller.abort();
    }
    _controller = new AbortController();

    const body = {};
    if (params.customer_name) body.customer_name = params.customer_name;
    if (params.service_tree_id) body.service_tree_id = params.service_tree_id;
    if (params.start_time) body.start_time = params.start_time;
    if (params.end_time) body.end_time = params.end_time;

    window.dispatchEvent(new CustomEvent('sse-connected'));

    try {
        const resp = await fetch('/api/run', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
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

                const payload = trimmed.slice(6); // strip "data: "

                // [DONE] sentinel marks stream end
                if (payload === '[DONE]') {
                    window.dispatchEvent(new CustomEvent('sse-done'));
                    _controller = null;
                    return;
                }

                try {
                    const event = JSON.parse(payload);
                    window.dispatchEvent(
                        new CustomEvent('agent-event', { detail: event })
                    );
                } catch (parseErr) {
                    console.warn('SSE parse error:', parseErr, 'payload:', payload);
                }
            }
        }

        // Stream ended without [DONE]
        window.dispatchEvent(new CustomEvent('sse-done'));
    } catch (err) {
        if (err.name === 'AbortError') {
            // User-initiated stop — not an error
            window.dispatchEvent(new CustomEvent('sse-done'));
        } else {
            console.error('SSE stream error:', err);
            window.dispatchEvent(
                new CustomEvent('sse-error', { detail: { error: err.message } })
            );
        }
    } finally {
        _controller = null;
    }
}

/**
 * Stop the active pipeline stream.
 */
export function stopPipeline() {
    if (_controller) {
        _controller.abort();
        _controller = null;
    }
}

/**
 * Check if a pipeline is currently running.
 * @returns {boolean}
 */
export function isRunning() {
    return _controller !== null;
}
