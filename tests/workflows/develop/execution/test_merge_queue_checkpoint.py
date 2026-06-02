"""Slice 08e-3b — durable merge queue post-DAG CHECKPOINT splice.

Integration tests for ``implementation._checkpoint_durable_merge_queue_group``
— the post-DAG group checkpoint that REPLACES the legacy ``_commit_group`` /
``_record_dag_group_commit_proof`` / ``_project_dag_group_checkpoint`` path for
the queue-driven flow. 08e-2 enqueues ``task:{id}`` lanes; 08e-3a drains them so
each lane is ``integrated``; 08e-3b runs ``GroupMergeCoordinator.checkpoint_group``
over those ``integrated`` lanes, which projects the single ``dag-group:{group_idx}``
checkpoint (owner ``merge_queue``) and advances every covered lane to ``done``.

These tests exercise the FULL end-to-end queue-driven path — enqueue (08e-2) ->
drain (08e-3a) -> checkpoint (08e-3b) — against a real Postgres queue database
(the ``mq_conn`` fixture from this directory's conftest) and a real on-disk git
repo for the canonical repo / base commit. They skip when no Postgres is
reachable. Coverage:

* a clean single-task group: enqueue -> drain -> ``integrated`` -> checkpoint
  projects ``dag-group:*`` (owner ``merge_queue``) and the lane is ``done``;
* a multi-task group: every task's lane drains and the single ``dag-group:*``
  checkpoint covers them all;
* the checkpoint is idempotent — a second call is a no-op success and never
  re-projects;
* a group with an undrained / failed lane fails the checkpoint closed and
  routes a typed ``checkpoint_contradiction`` through the Slice 07 router
  (NEVER a legacy ``_commit_group`` fallback);
* the checkpoint fails closed (no legacy checkpoint fallback) without a
  typed store.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.execution_control import ExecutionControlStore
from iriai_build_v2.execution_control.merge_queue_store import (
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoTargetCreate,
    TaskCoverageCreate,
)
from iriai_build_v2.workflows.develop.execution import git_service
from iriai_build_v2.workflows.develop.phases import implementation as impl

_DAG = "dag-sha"
_GROUP = 1


# ── git + DB staging helpers (mirror test_merge_queue_drain.py) ──────────────


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    ).stdout


def _init_repo(path: Path) -> str:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    _git(path, "config", "user.email", "test@example.com")
    _git(path, "config", "user.name", "Test User")
    _git(path, "config", "commit.gpgsign", "false")
    (path / "README.md").write_text("init\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-q", "-m", "initial")
    return _git(path, "rev-parse", "HEAD").strip()


def _diff_for_appended_line(repo: Path, text: str) -> str:
    """Stage no change; capture a diff that appends *text* to README.md."""
    original = (repo / "README.md").read_text()
    (repo / "README.md").write_text(original + text)
    patch = _git(repo, "diff")
    (repo / "README.md").write_text(original)
    return patch


async def _insert_feature(conn, feature_id: str) -> None:
    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $1, $1, 'develop', 'ws-1')",
        feature_id,
    )


async def _insert_contract(
    conn,
    feature_id: str,
    task_id: str,
    *,
    allowed_paths: list[dict] | None = None,
) -> int:
    return await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status, allowed_paths) "
        "VALUES ($1, $2, $3, 1, $4, $5, 'active', $6::jsonb) RETURNING id",
        feature_id,
        f"contract:{feature_id}:{task_id}",
        _DAG,
        task_id,
        f"cd-{task_id}",
        json.dumps(allowed_paths or []),
    )


async def _insert_artifact(conn, feature_id: str, key: str, value: str) -> int:
    return await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value) "
        "VALUES ($1, $2, $3) RETURNING id",
        feature_id,
        key,
        value,
    )


async def _insert_patch_evidence(
    conn, feature_id: str, *, repo_id: str, diff_artifact_id: int
) -> int:
    """A ``sandbox_patch_summary`` evidence node as the dispatcher records it."""
    payload = {"repo_id": repo_id, "diff_artifact_id": diff_artifact_id}
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, payload) "
        "VALUES ($1, $2, 'sandbox_patch_summary', $3, $4::jsonb) RETURNING id",
        feature_id,
        f"patch:{uuid.uuid4().hex}",
        f"hash-{uuid.uuid4().hex}",
        json.dumps(payload),
    )


async def _insert_gate_evidence(conn, feature_id: str) -> int:
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, status) "
        "VALUES ($1, $2, 'aggregate_verdict', $3, 'approved') RETURNING id",
        feature_id,
        f"gate:{uuid.uuid4().hex}",
        f"hash-{uuid.uuid4().hex}",
    )


async def _enqueue_drainable_lane(
    conn,
    feature_id: str,
    *,
    task_id: str,
    repo_id: str,
    repo_path: Path,
    base_commit: str,
    patch_text: str,
    allowed_file: str = "README.md",
    retry_of: int | None = None,
    head_commit: str = "",
    contract: int | None = None,
) -> int:
    """Enqueue one ``queued`` ``task:{id}`` lane the drain can claim.

    Mirrors what ``_enqueue_durable_merge_queue_for_results`` (08e-2) writes:
    a per-task lane with a real ``sandbox_patch_summary`` patch evidence node,
    a diff artifact, an active contract scoped to *allowed_file*, a pre-queue
    aggregate_verdict gate node, one task-coverage row, and one repo target.
    """
    if contract is None:
        contract = await _insert_contract(
            conn,
            feature_id,
            task_id,
            allowed_paths=[
                {"repo_id": repo_id, "path": allowed_file, "match_kind": "file"}
            ],
        )
    diff_artifact = await _insert_artifact(
        conn, feature_id, f"dag-sandbox-diff:{task_id}", patch_text
    )
    patch_evidence = await _insert_patch_evidence(
        conn, feature_id, repo_id=repo_id, diff_artifact_id=diff_artifact
    )
    gate = await _insert_gate_evidence(conn, feature_id)
    store = MergeQueueStore(conn)
    item = await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=_GROUP,
            base_commit=base_commit,
            repo_id=repo_id,
            repo_path=str(repo_path),
            head_commit=head_commit,
            integration_lane=f"task:{task_id}",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[patch_evidence],
            gate_evidence_ids=[gate],
            retry_of_queue_item_id=retry_of,
            task_coverage=[
                TaskCoverageCreate(task_id=task_id, contract_id=contract)
            ],
            repo_targets=[
                RepoTargetCreate(
                    repo_id=repo_id,
                    repo_path=str(repo_path),
                    base_commit=base_commit,
                )
            ],
            payload={"stage": "implementation", "task_ids": [task_id]},
        )
    )
    return item.id


def _runner(conn) -> SimpleNamespace:
    """A minimal runner with the typed store + a feature-event sink."""
    return SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(conn)},
    )


def _feature(feature_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=feature_id, slug=feature_id, metadata={})


async def _dag_group_projection(conn, feature_id: str, group_idx: int):
    """The ``dag-group:*`` compatibility projection row, or None."""
    return await conn.fetchrow(
        "SELECT projection_key, projection_owner, projection_kind "
        "FROM execution_artifact_projections "
        "WHERE feature_id = $1 AND projection_key = $2",
        feature_id,
        f"dag-group:{group_idx}",
    )


async def _drain(conn, feature_id: str) -> list:
    return await impl._drain_durable_merge_queue_for_feature(
        _runner(conn), _feature(feature_id), dag_sha256=_DAG
    )


# ── end-to-end happy path: enqueue -> drain -> checkpoint ────────────────────


@pytest.mark.asyncio
async def test_checkpoint_projects_dag_group_for_a_drained_single_task_group(
    mq_conn, tmp_path: Path
) -> None:
    """A clean single-task group lands fully through the durable queue.

    The lane is enqueued (08e-2), drained to ``integrated`` (08e-3a), then the
    08e-3b checkpoint runs ``GroupMergeCoordinator.checkpoint_group``: it
    projects the single ``dag-group:1`` checkpoint (owner ``merge_queue`` — the
    legacy ``_commit_group``/``_project_dag_group_checkpoint`` path is NOT
    used) and advances the lane ``integrated -> done``.
    """
    feature_id = "feat-ckpt-clean"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "checkpoint line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )

    # 08e-3a drain: the lane reaches `integrated`.
    drained = await _drain(mq_conn, feature_id)
    assert len(drained) == 1 and drained[0].integrated is True

    # 08e-3b checkpoint: the coordinator projects `dag-group:1` and the lane
    # advances to `done`.
    result = await impl._checkpoint_durable_merge_queue_group(
        _runner(mq_conn),
        _feature(feature_id),
        dag_sha256=_DAG,
        group_idx=_GROUP,
        expected_task_ids=["TASK-1"],
    )

    assert result.checkpointed is True
    assert result.group_idx == _GROUP
    assert lane in result.done_queue_item_ids
    assert result.result_commit  # the canonical commit the drain produced

    # The lane is terminal `done` with the full checkpoint proof chain.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None
    assert item.status == "done"
    assert item.checkpoint_projection_id is not None
    assert item.checkpoint_gate_evidence_id is not None
    assert item.checkpoint_evidence_id is not None
    assert item.checkpoint_coverage_digest
    assert item.checkpoint_body_sha256

    # The single `dag-group:1` compatibility projection exists, owned by the
    # merge queue — this is the 08e-3b splice replacing the legacy projection.
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is not None
    assert projection["projection_key"] == "dag-group:1"
    assert projection["projection_owner"] == "merge_queue"


@pytest.mark.asyncio
async def test_checkpoint_covers_every_task_of_a_multi_task_group(
    mq_conn, tmp_path: Path
) -> None:
    """A multi-task group drains every lane and checkpoints them as one group.

    Two tasks each enqueue a ``task:{id}`` lane; both drain to ``integrated``;
    the single 08e-3b checkpoint covers both and advances both to ``done`` —
    the group checkpoint stays a single coordinator-owned projection even
    though the group split into per-task lanes.
    """
    feature_id = "feat-ckpt-multi"
    await _insert_feature(mq_conn, feature_id)
    repo_a = tmp_path / "app_a"
    base_a = _init_repo(repo_a)
    patch_a = _diff_for_appended_line(repo_a, "from a\n")
    repo_b = tmp_path / "app_b"
    base_b = _init_repo(repo_b)
    patch_b = _diff_for_appended_line(repo_b, "from b\n")

    lane_a = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-A", repo_id="app_a",
        repo_path=repo_a, base_commit=base_a, patch_text=patch_a,
    )
    lane_b = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-B", repo_id="app_b",
        repo_path=repo_b, base_commit=base_b, patch_text=patch_b,
    )

    drained = await _drain(mq_conn, feature_id)
    assert {r.item_id for r in drained} == {lane_a, lane_b}
    assert all(r.integrated for r in drained)

    result = await impl._checkpoint_durable_merge_queue_group(
        _runner(mq_conn),
        _feature(feature_id),
        dag_sha256=_DAG,
        group_idx=_GROUP,
        expected_task_ids=["TASK-A", "TASK-B"],
    )

    assert result.checkpointed is True
    assert set(result.done_queue_item_ids) == {lane_a, lane_b}

    store = MergeQueueStore(mq_conn)
    for lane in (lane_a, lane_b):
        item = await store.get(lane)
        assert item is not None and item.status == "done"
    # One single `dag-group:1` projection covers the whole multi-lane group.
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is not None
    assert projection["projection_owner"] == "merge_queue"


@pytest.mark.asyncio
async def test_checkpoint_is_idempotent_on_a_re_run(
    mq_conn, tmp_path: Path
) -> None:
    """A second checkpoint of an already-``done`` group is a no-op success.

    Once the group is checkpointed (all lanes ``done``), re-running
    ``_checkpoint_durable_merge_queue_group`` — e.g. on a crash/resume —
    detects the already-checkpointed group and returns an idempotent success
    without re-projecting ``dag-group:*``.
    """
    feature_id = "feat-ckpt-idem"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "idem line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )
    await _drain(mq_conn, feature_id)

    first = await impl._checkpoint_durable_merge_queue_group(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG,
        group_idx=_GROUP, expected_task_ids=["TASK-1"],
    )
    second = await impl._checkpoint_durable_merge_queue_group(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG,
        group_idx=_GROUP, expected_task_ids=["TASK-1"],
    )

    assert first.checkpointed is True
    assert second.checkpointed is True
    assert lane in second.done_queue_item_ids
    # The lane is still terminal `done` — not re-driven.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "done"
    # Exactly one `dag-group:1` projection row exists (no double-projection).
    rows = await mq_conn.fetch(
        "SELECT id FROM execution_artifact_projections "
        "WHERE feature_id = $1 AND projection_key = 'dag-group:1'",
        feature_id,
    )
    assert len(rows) == 1


# ── 08g P2-A: a lane crashed in `checkpointing` is recovered, converges ──────


@pytest.mark.asyncio
async def test_drain_recovers_a_lane_crashed_in_checkpointing(
    mq_conn, tmp_path: Path
) -> None:
    """A lane crashed in ``checkpointing`` is recovered to ``done`` (08g P2-A).

    ``MergeQueueStore.recover_expired`` re-takes an expired ``checkpointing``
    row, but ``_LEASE_TRANSITIONS`` has no ``checkpointing`` forward key — so
    before 08g P2-A the recovery's ``transition(..., 'failed')`` raised
    ``MergeQueueError`` which the drain swallowed as a benign no-op. The row
    stayed ``checkpointing`` with an expired lease and was re-recovered every
    drain pass until ``MAX_RECOVERIES`` poisoned it (3 wasted cycles).

    Doc 08 § "Rollback And Recovery Table" — the ``checkpointing`` row — says a
    crashed ``checkpointing`` lane must "rerun the idempotent group checkpoint
    transaction", terminating at ``done`` or ``poisoned`` (NEVER a repo reset:
    a ``checkpointing`` lane has already committed). The 08g P2-A fix routes a
    recovered ``checkpointing`` lane to ``_recover_one_crashed_checkpointing_
    group``, which re-drives ``_checkpoint_durable_merge_queue_group``.

    This test stages exactly that crash: a lane is enqueued, drained to
    ``integrated``, then manually moved to ``checkpointing`` (with the real
    ``checkpoint_coverage_digest``/``checkpoint_body_sha256`` columns the schema
    CHECK requires) with an expired lease — simulating a coordinator that died
    after entering ``checkpointing`` but before ``complete_checkpoint``. A drain
    re-run must pick the lane up via ``recover_expired``, re-run the idempotent
    checkpoint, and CONVERGE the lane to terminal ``done`` — not loop.
    """
    feature_id = "feat-ckpt-crash"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "checkpointing crash line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )

    # Drain the lane to `integrated` (its full proof chain is now populated).
    drained = await _drain(mq_conn, feature_id)
    assert len(drained) == 1 and drained[0].integrated is True
    store = MergeQueueStore(mq_conn)
    integrated = await store.get(lane)
    assert integrated is not None and integrated.status == "integrated"
    head_after_drain = await git_service.head_commit(repo)

    # ── stage the crash: move the lane `integrated -> checkpointing` with the
    #    real digest columns the `merge_queue_items_checkpoint_digests_check`
    #    schema CHECK requires, and an expired lease — exactly a coordinator
    #    that died mid-`checkpointing` before `complete_checkpoint`.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'checkpointing', "
        "checkpoint_coverage_digest = 'staged-coverage-digest', "
        "checkpoint_body_sha256 = 'staged-body-sha', "
        "lease_owner = 'crashed_coordinator', lease_version = lease_version + 1, "
        "leased_until = now() - interval '1 hour' WHERE id = $1",
        lane,
    )
    crashed = await store.get(lane)
    assert crashed is not None and crashed.status == "checkpointing"

    # A normal `claim` must NOT pick up a `checkpointing` lane — it is invisible
    # to the drain without the `recover_expired` pass.
    assert await store.claim(feature_id, "another_worker") is None

    # ── the drain re-run: `recover_expired` re-takes the `checkpointing` lane
    #    and `_recover_one_crashed_checkpointing_group` re-drives the idempotent
    #    group checkpoint, converging the lane to `done`.
    drained_again = await _drain(mq_conn, feature_id)

    assert len(drained_again) == 1
    result = drained_again[0]
    assert result.item_id == lane
    # The recovered lane converged to the doc-08 `done` terminal — NOT failed,
    # NOT poisoned, NOT looped.
    assert result.terminal_status == "done"
    assert result.succeeded is True

    # The lane is terminal `done` with the full checkpoint proof chain — the
    # idempotent checkpoint re-run completed it.
    item = await store.get(lane)
    assert item is not None
    assert item.status == "done"
    assert item.checkpoint_projection_id is not None
    assert item.checkpoint_gate_evidence_id is not None
    assert item.checkpoint_evidence_id is not None

    # The `dag-group:1` compatibility projection now exists — the crashed
    # `checkpointing` was driven to completion, not abandoned.
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is not None
    assert projection["projection_owner"] == "merge_queue"

    # The canonical commit the drain produced was preserved untouched — a
    # `checkpointing` lane is past `integrated`, so recovery never resets it.
    assert await git_service.head_commit(repo) == head_after_drain
    assert head_after_drain != base

    # Convergence: a second drain re-run finds nothing claimable or
    # recoverable — the `done` lane is terminal and is NOT re-recovered. This
    # is the anti-loop assertion: before P2-A the lane would still be
    # `checkpointing` here and re-recovered every pass.
    third = await _drain(mq_conn, feature_id)
    assert third == []


@pytest.mark.asyncio
async def test_drain_poisons_a_crashed_checkpointing_lane_with_incomplete_group(
    mq_conn, tmp_path: Path
) -> None:
    """A crashed ``checkpointing`` lane whose group cannot checkpoint is poisoned.

    Doc 08 § "Rollback And Recovery Table" allows a crashed ``checkpointing``
    lane to terminate at ``done`` OR ``poisoned``. When the idempotent
    checkpoint re-drive cannot complete — here a second task of the group has a
    terminal ``failed`` lane, so ``checkpoint_group`` coverage is not approved
    — 08g P2-A drives the crashed lane to terminal ``poisoned`` via the
    ``checkpointing -> poisoned`` transition the fix added to
    ``_LEASE_TRANSITIONS``. The lane reaches a terminal state, so the drain's
    ``recover_expired`` pass cannot re-recover it and loop.
    """
    feature_id = "feat-ckpt-crash-incomplete"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "incomplete group line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )
    drained = await _drain(mq_conn, feature_id)
    assert len(drained) == 1 and drained[0].integrated is True
    store = MergeQueueStore(mq_conn)

    # Add a SECOND task `TASK-2` of the same group whose lane will FAIL the
    # drain: its repo diverges so the queued patch conflicts.
    # `_recover_one_crashed_checkpointing_group` reconstructs the group's
    # expected task set from EVERY lane's coverage rows — a terminal `failed`
    # TASK-2 lane (unsuperseded) makes `checkpoint_group` coverage refuse.
    repo2 = tmp_path / "app2"
    base2 = _init_repo(repo2)
    patch2 = _diff_for_appended_line(repo2, "task2 line\n")
    lane2 = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-2", repo_id="app2",
        repo_path=repo2, base_commit=base2, patch_text=patch2,
    )
    # Diverge repo2 so the queued TASK-2 patch conflicts -> its drain fails.
    (repo2 / "README.md").write_text("diverged content\n")
    _git(repo2, "add", "README.md")
    _git(repo2, "commit", "-q", "-m", "diverge")

    # Stage the crash on the TASK-1 lane: `integrated -> checkpointing` with an
    # expired lease.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status = 'checkpointing', "
        "checkpoint_coverage_digest = 'staged-coverage-digest', "
        "checkpoint_body_sha256 = 'staged-body-sha', "
        "lease_owner = 'crashed_coordinator', lease_version = lease_version + 1, "
        "leased_until = now() - interval '1 hour' WHERE id = $1",
        lane,
    )
    head_before = await git_service.head_commit(repo)

    # The drain re-run: `claim` first drains the `queued` TASK-2 lane (it FAILS
    # — conflicting patch); then `recover_expired` re-takes the `checkpointing`
    # TASK-1 lane and the idempotent checkpoint re-drive sees TASK-2 covered by
    # a terminal `failed` lane, so coverage is not approved and the crashed
    # TASK-1 lane is poisoned.
    drained_again = await _drain(mq_conn, feature_id)
    by_id = {r.item_id: r for r in drained_again}
    assert lane in by_id and lane2 in by_id
    # TASK-2's lane failed the drain (conflicting patch).
    assert by_id[lane2].terminal_status == "failed"
    crashed_result = by_id[lane]
    # The crashed `checkpointing` lane reached the doc-08 `poisoned` terminal.
    assert crashed_result.terminal_status == "poisoned"
    assert crashed_result.failure_class == "checkpoint_contradiction"
    assert crashed_result.routed_failure.get("routed") is True

    item = await store.get(lane)
    assert item is not None and item.status == "poisoned"
    # The committed canonical state is preserved — `checkpointing` recovery
    # never resets a lane past `integrated`.
    assert await git_service.head_commit(repo) == head_before

    # No `dag-group:1` projection — the checkpoint never completed.
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is None

    # Convergence: a re-drive finds nothing recoverable for the poisoned lane.
    third = await _drain(mq_conn, feature_id)
    assert all(r.item_id != lane for r in third)


# ── fail closed: an undrained / failed lane blocks the checkpoint, routed ────


@pytest.mark.asyncio
async def test_checkpoint_fails_closed_and_routes_when_a_lane_is_not_integrated(
    mq_conn, tmp_path: Path
) -> None:
    """A group with a non-``integrated`` lane fails the checkpoint closed.

    One task's lane drained to ``integrated``; a second expected task has NO
    covering lane (its drain never ran / failed). ``checkpoint_group`` coverage
    is not approved, so ``_checkpoint_durable_merge_queue_group`` returns
    ``checkpointed=False`` and routes a typed ``checkpoint_contradiction``
    through the Slice 07 failure router — it NEVER falls back to the legacy
    ``_commit_group`` / direct checkpoint, and no ``dag-group:*`` is projected.
    """
    feature_id = "feat-ckpt-incomplete"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "only one task\n")

    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )
    await _drain(mq_conn, feature_id)

    runner = _runner(mq_conn)
    # The group expects TASK-1 AND TASK-2, but only TASK-1 has a lane.
    result = await impl._checkpoint_durable_merge_queue_group(
        runner,
        _feature(feature_id),
        dag_sha256=_DAG,
        group_idx=_GROUP,
        expected_task_ids=["TASK-1", "TASK-2"],
    )

    assert result.checkpointed is False
    assert "not approved" in result.detail
    # The typed checkpoint failure was routed through the Slice 07 router.
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "checkpoint_contradiction"
    assert result.routed_failure["failure_type"] == "checkpoint_after_failed_gate"
    # checkpoint_contradiction is a deterministic, fail-closed quiesce route.
    assert result.routed_failure["route_action"] == "quiesce"
    assert result.routed_failure.get("typed_failure_id")

    # No `dag-group:*` projection was written — checkpoint never proceeded.
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is None

    # The router actually recorded a typed failure row.
    router = runner.services["failure_router"]
    record = router.get_failure(result.routed_failure["typed_failure_id"])
    assert record.observation.failure_class == "checkpoint_contradiction"
    assert record.observation.source == "merge_queue"


@pytest.mark.asyncio
async def test_checkpoint_fails_closed_and_routes_a_failed_drained_lane(
    mq_conn, tmp_path: Path
) -> None:
    """A terminal ``failed`` lane in the group blocks the checkpoint, routed.

    The lane's queued patch is stale (its base diverged), so the drain fails it
    closed (``merge_conflict``). ``checkpoint_group`` coverage rejects the
    group (a ``failed`` lane covers an expected task with no superseding
    retry). The 08e-3b checkpoint returns ``checkpointed=False`` and routes a
    typed ``checkpoint_contradiction`` — no legacy fallback, no ``dag-group:*``.
    """
    feature_id = "feat-ckpt-failedlane"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "stale patch\n")

    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )
    # Diverge the canonical repo so the queued patch no longer applies.
    (repo / "README.md").write_text("totally different\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "diverge")

    drained = await _drain(mq_conn, feature_id)
    assert len(drained) == 1 and drained[0].integrated is False

    result = await impl._checkpoint_durable_merge_queue_group(
        _runner(mq_conn),
        _feature(feature_id),
        dag_sha256=_DAG,
        group_idx=_GROUP,
        expected_task_ids=["TASK-1"],
    )

    assert result.checkpointed is False
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "checkpoint_contradiction"
    # No checkpoint projection — the group never checkpointed.
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is None


# ── fail closed: no silent fallback to the legacy checkpoint ─────────────────


@pytest.mark.asyncio
async def test_checkpoint_fails_closed_without_a_typed_store() -> None:
    """The checkpoint fails closed (no legacy checkpoint) without a store.

    A runner with no execution-control store cannot reach the durable queue
    coordinator. ``_checkpoint_durable_merge_queue_group`` raises
    ``_MergeQueueEnqueueError`` — it never silently falls back to
    ``_commit_group`` / a direct checkpoint.
    """
    runner = SimpleNamespace(services={})
    with pytest.raises(impl._MergeQueueEnqueueError, match="typed execution"):
        await impl._checkpoint_durable_merge_queue_group(
            runner,
            _feature("feat-no-store"),
            dag_sha256=_DAG,
            group_idx=_GROUP,
            expected_task_ids=["TASK-1"],
        )


# ── 08e-3b P1 remediation: the queue checkpoint body must pass the legacy ────
#    `dag-group:*` resume freshness gate + the post-test guard.
#
# The 6 tests above exercise the checkpoint helpers in isolation. They never
# touch the `_implement_dag` resume reconstruction loop or the post-test guard
# — which is exactly why the P1 (`_reconstruct_checkpoint_body` emitted
# `results: []`, and the queue path wrote no legacy `dag-group-commit-proof:*`
# / `dag-checkpoint-gate-proof:*` artifacts, so `_dag_group_checkpoint_is_fresh`
# rejected every queue-checkpointed group as stale) shipped undetected. The
# tests below are the regression net: they drive the REAL freshness predicate
# and the REAL `_implement_dag` resume call site against a queue-projected
# `dag-group:*` body.


class _PgArtifacts:
    """A minimal `runner.artifacts` over the merge-queue test connection.

    `_dag_group_checkpoint_is_fresh` (the resume freshness gate + the post-test
    guard) reads `dag-group:*` and the legacy proof artifacts via
    `runner.artifacts.get`. The queue checkpoint writes the `dag-group:*`
    projection straight into the Postgres `artifacts` table, so the resume gate
    must read that same table — this adapter does, mirroring
    `storage.artifacts.PostgresArtifactStore.get`.
    """

    def __init__(self, conn) -> None:
        self._conn = conn

    async def get(self, key: str, *, feature):
        row = await self._conn.fetchrow(
            "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
            "ORDER BY id DESC LIMIT 1",
            feature.id,
            key,
        )
        return None if row is None else row["value"]

    async def put(self, key: str, value, *, feature) -> None:
        await self._conn.execute(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1, $2, $3)",
            feature.id,
            key,
            str(value),
        )

    async def delete(self, key: str, *, feature) -> None:
        await self._conn.execute(
            "DELETE FROM artifacts WHERE feature_id = $1 AND key = $2",
            feature.id,
            key,
        )


def _build_feature_workspace(base: Path, slug: str) -> tuple[Path, Path, str]:
    """A real feature workspace with one canonical git repo under it.

    Returns `(workspace_base, canonical_repo, base_commit)`. The canonical repo
    lives at `{base}/.iriai/features/{slug}/repos/app` — the exact layout
    `implementation._get_feature_root` / `_discover_repo_roots_under` resolve,
    so the resume freshness gate's clean-repo / current-heads checks see it.
    """
    repos_root = base / ".iriai" / "features" / slug / "repos"
    repo = repos_root / "app"
    base_commit = _init_repo(repo)
    return base, repo, base_commit


def _workspace_runner(conn, base: Path) -> SimpleNamespace:
    """A runner with the typed store, a Postgres artifacts store, and a
    workspace manager rooted at *base* (so `_get_feature_root` resolves).

    `_base` is a `Path` — production's workspace manager exposes a `Path`, and
    `_ensure_task_worktrees` does `workspace_root / ...` on it directly.
    """
    return SimpleNamespace(
        artifacts=_PgArtifacts(conn),
        services={
            "execution_control_store": ExecutionControlStore(conn),
            "workspace_manager": SimpleNamespace(_base=Path(base)),
        },
    )


@pytest.mark.asyncio
async def test_queue_checkpoint_body_passes_the_legacy_resume_freshness_gate(
    mq_conn, tmp_path: Path
) -> None:
    """A queue-projected `dag-group:*` is fresh to the legacy resume gate.

    Drives the full queue path — enqueue (08e-2) -> drain (08e-3a) ->
    checkpoint (08e-3b) — against a canonical repo inside a real feature
    workspace, then calls `_dag_group_checkpoint_is_fresh` (THE predicate the
    `_implement_dag` resume reconstruction loop AND `post_test_observation`'s
    post-test guard both call) over the projected `dag-group:1` body.

    This MUST fail against the pre-P1-fix code: that code's
    `_reconstruct_checkpoint_body` emitted `results: []`, so
    `_checkpoint_results_match_tasks` rejected the body, and the queue path
    wrote no legacy proof artifacts so the freshness gate had no fallback. It
    passes only with the P1 fix (the body now carries the per-task
    `ImplementationResult` dumps, and the gate's new branch validates the
    typed `done` merge-queue lanes).
    """
    feature_id = "feat-ckpt-resume-gate"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "resume gate line\n")

    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
    )
    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)

    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    assert len(drained) == 1 and drained[0].integrated is True

    # The per-task ImplementationResult the `_implement_dag` call site passes.
    task_results = [
        impl.ImplementationResult(
            task_id="TASK-1", summary="did the work", status="completed"
        )
    ]
    result = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        expected_task_ids=["TASK-1"], task_results=task_results,
    )
    assert result.checkpointed is True

    # The projected `dag-group:1` body — the exact bytes the resume loop reads.
    body_raw = await runner.artifacts.get("dag-group:1", feature=feature)
    assert body_raw is not None
    body = json.loads(body_raw)
    # P1 fix: the body now carries one ImplementationResult dump per task id.
    assert [r["task_id"] for r in body["results"]] == ["TASK-1"]

    # THE regression assertion: the legacy resume freshness gate accepts it.
    fresh = await impl._dag_group_checkpoint_is_fresh(
        runner, feature, group_idx=_GROUP, group_task_ids=["TASK-1"],
        dag_sha256=_DAG, checkpoint=body,
    )
    assert fresh is True

    # And the queue-evidence freshness branch itself confirms the typed rows.
    current_heads = impl._current_feature_repo_heads(runner, feature)
    queue_fresh = await impl._dag_group_queue_checkpoint_is_fresh(
        runner, feature, group_idx=_GROUP, group_task_ids=["TASK-1"],
        dag_sha256=_DAG, commit_hash=str(body["commit_hash"]),
        current_heads=current_heads,
    )
    assert queue_fresh is True


@pytest.mark.asyncio
async def test_queue_checkpoint_freshness_ignores_dead_failed_retry_lanes(
    mq_conn, tmp_path: Path
) -> None:
    """A sealed group with DEAD `failed` retry-chain lanes is still fresh.

    A commit_hygiene recovery leaves a chain of `failed` lanes superseded by the
    `done` replacement that finally landed. The resume freshness gate
    (`_dag_group_queue_checkpoint_is_fresh`) must IGNORE those dead lanes —
    otherwise a validly-sealed group is judged "stale or lacks durable proof"
    and re-run forever, but its `done`-replaced lanes can no longer be
    re-enqueued (their retry sources already have a `done` replacement). Mirrors
    feature 8ac124d6 group 78, where 7 failed retry lanes (5,7,9,10,11,13,14)
    blocked the seal even though all four tasks were `done`.
    """
    feature_id = "feat-ckpt-dead-failed"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "dead failed lane line\n")
    # One contract shared by both lanes (a retry reuses its source's contract).
    contract = await _insert_contract(
        mq_conn, feature_id, "TASK-1",
        allowed_paths=[{"repo_id": "app", "path": "README.md", "match_kind": "file"}],
    )

    # The original lane that FAILED commit_hygiene — the dead retry-chain root.
    failed_lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
        head_commit="h-orig", contract=contract,
    )
    await mq_conn.execute(
        "UPDATE merge_queue_items SET status='failed' WHERE id=$1", failed_lane,
    )
    # The recovery retry that lands: enqueued as a retry_of the failed lane,
    # then drained + checkpointed to `done`.
    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
        retry_of=failed_lane, contract=contract,
    )
    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)

    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    assert any(d.integrated for d in drained)

    task_results = [
        impl.ImplementationResult(
            task_id="TASK-1", summary="recovered the work", status="completed"
        )
    ]
    result = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        expected_task_ids=["TASK-1"], task_results=task_results,
    )
    assert result.checkpointed is True

    body = json.loads(await runner.artifacts.get("dag-group:1", feature=feature))
    current_heads = impl._current_feature_repo_heads(runner, feature)
    # THE regression assertion: the dead `failed` lane does NOT make the
    # otherwise fully-`done` group stale.
    queue_fresh = await impl._dag_group_queue_checkpoint_is_fresh(
        runner, feature, group_idx=_GROUP, group_task_ids=["TASK-1"],
        dag_sha256=_DAG, commit_hash=str(body["commit_hash"]),
        current_heads=current_heads,
    )
    assert queue_fresh is True


@pytest.mark.asyncio
async def test_pre_p1_fix_empty_results_body_is_rejected_by_the_freshness_gate(
    mq_conn, tmp_path: Path
) -> None:
    """The pre-P1-fix body shape (`results: []`) is rejected as stale.

    This pins the exact P1: a `dag-group:*` body with an empty `results` list
    fails `_dag_group_checkpoint_is_fresh` (via `_checkpoint_results_match_tasks`,
    which hard-requires one `ImplementationResult` per task id). It documents
    why the queue checkpoint had to populate `results`, and proves the
    freshness gate is genuinely the gate that rejected the old body — so the
    test above is a real regression, not a tautology.
    """
    feature_id = "feat-ckpt-empty-results"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "empty results line\n")

    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
    )
    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)
    await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    result = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        expected_task_ids=["TASK-1"],
        task_results=[
            impl.ImplementationResult(task_id="TASK-1", summary="x")
        ],
    )
    assert result.checkpointed is True
    body = json.loads(await runner.artifacts.get("dag-group:1", feature=feature))

    # The genuine post-P1-fix body is fresh.
    assert await impl._dag_group_checkpoint_is_fresh(
        runner, feature, group_idx=_GROUP, group_task_ids=["TASK-1"],
        dag_sha256=_DAG, checkpoint=body,
    ) is True
    # The pre-P1-fix body shape (results stripped) is rejected as stale.
    stale_body = {**body, "results": []}
    assert await impl._dag_group_checkpoint_is_fresh(
        runner, feature, group_idx=_GROUP, group_task_ids=["TASK-1"],
        dag_sha256=_DAG, checkpoint=stale_body,
    ) is False


@pytest.mark.asyncio
async def test_implement_dag_resume_skips_a_queue_checkpointed_group(
    mq_conn, tmp_path: Path
) -> None:
    """`_implement_dag` resume skips a fully queue-checkpointed group.

    Exercises the REAL `_implement_dag` resume reconstruction loop (not a
    helper): a single-group sandbox/queue feature whose group was drained +
    checkpointed by the durable queue is RESUMED. The resume loop must judge
    the queue-projected `dag-group:0` checkpoint fresh, advance `start_group`
    past it, and reach `terminal_state == "complete"` WITHOUT re-dispatching
    the task or returning `workflow_blocked`.

    Pre-P1-fix this returns `workflow_blocked`: the resume loop judged the
    `results: []` body stale and `break`d without advancing `start_group`, the
    per-task resume block then saw the surviving `dag-task:` marker and
    re-blocked the feature. This test passes only with the P1 fix.
    """
    feature_id = "feat-ckpt-resume-dag"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "resumed group line\n")

    # Build the one-group DAG; its sha is what the queue lanes must cite.
    from iriai_build_v2.models.outputs import ImplementationDAG, ImplementationTask

    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-1", name="Task one", description="do task one",
                files=["app/feature.py"],
            )
        ],
        execution_order=[["TASK-1"]],
        complete=True,
    )
    dag_sha = __import__("hashlib").sha256(
        dag.model_dump_json().encode("utf-8")
    ).hexdigest()

    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)

    # Enqueue + drain + checkpoint the group under the DAG's real sha.
    await _enqueue_drainable_lane_for_dag(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
        dag_sha256=dag_sha, group_idx=0,
    )
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=dag_sha
    )
    assert len(drained) == 1 and drained[0].integrated is True
    checkpoint = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=dag_sha, group_idx=0,
        expected_task_ids=["TASK-1"],
        task_results=[
            impl.ImplementationResult(
                task_id="TASK-1", summary="implemented task one",
                status="completed",
            )
        ],
    )
    assert checkpoint.checkpointed is True

    # A stray `dag-task:TASK-1` marker the dispatcher would have written — the
    # pre-fix per-task resume block re-blocks on it when the group is judged
    # stale; the post-fix loop skips the whole group before reaching it.
    await runner.artifacts.put(
        "dag-task:TASK-1",
        impl.ImplementationResult(
            task_id="TASK-1", summary="implemented task one", status="completed"
        ).model_dump_json(),
        feature=feature,
    )

    # RESUME: drive the real `_implement_dag`. The checkpointed group must be
    # skipped — the runtime is never dispatched.
    runner.run = _no_dispatch  # any dispatch is a hard failure
    outcome = await impl._implement_dag(runner, feature, dag)

    assert outcome.terminal_state == "complete", outcome.failure
    assert outcome.failure == ""


async def _no_dispatch(*args, **kwargs):  # noqa: ANN002, ANN003
    raise AssertionError(
        "a resume past a queue-checkpointed group must not dispatch the task"
    )


async def _enqueue_drainable_lane_for_dag(
    conn,
    feature_id: str,
    *,
    task_id: str,
    repo_id: str,
    repo_path: Path,
    base_commit: str,
    patch_text: str,
    dag_sha256: str,
    group_idx: int,
    allowed_file: str = "README.md",
) -> int:
    """Like `_enqueue_drainable_lane` but for an arbitrary dag sha / group.

    The `_implement_dag` resume test needs the queue lane to cite the REAL
    DAG digest and group index `_implement_dag` computes, not the module
    `_DAG` / `_GROUP` constants.
    """
    contract = await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status, allowed_paths) "
        "VALUES ($1, $2, $3, $4, $5, $6, 'active', $7::jsonb) RETURNING id",
        feature_id,
        f"contract:{feature_id}:{task_id}",
        dag_sha256,
        group_idx,
        task_id,
        f"cd-{task_id}",
        json.dumps([{"repo_id": repo_id, "path": allowed_file, "match_kind": "file"}]),
    )
    diff_artifact = await _insert_artifact(
        conn, feature_id, f"dag-sandbox-diff:{task_id}", patch_text
    )
    patch_evidence = await _insert_patch_evidence(
        conn, feature_id, repo_id=repo_id, diff_artifact_id=diff_artifact
    )
    gate = await _insert_gate_evidence(conn, feature_id)
    store = MergeQueueStore(conn)
    item = await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=dag_sha256,
            group_idx=group_idx,
            base_commit=base_commit,
            repo_id=repo_id,
            repo_path=str(repo_path),
            head_commit="",
            integration_lane=f"task:{task_id}",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[patch_evidence],
            gate_evidence_ids=[gate],
            task_coverage=[TaskCoverageCreate(task_id=task_id, contract_id=contract)],
            repo_targets=[
                RepoTargetCreate(
                    repo_id=repo_id, repo_path=str(repo_path),
                    base_commit=base_commit,
                )
            ],
            payload={"stage": "implementation", "task_ids": [task_id]},
        )
    )
    return item.id


@pytest.mark.asyncio
async def test_post_test_guard_accepts_a_queue_projected_checkpoint(
    mq_conn, tmp_path: Path
) -> None:
    """The post-test guard does not quiesce on a queue-projected checkpoint.

    `post_test_observation`'s post-test guard iterates every `dag-group:*` and
    calls the SAME `_dag_group_checkpoint_is_fresh`. Pre-P1-fix it marked every
    queue-checkpointed group stale (`results: []`) and quiesced the feature
    with `post_test_blocked_dag_checkpoint_stale`. This test reproduces the
    guard's loop over a real queue-projected `dag-group:1` body and asserts the
    group is NOT flagged stale.
    """
    feature_id = "feat-ckpt-posttest"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "post-test line\n")

    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
    )
    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)
    await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    result = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        expected_task_ids=["TASK-1"],
        task_results=[
            impl.ImplementationResult(
                task_id="TASK-1", summary="post-test work", status="completed"
            )
        ],
    )
    assert result.checkpointed is True

    # The post-test guard's exact freshness loop (post_test_observation.py:
    # 394-418): iterate the DAG groups, collect the stale ones.
    execution_order = [["IGNORED-0"], ["TASK-1"]]  # group 1 == _GROUP
    stale_groups: list[int] = []
    for group_idx, group_task_ids in enumerate(execution_order):
        checkpoint_raw = await runner.artifacts.get(
            f"dag-group:{group_idx}", feature=feature
        )
        try:
            checkpoint = json.loads(checkpoint_raw)
        except Exception:
            checkpoint = {}
        if not await impl._dag_group_checkpoint_is_fresh(
            runner, feature, group_idx=group_idx,
            group_task_ids=list(group_task_ids), dag_sha256=_DAG,
            checkpoint=checkpoint,
        ):
            stale_groups.append(group_idx)

    # Group 0 has no checkpoint at all (genuinely stale); the queue-checkpointed
    # group 1 must NOT be flagged — that is the P1 regression for the guard.
    assert _GROUP not in stale_groups
    assert stale_groups == [0]


# ── 08e-3b REMEDIATION 2: a NON-lexically-sorted multi-task group. ────────────
#
# The 7 tests above all use lexically-sorted groups (`["TASK-1"]`,
# `["TASK-A","TASK-B"]` with `A` < `B`) — which is exactly why they were blind
# to the residual P1: the legacy resume freshness gate `_dag_group_checkpoint_
# is_fresh` compares the queue checkpoint body's `task_ids` ORDER-SENSITIVELY
# against `dag.execution_order[g]` (wave order, NOT lexical), and the queue
# body's `task_ids` was built from `coverage.expected_task_ids` = `sorted(...)`.
# For a group whose DAG order is NOT its sorted order the body was lexically
# ordered, the gate rejected it as stale, and the resume / post-test guard
# re-blocked it — the ORIGINAL P1 symptom recurred. The test below uses a group
# whose DAG order `["UI-C", "BACKEND-D"]` differs from `sorted(...)` =
# `["BACKEND-D", "UI-C"]`, drives the REAL `_implement_dag` resume past it, and
# reproduces the post-test guard's freshness loop over the same projected body.


def _build_two_repo_feature_workspace(
    base: Path, slug: str
) -> tuple[Path, Path, str, Path, str]:
    """A real feature workspace with TWO canonical git repos under it.

    Returns `(workspace_base, repo_ui, base_ui, repo_backend, base_backend)`.
    The repos live at `{base}/.iriai/features/{slug}/repos/{ui,backend}` — the
    layout `_get_feature_root` / `_discover_repo_roots_under` resolve, so the
    resume freshness gate's clean-repo / current-heads checks see both. A
    multi-repo group is what makes the queue checkpoint body's `commit_hash`
    and `task_ids` carry more than one entry — the order-sensitive `task_ids`
    comparison only bites when the group has >= 2 tasks.
    """
    repos_root = base / ".iriai" / "features" / slug / "repos"
    repo_ui = repos_root / "ui"
    repo_backend = repos_root / "backend"
    base_ui = _init_repo(repo_ui)
    base_backend = _init_repo(repo_backend)
    return base, repo_ui, base_ui, repo_backend, base_backend


@pytest.mark.asyncio
async def test_implement_dag_resume_skips_a_non_lexically_sorted_queue_group(
    mq_conn, tmp_path: Path
) -> None:
    """`_implement_dag` resume + the post-test guard accept a queue checkpoint
    of a group whose DAG order is NOT its lexical sort order.

    THE residual-P1 regression test. The group's DAG/wave order is
    `["UI-C", "BACKEND-D"]`; `sorted(...)` is `["BACKEND-D", "UI-C"]` — they
    differ. The 08e-3b REMEDIATION 2 fix threads the DAG order
    (`dag_ordered_task_ids`, what the production `if pending_merge_queue:` call
    site passes as `list(group)`) into the projected `dag-group:*` body's
    `task_ids`, so the legacy resume freshness gate `_dag_group_checkpoint_is_
    fresh` — which compares the body's `task_ids` ORDER-SENSITIVELY against
    `dag.execution_order[g]` — accepts it.

    Pre-fix (the first-remediation code, before REMEDIATION 2) this FAILS: the
    body's `task_ids` was built from `coverage.expected_task_ids` = `sorted(
    expected)` = `["BACKEND-D", "UI-C"]`, so `list(checkpoint["task_ids"]) !=
    list(group_task_ids)` was `True` at `implementation.py` ~19521,
    `_dag_group_checkpoint_is_fresh` returned `False`, the `_implement_dag`
    resume loop logged "checkpoint marker is stale" and `break`d WITHOUT
    advancing `start_group`, the per-task resume block then re-blocked the
    feature `workflow_blocked`, and the post-test guard flagged the group
    stale. Confirmed failing against a pre-fix simulation (see the journal).
    It passes only with the DAG-order body fix.
    """
    feature_id = "feat-ckpt-nonlexical"
    await _insert_feature(mq_conn, feature_id)
    base, repo_ui, base_ui, repo_backend, base_backend = (
        _build_two_repo_feature_workspace(tmp_path, feature_id)
    )
    patch_ui = _diff_for_appended_line(repo_ui, "ui-c work\n")
    patch_backend = _diff_for_appended_line(repo_backend, "backend-d work\n")

    from iriai_build_v2.models.outputs import (
        ImplementationDAG,
        ImplementationTask,
    )

    # The single group's DAG order is ["UI-C", "BACKEND-D"] — declaring UI-C
    # first puts it first in the wave. `sorted(...)` would be
    # ["BACKEND-D", "UI-C"]; the two orders DIFFER, which is the whole point.
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="UI-C", name="UI task C", description="do the UI work",
                files=["ui/component.py"],
            ),
            ImplementationTask(
                id="BACKEND-D", name="Backend task D",
                description="do the backend work",
                files=["backend/service.py"],
            ),
        ],
        execution_order=[["UI-C", "BACKEND-D"]],
        complete=True,
    )
    dag_sha = __import__("hashlib").sha256(
        dag.model_dump_json().encode("utf-8")
    ).hexdigest()
    # Guard the premise: the DAG order is genuinely not the sorted order.
    assert list(dag.execution_order[0]) == ["UI-C", "BACKEND-D"]
    assert sorted(dag.execution_order[0]) == ["BACKEND-D", "UI-C"]
    assert list(dag.execution_order[0]) != sorted(dag.execution_order[0])

    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)

    # Enqueue both lanes under the DAG's real sha / group 0, then drain them.
    await _enqueue_drainable_lane_for_dag(
        mq_conn, feature_id, task_id="UI-C", repo_id="ui",
        repo_path=repo_ui, base_commit=base_ui, patch_text=patch_ui,
        dag_sha256=dag_sha, group_idx=0,
    )
    await _enqueue_drainable_lane_for_dag(
        mq_conn, feature_id, task_id="BACKEND-D", repo_id="backend",
        repo_path=repo_backend, base_commit=base_backend,
        patch_text=patch_backend, dag_sha256=dag_sha, group_idx=0,
    )
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=dag_sha
    )
    assert len(drained) == 2 and all(r.integrated for r in drained)

    # Checkpoint the group EXACTLY as the production `if pending_merge_queue:`
    # call site does — threading `dag_ordered_task_ids=list(group)` (the DAG
    # order). `expected_task_ids` is the in-memory `pending_merge_queue` order.
    checkpoint = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=dag_sha, group_idx=0,
        expected_task_ids=["UI-C", "BACKEND-D"],
        task_results=[
            impl.ImplementationResult(
                task_id="UI-C", summary="implemented UI-C", status="completed",
            ),
            impl.ImplementationResult(
                task_id="BACKEND-D", summary="implemented BACKEND-D",
                status="completed",
            ),
        ],
        dag_ordered_task_ids=list(dag.execution_order[0]),
    )
    assert checkpoint.checkpointed is True

    # THE fix assertion: the projected `dag-group:0` body's `task_ids` carries
    # the DAG/wave order — NOT the lexical sort. Pre-fix this was
    # ["BACKEND-D", "UI-C"] (sorted) and every check below failed.
    body = json.loads(await runner.artifacts.get("dag-group:0", feature=feature))
    assert body["task_ids"] == ["UI-C", "BACKEND-D"]
    assert body["task_ids"] != sorted(body["task_ids"])
    # `results` still covers both tasks (set-membership — order-insensitive).
    assert sorted(r["task_id"] for r in body["results"]) == [
        "BACKEND-D", "UI-C",
    ]

    # The legacy resume freshness gate — ORDER-SENSITIVE on `task_ids` — accepts
    # the body now that `task_ids` matches `dag.execution_order[0]`.
    assert await impl._dag_group_checkpoint_is_fresh(
        runner, feature, group_idx=0,
        group_task_ids=list(dag.execution_order[0]), dag_sha256=dag_sha,
        checkpoint=body,
    ) is True

    # A stray `dag-task:*` marker per task — the pre-fix per-task resume block
    # re-blocks on these when the group is judged stale.
    for tid in ("UI-C", "BACKEND-D"):
        await runner.artifacts.put(
            f"dag-task:{tid}",
            impl.ImplementationResult(
                task_id=tid, summary=f"implemented {tid}", status="completed",
            ).model_dump_json(),
            feature=feature,
        )

    # RESUME: drive the REAL `_implement_dag`. The non-lexically-sorted
    # queue-checkpointed group must be skipped — no task is ever dispatched.
    runner.run = _no_dispatch
    outcome = await impl._implement_dag(runner, feature, dag)
    assert outcome.terminal_state == "complete", outcome.failure
    assert outcome.failure == ""

    # POST-TEST GUARD: reproduce its exact freshness loop (post_test_
    # observation.py:394-418) over the queue-projected body. The group must NOT
    # be flagged stale — pre-fix it was (`post_test_blocked_dag_checkpoint_
    # stale`).
    stale_groups: list[int] = []
    for group_idx, group_task_ids in enumerate(dag.execution_order):
        checkpoint_raw = await runner.artifacts.get(
            f"dag-group:{group_idx}", feature=feature
        )
        try:
            guard_checkpoint = json.loads(checkpoint_raw)
        except Exception:
            guard_checkpoint = {}
        if not await impl._dag_group_checkpoint_is_fresh(
            runner, feature, group_idx=group_idx,
            group_task_ids=list(group_task_ids), dag_sha256=dag_sha,
            checkpoint=guard_checkpoint,
        ):
            stale_groups.append(group_idx)
    assert stale_groups == []


