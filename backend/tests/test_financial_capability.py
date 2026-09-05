"""Targeted finance: исходные значения, границы полноты и отсутствие full check."""
import copy

import pytest
from pydantic import ValidationError

from app.agent.finance import build_financial_data, execute_financial_data
from app.agent.models import FullCompanyCheckArgs
from app.agent.tools import ToolContext, ToolDefinition, ToolRegistry
from app.agent.targeted_models import TargetedData
from app.config import Settings
from app.llm.groq_client import GroqClient


INN = "6165169320"


def _row(year, revenue=100, profit=20, capital=40, payables=15):
    return {
        "common": {"year": year, "proceeds": revenue, "profit": profit},
        "liabilities": {"capitals": capital, "shortTermLiabilities": {"accountsPayable": payables}},
    }


def _snapshot(*rows):
    return {"inn": INN, "short_name": "ООО Тест", "document": {"report": {"finReports": list(rows)}}}


def test_finance_reuses_builder_and_preserves_original_row_references(monkeypatch):
    import app.agent.finance as finance
    original = finance.build_finance
    calls = []

    def wrapped(document):
        calls.append(document)
        return original(document)

    monkeypatch.setattr(finance, "build_finance", wrapped)
    snapshot = _snapshot(_row(2024, {"$numberLong": "150"}), _row(2023, 100))
    snapshot["document"]["report"]["courtSummary"] = {"irrelevant": "never forwarded"}
    data = build_financial_data(snapshot, INN)
    assert len(calls) == 1
    assert set(calls[0]["report"]) == {"finReports"}
    assert data.availability == "PARTIAL"  # fixture does not disclose the extended balance
    assert data.facts["fin.proceeds.2024"].value == 150
    assert data.facts["fin.proceeds.2024"].field_ref == "report.finReports[0].common.proceeds"
    assert data.facts["fin.proceeds_change_pct"].value == 50
    assert data.facts["fin.series"].value[0]["year"] == 2023
    assert set(data.metric_ids) <= set(data.facts)
    assert data.series_ids == ["fin.series"]
    assert data.policy_signals == []


@pytest.mark.parametrize("snapshot", [_snapshot(), _snapshot(_row(2024, None, None, None, None)), {"inn": INN}])
def test_finance_no_data_does_not_become_zero_or_no_risk(snapshot):
    data = build_financial_data(snapshot, INN)
    assert data.availability == "NO_DATA"
    assert any("невозможно оценить" in gap for gap in data.gaps)
    assert data.policy_signals == []


def test_partial_missing_profit_preserves_zero_revenue():
    data = build_financial_data(_snapshot(_row(2023), _row(2024, 0, None)), INN)
    assert data.availability == "PARTIAL"
    assert data.facts["fin.proceeds.2024"].value == 0
    assert data.facts["fin.profit.2024"].value is None
    assert data.facts["fin.proceeds_change_pct"].value == -100
    assert "fin.proceeds_change_pct" in data.metric_ids
    assert data.policy_signals == []


@pytest.mark.parametrize("rows", [(_row(2020), _row(2024)), (_row(2023, 0), _row(2024))])
def test_yoy_requires_consecutive_years_and_nonzero_base(rows):
    data = build_financial_data(_snapshot(*rows), INN)
    assert data.availability == "PARTIAL"
    assert "fin.proceeds_change_pct" not in data.facts


@pytest.mark.parametrize("invalid_year", [
    2024.9, "2024.9", {"$numberDouble": "2024.9"},
    True, False, "NaN", "Infinity",
])
def test_invalid_year_is_discarded_without_losing_valid_adjacent_years(invalid_year):
    data = build_financial_data(
        _snapshot(_row(2023, 100), _row(invalid_year, 999), _row(2024, 150)), INN,
    )
    assert data.availability == "PARTIAL"
    assert any("корректного отчётного года" in gap for gap in data.gaps)
    assert [row["year"] for row in data.facts["fin.series"].value] == [2023, 2024]
    assert data.facts["fin.proceeds.2024"].value == 150
    assert data.facts["fin.proceeds.2024"].field_ref == "report.finReports[2].common.proceeds"
    assert data.facts["fin.proceeds_change_pct"].value == 50


@pytest.mark.parametrize("fractional_year", [2024.9, "2024.9"])
def test_fractional_year_cannot_create_a_fabricated_yoy(fractional_year):
    data = build_financial_data(_snapshot(_row(2023), _row(fractional_year, 150)), INN)
    assert data.availability == "PARTIAL"
    assert "fin.proceeds.2024" not in data.facts
    assert "fin.proceeds_change_pct" not in data.facts


