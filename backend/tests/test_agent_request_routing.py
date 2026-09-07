"""Observable admission, semantic tool choice and trusted-company boundaries."""
import pytest
from langchain_core.messages import AIMessage

from app.agent.runtime import (inspect_comparison_request, inspect_request,
                               is_direct_request, requested_tool)
from test_agent_runtime import (_answer, _model, _runtime, _tool_call,
                                _verified_context, FailingToolCallingModel)
from test_agent_multiturn import targeted_result


@pytest.mark.parametrize("message", [
    "Сделка на 10000000 рублей",
    "Договор № 123456789012345, сумма 10 млн рублей",
    "Сумма 6165169320",  # Even a checksum-valid amount is not a company switch.
    "Аванс 6165169320 ₽",
    "Платёж 6165169320,50 рублей",
    "2023",
])
def test_amounts_and_ordinary_long_numbers_do_not_fail_inn_admission(message):
    assert inspect_request(message) == ("missing_inn", None)


@pytest.mark.parametrize("message", [
    "Проверь контрагента 1234567890",
    "ИНН 123",
    "1234567890",
    "Финансы 6165169320 и 1234567890",
])
def test_explicit_bad_identifiers_still_need_correction(message):
    assert inspect_request(message) == ("invalid_inn", None)


def test_amounts_do_not_break_identified_checks_or_comparisons():
    assert inspect_request("ИНН 6165169320, сделка на 10000000 рублей") == (None, "6165169320")
    assert inspect_comparison_request(
        "Сравни 6165169320 и 0278949271 для сделки на 10000000 рублей"
    ) == (None, ["6165169320", "0278949271"])
    assert inspect_request("Сумма 10000000 рублей, ИНН 123") == ("invalid_inn", None)


@pytest.mark.parametrize("question", [
    "Почему до регистрации у 1684017097 нулевая выручка?",
    "Чем отличаются выручка и прибыль в данных 6165169320?",
    "Объясни по 6165169320 факт, интерпретацию и гипотезу на примере роста выручки.",
    "У 5029069967 есть подписанные госконтракты и лицензии: можно ли считать доказанными опыт и успешное исполнение?",
])
def test_explicit_single_domain_question_is_a_bounded_direct_read(question):
    target = requested_tool(question)
    assert target in {"get_financial_data", "get_legal_data"}
    assert is_direct_request(question, target)


def test_combined_licenses_and_procurements_use_profile_projection():
    from app.agent.runtime import detail_arguments
    question = "У 5029069967 есть подписанные госконтракты и лицензии?"
    assert detail_arguments(question) == {"section": "profile"}


def test_explicit_domain_negation_stays_with_semantic_router():
    question = "Не нужно проверять финансы 6165169320, объясни термин"
    assert requested_tool(question) == "get_financial_data"
    assert not is_direct_request(question, "get_financial_data")


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Не проверяй финансы, объясни проще",
    "Не запускай полную проверку, объясни значение прибыли",
    "Сделка на 10000000 рублей",
    "Расскажи о судах и финансах по уже полученным данным",
    "Почему?",
])
async def test_context_answer_does_not_force_tool_from_words(monkeypatch, question):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        _answer("Данные о прибыли получены."),
        _answer("Объяснение по доступным данным."),
    )
    runtime = _runtime(model, grounding_debug=False)
    calls = []

    async def execute(name, args, context):
        calls.append((name, args))
        return targeted_result()

    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Какая платёжеспособность у 6165169320?")
    response = await runtime.run(question, first.conversation_id)
    assert calls == [("get_financial_data", {"inn": "6165169320"})]
    assert response.metadata.tool_calls == 0
    assert response.metadata.model_calls == 1
    assert response.metadata.synthesis == "model"
    assert response.active_company.inn == "6165169320"
    assert response.message == "Объяснение по доступным данным."
    assert model._tool_bindings[-1]["tool_choice"] == "auto"
    assert set(model._tool_bindings[-1]["tools"]) == {
        "full_company_check", "get_financial_data", "get_legal_data",
    }
    assert _verified_context(model._messages[-1])["company"]["inn"] == "6165169320"


