"""Source-to-Master coverage: real snapshots plus adversarial accounting cases."""
import copy
import json

import pytest

from app.agent.data_sections import numeric
from app.agent.finance import ALL_PATHS, build_financial_data
from app.agent.legal import build_legal_data
from app.agent.synthesis import normalized_tool_context
from app.agent.tools import ToolContext, build_tool_registry
from app.config import Settings
from app.infrastructure.mongo import parse_date
from app.llm.groq_client import GroqClient
from test_financial_capability import _row, _snapshot
from test_comparison import RICH, EMPTY, OTHER

INN = RICH


def _real_snapshot(document):
    report = document["report"]
    base = report["baseInfo"]
    return {
        "inn": base["inn"], "ogrn": base.get("ogrn"),
        "short_name": base.get("shortName"), "full_name": base.get("fullName"),
        "status": report["status"]["status"],
        "risk_level": base.get("riskLevel"), "zsk_risk_level": report.get("zskRiskLevel"),
        "report_date": parse_date(report["reportDate"]),
        "snapshot_id": "offline:" + base["inn"] + ":" + parse_date(report["reportDate"]).isoformat(),
        "document": document,
    }


def _context():
    settings = Settings(llm_mock=True, groq_api_key=None)
    return ToolContext(settings=settings, client=GroqClient(settings), persist=False)


def _patch_snapshots(monkeypatch, snapshots):
    async def read(inn):
        return snapshots.get(inn)
    monkeypatch.setattr("app.infrastructure.repository.get_latest_snapshot", read)


@pytest.mark.asyncio
async def test_all_100_real_snapshots_survive_finance_legal_normalization(documents, monkeypatch):
    assert len(documents) == 100
    snapshots = {doc["report"]["baseInfo"]["inn"]: _real_snapshot(doc) for doc in documents}
    _patch_snapshots(monkeypatch, snapshots)
    context = _context()
    registry = build_tool_registry(context.settings)
    for inn, snapshot in snapshots.items():
        for tool in ("get_financial_data", "get_legal_data"):
            result = await registry.execute(tool, {"inn": inn}, context)
            assert result.status != "error", (inn, tool, result.error)
            normalized = normalized_tool_context(result)
            json.dumps(normalized, ensure_ascii=False, allow_nan=False)
            assert normalized["company"]["inn"] == inn
            assert normalized["company"]["snapshot_id"] == snapshot["snapshot_id"]
            assert normalized["company"]["risk_level"] == snapshot["risk_level"]
            assert normalized["company"]["zsk_risk_level"] == snapshot["zsk_risk_level"]
            assert normalized["company"]["report_date"]
            assert normalized["sections"]["source_dates"]["value"]["report_date"]
            assert len(result.model_dump_json()) <= registry.get_definition(tool).result_size_limit
            if tool == "get_financial_data":
                series = result.data["facts"]["fin.series"]["value"]
                raw_rows = snapshot["document"]["report"].get("finReports", [])
                for row in series:
                    for key, path in ALL_PATHS.items():
                        value, state = numeric(raw_rows[row["source_index"]], path)
                        assert row[key] == value, (inn, key, row["year"])
                        assert row["field_states"].get(key, "data") == state
                if series:
                    assert normalized["series"][0]["value"] == series
            else:
                for name in ("court_years", "proceedings", "inspections"):
                    section = normalized["sections"][name]
                    if isinstance(section["value"], list):
                        assert len(section["value"]) <= 5
                        assert section["included"] == len(section["value"])
                assert len(normalized["sections"]["court_stages"]["value"]) == 6


@pytest.mark.asyncio
async def test_three_rich_real_companies_comparison_is_scoped_and_bounded(documents, monkeypatch):
    chosen = sorted(documents, key=lambda doc: len(doc["report"]["executionProceedings"]), reverse=True)[:3]
    snapshots = {doc["report"]["baseInfo"]["inn"]: _real_snapshot(doc) for doc in chosen}
    _patch_snapshots(monkeypatch, snapshots)
    context = _context()
    registry = build_tool_registry(context.settings)
    result = await registry.execute("compare_companies", {"inns": list(snapshots), "focus": "both"}, context)
    assert result.status != "error", result.error
    normalized = normalized_tool_context(result)
    json.dumps(normalized, allow_nan=False)
    assert len(result.model_dump_json()) <= registry.get_definition("compare_companies").result_size_limit
    assert len(normalized["companies"]) == 3
    for company in normalized["companies"]:
        inn = company["inn"]
        assert all(metric["id"].startswith(inn + ":") for metric in company["metrics"])
        assert company["company"]["snapshot_id"] == snapshots[inn]["snapshot_id"]
        assert "finance_series" in company["sections"]
        assert "court_stages" in company["sections"]
        for row in company["sections"]["proceedings"]["value"]:
            raw = snapshots[inn]["document"]["report"]["executionProceedings"][row["source_index"]]
            assert row["number"] == raw["number"]


