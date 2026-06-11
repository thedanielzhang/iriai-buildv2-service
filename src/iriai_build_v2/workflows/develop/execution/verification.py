"""Pure verification-graph orchestration helpers.

This module is intentionally persistence-free.  It normalizes raw verifier and
expanded lens results, merges them into a deterministic aggregate verdict, and
builds graph approval proofs from aggregate evidence only.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Sequence
from typing import Any, Literal, TypeAlias

from iriai_compose import to_str
from pydantic import BaseModel, ConfigDict, Field, model_validator

from iriai_build_v2.models.outputs import (
    Check,
    Gap,
    ImplementationResult,
    ImplementationTask,
    Issue,
    Verdict,
)
from iriai_build_v2.workflows.develop.execution.types import DagVerifyLensSpec

try:
    # Slice 11g — the impl.py-local `dispatcher_stable_digest`
    # rename is re-imported here so the digest cluster moved from
    # `implementation.py` preserves the byte-for-byte fallback
    # chain `verify_graph_stable_digest or dispatcher_stable_digest`.
    from iriai_build_v2.workflows.develop.execution.dispatcher import (
        stable_digest as dispatcher_stable_digest,
    )
except ImportError:  # pragma: no cover - Slice 05 module may be absent in old installs.
    dispatcher_stable_digest = None  # type: ignore[assignment]


JsonValue: TypeAlias = str | int | float | bool | None | dict[str, Any] | list[Any]
EvidenceNodeKind = Literal[
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
EvidenceEdgeKind = Literal["requires", "reads", "produces", "blocks", "supersedes"]
EvidenceNodeStatus = Literal["pending", "running", "approved", "rejected", "failed", "skipped"]
VerifierKind = Literal["raw", "lens"]
VerifierFailureSource = Literal["provider", "parse", "context"]
RuntimeFailureReason = Literal["timeout", "crash", "parse_failed", "context_failed"]


BLOCKING_SEVERITIES = frozenset({"blocker", "major"})
_DEFAULT_STARTED_AT = "1970-01-01T00:00:00Z"
_DEFAULT_FINISHED_AT = "1970-01-01T00:00:00Z"
_SEVERITY_RANK = {
    "": 0,
    "nit": 1,
    "minor": 2,
    "major": 3,
    "blocker": 4,
}
_RETRYABLE_NODE_KINDS = frozenset({"raw_verifier", "expanded_lens", "aggregate_verdict"})


class _VerificationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GraphApprovalError(ValueError):
    """Raised when a merge/checkpoint proof cannot be built from graph evidence."""


class GraphIdempotencyConflict(ValueError):
    """Raised when an idempotency key is replayed with a different input hash."""


class EvidenceRef(_VerificationModel):
    kind: Literal["artifact", "event", "contract", "snapshot", "patch", "commit"]
    id: int | str
    sha256: str | None = None
    projection_key: str | None = None


class EvidenceNode(_VerificationModel):
    id: int
    feature_id: str
    group_idx: int
    stage: str
    kind: EvidenceNodeKind
    name: str
    idempotency_key: str
    status: EvidenceNodeStatus
    deterministic: bool
    input_hash: str
    output_hash: str | None = None
    started_at: str = _DEFAULT_STARTED_AT
    finished_at: str | None = _DEFAULT_FINISHED_AT
    input_refs: list[EvidenceRef] = Field(default_factory=list)
    output_refs: list[EvidenceRef] = Field(default_factory=list)
    failure_id: int | None = None
    verdict_id: int | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class EvidenceEdge(_VerificationModel):
    id: int
    from_node_id: int
    to_node_id: int
    kind: EvidenceEdgeKind
    required: bool = True


class BoundedQuery(_VerificationModel):
    source: Literal["artifact", "event", "file", "diff", "contract", "snapshot"]
    lookup_kind: Literal["id", "exact_key", "bounded_feature", "file_slice"]
    ids: list[int | str] = Field(default_factory=list)
    limit: int | None = None
    after_id: int | None = None
    event_types: list[str] = Field(default_factory=list)
    deterministic_order: str | None = None


class ReadBudgetReport(_VerificationModel):
    bounded_queries: list[BoundedQuery] = Field(default_factory=list)
    artifact_count: int = 0
    event_count: int = 0
    file_count: int = 0
    aggregate_bytes: int = 0
    omitted_optional_refs: list[EvidenceRef] = Field(default_factory=list)
    omitted_required_refs: list[EvidenceRef] = Field(default_factory=list)
    blocked_unbounded_read_count: int = 0
    budget_digest: str = ""

    @model_validator(mode="after")
    def _default_budget_digest(self) -> "ReadBudgetReport":
        if not self.budget_digest:
            self.budget_digest = stable_digest(
                {
                    "bounded_queries": [q.model_dump(mode="json") for q in self.bounded_queries],
                    "artifact_count": self.artifact_count,
                    "event_count": self.event_count,
                    "file_count": self.file_count,
                    "aggregate_bytes": self.aggregate_bytes,
                    "omitted_optional_refs": [
                        ref.model_dump(mode="json") for ref in self.omitted_optional_refs
                    ],
                    "omitted_required_refs": [
                        ref.model_dump(mode="json") for ref in self.omitted_required_refs
                    ],
                    "blocked_unbounded_read_count": self.blocked_unbounded_read_count,
                }
            )
        return self


class VerifierNodeResult(_VerificationModel):
    node_id: int
    verifier_kind: VerifierKind
    lens_slug: str | None = None
    approved: bool
    verdict_id: int
    provider_failure_id: int | None = None
    prompt_context_node_id: int
    read_budget: ReadBudgetReport

    @model_validator(mode="after")
    def _lens_slug_matches_kind(self) -> "VerifierNodeResult":
        if self.verifier_kind == "lens" and not self.lens_slug:
            raise ValueError("lens verifier results require lens_slug")
        if self.verifier_kind == "raw" and self.lens_slug is not None:
            raise ValueError("raw verifier results cannot set lens_slug")
        return self


class TypedFailure(_VerificationModel):
    failure_id: int | None = None
    local_code: str
    failure_class: str
    failure_type: str
    route: str
    blocking_failure_class: str


class VerifierCompatibilityLinks(_VerificationModel):
    raw_output_verifier_node_id: int | None = None
    parsed_verdict_verifier_node_id: int | None = None
    projection_verifier_node_id: int | None = None
    context_package_node_id: int | None = None
    context_hash_matches: bool = True


class VerificationNodeOutcome(_VerificationModel):
    node: EvidenceNode
    result: VerifierNodeResult
    verdict: Verdict | None = None
    typed_failure: TypedFailure | None = None
    compatibility: VerifierCompatibilityLinks = Field(default_factory=VerifierCompatibilityLinks)
    compatibility_conflicts: list[str] = Field(default_factory=list)

    @property
    def blocking_failure_class(self) -> str | None:
        if self.compatibility_conflicts:
            return "aggregate.conflict"
        if self.typed_failure is not None:
            return self.typed_failure.blocking_failure_class
        return None


class MergedConcern(_VerificationModel):
    concern: Issue
    sources: list[str]
    node_ids: list[int]


class MergedVerificationVerdict(_VerificationModel):
    verdict: Verdict
    concerns: list[MergedConcern] = Field(default_factory=list)


class AggregateVerdict(_VerificationModel):
    node_id: int
    approved: bool
    raw_verdict_node_id: int | None
    required_gate_node_ids: list[int]
    required_lens_node_ids: list[int]
    merged_verdict_id: int
    failure_ids: list[int]
    blocking_failure_class: str | None


class AggregateBuildResult(_VerificationModel):
    aggregate: AggregateVerdict
    node: EvidenceNode
    verdict: Verdict
    merged_concerns: list[MergedConcern] = Field(default_factory=list)
    required_edge_ids: list[int] = Field(default_factory=list)
    required_lineage_node_ids: list[int] = Field(default_factory=list)
    projection_keys: list[str] = Field(default_factory=list)
    verifier_compatibility_links: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    feature_id: str = ""
    dag_sha256: str = ""
    group_idx: int | None = None
    stage: str = ""


class GraphApprovalProof(_VerificationModel):
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    stage: str
    aggregate_node_id: int
    aggregate_verdict_id: int
    required_edge_ids: list[int]
    required_lineage_node_ids: list[int] = Field(default_factory=list)
    required_node_status_digest: str
    raw_verifier_node_id: int
    required_lens_node_ids: list[int]
    projection_keys: list[str] = Field(default_factory=list)
    verifier_compatibility_links: dict[str, dict[str, JsonValue]] = Field(default_factory=dict)
    graph_payload_digest: str = ""
    proof_digest: str


class VerificationGraph(_VerificationModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    stage: str
    attempt: int
    gate_request_node_id: int
    nodes: list[int]
    edges: list[EvidenceEdge]
    raw_verdict_id: int | None
    expanded_lens_ids: list[int]
    aggregate_verdict_id: int
    approved: bool


def stable_digest(value: Any) -> str:
    """Return a deterministic sha256 digest for JSON-like values."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# Slice 11g — alias used by the digest cluster moved byte-for-byte
