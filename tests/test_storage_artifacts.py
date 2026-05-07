from __future__ import annotations

import hashlib
from datetime import datetime, timezone

import pytest

from iriai_build_v2.storage.artifacts import PostgresArtifactStore


class _ListRecordsPool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        return [
            {
                "id": 12,
                "key": "dag-verify:g38:retry-0",
                "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
                "value": '{"status":"failed"}',
            }
        ]


@pytest.mark.asyncio
async def test_artifact_store_list_records_streams_repeated_keys_by_row_id() -> None:
    pool = _ListRecordsPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    records = await store.list_records(
        feature_id="8ac124d6",
        prefixes=("dag-verify:", "dag-task-reconcile:"),
        after_id=10,
        limit=50,
    )

    sql, args = pool.fetches[0]
    assert "id > $2" in sql
    assert "key LIKE $4" in sql
    assert "key LIKE $5" in sql
    assert args == (
        "8ac124d6",
        10,
        50,
        "dag-verify:%",
        "dag-task-reconcile:%",
    )
    assert records == [
        {
            "id": 12,
            "key": "dag-verify:g38:retry-0",
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "value": '{"status":"failed"}',
            "sha256": hashlib.sha256(b'{"status":"failed"}').hexdigest(),
        }
    ]


@pytest.mark.asyncio
async def test_artifact_store_list_records_supports_descending_latest_window() -> None:
    pool = _ListRecordsPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    await store.list_records(
        feature_id="8ac124d6",
        prefixes=("dag-verify:",),
        after_id=0,
        limit=10,
        order="desc",
    )

    sql, args = pool.fetches[0]
    assert "ORDER BY id DESC" in sql
    assert args == ("8ac124d6", 0, 10, "dag-verify:%")
