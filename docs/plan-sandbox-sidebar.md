# Plan: Code Sandbox Sidebar for CustomerAgentUI Stream View

## 1. Overview

Add a **Code Sandbox sidebar** to the Stream view in the **CustomerAgentUI** (`CustomerAgentUI/`). The CustomerAgentUI is a vanilla JavaScript SPA (no React/TypeScript) served by `server.py` on port 8020. It already has a dedicated Sandbox *tab view* (`views/sandbox.js`) that shows code execution results when the user navigates to the 🔬 Sandbox tab. This plan adds a **persistent sidebar** visible alongside the Stream view so operators can see sandbox activity in real-time without leaving the stream.

### Current Architecture

The CustomerAgentUI uses:
- **Vanilla JS** (ES modules, no build step, no framework)
- **CSS variables** defined in `styles.css` (e.g., `--bg-card`, `--border`, `--text-primary`, `--color-signal`)
- **3-column flex layout**: `#sidebar` (left) | `#center-panel` (center, views) | `#context-panels` (right)
- **SSE event bus**: `window.dispatchEvent(new CustomEvent('agent-event', { detail: event }))` routed in `app.js`
- **Existing sandbox events**: `sandbox_code_generated`, `sandbox_execution_started`, `sandbox_execution_complete`, `sandbox_error` — already emitted by the backend and routed to `addSandboxEvent()` in `app.js`

### What This Plan Adds

A new **sandbox sidebar panel** between the `#center-panel` and `#context-panels`, visible only when the Stream view is active. It captures every sandbox event into a tabbed interface:
- **Header** with "Sandbox Calls: N" counter
- **Tab strip** — one tab per sandbox execution (code → output pair)
- **Code container** — shows the code sent to the sandbox
- **Output container** — shows stdout/stderr returned

---

## 2. UI Layout Changes

### Current Layout

```
#main-layout (flex row)
├── #sidebar (240px)        — Run controls, view tabs, narration
├── #center-panel (flex: 1) — Stats bar + active view (stream/graph/agentflow/timeline/learning/sandbox)
└── #context-panels (280px) — Signals, Symptoms, Hypotheses, Evidence, Actions
```

### New Layout (when Stream view is active and sandbox events exist)

```
#main-layout (flex row)
├── #sidebar (240px)             — Unchanged
├── #center-panel (flex: 1)      — Unchanged (stream cards)
├── #sandbox-sidebar (360px)     — NEW: tabbed sandbox code/output
└── #context-panels (280px)      — Unchanged
```

The `#sandbox-sidebar` element is inserted into `index.html` between `#center-panel` and `#context-panels`. It is hidden by default (`display: none`) and shown only when:
1. The active view is `stream`
2. At least one `sandbox_*` event has been received

### Collapsibility

A toggle button in the sidebar header collapses it to a 40px strip showing a vertical "🔬" icon and the call count badge. This mirrors the existing sidebar collapse pattern.

---

## 3. Event Types (Already Exist)

**No new event types are needed.** The backend already emits sandbox events via `AgentLogger._emit()` and the SSE pipeline:

| Event Type | When Emitted | Key Fields |
|---|---|---|
| `sandbox_code_generated` | Agent writes code to execute | `code`, `filename` |
| `sandbox_execution_started` | Sandbox begins running | `filename` |
| `sandbox_execution_complete` | Sandbox finishes | `stdout`, `stderr`, `files`, `duration_seconds`, `success` |
| `sandbox_error` | Sandbox fails | `error` |

These events are already routed in `app.js` (line ~160):
```javascript
if (event.type?.startsWith('sandbox_')) {
    addSandboxEvent(event);  // → views/sandbox.js
    addStreamEvent(event);   // → views/stream.js (also shows in stream)
    return;
}
```

The new sidebar will receive these same events via a new `addSandboxSidebarEvent()` call added to this routing block.

---

## 4. New Module: `components/sandbox-sidebar.js`

**File:** `CustomerAgentUI/components/sandbox-sidebar.js` (new)

A self-contained ES module that manages the sandbox sidebar DOM and state.

### Exports

```javascript
export function initSandboxSidebar()        // Called once from app.js on DOMContentLoaded
export function addSandboxSidebarEvent(evt)  // Called from app.js for each sandbox_* event
export function clearSandboxSidebar()        // Called from app.js on pipeline reset
export function showSandboxSidebar()         // Show sidebar (stream view activated)
export function hideSandboxSidebar()         // Hide sidebar (non-stream view activated)
```

### Internal State

```javascript
let _container = null;           // #sandbox-sidebar DOM element
let _executions = [];            // Array of { id, code, filename, stdout, stderr, status, duration }
let _activeTabIndex = 0;         // Currently selected tab
let _userSelectedTab = false;    // True if user manually clicked a tab
let _collapsed = false;          // Sidebar collapsed state
```

