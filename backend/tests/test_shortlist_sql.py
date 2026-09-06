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
    migration = (Path(__file__).parents[1] / 'db/migrations/004_company_shortlist.sql').read_text()
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
            cur.execute('SELECT * FROM pg_temp.v_company_shortlist ORDER BY inn')
            rows = cur.fetchall()
            for row in rows[:2]:
                assert row['claims_amount'] is None
                assert row['enforcement_count'] is None and row['hard_stops'] is None
            assert rows[2]['claims_amount'] == rows[2]['hard_stops'] == rows[2]['enforcement_count'] == 0
            cur.execute('SELECT inn FROM pg_temp.v_company_shortlist WHERE hard_stops = 0 AND claims_amount <= 0')
            assert cur.fetchall() == [{'inn': '3'}]
        conn.rollback()
