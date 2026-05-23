"""Slice 08e-1 — production merge-queue wiring.

Integration tests against a real Postgres queue database (the ``mq_conn`` /
``mq_dsn`` fixtures from this directory's conftest). They skip when no Postgres
is reachable. Mirrors ``test_merge_queue_coordinator.py``'s structure and its
``_insert_feature`` / ``_insert_contract`` / ``_insert_evidence`` helpers.

Coverage:

* the fail-closed production readiness guard (each missing doc-08-step-8
  dependency reported);
* the gate-evidence persistence bridge (``gate_runner`` and
  ``checkpoint_projector`` persisting real ``evidence_nodes`` rows);
* ``checkpoint_projector`` idempotency (a recovery re-run is a no-op success);
* ``apply_input_provider`` patch-text + allowed-paths resolution.
"""

from __future__ import annotations

import json
import os
import subprocess
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest

from iriai_build_v2.execution_control import (
    ContractVerdict,
    ExecutionControlStore,
    PatchSummary,
)
from iriai_build_v2.execution_control.merge_queue_store import (
    MergeQueueItemCreate,
    MergeQueueStore,
    RepoTargetCreate,
    TaskCoverageCreate,
)
from iriai_build_v2.workflows.develop.execution import git_service
from iriai_build_v2.workflows.develop.execution.merge_queue import (
    GroupMergeCoordinator,
    LeaseToken,
)
from iriai_build_v2.workflows.develop.execution.merge_queue_wiring import (
    GateDecision,
    MergeQueueWiringError,
    build_apply_input_provider,
    build_checkpoint_projector,
    build_gate_runner,
    build_no_dirty_recorder,
    verify_merge_queue_production_ready,
)

_DAG = "dag-sha"

# Postgres connection settings — mirrors conftest.py's overridable env knobs.
_PG_HOST = os.environ.get("IRIAI_TEST_PGHOST", "localhost")
_PG_PORT = os.environ.get("IRIAI_TEST_PGPORT", "5431")
_PG_USER = os.environ.get("IRIAI_TEST_PGUSER") or os.environ.get("USER") or "postgres"
_PG_PASSWORD = os.environ.get("IRIAI_TEST_PGPASSWORD", "")
_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCHEMA_PATH = _REPO_ROOT / "schema.sql"


def _admin_dsn(database: str) -> str:
    auth = _PG_USER if not _PG_PASSWORD else f"{_PG_USER}:{_PG_PASSWORD}"
    return f"postgresql://{auth}@{_PG_HOST}:{_PG_PORT}/{database}"


@asynccontextmanager
async def _isolated_schema_db():
    """Yield a connection to a private throwaway DB with schema.sql loaded.

    The readiness-guard negative tests intentionally ``DROP TABLE`` /
    ``ALTER TABLE`` the schema. They MUST NOT mutate the session-scoped
    ``merge_queue_database`` (every other test would then fail), so each runs
    against its own database that is dropped on exit.
    """

    db_name = f"iriai_mqw_test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(_admin_dsn("postgres"))
    try:
        await admin.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await admin.close()
    conn = await asyncpg.connect(_admin_dsn(db_name))
    try:
        await conn.execute(_SCHEMA_PATH.read_text())
        yield conn
    finally:
        await conn.close()
        admin = await asyncpg.connect(_admin_dsn("postgres"))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await admin.close()


# ── shared fixtures / helpers (mirrors test_merge_queue_coordinator.py) ──────


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
    status: str = "active",
) -> int:
    return await conn.fetchval(
        "INSERT INTO task_deliverable_contracts "
        "(feature_id, idempotency_key, dag_sha256, group_idx, task_id, "
        " contract_digest, status, allowed_paths) "
        "VALUES ($1, $2, $3, 1, $4, $5, $6, $7::jsonb) RETURNING id",
        feature_id,
        f"contract:{feature_id}:{task_id}",
        _DAG,
        task_id,
        f"cd-{task_id}",
        status,
        json.dumps(allowed_paths or []),
    )


async def _insert_evidence(conn, feature_id: str) -> int:
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash) "
        "VALUES ($1, $2, 'gate_request', 'hash') RETURNING id",
        feature_id,
        f"ev:{uuid.uuid4().hex}",
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
    conn,
    feature_id: str,
    *,
    repo_id: str,
    diff_artifact_id: int | None,
) -> int:
    """A ``sandbox_patch_summary`` evidence node carrying a patch payload."""
    payload = {"repo_id": repo_id, "diff_artifact_id": diff_artifact_id}
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, payload) "
        "VALUES ($1, $2, 'sandbox_patch_summary', 'hash', $3::jsonb) "
        "RETURNING id",
        feature_id,
        f"patch:{uuid.uuid4().hex}",
        json.dumps(payload),
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


async def _enqueue_lane(
    conn,
    store: MergeQueueStore,
    feature_id: str,
    *,
    task_id: str,
    contract_id: int,
    repo_id: str = "repo-a",
    repo_path: str = "/repos/a",
    patch_evidence_ids: list[int] | None = None,
) -> int:
    gate = await _insert_evidence(conn, feature_id)
    item = await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            base_commit="base",
            head_commit="h1",
            integration_lane="group",
            pre_queue_gate_evidence_id=gate,
            contract_ids=[contract_id],
            patch_evidence_ids=patch_evidence_ids or [1],
            gate_evidence_ids=[gate],
            task_coverage=[
                TaskCoverageCreate(task_id=task_id, contract_id=contract_id)
            ],
            repo_targets=[
                RepoTargetCreate(
                    repo_id=repo_id, repo_path=repo_path, base_commit="base"
                )
            ],
        )
    )
    return item.id


