from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


ExecutionStatus = Literal["started", "succeeded", "failed", "cancelled", "incomplete"]
DispatcherState = Literal[
    "requested",
    "attempt_started",
    "context_prepared",
    "runtime_invoking",
    "runtime_returned",
    "patch_capturing",
    "output_normalizing",
    "evidence_recording",
    "succeeded",
    "failed",
    "cancelled",
    "incomplete",
]
RuntimeTerminalReason = Literal[
    "completed",
    "cancelled",
    "provider_error",
    "timeout",
    "watchdog_stall",
    "process_failed",
    "prompt_too_large",
    "context_materialization_failed",
    "structured_output_invalid",
    "sandbox_binding_failed",
    "patch_capture_failed",
]
RuntimeFailureClass = Literal[
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "sandbox_binding",
    "sandbox_capture",
    "dispatcher_internal",
]
ProjectionMode = Literal["legacy_compatibility"]
SandboxLeaseMode = Literal["wave", "task", "repair", "canonicalization"]
SandboxLeaseStatus = Literal[
    "allocating",
    "allocated",
    "binding",
    "running",
    "capturing",
    "captured",
    "released",
    "retained",
    "failed",
    "poisoned",
]
SandboxRepoBindingStatus = Literal["active", "released", "poisoned"]
VerificationGraphNodeKind = Literal[
    "gate_request",
    "candidate_manifest",
    "deterministic_gate",
    "context_package",
    "raw_verifier",
    "expanded_lens",
    "aggregate_verdict",
    "merge_gate",
    "checkpoint_gate",
]
RuntimeWorkspaceBindingStatus = Literal["bound", "started", "finished", "failed", "poisoned"]

SUPPORTED_PROJECTION_PREFIXES: tuple[str, ...] = (
    "dag-task:",
    "dag-task-contract:",
    "dag-verify:",
    "dag-commit-failure:",
    "dag-group:",
    "dag-sandbox:",
    "dag-sandbox-patch:",
    "dag-contract-verdict:",
    "dag-regroup:",
    "dag-regroup-active:",
    "dag-regroup-observation:",
    "dag-worktree-alias-preflight:",
    "dag-writeability-preflight:",
    "workspace-snapshot:",
    "worktree-registry",
)


class ExecutionControlError(RuntimeError):
    """Base error for typed execution control persistence."""


class MissingCompatibilityProjection(ExecutionControlError):
    """Raised before writes when legacy-visible success lacks projections."""


class MissingRequiredProjection(MissingCompatibilityProjection):
    """Raised when a projection-specific API lacks its legacy artifact body."""


class IdempotencyConflict(ExecutionControlError):
    """Raised when an idempotency key is reused for different typed input."""


class UnsupportedCompatibilityProjection(ExecutionControlError):
    """Raised when a projection key is outside the compatibility contract."""


@dataclass(frozen=True)
class CompatibilityProjection:
    key: str
    value: Any
    idempotency_key: str | None = None

    @property
    def digest(self) -> str:
        return stable_digest({"key": self.key, "value": self.value})


@dataclass(frozen=True)
class ExecutionJournalWrite:
    feature_id: str
    idempotency_key: str
    entry_type: str
    status: ExecutionStatus
    payload: dict[str, Any] = field(default_factory=dict)
    actor: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    task_id: str | None = None
    requires_legacy_visibility: bool = False
    compatibility_projections: tuple[CompatibilityProjection, ...] = ()
    projection_mode: ProjectionMode = "legacy_compatibility"
    request_digest: str | None = None
    dispatcher_state: DispatcherState = "requested"
    runtime: str = ""

    def normalized_request(self) -> dict[str, Any]:
        return {
            "actor": self.actor,
            "dag_sha256": self.dag_sha256,
            "entry_type": self.entry_type,
            "feature_id": self.feature_id,
            "group_idx": self.group_idx,
            "idempotency_key": self.idempotency_key,
            "payload": self.payload,
            "projection_mode": self.projection_mode,
            "dispatcher_state": self.dispatcher_state,
            "runtime": self.runtime,
            "compatibility_projections": [
                {
                    "digest": projection.digest,
                    "idempotency_key": projection.idempotency_key or "",
                    "key": projection.key,
                    "value_digest": stable_digest(projection.value),
                }
                for projection in self.compatibility_projections
            ],
            "requires_legacy_visibility": self.requires_legacy_visibility,
            "status": self.status,
            "task_id": self.task_id,
        }

    @property
    def digest(self) -> str:
        return self.request_digest or stable_digest(self.normalized_request())


@dataclass(frozen=True)
class ProjectionLink:
    id: int
    typed_row_id: int
    artifact_id: int
    feature_id: str
    projection_key: str
    projection_sha256: str
    idempotency_key: str
    legacy_event_id: int | None = None
    dashboard_outbox_event_id: str | None = None
    source_table: str = "execution_journal_rows"
    source_id: int | None = None
    projection_owner: str = ""
    projection_kind: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None

    @property
    def projection_digest(self) -> str:
        return self.projection_sha256


