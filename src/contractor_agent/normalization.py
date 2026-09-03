"""Нормализация GetFullReportResponse в бизнес-профиль контрагента.

Модуль использует только детерминированные преобразования. Он не выставляет
итоговый риск и не содержит LLM-зависимостей.
"""

from __future__ import annotations

import json
import math
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .models import (
    BankRisk,
    BusinessProfile,
    CompanyIdentity,
    Compliance,
    Conflict,
    DataQuality,
    DerivedMetric,
    Enforcement,
    FinancialHealth,
    LegalRisks,
    NormalizedCompanyProfile,
    Ownership,
    Procurement,
    RelatedCompanies,
    RiskSignal,
    SignalEvidence,
)


TRANSACTION_AMOUNT_UNIT = "RUB"

_MONGO_NUMBER_KEYS = ("$numberLong", "$numberInt", "$numberDouble", "$numberDecimal")
_CHAPTER_DOMAINS = {
    "arbitr": "LEGAL",
    "execproc": "ENFORCEMENT",
    "filials": "BUSINESS_PROFILE",
    "finance": "FINANCE",
    "license": "COMPLIANCE",
    "manager": "OWNERSHIP",
    "okved": "BUSINESS_PROFILE",
    "reestrs": "REGISTRY",
    "relatedcomp": "RELATED_COMPANIES",
    "site": "COMPANY_IDENTITY",
}
_PRESENCE_SIGNAL_POLARITY = {
    "ARBITRATION_DEFENDANT": "NEGATIVE",
    "EXECUTION_PROCEEDINGS": "NEGATIVE",
    "BRANCHES_INFO": "POSITIVE",
    "GOVERNMENT_CONTRACT": "POSITIVE",
    "LICENSES": "POSITIVE",
    "RELATED_COMPANIES": "POSITIVE",
    "WEB_SITE": "POSITIVE",
}
_SOURCE_SIGNAL_RULE_VERSION = "source-signal/v1"


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number(value: Any) -> int | float | None:
    """Приводит Mongo-числа и числовые строки к int/float."""
    if isinstance(value, dict):
        if value.get("unit") == "RUB" and "value" in value:
            return _number(value["value"])
        for key in _MONGO_NUMBER_KEYS:
            if key in value:
                return _number(value[key])
        return None
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        normalized = (
            value.strip()
            .replace("\u00a0", "")
            .replace(" ", "")
            .replace(",", ".")
        )
        if not normalized:
            return None
        try:
            parsed = float(normalized)
        except ValueError:
            return None
        if not math.isfinite(parsed):
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _money(value: Any) -> dict[str, Any]:
    """Возвращает единый формат денежного значения."""
    return {"value": _number(value), "unit": TRANSACTION_AMOUNT_UNIT}


def _date_scalar(value: Any) -> Any:
    if isinstance(value, dict) and "$date" in value:
        return _date_scalar(value["$date"])
    if isinstance(value, dict):
        for key in _MONGO_NUMBER_KEYS:
            if key in value:
                return _number(value[key])
    return value


