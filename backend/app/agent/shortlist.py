"""Подборка контрагентов по проверенным полям витрины.

Инструмент отвечает на «найди всех, у кого …»: он не делает выводов и не
запускает проверку, а показывает, кто подходит под критерии и сколько таких
всего. Сравнение остаётся отдельным шагом с явными ИНН.
"""
from __future__ import annotations

import re
from decimal import Decimal
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
    scope = "основной ОКВЭД" if args.activity_scope == "main" else "основной или дополнительный ОКВЭД"
    if args.activity_query:
        labels.append("Деятельность: %s (%s)" % (args.activity_query, scope))
    if args.okved_prefix:
        labels.append("ОКВЭД %s и дочерние коды (%s)" % (args.okved_prefix, scope))
    for value, template in (
        (args.min_proceeds, "выручка от %s"), (args.max_proceeds, "выручка до %s"),
        (args.min_profit, "прибыль от %s"), (args.max_profit, "прибыль до %s"),
        (args.min_claims_amount, "сумма исков к ответчику от %s"), (args.max_claims_amount, "сумма исков к ответчику до %s"),
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
            report_date=str(row["report_date"]) if row.get("report_date") else None,
            matched_activities=row.get("matched_activities") or [],
            inn=str(row["inn"]),
            name=(row.get("short_name") or str(row["inn"]))[:240],
            fin_year=row.get("fin_year"),
            proceeds=_number(row.get("proceeds")),
            profit=_number(row.get("profit")),
            claims_amount=_number(row.get("claims_amount")),
            enforcement_count=row.get("enforcement_count"),
            hard_stops=row.get("hard_stops"),
            risk_level=row.get("risk_level") or "UNKNOWN",
            zsk_risk_level=row.get("zsk_risk_level") or "UNKNOWN",
        )
        for row in found["rows"]
    ]
    ranking = [item.model_dump() for item in parsed.ranking]
    notes = []
    criteria = describe(parsed)
    if ranking:
        from .ranking import ranking_labels
        criteria.append("Порядок: " + "; затем ".join(ranking_labels(ranking)))
        eligible = found.get("eligible_total", found["total"])
        notes.append("Под фильтры подходит %s; доступны для выбора %s; исключены из-за неполных показателей %s."
                     % (found["total"], eligible, found["total"] - eligible))
        if eligible < parsed.limit:
            notes.append("Доступно меньше компаний, чем запрошено: показаны все %s." % eligible)
        notes.append(("При равенстве показателя используется следующий критерий; при полном равенстве — ИНН."
                      if len(ranking) > 1 else "При равенстве выбранного показателя порядок определяется по ИНН.")
                     + " Это не оценка надёжности.")
        if any(item["metric"] in {"proceeds", "profit"} for item in ranking):
            notes.append("Финансы — за последний доступный год каждой компании.")
            if len({row.fin_year for row in companies}) > 1:
                notes.append("Финансовые годы выбранных компаний различаются: это не сравнение за единый период.")
    data = ShortlistData(
        filters={key: value for key, value in parsed.model_dump(exclude_none=True).items()
                 if key not in {"ranking", "sort_by", "order", "limit"}},
        ranking=parsed.ranking, eligible_total=found.get("eligible_total"), notes=notes,
        criteria=criteria, total=found["total"], sort_by=parsed.sort_by,
        order=parsed.order, companies=companies,
    )
    warnings = list(notes)
    if data.total > len(companies):
        warnings.append(
            "Подошло %s компаний, показаны %s по критерию сортировки."
            % (data.total, len(companies))
        )
    if not companies:
        warnings.append("Нет компаний с полными выбранными показателями." if ranking and data.total else "Под эти критерии в загруженной выборке нет ни одной карточки.")
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