@dataclass(frozen=True)
class ExecutionJournalRow:
    id: int
    feature_id: str
    idempotency_key: str
    entry_type: str
    status: str
    request_digest: str
    payload: dict[str, Any]
    actor: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    task_id: str | None = None
    requires_legacy_visibility: bool = False
    projection_mode: str = "legacy_compatibility"
    dispatcher_state: str = "requested"
    runtime: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class ExecutionJournalResult:
    row: ExecutionJournalRow
    projection_links: tuple[ProjectionLink, ...]
    created: bool


@dataclass(frozen=True)
class DispatchAttemptRequest:
    feature_id: str
    dag_sha256: str
    group_idx: int
    task_id: str
    task_name: str = ""
    retry: int = 0
    retry_identity: dict[str, Any] = field(default_factory=dict)
    contract_ids: list[int] = field(default_factory=list)
    sandbox_id: str = ""
    workspace_snapshot_ids: list[int] = field(default_factory=list)
    base_commit_by_repo: dict[str, str] = field(default_factory=dict)
    runtime_policy: str = ""
    runtime_policy_digest: str = ""
    actor_role: str = ""
    actor_metadata: dict[str, Any] = field(default_factory=dict)
    prior_evidence_ids: list[int] = field(default_factory=list)
    cancellation_token: str | None = None
    request_digest: str = ""
    idempotency_key: str = ""

    @property
    def runtime(self) -> str:
        return str(self.actor_metadata.get("runtime") or "")

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:dispatch-attempt:{self.feature_id}:{self.dag_sha256}:"
            f"g{self.group_idx}:{self.task_id}:retry-{self.retry}:"
            f"{self.actor_role or '-'}"
        )

    def normalized_request(self) -> dict[str, Any]:
        return {
            "actor_metadata": self.actor_metadata,
            "actor_role": self.actor_role,
            "base_commit_by_repo": {
                str(key): str(value)
                for key, value in sorted(self.base_commit_by_repo.items())
            },
            "cancellation_token": self.cancellation_token,
            "contract_ids": sorted(int(item) for item in self.contract_ids),
            "dag_sha256": self.dag_sha256,
            "feature_id": self.feature_id,
            "group_idx": int(self.group_idx),
            "idempotency_key": self.stable_idempotency_key,
            "prior_evidence_ids": sorted(int(item) for item in self.prior_evidence_ids),
            "retry": int(self.retry),
            "retry_identity": self.retry_identity,
            "runtime_policy": self.runtime_policy,
            "runtime_policy_digest": self.runtime_policy_digest,
            "sandbox_id": self.sandbox_id,
            "task_id": self.task_id,
            "task_name": self.task_name,
            "workspace_snapshot_ids": sorted(
                int(item) for item in self.workspace_snapshot_ids
            ),
        }

    @property
    def digest(self) -> str:
        return self.request_digest or stable_digest(self.normalized_request())


@dataclass(frozen=True)
class DispatchAttemptResult:
    attempt: ExecutionJournalRow
    created: bool

    @property
    def attempt_id(self) -> int:
        return self.attempt.id


@dataclass(frozen=True)
class PromptContextEvidence:
    attempt_id: int
    prompt_ref: int
    prompt_sha256: str
    prompt_summary: str = ""
    context_file_refs: list[int] = field(default_factory=list)
    context_file_paths: list[str] = field(default_factory=list)
    context_sha256: str = ""
    included_contract_ids: list[int] = field(default_factory=list)
    included_evidence_ids: list[int] = field(default_factory=list)
    excluded_evidence_ids: list[int] = field(default_factory=list)
    truncation_notes: list[str] = field(default_factory=list)
    context_package_id: str | None = None
    context_package_digest: str | None = None
    context_package_ref: str | None = None
    context_package_kind: str | None = None
    context_package_completeness: str | None = None
    context_package_page_refs: list[Any] = field(default_factory=list)
    context_package_feature_id: str | None = None
    context_package_task_id: str | None = None
    context_package_source_dag_artifact_id: int | str | None = None
    context_package_dag_sha256: str | None = None
    context_package_evidence_snapshot_digest: str | None = None
    context_package_provider_state_digest: str | None = None
    context_package_advisory_only: bool | None = None
    idempotency_key: str = ""
    stage: str = "pre_runtime"
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        base_key = (
            f"idem:dispatch-prompt-context:{self.attempt_id}:"
            f"{self.prompt_sha256}:{self.context_sha256 or '-'}"
        )
        if self.context_package_digest:
            return f"{base_key}:context-package:{self.context_package_digest}"
        return base_key


