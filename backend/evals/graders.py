"""Exact checks on structures only. Russian prose is never graded with regexes."""
from __future__ import annotations

import math
import re

from .bank import at


def walk(x):
    if isinstance(x, dict):
        yield x
        for v in x.values():
            yield from walk(v)
    elif isinstance(x, list):
        for v in x:
            yield from walk(v)


def check(name, passed, detail=""):
    return {"name": name, "status": "NA" if passed is None else "PASS" if passed else "FAIL", "detail": detail}


def structured_checks(context, docs):
    """Check exact source refs, typed measures and calculation input provenance."""
    results = []
    verified = typed = ratios = periods = 0
    failures = {k: [] for k in ("source_values", "count_amount", "valid_denominator", "period_alignment")}
    company = (context.get("company") or {}).get("inn")
    for node in walk(context):
        ref = node.get("field_ref")
        if isinstance(ref, str) and re.fullmatch(r"report(?:\.[A-Za-z_][A-Za-z_0-9]*|\[\d+\])+", ref) and "value" in node:
            fact_id = node.get("id", "")
            inn = fact_id.split(":")[0] if ":" in fact_id else company
            if inn in docs:
                path = [int(v) if v.isdigit() else v for v in re.findall(r"[A-Za-z_][A-Za-z_0-9]*|\d+", ref)]
                src = at(docs[inn], path)
                # Sections project arrays/objects and convert dates; compare scalar values only.
                value = node["value"]
                if value is None or isinstance(value, (int, float, bool)):
                    verified += 1
                    source_value = src.get("value")
                    if isinstance(value, (int, float)) and not isinstance(value, bool) and isinstance(source_value, str):
                        try:
                            source_value = float(source_value)
                        except ValueError:
                            pass
                    if source_value != value:
                        failures["source_values"].append({"inn": inn, "path": path, "source": src, "actual": value})
        fid, value = node.get("id", ""), node.get("value")
        if isinstance(fid, str) and (fid.endswith("_count") or fid.endswith("_amount")) and value is not None:
            typed += 1
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or (fid.endswith("_count") and (value < 0 or value != int(value))):
                failures["count_amount"].append(fid)
        if isinstance(node.get("inputs"), dict) and isinstance(node.get("value"), list):
            for calc in node["value"]:
                if not isinstance(calc, dict) or "formula" not in calc:
                    continue
                inputs = [node["inputs"].get(k) for k in calc.get("input_refs", [])]
                if len(inputs) != 2 or any(not isinstance(i, dict) for i in inputs):
                    failures["valid_denominator"].append(calc.get("id")); continue
                if "/" in calc["formula"]:
                    ratios += 1
                    a, b = (i.get("value") for i in inputs)
                    invalid = a is None or b is None or not isinstance(b, (int, float)) or b <= 0
                    if invalid and calc.get("value") is not None:
                        failures["valid_denominator"].append(calc.get("id"))
                if calc.get("value") is not None and calc["formula"] in ("a/b", "a/b*100"):
                    years = [i.get("year") for i in inputs]
                    if all(y is not None for y in years):
                        periods += 1
                        if years[0] != years[1]:
                            failures["period_alignment"].append(calc.get("id"))
    for name, count in (("source_values", verified), ("count_amount", typed), ("valid_denominator", ratios), ("period_alignment", periods)):
        results.append(check(name, not failures[name] if count or failures[name] else None,
                             {"checked": count, "failures": failures[name]}))
    return results


