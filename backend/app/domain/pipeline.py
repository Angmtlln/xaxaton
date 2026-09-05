"""Один проход проверки контрагента по одному ИНН.

    ИНН
     ↓ карточка отчёта из PostgreSQL (последний снапшот)
     ↓ детерминированный слой фактов, 4 блока  (S2)
     ↓ 4 доменных агента Groq параллельно
     ↓ Summary-LLM поверх четырёх блочных резюме
     ↓ guardrails + заземление утверждений        (S5, H3)
     ↓ запись прогона в audit.* и ответ API
"""
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

from app.infrastructure import repository
from app.config import Settings
from app.domain import facts as facts_mod
from app.domain.facts import BLOCK_KEYS, FactBlock
from app.llm.agents import (BlockResult, SummaryResult, enforce_guardrails,
                            run_block_agents, run_summary_agent)
from app.llm.groq_client import GroqClient

log = logging.getLogger(__name__)


class CompanyNotFound(LookupError):
    """По ИНН нет карточки в загруженном снапшоте."""


# Факты, которые всегда уходят в Summary-LLM: короткий срез на экран.
KEY_FACT_IDS = [
    "company.name", "company.inn", "company.age_years", "company.status",
    "okved.total_count", "owners.share_capital", "owners.cofounders_count",
    "related.count",
    "bank.risk_level", "bank.zsk_level", "flags.negative_count",
    "flags.hard_stop_codes", "flags.green_with_hard_stop",
    "execproc.active_count", "execproc.active_amount",
    "court.defendant_count", "court.defendant_amount",
    "inspections.violations_count",
    "fin.last_year", "fin.proceeds_last", "fin.profit_last",
    "fin.proceeds_change_pct", "fin.negative_capitals",
    "procurement.contracts_signed", "license.active_count", "positive.count",
]


def select_key_facts(blocks: Dict[str, FactBlock]) -> List[Dict[str, Any]]:
    index = {f.id: f for blk in blocks.values() for f in blk.facts}
    out: List[Dict[str, Any]] = []
    for fact_id in KEY_FACT_IDS:
        fact = index.get(fact_id)
        if fact is None:
            continue
        value = fact.to_dict()["value"]
        if value in (None, [], {}, ""):
            continue
        out.append({"fact_id": fact.id, "label": fact.label, "value": value,
                    "field_ref": fact.field_ref, "unit": fact.unit})
    return out


def collect_statements(blocks: Dict[str, FactBlock], block_results: Dict[str, BlockResult],
                       summary: SummaryResult) -> List[Dict[str, Any]]:
    """Разбирает ответ агентов на отдельные утверждения со ссылками (S5)."""
    index = {f.id: f for blk in blocks.values() for f in blk.facts}
    statements: List[Dict[str, Any]] = []

    def add(block: Optional[str], text: str, fact_id: Optional[str]) -> None:
        if not text:
            return
        fact = index.get(fact_id) if fact_id else None
        if fact is not None:
            grounding = "GROUNDED"
            field_ref: Optional[str] = fact.field_ref
            value = str(fact.to_dict()["value"])[:500]
        elif fact_id:
            grounding, field_ref, value = "UNVERIFIED", None, None
        else:
            grounding, field_ref, value = "NO_REF", None, None
        statements.append({"block": block, "statement": text, "fact_id": fact_id,
                           "field_ref": field_ref, "fact_value": value, "grounding": grounding})

    for key, res in block_results.items():
        for finding in res.findings:
            add(key, finding.get("text", ""), finding.get("fact_id"))
    for item in summary.top_risks:
        add(None, item.get("text", ""), item.get("fact_id"))
    for item in summary.key_numbers:
        add(None, "%s: %s" % (item.get("label", ""), item.get("value", "")), item.get("fact_id"))
    for item in summary.positives:
        add(None, item.get("text", ""), item.get("fact_id"))
    return statements


