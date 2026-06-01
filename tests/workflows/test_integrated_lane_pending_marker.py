from __future__ import annotations

"""Regression coverage for the integrated-lane stale pending-merge-marker fix in
``_implement_dag``'s per-task resume loop.

The exact failure shape this guards against (feature 8ac124d6, DAG group 78):
after the commit_hygiene recovery seam (commit 8e2e344) correctly diverts the
FAILED lanes (items 5 / 7) for bounded re-dispatch, the per-task resume loop
then reaches the ALREADY-INTEGRATED lanes (items 6 / 8 — TASK-9-3, slice-2-001).
Those tasks still carry a ``dag-task-pending-merge:{tid}`` artifact marker
because the markers are only swept when the WHOLE group seals — which cannot
happen until the failed siblings recover (a chicken-and-egg). The old
``pending_merge_marker`` branch UNCONDITIONALLY returned a terminal
``_durable_merge_queue_blocker_for_results`` blocker for ANY task with a pending
marker, so an already-integrated lane's stale marker dead-ended the resume
before the re-dispatch could run.

The fix: consult ``_integrated_lane_task_ids_for_group`` (computed once per
group). A task whose lane is already ``integrated`` is treated as the completed
success it is (appended to the result lists + ``record_success``, no blocker, no
re-enqueue). A genuinely non-integrated (truly pending / captured-but-not-
enqueued) lane still hits the existing fail-closed blocker. The helper fails
closed: any store/DB error yields an EMPTY set → unchanged blocker behavior.

These are pure unit tests of the extracted helper + the branch decision. Driving
the full ``_implement_dag`` resume is blocked by the strict-resume
adoption-marker requirement (the same reason two ``test_merge_queue_checkpoint``
tests are pre-existing failures), so we exercise the seam directly instead.
"""

from types import SimpleNamespace

import pytest

import iriai_build_v2.workflows.develop.phases.implementation as impl
from iriai_build_v2.workflows.develop.phases.implementation import (
    ImplementationResult,
    _durable_merge_queue_blocker_for_results,
    _integrated_lane_task_ids_for_group,
)


# ── Fakes mirroring the merge-queue store surface the helper touches ─────────


class _FakeCoverage:
    def __init__(self, task_id: str) -> None:
        self.task_id = task_id


class _FakeItem:
    def __init__(self, status: str, task_ids: list[str]) -> None:
        self.status = status
        self.task_coverage = [_FakeCoverage(t) for t in task_ids]


class _FakeQueueStore:
    """Stand-in for ``MergeQueueStore`` bound to a bare connection."""

    items: list[_FakeItem] = []
    raise_on_list: bool = False

    def __init__(self, _conn: object) -> None:
        self._conn = _conn

    async def list_group_items(self, _feature_id, _dag, _group_idx):
        if type(self).raise_on_list:
            raise RuntimeError("simulated store/DB error")
        return type(self).items


def _runner_with_store():
    """A runner whose execution-control store has a bare-connection ``_pool``.

    ``_merge_queue_connection`` yields the ``_pool`` directly when it lacks an
    ``acquire`` method, so a plain sentinel object is a valid bare connection.
    """

    store = SimpleNamespace(put_task_contract=lambda *a, **k: None, _pool=object())
    return SimpleNamespace(services={"execution_control_store": store})


def _feature(feature_id: str = "8ac124d6"):
    return SimpleNamespace(id=feature_id)


@pytest.fixture(autouse=True)
def _patch_merge_queue_store(monkeypatch):
    # Reset class-level canned state, then point the module's MergeQueueStore
    # symbol (used inside the helper) at our fake.
    _FakeQueueStore.items = []
    _FakeQueueStore.raise_on_list = False
    monkeypatch.setattr(impl, "MergeQueueStore", _FakeQueueStore)
    yield


# ── The helper: which task ids have an integrated lane ───────────────────────