# from `implementation.py`. The impl.py-local rename
# `verify_graph_stable_digest` resolved to this module's
# `stable_digest`; the alias preserves the moved function bodies
# without altering their literal text.
verify_graph_stable_digest = stable_digest


def map_verifier_failure(
    verifier_kind: VerifierKind,
    source: VerifierFailureSource,
    *,
    reason: RuntimeFailureReason | None = None,
    failure_id: int | None = None,
) -> TypedFailure:
    """Map local verifier failure causes to Slice 07-compatible typed classes."""

    local_prefix = "raw_verifier" if verifier_kind == "raw" else "expanded_lens"
    if source == "context":
        return TypedFailure(
            failure_id=failure_id,
            local_code=f"{local_prefix}.runtime",
            failure_class="verifier_context",
            failure_type="context_materialization_failed",
            route="retry_verifier",
            blocking_failure_class="verifier_context",
        )

    failure_type = "verifier_parse_failed"
    if source == "provider":
        if reason == "timeout":
            failure_type = "verifier_provider_timeout"
        elif reason == "crash":
            failure_type = "verifier_provider_crash"
        elif reason == "parse_failed":
            failure_type = "verifier_parse_failed"

    return TypedFailure(
        failure_id=failure_id,
        local_code=f"{local_prefix}.runtime",
        failure_class="verifier_provider",
        failure_type=failure_type,
        route="retry_verifier",
        blocking_failure_class="verifier_provider",
    )


def normalize_raw_verifier_result(
    node: EvidenceNode,
    *,
    verdict: Verdict | None,
    verdict_id: int,
    prompt_context_node_id: int,
    read_budget: ReadBudgetReport,
    provider_failure_id: int | None = None,
    failure_source: VerifierFailureSource | None = None,
    runtime_failure_reason: RuntimeFailureReason | None = None,
    compatibility: VerifierCompatibilityLinks | None = None,
) -> VerificationNodeOutcome:
    """Build the canonical outcome for a raw verifier node."""

    return _normalize_verifier_result(
        "raw",
        node,
        verdict=verdict,
        verdict_id=verdict_id,
        prompt_context_node_id=prompt_context_node_id,
        read_budget=read_budget,
        provider_failure_id=provider_failure_id,
        failure_source=failure_source,
        runtime_failure_reason=runtime_failure_reason,
        compatibility=compatibility,
    )


def normalize_lens_verifier_result(
    node: EvidenceNode,
    *,
    lens_slug: str,
    verdict: Verdict | None,
    verdict_id: int,
    prompt_context_node_id: int,
    read_budget: ReadBudgetReport,
    provider_failure_id: int | None = None,
    failure_source: VerifierFailureSource | None = None,
    runtime_failure_reason: RuntimeFailureReason | None = None,
    compatibility: VerifierCompatibilityLinks | None = None,
) -> VerificationNodeOutcome:
    """Build the canonical outcome for an expanded lens verifier node."""

    return _normalize_verifier_result(
        "lens",
        node,
        lens_slug=lens_slug,
        verdict=verdict,
        verdict_id=verdict_id,
        prompt_context_node_id=prompt_context_node_id,
        read_budget=read_budget,
        provider_failure_id=provider_failure_id,
        failure_source=failure_source,
        runtime_failure_reason=runtime_failure_reason,
        compatibility=compatibility,
    )


def merge_verdicts_deterministically(
    raw_outcome: VerificationNodeOutcome | None,
    lens_outcomes: Sequence[VerificationNodeOutcome],
    *,
    approved: bool,
    summary_prefix: str = "Verification graph aggregate",
) -> MergedVerificationVerdict:
    """Merge raw and lens verdicts with deterministic lens ordering and concern dedupe."""

    sources: list[tuple[str, int, Verdict]] = []
    if raw_outcome is not None and raw_outcome.verdict is not None:
        sources.append(("raw", raw_outcome.node.id, raw_outcome.verdict))
    for lens in _sort_lens_outcomes(lens_outcomes):
        if lens.verdict is not None:
            sources.append((f"lens:{lens.result.lens_slug}", lens.node.id, lens.verdict))

    concern_by_key: dict[tuple[str, str], MergedConcern] = {}
    concern_order: list[tuple[str, str]] = []
    checks: list[Check] = []
    check_keys: set[tuple[str, str, str]] = set()
    gaps: list[Gap] = []
    gap_keys: set[tuple[str, str, str]] = set()
    suggestions: list[str] = []
    suggestion_keys: set[str] = set()
    summaries: list[str] = []

    for source, node_id, verdict in sources:
        summaries.append(f"{source}: {verdict.summary}")
        for concern in verdict.concerns:
            key = _concern_merge_key(concern)
            existing = concern_by_key.get(key)
            if existing is None:
                concern_by_key[key] = MergedConcern(
                    concern=concern,
                    sources=[source],
                    node_ids=[node_id],
                )
                concern_order.append(key)
            else:
                updated = _merge_concern(existing, concern, source, node_id)
                concern_by_key[key] = updated
        for check in verdict.checks:
            key = (
                check.criterion.strip().lower(),
                check.result.strip().upper(),
                check.detail.strip().lower(),
            )
            if key not in check_keys:
                check_keys.add(key)
                checks.append(check)
        for gap in verdict.gaps:
            key = (
                gap.category.strip().lower(),
                _canonical_text(gap.description),
                gap.plan_reference.strip().lower(),
            )
            if key not in gap_keys:
                gap_keys.add(key)
                gaps.append(gap)
        for suggestion in verdict.suggestions:
            key = suggestion.strip().lower()
            if key not in suggestion_keys:
                suggestion_keys.add(key)
                suggestions.append(suggestion)

    merged_concerns = [concern_by_key[key] for key in concern_order]
    verdict = Verdict(
        approved=approved,
        summary=_aggregate_summary(summary_prefix, summaries),
        concerns=[merged.concern for merged in merged_concerns],
        checks=checks,
        gaps=gaps,
        suggestions=suggestions,
    )
    return MergedVerificationVerdict(verdict=verdict, concerns=merged_concerns)


