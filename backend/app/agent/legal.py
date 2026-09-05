"""Targeted legal facts from the existing reliability builder; no full pipeline."""
from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel

from app.infrastructure import repository
from app.domain.facts import CALCULATOR_VERSION, build_reliability
from app.infrastructure.mongo import as_float, parse_date
from app.domain.pipeline import CompanyNotFound, _company_card

from .models import (FullCheckCompany, FullCompanyCheckArgs, PolicySignal,
                     ToolFact, ToolFreshness, ToolResult, ToolResultMetadata)
from .targeted_models import TargetedData
from .data_sections import company_from_snapshot, legal_sections
from .models import LegalDataArgs, DataSection
from .tools import ToolContext, _clean_text, _evidence_from_fact


METRIC_IDS = (
    "court.defendant_count", "court.defendant_amount", "court.plaintiff_count",
    "court.plaintiff_amount", "execproc.active_count", "execproc.active_amount",
    "court.common_count", "court.common_amount", "execproc.total_count",
    "inspections.count", "inspections.violations_count",
)


def _number(value: Any, *, count: bool = False) -> bool:
    if isinstance(value, bool):
        return False
    try:
        parsed = as_float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return (parsed is not None and math.isfinite(parsed) and parsed >= 0
            and (not count or parsed.is_integer()))


def _rows(value: Any) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _safe_report(document: Any) -> tuple[dict, list[str]]:
    """Protect each legacy builder section without inventing source values.

    Only an in-memory projection is changed. Unknown values become None;
    original report field paths remain valid for the facts we retain.
    """
    gaps: list[str] = []
    raw = document.get("report", document) if isinstance(document, dict) else None
    if not isinstance(raw, dict):
        return {}, ["Структура исходного отчёта повреждена."]
    report: dict[str, Any] = {}

    def gap(section: str) -> None:
        message = "Часть данных раздела «%s» повреждена или не раскрыта." % section
        if message not in gaps:
            gaps.append(message)

    def rows(key: str, section: str) -> list[dict]:
        value = raw.get(key)
        if value is None:
            return []
        if not isinstance(value, list) or any(not isinstance(row, dict) for row in value):
            gap(section)
            return []
        return [dict(row) for row in value]

    def numbers(row: dict, keys: tuple[str, ...], section: str) -> dict:
        result = dict(row)
        for key in keys:
            value = row.get(key)
            if not _number(value, count=key.endswith("Count") or key == "year"):
                result[key] = None
                if value is not None:
                    gap(section)
        return result

    report["arbitrationCases"] = [numbers(row, (
        "year", "defendantCount", "defendantAmount", "plaintiffCount", "plaintiffAmount",
    ), "Суды") for row in rows("arbitrationCases", "Суды")]
    summary = raw.get("arbitrationByStatus")
    if summary is not None and not isinstance(summary, dict):
        gap("Судебная сводка")
        summary = None
    if summary:
        summary = numbers(summary, ("commonCount", "commonAmount"), "Судебная сводка")
        group = summary.get("defandantArbitration")
        if group is not None and not isinstance(group, dict):
            gap("Судебная сводка")
            group = None
        cleaned_group = {}
        for node, prefix in (("defandantArbitrationPending", "dp"),
                             ("defandantArbitrationAppealed", "da"),
                             ("defandantArbitrationFinished", "df")):
            pair = (group or {}).get(node)
            if pair is not None and not isinstance(pair, dict):
                gap("Судебная сводка")
                pair = None
            cleaned_group[node] = numbers(pair or {}, (prefix + "Count", prefix + "Amount"),
                                          "Судебная сводка")
        summary["defandantArbitration"] = cleaned_group
    report["arbitrationByStatus"] = summary or {}
    proceedings = [numbers(row, ("amount",), "Исполнительные производства")
                   for row in rows("executionProceedings", "Исполнительные производства")]
    for row in proceedings:
        try:
            parsed_date = parse_date(row.get("date"))
        except (TypeError, ValueError, OverflowError, OSError):
            parsed_date = None
        if row.get("date") is not None and parsed_date is None:
            gap("Исполнительные производства")
        row["date"] = parsed_date
    report["executionProceedings"] = proceedings
    inspections = rows("inspections", "Надзорные проверки")
    for row in inspections:
        for key in ("inspectionStatus", "authorityName"):
            if row.get(key) is not None and not isinstance(row[key], str):
                row[key] = None
                gap("Надзорные проверки")
    report["inspections"] = inspections
    risks = raw.get("reputationalRisks")
    if risks is not None and not isinstance(risks, dict):
        gap("Метки источника")
        risks = None
    negative = (risks or {}).get("negative")
    if negative is not None and not isinstance(negative, list):
        gap("Метки источника")
        negative = []
    valid_flags = []
    for flag in negative or []:
        if not isinstance(flag, dict) or not isinstance(flag.get("code"), str):
            gap("Метки источника")
        else:
            valid_flags.append({"code": flag["code"]})
    report["reputationalRisks"] = {"negative": valid_flags}
    return report, gaps


async def execute_legal_data(context: ToolContext, args: BaseModel) -> ToolResult:
    parsed = LegalDataArgs.model_validate(args.model_dump())
    snapshot = await repository.get_latest_snapshot(parsed.inn)
    if snapshot is None or not snapshot.get("document"):
        raise CompanyNotFound(parsed.inn)
    data = build_legal_data(snapshot, year=parsed.year, offset=parsed.offset, section=parsed.section)
    return ToolResult(
        status="success" if data.availability == "DATA" else "partial",
        data=data.model_dump(mode="json"),
        evidence=[_evidence_from_fact(fact) for fact in data.facts.values()],
        warnings=data.gaps, freshness=ToolFreshness(report_date=data.company.report_date),
        metadata=ToolResultMetadata(tool="get_legal_data", latency_ms=0,
                                    calculator_version=CALCULATOR_VERSION),
    )


