from __future__ import annotations

import pytest

from iriai_build_v2 import db_safety
from iriai_build_v2.db_safety import (
    ConcurrentIndexSpec,
    capture_db_safety_snapshot,
    install_safety_indexes,
    rollback_safety_indexes,
)


class _FakeConnection:
    def __init__(self, *, invalid_index_exists: bool = True) -> None:
        self.executes: list[tuple[str, tuple[object, ...]]] = []
        self.fetchvals: list[tuple[str, tuple[object, ...]]] = []
        self.fetches: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrows: list[tuple[str, tuple[object, ...]]] = []
        self.invalid_index_exists = invalid_index_exists
        self.closed = False

    async def execute(self, sql: str, *args: object) -> None:
        self.executes.append((sql, args))

    async def fetchval(self, sql: str, *args: object) -> bool:
        self.fetchvals.append((sql, args))
        return self.invalid_index_exists

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        return [{"relname": "artifacts"}] if "pg_stat_user_tables" in sql else []

    async def fetchrow(self, sql: str, *args: object):
        self.fetchrows.append((sql, args))
        return {"pending_rows": 0} if "public_dashboard_outbox" in sql else {
            "temp_files": 0,
            "temp_bytes": 0,
            "active_connections": 1,
        }

    async def close(self) -> None:
        self.closed = True


async def _connect(conn: _FakeConnection) -> _FakeConnection:
    return conn


@pytest.mark.asyncio
async def test_install_safety_indexes_uses_timeouts_concurrently_and_drops_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection()
    monkeypatch.setattr(db_safety.asyncpg, "connect", lambda _dsn: _connect(conn))
    spec = ConcurrentIndexSpec(
        name="idx_test_safety",
        table="artifacts",
        columns_sql="(feature_id, id)",
    )

    installed = await install_safety_indexes(
        "postgresql://test",
        lock_timeout_ms=123,
        statement_timeout_ms=456,
        specs=(spec,),
    )

    assert installed == ["idx_test_safety"]
    executed = [sql for sql, _args in conn.executes]
    assert executed[0] == "SET lock_timeout = '123ms'"
    assert executed[1] == "SET statement_timeout = '456ms'"
    assert executed[2] == "DROP INDEX CONCURRENTLY IF EXISTS idx_test_safety"
    assert executed[3] == (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test_safety "
        "ON artifacts (feature_id, id)"
    )
    assert conn.closed is True


@pytest.mark.asyncio
async def test_install_safety_indexes_keeps_valid_existing_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection(invalid_index_exists=False)
    monkeypatch.setattr(db_safety.asyncpg, "connect", lambda _dsn: _connect(conn))
    spec = ConcurrentIndexSpec(
        name="idx_test_safety",
        table="artifacts",
        columns_sql="(feature_id, id)",
    )

    installed = await install_safety_indexes("postgresql://test", specs=(spec,))

    assert installed == ["idx_test_safety"]
    executed = [sql for sql, _args in conn.executes]
    assert "DROP INDEX CONCURRENTLY IF EXISTS idx_test_safety" not in executed
    assert (
        "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_test_safety "
        "ON artifacts (feature_id, id)"
    ) in executed
    assert conn.closed is True


@pytest.mark.asyncio
async def test_rollback_safety_indexes_drops_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection()
    monkeypatch.setattr(db_safety.asyncpg, "connect", lambda _dsn: _connect(conn))
    specs = (
        ConcurrentIndexSpec("idx_first", "artifacts", "(feature_id, id)"),
        ConcurrentIndexSpec("idx_second", "events", "(feature_id, id DESC)"),
    )

    dropped = await rollback_safety_indexes("postgresql://test", specs=specs)

    assert dropped == ["idx_second", "idx_first"]
    assert [sql for sql, _args in conn.executes] == [
        "DROP INDEX CONCURRENTLY IF EXISTS idx_second",
        "DROP INDEX CONCURRENTLY IF EXISTS idx_first",
    ]
    assert conn.closed is True


@pytest.mark.asyncio
async def test_capture_db_safety_snapshot_uses_bounded_read_only_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConnection()
    monkeypatch.setattr(db_safety.asyncpg, "connect", lambda _dsn: _connect(conn))

    snapshot = await capture_db_safety_snapshot("postgresql://test", feature_id="feat-1")

    assert snapshot["feature_id"] == "feat-1"
    all_sql = "\n".join(
        [sql for sql, _args in conn.fetches]
        + [sql for sql, _args in conn.fetchrows]
    )
    assert "SELECT *" not in all_sql
    assert "LIMIT 100" in all_sql
    assert "WHERE $1::text IS NULL OR feature_id = $1" in all_sql
    assert "db_value_bytes" in all_sql
    assert "logical_value_bytes" in all_sql
    assert "value LIKE '{\"__iriai_spill_v1__\"%'" in all_sql
    assert 'substring(value from \'"bytes"\\s*:\\s*([0-9]+)\')' in all_sql
    assert "ELSE pg_column_size(value)::bigint END" in all_sql
    assert conn.closed is True
