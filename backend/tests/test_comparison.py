"""Сравнение контрагентов: один вызов инструмента, сопоставимые меры, границы."""
import json
import pytest
from pydantic import ValidationError
from langchain_core.messages import AIMessage

from app.agent.comparison import execute_comparison, measure_key
from app.agent.models import AssistantResponse, CompareCompaniesArgs, ComparisonTableBlock
from app.agent.runtime import comparison_focus, inspect_comparison_request
from app.agent.targeted_models import ComparisonData
from app.agent.tools import ToolContext, build_tool_registry
from app.llm.groq_client import GroqClient
from test_agent_runtime import _answer, _model, _runtime, _settings, _tool_call, _verified, _verified_context

RICH = "6165169320"
EMPTY = "2901324364"
OTHER = "0278949271"


def _fin_row(year, proceeds, profit=20, capital=40, payables=15):
    return {
        "common": {"year": year, "proceeds": proceeds, "profit": profit},
        "liabilities": {"capitals": capital, "shortTermLiabilities": {"accountsPayable": payables}},
    }


def _snapshot(inn, name, *, fin_rows=(), defendants=0, hard_stop=False):
    report = {"finReports": list(fin_rows), "baseInfo": {"inn": inn, "shortName": name}}
    if defendants:
        report["arbitrationCases"] = [{
            "defendantCount": defendants, "defendantAmount": 1000,
            "plaintiffCount": 0, "plaintiffAmount": 0,
        }]
    if hard_stop:
        report["reputationalRisks"] = {
            "negative": [{"code": "fnsBlocking", "chapter": "tax"}]
        }
    return {"inn": inn, "short_name": name, "document": {"report": report}}


@pytest.fixture
def snapshots(monkeypatch):
    store = {
        RICH: _snapshot(RICH, "ООО Богатая", fin_rows=[_fin_row(2023, 100), _fin_row(2024, 138)],
                        defendants=8, hard_stop=True),
        EMPTY: _snapshot(EMPTY, "ООО Пустая"),
        # Последний доступный год другой: меры всё равно должны совпасть в строке.
        OTHER: _snapshot(OTHER, "ООО Иная", fin_rows=[_fin_row(2022, 70)]),
    }

    async def get_latest_snapshot(inn):
        return store.get(inn)

    monkeypatch.setattr("app.infrastructure.repository.get_latest_snapshot", get_latest_snapshot)
    return store


async def _compare(inns, focus="both"):
    settings = _settings()
    context = ToolContext(settings=settings, client=GroqClient(settings), persist=False)
    return await execute_comparison(context, CompareCompaniesArgs(inns=inns, focus=focus))


def _table(response) -> ComparisonTableBlock:
    return next(block for block in response.blocks if block.type == "comparison_table")


@pytest.mark.asyncio
async def test_comparison_returns_one_result_for_every_company(snapshots):
    result = await _compare([RICH, EMPTY])
    data = ComparisonData.model_validate(result.data)

    assert result.metadata.tool == "compare_companies"
    assert [item.inn for item in data.companies] == [RICH, EMPTY]
    assert data.companies[0].availability != "NO_DATA"
    assert data.companies[1].availability == "NO_DATA"


@pytest.mark.asyncio
async def test_fact_ids_stay_separable_per_company(snapshots):
    result = await _compare([RICH, OTHER])
    data = ComparisonData.model_validate(result.data)

    assert all(fact_id.startswith((RICH + ":", OTHER + ":")) for fact_id in data.facts)
    evidence_ids = [item.id for item in result.evidence]
    assert len(evidence_ids) == len(set(evidence_ids))
    assert set(evidence_ids) == set(data.facts)


@pytest.mark.asyncio
async def test_five_data_rich_companies_fit_the_evidence_contract(monkeypatch):
    rows = [_fin_row(year, 100 + year) for year in range(2020, 2025)]
    store = {
        inn: _snapshot(inn, "ООО %s" % index, fin_rows=rows, defendants=8, hard_stop=True)
        for index, inn in enumerate((RICH, EMPTY, OTHER, "2311304742", "3711039473"), start=1)
    }

    async def get_latest_snapshot(inn):
        return store.get(inn)

    monkeypatch.setattr("app.infrastructure.repository.get_latest_snapshot", get_latest_snapshot)
    result = await _compare(list(store))
    data = ComparisonData.model_validate(result.data)

    assert len(result.evidence) <= 60
    assert set(data.facts) == {item.id for item in result.evidence}
    assert all(len(company.metric_ids) <= 10 for company in data.companies)
    response = await _runtime(None).run("Сравни " + ", ".join(store))
    table = _table(response)
    assert len(table.columns) == 5
    assert all(len(row.cells) == 5 for row in table.rows)
    assert all(len(column.key_facts) == 3 for column in table.columns)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,accepted", [(80_000, True), (70_000, False)])
