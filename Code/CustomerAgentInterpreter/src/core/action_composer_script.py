"""Canonical action_composer sandbox script.

This file is the single source of truth for the per-correlation action plan
matcher. The orchestrator (``core/composer.py``) pre-stages it to ADLS at
``{ADLS_BASE_PATH}/{CORRELATION_ID}/composer/script/action_composer.py`` and
the agent invokes ``execute_python_in_sandbox(script_path=..., filename="action_composer.py")``.

DO NOT inline this script back into ``action_composer_prompt.txt``. The whole
point of the file is to take script authoring out of the LLM's hands so the
matcher behaves identically every run — when the LLM writes the script
itself, gpt-4o intermittently weakens the empty-lane filter, drops the
``__primary__`` infra override, or mis-evaluates the ``applies_when`` chain
and we end up with ``actions: []`` even when the inputs clearly match a
catalog entry (e.g. HYP-SUP-003 ``resolved_as_contributing`` confidence
0.65, scope_in __primary__).

Sandbox runtime contract
------------------------
The sandbox preamble (``sandbox/tools.py::_SANDBOX_PREAMBLE``) injects:
  - ``json``/``numpy``/``pandas`` aware ``json.dumps`` default
  - ``adls_exists`` / ``adls_read_text`` / ``adls_write_text`` / ``adls_list``
The ``execute_python_in_sandbox`` runtime injects these constants:
  - ``ADLS_BASE_PATH``       — staging root for this run's artifacts
  - ``ADLS_SOURCE_BASE_PATH``— xcv-evidence root (CustomerAgent output)
  - ``CORRELATION_ID``       — current correlation window id
  - ``OUTCOME_XCVS``         — list of xcvs in this correlation
  - ``ADLS_TOKEN`` / ``ADLS_TOKEN_EXPIRES_ON`` / ``ADLS_ACCOUNT`` / ``ADLS_FILESYSTEM``

So this script can use those names freely without re-declaring them.
"""
import json, pandas as pd, numpy as np

def _safe(o):
    if isinstance(o,(np.integer,)): return int(o)
    if isinstance(o,(np.floating,)): return float(o)
    if isinstance(o,(np.bool_,)): return bool(o)
    if isinstance(o,(np.ndarray,)): return o.tolist()
    if isinstance(o,(pd.Timestamp,)): return o.isoformat()
    if pd.isna(o): return None
    return str(o)
def _jd(o, **kw): kw.setdefault("default", _safe); kw.setdefault("indent", 2); return json.dumps(o, **kw)

base = f"{ADLS_BASE_PATH}/{CORRELATION_ID}"
input_data = json.loads(adls_read_text(f"{base}/input.json"))
outcomes        = input_data.get("outcomes", [])
all_hypotheses  = input_data.get("all_hypotheses", [])
action_catalog  = input_data.get("action_catalog", [])
xcv_svc_map     = input_data.get("xcv_service_names", {}) or {}
cdec            = input_data.get("correlation_decision", {}) or {}
xcv_to_group    = cdec.get("xcv_to_group_id", {}) or {}
xcv_to_siblings = cdec.get("xcv_to_siblings", {}) or {}
groups_by_id    = {g["group_id"]: g for g in cdec.get("groups", [])}
single_plan     = cdec.get("recommended_grouping") == "single_plan"