@pytest.mark.asyncio
async def test_semantic_choice_can_read_a_different_domain_and_preserves_arguments(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]), _answer(),
        AIMessage(content="", tool_calls=[_tool_call("get_legal_data", {
            "inn": "6165169320", "section": "licenses", "year": 2023, "offset": 5,
        })]), _answer("Прочитан запрошенный раздел."),
    )
    runtime = _runtime(model, grounding_debug=False)
    calls = []

    async def execute(name, args, context):
        calls.append((name, args))
        return targeted_result("finance" if name == "get_financial_data" else "legal")

    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    response = await runtime.run("Не нужны финансы, покажи лицензии за 2023, страница 2", first.conversation_id)
    assert calls[-1] == ("get_legal_data", {"inn": "6165169320", "section": "licenses", "year": 2023, "offset": 5})
    assert response.metadata.tool_calls == 1
    assert response.metadata.model_calls == 2
    assert response.metadata.synthesis == "model"
    assert response.external_news_status is None
    context = _verified_context(model._messages[-1])
    assert context["domain"] == "legal"
    assert context["related_domains"]["finance"]["metrics"]


def test_new_snapshot_never_reuses_old_domain_context():
    from app.agent.runtime import synthesis_context
    from app.agent.synthesis import normalized_tool_context
    cached = normalized_tool_context(targeted_result("finance"))
    cached["company"]["snapshot_id"] = 999
    context = synthesis_context(targeted_result("legal"), cached)
    assert not context.get("related_domains")


def test_complementary_context_does_not_nest_old_copies_of_the_new_domain():
    from app.agent.runtime import synthesis_context
    from app.agent.synthesis import normalized_tool_context
    finance = normalized_tool_context(targeted_result("finance"))
    legal = synthesis_context(targeted_result("legal"), finance)
    updated = synthesis_context(targeted_result("finance"), legal)
    assert set(updated["related_domains"]) == {"legal"}
    assert "related_domains" not in updated["related_domains"]["legal"]


@pytest.mark.asyncio
@pytest.mark.parametrize("proposal", [
    [_tool_call("get_financial_data"), _tool_call("full_company_check", call_id="extra")],
    [_tool_call("get_financial_data"), _tool_call("get_financial_data", call_id="extra")],
    [_tool_call("get_financial_data"), _tool_call("get_legal_data", {"inn": "6165169320", "sql": "select 1"}, call_id="extra")],
    [_tool_call("unknown_tool")],
    [_tool_call("get_financial_data", {"inn": "6165169320", "sql": "select 1"})],
])
async def test_invalid_proposals_never_trigger_guessed_fallback_execution(monkeypatch, proposal):
    runtime = _runtime(_model(AIMessage(content="", tool_calls=proposal)), grounding_debug=False)
    calls = []

    async def execute(*args):
        calls.append(args)
        return targeted_result()

    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Какая платёжеспособность у 6165169320?")
    assert not calls
    assert response.metadata.tool_calls == 0
    assert response.metadata.synthesis == "fallback"


@pytest.mark.asyncio
async def test_provider_failure_does_not_guess_a_domain(monkeypatch):
    runtime = _runtime(FailingToolCallingModel(responses=[_answer()]), grounding_debug=False)
    calls = []

    async def execute(*args):
        calls.append(args)
        return targeted_result()

    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Не проверяй финансы у 6165169320, объясни проще")
    assert not calls
    assert response.metadata.tool_calls == 0
    assert response.metadata.synthesis == "fallback"


@pytest.mark.asyncio
async def test_unavailable_capability_can_be_explained_without_running_a_check(monkeypatch):
    runtime = _runtime(_model(_answer("Счета-фактуры недоступны в этих инструментах.")), grounding_debug=False)
    calls = []

    async def execute(*args):
        calls.append(args)
        return targeted_result()

    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Какие счета-фактуры у контрагента 6165169320?")
    assert not calls
    assert response.metadata.model_calls == 1
    assert response.metadata.synthesis == "model"
    assert response.active_company.inn == "6165169320"


