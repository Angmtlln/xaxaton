"""Routing, budgets и typed tool errors первого agent-first vertical slice."""
import asyncio
import json

import httpx
import pytest

from app.agent.llm import GroqLLMAdapter, LLMMessage, ModelResponse
from app.agent.models import is_valid_inn
from app.agent.runtime import MasterAgentRuntime, inspect_request, is_full_check_request
from app.agent.tools import ToolContext, build_tool_registry
from app.config import Settings
from app.llm.groq_client import GroqClient
from app.pipeline import CompanyNotFound


class FakeLLM:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def chat(self, messages, *, tools, response_schema):
        self.calls += 1
        return ModelResponse(payload=self.payload, model="fake-router")


def _settings(**overrides):
    return Settings(
        llm_mock=True,
        groq_api_key=None,
        database_url="postgresql://localhost/none",
        **overrides,
    )


def _runtime(llm, settings=None):
    settings = settings or _settings()
    client = GroqClient(settings)
    return MasterAgentRuntime(
        llm=llm,
        registry=build_tool_registry(settings),
        tool_context=ToolContext(settings=settings, client=client, persist=False),
        model_timeout_s=1,
        run_timeout_s=3,
    )


@pytest.mark.asyncio
async def test_broad_request_routes_to_single_full_check(monkeypatch, check_payload):
    calls = []

    async def fake_run_check(inn, settings, client, persist):
        calls.append({"inn": inn, "persist": persist})
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    llm = FakeLLM({
        "type": "tool_call",
        "tool": "full_company_check",
        "arguments": {"inn": "6165169320"},
    })

    response = await _runtime(llm).run("Проверь контрагента 6165169320")

    assert calls == [{"inn": "6165169320", "persist": False}]
    assert llm.calls == 1
    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "model"
    assert response.metadata.status == "completed"
    assert [block.type for block in response.blocks] == [
        "company_card", "text", "metric_grid", "line_chart", "finding_list", "evidence_list"
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Проверь контрагента", "missing_inn"),
        ("Проверь контрагента 1234567890", "invalid_inn"),
        ("Какая выручка у контрагента 6165169320?", "unsupported_request"),
        ("Проверь 6165169320 и 0278949271", "ambiguous_inn"),
    ],
)
async def test_invalid_or_out_of_scope_request_never_calls_tool(
    monkeypatch, message, expected
):
    async def forbidden_run_check(*args, **kwargs):
        raise AssertionError("run_check не должен вызываться")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden_run_check)
    llm = FakeLLM({"type": "final", "reason": "unsupported_request"})

    response = await _runtime(llm).run(message)

    assert response.metadata.tool_calls == 0
    assert response.metadata.status == "needs_input"
    assert response.metadata.routing == "deterministic_guard"
    assert llm.calls == 0
    reason, _ = inspect_request(message)
    if expected != "unsupported_request":
        assert reason == expected


@pytest.mark.asyncio
async def test_malformed_model_action_uses_deterministic_fallback(monkeypatch, check_payload):
    calls = 0

    async def fake_run_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    response = await _runtime(FakeLLM({"unexpected": True})).run(
        "Проверь контрагента 6165169320"
    )

    assert calls == 1
    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "deterministic_fallback"


@pytest.mark.asyncio
async def test_unknown_tool_becomes_typed_error_without_run_check(monkeypatch):
    async def forbidden_run_check(*args, **kwargs):
        raise AssertionError("unknown tool не должен запускать run_check")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden_run_check)
    llm = FakeLLM({
        "type": "tool_call",
        "tool": "execute_anything",
        "arguments": {"inn": "6165169320"},
    })
    response = await _runtime(llm).run("Проверь контрагента 6165169320")

    assert response.metadata.status == "error"
    assert response.metadata.error_code == "unknown_tool"
    assert response.metadata.tool_calls == 1


@pytest.mark.asyncio
async def test_model_cannot_replace_explicit_inn(monkeypatch):
    async def forbidden_run_check(*args, **kwargs):
        raise AssertionError("подменённый ИНН не должен попасть в run_check")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden_run_check)
    llm = FakeLLM({
        "type": "tool_call",
        "tool": "full_company_check",
        "arguments": {"inn": "0278949271"},
    })
    response = await _runtime(llm).run("Проверь контрагента 6165169320")

    assert response.metadata.status == "error"
    assert response.metadata.error_code == "invalid_arguments"


