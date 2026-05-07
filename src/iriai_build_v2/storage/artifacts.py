from __future__ import annotations

import hashlib
import json
from typing import Any

import asyncpg
from pydantic import BaseModel

from iriai_compose import ArtifactStore, Feature

from ..public_dashboard import PublicDashboardOutbox


class PostgresArtifactStore(ArtifactStore):
    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        public_dashboard: PublicDashboardOutbox | None = None,
    ) -> None:
        self._pool = pool
        self._public_dashboard = public_dashboard

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

    async def get_record(self, key: str, *, feature: Feature) -> dict[str, Any] | None:
        row = await self._pool.fetchrow(
            "SELECT id, created_at, value FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        if row is None:
            return None
        value = row["value"]
        return {
            "id": row["id"],
            "created_at": row["created_at"],
            "value": value,
            "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
        }

    async def list_records(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...] | list[str] = (),
        after_id: int = 0,
        limit: int = 500,
        order: str = "asc",
    ) -> list[dict[str, Any]]:
        direction = "DESC" if str(order).lower() == "desc" else "ASC"
        args: list[Any] = [feature_id, after_id, limit]
        prefix_clause = ""
        if prefixes:
            prefix_clause = " AND (" + " OR ".join(
                f"key LIKE ${idx + 4}" for idx, _prefix in enumerate(prefixes)
            ) + ")"
            args.extend(f"{prefix}%" for prefix in prefixes)
        rows = await self._pool.fetch(
            f"""
            SELECT id, key, created_at, value
            FROM artifacts
            WHERE feature_id = $1 AND id > $2{prefix_clause}
            ORDER BY id {direction}
            LIMIT $3
            """,
            *args,
        )
        records: list[dict[str, Any]] = []
        for row in rows:
            value = row["value"]
            records.append(
                {
                    "id": row["id"],
                    "key": row["key"],
                    "created_at": row["created_at"],
                    "value": value,
                    "sha256": hashlib.sha256(value.encode("utf-8")).hexdigest(),
                }
            )
        return records

    async def put(self, key: str, value: Any, *, feature: Feature) -> None:
        serialized = self._serialize(value)
        artifact_id = await self._pool.fetchval(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1, $2, $3) RETURNING id",
            feature.id,
            key,
            serialized,
        )
        if self._public_dashboard is not None:
            await self._public_dashboard.mirror_artifact_write(
                source_artifact_id=artifact_id,
                feature=feature,
                key=key,
                value=serialized,
                visibility="internal",
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
