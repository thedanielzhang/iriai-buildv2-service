"""Slice 11a — extraction proof for `execution/types.py`.

Verifies the doc-11 § "How To Use This Map" four-question contract for the
types extraction:

1. What behavior moved: the cross-module Pydantic / dataclass request and
   outcome types + the `SandboxWorkflowBlocker` typed signal + the
   `_bounded_commit_output` / `COMMIT_FAILURE_OUTPUT_LIMIT` serialization
   helper moved from `workflows/develop/phases/implementation.py` to
   `workflows/develop/execution/types.py`.
2. Which legacy import names still work: every existing
   `from iriai_build_v2.workflows.develop.phases.implementation import X`
   keeps resolving to the SAME object as the canonical definition in
   `execution/types.py` (the shim is `is`-equivalent, not a copy).
3. Which targeted tests prove the new facade and the compatibility shim:
   THIS file is one of them; it pins every moved name's shim equivalence
   and smoke-tests representative type instantiations.
4. Why is the PR still refactor-only: nothing else moves. The shim re-
   exports preserve every importer + every monkeypatch target; the new
   module has no behavior beyond the moved class/value bodies.
"""

from __future__ import annotations

import pytest


# Each tuple is (legacy_import_module, type_attribute_name).
MOVED_TYPE_NAMES = [
    "DagExecutionOutcome",
    "RuntimeSandboxTaskBinding",
    "SandboxWorkflowBlocker",
    "CommitRepoOutcome",
    "WorkflowCommitError",
    "CommitFailureLocation",
    "CommitForbiddenPathMatch",
    "DagDirectRepairRoute",
    "DagAuthorityGateOutcome",
    "PlannedBugGroup",
    "PlannedBugDispatch",
    "DagTaskDriftRoute",
    "DagContradictionResolution",
    "DagContradictionResolutionValidation",
    "DagContradictionHandoffOutcome",
    "WorktreeRegistryRepo",
    "WorktreeRegistry",
    "WorkspaceAuthorityCompatibilityOutcome",
    "TaskContractCompileOutcome",
    "TaskContractCommitGuardOutcome",
    "_RepoNeed",
    "_MergeQueueEnqueueError",
    "_MergeQueueDrainResult",
    "_MergeQueueCheckpointResult",
    "_MergeQueueResumeRecovery",
    "DagVerifyLensSpec",
    "WorktreeAliasPathInfo",
    "DagTaskReconcileOutcome",
    "DagTaskSpecReconcileOutcome",
]

MOVED_CONSTANTS = [
    "COMMIT_FAILURE_OUTPUT_LIMIT",
    "_SANDBOX_WORKFLOW_BLOCKER_MARKER",
]

MOVED_HELPERS = [
    "_bounded_commit_output",
]


@pytest.mark.parametrize("name", MOVED_TYPE_NAMES)
def test_shim_re_export_is_same_object_as_new_canonical(name: str) -> None:
    """Every moved type imported via the OLD path is the SAME object
    as the import via the NEW canonical path. Proves the shim is a re-
    export, not a copy. Locks the monkeypatch target equivalence —
    `monkeypatch.setattr(implementation_module, name, ...)` will mutate
    the SAME class object that any direct
    `from execution.types import name` reader sees.
    """

    from iriai_build_v2.workflows.develop import execution as execution_pkg
    from iriai_build_v2.workflows.develop.execution import types as types_mod
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(types_mod, name)
    assert legacy is canonical, (
        f"shim drift: implementation.{name} is not the same object as "
        f"execution.types.{name}"
    )
    # The class' canonical __module__ should now point at the new module.
    if hasattr(canonical, "__module__"):
        assert canonical.__module__ == (
            "iriai_build_v2.workflows.develop.execution.types"
        ), (
            f"{name}.__module__ = {canonical.__module__!r}; expected the new "
            "types-module path"
        )
    # `execution_pkg` is imported only to ensure the package import chain
    # works end-to-end (no side-effect import errors).
    _ = execution_pkg


