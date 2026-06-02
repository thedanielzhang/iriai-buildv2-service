"""Read-only checkpoint discovery for a sealed DAG group.

`get_latest_sealed_checkpoint` finds the newest SEALED group for a feature and
its per-repo git ``result_commit``s, using ONLY read-only SELECTs against a
dedicated pool — it never acquires the feature advisory lock and never borrows
the orchestrator's shared pool, so it cannot contend with the running merge
queue.

The authoritative "sealed" signal is durable on the rows: a group is sealed iff
its lanes reached ``status='done'`` via ``complete_checkpoint``, which stamps a
non-null ``checkpoint_projection_id``. We do NOT recompute ``coverage.approved``
standalone — that depends on the live in-memory DAG's ``expected_task_ids``,
which cannot be reconstructed from the DB and would yield a wrong verdict.
"""

from __future__ import annotations

import os
from pathlib import PurePath
from typing import Any, Protocol

import asyncpg
from pydantic import BaseModel, Field

DEFAULT_DSN = os.environ.get(
    "IRIAI_E2E_DSN",
    "postgresql://danielzhang@localhost:5431/iriai_build_v2",
)


class _Fetcher(Protocol):
    async def fetch(self, query: str, *args: Any) -> list[Any]: ...


class RepoCheckpoint(BaseModel):
    repo_id: str
    repo_path: str = ""
    result_commit: str
    queue_item_id: str = ""

    @property
    def repo_key(self) -> str:
        """Human-readable key (repo dir basename), falling back to repo_id."""
        if self.repo_path:
            return PurePath(self.repo_path).name
        return self.repo_id


class SealedCheckpoint(BaseModel):
    feature_id: str
    group_idx: int
    dag_sha256: str = ""
    repos: list[RepoCheckpoint] = Field(default_factory=list)
    done_queue_item_ids: list[str] = Field(default_factory=list)

    def result_commits(self) -> dict[str, str]:
        """repo dir basename -> git result_commit (one per repo)."""
        return {r.repo_key: r.result_commit for r in self.repos}


_SEALED_GROUP_SQL = """
    SELECT max(group_idx) AS g
    FROM merge_queue_items
    WHERE feature_id = $1
      AND status = 'done'
      AND checkpoint_projection_id IS NOT NULL
      AND ($2::int IS NULL OR group_idx <= $2)
"""

_LANES_SQL = """
    SELECT i.id AS queue_item_id, i.dag_sha256, i.group_idx,
           i.repo_id, i.result_commit, t.repo_path
    FROM merge_queue_items i
    LEFT JOIN LATERAL (
        SELECT repo_path FROM merge_queue_repo_targets t
        WHERE t.queue_item_id = i.id AND t.repo_id = i.repo_id
        LIMIT 1
    ) t ON true
    WHERE i.feature_id = $1
      AND i.group_idx = $2
      AND i.status = 'done'
      AND i.checkpoint_projection_id IS NOT NULL
      AND i.result_commit <> ''
    ORDER BY i.repo_id, i.id
"""


async def get_latest_sealed_checkpoint(
    conn: _Fetcher,
    feature_id: str,
    *,
    max_group_idx: int | None = None,
) -> SealedCheckpoint | None:
    """Newest sealed checkpoint at or below ``max_group_idx`` (None = no cap).

    Returns ``None`` if the feature has no sealed group. Selects, per repo, the
    NEWEST ``done`` lane (highest queue-item id) — a repo can have multiple done
    lanes (one per task) within a group.
    """
    rows = await conn.fetch(_SEALED_GROUP_SQL, feature_id, max_group_idx)
    if not rows or rows[0]["g"] is None:
        return None
    group_idx = int(rows[0]["g"])

    lanes = await conn.fetch(_LANES_SQL, feature_id, group_idx)
    if not lanes:
        return None

    # Pick the newest done lane per repo_id (rows are ordered by repo_id, id asc).
    newest: dict[str, dict[str, Any]] = {}
    dag_sha256 = ""
    done_ids: list[str] = []
    for row in lanes:
        repo_id = str(row["repo_id"])
        done_ids.append(str(row["queue_item_id"]))
        dag_sha256 = dag_sha256 or (row["dag_sha256"] or "")
        newest[repo_id] = dict(row)  # later rows (higher id) overwrite

    repos = [
        RepoCheckpoint(
            repo_id=str(r["repo_id"]),
            repo_path=r.get("repo_path") or "",
            result_commit=r["result_commit"],
            queue_item_id=str(r["queue_item_id"]),
        )
        for r in newest.values()
    ]
    repos.sort(key=lambda r: r.repo_key)
    return SealedCheckpoint(
        feature_id=feature_id,
        group_idx=group_idx,
        dag_sha256=dag_sha256,
        repos=repos,
        done_queue_item_ids=done_ids,
    )


def open_readonly_pool(
    dsn: str = DEFAULT_DSN,
    *,
    max_size: int = 2,
    command_timeout: float = 15.0,
) -> Any:
    """Create a small DEDICATED asyncpg pool with a statement timeout.

    Never the orchestrator's shared pool. Returns the pool coroutine factory
    result (await it). Caller is responsible for ``await pool.close()``.
    """
    return asyncpg.create_pool(
        dsn,
        min_size=1,
        max_size=max_size,
        command_timeout=command_timeout,
    )


async def fetch_latest_sealed_checkpoint(
    feature_id: str,
    *,
    dsn: str = DEFAULT_DSN,
    max_group_idx: int | None = None,
) -> SealedCheckpoint | None:
    """Convenience: open a dedicated read-only pool, query, close.

    Runs inside a ``REPEATABLE READ, READ ONLY`` transaction for a consistent,
    side-effect-free snapshot.
    """
    pool = await open_readonly_pool(dsn)
    try:
        async with pool.acquire() as conn:
            tr = conn.transaction(isolation="repeatable_read", readonly=True)
            await tr.start()
            try:
                return await get_latest_sealed_checkpoint(
                    conn, feature_id, max_group_idx=max_group_idx
                )
            finally:
                await tr.rollback()
    finally:
        await pool.close()
