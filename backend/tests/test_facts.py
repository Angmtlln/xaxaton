"""Контрольный прогон детерминированного слоя по всей выгрузке.

Проверяем ровно то, что записано в критерии приёмки 7: расхождений
между вычисленными фактами и сырыми полями карточки быть не должно.
"""
from app.domain.facts import (BLOCK_KEYS, build_all_blocks, build_coverage,
                              fact_index)


def _value(blocks, fact_id):
    fact = fact_index(blocks).get(fact_id)
    return fact.to_dict()["value"] if fact else None


def test_all_documents_produce_four_blocks(documents):
    for doc in documents:
        blocks = build_all_blocks(doc)
        assert set(blocks) == set(BLOCK_KEYS)


def test_every_fact_has_field_ref(documents):
    for doc in documents[:20]:
        for block in build_all_blocks(doc).values():
            for fact in block.facts:
                assert fact.field_ref, "у факта %s нет ссылки на поле" % fact.id


def test_okved_count_matches_raw_fields(documents):
    """H4: число кодов считаем сами, а не берём из прозы отчёта."""
    for doc in documents:
        kinds = doc["report"].get("kindsOfActivityInfo") or {}
        expected = (1 if (kinds.get("mainKindOfActivity") or {}).get("code") else 0) \
            + len(kinds.get("otherKindsOfActivity") or [])
        assert _value(build_all_blocks(doc), "okved.total_count") == expected


def test_execution_proceedings_sums_match_raw(documents):
    for doc in documents:
        raw = doc["report"].get("executionProceedings") or []
        if not raw:
            continue
        blocks = build_all_blocks(doc)
        active = [p for p in raw if p.get("active") is True]
        assert _value(blocks, "execproc.total_count") == len(raw)
        assert _value(blocks, "execproc.active_count") == len(active)
        known = [float(p["amount"]) for p in active if p.get("amount") is not None]
        assert abs(_value(blocks, "execproc.active_amount") - round(sum(known), 2)) < 0.01


def test_hard_stop_codes_detected(documents):
    """H3: жёсткие факты выносятся отдельно даже при зелёном светофоре."""
    green_with_hard = 0
    for doc in documents:
        blocks = build_all_blocks(doc)
        if _value(blocks, "flags.green_with_hard_stop") is True:
            green_with_hard += 1
            assert _value(blocks, "flags.hard_stop_codes"), "флаг стоит, а фактов нет"
    assert green_with_hard > 0, "в выгрузке ожидались карточки GREEN с жёсткими метками"


def test_coverage_counts_nine_blocks(documents):
    for doc in documents:
        coverage = build_coverage(doc)
        assert coverage["total_blocks"] == 9
        assert 0 <= coverage["filled_blocks"] <= 9
        assert len(coverage["blocks"]) == 9


def test_finance_block_marks_missing_reports(documents):
    empty = [d for d in documents if not (d["report"].get("finReports") or [])]
    assert empty, "в выгрузке ожидались карточки без финансовой отчётности"
    for doc in empty:
        block = build_all_blocks(doc)["finance"]
        assert block.has_data is False
        assert block.missing, "пустой блок обязан явно сообщать об отсутствии данных"
