"""Free entry, contextual next questions and bounded suggestion contracts."""
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import ValidationError

from app.agent.models import MasterAnswer, SuggestedAction
from app.agent.suggestions import next_actions
from test_agent_runtime import _model, _runtime, _tool_call


def answer(message, actions=None):
    return AIMessage(content=json.dumps({"message": message, "artifact": "none",
        "suggested_actions": actions or []}, ensure_ascii=False))


@pytest.mark.asyncio
async def test_free_opening_and_clarification_keep_history_without_tools(monkeypatch):
    model = _model(answer("Помогу проверить компанию. Какую задачу решаете?"),
                   answer("Для проверки этой компании нужен ИНН."))
    runtime = _runtime(model)
    async def forbidden(*args, **kwargs):
        raise AssertionError("No data access before company selection")
    monkeypatch.setattr(runtime.registry, "execute", forbidden)
    first = await runtime.run("Здравствуйте! Мне нужна помощь с поставщиком")
    second = await runtime.run("Хочу проверить ООО Ромашка", first.conversation_id)
    assert first.metadata.routing == second.metadata.routing == "model"
    assert first.metadata.tool_calls == second.metadata.tool_calls == 0
    assert first.metadata.model_calls == second.metadata.model_calls == 1
    assert second.active_company is None and second.evidence == []
    assert second.leading_artifact is None and second.blocks == []
    assert second.conversation_id == first.conversation_id
    assert any(isinstance(m, HumanMessage) and "поставщиком" in m.content for m in model._messages[1])
    assert all(binding["tools"] == [] for binding in model._tool_bindings)


@pytest.mark.asyncio
async def test_opening_then_full_check_then_contextual_buttons(monkeypatch, check_payload):
    model = _model(
        answer("Для проверки пришлите ИНН."),
        AIMessage(content="", tool_calls=[_tool_call()]),
        answer("## Основное\n**Проверка завершена.**", [
            {"label": "Что уточнить?", "prompt": "Что уточнить у контрагента?", "mode": "submit"}]),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
        answer("Уточните условия исполнения обязательств.", [
            {"label": "Объяснить проще", "prompt": "Объясни проще", "mode": "submit"}]),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
    )
    runtime = _runtime(model)
    calls = []
    async def check(inn, *args, **kwargs):
        calls.append(inn)
        return check_payload
    monkeypatch.setattr("app.agent.tools.run_check", check)
    first = await runtime.run("Проверь контрагента")
    full = await runtime.run("Проверь контрагента 6165169320", first.conversation_id)
    follow = await runtime.run(full.suggested_actions[0].prompt, first.conversation_id)
    assert calls == ["6165169320"]
    assert full.leading_artifact is not None and full.message.startswith("##")
    assert follow.leading_artifact is None and follow.metadata.tool_calls == 0
    assert full.suggested_actions[0].label != follow.suggested_actions[0].label
    assert follow.active_company.inn == "6165169320"


@pytest.mark.asyncio
async def test_free_comparison_request_and_provider_failure_do_not_run_tools(monkeypatch):
    runtime = _runtime(None)
    async def forbidden(*args, **kwargs):
        raise AssertionError("No tools")
    monkeypatch.setattr(runtime.registry, "execute", forbidden)
    response = await runtime.run("Сравни контрагентов:")
    assert response.metadata.tool_calls == response.metadata.model_calls == 0
    assert response.metadata.synthesis == "fallback"
    assert response.suggested_actions[1].mode == "compose"
    invalid = await runtime.run("Сравни ИНН 1234567890")
    assert invalid.metadata.routing == "deterministic_guard"


def test_suggestions_preserve_model_choice_filter_untrusted_values_and_deduplicate():
    good = {"label": "Объяснить проще", "prompt": "Объясни проще"}
    proposal = MasterAnswer(message="Ответ", suggested_actions=[good, good,
        {"label": "Сравнить", "prompt": "Сравни 6165169320 и 1234567890"},
        {"label": "Сайт", "prompt": "Открой https://example.com"}])
    actions = next_actions(proposal, {"company": {"inn": "6165169320"}})
    assert len(actions) == 1 and actions[0].label == "Объяснить проще"
    assert next_actions(MasterAnswer(message="Готово", suggested_actions=[]), {}) == []


def test_old_model_answer_gets_domain_defaults_and_compare_draft():
    context = {"domain": "full_check", "company": {"inn": "6165169320"}}
    full = next_actions(MasterAnswer(message="Готово"), context)
    follow = next_actions(None, context, contextual=True)
    assert full != follow
    assert full[0].mode == "compose" and full[0].prompt == "Сравни контрагентов: 6165169320 и "


@pytest.mark.parametrize("values", [
    {"label": "<img src=x onerror=alert(1)>", "prompt": "Вопрос"},
    {"label": "Кнопка", "prompt": "Вопрос", "mode": "execute"},
    {"label": "Кнопка", "prompt": "x" * 301},
])
def test_suggestion_contract_rejects_markup_unbounded_text_and_execution(values):
    with pytest.raises(ValidationError):
        SuggestedAction(**values)

@pytest.mark.asyncio
@pytest.mark.parametrize(("opening", "reply", "tool"), [
    ("Проверь контрагента", "6165169320", "full_company_check"),
    ("Разбери финансы компании", "ИНН 6165169320", "get_financial_data"),
    ("Сравни контрагентов", "6165169320 и 0278949271", "compare_companies"),
])
async def test_identifier_reply_continues_original_user_intent(monkeypatch, opening, reply, tool):
    runtime = _runtime(None)
    calls = []
    from app.agent.models import ToolResult, ToolError, ToolResultMetadata
    async def execute(name, args, context):
        calls.append((name, args))
        return ToolResult(status="error", error=ToolError(code="not_found", user_safe_message="Нет снимка"),
                          metadata=ToolResultMetadata(tool=name, latency_ms=0))
    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run(opening)
    second = await runtime.run(reply, first.conversation_id)
    assert len(calls) == 1 and calls[0][0] == tool
    assert second.metadata.tool_calls == 1


def test_comparison_suggestion_hydrates_current_inn_when_master_uses_name():
    proposal = MasterAnswer(message="Ответ", suggested_actions=[
        {"label": "Сравнить", "prompt": "Сравни ООО ГДК с другим контрагентом, ИНН: ", "mode": "submit"}])
    action = next_actions(proposal, {"company": {"inn": "6165169320"}})[0]
    assert action.mode == "compose"
    from app.agent.runtime import inspect_comparison_request
    reason, inns = inspect_comparison_request(action.prompt + "0278949271")
    assert reason is None and inns == ["6165169320", "0278949271"]