# Manifest-driven evidence + analysis loaders. Every file path read MUST
# live under {ADLS_SOURCE_BASE_PATH}/{xcv}/ for the current run's xcv.
# Stale manifests / cross-run path leakage have caused services_breakdown
# rows from past runs to bleed in — the prefix check below is the gate.
evidence, analyses = {}, {}
_run_xcvs = set(OUTCOME_XCVS or [])
for xcv in OUTCOME_XCVS:
    mp = f"{ADLS_SOURCE_BASE_PATH}/{xcv}/_manifest.json"
    if not adls_exists(mp): continue
    _xcv_prefix = f"{ADLS_SOURCE_BASE_PATH}/{xcv}/"
    for f in json.loads(adls_read_text(mp)).get("files", []):
        path = f.get("path") or ""
        if not path.endswith(".json"): continue
        if not path.startswith(_xcv_prefix): continue  # cross-xcv leakage guard
        ftype = f.get("type") or ("analysis" if "/analysis/hypothesis_" in path else "evidence")
        try: raw = json.loads(adls_read_text(path))
        except Exception: continue
        if ftype == "analysis":
            hid = f.get("hypothesis_id") or path.rsplit("/",1)[-1].removeprefix("hypothesis_").removesuffix("_analysis.json")
            analyses[(xcv, hid)] = raw
        else:
            scope = f.get("service_scope") or "__primary__"
            evidence[(xcv, scope, f.get("er_id") or "")] = {"df": pd.DataFrame(raw.get("rows", [])), "rows": f.get("rows", 0)}

# Customer-scope whitelists from PRIMARY evidence.
# Tolerant column lookup — evidence row schemas vary (Region/region, SubscriptionId/subscriptionId/subscription_id, ResourceId/resourceId/resource_id).
_COL_ALIASES = {"region": ["Region","region","RegionName","location","Location"],
                "sub":    ["SubscriptionId","subscriptionId","subscription_id","SubscriptionID","Subscription","subscription"],
                "res":    ["ResourceId","resourceId","resource_id","ResourceID","Resource","resource"]}
def _col(df, key):
    if df is None or df.empty: return None
    for c in _COL_ALIASES[key]:
        if c in df.columns: return c
    return None
def _vals(df, key):
    c = _col(df, key)
    if not c: return []
    return [str(v).strip() for v in df[c].dropna().astype(str).unique().tolist() if str(v).strip()]

cust_subs, cust_regions, cust_resources = set(), set(), set()
for (x, sc, _e), v in evidence.items():
    if sc != "__primary__": continue
    df = v["df"]
    if df is None or df.empty: continue
    cust_subs.update(_vals(df, "sub"))
    cust_regions.update(_vals(df, "region"))
    cust_resources.update(_vals(df, "res"))

# hyp_id -> [(scope, er_id, xcv)]. Dedupe by (scope, xcv) — er_ids vary but a
# (scope, xcv) pair must only appear once or matchers double-count.
hyp_scopes = {}
for (xcv, hid), an in analyses.items():
    b = hyp_scopes.setdefault(hid, [])
    ers = an.get("evidence_requirements", []) or []
    if ers:
        for er in ers:
            b.append((er.get("service_scope") or "__primary__", er.get("er_id", ""), xcv))
    else:
        b.append(("__primary__", "", xcv))
hyp_to_xcv, hyp_to_svc = {}, {}
for o in outcomes:
    ox = o.get("xcv","")
    osvc = o.get("service_name","") or xcv_svc_map.get(ox, "")
    for h in o.get("hypotheses", []):
        hid = h.get("id")
        if hid:
            hyp_to_xcv.setdefault(hid, ox)
            hyp_to_svc.setdefault(hid, osvc)
hyp_by_id = {h.get("id"): h for h in all_hypotheses if h.get("id")}
# Fallback: only inject __primary__ when the hyp has NO scope info at all from
# analyses (otherwise we'd double the scope list and produce duplicate matches).
# This MUST run for every hyp in all_hypotheses, even when the analysis file
# is missing (e.g. service-x hyp confirmed but only service-y has analyses
# loaded for OUTCOME_XCVS) — otherwise the catalog matcher never sees the
# hyp at all and emits zero actions even though the hyp clearly qualifies.
for hid, h in hyp_by_id.items():
    if not hyp_scopes.get(hid):
        hyp_scopes[hid] = [("__primary__", "", hyp_to_xcv.get(hid, h.get("xcv","")))]
# Dedupe each hyp's scope list by (scope, xcv) keeping the first er_id seen.
for hid, lst in list(hyp_scopes.items()):
    seen, ded = set(), []
    for sc, eid, xc in lst:
        k = (sc, xc)
        if k in seen: continue
        seen.add(k); ded.append((sc, eid, xc))
    hyp_scopes[hid] = ded

