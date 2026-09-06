"""Provision read-only MCP access after schema setup; credentials are environment-only."""
import os
from pathlib import Path

import psycopg
from psycopg import sql


def setup():
    password = os.environ.get('MCP_DB_PASSWORD')
    if not password:
        raise ValueError('Set MCP_DB_PASSWORD in the environment')
    with psycopg.connect(os.environ['DATABASE_URL']) as conn:
        conn.execute((Path(__file__).resolve().parents[1] / 'db/migrations/007_mcp_read_access.sql').read_text())
        conn.execute((Path(__file__).resolve().parents[1] / 'db/migrations/008_company_name_search.sql').read_text())
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname='contractors_mcp'").fetchone()
        if not exists:
            conn.execute('CREATE ROLE contractors_mcp LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION')
        # Refuse to repurpose an existing privileged account.
        privileged = conn.execute("""SELECT rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls
                                      FROM pg_roles WHERE rolname='contractors_mcp'""").fetchone()[0]
        if privileged:
            raise ValueError('The existing MCP account has unexpected privileges')
        conn.execute(sql.SQL('ALTER ROLE contractors_mcp PASSWORD {}').format(sql.Literal(password)))
        conn.execute('GRANT contractors_mcp_reader TO contractors_mcp')
        conn.execute("ALTER ROLE contractors_mcp SET default_transaction_read_only = on")
    print('MCP read access configured')


if __name__ == '__main__':
    try:
        setup()
    except Exception as exc:
        # Exception messages from drivers may include credentials or SQL literals.
        raise SystemExit('MCP access setup failed (%s); check schema and environment' % type(exc).__name__) from None