@dataclass(frozen=True)
class RuntimeInvocationEvidence:
    attempt_id: int
    invocation_id: str
    runtime: str
    phase: Literal["request", "response"] = "request"
    actor_name: str = ""
    actor_role: str = ""
    actor_metadata: dict[str, Any] = field(default_factory=dict)
    runtime_workspace_binding_id: int | None = None
    prompt_ref: int | None = None
    prompt_sha256: str = ""
    output_schema: str = ""
    output_schema_digest: str = ""
    output_type_name: str = ""
    timeout_seconds: int | None = None
    retry_within_invocation: bool = True
    cancellation_token: str | None = None
    status: Literal["running", "completed", "failed", "cancelled"] = "running"
    terminal_reason: RuntimeTerminalReason | None = None
    process_started: bool = False
    raw_text_ref: int | None = None
    raw_artifact_id: int | None = None
    provider_request_id: str | None = None
    provider_error_code: str | None = None
    stdout_artifact_id: int | None = None
    stderr_artifact_id: int | None = None
    adapter_retry_ids: list[str] = field(default_factory=list)
    adapter_retry_count: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int | None = None
    idempotency_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        reason = self.terminal_reason or self.status
        return (
            f"idem:dispatch-runtime-invocation:{self.attempt_id}:"
            f"{self.invocation_id}:{self.phase}:{self.runtime}:{reason}"
        )


@dataclass(frozen=True)
class RawOutputEvidence:
    attempt_id: int
    invocation_id: str
    runtime: str
    status: Literal["completed", "failed", "cancelled"] = "failed"
    terminal_reason: RuntimeTerminalReason | None = None
    raw_text: str = ""
    raw_artifact_id: int | None = None
    provider_request_id: str | None = None
    provider_error_code: str | None = None
    idempotency_key: str = ""
    stage: str = "runtime"
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def raw_text_sha256(self) -> str:
        return hashlib.sha256(self.raw_text.encode("utf-8")).hexdigest()

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:dispatch-raw-output:{self.attempt_id}:"
            f"{self.invocation_id}:{self.raw_text_sha256}"
        )


@dataclass(frozen=True)
class StructuredOutputEvidence:
    attempt_id: int
    schema_name: str
    schema_digest: str
    valid: bool
    original_payload: dict[str, Any] | None = None
    normalized_payload: dict[str, Any] | None = None
    validation_errors: list[str] = field(default_factory=list)
    corrected_fields: dict[str, Any] = field(default_factory=dict)
    task_id_matches_request: bool = True
    projection_body: str | None = None
    raw_text_ref: int | None = None
    raw_artifact_id: int | None = None
    idempotency_key: str = ""
    stage: str = "post_runtime"
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_payload(self) -> dict[str, Any]:
        return {
            "corrected_fields": self.corrected_fields,
            "normalized_payload": self.normalized_payload,
            "original_payload": self.original_payload,
            "projection_body": self.projection_body,
            "raw_artifact_id": self.raw_artifact_id,
            "raw_text_ref": self.raw_text_ref,
            "schema_digest": self.schema_digest,
            "schema_name": self.schema_name,
            "task_id_matches_request": self.task_id_matches_request,
            "valid": self.valid,
            "validation_errors": self.validation_errors,
            **self.payload,
        }

    @property
    def content_hash(self) -> str:
        return stable_digest(self.stable_payload)

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:dispatch-structured-output:{self.attempt_id}:"
            f"{self.schema_digest}:{self.content_hash}"
        )


@dataclass(frozen=True)
class RuntimeFailureEvidence:
    attempt_id: int
    failure_class: RuntimeFailureClass
    failure_type: str
    retryable: bool
    deterministic: bool
    operator_required: bool = False
    provider_request_id: str | None = None
    evidence_ids: list[int] = field(default_factory=list)
    signature_hash: str = ""
    runtime: str = ""
    provider_error_code: str | None = None
    terminal_reason: RuntimeTerminalReason | None = None
    idempotency_key: str = ""
    stage: str = "post_runtime"
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_signature_hash(self) -> str:
        if self.signature_hash:
            return self.signature_hash
        return stable_digest({
            "evidence_ids": sorted(int(item) for item in self.evidence_ids),
            "failure_class": self.failure_class,
            "failure_type": self.failure_type,
            "provider_error_code": self.provider_error_code,
            "provider_request_id": self.provider_request_id,
            "runtime": self.runtime,
            "terminal_reason": self.terminal_reason,
        })

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:dispatch-runtime-failure:{self.attempt_id}:"
            f"{self.failure_class}:{self.failure_type}:{self.stable_signature_hash}"
        )


@dataclass(frozen=True)
class RuntimeFailureResult:
    evidence: EvidenceNode
    failure_id: int
    typed_failure_id: int
    signature_hash: str
    created: bool