@pytest.mark.parametrize("value,state", [(0, "data"), (None, "null"), ("NaN", "invalid"),
                                        ("Infinity", "invalid"), (True, "invalid"), ({}, "invalid")])
def test_finance_field_states_preserve_zero_null_invalid(value, state):
    row = _row(2025)
    row["assets"] = {"currentAssets": {"bankroll": value}}
    data = build_financial_data(_snapshot(row), RICH)
    observation = data.facts["fin.series"].value[0]
    assert observation["bankroll"] == (0 if state == "data" else None)
    assert observation["field_states"].get("bankroll", "data") == state
    assert observation["field_states"]["receivables"] == "missing"
    json.dumps(data.model_dump(mode="json"), allow_nan=False)


def test_balance_only_report_is_available_even_without_legacy_four_metrics():
    row = {"common": {"year": 2025}, "assets": {"currentAssets": {"total": 100, "bankroll": 20}},
           "liabilities": {"shortTermLiabilities": {"total": 50}}}
    data = build_financial_data(_snapshot(row), RICH)
    assert data.availability == "PARTIAL"
    calculations = {item["id"]: item for item in data.sections["calculations"].value}
    assert calculations["current_ratio"]["value"] == 2


def _balance_row(year, proceeds=100):
    row = _row(year, revenue=proceeds, profit=20, capital=80, payables=15)
    row["assets"] = {"totalAssets": 200, "currentAssets": {"total": 100, "receivables": 40,
                      "bankroll": 20, "stocks": 30}, "uncurrentAssets": {"total": 100, "fixedAssets": 90}}
    row["liabilities"].update({"totalLiabilities": 200, "longTermDuties": {"total": 70, "others": 5}})
    row["liabilities"]["shortTermLiabilities"].update({"total": 50, "borrowedFunds": 35})
    return row


def test_finance_new_balance_fields_and_formula_exact_provenance():
    snapshot = _snapshot(_balance_row(2025, 150), _balance_row(2024, 100))
    data = build_financial_data(snapshot, RICH)
    last = data.facts["fin.series"].value[-1]
    assert {key: last[key] for key in ("stocks", "fixed_assets", "borrowed_funds", "long_term_others")} == {
        "stocks": 30, "fixed_assets": 90, "borrowed_funds": 35, "long_term_others": 5}
    calculations = {item["id"]: item for item in data.sections["calculations"].value}
    expected = {"working_capital": 50, "current_ratio": 2, "capital_to_assets_pct": 40,
                "cash_to_current_assets_pct": 20, "receivables_to_current_assets_pct": 40,
                "stocks_to_current_assets_pct": 30, "profit_to_proceeds_pct": 13.33,
                "payables_to_proceeds_pct": 10, "proceeds_change": 50, "proceeds_change_pct": 50}
    for name, value in expected.items():
        calc = calculations[name]
        assert calc["value"] == value
        assert calc["state"] == "data" and calc["formula"] and calc["version"]
        assert len(calc["input_refs"]) == 2
        assert set(calc["input_refs"]) <= data.sections["calculations"].inputs.keys()
    inputs = data.sections["calculations"].inputs
    assert [inputs[ref] for ref in calculations["working_capital"]["input_refs"]] == [
        {"field_ref": "report.finReports[0].assets.currentAssets.total", "value": 100, "year": 2025, "unit": "руб"},
        {"field_ref": "report.finReports[0].liabilities.shortTermLiabilities.total", "value": 50, "year": 2025, "unit": "руб"},
    ]
    assert inputs[calculations["proceeds_change"]["input_refs"][1]]["field_ref"] == "report.finReports[1].common.proceeds"


def test_finance_undefined_ratios_and_nonconsecutive_years_are_explicit():
    row = _balance_row(2025)
    row["liabilities"]["shortTermLiabilities"]["total"] = 0
    row["assets"]["currentAssets"].pop("bankroll")
    data = build_financial_data(_snapshot(_balance_row(2020), row), RICH)
    calculations = {item["id"]: item for item in data.sections["calculations"].value}
    for name, reason in (("current_ratio", "nonpositive_denominator"),
                         ("cash_to_current_assets_pct", "missing_or_invalid_input"),
                         ("proceeds_change_pct", "nonconsecutive_years")):
        assert calculations[name]["state"] == "not_calculable"
        assert calculations[name]["value"] is None
        assert calculations[name]["reason"] == reason