async def _force_status(conn, feature_id: str, item_id: int, status: str) -> None:
    """Force a queue item to *status*, satisfying the schema proof CHECKs.

    Copied from test_merge_queue_coordinator.py so checkpoint tests can stage
    integrated/done lanes directly.
    """
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


_TOKEN = LeaseToken(item_id=0, lease_owner="coordinator", lease_version=0)


# ── readiness guard ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_production_readiness_guard_ready_on_full_schema(mq_conn) -> None:
    """A database with the complete schema.sql passes the production guard."""
    readiness = await verify_merge_queue_production_ready(mq_conn)
    assert readiness.ready is True
    assert readiness.missing == []


@pytest.mark.asyncio
async def test_production_readiness_guard_wraps_partial_queue_schema_check(
    merge_queue_database,
) -> None:
    """Dropping a queue table fails closed via the wrapped partial guard.

    Runs against an isolated throwaway DB — it mutates the schema.
    """
    async with _isolated_schema_db() as conn:
        await conn.execute("DROP TABLE merge_queue_repo_targets CASCADE")
        readiness = await verify_merge_queue_production_ready(conn)
        assert readiness.ready is False
        assert "table:merge_queue_repo_targets" in readiness.missing


@pytest.mark.asyncio
async def test_production_readiness_guard_fails_closed_on_missing_projection_owner(
    merge_queue_database,
) -> None:
    """Doc-08 step 8: a missing projection-ownership ledger fails closed."""
    async with _isolated_schema_db() as conn:
        await conn.execute(
            "ALTER TABLE execution_artifact_projections "
            "DROP COLUMN projection_owner"
        )
        readiness = await verify_merge_queue_production_ready(conn)
        assert readiness.ready is False
        assert "journal_projection_ownership" in readiness.missing


@pytest.mark.asyncio
async def test_production_readiness_guard_fails_closed_on_missing_sandbox_and_gate(
    merge_queue_database,
) -> None:
    """A kind constraint lacking patch + gate kinds fails both added checks."""
    async with _isolated_schema_db() as conn:
        # Replace the kind CHECK with one that admits none of the kinds the
        # sandbox-patch-capture and gate-runner guards require.
        await conn.execute(
            "ALTER TABLE evidence_nodes DROP CONSTRAINT evidence_nodes_kind_check"
        )
        await conn.execute(
            "ALTER TABLE evidence_nodes ADD CONSTRAINT evidence_nodes_kind_check "
            "CHECK (kind IN ('gate_request'))"
        )
        readiness = await verify_merge_queue_production_ready(conn)
        assert readiness.ready is False
        assert "sandbox_patch_capture" in readiness.missing
        assert "gate_runner" in readiness.missing
        # And the wrapped partial guard's evidence-kind checks also fire.
        assert "evidence_kind:merge_proof" in readiness.missing


# ── apply_input_provider ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_apply_input_provider_resolves_patch_text_and_allowed_paths(
    mq_conn,
) -> None:
    """patch_evidence_ids -> diff text; contract_ids -> allowed_paths."""
    feature_id = "feat-apply"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)

    diff_text = "diff --git a/src/app.py b/src/app.py\n+changed\n"
    artifact_id = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-patch:g1:repo-repo-a.patch", diff_text
    )
    patch_ev = await _insert_patch_evidence(
        mq_conn, feature_id, repo_id="repo-a", diff_artifact_id=artifact_id
    )
    contract = await _insert_contract(
        mq_conn,
        feature_id,
        "T1",
        allowed_paths=[
            {"repo_id": "repo-a", "path": "src/app.py", "match_kind": "file"},
            {"repo_id": "repo-a", "path": "src/lib", "match_kind": "directory"},
            {"repo_id": "other", "path": "skip.py", "match_kind": "file"},
        ],
    )
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        patch_evidence_ids=[patch_ev],
    )
    item = await store.get(item_id)
    assert item is not None

    provider = build_apply_input_provider(mq_conn)
    inputs = await provider(item)

    assert len(inputs) == 1
    assert inputs[0].repo_id == "repo-a"
    assert inputs[0].patch_text == diff_text
    # The "other" repo rule is excluded; the directory rule gets a `/` suffix.
    assert inputs[0].allowed_paths == ["src/app.py", "src/lib/"]


@pytest.mark.asyncio
async def test_apply_input_provider_concatenates_multi_evidence_for_one_repo(
    mq_conn,
) -> None:
    """Two patch evidence rows for one repo concatenate in evidence-id order."""
    feature_id = "feat-apply-multi"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)

    art_a = await _insert_artifact(mq_conn, feature_id, "patch-a", "AAA\n")
    art_b = await _insert_artifact(mq_conn, feature_id, "patch-b", "BBB\n")
    ev_a = await _insert_patch_evidence(
        mq_conn, feature_id, repo_id="repo-a", diff_artifact_id=art_a
    )
    ev_b = await _insert_patch_evidence(
        mq_conn, feature_id, repo_id="repo-a", diff_artifact_id=art_b
    )
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        patch_evidence_ids=[ev_a, ev_b],
    )
    item = await store.get(item_id)
    assert item is not None

    inputs = await build_apply_input_provider(mq_conn)(item)
    assert len(inputs) == 1
    assert inputs[0].patch_text == "AAA\nBBB\n"


