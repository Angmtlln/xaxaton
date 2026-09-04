"""Targeted response keeps Master prose free and artifacts backend-owned."""
import time

import pytest

from app.agent.finance import build_financial_data
from app.agent.models import MasterAnswer, ToolResult, ToolResultMetadata
from app.agent.response import tool_result_to_assistant
from app.agent.synthesis import normalized_tool_context
from app.agent.tools import _evidence_from_fact


@pytest.fixture
def result():
    data = build_financial_data({"document": {"report": {"finReports": [
        {"common": {"year": 2024, "proceeds": 100, "profit": 10}, "liabilities": {"capitals": 50}},
        {"common": {"year": 2025, "proceeds": 50, "profit": -10}, "liabilities": {"capitals": -20}},
    ]}}}, "6165169320")
    return ToolResult(
        status="partial",
        data=data.model_dump(mode="json"),
        evidence=[_evidence_from_fact(fact) for fact in data.facts.values()],
        metadata=ToolResultMetadata(tool="get_financial_data", latency_ms=0),
    )


def render(result, answer=None, *, contextual=False):
    return tool_result_to_assistant(
        None if contextual else result,
        trusted_context=normalized_tool_context(result),
        master_answer=answer,
        agent_run_id="test",
        routing="model" if answer else "deterministic_fallback",
        model="fake",
        started=time.perf_counter(),
        contextual=contextual,
        grounding_status="verified" if answer else "fallback",
    )


def test_natural_grounded_interpretation_is_preserved_verbatim(result):
    message = (
        "Прибыль ушла в минус одновременно со снижением выручки. Это не готовый "
        "приговор, но при отсрочке я бы уточнил источник будущего платежа."
    )
    response = render(result, MasterAnswer(message=message))
    assert response.message == message
    assert response.metadata.synthesis == "model"
    assert response.leading_artifact is None
    assert response.blocks == []


@pytest.mark.parametrize("artifact,expected", [
    ("none", []), ("metrics", ["metric_grid"]), ("chart", ["line_chart"]),
])
def test_targeted_reply_hydrates_at_most_one_backend_artifact(result, artifact, expected):
    response = render(result, MasterAnswer(message="Разберём проверенную финансовую динамику.", artifact=artifact))
    assert response.leading_artifact is None
    assert [block.type for block in response.blocks] == expected
    assert response.evidence
    if artifact == "metrics":
        values = {item.id: item.value for item in response.blocks[0].items}
        assert values["fin.profit.2025"] == -10
        assert values["fin.capitals.2025"] == -20
    if artifact == "chart":
        profit = next(series for series in response.blocks[0].series if series.key == "profit")
        assert [point.value for point in profit.points] == [10, -10]


def test_contextual_reply_has_no_repeated_artifact_or_tool_owned_company_card(result):
    response = render(
        result,
        MasterAnswer(message="Проще: денег от основной работы стало меньше, а итог года отрицательный."),
        contextual=True,
    )
    assert response.blocks == []
    assert response.leading_artifact is None
    assert response.metadata.tool_calls == 0
    assert response.evidence


@pytest.mark.parametrize("field,value", [
    ("display_value", "999999999"), ("field_ref", "report.other"),
    ("fact_id", "fin.profit.2025"), ("source", "source_signal"),
])
def test_existing_evidence_id_is_not_enough_to_prove_fact(result, field, value):
    result.evidence[0] = result.evidence[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match="does not match"):
        normalized_tool_context(result)


def test_missing_evidence_is_rejected(result):
    result.evidence = []
    with pytest.raises(ValueError, match="lack verified evidence"):
        normalized_tool_context(result)


def test_deterministic_fallback_uses_verified_values_only(result):
    response = render(result)
    assert response.metadata.synthesis == "fallback"
    assert "Подтверждённые данные" in response.message
    assert "Выручка за 2025 год" in response.message
    assert "50 ₽" in response.message
