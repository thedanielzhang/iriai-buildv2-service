"""Slice 08e-3a — durable merge queue DRAIN worker.

Integration tests for ``implementation._drain_durable_merge_queue_for_feature``
— the worker that drains the ``task:{id}`` lanes the 08e-2 enqueue produces.
08e-2 enqueues lanes (status ``queued``) and halts the workflow; the drain
claims each lane and runs it through the Slice 08d ``MergeQueue`` worker
(``apply_candidate -> run_required_gates -> commit_and_prove_clean ->
mark_integrated``) so the durable queue actually applies the sandbox patch to
the canonical repo, commits, and leaves the lane ``integrated``.

These tests use a real Postgres queue database (the ``mq_conn`` fixture from
this directory's conftest) and a real on-disk git repo for the canonical
repo / base commit. They skip when no Postgres is reachable. Coverage:

* a clean lane drains to ``integrated`` with a real canonical commit;
* a conflicting patch fails closed and routes a typed ``merge_conflict``
  failure through the Slice 07 failure router;
* a commit-hook rejection fails closed and routes ``commit_hygiene``;
* a sibling lane's failure does not strand the other lanes;
* a *raised* worker exception fails only its lane and the drain loop still
  drains the clean siblings (08e-3a remediation P2-1);
* the drain fails closed (no legacy commit fallback) without a store;
* the deterministic post-apply ``diff --check HEAD`` gate rejects a STAGED
  conflict marker (08e-3a remediation P2-2);
* a lane crashed mid-canonical-apply (stuck ``applying`` with an expired
  lease) is recovered via ``recover_expired`` and driven to a fail-closed
  terminal on a drain re-run (08e-3a remediation P2-3).
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
    MergeQueueError as MergeQueueStoreError,
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoTargetCreate,
    TaskCoverageCreate,
)
from iriai_build_v2.workflows.develop.execution import git_service
from iriai_build_v2.workflows.develop.phases import implementation as impl

_DAG = "dag-sha"


# ── git + DB staging helpers (mirror test_merge_queue_wiring.py) ─────────────


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
) -> int:
    """Enqueue one ``queued`` ``task:{id}`` lane the drain can claim.

    Mirrors what ``_enqueue_durable_merge_queue_for_results`` (08e-2) writes:
    a per-task lane with a real ``sandbox_patch_summary`` patch evidence node,
    a diff artifact, an active contract scoped to *allowed_file*, a pre-queue
    aggregate_verdict gate node, one task-coverage row, and one repo target.
    """
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
            group_idx=1,
            base_commit=base_commit,
            repo_id=repo_id,
            repo_path=str(repo_path),
            head_commit="",
            integration_lane=f"task:{task_id}",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[patch_evidence],
            gate_evidence_ids=[gate],
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


# ── happy path: a clean lane drains to integrated ───────────────────────────


@pytest.mark.asyncio
async def test_drain_advances_a_clean_lane_to_integrated(
    mq_conn, tmp_path: Path
) -> None:
    """The drain claims a queued lane and drives it to ``integrated``.

    A clean sandbox patch is applied to the canonical repo, the post-apply
    gate approves, the lane commits + proves clean, and ends ``integrated``
    with a real canonical commit. The group checkpoint (integrated -> done)
    is 08e-3b — the lane must stop at ``integrated`` here.
    """
    feature_id = "feat-drain-clean"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "drained line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn,
        feature_id,
        task_id="TASK-1",
        repo_id="app",
        repo_path=repo,
        base_commit=base,
        patch_text=patch,
    )

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.item_id == lane
    assert result.integrated is True
    assert result.terminal_status == "integrated"
    assert result.result_commit
    assert result.task_ids == ["TASK-1"]

    # The typed queue lane is authoritative — it is integrated, with proof ids.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None
    assert item.status == "integrated"
    assert item.merge_proof_evidence_id is not None
    assert item.post_apply_gate_evidence_id is not None
    assert item.commit_proof_evidence_id is not None
    assert item.result_commit
    # checkpoint columns stay empty — checkpoint is 08e-3b.
    assert item.checkpoint_projection_id is None
    assert item.repo_targets[0].status == "clean"

    # The canonical repo has a real new commit and is clean.
    head = await git_service.head_commit(repo)
    assert head != base
    assert await git_service.working_tree_clean(repo) is True
    assert (repo / "README.md").read_text() == "init\ndrained line\n"


@pytest.mark.asyncio
async def test_drain_returns_empty_when_no_lanes_are_claimable(
    mq_conn,
) -> None:
    """A feature with no queued lanes drains to an empty result list."""
    feature_id = "feat-drain-empty"
    await _insert_feature(mq_conn, feature_id)
    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )
    assert drained == []


@pytest.mark.asyncio
async def test_drain_drains_multiple_lanes_for_one_feature(
    mq_conn, tmp_path: Path
) -> None:
    """The drain loop claims and integrates every queued lane of a feature."""
    feature_id = "feat-drain-multi"
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

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    assert {r.item_id for r in drained} == {lane_a, lane_b}
    assert all(r.integrated for r in drained)
    store = MergeQueueStore(mq_conn)
    for lane in (lane_a, lane_b):
        item = await store.get(lane)
        assert item is not None and item.status == "integrated"
    assert await git_service.head_commit(repo_a) != base_a
    assert await git_service.head_commit(repo_b) != base_b


# ── failure routing: a conflicting patch fails closed + routes typed ────────


@pytest.mark.asyncio
async def test_drain_routes_a_merge_conflict_through_the_failure_router(
    mq_conn, tmp_path: Path
) -> None:
    """A non-applying patch fails the lane closed and routes a typed failure.

    The canonical repo HEAD advances past the patch's base so the patch no
    longer applies (the queued patch context is stale). ``apply_candidate``
    records a typed ``merge_conflict`` and leaves the lane ``failed``; the
    drain routes that through the Slice 07 failure router. The legacy commit
    is NEVER used as a fallback.
    """
    feature_id = "feat-drain-conflict"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    # A patch whose context line is the original README content.
    patch = _diff_for_appended_line(repo, "conflicting line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )

    # Mutate the canonical README so the queued patch no longer applies, then
    # commit so the working tree is clean (the apply baseline-clean check
    # passes; the apply itself conflicts).
    (repo / "README.md").write_text("totally different content\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "diverge")
    diverged_head = _git(repo, "rev-parse", "HEAD").strip()

    runner = _runner(mq_conn)
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.integrated is False
    assert result.terminal_status == "failed"
    assert result.failure_class == "merge_conflict"
    # The typed failure was routed through the Slice 07 router.
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "merge_conflict"
    assert result.routed_failure["failure_type"] == "patch_apply_conflict"
    assert result.routed_failure["route_action"] == "retry_merge"
    assert result.routed_failure.get("typed_failure_id")

    # The queue lane is terminal failed; the canonical repo is untouched by
    # the failed apply (still at the diverged commit, clean — NO legacy commit).
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "failed"
    assert await git_service.head_commit(repo) == diverged_head
    assert await git_service.working_tree_clean(repo) is True

    # The router actually recorded a typed failure row.
    router = runner.services["failure_router"]
    record = router.get_failure(result.routed_failure["typed_failure_id"])
    assert record.observation.failure_class == "merge_conflict"
    assert record.observation.source == "merge_queue"


@pytest.mark.asyncio
async def test_drain_routes_a_commit_hook_failure_as_commit_hygiene(
    mq_conn, tmp_path: Path
) -> None:
    """A pre-commit hook rejection fails closed and routes ``commit_hygiene``.

    The patch applies and the post-apply gate approves, but the canonical
    repo has a failing pre-commit hook. ``commit_and_prove_clean`` records a
    typed ``commit_hygiene`` failure and resets the repo (no commit); the
    drain routes it through the failure router. No legacy commit fallback.
    """
    feature_id = "feat-drain-hook"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "hook-blocked line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )
    # Install a pre-commit hook that always rejects.
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'pre-commit rejected' >&2\nexit 1\n")
    hook.chmod(0o755)

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.integrated is False
    assert result.failure_class == "commit_hygiene"
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "commit_hygiene"
    assert result.routed_failure["failure_type"] == "commit_hook_failed"
    assert result.routed_failure["route_action"] == "run_commit_hygiene_repair"

    # The lane is failed; the hook rejection means no commit — repo reset clean.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "failed"
    assert await git_service.head_commit(repo) == base
    assert await git_service.working_tree_clean(repo) is True


@pytest.mark.asyncio
async def test_drain_continues_after_a_failed_sibling_lane(
    mq_conn, tmp_path: Path
) -> None:
    """A failing lane does not strand the other claimable lanes.

    One lane has a stale patch (conflict); a sibling lane is clean. The drain
    must fail-close the conflicting lane (routed) AND still integrate the
    clean sibling — a per-lane failure is not fatal to the drain.
    """
    feature_id = "feat-drain-mixed"
    await _insert_feature(mq_conn, feature_id)
    repo_bad = tmp_path / "app_bad"
    base_bad = _init_repo(repo_bad)
    patch_bad = _diff_for_appended_line(repo_bad, "bad line\n")
    repo_ok = tmp_path / "app_ok"
    base_ok = _init_repo(repo_ok)
    patch_ok = _diff_for_appended_line(repo_ok, "ok line\n")

    lane_bad = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-BAD", repo_id="app_bad",
        repo_path=repo_bad, base_commit=base_bad, patch_text=patch_bad,
    )
    lane_ok = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-OK", repo_id="app_ok",
        repo_path=repo_ok, base_commit=base_ok, patch_text=patch_ok,
    )
    # Diverge the bad repo so its queued patch conflicts.
    (repo_bad / "README.md").write_text("diverged\n")
    _git(repo_bad, "add", "README.md")
    _git(repo_bad, "commit", "-q", "-m", "diverge")

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    by_id = {r.item_id: r for r in drained}
    assert set(by_id) == {lane_bad, lane_ok}
    assert by_id[lane_bad].integrated is False
    assert by_id[lane_bad].failure_class == "merge_conflict"
    assert by_id[lane_bad].routed_failure.get("routed") is True
    # The clean sibling still integrated despite the sibling failure.
    assert by_id[lane_ok].integrated is True

    store = MergeQueueStore(mq_conn)
    assert (await store.get(lane_bad)).status == "failed"
    ok_item = await store.get(lane_ok)
    assert ok_item is not None and ok_item.status == "integrated"
    assert await git_service.head_commit(repo_ok) != base_ok


# ── fail closed: no silent fallback to the legacy commit ────────────────────


@pytest.mark.asyncio
async def test_drain_fails_closed_without_a_typed_store() -> None:
    """The drain fails closed (no legacy commit) when no typed store exists.

    A runner with no execution-control store and no usable pool cannot reach
    the durable queue. The drain raises ``_MergeQueueEnqueueError`` — it never
    silently falls back to ``_commit_group`` / direct commit.
    """
    runner = SimpleNamespace(services={})
    feature = _feature("feat-no-store")
    with pytest.raises(impl._MergeQueueEnqueueError, match="typed execution"):
        await impl._drain_durable_merge_queue_for_feature(
            runner, feature, dag_sha256=_DAG
        )


# ── deterministic post-apply gate ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_post_apply_gate_decision_approves_a_clean_applied_tree(
    tmp_path: Path,
) -> None:
    """The deterministic post-apply gate approves a non-conflicted applied tree."""
    repo = tmp_path / "app"
    _init_repo(repo)
    # An applied-but-uncommitted clean change (no conflict markers).
    (repo / "README.md").write_text("init\nclean applied change\n")
    item = SimpleNamespace(
        repo_targets=[SimpleNamespace(repo_id="app", repo_path=str(repo))]
    )
    decision = await impl._merge_queue_post_apply_gate_decision(item)
    assert decision.approved is True


@pytest.mark.asyncio
async def test_post_apply_gate_decision_rejects_conflict_markers(
    tmp_path: Path,
) -> None:
    """The post-apply gate rejects a STAGED conflict marker (P2-2 remediation).

    ``apply_candidate`` applies via ``git apply --index``, which STAGES the
    applied hunks. A bare ``git diff --check`` (worktree-vs-index) would NOT
    see a conflict marker that lives only in the staged content and would
    falsely approve. The fixed gate runs ``git diff --check HEAD`` (HEAD vs the
    combined staged+unstaged tree), so it catches the staged marker and
    rejects with ``merge_conflict`` so the drain routes it.

    The conflict marker here is fully STAGED (``git add``) and the worktree
    matches the index — exactly the post-``apply_candidate`` state. This test
    asserts both that the buggy bare ``git diff --check`` MISSED this state and
    that the fixed gate CATCHES it.
    """
    repo = tmp_path / "app"
    _init_repo(repo)
    # An applied tree carrying a leftover conflict marker, fully STAGED — the
    # exact index state `git apply --index` leaves behind.
    (repo / "README.md").write_text(
        "init\n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"
    )
    _git(repo, "add", "README.md")

    # The buggy gate (bare `git diff --check`, worktree-vs-index) MISSES a
    # staged-only conflict marker — it reports clean. This is the masked bug.
    buggy = subprocess.run(
        ["git", "diff", "--check"], cwd=repo, capture_output=True, text=True
    )
    assert buggy.returncode == 0, (
        "bare `git diff --check` should miss a staged conflict marker — "
        "this is the P2-2 bug the fixed gate must close"
    )
    # The fixed gate's `git diff --check HEAD` DOES catch the staged marker.
    fixed = subprocess.run(
        ["git", "diff", "--check", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert fixed.returncode != 0

    item = SimpleNamespace(
        repo_targets=[SimpleNamespace(repo_id="app", repo_path=str(repo))]
    )
    decision = await impl._merge_queue_post_apply_gate_decision(item)
    assert decision.approved is False
    assert decision.failure_class == "merge_conflict"
    assert "app" in decision.verdict_payload["rejected_repo_ids"]


@pytest.mark.asyncio
async def test_post_apply_gate_decision_warns_not_fails_on_whitespace_only(
    tmp_path: Path,
) -> None:
    """Whitespace-only `diff --check` findings are WARN, not merge_conflict (M-4).

    An implementer patch carrying trailing whitespace / a blank line at EOF is
    NOT a merge conflict — failing the lane as `merge_conflict` mis-routes the
    retry implementer ("your patch conflicts") and it regenerates the same
    whitespace. The gate must approve, recording the findings in the verdict
    payload.
    """
    repo = tmp_path / "app"
    _init_repo(repo)
    # Staged trailing whitespace + new blank line at EOF — the exact state
    # `git apply --index` leaves behind for a whitespace-carrying patch.
    (repo / "README.md").write_text("init\ntrailing here   \n\n\n")
    _git(repo, "add", "README.md")
    check = subprocess.run(
        ["git", "diff", "--check", "HEAD"], cwd=repo, capture_output=True, text=True
    )
    assert check.returncode != 0, "precondition: --check must flag the whitespace"

    item = SimpleNamespace(
        repo_targets=[SimpleNamespace(repo_id="app", repo_path=str(repo))]
    )
    decision = await impl._merge_queue_post_apply_gate_decision(item)
    assert decision.approved is True
    assert decision.verdict_payload["whitespace_warning_repo_ids"] == ["app"]
    assert "trailing whitespace" in (
        decision.verdict_payload["whitespace_warnings"]["app"]
    )


@pytest.mark.asyncio
async def test_post_apply_gate_decision_still_rejects_conflict_among_whitespace(
    tmp_path: Path,
) -> None:
    """A real conflict marker still rejects even when whitespace findings coexist."""
    repo = tmp_path / "app"
    _init_repo(repo)
    (repo / "README.md").write_text(
        "init\ntrailing here   \n<<<<<<< HEAD\nours\n=======\ntheirs\n>>>>>>> branch\n"
    )
    _git(repo, "add", "README.md")

    item = SimpleNamespace(
        repo_targets=[SimpleNamespace(repo_id="app", repo_path=str(repo))]
    )
    decision = await impl._merge_queue_post_apply_gate_decision(item)
    assert decision.approved is False
    assert decision.failure_class == "merge_conflict"
    assert "app" in decision.verdict_payload["rejected_repo_ids"]


def test_diff_check_findings_whitespace_only_classifier() -> None:
    """The `--check` output classifier fails closed on anything non-whitespace."""
    ws = (
        "a.txt:2: trailing whitespace.\n"
        "+foo   \n"
        "a.txt:3: new blank line at EOF.\n"
    )
    assert impl._diff_check_findings_whitespace_only(ws) is True
    # A leftover conflict marker is never whitespace-only.
    assert impl._diff_check_findings_whitespace_only(
        "a.txt:2: leftover conflict marker\n"
    ) is False
    assert impl._diff_check_findings_whitespace_only(
        ws + "a.txt:5: leftover conflict marker\n"
    ) is False
    # Empty / unparseable output (non-zero exit with no findings) fails closed.
    assert impl._diff_check_findings_whitespace_only("") is False
    assert impl._diff_check_findings_whitespace_only("fatal: bad revision\n") is False


# ── raised worker exception: fails one lane, does not strand siblings ────────


@pytest.mark.asyncio
async def test_drain_continues_after_a_raised_worker_exception(
    mq_conn, tmp_path: Path, monkeypatch
) -> None:
    """A *raised* worker exception fails only its lane (P2-1 remediation).

    The Slice 08d ``MergeQueue`` worker methods catch only their own
    ``_ApplyFailure``; a ``LeaseFencedError`` / ``MergeQueueError`` raised from
    a lease-fenced ``transition`` (e.g. a lane whose gate exceeds the 5-min
    lease TTL) propagates out of the worker method. Before the P2-1 fix that
    raised exception escaped ``_drain_one_merge_queue_lane`` and aborted the
    whole drain loop, stranding every still-``queued`` sibling.

    This test drives a worker whose ``apply_candidate`` *raises* a real
    ``MergeQueueError`` for one designated lane and delegates to the genuine
    worker for the others. The raised lane must end up routed + failed, and the
    clean sibling lane must STILL integrate — the loop continued.
    """
    feature_id = "feat-drain-raise"
    await _insert_feature(mq_conn, feature_id)
    repo_raise = tmp_path / "app_raise"
    base_raise = _init_repo(repo_raise)
    patch_raise = _diff_for_appended_line(repo_raise, "raise line\n")
    repo_ok = tmp_path / "app_ok"
    base_ok = _init_repo(repo_ok)
    patch_ok = _diff_for_appended_line(repo_ok, "ok line\n")

    lane_raise = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-RAISE", repo_id="app_raise",
        repo_path=repo_raise, base_commit=base_raise, patch_text=patch_raise,
    )
    lane_ok = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-OK", repo_id="app_ok",
        repo_path=repo_ok, base_commit=base_ok, patch_text=patch_ok,
    )

    real_worker_cls = impl.MergeQueueWorker

    class _RaisingWorker(real_worker_cls):  # type: ignore[misc,valid-type]
        """A worker whose ``apply_candidate`` RAISES for the designated lane.

        Simulates a ``LeaseFencedError``/asyncpg error escaping the Slice 08d
        worker method (the worker only catches its own ``_ApplyFailure``).
        """

        async def apply_candidate(self, item, token):
            if int(item.id) == lane_raise:
                # The exception type a fenced `transition` raises.
                raise MergeQueueStoreError(
                    f"simulated fenced transition for lane {item.id}"
                )
            return await super().apply_candidate(item, token)

    monkeypatch.setattr(impl, "MergeQueueWorker", _RaisingWorker)

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    by_id = {r.item_id: r for r in drained}
    assert set(by_id) == {lane_raise, lane_ok}, (
        "the drain loop must visit BOTH lanes — a raised exception on one "
        "lane must not abort the loop and strand the sibling"
    )
    # The raised lane failed closed and was routed through the Slice 07 router.
    raised = by_id[lane_raise]
    assert raised.integrated is False
    assert raised.failure_class == "checkpoint_contradiction"
    assert raised.routed_failure.get("routed") is True
    assert "simulated fenced transition" in raised.detail
    # The clean sibling STILL integrated despite the raised sibling failure.
    assert by_id[lane_ok].integrated is True

    store = MergeQueueStore(mq_conn)
    raise_item = await store.get(lane_raise)
    # `apply_candidate` raised before any `leased -> applying` transition, so
    # the lane is still `leased`; the drain recorded the routed failure.
    assert raise_item is not None and raise_item.status in ("leased", "failed")
    ok_item = await store.get(lane_ok)
    assert ok_item is not None and ok_item.status == "integrated"
    assert await git_service.head_commit(repo_ok) != base_ok


# ── crash recovery: a crashed-mid-apply lane is recovered on a re-drive ──────


@pytest.mark.asyncio
async def test_drain_recovers_a_lane_crashed_mid_apply(
    mq_conn, tmp_path: Path
) -> None:
    """A lane stuck ``applying`` with an expired lease is recovered (P2-3).

    ``claim``'s predicate matches only ``queued`` / expired-``leased`` rows; it
    NEVER re-takes a lane stuck in ``applying``/``verifying``/``committing``
    after the drain crashed mid-canonical-apply. Before the P2-3 fix such a
    lane was invisible to every drain re-run and stranded mid-flight forever.

    This test stages exactly that crash: a lane is claimed, transitioned to
    ``applying`` with a recorded ``pre_apply_head``, its patch is left applied
    (staged) in the canonical repo, and its lease is expired — simulating a
    worker that died after starting the canonical apply. A drain re-run must
    pick the lane up via ``recover_expired``, reset the canonical repo to its
    pre-apply HEAD, and drive the lane to a deterministic fail-closed terminal.
    """
    feature_id = "feat-drain-crash"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "crashed mid-apply line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-CRASH", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )

    # ── stage the crash: claim, advance to `applying`, leave the repo mutated.
    store = MergeQueueStore(mq_conn)
    owner = "crashed_worker"
    claimed = await store.claim(feature_id, owner)
    assert claimed is not None and claimed.id == lane
    lv = claimed.lease_version
    await store.transition(lane, owner, lv, "applying")
    await store.advance_repo_target(
        lane, "app", owner, lv, "pre_apply_recorded", pre_apply_head=base
    )
    # The crashed worker had already applied the patch to the canonical repo
    # (staged via `git apply --index`) — recovery must reset this away.
    #
    # `_diff_for_appended_line` rewrites README.md twice within git's index
    # mtime granularity, leaving its index entry "racy-clean": `git apply
    # --index` then intermittently reports "README.md: does not match index".
    # `git update-index --refresh` re-stats tracked files and clears the racy
    # flag, making the apply deterministic (it does NOT mutate content).
    _git(repo, "update-index", "-q", "--refresh")
    git_service_apply = subprocess.run(
        ["git", "apply", "--index", "--3way"],
        cwd=repo,
        input=patch,
        capture_output=True,
        text=True,
    )
    assert git_service_apply.returncode == 0, git_service_apply.stderr
    assert (repo / "README.md").read_text() != "init\n"  # repo is mutated
    # Expire the lease so the crashed lane is recoverable.
    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour' "
        "WHERE id = $1",
        lane,
    )

    # A normal `claim` must NOT pick up an `applying` lane — proving the lane
    # is invisible to the drain without the `recover_expired` pass.
    assert await store.claim(feature_id, "another_worker") is None

    # ── the drain re-run: `recover_expired` re-takes and re-drives the lane.
    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.item_id == lane
    assert result.integrated is False
    # The crashed lane is failed closed (the canonical repo reset cleanly).
    assert result.terminal_status == "failed"
    assert result.failure_class == "checkpoint_contradiction"
    assert "crashed in 'applying'" in result.detail
    # The recovery routed the typed failure through the Slice 07 router.
    assert result.routed_failure.get("routed") is True

    # The lane is terminal `failed`; the canonical repo was reset to its
    # pre-apply HEAD (clean, no mid-apply residue, NO legacy commit).
    item = await store.get(lane)
    assert item is not None and item.status == "failed"
    assert await git_service.head_commit(repo) == base
    assert await git_service.working_tree_clean(repo) is True
    assert (repo / "README.md").read_text() == "init\n"

    # Idempotency: a second drain re-run finds nothing claimable or
    # recoverable — the failed lane is terminal and is not re-driven.
    again = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )
    assert again == []


# ── deterministic rebase: a non-conflicting HEAD advance still integrates ────


@pytest.mark.asyncio
async def test_drain_integrates_a_lane_via_deterministic_rebase(
    mq_conn, tmp_path: Path
) -> None:
    """A lane whose ``base_commit`` is an ancestor of HEAD rebases + integrates.

    Doc 08 § Tests: "Deterministic rebase succeeds when ``base_commit`` is an
    ancestor of current HEAD and ``git apply --3way --check`` passes." Every
    other drain test uses HEAD == ``base_commit`` (direct apply) or a DIVERGED
    (non-ancestor) HEAD (conflict). This test advances the canonical HEAD with
    a NON-conflicting commit after the lane is enqueued, so ``base_commit``
    stays an ancestor — the drain takes the deterministic-rebase apply branch
    and the lane still drains all the way to ``integrated``.
    """
    feature_id = "feat-drain-rebase"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    # The lane's patch appends a README line, captured against the ORIGINAL base.
    patch = _diff_for_appended_line(repo, "rebased-onto-advanced-head\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )

    # Advance the canonical HEAD with a NON-conflicting commit — a brand-new
    # file. `base` stays an ancestor of HEAD and the queued README patch does
    # not conflict, so the deterministic three-way rebase applies it cleanly.
    (repo / "unrelated.txt").write_text("unrelated advance\n")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "-q", "-m", "advance head (non-conflicting)")
    advanced_head = _git(repo, "rev-parse", "HEAD").strip()
    assert advanced_head != base

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.item_id == lane
    # The lane rebased onto the advanced HEAD and integrated.
    assert result.integrated is True
    assert result.terminal_status == "integrated"
    assert result.result_commit

    store = MergeQueueStore(mq_conn)
    item = await store.get(lane)
    assert item is not None and item.status == "integrated"
    # The merge proof records the deterministic rebase decision.
    assert item.merge_proof_evidence_id is not None
    proof = await store.load_proof(item.merge_proof_evidence_id)
    assert proof is not None
    assert proof["rebased"] is True

    # Both the README patch and the unrelated advance commit are in the repo.
    assert (repo / "unrelated.txt").exists()
    assert (repo / "README.md").read_text() == (
        "init\nrebased-onto-advanced-head\n"
    )


# ── contract_violation routing: an out-of-contract patch fails closed ────────


@pytest.mark.asyncio
async def test_drain_routes_a_contract_violation_through_the_failure_router(
    mq_conn, tmp_path: Path
) -> None:
    """A patch touching an out-of-contract path fails closed + routes typed.

    Doc 08 § Tests: "Patch that touches outside-contract paths fails before
    apply." The lane's contract is scoped to ``README.md``; the sandbox patch
    ALSO creates ``escape.txt`` — a path outside the lane's ``allowed_paths``.
    ``apply_candidate`` validates the patch path set against the contract
    BEFORE any git mutation, records a typed ``contract_violation``, and leaves
    the lane ``failed``; the drain routes it through the Slice 07 failure
    router as ``contract_violation`` / ``outside_allowed_paths``. It is NEVER
    sent to broad product repair and the legacy commit is not a fallback.
    """
    feature_id = "feat-drain-contract"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)

    # A patch that creates a NEW file `escape.txt` — outside the contract,
    # which `_enqueue_drainable_lane` scopes to `README.md` only.
    (repo / "escape.txt").write_text("outside the contract\n")
    _git(repo, "add", "escape.txt")
    patch = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q")
    (repo / "escape.txt").unlink()

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
        allowed_file="README.md",
    )

    runner = _runner(mq_conn)
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.integrated is False
    assert result.terminal_status == "failed"
    assert result.failure_class == "contract_violation"
    # The typed failure routed through the Slice 07 router as the
    # contract_violation / outside_allowed_paths pair.
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "contract_violation"
    assert result.routed_failure["failure_type"] == "outside_allowed_paths"
    assert result.routed_failure.get("typed_failure_id")

    # The lane is terminal `failed`; the out-of-contract path was rejected
    # BEFORE apply so the canonical repo is untouched (no escape.txt, clean).
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "failed"
    assert not (repo / "escape.txt").exists()
    assert await git_service.head_commit(repo) == base
    assert await git_service.working_tree_clean(repo) is True

    # The router recorded a typed failure row with source `merge_queue`.
    router = runner.services["failure_router"]
    record = router.get_failure(result.routed_failure["typed_failure_id"])
    assert record.observation.failure_class == "contract_violation"
    assert record.observation.source == "merge_queue"


# ── recover_expired poisons a lane past MAX_RECOVERIES ───────────────────────


@pytest.mark.asyncio
async def test_drain_handles_a_recover_expired_poisoned_terminal_lane(
    mq_conn, tmp_path: Path
) -> None:
    """``recover_expired`` poisons a lane past ``MAX_RECOVERIES`` (doc 08 table).

    Doc 08 § Lease Semantics + the "Rollback And Recovery Table": after three
    expired-active recoveries of one row, ``recover_expired`` marks it
    ``poisoned`` instead of re-leasing it. The drain's recovery handler sees a
    ``poisoned`` recovered row, treats it as the terminal it already is, routes
    the typed failure through the Slice 07 router, and does NOT mutate the row
    further. A poisoned row stops automatic feature resume.

    This stages a lane stuck ``applying`` with an expired lease whose
    ``payload.recovery_count`` is already at ``MAX_RECOVERIES`` (3), so the next
    ``recover_expired`` poisons it.
    """
    feature_id = "feat-drain-poison"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "poisoned lane line\n")

    lane = await _enqueue_drainable_lane(
        mq_conn, feature_id, task_id="TASK-1", repo_id="app",
        repo_path=repo, base_commit=base, patch_text=patch,
    )

    # Stage a lane stuck `applying` with an expired lease and a recovery_count
    # already at MAX_RECOVERIES — the next `recover_expired` poisons it.
    store = MergeQueueStore(mq_conn)
    owner = "crashed_worker"
    claimed = await store.claim(feature_id, owner)
    assert claimed is not None and claimed.id == lane
    await store.transition(lane, owner, claimed.lease_version, "applying")
    await mq_conn.execute(
        "UPDATE merge_queue_items SET leased_until = now() - interval '1 hour', "
        "payload = jsonb_set(payload, '{recovery_count}', '3'::jsonb) "
        "WHERE id = $1",
        lane,
    )

    drained = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )

    assert len(drained) == 1
    result = drained[0]
    assert result.item_id == lane
    # `recover_expired` poisoned the lane (recovery limit exceeded); the drain
    # routed the typed terminal failure.
    assert result.terminal_status == "poisoned"
    assert result.integrated is False
    assert result.failure_class == "checkpoint_contradiction"
    assert result.routed_failure.get("routed") is True

    # The lane is terminal `poisoned` — a poisoned row stops automatic resume.
    item = await store.get(lane)
    assert item is not None and item.status == "poisoned"

    # Convergence: a second drain re-run finds nothing claimable or
    # recoverable — the poisoned lane is terminal and is never re-recovered.
    again = await impl._drain_durable_merge_queue_for_feature(
        _runner(mq_conn), _feature(feature_id), dag_sha256=_DAG
    )
    assert again == []
