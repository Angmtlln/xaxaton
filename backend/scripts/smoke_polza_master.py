"""Live Polza probes plus the seven-turn grounded conversation smoke."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen

from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.master_model import build_master_model  # noqa: E402
from app.config import Settings  # noqa: E402


class StructuredProbe(BaseModel):
    status: Literal["ok"]
    language: Literal["ru"]


async def probe_provider(settings: Settings) -> list[dict]:
    if settings.master_provider != "polza":
        raise RuntimeError("Для smoke задайте MASTER_PROVIDER=polza")
    if not settings.polza_api_key:
        raise RuntimeError("POLZA_API_KEY не задан; live Polza smoke не выполнялся")
    model = build_master_model(settings)
    if model is None:
        raise RuntimeError("Polza Master model отключён конфигурацией")

    results = []
    started = time.perf_counter()
    message = await model.ainvoke("Ответь одним словом по-русски: готово")
    results.append({"probe": "text_completion", "ok": bool(message.text.strip()),
                    "latency_ms": round((time.perf_counter() - started) * 1000)})

    started = time.perf_counter()
    structured = await model.with_structured_output(
        StructuredProbe, method="json_schema"
    ).ainvoke("Верни status=ok и language=ru")
    results.append({"probe": "structured_output",
                    "ok": structured.status == "ok" and structured.language == "ru",
                    "latency_ms": round((time.perf_counter() - started) * 1000)})
    return results


def probe_conversation(base_url: str, inn: str) -> list[dict]:
    conversation_id = None
    results = []
    messages = [
        "Проверь контрагента " + inn,
        "А что у них с финансами?",
        "Почему это вообще плохо?",
        "Объясни проще",
        "Насколько это критично для сделки с отсрочкой?",
        "А что с судами?",
        "Что здесь самое неприятное?",
    ]
    for index, message in enumerate(messages, start=1):
        request = Request(
            base_url.rstrip("/") + "/api/v1/chat/messages",
            data=json.dumps({"message": message, "conversation_id": conversation_id}).encode(),
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        with urlopen(request, timeout=190) as response:
            payload = json.load(response)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        conversation_id = payload["conversation_id"]
        metadata = payload["metadata"]
        tool_turn = index in (1, 2, 6)
        expected_tools = 1 if tool_turn else 0
        provider_ok = (
            metadata["model"] == "z-ai/glm-5.3-flash"
            and metadata["routing"] == "model"
            and metadata["tool_calls"] == expected_tools
            and 1 <= metadata["model_calls"] <= 5
            and metadata["grounding_status"] in (
                ("skipped_rewrite",) if index == 4 else ("verified", "repaired")
            )
        )
        results.append({
            "turn": index,
            "message": message,
            "answer": payload["message"],
            "status": metadata["status"],
            "provider_ok": provider_ok,
            "routing": metadata["routing"],
            "synthesis": metadata["synthesis"],
            "grounding_status": metadata["grounding_status"],
            "repair_attempts": metadata["repair_attempts"],
            "model_calls": metadata["model_calls"],
            "tool_calls": metadata["tool_calls"],
            "latency_ms": metadata.get("latency_ms", elapsed_ms),
            "wall_latency_ms": elapsed_ms,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--inn", default="6165169320")
    args = parser.parse_args()

    settings = Settings()
    provider = asyncio.run(probe_provider(settings))
    conversation = probe_conversation(args.base_url, args.inn)
    output = {"provider": provider, "conversation": conversation}
    print(json.dumps(output, ensure_ascii=False, indent=2), flush=True)

    failed_provider = [item for item in provider if not item["ok"]]
    failed_turns = [item for item in conversation if not item["provider_ok"]]
    if failed_provider or failed_turns:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
