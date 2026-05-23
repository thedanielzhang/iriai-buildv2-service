"""Deterministic route-to-repair request builders.

This module is intentionally side-effect free.  The failure router owns route
selection and budget reservation; the executor turns a started route decision
into the concrete repair or retry request that later workers can persist and
execute.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any, Literal, Mapping, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .failure_router import ROUTE_TABLE, RouteAction, RouteDecision

try:
    # Slice 11h — the impl.py-local Verdict / Issue Pydantic types
    # for the direct-route classification cluster are imported here so
    # the helpers moved from `implementation.py` preserve byte-for-byte
    # semantics (the classifier ignores anything that is not a
    # `Verdict` instance).
    from iriai_build_v2.models.outputs import Issue, Verdict
except ImportError:  # pragma: no cover - models module may be absent in old installs.
    Issue = None  # type: ignore[assignment]
    Verdict = None  # type: ignore[assignment]

try:
    # Slice 11h — the Slice-11a typed `DagDirectRepairRoute` +
    # `DagContradictionResolution` request shapes used by the direct-
    # route classifier and contradiction-helpers respectively. Imported
    # under their canonical names so the moved helper bodies stay
    # byte-for-byte identical to their pre-11h impl.py source.
    from iriai_build_v2.workflows.develop.execution.types import (
        DagContradictionResolution,
        DagDirectRepairRoute,
    )
except ImportError:  # pragma: no cover - Slice 11a module may be absent in old installs.
    DagContradictionResolution = None  # type: ignore[assignment]
    DagDirectRepairRoute = None  # type: ignore[assignment]


RepairKind = Literal[
    "product",
    "contract",
    "canonicalization",
    "workspace",
    "commit_hygiene",
    "sandbox_cleanup",
]

RepairMutation = Literal[
    "sandbox_product_patch",
    "contract_recompile",
    "workspace_metadata",
    "workspace_acl",
    "projection_refresh",
    "commit_hygiene_patch",
    "sandbox_cleanup",
]

RepairAttemptStatus = Literal[
    "requested",
    "sandbox_allocating",
    "dispatching",
    "capturing",
    "validating",
    "queued_for_merge",
    "metadata_applied",
    "succeeded",
    "failed",
    "quiesced",
]

RetryKind = Literal[
    "dispatch",
    "verifier",
    "merge",
    "sandbox_capture",
    "sandbox_cleanup",
]

RetryRequestStatus = Literal["requested", "started", "succeeded", "failed", "quiesced"]

SandboxMode = Literal["none", "task", "repair", "canonicalization"]
EnqueueStrategy = Literal["none", "merge_queue", "metadata_only", "cleanup_only"]
AttemptKind = Literal["task", "verify", "merge", "repair"]


class RouteExecutorError(ValueError):
    """Raised when a route decision cannot build a safe request."""


class _RepairModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class RepairTarget(_RepairModel):
    repo_id: str | None = None
    path: str | None = None
    contract_id: int | None = None
    evidence_id: int | None = None
    failure_id: int | None = None
    reason: str


class RepairRequest(_RepairModel):
    id: int | None = None
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_id: str | None = None
    route_decision_id: int
    failure_id: int
    action: RouteAction
    repair_kind: RepairKind
    allowed_mutations: list[RepairMutation]
    target_repo_ids: list[str]
    target_paths: list[str]
    target_contract_ids: list[int]
    required_evidence_ids: list[int]
    source_verdict_key: str | None = None
    targets: list[RepairTarget]
    sandbox_mode: SandboxMode
    enqueue_strategy: EnqueueStrategy
    required_gate_ids: list[str]
    prompt_constraints: list[str]
    budget_key: str
    idempotency_key: str
    input_digest: str

    @field_validator("feature_id", "dag_sha256", "budget_key", "idempotency_key", "input_digest")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be empty")
        return value

    @model_validator(mode="after")
    def _mutation_invariants(self) -> "RepairRequest":
        if self.action.startswith("retry_"):
            raise ValueError("retry actions must build RetryRequest")
        if (
            any(mutation in {"sandbox_product_patch", "commit_hygiene_patch"} for mutation in self.allowed_mutations)
            and self.enqueue_strategy != "merge_queue"
        ):
            raise ValueError("file-touching repairs must use merge_queue")
        if self.repair_kind in {"workspace", "contract"}:
            forbidden = {"sandbox_product_patch", "commit_hygiene_patch", "sandbox_cleanup"}
            if any(mutation in forbidden for mutation in self.allowed_mutations):
                raise ValueError("workspace and contract repairs cannot mutate product files")
            if self.sandbox_mode != "none":
                raise ValueError("workspace and contract repairs cannot run product sandboxes")
        return self


class RepairOutcome(_RepairModel):
    id: int | None = None
    repair_request_id: int
    status: RepairAttemptStatus
    attempt_id: int | None = None
    sandbox_lease_id: int | None = None
    dispatcher_attempt_id: int | None = None
    patch_summary_ids: list[int] = Field(default_factory=list)
    contract_verdict_ids: list[int] = Field(default_factory=list)
    workspace_snapshot_ids: list[int] = Field(default_factory=list)
    merge_queue_item_ids: list[int] = Field(default_factory=list)
    projected_artifact_ids: list[int] = Field(default_factory=list)
    resolved_failure_id: int | None = None
    produced_failure_id: int | None = None
    summary: str
    idempotency_key: str


class RetryRequest(_RepairModel):
    id: int | None = None
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_id: str | None = None
    route_decision_id: int
    failure_id: int
    action: RouteAction
    retry_kind: RetryKind
    attempt_kind: AttemptKind
    preserve_contract_ids: list[int]
    preserve_gate_ids: list[str]
    preserve_sandbox_lease_id: int | None = None
    preserve_merge_queue_item_id: int | None = None
    required_evidence_ids: list[int]
    reset_context: bool = False
    allocate_new_sandbox: bool = False
    idempotency_key: str
    input_digest: str

    @field_validator("feature_id", "dag_sha256", "idempotency_key", "input_digest")
    @classmethod
    def _required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("value cannot be empty")
        return value

    @model_validator(mode="after")
    def _retry_invariants(self) -> "RetryRequest":
        if self.action.startswith("run_") and self.action != "run_sandbox_cleanup":
            raise ValueError("repair actions must build RepairRequest")
        if self.action == "retry_merge" and self.preserve_merge_queue_item_id is None:
            raise ValueError("retry_merge requires a preserved merge queue item")
        if self.retry_kind == "sandbox_capture" and self.preserve_sandbox_lease_id is None:
            raise ValueError("retry_sandbox_capture requires a retained sandbox lease")
        return self


class RetryOutcome(_RepairModel):
    id: int | None = None
    retry_request_id: int
    status: RetryRequestStatus
    spawned_attempt_id: int | None = None
    spawned_evidence_ids: list[int] = Field(default_factory=list)
    spawned_merge_queue_item_ids: list[int] = Field(default_factory=list)
    resolved_failure_id: int | None = None
    produced_failure_id: int | None = None
    summary: str
    idempotency_key: str


RouteRequest: TypeAlias = RepairRequest | RetryRequest

_DIRECT_ROUTE_SOURCE_RE = re.compile(
    r"^dag-verify:g(?P<group_idx>\d+):(?P<suffix>initial|retry-\d+|checkpoint-commit)$"
)
_DIRECT_PRODUCT_REPAIR_ROUTES = frozenset({"normal_verify_repair"})
_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES = frozenset({"manifest_forbidden_product_cleanup"})
_SCOPED_CONTRACT_PRODUCT_TYPES = frozenset({
    "outside_allowed_paths",
    "forbidden_path_touched",
    "read_only_path_touched",
})


class RouteExecutor:
    """Build typed route requests from started router decisions."""

    def build_route_request(self, decision: RouteDecision) -> RouteRequest:
        action = _decision_action(decision)
        if action in _REPAIR_ACTIONS:
            return self.build_repair_request(decision)
        if action in _RETRY_ACTIONS:
            return self.build_retry_request(decision)
        raise RouteExecutorError(f"route action {action!r} does not build a request")

    def build_repair_request(self, decision: RouteDecision) -> RepairRequest:
        route_decision_id = _started_route_decision_id(decision)
        action = _decision_action(decision)
        if action not in _REPAIR_ACTIONS:
            raise RouteExecutorError(f"route action {action!r} does not build RepairRequest")
        scope = _scope(decision)
        _assert_route_table_consistency(action, scope)
        common = _common_request_fields(decision, route_decision_id, scope)

        if action == "run_product_repair":
            config = self._product_repair(scope)
        elif action == "run_contract_repair":
            config = self._contract_repair(scope)
        elif action == "run_canonicalization_repair":
            config = self._canonicalization_repair(scope)
        elif action == "run_workspace_repair":
            config = self._workspace_repair(scope)
        elif action == "run_commit_hygiene_repair":
            config = self._commit_hygiene_repair(scope)
        else:  # pragma: no cover - guarded by _REPAIR_ACTIONS.
            raise RouteExecutorError(f"unsupported repair action {action!r}")

        target_paths = _strings(scope, "target_paths", "paths", "offending_paths", "staged_paths")
        _validate_target_paths(target_paths)
        target_contract_ids = _ints(scope, "target_contract_ids", "contract_ids")
        target_repo_ids = _strings(scope, "target_repo_ids", "repo_ids")
        required_gate_ids = _strings(scope, "required_gate_ids", "gate_ids")
        required_evidence_ids = _required_evidence(decision, scope)
        source_verdict_key = _optional_str(scope, "source_verdict_key")
        if action == "run_product_repair" and not (
            required_evidence_ids
            or _authorized_product_repair_source_verdict(scope)
        ):
            raise RouteExecutorError("product repair requires typed verifier/gate evidence or source verdict key")
        prompt_constraints = [
            *_strings(scope, "prompt_constraints", "non_goals"),
            *config["prompt_constraints"],
        ]

        return RepairRequest(
            **common,
            action=action,
            repair_kind=config["repair_kind"],
            allowed_mutations=config["allowed_mutations"],
            target_repo_ids=target_repo_ids,
            target_paths=target_paths,
            target_contract_ids=target_contract_ids,
            required_evidence_ids=required_evidence_ids,
            source_verdict_key=source_verdict_key,
            targets=_repair_targets(
                failure_id=int(_field(decision, "failure_id")),
                target_repo_ids=target_repo_ids,
                target_paths=target_paths,
                target_contract_ids=target_contract_ids,
                required_evidence_ids=required_evidence_ids,
                reason=str(_field(decision, "reason") or action),
            ),
            sandbox_mode=config["sandbox_mode"],
            enqueue_strategy=config["enqueue_strategy"],
            required_gate_ids=required_gate_ids,
            prompt_constraints=_unique_text(prompt_constraints),
            budget_key=_budget_key(decision, scope),
            idempotency_key=_request_idempotency_key(decision, scope, request_kind="repair"),
            input_digest=_request_input_digest(decision, scope, request_kind="repair"),
        )

    def build_retry_request(self, decision: RouteDecision) -> RetryRequest:
        route_decision_id = _started_route_decision_id(decision)
        action = _decision_action(decision)
        if action not in _RETRY_ACTIONS:
            raise RouteExecutorError(f"route action {action!r} does not build RetryRequest")
        scope = _scope(decision)
        _assert_route_table_consistency(action, scope)
        common = _common_request_fields(decision, route_decision_id, scope)

        retry_kind: RetryKind
        attempt_kind: AttemptKind
        reset_context = bool(scope.get("reset_context") or False)
        allocate_new_sandbox = bool(scope.get("allocate_new_sandbox") or False)
        preserve_merge_queue_item_id = _optional_int(
            scope,
            "preserve_merge_queue_item_id",
            "failed_merge_queue_item_id",
            "merge_queue_item_id",
            "queue_item_id",
        )
        preserve_sandbox_lease_id = _optional_int(
            scope,
            "preserve_sandbox_lease_id",
            "retained_sandbox_lease_id",
            "sandbox_lease_id",
        )

        if action == "retry_dispatch":
            retry_kind = "dispatch"
            attempt_kind = "task"
            allocate_new_sandbox = allocate_new_sandbox or scope.get("failure_class") == "sandbox_allocation"
        elif action == "retry_verifier":
            retry_kind = "verifier"
            attempt_kind = "verify"
            reset_context = reset_context or scope.get("failure_type") == "verifier_context_stale"
        elif action == "retry_merge":
            self._validate_retry_merge_scope(scope)
            retry_kind = "merge"
            attempt_kind = "merge"
            preserve_merge_queue_item_id = _required_int(
                scope,
                "preserve_merge_queue_item_id",
                "failed_merge_queue_item_id",
                "merge_queue_item_id",
                "queue_item_id",
            )
        elif action == "retry_sandbox_capture":
            retry_kind = "sandbox_capture"
            attempt_kind = "repair"
            if preserve_sandbox_lease_id is None:
                raise RouteExecutorError("retry_sandbox_capture requires retained sandbox lease id")
        elif action == "run_sandbox_cleanup":
            retry_kind = "sandbox_cleanup"
            attempt_kind = "repair"
        else:  # pragma: no cover - guarded by _RETRY_ACTIONS.
            raise RouteExecutorError(f"unsupported retry action {action!r}")

        return RetryRequest(
            **common,
            action=action,
            retry_kind=retry_kind,
            attempt_kind=attempt_kind,
            preserve_contract_ids=_ints(scope, "preserve_contract_ids", "contract_ids"),
            preserve_gate_ids=_strings(scope, "preserve_gate_ids", "gate_ids", "required_gate_ids"),
            preserve_sandbox_lease_id=preserve_sandbox_lease_id,
            preserve_merge_queue_item_id=preserve_merge_queue_item_id,
            required_evidence_ids=_required_evidence(decision, scope),
            reset_context=reset_context,
            allocate_new_sandbox=allocate_new_sandbox,
            idempotency_key=_request_idempotency_key(decision, scope, request_kind="retry"),
            input_digest=_request_input_digest(decision, scope, request_kind="retry"),
        )

    async def execute_repair(self, request: RepairRequest) -> RepairOutcome:
        raise NotImplementedError("repair execution side effects are owned by later slices")

    async def execute_retry(self, request: RetryRequest) -> RetryOutcome:
        raise NotImplementedError("retry execution side effects are owned by later slices")

    def _product_repair(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        failure_class = str(scope.get("failure_class") or "")
        contract_ids = _ints(scope, "target_contract_ids", "contract_ids")
        target_paths = _strings(scope, "target_paths", "paths", "offending_paths")
        has_contract_scope = bool(
            contract_ids
            or _authorized_product_repair_source_verdict(scope)
        )
        if failure_class not in {"product_defect", "contract_violation"}:
            raise RouteExecutorError("product repair is only allowed for product defects or scoped contract violations")
        if failure_class == "product_defect" and not target_paths:
            raise RouteExecutorError("product repair requires scoped product target paths")
        if (
            failure_class == "contract_violation"
            and str(scope.get("failure_type") or "") not in _SCOPED_CONTRACT_PRODUCT_TYPES
        ):
            raise RouteExecutorError("contract violation product repair requires a scoped contract violation type")
        if failure_class == "contract_violation" and (not has_contract_scope or not target_paths):
            raise RouteExecutorError("contract violation product repair requires fixed contracts and offending paths")
        return {
            "repair_kind": "product",
            "allowed_mutations": ["sandbox_product_patch"],
            "sandbox_mode": "repair",
            "enqueue_strategy": "merge_queue",
            "prompt_constraints": [
                "do not broaden contracts",
                "do not edit workflow metadata",
                "touch only target product paths",
            ],
        }

    def _contract_repair(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        if _truthy(scope, "contract_widening", "widen_contracts", "edit_root_dag", "mutate_product_files"):
            raise RouteExecutorError("contract repair cannot widen contracts, edit root DAG, or mutate product files")
        return {
            "repair_kind": "contract",
            "allowed_mutations": ["contract_recompile"],
            "sandbox_mode": "none",
            "enqueue_strategy": "metadata_only",
            "prompt_constraints": [
                "derive contracts only from immutable DAG and workspace metadata",
                "do not widen contracts",
                "do not edit root DAG",
                "do not mutate product files",
            ],
        }

    def _canonicalization_repair(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        touches_files = bool(
            _strings(scope, "target_paths", "paths", "offending_paths")
            or scope.get("canonicalization_mode") in {"product_content", "alias_content"}
            or scope.get("mutate_product_files")
        )
        if touches_files:
            return {
                "repair_kind": "canonicalization",
                "allowed_mutations": ["sandbox_product_patch"],
                "sandbox_mode": "canonicalization",
                "enqueue_strategy": "merge_queue",
                "prompt_constraints": ["canonicalize only named paths", "preserve existing contracts"],
            }
        return {
            "repair_kind": "canonicalization",
            "allowed_mutations": ["projection_refresh"],
            "sandbox_mode": "none",
            "enqueue_strategy": "metadata_only",
            "prompt_constraints": ["refresh alias/projection metadata only", "do not mutate product files"],
        }

    def _workspace_repair(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        if _truthy(scope, "mutate_product_files") or _strings(scope, "product_paths"):
            raise RouteExecutorError("workspace repair cannot mutate product files")
        if str(scope.get("workspace_repair_mode") or "") == "acl" or scope.get("failure_class") == "acl_workability":
            mutations: list[RepairMutation] = ["workspace_acl"]
        else:
            mutations = ["workspace_metadata", "projection_refresh"]
        return {
            "repair_kind": "workspace",
            "allowed_mutations": mutations,
            "sandbox_mode": "none",
            "enqueue_strategy": "metadata_only",
            "prompt_constraints": ["repair workspace metadata/ACL only", "do not mutate product files"],
        }

    def _commit_hygiene_repair(self, scope: Mapping[str, Any]) -> dict[str, Any]:
        if not (
            _required_evidence_ids_from_scope(
                scope,
                "hook_evidence_ids",
                "status_evidence_ids",
                "no_dirty_proof_evidence_ids",
            )
            or scope.get("source_verdict_key")
        ):
            raise RouteExecutorError("commit hygiene repair requires hook/status/no-dirty evidence")
        return {
            "repair_kind": "commit_hygiene",
            "allowed_mutations": ["commit_hygiene_patch"],
            "sandbox_mode": "repair",
            "enqueue_strategy": "merge_queue",
            "prompt_constraints": [
                "repair commit hygiene only",
                "do not perform semantic product RCA",
                "preserve no-dirty proof requirements",
            ],
        }

    def _validate_retry_merge_scope(self, scope: Mapping[str, Any]) -> None:
        _required_int(
            scope,
            "preserve_merge_queue_item_id",
            "failed_merge_queue_item_id",
            "merge_queue_item_id",
            "queue_item_id",
        )
        if _optional_int(scope, "failed_source_queue_item_evidence_id", "source_queue_item_evidence_id") is None:
            raise RouteExecutorError("retry_merge requires failed source queue item evidence in repair_scope")
        source_status = str(scope.get("source_queue_item_status") or scope.get("queue_item_status") or "failed")
        if source_status != "failed":
            raise RouteExecutorError("retry_merge source queue item must be terminal failed")
        if scope.get("result_commit"):
            raise RouteExecutorError("retry_merge source queue item must not have result_commit")
        if _truthy(scope, "replacement_chain_active", "already_superseded"):
            raise RouteExecutorError("retry_merge source queue item is already superseded")


_REPAIR_ACTIONS = {
    "run_product_repair",
    "run_contract_repair",
    "run_canonicalization_repair",
    "run_workspace_repair",
    "run_commit_hygiene_repair",
}
_RETRY_ACTIONS = {
    "retry_dispatch",
    "retry_verifier",
    "retry_merge",
    "retry_sandbox_capture",
    "run_sandbox_cleanup",
}


def _started_route_decision_id(decision: RouteDecision) -> int:
    route_decision_id = _field(decision, "route_decision_id")
    try:
        parsed = int(route_decision_id)
    except (TypeError, ValueError):
        parsed = 0
    if parsed <= 0:
        raise RouteExecutorError("RouteExecutor requires a started/reserved route decision")
    return parsed


def _decision_action(decision: RouteDecision) -> str:
    return str(_field(decision, "action") or "")


def _assert_route_table_consistency(action: str, scope: Mapping[str, Any]) -> None:
    """Reject a decision whose action contradicts the route table for its scope.

    The router always emits the route-table action for a failure's
    ``(failure_class, failure_type)`` (the only deviation is budget-exhaustion
    quiesce, which never reaches a request builder). A persisted/reconstructed
    decision whose action disagrees with the route table for the scope it
    carries was assembled inconsistently and must not build a repair or retry
    request — this is the resume-forgery guard for product repair.
    """

    failure_class = str(scope.get("failure_class") or "")
    failure_type = str(scope.get("failure_type") or "")
    if not failure_class or not failure_type:
        return
    policy = ROUTE_TABLE.get((failure_class, failure_type))
    if policy is None:
        return
    if policy.action != action:
        raise RouteExecutorError(
            f"route action {action!r} contradicts route table action "
            f"{policy.action!r} for {failure_class}/{failure_type}"
        )


def _scope(decision: RouteDecision) -> dict[str, Any]:
    value = _field(decision, "repair_scope") or {}
    if not isinstance(value, Mapping):
        raise RouteExecutorError("route decision repair_scope must be an object")
    return {str(key): item for key, item in value.items()}


def _common_request_fields(
    decision: RouteDecision,
    route_decision_id: int,
    scope: Mapping[str, Any],
) -> dict[str, Any]:
    feature_id = str(scope.get("feature_id") or "")
    dag_sha256 = str(scope.get("dag_sha256") or "")
    if not feature_id:
        raise RouteExecutorError("repair_scope.feature_id is required")
    if not dag_sha256:
        raise RouteExecutorError("repair_scope.dag_sha256 is required")
    return {
        "feature_id": feature_id,
        "dag_sha256": dag_sha256,
        "group_idx": _optional_int(scope, "group_idx"),
        "task_id": _optional_str(scope, "task_id"),
        "route_decision_id": route_decision_id,
        "failure_id": int(_field(decision, "failure_id")),
    }


def _budget_key(decision: RouteDecision, scope: Mapping[str, Any]) -> str:
    return ":".join(
        [
            "route-budget",
            str(scope.get("feature_id") or ""),
            str(scope.get("failure_class") or "unknown"),
            str(scope.get("failure_type") or "unclassified"),
            str(_field(decision, "signature_hash") or ""),
        ]
    )


def _request_idempotency_key(decision: RouteDecision, scope: Mapping[str, Any], *, request_kind: str) -> str:
    return f"idem:{request_kind}-request:{_request_input_digest(decision, scope, request_kind=request_kind)}"


def _request_input_digest(decision: RouteDecision, scope: Mapping[str, Any], *, request_kind: str) -> str:
    return stable_digest(
        {
            "request_kind": request_kind,
            "route_decision": _route_decision_material(decision),
            "repair_scope": _jsonable(scope),
        }
    )


def _route_decision_material(decision: RouteDecision) -> dict[str, Any]:
    material = _jsonable(decision)
    if not isinstance(material, dict):
        return {}
    return {
        key: material.get(key)
        for key in (
            "failure_id",
            "route_decision_id",
            "action",
            "required_evidence_ids",
            "signature_hash",
            "idempotency_key",
            "repair_scope",
        )
    }


def _repair_targets(
    *,
    failure_id: int,
    target_repo_ids: list[str],
    target_paths: list[str],
    target_contract_ids: list[int],
    required_evidence_ids: list[int],
    reason: str,
) -> list[RepairTarget]:
    targets: list[RepairTarget] = [
        RepairTarget(failure_id=failure_id, reason=reason),
    ]
    for repo_id in target_repo_ids:
        targets.append(RepairTarget(repo_id=repo_id, failure_id=failure_id, reason=reason))
    for path in target_paths:
        targets.append(RepairTarget(path=path, failure_id=failure_id, reason=reason))
    for contract_id in target_contract_ids:
        targets.append(RepairTarget(contract_id=contract_id, failure_id=failure_id, reason=reason))
    for evidence_id in required_evidence_ids:
        targets.append(RepairTarget(evidence_id=evidence_id, failure_id=failure_id, reason=reason))
    return targets


def _required_evidence(decision: RouteDecision, scope: Mapping[str, Any]) -> list[int]:
    return _unique_ints(
        [
            *_list_ints(_field(decision, "required_evidence_ids")),
            *_ints(scope, "route_decision_evidence_ids", "evidence_ids"),
            *_required_evidence_ids_from_scope(
                scope,
                "patch_evidence_ids",
                "verdict_evidence_ids",
                "hook_evidence_ids",
                "status_evidence_ids",
                "no_dirty_proof_evidence_ids",
            ),
            *(
                [_required_int(scope, "failed_source_queue_item_evidence_id", "source_queue_item_evidence_id")]
                if "failed_source_queue_item_evidence_id" in scope or "source_queue_item_evidence_id" in scope
                else []
            ),
        ]
    )


def _required_evidence_ids_from_scope(scope: Mapping[str, Any], *keys: str) -> list[int]:
    values: list[int] = []
    for key in keys:
        values.extend(_list_ints(scope.get(key)))
    return _unique_ints(values)


def _strings(scope: Mapping[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        values.extend(_list_strs(scope.get(key)))
    return _unique_text(values)


def _ints(scope: Mapping[str, Any], *keys: str) -> list[int]:
    values: list[int] = []
    for key in keys:
        values.extend(_list_ints(scope.get(key)))
    return _unique_ints(values)


def _list_strs(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, Mapping):
        return [str(key) for key in value]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item)]
    return [str(value)] if str(value) else []


def _list_ints(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, Mapping):
        iterable = value.values()
    elif isinstance(value, (list, tuple, set)):
        iterable = value
    else:
        iterable = [value]
    result: list[int] = []
    for item in iterable:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            result.append(parsed)
    return result


def _optional_int(scope: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        if key not in scope:
            continue
        values = _list_ints(scope.get(key))
        if values:
            return values[0]
    return None


def _required_int(scope: Mapping[str, Any], *keys: str) -> int:
    value = _optional_int(scope, *keys)
    if value is None:
        raise RouteExecutorError(f"repair_scope requires one of: {', '.join(keys)}")
    return value


def _optional_str(scope: Mapping[str, Any], key: str) -> str | None:
    value = scope.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _truthy(scope: Mapping[str, Any], *keys: str) -> bool:
    return any(bool(scope.get(key)) for key in keys)


def _validate_target_paths(paths: list[str]) -> None:
    for path in paths:
        text = str(path or "").strip().replace("\\", "/")
        while text.startswith("./"):
            text = text[2:]
        if (
            not text
            or text == "."
            or text.startswith("~")
            or text.startswith("/")
            or re.match(r"^[A-Za-z]:", text)
            or "\x00" in text
            or any(part == ".." for part in text.split("/"))
        ):
            raise RouteExecutorError(f"unsafe repair target path: {path!r}")


def _authorized_direct_source_verdict(
    scope: Mapping[str, Any],
    *,
    allowed_legacy_routes: frozenset[str],
    allowed_sources: frozenset[str],
) -> bool:
    key = _optional_str(scope, "source_verdict_key")
    legacy_route = _optional_str(scope, "legacy_route")
    source = _optional_str(scope, "source")
    if key is None or legacy_route not in allowed_legacy_routes or source not in allowed_sources:
        return False
    match = _DIRECT_ROUTE_SOURCE_RE.match(key)
    if match is None:
        return False
    group_idx = _optional_int(scope, "group_idx")
    if group_idx is None or int(match.group("group_idx")) != group_idx:
        return False
    return True


def _authorized_product_repair_source_verdict(scope: Mapping[str, Any]) -> bool:
    failure_class = str(scope.get("failure_class") or "")
    if failure_class == "product_defect":
        return _authorized_direct_source_verdict(
            scope,
            allowed_legacy_routes=_DIRECT_PRODUCT_REPAIR_ROUTES,
            allowed_sources=frozenset({"verification_graph"}),
        )
    if failure_class == "contract_violation":
        if str(scope.get("failure_type") or "") not in _SCOPED_CONTRACT_PRODUCT_TYPES:
            return False
        return _authorized_direct_source_verdict(
            scope,
            allowed_legacy_routes=_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES,
            allowed_sources=frozenset({"contract"}),
        )
    return False


def _unique_text(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _unique_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    result: list[int] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _field(value: Any, name: str) -> Any:
    if isinstance(value, Mapping):
        return value.get(name)
    return getattr(value, name, None)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return model_dump(mode="json")
        except TypeError:
            return model_dump()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, set):
        return sorted(_jsonable(item) for item in value)
    return value


def stable_digest(value: Any) -> str:
    payload = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Slice-11h: pure repair-domain helpers moved from implementation.py ----
# Per docs/execution-control-plane/11-refactor-map.md § "Boundary-level API
# contracts" row for execution/repair.py, the pure repair-domain primitives
# (direct-route classification + pure artifact-key classifiers + repair-misc
# misc primitives) live here. These 24 helpers + constants were moved byte-
# for-byte from `workflows/develop/phases/implementation.py` by Slice 11h.
# The phase-level repair PORT surface (the async runner+feature-coupled
# orchestrators + the impl.py-local `_dedupe_preserving_order`-coupled
# text-scanner cluster + the impl.py-local `_safe_context_stem`-coupled
# synthetic-result factories) STAYS in `implementation.py` per the doc-11
# § "Boundary-level API contracts" PRIMITIVE/PORT split.


# Route constants (7) — the deterministic direct-route names used by the
# direct-route classifier and the failure-router. Note that
# `_NORMAL_VERIFY_ROUTE` and `_MANIFEST_FORBIDDEN_CLEANUP_ROUTE` share their
# literal values with the pre-11h `_DIRECT_PRODUCT_REPAIR_ROUTES` and
# `_DIRECT_CONTRACT_PRODUCT_REPAIR_ROUTES` frozensets above; this is
# intentional and locked by `test_repair_extraction.py`.
_COMMIT_HYGIENE_ROUTE = "commit_hygiene_focused"
_MANIFEST_FORBIDDEN_CLEANUP_ROUTE = "manifest_forbidden_product_cleanup"
_REPO_HYGIENE_ROUTE = "repo_hygiene_operator"
_NORMAL_VERIFY_ROUTE = "normal_verify_repair"
_MANIFEST_FORBIDDEN_MARKER = "manifest-forbidden product cleanup"
_OPERATOR_REQUIRED_MARKER = "operator_required=true"
_DAG_CONTRADICTION_MIXED_REPAIR_KIND = "mixed_repair"


def _direct_route_target_paths(route: DagDirectRepairRoute) -> list[str]:
    paths: list[str] = []
    for target in route.target_files:
        value = str(target or "").strip()
        if not value:
            continue
        if ":" in value:
            maybe_path, maybe_line = value.rsplit(":", 1)
            if maybe_line.isdigit():
                value = maybe_path
        paths.append(value.replace("\\", "/"))
    return sorted(set(paths))


def _direct_route_failure_pair(route: DagDirectRepairRoute) -> tuple[str, str, str]:
    if route.operator_required or route.route == _REPO_HYGIENE_ROUTE:
        return "operator_required", "operator_clearance_required", "workspace_authority"
    if route.route == _COMMIT_HYGIENE_ROUTE:
        return "commit_hygiene", "commit_hook_failed", "merge_queue"
    if route.route == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE:
        return "contract_violation", "forbidden_path_touched", "contract"
    if route.route == _NORMAL_VERIFY_ROUTE:
        return "product_defect", "semantic_verifier_rejected", "verification_graph"
    return "unknown", "unclassified", "journal"


def _commit_failure_issue_kind(issue: Issue) -> str:
    text = f"{issue.description}\n{issue.file}".lower()
    if _MANIFEST_FORBIDDEN_MARKER in text:
        return _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
    if (
        "workflow repo hygiene blocker" in text
        or "workflow repos with hygiene blockers" in text
        or "embedded .git" in text
        or "gitlink" in text
    ):
        return _REPO_HYGIENE_ROUTE
    if (
        "pre-commit/husky failed" in text
        or ("commit failed" in text and ("pre-commit" in text or "husky" in text))
    ):
        return _COMMIT_HYGIENE_ROUTE
    return _NORMAL_VERIFY_ROUTE


def _is_deterministic_dag_preflight_issue(issue: Issue) -> bool:
    text = f"{issue.description}\n{issue.file}".lower()
    return bool(
        _MANIFEST_FORBIDDEN_MARKER in text
        or "manifest-forbidden/stale path" in text
        or (
            "reports changed file that is missing from the feature workspace" in text
            and ("manifest" in text or "forbidden" in text)
        )
        or "repair stale task metadata" in text
        or "programmatic dag preflight" in text
    )


def _direct_route_target(issue: Issue) -> str:
    if not issue.file:
        return ""
    if issue.line:
        return f"{issue.file}:{issue.line}"
    return issue.file


def _direct_route_issue_operator_required(issue: Issue) -> bool:
    return _OPERATOR_REQUIRED_MARKER in issue.description


def _normalize_direct_route_signature(verdict: Verdict, route: str) -> str:
    parts = [route]
    for issue in sorted(
        verdict.concerns,
        key=lambda item: (
            item.file,
            item.line,
            re.sub(r"\s+", " ", item.description.strip().lower()),
        ),
    ):
        description = re.sub(r"\s+", " ", issue.description.strip().lower())
        description = re.sub(r"retry-\d+", "retry-N", description)
        description = re.sub(r"attempt \d+", "attempt N", description)
        parts.append(f"{issue.file}:{issue.line}:{description[:500]}")
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def _classify_dag_direct_repair_route(verdict: object) -> DagDirectRepairRoute:
    if not isinstance(verdict, Verdict) or verdict.approved:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="not_a_failed_verdict",
            signature="",
        )
    if verdict.gaps:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_gaps",
            signature="",
        )
    failed_checks = [check for check in verdict.checks if check.result == "FAIL"]
    if failed_checks:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_failed_checks",
            signature="",
        )
    if not verdict.concerns:
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_no_concerns",
            signature="",
        )

    kinds = [_commit_failure_issue_kind(issue) for issue in verdict.concerns]
    if any(kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE for kind in kinds) and all(
        kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
        or _is_deterministic_dag_preflight_issue(issue)
        for kind, issue in zip(kinds, verdict.concerns)
    ):
        targets = sorted({
            target
            for kind, issue in zip(kinds, verdict.concerns)
            if kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE
            if (target := _direct_route_target(issue))
        })
        operator_required = any(
            _direct_route_issue_operator_required(issue)
            for issue in verdict.concerns
        )
        return DagDirectRepairRoute(
            route=_MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            reason=(
                "manifest_forbidden_cleanup_operator_required"
                if operator_required
                else "manifest_forbidden_cleanup"
            ),
            signature=_normalize_direct_route_signature(
                verdict,
                _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            ),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
            operator_required=operator_required,
        )
    if any(kind == _NORMAL_VERIFY_ROUTE for kind in kinds):
        return DagDirectRepairRoute(
            route=_NORMAL_VERIFY_ROUTE,
            reason="verdict_has_non_commit_concerns",
            signature="",
        )
    if all(kind == _REPO_HYGIENE_ROUTE for kind in kinds):
        targets = sorted({
            target
            for issue in verdict.concerns
            if (target := _direct_route_target(issue))
        })
        return DagDirectRepairRoute(
            route=_REPO_HYGIENE_ROUTE,
            reason="repo_hygiene_only_verdict",
            signature=_normalize_direct_route_signature(verdict, _REPO_HYGIENE_ROUTE),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
            operator_required=True,
        )
    if all(kind == _COMMIT_HYGIENE_ROUTE for kind in kinds):
        targets = sorted({
            target
            for issue in verdict.concerns
            if (target := _direct_route_target(issue))
        })
        return DagDirectRepairRoute(
            route=_COMMIT_HYGIENE_ROUTE,
            reason="commit_hygiene_only_verdict",
            signature=_normalize_direct_route_signature(verdict, _COMMIT_HYGIENE_ROUTE),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
        )
    if all(kind == _MANIFEST_FORBIDDEN_CLEANUP_ROUTE for kind in kinds):
        targets = sorted({
            target
            for issue in verdict.concerns
            if (target := _direct_route_target(issue))
        })
        operator_required = any(
            _direct_route_issue_operator_required(issue)
            for issue in verdict.concerns
        )
        return DagDirectRepairRoute(
            route=_MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            reason=(
                "manifest_forbidden_commit_failure_operator_required"
                if operator_required
                else "manifest_forbidden_commit_failure"
            ),
            signature=_normalize_direct_route_signature(
                verdict,
                _MANIFEST_FORBIDDEN_CLEANUP_ROUTE,
            ),
            target_files=targets,
            skip_expanded_verify=True,
            skip_parallel_repair=True,
            skip_rca=True,
            operator_required=operator_required,
        )
    return DagDirectRepairRoute(
        route=_NORMAL_VERIFY_ROUTE,
        reason="mixed_deterministic_routes",
        signature="",
    )


# Pure artifact-key classifiers (5). The text-scanner cluster
# (`_dag_artifact_repair_refs_from_text`,
# `_dag_artifact_repair_refs_from_planned`, etc.) depends on impl.py-local
# `_dedupe_preserving_order` and so STAYS in `implementation.py`. These 5
# primitives are pure str→bool/str predicates over normalized refs.


def _is_dag_artifact_repair_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    if not normalized:
        return True
    if normalized in {"dag.md"}:
        return True
    if normalized.startswith("(") and normalized.endswith(")"):
        return True
    artifact_markers = (
        "/.iriai/artifacts/features/",
        "/.iriai-context/",
        "/.staging/",
        "/dag/",
        "/dag-fragments/",
        "/outputs/dag-ws-",
        "/subfeatures/",
    )
    if any(marker in normalized for marker in artifact_markers):
        return True
    return normalized.startswith((
        ".iriai/",
        ".iriai-context/",
        ".staging/",
        "compile-",
        "dag/",
        "subfeatures/",
        "dag-fragments/",
        "dag-ws-",
        "outputs/dag-ws-",
    ))


def _normalize_dag_artifact_repair_ref(ref: str) -> str:
    normalized = ref.strip().replace("\\", "/")
    normalized = normalized.strip("`'\"")
    normalized = normalized.strip("[](){}<>")
    normalized = normalized.rstrip(".,;:")
    line_ref = re.match(r"^(.+\.[A-Za-z0-9]+):\d+(?::\d+)?$", normalized)
    if line_ref:
        normalized = line_ref.group(1)
    return normalized


def _is_dag_artifact_repair_key(ref: str) -> bool:
    normalized = ref.strip()
    if not normalized or "/" in normalized or "\\" in normalized:
        return False
    if _is_dag_task_artifact_key(normalized):
        return True
    top_level = {
        "artifact-audit-summary",
        "artifact-backfill-status",
        "context",
        "contradiction-decisions",
        "decisions",
        "decomposition",
        "decomposition-structured",
        "design",
        "plan",
        "prd",
        "scope",
        "system-design",
        "test-plan",
    }
    if normalized in top_level:
        return True
    if ":" not in normalized:
        return False
    prefix, slug = normalized.split(":", 1)
    if not slug:
        return False
    return prefix in {
        "artifact-audit",
        "dag-fragment",
        "dag-fragment-attempt",
        "dag-slices",
        "decisions",
        "decisions-structured",
        "design",
        "design-structured",
        "gate-enhancement-backlog",
        "gate-review-ledger",
        "planning-index",
        "plan",
        "plan-structured",
        "prd",
        "prd-structured",
        "system-design",
        "system-design-structured",
        "test-plan",
        "test-plan-structured",
    }


def _is_derived_dag_artifact_key(ref: str) -> bool:
    normalized = ref.strip()
    if not normalized or "/" in normalized or "\\" in normalized:
        return False
    return normalized.startswith(("derived-dag:", "dag-derived:", "dag-regroup:")) and bool(
        normalized.split(":", 1)[1].strip()
    )


def _is_dag_task_artifact_key(ref: str) -> bool:
    normalized = ref.strip()
    if "/" in normalized or "\\" in normalized:
        return False
    return normalized.startswith("dag-task:") and bool(
        normalized.removeprefix("dag-task:").strip()
    )


# Repair-misc primitives (4) — pure helpers over typed types.


def _post_dag_repair_group_idx(source: str, attempt_number: int) -> int:
    digest = hashlib.sha256(f"{source}:{attempt_number}".encode("utf-8")).hexdigest()
    return 100_000 + (int(digest[:8], 16) % 100_000)


def _dag_contradiction_needs_artifact_repair(
    resolution: DagContradictionResolution,
) -> bool:
    return resolution.resolution_kind in {
        "artifact_repair",
        _DAG_CONTRADICTION_MIXED_REPAIR_KIND,
    }


def _dag_contradiction_fix_guidance(
    resolution: DagContradictionResolution,
) -> str:
    return (
        f"{resolution.implementation_direction or resolution.resolution}\n\n"
        "Authoritative contradiction resolution:\n"
        f"{resolution.resolution}\n\n"
        f"Resolution kind: {resolution.resolution_kind}\n\n"
        "Superseded expectation:\n"
        f"{resolution.superseded_expectation or 'not specified'}"
    )


def _dag_product_cleanup_ready_for_artifact_repair(
    report: dict[str, Any],
) -> bool:
    """Return true when product cleanup is done enough to repair metadata."""
    blocking_reasons = {
        "missing_feature_roots",
        "fix_result_status_not_completed_or_partial",
        "product_cleanup_still_required",
    }
    for item in report.get("skipped", []) or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("reason", "")) in blocking_reasons:
            return False
    return True


__all__ = [
    "AttemptKind",
    "EnqueueStrategy",
    "RepairAttemptStatus",
    "RepairKind",
    "RepairMutation",
    "RepairOutcome",
    "RepairRequest",
    "RepairTarget",
    "RetryKind",
    "RetryOutcome",
    "RetryRequest",
    "RetryRequestStatus",
    "RouteAction",
    "RouteDecision",
    "RouteExecutor",
    "RouteExecutorError",
    "RouteRequest",
    "SandboxMode",
    "stable_digest",
    # Slice 11h — direct-route classification cluster.
    "_classify_dag_direct_repair_route",
    "_commit_failure_issue_kind",
    "_direct_route_failure_pair",
    "_direct_route_issue_operator_required",
    "_direct_route_target",
    "_direct_route_target_paths",
    "_is_deterministic_dag_preflight_issue",
    "_normalize_direct_route_signature",
    # Slice 11h — pure artifact-key classifiers.
    "_is_dag_artifact_repair_key",
    "_is_dag_artifact_repair_path",
    "_is_dag_task_artifact_key",
    "_is_derived_dag_artifact_key",
    "_normalize_dag_artifact_repair_ref",
    # Slice 11h — repair-misc primitives.
    "_dag_contradiction_fix_guidance",
    "_dag_contradiction_needs_artifact_repair",
    "_dag_product_cleanup_ready_for_artifact_repair",
    "_post_dag_repair_group_idx",
    # Slice 11h — route constants.
    "_COMMIT_HYGIENE_ROUTE",
    "_DAG_CONTRADICTION_MIXED_REPAIR_KIND",
    "_MANIFEST_FORBIDDEN_CLEANUP_ROUTE",
    "_MANIFEST_FORBIDDEN_MARKER",
    "_NORMAL_VERIFY_ROUTE",
    "_OPERATOR_REQUIRED_MARKER",
    "_REPO_HYGIENE_ROUTE",
]
