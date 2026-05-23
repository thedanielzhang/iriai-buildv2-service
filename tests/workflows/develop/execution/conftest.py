"""Real-Postgres pytest fixtures for the durable merge queue (Slice 08).

The merge queue's correctness is in Postgres lease/transaction/FK/locking
semantics, which the repo's in-memory ``_FakeConnection`` cannot exercise.
These fixtures spin up a throwaway database, load ``schema.sql``, and tear it
down. When no Postgres is reachable the dependent tests skip rather than fail.

This conftest is directory-scoped (``tests/workflows/develop/execution/``) so
the fixtures are offered only to merge-queue / execution tests and cannot
affect the rest of the suite.

Overridable via env: ``IRIAI_TEST_PGHOST`` (default ``localhost``),
``IRIAI_TEST_PGPORT`` (default ``5431``), ``IRIAI_TEST_PGUSER`` (default
``$USER``), ``IRIAI_TEST_PGPASSWORD`` (default empty / trust auth).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_PATH = _REPO_ROOT / "schema.sql"

_PG_HOST = os.environ.get("IRIAI_TEST_PGHOST", "localhost")
_PG_PORT = os.environ.get("IRIAI_TEST_PGPORT", "5431")
_PG_USER = os.environ.get("IRIAI_TEST_PGUSER") or os.environ.get("USER") or "postgres"
_PG_PASSWORD = os.environ.get("IRIAI_TEST_PGPASSWORD", "")


def _dsn(database: str) -> str:
    auth = _PG_USER if not _PG_PASSWORD else f"{_PG_USER}:{_PG_PASSWORD}"
    return f"postgresql://{auth}@{_PG_HOST}:{_PG_PORT}/{database}"


@pytest.fixture(scope="session")
def merge_queue_database() -> Iterator[str]:
    """A throwaway Postgres database with ``schema.sql`` loaded.

    Yields a DSN. Skips dependent tests when no Postgres is reachable. DB
    lifecycle runs synchronously (its own short-lived event loops) so the
    fixture does not contend with pytest-asyncio's per-test loop.
    """

    db_name = f"iriai_mq_test_{uuid.uuid4().hex[:12]}"

    async def _probe() -> None:
        conn = await asyncpg.connect(_dsn("postgres"))
        await conn.close()

    async def _create() -> None:
        admin = await asyncpg.connect(_dsn("postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(_dsn(db_name))
        try:
            await conn.execute(_SCHEMA_PATH.read_text())
        finally:
            await conn.close()

    async def _drop() -> None:
        admin = await asyncpg.connect(_dsn("postgres"))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await admin.close()

    try:
        asyncio.run(_probe())
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - env
        pytest.skip(f"Postgres unavailable for merge-queue tests: {exc}")

    asyncio.run(_create())
    try:
        yield _dsn(db_name)
    finally:
        asyncio.run(_drop())


async def _truncate_all(conn: asyncpg.Connection) -> None:
    rows = await conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
    )
    if not rows:
        return
    names = ", ".join(f'"{row["tablename"]}"' for row in rows)
    await conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")


@pytest_asyncio.fixture
async def mq_dsn(merge_queue_database: str) -> str:
    """DSN of a clean-slate test database (use for multi-connection tests).

    Truncates every table before the test so each test starts empty.
    """

    conn = await asyncpg.connect(merge_queue_database)
    try:
        await _truncate_all(conn)
    finally:
        await conn.close()
    return merge_queue_database


@pytest_asyncio.fixture
async def mq_conn(mq_dsn: str) -> AsyncIterator[asyncpg.Connection]:
    """A single connection to a clean-slate merge-queue test database."""

    conn = await asyncpg.connect(mq_dsn)
    try:
        yield conn
    finally:
        await conn.close()
