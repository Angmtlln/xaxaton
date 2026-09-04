"""Observable guarantees LangChain Master Agent первого vertical slice."""
import asyncio
import json

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage
from pydantic import PrivateAttr

from app.agent.models import is_valid_inn
from app.agent.runtime import (MasterAgentRuntime, build_master_model,
                               inspect_request, is_full_check_request)
from app.agent.tools import ToolContext, build_tool_registry
from app.config import Settings
from app.llm.groq_client import GroqClient
from app.pipeline import CompanyNotFound
from app.api.schemas import CheckResponse
from app.agent.tools import _compact_check
from app.agent.synthesis import full_check_findings


class FakeToolCallingModel(FakeMessagesListChatModel):
    _bound_tools = PrivateAttr(default_factory=list)
    _bind_kwargs = PrivateAttr(default_factory=dict)
    _calls = PrivateAttr(default=0)
    _messages = PrivateAttr(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        self._bound_tools = list(tools)
        self._bind_kwargs = dict(kwargs)
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        self._messages.append(list(messages))
        return super()._generate(
            messages, stop=stop, run_manager=run_manager, **kwargs
        )

    @property
    def calls(self):
        return self._calls


class FailingToolCallingModel(FakeToolCallingModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self._calls += 1
        raise RuntimeError("provider unavailable")


def _tool_call(name="full_company_check", args=None, call_id="call-1"):
    return {
        "name": name,
        "args": args or {"inn": "6165169320"},
        "id": call_id,
        "type": "tool_call",
    }


def _model(*responses):
    return FakeToolCallingModel(responses=list(responses))


def _settings(**overrides):
    return Settings(
        llm_mock=True,
        groq_api_key=None,
        database_url="postgresql://localhost/none",
        **overrides,
    )


def _runtime(model, settings=None):
    settings = settings or _settings()
    client = GroqClient(settings)
    return MasterAgentRuntime(
        model=model,
        model_name="fake-router" if model is not None else None,
        registry=build_tool_registry(settings),
        tool_context=ToolContext(settings=settings, client=client, persist=False),
        model_timeout_s=1,
        run_timeout_s=3,
    )


@pytest.mark.asyncio
async def test_broad_request_routes_through_create_agent_to_single_full_check(
    monkeypatch, check_payload
):
    calls = []

    async def fake_run_check(inn, settings, client, persist):
        calls.append({"inn": inn, "persist": persist})
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    model = _model(AIMessage(content="", tool_calls=[_tool_call()]), AIMessage(content='{"finding_ids":[]}'))

    response = await _runtime(model).run("Проверь контрагента 6165169320")

    assert calls == [{"inn": "6165169320", "persist": False}]
    assert model.calls == 2
    assert [tool.name for tool in model._bound_tools] == ["full_company_check"]
    assert model._bind_kwargs["tool_choice"] == "none"
    assert model._bind_kwargs["parallel_tool_calls"] is False
    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "model"
    assert response.metadata.status == "completed"
    assert response.leading_artifact.type == "company_summary"
    assert response.blocks == []
    assert response.metadata.synthesis == "fallback"  # empty model selection is invalid



@pytest.mark.asyncio
async def test_router_model_text_is_never_used_for_rich_response(
    monkeypatch, check_payload
):
    async def fake_run_check(*args, **kwargs):
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    model = _model(AIMessage(
        content="MODEL_TEXT_MUST_NOT_APPEAR 987654321123 <script>alert(1)</script>",
        tool_calls=[_tool_call()],
    ))

    response = await _runtime(model).run("Проверь контрагента 6165169320")
    rendered = response.model_dump_json()

    assert "MODEL_TEXT_MUST_NOT_APPEAR" not in rendered
    assert "987654321123" not in rendered
    assert "<script>" not in rendered


@pytest.mark.asyncio
async def test_full_check_second_step_receives_compact_catalog_and_selects_artifact(monkeypatch, check_payload):
    async def fake_run_check(*args, **kwargs):
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    data, _ = _compact_check(CheckResponse.model_validate(check_payload))
    finding = full_check_findings(data)[-1]
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call()]),
        AIMessage(content=json.dumps({"finding_ids": [finding.id], "artifact": "metrics"})),
    )
    response = await _runtime(model).run("Проверь контрагента 6165169320")
    assert model.calls == 2
    assert response.metadata.synthesis == "model"
    assert response.leading_artifact.type == "company_summary"
    assert [block.type for block in response.blocks] == ["metric_grid"]
    assert response.message.split("\n\n")[1] == finding.text
    context = model._messages[1]
    observation = json.loads(next(message.content for message in context if isinstance(message, ToolMessage)))
    assert finding.id in [item["id"] for item in observation["findings"]]
    assert "company" not in observation
    assert "facts" not in observation
    assert "summary" not in observation
    assert "fin.series" not in observation
    current_schema = json.loads(context[0].content.split("Схема финального ответа для ТЕКУЩЕГО ToolResult: ")[1].split("\n")[0])
    assert finding.id in current_schema["properties"]["finding_ids"]["items"]["enum"]
    assert "metrics" in current_schema["properties"]["artifact"]["enum"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Проверь контрагента", "missing_inn"),
        ("Проверь контрагента 1234567890", "invalid_inn"),
        ("Какие закупки у контрагента 6165169320?", "unsupported_request"),
        ("Проверь 6165169320 и 0278949271", "ambiguous_inn"),
    ],
)
async def test_invalid_or_out_of_scope_request_never_calls_model_or_tool(
    monkeypatch, message, expected
):
    async def forbidden_run_check(*args, **kwargs):
        raise AssertionError("run_check не должен вызываться")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden_run_check)
    model = _model(AIMessage(content="unused"))

    response = await _runtime(model).run(message)

    assert response.metadata.tool_calls == 0
    assert response.metadata.status == "needs_input"
    assert response.metadata.routing == "deterministic_guard"
    assert model.calls == 0
    reason, _ = inspect_request(message)
    if expected != "unsupported_request":
        assert reason == expected