@pytest.mark.asyncio
async def test_apply_input_provider_fails_closed_on_missing_diff_artifact(
    mq_conn,
) -> None:
    """A patch evidence node whose diff artifact is absent fails closed."""
    feature_id = "feat-apply-missing"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)

    patch_ev = await _insert_patch_evidence(
        mq_conn, feature_id, repo_id="repo-a", diff_artifact_id=999999
    )
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        patch_evidence_ids=[patch_ev],
    )
    item = await store.get(item_id)
    assert item is not None

    with pytest.raises(MergeQueueWiringError, match="diff artifact"):
        await build_apply_input_provider(mq_conn)(item)


@pytest.mark.asyncio
async def test_apply_input_provider_fails_closed_on_unknown_patch_evidence(
    mq_conn,
) -> None:
    """A patch_evidence_id with no evidence_nodes row fails closed."""
    feature_id = "feat-apply-unknown"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        patch_evidence_ids=[424242],
    )
    item = await store.get(item_id)
    assert item is not None

    with pytest.raises(MergeQueueWiringError, match="does not exist"):
        await build_apply_input_provider(mq_conn)(item)


# ── gate-evidence persistence bridge: gate_runner ───────────────────────────


def _gate_decision_provider(decision: GateDecision):
    async def provide(_item):
        return decision

    return provide


@pytest.mark.asyncio
async def test_gate_runner_persists_approved_aggregate_evidence(mq_conn) -> None:
    """An approved gate decision persists a real aggregate_verdict node."""
    feature_id = "feat-gate"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    item = await store.get(item_id)
    assert item is not None

    control = ExecutionControlStore(mq_conn)
    runner = build_gate_runner(
        control,
        _gate_decision_provider(
            GateDecision(approved=True, verdict_payload={"raw": "ok"})
        ),
    )
    outcome = await runner(item)

    assert outcome.approved is True
    assert outcome.aggregate_evidence_id is not None
    # The persisted node is a real evidence_nodes row of kind aggregate_verdict.
    node = await mq_conn.fetchrow(
        "SELECT kind, status, feature_id FROM evidence_nodes WHERE id = $1",
        outcome.aggregate_evidence_id,
    )
    assert node is not None
    assert node["kind"] == "aggregate_verdict"
    assert node["status"] == "approved"
    assert node["feature_id"] == feature_id


@pytest.mark.asyncio
async def test_gate_runner_persists_rejected_evidence_with_failure_class(
    mq_conn,
) -> None:
    """A rejected gate decision still persists a node + a typed failure class.

    run_required_gates fails the lane closed when an approval lacks an
    evidence id, so the bridge must persist a node even for rejections.
    """
    feature_id = "feat-gate-reject"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    item = await store.get(item_id)
    assert item is not None

    control = ExecutionControlStore(mq_conn)
    runner = build_gate_runner(
        control,
        _gate_decision_provider(
            GateDecision(
                approved=False,
                failure_class="verifier_context",
                detail="stale context",
            )
        ),
    )
    outcome = await runner(item)

    assert outcome.approved is False
    assert outcome.aggregate_evidence_id is not None
    assert outcome.failure_class == "verifier_context"
    node = await mq_conn.fetchrow(
        "SELECT status FROM evidence_nodes WHERE id = $1",
        outcome.aggregate_evidence_id,
    )
    assert node is not None and node["status"] == "rejected"


@pytest.mark.asyncio
async def test_gate_runner_is_idempotent_on_repeat(mq_conn) -> None:
    """Re-running the gate runner with the same verdict reuses the node."""
    feature_id = "feat-gate-idem"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    item = await store.get(item_id)
    assert item is not None

    control = ExecutionControlStore(mq_conn)
    runner = build_gate_runner(
        control, _gate_decision_provider(GateDecision(approved=True))
    )
    first = await runner(item)
    second = await runner(item)

    assert first.aggregate_evidence_id == second.aggregate_evidence_id
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM evidence_nodes "
        "WHERE feature_id = $1 AND kind = 'aggregate_verdict'",
        feature_id,
    )
    assert count == 1


# ── no_dirty_recorder ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_dirty_recorder_records_snapshot_for_clean_repo(
    mq_conn, tmp_path: Path
) -> None:
    """A clean canonical repo yields a real workspace_snapshots row."""
    feature_id = "feat-nodirty"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    repo = tmp_path / "canonical"
    _init_repo(repo)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        repo_id="repo-a", repo_path=str(repo),
    )
    item = await store.get(item_id)
    assert item is not None

    control = ExecutionControlStore(mq_conn)
    recorder = build_no_dirty_recorder(control)
    snapshot_id = await recorder(item, "repo-a")

    row = await mq_conn.fetchrow(
        "SELECT repo_id, stage, feature_id FROM workspace_snapshots WHERE id = $1",
        snapshot_id,
    )
    assert row is not None
    assert row["repo_id"] == "repo-a"
    assert row["stage"] == "merge_queue_no_dirty"
    assert row["feature_id"] == feature_id