def build_aggregate_verdict(
    *,
    aggregate_node: EvidenceNode,
    required_gate_nodes: Sequence[EvidenceNode],
    raw_outcome: VerificationNodeOutcome | None,
    lens_outcomes: Sequence[VerificationNodeOutcome],
    required_lens_slugs: Sequence[str],
    merged_verdict_id: int,
    required_edge_ids: Sequence[int] = (),
    required_lineage_node_ids: Sequence[int] = (),
    projection_keys: Sequence[str] = (),
) -> AggregateBuildResult:
    """Conservatively merge deterministic gates, raw verifier, and lenses."""

    gate_nodes = sorted(required_gate_nodes, key=lambda node: node.id)
    sorted_lenses = _sort_lens_outcomes(lens_outcomes)
    required_lens_slug_set = set(required_lens_slugs)
    required_lens_by_slug = {
        lens.result.lens_slug: lens
        for lens in sorted_lenses
        if lens.result.lens_slug in required_lens_slug_set
    }
    required_lens_node_ids = [
        required_lens_by_slug[slug].node.id
        for slug in sorted(required_lens_slug_set)
        if slug in required_lens_by_slug
    ]

    blocking_class: str | None = None
    failure_ids: list[int] = []
    approved = True

    rejected_gate = next(
        (node for node in gate_nodes if node.status in {"rejected", "failed", "skipped"}),
        None,
    )
    unapproved_gate = next(
        (node for node in gate_nodes if node.status != "approved"),
        None,
    )
    if rejected_gate is not None:
        approved = False
        blocking_class = "deterministic_gate"
        if rejected_gate.failure_id is not None:
            failure_ids.append(rejected_gate.failure_id)
    elif unapproved_gate is not None:
        approved = False
        blocking_class = "deterministic_gate"
    elif raw_outcome is None:
        approved = False
        blocking_class = "verifier_context"
    elif raw_outcome.compatibility_conflicts:
        approved = False
        blocking_class = "aggregate.conflict"
    elif raw_outcome.typed_failure is not None:
        approved = False
        blocking_class = raw_outcome.typed_failure.blocking_failure_class
        if raw_outcome.typed_failure.failure_id is not None:
            failure_ids.append(raw_outcome.typed_failure.failure_id)
    elif not raw_outcome.result.approved:
        approved = False
        blocking_class = "product_defect"
    else:
        missing_lenses = sorted(required_lens_slug_set - set(required_lens_by_slug))
        failed_lens = next(
            (
                lens
                for lens in sorted_lenses
                if lens.result.lens_slug in required_lens_slug_set
                and (
                    lens.compatibility_conflicts
                    or lens.typed_failure is not None
                    or not lens.result.approved
                )
            ),
            None,
        )
        if missing_lenses:
            approved = False
            blocking_class = "verifier_context"
        elif failed_lens is not None:
            approved = False
            if failed_lens.compatibility_conflicts:
                blocking_class = "aggregate.conflict"
            elif failed_lens.typed_failure is not None:
                blocking_class = failed_lens.typed_failure.blocking_failure_class
                if failed_lens.typed_failure.failure_id is not None:
                    failure_ids.append(failed_lens.typed_failure.failure_id)
            else:
                blocking_class = "product_defect"

    merged = merge_verdicts_deterministically(
        raw_outcome,
        sorted_lenses,
        approved=approved,
        summary_prefix=(
            "Verification graph approved"
            if approved
            else f"Verification graph rejected: {blocking_class or 'unknown'}"
        ),
    )
    verifier_compatibility_links = _verifier_compatibility_links(
        raw_outcome,
        [
            lens
            for lens in sorted_lenses
            if lens.result.lens_slug in required_lens_slug_set
        ],
    )
    aggregate = AggregateVerdict(
        node_id=aggregate_node.id,
        approved=approved,
        raw_verdict_node_id=raw_outcome.node.id if raw_outcome is not None else None,
        required_gate_node_ids=[node.id for node in gate_nodes],
        required_lens_node_ids=required_lens_node_ids,
        merged_verdict_id=merged_verdict_id,
        failure_ids=sorted(set(failure_ids)),
        blocking_failure_class=blocking_class,
    )
    return AggregateBuildResult(
        aggregate=aggregate,
        node=aggregate_node.model_copy(
            update={
                "status": "approved" if approved else "rejected",
                "verdict_id": merged_verdict_id,
                "metadata": {
                    **aggregate_node.metadata,
                    "blocking_failure_class": blocking_class,
                    "required_lens_slugs": sorted(required_lens_slug_set),
                    "verifier_compatibility_links": verifier_compatibility_links,
                },
            }
        ),
        verdict=merged.verdict,
        merged_concerns=merged.concerns,
        required_edge_ids=sorted(required_edge_ids),
        required_lineage_node_ids=sorted(set(int(item) for item in required_lineage_node_ids)),
        projection_keys=sorted(projection_keys),
        verifier_compatibility_links=verifier_compatibility_links,
        feature_id=aggregate_node.feature_id,
        group_idx=aggregate_node.group_idx,
        stage=aggregate_node.stage,
    )


def build_graph_approval_proof(
    aggregate_result: AggregateBuildResult | None,
    *,
    required_node_statuses: dict[int, EvidenceNodeStatus],
    raw_compat_projection_key: str | None = None,
    feature_id: str | None = None,
    dag_sha256: str | None = None,
    group_idx: int | None = None,
    stage: str | None = None,
    graph_payload_digest: str = "",
) -> GraphApprovalProof:
    """Build a merge/checkpoint proof from approved aggregate evidence only."""

    if aggregate_result is None:
        detail = "raw compatibility projection alone cannot approve graph"
        if raw_compat_projection_key:
            detail = f"{detail}: {raw_compat_projection_key}"
        raise GraphApprovalError(detail)
    aggregate = aggregate_result.aggregate
    if not aggregate.approved:
        raise GraphApprovalError("aggregate verdict is not approved")
    if aggregate.raw_verdict_node_id is None:
        raise GraphApprovalError("approved aggregate is missing raw verifier node")

    required_ids = [
        *aggregate.required_gate_node_ids,
        aggregate.raw_verdict_node_id,
        *aggregate.required_lens_node_ids,
        *aggregate_result.required_lineage_node_ids,
    ]
    required_ids = sorted(set(required_ids))
    missing = [node_id for node_id in required_ids if required_node_statuses.get(node_id) != "approved"]
    if missing:
        raise GraphApprovalError(f"required graph nodes are not approved: {missing}")

    status_digest = stable_digest(
        [
            {"node_id": node_id, "status": required_node_statuses[node_id]}
            for node_id in sorted(required_ids)
        ]
    )
    projection_keys = sorted(set(aggregate_result.projection_keys))
    proof_feature_id = feature_id or aggregate_result.feature_id or aggregate_result.node.feature_id
    proof_dag_sha256 = dag_sha256 if dag_sha256 is not None else aggregate_result.dag_sha256
    proof_group_idx = (
        group_idx
        if group_idx is not None
        else aggregate_result.group_idx
        if aggregate_result.group_idx is not None
        else aggregate_result.node.group_idx
    )
    proof_stage = stage if stage is not None else aggregate_result.stage or aggregate_result.node.stage
    proof_payload = {
        "feature_id": proof_feature_id,
        "dag_sha256": proof_dag_sha256,
        "group_idx": proof_group_idx,
        "stage": proof_stage,
        "aggregate_node_id": aggregate.node_id,
        "aggregate_verdict_id": aggregate.merged_verdict_id,
        "required_edge_ids": sorted(aggregate_result.required_edge_ids),
        "required_lineage_node_ids": required_ids,
        "required_node_status_digest": status_digest,
        "raw_verifier_node_id": aggregate.raw_verdict_node_id,
        "required_lens_node_ids": aggregate.required_lens_node_ids,
        "projection_keys": projection_keys,
        "verifier_compatibility_links": aggregate_result.verifier_compatibility_links,
        "graph_payload_digest": graph_payload_digest,
    }
    return GraphApprovalProof(
        feature_id=proof_feature_id,
        dag_sha256=proof_dag_sha256,
        group_idx=proof_group_idx,
        stage=proof_stage,
        aggregate_node_id=aggregate.node_id,
        aggregate_verdict_id=aggregate.merged_verdict_id,
        required_edge_ids=sorted(aggregate_result.required_edge_ids),
        required_lineage_node_ids=required_ids,
        required_node_status_digest=status_digest,
        raw_verifier_node_id=aggregate.raw_verdict_node_id,
        required_lens_node_ids=aggregate.required_lens_node_ids,
        projection_keys=projection_keys,
        verifier_compatibility_links=aggregate_result.verifier_compatibility_links,
        graph_payload_digest=graph_payload_digest,
        proof_digest=stable_digest(proof_payload),
    )


