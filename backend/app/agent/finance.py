"""Точечная финансовая capability без полного pipeline и вызовов LLM."""
from __future__ import annotations

import math
from collections import Counter

from app.infrastructure import repository
from app.domain.facts import CALCULATOR_VERSION, build_finance
from app.infrastructure.mongo import as_int, num
from app.domain.pipeline import CompanyNotFound

from .models import (FullCheckCompany, FullCompanyCheckArgs, ToolFact,
                     ToolFreshness, ToolResult, ToolResultMetadata)
from .targeted_models import TargetedData
from .tools import ToolContext, _clean_text, _evidence_from_fact


FIELDS = {
    "proceeds": ("Выручка", "common.proceeds"),
    "profit": ("Прибыль", "common.profit"),
    "capitals": ("Собственный капитал", "liabilities.capitals"),
    "accounts_payable": (
        "Кредиторская задолженность", "liabilities.shortTermLiabilities.accountsPayable"
    ),
}


def _number(value):
    return value if isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) else None


async def execute_financial_data(context: ToolContext, args: FullCompanyCheckArgs) -> ToolResult:
    parsed = FullCompanyCheckArgs.model_validate(args)
    snapshot = await repository.get_latest_snapshot(parsed.inn)
    if snapshot is None:
        raise CompanyNotFound(parsed.inn)
    data = build_financial_data(snapshot, parsed.inn)
    return ToolResult(
        status="success" if data.availability == "DATA" else "partial",
        data=data.model_dump(mode="json"),
        evidence=[_evidence_from_fact(fact) for fact in data.facts.values()],
        warnings=data.gaps,
        freshness=ToolFreshness(report_date=data.company.report_date),
        metadata=ToolResultMetadata(
            tool="get_financial_data", latency_ms=0,
            calculator_version=CALCULATOR_VERSION,
        ),
    )


def build_financial_data(snapshot: dict, inn: str) -> TargetedData:
    """Пересобирает только finance facts и сохраняет точные пути исходных строк."""
    document = snapshot.get("document") or {}
    report = document.get("report", document) if isinstance(document, dict) else {}
    report = report if isinstance(report, dict) else {}
    raw_reports = report.get("finReports")
    raw_reports = raw_reports if isinstance(raw_reports, list) else []
    gaps = []
    candidates = []
    for index, item in enumerate(raw_reports):
        if not isinstance(item, dict) or not isinstance(item.get("common"), dict):
            gaps.append("Часть финансовых строк имеет неподдерживаемую структуру.")
            continue
        try:
            raw_year = num(item["common"].get("year"))
            year = (
                as_int(raw_year)
                if raw_year is not None
                and raw_year.is_finite()
                and raw_year == raw_year.to_integral_value()
                and 1900 <= raw_year <= 2100
                else None
            )
        except (ValueError, OverflowError):
            year = None
        if year is None or not 1900 <= year <= 2100:
            gaps.append("Часть финансовых строк не содержит корректного отчётного года.")
            continue
        candidates.append((year, index, item))
    counts = Counter(year for year, _, _ in candidates)
    if any(count > 1 for count in counts.values()):
        gaps.append("Повторяющиеся отчётные годы исключены: значения требуют уточнения источника.")
    selected = sorted((row for row in candidates if counts[row[0]] == 1), key=lambda row: row[0])[-5:]
    # Убираем чужие домены до вызова существующего builder.
    prepared = []
    for year, _, item in selected:
        prepared.append({
            "common": {**item["common"], "year": year},
            "assets": item.get("assets") if isinstance(item.get("assets"), dict) else {},
            "liabilities": item.get("liabilities") if isinstance(item.get("liabilities"), dict) else {},
        })
    finance = build_finance({"report": {"finReports": prepared}}).index()
    built_rows = finance["fin.series"].value if "fin.series" in finance else []
    facts = {}
    series = []
    for row, (_, index, _) in zip(built_rows, selected):
        year = row["year"]
        compact_row = {"year": year}
        for key, (label, path) in FIELDS.items():
            value = _number(row.get(key))
            compact_row[key] = value
            fact_id = "fin.%s.%s" % (key, year)
            facts[fact_id] = ToolFact(
                id=fact_id, label="%s за %s год" % (label, year), value=value,
                field_ref="report.finReports[%s].%s" % (index, path),
                unit="руб", source="raw",
            )
        series.append(compact_row)
    facts["fin.series"] = ToolFact(
        id="fin.series", label="Финансовые показатели по доступным годам", value=series,
        field_ref="report.finReports[]", source="computed", unit="руб",
    )
    metrics = []
    if series:
        last = series[-1]
        for key, (label, _) in FIELDS.items():
            fact_id = "fin.%s.%s" % (key, last["year"])
            metrics.append(fact_id)
            if last[key] is None:
                gaps.append("%s за последний доступный год отсутствует в карточке." % label)
        if any(row["proceeds"] is None or row["profit"] is None for row in series):
            gaps.append("В финансовом ряду есть пропуски выручки или прибыли; они не заменены нулями.")
        if len(series) >= 2 and series[-2]["year"] + 1 == last["year"]:
            prev = series[-2]
            if prev["proceeds"] not in (None, 0) and last["proceeds"] is not None:
                change = round((last["proceeds"] - prev["proceeds"]) / abs(prev["proceeds"]) * 100, 1)
                if math.isfinite(change):
                    fact_id = "fin.proceeds_change_pct"
                    refs = ["fin.proceeds.%s" % row["year"] for row in (prev, last)]
                    facts[fact_id] = ToolFact(
                        id=fact_id, label="Изменение выручки год к году", value=change,
                        field_ref="; ".join(facts[ref].field_ref for ref in refs),
                        unit="%", source="computed",
                    )
                    metrics.append(fact_id)
                else:
                    gaps.append("Динамика выручки не рассчитана: значения выходят за поддерживаемый диапазон.")
            else:
                gaps.append("Динамика выручки не рассчитана: отсутствует значение или базовая выручка равна нулю.")
        else:
            gaps.append("Для годовой динамики нужны два последовательных года отчётности.")
    has_values = any(row[key] is not None for row in series for key in FIELDS)
    if not has_values:
        gaps.insert(0, "NO_DATA: невозможно оценить финансовое состояние по доступным данным.")
    gaps = list(dict.fromkeys(gaps))[:10]
    company_values = {}
    for key in ("short_name", "full_name", "report_date", "ogrn"):
        value = snapshot.get(key)
        if value is not None:
            company_values[key] = _clean_text(value.isoformat() if hasattr(value, "isoformat") else str(value), 800)
    return TargetedData(
        domain="finance", company=FullCheckCompany(inn=inn, **company_values),
        availability="NO_DATA" if not has_values else ("PARTIAL" if gaps else "DATA"),
        facts=facts, metric_ids=metrics,
        series_ids=["fin.series"] if series else [], gaps=gaps,
    )