@pytest.mark.asyncio
async def test_no_dirty_recorder_is_idempotent(mq_conn, tmp_path: Path) -> None:
    """Re-recording a clean repo reuses the same workspace_snapshots row."""
    feature_id = "feat-nodirty-idem"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    repo = tmp_path / "canonical"
    _init_repo(repo)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        repo_id="repo-a", repo_path=str(repo),
    )
    item = await store.get(item_id)
    assert item is not None

    recorder = build_no_dirty_recorder(ExecutionControlStore(mq_conn))
    first = await recorder(item, "repo-a")
    second = await recorder(item, "repo-a")
    assert first == second
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM workspace_snapshots WHERE feature_id = $1",
        feature_id,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_no_dirty_recorder_fails_closed_on_dirty_repo(
    mq_conn, tmp_path: Path
) -> None:
    """A dirty canonical repo fails closed — no snapshot is recorded."""
    feature_id = "feat-nodirty-dirty"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    repo = tmp_path / "canonical"
    _init_repo(repo)
    (repo / "dirty.txt").write_text("uncommitted\n")  # leave the tree dirty
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    item_id = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract,
        repo_id="repo-a", repo_path=str(repo),
    )
    item = await store.get(item_id)
    assert item is not None

    recorder = build_no_dirty_recorder(ExecutionControlStore(mq_conn))
    with pytest.raises(MergeQueueWiringError, match="not clean"):
        await recorder(item, "repo-a")


# ── checkpoint_projector: persistence bridge + idempotency ──────────────────


def _checkpoint_gate_provider(decision: GateDecision):
    async def provide(_coverage):
        return decision

    return provide


@pytest.mark.asyncio
async def test_checkpoint_projector_creates_approved_gate_then_projects(
    mq_conn,
) -> None:
    """The projector persists an approved checkpoint_gate, then projects.

    project_group_checkpoint REQUIRES source_table='evidence_nodes' + an
    approved checkpoint_gate source_id — so the bridge must create that node
    before projecting. A successful run proves the ordering is right.
    """
    feature_id = "feat-ckpt"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    lane = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    await _force_status(mq_conn, feature_id, lane, "integrated")

    control = ExecutionControlStore(mq_conn)
    projector = build_checkpoint_projector(
        control, _checkpoint_gate_provider(GateDecision(approved=True))
    )
    coord = GroupMergeCoordinator(store, _expected_provider(["T1"]), projector)
    coverage = await coord.coverage(feature_id, _DAG, 1)
    result = await coord.checkpoint_group(coverage, _TOKEN)

    assert result.checkpointed is True
    assert result.checkpoint_projection_id is not None
    # The covered lane is advanced to done and carries the real ids.
    item = await store.get(lane)
    assert item is not None and item.status == "done"
    assert item.checkpoint_projection_id == result.checkpoint_projection_id
    # An approved checkpoint_gate evidence node now exists.
    gate = await mq_conn.fetchrow(
        "SELECT status FROM evidence_nodes "
        "WHERE feature_id = $1 AND kind = 'checkpoint_gate'",
        feature_id,
    )
    assert gate is not None and gate["status"] == "approved"
    # The legacy dag-group:* compatibility artifact is projected.
    artifact = await mq_conn.fetchrow(
        "SELECT key FROM artifacts WHERE feature_id = $1 AND key = 'dag-group:1'",
        feature_id,
    )
    assert artifact is not None


@pytest.mark.asyncio
async def test_checkpoint_projector_fails_closed_on_unapproved_gate(
    mq_conn,
) -> None:
    """An unapproved checkpoint gate fails closed — no checkpoint is projected."""
    feature_id = "feat-ckpt-reject"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    lane = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    await _force_status(mq_conn, feature_id, lane, "integrated")

    control = ExecutionControlStore(mq_conn)
    projector = build_checkpoint_projector(
        control,
        _checkpoint_gate_provider(
            GateDecision(approved=False, failure_class="verifier_context")
        ),
    )
    coord = GroupMergeCoordinator(store, _expected_provider(["T1"]), projector)
    coverage = await coord.coverage(feature_id, _DAG, 1)

    with pytest.raises(MergeQueueWiringError, match="not approved"):
        await coord.checkpoint_group(coverage, _TOKEN)
    # The lane stays integrated — no checkpoint, no dag-group:* artifact.
    item = await store.get(lane)
    assert item is not None and item.status == "integrated"
    artifact = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts WHERE feature_id = $1",
        feature_id,
    )
    assert artifact == 0


@pytest.mark.asyncio
async def test_checkpoint_projector_recovery_rerun_is_a_noop_success(
    mq_conn,
) -> None:
    """A crash-recovery re-run of the projector double-projects nothing.

    Simulates a crash between project() and complete_checkpoint by resetting
    the lane after a first successful checkpoint, then re-running. The
    deterministic checkpoint key makes record_verification_graph_node and
    project_group_checkpoint reuse the existing rows.
    """
    feature_id = "feat-ckpt-recover"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    lane = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    await _force_status(mq_conn, feature_id, lane, "integrated")

    control = ExecutionControlStore(mq_conn)
    projector = build_checkpoint_projector(
        control, _checkpoint_gate_provider(GateDecision(approved=True))
    )
    coord = GroupMergeCoordinator(store, _expected_provider(["T1"]), projector)
    coverage = await coord.coverage(feature_id, _DAG, 1)

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

    second = await coord.checkpoint_group(coverage, _TOKEN)
    assert second.checkpointed is True
    assert second.checkpoint_projection_id == first.checkpoint_projection_id

    # Exactly one checkpoint_gate node, one body node, one dag-group:* artifact.
    gate_count = await mq_conn.fetchval(
        "SELECT count(*) FROM evidence_nodes "
        "WHERE feature_id = $1 AND kind = 'checkpoint_gate'",
        feature_id,
    )
    assert gate_count == 1
    artifact_count = await mq_conn.fetchval(
        "SELECT count(*) FROM artifacts "
        "WHERE feature_id = $1 AND key = 'dag-group:1'",
        feature_id,
    )
    assert artifact_count == 1