def grade(row, session, docs, latency_ms=60000):
    if "error" in row:
        return [check("runtime_completed", False, row["error"])]
    response, before, after = row["response"], row["before"], row["after"]
    meta = response["metadata"]
    names = [t["name"] for t in row["tools"]]
    trusted = after.get("trusted_context") or {}
    comp = after.get("comparison_context") or {}
    active = (after.get("active_company") or {}).get("inn")
    expected_active = session["aliases"]["A"]
    allowed = set(session["aliases"].values())
    out = [check("no_fallback", meta["synthesis"] == "model" and meta["grounding_status"] != "fallback" and not meta.get("error_code"),
                 {k: meta.get(k) for k in ("synthesis", "error_code", "grounding_status")}),
           check("model_tool_budget", meta["model_calls"] <= 5 and sum(c["kind"] == "master" for c in row["calls"]) <= 5 and len(names) <= 1 and meta["tool_calls"] == len(names)),
           check("latency", row["wall_ms"] <= latency_ms, {"actual_ms": row["wall_ms"], "threshold_ms": latency_ms}),
           check("correct_active_company", active == expected_active if session["mode"] == "solo" else active is None or active in allowed, {"actual": active, "expected": expected_active if session["mode"] == "solo" else sorted(allowed)}),
           check("no_repeated_full_check", "full_company_check" not in names if before.get("trusted_context") or before.get("comparison_context") else None),
           check("company_context_not_mixed", all((d.get("company") or {}).get("inn") == (trusted.get("company") or {}).get("inn") for d in (trusted.get("domains") or {}).values())),
           check("tool_company_allowlist", all(set([t["arguments"]["inn"]] if "inn" in t["arguments"] else t["arguments"].get("inns", [])) <= allowed for t in row["tools"]))]
    # Expected tool is defined by source scenario contract, not inferred from answer wording.
    required = None
    if row["case_id"] in ("K01", "S02_01") or row["case_id"].startswith("F-") or row.get("setup"):
        required = "compare_companies" if session["mode"] == "comparison" else "full_company_check"
    if row["case_id"] in ("K15", "M03.1", "M07.1", "S10_01", "S10_02", "S14_01"):
        required = "compare_companies"
    out.append(check("expected_tool", required in names if required else None, required))
    expected_domain = None
    if row["case_id"] in ("K05", "K06") or row["case_id"].startswith("S03_"):
        expected_domain = "finance"
    elif row["case_id"] in ("K08", "K09", "K11") or row["case_id"].startswith(("S04_", "S05_")):
        expected_domain = "legal"
    if row.get("setup"):
        expected_domain = None
    if expected_domain:
        expected_tool = "get_financial_data" if expected_domain == "finance" else "get_legal_data"
        out.append(check("targeted_tool_or_trusted_domain", (not names and expected_domain in trusted.get("domains", {})) or names == [expected_tool], expected_domain))
    forbid = row["case_id"] in ("K02", "K03", "K04", "M01.2", "M01.3", "M01.4", "M03.3")
    out.append(check("forbidden_tool", not names if forbid else None, "All tools forbidden for explanation with existing context"))
    out.append(check("context_reuse", bool(before.get("trusted_context") or before.get("comparison_context")) and not names if forbid else None))
    known_rows = list(walk({"before": before, "after": after, "tools": row["tools"]}))
    known_ids = {n[k] for n in known_rows for k in ("id", "fact_id") if isinstance(n.get(k), str)}
    response_ids = {e["fact_id"] for e in response.get("evidence", [])}
    mismatched = []
    for evidence in response.get("evidence", []):
        candidates = [n["display_value"] for n in known_rows if n.get("fact_id", n.get("id")) == evidence["fact_id"] and "display_value" in n]
        if not candidates or evidence["display_value"] not in candidates:
            mismatched.append(evidence["fact_id"])
    out.append(check("verified_value_in_trusted_context", response_ids <= known_ids and not mismatched if response_ids else None,
                     {"missing_fact_ids": sorted(response_ids - known_ids), "unmatched_display_values": mismatched, "checked": len(response_ids)}))
    fixture = session.get("fixture")
    if fixture:
        contract = session.get("fixture_contract")
        matches = all(at(docs[expected_active], e["path"]) == {k: v for k, v in e.items() if k != "path"} for e in contract["evidence"]) if contract else None
        out.append(check("fixture_source_requirement", matches, fixture))
        if fixture == "missing_profit":
            fs = docs[expected_active]["report"]["finReports"]
            latest = max(fs, key=lambda f: f["common"]["year"])
            out.append(check("missing_field_really_missing", latest["common"].get("profit") is None, latest["common"]))
    expected_comp = None
    if required == "compare_companies":
        expected_comp = {inn for inn in allowed if inn in row["question"]}
    comp_inns = {c["inn"] for c in comp.get("companies", [])}
    out.append(check("comparison_companies", comp_inns == expected_comp if expected_comp else None, {"actual": sorted(comp_inns), "expected": sorted(expected_comp or [])}))
    out.extend(structured_checks(trusted, docs))
    if comp:
        for company in comp.get("companies", []):
            out.extend(structured_checks(company, docs))
    for tool in row["tools"]:
        data = tool.get("result", {}).get("data") or {}
        if data:
            out.extend(structured_checks(data, docs))
    # Domain ToolResult comparison periods refer to source finance years.
    for tool in row["tools"]:
        data = tool.get("result", {}).get("data") or {}
        if tool["name"] == "compare_companies" and data.get("companies"):
            companies = data["companies"]
            for measure, path in {"proceeds": ["common", "proceeds"], "profit": ["common", "profit"], "capitals": ["liabilities", "capitals"], "accounts_payable": ["liabilities", "shortTermLiabilities", "accountsPayable"]}.items():
                years = [{f["common"]["year"] for f in docs[c["inn"]]["report"].get("finReports", []) if at(f, path).get("value") is not None} for c in companies]
                common = set.intersection(*years)
                expected = max(common) if common else None
                out.append(check("comparison_period_alignment", all(c.get("comparison_periods", {}).get(measure) == expected for c in companies), {"measure": measure, "expected_year": expected}))
    return out
