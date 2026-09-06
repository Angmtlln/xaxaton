"""SQL-подборка на изолированных временных таблицах PostgreSQL.

TEST_SHORTLIST_DATABASE_URL=... PYTHONPATH=. .venv/bin/python -m pytest -q tests/test_shortlist_sql.py
Никаких записей в core/raw: все fixtures живут в pg_temp и откатываются.
"""
import os
from pathlib import Path

import pytest


def test_shortlist_view_preserves_unknowns_and_filters_verified_zero():
    dsn = os.getenv('TEST_SHORTLIST_DATABASE_URL')
    if not dsn:
        pytest.skip('Требуется TEST_SHORTLIST_DATABASE_URL для PostgreSQL integration')
    import psycopg
    from psycopg.types.json import Jsonb
    from psycopg.rows import dict_row
    migration = (Path(__file__).parents[1] / 'db/migrations/006_shortlist_ranking.sql').read_text()
    with psycopg.connect(dsn, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            for name in ['core.fin_reports', 'core.reputational_risks', 'core.risk_code_dictionary',
                         'core.execution_proceedings', 'core.v_latest_snapshots', 'core.companies',
                         'core.arbitration_summary', 'raw.report_documents']:
                table = name.split('.')[1]
                cur.execute(f'CREATE TEMP TABLE {table} AS SELECT * FROM {name} WITH NO DATA')
            for index, report in enumerate([{}, {'executionProceedings': None}, {
                'executionProceedings': [], 'reputationalRisks': {'negative': []},
                'arbitrationByStatus': {'defandantArbitration': {
                    'defandantArbitrationFinished': {'dfAmount': 0},
                    'defandantArbitrationAppealed': {'daAmount': 0},
                    'defandantArbitrationPending': {'dpAmount': 0},
                }},
            }], 1):
                cur.execute('INSERT INTO companies (id, inn, short_name) VALUES (%s, %s, %s)',
                            (index, str(index), f'Company {index}'))
                cur.execute('INSERT INTO report_documents (id, document) VALUES (%s, %s)',
                            (index, Jsonb({'report': report})))
                cur.execute('INSERT INTO v_latest_snapshots (id, company_id, raw_document_id) VALUES (%s,%s,%s)',
                            (index, index, index))
                cur.execute('INSERT INTO fin_reports (snapshot_id, year, proceeds) VALUES (%s, 2024, 100)', (index,))
                # Even imported zeros must not erase missing source fields.
                cur.execute('INSERT INTO arbitration_summary (snapshot_id, df_amount, da_amount, dp_amount) VALUES (%s,0,0,0)', (index,))
            cur.execute(migration.replace('core.', 'pg_temp.').replace('raw.', 'pg_temp.'))
            cur.execute(migration.replace('core.', 'pg_temp.').replace('raw.', 'pg_temp.'))
            cur.execute('SELECT * FROM pg_temp.v_company_shortlist ORDER BY inn')
            rows = cur.fetchall()
            assert all('report_date' in row and 'snapshot_id' in row for row in rows)
            for row in rows[:2]:
                assert row['claims_amount'] is None
                assert row['enforcement_count'] is None and row['hard_stops'] is None
            assert rows[2]['claims_amount'] == rows[2]['hard_stops'] == rows[2]['enforcement_count'] == 0
            cur.execute('SELECT inn FROM pg_temp.v_company_shortlist WHERE hard_stops = 0 AND claims_amount <= 0')
            assert cur.fetchall() == [{'inn': '3'}]
        conn.rollback()


@pytest.mark.asyncio
async def test_activity_repository_filters_before_limit_and_counts_companies(monkeypatch):
    dsn = os.getenv('TEST_SHORTLIST_DATABASE_URL')
    if not dsn:
        pytest.skip('Требуется TEST_SHORTLIST_DATABASE_URL для PostgreSQL integration')
    import psycopg
    from psycopg.rows import dict_row
    from contextlib import asynccontextmanager
    from app.infrastructure import repository
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        await conn.execute('CREATE TEMP TABLE v_company_shortlist AS SELECT * FROM core.v_company_shortlist WITH NO DATA')
        await conn.execute('CREATE TEMP TABLE activity_codes AS SELECT * FROM core.activity_codes WITH NO DATA')
        for sid, proceeds in [(1, 20_000_000), (2, 30_000_000), (3, 5_000_000), (4, 100_000_000)]:
            await conn.execute('INSERT INTO v_company_shortlist (snapshot_id, inn, short_name, proceeds) VALUES (%s,%s,%s,%s)',
                               (sid, str(sid), 'Компания '+str(sid), proceeds))
        for sid, code, desc, main, idx in [
            (1,'46.73','Торговля оптовая строительными материалами',True,0),
            (1,'46.73.1','Торговля оптовая лесоматериалами',False,1),
            (2,'41.20','Строительство жилых и нежилых зданий',True,0),
            (2,'46.31','Торговля оптовая фруктами и овощами',False,1),
            (3,'47.11','Торговля розничная продуктами',True,0),
            (4,'41.20','Строительство жилых и нежилых зданий',True,0),
        ]:
            await conn.execute('INSERT INTO activity_codes (snapshot_id,code,description,is_main,idx) VALUES (%s,%s,%s,%s,%s)',
                               (sid,code,desc,main,idx))
        class Cursor:
            async def __aenter__(self):
                self.cursor = conn.cursor()
                return self
            async def __aexit__(self, *args): await self.cursor.close()
            async def execute(self, sql, params):
                await self.cursor.execute(sql.replace('core.', 'pg_temp.'), params)
            async def fetchone(self): return await self.cursor.fetchone()
            async def fetchall(self): return await self.cursor.fetchall()
        class Pool:
            @asynccontextmanager
            async def connection(self): yield self
            def cursor(self): return Cursor()
        monkeypatch.setattr('app.infrastructure.company_postgres.get_pool', lambda: Pool())
        result = await repository.find_companies(activity_query='торговлей', min_proceeds=10_000_000, limit=1)
        assert result['total'] == 2 and [r['inn'] for r in result['rows']] == ['2']
        match = result['rows'][0]['matched_activities'][0]
        assert match['code'] == '46.31' and match['is_main'] is False
        assert match['field_ref'].endswith('otherKindsOfActivity[0]')
        main = await repository.find_companies(activity_query='торговля', activity_scope='main', min_proceeds=10_000_000)
        assert main['total'] == 1 and main['rows'][0]['inn'] == '1'
        prefix = await repository.find_companies(okved_prefix='46.7')
        assert prefix['total'] == 1 and len(prefix['rows'][0]['matched_activities']) == 2
        empty = await repository.find_companies(activity_query='разведение верблюдов', min_proceeds=1)
        assert empty == {'total': 0, 'rows': []}
        # Same company does not satisfy keywords spread across unrelated activity codes.
        strict = await repository.find_companies(activity_query='строительство фруктами')
        assert strict['total'] == 0
        # The initial LIMIT=1 showed company 2. Ranking must still find company 1.
        await conn.execute("UPDATE v_company_shortlist SET profit = CASE inn WHEN '1' THEN -1 WHEN '2' THEN -5 END")
        await conn.execute("UPDATE v_company_shortlist SET proceeds = 15000000 WHERE inn = '3'")
        rank = [{'metric': 'profit', 'order': 'desc'}]
        best = await repository.find_companies(activity_query='торговлей', min_proceeds=10000000, ranking=rank, limit=1)
        assert best['total'] == 3 and best['eligible_total'] == 2
        assert [r['inn'] for r in best['rows']] == ['1']
        worst = await repository.find_companies(activity_query='торговлей', ranking=[{'metric':'profit','order':'asc'}], limit=1)
        assert worst['rows'][0]['inn'] == '2'
        await conn.execute("UPDATE v_company_shortlist SET profit = -1, claims_amount = CASE inn WHEN '1' THEN 20 ELSE 10 END, enforcement_count = CASE inn WHEN '1' THEN 2 ELSE 1 END WHERE inn IN ('1', '2')")
        secondary = await repository.find_companies(activity_query='торговлей', ranking=rank + [{'metric':'claims','order':'asc'}])
        assert [r['inn'] for r in secondary['rows']] == ['2', '1']
        by_exec = await repository.find_companies(activity_query='торговлей', ranking=[{'metric':'enforcement','order':'asc'}])
        assert [r['inn'] for r in by_exec['rows']] == ['2', '1']
        by_revenue = await repository.find_companies(activity_query='торговлей', ranking=[{'metric':'proceeds','order':'desc'}])
        assert [r['inn'] for r in by_revenue['rows']] == ['2', '1', '3']
        tied = await repository.find_companies(activity_query='торговлей', ranking=rank)
        assert [r['inn'] for r in tied['rows']] == ['1', '2']
        missing = await repository.find_companies(min_proceeds=50000000, ranking=rank)
        assert missing == {'total': 1, 'eligible_total': 0, 'rows': []}
        await conn.rollback()


@pytest.mark.asyncio
async def test_selection_reads_exact_snapshot_ids_and_bounds_batch(monkeypatch):
    """Two reports for one company: selection must not silently switch to newer data."""
    dsn = os.getenv('TEST_SHORTLIST_DATABASE_URL')
    if not dsn:
        pytest.skip('Требуется TEST_SHORTLIST_DATABASE_URL для PostgreSQL integration')
    import psycopg
    from psycopg.rows import dict_row
    from psycopg.types.json import Jsonb
    from contextlib import asynccontextmanager
    from app.infrastructure import repository
    async with await psycopg.AsyncConnection.connect(dsn, row_factory=dict_row) as conn:
        for name in ['core.report_snapshots', 'core.companies', 'raw.report_documents']:
            await conn.execute(f'CREATE TEMP TABLE {name.split(".")[1]} AS SELECT * FROM {name} WITH NO DATA')
        await conn.execute("INSERT INTO companies (id,inn,short_name) VALUES (1,'6165169320','Компания')")
        for sid, date in [(1,'2024-01-01'),(2,'2025-01-01')]:
            await conn.execute('INSERT INTO report_documents (id,document) VALUES (%s,%s)',(sid,Jsonb({'version':sid})))
            await conn.execute('INSERT INTO report_snapshots (id,company_id,raw_document_id,report_date) VALUES (%s,1,%s,%s)',(sid,sid,date))
        class Cursor:
            async def __aenter__(self):
                self.cur=conn.cursor()
                return self
            async def __aexit__(self,*args): await self.cur.close()
            async def execute(self,sql,params):
                await self.cur.execute(sql.replace('core.','pg_temp.').replace('raw.','pg_temp.'),params)
            async def fetchall(self): return await self.cur.fetchall()
        class Pool:
            @asynccontextmanager
            async def connection(self): yield self
            def cursor(self): return Cursor()
        monkeypatch.setattr('app.infrastructure.company_postgres.get_pool',lambda:Pool())
        selected=await repository.get_selection_snapshots([1])
        assert len(selected)==1 and selected[0]['snapshot_id']==1
        assert selected[0]['document']=={'version':1}
        assert len(await repository.get_selection_snapshots([1,2]))==2
        assert await repository.get_selection_snapshots([])==[]
        with pytest.raises(ValueError):
            await repository.get_selection_snapshots(list(range(51)))
        await conn.rollback()