async def test_registry_preserves_real_k15_comparison_within_size_limit(monkeypatch, documents, limit, accepted):
    inns = [RICH, "3711039473", "7813664770"]
    store = {
        doc["report"]["baseInfo"]["inn"]: {
            "inn": doc["report"]["baseInfo"]["inn"],
            "short_name": doc["report"]["baseInfo"].get("shortName"),
            "document": doc,
        }
        for doc in documents if doc["report"]["baseInfo"]["inn"] in inns
    }

    async def get_latest_snapshot(inn):
        return store.get(inn)

    monkeypatch.setattr("app.infrastructure.repository.get_latest_snapshot", get_latest_snapshot)
    settings = _settings(agent_tool_result_max_chars=limit)
    client = GroqClient(settings)
    context = ToolContext(settings=settings, client=client, persist=False)
    args = CompareCompaniesArgs(inns=inns, focus="both")
    try:
        expected = await execute_comparison(context, args)
        result = await build_tool_registry(settings).execute(
            "compare_companies", args.model_dump(mode="json"), context,
        )
    finally:
        await client.aclose()

    assert expected.status != "error"
    if accepted:
        assert result.status == expected.status
        assert result.data == expected.data
        assert result.evidence == expected.evidence
        assert result.warnings == expected.warnings
        assert [company["inn"] for company in result.data["companies"]] == inns
    else:
        assert result.status == "error"
        assert result.error.code == "result_too_large"


@pytest.mark.asyncio
async def test_missing_comparison_company_is_named_in_tool_error(snapshots):
    missing = "2311304742"
    settings = _settings()
    result = await build_tool_registry(settings).execute(
        "compare_companies",
        {"inns": [RICH, missing], "focus": "both"},
        ToolContext(settings=settings, client=GroqClient(settings), persist=False),
    )

    assert result.status == "error"
    assert result.error.code == "not_found"
    assert missing in result.error.user_safe_message


@pytest.mark.asyncio
async def test_focus_limits_collection_to_the_named_domain(snapshots):
    finance_only = ComparisonData.model_validate((await _compare([RICH, OTHER], "finance")).data)
    legal_only = ComparisonData.model_validate((await _compare([RICH, OTHER], "legal")).data)

    assert finance_only.focus == ["finance"]
    assert all("court." not in key and "execproc." not in key for key in finance_only.facts)
    assert legal_only.focus == ["legal"]
    assert all("fin." not in key for key in legal_only.facts)


def test_measure_key_folds_year_suffixed_finance_ids():
    assert measure_key("fin.proceeds.2024") == measure_key("fin.proceeds.2022") == "proceeds"
    assert measure_key("court.defendant_count") == "court.defendant_count"
    assert measure_key("fin.series") is None


def test_comparison_focus_follows_the_user_priority():
    assert comparison_focus("Сравни по выручке") == "finance"
    assert comparison_focus("Сравни по судам") == "legal"
    assert comparison_focus("Сравни этих поставщиков") == "both"


@pytest.mark.parametrize("message,expected", [
    ("Сравни 6165169320 и 2901324364", None),
    ("Сравни этих поставщиков", "comparison_needs_two"),
    ("Сравни 6165169320 и 1234567890", "invalid_inn"),
    ("Сравни 6165169320, 2901324364, 0278949271 и 2311304742", None),
    ("Сравни 6165169320, 2901324364, 0278949271, 2311304742 и 3711039473", None),
    ("Сравни 6165169320, 2901324364, 0278949271, 2311304742, 3711039473 и 7813664770", "comparison_limit"),
])
def test_comparison_request_guards(message, expected):
    reason, inns = inspect_comparison_request(message)
    assert reason == expected
    assert (inns is None) == (expected is not None)


