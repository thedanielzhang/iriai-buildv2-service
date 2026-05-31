from __future__ import annotations

import pytest

from iriai_build_v2.storage.features import AdvisoryLockTimeout, PostgresFeatureStore


class _RecordingPool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        return []


class _FakeTx:
    def __init__(self) -> None:
        self.started = False
        self.rolled_back = False

    async def start(self) -> None:
        self.started = True

    async def rollback(self) -> None:
        self.rolled_back = True


class _FakeLockConn:
    """Connection double whose ``pg_try_advisory_xact_lock`` returns scripted bools."""

    def __init__(self, results: list[bool], *, default: bool = True) -> None:
        self._results = list(results)
        self._default = default
        self.fetchval_calls: list[tuple[str, tuple[object, ...]]] = []
        self.tx = _FakeTx()

    def transaction(self) -> _FakeTx:
        return self.tx

    async def fetchval(self, sql: str, *args: object) -> bool:
        self.fetchval_calls.append((sql, args))
        return self._results.pop(0) if self._results else self._default


class _LockPool:
    def __init__(self, conn: _FakeLockConn) -> None:
        self._conn = conn
        self.acquired = 0
        self.released: list[object] = []

    async def acquire(self) -> _FakeLockConn:
        self.acquired += 1
        return self._conn

    async def release(self, conn: object) -> None:
        self.released.append(conn)


@pytest.mark.asyncio
async def test_advisory_lock_acquires_non_blocking_and_releases():
    conn = _FakeLockConn([True])
    pool = _LockPool(conn)
    store = PostgresFeatureStore(pool)  # type: ignore[arg-type]

    async with store.advisory_lock("feat-1", "planning-decisions") as held:
        assert held is conn

    # NON-blocking acquisition only — never the old pg_advisory_xact_lock that
    # parked the loop forever behind a contended holder.
    sqls = [s for s, _ in conn.fetchval_calls]
    assert sqls and all("pg_try_advisory_xact_lock" in s for s in sqls)
    assert conn.tx.started and conn.tx.rolled_back
    assert pool.released == [conn]


@pytest.mark.asyncio
async def test_advisory_lock_retries_until_acquired(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("IRIAI_ADVISORY_LOCK_RETRY_SECONDS", "0.001")
    conn = _FakeLockConn([False, False, True])
    pool = _LockPool(conn)
    store = PostgresFeatureStore(pool)  # type: ignore[arg-type]

    async with store.advisory_lock("feat-1", "planning-decisions"):
        pass

    assert len(conn.fetchval_calls) == 3
    assert pool.released == [conn]


@pytest.mark.asyncio
async def test_advisory_lock_times_out_when_contended(monkeypatch: pytest.MonkeyPatch):
    # A perpetually-contended lock must FAIL FAST (loud error), not hang forever.
    monkeypatch.setenv("IRIAI_ADVISORY_LOCK_TIMEOUT_SECONDS", "0.05")
    monkeypatch.setenv("IRIAI_ADVISORY_LOCK_RETRY_SECONDS", "0.001")
    conn = _FakeLockConn([], default=False)
    pool = _LockPool(conn)
    store = PostgresFeatureStore(pool)  # type: ignore[arg-type]

    with pytest.raises(AdvisoryLockTimeout):
        async with store.advisory_lock("feat-1", "planning-decisions"):
            pass

    # Even on timeout the connection is returned so the (un-acquired) transaction
    # is rolled back and the pooled connection is not leaked.
    assert conn.tx.rolled_back
    assert pool.released == [conn]


@pytest.mark.asyncio
async def test_advisory_lock_releases_connection_when_body_raises():
    # A body that fails / is cancelled mid-block must not leak an idle-in-txn
    # holder: the connection is always released, freeing the advisory lock.
    conn = _FakeLockConn([True])
    pool = _LockPool(conn)
    store = PostgresFeatureStore(pool)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError):
        async with store.advisory_lock("feat-1", "planning-decisions"):
            raise RuntimeError("boom")

    assert conn.tx.rolled_back
    assert pool.released == [conn]


@pytest.mark.asyncio
async def test_feature_store_list_event_summaries_uses_bounded_projection_query():
    pool = _RecordingPool()
    store = PostgresFeatureStore(pool)  # type: ignore[arg-type]

    rows = await store.list_event_summaries(
        "feat-1",
        after_id=0,
        limit=999,
        order="desc",
        group_idx=44,
        preview_chars=9_000,
    )

    assert rows == []
    sql, args = pool.fetches[0]
    assert "SELECT *" not in sql
    assert "substring(content from 1 for $4)" in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT $3" in sql
    assert args == ("feat-1", 0, 500, 4000, "44")
