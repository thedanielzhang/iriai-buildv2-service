"""Slice 08d — MergeQueue worker orchestration (apply_candidate).

Integration tests: a real temporary canonical git repo plus a real Postgres
queue database (the `mq_conn` fixture). They skip when no Postgres is reachable.
"""

from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

import pytest

from iriai_build_v2.execution_control.merge_queue_store import (
    MergeQueueError,
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoTargetCreate,
    TaskCoverageCreate,
)
from iriai_build_v2.workflows.develop.execution import git_service
from iriai_build_v2.workflows.develop.execution.merge_queue import (
    GateOutcome,
    LeaseToken,
    MergeCommitResult,
    MergeQueue,
    RepoApplyInput,
)


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
        "VALUES ($1, $2, 'dag-sha', 1, $3, $4, 'active') RETURNING id",
        feature_id,
        f"contract:{feature_id}:{task_id}",
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


async def _setup_apply_item(conn, repo_path: Path, base_commit: str):
    feature_id = "feat-apply"
    await _insert_feature(conn, feature_id)
    gate = await _insert_evidence(conn, feature_id)
    contract = await _insert_contract(conn, feature_id, "T1")
    store = MergeQueueStore(conn)
    await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256="dag-sha",
            group_idx=1,
            base_commit=base_commit,
            head_commit="head-candidate",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[1],
            gate_evidence_ids=[gate],
            task_coverage=[TaskCoverageCreate(task_id="T1", contract_id=contract)],
            repo_targets=[
                RepoTargetCreate(
                    repo_id="repo-a",
                    repo_path=str(repo_path),
                    base_commit=base_commit,
                )
            ],
        )
    )
    claimed = await store.claim(feature_id, "worker-1")
    assert claimed is not None
    return store, claimed, LeaseToken.for_item(claimed)


def _provider(patch_text: str, allowed_paths: list[str]):
    async def provide(item):
        return [
            RepoApplyInput(
                repo_id="repo-a",
                patch_text=patch_text,
                allowed_paths=allowed_paths,
            )
        ]

    return provide


@pytest.mark.asyncio
async def test_apply_candidate_applies_patch_and_advances_to_verifying(
    mq_conn, tmp_path: Path
) -> None:
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nappended\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    queue = MergeQueue(store, _provider(patch, ["README.md"]))

    result = await queue.apply_candidate(item, token)

    assert result.applied is True
    assert result.status == "verifying"
    assert result.merge_proof_evidence_id is not None
    assert (repo / "README.md").read_text() == "init\nappended\n"

    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "verifying"
    assert refreshed.merge_proof_evidence_id == result.merge_proof_evidence_id
    assert refreshed.repo_targets[0].status == "applied"
    assert refreshed.repo_targets[0].pre_apply_head == base


@pytest.mark.asyncio
async def test_apply_candidate_fails_closed_on_a_nonapplying_patch(
    mq_conn, tmp_path: Path
) -> None:
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    bad_patch = (
        "diff --git a/missing.txt b/missing.txt\n"
        "--- a/missing.txt\n"
        "+++ b/missing.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )
    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    queue = MergeQueue(store, _provider(bad_patch, ["missing.txt"]))

    result = await queue.apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "merge_conflict"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    # The canonical repo is left clean (reset to pre-apply HEAD).
    assert await git_service.working_tree_clean(repo) is True
    assert await git_service.head_commit(repo) == base


@pytest.mark.asyncio
async def test_apply_candidate_rejects_a_patch_outside_contracts(
    mq_conn, tmp_path: Path
) -> None:
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nedit\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    # allowed_paths excludes README.md — the patch escapes the lane contracts.
    queue = MergeQueue(store, _provider(patch, ["src/only_this.py"]))

    result = await queue.apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "contract_violation"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    # Rejected before touching git — the repo is untouched.
    assert (repo / "README.md").read_text() == "init\n"


@pytest.mark.asyncio
async def test_apply_candidate_rejects_a_dirty_baseline_repo(
    mq_conn, tmp_path: Path
) -> None:
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nuncommitted\n")  # leave it dirty

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    queue = MergeQueue(store, _provider("", []))

    result = await queue.apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "checkpoint_contradiction"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"


async def _setup_multi_repo_item(
    conn, repo_a: Path, base_a: str, repo_b: Path, base_b: str
):
    feature_id = "feat-multi"
    await _insert_feature(conn, feature_id)
    gate = await _insert_evidence(conn, feature_id)
    contract = await _insert_contract(conn, feature_id, "T1")
    store = MergeQueueStore(conn)
    await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256="dag-sha",
            group_idx=1,
            base_commit=base_a,
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[1],
            gate_evidence_ids=[gate],
            task_coverage=[TaskCoverageCreate(task_id="T1", contract_id=contract)],
            repo_targets=[
                RepoTargetCreate(
                    repo_id="repo-a", repo_path=str(repo_a), base_commit=base_a
                ),
                RepoTargetCreate(
                    repo_id="repo-b", repo_path=str(repo_b), base_commit=base_b
                ),
            ],
        )
    )
    claimed = await store.claim(feature_id, "worker-1")
    assert claimed is not None
    return store, claimed, LeaseToken.for_item(claimed)


