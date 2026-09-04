"""HTTP-контракт нового chat flow и регрессия legacy checks API."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.llm.groq_client import GroqClient
from app.main import app, groq_dep, settings_dep


@pytest.fixture
def api_client():
    settings = Settings(
        llm_mock=True,
        groq_api_key=None,
        database_url="postgresql://localhost/none",
    )
    app.dependency_overrides[settings_dep] = lambda: settings
    app.dependency_overrides[groq_dep] = lambda: GroqClient(settings)
    client = TestClient(app)
    try:
        yield client
    finally:
        client.close()
        app.dependency_overrides.clear()


def test_chat_api_runs_complete_vertical_slice_once(
    api_client, monkeypatch, check_payload
):
    calls = []

    async def fake_run_check(inn, settings, client, persist):
        calls.append((inn, persist))
        return check_payload

    monkeypatch.setattr("app.agent.tools.run_check", fake_run_check)
    response = api_client.post(
        "/api/v1/chat/messages",
        json={"message": "Проверь контрагента 6165169320"},
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == [("6165169320", True)]
    assert body["metadata"]["tool_calls"] == 1
    assert body["metadata"]["routing"] == "deterministic_fallback"
    assert [block["type"] for block in body["blocks"]] == [
        "company_card", "text", "metric_grid", "line_chart", "finding_list", "evidence_list"
    ]


@pytest.mark.parametrize(
    "message",
    [
        "Проверь контрагента",
        "Проверь контрагента 1234567890",
        "Какая выручка у 6165169320?",
    ],
)
def test_chat_api_does_not_run_check_without_valid_broad_request(
    api_client, monkeypatch, message
):
    async def forbidden(*args, **kwargs):
        raise AssertionError("run_check не должен вызываться")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden)
    response = api_client.post("/api/v1/chat/messages", json={"message": message})

    assert response.status_code == 200
    assert response.json()["metadata"]["tool_calls"] == 0
    assert response.json()["metadata"]["status"] == "needs_input"


def test_chat_request_rejects_unknown_fields(api_client):
    response = api_client.post(
        "/api/v1/chat/messages",
        json={"message": "Проверь контрагента 6165169320", "html": "<b>нет</b>"},
    )
    assert response.status_code == 422


def test_legacy_checks_api_contract_is_unchanged(api_client, monkeypatch, check_payload):
    calls = []

    async def fake_run_check(inn, settings, client, persist):
        calls.append((inn, persist))
        return check_payload

    monkeypatch.setattr("app.main.run_check", fake_run_check)
    response = api_client.post(
        "/api/v1/checks",
        json={"inn": "6165169320", "persist": False},
    )

    assert response.status_code == 200
    body = response.json()
    assert calls == [("6165169320", False)]
    assert body["inn"] == "6165169320"
    assert body["status"] == "SUCCEEDED"
    assert len(body["blocks"]) == 4
    assert "summary" in body and "grounding" in body and "llm" in body


def test_legacy_report_and_landing_routes_are_unchanged(api_client):
    landing = api_client.get("/")
    report = api_client.get("/report?inn=6165169320")

    assert landing.status_code == 200
    assert "AI-аналитик" in landing.text
    assert report.status_code == 200
    assert "Отчёт" in report.text
