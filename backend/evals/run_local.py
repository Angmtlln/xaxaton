"""Live Master workflow, fixed real snapshot, evidence saved after every turn."""
from __future__ import annotations

import argparse
import asyncio
import contextvars
import gzip
import json
import logging
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from langchain_openai import ChatOpenAI
from app.agent.conversations import ConversationStore
from app.agent.runtime import build_master_runtime
from app.agent.tools import ToolRegistry
from app.config import Settings
from app.llm.groq_client import GroqClient
from .bank import BANK, ROOT, SUITES, documents, select, validate_bank, sha
from .graders import grade

CURRENT = contextvars.ContextVar("eval_capture", default=None)


def snapshot(d):
    r, b = d["report"], d["report"]["baseInfo"]
    reg = b.get("registrationInfo") or {}
    return dict(document=d, inn=b["inn"], ogrn=b.get("ogrn"), short_name=b.get("shortName"),
                full_name=b.get("fullName"), address=b.get("address"),
                status=(r.get("status") or {}).get("status"), status_reason=(r.get("status") or {}).get("reasonName"),
                risk_level=b.get("riskLevel"), zsk_risk_level=r.get("zskRiskLevel"),
                report_date=r.get("reportDate"), registration_date=reg.get("registrationDate"),
                years_from_registration=reg.get("yearsFromRegistration"))


async def state(store, cid):
    if not cid:
        return {}
    cp = await store.checkpointer.aget_tuple({"configurable": {"thread_id": cid}})
    values = cp.checkpoint["channel_values"] if cp else {}
    return {k: values.get(k) for k in ("active_company", "trusted_context", "comparison_context", "last_topic", "user_context")}


def save_trace(path, row):
    with gzip.open(path, "wt", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False)


def summarize(out, manifest, rows):
    targets = [r for r in rows if r.get("scored")]
    def status(r):
        return "FAIL" if any(c["status"] == "FAIL" for c in r["checks"]) else "PASS"
    summary = {**manifest, "completed_turns": len(rows), "scored_turns": len(targets),
               "deterministic_pass": sum(status(r) == "PASS" for r in targets),
               "deterministic_fail": sum(status(r) == "FAIL" for r in targets),
               "semantic_status": "NOT_REVIEWED", "rows": rows}
    (out / "latest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Behavioral eval: первый live прогон", "", f"Suite: `{manifest['suite']}`; commit: `{manifest['git_commit']}`.",
             f"Live Master: `{manifest['master_model']}`; LLM_MOCK=false. БД заменена фиксированным snapshot; persist=False.",
             "", f"Завершено {len(targets)}/{manifest['planned_scored_turns']} оцениваемых реплик; технический PASS {summary['deterministic_pass']}, FAIL {summary['deterministic_fail']}.",
             "Содержательная оценка отдельно: NOT_REVIEWED до judge/ручного review. PASS ниже относится только к точным техническим проверкам.",
             "NA означает неприменимо/не наблюдалось, не PASS. Latency threshold — eval SLO, не требование исходного документа.",
             "", "| Case | Technical | ms | Failed checks |", "|---|---|---:|---|"]
    for r in targets:
        lines.append(f"| {r['case_id']} | {status(r)} | {r['wall_ms']} | {', '.join(sorted({c['name'] for c in r['checks'] if c['status'] == 'FAIL'}))} |")
    (out / "report.md").write_text("\n".join(lines) + "\n")
    return summary


