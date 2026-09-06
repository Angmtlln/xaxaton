"""Read-only live selection acceptance; records responses, usage and stage timing."""
import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.agent.runtime import build_master_runtime
from app.config import Settings
from app.infrastructure.db import init_pool, close_pool
from app.llm.groq_client import GroqClient


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--message', action='append')
    cli = parser.parse_args()
    settings = Settings()
    if settings.llm_mock or not settings.openrouter_api_key:
        raise SystemExit('Live Master is not configured; no acceptance claim made.')
    await init_pool(settings)
    rows = []
    try:
        runtime = build_master_runtime(settings, GroqClient(settings), persist=False)
        cid = None
        for message in cli.message or [
            'Подбери 3 лучших поставщиков: компании занимающиеся торговлей и с выручкой от 100 млн. Работа без аванса, важна устойчивость поставок.',
            'Почему эти?',
        ]:
            response = await runtime.run(message, cid)
            cid = response.conversation_id
            rows.append({'request': message, 'response': response.model_dump(mode='json')})
            Path(cli.output).write_text(json.dumps(rows, ensure_ascii=False, indent=2))
            print(json.dumps({'request': message, 'metadata': response.metadata.model_dump(),
                              'message': response.message}, ensure_ascii=False), flush=True)
    finally:
        await close_pool()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    asyncio.run(main())
