from __future__ import annotations

import os
from pathlib import Path

import asyncpg

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schema.sql"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


async def create_pool(dsn: str) -> asyncpg.Pool:
    min_size = max(1, _int_env("IRIAI_DB_POOL_MIN_SIZE", 1))
    max_size = max(min_size, _int_env("IRIAI_DB_POOL_MAX_SIZE", 5))
    command_timeout = max(1, _int_env("IRIAI_DB_COMMAND_TIMEOUT_SECONDS", 30))
    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
    )
    assert pool is not None
    return pool


async def ensure_schema(pool: asyncpg.Pool) -> None:
    sql = SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)