@pytest.mark.asyncio
async def test_apply_candidate_rolls_back_all_repos_when_a_later_repo_fails(
    mq_conn, tmp_path: Path
) -> None:
    repo_a = tmp_path / "repo_a"
    base_a = _init_repo(repo_a)
    repo_b = tmp_path / "repo_b"
    base_b = _init_repo(repo_b)

    # repo-a gets a valid patch; repo-b gets a non-applying patch.
    (repo_a / "README.md").write_text("init\nfrom-a\n")
    patch_a = _git(repo_a, "diff")
    _git(repo_a, "checkout", "--", "README.md")
    bad_patch = (
        "diff --git a/missing.txt b/missing.txt\n"
        "--- a/missing.txt\n"
        "+++ b/missing.txt\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    store, item, token = await _setup_multi_repo_item(
        mq_conn, repo_a, base_a, repo_b, base_b
    )

    async def _provide(_item):
        return [
            RepoApplyInput(
                repo_id="repo-a", patch_text=patch_a, allowed_paths=["README.md"]
            ),
            RepoApplyInput(
                repo_id="repo-b", patch_text=bad_patch,
                allowed_paths=["missing.txt"],
            ),
        ]

    result = await MergeQueue(store, _provide).apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "merge_conflict"
    # repo-a applied first; when repo-b fails, repo-a must NOT be left mutated.
    assert await git_service.head_commit(repo_a) == base_a
    assert await git_service.working_tree_clean(repo_a) is True
    assert (repo_a / "README.md").read_text() == "init\n"
    assert await git_service.working_tree_clean(repo_b) is True
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"


# ── deterministic rebase + stale_projection (08g P2-B / P2-C) ───────────────


async def _setup_apply_item_with_expected_head(
    conn, repo_path: Path, base_commit: str, expected_head: str
):
    """Enqueue + claim a lane whose repo target carries an ``expected_head``.

    Mirrors ``_setup_apply_item`` but threads a non-empty
    ``RepoTargetCreate.expected_head`` so the ``apply_candidate``
    ``expected_head != pre_apply`` -> ``stale_projection`` branch can be driven.
    """
    feature_id = "feat-expected-head"
    await _insert_feature(conn, feature_id)
    gate = await _insert_evidence(conn, feature_id)
    contract = await _insert_contract(conn, feature_id, "T1")
    store = MergeQueueStore(conn)
    await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256="dag-sha",
            group_idx=1,
            base_commit=base_commit,
            head_commit="head-candidate",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract],
            patch_evidence_ids=[1],
            gate_evidence_ids=[gate],
            task_coverage=[TaskCoverageCreate(task_id="T1", contract_id=contract)],
            repo_targets=[
                RepoTargetCreate(
                    repo_id="repo-a",
                    repo_path=str(repo_path),
                    base_commit=base_commit,
                    expected_head=expected_head,
                )
            ],
        )
    )
    claimed = await store.claim(feature_id, "worker-1")
    assert claimed is not None
    return store, claimed, LeaseToken.for_item(claimed)


