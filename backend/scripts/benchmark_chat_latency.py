"""Live six-turn waterfall; run from backend with real DB/provider credentials.

Instrumentation is process-local and does not alter provider responses. Timers
are nested: do not add domain/tool spans to the HTTP calls inside them.
BENCH_OUTPUT chooses JSON output; BENCH_CAPTURE_PROMPTS=1 saves prompt fixtures
locally for replay (never credentials or hidden reasoning). BENCH_INCLUDE_SUMMARY=1
re-enables only the legacy Summary for an intermediate comparison measurement.
"""
import asyncio
import inspect
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_openai import ChatOpenAI
from app.config import Settings
from app.agent.runtime import build_master_runtime
from app.agent.tools import ToolRegistry
from app.infrastructure import repository, db
from app.llm.groq_client import GroqClient
from app.llm import agents
out = Path(os.environ.get('BENCH_OUTPUT', '/tmp/chat-latency.json'))
out.parent.mkdir(parents=True, exist_ok=True)
rows = []
events = []
origin = 0
captured = []

def event(stage, start, **kw):
    events.append(dict(stage=stage, start_ms=round((start - origin) * 1000), latency_ms=round((time.perf_counter() - start) * 1000), **kw))
orig_chat = ChatOpenAI._agenerate

async def chat(self, messages, *a, **kw):
    start = time.perf_counter()
    system = str(messages[0].content)
    stage = 'verifier' if 'Ты узкий grounding verifier' in system else 'repair' if 'единственную repair' in system else 'synthesis' if 'verified_context (проверенные' in system else 'routing'
    item = dict(stage=stage, messages=[dict(role=m.type, content=m.content) for m in messages], kwargs={k: v for k, v in kw.items() if k in ('max_tokens', 'response_format', 'extra_body')})
    captured.append(item)
    try:
        result = await orig_chat(self, messages, *a, **kw)
        m = result.generations[0].message
        event(stage, start, provider='openrouter', model=self.model_name, usage=m.usage_metadata, metadata=m.response_metadata, finish_reason=result.generations[0].generation_info, answer=m.content)
        return result
    except BaseException as e:
        event(stage, start, provider='openrouter', model=self.model_name, error=type(e).__name__)
        raise
orig_groq = GroqClient._call_model

async def groq(self, model, system, user, *a, **kw):
    start = time.perf_counter()
    try:
        r = await orig_groq(self, model, system, user, *a, **kw)
        event('domain_http', start, provider='groq', model=model, usage=r.raw.get('usage'), finish_reason=r.raw['choices'][0].get('finish_reason'))
        return r
    except BaseException as e:
        event('domain_http', start, provider='groq', model=model, error=type(e).__name__)
        raise
orig_tool = ToolRegistry.execute

async def tool(self, name, arguments, context):
    start = time.perf_counter()
    r = await orig_tool(self, name, arguments, context)
    event('tool', start, tool=name, status=r.status)
    return r
orig_block = agents.run_block_agent

async def block(*a, **kw):
    start = time.perf_counter()
    r = await orig_block(*a, **kw)
    event('domain_' + r.block, start, model=r.model, error=r.error)
    return r

def wrap(fn, name):

    async def f(*a, **kw):
        start = time.perf_counter()
        try:
            return await fn(*a, **kw)
        finally:
            event(name, start)
    return f

async def main():
    global origin, events, captured
    s = Settings()
    if s.llm_mock or not s.openrouter_api_key or (not s.groq_api_key):
        raise RuntimeError('Live benchmark requires OpenRouter/Groq credentials and LLM_MOCK=false')
    await db.init_pool(s)
    client = GroqClient(s)
    runtime = build_master_runtime(s, client, persist=True)
    cid = None
    try:
        for i, q in enumerate(['Проверь контрагента 6165169320', 'Почему это вообще плохо?', 'Объясни проще', 'А что у них с финансами?', 'А с судами?', 'Сравни 6165169320, 2901324364 и 0278949271'], 1):
            events = []
            captured = []
            origin = time.perf_counter()
            r = await runtime.run(q, cid)
            cid = r.conversation_id
            row = dict(turn=i, question=q, wall_ms=round((time.perf_counter() - origin) * 1000), response=r.model_dump(mode='json'), events=sorted(events, key=lambda e: e['start_ms']))
            rows.append(row)
            out.write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            if os.environ.get('BENCH_CAPTURE_PROMPTS') == '1':
                out.with_name(out.stem + f'-prompts-{i}.json').write_text(json.dumps(captured, ensure_ascii=False, indent=2))
            print(json.dumps({k: row[k] for k in ('turn', 'wall_ms')} | {'metadata': row['response']['metadata']}, ensure_ascii=False), flush=True)
    finally:
        await client.aclose()
        await db.close_pool()

def run():
    with patch.object(ChatOpenAI, '_agenerate', chat), patch.object(GroqClient, '_call_model', groq), patch.object(ToolRegistry, 'execute', tool), patch.object(agents, 'run_block_agent', block):
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, fn in inspect.getmembers(repository, inspect.iscoroutinefunction):
                stack.enter_context(patch.object(repository, name, wrap(fn, 'db.' + name)))
            import app.domain.pipeline as p
            stack.enter_context(patch.object(p, 'run_summary_agent', wrap(p.run_summary_agent, 'legacy_summary')))
            if os.environ.get('BENCH_INCLUDE_SUMMARY') == '1':
                import app.agent.tools as tool_module
                original_check = tool_module.run_check

                async def with_summary(*args, **kwargs):
                    kwargs['include_summary'] = True
                    return await original_check(*args, **kwargs)
                stack.enter_context(patch.object(tool_module, 'run_check', with_summary))
            asyncio.run(main())
    if any(row["response"]["metadata"]["synthesis"] != "model" for row in rows):
        raise SystemExit("Benchmark contains model fallback; inspect the saved waterfall")


if __name__ == '__main__':
    run()
