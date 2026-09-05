"""GLM-only payment-capacity probes; original questions plus disclosed synthetic overlays."""
import argparse
import asyncio
import copy
import json
from pathlib import Path
from unittest.mock import patch

from app.agent.synthesis import normalized_tool_context
from app.agent.tools import ToolContext, build_tool_registry
from app.config import Settings
from app.llm.groq_client import GroqClient
from . import compare_models
from .bank import documents
from .run_local import snapshot

INN = "7813664770"
MODEL = "z-ai/glm-5.3-flash"
SOURCE_RUNS = ["evals/results/structured-scope-final-comparison",
               "evals/results/structured-scope-final-targets"]


def overlay_documents(original, variant):
    docs = copy.deepcopy(original)
    if variant not in {"cash_rich", "fixed_assets"}:
        raise ValueError("Unknown synthetic variant")
    row = docs[INN]["report"]["finReports"][0]
    assets = row["assets"]
    # Swap only the disclosed cash/fixed-assets composition; preserve total assets,
    # receivables, equity, all liabilities, missing totals and zero revenue.
    if variant == "cash_rich":
        assets["currentAssets"]["bankroll"], assets["uncurrentAssets"]["fixedAssets"] = (
            assets["uncurrentAssets"]["fixedAssets"], assets["currentAssets"]["bankroll"])
    return docs


async def build_probes(cases, docs):
    settings = Settings()
    client = GroqClient(settings)
    async def get(inn):
        return snapshot(docs[inn])
    try:
        with patch("app.infrastructure.repository.get_latest_snapshot", get):
            result = await build_tool_registry(settings).execute("compare_companies",
                {"inns": ["6165169320", "3711039473", INN], "focus": "both"},
                ToolContext(settings, client, False))
        context = normalized_tool_context(result)
    finally:
        await client.aclose()
    probes = copy.deepcopy(cases)
    for case in probes:
        # A controlled setup avoids stale numbers from the real assistant history.
        # This is an additional probe, not a replacement for the original replay.
        question = "Сравни 6165169320, 3711039473, 7813664770. Кто выглядит устойчивее?"
        case["frozen"] = {"question": case["frozen"]["question"], "last_answer_verified": False,
            "history": [{"type": "human", "content": question},
                        {"type": "ai", "content": "Данные компаний получены."}],
            "before": {"active_company": None, "trusted_context": None,
                       "comparison_context": copy.deepcopy(context), "last_topic": "comparison",
                       "user_context": [question]}}
        case["input_sha256"] = compare_models.digest(case["frozen"])
    return probes


async def run(args):
    if Settings().master_model != MODEL:
        raise ValueError("This experiment is authorized only for the current GLM model")
    bank, cases = compare_models.load_cases(SOURCE_RUNS, ["K19", "S15_10"])
    source = documents()
    if args.variant != "original":
        source = overlay_documents(source, args.variant)
        cases = await build_probes(cases, source)
    args.run, args.case, args.model = SOURCE_RUNS, ["K19", "S15_10"], [MODEL]
    args.latency_ms = 60000
    with patch.object(compare_models, "load_cases", lambda *_: (bank, cases)), \
         patch.object(compare_models, "documents", lambda: source):
        result = await compare_models.run(args)
    path = Path(args.output)
    metadata = dict(variant=args.variant, synthetic=args.variant != "original",
        source_snapshot_sha256=bank["snapshot_sha256"], effective_documents_sha256=compare_models.digest(source),
        affected_inn=INN if args.variant != "original" else None,
        rule="additional probe; fixed_assets retains real balance, cash_rich swaps cash/fixedAssets; original questions retained",
        finance_row=source[INN]["report"]["finReports"][0],
        semantic_status="NOT_REVIEWED")
    (path / "variant.json").write_text(json.dumps(metadata, ensure_ascii=False, indent=2)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--variant", choices=["original", "cash_rich", "fixed_assets"], required=True)
    parser.add_argument("--repetitions", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--concurrency", type=int, choices=range(1, 5), default=2)
    parser.add_argument("--output", required=True)
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
