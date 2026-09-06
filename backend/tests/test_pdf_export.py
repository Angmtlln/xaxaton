"""Snapshot isolation, lifecycle, routing and the real PDF renderer."""
import json
import os
import uuid
from pathlib import Path

import pytest

from app.agent.conversations import ConversationStore, UnknownConversation
from app.agent.pdf_export import render_pdf
from app.agent.runtime import build_master_runtime
from app.config import Settings
from app.llm.groq_client import GroqClient
from test_chat_api import api_client
from test_comparison import snapshots


async def full_result(monkeypatch, check_payload, store):
    async def check(*args, **kwargs):
        return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    settings = Settings(_env_file=None, llm_mock=True, groq_api_key=None)
    runtime = build_master_runtime(settings, GroqClient(settings), persist=False, conversation_store=store)
    response = await runtime.run('Проверь контрагента 6165169320')
    return runtime, response


@pytest.mark.asyncio
async def test_snapshot_isolation_cache_and_command(monkeypatch, check_payload):
    store = ConversationStore()
    runtime, response = await full_result(monkeypatch, check_payload, store)
    cid, rid = response.conversation_id, response.metadata.agent_run_id
    original = response.message
    response.message = 'Изменённый текст клиента'
    calls = []
    async def render(payload):
        calls.append(payload)
        return b'%PDF-1.4 test'
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', render)
    async def forbidden(*args, **kwargs):
        pytest.fail('Export must not analyze again')
    monkeypatch.setattr('app.agent.tools.run_check', forbidden)
    # Export must leave the existing factual and conversational context untouched.
    before = await store.checkpointer.aget_tuple({'configurable': {'thread_id': cid}})
    exported = await runtime.run('экспортируй это в PDF', cid)
    assert exported.attachments and exported.metadata.model_calls == 0
    assert calls[0]['message'] == original
    again = await runtime.run('сохрани результат в PDF', cid)
    assert again.attachments == exported.attachments and len(calls) == 1
    after = await store.checkpointer.aget_tuple({'configurable': {'thread_id': cid}})
    assert before.checkpoint == after.checkpoint
    async with store.session(cid):
        assert await store.exports.export_result_pdf(cid, rid) == exported.attachments[0]
    store.exports.close()


@pytest.mark.asyncio
async def test_no_result_ambiguous_and_failure(monkeypatch, check_payload):
    store = ConversationStore()
    runtime, result = await full_result(monkeypatch, check_payload, store)
    missing = await runtime.run('экспортируй это в PDF')
    assert missing.metadata.status == 'needs_input' and not missing.attachments
    ambiguous = await runtime.run('экспортируй предыдущий отчёт в PDF', result.conversation_id)
    assert ambiguous.metadata.status == 'needs_input'
    async def fail(payload):
        raise RuntimeError('Chromium failed')
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', fail)
    error = await runtime.run('экспортируй это в PDF', result.conversation_id)
    assert error.metadata.error_code == 'pdf_unavailable'
    assert error.suggested_actions[0].type == 'export_pdf'
    record = store.exports.record(result.conversation_id, result.metadata.agent_run_id)
    assert record['path'] is None
    store.exports.close()


@pytest.mark.asyncio
async def test_capacity_expiry_and_files(monkeypatch, check_payload):
    store = ConversationStore(ttl_s=10, max_conversations=1)
    _, response = await full_result(monkeypatch, check_payload, store)
    cid = response.conversation_id
    async def render(payload):
        return b'%PDF-1.4 test'
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', render)
    async with store.session(cid):
        await store.exports.export_result_pdf(cid, response.metadata.agent_run_id)
        path = store.exports.record(cid, response.metadata.agent_run_id)['path']
        assert path.exists()
        for _ in range(6):
            copy = response.model_copy(deep=True)
            copy.metadata.agent_run_id = str(uuid.uuid4())
            store.exports.capture(cid, copy)
        assert len(store.exports.results[cid]) == 6
        assert not path.exists()
        await store.exports.export_result_pdf(cid, copy.metadata.agent_run_id)
        last_path = store.exports.record(cid, copy.metadata.agent_run_id)['path']
    store._leases[cid].touched -= 11
    with pytest.raises(UnknownConversation):
        async with store.session(cid):
            pass
    assert not last_path.exists() and cid not in store.exports.results
    store.exports.close()


