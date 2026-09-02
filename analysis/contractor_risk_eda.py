"""Воспроизводимые функции для EDA рисков контрагентов.

Модуль не меняет исходные данные. Он приводит JSON и flattened CSV к
общей модели признаков, а затем считает только статистические связи.
Причинные выводы по этому наблюдательному набору делать нельзя.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
from scipy.stats import chi2_contingency, fisher_exact, spearmanr
from sklearn.metrics import cohen_kappa_score
from statsmodels.stats.multitest import multipletests


BASE_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
ZSK_ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}

RAW_BINARY_FEATURES = [
    "status_not_current",
    "is_young_company",
    "has_arbitration_defendant",
    "has_arbitration_defendant_yearly",
    "has_arbitration_defendant_status",
    "has_arbitration_plaintiff",
    "has_pending_defendant_cases",
    "has_any_execution",
    "has_active_execution",
    "profit_negative",
    "capital_negative",
    "revenue_decline",
    "has_related_companies",
    "has_inspections",
    "has_inspection_violations",
    "has_licenses",
    "many_okved",
    "has_procurement_contracts",
]

RAW_NUMERIC_FEATURES = [
    "company_age_years",
    "other_okved_count",
    "branch_count",
    "founder_count",
    "related_companies_count",
    "revenue_latest",
    "profit_latest",
    "assets_latest",
    "capital_latest",
    "receivables_latest",
    "cash_latest",
    "accounts_payable_latest",
    "revenue_change_pct",
    "sustainability",
    "solvency",
    "profitability",
    "arbitration_defendant_count",
    "arbitration_defendant_status_count",
    "arbitration_defendant_amount",
    "arbitration_plaintiff_count",
    "arbitration_plaintiff_amount",
    "pending_defendant_count",
    "active_execution_count",
    "active_execution_amount",
    "execution_total_count",
    "execution_total_amount",
    "inspection_count",
    "inspection_violation_count",
    "license_count",
    "procurement_contract_count",
    "procurement_contract_amount",
]

FEATURE_NAMES_RU = {
    "status_not_current": "статус компании не CURRENT",
    "is_young_company": "возраст компании ≤ 3 лет",
    "has_arbitration_defendant": "есть арбитраж как ответчик",
    "has_arbitration_defendant_yearly": "есть арбитраж как ответчик (yearly)",
    "has_arbitration_defendant_status": "есть арбитраж как ответчик (status)",
    "has_arbitration_plaintiff": "есть арбитраж как истец",
    "has_pending_defendant_cases": "есть pending-дела как ответчик",
    "has_any_execution": "есть исполнительные производства",
    "has_active_execution": "есть активные исполнительные производства",
    "profit_negative": "отрицательная прибыль",
    "capital_negative": "отрицательный капитал",
    "revenue_decline": "снижение выручки к предыдущему году",
    "has_related_companies": "есть связанные компании",
    "has_inspections": "есть проверки",
    "has_inspection_violations": "есть проверки с нарушениями",
    "has_licenses": "есть лицензии",
    "many_okved": "не менее 10 дополнительных ОКВЭД",
    "has_procurement_contracts": "есть заключённые госконтракты",
    "neg_domain__registry": "есть negative signal ФНС / реестров",
    "neg_domain__management": "есть negative signal по руководству",
    "neg_domain__finance": "есть negative signal по финансам",
    "neg_domain__arbitration": "есть negative signal по арбитражу",
    "neg_domain__execution": "есть negative signal по исполнительным делам",
    "neg_domain__business_profile": "есть negative signal по ОКВЭД",
    "company_age_years": "возраст компании, лет",
    "other_okved_count": "число дополнительных ОКВЭД",
    "branch_count": "число филиалов",
    "founder_count": "число учредителей",
    "related_companies_count": "число связанных компаний",
    "revenue_latest": "последняя выручка",
    "profit_latest": "последняя прибыль",
    "assets_latest": "последние активы",
    "capital_latest": "последний капитал",
    "receivables_latest": "последняя дебиторская задолженность",
    "cash_latest": "последние денежные средства",
    "accounts_payable_latest": "последняя кредиторская задолженность",
    "revenue_change_pct": "изменение выручки, %",
    "sustainability": "коэффициент sustainability",
    "solvency": "коэффициент solvency",
    "profitability": "коэффициент profitability",
    "arbitration_defendant_count": "число арбитражных дел как ответчик",
    "arbitration_defendant_status_count": "число арбитражных дел как ответчик (status)",
    "arbitration_defendant_amount": "сумма требований к компании в арбитраже",
    "arbitration_plaintiff_count": "число арбитражных дел как истец",
    "arbitration_plaintiff_amount": "сумма требований компании в арбитраже",
    "pending_defendant_count": "число pending-дел как ответчик",
    "active_execution_count": "число активных исполнительных производств",
    "active_execution_amount": "сумма активных исполнительных производств",
    "execution_total_count": "всего исполнительных производств",
    "execution_total_amount": "общая сумма исполнительных производств",
    "inspection_count": "число проверок",
    "inspection_violation_count": "число проверок с нарушениями",
    "license_count": "число лицензий",
    "procurement_contract_count": "число заключённых госконтрактов",
    "procurement_contract_amount": "сумма заключённых госконтрактов",
}


def parse_scalar(value: str, path: str) -> Any:
    """Приводит CSV-значения к числам/булевым, но не портит идентификаторы."""
    text = value.strip()
    if text == "":
        return None
    lowered = text.casefold()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    last_name = re.sub(r"\[\d+\]", "", path.split(".")[-1]).casefold()
    keep_as_text = {
        "inn",
        "ogrn",
        "kpp",
        "okpo",
        "number",
        "erpid",
        "code",
        "phone",
        "$date",
    }
    if last_name in keep_as_text:
        return text
    if re.fullmatch(r"[-+]?\d+", text):
        try:
            return int(text)
        except ValueError:
            return text
    if re.fullmatch(r"[-+]?(?:\d+\.\d*|\d*\.\d+)(?:[eE][-+]?\d+)?", text):
        try:
            return float(text)
        except ValueError:
            return text
    return text


def path_tokens(path: str) -> list[str | int]:
    tokens: list[str | int] = []
    for part in path.split("."):
        match = re.match(r"^([^\[]+)", part)
        if match:
            tokens.append(match.group(1))
        tokens.extend(int(index) for index in re.findall(r"\[(\d+)\]", part))
    return tokens


def set_nested(root: dict[str, Any], path: str, value: Any) -> None:
    tokens = path_tokens(path)
    current: Any = root
    for position, token in enumerate(tokens):
        last = position == len(tokens) - 1
        next_token = None if last else tokens[position + 1]
        if isinstance(token, str):
            if last:
                current[token] = value
            elif isinstance(next_token, int):
                current = current.setdefault(token, [])
            else:
                current = current.setdefault(token, {})
        else:
            while len(current) <= token:
                current.append(None)
            if last:
                current[token] = value
            elif current[token] is None:
                current[token] = [] if isinstance(next_token, int) else {}
                current = current[token]
            else:
                current = current[token]


def load_json_records(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open(encoding="utf-8") as source:
        data = json.load(source)
    if not isinstance(data, list):
        raise ValueError("JSON должен содержать список карточек")
    return data


def load_flattened_csv_records(path: str | Path) -> list[dict[str, Any]]:
    csv.field_size_limit(100_000_000)
    records: list[dict[str, Any]] = []
    with Path(path).open(newline="", encoding="utf-8-sig") as source:
        for flat_row in csv.DictReader(source):
            record: dict[str, Any] = {}
            for column, raw_value in flat_row.items():
                parsed = parse_scalar(raw_value or "", column)
                if parsed is not None:
                    set_nested(record, column, parsed)
            records.append(record)
    return records


def as_number(value: Any) -> float:
    if isinstance(value, dict):
        for mongo_key in ("$numberLong", "$numberDecimal"):
            if mongo_key in value:
                return as_number(value[mongo_key])
        return math.nan
    if value is None or isinstance(value, bool):
        return math.nan
    if isinstance(value, (int, float, np.number)):
        return float(value)
    if isinstance(value, str):
        normalized = value.strip().replace(" ", "").replace(",", ".")
        try:
            return float(normalized)
        except ValueError:
            return math.nan
    return math.nan


def nested_get(root: Any, *keys: str, default: Any = None) -> Any:
    current = root
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def finite_sum(values: Iterable[Any]) -> float:
    numbers = [as_number(value) for value in values]
    finite = [value for value in numbers if math.isfinite(value)]
    return float(sum(finite)) if finite else 0.0


def canonical_signal_code(value: Any) -> str:
    text = str(value or "").strip().casefold()
    # В исходнике встречается кириллическая "а" в коде арбитражного сигнала.
    text = text.translate(str.maketrans({"а": "a", "А": "a"}))
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def latest_financial_reports(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = dict_items(report.get("finReports"))
    return sorted(
        rows,
        key=lambda item: as_number(nested_get(item, "common", "year")),
        reverse=True,
    )


def financial_value(fin_report: dict[str, Any] | None, *path: str) -> float:
    if not fin_report:
        return math.nan
    return as_number(nested_get(fin_report, *path))


def latest_available_financial_value(
    fin_reports: list[dict[str, Any]], *path: str
) -> tuple[float, float]:
    """Берёт последнее доступное значение конкретной метрики и его год."""
    for fin_report in fin_reports:
        value = financial_value(fin_report, *path)
        if math.isfinite(value):
            return value, financial_value(fin_report, "common", "year")
    return math.nan, math.nan


def extract_company_features(record: dict[str, Any], dataset: str) -> dict[str, Any]:
    report = record.get("report") if isinstance(record.get("report"), dict) else {}
    base = report.get("baseInfo") if isinstance(report.get("baseInfo"), dict) else {}
    status = report.get("status") if isinstance(report.get("status"), dict) else {}
    founders = report.get("foundersInfo") if isinstance(report.get("foundersInfo"), dict) else {}
    activities = report.get("kindsOfActivityInfo") if isinstance(report.get("kindsOfActivityInfo"), dict) else {}
    branches = report.get("branchesInfo") if isinstance(report.get("branchesInfo"), dict) else {}

    arbitration_cases = dict_items(report.get("arbitrationCases"))
    execution_rows = dict_items(report.get("executionProceedings"))
    related_rows = dict_items(report.get("relatedCompanies"))
    inspection_rows = dict_items(report.get("inspections"))
    license_rows = dict_items(report.get("licenses"))
    procurement_rows = dict_items(report.get("procurements"))
    cofounders = dict_items(founders.get("cofounders"))
    other_activities = dict_items(activities.get("otherKindsOfActivity"))

    fin_reports = latest_financial_reports(report)
    revenue_latest, revenue_year = latest_available_financial_value(
        fin_reports, "common", "proceeds"
    )
    profit_latest, profit_year = latest_available_financial_value(fin_reports, "common", "profit")
    assets_latest, assets_year = latest_available_financial_value(fin_reports, "assets", "totalAssets")
    capital_latest, capital_year = latest_available_financial_value(
        fin_reports, "liabilities", "capitals"
    )
    receivables_latest, receivables_year = latest_available_financial_value(
        fin_reports, "assets", "currentAssets", "receivables"
    )
    cash_latest, cash_year = latest_available_financial_value(
        fin_reports, "assets", "currentAssets", "bankroll"
    )
    accounts_payable_latest, accounts_payable_year = latest_available_financial_value(
        fin_reports, "liabilities", "shortTermLiabilities", "accountsPayable"
    )

    revenue_series = []
    for fin_report in fin_reports:
        value = financial_value(fin_report, "common", "proceeds")
        if math.isfinite(value):
            revenue_series.append(value)
    revenue_previous = revenue_series[1] if len(revenue_series) > 1 else math.nan

    revenue_change_pct = math.nan
    if math.isfinite(revenue_latest) and math.isfinite(revenue_previous) and revenue_previous != 0:
        revenue_change_pct = 100.0 * (revenue_latest - revenue_previous) / abs(revenue_previous)

    defendant_count = finite_sum(item.get("defendantCount") for item in arbitration_cases)
    defendant_amount = finite_sum(item.get("defendantAmount") for item in arbitration_cases)
    plaintiff_count = finite_sum(item.get("plaintiffCount") for item in arbitration_cases)
    plaintiff_amount = finite_sum(item.get("plaintiffAmount") for item in arbitration_cases)
    defendant_status_finished = as_number(
        nested_get(
            report,
            "arbitrationByStatus",
            "defandantArbitration",
            "defandantArbitrationFinished",
            "dfCount",
        )
    )
    defendant_status_appealed = as_number(
        nested_get(
            report,
            "arbitrationByStatus",
            "defandantArbitration",
            "defandantArbitrationAppealed",
            "daCount",
        )
    )
    pending_defendant_count = as_number(
        nested_get(
            report,
            "arbitrationByStatus",
            "defandantArbitration",
            "defandantArbitrationPending",
            "dpCount",
        )
    )
    if not math.isfinite(pending_defendant_count):
        pending_defendant_count = 0.0
    defendant_status_count = finite_sum(
        [defendant_status_finished, defendant_status_appealed, pending_defendant_count]
    )

    active_execution = [item for item in execution_rows if item.get("active") is True]
    execution_amounts = [item.get("amount") for item in execution_rows]
    active_execution_amounts = [item.get("amount") for item in active_execution]
    execution_known_amount_count = sum(
        math.isfinite(as_number(value)) for value in execution_amounts
    )
    active_execution_known_amount_count = sum(
        math.isfinite(as_number(value)) for value in active_execution_amounts
    )

    violation_rows = [
        item
        for item in inspection_rows
        if "violationdetected" in str(item.get("inspectionStatus", "")).casefold()
        and "notdetected" not in str(item.get("inspectionStatus", "")).casefold()
    ]

    negative_signals = dict_items(nested_get(report, "reputationalRisks", "negative", default=[]))
    positive_signals = dict_items(nested_get(report, "reputationalRisks", "positive", default=[]))
    negative_codes = {canonical_signal_code(item.get("code")) for item in negative_signals}
    positive_codes = {canonical_signal_code(item.get("code")) for item in positive_signals}
    negative_codes.discard("")
    positive_codes.discard("")
    negative_chapters = {str(item.get("chapter", "")).casefold() for item in negative_signals}

    company_age = as_number(nested_get(base, "registrationInfo", "yearsFromRegistration"))
    branch_count = as_number(branches.get("branchesCount"))
    if not math.isfinite(branch_count):
        branch_count = float(len(dict_items(branches.get("branches"))))

    contract_count = finite_sum(item.get("contractSignedCnt") for item in procurement_rows)
    contract_amount = finite_sum(item.get("contractSignedAmt") for item in procurement_rows)
    report_date = nested_get(report, "reportDate", "$date")
    if report_date is None:
        report_date = report.get("reportDate")

    features: dict[str, Any] = {
        "dataset": dataset,
        "inn": str(base.get("inn", "")),
        "ogrn": str(base.get("ogrn", "")),
        "company_name": base.get("shortName") or base.get("fullName"),
        "report_date": report_date,
        "base_risk": base.get("riskLevel"),
        "zsk_risk": report.get("zskRiskLevel"),
        "company_size": base.get("companySize"),
        "tax_system": (dict_items(report.get("taxSystem")) or [{}])[0].get("shortName"),
        "status_not_current": int(bool(status) and status.get("status") != "CURRENT"),
        "company_age_years": company_age,
        "is_young_company": int(math.isfinite(company_age) and company_age <= 3),
        "other_okved_count": float(len(other_activities)),
        "many_okved": int(len(other_activities) >= 10),
        "branch_count": branch_count,
        "founder_count": float(len(cofounders)),
        "related_companies_count": float(len(related_rows)),
        "has_related_companies": int(bool(related_rows)),
        "financial_years_count": float(len(fin_reports)),
        "revenue_latest": revenue_latest,
        "revenue_year": revenue_year,
        "profit_latest": profit_latest,
        "profit_year": profit_year,
        "assets_latest": assets_latest,
        "assets_year": assets_year,
        "capital_latest": capital_latest,
        "capital_year": capital_year,
        "receivables_latest": receivables_latest,
        "receivables_year": receivables_year,
        "cash_latest": cash_latest,
        "cash_year": cash_year,
        "accounts_payable_latest": accounts_payable_latest,
        "accounts_payable_year": accounts_payable_year,
        "revenue_change_pct": revenue_change_pct,
        "revenue_decline": (
            int(revenue_latest < revenue_previous)
            if math.isfinite(revenue_latest) and math.isfinite(revenue_previous)
            else math.nan
        ),
        "profit_negative": int(profit_latest < 0) if math.isfinite(profit_latest) else math.nan,
        "capital_negative": int(capital_latest < 0) if math.isfinite(capital_latest) else math.nan,
        "sustainability": as_number(nested_get(report, "coefficient", "sustainability")),
        "solvency": as_number(nested_get(report, "coefficient", "solvency")),
        "profitability": as_number(nested_get(report, "coefficient", "profitability")),
        "arbitration_defendant_count": defendant_count,
        "arbitration_defendant_status_count": defendant_status_count,
        "arbitration_defendant_amount": defendant_amount,
        "arbitration_plaintiff_count": plaintiff_count,
        "arbitration_plaintiff_amount": plaintiff_amount,
        "pending_defendant_count": pending_defendant_count,
        "has_arbitration_defendant": int(defendant_count > 0 or defendant_status_count > 0),
        "has_arbitration_defendant_yearly": int(defendant_count > 0),
        "has_arbitration_defendant_status": int(defendant_status_count > 0),
        "has_arbitration_plaintiff": int(plaintiff_count > 0),
        "has_pending_defendant_cases": int(pending_defendant_count > 0),
        "execution_total_count": float(len(execution_rows)),
        "execution_total_amount": finite_sum(execution_amounts),
        "execution_amount_coverage": (
            execution_known_amount_count / len(execution_rows) if execution_rows else math.nan
        ),
        "active_execution_count": float(len(active_execution)),
        "active_execution_amount": finite_sum(active_execution_amounts),
        "active_execution_amount_coverage": (
            active_execution_known_amount_count / len(active_execution)
            if active_execution
            else math.nan
        ),
        "has_any_execution": int(bool(execution_rows)),
        "has_active_execution": int(bool(active_execution)),
        "inspection_count": float(len(inspection_rows)),
        "inspection_violation_count": float(len(violation_rows)),
        "has_inspections": int(bool(inspection_rows)),
        "has_inspection_violations": int(bool(violation_rows)),
        "license_count": float(len(license_rows)),
        "has_licenses": int(bool(license_rows)),
        "procurement_contract_count": contract_count,
        "procurement_contract_amount": contract_amount,
        "has_procurement_contracts": int(contract_count > 0),
        "negative_signal_count": float(len(negative_signals)),
        "positive_signal_count": float(len(positive_signals)),
        "neg_domain__registry": int("reestrs" in negative_chapters),
        "neg_domain__management": int("manager" in negative_chapters),
        "neg_domain__finance": int("finance" in negative_chapters),
        "neg_domain__arbitration": int("arbitr" in negative_chapters),
        "neg_domain__execution": int("execproc" in negative_chapters),
        "neg_domain__business_profile": int("okved" in negative_chapters),
    }
    for code in sorted(negative_codes):
        features[f"neg_signal__{code}"] = 1
    for code in sorted(positive_codes):
        features[f"pos_signal__{code}"] = 1
    return features


def build_feature_table(
    json_path: str | Path,
    csv_path: str | Path,
) -> pd.DataFrame:
    json_rows = [extract_company_features(item, "JSON") for item in load_json_records(json_path)]
    csv_rows = [extract_company_features(item, "CSV") for item in load_flattened_csv_records(csv_path)]
    frame = pd.DataFrame(json_rows + csv_rows)
    signal_columns = [
        column for column in frame.columns if column.startswith(("neg_signal__", "pos_signal__"))
    ]
    frame[signal_columns] = frame[signal_columns].fillna(0).astype(int)
    frame["base_score"] = frame["base_risk"].map(BASE_ORDER)
    frame["zsk_score"] = frame["zsk_risk"].map(ZSK_ORDER)
    frame["base_elevated"] = frame["base_risk"].isin(["MEDIUM", "HIGH"]).astype("Int64")
    frame.loc[frame["base_risk"].eq("UNKNOWN") | frame["base_risk"].isna(), "base_elevated"] = pd.NA
    frame["zsk_elevated"] = frame["zsk_risk"].isin(["YELLOW", "RED"]).astype("Int64")
    frame.loc[frame["zsk_risk"].isna(), "zsk_elevated"] = pd.NA
    return frame


def benjamini_hochberg(p_values: pd.Series) -> pd.Series:
    result = pd.Series(np.nan, index=p_values.index, dtype=float)
    finite = p_values.notna()
    if finite.any():
        result.loc[finite] = multipletests(p_values.loc[finite], method="fdr_bh")[1]
    return result


def binary_associations(
    frame: pd.DataFrame,
    target: str,
    features: Iterable[str],
    min_support: int = 5,
    min_group: int = 5,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in features:
        if feature not in frame:
            continue
        subset = frame[[feature, target]].dropna().copy()
        if subset.empty:
            continue
        subset[feature] = pd.to_numeric(subset[feature], errors="coerce")
        subset[target] = pd.to_numeric(subset[target], errors="coerce")
        subset = subset.dropna()
        if not set(subset[feature].unique()).issubset({0, 1}):
            continue
        present = subset[feature].eq(1)
        elevated = subset[target].eq(1)
        a = int((present & elevated).sum())
        b = int((present & ~elevated).sum())
        c = int((~present & elevated).sum())
        d = int((~present & ~elevated).sum())
        support = a + b
        absent_support = c + d
        if support < min_support or absent_support < min_group:
            continue
        present_rate = a / support if support else math.nan
        absent_rate = c / absent_support if absent_support else math.nan
        risk_ratio = present_rate / absent_rate if absent_rate > 0 else math.inf
        odds_ratio, p_value = fisher_exact([[a, b], [c, d]], alternative="two-sided")
        rows.append(
            {
                "feature": feature,
                "feature_ru": FEATURE_NAMES_RU.get(feature, feature),
                "n": len(subset),
                "support": support,
                "elevated_with_feature": a,
                "elevated_rate_with": present_rate,
                "elevated_rate_without": absent_rate,
                "delta_pp": 100.0 * (present_rate - absent_rate),
                "risk_ratio": risk_ratio,
                "odds_ratio": odds_ratio,
                "p_value": p_value,
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["q_value"] = benjamini_hochberg(result["p_value"])
    return result.sort_values(["q_value", "p_value", "delta_pp"], ascending=[True, True, False])


def numeric_associations(
    frame: pd.DataFrame,
    target_score: str,
    features: Iterable[str],
    min_n: int = 30,
    min_unique: int = 3,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature in features:
        if feature not in frame:
            continue
        subset = frame[[feature, target_score]].apply(pd.to_numeric, errors="coerce").dropna()
        if len(subset) < min_n or subset[feature].nunique() < min_unique:
            continue
        rho, p_value = spearmanr(subset[feature], subset[target_score])
        rows.append(
            {
                "feature": feature,
                "feature_ru": FEATURE_NAMES_RU.get(feature, feature),
                "n": len(subset),
                "nonzero": int(subset[feature].ne(0).sum()),
                "rho": float(rho),
                "abs_rho": abs(float(rho)),
                "p_value": float(p_value),
            }
        )
    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result["q_value"] = benjamini_hochberg(result["p_value"])
    return result.sort_values(["q_value", "abs_rho"], ascending=[True, False])


def cramers_v(table: pd.DataFrame) -> float:
    if table.shape[0] < 2 or table.shape[1] < 2:
        return math.nan
    chi2 = chi2_contingency(table, correction=False)[0]
    n = table.to_numpy().sum()
    if n == 0:
        return math.nan
    phi2 = chi2 / n
    rows, columns = table.shape
    phi2_corrected = max(0.0, phi2 - ((columns - 1) * (rows - 1)) / max(n - 1, 1))
    rows_corrected = rows - ((rows - 1) ** 2) / max(n - 1, 1)
    columns_corrected = columns - ((columns - 1) ** 2) / max(n - 1, 1)
    denominator = min(columns_corrected - 1, rows_corrected - 1)
    return math.sqrt(phi2_corrected / denominator) if denominator > 0 else math.nan


def label_relationship(frame: pd.DataFrame) -> dict[str, Any]:
    subset = frame.loc[
        frame["base_score"].notna() & frame["zsk_score"].notna(),
        ["base_score", "zsk_score"],
    ]
    contingency = pd.crosstab(frame["base_risk"], frame["zsk_risk"])
    rho, p_value = spearmanr(subset["base_score"], subset["zsk_score"])
    return {
        "contingency": contingency,
        "n": len(subset),
        "spearman_rho": float(rho),
        "spearman_p": float(p_value),
        "weighted_kappa": float(
            cohen_kappa_score(subset["base_score"], subset["zsk_score"], weights="quadratic")
        ),
        "cramers_v": float(cramers_v(contingency)),
    }


def dataset_quality_summary(frame: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for dataset, subset in frame.groupby("dataset", sort=False):
        rows.append(
            {
                "dataset": dataset,
                "companies": len(subset),
                "unique_inn": subset["inn"].replace("", np.nan).nunique(),
                "unique_ogrn": subset["ogrn"].replace("", np.nan).nunique(),
                "base_unknown": int(subset["base_risk"].eq("UNKNOWN").sum()),
                "financials_available_pct": 100 * subset["financial_years_count"].gt(0).mean(),
                "profit_available_pct": 100 * subset["profit_latest"].notna().mean(),
                "coefficients_available_pct": 100 * subset["sustainability"].notna().mean(),
                "arbitration_yearly_nonempty_pct": 100
                * subset["has_arbitration_defendant_yearly"].mean(),
                "arbitration_status_nonempty_pct": 100
                * subset["has_arbitration_defendant_status"].mean(),
                "execution_amount_nonzero_pct": 100 * subset["execution_total_amount"].gt(0).mean(),
                "execution_amount_known_pct": 100
                * (
                    subset["execution_total_count"] * subset["execution_amount_coverage"]
                ).sum()
                / subset["execution_total_count"].sum(),
            }
        )
    return pd.DataFrame(rows)


def signal_columns(frame: pd.DataFrame, polarity: str = "negative") -> list[str]:
    prefix = "neg_signal__" if polarity == "negative" else "pos_signal__"
    return sorted(column for column in frame.columns if column.startswith(prefix))


def negative_domain_columns(frame: pd.DataFrame) -> list[str]:
    return sorted(column for column in frame.columns if column.startswith("neg_domain__"))


def raw_binary_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in RAW_BINARY_FEATURES if column in frame.columns]


def raw_numeric_columns(frame: pd.DataFrame) -> list[str]:
    return [column for column in RAW_NUMERIC_FEATURES if column in frame.columns]


def summarize_signal_code(column: str) -> str:
    return column.split("__", 1)[-1]


def top_replicated_associations(
    frame: pd.DataFrame,
    target: str,
    features: Iterable[str],
    min_support: int = 5,
) -> pd.DataFrame:
    parts = []
    for dataset in ("JSON", "CSV"):
        result = binary_associations(
            frame.loc[frame["dataset"].eq(dataset)],
            target,
            features,
            min_support=min_support,
        )
        if result.empty:
            continue
        parts.append(
            result[["feature", "delta_pp", "risk_ratio", "p_value", "q_value"]]
            .rename(
                columns={
                    "delta_pp": f"delta_pp_{dataset}",
                    "risk_ratio": f"risk_ratio_{dataset}",
                    "p_value": f"p_value_{dataset}",
                    "q_value": f"q_value_{dataset}",
                }
            )
            .set_index("feature")
        )
    if not parts:
        return pd.DataFrame()
    combined = pd.concat(parts, axis=1, join="inner").reset_index()
    if {"delta_pp_JSON", "delta_pp_CSV"}.issubset(combined.columns):
        combined["same_direction"] = np.sign(combined["delta_pp_JSON"]) == np.sign(
            combined["delta_pp_CSV"]
        )
        combined["min_abs_delta_pp"] = combined[["delta_pp_JSON", "delta_pp_CSV"]].abs().min(axis=1)
        combined = combined.sort_values(
            ["same_direction", "min_abs_delta_pp"], ascending=[False, False]
        )
    return combined


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="EDA связей признаков с risk-level")
    parser.add_argument("--json", required=True, dest="json_path")
    parser.add_argument("--csv", required=True, dest="csv_path")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    frame = build_feature_table(args.json_path, args.csv_path)
    print(dataset_quality_summary(frame).to_string(index=False))
    print("\nbaseInfo.riskLevel:\n", pd.crosstab(frame["dataset"], frame["base_risk"]))
    print("\nzskRiskLevel:\n", pd.crosstab(frame["dataset"], frame["zsk_risk"]))
    relation = label_relationship(frame)
    print("\nСвязь risk-level полей:", {k: v for k, v in relation.items() if k != "contingency"})
    print(relation["contingency"])

    if args.output:
        args.output.mkdir(parents=True, exist_ok=True)
        frame.to_csv(args.output / "contractor_features.csv", index=False)
        for target in ("base_elevated", "zsk_elevated"):
            binary_associations(
                frame,
                target,
                raw_binary_columns(frame) + signal_columns(frame, "negative"),
            ).to_csv(args.output / f"binary_associations_{target}.csv", index=False)
        for target in ("base_score", "zsk_score"):
            numeric_associations(frame, target, raw_numeric_columns(frame)).to_csv(
                args.output / f"numeric_associations_{target}.csv", index=False
            )


if __name__ == "__main__":
    main()