@pytest.mark.asyncio
async def test_comparison_runs_one_tool_call_and_renders_a_backend_table(snapshots):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call(
            "compare_companies", {"inns": [RICH, OTHER], "focus": "both"})]),
        _answer("Выручка выше у первой компании, но у неё же больше исков."),
        _verified(),
    )
    runtime = _runtime(model)

    response = await runtime.run("Сравни %s и %s" % (RICH, OTHER))

    assert response.metadata.tool_calls == 1
    assert response.metadata.routing == "model"
    table = _table(response)
    assert [column.inn for column in table.columns] == [RICH, OTHER]
    # Разные последние годы сведены к одной сравнимой мере.
    revenue = next(row for row in table.rows if row.id == "proceeds")
    assert [cell.state for cell in revenue.cells] == ["data", "data"]
    assert all(len(row.cells) == len(table.columns) for row in table.rows)


@pytest.mark.asyncio
async def test_comparison_never_falls_back_to_a_full_check(monkeypatch, snapshots):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Сравнение не должно запускать полную проверку")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden)
    runtime = _runtime(None)

    response = await runtime.run("Сравни %s и %s" % (RICH, EMPTY))

    assert response.metadata.tool_calls == 1
    assert _table(response).columns[1].availability == "NO_DATA"


@pytest.mark.asyncio
async def test_backend_owns_the_compared_identifiers(snapshots):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call(
            "compare_companies", {"inns": [OTHER, EMPTY], "focus": "both"})]),
        _answer("Сравнение опирается на проверенные данные."),
        _verified(),
    )

    response = await _runtime(model).run("Сравни %s и %s" % (RICH, OTHER))

    # Модель предложила другой набор: сравниваются ИНН из сообщения пользователя.
    assert [column.inn for column in _table(response).columns] == [RICH, OTHER]
    assert response.metadata.routing == "deterministic_fallback"


@pytest.mark.asyncio
async def test_missing_company_data_is_shown_not_replaced_by_zero(snapshots):
    response = await _runtime(None).run("Сравни %s и %s" % (RICH, EMPTY))
    table = _table(response)

    revenue = next(row for row in table.rows if row.id == "proceeds")
    assert revenue.cells[1].state == "no_data"
    assert revenue.cells[1].display_value == "Нет данных"
    assert revenue.cells[1].evidence_id is None
    assert table.columns[1].key_facts == []
    assert table.columns[1].gaps


@pytest.mark.asyncio
async def test_summary_facts_keep_policy_provenance_and_table_periods(snapshots):
    response = await _runtime(None).run("Сравни %s и %s" % (RICH, OTHER))
    table = _table(response)
    first = table.columns[0]
    assert first.key_facts[0].evidence_id == RICH + ":flags.hard_stop_codes"
    revenue = next(row for row in table.rows if row.id == "proceeds").cells[0]
    fact = next(fact for fact in first.key_facts if fact.label == "Выручка")
    assert fact.display_value == revenue.display_value
    assert fact.evidence_id == revenue.evidence_id
    assert first.coverage_scope == "Финансы и правовые данные"
    assert "нет общего заполненного года" in " ".join(first.gaps)
    known = {item.id for item in response.evidence}
    assert all(fact.evidence_id in known for col in table.columns for fact in col.key_facts)
    payload = response.model_dump(mode="json")
    block = next(block for block in payload["blocks"] if block["type"] == "comparison_table")
    block["columns"][0]["key_facts"][0]["evidence_id"] = "invented"
    with pytest.raises(ValidationError, match="Неизвестные evidence"):
        AssistantResponse.model_validate(payload)


@pytest.mark.asyncio
async def test_finance_summary_does_not_claim_full_check_coverage(snapshots):
    response = await _runtime(None).run("Сравни финансы %s и %s" % (RICH, OTHER))
    assert all(col.coverage_scope == "Только финансы" for col in _table(response).columns)
    assert all(col.total_count == 5 for col in _table(response).columns)


@pytest.mark.asyncio
async def test_comparison_headers_use_actual_counts_and_independent_bank_risks(snapshots):
    snapshots[RICH]["document"]["report"]["baseInfo"]["riskLevel"] = "LOW"
    snapshots[RICH]["document"]["report"]["zskRiskLevel"] = "RED"
    response = await _runtime(None).run("Сравни %s и %s" % (RICH, EMPTY))
    table = _table(response)
    assert len(table.rows) == 10
    for index, column in enumerate(table.columns):
        assert column.total_count == 10
        assert column.filled_count == sum(row.cells[index].state == "data" for row in table.rows)
    assert table.columns[1].filled_count == 0
    assert table.columns[0].bank_risk_level == "LOW"
    assert table.columns[0].zsk_risk_level == "RED"
    assert table.columns[1].bank_risk_level is None
    assert {row.section for row in table.rows} == {"finance", "courts", "enforcement", "regulatory"}
    # Метка ограничения сохраняет источник даже при независимом низком банковском риске.
    assert table.columns[0].key_facts[0].evidence_id == RICH + ":flags.hard_stop_codes"