_match_diag = {}  # action_id -> {"matched":N, "no_hyp":N, "verdict":N, "confidence":N, "scope":N, "id_or_cat":N, "samples":[...]}
def _applies(entry, hid, scope):
    aid = entry.get("action_id", "?")
    d = _match_diag.setdefault(aid, {"matched":0,"no_hyp":0,"verdict":0,"confidence":0,"scope":0,"id_or_cat":0,"samples":[]})
    aw = entry.get("applies_when", {}) or {}
    h = hyp_by_id.get(hid)
    if not h:
        d["no_hyp"] += 1
        if len(d["samples"]) < 3: d["samples"].append({"hid":hid,"scope":scope,"reason":"hypothesis_id not in all_hypotheses"})
        return False
    if h.get("status") not in (aw.get("verdict_in") or []):
        d["verdict"] += 1
        if len(d["samples"]) < 3: d["samples"].append({"hid":hid,"scope":scope,"reason":f"status={h.get('status')!r} not in {aw.get('verdict_in')}"})
        return False
    conf = float(h.get("confidence") or 0)
    if conf < float(aw.get("min_confidence") or 0):
        d["confidence"] += 1
        if len(d["samples"]) < 3: d["samples"].append({"hid":hid,"scope":scope,"reason":f"confidence={conf} < min={aw.get('min_confidence')}"})
        return False
    sin = aw.get("scope_in") or []
    if sin and not (scope in sin or (scope != "__primary__" and "<dependency_slug>" in sin)):
        d["scope"] += 1
        if len(d["samples"]) < 3: d["samples"].append({"hid":hid,"scope":scope,"reason":f"scope={scope!r} not in {sin}"})
        return False
    apps_h = aw.get("applicable_hypotheses") or []
    apps_c = aw.get("applicable_categories") or []
    if apps_h or apps_c:
        if hid in apps_h or h.get("category") in apps_c:
            d["matched"] += 1; return True
        d["id_or_cat"] += 1
        if len(d["samples"]) < 3: d["samples"].append({"hid":hid,"category":h.get("category"),"scope":scope,"reason":f"id not in apps_h={apps_h} AND category not in apps_c={apps_c}"})
        return False
    d["matched"] += 1
    return True

def _service_for(scope, xcv, hid=None):
    if scope == "__primary__":
        return xcv_svc_map.get(xcv) or hyp_to_svc.get(hid) or input_data.get("primary_service") or "primary_service"
    return scope  # dependency slug

def _impact(hid, scope, only_xcv=None):
    regs, subs, ress = set(), set(), set()
    xs = {x for s,_e,x in hyp_scopes.get(hid, []) if s == scope and (only_xcv is None or x == only_xcv)}
    for (ex, esc, _eid), v in evidence.items():
        if ex not in xs or esc != scope: continue
        df = v["df"]
        if df is None or df.empty: continue
        for r in _vals(df, "region"):
            if not cust_regions or r in cust_regions: regs.add(r)
        for s in _vals(df, "sub"):
            if not cust_subs or s in cust_subs: subs.add(s)
        for r in _vals(df, "res"):
            if not cust_resources or r in cust_resources: ress.add(r)
    return {"regions": sorted(regs), "subscription_count": len(subs),
            "resource_count": len(ress) if ress else len(subs),
            "impacted_subscriptions": sorted(subs), "impacted_resources": sorted(ress)}