@pytest.mark.asyncio
async def test_checkpoint_projector_direct_idempotency(mq_conn) -> None:
    """Calling the projector twice directly returns identical ids.

    Exercises the projector callable in isolation (not via the coordinator's
    already-done short-circuit) to prove the persistence bridge itself is
    idempotent on the doc-08 step-3 checkpoint key.
    """
    feature_id = "feat-ckpt-direct"
    await _insert_feature(mq_conn, feature_id)
    store = MergeQueueStore(mq_conn)
    contract = await _insert_contract(mq_conn, feature_id, "T1")
    lane = await _enqueue_lane(
        mq_conn, store, feature_id, task_id="T1", contract_id=contract
    )
    await _force_status(mq_conn, feature_id, lane, "integrated")

    control = ExecutionControlStore(mq_conn)
    projector = build_checkpoint_projector(
        control, _checkpoint_gate_provider(GateDecision(approved=True))
    )
    coord = GroupMergeCoordinator(store, _expected_provider(["T1"]), projector)
    coverage = await coord.coverage(feature_id, _DAG, 1)
    body = {"group_idx": 1, "task_ids": ["T1"], "verdict": "approved"}

    first = await projector(coverage, body)
    second = await projector(coverage, body)

    assert first.checkpoint_projection_id == second.checkpoint_projection_id
    assert first.checkpoint_gate_evidence_id == second.checkpoint_gate_evidence_id
    assert first.checkpoint_evidence_id == second.checkpoint_evidence_id
    assert first.body_sha256 == second.body_sha256


# ── Slice 08e-2 — implementation-worker enqueue splice ──────────────────────
#
# These exercise `_enqueue_durable_merge_queue_for_results` — the splice that
# replaces the legacy canonical commit on the implementation worker path with
# a durable merge-queue enqueue. They use a real Postgres queue DB via the
# `mq_conn` fixture (so `MergeQueueStore.enqueue` runs its real transaction)
# and a real on-disk git repo for the canonical repo / base commit.


def _enqueue_implementation_module():
    """Import the implementation phase module lazily (heavy import)."""
    from iriai_build_v2.workflows.develop.phases import implementation as _impl

    return _impl


async def _insert_sandbox_patch_evidence(
    conn,
    feature_id: str,
    *,
    repo_id: str,
    base_commit: str,
    diff_artifact_id: int,
) -> int:
    """A ``sandbox_patch_summary`` evidence node as the dispatcher records it."""
    payload = {
        "repo_id": repo_id,
        "base_commit": base_commit,
        "diff_artifact_id": diff_artifact_id,
    }
    return await conn.fetchval(
        "INSERT INTO evidence_nodes "
        "(feature_id, idempotency_key, kind, content_hash, payload) "
        "VALUES ($1, $2, 'sandbox_patch_summary', $3, $4::jsonb) RETURNING id",
        feature_id,
        f"patch:{uuid.uuid4().hex}",
        f"hash-{uuid.uuid4().hex}",
        json.dumps(payload),
    )


