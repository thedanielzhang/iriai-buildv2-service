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

from types import SimpleNamespace

import pytest

import iriai_build_v2.workflows.develop.phases.implementation as impl
from iriai_build_v2.workflows.develop.phases.implementation import (
    _PENDING_DURABLE_MERGE_QUEUE_NOTE,
    ImplementationResult,
    _commit_hygiene_recovery_lanes_for_group,
    _commit_hygiene_retry_refailure_counts_for_group,
    _commit_hygiene_rerun_block_marker_key,
    _integrate_nonblocked_successes_before_block,
    _integrated_lane_task_ids_for_group,
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
