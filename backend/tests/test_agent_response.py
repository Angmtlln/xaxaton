"""ToolResult → AssistantResponse: данные UI только из проверенных facts."""
import copy
import time

import pytest
from pydantic import ValidationError

from app.agent.models import AssistantResponse
from app.agent.response import tool_result_to_assistant
from app.agent.tools import ToolContext, build_tool_registry
from app.agent.synthesis import observation_findings
from app.config import Settings
from app.llm.groq_client import GroqClient


def _settings():
    return Settings(
        llm_mock=True,
        groq_api_key=None,
        database_url="postgresql://localhost/none",
    )


async def _tool_result(monkeypatch, payload):
    async def fake_run_check(*args, **kwargs):
        return payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    settings = _settings()
    registry = build_tool_registry(settings)
    return await registry.execute(
        "full_company_check",
        {"inn": payload["inn"]},
        ToolContext(settings=settings, client=GroqClient(settings), persist=False),
    )


def _assistant(result, synthesis=None):
    return tool_result_to_assistant(
        result,
        agent_run_id="agent-test",
        routing="deterministic_fallback",
        model=None,
        started=time.perf_counter(),
        synthesis=synthesis,
    )


@pytest.mark.asyncio
async def test_response_hydrates_metrics_chart_and_evidence_from_tool(
    monkeypatch, check_payload
):
    result = await _tool_result(monkeypatch, check_payload)
    selected = [observation_findings(result)[0]["id"]]
    response = _assistant(result, {"finding_ids": selected, "artifact": "metrics"})
    facts = {
        fact["id"]: fact
        for block in check_payload["blocks"]
        for fact in block["facts"]
    }

    assert result.status == "success"
    assert response.leading_artifact.type == "company_summary"
    assert response.leading_artifact.inn == check_payload["inn"]
    assert [block.type for block in response.blocks] == ["metric_grid"]
    metric_block = next(block for block in response.blocks if block.type == "metric_grid")
    for item in metric_block.items:
        if item.id in facts:
            assert item.value == facts[item.id]["value"]

    chart_response = _assistant(result, {"finding_ids": selected, "artifact": "chart"})
    chart = next(block for block in chart_response.blocks if block.type == "line_chart")
    source_rows = facts["fin.series"]["value"]
    revenue = next(series for series in chart.series if series.key == "proceeds")
    assert [point.value for point in revenue.points] == [row["proceeds"] for row in source_rows]

    known_evidence = {item.id for item in response.evidence}
    assert known_evidence
    for block in response.blocks:
        if block.type == "evidence_list":
            assert set(block.evidence_ids) <= known_evidence


@pytest.mark.asyncio
async def test_model_numbers_and_markup_do_not_enter_text_block(monkeypatch, check_payload):
    payload = copy.deepcopy(check_payload)
    payload["summary"]["headline"] = "Выручка 999999 рублей <svg onload=alert(1)>"
    payload["summary"]["narrative_points"] = [
        "Компания заработала 999999 рублей.",
        "Проверка завершена без дополнительных числовых утверждений.",
    ]

    response = _assistant(await _tool_result(monkeypatch, payload))

    assert "999999" not in response.message
    assert "<svg" not in response.message
    assert "стоп-факторы" in response.message
    assert "детерминированные" not in response.message


@pytest.mark.asyncio
async def test_partial_and_no_data_are_explicit_states(monkeypatch, check_payload):
    partial_payload = copy.deepcopy(check_payload)
    partial_payload["status"] = "PARTIAL"
    partial = _assistant(await _tool_result(monkeypatch, partial_payload))
    assert partial.metadata.status == "partial"
    assert "Результат частичный" in partial.message

    no_data_payload = copy.deepcopy(check_payload)
    no_data_payload["summary"]["verdict_group"] = "NO_DATA"
    no_data = _assistant(await _tool_result(monkeypatch, no_data_payload))
    assert "невозможно" in no_data.message
    assert "рисков нет" not in no_data.message.lower()


@pytest.mark.asyncio
async def test_assistant_contract_rejects_unknown_blocks_html_and_fake_evidence(
    monkeypatch, check_payload
):
    response = _assistant(await _tool_result(monkeypatch, check_payload))
    payload = response.model_dump(mode="json")

    unknown_block = copy.deepcopy(payload)
    unknown_block["blocks"] = [{"type": "raw_html", "html": "<b>unsafe</b>"}]
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unknown_block)

    unsafe_text = copy.deepcopy(payload)
    unsafe_text["message"] = "<script>alert(1)</script>"
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unsafe_text)

    fake_evidence = copy.deepcopy(payload)
    fake_evidence["leading_artifact"]["evidence_ids"] = ["fact.does.not.exist"]
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(fake_evidence)

    unsafe_report_link = copy.deepcopy(payload)
    company = unsafe_report_link["leading_artifact"]
    company["report_url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unsafe_report_link)


@pytest.mark.asyncio
@pytest.mark.parametrize("synthesis", [
    "not json", {"finding_ids": ["forged"]}, {"finding_ids": []},
    {"finding_ids": [], "message": "Рисков нет, выручка 999999999"},
    {"finding_ids": [], "leading_artifact": {"name": "Выдуманная"}},
    {"finding_ids": [], "artifact": "raw_html"},
])
async def test_full_check_malformed_synthesis_keeps_summary_guards_and_facts(monkeypatch, check_payload, synthesis):
    result = await _tool_result(monkeypatch, check_payload)
    baseline = _assistant(result)
    response = _assistant(result, synthesis)
    assert response.metadata.synthesis == "fallback"
    assert response.leading_artifact == baseline.leading_artifact
    assert response.message == baseline.message
    assert response.evidence == baseline.evidence
    assert response.blocks == []
    assert "стоп-факторы" in response.message


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("display_value", "999999999"), ("field_ref", "report.other"),
    ("fact_id", "unrelated"), ("source", "source_signal"),
])
async def test_full_check_rejects_mismatched_evidence(monkeypatch, check_payload, field, value):
    result = await _tool_result(monkeypatch, check_payload)
    result.evidence[0] = result.evidence[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match="does not match"):
        _assistant(result)


@pytest.mark.asyncio
async def test_full_summary_fields_come_from_facts_not_legacy_company(monkeypatch, check_payload):
    result = await _tool_result(monkeypatch, check_payload)
    original = _assistant(result).leading_artifact
    result.data["company"].update(short_name="Выдуманное название", status="НЕИЗВЕСТНО", risk_level="FAKE")
    assert _assistant(result).leading_artifact == original


@pytest.mark.asyncio
@pytest.mark.parametrize("fact_id", ["company.name", "company.status", "bank.risk_level"])
async def test_source_markup_in_summary_is_sanitized(monkeypatch, check_payload, fact_id):
    for block in check_payload["blocks"]:
        for fact in block["facts"]:
            if fact["id"] == fact_id:
                fact["value"] = "<script>alert(1)</script>Данные источника"
    response = _assistant(await _tool_result(monkeypatch, check_payload))
    assert response.leading_artifact is not None
    assert "<script>" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_full_model_selection_orders_explanations_and_cannot_hide_required_facts(monkeypatch, check_payload):
    result = await _tool_result(monkeypatch, check_payload)
    findings = observation_findings(result)
    last = findings[-1]
    response = _assistant(result, {"finding_ids": [last["id"]], "artifact": "none"})
    assert response.metadata.synthesis == "model"
    assert response.message.split("\n\n")[1] == last["text"]
    for finding in findings:
        if finding["required"]:
            assert finding["text"] in response.message
    assert response.leading_artifact.type == "company_summary"
    assert response.blocks == []
