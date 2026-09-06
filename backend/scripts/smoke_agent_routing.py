"""Live HTTP probes for identifier admission and single-company Master routing.

Rebuild the API first. Uses the configured real model and database; no fixtures
or model substitutions. Output contains complete public answers, never secrets.
"""
import argparse
import json
import time
from pathlib import Path
from urllib.request import Request, urlopen


CASES = [
    ("Какая платёжеспособность у 6165169320?", "6165169320", 1),
    ("Не проверяй финансы, объясни проще", "6165169320", 0),
    ("Мы продаём им товар на 10000000 рублей с отсрочкой 30 дней. Что это меняет?", "6165169320", 0),
    ("Расскажи о судах и финансах: финансовые данные уже есть, судебные нужно прочитать", "6165169320", 1),
    ("Почему?", "6165169320", 0),
    ("Какая платёжеспособность у ИНН 0278949271?", "0278949271", 1),
    ("Объясни проще", "0278949271", 0),
]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--turns", type=int, nargs="+", choices=range(1, len(CASES) + 1),
                        default=list(range(1, len(CASES) + 1)),
                        help="Run selected original turns in order; include the setup turn")
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new path to preserve the previous run")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    cid = None
    for question, inn, tool_count in [CASES[index - 1] for index in args.turns]:
        started = time.monotonic()
        request = Request(args.base_url.rstrip("/") + "/api/v1/chat/messages",
                          data=json.dumps({"message": question, "conversation_id": cid}).encode(),
                          headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urlopen(request, timeout=320) as response:
                payload = json.load(response)
            cid = payload.get("conversation_id") or cid
            meta = payload.get("metadata", {})
            checks = {
                "active_company": (payload.get("active_company") or {}).get("inn") == inn,
                "tool_count": meta.get("tool_calls") == tool_count,
                "model_answer": meta.get("synthesis") == "model",
                "no_fallback": meta.get("routing") == "model",
                "no_external_search": payload.get("external_news_status") is None,
            }
            row = {"question": question, "checks": checks, "response": payload,
                   "latency_s": round(time.monotonic() - started, 3)}
        except Exception as exc:
            row = {"question": question, "checks": {"transport": False}, "error": str(exc),
                   "latency_s": round(time.monotonic() - started, 3)}
        rows.append(row)
        args.output.write_text(json.dumps({"base_url": args.base_url, "planned": len(args.turns),
                                          "completed": len(rows), "results": rows}, ensure_ascii=False, indent=2))
        print(json.dumps({"turn": len(rows), "question": question, "checks": row["checks"],
                          "latency_s": row["latency_s"]}, ensure_ascii=False), flush=True)
    raise SystemExit(0 if all(all(row["checks"].values()) for row in rows) else 1)


if __name__ == "__main__":
    main()
