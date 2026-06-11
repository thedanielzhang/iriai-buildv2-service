"""Pure task deliverable contract compiler and validator.

This module is intentionally side-effect free. It turns planning-time
``ImplementationTask`` intent plus canonical workspace metadata into immutable
execution contracts, then validates sandbox/workspace evidence against those
contracts. It does not import the implementation workflow monolith, mutate git,
or persist records.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Literal, Sequence, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iriai_build_v2.workflows.develop.execution.workspace_authority import (
    CanonicalRepoRegistry,
    RepoIdentity,
    WorkspaceSnapshot,
    stable_digest,
)


logger = logging.getLogger(__name__)

JsonValue: TypeAlias = (
    str | int | float | bool | None | dict[str, Any] | list[Any]
)
PathIntent = Literal["create", "modify", "delete", "read_only", "generated", "unknown"]
PathMatchKind = Literal["file", "directory"]
GateKind = Literal[
    "deterministic",
    "command",
    "model_verifier",
    "expanded_lens",
    "manual_raw_gate",
]
EvidenceRequirementKind = Literal[
    "path_exists",
    "path_absent",
    "command_passed",
    "verdict_approved",
    "snapshot_fresh",
    "artifact_projection",
]
WriteSetMode = Literal["declared", "unknown_isolated"]
SandboxIsolationMode = Literal["group_shared", "per_task"]
MergeAdmissionMode = Literal["atomic_group", "single_task"]
ContractStatus = Literal["active", "superseded", "cancelled"]
CaseSensitivity = Literal["case_sensitive", "case_insensitive", "unknown"]


_EMPTY_DIGEST = hashlib.sha256(b"").hexdigest()


class _ContractModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)


class ContractCompileError(ValueError):
    """Closed compile failure with Slice 07-compatible routing metadata."""

    def __init__(
        self,
        failure_type: str,
        message: str,
        *,
        failure_class: str = "contract_compile",
        route: str = "run_contract_repair",
        violations: Sequence[dict[str, str]] | None = None,
        warnings: Sequence[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.failure_type = failure_type
        self.route = route
        self.violations = list(violations or [])
        self.warnings = list(warnings or [])


class ContractPathRule(_ContractModel):
    repo_id: str
    path: str
    match_kind: PathMatchKind = "file"
    intent: PathIntent
    required: bool = False
    allow_modify: bool = False
    allow_create: bool = False
    allow_delete: bool = False
    source: str

    @model_validator(mode="after")
    def _canonical_directory_suffix(self) -> "ContractPathRule":
        if self.match_kind == "directory" and self.path and not self.path.endswith("/"):
            self.path = f"{self.path}/"
        if self.match_kind == "file" and self.path.endswith("/"):
            raise ValueError("file path rules cannot end with '/'")
        return self


class AcceptanceCriterionSpec(_ContractModel):
    id: str
    source_model: Literal[
        "TaskAcceptanceCriterion",
        "ImplementationTask",
        "TestAcceptanceCriterion",
        "derived",
    ]
    source_field: str
    source_ordinal: int
    text: str
    must_pass: bool = True
    linked_path_rules: list[str] = Field(default_factory=list)
    digest: str


class RequiredEvidenceSpec(_ContractModel):
    id: str
    kind: EvidenceRequirementKind
    repo_id: str | None = None
    path: str | None = None
    command_id: str | None = None
    criterion_ids: list[str] = Field(default_factory=list)
    evidence_node_kind: str
    required: bool = True

    @model_validator(mode="after")
    def _required_path_fields(self) -> "RequiredEvidenceSpec":
        if self.kind in {"path_exists", "path_absent"} and (not self.repo_id or not self.path):
            raise ValueError(f"{self.kind} evidence requires repo_id and path")
        if self.kind == "command_passed" and not self.command_id:
            raise ValueError("command_passed evidence requires command_id")
        return self


class GateCommandSpec(_ContractModel):
    id: str
    command: list[str]
    cwd_repo_id: str
    timeout_seconds: int
    env_allowlist: list[str] = Field(default_factory=list)
    expected_exit_code: int = 0
    output_budget_chars: int = 12000

    @field_validator("command", mode="before")
    @classmethod
    def _command_must_be_argv(cls, value: Any) -> Any:
        if isinstance(value, str):
            raise ValueError("command gates must provide argv lists, not shell strings")
        return value

    @model_validator(mode="after")
    def _command_invariants(self) -> "GateCommandSpec":
        if not self.command:
            raise ValueError("command argv cannot be empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.output_budget_chars <= 0:
            raise ValueError("output_budget_chars must be positive")
        return self


class VerificationGateSpec(_ContractModel):
    id: str
    gate_kind: GateKind
    name: str
    source: Literal["task_acceptance", "task_verification", "manifest", "derived"]
    criterion_ids: list[str]
    command: GateCommandSpec | None = None
    required_evidence: list[RequiredEvidenceSpec] = Field(default_factory=list)
    lens_slug: str | None = None
    blocks_merge: bool = True
    blocks_checkpoint: bool = True
    digest: str

    @model_validator(mode="after")
    def _gate_invariants(self) -> "VerificationGateSpec":
        if not self.criterion_ids and not (
            self.source == "derived" and self.gate_kind == "deterministic"
        ):
            raise ValueError(
                "gates with empty criterion_ids must be derived deterministic infrastructure gates"
            )
        if self.gate_kind == "command" and self.command is None:
            raise ValueError("command gates require command")
        if self.command is not None and self.gate_kind != "command":
            raise ValueError("command specs are only valid on command gates")
        return self


class ContractExecutionPolicy(_ContractModel):
    write_set_mode: WriteSetMode
    sandbox_isolation: SandboxIsolationMode
    merge_admission: MergeAdmissionMode
    requires_contract_verdict: bool = True
    repair_may_broaden_scope: bool = False
    phased_rollout_allowed: bool = False


class TaskDeliverableContract(_ContractModel):
    id: int | None = None
    feature_id: str
    dag_sha256: str
    source_dag_artifact_id: int
    source_dag_sha256: str
    group_idx: int
    task_id: str
    repo_id: str
    repo_path: str
    required_paths: list[ContractPathRule]
    allowed_paths: list[ContractPathRule]
    read_only_paths: list[ContractPathRule]
    forbidden_paths: list[ContractPathRule]
    generated_outputs: list[ContractPathRule]
    acceptance_criteria: list[AcceptanceCriterionSpec]
    verification_gates: list[VerificationGateSpec]
    execution_policy: ContractExecutionPolicy
    non_goals: list[str]
    dependency_task_ids: list[str]
    unknown_write_set: bool = False
    compile_warnings: list[str]
    normalized_contract_json: dict[str, JsonValue]
    contract_digest: str
    status: ContractStatus = "active"
    idempotency_key: str


class PatchSummary(_ContractModel):
    id: int | None = None
    evidence_node_id: int | None = None
    sandbox_id: str
    contract_ids: list[int] = Field(default_factory=list)
    repo_id: str
    base_commit: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    created_paths: list[str] = Field(default_factory=list)
    modified_paths: list[str] = Field(default_factory=list)
    deleted_paths: list[str] = Field(default_factory=list)
    renamed_paths: dict[str, str] = Field(default_factory=dict)
    diff_sha256: str = _EMPTY_DIGEST
    diff_artifact_id: int | None = None
    summary_artifact_id: int | None = None


class ContractVerdict(_ContractModel):
    id: int | None = None
    contract_id: int
    patch_summary_id: int
    approved: bool
    violation_codes: list[str]
    violations: list[dict[str, str]]
    required_evidence_node_ids: list[int]


class ContractCompileRequest(_ContractModel):
    feature_id: str
    dag_sha256: str
    source_dag_artifact_id: int
    source_dag_sha256: str
    group_idx: int
    task: Any
    all_task_ids: list[str] = Field(default_factory=list)
    workspace_registry: CanonicalRepoRegistry
    repo_id: str | None = None
    manifest_expected_files: list[Any] = Field(default_factory=list)
    manifest_forbidden_files: list[Any] = Field(default_factory=list)
    generated_outputs: list[Any] = Field(default_factory=list)
    verification_gates: list[Any] = Field(default_factory=list)
    external_acceptance_criteria: list[Any] | dict[str, Any] = Field(default_factory=list)
    task_lineage_sources: list[str] = Field(default_factory=list)
    case_sensitivity_by_repo: dict[str, CaseSensitivity] = Field(default_factory=dict)
    task_wave: int = 0


class ContractGroupCompileRequest(_ContractModel):
    feature_id: str
    dag_sha256: str
    source_dag_artifact_id: int
    source_dag_sha256: str
    group_idx: int
    tasks: list[Any]
    all_task_ids: list[str] = Field(default_factory=list)
    workspace_registry: CanonicalRepoRegistry
    manifest_expected_files: list[Any] = Field(default_factory=list)
    manifest_forbidden_files: list[Any] = Field(default_factory=list)
    generated_outputs: list[Any] | dict[str, list[Any]] = Field(default_factory=list)
    verification_gates: list[Any] | dict[str, list[Any]] = Field(default_factory=list)
    external_acceptance_criteria: list[Any] | dict[str, Any] = Field(default_factory=list)
    task_lineage_sources: dict[str, list[str]] = Field(default_factory=dict)
    repo_ids_by_task: dict[str, str] = Field(default_factory=dict)
    case_sensitivity_by_repo: dict[str, CaseSensitivity] = Field(default_factory=dict)
    task_waves: dict[str, int] = Field(default_factory=dict)


class _PathContext(_ContractModel):
    repo: RepoIdentity
    registry: CanonicalRepoRegistry
    display_repo_path: str = ""
    case_sensitivity: CaseSensitivity = "unknown"


class ContractCompiler:
    def compile_task(self, request: ContractCompileRequest) -> TaskDeliverableContract:
        if not isinstance(request, ContractCompileRequest):
            request = ContractCompileRequest.model_validate(request)
        task_id = _task_attr(request.task, "id", "")
        if not task_id:
            _raise_compile("contract_invalid_task", "task id is required")

        repo = _resolve_task_repo(request)
        # N-18 fix 3: _resolve_task_repo (N-17) already ignores an absolute
        # task.repo_path and returns the resolved repo.  But the contract field
        # was still set from the raw task value, so the compiled contract
        # carried e.g. "/Users/.../repos" which later fails the sandbox
        # "repo_path mismatch" check at dispatch.  The contract's repo_path
        # must always be the RESOLVED repo's workspace_relative_path so that
        # _normalize_dispatch_repo_path can match it to the registry.
        raw_task_repo_path = str(_task_attr(request.task, "repo_path", "") or "").strip()
        if raw_task_repo_path and _is_absolute_like(raw_task_repo_path):
            raw_task_repo_path = ""
        repo_path = raw_task_repo_path or repo.workspace_relative_path or repo.repo_name
        context = _PathContext(
            repo=repo,
            registry=request.workspace_registry,
            display_repo_path=repo_path,
            case_sensitivity=request.case_sensitivity_by_repo.get(repo.repo_id, "unknown"),
        )
        all_task_ids = list(dict.fromkeys([*request.all_task_ids, task_id]))

        task_acceptance = _compile_acceptance_criteria(request.task)
        acceptance = _acceptance_with_external_task_gates(
            request.task,
            task_acceptance,
            request.external_acceptance_criteria,
        )
        task_criterion_ids = {criterion.id for criterion in task_acceptance}
        criterion_ids = {criterion.id for criterion in acceptance}
        evidence_specs: list[RequiredEvidenceSpec] = []
        required_paths: list[ContractPathRule] = []
        allowed_paths: list[ContractPathRule] = []
        read_only_paths: list[ContractPathRule] = []
        forbidden_paths: list[ContractPathRule] = []
        generated_outputs: list[ContractPathRule] = []
        compile_warnings: list[str] = []

        file_scope_rules: list[ContractPathRule] = []
        for idx, scope in enumerate(list(_task_attr(request.task, "file_scope", []) or [])):
            raw_path = str(_item_attr(scope, "path", ""))
            action = str(_item_attr(scope, "action", "")).strip().lower()
            if action not in {"create", "modify", "delete", "read_only"}:
                _raise_compile(
                    "contract_invalid_action",
                    f"{task_id} file_scope[{idx}] has unknown action {action!r}",
                    path=raw_path,
                )
            path, match_kind = _normalize_contract_path(
                raw_path,
                context,
                source=f"file_scope[{idx}].path",
                match_kind=_declared_match_kind(scope),
            )
            if action in {"modify", "read_only"}:
                aliased_path = _resolve_existing_file_scope_alias(
                    path,
                    match_kind,
                    context,
                    source=f"file_scope[{idx}].path",
                )
                if aliased_path != path:
                    compile_warnings.append(
                        f"{task_id} file_scope[{idx}].path resolved existing path alias "
                        f"{path} -> {aliased_path}"
                    )
                    path = aliased_path
            if action == "read_only":
                read_only_paths.append(
                    ContractPathRule(
                        repo_id=repo.repo_id,
                        path=path,
                        match_kind=match_kind,
                        intent="read_only",
                        source="file_scope",
                    )
                )
                continue

            has_gate_absence_evidence = _request_has_absence_evidence(
                request,
                path,
                context,
            )
            if action == "delete" and not criterion_ids and not has_gate_absence_evidence:
                _raise_compile(
                    "contract_missing_deletion_evidence",
                    f"{task_id} deletes {path} without acceptance or gate evidence for absence",
                    path=path,
                )

            required = action in {"create", "modify"}
            rule = ContractPathRule(
                repo_id=repo.repo_id,
                path=path,
                match_kind=match_kind,
                intent=_path_intent_for_action(action),
                required=required,
                allow_create=action == "create",
                allow_modify=action == "modify",
                allow_delete=action == "delete",
                source="file_scope",
            )
            if required:
                required_paths.append(rule)
            allowed_paths.append(rule.model_copy(update={"required": False}))
            file_scope_rules.append(rule.model_copy(update={"required": False}))

            if action == "delete" and criterion_ids:
                evidence_specs.append(
                    _evidence_spec(
                        kind="path_absent",
                        repo_id=repo.repo_id,
                        path=path,
                        criterion_ids=sorted(criterion_ids),
                        source="file_scope",
                        required=True,
                    )
                )

        legacy_files = [str(path) for path in list(_task_attr(request.task, "files", []) or []) if str(path).strip()]
        if legacy_files:
            legacy_allowed, warnings = _compile_legacy_files(
                task_id,
                legacy_files,
                context,
                file_scope_rules,
            )
            compile_warnings.extend(warnings)
            if warnings:
                if _legacy_files_widening_tolerated():
                    # N-17b: the planning lane emits legacy `files` =
                    # file_scope paths + read-context extras. The extras are
                    # NEVER added to allowed_paths (zero write authority), so
                    # the contract scope is not widened — tolerate with a
                    # loud WARN instead of failing the group.
                    logger.warning(
                        "contract compile: legacy files extras tolerated "
                        "under IRIAI_CONTRACT_LEGACY_FILES_TOLERANT=1 "
                        "(no write authority granted): %s",
                        "; ".join(warnings),
                    )
                else:
                    _raise_compile(
                        "contract_scope_conflict",
                        "; ".join(warnings),
                        warnings=compile_warnings,
                    )
            allowed_paths.extend(legacy_allowed)

        for idx, entry in enumerate(_request_manifest_entries(request, "forbidden_files")):
            entry_repo_id = str(entry.get("repo_id") or repo.repo_id)
            if entry_repo_id != repo.repo_id:
                continue
            path, match_kind = _normalize_contract_path(
                str(entry.get("path") or ""),
                context,
                source=f"manifest.forbidden_files[{idx}].path",
                match_kind=_entry_match_kind(entry),
            )
            forbidden_paths.append(
                ContractPathRule(
                    repo_id=repo.repo_id,
                    path=path,
                    match_kind=match_kind,
                    intent="unknown",
                    source=str(entry.get("source") or "manifest.forbidden_files"),
                )
            )

        for idx, entry in enumerate(_request_manifest_entries(request, "expected_files")):
            entry_repo_id = str(entry.get("repo_id") or repo.repo_id)
            if entry_repo_id != repo.repo_id:
                continue
            path, match_kind = _normalize_contract_path(
                str(entry.get("path") or ""),
                context,
                source=f"manifest.expected_files[{idx}].path",
                match_kind=_entry_match_kind(entry),
            )
            references_task = _entry_references_task(entry, task_id, request.task_lineage_sources)
            if references_task:
                required_paths.append(
                    ContractPathRule(
                        repo_id=repo.repo_id,
                        path=path,
                        match_kind=match_kind,
                        intent="generated" if _truthy(entry.get("generated")) else "unknown",
                        required=True,
                        source=str(entry.get("source") or "manifest.expected_files"),
                    )
                )
            evidence_specs.append(
                _evidence_spec(
                    kind="path_exists",
                    repo_id=repo.repo_id,
                    path=path,
                    criterion_ids=[],
                    source=str(entry.get("source") or "manifest.expected_files"),
                    required=references_task,
                )
            )

        for idx, item in enumerate(_task_scoped_items(request.generated_outputs, task_id)):
            entry = _entry_dict(item, default_key="path")
            entry_repo_id = str(entry.get("repo_id") or repo.repo_id)
            if entry_repo_id != repo.repo_id:
                continue
            path, match_kind = _normalize_contract_path(
                str(entry.get("path") or ""),
                context,
                source=f"generated_outputs[{idx}].path",
                match_kind=_entry_match_kind(entry),
            )
            absent = _truthy(entry.get("absent") or entry.get("path_absent") or entry.get("intentional_absence"))
            entry_criterion_ids = _normalized_criterion_ids(entry.get("criterion_ids") or entry.get("criteria") or [])
            unknown_criteria = sorted(set(entry_criterion_ids) - task_criterion_ids)
            if unknown_criteria:
                _raise_compile(
                    "contract_unknown_criterion",
                    f"generated output {path} references unknown criteria {unknown_criteria}",
                    path=path,
                )
            if absent and not entry_criterion_ids:
                _raise_compile(
                    "contract_missing_absence_evidence",
                    f"generated output {path} is intentionally absent without authorizing criteria",
                    path=path,
                )
            tied = bool(
                entry.get("source_path")
                or entry.get("gate_id")
                or entry_criterion_ids
                or _path_matches_any(path, required_paths, case_sensitivity=context.case_sensitivity)
            )
            generated_rule = ContractPathRule(
                repo_id=repo.repo_id,
                path=path,
                match_kind=match_kind,
                intent="generated",
                required=not absent,
                allow_create=tied and not absent,
                allow_modify=tied and not absent,
                allow_delete=tied and absent,
                source=str(entry.get("source") or "generated_outputs"),
            )
            generated_outputs.append(generated_rule)
            if tied:
                allowed_paths.append(generated_rule.model_copy(update={"required": False}))
                evidence_specs.append(
                    _evidence_spec(
                        kind="path_absent" if absent else "path_exists",
                        repo_id=repo.repo_id,
                        path=path,
                        criterion_ids=entry_criterion_ids,
                        source=str(entry.get("source") or "generated_outputs"),
                        required=True,
                    )
                )
            else:
                compile_warnings.append(
                    f"{task_id} generated output {path} is not tied to a required source path or gate"
                )
                read_only_paths.append(
                    ContractPathRule(
                        repo_id=repo.repo_id,
                        path=path,
                        match_kind=match_kind,
                        intent="generated",
                        source=str(entry.get("source") or "generated_outputs"),
                    )
                )

        required_paths = _sort_rules(_merge_rules(required_paths))
        allowed_paths = _sort_rules(_merge_rules(allowed_paths))
        read_only_paths = _sort_rules(_merge_rules(read_only_paths))
        forbidden_paths = _sort_rules(_merge_rules(forbidden_paths))
        generated_outputs = _sort_rules(_merge_rules(generated_outputs))

        _fail_on_case_collisions(
            [*required_paths, *allowed_paths, *read_only_paths, *generated_outputs],
            case_sensitivity=context.case_sensitivity,
        )
        _fail_on_same_contract_conflicts(
            task_id,
            required_paths,
            allowed_paths,
            read_only_paths,
            forbidden_paths,
            generated_outputs,
        )

        unknown_write_set = not allowed_paths
        execution_policy = _execution_policy_for_unknown(unknown_write_set)

        gates = _compile_verification_gates(
            request,
            acceptance,
            evidence_specs,
            repo_ids={repo.repo_id for repo in request.workspace_registry.repos},
            explicit_acceptance=task_acceptance,
        )
        dependencies = _normalize_dependencies(
            _task_attr(request.task, "dependencies", []) or [],
            all_task_ids=all_task_ids,
            task_id=task_id,
        )
        non_goals = _compile_non_goals(request.task)

        material = _contract_material(
            feature_id=request.feature_id,
            dag_sha256=request.dag_sha256,
            source_dag_artifact_id=request.source_dag_artifact_id,
            source_dag_sha256=request.source_dag_sha256,
            group_idx=request.group_idx,
            task=request.task,
            task_id=task_id,
            repo_id=repo.repo_id,
            repo_path=repo_path,
            required_paths=required_paths,
            allowed_paths=allowed_paths,
            read_only_paths=read_only_paths,
            forbidden_paths=forbidden_paths,
            generated_outputs=generated_outputs,
            acceptance_criteria=acceptance,
            verification_gates=gates,
            execution_policy=execution_policy,
            non_goals=non_goals,
            dependency_task_ids=dependencies,
            unknown_write_set=unknown_write_set,
            compile_warnings=compile_warnings,
        )
        contract_digest = stable_digest(material)
        idempotency_key = stable_digest(
            {
                "kind": "task-deliverable-contract",
                "feature_id": request.feature_id,
                "dag_sha256": request.dag_sha256,
                "group_idx": request.group_idx,
                "task_id": task_id,
                "repo_id": repo.repo_id,
                "contract_digest": contract_digest,
            }
        )
        return TaskDeliverableContract(
            feature_id=request.feature_id,
            dag_sha256=request.dag_sha256,
            source_dag_artifact_id=request.source_dag_artifact_id,
            source_dag_sha256=request.source_dag_sha256,
            group_idx=request.group_idx,
            task_id=task_id,
            repo_id=repo.repo_id,
            repo_path=repo_path,
            required_paths=required_paths,
            allowed_paths=allowed_paths,
            read_only_paths=read_only_paths,
            forbidden_paths=forbidden_paths,
            generated_outputs=generated_outputs,
            acceptance_criteria=acceptance,
            verification_gates=gates,
            execution_policy=execution_policy,
            non_goals=non_goals,
            dependency_task_ids=dependencies,
            unknown_write_set=unknown_write_set,
            compile_warnings=compile_warnings,
            normalized_contract_json=material,
            contract_digest=contract_digest,
            idempotency_key=idempotency_key,
        )

    def compile_group(self, request: ContractGroupCompileRequest) -> list[TaskDeliverableContract]:
        if not isinstance(request, ContractGroupCompileRequest):
            request = ContractGroupCompileRequest.model_validate(request)
        all_task_ids = list(request.all_task_ids or [_task_attr(task, "id", "") for task in request.tasks])
        contracts: list[TaskDeliverableContract] = []
        for task in request.tasks:
            task_id = str(_task_attr(task, "id", ""))
            compile_request = ContractCompileRequest(
                feature_id=request.feature_id,
                dag_sha256=request.dag_sha256,
                source_dag_artifact_id=request.source_dag_artifact_id,
                source_dag_sha256=request.source_dag_sha256,
                group_idx=request.group_idx,
                task=task,
                all_task_ids=all_task_ids,
                workspace_registry=request.workspace_registry,
                repo_id=request.repo_ids_by_task.get(task_id),
                manifest_expected_files=request.manifest_expected_files,
                manifest_forbidden_files=request.manifest_forbidden_files,
                manifest_entries=_extra_attr(request, "manifest_entries", {}),
                manifest=_extra_attr(request, "manifest", {}),
                generated_outputs=_task_scoped_items(request.generated_outputs, task_id),
                verification_gates=_task_scoped_items(request.verification_gates, task_id),
                external_acceptance_criteria=request.external_acceptance_criteria,
                task_lineage_sources=request.task_lineage_sources.get(task_id, []),
                case_sensitivity_by_repo=request.case_sensitivity_by_repo,
                task_wave=request.task_waves.get(task_id, 0),
            )
            contracts.append(self.compile_task(compile_request))

        _fail_on_group_conflicts(
            contracts,
            task_waves=request.task_waves,
        )
        return contracts

    def validate_patch(
        self,
        contract: TaskDeliverableContract,
        patch: PatchSummary,
        workspace: WorkspaceSnapshot,
    ) -> ContractVerdict:
        violations: list[dict[str, str]] = []
        if patch.repo_id != contract.repo_id:
            _add_violation(
                violations,
                code="contract_id_mismatch",
                failure_class="contract_violation",
                failure_type="contract_id_mismatch",
                route="quiesce",
                path="",
                detail=f"patch repo {patch.repo_id} does not match contract repo {contract.repo_id}",
            )
        if contract.id is not None and patch.contract_ids and contract.id not in patch.contract_ids:
            _add_violation(
                violations,
                code="contract_id_mismatch",
                failure_class="contract_violation",
                failure_type="contract_id_mismatch",
                route="quiesce",
                path="",
                detail=f"patch contracts {patch.contract_ids} do not include {contract.id}",
            )
        _validate_patch_digest(patch, violations)

        case_sensitivity = workspace.case_sensitivity or "unknown"
        normalized_patch = _normalize_patch_paths(contract, patch, workspace, violations)
        _validate_patch_case_collisions(normalized_patch, case_sensitivity, violations)
        base_present_paths = _snapshot_base_present_paths(workspace)

        for operation, path in normalized_patch.created:
            self._validate_patch_operation(
                contract,
                path,
                operation,
                case_sensitivity,
                violations,
                base_present_paths=base_present_paths,
            )
        for operation, path in normalized_patch.modified:
            self._validate_patch_operation(
                contract,
                path,
                operation,
                case_sensitivity,
                violations,
                base_present_paths=base_present_paths,
            )
        for operation, path in normalized_patch.deleted:
            self._validate_patch_operation(
                contract,
                path,
                operation,
                case_sensitivity,
                violations,
                base_present_paths=base_present_paths,
            )
        for old_path, new_path in normalized_patch.renamed:
            self._validate_patch_operation(
                contract,
                old_path,
                "rename_from",
                case_sensitivity,
                violations,
                base_present_paths=base_present_paths,
            )
            self._validate_patch_operation(
                contract,
                new_path,
                "rename_to",
                case_sensitivity,
                violations,
                base_present_paths=base_present_paths,
            )

        changed_count = normalized_patch.change_count
        if changed_count == 0 and _contract_requires_mutation(contract):
            _add_violation(
                violations,
                code="empty_patch_requires_mutation",
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path="",
                detail="empty patch cannot satisfy required writable or generated deliverables",
            )

        present_paths = _virtual_presence_after_patch(contract, patch, workspace, normalized_patch)
        _validate_required_presence(contract, present_paths, violations, generated=False)
        _validate_required_presence(contract, present_paths, violations, generated=True)

        return _verdict(
            contract,
            patch_summary_id=patch.id,
            violations=violations,
            required_evidence_node_ids=_int_list(_extra_attr(patch, "required_evidence_node_ids", [])),
        )

    def validate_presence(
        self,
        contract: TaskDeliverableContract,
        snapshot: WorkspaceSnapshot,
    ) -> ContractVerdict:
        violations: list[dict[str, str]] = []
        present_paths = _snapshot_present_paths(snapshot)
        case_sensitivity = snapshot.case_sensitivity or "unknown"
        for forbidden in contract.forbidden_paths:
            for path in sorted(present_paths):
                if _rule_matches_path(forbidden, path, case_sensitivity=case_sensitivity):
                    _add_violation(
                        violations,
                        code="forbidden_path_present",
                        failure_class="contract_violation",
                        failure_type="forbidden_path_touched",
                        route="run_product_repair",
                        path=path,
                        rule=forbidden.path,
                    )
        _validate_required_presence(contract, present_paths, violations, generated=False)
        _validate_required_presence(contract, present_paths, violations, generated=True)
        return _verdict(
            contract,
            patch_summary_id=None,
            violations=violations,
            required_evidence_node_ids=_int_list(_extra_attr(snapshot, "required_evidence_node_ids", [])),
        )

    def _validate_patch_operation(
        self,
        contract: TaskDeliverableContract,
        path: str,
        operation: str,
        case_sensitivity: CaseSensitivity,
        violations: list[dict[str, str]],
        *,
        base_present_paths: set[str] | None = None,
    ) -> None:
        forbidden = _first_matching_rule(contract.forbidden_paths, path, case_sensitivity=case_sensitivity)
        if forbidden is not None:
            _add_violation(
                violations,
                code="forbidden_path_touched",
                failure_class="contract_violation",
                failure_type="forbidden_path_touched",
                route="run_product_repair",
                path=path,
                operation=operation,
                rule=forbidden.path,
            )
            return

        forbidden_variant = _case_variant_rule(contract.forbidden_paths, path)
        if case_sensitivity == "unknown" and forbidden_variant is not None:
            _add_violation(
                violations,
                code="case_collision_variant",
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path=path,
                rule=forbidden_variant.path,
            )
            return

        read_only_variant = _case_variant_rule(contract.read_only_paths, path)
        if case_sensitivity == "unknown" and read_only_variant is not None:
            _add_violation(
                violations,
                code="case_collision_variant",
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path=path,
                rule=read_only_variant.path,
            )
            return
        read_only = _first_matching_rule(contract.read_only_paths, path, case_sensitivity=case_sensitivity)
        if read_only is not None:
            _add_violation(
                violations,
                code="read_only_path_touched",
                failure_class="contract_violation",
                failure_type="read_only_path_touched",
                route="run_product_repair",
                path=path,
                operation=operation,
                rule=read_only.path,
            )
            return

        allowed_variant = _case_variant_rule(contract.allowed_paths, path)
        if case_sensitivity == "unknown" and allowed_variant is not None:
            _add_violation(
                violations,
                code="case_collision_variant",
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path=path,
                rule=allowed_variant.path,
            )
            return

        matching = [
            rule
            for rule in contract.allowed_paths
            if _rule_matches_path(rule, path, case_sensitivity=case_sensitivity)
        ]
        if operation in {"create", "rename_to"}:
            permitted = any(rule.allow_create for rule in matching)
            if not permitted and base_present_paths is not None:
                # Symmetric to the modify-when-present tolerance below: the
                # contract declared this exact file as a `modify` deliverable,
                # but it is ABSENT at the base commit, so a `create` is the only
                # way to deliver it (the upstream DAG mislabeled create-vs-modify
                # intent). Permit it — this does NOT broaden writes beyond the
                # already-declared deliverable path.
                permitted = any(
                    rule.allow_modify
                    and rule.match_kind == "file"
                    and _same_path(rule.path, path, case_sensitivity)
                    and not _path_present_exact(
                        base_present_paths,
                        path,
                        case_sensitivity=case_sensitivity,
                    )
                    for rule in matching
                )
        elif operation in {"modify"}:
            permitted = any(rule.allow_modify for rule in matching)
            if not permitted and base_present_paths is not None:
                permitted = any(
                    rule.allow_create
                    and rule.match_kind == "file"
                    and _same_path(rule.path, path, case_sensitivity)
                    and _path_present_exact(
                        base_present_paths,
                        path,
                        case_sensitivity=case_sensitivity,
                    )
                    for rule in matching
                )
        elif operation in {"delete", "rename_from"}:
            permitted = any(rule.allow_delete for rule in matching)
        else:
            permitted = False

        if not permitted:
            code = {
                "create": "create_outside_allowed_paths",
                "modify": "modify_outside_allowed_paths",
                "delete": "delete_outside_allowed_paths",
                "rename_from": "rename_from_outside_allowed_paths",
                "rename_to": "rename_to_outside_allowed_paths",
            }.get(operation, "outside_allowed_paths")
            _add_violation(
                violations,
                code=code,
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path=path,
                operation=operation,
            )


class _NormalizedPatch(_ContractModel):
    created: list[tuple[str, str]] = Field(default_factory=list)
    modified: list[tuple[str, str]] = Field(default_factory=list)
    deleted: list[tuple[str, str]] = Field(default_factory=list)
    renamed: list[tuple[str, str]] = Field(default_factory=list)

    @property
    def change_count(self) -> int:
        return len(self.created) + len(self.modified) + len(self.deleted) + len(self.renamed)

    @property
    def all_paths(self) -> list[str]:
        paths: list[str] = []
        paths.extend(path for _, path in self.created)
        paths.extend(path for _, path in self.modified)
        paths.extend(path for _, path in self.deleted)
        for old_path, new_path in self.renamed:
            paths.extend([old_path, new_path])
        return paths


def _compile_acceptance_criteria(task: Any) -> list[AcceptanceCriterionSpec]:
    rows: list[AcceptanceCriterionSpec] = []

    def add_row(
        *,
        source_model: Literal["TaskAcceptanceCriterion", "ImplementationTask", "derived"],
        source_field: str,
        source_ordinal: int,
        text: str,
        source_id: str | None = None,
    ) -> None:
        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            return
        criterion_id = _slug_id(source_id) if source_id else f"ac-{source_ordinal}-{_short_digest(clean_text)}"
        digest = stable_digest(
            {
                "id": criterion_id,
                "source_model": source_model,
                "source_field": source_field,
                "source_ordinal": source_ordinal,
                "text": clean_text,
                "must_pass": True,
            }
        )
        rows.append(
            AcceptanceCriterionSpec(
                id=criterion_id,
                source_model=source_model,
                source_field=source_field,
                source_ordinal=source_ordinal,
                text=clean_text,
                must_pass=True,
                digest=digest,
            )
        )

    ordinal = 0
    for item in list(_task_attr(task, "acceptance_criteria", []) or []):
        source_id = str(_item_attr(item, "id", "") or _item_attr(item, "criterion_id", "") or "")
        add_row(
            source_model="TaskAcceptanceCriterion",
            source_field="description",
            source_ordinal=ordinal,
            text=str(_item_attr(item, "description", "")),
            source_id=source_id or None,
        )
        ordinal += 1
        not_text = str(_item_attr(item, "not_criteria", "") or "").strip()
        if not_text:
            add_row(
                source_model="TaskAcceptanceCriterion",
                source_field="not_criteria",
                source_ordinal=ordinal,
                text=f"Must not: {not_text}",
            )
            ordinal += 1

    for field_name, prefix in (
        ("not_criteria", "Must not"),
        ("counterexamples", "Prohibited counterexample"),
        ("security_concerns", "Security requirement"),
    ):
        values = _string_list(_task_attr(task, field_name, []) or [])
        for text in values:
            add_row(
                source_model="ImplementationTask",
                source_field=field_name,
                source_ordinal=ordinal,
                text=f"{prefix}: {text}",
            )
            ordinal += 1
    return sorted(rows, key=lambda row: (row.source_ordinal, row.id))


def _acceptance_with_external_task_gates(
    task: Any,
    acceptance: Sequence[AcceptanceCriterionSpec],
    external_acceptance_criteria: list[Any] | dict[str, Any],
) -> list[AcceptanceCriterionSpec]:
    existing_ids = {criterion.id for criterion in acceptance}
    gate_ids = {
        _slug_id(str(raw_id))
        for raw_id in list(_task_attr(task, "verification_gates", []) or [])
    }
    needed_ids = gate_ids - existing_ids
    if not needed_ids:
        return list(acceptance)
    external_by_id = _external_acceptance_criteria_by_id(
        external_acceptance_criteria,
        needed_ids=needed_ids,
    )
    additions: list[AcceptanceCriterionSpec] = []
    added_ids: set[str] = set()
    for raw_id in list(_task_attr(task, "verification_gates", []) or []):
        criterion_id = _slug_id(str(raw_id))
        if criterion_id in existing_ids or criterion_id in added_ids:
            continue
        external = external_by_id.get(criterion_id)
        if external is None:
            continue
        additions.append(external)
        added_ids.add(criterion_id)
    return sorted(
        [*acceptance, *additions],
        key=lambda row: (row.source_ordinal, row.id),
    )


def _external_acceptance_criteria_by_id(
    external_acceptance_criteria: list[Any] | dict[str, Any],
    *,
    needed_ids: set[str] | None = None,
) -> dict[str, AcceptanceCriterionSpec]:
    criteria: dict[str, AcceptanceCriterionSpec] = {}
    for ordinal, item in enumerate(_external_acceptance_items(external_acceptance_criteria)):
        entry = _entry_dict(item)
        raw_id = str(
            entry.get("id")
            or entry.get("criterion_id")
            or entry.get("ac_id")
            or entry.get("acceptance_criterion_id")
            or ""
        ).strip()
        if not raw_id:
            continue
        criterion_id = _slug_id(raw_id)
        if needed_ids is not None and criterion_id not in needed_ids:
            continue
        text = _external_acceptance_text(raw_id, entry)
        if not text:
            continue
        source = str(
            entry.get("source")
            or entry.get("source_ref")
            or entry.get("artifact_key")
            or "external_acceptance_criteria"
        )
        source_ordinal = _coerce_int(entry.get("source_ordinal"), 100_000 + ordinal)
        # Item-5: planning-contract AC waivers. The catalog loader (flag-gated,
        # IRIAI_DEVELOP_CONTRACT_AC_WAIVERS) marks waived entries; an unmarked
        # catalog (the default) compiles must_pass=True with byte-identical
        # digests. Every waiver application is WARN-logged with its source —
        # no silent waivers (fefd8f8 discipline).
        waived = bool(entry.get("waived"))
        must_pass = not waived
        if waived:
            logger.warning(
                "contract AC waiver honored: %s compiled with must_pass=False "
                "(source: %s)",
                raw_id,
                str(entry.get("waiver_source") or source),
            )
        digest = stable_digest(
            {
                "id": criterion_id,
                "source_model": "TestAcceptanceCriterion",
                "source_field": source,
                "source_ordinal": source_ordinal,
                "text": text,
                "must_pass": must_pass,
            }
        )
        criterion = AcceptanceCriterionSpec(
            id=criterion_id,
            source_model="TestAcceptanceCriterion",
            source_field=source,
            source_ordinal=source_ordinal,
            text=text,
            must_pass=must_pass,
            digest=digest,
        )
        existing = criteria.get(criterion_id)
        if existing is not None and existing.digest != criterion.digest:
            _raise_compile(
                "contract_duplicate_criterion",
                f"external acceptance criterion {raw_id!r} has conflicting definitions",
            )
        criteria[criterion_id] = criterion
    return criteria


def _external_acceptance_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        if isinstance(value.get("content"), dict):
            return _external_acceptance_items(value["content"])
        if "acceptance_criteria" in value:
            return list(value.get("acceptance_criteria") or [])
        if any(key in value for key in ("id", "criterion_id", "ac_id", "acceptance_criterion_id")):
            return [value]
        items: list[Any] = []
        for key, item in value.items():
            if isinstance(item, dict):
                entry = dict(item)
                entry.setdefault("id", key)
                items.append(entry)
            else:
                items.append({"id": key, "description": str(item)})
        return items
    if isinstance(value, (list, tuple)):
        return list(value)
    return []


def _external_acceptance_text(raw_id: str, entry: dict[str, Any]) -> str:
    parts: list[str] = []
    description = str(
        entry.get("description")
        or entry.get("text")
        or entry.get("title")
        or entry.get("name")
        or ""
    ).strip()
    if description:
        parts.append(description)
    method = str(entry.get("verification_method") or "").strip()
    if method:
        parts.append(f"Method: {method}")
    pass_condition = str(entry.get("pass_condition") or "").strip()
    if pass_condition:
        parts.append(f"Pass Condition: {pass_condition}")
    if not parts:
        return ""
    return " ".join(f"{raw_id} — {' '.join(parts)}".split())


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _legacy_files_widening_tolerated() -> bool:
    """N-17b flag (IRIAI_CONTRACT_LEGACY_FILES_TOLERANT, default OFF).

    ON: legacy ``files`` entries outside the writable file_scope log a WARN
    instead of failing the group compile. They still grant NO write
    authority either way; the flag only controls fatality.
    """
    return os.environ.get("IRIAI_CONTRACT_LEGACY_FILES_TOLERANT", "") == "1"


def _compile_legacy_files(
    task_id: str,
    legacy_files: Sequence[str],
    context: _PathContext,
    file_scope_rules: Sequence[ContractPathRule],
) -> tuple[list[ContractPathRule], list[str]]:
    allowed: list[ContractPathRule] = []
    warnings: list[str] = []
    has_writable_scope = bool(file_scope_rules)
    for idx, raw_path in enumerate(legacy_files):
        path, match_kind = _normalize_contract_path(
            raw_path,
            context,
            source=f"legacy files[{idx}]",
            match_kind="directory" if raw_path.strip().endswith("/") else None,
        )
        comparison_path = path
        if has_writable_scope:
            comparison_path = _resolve_existing_file_scope_alias(
                path,
                match_kind,
                context,
                source=f"legacy files[{idx}]",
            )
        if has_writable_scope and not any(
            _rule_matches_path(rule, comparison_path, case_sensitivity=context.case_sensitivity)
            for rule in file_scope_rules
        ):
            warnings.append(
                f"{task_id} legacy files[{idx}]={comparison_path} widens non-empty file_scope"
            )
            continue
        if not has_writable_scope:
            allowed.append(
                ContractPathRule(
                    repo_id=context.repo.repo_id,
                    path=path,
                    match_kind=match_kind,
                    intent="unknown",
                    allow_create=True,
                    allow_modify=True,
                    source="legacy files",
                )
            )
    return allowed, warnings


def _compile_verification_gates(
    request: ContractCompileRequest,
    acceptance: Sequence[AcceptanceCriterionSpec],
    evidence_specs: Sequence[RequiredEvidenceSpec],
    *,
    repo_ids: set[str],
    explicit_acceptance: Sequence[AcceptanceCriterionSpec] | None = None,
) -> list[VerificationGateSpec]:
    criteria_by_id = {criterion.id: criterion for criterion in acceptance}
    explicit_criteria_by_id = {
        criterion.id: criterion
        for criterion in (explicit_acceptance if explicit_acceptance is not None else acceptance)
    }
    gates: list[VerificationGateSpec] = []

    for raw_gate in _task_scoped_items(request.verification_gates, str(_task_attr(request.task, "id", ""))):
        gates.append(
            _coerce_gate(
                raw_gate,
                explicit_criteria_by_id,
                repo_ids=repo_ids,
                default_source="task_verification",
            )
        )

    task_id = str(_task_attr(request.task, "id", ""))
    for raw_id in list(_task_attr(request.task, "verification_gates", []) or []):
        criterion_id = _slug_id(str(raw_id))
        criterion = criteria_by_id.get(criterion_id)
        if criterion is None:
            _raise_compile(
                "contract_unknown_criterion",
                f"{task_id} verification gate references unknown criterion {raw_id!r}",
            )
        gate_digest_seed = stable_digest({"criterion_id": criterion_id, "digest": criterion.digest})
        # Item-5: a gate citing a planning-contract-waived AC (must_pass=False)
        # is kept VISIBLE but non-blocking — never a hard model_verifier gate,
        # never silently dropped. WARN-logged per application (no silent
        # waivers). Default path (no waivers marked) is byte-identical.
        gate_waived = not criterion.must_pass
        if gate_waived:
            logger.warning(
                "contract AC waiver honored: %s verification gate for %s "
                "compiled non-blocking (blocks_merge=False, "
                "blocks_checkpoint=False)",
                task_id,
                criterion_id,
            )
        gates.append(
            VerificationGateSpec(
                id=f"gate:{task_id}:model_verifier:{gate_digest_seed[:10]}",
                gate_kind="model_verifier",
                name=(
                    f"Verify {criterion_id} (waived)"
                    if gate_waived
                    else f"Verify {criterion_id}"
                ),
                source="task_verification",
                criterion_ids=[criterion_id],
                blocks_merge=not gate_waived,
                blocks_checkpoint=not gate_waived,
                digest="",
            )
        )

    if evidence_specs:
        evidence = sorted(evidence_specs, key=lambda item: item.id)
        gate_digest_seed = stable_digest([item.model_dump(mode="json") for item in evidence])
        gates.append(
            VerificationGateSpec(
                id=f"gate:{task_id}:deterministic:{gate_digest_seed[:10]}",
                gate_kind="deterministic",
                name="Contract path evidence",
                source="derived",
                criterion_ids=[],
                required_evidence=evidence,
                digest="",
            )
        )

    deduped: dict[str, VerificationGateSpec] = {}
    for gate in gates:
        gate = _finalize_gate(gate, criteria_by_id, repo_ids=repo_ids)
        existing = deduped.get(gate.id)
        if existing is not None and existing.digest != gate.digest:
            _raise_compile(
                "contract_duplicate_gate",
                f"duplicate gate id {gate.id!r} has different digest",
            )
        deduped[gate.id] = gate
    return sorted(deduped.values(), key=lambda gate: gate.id)


def _coerce_gate(
    raw_gate: Any,
    criteria_by_id: dict[str, AcceptanceCriterionSpec],
    *,
    repo_ids: set[str],
    default_source: Literal["task_acceptance", "task_verification", "manifest", "derived"],
) -> VerificationGateSpec:
    if isinstance(raw_gate, VerificationGateSpec):
        gate = raw_gate
    else:
        data = _entry_dict(raw_gate)
        if "criterion_id" in data and "criterion_ids" not in data:
            data["criterion_ids"] = [data.pop("criterion_id")]
        data["criterion_ids"] = _normalized_criterion_ids(data.get("criterion_ids") or [])
        if "source" not in data:
            data["source"] = default_source
        if "gate_kind" not in data:
            data["gate_kind"] = "command" if data.get("command") else "model_verifier"
        if "name" not in data:
            data["name"] = str(data.get("id") or data["gate_kind"])
        if data.get("command") and not isinstance(data["command"], GateCommandSpec):
            try:
                data["command"] = GateCommandSpec.model_validate(data["command"])
            except ValueError as exc:
                _raise_compile(
                    "contract_invalid_gate",
                    f"gate {data.get('id') or data['gate_kind']} has invalid command spec: {exc}",
                )
        if "id" not in data or not data["id"]:
            digest_seed = stable_digest(
                {
                    "kind": data["gate_kind"],
                    "criterion_ids": data["criterion_ids"],
                    "command": _model_dump(data.get("command")),
                }
            )
            task_id = str(data.get("task_id") or "task")
            data["id"] = f"gate:{task_id}:{data['gate_kind']}:{digest_seed[:10]}"
        data["digest"] = str(data.get("digest") or "")
        gate = VerificationGateSpec.model_validate(data)
    return _finalize_gate(gate, criteria_by_id, repo_ids=repo_ids)


def _finalize_gate(
    gate: VerificationGateSpec,
    criteria_by_id: dict[str, AcceptanceCriterionSpec],
    *,
    repo_ids: set[str],
) -> VerificationGateSpec:
    criterion_ids = _normalized_criterion_ids(gate.criterion_ids)
    unknown = sorted(set(criterion_ids) - set(criteria_by_id))
    if unknown:
        _raise_compile(
            "contract_unknown_criterion",
            f"gate {gate.id} references unknown criteria {unknown}",
        )
    if gate.command is not None and gate.command.cwd_repo_id not in repo_ids:
        _raise_compile(
            "contract_invalid_path",
            f"gate {gate.id} command cwd repo {gate.command.cwd_repo_id!r} is not in registry",
        )
    for evidence in gate.required_evidence:
        unknown_evidence_criteria = sorted(set(_normalized_criterion_ids(evidence.criterion_ids)) - set(criteria_by_id))
        if unknown_evidence_criteria:
            _raise_compile(
                "contract_unknown_criterion",
                f"gate {gate.id} evidence {evidence.id} references unknown criteria {unknown_evidence_criteria}",
            )
    digest_material = gate.model_dump(mode="json", exclude={"digest"})
    digest_material["criterion_ids"] = criterion_ids
    digest_material["required_evidence"] = sorted(
        (_model_dump(item) for item in gate.required_evidence),
        key=lambda item: str(item.get("id", "")),
    )
    digest = stable_digest(digest_material)
    return gate.model_copy(update={"criterion_ids": criterion_ids, "digest": digest})


def _resolve_task_repo(request: ContractCompileRequest) -> RepoIdentity:
    repo_id = request.repo_id or str(_task_attr(request.task, "repo_id", "") or "")
    if repo_id:
        repo = _repo_by_id(request.workspace_registry, repo_id)
        if repo is None:
            _raise_compile(
                "contract_invalid_path",
                f"repo id {repo_id!r} is not present in the canonical registry",
            )
        return repo

    raw_repo_path = str(_task_attr(request.task, "repo_path", "") or "").strip()
    if raw_repo_path and _is_absolute_like(raw_repo_path):
        # N-17: the planning DAG path-resolution lane emits the absolute
        # repos ROOT as repo_path (the root of ALL repos — zero repo
        # information) while file_scope paths are repos-root-relative with
        # the repo-name prefix. Treat the absolute value as unset and fall
        # through to file_scope prefix derivation; derivation failure below
        # stays loud, so never-guess is preserved.
        logger.warning(
            "contract compile: task %s repo_path %r is absolute (planning "
            "interface N-17) — ignoring it and deriving the repo from "
            "file_scope prefixes",
            _task_attr(request.task, "id", "?"),
            raw_repo_path,
        )
        raw_repo_path = ""
    if raw_repo_path:
        alias_prefix = _matching_alias_prefix(raw_repo_path, request.workspace_registry)
        if alias_prefix:
            _raise_worktree_alias_compile(
                f"task repo_path {raw_repo_path!r} references alias {alias_prefix}",
                path=raw_repo_path,
            )
        for repo in request.workspace_registry.repos:
            if _path_prefix_matches(raw_repo_path, _repo_prefixes(repo, "")):
                return repo
        _raise_compile(
            "contract_invalid_path",
            f"task repo_path {raw_repo_path!r} does not resolve to a canonical repo",
        )

    if len(request.workspace_registry.repos) == 1:
        return request.workspace_registry.repos[0]

    derived = _derive_repo_from_file_scope(request)
    if derived is not None:
        logger.warning(
            "contract compile: task %s repo derived from file_scope path "
            "prefixes -> %s (N-17 planning interface fallback)",
            _task_attr(request.task, "id", "?"),
            derived.repo_name,
        )
        return derived

    file_scope = _task_attr(request.task, "file_scope", None) or []
    if not file_scope:
        # Pure gate/probe tasks carry no files: the repo only anchors the
        # contract's cwd formality — there is no write surface to mis-scope.
        first = request.workspace_registry.repos[0]
        logger.warning(
            "contract compile: task %s has no file_scope and no resolvable "
            "repo — anchoring to first registry repo %s (N-17 zero-file "
            "fallback)",
            _task_attr(request.task, "id", "?"),
            first.repo_name,
        )
        return first

    _raise_compile(
        "contract_invalid_path",
        "task repo_id or repo_path is required when the registry has multiple repos",
    )


def _derive_repo_from_file_scope(request: ContractCompileRequest) -> RepoIdentity | None:
    """N-17 fallback: derive the task's repo from file_scope path prefixes.

    A task's repo is where it WRITES: write actions (create/modify/delete)
    vote first; read_only paths vote only when the task has no writes. The
    unique top-voted repo wins; a tie or zero matches returns None so the
    caller's loud failure is preserved (never-guess).
    """
    file_scope = _task_attr(request.task, "file_scope", None) or []
    write_votes: dict[str, int] = {}
    all_votes: dict[str, int] = {}
    repos_by_name: dict[str, RepoIdentity] = {}
    for entry in file_scope:
        path = str(_item_attr(entry, "path", "") or "")
        action = str(_item_attr(entry, "action", "") or "")
        if not path:
            continue
        for repo in request.workspace_registry.repos:
            if _path_prefix_matches(path, _repo_prefixes(repo, "")):
                repos_by_name[repo.repo_name] = repo
                all_votes[repo.repo_name] = all_votes.get(repo.repo_name, 0) + 1
                if action in {"create", "modify", "delete"}:
                    write_votes[repo.repo_name] = write_votes.get(repo.repo_name, 0) + 1
                break
    for votes in (write_votes, all_votes):
        if not votes:
            continue
        ranked = sorted(votes.items(), key=lambda kv: (-kv[1], kv[0]))
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            return repos_by_name[ranked[0][0]]
        return None  # tie — stay loud
    return None


def _normalize_contract_path(
    raw_path: str,
    context: _PathContext,
    *,
    source: str,
    match_kind: PathMatchKind | None = None,
) -> tuple[str, PathMatchKind]:
    text = str(raw_path or "").strip()
    if not text:
        _raise_compile("contract_invalid_path", f"{source} is empty")
    if "\x00" in text:
        _raise_compile("contract_invalid_path", f"{source} contains a NUL byte", path=text)
    text = text.replace("\\", "/")
    if _matching_alias_prefix(text, context.registry):
        _raise_worktree_alias_compile(f"{source} references an unresolved worktree alias", path=text)
    if text.startswith("~") or _is_absolute_like(text):
        _raise_compile("contract_invalid_path", f"{source} must be repo-relative POSIX, got {raw_path!r}", path=text)
    if _windows_drive_path(text):
        _raise_compile("contract_invalid_path", f"{source} must be repo-relative POSIX, got {raw_path!r}", path=text)

    for other in context.registry.repos:
        if other.repo_id == context.repo.repo_id:
            continue
        for prefix in _repo_prefixes(other, ""):
            if _path_prefix_matches(text, [prefix]):
                _raise_compile(
                    "contract_invalid_path",
                    f"{source} points at repo {other.repo_id}, not {context.repo.repo_id}",
                    path=text,
                )

    stripped = _strip_repo_prefix(text, context)
    explicit_directory = stripped.endswith("/") or match_kind == "directory"
    parts = stripped.strip("/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        _raise_compile("contract_invalid_path", f"{source} contains invalid path segments", path=text)
    normalized = "/".join(parts)
    resolved_kind: PathMatchKind = "directory" if explicit_directory else "file"
    if match_kind == "file" and explicit_directory:
        _raise_compile("contract_invalid_path", f"{source} declares file path with directory suffix", path=text)
    if resolved_kind == "directory":
        normalized = f"{normalized}/"

    _reject_symlink_escape(context.repo, normalized, source=source)
    return normalized, resolved_kind


def _resolve_existing_file_scope_alias(
    normalized_path: str,
    match_kind: PathMatchKind,
    context: _PathContext,
    *,
    source: str,
) -> str:
    if match_kind != "file" or not context.repo.canonical_path:
        return normalized_path
    root = Path(context.repo.canonical_path)
    if not root.exists():
        return normalized_path
    if (root / normalized_path).exists():
        return normalized_path

    package_roots = _existing_package_roots(root, context.repo)
    candidates: list[str] = []
    for package_root in package_roots:
        candidate = package_root / normalized_path
        if candidate.is_file():
            try:
                candidates.append(candidate.relative_to(root).as_posix())
            except ValueError:
                continue
    candidates = sorted(dict.fromkeys(candidates))
    if not candidates:
        return normalized_path
    if len(candidates) > 1:
        _raise_compile(
            "contract_invalid_path",
            f"{source} has ambiguous existing path aliases for {normalized_path}: "
            + ", ".join(candidates[:10]),
            path=normalized_path,
        )
    aliased = candidates[0]
    _reject_symlink_escape(context.repo, aliased, source=source)
    return aliased


def _existing_package_roots(root: Path, repo: RepoIdentity) -> list[Path]:
    names: list[str] = []
    for raw in (
        repo.repo_name,
        Path(repo.workspace_relative_path).name if repo.workspace_relative_path else "",
        root.name,
    ):
        text = str(raw or "").strip()
        if not text:
            continue
        names.append(text)
        names.append(text.replace("-", "_"))
    candidates: list[Path] = []
    for name in dict.fromkeys(names):
        for base in (root, root / "src"):
            candidate = base / name
            if candidate.is_dir() and (candidate / "__init__.py").exists():
                candidates.append(candidate)
    try:
        for child in root.iterdir():
            if child.is_dir() and (child / "__init__.py").exists():
                candidates.append(child)
        src = root / "src"
        if src.is_dir():
            for child in src.iterdir():
                if child.is_dir() and (child / "__init__.py").exists():
                    candidates.append(child)
    except OSError:
        return []
    deduped: dict[str, Path] = {}
    for candidate in candidates:
        try:
            key = candidate.resolve(strict=False).as_posix()
        except OSError:
            key = candidate.as_posix()
        deduped[key] = candidate
    return [deduped[key] for key in sorted(deduped)]


def _normalize_patch_paths(
    contract: TaskDeliverableContract,
    patch: PatchSummary,
    snapshot: WorkspaceSnapshot,
    violations: list[dict[str, str]],
) -> _NormalizedPatch:
    normalized = _NormalizedPatch()

    def normalize(raw_path: str, operation: str) -> str | None:
        try:
            return _normalize_observed_path(raw_path, contract, snapshot)
        except ValueError as exc:
            _add_violation(
                violations,
                code="outside_allowed_paths",
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path=str(raw_path),
                operation=operation,
                detail=str(exc),
            )
            return None

    for raw in patch.created_paths:
        path = normalize(raw, "create")
        if path:
            normalized.created.append(("create", path))
    for raw in patch.modified_paths:
        path = normalize(raw, "modify")
        if path:
            normalized.modified.append(("modify", path))
    for raw in patch.deleted_paths:
        path = normalize(raw, "delete")
        if path:
            normalized.deleted.append(("delete", path))
    for raw_old, raw_new in patch.renamed_paths.items():
        old_path = normalize(raw_old, "rename_from")
        new_path = normalize(raw_new, "rename_to")
        if old_path and new_path:
            normalized.renamed.append((old_path, new_path))
    return normalized


def _normalize_observed_path(
    raw_path: str,
    contract: TaskDeliverableContract,
    snapshot: WorkspaceSnapshot,
) -> str:
    text = str(raw_path or "").strip().replace("\\", "/")
    if not text:
        raise ValueError("empty path")
    if "\x00" in text:
        raise ValueError("NUL byte in path")
    if text.startswith("~") or _is_absolute_like(text) or _windows_drive_path(text):
        raise ValueError("absolute paths are outside contract authority")
    text = _strip_display_repo_prefix(text, contract.repo_path)
    parts = text.strip("/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("path traversal or invalid segments")
    normalized = "/".join(parts)
    if snapshot.canonical_path:
        root = Path(snapshot.canonical_path)
        target = root / normalized
        try:
            resolved = target.resolve(strict=False)
            root_resolved = root.resolve(strict=False)
            resolved.relative_to(root_resolved)
        except (OSError, ValueError) as exc:
            raise ValueError("path resolves outside canonical repo root") from exc
    return normalized


def _manifest_entries(items: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(items, dict):
        if key in items:
            items = items.get(key)
        elif "path" in items:
            items = [items]
        else:
            items = []
    return [_entry_dict(item, default_key="path") for item in list(items or [])]


def _request_manifest_entries(request: ContractCompileRequest, key: str) -> list[dict[str, Any]]:
    explicit = (
        request.manifest_expected_files
        if key == "expected_files"
        else request.manifest_forbidden_files
    )
    entries = _manifest_entries(explicit, key)
    for extra_key in ("manifest_entries", "manifest"):
        extra_manifest = _extra_attr(request, extra_key, None)
        if extra_manifest:
            entries.extend(_manifest_entries(extra_manifest, key))
    return entries


def _request_has_absence_evidence(
    request: ContractCompileRequest,
    path: str,
    context: _PathContext,
) -> bool:
    task_id = str(_task_attr(request.task, "id", ""))
    for raw_gate in _task_scoped_items(request.verification_gates, task_id):
        gate = _entry_dict(raw_gate)
        for raw_evidence in list(gate.get("required_evidence") or []):
            evidence = _entry_dict(raw_evidence)
            if str(evidence.get("kind") or "") != "path_absent":
                continue
            raw_path = str(evidence.get("path") or "")
            if not raw_path:
                continue
            try:
                evidence_path, _match_kind = _normalize_contract_path(
                    raw_path,
                    context,
                    source="verification_gates.required_evidence.path",
                    match_kind=_entry_match_kind(evidence),
                )
            except ContractCompileError:
                continue
            if _same_path(evidence_path, path, "case_sensitive"):
                return True
    return False


def _entry_dict(item: Any, default_key: str | None = None) -> dict[str, Any]:
    if isinstance(item, BaseModel):
        return item.model_dump(mode="json")
    if isinstance(item, dict):
        return dict(item)
    if default_key is not None:
        return {default_key: str(item)}
    return {"value": item}


def _entry_match_kind(entry: dict[str, Any]) -> PathMatchKind | None:
    raw = str(entry.get("match_kind") or entry.get("path_kind") or entry.get("kind") or entry.get("type") or "")
    raw = raw.strip().lower()
    if raw in {"directory", "dir"}:
        return "directory"
    if raw == "file":
        return "file"
    path = str(entry.get("path") or "")
    return "directory" if path.strip().endswith("/") else None


def _declared_match_kind(item: Any) -> PathMatchKind | None:
    raw = str(_item_attr(item, "match_kind", "") or _item_attr(item, "path_kind", "") or _item_attr(item, "kind", ""))
    raw = raw.strip().lower()
    if raw in {"directory", "dir"}:
        return "directory"
    if raw == "file":
        return "file"
    path = str(_item_attr(item, "path", "") or "")
    return "directory" if path.strip().endswith("/") else None


def _entry_references_task(entry: dict[str, Any], task_id: str, lineage: Sequence[str]) -> bool:
    task_ids = _string_list(entry.get("task_ids") or entry.get("tasks") or [])
    if str(entry.get("task_id") or ""):
        task_ids.append(str(entry.get("task_id")))
    if task_id in task_ids:
        return True
    text_fields = [
        str(entry.get("source") or ""),
        str(entry.get("source_artifact") or ""),
        str(entry.get("source_artifact_key") or ""),
        str(entry.get("source_artifact_id") or ""),
    ]
    needles = [task_id, *[str(item) for item in lineage]]
    return any(needle and needle in text for needle in needles for text in text_fields)


def _task_scoped_items(items: Any, task_id: str) -> list[Any]:
    if isinstance(items, dict):
        scoped = items.get(task_id, [])
        if "path" in items or "id" in items:
            scoped = [items]
        return list(scoped or [])
    result = []
    for item in list(items or []):
        entry = _entry_dict(item, default_key="path")
        item_task_id = str(entry.get("task_id") or "")
        if item_task_id and item_task_id != task_id:
            continue
        task_ids = _string_list(entry.get("task_ids") or [])
        if task_ids and task_id not in task_ids:
            continue
        result.append(item)
    return result


def _evidence_spec(
    *,
    kind: EvidenceRequirementKind,
    repo_id: str,
    path: str,
    criterion_ids: Sequence[str],
    source: str,
    required: bool,
) -> RequiredEvidenceSpec:
    criterion_ids = sorted(dict.fromkeys(criterion_ids))
    digest = stable_digest(
        {
            "kind": kind,
            "repo_id": repo_id,
            "path": path,
            "criterion_ids": criterion_ids,
            "source": source,
            "required": required,
        }
    )
    return RequiredEvidenceSpec(
        id=f"evidence:{kind}:{repo_id}:{_path_token(path)}:{digest[:10]}",
        kind=kind,
        repo_id=repo_id,
        path=path,
        criterion_ids=list(criterion_ids),
        evidence_node_kind=kind,
        required=required,
    )


def _fail_on_same_contract_conflicts(
    task_id: str,
    required_paths: Sequence[ContractPathRule],
    allowed_paths: Sequence[ContractPathRule],
    read_only_paths: Sequence[ContractPathRule],
    forbidden_paths: Sequence[ContractPathRule],
    generated_outputs: Sequence[ContractPathRule],
) -> None:
    for read_only in read_only_paths:
        for writable in allowed_paths:
            if _rules_intersect(read_only, writable):
                _raise_compile(
                    "contract_scope_conflict",
                    f"{task_id} read-only path {read_only.path} intersects writable rule {writable.path}",
                    path=read_only.path,
                )
    for forbidden in forbidden_paths:
        for rule in [*required_paths, *allowed_paths, *read_only_paths, *generated_outputs]:
            if _rules_intersect(forbidden, rule):
                _raise_compile(
                    "contract_scope_conflict",
                    f"{task_id} path {rule.path} intersects forbidden rule {forbidden.path}",
                    path=rule.path,
                )


def _fail_on_group_conflicts(
    contracts: Sequence[TaskDeliverableContract],
    *,
    task_waves: dict[str, int],
) -> None:
    for left_idx, left in enumerate(contracts):
        for right in contracts[left_idx + 1 :]:
            if not left.unknown_write_set and not right.unknown_write_set:
                for left_allowed in left.allowed_paths:
                    for right_allowed in right.allowed_paths:
                        if _rules_intersect(left_allowed, right_allowed):
                            _raise_compile(
                                "contract_scope_conflict",
                                f"{left.task_id} and {right.task_id} have overlapping writable rules",
                                path=left_allowed.path,
                            )
            _fail_cross_forbidden(left, right)
            _fail_cross_forbidden(right, left)
            _fail_cross_read_write(left, right, task_waves)
            _fail_cross_read_write(right, left, task_waves)


def _fail_cross_forbidden(
    owner: TaskDeliverableContract,
    other: TaskDeliverableContract,
) -> None:
    for forbidden in owner.forbidden_paths:
        for rule in [*other.required_paths, *other.allowed_paths, *other.generated_outputs]:
            if _rules_intersect(forbidden, rule):
                _raise_compile(
                    "contract_scope_conflict",
                    f"{owner.task_id} forbids {rule.path} required or writable by {other.task_id}",
                    path=rule.path,
                )


def _fail_cross_read_write(
    reader: TaskDeliverableContract,
    writer: TaskDeliverableContract,
    task_waves: dict[str, int],
) -> None:
    for read_only in reader.read_only_paths:
        for writable in writer.allowed_paths:
            if not _rules_intersect(read_only, writable):
                continue
            reader_wave = task_waves.get(reader.task_id, 0)
            writer_wave = task_waves.get(writer.task_id, 0)
            if writer.task_id in reader.dependency_task_ids and reader_wave > writer_wave:
                continue
            _raise_compile(
                "contract_scope_conflict",
                f"{reader.task_id} reads {read_only.path} while {writer.task_id} writes it in the same group wave",
                path=read_only.path,
            )


def _execution_policy_for_unknown(unknown_write_set: bool) -> ContractExecutionPolicy:
    if unknown_write_set:
        return ContractExecutionPolicy(
            write_set_mode="unknown_isolated",
            sandbox_isolation="per_task",
            merge_admission="single_task",
        )
    return ContractExecutionPolicy(
        write_set_mode="declared",
        sandbox_isolation="group_shared",
        merge_admission="atomic_group",
    )


def _path_intent_for_action(action: str) -> PathIntent:
    mapping: dict[str, PathIntent] = {
        "create": "create",
        "modify": "modify",
        "delete": "delete",
        "read_only": "read_only",
    }
    return mapping.get(action, "unknown")


def _contract_material(
    *,
    feature_id: str,
    dag_sha256: str,
    source_dag_artifact_id: int,
    source_dag_sha256: str,
    group_idx: int,
    task: Any,
    task_id: str,
    repo_id: str,
    repo_path: str,
    required_paths: Sequence[ContractPathRule],
    allowed_paths: Sequence[ContractPathRule],
    read_only_paths: Sequence[ContractPathRule],
    forbidden_paths: Sequence[ContractPathRule],
    generated_outputs: Sequence[ContractPathRule],
    acceptance_criteria: Sequence[AcceptanceCriterionSpec],
    verification_gates: Sequence[VerificationGateSpec],
    execution_policy: ContractExecutionPolicy,
    non_goals: Sequence[str],
    dependency_task_ids: Sequence[str],
    unknown_write_set: bool,
    compile_warnings: Sequence[str],
) -> dict[str, JsonValue]:
    return {
        "schema": "task-deliverable-contract-v1",
        "feature_id": feature_id,
        "dag_sha256": dag_sha256,
        "source_dag_artifact_id": source_dag_artifact_id,
        "source_dag_sha256": source_dag_sha256,
        "group_idx": group_idx,
        "task_id": task_id,
        "task_description": str(_task_attr(task, "description", "") or ""),
        "repo_id": repo_id,
        "repo_path": repo_path,
        "required_paths": [_model_dump(rule) for rule in _sort_rules(required_paths)],
        "allowed_paths": [_model_dump(rule) for rule in _sort_rules(allowed_paths)],
        "read_only_paths": [_model_dump(rule) for rule in _sort_rules(read_only_paths)],
        "forbidden_paths": [_model_dump(rule) for rule in _sort_rules(forbidden_paths)],
        "generated_outputs": [_model_dump(rule) for rule in _sort_rules(generated_outputs)],
        "acceptance_criteria": sorted(
            (_model_dump(criterion) for criterion in acceptance_criteria),
            key=lambda item: (int(item.get("source_ordinal", 0)), str(item.get("id", ""))),
        ),
        "verification_gates": sorted(
            (_model_dump(gate) for gate in verification_gates),
            key=lambda item: str(item.get("id", "")),
        ),
        "execution_policy": _model_dump(execution_policy),
        "non_goals": sorted(dict.fromkeys(non_goals)),
        "dependency_task_ids": sorted(dict.fromkeys(dependency_task_ids)),
        "unknown_write_set": unknown_write_set,
        "compile_warnings": sorted(dict.fromkeys(compile_warnings)),
    }


def _validate_patch_digest(patch: PatchSummary, violations: list[dict[str, str]]) -> None:
    observed = (
        _extra_attr(patch, "diff_artifact_sha256", None)
        or _extra_attr(patch, "captured_diff_sha256", None)
        or _extra_attr(patch, "artifact_sha256", None)
    )
    if observed and str(observed) != patch.diff_sha256:
        _add_violation(
            violations,
            code="payload_digest_mismatch",
            failure_class="evidence_corruption",
            failure_type="payload_digest_mismatch",
            route="quiesce",
            path="",
            detail="patch diff artifact digest does not match patch summary diff_sha256",
        )


def _validate_required_presence(
    contract: TaskDeliverableContract,
    present_paths: set[str],
    violations: list[dict[str, str]],
    *,
    generated: bool,
) -> None:
    rules = contract.generated_outputs if generated else contract.required_paths
    for rule in rules:
        if generated and not rule.required and _has_absence_evidence(contract, rule.path):
            continue
        if not generated and rule.allow_delete and _has_absence_evidence(contract, rule.path):
            continue
        if _any_path_matches_rule(present_paths, rule):
            continue
        if _has_absence_evidence(contract, rule.path):
            continue
        _add_violation(
            violations,
            code="generated_output_missing" if generated else "required_path_missing",
            failure_class="product_defect",
            failure_type="required_path_missing",
            route="run_product_repair",
            path=rule.path,
            rule=rule.path,
        )


def _has_absence_evidence(contract: TaskDeliverableContract, path: str) -> bool:
    for gate in contract.verification_gates:
        for evidence in gate.required_evidence:
            if evidence.kind == "path_absent" and _same_path(evidence.path or "", path, "case_sensitive"):
                return True
    return False


def _virtual_presence_after_patch(
    contract: TaskDeliverableContract,
    patch: PatchSummary,
    snapshot: WorkspaceSnapshot,
    normalized_patch: _NormalizedPatch,
) -> set[str]:
    del contract, patch
    present = _snapshot_present_paths(snapshot)
    for _, path in normalized_patch.created:
        present.add(path)
    for _, path in normalized_patch.modified:
        present.add(path)
    for _, path in normalized_patch.deleted:
        present.discard(path)
    for old_path, new_path in normalized_patch.renamed:
        present.discard(old_path)
        present.add(new_path)
    return present


def _snapshot_present_paths(snapshot: WorkspaceSnapshot) -> set[str]:
    present: set[str] = set()
    for field_name in (
        "present_paths",
        "tracked_paths",
        "all_paths",
        "existing_paths",
        "dirty_paths",
        "staged_paths",
        "untracked_paths",
        "agent_writable_paths",
    ):
        for path in _string_list(_extra_attr(snapshot, field_name, [])):
            normalized = _normalize_snapshot_path(path, snapshot)
            if normalized:
                present.add(normalized)
    path_exists = _extra_attr(snapshot, "path_exists", {})
    if isinstance(path_exists, dict):
        for path, exists in path_exists.items():
            if exists:
                normalized = _normalize_snapshot_path(str(path), snapshot)
                if normalized:
                    present.add(normalized)
    if snapshot.canonical_path:
        root = Path(snapshot.canonical_path)
        if root.exists():
            for rule_path in _string_list(_extra_attr(snapshot, "probe_paths", [])):
                candidate = _normalize_snapshot_path(rule_path, snapshot)
                if candidate and (root / candidate.rstrip("/")).exists():
                    present.add(candidate)
    for path in _string_list(snapshot.forbidden_paths):
        normalized = _normalize_snapshot_path(path, snapshot)
        if normalized:
            present.add(normalized)
    return present


def _snapshot_base_present_paths(snapshot: WorkspaceSnapshot) -> set[str]:
    present: set[str] = set()
    for field_name in (
        "present_paths",
        "tracked_paths",
        "all_paths",
        "existing_paths",
    ):
        for path in _string_list(_extra_attr(snapshot, field_name, [])):
            normalized = _normalize_snapshot_path(path, snapshot)
            if normalized:
                present.add(normalized)
    path_exists = _extra_attr(snapshot, "path_exists", {})
    if isinstance(path_exists, dict):
        for path, exists in path_exists.items():
            if exists:
                normalized = _normalize_snapshot_path(str(path), snapshot)
                if normalized:
                    present.add(normalized)
    if snapshot.canonical_path:
        root = Path(snapshot.canonical_path)
        if root.exists():
            for rule_path in _string_list(_extra_attr(snapshot, "probe_paths", [])):
                candidate = _normalize_snapshot_path(rule_path, snapshot)
                if candidate and (root / candidate.rstrip("/")).exists():
                    present.add(candidate)
    return present


def _path_present_exact(
    present_paths: set[str],
    path: str,
    *,
    case_sensitivity: CaseSensitivity,
) -> bool:
    return any(_same_path(existing, path, case_sensitivity) for existing in present_paths)


def _normalize_snapshot_path(path: str, snapshot: WorkspaceSnapshot) -> str | None:
    text = str(path or "").strip().replace("\\", "/")
    if not text:
        return None
    if snapshot.canonical_path and _is_absolute_like(text):
        try:
            rel = Path(text).resolve(strict=False).relative_to(Path(snapshot.canonical_path).resolve(strict=False))
        except (OSError, ValueError):
            return None
        text = rel.as_posix()
    parts = text.strip("/").split("/")
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return "/".join(parts)


def _contract_requires_mutation(contract: TaskDeliverableContract) -> bool:
    return bool(contract.required_paths or contract.generated_outputs or contract.allowed_paths)


def _validate_patch_case_collisions(
    patch: _NormalizedPatch,
    case_sensitivity: CaseSensitivity,
    violations: list[dict[str, str]],
) -> None:
    if case_sensitivity != "unknown":
        return
    seen: dict[str, str] = {}
    for path in patch.all_paths:
        key = path.lower()
        prior = seen.get(key)
        if prior is not None and prior != path:
            _add_violation(
                violations,
                code="case_collision_variant",
                failure_class="contract_violation",
                failure_type="outside_allowed_paths",
                route="run_product_repair",
                path=path,
                detail=f"{path} conflicts with case variant {prior}",
            )
        seen[key] = path


def _fail_on_case_collisions(
    rules: Sequence[ContractPathRule],
    *,
    case_sensitivity: CaseSensitivity,
) -> None:
    if case_sensitivity != "unknown":
        return
    seen: dict[tuple[str, str, str], ContractPathRule] = {}
    for rule in rules:
        key = (rule.repo_id, rule.match_kind, rule.path.lower())
        prior = seen.get(key)
        if prior is not None and prior.path != rule.path:
            _raise_compile(
                "contract_invalid_path",
                f"case-collision variant {rule.path} conflicts with {prior.path}",
                path=rule.path,
            )
        seen[key] = rule


def _case_variant_rule(rules: Sequence[ContractPathRule], path: str) -> ContractPathRule | None:
    for rule in rules:
        if _rule_matches_path(rule, path, case_sensitivity="case_sensitive"):
            continue
        if _rule_matches_path(rule, path, case_sensitivity="case_insensitive"):
            return rule
    return None


def _first_matching_rule(
    rules: Sequence[ContractPathRule],
    path: str,
    *,
    case_sensitivity: CaseSensitivity,
) -> ContractPathRule | None:
    return next(
        (rule for rule in rules if _rule_matches_path(rule, path, case_sensitivity=case_sensitivity)),
        None,
    )


def _path_matches_any(
    path: str,
    rules: Sequence[ContractPathRule],
    *,
    case_sensitivity: CaseSensitivity,
) -> bool:
    return any(_rule_matches_path(rule, path, case_sensitivity=case_sensitivity) for rule in rules)


def _any_path_matches_rule(paths: Iterable[str], rule: ContractPathRule) -> bool:
    return any(_rule_matches_path(rule, path, case_sensitivity="case_sensitive") for path in paths)


def _rule_matches_path(
    rule: ContractPathRule,
    path: str,
    *,
    case_sensitivity: CaseSensitivity,
) -> bool:
    rule_path = _path_compare_value(rule.path, case_sensitivity)
    candidate = _path_compare_value(path, case_sensitivity)
    if rule.match_kind == "file":
        return candidate == rule_path.rstrip("/")
    directory = rule_path.rstrip("/")
    return candidate == directory or candidate.startswith(f"{directory}/")


def _rules_intersect(left: ContractPathRule, right: ContractPathRule) -> bool:
    if left.repo_id != right.repo_id:
        return False
    return _rule_matches_path(left, right.path.rstrip("/"), case_sensitivity="case_insensitive") or _rule_matches_path(
        right, left.path.rstrip("/"), case_sensitivity="case_insensitive"
    )


def _same_path(left: str, right: str, case_sensitivity: CaseSensitivity) -> bool:
    return _path_compare_value(left.rstrip("/"), case_sensitivity) == _path_compare_value(
        right.rstrip("/"),
        case_sensitivity,
    )


def _path_compare_value(path: str, case_sensitivity: CaseSensitivity) -> str:
    value = str(path).rstrip("/")
    if case_sensitivity == "case_insensitive":
        return value.lower()
    return value


def _merge_rules(rules: Sequence[ContractPathRule]) -> list[ContractPathRule]:
    merged: dict[tuple[str, str, str], ContractPathRule] = {}
    for rule in rules:
        key = (rule.repo_id, rule.path, rule.match_kind)
        existing = merged.get(key)
        if existing is None:
            merged[key] = rule
            continue
        updates = {
            "required": existing.required or rule.required,
            "allow_create": existing.allow_create or rule.allow_create,
            "allow_modify": existing.allow_modify or rule.allow_modify,
            "allow_delete": existing.allow_delete or rule.allow_delete,
            "source": ",".join(sorted(dict.fromkeys([existing.source, rule.source]))),
            "intent": _merged_intent(existing.intent, rule.intent),
        }
        merged[key] = existing.model_copy(update=updates)
    return list(merged.values())


def _merged_intent(left: PathIntent, right: PathIntent) -> PathIntent:
    order: list[PathIntent] = ["delete", "generated", "create", "modify", "read_only", "unknown"]
    return min([left, right], key=order.index)


def _sort_rules(rules: Sequence[ContractPathRule]) -> list[ContractPathRule]:
    return sorted(
        list(rules),
        key=lambda rule: (
            rule.repo_id,
            rule.path.lower(),
            rule.path,
            rule.match_kind,
            rule.intent,
            rule.source,
        ),
    )


def _normalize_dependencies(
    dependencies: Sequence[Any],
    *,
    all_task_ids: Sequence[str],
    task_id: str,
) -> list[str]:
    known = set(all_task_ids)
    result: list[str] = []
    for dependency in dependencies:
        dep_id = str(dependency)
        if dep_id == task_id:
            _raise_compile("contract_dependency_cycle", f"{task_id} depends on itself")
        if known and dep_id not in known:
            _raise_compile("contract_unknown_dependency", f"{task_id} depends on unknown task {dep_id}")
        result.append(dep_id)
    return sorted(dict.fromkeys(result))


def _compile_non_goals(task: Any) -> list[str]:
    values: list[str] = []
    for field_name in ("non_goals", "not_criteria"):
        values.extend(_string_list(_task_attr(task, field_name, []) or []))
    for criterion in list(_task_attr(task, "acceptance_criteria", []) or []):
        values.extend(_string_list(_item_attr(criterion, "not_criteria", "") or []))
    return sorted(dict.fromkeys(" ".join(value.split()) for value in values if str(value).strip()))


def _repo_by_id(registry: CanonicalRepoRegistry, repo_id: str) -> RepoIdentity | None:
    return next((repo for repo in registry.repos if repo.repo_id == repo_id), None)


def _repo_prefixes(repo: RepoIdentity, display_repo_path: str) -> list[str]:
    prefixes = [
        display_repo_path,
        repo.workspace_relative_path,
        repo.repo_name,
        Path(repo.canonical_path).name if repo.canonical_path else "",
    ]
    return sorted(
        {
            prefix.strip("/").replace("\\", "/")
            for prefix in prefixes
            if prefix and prefix.strip("/") not in {"."}
        },
        key=lambda item: (-len(item), item),
    )


def _strip_repo_prefix(text: str, context: _PathContext) -> str:
    return _strip_display_repo_prefix(text, *_repo_prefixes(context.repo, context.display_repo_path))


def _strip_display_repo_prefix(text: str, *prefixes: str) -> str:
    candidate = text.strip("/")
    for prefix in sorted({prefix.strip("/") for prefix in prefixes if prefix}, key=lambda item: (-len(item), item)):
        if candidate == prefix:
            return ""
        if candidate.startswith(f"{prefix}/"):
            return candidate[len(prefix) + 1 :]
    return candidate


def _path_prefix_matches(path: str, prefixes: Sequence[str]) -> bool:
    candidate = path.strip("/").replace("\\", "/")
    return any(candidate == prefix.strip("/") or candidate.startswith(f"{prefix.strip('/')}/") for prefix in prefixes if prefix)


def _matching_alias_prefix(path: str, registry: CanonicalRepoRegistry) -> str | None:
    candidate = path.strip("/").replace("\\", "/")
    feature_root = Path(registry.feature_root).resolve(strict=False) if registry.feature_root else None
    for alias_raw in registry.aliases:
        aliases = [str(alias_raw).strip("/").replace("\\", "/")]
        alias_path = Path(str(alias_raw)).expanduser()
        if feature_root and alias_path.is_absolute():
            try:
                aliases.append(alias_path.resolve(strict=False).relative_to(feature_root).as_posix())
            except (OSError, ValueError):
                pass
        for alias in aliases:
            if alias and (candidate == alias or candidate.startswith(f"{alias}/")):
                return alias
    return None


def _reject_symlink_escape(repo: RepoIdentity, normalized_path: str, *, source: str) -> None:
    if not repo.canonical_path:
        return
    root = Path(repo.canonical_path)
    target = root / normalized_path.rstrip("/")
    try:
        root_resolved = root.resolve(strict=False)
        current = root
        for part in normalized_path.rstrip("/").split("/"):
            current = current / part
            if current.is_symlink():
                resolved = current.resolve(strict=False)
                try:
                    resolved.relative_to(root_resolved)
                except ValueError:
                    _raise_compile(
                        "contract_invalid_path",
                        f"{source} crosses a symlink escape at {current}",
                        path=normalized_path,
                    )
        target.resolve(strict=False).relative_to(root_resolved)
    except OSError as exc:
        _raise_compile("contract_invalid_path", f"{source} cannot be resolved: {exc}", path=normalized_path)
    except ValueError:
        _raise_compile("contract_invalid_path", f"{source} resolves outside the canonical repo root", path=normalized_path)


def _is_absolute_like(path: str) -> bool:
    return path.startswith("/") or PurePosixPath(path).is_absolute()


def _windows_drive_path(path: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[/\\]", path))


def _slug_id(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_.:-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or f"id-{_short_digest(value)}"


def _normalized_criterion_ids(values: Any) -> list[str]:
    return sorted(dict.fromkeys(_slug_id(value) for value in _string_list(values)))


def _short_digest(value: Any, length: int = 10) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:length]


def _path_token(path: str) -> str:
    return _slug_id(path.replace("/", "-").replace(".", "-"))[:48]


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def _task_attr(task: Any, key: str, default: Any = None) -> Any:
    if isinstance(task, dict):
        return task.get(key, default)
    return getattr(task, key, default)


def _item_attr(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _extra_attr(model: Any, key: str, default: Any = None) -> Any:
    if isinstance(model, dict):
        return model.get(key, default)
    value = getattr(model, key, default)
    if value is not default:
        return value
    extra = getattr(model, "__pydantic_extra__", None) or {}
    return extra.get(key, default)


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, dict):
        return [str(item) for item in value.values() if str(item).strip()]
    if isinstance(value, Iterable):
        return [str(item) for item in value if str(item).strip()]
    return [str(value)] if str(value).strip() else []


def _int_list(value: Any) -> list[int]:
    result: list[int] = []
    for item in value or []:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _model_dump(value: Any) -> dict[str, JsonValue]:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return dict(value)
    return {"value": value}


def _add_violation(
    violations: list[dict[str, str]],
    *,
    code: str,
    failure_class: str,
    failure_type: str,
    route: str,
    path: str,
    **details: Any,
) -> None:
    row = {
        "code": code,
        "failure_class": failure_class,
        "failure_type": failure_type,
        "route": route,
        "path": str(path or ""),
    }
    row.update({key: str(value) for key, value in details.items() if value is not None})
    violations.append(row)


def _verdict(
    contract: TaskDeliverableContract,
    *,
    patch_summary_id: int | None,
    violations: Sequence[dict[str, str]],
    required_evidence_node_ids: Sequence[int],
) -> ContractVerdict:
    codes = sorted(dict.fromkeys(str(item.get("code", "")) for item in violations if item.get("code")))
    return ContractVerdict(
        contract_id=contract.id or 0,
        patch_summary_id=patch_summary_id or 0,
        approved=not bool(violations),
        violation_codes=codes,
        violations=list(violations),
        required_evidence_node_ids=list(required_evidence_node_ids),
    )


def _raise_compile(
    failure_type: str,
    message: str,
    *,
    path: str = "",
    warnings: Sequence[str] | None = None,
    failure_class: str = "contract_compile",
    route: str | None = None,
) -> None:
    route = route or ("quiesce" if failure_type == "contract_scope_conflict" else "run_contract_repair")
    raise ContractCompileError(
        failure_type,
        message,
        failure_class=failure_class,
        route=route,
        violations=[
            {
                "code": failure_type,
                "failure_class": failure_class,
                "failure_type": failure_type,
                "route": route,
                "path": path,
                "detail": message,
            }
        ],
        warnings=warnings,
    )


def _raise_worktree_alias_compile(message: str, *, path: str = "") -> None:
    _raise_compile(
        "contract_invalid_path",
        message,
        path=path,
        failure_class="worktree_alias",
        route="run_canonicalization_repair",
    )


# --- Slice-11c pure task-contract projection/key helpers --------------------
# Per docs/execution-control-plane/11-refactor-map.md § "Boundary-level API
# contracts" row for execution/task_contracts.py, this module owns
# "Required/forbidden path contracts, write-set authority, deliverable
# validation." The three helpers below are the pure task-contract
# projection-key + sanitization primitives moved byte-for-byte from
# workflows/develop/phases/implementation.py at :2483-2484, :2779-2780,
# :2783-2787. Every other task-contract helper in implementation.py has a
# named non-pure dependency (runner/feature/verdict-coupled orchestrators,
# the _model_json_dict-coupled cluster, the _workspace_authority_jsonable-
# coupled cluster, or the _dedupe_preserving_order-coupled cluster) and is
# deferred to a later slice per the Slice-11c BEFORE-chunk journal entry.
def _task_contract_projection_key(task_id: str) -> str:
    return f"dag-task-contract:{task_id}"


def _safe_contract_key_fragment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-") or "unknown"


def _contract_stage_sandbox_id(group_idx: int, stage: str, repo_id: str) -> str:
    return (
        f"canonical-precommit-g{group_idx}-{_safe_contract_key_fragment(stage)}-"
        f"repo-{_safe_contract_key_fragment(repo_id)}"
    )


__all__ = [
    "AcceptanceCriterionSpec",
    "ContractCompileError",
    "ContractCompileRequest",
    "ContractCompiler",
    "ContractExecutionPolicy",
    "ContractGroupCompileRequest",
    "ContractPathRule",
    "ContractVerdict",
    "GateCommandSpec",
    "PatchSummary",
    "RequiredEvidenceSpec",
    "TaskDeliverableContract",
    "VerificationGateSpec",
    "JsonValue",
    "PathIntent",
    "PathMatchKind",
    "GateKind",
    "EvidenceRequirementKind",
    "WriteSetMode",
    "SandboxIsolationMode",
    "MergeAdmissionMode",
    "_contract_stage_sandbox_id",
    "_safe_contract_key_fragment",
    "_task_contract_projection_key",
]
