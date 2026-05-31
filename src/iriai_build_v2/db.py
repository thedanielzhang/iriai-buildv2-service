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


def _release_timeout_seconds() -> float:
    return float(max(1, _int_env("IRIAI_DB_RELEASE_TIMEOUT_SECONDS", 15)))


async def _bounded_release(pool_release, connection, timeout):
    """Default a connection release to a finite timeout. asyncpg's reset
    (``command_timeout`` does NOT cover it) is otherwise unbounded."""
    if timeout is None:
        timeout = _release_timeout_seconds()
    return await pool_release(connection, timeout=timeout)


class _BoundedReleasePool(asyncpg.pool.Pool):
    """asyncpg's ``Pool.release`` returns the connection by running its reset
    (a ROLLBACK/DISCARD round-trip) under ``asyncio.shield(ch.release(timeout))``
    with ``timeout=None`` by default. On a dead Postgres socket or an in-flight
    transaction that reset hangs FOREVER, and because it is shielded the
    cancellation a watchdog/caller sends is swallowed — an uncancellable deadlock
    that froze the whole event loop (observed: ``resume_workflow`` parked on
    ``PoolConnectionHolder.release`` after a dispatch, blocking the bridge).

    Default EVERY release — including the implicit ``async with pool.acquire()``
    auto-release — to a finite timeout so the reset is bounded; on timeout asyncpg
    discards the bad connection instead of deadlocking. ``__slots__ = ()`` keeps
    the C layout identical to ``Pool`` so ``pool.__class__`` can be swapped to
    this subclass (create_pool exposes no ``pool_class`` hook)."""

    __slots__ = ()

    async def release(self, connection, *, timeout=None):
        return await _bounded_release(super().release, connection, timeout)


async def _setup_session_guards(conn: asyncpg.Connection) -> None:
    """Applied on EVERY acquire (asyncpg's reset-on-release clears session GUCs).

    A coroutine cancelled mid-transaction (e.g. by a dispatch watchdog) can leave
    its connection ``idle in transaction`` holding an advisory/row lock, which
    then blocks FOREVER anything that waits for that lock — observed:
    ``resume_workflow`` parked on ``pg_advisory_lock`` behind a 12-minute
    idle-in-transaction holder of ``pg_advisory_xact_lock``.
    ``idle_in_transaction_session_timeout`` makes Postgres terminate such leaked
    sessions so their locks release; ``lock_timeout`` bounds any remaining
    blocking lock acquisition so it raises instead of hanging the event loop."""
    idle_ms = max(1000, _int_env("IRIAI_DB_IDLE_IN_TXN_TIMEOUT_MS", 60000))
    lock_ms = max(1000, _int_env("IRIAI_DB_LOCK_TIMEOUT_MS", 90000))
    await conn.execute(f"SET idle_in_transaction_session_timeout = {idle_ms}")
    await conn.execute(f"SET lock_timeout = {lock_ms}")


async def create_pool(dsn: str) -> asyncpg.Pool:
    min_size = max(1, _int_env("IRIAI_DB_POOL_MIN_SIZE", 1))
    max_size = max(min_size, _int_env("IRIAI_DB_POOL_MAX_SIZE", 5))
    command_timeout = max(1, _int_env("IRIAI_DB_COMMAND_TIMEOUT_SECONDS", 30))
    pool = await asyncpg.create_pool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        command_timeout=command_timeout,
        setup=_setup_session_guards,
    )
    assert pool is not None
    # Bound every connection release so a wedged reset (dead socket / in-flight
    # txn) can't deadlock the loop under asyncpg's internal shield. Guarded so a
    # test double / unexpected return is left untouched.
    if isinstance(pool, asyncpg.pool.Pool):
        pool.__class__ = _BoundedReleasePool
    return pool


async def ensure_schema(pool: asyncpg.Pool) -> None:
    sql = SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)
