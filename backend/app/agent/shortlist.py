"""Подборка контрагентов по проверенным полям витрины.

Инструмент отвечает на «найди всех, у кого …»: он не делает выводов и не
запускает проверку, а показывает, кто подходит под критерии и сколько таких
всего. Сравнение остаётся отдельным шагом с явными ИНН.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel

from app.domain.facts import CALCULATOR_VERSION
from app.infrastructure import repository

from .models import (FindCompaniesArgs, ToolFreshness, ToolResult,
                     ToolResultMetadata)
from .targeted_models import ShortlistCompany, ShortlistData
from .tools import ToolContext

MONEY_UNITS = ((1_000_000_000, "млрд"), (1_000_000, "млн"), (1_000, "тыс"))


def money(value) -> str:
    """Читаемая сумма; отсутствие значения не превращается в ноль."""
    if value is None:
        return "Нет данных"
    amount = float(value)
    for scale, suffix in MONEY_UNITS:
        if abs(amount) >= scale:
            return "%s %s ₽" % (f"{amount / scale:,.1f}".replace(",", " "), suffix)
    return "%s ₽" % f"{amount:,.0f}".replace(",", " ")


def describe(args: FindCompaniesArgs) -> list[str]:
    """Человекочитаемые критерии — их формулирует backend, а не модель."""
    labels = []
    for value, template in (
        (args.min_proceeds, "выручка от %s"), (args.max_proceeds, "выручка до %s"),
        (args.min_profit, "прибыль от %s"), (args.max_profit, "прибыль до %s"),
        (args.min_claims_amount, "иски от %s"), (args.max_claims_amount, "иски до %s"),
    ):
        if value is not None:
            labels.append(template % money(value))
    for value, template in (
        (args.min_enforcement_count, "исполнительных производств от %s"),
        (args.max_enforcement_count, "исполнительных производств до %s"),
    ):
        if value is not None:
            labels.append(template % value)
    if args.risk_level:
        labels.append("уровень риска банка %s" % args.risk_level)
    if args.zsk_risk_level:
        labels.append("светофор ЗСК %s" % args.zsk_risk_level)
    if args.hard_stops == "with":
        labels.append("есть жёсткие стоп-факторы")
    elif args.hard_stops == "without":
        labels.append("без жёстких стоп-факторов")
    return labels


async def execute_find_companies(context: ToolContext, args: BaseModel) -> ToolResult:
    parsed = FindCompaniesArgs.model_validate(args)
    found = await repository.find_companies(**parsed.model_dump())
    companies = [
        ShortlistCompany(
            inn=str(row["inn"]),
            name=(row.get("short_name") or str(row["inn"]))[:240],
            fin_year=row.get("fin_year"),
            proceeds=_number(row.get("proceeds")),
            profit=_number(row.get("profit")),
            claims_amount=_number(row.get("claims_amount")),
            enforcement_count=int(row.get("enforcement_count") or 0),
            hard_stops=int(row.get("hard_stops") or 0),
            risk_level=row.get("risk_level") or "UNKNOWN",
            zsk_risk_level=row.get("zsk_risk_level") or "UNKNOWN",
        )
        for row in found["rows"]
    ]
    data = ShortlistData(
        criteria=describe(parsed), total=found["total"], sort_by=parsed.sort_by,
        order=parsed.order, companies=companies,
    )
    warnings = []
    if data.total > len(companies):
        warnings.append(
            "Подошло %s компаний, показаны %s по критерию сортировки."
            % (data.total, len(companies))
        )
    if not companies:
        warnings.append("Под эти критерии в загруженной выборке нет ни одной карточки.")
    return ToolResult(
        status="success" if companies else "partial",
        data=data.model_dump(mode="json"),
        evidence=[],
        warnings=warnings,
        freshness=ToolFreshness(report_date=None),
        metadata=ToolResultMetadata(tool="find_companies", latency_ms=0,
                                    calculator_version=CALCULATOR_VERSION),
    )


def _number(value) -> Optional[float]:
    return None if value is None else float(value)