def test_finance_extreme_finite_inputs_do_not_emit_infinity():
    old = _balance_row(2024, proceeds=1e-300)
    latest = _balance_row(2025, proceeds=1e300)
    data = build_financial_data(_snapshot(old, latest), RICH)
    json.dumps(data.model_dump(mode="json"), allow_nan=False)
    calculations = {item["id"]: item for item in data.sections["calculations"].value}
    assert calculations["proceeds_change_pct"]["state"] == "not_calculable"
    assert calculations["proceeds_change_pct"]["value"] is None


@pytest.mark.asyncio
async def test_profile_content_and_source_date_conflict_reach_master(documents, monkeypatch):
    source = next(doc for doc in documents if doc["report"]["baseInfo"].get("website"))
    snapshot = _real_snapshot(copy.deepcopy(source))
    report = snapshot["document"]["report"]
    report["status"]["reasonName"] = "Сведения о причине из источника"
    snapshot["report_date"] = parse_date("2020-01-01")
    report["baseInfo"]["email"] = "private@example.invalid"
    report["baseInfo"]["secret_token"] = "never send this"
    inn = snapshot["inn"]
    _patch_snapshots(monkeypatch, {inn: snapshot})
    context = _context()
    result = await build_tool_registry(context.settings).execute(
        "get_financial_data", {"inn": inn, "section": "profile"}, context)
    assert result.status != "error", result.error
    normalized = normalized_tool_context(result)
    assert normalized["company"]["status_reason"] == report["status"]["reasonName"]
    dates = normalized["sections"]["source_dates"]["value"]
    assert dates["report_date"] != dates["snapshot_report_date"]
    assert normalized["sections"]["profile"]["value"]["website"] == report["baseInfo"]["website"]
    assert normalized["sections"]["positive"]["value"][0]["code"] == report["reputationalRisks"]["positive"][0]["code"]
    assert "private@example.invalid" not in json.dumps(normalized)
    assert "never send this" not in json.dumps(normalized)


def test_legal_both_roles_stages_and_page_scope():
    snapshot = _snapshot()
    report = snapshot["document"]["report"]
    report["arbitrationByStatus"] = {
        "plaintiffArbitration": {"plaintiffArbitrationFinished": {"pfCount": 2, "pfAmount": 400}},
        "defandantArbitration": {"defandantArbitrationPending": {"dpCount": 0, "dpAmount": 0}},
    }
    report["executionProceedings"] = [
        {"number": str(i), "active": i % 2 == 0, "amount": str(i * 10), "date": {"$date": "2025-01-%02dT00:00:00Z" % (i + 1)}}
        for i in range(12)
    ]
    original = copy.deepcopy(snapshot)
    first = build_legal_data(snapshot)
    second = build_legal_data(snapshot, offset=5)
    stages = {(row["role"], row["stage"]): row for row in first.sections["court_stages"].value}
    assert stages[("plaintiff", "Finished")]["count"] == 2
    assert stages[("plaintiff", "Finished")]["field_ref"].endswith("plaintiffArbitration.plaintiffArbitrationFinished")
    assert stages[("defendant", "Pending")]["count"] == 0
    assert stages[("defendant", "Pending")]["field_states"]["count"] == "data"
    assert stages[("defendant", "Finished")]["count"] is None
    page = first.sections["proceedings"]
    assert (page.total, page.included, page.next_offset, page.truncated) == (12, 5, 5, True)
    assert [row["source_index"] for row in page.value] == [11, 10, 9, 8, 7]
    assert [row["source_index"] for row in second.sections["proceedings"].value] == [6, 5, 4, 3, 2]
    assert "not outstanding balance" in page.scope
    assert snapshot == original


@pytest.mark.asyncio
async def test_comparison_common_period_preserves_individual_latest_series(monkeypatch):
    snapshots = {
        RICH: _snapshot(_balance_row(2024, 100), _balance_row(2025, 150)),
        OTHER: _snapshot(_balance_row(2024, 80)),
        EMPTY: _snapshot(_balance_row(2024, 70), _balance_row(2025, 90)),
    }
    _patch_snapshots(monkeypatch, snapshots)
    context = _context()
    result = await build_tool_registry(context.settings).execute(
        "compare_companies", {"inns": list(snapshots), "focus": "finance"}, context)
    assert result.status != "error", result.error
    normalized = normalized_tool_context(result)
    for company in normalized["companies"]:
        assert company["comparison_periods"]["proceeds"] == 2024
        revenue = next(row for row in company["metrics"] if ":fin.proceeds." in row["id"])
        assert revenue["id"].endswith(".2024")
        expected_latest = 2024 if company["inn"] == OTHER else 2025
        assert company["sections"]["finance_series"]["value"][-1]["year"] == expected_latest