def test_negative_capital_and_losses_remain_data_not_policy_conclusions():
    data = build_financial_data(_snapshot(_row(2023), _row(2024, profit=-1, capital=-2)), INN)
    assert data.facts["fin.profit.2024"].value == -1
    assert data.facts["fin.capitals.2024"].value == -2
    assert data.facts["fin.series"].value[-1]["profit"] == -1
    assert data.policy_signals == []


def test_bounded_rows_and_nonfinite_values():
    data = build_financial_data(_snapshot(*[_row(year) for year in range(2010, 2025)]), INN)
    assert len(data.facts["fin.series"].value) == 5
    assert len(data.facts) < 60
    assert len(data.metric_ids) <= 8
    nonfinite = build_financial_data(_snapshot(_row(2024, "NaN", "Infinity")), INN)
    assert nonfinite.facts["fin.proceeds.2024"].value is None
    assert nonfinite.facts["fin.profit.2024"].value is None


def test_boolean_financial_values_are_missing_not_currency():
    data = build_financial_data(
        _snapshot(_row(2024, revenue=True, profit=False, capital=True, payables=False)), INN,
    )
    assert data.availability == "NO_DATA"
    for key in ("proceeds", "profit", "capitals", "accounts_payable"):
        assert data.facts["fin.%s.2024" % key].value is None
        assert data.facts["fin.series"].value[0][key] is None


def test_duplicate_years_are_not_arbitrarily_selected():
    data = build_financial_data(_snapshot(_row(2023), _row(2024, 200), _row(2024, 999)), INN)
    assert "fin.proceeds.2024" not in data.facts
    assert data.availability == "PARTIAL"
    assert any("Повторяющиеся" in gap for gap in data.gaps)


@pytest.mark.asyncio
async def test_executor_reads_snapshot_only_and_returns_exact_evidence(monkeypatch):
    calls = []

    async def snapshot(inn):
        calls.append(inn)
        return _snapshot(_row(2023), _row(2024, 125))

    def forbidden(*args, **kwargs):
        raise AssertionError("Targeted finance must not execute full pipeline or LLM")

    monkeypatch.setattr("app.agent.finance.repository.get_latest_snapshot", snapshot)
    monkeypatch.setattr("app.domain.pipeline.run_check", forbidden)
    monkeypatch.setattr("app.agent.tools.run_check", forbidden)
    monkeypatch.setattr("app.domain.facts.build_all_blocks", forbidden)
    monkeypatch.setattr("app.llm.agents.run_block_agents", forbidden)
    monkeypatch.setattr("app.llm.groq_client.GroqClient.complete_json", forbidden)
    settings = Settings(llm_mock=True)
    context = ToolContext(settings=settings, client=GroqClient(settings), persist=False)
    result = await execute_financial_data(context, FullCompanyCheckArgs(inn=INN))
    assert calls == [INN]
    assert result.status == "partial"  # source omits balance items
    data = TargetedData.model_validate(result.data)
    for evidence in result.evidence:
        assert evidence.fact_id in data.facts
        assert evidence.field_ref == data.facts[evidence.fact_id].field_ref
    assert result.metadata.tool == "get_financial_data"
    assert result.metadata.run_id is None


@pytest.mark.asyncio
async def test_registry_rejects_invalid_args_and_normalizes_missing_snapshot(monkeypatch):
    calls = []

    async def missing(inn):
        calls.append(inn)
        return None

    monkeypatch.setattr("app.agent.finance.repository.get_latest_snapshot", missing)
    settings = Settings(llm_mock=True)
    context = ToolContext(settings=settings, client=GroqClient(settings), persist=False)
    registry = ToolRegistry([ToolDefinition(
        name="get_financial_data", description="Finance", input_model=FullCompanyCheckArgs,
        output_model=TargetedData, risk_class="read_only", side_effects="none", timeout_s=1,
        result_size_limit=50000, retry_policy="none", executor=execute_financial_data,
    )])
    invalid = await registry.execute("get_financial_data", {"inn": INN, "extra": True}, context)
    assert invalid.error.code == "invalid_arguments"
    assert not calls
    missing_result = await registry.execute("get_financial_data", {"inn": INN}, context)
    assert missing_result.error.code == "not_found"


def test_contract_rejects_unknown_normalized_fact_reference():
    payload = build_financial_data(_snapshot(_row(2023), _row(2024)), INN).model_dump()
    payload = copy.deepcopy(payload)
    payload["series_ids"] = ["fin.fabricated"]
    with pytest.raises(ValidationError):
        TargetedData.model_validate(payload)
