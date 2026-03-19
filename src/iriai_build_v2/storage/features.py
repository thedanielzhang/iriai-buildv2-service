from __future__ import annotations

import json
from typing import Any

import asyncpg

from iriai_compose import Feature


class PostgresFeatureStore:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

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
        await self._pool.execute(
            """
            INSERT INTO events (feature_id, event_type, source, content, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            """,
            feature_id,
            event_type,
            source,
            content,
            metadata_json,
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
