"""Bounded live replay over the fixed local snapshot, no DB writes.
Run from backend: PYTHONPATH=. BENCH_OUTPUT=/tmp/playbook.json .venv/bin/python scripts/benchmark_playbook.py
BENCH_MODE=full runs the supplemental full-check/cache sequence.
"""
import asyncio,json,os,time,hashlib
from pathlib import Path
from unittest.mock import patch
from langchain_openai import ChatOpenAI
from app.agent.runtime import build_master_runtime
from app.config import Settings
from app.llm.groq_client import GroqClient
D=json.loads(Path('../contractors_audit.snapshot.json').read_text())
def snapshot(d):
 r=d['report']; b=r['baseInfo']; reg=b.get('registrationInfo') or {}
 return dict(document=d,inn=b['inn'],ogrn=b.get('ogrn'),short_name=b.get('shortName'),full_name=b.get('fullName'),address=b.get('address'),status=(r.get('status') or {}).get('status'),risk_level=b.get('riskLevel'),zsk_risk_level=r.get('zskRiskLevel'),report_date=r.get('reportDate'),years_from_registration=reg.get('yearsFromRegistration'))
S={d['report']['baseInfo']['inn']:snapshot(d) for d in D}
async def get(inn):return S.get(inn)
orig=ChatOpenAI._agenerate
calls=[]
async def capture(self,messages,*args,**kw):
 t=time.perf_counter()
 result=await orig(self,messages,*args,**kw)
 m=result.generations[0].message
 calls.append(dict(kind='master',ms=round((time.perf_counter()-t)*1000),usage=m.usage_metadata,finish_reason=m.response_metadata.get('finish_reason'),input_chars=sum(len(str(x.content)) for x in messages)))
 return result
original_domain = GroqClient._call_model
async def capture_domain(self, model, *args, **kwargs):
 t=time.perf_counter()
 result=await original_domain(self, model, *args, **kwargs)
 calls.append(dict(kind='domain',model=model,ms=round((time.perf_counter()-t)*1000),usage=result.raw.get('usage')))
 return result
async def main():
 s=Settings(); print({'llm_mock':s.llm_mock,'master_model':s.master_model,'openrouter_configured':bool(s.openrouter_api_key)},flush=True)
 if s.llm_mock or not s.openrouter_api_key:raise RuntimeError('Live not configured')
 client=GroqClient(s); runtime=build_master_runtime(s,client,persist=False)
 questions=['Разбери баланс и ликвидность компании 6165169320','Почему?','Покажи судебную динамику компании 6165169320','Сравни финансы компаний 6165169320 и 1684017097','Я продаю компании 6165169320 с отсрочкой. На что обратить внимание?','Теперь я покупаю у компании 6165169320 с авансом. Что меняется?']
 if os.environ.get('BENCH_MODE') == 'full':
  questions=['Проверь контрагента 6165169320','Что с оборотными активами и краткосрочными обязательствами?','Почему это важно?']
 rows=[]; cid=None
 try:
  with patch('app.infrastructure.repository.get_latest_snapshot',get),patch.object(ChatOpenAI,'_agenerate',capture),patch.object(GroqClient,'_call_model',capture_domain):
   for q in questions:
    calls.clear(); t=time.perf_counter(); r=await runtime.run(q,cid); cid=r.conversation_id
    row={'q':q,'wall_ms':round((time.perf_counter()-t)*1000),'metadata':r.metadata.model_dump(mode='json'),'message':r.message,'calls':list(calls)};rows.append(row)
    Path(os.environ['BENCH_OUTPUT']).write_text(json.dumps({'snapshot_sha256':hashlib.sha256(Path('../contractors_audit.snapshot.json').read_bytes()).hexdigest(),'model':s.master_model,'rows':rows},ensure_ascii=False,indent=2))
    print(json.dumps(row,ensure_ascii=False),flush=True)
 finally:await client.aclose()
 if any(row['metadata']['synthesis'] != 'model' for row in rows):
  raise SystemExit('Live replay contains fallback; it is not a successful speedup')

if __name__ == '__main__':
 asyncio.run(main())