@pytest.mark.asyncio
@pytest.mark.parametrize("left,right,left_year,right_year,expected", [
    (100, 200, 2024, 2024, True),
    (100, 100, 2024, 2024, False),
    (100, 200, 2023, 2024, False),
    (None, 200, 2024, 2024, False),
    (0, 200, 2024, 2024, True),
    (None, None, 2024, 2024, False),
])
async def test_key_differences_compare_values_not_gaps_or_periods(
    snapshots, left, right, left_year, right_year, expected,
):
    snapshots[RICH] = _snapshot(RICH, "Первая", fin_rows=[_fin_row(left_year, left)])
    snapshots[OTHER] = _snapshot(OTHER, "Вторая", fin_rows=[_fin_row(right_year, right)])
    response = await _runtime(None).run("Сравни %s и %s" % (RICH, OTHER))
    table = _table(response)
    revenue = next(row for row in table.rows if row.id == "proceeds")
    assert revenue.is_key_difference is expected
    assert all(not row.is_key_difference for row in table.rows
               if row.id in {"inspections.count", "execproc.total_count"})


@pytest.mark.asyncio
async def test_hard_stop_signal_names_its_company(snapshots):
    response = await _runtime(None).run("Сравни %s и %s" % (RICH, OTHER))
    findings = next(block for block in response.blocks if block.type == "finding_list")

    assert findings.items
    assert all("ООО Богатая" in item.title for item in findings.items)
    known = {item.id for item in response.evidence}
    assert all(set(item.evidence_ids) <= known for item in findings.items)


@pytest.mark.asyncio
async def test_follow_up_after_comparison_needs_no_new_tool_call(snapshots):
    model = _model(
        AIMessage(content="", tool_calls=[_tool_call(
            "compare_companies", {"inns": [RICH, OTHER], "focus": "both"})]),
        _answer("Первая компания крупнее по выручке."),
        _verified(),
        _answer("Ориентируйтесь на выручку и судебную нагрузку."),
        _verified(),
    )
    runtime = _runtime(model)

    first = await runtime.run("Сравни %s и %s" % (RICH, OTHER))
    second = await runtime.run("Кого выбрать и почему?", first.conversation_id)

    assert second.metadata.tool_calls == 0
    assert second.metadata.error_code is None
    assert second.metadata.synthesis == "model"
    assert second.blocks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("debug", [False, True])
async def test_irrelevant_broken_risk_profile_does_not_discard_comparison(snapshots, direct, debug):
    text = "**Сравнение получено.**\n\n- **Оговорки:** учитывайте пробелы данных."
    answer = AIMessage(content=json.dumps({
        "message": text, "artifact": "none",
        "risk_profile": {"finance": {"level": "medium", "reason": "Неполный профиль"}},
    }, ensure_ascii=False))
    turns = [] if direct else [AIMessage(content="", tool_calls=[_tool_call(
        "compare_companies", {"inns": [RICH, OTHER], "focus": "both"})])]
    turns += [answer] + ([_verified()] if debug else [])
    turns += [answer] + ([_verified()] if debug else [])
    model = _model(*turns)
    runtime = _runtime(model, direct_dispatch=direct, grounding_debug=debug)
    first = await runtime.run("Сравни %s и %s" % (RICH, OTHER))
    assert first.message == text
    assert first.metadata.synthesis == "model"
    assert first.metadata.tool_calls == 1
    assert first.metadata.model_calls == (1 if direct else 2) + int(debug)
    assert len(_table(first).columns) == 2
    followup = await runtime.run("Кого выбрать и почему?", first.conversation_id)
    assert followup.message == text
    assert followup.metadata.synthesis == "model"
    assert followup.metadata.tool_calls == 0
    assert followup.blocks == []