@pytest.mark.asyncio
async def test_company_switch_without_reading_drops_old_trusted_data(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]), _answer(),
        _answer("Уточните условия сделки."), _answer("Сначала нужны данные новой компании."),
    )
    runtime = _runtime(model, grounding_debug=False)

    async def execute(*args):
        return targeted_result()

    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    second = await runtime.run("Обсудим условия для ИНН 0278949271", first.conversation_id)
    third = await runtime.run("Почему?", first.conversation_id)
    assert second.active_company.inn == third.active_company.inn == "0278949271"
    for messages in model._messages[-2:]:
        context = _verified_context(messages)
        assert context["company"]["inn"] == "0278949271"
        assert not context.get("metrics")
        assert "6165169320" not in messages[0].content


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_domain", [None, "finance", "legal"])
async def test_two_distinct_reads_keep_success_and_followup_context(monkeypatch, failed_domain):
    from app.agent.models import ToolError, ToolResult, ToolResultMetadata
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        AIMessage(content="", tool_calls=[_tool_call("get_legal_data", call_id="legal")]),
        _answer("Вывод по прочитанным данным."),
        _answer("Объяснение по сохранённым данным."),
    )
    runtime = _runtime(model, grounding_debug=False)
    calls = []

    async def execute(name, args, context):
        calls.append(name)
        domain = "finance" if name == "get_financial_data" else "legal"
        if domain == failed_domain:
            return ToolResult(status="error", error=ToolError(code="timeout", user_safe_message="Источник не ответил."),
                              metadata=ToolResultMetadata(tool=name, latency_ms=1))
        return targeted_result(domain)

    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Прочитай финансы и суды у 6165169320, без полной проверки")
    assert calls == ["get_financial_data", "get_legal_data"]
    assert response.metadata.tool_calls == 2
    assert response.metadata.model_calls == 3
    assert response.metadata.synthesis == "model"
    assert model._tool_bindings[1]["tools"] == ["get_legal_data"]
    assert len(model._tool_bindings) == 2  # Final call does not bind any tools.
    context = _verified_context(model._messages[-1])
    if failed_domain:
        assert response.metadata.status == "partial"
        assert "не удалось прочитать" in response.message
        assert context["tool_errors"]
    else:
        assert context["related_domains"]["finance"]["metrics"]
        assert {item.fact_id for item in response.evidence} >= {"fin.profit_last", "court.defendant_count"}
    followup = await runtime.run("Почему?", response.conversation_id)
    assert followup.metadata.tool_calls == 0
    assert len(calls) == 2
    context = _verified_context(model._messages[-1])
    assert context["metrics"]
    if not failed_domain:
        assert context["related_domains"]


@pytest.mark.asyncio
@pytest.mark.parametrize("next_tool", ["get_financial_data", "full_company_check"])
async def test_second_read_cannot_repeat_or_escalate_to_full(monkeypatch, next_tool):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        AIMessage(content="", tool_calls=[_tool_call(next_tool, call_id="invalid")]),
    )
    runtime = _runtime(model, grounding_debug=False)
    calls = []
    async def execute(name, args, context):
        calls.append(name)
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Финансы и суды 6165169320")
    assert calls == ["get_financial_data"]
    assert response.metadata.tool_calls == 1
    assert response.metadata.synthesis == "fallback"
    assert response.evidence


def test_partial_first_domain_does_not_become_complete_after_second_read():
    from app.agent.langchain_tools import LangChainToolExecution
    from app.agent.runtime import execution_context
    execution = LangChainToolExecution(results=[targeted_result(availability="PARTIAL"), targeted_result("legal")])
    assert execution_context(execution, None)["coverage"]["state"] == "PARTIAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("order", [("finance", "legal"), ("legal", "finance")])
async def test_native_pair_is_read_sequentially_then_answered_once(monkeypatch, order):
    import asyncio
    names = {"finance": "get_financial_data", "legal": "get_legal_data"}
    model = _model(AIMessage(content="", tool_calls=[_tool_call(names[domain], call_id=domain) for domain in order]),
                   _answer("Совместный ответ."))
    runtime = _runtime(model, grounding_debug=False)
    active = 0
    reads = []
    async def execute(name, args, context):
        nonlocal active
        active += 1
        assert active == 1
        await asyncio.sleep(.01)
        reads.append(name)
        active -= 1
        return targeted_result("finance" if name == names["finance"] else "legal")
    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Финансы и суды 6165169320")
    assert set(reads) == set(names.values())
    assert response.metadata.tool_calls == response.metadata.model_calls == 2
    assert response.metadata.synthesis == "model"
    assert _verified_context(model._messages[-1])["related_domains"]
