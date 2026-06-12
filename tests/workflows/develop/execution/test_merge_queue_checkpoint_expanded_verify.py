"""W-LQ — checkpoint-required expanded verify lenses on the QUEUE seal route.

`IRIAI_DAG_EXPANDED_VERIFY=1` wires a five-lens agentic review at the legacy
route's approved-Verdict boundary (`_run_checkpoint_required_dag_verify_lenses`
inside `_verify_and_fix_group`), but queue-sealed groups checkpoint through
`_checkpoint_durable_merge_queue_group`, whose checkpoint-gate decision was
purely DETERMINISTIC (coverage approval) — so the lens pass had NEVER executed
on the durable merge-queue route. W-LQ runs the lens pass for a FRESH queue
seal, merges via the existing `_merge_dag_expanded_verify_verdicts` semantics,
persists the dag-verify artifacts under the group's real projection key
(`dag-verify:g{N}:queue-checkpoint`), and threads the durable lens evidence id
into the persisted `checkpoint_gate` node's `input_refs`.

Coverage (real Postgres via the directory `mq_conn` fixture; the LENS
INVOCATION LAYER — `_run_bound_diagnostic_ask`, the agent call — is mocked, no
real runtimes are ever invoked):

* flag OFF → the seal and the persisted checkpoint-gate decision are
  byte-identical to the deterministic path, and the lens layer is never
  touched;
* flag ON + all lenses approve → the group checkpoints, the per-lens /
  expanded-verify / verification-graph artifacts are recorded under the
  queue-checkpoint stage, and the gate node carries the lens evidence id;
* flag ON + one lens rejects → the checkpoint is REFUSED through the typed
  Slice 07 failure router (`checkpoint_contradiction`), lanes stay
  `integrated`, no `dag-group:*` projection is written — never a legacy
  fallback;
* flag ON + one lens provider crash → isolated and non-blocking (existing
  `_merge_dag_expanded_verify_verdicts` semantics) — the seal completes;
* flag ON re-drive of an ALREADY checkpointed group → idempotent success
  without re-running the lenses;
* flag ON + unapproved coverage → the lens pass is skipped and the existing
  typed coverage failure is preserved verbatim.
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
from iriai_build_v2.models.outputs import (
    ImplementationDAG,
    ImplementationResult,
    ImplementationTask,
    Issue,
    Verdict,
)
from iriai_build_v2.workflows.develop.phases import implementation as impl

_DAG = "dag-sha"
_GROUP = 1
_TASK = "TASK-1"


# ── git + DB staging helpers (mirror test_merge_queue_checkpoint.py) ─────────


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


async def _insert_contract(conn, feature_id: str, task_id: str) -> int:
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
        json.dumps(
            [{"repo_id": "app", "path": "README.md", "match_kind": "file"}]
        ),
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
    repo_path: Path,
    base_commit: str,
    patch_text: str,
) -> int:
    contract = await _insert_contract(conn, feature_id, task_id)
    diff_artifact = await _insert_artifact(
        conn, feature_id, f"dag-sandbox-diff:{task_id}", patch_text
    )
    patch_evidence = await _insert_patch_evidence(
        conn, feature_id, repo_id="app", diff_artifact_id=diff_artifact
    )
    gate = await _insert_gate_evidence(conn, feature_id)
    store = MergeQueueStore(conn)
    item = await store.enqueue(
        MergeQueueItemCreate(
            feature_id=feature_id,
            dag_sha256=_DAG,
            group_idx=_GROUP,
            base_commit=base_commit,
            repo_id="app",
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
                    repo_id="app",
                    repo_path=str(repo_path),
                    base_commit=base_commit,
                )
            ],
            payload={"stage": "implementation", "task_ids": [task_id]},
        )
    )
    return item.id


class _FakeArtifacts:
    """In-memory `runner.artifacts` — the lens pass and the verification-graph
    recorder read/write the `dag`, `dag-repair-lens:*`,
    `dag-repair-expanded-verify:*`, `dag-verify:*`, and `dag-verify-graph:*`
    artifacts through this interface."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str, feature=None):
        return self.store.get(key)

    async def put(self, key: str, value: str, feature=None) -> None:
        self.store[key] = value

    async def delete(self, key: str, feature=None) -> None:
        self.store.pop(key, None)


def _runner(conn) -> SimpleNamespace:
    return SimpleNamespace(
        services={"execution_control_store": ExecutionControlStore(conn)},
        artifacts=_FakeArtifacts(),
    )


