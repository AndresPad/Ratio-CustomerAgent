/**
 * Event Card Renderers
 *
 * Each function takes a parsed SSE event and returns an HTML string
 * for an event card.  The card has a color-coded left border and
 * a type label matching the event category.
 */

/**
 * Render an event into an HTML card string.
 * @param {Object} event — parsed SSE event
 * @returns {string} HTML string for the card
 */
export function renderCard(event) {
    const type = event.type || 'unknown';
    const time = _formatTime(event.created_at || event.timestamp);
    const classified = _classify(event);
    if (!classified) return '';  // Skip suppressed events
    const { category, label, body } = classified;

    return `
        <div class="event-card type-${category}">
            <div class="card-header">
                <span class="card-type">${_esc(label)}</span>
                <span class="card-time">${time}</span>
            </div>
            <div class="card-body">${body}</div>
        </div>
    `;
}

/* ── Event classification and body rendering ────────────────────────────── */

function _classify(event) {
    const t = event.type;

    // Pipeline lifecycle
    if (t === 'pipeline_started')
        return { category: 'agent', label: 'Pipeline Started', body: `XCV: <code>${_esc(event.source_xcv || event.xcv)}</code>` };
    if (t === 'pipeline_complete')
        return { category: 'complete', label: 'Pipeline Complete', body: `Investigations: ${event.investigation_count || 0}` };
    if (t === 'pipeline_error')
        return { category: 'error', label: 'Pipeline Error', body: `<pre>${_esc(event.error)}</pre>` };

    // Signal evaluation
    if (t === 'SignalEvaluationStart')
        return { category: 'signal', label: 'Signal Eval Start', body: `Customer: ${_esc(event.CustomerName || '')}${event.ServiceName ? ` · Service: ${_esc(event.ServiceName)}` : ''}${event.ServiceTreeId ? ` · ID: <code>${_esc(event.ServiceTreeId)}</code>` : ''}` };
    if (t === 'SignalTypeEvaluated') {
        const sName = event.SignalName || event.SignalType || event.SignalTypeId || 'unknown';
        const rows = event.RowCount != null ? event.RowCount : event.TotalEvaluated;
        let body = `Activated: ${event.ActivatedCount || 0}${rows ? ` / ${rows} rows` : ''} · Strength: ${event.MaxStrength || 0} · Confidence: ${_esc(event.BestConfidence || '')}`;
        if (event.ActivatedSLIs) {
            body += `<br><span class="card-slis">SLIs: ${_esc(event.ActivatedSLIs)}</span>`;
        }
        return { category: 'signal', label: `Signal: ${_esc(sName)}`, body };
    }
    if (t === 'MCPCollectionCall')
        return { category: 'mcp', label: 'MCP Call', body: `Tool: <code>${_esc(event.Tool || '')}</code>${event.ServiceName ? ` · Service: ${_esc(event.ServiceName)}` : ''}${_renderParams(event.Parameters)}${event.RowCount != null ? ` · Rows: ${event.RowCount}` : ''}${event.DurationMs ? ` · ${event.DurationMs}ms` : ''}${event.Error ? ` · <span class="card-error">${_esc(event.Error)}</span>` : ''}` };
    if (t === 'CompoundEvaluated') {
        let cBody = `Activated: ${event.Activated ? 'Yes' : 'No'} · Strength: ${event.Strength ?? '--'}`;
        if (event.Confidence) cBody += ` · Confidence: ${_esc(event.Confidence)}`;
        if (event.ContributingTypes) cBody += `<br><span class="card-slis">Types: ${_esc(event.ContributingTypes)}</span>`;
        if (event.Rationale) cBody += `<br><span class="card-slis">${_esc(event.Rationale)}</span>`;
        return { category: 'compound', label: `Compound: ${_esc(event.CompoundName || '')}`, body: cBody };
    }
    if (t === 'SignalDecision')
        return { category: 'decision', label: 'Decision', body: `Action: <strong>${_esc(event.Action || '')}</strong> · Activated: ${event.ActivatedSignalCount || 0} signals, ${event.ActivatedCompoundCount || 0} compounds` };
    if (t === 'signal_evaluation_complete')
        return { category: 'decision', label: 'Signals Complete', body: `${(event.results || []).length} result(s)` };

    // Investigation lifecycle
    if (t === 'investigation_started')
        return { category: 'agent', label: 'Investigation Started', body: `ID: <code>${_esc(event.investigation_id || '')}</code> · Signals: ${event.signal_count || 0} · Compounds: ${event.compound_count || 0}` };
    if (t === 'investigations_starting')
        return { category: 'agent', label: 'Investigations Starting', body: `Count: ${event.count || 0}` };
    if (t === 'hypothesis_evaluation_started') {
        const badge = event.rank === 1 ? ' ★' : '';
        return { category: 'hypothesis', label: `Evaluating Hypothesis #${event.rank || '?'}${badge}`, body: `<strong>${_esc(event.hypothesis_id || '')}</strong> · Score: ${event.match_score || '?'}<br>${_esc(event.statement || '')}` };
    }

    // Agent events
    if (t === 'investigation_agent_start')
        return { category: 'agent', label: `Agent: ${_esc(event.agent || '')}`, body: 'Starting...' };
    if (t === 'investigation_agent_response')
        return _renderAgentResponse(event);
    if (t === 'investigation_narrator')
        return { category: 'narrator', label: `Narrator (after ${_esc(event.narrated_agent || '?')})`, body: `<em>${_esc(event.text || '')}</em>` };
    if (t === 'AgentInvoked')
        return { category: 'agent', label: `Agent Invoked: ${_esc(event.Agent || '')}`, body: _truncate(event.Input || event.input || '', 200) };
    if (t === 'AgentResponse')
        return { category: 'agent', label: `Agent Response: ${_esc(event.Agent || '')}`, body: _truncate(event.Output || event.output || '', 300) };

    // Phase transitions
    if (t === 'PhaseTransition')
        return { category: 'phase', label: 'Phase Transition', body: `${_esc(event.FromPhase || event.from_phase || '')} → <strong>${_esc(event.ToPhase || event.to_phase || '')}</strong> (agent: ${_esc(event.Agent || event.agent_name || '')})` };

    // Hypothesis events
    if (t === 'HypothesisScoring')
        return { category: 'hypothesis', label: `Hypotheses Scored`, body: `Candidates: ${event.OutputHypothesisCount || '?'} · Top: ${_esc(event.TopHypothesisId || '')} (${event.TopScore || '?'}) · Scores: ${_esc(event.AllScores || '')}` };
    if (t === 'HypothesisSelected') {
        const badge = event.Rank === 1 ? ' ★' : '';
        return { category: 'hypothesis', label: `Hypothesis #${event.Rank || '?'}${badge}`, body: `<strong>${_esc(event.HypothesisId || '')}</strong> · Score: ${event.MatchScore || '?'}<br>${_esc(event.Statement || '')}<br>Matched: ${_esc(event.MatchedSymptoms || 'none')} · Evidence needed: ${_esc(event.EvidenceNeeded || 'none')}` };
    }
    if (t === 'HypothesisTransition')
        return { category: 'hypothesis', label: 'Hypothesis Transition', body: `<strong>${_esc(event.HypothesisId || '')}</strong>: ${_esc(event.OldStatus || '')} → <strong>${_esc(event.NewStatus || event.ToStatus || '')}</strong> · Confidence: ${event.Confidence || '?'}` };

    // Evidence
    if (t === 'EvidenceCycle')
        return { category: 'evidence', label: `Evidence Cycle ${event.Cycle || ''}`, body: `Hypothesis: ${_esc(event.HypothesisId || '')}` };

    // Sandbox events
    if (t === 'sandbox_code_generated') {
        const code = event.code || '';
        const highlighted = code ? _highlightPython(code) : '<span class="placeholder">No code yet</span>';
        const taskLine = event.task ? `<br>task: ${_esc(_truncate(event.task, 120))}` : '';
        return { category: 'tool', label: 'Tool: SANDBOX_CODE_CREATOR', body: `Agent: sandbox_code_creator · Stage: ${_esc(event.stage || '')}${taskLine}${event.duration_seconds ? ` · Duration: ${event.duration_seconds.toFixed(1)}s` : ''}<div class="card-code-container"><div class="card-code-header">🚀 CODE</div><pre class="card-code-content">${highlighted}</pre></div>` };
    }
    if (t === 'sandbox_execution_started') {
        // Skip rendering — sandbox_code_generated already shows the code
        return null;
    }
    if (t === 'sandbox_execution_complete') {
        const cat = event.success ? 'complete' : 'error';
        const status = event.success ? '✅ Success' : '❌ Failed';
        const dur = event.duration_seconds ? ` · Duration: ${event.duration_seconds.toFixed(1)}s` : '';
        let output = '';
        if (event.stdout) output += `<div class="card-code-container"><div class="card-code-header">STDOUT</div><pre class="card-code-content">${_esc(event.stdout)}</pre></div>`;
        if (event.stderr) output += `<div class="card-code-container"><div class="card-code-header">STDERR</div><pre class="card-code-content">${_esc(event.stderr)}</pre></div>`;
        return { category: cat, label: 'Sandbox: Execution Complete', body: `${status}${dur}${output}` };
    }
    if (t === 'sandbox_error') {
        return { category: 'error', label: 'Sandbox Error', body: `<pre>${_esc(event.error || '')}</pre>${event.filename ? `<br>File: ${_esc(event.filename)}` : ''}` };
    }

    // Tool calls
    if (t === 'ToolCall') {
        const toolName = event.Tool || '';
        const stage = event.Stage || '';
        const hypId = event.HypothesisId || '';
        let stageLabel = stage ? ` · Stage: ${_esc(stage)}` : '';
        if (hypId) stageLabel += ` · Hypothesis: ${_esc(hypId)}`;

        // Sandbox tools — code is shown via sandbox_code_generated event
        if (toolName === 'execute_python_in_sandbox') {
            // Don't try to extract code from Arguments (regex breaks on multi-line Python)
            // The sandbox_code_generated event renders the code with proper highlighting
            return null;  // Suppress — redundant with sandbox_code_generated + sandbox_execution_complete
        }
        if (toolName === 'sandbox_code_creator' || toolName === 'sandbox_coder') {
            const task = _extractArg(event.Arguments, 'task');
            const code = _extractArg(event.Arguments, 'code');
            let body = `Agent: ${_esc(event.Agent || '')}${stageLabel}`;
            if (task) body += `<br>task: ${_esc(_truncate(task, 200))}`;
            body += ` · Duration: ${event.DurationMs || '?'}ms`;
            if (code) body += `<div class="card-code-container"><div class="card-code-header">🚀 CODE</div><pre class="card-code-content">${_highlightPython(code)}</pre></div>`;
            return { category: 'tool', label: `Tool: ${_esc(toolName.toUpperCase())}`, body };
        }

        return { category: 'tool', label: `Tool: ${_esc(toolName)}`, body: `Agent: ${_esc(event.Agent || '')}${stageLabel}${_renderParams(event.Arguments)} · Duration: ${event.DurationMs || '?'}ms` };
    }

    // LLM calls
    if (t === 'LLMCall')
        return { category: 'tool', label: 'LLM Call', body: `Model: ${_esc(event.Model || '')} · Tokens: ${event.TotalTokens || '?'}` };

    // Investigation complete
    if (t === 'investigation_complete')
        return { category: 'complete', label: 'Investigation Complete', body: `Symptoms: ${event.symptoms_count || 0} · Hypotheses: ${event.hypotheses_count || 0} · Evidence: ${event.evidence_count || 0} · Actions: ${event.actions_count || 0} · Duration: ${(event.duration_seconds || 0).toFixed(1)}s` };
    if (t === 'investigation_error')
        return { category: 'error', label: 'Investigation Error', body: `<pre>${_esc(event.error || '')}</pre>` };
    if (t === 'investigation_stall_warning')
        return { category: 'warn', label: 'Stall Warning', body: `Waiting ${event.wait_seconds || '?'}s (warn #${event.warn_count || '?'}) · Agent: ${_esc(event.agent || '?')} · Phase: ${_esc(event.phase || '?')}${event.llm_detail ? ' · ' + _esc(event.llm_detail) : ''}` };
    if (t === 'InvestigationError')
        return { category: 'error', label: 'Investigation Error', body: `Phase: ${_esc(event.Phase || '')} · <pre>${_esc(event.Error || '')}</pre>` };

    // Symptom templates
    if (t === 'SymptomTemplatesLoaded')
        return { category: 'signal', label: 'Symptoms Loaded', body: `Templates: ${event.TemplateCount || event.template_count || 0}` };

    // Speaker selection
    if (t === 'SpeakerSelected')
        return { category: 'agent', label: 'Speaker Selected', body: `${_esc(event.NextSpeaker || event.Agent || event.speaker || '')} (round ${event.EvidenceCycle || event.Round || ''})` };

    // Output parsed
    if (t === 'OutputParsed')
        return { category: 'agent', label: 'Output Parsed', body: `Agent: ${_esc(event.Agent || '')} · Phase Complete: ${event.PhaseComplete || 'false'}` };

    // Request lifecycle (from AgentLogger)
    if (t === 'RequestStart')
        return { category: 'agent', label: 'Request Start', body: `Query: ${_truncate(event.Query || '', 100)}` };
    if (t === 'AgentsLoaded')
        return { category: 'agent', label: 'Agents Loaded', body: `Count: ${event.AgentCount || '?'}` };

    // Prompt injection
    if (t === 'PromptInjectionDetected') {
        const verdict = event.FinalVerdict || 'UNKNOWN';
        const reasons = Array.isArray(event.Reasons) ? event.Reasons.join(', ') : '';
        const apiMs = event.ApiLatencyMs ? `${Math.round(event.ApiLatencyMs)}ms` : '';
        const totalMs = event.DurationMs ? `${Math.round(event.DurationMs)}ms` : '';
        const isInjection = event.IsInjection === true;
        const cat = isInjection ? 'error' : 'phase';
        const lbl = isInjection ? 'Injection BLOCKED' : 'Injection Check: Safe';

        let body = `Agent: <strong>${_esc(event.Agent || '')}</strong> · Verdict: <strong>${_esc(verdict)}</strong>`;
        if (reasons) body += ` · Reasons: ${_esc(reasons)}`;
        if (apiMs) body += ` · API: ${apiMs}`;
        if (totalMs) body += ` · Total: ${totalMs}`;

        // Show detector details in a collapsible section
        if (event.Detectors && typeof event.Detectors === 'object' && Object.keys(event.Detectors).length > 0) {
            const detRaw = _esc(JSON.stringify(event.Detectors, null, 2));
            body += `<details><summary>Detector Details</summary><pre class="detector-details">${detRaw}</pre></details>`;
        }
        return { category: cat, label: lbl, body };
    }

    // Injection API call audit trail (verbose — only shown for debugging)
    if (t === 'InjectionApiCall') {
        const verdict = event.FinalVerdict || 'SAFE';
        const reasons = Array.isArray(event.Reasons) ? event.Reasons.join(', ') : '';
        const err = event.Error || '';
        let body = `Agent: <strong>${_esc(event.Agent || '')}</strong> · Verdict: ${_esc(verdict)}`;
        if (reasons) body += ` · Reasons: ${_esc(reasons)}`;
        body += ` · ${Math.round(event.DurationMs || 0)}ms`;
        if (err) body += ` · <span style="color:var(--error)">Error: ${_esc(err)}</span>`;
        return { category: err ? 'error' : 'agent', label: 'Injection API Call', body };
    }

    // Context folding
    if (t === 'ContextFolding') {
        const reduction = event.TokenReduction || 0;
        const pct = event.OriginalTokens ? Math.round((reduction / event.OriginalTokens) * 100) : 0;
        let body = `Agent: <strong>${_esc(event.Agent || '')}</strong> · Phase: ${_esc(event.Phase || '')} · Fold #${event.FoldNumber || 1}`
            + `<br>Messages folded: ${event.MessagesFolded || 0} · Tokens: ${event.OriginalTokens || '?'} → ${event.FoldedTokens || '?'} (saved ${reduction}, ${pct}%)`;
        if (event.SummaryContent) {
            body += `<details><summary>Summary Content</summary><pre class="folding-summary">${_esc(event.SummaryContent)}</pre></details>`;
        }
        return { category: 'phase', label: 'Context Folding', body };
    }

    // Fallback for unknown events
    const _raw = _esc(JSON.stringify(event, null, 2));
    const _fallbackBody = _raw.length > 500
        ? `<details><summary>${_raw.substring(0, 200)}…</summary><pre>${_raw}</pre></details>`
        : `<pre>${_raw}</pre>`;
    return { category: 'agent', label: t, body: _fallbackBody };
}