# ── 08e-3b P2-a: resume re-drives the idempotent checkpoint of an `integrated`
#    (drain-succeeded, checkpoint-crashed) group instead of re-blocking.
#
# `_resume_recover_durable_merge_queue_group` IS the whole P2-a recovery: the
# per-task resume block in `_implement_dag` calls it whenever a pending
# `dag-task-pending-merge:*` marker is present, and on `recovered=True` the
# block advances the group instead of returning `workflow_blocked`. These
# tests drive that recovery function directly against real Postgres.


@pytest.mark.asyncio
async def test_resume_recovery_redrives_a_drained_uncheckpointed_group(
    mq_conn, tmp_path: Path
) -> None:
    """Resume recovery completes a drained-but-uncheckpointed group (vector 6).

    The 08e-3b `if pending_merge_queue:` block enqueued + drained the group's
    lanes (now `integrated`) but `_checkpoint_durable_merge_queue_group` then
    crashed — NO `dag-group:*` projection exists, the lanes are `integrated`
    (not `done`), and the per-task `dag-task-pending-merge:*` markers survive.
    On resume the per-task block must NOT just re-block on the surviving marker
    (the 08e-3b P2 finding): it calls `_resume_recover_durable_merge_queue_
    group`, which re-drives the IDEMPOTENT drain + checkpoint. The drain is a
    no-op here (the `integrated` lanes cannot be re-claimed — no double-apply),
    the checkpoint completes the group, and the group becomes `done`.
    """
    feature_id = "feat-ckpt-resume-redrive"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "redrive line\n")

    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)

    # Enqueue + drain ONLY — the lane reaches `integrated`; NO checkpoint runs
    # (simulating a crash between drain success and the group checkpoint).
    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
    )
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    assert len(drained) == 1 and drained[0].integrated is True
    # The lane is `integrated`, NOT `done` — the checkpoint never ran.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "integrated"
    assert await _dag_group_projection(mq_conn, feature_id, _GROUP) is None

    # The surviving per-task markers the `if pending_merge_queue:` block wrote:
    # the `dag-task:*` attempt marker (carrying the durable merge-queue note,
    # which `_resume_recover_durable_merge_queue_group` reconstructs the
    # checkpoint `results` from) and the `dag-task-pending-merge:*` marker.
    await runner.artifacts.put(
        "dag-task:TASK-1",
        impl.ImplementationResult(
            task_id="TASK-1", summary="implemented task one", status="completed",
            notes="canonical_mutation=pending_durable_merge_queue",
        ).model_dump_json(),
        feature=feature,
    )
    await runner.artifacts.put(
        "dag-task-pending-merge:TASK-1",
        "canonical_mutation=pending_durable_merge_queue",
        feature=feature,
    )

    # The P2-a resume recovery: re-drive the idempotent drain + checkpoint.
    recovery = await impl._resume_recover_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        group_task_ids=["TASK-1"],
    )

    assert recovery.recovered is True
    assert lane in recovery.done_queue_item_ids
    assert recovery.result_commit

    # The group is now checkpointed: the lane is `done` and `dag-group:1` exists
    # with the per-task results the legacy resume freshness gate requires.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "done"
    projection = await _dag_group_projection(mq_conn, feature_id, _GROUP)
    assert projection is not None
    assert projection["projection_owner"] == "merge_queue"
    body = json.loads(await runner.artifacts.get("dag-group:1", feature=feature))
    assert [r["task_id"] for r in body["results"]] == ["TASK-1"]
    # The recovered checkpoint is fresh to the legacy resume gate.
    assert await impl._dag_group_checkpoint_is_fresh(
        runner, feature, group_idx=_GROUP, group_task_ids=["TASK-1"],
        dag_sha256=_DAG, checkpoint=body,
    ) is True