@pytest.mark.parametrize("name", MOVED_CONSTANTS + MOVED_HELPERS)
def test_shim_re_export_constants_and_helpers_match(name: str) -> None:
    """Module-level constants and helpers that participate in the type
    contracts also re-export through the shim with identity preserved.
    """

    from iriai_build_v2.workflows.develop.execution import types as types_mod
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    legacy = getattr(impl_mod, name)
    canonical = getattr(types_mod, name)
    # For strings and small ints, Python may not preserve identity across
    # imports in every interpreter version; equality is the meaningful
    # check. For the helper callable, identity is preserved.
    if callable(canonical) and not isinstance(canonical, type):
        assert legacy is canonical
    else:
        assert legacy == canonical


def test_dag_execution_outcome_smoke_instantiation() -> None:
    """`DagExecutionOutcome` is a Pydantic-free slots dataclass with a
    HandoverDoc field; instantiation must work + `__iter__` must yield
    the legacy 3-tuple order.
    """

    from iriai_build_v2.models.outputs import HandoverDoc
    from iriai_build_v2.workflows.develop.execution.types import DagExecutionOutcome

    handover = HandoverDoc()
    outcome = DagExecutionOutcome(
        implementation_text="hello",
        failure="",
        handover=handover,
    )
    # Default terminal_state.
    assert outcome.terminal_state == "complete"
    text, failure, returned_handover = list(outcome)
    assert text == "hello"
    assert failure == ""
    assert returned_handover is handover


def test_commit_repo_outcome_to_dict_uses_bounded_helper() -> None:
    """`CommitRepoOutcome.to_dict()` uses `_bounded_commit_output` to
    cap the stdout/stderr/status fields. With small values the dict
    round-trips losslessly.
    """

    from iriai_build_v2.workflows.develop.execution.types import (
        COMMIT_FAILURE_OUTPUT_LIMIT,
        CommitRepoOutcome,
        _bounded_commit_output,
    )

    outcome = CommitRepoOutcome(
        repo_path="/tmp/repo",
        repo_name="repo",
        message="initial commit",
        stderr="hello",
    )
    payload = outcome.to_dict()
    assert payload["repo_path"] == "/tmp/repo"
    assert payload["repo_name"] == "repo"
    assert payload["message"] == "initial commit"
    assert payload["stderr"] == "hello"
    # `_bounded_commit_output` is the actual helper.
    assert _bounded_commit_output("x") == "x"
    long_value = "x" * (COMMIT_FAILURE_OUTPUT_LIMIT + 10)
    assert "[... truncated 10 chars ...]" in _bounded_commit_output(long_value)


def test_workflow_commit_error_carries_failed_outcomes() -> None:
    """`WorkflowCommitError` collects the failed outcomes and exposes them
    via `failed_outcomes` + `to_payload()`. The exception inherits from
    `RuntimeError` (so the existing `except RuntimeError:` arms in
    legacy code keep catching it).
    """

    from iriai_build_v2.workflows.develop.execution.types import (
        CommitRepoOutcome,
        WorkflowCommitError,
    )

    ok = CommitRepoOutcome(repo_path="/a", repo_name="a", message="ok", commit_hash="abc")
    bad = CommitRepoOutcome(
        repo_path="/b",
        repo_name="b",
        message="bad",
        exit_code=1,
        error="hook fail",
    )
    error = WorkflowCommitError("commit failed", [ok, bad])
    assert isinstance(error, RuntimeError)
    assert error.successful_hashes == ["abc"]
    failed = error.failed_outcomes
    assert len(failed) == 1 and failed[0].repo_name == "b"
    payload = error.to_payload()
    assert payload["failed_repo_count"] == 1
    assert payload["successful_commit_hashes"] == ["abc"]
    assert len(payload["outcomes"]) == 2


def test_dag_authority_gate_outcome_default_route_matches_routing_constant() -> None:
    """`DagAuthorityGateOutcome.route` defaults to the
    `"semantic_verify_needed"` literal — the SAME value as the
    `_DAG_AUTHORITY_SEMANTIC_ROUTE` routing constant that still lives
    in `implementation.py`. The literal inlining preserves the field
    default byte-for-byte while keeping the routing-helper constant
    co-located with its routing-helper users.
    """

    from iriai_build_v2.workflows.develop.execution.types import DagAuthorityGateOutcome
    from iriai_build_v2.workflows.develop.phases import implementation as impl_mod

    outcome = DagAuthorityGateOutcome()
    assert outcome.route == "semantic_verify_needed"
    # And the routing constant in implementation.py still equals the literal.
    assert impl_mod._DAG_AUTHORITY_SEMANTIC_ROUTE == "semantic_verify_needed"
    # `handled` defaults to False (no repair results, no blocked verdict).
    assert outcome.handled is False


