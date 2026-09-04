#!/usr/bin/env python3
"""Проход по одному ИНН прямо из файла выгрузки, без PostgreSQL.

Нужен для быстрой проверки промптов и для демо, когда базы под рукой нет.

    python scripts/demo_offline.py --inn 1684017097
    python scripts/demo_offline.py --inn 1684017097 --json > out.json
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import get_settings                                   # noqa: E402
from app.domain import facts as facts_mod                             # noqa: E402
from app.llm.agents import enforce_guardrails, run_block_agents, run_summary_agent  # noqa: E402
from app.llm.groq_client import GroqClient                            # noqa: E402
from app.pipeline import (collect_statements, grounding_metrics,      # noqa: E402
                          select_key_facts, _block_public, _summary_public)

SIGNAL_MARK = {"NORM": "[норма]", "ATTENTION": "[внимание]", "RISK": "[риск]", "NO_DATA": "[нет данных]"}


def find_document(path: Path, inn: Optional[str]) -> Dict[str, Any]:
    documents: List[Dict[str, Any]] = json.loads(path.read_text(encoding="utf-8"))
    if inn is None:
        return documents[0]
    for doc in documents:
        if (doc.get("report") or {}).get("baseInfo", {}).get("inn") == inn:
            return doc
    raise SystemExit("ИНН %s в файле не найден" % inn)


async def run(document: Dict[str, Any]) -> Dict[str, Any]:
    settings = get_settings()
    client = GroqClient(settings)
    base = (document.get("report") or {}).get("baseInfo") or {}

    blocks = facts_mod.build_all_blocks(document)
    coverage = facts_mod.build_coverage(document)
    company = {
        "inn": base.get("inn"), "ogrn": base.get("ogrn"),
        "short_name": base.get("shortName"), "full_name": base.get("fullName"),
        "address": base.get("address"),
        "risk_level": base.get("riskLevel"),
        "zsk_risk_level": (document.get("report") or {}).get("zskRiskLevel"),
    }
    key_facts = select_key_facts(blocks)

    block_results = await run_block_agents(client, settings, blocks, company, coverage)
    all_fact_ids = {f.id for blk in blocks.values() for f in blk.facts}
    summary = await run_summary_agent(client, settings, company, block_results, key_facts,
                                      coverage, all_fact_ids=all_fact_ids)
    block_results, summary, notes = enforce_guardrails(blocks, block_results, summary)
    statements = collect_statements(blocks, block_results, summary)
    await client.aclose()

    return {
        "company": company,
        "coverage": coverage,
        "summary": _summary_public(summary),
        "blocks": [_block_public(block_results[k], blocks[k])
                   for k in facts_mod.BLOCK_KEYS if k in block_results],
        "key_facts": key_facts,
        "grounding": grounding_metrics(statements),
        "guardrail_notes": notes,
        "llm_mode": "groq" if client.enabled else "mock",
    }


def render(result: Dict[str, Any]) -> None:
    company, summary = result["company"], result["summary"]
    print("=" * 78)
    print("%s, ИНН %s" % (company["short_name"], company["inn"]))
    print("Оценка банка: %s, светофор ЗСК: %s (приводятся без изменений)"
          % (company["risk_level"], company["zsk_risk_level"]))
    print("Полнота данных: %s из %s блоков" % (result["coverage"]["filled_blocks"],
                                               result["coverage"]["total_blocks"]))
    print("Режим LLM: %s" % result["llm_mode"])
    print("=" * 78)
    print("\nИТОГ: %s" % summary["verdict_group"])
    print(summary["headline"])
    print("\n%s" % summary["narrative"])

    if summary["top_risks"]:
        print("\nНа что обратить внимание:")
        for item in summary["top_risks"]:
            print("  - %s   [%s]" % (item.get("text"), item.get("fact_id") or "без ссылки"))
    if summary["key_numbers"]:
        print("\nКлючевые цифры:")
        for item in summary["key_numbers"]:
            print("  - %s: %s   [%s]" % (item.get("label"), item.get("value"), item.get("fact_id")))
    if summary["data_gaps"]:
        print("\nЧего нет в данных:")
        for gap in summary["data_gaps"]:
            print("  - %s" % gap)
    if summary["questions_to_ask"]:
        print("\nВопросы контрагенту:")
        for q in summary["questions_to_ask"]:
            print("  - %s" % q)

    print("\n" + "-" * 78)
    for block in result["blocks"]:
        print("\n%s %s" % (SIGNAL_MARK.get(block["signal"], block["signal"]), block["title"]))
        print("  %s" % block["headline"])
        print("  Факты: %s" % block["facts_sentence"])
        print("  Вывод: %s" % block["interpretation"])
        for finding in block["findings"]:
            mark = "+" if finding.get("grounded") else "!"
            print("   %s %s [%s]" % (mark, finding["text"], finding.get("fact_id")))
        for gap in block["cannot_assess"][:3]:
            print("   ? %s" % gap)

    grounding = result["grounding"]
    print("\n" + "-" * 78)
    print("Заземление: %s утверждений, со ссылкой на факт %s (%s %%), "
          "ссылок на несуществующие факты %s"
          % (grounding["statements"], grounding["grounded"], grounding["grounded_pct"],
             grounding["unverified"]))
    for note in result["guardrail_notes"]:
        print("Guardrail: %s" % note)


def main() -> int:
    parser = argparse.ArgumentParser(description="Проход по одному ИНН без базы")
    parser.add_argument("--file", default="../contractors_audit.snapshot.json")
    parser.add_argument("--inn", default=None, help="ИНН из выгрузки")
    parser.add_argument("--json", action="store_true", help="печатать сырой JSON ответа")
    args = parser.parse_args()

    document = find_document(Path(args.file), args.inn)
    result = asyncio.run(run(document))
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        render(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
