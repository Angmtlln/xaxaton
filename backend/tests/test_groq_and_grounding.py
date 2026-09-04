"""Разбор ответа Groq и обработка ссылок на несуществующие факты.

Настоящий ключ здесь не нужен: HTTP подменяется транспортом httpx.
"""
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.domain.facts import build_all_blocks, build_coverage
from app.llm.agents import (SUMMARY_POINT_CHAR_LIMIT, SUMMARY_POINT_MAX,
                            SUMMARY_POINTS_TOTAL_LIMIT, BlockResult, SummaryResult,
                            normalize_summary_points, run_block_agent,
                            run_summary_agent)
from app.llm.groq_client import GroqClient, LLMError
from app.llm.prompts import SUMMARY_SYSTEM_PROMPT
from app.pipeline import collect_statements, grounding_metrics, select_key_facts


def _settings():
    return Settings(groq_api_key="test-key", llm_mock=False,
                    database_url="postgresql://localhost/none")


def _groq_answer(payload: dict, model: str = "llama-3.3-70b-versatile") -> httpx.Response:
    return httpx.Response(200, json={
        "model": model,
        "choices": [{"message": {"role": "assistant", "content": json.dumps(payload, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 50},
    })


BLOCK_ANSWER = {
    "signal": "ATTENTION",
    "headline": "Компания действующая, есть что уточнить",
    "facts_sentence": "Компании 15 лет, кодов ОКВЭД 48.",
    "interpretation": "Профиль деятельности стоит уточнить.",
    "findings": [
        {"text": "Заявлено 48 кодов ОКВЭД", "severity": "medium", "fact_id": "okved.total_count"},
        {"text": "Выдуманное наблюдение", "severity": "high", "fact_id": "не.существует"},
        {"text": "Без ссылки на факт", "severity": "low"},
    ],
    "data_gaps": ["нет данных о лицензиях"],
    "cannot_assess": ["опыт госзакупок"],
}


def _run_block(document, transport):
    settings = _settings()
    client = GroqClient(settings, client=httpx.AsyncClient(transport=transport))
    blocks = build_all_blocks(document)
    coverage = build_coverage(document)
    company = {"inn": document["report"]["baseInfo"]["inn"]}
    return blocks, asyncio.run(
        run_block_agent(client, settings, "identity", blocks["identity"], company, coverage))


def _run_summary(document, transport):
    settings = _settings()
    client = GroqClient(settings, client=httpx.AsyncClient(transport=transport))
    blocks = build_all_blocks(document)
    coverage = build_coverage(document)
    base = document["report"]["baseInfo"]
    company = {"inn": base["inn"], "short_name": base.get("shortName"),
               "risk_level": base.get("riskLevel"),
               "zsk_risk_level": document["report"].get("zskRiskLevel")}
    block_results = {key: BlockResult(block=key, signal="NORM") for key in blocks}
    key_facts = select_key_facts(blocks)
    all_fact_ids = {fact.id for block in blocks.values() for fact in block.facts}
    return asyncio.run(run_summary_agent(
        client, settings, company, block_results, key_facts, coverage,
        all_fact_ids=all_fact_ids))


def test_block_agent_parses_groq_answer(document):
    transport = httpx.MockTransport(lambda request: _groq_answer(BLOCK_ANSWER))
    _, result = _run_block(document, transport)

    assert result.signal == "ATTENTION"
    assert result.headline == BLOCK_ANSWER["headline"]
    assert result.prompt_tokens == 100 and result.completion_tokens == 50
    assert len(result.findings) == 3


def test_unknown_fact_reference_is_marked_unverified(document):
    """Ссылка на несуществующий факт не должна пройти как подтверждённая."""
    transport = httpx.MockTransport(lambda request: _groq_answer(BLOCK_ANSWER))
    blocks, result = _run_block(document, transport)

    grounded = {f["text"]: f["grounded"] for f in result.findings}
    assert grounded["Заявлено 48 кодов ОКВЭД"] is True
    assert grounded["Выдуманное наблюдение"] is False
    assert grounded["Без ссылки на факт"] is False

    statements = collect_statements(blocks, {"identity": result}, SummaryResult())
    kinds = {s["statement"]: s["grounding"] for s in statements}
    assert kinds["Заявлено 48 кодов ОКВЭД"] == "GROUNDED"
    assert kinds["Выдуманное наблюдение"] == "UNVERIFIED"
    assert kinds["Без ссылки на факт"] == "NO_REF"
    assert grounding_metrics(statements)["unverified"] == 1


def test_model_wrapped_json_is_recovered(document):
    """Малая модель любит обрамлять JSON текстом и markdown."""
    content = "Вот результат:\n```json\n%s\n```" % json.dumps(BLOCK_ANSWER, ensure_ascii=False)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        "model": "llama-3.3-70b-versatile",
        "choices": [{"message": {"content": content}}],
        "usage": {},
    }))
    _, result = _run_block(document, transport)
    assert result.signal == "ATTENTION"
    assert result.error is None