@dataclass(frozen=True)
class DispatchOutcome:
    attempt_id: int
    state: DispatcherState
    status: Literal["succeeded", "failed", "cancelled", "incomplete"]
    runtime_terminal_reason: RuntimeTerminalReason | None = None
    structured_result_evidence_id: int | None = None
    raw_text_ref: int | None = None
    patch_summary_ids: list[int] = field(default_factory=list)
    compatibility_artifact_ids: list[int] = field(default_factory=list)
    runtime_failure_id: int | None = None
    typed_failure_id: int | None = None
    idempotency_key: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def normalized_outcome(self) -> dict[str, Any]:
        return {
            "attempt_id": int(self.attempt_id),
            "compatibility_artifact_ids": sorted(
                int(item) for item in self.compatibility_artifact_ids
            ),
            "idempotency_key": self.idempotency_key,
            "metadata": self.metadata,
            "patch_summary_ids": sorted(int(item) for item in self.patch_summary_ids),
            "raw_text_ref": self.raw_text_ref,
            "runtime_failure_id": self.runtime_failure_id,
            "runtime_terminal_reason": self.runtime_terminal_reason,
            "state": self.state,
            "status": self.status,
            "structured_result_evidence_id": self.structured_result_evidence_id,
            "typed_failure_id": self.typed_failure_id,
        }

    @property
    def digest(self) -> str:
        return stable_digest(self.normalized_outcome())


@dataclass(frozen=True)
class TaskResultProjectionFromAttempt:
    attempt_id: int
    structured_result_evidence_id: int | None = None
    idempotency_key: str = ""


@dataclass(frozen=True)
class WorkspaceRegistryEvidence:
    feature_id: str
    idempotency_key: str
    payload: Any
    artifact_key: str = "worktree-registry"
    registry_digest: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    stage: str = "registry"
    actor: str = "workspace_authority"


@dataclass(frozen=True)
class WorkspacePreflightEvidence:
    feature_id: str
    idempotency_key: str
    payload: Any
    artifact_key: str
    dag_sha256: str = ""
    group_idx: int | None = None
    attempt_id: int | None = None
    stage: str = ""
    registry_digest: str = ""
    actor: str = "workspace_authority"


@dataclass(frozen=True)
class WorkspaceSnapshotEvidence:
    feature_id: str
    payload: Any
    dag_sha256: str = ""
    group_idx: int | None = None
    attempt_id: int | None = None
    stage: str = ""
    repo_id: str = ""
    canonical_path: str = ""
    registry_digest: str = ""
    head_sha: str = ""
    index_digest: str = ""
    worktree_status_digest: str = ""
    artifact_key: str = ""
    idempotency_key: str = ""
    captured_at: datetime | None = None
    actor: str = "workspace_authority"

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        payload = _payload_dict_for_identity(self.payload)
        return workspace_snapshot_idempotency_key(
            feature_id=self.feature_id or str(payload.get("feature_id") or ""),
            dag_sha256=self.dag_sha256 or str(payload.get("dag_sha256") or ""),
            group_idx=self.group_idx if self.group_idx is not None else payload.get("group_idx"),
            stage=self.stage or str(payload.get("stage") or ""),
            repo_id=self.repo_id or str(payload.get("repo_id") or ""),
            head_sha=self.head_sha or str(payload.get("head_sha") or ""),
            index_digest=self.index_digest or str(payload.get("index_digest") or ""),
            worktree_status_digest=(
                self.worktree_status_digest
                or str(payload.get("worktree_status_digest") or "")
            ),
        )

    @property
    def projection_key(self) -> str:
        if self.artifact_key:
            return self.artifact_key
        payload = _payload_dict_for_identity(self.payload)
        return workspace_snapshot_projection_key(
            feature_id=self.feature_id or str(payload.get("feature_id") or ""),
            dag_sha256=self.dag_sha256 or str(payload.get("dag_sha256") or ""),
            group_idx=self.group_idx if self.group_idx is not None else payload.get("group_idx"),
            stage=self.stage or str(payload.get("stage") or ""),
            repo_id=self.repo_id or str(payload.get("repo_id") or ""),
        )


@dataclass(frozen=True)
class WorkspaceSnapshotRow:
    id: int
    feature_id: str
    idempotency_key: str
    execution_journal_row_id: int
    dag_sha256: str
    group_idx: int | None
    attempt_id: int | None
    stage: str
    repo_id: str
    canonical_path: str
    registry_digest: str
    snapshot_digest: str
    payload: dict[str, Any]
    captured_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class WorkspaceSnapshotResult:
    snapshot: WorkspaceSnapshotRow
    execution: ExecutionJournalResult
    created: bool


@dataclass(frozen=True)
class SandboxSpec:
    feature_id: str
    dag_sha256: str
    group_idx: int
    attempt_no: int
    task_ids: list[str] = field(default_factory=list)
    repo_ids: list[str] = field(default_factory=list)
    base_snapshot_ids: list[int] = field(default_factory=list)
    base_commits: dict[str, str] = field(default_factory=dict)
    mode: SandboxLeaseMode = "wave"
    writable_roots: list[str] = field(default_factory=list)
    readonly_roots: list[str] = field(default_factory=list)
    contract_ids: list[int] = field(default_factory=list)
    ttl_seconds: int = 86_400
    idempotency_key: str = ""

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return sandbox_lease_idempotency_key(
            feature_id=self.feature_id,
            dag_sha256=self.dag_sha256,
            group_idx=self.group_idx,
            attempt_no=self.attempt_no,
            mode=self.mode,
            repo_ids=self.repo_ids,
            base_commits=self.base_commits,
            contract_ids=self.contract_ids,
        )