def _direct_basic_arguments(message: str) -> Optional[dict]:
    """Только полные явные команды: незнакомый остаток оставляем native routing.

    Как и direct dispatch по ИНН, это разбор пользовательских аргументов,
    а не проверка смысла ответа или собственные критерии благонадёжности.
    """
    match = re.fullmatch(
        r"(?:найди|покажи|подбери)\s+(?:компании|контрагентов)\s+"
        r"(?P<condition>.+?)(?:,?\s+покажи\s+первые\s+(?P<limit>[0-9]+))?[.!]?",
        message.strip(), re.I,
    )
    if not match:
        return None
    condition = match['condition'].strip()
    args = {}
    if re.fullmatch(r"без\s+(?:ж[её]стких\s+)?стоп-факторов", condition, re.I):
        args['hard_stops'] = 'without'
    else:
        numeric = re.fullmatch(
            r"с\s+(?P<metric>выручкой|прибылью)\s+(?P<bound>от|до)\s+"
            r"(?P<amount>[0-9]+(?:[.,][0-9]+)?)\s*"
            r"(?P<unit>тыс|млн|млрд)?\.?\s*(?:рублей|руб\.?|₽)?", condition, re.I,
        )
        if not numeric:
            return None
        scale = {None: 1, 'тыс': 1000, 'млн': 1000000, 'млрд': 1000000000}[numeric['unit'].lower() if numeric['unit'] else None]
        field = ('min_' if numeric['bound'].lower() == 'от' else 'max_') + (
            'proceeds' if numeric['metric'].lower() == 'выручкой' else 'profit')
        args[field] = float(Decimal(numeric['amount'].replace(',', '.')) * scale)
    if match['limit']:
        args['limit'] = int(match['limit'])
    try:
        return FindCompaniesArgs(**args).model_dump(exclude_none=True)
    except ValueError:
        return None


_ACTIVITY_CLAUSE = re.compile(
    r"(?:занимающ(?:иеся|ихся)\s+|по\s+деятельности\s+)(?P<query>.+)", re.I)


def activity_arguments(message: str) -> dict:
    """Явная деятельность остаётся обязательной и при модельном разборе порогов."""
    match = re.search(
        r"(?:занимающ(?:иеся|ихся)\s+|по\s+деятельности\s+)(.+?)"
        r"(?=\s+и\s+(?:с\s+)?(?:выручк|прибыл|без\s)|,|$)", message.strip().rstrip('.!'), re.I)
    code = re.search(r"\bокв[еэ]д\s+([0-9]{2}(?:\.[0-9]{1,2}){0,2})(?![0-9.])", message, re.I)
    if not match and not code:
        return {}
    scope = "main" if re.search(r"только\s+основной(?:\s+окв[еэ]д)?", message, re.I) else "any"
    query = match[1].strip() if match else None
    if query:
        query = re.sub(r"\s+(?:только\s+основной|включая\s+дополнительные)(?:\s+окв[еэ]д)?$", "", query, flags=re.I)
    return {"activity_query": query, "okved_prefix": code[1] if code else None,
            "activity_scope": scope}


def direct_shortlist_arguments(message: str) -> Optional[dict]:
    basic = _direct_basic_arguments(message)
    if basic is not None:
        return basic
    match = re.fullmatch(
        r"(?:найди|покажи|подбери)\s+(?:компании|контрагентов),?\s+"
        r"(?P<condition>.+?)(?:,?\s+покажи\s+первые\s+(?P<limit>[0-9]+))?[.!]?",
        message.strip(), re.I,
    )
    if not match:
        return None
    condition = match['condition'].strip()
    scope = 'any'
    scope_suffix = re.search(r",?\s+включая\s+дополнительные(?:\s+окв[еэ]д)?$", condition, re.I)
    if scope_suffix:
        scope = 'any'
        condition = condition[:scope_suffix.start()].strip()
    main_suffix = re.search(r",?\s+только\s+основной(?:\s+окв[еэ]д)?$", condition, re.I)
    if main_suffix:
        scope = 'main'
        condition = condition[:main_suffix.start()].strip()
    parts = re.split(r"(?:\s+и\s+|,\s*(?:и\s+)?)(?=(?:с\s+)?(?:выручкой|прибылью)|без\s)", condition, flags=re.I)
    activity = _ACTIVITY_CLAUSE.fullmatch(parts[0])
    code = re.fullmatch(r"(?:с\s+)?окв[еэ]д\s+([0-9.]+)", parts[0], re.I)
    if activity:
        args = {'activity_query': activity['query'].strip(), 'activity_scope': scope}
    elif code:
        args = {'okved_prefix': code[1], 'activity_scope': scope}
    else:
        return None
    for part in parts[1:]:
        if part.lower().startswith(('выручкой', 'прибылью')):
            part = 'с ' + part
        parsed = _direct_basic_arguments('Найди компании ' + part)
        if parsed is None:
            return None
        for key, value in parsed.items():
            if key in {'sort_by', 'order', 'limit', 'activity_scope'}:
                continue
            if key in args and args[key] != value:
                return None
            args[key] = value
    if match['limit']:
        args['limit'] = int(match['limit'])
    try:
        return FindCompaniesArgs(**args).model_dump(exclude_none=True)
    except ValueError:
        return None
