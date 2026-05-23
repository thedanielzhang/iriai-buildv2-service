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
