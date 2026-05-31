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
    assert calls == [
        (
            "postgresql://test",
            {"min_size": 1, "max_size": 5, "command_timeout": 30},
        )
    ]


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

    assert calls == [
        (
            "postgresql://test",
            {"min_size": 4, "max_size": 4, "command_timeout": 12},
        )
    ]


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
