"""Provider selection and per-conversation Master model stability."""
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI

from app.agent.conversations import ConversationStore
from app.agent import master_model as master_model_module
from app.agent.master_model import FailoverChatModel, build_master_model
from app.agent.runtime import build_master_runtime
from app.config import Settings
from app.llm.groq_client import GroqClient
from test_agent_multiturn import targeted_result
from test_agent_runtime import _model, _runtime, _tool_call


def _settings(**overrides):
    return Settings(_env_file=None, database_url="postgresql://localhost/none", **overrides)


class _RateLimited(Exception):
    """Как отдаёт лимит SDK провайдера: код 429 на объекте исключения."""

    status_code = 429


class _RaisingModel(BaseChatModel):
    """Модель, которая всегда падает заданной ошибкой; сеть не трогается."""

    error: Exception

    @property
    def _llm_type(self):
        return "raising-stub"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise self.error


class _BusyThenReadyModel(BaseChatModel):
    """Отдаёт лимит, пока не выдержана названная провайдером пауза."""

    error: Exception
    busy_calls: int

    @property
    def _llm_type(self):
        return "busy-then-ready-stub"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if self.busy_calls > 0:
            self.busy_calls -= 1
            raise self.error
        return ChatResult(
            generations=[ChatGeneration(message=AIMessage(content="ответ после паузы"))]
        )


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

    assert isinstance(model, FailoverChatModel)
    primary = model.runnables[0]
    assert isinstance(primary, ChatGroq)
    assert primary.model_name == "openai/gpt-oss-20b"
    assert primary.reasoning_format == "hidden"
    assert primary.reasoning_effort == "low"


def test_groq_master_gets_ordered_spare_models_without_duplicates():
    settings = _settings(
        master_provider="groq",
        master_model="openai/gpt-oss-20b",
        groq_api_key="test-key",
        groq_master_fallback_models="openai/gpt-oss-120b, openai/gpt-oss-20b ,qwen/qwen3.8-27b",
    )

    assert settings.master_model_chain() == [
        "openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b",
    ]
    assert build_master_model(settings).model_names == settings.master_model_chain()


def test_groq_spare_models_do_not_leak_into_another_provider():
    settings = _settings(
        master_provider="polza",
        master_model="z-ai/glm-5.3-flash",
        polza_api_key="test-key",
        groq_master_fallback_models="openai/gpt-oss-120b",
    )

    assert settings.master_model_chain() == ["z-ai/glm-5.3-flash"]
    assert isinstance(build_master_model(settings), ChatOpenAI)


@pytest.mark.asyncio
async def test_rate_limited_master_model_answers_from_the_next_model():
    exhausted = _RaisingModel(error=_RateLimited("rate limit reached"))
    spare = _model(AIMessage(content="ответ запасной модели"))
    model = FailoverChatModel(
        runnables=[exhausted, spare], model_names=["primary", "spare"]
    )

    answer = await model.ainvoke("вопрос")

    assert answer.content == "ответ запасной модели"


@pytest.mark.asyncio
async def test_master_does_not_retry_another_model_on_a_request_error():
    broken = _RaisingModel(error=ValueError("bad request"))
    spare = _model(AIMessage(content="не должно быть вызвано"))
    model = FailoverChatModel(
        runnables=[broken, spare], model_names=["primary", "spare"]
    )

    with pytest.raises(ValueError):
        await model.ainvoke("вопрос")


def test_openrouter_is_the_default_master_provider_with_glm():
    settings = _settings(
        openrouter_api_key="test-key",
        openrouter_base_url="https://openrouter.ai/api/v1/",
        openrouter_app_url="https://example.test",
        agent_router_max_tokens=321,
        agent_model_timeout_s=7,
    )

    assert settings.master_provider == "openrouter"
    assert settings.master_model_name() == "z-ai/glm-5.3-flash"
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


def test_openrouter_attribution_is_optional():
    model = build_master_model(_settings(openrouter_api_key="test-key"))

    assert "HTTP-Referer" not in model.default_headers


def test_non_latin1_attribution_never_breaks_the_call():
    """Кириллица в HTTP-заголовке роняла каждый вызов Master до сети."""
    model = build_master_model(_settings(
        openrouter_api_key="test-key", openrouter_app_title="Контрагент-агент",
    ))

    assert "X-Title" not in model.default_headers
    for name, value in model.default_headers.items():
        value.encode("latin-1")


def test_reasoning_effort_keeps_structured_answers_alive():
    """GLM тратит выход на скрытые рассуждения и без низкого effort молчит."""
    quiet = build_master_model(_settings(openrouter_api_key="k"))
    loud = build_master_model(_settings(openrouter_api_key="k", openrouter_reasoning_effort=""))

    assert quiet.extra_body == {"reasoning": {"effort": "low"}}
    assert loud.extra_body is None


def test_token_budgets_follow_the_provider_limits():
    groq = _settings(master_provider="groq", groq_api_key="k")
    openrouter = _settings(openrouter_api_key="k")

    # У Groq лимит выходных токенов в минуту, у OpenRouter его нет.
    assert (groq.answer_max_tokens(), groq.verifier_max_tokens()) == (600, 200)
    assert openrouter.answer_max_tokens() > groq.answer_max_tokens()
    assert openrouter.verifier_max_tokens() > groq.verifier_max_tokens()


def test_every_provider_reads_only_its_own_key():
    keys = {"openrouter": "openrouter_api_key", "polza": "polza_api_key", "groq": "groq_api_key"}
    for provider, field in keys.items():
        settings = _settings(master_provider=provider, **{field: "test-key"})
        assert settings.master_api_key() == "test-key"
        assert build_master_model(settings) is not None
        for other in keys:
            if other != provider:
                assert build_master_model(_settings(master_provider=other, **{field: "test-key"})) is None


def test_selected_provider_requires_its_own_key_and_mock_disables_master():
    assert build_master_model(_settings(master_provider="openrouter", openrouter_api_key=None)) is None
    assert build_master_model(_settings(master_provider="polza", polza_api_key=None)) is None
    assert build_master_model(_settings(master_provider="groq", groq_api_key=None)) is None
    assert build_master_model(_settings(llm_mock=True, openrouter_api_key="test-key")) is None


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


@pytest.mark.asyncio
async def test_master_waits_the_pause_named_by_the_provider_before_giving_up(monkeypatch):
    waited = []

    async def record(seconds):
        waited.append(seconds)

    monkeypatch.setattr(master_model_module.asyncio, "sleep", record)
    busy = _BusyThenReadyModel(
        error=_RateLimited("rate limit reached. Please try again in 2.5s"), busy_calls=1
    )
    model = FailoverChatModel(runnables=[busy], model_names=["primary"])

    answer = await model.ainvoke("вопрос")

    assert answer.content == "ответ после паузы"
    assert waited == [2.75]


@pytest.mark.asyncio
async def test_master_gives_up_when_the_provider_names_no_usable_pause():
    exhausted = _RaisingModel(error=_RateLimited("rate limit reached"))
    model = FailoverChatModel(runnables=[exhausted], model_names=["primary"])

    with pytest.raises(_RateLimited):
        await model.ainvoke("вопрос")
