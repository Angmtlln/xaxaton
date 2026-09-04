"""Provider selection and per-conversation Master model stability."""
import pytest
from langchain_core.messages import AIMessage
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.agent.conversations import ConversationStore
from app.agent.master_model import build_master_model
from app.agent.runtime import build_master_runtime
from app.config import Settings
from app.llm.groq_client import GroqClient
from test_agent_multiturn import targeted_result
from test_agent_runtime import _model, _runtime, _tool_call


def _settings(**overrides):
    return Settings(database_url="postgresql://localhost/none", **overrides)


def test_polza_factory_uses_standard_openai_compatible_adapter_without_network():
    settings = _settings(
        master_provider="polza",
        master_model="z-ai/glm-5.3-flash",
        polza_api_key="test-key",
        polza_base_url="https://polza.ai/api/v1/",
        agent_router_max_tokens=321,
        agent_model_timeout_s=7,
    )

    model = build_master_model(settings)

    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "z-ai/glm-5.3-flash"
    assert str(model.openai_api_base).rstrip("/") == "https://polza.ai/api/v1"
    assert model.max_tokens == 321
    assert model.request_timeout == 7
    assert model.max_retries == 0
    assert model.model_kwargs["parallel_tool_calls"] is False


def test_groq_remains_a_master_configuration_alternative():
    settings = _settings(
        master_provider="groq",
        master_model="openai/gpt-oss-20b",
        groq_api_key="test-key",
        groq_reasoning_effort="low",
    )

    model = build_master_model(settings)

    assert isinstance(model, ChatGroq)
    assert model.model_name == "openai/gpt-oss-20b"
    assert model.reasoning_format == "hidden"
    assert model.reasoning_effort == "low"


def test_selected_provider_requires_its_own_key_and_mock_disables_master():
    assert build_master_model(_settings(master_provider="polza", polza_api_key=None)) is None
    assert build_master_model(_settings(master_provider="groq", groq_api_key=None)) is None
    assert build_master_model(_settings(llm_mock=True, polza_api_key="test-key")) is None


def test_documented_master_environment_names(monkeypatch):
    monkeypatch.setenv("MASTER_PROVIDER", "polza")
    monkeypatch.setenv("MASTER_MODEL", "z-ai/glm-5.3-flash")
    monkeypatch.setenv("POLZA_API_KEY", "test-key")
    monkeypatch.setenv("POLZA_BASE_URL", "https://polza.ai/api/v1")

    settings = Settings(_env_file=None)

    assert settings.master_provider == "polza"
    assert settings.master_model_name() == "z-ai/glm-5.3-flash"
    assert settings.polza_api_key == "test-key"
    assert settings.polza_base_url == "https://polza.ai/api/v1"


def test_polza_master_is_not_gated_by_domain_groq_client():
    settings = _settings(
        master_provider="polza",
        master_model="z-ai/glm-5.3-flash",
        polza_api_key="test-key",
        groq_api_key=None,
    )

    runtime = build_master_runtime(settings, GroqClient(settings), persist=False)

    assert isinstance(runtime.model, ChatOpenAI)
    assert runtime.model_name == "z-ai/glm-5.3-flash"
    assert runtime.model_provider == "polza"


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
    first_runtime.model_provider = "polza"
    replacement_runtime = _runtime(replacement_model)
    replacement_runtime.conversation_store = store
    replacement_runtime.model_name = "openai/gpt-oss-20b"
    replacement_runtime.model_provider = "groq"

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
