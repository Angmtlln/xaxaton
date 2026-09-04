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


def render(result, synthesis=None, question=""):
    return tool_result_to_assistant(result, agent_run_id="test", routing="model",
                                    model="fake", started=time.perf_counter(), synthesis=synthesis, question=question)


def test_master_selects_observations_but_cannot_hide_required_findings(result):
    response = render(result, {"finding_ids": ["finance.latest"]})
    assert response.metadata.synthesis == "model"
    assert response.leading_artifact is None
    assert response.blocks == []
    assert "есть убыток" in response.message
    assert "капитал в последнем доступном году отрицателен" in response.message
    assert "Изменение выручки" in response.message
    assert "отсутствует в карточке" in response.message



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
    assert response.message.startswith("В доступной отчётности есть убыток")
    assert response.metadata.synthesis == "model"


@pytest.mark.parametrize("artifact,expected", [("none", []), ("metrics", ["metric_grid"]), ("chart", ["line_chart"]), ("findings", [])])
def test_targeted_reply_has_at_most_one_helpful_artifact_and_no_company_summary(result, artifact, expected):
    response = render(result, {"finding_ids": ["finance.latest"], "artifact": artifact})
    assert response.leading_artifact is None
    assert [block.type for block in response.blocks] == expected
    assert response.evidence
    assert "Убыточные" not in response.message  # no dashboard title, explanation is prose
    assert "есть убыток" in response.message


@pytest.mark.parametrize("artifact", ["none", "metrics", "chart"])
def test_profit_question_starts_with_missing_profit_even_when_revenue_exists(artifact):
    data = build_financial_data({"document": {"report": {"finReports": [
        {"common": {"year": 2025, "proceeds": 100}, "liabilities": {"capitals": 50}},
    ]}}}, "6165169320")
    result = ToolResult(status="partial", data=data.model_dump(mode="json"),
        evidence=[_evidence_from_fact(fact) for fact in data.facts.values()],
        metadata=ToolResultMetadata(tool="get_financial_data", latency_ms=0))
    response = render(result, {"finding_ids": ["finance.latest"], "artifact": artifact}, "А что с прибылью?")
    assert response.message.startswith("Прибыль за 2025 год: нет данных")
    assert "Выручка за 2025 год: 100" not in response.message
    assert "Оценить прибыль" in response.message
    assert response.blocks == []
    assert response.leading_artifact is None
    assert any(item.id == "fin.profit.2025" for item in response.evidence)


@pytest.mark.parametrize("artifact", ["metrics", "chart"])
def test_profit_question_focuses_artifact_and_preserves_adverse_findings(result, artifact):
    response = render(result, {"finding_ids": ["finance.latest"], "artifact": artifact}, "А что с прибылью?")
    assert response.message.startswith("Прибыль за 2025 год: -10 ₽")
    assert "есть убыток" in response.message
    assert "капитал в последнем доступном году отрицателен" in response.message
    assert "Изменение выручки" in response.message  # required signal cannot be hidden
    assert len(response.blocks) == 1
    if artifact == "metrics":
        assert [item.id for item in response.blocks[0].items] == ["fin.profit.2025"]
    else:
        assert [series.key for series in response.blocks[0].series] == ["profit"]


def test_combined_finance_question_keeps_both_topics(result):
    response = render(result, {"finding_ids": ["finance.latest"]}, "Что с выручкой и прибылью?")
    assert "Выручка за 2025 год" in response.message
    assert "Прибыль за 2025 год" in response.message


def test_combined_profit_and_payables_question_keeps_payables():
    data = build_financial_data({"document": {"report": {"finReports": [
        {"common": {"year": 2025, "proceeds": 100, "profit": 10},
         "liabilities": {"capitals": 50, "shortTermLiabilities": {"accountsPayable": 77}}},
    ]}}}, "6165169320")
    result = ToolResult(status="partial", data=data.model_dump(mode="json"),
        evidence=[_evidence_from_fact(fact) for fact in data.facts.values()],
        metadata=ToolResultMetadata(tool="get_financial_data", latency_ms=0))
    response = render(result, {"finding_ids": ["finance.latest"], "artifact": "metrics"},
                      "Что с прибылью и кредиторской задолженностью?")
    assert "Прибыль за 2025 год: 10 ₽" in response.message
    assert "Кредиторская задолженность за 2025 год: 77 ₽" in response.message
    metrics = {item.id: item.value for item in response.blocks[0].items}
    assert metrics["fin.profit.2025"] == 10
    assert metrics["fin.accounts_payable.2025"] == 77