@dataclass(frozen=True)
class SandboxLease:
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: int = 0
    attempt_no: int = 0
    mode: SandboxLeaseMode = "wave"
    lease_owner: str = "sandbox_runner"
    owner: str = ""
    leased_until: datetime | None = None
    expires_at: datetime | str | None = None
    sandbox_root: str = ""
    root: str = ""
    sandbox_id: str = ""
    manifest_path: str = ""
    base_snapshot_ids: list[int] = field(default_factory=list)
    repo_ids: list[str] = field(default_factory=list)
    repo_roots: dict[str, str] = field(default_factory=dict)
    base_commits: dict[str, str] = field(default_factory=dict)
    task_ids: list[str] = field(default_factory=list)
    contract_ids: list[int] = field(default_factory=list)
    writable_roots: list[str] = field(default_factory=list)
    readonly_roots: list[str] = field(default_factory=list)
    blocked_roots: list[str] = field(default_factory=list)
    patch_summary_ids: list[int] = field(default_factory=list)
    status: SandboxLeaseStatus = "allocating"
    idempotency_key: str = ""
    lease_digest: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    execution_journal_row_id: int | None = None
    lease_version: int = 0
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return sandbox_lease_idempotency_key(
            feature_id=self.feature_id,
            dag_sha256=self.dag_sha256,
            group_idx=self.group_idx,
            attempt_no=self.attempt_no,
            mode=self.mode,
            repo_ids=self.repo_ids or sorted(self.repo_roots),
            base_commits=self.base_commits,
            contract_ids=self.contract_ids,
        )

    @property
    def stable_lease_digest(self) -> str:
        if self.lease_digest:
            return self.lease_digest
        return sandbox_lease_digest(
            sandbox_id=self.sandbox_id,
            sandbox_root=self.sandbox_root or self.root,
            manifest_path=self.manifest_path,
            base_snapshot_ids=self.base_snapshot_ids,
            repo_ids=self.repo_ids or sorted(self.repo_roots),
            base_commits=self.base_commits,
            mode=self.mode,
            lease_owner=self.lease_owner or self.owner,
            task_ids=self.task_ids,
            contract_ids=self.contract_ids,
            writable_roots=self.writable_roots,
            readonly_roots=self.readonly_roots,
            blocked_roots=self.blocked_roots,
        )

    @property
    def projection_key(self) -> str:
        return sandbox_manifest_projection_key(
            group_idx=self.group_idx,
            attempt_no=self.attempt_no,
        )


@dataclass(frozen=True)
class SandboxRepoBinding:
    feature_id: str = ""
    sandbox_lease_id: int = 0
    repo_id: str = ""
    sandbox_repo_root: str = ""
    canonical_repo_root: str = ""
    base_snapshot_id: int = 0
    base_commit: str = ""
    writable: bool = True
    writable_roots: list[str] = field(default_factory=list)
    readonly_roots: list[str] = field(default_factory=list)
    blocked_canonical_roots: list[str] = field(default_factory=list)
    status: SandboxRepoBindingStatus = "active"
    binding_digest: str = ""
    idempotency_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def stable_binding_digest(self) -> str:
        if self.binding_digest:
            return self.binding_digest
        return sandbox_repo_binding_digest(
            sandbox_lease_id=self.sandbox_lease_id,
            repo_id=self.repo_id,
            sandbox_repo_root=self.sandbox_repo_root,
            canonical_repo_root=self.canonical_repo_root,
            base_snapshot_id=self.base_snapshot_id,
            base_commit=self.base_commit,
            writable=self.writable,
            writable_roots=self.writable_roots,
            readonly_roots=self.readonly_roots,
            blocked_canonical_roots=self.blocked_canonical_roots,
        )

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:sandbox-repo-binding:{self.sandbox_lease_id}:"
            f"{self.repo_id}:{self.stable_binding_digest}"
        )


@dataclass(frozen=True)
class RuntimeWorkspaceBinding:
    feature_id: str = ""
    sandbox_lease_id: int = 0
    sandbox_id: str = ""
    attempt_id: int = 0
    runtime_name: str = ""
    runtime: str = ""
    cwd: str = ""
    workspace_override: str = ""
    manifest_path: str = ""
    repo_roots: dict[str, str] = field(default_factory=dict)
    writable_roots: list[str] = field(default_factory=list)
    readonly_roots: list[str] = field(default_factory=list)
    blocked_roots: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    role_metadata: dict[str, Any] = field(default_factory=dict)
    role_metadata_digest: str = ""
    status: RuntimeWorkspaceBindingStatus = "bound"
    binding_digest: str = ""
    idempotency_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @property
    def stable_role_metadata_digest(self) -> str:
        return self.role_metadata_digest or stable_digest(self.role_metadata)

    @property
    def stable_binding_digest(self) -> str:
        if self.binding_digest:
            return self.binding_digest
        return runtime_workspace_binding_digest(
            sandbox_lease_id=self.sandbox_lease_id,
            attempt_id=self.attempt_id,
            runtime_name=self.runtime_name or self.runtime,
            cwd=self.cwd,
            workspace_override=self.workspace_override,
            manifest_path=self.manifest_path,
            repo_roots=self.repo_roots,
            writable_roots=self.writable_roots,
            readonly_roots=self.readonly_roots,
            blocked_roots=self.blocked_roots,
            env=self.env,
            role_metadata_digest=self.stable_role_metadata_digest,
        )

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:runtime-workspace-binding:{self.sandbox_lease_id}:"
            f"{self.attempt_id}:{self.runtime_name or self.runtime}:"
            f"{self.stable_binding_digest}"
        )