def _impl_result(impl, task_id: str, patch_evidence_ids: list[int]):
    """An ImplementationResult carrying the dispatcher's patch-evidence note."""
    notes = (
        "dispatcher_attempt_id=1; patch_summary_ids="
        + ",".join(str(i) for i in patch_evidence_ids)
        + "\n"
        + impl._PENDING_DURABLE_MERGE_QUEUE_NOTE
    )
    return impl.ImplementationResult(
        task_id=task_id,
        summary="implemented",
        status="completed",
        notes=notes,
    )


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_creates_durable_lane(
    mq_conn, tmp_path: Path
) -> None:
    """The 08e-2 splice enqueues a real per-task merge-queue lane.

    Replaces the legacy canonical commit: a sandbox result with immutable
    patch evidence is enqueued as a `task:` integration lane with a real
    pre-queue gate evidence node, task coverage, and a repo target.
    """
    impl = _enqueue_implementation_module()
    feature_id = "feat-impl-enqueue"
    await _insert_feature(mq_conn, feature_id)
    dag_sha256 = await mq_conn.fetchval("SELECT 'dag-sha'::text")

    repo = tmp_path / "app"
    base = _init_repo(repo)
    contract = await _insert_contract(mq_conn, feature_id, "TASK-1")
    diff_artifact = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-diff:TASK-1", "diff --git a/x b/x\n"
    )
    patch_evidence = await _insert_sandbox_patch_evidence(
        mq_conn,
        feature_id,
        repo_id="app",
        base_commit=base,
        diff_artifact_id=diff_artifact,
    )

    # Production `contracts_by_task_id` holds compiled contract objects whose
    # repo identity is attribute-addressable — mirror that with a namespace.
    contract_row = SimpleNamespace(
        id=contract,
        repo_id="app",
        repo_path="app",
        unknown_write_set=False,
    )
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})
    runner = SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(mq_conn)},
    )

    enqueued = await impl._enqueue_durable_merge_queue_for_results(
        runner,
        feature,
        [_impl_result(impl, "TASK-1", [patch_evidence])],
        dag_sha256=dag_sha256,
        group_idx=1,
        contracts_by_task_id={"TASK-1": contract_row},
        feature_root=tmp_path,
        stage="implementation",
    )

    assert len(enqueued) == 1
    item_id = enqueued[0]
    # Reload the typed lane: the queue is authoritative for integration state.
    item = await MergeQueueStore(mq_conn).get(item_id)
    assert item is not None
    assert item.feature_id == feature_id
    assert item.group_idx == 1
    assert item.status == "queued"
    assert item.base_commit == base
    assert item.patch_evidence_ids == [patch_evidence]
    assert item.contract_ids == [contract]
    # A per-task integration lane — the lane identity is in the idempotency key.
    assert "task:TASK-1" in item.idempotency_key
    # The pre-queue gate is a real, persisted aggregate_verdict evidence node.
    gate_id = item.pre_queue_gate_evidence_id
    assert gate_id is not None
    gate_kind = await mq_conn.fetchval(
        "SELECT kind FROM evidence_nodes WHERE id = $1", gate_id
    )
    assert gate_kind == "aggregate_verdict"

    # Coverage + repo-target child rows exist in the same lane.
    assert len(item.task_coverage) == 1
    assert item.task_coverage[0].task_id == "TASK-1"
    assert item.task_coverage[0].contract_id == contract
    assert len(item.repo_targets) == 1
    assert item.repo_targets[0].repo_id == "app"
    assert item.repo_targets[0].repo_path == str(repo)
    assert item.repo_targets[0].base_commit == base


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_is_idempotent(
    mq_conn, tmp_path: Path
) -> None:
    """Re-running the enqueue (resume / crash recovery) reuses the same lane."""
    impl = _enqueue_implementation_module()
    feature_id = "feat-impl-enqueue-idem"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    contract = await _insert_contract(mq_conn, feature_id, "TASK-1")
    diff_artifact = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-diff:TASK-1", "diff\n"
    )
    patch_evidence = await _insert_sandbox_patch_evidence(
        mq_conn, feature_id, repo_id="app", base_commit=base,
        diff_artifact_id=diff_artifact,
    )
    # Production `contracts_by_task_id` holds compiled contract objects whose
    # repo identity is attribute-addressable — mirror that with a namespace.
    contract_row = SimpleNamespace(
        id=contract,
        repo_id="app",
        repo_path="app",
        unknown_write_set=False,
    )
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})
    runner = SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(mq_conn)},
    )

    async def _enqueue() -> list[int]:
        return await impl._enqueue_durable_merge_queue_for_results(
            runner,
            feature,
            [_impl_result(impl, "TASK-1", [patch_evidence])],
            dag_sha256="dag-sha",
            group_idx=1,
            contracts_by_task_id={"TASK-1": contract_row},
            feature_root=tmp_path,
            stage="implementation",
        )

    first = await _enqueue()
    second = await _enqueue()

    assert first == second
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
        feature_id,
    )
    assert count == 1


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_fails_closed_without_store(
    tmp_path: Path,
) -> None:
    """No typed store -> fail closed; never silently fall back to a commit."""
    impl = _enqueue_implementation_module()
    feature = SimpleNamespace(id="f", slug="f", metadata={})
    runner = SimpleNamespace(services={})

    with pytest.raises(impl._MergeQueueEnqueueError, match="execution control"):
        await impl._enqueue_durable_merge_queue_for_results(
            runner,
            feature,
            [_impl_result(impl, "TASK-1", [1])],
            dag_sha256="dag-sha",
            group_idx=0,
            contracts_by_task_id={},
            feature_root=tmp_path,
            stage="implementation",
        )


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_fails_closed_on_foreign_patch(
    mq_conn, tmp_path: Path
) -> None:
    """Patch evidence from another feature fails the enqueue closed."""
    impl = _enqueue_implementation_module()
    feature_id = "feat-impl-enqueue-foreign"
    other_id = "feat-impl-enqueue-other"
    await _insert_feature(mq_conn, feature_id)
    await _insert_feature(mq_conn, other_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    contract = await _insert_contract(mq_conn, feature_id, "TASK-1")
    diff_artifact = await _insert_artifact(
        mq_conn, other_id, "dag-sandbox-diff:TASK-1", "diff\n"
    )
    # Patch evidence belongs to a DIFFERENT feature.
    foreign_patch = await _insert_sandbox_patch_evidence(
        mq_conn, other_id, repo_id="app", base_commit=base,
        diff_artifact_id=diff_artifact,
    )
    # Production `contracts_by_task_id` holds compiled contract objects whose
    # repo identity is attribute-addressable — mirror that with a namespace.
    contract_row = SimpleNamespace(
        id=contract,
        repo_id="app",
        repo_path="app",
        unknown_write_set=False,
    )
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})
    runner = SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(mq_conn)},
    )

    with pytest.raises(
        impl._MergeQueueEnqueueError, match="different feature"
    ):
        await impl._enqueue_durable_merge_queue_for_results(
            runner,
            feature,
            [_impl_result(impl, "TASK-1", [foreign_patch])],
            dag_sha256="dag-sha",
            group_idx=1,
            contracts_by_task_id={"TASK-1": contract_row},
            feature_root=tmp_path,
            stage="implementation",
        )
    # No queue item was created — the enqueue fully failed closed.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
        feature_id,
    )
    assert count == 0


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_rejects_an_outside_root_repo_path(
    mq_conn, tmp_path: Path
) -> None:
    """A contract repo path that escapes the feature root fails the enqueue.

    Doc 08 § Tests (line ~691): "noncanonical repo paths, outside-root paths
    fail in the enqueue transaction." 08g P3-ii adds a defense-in-depth
    queue-layer containment guard to ``_resolve_merge_queue_lane_inputs``: the
    resolved canonical repo dir must be contained within the canonical feature
    root. Slice 03 ``task_contracts.py`` already validates ``repo_path`` at
    contract-compile time so a ``..`` escape is not reachable in production —
    but the merge queue OWNS canonical mutation, so it independently refuses a
    repo path that resolves outside the feature root rather than trusting an
    upstream check.

    The feature root is a SUBDIRECTORY of ``tmp_path``; a real git repo sits
    in a SIBLING directory. The contract's ``repo_path`` is ``../escape``,
    which resolves OUT of the feature root into that sibling repo — proving the
    guard rejects on CONTAINMENT, not merely on a missing ``.git``. The enqueue
    must fail closed with ``_MergeQueueEnqueueError`` (the containment guard
    fires before the ``.git`` check) and write NO queue rows.
    """
    impl = _enqueue_implementation_module()
    feature_id = "feat-impl-enqueue-outside-root"
    await _insert_feature(mq_conn, feature_id)
    # The feature root is a subdir of tmp_path; the escape repo is a sibling of
    # it (still under tmp_path, so pytest cleans it up — but OUTSIDE the
    # feature root the guard checks containment against).
    feature_root = tmp_path / "feature"
    feature_root.mkdir()
    outside_repo = tmp_path / "escape"
    base = _init_repo(outside_repo)
    assert (tmp_path / "feature" / ".." / "escape").resolve() == outside_repo

    contract = await _insert_contract(mq_conn, feature_id, "TASK-1")
    diff_artifact = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-diff:TASK-1", "diff\n"
    )
    patch_evidence = await _insert_sandbox_patch_evidence(
        mq_conn, feature_id, repo_id="app", base_commit=base,
        diff_artifact_id=diff_artifact,
    )
    # The contract's `repo_path` is a `..` path escaping the feature root.
    contract_row = SimpleNamespace(
        id=contract,
        repo_id="app",
        repo_path="../escape",
        unknown_write_set=False,
    )
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})
    runner = SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(mq_conn)},
    )

    with pytest.raises(
        impl._MergeQueueEnqueueError, match="outside the canonical feature"
    ):
        await impl._enqueue_durable_merge_queue_for_results(
            runner,
            feature,
            [_impl_result(impl, "TASK-1", [patch_evidence])],
            dag_sha256="dag-sha",
            group_idx=1,
            contracts_by_task_id={"TASK-1": contract_row},
            feature_root=feature_root,
            stage="implementation",
        )
    # The enqueue failed closed — NO queue rows were written.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
        feature_id,
    )
    assert count == 0


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_partial_failure_rolls_back_all(
    mq_conn, tmp_path: Path
) -> None:
    """Slice 08e-2 P2: a mid-loop enqueue failure leaves ZERO lanes.

    A two-task group is enqueued where the SECOND task's contract is
    `superseded`. Phase-1 lane resolution does not check contract status, so
    both lanes resolve and both pre-queue gate nodes are recorded; the failure
    surfaces only in phase 3 when `MergeQueueStore.enqueue` validates the
    contract. Task 1 enqueues into a savepoint inside the splice's outer
    `conn.transaction()`; task 2 raises; the exception escapes the outer
    transaction, which ROLLS BACK — so task 1's lane is undone too. The
    feature blocks cleanly with no orphaned lane.
    """
    impl = _enqueue_implementation_module()
    feature_id = "feat-impl-enqueue-partial"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)

    # Task 1: a healthy `active` contract. Task 2: a `superseded` contract,
    # which `MergeQueueStore.enqueue._validate_contracts` rejects mid-loop.
    contract_ok = await _insert_contract(mq_conn, feature_id, "TASK-1")
    contract_bad = await _insert_contract(
        mq_conn, feature_id, "TASK-2", status="superseded"
    )
    art_a = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-diff:TASK-1", "diff\n"
    )
    art_b = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-diff:TASK-2", "diff\n"
    )
    patch_a = await _insert_sandbox_patch_evidence(
        mq_conn, feature_id, repo_id="app", base_commit=base,
        diff_artifact_id=art_a,
    )
    patch_b = await _insert_sandbox_patch_evidence(
        mq_conn, feature_id, repo_id="app", base_commit=base,
        diff_artifact_id=art_b,
    )
    row_ok = SimpleNamespace(
        id=contract_ok, repo_id="app", repo_path="app", unknown_write_set=False
    )
    row_bad = SimpleNamespace(
        id=contract_bad, repo_id="app", repo_path="app",
        unknown_write_set=False,
    )
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})
    runner = SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(mq_conn)},
    )

    async def _enqueue() -> list[int]:
        return await impl._enqueue_durable_merge_queue_for_results(
            runner,
            feature,
            [
                _impl_result(impl, "TASK-1", [patch_a]),
                _impl_result(impl, "TASK-2", [patch_b]),
            ],
            dag_sha256="dag-sha",
            group_idx=1,
            contracts_by_task_id={"TASK-1": row_ok, "TASK-2": row_bad},
            feature_root=tmp_path,
            stage="implementation",
        )

    with pytest.raises(impl._MergeQueueEnqueueError, match="TASK-2"):
        await _enqueue()

    # The whole phase-3 transaction rolled back: task 1's lane is NOT orphaned.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
        feature_id,
    )
    assert count == 0

    # Recovery: once task 2's contract is active a clean re-run enqueues BOTH
    # lanes — the partial failure was fully recoverable, no orphans to dedupe.
    await mq_conn.execute(
        "UPDATE task_deliverable_contracts SET status = 'active' WHERE id = $1",
        contract_bad,
    )
    enqueued = await _enqueue()
    assert len(enqueued) == 2
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
        feature_id,
    )
    assert count == 2


