from __future__ import annotations

import json
from typing import Any

import asyncpg
from pydantic import BaseModel

from iriai_compose import ArtifactStore, Feature


class PostgresArtifactStore(ArtifactStore):
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get(self, key: str, *, feature: Feature) -> Any | None:
        row = await self._pool.fetchrow(
            "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
            "ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        if row is None:
            return None
        return row["value"]

    async def put(self, key: str, value: Any, *, feature: Feature) -> None:
        serialized = self._serialize(value)
        await self._pool.execute(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1, $2, $3)",
            feature.id,
            key,
            serialized,
        )

    async def delete(self, key: str, *, feature: Feature) -> None:
        await self._pool.execute(
            "DELETE FROM artifacts WHERE feature_id = $1 AND key = $2",
            feature.id,
            key,
        )

    @staticmethod
    def _serialize(value: Any) -> str:
        if isinstance(value, BaseModel):
            return value.model_dump_json()
        if isinstance(value, str):
            return value
        return json.dumps(value)