def _parse_datetime(value: Any) -> datetime | None:
    raw = _date_scalar(value)
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            return datetime.fromtimestamp(float(raw) / 1000, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    normalized = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        return datetime.fromisoformat(normalized)
    except ValueError:
        for pattern in ("%d.%m.%Y", "%Y-%m-%d"):
            try:
                return datetime.strptime(text, pattern)
            except ValueError:
                continue
    return None


def _normalized_date(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    if parsed is None:
        return _text(_date_scalar(value))
    if parsed.tzinfo is not None:
        return parsed.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    if parsed.time() == datetime.min.time():
        return parsed.date().isoformat()
    return parsed.isoformat(timespec="milliseconds")


def _date_only(value: Any) -> date | None:
    parsed = _parse_datetime(value)
    return parsed.date() if parsed else None


def _years_between(start: Any, end: Any) -> float | None:
    start_date = _date_only(start)
    end_date = _date_only(end)
    if start_date is None or end_date is None or end_date < start_date:
        return None
    return round((end_date - start_date).days / 365.2425, 2)


def _get(root: Any, *keys: str) -> Any:
    current = root
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return current


def _canonical_signal_code(value: Any) -> str:
    text = _text(value) or "UNKNOWN_SIGNAL"
    text = text.translate(str.maketrans({"а": "a", "А": "A"}))
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", text)
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").upper()


def _normalized_name(value: Any) -> str:
    text = (_text(value) or "").casefold().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", " ", text).strip()


def _metric(
    name: str,
    value: Any,
    source_fields: Sequence[str],
    formula: str,
    *,
    status: str = "CALCULATED",
    unit: str | None = None,
) -> DerivedMetric:
    return DerivedMetric(
        metric=name,
        value=value,
        source_fields=list(source_fields),
        formula=formula,
        status=status,  # type: ignore[arg-type]
        unit=unit,
    )


def _missing_metric(
    name: str,
    source_fields: Sequence[str],
    formula: str,
    *,
    status: str = "NOT_AVAILABLE",
    unit: str | None = None,
) -> DerivedMetric:
    return _metric(name, None, source_fields, formula, status=status, unit=unit)


def _record_id(record: dict[str, Any]) -> str | None:
    raw_id = record.get("_id")
    if isinstance(raw_id, dict):
        return _text(raw_id.get("ogrn") or raw_id.get("$oid"))
    return _text(raw_id)


def _normalize_signal(
    item: dict[str, Any], polarity: str, index: int
) -> RiskSignal:
    chapter = (_text(item.get("chapter")) or "").casefold()
    signal_type = polarity.upper()
    source_path = f"report.reputationalRisks.{polarity}[{index}]"
    code = _canonical_signal_code(item.get("code"))
    return RiskSignal(
        code=code,
        domain=_CHAPTER_DOMAINS.get(chapter, "COMPLIANCE"),
        type=signal_type,  # type: ignore[arg-type]
        impact_level="LOW" if signal_type == "POSITIVE" else "MEDIUM",
        origin="SOURCE_SIGNAL",
        description=_text(item.get("name")) or code,
        evidence=[
            SignalEvidence(
                source_type="SOURCE_SIGNAL",
                source_path=source_path,
                metric="reputational_signal_code",
                value=code,
            )
        ],
        rule="Сигнал перенесён из reputationalRisks без изменения полярности.",
        rule_version=_SOURCE_SIGNAL_RULE_VERSION,
    )


def _normalize_founder(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "inn": _text(item.get("inn")),
        "name": _text(item.get("name")),
        "share_percent": _number(item.get("share")),
        "contribution_amount": _money(item.get("amount")),
        "active": item.get("active") if isinstance(item.get("active"), bool) else None,
        "date_from": _normalized_date(item.get("dateFrom")),
    }


def _normalize_financial_statement(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "year": _number(_get(item, "common", "year")),
        "revenue": _money(_get(item, "common", "proceeds")),
        "profit": _money(_get(item, "common", "profit")),
        "assets": {
            "total": _money(_get(item, "assets", "totalAssets")),
            "current": _money(_get(item, "assets", "currentAssets", "total")),
            "non_current": _money(_get(item, "assets", "uncurrentAssets", "total")),
            "receivables": _money(
                _get(item, "assets", "currentAssets", "receivables")
            ),
            "cash": _money(_get(item, "assets", "currentAssets", "bankroll")),
            "stocks": _money(_get(item, "assets", "currentAssets", "stocks")),
        },
        "liabilities": {
            "balance_total": _money(_get(item, "liabilities", "totalLiabilities")),
            "capital": _money(_get(item, "liabilities", "capitals")),
            "short_term": _money(
                _get(item, "liabilities", "shortTermLiabilities", "total")
            ),
            "long_term": _money(
                _get(item, "liabilities", "longTermDuties", "total")
            ),
            "accounts_payable": _money(
                _get(item, "liabilities", "shortTermLiabilities", "accountsPayable")
            ),
        },
    }


def _normalize_status_bucket(
    item: Any, count_key: str, amount_key: str
) -> dict[str, Any]:
    bucket = _as_dict(item)
    return {
        "count": _number(bucket.get(count_key)),
        "amount_rub": _money(bucket.get(amount_key)),
    }


def _normalize_arbitration_by_status(value: Any) -> dict[str, Any]:
    raw = _as_dict(value)
    defendant = _as_dict(raw.get("defandantArbitration"))
    plaintiff = _as_dict(raw.get("plaintiffArbitration"))
    return {
        "common_count": _number(raw.get("commonCount")),
        "common_amount_rub": _money(raw.get("commonAmount")),
        "defendant": {
            "finished": _normalize_status_bucket(
                defendant.get("defandantArbitrationFinished"), "dfCount", "dfAmount"
            ),
            "appealed": _normalize_status_bucket(
                defendant.get("defandantArbitrationAppealed"), "daCount", "daAmount"
            ),
            "pending": _normalize_status_bucket(
                defendant.get("defandantArbitrationPending"), "dpCount", "dpAmount"
            ),
        },
        "plaintiff": {
            "finished": _normalize_status_bucket(
                plaintiff.get("plaintiffArbitrationFinished"), "pfCount", "pfAmount"
            ),
            "appealed": _normalize_status_bucket(
                plaintiff.get("plaintiffArbitrationAppealed"), "paCount", "paAmount"
            ),
            "pending": _normalize_status_bucket(
                plaintiff.get("plaintiffArbitrationPending"), "ppCount", "ppAmount"
            ),
        },
    }


def _series(
    rows: list[tuple[int, dict[str, Any]]], path: Sequence[str]
) -> list[tuple[int | float, int | float, int]]:
    result: list[tuple[int | float, int | float, int]] = []
    for source_index, item in rows:
        year = _number(_get(item, "common", "year"))
        value = _number(_get(item, *path))
        if year is not None and value is not None:
            result.append((year, value, source_index))
    return sorted(result, key=lambda row: row[0])


def _latest_pair(
    rows: list[tuple[int, dict[str, Any]]],
    numerator_path: Sequence[str],
    denominator_path: Sequence[str],
) -> tuple[int | float, int | float, int | float, int] | None:
    candidates: list[tuple[int | float, int | float, int | float, int]] = []
    for source_index, item in rows:
        year = _number(_get(item, "common", "year"))
        numerator = _number(_get(item, *numerator_path))
        denominator = _number(_get(item, *denominator_path))
        if year is not None and numerator is not None and denominator is not None:
            candidates.append((year, numerator, denominator, source_index))
    return max(candidates, default=None, key=lambda row: row[0])


def _trend_metric(
    name: str,
    values: list[tuple[int | float, int | float, int]],
    value_path: str,
) -> DerivedMetric:
    source_fields = [f"report.finReports[{index}].{value_path}" for _, _, index in values]
    if len(values) < 2:
        return _missing_metric(
            name,
            source_fields or [f"report.finReports[].{value_path}"],
            "Классификация последовательных изменений минимум за два года",
        )
    changes = [right[1] - left[1] for left, right in zip(values, values[1:])]
    if all(change > 0 for change in changes):
        trend = "GROWING"
    elif all(change < 0 for change in changes):
        trend = "DECLINING"
    elif all(change == 0 for change in changes):
        trend = "STABLE"
    else:
        trend = "MIXED"
    return _metric(
        name,
        trend,
        source_fields,
        "GROWING — все изменения положительные; DECLINING — отрицательные; "
        "STABLE — нулевые; иначе MIXED",
    )


def _ratio_metric(
    name: str,
    pair: tuple[int | float, int | float, int | float, int] | None,
    numerator_path: str,
    denominator_path: str,
    formula: str,
) -> DerivedMetric:
    generic_sources = [
        f"report.finReports[].{numerator_path}",
        f"report.finReports[].{denominator_path}",
    ]
    if pair is None:
        return _missing_metric(name, generic_sources, formula, unit="RATIO")
    _, numerator, denominator, source_index = pair
    sources = [
        f"report.finReports[{source_index}].{numerator_path}",
        f"report.finReports[{source_index}].{denominator_path}",
    ]
    if denominator == 0:
        return _missing_metric(
            name, sources, formula, status="NOT_APPLICABLE", unit="RATIO"
        )
    return _metric(
        name,
        round(numerator / denominator, 6),
        sources,
        formula,
        unit="RATIO",
    )


def _sum_optional(values: Iterable[Any]) -> tuple[int | float, bool]:
    total: int | float = 0
    complete = True
    for value in values:
        number = _number(value)
        if number is None:
            complete = False
        else:
            total += number
    return total, complete


def _status_count(by_status: dict[str, Any], role: str) -> int | float:
    return sum((by_status[role][status]["count"] or 0) for status in ("finished", "appealed", "pending"))


def _status_amount(
    by_status: dict[str, Any], role: str
) -> tuple[int | float, bool]:
    values: list[int | float | None] = []
    for status in ("finished", "appealed", "pending"):
        bucket = by_status[role][status]
        count = bucket["count"] or 0
        amount = _number(bucket["amount_rub"])
        if count == 0 and amount is None:
            amount = 0
        values.append(amount)
    return _sum_optional(values)


def _metric_value(metrics: dict[str, DerivedMetric], name: str) -> Any:
    metric = metrics.get(name)
    return metric.value if metric and metric.status in {"CALCULATED", "PARTIAL"} else None


def _metric_presence(
    metrics: dict[str, DerivedMetric], name: str
) -> bool | None:
    value = _metric_value(metrics, name)
    return None if value is None else bool(value)


def _add_source_conflicts(
    source_signals: list[RiskSignal],
    raw_presence: dict[str, tuple[bool | None, list[str]]],
) -> list[Conflict]:
    conflicts: list[Conflict] = []
    positive_codes = {item.code for item in source_signals if item.type == "POSITIVE"}
    negative_codes = {item.code for item in source_signals if item.type == "NEGATIVE"}

    for code in sorted(positive_codes & negative_codes):
        sources = [
            item.evidence[0].source_path
            for item in source_signals
            if item.code == code
        ]
        conflicts.append(
            Conflict(
                type="SOURCE_CONFLICT",
                code=code,
                description="Один и тот же reputational signal имеет обе полярности.",
                source_fields=sources,
            )
        )

    for code, (is_present, raw_sources) in raw_presence.items():
        if is_present is None or code not in _PRESENCE_SIGNAL_POLARITY:
            continue
        expected_when_present = _PRESENCE_SIGNAL_POLARITY[code]
        expected_polarity = (
            expected_when_present
            if is_present
            else ("POSITIVE" if expected_when_present == "NEGATIVE" else "NEGATIVE")
        )
        for signal in source_signals:
            if signal.code != code or signal.type == expected_polarity:
                continue
            conflicts.append(
                Conflict(
                    type="SOURCE_CONFLICT",
                    code=code,
                    description=(
                        f"Raw-признак {', '.join(raw_sources)} противоречит полярности "
                        "reputational signal."
                    ),
                    source_fields=[*raw_sources, signal.evidence[0].source_path],
                )
            )
    return conflicts


def normalize_record(record: dict[str, Any]) -> NormalizedCompanyProfile:
    """Преобразует одну сырую карточку в профиль из десяти бизнес-блоков."""
    report = _as_dict(record.get("report"))
    base = _as_dict(report.get("baseInfo"))
    status = _as_dict(report.get("status"))
    report_date_raw = report.get("reportDate")

    company_identity = CompanyIdentity(
        source_record_id=_record_id(record),
        inn=_text(base.get("inn")),
        ogrn=_text(base.get("ogrn")),
        name=_text(base.get("shortName") or base.get("fullName")),
        full_name=_text(base.get("fullName")),
        address=_text(base.get("address")),
        email=_text(base.get("email")),
        website=_text(base.get("website")),
        company_size=_text(base.get("companySize")),
        registration_date=_normalized_date(_get(base, "registrationInfo", "registrationDate")),
        reported_company_age_years=_number(
            _get(base, "registrationInfo", "yearsFromRegistration")
        ),
        status=_text(status.get("status")),
        status_date=_normalized_date(status.get("date")),
        status_reason=_text(status.get("reasonName")),
    )
    bank_risk = BankRisk(
        base_risk_level=_text(base.get("riskLevel")),
        zsk_risk_level=_text(report.get("zskRiskLevel")),
        report_date=_normalized_date(report_date_raw),
    )

    founders_info = _as_dict(report.get("foundersInfo"))
    raw_founders = _dict_items(founders_info.get("cofounders"))
    founders = [_normalize_founder(item) for item in raw_founders]
    auth_person = _as_dict(founders_info.get("authPerson"))
    director = None
    if auth_person:
        director = {
            "inn": _text(auth_person.get("inn")),
            "name": _text(auth_person.get("name")),
            "position": _text(auth_person.get("positionName")),
            "appointment_date": _normalized_date(auth_person.get("positionDate")),
        }
    ownership = Ownership(
        source_available=isinstance(report.get("foundersInfo"), dict),
        founders=founders,
        share_capital=_money(founders_info.get("shareCapital")),
        director=director,
    )

    raw_related = _dict_items(report.get("relatedCompanies"))
    related_items: list[dict[str, Any]] = []
    related_directors: list[str] = []
    for item in raw_related:
        director_name = _text(item.get("authPersonName"))
        if director_name:
            related_directors.append(director_name)
        related_items.append(
            {
                "inn": _text(item.get("inn")),
                "ogrn": _text(item.get("ogrn")),
                "name": _text(item.get("name")),
                "registration_date": _normalized_date(item.get("registrationDate")),
                "director_name": director_name,
                "director_position": _text(item.get("authPersonPosition")),
                "parent_organizations": [
                    {
                        "inn": _text(parent.get("inn")),
                        "ogrn": _text(parent.get("ogrn")),
                        "name": _text(parent.get("fullName")),
                        "relation_date": _normalized_date(parent.get("parentDate")),
                    }
                    for parent in _dict_items(item.get("parentOrganizations"))
                ],
            }
        )
    related_companies = RelatedCompanies(
        source_available=isinstance(report.get("relatedCompanies"), list),
        companies=related_items,
        directors=related_directors,
    )

    activities = _as_dict(report.get("kindsOfActivityInfo"))
    main_okved_raw = _as_dict(activities.get("mainKindOfActivity"))
    main_okved = None
    if main_okved_raw:
        main_okved = {
            "code": _text(main_okved_raw.get("code")),
            "description": _text(main_okved_raw.get("description")),
        }
    additional_okveds = [
        {"code": _text(item.get("code")), "description": _text(item.get("description"))}
        for item in _dict_items(activities.get("otherKindsOfActivity"))
    ]
    branches_info = _as_dict(report.get("branchesInfo"))
    branches = [
        {"name": _text(item.get("name")), "address": _text(item.get("address"))}
        for item in _dict_items(branches_info.get("branches"))
    ]
    tax_systems = [
        {"short_name": _text(item.get("shortName")), "full_name": _text(item.get("fullName"))}
        for item in _dict_items(report.get("taxSystem"))
    ]
    business_profile = BusinessProfile(
        main_okved=main_okved,
        additional_okveds=additional_okveds,
        branches_source_available=isinstance(report.get("branchesInfo"), dict),
        branches=branches,
        tax_systems=tax_systems,
    )

    raw_financial_rows = [
        (index, item) for index, item in enumerate(_dict_items(report.get("finReports")))
    ]
    normalized_financial_rows = [
        _normalize_financial_statement(item) for _, item in raw_financial_rows
    ]
    normalized_financial_rows.sort(key=lambda item: item["year"] or -math.inf)
    raw_coefficients = _as_dict(report.get("coefficient"))
    coefficients = {
        "year": _number(raw_coefficients.get("year")),
        "sustainability": _number(raw_coefficients.get("sustainability")),
        "solvency": _number(raw_coefficients.get("solvency")),
        "profitability": _number(raw_coefficients.get("profitability")),
    }
    financial_health = FinancialHealth(
        source_available=isinstance(report.get("finReports"), list),
        statements=normalized_financial_rows,
        coefficients=coefficients,
    )

    raw_yearly_cases = _dict_items(report.get("arbitrationCases"))
    yearly_cases = [
        {
            "year": _number(item.get("year")),
            "plaintiff_count": _number(item.get("plaintiffCount")),
            "plaintiff_amount_rub": _money(item.get("plaintiffAmount")),
            "defendant_count": _number(item.get("defendantCount")),
            "defendant_amount_rub": _money(item.get("defendantAmount")),
        }
        for item in raw_yearly_cases
    ]
    yearly_cases.sort(key=lambda item: item["year"] or -math.inf)
    arbitration_by_status = _normalize_arbitration_by_status(
        report.get("arbitrationByStatus")
    )
    legal_risks = LegalRisks(
        yearly_cases_source_available=isinstance(report.get("arbitrationCases"), list),
        yearly_cases=yearly_cases,
        by_status_source_available=isinstance(report.get("arbitrationByStatus"), dict),
        by_status=arbitration_by_status,
    )

    raw_executions = _dict_items(report.get("executionProceedings"))
    proceedings = [
        {
            "number": _text(item.get("number")),
            "date": _normalized_date(item.get("date")),
            "active": item.get("active") if isinstance(item.get("active"), bool) else None,
            "amount_rub": _money(item.get("amount")),
        }
        for item in raw_executions
    ]
    enforcement = Enforcement(
        source_available=isinstance(report.get("executionProceedings"), list),
        proceedings=proceedings,
    )

    reputational = _as_dict(report.get("reputationalRisks"))
    positive_signals = [
        _normalize_signal(item, "positive", index)
        for index, item in enumerate(_dict_items(reputational.get("positive")))
    ]
    negative_signals = [
        _normalize_signal(item, "negative", index)
        for index, item in enumerate(_dict_items(reputational.get("negative")))
    ]
    raw_inspections = _dict_items(report.get("inspections"))
    inspections = [
        {
            "erp_id": _text(item.get("erpId")),
            "authority": _text(item.get("authorityName")),
            "form": _text(item.get("form")),
            "type": _text(item.get("type")),
            "status": _text(item.get("inspectionStatus")),
            "start_date": _normalized_date(item.get("startDate")),
            "end_date": _normalized_date(item.get("endDate")),
        }
        for item in raw_inspections
    ]
    raw_licenses = _dict_items(report.get("licenses"))
    licenses = [
        {
            "number": _text(item.get("number")),
            "name": _text(item.get("name")),
            "status": _text(item.get("status")),
            "issuing_authority": _text(item.get("issuingAuthority")),
            "issue_date": _normalized_date(item.get("issueDate")),
            "end_date": _normalized_date(item.get("endDate")),
        }
        for item in raw_licenses
    ]
    compliance = Compliance(
        source_signals=positive_signals + negative_signals,
        inspections_source_available=isinstance(report.get("inspections"), list),
        inspections=inspections,
        licenses_source_available=isinstance(report.get("licenses"), list),
        licenses=licenses,
    )

    raw_procurements = _dict_items(report.get("procurements"))
    procurement_activity = [
        {
            "year": _number(item.get("procurementsYear")),
            "federal_law_code": _text(item.get("federalLawCode")),
            "winner_count": _number(item.get("tenderWinnerCnt")),
            "signed_contract_count": _number(item.get("contractSignedCnt")),
            "signed_contract_amount_rub": _money(item.get("contractSignedAmt")),
        }
        for item in raw_procurements
    ]
    procurement_activity.sort(key=lambda item: item["year"] or -math.inf)
    procurement = Procurement(
        source_available=isinstance(report.get("procurements"), list),
        yearly_activity=procurement_activity,
    )

    metrics: dict[str, DerivedMetric] = {}
    metrics["company_age_years"] = (
        _metric(
            "company_age_years",
            _years_between(
                _get(base, "registrationInfo", "registrationDate"), report_date_raw
            ),
            [
                "report.baseInfo.registrationInfo.registrationDate",
                "report.reportDate",
            ],
            "(report_date - registration_date) / 365.2425",
            unit="YEARS",
        )
        if _years_between(
            _get(base, "registrationInfo", "registrationDate"), report_date_raw
        )
        is not None
        else _missing_metric(
            "company_age_years",
            [
                "report.baseInfo.registrationInfo.registrationDate",
                "report.reportDate",
            ],
            "(report_date - registration_date) / 365.2425",
            unit="YEARS",
        )
    )
    if company_identity.status is None:
        metrics["is_active_company"] = _missing_metric(
            "is_active_company",
            ["report.status.status"],
            "status == 'CURRENT'",
        )
    else:
        metrics["is_active_company"] = _metric(
            "is_active_company",
            company_identity.status == "CURRENT",
            ["report.status.status"],
            "status == 'CURRENT'",
        )

    metrics["founder_count"] = _metric(
        "founder_count",
        len(founders),
        ["report.foundersInfo.cofounders"],
        "Количество элементов; отсутствующая коллекция нормализуется в []",
        unit="COUNT",
    )
    shares = [item["share_percent"] for item in founders if item["share_percent"] is not None]
    metrics["max_owner_share"] = (
        _metric(
            "max_owner_share",
            max(shares),
            ["report.foundersInfo.cofounders[].share"],
            "max(founder.share)",
            unit="PERCENT",
        )
        if shares
        else _missing_metric(
            "max_owner_share",
            ["report.foundersInfo.cofounders[].share"],
            "max(founder.share)",
            unit="PERCENT",
        )
    )
    director_tenure = _years_between(
        auth_person.get("positionDate"), report_date_raw
    )
    metrics["director_tenure_years"] = (
        _metric(
            "director_tenure_years",
            director_tenure,
            ["report.foundersInfo.authPerson.positionDate", "report.reportDate"],
            "(report_date - director_appointment_date) / 365.2425",
            unit="YEARS",
        )
        if director_tenure is not None
        else _missing_metric(
            "director_tenure_years",
            ["report.foundersInfo.authPerson.positionDate", "report.reportDate"],
            "(report_date - director_appointment_date) / 365.2425",
            unit="YEARS",
        )
    )
    if not director or not founders:
        metrics["is_director_also_founder"] = _missing_metric(
            "is_director_also_founder",
            [
                "report.foundersInfo.authPerson",
                "report.foundersInfo.cofounders[]",
            ],
            "Совпадение по ИНН, иначе по нормализованному ФИО",
        )
    else:
        director_inn = director.get("inn")
        director_name = _normalized_name(director.get("name"))
        matches = any(
            (
                bool(director_inn and founder.get("inn"))
                and director_inn == founder.get("inn")
            )
            or (
                bool(director_name and _normalized_name(founder.get("name")))
                and director_name == _normalized_name(founder.get("name"))
            )
            for founder in founders
        )
        metrics["is_director_also_founder"] = _metric(
            "is_director_also_founder",
            matches,
            [
                "report.foundersInfo.authPerson",
                "report.foundersInfo.cofounders[]",
            ],
            "Совпадение по ИНН, иначе по нормализованному ФИО",
        )

    metrics["related_company_count"] = _metric(
        "related_company_count",
        len(related_items),
        ["report.relatedCompanies"],
        "Количество элементов; отсутствующая коллекция нормализуется в []",
        unit="COUNT",
    )
    unique_related_directors = {
        _normalized_name(name) for name in related_directors if _normalized_name(name)
    }
    metrics["unique_related_director_count"] = _metric(
        "unique_related_director_count",
        len(unique_related_directors),
        ["report.relatedCompanies[].authPersonName"],
        "Количество уникальных непустых нормализованных ФИО",
        unit="COUNT",
    )

    other_okved_source_present = isinstance(activities.get("otherKindsOfActivity"), list)
    if main_okved is None and not other_okved_source_present:
        metrics["okved_count"] = _missing_metric(
            "okved_count",
            [
                "report.kindsOfActivityInfo.mainKindOfActivity",
                "report.kindsOfActivityInfo.otherKindsOfActivity",
            ],
            "Основной ОКВЭД (если есть) + число дополнительных",
            unit="COUNT",
        )
    else:
        metrics["okved_count"] = _metric(
            "okved_count",
            (1 if main_okved else 0) + len(additional_okveds),
            [
                "report.kindsOfActivityInfo.mainKindOfActivity",
                "report.kindsOfActivityInfo.otherKindsOfActivity",
            ],
            "Основной ОКВЭД (если есть) + число дополнительных",
            unit="COUNT",
        )
    reported_branch_count = _number(branches_info.get("branchesCount"))
    metrics["branches_count"] = _metric(
        "branches_count",
        reported_branch_count if reported_branch_count is not None else len(branches),
        ["report.branchesInfo.branchesCount", "report.branchesInfo.branches"],
        "branchesCount при наличии, иначе число элементов; отсутствующий блок = 0",
        unit="COUNT",
    )

    revenue_series = _series(raw_financial_rows, ("common", "proceeds"))
    profit_series = _series(raw_financial_rows, ("common", "profit"))
    if len(revenue_series) < 2:
        metrics["revenue_growth_yoy"] = _missing_metric(
            "revenue_growth_yoy",
            ["report.finReports[].common.proceeds"],
            "(latest_revenue - previous_revenue) / previous_revenue",
            unit="RATIO",
        )
    else:
        previous = revenue_series[-2]
        latest = revenue_series[-1]
        sources = [
            f"report.finReports[{latest[2]}].common.proceeds",
            f"report.finReports[{previous[2]}].common.proceeds",
        ]
        if previous[1] == 0:
            metrics["revenue_growth_yoy"] = _missing_metric(
                "revenue_growth_yoy",
                sources,
                "(latest_revenue - previous_revenue) / previous_revenue",
                status="NOT_APPLICABLE",
                unit="RATIO",
            )
        else:
            metrics["revenue_growth_yoy"] = _metric(
                "revenue_growth_yoy",
                round((latest[1] - previous[1]) / previous[1], 6),
                sources,
                "(latest_revenue - previous_revenue) / previous_revenue",
                unit="RATIO",
            )
    metrics["profit_margin"] = _ratio_metric(
        "profit_margin",
        _latest_pair(
            raw_financial_rows, ("common", "profit"), ("common", "proceeds")
        ),
        "common.profit",
        "common.proceeds",
        "profit / revenue для последнего года, где доступны оба значения",
    )
    metrics["revenue_trend"] = _trend_metric(
        "revenue_trend", revenue_series, "common.proceeds"
    )
    metrics["profit_trend"] = _trend_metric(
        "profit_trend", profit_series, "common.profit"
    )
    metrics["current_assets_to_short_term_liabilities"] = _ratio_metric(
        "current_assets_to_short_term_liabilities",
        _latest_pair(
            raw_financial_rows,
            ("assets", "currentAssets", "total"),
            ("liabilities", "shortTermLiabilities", "total"),
        ),
        "assets.currentAssets.total",
        "liabilities.shortTermLiabilities.total",
        "current_assets / short_term_liabilities",
    )
    metrics["cash_to_short_term_liabilities"] = _ratio_metric(
        "cash_to_short_term_liabilities",
        _latest_pair(
            raw_financial_rows,
            ("assets", "currentAssets", "bankroll"),
            ("liabilities", "shortTermLiabilities", "total"),
        ),
        "assets.currentAssets.bankroll",
        "liabilities.shortTermLiabilities.total",
        "cash / short_term_liabilities",
    )
    metrics["receivables_share"] = _ratio_metric(
        "receivables_share",
        _latest_pair(
            raw_financial_rows,
            ("assets", "currentAssets", "receivables"),
            ("assets", "totalAssets"),
        ),
        "assets.currentAssets.receivables",
        "assets.totalAssets",
        "receivables / total_assets",
    )
    liabilities_pair = _latest_pair(
        raw_financial_rows,
        ("liabilities", "capitals"),
        ("assets", "totalAssets"),
    )
    if liabilities_pair is None:
        metrics["liabilities_to_assets"] = _missing_metric(
            "liabilities_to_assets",
            [
                "report.finReports[].assets.totalAssets",
                "report.finReports[].liabilities.capitals",
            ],
            "(total_assets - capital) / total_assets",
            unit="RATIO",
        )
    else:
        _, capital, assets, source_index = liabilities_pair
        sources = [
            f"report.finReports[{source_index}].assets.totalAssets",
            f"report.finReports[{source_index}].liabilities.capitals",
        ]
        if assets == 0:
            metrics["liabilities_to_assets"] = _missing_metric(
                "liabilities_to_assets",
                sources,
                "(total_assets - capital) / total_assets",
                status="NOT_APPLICABLE",
                unit="RATIO",
            )
        else:
            metrics["liabilities_to_assets"] = _metric(
                "liabilities_to_assets",
                round((assets - capital) / assets, 6),
                sources,
                "(total_assets - capital) / total_assets",
                unit="RATIO",
            )

    status_available = legal_risks.by_status_source_available
    status_count_sources = [
        "report.arbitrationByStatus.defandantArbitration",
        "report.arbitrationByStatus.plaintiffArbitration",
    ]
    yearly_count_sources = [
        "report.arbitrationCases[].defendantCount",
        "report.arbitrationCases[].plaintiffCount",
    ]
    if arbitration_by_status["common_count"] is not None:
        total_cases = arbitration_by_status["common_count"]
        total_sources = ["report.arbitrationByStatus.commonCount"]
    elif status_available:
        total_cases = _status_count(arbitration_by_status, "defendant") + _status_count(
            arbitration_by_status, "plaintiff"
        )
        total_sources = status_count_sources
    elif legal_risks.yearly_cases_source_available:
        total_cases = sum(
            (item["defendant_count"] or 0) + (item["plaintiff_count"] or 0)
            for item in yearly_cases
        )
        total_sources = yearly_count_sources
    else:
        total_cases = None
        total_sources = status_count_sources + yearly_count_sources
    metrics["total_arbitration_cases"] = (
        _metric(
            "total_arbitration_cases",
            total_cases,
            total_sources,
            "commonCount; иначе status-блоки; иначе yearly-блоки; источники не суммируются",
            unit="COUNT",
        )
        if total_cases is not None
        else _missing_metric(
            "total_arbitration_cases",
            total_sources,
            "commonCount; иначе status-блоки; иначе yearly-блоки; источники не суммируются",
            unit="COUNT",
        )
    )
    if status_available:
        defendant_count = _status_count(arbitration_by_status, "defendant")
        defendant_count_sources = [
            "report.arbitrationByStatus.defandantArbitration"
        ]
    elif legal_risks.yearly_cases_source_available:
        defendant_count = sum(item["defendant_count"] or 0 for item in yearly_cases)
        defendant_count_sources = ["report.arbitrationCases[].defendantCount"]
    else:
        defendant_count = None
        defendant_count_sources = [
            "report.arbitrationByStatus.defandantArbitration",
            "report.arbitrationCases[].defendantCount",
        ]
    metrics["defendant_cases_count"] = (
        _metric(
            "defendant_cases_count",
            defendant_count,
            defendant_count_sources,
            "Сумма дел ответчика по status; fallback — yearly; источники не суммируются",
            unit="COUNT",
        )
        if defendant_count is not None
        else _missing_metric(
            "defendant_cases_count",
            defendant_count_sources,
            "Сумма дел ответчика по status; fallback — yearly; источники не суммируются",
            unit="COUNT",
        )
    )
    pending_bucket = arbitration_by_status["defendant"]["pending"]
    if status_available:
        pending_count = pending_bucket["count"] or 0
        pending_amount = _number(pending_bucket["amount_rub"])
        if pending_amount is None and pending_count == 0:
            pending_amount = 0
        metrics["defendant_pending_cases_count"] = _metric(
            "defendant_pending_cases_count",
            pending_count,
            [
                "report.arbitrationByStatus.defandantArbitration."
                "defandantArbitrationPending.dpCount"
            ],
            "dpCount; отсутствующий счётчик внутри существующего status-блока = 0",
            unit="COUNT",
        )
        metrics["defendant_pending_amount"] = _metric(
            "defendant_pending_amount",
            pending_amount,
            [
                "report.arbitrationByStatus.defandantArbitration."
                "defandantArbitrationPending.dpAmount"
            ],
            "dpAmount; отсутствие суммы при dpCount = 0 трактуется как 0",
            status="CALCULATED" if pending_amount is not None else "PARTIAL",
            unit="RUB",
        )
    else:
        metrics["defendant_pending_cases_count"] = _missing_metric(
            "defendant_pending_cases_count",
            [
                "report.arbitrationByStatus.defandantArbitration."
                "defandantArbitrationPending.dpCount"
            ],
            "dpCount",
            unit="COUNT",
        )
        metrics["defendant_pending_amount"] = _missing_metric(
            "defendant_pending_amount",
            [
                "report.arbitrationByStatus.defandantArbitration."
                "defandantArbitrationPending.dpAmount"
            ],
            "dpAmount",
            unit="RUB",
        )
    if total_cases in (None, 0) or defendant_count is None:
        status_name = "NOT_AVAILABLE" if total_cases is None else "NOT_APPLICABLE"
        metrics["defendant_cases_share"] = _missing_metric(
            "defendant_cases_share",
            total_sources + defendant_count_sources,
            "defendant_cases_count / total_arbitration_cases",
            status=status_name,
            unit="RATIO",
        )
    else:
        metrics["defendant_cases_share"] = _metric(
            "defendant_cases_share",
            round(defendant_count / total_cases, 6),
            total_sources + defendant_count_sources,
            "defendant_cases_count / total_arbitration_cases",
            unit="RATIO",
        )

    if status_available:
        defendant_amount, defendant_amount_complete = _status_amount(
            arbitration_by_status, "defendant"
        )
        defendant_amount_sources = [
            "report.arbitrationByStatus.defandantArbitration"
        ]
    elif legal_risks.yearly_cases_source_available:
        defendant_amount, defendant_amount_complete = _sum_optional(
            item["defendant_amount_rub"] for item in yearly_cases
        )
        defendant_amount_sources = ["report.arbitrationCases[].defendantAmount"]
    else:
        defendant_amount = None
        defendant_amount_complete = False
        defendant_amount_sources = [
            "report.arbitrationByStatus.defandantArbitration",
            "report.arbitrationCases[].defendantAmount",
        ]
    latest_revenue = revenue_series[-1] if revenue_series else None
    arbitration_ratio_sources = list(defendant_amount_sources)
    if latest_revenue:
        arbitration_ratio_sources.append(
            f"report.finReports[{latest_revenue[2]}].common.proceeds"
        )
    else:
        arbitration_ratio_sources.append("report.finReports[].common.proceeds")
    if defendant_amount is None or latest_revenue is None:
        metrics["arbitration_amount_to_revenue"] = _missing_metric(
            "arbitration_amount_to_revenue",
            arbitration_ratio_sources,
            "defendant_arbitration_amount_rub / latest_revenue_rub",
            unit="RATIO",
        )
    elif latest_revenue[1] == 0:
        metrics["arbitration_amount_to_revenue"] = _missing_metric(
            "arbitration_amount_to_revenue",
            arbitration_ratio_sources,
            "defendant_arbitration_amount_rub / latest_revenue_rub",
            status="NOT_APPLICABLE",
            unit="RATIO",
        )
    else:
        metrics["arbitration_amount_to_revenue"] = _metric(
            "arbitration_amount_to_revenue",
            round(defendant_amount / latest_revenue[1], 6),
            arbitration_ratio_sources,
            "defendant_arbitration_amount_rub / latest_revenue_rub",
            status="CALCULATED" if defendant_amount_complete else "PARTIAL",
            unit="RATIO",
        )

    metrics["total_execution_count"] = _metric(
        "total_execution_count",
        len(proceedings),
        ["report.executionProceedings"],
        "Количество элементов; отсутствующая коллекция нормализуется в []",
        unit="COUNT",
    )
    active_proceedings = [item for item in proceedings if item["active"] is True]
    metrics["active_execution_count"] = _metric(
        "active_execution_count",
        len(active_proceedings),
        ["report.executionProceedings[].active"],
        "Количество элементов с active == true",
        unit="COUNT",
    )
    active_amount, active_amount_complete = _sum_optional(
        item["amount_rub"] for item in active_proceedings
    )
    metrics["active_execution_amount"] = _metric(
        "active_execution_amount",
        active_amount,
        [
            "report.executionProceedings[].active",
            "report.executionProceedings[].amount",
        ],
        "Сумма amount для элементов с active == true",
        status="CALCULATED" if active_amount_complete else "PARTIAL",
        unit="RUB",
    )
    execution_dates = [
        item["date"] for item in proceedings if _date_only(item["date"]) is not None
    ]
    if execution_dates:
        latest_execution_date = max(
            execution_dates, key=lambda value: _date_only(value) or date.min
        )
        metrics["latest_execution_date"] = _metric(
            "latest_execution_date",
            latest_execution_date,
            ["report.executionProceedings[].date"],
            "max(date)",
            unit="ISO_DATE",
        )
    else:
        metrics["latest_execution_date"] = _missing_metric(
            "latest_execution_date",
            ["report.executionProceedings[].date"],
            "max(date)",
            status="NOT_APPLICABLE" if not proceedings else "NOT_AVAILABLE",
            unit="ISO_DATE",
        )

    metrics["tender_count"] = _missing_metric(
        "tender_count",
        ["report.procurements"],
        "В GetFullReportResponse нет отдельного поля количества участий в тендерах",
        unit="COUNT",
    )
    if procurement.source_available:
        winner_count, winner_complete = _sum_optional(
            item["winner_count"] for item in procurement_activity
        )
        metrics["winner_count"] = _metric(
            "winner_count",
            winner_count,
            ["report.procurements[].tenderWinnerCnt"],
            "sum(tenderWinnerCnt)",
            status="CALCULATED" if winner_complete else "PARTIAL",
            unit="COUNT",
        )
        contract_amount, contract_complete = _sum_optional(
            item["signed_contract_amount_rub"] for item in procurement_activity
        )
        metrics["signed_contract_amount"] = _metric(
            "signed_contract_amount",
            contract_amount,
            ["report.procurements[].contractSignedAmt"],
            "sum(contractSignedAmt)",
            status="CALCULATED" if contract_complete else "PARTIAL",
            unit="RUB",
        )
    else:
        metrics["winner_count"] = _missing_metric(
            "winner_count",
            ["report.procurements[].tenderWinnerCnt"],
            "sum(tenderWinnerCnt)",
            unit="COUNT",
        )
        metrics["signed_contract_amount"] = _missing_metric(
            "signed_contract_amount",
            ["report.procurements[].contractSignedAmt"],
            "sum(contractSignedAmt)",
            unit="RUB",
        )

    raw_presence = {
        "ARBITRATION_DEFENDANT": (
            _metric_presence(metrics, "defendant_cases_count"),
            ["report.arbitrationByStatus.defandantArbitration"],
        ),
        "EXECUTION_PROCEEDINGS": (
            _metric_presence(metrics, "active_execution_count"),
            ["report.executionProceedings[].active"],
        ),
        "BRANCHES_INFO": (
            _metric_presence(metrics, "branches_count"),
            ["report.branchesInfo"],
        ),
        "GOVERNMENT_CONTRACT": (
            (
                any(
                    (item["winner_count"] or 0) > 0
                    or (item["signed_contract_count"] or 0) > 0
                    for item in procurement_activity
                )
                if procurement.source_available
                else None
            ),
            [
                "report.procurements[].tenderWinnerCnt",
                "report.procurements[].contractSignedCnt",
            ],
        ),
        "LICENSES": (bool(licenses), ["report.licenses"]),
        "RELATED_COMPANIES": (
            _metric_presence(metrics, "related_company_count"),
            ["report.relatedCompanies"],
        ),
        "WEB_SITE": (bool(company_identity.website), ["report.baseInfo.website"]),
    }
    source_signals = positive_signals + negative_signals
    conflicts = _add_source_conflicts(source_signals, raw_presence)
    warnings: list[Conflict] = []

    historical_total_count = (
        sum(
            (item["defendant_count"] or 0) + (item["plaintiff_count"] or 0)
            for item in yearly_cases
        )
        if legal_risks.yearly_cases_source_available
        else None
    )
    current_total_count = (
        (
            arbitration_by_status["common_count"]
            if arbitration_by_status["common_count"] is not None
            else _status_count(arbitration_by_status, "defendant")
            + _status_count(arbitration_by_status, "plaintiff")
        )
        if legal_risks.by_status_source_available
        else None
    )
    if (
        historical_total_count is not None
        and current_total_count is not None
        and historical_total_count != current_total_count
    ):
        warnings.append(
            Conflict(
                type="ARBITRATION_SCOPE_DIFFERENCE",
                code="ARBITRATION_SCOPE_DIFFERENCE",
                description=(
                    "Историческое число дел в arbitrationCases отличается от "
                    "текущего состояния arbitrationByStatus; источники имеют разные "
                    "временные срезы и не суммируются."
                ),
                source_fields=[
                    "report.arbitrationCases[].defendantCount",
                    "report.arbitrationCases[].plaintiffCount",
                    "report.arbitrationByStatus.commonCount",
                    "report.arbitrationByStatus.defandantArbitration",
                    "report.arbitrationByStatus.plaintiffArbitration",
                ],
            )
        )

    partial_metrics = sorted(
        metric.metric for metric in metrics.values() if metric.status == "PARTIAL"
    )
    if partial_metrics:
        warnings.append(
            Conflict(
                type="PARTIAL_DERIVED_METRICS",
                code="PARTIAL_DERIVED_METRICS",
                description=(
                    "Часть метрик рассчитана по неполному набору исходных значений: "
                    + ", ".join(partial_metrics)
                ),
                source_fields=sorted(
                    {
                        source
                        for name in partial_metrics
                        for source in metrics[name].source_fields
                    }
                ),
            )
        )

    return NormalizedCompanyProfile(
        company_identity=company_identity,
        bank_risk=bank_risk,
        ownership=ownership,
        related_companies=related_companies,
        business_profile=business_profile,
        financial_health=financial_health,
        legal_risks=legal_risks,
        enforcement=enforcement,
        compliance=compliance,
        procurement=procurement,
        derived_metrics=metrics,
        data_quality=DataQuality(conflicts=conflicts, warnings=warnings),
    )


def normalize_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Нормализует набор карточек в JSON-совместимые словари."""
    return [normalize_record(record).to_dict() for record in records]


def load_records(path: str | Path) -> list[dict[str, Any]]:
    """Загружает массив карточек GetFullReportResponse из JSON-файла."""
    with Path(path).open(encoding="utf-8") as source:
        value = json.load(source)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError("Ожидается JSON-массив объектов GetFullReportResponse")
    return value
