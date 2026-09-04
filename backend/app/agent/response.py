"""Backend hydration for Master-authored prose and verified UI artifacts."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .models import (AssistantMetadata, AssistantResponse, ChartPoint, ChartSeries,
                     CompanySummaryBlock, Evidence, FindingItem, FindingListBlock,
                     FullCompanyCheckData, LineChartBlock, MasterAnswer,
                     MetricGridBlock, MetricItem, ToolResult)
from .prompt import MASTER_PROMPT_VERSION
from .synthesis import normalized_tool_context, verified_evidence
from .targeted_models import TargetedData
from .tools import display_fact_value


METRIC_FACT_IDS = (
    "fin.proceeds_last",
    "fin.profit_last",
    "fin.capitals_last",
    "fin.payables_to_proceeds_pct",
    "court.defendant_count",
    "court.defendant_amount",
    "execproc.active_count",
    "execproc.active_amount",
)

COMPANY_EVIDENCE_IDS = (
    "company.name",
    "company.inn",
    "company.status",
    "company.age_years",
    "bank.risk_level",
    "bank.zsk_level",
)

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
    return AssistantResponse(
        message=GUARD_MESSAGES.get(reason, GUARD_MESSAGES["unsupported_request"]),
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
    result: Optional[ToolResult],
    *,
    trusted_context: Optional[dict],
    master_answer: Optional[MasterAnswer],
    agent_run_id: str,
    routing: str,
    model: Optional[str],
    started: float,
    contextual: bool = False,
    grounding_status: str = "not_required",
    repair_attempts: int = 0,
) -> AssistantResponse:
    if result is not None and result.status == "error":
        message = result.error.user_safe_message if result.error else "Не удалось выполнить проверку."
        return AssistantResponse(
            message=message,
            suggested_actions=["Проверь контрагента 6165169320"],
            metadata=AssistantMetadata(
                agent_run_id=agent_run_id,
                status="error",
                tool_calls=0 if contextual else 1,
                routing=routing,
                model=model,
                prompt_version=MASTER_PROMPT_VERSION,
                latency_ms=_elapsed_ms(started),
                error_code=result.error.code if result.error else "internal_error",
            ),
        )

    context = trusted_context or (normalized_tool_context(result) if result is not None else None)
    if context is None:
        raise ValueError("Verified context is required for an analytical response")
    data = _result_data(result) if result is not None else None
    evidence_by_id = (
        verified_evidence(data, result)
        if data is not None and result is not None
        else {item.id: item for item in map(Evidence.model_validate, context.get("evidence", []))}
    )
    message = master_answer.message if master_answer is not None else _fallback_message(context)
    artifact = master_answer.artifact if master_answer is not None else "none"

    blocks = []
    if not contextual and data is not None:
        policy = _policy_block(data, evidence_by_id)
        if policy is not None:
            blocks.append(policy)
        optional = _optional_artifact(data, evidence_by_id, artifact)
        if optional is not None:
            blocks.append(optional)

    full = isinstance(data, FullCompanyCheckData)
    coverage_state = (context.get("coverage") or {}).get("state", "DATA")
    partial = (result is not None and result.status == "partial") or coverage_state != "DATA"
    domain = context.get("domain")
    if domain == "full_check":
        suggested = ["А что у них с финансами?", "А что у них с судами?"]
    elif domain == "finance":
        suggested = ["А что у них с судами?"]
    else:
        suggested = ["А что у них с финансами?"]

    return AssistantResponse(
        message=message,
        leading_artifact=_company_summary(data, evidence_by_id) if full and not contextual else None,
        blocks=blocks,
        evidence=list(evidence_by_id.values()),
        suggested_actions=suggested,
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id,
            check_run_id=data.check_run_id if full and not contextual else None,
            status="partial" if partial else "completed",
            tool_calls=0 if contextual else 1,
            routing=routing,
            model=model,
            prompt_version=MASTER_PROMPT_VERSION,
            latency_ms=_elapsed_ms(started),
            synthesis="model" if master_answer is not None else "fallback",
            grounding_status=grounding_status,
            repair_attempts=repair_attempts,
        ),
    )


def _result_data(result: ToolResult):
    if result.metadata.tool == "full_company_check":
        return FullCompanyCheckData.model_validate(result.data)
    return TargetedData.model_validate(result.data)


def _fallback_message(context: dict) -> str:
    hard_stops = [
        signal for signal in context.get("policy_signals", [])
        if signal.get("kind") == "official_hard_stop"
    ]
    if hard_stops:
        return (
            "В проверенных данных есть официальный стоп-сигнал. Аналитический ответ "
            "сейчас недоступен; проверьте сигнал и первичные сведения до сделки."
        )
    state = (context.get("coverage") or {}).get("state")
    if state == "NO_DATA":
        return (
            "По доступным проверенным данным содержательный вывод сделать нельзя. "
            "Отсутствие сведений не подтверждает отсутствие событий или риска."
        )
    observations = list(context.get("metrics", [])) + list(context.get("statuses", []))
    shown = [
        "%s — %s" % (item.get("label"), item.get("display_value"))
        for item in observations[:3] if item.get("label") and item.get("display_value")
    ]
    if shown:
        return "Аналитический ответ сейчас недоступен. Подтверждённые данные: %s." % "; ".join(shown)
    return "Проверенные данные получены, но сформировать аналитическое объяснение сейчас не удалось."


def _policy_block(data, evidence_by_id: Dict[str, Evidence]) -> Optional[FindingListBlock]:
    items = []
    for signal in data.policy_signals:
        if signal.kind not in {"official_hard_stop", "source_attention"}:
            continue
        fact = data.facts.get(signal.id)
        if fact is None:
            continue
        prefix = "Официальный стоп-сигнал источника" if signal.kind == "official_hard_stop" else "Сигнал источника для уточнения"
        items.append(FindingItem(
            title=signal.label,
            text="%s: %s." % (prefix, display_fact_value(fact)),
            evidence_ids=[ref for ref in signal.evidence_ids if ref in evidence_by_id],
        ))
    return FindingListBlock(title="Детерминированные сигналы", items=items) if items else None


def _optional_artifact(data, evidence_by_id: Dict[str, Evidence], artifact: str):
    if artifact == "chart":
        chart = _chart_block(data, evidence_by_id)
        if chart.state == "data" and any(
            sum(point.value is not None for point in series.points) >= 2 for series in chart.series
        ):
            return chart
    if artifact == "metrics":
        block = _metric_block(data, evidence_by_id) if isinstance(data, FullCompanyCheckData) else _targeted_metrics(data)
        if block is not None and any(item.state == "data" for item in block.items):
            return block
    return None


def _targeted_metrics(data: TargetedData) -> Optional[MetricGridBlock]:
    items = []
    for fact_id in data.metric_ids:
        fact = data.facts[fact_id]
        value = fact.value if isinstance(fact.value, (int, float, str, bool)) else None
        items.append(MetricItem(
            id=fact.id,
            label=fact.label,
            value=value,
            display_value=display_fact_value(fact),
            unit=fact.unit,
            state="no_data" if value is None else "data",
            evidence_id=fact.id,
        ))
    return MetricGridBlock(title="Показатели по вопросу", items=items) if items else None


def _company_summary(data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]):
    def value(fact_id):
        fact = data.facts.get(fact_id)
        if fact is None or fact.value is None:
            return None
        return evidence_by_id[fact_id].display_value if isinstance(fact.value, str) else fact.value

    inn = value("company.inn") or data.inn
    if inn != data.inn or inn != data.company.inn:
        raise ValueError("Company identifier does not match backend fact")
    return CompanySummaryBlock(
        name=value("company.name") or "Контрагент",
        inn=inn,
        status=value("company.status"),
        years_from_registration=value("company.age_years"),
        bank_risk_level=value("bank.risk_level"),
        zsk_risk_level=value("bank.zsk_level"),
        report_url="/report?inn=%s" % inn,
        evidence_ids=[key for key in COMPANY_EVIDENCE_IDS if key in evidence_by_id],
    )


def runtime_timeout_response(agent_run_id: str, started: float, *, tool_calls: int = 0) -> AssistantResponse:
    return AssistantResponse(
        message="Проверка не завершилась в отведённое время. Попробуйте ещё раз.",
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


def _metric_block(data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]) -> MetricGridBlock:
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
            id="metrics.no_data", label="Показатели", display_value="Нет данных", state="no_data"
        ))
    return MetricGridBlock(title="Ключевые показатели", items=items)


def _chart_block(data, evidence_by_id: Dict[str, Evidence]) -> LineChartBlock:
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
            series.append(ChartSeries(key=key, label=label, points=points, evidence_id=fact.id))
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
        description="График строится только из проверенного ряда fin.series.",
        unit="руб",
        state="no_data",
        empty_message="Финансовых рядов в доступной карточке нет.",
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