@pytest.mark.asyncio
async def test_implementation_worker_enqueue_fails_closed_on_partial_result(
    mq_conn, tmp_path: Path
) -> None:
    """Slice 08e-2 P3a: a `partial`-status result is refused, not enqueued.

    The dispatcher only sets `validate_contract=True` at sandbox patch capture
    for a `completed` result, so a `partial` result's deliverable contract was
    never validated against its captured patch. The splice must fail closed
    rather than stamp an `approved` pre-queue gate node for unvalidated work.
    """
    impl = _enqueue_implementation_module()
    feature_id = "feat-impl-enqueue-partial-status"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    contract = await _insert_contract(mq_conn, feature_id, "TASK-1")
    diff_artifact = await _insert_artifact(
        mq_conn, feature_id, "dag-sandbox-diff:TASK-1", "diff\n"
    )
    patch_evidence = await _insert_sandbox_patch_evidence(
        mq_conn, feature_id, repo_id="app", base_commit=base,
        diff_artifact_id=diff_artifact,
    )
    contract_row = SimpleNamespace(
        id=contract, repo_id="app", repo_path="app", unknown_write_set=False
    )
    feature = SimpleNamespace(id=feature_id, slug=feature_id, metadata={})
    runner = SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(mq_conn)},
    )
    # A `partial` result still carries the pending-durable-merge-queue note
    # (the dispatcher's normalizer stamps it on every validated result).
    partial = _impl_result(impl, "TASK-1", [patch_evidence])
    partial.status = "partial"

    with pytest.raises(impl._MergeQueueEnqueueError, match="not 'completed'"):
        await impl._enqueue_durable_merge_queue_for_results(
            runner,
            feature,
            [partial],
            dag_sha256="dag-sha",
            group_idx=1,
            contracts_by_task_id={"TASK-1": contract_row},
            feature_root=tmp_path,
            stage="implementation",
        )
    # Fail-closed: no lane enqueued for the unvalidated partial result.
    count = await mq_conn.fetchval(
        "SELECT count(*) FROM merge_queue_items WHERE feature_id = $1",
        feature_id,
    )
    assert count == 0