def test_http_export_ownership_and_retry(api_client, monkeypatch, check_payload):
    async def check(*args, **kwargs):
        return check_payload
    seen = []
    async def render(payload):
        seen.append(payload)
        return b'%PDF-1.4 test'
    monkeypatch.setattr('app.agent.tools.run_check', check)
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', render)
    response = api_client.post('/api/v1/chat/messages', json={'message': 'Проверь контрагента 6165169320'}).json()
    cid, rid = response['conversation_id'], response['metadata']['agent_run_id']
    action = response['suggested_actions'][-1]
    assert action['type'] == 'export_pdf' and action['result_id'] == rid
    endpoint = f'/api/v1/chat/{cid}/exports'
    file = api_client.post(endpoint, json={'result_id': rid}).json()
    assert api_client.post(endpoint, json={'result_id': rid}).json() == file
    assert len(seen) == 1
    downloaded = api_client.get(file['download_url'])
    assert downloaded.content.startswith(b'%PDF-')
    assert 'attachment;' in downloaded.headers['content-disposition']
    other = api_client.post('/api/v1/chat/messages', json={'message': 'Привет'}).json()['conversation_id']
    assert api_client.post(f'/api/v1/chat/{other}/exports', json={'result_id': rid}).status_code == 404
    assert api_client.get(file['download_url'].replace(cid, other)).status_code == 404
    assert api_client.post(endpoint, json={'result_id': rid, 'html': '<script>'}).status_code == 422


@pytest.mark.asyncio
async def test_exact_earlier_result_after_new_result(monkeypatch, check_payload):
    store = ConversationStore()
    _, first = await full_result(monkeypatch, check_payload, store)
    second = first.model_copy(deep=True)
    second.metadata.agent_run_id = str(uuid.uuid4())
    second.message = 'Другая компания'
    store.exports.capture(first.conversation_id, second)
    seen = []
    async def render(payload):
        seen.append(payload)
        return b'%PDF-1.4 test'
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', render)
    async with store.session(first.conversation_id):
        await store.exports.export_result_pdf(first.conversation_id, first.metadata.agent_run_id)
    assert seen[0]['message'] == first.message
    store.exports.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get('PDF_REAL_TEST') != '1', reason='Set PDF_REAL_TEST=1 with Chromium installed')
async def test_real_pdf_documents(monkeypatch, check_payload, snapshots):
    from test_comparison import _snapshot, _fin_row
    store = ConversationStore()
    runtime, full = await full_result(monkeypatch, check_payload, store)
    output = Path(os.environ.get('PDF_TEST_OUTPUT', '/tmp/xaxaton-pdf-test'))
    output.mkdir(parents=True, exist_ok=True)
    examples = [('report', full)]
    from app.agent.models import LineChartBlock, ExternalNews
    detailed = full.model_copy(deep=True)
    series = next(f['value'] for b in check_payload['blocks'] for f in b['facts'] if f['id'] == 'fin.series')
    detailed.blocks.append(LineChartBlock(title='Динамика выручки', description='Данные тестового снимка',
        unit='руб', state='data', series=[{'key': 'revenue', 'label': 'Выручка', 'evidence_id': 'fin.series',
            'points': [{'x': str(row['year']), 'value': row['proceeds']} for row in series]}]))
    detailed.external_news_status = 'completed'
    detailed.external_news = [ExternalNews(title=f'Тестовая публикация {i}', date='2026-09-06',
        source='example.com', url=f'https://example.com/news/{i}',
        summary='Синтетическая новость для проверки печатной вёрстки. Не сведения о компании.') for i in range(1, 5)]
    examples.append(('report-chart-news', detailed))
    inns = ['6165169320', '2901324364', '0278949271', '2311304742', '3711039473']
    for inn in inns[3:]:
        snapshots[inn] = _snapshot(inn, 'ООО Очень длинное название производственной компании по поставкам оборудования', fin_rows=[_fin_row(2024, 123456789)])
    for count in (2, 5):
        result = await runtime.run('Сравни ' + ', '.join(inns[:count]))
        assert any(b.type == 'comparison_table' for b in result.blocks)
        examples.append((f'comparison-{count}', result))
    for name, response in examples:
        payload = response.model_dump(mode='json')
        (output / f'{name}.json').write_text(json.dumps(payload, ensure_ascii=False))
        data = await render_pdf(payload)
        assert data.startswith(b'%PDF-') and len(data) > 10000
        (output / f'{name}.pdf').write_bytes(data)
    store.exports.close()


@pytest.mark.asyncio
async def test_concurrent_generation_is_bounded(monkeypatch, check_payload):
    import asyncio
    store = ConversationStore()
    _, response = await full_result(monkeypatch, check_payload, store)
    active = peak = 0
    async def render(payload):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.02)
        active -= 1
        return b'%PDF-1.4 test'
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', render)
    ids = []
    for _ in range(3):
        async with store.session() as (cid, _):
            store.exports.capture(cid, response)
            ids.append(cid)
    async def export(cid):
        async with store.session(cid):
            return await store.exports.export_result_pdf(cid, response.metadata.agent_run_id)
    await asyncio.gather(*(export(cid) for cid in ids))
    assert peak == 1
    store.exports.close()