class VerificationGraphAttempt:
    """Small idempotent helper used by workflow wiring and replay tests."""

    def __init__(
        self,
        *,
        feature_id: str,
        dag_sha256: str,
        group_idx: int,
        stage: str,
        attempt: int,
        nodes: Sequence[EvidenceNode] = (),
        edges: Sequence[EvidenceEdge] = (),
    ) -> None:
        self.feature_id = feature_id
        self.dag_sha256 = dag_sha256
        self.group_idx = group_idx
        self.stage = stage
        self.attempt = attempt
        self.nodes: list[EvidenceNode] = list(nodes)
        self.edges: list[EvidenceEdge] = list(edges)
        self._node_by_key: dict[str, EvidenceNode] = {
            node.idempotency_key: node for node in self.nodes
        }
        self._edge_keys: dict[tuple[int, int, EvidenceEdgeKind, bool], EvidenceEdge] = {
            (edge.from_node_id, edge.to_node_id, edge.kind, edge.required): edge
            for edge in self.edges
        }
        self._next_node_id = max((node.id for node in self.nodes), default=0) + 1
        self._next_edge_id = max((edge.id for edge in self.edges), default=0) + 1

    def clone_for_replay(self) -> "VerificationGraphAttempt":
        return VerificationGraphAttempt(
            feature_id=self.feature_id,
            dag_sha256=self.dag_sha256,
            group_idx=self.group_idx,
            stage=self.stage,
            attempt=self.attempt,
            nodes=[node.model_copy(deep=True) for node in self.nodes],
            edges=[edge.model_copy(deep=True) for edge in self.edges],
        )

    def upsert_node(
        self,
        *,
        kind: EvidenceNodeKind,
        name: str,
        input_payload: Any,
        status: EvidenceNodeStatus,
        deterministic: bool,
        output_payload: Any | None = None,
        failure_id: int | None = None,
        verdict_id: int | None = None,
        metadata: dict[str, JsonValue] | None = None,
    ) -> EvidenceNode:
        input_hash = stable_digest(input_payload)
        output_hash = stable_digest(output_payload) if output_payload is not None else None
        lineage_key = self.idempotency_key(name)
        lineage_nodes = self._lineage_nodes(lineage_key)
        existing = next(
            (node for node in lineage_nodes if node.input_hash == input_hash),
            None,
        )
        if existing is not None:
            return existing
        superseded_node = lineage_nodes[-1] if lineage_nodes else None
        if superseded_node is not None and kind not in _RETRYABLE_NODE_KINDS:
            raise GraphIdempotencyConflict(
                f"idempotency key {lineage_key!r} replayed with different input hash"
            )
        key = (
            lineage_key
            if superseded_node is None
            else self._retry_idempotency_key(lineage_key, input_hash)
        )
        node_metadata: dict[str, JsonValue] = dict(metadata or {})
        if kind in _RETRYABLE_NODE_KINDS:
            node_metadata["idempotency_lineage_key"] = lineage_key
            if superseded_node is not None:
                node_metadata["supersedes_node_id"] = superseded_node.id

        node = EvidenceNode(
            id=self._next_node_id,
            feature_id=self.feature_id,
            group_idx=self.group_idx,
            stage=self.stage,
            kind=kind,
            name=name,
            idempotency_key=key,
            status=status,
            deterministic=deterministic,
            input_hash=input_hash,
            output_hash=output_hash,
            failure_id=failure_id,
            verdict_id=verdict_id,
            metadata=node_metadata,
        )
        self._next_node_id += 1
        self.nodes.append(node)
        self._node_by_key[key] = node
        if superseded_node is not None:
            self.supersede(superseded_node, node)
        return node

    def require(self, from_node: EvidenceNode, to_node: EvidenceNode) -> EvidenceEdge:
        key = (from_node.id, to_node.id, "requires", True)
        existing = self._edge_keys.get(key)
        if existing is not None:
            return existing
        edge = EvidenceEdge(
            id=self._next_edge_id,
            from_node_id=from_node.id,
            to_node_id=to_node.id,
            kind="requires",
            required=True,
        )
        self._next_edge_id += 1
        self.edges.append(edge)
        self._edge_keys[key] = edge
        return edge

    def supersede(
        self,
        prior_node: EvidenceNode,
        replacement_node: EvidenceNode,
    ) -> EvidenceEdge:
        key = (prior_node.id, replacement_node.id, "supersedes", False)
        existing = self._edge_keys.get(key)
        if existing is not None:
            return existing
        edge = EvidenceEdge(
            id=self._next_edge_id,
            from_node_id=prior_node.id,
            to_node_id=replacement_node.id,
            kind="supersedes",
            required=False,
        )
        self._next_edge_id += 1
        self.edges.append(edge)
        self._edge_keys[key] = edge
        return edge

    def record_raw_verifier(
        self,
        *,
        context_node: EvidenceNode,
        verdict: Verdict | None,
        verdict_id: int,
        read_budget: ReadBudgetReport,
        provider_failure_id: int | None = None,
        failure_source: VerifierFailureSource | None = None,
        runtime_failure_reason: RuntimeFailureReason | None = None,
        compatibility: VerifierCompatibilityLinks | None = None,
    ) -> VerificationNodeOutcome:
        node = self.upsert_node(
            kind="raw_verifier",
            name="raw_verifier",
            input_payload={
                "context_node_id": context_node.id,
                "verdict_id": verdict_id,
                "verdict_digest": (
                    stable_digest(verdict.model_dump(mode="json"))
                    if verdict is not None
                    else None
                ),
                "read_budget": read_budget.model_dump(mode="json"),
                "provider_failure_id": provider_failure_id,
                "failure_source": failure_source,
                "runtime_failure_reason": runtime_failure_reason,
            },
            status="running",
            deterministic=False,
            verdict_id=verdict_id,
        )
        self.require(context_node, node)
        outcome = normalize_raw_verifier_result(
            node,
            verdict=verdict,
            verdict_id=verdict_id,
            prompt_context_node_id=context_node.id,
            read_budget=read_budget,
            provider_failure_id=provider_failure_id,
            failure_source=failure_source,
            runtime_failure_reason=runtime_failure_reason,
            compatibility=compatibility,
        )
        self._replace_node(outcome.node)
        return outcome

    def record_lens_verifier(
        self,
        *,
        lens_slug: str,
        context_node: EvidenceNode,
        raw_outcome: VerificationNodeOutcome,
        verdict: Verdict | None,
        verdict_id: int,
        read_budget: ReadBudgetReport,
        provider_failure_id: int | None = None,
        failure_source: VerifierFailureSource | None = None,
        runtime_failure_reason: RuntimeFailureReason | None = None,
        compatibility: VerifierCompatibilityLinks | None = None,
    ) -> VerificationNodeOutcome:
        node = self.upsert_node(
            kind="expanded_lens",
            name=f"expanded_lens:{lens_slug}",
            input_payload={
                "lens_slug": lens_slug,
                "context_node_id": context_node.id,
                "raw_verifier_node_id": raw_outcome.node.id,
                "verdict_id": verdict_id,
                "verdict_digest": (
                    stable_digest(verdict.model_dump(mode="json"))
                    if verdict is not None
                    else None
                ),
                "read_budget": read_budget.model_dump(mode="json"),
                "provider_failure_id": provider_failure_id,
                "failure_source": failure_source,
                "runtime_failure_reason": runtime_failure_reason,
            },
            status="running",
            deterministic=False,
            verdict_id=verdict_id,
            metadata={"lens_slug": lens_slug, "raw_verdict_node_id": raw_outcome.node.id},
        )
        self.require(context_node, node)
        self.require(raw_outcome.node, node)
        outcome = normalize_lens_verifier_result(
            node,
            lens_slug=lens_slug,
            verdict=verdict,
            verdict_id=verdict_id,
            prompt_context_node_id=context_node.id,
            read_budget=read_budget,
            provider_failure_id=provider_failure_id,
            failure_source=failure_source,
            runtime_failure_reason=runtime_failure_reason,
            compatibility=compatibility,
        )
        self._replace_node(outcome.node)
        return outcome

    def aggregate(
        self,
        *,
        required_gate_nodes: Sequence[EvidenceNode],
        raw_outcome: VerificationNodeOutcome | None,
        lens_outcomes: Sequence[VerificationNodeOutcome],
        required_lens_slugs: Sequence[str],
        merged_verdict_id: int,
        projection_keys: Sequence[str] = (),
    ) -> AggregateBuildResult:
        aggregate_input = {
            "required_gate_node_ids": [node.id for node in sorted(required_gate_nodes, key=lambda item: item.id)],
            "gate_statuses": {
                node.id: {"status": node.status, "failure_id": node.failure_id}
                for node in required_gate_nodes
            },
            "raw_node_id": raw_outcome.node.id if raw_outcome is not None else None,
            "raw_status": raw_outcome.node.status if raw_outcome is not None else None,
            "lens_node_ids": [
                lens.node.id for lens in _sort_lens_outcomes(lens_outcomes)
            ],
            "lens_statuses": {
                lens.node.id: {
                    "status": lens.node.status,
                    "lens_slug": lens.result.lens_slug,
                    "failure_id": lens.node.failure_id,
                }
                for lens in lens_outcomes
            },
            "required_lens_slugs": sorted(set(required_lens_slugs)),
            "merged_verdict_id": merged_verdict_id,
            "projection_keys": sorted(projection_keys),
        }
        aggregate_node = self.upsert_node(
            kind="aggregate_verdict",
            name="aggregate_verdict",
            input_payload=aggregate_input,
            status="running",
            deterministic=True,
            verdict_id=merged_verdict_id,
        )
        required_edges: list[EvidenceEdge] = []
        for gate_node in required_gate_nodes:
            required_edges.append(self.require(gate_node, aggregate_node))
        if raw_outcome is not None:
            required_edges.append(self.require(raw_outcome.node, aggregate_node))
        for lens in lens_outcomes:
            if lens.result.lens_slug in set(required_lens_slugs):
                required_edges.append(self.require(lens.node, aggregate_node))
        required_node_ids = {
            node.id for node in required_gate_nodes
        }
        if raw_outcome is not None:
            required_node_ids.add(raw_outcome.node.id)
        for lens in lens_outcomes:
            if lens.result.lens_slug in set(required_lens_slugs):
                required_node_ids.add(lens.node.id)
        for edge in self.edges:
            if (
                edge.required
                and edge.kind == "requires"
                and edge.from_node_id in required_node_ids
                and edge.to_node_id in required_node_ids
                and edge not in required_edges
            ):
                required_edges.append(edge)

        result = build_aggregate_verdict(
            aggregate_node=aggregate_node,
            required_gate_nodes=required_gate_nodes,
            raw_outcome=raw_outcome,
            lens_outcomes=lens_outcomes,
            required_lens_slugs=required_lens_slugs,
            merged_verdict_id=merged_verdict_id,
            required_edge_ids=[edge.id for edge in required_edges],
            required_lineage_node_ids=sorted(required_node_ids),
            projection_keys=projection_keys,
        )
        result = result.model_copy(
            update={
                "feature_id": self.feature_id,
                "dag_sha256": self.dag_sha256,
                "group_idx": self.group_idx,
                "stage": self.stage,
            }
        )
        self._replace_node(result.node)
        return result

    def node_statuses(self) -> dict[int, EvidenceNodeStatus]:
        return {node.id: node.status for node in self.nodes}

    def idempotency_key(self, node_name: str) -> str:
        return (
            f"verify-graph:{self.feature_id}:{self.dag_sha256}:"
            f"g{self.group_idx}:{self.stage}:a{self.attempt}:{node_name}"
        )

    def _replace_node(self, replacement: EvidenceNode) -> None:
        for idx, node in enumerate(self.nodes):
            if node.id == replacement.id:
                self.nodes[idx] = replacement
                self._node_by_key[replacement.idempotency_key] = replacement
                return
        raise KeyError(f"unknown evidence node id {replacement.id}")

    def _lineage_nodes(self, lineage_key: str) -> list[EvidenceNode]:
        return [
            node
            for node in self.nodes
            if self._node_lineage_key(node, lineage_key) == lineage_key
        ]

    def _node_lineage_key(self, node: EvidenceNode, lineage_key: str) -> str:
        metadata_lineage = node.metadata.get("idempotency_lineage_key")
        if isinstance(metadata_lineage, str):
            return metadata_lineage
        if node.idempotency_key == lineage_key:
            return lineage_key
        if node.idempotency_key.startswith(f"{lineage_key}:input:"):
            return lineage_key
        return node.idempotency_key

    def _retry_idempotency_key(self, lineage_key: str, input_hash: str) -> str:
        candidate = f"{lineage_key}:input:{input_hash[:16]}"
        if candidate not in self._node_by_key:
            return candidate
        counter = 2
        while f"{candidate}:{counter}" in self._node_by_key:
            counter += 1
        return f"{candidate}:{counter}"


