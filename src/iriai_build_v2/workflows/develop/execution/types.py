"""Shared Pydantic / dataclass value-object types for the develop workflow.

This module is the leaf-most layer of the Slice-11 refactor map: it owns the
cross-module Pydantic / dataclass request and outcome objects, the typed
failure signal sentinels, and the bounded-output / serialization helpers
that participate in those type contracts. Per
``docs/execution-control-plane/11-refactor-map.md`` § "Boundary-level API
contracts" row 1, this module must NOT own persistence, git, runtime calls,
or artifact-key construction; it MUST NOT import from ``implementation.py``
or any other workflow-phase module (compatibility flows point from
``implementation.py`` into this module, never the reverse).

Every public name here is re-exported from
``workflows/develop/phases/implementation.py`` via a shim import, so every
existing ``from iriai_build_v2.workflows.develop.phases.implementation
import X`` keeps resolving to the same object after the Slice-11a
extraction (the doc-11 § "How To Use This Map" four-question contract).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from iriai_compose import AgentActor, Feature, WorkflowRunner
from pydantic import BaseModel, Field

from ....models.outputs import (
    BugGroup,
    BugTriage,
    HandoverDoc,
    ImplementationResult,
    ImplementationTask,
    RootCauseAnalysis,
    Verdict,
)


# --- Bounded-output serialization helper ---------------------------------------

COMMIT_FAILURE_OUTPUT_LIMIT = 12000


def _bounded_commit_output(value: str, *, limit: int = COMMIT_FAILURE_OUTPUT_LIMIT) -> str:
    if len(value) <= limit:
        return value
    omitted = len(value) - limit
    return f"{value[:limit]}\n\n[... truncated {omitted} chars ...]"


# --- DAG execution outcome ----------------------------------------------------


@dataclass(slots=True)
class DagExecutionOutcome:
    implementation_text: str
    failure: str
    handover: HandoverDoc
    terminal_state: str = "complete"
    # Typed-recoverable terminal signal. ``recoverable`` is True ONLY for a
    # known self-healing checkpoint the orchestrator may auto-continue without
    # an external resume (conservative allowlist — defaults False so every
    # other ``workflow_blocked`` return is a genuine halt). ``recovery_class``
    # names the recoverable condition and ``progress_token`` is a monotonic
    # marker the orchestrator uses to detect a stuck auto-continue loop.
    recoverable: bool = False
    recovery_class: str = ""
    progress_token: str = ""

    def __iter__(self):
        yield self.implementation_text
        yield self.failure
        yield self.handover


@dataclass(slots=True)
class RuntimeSandboxTaskBinding:
    runner: Any
    lease: Any
    binding: Any
    workflow_runner: WorkflowRunner | None = None
    feature: Feature | None = None
    task_contract: Any | None = None
    feature_root: Path | None = None
    dag_sha256: str = ""
    group_idx: int | None = None
    stage: str = ""
    snapshots: list[Any] = field(default_factory=list)


_SANDBOX_WORKFLOW_BLOCKER_MARKER = "SANDBOX_WORKFLOW_BLOCKER"


class SandboxWorkflowBlocker(RuntimeError):
    """Deterministic sandbox control-plane blocker."""

    def __init__(self, message: str, *, task_id: str | None = None) -> None:
        self.task_id = task_id
        self.failure = (
            f"{_SANDBOX_WORKFLOW_BLOCKER_MARKER}: {message}"
            if _SANDBOX_WORKFLOW_BLOCKER_MARKER not in message
            else message
        )
        super().__init__(self.failure)


# --- Commit outcomes / failures -----------------------------------------------


@dataclass(slots=True)
class CommitRepoOutcome:
    repo_path: str
    repo_name: str
    message: str
    status_before: str = ""
    status_after: str = ""
    dirty: bool = False
    command: list[str] = field(default_factory=list)
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    commit_hash: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_path": self.repo_path,
            "repo_name": self.repo_name,
            "message": self.message,
            "status_before": _bounded_commit_output(self.status_before),
            "status_after": _bounded_commit_output(self.status_after),
            "dirty": self.dirty,
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": _bounded_commit_output(self.stdout),
            "stderr": _bounded_commit_output(self.stderr),
            "commit_hash": self.commit_hash,
            "error": self.error,
        }


class WorkflowCommitError(RuntimeError):
    """Raised when a dirty workflow repo cannot be committed."""

    def __init__(self, message: str, outcomes: list[CommitRepoOutcome]) -> None:
        self.outcomes = outcomes
        self.successful_hashes = [
            outcome.commit_hash for outcome in outcomes if outcome.commit_hash
        ]
        failed = [outcome for outcome in outcomes if outcome.error or outcome.exit_code]
        failed_repos = ", ".join(
            outcome.repo_name or outcome.repo_path for outcome in failed
        ) or "unknown repo"
        super().__init__(f"{message}: commit failed for {failed_repos}")

    @property
    def failed_outcomes(self) -> list[CommitRepoOutcome]:
        return [
            outcome for outcome in self.outcomes
            if outcome.error or outcome.exit_code
        ]

    def to_payload(self) -> dict[str, Any]:
        return {
            "error": str(self),
            "failed_repo_count": len(self.failed_outcomes),
            "successful_commit_hashes": self.successful_hashes,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
        }


@dataclass(slots=True)
class CommitFailureLocation:
    file: str = ""
    line: int = 0


@dataclass(slots=True)
class CommitForbiddenPathMatch:
    path: str
    repo_path: str
    repo_name: str
    manifest_rule: str
    config_path: str
    source: str
    git_state: str = ""
    line: int = 0
    operator_required: bool = False
    operator_reasons: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "repo_path": self.repo_path,
            "repo_name": self.repo_name,
            "manifest_rule": self.manifest_rule,
            "config_path": self.config_path,
            "source": self.source,
            "git_state": self.git_state,
            "line": self.line,
            "operator_required": self.operator_required,
            "operator_reasons": self.operator_reasons,
        }


@dataclass(slots=True)
class DagDirectRepairRoute:
    route: str
    reason: str
    signature: str
    target_files: list[str] = field(default_factory=list)
    skip_expanded_verify: bool = False
    skip_parallel_repair: bool = False
    skip_rca: bool = False
    operator_required: bool = False
    workspace_permission_repair: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "reason": self.reason,
            "signature": self.signature,
            "target_files": self.target_files,
            "skip_expanded_verify": self.skip_expanded_verify,
            "skip_parallel_repair": self.skip_parallel_repair,
            "skip_rca": self.skip_rca,
            "operator_required": self.operator_required,
            "workspace_permission_repair": self.workspace_permission_repair,
        }


@dataclass(slots=True)
class DagAuthorityGateOutcome:
    # The default value below MUST equal `execution/gates.py`'s
    # `_DAG_AUTHORITY_SEMANTIC_ROUTE` constant (moved there by Slice 11f
    # alongside the other DAG-authority routing constants + helpers; the
    # routing helpers in `execution/gates.py` continue to reference the
    # constant by name, and `implementation.py` resolves it via the
    # Slice-11f shim). The default value here is inlined as the same
    # literal to avoid duplicating the constant outside its routing-helper
    # neighborhood. See doc-11 row 1 - module-level routing constants are
    # scoped to their owning module; only the per-type default participates
    # in the type contract.
    route: str = "semantic_verify_needed"
    status: str = "not_applicable"
    reason: str = ""
    repair_results: list[ImplementationResult] = field(default_factory=list)
    blocked_verdict: Verdict | None = None
    report: dict[str, Any] = field(default_factory=dict)

    @property
    def handled(self) -> bool:
        return bool(self.repair_results or self.blocked_verdict is not None)


# --- Bug-fix planning -------------------------------------------------------


@dataclass(slots=True)
class PlannedBugGroup:
    group: BugGroup
    rca: RootCauseAnalysis
    issue_text: str
    rca_key: str


@dataclass(slots=True)
class PlannedBugDispatch:
    attempt_number: int
    triage: BugTriage
    groups: list[PlannedBugGroup]
    fixable_groups: list[PlannedBugGroup]
    contradiction_groups: list[PlannedBugGroup]
    schedule: list[list[str]]
    dispatch_key: str
    strategy_mode: str = "ordinary_retry"
    strategy_reason: str = ""
    required_checks: list[str] = field(default_factory=list)
    required_files: list[str] = field(default_factory=list)
    stable_blocker_summary: str = ""
    similar_cluster_hints: list[str] = field(default_factory=list)


# --- DAG task drift / contradiction ----------------------------------------


@dataclass(slots=True)
class DagTaskDriftRoute:
    task_id: str
    artifact_key: str
    route: str
    reason: str
    path_problems: list[dict[str, Any]] = field(default_factory=list)
    forbidden_workspace_paths: list[dict[str, Any]] = field(default_factory=list)
    candidate_evidence: list[dict[str, Any]] = field(default_factory=list)


class DagContradictionResolution(BaseModel):
    """Autonomous adjudication of a DAG repair spec contradiction."""

    resolution: str
    resolution_kind: str = "decision_only"  # decision_only | requires_code_change | artifact_repair | mixed_repair | stale_not_reproducing | needs_human
    authoritative_sources: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    superseded_expectation: str = ""
    implementation_direction: str = ""
    requires_code_change: bool = False
    needs_human: bool = False
    confidence: str = "medium"  # high | medium | low
    rationale: str = ""


@dataclass(slots=True)
class DagContradictionResolutionValidation:
    resolution: DagContradictionResolution | None
    rejection_reasons: list[str] = field(default_factory=list)


@dataclass(slots=True)
class DagContradictionHandoffOutcome:
    resolution: DagContradictionResolution | None = None
    resolution_record: dict[str, Any] | None = None
    handoff_record: dict[str, Any] | None = None
    rejection_record: dict[str, Any] | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    decision_source: str = ""
    decision_metadata: dict[str, Any] = field(default_factory=dict)


# ── Worktree management ─────────────────────────────────────────────────────


class WorktreeRegistryRepo(BaseModel):
    repo_path: str
    action: str
    repo_id: str = ""
    role: str = "execution"
    task_ids: list[str] = Field(default_factory=list)
    writable_task_ids: list[str] = Field(default_factory=list)
    read_only_task_ids: list[str] = Field(default_factory=list)
    nested_requests: list[str] = Field(default_factory=list)
    source_path: str = ""
    destination_path: str = ""
    canonical_path: str = ""
    remote_url: str = ""
    branch: str = ""
    head_sha: str = ""
    git_common_dir: str = ""
    writable: bool = False
    dirty_summary: list[str] = Field(default_factory=list)
    source_git_exists: bool = False
    destination_exists_before: bool = False
    destination_is_symlink_before: bool = False
    destination_isolated_before: bool = False
    destination_isolated_after: bool = False
    preflight_status: str = "pending"
    preflight_errors: list[str] = Field(default_factory=list)


class WorktreeRegistry(BaseModel):
    feature_id: str = ""
    feature_slug: str = ""
    workspace_root: str
    feature_root: str
    repos: list[WorktreeRegistryRepo] = Field(default_factory=list)
    complete: bool = False


@dataclass(slots=True)
class WorkspaceAuthorityCompatibilityOutcome:
    approved: bool = True
    operator_required: bool = False
    registry: Any | None = None
    preflight: Any | None = None
    acl_normalization: Any | None = None
    routes: list[Any] = field(default_factory=list)
    snapshots: list[Any] = field(default_factory=list)
    artifact_keys: dict[str, str] = field(default_factory=dict)
    unavailable_reason: str = ""


@dataclass(slots=True)
class TaskContractCompileOutcome:
    approved: bool = True
    contracts_by_task_id: dict[str, Any] = field(default_factory=dict)
    preexisting_contract_digests: dict[str, str] = field(default_factory=dict)
    failure: str = ""
    failure_class: str = ""
    failure_type: str = ""
    route: str = ""
    artifact_key: str = ""


@dataclass(slots=True)
class TaskContractCommitGuardOutcome:
    approved: bool = True
    failure: str = ""
    violation_codes: list[str] = field(default_factory=list)
    artifact_keys: list[str] = field(default_factory=list)


@dataclass(slots=True)
class _RepoNeed:
    action: str
    task_ids: list[str] = field(default_factory=list)
    writable_task_ids: list[str] = field(default_factory=list)
    read_only_task_ids: list[str] = field(default_factory=list)
    nested_requests: list[str] = field(default_factory=list)


# --- Merge-queue drain / checkpoint / resume recovery ---------------------


class _MergeQueueEnqueueError(RuntimeError):
    """The durable merge queue enqueue could not complete for a sandbox lane.

    Slice 08e-2: raised (fails closed) instead of silently falling back to the
    legacy canonical commit when the merge queue / readiness guard is
    unavailable or the enqueue inputs cannot be resolved.
    """


class _MergeQueueDrainResult:
    """Outcome of draining one merge-queue lane (08e-3a).

    `terminal_status` is the lane's final queue status — `integrated` on
    success, `failed`/`poisoned` on a routed failure. `routed_failure` is the
    Slice 07 routing result dict (empty on success or when the router is
    unavailable).
    """

    __slots__ = (
        "item_id",
        "task_ids",
        "terminal_status",
        "result_commit",
        "failure_class",
        "detail",
        "routed_failure",
    )

    def __init__(
        self,
        *,
        item_id: int,
        task_ids: list[str],
        terminal_status: str,
        result_commit: str = "",
        failure_class: str = "",
        detail: str = "",
        routed_failure: dict[str, Any] | None = None,
    ) -> None:
        self.item_id = item_id
        self.task_ids = task_ids
        self.terminal_status = terminal_status
        self.result_commit = result_commit
        self.failure_class = failure_class
        self.detail = detail
        self.routed_failure = routed_failure or {}

    @property
    def integrated(self) -> bool:
        return self.terminal_status == "integrated"

    @property
    def succeeded(self) -> bool:
        """True when the lane reached a NON-FAILURE drain terminal.

        A normal drained lane stops at `integrated`. A lane recovered from a
        crashed `checkpointing` state (08g P2-A) may instead reach `done` —
        the doc-08 idempotent group checkpoint re-run completed and advanced it
        past `integrated`. Both are successful terminals: callers' `failed_lanes`
        filters must treat a recovered `done` lane as success, never block the
        group on it. `failed`/`poisoned` are the failure terminals.
        """

        return self.terminal_status in ("integrated", "done")


class _MergeQueueCheckpointResult:
    """Outcome of the 08e-3b post-DAG checkpoint for one DAG group.

    `checkpointed` is true once `GroupMergeCoordinator.checkpoint_group`
    projected (or idempotently re-confirmed) the `dag-group:*` checkpoint and
    advanced every covered lane to `done`. `detail` carries the failure context
    when it is false; `routed_failure` is the Slice 07 routing result dict.
    """

    __slots__ = (
        "group_idx",
        "checkpointed",
        "result_commit",
        "done_queue_item_ids",
        "detail",
        "routed_failure",
    )

    def __init__(
        self,
        *,
        group_idx: int,
        checkpointed: bool,
        result_commit: str = "",
        done_queue_item_ids: list[int] | None = None,
        detail: str = "",
        routed_failure: dict[str, Any] | None = None,
    ) -> None:
        self.group_idx = group_idx
        self.checkpointed = checkpointed
        self.result_commit = result_commit
        self.done_queue_item_ids = done_queue_item_ids or []
        self.detail = detail
        self.routed_failure = routed_failure or {}


class _MergeQueueResumeRecovery:
    """Outcome of the 08e-3b P2-a resume re-drive of a queue-driven group.

    `recovered` is true once the idempotent drain + `checkpoint_group` re-run
    completed the group checkpoint on resume (the `dag-group:*` projection now
    exists and every lane is `done`). When false, the resume must fall through
    to the existing fail-closed pending-marker block; `detail` carries the
    context for the typed `workflow_blocked`.
    """

    __slots__ = (
        "recovered",
        "done_queue_item_ids",
        "result_commit",
        "detail",
    )

    def __init__(
        self,
        *,
        recovered: bool,
        done_queue_item_ids: list[int] | None = None,
        result_commit: str = "",
        detail: str = "",
    ) -> None:
        self.recovered = recovered
        self.done_queue_item_ids = done_queue_item_ids or []
        self.result_commit = result_commit
        self.detail = detail


# --- DAG verification lens spec ---------------------------------------------


@dataclass(frozen=True)
class DagVerifyLensSpec:
    slug: str
    label: str
    actor: AgentActor
    focus: str


# --- Worktree alias path info -----------------------------------------------


@dataclass(frozen=True)
class WorktreeAliasPathInfo:
    original: str
    canonical: str
    alias_repo: str
    canonical_repo: str
    alias_exists: bool
    canonical_exists: bool
    divergent: bool
    category: str
    repair_route: str


# --- DAG task / spec reconcile outcomes --------------------------------------


@dataclass(slots=True)
class DagTaskReconcileOutcome:
    results: list[object]
    verify_results_context: list[object]
    all_results: list[object]
    report: dict[str, Any]


@dataclass(slots=True)
class DagTaskSpecReconcileOutcome:
    tasks: list[ImplementationTask]
    report: dict[str, Any]


__all__ = [
    "COMMIT_FAILURE_OUTPUT_LIMIT",
    "_bounded_commit_output",
    "DagExecutionOutcome",
    "RuntimeSandboxTaskBinding",
    "_SANDBOX_WORKFLOW_BLOCKER_MARKER",
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