@pytest.mark.asyncio
@pytest.mark.parametrize("with_active_company", [False, True])
async def test_k16_to_k20_reuse_comparison_before_single_company_context(snapshots, with_active_company):
    model = _model(_answer("Данные получены."))
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    cid = None
    if with_active_company:
        active = await runtime.run("Финансы %s" % RICH)
        cid = active.conversation_id
    first = await runtime.run("Сравни %s и %s" % (RICH, OTHER), cid)
    config = {"configurable": {"thread_id": first.conversation_id}}
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(config)
    initial = checkpoint.checkpoint["channel_values"]
    for question in (
        "У кого больше кредиторская задолженность?",
        "У кого кредиторка больше относительно выручки?",
        "У кого больше всего кредиторов?",
        "У кого выше судебная нагрузка относительно масштаба бизнеса?",
        "А теперь главное — минимальный legal risk. Кто лучший?",
    ):
        response = await runtime.run(question, first.conversation_id)
        assert response.metadata.synthesis == "model"
        assert response.metadata.model_calls == 1
        assert response.metadata.tool_calls == 0
        assert response.metadata.error_code is None
        context = _verified_context(model._messages[-1])
        assert context == initial["comparison_context"]
        assert [company["inn"] for company in context["companies"]] == [RICH, OTHER]
    checkpoint = await runtime.conversation_store.checkpointer.aget_tuple(config)
    final = checkpoint.checkpoint["channel_values"]
    for key in ("active_company", "trusted_context", "comparison_context", "last_topic"):
        assert final.get(key) == initial.get(key)


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "У кого больше кредиторская задолженность?",
    "У кого кредиторка больше относительно выручки?",
    "У кого выше судебная нагрузка относительно масштаба бизнеса?",
])
async def test_comparison_followup_in_new_chat_requires_identifiers(snapshots, question):
    model = _model(_answer())
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    await runtime.run("Сравни %s и %s" % (RICH, OTHER))
    response = await runtime.run(question)
    assert response.metadata.status == "needs_input"
    assert response.metadata.model_calls == 1
    assert response.metadata.tool_calls == 0
    # A new chat can clarify freely, but must not see another chat's companies.
    assert RICH not in model._messages[-1][0].content
    assert OTHER not in model._messages[-1][0].content


@pytest.mark.asyncio
async def test_comparison_context_does_not_override_explicit_identifiers(snapshots):
    model = _model(
        _answer(),
        AIMessage(content="", tool_calls=[_tool_call("get_financial_data", {"inn": OTHER})]),
        _answer(), _answer(),
    )
    runtime = _runtime(model, direct_dispatch=True, grounding_debug=False)
    first = await runtime.run("Сравни %s и %s" % (RICH, OTHER))
    for question in ("Почему? ИНН 123", "Почему 6165169320 и 0278949271?"):
        response = await runtime.run(question, first.conversation_id)
        assert response.metadata.status == "needs_input"
        assert response.metadata.model_calls == response.metadata.tool_calls == 0
    response = await runtime.run("Финансы %s" % OTHER, first.conversation_id)
    assert response.metadata.tool_calls == 1
    assert response.active_company.inn == OTHER
    assert _verified_context(model._messages[-1])["domain"] == "finance"
    response = await runtime.run("У кого больше кредиторская задолженность?", first.conversation_id)
    assert _verified_context(model._messages[-1])["domain"] == "finance"


@pytest.mark.asyncio
@pytest.mark.parametrize("question", [
    "Обнови финансовые данные",
    "Покажи судебные дела за 2023 год",
    "Нужна полная проверка",
])
async def test_comparison_followup_does_not_replace_explicit_data_requests(snapshots, question):
    runtime = _runtime(_model(_answer()), direct_dispatch=True, grounding_debug=False)
    first = await runtime.run("Сравни %s и %s" % (RICH, OTHER))
    response = await runtime.run(question, first.conversation_id)
    assert response.metadata.status == "needs_input"
    assert response.metadata.model_calls == response.metadata.tool_calls == 0


@pytest.mark.asyncio
async def test_finance_only_comparison_is_not_reused_as_legal_context(snapshots):
    runtime = _runtime(_model(
        AIMessage(content="", tool_calls=[_tool_call(
            "compare_companies", {"inns": [RICH, OTHER], "focus": "finance"})]),
        _answer(),
    ), grounding_debug=False)
    first = await runtime.run("Сравни финансы %s и %s" % (RICH, OTHER))
    response = await runtime.run("У кого выше судебная нагрузка?", first.conversation_id)
    assert response.metadata.status == "needs_input"
    assert response.metadata.model_calls == response.metadata.tool_calls == 0


@pytest.mark.asyncio
async def test_comparison_does_not_overwrite_the_active_company(snapshots, monkeypatch, check_payload):
    async def fake_run_check(inn, *args, **kwargs):
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    runtime = _runtime(None)

    checked = await runtime.run("Проверь контрагента %s" % RICH)
    compared = await runtime.run("Сравни %s и %s" % (RICH, OTHER), checked.conversation_id)

    assert checked.active_company.inn == RICH
    assert compared.active_company.inn == RICH
