"""Slice 08d-5 — GroupMergeCoordinator group checkpoint coverage.

Integration tests against a real Postgres queue database (the `mq_conn`
fixture). They skip when no Postgres is reachable.
"""

from __future__ import annotations

import uuid

import pytest

from iriai_build_v2.execution_control.merge_queue_store import (
    MergeQueueError,
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoTargetCreate,
    TaskCoverageCreate,
)
from iriai_build_v2.workflows.develop.execution.merge_queue import (
    CheckpointProjection,
    GroupMergeCoordinator,
    LeaseToken,
)

_DAG = "dag-sha"


async def _insert_feature(conn, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $1, $1, 'develop', 'ws-1')",
        feature_id,
    )


async def _insert_contract(conn, feature_id: str, task_id: str) -> int:
    return await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status) "
        "VALUES ($1, $2, $3, 1, $4, $5, 'active') RETURNING id",
        feature_id,
        f"contract:{feature_id}:{task_id}",
        _DAG,
        task_id,
        f"cd-{task_id}",
    )


async def _insert_evidence(conn, feature_id: str) -> int:
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash) "
        "VALUES ($1, $2, 'gate_request', 'hash') RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
    )


async def _enqueue_lane(
    conn,
    store: MergeQueueStore,
    feature_id: str,
    *,
    task_id: str,
    contract_id: int,
    lane: str,
    head: str,
    retry_of: int | None = None,
) -> int:
    gate = await _insert_evidence(conn, feature_id)
    item = await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            base_commit="base",
            head_commit=head,
            integration_lane=lane,
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract_id],
            patch_evidence_ids=[1],
            gate_evidence_ids=[gate],
            retry_of_queue_item_id=retry_of,
            task_coverage=[
                TaskCoverageCreate(task_id=task_id, contract_id=contract_id)
            ],
            repo_targets=[
                RepoTargetCreate(
                    repo_id="repo-a", repo_path="/repos/a", base_commit="base"
                )
            ],
        )
    )
    return item.id


async def _force_status(conn, feature_id: str, item_id: int, status: str) -> None:
    """Force a queue item to *status*, satisfying the schema proof CHECKs."""
    sets = ["status = $2"]
    params: list = [item_id, status]

    async def _ev() -> int:
        return await _insert_evidence(conn, feature_id)

    if status in ("verifying", "committing", "integrated", "checkpointing", "done"):
        params.append(await _ev())
        sets.append(f"merge_proof_evidence_id = ${len(params)}")
    if status in ("committing", "integrated", "checkpointing", "done"):
        params.append(await _ev())
        sets.append(f"post_apply_gate_evidence_id = ${len(params)}")
    if status in ("integrated", "checkpointing", "done"):
        params.append(await _ev())
        sets.append(f"commit_proof_evidence_id = ${len(params)}")
        params.append(f"commit-{item_id}")
        sets.append(f"result_commit = ${len(params)}")
    if status in ("checkpointing", "done"):
        sets.append("checkpoint_coverage_digest = 'cov'")
        sets.append("checkpoint_body_sha256 = 'body'")
    if status == "done":
        params.append(await _ev())
        sets.append(f"checkpoint_gate_evidence_id = ${len(params)}")
        params.append(await _ev())
        sets.append(f"checkpoint_evidence_id = ${len(params)}")
        sets.append("checkpoint_projection_id = 1")
    await conn.execute(
        f"UPDATE merge_queue_items SET {', '.join(sets)} WHERE id = $1",
        *params,
    )


def _expected_provider(expected: list[str]):
    async def provide(_feature_id, _dag, _group):
        return list(expected)

    return provide


def _coordinator(store: MergeQueueStore, expected: list[str]) -> GroupMergeCoordinator:
    return GroupMergeCoordinator(store, _expected_provider(expected))


@pytest.mark.asyncio
async def test_coverage_approved_when_expected_task_is_integrated(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "integrated")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is True
    assert cov.integrated_queue_item_ids == [lane]
    assert cov.missing_task_ids == []
    assert cov.duplicate_task_ids == []


@pytest.mark.asyncio
async def test_coverage_missing_when_no_lane_covers_a_task(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is False
    assert cov.missing_task_ids == ["T1"]


@pytest.mark.asyncio
async def test_coverage_duplicate_when_two_lanes_cover_a_task(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    # Enqueue lane A, cancel it so a second lane for T1 is allowed, then
    # force both to integrated — coverage() must defensively detect the dup.
    lane_a = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane_a, "cancelled")
    lane_b = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="repo:b", head="h2",
    )
    await _force_status(mq_conn, "feat-c", lane_a, "integrated")
    await _force_status(mq_conn, "feat-c", lane_b, "integrated")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is False
    assert cov.duplicate_task_ids == ["T1"]