def _evidence_refs(hid, scope, only_xcv=None):
    out, seen = [], set()
    xs = {x for s,_e,x in hyp_scopes.get(hid, []) if s == scope and (only_xcv is None or x == only_xcv)}
    for (ex, esc, eid) in evidence.keys():
        if ex in xs and esc == scope and eid:
            k = (ex, eid)
            if k in seen: continue
            seen.add(k); out.append({"xcv": ex, "hypothesis_id": hid, "er_id": eid})
    for s, eid, x in hyp_scopes.get(hid, []):
        if s != scope or not eid: continue
        if only_xcv is not None and x != only_xcv: continue
        k = (x, eid)
        if k in seen: continue
        seen.add(k); out.append({"xcv": x, "hypothesis_id": hid, "er_id": eid})
    if not out:
        for s, eid, x in hyp_scopes.get(hid, []):
            if s != scope: continue
            if only_xcv is not None and x != only_xcv: continue
            out.append({"xcv": x, "hypothesis_id": hid, "er_id": eid})
    return out

def _customer_impact_all_primary():
    # Customer-wide footprint: union of ALL primary-scope evidence in the
    # window, regardless of which hypothesis matched. This is what
    # per_correlation summary actions report (e.g. EMAIL-AED-IMPACT) so
    # subscriptions/regions from non-matched-but-relevant evidence files
    # (e.g. sli_customer.json when only HYP-SUP-* matched) are not lost.
    regs, subs, ress = set(), set(), set()
    for (_ex, esc, _eid), v in evidence.items():
        if esc != "__primary__": continue
        df = v["df"]
        if df is None or df.empty: continue
        regs.update(_vals(df, "region"))
        subs.update(_vals(df, "sub"))
        ress.update(_vals(df, "res"))
    return {"regions": sorted(regs), "subscription_count": len(subs),
            "resource_count": len(ress) if ress else len(subs),
            "impacted_subscriptions": sorted(subs), "impacted_resources": sorted(ress)}

def _primary_impact_for_xcv(xcv):
    # Per-service primary footprint: union of ALL __primary__ evidence
    # for ONE xcv, regardless of which hypothesis matched. Per_service
    # primary actions (INC-PRIMARY-SVC, EMAIL-AED-IMPACT __primary__
    # lanes) need this because hypothesis-scoped _impact() yields zero
    # rows for support-category hypotheses (HYP-SUP-*) whose evidence
    # lives in ER-TKT-* / ER-OUT-* projector files — the primary
    # infra rows in sli_customer.json for the SAME xcv would otherwise
    # be silently dropped from the action's impact.regions /
    # impacted_subscriptions / impacted_resources.
    regs, subs, ress = set(), set(), set()
    for (ex, esc, _eid), v in evidence.items():
        if esc != "__primary__" or ex != xcv: continue
        df = v["df"]
        if df is None or df.empty: continue
        for r in _vals(df, "region"):
            if not cust_regions or r in cust_regions: regs.add(r)
        for s in _vals(df, "sub"):
            if not cust_subs or s in cust_subs: subs.add(s)
        for r in _vals(df, "res"):
            if not cust_resources or r in cust_resources: ress.add(r)
    return {"regions": sorted(regs), "subscription_count": len(subs),
            "resource_count": len(ress) if ress else len(subs),
            "impacted_subscriptions": sorted(subs), "impacted_resources": sorted(ress)}

def _project_incidents(only_xcv=None):
    # ER-OUT-001 = incidents.json. Surface key fields for downstream rendering.
    # ``only_xcv`` restricts to a single xcv — used by per_service primary
    # actions so each service's email carries only its own incidents.
    out, seen = [], set()
    for (ex, _esc, eid), v in evidence.items():
        if eid != "ER-OUT-001": continue
        if only_xcv is not None and ex != only_xcv: continue
        df = v["df"]
        if df is None or df.empty: continue
        for _, r in df.iterrows():
            iid = str(r.get("IncidentId") or "")
            if not iid or iid in seen: continue
            seen.add(iid)
            out.append({"incident_id": iid, "severity": _safe(r.get("Severity")),
                        "is_outage": bool(r.get("IsOutage")) if pd.notna(r.get("IsOutage")) else None,
                        "status": _safe(r.get("Status")), "title": _safe(r.get("Title")),
                        "owning_tenant": _safe(r.get("OwningTenantName")),
                        "impact_start": _safe(r.get("ImpactStartDate"))})
    return out

