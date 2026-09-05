"""Conversation isolation and observable two-step targeted Master loop."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.conversations import ConversationStore
from app.agent.models import ToolError, ToolFact, ToolResult, ToolResultMetadata
from app.agent.runtime import MasterAgentRuntime
from app.agent.tools import _evidence_from_fact
from test_agent_runtime import _runtime, _model, _tool_call, _verified_context


def targeted_result(domain="finance", inn="6165169320", availability="DATA"):
    fact_id = "fin.profit_last" if domain == "finance" else "court.defendant_count"
    fact = ToolFact(id=fact_id, label="Прибыль" if domain == "finance" else "Судебные дела",
                    value=500 if domain == "finance" else 2,
                    field_ref="report.test", source="computed")
    return ToolResult(status="success" if availability == "DATA" else "partial",
        data={"domain": domain, "company": {"inn": inn, "short_name": "Проверенная компания"},
              "availability": availability, "facts": {fact_id: fact.model_dump()},
              "metric_ids": [fact_id], "series_ids": [], "event_ids": [],
              "status_ids": [], "policy_signals": [], "gaps": []},
        evidence=[_evidence_from_fact(fact)], metadata=ToolResultMetadata(
            tool="get_financial_data" if domain == "finance" else "get_legal_data", latency_ms=1))


@pytest.mark.asyncio
async def test_full_check_then_finance_and_legal_use_active_company_and_second_model_step(monkeypatch, check_payload):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call()]),
        AIMessage(content='{"message":"Проверка завершена, разберём важное.","artifact":"none"}'),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        AIMessage(content='{"message":"Прибыль нужно смотреть вместе с динамикой.","artifact":"none"}'),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
        AIMessage(content="", tool_calls=[_tool_call("get_legal_data")]),
        AIMessage(content='{"message":"Судебные события требуют разбора контекста.","artifact":"none"}'),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
    )
    runtime = _runtime(model)
    calls = []
    original = runtime.registry.execute

    async def fake_check(*args, **kwargs):
        return check_payload
    monkeypatch.setattr("app.agent.tools.run_check", fake_check)

    async def execute(name, arguments, context):
        calls.append((name, arguments))
        if name == "full_company_check":
            return await original(name, arguments, context)
        return targeted_result("finance" if name == "get_financial_data" else "legal")
    monkeypatch.setattr(runtime.registry, "execute", execute)

    first = await runtime.run("Проверь контрагента 6165169320")
    second = await runtime.run("А что у них с финансами?", first.conversation_id)
    third = await runtime.run("А что у них с судами?", first.conversation_id)
    assert first.active_company.inn == "6165169320"
    assert second.conversation_id == third.conversation_id == first.conversation_id
    assert calls == [(name, {"inn": "6165169320"}) for name in
                     ("full_company_check", "get_financial_data", "get_legal_data")]
    assert model.calls == 9
    assert second.metadata.model_calls == third.metadata.model_calls == 3
    assert second.metadata.synthesis == third.metadata.synthesis == "model"
    for index, domain in ((4, "finance"), (7, "legal")):
        payload = _verified_context(model._messages[index])
        assert payload["domain"] == domain
        assert payload["evidence"][0]["field_ref"] == "report.test"


@pytest.mark.asyncio
async def test_model_cannot_replace_active_company_or_verified_data(monkeypatch):
    model = _model(AIMessage(content="", tool_calls=[_tool_call("get_financial_data", {"inn": "0278949271"})]))
    runtime = _runtime(model)
    calls = []
    async def execute(name, args, context):
        calls.append(args)
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Какая прибыль у 6165169320?")
    assert calls == [{"inn": "6165169320"}]
    assert response.active_company.inn == "6165169320"
    assert response.metadata.synthesis == "fallback"
    assert "987654321" not in response.model_dump_json()
    assert "invented.test" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_new_session_unknown_id_and_missing_context_do_not_reuse_another_company(monkeypatch):
    runtime = _runtime(None)
    calls = []
    async def execute(name, args, context):
        calls.append(args)
        return targeted_result(inn=args["inn"])
    monkeypatch.setattr(runtime.registry, "execute", execute)
    a = await runtime.run("Финансы 6165169320")
    b = await runtime.run("Финансы 0278949271")
    follow = await runtime.run("А что с финансами?", a.conversation_id)
    fresh = await runtime.run("А что с финансами?")
    unknown = await runtime.run("А что с финансами?", "unknown-id")
    assert follow.active_company.inn == a.active_company.inn == "6165169320"
    assert b.active_company.inn == "0278949271"
    assert fresh.active_company is None and fresh.metadata.tool_calls == 0
    assert unknown.metadata.error_code == "unknown_conversation"
    assert unknown.conversation_id is None
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_failed_new_run_never_returns_previous_tool_artifact(monkeypatch):
    runtime = _runtime(None)
    async def execute(name, args, context):
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    async def slow(name, args, context):
        await asyncio.sleep(1)
    monkeypatch.setattr(runtime.registry, "execute", slow)
    runtime.run_timeout_s = .01
    second = await runtime.run("А что с судами?", first.conversation_id)
    assert second.metadata.error_code == "timeout"
    assert not second.evidence
    assert second.active_company.inn == first.active_company.inn


@pytest.mark.asyncio
async def test_failed_company_switch_preserves_previous_active_company_and_trusted_context(monkeypatch):
    runtime = _runtime(None)

    async def execute(name, args, context):
        if args["inn"] == "6165169320":
            return targeted_result(inn=args["inn"])
        return ToolResult(
            status="error",
            error=ToolError(
                code="not_found", user_safe_message="Карточка не найдена."
            ),
            metadata=ToolResultMetadata(tool=name, latency_ms=1),
        )

    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    failed = await runtime.run("Финансы 0278949271", first.conversation_id)

    assert failed.metadata.error_code == "not_found"
    assert failed.active_company.inn == "6165169320"
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(
        {"configurable": {"thread_id": first.conversation_id}}
    )
    values = checkpoint.checkpoint["channel_values"]
    assert values["active_company"]["inn"] == "6165169320"
    assert values["trusted_context"]["company"]["inn"] == "6165169320"
    assert all("0278949271" not in item for item in values["user_context"])


@pytest.mark.asyncio
async def test_history_and_checkpoints_are_bounded_and_only_trusted_turns_persist(monkeypatch):
    runtime = _runtime(None)
    runtime.conversation_store = ConversationStore(max_turns=2)
    async def execute(name, args, context):
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Финансы 6165169320")
    for _ in range(6):
        response = await runtime.run("Финансы?", response.conversation_id)
    config = {"configurable": {"thread_id": response.conversation_id}}
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(config)
    values = checkpoint.checkpoint["channel_values"]
    assert len(values["messages"]) == 4
    assert all(not isinstance(m, ToolMessage) for m in values["messages"])
    assert values["active_company"]["inn"] == "6165169320"
    checkpoints = [item async for item in runtime.conversation_store.checkpointer.alist(config)]
    assert len(checkpoints) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("domain,question", [("finance", "Финансы?"), ("legal", "Суды?")])
async def test_unavailable_routing_fallback_stays_targeted(monkeypatch, domain, question):
    runtime = _runtime(None)
    calls = []
    async def execute(name, args, context):
        calls.append(name)
        return targeted_result(domain, availability="NO_DATA")
    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run(question + " 6165169320")
    second = await runtime.run(question, first.conversation_id)
    expected = "get_financial_data" if domain == "finance" else "get_legal_data"
    assert calls == [expected, expected]
    assert second.metadata.routing == "deterministic_fallback"
    assert second.metadata.status == "partial"
    assert "содержательный вывод сделать нельзя" in second.message.lower()


@pytest.mark.asyncio
async def test_followup_year_is_not_inn_but_explicit_invalid_inn_and_comparison_are_rejected(monkeypatch):
    runtime = _runtime(None)
    calls = []
    async def execute(name, args, context):
        calls.append((name, args))
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    dated = await runtime.run("А финансы за 2023?", first.conversation_id)
    invalid = await runtime.run("Финансы ИНН 123?", first.conversation_id)
    mixed = await runtime.run("Финансы 6165169320 и 1234567890", first.conversation_id)
    comparison = await runtime.run("Сравни финансы", first.conversation_id)
    assert dated.metadata.tool_calls == 1
    for response in (invalid, mixed, comparison):
        assert response.metadata.status == "needs_input"
        assert response.metadata.tool_calls == 0
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_queued_timeout_does_not_execute_or_modify_checkpoint(monkeypatch):
    runtime = _runtime(None)
    calls = []
    async def execute(name, args, context):
        calls.append(name)
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    config = {"configurable": {"thread_id": first.conversation_id}}
    before = await runtime.conversation_store.checkpointer.aget_tuple(config)
    runtime.run_timeout_s = .02
    async with runtime.conversation_store.session(first.conversation_id):
        response = await asyncio.wait_for(
            runtime.run("А что с судами?", first.conversation_id), timeout=.2
        )
        assert response.metadata.error_code == "timeout"
        assert response.metadata.tool_calls == response.metadata.model_calls == 0
        assert response.active_company == first.active_company
        assert response.conversation_id == first.conversation_id
    after = await runtime.conversation_store.checkpointer.aget_tuple(config)
    assert after.checkpoint == before.checkpoint
    assert calls == ["get_financial_data"]
    # A cancelled waiter releases its lease reference and never executes later.
    assert runtime.conversation_store._leases[first.conversation_id].users == 0
    await asyncio.sleep(0)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_execution_uses_budget_remaining_after_queue_wait(monkeypatch):
    runtime = _runtime(None)
    async def execute(name, args, context):
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    first = await runtime.run("Финансы 6165169320")
    runtime.run_timeout_s = .1
    executed = []
    async def slow(name, args, context):
        executed.append(name)
        await asyncio.sleep(.07)
        return targeted_result("legal")
    monkeypatch.setattr(runtime.registry, "execute", slow)
    async with runtime.conversation_store.session(first.conversation_id):
        queued = asyncio.create_task(runtime.run("А что с судами?", first.conversation_id))
        await asyncio.sleep(.06)
    response = await queued
    assert executed == ["get_legal_data"]
    assert response.metadata.error_code == "timeout"
    assert response.active_company == first.active_company
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(
        {"configurable": {"thread_id": first.conversation_id}}
    )
    assert checkpoint.checkpoint["channel_values"]["active_company"]["inn"] == first.active_company.inn
    assert checkpoint.checkpoint["channel_values"]["messages"][-1].content == response.message


@pytest.mark.asyncio
async def test_operational_trace_includes_safe_context_usage_and_fallback(monkeypatch, caplog):
    caplog.set_level("INFO", logger="app.agent")
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")],
                  usage_metadata={"input_tokens": 5, "output_tokens": 2, "total_tokens": 7}),
        AIMessage(content='{"message":"Проверенный финансовый ответ.","artifact":"none"}',
                  usage_metadata={"input_tokens": 10, "output_tokens": 3, "total_tokens": 13}),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
    )
    runtime = _runtime(model)
    async def execute(name, args, context):
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    await runtime.run("Финансы 6165169320 секретный-текст-пользователя")
    runtime.model = None
    await runtime.run("Финансы 6165169320")
    trace = caplog.text
    assert "agent_run_started" in trace and "prompt_version=" in trace
    assert "tool_bundle_version=" in trace and "inn=6165169320" in trace
    assert "input_tokens=15 output_tokens=5" in trace
    assert "agent_tool_result" in trace and "routing=deterministic_fallback" in trace
    assert "synthesis=model" in trace and "latency_ms=" in trace
    assert "секретный-текст-пользователя" not in trace


@pytest.mark.asyncio
async def test_second_model_policy_has_answer_schema_and_normalized_context(monkeypatch):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data")]),
        AIMessage(content='{"message":"Содержательный ответ.","artifact":"none"}'),
        AIMessage(content='{"supported":true,"unsupported_claims":[]}'),
    )
    runtime = _runtime(model)
    async def execute(name, args, context):
        return targeted_result()
    monkeypatch.setattr(runtime.registry, "execute", execute)
    response = await runtime.run("Финансы 6165169320")
    assert response.metadata.synthesis == "model"
    system = model._messages[1][0].content
    assert '"required":["message"]' in system
    assert '"additionalProperties":false' in system
    assert '"domain":"finance"' in system
    assert '"findings"' not in system
