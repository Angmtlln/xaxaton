"""Run pinned defense scenarios with real models and an isolated production reader."""
from __future__ import annotations

import argparse
import asyncio
import contextvars
from contextlib import ExitStack
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
from pathlib import Path
import subprocess
import time
from unittest.mock import patch

from langchain_openai import ChatOpenAI
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.agent.conversations import ConversationStore, ConversationState
from app.agent.runtime import build_master_runtime
from app.agent.tools import ToolRegistry
from app.config import Settings
from app.infrastructure.company_postgres import PostgresCompanyDataReader
from app.llm.groq_client import GroqClient
from .bank import ROOT, SNAPSHOT, documents, sha
from .defense_bank import BANK, load
from .defense_data import temporary_database
from .defense_grade import grade

CAPTURE=contextvars.ContextVar('defense_capture',default=None)


def encode(value):
    return json.dumps(value,ensure_ascii=False,default=str,sort_keys=True)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


def save(path,value):
    temp=path.with_suffix(path.suffix+'.tmp')
    if path.suffix=='.gz':
        with gzip.open(temp,'wt',encoding='utf-8') as f: f.write(encode(value))
    else: temp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n')
    temp.replace(path)


def read(path):
    return json.loads(gzip.decompress(path.read_bytes())) if path.suffix=='.gz' else json.loads(path.read_text())


async def state(store,cid):
    if not cid: return {}
    cp=await store.checkpointer.aget_tuple({'configurable':{'thread_id':cid}})
    values=cp.checkpoint['channel_values'] if cp else {}
    # Capture all declared application state, including new pending/selection states.
    return json.loads(encode({k:values.get(k) for k in ConversationState.__annotations__}))


class CapturedReader:
    def __init__(self,reader): self.reader=reader
    def __getattr__(self,name):
        target=getattr(self.reader,name)
        async def call(*args,**kwargs):
            event=dict(method=name,args=args,kwargs=kwargs)
            row=CAPTURE.get()
            if row is not None: row['reads'].append(event)
            try:
                result=await target(*args,**kwargs)
                event['result']=json.loads(encode(result))
                return result
            except Exception as e:
                event['error']=type(e).__name__; raise
        return call


def capture(stack):
    model_original=ChatOpenAI._agenerate; domain_original=GroqClient._call_model
    tool_original=ToolRegistry.execute
    async def model(self,messages,*a,**kw):
        row=CAPTURE.get(); started=time.monotonic()
        event={'kind':'master','model':self.model_name,
               'input_sha256':digest([{'type':m.type,'content':m.content,'tool_calls':getattr(m,'tool_calls',[])} for m in messages])}
        if row is not None: row['calls'].append(event)
        try:
            result=await model_original(self,messages,*a,**kw)
            m=result.generations[0].message
            event.update(usage=m.usage_metadata,finish_reason=m.response_metadata.get('finish_reason'),
                         actual_model=m.response_metadata.get('model_name'),response_id=m.id,
                         token_usage=m.response_metadata.get('token_usage'))
            return result
        except Exception as e: event['error']=type(e).__name__; raise
        finally: event['ms']=round((time.monotonic()-started)*1000)
    async def domain(self,model_name,*a,**kw):
        row=CAPTURE.get(); started=time.monotonic(); event={'kind':'domain','model':model_name}
        if row is not None: row['calls'].append(event)
        try:
            result=await domain_original(self,model_name,*a,**kw)
            event.update(usage=result.raw.get('usage'),finish_reason=result.raw.get('choices',[{}])[0].get('finish_reason'))
            return result
        except Exception as e: event['error']=type(e).__name__; raise
        finally: event['ms']=round((time.monotonic()-started)*1000)
    async def tool(self,name,arguments,context):
        row=CAPTURE.get(); event={'name':name,'arguments':arguments}
        if row is not None: row['tools'].append(event)
        result=await tool_original(self,name,arguments,context)
        event['result']=result.model_dump(mode='json'); return result
    stack.enter_context(patch.object(ChatOpenAI,'_agenerate',model))
    stack.enter_context(patch.object(GroqClient,'_call_model',domain))
    stack.enter_context(patch.object(ToolRegistry,'execute',tool))


def select_cases(bank,args):
    cases=bank['cases']
    if args.suite=='pilot':
        ids={g+n for g in 'GFLNRSECM' for n in ('01','03')}|{'C02','M07'}
        cases=[c for c in cases if c['id'] in ids]
    elif args.suite in ('development','holdout'):
        cases=[c for c in cases if c['split']==args.suite]
    if args.case:
        unknown=set(args.case)-{c['id'] for c in cases}
        if unknown: raise ValueError(f'Unknown cases in suite: {sorted(unknown)}')
        cases=[c for c in cases if c['id'] in args.case]
    excluded=getattr(args,'exclude_case',None)
    if excluded:
        known={c['id'] for c in bank['cases']}
        unknown=set(excluded)-known
        if unknown: raise ValueError(f'Unknown excluded cases: {sorted(unknown)}')
        cases=[c for c in cases if c['id'] not in excluded]
    if not cases:
        raise ValueError('Scenario selection is empty')
    return cases


