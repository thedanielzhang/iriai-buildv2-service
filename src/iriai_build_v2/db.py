from __future__ import annotations

from pathlib import Path

import asyncpg

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema.sql"


async def create_pool(dsn: str) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(dsn)
    assert pool is not None
    return pool


async def ensure_schema(pool: asyncpg.Pool) -> None:
    sql = SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)