@dataclass(frozen=True)
class SandboxRepoBindingResult:
    binding: SandboxRepoBinding
    created: bool


@dataclass(frozen=True)
class RuntimeWorkspaceBindingResult:
    binding: RuntimeWorkspaceBinding
    created: bool


@dataclass(frozen=True)
class SandboxLeaseResult:
    lease: SandboxLease
    repo_bindings: tuple[SandboxRepoBinding, ...]
    execution: ExecutionJournalResult
    created: bool


@dataclass(frozen=True)
class TaskDeliverableContract:
    feature_id: str = ""
    dag_sha256: str = ""
    source_dag_artifact_id: int | None = None
    source_dag_sha256: str = ""
    group_idx: int = 0
    task_id: str = ""
    repo_id: str = ""
    repo_path: str = ""
    required_paths: list[dict[str, Any]] = field(default_factory=list)
    allowed_paths: list[dict[str, Any]] = field(default_factory=list)
    read_only_paths: list[dict[str, Any]] = field(default_factory=list)
    forbidden_paths: list[dict[str, Any]] = field(default_factory=list)
    generated_outputs: list[dict[str, Any]] = field(default_factory=list)
    acceptance_criteria: list[dict[str, Any]] = field(default_factory=list)
    verification_gates: list[dict[str, Any]] = field(default_factory=list)
    execution_policy: dict[str, Any] = field(default_factory=dict)
    non_goals: list[str] = field(default_factory=list)
    dependency_task_ids: list[str] = field(default_factory=list)
    unknown_write_set: bool = False
    compile_warnings: list[str] = field(default_factory=list)
    normalized_contract_json: dict[str, Any] = field(default_factory=dict)
    contract_digest: str = ""
    status: Literal["active", "superseded", "cancelled"] = "active"
    idempotency_key: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    id: int | None = None
    execution_journal_row_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TaskContractResult:
    contract: TaskDeliverableContract
    execution: ExecutionJournalResult
    created: bool


@dataclass(frozen=True)
class EvidenceNode:
    id: int
    feature_id: str
    idempotency_key: str
    kind: str
    status: str
    content_hash: str
    payload: dict[str, Any]
    execution_journal_row_id: int | None = None
    attempt_id: int | None = None
    contract_id: int | None = None
    snapshot_id: int | None = None
    group_idx: int | None = None
    stage: str = ""
    name: str = ""
    deterministic: bool = True
    source_ref: str = ""
    artifact_id: int | None = None
    artifact_key: str = ""
    event_id: int | None = None
    input_refs: list[Any] = field(default_factory=list)
    output_refs: list[Any] = field(default_factory=list)
    failure_id: int | None = None
    verdict_id: int | None = None
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class EvidenceNodeResult:
    evidence: EvidenceNode
    execution: ExecutionJournalResult
    created: bool


@dataclass(frozen=True)
class VerificationGraphNodeEvidence:
    feature_id: str
    idempotency_key: str
    kind: str
    status: str
    payload: dict[str, Any]
    content_hash: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    stage: str = ""
    name: str = ""
    deterministic: bool = True
    input_refs: list[Any] = field(default_factory=list)
    output_refs: list[Any] = field(default_factory=list)
    failure_id: int | None = None
    verdict_id: int | None = None
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    attempt_id: int | None = None
    contract_id: int | None = None
    snapshot_id: int | None = None
    artifact_id: int | None = None
    artifact_key: str = ""
    event_id: int | None = None

    @property
    def stable_content_hash(self) -> str:
        return self.content_hash or stable_digest({
            "deterministic": self.deterministic,
            "failure_id": self.failure_id,
            "input_refs": self.input_refs,
            "kind": self.kind,
            "metadata": self.metadata,
            "name": self.name,
            "output_refs": self.output_refs,
            "payload": self.payload,
            "status": self.status,
            "summary": self.summary,
            "verdict_id": self.verdict_id,
        })


@dataclass(frozen=True)
class VerificationGraphProjection:
    feature_id: str
    projection_key: str
    graph_payload: dict[str, Any]
    aggregate_node_id: int | None = None
    idempotency_key: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    stage: str = ""
    projection_body: Any | None = None
    approved: bool = False
    proof_digest: str = ""

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        digest = self.proof_digest or stable_digest(self.graph_payload)
        return (
            f"idem:verification-graph-projection:{self.feature_id}:"
            f"{self.projection_key}:{digest}"
        )


