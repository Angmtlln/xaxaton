"""Source-to-context regressions from AGENT_EVALS K19/K21/S15_10/S15_12/S15_18."""
import copy
import json

import pytest

from app.agent.conversations import merge_trusted_context
from app.agent.data_sections import claim_scale
from app.agent.finance import build_financial_data
from app.agent.legal import build_legal_data
from app.agent.synthesis import normalized_tool_context
from app.agent.tools import build_tool_registry
from app.infrastructure.mongo import parse_date
from test_data_coverage import _context, _real_snapshot, _patch_snapshots

INN = "6165169320"


def _source(documents, inn=INN):
    return _real_snapshot(copy.deepcopy(next(d for d in documents if d["report"]["baseInfo"]["inn"] == inn)))


def _scale(snapshot):
    return claim_scale(build_financial_data(snapshot, snapshot["inn"]), build_legal_data(snapshot))


def test_pending_and_all_claims_use_distinct_source_amounts(documents):
    section = _scale(_source(documents))
    rows = {r["id"]: r for r in section.value}
    total = rows["court.defendant_amount_to_capitals_pct"]
    pending = rows["court.defendant_pending_amount_to_capitals_pct"]
    assert total["value"] == 551.12
    assert pending["value"] == 499.59
    a, b = (section.inputs[r["input_refs"][0]] for r in (total, pending))
    assert a["value"] == 20843367 and b["value"] == 18894669
    assert a["stage"] == "all_disclosed_years" and b["stage"] == "Pending"
    assert a["role"] == b["role"] == "defendant"
    assert b["field_ref"].endswith("defandantArbitrationPending.dpAmount")
    assert "year" not in a and "year" not in b
    assert section.inputs[pending["input_refs"][1]]["year"] == 2025


@pytest.mark.parametrize("amount,expected", [(None, None), (-1, None), ("NaN", None), (0, 0)])
def test_unknown_pending_amount_is_not_reconstructed_from_year_totals(documents, amount, expected):
    snapshot = _source(documents)
    snapshot["document"]["report"]["arbitrationByStatus"]["defandantArbitration"]["defandantArbitrationPending"]["dpAmount"] = amount
    rows = {r["id"]: r for r in _scale(snapshot).value}
    assert rows["court.defendant_amount_to_capitals_pct"]["value"] == 551.12
    pending = rows["court.defendant_pending_amount_to_capitals_pct"]
    assert pending["value"] == expected
    assert pending["state"] == ("not_calculable" if expected is None else "data")
    json.dumps(rows, allow_nan=False)


def test_pending_ratio_preserves_zero_revenue_boundary(documents):
    snapshot = _source(documents)
    snapshot["document"]["report"]["finReports"][0]["common"]["proceeds"] = 0
    section = _scale(snapshot)
    ratio = next(r for r in section.value if r["id"] == "court.defendant_pending_amount_to_proceeds_pct")
    assert ratio["value"] is None
    assert ratio["reason"] == "nonpositive_denominator"


@pytest.mark.asyncio
@pytest.mark.parametrize("inn", [INN, "7813664770"])
async def test_full_check_exposes_finance_commentary_and_founders_in_followups(documents, monkeypatch, inn):
    snapshot = _source(documents, inn)
    _patch_snapshots(monkeypatch, {inn: snapshot})
    ctx = _context()
    try:
        result = await build_tool_registry(ctx.settings).execute("full_company_check", {"inn": inn}, ctx)
    finally:
        await ctx.client.aclose()
    assert result.status != "error", result.error
    context = normalized_tool_context(result)
    store = merge_trusted_context(None, context)
    raw = snapshot["document"]["report"]
    for view in (context, store["domains"]["finance"]):
        founders = view["sections"]["cofounders"]
        original = raw["foundersInfo"]["cofounders"][0]
        assert {k: v for k, v in founders["value"][0].items() if k != "dateFrom"} == {k: v for k, v in original.items() if k != "dateFrom"}
        assert founders["value"][0]["dateFrom"][:10] == parse_date(original["dateFrom"]).isoformat()
        assert founders["field_ref"] == "report.foundersInfo.cofounders"
        prose = view["sections"]["finance_source_commentary"]
        expected = next(i for i, r in enumerate(raw["reputationalRisks"]["positive"]) if r["code"] == "proceeds")
        row = next(r for r in prose["value"] if r["code"] == "proceeds")
        assert row["name"] == raw["reputationalRisks"]["positive"][expected]["name"]
        assert row["field_ref"] == "report.reputationalRisks.positive[%s]" % expected
        assert expected >= 5  # Previously absent from the first page of source markers.
    assert store["domains"]["legal"]["sections"]["claim_scale"] == context["sections"]["claim_scale"]
    definitions = store["domains"]["finance"]["sections"]["finance_scope"]["value"]
    assert definitions["paths"]["total_liabilities"] == "liabilities.totalLiabilities"
    assert definitions["total_liabilities_definition"]
    assert definitions["capitals_definition"]


def test_cofounders_are_paginated_without_changing_share_capital(documents):
    snapshot = _source(documents)
    raw = snapshot["document"]["report"]["foundersInfo"]
    raw["cofounders"] = [dict(raw["cofounders"][0], name="Источник %s" % n) for n in range(7)]
    first = build_legal_data(snapshot, section="connections")
    second = build_legal_data(snapshot, section="connections", offset=5)
    assert [r["name"] for r in first.sections["cofounders"].value] == ["Источник %s" % n for n in range(5)]
    assert first.sections["cofounders"].next_offset == 5
    assert [r["name"] for r in second.sections["cofounders"].value] == ["Источник 5", "Источник 6"]
    assert first.sections["founders"].value == {"shareCapital": raw["shareCapital"]}
    assert first.sections["available_sections"].value["cofounders"]["section"] == "connections"
