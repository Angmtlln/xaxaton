"""Проход в детерминированном режиме, без сети и без базы."""
import asyncio

import pytest

from app.config import Settings
from app.domain.facts import build_all_blocks, build_coverage
from app.llm.agents import (SUMMARY_POINT_CHAR_LIMIT, SUMMARY_POINT_MAX,
                            SUMMARY_POINT_MIN, SUMMARY_POINTS_TOTAL_LIMIT,
                            enforce_guardrails, run_block_agents, run_summary_agent)
from app.llm.groq_client import GroqClient
from app.domain.pipeline import collect_statements, grounding_metrics, select_key_facts


def _run(doc):
    settings = Settings(llm_mock=True, groq_api_key=None)
    client = GroqClient(settings)
    base = doc["report"]["baseInfo"]
    company = {"inn": base["inn"], "short_name": base.get("shortName"),
               "risk_level": base.get("riskLevel"),
               "zsk_risk_level": doc["report"].get("zskRiskLevel")}

    blocks = build_all_blocks(doc)
    coverage = build_coverage(doc)
    key_facts = select_key_facts(blocks)

    async def _inner():
        results = await run_block_agents(client, settings, blocks, company, coverage)
        summary = await run_summary_agent(client, settings, company, results, key_facts, coverage)
        return enforce_guardrails(blocks, results, summary)

    results, summary, notes = asyncio.run(_inner())
    statements = collect_statements(blocks, results, summary)
    return blocks, results, summary, notes, statements


def test_four_agents_answer(document):
    _, results, summary, _, _ = _run(document)
    assert set(results) == {"identity", "reliability", "finance", "experience"}
    for res in results.values():
        assert res.signal in {"NORM", "ATTENTION", "RISK", "NO_DATA"}
        assert res.facts_sentence
    assert summary.verdict_group in {"STOP", "ENHANCED_CHECK", "CONDITIONALLY_OK", "NO_DATA"}
    assert SUMMARY_POINT_MIN <= len(summary.narrative_points) <= SUMMARY_POINT_MAX
    assert all(len(point) <= SUMMARY_POINT_CHAR_LIMIT for point in summary.narrative_points)
    assert sum(map(len, summary.narrative_points)) <= SUMMARY_POINTS_TOTAL_LIMIT
    assert summary.narrative == " ".join(summary.narrative_points)


def test_no_hallucinated_references(documents):
    """Каждая ссылка агента должна вести на существующий факт (S5)."""
    for doc in documents[:10]:
        _, _, _, _, statements = _run(doc)
        metrics = grounding_metrics(statements)
        assert metrics["unverified"] == 0
        assert metrics["grounded_pct"] >= 95.0


def test_green_with_hard_stop_forces_stop(documents):
    """Зелёный светофор не может дать мягкий вывод при жёстком факте."""
    target = None
    for doc in documents:
        negative = [n.get("code") for n in doc["report"]["reputationalRisks"].get("negative") or []]
        if doc["report"].get("zskRiskLevel") == "GREEN" and "fnsBlocking" in negative:
            target = doc
            break
    if target is None:
        pytest.skip("в выгрузке нет карточки GREEN с блокировкой счетов")

    blocks, results, summary, _, _ = _run(target)
    assert summary.verdict_group == "STOP"
    assert results["reliability"].signal == "RISK"
    assert any(r.get("fact_id") == "flags.hard_stop_codes" for r in summary.top_risks)

    # Даже если модель ответила мягко, защитный слой поднимает вывод.
    results["reliability"].signal = "NORM"
    summary.verdict_group = "CONDITIONALLY_OK"
    summary.top_risks = []
    results, summary, notes = enforce_guardrails(blocks, results, summary)
    assert summary.verdict_group == "STOP"
    assert results["reliability"].signal == "RISK"
    assert notes
    assert summary.top_risks[0]["added_by"] == "guardrail"


def test_execution_proceedings_are_attention_not_red(documents):
    """Одиночное исполнительное производство не выглядит как STOP без жёсткого факта."""
    target = next(d for d in documents if d["report"]["baseInfo"]["inn"] == "0278949271")
    _, results, summary, _, _ = _run(target)

    reliability_findings = {
        item["fact_id"]: item for item in results["reliability"].findings
    }
    summary_risks = {item["fact_id"]: item for item in summary.top_risks}
    assert results["reliability"].signal == "ATTENTION"
    assert reliability_findings["execproc.active_count"]["severity"] == "medium"
    assert summary_risks["execproc.active_count"]["severity"] == "medium"
    assert summary.verdict_group == "ENHANCED_CHECK"


def test_empty_card_says_no_data(documents):
    """Пустой блок обязан честно отвечать «невозможно оценить»."""
    empty = [d for d in documents if not (d["report"].get("finReports") or [])]
    if not empty:
        pytest.skip("нет карточек без финансовой отчётности")
    _, results, _, _, _ = _run(empty[0])
    finance = results["finance"]
    assert finance.signal == "NO_DATA"
    assert finance.cannot_assess
