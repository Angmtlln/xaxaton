"""ToolResult → AssistantResponse: данные UI только из проверенных facts."""
import copy
import time

import pytest
from pydantic import ValidationError

from app.agent.models import AssistantResponse
from app.agent.response import tool_result_to_assistant
from app.agent.tools import ToolContext, build_tool_registry
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


def _assistant(result):
    return tool_result_to_assistant(
        result,
        agent_run_id="agent-test",
        routing="deterministic_fallback",
        model=None,
        started=time.perf_counter(),
    )


@pytest.mark.asyncio
async def test_response_hydrates_metrics_chart_and_evidence_from_tool(
    monkeypatch, check_payload
):
    result = await _tool_result(monkeypatch, check_payload)
    response = _assistant(result)
    facts = {
        fact["id"]: fact
        for block in check_payload["blocks"]
        for fact in block["facts"]
    }

    assert result.status == "success"
    assert [block.type for block in response.blocks] == [
        "company_card", "text", "metric_grid", "line_chart", "finding_list", "evidence_list"
    ]
    metric_block = next(block for block in response.blocks if block.type == "metric_grid")
    for item in metric_block.items:
        if item.id in facts:
            assert item.value == facts[item.id]["value"]

    chart = next(block for block in response.blocks if block.type == "line_chart")
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
    text_block = next(block for block in response.blocks if block.type == "text")

    assert "999999" not in text_block.text
    assert "<svg" not in text_block.text
    assert "детерминированные стоп-факторы" in text_block.text


@pytest.mark.asyncio
async def test_partial_and_no_data_are_explicit_states(monkeypatch, check_payload):
    partial_payload = copy.deepcopy(check_payload)
    partial_payload["status"] = "PARTIAL"
    partial = _assistant(await _tool_result(monkeypatch, partial_payload))
    assert partial.metadata.status == "partial"
    assert "Результат частичный" in next(
        block.text for block in partial.blocks if block.type == "text"
    )

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
    unknown_block["blocks"][0] = {"type": "raw_html", "html": "<b>unsafe</b>"}
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unknown_block)

    unsafe_text = copy.deepcopy(payload)
    unsafe_text["message"] = "<script>alert(1)</script>"
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unsafe_text)

    fake_evidence = copy.deepcopy(payload)
    finding = next(block for block in fake_evidence["blocks"] if block["type"] == "finding_list")
    finding["items"][0]["evidence_ids"] = ["fact.does.not.exist"]
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(fake_evidence)

    unsafe_report_link = copy.deepcopy(payload)
    company = next(
        block for block in unsafe_report_link["blocks"] if block["type"] == "company_card"
    )
    company["report_url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unsafe_report_link)
