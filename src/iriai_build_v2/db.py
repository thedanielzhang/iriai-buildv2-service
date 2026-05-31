from __future__ import annotations

import asyncio
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


def _release_grace_seconds() -> float:
    return float(max(0, _int_env("IRIAI_DB_RELEASE_GRACE_SECONDS", 5)))


async def _bounded_release(pool_release, connection, timeout):
    """Release a pooled connection under a HARD outer timeout.

    Passing ``timeout`` to asyncpg's ``Pool.release`` is NOT sufficient: that
    timeout is handed to ``Connection.reset``, and on a dead/half-open socket the
    reset has been observed to hang inside ``Connection.reset`` *under
    ``asyncio.shield``* — so asyncpg's own timeout never fires AND the cancellation
    a caller/watchdog sends is swallowed. The owning coroutine (e.g.
    ``resume_workflow``) then deadlocks forever awaiting the shielded release
    (confirmed via an asyncio task dump: ``PoolConnectionHolder.release`` parked at
    ``await self._con.reset(timeout=budget)``).

    Wrap the whole release in ``asyncio.wait_for`` so a wedged reset can't outlast
    ``timeout + grace``. On expiry, ``terminate()`` the connection so the orphaned
    shielded reset unwinds (and the dead connection is dropped from the pool), then
    surface ``TimeoutError`` instead of hanging the event loop."""
    if timeout is None:
        timeout = _release_timeout_seconds()
    try:
        return await asyncio.wait_for(
            pool_release(connection, timeout=timeout),
            timeout=timeout + _release_grace_seconds(),
        )
    except asyncio.TimeoutError:
        terminate = getattr(connection, "terminate", None)
        if callable(terminate):
            try:
                terminate()
            except Exception:
                pass
        raise


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

    ``lock_timeout`` bounds any BLOCKING lock acquisition (row locks, the
    remaining ``pg_advisory_xact_lock`` call sites) so it raises instead of
    hanging the event loop.

    NOTE: deliberately does NOT set ``idle_in_transaction_session_timeout``. That
    guard (tried in 48a2735) terminates the connection of a LEGITIMATE
    advisory-lock holder that is held across a long op, leaving the holder's
    coroutine to fail on a dead connection (a dead-connection hang). The leaked /
    contended-holder deadlock is instead fixed at the source: ``advisory_lock``
    now acquires non-blocking (``pg_try_advisory_xact_lock`` + bounded retry) and
    always releases its connection, so no waiter can park forever and a cancelled
    holder no longer leaks its lock."""
    lock_ms = max(1000, _int_env("IRIAI_DB_LOCK_TIMEOUT_MS", 90000))
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