@pytest.mark.asyncio
async def test_apply_candidate_deterministic_rebase_succeeds_on_ancestor_base(
    mq_conn, tmp_path: Path
) -> None:
    """A patch whose ``base_commit`` is an ancestor of HEAD rebases + applies.

    Doc 08 § Tests: "Deterministic rebase succeeds when ``base_commit`` is an
    ancestor of current HEAD and ``git apply --3way --check`` passes."

    The lane's patch is built against the ORIGINAL base; the canonical HEAD
    then advances with a NON-conflicting commit (a brand-new file, so the
    patch's README hunk still applies cleanly). ``apply_candidate`` sees
    ``pre_apply (= advanced HEAD) != base_commit``, confirms ``base_commit`` is
    an ancestor of HEAD, takes the deterministic-rebase branch, and the patch
    still applies — the lane reaches ``verifying`` with ``rebased=True`` in the
    merge proof. Every existing conflict test uses a DIVERGED (non-ancestor)
    commit, so this rebase-applies branch + the ``rebased`` proof field were
    never exercised before this test.
    """
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    # Build a patch (appends a README line) against the ORIGINAL base.
    (repo / "README.md").write_text("init\nrebased line\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    assert item.base_commit == base

    # Advance the canonical HEAD with a NON-conflicting commit: a brand-new
    # file. `base` stays an ancestor of HEAD; the queued README patch does not
    # conflict with the new file, so the deterministic rebase applies.
    (repo / "unrelated.txt").write_text("new unrelated file\n")
    _git(repo, "add", "unrelated.txt")
    _git(repo, "commit", "-q", "-m", "advance head (non-conflicting)")
    advanced_head = _git(repo, "rev-parse", "HEAD").strip()
    assert advanced_head != base

    queue = MergeQueue(store, _provider(patch, ["README.md"]))
    result = await queue.apply_candidate(item, token)

    # The lane advanced past apply — the rebase branch applied the patch.
    assert result.applied is True
    assert result.status == "verifying"
    assert result.merge_proof_evidence_id is not None
    # The README patch applied on top of the advanced HEAD.
    assert (repo / "README.md").read_text() == "init\nrebased line\n"
    # The non-conflicting commit's file is still present (HEAD was not reset).
    assert (repo / "unrelated.txt").exists()

    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "verifying"
    # The repo target's pre-apply HEAD is the ADVANCED head, not the base.
    assert refreshed.repo_targets[0].pre_apply_head == advanced_head
    assert refreshed.repo_targets[0].status == "applied"

    # The merge proof records `rebased=True` — the deterministic-rebase proof
    # field that every prior (diverged-base) conflict test left unexercised.
    proof = await store.load_proof(result.merge_proof_evidence_id)
    assert proof is not None
    assert proof["rebased"] is True


@pytest.mark.asyncio
async def test_apply_candidate_fails_stale_projection_on_wrong_expected_head(
    mq_conn, tmp_path: Path
) -> None:
    """A repo target whose ``expected_head`` != live HEAD fails closed.

    Doc 08 § Tests / Patch Apply step 4: "If ``expected_head`` is set, it must
    equal the live HEAD." A stale ``expected_head`` means the queued
    projection of the canonical repo state is out of date — ``apply_candidate``
    raises a typed ``stale_projection`` failure BEFORE any git mutation and
    leaves the lane ``failed``, never entering broad product repair. No prior
    test set ``RepoTargetCreate.expected_head`` so this branch was untested.
    """
    repo = tmp_path / "canonical"
    base = _init_repo(repo)

    # The repo target's `expected_head` is a stale 40-hex sha that is NOT the
    # live HEAD (`base`). `apply_candidate` must reject it as `stale_projection`.
    stale_head = "0" * 40
    assert stale_head != base
    store, item, token = await _setup_apply_item_with_expected_head(
        mq_conn, repo, base, stale_head
    )
    assert item.repo_targets[0].expected_head == stale_head

    queue = MergeQueue(store, _provider("", []))
    result = await queue.apply_candidate(item, token)

    assert result.applied is False
    assert result.failure_class == "stale_projection"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    # The stale-projection check fires after `pre_apply_recorded` is persisted
    # but before any apply — the canonical repo HEAD is untouched and clean.
    assert await git_service.head_commit(repo) == base
    assert await git_service.working_tree_clean(repo) is True


@pytest.mark.asyncio
async def test_apply_candidate_succeeds_when_expected_head_matches_live_head(
    mq_conn, tmp_path: Path
) -> None:
    """A repo target whose ``expected_head`` == live HEAD applies normally.

    The positive companion to the ``stale_projection`` test: when the
    ``expected_head`` projection is fresh (equals the live canonical HEAD) the
    ``expected_head`` guard passes and the lane applies + advances to
    ``verifying`` as usual — proving the guard rejects only a STALE head.
    """
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nfresh-head line\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    # `expected_head` equals the live HEAD (`base`) — a fresh projection.
    store, item, token = await _setup_apply_item_with_expected_head(
        mq_conn, repo, base, base
    )

    queue = MergeQueue(store, _provider(patch, ["README.md"]))
    result = await queue.apply_candidate(item, token)

    assert result.applied is True
    assert result.status == "verifying"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "verifying"


# ── run_required_gates (08d-3) ──────────────────────────────────────────────


async def _apply_to_verifying(mq_conn, tmp_path: Path):
    """Set up a repo + item and apply a patch, leaving the lane at verifying."""
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nappended\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    queue = MergeQueue(store, _provider(patch, ["README.md"]))
    result = await queue.apply_candidate(item, token)
    assert result.status == "verifying"
    verifying = await store.get(item.id)
    assert verifying is not None
    return store, repo, base, verifying, token


@pytest.mark.asyncio
async def test_run_required_gates_approval_advances_to_committing(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_to_verifying(mq_conn, tmp_path)
    gate_evidence = await _insert_evidence(mq_conn, item.feature_id)

    async def gate_runner(_item):
        return GateOutcome(approved=True, aggregate_evidence_id=gate_evidence)

    queue = MergeQueue(store, _provider("", []), gate_runner)
    result = await queue.run_required_gates(item, token)

    assert result.approved is True
    assert result.status == "committing"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "committing"
    assert refreshed.post_apply_gate_evidence_id == gate_evidence


@pytest.mark.asyncio
async def test_run_required_gates_rejection_resets_and_fails(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_to_verifying(mq_conn, tmp_path)
    # The applied (staged) change is present before gates run.
    assert (repo / "README.md").read_text() == "init\nappended\n"

    async def gate_runner(_item):
        return GateOutcome(
            approved=False,
            failure_class="verifier_provider",
            detail="post-apply lint failed",
        )

    queue = MergeQueue(store, _provider("", []), gate_runner)
    result = await queue.run_required_gates(item, token)

    assert result.approved is False
    assert result.status == "failed"
    assert result.failure_class == "verifier_provider"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    # Parent and repo-target ledger agree — the target is also failed.
    assert refreshed.repo_targets[0].status == "failed"
    # Gate rejection resets the canonical repo to its pre-apply HEAD.
    assert await git_service.head_commit(repo) == base
    assert await git_service.working_tree_clean(repo) is True
    assert (repo / "README.md").read_text() == "init\n"


@pytest.mark.asyncio
async def test_run_required_gates_rejection_cleans_untracked_added_files(
    mq_conn, tmp_path: Path
) -> None:
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    # Build an add-a-new-file patch.
    (repo / "newfile.txt").write_text("brand new\n")
    _git(repo, "add", "newfile.txt")
    patch = _git(repo, "diff", "--cached")
    _git(repo, "reset", "-q")
    (repo / "newfile.txt").unlink()

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    apply_result = await MergeQueue(
        store, _provider(patch, ["newfile.txt"])
    ).apply_candidate(item, token)
    assert apply_result.status == "verifying"
    assert (repo / "newfile.txt").exists()  # applied to the worktree

    verifying = await store.get(item.id)
    assert verifying is not None

    async def gate_runner(_item):
        return GateOutcome(
            approved=False, failure_class="verifier_provider", detail="rejected"
        )

    result = await MergeQueue(
        store, _provider("", []), gate_runner
    ).run_required_gates(verifying, token)

    assert result.approved is False
    # The untracked file the patch added is cleaned up by the reset.
    assert not (repo / "newfile.txt").exists()
    assert await git_service.working_tree_clean(repo) is True


@pytest.mark.asyncio
async def test_run_required_gates_requires_a_gate_runner(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_to_verifying(mq_conn, tmp_path)
    queue = MergeQueue(store, _provider("", []))  # no gate_runner
    with pytest.raises(MergeQueueError, match="gate_runner"):
        await queue.run_required_gates(item, token)


# ── commit_and_prove_clean / mark_integrated (08d-4) ────────────────────────


def _no_dirty_recorder(conn):
    async def record(item, repo_id: str) -> int:
        journal_row = await conn.fetchval(
            "INSERT INTO execution_journal_rows "
            "(feature_id, idempotency_key, entry_type, status, request_digest) "
            "VALUES ($1, $2, 'merge', 'succeeded', 'rd') RETURNING id",
            item.feature_id,
            f"jr:{uuid.uuid4().hex}",
        )
        return await conn.fetchval(
            "INSERT INTO workspace_snapshots "
            "(feature_id, idempotency_key, execution_journal_row_id, "
            " snapshot_digest) VALUES ($1, $2, $3, 'snap-digest') RETURNING id",
            item.feature_id,
            f"ws:{uuid.uuid4().hex}",
            journal_row,
        )

    return record


async def _apply_gate_to_committing(mq_conn, tmp_path: Path):
    """Set up a lane and drive it through apply + gates to ``committing``."""
    repo = tmp_path / "canonical"
    base = _init_repo(repo)
    (repo / "README.md").write_text("init\nappended\n")
    patch = _git(repo, "diff")
    _git(repo, "checkout", "--", "README.md")

    store, item, token = await _setup_apply_item(mq_conn, repo, base)
    await MergeQueue(store, _provider(patch, ["README.md"])).apply_candidate(
        item, token
    )
    verifying = await store.get(item.id)
    assert verifying is not None
    gate_evidence = await _insert_evidence(mq_conn, verifying.feature_id)

    async def gate_runner(_item):
        return GateOutcome(approved=True, aggregate_evidence_id=gate_evidence)

    await MergeQueue(
        store, _provider("", []), gate_runner
    ).run_required_gates(verifying, token)
    committing = await store.get(item.id)
    assert committing is not None
    return store, repo, base, committing, token


@pytest.mark.asyncio
async def test_commit_and_prove_clean_then_mark_integrated(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_gate_to_committing(
        mq_conn, tmp_path
    )
    queue = MergeQueue(
        store, _provider("", []), no_dirty_recorder=_no_dirty_recorder(mq_conn)
    )

    commit_result = await queue.commit_and_prove_clean(item, token)
    assert commit_result.committed is True
    assert commit_result.result_commit
    assert commit_result.commit_proof_evidence_id is not None
    # The canonical repo has a new commit and is clean.
    assert await git_service.head_commit(repo) != base
    assert await git_service.working_tree_clean(repo) is True
    assert (repo / "README.md").read_text() == "init\nappended\n"

    integrated = await queue.mark_integrated(item, token, commit_result)
    assert integrated.status == "integrated"
    assert integrated.result_commit == commit_result.result_commit

    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "integrated"
    assert refreshed.repo_targets[0].status == "clean"
    assert refreshed.commit_proof_evidence_id == commit_result.commit_proof_evidence_id


@pytest.mark.asyncio
async def test_commit_and_prove_clean_routes_hook_failure_as_commit_hygiene(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_gate_to_committing(
        mq_conn, tmp_path
    )
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\necho 'rejected' >&2\nexit 1\n")
    hook.chmod(0o755)

    queue = MergeQueue(
        store, _provider("", []), no_dirty_recorder=_no_dirty_recorder(mq_conn)
    )
    commit_result = await queue.commit_and_prove_clean(item, token)

    assert commit_result.committed is False
    assert commit_result.failure_class == "commit_hygiene"
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    # Hook rejection means no commit happened — the repo is reset clean.
    assert await git_service.head_commit(repo) == base
    assert await git_service.working_tree_clean(repo) is True


@pytest.mark.asyncio
async def test_mark_integrated_requires_a_successful_commit(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_gate_to_committing(
        mq_conn, tmp_path
    )
    queue = MergeQueue(store, _provider("", []))
    bad = MergeCommitResult(item_id=item.id, committed=False, status="failed")
    with pytest.raises(MergeQueueError, match="successful commit"):
        await queue.mark_integrated(item, token, bad)


async def _apply_gate_to_committing_multi(mq_conn, tmp_path: Path):
    """Drive a 2-repo lane through apply + gates to ``committing``."""
    repo_a = tmp_path / "repo_a"
    base_a = _init_repo(repo_a)
    repo_b = tmp_path / "repo_b"
    base_b = _init_repo(repo_b)
    (repo_a / "README.md").write_text("init\nfrom-a\n")
    patch_a = _git(repo_a, "diff")
    _git(repo_a, "checkout", "--", "README.md")
    (repo_b / "README.md").write_text("init\nfrom-b\n")
    patch_b = _git(repo_b, "diff")
    _git(repo_b, "checkout", "--", "README.md")

    store, item, token = await _setup_multi_repo_item(
        mq_conn, repo_a, base_a, repo_b, base_b
    )

    async def provide(_item):
        return [
            RepoApplyInput(
                repo_id="repo-a", patch_text=patch_a, allowed_paths=["README.md"]
            ),
            RepoApplyInput(
                repo_id="repo-b", patch_text=patch_b, allowed_paths=["README.md"]
            ),
        ]

    await MergeQueue(store, provide).apply_candidate(item, token)
    verifying = await store.get(item.id)
    assert verifying is not None
    gate_evidence = await _insert_evidence(mq_conn, verifying.feature_id)

    async def gate_runner(_item):
        return GateOutcome(approved=True, aggregate_evidence_id=gate_evidence)

    await MergeQueue(
        store, _provider("", []), gate_runner
    ).run_required_gates(verifying, token)
    committing = await store.get(item.id)
    assert committing is not None
    return store, repo_a, base_a, repo_b, base_b, committing, token


@pytest.mark.asyncio
async def test_commit_hook_failure_on_a_later_repo_preserves_earlier_commit(
    mq_conn, tmp_path: Path
) -> None:
    store, repo_a, base_a, repo_b, base_b, item, token = (
        await _apply_gate_to_committing_multi(mq_conn, tmp_path)
    )
    # repo-b's commit hook rejects the candidate.
    hook = repo_b / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    queue = MergeQueue(
        store, _provider("", []), no_dirty_recorder=_no_dirty_recorder(mq_conn)
    )
    result = await queue.commit_and_prove_clean(item, token)

    assert result.committed is False
    assert result.failure_class == "commit_hygiene"
    # repo-a committed first — its real commit MUST be preserved, not discarded.
    assert await git_service.head_commit(repo_a) != base_a
    # repo-b's hook-rejected commit never happened — that repo is reset.
    assert await git_service.head_commit(repo_b) == base_b
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"


@pytest.mark.asyncio
async def test_commit_and_prove_clean_blocks_a_dirty_after_commit_repo(
    mq_conn, tmp_path: Path
) -> None:
    store, repo, base, item, token = await _apply_gate_to_committing(
        mq_conn, tmp_path
    )
    # A post-commit hook makes the repo dirty AFTER the commit lands.
    hook = repo / ".git" / "hooks" / "post-commit"
    hook.write_text("#!/bin/sh\necho dirty > residue.txt\n")
    hook.chmod(0o755)

    queue = MergeQueue(
        store, _provider("", []), no_dirty_recorder=_no_dirty_recorder(mq_conn)
    )
    result = await queue.commit_and_prove_clean(item, token)

    assert result.committed is False
    assert result.failure_class == "checkpoint_contradiction"
    # The commit is real — it must be preserved (doc 08 step 9, never reset).
    assert await git_service.head_commit(repo) != base
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    assert refreshed.repo_targets[0].result_commit != ""


@pytest.mark.asyncio
async def test_commit_and_prove_clean_does_not_create_an_empty_commit(
    mq_conn, tmp_path: Path
) -> None:
    """A clean-repo no-op group does NOT create an empty commit (doc 08 § Tests).

    Doc 08 § Tests: "Clean repo no-op group does not create an empty commit
    unless explicitly marked as a no-op checkpoint by gates and contracts."
    ``commit_and_prove_clean`` calls ``git_service.commit`` WITHOUT
    ``allow_empty`` — so a repo with no changed paths fails the commit closed
    (git "nothing to commit") as a typed ``commit_hygiene`` failure rather than
    fabricating an empty canonical commit.

    The lane is driven through apply + gates to ``committing`` normally; the
    applied change is then reverted (worktree + index reset to HEAD) so the
    repo has NOTHING staged when ``commit_and_prove_clean`` runs — exactly a
    no-op group. The canonical HEAD must be UNCHANGED afterwards: no empty
    commit was created.
    """
    store, repo, base, item, token = await _apply_gate_to_committing(
        mq_conn, tmp_path
    )
    # The lane reached `committing` with an applied (staged) change. Revert it:
    # reset the worktree + index back to HEAD so the repo is clean with NOTHING
    # to commit — a no-op group's canonical state.
    _git(repo, "reset", "-q", "--hard", "HEAD")
    assert await git_service.working_tree_clean(repo) is True
    assert sorted(await git_service.changed_path_set(repo)) == []

    queue = MergeQueue(
        store, _provider("", []), no_dirty_recorder=_no_dirty_recorder(mq_conn)
    )
    result = await queue.commit_and_prove_clean(item, token)

    # No empty commit was created — the commit failed closed as commit_hygiene
    # ("nothing to commit"); `git_service.commit` never passes `--allow-empty`.
    assert result.committed is False
    assert result.failure_class == "commit_hygiene"
    # The canonical HEAD is UNCHANGED — no fabricated empty commit.
    assert await git_service.head_commit(repo) == base
    refreshed = await store.get(item.id)
    assert refreshed is not None
    assert refreshed.status == "failed"
    # No result commit was recorded on the repo target.
    assert refreshed.repo_targets[0].result_commit == ""