### Event Processing

Each sandbox event updates the internal `_executions` array:

- **`sandbox_code_generated`** → Push a new execution entry `{ id: _executions.length + 1, code: event.code, filename: event.filename, status: 'writing', ... }`. Auto-select this tab unless `_userSelectedTab` is true.
- **`sandbox_execution_started`** → Update the latest execution's status to `'running'`.
- **`sandbox_execution_complete`** → Update with `stdout`, `stderr`, `duration_seconds`, set status to `event.success ? 'complete' : 'error'`.
- **`sandbox_error`** → Update with `error` text, set status to `'error'`.

### DOM Structure (rendered by `_render()`)

```html
<div id="sandbox-sidebar" class="sandbox-sidebar">
  <!-- Header -->
  <div class="sbx-sb-header">
    <span class="sbx-sb-title">🔬 Sandbox</span>
    <span class="sbx-sb-counter">0</span>
    <button class="sbx-sb-collapse-btn" title="Toggle sidebar">◀</button>
  </div>

  <!-- Tab strip (horizontal, scrollable) -->
  <div class="sbx-sb-tabs">
    <button class="sbx-sb-tab active">1</button>
    <button class="sbx-sb-tab">2</button>
    ...
  </div>

  <!-- Active tab content -->
  <div class="sbx-sb-content">
    <!-- Metadata bar -->
    <div class="sbx-sb-meta">
      <span class="sbx-sb-filename">agent_script.py</span>
      <span class="sbx-sb-status badge-complete">COMPLETE</span>
      <span class="sbx-sb-duration">2.3s</span>
    </div>

    <!-- Code container -->
    <div class="sbx-sb-code-section">
      <div class="sbx-sb-section-header">
        ✏️ Code
        <button class="sbx-sb-copy-btn" title="Copy code">📋</button>
      </div>
      <pre class="sbx-sb-code"></pre>
    </div>

    <!-- Output container -->
    <div class="sbx-sb-output-section">
      <div class="sbx-sb-section-header">📤 Output</div>
      <pre class="sbx-sb-output"></pre>
    </div>
  </div>
</div>
```

### Tab Behavior

- Each tab button is labeled with the execution number (1, 2, 3…)
- Clicking a tab sets `_activeTabIndex` and `_userSelectedTab = true`
- New executions auto-select the latest tab unless `_userSelectedTab` is true
- `_userSelectedTab` resets to `false` when a new pipeline run starts (`clearSandboxSidebar()`)

---

## 5. HTML Changes

**File:** `CustomerAgentUI/index.html`

Add the `#sandbox-sidebar` element between `</main>` and `<aside id="context-panels">`:

```html
        </main>

        <!-- Sandbox Sidebar (hidden by default, shown during stream view) -->
        <aside id="sandbox-sidebar" class="sandbox-sidebar" style="display:none"></aside>

        <!-- Right: Context Panels (collapsible) -->
        <aside id="context-panels">
```

---

## 6. App.js Integration

**File:** `CustomerAgentUI/app.js`

### 6.1 Import

Add to the import block at the top:

```javascript
import { initSandboxSidebar, addSandboxSidebarEvent, clearSandboxSidebar,
         showSandboxSidebar, hideSandboxSidebar } from '/components/sandbox-sidebar.js';
```

### 6.2 Initialization

Add `initSandboxSidebar()` to the `DOMContentLoaded` handler after `initSandboxView()`.

### 6.3 Event Routing

Update the sandbox event routing block to also feed the sidebar:

```javascript
// Existing:
if (event.type?.startsWith('sandbox_')) {
    addSandboxEvent(event);
    addStreamEvent(event);
    addSandboxSidebarEvent(event);  // ← NEW
    return;
}
```

### 6.4 View Switching

When the view tab changes, show/hide the sandbox sidebar:

```javascript
// In the view tab click handler:
if (activeView === 'stream') {
    showSandboxSidebar();
} else {
    hideSandboxSidebar();
}
```

### 6.5 Reset

Add `clearSandboxSidebar()` to the existing `_resetAll()` function.

---

## 7. Styling

**File:** `CustomerAgentUI/styles.css`

All new styles use the existing CSS variables: `--bg-card`, `--bg-secondary`, `--border`, `--text-primary`, `--text-secondary`, `--font-mono`.

Alternatively, `sandbox-sidebar.js` can inject its own `<style>` tag (matching the pattern used by `sandbox.js` via `_injectStyles()`).

### Sidebar Container

| Class | Purpose |
|-------|---------|
| `.sandbox-sidebar` | `width: 360px; min-width: 360px; border-left: 1px solid var(--border); display: flex; flex-direction: column; overflow-y: auto; background: var(--bg-secondary);` |
| `.sandbox-sidebar.collapsed` | `width: 40px; min-width: 40px; overflow: hidden;` |