def _project_sli_multicustomer(only_xcv=None):
    # ER-SLI-002 = sli_multicustomer.json (cross_customer_region). Aggregated
    # cross-customer SLI footprint for OTHER customers (counts only, never
    # per-row detail). Sums impacted subs/resources and unions distinct
    # customers/regions across all rows in the window.
    subs_total, res_total, cust_total = 0, 0, 0
    regions = set()
    has_any = False
    for (ex, _esc, eid), v in evidence.items():
        if eid != "ER-SLI-002": continue
        if only_xcv is not None and ex != only_xcv: continue
        df = v["df"]
        if df is None or df.empty: continue
        has_any = True
        for _, r in df.iterrows():
            try: subs_total += int(r.get("impacted_subscriptions") or r.get("ImpactedSubscriptions") or 0)
            except Exception: pass
            try: res_total += int(r.get("total_impacted_resources") or r.get("ImpactedResources") or 0)
            except Exception: pass
            try: cust_total = max(cust_total, int(r.get("distinct_customers") or 0))
            except Exception: pass
            reg = r.get("Region") or r.get("region")
            if reg and str(reg).strip(): regions.add(str(reg).strip())
    if not has_any: return None
    return {"subscription_count": subs_total, "resource_count": res_total,
            "customer_count": cust_total, "regions": sorted(regions)}

def _project_support_customer(only_xcv=None):
    # ER-TKT-001 = support_customer.json. Per-ticket detail for THIS customer
    # (every case + critsit/escalation flags). One row per CaseNumber.
    out, seen = [], set()
    for (ex, _esc, eid), v in evidence.items():
        if eid != "ER-TKT-001": continue
        if only_xcv is not None and ex != only_xcv: continue
        df = v["df"]
        if df is None or df.empty: continue
        for _, r in df.iterrows():
            cn = str(r.get("CaseNumber") or "")
            if not cn or cn in seen: continue
            seen.add(cn)
            out.append({"case_number": cn,
                        "is_crit_sit": bool(r.get("IsCritSit")) if pd.notna(r.get("IsCritSit")) else False,
                        "region": _safe(r.get("Region"))})
    return out

def _project_support_summary():
    # ER-TKT-002 = support_multicustomer.json. Aggregate cross-customer SR
    # signal (SR count, distinct customer count, critsit count, products).
    sr_total, crit_total, customers, products = 0, 0, set(), set()
    for (_ex, _esc, eid), v in evidence.items():
        if eid != "ER-TKT-002": continue
        df = v["df"]
        if df is None or df.empty: continue
        for _, r in df.iterrows():
            try: sr_total += int(r.get("TotalCaseCount") or 0)
            except Exception: pass
            try: crit_total += int(r.get("CritSitCount") or 0)
            except Exception: pass
            cl = r.get("CustomerList")
            if isinstance(cl, list): customers.update(str(c) for c in cl if c)
            elif isinstance(cl, str): customers.update(c.strip() for c in cl.split(",") if c.strip())
            sp = r.get("SupportProductName")
            if sp: products.add(str(sp))
    if not (sr_total or crit_total or customers or products): return None
    return {"sr_count": sr_total, "customer_count": len(customers),
            "critsit_count": crit_total, "products": sorted(products),
            "customers": sorted(customers)}

def _correlation_context(xcv):
    sibs = xcv_to_siblings.get(xcv) or []
    if not sibs:
        return "Correlation context: standalone lane (no correlated siblings)."
    g = groups_by_id.get(xcv_to_group.get(xcv, ""), {})
    pat = g.get("pattern_type") or "unspecified"
    lines = [f"Correlated with {len(sibs)} other lane(s) in this correlation window:"]
    for s in sibs:
        sx = (s.get("xcv") or "")[:8]
        lines.append(f"  - {sx}: {s.get('service_name') or '(unknown service)'}")
    lines.append(f"Pattern: {pat}")
    return "\n".join(lines)

