"""Сравнение контрагентов: один вызов инструмента, сопоставимые меры, границы."""
import pytest
from langchain_core.messages import AIMessage

from app.agent.comparison import execute_comparison, measure_key
from app.agent.models import CompareCompaniesArgs, ComparisonTableBlock
from app.agent.runtime import comparison_focus, inspect_comparison_request
from app.agent.targeted_models import ComparisonData
from app.agent.tools import ToolContext, build_tool_registry
from app.llm.groq_client import GroqClient
from test_agent_runtime import _answer, _model, _runtime, _settings, _tool_call, _verified

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
async def test_three_data_rich_companies_fit_the_evidence_contract(monkeypatch):
    rows = [_fin_row(year, 100 + year) for year in range(2020, 2025)]
    store = {
        inn: _snapshot(inn, "ООО %s" % index, fin_rows=rows, defendants=8, hard_stop=True)
        for index, inn in enumerate((RICH, EMPTY, OTHER), start=1)
    }

    async def get_latest_snapshot(inn):
        return store.get(inn)

    monkeypatch.setattr("app.infrastructure.repository.get_latest_snapshot", get_latest_snapshot)
    result = await _compare([RICH, EMPTY, OTHER])
    data = ComparisonData.model_validate(result.data)

    assert len(result.evidence) <= 60
    assert set(data.facts) == {item.id for item in result.evidence}
    assert all(len(company.metric_ids) <= 10 for company in data.companies)


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
    ("Сравни 6165169320, 2901324364, 0278949271 и 2311304742", "comparison_limit"),
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
async def test_comparison_does_not_overwrite_the_active_company(snapshots, monkeypatch, check_payload):
    async def fake_run_check(inn, *args, **kwargs):
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    runtime = _runtime(None)

    checked = await runtime.run("Проверь контрагента %s" % RICH)
    compared = await runtime.run("Сравни %s и %s" % (RICH, OTHER), checked.conversation_id)

    assert checked.active_company.inn == RICH
    assert compared.active_company.inn == RICH