@pytest.mark.asyncio
async def test_company_not_found_is_typed_tool_result(monkeypatch):
    async def missing(*args, **kwargs):
        raise CompanyNotFound("6165169320")

    monkeypatch.setattr("app.agent.tools.run_check", missing)
    llm = FakeLLM({
        "type": "tool_call",
        "tool": "full_company_check",
        "arguments": {"inn": "6165169320"},
    })
    response = await _runtime(llm).run("Проверь контрагента 6165169320")

    assert response.metadata.status == "error"
    assert response.metadata.error_code == "not_found"
    assert "не найдена" in response.message


@pytest.mark.asyncio
async def test_tool_timeout_is_typed_and_not_retried(monkeypatch):
    calls = 0

    async def slow(*args, **kwargs):
        nonlocal calls
        calls += 1
        await asyncio.sleep(0.05)

    monkeypatch.setattr("app.agent.tools.run_check", slow)
    settings = _settings(agent_tool_timeout_s=0.01)
    llm = FakeLLM({
        "type": "tool_call",
        "tool": "full_company_check",
        "arguments": {"inn": "6165169320"},
    })
    response = await _runtime(llm, settings=settings).run(
        "Проверь контрагента 6165169320"
    )

    assert calls == 1
    assert response.metadata.error_code == "timeout"
    assert response.metadata.tool_calls == 1


def test_inn_and_intent_checks_are_deterministic():
    assert is_valid_inn("6165169320")
    assert not is_valid_inn("1234567890")
    assert not is_valid_inn("0000000000")
    assert is_full_check_request("Проверь контрагента 6165169320")
    assert not is_full_check_request("Какая выручка у 6165169320?")


def test_registry_exposes_exactly_one_bounded_tool():
    contracts = build_tool_registry(_settings()).visible_contracts()

    assert [item["name"] for item in contracts] == ["full_company_check"]
    assert contracts[0]["risk_class"] == "read_only"
    assert contracts[0]["retry_policy"] == "none"
    assert contracts[0]["input_schema"]["additionalProperties"] is False


@pytest.mark.asyncio
async def test_registry_rejects_extra_arguments_before_executor(monkeypatch):
    async def forbidden_run_check(*args, **kwargs):
        raise AssertionError("run_check не должен получить невалидные аргументы")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden_run_check)
    settings = _settings()
    result = await build_tool_registry(settings).execute(
        "full_company_check",
        {"inn": "6165169320", "unexpected": True},
        ToolContext(settings=settings, client=GroqClient(settings), persist=False),
    )

    assert result.status == "error"
    assert result.error.code == "invalid_arguments"


@pytest.mark.asyncio
async def test_registry_enforces_result_size_limit(monkeypatch, check_payload):
    async def fake_run_check(*args, **kwargs):
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    settings = _settings(agent_tool_result_max_chars=10)
    result = await build_tool_registry(settings).execute(
        "full_company_check",
        {"inn": "6165169320"},
        ToolContext(settings=settings, client=GroqClient(settings), persist=False),
    )

    assert result.status == "error"
    assert result.error.code == "result_too_large"


@pytest.mark.asyncio
async def test_groq_adapter_wraps_current_complete_json_contract():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "model": "openai/gpt-oss-20b",
            "choices": [{"message": {"content": json.dumps({
                "type": "tool_call",
                "tool": "full_company_check",
                "arguments": {"inn": "6165169320"},
            })}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        })

    settings = Settings(
        llm_mock=False,
        groq_api_key="test-key",
        database_url="postgresql://localhost/none",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        adapter = GroqLLMAdapter(GroqClient(settings, client=http_client), settings)
        response = await adapter.chat(
            [
                LLMMessage(role="system", content="system"),
                LLMMessage(role="user", content="Проверь контрагента 6165169320"),
            ],
            tools=[{"name": "full_company_check"}],
            response_schema={"type": "object"},
        )

    sent = json.loads(captured["messages"][1]["content"])
    assert sent["available_tools"] == [{"name": "full_company_check"}]
    assert sent["response_schema"] == {"type": "object"}
    assert response.payload["tool"] == "full_company_check"
    assert response.prompt_tokens == 10