actions = []
# Customer-wide projectors (shared by per_correlation summary actions).
# NEVER reuse these on per_service lanes — those need per-xcv variants
# (called inline below) so each service's email/incident carries only its
# own incidents/tickets/SLI.
_inc_proj = _project_incidents()
_cust_tix_proj = _project_support_customer()
_sli_multi_proj = _project_sli_multicustomer()
# 1) per_service
seen, dep_buckets = set(), {}  # dep_buckets[(action_id, group_id, svc)] = action
for entry in action_catalog:
    if entry.get("grain") != "per_service": continue
    aid = entry["action_id"]
    merge_in_group = bool(entry.get("merge_within_correlation_group"))
    for hid, scope_list in hyp_scopes.items():
        for scope, _eid, xcv in scope_list:
            if not _applies(entry, hid, scope): continue
            svc = _service_for(scope, xcv, hid)
            ctx = _correlation_context(xcv)
            gid = xcv_to_group.get(xcv)
            if merge_in_group and gid:
                bk = (aid, gid, svc)
                if bk in dep_buckets:
                    a = dep_buckets[bk]
                    a["structured_evidence"].extend(_evidence_refs(hid, scope))
                    a["upstream_customers"] = sorted(set(a.get("upstream_customers", []) + [input_data.get("customer_name", "")]))
                    imp_new = _impact(hid, scope)
                    a["impact"]["regions"] = sorted(set(a["impact"]["regions"]) | set(imp_new["regions"]))
                    a["impact"]["impacted_subscriptions"] = sorted(set(a["impact"]["impacted_subscriptions"]) | set(imp_new["impacted_subscriptions"]))
                    a["impact"]["impacted_resources"] = sorted(set(a["impact"]["impacted_resources"]) | set(imp_new["impacted_resources"]))
                    a["impact"]["subscription_count"] = len(a["impact"]["impacted_subscriptions"])
                    a["impact"]["resource_count"] = len(a["impact"]["impacted_resources"]) or len(a["impact"]["impacted_subscriptions"])
                    continue
            else:
                key = (aid, scope, svc)
                if key in seen: continue
                seen.add(key)
            imp = _impact(hid, scope)
            # For __primary__ per_service lanes, override the hypothesis-
            # scoped infra footprint with the per-xcv union so support-
            # category hypotheses (HYP-SUP-*) still surface the primary
            # service's regions / subs / resources from sli_customer.json
            # etc. Hypothesis-scoped _impact() returns empty for them.
            if scope == "__primary__":
                imp = _primary_impact_for_xcv(xcv)
            # Inject per-xcv projectors on primary-scope lanes BEFORE the
            # empty-lane filter. MUST be xcv-scoped so each service's email
            # carries only its own incidents / tickets / multicustomer SLI —
            # the precomputed _inc_proj / _cust_tix_proj / _sli_multi_proj
            # are customer-wide unions and would leak the other service's
            # rows into this action.
            if scope == "__primary__":
                imp = dict(imp)
                imp["incidents"] = _project_incidents(only_xcv=xcv)
                imp["customer_support_tickets"] = _project_support_customer(only_xcv=xcv)
                imp["sli_multicustomer_summary"] = _project_sli_multicustomer(only_xcv=xcv)
            # Skip stale lanes — accept ANY customer-scoped signal
            # (infra OR projector). Drop only when nothing survives the
            # cust_subs / cust_regions filter AND no projector data exists.
            # NOTE: hypothesis match (verdict resolved_as_contributing /
            # confirmed) is by itself sufficient grounds to emit the action
            # for support-category hyps even when projectors are empty,
            # because the verdict already represents an investigator's
            # judgment that the customer is impacted. We therefore also
            # accept any matched HYP-SUP-* regardless of evidence rows.
            _is_support = isinstance(hid, str) and hid.startswith("HYP-SUP-")
            if not (
                imp.get("regions")
                or imp.get("impacted_subscriptions")
                or imp.get("impacted_resources")
                or imp.get("incidents")
                or imp.get("customer_support_tickets")
                or imp.get("sli_multicustomer_summary")
                or _is_support
            ):
                continue
            a = {"action_id": aid, "service_name": svc, "priority": 1,
                 "title": entry.get("display_name", aid),
                 "action_type": entry.get("action_type", "incident"), "grain": "per_service",
                 "description": entry.get("description", ""), "impact": imp,
                 "structured_evidence": _evidence_refs(hid, scope),
                 "correlation_context": ctx, "correlation_group_id": gid,
                 "catalog_match_confidence": float(hyp_by_id[hid].get("confidence") or 0),
                 "supporting_evidence": hyp_by_id[hid].get("statement") or "",
                 "estimated_impact": "high"}
            if merge_in_group and gid:
                a["upstream_customers"] = [input_data.get("customer_name", "")]
                dep_buckets[(aid, gid, svc)] = a
            actions.append(a)