@pytest.mark.asyncio
async def test_common_period_search_is_not_limited_to_displayed_five_years(monkeypatch):
    snapshots = {
        RICH: _snapshot(*[_balance_row(year) for year in range(2018, 2026)]),
        OTHER: _snapshot(_balance_row(2018)),
    }
    _patch_snapshots(monkeypatch, snapshots)
    context = _context()
    result = await build_tool_registry(context.settings).execute(
        "compare_companies", {"inns": list(snapshots), "focus": "finance"}, context)
    assert result.status != "error", result.error
    assert all(company["comparison_periods"]["proceeds"] == 2018 for company in result.data["companies"])


@pytest.mark.asyncio
async def test_real_full_check_pipeline_delivers_same_source_sections(documents, monkeypatch):
    document = next(doc for doc in documents if doc["report"]["baseInfo"]["inn"] == RICH)
    snapshot = _real_snapshot(document)
    _patch_snapshots(monkeypatch, {RICH: snapshot})
    context = _context()
    registry = build_tool_registry(context.settings)
    result = await registry.execute("full_company_check", {"inn": RICH}, context)
    assert result.status != "error", result.error
    normalized = normalized_tool_context(result)
    finance = build_financial_data(snapshot, RICH)
    assert normalized["series"][0]["value"] == finance.facts["fin.series"].value
    assert normalized["company"]["snapshot_id"] == snapshot["snapshot_id"]
    assert "positive" in normalized["sections"]
    assert "claim_scale" in normalized["sections"]
    assert "court_stages" in normalized["sections"]
    assert "_agent_snapshot" not in result.model_dump_json()
    json.dumps(normalized, allow_nan=False)


@pytest.mark.asyncio
async def test_full_snapshot_seeds_finance_and_legal_without_another_domain_call(documents, monkeypatch):
    from test_agent_runtime import _runtime
    snapshot = _real_snapshot(next(d for d in documents if d['report']['baseInfo']['inn'] == INN))
    _patch_snapshots(monkeypatch, {INN: snapshot})
    runtime = _runtime(None)
    first = await runtime.run('Проверь контрагента ' + INN)
    second = await runtime.run('Что с оборотными активами?', first.conversation_id)
    third = await runtime.run('А что с судами?', first.conversation_id)
    assert first.metadata.tool_calls == 1
    assert second.metadata.tool_calls == third.metadata.tool_calls == 0


@pytest.mark.asyncio
async def test_named_section_and_page_reach_existing_tool_without_full_check(documents, monkeypatch):
    from test_agent_runtime import _runtime
    snapshot = _real_snapshot(next(d for d in documents if d['report']['baseInfo']['inn'] == INN))
    _patch_snapshots(monkeypatch, {INN: snapshot})
    runtime = _runtime(None)
    seen = []
    execute = runtime.registry.execute
    async def record(name, args, context):
        seen.append((name, args))
        return await execute(name, args, context)
    monkeypatch.setattr(runtime.registry, 'execute', record)
    await runtime.run('Покажи лицензии ' + INN + ', страница 2')
    assert seen == [('get_legal_data', {'inn': INN, 'section': 'licenses', 'offset': 5})]


def test_new_snapshot_invalidates_other_cached_domains():
    from app.agent.conversations import merge_trusted_context
    base = dict(schema_version='verified-context-1', domain='finance', company=dict(inn=INN,snapshot_id='1'))
    first = merge_trusted_context(None, base)
    updated = merge_trusted_context(first, {**base,'domain':'legal','company':{'inn':INN,'snapshot_id':'2'}})
    assert set(updated['domains']) == {'legal'}


def test_parent_provenance_uses_real_nested_path():
    from app.agent.data_sections import profile_sections
    related = [{'inn': str(n), 'parentOrganizations': [{'inn':'7707083893','fullName':'Parent'}]} for n in range(9)]
    sections = profile_sections({'document':{'report':{'relatedCompanies':related}}}, 'connections', 5)
    rows = sections['connection_parents'].value
    assert len(rows) == 4
    assert rows[-1]['field_ref'] == 'report.relatedCompanies[8].parentOrganizations[0]'
    assert rows[-1]['related_company_inn'] == '8'


@pytest.mark.asyncio
async def test_historical_projection_cannot_answer_generic_finance_as_latest(documents, monkeypatch):
    from test_agent_runtime import _runtime
    snapshot = _real_snapshot(next(d for d in documents if d['report']['baseInfo']['inn'] == INN))
    _patch_snapshots(monkeypatch, {INN:snapshot})
    runtime = _runtime(None)
    historical = await runtime.run('Финансы ' + INN + ' за 2024')
    latest = await runtime.run('Покажи финансы', historical.conversation_id)
    assert latest.metadata.tool_calls == 1
    state = await runtime.conversation_store.checkpointer.aget_tuple({'configurable':{'thread_id':latest.conversation_id}})
    finance = state.checkpoint['channel_values']['trusted_context']['domains']['finance']
    assert finance['series'][0]['value'][-1]['year'] == 2025
