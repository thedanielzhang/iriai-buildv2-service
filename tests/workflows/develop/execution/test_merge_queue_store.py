"""Slice 08c — durable merge queue persistence.

These tests run against a real Postgres database (the `mq_conn` / `mq_dsn`
fixtures in this directory's conftest). They skip when no Postgres is reachable.

Covers the real-Postgres fixture and schema constraints (08c-1), transactional
enqueue (08c-2), the claim/heartbeat lease layer (08c-3), and recover_expired /
the lease-fenced transition (08c-4). Queue evidence and the checkpoint
projection land in a later 08c iteration.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest

from iriai_build_v2.execution_control import IdempotencyConflict
from iriai_build_v2.execution_control.merge_queue_store import (
    LeaseFencedError,
    MergeProof,
    MergeQueueError,
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoCommitProof,
    RepoTargetCreate,
    TaskCoverageCreate,
)


async def _insert_feature(conn: asyncpg.Connection, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id,
        feature_id,
        feature_id,
        "develop",
        "ws-1",
    )


_INSERT_ITEM = (
    "INSERT INTO merge_queue_items "
    "(feature_id, dag_sha256, group_idx, base_commit, status, "
    " request_digest, idempotency_key) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7)"
)


@pytest.mark.asyncio
async def test_fixture_provides_loaded_merge_queue_schema(mq_conn) -> None:
    tables = await mq_conn.fetch(
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
        "AND tablename LIKE 'merge_queue%' ORDER BY tablename"
    )
    assert [t["tablename"] for t in tables] == [
        "merge_queue_items",
        "merge_queue_repo_targets",
        "merge_queue_task_coverage",
    ]


@pytest.mark.asyncio
async def test_merge_queue_item_round_trips(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-smoke")
    row_id = await mq_conn.fetchval(
        _INSERT_ITEM + " RETURNING id",
        "feat-smoke",
        "dag-sha",
        1,
        "base-commit",
        "failed",
        "req-digest",
        "merge:feat-smoke:dag-sha:g1",
    )
    assert row_id > 0
    row = await mq_conn.fetchrow(
        "SELECT feature_id, status, lease_version, priority "
        "FROM merge_queue_items WHERE id = $1",
        row_id,
    )
    assert row["feature_id"] == "feat-smoke"
    assert row["status"] == "failed"
    assert row["lease_version"] == 0
    assert row["priority"] == 100


@pytest.mark.asyncio
async def test_status_check_rejects_unknown_status(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-bad")
    with pytest.raises(asyncpg.PostgresError):
        await mq_conn.execute(
            _INSERT_ITEM,
            "feat-bad",
            "dag",
            1,
            "base",
            "not-a-real-status",
            "rd",
            "merge:feat-bad:g1",
        )


@pytest.mark.asyncio
async def test_done_status_requires_proof_ids(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-done")
    # A 'done' row with no gate/proof/checkpoint ids violates the progression
    # CHECK constraints — the schema must reject it.
    with pytest.raises(asyncpg.PostgresError):
        await mq_conn.execute(
            _INSERT_ITEM,
            "feat-done",
            "dag",
            1,
            "base",
            "done",
            "rd",
            "merge:feat-done:g1",
        )


@pytest.mark.asyncio
async def test_idempotency_key_is_unique(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-idem")
    args = (
        "feat-idem",
        "dag",
        1,
        "base",
        "failed",
        "rd",
        "merge:feat-idem:g1",
    )
    await mq_conn.execute(_INSERT_ITEM, *args)
    with pytest.raises(asyncpg.UniqueViolationError):
        await mq_conn.execute(_INSERT_ITEM, *args)


@pytest.mark.asyncio
async def test_truncation_isolates_each_test(mq_conn) -> None:
    # The mq_dsn fixture truncates before every test, so each starts empty.
    assert await mq_conn.fetchval("SELECT count(*) FROM merge_queue_items") == 0
    assert await mq_conn.fetchval("SELECT count(*) FROM features") == 0


@pytest.mark.asyncio
async def test_separate_connections_share_committed_state(mq_dsn: str) -> None:
    # Concurrency tests need real committed visibility across connections.
    writer = await asyncpg.connect(mq_dsn)
    reader = await asyncpg.connect(mq_dsn)
    try:
        await _insert_feature(writer, "feat-shared")
        await writer.execute(
            _INSERT_ITEM,
            "feat-shared",
            "dag",
            1,
            "base",
            "failed",
            "rd",
            "merge:feat-shared:g1",
        )
        seen = await reader.fetchval(
            "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
            "feat-shared",
        )
        assert seen == 1
    finally:
        await writer.close()
        await reader.close()


# ── enqueue (08c-2) ─────────────────────────────────────────────────────────


async def _insert_contract(
    conn: asyncpg.Connection,
    feature_id: str,
    task_id: str,
    *,
    dag: str = "dag-sha",
    group: int = 1,
    status: str = "active",
) -> int:
    return await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id",
        feature_id,
        f"contract:{feature_id}:{group}:{task_id}",
        dag,
        group,
        task_id,
        f"cd-{task_id}",
        status,
    )


async def _insert_evidence(
    conn: asyncpg.Connection, feature_id: str, kind: str = "gate_request"
) -> int:
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash) "
        "VALUES ($1,$2,$3,$4) RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
        kind,
        "content-hash",
    )


async def _setup_create(
    conn: asyncpg.Connection,
    *,
    feature_id: str = "feat-q",
    task_ids: tuple[str, ...] = ("T1",),
    head_commit: str = "head-1",
    lane: str = "group",
    contract_status: str = "active",
) -> MergeQueueItemCreate:
    gate = await _insert_evidence(conn, feature_id)
    coverage: list[TaskCoverageCreate] = []
    contract_ids: list[int] = []
    for task_id in task_ids:
        contract_id = await _insert_contract(
            conn, feature_id, task_id, status=contract_status
        )
        coverage.append(
            TaskCoverageCreate(task_id=task_id, contract_id=contract_id)
        )
        contract_ids.append(contract_id)
    return MergeQueueItemCreate(
        feature_id=feature_id,
        dag_sha256="dag-sha",
        group_idx=1,
        base_commit="base-1",
        head_commit=head_commit,
        integration_lane=lane,
        pre_queue_gate_evidence_id=gate,
        contract_ids=contract_ids,
        patch_evidence_ids=[1, 2],
        gate_evidence_ids=[gate],
        task_coverage=coverage,
        repo_targets=[
            RepoTargetCreate(
                repo_id="repo-a", repo_path="/repos/a", base_commit="base-1"
            )
        ],
    )


@pytest.mark.asyncio
async def test_enqueue_creates_item_with_coverage_and_repo_targets(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1", "T2"))
    store = MergeQueueStore(mq_conn)

    item = await store.enqueue(create)

    assert item.id > 0
    assert item.status == "queued"
    assert item.lease_version == 0
    assert {c.task_id for c in item.task_coverage} == {"T1", "T2"}
    assert [t.repo_id for t in item.repo_targets] == ["repo-a"]
    assert item.repo_targets[0].status == "pending"
    assert item.request_digest
    assert item.idempotency_key.startswith("merge:feat-q:")


@pytest.mark.asyncio
async def test_enqueue_is_idempotent_for_an_identical_request(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn)
    store = MergeQueueStore(mq_conn)

    first = await store.enqueue(create)
    second = await store.enqueue(create)

    assert first.id == second.id
    count = await mq_conn.fetchval("SELECT count(*) FROM merge_queue_items")
    assert count == 1


@pytest.mark.asyncio
async def test_enqueue_conflict_on_same_key_different_digest(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn)
    store = MergeQueueStore(mq_conn)
    await store.enqueue(create)

    # Same idempotency-key inputs (feature/dag/group/lane/tasks/repo/base/head)
    # but a different request digest via a changed payload.
    conflicting = create.model_copy(update={"payload": {"changed": True}})
    with pytest.raises(IdempotencyConflict):
        await store.enqueue(conflicting)


@pytest.mark.asyncio
async def test_enqueue_requires_pre_queue_gate_evidence(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn)
    create = create.model_copy(update={"pre_queue_gate_evidence_id": None})
    with pytest.raises(MergeQueueError, match="pre_queue_gate"):
        await MergeQueueStore(mq_conn).enqueue(create)


@pytest.mark.asyncio
async def test_enqueue_requires_task_coverage(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn)
    create = create.model_copy(update={"task_coverage": []})
    with pytest.raises(MergeQueueError, match="task coverage"):
        await MergeQueueStore(mq_conn).enqueue(create)


@pytest.mark.asyncio
async def test_enqueue_rejects_inactive_contract(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, contract_status="superseded")
    with pytest.raises(MergeQueueError, match="not active"):
        await MergeQueueStore(mq_conn).enqueue(create)


@pytest.mark.asyncio
async def test_enqueue_rejects_contract_scope_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    gate = await _insert_evidence(mq_conn, "feat-q")
    # A contract for task OTHER referenced under coverage for task T1.
    other_contract = await _insert_contract(mq_conn, "feat-q", "OTHER")
    create = MergeQueueItemCreate(
        feature_id="feat-q",
        dag_sha256="dag-sha",
        group_idx=1,
        base_commit="base-1",
        pre_queue_gate_evidence_id=gate,
        contract_ids=[other_contract],
        patch_evidence_ids=[1],
        gate_evidence_ids=[gate],
        task_coverage=[
            TaskCoverageCreate(task_id="T1", contract_id=other_contract)
        ],
    )
    with pytest.raises(MergeQueueError, match="scope does not match"):
        await MergeQueueStore(mq_conn).enqueue(create)


@pytest.mark.asyncio
async def test_enqueue_rejects_a_competing_live_lane(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1",))
    store = MergeQueueStore(mq_conn)
    await store.enqueue(create)

    # A second lane covering T1 (fresh head/lane, reusing the same contract).
    competing = create.model_copy(
        update={"head_commit": "head-2", "integration_lane": "repo:other"}
    )
    with pytest.raises(MergeQueueError, match="already covered"):
        await store.enqueue(competing)


@pytest.mark.asyncio
async def test_enqueue_allows_retry_replacement_of_a_failed_source(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1",))
    store = MergeQueueStore(mq_conn)
    source = await store.enqueue(create)

    # The source lane reaches terminal failed.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'failed' WHERE id = $1",
        source.id,
    )

    # A retry replacement carries a fresh patch (different head) and the real
    # retry_of_queue_item_id column.
    retry = create.model_copy(
        update={
            "head_commit": "head-retry",
            "retry_of_queue_item_id": source.id,
        }
    )
    replacement = await store.enqueue(retry)
    assert replacement.id != source.id
    assert replacement.retry_of_queue_item_id == source.id
    assert {c.task_id for c in replacement.task_coverage} == {"T1"}


@pytest.mark.asyncio
async def test_enqueue_retry_rejects_a_non_failed_source(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1",))
    store = MergeQueueStore(mq_conn)
    source = await store.enqueue(create)  # still 'queued'

    retry = create.model_copy(
        update={
            "head_commit": "head-retry",
            "retry_of_queue_item_id": source.id,
        }
    )
    with pytest.raises(MergeQueueError, match="not terminal failed"):
        await store.enqueue(retry)


@pytest.mark.asyncio
async def test_enqueue_rejects_duplicate_task_coverage(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1",))
    dup = create.task_coverage[0]
    create = create.model_copy(update={"task_coverage": [dup, dup]})
    with pytest.raises(MergeQueueError, match="duplicate task coverage"):
        await MergeQueueStore(mq_conn).enqueue(create)


@pytest.mark.asyncio
async def test_enqueue_rejects_duplicate_repo_target(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn)
    create = create.model_copy(
        update={
            "repo_targets": [
                RepoTargetCreate(
                    repo_id="repo-a", repo_path="/repos/a", base_commit="b"
                ),
                RepoTargetCreate(
                    repo_id="repo-a", repo_path="/repos/a2", base_commit="b"
                ),
            ]
        }
    )
    with pytest.raises(MergeQueueError, match="duplicate repo target"):
        await MergeQueueStore(mq_conn).enqueue(create)


@pytest.mark.asyncio
async def test_enqueue_retry_rejects_identical_head(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1",), head_commit="head-1")
    store = MergeQueueStore(mq_conn)
    source = await store.enqueue(create)
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'failed' WHERE id = $1",
        source.id,
    )
    # A retry that reuses the source's head_commit is not a fresh patch. The
    # lane differs so the idempotency keys differ and the retry-source
    # validation (not the idempotency check) is what rejects it.
    retry = create.model_copy(
        update={
            "retry_of_queue_item_id": source.id,
            "integration_lane": "repo:retry-lane",
        }
    )
    with pytest.raises(MergeQueueError, match="fresh patch"):
        await store.enqueue(retry)


@pytest.mark.asyncio
async def test_enqueue_retry_rejects_coverage_mismatch(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    create = await _setup_create(mq_conn, task_ids=("T1",))
    store = MergeQueueStore(mq_conn)
    source = await store.enqueue(create)
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'failed' WHERE id = $1",
        source.id,
    )
    other_contract = await _insert_contract(mq_conn, "feat-q", "T2")
    retry = create.model_copy(
        update={
            "head_commit": "head-retry",
            "retry_of_queue_item_id": source.id,
            "task_coverage": [
                TaskCoverageCreate(task_id="T2", contract_id=other_contract)
            ],
            "contract_ids": [other_contract],
        }
    )
    with pytest.raises(MergeQueueError, match="does not match"):
        await store.enqueue(retry)


@pytest.mark.asyncio
async def test_enqueue_concurrent_duplicate_resolves_idempotently(
    mq_dsn: str,
) -> None:
    setup = await asyncpg.connect(mq_dsn)
    try:
        await _insert_feature(setup, "feat-q")
        create = await _setup_create(setup, task_ids=("T1",))
    finally:
        await setup.close()

    conn_a = await asyncpg.connect(mq_dsn)
    conn_b = await asyncpg.connect(mq_dsn)
    try:
        # Two workers enqueue the identical request at once. One inserts; the
        # other must resolve idempotently to the same row, not fail.
        first, second = await asyncio.gather(
            MergeQueueStore(conn_a).enqueue(create),
            MergeQueueStore(conn_b).enqueue(create),
        )
        assert first.id == second.id
        count = await conn_a.fetchval("SELECT count(*) FROM merge_queue_items")
        assert count == 1
    finally:
        await conn_a.close()
        await conn_b.close()


# ── claim / heartbeat (08c-3) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_returns_a_queued_lane(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    enqueued = await store.enqueue(await _setup_create(mq_conn))

    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    assert claimed.id == enqueued.id
    assert claimed.status == "leased"
    assert claimed.lease_owner == "worker-1"
    assert claimed.lease_version == 1
    assert claimed.leased_until is not None


@pytest.mark.asyncio
async def test_claim_returns_none_when_nothing_claimable(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    assert await MergeQueueStore(mq_conn).claim("feat-q", "worker-1") is None


@pytest.mark.asyncio
async def test_concurrent_claim_yields_exactly_one_winner(mq_dsn: str) -> None:
    setup = await asyncpg.connect(mq_dsn)
    try:
        await _insert_feature(setup, "feat-q")
        await MergeQueueStore(setup).enqueue(
            await _setup_create(setup, task_ids=("T1",))
        )
    finally:
        await setup.close()

    conn_a = await asyncpg.connect(mq_dsn)
    conn_b = await asyncpg.connect(mq_dsn)
    try:
        a, b = await asyncio.gather(
            MergeQueueStore(conn_a).claim("feat-q", "worker-a"),
            MergeQueueStore(conn_b).claim("feat-q", "worker-b"),
        )
        assert len([r for r in (a, b) if r is not None]) == 1
    finally:
        await conn_a.close()
        await conn_b.close()


@pytest.mark.asyncio
async def test_non_expired_lease_is_not_claimable(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    first = await store.claim("feat-q", "worker-1")
    assert first is not None
    # The lease is fresh (not expired) -> a second claim finds nothing.
    assert await store.claim("feat-q", "worker-2") is None


@pytest.mark.asyncio
async def test_expired_lease_is_reclaimable_and_bumps_version(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    first = await store.claim("feat-q", "worker-1")
    assert first is not None and first.lease_version == 1

    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour' "
        "WHERE id = $1",
        first.id,
    )
    reclaimed = await store.claim("feat-q", "worker-2")
    assert reclaimed is not None
    assert reclaimed.id == first.id
    assert reclaimed.lease_owner == "worker-2"
    assert reclaimed.lease_version == 2


@pytest.mark.asyncio
async def test_heartbeat_extends_lease_without_bumping_version(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    before = claimed.leased_until
    assert before is not None

    beat = await store.heartbeat(
        claimed.id, "worker-1", claimed.lease_version, ttl_seconds=600
    )
    assert beat.lease_version == claimed.lease_version
    assert beat.leased_until is not None
    assert beat.leased_until > before


@pytest.mark.asyncio
async def test_heartbeat_with_stale_lease_version_is_fenced(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    first = await store.claim("feat-q", "worker-1")  # lease_version 1
    assert first is not None

    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour' "
        "WHERE id = $1",
        first.id,
    )
    await store.claim("feat-q", "worker-2")  # lease_version 2

    with pytest.raises(LeaseFencedError):
        await store.heartbeat(first.id, "worker-1", 1)


@pytest.mark.asyncio
async def test_heartbeat_on_terminal_row_is_fenced(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'failed' WHERE id = $1",
        claimed.id,
    )
    with pytest.raises(LeaseFencedError):
        await store.heartbeat(claimed.id, "worker-1", claimed.lease_version)


@pytest.mark.asyncio
async def test_claim_orders_by_priority(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    low = await _setup_create(mq_conn, task_ids=("T1",), head_commit="h1")
    high = await _setup_create(mq_conn, task_ids=("T2",), head_commit="h2")
    high = high.model_copy(update={"priority": 10})
    await store.enqueue(low)  # priority 100 (default)
    high_item = await store.enqueue(high)  # priority 10

    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    assert claimed.id == high_item.id  # lower priority number first


@pytest.mark.asyncio
async def test_claim_does_not_steal_an_in_flight_lane(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    # An `applying` lane with a long-expired lease must still never be taken by
    # a normal claim — only recover_expired may take an in-flight lane.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'applying', "
        "leased_until = now() - interval '1 hour' WHERE id = $1",
        item.id,
    )
    assert await store.claim("feat-q", "worker-1") is None


# ── recover_expired / transition (08c-4) ────────────────────────────────────


async def _claimed_in_flight(mq_conn, store: MergeQueueStore, status: str):
    """Enqueue, claim, and force the row into an expired in-flight *status*,
    satisfying the schema proof-progression CHECK constraints for that status."""
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    sets = ["status = $2", "leased_until = now() - interval '1 hour'"]
    params: list = [claimed.id, status]
    if status in ("verifying", "committing", "checkpointing"):
        params.append(await _insert_evidence(mq_conn, "feat-q"))
        sets.append(f"merge_proof_evidence_id = ${len(params)}")
    if status in ("committing", "checkpointing"):
        params.append(await _insert_evidence(mq_conn, "feat-q"))
        sets.append(f"post_apply_gate_evidence_id = ${len(params)}")
    if status == "checkpointing":
        params.append(await _insert_evidence(mq_conn, "feat-q"))
        sets.append(f"commit_proof_evidence_id = ${len(params)}")
        sets.append("checkpoint_coverage_digest = 'cov-digest'")
        sets.append("checkpoint_body_sha256 = 'body-sha'")
    await mq_conn.execute(
        f"UPDATE merge_queue_items SET {', '.join(sets)} WHERE id = $1",
        *params,
    )
    return claimed


@pytest.mark.parametrize(
    "in_flight_status", ["applying", "verifying", "committing", "checkpointing"]
)
@pytest.mark.asyncio
async def test_recover_expired_takes_over_an_in_flight_lane(
    mq_conn, in_flight_status: str
) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    claimed = await _claimed_in_flight(mq_conn, store, in_flight_status)

    recovered = await store.recover_expired("feat-q", "recovery-worker")
    assert recovered is not None
    assert recovered.id == claimed.id
    assert recovered.lease_owner == "recovery-worker"
    assert recovered.lease_version == claimed.lease_version + 1
    assert recovered.status == in_flight_status
    assert recovered.payload["recovery_count"] == 1
    assert len(recovered.payload["recovery_history"]) == 1


@pytest.mark.asyncio
async def test_recover_expired_returns_none_when_nothing_expired(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    # A queued row (no in-flight expired row) -> nothing to recover.
    assert await store.recover_expired("feat-q", "recovery-worker") is None


@pytest.mark.asyncio
async def test_recover_expired_ignores_a_fresh_in_flight_lane(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    # applying with a still-valid lease -> not recoverable.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'applying' WHERE id = $1",
        item.id,
    )
    assert await store.recover_expired("feat-q", "recovery-worker") is None


@pytest.mark.asyncio
async def test_recover_expired_does_not_take_an_expired_leased_row(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    # An expired *leased* row is claim()'s job, not recover_expired()'s.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour' "
        "WHERE id = $1",
        claimed.id,
    )
    assert await store.recover_expired("feat-q", "recovery-worker") is None


@pytest.mark.asyncio
async def test_recover_expired_poisons_after_max_recoveries(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    claimed = await _claimed_in_flight(mq_conn, store, "applying")
    await mq_conn.execute(
        "UPDATE merge_queue_items SET payload = '{\"recovery_count\": 3}'::jsonb "
        "WHERE id = $1",
        claimed.id,
    )
    recovered = await store.recover_expired("feat-q", "recovery-worker")
    assert recovered is not None
    assert recovered.status == "poisoned"
    assert recovered.payload["poison_reason"]
    assert recovered.payload["recovery_count"] == 4
    assert len(recovered.payload["recovery_history"]) >= 1


@pytest.mark.asyncio
async def test_transition_advances_leased_to_applying(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None

    moved = await store.transition(
        claimed.id, "worker-1", claimed.lease_version, "applying"
    )
    assert moved.status == "applying"
    assert moved.lease_version == claimed.lease_version  # transition never bumps


@pytest.mark.asyncio
async def test_transition_rejects_a_disallowed_transition(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    with pytest.raises(MergeQueueError, match="not an allowed"):
        await store.transition(
            claimed.id, "worker-1", claimed.lease_version, "committing"
        )


@pytest.mark.asyncio
async def test_transition_to_verifying_requires_merge_proof(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    await store.transition(
        claimed.id, "worker-1", claimed.lease_version, "applying"
    )
    with pytest.raises(MergeQueueError, match="merge_proof_evidence_id"):
        await store.transition(
            claimed.id, "worker-1", claimed.lease_version, "verifying"
        )


@pytest.mark.asyncio
async def test_transition_is_lease_fenced(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    first = await store.claim("feat-q", "worker-1")  # lease_version 1
    assert first is not None
    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour' "
        "WHERE id = $1",
        first.id,
    )
    await store.claim("feat-q", "worker-2")  # lease_version 2
    with pytest.raises(LeaseFencedError):
        await store.transition(first.id, "worker-1", 1, "applying")


@pytest.mark.asyncio
async def test_transition_to_failed_records_failure_context(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    await store.transition(
        claimed.id, "worker-1", claimed.lease_version, "applying"
    )
    failed = await store.transition(
        claimed.id,
        "worker-1",
        claimed.lease_version,
        "failed",
        last_error="git apply conflict",
    )
    assert failed.status == "failed"
    assert failed.payload["last_error"] == "git apply conflict"


@pytest.mark.asyncio
async def test_transition_full_worker_progression_to_integrated(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    owner, version = "worker-1", claimed.lease_version

    merge_proof = await _insert_evidence(mq_conn, "feat-q")
    post_apply = await _insert_evidence(mq_conn, "feat-q")
    commit_proof = await _insert_evidence(mq_conn, "feat-q")

    await store.transition(claimed.id, owner, version, "applying")
    await store.transition(
        claimed.id, owner, version, "verifying",
        merge_proof_evidence_id=merge_proof,
    )
    await store.transition(
        claimed.id, owner, version, "committing",
        post_apply_gate_evidence_id=post_apply,
    )
    integrated = await store.transition(
        claimed.id, owner, version, "integrated",
        commit_proof_evidence_id=commit_proof,
        result_commit="abc123",
    )
    assert integrated.status == "integrated"
    assert integrated.merge_proof_evidence_id == merge_proof
    assert integrated.post_apply_gate_evidence_id == post_apply
    assert integrated.commit_proof_evidence_id == commit_proof
    assert integrated.result_commit == "abc123"


# ── proof evidence recorders (08c-5) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_record_merge_proof_creates_evidence(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))

    evidence_id = await store.record_merge_proof(
        item.id,
        feature_id=item.feature_id,
        group_idx=item.group_idx,
        proof=MergeProof(
            base_commit="base-1",
            applied_heads={"repo-a": "head-applied"},
            patch_digest="patch-sha",
            rebased=False,
        ),
    )
    assert evidence_id > 0
    kind = await mq_conn.fetchval(
        "SELECT kind FROM evidence_nodes WHERE id = $1", evidence_id
    )
    assert kind == "merge_proof"


@pytest.mark.asyncio
async def test_record_merge_proof_is_idempotent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    proof = MergeProof(base_commit="base-1", patch_digest="patch-sha")

    first = await store.record_merge_proof(
        item.id, feature_id=item.feature_id, group_idx=item.group_idx, proof=proof
    )
    second = await store.record_merge_proof(
        item.id, feature_id=item.feature_id, group_idx=item.group_idx, proof=proof
    )
    assert first == second
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM evidence_nodes WHERE kind = 'merge_proof'"
    )
    assert count == 1


@pytest.mark.asyncio
async def test_record_commit_proof_creates_evidence(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))

    evidence_id = await store.record_commit_proof(
        item.id,
        feature_id=item.feature_id,
        group_idx=item.group_idx,
        repo_proofs=[
            RepoCommitProof(
                repo_id="repo-a",
                repo_path="/repos/a",
                pre_apply_head="pre",
                applied_head="applied",
                result_commit="abc123",
                tree_sha="tree-sha",
                changed_paths=["src/x.py"],
                no_dirty_snapshot_id=999,
            )
        ],
    )
    assert evidence_id > 0
    kind = await mq_conn.fetchval(
        "SELECT kind FROM evidence_nodes WHERE id = $1", evidence_id
    )
    assert kind == "commit_proof"


@pytest.mark.asyncio
async def test_record_commit_proof_requires_repo_proofs(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    with pytest.raises(MergeQueueError, match="at least one repo proof"):
        await store.record_commit_proof(
            item.id,
            feature_id=item.feature_id,
            group_idx=item.group_idx,
            repo_proofs=[],
        )


@pytest.mark.asyncio
async def test_proof_evidence_drives_the_worker_progression(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    owner, version = "worker-1", claimed.lease_version

    await store.transition(item.id, owner, version, "applying")

    merge_proof = await store.record_merge_proof(
        item.id,
        feature_id=item.feature_id,
        group_idx=item.group_idx,
        proof=MergeProof(base_commit="base-1", patch_digest="patch-sha"),
    )
    await store.transition(
        item.id, owner, version, "verifying",
        merge_proof_evidence_id=merge_proof,
    )

    post_apply = await _insert_evidence(mq_conn, "feat-q")
    await store.transition(
        item.id, owner, version, "committing",
        post_apply_gate_evidence_id=post_apply,
    )

    commit_proof = await store.record_commit_proof(
        item.id,
        feature_id=item.feature_id,
        group_idx=item.group_idx,
        repo_proofs=[
            RepoCommitProof(
                repo_id="repo-a",
                repo_path="/repos/a",
                pre_apply_head="pre",
                applied_head="applied",
                result_commit="abc123",
                tree_sha="tree-sha",
                no_dirty_snapshot_id=1,
            )
        ],
    )
    integrated = await store.transition(
        item.id, owner, version, "integrated",
        commit_proof_evidence_id=commit_proof,
        result_commit="abc123",
    )
    assert integrated.status == "integrated"
    assert integrated.merge_proof_evidence_id == merge_proof
    assert integrated.commit_proof_evidence_id == commit_proof


# ── advance_repo_target (08d-1) ─────────────────────────────────────────────


async def _insert_workspace_snapshot(
    conn: asyncpg.Connection, feature_id: str
) -> int:
    journal_row = await conn.fetchval(
        "INSERT INTO execution_journal_rows "
        "(feature_id, idempotency_key, entry_type, status, request_digest) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        feature_id,
        f"jr:{uuid.uuid4().hex}",
        "merge",
        "succeeded",
        "rd",
    )
    return await conn.fetchval(
        "INSERT INTO workspace_snapshots "
        "(feature_id, idempotency_key, execution_journal_row_id, "
        " snapshot_digest) VALUES ($1, $2, $3, $4) RETURNING id",
        feature_id,
        f"ws:{uuid.uuid4().hex}",
        journal_row,
        "snap-digest",
    )


@pytest.mark.asyncio
async def test_advance_repo_target_full_progression(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    owner, version = "worker-1", claimed.lease_version

    pre = await store.advance_repo_target(
        item.id, "repo-a", owner, version, "pre_apply_recorded",
        pre_apply_head="head-pre",
    )
    assert pre.status == "pre_apply_recorded"
    assert pre.pre_apply_head == "head-pre"

    applied = await store.advance_repo_target(
        item.id, "repo-a", owner, version, "applied", applied_head="head-app",
    )
    assert applied.status == "applied"

    committed = await store.advance_repo_target(
        item.id, "repo-a", owner, version, "committed",
        result_commit="abc123", tree_sha="tree-sha",
    )
    assert committed.status == "committed"

    snapshot_id = await _insert_workspace_snapshot(mq_conn, "feat-q")
    clean = await store.advance_repo_target(
        item.id, "repo-a", owner, version, "clean",
        no_dirty_snapshot_id=snapshot_id,
    )
    assert clean.status == "clean"
    assert clean.no_dirty_snapshot_id == snapshot_id


@pytest.mark.parametrize(
    "target", ["pre_apply_recorded", "applied", "committed", "clean"]
)
@pytest.mark.asyncio
async def test_advance_repo_target_requires_proof_columns(
    mq_conn, target: str
) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    owner, version = "worker-1", claimed.lease_version
    # Advance (with proofs) up to the state just before `target`.
    chain = [
        ("pre_apply_recorded", {"pre_apply_head": "h-pre"}),
        ("applied", {"applied_head": "h-app"}),
        ("committed", {"result_commit": "abc", "tree_sha": "t"}),
    ]
    for status, proof in chain:
        if status == target:
            break
        await store.advance_repo_target(
            item.id, "repo-a", owner, version, status, **proof
        )
    # The `target` advance with no proof column must fail closed.
    with pytest.raises(MergeQueueError):
        await store.advance_repo_target(
            item.id, "repo-a", owner, version, target
        )


@pytest.mark.asyncio
async def test_advance_repo_target_rejects_skipping_a_state(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    claimed = await store.claim("feat-q", "worker-1")
    assert claimed is not None
    # pending -> applied skips pre_apply_recorded.
    with pytest.raises(MergeQueueError, match="not allowed"):
        await store.advance_repo_target(
            item.id, "repo-a", "worker-1", claimed.lease_version, "applied",
            applied_head="head-app",
        )


@pytest.mark.asyncio
async def test_advance_repo_target_is_lease_fenced(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-q")
    store = MergeQueueStore(mq_conn)
    item = await store.enqueue(await _setup_create(mq_conn))
    first = await store.claim("feat-q", "worker-1")  # lease_version 1
    assert first is not None
    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour' "
        "WHERE id = $1",
        item.id,
    )
    await store.claim("feat-q", "worker-2")  # lease_version 2
    with pytest.raises(LeaseFencedError):
        await store.advance_repo_target(
            item.id, "repo-a", "worker-1", 1, "pre_apply_recorded",
            pre_apply_head="head-pre",
        )