for a in dep_buckets.values():
    seen_e = set(); ded = []
    for e in a["structured_evidence"]:
        k = (e.get("xcv"), e.get("hypothesis_id"), e.get("er_id"))
        if k in seen_e: continue
        seen_e.add(k); ded.append(e)
    a["structured_evidence"] = ded

# 2) per_correlation
for entry in action_catalog:
    if entry.get("grain") != "per_correlation": continue
    # Dedupe by (hid, scope, xcv) so multi-lane correlations don't collapse
    # different services into one breakdown row.
    matched, seen_triples = [], set()
    for hid, scope_list in hyp_scopes.items():
        for scope, _eid, xcv in scope_list:
            k = (hid, scope, xcv)
            if k in seen_triples: continue
            if _applies(entry, hid, scope):
                matched.append((hid, scope, xcv)); seen_triples.add(k)
    if not matched: continue
    mr, ms, mres, mev, breakdown, seen_bd = set(), set(), set(), [], [], set()
    svc_names = []
    contributing_xcvs = set()
    for hid, scope, xcv in matched:
        imp = _impact(hid, scope, only_xcv=xcv)
        # Drop lanes with zero customer-scoped impact. These are usually
        # stale hypotheses leaked via OUTCOME_XCVS — the analysis file
        # exists but no evidence row matched cust_subs/cust_regions for
        # this xcv. Without this filter we emit one phantom
        # services_breakdown row per stale xcv.
        if not (imp["regions"] or imp["impacted_subscriptions"] or imp["impacted_resources"]):
            continue
        contributing_xcvs.add(xcv)
        mr.update(imp["regions"]); ms.update(imp["impacted_subscriptions"]); mres.update(imp["impacted_resources"])
        mev.extend(_evidence_refs(hid, scope, only_xcv=xcv))
        sname = _service_for(scope, xcv, hid)
        bk = (hid, scope, xcv, sname)
        if bk in seen_bd: continue
        seen_bd.add(bk)
        if sname not in svc_names: svc_names.append(sname)
        breakdown.append({"hypothesis_id": hid, "scope": scope, "xcv": xcv, "service_name": sname,
                          "regions": imp["regions"], "subscription_count": imp["subscription_count"],
                          "resource_count": imp["resource_count"],
                          "impacted_subscriptions": imp["impacted_subscriptions"],
                          "impacted_resources": imp["impacted_resources"]})
    if not breakdown: continue
    seen_e = set(); ded = []
    for e in mev:
        # Restrict structured_evidence to xcvs that produced real impact in
        # this correlation — keeps stale-xcv ER rows from leaking through.
        if e.get("xcv") not in contributing_xcvs: continue
        k = (e.get("xcv"), e.get("hypothesis_id"), e.get("er_id"))
        if k in seen_e: continue
        seen_e.add(k); ded.append(e)
    # Per-correlation summary actions (e.g. EMAIL-AED-IMPACT) report the
    # customer's WHOLE primary footprint in the window, not just the slice
    # tied to matched hypotheses. Hypothesis match is the trigger; evidence
    # inventory is the substrate.
    cust_imp = _customer_impact_all_primary()
    incidents = _inc_proj
    customer_support_tickets = _cust_tix_proj
    support_summary = _project_support_summary()
    sli_multicustomer_summary = _sli_multi_proj
    actions.append({"action_id": entry["action_id"], "service_name": ", ".join(svc_names), "priority": 1,
        "title": entry.get("display_name", entry["action_id"]),
        "action_type": entry.get("action_type", "email"), "grain": "per_correlation",
        "description": entry.get("description", ""),
        "impact": {"regions": cust_imp["regions"], "subscription_count": cust_imp["subscription_count"],
                   "resource_count": cust_imp["resource_count"],
                   "impacted_subscriptions": cust_imp["impacted_subscriptions"],
                   "impacted_resources": cust_imp["impacted_resources"],
                   "services_breakdown": breakdown,
                   "incidents": incidents,
                   "customer_support_tickets": customer_support_tickets,
                   "support_summary": support_summary,
                   "sli_multicustomer_summary": sli_multicustomer_summary},
        "structured_evidence": ded,
        "recommended_grouping": cdec.get("recommended_grouping"),
        "merged_from_groups": sorted({g for g in xcv_to_group.values()}) if single_plan else None,
        "catalog_match_confidence": max((float(hyp_by_id[h].get("confidence") or 0) for h, _s, _x in matched), default=0.0),
        "supporting_evidence": f"{len(matched)} (hyp, scope, xcv) triple(s) satisfied applies_when across {len(svc_names)} service(s)",
        "estimated_impact": "high"})

