"""Детерминированное преобразование ToolResult в allowlisted rich response."""
from __future__ import annotations

import time
import re
from typing import Dict, List, Optional

from .models import (AssistantMetadata, AssistantResponse, ChartPoint, ChartSeries,
                     CompanySummaryBlock, Evidence, FindingItem,
                     FindingListBlock, FullCompanyCheckData, LineChartBlock,
                     MetricGridBlock, MetricItem, ToolResult)
from .prompt import MASTER_PROMPT_VERSION
from .tools import display_fact_value
from .targeted_models import TargetedData
from .synthesis import full_check_findings, select_synthesis, verified_evidence


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
    "STOP": "В карточке есть стоп-факторы — проверьте их до сделки.",
    "ENHANCED_CHECK": "В доступных данных есть факты, которые стоит уточнить до сделки.",
    "CONDITIONALLY_OK": (
        "Стоп-факторы в доступных данных не выявлены, "
        "но это не означает отсутствия рисков."
    ),
    "NO_DATA": "По доступной карточке невозможно сделать содержательный вывод: данных недостаточно.",
}

GUARD_MESSAGES = {
    "missing_inn": "Укажите ИНН контрагента. После выбора компании можно задавать вопросы без повторного ИНН.",
    "invalid_inn": "Проверьте ИНН: нужны 10 или 12 цифр с корректными контрольными знаками.",
    "ambiguous_inn": "На этом этапе можно проверить только одного контрагента за запрос. Укажите один ИНН.",
    "unsupported_request": (
        "Доступны полная проверка, финансовые и юридические вопросы об одном контрагенте. "
        "Напишите, например: «Проверь контрагента 6165169320», затем «А что у них с финансами?»."
    ),
    "unknown_conversation": "Диалог истёк или не найден. Начните новый диалог и укажите ИНН контрагента.",
}


def guard_response(reason: str, agent_run_id: str, started: float) -> AssistantResponse:
    message = GUARD_MESSAGES.get(reason, GUARD_MESSAGES["unsupported_request"])
    return AssistantResponse(
        message=message,
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
    synthesis: object = None,
    question: str = "",
) -> AssistantResponse:
    if result.status == "error":
        message = (
            result.error.user_safe_message
            if result.error is not None
            else "Не удалось выполнить проверку."
        )
        return AssistantResponse(
            message=message,
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

    if result.metadata.tool in ("get_financial_data", "get_legal_data"):
        return _targeted_response(
            result, agent_run_id=agent_run_id, routing=routing,
            model=model, started=started, synthesis=synthesis, question=question,
        )

    data = FullCompanyCheckData.model_validate(result.data)
    evidence_by_id = verified_evidence(data, result)
    selected, artifact, synthesis_status = select_synthesis(full_check_findings(data), synthesis)
    paragraphs = ["Я проверил компанию. " + VERDICT_MESSAGES[data.summary.verdict_group]]
    paragraphs.extend(item.text for item in selected)
    if data.coverage.empty_blocks:
        paragraphs.append("Невозможно оценить по доступным данным разделы: %s. Отсутствие данных не означает отсутствия событий."
                          % ", ".join(data.coverage.empty_blocks))
    if result.warnings:
        paragraphs.append(("Результат частичный. " if result.status == "partial" else "Ограничения ответа. ") + " ".join(result.warnings))
    paragraphs.append("Могу отдельно разобрать финансовую динамику или судебные дела — выберите, что важно для вашей проверки.")
    blocks = _optional_artifact(data, evidence_by_id, selected, artifact, full_check=True)
    return AssistantResponse(
        message="\n\n".join(paragraphs),
        leading_artifact=_company_summary(data, evidence_by_id),
        blocks=blocks,
        evidence=list(evidence_by_id.values()),
        suggested_actions=["А что у них с финансами?", "А что у них с судами?"],
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id, check_run_id=data.check_run_id,
            status="partial" if result.status == "partial" else "completed",
            tool_calls=1, routing=routing, model=model,
            prompt_version=MASTER_PROMPT_VERSION, latency_ms=_elapsed_ms(started),
            synthesis=synthesis_status,
        ),
    )


