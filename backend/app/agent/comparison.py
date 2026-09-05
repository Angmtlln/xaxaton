"""Сравнение нескольких контрагентов одним вызовом инструмента.

Домены собираются теми же строителями, что и точечные capability, поэтому
сравнение не заводит второй доменный слой и не запускает N полных проверок.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.domain.facts import CALCULATOR_VERSION
from app.domain.pipeline import CompanyNotFound
from app.infrastructure import repository

from .finance import build_financial_data
from .legal import build_legal_data
from .models import (CompareCompaniesArgs, PolicySignal, ToolFact, ToolFreshness,
                     ToolResult, ToolResultMetadata)
from .targeted_models import ComparisonCompanyData, ComparisonData, TargetedData
from .tools import ToolContext, _evidence_from_fact

# Порядок строк таблицы. Ключ финансовой строки не содержит года: у разных
# компаний последний доступный год отличается, а сравнивать нужно одну меру.
ROW_SPECS = (
    ("proceeds", "Выручка за последний доступный год", "руб"),
    ("profit", "Прибыль за последний доступный год", "руб"),
    ("capitals", "Собственный капитал", "руб"),
    ("accounts_payable", "Кредиторская задолженность", "руб"),
    ("proceeds_change_pct", "Изменение выручки год к году", "%"),
    ("court.defendant_count", "Дел в роли ответчика", None),
    ("court.defendant_amount", "Сумма исков к компании", "руб"),
    ("execproc.total_count", "Исполнительных производств", None),
    ("execproc.active_amount", "Сумма действующих производств", "руб"),
    ("inspections.count", "Надзорных проверок", None),
)
ROW_ORDER = {key: index for index, (key, _, _) in enumerate(ROW_SPECS)}


def measure_key(fact_id: str) -> Optional[str]:
    """Каноническая мера факта: `fin.proceeds.2024` и `fin.proceeds.2023` — одна строка."""
    if not fact_id.startswith("fin."):
        return fact_id if fact_id in ROW_ORDER else None
    rest = fact_id[len("fin."):]
    if rest in ROW_ORDER:
        return rest
    base, _, tail = rest.rpartition(".")
    return base if base in ROW_ORDER and tail.isdigit() else None


def _prefixed(inn: str, fact_id: str) -> str:
    return "%s:%s" % (inn, fact_id)


def _worst_availability(states: list[str]) -> str:
    if all(state == "NO_DATA" for state in states):
        return "NO_DATA"
    return "DATA" if all(state == "DATA" for state in states) else "PARTIAL"


def _collect(inn: str, parts: list[TargetedData]) -> tuple[ComparisonCompanyData, dict, list]:
    facts: dict[str, ToolFact] = {}
    signals: list[PolicySignal] = []
    status_ids: list[str] = []
    signal_ids: list[str] = []
    gaps: list[str] = []
    for part in parts:
        for fact in part.facts.values():
            renamed = fact.model_copy(update={"id": _prefixed(inn, fact.id)})
            facts[renamed.id] = renamed
        status_ids.extend(_prefixed(inn, item) for item in part.status_ids)
        for signal in part.policy_signals:
            moved = signal.model_copy(update={
                "id": _prefixed(inn, signal.id),
                "evidence_ids": [_prefixed(inn, ref) for ref in signal.evidence_ids],
            })
            signals.append(moved)
            signal_ids.append(moved.id)
        gaps.extend(part.gaps)

    by_measure = {}
    for fact_id in facts:
        key = measure_key(fact_id.split(":", 1)[1])
        if key is not None:
            # Финансовые факты вставлены по возрастанию года: последнее значение
            # для меры и есть последний доступный отчётный год компании.
            by_measure[key] = fact_id
    metric_ids = [by_measure[key] for key, _, _ in ROW_SPECS if key in by_measure]
    status_ids = status_ids[:8]
    signal_ids = signal_ids[:8]
    referenced = set(metric_ids + status_ids + signal_ids)
    for signal in signals:
        if signal.id in signal_ids:
            referenced.update(signal.evidence_ids)
    # Три насыщенные карточки содержат больше 60 исходных фактов. Для сравнения
    # оставляем только меры таблицы, статусы и policy provenance: это сохраняет
    # полный проверяемый контекст сравнения и укладывается в ToolResult contract.
    facts = {fact_id: fact for fact_id, fact in facts.items() if fact_id in referenced}

    company = ComparisonCompanyData(
        inn=inn,
        company=parts[0].company,
        availability=_worst_availability([part.availability for part in parts]),
        metric_ids=metric_ids,
        status_ids=status_ids,
        policy_signal_ids=signal_ids,
        gaps=list(dict.fromkeys(gaps))[:10],
    )
    return company, facts, signals


async def execute_comparison(context: ToolContext, args: BaseModel) -> ToolResult:
    parsed = CompareCompaniesArgs.model_validate(args)
    focus = ["finance", "legal"] if parsed.focus == "both" else [parsed.focus]
    companies: list[ComparisonCompanyData] = []
    facts: dict[str, ToolFact] = {}
    signals: list[PolicySignal] = []
    warnings: list[str] = []
    report_dates: list[str] = []

    for inn in parsed.inns:
        snapshot = await repository.get_latest_snapshot(inn)
        if snapshot is None or not snapshot.get("document"):
            raise CompanyNotFound(inn)
        parts: list[TargetedData] = []
        if "finance" in focus:
            parts.append(build_financial_data(snapshot, inn))
        if "legal" in focus:
            parts.append(build_legal_data(snapshot))
        company, company_facts, company_signals = _collect(inn, parts)
        companies.append(company)
        facts.update(company_facts)
        signals.extend(company_signals)
        name = company.company.short_name or company.company.full_name or inn
        warnings.extend("%s: %s" % (name, gap) for gap in company.gaps)
        if company.company.report_date:
            report_dates.append(company.company.report_date)

    data = ComparisonData(
        focus=focus, companies=companies, facts=facts, policy_signals=signals[:24],
    )
    complete = all(item.availability == "DATA" for item in data.companies)
    return ToolResult(
        status="success" if complete else "partial",
        data=data.model_dump(mode="json"),
        evidence=[_evidence_from_fact(fact) for fact in facts.values()],
        warnings=list(dict.fromkeys(warnings))[:10],
        freshness=ToolFreshness(report_date=min(report_dates) if report_dates else None),
        metadata=ToolResultMetadata(tool="compare_companies", latency_ms=0,
                                    calculator_version=CALCULATOR_VERSION),
    )
