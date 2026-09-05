"""Full-check ToolResult → AssistantResponse keeps prose and UI ownership separate."""
import copy
import time

import pytest
from pydantic import ValidationError

from app.agent.models import AssistantResponse, MasterAnswer
from app.agent.response import tool_result_to_assistant
from app.agent.synthesis import normalized_tool_context, parse_master_answer
from app.agent.tools import ToolContext, build_tool_registry
from app.config import Settings
from app.llm.groq_client import GroqClient


def _settings():
    return Settings(_env_file=None, llm_mock=True, groq_api_key=None,
                    database_url="postgresql://localhost/none")


async def _tool_result(monkeypatch, payload):
    async def fake_run_check(*args, **kwargs):
        return payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    settings = _settings()
    return await build_tool_registry(settings).execute(
        "full_company_check",
        {"inn": payload["inn"]},
        ToolContext(settings=settings, client=GroqClient(settings), persist=False),
    )


def _assistant(result, answer=None):
    return tool_result_to_assistant(
        result,
        trusted_context=normalized_tool_context(result),
        master_answer=answer,
        agent_run_id="agent-test",
        routing="model" if answer else "deterministic_fallback",
        model="fake" if answer else None,
        started=time.perf_counter(),
        grounding_status="verified" if answer else "fallback",
    )


@pytest.mark.asyncio
async def test_response_hydrates_metrics_chart_and_evidence_from_tool(monkeypatch, check_payload):
    result = await _tool_result(monkeypatch, check_payload)
    facts = {fact["id"]: fact for block in check_payload["blocks"] for fact in block["facts"]}
    response = _assistant(result, MasterAnswer(message="Сопоставил ключевые проверенные показатели.", artifact="metrics"))

    assert result.status == "success"
    assert response.leading_artifact.type == "company_summary"
    assert response.leading_artifact.inn == check_payload["inn"]
    assert [block.type for block in response.blocks] == ["finding_list", "metric_grid"]
    metric_block = next(block for block in response.blocks if block.type == "metric_grid")
    for item in metric_block.items:
        if item.id in facts:
            assert item.value == facts[item.id]["value"]

    chart_response = _assistant(result, MasterAnswer(message="На графике видна проверенная динамика.", artifact="chart"))
    chart = next(block for block in chart_response.blocks if block.type == "line_chart")
    source_rows = facts["fin.series"]["value"]
    revenue = next(series for series in chart.series if series.key == "proceeds")
    assert [point.value for point in revenue.points] == [row["proceeds"] for row in source_rows]
    assert {item.id for item in response.evidence}


@pytest.mark.asyncio
async def test_legacy_summary_prose_is_not_master_context_or_response(monkeypatch, check_payload):
    payload = copy.deepcopy(check_payload)
    payload["summary"]["headline"] = "Выручка 999999 рублей <svg onload=alert(1)>"
    payload["summary"]["narrative_points"] = ["Компания якобы безупречна 999999 раз."]
    result = await _tool_result(monkeypatch, payload)
    context = normalized_tool_context(result)
    response = _assistant(result)

    assert "summary" not in context
    assert "999999" not in response.message
    assert "<svg" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_partial_and_no_data_are_explicit_structured_states(monkeypatch, check_payload):
    partial_payload = copy.deepcopy(check_payload)
    partial_payload["status"] = "PARTIAL"
    partial = _assistant(await _tool_result(monkeypatch, partial_payload))
    assert partial.metadata.status == "partial"

    no_data_payload = copy.deepcopy(check_payload)
    no_data_payload["coverage"].update(filled_blocks=0, empty_blocks=["Финансы", "Суды"])
    result = await _tool_result(monkeypatch, no_data_payload)
    no_data = _assistant(result)
    assert normalized_tool_context(result)["coverage"]["state"] == "NO_DATA"
    assert "рисков нет" not in no_data.message.lower()
    assert no_data.metadata.status == "partial"


@pytest.mark.asyncio
async def test_assistant_contract_rejects_unknown_blocks_html_and_fake_evidence(monkeypatch, check_payload):
    payload = _assistant(await _tool_result(monkeypatch, check_payload)).model_dump(mode="json")
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
    unsafe_report_link["leading_artifact"]["report_url"] = "javascript:alert(1)"
    with pytest.raises(ValidationError):
        AssistantResponse.model_validate(unsafe_report_link)


@pytest.mark.parametrize("proposal", [
    "",
    "   ",
    "<script>alert(1)</script>",
    {"message": "ok", "artifact": "raw_html"},
    {"message": "ok", "url": "https://invented.test"},
    {"message": "<script>alert(1)</script>"},
])
def test_master_contract_rejects_non_answer_fields_and_unsafe_markup(proposal):
    with pytest.raises((ValidationError, ValueError)):
        parse_master_answer(proposal, allowed_artifacts=("none", "metrics", "chart"))


@pytest.mark.parametrize("raw", [
    '{"message":"Данных по финансам нет.","artifact":"none"}',
    '```json\n{"message":"Данных по финансам нет.","artifact":"none"}\n```',
    '<think>Надо ответить кратко.</think>{"message":"Данных по финансам нет.","artifact":"none"}',
    'Вот ответ: {"message":"Данных по финансам нет.","artifact":"none"}',
])
def test_master_answer_survives_provider_specific_wrapping(raw):
    answer = parse_master_answer(raw, allowed_artifacts=("none", "metrics"))

    assert answer.message == "Данных по финансам нет."
    assert answer.artifact == "none"


def test_prose_answer_is_kept_instead_of_a_canned_fallback():
    """Часть моделей игнорирует JSON-контракт, но текст — это и есть ответ."""
    answer = parse_master_answer(
        "У компании нет финансовой отчётности, поэтому оценить устойчивость нельзя.",
        allowed_artifacts=("none", "metrics"),
    )

    assert answer.message.startswith("У компании нет финансовой отчётности")
    # Артефакт остаётся за бэкендом: модель его не выбирала.
    assert answer.artifact == "none"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("display_value", "999999999"), ("field_ref", "report.other"),
    ("fact_id", "unrelated"), ("source", "source_signal"),
])
async def test_full_check_rejects_mismatched_evidence(monkeypatch, check_payload, field, value):
    result = await _tool_result(monkeypatch, check_payload)
    result.evidence[0] = result.evidence[0].model_copy(update={field: value})
    with pytest.raises(ValueError, match="does not match"):
        normalized_tool_context(result)


@pytest.mark.asyncio
async def test_company_summary_fields_come_from_facts_not_legacy_company(monkeypatch, check_payload):
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
async def test_master_is_answer_author_and_policy_remains_backend_owned(monkeypatch, check_payload):
    result = await _tool_result(monkeypatch, check_payload)
    message = "Здесь важна не одна цифра, а сочетание денежных и юридических наблюдений."
    response = _assistant(result, MasterAnswer(message=message))
    assert response.message == message
    policy = next(block for block in response.blocks if block.type == "finding_list")
    assert any("Официальный стоп-сигнал" in item.text for item in policy.items)
    assert response.leading_artifact.type == "company_summary"