/** Render a rich agent response card with parsed signals and optional tool calls */
function _renderAgentResponse(event) {
    const agent = event.agent || '';
    const phase = event.phase || '';
    const signals = event.parsed_signals || {};
    let body = `Phase: <strong>${_esc(phase)}</strong>`;

    // Add parsed signal flags
    const flags = [];
    if (signals.phase_complete) flags.push('phase_complete');
    if (signals.investigation_resolved) flags.push('resolved');
    if (signals.needs_more_evidence) flags.push('needs_evidence');
    if (signals.next_agent) flags.push(`next: ${signals.next_agent}`);
    if (flags.length) body += ` · Signals: ${flags.join(', ')}`;

    // Counts
    body += `<br>Symptoms: ${event.symptoms_count || 0} · Hypotheses: ${event.hypotheses_count || 0} · Evidence: ${event.evidence_count || 0}`;

    // Tool calls (if present)
    if (event.tool_calls && event.tool_calls.length > 0) {
        body += '<br>Tools: ' + event.tool_calls.map(tc =>
            `<code>${_esc(tc.tool || tc.name || 'unknown')}</code>`
        ).join(', ');
    }

    // Full agent response text
    if (event.text) {
        body += `<pre>${_esc(event.text)}</pre>`;
    }

    return { category: 'agent', label: `${_esc(agent)} Response`, body };
}