@pytest.mark.asyncio
async def test_coverage_blocked_by_a_poisoned_lane(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "poisoned")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is False


@pytest.mark.asyncio
async def test_coverage_failed_lane_superseded_by_retry_is_approved(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    source = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", source, "failed")
    # An authorized retry replacement covering the same task.
    replacement = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h-retry", retry_of=source,
    )
    await _force_status(mq_conn, "feat-c", replacement, "integrated")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is True
    assert source in cov.failed_queue_item_ids
    assert cov.integrated_queue_item_ids == [replacement]


@pytest.mark.asyncio
async def test_coverage_failed_chain_superseded_by_transitive_retry_is_approved(
    mq_conn,
) -> None:
    # A commit_hygiene recovery can re-fail several times before its patch
    # finally integrates: original FAILED -> retry-1 FAILED -> retry-2
    # INTEGRATED. Every failed lane in that chain is transitively superseded by
    # the integrated head, so coverage must APPROVE the group — checking only
    # the DIRECT replacement (which is itself `failed`) would wrongly block the
    # checkpoint forever even though the task is integrated. (Mirrors feature
    # 8ac124d6 group 78 slice-14: lanes 5->9->11->13->14 all failed, 15
    # integrated.)
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    source = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", source, "failed")
    # Each retry is enqueued while its source is `failed` with no live
    # replacement (the retry-source rule), then itself forced terminal.
    retry1 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h-r1", retry_of=source,
    )
    await _force_status(mq_conn, "feat-c", retry1, "failed")
    retry2 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h-r2", retry_of=retry1,
    )
    await _force_status(mq_conn, "feat-c", retry2, "integrated")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is True
    assert source in cov.failed_queue_item_ids
    assert retry1 in cov.failed_queue_item_ids
    assert cov.integrated_queue_item_ids == [retry2]


@pytest.mark.asyncio
async def test_coverage_blocked_by_an_unsuperseded_failed_lane(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "failed")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is False
    assert cov.missing_task_ids == ["T1"]


@pytest.mark.asyncio
async def test_coverage_approved_for_a_multi_task_group(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    c1 = await _insert_contract(mq_conn, "feat-c", "T1")
    c2 = await _insert_contract(mq_conn, "feat-c", "T2")
    lane1 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=c1,
        lane="repo:a", head="h1",
    )
    lane2 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T2", contract_id=c2,
        lane="repo:b", head="h2",
    )
    await _force_status(mq_conn, "feat-c", lane1, "integrated")
    await _force_status(mq_conn, "feat-c", lane2, "integrated")

    cov = await _coordinator(store, ["T1", "T2"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is True
    assert sorted(cov.integrated_queue_item_ids) == sorted([lane1, lane2])
    assert cov.missing_task_ids == []


@pytest.mark.asyncio
async def test_coverage_reports_an_already_done_group(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "done")

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    # An already-done group is still "covered" — checkpoint_group (08d-5b)
    # detects this as the idempotent-success case.
    assert cov.approved is True
    assert cov.done_queue_item_ids == [lane]
    assert cov.integrated_queue_item_ids == []


@pytest.mark.asyncio
async def test_coverage_blocked_when_retry_replacement_is_not_a_candidate(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    source = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", source, "failed")
    # The replacement is enqueued but still `queued` — not yet a candidate.
    await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h-retry", retry_of=source,
    )

    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is False


@pytest.mark.asyncio
async def test_coverage_blocked_by_a_lane_covering_an_unexpected_task(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    c1 = await _insert_contract(mq_conn, "feat-c", "T1")
    c2 = await _insert_contract(mq_conn, "feat-c", "T2")
    lane1 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=c1,
        lane="repo:a", head="h1",
    )
    lane2 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T2", contract_id=c2,
        lane="repo:b", head="h2",
    )
    await _force_status(mq_conn, "feat-c", lane1, "integrated")
    await _force_status(mq_conn, "feat-c", lane2, "integrated")

    # Only T1 is expected — lane2 covering T2 is a DAG-drift integrity violation.
    cov = await _coordinator(store, ["T1"]).coverage("feat-c", _DAG, 1)
    assert cov.approved is False


# ── complete_checkpoint (08d-5b) ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_complete_checkpoint_advances_integrated_lanes_to_done(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    c1 = await _insert_contract(mq_conn, "feat-c", "T1")
    c2 = await _insert_contract(mq_conn, "feat-c", "T2")
    lane1 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=c1,
        lane="repo:a", head="h1",
    )
    lane2 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T2", contract_id=c2,
        lane="repo:b", head="h2",
    )
    await _force_status(mq_conn, "feat-c", lane1, "integrated")
    await _force_status(mq_conn, "feat-c", lane2, "integrated")
    gate_ev = await _insert_evidence(mq_conn, "feat-c")
    body_ev = await _insert_evidence(mq_conn, "feat-c")

    advanced = await store.complete_checkpoint(
        [lane1, lane2],
        checkpoint_gate_evidence_id=gate_ev,
        checkpoint_evidence_id=body_ev,
        checkpoint_projection_id=7,
        checkpoint_coverage_digest="cov-digest",
        checkpoint_body_sha256="body-sha",
    )
    assert sorted(advanced) == sorted([lane1, lane2])
    for lane_id in (lane1, lane2):
        item = await store.get(lane_id)
        assert item is not None
        assert item.status == "done"
        assert item.checkpoint_gate_evidence_id == gate_ev
        assert item.checkpoint_evidence_id == body_ev
        assert item.checkpoint_projection_id == 7
        assert item.lease_owner is None