### Header

| Class | Purpose |
|-------|---------|
| `.sbx-sb-header` | `display: flex; align-items: center; gap: 8px; padding: 10px 12px; border-bottom: 1px solid var(--border); background: var(--bg-card);` |
| `.sbx-sb-title` | `font-size: 12px; font-weight: 700; color: var(--text-primary);` |
| `.sbx-sb-counter` | Badge — `font-size: 10px; font-weight: 700; background: #6366f1; color: #fff; padding: 1px 8px; border-radius: 10px;` |
| `.sbx-sb-collapse-btn` | `margin-left: auto; background: none; border: none; cursor: pointer; font-size: 12px; color: var(--text-secondary);` |

### Tab Strip

| Class | Purpose |
|-------|---------|
| `.sbx-sb-tabs` | `display: flex; gap: 2px; padding: 6px 8px; overflow-x: auto; border-bottom: 1px solid var(--border); background: var(--bg-secondary);` |
| `.sbx-sb-tab` | `padding: 4px 12px; font-size: 11px; font-weight: 600; border: 1px solid var(--border); border-radius: 4px; background: var(--bg-card); cursor: pointer; color: var(--text-secondary);` |
| `.sbx-sb-tab.active` | `background: #6366f1; color: #fff; border-color: #6366f1;` |
| `.sbx-sb-tab.error` | `border-color: #dc2626; color: #dc2626;` |

### Content Area

| Class | Purpose |
|-------|---------|
| `.sbx-sb-content` | `flex: 1; overflow-y: auto; padding: 8px;` |
| `.sbx-sb-meta` | `display: flex; align-items: center; gap: 8px; padding: 6px 8px; margin-bottom: 8px; font-size: 11px;` |
| `.sbx-sb-filename` | `font-family: var(--font-mono); font-weight: 600; color: var(--text-primary);` |
| `.sbx-sb-status` | Badge using existing `badge-*` classes from styles.css |
| `.sbx-sb-duration` | `font-family: var(--font-mono); color: var(--text-secondary);` |

### Code Block

| Class | Purpose |
|-------|---------|
| `.sbx-sb-code-section` | `margin-bottom: 8px;` |
| `.sbx-sb-section-header` | `font-size: 11px; font-weight: 700; padding: 6px 8px; background: var(--bg-secondary); border: 1px solid var(--border); border-bottom: none; border-radius: 6px 6px 0 0; display: flex; align-items: center; justify-content: space-between;` |
| `.sbx-sb-code` | `background: #1e1e1e; color: #d4d4d4; font-family: var(--font-mono); font-size: 12px; padding: 10px; margin: 0; border-radius: 0 0 6px 6px; overflow-x: auto; max-height: 250px; white-space: pre;` — reuses the dark background from `sandbox.js` `.sbx-col-left` |
| `.sbx-sb-copy-btn` | `background: none; border: none; cursor: pointer; font-size: 12px; opacity: 0.7; transition: opacity 0.2s;` |

### Output Block

| Class | Purpose |
|-------|---------|
| `.sbx-sb-output-section` | Same structure as code section |
| `.sbx-sb-output` | `background: var(--bg-card); border: 1px solid var(--border); font-family: var(--font-mono); font-size: 12px; padding: 10px; margin: 0; border-radius: 0 0 6px 6px; overflow-x: auto; max-height: 250px; white-space: pre-wrap; word-break: break-word;` |
| `.sbx-sb-output .sbx-output-stdout` | `color: var(--text-primary);` |
| `.sbx-sb-output .sbx-output-stderr` | `color: #dc2626; background: #fee2e2; padding: 6px; border-radius: 4px; margin-top: 4px;` |

---

## 8. Backend Changes

**No backend changes needed.** The existing sandbox events (`sandbox_code_generated`, `sandbox_execution_started`, `sandbox_execution_complete`, `sandbox_error`) already flow through the SSE pipeline and are already routed to both `addSandboxEvent()` and `addStreamEvent()` in `app.js`. The new sidebar just adds a third consumer for these same events.

---

## 9. File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `CustomerAgentUI/components/sandbox-sidebar.js` | Create | New ES module: sidebar initialization, event handling, DOM rendering, tab management |
| `CustomerAgentUI/index.html` | Modify | Add `<aside id="sandbox-sidebar">` element between `</main>` and `<aside id="context-panels">` |
| `CustomerAgentUI/app.js` | Modify | Import new module, call `initSandboxSidebar()`, route sandbox events to sidebar, show/hide on view switch, clear on reset |
| `CustomerAgentUI/styles.css` | Modify | Add sandbox sidebar styles (or self-inject via `_injectStyles()` in the module) |
| Backend | No change | Sandbox events already emitted and routed |
