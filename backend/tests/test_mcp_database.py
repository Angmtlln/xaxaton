"""Opt-in real PostgreSQL + separate MCP process, isolated from source/audit data.

TEST_MCP_ADMIN_DATABASE_URL must point to a disposable-capable local PostgreSQL
cluster (CREATE DATABASE/ROLE). Never run against a shared production cluster.
"""
import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import uuid

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
import pytest

from app.config import Settings, get_settings
from app.domain.facts import build_all_blocks, build_coverage
from app.infrastructure.company_postgres import PostgresCompanyDataReader
from app.mcp_data.client import McpCompanyDataReader
from app.mcp_data.errors import CompanySourceError

ROOT = Path(__file__).resolve().parents[1]


class TestDatabase:
    __test__ = False
    def start_server(self):
        self.process = subprocess.Popen([sys.executable, '-c',
            'import sys,uvicorn; from app.mcp_data.server import create_app; '
            'uvicorn.run(create_app(),host="127.0.0.1",port=int(sys.argv[1]),log_level="critical",access_log=False)', str(self.port)],
            cwd=ROOT, env={**os.environ, 'MCP_DATABASE_URL':self.read_dsn, 'PYTHONPATH':str(ROOT)},
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                urllib.request.urlopen(self.url.replace('/mcp','/health'), timeout=.2).close()
                return
            except Exception:
                if self.process.poll() is not None: raise AssertionError('MCP DB server failed to start')
                time.sleep(.05)
        raise AssertionError('MCP DB server startup timeout')

    def stop_server(self):
        if self.process.poll() is None:
            self.process.terminate(); self.process.wait(timeout=5)


@pytest.fixture(scope='module')
def mcp_database():
    admin = os.environ.get('TEST_MCP_ADMIN_DATABASE_URL')
    if not admin: pytest.skip('Set TEST_MCP_ADMIN_DATABASE_URL for isolated PostgreSQL integration')
    db = TestDatabase()
    name = 'mcp_test_' + uuid.uuid4().hex[:12]
    password = os.environ['MCP_DB_PASSWORD']
    db.admin_dsn = make_conninfo(admin, dbname=name)
    db.read_dsn = make_conninfo(admin, dbname=name, user='contractors_mcp', password=password)
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); db.port=sock.getsockname()[1]
    db.url = f'http://127.0.0.1:{db.port}/mcp'
    with psycopg.connect(admin, autocommit=True) as conn:
        conn.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with psycopg.connect(db.admin_dsn) as conn:
            conn.execute((ROOT/'db/schema.sql').read_text())
            conn.execute((ROOT/'db/seed_dictionary.sql').read_text())
        env={**os.environ,'DATABASE_URL':db.admin_dsn,'PYTHONPATH':str(ROOT)}
        for command in [
            [sys.executable,'scripts/load_snapshot.py','--file',str(ROOT.parent/'contractors_audit.snapshot.json')],
            [sys.executable,'scripts/setup_mcp_access.py'],
            [sys.executable,'scripts/setup_mcp_access.py'],
        ]:
            result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True)
            assert result.returncode==0, 'Isolated DB preparation failed (output withheld to protect credentials)'
        db.start_server()
        yield db
    finally:
        if hasattr(db,'process'): db.stop_server()
        with psycopg.connect(admin,autocommit=True) as conn:
            conn.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(name)))


@pytest.mark.asyncio
async def test_all_100_snapshots_and_six_read_operations_match(mcp_database):
    db=mcp_database
    async with AsyncConnectionPool(db.admin_dsn,open=False,kwargs={'row_factory':dict_row}) as pool:
        direct=PostgresCompanyDataReader(pool); mcp=McpCompanyDataReader(db.url)
        catalog=await direct.list_companies(limit=200)
        assert len(catalog)==100
        timings={'direct':[],'mcp':[]}
        for row in catalog:
            t=time.perf_counter(); expected=await direct.get_latest_snapshot(row['inn']); timings['direct'].append(time.perf_counter()-t)
            t=time.perf_counter(); actual=await mcp.get_latest_snapshot(row['inn']); timings['mcp'].append(time.perf_counter()-t)
            assert actual==expected
            assert {k:v.to_dict() for k,v in build_all_blocks(actual['document']).items()} == {k:v.to_dict() for k,v in build_all_blocks(expected['document']).items()}
            assert build_coverage(actual['document'])==build_coverage(expected['document'])
        assert await mcp.list_companies(limit=200)==catalog
        assert await mcp.list_companies(limit=3,offset=2,risk_level='LOW') == await direct.list_companies(limit=3,offset=2,risk_level='LOW')
        ids=[r['snapshot_id'] for r in catalog[:50]]
        assert await mcp.get_selection_snapshots(ids)==await direct.get_selection_snapshots(ids)
        assert await mcp.get_selection_snapshots([])==[]
        assert await mcp.get_latest_snapshot('0000000000') is None
        assert await mcp.get_connection_candidates()==await direct.get_connection_candidates()
        inns=['7805327192','4720028039']
        assert await mcp.get_snapshots_for_connections(inns)==await direct.get_snapshots_for_connections(inns)
        for criteria in [
            {'min_proceeds':0, 'limit':50},
            {'activity_query':'торговля','min_proceeds':10000000,'limit':1},
            {'okved_prefix':'46','activity_scope':'main'},
            {'min_proceeds':0,'ranking':[{'metric':'profit','order':'desc'}],'limit':5},
            {'min_profit':-1,'max_profit':0},
        ]:
            assert await mcp.find_companies(**criteria)==await direct.find_companies(**criteria)
        import statistics
        report={kind:{'median_ms':round(statistics.median(values)*1000,2),'max_ms':round(max(values)*1000,2)} for kind,values in timings.items()}
        Path('/tmp/mcp-data-parity.json').write_text(json.dumps({'snapshots':100,'operations':6,'timings':report},indent=2))
        print('MCP parity:',json.dumps(report))