@pytest.mark.asyncio
async def test_complete_checkpoint_is_idempotent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "integrated")
    gate_ev = await _insert_evidence(mq_conn, "feat-c")
    body_ev = await _insert_evidence(mq_conn, "feat-c")
    kwargs = dict(
        checkpoint_gate_evidence_id=gate_ev,
        checkpoint_evidence_id=body_ev,
        checkpoint_projection_id=9,
        checkpoint_coverage_digest="cov",
        checkpoint_body_sha256="body",
    )
    first = await store.complete_checkpoint([lane], **kwargs)
    second = await store.complete_checkpoint([lane], **kwargs)
    assert first == [lane]
    assert second == []  # already done — no-op
    item = await store.get(lane)
    assert item is not None and item.status == "done"


@pytest.mark.asyncio
async def test_complete_checkpoint_skips_non_candidate_lanes(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    c1 = await _insert_contract(mq_conn, "feat-c", "T1")
    c2 = await _insert_contract(mq_conn, "feat-c", "T2")
    integrated = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=c1,
        lane="repo:a", head="h1",
    )
    await _force_status(mq_conn, "feat-c", integrated, "integrated")
    queued = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T2", contract_id=c2,
        lane="repo:b", head="h2",
    )  # left at `queued`
    gate_ev = await _insert_evidence(mq_conn, "feat-c")
    body_ev = await _insert_evidence(mq_conn, "feat-c")

    advanced = await store.complete_checkpoint(
        [integrated, queued],
        checkpoint_gate_evidence_id=gate_ev,
        checkpoint_evidence_id=body_ev,
        checkpoint_projection_id=1,
        checkpoint_coverage_digest="cov",
        checkpoint_body_sha256="body",
    )
    assert advanced == [integrated]
    queued_item = await store.get(queued)
    assert queued_item is not None and queued_item.status == "queued"


@pytest.mark.asyncio
async def test_complete_checkpoint_requires_digests(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "integrated")
    gate_ev = await _insert_evidence(mq_conn, "feat-c")
    body_ev = await _insert_evidence(mq_conn, "feat-c")
    with pytest.raises(MergeQueueError, match="digest"):
        await store.complete_checkpoint(
            [lane],
            checkpoint_gate_evidence_id=gate_ev,
            checkpoint_evidence_id=body_ev,
            checkpoint_projection_id=1,
            checkpoint_coverage_digest="",
            checkpoint_body_sha256="body",
        )


# ── checkpoint_group (08d-5c) ───────────────────────────────────────────────


_TOKEN = LeaseToken(item_id=0, lease_owner="coordinator", lease_version=0)


def _projector(conn, feature_id: str, calls: list):
    async def project(coverage, body):
        calls.append(body)
        gate = await _insert_evidence(conn, feature_id)
        body_ev = await _insert_evidence(conn, feature_id)
        return CheckpointProjection(
            checkpoint_projection_id=100 + len(calls),
            checkpoint_gate_evidence_id=gate,
            checkpoint_evidence_id=body_ev,
            body_sha256=f"body-sha-{len(calls)}",
        )

    return project


