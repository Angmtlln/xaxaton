"""Paired live model evaluation on frozen, source-backed contextual turns."""
from __future__ import annotations

import argparse
import asyncio
import copy
import gzip
import hashlib
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from langchain.agents import create_agent
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI

from app.agent.conversations import ConversationState, ConversationStore
from app.agent.runtime import build_master_runtime
from app.agent.tools import ToolRegistry
from app.config import Settings
from app.llm.groq_client import GroqClient
from .bank import BANK, ROOT, documents, select, sha, validate_bank
from .graders import grade
from .run_local import CURRENT, save_trace, state


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


def load_cases(paths, case_ids):
    """Restore runtime's bounded prose history separately from saved trusted state."""
    bank = json.loads(BANK.read_text()); validate_bank(bank)
    found = {}
    for path in map(Path, paths):
        manifest = json.loads((path / "latest.json").read_text())
        if not manifest.get("finished_at"):
            raise ValueError("Source run is incomplete")
        for key, expected in (("source_sha256", bank["source_sha256"]),
                              ("snapshot_sha256", bank["snapshot_sha256"]), ("bank_sha256", sha(BANK))):
            if manifest[key] != expected:
                raise ValueError("Source run drift: " + key)
        sessions = {s["id"]: s for s in select(bank, manifest["suite"])}
        histories, previous = {}, {}
        for item in manifest["rows"]:
            row = json.loads(gzip.decompress((path / item["trace"]).read_bytes()))
            sid, cid = row["session"], row["case_id"]
            history = histories.setdefault(sid, [])
            if cid in case_ids:
                if cid in found or row["tools"] or not row["scored"] or not history:
                    raise ValueError("Replay requires unique scored contextual turns with history")
                session = sessions[sid]
                if session.get("fixture"):
                    session["fixture_contract"] = bank["fixtures"][session["fixture"]]
                expected = next(t for t in session["turns"] if t["id"] == cid)
                if row["question"] != expected["question"]:
                    raise ValueError("Question differs from source bank")
                prior = previous[sid]
                if row["before"] != prior["after"]:
                    raise ValueError("Discontinuous trusted state")
                verified = prior["response"]["metadata"]["grounding_status"] in {
                    "verified", "repaired", "skipped_rewrite", "fallback"}
                frozen = {"before": row["before"], "history": copy.deepcopy(history),
                          "last_answer_verified": verified, "question": row["question"]}
                found[cid] = dict(case_id=cid, session=session, frozen=frozen,
                                  input_sha256=digest(frozen), source_run=str(path),
                                  source_trace_sha256=sha(path / item["trace"]))
            if row.get("response") is None:
                raise ValueError("Source history contains a failed turn")
            if row["before"].get("active_company") and row["before"].get("active_company") != row["after"].get("active_company"):
                history.clear()
            history.extend([{"type": "human", "content": row["question"]},
                            {"type": "ai", "content": row["response"]["message"]}])
            histories[sid] = history[-12:]
            previous[sid] = row
    if set(found) != set(case_ids):
        raise ValueError("Missing requested cases: " + str(set(case_ids) - set(found)))
    return bank, [found[cid] for cid in case_ids]


async def seed_store(runtime, case):
    frozen = copy.deepcopy(case["frozen"])
    store = runtime.conversation_store
    async with store.session(None) as (cid, _):
        agent = create_agent(model=runtime.model, tools=[], state_schema=ConversationState,
                             checkpointer=store.checkpointer)
        history = [(HumanMessage if m["type"] == "human" else AIMessage)(content=m["content"])
                   for m in frozen["history"]]
        await agent.aupdate_state({"configurable": {"thread_id": cid}},
                                 {**frozen["before"], "messages": history,
                                  "last_answer_verified": frozen["last_answer_verified"]}, as_node="model")
    return cid


