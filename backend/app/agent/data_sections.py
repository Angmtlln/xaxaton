"""Source-bound domain sections and finite arithmetic, without another LLM stage."""
from __future__ import annotations

import math
import json
from datetime import date, datetime
from decimal import Decimal

from app.infrastructure.mongo import num, parse_date, unwrap
from .models import DataSection, FullCheckCompany

PAGE_SIZE = 5


def report_of(snapshot):
    document = snapshot.get("document") or {}
    value = document.get("report", document) if isinstance(document, dict) else {}
    return value if isinstance(value, dict) else {}


def safe_value(value):
    value = unwrap(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value) if value.is_finite() else None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, str):
        return value.replace("<", "‹").replace(">", "›")[:500]
    if isinstance(value, dict):
        return {str(k): safe_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [safe_value(v) for v in value]
    return value


def numeric(row, path):
    value = row
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return None, "missing"
        value = value[key]
    if value is None:
        return None, "null"
    try:
        parsed = num(value)
        valid = not isinstance(value, bool) and parsed is not None and parsed.is_finite()
        result = float(parsed) if valid else None
        if result is None or not math.isfinite(result):
            return None, "invalid"
        return result, "data"
    except (ValueError, TypeError, OverflowError):
        return None, "invalid"


def company_from_snapshot(snapshot, inn=None):
    report = report_of(snapshot)
    base = report.get("baseInfo") or {}
    base = base if isinstance(base, dict) else {}
    status = report.get("status") or {}
    status = status if isinstance(status, dict) else {}
    reg = base.get("registrationInfo") or {}
    reg = reg if isinstance(reg, dict) else {}
    values = {
        "inn": inn or snapshot.get("inn") or base.get("inn"),
        "ogrn": snapshot.get("ogrn") or base.get("ogrn"),
        "short_name": snapshot.get("short_name") or base.get("shortName"),
        "full_name": snapshot.get("full_name") or base.get("fullName"),
        "address": snapshot.get("address") or base.get("address"),
        "status": snapshot.get("status") or status.get("status"),
        "status_reason": status.get("reasonName"), "status_date": status.get("date"),
        "registration_date": snapshot.get("registration_date") or reg.get("registrationDate"),
        "years_from_registration": snapshot.get("years_from_registration", reg.get("yearsFromRegistration")),
        "risk_level": snapshot.get("risk_level") or base.get("riskLevel"),
        "zsk_risk_level": snapshot.get("zsk_risk_level") or report.get("zskRiskLevel"),
        "report_date": snapshot.get("report_date") or report.get("reportDate"),
        "snapshot_id": str(snapshot["snapshot_id"]) if snapshot.get("snapshot_id") is not None else None,
    }
    for key, value in values.items():
        if key == "years_from_registration":
            value, _ = numeric({"v": value}, "v")
            values[key] = int(value) if value is not None and value.is_integer() else None
        elif value is not None:
            values[key] = str(safe_value(value))
    return FullCheckCompany.model_validate(values)


def source_section(report, key, fields, offset=0):
    path = "report." + key
    raw = report
    for part in key.split("."):
        if not isinstance(raw, dict) or part not in raw:
            return DataSection(field_ref=path, state="missing")
        raw = raw[part]
    if raw is None:
        return DataSection(field_ref=path, state="null")
    if isinstance(raw, dict):
        return DataSection(field_ref=path, value=safe_value({k: raw[k] for k in fields if k in raw}))
    if not isinstance(raw, list) or any(not isinstance(x, dict) for x in raw):
        return DataSection(field_ref=path, state="invalid")
    selected = raw[offset:offset + PAGE_SIZE]
    end = offset + len(selected)
    return DataSection(field_ref=path, state="data" if raw else "empty",
                       value=[safe_value({k: x[k] for k in fields if k in x}) for x in selected],
                       total=len(raw), included=len(selected), offset=offset,
                       truncated=offset > 0 or end < len(raw),
                       next_offset=end if end < len(raw) else None,
                       scope="source order; total is snapshot records, not unique real-world events")


# Only named domain sections, never arbitrary paths supplied by a model.
SECTION_FIELDS = {
    "connections": ("relatedCompanies", ("inn", "ogrn", "name", "registrationDate")),
    "founders": ("foundersInfo", ("shareCapital",)),
    "cofounders": ("foundersInfo.cofounders", ("name", "inn", "amount", "share", "dateFrom", "active")),
    "parents": ("foundersInfo.parentOrganizations", ("inn", "ogrn", "fullName", "parentDate")),
    "branches": ("branchesInfo.branches", ("name", "address")),
    "tax": ("taxSystem", ("shortName", "fullName")),
    "coefficients": ("coefficient", ("year", "sustainability", "solvency", "profitability")),
    "profile": ("baseInfo", ("companySize", "website", "staff")),
    "activity": ("kindsOfActivityInfo", ("mainKindOfActivity",)),
    "activity_other": ("kindsOfActivityInfo.otherKindsOfActivity", ("code", "name", "description")),
    "positive": ("reputationalRisks.positive", ("code", "name", "chapter")),
    "negative": ("reputationalRisks.negative", ("code", "name", "chapter")),
    "licenses": ("licenses", ("name", "number", "status", "issuingAuthority", "issueDate", "endDate")),
    "procurements": ("procurements", ("procurementsYear", "federalLawCode", "tenderWinnerCnt", "contractSignedCnt", "contractSignedAmt")),
    "inspections": ("inspections", ("erpId", "type", "form", "authorityName", "startDate", "endDate", "inspectionStatus")),
}


def profile_sections(snapshot, section="default", offset=0):
    report = report_of(snapshot)
    keys = {
        "default": ("profile", "activity", "positive", "negative"),
        "profile": ("profile", "activity", "positive", "negative", "licenses", "procurements", "founders", "cofounders", "tax"),
        "connections": ("connections", "parents", "branches", "founders", "cofounders"),
        "finance": ("coefficients",),
        "activity": ("activity", "activity_other"),
        "signals": ("positive", "negative"),
        "licenses": ("licenses",), "procurements": ("procurements",),
        "inspections": ("inspections",),
    }.get(section, ())
    sections = {key: source_section(report, *SECTION_FIELDS[key], offset) for key in keys}
    if "connections" in sections and isinstance(report.get("relatedCompanies"), list):
        parent_rows = []
        for index, raw in enumerate(report["relatedCompanies"]):
            if not isinstance(raw, dict): continue
            parents = raw.get("parentOrganizations")
            if not isinstance(parents, list): continue
            for parent_index, parent in enumerate(parents):
                if isinstance(parent, dict):
                    parent_rows.append({"related_company_inn": safe_value(raw.get("inn")),
                        "field_ref": "report.relatedCompanies[%s].parentOrganizations[%s]" % (index, parent_index),
                        **{k: safe_value(parent[k]) for k in ("inn", "ogrn", "fullName", "parentDate") if k in parent}})
        chosen = parent_rows[offset:offset + PAGE_SIZE]
        sections["connection_parents"] = DataSection(field_ref="report.relatedCompanies[].parentOrganizations[]",
            value=chosen, state="data" if parent_rows else "empty", total=len(parent_rows),
            included=len(chosen), offset=offset, truncated=offset > 0 or offset + len(chosen) < len(parent_rows),
            next_offset=offset + len(chosen) if offset + len(chosen) < len(parent_rows) else None,
            scope="flattened corporate parents in source order; independent of connection page")
    if section in {"activity", "profile", "default"}:
        kinds = report.get("kindsOfActivityInfo") or {}
        if isinstance(kinds, dict):
            other = kinds.get("otherKindsOfActivity")
            main = kinds.get("mainKindOfActivity") or {}
            sections["activity_count"] = DataSection(field_ref="report.kindsOfActivityInfo",
                value={"count": int(bool(main.get("code"))) + len(other) if isinstance(other, list) and isinstance(main, dict) else None,
                       "meaning": "number of codes; not the source massOkved marker"})
    if section in {"signals", "profile", "default", "activity"}:
        markers = report.get("reputationalRisks") or {}
        found = [{"polarity": polarity, **{k: safe_value(row.get(k)) for k in ("code", "name", "chapter")}}
                 for polarity in ("positive", "negative") for row in (markers.get(polarity) or [])
                 if isinstance(row, dict) and row.get("code") == "massOkved"] if isinstance(markers, dict) else []
        sections["mass_okved_source"] = DataSection(field_ref="report.reputationalRisks",
            value=found, scope="source description may contradict case clarification; distinct from code count, never infer fictitious business")
    # Preserve both dates; an imported snapshot column need not equal raw reportDate.
    sections["source_dates"] = DataSection(field_ref="report.reportDate; snapshot.report_date",
        value={"report_date": safe_value(report.get("reportDate")),
               "snapshot_report_date": safe_value(snapshot.get("report_date"))})
    available = {}
    for name, (path, fields) in SECTION_FIELDS.items():
        item = source_section(report, path, fields)
        available[name] = {"state": item.state, "records": item.total,
                           "section": "signals" if name in {"positive", "negative"} else "activity" if name == "activity_other" else "connections" if name in {"parents", "branches", "founders", "cofounders"} else "profile" if name == "tax" else "finance" if name == "coefficients" else name}
    sections["available_sections"] = DataSection(field_ref="report", value=available,
        scope="read via existing get_financial_data/get_legal_data: section, year, offset; page size 5; full source remains in repository")
    return sections


def calculation(name, formula, inputs, unit, *, reason=None):
    """Inputs carry values and original row paths. A missing input never means zero."""
    values = [x["value"] for x in inputs]
    result = None
    if reason is None and any(x is None for x in values):
        reason = "missing_or_invalid_input"
    if reason is None:
        if formula == "a-b": result = values[0] - values[1]
        elif values[1] <= 0: reason = "nonpositive_denominator"
        elif formula == "(a/b-1)*100": result = (values[0] / values[1] - 1) * 100
        elif formula == "a/b*100": result = values[0] / values[1] * 100
        elif formula == "a/b": result = values[0] / values[1]
    if result is not None and not math.isfinite(result):
        result, reason = None, "nonfinite_result"
    return {"id": name, "formula": formula, "version": "balance-1", "inputs": inputs,
            "unit": unit, "value": round(result, 2) if result is not None else None,
            "state": "not_calculable" if reason else "data", "reason": reason}


def calculated_section(values, *, field_ref, scope):
    inputs, keys = {}, {}
    for item in values:
        refs = []
        for value in item.pop("inputs"):
            encoded = json.dumps(value, sort_keys=True, ensure_ascii=False)
            if encoded not in keys:
                key = "i%s" % len(keys)
                keys[encoded] = key
                inputs[key] = value
            refs.append(keys[encoded])
        item["input_refs"] = refs
    return DataSection(field_ref=field_ref, value=values, inputs=inputs, scope=scope)


def finance_calculations(rows, paths):
    if not rows:
        return DataSection(field_ref="report.finReports[]", state="not_calculable", value=[])
    last = rows[-1]
    def inp(row, key):
        return {"field_ref": "report.finReports[%s].%s" % (row["source_index"], paths[key]),
                "value": row[key], "year": row["year"], "unit": "руб"}
    result = []
    for name, a, b, formula, unit in (
        ("working_capital", "current_assets", "short_term_total", "a-b", "руб"),
        ("current_ratio", "current_assets", "short_term_total", "a/b", "ratio"),
        ("capital_to_assets_pct", "capitals", "total_assets", "a/b*100", "%"),
        ("receivables_to_current_assets_pct", "receivables", "current_assets", "a/b*100", "%"),
        ("cash_to_current_assets_pct", "bankroll", "current_assets", "a/b*100", "%"),
        ("profit_to_proceeds_pct", "profit", "proceeds", "a/b*100", "%"),
        ("short_term_to_liabilities_pct", "short_term_total", "total_liabilities", "a/b*100", "%"),
        ("long_term_to_liabilities_pct", "long_term_total", "total_liabilities", "a/b*100", "%"),
        ("stocks_to_current_assets_pct", "stocks", "current_assets", "a/b*100", "%"),
        ("payables_to_proceeds_pct", "accounts_payable", "proceeds", "a/b*100", "%"),
    ):
        result.append(calculation(name, formula, [inp(last, a), inp(last, b)], unit))
    if len(rows) >= 2:
        prev = rows[-2]
        for key in ("proceeds", "profit", "capitals", "accounts_payable", "total_assets", "receivables"):
            inputs = [inp(last, key), inp(prev, key)]
            result.append(calculation(key + "_change", "a-b", inputs, "руб"))
            result.append(calculation(key + "_change_pct", "(a/b-1)*100", inputs, "%",
                          reason="nonconsecutive_years" if last["year"] != prev["year"] + 1 else None))
    return calculated_section(result, field_ref="report.finReports[]",
                       scope="latest disclosed years; RUB follows existing import convention; profit means profit/loss, not necessarily net income; bankroll means cash and equivalents")


def legal_sections(snapshot, *, year=None, offset=0, section="default"):
    report = report_of(snapshot)
    sections = profile_sections(snapshot, section if section not in {"default", "legal", "proceedings"} else "core", offset)
    cases = report.get("arbitrationCases")
    if isinstance(cases, list):
        rows = []
        for index, raw in enumerate(cases):
            if not isinstance(raw, dict):
                continue
            row = {"source_index": index, "field_states": {}}
            for key in ("year", "defendantCount", "defendantAmount", "plaintiffCount", "plaintiffAmount"):
                value, state = numeric(raw, key)
                if value is not None and (value < 0 or (key in {"year", "defendantCount", "plaintiffCount"} and not value.is_integer())):
                    value, state = None, "invalid"
                row[key] = value
                if state != "data": row["field_states"][key] = state
            if year is None or row["year"] == year:
                rows.append(row)
        included = rows[offset:offset + PAGE_SIZE]
        sections["court_years"] = DataSection(field_ref="report.arbitrationCases[]", value=included,
            total=len(rows), included=len(included), offset=offset,
            truncated=offset > 0 or offset + len(included) < len(rows),
            next_offset=offset + len(included) if offset + len(included) < len(rows) else None,
            scope="source order; RUB amounts and counts; full-year coverage unknown; no automatic trend")
    else:
        sections["court_years"] = DataSection(field_ref="report.arbitrationCases", state="null" if cases is None and "arbitrationCases" in report else "missing" if cases is None else "invalid")
    summary = report.get("arbitrationByStatus")
    stages = []
    for role, group, prefix in (("defendant", "defandantArbitration", "d"), ("plaintiff", "plaintiffArbitration", "p")):
        for stage, suffix in (("Pending", "p"), ("Appealed", "a"), ("Finished", "f")):
            path = "%s.%s%s" % (group, group, stage)
            count, count_state = numeric(summary, path + "." + prefix + suffix + "Count")
            amount, amount_state = numeric(summary, path + "." + prefix + suffix + "Amount")
            if count is not None and (count < 0 or not count.is_integer()): count, count_state = None, "invalid"
            if amount is not None and amount < 0: amount, amount_state = None, "invalid"
            stages.append(dict(role=role, stage=stage, count=count, amount=amount,
                               field_states=dict(count=count_state, amount=amount_state),
                               field_ref="report.arbitrationByStatus." + path))
    sections["court_stages"] = DataSection(field_ref="report.arbitrationByStatus", value=stages,
        scope="counts and RUB amounts; stages must not be added as unique obligations")
    proceedings = report.get("executionProceedings")
    if isinstance(proceedings, list):
        indexed = []
        for index, raw in enumerate(proceedings):
            if not isinstance(raw, dict): continue
            try: day = parse_date(raw.get("date"))
            except (TypeError, ValueError, OverflowError, OSError): day = None
            if year is None or (day is not None and day.year == year):
                amount, state = numeric(raw, "amount")
                if amount is not None and amount < 0: amount, state = None, "invalid"
                indexed.append(dict(source_index=index, number=safe_value(raw.get("number")),
                    date=day.isoformat() if day else None, active=raw.get("active") if isinstance(raw.get("active"), bool) else None,
                    amount=amount, amount_state=state))
        indexed.sort(key=lambda row: row["date"] or "", reverse=True)
        chosen = indexed[offset:offset + PAGE_SIZE]
        sections["proceedings"] = DataSection(field_ref="report.executionProceedings[]", value=chosen,
            state="data" if indexed else "empty", total=len(indexed), included=len(chosen), offset=offset,
            truncated=offset > 0 or offset + len(chosen) < len(indexed),
            next_offset=offset + len(chosen) if offset + len(chosen) < len(indexed) else None,
            scope="date descending; all activity states; RUB amount is not outstanding balance; year=%s" % year)
    else:
        sections["proceedings"] = DataSection(field_ref="report.executionProceedings", state="null" if proceedings is None and "executionProceedings" in report else "missing" if proceedings is None else "invalid")
    sections["inspections"] = source_section(report, *SECTION_FIELDS["inspections"], offset)
    return sections


def claim_scale(finance, legal):
    rows = finance.facts.get("fin.series")
    rows = rows.value if rows else []
    calculations = []
    if rows:
        last = rows[-1]
        paths = finance.sections["finance_scope"].value["paths"]
        numerators = []
        for numerator, role, stage in (("court.defendant_amount", "defendant", "all_disclosed_years"),
                                        ("execproc.active_amount", None, "active")):
            fact = legal.facts.get(numerator)
            numerators.append((numerator, dict(value=fact.value if fact else None,
                field_ref=fact.field_ref if fact else numerator, unit="руб",
                report_date=legal.company.report_date, role=role, stage=stage)))
        # Stage amounts have no year key; never join them to arbitrationCases years.
        pending = next((row for row in legal.sections["court_stages"].value
                        if row["role"] == "defendant" and row["stage"] == "Pending"), None)
        numerators.append(("court.defendant_pending_amount", dict(
            value=pending["amount"] if pending else None,
            field_ref="report.arbitrationByStatus.defandantArbitration.defandantArbitrationPending.dpAmount",
            unit="руб", report_date=legal.company.report_date, role="defendant", stage="Pending")))
        for numerator, source in numerators:
            for base in ("total_assets", "capitals", "proceeds"):
                inputs = [source,
                          dict(value=last.get(base), field_ref="report.finReports[%s].%s" % (last["source_index"], paths[base]),
                               unit="руб", year=last["year"])]
                calculations.append(calculation(numerator + "_to_" + base + "_pct", "a/b*100", inputs, "%"))
    return calculated_section(calculations, field_ref="report.arbitrationCases[]; report.arbitrationByStatus; report.executionProceedings[]; report.finReports[]",
        scope="scale only; numerator role/stage in inputs; annual denominator year is not the case year; claims/proceedings may overlap")


def finance_source_commentary(snapshot):
    """Expose source finance prose alongside numbers, without endorsing or rewriting it."""
    report = report_of(snapshot)
    risks = report.get("reputationalRisks") or {}
    rows = []
    for polarity in ("positive", "negative"):
        raw = risks.get(polarity) if isinstance(risks, dict) else None
        for index, item in enumerate(raw if isinstance(raw, list) else []):
            if isinstance(item, dict) and item.get("chapter") == "finance":
                rows.append({"field_ref": "report.reputationalRisks.%s[%s]" % (polarity, index),
                             "polarity": polarity,
                             **{key: safe_value(item.get(key)) for key in ("code", "name", "chapter")}})
    return DataSection(field_ref="report.reputationalRisks", value=rows[:PAGE_SIZE],
        state="data" if rows else "empty", total=len(rows), included=min(len(rows), PAGE_SIZE),
        truncated=len(rows) > PAGE_SIZE,
        scope="source commentary, not verified conclusions; filtered by chapter=finance; original paths preserved")
