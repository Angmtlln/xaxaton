"""Live HTTP smoke: full check → finance → legal, against PostgreSQL/ChatGroq."""
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
    assert health["database"] is True and health["llm_mode"] == "groq", health
    conversation_id = None
    for index, message in enumerate([
        "Проверь контрагента " + args.inn,
        "А что у них с финансами?",
        "А что у них с судами?",
    ]):
        if index and args.pause_seconds > 0:
            time.sleep(args.pause_seconds)
        payload = request("/api/v1/chat/messages", {
            "message": message, "conversation_id": conversation_id,
        })
        meta = payload["metadata"]
        assert meta["status"] in ("completed", "partial"), meta
        assert payload["active_company"]["inn"] == args.inn, payload["active_company"]
        assert meta["tool_calls"] == 1 and meta["model_calls"] == 2, meta
        assert meta["routing"] == "model", meta
        if conversation_id:
            assert payload["conversation_id"] == conversation_id
        conversation_id = payload["conversation_id"]
        if index:
            assert meta["synthesis"] == "model", meta
            assert meta["check_run_id"] is None, meta
        evidence = {item["id"] for item in payload["evidence"]}
        assert evidence
        for block in payload["blocks"]:
            for item in block.get("items", []):
                if item.get("evidence_id"):
                    assert item["evidence_id"] in evidence
        print(json.dumps({"turn": index + 1, "conversation_id": conversation_id,
                          "metadata": meta, "blocks": [b["type"] for b in payload["blocks"]],
                          "evidence_count": len(evidence)}, ensure_ascii=False), flush=True)
    for path in ("/", "/report?inn=" + args.inn):
        with urlopen(args.base_url.rstrip("/") + path) as response:
            assert response.status == 200
    print("PASS: live multi-turn ChatGroq + PostgreSQL, landing and legacy report", flush=True)


if __name__ == "__main__":
    main()