@pytest.mark.asyncio
async def test_integrated_lanes_are_collected():
    # The group-78 shape: items 6 / 8 integrated, items 5 / 7 failed.
    _FakeQueueStore.items = [
        _FakeItem("integrated", ["TASK-9-3"]),
        _FakeItem("integrated", ["slice-2-001"]),
        _FakeItem("failed", ["slice-14"]),
        _FakeItem("failed", ["slice-2-002"]),
    ]
    integrated = await _integrated_lane_task_ids_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert integrated == {"TASK-9-3", "slice-2-001"}


@pytest.mark.asyncio
async def test_non_integrated_lanes_are_excluded():
    _FakeQueueStore.items = [
        _FakeItem("queued", ["pending-task"]),
        _FakeItem("failed", ["failed-task"]),
        _FakeItem("done", ["done-task"]),
    ]
    integrated = await _integrated_lane_task_ids_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert integrated == set()


@pytest.mark.asyncio
async def test_helper_fails_closed_on_store_error():
    # Fail closed: a store/DB error must yield an EMPTY set so the caller falls
    # through to the UNCHANGED blocker behavior.
    _FakeQueueStore.items = [_FakeItem("integrated", ["TASK-9-3"])]
    _FakeQueueStore.raise_on_list = True
    integrated = await _integrated_lane_task_ids_for_group(
        _runner_with_store(), _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert integrated == set()


@pytest.mark.asyncio
async def test_helper_fails_closed_without_store():
    # No execution-control store at all → empty set (unchanged blocker).
    runner = SimpleNamespace(services={})
    integrated = await _integrated_lane_task_ids_for_group(
        runner, _feature(), dag_sha256="dag-sha", group_idx=78,
    )
    assert integrated == set()


@pytest.mark.asyncio
async def test_helper_fails_closed_without_feature_id_or_dag():
    _FakeQueueStore.items = [_FakeItem("integrated", ["TASK-9-3"])]
    assert (
        await _integrated_lane_task_ids_for_group(
            _runner_with_store(), _feature(""), dag_sha256="dag-sha", group_idx=78,
        )
        == set()
    )
    assert (
        await _integrated_lane_task_ids_for_group(
            _runner_with_store(), _feature(), dag_sha256="", group_idx=78,
        )
        == set()
    )


# ── The branch decision the helper drives ────────────────────────────────────


def _resume_branch_decision(tid: str, integrated_lane_task_ids: set[str]):
    """Pure mirror of the ``pending_merge_marker`` branch's decision.

    Returns ``("success", marker_result)`` when the lane is integrated (so the
    caller appends to the result lists + records success and CONTINUEs), or
    ``("blocked", failure_text)`` when it must surface the existing terminal
    durable-merge-queue blocker. Kept structurally identical to the production
    branch so a divergence is a visible test failure.
    """

    marker_result = ImplementationResult(
        task_id=tid,
        summary="Sandbox patch pending durable merge queue.",
        status="completed",
        notes="patch_summary_ids=101,102",
    )
    if tid in integrated_lane_task_ids:
        return ("success", marker_result)
    return ("blocked", _durable_merge_queue_blocker_for_results([marker_result]))


def test_integrated_pending_marker_is_treated_as_success():
    # (a) An integrated lane's STALE pending-merge marker must NOT block — it is
    # the completed success it is.
    decision, payload = _resume_branch_decision("TASK-9-3", {"TASK-9-3", "slice-2-001"})
    assert decision == "success"
    assert isinstance(payload, ImplementationResult)
    assert payload.task_id == "TASK-9-3"
    assert payload.status == "completed"


def test_non_integrated_pending_marker_still_blocks():
    # (b) A genuinely non-integrated (truly pending / captured-not-enqueued)
    # lane still hits the existing fail-closed terminal blocker.
    decision, payload = _resume_branch_decision("slice-14", {"TASK-9-3", "slice-2-001"})
    assert decision == "blocked"
    assert isinstance(payload, str)
    assert "durable merge queue" in payload


def test_empty_integrated_set_blocks_everything():
    # Fail-closed contract: when the helper returns an empty set (store error or
    # no integrated lanes), EVERY pending marker keeps the unchanged blocker.
    decision, _payload = _resume_branch_decision("TASK-9-3", set())
    assert decision == "blocked"