async def execute(args,bank,cases,out,db,manifest):
    settings=Settings(web_news_enabled=args.web=='live')
    if settings.llm_mock or not settings.openrouter_api_key or not settings.groq_api_key:
        raise RuntimeError('Live Master and domain credentials required; no mock fallback')
    docs=documents(); rows=[]; gate=asyncio.Semaphore(args.concurrency)
    manifest.update(master_model=settings.master_model,block_models=settings.block_models(),
                    grounding_debug=settings.agent_grounding_debug,web_mode=args.web,
                    settings={k:getattr(settings,k) for k in ('openrouter_reasoning_effort','openrouter_provider_sort',
                        'agent_model_timeout_s','agent_run_timeout_s','agent_tool_result_max_chars','agent_answer_max_tokens')},
                    data_mode='isolated_postgresql',source_verified=db['source_verified'],postgres_image=db['image'])
    save(out/'manifest.json',manifest)
    async def conversation(case,attempt):
        async with gate:
            client=GroqClient(settings); store=ConversationStore(); cid=None
            runtime=build_master_runtime(settings,client,persist=False,conversation_store=store)
            previous_row=None
            try:
                for index,spec in enumerate(case['setup']+case['turns']):
                    scored=index>=len(case['setup']); turn_index=index-len(case['setup'])+1
                    tid=f"{case['id']}.a{attempt}."+(f't{turn_index}' if scored else f'setup{index+1}')
                    row=dict(id=tid,case_id=case['id'],attempt=attempt,turn=turn_index,scored=scored,
                             spec=spec,reads=[],tools=[],calls=[],before=await state(store,cid))
                    token=CAPTURE.set(row); started=time.monotonic()
                    try:
                        choice=None
                        if spec.get('choice'):
                            pending=(previous_row or {}).get('after',{}).get('pending_company_search')
                            if not pending: raise ValueError('Choice prerequisite missing')
                            choice={'search_id':pending['search_id'],'inn':pending['rows'][spec['choice']['index']]['inn']}
                        row['request']={'message':spec['question'],'company_selection':choice}
                        response=await runtime.run(spec['question'],cid,company_selection=choice)
                        row['response']=response.model_dump(mode='json'); cid=response.conversation_id
                    except Exception as e: row['error']=type(e).__name__
                    finally: CAPTURE.reset(token)
                    row['wall_ms']=round((time.monotonic()-started)*1000)
                    row['after']=await state(store,cid); row['checks']=grade(row,spec,docs)
                    save(out/(tid+'.json.gz'),row)
                    rows.append({'id':tid,'case_id':case['id'],'attempt':attempt,'turn':turn_index,'scored':scored,
                                 'wall_ms':row['wall_ms'],'trace':tid+'.json.gz','trace_sha256':sha(out/(tid+'.json.gz')),
                                 'technical_failures':[c['name'] for c in row['checks'] if c['status']=='FAIL']})
                    save(out/'progress.json',{'rows':rows,'completed_scored_turns':sum(r['scored'] for r in rows)})
                    print(encode({'case':tid,'ms':row['wall_ms'],'failed':rows[-1]['technical_failures']}),flush=True)
                    previous_row=row
            finally: await client.aclose()
    async with AsyncConnectionPool(db['dsn'],open=False,min_size=1,max_size=max(4,args.concurrency*2),
                                    kwargs={'row_factory':dict_row}) as pool:
        reader=CapturedReader(PostgresCompanyDataReader(pool))
        with ExitStack() as stack:
            stack.enter_context(patch('app.infrastructure.repository.get_company_reader',lambda:reader))
            def forbidden_pool(): raise AssertionError('Eval must not access production/audit database')
            stack.enter_context(patch('app.infrastructure.repository.get_pool',forbidden_pool))
            capture(stack)
            await asyncio.gather(*(conversation(c,a) for c in cases for a in range(1,args.repetitions+1)))
    manifest['finished_at']=datetime.now(timezone.utc).isoformat()
    save(out/'manifest.json',manifest)


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',choices=['pilot','full','development','holdout'],default='pilot')
    p.add_argument('--case',action='append'); p.add_argument('--exclude-case',action='append')
    p.add_argument('--concurrency',type=int,choices=range(1,5),default=2)
    p.add_argument('--repetitions',type=int,choices=range(1,4),default=1)
    p.add_argument('--web',choices=['disabled','live'],default='disabled')
    p.add_argument('--output',required=True); args=p.parse_args()
    bank=load(); cases=select_cases(bank,args)
    settings=Settings()
    if settings.llm_mock or not settings.openrouter_api_key or not settings.groq_api_key:
        raise SystemExit('Live credentials required; nothing started')
    out=Path(args.output); out.mkdir(parents=True,exist_ok=False)
    save(out/'bank.json',bank)
    save(out/'source.json.gz',json.loads(SNAPSHOT.read_text()))
    code_paths=[ROOT/'backend/app',ROOT/'backend/evals',ROOT/'backend/db']
    hashes={str(f.relative_to(ROOT)):sha(f) for root in code_paths for f in sorted(root.rglob('*'))
            if f.is_file() and f.suffix in ('.py','.md','.sql') and 'results' not in f.parts}
    manifest=dict(version='defense-1',suite=args.suite,case_ids=[c['id'] for c in cases],
                  repetitions=args.repetitions,concurrency=args.concurrency,planned_scenarios=len(cases),
                  planned_attempts=len(cases)*args.repetitions,
                  planned_scored_turns=sum(len(c['turns']) for c in cases)*args.repetitions,
                  bank_sha256=sha(BANK),snapshot_sha256=sha(SNAPSHOT),code_hashes=hashes,
                  git_commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
                  git_dirty=bool(subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip()),
                  started_at=datetime.now(timezone.utc).isoformat())
    save(out/'manifest.json',manifest)
    logging.basicConfig(level=logging.ERROR)
    with temporary_database() as db: asyncio.run(execute(args,bank,cases,out,db,manifest))
    from .defense_report import report
    report(out)


if __name__=='__main__': main()
