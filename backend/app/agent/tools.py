"""Минимальный Tool Registry и wrapper над существующим run_check()."""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, Dict, List, Optional, Type

from pydantic import BaseModel, ValidationError

from app.api.schemas import CheckResponse
from app.config import Settings
from app.llm.groq_client import GroqClient
from app.pipeline import CompanyNotFound, run_check

from .models import (Evidence, FullCheckCompany, FullCheckCoverage,
                     FullCheckGrounding, FullCheckSummary, FullCompanyCheckArgs,
                     FullCompanyCheckData, ToolError, ToolFact, ToolFreshness,
                     ToolResult, ToolResultMetadata, contains_unsafe_markup)

log = logging.getLogger(__name__)


PRESENTATION_FACT_IDS = (
    "company.name",
    "company.inn",
    "company.status",
    "company.age_years",
    "bank.risk_level",
    "bank.zsk_level",
    "flags.hard_stop_codes",
    "flags.attention_codes",
    "execproc.active_count",
    "execproc.active_amount",
    "court.defendant_count",
    "court.defendant_amount",
    "fin.series",
    "fin.proceeds_last",
    "fin.profit_last",
    "fin.proceeds_change_pct",
    "fin.negative_capitals",
    "procurement.contracts_signed",
    "positive.count",
)


@dataclass(frozen=True)
class ToolContext:
    settings: Settings
    client: GroqClient
    persist: bool = True


