from __future__ import annotations

"""Regression coverage for the commit-hygiene recovery *accounting* refinements
(feature 8ac124d6, DAG group 78) that surfaced when the blocker-#2 recovery seam
ran live:

* **Counter over-count** — the old budget was incremented at the rerun DECISION
  (before dispatch), so a resume that was blocked downstream (a sibling's
  terminal) AND a re-run that failed on a runtime context-window overflow before
  it could enqueue a retry lane each burned a budget unit without a real hygiene
  non-convergence. slice-14 thereby reached the cap (2) and would FALSE-escalate
  to a terminal ``workflow_blocked`` even though its effective genuine
  hygiene-refailure count was 0. The budget is now derived from durable
  evidence: confirmed hygiene re-failures (failed RETRY lanes,
  :func:`_commit_hygiene_retry_refailure_counts_for_group`) PLUS re-runs that
  terminated in a runtime block (the ``dag-commit-hygiene-rerun-block:{tid}``
  counter), the latter incremented only AFTER a dispatched re-run returns
  blocked — never at the decision, never on a sibling's block.

* **Sibling isolation** — the group dispatch returns ``workflow_blocked`` as soon
  as ANY task blocks, discarding a sibling's clean, enqueueable success. The
  success is now enqueued + drained to ``integrated`` before the blocked
  terminal (:func:`_integrate_nonblocked_successes_before_block`) so it lands
  independently of the blocked sibling.

These are pure unit tests of the extracted helpers + the fake merge-queue store
surface, matching ``test_integrated_lane_pending_marker.py`` (driving the full
``_implement_dag`` resume is blocked by the strict-resume adoption-marker
requirement — the same reason two ``test_merge_queue_checkpoint`` tests are
pre-existing failures).
"""

from pathlib import Path
from types import SimpleNamespace

import pytest

import iriai_build_v2.workflows.develop.phases.implementation as impl
from iriai_build_v2.workflows.develop.phases.implementation import (
    _PENDING_DURABLE_MERGE_QUEUE_NOTE,
    ImplementationResult,
    _commit_hygiene_augmented_contract,
    _commit_hygiene_recovery_feedback,
    _commit_hygiene_recovery_lanes_for_group,
    _commit_hygiene_retry_refailure_counts_for_group,
    _commit_hygiene_rerun_block_marker_key,
    _commit_hygiene_widening_grant,
    _CommitHygieneRecoveryLane,
    _integrate_nonblocked_successes_before_block,
    _integrated_lane_task_ids_for_group,
    _parse_commit_hygiene_hook_offenders,
)
from iriai_build_v2.workflows.develop.execution.task_contracts import (
    ContractCompiler,
    ContractExecutionPolicy,
    ContractPathRule,
    PatchSummary,
    TaskDeliverableContract,
)
from iriai_build_v2.workflows.develop.execution.workspace_authority import (
    WorkspaceSnapshot,
)


# ── Fakes mirroring the merge-queue store surface the helper touches ─────────


class _FakeCoverage:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class _FakeItem:
    def __init__(
        self,
        status: str,
        task_ids: list[str],
        *,
        id: int = 0,
        last_error: str = "",
        retry_of_queue_item_id: int | None = None,
        retry_of_in_payload: bool = False,
    ) -> None:
        self.id = id
        self.status = status
        self.task_coverage = [_FakeCoverage(t) for t in task_ids]
        self.retry_of_queue_item_id = retry_of_queue_item_id
        self.payload: dict = {"last_error": last_error} if last_error else {}
        # Some lanes carry the retry source only in the payload (the enqueue
        # writes both the typed field AND payload["retry_of_queue_item_id"]).
        if retry_of_in_payload and retry_of_queue_item_id is not None:
            self.retry_of_queue_item_id = None
            self.payload["retry_of_queue_item_id"] = retry_of_queue_item_id


class _FakeQueueStore:
    items: list[_FakeItem] = []
    raise_on_list: bool = False

    def __init__(self, _conn: object) -> None:
        self._conn = _conn

    async def list_group_items(self, _feature_id, _dag, _group_idx):
        if type(self).raise_on_list:
            raise RuntimeError("simulated store/DB error")
        return type(self).items


def _runner_with_store():
    store = SimpleNamespace(put_task_contract=lambda *a, **k: None, _pool=object())
    return SimpleNamespace(services={"execution_control_store": store})


