"""Run with python -m app.mcp_data.server. No LLM or audit credentials required."""
from contextlib import asynccontextmanager
import asyncio
import logging
import os
import time
import uuid

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ValidationError
from psycopg.errors import QueryCanceled
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from starlette.responses import JSONResponse

from app.infrastructure.company_postgres import PostgresCompanyDataReader
from .contracts import (VERSION, MAX_RESULT_BYTES, INPUTS, OUTPUTS, wrap_result,
                        SnapshotArgs, SnapshotIdsArgs, CatalogArgs, SearchArgs,
                        CandidatesArgs, NeighboursArgs, SnapshotReply, SnapshotsReply,
                        CatalogReply, SearchReply, CandidatesReply, StatusReply)

log = logging.getLogger(__name__)


def create_server(reader=None):
    @asynccontextmanager
    async def lifespan(server):
        nonlocal reader
        if reader is not None:
            yield
            return
        # Never use app Settings here: that would load LLM keys from .env.
        dsn = os.environ['MCP_DATABASE_URL']
        async with AsyncConnectionPool(dsn, min_size=1, max_size=8, open=False,
                                      kwargs={'row_factory': dict_row,
                                              'options': '-c statement_timeout=8000'}) as pool:
            reader = PostgresCompanyDataReader(pool)
            try:
                yield
            finally:
                reader = None

    server = MCPServer('company-data', version=VERSION, lifespan=lifespan, log_level='WARNING')
    annotations = ToolAnnotations(read_only_hint=True, destructive_hint=False,
                                  idempotent_hint=True, open_world_hint=False)

    async def invoke(operation, params):
        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        code, rows, size = 'ok', 0, 0
        try:
            arguments = INPUTS[operation].model_validate(params).model_dump()
            async with asyncio.timeout(9):
                value = await getattr(reader, operation)(**arguments)
            result = OUTPUTS[operation].model_validate(wrap_result(operation, value))
            data = result.model_dump(mode='json', exclude_unset=True)
            data['version'] = VERSION
            size = len(result.model_copy(update={'version': VERSION}).model_dump_json(exclude_unset=True).encode())
            rows = len(data.get('rows', [])) if 'rows' in data else int(data.get('snapshot') is not None)
            if size > MAX_RESULT_BYTES:
                code = 'result_too_large'
            else:
                # Avoid SDK-generated duplicate textual copies of large source documents.
                return CallToolResult(content=[], structured_content=data)
        except (TimeoutError, QueryCanceled):
            code = 'timeout'
        except ValidationError:
            code = 'invalid_response'
        except Exception:
            # Raw SQL/DSN and source payloads must never leave the boundary or enter logs.
            code = 'source_unavailable'
        finally:
            log.info('mcp_read operation=%s request_id=%s duration_ms=%d rows=%d bytes=%d code=%s',
                     operation, request_id, (time.perf_counter()-started)*1000, rows, size, code)
        return CallToolResult(is_error=True, content=[TextContent(type='text', text=code)],
                              structured_content={'version': VERSION, 'error': code})

    @server.tool(annotations=annotations)
    async def get_latest_snapshot(params: SnapshotArgs) -> SnapshotReply:
        """Read the latest source snapshot by INN; null means no matching company."""
        return await invoke('get_latest_snapshot', params)

    @server.tool(annotations=annotations)
    async def get_selection_snapshots(params: SnapshotIdsArgs) -> SnapshotsReply:
        """Read up to 50 exact snapshot IDs, preserving selection provenance."""
        return await invoke('get_selection_snapshots', params)

    @server.tool(annotations=annotations)
    async def list_companies(params: CatalogArgs) -> CatalogReply:
        """Read the paginated company catalog, up to 200 rows."""
        return await invoke('list_companies', params)

    @server.tool(annotations=annotations)
    async def find_companies(params: SearchArgs) -> SearchReply:
        """Filter and rank companies using verified fields; up to 50 rows."""
        return await invoke('find_companies', params)

    @server.tool(annotations=annotations)
    async def get_connection_candidates(params: CandidatesArgs) -> CandidatesReply:
        """Read bounded identity projections for one-hop relationship checks."""
        return await invoke('get_connection_candidates', params)

    @server.tool(annotations=annotations)
    async def get_snapshots_for_connections(params: NeighboursArgs) -> SnapshotsReply:
        """Read latest snapshots of up to six explicitly identified neighbours."""
        return await invoke('get_snapshots_for_connections', params)

    @server.tool(annotations=annotations)
    async def data_source_status() -> StatusReply:
        """Report protocol contract version and database availability."""
        return await invoke('data_source_status', {})

    @server.custom_route('/health', methods=['GET'])
    async def health(request):
        try:
            async with asyncio.timeout(2):
                status = await reader.data_source_status()
            return JSONResponse({'version': VERSION, **status}, status_code=200 if status['database'] else 503)
        except Exception:
            return JSONResponse({'version': VERSION, 'database': False}, status_code=503)

    return server


def create_app(reader=None):
    return create_server(reader).streamable_http_app(
        stateless_http=True, json_response=True, host='0.0.0.0', max_request_body_size=64*1024,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=['company-data-mcp:8001', 'localhost:*', '127.0.0.1:*', '[::1]:*'],
            allowed_origins=['http://localhost:*', 'http://127.0.0.1:*', 'http://company-data-mcp:8001'],
        ),
    )


if __name__ == '__main__':
    import argparse
    import uvicorn
    parser = argparse.ArgumentParser(description='Read-only company data MCP server')
    parser.add_argument('--host', default='127.0.0.1')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    uvicorn.run(create_app(), host=args.host, port=8001, access_log=False, log_level='warning')
