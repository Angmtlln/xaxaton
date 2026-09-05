"""Sequential OpenRouter probes plus the seven-turn grounded conversation smoke."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Literal
from urllib.request import Request, urlopen

from langchain_core.tools import tool
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent.master_model import build_master_model  # noqa: E402
from app.config import Settings  # noqa: E402


class StructuredProbe(BaseModel):
    status: Literal["ok"]
    language: Literal["ru"]


@tool
def openrouter_tool_probe(value: Literal["ok"]) -> str:
    """Return a fixed confirmation for an OpenRouter tool-calling smoke test."""
    return "confirmed:" + value


def response_details(message) -> dict:
    metadata = message.response_metadata or {}
    return {
        "finish_reason": metadata.get("finish_reason"),
        "response_model": metadata.get("model_name") or metadata.get("model"),
    }


async def probe_provider(settings: Settings) -> list[dict]:
    if not settings.openrouter_api_key:
        raise RuntimeError(
            "OPENROUTER_API_KEY не задан; live OpenRouter smoke не выполнялся"
        )
    model = build_master_model(settings)
    if model is None:
        raise RuntimeError("OpenRouter Master model отключён конфигурацией")

    results = []
    started = time.perf_counter()
    message = await model.ainvoke("Ответь одним словом по-русски: готово")
    results.append({
        "probe": "text_completion",
        "ok": bool(message.text.strip()),
        "model_calls": 1,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        **response_details(message),
    })

    started = time.perf_counter()
    structured_result = await model.with_structured_output(
        StructuredProbe, method="json_schema", include_raw=True
    ).ainvoke("Верни status=ok и language=ru")
    structured = structured_result["parsed"]
    raw_structured = structured_result["raw"]
    results.append({
        "probe": "structured_output",
        "ok": (
            structured is not None
            and structured.status == "ok"
            and structured.language == "ru"
            and structured_result["parsing_error"] is None
        ),
        "model_calls": 1,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        **response_details(raw_structured),
    })

    started = time.perf_counter()
    tool_message = await model.bind_tools(
        [openrouter_tool_probe], tool_choice="required", parallel_tool_calls=False
    ).ainvoke("Вызови openrouter_tool_probe со значением ok.")
    calls = tool_message.tool_calls
    tool_result = openrouter_tool_probe.invoke(calls[0]["args"]) if len(calls) == 1 else None
    results.append({
        "probe": "langchain_tool_calling",
        "ok": (
            len(calls) == 1
            and calls[0]["name"] == "openrouter_tool_probe"
            and calls[0]["args"] == {"value": "ok"}
            and tool_result == "confirmed:ok"
        ),
        "model_calls": 1,
        "tool_calls": [call["name"] for call in calls],
        "tool_result": tool_result,
        "latency_ms": round((time.perf_counter() - started) * 1000),
        **response_details(tool_message),
    })
    return results


def probe_conversation(base_url: str, inn: str, *, grounding_debug: bool = False) -> list[dict]:
    conversation_id = None
    results = []
    messages = [
        "Проверь контрагента " + inn,
        "Почему это вообще плохо?",
        "Объясни проще",
        "Насколько это критично?",
        "А что у них с финансами?",
        "Что из этого действительно подтверждено, а что ты предполагаешь?",
        "Стоит ли с ними работать?",
        "Мы покупаем у них товар на 20 млн, аванс 30%, остаток после поставки. Что теперь думаешь?",
    ]
    expected_tools = {
        1: "full_company_check",
        5: "get_financial_data",
    }
    for index, message in enumerate(messages, start=1):
        request = Request(
            base_url.rstrip("/") + "/api/v1/chat/messages",
            data=json.dumps({
                "message": message,
                "conversation_id": conversation_id,
            }).encode(),
            headers={"Content-Type": "application/json"},
        )
        started = time.perf_counter()
        with urlopen(request, timeout=190) as response:
            payload = json.load(response)
        elapsed_ms = round((time.perf_counter() - started) * 1000)
        conversation_id = payload["conversation_id"]
        metadata = payload["metadata"]
        expected_tool = expected_tools.get(index)
        expected_tool_count = int(expected_tool is not None)
        fallback = (
            metadata["routing"] != "model"
            or metadata["synthesis"] != "model"
            or metadata["grounding_status"] == "fallback"
        )
        expected_grounding = ("verified", "repaired") if grounding_debug else ("not_requested",)
        provider_ok = (
            metadata["model"] == "z-ai/glm-5.3-flash"
            and metadata["tool_calls"] == expected_tool_count
            and 1 <= metadata["model_calls"] <= 5
            and metadata["grounding_status"] in expected_grounding
            and (index != 7 or "?" in payload["message"])
            and not fallback
        )
        normalized_answer = payload["message"].casefold()
        if index == 4:
            provider_ok = provider_ok and not any(term in normalized_answer for term in (
                "только по предоплате", "не давать отсрочку", "не платите аванс",
            ))
        if index == 6:
            provider_ok = provider_ok and "подтвержд" in normalized_answer and (
                "предполага" in normalized_answer or "гипотез" in normalized_answer
            )
        if index == 7:
            provider_ok = provider_ok and len(payload["message"]) <= 700 and not any(
                term in normalized_answer for term in (
                    "если вы поставщик", "если вы покупатель", "только по предоплате",
                    "не давать отсрочку", "не платите аванс",
                )
            )
        results.append({
            "turn": index,
            "message": message,
            "answer": payload["message"],
            "status": metadata["status"],
            "provider": "openrouter",
            "model": metadata["model"],
            "provider_ok": provider_ok,
            "routing": metadata["routing"],
            "tool": expected_tool if metadata["tool_calls"] else None,
            "tool_calls": metadata["tool_calls"],
            "model_calls": metadata["model_calls"],
            "synthesis": metadata["synthesis"],
            "verifier": metadata["grounding_status"],
            "repair_attempts": metadata["repair_attempts"],
            "fallback": fallback,
            "latency_ms": metadata.get("latency_ms", elapsed_ms),
            "wall_latency_ms": elapsed_ms,
        })
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--inn", default="6165169320")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    settings = Settings()
    provider = asyncio.run(probe_provider(settings))
    conversation = probe_conversation(args.base_url, args.inn, grounding_debug=settings.agent_grounding_debug)
    first = conversation[0]
    checks = {
        "full_company_check": first["tool"] == "full_company_check" and not first["fallback"],
        "post_tool_master_synthesis": first["synthesis"] == "model" and not first["fallback"],
        "grounding_policy": first["verifier"] in (
            ("verified", "repaired") if settings.agent_grounding_debug else ("not_requested",)
        ),
        "calibrated_deal_context_conversation": all(item["provider_ok"] for item in conversation),
    }
    output = {
        "provider": "openrouter",
        "configured_model": settings.master_model,
        "probes": provider,
        "checks": checks,
        "conversation": conversation,
    }
    rendered = json.dumps(output, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered, flush=True)

    failed_provider = [item for item in provider if not item["ok"]]
    failed_checks = [name for name, ok in checks.items() if not ok]
    if failed_provider or failed_checks:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
