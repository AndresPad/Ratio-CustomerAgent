/**
 * SSE Client for Sandbox Code Execution.
 *
 * Connects to POST /api/sandbox/run and dispatches sandbox events
 * through the global event bus (window.dispatchEvent).
 *
 * Each event is dispatched as:
 *   new CustomEvent('agent-event', { detail: { type, ...data } })
 *
 * Lifecycle events:
 *   'sandbox-connected'  — stream opened
 *   'sandbox-done'       — stream ended normally
 *   'sandbox-error'      — stream error / disconnect
 */

/** @type {AbortController|null} */
let _controller = null;

/**
 * Start a sandbox SSE stream.
 * @param {Object} params — optional parameters (code, scenario, etc.)
 */
export async function startSandbox(params = {}) {
    if (_controller) _controller.abort();
    _controller = new AbortController();

    window.dispatchEvent(new CustomEvent('sandbox-connected'));

    try {
        const resp = await fetch('/api/sandbox/run', {
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

            const lines = buffer.split('\n');
            buffer = lines.pop(); // keep incomplete line in buffer

            for (const line of lines) {
                const trimmed = line.trim();
                if (!trimmed || !trimmed.startsWith('data: ')) continue;

                const payload = trimmed.slice(6);

                if (payload === '[DONE]') {
                    window.dispatchEvent(new CustomEvent('sandbox-done'));
                    _controller = null;
                    return;
                }

                try {
                    const event = JSON.parse(payload);
                    window.dispatchEvent(
                        new CustomEvent('agent-event', { detail: event })
                    );
                } catch (parseErr) {
                    console.warn('Sandbox SSE parse error:', parseErr, 'payload:', payload);
                }
            }
        }

        window.dispatchEvent(new CustomEvent('sandbox-done'));
    } catch (err) {
        if (err.name === 'AbortError') {
            window.dispatchEvent(new CustomEvent('sandbox-done'));
        } else {
            console.error('Sandbox SSE error:', err);
            window.dispatchEvent(
                new CustomEvent('sandbox-error', { detail: { error: err.message } })
            );
        }
    } finally {
        _controller = null;
    }
}

/**
 * Stop the active sandbox stream.
 */
export function stopSandbox() {
    if (_controller) {
        _controller.abort();
        _controller = null;
    }
}
