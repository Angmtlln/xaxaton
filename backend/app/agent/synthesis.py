"""Verified context for Master reasoning and backend hydration.

This module deliberately contains no catalog of prose conclusions. Domain data
is normalized into metrics, series, events, statuses, coverage and explicit
policy signals. Evidence IDs remain provenance links, not a reasoning whitelist.
"""
from __future__ import annotations

import json
import re
from typing import Iterable

from .models import FullCompanyCheckData, MasterAnswer, ToolFact, ToolResult
from .targeted_models import ComparisonData, TargetedData
from .tools import _evidence_from_fact, display_fact_value


# Часть провайдеров отдаёт структурный ответ вместе с рассуждением или в
# Markdown-блоке. Достаём сам объект: это разбор контракта, а не правка прозы.
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def clean_model_text(value: str) -> str:
    """Снимает служебную обёртку провайдера, не трогая сам текст ответа."""
    text = THINK_BLOCK_RE.sub("", value).strip()
    fenced = JSON_FENCE_RE.search(text)
    return fenced.group(1).strip() if fenced else text


def json_payload(value: str) -> dict:
    """Объект из ответа модели; за его смысл отвечает валидация схемы."""
    text = clean_model_text(value)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("Model response carries no JSON object: %r" % text[:120])
    return json.loads(text[start:end + 1])


def _validated_data(result: ToolResult) -> FullCompanyCheckData | TargetedData | ComparisonData:
    if result.metadata.tool == "full_company_check":
        return FullCompanyCheckData.model_validate(result.data)
    if result.metadata.tool in {"get_financial_data", "get_legal_data"}:
        return TargetedData.model_validate(result.data)
    if result.metadata.tool == "compare_companies":
        return ComparisonData.model_validate(result.data)
    raise ValueError("Unsupported ToolResult domain")


def verified_evidence(data, result: ToolResult):
    """Rebuild every evidence row from facts and reject any foreign reference."""
    expected = {key: _evidence_from_fact(fact) for key, fact in data.facts.items()}
    evidence = {}
    for item in result.evidence:
        if item.id in evidence or expected.get(item.id) != item:
            raise ValueError("Tool evidence does not match backend fact")
        evidence[item.id] = item
    if data.facts.keys() - evidence.keys():
        raise ValueError("Tool observations lack verified evidence")
    return evidence


def _observation(fact: ToolFact) -> dict:
    return {
        "id": fact.id,
        "label": fact.label,
        "value": fact.value,
        "display_value": display_fact_value(fact),
        "unit": fact.unit,
        "evidence_ids": [fact.id],
    }


def _observations(data, ids: Iterable[str]) -> list[dict]:
    return [_observation(data.facts[fact_id]) for fact_id in ids if fact_id in data.facts]


def normalized_tool_context(result: ToolResult) -> dict:
    """Return compact verified data for one Master turn; never legacy prose."""
    if result.status == "error":
        raise ValueError("Error ToolResult cannot become trusted context")
    data = _validated_data(result)
    evidence = verified_evidence(data, result)
    policy_ids = []
    for signal in data.policy_signals:
        if signal.id in policy_ids or set(signal.evidence_ids) - evidence.keys():
            raise ValueError("Policy signal has invalid provenance")
        policy_ids.append(signal.id)

    if isinstance(data, ComparisonData):
        return _comparison_context(data, result, evidence)

    full = isinstance(data, FullCompanyCheckData)
    domain = "full_check" if full else data.domain
    coverage = (
        {
            "state": data.availability,
            "filled_blocks": data.coverage.filled_blocks,
            "total_blocks": data.coverage.total_blocks,
            "coverage_pct": data.coverage.coverage_pct,
            "empty_blocks": data.coverage.empty_blocks,
        }
        if full
        else {"state": data.availability, "gaps": data.gaps}
    )
    company = {
        "inn": data.company.inn,
        "ogrn": data.company.ogrn,
        "name": data.company.short_name or data.company.full_name,
        "status": data.company.status,
    }
    return {
        "schema_version": "verified-context-1",
        "tool": result.metadata.tool,
        "domain": domain,
        "status": result.status,
        "company": company,
        "metrics": _observations(data, data.metric_ids),
        "series": _observations(data, data.series_ids),
        "events": _observations(data, data.event_ids),
        "statuses": _observations(data, data.status_ids),
        "coverage": coverage,
        "policy_signals": [signal.model_dump(mode="json") for signal in data.policy_signals],
        "evidence": [item.model_dump(mode="json") for item in evidence.values()],
        "warnings": list(dict.fromkeys(result.warnings)),
    }


def _comparison_context(data: ComparisonData, result: ToolResult, evidence) -> dict:
    """Компактный контекст сравнения: по компании на запись, без N отчётов."""
    companies = []
    for item in data.companies:
        companies.append({
            "inn": item.company.inn,
            "name": item.company.short_name or item.company.full_name,
            "status": item.company.status,
            "coverage": {"state": item.availability, "gaps": item.gaps},
            "metrics": _observations(data, item.metric_ids),
            "statuses": _observations(data, item.status_ids),
            "policy_signals": [
                signal.model_dump(mode="json") for signal in data.policy_signals
                if signal.id in set(item.policy_signal_ids)
            ],
        })
    return {
        "schema_version": "verified-context-1",
        "tool": result.metadata.tool,
        "domain": "comparison",
        "status": result.status,
        "focus": list(data.focus),
        "companies": companies,
        "evidence": [item.model_dump(mode="json") for item in evidence.values()],
        "warnings": list(dict.fromkeys(result.warnings)),
    }


def parse_master_answer(value, *, allowed_artifacts: Iterable[str]) -> MasterAnswer:
    """Validate structure only; natural-language meaning is checked separately."""
    proposal = MasterAnswer.model_validate(
        _answer_payload(value) if isinstance(value, str) else value
    )
    if proposal.artifact not in set(allowed_artifacts):
        raise ValueError("Artifact is unavailable for this turn")
    return proposal


def _answer_payload(value: str) -> dict:
    """Часть моделей отвечает прозой вместо контракта.

    Текст — это и есть ответ пользователю, и он проходит ту же валидацию, что и
    поле message: SafeText, проверка backend-owned значений и заземление.
    Артефакт при этом остаётся за бэкендом, поэтому подставляется none.
    """
    try:
        return json_payload(value)
    except ValueError:
        text = clean_model_text(value)
        if not text:
            raise
        return {"message": text, "artifact": "none"}


def allowed_artifacts(result: ToolResult | None, *, contextual: bool) -> tuple[str, ...]:
    if contextual or result is None:
        return ("none",)
    if result.metadata.tool == "compare_companies":
        # Таблицу сравнения бэкенд добавляет сам: это детерминированный артефакт.
        return ("none",)
    if result.metadata.tool in {"full_company_check", "get_financial_data"}:
        return ("none", "metrics", "chart")
    return ("none", "metrics")
