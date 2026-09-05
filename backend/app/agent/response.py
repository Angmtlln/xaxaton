"""Backend hydration for Master-authored prose and verified UI artifacts."""
from __future__ import annotations

import time
from typing import Dict, List, Optional

from .models import (AssistantMetadata, AssistantResponse, ChartPoint, ChartSeries,
                     ComparisonCell, ComparisonColumn, ComparisonRow,
                     ComparisonTableBlock, CompanySummaryBlock, Evidence, FindingItem,
                     FindingListBlock, FullCompanyCheckData, LineChartBlock, MasterAnswer,
                     MetricGridBlock, MetricItem, ToolResult)
from .prompt import MASTER_PROMPT_VERSION
from .synthesis import normalized_tool_context, verified_evidence
from .comparison import ROW_SPECS, measure_key
from .targeted_models import ComparisonData, TargetedData
from .tools import display_fact_value
from .suggestions import next_actions


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

SUMMARY_METRICS = {
    "fin.proceeds_last": "Выручка · последний год",
    "fin.profit_last": "Прибыль · последний год",
    "court.defendant_count": "Дел в роли ответчика",
    "execproc.active_count": "Действующих производств",
}

GUARD_MESSAGES = {
    "missing_inn": "Укажите ИНН контрагента. После выбора компании можно задавать вопросы без повторного ИНН.",
    "invalid_inn": "Проверьте ИНН: нужны 10 или 12 цифр с корректными контрольными знаками.",
    "ambiguous_inn": "На этом этапе можно проверить только одного контрагента за запрос. Укажите один ИНН.",
    "unsupported_request": (
        "Доступны полная проверка, финансовые и юридические вопросы об одном контрагенте, "
        "а также сравнение двух-трёх компаний. Напишите, например: «Проверь контрагента "
        "6165169320», затем «А что у них с финансами?»."
    ),
    "comparison_needs_two": (
        "Для сравнения укажите ИНН двух или трёх компаний в одном сообщении, например: "
        "«Сравни 6165169320 и 2311304742, важнее финансовая устойчивость»."
    ),
    "comparison_limit": "За один раз можно сравнить не больше трёх компаний. Оставьте два или три ИНН.",
    "unknown_conversation": "Диалог истёк или не найден. Начните новый диалог и укажите ИНН контрагента.",
}


