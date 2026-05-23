from __future__ import annotations

import pytest

from iriai_build_v2.storage.features import PostgresFeatureStore


class _RecordingPool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        return []


@pytest.mark.asyncio
async def test_feature_store_list_event_summaries_uses_bounded_projection_query():
    pool = _RecordingPool()
    store = PostgresFeatureStore(pool)  # type: ignore[arg-type]

    rows = await store.list_event_summaries(
        "feat-1",
        after_id=0,
        limit=999,
        order="desc",
        group_idx=44,
        preview_chars=9_000,
    )

    assert rows == []
    sql, args = pool.fetches[0]
    assert "SELECT *" not in sql
    assert "substring(content from 1 for $4)" in sql
    assert "ORDER BY id DESC" in sql
    assert "LIMIT $3" in sql
    assert args == ("feat-1", 0, 500, 4000, "44")
