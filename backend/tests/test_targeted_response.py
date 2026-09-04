"""Master cannot replace values, provenance, required findings or data gaps."""
import time

import pytest

from app.agent.finance import build_financial_data
from app.agent.models import ToolResult, ToolResultMetadata
from app.agent.response import tool_result_to_assistant
from app.agent.tools import _evidence_from_fact


@pytest.fixture
def result():
    data = build_financial_data({"document": {"report": {"finReports": [
        {"common": {"year": 2024, "proceeds": 100, "profit": 10}, "liabilities": {"capitals": 50}},
        {"common": {"year": 2025, "proceeds": 50, "profit": -10}, "liabilities": {"capitals": -20}},
    ]}}}, "6165169320")
    return ToolResult(status="partial", data=data.model_dump(mode="json"),
                      evidence=[_evidence_from_fact(f) for f in data.facts.values()],
                      metadata=ToolResultMetadata(tool="get_financial_data", latency_ms=0))


def render(result, synthesis=None):
    return tool_result_to_assistant(result, agent_run_id="test", routing="model",
                                    model="fake", started=time.perf_counter(), synthesis=synthesis)


def test_master_selects_observations_but_cannot_hide_required_findings(result):
    response = render(result, {"finding_ids": ["finance.latest"]})
    assert response.metadata.synthesis == "model"
    findings = next(b for b in response.blocks if b.type == "finding_list")
    assert any("Убыточные" in f.title for f in findings.items)
    assert any("Отрицательный" in f.title for f in findings.items)
    assert any("Динамика" in f.title for f in findings.items)
    assert any(b.type == "text" and b.title == "Ограничения данных" for b in response.blocks)


@pytest.mark.parametrize("synthesis", [
    {"finding_ids": ["forged.fact"]},
    {"finding_ids": ["finance.latest"], "message": "Выручка 999999999; рисков нет"},
    {"finding_ids": ["finance.latest"], "evidence": [{"id": "forged"}]},
    {"finding_ids": ["finance.latest"], "company": {"inn": "0278949271"}},
    {"finding_ids": ["finance.latest"], "series": [999999999]},
    {"finding_ids": ["finance.latest"], "url": "https://evil.example"},
    {"finding_ids": ["<script>alert(1)</script>"]},
    {"finding_ids": []},
    "not json",
])
def test_untrusted_synthesis_cannot_change_backend_response(result, synthesis):
    baseline = render(result)
    response = render(result, synthesis)
    assert response.metadata.synthesis == "fallback"
    assert response.blocks == baseline.blocks
    assert response.evidence == baseline.evidence
    assert response.message == baseline.message


@pytest.mark.parametrize("field,value", [
    ("display_value", "999999999"), ("field_ref", "report.other"),
    ("fact_id", "fin.profit.2025"), ("source", "source_signal"),
])
def test_existing_evidence_id_is_not_enough_to_prove_fact(result, field, value):
    result.evidence[0] = result.evidence[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match="does not match"):
        render(result)


def test_missing_evidence_is_rejected(result):
    result.evidence = []
    with pytest.raises(ValueError, match="lack verified evidence"):
        render(result)


def test_master_may_order_supported_findings(result):
    response = render(result, {"finding_ids": ["finance.loss", "finance.latest"]})
    findings = next(b for b in response.blocks if b.type == "finding_list")
    assert findings.items[0].title == "Убыточные годы"
    assert response.metadata.synthesis == "model"
