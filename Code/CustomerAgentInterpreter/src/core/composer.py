"""
ActionComposer — uses a MAF Agent to compose a unified action plan
from correlated + deduplicated outcomes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Path to the canonical sandbox script that the action composer agent runs.
# We pre-stage this file to ADLS and pass the staged URL via ``script_path``
# in the user_message so the LLM never has to author the script itself —
# eliminating gpt-4o drift (truncated catalog matchers, weakened empty-lane
# filters, dropped __primary__ infra overrides) that previously caused
# intermittent ``actions: []`` outputs even when the inputs clearly matched
# a catalog entry. See action_composer_script.py for full rationale.
_COMPOSER_SCRIPT_PATH = Path(__file__).resolve().parent / "action_composer_script.py"

from agent_framework import Agent
from pydantic import ValidationError

from helper.action_catalog import load_action_catalog
from helper.agent_logger import AgentLogger
from helper.azure_clients import upsert_action_plan
from models.schemas import ActionPlan, ActionPlanLLMResponse, CorrelationGroup

logger = logging.getLogger(__name__)
tracker = AgentLogger.get_instance()

# Bounded retry around the LLM call to absorb transient gpt-4o / network blips.
# These are LLM-call retries (orchestrator level) and are independent of the
# in-sandbox script-failure retries documented in the agent prompt.
_AGENT_RETRY_ATTEMPTS = int(os.getenv("INTERPRETER_COMPOSER_RETRY_ATTEMPTS", "3"))
_AGENT_RETRY_BASE_DELAY = float(os.getenv("INTERPRETER_COMPOSER_RETRY_BASE_DELAY", "1.0"))
_AGENT_RETRY_MAX_DELAY = float(os.getenv("INTERPRETER_COMPOSER_RETRY_MAX_DELAY", "8.0"))


# Map hypothesis ID prefix -> category. Mirrors the authoritative hypothesis
# catalog files under Code/CustomerAgent/src/config/hypotheses/. Keeping this
# tiny map in-process avoids cross-repo file coupling. If CustomerAgent ever
# adds a new category, add the prefix here.
_HYP_PREFIX_CATEGORY: dict[str, str] = {
    "HYP-OUT-": "outage",
    "HYP-SLI-": "sli",
    "HYP-SUP-": "support",
    "HYP-DEP-": "dependency",
}


def _hypothesis_category(hyp_id: str | None) -> str:
    """Resolve a hypothesis ID like ``HYP-DEP-002`` to its category.

    Returns ``""`` for unknown / malformed IDs so callers can downgrade
    gracefully (the catalog matcher will simply not match category-only rules).
    """
    if not hyp_id:
        return ""
    for prefix, cat in _HYP_PREFIX_CATEGORY.items():
        if hyp_id.startswith(prefix):
            return cat
    return ""


def _build_correlation_decision(
    correlations: dict[str, Any] | None,
    xcv_service_names: dict[str, str],
) -> dict[str, Any]:
    """Re-shape the correlator output into a composer-friendly decision block.

    The correlator emits ``correlations[]`` with rich pattern info but no stable
    group IDs and no inverse (xcv -> group) lookup. This helper:

    * Assigns deterministic ``G1, G2, ...`` IDs in input order.
    * Builds ``xcv_to_group_id`` and ``xcv_to_siblings`` so the composer prompt
      can attach a ``correlation_context`` to each per-service action without
      re-scanning the whole correlations array.
    * Lifts ``recommended_grouping`` and ``themes`` to the top so the prompt
      can branch on them cheaply.

    Returns a dict with keys: ``recommended_grouping``, ``reasoning``,
    ``themes``, ``groups``, ``xcv_to_group_id``, ``xcv_to_siblings``.
    Returns an "empty" decision (single_plan grouping, no groups) when no
    correlations were produced — composer treats this as "each xcv stands
    alone, no cross-references needed".
    """
    if not correlations:
        return {
            "recommended_grouping": "separate_plans",
            "reasoning": "No correlator output available.",
            "themes": [],
            "groups": [],
            "xcv_to_group_id": {},
            "xcv_to_siblings": {},
        }

    raw_groups = correlations.get("correlations") or []
    groups: list[dict[str, Any]] = []
    xcv_to_group_id: dict[str, str] = {}
    xcv_to_siblings: dict[str, list[dict[str, str]]] = {}

    for idx, g in enumerate(raw_groups, start=1):
        group_id = f"G{idx}"
        related_xcvs = list(g.get("related_xcvs") or [])
        groups.append({
            "group_id": group_id,
            "pattern_type": g.get("pattern_type"),
            "description": g.get("description"),
            "confidence": g.get("confidence"),
            "related_xcvs": related_xcvs,
            "related_er_ids": list(g.get("related_er_ids") or []),
            "service_scopes": list(g.get("service_scopes") or []),
            "shared_hypotheses": list(g.get("shared_hypotheses") or []),
            "common_resources": list(g.get("common_resources") or []),
            "statistical_evidence": g.get("statistical_evidence"),
        })
        for xcv in related_xcvs:
            # First group wins for inverse lookup — keeps the mapping
            # single-valued and predictable. The full groups[] list is still
            # available if the prompt needs multi-membership info.
            if xcv not in xcv_to_group_id:
                xcv_to_group_id[xcv] = group_id
            siblings = [
                {"xcv": other, "service_name": xcv_service_names.get(other, "")}
                for other in related_xcvs if other != xcv
            ]
            # Merge into existing sibling list, dedup by xcv.
            existing = xcv_to_siblings.setdefault(xcv, [])
            seen = {s["xcv"] for s in existing}
            for s in siblings:
                if s["xcv"] not in seen:
                    existing.append(s)
                    seen.add(s["xcv"])

    return {
        "recommended_grouping": correlations.get("recommended_grouping") or "separate_plans",
        "reasoning": correlations.get("reasoning") or "",
        "themes": list(correlations.get("themes") or []),
        "groups": groups,
        "xcv_to_group_id": xcv_to_group_id,
        "xcv_to_siblings": xcv_to_siblings,
    }


class ActionComposer:
    """Composes a unified action plan using the action_composer agent."""

    def __init__(self, agents: dict[str, Agent]) -> None:
        self._agent = agents["action_composer"]

    async def compose(
        self,
        group: CorrelationGroup,
        deduped_actions: list[dict[str, Any]],
        correlations: dict[str, Any] | None = None,
        xcv_service_names: dict[str, str] | None = None,
    ) -> ActionPlan:
        """
        Run action_composer agent to produce a unified action plan.

        Args:
            group: The correlation group with full outcome data.
            deduped_actions: Pre-deduplicated actions from all outcomes.
            correlations: Output from correlator agent (if available).

        Returns:
            ActionPlan persisted to Cosmos.
        """
        # Build context for the agent
        context = self._build_context(group, deduped_actions, correlations, xcv_service_names)

        # Run agent (sandbox script composes actions from all_hypotheses ×
        # action_catalog and extracts affected_resources from raw evidence).
        plan = await self._run_agent(context, group)

        # Persist to Cosmos action_ledger
        await upsert_action_plan(plan)

        tracker.log_action_plan_composed(plan.correlation_id, len(plan.actions))
        logger.info(
            "Composed action plan %s for %s (%d actions)",
            plan.correlation_id, plan.customer_name, len(plan.actions),
        )
        return plan

    def _build_context(
        self,
        group: CorrelationGroup,
        deduped_actions: list[dict[str, Any]],
        correlations: dict[str, Any] | None = None,
        xcv_service_names: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Build the context dict to pass to the action_composer agent.

        ``xcv_service_names`` is a {xcv: service_name} map sourced from the
        OutcomeMessages held in the correlator's buffer. ``OutcomeDocument``
        itself does not carry ``service_name``, so without this map the
        composer cannot label per-service actions with the customer's actual
        primary service (it falls back to the literal string ``"primary_service"``).
        """
        # Enrich each outcome dict with service_name so the sandbox script can
        # resolve the primary service per-xcv without a separate lookup table.
        svc_map = xcv_service_names or {}
        outcome_dicts = []
        for o in group.outcomes:
            d = o.model_dump()
            if "service_name" not in d or not d.get("service_name"):
                d["service_name"] = svc_map.get(o.xcv, "")
            outcome_dicts.append(d)

        # Flatten hypotheses across outcomes and enrich with:
        #   - ``category`` derived from the ID prefix (HYP-OUT-/HYP-SLI-/HYP-SUP-/HYP-DEP-)
        #     so the catalog matcher can fire ``applicable_categories`` rules
        #     without having to know each individual hypothesis ID.
        #   - ``xcv`` so the script can attribute each hypothesis back to the
        #     correct outcome / service when composing per-service actions.
        # NOTE: we deliberately do NOT dedupe here — the same hypothesis ID
        # can legitimately fire on multiple xcvs and each occurrence is its
        # own data point for impact aggregation in the sandbox.
        all_hypotheses: list[dict[str, Any]] = []
        for o in group.outcomes:
            for h in o.hypotheses:
                if not isinstance(h, dict):
                    continue
                hyp = dict(h)  # shallow copy — never mutate stored documents
                hid = hyp.get("id") or hyp.get("hypothesis_id")
                if not hyp.get("category"):
                    hyp["category"] = _hypothesis_category(hid)
                hyp.setdefault("xcv", o.xcv)
                all_hypotheses.append(hyp)

        # Pre-compute the correlation decision block (stable group IDs,
        # xcv -> group lookup, sibling lists). Replaces the raw correlator
        # output in the context — sandbox script no longer has to walk the
        # ``correlations[]`` array to figure out which xcvs share a group.
        correlation_decision = _build_correlation_decision(correlations, svc_map)

        ctx = {
            "customer_name": group.customer_name,
            "correlation_id": group.correlation_id,
            "window_start": group.window_start.isoformat(),
            "window_end": group.window_end.isoformat(),
            "outcomes_count": len(group.outcomes),
            # Map of xcv -> primary service_name. The script uses this to
            # label per_service actions with the customer's real service
            # (e.g. "SQL Connectivity") instead of the literal placeholder.
            "xcv_service_names": svc_map,
            "outcomes": outcome_dicts,
            "deduped_actions": deduped_actions,
            "all_hypotheses": all_hypotheses,
            # Master action catalog — composer MUST map each emitted action to
            # exactly one entry here (action_id). Catalog is the source of truth
            # for grain, tier, applicability, and payload shape.
            "action_catalog": load_action_catalog().get("actions", []),
            # Composer-friendly correlation summary. The raw correlator dict is
            # also kept under ``correlations`` for any consumer that still
            # wants the full pattern_type / statistical_evidence detail.
            "correlation_decision": correlation_decision,
        }
        if correlations:
            ctx["correlations"] = correlations
        return ctx

    async def _run_agent(
        self,
        context: dict[str, Any],
        group: CorrelationGroup,
    ) -> ActionPlan:
        """Invoke the action_composer MAF Agent to produce an ActionPlan.

        Resilience model:
        - Up to ``_AGENT_RETRY_ATTEMPTS`` attempts of ``agent.run()`` with
          exponential backoff + jitter. Catches transient gpt-4o / network
          failures (the agent prompt's "max 2 retries inside the sandbox" only
          covers sandbox-script failures, not LLM/network failures).
        - LLM output is JSON-parsed and validated against
          ``ActionPlanLLMResponse`` so a malformed shape (e.g. ``actions``
          returned as a string) is rejected instead of being persisted.
        - On exhausted retries or validation failure, emit an empty plan with
          the error captured in ``summary`` so the run is observable.

        Pre-staging: the full ``context`` blob (outcomes + hypotheses + the
        action_catalog) is 8 KB+ and contains brace-heavy ``payload_template``
        strings. Asking gpt-4o to echo that verbatim into
        ``write_data_to_sandbox`` is unreliable — the LLM routinely truncates,
        duplicates, or breaks out of string context, producing
        ``Extra data: line 1 column N`` JSONDecodeErrors every run. We avoid
        that entire failure mode by uploading ``input.json`` server-side
        before invoking the agent and passing only the ADLS path in the user
        message. The prompt then instructs the sandbox script to read it via
        ``read_sandbox_file`` (or ``adls_read_text``) directly.
        """
        # Pre-stage input.json server-side. ``_adls_base()`` mirrors the path
        # convention used by ``write_data_to_sandbox`` so the sandbox script
        # can locate it deterministically.
        from sandbox.tools import _adls_base, _get_client  # local import: avoid circular at module import time
        adls_base = _adls_base()
        input_path = f"{adls_base}/{group.correlation_id}/input.json"
        try:
            # indent=2 so any composer input.json staged in ADLS stays
            # human-readable when inspected via Storage Explorer / portal.
            staged_payload = json.dumps(context, default=str, ensure_ascii=False, indent=2)
            await _get_client().upload_file(input_path, staged_payload)
            logger.info(
                "Composer: pre-staged input.json (%d bytes) at %s",
                len(staged_payload), input_path,
            )
        except Exception:
            # Surface but don't crash — the agent prompt also accepts the
            # legacy "stage it yourself" path as a fallback.
            logger.exception(
                "Composer: failed to pre-stage input.json at %s; "
                "agent will be asked to stage it instead", input_path,
            )

        # Pre-stage the canonical sandbox script. Passing it via
        # ``script_path`` makes the agent invoke a deterministic Python
        # file instead of authoring code itself — see _COMPOSER_SCRIPT_PATH
        # above for the full rationale.
        script_path: str | None = (
            f"{adls_base}/{group.correlation_id}/composer/script/action_composer.py"
        )
        try:
            script_body = _COMPOSER_SCRIPT_PATH.read_text(encoding="utf-8")
            await _get_client().upload_file(script_path, script_body)
            logger.info(
                "Composer: pre-staged action_composer.py (%d bytes) at %s",
                len(script_body), script_path,
            )
        except FileNotFoundError:
            logger.error(
                "Composer: canonical script not found at %s — agent will fall "
                "back to authoring the script itself (unstable)",
                _COMPOSER_SCRIPT_PATH,
            )
            script_path = None
        except Exception:
            logger.exception(
                "Composer: failed to pre-stage action_composer.py at %s; "
                "agent will fall back to authoring the script itself",
                script_path,
            )
            script_path = None

        user_message = json.dumps(
            {
                "input_path": input_path,
                "script_path": script_path,
                "correlation_id": group.correlation_id,
                "customer_name": group.customer_name,
                "outcomes_count": len(group.outcomes),
            },
            default=str,
        )
        tracker.log_agent_invoked("action_composer", user_message)

        result: dict[str, Any] | None = None
        last_exc: Exception | None = None

        for attempt in range(1, _AGENT_RETRY_ATTEMPTS + 1):
            t0 = time.perf_counter()
            response = None
            try:
                response = await self._agent.run(user_message)
                duration_ms = (time.perf_counter() - t0) * 1000

                raw = json.loads(response.text)
                validated = ActionPlanLLMResponse.model_validate(raw)
                tracker.log_agent_completed("action_composer", response.text, duration_ms)
                result = validated.model_dump()
                break
            except ValidationError as exc:
                # Schema mismatch: the LLM returned the wrong shape. Retrying
                # rarely helps for this class of failure, so fail fast.
                last_exc = exc
                tracker.log_agent_error(
                    "action_composer",
                    f"schema validation failed: {exc}",
                )
                logger.error(
                    "Action composer returned invalid shape for %s: %s",
                    group.correlation_id, exc,
                )
                break
            except json.JSONDecodeError as exc:
                last_exc = exc
                logger.warning(
                    "Action composer returned non-JSON for %s (attempt %d/%d): %s",
                    group.correlation_id, attempt, _AGENT_RETRY_ATTEMPTS, exc,
                )
            except Exception as exc:  # transient: network, gpt-4o 5xx, timeouts, etc.
                last_exc = exc
                logger.warning(
                    "Action composer agent attempt %d/%d failed for %s: %s",
                    attempt, _AGENT_RETRY_ATTEMPTS, group.correlation_id, exc,
                )
            finally:
                # ALWAYS dump the LLM trace, including on JSONDecodeError /
                # ValidationError / mid-stream failures — otherwise we have
                # no visibility into truncated or malformed responses.
                if response is not None:
                    try:
                        await self._dump_llm_trace(
                            group.correlation_id, attempt, response, user_message,
                        )
                    except Exception:
                        logger.exception(
                            "Composer: trace dump itself raised for cid=%s attempt=%d",
                            group.correlation_id, attempt,
                        )

            if attempt < _AGENT_RETRY_ATTEMPTS:
                delay = min(
                    _AGENT_RETRY_MAX_DELAY,
                    _AGENT_RETRY_BASE_DELAY * (2 ** (attempt - 1)),
                )
                # Full jitter to avoid thundering herd across customers.
                delay = random.uniform(0, delay)
                await asyncio.sleep(delay)

        if result is None:
            tracker.log_agent_error("action_composer", str(last_exc))
            logger.error(
                "Action composer agent exhausted retries for %s: %s",
                group.correlation_id, last_exc,
            )
            result = {
                "actions": [],
                "affected_resources": [],
                "summary": f"Agent error: {last_exc}",
            }

        return ActionPlan(
            correlation_id=group.correlation_id,
            customer_name=group.customer_name,
            actions=result.get("actions", []),
            affected_resources=result.get("affected_resources", []),
            summary=result.get("summary", ""),
            created_at=datetime.now(timezone.utc).isoformat(),
        )

    async def _dump_llm_trace(
        self,
        correlation_id: str,
        attempt: int,
        response: Any,
        user_message: str,
    ) -> None:
        """Persist a per-attempt LLM trace to ADLS for debugging.

        Captures (a) the raw response.text, (b) per-message role + tool-call
        summary so we can tell whether the agent invoked
        ``execute_python_in_sandbox`` or short-circuited to a final answer.
        Best-effort — never raises.
        """
        try:
            from sandbox.tools import _adls_base, _get_client  # local: avoid circular

            messages = list(getattr(response, "messages", []) or [])
            tool_call_count = 0
            tool_names: list[str] = []
            message_summary: list[dict[str, Any]] = []

            for idx, msg in enumerate(messages):
                role = getattr(msg, "role", None)
                role_str = getattr(role, "value", str(role)) if role else "?"
                contents = getattr(msg, "contents", []) or []
                kinds: list[str] = []
                content_dump: list[dict[str, Any]] = []
                for c in contents:
                    cls = type(c).__name__
                    kinds.append(cls)
                    # FunctionCallContent / ToolCallContent etc.
                    if "FunctionCall" in cls or "ToolCall" in cls:
                        tool_call_count += 1
                        name = getattr(c, "name", None) or getattr(c, "function_name", None)
                        if name:
                            tool_names.append(str(name))
                    # Best-effort: dump every attribute we can see on the
                    # content object so we can inspect tool args / tool
                    # results / function names regardless of MAF's wrapper
                    # class. Strings are truncated to 4 KB so the trace
                    # stays bounded.
                    attrs: dict[str, Any] = {"_cls": cls}
                    for attr in (
                        "name", "function_name", "call_id",
                        "arguments", "args",
                        "text", "content",
                        "result", "output",
                        "exception", "error",
                    ):
                        if hasattr(c, attr):
                            try:
                                v = getattr(c, attr)
                                if isinstance(v, (dict, list)):
                                    v_str = json.dumps(v, default=str, ensure_ascii=False)
                                else:
                                    v_str = str(v) if v is not None else None
                                if v_str is not None and len(v_str) > 4000:
                                    v_str = v_str[:4000] + f"...<truncated {len(v_str) - 4000} chars>"
                                attrs[attr] = v_str
                            except Exception as e:
                                attrs[attr] = f"<getattr-error: {e}>"
                    # Detect tool-name from any of the surfaced attrs as a
                    # fallback for MAF wrappers we didn't recognise above.
                    fn_name = attrs.get("name") or attrs.get("function_name")
                    if fn_name and role_str == "assistant" and fn_name not in tool_names:
                        # assistant-side tool invocation
                        tool_call_count += 1
                        tool_names.append(fn_name)
                    content_dump.append(attrs)
                text_attr = getattr(msg, "text", None)
                message_summary.append({
                    "i": idx,
                    "role": role_str,
                    "content_kinds": kinds,
                    "text_preview": (text_attr[:300] if isinstance(text_attr, str) else None),
                    "contents": content_dump,
                })

            trace = {
                "correlation_id": correlation_id,
                "attempt": attempt,
                "user_message": json.loads(user_message) if user_message else None,
                "tool_call_count": tool_call_count,
                "tool_names": tool_names,
                "message_count": len(messages),
                "messages": message_summary,
                "response_text": getattr(response, "text", None),
            }

            # Loud INFO-level signal so we can see this in the server log
            # without having to fetch ADLS.
            logger.info(
                "Composer LLM trace: cid=%s attempt=%d tool_calls=%d tools=%s msgs=%d response_len=%d",
                correlation_id, attempt, tool_call_count, tool_names,
                len(messages), len(getattr(response, "text", "") or ""),
            )
            if tool_call_count == 0:
                logger.warning(
                    "Composer LLM did NOT invoke any tools for cid=%s attempt=%d. "
                    "Raw response (first 1000 chars): %s",
                    correlation_id, attempt,
                    (getattr(response, "text", "") or "")[:1000],
                )

            adls_base = _adls_base()
            trace_path = f"{adls_base}/{correlation_id}/composer/debug/llm_trace_attempt_{attempt}.json"
            await _get_client().upload_file(
                trace_path,
                json.dumps(trace, default=str, ensure_ascii=False, indent=2),
            )
        except Exception:
            logger.exception(
                "Composer: failed to dump LLM trace for cid=%s attempt=%d",
                correlation_id, attempt,
            )