@dataclass(frozen=True)
class PatchSummary:
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    attempt_no: int | None = None
    sandbox_id: str = ""
    task_id: str = ""
    contract_ids: list[int] = field(default_factory=list)
    repo_id: str = ""
    base_commit: str | None = None
    changed_paths: list[str] = field(default_factory=list)
    created_paths: list[str] = field(default_factory=list)
    modified_paths: list[str] = field(default_factory=list)
    deleted_paths: list[str] = field(default_factory=list)
    renamed_paths: dict[str, str] = field(default_factory=dict)
    diff_sha256: str = ""
    diff_artifact_id: int | None = None
    summary_artifact_id: int | None = None
    summary: str = ""
    stage: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    id: int | None = None
    evidence_node_id: int | None = None

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        contract_identity = ",".join(str(item) for item in self.contract_ids) or "-"
        return (
            f"idem:sandbox-patch:{self.feature_id}:{self.dag_sha256}:"
            f"g{self.group_idx if self.group_idx is not None else '-'}:"
            f"attempt-{self.attempt_no if self.attempt_no is not None else '-'}:"
            f"repo-{self.repo_id}:task-{self.task_id or '-'}:"
            f"stage-{self.stage or '-'}:contracts-{contract_identity}:"
            f"{self.sandbox_id}:{self.base_commit or ''}:"
            f"{self.diff_sha256}"
        )

    @property
    def projection_key(self) -> str:
        return (
            f"dag-sandbox-patch:g{self.group_idx if self.group_idx is not None else '-'}:"
            f"attempt-{self.attempt_no if self.attempt_no is not None else '-'}:"
            f"repo-{self.repo_id}"
        )


@dataclass(frozen=True)
class ContractVerdict:
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    task_id: str = ""
    sandbox_id: str = ""
    contract_id: int = 0
    patch_summary_id: int = 0
    approved: bool = False
    violation_codes: list[str] = field(default_factory=list)
    violations: list[dict[str, str]] = field(default_factory=list)
    required_evidence_node_ids: list[int] = field(default_factory=list)
    workspace_snapshot_id: int | None = None
    stage: str = ""
    summary: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    payload: dict[str, Any] = field(default_factory=dict)
    idempotency_key: str = ""
    id: int | None = None
    evidence_node_id: int | None = None

    @property
    def stable_idempotency_key(self) -> str:
        if self.idempotency_key:
            return self.idempotency_key
        return (
            f"idem:contract-verdict:{self.feature_id}:{self.dag_sha256}:"
            f"g{self.group_idx if self.group_idx is not None else '-'}:"
            f"{self.task_id}:{self.sandbox_id}:{self.contract_id}:"
            f"{self.patch_summary_id}:{stable_digest(self.violation_codes)}"
        )

    @property
    def projection_key(self) -> str:
        return (
            f"dag-contract-verdict:g{self.group_idx if self.group_idx is not None else '-'}:"
            f"{self.task_id}:{self.sandbox_id}"
        )


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(stable_json(value).encode("utf-8")).hexdigest()


