"""Behavioral acceptance for trusted context and bounded grounding repair."""
import json

import pytest
from langchain_core.messages import AIMessage

from test_agent_multiturn import targeted_result
from test_agent_runtime import _model, _runtime, _tool_call


SUPPORTED = AIMessage(content='{"supported":true,"unsupported_claims":[]}')
UNSUPPORTED = AIMessage(content=json.dumps({
    "supported": False,
    "unsupported_claims": ["В verified context нет сведений о 99 филиалах"],
}, ensure_ascii=False))


def answer(message, artifact="none"):
    return AIMessage(content=json.dumps({"message": message, "artifact": artifact}, ensure_ascii=False))


def install_finance(runtime, monkeypatch, calls):
    async def execute(name, args, context):
        calls.append((name, args))
        return targeted_result("finance")

    monkeypatch.setattr(runtime.registry, "execute", execute)


@pytest.mark.asyncio
async def test_seven_turn_acceptance_keeps_one_conversation_and_three_tools(
    monkeypatch, check_payload
):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call()]),
        answer("В карточке есть официальный стоп-сигнал; начнём с его проверки."),
        SUPPORTED,
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("Прибыль нужно сопоставлять с динамикой и обязательствами."),
        SUPPORTED,
        answer("Потому что один удачный период не гарантирует будущий платёж."),
        SUPPORTED,
        answer("Проще: плюс сейчас ещё не означает, что денег хватит потом."),
        answer("Для отсрочки это существенно: риск проявится к дате платежа."),
        SUPPORTED,
        AIMessage(content="", tool_calls=[_tool_call("get_legal_data")]),
        answer("Есть судебные дела; их роль, суммы и актуальный статус нужно разобрать."),
        SUPPORTED,
        answer("Самое неприятное — официальный стоп-сигнал и незакрытая неопределённость по спорам."),
        SUPPORTED,
    )
    runtime = _runtime(model)
    original = runtime.registry.execute
    calls = []

    async def fake_run_check(*args, **kwargs):
        return check_payload

    async def execute(name, arguments, context):
        calls.append((name, arguments))
        if name == "full_company_check":
            return await original(name, arguments, context)
        return targeted_result("finance" if name == "get_financial_data" else "legal")

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    monkeypatch.setattr(runtime.registry, "execute", execute)
    questions = [
        "Проверь контрагента 6165169320",
        "А что у них с финансами?",
        "Почему это вообще плохо?",
        "Объясни проще",
        "Насколько это критично для сделки с отсрочкой?",
        "А что с судами?",
        "Что здесь самое неприятное?",
    ]
    responses = []
    conversation_id = None
    for question in questions:
        response = await runtime.run(question, conversation_id)
        conversation_id = response.conversation_id
        responses.append(response)

    assert len({item.conversation_id for item in responses}) == 1
    assert [item.metadata.tool_calls for item in responses] == [1, 1, 0, 0, 0, 1, 0]
    assert [item.metadata.grounding_status for item in responses] == [
        "verified", "verified", "verified", "skipped_rewrite", "verified",
        "verified", "verified",
    ]
    assert calls == [
        (name, {"inn": "6165169320"})
        for name in ("full_company_check", "get_financial_data", "get_legal_data")
    ]
    assert model.calls == 16


@pytest.mark.asyncio
async def test_natural_company_specific_reasoning_is_allowed(monkeypatch):
    message = (
        "Прибыль сама по себе положительная, но её стоит сопоставить с динамикой "
        "выручки: для отсрочки важна способность платить в будущем, а не одна цифра."
    )
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer(message), SUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    response = await runtime.run("Что у компании с финансами? ИНН 6165169320")

    assert response.message == message
    assert response.metadata.grounding_status == "verified"
    assert response.metadata.model_calls == 3
    assert calls == [("get_financial_data", {"inn": "6165169320"})]