def build_legal_data(snapshot: dict, *, year=None, offset=0, section="default") -> TargetedData:
    """Пересобирает только legal facts; та же логика нужна и в сравнении."""
    document = snapshot["document"]
    report, gaps = _safe_report(document)
    index = build_reliability(report).index()
    facts: dict[str, ToolFact] = {}

    def keep(fact_id: str) -> None:
        fact = index.get(fact_id)
        if fact is None or fact.value is None:
            return
        payload = fact.to_dict()
        for field in ("label", "field_ref", "comment"):
            if payload.get(field):
                payload[field] = _clean_text(payload[field], 500)
        facts[fact_id] = ToolFact.model_validate(payload)

    # Builder legacy defaults missing components to zero. Publish an aggregate
    # only when every source row actually supplies that component.
    cases = _rows(report.get("arbitrationCases"))
    summary = report.get("arbitrationByStatus") or {}
    if not cases and not summary:
        gaps.append("Арбитражных данных нет: отсутствие записей не означает отсутствия судов.")
    if cases:
        incomplete = False
        for raw_key, fact_id in (
            ("defendantCount", "court.defendant_count"),
            ("defendantAmount", "court.defendant_amount"),
            ("plaintiffCount", "court.plaintiff_count"),
            ("plaintiffAmount", "court.plaintiff_amount"),
        ):
            if all(_number(row.get(raw_key), count=raw_key.endswith("Count")) for row in cases):
                keep(fact_id)
            else:
                incomplete = True
        if incomplete:
            gaps.append("Часть судебных количеств или сумм не раскрыта; неполные агрегаты исключены.")
    if summary:
        for key, fact_id in (("commonCount", "court.common_count"),
                             ("commonAmount", "court.common_amount")):
            if _number(summary.get(key), count=key.endswith("Count")):
                keep(fact_id)
        for node, prefix, fact_id in (
            ("defandantArbitrationPending", "dp", "court.defendant_pending"),
            ("defandantArbitrationAppealed", "da", "court.defendant_appealed"),
            ("defandantArbitrationFinished", "df", "court.defendant_finished"),
        ):
            pair = (summary.get("defandantArbitration") or {}).get(node) or {}
            if _number(pair.get(prefix + "Count"), count=True) and _number(pair.get(prefix + "Amount")):
                keep(fact_id)
        if not cases:
            gaps.append("Есть судебная сводка, но нет данных по годам и ролям; их итоги не рассчитаны.")

    proceedings = _rows(report.get("executionProceedings"))
    if proceedings:
        keep("execproc.total_count")
        keep("execproc.amount_unknown_count")
        statuses_known = all(isinstance(row.get("active"), bool) for row in proceedings)
        amounts_known = all(_number(row.get("amount")) for row in proceedings)
        if amounts_known:
            keep("execproc.total_amount")
        else:
            gaps.append("Не все суммы исполнительных производств раскрыты; полная сумма неизвестна.")
        if statuses_known:
            keep("execproc.active_count")
            active = [row for row in proceedings if row["active"]]
            if all(_number(row.get("amount")) for row in active):
                keep("execproc.active_amount")
        else:
            gaps.append("Не все статусы производств известны; действующие производства нельзя полностью подсчитать.")
    else:
        gaps.append("Нет данных об исполнительных производствах; отсутствие записей не подтверждает отсутствие взысканий.")

    inspections = _rows(report.get("inspections"))
    if inspections:
        keep("inspections.count")
        if all(row.get("inspectionStatus") in {
            "InspectionsViolationDetected", "InspectionsViolationNotDetected",
            "ViolationDetected", "ViolationNotDetected",
        } for row in inspections):
            keep("inspections.violations_count")
        else:
            gaps.append("Результаты части надзорных проверок не раскрыты.")
    else:
        gaps.append("Данные о надзорных проверках отсутствуют.")

    # Only the builder's deterministic interpretation of allowlisted codes is
    # exposed, never untrusted reputationalRisks[].name or a proprietary score.
    for fact_id in ("flags.hard_stop_codes", "flags.attention_codes"):
        if index[fact_id].value:
            keep(fact_id)

    policy_signals: list[PolicySignal] = []
    for fact_id, kind in (("flags.hard_stop_codes", "official_hard_stop"),
                          ("flags.attention_codes", "source_attention")):
        fact = facts.get(fact_id)
        if fact is not None:
            policy_signals.append(PolicySignal(
                id=fact.id, kind=kind, label=fact.label, value=fact.value,
                evidence_ids=[fact.id],
            ))

    availability = "NO_DATA" if not facts else ("PARTIAL" if gaps else "DATA")
    if availability == "NO_DATA":
        gaps.insert(0, "Правовое положение невозможно оценить по доступным данным.")
    company = company_from_snapshot(snapshot)
    sections = legal_sections(snapshot, year=year, offset=offset, section=section)
    sections["legal_aggregates"] = DataSection(field_ref="report.arbitrationCases[]; report.executionProceedings[]; report.inspections[]",
        value={key: {"value": fact.value, "unit": fact.unit, "field_ref": fact.field_ref}
               for key, fact in facts.items() if not key.startswith("flags.")},
        scope="all disclosed snapshot records; incomplete aggregates omitted")
    sections["request"] = DataSection(field_ref="report", value={"section": section, "year": year, "offset": offset}, scope="requested projection, not source data")
    return TargetedData(
        domain="legal", company=company, availability=availability, facts=facts,
        metric_ids=[item for item in METRIC_IDS if item in facts][:8],
        policy_signals=policy_signals, gaps=gaps[:10], sections=sections,
    )
