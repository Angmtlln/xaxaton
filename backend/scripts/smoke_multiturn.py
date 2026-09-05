"""Live seven-turn Master conversation against the configured provider and DB."""
import argparse
import json
import time
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--inn", default="6165169320")
    parser.add_argument("--pause-seconds", type=float, default=0,
                        help="Пауза между turns при общем TPM-лимите провайдера")
    args = parser.parse_args()

    def request(path, payload=None):
        req = Request(args.base_url.rstrip("/") + path,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=190) as response:
            return json.load(response)

    health = request("/health")
    assert health["database"] is True, health
    conversation_id = None
    turns = [
        "Проверь контрагента " + args.inn,
        "А что у них с финансами?",
        "Почему это вообще плохо?",
        "Объясни проще",
        "Насколько это критично для сделки с отсрочкой?",
        "А что с судами?",
        "Что здесь самое неприятное?",
    ]
    tool_turns = {1, 2, 6}
    total_tool_calls = 0
    for index, message in enumerate(turns, start=1):
        if index > 1 and args.pause_seconds > 0:
            time.sleep(args.pause_seconds)
        payload = request("/api/v1/chat/messages", {
            "message": message, "conversation_id": conversation_id,
        })
        meta = payload["metadata"]
        assert meta["status"] in ("completed", "partial"), meta
        assert payload["active_company"]["inn"] == args.inn, payload["active_company"]
        expected_tools = 1 if index in tool_turns else 0
        assert meta["tool_calls"] == expected_tools, meta
        assert 1 <= meta["model_calls"] <= 5, meta
        assert meta["routing"] == "model", meta
        expected_grounding = "skipped_rewrite" if index == 4 else None
        if expected_grounding:
            assert meta["grounding_status"] == expected_grounding, meta
        else:
            assert meta["grounding_status"] in ("verified", "repaired"), meta
        total_tool_calls += meta["tool_calls"]
        if conversation_id:
            assert payload["conversation_id"] == conversation_id
        conversation_id = payload["conversation_id"]
        assert meta["synthesis"] == "model", meta
        assert payload["message"].strip(), payload
        if index != 1:
            assert payload["leading_artifact"] is None, payload
            assert len(payload["blocks"]) <= 2, payload["blocks"]
            assert meta["check_run_id"] is None, meta
        else:
            summary = payload["leading_artifact"]
            assert summary["type"] == "company_summary", summary
            assert summary["inn"] == args.inn, summary
            assert summary["report_url"] == "/report?inn=" + args.inn, summary
        assert not any(b["type"] in ("company_card", "evidence_list")
                       for b in payload["blocks"]), payload["blocks"]
        evidence = {item["id"] for item in payload["evidence"]}
        assert evidence
        for block in payload["blocks"]:
            for item in block.get("items", []):
                if item.get("evidence_id"):
                    assert item["evidence_id"] in evidence
        print(json.dumps({"turn": index, "question": message,
                          "answer": payload["message"], "conversation_id": conversation_id,
                          "metadata": meta, "blocks": [b["type"] for b in payload["blocks"]],
                          "leading_artifact": (payload.get("leading_artifact") or {}).get("type"),
                          "evidence_count": len(evidence)}, ensure_ascii=False), flush=True)
    assert total_tool_calls == 3, total_tool_calls
    for path in ("/", "/report?inn=" + args.inn):
        with urlopen(args.base_url.rstrip("/") + path) as response:
            assert response.status == 200
    print("PASS: seven-turn grounded Master conversation, PostgreSQL, landing and legacy report", flush=True)


if __name__ == "__main__":
    main()
