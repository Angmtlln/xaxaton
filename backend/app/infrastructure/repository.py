"""Доступ к данным: чтение карточек и запись прогонов агента."""
import json
import logging
from typing import Any, Dict, List, Optional
from uuid import UUID

from psycopg.types.json import Jsonb

from app.infrastructure.db import get_pool

log = logging.getLogger(__name__)


# ----------------------------- чтение --------------------------------

SNAPSHOT_SQL = """
SELECT s.id            AS snapshot_id,
       s.report_date,
       s.address, s.email, s.website, s.company_size,
       s.registration_date, s.years_from_registration,
       s.status, s.status_reason, s.status_date,
       s.risk_level::text  AS risk_level,
       s.zsk_risk_level::text AS zsk_risk_level,
       c.id AS company_id, c.inn, c.ogrn, c.kpp, c.okpo, c.short_name, c.full_name,
       d.document
FROM   core.report_snapshots s
JOIN   core.companies c        ON c.id = s.company_id
LEFT   JOIN raw.report_documents d ON d.id = s.raw_document_id
WHERE  c.inn = %(inn)s
ORDER  BY s.report_date DESC
LIMIT  1
"""


async def get_latest_snapshot(inn: str) -> Optional[Dict[str, Any]]:
    """Последний отчёт по ИНН вместе с сырым документом карточки."""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(SNAPSHOT_SQL, {"inn": inn})
            return await cur.fetchone()


async def list_companies(limit: int = 50, offset: int = 0,
                         risk_level: Optional[str] = None,
                         zsk_risk_level: Optional[str] = None,
                         min_filled_blocks: Optional[int] = None,
                         query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Витрина доступных карточек. Нужна демо-режиму: какие ИНН пробовать."""
    sql = [
        """
        SELECT c.inn, c.short_name, s.id AS snapshot_id, s.report_date,
               s.risk_level::text AS risk_level, s.zsk_risk_level::text AS zsk_risk_level,
               COALESCE(cov.filled_blocks, 0) AS filled_blocks,
               (SELECT count(*) FROM core.reputational_risks r
                 WHERE r.snapshot_id = s.id AND r.polarity = 'NEGATIVE') AS negative_count
        FROM   core.v_latest_snapshots s
        JOIN   core.companies c ON c.id = s.company_id
        LEFT   JOIN core.snapshot_coverage cov ON cov.snapshot_id = s.id
        WHERE  1 = 1
        """
    ]
    params: Dict[str, Any] = {"limit": limit, "offset": offset}
    if risk_level:
        sql.append("AND s.risk_level = %(risk_level)s::core.risk_level")
        params["risk_level"] = risk_level
    if zsk_risk_level:
        sql.append("AND s.zsk_risk_level = %(zsk)s::core.zsk_level")
        params["zsk"] = zsk_risk_level
    if min_filled_blocks is not None:
        sql.append("AND COALESCE(cov.filled_blocks, 0) >= %(min_blocks)s")
        params["min_blocks"] = min_filled_blocks
    if query:
        sql.append("AND (c.inn LIKE %(q)s OR c.short_name ILIKE %(q_like)s)")
        params["q"] = "%s%%" % query
        params["q_like"] = "%%%s%%" % query
    sql.append("ORDER BY c.short_name LIMIT %(limit)s OFFSET %(offset)s")

    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("\n".join(sql), params)
            return await cur.fetchall()


async def get_cached_facts(snapshot_id: int, calculator_ver: str) -> Dict[str, Any]:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT block::text AS block, facts
                     FROM audit.snapshot_facts
                    WHERE snapshot_id = %(sid)s AND calculator_ver = %(ver)s""",
                {"sid": snapshot_id, "ver": calculator_ver})
            rows = await cur.fetchall()
    return {r["block"]: r["facts"] for r in rows}


async def save_facts(snapshot_id: int, calculator_ver: str, blocks: Dict[str, Any]) -> None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            for block, payload in blocks.items():
                await cur.execute(
                    """INSERT INTO audit.snapshot_facts (snapshot_id, block, calculator_ver, facts)
                       VALUES (%(sid)s, %(block)s::audit.block_key, %(ver)s, %(facts)s)
                       ON CONFLICT (snapshot_id, block, calculator_ver)
                       DO UPDATE SET facts = EXCLUDED.facts, computed_at = now()""",
                    {"sid": snapshot_id, "block": block, "ver": calculator_ver,
                     "facts": Jsonb(payload)})
        await conn.commit()


