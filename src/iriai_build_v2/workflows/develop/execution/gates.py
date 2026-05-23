"""Deterministic gate primitives for verification graph construction.

This module is intentionally store-agnostic.  It validates explicit gate inputs,
records idempotent evidence nodes through a small recorder interface, and builds
bounded verifier context packages through an explicit read gateway.  Persistence
and provider dispatch are integration concerns for later slices.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Literal, Protocol, Sequence, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iriai_build_v2.workflows.develop.execution.workspace_authority import (
    stable_digest,
)
from iriai_build_v2.models.outputs import (
    ImplementationResult,
    Issue,
    Verdict,
)


JsonValue: TypeAlias = str | int | float | bool | None | dict[str, Any] | list[Any]

EvidenceRefKind = Literal["artifact", "event", "contract", "snapshot", "patch", "commit"]
BoundedSource = Literal["artifact", "event", "file", "diff", "contract", "snapshot"]
LookupKind = Literal["id", "exact_key", "bounded_feature", "file_slice"]
GateStatus = Literal["pending", "running", "approved", "rejected", "failed", "skipped"]


class _GateModel(BaseModel):
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True, from_attributes=True)


class GateValidationError(ValueError):
    """Deterministic gate failure with Slice 07-compatible routing metadata."""

    def __init__(
        self,
        local_code: str,
        failure_class: str,
        failure_type: str,
        message: str,
        *,
        details: dict[str, JsonValue] | None = None,
    ) -> None:
        super().__init__(message)
        self.local_code = local_code
        self.failure_class = failure_class
        self.failure_type = failure_type
        self.details = details or {}


class IdempotencyConflict(GateValidationError):
    """Raised when the same idempotency key is replayed with different inputs."""

    def __init__(self, idempotency_key: str, existing_hash: str, incoming_hash: str) -> None:
        super().__init__(
            "gate_request.invalid",
            "dispatcher_internal",
            "idempotency_conflict",
            f"idempotency key {idempotency_key!r} already has a different input hash",
            details={
                "idempotency_key": idempotency_key,
                "existing_hash": existing_hash,
                "incoming_hash": incoming_hash,
            },
        )


class EvidenceRef(_GateModel):
    kind: EvidenceRefKind
    id: int | str
    sha256: str | None = None
    projection_key: str | None = None


class GateRequest(_GateModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    contract_ids: list[int]
    verification_gate_ids: list[str]
    workspace_snapshot_ids: list[int]
    patch_summary_ids: list[int]
    task_attempt_ids: list[int]
    candidate_manifest_id: int
    idempotency_key: str

    @field_validator(
        "contract_ids",
        "workspace_snapshot_ids",
        "patch_summary_ids",
        "task_attempt_ids",
        mode="after",
    )
    @classmethod
    def _unique_integer_ids(cls, value: list[int]) -> list[int]:
        if not value:
            raise ValueError("required input id lists cannot be empty")
        if len(value) != len(set(value)):
            raise ValueError("input ids must be unique")
        return sorted(value)

    @field_validator("verification_gate_ids", mode="after")
    @classmethod
    def _unique_gate_ids(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("verification_gate_ids cannot be empty")
        stripped = [item.strip() for item in value]
        if any(not item for item in stripped):
            raise ValueError("verification_gate_ids cannot contain blanks")
        if len(stripped) != len(set(stripped)):
            raise ValueError("verification_gate_ids must be unique")
        return sorted(stripped)

    @model_validator(mode="after")
    def _required_scalars(self) -> "GateRequest":
        if not self.feature_id:
            raise ValueError("feature_id is required")
        if not self.dag_sha256:
            raise ValueError("dag_sha256 is required")
        if self.group_idx < 0:
            raise ValueError("group_idx must be non-negative")
        if not self.stage:
            raise ValueError("stage is required")
        if self.attempt < 0:
            raise ValueError("attempt must be non-negative")
        if self.candidate_manifest_id <= 0:
            raise ValueError("candidate_manifest_id must be positive")
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        return self


class CandidateManifest(_GateModel):
    id: int | None = None
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    contract_ids: list[int]
    workspace_snapshot_ids: list[int]
    patch_summary_ids: list[int]
    task_attempt_ids: list[int]
    merge_queue_item_id: int | None = None
    manifest_digest: str
    idempotency_key: str
    workspace_root: str | None = None
    base_commit: str | None = None
    workspace_snapshot_hashes: dict[int, str] = Field(default_factory=dict)

    @classmethod
    def from_request(
        cls,
        request: GateRequest,
        *,
        merge_queue_item_id: int | None = None,
        workspace_root: str | None = None,
        base_commit: str | None = None,
        workspace_snapshot_hashes: dict[int, str] | None = None,
    ) -> "CandidateManifest":
        material = {
            "feature_id": request.feature_id,
            "dag_sha256": request.dag_sha256,
            "group_idx": request.group_idx,
            "stage": request.stage,
            "attempt": request.attempt,
            "contract_ids": request.contract_ids,
            "workspace_snapshot_ids": request.workspace_snapshot_ids,
            "patch_summary_ids": request.patch_summary_ids,
            "task_attempt_ids": request.task_attempt_ids,
            "merge_queue_item_id": merge_queue_item_id,
            "workspace_root": workspace_root,
            "base_commit": base_commit,
            "workspace_snapshot_hashes": workspace_snapshot_hashes or {},
        }
        digest = stable_digest(material)
        return cls(
            id=request.candidate_manifest_id,
            feature_id=request.feature_id,
            dag_sha256=request.dag_sha256,
            group_idx=request.group_idx,
            stage=request.stage,
            attempt=request.attempt,
            contract_ids=list(request.contract_ids),
            workspace_snapshot_ids=list(request.workspace_snapshot_ids),
            patch_summary_ids=list(request.patch_summary_ids),
            task_attempt_ids=list(request.task_attempt_ids),
            merge_queue_item_id=merge_queue_item_id,
            manifest_digest=digest,
            idempotency_key=f"candidate-manifest:{digest}",
            workspace_root=workspace_root,
            base_commit=base_commit,
            workspace_snapshot_hashes=workspace_snapshot_hashes or {},
        )


class BoundedQuery(_GateModel):
    source: BoundedSource
    lookup_kind: LookupKind
    ids: list[int | str] = Field(default_factory=list)
    limit: int | None = None
    after_id: int | None = None
    event_types: list[str] = Field(default_factory=list)
    deterministic_order: str | None = None


class ReadBudgetReport(_GateModel):
    bounded_queries: list[BoundedQuery]
    artifact_count: int
    event_count: int
    file_count: int
    aggregate_bytes: int
    omitted_optional_refs: list[EvidenceRef] = Field(default_factory=list)
    omitted_required_refs: list[EvidenceRef] = Field(default_factory=list)
    blocked_unbounded_read_count: int = 0
    budget_digest: str

    @classmethod
    def build(
        cls,
        *,
        bounded_queries: Sequence[BoundedQuery],
        artifact_count: int,
        event_count: int,
        file_count: int,
        aggregate_bytes: int,
        omitted_optional_refs: Sequence[EvidenceRef] = (),
        omitted_required_refs: Sequence[EvidenceRef] = (),
        blocked_unbounded_read_count: int = 0,
    ) -> "ReadBudgetReport":
        material = {
            "bounded_queries": [query.model_dump(mode="json") for query in bounded_queries],
            "artifact_count": artifact_count,
            "event_count": event_count,
            "file_count": file_count,
            "aggregate_bytes": aggregate_bytes,
            "omitted_optional_refs": [ref.model_dump(mode="json") for ref in omitted_optional_refs],
            "omitted_required_refs": [ref.model_dump(mode="json") for ref in omitted_required_refs],
            "blocked_unbounded_read_count": blocked_unbounded_read_count,
        }
        return cls(
            bounded_queries=list(bounded_queries),
            artifact_count=artifact_count,
            event_count=event_count,
            file_count=file_count,
            aggregate_bytes=aggregate_bytes,
            omitted_optional_refs=list(omitted_optional_refs),
            omitted_required_refs=list(omitted_required_refs),
            blocked_unbounded_read_count=blocked_unbounded_read_count,
            budget_digest=stable_digest(material),
        )


class GateFailure(_GateModel):
    local_code: str
    failure_class: str
    failure_type: str
    message: str
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @classmethod
    def from_error(cls, error: GateValidationError) -> "GateFailure":
        return cls(
            local_code=error.local_code,
            failure_class=error.failure_class,
            failure_type=error.failure_type,
            message=str(error),
            details=error.details,
        )


class EvidenceNode(_GateModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    name: str
    kind: str
    status: GateStatus
    deterministic: bool = True
    idempotency_key: str
    input_hash: str
    output_hash: str | None = None
    input_refs: list[EvidenceRef] = Field(default_factory=list)
    output_refs: list[EvidenceRef] = Field(default_factory=list)
    failure: GateFailure | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class GateResult(_GateModel):
    gate_name: str
    approved: bool
    deterministic: bool
    evidence_node_id: int
    failure_id: int | None = None
    failure: GateFailure | None = None


class ContextReadRef(_GateModel):
    source: Literal["artifact", "event", "file", "diff"]
    id: int | str | None = None
    projection_key: str | None = None
    required: bool = True
    path: str | None = None
    start_line: int | None = None
    end_line: int | None = None
    reason: str = "verification_context"
    lookup_kind: LookupKind | None = None
    limit: int | None = None
    after_id: int | None = None
    event_types: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _infer_lookup_kind(self) -> "ContextReadRef":
        if self.lookup_kind is None:
            if self.source == "file":
                self.lookup_kind = "file_slice"
            elif self.projection_key:
                self.lookup_kind = "exact_key"
            else:
                self.lookup_kind = "id"
        return self

    def evidence_ref(self, sha256: str | None = None) -> EvidenceRef:
        if self.source == "diff":
            kind: EvidenceRefKind = "patch"
        elif self.source == "file":
            kind = "artifact"
        else:
            kind = self.source  # type: ignore[assignment]
        return EvidenceRef(
            kind=kind,
            id=self.id if self.id is not None else self.path or self.projection_key or "unbounded",
            sha256=sha256,
            projection_key=self.projection_key,
        )


class ContextPackage(_GateModel):
    approved: bool
    package_hash: str
    read_budget: ReadBudgetReport
    selected_refs: list[EvidenceRef] = Field(default_factory=list)
    failure: GateFailure | None = None
    payloads: list[dict[str, JsonValue]] = Field(default_factory=list)


class ContextBudget(_GateModel):
    max_artifacts_events: int = 200
    max_files: int = 80
    max_aggregate_bytes: int = 2 * 1024 * 1024
    max_file_slice_bytes: int = 20 * 1024
    max_file_slice_lines: int = 200


class GateWorkspaceSnapshot(_GateModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    root: str | None = None
    canonical_root: str | None = None
    base_commit: str | None = None
    snapshot_hash: str | None = None
    present_paths: list[str] = Field(default_factory=list)
    retired_aliases: list[str] = Field(default_factory=list)


class GateContractSnapshot(_GateModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    task_id: str
    status: str = "active"
    contract_digest: str | None = None
    dependency_task_ids: list[str] = Field(default_factory=list)
    known_task_ids: list[str] = Field(default_factory=list)
    all_task_ids: list[str] = Field(default_factory=list)
    acceptance_criteria: list[Any] = Field(default_factory=list)
    verification_gates: list[Any] = Field(default_factory=list)
    allowed_paths: list[Any] = Field(default_factory=list)
    required_paths: list[Any] = Field(default_factory=list)
    generated_outputs: list[Any] = Field(default_factory=list)
    forbidden_paths: list[Any] = Field(default_factory=list)


class GateTaskAttempt(_GateModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    attempt: int
    task_id: str
    superseded_by_id: int | None = None


class GatePatchSummary(_GateModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    attempt: int
    repo_id: str | None = None
    workspace_snapshot_id: int | None = None
    patch_id: int | str | None = None
    summary_sha256: str | None = None
    summary_hash: str | None = None
    actual_summary_sha256: str | None = None
    computed_summary_sha256: str | None = None
    touched_paths: list[str] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    created_paths: list[str] = Field(default_factory=list)
    modified_paths: list[str] = Field(default_factory=list)
    deleted_paths: list[str] = Field(default_factory=list)
    expected_touched_paths: list[str] = Field(default_factory=list)
    superseded_by_id: int | None = None


class GateReadGateway(Protocol):
    def get_workspace_snapshots_by_ids(self, ids: Sequence[int]) -> Sequence[Any]: ...

    def get_contracts_by_ids(self, ids: Sequence[int]) -> Sequence[Any]: ...

    def get_task_attempts_by_ids(self, ids: Sequence[int]) -> Sequence[Any]: ...

    def get_patch_summaries_by_ids(self, ids: Sequence[int]) -> Sequence[Any]: ...


class ContextReadGateway(Protocol):
    def get_artifacts_by_ids(self, ids: Sequence[int | str]) -> Sequence[Any]: ...

    def get_artifact_by_exact_key(self, key: str) -> Any | None: ...

    def get_events_by_ids(self, ids: Sequence[int | str]) -> Sequence[Any]: ...

    def get_events_by_feature(
        self,
        feature_id: int | str,
        *,
        event_types: Sequence[str],
        after_id: int,
        limit: int,
    ) -> Sequence[Any]: ...

    def get_file_slice(
        self,
        path: str,
        *,
        start_line: int,
        end_line: int,
        max_bytes: int,
    ) -> Any: ...


@dataclass
class InMemoryEvidenceRecorder:
    """Small idempotent recorder used by tests and future store adapters."""

    next_id: int = 1

    def __post_init__(self) -> None:
        self.nodes: list[EvidenceNode] = []
        self._by_key: dict[str, EvidenceNode] = {}

    def write_node(
        self,
        *,
        request: GateRequest,
        name: str,
        kind: str,
        input_hash: str,
        status: GateStatus,
        deterministic: bool = True,
        failure: GateFailure | None = None,
        input_refs: Sequence[EvidenceRef] = (),
        output_refs: Sequence[EvidenceRef] = (),
        metadata: dict[str, JsonValue] | None = None,
    ) -> EvidenceNode:
        key = gate_node_idempotency_key(request, name)
        existing = self._by_key.get(key)
        if existing is not None:
            if existing.input_hash != input_hash:
                raise IdempotencyConflict(key, existing.input_hash, input_hash)
            return existing
        node = EvidenceNode(
            id=self.next_id,
            feature_id=request.feature_id,
            dag_sha256=request.dag_sha256,
            group_idx=request.group_idx,
            stage=request.stage,
            attempt=request.attempt,
            name=name,
            kind=kind,
            status=status,
            deterministic=deterministic,
            idempotency_key=key,
            input_hash=input_hash,
            input_refs=list(input_refs),
            output_refs=list(output_refs),
            failure=failure,
            metadata=metadata or {},
        )
        self.next_id += 1
        self.nodes.append(node)
        self._by_key[key] = node
        return node


class ContextPackageBuilder:
    def __init__(self, gateway: ContextReadGateway, budget: ContextBudget | None = None) -> None:
        self.gateway = gateway
        self.budget = budget or ContextBudget()

    def build(self, refs: Sequence[ContextReadRef]) -> ContextPackage:
        queries: list[BoundedQuery] = []
        selected_refs: list[EvidenceRef] = []
        omitted_required: list[EvidenceRef] = []
        omitted_optional: list[EvidenceRef] = []
        payloads: list[dict[str, JsonValue]] = []
        artifact_count = 0
        event_count = 0
        file_count = 0
        aggregate_bytes = 0
        blocked_unbounded = 0

        for ref in refs:
            if self._is_unbounded(ref):
                blocked_unbounded += 1
                if ref.required:
                    omitted_required.append(ref.evidence_ref())
                else:
                    omitted_optional.append(ref.evidence_ref())
                continue

            if ref.source == "artifact":
                query, records = self._read_artifact(ref)
                queries.append(query)
                if not records:
                    _append_omitted(ref, None, omitted_required, omitted_optional)
                for record in records:
                    byte_count, digest = _record_bytes_and_digest(record)
                    if (
                        artifact_count + event_count + 1 > self.budget.max_artifacts_events
                        or not _budget_allows_item(
                            aggregate_bytes,
                            byte_count,
                            self.budget.max_aggregate_bytes,
                        )
                    ):
                        _append_omitted(ref, digest, omitted_required, omitted_optional)
                        continue
                    artifact_count += 1
                    aggregate_bytes += byte_count
                    selected_refs.append(ref.evidence_ref(digest))
                    payloads.append(_record_payload(record, "artifact", byte_count, digest))
            elif ref.source == "event":
                query, records = self._read_event(ref)
                queries.append(query)
                if not records:
                    _append_omitted(ref, None, omitted_required, omitted_optional)
                for record in records:
                    byte_count, digest = _record_bytes_and_digest(record)
                    if (
                        artifact_count + event_count + 1 > self.budget.max_artifacts_events
                        or not _budget_allows_item(
                            aggregate_bytes,
                            byte_count,
                            self.budget.max_aggregate_bytes,
                        )
                    ):
                        _append_omitted(ref, digest, omitted_required, omitted_optional)
                        continue
                    event_count += 1
                    aggregate_bytes += byte_count
                    selected_refs.append(ref.evidence_ref(digest))
                    payloads.append(_record_payload(record, "event", byte_count, digest))
            elif ref.source == "file":
                query, record = self._read_file(ref)
                queries.append(query)
                byte_count, digest = _record_bytes_and_digest(record)
                line_count = _line_count(record)
                if (
                    file_count + 1 > self.budget.max_files
                    or line_count > self.budget.max_file_slice_lines
                    or byte_count > self.budget.max_file_slice_bytes
                    or not _budget_allows_item(
                        aggregate_bytes,
                        byte_count,
                        self.budget.max_aggregate_bytes,
                    )
                ):
                    _append_omitted(ref, digest, omitted_required, omitted_optional)
                    continue
                file_count += 1
                aggregate_bytes += byte_count
                selected_refs.append(ref.evidence_ref(digest))
                payloads.append(_record_payload(record, "file", byte_count, digest))
            else:
                query = BoundedQuery(source="diff", lookup_kind="id", ids=[ref.id] if ref.id is not None else [])
                queries.append(query)
                byte_count = 0
                digest = stable_digest({"diff_ref": ref.model_dump(mode="json")})
                if not _budget_allows_item(
                    aggregate_bytes,
                    byte_count,
                    self.budget.max_aggregate_bytes,
                ):
                    _append_omitted(ref, digest, omitted_required, omitted_optional)
                    continue
                selected_refs.append(ref.evidence_ref(digest))
                payloads.append({"source": "diff", "id": ref.id, "sha256": digest, "byte_count": 0})

        report = ReadBudgetReport.build(
            bounded_queries=queries,
            artifact_count=artifact_count,
            event_count=event_count,
            file_count=file_count,
            aggregate_bytes=aggregate_bytes,
            omitted_optional_refs=omitted_optional,
            omitted_required_refs=omitted_required,
            blocked_unbounded_read_count=blocked_unbounded,
        )
        failure: GateFailure | None = None
        approved = not omitted_required and blocked_unbounded == 0
        if not approved:
            failure = GateFailure(
                local_code="context_package.insufficient",
                failure_class="verifier_context",
                failure_type="context_materialization_failed",
                message="bounded context package omitted required refs or attempted an unbounded read",
                details={
                    "omitted_required_refs": [ref.model_dump(mode="json") for ref in omitted_required],
                    "blocked_unbounded_read_count": blocked_unbounded,
                },
            )
        package_hash = stable_digest(
            {
                "approved": approved,
                "selected_refs": [ref.model_dump(mode="json") for ref in selected_refs],
                "read_budget": report.model_dump(mode="json"),
                "payloads": payloads,
            }
        )
        return ContextPackage(
            approved=approved,
            package_hash=package_hash,
            read_budget=report,
            selected_refs=selected_refs,
            failure=failure,
            payloads=payloads,
        )

    def _is_unbounded(self, ref: ContextReadRef) -> bool:
        if ref.lookup_kind == "bounded_feature":
            return (
                ref.id is None
                or ref.limit is None
                or ref.limit <= 0
                or not ref.event_types
            )
        if ref.lookup_kind == "file_slice":
            return not ref.path or ref.start_line is None or ref.end_line is None
        if ref.lookup_kind == "exact_key":
            return not ref.projection_key
        return ref.id is None

    def _read_artifact(self, ref: ContextReadRef) -> tuple[BoundedQuery, list[Any]]:
        if ref.lookup_kind == "exact_key":
            query = BoundedQuery(
                source="artifact",
                lookup_kind="exact_key",
                ids=[ref.projection_key or ""],
                limit=1,
                deterministic_order="projection_key",
            )
            record = self.gateway.get_artifact_by_exact_key(ref.projection_key or "")
            return query, [] if record is None else [record]
        query = BoundedQuery(
            source="artifact",
            lookup_kind="id",
            ids=[ref.id] if ref.id is not None else [],
            limit=1,
            deterministic_order="id",
        )
        return query, list(self.gateway.get_artifacts_by_ids(query.ids))

    def _read_event(self, ref: ContextReadRef) -> tuple[BoundedQuery, list[Any]]:
        if ref.lookup_kind == "bounded_feature":
            query = BoundedQuery(
                source="event",
                lookup_kind="bounded_feature",
                ids=[ref.id] if ref.id is not None else [],
                limit=ref.limit,
                after_id=ref.after_id or 0,
                event_types=ref.event_types,
                deterministic_order="feature_id,event_type,id",
            )
            return query, list(
                self.gateway.get_events_by_feature(
                    ref.id or "",
                    event_types=ref.event_types,
                    after_id=ref.after_id or 0,
                    limit=ref.limit or 0,
                )
            )
        query = BoundedQuery(
            source="event",
            lookup_kind="id",
            ids=[ref.id] if ref.id is not None else [],
            limit=1,
            event_types=ref.event_types,
            deterministic_order="id",
        )
        return query, list(self.gateway.get_events_by_ids(query.ids))

    def _read_file(self, ref: ContextReadRef) -> tuple[BoundedQuery, Any]:
        start_line = int(ref.start_line or 1)
        end_line = int(ref.end_line or start_line)
        query = BoundedQuery(
            source="file",
            lookup_kind="file_slice",
            ids=[ref.path or ""],
            limit=end_line - start_line + 1,
            deterministic_order="path,line",
        )
        record = self.gateway.get_file_slice(
            ref.path or "",
            start_line=start_line,
            end_line=end_line,
            max_bytes=self.budget.max_file_slice_bytes,
        )
        return query, record


class GateRunResult(_GateModel):
    approved: bool
    should_dispatch_verifier: bool
    candidate_manifest: CandidateManifest
    nodes: list[EvidenceNode]
    failure: GateFailure | None = None
    context_package: ContextPackage | None = None


class GateRunner:
    def __init__(
        self,
        gateway: GateReadGateway,
        *,
        recorder: InMemoryEvidenceRecorder | None = None,
        context_builder: ContextPackageBuilder | None = None,
    ) -> None:
        self.gateway = gateway
        self.recorder = recorder or InMemoryEvidenceRecorder()
        self.context_builder = context_builder

    def run_preflight(
        self,
        request: GateRequest,
        *,
        candidate_manifest: CandidateManifest | None = None,
        context_refs: Sequence[ContextReadRef] = (),
    ) -> GateRunResult:
        manifest = candidate_manifest or CandidateManifest.from_request(request)
        start_index = len(self.recorder.nodes)
        self._write_approved_node(
            request,
            "gate_request",
            {"request": _dump(request), "manifest": _dump(manifest)},
            input_refs=[EvidenceRef(kind="artifact", id=manifest.id or request.candidate_manifest_id, sha256=manifest.manifest_digest)],
        )

        try:
            snapshots = self._validate_workspace_snapshots(request, manifest)
            contracts = self._validate_contracts(request)
            task_attempts, patch_summaries = self._validate_artifacts(request)
            self._validate_path_scope(request, snapshots, contracts, patch_summaries)
            self._validate_patch_integrity(request, manifest, patch_summaries)
            context_package: ContextPackage | None = None
            if self.context_builder is not None:
                context_package = self.context_builder.build(context_refs)
                if not context_package.approved:
                    raise GateValidationError(
                        context_package.failure.local_code if context_package.failure else "context_package.insufficient",
                        context_package.failure.failure_class if context_package.failure else "verifier_context",
                        context_package.failure.failure_type if context_package.failure else "context_materialization_failed",
                        context_package.failure.message if context_package.failure else "bounded context package failed",
                        details=context_package.failure.details if context_package.failure else {},
                    )
                self._write_approved_node(
                    request,
                    "bounded_context_package",
                    {"package_hash": context_package.package_hash, "read_budget": _dump(context_package.read_budget)},
                    input_refs=context_package.selected_refs,
                    metadata={"package_hash": context_package.package_hash},
                )
            return GateRunResult(
                approved=True,
                should_dispatch_verifier=True,
                candidate_manifest=manifest,
                nodes=self.recorder.nodes[start_index:],
                context_package=context_package,
            )
        except IdempotencyConflict:
            raise
        except GateValidationError as error:
            failure = GateFailure.from_error(error)
            node_name = _node_name_for_local_code(error.local_code)
            self.recorder.write_node(
                request=request,
                name=node_name,
                kind="deterministic_gate",
                input_hash=stable_digest({"request": _dump(request), "failure": failure.model_dump(mode="json")}),
                status="rejected",
                failure=failure,
                metadata={"local_code": error.local_code},
            )
            return GateRunResult(
                approved=False,
                should_dispatch_verifier=False,
                candidate_manifest=manifest,
                nodes=self.recorder.nodes[start_index:],
                failure=failure,
            )

    def _write_approved_node(
        self,
        request: GateRequest,
        name: str,
        material: Any,
        *,
        input_refs: Sequence[EvidenceRef] = (),
        metadata: dict[str, JsonValue] | None = None,
    ) -> EvidenceNode:
        return self.recorder.write_node(
            request=request,
            name=name,
            kind="deterministic_gate" if name != "gate_request" else "gate_request",
            input_hash=stable_digest(material),
            status="approved",
            input_refs=input_refs,
            metadata=metadata,
        )

    def _validate_workspace_snapshots(
        self,
        request: GateRequest,
        manifest: CandidateManifest,
    ) -> list[GateWorkspaceSnapshot]:
        snapshots = _coerce_list(
            self.gateway.get_workspace_snapshots_by_ids(request.workspace_snapshot_ids),
            GateWorkspaceSnapshot,
        )
        _require_exact_ids("workspace snapshot", request.workspace_snapshot_ids, snapshots)
        for snapshot in snapshots:
            if (
                snapshot.feature_id != request.feature_id
                or snapshot.dag_sha256 != request.dag_sha256
                or snapshot.group_idx != request.group_idx
            ):
                raise GateValidationError(
                    "workspace_snapshot.stale",
                    "stale_projection",
                    "workspace_snapshot_stale",
                    "workspace snapshot does not belong to the gate request lineage",
                    details={"snapshot_id": snapshot.id},
                )
            root = snapshot.canonical_root or snapshot.root
            if manifest.workspace_root and root and manifest.workspace_root != root:
                raise GateValidationError(
                    "workspace_snapshot.stale",
                    "stale_projection",
                    "workspace_snapshot_stale",
                    "workspace snapshot root does not match candidate manifest",
                    details={"snapshot_id": snapshot.id, "manifest_root": manifest.workspace_root, "snapshot_root": root},
                )
            if manifest.base_commit and snapshot.base_commit and manifest.base_commit != snapshot.base_commit:
                raise GateValidationError(
                    "workspace_snapshot.stale",
                    "stale_projection",
                    "workspace_snapshot_stale",
                    "workspace snapshot base commit does not match candidate manifest",
                    details={"snapshot_id": snapshot.id},
                )
            expected_hash = manifest.workspace_snapshot_hashes.get(snapshot.id)
            if expected_hash and snapshot.snapshot_hash and expected_hash != snapshot.snapshot_hash:
                raise GateValidationError(
                    "workspace_snapshot.stale",
                    "stale_projection",
                    "workspace_snapshot_stale",
                    "workspace snapshot hash does not match candidate manifest",
                    details={"snapshot_id": snapshot.id},
                )
        self._write_approved_node(
            request,
            "workspace_snapshot_freshness",
            {"request": _dump(request), "snapshots": [_dump(snapshot) for snapshot in snapshots]},
            input_refs=[EvidenceRef(kind="snapshot", id=snapshot.id, sha256=snapshot.snapshot_hash) for snapshot in snapshots],
        )
        return snapshots

    def _validate_contracts(self, request: GateRequest) -> list[GateContractSnapshot]:
        contracts = _coerce_list(self.gateway.get_contracts_by_ids(request.contract_ids), GateContractSnapshot)
        _require_exact_ids("contract", request.contract_ids, contracts)
        for contract in contracts:
            if (
                contract.feature_id != request.feature_id
                or contract.dag_sha256 != request.dag_sha256
                or contract.group_idx != request.group_idx
                or contract.status != "active"
            ):
                raise GateValidationError(
                    "artifact_freshness.stale",
                    "stale_projection",
                    "verifier_context_stale",
                    "contract does not belong to the current active gate lineage",
                    details={"contract_id": contract.id},
                )

        group_task_ids = {contract.task_id for contract in contracts}
        known_task_ids = set(group_task_ids)
        for contract in contracts:
            known_task_ids.update(contract.known_task_ids)
            known_task_ids.update(contract.all_task_ids)

        unknown_dependencies: dict[str, list[str]] = {}
        same_wave_dependencies: dict[str, list[str]] = {}
        for contract in contracts:
            for dependency in contract.dependency_task_ids:
                if dependency in group_task_ids:
                    same_wave_dependencies.setdefault(contract.task_id, []).append(dependency)
                elif dependency not in known_task_ids:
                    unknown_dependencies.setdefault(contract.task_id, []).append(dependency)
        if unknown_dependencies or same_wave_dependencies:
            failure_type = (
                "contract_same_wave_dependency"
                if same_wave_dependencies
                else "contract_missing_dependency"
            )
            raise GateValidationError(
                "contract_closure.invalid",
                "contract_compile",
                failure_type,
                "contract closure contains unknown or same-wave dependencies",
                details={
                    "unknown_dependencies": unknown_dependencies,
                    "same_wave_dependencies": same_wave_dependencies,
                },
            )

        gate_sources: dict[str, list[tuple[GateContractSnapshot, Any]]] = {}
        for contract in contracts:
            criterion_ids = _criterion_ids(contract)
            for gate in contract.verification_gates:
                gate_id = str(_attr(gate, "id", ""))
                if not gate_id:
                    continue
                for criterion_id in _string_list(_attr(gate, "criterion_ids", [])):
                    if criterion_id not in criterion_ids:
                        raise GateValidationError(
                            "contract_closure.invalid",
                            "contract_compile",
                            "contract_unknown_criterion",
                            "gate criterion id is not present in the owning contract",
                            details={"contract_id": contract.id, "gate_id": gate_id, "criterion_id": criterion_id},
                        )
                gate_sources.setdefault(gate_id, []).append((contract, gate))

        missing_gates = sorted(set(request.verification_gate_ids) - set(gate_sources))
        duplicate_gates = sorted(
            gate_id
            for gate_id, sources in gate_sources.items()
            if gate_id in request.verification_gate_ids and len(sources) > 1 and not _all_derived(sources)
        )
        if missing_gates or duplicate_gates:
            raise GateValidationError(
                "contract_closure.invalid",
                "contract_compile",
                "contract_unknown_gate",
                "gate request references missing or ambiguous verification gates",
                details={"missing_gates": missing_gates, "duplicate_gates": duplicate_gates},
            )

        self._write_approved_node(
            request,
            "contract_closure",
            {"request": _dump(request), "contracts": [_dump(contract) for contract in contracts]},
            input_refs=[
                EvidenceRef(kind="contract", id=contract.id, sha256=contract.contract_digest)
                for contract in contracts
            ],
        )
        return contracts

    def _validate_artifacts(
        self,
        request: GateRequest,
    ) -> tuple[list[GateTaskAttempt], list[GatePatchSummary]]:
        task_attempts = _coerce_list(
            self.gateway.get_task_attempts_by_ids(request.task_attempt_ids),
            GateTaskAttempt,
        )
        patch_summaries = _coerce_list(
            self.gateway.get_patch_summaries_by_ids(request.patch_summary_ids),
            GatePatchSummary,
        )
        _require_exact_ids("task attempt", request.task_attempt_ids, task_attempts)
        _require_exact_ids("patch summary", request.patch_summary_ids, patch_summaries)

        stale_ids: list[int] = []
        for attempt in task_attempts:
            if (
                attempt.feature_id != request.feature_id
                or attempt.dag_sha256 != request.dag_sha256
                or attempt.group_idx != request.group_idx
                or attempt.attempt != request.attempt
                or attempt.superseded_by_id is not None
            ):
                stale_ids.append(attempt.id)
        for summary in patch_summaries:
            if (
                summary.feature_id != request.feature_id
                or summary.dag_sha256 != request.dag_sha256
                or summary.group_idx != request.group_idx
                or summary.attempt != request.attempt
                or summary.superseded_by_id is not None
            ):
                stale_ids.append(summary.id)
        if stale_ids:
            raise GateValidationError(
                "artifact_freshness.stale",
                "stale_projection",
                "verifier_context_stale",
                "task attempt or patch summary is stale for the current gate lineage",
                details={"stale_ids": sorted(stale_ids)},
            )

        self._write_approved_node(
            request,
            "artifact_freshness",
            {
                "request": _dump(request),
                "task_attempts": [_dump(attempt) for attempt in task_attempts],
                "patch_summaries": [_dump(summary) for summary in patch_summaries],
            },
            input_refs=[
                *[EvidenceRef(kind="event", id=attempt.id) for attempt in task_attempts],
                *[EvidenceRef(kind="patch", id=summary.id) for summary in patch_summaries],
            ],
        )
        return task_attempts, patch_summaries

    def _validate_path_scope(
        self,
        request: GateRequest,
        snapshots: Sequence[GateWorkspaceSnapshot],
        contracts: Sequence[GateContractSnapshot],
        patch_summaries: Sequence[GatePatchSummary],
    ) -> None:
        present_paths = set()
        retired_aliases = set()
        for snapshot in snapshots:
            present_paths.update(_canonical_relpath(path) for path in snapshot.present_paths)
            retired_aliases.update(_canonical_relpath(path) for path in snapshot.retired_aliases)
        allowed_paths = [_rule_path(rule) for contract in contracts for rule in [*contract.allowed_paths, *contract.required_paths, *contract.generated_outputs]]
        forbidden_paths = [_rule_path(rule) for contract in contracts for rule in contract.forbidden_paths]

        invalid: list[dict[str, str]] = []
        for summary in patch_summaries:
            for path in _patch_raw_paths(summary):
                raw_path = str(path).replace("\\", "/").strip()
                if not raw_path:
                    continue
                if (
                    raw_path.startswith("/")
                    or raw_path.startswith("../")
                    or "/../" in f"/{raw_path}/"
                ):
                    invalid.append({"path": raw_path, "reason": "retired_alias_or_noncanonical"})
            touched_paths = _patch_touched_paths(summary)
            for path in touched_paths:
                canonical = _canonical_relpath(path)
                if not canonical or canonical.startswith("../") or path.startswith("/") or path in retired_aliases or canonical in retired_aliases:
                    invalid.append({"path": path, "reason": "retired_alias_or_noncanonical"})
                    continue
                if allowed_paths and not any(_path_matches_rule(canonical, rule) for rule in allowed_paths):
                    invalid.append({"path": path, "reason": "outside_allowed_paths"})
                    continue
            for path in [*summary.modified_paths, *summary.deleted_paths]:
                canonical = _canonical_relpath(path)
                if present_paths and canonical not in present_paths:
                    invalid.append({"path": path, "reason": "missing_changed_file"})
            for path in summary.deleted_paths:
                canonical = _canonical_relpath(path)
                if any(_path_matches_rule(canonical, rule) for rule in forbidden_paths):
                    invalid.append({"path": path, "reason": "forbidden_delete"})
        if invalid:
            failure_type = "outside_allowed_paths"
            if any(item["reason"] == "retired_alias_or_noncanonical" for item in invalid):
                failure_type = "alias_points_to_noncanonical_root"
                failure_class = "worktree_alias"
            elif any(item["reason"] == "forbidden_delete" for item in invalid):
                failure_type = "forbidden_path_touched"
                failure_class = "contract_violation"
            else:
                failure_class = "contract_violation"
            raise GateValidationError(
                "path_scope.invalid",
                failure_class,
                failure_type,
                "patch paths are outside the current contract path scope",
                details={"invalid_paths": invalid},
            )

        self._write_approved_node(
            request,
            "path_scope_and_projection",
            {
                "snapshots": [_dump(snapshot) for snapshot in snapshots],
                "contracts": [_dump(contract) for contract in contracts],
                "patch_summaries": [_dump(summary) for summary in patch_summaries],
            },
        )

    def _validate_patch_integrity(
        self,
        request: GateRequest,
        manifest: CandidateManifest,
        patch_summaries: Sequence[GatePatchSummary],
    ) -> None:
        invalid: list[dict[str, JsonValue]] = []
        for summary in patch_summaries:
            expected_hash = summary.summary_sha256 or summary.summary_hash
            actual_hash = summary.computed_summary_sha256 or summary.actual_summary_sha256
            if expected_hash and actual_hash and expected_hash != actual_hash:
                invalid.append({"patch_summary_id": summary.id, "reason": "summary_hash_mismatch"})
            if summary.workspace_snapshot_id is None:
                invalid.append({"patch_summary_id": summary.id, "reason": "missing_workspace_snapshot"})
            elif summary.workspace_snapshot_id not in manifest.workspace_snapshot_ids:
                invalid.append({"patch_summary_id": summary.id, "reason": "unknown_workspace_snapshot"})
            if summary.expected_touched_paths:
                expected_paths = sorted(_canonical_relpath(path) for path in summary.expected_touched_paths)
                actual_paths = sorted(_patch_touched_paths(summary))
                if expected_paths != actual_paths:
                    invalid.append({"patch_summary_id": summary.id, "reason": "touched_path_mismatch"})
        if invalid:
            raise GateValidationError(
                "patch_integrity.invalid",
                "evidence_corruption",
                "payload_digest_mismatch",
                "patch summary integrity check failed",
                details={"invalid_patch_summaries": invalid},
            )
        self._write_approved_node(
            request,
            "patch_integrity",
            {"request": _dump(request), "manifest": _dump(manifest), "patch_summaries": [_dump(summary) for summary in patch_summaries]},
        )


def gate_node_idempotency_key(request: GateRequest, node_name: str) -> str:
    return (
        f"verify-graph:{request.feature_id}:{request.dag_sha256}:"
        f"g{request.group_idx}:{request.stage}:a{request.attempt}:{node_name}"
    )


def input_hash(value: Any) -> str:
    return stable_digest(_dump(value))


def _append_omitted(
    ref: ContextReadRef,
    digest: str | None,
    omitted_required: list[EvidenceRef],
    omitted_optional: list[EvidenceRef],
) -> None:
    target = omitted_required if ref.required else omitted_optional
    target.append(ref.evidence_ref(digest))


def _budget_allows_item(current: int, byte_count: int, limit: int) -> bool:
    return current + byte_count <= limit


def _record_bytes_and_digest(record: Any) -> tuple[int, str]:
    text = _record_text(record)
    byte_count = int(_attr(record, "byte_count", len(text.encode("utf-8"))))
    digest = str(_attr(record, "sha256", "") or stable_digest(text))
    return byte_count, digest


def _record_text(record: Any) -> str:
    value = _attr(record, "text", None)
    if value is None:
        value = _attr(record, "body", None)
    if value is None:
        value = _attr(record, "value", None)
    if value is None:
        value = record
    return value if isinstance(value, str) else str(value)


def _record_payload(record: Any, source: str, byte_count: int, digest: str) -> dict[str, JsonValue]:
    return {
        "source": source,
        "id": _attr(record, "id", None),
        "byte_count": byte_count,
        "sha256": digest,
    }


def _line_count(record: Any) -> int:
    start_line = _attr(record, "start_line", None)
    end_line = _attr(record, "end_line", None)
    if isinstance(start_line, int) and isinstance(end_line, int) and end_line >= start_line:
        return end_line - start_line + 1
    return max(1, len(_record_text(record).splitlines()))


def _node_name_for_local_code(local_code: str) -> str:
    return {
        "workspace_snapshot.stale": "workspace_snapshot_freshness",
        "contract_closure.invalid": "contract_closure",
        "artifact_freshness.stale": "artifact_freshness",
        "path_scope.invalid": "path_scope_and_projection",
        "patch_integrity.invalid": "patch_integrity",
        "context_package.insufficient": "bounded_context_package",
        "gate_request.invalid": "gate_request",
    }.get(local_code, local_code.replace(".", "_"))


def _coerce_list(values: Sequence[Any], model: type[_GateModel]) -> list[Any]:
    return [model.model_validate(value) for value in values]


def _require_exact_ids(label: str, expected: Sequence[int], records: Sequence[Any]) -> None:
    actual = sorted(int(_attr(record, "id")) for record in records)
    expected_sorted = sorted(expected)
    if actual != expected_sorted:
        raise GateValidationError(
            "context_package.insufficient",
            "verifier_context",
            "context_materialization_failed",
            f"missing explicit {label} ids",
            details={"expected_ids": expected_sorted, "actual_ids": actual},
        )


def _criterion_ids(contract: GateContractSnapshot) -> set[str]:
    return {str(_attr(criterion, "id", "")) for criterion in contract.acceptance_criteria if _attr(criterion, "id", "")}


def _all_derived(sources: Sequence[tuple[GateContractSnapshot, Any]]) -> bool:
    return all(str(_attr(gate, "source", "")) == "derived" for _contract, gate in sources)


def _patch_touched_paths(summary: GatePatchSummary) -> list[str]:
    return sorted({path for path in (_canonical_relpath(item) for item in _patch_raw_paths(summary)) if path})


def _patch_raw_paths(summary: GatePatchSummary) -> list[str]:
    return [
        *summary.touched_paths,
        *summary.changed_paths,
        *summary.created_paths,
        *summary.modified_paths,
        *summary.deleted_paths,
    ]


def _canonical_relpath(path: str) -> str:
    path = str(path).replace("\\", "/").strip()
    while path.startswith("./"):
        path = path[2:]
    parts: list[str] = []
    for part in path.split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            if parts:
                parts.pop()
            else:
                return "../" + "/".join(path.split("/")[1:])
        else:
            parts.append(part)
    return "/".join(parts)


def _rule_path(rule: Any) -> str:
    return _canonical_relpath(str(_attr(rule, "path", "")))


def _path_matches_rule(path: str, rule: str) -> bool:
    if not rule:
        return False
    if rule.endswith("/"):
        return path.startswith(rule)
    return path == rule


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _attr(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def _dump(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, dict):
        return {str(key): _dump(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_dump(item) for item in value]
    return value


# --- Slice 11f -- DAG-authority routing primitives + post-DAG gate
# proof-key/notify helpers ---------------------------------------------------
# Per docs/execution-control-plane/11-refactor-map.md row 9 for
# `execution/gates.py` ("GateRunner.run_preflight, run_raw_gate,
# run_checkpoint_gate. Deterministic preflight gates, raw-gate ordering,
# stale-context blockers, gate evidence shape."), the Slice-06 surface
# above this banner owns the canonical gate primitives. Slice 11f
# EXTENDS that module with 6 pure DAG-authority routing constants + 7
# pure helpers (the route classifier, the preflight-key formatter, the
# blocked-verdict factory, the synthetic-result factory, the
# reconcile-target-coverage projection, the post-DAG-gate-proof-key
# formatter, and the notify-gate-proof-extra transform) moved
# byte-for-byte from
# `workflows/develop/phases/implementation.py`. Each one only depends on
# stdlib + `Verdict`/`Issue`/`ImplementationResult` from
# `models.outputs`; no runner/feature/store/logger/`_model_json_dict`
# coupling. The phase-level gate PORT surface (the async
# runner+feature-coupled `_attempt_dag_authority_gate_repair` /
# `_dag_authority_load_preflight_report` / `_record_dag_authority_gate`,
# the `_execution_control_store_for_runner`-coupled
# `_record_typed_verification_gate_node` /
# `_typed_verification_gate_node_is_fresh`, the merge-queue-PORT-coupled
# `_merge_queue_post_apply_gate_decision`, the
# `_get_feature_root`+subprocess-coupled `_post_dag_gate_tree_digest`
# family, the `_record_post_dag_gate_proof` /
# `_record_dag_checkpoint_gate_proof` /
# `_checkpoint_gate_graph_projection_is_fresh` /
# `_checkpoint_gate_proof_is_fresh` artifact recorders) STAYS in
# `implementation.py` per the prompt hard rule against splitting non-
# pure helpers, and the `_is_dag_task_artifact_key`-coupled
# `_dag_authority_applied_dag_task_updates` /
# `_dag_authority_task_refs_from_path_problems` are deferred until
# their impl.py-local dependency utilities migrate in a future
# utility-cluster sub-slice.


_DAG_AUTHORITY_SEMANTIC_ROUTE = "semantic_verify_needed"
_DAG_AUTHORITY_DB_TASK_RESULT_ROUTE = "db_task_result_drift"
_DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE = "task_spec_projection_drift"
_DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE = "source_dag_artifact_drift"
_DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE = "product_workspace_drift"
_DAG_AUTHORITY_REPO_BLOCKER_ROUTE = "repo_or_permission_blocker"


def _dag_authority_preflight_key(
    group_idx: int,
    retry_label: str,
) -> str:
    return f"dag-repair-preflight:g{group_idx}:retry-{retry_label}"


def _dag_authority_path_problem_route(
    problems: list[dict[str, Any]],
    artifact_only: list[dict[str, Any]],
) -> tuple[str, str]:
    if not problems:
        return _DAG_AUTHORITY_SEMANTIC_ROUTE, "no_path_problems"
    if any(
        str(problem.get("reason", "")) in {"embedded_git", "gitlink", "parked_fallback"}
        for problem in problems
    ):
        return _DAG_AUTHORITY_REPO_BLOCKER_ROUTE, "repo_hygiene_path_problem"
    if any(
        str(problem.get("repair_route", "")) == "product_cleanup_required"
        or bool(problem.get("exists_on_disk"))
        or bool(problem.get("tracked_or_staged"))
        or str(problem.get("git_state", "")) in {
            "clean_tracked",
            "unstaged_delete",
            "staged_add",
            "untracked",
        }
        for problem in problems
    ):
        return _DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE, (
            "path_problem_requires_product_workspace_cleanup"
        )
    if not artifact_only:
        return _DAG_AUTHORITY_SEMANTIC_ROUTE, "no_deterministic_artifact_only_problem"
    if any(
        str(problem.get("reason", "")) in {
            "forbidden_task_spec",
            "forbidden_task_spec_source_artifact",
        }
        for problem in artifact_only
    ):
        return _DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE, "task_spec_projection_drift"
    if any(
        str(problem.get("source_artifact_ref", "")).strip()
        and not str(problem.get("artifact_key", "")).startswith("dag-task:")
        for problem in artifact_only
    ):
        return _DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE, "source_artifact_drift"
    return _DAG_AUTHORITY_DB_TASK_RESULT_ROUTE, "db_task_result_drift"


def _dag_authority_blocked_verdict(
    group_idx: int,
    retry: int,
    *,
    route: str,
    reason: str,
    target_refs: list[str],
    detail: str,
) -> Verdict:
    refs = ", ".join(target_refs) if target_refs else "(none)"
    return Verdict(
        approved=False,
        summary=(
            f"Group {group_idx} authority gate blocked retry {retry}: "
            f"{route} did not produce a valid authoritative repair."
        ),
        concerns=[
            Issue(
                severity="blocker",
                description=(
                    "DAG authority gate blocked broad repair. "
                    f"Route: {route}; reason: {reason}; target refs: {refs}. "
                    f"{detail}"
                ),
            )
        ],
        suggestions=[
            "Repair the authoritative DAG/task artifact state, then retry verification.",
        ],
    )


def _dag_authority_synthetic_result(
    group_idx: int,
    retry: int,
    report: dict[str, Any],
) -> ImplementationResult:
    return ImplementationResult(
        task_id=f"DAG-AUTHORITY-REPAIR-g{group_idx}-r{retry}",
        summary=(
            "DAG authority gate repaired deterministic workflow metadata "
            f"via {report.get('status', 'unknown')}."
        ),
        status="completed",
        files_created=[],
        files_modified=[],
        notes=json.dumps(report, indent=2),
    )


def _dag_authority_reconcile_target_coverage(
    reconcile_report: dict[str, Any],
    target_refs: list[str],
) -> dict[str, Any]:
    targets = set(target_refs)
    covered = {
        str(item.get("artifact_key", ""))
        for item in reconcile_report.get("applied", []) or []
        if isinstance(item, dict)
    } & targets
    skipped = [
        item for item in reconcile_report.get("skipped", []) or []
        if isinstance(item, dict)
        and str(item.get("artifact_key", "")) in targets
    ]
    blockers = [
        item for item in reconcile_report.get("blockers", []) or []
        if isinstance(item, dict)
        and str(item.get("artifact_key", "")) in targets
    ]
    missing = sorted(targets - covered)
    return {
        "target_refs": sorted(targets),
        "covered_refs": sorted(covered),
        "missing_refs": missing,
        "complete": not missing and not blockers,
        "skipped": skipped,
        "blockers": blockers,
    }


def _post_dag_gate_proof_key(gate_name: str) -> str:
    return f"dag-gate-proof:{gate_name}"


def _notify_gate_proof_extra_from_delivery(delivery: dict[str, Any]) -> dict[str, Any]:
    return {
        "delivery_id": str(delivery.get("delivery_id") or ""),
        "notification_sha256": str(delivery.get("notification_sha256") or ""),
    }


__all__ = [
    "BoundedQuery",
    "CandidateManifest",
    "ContextBudget",
    "ContextPackage",
    "ContextPackageBuilder",
    "ContextReadRef",
    "EvidenceNode",
    "EvidenceRef",
    "GateFailure",
    "GatePatchSummary",
    "GateReadGateway",
    "GateRequest",
    "GateResult",
    "GateRunner",
    "GateTaskAttempt",
    "GateValidationError",
    "GateWorkspaceSnapshot",
    "IdempotencyConflict",
    "InMemoryEvidenceRecorder",
    "ReadBudgetReport",
    "_DAG_AUTHORITY_DB_TASK_RESULT_ROUTE",
    "_DAG_AUTHORITY_PRODUCT_WORKSPACE_ROUTE",
    "_DAG_AUTHORITY_REPO_BLOCKER_ROUTE",
    "_DAG_AUTHORITY_SEMANTIC_ROUTE",
    "_DAG_AUTHORITY_SOURCE_ARTIFACT_ROUTE",
    "_DAG_AUTHORITY_TASK_SPEC_PROJECTION_ROUTE",
    "_dag_authority_blocked_verdict",
    "_dag_authority_path_problem_route",
    "_dag_authority_preflight_key",
    "_dag_authority_reconcile_target_coverage",
    "_dag_authority_synthetic_result",
    "_notify_gate_proof_extra_from_delivery",
    "_post_dag_gate_proof_key",
    "gate_node_idempotency_key",
    "input_hash",
]