def _targeted_response(result, *, agent_run_id, routing, model, started, synthesis, question):
    data = TargetedData.model_validate(result.data)
    evidence_by_id = verified_evidence(data, result)
    selected, artifact, synthesis_status = select_synthesis(data.findings, synthesis)
    profit_focus = data.domain == "finance" and bool(re.search(r"\bприбыл\w*", question, re.I)) and not re.search(
        r"\b(?:выручк|финанс|капитал|рентабельн|баланс|кредиторск|задолженн)\w*", question, re.I)
    if profit_focus:
        profit = next((data.facts[key] for key in data.metric_ids if _profit_fact(key)), None)
        if profit is None or profit.value is None:
            prefix = profit.label if profit is not None else "Прибыль"
            paragraphs = [prefix + ": нет данных в доступной карточке. Оценить прибыль за этот период невозможно."]
        else:
            paragraphs = ["%s: %s. Для оценки устойчивости прибыли полезно посмотреть её динамику."
                          % (profit.label, display_fact_value(profit))]
        # Keep required adverse observations, but don't lead a profit question
        # with the generic revenue/capital/payables overview.
        selected = [item for item in selected if item.required or any(_profit_fact(ref) for ref in item.evidence_ids)]
        selected = [item for item in selected if item.id != "finance.latest"]
        paragraphs.extend(item.text for item in selected)
    elif data.availability == "NO_DATA":
        paragraphs = ["Невозможно оценить по доступным данным."]
    elif selected:
        paragraphs = [item.text for item in selected]
    else:
        paragraphs = ["В доступной карточке недостаточно сведений для содержательного ответа."]
    if data.availability == "PARTIAL":
        paragraphs.append("Данные неполные.")
    if data.gaps or result.warnings:
        paragraphs.append(" ".join(dict.fromkeys(data.gaps + result.warnings)))
    return AssistantResponse(
        message="\n\n".join(paragraphs),
        blocks=_optional_artifact(data, evidence_by_id, selected, artifact, profit_focus=profit_focus),
        evidence=list(evidence_by_id.values()),
        suggested_actions=["А что у них с судами?"] if data.domain == "finance" else ["А что у них с финансами?"],
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id, status="partial" if result.status == "partial" or data.availability != "DATA" else "completed",
            tool_calls=1, routing=routing, model=model, prompt_version=MASTER_PROMPT_VERSION,
            latency_ms=_elapsed_ms(started), synthesis=synthesis_status,
        ),
    )


def _profit_fact(fact_id):
    return fact_id.startswith("fin.profit.") or fact_id == "fin.profit_last"


def _optional_artifact(data, evidence_by_id, selected, artifact, *, full_check=False, profit_focus=False):
    if artifact == "chart":
        chart = _chart_block(data, evidence_by_id)
        if profit_focus:
            chart.series = [series for series in chart.series if series.key == "profit"]
            chart.title = "Динамика прибыли"
            chart.description = "Прибыль по доступным отчётным годам."
        # A single point or NO_DATA panel does not explain a trend.
        if chart.state == "data" and any(sum(p.value is not None for p in s.points) >= 2 for s in chart.series):
            return [chart]
    elif artifact == "metrics":
        if full_check:
            block = _metric_block(data, evidence_by_id)
        else:
            block = _targeted_metrics(data, profit_focus=profit_focus)
        if block and any(item.state == "data" for item in block.items):
            return [block]
    elif artifact == "findings" and full_check and selected:
        return [FindingListBlock(title="Что стоит уточнить", items=[
            FindingItem(title=item.title, text=item.text, evidence_ids=item.evidence_ids)
            for item in selected
        ])]
    return []


def _targeted_metrics(data, *, profit_focus=False):
    items = []
    for fact_id in data.metric_ids:
        if profit_focus and not _profit_fact(fact_id):
            continue
        fact = data.facts[fact_id]
        value = fact.value if isinstance(fact.value, (int, float, str, bool)) else None
        items.append(MetricItem(
            id=fact.id, label=fact.label, value=value,
            display_value=display_fact_value(fact), unit=fact.unit,
            state="no_data" if value is None else "data", evidence_id=fact.id,
        ))
    return MetricGridBlock(title="Показатели по вопросу", items=items) if items else None


def _company_summary(data, evidence_by_id):
    def value(fact_id):
        fact = data.facts.get(fact_id)
        if fact is None or fact.value is None:
            return None
        # Use the same sanitized source value as evidence, not raw source markup.
        return evidence_by_id[fact_id].display_value if isinstance(fact.value, str) else fact.value

    inn = value("company.inn") or data.inn
    if inn != data.inn or inn != data.company.inn:
        raise ValueError("Company identifier does not match backend fact")
    # Identity/status/bank indicators use facts, never synthesis or legacy prose.
    return CompanySummaryBlock(
        name=value("company.name") or "Контрагент", inn=inn,
        status=value("company.status"),
        years_from_registration=value("company.age_years"),
        bank_risk_level=value("bank.risk_level"),
        zsk_risk_level=value("bank.zsk_level"),
        report_url="/report?inn=%s" % inn,
        evidence_ids=[key for key in COMPANY_EVIDENCE_IDS if key in evidence_by_id],
    )


def runtime_timeout_response(
    agent_run_id: str, started: float, *, tool_calls: int = 0
) -> AssistantResponse:
    message = "Проверка не завершилась в отведённое время. Попробуйте ещё раз."
    return AssistantResponse(
        message=message,
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


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
