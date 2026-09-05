"""External provenance, bounded hydration and full-check-only search behavior."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from app.agent.master_model import OpenRouterChatModel
from app.agent.models import MasterAnswer
from app.agent.news import _PublicationMetadata, _publication_date, hydrate_news
from app.config import Settings
from test_agent_runtime import (FailingToolCallingModel, _model, _runtime,
                                _tool_call, _verified_context)


def _choice(index=1, **extra):
    return dict(url=f"https://news.example/article/{index}",
                company_match="Совпадают ИНН, название и регион компании.",
                summary="По сообщению источника, компания заключила крупный контракт.", **extra)


def _answer(choices):
    return MasterAnswer(message="Внутренние факты проверены.", news_selection=choices)


def _citation(index=1, **extra):
    return {"type": "url_citation", "url_citation": {
        "url": f"https://news.example/article/{index}", "title": f"Крупный контракт {index}",
        "content": "Компания заключила крупный контракт.", **extra,
    }}


def _settings(**overrides):
    return Settings(_env_file=None, openrouter_api_key="test-key", **overrides)


@pytest.mark.parametrize("markup,expected", [
    ('<meta property="article:published_time" content="2026-09-03T10:00:00+03:00">', "2026-09-03"),
    ('<script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","datePublished":"2026-09-02"}]}</script>', "2026-09-02"),
    ('<meta property="article:modified_time" content="2026-09-03"><p>03.09.2026</p>', None),
    ('<meta itemprop="datePublished" content="2026-09-01"><meta property="article:published_time" content="2026-09-03">', None),
    ('<script type="application/ld+json">{"@type":"Organization","datePublished":"2026-09-02"}</script>', None),
])
def test_publication_date_requires_unambiguous_source_metadata(markup, expected):
    parser = _PublicationMetadata()
    parser.feed(markup)
    actual = parser.published()
    assert (actual.isoformat() if actual else None) == expected


def test_selection_schema_max_four_and_no_model_authored_metadata():
    with pytest.raises(ValidationError):
        _answer([_choice(i) for i in range(5)])
    with pytest.raises(ValidationError):
        _answer([_choice(title="Выдуманный заголовок")])


@pytest.mark.asyncio
async def test_hydration_uses_provider_metadata_deduplicates_and_bounds(monkeypatch):
    calls = []
    async def published(url, client):
        calls.append(url)
        return datetime.now(timezone.utc).date()
    monkeypatch.setattr("app.agent.news._publication_date", published)
    choices = [_choice(1), _choice(1), _choice(2), _choice(3)]
    rows, status = await hydrate_news([_citation(i) for i in range(1, 4)], _answer(choices),
                                      requested=True, settings=_settings())
    assert status == "completed"
    assert len(rows) == len(calls) == 3
    assert rows[0].title == "Крупный контракт 1"
    assert rows[0].source == "news.example"
    assert rows[0].summary == choices[0]["summary"]


@pytest.mark.asyncio
async def test_unreturned_url_and_directory_are_never_fetched(monkeypatch):
    async def forbidden(*args):
        pytest.fail("Cannot fetch model-invented URL")
    monkeypatch.setattr("app.agent.news._publication_date", forbidden)
    rows, status = await hydrate_news([_citation(url="https://rusprofile.ru/id/123")],
                                      _answer([_choice(999)]), requested=True, settings=_settings())
    assert rows == [] and status == "partial"


@pytest.mark.asyncio
async def test_stale_future_missing_dates_and_timeout(monkeypatch):
    today = datetime.now(timezone.utc).date()
    async def published(url, client):
        if url.endswith("/1"): return today - timedelta(days=91)
        if url.endswith("/2"): return today + timedelta(days=1)
        if url.endswith("/3"): return None
        await asyncio.sleep(1)
    monkeypatch.setattr("app.agent.news._publication_date", published)
    rows, status = await hydrate_news([_citation(i) for i in range(1, 5)],
                                      _answer([_choice(i) for i in range(1, 5)]),
                                      requested=True, settings=_settings(web_news_timeout_s=.01))
    assert rows == [] and status == "partial"


@pytest.mark.asyncio
async def test_empty_selection_and_failures_are_distinct():
    for answer, requested, expected in [
        (_answer([]), True, "completed"), (None, True, "selection_unavailable"),
        (_answer([]), False, "unavailable"),
    ]:
        rows, status = await hydrate_news([], answer, requested=requested, settings=_settings())
        assert rows == [] and status == expected


@pytest.mark.asyncio
async def test_metadata_redirect_to_private_address_is_blocked(monkeypatch):
    loop = asyncio.get_running_loop()
    async def addresses(host, *args, **kwargs):
        return [(2, 1, 6, "", ("127.0.0.1" if host == "127.0.0.1" else "93.184.216.34", 443))]
    monkeypatch.setattr(loop, "getaddrinfo", addresses)
    calls = []
    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="Non-public"):
            await _publication_date("https://news.example/article", client)
    assert calls == ["https://93.184.216.34/article"]


def test_openrouter_adapter_preserves_annotations_without_inventing_them():
    model = OpenRouterChatModel(api_key="test", model="fake")
    payload = {"choices": [{"message": {"role": "assistant", "content": "{}", "annotations": [_citation()]},
                            "finish_reason": "stop"}]}
    message = model._create_chat_result(payload).generations[0].message
    assert message.additional_kwargs["annotations"] == [_citation()]


@pytest.mark.asyncio
async def test_search_model_failure_preserves_internal_fallback(monkeypatch, check_payload):
    async def full(*args, **kwargs): return check_payload
    monkeypatch.setattr("app.agent.tools.run_check", full)
    model = FailingToolCallingModel(responses=[AIMessage(content="")])
    result = await _runtime(model, direct_dispatch=True, grounding_debug=False).run(
        "Проверь контрагента 6165169320"
    )
    assert result.leading_artifact is not None and result.evidence
    assert result.metadata.synthesis == "fallback"
    assert result.metadata.model_calls == result.metadata.tool_calls == 1
    assert result.external_news == []
    assert result.external_news_status == "selection_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [True, False])
async def test_full_check_search_is_automatic_separate_and_once(monkeypatch, check_payload, direct):
    async def full(*args, **kwargs): return check_payload
    async def published(*args): return datetime.now(timezone.utc).date()
    monkeypatch.setattr("app.agent.tools.run_check", full)
    monkeypatch.setattr("app.agent.news._publication_date", published)
    responses = [] if direct else [AIMessage(content="", tool_calls=[_tool_call()])]
    responses += [AIMessage(content=_answer([_choice()]).model_dump_json(), additional_kwargs={"annotations": [_citation()]}),
                  AIMessage(content='{"message":"Объяснение прежних внутренних фактов.","artifact":"none"}')]
    model = _model(*responses)
    runtime = _runtime(model, direct_dispatch=direct, grounding_debug=False)
    first = await runtime.run("Проверь контрагента 6165169320")
    assert first.external_news_status == "completed"
    assert len(first.external_news) == 1
    assert first.metadata.model_calls == (1 if direct else 2)
    assert first.metadata.tool_calls == 1
    assert first.leading_artifact.bank_risk_level == check_payload["company"]["risk_level"]
    assert "news.example" not in json.dumps(_verified_context(model._messages[-1]))
    follow = await runtime.run("Объясни проще", first.conversation_id)
    assert follow.external_news == [] and follow.external_news_status is None
    assert follow.metadata.tool_calls == 0
    assert "news.example" not in json.dumps(_verified_context(model._messages[-1]))


@pytest.mark.asyncio
@pytest.mark.parametrize("question,search", [
    ("Проверь контрагента 6165169320", True),
    ("Финансы 6165169320", False),
    ("Суды 6165169320", False),
    ("Сравни 6165169320 и 1684017097", False),
])
async def test_actual_openrouter_request_enables_search_only_for_full_check(monkeypatch, check_payload, documents, question, search):
    async def full(*args, **kwargs): return check_payload
    async def snapshot(inn):
        document = next(item for item in documents if item["report"]["baseInfo"]["inn"] == inn)
        return {"inn": inn, "document": document}
    monkeypatch.setattr("app.agent.tools.run_check", full)
    monkeypatch.setattr("app.infrastructure.repository.get_latest_snapshot", snapshot)
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "test", "object": "chat.completion", "created": 0, "model": "fake",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": _answer([]).model_dump_json()}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        })
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        model = OpenRouterChatModel(api_key="test", model="fake", http_async_client=client,
                                    extra_body={"provider": {"sort": "throughput"}})
        runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
        result = await runtime.run(question)
        if search:
            follow = await runtime.run("Объясни проще", result.conversation_id)
            assert follow.metadata.tool_calls == 0
            assert "plugins" not in bodies[-1]
            assert follow.external_news == [] and follow.external_news_status is None
    assert result.metadata.tool_calls == 1
    assert len(bodies) == (2 if search else 1)
    assert bool(bodies[0].get("plugins")) is search
    assert bodies[0]["provider"] == {"sort": "throughput"}
    assert result.external_news == []
    assert result.external_news_status == ("completed" if search else None)
