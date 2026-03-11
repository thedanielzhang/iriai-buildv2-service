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

    async def get_events(self, feature_id: str) -> list[dict[str, Any]]:
        rows = await self._pool.fetch(
            "SELECT * FROM events WHERE feature_id = $1 ORDER BY created_at",
            feature_id,
        )
        return [dict(row) for row in rows]