async def run(args):
    bank = json.loads(BANK.read_text()); validate_bank(bank)
    sessions = select(bank, args.suite)
    for session in sessions:
        if session.get("fixture"):
            session["fixture_contract"] = bank["fixtures"][session["fixture"]]
    if args.session:
        sessions = [s for s in sessions if s["id"] in args.session]
        if not sessions:
            raise ValueError("Unknown/empty session selection")
    out = Path(args.output); out.mkdir(parents=True, exist_ok=True)
    settings = Settings()
    if settings.llm_mock or not settings.openrouter_api_key or not settings.groq_api_key:
        raise RuntimeError("Requires LLM_MOCK=false, OPENROUTER_API_KEY and GROQ_API_KEY; no mock fallback accepted")
    manifest = dict(suite=args.suite, started_at=datetime.now(timezone.utc).isoformat(),
                    git_commit=subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                    source_sha256=bank["source_sha256"], snapshot_sha256=bank["snapshot_sha256"], bank_sha256=sha(BANK),
                    master_model=settings.master_model, block_models=settings.block_models(),
                    grounding_debug=settings.agent_grounding_debug, concurrency=args.concurrency,
                    latency_threshold_ms=args.latency_ms, data_mode="snapshot", persist=False,
                    planned_scored_turns=sum(t["scored"] for s in sessions for t in s["turns"]))
    if (out / "latest.json").exists():
        raise ValueError("Output directory already contains a run; choose a new one to preserve failed attempts")
    (out / "scenarios.json").write_text(json.dumps(bank, ensure_ascii=False, indent=2) + "\n")
    docs = documents(); snapshots = {inn: snapshot(d) for inn, d in docs.items()}
    rows = []
    async def get(inn):
        return snapshots.get(inn)
    original_model, original_domain, original_tool = ChatOpenAI._agenerate, GroqClient._call_model, ToolRegistry.execute
    async def model(self, messages, *a, **kw):
        start = time.perf_counter(); event = {"kind": "master", "model": self.model_name}
        try:
            result = await original_model(self, messages, *a, **kw)
            m = result.generations[0].message
            event.update(usage=m.usage_metadata, finish_reason=m.response_metadata.get("finish_reason"),
                         actual_model=m.response_metadata.get("model_name"))
            return result
        except Exception as e:
            event["error"] = type(e).__name__; raise
        finally:
            event["ms"] = round((time.perf_counter() - start) * 1000)
            if CURRENT.get() is not None: CURRENT.get()["calls"].append(event)
    async def domain(self, model_name, *a, **kw):
        start = time.perf_counter(); event = {"kind": "domain", "model": model_name}
        try:
            result = await original_domain(self, model_name, *a, **kw)
            event.update(usage=result.raw.get("usage"), finish_reason=result.raw.get("choices", [{}])[0].get("finish_reason"))
            return result
        except Exception as e:
            event["error"] = type(e).__name__; raise
        finally:
            event["ms"] = round((time.perf_counter() - start) * 1000)
            if CURRENT.get() is not None: CURRENT.get()["calls"].append(event)
    async def tool(self, name, arguments, context):
        event = {"name": name, "arguments": arguments}
        if CURRENT.get() is not None: CURRENT.get()["tools"].append(event)
        result = await original_tool(self, name, arguments, context)
        event["result"] = result.model_dump(mode="json")
        return result
    sem = asyncio.Semaphore(args.concurrency)
    async def conversation(session):
        async with sem:
            client = GroqClient(settings); store = ConversationStore()
            runtime = build_master_runtime(settings, client, persist=False, conversation_store=store)
            cid = None
            turns = [{"id": session["id"] + f".setup{i}", "question": q, "scored": False, "setup": True,
                      "expected_tool": session.get("setup_tools", [None] * len(session["setup"]))[i - 1]} for i, q in enumerate(session["setup"], 1)] + session["turns"]
            try:
                for t in turns:
                    row = {"session": session["id"], "case_id": t["id"], "question": t["question"],
                           "scored": t["scored"], "setup": t.get("setup", False), "expectation": t.get("expectation"), "expected_tool": t.get("expected_tool"),
                           "calls": [], "tools": [], "before": await state(store, cid)}
                    token = CURRENT.set(row); start = time.perf_counter()
                    try:
                        response = await runtime.run(t["question"], cid)
                        row["response"] = response.model_dump(mode="json"); cid = response.conversation_id
                    except Exception as e:
                        row["error"] = type(e).__name__
                    finally:
                        CURRENT.reset(token)
                    row["wall_ms"] = round((time.perf_counter() - start) * 1000)
                    row["after"] = await state(store, cid)
                    row["checks"] = grade(row, session, docs, args.latency_ms)
                    name = row["case_id"] + ".json.gz"
                    save_trace(out / name, row)
                    compact = {k: row[k] for k in ("session", "case_id", "question", "scored", "wall_ms", "checks", "calls")}
                    compact.update(trace=name, metadata=row.get("response", {}).get("metadata"), message=row.get("response", {}).get("message"), semantic_status="NOT_REVIEWED")
                    rows.append(compact); summarize(out, manifest, rows)
                    print(json.dumps({"case": t["id"], "done": len(rows), "ms": row["wall_ms"], "failed": sorted({c['name'] for c in row['checks'] if c['status'] == 'FAIL'})}), flush=True)
            finally:
                await client.aclose()
    with patch("app.infrastructure.repository.get_latest_snapshot", get), patch.object(ChatOpenAI, "_agenerate", model), patch.object(GroqClient, "_call_model", domain), patch.object(ToolRegistry, "execute", tool):
        await asyncio.gather(*(conversation(s) for s in sessions))
    manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
    result = summarize(out, manifest, rows)
    return 1 if result["deterministic_fail"] else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=SUITES, default="killer")
    parser.add_argument("--session", action="append")
    parser.add_argument("--concurrency", type=int, choices=range(1, 9), default=3)
    parser.add_argument("--latency-ms", type=int, default=60000)
    parser.add_argument("--output", default="evals/results/" + datetime.now().strftime("%Y%m%d-%H%M%S"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.ERROR)
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
