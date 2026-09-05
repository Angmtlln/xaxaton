"""OpenRouter configuration and per-conversation Master model stability."""
import pytest
from langchain_core.messages import AIMessage
from langchain_openai import ChatOpenAI

from app.agent.conversations import ConversationStore
from app.agent.master_model import build_master_model
from app.agent.runtime import build_master_runtime
from app.config import Settings
from app.llm.groq_client import GroqClient
from test_agent_multiturn import targeted_result
from test_agent_runtime import _model, _runtime, _tool_call


def _settings(**overrides):
    return Settings(
        _env_file=None,
        database_url="postgresql://localhost/none",
        **overrides,
    )


def test_openrouter_is_the_only_default_master_profile():
    settings = _settings()

    assert settings.master_model == "z-ai/glm-5.3-flash"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"
    assert settings.openrouter_reasoning_effort == "low"
    assert settings.agent_router_max_tokens == 512
    assert settings.answer_max_tokens() == 4096
    assert settings.verifier_max_tokens() == 2048
    assert settings.repair_max_tokens() == 4096
    assert not hasattr(settings, "master_provider")


def test_openrouter_factory_uses_standard_openai_compatible_adapter_without_network():
    settings = _settings(
        master_model="z-ai/glm-5.3-flash",
        openrouter_api_key="test-key",
        openrouter_base_url="https://openrouter.ai/api/v1/",
        openrouter_app_url="https://example.test",
        agent_router_max_tokens=321,
        agent_model_timeout_s=7,
    )

    model = build_master_model(settings)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "z-ai/glm-5.3-flash"
    assert str(model.openai_api_base).rstrip("/") == "https://openrouter.ai/api/v1"
    assert model.max_tokens == 321
    assert model.request_timeout == 7
    assert model.max_retries == 0
    assert model.model_kwargs["parallel_tool_calls"] is False
    assert model.default_headers["HTTP-Referer"] == "https://example.test"
    assert model.default_headers["X-Title"] == "Counterparty Agent"
    assert model.extra_body == {"reasoning": {"effort": "low"}}


def test_openrouter_key_is_required_and_mock_disables_master():
    assert build_master_model(_settings(openrouter_api_key=None)) is None
    assert build_master_model(_settings(llm_mock=True, openrouter_api_key="test-key")) is None


def test_openrouter_attribution_is_optional_and_latin1_safe():
    plain = build_master_model(
        _settings(openrouter_api_key="test-key", openrouter_app_title="")
    )
    non_latin = build_master_model(
        _settings(openrouter_api_key="test-key", openrouter_app_title="Контрагент")
    )

    assert plain.default_headers == {}
    assert "X-Title" not in non_latin.default_headers
    for value in non_latin.default_headers.values():
        value.encode("latin-1")


def test_reasoning_effort_can_be_disabled():
    model = build_master_model(
        _settings(openrouter_api_key="test-key", openrouter_reasoning_effort="")
    )

    assert model.extra_body is None


def test_documented_master_environment_names(monkeypatch):
    monkeypatch.setenv("MASTER_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

    settings = Settings(_env_file=None)

    assert settings.master_model == "z-ai/glm-5.3-flash"
    assert settings.openrouter_api_key == "test-key"
    assert settings.openrouter_base_url == "https://openrouter.ai/api/v1"


def test_openrouter_master_is_not_gated_by_domain_groq_client():
    settings = _settings(
        master_model="z-ai/glm-5.3-flash",
        openrouter_api_key="test-key",
        groq_api_key=None,
    )

    runtime = build_master_runtime(settings, GroqClient(settings), persist=False)

    assert isinstance(runtime.model, ChatOpenAI)
    assert runtime.model_name == "z-ai/glm-5.3-flash"
    assert runtime.model_provider == "openrouter"
    assert runtime.answer_max_tokens == 4096
    assert runtime.verifier_max_tokens == 2048
    assert runtime.repair_max_tokens == 4096


@pytest.mark.asyncio
async def test_conversation_keeps_first_master_model_and_provider(monkeypatch, check_payload):
    first_model = _model(
        AIMessage(content="", tool_calls=[_tool_call()]),
        AIMessage(content='{"finding_ids":[]}'),
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        AIMessage(content='{"finding_ids":["observation"]}'),
    )
    replacement_model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        AIMessage(content='{"finding_ids":["observation"]}'),
    )
    store = ConversationStore()
    first_runtime = _runtime(first_model)
    first_runtime.conversation_store = store
    first_runtime.model_name = "z-ai/glm-5.3-flash"
    first_runtime.model_provider = "openrouter"
    replacement_runtime = _runtime(replacement_model)
    replacement_runtime.conversation_store = store
    replacement_runtime.model_name = "replacement-model"
    replacement_runtime.model_provider = "custom"

    async def fake_run_check(*args, **kwargs):
        return check_payload

    async def targeted_execute(name, arguments, context):
        assert name == "get_financial_data"
        return targeted_result()

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    monkeypatch.setattr(replacement_runtime.registry, "execute", targeted_execute)

    first = await first_runtime.run("Проверь контрагента 6165169320")
    followup = await replacement_runtime.run(
        "А что у них с финансами?", first.conversation_id
    )

    assert followup.conversation_id == first.conversation_id
    assert followup.metadata.model == "z-ai/glm-5.3-flash"
    assert first_model.calls == 4
    assert replacement_model.calls == 0