@pytest.mark.asyncio
async def test_followup_reasoning_uses_trusted_context_without_domain_tool(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("Прибыль подтверждена, но одной цифры недостаточно."), SUPPORTED,
        answer("Потому что платёжеспособность зависит от устойчивости денежных потоков, а не от одного периода."),
        SUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    first = await runtime.run("Финансы 6165169320")
    second = await runtime.run("Почему это вообще плохо?", first.conversation_id)

    assert len(calls) == 1
    assert second.metadata.tool_calls == 0
    assert second.metadata.model_calls == 2
    assert second.metadata.grounding_status == "verified"
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(
        {"configurable": {"thread_id": first.conversation_id}}
    )
    values = checkpoint.checkpoint["channel_values"]
    assert values["last_topic"] == "finance"
    assert values["trusted_context"]["domains"]["finance"]["metrics"][0]["value"] == 500
    assert "Потому что" not in json.dumps(values["trusted_context"], ensure_ascii=False)


@pytest.mark.asyncio
async def test_previous_assistant_hallucination_never_becomes_trusted_fact(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("У компании 99 филиалов, а прибыль подтверждена."), SUPPORTED,
        answer("Это важно ещё и потому, что у компании 99 филиалов."), UNSUPPORTED,
        answer("Это важно, потому что устойчивость прибыли нужно оценивать по нескольким периодам."),
        SUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    first = await runtime.run("Финансы 6165169320")
    second = await runtime.run("Почему это важно?", first.conversation_id)

    assert "99 филиалов" in first.message
    assert "99 филиалов" not in second.message
    assert second.metadata.grounding_status == "repaired"
    assert second.metadata.repair_attempts == 1
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(
        {"configurable": {"thread_id": first.conversation_id}}
    )
    trusted = checkpoint.checkpoint["channel_values"]["trusted_context"]
    assert "99 филиалов" not in json.dumps(trusted, ensure_ascii=False)


@pytest.mark.asyncio
async def test_unknown_url_and_company_identifier_are_repaired_before_response(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("ИНН 0278949271 подтверждает детали на https://invented.test"),
        answer("По проверенным данным прибыль есть, но её устойчивость требует контекста."),
        SUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    response = await runtime.run("Финансы 6165169320")

    assert "invented.test" not in response.model_dump_json()
    assert "0278949271" not in response.model_dump_json()
    assert response.active_company.inn == "6165169320"
    assert response.metadata.grounding_status == "repaired"
    assert all(item.fact_id in {"fin.profit_last"} for item in response.evidence)


@pytest.mark.asyncio
async def test_fabricated_fact_is_repaired_once(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("У компании 99 филиалов и стабильная прибыль."), UNSUPPORTED,
        answer("По проверенным данным видна прибыль; устойчивость по одному значению не доказана."),
        SUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    response = await runtime.run("Финансы 6165169320")

    assert "99 филиалов" not in response.message
    assert response.metadata.grounding_status == "repaired"
    assert response.metadata.repair_attempts == 1
    assert response.metadata.model_calls == 5


@pytest.mark.asyncio
async def test_failed_repair_uses_conservative_deterministic_fallback(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("У компании 99 филиалов."), UNSUPPORTED,
        answer("У компании по-прежнему 99 филиалов."), UNSUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    response = await runtime.run("Финансы 6165169320")

    assert "99 филиалов" not in response.message
    assert "Подтверждённые данные" in response.message
    assert response.metadata.grounding_status == "fallback"
    assert response.metadata.repair_attempts == 1
    assert response.metadata.routing == "deterministic_fallback"


@pytest.mark.asyncio
async def test_contextual_rewrite_skips_tool_and_expensive_verifier(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("Одна прибыль не доказывает устойчивость будущего платежа."), SUPPORTED,
        answer("Проще: сейчас плюс есть, но важно понять, повторится ли он."),
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    first = await runtime.run("Финансы 6165169320")
    second = await runtime.run("Объясни проще", first.conversation_id)

    assert len(calls) == 1
    assert second.metadata.tool_calls == 0
    assert second.metadata.model_calls == 1
    assert second.metadata.grounding_status == "skipped_rewrite"
    assert model.calls == 4


@pytest.mark.asyncio
async def test_rewrite_with_new_fact_request_still_runs_grounding(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        answer("Одна прибыль не доказывает устойчивость будущего платежа."), SUPPORTED,
        answer("Проще: у компании 99 филиалов."), UNSUPPORTED,
        answer("Проще: одной цифры прибыли недостаточно для вывода об устойчивости."),
        SUPPORTED,
    )
    runtime = _runtime(model)
    calls = []
    install_finance(runtime, monkeypatch, calls)

    first = await runtime.run("Финансы 6165169320")
    second = await runtime.run(
        "Объясни проще и добавь, сколько у них филиалов", first.conversation_id
    )

    assert len(calls) == 1
    assert "99 филиалов" not in second.message
    assert second.metadata.tool_calls == 0
    assert second.metadata.model_calls == 4
    assert second.metadata.grounding_status == "repaired"
