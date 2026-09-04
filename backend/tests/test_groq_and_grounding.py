"""Разбор ответа Groq и обработка ссылок на несуществующие факты.

Настоящий ключ здесь не нужен: HTTP подменяется транспортом httpx.
"""
import asyncio
import json

import httpx
import pytest

from app.config import Settings
from app.domain.facts import build_all_blocks, build_coverage
from app.llm.agents import SummaryResult, run_block_agent
from app.llm.groq_client import GroqClient, LLMError
from app.pipeline import collect_statements, grounding_metrics


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
