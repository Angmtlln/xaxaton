"""Disposable PostgreSQL with production schema/loader/reader; never the demo DB."""
from __future__ import annotations

from contextlib import contextmanager
import json
import subprocess
import time
import uuid

import psycopg
from psycopg.rows import dict_row

from .bank import ROOT, SNAPSHOT, normalize, documents


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True, stderr=subprocess.PIPE).strip()


@contextmanager
def temporary_database(image='postgres:16-alpine'):
    """No host volumes, random loopback port, tmpfs data destroyed with this container."""
    name='xaxaton-eval-'+uuid.uuid4().hex[:12]
    docker('run','-d','--name',name,'--label','xaxaton.disposable-eval=true',
           '--tmpfs','/var/lib/postgresql/data','-p','127.0.0.1::5432',
           '-e','POSTGRES_HOST_AUTH_METHOD=trust',image)
    try:
        port=json.loads(docker('inspect',name))[0]['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort']
        dsn=f'postgresql://postgres@127.0.0.1:{port}/postgres'
        for attempt in range(60):
            try:
                with psycopg.connect(dsn,connect_timeout=1): pass
                break
            except psycopg.OperationalError:
                if attempt==59: raise
                time.sleep(.25)
        # Same schema and ingestion function as the application, in a fresh database.
        from scripts.load_snapshot import load_document
        with psycopg.connect(dsn,row_factory=dict_row) as conn:
            conn.execute((ROOT/'backend/db/schema.sql').read_text())
            conn.execute((ROOT/'backend/db/seed_dictionary.sql').read_text())
            with conn.cursor() as cur:
                for doc in json.loads(SNAPSHOT.read_text()):
                    load_document(cur,doc,SNAPSHOT.name)
            actual={r['inn']:normalize(r['document']) for r in conn.execute('SELECT inn,document FROM raw.report_documents')}
            if actual != documents(): raise ValueError('Imported source differs from pinned snapshot')
            conn.execute('CREATE ROLE eval_reader LOGIN')
            conn.execute('GRANT USAGE ON SCHEMA core,raw TO eval_reader')
            conn.execute('GRANT SELECT ON ALL TABLES IN SCHEMA core,raw TO eval_reader')
            conn.execute('ALTER ROLE eval_reader SET default_transaction_read_only=on')
        yield dict(dsn=f'postgresql://eval_reader@127.0.0.1:{port}/postgres',
                   image=image,container=name,documents=len(actual),source_verified=True)
    finally:
        docker('rm','-f',name)
