import json
from pathlib import Path

import pytest

from app.domain.facts import BLOCK_KEYS, build_all_blocks, build_coverage

SNAPSHOT = Path(__file__).resolve().parents[2] / "contractors_audit.snapshot.json"


@pytest.fixture(scope="session")
def documents():
    if not SNAPSHOT.exists():
        pytest.skip("Выгрузка %s не найдена" % SNAPSHOT)
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def document(documents):
    return documents[0]


@pytest.fixture
def check_payload(documents):
    """Минимальный, но реалистичный CheckResponse на детерминированных фактах."""
    document = next(
        item for item in documents
        if item["report"]["baseInfo"]["inn"] == "6165169320"
    )
    report = document["report"]
    base = report["baseInfo"]
    fact_blocks = build_all_blocks(document)
    blocks = []
    for key in BLOCK_KEYS:
        block = fact_blocks[key]
        blocks.append({
            "block": key,
            "title": block.title,
            "signal": "RISK" if key == "reliability" else "NORM",
            "headline": "Проверены факты блока",
            "facts_sentence": "Факты рассчитаны детерминированно.",
            "interpretation": "Интерпретация ограничена доступными данными.",
            "findings": [],
            "data_gaps": [],
            "cannot_assess": [],
            "facts": [fact.to_dict() for fact in block.facts],
            "model": "deterministic",
            "latency_ms": 1,
        })

    return {
        "run_id": "00000000-0000-0000-0000-000000000001",
        "status": "SUCCEEDED",
        "inn": base["inn"],
        "company": {
            "inn": base["inn"],
            "ogrn": base.get("ogrn"),
            "short_name": base.get("shortName"),
            "full_name": base.get("fullName"),
            "address": base.get("address"),
            "status": (report.get("status") or {}).get("status"),
            "years_from_registration": (
                base.get("registrationInfo") or {}
            ).get("yearsFromRegistration"),
            "risk_level": base.get("riskLevel"),
            "zsk_risk_level": report.get("zskRiskLevel"),
            "report_date": None,
        },
        "coverage": build_coverage(document),
        "summary": {
            "verdict_group": "STOP",
            "headline": "До сделки проверьте детерминированные стоп-факторы",
            "narrative": "В карточке есть факты для дополнительной проверки.",
            "narrative_points": [
                "В карточке есть факты для дополнительной проверки.",
                "Запросите у контрагента подтверждающие документы.",
            ],
            "key_numbers": [],
            "top_risks": [],
            "positives": [],
            "data_gaps": [],
            "questions_to_ask": [],
            "model": "deterministic",
            "latency_ms": 1,
        },
        "blocks": blocks,
        "key_facts": [],
        "grounding": {
            "statements": 2,
            "grounded": 2,
            "unverified": 0,
            "no_ref": 0,
            "grounded_pct": 100.0,
        },
        "guardrail_notes": [],
        "llm": {
            "mode": "mock",
            "block_model": "deterministic",
            "block_models": {},
            "summary_model": "deterministic",
            "calculator_version": "facts-1.0.0",
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "latency_ms": 5,
        },
    }
