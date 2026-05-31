from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from contextlib import asynccontextmanager, suppress
from typing import Any

import asyncpg

from iriai_compose import Feature

from ..public_dashboard import PublicDashboardOutbox

_MIRROR_TIMEOUT_ENV = "IRIAI_PUBLIC_DASHBOARD_MIRROR_TIMEOUT_SECONDS"
_DEFAULT_MIRROR_TIMEOUT_SECONDS = 0.75

_ADVISORY_LOCK_TIMEOUT_ENV = "IRIAI_ADVISORY_LOCK_TIMEOUT_SECONDS"
_DEFAULT_ADVISORY_LOCK_TIMEOUT_SECONDS = 90.0
_ADVISORY_LOCK_RETRY_ENV = "IRIAI_ADVISORY_LOCK_RETRY_SECONDS"
_DEFAULT_ADVISORY_LOCK_RETRY_SECONDS = 0.25


class AdvisoryLockTimeout(TimeoutError):
    """Raised when an advisory lock cannot be acquired within the bounded window.

    Failing fast and loud is intentional: the prior blocking acquisition parked the
    caller (e.g. ``resume_workflow``) forever behind a contending/leaked holder."""


class PostgresFeatureStore:
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        public_dashboard: PublicDashboardOutbox | None = None,
    ) -> None:
        self._pool = pool
        self._public_dashboard = public_dashboard

    async def create(self, feature: Feature, phase: str = "pm") -> None:
        metadata_json = json.dumps(feature.metadata)
        await self._pool.execute(
            """
            INSERT INTO features (id, name, slug, workflow_name, workspace_id, phase, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            """,
            feature.id,
            feature.name,
            feature.slug,
            feature.workflow_name,
            feature.workspace_id,
            phase,
            metadata_json,
        )
        await self.log_event(feature.id, "phase_start", "system", phase)

    async def transition_phase(self, feature_id: str, new_phase: str) -> None:
        await self._pool.execute(
            "UPDATE features SET phase = $1, updated_at = NOW() WHERE id = $2",
            new_phase,
            feature_id,
        )
        await self.log_event(feature_id, "phase_transition", "system", new_phase)

    async def log_event(
        self,
        feature_id: str,
        event_type: str,
        source: str,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        metadata_json = json.dumps(metadata or {})
        event_id = await self._pool.fetchval(
            """
            INSERT INTO events (feature_id, event_type, source, content, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id
            """,
            feature_id,
            event_type,
            source,
            content,
            metadata_json,
        )
        if self._public_dashboard is not None:
            await _best_effort_mirror(
                self._public_dashboard.mirror_private_event(
                    source_event_id=event_id,
                    feature_id=feature_id,
                    event_type=event_type,
                    source=source,
                    content=content,
                    metadata=metadata or {},
                )
            )

    async def get_feature(self, feature_id: str) -> Feature | None:
        """Load a single feature by ID, or None if not found."""
        row = await self._pool.fetchrow(
            "SELECT id, name, slug, workflow_name, workspace_id, phase, metadata FROM features WHERE id = $1",
            feature_id,
        )
        if row is None:
            return None
        return _row_to_feature(row)

    async def update_metadata(self, feature_id: str, patch: dict[str, Any]) -> None:
        """Merge keys into the feature's metadata JSONB (non-destructive)."""
        await self._pool.execute(
            "UPDATE features SET metadata = metadata || $1::jsonb, updated_at = NOW() WHERE id = $2",
            json.dumps(patch),
            feature_id,
        )

    async def list_active(self) -> list[Feature]:
        """Return all features whose phase is not terminal."""
        rows = await self._pool.fetch(
            "SELECT id, name, slug, workflow_name, workspace_id, phase, metadata "
            "FROM features WHERE phase NOT IN ('complete', 'failed') "
            "ORDER BY created_at DESC"
        )
        return [_row_to_feature(row) for row in rows]

    async def get_events(self, feature_id: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM events WHERE feature_id = $1 ORDER BY created_at",
            feature_id,
        )
        return [dict(row) for row in rows]

    async def list_event_summaries(
        self,
        feature_id: str,
        *,
        after_id: int = 0,
        limit: int = 50,
        order: str = "asc",
        group_idx: int | None = None,
        preview_chars: int = 700,
    ) -> list[dict[str, Any]]:
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        limit = max(1, min(500, int(limit or 50)))
        preview_chars = max(0, min(4_000, int(preview_chars or 0)))
        args: list[Any] = [feature_id, max(0, int(after_id or 0)), limit, preview_chars]
        group_clause = ""
        if group_idx is not None:
            args.append(str(int(group_idx)))
            group_clause = (
                " AND (metadata->>'group_idx' = $5 OR metadata->>'group' = $5 "
                "OR metadata->>'group_index' = $5 OR metadata->>'dag_group' = $5)"
            )
        rows = await self._pool.fetch(
            f"""
            SELECT id, feature_id, event_type, source,
                   CASE
                       WHEN content IS NULL THEN NULL
                       WHEN $4 = 0 THEN NULL
                       ELSE substring(content from 1 for $4)
                   END AS content,
                   metadata,
                   created_at,
                   COALESCE(pg_column_size(content), 0)::bigint AS content_bytes
            FROM events
            WHERE feature_id = $1 AND id > $2{group_clause}
            ORDER BY id {direction}
            LIMIT $3
            """,
            *args,
        )
        return [dict(row) for row in rows]

    @asynccontextmanager
    async def advisory_lock(self, feature_id: str, name: str):
        """Hold a transaction-scoped advisory lock for the duration of a block.

        Acquisition is NON-BLOCKING (``pg_try_advisory_xact_lock``) on a bounded
        retry loop. The old blocking ``pg_advisory_xact_lock`` parked the coroutine
        on a query future that never resolved when a contending/leaked holder kept
        the lock — deadlocking ``resume_workflow`` forever. Now a still-contended
        lock raises ``AdvisoryLockTimeout`` after a bounded window instead.

        Cleanup ALWAYS releases the connection (in the outer ``finally``), even if
        the body is cancelled mid-transaction: the bounded pool release runs the
        connection reset (``ROLLBACK``), freeing the advisory lock, so a cancelled
        holder can no longer leak an ``idle in transaction`` lock holder."""
        lock_key = _advisory_lock_key(feature_id, name)
        deadline = time.monotonic() + _advisory_lock_timeout_seconds()
        interval = _advisory_lock_retry_seconds()
        conn = await self._pool.acquire()
        try:
            tx = conn.transaction()
            await tx.start()
            try:
                while not await conn.fetchval(
                    "SELECT pg_try_advisory_xact_lock($1)", lock_key
                ):
                    if time.monotonic() >= deadline:
                        raise AdvisoryLockTimeout(
                            f"advisory_lock({feature_id!r}, {name!r}) not acquired "
                            f"within {_advisory_lock_timeout_seconds():.1f}s; "
                            "another holder is contending"
                        )
                    await asyncio.sleep(interval)
                yield conn
            finally:
                # Best-effort rollback, shielded so cancellation can't skip it; the
                # outer finally guarantees release regardless.
                with suppress(Exception):
                    await asyncio.shield(tx.rollback())
        finally:
            await self._pool.release(conn)


def _row_to_feature(row: asyncpg.Record) -> Feature:
    meta = row["metadata"]
    metadata = json.loads(meta) if isinstance(meta, str) else (meta or {})
    # Stash the DB phase into metadata so callers can access it
    if "phase" in row.keys():
        metadata["_db_phase"] = row["phase"]
    return Feature(
        id=row["id"],
        name=row["name"],
        slug=row["slug"],
        workflow_name=row["workflow_name"],
        workspace_id=row["workspace_id"],
        metadata=metadata,
    )


def _advisory_lock_key(feature_id: str, name: str) -> int:
    digest = hashlib.blake2b(
        f"{feature_id}:{name}".encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def _best_effort_mirror(awaitable: Any) -> None:
    try:
        await asyncio.wait_for(awaitable, timeout=_mirror_timeout_seconds())
    except Exception:
        return


def _mirror_timeout_seconds() -> float:
    raw = os.environ.get(_MIRROR_TIMEOUT_ENV, "")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MIRROR_TIMEOUT_SECONDS
    return max(0.05, parsed)


def _advisory_lock_timeout_seconds() -> float:
    raw = os.environ.get(_ADVISORY_LOCK_TIMEOUT_ENV, "")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = _DEFAULT_ADVISORY_LOCK_TIMEOUT_SECONDS
    return max(0.1, parsed)


def _advisory_lock_retry_seconds() -> float:
    raw = os.environ.get(_ADVISORY_LOCK_RETRY_ENV, "")
    try:
        parsed = float(raw)
    except (TypeError, ValueError):
        parsed = _DEFAULT_ADVISORY_LOCK_RETRY_SECONDS
    return max(0.01, parsed)