@pytest.mark.asyncio
@pytest.mark.skipif(os.environ.get('PDF_REAL_TEST') != '1', reason='Set PDF_REAL_TEST=1 with Chromium installed')
async def test_desktop_export_download(monkeypatch, check_payload, snapshots):
    """Real browser -> chat runtime -> real Chromium PDF -> download, fixture data only."""
    import asyncio
    import socket
    from contextlib import asynccontextmanager
    import uvicorn
    from playwright.async_api import async_playwright
    from app.api.deps import settings_dep, groq_dep
    from app.main import create_app

    settings = Settings(_env_file=None, llm_mock=True, groq_api_key=None)
    client = GroqClient(settings)
    application = create_app()
    application.state.conversation_store = ConversationStore()
    application.dependency_overrides[settings_dep] = lambda: settings
    application.dependency_overrides[groq_dep] = lambda: client
    checks = []
    async def check(*args, **kwargs):
        checks.append(1)
        return check_payload
    monkeypatch.setattr('app.agent.tools.run_check', check)
    @asynccontextmanager
    async def lifespan(app):
        yield
        app.state.conversation_store.exports.close()
        await client.aclose()
    application.router.lifespan_context = lifespan
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(application, log_level='error'))
    task = asyncio.create_task(server.serve(sockets=[sock]))
    output = Path(os.environ.get('PDF_TEST_OUTPUT', '/tmp/xaxaton-pdf-test'))
    output.mkdir(parents=True, exist_ok=True)
    try:
        async with asyncio.timeout(15):
            while not server.started:
                await asyncio.sleep(.05)
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            try:
                page = await browser.new_page(viewport={'width': 1440, 'height': 1000}, accept_downloads=True)
                failures = []
                page.on('pageerror', lambda error: failures.append(str(error)))
                await page.goto(f'http://127.0.0.1:{port}/')
                await page.locator('#chat-input').fill('Проверь контрагента 6165169320')
                await page.locator('#send-button').click()
                await page.get_by_role('button', name='Экспортировать в PDF', exact=True).wait_for()
                assert len(checks) == 1
                await page.get_by_role('button', name='Экспортировать в PDF', exact=True).click()
                link = page.get_by_role('link', name='Скачать PDF', exact=True)
                await link.wait_for(timeout=35000)
                async with page.expect_download() as pending:
                    await link.click()
                download = await pending.value
                await download.save_as(output / 'desktop-report.pdf')
                assert (output / 'desktop-report.pdf').read_bytes().startswith(b'%PDF-')
                assert len(checks) == 1
                await page.locator('#chat-input').fill('Сравни 6165169320 и 2901324364')
                await page.locator('#send-button').click()
                await page.locator('.comparison-table').wait_for()
                await page.get_by_role('button', name='Экспортировать в PDF', exact=True).last.click()
                await page.locator('.pdf-file-card').nth(1).wait_for(timeout=35000)
                async with page.expect_download() as pending:
                    await page.get_by_role('link', name='Скачать PDF', exact=True).last.click()
                await (await pending.value).save_as(output / 'desktop-comparison.pdf')
                assert len(checks) == 1
                await page.get_by_role('button', name='Экспортировать в PDF', exact=True).first.click()
                await page.locator('.pdf-file-card').nth(2).wait_for(timeout=35000)
                assert not failures
                await page.screenshot(path=str(output / 'desktop-export.png'), full_page=True)
            finally:
                await browser.close()
    finally:
        server.should_exit = True
        await task
        sock.close()


@pytest.mark.asyncio
async def test_export_timeout_releases_slot(monkeypatch, check_payload):
    import asyncio
    from app.agent.pdf_export import ExportUnavailable
    store = ConversationStore()
    _, result = await full_result(monkeypatch, check_payload, store)
    cancelled = []
    async def slow(payload):
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.append(True)
    monkeypatch.setattr('app.agent.pdf_export.render_pdf', slow)
    monkeypatch.setattr('app.agent.pdf_export.PDF_TIMEOUT_S', .01)
    async with store.session(result.conversation_id):
        with pytest.raises(ExportUnavailable):
            await store.exports.export_result_pdf(result.conversation_id, result.metadata.agent_run_id)
        assert cancelled
        async def ready(payload):
            return b'%PDF-1.4 test'
        monkeypatch.setattr('app.agent.pdf_export.render_pdf', ready)
        assert await store.exports.export_result_pdf(result.conversation_id, result.metadata.agent_run_id)
    store.exports.close()