def test_read_role_cannot_write_even_with_read_only_disabled(mcp_database):
    with psycopg.connect(mcp_database.read_dsn,autocommit=True) as conn:
        conn.execute('SET default_transaction_read_only=off')
        assert conn.execute('SELECT count(*) FROM raw.report_documents').fetchone()[0]==100
        for statement in [
            'UPDATE core.companies SET short_name=short_name WHERE false',
            'DELETE FROM raw.report_documents WHERE false',
            'SELECT * FROM audit.analysis_runs LIMIT 0',
            'CREATE TABLE core.mcp_forbidden(id int)',
        ]:
            with pytest.raises(psycopg.errors.InsufficientPrivilege): conn.execute(statement)


@pytest.mark.asyncio
async def test_restart_and_audited_mock_pipeline_via_mcp(mcp_database,monkeypatch):
    from app.infrastructure import repository, db as database
    from app.domain.pipeline import run_check
    from app.llm.groq_client import GroqClient
    db=mcp_database; reader=McpCompanyDataReader(db.url,timeout_s=3)
    db.stop_server()
    with pytest.raises(CompanySourceError): await reader.get_latest_snapshot('7805327192')
    db.start_server()
    assert (await reader.get_latest_snapshot('7805327192'))['inn']=='7805327192'
    settings=Settings(_env_file=None,database_url=db.admin_dsn,company_data_backend='mcp',mcp_server_url=db.url,llm_mock=True)
    monkeypatch.setattr('app.config.get_settings',lambda:settings)
    def forbidden(*args,**kwargs): raise AssertionError('Hidden direct SQL read')
    monkeypatch.setattr(PostgresCompanyDataReader,'_pool',forbidden)
    await database.init_pool(settings)
    client=GroqClient(settings)
    try:
        result=await run_check('7805327192',settings,client,persist=True)
        assert result['status']=='SUCCEEDED'
        saved=await repository.get_run(result['run_id'])
        assert saved and saved['inn']=='7805327192'
    finally:
        await client.aclose(); await database.close_pool()


@pytest.mark.asyncio
async def test_name_search_sql_and_mcp_parity(mcp_database):
    """Fixtures are written only to the disposable test database, never the demo DB."""
    db = mcp_database
    names = [
        ('9910000001', 'ООО «Ёлка Тест»', 'Общество с ограниченной ответственностью «Ёлка Тест»'),
        ('9910000002', 'АО «Елка Тест»', None),
        ('9910000003', 'ООО «Ёлка Тест Плюс»', None),
        ('9910000004', 'ООО «Супер Ёлка Тест»', None),
        ('9910000005', 'ООО «100%_Тест»', None),
        ('9910000006', 'ООО «100AAТест»', None),
        ('9910000007', 'ООО «Ёлка Тест»', None),
        ('9910000008', 'ООО «Ёлка Тест»', None),
        ('9910000009', 'ООО «Ёлка Тест»', None),
        ('9910000010', 'ООО «Ёлка Тест»', None),
        ('9910000011', 'ООО «Иное имя»', 'Общество с ограниченной ответственностью «Полное уникальное имя»'),
    ]
    with psycopg.connect(db.admin_dsn) as conn:
        # Applying the migration twice checks existing-DB upgrade and idempotence.
        for _ in range(2): conn.execute((ROOT/'db/migrations/008_company_name_search.sql').read_text())
        for inn, short, full in names:
            cid = conn.execute('INSERT INTO core.companies(inn,short_name,full_name) VALUES (%s,%s,%s) RETURNING id', (inn,short,full)).fetchone()[0]
            conn.execute("INSERT INTO core.report_snapshots(company_id,report_date,address) VALUES (%s,'2026-01-01','Москва')", (cid,))
    try:
        async with AsyncConnectionPool(db.admin_dsn,open=False,kwargs={'row_factory':dict_row}) as pool:
            direct = PostgresCompanyDataReader(pool)
            mcp = McpCompanyDataReader(db.url)
            for query in ['елка тест', '  ООО  «ЁЛКА   ТЕСТ»  ', 'Общество с ограниченной ответственностью Ёлка Тест',
                          'Ёлка', 'лка тест', '100%_', '100AA', 'Полное уникальное имя', 'СовсемНеСуществует', '9910000001']:
                expected = await direct.search_companies(query)
                assert await mcp.search_companies(query) == expected
            found = await direct.search_companies('елка тест')
            assert found['total'] == 8 and found['exact_total'] == 6 and len(found['rows']) == 5
            assert all(r['match'] == 'exact' for r in found['rows'])
            assert [r['inn'] for r in found['rows']] == sorted(r['inn'] for r in found['rows'])
            assert (await direct.search_companies('100%_'))['rows'][0]['inn'] == '9910000005'
            assert (await direct.search_companies('Полное уникальное имя'))['exact_total'] == 1
            assert (await direct.search_companies('  ООО  ')) == {'rows': [], 'total': 0, 'exact_total': 0}
            prefixes = await direct.search_companies('Супер Ёлка')
            assert prefixes['rows'][0]['match'] == 'prefix'
            partial = await direct.search_companies('лка тест плюс')
            assert partial['rows'][0]['match'] == 'partial'
    finally:
        with psycopg.connect(db.admin_dsn) as conn:
            conn.execute('DELETE FROM core.companies WHERE inn = ANY(%s)', ([n[0] for n in names],))