@pytest.mark.asyncio
async def test_unavailable_model_uses_deterministic_fallback_once(
    monkeypatch, check_payload
):
    calls = 0

    async def fake_run_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    response = await _runtime(None).run("Проверь контрагента 6165169320")

    assert calls == 1
    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "deterministic_fallback"


@pytest.mark.asyncio
async def test_provider_error_uses_deterministic_fallback_once(
    monkeypatch, check_payload
):
    calls = 0

    async def fake_run_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    model = FailingToolCallingModel(responses=[AIMessage(content="unused")])

    response = await _runtime(model).run("Проверь контрагента 6165169320")

    assert model.calls == 1
    assert calls == 1
    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "deterministic_fallback"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "ai_message",
    [
        AIMessage(content="Не буду вызывать tool"),
        AIMessage(content="", tool_calls=[_tool_call("execute_anything")]),
        AIMessage(
            content="",
            tool_calls=[
                _tool_call(call_id="call-1"),
                _tool_call(call_id="call-2"),
            ],
        ),
        AIMessage(
            content="",
            tool_calls=[_tool_call(args={"inn": "6165169320", "unexpected": True})],
        ),
    ],
)
async def test_incorrect_native_tool_call_falls_back_to_one_allowlisted_execution(
    monkeypatch, check_payload, ai_message
):
    calls = []

    async def fake_run_check(inn, *args, **kwargs):
        calls.append(inn)
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    model = _model(ai_message)

    response = await _runtime(model).run("Проверь контрагента 6165169320")

    assert calls == ["6165169320"]
    assert model.calls == 1
    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "deterministic_fallback"
    assert response.metadata.status == "completed"


@pytest.mark.asyncio
async def test_model_cannot_replace_explicit_inn(monkeypatch, check_payload):
    calls = []

    async def fake_run_check(inn, *args, **kwargs):
        calls.append(inn)
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    model = _model(AIMessage(
        content="",
        tool_calls=[_tool_call(args={"inn": "0278949271"})],
    ))

    response = await _runtime(model).run("Проверь контрагента 6165169320")

    assert calls == ["6165169320"]
    assert response.metadata.routing == "deterministic_fallback"
    assert response.metadata.tool_calls == 1


@pytest.mark.asyncio
async def test_company_not_found_is_typed_and_not_retried(monkeypatch):
    calls = 0

    async def missing(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise CompanyNotFound("6165169320")

    monkeypatch.setattr("app.agent.tools.run_check", missing)
    model = _model(AIMessage(content="", tool_calls=[_tool_call()]))
    response = await _runtime(model).run("Проверь контрагента 6165169320")

    assert calls == 1
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
    model = _model(AIMessage(content="", tool_calls=[_tool_call()]))
    response = await _runtime(model, settings=settings).run(
        "Проверь контрагента 6165169320"
    )

    assert calls == 1
    assert response.metadata.error_code == "timeout"
    assert response.metadata.tool_calls == 1


@pytest.mark.asyncio
async def test_model_timeout_falls_back_before_tool_execution(monkeypatch, check_payload):
    calls = 0

    async def fake_run_check(*args, **kwargs):
        nonlocal calls
        calls += 1
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    model = _model(AIMessage(content="", tool_calls=[_tool_call()]))
    model.sleep = 0.05
    runtime = _runtime(model)
    runtime.model_timeout_s = 0.01

    response = await runtime.run("Проверь контрагента 6165169320")

    assert calls == 1
    assert response.metadata.routing == "deterministic_fallback"


def test_inn_and_intent_checks_are_deterministic():
    assert is_valid_inn("6165169320")
    assert not is_valid_inn("1234567890")
    assert not is_valid_inn("0000000000")
    assert is_full_check_request("Проверь контрагента 6165169320")
    assert not is_full_check_request("Какая выручка у 6165169320?")


def test_registry_exposes_three_bounded_tools():
    contracts = build_tool_registry(_settings()).visible_contracts()

    assert [item["name"] for item in contracts] == [
        "full_company_check", "get_financial_data", "get_legal_data"
    ]
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


def test_chat_groq_factory_preserves_master_settings_without_network_call():
    settings = Settings(
        llm_mock=False,
        master_provider="groq",
        master_model="openai/gpt-oss-20b",
        groq_api_key="test-key",
        groq_base_url="https://api.groq.com/openai/v1",
        groq_reasoning_effort="low",
        agent_router_max_tokens=321,
        agent_model_timeout_s=7,
        database_url="postgresql://localhost/none",
    )

    model = build_master_model(settings)

    assert model.model_name == "openai/gpt-oss-20b"
    assert str(model.groq_api_base) == "https://api.groq.com"
    assert model.max_tokens == 321
    assert model.request_timeout == 7
    assert model.max_retries == 0
    assert model.reasoning_format == "hidden"
    assert model.reasoning_effort == "low"
    assert model.model_kwargs["parallel_tool_calls"] is False
