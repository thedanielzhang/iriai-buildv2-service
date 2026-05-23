from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import PurePosixPath
from typing import Any, Literal, Mapping, Protocol, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator

FailureSeverity: TypeAlias = Literal["info", "warning", "error", "fatal"]

FailureClass: TypeAlias = Literal[
    "product_defect",
    "contract_compile",
    "contract_violation",
    "stale_projection",
    "worktree_alias",
    "acl_workability",
    "sandbox_allocation",
    "sandbox_binding",
    "sandbox_isolation",
    "sandbox_capture",
    "sandbox_cleanup",
    "commit_hygiene",
    "merge_conflict",
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "dispatcher_internal",
    "verifier_provider",
    "verifier_context",
    "checkpoint_contradiction",
    "regroup_invalid",
    "evidence_corruption",
    "resource_exhausted",
    "operator_required",
    "unknown",
]

FailureType: TypeAlias = Literal[
    "semantic_verifier_rejected",
    "required_path_missing",
    "contract_invalid_path",
    "contract_scope_conflict",
    "contract_missing_dependency",
    "contract_same_wave_dependency",
    "outside_allowed_paths",
    "forbidden_path_touched",
    "read_only_path_touched",
    "contract_id_mismatch",
    "alias_points_to_noncanonical_root",
    "alias_only_canonical_missing",
    "alias_canonical_divergent",
    "unwritable_runtime_path",
    "sandbox_clone_failed",
    "sandbox_disk_quota",
    "sandbox_base_snapshot_unavailable",
    "runtime_workspace_binding_failed",
    "canonical_path_exposed_to_writer",
    "path_escape_detected",
    "patch_capture_failed",
    "sandbox_index_corrupt",
    "cleanup_failed",
    "commit_hook_failed",
    "dirty_after_commit",
    "stale_base_commit",
    "rebase_conflict",
    "patch_apply_conflict",
    "provider_internal_error",
    "provider_rate_limited",
    "provider_transport_error",
    "process_failed",
    "watchdog_timeout",
    "runtime_cancelled",
    "prompt_too_large",
    "context_materialization_failed",
    "context_permission_denied",
    "malformed_structured_output",
    "idempotency_conflict",
    "verifier_context_stale",
    "workspace_snapshot_stale",
    "verifier_provider_timeout",
    "verifier_provider_crash",
    "verifier_parse_failed",
    "checkpoint_after_failed_gate",
    "regroup_dependency_cycle",
    "regroup_write_conflict",
    "artifact_hash_mismatch",
    "payload_digest_mismatch",
    "projection_body_conflict",
    "db_resource_exhausted",
    "disk_resource_exhausted",
    "process_resource_exhausted",
    "provider_quota_exhausted",
    "operator_clearance_required",
    "unclassified",
]

RouteAction: TypeAlias = Literal[
    "retry_dispatch",
    "run_product_repair",
    "run_contract_repair",
    "run_canonicalization_repair",
    "run_workspace_repair",
    "run_commit_hygiene_repair",
    "retry_verifier",
    "retry_merge",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
    "quiesce",
    "operator_required",
]

FailureSource: TypeAlias = Literal[
    "dispatcher",
    "workspace_authority",
    "contract",
    "sandbox",
    "verification_graph",
    "merge_queue",
    "regroup",
    "journal",
    "artifact_store",
]

FAILURE_SEVERITIES: tuple[str, ...] = ("info", "warning", "error", "fatal")

FAILURE_CLASSES: tuple[str, ...] = (
    "product_defect",
    "contract_compile",
    "contract_violation",
    "stale_projection",
    "worktree_alias",
    "acl_workability",
    "sandbox_allocation",
    "sandbox_binding",
    "sandbox_isolation",
    "sandbox_capture",
    "sandbox_cleanup",
    "commit_hygiene",
    "merge_conflict",
    "runtime_provider",
    "runtime_timeout",
    "runtime_cancelled",
    "runtime_context",
    "runtime_structured_output",
    "dispatcher_internal",
    "verifier_provider",
    "verifier_context",
    "checkpoint_contradiction",
    "regroup_invalid",
    "evidence_corruption",
    "resource_exhausted",
    "operator_required",
    "unknown",
)

FAILURE_TYPES: tuple[str, ...] = (
    "semantic_verifier_rejected",
    "required_path_missing",
    "contract_invalid_path",
    "contract_scope_conflict",
    "contract_missing_dependency",
    "contract_same_wave_dependency",
    "outside_allowed_paths",
    "forbidden_path_touched",
    "read_only_path_touched",
    "contract_id_mismatch",
    "alias_points_to_noncanonical_root",
    "alias_only_canonical_missing",
    "alias_canonical_divergent",
    "unwritable_runtime_path",
    "sandbox_clone_failed",
    "sandbox_disk_quota",
    "sandbox_base_snapshot_unavailable",
    "runtime_workspace_binding_failed",
    "canonical_path_exposed_to_writer",
    "path_escape_detected",
    "patch_capture_failed",
    "sandbox_index_corrupt",
    "cleanup_failed",
    "commit_hook_failed",
    "dirty_after_commit",
    "stale_base_commit",
    "rebase_conflict",
    "patch_apply_conflict",
    "provider_internal_error",
    "provider_rate_limited",
    "provider_transport_error",
    "process_failed",
    "watchdog_timeout",
    "runtime_cancelled",
    "prompt_too_large",
    "context_materialization_failed",
    "context_permission_denied",
    "malformed_structured_output",
    "idempotency_conflict",
    "verifier_context_stale",
    "workspace_snapshot_stale",
    "verifier_provider_timeout",
    "verifier_provider_crash",
    "verifier_parse_failed",
    "checkpoint_after_failed_gate",
    "regroup_dependency_cycle",
    "regroup_write_conflict",
    "artifact_hash_mismatch",
    "payload_digest_mismatch",
    "projection_body_conflict",
    "db_resource_exhausted",
    "disk_resource_exhausted",
    "process_resource_exhausted",
    "provider_quota_exhausted",
    "operator_clearance_required",
    "unclassified",
)

ROUTE_ACTIONS: tuple[str, ...] = (
    "retry_dispatch",
    "run_product_repair",
    "run_contract_repair",
    "run_canonicalization_repair",
    "run_workspace_repair",
    "run_commit_hygiene_repair",
    "retry_verifier",
    "retry_merge",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
    "quiesce",
    "operator_required",
)

FAILURE_SOURCES: tuple[str, ...] = (
    "dispatcher",
    "workspace_authority",
    "contract",
    "sandbox",
    "verification_graph",
    "merge_queue",
    "regroup",
    "journal",
    "artifact_store",
)

