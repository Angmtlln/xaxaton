"""SQL-only company reader shared by direct mode and the MCP server."""
from typing import Any, Dict, List, Optional
from app.infrastructure.db import get_pool

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

SHORTLIST_SORT = {
    "proceeds": "proceeds",
    "profit": "profit",
    "claims": "claims_amount",
    "enforcement": "enforcement_count",
}


class PostgresCompanyDataReader:
    def __init__(self, pool=None):
        self.pool = pool

    def _pool(self):
        return self.pool if self.pool is not None else get_pool()

    async def data_source_status(self):
        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1 AS ok")
                return {"database": (await cur.fetchone())["ok"] == 1}

    async def get_latest_snapshot(self, inn: str) -> Optional[Dict[str, Any]]:
        """Последний отчёт по ИНН вместе с сырым документом карточки."""
        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(SNAPSHOT_SQL, {"inn": inn})
                return await cur.fetchone()

    async def get_selection_snapshots(self, snapshot_ids: List[int]) -> List[Dict[str, Any]]:
        """Read only the bounded exact snapshots selected by the filter query."""
        if not snapshot_ids:
            return []
        if len(snapshot_ids) > 50:
            raise ValueError("Selection is limited to 50 snapshots")
        sql = SNAPSHOT_SQL.replace("c.inn = %(inn)s", "s.id = ANY(%(ids)s)").replace(
            "ORDER  BY s.report_date DESC\nLIMIT  1", "ORDER BY c.inn, s.id")
        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {"ids": snapshot_ids})
                return await cur.fetchall()

    async def search_companies(self, query: str, limit: int = 5) -> Dict[str, Any]:
        from app.domain.company_search import CompanySearchArgs, CompanySearchResult
        args = CompanySearchArgs(query=query, limit=limit)
        # strpos/left treat %, _ and backslash literally, without LIKE escaping.
        sql = """
        WITH q AS (SELECT core.normalize_company_name(%(query)s) AS key),
        matches AS (
          SELECT v.inn, v.name, v.full_name, v.address, v.snapshot_id,
                 CASE WHEN q.key IN (v.short_key, v.full_key, v.inn) THEN 0
                      WHEN left(v.short_key, length(q.key)) = q.key
                        OR left(v.full_key, length(q.key)) = q.key
                        OR left(v.inn, length(q.key)) = q.key THEN 1 ELSE 2 END AS priority
          FROM core.v_company_name_search v CROSS JOIN q
          WHERE length(q.key) >= 2 AND (strpos(v.short_key, q.key) > 0
            OR strpos(v.full_key, q.key) > 0 OR left(v.inn, length(q.key)) = q.key)
        ), page AS (
          SELECT inn, name, full_name, address, snapshot_id,
                 CASE priority WHEN 0 THEN 'exact' WHEN 1 THEN 'prefix' ELSE 'partial' END AS match,
                 priority
          FROM matches ORDER BY priority, inn LIMIT %(limit)s
        )
        SELECT (SELECT count(*) FROM matches) AS total,
               (SELECT count(*) FROM matches WHERE priority = 0) AS exact_total,
               COALESCE((SELECT jsonb_agg(to_jsonb(page) - 'priority' ORDER BY priority, inn)
                         FROM page), '[]'::jsonb) AS rows
        """
        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, args.model_dump())
                return CompanySearchResult.model_validate(await cur.fetchone()).model_dump()

    async def list_companies(self, limit: int = 50, offset: int = 0,
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

        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("\n".join(sql), params)
                return await cur.fetchall()

    async def get_connection_candidates(self, limit: int = 10001) -> List[Dict[str, Any]]:
        """Bounded identity projection of latest snapshots, without analytical arrays."""
        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT * FROM core.v_connection_candidates ORDER BY inn LIMIT %(limit)s",
                    {"limit": limit},
                )
                return await cur.fetchall()

    async def get_snapshots_for_connections(self, inns: List[str]) -> List[Dict[str, Any]]:
        """One batch for the bounded one-hop review; no per-neighbour pipeline."""
        if not inns:
            return []
        sql = SNAPSHOT_SQL.replace("SELECT s.id", "SELECT DISTINCT ON (c.inn) s.id").replace(
            "c.inn = %(inn)s", "c.inn = ANY(%(inns)s)"
        ).replace("ORDER  BY s.report_date DESC\nLIMIT  1", "ORDER BY c.inn, s.report_date DESC, s.id DESC")
        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(sql, {"inns": inns})
                return await cur.fetchall()

    async def find_companies(self,
        *, activity_query=None, okved_prefix=None, activity_scope="any",
        min_proceeds=None, max_proceeds=None, min_profit=None, max_profit=None,
        risk_level=None, zsk_risk_level=None, hard_stops=None,
        min_claims_amount=None, max_claims_amount=None,
        min_enforcement_count=None, max_enforcement_count=None,
        sort_by="proceeds", order="desc", limit=10, ranking=None,
    ) -> Dict[str, Any]:
        """Подборка карточек по проверенным полям витрины; выводы здесь не делаются."""
        ranges = (
            ("min_proceeds", "proceeds", ">=", min_proceeds),
            ("max_proceeds", "proceeds", "<=", max_proceeds),
            ("min_profit", "profit", ">=", min_profit),
            ("max_profit", "profit", "<=", max_profit),
            ("min_claims_amount", "claims_amount", ">=", min_claims_amount),
            ("max_claims_amount", "claims_amount", "<=", max_claims_amount),
            ("min_enforcement_count", "enforcement_count", ">=", min_enforcement_count),
            ("max_enforcement_count", "enforcement_count", "<=", max_enforcement_count),
        )
        where: List[str] = []
        params: Dict[str, Any] = {"limit": limit}
        for name, column, op, value in ranges:
            if value is not None:
                where.append("AND %s %s %%(%s)s" % (column, op, name))
                params[name] = value
        if risk_level:
            where.append("AND risk_level = %(risk_level)s")
            params["risk_level"] = risk_level
        if zsk_risk_level:
            where.append("AND zsk_risk_level = %(zsk)s")
            params["zsk"] = zsk_risk_level
        if hard_stops == "with":
            where.append("AND hard_stops > 0")
        elif hard_stops == "without":
            where.append("AND hard_stops = 0")

        # Колонка сортировки берётся из allowlist, а не из аргумента модели.
        column = SHORTLIST_SORT.get(sort_by, "proceeds")
        direction = "ASC" if order == "asc" else "DESC"
        activity_filter = ["ac.snapshot_id = v.snapshot_id"]
        if activity_scope == "main" or not (activity_query or okved_prefix):
            activity_filter.append("ac.is_main")
        if activity_query:
            activity_filter.append("to_tsvector('russian', COALESCE(ac.description, '')) @@ plainto_tsquery('russian', %(activity_query)s)")
            params["activity_query"] = activity_query
        if okved_prefix:
            activity_filter.append("replace(ac.code, '.', '') LIKE %(okved_children)s")
            params["okved_children"] = okved_prefix.replace(".", "") + "%"
        match_sql = " AND ".join(activity_filter)
        if activity_query or okved_prefix:
            where.append("AND EXISTS (SELECT 1 FROM core.activity_codes ac WHERE " + match_sql + ")")
        # Сначала фильтруем компании через EXISTS: дополнительные коды не размножают
        # строки, count и LIMIT. Показываем до пяти реальных совпадений на компанию.
        selection = """
        SELECT v.*, COALESCE((SELECT jsonb_agg(matches) FROM (
            SELECT ac.code, ac.description, ac.is_main,
                   CASE WHEN ac.is_main THEN 'report.kindsOfActivityInfo.mainKindOfActivity'
                        ELSE 'report.kindsOfActivityInfo.otherKindsOfActivity[' || (ac.idx - 1)::text || ']' END AS field_ref
            FROM core.activity_codes ac WHERE %s
            ORDER BY ac.is_main DESC, ac.code, ac.idx LIMIT 5
        ) matches), '[]'::jsonb) AS matched_activities
        FROM core.v_company_shortlist v WHERE 1 = 1
        """ % match_sql
        if not (activity_query or okved_prefix):
            selection = "SELECT v.*, '[]'::jsonb AS matched_activities FROM core.v_company_shortlist v WHERE 1 = 1"
        filtered = selection + "\n" + "\n".join(where)
        ranking = ranking or []
        # SQL identifiers and directions are selected only from validated allowlists.
        if len(ranking) > 4 or len({item["metric"] for item in ranking}) != len(ranking):
            raise ValueError("Invalid ranking criteria")
        order_sql = f"{column} {direction} NULLS LAST, inn"
        ranked = filtered
        if ranking:
            if any(item["metric"] not in SHORTLIST_SORT or item["order"] not in {"asc", "desc"} for item in ranking):
                raise ValueError("Invalid ranking criterion")
            ranked += "\n" + "\n".join(
                "AND %s IS NOT NULL" % SHORTLIST_SORT[item["metric"]] for item in ranking)
            order_sql = ", ".join("%s %s" % (SHORTLIST_SORT[item["metric"]], item["order"].upper()) for item in ranking) + ", inn"

        async with self._pool().connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) AS total FROM (%s) q" % filtered, params)
                total = (await cur.fetchone())["total"]
                eligible_total = None
                if ranking:
                    await cur.execute("SELECT count(*) AS total FROM (%s) q" % ranked, params)
                    eligible_total = int((await cur.fetchone())["total"])
                await cur.execute(
                    "%s ORDER BY %s LIMIT %%(limit)s" % (ranked, order_sql),
                    params,
                )
                rows = await cur.fetchall()
        result = {"total": int(total), "rows": rows}
        if ranking:
            result["eligible_total"] = eligible_total
        return result