def test_summary_agent_uses_bounded_points_and_checks_grounding(document):
    points = [
        "Компания действует, банковские оценки приведены без пересчёта.",
        "Перед сделкой требуется уточнить исполнительные производства.",
        "Запросите документы по отмеченным фактам и пробелам данных.",
    ]
    answer = {
        "verdict_group": "ENHANCED_CHECK",
        "headline": "Работать можно после уточнения отмеченных фактов",
        "narrative_points": points,
        "key_numbers": [],
        "top_risks": [{"text": "Неподтверждённый риск", "severity": "high",
                       "fact_id": "не.существует"}],
        "positives": [], "data_gaps": [], "questions_to_ask": ["Что требуется уточнить?"],
    }
    result = _run_summary(
        document, httpx.MockTransport(lambda request: _groq_answer(answer)))

    assert result.narrative_points == points
    assert result.narrative == " ".join(points)
    assert result.top_risks[0]["grounded"] is False


def test_summary_points_are_normalized_when_model_ignores_limits():
    excessive = [
        "Первый тезис " + "с очень подробным описанием " * 10,
        "Второй тезис " + "с повторяющимися деталями " * 10,
        "Третий тезис " + "с ненужными подробностями " * 10,
        "Четвёртый лишний тезис.",
    ]
    points = normalize_summary_points(excessive)
    legacy = normalize_summary_points(
        None, "Первый вывод. Второй вывод. Третий вывод. Четвёртый лишний вывод.")
    too_short = normalize_summary_points(["Единственный тезис модели."])

    assert len(points) == SUMMARY_POINT_MAX
    assert all(len(point) <= SUMMARY_POINT_CHAR_LIMIT for point in points)
    assert sum(map(len, points)) <= SUMMARY_POINTS_TOTAL_LIMIT
    assert legacy == ["Первый вывод.", "Второй вывод.", "Третий вывод."]
    assert len(too_short) == 2


def test_summary_prompt_requests_compact_structured_points():
    assert '"narrative_points"' in SUMMARY_SYSTEM_PROMPT
    assert "2–3" in SUMMARY_SYSTEM_PROMPT
    assert "135 символов" in SUMMARY_SYSTEM_PROMPT
    assert "360 символов" in SUMMARY_SYSTEM_PROMPT


def test_falls_back_to_deterministic_when_groq_fails(document):
    """Отказ модели не роняет проход: включается шаблонный режим."""
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="upstream error"))
    _, result = _run_block(document, transport)

    assert result.degraded is True
    assert result.error and "500" in result.error
    assert result.model == "deterministic"
    assert result.facts_sentence


def test_missing_key_raises():
    settings = Settings(groq_api_key=None, llm_mock=False, database_url="postgresql://localhost/none")
    client = GroqClient(settings)
    with pytest.raises(LLMError):
        asyncio.run(client.complete_json(model="m", system="s", user="u", temperature=0.1))


def test_rate_limit_switches_to_next_model():
    """429 на первой модели не ждёт минуту, а уходит на следующую."""
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        seen.append(model)
        if model == "openai/gpt-oss-120b":
            return httpx.Response(429, json={"error": {
                "message": "Rate limit reached for model `openai/gpt-oss-120b` ... "
                           "Please try again in 25.1775s",
                "code": "rate_limit_exceeded"}})
        return _groq_answer({"signal": "NORM", "headline": "ок"}, model=model)

    settings = Settings(groq_api_key="test-key", llm_mock=False,
                        database_url="postgresql://localhost/none")
    client = GroqClient(settings, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    response = asyncio.run(client.complete_json(
        model="openai/gpt-oss-120b", system="s", user="u", temperature=0.1,
        fallback_models=["qwen/qwen3.8-27b"]))

    assert seen == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
    assert response.model == "qwen/qwen3.8-27b"
    assert response.json_payload()["signal"] == "NORM"
