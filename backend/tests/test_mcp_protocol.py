import asyncio
from datetime import date, datetime
from decimal import Decimal
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from app.mcp_data.client import McpCompanyDataReader, LimitedTransport
from app.mcp_data.contracts import INPUTS, MAX_HTTP_BYTES
from app.mcp_data.errors import CompanySourceError, SourceTimeout, SourceResultTooLarge, InvalidSourceResponse
from mcp_source_fixture import DOCUMENT, INN


@pytest.fixture(scope='module')
def mcp_url():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    process = subprocess.Popen([sys.executable, 'tests/mcp_source_fixture.py', str(port)],
        cwd=Path(__file__).resolve().parents[1], env={**os.environ, 'PYTHONPATH': '.'},
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            try: urllib.request.urlopen(url+'/health', timeout=.2).close(); break
            except Exception:
                if process.poll() is not None: pytest.fail('MCP fixture process failed')
                time.sleep(.05)
        else: pytest.fail('MCP fixture did not start')
        yield url+'/mcp'
    finally:
        process.terminate(); process.wait(timeout=5)


@pytest.mark.asyncio
async def test_real_protocol_discovery_and_all_read_operations(mcp_url):
    async with streamable_http_client(mcp_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = (await session.list_tools()).tools
            assert {t.name for t in tools} == set(INPUTS)
            assert all(t.annotations.read_only_hint for t in tools)
    reader = McpCompanyDataReader(mcp_url)
    snapshot = await reader.get_latest_snapshot(INN)
    assert snapshot['document'] == DOCUMENT
    assert type(snapshot['report_date']) is datetime and type(snapshot['registration_date']) is date
    assert await reader.get_latest_snapshot('0000000000') is None
    assert await reader.get_selection_snapshots([1]) == [snapshot]
    assert await reader.get_selection_snapshots([]) == []
    assert (await reader.list_companies())[0]['filled_blocks'] == 0
    assert (await reader.search_companies('Тест'))['exact_total'] == 1
    found = await reader.find_companies(min_proceeds=0)
    assert 'eligible_total' not in found
    assert found['rows'][0]['proceeds'] == Decimal('999999999999999999.99')
    assert found['rows'][0]['profit'] == Decimal('-1.01')
    assert found['rows'][0]['claims_amount'] is None
    assert (await reader.get_connection_candidates())[0]['document'] == DOCUMENT
    assert await reader.get_snapshots_for_connections([INN]) == [snapshot]
    assert await reader.data_source_status() == {'database': True}


@pytest.mark.asyncio
async def test_timeouts_cancellation_concurrency_and_recovery(mcp_url):
    reader = McpCompanyDataReader(mcp_url, timeout_s=.4)
    with pytest.raises(SourceTimeout): await reader.get_latest_snapshot('1111111111')
    reader.timeout_s = 5
    task = asyncio.create_task(reader.get_latest_snapshot('1111111111'))
    await asyncio.sleep(.1); task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    results = await asyncio.gather(*(reader.get_latest_snapshot(INN) for _ in range(3)))
    assert all(r['document'] == DOCUMENT for r in results)


@pytest.mark.asyncio
async def test_safe_upstream_error_and_size_limit(mcp_url):
    reader = McpCompanyDataReader(mcp_url)
    with pytest.raises(CompanySourceError) as error: await reader.get_latest_snapshot('2222222222')
    assert 'secret' not in str(error.value)
    with pytest.raises(SourceResultTooLarge): await reader.get_latest_snapshot('3333333333')


@pytest.mark.asyncio
async def test_http_limit_before_parsing():
    class FakeTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx2.Response(200, headers={'content-length':str(MAX_HTTP_BYTES+1)}, content=b'')
    async with httpx2.AsyncClient(transport=LimitedTransport(FakeTransport())) as client:
        with pytest.raises(SourceResultTooLarge): await client.get('http://localhost')


@pytest.mark.asyncio
async def test_invalid_response_is_not_missing_company(mcp_url):
    with pytest.raises(InvalidSourceResponse):
        await McpCompanyDataReader(mcp_url).get_latest_snapshot('4444444444')


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [
    {'version': 'company-data-v2', 'snapshot': None},
    {'version': 'company-data-v1', 'snapshot': {'inn': INN}},
    None,
])
async def test_reject_incompatible_or_corrupted_reply(mcp_url, monkeypatch, payload):
    from types import SimpleNamespace
    async def call(*args, **kwargs):
        return SimpleNamespace(structured_content=payload, is_error=False)
    monkeypatch.setattr(ClientSession, 'call_tool', call)
    with pytest.raises(InvalidSourceResponse):
        await McpCompanyDataReader(mcp_url).get_latest_snapshot(INN)


@pytest.mark.asyncio
async def test_server_validates_arguments_and_rebinding(mcp_url):
    async with streamable_http_client(mcp_url) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool('get_selection_snapshots', {'params': {'snapshot_ids': list(range(1, 52))}})
            assert result.is_error
            result = await session.call_tool('get_latest_snapshot', {'params': {'inn': 'bad'}})
            assert result.is_error
    async with httpx2.AsyncClient() as http:
        response = await http.post(mcp_url, headers={'host':'evil.example'}, json={})
        assert response.status_code == 421


@pytest.mark.asyncio
async def test_http_stream_limit_without_content_length():
    class Chunks(httpx2.AsyncByteStream):
        async def __aiter__(self):
            yield b'12345'
            yield b'67890'
    class FakeTransport(httpx2.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx2.Response(200, stream=Chunks())
    async with httpx2.AsyncClient(transport=LimitedTransport(FakeTransport(), max_bytes=8)) as client:
        with pytest.raises(SourceResultTooLarge): await client.get('http://localhost')
