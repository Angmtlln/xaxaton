"""Application boundary tests use a real MCP subprocess with a fixture source."""
import pytest
from fastapi.testclient import TestClient
from app.config import Settings
from app.infrastructure import repository
from app.infrastructure.company_postgres import PostgresCompanyDataReader
from app.mcp_data.errors import CompanySourceError, SourceTimeout, InvalidSourceResponse
from mcp_source_fixture import INN
from test_mcp_protocol import mcp_url


@pytest.mark.asyncio
async def test_all_facade_reads_use_mcp_without_direct_fallback(mcp_url, monkeypatch):
    settings = Settings(_env_file=None, company_data_backend='mcp', mcp_server_url=mcp_url)
    monkeypatch.setattr('app.config.get_settings', lambda: settings)
    def forbidden(*args, **kwargs): raise AssertionError('Direct SQL must not run in MCP mode')
    monkeypatch.setattr(PostgresCompanyDataReader, '_pool', forbidden)
    snapshot = await repository.get_latest_snapshot(INN)
    assert (await repository.get_selection_snapshots([1])) == [snapshot]
    assert (await repository.list_companies())[0]['inn'] == INN
    assert (await repository.find_companies(min_proceeds=0))['total'] == 1
    assert (await repository.search_companies('Тест'))['exact_total'] == 1
    assert (await repository.get_connection_candidates())[0]['inn'] == INN
    assert (await repository.get_snapshots_for_connections([INN])) == [snapshot]
    settings.mcp_server_url = 'http://127.0.0.1:1/mcp'
    with pytest.raises(CompanySourceError): await repository.get_latest_snapshot(INN)


@pytest.mark.parametrize('error,status', [(CompanySourceError,503), (SourceTimeout,503), (InvalidSourceResponse,502)])
def test_rest_source_failures_are_safe_and_not_404(monkeypatch, error, status):
    from app.main import create_app
    async def fail(*args, **kwargs): raise error()
    monkeypatch.setattr(repository, 'get_latest_snapshot', fail)
    from app.api.deps import groq_dep
    from app.llm.groq_client import GroqClient
    app = create_app()
    app.dependency_overrides[groq_dep] = lambda: GroqClient(Settings(_env_file=None, llm_mock=True))
    client = TestClient(app, raise_server_exceptions=False)
    try:
        response = client.get('/api/v1/companies/6165169320/facts')
        assert response.status_code == status
        assert response.json() == {'detail': error.message}
        response = client.post('/api/v1/checks', json={'inn':'6165169320','persist':False})
        assert response.status_code == status
    finally:
        client.close()


@pytest.mark.asyncio
async def test_health_requires_both_audit_db_and_source(mcp_url, monkeypatch):
    from app.api.routes.health import health
    async def healthy(): return True
    monkeypatch.setattr('app.api.routes.health.healthcheck', healthy)
    settings = Settings(_env_file=None, company_data_backend='mcp', mcp_server_url=mcp_url)
    result = await health(settings)
    assert result.status == 'ok' and result.database and result.data_source_available
    settings.mcp_server_url = 'http://127.0.0.1:1/mcp'
    result = await health(settings)
    assert result.status == 'degraded' and result.database and not result.data_source_available


@pytest.mark.asyncio
async def test_registry_preserves_source_error_codes(monkeypatch):
    from app.agent.tools import build_tool_registry, ToolContext
    from app.llm.groq_client import GroqClient
    settings = Settings(_env_file=None, llm_mock=True)
    async def fail(*args, **kwargs): raise CompanySourceError()
    monkeypatch.setattr(repository, 'get_latest_snapshot', fail)
    client = GroqClient(settings)
    try:
        result = await build_tool_registry(settings).execute('get_financial_data', {'inn':'6165169320'},
            ToolContext(settings=settings, client=client, persist=False))
        assert result.status == 'error' and result.error.code == 'source_unavailable'
    finally: await client.aclose()


def test_ndjson_delivers_safe_source_error(monkeypatch):
    import json
    from app.main import create_app
    from app.api.deps import settings_dep, groq_dep
    from app.agent.conversations import ConversationStore
    from app.llm.groq_client import GroqClient
    settings = Settings(_env_file=None, llm_mock=True)
    async def fail(*args, **kwargs): raise CompanySourceError()
    monkeypatch.setattr(repository, 'get_latest_snapshot', fail)
    app = create_app()
    app.state.conversation_store = ConversationStore()
    app.dependency_overrides[settings_dep] = lambda: settings
    app.dependency_overrides[groq_dep] = lambda: GroqClient(settings)
    client = TestClient(app)
    try:
        response = client.post('/api/v1/chat/messages/stream', json={'message':'Проверь контрагента 6165169320'})
        events = [json.loads(line) for line in response.text.splitlines()]
        assert response.status_code == 200
        result = events[-1]['payload']
        assert result['metadata']['status'] == 'error'
        assert result['metadata']['error_code'] == 'source_unavailable'
        assert result['metadata']['model_calls'] == 0
        assert 'не найдена' not in result['message'].lower()
    finally:
        client.close()