@pytest.mark.asyncio
async def test_checkpoint_group_marks_covered_lanes_done(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "integrated")

    calls: list = []
    coord = GroupMergeCoordinator(
        store, _expected_provider(["T1"]), _projector(mq_conn, "feat-c", calls)
    )
    coverage = await coord.coverage("feat-c", _DAG, 1)
    result = await coord.checkpoint_group(coverage, _TOKEN)

    assert result.checkpointed is True
    assert result.approved is True
    assert lane in result.done_queue_item_ids
    assert result.checkpoint_projection_id is not None
    item = await store.get(lane)
    assert item is not None
    assert item.status == "done"
    assert item.checkpoint_projection_id == result.checkpoint_projection_id
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_checkpoint_group_refuses_an_unapproved_group(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    # T1 expected but no lane covers it -> coverage not approved.
    calls: list = []
    coord = GroupMergeCoordinator(
        store, _expected_provider(["T1"]), _projector(mq_conn, "feat-c", calls)
    )
    coverage = await coord.coverage("feat-c", _DAG, 1)
    result = await coord.checkpoint_group(coverage, _TOKEN)

    assert result.checkpointed is False
    assert result.approved is False
    assert calls == []  # the projector is never invoked for an unapproved group


@pytest.mark.asyncio
async def test_checkpoint_group_is_idempotent(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "integrated")

    calls: list = []
    coord = GroupMergeCoordinator(
        store, _expected_provider(["T1"]), _projector(mq_conn, "feat-c", calls)
    )
    coverage = await coord.coverage("feat-c", _DAG, 1)

    first = await coord.checkpoint_group(coverage, _TOKEN)
    second = await coord.checkpoint_group(coverage, _TOKEN)

    assert first.checkpointed is True
    assert second.checkpointed is True
    # The projector ran exactly once — the second call sees an already-done
    # group and returns idempotently.
    assert len(calls) == 1
    item = await store.get(lane)
    assert item is not None and item.status == "done"


@pytest.mark.asyncio
async def test_checkpoint_group_requires_a_projector(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    coord = GroupMergeCoordinator(store, _expected_provider(["T1"]))
    coverage = await coord.coverage("feat-c", _DAG, 1)
    with pytest.raises(MergeQueueError, match="checkpoint_projector"):
        await coord.checkpoint_group(coverage, _TOKEN)


@pytest.mark.asyncio
async def test_checkpoint_group_recovers_with_an_idempotent_projector(
    mq_conn,
) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, "feat-c", "T1")
    lane = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=contract,
        lane="group", head="h1",
    )
    await _force_status(mq_conn, "feat-c", lane, "integrated")

    # An idempotent projector — same ids on every invocation.
    gate = await _insert_evidence(mq_conn, "feat-c")
    body_ev = await _insert_evidence(mq_conn, "feat-c")
    calls: list = []

    async def projector(coverage, body):
        calls.append(body)
        return CheckpointProjection(
            checkpoint_projection_id=555,
            checkpoint_gate_evidence_id=gate,
            checkpoint_evidence_id=body_ev,
            body_sha256="fixed-body-sha",
        )

    coord = GroupMergeCoordinator(store, _expected_provider(["T1"]), projector)
    coverage = await coord.coverage("feat-c", _DAG, 1)
    first = await coord.checkpoint_group(coverage, _TOKEN)
    assert first.checkpointed is True

    # Simulate a crash that lost the checkpoint completion — reset the lane.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'integrated', "
        "checkpoint_gate_evidence_id = NULL, checkpoint_evidence_id = NULL, "
        "checkpoint_projection_id = NULL, checkpoint_coverage_digest = '', "
        "checkpoint_body_sha256 = '' WHERE id = $1",
        lane,
    )

    # Recovery re-run — the idempotent projector returns the same ids.
    second = await coord.checkpoint_group(coverage, _TOKEN)
    assert second.checkpointed is True
    assert second.checkpoint_projection_id == 555
    item = await store.get(lane)
    assert item is not None and item.status == "done"
    assert item.checkpoint_projection_id == 555
    assert len(calls) == 2  # ran twice, but idempotently — no double projection


@pytest.mark.asyncio
async def test_checkpoint_group_detects_divergent_checkpoint_ids(mq_conn) -> None:
    await _insert_feature(mq_conn, "feat-c")
    store = MergeQueueStore(mq_conn)
    c1 = await _insert_contract(mq_conn, "feat-c", "T1")
    c2 = await _insert_contract(mq_conn, "feat-c", "T2")
    lane1 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T1", contract_id=c1,
        lane="repo:a", head="h1",
    )
    lane2 = await _enqueue_lane(
        mq_conn, store, "feat-c", task_id="T2", contract_id=c2,
        lane="repo:b", head="h2",
    )
    await _force_status(mq_conn, "feat-c", lane1, "done")
    await _force_status(mq_conn, "feat-c", lane2, "done")
    # Split-brain: lane2 carries a different checkpoint_projection_id.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET checkpoint_projection_id = 999 "
        "WHERE id = $1",
        lane2,
    )

    calls: list = []
    coord = GroupMergeCoordinator(
        store, _expected_provider(["T1", "T2"]),
        _projector(mq_conn, "feat-c", calls),
    )
    coverage = await coord.coverage("feat-c", _DAG, 1)
    result = await coord.checkpoint_group(coverage, _TOKEN)

    assert result.checkpointed is False
    assert "checkpoint_contradiction" in result.detail
    assert calls == []  # short-circuited — the projector is never called