def _normalize_verifier_result(
    verifier_kind: VerifierKind,
    node: EvidenceNode,
    *,
    verdict: Verdict | None,
    verdict_id: int,
    prompt_context_node_id: int,
    read_budget: ReadBudgetReport,
    lens_slug: str | None = None,
    provider_failure_id: int | None = None,
    failure_source: VerifierFailureSource | None = None,
    runtime_failure_reason: RuntimeFailureReason | None = None,
    compatibility: VerifierCompatibilityLinks | None = None,
) -> VerificationNodeOutcome:
    effective_failure_source = failure_source
    typed_failure: TypedFailure | None = None
    status: EvidenceNodeStatus

    if read_budget.blocked_unbounded_read_count or read_budget.omitted_required_refs:
        effective_failure_source = "context"

    runtime_failed = (
        provider_failure_id is not None
        or verdict is None
        or effective_failure_source is not None
    )
    compatibility = compatibility or VerifierCompatibilityLinks()
    conflicts = (
        []
        if runtime_failed
        else _compatibility_conflicts(node.id, prompt_context_node_id, compatibility)
    )

    if runtime_failed:
        effective_failure_source = effective_failure_source or "provider"
        reason = runtime_failure_reason
        if effective_failure_source == "parse":
            reason = "parse_failed"
        typed_failure = map_verifier_failure(
            verifier_kind,
            effective_failure_source,
            reason=reason,
            failure_id=provider_failure_id,
        )
        status = "failed"
        approved = False
    elif conflicts:
        typed_failure = _compatibility_conflict_failure(verifier_kind)
        status = "rejected"
        approved = False
    elif _verdict_effectively_approved(verdict):
        status = "approved"
        approved = True
    else:
        typed_failure = TypedFailure(
            local_code=(
                "raw_verifier.rejected"
                if verifier_kind == "raw"
                else "expanded_lens.rejected"
            ),
            failure_class="product_defect",
            failure_type="semantic_verifier_rejected",
            route="run_product_repair",
            blocking_failure_class="product_defect",
        )
        status = "rejected"
        approved = False

    result = VerifierNodeResult(
        node_id=node.id,
        verifier_kind=verifier_kind,
        lens_slug=lens_slug,
        approved=approved,
        verdict_id=verdict_id,
        provider_failure_id=provider_failure_id,
        prompt_context_node_id=prompt_context_node_id,
        read_budget=read_budget,
    )
    return VerificationNodeOutcome(
        node=node.model_copy(
            update={
                "status": status,
                "failure_id": typed_failure.failure_id if typed_failure else node.failure_id,
                "verdict_id": verdict_id,
                "metadata": {
                    **node.metadata,
                    "blocking_failure_class": (
                        "aggregate.conflict"
                        if conflicts
                        else typed_failure.blocking_failure_class if typed_failure else None
                    ),
                    "failure_class": typed_failure.failure_class if typed_failure else None,
                    "failure_type": typed_failure.failure_type if typed_failure else None,
                    "route": typed_failure.route if typed_failure else None,
                    "read_budget_digest": read_budget.budget_digest,
                    "verifier_compatibility_links": _verifier_compatibility_link_payload(
                        compatibility
                    ),
                    "verifier_compatibility_conflicts": conflicts,
                },
            }
        ),
        result=result,
        verdict=verdict,
        typed_failure=typed_failure,
        compatibility=compatibility,
        compatibility_conflicts=conflicts,
    )


