"""Grounded observation catalog shared by post-tool context and hydration.

The model selects emphasis and optional visualization. Statements and their
explanations are backend templates applied to the same verified facts.
"""
import json

from .targeted_models import MasterSynthesis, TargetedFinding
from .tools import _evidence_from_fact, display_fact_value
from .models import FullCompanyCheckData


def observation_findings(result):
    if result.status == "error":
        return []
    if result.metadata.tool == "full_company_check":
        return [item.model_dump(mode="json") for item in full_check_findings(
            FullCompanyCheckData.model_validate(result.data))]
    return result.data.get("findings", [])


def verified_evidence(data, result):
    expected = {key: _evidence_from_fact(fact) for key, fact in data.facts.items()}
    evidence = {}
    for item in result.evidence:
        if item.id in evidence or expected.get(item.id) != item:
            raise ValueError("Tool evidence does not match backend fact")
        evidence[item.id] = item
    if data.facts.keys() - evidence.keys():
        raise ValueError("Tool observations lack verified evidence")
    return evidence


def full_check_findings(data):
    # The predicate and explanation belong together: a model cannot attach the
    # favorable interpretation of one fact to a different adverse observation.
    rules = (
        ("flags.hard_stop_codes", bool, True,
         "Эти стоп-факторы нужно проверить до обсуждения условий сделки."),
        ("flags.attention_codes", bool, True,
         "По этим сигналам стоит запросить пояснения и подтверждающие документы."),
        ("execproc.active_count", _positive, True,
         "Перед сделкой стоит уточнить основания и текущее состояние производств."),
        ("court.defendant_count", _positive, False,
         "Само число дел не описывает исход споров; стоит отдельно разобрать их содержание."),
        ("fin.negative_capitals", lambda value: value is True, True,
         "Это повод запросить актуальную отчётность и пояснения о собственном капитале."),
        ("fin.proceeds_change_pct", _negative, True,
         "Причины снижения выручки стоит уточнить; одна динамика не определяет условия сделки."),
        ("fin.profit_last", lambda value: isinstance(value, (int, float)) and not isinstance(value, bool), False,
         "Для оценки устойчивости прибыли полезно посмотреть её динамику по годам."),
        ("procurement.contracts_signed", _positive, False,
         "Количество заключённых контрактов само по себе не подтверждает качество исполнения."),
        ("positive.count", _positive, False,
         "Положительные сведения стоит учитывать вместе с ограничениями и остальными фактами."),
    )
    findings = []
    for fact_id, predicate, required, explanation in rules:
        fact = data.facts.get(fact_id)
        if fact is None or not predicate(fact.value):
            continue
        findings.append(TargetedFinding(
            id=fact.id, title=fact.label,
            text="%s: %s. %s" % (fact.label, display_fact_value(fact), explanation),
            evidence_ids=[fact.id], required=required or (fact_id == "fin.profit_last" and fact.value < 0),
        ))
    return findings


def select_synthesis(findings, synthesis):
    known = {item.id: item for item in findings}
    # A safe fallback still reads like a short answer, not a dump of the catalog.
    selected = list(known)[:3]
    artifact = "none"
    status = "deterministic" if synthesis is None else "fallback"
    if synthesis is not None:
        try:
            proposal = MasterSynthesis.model_validate(
                json.loads(synthesis) if isinstance(synthesis, str) else synthesis
            )
            ids = proposal.finding_ids
            if len(set(ids)) != len(ids) or set(ids) - known.keys() or (known and not ids):
                raise ValueError("Unknown, repeated or empty synthesis")
            # Selection is bounded even if an over-verbose model selects all IDs.
            selected = ids[:3]
            artifact = proposal.artifact
            status = "model"
        except (ValueError, TypeError):
            pass
    selected += [item.id for item in findings if item.required and item.id not in selected]
    return [known[key] for key in selected], artifact, status


def _positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def _negative(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0
