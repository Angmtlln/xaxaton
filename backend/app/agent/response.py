"""Детерминированное преобразование ToolResult в allowlisted rich response."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .models import (AssistantMetadata, AssistantResponse, ChartPoint, ChartSeries,
                     CompanyCardBlock, Evidence, EvidenceListBlock, FindingItem,
                     FindingListBlock, FullCompanyCheckData, LineChartBlock,
                     MetricGridBlock, MetricItem, TextBlock, ToolFact, ToolResult)
from .prompt import MASTER_PROMPT_VERSION
from .tools import display_fact_value


METRIC_FACT_IDS = (
    "fin.proceeds_last",
    "fin.profit_last",
    "court.defendant_count",
    "execproc.active_count",
)

COMPANY_EVIDENCE_IDS = (
    "company.name",
    "company.inn",
    "company.status",
    "company.age_years",
    "bank.risk_level",
    "bank.zsk_level",
)

VERDICT_MESSAGES = {
    "STOP": "В карточке есть детерминированные стоп-факторы — проверьте их до сделки.",
    "ENHANCED_CHECK": "В доступных данных есть факты, которые стоит уточнить до сделки.",
    "CONDITIONALLY_OK": (
        "Детерминированные стоп-факторы в доступных данных не выявлены, "
        "но это не означает отсутствия рисков."
    ),
    "NO_DATA": "По доступной карточке невозможно сделать содержательный вывод: данных недостаточно.",
}

GUARD_MESSAGES = {
    "missing_inn": "Укажите один ИНН в сообщении, чтобы запустить полную проверку контрагента.",
    "invalid_inn": "Проверьте ИНН: нужны 10 или 12 цифр с корректными контрольными знаками.",
    "ambiguous_inn": "На этом этапе можно проверить только одного контрагента за запрос. Укажите один ИНН.",
    "unsupported_request": (
        "На первом этапе доступна полная проверка по явному ИНН. "
        "Напишите, например: «Проверь контрагента 6165169320»."
    ),
}


def guard_response(reason: str, agent_run_id: str, started: float) -> AssistantResponse:
    message = GUARD_MESSAGES.get(reason, GUARD_MESSAGES["unsupported_request"])
    return AssistantResponse(
        message=message,
        blocks=[TextBlock(title="Нужны данные для запуска", text=message)],
        suggested_actions=["Проверь контрагента 6165169320"],
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id,
            status="needs_input",
            tool_calls=0,
            routing="deterministic_guard",
            prompt_version=MASTER_PROMPT_VERSION,
            latency_ms=_elapsed_ms(started),
        ),
    )


def tool_result_to_assistant(
    result: ToolResult,
    *,
    agent_run_id: str,
    routing: str,
    model: Optional[str],
    started: float,
) -> AssistantResponse:
    if result.status == "error":
        message = (
            result.error.user_safe_message
            if result.error is not None
            else "Не удалось выполнить проверку."
        )
        return AssistantResponse(
            message=message,
            blocks=[TextBlock(title="Проверка не выполнена", text=message)],
            suggested_actions=["Проверь контрагента 6165169320"],
            metadata=AssistantMetadata(
                agent_run_id=agent_run_id,
                status="error",
                tool_calls=1,
                routing=routing,
                model=model,
                prompt_version=MASTER_PROMPT_VERSION,
                latency_ms=_elapsed_ms(started),
                error_code=result.error.code if result.error else "internal_error",
            ),
        )

    data = FullCompanyCheckData.model_validate(result.data)
    evidence_by_id = {item.id: item for item in result.evidence}
    blocks = [
        _company_block(data, evidence_by_id),
        _text_block(data, result.warnings, evidence_by_id),
        _metric_block(data, evidence_by_id),
        _chart_block(data, evidence_by_id),
        _finding_block(data, evidence_by_id),
        EvidenceListBlock(
            title="Факты и поля исходной карточки",
            evidence_ids=list(evidence_by_id),
        ),
    ]
    status = "partial" if result.status == "partial" else "completed"
    return AssistantResponse(
        message=VERDICT_MESSAGES[data.summary.verdict_group],
        blocks=blocks,
        evidence=list(evidence_by_id.values()),
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id,
            check_run_id=data.check_run_id,
            status=status,
            tool_calls=1,
            routing=routing,
            model=model,
            prompt_version=MASTER_PROMPT_VERSION,
            latency_ms=_elapsed_ms(started),
        ),
    )


def runtime_timeout_response(
    agent_run_id: str, started: float, *, tool_calls: int = 0
) -> AssistantResponse:
    message = "Проверка не завершилась в отведённое время. Попробуйте ещё раз."
    return AssistantResponse(
        message=message,
        blocks=[TextBlock(title="Превышен лимит времени", text=message)],
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id,
            status="error",
            tool_calls=min(tool_calls, 1),
            routing="deterministic_fallback",
            prompt_version=MASTER_PROMPT_VERSION,
            latency_ms=_elapsed_ms(started),
            error_code="timeout",
        ),
    )


def _company_block(
    data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]
) -> CompanyCardBlock:
    company = data.company
    return CompanyCardBlock(
        name=company.short_name or company.full_name or "Контрагент",
        inn=company.inn,
        ogrn=company.ogrn,
        status=company.status,
        address=company.address,
        years_from_registration=company.years_from_registration,
        bank_risk_level=company.risk_level,
        zsk_risk_level=company.zsk_risk_level,
        report_date=company.report_date,
        report_url="/report?inn=%s" % company.inn,
        evidence_ids=[item for item in COMPANY_EVIDENCE_IDS if item in evidence_by_id],
    )


def _text_block(
    data: FullCompanyCheckData,
    warnings: List[str],
    evidence_by_id: Dict[str, Evidence],
) -> TextBlock:
    parts: List[str] = [VERDICT_MESSAGES[data.summary.verdict_group]]
    evidence_ids: List[str] = []
    if data.summary.verdict_group == "STOP" and "flags.hard_stop_codes" in evidence_by_id:
        evidence_ids.append("flags.hard_stop_codes")
    if (
        data.summary.verdict_group == "ENHANCED_CHECK"
        and "flags.attention_codes" in evidence_by_id
    ):
        evidence_ids.append("flags.attention_codes")
    if data.summary.verdict_group == "CONDITIONALLY_OK":
        if "flags.hard_stop_codes" in evidence_by_id:
            evidence_ids.append("flags.hard_stop_codes")
        parts.append("Отсутствие события в карточке не доказывает отсутствие риска вне доступных данных.")
    if data.summary.verdict_group == "NO_DATA" or data.coverage.empty_blocks:
        parts.append("Пустые разделы трактуются как NO_DATA, а не как отсутствие событий.")
    if warnings:
        prefix = (
            "Результат частичный"
            if data.pipeline_status == "PARTIAL"
            else "Ограничения ответа"
        )
        parts.append("%s: %s" % (prefix, " ".join(warnings)))
    return TextBlock(
        title="Итог анализа",
        text=" ".join(parts),
        evidence_ids=evidence_ids,
    )


def _metric_block(
    data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]
) -> MetricGridBlock:
    items: List[MetricItem] = []
    for fact_id in METRIC_FACT_IDS:
        fact = data.facts.get(fact_id)
        if fact is None:
            continue
        value = fact.value if isinstance(fact.value, (int, float, str, bool)) else None
        has_data = value is not None and value != ""
        items.append(MetricItem(
            id=fact.id,
            label=fact.label,
            value=value,
            display_value=display_fact_value(fact),
            unit=fact.unit,
            state="data" if has_data else "no_data",
            evidence_id=fact.id if fact.id in evidence_by_id else None,
        ))
    if not items:
        items.append(MetricItem(
            id="metrics.no_data",
            label="Показатели",
            display_value="Нет данных",
            state="no_data",
        ))
    return MetricGridBlock(title="Ключевые показатели", items=items)


def _chart_block(
    data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]
) -> LineChartBlock:
    fact = data.facts.get("fin.series")
    if fact is None or fact.id not in evidence_by_id or not isinstance(fact.value, list):
        return _empty_chart()

    rows = [row for row in fact.value if isinstance(row, dict) and row.get("year") is not None]
    rows.sort(key=lambda row: str(row.get("year")))
    series: List[ChartSeries] = []
    for key, label in (("proceeds", "Выручка"), ("profit", "Прибыль")):
        points = [
            ChartPoint(
                x=str(row["year"]),
                value=float(row[key]) if isinstance(row.get(key), (int, float)) else None,
            )
            for row in rows
        ]
        if any(point.value is not None for point in points):
            series.append(ChartSeries(
                key=key,
                label=label,
                points=points,
                evidence_id=fact.id,
            ))
    if not series:
        return _empty_chart()
    return LineChartBlock(
        title="Финансовая динамика",
        description="Выручка и прибыль по доступным отчётным годам.",
        unit="руб",
        state="data",
        series=series,
    )


def _empty_chart() -> LineChartBlock:
    return LineChartBlock(
        title="Финансовая динамика",
        description="График строится только из детерминированного ряда fin.series.",
        unit="руб",
        state="no_data",
        empty_message="Финансовых рядов в доступной карточке нет.",
    )


def _finding_block(
    data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]
) -> FindingListBlock:
    rules = (
        ("flags.hard_stop_codes", _is_nonempty),
        ("flags.attention_codes", _is_nonempty),
        ("execproc.active_count", _is_positive),
        ("court.defendant_count", _is_positive),
        ("fin.negative_capitals", lambda value: value is True),
        ("fin.proceeds_change_pct", _is_negative),
        ("procurement.contracts_signed", _is_positive),
        ("positive.count", _is_positive),
    )
    items: List[FindingItem] = []
    for fact_id, predicate in rules:
        fact = data.facts.get(fact_id)
        if fact is None or fact.id not in evidence_by_id or not predicate(fact.value):
            continue
        items.append(FindingItem(
            title=fact.label,
            text="Значение по данным карточки: %s." % display_fact_value(fact),
            evidence_ids=[fact.id],
        ))
        if len(items) >= 8:
            break

    for block_name in data.coverage.empty_blocks[:2]:
        items.append(FindingItem(
            title="Недостаточно данных: %s" % block_name,
            text="Этот раздел невозможно оценить по доступной карточке.",
        ))
    return FindingListBlock(
        title="Наблюдения по фактам",
        items=items,
        empty_message=(
            None if items else
            "В выбранном наборе фактов нет отдельных наблюдений; это не означает отсутствия рисков."
        ),
    )


def _is_nonempty(value: object) -> bool:
    return bool(value)


def _is_positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _is_negative(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
