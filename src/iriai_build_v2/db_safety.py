from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import asyncpg


@dataclass(frozen=True)
class ConcurrentIndexSpec:
    name: str
    table: str
    columns_sql: str

    @property
    def create_sql(self) -> str:
        return (
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {self.name} "
            f"ON {self.table} {self.columns_sql}"
        )

    @property
    def drop_sql(self) -> str:
        return f"DROP INDEX CONCURRENTLY IF EXISTS {self.name}"


@dataclass(frozen=True)
class DBSafetyThresholds:
    postgres_rss_mb: int = 3_072
    bridge_rss_mb: int = 2_048
    dashboard_rss_mb: int = 1_024
    supervisor_rss_mb: int = 1_024
    db_growth_mb_per_hour: int = 512
    artifact_growth_mb_per_hour: int = 512
    outbox_pending_mb: int = 512
    temp_file_growth_mb_per_hour: int = 1_024
    active_connections: int = 30


DEFAULT_DB_SAFETY_THRESHOLDS = DBSafetyThresholds()


SAFETY_INDEXES = (
    ConcurrentIndexSpec(
        name="idx_artifacts_feature_id",
        table="artifacts",
        columns_sql="(feature_id, id)",
    ),
    ConcurrentIndexSpec(
        name="idx_artifacts_feature_created_desc",
        table="artifacts",
        columns_sql="(feature_id, created_at DESC)",
    ),
    ConcurrentIndexSpec(
        name="idx_events_feature_id_desc",
        table="events",
        columns_sql="(feature_id, id DESC)",
    ),
)


_SPILL_LOGICAL_BYTES_SQL = (
    "CASE WHEN value LIKE '{\"__iriai_spill_v1__\"%' THEN "
    "COALESCE(NULLIF(substring(value from '\"bytes\"\\s*:\\s*([0-9]+)'), '')::bigint, "
    "pg_column_size(value)::bigint) "
    "ELSE pg_column_size(value)::bigint END"
)


async def install_safety_indexes(
    dsn: str,
    *,
    lock_timeout_ms: int = 2_000,
    statement_timeout_ms: int = 120_000,
    specs: Iterable[ConcurrentIndexSpec] = SAFETY_INDEXES,
) -> list[str]:
    """Install workflow safety indexes without blocking active execution.

    This intentionally runs outside ``ensure_schema`` because
    ``CREATE INDEX CONCURRENTLY`` cannot run inside a normal migration
    transaction and should be invoked only after an operator checks disk and
    bridge activity.
    """

    conn = await asyncpg.connect(dsn)
    installed: list[str] = []
    try:
        await conn.execute(f"SET lock_timeout = '{int(lock_timeout_ms)}ms'")
        await conn.execute(f"SET statement_timeout = '{int(statement_timeout_ms)}ms'")
        for spec in specs:
            invalid = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM pg_class c
                    JOIN pg_index i ON i.indexrelid = c.oid
                    WHERE c.relname = $1
                      AND NOT i.indisvalid
                )
                """,
                spec.name,
            )
            if invalid:
                await conn.execute(spec.drop_sql)
            await conn.execute(spec.create_sql)
            installed.append(spec.name)
    finally:
        await conn.close()
    return installed


async def rollback_safety_indexes(
    dsn: str,
    *,
    specs: Iterable[ConcurrentIndexSpec] = SAFETY_INDEXES,
) -> list[str]:
    conn = await asyncpg.connect(dsn)
    dropped: list[str] = []
    try:
        for spec in reversed(tuple(specs)):
            await conn.execute(spec.drop_sql)
            dropped.append(spec.name)
    finally:
        await conn.close()
    return dropped


async def capture_db_safety_snapshot(dsn: str, *, feature_id: str | None = None) -> dict[str, object]:
    """Capture the read-only Phase 0A DB baseline used before regrouping."""

    conn = await asyncpg.connect(dsn)
    try:
        tables = await conn.fetch(
            """
            SELECT relname,
                   pg_total_relation_size(relid)::bigint AS total_bytes,
                   pg_relation_size(relid)::bigint AS table_bytes,
                   n_live_tup::bigint AS live_rows
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
            """
        )
        artifact_keys = await conn.fetch(
            f"""
            SELECT feature_id,
                   split_part(key, ':', 1) AS key_family,
                   count(*)::bigint AS rows,
                   COALESCE(sum(pg_column_size(value)), 0)::bigint AS db_value_bytes,
                   COALESCE(sum({_SPILL_LOGICAL_BYTES_SQL}), 0)::bigint AS value_bytes,
                   COALESCE(sum({_SPILL_LOGICAL_BYTES_SQL}), 0)::bigint AS logical_value_bytes,
                   COALESCE(max(pg_column_size(value)), 0)::bigint AS max_db_value_bytes,
                   COALESCE(max({_SPILL_LOGICAL_BYTES_SQL}), 0)::bigint AS max_value_bytes
            FROM artifacts
            WHERE $1::text IS NULL OR feature_id = $1
            GROUP BY feature_id, key_family
            ORDER BY value_bytes DESC
            LIMIT 100
            """,
            feature_id,
        )
        outbox = await conn.fetchrow(
            """
            SELECT count(*)::bigint AS pending_rows,
                   COALESCE(sum(pg_column_size(payload)), 0)::bigint AS pending_payload_bytes,
                   min(created_at) AS oldest_pending_at,
                   max(created_at) AS newest_pending_at
            FROM public_dashboard_outbox
            WHERE status = 'pending'
              AND ($1::text IS NULL OR feature_id = $1)
            """,
            feature_id,
        )
        temp = await conn.fetchrow(
            """
            SELECT temp_files::bigint AS temp_files,
                   temp_bytes::bigint AS temp_bytes,
                   numbackends::bigint AS active_connections
            FROM pg_stat_database
            WHERE datname = current_database()
            """
        )
        return {
            "feature_id": feature_id,
            "thresholds": DEFAULT_DB_SAFETY_THRESHOLDS.__dict__,
            "tables": [dict(row) for row in tables],
            "artifact_key_families": [dict(row) for row in artifact_keys],
            "outbox": dict(outbox) if outbox is not None else {},
            "database": dict(temp) if temp is not None else {},
        }
    finally:
        await conn.close()
