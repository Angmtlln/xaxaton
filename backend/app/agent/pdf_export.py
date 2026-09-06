"""Export immutable presentation snapshots; never re-run analysis or trust client HTML."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
import time
import uuid
from collections import OrderedDict
from pathlib import Path

from langchain_core.tools import StructuredTool

from .models import AssistantMetadata, AssistantResponse, CompanyRef, ExportPdfAction, PdfAttachment

log = logging.getLogger(__name__)
PDF_TIMEOUT_S = 30


class ExportUnavailable(ValueError):
    pass


class ExportStore:
    """Owned by ConversationStore; all accesses occur inside the conversation lease."""

    def __init__(self):
        self.results = {}
        self._directory = None
        self._slots = asyncio.Semaphore(1)

    def capture(self, cid, response):
        eligible = response.leading_artifact is not None or any(
            block.type == 'comparison_table' for block in response.blocks)
        if not eligible or response.metadata.status not in {'completed', 'partial'}:
            return
        payload = response.model_dump(mode='json')
        payload['suggested_actions'] = []
        payload['attachments'] = []
        encoded = json.dumps(payload, ensure_ascii=False)
        if len(encoded.encode()) > 2_000_000:
            return
        result_id = response.metadata.agent_run_id
        records = self.results.setdefault(cid, OrderedDict())
        if result_id not in records:
            records[result_id] = {'payload': encoded, 'attachment': None, 'path': None}
        while len(records) > 6:
            _, old = records.popitem(last=False)
            self._remove_file(old)
        response.suggested_actions = response.suggested_actions[:3] + [ExportPdfAction(result_id=result_id)]

    @staticmethod
    def _remove_file(record):
        if record['path']:
            record['path'].unlink(missing_ok=True)

    def discard(self, cid):
        for record in self.results.pop(cid, {}).values():
            self._remove_file(record)

    def close(self):
        self.results.clear()
        if self._directory:
            self._directory.cleanup()
            self._directory = None

    def record(self, cid, result_id):
        record = self.results.get(cid, {}).get(result_id)
        if record is None:
            raise ExportUnavailable('Результат недоступен. Выполните анализ или сравнение заново.')
        return record

    async def export_result_pdf(self, cid, result_id):
        from app.infrastructure.progress import emit_progress
        emit_progress('pdf_export')
        record = self.record(cid, result_id)
        if record['attachment']:
            return record['attachment']
        path = None
        try:
            async with asyncio.timeout(PDF_TIMEOUT_S):
                async with self._slots:
                    data = await render_pdf(json.loads(record['payload']))
            if not data.startswith(b'%PDF-') or len(data) > 10_000_000:
                raise ValueError('Invalid or oversized PDF')
            if self._directory is None:
                self._directory = tempfile.TemporaryDirectory(prefix='xaxaton-pdf-')
            file_id = str(uuid.uuid4())
            path = Path(self._directory.name) / (file_id + '.pdf')
            path.write_bytes(data)
            payload = json.loads(record['payload'])
            comparison = any(b['type'] == 'comparison_table' for b in payload['blocks'])
            name = 'comparison' if comparison else 'report'
            attachment = PdfAttachment(id=file_id, filename=f'{name}-{result_id}.pdf',
                size_bytes=len(data), download_url=f'/api/v1/chat/{cid}/exports/{file_id}')
            record.update(path=path, attachment=attachment)
            return attachment
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if path is not None:
                path.unlink(missing_ok=True)
            log.exception('PDF generation failed result_id=%s', result_id)
            raise ExportUnavailable('Не удалось создать PDF. Попробуйте экспорт ещё раз.') from exc

    def download(self, cid, file_id):
        for record in self.results.get(cid, {}).values():
            attachment = record['attachment']
            if attachment and attachment.id == file_id:
                return attachment, record['path'].read_bytes()
        raise ExportUnavailable('Файл недоступен или срок его хранения истёк.')


async def render_pdf(payload):
    """Intercept every browser request: only bundled frontend files can be loaded."""
    import mimetypes
    from playwright.async_api import async_playwright
    from app.api.routes.pages import frontend_dir

    root = frontend_dir().resolve()
    origin = 'http://pdf.internal'
    async def serve(route):
        from urllib.parse import urlsplit, unquote
        url = urlsplit(route.request.url)
        if f'{url.scheme}://{url.netloc}' != origin or not url.path.startswith('/static/'):
            await route.abort()
            return
        file = (root / unquote(url.path[len('/static/'):])).resolve()
        if not file.is_relative_to(root) or not file.is_file():
            await route.abort()
            return
        await route.fulfill(body=file.read_bytes(), content_type=mimetypes.guess_type(file)[0] or 'application/octet-stream')

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        try:
            context = await browser.new_context(viewport={'width': 1440, 'height': 1000},
                service_workers='block', reduced_motion='reduce')
            try:
                await context.route('**/*', serve)
                page = await context.new_page()
                await page.goto(origin + '/static/pdf.html')
                await page.wait_for_function('typeof window.renderPdf === "function"')
                await page.evaluate('(payload) => window.renderPdf(payload)', payload)
                await page.evaluate('document.fonts.ready')
                comparison = any(b['type'] == 'comparison_table' for b in payload['blocks'])
                return await page.pdf(format='A4', landscape=comparison, print_background=True,
                    display_header_footer=True, header_template='<span></span>',
                    footer_template='<div style="font-size:9px;width:100%;text-align:center;color:#777"><span class="pageNumber"></span> / <span class="totalPages"></span></div>',
                    margin={'top': '12mm', 'bottom': '16mm', 'left': '12mm', 'right': '12mm'})
            finally:
                await context.close()
        finally:
            await browser.close()


def build_export_pdf_tool(store, cid):
    async def export_result_pdf(result_id: str) -> dict:
        """Сохранить готовый результат анализа/сравнения в PDF по его result_id."""
        return (await store.exports.export_result_pdf(cid, result_id)).model_dump(mode='json')
    return StructuredTool.from_function(coroutine=export_result_pdf)


def handles_pdf_request(message):
    return bool(re.search(r'\b(?:pdf|пдф)\b', message, re.I) and
                re.search(r'экспорт|сохран|скача|сдела|созда|выгруз|пришли|отправь', message, re.I))


async def pdf_chat_response(store, cid, message, run_id, started):
    """Explicit export command uses the Master tool adapter without another model call."""
    checkpoint = await store.checkpointer.aget_tuple({'configurable': {'thread_id': cid}})
    active = checkpoint.checkpoint['channel_values'].get('active_company') if checkpoint else None
    response = AssistantResponse(message='PDF готов.', conversation_id=cid,
        active_company=CompanyRef.model_validate(active) if active else None,
        metadata=AssistantMetadata(agent_run_id=run_id, status='completed', tool_calls=0,
            routing='deterministic_guard', prompt_version='pdf-export-1', latency_ms=0))
    records = store.exports.results.get(cid, {})
    simple = re.fullmatch(
        r'\s*(?:пожалуйста[, ]+)?(?:экспорт\w*|сохран\w*|скача\w*|сделай|создай|выгрузи|пришли|отправь)'
        r'\s+(?:(?:это|результат|отч[её]т|сравнение|текущий результат|текущий отч[её]т|текущее сравнение)\s+)?'
        r'(?:(?:в|как|в формате)\s+)?(?:pdf|пдф)(?:[- ]файл)?[.!?\s]*', message, re.I)
    if not simple or not records:
        response.message = ('Уточните, какой результат нужно экспортировать, или нажмите «Экспортировать в PDF» под ним.'
                            if records else 'Сначала выполните анализ или сравнение, затем экспортируйте результат в PDF.')
        response.metadata.status = 'needs_input'
        return response
    result_id = next(reversed(records))
    # An explicitly named report/comparison must not silently select another kind.
    payload = json.loads(records[result_id]['payload'])
    is_comparison = any(b['type'] == 'comparison_table' for b in payload['blocks'])
    if (re.search('сравнение', message, re.I) and not is_comparison or
            re.search('отч[её]т', message, re.I) and is_comparison):
        response.message = 'Нажмите «Экспортировать в PDF» под нужным результатом.'
        response.metadata.status = 'needs_input'
        return response
    response.metadata.tool_calls = 1
    try:
        tool = build_export_pdf_tool(store, cid)
        response.attachments = [PdfAttachment.model_validate(await tool.ainvoke({'result_id': result_id}))]
    except ExportUnavailable as exc:
        response.message = str(exc)
        response.metadata.status = 'error'
        response.metadata.error_code = 'pdf_unavailable'
        response.suggested_actions = [ExportPdfAction(result_id=result_id)]
    response.metadata.latency_ms = int((time.perf_counter() - started) * 1000)
    return response
