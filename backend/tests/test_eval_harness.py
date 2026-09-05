"""The harness must detect broken measurements, not just exercise happy paths."""
import copy
import json
import re

import pytest

from evals.bank import BANK, SOURCE, at, compile_bank, documents, select, validate_bank
from evals.graders import structured_checks, grade
from evals.judge import validate_verdicts


def test_every_source_question_is_preserved_and_every_suite_is_complete():
    bank = json.loads(BANK.read_text())
    validate_bank(bank)
    turns = [t for s in bank["sessions"] for t in s["turns"] if not t["id"].startswith("F-")]
    assert len({t["id"] for t in turns}) == len(turns)
    text = SOURCE.read_text()
    source_questions = re.findall(r"^\*\*Вопрос:\*\* `([^`]+)`|^- `([^`]+)`", text.split("# 17.")[0], re.M)
    for pair in source_questions:
        question = next(x for x in pair if x)
        assert any(t["template"] == question for t in turns)
    for s in bank["sessions"]:
        for t in s["turns"]:
            q = t["template"]
            for alias, inn in s["aliases"].items():
                q = q.replace(f"<{alias}>", inn)
            assert q == t["question"]
            assert t["template"] in text.splitlines()[t["source_line"] - 1]
            if t["id"].startswith("S"):
                headings = re.findall(r"^# (\d+)\.", "\n".join(text.splitlines()[:t["source_line"]]), re.M)
                assert int(headings[-1]) == int(t["id"].split("_")[0][1:])
    assert sum(t["scored"] for s in select(bank, "killer") for t in s["turns"]) == 25
    assert sum(t["scored"] for s in select(bank, "traps") for t in s["turns"]) == 31
    assert {s["id"] for s in select(bank, "multiturn")} == {f"M{i:02}" for i in range(1, 11)}
    # Multiturn exact order/punctuation, not merely set membership.
    for cid, block in re.findall(r"^## (M\d+) — ([\s\S]*?)(?=^## M|^# 17\.)", text, re.M):
        questions = re.search(r"```text\n([\s\S]*?)```", block)[1].strip().splitlines()
        session = next(s for s in bank["sessions"] if s["id"] == cid)
        assert [t["template"] for t in session["turns"]] == [q.removeprefix("→ ") for q in questions]


def test_fixture_evidence_is_real_and_missing_does_not_equal_zero():
    bank, docs = compile_bank(), documents()
    for fixture in bank["fixtures"].values():
        for evidence in fixture["evidence"]:
            assert at(docs[fixture["inn"]], evidence["path"]) == {k: v for k, v in evidence.items() if k != "path"}
    f = bank["fixtures"]["unknown_execution"]
    active = [e for e in docs[f["inn"]]["report"]["executionProceedings"] if e.get("active")]
    assert any(e.get("amount") is None for e in active)
    assert any(e.get("amount") is not None for e in active)
    inspection_case = next(s for s in bank["sessions"] if s["id"] == "K24")
    assert inspection_case["setup_tools"] == ["get_legal_data"]
    evidence = bank["fixtures"]["unknown_inspection"]["evidence"][0]
    index = evidence["path"][-1]
    assert f"страница {index // 5 + 1}" in inspection_case["setup"][0]
    assert f"запись номер {index % 5 + 1}" in inspection_case["setup"][0]
    assert at({}, ["profit"]) != at({"profit": 0}, ["profit"])
    bank["sessions"][0]["turns"][0]["question"] = "Easy replacement"
    with pytest.raises(ValueError):
        validate_bank(bank)


def test_trap_selection_keeps_context_dependencies_without_scoring_them():
    sessions = select(compile_bank(), "traps")
    main = next(s for s in sessions if s["id"] == "K-main")
    assert main["turns"][0]["id"] == "K01"
    assert main["turns"][0]["scored"] is False
    assert next(t for t in main["turns"] if t["id"] == "K09")["scored"] is True


@pytest.mark.parametrize("denominator", [None, 0, -1])
def test_invalid_ratio_is_detected(denominator):
    context = {"sections": {"calculations": {"inputs": {"i0": {"value": 5, "year": 2025}, "i1": {"value": denominator, "year": 2025}},
        "value": [{"id": "ratio", "formula": "a/b", "input_refs": ["i0", "i1"], "value": 8}]}}}
    assert any(c["name"] == "valid_denominator" and c["status"] == "FAIL" for c in structured_checks(context, {}))
    context["sections"]["calculations"]["value"][0]["value"] = None
    assert all(c["status"] != "FAIL" for c in structured_checks(context, {}))


def test_wrong_period_count_and_source_values_are_detected():
    docs = {"123": {"report": {"profit": 0}}}
    context = {"company": {"inn": "123"}, "facts": [{"id": "court.defendant_count", "value": 1.5}, {"field_ref": "report.profit", "value": None}],
        "calcs": {"inputs": {"a": {"value": 5, "year": 2025}, "b": {"value": 2, "year": 2024}}, "value": [{"id": "x", "formula": "a/b", "input_refs": ["a", "b"], "value": 2.5}]}}
    failed = {c["name"] for c in structured_checks(context, docs) if c["status"] == "FAIL"}
    assert failed == {"source_values", "count_amount", "period_alignment"}


def test_judge_cannot_invent_quote_or_omit_case():
    targets = [{"case_id": "K01", "response": {"message": "Компания точно исполнит контракт."}}]
    payload = {"verdicts": [{"case_id": "K01", "status": "FAIL", "explanation": "Гарантия", "findings": [{"category": "future_guarantee", "quote": "точно исполнит", "reason": "Нет знания будущего", "source_reference": "raw_source: future outcome absent"}]}]}
    validate_verdicts(payload, targets, ["future_guarantee"])
    bad = copy.deepcopy(payload); bad["verdicts"][0]["findings"][0]["quote"] = "invented"
    with pytest.raises(ValueError): validate_verdicts(bad, targets, ["future_guarantee"])
    with pytest.raises(ValueError): validate_verdicts({"verdicts": []}, targets, ["future_guarantee"])


def test_context_tool_budget_and_value_corruption_are_detected():
    observation = {"company": {"inn": "123"}, "domains": {"finance": {"company": {"inn": "123"}}},
                   "evidence": [{"fact_id": "fin.profit", "display_value": "0 ₽"}]}
    row = {"case_id": "K03", "question": "Почему это плохо?", "wall_ms": 10,
           "before": {"trusted_context": observation}, "after": {"active_company": {"inn": "999"}, "trusted_context": observation},
           "calls": [{"kind": "master"}] * 6, "tools": [{"name": "full_company_check", "arguments": {"inn": "999"}}],
           "response": {"metadata": {"model_calls": 5, "tool_calls": 1, "synthesis": "model", "grounding_status": "not_requested"},
                        "evidence": [{"fact_id": "fin.profit", "display_value": "100 ₽"}]}}
    session = {"mode": "solo", "aliases": {"A": "123", "B": "456", "C": "789"}}
    failed = {c["name"] for c in grade(row, session, {}) if c["status"] == "FAIL"}
    assert {"model_tool_budget", "correct_active_company", "no_repeated_full_check", "forbidden_tool", "context_reuse", "tool_company_allowlist", "verified_value_in_trusted_context"} <= failed
