from __future__ import annotations

import pytest

from iriai_build_v2 import db


@pytest.mark.asyncio
async def test_create_pool_uses_bounded_safety_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IRIAI_DB_POOL_MIN_SIZE", raising=False)
    monkeypatch.delenv("IRIAI_DB_POOL_MAX_SIZE", raising=False)
    monkeypatch.delenv("IRIAI_DB_COMMAND_TIMEOUT_SECONDS", raising=False)
    calls: list[tuple[str, dict[str, object]]] = []
    sentinel = object()

    async def fake_create_pool(dsn: str, **kwargs: object) -> object:
        calls.append((dsn, kwargs))
        return sentinel

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)

    pool = await db.create_pool("postgresql://test")

    assert pool is sentinel
    assert len(calls) == 1
    dsn, kwargs = calls[0]
    assert dsn == "postgresql://test"
    assert kwargs["min_size"] == 1
    assert kwargs["max_size"] == 5
    assert kwargs["command_timeout"] == 30
    assert callable(kwargs["setup"])  # session-guard hook installed


@pytest.mark.asyncio
async def test_create_pool_applies_env_overrides_and_clamps_max_to_min(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_DB_POOL_MIN_SIZE", "4")
    monkeypatch.setenv("IRIAI_DB_POOL_MAX_SIZE", "2")
    monkeypatch.setenv("IRIAI_DB_COMMAND_TIMEOUT_SECONDS", "12")
    calls: list[tuple[str, dict[str, object]]] = []

    async def fake_create_pool(dsn: str, **kwargs: object) -> object:
        calls.append((dsn, kwargs))
        return object()

    monkeypatch.setattr(db.asyncpg, "create_pool", fake_create_pool)

    await db.create_pool("postgresql://test")

    assert len(calls) == 1
    dsn, kwargs = calls[0]
    assert dsn == "postgresql://test"
    assert kwargs["min_size"] == 4
    assert kwargs["max_size"] == 4
    assert kwargs["command_timeout"] == 12
    assert callable(kwargs["setup"])


@pytest.mark.asyncio
async def test_setup_session_guards_sets_idle_txn_and_lock_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A leaked idle-in-transaction connection holding an advisory lock deadlocks
    # any waiter forever; these GUCs make Postgres self-heal that. Re-applied on
    # every acquire because asyncpg's reset-on-release clears session settings.
    monkeypatch.delenv("IRIAI_DB_IDLE_IN_TXN_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("IRIAI_DB_LOCK_TIMEOUT_MS", raising=False)
    executed: list[str] = []

    class _FakeConn:
        async def execute(self, sql: str) -> None:
            executed.append(sql)

    await db._setup_session_guards(_FakeConn())

    assert any("idle_in_transaction_session_timeout = 60000" in s for s in executed)
    assert any("lock_timeout = 90000" in s for s in executed)

    monkeypatch.setenv("IRIAI_DB_IDLE_IN_TXN_TIMEOUT_MS", "5000")
    monkeypatch.setenv("IRIAI_DB_LOCK_TIMEOUT_MS", "7000")
    executed.clear()
    await db._setup_session_guards(_FakeConn())
    assert any("idle_in_transaction_session_timeout = 5000" in s for s in executed)
    assert any("lock_timeout = 7000" in s for s in executed)


@pytest.mark.asyncio
async def test_bounded_release_defaults_finite_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    # asyncpg's Pool.release runs the connection reset under asyncio.shield with
    # timeout=None — an unbounded, uncancellable hang on a dead socket. Every
    # release must default to a finite timeout so the reset can't deadlock.
    monkeypatch.delenv("IRIAI_DB_RELEASE_TIMEOUT_SECONDS", raising=False)
    recorded: list[tuple[object, object]] = []

    async def fake_release(connection: object, *, timeout: object) -> str:
        recorded.append((connection, timeout))
        return "released"

    out = await db._bounded_release(fake_release, "conn", None)
    assert out == "released"
    assert recorded[-1] == ("conn", 15)  # None -> finite default

    await db._bounded_release(fake_release, "conn", 3)
    assert recorded[-1] == ("conn", 3)  # explicit timeout preserved

    monkeypatch.setenv("IRIAI_DB_RELEASE_TIMEOUT_SECONDS", "7")
    await db._bounded_release(fake_release, "conn", None)
    assert recorded[-1] == ("conn", 7)  # env override


def test_bounded_release_pool_is_a_pool_subclass_with_compatible_layout() -> None:
    # __class__ swap in create_pool requires identical C layout (so __slots__=()).
    assert issubclass(db._BoundedReleasePool, db.asyncpg.pool.Pool)
    assert db._BoundedReleasePool.__basicsize__ == db.asyncpg.pool.Pool.__basicsize__