_VOLATILE_FIELD_NAMES = frozenset(
    {
        "attempt",
        "attempt_no",
        "attempt_number",
        "attempt_ordinal",
        "captured_at",
        "completed_at",
        "created",
        "created_at",
        "duration_ms",
        "elapsed_ms",
        "finished_at",
        "idempotency_key",
        "line",
        "line_no",
        "line_number",
        "pid",
        "process_id",
        "raw_stderr",
        "raw_stdout",
        "raw_text",
        "retry",
        "retry_count",
        "retry_ordinal",
        "source_verdict_key",
        "started_at",
        "status",
        "stderr",
        "stderr_body",
        "stdout",
        "stdout_body",
        "timestamp",
        "updated_at",
        "wall_time_ms",
    }
)

_DIRECT_ROUTE_SOURCE_RE = re.compile(
    r"^dag-verify:g(?P<group_idx>\d+):(?P<suffix>initial|retry-\d+|checkpoint-commit)$"
)
_DIRECT_PRODUCT_REPAIR_ROUTES = frozenset({"normal_verify_repair"})
_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES = frozenset({"manifest_forbidden_product_cleanup"})

_PATH_FIELD_NAMES = frozenset(
    {
        "allowed_paths",
        "blocked_paths",
        "canonical_path",
        "canonical_root",
        "context_file_paths",
        "forbidden_paths",
        "offending_path",
        "offending_paths",
        "path",
        "paths",
        "readonly_roots",
        "repo_path",
        "repo_paths",
        "repo_root",
        "repo_roots",
        "root",
        "roots",
        "target_path",
        "target_paths",
        "touched_paths",
        "workspace_path",
        "workspace_root",
        "writable_roots",
    }
)

_UNORDERED_FIELD_NAMES = frozenset(
    {
        "allowed_paths",
        "base_commits",
        "contract_ids",
        "evidence_ids",
        "gate_ids",
        "offending_paths",
        "paths",
        "repo_ids",
        "required_evidence_ids",
        "snapshot_ids",
        "source_evidence_ids",
        "target_contract_ids",
        "target_paths",
        "touched_paths",
        "workspace_snapshot_ids",
    }
)

_SCOPED_CONTRACT_PRODUCT_TYPES = frozenset(
    {"outside_allowed_paths", "forbidden_path_touched", "read_only_path_touched"}
)

CLASS_RETRY_BUDGETS: dict[str, int] = {
    "product_defect": 2,
    "contract_compile": 1,
    "contract_violation": 1,
    "stale_projection": 1,
    "worktree_alias": 1,
    "acl_workability": 1,
    "sandbox_allocation": 2,
    "sandbox_binding": 0,
    "sandbox_isolation": 0,
    "sandbox_capture": 1,
    "sandbox_cleanup": 3,
    "commit_hygiene": 1,
    "merge_conflict": 1,
    "runtime_provider": 2,
    "runtime_timeout": 1,
    "runtime_cancelled": 0,
    "runtime_context": 1,
    "runtime_structured_output": 1,
    "dispatcher_internal": 0,
    "verifier_provider": 2,
    "verifier_context": 1,
    "checkpoint_contradiction": 0,
    "regroup_invalid": 0,
    "evidence_corruption": 1,
    "resource_exhausted": 1,
    "operator_required": 0,
    "unknown": 0,
}

_DETERMINISTIC_FAILURE_TYPES = frozenset(
    {
        "contract_invalid_path",
        "contract_scope_conflict",
        "contract_missing_dependency",
        "contract_same_wave_dependency",
        "outside_allowed_paths",
        "forbidden_path_touched",
        "read_only_path_touched",
        "contract_id_mismatch",
        "alias_points_to_noncanonical_root",
        "alias_only_canonical_missing",
        "alias_canonical_divergent",
        "unwritable_runtime_path",
        "workspace_snapshot_stale",
        "runtime_workspace_binding_failed",
        "canonical_path_exposed_to_writer",
        "path_escape_detected",
        "sandbox_index_corrupt",
        "commit_hook_failed",
        "dirty_after_commit",
        "prompt_too_large",
        "context_materialization_failed",
        "context_permission_denied",
        "malformed_structured_output",
        "idempotency_conflict",
        "verifier_context_stale",
        "checkpoint_after_failed_gate",
        "regroup_dependency_cycle",
        "regroup_write_conflict",
        "artifact_hash_mismatch",
        "payload_digest_mismatch",
        "projection_body_conflict",
        "operator_clearance_required",
    }
)

_RETRYABLE_FAILURE_TYPES = frozenset(
    {
        "semantic_verifier_rejected",
        "required_path_missing",
        "alias_points_to_noncanonical_root",
        "alias_only_canonical_missing",
        "alias_canonical_divergent",
        "unwritable_runtime_path",
        "workspace_snapshot_stale",
        "sandbox_clone_failed",
        "sandbox_disk_quota",
        "sandbox_base_snapshot_unavailable",
        "patch_capture_failed",
        "cleanup_failed",
        "commit_hook_failed",
        "dirty_after_commit",
        "stale_base_commit",
        "rebase_conflict",
        "patch_apply_conflict",
        "provider_internal_error",
        "provider_rate_limited",
        "provider_transport_error",
        "process_failed",
        "watchdog_timeout",
        "prompt_too_large",
        "context_materialization_failed",
        "malformed_structured_output",
        "verifier_context_stale",
        "verifier_provider_timeout",
        "verifier_provider_crash",
        "verifier_parse_failed",
        "db_resource_exhausted",
        "disk_resource_exhausted",
        "process_resource_exhausted",
        "provider_quota_exhausted",
    }
)

_OPERATOR_REQUIRED_FAILURE_TYPES = frozenset(
    {"context_permission_denied", "operator_clearance_required"}
)

_RETRY_ACTIONS = frozenset(
    {"retry_dispatch", "retry_verifier", "retry_merge", "retry_sandbox_capture"}
)


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_digest(value: Any) -> str:
    return hashlib.sha256(_stable_json(value).encode("utf-8")).hexdigest()


def _normalize_path(value: str) -> str:
    raw = value.replace("\\", "/").strip()
    if not raw:
        return raw
    raw = re.sub(r"^[A-Za-z]:", "", raw)
    parts: list[str] = []
    for part in raw.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts and parts[-1] != "..":
                parts.pop()
            else:
                parts.append(part)
            continue
        parts.append(part)
    normalized = str(PurePosixPath(*parts)) if parts else "."
    return f"/{normalized}" if raw.startswith("/") and normalized != "." else normalized


def _looks_like_path_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in _PATH_FIELD_NAMES or lowered.endswith("_path") or lowered.endswith("_paths")


def _normalize_scalar_for_signature(key: str, value: Any) -> Any:
    if isinstance(value, str) and _looks_like_path_key(key):
        return _normalize_path(value)
    return value