/* ── Utilities ──────────────────────────────────────────────────────────── */

/**
 * Extract a named argument value from a stringified Python dict.
 * Arguments arrive as "'code': 'import pandas...', 'filename': '...'" etc.
 * This does a best-effort extraction of the value for a given key.
 */
function _extractArg(raw, key) {
    if (!raw) return '';
    const str = String(raw);
    // Match 'key': 'value' or "key": "value" patterns
    // The value may contain escaped quotes, newlines, etc.
    const patterns = [
        new RegExp(`['"]${key}['"]\\s*:\\s*['"]([\\s\\S]*?)(?:['"]\\s*,\\s*['"]|['"]\\s*\\})`),
        new RegExp(`['"]${key}['"]\\s*:\\s*['"]([\\s\\S]*?)['"]\\s*$`),
    ];
    for (const pat of patterns) {
        const m = str.match(pat);
        if (m && m[1]) return m[1].replace(/\\n/g, '\n').replace(/\\t/g, '\t').replace(/\\'/g, "'");
    }
    // Fallback: try to find key: value without quotes (for truncated output)
    const fallback = str.match(new RegExp(`${key}:\\s*(.+?)(?:,\\s*\\w+:|$)`));
    if (fallback && fallback[1]) return fallback[1].trim();
    return '';
}

/**
 * Render a parameters string (from backend) as a compact key=value list.
 * The backend sends Parameters as a stringified dict like "{'key': 'val', ...}".
 */
function _renderParams(raw) {
    if (!raw) return '';
    let text = String(raw);
    // Strip outer braces and quotes for readability
    text = text.replace(/^\{|\}$/g, '').trim();
    if (!text) return '';
    // Collapse to compact form and truncate
    text = text.replace(/'/g, '');
    if (text.length > 200) text = text.substring(0, 200) + '...';
    return `<br><span class="card-params">${_esc(text)}</span>`;
}

/** Minimal Python syntax highlighter for inline code cards */
function _highlightPython(code) {
    let escaped = _esc(code);
    const keywords = ['import', 'from', 'def', 'class', 'return', 'if', 'else', 'elif',
        'for', 'while', 'in', 'not', 'and', 'or', 'try', 'except', 'finally',
        'with', 'as', 'yield', 'raise', 'pass', 'break', 'continue', 'True',
        'False', 'None', 'async', 'await', 'print', 'lambda'];
    escaped = escaped.replace(/(&#39;.*?&#39;|&quot;.*?&quot;|'.*?'|".*?")/g,
        '<span style="color:#ce9178">$1</span>');
    escaped = escaped.replace(/(#.*?)$/gm,
        '<span style="color:#6a9955">$1</span>');
    keywords.forEach(kw => {
        escaped = escaped.replace(new RegExp(`\\b(${kw})\\b`, 'g'),
            '<span style="color:#569cd6">$1</span>');
    });
    escaped = escaped.replace(/\b(\d+\.?\d*)\b/g,
        '<span style="color:#b5cea8">$1</span>');
    return escaped;
}

/** Escape HTML to prevent XSS */
function _esc(str) {
    if (!str) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;');
}

/** Truncate long strings with ellipsis */
function _truncate(str, maxLen) {
    if (!str) return '';
    str = String(str);
    return str.length > maxLen ? str.substring(0, maxLen) + '...' : str;
}

/** Format a Unix timestamp into HH:MM:SS.mmm */
function _formatTime(ts) {
    if (!ts) return '';
    const d = new Date(ts * 1000);
    const h = String(d.getHours()).padStart(2, '0');
    const m = String(d.getMinutes()).padStart(2, '0');
    const s = String(d.getSeconds()).padStart(2, '0');
    const ms = String(d.getMilliseconds()).padStart(3, '0');
    return `${h}:${m}:${s}.${ms}`;
}