def _compatibility_conflict_failure(verifier_kind: VerifierKind) -> TypedFailure:
    local_prefix = "raw_verifier" if verifier_kind == "raw" else "expanded_lens"
    return TypedFailure(
        local_code=f"{local_prefix}.compatibility_conflict",
        failure_class="evidence_corruption",
        failure_type="projection_body_conflict",
        route="quiesce",
        blocking_failure_class="aggregate.conflict",
    )


def _compatibility_conflicts(
    node_id: int,
    prompt_context_node_id: int,
    links: VerifierCompatibilityLinks,
) -> list[str]:
    conflicts: list[str] = []
    for field_name in (
        "raw_output_verifier_node_id",
        "parsed_verdict_verifier_node_id",
        "projection_verifier_node_id",
    ):
        value = getattr(links, field_name)
        if value != node_id:
            conflicts.append(field_name)
    if links.context_package_node_id != prompt_context_node_id:
        conflicts.append("context_package_node_id")
    if not links.context_hash_matches:
        conflicts.append("context_hash")
    return conflicts


def _verifier_compatibility_links(
    raw_outcome: VerificationNodeOutcome | None,
    lens_outcomes: Sequence[VerificationNodeOutcome],
) -> dict[str, dict[str, JsonValue]]:
    links: dict[str, dict[str, JsonValue]] = {}
    if raw_outcome is not None:
        links[str(raw_outcome.node.id)] = _verifier_compatibility_link_payload(
            raw_outcome.compatibility
        )
    for lens in _sort_lens_outcomes(lens_outcomes):
        links[str(lens.node.id)] = _verifier_compatibility_link_payload(
            lens.compatibility
        )
    return links


def _verifier_compatibility_link_payload(
    links: VerifierCompatibilityLinks,
) -> dict[str, JsonValue]:
    return {
        "raw_output_verifier_node_id": links.raw_output_verifier_node_id,
        "parsed_verdict_verifier_node_id": links.parsed_verdict_verifier_node_id,
        "projection_verifier_node_id": links.projection_verifier_node_id,
        "context_package_node_id": links.context_package_node_id,
        "context_hash_matches": links.context_hash_matches,
    }


def _verdict_effectively_approved(verdict: Verdict) -> bool:
    if not verdict.approved:
        return False
    import os as _os

    if _os.environ.get("IRIAI_STRICT_VERDICT_DISPOSITION", "").strip().lower() in (
        "1", "true", "yes", "on",
    ):
        # Item-3 strict mode: use the same normalized severity membership as
        # the gate path (closes the intra-repo inconsistency where this
        # function uppercased check results but kept unnormalized severities).
        # Shared helpers live in models.outputs (this module must not
        # back-import from phases.implementation).
        from iriai_build_v2.models.outputs import (
            check_result_failed_strict,
            normalize_severity_strict,
        )

        if any(
            normalize_severity_strict(issue.severity, context="lens-concern")
            in BLOCKING_SEVERITIES
            for issue in verdict.concerns
        ):
            return False
        if any(
            normalize_severity_strict(gap.severity, context="lens-gap")
            in BLOCKING_SEVERITIES
            for gap in verdict.gaps
        ):
            return False
        if any(
            check_result_failed_strict(check.result, context="lens-check")
            for check in verdict.checks
        ):
            return False
        return True
    if any(issue.severity in BLOCKING_SEVERITIES for issue in verdict.concerns):
        return False
    if any(gap.severity in BLOCKING_SEVERITIES for gap in verdict.gaps):
        return False
    if any(check.result.strip().upper() == "FAIL" for check in verdict.checks):
        return False
    return True


def _sort_lens_outcomes(
    lens_outcomes: Sequence[VerificationNodeOutcome],
) -> list[VerificationNodeOutcome]:
    return sorted(
        lens_outcomes,
        key=lambda lens: (str(lens.result.lens_slug or ""), lens.node.id),
    )


def _merge_concern(
    existing: MergedConcern,
    incoming: Issue,
    source: str,
    node_id: int,
) -> MergedConcern:
    best = existing.concern
    if _severity_rank(incoming.severity) > _severity_rank(best.severity):
        best = best.model_copy(update={"severity": incoming.severity})
    sources = sorted(set([*existing.sources, source]))
    node_ids = sorted(set([*existing.node_ids, node_id]))
    return existing.model_copy(
        update={
            "concern": best,
            "sources": sources,
            "node_ids": node_ids,
        }
    )


def _concern_merge_key(issue: Issue) -> tuple[str, str]:
    return (issue.file.strip(), _canonical_text(issue.description))