async def run(args):
    bank, cases = load_cases(args.run, args.case)
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    settings = Settings()
    if settings.llm_mock or not settings.openrouter_api_key or settings.agent_grounding_debug:
        raise ValueError("Requires live OpenRouter and grounding_debug=false for single-call comparison")
    manifest = dict(started_at=datetime.now(timezone.utc).isoformat(),
        git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        source_sha256=bank["source_sha256"], snapshot_sha256=bank["snapshot_sha256"], bank_sha256=sha(BANK),
        prompt_hashes={p: sha(ROOT / p) for p in ("backend/app/agent/prompt.py", "backend/app/agent/RISK_PLAYBOOK.md")},
        models=args.model, repetitions=args.repetitions, planned=len(cases)*len(args.model)*args.repetitions,
        settings={k: getattr(settings, k) for k in ("openrouter_base_url", "openrouter_reasoning_effort",
            "openrouter_provider_sort", "agent_answer_max_tokens", "agent_model_timeout_s", "agent_grounding_debug")},
        mode="frozen contextual runtime replay; no domain calls; original history retained", rows=[])
    save_trace(out / "inputs.json.gz", cases)
    original = ChatOpenAI._agenerate

    async def capture(self, messages, *a, **kw):
        event = {"kind": "master", "model": self.model_name,
                 "messages": [{"type": m.type, "content": m.content} for m in messages]}
        event["messages_sha256"] = digest(event["messages"])
        start = time.perf_counter()
        CURRENT.get()["calls"].append(event)
        try:
            result = await original(self, messages, *a, **kw)
            message = result.generations[0].message
            event.update(usage=message.usage_metadata,
                         finish_reason=message.response_metadata.get("finish_reason"),
                         actual_model=message.response_metadata.get("model_name"))
            return result
        except Exception as exc:
            event["error"] = type(exc).__name__
            raise
        finally:
            event["ms"] = round((time.perf_counter()-start)*1000)

    async def forbidden(*a, **kw):
        CURRENT.get()["tools"].append({"name": "unexpected_domain_access", "arguments": {}})
        raise RuntimeError("Frozen replay must not access tools or repositories")

    docs = documents()
    sem = asyncio.Semaphore(args.concurrency)
    async def attempt(case, model, repetition):
        async with sem:
            client = GroqClient(settings)
            config = settings.model_copy(update={"master_model": model})
            runtime = build_master_runtime(config, client, persist=False, conversation_store=ConversationStore())
            row = dict(case_id=case["case_id"], session=case["session"]["id"], scored=True,
                       question=case["frozen"]["question"], model=model, repetition=repetition,
                       input_sha256=case["input_sha256"], calls=[], tools=[], before=case["frozen"]["before"])
            token = CURRENT.set(row)
            start = time.perf_counter()
            try:
                cid = await seed_store(runtime, case)
                assert await state(runtime.conversation_store, cid) == row["before"]
                response = await runtime.run(row["question"], cid)
                row["response"] = response.model_dump(mode="json")
                row["after"] = await state(runtime.conversation_store, cid)
            except Exception as exc:
                row["error"] = type(exc).__name__
                row.setdefault("after", {})
            finally:
                CURRENT.reset(token)
                await client.aclose()
            row["wall_ms"] = round((time.perf_counter()-start)*1000)
            row["checks"] = grade(row, case["session"], docs, args.latency_ms)
            row["checks"].append(dict(name="single_call_frozen_replay", status="PASS" if len(row["calls"]) == 1 and not row["tools"] else "FAIL"))
            filename = f"{case['case_id']}-{args.model.index(model)}-{repetition}.json.gz"
            save_trace(out / filename, row)
            compact = {k: row[k] for k in ("case_id", "model", "repetition", "input_sha256", "wall_ms", "checks")}
            compact.update(trace=filename, semantic_status="NOT_REVIEWED",
                           messages_sha256=[c["messages_sha256"] for c in row["calls"]])
            manifest["rows"].append(compact)
            (out / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
            print(json.dumps({k: compact[k] for k in ("case_id", "model", "repetition", "wall_ms")}), flush=True)

    # Paired scheduling alternates model order on each repetition.
    with patch.object(ChatOpenAI, "_agenerate", capture), patch.object(ToolRegistry, "execute", forbidden), patch("app.infrastructure.repository.get_latest_snapshot", forbidden):
        await asyncio.gather(*(attempt(case, model, rep) for rep in range(1, args.repetitions+1)
            for case in cases for model in (args.model if rep % 2 else args.model[::-1])))
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    manifest["identical_model_messages"] = all(len({tuple(r["messages_sha256"]) for r in manifest["rows"] if r["case_id"] == case["case_id"]}) == 1 for case in cases)
    (out / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
    return int(not manifest["identical_model_messages"] or any(c["status"] == "FAIL" for r in manifest["rows"] for c in r["checks"]))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run", action="append", required=True)
    p.add_argument("--case", action="append", required=True)
    p.add_argument("--model", action="append", required=True)
    p.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    p.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)
    p.add_argument("--latency-ms", type=int, default=60000)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    if len(set(args.model)) != len(args.model) or len(set(args.case)) != len(args.case):
        p.error("Duplicate models/cases are not allowed; use --repetitions")
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
