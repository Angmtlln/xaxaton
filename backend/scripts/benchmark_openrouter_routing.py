"""Replay a captured real synthesis prompt with three OpenRouter routing policies."""
import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from app.agent.master_model import build_master_model
from app.config import Settings


async def benchmark(args):
    settings = Settings()
    model = build_master_model(settings)
    if model is None:
        raise RuntimeError('Live OpenRouter model is required')
    fixture = next(item for item in json.loads(args.prompt_file.read_text())
                   if item['stage'] == 'synthesis')
    roles = {'system': SystemMessage, 'human': HumanMessage, 'ai': AIMessage}
    messages = [roles[item['role']](content=item['content']) for item in fixture['messages']]
    policies = [('throughput', {'sort': 'throughput'}),
                ('latency', {'sort': 'latency'}),
                ('latency_cap', {'sort': 'latency', 'preferred_max_latency': 3.0})]
    rows = []
    for repeat in range(args.repeats):
        for name, provider in policies if repeat % 2 == 0 else reversed(policies):
            started = time.perf_counter()
            row = {'round': repeat, 'mode': name, 'model': settings.master_model}
            try:
                result = await asyncio.wait_for(model.ainvoke(
                    messages, max_tokens=settings.answer_max_tokens(),
                    response_format={'type': 'json_object'},
                    extra_body={**(model.extra_body or {}), 'provider': provider},
                ), timeout=settings.agent_model_timeout_s)
                row.update(usage=result.usage_metadata, metadata=result.response_metadata,
                           answer=result.content)
            except Exception as exc:
                row['error'] = type(exc).__name__
            row['ms'] = round((time.perf_counter() - started) * 1000)
            rows.append(row)
            args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
            print({key: row[key] for key in ('round', 'mode', 'ms')}, flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prompt-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, default=Path('/tmp/openrouter-routing.json'))
    parser.add_argument('--repeats', type=int, default=1)
    args = parser.parse_args()
    asyncio.run(benchmark(args))