def _payload_dict_for_identity(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    model_dump = getattr(value, "model_dump", None)
    if model_dump is not None:
        try:
            dumped = model_dump(mode="json")
        except TypeError:
            dumped = model_dump()
        return dumped if isinstance(dumped, dict) else {}
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def projection_idempotency_key(
    *,
    feature_id: str,
    typed_row_id: int,
    projection: CompatibilityProjection,
) -> str:
    if projection.idempotency_key:
        return projection.idempotency_key
    return stable_digest({
        "feature_id": feature_id,
        "projection_sha256": stable_digest(projection.value),
        "projection_key": projection.key,
        "typed_row_id": typed_row_id,
    })


def workspace_snapshot_idempotency_key(
    *,
    feature_id: str,
    dag_sha256: str,
    group_idx: int | None,
    stage: str,
    repo_id: str,
    head_sha: str,
    index_digest: str,
    worktree_status_digest: str,
) -> str:
    return (
        f"snapshot:{feature_id}:{dag_sha256}:"
        f"g{group_idx if group_idx is not None else '-'}:"
        f"{stage}:{repo_id}:{head_sha}:{index_digest}:{worktree_status_digest}"
    )


def workspace_snapshot_projection_key(
    *,
    feature_id: str,
    dag_sha256: str,
    group_idx: int | None,
    stage: str,
    repo_id: str,
) -> str:
    digest = stable_digest({
        "dag_sha256": dag_sha256,
        "feature_id": feature_id,
        "group_idx": group_idx,
        "repo_id": repo_id,
        "stage": stage,
    })[:16]
    return (
        f"workspace-snapshot:g{group_idx if group_idx is not None else '-'}:"
        f"{stage or 'snapshot'}:{repo_id or digest}"
    )


def sandbox_manifest_projection_key(*, group_idx: int | None, attempt_no: int | None) -> str:
    return (
        f"dag-sandbox:g{group_idx if group_idx is not None else '-'}:"
        f"attempt-{attempt_no if attempt_no is not None else '-'}"
    )


def sandbox_lease_idempotency_key(
    *,
    feature_id: str,
    dag_sha256: str,
    group_idx: int | None,
    attempt_no: int | None,
    mode: str,
    repo_ids: list[str],
    base_commits: dict[str, str],
    contract_ids: list[int],
) -> str:
    repo_digest = stable_digest(sorted(str(item) for item in repo_ids))[:16]
    base_digest = stable_digest({
        str(key): str(value)
        for key, value in sorted(base_commits.items(), key=lambda item: str(item[0]))
    })[:16]
    contract_digest = stable_digest(sorted(int(item) for item in contract_ids))[:16]
    return (
        f"idem:sandbox-lease:{feature_id}:{dag_sha256}:"
        f"g{group_idx if group_idx is not None else '-'}:"
        f"attempt-{attempt_no if attempt_no is not None else '-'}:"
        f"{mode}:repos-{repo_digest}:base-{base_digest}:contracts-{contract_digest}"
    )


def sandbox_lease_digest(
    *,
    sandbox_id: str,
    sandbox_root: str,
    manifest_path: str,
    base_snapshot_ids: list[int],
    repo_ids: list[str],
    base_commits: dict[str, str],
    mode: str,
    lease_owner: str,
    task_ids: list[str],
    contract_ids: list[int],
    writable_roots: list[str],
    readonly_roots: list[str],
    blocked_roots: list[str],
) -> str:
    return stable_digest({
        "base_commits": {
            str(key): str(value)
            for key, value in sorted(base_commits.items(), key=lambda item: str(item[0]))
        },
        "base_snapshot_ids": sorted(int(item) for item in base_snapshot_ids),
        "blocked_roots": sorted(str(item) for item in blocked_roots),
        "contract_ids": sorted(int(item) for item in contract_ids),
        "lease_owner": lease_owner,
        "manifest_path": manifest_path,
        "mode": mode,
        "readonly_roots": sorted(str(item) for item in readonly_roots),
        "repo_ids": sorted(str(item) for item in repo_ids),
        "sandbox_id": sandbox_id,
        "sandbox_root": sandbox_root,
        "task_ids": sorted(str(item) for item in task_ids),
        "writable_roots": sorted(str(item) for item in writable_roots),
    })


def sandbox_repo_binding_digest(
    *,
    sandbox_lease_id: int,
    repo_id: str,
    sandbox_repo_root: str,
    canonical_repo_root: str,
    base_snapshot_id: int,
    base_commit: str,
    writable: bool,
    writable_roots: list[str],
    readonly_roots: list[str],
    blocked_canonical_roots: list[str],
) -> str:
    return stable_digest({
        "base_commit": base_commit,
        "base_snapshot_id": int(base_snapshot_id or 0),
        "blocked_canonical_roots": sorted(str(item) for item in blocked_canonical_roots),
        "canonical_repo_root": canonical_repo_root,
        "readonly_roots": sorted(str(item) for item in readonly_roots),
        "repo_id": repo_id,
        "sandbox_lease_id": int(sandbox_lease_id or 0),
        "sandbox_repo_root": sandbox_repo_root,
        "writable": bool(writable),
        "writable_roots": sorted(str(item) for item in writable_roots),
    })


def runtime_workspace_binding_digest(
    *,
    sandbox_lease_id: int,
    attempt_id: int,
    runtime_name: str,
    cwd: str,
    workspace_override: str,
    manifest_path: str,
    repo_roots: dict[str, str],
    writable_roots: list[str],
    readonly_roots: list[str],
    blocked_roots: list[str],
    env: dict[str, str],
    role_metadata_digest: str,
) -> str:
    return stable_digest({
        "attempt_id": int(attempt_id or 0),
        "blocked_roots": sorted(str(item) for item in blocked_roots),
        "cwd": cwd,
        "env": {str(key): str(value) for key, value in sorted(env.items())},
        "manifest_path": manifest_path,
        "readonly_roots": sorted(str(item) for item in readonly_roots),
        "repo_roots": {str(key): str(value) for key, value in sorted(repo_roots.items())},
        "role_metadata_digest": role_metadata_digest,
        "runtime_name": runtime_name,
        "sandbox_lease_id": int(sandbox_lease_id or 0),
        "workspace_override": workspace_override,
        "writable_roots": sorted(str(item) for item in writable_roots),
    })


class _ProjectionInput:
    """Flexible projection DTO for current tests and later typed variants."""

    def __init__(self, **kwargs: Any) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class TaskResultProjection(_ProjectionInput):
    pass


class VerifyProjection(_ProjectionInput):
    pass


class CommitFailureProjection(_ProjectionInput):
    pass


class GroupCheckpointProjection(_ProjectionInput):
    pass


class RegroupProjection(_ProjectionInput):
    pass


class RegroupActiveProjection(_ProjectionInput):
    pass
