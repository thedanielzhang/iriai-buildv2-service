"""Pinning tests — operator-overridden group checkpoint acceptance (W-OG seal).

An empty-write-set group completed by operator fiat has no write product:
its authorized-repo set is empty, so `_current_feature_repo_heads` returns ""
and the legacy/queue proof paths structurally cannot exist. The reader branch
under test accepts such a checkpoint ONLY when every checkpoint result AND
every durable `dag-task:{tid}` row is a terminal operator-override completion;
everything else falls through to the unchanged fail-closed paths.
"""
from __future__ import annotations

import pytest

from iriai_build_v2.models.outputs import ImplementationResult
from iriai_build_v2.workflows.develop.execution.operator_override import (
    OPERATOR_OVERRIDE_RESULT_NOTE,
)
from iriai_build_v2.workflows.develop.phases.implementation import (
    _checkpoint_results_all_operator_override,
    _dag_group_checkpoint_is_fresh,
)


class _Artifacts:
    def __init__(self, data: dict[str, str]):
        self._data = dict(data)

    async def get(self, key: str, feature=None):
        return self._data.get(key)


class _Runner:
    def __init__(self, artifacts: _Artifacts):
        self.artifacts = artifacts
        self.services: dict = {}


def _override_result(task_id: str = "TASK-GATE-0") -> ImplementationResult:
    return ImplementationResult(
        task_id=task_id,
        summary="OPERATOR-OVERRIDE GATE COMPLETION (test)",
        status="completed",
        files_created=[],
        files_modified=[],
        commit_hash="",
        notes=f"reason=test\n{OPERATOR_OVERRIDE_RESULT_NOTE}",
    )


def _normal_result(task_id: str = "TASK-GATE-0") -> ImplementationResult:
    return ImplementationResult(
        task_id=task_id,
        summary="normal completion",
        status="completed",
        files_created=[],
        files_modified=[],
        commit_hash="abc123",
        notes="",
    )


def _checkpoint(results: list[ImplementationResult], task_ids: list[str]):
    return {
        "group_idx": 2,
        "task_ids": task_ids,
        "results": [r.model_dump() for r in results],
        "verdict": "approved",
        "commit_hash": "",
        "dag_sha256": "d" * 64,
    }


def test_all_operator_override_detector() -> None:
    assert _checkpoint_results_all_operator_override(
        _checkpoint([_override_result()], ["TASK-GATE-0"])
    )
    # empty results: never override-accepted
    assert not _checkpoint_results_all_operator_override(
        _checkpoint([], ["TASK-GATE-0"])
    )
    # mixed normal+override: not override-accepted
    assert not _checkpoint_results_all_operator_override(
        _checkpoint(
            [_override_result("A"), _normal_result("B")], ["A", "B"]
        )
    )
    # normal-only: not override-accepted
    assert not _checkpoint_results_all_operator_override(
        _checkpoint([_normal_result()], ["TASK-GATE-0"])
    )


@pytest.mark.asyncio
async def test_override_group_checkpoint_accepted_on_durable_rows() -> None:
    stored = _override_result()
    runner = _Runner(_Artifacts({"dag-task:TASK-GATE-0": stored.model_dump_json()}))
    assert await _dag_group_checkpoint_is_fresh(
        runner,
        feature=None,
        group_idx=2,
        group_task_ids=["TASK-GATE-0"],
        dag_sha256="d" * 64,
        checkpoint=_checkpoint([_override_result()], ["TASK-GATE-0"]),
    )


@pytest.mark.asyncio
async def test_override_group_checkpoint_rejected_without_durable_row() -> None:
    runner = _Runner(_Artifacts({}))
    assert not await _dag_group_checkpoint_is_fresh(
        runner,
        feature=None,
        group_idx=2,
        group_task_ids=["TASK-GATE-0"],
        dag_sha256="d" * 64,
        checkpoint=_checkpoint([_override_result()], ["TASK-GATE-0"]),
    )


@pytest.mark.asyncio
async def test_override_group_checkpoint_rejected_on_tampered_row() -> None:
    # checkpoint claims override, but the durable row is a NORMAL completion
    runner = _Runner(
        _Artifacts({"dag-task:TASK-GATE-0": _normal_result().model_dump_json()})
    )
    assert not await _dag_group_checkpoint_is_fresh(
        runner,
        feature=None,
        group_idx=2,
        group_task_ids=["TASK-GATE-0"],
        dag_sha256="d" * 64,
        checkpoint=_checkpoint([_override_result()], ["TASK-GATE-0"]),
    )


@pytest.mark.asyncio
async def test_non_override_checkpoint_still_requires_commit_hash() -> None:
    # unchanged fail-closed behavior: normal results + empty commit_hash → stale
    runner = _Runner(_Artifacts({"dag-task:TASK-GATE-0": _normal_result().model_dump_json()}))
    assert not await _dag_group_checkpoint_is_fresh(
        runner,
        feature=None,
        group_idx=2,
        group_task_ids=["TASK-GATE-0"],
        dag_sha256="d" * 64,
        checkpoint=_checkpoint([_normal_result()], ["TASK-GATE-0"]),
    )