def test_sandbox_workflow_blocker_preserves_marker() -> None:
    """`SandboxWorkflowBlocker` prefixes its `failure` with the typed
    marker exactly once. This is the deterministic-blocker contract
    consumed by the workflow-routing logic in `implementation.py`.
    """

    from iriai_build_v2.workflows.develop.execution.types import (
        SandboxWorkflowBlocker,
        _SANDBOX_WORKFLOW_BLOCKER_MARKER,
    )

    blocker = SandboxWorkflowBlocker("sandbox lease unavailable", task_id="T-1")
    assert blocker.task_id == "T-1"
    assert _SANDBOX_WORKFLOW_BLOCKER_MARKER in blocker.failure
    # Idempotent: a message that already contains the marker is not
    # double-prefixed.
    blocker2 = SandboxWorkflowBlocker(blocker.failure, task_id="T-2")
    assert blocker2.failure.count(_SANDBOX_WORKFLOW_BLOCKER_MARKER) == 1


def test_worktree_registry_pydantic_validation_round_trip() -> None:
    """`WorktreeRegistry` and `WorktreeRegistryRepo` are Pydantic
    BaseModels with default-factory list fields; round-tripping through
    `model_dump()` and `model_validate()` preserves every field.
    """

    from iriai_build_v2.workflows.develop.execution.types import (
        WorktreeRegistry,
        WorktreeRegistryRepo,
    )

    repo = WorktreeRegistryRepo(
        repo_path="apps/web",
        action="materialize",
        task_ids=["T-1", "T-2"],
        writable_task_ids=["T-1"],
    )
    registry = WorktreeRegistry(
        workspace_root="/work",
        feature_root="/work/.iriai/features/feat",
        repos=[repo],
    )
    dumped = registry.model_dump()
    rebuilt = WorktreeRegistry.model_validate(dumped)
    assert rebuilt.repos[0].repo_path == "apps/web"
    assert rebuilt.repos[0].task_ids == ["T-1", "T-2"]
    assert rebuilt.feature_id == ""
    assert rebuilt.complete is False


def test_merge_queue_drain_result_succeeded_is_two_value_alphabet() -> None:
    """The `_MergeQueueDrainResult.succeeded` property is True on
    `integrated` OR `done`, False otherwise (the failed/poisoned/queued
    family). This contract is consumed by the 08e-3a drain post-test
    guard; the moved class must preserve it.
    """

    from iriai_build_v2.workflows.develop.execution.types import _MergeQueueDrainResult

    integrated = _MergeQueueDrainResult(
        item_id=1, task_ids=["T-1"], terminal_status="integrated"
    )
    done = _MergeQueueDrainResult(
        item_id=2, task_ids=["T-2"], terminal_status="done"
    )
    failed = _MergeQueueDrainResult(
        item_id=3, task_ids=["T-3"], terminal_status="failed"
    )
    poisoned = _MergeQueueDrainResult(
        item_id=4, task_ids=["T-4"], terminal_status="poisoned"
    )
    queued = _MergeQueueDrainResult(
        item_id=5, task_ids=["T-5"], terminal_status="queued"
    )
    assert integrated.integrated is True
    assert integrated.succeeded is True
    assert done.integrated is False
    assert done.succeeded is True
    for blocked in (failed, poisoned, queued):
        assert blocked.succeeded is False


def test_dag_contradiction_resolution_pydantic_defaults() -> None:
    """`DagContradictionResolution` is a Pydantic BaseModel with a
    documented field set; instantiation with minimum fields exposes the
    documented defaults.
    """

    from iriai_build_v2.workflows.develop.execution.types import DagContradictionResolution

    resolution = DagContradictionResolution(resolution="proceed with current spec")
    assert resolution.resolution == "proceed with current spec"
    assert resolution.resolution_kind == "decision_only"
    assert resolution.confidence == "medium"
    assert resolution.authoritative_sources == []
    assert resolution.requires_code_change is False
