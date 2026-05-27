"""Typed Slice 21 context-layer models and digest helpers.

The Slice 21 context layer is advisory-only. The shapes in this module carry
exact-or-paged context identity for dispatcher, gate, and reporting consumers,
but they do not grant mutation, routing, merge, checkpoint, or policy authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from iriai_build_v2.execution_control.completeness import (
    CompletenessState as ContextCompleteness,
)
from iriai_build_v2.workflows.develop.governance.models import GovernanceEvidenceRef


ContextProviderName = Literal["git_ai", "engram", "h5i", "native_git"]
ContextPackageKind = Literal["manifest", "exact", "preview"]
ProviderStatus = Literal[
    "available",
    "disabled",
    "unavailable",
    "timed_out",
    "error",
]

CONTEXT_LAYER_SCHEMA_VERSION: Literal["iriai.context_layer.v1"] = (
    "iriai.context_layer.v1"
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: object) -> str:
    """Return deterministic JSON for context-layer identity digests."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def sha256_hex(value: str | bytes) -> str:
    """Return a SHA-256 hex digest for text or bytes."""

    payload = value if isinstance(value, bytes) else value.encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def digest_payload(value: object) -> str:
    """Hash a canonical JSON projection."""

    return sha256_hex(canonical_json(value))


def _dump(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [_dump(item) for item in value]
    if isinstance(value, tuple):
        return [_dump(item) for item in value]
    if isinstance(value, dict):
        return {key: _dump(item) for key, item in value.items()}
    return value


def _non_empty(value: str) -> str:
    if not value or not value.strip():
        raise ValueError("value must be non-empty")
    return value


def _dedupe_strings(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = _non_empty(value)
        if normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _dedupe_ints(values: list[int]) -> list[int]:
    seen: set[int] = set()
    deduped: list[int] = []
    for value in values:
        if value < 0:
            raise ValueError("integer identifiers must be non-negative")
        if value in seen:
            continue
        seen.add(value)
        deduped.append(value)
    return deduped


def _validate_repo_path(value: str) -> str:
    normalized = _non_empty(value).replace("\\", "/")
    if normalized.startswith("/"):
        raise ValueError("context paths must be repo-relative")
    parts = [part for part in normalized.split("/") if part]
    if any(part == ".." for part in parts):
        raise ValueError("context paths must not escape the repo")
    return "/".join(parts)


class ContextLayerBudget(BaseModel):
    """Read/page limits for context selection, not semantic truncation."""

    model_config = ConfigDict(extra="forbid")

    max_files: int = Field(default=12, ge=1)
    max_spans_per_file: int = Field(default=20, ge=1)
    max_lines_per_span: int = Field(default=80, ge=1)
    max_commits: int = Field(default=50, ge=1)
    max_provider_records: int = Field(default=120, ge=1)
    max_provider_refs_per_record: int = Field(default=20, ge=1)
    max_provider_warnings_per_record: int = Field(default=10, ge=1)
    max_lineage_records: int = Field(default=120, ge=1)
    max_task_ids_per_lineage: int = Field(default=20, ge=1)
    max_artifact_ids_per_lineage: int = Field(default=40, ge=1)
    max_evidence_refs_per_lineage: int = Field(default=40, ge=1)
    max_governance_findings_per_lineage: int = Field(default=20, ge=1)
    max_omitted_refs: int = Field(default=200, ge=1)
    max_provider_payload_bytes: int = Field(default=512_000, ge=1)
    max_rendered_preview_chars: int = Field(default=20_000, ge=1)
    timeout_ms: int = Field(default=10_000, ge=1)


class CodeSpanRef(BaseModel):
    """A repo-relative, 1-indexed code span at a Git ref."""

    model_config = ConfigDict(extra="forbid")

    repo_id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    ref: str = "HEAD"

    @field_validator("repo_id", "ref")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("path")
    @classmethod
    def _path_is_repo_relative(cls, value: str) -> str:
        return _validate_repo_path(value)

    @model_validator(mode="after")
    def _line_range_is_valid(self) -> "CodeSpanRef":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        return self

    @property
    def line_count(self) -> int:
        return self.end_line - self.start_line + 1


class ProviderLineageRecord(BaseModel):
    """One provider-sourced code-span lineage record."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    provider: ContextProviderName
    repo_id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=1)
    code_span: CodeSpanRef
    commit_hashes: list[str] = Field(default_factory=list)
    provider_refs: list[str] = Field(default_factory=list)
    provider_state_digest: str
    content_digest: str
    confidence: float = Field(ge=0.0, le=1.0)
    warnings: list[str] = Field(default_factory=list)

    @field_validator("record_id", "repo_id", "provider_state_digest", "content_digest")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("path")
    @classmethod
    def _path_is_repo_relative(cls, value: str) -> str:
        return _validate_repo_path(value)

    @field_validator("commit_hashes", "provider_refs", "warnings")
    @classmethod
    def _string_lists_are_non_empty_and_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)

    @model_validator(mode="after")
    def _span_matches_record_fields(self) -> "ProviderLineageRecord":
        if self.end_line < self.start_line:
            raise ValueError("end_line must be greater than or equal to start_line")
        if (
            self.code_span.repo_id != self.repo_id
            or self.code_span.path != self.path
            or self.code_span.start_line != self.start_line
            or self.code_span.end_line != self.end_line
        ):
            raise ValueError("code_span must match the provider record span fields")
        return self

    def canonical_content(self) -> dict[str, Any]:
        """Return the canonical content projection used for package digests."""

        return {
            "record_id": self.record_id,
            "provider": self.provider,
            "repo_id": self.repo_id,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "code_span": self.code_span.model_dump(mode="json"),
            "commit_hashes": list(self.commit_hashes),
            "provider_refs": list(self.provider_refs),
            "provider_state_digest": self.provider_state_digest,
            "content_digest": self.content_digest,
            "confidence": self.confidence,
            "warnings": list(self.warnings),
        }


class IriAILineageRecord(BaseModel):
    """Provider span reconciled with IriAI typed workflow evidence."""

    model_config = ConfigDict(extra="forbid")

    lineage_record_id: str
    feature_id: str
    group_idx: int | None
    effective_group_idx: int | None
    code_span: CodeSpanRef
    provider_record_ids: list[str] = Field(default_factory=list)
    commit_hashes: list[str] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[int] = Field(default_factory=list)
    verify_evidence_ids: list[int] = Field(default_factory=list)
    rca_evidence_ids: list[int] = Field(default_factory=list)
    repair_evidence_ids: list[int] = Field(default_factory=list)
    checkpoint_artifact_ids: list[int] = Field(default_factory=list)
    commit_proof_evidence_ids: list[int] = Field(default_factory=list)
    governance_finding_ids: list[str] = Field(default_factory=list)
    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    content_digest: str
    linkage_confidence: float = Field(ge=0.0, le=1.0)
    gaps: list[str] = Field(default_factory=list)

    @field_validator("lineage_record_id", "feature_id", "content_digest")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator(
        "provider_record_ids",
        "commit_hashes",
        "task_ids",
        "governance_finding_ids",
        "gaps",
    )
    @classmethod
    def _string_lists_are_non_empty_and_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)

    @field_validator(
        "artifact_ids",
        "verify_evidence_ids",
        "rca_evidence_ids",
        "repair_evidence_ids",
        "checkpoint_artifact_ids",
        "commit_proof_evidence_ids",
    )
    @classmethod
    def _int_lists_are_non_negative_and_deduped(cls, value: list[int]) -> list[int]:
        return _dedupe_ints(value)

    def canonical_content(self) -> dict[str, Any]:
        """Return the canonical content projection used for package digests."""

        return {
            "lineage_record_id": self.lineage_record_id,
            "feature_id": self.feature_id,
            "group_idx": self.group_idx,
            "effective_group_idx": self.effective_group_idx,
            "code_span": self.code_span.model_dump(mode="json"),
            "provider_record_ids": list(self.provider_record_ids),
            "commit_hashes": list(self.commit_hashes),
            "task_ids": list(self.task_ids),
            "artifact_ids": list(self.artifact_ids),
            "verify_evidence_ids": list(self.verify_evidence_ids),
            "rca_evidence_ids": list(self.rca_evidence_ids),
            "repair_evidence_ids": list(self.repair_evidence_ids),
            "checkpoint_artifact_ids": list(self.checkpoint_artifact_ids),
            "commit_proof_evidence_ids": list(self.commit_proof_evidence_ids),
            "governance_finding_ids": list(self.governance_finding_ids),
            "evidence_refs": [ref.model_dump(mode="json") for ref in self.evidence_refs],
            "content_digest": self.content_digest,
            "linkage_confidence": self.linkage_confidence,
            "gaps": list(self.gaps),
        }


class ProviderStateRef(BaseModel):
    """Provider state included in package freshness identity."""

    model_config = ConfigDict(extra="forbid")

    provider: ContextProviderName
    repo_id: str
    ref: str
    state_digest: str
    indexed_at: datetime | None = None
    status: ProviderStatus

    @field_validator("repo_id", "ref", "state_digest")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)


class ProviderAvailability(BaseModel):
    """Availability check result for one provenance provider."""

    model_config = ConfigDict(extra="forbid")

    provider: ContextProviderName
    status: ProviderStatus
    version: str | None = None
    checked_at: datetime
    state_digest: str | None = None
    timeout_ms: int = Field(ge=1)
    message: str | None = None

    @field_validator("version", "state_digest", "message")
    @classmethod
    def _optional_strings_are_non_empty(cls, value: str | None) -> str | None:
        return _non_empty(value) if value is not None else None


class ProviderIndexResult(BaseModel):
    """Read-only provider indexing result for one repo."""

    model_config = ConfigDict(extra="forbid")

    provider: ContextProviderName
    repo_id: str
    state_ref: ProviderStateRef | None
    indexed: bool
    warnings: list[str] = Field(default_factory=list)
    omitted_counts: dict[str, int] = Field(default_factory=dict)

    @field_validator("repo_id")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("warnings")
    @classmethod
    def _warnings_are_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)

    @field_validator("omitted_counts")
    @classmethod
    def _omitted_counts_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        for key, count in value.items():
            _non_empty(key)
            if count < 0:
                raise ValueError("omitted counts must be non-negative")
        return dict(value)


class ContextEvidenceSnapshot(BaseModel):
    """Identity of the typed evidence snapshot used by a context package."""

    model_config = ConfigDict(extra="forbid")

    source_dag_artifact_id: int = Field(ge=1)
    dag_sha256: str
    typed_journal_high_watermark: int = Field(ge=0)
    typed_evidence_digest: str
    commit_proof_digest: str | None = None
    governance_snapshot_digest: str | None = None

    @field_validator(
        "dag_sha256",
        "typed_evidence_digest",
        "commit_proof_digest",
        "governance_snapshot_digest",
    )
    @classmethod
    def _digest_strings_are_non_empty(cls, value: str | None) -> str | None:
        return _non_empty(value) if value is not None else None


class ContextLayerRequest(BaseModel):
    """Bounded request for a read-safe task context package."""

    model_config = ConfigDict(extra="forbid")

    feature_id: str
    source_dag_artifact_id: int = Field(ge=1)
    dag_sha256: str
    evidence_snapshot: ContextEvidenceSnapshot
    task_id: str | None = None
    group_idx: int | None = None
    repo_ids: list[str] = Field(default_factory=list)
    spans: list[CodeSpanRef] = Field(default_factory=list)
    changed_paths: list[str] = Field(default_factory=list)
    include_governance: bool = True
    require_complete: bool = True
    budget: ContextLayerBudget = Field(default_factory=ContextLayerBudget)

    @field_validator("feature_id", "dag_sha256")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("task_id")
    @classmethod
    def _optional_task_id_is_non_empty(cls, value: str | None) -> str | None:
        return _non_empty(value) if value is not None else None

    @field_validator("repo_ids")
    @classmethod
    def _repo_ids_are_deduped(cls, value: list[str]) -> list[str]:
        return _dedupe_strings(value)

    @field_validator("changed_paths")
    @classmethod
    def _changed_paths_are_deduped_repo_paths(cls, value: list[str]) -> list[str]:
        return _dedupe_strings([_validate_repo_path(path) for path in value])

    @model_validator(mode="after")
    def _snapshot_identity_matches_request(self) -> "ContextLayerRequest":
        if self.source_dag_artifact_id != self.evidence_snapshot.source_dag_artifact_id:
            raise ValueError(
                "request source_dag_artifact_id must match evidence snapshot"
            )
        if self.dag_sha256 != self.evidence_snapshot.dag_sha256:
            raise ValueError("request dag_sha256 must match evidence snapshot")
        spans_by_file: dict[tuple[str, str], int] = {}
        for span in self.spans:
            key = (span.repo_id, span.path)
            spans_by_file[key] = spans_by_file.get(key, 0) + 1
        if len({span.path for span in self.spans} | set(self.changed_paths)) > self.budget.max_files:
            raise ValueError("request exceeds max_files budget")
        if any(count > self.budget.max_spans_per_file for count in spans_by_file.values()):
            raise ValueError("request exceeds max_spans_per_file budget")
        return self


class ContextPageContent(BaseModel):
    """Exact page payload retained behind a stable context page ref."""

    model_config = ConfigDict(extra="forbid")

    package_id: str
    page_ref: GovernanceEvidenceRef
    page_kind: Literal["provider_records", "iriai_lineage", "provider_record_details"]
    records: list[dict[str, Any]]
    content_digest: str

    @field_validator("package_id", "content_digest")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @model_validator(mode="after")
    def _page_ref_digest_matches_content(self) -> "ContextPageContent":
        if self.page_ref.digest != self.content_digest:
            raise ValueError("page_ref digest must match page content digest")
        return self


class ContextLayerPackage(BaseModel):
    """Read-safe, advisory-only context manifest."""

    model_config = ConfigDict(extra="forbid")

    package_id: str
    package_digest: str
    generated_at: datetime
    package_kind: ContextPackageKind
    completeness: ContextCompleteness
    request: ContextLayerRequest
    source_dag_artifact_id: int
    dag_sha256: str
    evidence_snapshot: ContextEvidenceSnapshot
    provider_state_refs: list[ProviderStateRef]
    provider_state_digest: str
    provider_order: list[ContextProviderName]
    provider_records: list[ProviderLineageRecord]
    iriai_lineage: list[IriAILineageRecord]
    rendered_preview: str | None = None
    page_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    omitted_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    omitted_counts: dict[str, int] = Field(default_factory=dict)
    incomplete_reason: str | None = None
    advisory_only: Literal[True] = True

    @field_validator("package_id", "package_digest", "dag_sha256", "provider_state_digest")
    @classmethod
    def _required_strings_are_non_empty(cls, value: str) -> str:
        return _non_empty(value)

    @field_validator("provider_order")
    @classmethod
    def _provider_order_is_deduped(cls, value: list[ContextProviderName]) -> list[ContextProviderName]:
        seen: set[ContextProviderName] = set()
        deduped: list[ContextProviderName] = []
        for provider in value:
            if provider in seen:
                continue
            seen.add(provider)
            deduped.append(provider)
        return deduped

    @field_validator("omitted_counts")
    @classmethod
    def _omitted_counts_are_non_negative(cls, value: dict[str, int]) -> dict[str, int]:
        for key, count in value.items():
            _non_empty(key)
            if count < 0:
                raise ValueError("omitted counts must be non-negative")
        return dict(value)

    @model_validator(mode="after")
    def _identity_fields_are_consistent(self) -> "ContextLayerPackage":
        if self.source_dag_artifact_id != self.request.source_dag_artifact_id:
            raise ValueError("package source DAG id must match request")
        if self.source_dag_artifact_id != self.evidence_snapshot.source_dag_artifact_id:
            raise ValueError("package source DAG id must match evidence snapshot")
        if self.dag_sha256 != self.request.dag_sha256:
            raise ValueError("package DAG sha must match request")
        if self.dag_sha256 != self.evidence_snapshot.dag_sha256:
            raise ValueError("package DAG sha must match evidence snapshot")
        if self.completeness == "unavailable" and not self.incomplete_reason:
            raise ValueError("unavailable context packages require incomplete_reason")
        if self.completeness != "unavailable" and self.incomplete_reason:
            raise ValueError("only unavailable context packages may set incomplete_reason")
        if self.completeness == "complete" and self.page_refs:
            raise ValueError("complete context packages must not carry page_refs")
        if self.completeness == "preview_only" and self.advisory_only is not True:
            raise ValueError("preview-only packages must be advisory_only")
        return self

    @property
    def review_ref(self) -> str:
        return context_package_review_ref(self.package_id)


def context_package_review_ref(package_id: str) -> str:
    """Return the bounded review artifact ref for a context package."""

    return f"review:context-package:{_non_empty(package_id)}"


def compute_provider_state_digest(refs: list[ProviderStateRef]) -> str:
    """Hash the ordered provider-state refs, including unavailable states."""

    return digest_payload(
        [
            {
                "provider": ref.provider,
                "repo_id": ref.repo_id,
                "ref": ref.ref,
                "state_digest": ref.state_digest,
                "status": ref.status,
            }
            for ref in refs
        ]
    )


def compute_provider_record_digest(record: ProviderLineageRecord) -> str:
    """Hash one provider record's canonical content."""

    return digest_payload(record.canonical_content())


def compute_iriai_lineage_digest(record: IriAILineageRecord) -> str:
    """Hash one IriAI lineage record's canonical content."""

    return digest_payload(record.canonical_content())


def compute_context_package_digest(
    *,
    request: ContextLayerRequest,
    evidence_snapshot: ContextEvidenceSnapshot,
    provider_state_digest: str,
    provider_order: list[ContextProviderName],
    provider_records: list[ProviderLineageRecord],
    iriai_lineage: list[IriAILineageRecord],
    page_refs: list[GovernanceEvidenceRef],
    omitted_counts: dict[str, int],
    completeness: ContextCompleteness,
    rendered_preview: str | None,
    incomplete_reason: str | None,
) -> str:
    """Compute the restart-stable package digest required by Slice 21."""

    rendered_context_digest = sha256_hex(rendered_preview or "")
    return digest_payload(
        {
            "schema_version": CONTEXT_LAYER_SCHEMA_VERSION,
            "request": request.model_dump(mode="json"),
            "source_dag_artifact_id": request.source_dag_artifact_id,
            "dag_sha256": request.dag_sha256,
            "evidence_snapshot": evidence_snapshot.model_dump(mode="json"),
            "provider_state_digest": provider_state_digest,
            "provider_order": list(provider_order),
            "provider_record_content_digests": [
                compute_provider_record_digest(record) for record in provider_records
            ],
            "iriai_lineage_content_digests": [
                compute_iriai_lineage_digest(record) for record in iriai_lineage
            ],
            "page_refs": [ref.model_dump(mode="json") for ref in page_refs],
            "omitted_counts": dict(sorted(omitted_counts.items())),
            "rendered_context_digest": rendered_context_digest,
            "completeness": completeness,
            "incomplete_reason": incomplete_reason,
        }
    )


def provisional_context_package_digest(
    *,
    request: ContextLayerRequest,
    evidence_snapshot: ContextEvidenceSnapshot,
    provider_state_digest: str,
    provider_order: list[ContextProviderName],
    all_provider_records: list[ProviderLineageRecord],
    all_iriai_lineage: list[IriAILineageRecord],
    omitted_counts: dict[str, int],
    rendered_preview: str | None,
    completeness: ContextCompleteness,
    incomplete_reason: str | None,
) -> str:
    """Digest used to derive package id before exact page refs exist."""

    return digest_payload(
        {
            "schema_version": CONTEXT_LAYER_SCHEMA_VERSION,
            "request": request.model_dump(mode="json"),
            "source_dag_artifact_id": request.source_dag_artifact_id,
            "dag_sha256": request.dag_sha256,
            "evidence_snapshot": evidence_snapshot.model_dump(mode="json"),
            "provider_state_digest": provider_state_digest,
            "provider_order": list(provider_order),
            "all_provider_record_content_digests": [
                compute_provider_record_digest(record) for record in all_provider_records
            ],
            "all_iriai_lineage_content_digests": [
                compute_iriai_lineage_digest(record) for record in all_iriai_lineage
            ],
            "omitted_counts": dict(sorted(omitted_counts.items())),
            "rendered_context_digest": sha256_hex(rendered_preview or ""),
            "completeness": completeness,
            "incomplete_reason": incomplete_reason,
        }
    )


__all__ = [
    "CONTEXT_LAYER_SCHEMA_VERSION",
    "CodeSpanRef",
    "ContextCompleteness",
    "ContextEvidenceSnapshot",
    "ContextLayerBudget",
    "ContextLayerPackage",
    "ContextLayerRequest",
    "ContextPackageKind",
    "ContextPageContent",
    "ContextProviderName",
    "IriAILineageRecord",
    "ProviderAvailability",
    "ProviderIndexResult",
    "ProviderLineageRecord",
    "ProviderStateRef",
    "ProviderStatus",
    "canonical_json",
    "compute_context_package_digest",
    "compute_iriai_lineage_digest",
    "compute_provider_record_digest",
    "compute_provider_state_digest",
    "context_package_review_ref",
    "digest_payload",
    "provisional_context_package_digest",
    "sha256_hex",
]