# ── Slice 08f P3-2: real-Postgres coverage for the store.py COALESCE fix ──────
#
# `_insert_or_reuse_evidence_node` binds `started_at` explicitly and the
# field-builder helpers for `record_patch_summary` / `record_contract_verdict`
# omit the key, so `fields.get("started_at")` is `None`. Without the
# `COALESCE($25, NOW())` fix asyncpg binds an explicit SQL `NULL`, which
# suppresses the `evidence_nodes.started_at` column DEFAULT and violates its
# `NOT NULL` constraint against a real Postgres server. The fix was previously
# real-PG-exercised only via `record_verification_graph_node` (the gate runner
# bridge tests above); these two siblings were transitively fixed but uncovered.


@pytest.mark.asyncio
async def test_record_patch_summary_persists_started_at_on_real_postgres(
    mq_conn,
) -> None:
    """`record_patch_summary` writes an `evidence_nodes` row with `started_at`.

    A real Postgres `NOT NULL` insert would fail outright without the COALESCE
    fix; this asserts the row lands and `started_at` is server-defaulted.
    """
    feature_id = "feat-patch-summary-coalesce"
    await _insert_feature(mq_conn, feature_id)

    store = ExecutionControlStore(mq_conn)
    result = await store.record_patch_summary(
        PatchSummary(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            attempt_no=0,
            sandbox_id="sandbox-1",
            task_id="TASK-1",
            repo_id="repo-a",
            base_commit="0" * 40,
            changed_paths=["repo-a/service.py"],
            modified_paths=["repo-a/service.py"],
            diff_sha256="d" * 64,
            summary="patch summary",
            stage="implementation",
        )
    )

    assert result.evidence.id is not None
    row = await mq_conn.fetchrow(
        "SELECT kind, started_at FROM evidence_nodes WHERE id = $1",
        result.evidence.id,
    )
    assert row is not None
    assert row["kind"] == "sandbox_patch_summary"
    # The COALESCE fix restores the schema default; the column is NOT NULL.
    assert row["started_at"] is not None


@pytest.mark.asyncio
async def test_record_contract_verdict_persists_started_at_on_real_postgres(
    mq_conn,
) -> None:
    """`record_contract_verdict` writes an `evidence_nodes` row with `started_at`."""
    feature_id = "feat-contract-verdict-coalesce"
    await _insert_feature(mq_conn, feature_id)
    contract_id = await _insert_contract(mq_conn, feature_id, "TASK-1")

    store = ExecutionControlStore(mq_conn)
    patch_result = await store.record_patch_summary(
        PatchSummary(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            attempt_no=0,
            sandbox_id="sandbox-1",
            task_id="TASK-1",
            repo_id="repo-a",
            base_commit="0" * 40,
            changed_paths=["repo-a/service.py"],
            modified_paths=["repo-a/service.py"],
            diff_sha256="d" * 64,
            summary="patch summary",
            stage="implementation",
        )
    )

    verdict_result = await store.record_contract_verdict(
        ContractVerdict(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=1,
            task_id="TASK-1",
            sandbox_id="sandbox-1",
            contract_id=contract_id,
            patch_summary_id=patch_result.evidence.id,
            approved=True,
            stage="implementation",
            summary="contract verdict",
        )
    )

    assert verdict_result.evidence.id is not None
    row = await mq_conn.fetchrow(
        "SELECT kind, status, started_at FROM evidence_nodes WHERE id = $1",
        verdict_result.evidence.id,
    )
    assert row is not None
    assert row["kind"] == "contract_verdict"
    # The COALESCE fix restores the schema default; the column is NOT NULL.
    assert row["started_at"] is not None