def _canonicalize_for_signature(key: str, value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_child_key, child_value in value.items():
            child_key = str(raw_child_key)
            if child_key in _VOLATILE_FIELD_NAMES:
                continue
            result[child_key] = _canonicalize_for_signature(child_key, child_value)
        return {child_key: result[child_key] for child_key in sorted(result)}

    if isinstance(value, (list, tuple, set, frozenset)):
        normalized_items = [
            _canonicalize_for_signature(key, item)
            for item in value
        ]
        if key in _UNORDERED_FIELD_NAMES or _looks_like_path_key(key):
            return sorted(normalized_items, key=_stable_json)
        return normalized_items

    return _normalize_scalar_for_signature(key, value)


def _payload_value(payload: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload[key]
    return None


def _str_list(value: Any, *, path: bool = False) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    result = []
    for item in values:
        if item is None:
            continue
        text = str(item)
        result.append(_normalize_path(text) if path else text)
    return sorted(set(result))


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    values = value if isinstance(value, (list, tuple, set, frozenset)) else [value]
    result: list[int] = []
    for item in values:
        if item is None:
            continue
        result.append(int(item))
    return sorted(set(result))


class FailureRouterError(RuntimeError):
    pass


class IdempotencyConflict(FailureRouterError):
    def __init__(
        self,
        idempotency_key: str,
        existing_digest: str,
        incoming_digest: str,
    ) -> None:
        super().__init__(
            "idempotency conflict for "
            f"{idempotency_key}: {existing_digest} != {incoming_digest}"
        )
        self.idempotency_key = idempotency_key
        self.existing_digest = existing_digest
        self.incoming_digest = incoming_digest


class UnknownFailurePolicyError(FailureRouterError):
    pass


class _RouterModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


class FailureObservation(_RouterModel):
    feature_id: str
    dag_sha256: str
    group_idx: int | None = None
    task_id: str | None = None
    attempt_id: int | None = None
    source: FailureSource
    failure_class: FailureClass
    failure_type: FailureType
    severity: FailureSeverity = "error"
    deterministic: bool
    retryable: bool
    operator_required: bool = False
    evidence_ids: list[int]
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_ids", mode="before")
    @classmethod
    def _sort_evidence_ids(cls, value: Any) -> list[int]:
        if value is None:
            return []
        return sorted({int(item) for item in value})


class FailureTypePolicy(_RouterModel):
    failure_class: FailureClass
    failure_type: FailureType
    severity: FailureSeverity = "error"
    deterministic: bool
    retryable: bool
    operator_required: bool = False


class FailureRoutePolicy(_RouterModel):
    failure_class: FailureClass
    failure_type: FailureType
    action: RouteAction
    budget: int
    reason: str
    repair_kind: str | None = None
    allow_product_repair: bool = False
    requires_scoped_product_repair: bool = False


class RouteDecision(_RouterModel):
    failure_id: int
    route_decision_id: int | None
    action: RouteAction
    budget_remaining: int
    budget_exhausted: bool = False
    reason: str
    required_evidence_ids: list[int]
    signature_hash: str
    idempotency_key: str
    repair_scope: dict[str, Any] = Field(default_factory=dict)
    budget_key: str = ""
    reservation_ordinal: int = 0
    started: bool = False

    @field_validator("required_evidence_ids", mode="before")
    @classmethod
    def _sort_required_evidence_ids(cls, value: Any) -> list[int]:
        if value is None:
            return []
        return sorted({int(item) for item in value})


class RetryBudgetState(_RouterModel):
    budget_key: str
    feature_id: str
    failure_class: FailureClass
    failure_type: FailureType
    signature_hash: str
    max_attempts: int
    reserved_attempts: int = 0
    completed_attempts: int = 0
    last_failure_id: int | None = None
    last_route_decision_id: int | None = None

    @property
    def budget_remaining(self) -> int:
        return max(self.max_attempts - self.reserved_attempts, 0)

    @property
    def exhausted(self) -> bool:
        return self.budget_remaining <= 0


class FailureRecord(_RouterModel):
    failure_id: int | None = None
    observation: FailureObservation
    policy: FailureTypePolicy
    signature_hash: str
    idempotency_key: str
    input_digest: str
    occurrence_count: int = 1


class RouteRecord(_RouterModel):
    route_decision_id: int | None = None
    decision: RouteDecision
    input_digest: str
    status: Literal["started", "succeeded", "failed"] = "started"
    produced_failure_id: int | None = None


class FailureRouterPort(Protocol):
    def record_failure(self, record: FailureRecord) -> FailureRecord: ...
    def get_failure(self, failure_id: int) -> FailureRecord | None: ...
    def get_failure_by_key(self, idempotency_key: str) -> FailureRecord | None: ...
    def get_budget(self, budget_key: str) -> RetryBudgetState | None: ...
    def reserve_budget(
        self,
        *,
        budget_key: str,
        feature_id: str,
        failure_class: FailureClass,
        failure_type: FailureType,
        signature_hash: str,
        max_attempts: int,
        failure_id: int,
    ) -> RetryBudgetState: ...
    def get_route_by_key(self, idempotency_key: str) -> RouteRecord | None: ...
    def record_route_started(
        self,
        decision: RouteDecision,
        input_digest: str,
        *,
        budget_reservation: dict[str, Any] | None = None,
    ) -> RouteRecord: ...
    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None: ...


class InMemoryFailureRouterPort:
    def __init__(self) -> None:
        self._next_failure_id = 1
        self._next_route_decision_id = 1
        self.failures: dict[int, FailureRecord] = {}
        self.failures_by_key: dict[str, int] = {}
        self.budgets: dict[str, RetryBudgetState] = {}
        self.routes: dict[int, RouteRecord] = {}
        self.routes_by_key: dict[str, int] = {}

    def record_failure(self, record: FailureRecord) -> FailureRecord:
        existing_id = self.failures_by_key.get(record.idempotency_key)
        if existing_id is not None:
            existing = self.failures[existing_id]
            if existing.input_digest != record.input_digest:
                raise IdempotencyConflict(
                    record.idempotency_key,
                    existing.input_digest,
                    record.input_digest,
                )
            updated = existing.model_copy(
                update={"occurrence_count": existing.occurrence_count + 1}
            )
            self.failures[existing_id] = updated
            return updated

        failure_id = self._next_failure_id
        self._next_failure_id += 1
        stored = record.model_copy(update={"failure_id": failure_id})
        self.failures[failure_id] = stored
        self.failures_by_key[stored.idempotency_key] = failure_id
        return stored

    def get_failure(self, failure_id: int) -> FailureRecord | None:
        return self.failures.get(failure_id)

    def get_failure_by_key(self, idempotency_key: str) -> FailureRecord | None:
        failure_id = self.failures_by_key.get(idempotency_key)
        return None if failure_id is None else self.failures[failure_id]

    def get_budget(self, budget_key: str) -> RetryBudgetState | None:
        return self.budgets.get(budget_key)

    def reserve_budget(
        self,
        *,
        budget_key: str,
        feature_id: str,
        failure_class: FailureClass,
        failure_type: FailureType,
        signature_hash: str,
        max_attempts: int,
        failure_id: int,
    ) -> RetryBudgetState:
        state = self.budgets.get(budget_key)
        if state is None:
            state = RetryBudgetState(
                budget_key=budget_key,
                feature_id=feature_id,
                failure_class=failure_class,
                failure_type=failure_type,
                signature_hash=signature_hash,
                max_attempts=max_attempts,
            )
        if state.reserved_attempts >= state.max_attempts:
            updated = state.model_copy(update={"last_failure_id": failure_id})
            self.budgets[budget_key] = updated
            return updated

        updated = state.model_copy(
            update={
                "reserved_attempts": state.reserved_attempts + 1,
                "last_failure_id": failure_id,
            }
        )
        self.budgets[budget_key] = updated
        return updated

    def get_route_by_key(self, idempotency_key: str) -> RouteRecord | None:
        route_id = self.routes_by_key.get(idempotency_key)
        return None if route_id is None else self.routes[route_id]

    def record_route_started(
        self,
        decision: RouteDecision,
        input_digest: str,
        *,
        budget_reservation: dict[str, Any] | None = None,
    ) -> RouteRecord:
        existing_id = self.routes_by_key.get(decision.idempotency_key)
        if existing_id is not None:
            existing = self.routes[existing_id]
            if existing.input_digest != input_digest and not _route_replay_compatible(
                existing.decision,
                decision,
            ):
                raise IdempotencyConflict(
                    decision.idempotency_key,
                    existing.input_digest,
                    input_digest,
                )
            return existing

        stored_decision = decision
        stored_input_digest = input_digest
        if budget_reservation is not None:
            budget_key = str(budget_reservation["budget_key"])
            max_attempts = int(budget_reservation["max_attempts"])
            failure_id = int(budget_reservation["failure_id"])
            state = self.budgets.get(budget_key)
            if state is None:
                state = RetryBudgetState(
                    budget_key=budget_key,
                    feature_id=str(budget_reservation["feature_id"]),
                    failure_class=budget_reservation["failure_class"],
                    failure_type=budget_reservation["failure_type"],
                    signature_hash=str(budget_reservation["signature_hash"]),
                    max_attempts=max_attempts,
                )
            if state.reserved_attempts >= state.max_attempts:
                ordinal = state.reserved_attempts
                idempotency_key = _route_idempotency_key_from_parts(
                    feature_id=str(budget_reservation["feature_id"]),
                    failure_id=failure_id,
                    signature_hash=str(budget_reservation["signature_hash"]),
                    action="quiesce",
                    reservation_ordinal=ordinal,
                )
                stored_decision = decision.model_copy(
                    update={
                        "action": "quiesce",
                        "budget_remaining": 0,
                        "budget_exhausted": True,
                        "idempotency_key": idempotency_key,
                        "reservation_ordinal": ordinal,
                        "reason": (
                            "retry budget exhausted for "
                            f"{budget_reservation['failure_class']}/"
                            f"{budget_reservation['failure_type']}"
                        ),
                    }
                )
                self.budgets[budget_key] = state.model_copy(
                    update={"last_failure_id": failure_id}
                )
            else:
                ordinal = state.reserved_attempts + 1
                state = state.model_copy(
                    update={
                        "reserved_attempts": ordinal,
                        "last_failure_id": failure_id,
                    }
                )
                self.budgets[budget_key] = state
                idempotency_key = _route_idempotency_key_from_parts(
                    feature_id=str(budget_reservation["feature_id"]),
                    failure_id=failure_id,
                    signature_hash=str(budget_reservation["signature_hash"]),
                    action=decision.action,
                    reservation_ordinal=ordinal,
                )
                stored_decision = decision.model_copy(
                    update={
                        "budget_remaining": state.budget_remaining,
                        "idempotency_key": idempotency_key,
                        "reservation_ordinal": ordinal,
                    }
                )
            stored_input_digest = _route_input_digest(stored_decision)
            existing_id = self.routes_by_key.get(stored_decision.idempotency_key)
            if existing_id is not None:
                existing = self.routes[existing_id]
                if existing.input_digest != stored_input_digest:
                    raise IdempotencyConflict(
                        stored_decision.idempotency_key,
                        existing.input_digest,
                        stored_input_digest,
                    )
                return existing

        route_decision_id = self._next_route_decision_id
        self._next_route_decision_id += 1
        stored_decision = stored_decision.model_copy(
            update={
                "route_decision_id": route_decision_id,
                "started": True,
            }
        )
        record = RouteRecord(
            route_decision_id=route_decision_id,
            decision=stored_decision,
            input_digest=stored_input_digest,
        )
        self.routes[route_decision_id] = record
        self.routes_by_key[stored_decision.idempotency_key] = route_decision_id
        if decision.idempotency_key != stored_decision.idempotency_key:
            self.routes_by_key[decision.idempotency_key] = route_decision_id
        return record

    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None:
        if decision.route_decision_id is None:
            return
        record = self.routes.get(decision.route_decision_id)
        if record is None:
            return
        status: Literal["started", "succeeded", "failed"] = (
            "succeeded" if succeeded else "failed"
        )
        self.routes[decision.route_decision_id] = record.model_copy(
            update={"status": status, "produced_failure_id": produced_failure_id}
        )
        budget = self.budgets.get(decision.budget_key)
        if budget is not None and record.status == "started":
            self.budgets[decision.budget_key] = budget.model_copy(
                update={
                    "completed_attempts": budget.completed_attempts + 1,
                    "last_route_decision_id": decision.route_decision_id,
                }
            )


def _route(
    failure_class: FailureClass,
    failure_type: FailureType,
    action: RouteAction,
    reason: str,
    *,
    budget: int | None = None,
    severity: FailureSeverity = "error",
    deterministic: bool | None = None,
    retryable: bool | None = None,
    operator_required: bool | None = None,
    repair_kind: str | None = None,
) -> tuple[FailureTypePolicy, FailureRoutePolicy]:
    resolved_budget = CLASS_RETRY_BUDGETS[failure_class] if budget is None else budget
    resolved_operator_required = (
        action == "operator_required" or failure_type in _OPERATOR_REQUIRED_FAILURE_TYPES
        if operator_required is None
        else operator_required
    )
    resolved_deterministic = (
        failure_type in _DETERMINISTIC_FAILURE_TYPES
        if deterministic is None
        else deterministic
    )
    resolved_retryable = (
        failure_type in _RETRYABLE_FAILURE_TYPES
        if retryable is None
        else retryable
    )
    allow_product = action == "run_product_repair" and (
        failure_class == "product_defect"
        or (
            failure_class == "contract_violation"
            and failure_type in _SCOPED_CONTRACT_PRODUCT_TYPES
        )
    )
    policy = FailureTypePolicy(
        failure_class=failure_class,
        failure_type=failure_type,
        severity=severity,
        deterministic=resolved_deterministic,
        retryable=resolved_retryable,
        operator_required=resolved_operator_required,
    )
    route_policy = FailureRoutePolicy(
        failure_class=failure_class,
        failure_type=failure_type,
        action=action,
        budget=resolved_budget,
        reason=reason,
        repair_kind=repair_kind,
        allow_product_repair=allow_product,
        requires_scoped_product_repair=(
            action == "run_product_repair" and failure_class == "contract_violation"
        ),
    )
    return policy, route_policy


_ROUTE_ROWS = (
    _route(
        "product_defect",
        "semantic_verifier_rejected",
        "run_product_repair",
        "semantic verifier rejected product behavior",
        repair_kind="product",
    ),
    _route(
        "product_defect",
        "required_path_missing",
        "run_product_repair",
        "required product path missing",
        repair_kind="product",
    ),
    _route(
        "contract_compile",
        "contract_invalid_path",
        "run_contract_repair",
        "contract contains an invalid path",
        repair_kind="contract",
    ),
    _route(
        "contract_compile",
        "contract_scope_conflict",
        "quiesce",
        "contract scope conflict must be resolved before dispatch",
    ),
    _route(
        "contract_compile",
        "contract_missing_dependency",
        "quiesce",
        "contract dependency is missing",
    ),
    _route(
        "contract_compile",
        "contract_same_wave_dependency",
        "quiesce",
        "contract depends on a same-wave task",
    ),
    _route(
        "contract_violation",
        "outside_allowed_paths",
        "run_product_repair",
        "product patch touched paths outside the contract",
        repair_kind="product",
    ),
    _route(
        "contract_violation",
        "forbidden_path_touched",
        "run_product_repair",
        "product patch touched forbidden paths",
        repair_kind="product",
    ),
    _route(
        "contract_violation",
        "read_only_path_touched",
        "run_product_repair",
        "product patch touched read-only paths",
        repair_kind="product",
    ),
    _route(
        "contract_violation",
        "contract_id_mismatch",
        "quiesce",
        "contract id mismatch is not repairable by product edits",
    ),
    _route(
        "stale_projection",
        "verifier_context_stale",
        "retry_verifier",
        "verifier projection is stale",
    ),
    _route(
        "stale_projection",
        "workspace_snapshot_stale",
        "run_workspace_repair",
        "workspace snapshot projection is stale",
        repair_kind="workspace",
    ),
    _route(
        "worktree_alias",
        "alias_points_to_noncanonical_root",
        "run_canonicalization_repair",
        "worktree alias points to a noncanonical root",
        repair_kind="canonicalization",
    ),
    _route(
        "worktree_alias",
        "alias_only_canonical_missing",
        "run_canonicalization_repair",
        "canonical root is missing for an alias",
        repair_kind="canonicalization",
    ),
    _route(
        "worktree_alias",
        "alias_canonical_divergent",
        "run_canonicalization_repair",
        "canonical and alias worktrees diverged",
        repair_kind="canonicalization",
    ),
    _route(
        "acl_workability",
        "unwritable_runtime_path",
        "run_workspace_repair",
        "runtime path is not writable",
        repair_kind="workspace",
    ),
    _route(
        "sandbox_allocation",
        "sandbox_clone_failed",
        "retry_dispatch",
        "sandbox clone failed",
    ),
    _route(
        "sandbox_allocation",
        "sandbox_disk_quota",
        "quiesce",
        "sandbox disk quota is exhausted",
    ),
    _route(
        "sandbox_allocation",
        "sandbox_base_snapshot_unavailable",
        "retry_dispatch",
        "sandbox base snapshot is unavailable",
    ),
    _route(
        "sandbox_binding",
        "runtime_workspace_binding_failed",
        "quiesce",
        "runtime workspace binding failed",
    ),
    _route(
        "sandbox_isolation",
        "canonical_path_exposed_to_writer",
        "quiesce",
        "canonical path was exposed to writer runtime",
    ),
    _route(
        "sandbox_isolation",
        "path_escape_detected",
        "quiesce",
        "sandbox path escape detected",
    ),
    _route(
        "sandbox_capture",
        "patch_capture_failed",
        "retry_sandbox_capture",
        "patch capture failed",
    ),
    _route(
        "sandbox_capture",
        "sandbox_index_corrupt",
        "quiesce",
        "sandbox index is corrupt",
    ),
    _route(
        "sandbox_cleanup",
        "cleanup_failed",
        "run_sandbox_cleanup",
        "sandbox cleanup failed",
        repair_kind="sandbox_cleanup",
    ),
    _route(
        "commit_hygiene",
        "commit_hook_failed",
        "run_commit_hygiene_repair",
        "commit hook failed",
        repair_kind="commit_hygiene",
    ),
    _route(
        "commit_hygiene",
        "dirty_after_commit",
        "run_commit_hygiene_repair",
        "commit left dirty worktree state",
        repair_kind="commit_hygiene",
    ),
    _route(
        "merge_conflict",
        "stale_base_commit",
        "retry_merge",
        "merge base commit is stale",
    ),
    _route(
        "merge_conflict",
        "rebase_conflict",
        "retry_merge",
        "rebase conflict requires merge retry",
    ),
    _route(
        "merge_conflict",
        "patch_apply_conflict",
        "retry_merge",
        "patch apply conflict requires merge retry",
    ),
    _route(
        "runtime_provider",
        "provider_internal_error",
        "retry_dispatch",
        "runtime provider internal error",
    ),
    _route(
        "runtime_provider",
        "provider_rate_limited",
        "retry_dispatch",
        "runtime provider rate limited",
    ),
    _route(
        "runtime_provider",
        "provider_transport_error",
        "retry_dispatch",
        "runtime provider transport error",
    ),
    _route(
        "runtime_provider",
        "process_failed",
        "retry_dispatch",
        "runtime process failed before durable completion",
    ),
    _route(
        "runtime_timeout",
        "watchdog_timeout",
        "retry_dispatch",
        "runtime watchdog timed out",
    ),
    _route(
        "runtime_cancelled",
        "runtime_cancelled",
        "quiesce",
        "runtime cancellation should quiesce the route",
    ),
    _route(
        "runtime_context",
        "prompt_too_large",
        "retry_dispatch",
        "runtime prompt exceeded the context budget",
    ),
    _route(
        "runtime_context",
        "context_materialization_failed",
        "quiesce",
        "runtime context materialization failed",
    ),
    _route(
        "runtime_context",
        "context_permission_denied",
        "operator_required",
        "runtime context requires operator clearance",
    ),
    _route(
        "runtime_structured_output",
        "malformed_structured_output",
        "retry_dispatch",
        "runtime produced malformed structured output",
    ),
    _route(
        "dispatcher_internal",
        "idempotency_conflict",
        "quiesce",
        "dispatcher idempotency conflict requires investigation",
    ),
    _route(
        "verifier_provider",
        "verifier_provider_timeout",
        "retry_verifier",
        "verifier provider timed out",
    ),
    _route(
        "verifier_provider",
        "verifier_provider_crash",
        "retry_verifier",
        "verifier provider crashed",
    ),
    _route(
        "verifier_provider",
        "verifier_parse_failed",
        "retry_verifier",
        "verifier output parse failed",
    ),
    _route(
        "verifier_context",
        "context_materialization_failed",
        "retry_verifier",
        "verifier context materialization failed",
    ),
    _route(
        "verifier_context",
        "verifier_context_stale",
        "retry_verifier",
        "verifier context is stale",
    ),
    _route(
        "checkpoint_contradiction",
        "checkpoint_after_failed_gate",
        "quiesce",
        "checkpoint contradicts a failed gate",
    ),
    _route(
        "regroup_invalid",
        "regroup_dependency_cycle",
        "quiesce",
        "regroup dependency cycle is deterministic",
    ),
    _route(
        "regroup_invalid",
        "regroup_write_conflict",
        "quiesce",
        "regroup write conflict is deterministic",
    ),
    _route(
        "evidence_corruption",
        "artifact_hash_mismatch",
        "quiesce",
        "artifact hash mismatch must stop replay",
    ),
    _route(
        "evidence_corruption",
        "payload_digest_mismatch",
        "quiesce",
        "payload digest mismatch must stop replay",
    ),
    _route(
        "evidence_corruption",
        "projection_body_conflict",
        "quiesce",
        "projection body conflict must stop replay",
    ),
    _route(
        "resource_exhausted",
        "db_resource_exhausted",
        "quiesce",
        "database resource exhaustion must quiesce",
    ),
    _route(
        "resource_exhausted",
        "disk_resource_exhausted",
        "quiesce",
        "disk resource exhaustion must quiesce",
    ),
    _route(
        "resource_exhausted",
        "process_resource_exhausted",
        "retry_dispatch",
        "process resource exhaustion can retry dispatch",
    ),
    _route(
        "resource_exhausted",
        "provider_quota_exhausted",
        "retry_dispatch",
        "provider quota exhaustion can retry dispatch",
    ),
    _route(
        "resource_exhausted",
        "unclassified",
        "quiesce",
        "unclassified resource exhaustion must quiesce",
    ),
    _route(
        "operator_required",
        "operator_clearance_required",
        "operator_required",
        "operator clearance is required",
    ),
    _route(
        "unknown",
        "unclassified",
        "quiesce",
        "unknown failure class must quiesce",
    ),
)

FAILURE_TYPE_POLICIES: dict[tuple[str, str], FailureTypePolicy] = {
    (policy.failure_class, policy.failure_type): policy for policy, _ in _ROUTE_ROWS
}

ROUTE_TABLE: dict[tuple[str, str], FailureRoutePolicy] = {
    (route.failure_class, route.failure_type): route for _, route in _ROUTE_ROWS
}


def _validate_route_table() -> None:
    for key, route in ROUTE_TABLE.items():
        if route.action != "run_product_repair":
            continue
        failure_class, failure_type = key
        allowed = failure_class == "product_defect" or (
            failure_class == "contract_violation"
            and failure_type in _SCOPED_CONTRACT_PRODUCT_TYPES
        )
        if not allowed or not route.allow_product_repair:
            raise RuntimeError(f"unsafe product repair route: {key!r}")


_validate_route_table()


def build_failure_signature(observation: FailureObservation) -> dict[str, Any]:
    payload = _canonicalize_for_signature("payload", observation.payload)
    return {
        "dag_sha256": observation.dag_sha256,
        "deterministic": observation.deterministic,
        "evidence_ids": sorted(observation.evidence_ids),
        "failure_class": observation.failure_class,
        "failure_type": observation.failure_type,
        "feature_id": observation.feature_id,
        "group_idx": observation.group_idx,
        "payload": payload,
        "severity": observation.severity,
        "source": observation.source,
        "task_id": observation.task_id,
    }


def stable_signature_hash(observation: FailureObservation) -> str:
    return stable_digest(build_failure_signature(observation))


def failure_idempotency_key(
    observation: FailureObservation,
    signature_hash: str,
) -> str:
    payload_key = observation.payload.get("idempotency_key")
    if isinstance(payload_key, str) and payload_key.startswith("failure:"):
        return payload_key
    attempt = observation.attempt_id if observation.attempt_id is not None else "-"
    return (
        f"failure:{observation.feature_id}:{attempt}:"
        f"{observation.failure_class}:{signature_hash}"
    )


def route_budget_key(record: FailureRecord) -> str:
    observation = record.observation
    return (
        f"budget:{observation.feature_id}:{observation.failure_class}:"
        f"{observation.failure_type}:{record.signature_hash}"
    )


def route_idempotency_key(
    record: FailureRecord,
    action: RouteAction,
    reservation_ordinal: int,
) -> str:
    observation = record.observation
    return _route_idempotency_key_from_parts(
        feature_id=observation.feature_id,
        failure_id=record.failure_id or 0,
        signature_hash=record.signature_hash,
        action=action,
        reservation_ordinal=reservation_ordinal,
    )


def _route_idempotency_key_from_parts(
    *,
    feature_id: str,
    failure_id: int,
    signature_hash: str,
    action: RouteAction,
    reservation_ordinal: int,
) -> str:
    return (
        f"route:{feature_id}:{failure_id}:"
        f"{signature_hash}:{action}:n{reservation_ordinal}"
    )


def _route_input_digest(decision: RouteDecision) -> str:
    return stable_digest(
        {
            "action": decision.action,
            "budget_key": decision.budget_key,
            "failure_id": decision.failure_id,
            "idempotency_key": decision.idempotency_key,
            "repair_scope": decision.repair_scope,
            "required_evidence_ids": sorted(decision.required_evidence_ids),
            "signature_hash": decision.signature_hash,
        }
    )


def _route_replay_compatible(stored: RouteDecision, incoming: RouteDecision) -> bool:
    return (
        stored.failure_id == incoming.failure_id
        and stored.signature_hash == incoming.signature_hash
        and stored.budget_key == incoming.budget_key
    )


class FailureRouter:
    def __init__(
        self,
        *,
        port: FailureRouterPort | None = None,
        route_table: Mapping[tuple[str, str], FailureRoutePolicy] | None = None,
        type_policies: Mapping[tuple[str, str], FailureTypePolicy] | None = None,
    ) -> None:
        self.port: FailureRouterPort = port or InMemoryFailureRouterPort()
        self.route_table = dict(route_table or ROUTE_TABLE)
        self.type_policies = dict(type_policies or FAILURE_TYPE_POLICIES)

    def record(self, observation: FailureObservation) -> int:
        route = self._route_for(observation.failure_class, observation.failure_type)
        policy = self._policy_for(observation.failure_class, observation.failure_type)
        normalized = self._normalize_observation(observation, policy)
        signature_hash = stable_signature_hash(normalized)
        input_digest = stable_digest(build_failure_signature(normalized))
        record = FailureRecord(
            observation=normalized,
            policy=policy,
            signature_hash=signature_hash,
            idempotency_key=failure_idempotency_key(normalized, signature_hash),
            input_digest=input_digest,
        )
        if route.action == "run_product_repair" and not route.allow_product_repair:
            raise UnknownFailurePolicyError(
                f"unsafe product repair route for {route.failure_class}/{route.failure_type}"
            )
        return self.port.record_failure(record).failure_id or 0

    def decide(self, failure_id: int) -> RouteDecision:
        record = self.get_failure(failure_id)
        route = self._route_for(
            record.observation.failure_class,
            record.observation.failure_type,
        )
        budget_key = route_budget_key(record)
        state = self.port.get_budget(budget_key)
        reserved = state.reserved_attempts if state is not None else 0
        budget_remaining = max(route.budget - reserved, 0)
        repair_scope = self._repair_scope(record, route)
        action = route.action
        budget_exhausted = False
        reason = route.reason

        if action == "run_product_repair" and not self._allows_product_repair(
            record,
            route,
            repair_scope,
        ):
            action = "quiesce"
            budget_remaining = 0
            reason = (
                "product repair requires product defect class or scoped contract "
                "violation evidence"
            )
        elif action not in ("quiesce", "operator_required") and budget_remaining <= 0:
            action = "quiesce"
            budget_exhausted = True
            reason = f"retry budget exhausted for {route.failure_class}/{route.failure_type}"

        ordinal = reserved + 1 if not budget_exhausted else reserved
        return RouteDecision(
            failure_id=failure_id,
            route_decision_id=None,
            action=action,
            budget_remaining=budget_remaining,
            budget_exhausted=budget_exhausted,
            reason=reason,
            required_evidence_ids=record.observation.evidence_ids,
            signature_hash=record.signature_hash,
            idempotency_key=route_idempotency_key(record, action, ordinal),
            repair_scope=repair_scope,
            budget_key=budget_key,
            reservation_ordinal=ordinal,
        )

    def mark_route_started(self, decision: RouteDecision) -> RouteDecision:
        existing = self.port.get_route_by_key(decision.idempotency_key)
        input_digest = _route_input_digest(decision)
        if existing is not None:
            if existing.input_digest != input_digest and not _route_replay_compatible(
                existing.decision,
                decision,
            ):
                raise IdempotencyConflict(
                    decision.idempotency_key,
                    existing.input_digest,
                    input_digest,
                )
            return existing.decision

        record = self.get_failure(decision.failure_id)
        route = self._route_for(
            record.observation.failure_class,
            record.observation.failure_type,
        )
        if decision.action in ("quiesce", "operator_required") or route.budget <= 0:
            stored = self.port.record_route_started(decision, input_digest)
            return stored.decision

        stored = self.port.record_route_started(
            decision,
            input_digest,
            budget_reservation={
                "budget_key": decision.budget_key,
                "feature_id": record.observation.feature_id,
                "failure_class": record.observation.failure_class,
                "failure_type": record.observation.failure_type,
                "signature_hash": record.signature_hash,
                "max_attempts": route.budget,
                "failure_id": decision.failure_id,
            },
        )
        return stored.decision

    def mark_route_finished(
        self,
        decision: RouteDecision,
        *,
        succeeded: bool,
        produced_failure_id: int | None = None,
    ) -> None:
        self.port.mark_route_finished(
            decision,
            succeeded=succeeded,
            produced_failure_id=produced_failure_id,
        )

    def get_failure(self, failure_id: int) -> FailureRecord:
        record = self.port.get_failure(failure_id)
        if record is None:
            raise KeyError(f"unknown failure id: {failure_id}")
        return record

    def _normalize_observation(
        self,
        observation: FailureObservation,
        policy: FailureTypePolicy,
    ) -> FailureObservation:
        payload = deepcopy(observation.payload)
        return observation.model_copy(
            update={
                "deterministic": policy.deterministic,
                "retryable": policy.retryable,
                "operator_required": policy.operator_required,
                "severity": policy.severity,
                "payload": payload,
            }
        )

    def _policy_for(
        self,
        failure_class: str,
        failure_type: str,
    ) -> FailureTypePolicy:
        policy = self.type_policies.get((failure_class, failure_type))
        if policy is None:
            raise UnknownFailurePolicyError(
                f"no failure type policy for {failure_class}/{failure_type}"
            )
        return policy

    def _route_for(
        self,
        failure_class: str,
        failure_type: str,
    ) -> FailureRoutePolicy:
        route = self.route_table.get((failure_class, failure_type))
        if route is None:
            raise UnknownFailurePolicyError(
                f"no route policy for {failure_class}/{failure_type}"
            )
        return route

    def _repair_scope(
        self,
        record: FailureRecord,
        route: FailureRoutePolicy,
    ) -> dict[str, Any]:
        payload = record.observation.payload
        scope = {
            "feature_id": record.observation.feature_id,
            "dag_sha256": record.observation.dag_sha256,
            "group_idx": record.observation.group_idx,
            "task_id": record.observation.task_id,
            "attempt_id": record.observation.attempt_id,
            "source": record.observation.source,
            "failure_class": record.observation.failure_class,
            "failure_type": record.observation.failure_type,
            "repair_kind": route.repair_kind,
            "repo_ids": _str_list(_payload_value(payload, "repo_ids", "repo_id")),
            "target_paths": _str_list(
                _payload_value(
                    payload,
                    "target_paths",
                    "offending_paths",
                    "paths",
                    "path",
                ),
                path=True,
            ),
            "target_contract_ids": _int_list(
                _payload_value(payload, "target_contract_ids", "contract_ids", "contract_id")
            ),
            "contract_ids": _int_list(
                _payload_value(payload, "contract_ids", "target_contract_ids", "contract_id")
            ),
            "required_gate_ids": _str_list(_payload_value(payload, "gate_ids", "gate_id")),
            "gate_ids": _str_list(_payload_value(payload, "gate_ids", "required_gate_ids", "gate_id")),
            "sandbox_id": _payload_value(payload, "sandbox_id"),
            "queue_id": _payload_value(payload, "queue_id", "merge_queue_id"),
            "evidence_ids": record.observation.evidence_ids,
            "hook_evidence_ids": _int_list(_payload_value(payload, "hook_evidence_ids")),
            "status_evidence_ids": _int_list(_payload_value(payload, "status_evidence_ids")),
            "no_dirty_proof_evidence_ids": _int_list(
                _payload_value(payload, "no_dirty_proof_evidence_ids")
            ),
            "failed_merge_queue_item_id": _payload_value(
                payload,
                "failed_merge_queue_item_id",
                "merge_queue_item_id",
                "queue_item_id",
            ),
            "failed_source_queue_item_evidence_id": _payload_value(
                payload,
                "failed_source_queue_item_evidence_id",
                "source_queue_item_evidence_id",
            ),
            "source_queue_item_status": _payload_value(
                payload,
                "source_queue_item_status",
                "queue_item_status",
            ),
            "sandbox_lease_id": _payload_value(
                payload,
                "sandbox_lease_id",
                "retained_sandbox_lease_id",
            ),
            "source_verdict_key": _payload_value(payload, "source_verdict_key"),
            "legacy_route": _payload_value(payload, "legacy_route"),
        }
        return {key: value for key, value in scope.items() if value not in (None, [], {})}

    def _allows_product_repair(
        self,
        record: FailureRecord,
        route: FailureRoutePolicy,
        repair_scope: Mapping[str, Any],
    ) -> bool:
        if route.action != "run_product_repair":
            return True
        if record.observation.failure_class == "product_defect":
            return bool(
                route.allow_product_repair
                and (
                    record.observation.evidence_ids
                    or self._authorized_direct_source_verdict(
                        record,
                        repair_scope,
                        allowed_legacy_routes=_DIRECT_PRODUCT_REPAIR_ROUTES,
                        allowed_sources={"verification_graph"},
                    )
                )
                and repair_scope.get("target_paths")
            )
        if (
            record.observation.failure_class != "contract_violation"
            or record.observation.failure_type not in _SCOPED_CONTRACT_PRODUCT_TYPES
        ):
            return False
        return bool(
            repair_scope.get("target_paths")
            and (
                repair_scope.get("target_contract_ids")
                or self._authorized_direct_source_verdict(
                    record,
                    repair_scope,
                    allowed_legacy_routes=_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES,
                    allowed_sources={"contract"},
                )
            )
        )

    def _authorized_direct_source_verdict(
        self,
        record: FailureRecord,
        repair_scope: Mapping[str, Any],
        *,
        allowed_legacy_routes: frozenset[str],
        allowed_sources: set[str],
    ) -> bool:
        key = repair_scope.get("source_verdict_key")
        legacy_route = repair_scope.get("legacy_route")
        if (
            not isinstance(key, str)
            or not isinstance(legacy_route, str)
            or legacy_route not in allowed_legacy_routes
            or record.observation.source not in allowed_sources
        ):
            return False
        match = _DIRECT_ROUTE_SOURCE_RE.match(key)
        if match is None:
            return False
        group_idx = record.observation.group_idx
        if group_idx is None or int(match.group("group_idx")) != int(group_idx):
            return False
        return True


# --- Slice 11i -- pure decision-payload adapter helpers --------------------
# Moved byte-for-byte from `workflows/develop/phases/implementation.py` in
# Slice 11i. Per `docs/execution-control-plane/11-refactor-map.md` § "Boundary-
# level API contracts" row for `execution/failure_router.py`
# ("FailureRouter.decide(failure_id) -> RouteDecision. Typed failure taxonomy,
# retry budgets, deterministic route selection, quiesce/escalation decisions."),
# the typed→legacy `RouteDecision`-to-dict adapter functions belong on the
# failure-router surface: they read ONLY a `RouteDecision`-shaped object via
# `getattr` (duck-typed) and return a flat dict payload that legacy callers
# (`implementation.py` direct callers + the persisted `route_decision` payload
# on every `runtime_failure_context` evidence row -- the REAL typed budget
# source the Slice 10c-2 `_typed_retry_budgets` reads) consume.
#
# The phase-level failure-router PORT surface (`_failure_router_for_runner`,
# `_typed_direct_route_payload`, `_route_merge_queue_drain_failure`, and the
# `runtime_provider` retry-route adapter at `implementation.py:13190`) STAYS
# in `implementation.py` per the prompt hard rule against splitting non-pure
# helpers -- those are runner+feature/services-coupled (each takes a
# `WorkflowRunner` + `Feature` + reads `runner.services` /
# `runner._failure_router_port` and builds a typed `FailureObservation`
# observation around it).
def _route_decision_retry_budget_payload(
    decision: Any,
    *,
    action: str,
    max_attempts: int | None = None,
) -> dict[str, Any]:
    remaining = max(0, int(getattr(decision, "budget_remaining", 0) or 0))
    ordinal = max(0, int(getattr(decision, "reservation_ordinal", 0) or 0))
    if max_attempts is None:
        max_attempts = remaining + ordinal if ordinal else remaining
    return {
        "route": action,
        "budget_key": str(getattr(decision, "budget_key", "") or ""),
        "max_attempts": max_attempts,
        "max_retries": max_attempts,
        "remaining_attempts": remaining,
        "idempotency_key": str(getattr(decision, "idempotency_key", "") or ""),
        "reservation_ordinal": ordinal,
        "budget_exhausted": bool(getattr(decision, "budget_exhausted", False)),
    }


def _route_decision_compat_payload(
    decision: Any,
    *,
    failure_class: str,
    failure_type: str,
    max_attempts: int | None = None,
    legacy_route: str = "",
    legacy_failure_type: str = "",
) -> dict[str, Any]:
    action = str(getattr(decision, "action", "") or "")
    budget = _route_decision_retry_budget_payload(
        decision,
        action=action,
        max_attempts=max_attempts,
    )
    payload = {
        "failure_id": getattr(decision, "failure_id", None),
        "typed_failure_id": getattr(decision, "failure_id", None),
        "route_decision_id": getattr(decision, "route_decision_id", None),
        "route": action,
        "action": action,
        "failure_class": failure_class,
        "failure_type": failure_type,
        "operator_required": action == "operator_required",
        "retryable": action.startswith("retry_"),
        "budget_remaining": budget["remaining_attempts"],
        "budget_exhausted": bool(getattr(decision, "budget_exhausted", False)),
        "reason": str(getattr(decision, "reason", "") or ""),
        "required_evidence_ids": list(getattr(decision, "required_evidence_ids", []) or []),
        "stable_signature_hash": str(getattr(decision, "signature_hash", "") or ""),
        "signature_hash": str(getattr(decision, "signature_hash", "") or ""),
        "idempotency_key": str(getattr(decision, "idempotency_key", "") or ""),
        "budget_key": budget["budget_key"],
        "reservation_ordinal": budget["reservation_ordinal"],
        "retry_budget": budget,
        "repair_scope": dict(getattr(decision, "repair_scope", {}) or {}),
    }
    if legacy_route:
        payload["legacy_route"] = legacy_route
    if legacy_failure_type:
        payload["legacy_failure_type"] = legacy_failure_type
    return payload


__all__ = [
    "CLASS_RETRY_BUDGETS",
    "FAILURE_CLASSES",
    "FAILURE_SEVERITIES",
    "FAILURE_SOURCES",
    "FAILURE_TYPES",
    "FAILURE_TYPE_POLICIES",
    "ROUTE_ACTIONS",
    "ROUTE_TABLE",
    "FailureClass",
    "FailureObservation",
    "FailureRecord",
    "FailureRoutePolicy",
    "FailureRouter",
    "FailureRouterError",
    "FailureRouterPort",
    "FailureSeverity",
    "FailureSource",
    "FailureType",
    "FailureTypePolicy",
    "IdempotencyConflict",
    "InMemoryFailureRouterPort",
    "RetryBudgetState",
    "RouteAction",
    "RouteDecision",
    "RouteRecord",
    "UnknownFailurePolicyError",
    "_route_decision_compat_payload",
    "_route_decision_retry_budget_payload",
    "build_failure_signature",
    "failure_idempotency_key",
    "route_budget_key",
    "route_idempotency_key",
    "stable_digest",
    "stable_signature_hash",
]