# ----------------------------- запись --------------------------------

async def create_run(inn: str, company_id: Optional[int], snapshot_id: Optional[int],
                     block_model: str, summary_model: str, calculator_ver: str,
                     llm_mode: str) -> str:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO audit.analysis_runs
                       (inn, company_id, snapshot_id, status, block_model, summary_model,
                        calculator_ver, llm_mode)
                   VALUES (%(inn)s, %(cid)s, %(sid)s, 'RUNNING', %(bm)s, %(sm)s, %(ver)s, %(mode)s)
                   RETURNING id""",
                {"inn": inn, "cid": company_id, "sid": snapshot_id, "bm": block_model,
                 "sm": summary_model, "ver": calculator_ver, "mode": llm_mode})
            row = await cur.fetchone()
        await conn.commit()
    return str(row["id"])


async def finish_run(run_id: str, status: str, latency_ms: int,
                     prompt_tokens: int, completion_tokens: int,
                     error: Optional[str] = None) -> None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """UPDATE audit.analysis_runs
                      SET status = %(status)s::audit.run_status,
                          finished_at = now(), latency_ms = %(lat)s,
                          prompt_tokens = %(pt)s, completion_tokens = %(ct)s,
                          error = %(err)s
                    WHERE id = %(id)s""",
                {"status": status, "lat": latency_ms, "pt": prompt_tokens,
                 "ct": completion_tokens, "err": error, "id": UUID(run_id)})
        await conn.commit()


async def save_block_result(run_id: str, payload: Dict[str, Any]) -> None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO audit.run_blocks
                       (run_id, block, signal, headline, facts_sentence, interpretation,
                        findings, data_gaps, cannot_assess, facts_input, model,
                        latency_ms, prompt_tokens, completion_tokens, raw_response, error)
                   VALUES (%(run)s, %(block)s::audit.block_key, %(signal)s::audit.block_signal,
                           %(headline)s, %(facts_sentence)s, %(interpretation)s,
                           %(findings)s, %(data_gaps)s, %(cannot_assess)s, %(facts_input)s,
                           %(model)s, %(lat)s, %(pt)s, %(ct)s, %(raw)s, %(err)s)
                   ON CONFLICT (run_id, block) DO UPDATE SET
                       signal = EXCLUDED.signal, headline = EXCLUDED.headline,
                       facts_sentence = EXCLUDED.facts_sentence,
                       interpretation = EXCLUDED.interpretation,
                       findings = EXCLUDED.findings, data_gaps = EXCLUDED.data_gaps,
                       cannot_assess = EXCLUDED.cannot_assess,
                       facts_input = EXCLUDED.facts_input, model = EXCLUDED.model,
                       latency_ms = EXCLUDED.latency_ms, raw_response = EXCLUDED.raw_response,
                       error = EXCLUDED.error""",
                {"run": UUID(run_id), "block": payload["block"], "signal": payload["signal"],
                 "headline": payload["headline"], "facts_sentence": payload["facts_sentence"],
                 "interpretation": payload["interpretation"],
                 "findings": Jsonb(payload["findings"]), "data_gaps": Jsonb(payload["data_gaps"]),
                 "cannot_assess": Jsonb(payload["cannot_assess"]),
                 "facts_input": Jsonb(payload["facts_input"]), "model": payload["model"],
                 "lat": payload["latency_ms"], "pt": payload["prompt_tokens"],
                 "ct": payload["completion_tokens"], "raw": Jsonb(payload.get("raw_response")),
                 "err": payload.get("error")})
        await conn.commit()