ToolExecutor = Callable[[ToolContext, BaseModel], Awaitable[ToolResult]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: Type[BaseModel]
    output_model: Type[BaseModel]
    risk_class: str
    side_effects: str
    timeout_s: float
    result_size_limit: int
    retry_policy: str
    executor: ToolExecutor

    def public_contract(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_model.model_json_schema(),
            "output_schema": self.output_model.model_json_schema(),
            "risk_class": self.risk_class,
            "side_effects": self.side_effects,
            "timeout_seconds": self.timeout_s,
            "result_size_limit": self.result_size_limit,
            "retry_policy": self.retry_policy,
        }

class ToolRegistry:
    def __init__(self, definitions: List[ToolDefinition]):
        self._definitions = {definition.name: definition for definition in definitions}
        if len(self._definitions) != len(definitions):
            raise ValueError("Имена tools в registry должны быть уникальны")

    def visible_contracts(self) -> List[Dict[str, object]]:
        return [self._definitions[name].public_contract() for name in sorted(self._definitions)]

    def get_definition(self, name: str) -> Optional[ToolDefinition]:
        return self._definitions.get(name)

    async def execute(self, name: str, arguments: Dict[str, object], context: ToolContext) -> ToolResult:
        started = time.perf_counter()
        definition = self._definitions.get(name)
        if definition is None:
            return _error_result(
                "unknown_tool",
                "Запрошенный инструмент недоступен на этом этапе.",
                tool="unknown_tool",
                latency_ms=_elapsed_ms(started),
            )

        try:
            parsed_args = definition.input_model.model_validate(arguments)
        except ValidationError:
            return _error_result(
                "invalid_arguments",
                "Не удалось запустить проверку: аргументы инструмента не прошли проверку.",
                tool=definition.name,
                latency_ms=_elapsed_ms(started),
            )

        try:
            result = await asyncio.wait_for(
                definition.executor(context, parsed_args), timeout=definition.timeout_s
            )
        except CompanyNotFound:
            inn = getattr(parsed_args, "inn", "")
            return _error_result(
                "not_found",
                "Карточка по ИНН %s не найдена." % inn,
                tool=definition.name,
                latency_ms=_elapsed_ms(started),
            )
        except asyncio.TimeoutError:
            return _error_result(
                "timeout",
                "Проверка не завершилась в отведённое время. Попробуйте ещё раз.",
                tool=definition.name,
                latency_ms=_elapsed_ms(started),
                retryable=True,
            )
        except Exception:  # noqa: BLE001
            log.exception("Ошибка выполнения tool %s", definition.name)
            return _error_result(
                "internal_error",
                "Не удалось выполнить проверку из-за внутренней ошибки.",
                tool=definition.name,
                latency_ms=_elapsed_ms(started),
                retryable=True,
            )

        if result.status != "error":
            try:
                validated = definition.output_model.model_validate(result.data)
            except ValidationError:
                log.exception("Tool %s вернул данные вне output schema", definition.name)
                return _error_result(
                    "internal_error",
                    "Результат проверки не прошёл внутреннюю валидацию.",
                    tool=definition.name,
                    latency_ms=_elapsed_ms(started),
                )
            result = result.model_copy(update={"data": validated.model_dump(mode="json")})

        metadata = result.metadata.model_copy(update={"latency_ms": _elapsed_ms(started)})
        result = result.model_copy(update={"metadata": metadata})
        encoded_size = len(json.dumps(result.model_dump(mode="json"), ensure_ascii=False))
        if encoded_size > definition.result_size_limit:
            return _error_result(
                "result_too_large",
                "Результат проверки превышает допустимый размер.",
                tool=definition.name,
                latency_ms=_elapsed_ms(started),
            )
        return result


def build_tool_registry(settings: Settings) -> ToolRegistry:
    from .finance import execute_financial_data
    from .legal import execute_legal_data
    from .targeted_models import TargetedData

    return ToolRegistry([
        ToolDefinition(
            name="full_company_check",
            description=(
                "Полная проверка одного контрагента по явно указанному валидному ИНН. "
                "Не использовать для точечных вопросов или нескольких компаний."
            ),
            input_model=FullCompanyCheckArgs,
            output_model=FullCompanyCheckData,
            risk_class="read_only",
            side_effects="operational_audit_only",
            timeout_s=settings.agent_tool_timeout_s,
            result_size_limit=settings.agent_tool_result_max_chars,
            retry_policy="none",
            executor=_execute_full_company_check,
        ),
        *[
            ToolDefinition(
                name=name,
                description=description,
                input_model=FullCompanyCheckArgs,
                output_model=TargetedData,
                risk_class="read_only",
                side_effects="none",
                timeout_s=settings.agent_tool_timeout_s,
                result_size_limit=min(settings.agent_tool_result_max_chars, 40_000),
                retry_policy="none",
                executor=executor,
            )
            for name, description, executor in (
                ("get_financial_data", "Финансовые показатели и динамика одного контрагента по ИНН, без полной проверки.", execute_financial_data),
                ("get_legal_data", "Суды, исполнительные производства и правовые факты одного контрагента по ИНН, без полной проверки.", execute_legal_data),
            )
        ],
    ])


async def _execute_full_company_check(context: ToolContext, args: BaseModel) -> ToolResult:
    parsed = FullCompanyCheckArgs.model_validate(args)
    check_payload = await run_check(
        parsed.inn,
        context.settings,
        context.client,
        persist=context.persist,
    )
    check = CheckResponse.model_validate(check_payload)
    data, evidence = _compact_check(check)
    warnings: List[str] = []
    if check.status == "PARTIAL":
        warnings.append("Некоторые разделы удалось оценить только по фактам исходной карточки.")
    if check.grounding.unverified:
        warnings.append(
            "Часть утверждений не удалось подтвердить по источникам; они не включены в ответ."
        )
    return ToolResult(
        status="partial" if check.status == "PARTIAL" else "success",
        data=data.model_dump(mode="json"),
        evidence=evidence,
        warnings=warnings,
        freshness=ToolFreshness(report_date=check.company.report_date),
        metadata=ToolResultMetadata(
            tool="full_company_check",
            run_id=check.run_id,
            latency_ms=check.llm.latency_ms,
            calculator_version=check.llm.calculator_version,
        ),
    )


def _compact_check(check: CheckResponse) -> tuple[FullCompanyCheckData, List[Evidence]]:
    fact_index = {
        fact.id: fact
        for block in check.blocks
        for fact in block.facts
        if fact.id in PRESENTATION_FACT_IDS
    }
    facts: Dict[str, ToolFact] = {}
    evidence: List[Evidence] = []
    for fact_id in PRESENTATION_FACT_IDS:
        fact = fact_index.get(fact_id)
        if fact is None:
            continue
        tool_fact = ToolFact(
            id=_clean_text(fact.id, 160),
            label=_clean_text(fact.label, 240),
            value=fact.value,
            field_ref=_clean_text(fact.field_ref, 500),
            unit=_clean_optional(fact.unit, 40),
            source=_clean_text(fact.source, 60),
            comment=_clean_optional(fact.comment, 500),
        )
        facts[fact_id] = tool_fact
        evidence.append(_evidence_from_fact(tool_fact))

    company_payload = {
        key: _clean_optional(getattr(check.company, key), 800)
        for key in (
            "inn", "ogrn", "short_name", "full_name", "address", "status",
            "registration_date", "risk_level", "zsk_risk_level", "report_date",
        )
    }
    company_payload["years_from_registration"] = check.company.years_from_registration

    summary_points = [
        text for text in (_safe_model_text(item, 500) for item in check.summary.narrative_points)
        if text
    ][:3]
    summary = FullCheckSummary(
        verdict_group=check.summary.verdict_group,
        headline=_safe_model_text(check.summary.headline, 300),
        narrative_points=summary_points,
        data_gaps=[
            _clean_text(item, 500) for item in check.summary.data_gaps
            if item and not contains_unsafe_markup(item)
        ][:8],
        questions_to_ask=[
            _clean_text(item, 500) for item in check.summary.questions_to_ask
            if item and not contains_unsafe_markup(item)
        ][:8],
    )
    data = FullCompanyCheckData(
        check_run_id=check.run_id,
        pipeline_status=check.status,
        inn=check.inn,
        company=FullCheckCompany.model_validate(company_payload),
        coverage=FullCheckCoverage(
            filled_blocks=check.coverage.filled_blocks,
            total_blocks=check.coverage.total_blocks,
            coverage_pct=check.coverage.coverage_pct,
            empty_blocks=[_clean_text(item, 240) for item in check.coverage.empty_blocks],
        ),
        summary=summary,
        facts=facts,
        grounding=FullCheckGrounding.model_validate(check.grounding.model_dump()),
        llm_mode=_clean_text(check.llm.mode, 80),
        calculator_version=_clean_text(check.llm.calculator_version, 120),
    )
    return data, evidence


def display_fact_value(fact: ToolFact) -> str:
    value = fact.value
    if value is None or value == "":
        return "Нет данных"
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, (int, float)):
        return _format_number(value, fact.unit)
    if isinstance(value, list):
        if not value:
            return "Нет"
        if fact.id == "fin.series":
            years = [str(item.get("year")) for item in value
                     if isinstance(item, dict) and item.get("year") is not None]
            count = len(years)
            phrase = "отчётный период" if count == 1 else (
                "отчётных периода" if 2 <= count <= 4 else "отчётных периодов"
            )
            return "%s %s: %s" % (count, phrase, ", ".join(years))
        parts: List[str] = []
        for item in value[:8]:
            if isinstance(item, dict):
                label = item.get("meaning") or item.get("code") or item.get("name")
                parts.append(str(label) if label is not None else json.dumps(item, ensure_ascii=False))
            else:
                parts.append(str(item))
        suffix = " …" if len(value) > len(parts) else ""
        return _clean_text(", ".join(parts) + suffix, 1200)
    if isinstance(value, dict):
        return _clean_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), 1200)
    return _clean_text(str(value), 1200)