def _feature(feature_id: str) -> SimpleNamespace:
    return SimpleNamespace(id=feature_id, slug=feature_id, metadata={})


def _group_dag_json() -> str:
    return ImplementationDAG(
        tasks=[
            ImplementationTask(
                id=_TASK,
                name="Append a README line",
                description="Append one line to README.md.",
            )
        ],
        execution_order=[[_TASK]],
        complete=True,
    ).model_dump_json()


def _task_results() -> list[ImplementationResult]:
    return [
        ImplementationResult(
            task_id=_TASK,
            summary="Appended the line via the sandbox.",
            status="completed",
            files_modified=["README.md"],
        )
    ]


async def _stage_drained_group(conn, tmp_path: Path, feature_id: str):
    """Enqueue + drain one single-task group; return (runner, feature, lane)."""
    await _insert_feature(conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "expanded verify line\n")
    lane = await _enqueue_drainable_lane(
        conn,
        feature_id,
        task_id=_TASK,
        repo_path=repo,
        base_commit=base,
        patch_text=patch,
    )
    runner = _runner(conn)
    runner.artifacts.store["dag"] = _group_dag_json()
    feature = _feature(feature_id)
    drained = await impl._drain_durable_merge_queue_for_feature(
        runner, feature, dag_sha256=_DAG
    )
    assert len(drained) == 1 and drained[0].succeeded
    return runner, feature, lane


def _install_lens_ask_mock(
    monkeypatch: pytest.MonkeyPatch,
    *,
    reject_slugs: frozenset[str] = frozenset(),
    crash_slugs: frozenset[str] = frozenset(),
) -> tuple[list[str], list[dict]]:
    """Mock the LENS INVOCATION LAYER (`_run_bound_diagnostic_ask`).

    Returns ``(called_slugs, captured_kwargs)``. The real
    `_run_expanded_dag_verify_lenses` machinery (per-lens isolation, merge,
    artifact writes, verification-graph persistence) runs unmocked. The
    prompt-context packager is reduced to its documented None fallback so no
    workspace machinery is needed.
    """
    called: list[str] = []
    captured: list[dict] = []

    async def _ask(runner, feature, **kwargs):
        lane_id = str(kwargs.get("lane_id") or "")
        slug = lane_id.rsplit(":", 1)[-1]
        called.append(slug)
        captured.append(dict(kwargs))
        if slug in crash_slugs:
            raise RuntimeError(f"lens provider crashed for {slug}")
        if slug in reject_slugs:
            return Verdict(
                approved=False,
                summary=f"{slug}: blocking defect found",
                concerns=[
                    Issue(
                        severity="blocker",
                        description=f"{slug} lens found a blocking defect",
                    )
                ],
            )
        return Verdict(approved=True, summary=f"{slug}: clean")

    async def _no_context_package(runner, feature, **kwargs):
        captured.append({"context_sections": kwargs.get("sections")})
        return None

    monkeypatch.setattr(impl, "_run_bound_diagnostic_ask", _ask)
    monkeypatch.setattr(
        impl, "_build_prompt_context_package", _no_context_package
    )
    return called, captured


async def _checkpoint(runner, feature) -> impl._MergeQueueCheckpointResult:
    return await impl._checkpoint_durable_merge_queue_group(
        runner,
        feature,
        dag_sha256=_DAG,
        group_idx=_GROUP,
        expected_task_ids=[_TASK],
        task_results=_task_results(),
        dag_ordered_task_ids=[_TASK],
    )


async def _checkpoint_gate_node(conn, feature_id: str):
    return await conn.fetchrow(
        "SELECT payload, input_refs FROM evidence_nodes "
        "WHERE feature_id = $1 AND kind = 'checkpoint_gate'",
        feature_id,
    )


def _jsonb(value):
    return json.loads(value) if isinstance(value, str) else value


# ── (a) flag OFF: byte-identical deterministic gate decision ─────────────────