async def save_summary(run_id: str, payload: Dict[str, Any]) -> None:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """INSERT INTO audit.run_summaries
                       (run_id, verdict_group, headline, narrative, key_numbers, top_risks,
                        positives, data_gaps, questions_to_ask, model, latency_ms,
                        prompt_tokens, completion_tokens, raw_response, error)
                   VALUES (%(run)s, %(verdict)s::audit.verdict_group, %(headline)s, %(narrative)s,
                           %(kn)s, %(tr)s, %(pos)s, %(gaps)s, %(q)s, %(model)s, %(lat)s,
                           %(pt)s, %(ct)s, %(raw)s, %(err)s)
                   ON CONFLICT (run_id) DO UPDATE SET
                       verdict_group = EXCLUDED.verdict_group, headline = EXCLUDED.headline,
                       narrative = EXCLUDED.narrative, key_numbers = EXCLUDED.key_numbers,
                       top_risks = EXCLUDED.top_risks, positives = EXCLUDED.positives,
                       data_gaps = EXCLUDED.data_gaps, questions_to_ask = EXCLUDED.questions_to_ask,
                       model = EXCLUDED.model, latency_ms = EXCLUDED.latency_ms,
                       raw_response = EXCLUDED.raw_response, error = EXCLUDED.error""",
                {"run": UUID(run_id), "verdict": payload["verdict_group"],
                 "headline": payload["headline"], "narrative": payload["narrative"],
                 "kn": Jsonb(payload["key_numbers"]), "tr": Jsonb(payload["top_risks"]),
                 "pos": Jsonb(payload["positives"]), "gaps": Jsonb(payload["data_gaps"]),
                 "q": Jsonb(payload["questions_to_ask"]), "model": payload["model"],
                 "lat": payload["latency_ms"], "pt": payload["prompt_tokens"],
                 "ct": payload["completion_tokens"], "raw": Jsonb(payload.get("raw_response")),
                 "err": payload.get("error")})
        await conn.commit()


async def save_statements(run_id: str, statements: List[Dict[str, Any]]) -> None:
    if not statements:
        return
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            for st in statements:
                await cur.execute(
                    """INSERT INTO audit.run_statements
                           (run_id, block, statement, fact_id, field_ref, fact_value, grounding)
                       VALUES (%(run)s, %(block)s::audit.block_key, %(text)s, %(fid)s,
                               %(ref)s, %(val)s, %(gr)s::audit.grounding)""",
                    {"run": UUID(run_id), "block": st.get("block"), "text": st["statement"],
                     "fid": st.get("fact_id"), "ref": st.get("field_ref"),
                     "val": st.get("fact_value"), "gr": st.get("grounding", "NO_REF")})
        await conn.commit()


# --------------------------- чтение прогона --------------------------

async def get_run(run_id: str) -> Optional[Dict[str, Any]]:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                """SELECT r.id::text AS run_id, r.inn, r.status::text AS status,
                          r.block_model, r.summary_model, r.calculator_ver, r.llm_mode,
                          r.started_at, r.finished_at, r.latency_ms,
                          r.prompt_tokens, r.completion_tokens, r.error,
                          c.short_name, c.ogrn,
                          s.risk_level::text AS risk_level,
                          s.zsk_risk_level::text AS zsk_risk_level,
                          s.report_date
                     FROM audit.analysis_runs r
                     LEFT JOIN core.companies c ON c.id = r.company_id
                     LEFT JOIN core.report_snapshots s ON s.id = r.snapshot_id
                    WHERE r.id = %(id)s""",
                {"id": UUID(run_id)})
            run = await cur.fetchone()
            if run is None:
                return None
            await cur.execute(
                """SELECT block::text AS block, signal::text AS signal, headline,
                          facts_sentence, interpretation, findings, data_gaps,
                          cannot_assess, model, latency_ms, error
                     FROM audit.run_blocks WHERE run_id = %(id)s
                    ORDER BY array_position(ARRAY['identity','reliability','finance','experience'],
                                            block::text)""",
                {"id": UUID(run_id)})
            run["blocks"] = await cur.fetchall()
            await cur.execute(
                """SELECT verdict_group::text AS verdict_group, headline, narrative,
                          key_numbers, top_risks, positives, data_gaps, questions_to_ask,
                          model, latency_ms, error
                     FROM audit.run_summaries WHERE run_id = %(id)s""",
                {"id": UUID(run_id)})
            run["summary"] = await cur.fetchone()
            await cur.execute(
                "SELECT * FROM audit.v_run_grounding WHERE run_id = %(id)s",
                {"id": UUID(run_id)})
            run["grounding"] = await cur.fetchone()
    return run


async def list_runs(inn: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    sql = """SELECT r.id::text AS run_id, r.inn, r.status::text AS status, r.started_at,
                    r.finished_at, r.latency_ms, r.llm_mode,
                    su.verdict_group::text AS verdict_group, su.headline
               FROM audit.analysis_runs r
               LEFT JOIN audit.run_summaries su ON su.run_id = r.id
              WHERE (%(inn)s::text IS NULL OR r.inn = %(inn)s::text)
              ORDER BY r.started_at DESC LIMIT %(limit)s"""
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute(sql, {"inn": inn, "limit": limit})
            return await cur.fetchall()