def _evidence_from_fact(fact: ToolFact) -> Evidence:
    if "reputationalRisks" in fact.field_ref:
        source = "source_signal"
    elif fact.source == "raw":
        source = "raw_fact"
    else:
        source = "derived_metric"
    return Evidence(
        id=fact.id,
        fact_id=fact.id,
        source=source,
        title=fact.label,
        field_ref=fact.field_ref,
        display_value=display_fact_value(fact),
        unit=fact.unit,
    )


def _safe_model_text(value: Optional[str], limit: int) -> str:
    """В rich UI допускается интерпретация модели, но не сгенерированные числа/markup."""
    forbidden_markers = ("report.", "field_ref", "fact_id", "```", "{", "}")
    if (
        not value
        or contains_unsafe_markup(value)
        or any(char.isdigit() for char in value)
        or any(marker in value.lower() for marker in forbidden_markers)
    ):
        return ""
    return _clean_text(value.strip(), limit)


def _clean_optional(value: object, limit: int) -> Optional[str]:
    if value is None:
        return None
    return _clean_text(str(value), limit)


def _clean_text(value: str, limit: int) -> str:
    return value.replace("<", "‹").replace(">", "›")[:limit]


def _format_number(value: float, unit: Optional[str]) -> str:
    number = float(value)
    if number.is_integer():
        text = f"{int(number):,}".replace(",", " ")
    else:
        text = f"{number:,.1f}".replace(",", " ").replace(".", ",")
    suffix = {"руб": " ₽", "%": " %", "шт": "", "лет": " лет"}.get(unit, "")
    return text + suffix


def _error_result(
    code: str,
    message: str,
    *,
    tool: str,
    latency_ms: int,
    retryable: bool = False,
) -> ToolResult:
    return ToolResult(
        status="error",
        error=ToolError(code=code, user_safe_message=message, retryable=retryable),
        metadata=ToolResultMetadata(tool=tool, latency_ms=latency_ms),
    )


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))