def grounding_metrics(statements: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(statements)
    grounded = sum(1 for s in statements if s["grounding"] == "GROUNDED")
    unverified = sum(1 for s in statements if s["grounding"] == "UNVERIFIED")
    return {
        "statements": total,
        "grounded": grounded,
        "unverified": unverified,
        "no_ref": total - grounded - unverified,
        "grounded_pct": round(grounded / total * 100, 1) if total else 0.0,
    }


def _company_card(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "inn": snapshot["inn"],
        "ogrn": snapshot.get("ogrn"),
        "short_name": snapshot.get("short_name"),
        "full_name": snapshot.get("full_name"),
        "address": snapshot.get("address"),
        "status": snapshot.get("status"),
        "registration_date": _iso(snapshot.get("registration_date")),
        "years_from_registration": snapshot.get("years_from_registration"),
        "risk_level": snapshot.get("risk_level"),
        "zsk_risk_level": snapshot.get("zsk_risk_level"),
        "report_date": _iso(snapshot.get("report_date")),
    }


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def run_check(inn: str, settings: Settings, client: GroqClient,
                    persist: bool = True, *, include_summary: bool = True) -> Dict[str, Any]:
    """Полный проход; chat пропускает только legacy Summary, сохраняя аудит блоков."""
    started = time.perf_counter()

    snapshot = await repository.get_latest_snapshot(inn)
    if snapshot is None:
        raise CompanyNotFound(inn)

    document = snapshot.get("document")
    if not document:
        raise CompanyNotFound("%s: карточка отчёта пуста" % inn)

    # 1. Детерминированный слой: 4 блока фактов + паспорт полноты.
    blocks = facts_mod.build_all_blocks(document)
    coverage = facts_mod.build_coverage(document)
    company = _company_card(snapshot)
    key_facts = select_key_facts(blocks)

    llm_mode = "groq" if client.enabled else "mock"
    run_id: Optional[str] = None
    if persist:
        run_id = await repository.create_run(
            inn=inn, company_id=snapshot.get("company_id"), snapshot_id=snapshot.get("snapshot_id"),
            block_model=settings.groq_block_model, summary_model=settings.groq_summary_model if include_summary else "not_requested",
            calculator_ver=settings.calculator_version, llm_mode=llm_mode)
        await repository.save_facts(
            snapshot["snapshot_id"], settings.calculator_version,
            {key: blk.to_dict() for key, blk in blocks.items()})

    # 2. Четыре доменных агента параллельно.
    block_results = await run_block_agents(client, settings, blocks, company, coverage)

    # 3. Summary-LLM поверх блочных резюме.
    all_fact_ids = {f.id for blk in blocks.values() for f in blk.facts}
    summary = (
        await run_summary_agent(client, settings, company, block_results, key_facts,
                                coverage, all_fact_ids=all_fact_ids)
        if include_summary else SummaryResult(model="not_requested")
    )

    # 4. Защитные слои.
    block_results, summary, guardrail_notes = enforce_guardrails(blocks, block_results, summary)
    statements = collect_statements(blocks, block_results, summary)
    metrics = grounding_metrics(statements)

    total_prompt = sum(r.prompt_tokens for r in block_results.values()) + summary.prompt_tokens
    total_completion = sum(r.completion_tokens for r in block_results.values()) + summary.completion_tokens
    latency_ms = int((time.perf_counter() - started) * 1000)
    degraded = any(r.degraded for r in block_results.values()) or summary.degraded
    status = "PARTIAL" if degraded else "SUCCEEDED"

    errors = [r.error for r in block_results.values() if r.error]
    if summary.error:
        errors.append(summary.error)

    if persist and run_id:
        for res in block_results.values():
            await repository.save_block_result(run_id, _block_payload(res))
        if include_summary:
            await repository.save_summary(run_id, _summary_payload(summary))
        await repository.save_statements(run_id, statements)
        await repository.finish_run(run_id, status, latency_ms, total_prompt, total_completion,
                                    error="; ".join(sorted(set(errors))) or None)

    return {
        "run_id": run_id,
        "status": status,
        "inn": inn,
        "company": company,
        "coverage": coverage,
        "summary": _summary_public(summary),
        "blocks": [_block_public(block_results[k], blocks[k]) for k in BLOCK_KEYS if k in block_results],
        "key_facts": key_facts,
        "grounding": metrics,
        "guardrail_notes": guardrail_notes,
        "llm": {
            "mode": llm_mode,
            "block_model": settings.groq_block_model if llm_mode == "groq" else "deterministic",
            "block_models": settings.block_models() if llm_mode == "groq" else {},
            "summary_model": (settings.groq_summary_model if llm_mode == "groq" else "deterministic")
            if include_summary else "not_requested",
            "calculator_version": settings.calculator_version,
            "prompt_tokens": total_prompt,
            "completion_tokens": total_completion,
            "latency_ms": latency_ms,
        },
    }


def _block_payload(res: BlockResult) -> Dict[str, Any]:
    return {
        "block": res.block, "signal": res.signal, "headline": res.headline,
        "facts_sentence": res.facts_sentence, "interpretation": res.interpretation,
        "findings": res.findings, "data_gaps": res.data_gaps,
        "cannot_assess": res.cannot_assess, "facts_input": res.facts_input,
        "model": res.model, "latency_ms": res.latency_ms,
        "prompt_tokens": res.prompt_tokens, "completion_tokens": res.completion_tokens,
        "raw_response": res.raw_response, "error": res.error,
    }


def _block_public(res: BlockResult, fact_block: FactBlock) -> Dict[str, Any]:
    return {
        "block": res.block,
        "title": fact_block.title,
        "signal": res.signal,
        "headline": res.headline,
        "facts_sentence": res.facts_sentence,
        "interpretation": res.interpretation,
        "findings": res.findings,
        "data_gaps": res.data_gaps,
        "cannot_assess": res.cannot_assess,
        "facts": [f.to_dict() for f in fact_block.facts],
        "model": res.model,
        "latency_ms": res.latency_ms,
        "error": res.error,
    }


def _summary_public(summary: SummaryResult) -> Dict[str, Any]:
    """То же самое, но без сырого ответа модели: наружу он не нужен."""
    payload = _summary_payload(summary)
    payload.pop("raw_response", None)
    return payload


def _summary_payload(summary: SummaryResult) -> Dict[str, Any]:
    return {
        "verdict_group": summary.verdict_group, "headline": summary.headline,
        "narrative": summary.narrative, "narrative_points": summary.narrative_points,
        "key_numbers": summary.key_numbers,
        "top_risks": summary.top_risks, "positives": summary.positives,
        "data_gaps": summary.data_gaps, "questions_to_ask": summary.questions_to_ask,
        "model": summary.model, "latency_ms": summary.latency_ms,
        "prompt_tokens": summary.prompt_tokens, "completion_tokens": summary.completion_tokens,
        "raw_response": summary.raw_response, "error": summary.error,
    }
