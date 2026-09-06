"""Separate-process source fixture, never used by the application."""
import asyncio
from datetime import datetime, date, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import uvicorn
from app.mcp_data.server import create_app

DOCUMENT = json.loads((Path(__file__).resolve().parents[2] / 'contractors_audit.snapshot.json').read_text())[0]
INN = DOCUMENT['report']['baseInfo']['inn']
SNAPSHOT = dict.fromkeys(('address email website company_size registration_date years_from_registration '
                         'status status_reason status_date risk_level zsk_risk_level ogrn kpp okpo short_name full_name').split())
SNAPSHOT.update(snapshot_id=1, company_id=1, inn=INN, report_date=datetime(2026,9,6,tzinfo=timezone.utc),
                registration_date=date(2000,1,1), document=DOCUMENT)


class FixtureReader:
    async def get_latest_snapshot(self, inn):
        if inn == '0000000000': return None
        if inn == '1111111111': await asyncio.sleep(2)
        if inn == '2222222222': raise RuntimeError('secret should never leave server')
        if inn == '3333333333': return {**SNAPSHOT, 'inn': inn, 'document': {'large': 'x' * (9*1024*1024)}}
        if inn == '4444444444': return {'invalid': 'upstream corruption'}
        return {**SNAPSHOT, 'inn': inn}

    async def get_selection_snapshots(self, snapshot_ids): return [SNAPSHOT] if 1 in snapshot_ids else []
    async def list_companies(self, **kwargs):
        return [{k: SNAPSHOT[k] for k in ('inn','short_name','snapshot_id','report_date','risk_level','zsk_risk_level')} |
                {'filled_blocks': 0, 'negative_count': 0}]
    async def search_companies(self, query, limit=5):
        return {'rows': [{'inn': INN, 'name': 'Тест', 'full_name': None, 'address': None,
                          'snapshot_id': 1, 'match': 'exact'}], 'total': 1, 'exact_total': 1}
    async def find_companies(self, **kwargs):
        return {'total': 1, 'rows': [{k: SNAPSHOT[k] for k in ('inn','short_name','snapshot_id','report_date','risk_level','zsk_risk_level')} |
            {'fin_year': 2025, 'proceeds': Decimal('999999999999999999.99'), 'profit': Decimal('-1.01'),
             'claims_amount': None, 'enforcement_count': 0, 'hard_stops': None, 'matched_activities': []}]}
    async def get_connection_candidates(self, limit=10001):
        return [{k: SNAPSHOT[k] for k in ('inn','snapshot_id','report_date','document')}]
    async def get_snapshots_for_connections(self, inns): return [SNAPSHOT] if INN in inns else []
    async def data_source_status(self): return {'database': True}


if __name__ == '__main__':
    uvicorn.run(create_app(FixtureReader()), host='127.0.0.1', port=int(sys.argv[1]), log_level='critical', access_log=False)
