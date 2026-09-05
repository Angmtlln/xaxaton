"""Пул подключений к PostgreSQL (psycopg 3, async)."""
import logging
from typing import Optional

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import Settings

log = logging.getLogger(__name__)

_pool: Optional[AsyncConnectionPool] = None


async def init_pool(settings: Settings) -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=settings.database_url,
            min_size=settings.db_pool_min,
            max_size=settings.db_pool_max,
            kwargs={"row_factory": dict_row},
            open=False,
        )
        await _pool.open(wait=True, timeout=15)
        log.info("Пул подключений к БД открыт")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("Пул подключений к БД закрыт")


def get_pool() -> AsyncConnectionPool:
    if _pool is None:
        raise RuntimeError("Пул БД не инициализирован")
    return _pool


async def healthcheck() -> bool:
    async with get_pool().connection() as conn:
        async with conn.cursor() as cur:
            await cur.execute("SELECT 1 AS ok")
            row = await cur.fetchone()
            return bool(row and row.get("ok") == 1)