def guard_response(reason: str, agent_run_id: str, started: float) -> AssistantResponse:
    return AssistantResponse(
        message=GUARD_MESSAGES.get(reason, GUARD_MESSAGES["unsupported_request"]),
        suggested_actions=next_actions(None, {"domain": "intro"}),
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
        if isinstance(data, ComparisonData):
            # Компактная таблица заменяет N отдельных отчётов и всегда гидратируется кодом.
            blocks.append(_comparison_table(data, evidence_by_id))
        optional = _optional_artifact(data, evidence_by_id, artifact)
        if optional is not None:
            blocks.append(optional)

    full = isinstance(data, FullCompanyCheckData)
    domain = context.get("domain")
    if domain == "comparison":
        states = [(item.get("coverage") or {}).get("state") for item in context.get("companies", [])]
        coverage_state = "DATA" if states and all(state == "DATA" for state in states) else "PARTIAL"
    else:
        coverage_state = (context.get("coverage") or {}).get("state", "DATA")
    partial = (result is not None and result.status == "partial") or coverage_state != "DATA"

    return AssistantResponse(
        message=message,
        leading_artifact=_company_summary(data, evidence_by_id) if full and not contextual else None,
        blocks=blocks,
        evidence=list(evidence_by_id.values()),
        suggested_actions=next_actions(master_answer, context, contextual=contextual),
        metadata=AssistantMetadata(
            agent_run_id=agent_run_id,
            check_run_id=data.check_run_id if full and not contextual else None,
            status="needs_input" if domain == "intro" else "partial" if partial else "completed",
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
    if result.metadata.tool == "compare_companies":
        return ComparisonData.model_validate(result.data)
    return TargetedData.model_validate(result.data)


def _fallback_message(context: dict) -> str:
    if context.get("domain") == "intro":
        return (
            "**Помогу разобраться в контрагенте:** проверить доступные сведения, "
            "объяснить финансовые и судебные данные, сравнить две-три компании. "
            "Сейчас ответ модели недоступен. Напишите задачу и ИНН для проверки "
            "или два-три ИНН для сравнения."
        )
    if context.get("domain") == "comparison":
        return _comparison_fallback(context)
    hard_stops = [
        signal for signal in context.get("policy_signals", [])
        if signal.get("kind") == "official_hard_stop"
    ]
    if hard_stops:
        return (
            "В данных есть метка ограничения, требующая уточнения. Аналитический ответ "
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


def _comparison_names(data: ComparisonData) -> Dict[str, str]:
    return {
        item.inn: (item.company.short_name or item.company.full_name or item.inn)
        for item in data.companies
    }


def _comparison_table(data: ComparisonData, evidence_by_id: Dict[str, Evidence]):
    """Одна таблица вместо N отчётов; значения берутся из проверенных фактов."""
    columns = [
        ComparisonColumn(
            inn=item.inn,
            name=item.company.short_name or item.company.full_name or item.inn,
            availability=item.availability,
        )
        for item in data.companies
    ]
    by_company = [
        {measure_key(fact_id.split(":", 1)[1]): fact_id for fact_id in item.metric_ids}
        for item in data.companies
    ]
    rows = []
    for key, label, unit in ROW_SPECS:
        if not any(key in mapping for mapping in by_company):
            continue
        cells = []
        for owner, mapping in zip(data.companies, by_company):
            fact_id = mapping.get(key)
            fact = data.facts.get(fact_id) if fact_id else None
            if fact is None or fact.value is None:
                cells.append(ComparisonCell(display_value="Нет данных", state="no_data"))
            else:
                display = display_fact_value(fact)
                if ":fin." in fact.id and fact.id.rsplit(".", 1)[-1].isdigit():
                    display += " · " + fact.id.rsplit(".", 1)[-1]
                elif key == "proceeds_change_pct":
                    section = owner.sections.get("finance_series")
                    series = section.value if section else []
                    if len(series) >= 2:
                        display += " · %s→%s" % (series[-2]["year"], series[-1]["year"])
                cells.append(ComparisonCell(
                    display_value=display,
                    state="data",
                    evidence_id=fact.id if fact.id in evidence_by_id else None,
                ))
        rows.append(ComparisonRow(id=key, label=label, unit=unit, cells=cells))
    return ComparisonTableBlock(
        title="Сравнение контрагентов",
        columns=columns,
        rows=rows[:10],
        empty_message=None if rows else "Сопоставимых показателей в карточках нет.",
    )


def _comparison_fallback(context: dict) -> str:
    """Без аналитики Master называем только то, что посчитано кодом."""
    flagged = [
        item.get("name") or item.get("inn") for item in context.get("companies", [])
        if any(signal.get("kind") == "official_hard_stop"
               for signal in item.get("policy_signals", []))
    ]
    if flagged:
        return (
            "Аналитическое сравнение сейчас недоступно. Метка ограничения есть "
            "у следующих компаний: %s. Проверьте его до сделки." % ", ".join(flagged)
        )
    empty = [
        item.get("name") or item.get("inn") for item in context.get("companies", [])
        if (item.get("coverage") or {}).get("state") == "NO_DATA"
    ]
    if empty:
        return (
            "Аналитическое сравнение сейчас недоступно. По этим компаниям данных нет: %s. "
            "Отсутствие сведений не подтверждает отсутствие событий или риска." % ", ".join(empty)
        )
    return (
        "Аналитическое сравнение сейчас недоступно. Проверенные показатели компаний "
        "собраны в таблице ниже."
    )


def _policy_block(data, evidence_by_id: Dict[str, Evidence]) -> Optional[FindingListBlock]:
    if isinstance(data, ComparisonData):
        return _comparison_policy_block(data, evidence_by_id)
    items = []
    for signal in data.policy_signals:
        if signal.kind not in {"official_hard_stop", "source_attention"}:
            continue
        fact = data.facts.get(signal.id)
        if fact is None:
            continue
        prefix = "Метка ограничения из источника" if signal.kind == "official_hard_stop" else "Сигнал источника для уточнения"
        items.append(FindingItem(
            title=signal.label,
            text="%s: %s." % (prefix, display_fact_value(fact)),
            evidence_ids=[ref for ref in signal.evidence_ids if ref in evidence_by_id],
        ))
    return FindingListBlock(title="Метки источника", items=items) if items else None


def _comparison_policy_block(data: ComparisonData, evidence_by_id: Dict[str, Evidence]):
    """Метки источника сравнения всегда подписаны компанией."""
    names = _comparison_names(data)
    owner = {
        signal_id: item.inn for item in data.companies for signal_id in item.policy_signal_ids
    }
    items = []
    for signal in data.policy_signals:
        if signal.kind not in {"official_hard_stop", "source_attention"}:
            continue
        fact = data.facts.get(signal.id)
        if fact is None:
            continue
        prefix = ("Метка ограничения из источника" if signal.kind == "official_hard_stop"
                  else "Сигнал источника для уточнения")
        items.append(FindingItem(
            title="%s — %s" % (names.get(owner.get(signal.id), ""), signal.label),
            text="%s: %s." % (prefix, display_fact_value(fact)),
            evidence_ids=[ref for ref in signal.evidence_ids if ref in evidence_by_id],
        ))
    return FindingListBlock(title="Метки источника", items=items) if items else None


def _optional_artifact(data, evidence_by_id: Dict[str, Evidence], artifact: str):
    if artifact == "chart":
        chart = _chart_block(data, evidence_by_id)
        if chart.state == "data" and any(
            sum(point.value is not None for point in series.points) >= 2 for series in chart.series
        ):
            return chart
    if artifact == "metrics":
        block = _metric_block(data, evidence_by_id) if isinstance(data, FullCompanyCheckData) else _targeted_metrics(data)
        if block is not None and isinstance(data, FullCompanyCheckData):
            remaining = [item for item in block.items if item.id not in SUMMARY_METRICS]
            block = block.model_copy(update={"items": remaining}) if remaining else None
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
        evidence_ids=[key for key in (*COMPANY_EVIDENCE_IDS, "fin.series") if key in evidence_by_id],
        metrics=_summary_metrics(data, evidence_by_id),
    )


def _summary_metrics(data: FullCompanyCheckData, evidence_by_id: Dict[str, Evidence]):
    available = {item.id: item for item in _metric_block(data, evidence_by_id).items}
    labels = dict(SUMMARY_METRICS)
    series = data.facts.get("fin.series")
    rows = series.value if series is not None and isinstance(series.value, list) else []
    years = [str(row.get("year")) for row in rows
             if isinstance(row, dict) and str(row.get("year", "")).isdigit()]
    if years:
        year = max(years, key=int)
        labels.update({"fin.proceeds_last": "Выручка · %s" % year,
                       "fin.profit_last": "Прибыль · %s" % year})
    return [
        available[key].model_copy(update={"label": label}) if key in available else
        MetricItem(id=key, label=label, display_value="Нет данных", state="no_data")
        for key, label in labels.items()
    ]


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