def _feature(feature_id: str = "8ac124d6"):
    return SimpleNamespace(id=feature_id)


@pytest.fixture(autouse=True)
def _patch_merge_queue_store(monkeypatch):
    _FakeQueueStore.items = []
    _FakeQueueStore.raise_on_list = False
    monkeypatch.setattr(impl, "MergeQueueStore", _FakeQueueStore)
    yield


_HYG = "commit_hygiene: commit hook failed (exit 1)\nstderr:\n<rule violation>"


# ── The runtime-block marker key ─────────────────────────────────────────────


def test_rerun_block_marker_key_is_task_scoped_and_distinct():
    # A separate namespace from the (now-unused) decision counter so the stale,
    # over-counted `dag-commit-hygiene-rerun:{tid}` value is never consulted.
    assert (
        _commit_hygiene_rerun_block_marker_key("slice-14")
        == "dag-commit-hygiene-rerun-block:slice-14"
    )
    assert _commit_hygiene_rerun_block_marker_key(
        "a"
    ) != "dag-commit-hygiene-rerun:a"


# ── Genuine hygiene-refailure counts (failed RETRY lanes only) ───────────────


@pytest.mark.asyncio
async def test_only_failed_retry_lanes_count():
    # A failed ORIGINAL lane (retry_of None) is the recovery SOURCE, not a
    # re-failure — it must NOT count. A failed RETRY lane (retry_of set) is a
    # confirmed hygiene re-failure of a recovered patch — it counts.
    _FakeQueueStore.items = [
        _FakeItem("failed", ["slice-14"], last_error=_HYG),  # original source
        _FakeItem(
            "failed", ["slice-2-002"], last_error=_HYG, retry_of_queue_item_id=7
        ),  # genuine re-failure
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {"slice-2-002": 1}
    assert "slice-14" not in counts  # the items-5/7 shape: 0 genuine refailures


@pytest.mark.asyncio
async def test_retry_source_in_payload_is_honored():
    _FakeQueueStore.items = [
        _FakeItem(
            "failed",
            ["t"],
            last_error=_HYG,
            retry_of_queue_item_id=5,
            retry_of_in_payload=True,
        ),
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {"t": 1}


@pytest.mark.asyncio
async def test_multiple_refailures_accumulate_per_task():
    _FakeQueueStore.items = [
        _FakeItem("failed", ["t"], last_error=_HYG, retry_of_queue_item_id=1),
        _FakeItem("failed", ["t"], last_error=_HYG, retry_of_queue_item_id=2),
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {"t": 2}


@pytest.mark.asyncio
async def test_non_commit_hygiene_retry_does_not_count():
    _FakeQueueStore.items = [
        _FakeItem(
            "failed",
            ["t"],
            last_error="merge_conflict: rebase rejected",
            retry_of_queue_item_id=9,
        ),
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {}


@pytest.mark.asyncio
async def test_non_failed_retry_lane_does_not_count():
    # An integrated retry lane is a SUCCESS, not a re-failure.
    _FakeQueueStore.items = [
        _FakeItem(
            "integrated", ["t"], last_error="", retry_of_queue_item_id=9
        ),
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {}


@pytest.mark.asyncio
async def test_refailure_counts_fail_closed():
    # Fail closed on store error → empty map → the budget falls back to the
    # block counter alone (never a fabricated escalation).
    _FakeQueueStore.items = [
        _FakeItem("failed", ["t"], last_error=_HYG, retry_of_queue_item_id=1)
    ]
    _FakeQueueStore.raise_on_list = True
    assert (
        await _commit_hygiene_retry_refailure_counts_for_group(
            _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
        )
        == {}
    )

    # No execution-control store at all → empty map.
    assert (
        await _commit_hygiene_retry_refailure_counts_for_group(
            SimpleNamespace(services={}), _feature(),
            dag_sha256="dag-sha", group_idx=78,
        )
        == {}
    )

    # Missing feature id / dag → empty map.
    assert (
        await _commit_hygiene_retry_refailure_counts_for_group(
            _runner_with_store(), _feature(""), dag_sha256="dag-sha", group_idx=78,
        )
        == {}
    )


@pytest.mark.asyncio
async def test_refailure_on_stale_vague_source_does_not_count():
    # slice-14 shape: lane 5 (stale, vague "exit 1") -> lane 9 (retry_of=5,
    # actionable detail) -> lane 11 (retry_of=9, actionable). Lane 9's
    # re-failure was produced on the VAGUE lane-5 feedback (the agent never saw
    # the violation) so it must NOT count; lane 11's was produced on lane 9's
    # ACTIONABLE feedback so it counts. Net = 1, so the task gets a convergence
    # attempt instead of false-escalating at 2.
    vague = "commit_hygiene: commit hook failed (exit 1)"
    _FakeQueueStore.items = [
        _FakeItem("failed", ["slice-14"], id=5, last_error=vague),
        _FakeItem("failed", ["slice-14"], id=9, last_error=_HYG,
                  retry_of_queue_item_id=5),
        _FakeItem("failed", ["slice-14"], id=11, last_error=_HYG,
                  retry_of_queue_item_id=9),
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {"slice-14": 1}


@pytest.mark.asyncio
async def test_refailure_on_actionable_source_counts():
    # An original lane with actionable detail (HEAD code captures stderr) whose
    # retry re-fails: the agent HAD the concrete error and still failed → counts.
    _FakeQueueStore.items = [
        _FakeItem("failed", ["t"], id=1, last_error=_HYG),
        _FakeItem("failed", ["t"], id=2, last_error=_HYG,
                  retry_of_queue_item_id=1),
    ]
    counts = await _commit_hygiene_retry_refailure_counts_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert counts == {"t": 1}


# ── Recovery-lane selection: newest failed lane (source + actionable detail) ─


_ACTIONABLE = (
    "commit_hygiene: commit hook failed (exit 1)\nstderr:\n"
    "TaskRow.test.tsx(146,82): Unexpected unicode character"
)


@pytest.mark.asyncio
async def test_recovery_uses_newest_failed_lane_not_oldest():
    # The items-5/9 shape after the first recovery retry: lane 5 is the STALE
    # original ("exit 1", no stderr) and lane 9 is its failed retry replacement
    # carrying the ACTUAL hook stderr. The recovery must select lane 9 (the
    # unreplaced chain head) — both because `_validate_retry_source` would refuse
    # lane 5 (already replaced by 9) AND because lane 9 carries the actionable
    # feedback the agent needs to fix the concrete hygiene violation.
    _FakeQueueStore.items = [
        _FakeItem(
            "failed", ["slice-14"], id=5,
            last_error="commit_hygiene: commit hook failed (exit 1)",
        ),
        _FakeItem(
            "failed", ["slice-14"], id=9,
            last_error=_ACTIONABLE, retry_of_queue_item_id=5,
        ),
    ]
    recovery = await _commit_hygiene_recovery_lanes_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert recovery["slice-14"].lane_id == 9  # newest = valid retry source
    assert "Unexpected unicode character" in recovery["slice-14"].hook_detail


@pytest.mark.asyncio
async def test_recovery_newest_selection_is_order_independent():
    # The newest lane wins regardless of the order items arrive from the store.
    items_low_first = [
        _FakeItem("failed", ["t"], id=5, last_error="commit_hygiene: a"),
        _FakeItem("failed", ["t"], id=9, last_error="commit_hygiene: b",
                  retry_of_queue_item_id=5),
    ]
    items_high_first = list(reversed(items_low_first))
    for items in (items_low_first, items_high_first):
        _FakeQueueStore.items = items
        recovery = await _commit_hygiene_recovery_lanes_for_group(
            _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
        )
        assert recovery["t"].lane_id == 9


@pytest.mark.asyncio
async def test_integrated_task_with_stale_failed_lanes_is_skipped_by_seam():
    # slice-2-002 shape after self-heal: failed lanes 7 + 10 AND integrated lane
    # 12. The recovery-lanes helper still returns it (it has failed lanes) AND
    # the integrated-lanes helper returns it. The seam combines them
    # (`recovery_lane is not None and tid not in integrated`) to SKIP recovery —
    # never re-dispatching an already-integrated task, which would source the
    # retry from lane 10 (now replaced by 12), get refused by
    # `_validate_retry_source`, and roll back the whole enqueue batch.
    _FakeQueueStore.items = [
        _FakeItem("failed", ["slice-2-002"], id=7,
                  last_error="commit_hygiene: commit hook failed (exit 1)"),
        _FakeItem("failed", ["slice-2-002"], id=10, last_error=_HYG,
                  retry_of_queue_item_id=7),
        _FakeItem("integrated", ["slice-2-002"], id=12,
                  retry_of_queue_item_id=10),
    ]
    recovery = await _commit_hygiene_recovery_lanes_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    integrated = await _integrated_lane_task_ids_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert "slice-2-002" in recovery     # has failed lanes -> recovery candidate
    assert "slice-2-002" in integrated   # but already integrated -> seam skips it


@pytest.mark.asyncio
async def test_recovery_single_failed_lane_is_selected():
    _FakeQueueStore.items = [
        _FakeItem("failed", ["t"], id=5, last_error=_ACTIONABLE),
    ]
    recovery = await _commit_hygiene_recovery_lanes_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert recovery["t"].lane_id == 5


# ── Sibling isolation: integrate non-blocked successes before the block ──────


class _FakeArtifacts:
    def __init__(self) -> None:
        self.puts: dict[str, str] = {}

    async def put(self, key, value, *, feature=None):
        self.puts[key] = value


class _FakeDrainResult:
    def __init__(self, task_id, succeeded):
        self.task_id = task_id
        self.succeeded = succeeded
        self.item_id = 100
        self.terminal_status = "integrated" if succeeded else "failed"
        self.failure_class = None if succeeded else "commit_hygiene"


def _success(task_id: str) -> ImplementationResult:
    return ImplementationResult(
        task_id=task_id,
        summary="Sandbox patch pending durable merge queue.",
        status="completed",
        notes=f"patch_summary_ids=1; {_PENDING_DURABLE_MERGE_QUEUE_NOTE}",
    )


def _blocked(task_id: str) -> ImplementationResult:
    return ImplementationResult(
        task_id=task_id,
        summary="Runtime prompt too large for model context window.",
        status="blocked",
    )


@pytest.mark.asyncio
async def test_sibling_isolation_enqueues_only_nonblocked_successes(monkeypatch):
    captured: dict = {}

    async def _fake_enqueue(runner, feature, pending, **kw):
        captured["pending_task_ids"] = [r.task_id for r in pending]
        captured["retry_source"] = kw.get("retry_source_by_task")
        return [201]

    async def _fake_drain(runner, feature, **kw):
        return [_FakeDrainResult("slice-2-002", True)]

    logged: list = []

    async def _fake_log(runner, fid, etype, phase, **kw):
        logged.append(etype)

    monkeypatch.setattr(impl, "_enqueue_durable_merge_queue_for_results", _fake_enqueue)
    monkeypatch.setattr(impl, "_drain_durable_merge_queue_for_feature", _fake_drain)
    monkeypatch.setattr(impl, "_log_feature_event", _fake_log)

    arts = _FakeArtifacts()
    runner = SimpleNamespace(services={}, artifacts=arts)

    ids = await _integrate_nonblocked_successes_before_block(
        runner,
        _feature(),
        [_success("slice-2-002"), _blocked("slice-14")],
        dag_sha256="dag-sha",
        group_idx=78,
        contracts_by_task_id={},
        feature_root=None,
        retry_source_by_task={"slice-2-002": 7},
    )

    # The blocked sibling is excluded; only the durable-merge success is enqueued.
    assert captured["pending_task_ids"] == ["slice-2-002"]
    assert captured["retry_source"] == {"slice-2-002": 7}
    assert ids == [201]
    # Its pending-merge marker is persisted so the resume recognizes the lane.
    assert "dag-task-pending-merge:slice-2-002" in arts.puts
    assert "dag-task-pending-merge:slice-14" not in arts.puts
    assert "dag_sibling_isolation_integrated" in logged


@pytest.mark.asyncio
async def test_sibling_isolation_noop_without_durable_successes(monkeypatch):
    # Only blocked results → nothing to integrate → no enqueue, no markers.
    enqueue_called = False

    async def _fake_enqueue(*a, **k):
        nonlocal enqueue_called
        enqueue_called = True
        return []

    monkeypatch.setattr(impl, "_enqueue_durable_merge_queue_for_results", _fake_enqueue)
    arts = _FakeArtifacts()
    runner = SimpleNamespace(services={}, artifacts=arts)

    ids = await _integrate_nonblocked_successes_before_block(
        runner,
        _feature(),
        [_blocked("slice-14")],
        dag_sha256="dag-sha",
        group_idx=78,
        contracts_by_task_id={},
        feature_root=None,
        retry_source_by_task=None,
    )
    assert ids == []
    assert enqueue_called is False
    assert arts.puts == {}


@pytest.mark.asyncio
async def test_sibling_isolation_enqueue_error_is_best_effort(monkeypatch):
    # An enqueue failure must NOT raise — the caller still surfaces the blocked
    # terminal and a resume re-drives the idempotent enqueue.
    async def _fake_enqueue(*a, **k):
        raise impl._MergeQueueEnqueueError("simulated enqueue failure")

    monkeypatch.setattr(impl, "_enqueue_durable_merge_queue_for_results", _fake_enqueue)
    arts = _FakeArtifacts()
    runner = SimpleNamespace(services={}, artifacts=arts)

    ids = await _integrate_nonblocked_successes_before_block(
        runner,
        _feature(),
        [_success("slice-2-002"), _blocked("slice-14")],
        dag_sha256="dag-sha",
        group_idx=78,
        contracts_by_task_id={},
        feature_root=None,
        retry_source_by_task=None,
    )
    assert ids == []
    # No markers written when the enqueue itself failed.
    assert arts.puts == {}


# ── Typed-recoverable drain-failure allowlist ────────────────────────────────
#
# `_drain_failure_is_commit_hygiene_rerun` decides whether a merge-queue drain
# that left failed lanes is the self-healing checkpoint the orchestrator may
# auto-continue (commit_hygiene + budget remaining) vs a genuine halt. It reuses
# the SAME budget accounting (`_commit_hygiene_recovery_plan` + refailure counts
# + the runtime-block marker) so it never diverges from the escalate decision.


class _FakeRecoverableLane:
    """The fields the typed-recoverable allowlist inspects on a drain result."""

    def __init__(self, task_ids, failure_class="commit_hygiene"):
        self.task_ids = task_ids
        self.failure_class = failure_class


class _FakeArtifactsGet:
    def __init__(self, block_counts=None):
        # {task_id: runtime-block-rerun count} → the block-marker artifact value.
        self._block_counts = block_counts or {}

    async def get(self, key, *, feature=None):
        for tid, count in self._block_counts.items():
            if key == _commit_hygiene_rerun_block_marker_key(tid):
                return str(count)
        return None


def _drain_runner(block_counts=None):
    return SimpleNamespace(services={}, artifacts=_FakeArtifactsGet(block_counts))


@pytest.mark.asyncio
async def test_drain_failure_recoverable_when_all_commit_hygiene_with_budget():
    # All failed lanes are commit_hygiene with budget remaining (1 < 4) → the
    # next re-entry would "rerun" → auto-continue allowed.
    recoverable = await impl._drain_failure_is_commit_hygiene_rerun(
        failed_lanes=[_FakeRecoverableLane(["slice-1-TASK-12"])],
        runner=_drain_runner(),
        feature=_feature(),
        refailure_counts={"slice-1-TASK-12": 1},
        max_reruns=4,
    )
    assert recoverable is True


@pytest.mark.asyncio
async def test_drain_failure_not_recoverable_with_a_non_hygiene_lane():
    # A single non-commit_hygiene failed lane (e.g. an apply conflict) is NOT a
    # known self-heal → genuine halt even though a sibling lane is hygiene.
    recoverable = await impl._drain_failure_is_commit_hygiene_rerun(
        failed_lanes=[
            _FakeRecoverableLane(["slice-1-TASK-12"]),
            _FakeRecoverableLane(["slice-2"], failure_class="apply_conflict"),
        ],
        runner=_drain_runner(),
        feature=_feature(),
        refailure_counts={},
        max_reruns=4,
    )
    assert recoverable is False


@pytest.mark.asyncio
async def test_drain_failure_not_recoverable_when_budget_exhausted():
    # refailures (3) + runtime-block reruns (1) == 4 == max → escalate, not rerun
    # → genuine halt (mirrors the escalate terminal in the dispatch loop).
    recoverable = await impl._drain_failure_is_commit_hygiene_rerun(
        failed_lanes=[_FakeRecoverableLane(["slice-1-TASK-12"])],
        runner=_drain_runner(block_counts={"slice-1-TASK-12": 1}),
        feature=_feature(),
        refailure_counts={"slice-1-TASK-12": 3},
        max_reruns=4,
    )
    assert recoverable is False


@pytest.mark.asyncio
async def test_drain_failure_not_recoverable_for_empty_or_untracked_lane():
    # No failed lanes → not a recoverable terminal.
    assert (
        await impl._drain_failure_is_commit_hygiene_rerun(
            failed_lanes=[],
            runner=_drain_runner(),
            feature=_feature(),
            refailure_counts={},
            max_reruns=4,
        )
        is False
    )
    # A commit_hygiene lane with no task_ids can't be budget-checked → halt.
    assert (
        await impl._drain_failure_is_commit_hygiene_rerun(
            failed_lanes=[_FakeRecoverableLane([])],
            runner=_drain_runner(),
            feature=_feature(),
            refailure_counts={},
            max_reruns=4,
        )
        is False
    )


# ── Evidence-gated, allow-list-bounded write-set widening ────────────────────
#
# The commit-hygiene recovery loop can self-heal a blocker whose only fix is a
# carve-out in a shared-config file OUTSIDE the task's write-set (e.g. a
# `local/code-import-patterns` lint failure on the task's own new `impl/**`
# files, fixed by editing `eslint.config.js`). Triggering is gated on the
# product hook's OWN evidence — the rule fired on the task's OWN subtree — and
# the widened set is bounded by a static allow-list of shared-config files,
# `modify`-only, must pre-exist. Every other path still fails closed.


_REPO = "repo-app"


def _file_rule(path: str, *, modify: bool = False, create: bool = False) -> ContractPathRule:
    return ContractPathRule(
        repo_id=_REPO,
        path=path,
        match_kind="file",
        intent="modify" if modify else "create",
        required=False,
        allow_modify=modify,
        allow_create=create,
        allow_delete=False,
        source="test",
    )


def _dir_rule(path: str) -> ContractPathRule:
    return ContractPathRule(
        repo_id=_REPO,
        path=path,
        match_kind="directory",
        intent="create",
        required=False,
        allow_modify=True,
        allow_create=True,
        allow_delete=False,
        source="test",
    )


def _contract(allowed_paths: list[ContractPathRule]) -> TaskDeliverableContract:
    normalized = {
        "feature_id": "8ac124d6",
        "dag_sha256": "dag-sha",
        "group_idx": 79,
        "task_id": "TASK-12",
        "repo_id": _REPO,
        "allowed_paths": [r.model_dump(mode="json") for r in allowed_paths],
    }
    return TaskDeliverableContract(
        id=101,
        feature_id="8ac124d6",
        dag_sha256="dag-sha",
        source_dag_artifact_id=1,
        source_dag_sha256="src-dag-sha",
        group_idx=79,
        task_id="TASK-12",
        repo_id=_REPO,
        repo_path="app",
        required_paths=[],
        allowed_paths=allowed_paths,
        read_only_paths=[],
        forbidden_paths=[],
        generated_outputs=[],
        acceptance_criteria=[],
        verification_gates=[],
        execution_policy=ContractExecutionPolicy(
            write_set_mode="declared",
            sandbox_isolation="per_task",
            merge_admission="single_task",
            requires_contract_verdict=True,
            repair_may_broaden_scope=False,
            phased_rollout_allowed=False,
        ),
        non_goals=[],
        dependency_task_ids=[],
        unknown_write_set=False,
        compile_warnings=[],
        normalized_contract_json=normalized,
        contract_digest="digest-original",
        status="active",
        idempotency_key="idem-original",
    )


def _hook_detail(*abs_paths: str, rule: str = "local/code-import-patterns") -> str:
    lines = ["commit_hygiene: commit hook failed (exit 1)", "stderr:"]
    for path in abs_paths:
        lines.append(
            f"{path}: line 3, col 1, Warning - disallowed import ({rule})"
        )
    return "\n".join(lines)


# ── Parse helper ─────────────────────────────────────────────────────────────


def test_parse_hook_offenders_extracts_paths_and_rules():
    detail = _hook_detail(
        "/repo/app/impl/selection/a.ts",
        "/repo/app/impl/selection/b.ts",
    )
    paths, rules = _parse_commit_hygiene_hook_offenders(detail)
    assert paths == [
        "/repo/app/impl/selection/a.ts",
        "/repo/app/impl/selection/b.ts",
    ]
    assert rules == ["local/code-import-patterns"]


def test_parse_hook_offenders_ignores_unparseable_lines():
    paths, rules = _parse_commit_hygiene_hook_offenders(
        "commit_hygiene: commit hook failed (exit 1)\nstderr:\n(no offenders)"
    )
    assert paths == []
    assert rules == []


# ── Trigger / boundary (a)-(d) ───────────────────────────────────────────────


def test_widen_grants_eslint_when_local_rule_and_offenders_in_contract(tmp_path: Path):
    # (a) local/* rule + EVERY offender inside the task's contract → grant
    # eslint.config.js (modify), but only because the config file exists.
    repo = tmp_path / "app"
    (repo / "impl" / "selection").mkdir(parents=True)
    (repo / "eslint.config.js").write_text("module.exports = [];")
    contract = _contract([_dir_rule("impl/selection")])
    detail = _hook_detail(str(repo / "impl" / "selection" / "a.ts"))
    granted = _commit_hygiene_widening_grant(
        contract=contract, hook_detail=detail, repo_root=repo,
    )
    assert granted == ["eslint.config.js"]


def test_no_widen_when_offender_outside_contract(tmp_path: Path):
    # (b) an offender OUTSIDE the task's contract is a GENUINE violation → no grant.
    repo = tmp_path / "app"
    (repo / "impl" / "selection").mkdir(parents=True)
    (repo / "src").mkdir(parents=True)
    (repo / "eslint.config.js").write_text("module.exports = [];")
    contract = _contract([_dir_rule("impl/selection")])
    detail = _hook_detail(
        str(repo / "impl" / "selection" / "a.ts"),   # in-contract
        str(repo / "src" / "stray.ts"),               # OUTSIDE the contract
    )
    granted = _commit_hygiene_widening_grant(
        contract=contract, hook_detail=detail, repo_root=repo,
    )
    assert granted == []


def test_no_widen_when_no_local_rule(tmp_path: Path):
    # (c) no `local/*` rule named → no grant (not an evidence-gated trigger).
    repo = tmp_path / "app"
    (repo / "impl" / "selection").mkdir(parents=True)
    (repo / "eslint.config.js").write_text("module.exports = [];")
    contract = _contract([_dir_rule("impl/selection")])
    detail = _hook_detail(
        str(repo / "impl" / "selection" / "a.ts"),
        rule="prettier/prettier",   # not a local/* hygiene rule
    )
    granted = _commit_hygiene_widening_grant(
        contract=contract, hook_detail=detail, repo_root=repo,
    )
    assert granted == []


def test_no_widen_when_granted_config_absent(tmp_path: Path):
    # (d) the granted config path must EXIST in the repo — it never does for a
    # repo without the shared eslint config, so no grant.
    repo = tmp_path / "app"
    (repo / "impl" / "selection").mkdir(parents=True)
    # NOTE: no eslint.config.js written.
    contract = _contract([_dir_rule("impl/selection")])
    detail = _hook_detail(str(repo / "impl" / "selection" / "a.ts"))
    granted = _commit_hygiene_widening_grant(
        contract=contract, hook_detail=detail, repo_root=repo,
    )
    assert granted == []


def test_no_widen_when_config_already_permitted(tmp_path: Path):
    # The augmented builder returns None when the only granted path is already
    # in the contract (nothing to add).
    repo = tmp_path / "app"
    (repo / "impl" / "selection").mkdir(parents=True)
    (repo / "eslint.config.js").write_text("module.exports = [];")
    contract = _contract(
        [_dir_rule("impl/selection"), _file_rule("eslint.config.js", modify=True)]
    )
    detail = _hook_detail(str(repo / "impl" / "selection" / "a.ts"))
    granted = _commit_hygiene_widening_grant(
        contract=contract, hook_detail=detail, repo_root=repo,
    )
    assert granted == ["eslint.config.js"]
    # ...but augmenting yields None: the config is ALREADY permitted.
    assert _commit_hygiene_augmented_contract(
        contract=contract, granted_config_paths=granted,
    ) is None


def test_widening_grant_fails_closed_without_repo_root():
    contract = _contract([_dir_rule("impl/selection")])
    assert _commit_hygiene_widening_grant(
        contract=contract,
        hook_detail=_hook_detail("/x/impl/selection/a.ts"),
        repo_root=None,
    ) == []


# ── Augmented contract: validates the widened path, original still rejects ───


def _snapshot_for(contract: TaskDeliverableContract) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        feature_id=contract.feature_id,
        dag_sha256=contract.dag_sha256,
        group_idx=contract.group_idx,
        repo_id=contract.repo_id,
        canonical_path="/tmp/app",
        workspace_relative_path="app",
        case_sensitivity="case_sensitive",
        present_paths=["eslint.config.js", "impl/selection/a.ts"],
    )


def test_augmented_contract_permits_config_modify_original_rejects():
    original = _contract([_dir_rule("impl/selection")])
    augmented = _commit_hygiene_augmented_contract(
        contract=original, granted_config_paths=["eslint.config.js"],
    )
    assert augmented is not None

    # Augmented allowed_paths = the originals + the eslint.config.js modify rule.
    paths = {(r.path, r.allow_modify) for r in augmented.allowed_paths}
    assert ("impl/selection/", True) in paths
    assert ("eslint.config.js", True) in paths
    assert {r.path for r in original.allowed_paths} <= {
        r.path for r in augmented.allowed_paths
    }

    # Distinct idempotency_key + distinct contract_digest (required so
    # put_task_contract supersedes the narrow original instead of conflicting).
    assert augmented.idempotency_key != original.idempotency_key
    assert augmented.contract_digest != original.contract_digest
    assert augmented.status == "active"
    assert augmented.id is None  # a NEW row, not the original

    compiler = ContractCompiler()

    # A modify of eslint.config.js is REJECTED under the original (out of scope)...
    original_violations: list[dict] = []
    compiler._validate_patch_operation(
        original,
        "eslint.config.js",
        "modify",
        "case_sensitive",
        original_violations,
        base_present_paths={"eslint.config.js"},
    )
    assert any(
        v["failure_type"] == "outside_allowed_paths" for v in original_violations
    )

    # ...and PERMITTED under the augmented contract.
    augmented_violations: list[dict] = []
    compiler._validate_patch_operation(
        augmented,
        "eslint.config.js",
        "modify",
        "case_sensitive",
        augmented_violations,
        base_present_paths={"eslint.config.js"},
    )
    assert augmented_violations == []

    # The original deliverable subtree still validates under the augmented one.
    subtree_violations: list[dict] = []
    compiler._validate_patch_operation(
        augmented,
        "impl/selection/a.ts",
        "create",
        "case_sensitive",
        subtree_violations,
        base_present_paths=set(),
    )
    assert subtree_violations == []


def test_augmented_contract_full_verdict_round_trip():
    # End-to-end through validate_patch: a patch that creates the task's own file
    # AND modifies eslint.config.js is APPROVED under the augmented contract but
    # produces a contract_violation under the original.
    original = _contract([_dir_rule("impl/selection")])
    augmented = _commit_hygiene_augmented_contract(
        contract=original, granted_config_paths=["eslint.config.js"],
    )
    assert augmented is not None
    compiler = ContractCompiler()
    snapshot = _snapshot_for(original)
    patch = PatchSummary(
        sandbox_id="sbx-1",
        repo_id=_REPO,
        created_paths=["impl/selection/a.ts"],
        modified_paths=["eslint.config.js"],
        changed_paths=["impl/selection/a.ts", "eslint.config.js"],
    )

    original_verdict = compiler.validate_patch(original, patch, snapshot)
    assert not original_verdict.approved
    assert "modify_outside_allowed_paths" in original_verdict.violation_codes

    augmented_verdict = compiler.validate_patch(augmented, patch, snapshot)
    assert augmented_verdict.approved, augmented_verdict.violations


# ── Feedback steering ────────────────────────────────────────────────────────


def test_feedback_mentions_granted_config_when_widened():
    lane = _CommitHygieneRecoveryLane(
        task_id="TASK-12",
        lane_id=12,
        failure_class="commit_hygiene",
        hook_detail=_hook_detail("/repo/app/impl/selection/a.ts"),
    )
    text = _commit_hygiene_recovery_feedback(
        lane, widened_config_paths=["eslint.config.js"],
    )
    assert "eslint.config.js" in text
    assert "modify-only" in text
    # The bans stay intact.
    assert "--no-verify" in text
    assert "eslint-disable" in text


def test_feedback_omits_carveout_when_not_widened():
    lane = _CommitHygieneRecoveryLane(
        task_id="TASK-12",
        lane_id=12,
        failure_class="commit_hygiene",
        hook_detail=_hook_detail("/repo/app/impl/selection/a.ts"),
    )
    text = _commit_hygiene_recovery_feedback(lane)
    assert "Shared-config carve-out permitted" not in text
    # The ban on bypassing the hook is still present.
    assert "--no-verify" in text
