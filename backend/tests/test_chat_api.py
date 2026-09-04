"""HTTP-контракт нового chat flow и регрессия legacy checks API."""
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.agent.conversations import ConversationStore
from app.llm.groq_client import GroqClient
from app.main import app, groq_dep, settings_dep


@pytest.fixture
def api_client():
    app.state.conversation_store = ConversationStore()
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
        "Какие тендеры у 6165169320?",
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


def test_multi_turn_api_uses_active_company_and_only_targeted_tools(
    api_client, monkeypatch, check_payload, documents
):
    calls = []
    document = next(item for item in documents if item["report"]["baseInfo"]["inn"] == "6165169320")

    async def full_check(inn, *args, **kwargs):
        calls.append(("full", inn))
        return check_payload

    async def snapshot(inn):
        calls.append(("snapshot", inn))
        return {"inn": inn, "document": document, "short_name": "Демо"}

    monkeypatch.setattr("app.agent.tools.run_check", full_check)
    monkeypatch.setattr("app.repository.get_latest_snapshot", snapshot)
    first = api_client.post("/api/v1/chat/messages", json={"message": "Проверь контрагента 6165169320"}).json()
    conversation_id = first["conversation_id"]
    assert first["active_company"]["inn"] == "6165169320"
    for question, label in (("А что у них с финансами?", "Финансы"), ("А что у них с судами?", "Суды")):
        response = api_client.post("/api/v1/chat/messages", json={
            "message": question, "conversation_id": conversation_id,
        })
        assert response.status_code == 200
        body = response.json()
        assert body["conversation_id"] == conversation_id
        assert body["active_company"]["inn"] == "6165169320"
        assert body["metadata"]["tool_calls"] == 1
        assert body["message"].startswith(label)
    assert calls == [("full", "6165169320"), ("snapshot", "6165169320"), ("snapshot", "6165169320")]


def test_unknown_conversation_does_not_start_check(api_client, monkeypatch):
    async def forbidden(*args, **kwargs):
        raise AssertionError("Unknown conversations must not execute tools")

    monkeypatch.setattr("app.agent.tools.run_check", forbidden)
    response = api_client.post("/api/v1/chat/messages", json={
        "message": "Проверь контрагента 6165169320",
        "conversation_id": "00000000-0000-0000-0000-000000000099",
    })
    assert response.status_code == 200
    assert response.json()["metadata"]["error_code"] == "unknown_conversation"
    assert response.json()["metadata"]["tool_calls"] == 0


def test_conversation_id_schema_is_uuid(api_client):
    assert api_client.post("/api/v1/chat/messages", json={
        "message": "А финансы?", "conversation_id": "arbitrary-thread",
    }).status_code == 422