def _canonical_text(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _severity_rank(severity: str) -> int:
    return _SEVERITY_RANK.get(severity.strip().lower(), 0)


def _aggregate_summary(prefix: str, summaries: Iterable[str]) -> str:
    body = "\n".join(f"- {summary}" for summary in summaries)
    if not body:
        return prefix
    return f"{prefix}\n{body}"


# ---------------------------------------------------------------------------
# Slice 11g — pure verification-graph helpers extracted byte-for-byte from
# `workflows/develop/phases/implementation.py`.
#
# Per docs/execution-control-plane/11-refactor-map.md § "Boundary-level API
# contracts" row for `execution/verification.py` ("Model verifier
# orchestration, expanded lenses, bounded verify context, verifier outcome
# typing"), the canonical home for the verification-domain primitive
# surface is THIS module. Slice 11g EXTENDS the pre-existing pure
# verification-graph orchestration helpers above with 19 small pure
# helpers that the phase-level verification PORT currently consumes
# from `implementation.py`. Each one only depends on stdlib + the
# `Verdict`/`Issue`/`Gap`/`ImplementationResult`/`ImplementationTask`
# Pydantic models from `models.outputs` + the `DagVerifyLensSpec`
# dataclass from `.types` + the cluster-mate `stable_digest`
# (aliased above as `verify_graph_stable_digest`) +
# `dispatcher_stable_digest` (imported above with an ImportError
# fallback). NO runner/feature/store/logger coupling; NO import from
# `..phases.implementation`. The phase-level verification PORT
# surface (the async runner+feature-coupled artifact/store helpers
# `_get_artifact_text`, `_load_verified_dag_verification_graph_
# projection`, `_recover_dag_verification_graph_payload_from_store`,
# `_persist_dag_verification_graph_payload`, `_record_dag_
# verification_graph_artifact`, `_put_dag_verify_artifact`,
# `_validate_dag_verification_graph_payload`,
# `_validate_dag_verification_graph_rejected_payload`,
# `_record_dag_verifier_runtime_failure`,
# `_run_checkpoint_required_dag_verify_lenses`,
# `_require_dag_verification_graph_approval`, `_verify_and_fix_group`,
# `_run_expanded_dag_verify_lenses`, `_merge_dag_expanded_verify_
# verdicts`, `_verify`, `_verify_enhancements`, `_single_rca_fix_
# verify`, the env-coupled `_dag_expanded_verify_enabled` +
# `_dag_verify_lens_specs` + `_dag_verify_required_lens_specs`, the
# `_dag_verification_graph_blocker_route_payload` consumer of the
# impl.py-local `_json_object_from_text`, and the
# `_dag_verification_graph_attempt_from_payload` consumer of the
# impl.py-local `VerifyEvidenceNode`/`VerifyEvidenceEdge`/
# `VerificationGraphAttempt` rename aliases) STAYS in
# `implementation.py` per the prompt hard rule against splitting
# non-pure helpers.
# ---------------------------------------------------------------------------


def _dag_verify_stage_from_projection_key(projection_key: str) -> str:
    parts = str(projection_key).split(":", 2)
    return parts[2] if len(parts) == 3 and parts[0] == "dag-verify" else "unknown"


def _dag_verify_graph_artifact_key(group_idx: int, stage: str) -> str:
    return f"dag-verify-graph:g{group_idx}:{stage}"


def _pydantic_json(value: object) -> object:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    return to_str(value)


def _synthetic_verification_verdict_id(
    projection_key: str,
    verdict: object,
    *,
    suffix: str = "",
) -> int:
    digest_fn = verify_graph_stable_digest or dispatcher_stable_digest
    if digest_fn is None:
        digest = hashlib.sha256(to_str(verdict).encode("utf-8")).hexdigest()
    else:
        digest = str(digest_fn({
            "projection_key": projection_key,
            "suffix": suffix,
            "verdict": _pydantic_json(verdict),
        }))
    return int(digest[:12], 16)


def _dag_verify_graph_payload_covers_projection(
    payload_text: str,
    projection_key: str,
) -> bool:
    payload = _dag_verify_graph_payload_for_projection(payload_text, projection_key)
    if payload is None:
        return False
    proof = payload.get("proof")
    if not isinstance(proof, dict) or not proof.get("proof_digest"):
        return False
    projection_keys = proof.get("projection_keys")
    return isinstance(projection_keys, list) and projection_key in projection_keys


def _dag_verify_graph_payload_has_durable_projection(payload: dict[str, Any]) -> bool:
    durable = payload.get("durable_projection")
    if not isinstance(durable, dict) or durable.get("persisted") is not True:
        return False
    edge_ids = durable.get("evidence_edge_ids") or durable.get("required_edge_ids")
    if edge_ids is not None and not isinstance(edge_ids, list):
        return False
    return bool(durable.get("typed_row_id") or durable.get("projection_row_id"))


def _dag_verify_graph_digest(value: object) -> str:
    digest_fn = verify_graph_stable_digest or dispatcher_stable_digest
    if digest_fn is not None:
        return str(digest_fn(value))
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()


def _dag_verify_graph_projection_metadata(result: object) -> dict[str, Any]:
    row = getattr(result, "row", None)
    row_payload = getattr(row, "payload", None)
    if not isinstance(row_payload, dict):
        row_payload = {}
    projection_links = list(getattr(result, "projection_links", []) or [])
    link_payloads = [
        payload
        for payload in (
            getattr(link, "payload", None) for link in projection_links
        )
        if isinstance(payload, dict)
    ]
    metadata: dict[str, Any] = {
        "persisted": True,
        "typed_row_id": getattr(row, "id", None),
        "projection_link_ids": [
            getattr(link, "id", None)
            for link in projection_links
            if getattr(link, "id", None) is not None
        ],
        "compatibility_artifact_ids": [
            getattr(link, "artifact_id", None)
            for link in projection_links
            if getattr(link, "artifact_id", None) is not None
        ],
    }
    for key in (
        "aggregate_evidence_node_id",
        "evidence_graph_id",
        "proof_row_id",
        "required_edge_ids",
        "evidence_edge_ids",
        "graph_payload_digest",
    ):
        if row_payload.get(key) is not None:
            metadata[key] = row_payload[key]
    for payload in link_payloads:
        for key in (
            "aggregate_evidence_node_id",
            "evidence_graph_id",
            "required_edge_ids",
            "evidence_edge_ids",
            "graph_payload_digest",
            "proof_digest",
        ):
            if metadata.get(key) is None and payload.get(key) is not None:
                metadata[key] = payload[key]
    graph_record = getattr(result, "graph", None) or getattr(result, "evidence_graph", None)
    if graph_record is not None:
        for attr, target in (
            ("id", "evidence_graph_id"),
            ("aggregate_evidence_node_id", "aggregate_evidence_node_id"),
            ("proof_digest", "proof_digest"),
            ("graph_payload_digest", "graph_payload_digest"),
        ):
            value = getattr(graph_record, attr, None)
            if value is not None:
                metadata[target] = value
    edge_ids = getattr(result, "evidence_edge_ids", None)
    if edge_ids is not None:
        metadata["evidence_edge_ids"] = list(edge_ids)
    return metadata


def _dag_verify_graph_payload_without_durable(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = json.loads(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    )
    if isinstance(canonical, dict):
        canonical.pop("durable_projection", None)
    return canonical if isinstance(canonical, dict) else {}


def _dag_verify_graph_store_payload_digest(payload: dict[str, Any]) -> str:
    return _dag_verify_graph_digest(_dag_verify_graph_payload_without_durable(payload))


def _dag_verify_graph_payload_digest_for_proof(payload: dict[str, Any]) -> str:
    canonical = _dag_verify_graph_payload_without_durable(payload)
    proof = canonical.get("proof")
    if isinstance(proof, dict):
        proof.pop("proof_digest", None)
        proof.pop("graph_payload_digest", None)
    return _dag_verify_graph_digest(canonical)


def _dag_verify_graph_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dag_verify_graph_edge_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    normalized: list[str] = []
    for item in value:
        parsed = _dag_verify_graph_int(item)
        normalized.append(str(parsed) if parsed is not None else str(item))
    return normalized


def _dag_verify_graph_durable_metadata_from_reload(
    result: object | None,
    verified: dict[str, Any],
) -> dict[str, Any]:
    metadata = _dag_verify_graph_projection_metadata(result)
    graph = verified.get("graph")
    if isinstance(graph, dict):
        for source, target in (
            ("id", "evidence_graph_id"),
            ("execution_journal_row_id", "typed_row_id"),
            ("aggregate_evidence_node_id", "aggregate_evidence_node_id"),
            ("proof_digest", "proof_digest"),
            ("graph_payload_digest", "graph_payload_digest"),
            ("required_edge_ids", "required_edge_ids"),
        ):
            if graph.get(source) is not None:
                metadata[target] = graph[source]
    required_edges = [
        edge
        for edge in verified.get("required_edges", [])
        if isinstance(edge, dict)
    ]
    if required_edges:
        metadata["evidence_edge_ids"] = [
            str(edge.get("graph_edge_id"))
            for edge in required_edges
            if edge.get("graph_edge_id") is not None
        ]
        metadata["evidence_edge_row_ids"] = [
            edge.get("id")
            for edge in required_edges
            if edge.get("id") is not None
        ]
    projection_links = [
        link
        for link in verified.get("projection_links", [])
        if isinstance(link, dict)
    ]
    if projection_links:
        metadata["projection_link_ids"] = [
            link.get("id")
            for link in projection_links
            if link.get("id") is not None
        ]
        metadata["compatibility_artifact_ids"] = [
            link.get("artifact_id")
            for link in projection_links
            if link.get("artifact_id") is not None
        ]
    required_edge_ids = metadata.get("required_edge_ids")
    edge_lineage_required = required_edge_ids not in (None, [])
    edge_lineage_present = bool(metadata.get("evidence_edge_ids"))
    metadata["persisted"] = bool(
        metadata.get("typed_row_id")
        and metadata.get("evidence_graph_id")
        and metadata.get("projection_link_ids")
        and (edge_lineage_present or not edge_lineage_required)
    )
    return metadata


def _dag_verify_graph_payload_for_projection(
    payload_text: str,
    projection_key: str,
) -> dict[str, Any] | None:
    try:
        payload = json.loads(payload_text)
    except Exception:
        return None
    if payload.get("projection_key") != projection_key:
        return None
    if not isinstance(payload.get("nodes"), list) or not isinstance(payload.get("aggregate"), dict):
        return None
    return payload


def _dag_verify_graph_ref(value: Any) -> dict[str, Any]:
    ref: dict[str, Any] = {}
    for attr in (
        "id",
        "task_id",
        "contract_id",
        "contract_digest",
        "snapshot_id",
        "snapshot_digest",
        "workspace_snapshot_id",
        "patch_summary_id",
        "diff_sha256",
    ):
        item = getattr(value, attr, None)
        if item not in (None, "", []):
            ref[attr] = item
    payload = getattr(value, "payload", None)
    if isinstance(payload, dict):
        for attr in (
            "contract_id",
            "contract_ids",
            "contract_digest",
            "workspace_snapshot_id",
            "base_snapshot_id",
            "base_snapshot_ids",
            "patch_summary_id",
            "diff_sha256",
        ):
            item = payload.get(attr)
            if item not in (None, "", []) and attr not in ref:
                ref[attr] = item
    return ref


def _dag_verify_graph_lineage_payload(
    *,
    feature_id: str,
    projection_key: str,
    dag_sha256: str,
    group_idx: int,
    stage: str,
    tasks: list[ImplementationTask] | None = None,
    results: list[object] | None = None,
    contracts_by_task_id: dict[str, Any] | None = None,
    workspace_snapshots: list[Any] | None = None,
) -> dict[str, Any]:
    task_ids = [task.id for task in tasks or []]
    result_refs = [
        {
            "task_id": result.task_id,
            "status": result.status,
            "files_created": sorted(result.files_created),
            "files_modified": sorted(result.files_modified),
        }
        for result in results or []
        if isinstance(result, ImplementationResult)
    ]
    contract_refs = [
        {
            "task_id": str(task_id),
            **_dag_verify_graph_ref(contract),
        }
        for task_id, contract in sorted((contracts_by_task_id or {}).items())
        if contract is not None
    ]
    workspace_refs = [
        _dag_verify_graph_ref(snapshot)
        for snapshot in workspace_snapshots or []
        if snapshot is not None
    ]
    lineage = {
        "feature_id": feature_id,
        "projection_key": projection_key,
        "dag_sha256": dag_sha256 or "",
        "group_idx": group_idx,
        "stage": stage,
        "task_ids": task_ids,
        "result_refs": result_refs,
        "contract_refs": contract_refs,
        "workspace_snapshot_refs": workspace_refs,
    }
    lineage["lineage_digest"] = _dag_verify_graph_digest(lineage)
    return lineage


def _prefix_lens_issue(spec: DagVerifyLensSpec, issue: Issue) -> Issue:
    return issue.model_copy(update={
        "description": f"[{spec.label} Lens] {issue.description}",
    })


def _prefix_lens_gap(spec: DagVerifyLensSpec, gap: Gap) -> Gap:
    return gap.model_copy(update={
        "description": f"[{spec.label} Lens] {gap.description}",
    })


__all__ = [
    "AggregateBuildResult",
    "AggregateVerdict",
    "BoundedQuery",
    "EvidenceEdge",
    "EvidenceNode",
    "EvidenceRef",
    "GraphApprovalError",
    "GraphApprovalProof",
    "GraphIdempotencyConflict",
    "MergedConcern",
    "MergedVerificationVerdict",
    "ReadBudgetReport",
    "RuntimeFailureReason",
    "TypedFailure",
    "VerifierCompatibilityLinks",
    "VerifierFailureSource",
    "VerifierNodeResult",
    "VerificationGraph",
    "VerificationGraphAttempt",
    "VerificationNodeOutcome",
    "_dag_verify_graph_artifact_key",
    "_dag_verify_graph_digest",
    "_dag_verify_graph_durable_metadata_from_reload",
    "_dag_verify_graph_edge_ids",
    "_dag_verify_graph_int",
    "_dag_verify_graph_lineage_payload",
    "_dag_verify_graph_payload_covers_projection",
    "_dag_verify_graph_payload_digest_for_proof",
    "_dag_verify_graph_payload_for_projection",
    "_dag_verify_graph_payload_has_durable_projection",
    "_dag_verify_graph_payload_without_durable",
    "_dag_verify_graph_projection_metadata",
    "_dag_verify_graph_ref",
    "_dag_verify_graph_store_payload_digest",
    "_dag_verify_stage_from_projection_key",
    "_prefix_lens_gap",
    "_prefix_lens_issue",
    "_pydantic_json",
    "_synthetic_verification_verdict_id",
    "build_aggregate_verdict",
    "build_graph_approval_proof",
    "map_verifier_failure",
    "merge_verdicts_deterministically",
    "normalize_lens_verifier_result",
    "normalize_raw_verifier_result",
    "stable_digest",
    "verify_graph_stable_digest",
]