import os as _os
_dbg_on = _os.getenv("INTERPRETER_COMPOSER_DEBUG","").lower() in ("1","true","yes","on")
_summary_counts = {s: sum(1 for h in all_hypotheses if h.get("status")==s) for s in {h.get("status") for h in all_hypotheses}}
result = {"actions": actions, "affected_resources": [],
    "summary": (f"Composed {len(actions)} action(s) from {len(all_hypotheses)} hypothesis "
                f"({_summary_counts}) across {len(analyses)} analyses, "
                f"{len(evidence)} evidence files, {len(groups_by_id)} correlation group(s).")}
# Emit debug ALWAYS when zero actions were composed — this is the only way
# to diagnose why a hypothesis (e.g. HYP-SUP-003 resolved_as_contributing)
# failed to match a catalog entry. The DEBUG env var still forces inclusion
# even on non-empty plans.
if _dbg_on or not actions:
    result["debug"] = {"hypotheses_total": len(all_hypotheses),
        "hypotheses_status_counts": _summary_counts,
        "hypotheses_inventory": [{"id":h.get("id"),"status":h.get("status"),"confidence":h.get("confidence"),"category":h.get("category"),"xcv":h.get("xcv")} for h in all_hypotheses],
        "hyp_scopes_keys": [{"hid":hid,"scopes":[s for s,_e,_x in v],"xcvs":[x for _s,_e,x in v]} for hid,v in hyp_scopes.items()],
        "match_diagnostics": _match_diag,
        "evidence_columns_seen": sorted({c for v in evidence.values() if v["df"] is not None and not v["df"].empty for c in v["df"].columns}),
        "customer_scope": {"subs": len(cust_subs), "regions": len(cust_regions), "resources": len(cust_resources)},
        "analyses_loaded": len(analyses), "evidence_files_loaded": len(evidence),
        "outcomes_count": len(outcomes), "outcome_xcvs": [o.get("xcv") for o in outcomes],
        "catalog_entries": len(action_catalog), "actions_emitted": len(actions),
        "correlation_groups": len(groups_by_id), "recommended_grouping": cdec.get("recommended_grouping")}
adls_write_text(f"{base}/composer/analysis/action_plan.json", _jd(result))
mp = f"{base}/_manifest.json"
m = json.loads(adls_read_text(mp)) if adls_exists(mp) else {"files": []}
m["files"].append({"path": f"{base}/composer/analysis/action_plan.json", "description": "Composed action plan", "rows": len(actions)})
adls_write_text(mp, _jd(m))
print(_jd(result))