@pytest.mark.asyncio
async def test_resume_recovery_is_idempotent_on_an_already_checkpointed_group(
    mq_conn, tmp_path: Path
) -> None:
    """Resume recovery of an already-`done` group is a no-op success.

    If the drain AND the checkpoint already completed (the group is `done`)
    before the crash, a resume that still sees a stale pending marker re-drives
    `_resume_recover_durable_merge_queue_group` — which must be an idempotent
    no-op success (the drain claims nothing, `checkpoint_group` detects the
    already-checkpointed group), never a re-apply or a double-projection.
    """
    feature_id = "feat-ckpt-resume-idem"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "resume idem line\n")

    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)
    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
    )
    await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    first = await impl._checkpoint_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        expected_task_ids=["TASK-1"],
        task_results=[
            impl.ImplementationResult(task_id="TASK-1", summary="x")
        ],
    )
    assert first.checkpointed is True

    await runner.artifacts.put(
        "dag-task:TASK-1",
        impl.ImplementationResult(
            task_id="TASK-1", summary="x", status="completed",
            notes="canonical_mutation=pending_durable_merge_queue",
        ).model_dump_json(),
        feature=feature,
    )

    # Re-drive the recovery against the already-`done` group — idempotent.
    recovery = await impl._resume_recover_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        group_task_ids=["TASK-1"],
    )
    assert recovery.recovered is True
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "done"
    # Still exactly one `dag-group:1` projection (no double-projection).
    rows = await mq_conn.fetch(
        "SELECT id FROM execution_artifact_projections "
        "WHERE feature_id = $1 AND projection_key = 'dag-group:1'",
        feature_id,
    )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_resume_recovery_fails_closed_on_a_failed_lane(
    mq_conn, tmp_path: Path
) -> None:
    """Resume recovery fails closed when a lane cannot integrate.

    If the queued patch is stale (its base diverged), the re-driven drain fails
    the lane closed (`merge_conflict`). `_resume_recover_durable_merge_queue_
    group` returns `recovered=False` — the `_implement_dag` per-task resume
    block then falls through to the existing fail-closed `workflow_blocked`
    path. It NEVER falls back to the legacy `_commit_group` / direct checkpoint,
    and no `dag-group:*` is projected.
    """
    feature_id = "feat-ckpt-resume-failclosed"
    await _insert_feature(mq_conn, feature_id)
    base, repo, base_commit = _build_feature_workspace(tmp_path, feature_id)
    patch = _diff_for_appended_line(repo, "stale resume patch\n")

    runner = _workspace_runner(mq_conn, base)
    feature = _feature(feature_id)
    await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base_commit, patch_text=patch,
    )
    # Diverge the canonical repo so the queued patch no longer applies.
    (repo / "README.md").write_text("totally different\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "diverge")

    await runner.artifacts.put(
        "dag-task:TASK-1",
        impl.ImplementationResult(
            task_id="TASK-1", summary="x", status="completed",
            notes="canonical_mutation=pending_durable_merge_queue",
        ).model_dump_json(),
        feature=feature,
    )

    recovery = await impl._resume_recover_durable_merge_queue_group(
        runner, feature, dag_sha256=_DAG, group_idx=_GROUP,
        group_task_ids=["TASK-1"],
    )
    assert recovery.recovered is False
    assert "could not integrate" in recovery.detail
    # No checkpoint projection — the recovery failed closed.
    assert await _dag_group_projection(mq_conn, feature_id, _GROUP) is None
