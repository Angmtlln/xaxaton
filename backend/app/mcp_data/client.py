"""Each deterministic read owns its MCP transport and session in one task."""
import asyncio
import json
import logging
import time
import uuid
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from pydantic import ValidationError
from .contracts import INPUTS, OUTPUTS, VERSION, MAX_HTTP_BYTES, MAX_RESULT_BYTES, unwrap_result
from .errors import CompanySourceError, InvalidSourceResponse, SourceResultTooLarge, SourceTimeout

log = logging.getLogger(__name__)


class LimitedTransport(httpx2.AsyncBaseTransport):
    """Bound bytes before the SDK parses responses, including error bodies."""
    def __init__(self, inner=None, max_bytes=MAX_HTTP_BYTES):
        self.inner = inner if inner is not None else httpx2.AsyncHTTPTransport(retries=0)
        self.max_bytes = max_bytes

    async def handle_async_request(self, request):
        response = await self.inner.handle_async_request(request)
        try:
            length = response.headers.get('content-length')
            if length and int(length) > self.max_bytes:
                raise SourceResultTooLarge()
            data = bytearray()
            async for chunk in response.aiter_bytes():
                if len(data) + len(chunk) > self.max_bytes:
                    raise SourceResultTooLarge()
                data.extend(chunk)
            headers = [(k, v) for k, v in response.headers.multi_items()
                       if k.lower() not in {'content-encoding', 'content-length'}]
            return httpx2.Response(response.status_code, headers=headers, content=bytes(data), extensions=response.extensions)
        finally:
            await response.aclose()

    async def aclose(self):
        await self.inner.aclose()


def source_error(exc):
    if isinstance(exc, CompanySourceError): return exc
    if isinstance(exc, (TimeoutError, httpx2.TimeoutException)): return SourceTimeout()
    # SDK output-schema validation raises RuntimeError; never surface its payload.
    if isinstance(exc, (RuntimeError, ValidationError, json.JSONDecodeError)):
        return InvalidSourceResponse()
    if isinstance(exc, BaseExceptionGroup):
        errors = [source_error(child) for child in exc.exceptions]
        return next((e for e in errors if type(e) is not CompanySourceError), errors[0])
    return CompanySourceError()


class McpCompanyDataReader:
    def __init__(self, url, timeout_s=10):
        self.url, self.timeout_s = url, timeout_s

    async def _read(self, operation, arguments):
        # repository historically uses None for an omitted ranking list.
        if operation == 'find_companies' and arguments.get('ranking') is None:
            arguments = {k: v for k, v in arguments.items() if k != 'ranking'}
        params = INPUTS[operation].model_validate(arguments).model_dump(mode='json')
        started, request_id = time.perf_counter(), uuid.uuid4().hex
        code, rows, size = 'ok', 0, 0
        try:
            async with asyncio.timeout(self.timeout_s):
                async with httpx2.AsyncClient(transport=LimitedTransport(), timeout=self.timeout_s,
                                             trust_env=False, follow_redirects=False,
                                             headers={'Accept-Encoding': 'identity', 'X-Request-ID': request_id}) as http:
                    async with streamable_http_client(self.url, http_client=http) as (read, write):
                        async with ClientSession(read, write, read_timeout_seconds=self.timeout_s) as session:
                            await session.initialize()
                            result = await session.call_tool(operation, {} if operation == 'data_source_status' else {'params': params})
                            data = result.structured_content
                            if not isinstance(data, dict) or data.get('version') != VERSION:
                                raise InvalidSourceResponse()
                            size = len(json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode())
                            if size > MAX_RESULT_BYTES: raise SourceResultTooLarge()
                            if result.is_error:
                                error = {'timeout': SourceTimeout, 'result_too_large': SourceResultTooLarge,
                                         'invalid_response': InvalidSourceResponse}.get(data.get('error'), CompanySourceError)
                                raise error()
                            try: reply = OUTPUTS[operation].model_validate(data)
                            except ValidationError: raise InvalidSourceResponse() from None
                            value = unwrap_result(operation, reply)
                            if operation == 'get_latest_snapshot' and value is not None and value['inn'] != params['inn']:
                                raise InvalidSourceResponse()
                            if operation == 'get_selection_snapshots' and any(r['snapshot_id'] not in params['snapshot_ids'] for r in value):
                                raise InvalidSourceResponse()
                            if operation == 'get_snapshots_for_connections' and any(r['inn'] not in params['inns'] for r in value):
                                raise InvalidSourceResponse()
                            rows = len(value) if isinstance(value, list) else len(value.get('rows', [])) if operation in {'find_companies', 'search_companies'} else int(value is not None)
                            return value
        except Exception as exc:
            error = source_error(exc); code = error.code
            raise error from None
        finally:
            log.info('company_source operation=%s request_id=%s duration_ms=%d rows=%d bytes=%d code=%s',
                     operation, request_id, (time.perf_counter()-started)*1000, rows, size, code)

    async def get_latest_snapshot(self, inn): return await self._read('get_latest_snapshot', {'inn': inn})
    async def get_selection_snapshots(self, snapshot_ids): return await self._read('get_selection_snapshots', {'snapshot_ids': snapshot_ids})
    async def list_companies(self, **kwargs): return await self._read('list_companies', kwargs)
    async def search_companies(self, query, limit=5): return await self._read('search_companies', {'query': query, 'limit': limit})
    async def find_companies(self, **kwargs): return await self._read('find_companies', kwargs)
    async def get_connection_candidates(self, limit=10001): return await self._read('get_connection_candidates', {'limit': limit})
    async def get_snapshots_for_connections(self, inns): return await self._read('get_snapshots_for_connections', {'inns': inns})
    async def data_source_status(self): return await self._read('data_source_status', {})