@pytest.mark.asyncio
async def test_expanded_verify_off_gate_decision_is_byte_identical(
    mq_conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`IRIAI_DAG_EXPANDED_VERIFY=0` pins the pre-W-LQ deterministic seal.

    The lens layer must never be touched, the persisted `checkpoint_gate`
    evidence node must carry the EXACT pre-W-LQ verdict payload with EMPTY
    `input_refs`, and no queue-checkpoint dag-verify artifacts may exist.
    """
    monkeypatch.setenv("IRIAI_DAG_EXPANDED_VERIFY", "0")

    async def _must_not_run(*args, **kwargs):  # pragma: no cover - sentinel.
        raise AssertionError(
            "the expanded-verify lens layer ran with the flag OFF"
        )

    monkeypatch.setattr(
        impl, "_run_queue_checkpoint_expanded_verify", _must_not_run
    )
    monkeypatch.setattr(
        impl, "_run_checkpoint_required_dag_verify_lenses", _must_not_run
    )

    feature_id = "feat-xv-off"
    runner, feature, lane = await _stage_drained_group(
        mq_conn, tmp_path, feature_id
    )
    result = await _checkpoint(runner, feature)

    assert result.checkpointed is True
    assert lane in result.done_queue_item_ids

    gate = await _checkpoint_gate_node(mq_conn, feature_id)
    assert gate is not None
    assert _jsonb(gate["input_refs"]) == []
    payload = _jsonb(gate["payload"])
    # Byte-identical deterministic verdict payload (no expanded_verify key,
    # no lens lineage) — exactly what the pre-W-LQ gate decision persisted.
    assert payload["verdict"] == {
        "gate": "merge_queue_group_checkpoint",
        "group_idx": _GROUP,
        "expected_task_ids": [_TASK],
        "integrated_queue_item_ids": [lane],
    }
    # No queue-checkpoint dag-verify rows were written anywhere.
    assert not [
        key
        for key in runner.artifacts.store
        if "queue-checkpoint" in key
    ]


# ── (b) flag ON, all lenses approve ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_expanded_verify_on_all_lenses_approve_seals_with_lens_evidence(
    mq_conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """All lenses approve → approved checkpoint + lens artifacts + lineage.

    The five checkpoint-required lenses run (mocked agent layer), the merged
    verdict stays approved, the dag-verify artifacts land under the group's
    REAL projection key `dag-verify:g1:queue-checkpoint` with a durable
    verification-graph projection, and the persisted `checkpoint_gate` node
    records the graph's aggregate evidence id in `input_refs`.
    """
    monkeypatch.setenv("IRIAI_DAG_EXPANDED_VERIFY", "1")
    called, captured = _install_lens_ask_mock(monkeypatch)

    feature_id = "feat-xv-approve"
    runner, feature, lane = await _stage_drained_group(
        mq_conn, tmp_path, feature_id
    )
    result = await _checkpoint(runner, feature)

    assert result.checkpointed is True
    assert lane in result.done_queue_item_ids

    # Every required lens ran exactly once through the (mocked) agent layer.
    required = impl._dag_verify_required_lens_slugs()
    assert required and sorted(called) == sorted(required)

    # The lens runtime is the dag-final-verify resolution chain — the same
    # chain `_verify_and_fix_group` seeds its checkpoint-required lens call
    # with (group review runtime -> dag-normal-verify -> dag-final-verify ->
    # per-lens), never the raw default.
    policy = impl._runner_runtime_policy(runner)
    _impl_rt, review_rt = impl._dag_group_runtime_pair(_GROUP, policy)
    expected_base = impl._dag_repair_runtime_for(
        "dag-final-verify",
        impl._dag_repair_runtime_for("dag-normal-verify", review_rt),
    )
    lens_calls = [c for c in captured if "lane_id" in c]
    for call in lens_calls:
        slug = str(call["lane_id"]).rsplit(":", 1)[-1]
        assert call["runtime"] == impl._dag_repair_runtime_for(
            f"lens:{slug}", expected_base
        )

    # Group context threading: the lens prompt context carries the group's
    # task specs (from the durable `dag` artifact) and the changed-file list
    # (from the per-task ImplementationResults).
    section_payloads = [
        c["context_sections"] for c in captured if "context_sections" in c
    ]
    assert section_payloads
    flattened = json.dumps(section_payloads[0])
    assert _TASK in flattened
    assert "README.md" in flattened

    # Per-lens + merged expanded-verify artifacts under the queue-checkpoint
    # stage label.
    store = runner.artifacts.store
    for slug in required:
        lens_artifact = json.loads(
            store[f"dag-repair-lens:g{_GROUP}:{slug}:retry-queue-checkpoint"]
        )
        assert lens_artifact["status"] == "completed"
    merged_artifact = json.loads(
        store[f"dag-repair-expanded-verify:g{_GROUP}:retry-queue-checkpoint"]
    )
    assert merged_artifact["normal_approved"] is True
    assert merged_artifact["merged_approved"] is True
    assert merged_artifact["failed_lenses"] == []

    # The verification graph landed under the group's REAL projection key and
    # is durably persisted (typed-store reload-backed), exactly as the legacy
    # stages persist theirs — the resume reader / gate-proof machinery parse
    # the `queue-checkpoint` stage from the same key format.
    graph = json.loads(store[f"dag-verify-graph:g{_GROUP}:queue-checkpoint"])
    assert graph["projection_key"] == f"dag-verify:g{_GROUP}:queue-checkpoint"
    assert graph["stage"] == "queue-checkpoint"
    assert graph["group_idx"] == _GROUP
    assert graph["dag_sha256"] == _DAG
    assert graph["approved"] is True
    assert graph["proof"]
    durable = graph["durable_projection"]
    assert durable["persisted"] is True
    aggregate_evidence_id = int(durable["aggregate_evidence_node_id"])

    # GateDecision lineage: the persisted checkpoint_gate node references the
    # lens graph's aggregate evidence node and carries the expanded-verify
    # verdict material.
    gate = await _checkpoint_gate_node(mq_conn, feature_id)
    assert gate is not None
    assert _jsonb(gate["input_refs"]) == [aggregate_evidence_id]
    verdict_payload = _jsonb(gate["payload"])["verdict"]
    expanded = verdict_payload["expanded_verify"]
    assert expanded["approved"] is True
    assert expanded["stage_label"] == "queue-checkpoint"
    assert expanded["projection_key"] == (
        f"dag-verify:g{_GROUP}:queue-checkpoint"
    )
    assert expanded["lens_run_count"] == len(required)
    assert expanded["lens_failure_count"] == 0
    assert expanded["lens_evidence_ids"] == [aggregate_evidence_id]


# ── (c) flag ON, one lens rejects ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_expanded_verify_on_lens_rejection_refuses_checkpoint_typed(
    mq_conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A blocking lens finding refuses the seal through the typed router.

    The merged verdict loses approval (existing
    `_merge_dag_expanded_verify_verdicts` semantics), the checkpoint returns
    `checkpointed=False` with the typed `checkpoint_contradiction` route —
    NEVER a legacy fallback — the lanes stay `integrated` (re-drivable after
    repair), and no `dag-group:*` projection is written.
    """
    monkeypatch.setenv("IRIAI_DAG_EXPANDED_VERIFY", "1")
    required = impl._dag_verify_required_lens_slugs()
    assert required
    _called, _captured = _install_lens_ask_mock(
        monkeypatch, reject_slugs=frozenset({required[0]})
    )

    feature_id = "feat-xv-reject"
    runner, feature, lane = await _stage_drained_group(
        mq_conn, tmp_path, feature_id
    )
    result = await _checkpoint(runner, feature)

    assert result.checkpointed is False
    assert (
        "refused by the checkpoint-required expanded verify lenses"
        in result.detail
    )
    # Typed Slice 07 route — same family as every other checkpoint failure.
    assert result.routed_failure.get("routed") is True
    assert result.routed_failure["failure_class"] == "checkpoint_contradiction"
    assert result.routed_failure.get("typed_failure_id")
    # A lens rejection is a finding to repair, not a coverage-visibility
    # transient — it must NOT match the auto-re-drive classifier.
    assert impl._checkpoint_coverage_redrivable(result.detail) is False

    # The lane is still `integrated` (not done, not poisoned) and no
    # dag-group projection exists — the group can re-seal after repair.
    item = await MergeQueueStore(mq_conn).get(lane)
    assert item is not None and item.status == "integrated"
    projection = await mq_conn.fetchrow(
        "SELECT 1 FROM execution_artifact_projections "
        "WHERE feature_id = $1 AND projection_key = $2",
        feature_id,
        f"dag-group:{_GROUP}",
    )
    assert projection is None

    # The rejecting finding is durably recorded under the queue-checkpoint
    # projection key for the repair cycle to consume.
    merged_artifact = json.loads(
        runner.artifacts.store[
            f"dag-repair-expanded-verify:g{_GROUP}:retry-queue-checkpoint"
        ]
    )
    assert merged_artifact["merged_approved"] is False
    assert merged_artifact["concerns"] >= 1


# ── (d) flag ON, lens provider crash is isolated ─────────────────────────────


@pytest.mark.asyncio
async def test_expanded_verify_on_lens_provider_crash_is_non_blocking(
    mq_conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One lens provider crash is isolated; the seal completes (existing
    `_run_expanded_dag_verify_lenses` per-lens isolation semantics)."""
    monkeypatch.setenv("IRIAI_DAG_EXPANDED_VERIFY", "1")
    required = impl._dag_verify_required_lens_slugs()
    assert required
    called, _captured = _install_lens_ask_mock(
        monkeypatch, crash_slugs=frozenset({required[-1]})
    )

    feature_id = "feat-xv-crash"
    runner, feature, lane = await _stage_drained_group(
        mq_conn, tmp_path, feature_id
    )
    result = await _checkpoint(runner, feature)

    assert result.checkpointed is True
    assert lane in result.done_queue_item_ids
    assert sorted(called) == sorted(required)

    merged_artifact = json.loads(
        runner.artifacts.store[
            f"dag-repair-expanded-verify:g{_GROUP}:retry-queue-checkpoint"
        ]
    )
    assert merged_artifact["merged_approved"] is True
    assert [f["lens"] for f in merged_artifact["failed_lenses"]] == [
        required[-1]
    ]
    crashed_lens_artifact = json.loads(
        runner.artifacts.store[
            f"dag-repair-lens:g{_GROUP}:{required[-1]}:retry-queue-checkpoint"
        ]
    )
    assert crashed_lens_artifact["status"] == "failed"

    gate = await _checkpoint_gate_node(mq_conn, feature_id)
    assert gate is not None
    expanded = _jsonb(gate["payload"])["verdict"]["expanded_verify"]
    assert expanded["approved"] is True
    assert expanded["lens_run_count"] == len(required) - 1
    assert expanded["lens_failure_count"] == 1


# ── idempotent re-drive + unapproved-coverage guards ─────────────────────────


@pytest.mark.asyncio
async def test_expanded_verify_on_idempotent_redrive_skips_lenses(
    mq_conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-drive of an ALREADY checkpointed group never re-runs the lenses.

    Doc-08 recovery re-drives the idempotent checkpoint; the lens pass is a
    fresh-seal concern, so the re-drive must stay a lens-free no-op success.
    """
    monkeypatch.setenv("IRIAI_DAG_EXPANDED_VERIFY", "1")
    called, _captured = _install_lens_ask_mock(monkeypatch)

    feature_id = "feat-xv-redrive"
    runner, feature, lane = await _stage_drained_group(
        mq_conn, tmp_path, feature_id
    )
    first = await _checkpoint(runner, feature)
    assert first.checkpointed is True
    lens_runs_at_seal = len(called)
    assert lens_runs_at_seal == len(impl._dag_verify_required_lens_slugs())

    async def _must_not_run(*args, **kwargs):  # pragma: no cover - sentinel.
        raise AssertionError("lenses re-ran on an idempotent re-drive")

    monkeypatch.setattr(
        impl, "_run_queue_checkpoint_expanded_verify", _must_not_run
    )
    second = await _checkpoint(runner, feature)
    assert second.checkpointed is True
    assert lane in second.done_queue_item_ids


@pytest.mark.asyncio
async def test_expanded_verify_on_unapproved_coverage_skips_lenses(
    mq_conn, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An undrained (still `queued`) lane keeps the EXISTING typed coverage
    failure verbatim and never reaches the lens layer."""
    monkeypatch.setenv("IRIAI_DAG_EXPANDED_VERIFY", "1")

    async def _must_not_run(*args, **kwargs):  # pragma: no cover - sentinel.
        raise AssertionError("lenses ran for an unapproved coverage")

    monkeypatch.setattr(
        impl, "_run_queue_checkpoint_expanded_verify", _must_not_run
    )

    feature_id = "feat-xv-uncovered"
    await _insert_feature(mq_conn, feature_id)
    repo = tmp_path / "app"
    base = _init_repo(repo)
    patch = _diff_for_appended_line(repo, "never drained\n")
    await _enqueue_drainable_lane(
        mq_conn,
        feature_id,
        task_id=_TASK,
        repo_path=repo,
        base_commit=base,
        patch_text=patch,
    )
    runner = _runner(mq_conn)
    feature = _feature(feature_id)
    # NO drain — the lane is still `queued`, so coverage is not approved.
    result = await _checkpoint(runner, feature)

    assert result.checkpointed is False
    assert "coverage is not approved" in result.detail
    assert result.routed_failure.get("routed") is True
