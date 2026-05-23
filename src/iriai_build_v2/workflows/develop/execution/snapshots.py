"""Typed control-plane snapshot contract + summary models (Slice 10a).

Slice 10 ("Supervisor And Dashboard Integration") moves supervisor and
dashboard visibility from artifact-body inference toward a single typed
control-plane summary contract. This module (**10a**, the first split seam) is
the *foundation*: the typed :class:`ControlPlaneSnapshot` Pydantic contract,
its summary sub-models, and the query/budget/cursor types that bound it.

Doc 10 § "Proposed Interfaces/Types" is the SPEC for these models — field
lists / types / enum literals are transcribed verbatim from it. The two store
methods that build a :class:`ControlPlaneSnapshot`
(``ExecutionControlStore.get_control_plane_snapshot`` /
``get_control_plane_snapshot_version``) land alongside this module in
``execution_control/store.py`` (doc 10 § "Refactoring Steps" steps 1-2).

Why this is a NEW module, not a refactor of the pre-Slice-10 dict-based
``execution_control/store.fetch_control_plane_snapshot``: that standalone
function is the exploratory pre-Slice-10 implementation already wired into the
dashboard / supervisor evidence path. Doc 10 § "Refactoring Steps" step 2
("Add Pydantic models above and serialize snapshots through stable JSON so
dashboard, MCP, Slack, and tests share one contract") calls for this typed
contract as *additive* new code. Later Slice 10 sub-slices switch the
consumers over; 10a only adds the typed contract + the typed store methods.

Contract invariants (doc 10 § "Snapshot contract invariants"):

- :class:`ControlPlaneSnapshot` is the single shared status contract for the
  dashboard, MCP, supervisor classification, Slack digest generation, and the
  public-dashboard projection. Consumers may render smaller views but must not
  reconstruct workflow authority from artifact bodies or untyped event text.
- ``snapshot_version`` is the ETag seed, Slack/outbox idempotency seed, audit
  replay cursor, and optimistic-concurrency token. Every supervisor digest /
  Slack decision / public outbox event records the exact version it used.
- All payloads are SUMMARY-ONLY: ids, digests, counts, bounded samples,
  timestamps, statuses, routes, and citations. Raw prompts, artifact values,
  stdout/stderr bodies, verifier bodies, and complete dirty-path lists stay
  behind bounded detail endpoints — NEVER in a snapshot field.

Doc-10 ambiguity resolution (journaled — Slice 10a entry):

The doc-10 ``SnapshotCursor.table`` / version-digest text names typed tables
``execution_attempts``, ``typed_failures``, ``failure_route_budgets``. The
as-built control plane has NO such tables — ``schema.sql`` line 564-566 states
this explicitly ("there is no execution_attempts or typed_failures table") and
Slice 08/09 already adopted the convention. The physical backing is:

- ``execution_attempts``           -> ``execution_journal_rows``
- ``typed_failures``               -> ``evidence_nodes`` (kind
  ``runtime_failure_context`` / ``failure_route_decision``)
- ``failure_route_budgets``        -> retry-budget payload carried on the
  ``evidence_nodes`` / ``execution_journal_rows`` rows above

``SnapshotCursor.table`` keeps the doc-10 LOGICAL names verbatim (they are the
stable contract enum the version digest keys against, and a later slice may
introduce dedicated tables without breaking the contract); the store maps each
logical name to its physical table. This mirrors how the Slice-08
``merge_queue_items`` schema comment handles the identical non-existence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

__all__ = [
    "SnapshotScope",
    "SnapshotSource",
    "SnapshotRecommendedAction",
    "SupervisorClassification",
    "SnapshotBudget",
    "ControlPlaneSnapshotQuery",
    "SnapshotCursor",
    "EvidenceRef",
    "ExecutionAttemptSummary",
    "WorkspaceSnapshotSummary",
    "TypedFailureSummary",
    "MergeQueueSummary",
    "RetryBudgetSummary",
    "GateStatusSummary",
    "SandboxLeaseSummary",
    "RuntimeBindingSummary",
    "ControlPlaneSnapshot",
    "SupervisorDigest",
    "control_plane_snapshot_version",
]


# ── Shared enum literals (doc 10 § "Proposed Interfaces/Types") ─────────────
#
# doc 10 spells most of these inline on each model; hoisting them to named
# aliases mirrors the Slice 08 ``merge_queue_store`` (``MergeQueueStatus``) and
# Slice 09a ``regroup_overlay`` (``OverlayStatus``) conventions so the store
# layer and callers share one vocabulary.

SnapshotScope = Literal["dashboard", "supervisor", "mcp"]

SnapshotSource = Literal["typed", "legacy_fallback", "mixed"]

SnapshotRecommendedAction = Literal[
    "observe",
    "digest",
    "recommend",
    "act_guarded",
    "stop/escalate",
]

# Mirrors `supervisor/models.py:FailureClass` (the supervisor-class enum) — the
# typed classifier mapping (a later Slice 10 sub-slice) emits exactly these.
SupervisorClassification = Literal[
    "healthy_progress",
    "normal_product_repair",
    "deterministic_unblock",
    "pipeline_bug_suspected",
    "operator_required",
    "watch_only",
    "safe_restart_candidate",
    "stale_codex_invocation",
]


# ── Query / budget / cursor (doc 10 § "Proposed Interfaces/Types") ─────────


class SnapshotBudget(BaseModel):
    """Maximum caps for a bounded snapshot read (doc 10 § "Bounded-Read
    Constraints" — default query caps).

    These are MAXIMUM caps, not caller preferences. doc 10: "Budgets are
    maximum caps, not caller preferences that can be raised through query
    parameters." :meth:`ControlPlaneSnapshotQuery` clamps any caller-supplied
    budget DOWN to these defaults and rejects negative limits.
    """

    max_attempts: int = 20
    max_failures: int = 40
    max_merge_items: int = 40
    max_retry_budgets: int = 40
    max_gate_results: int = 40
    max_workspace_snapshots: int = 20
    max_evidence_refs: int = 80
    max_event_summaries: int = 100
    max_artifact_summaries: int = 200
    max_artifact_detail_chars: int = 20_000
    max_path_samples_per_snapshot: int = 10
    max_response_bytes: int = 250_000
    query_timeout_ms: int = 1_500

    @field_validator(
        "max_attempts",
        "max_failures",
        "max_merge_items",
        "max_retry_budgets",
        "max_gate_results",
        "max_workspace_snapshots",
        "max_evidence_refs",
        "max_event_summaries",
        "max_artifact_summaries",
        "max_artifact_detail_chars",
        "max_path_samples_per_snapshot",
        "max_response_bytes",
        "query_timeout_ms",
    )
    @classmethod
    def _reject_non_positive(cls, value: int) -> int:
        # doc 10 § "Tests": "ControlPlaneSnapshotQuery enforces budget caps and
        # rejects negative limits." A zero or negative cap would defeat the
        # `LIMIT cap + 1` truncation probe — fail fast (no silent degradation).
        if value <= 0:
            raise ValueError("snapshot budget values must be positive")
        return value


# The default (un-mutated) ceiling. A caller-supplied budget is clamped DOWN to
# this per-field — a caller can shrink a cap but can never raise it.
_BUDGET_CEILING = SnapshotBudget()


class ControlPlaneSnapshotQuery(BaseModel):
    """A bounded request for a typed control-plane snapshot.

    doc 10 § "Proposed Interfaces/Types". The ``budget`` is clamped to
    :data:`_BUDGET_CEILING` per-field by :meth:`_clamp_budget_to_ceiling` so an
    over-large caller budget cannot widen a store read.
    """

    feature_id: str
    group_idx: int | None = None
    after_snapshot_version: str | None = None
    include_terminal_groups: bool = False
    scope: SnapshotScope
    budget: SnapshotBudget = Field(default_factory=SnapshotBudget)

    @field_validator("feature_id")
    @classmethod
    def _feature_id_non_empty(cls, value: str) -> str:
        if not value or not value.strip():
            raise ValueError("control-plane snapshot query requires a feature_id")
        return value

    @model_validator(mode="after")
    def _clamp_budget_to_ceiling(self) -> "ControlPlaneSnapshotQuery":
        # doc 10: budgets are maximum caps. Clamp every caller-supplied field
        # DOWN to the ceiling — never up. A caller may request a tighter read
        # (e.g. a smaller dashboard panel) but cannot widen the store query.
        ceiling = _BUDGET_CEILING
        clamped: dict[str, int] = {}
        for name in SnapshotBudget.model_fields:
            cap = getattr(ceiling, name)
            requested = getattr(self.budget, name)
            clamped[name] = min(int(requested), int(cap))
        object.__setattr__(self, "budget", SnapshotBudget(**clamped))
        return self


class SnapshotCursor(BaseModel):
    """A keyset cursor over one bounded table read (doc 10 § "Proposed
    Interfaces/Types").

    ``table`` carries the doc-10 LOGICAL table names verbatim — they are the
    stable contract enum the snapshot version digest keys against. See the
    module docstring's "doc-10 ambiguity resolution": the physical backing for
    ``execution_attempts`` / ``typed_failures`` / ``failure_route_budgets`` is
    ``execution_journal_rows`` / ``evidence_nodes``; the store maps each
    logical name to its physical table.
    """

    table: Literal[
        "execution_attempts",
        "workspace_snapshots",
        "typed_failures",
        "failure_route_budgets",
        "merge_queue_items",
        "evidence_nodes",
        "sandbox_leases",
        "runtime_workspace_bindings",
    ]
    max_id: int
    max_updated_at: datetime | None = None


# ── Evidence / summary models (doc 10 § "Proposed Interfaces/Types") ───────


class EvidenceRef(BaseModel):
    """A bounded citation to a detail record fetched separately.

    The snapshot carries only the ``id`` + ``citation`` + a short ``summary``;
    a detail pane calls an existing bounded artifact/event endpoint by ``id``.
    doc 10: "The summary payload never includes artifact bodies."
    """

    table: Literal["evidence_nodes", "artifacts", "events", "workspace_snapshots"]
    id: int
    citation: str
    kind: str = ""
    summary: str = ""
    artifact_key: str = ""


class ExecutionAttemptSummary(BaseModel):
    """A bounded summary of one typed execution attempt.

    Physical backing: ``execution_journal_rows`` (doc-10 logical name
    ``execution_attempts``). ``input_digest`` is the typed row
    ``request_digest``; ``latest_evidence_ids`` are cited ``evidence_nodes``
    ids — NEVER prompt/output bodies.
    """

    attempt_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int | None
    task_id: str | None
    attempt_kind: Literal[
        "task", "verify", "repair", "merge", "checkpoint", "regroup"
    ]
    stage: str
    retry: int
    status: Literal["started", "succeeded", "failed", "cancelled", "incomplete"]
    actor: str
    runtime: str
    input_digest: str
    workspace_snapshot_id: int | None
    latest_evidence_ids: list[int] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime | None
    updated_at: datetime


class WorkspaceSnapshotSummary(BaseModel):
    """A bounded summary of one ``workspace_snapshots`` row.

    CRITICAL: ``dirty_path_count`` / ``forbidden_path_count`` are counts and
    ``dirty_path_sample`` / ``forbidden_path_sample`` are bounded samples
    (capped at :attr:`SnapshotBudget.max_path_samples_per_snapshot`). The full
    dirty-path list lives in ``workspace_snapshots.payload`` and is NEVER
    surfaced here — doc 10 § "Bounded-Read Constraints" (a full dirty path list
    is a P1).
    """

    snapshot_id: int
    attempt_id: int | None
    group_idx: int | None
    repo_id: str
    role: str
    canonical_path: str
    workspace_relative_path: str
    stage: str
    head_sha: str
    index_digest: str
    worktree_status_digest: str
    no_dirty: bool
    safety_status: str
    dirty_path_count: int
    dirty_path_sample: list[str] = Field(default_factory=list)
    forbidden_path_count: int
    forbidden_path_sample: list[str] = Field(default_factory=list)
    captured_at: datetime

    @field_validator("dirty_path_count", "forbidden_path_count")
    @classmethod
    def _counts_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("workspace snapshot path counts must be >= 0")
        return value


class TypedFailureSummary(BaseModel):
    """A bounded summary of one typed failure / route decision.

    Physical backing: ``evidence_nodes`` (doc-10 logical name
    ``typed_failures``). ``failure_class`` mirrors the Slice-07
    ``failure_router.FailureClass`` taxonomy; ``route`` mirrors
    ``failure_router.RouteAction``. ``summary`` is a bounded preview, never the
    full failure context body.
    """

    failure_id: int
    attempt_id: int | None
    evidence_id: int | None
    failure_class: str
    failure_type: str
    severity: Literal["info", "warning", "error", "fatal"]
    deterministic: bool
    operator_required: bool
    retryable: bool
    status: Literal["open", "routed", "retrying", "resolved", "suppressed"]
    route: str
    signature_hash: str
    summary: str = ""
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    created_at: datetime
    resolved_at: datetime | None


class MergeQueueSummary(BaseModel):
    """A bounded summary of one ``merge_queue_items`` row.

    Mirrors the Slice-08 ``MergeQueueStatus`` state machine. ``result_commit``
    is the commit sha (already a digest); ``required_gate_evidence_ids`` are
    cited ``evidence_nodes`` ids — no artifact bodies are read.
    """

    item_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    repo_id: str
    status: Literal[
        "queued", "leased", "applying", "verifying", "committing",
        "integrated", "checkpointing", "done", "failed", "poisoned",
        "cancelled",
    ]
    priority: int
    lease_owner: str | None
    leased_until: datetime | None
    lease_version: int
    result_commit: str = ""
    failure_id: int | None
    required_gate_evidence_ids: list[int] = Field(default_factory=list)
    updated_at: datetime


class RetryBudgetSummary(BaseModel):
    """A bounded summary of one route/retry budget decision.

    Physical backing: the retry-budget payload carried on the typed failure /
    attempt rows (doc-10 logical name ``failure_route_budgets``).
    """

    scope: Literal["feature", "group", "failure_signature", "route"]
    group_idx: int | None
    route: str
    failure_signature_hash: str | None
    budget_total: int
    budget_used: int
    budget_remaining: int
    terminal_reason: str = ""

    @field_validator("budget_total", "budget_used", "budget_remaining")
    @classmethod
    def _budget_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("retry budget counters must be >= 0")
        return value


class GateStatusSummary(BaseModel):
    """A bounded summary of one gate / aggregate-verdict node.

    Physical backing: ``evidence_nodes`` (gate kinds). ``evidence_id`` is the
    cited node id; a checkpoint display that lacks gate evidence is rejected by
    the consumer (doc 10 § "Tests": "reject checkpoint display when gate
    evidence is missing").
    """

    gate_name: str
    group_idx: int | None
    approved: bool
    deterministic: bool
    evidence_id: int
    failure_id: int | None
    created_at: datetime


class SandboxLeaseSummary(BaseModel):
    """A bounded summary of one ``sandbox_leases`` row.

    ``patch_summary_ids`` are cited ``evidence_nodes`` (``sandbox_patch_summary``
    kind) ids — the actual captured patch/diff body is never surfaced here.
    """

    lease_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    mode: str
    status: str
    sandbox_root: str
    patch_summary_ids: list[int] = Field(default_factory=list)
    leased_until: datetime | None
    updated_at: datetime


class RuntimeBindingSummary(BaseModel):
    """A bounded summary of one ``runtime_workspace_bindings`` row."""

    binding_id: int
    sandbox_lease_id: int
    attempt_id: int
    runtime_name: str
    status: str
    cwd: str
    updated_at: datetime


# ── The snapshot contract (doc 10 § "Proposed Interfaces/Types") ───────────


class ControlPlaneSnapshot(BaseModel):
    """The single shared, bounded, versioned control-plane status contract.

    doc 10 § "Proposed Interfaces/Types" / "Snapshot contract invariants".
    Built by ``ExecutionControlStore.get_control_plane_snapshot``; consumed by
    the dashboard, MCP, supervisor classification, Slack digest generation, and
    the public-dashboard projection.

    Every field is SUMMARY-ONLY (ids / digests / counts / bounded samples /
    citations). ``truncated`` + ``omitted_counts`` + ``cursors`` carry bounded
    truncation metadata so a consumer can display degraded/partial state.
    """

    feature_id: str
    snapshot_version: str
    generated_at: datetime
    source: SnapshotSource
    degraded: bool = False
    degradation_reasons: list[str] = Field(default_factory=list)
    truncated: bool = False
    omitted_counts: dict[str, int] = Field(default_factory=dict)
    cursors: list[SnapshotCursor] = Field(default_factory=list)
    active_group_idx: int | None
    active_attempts: list[ExecutionAttemptSummary] = Field(default_factory=list)
    workspace_snapshots: list[WorkspaceSnapshotSummary] = Field(default_factory=list)
    latest_failures: list[TypedFailureSummary] = Field(default_factory=list)
    merge_queue: list[MergeQueueSummary] = Field(default_factory=list)
    retry_budgets: list[RetryBudgetSummary] = Field(default_factory=list)
    sandbox_leases: list[SandboxLeaseSummary] = Field(default_factory=list)
    runtime_bindings: list[RuntimeBindingSummary] = Field(default_factory=list)
    gates: list[GateStatusSummary] = Field(default_factory=list)
    checkpoints: list[EvidenceRef] = Field(default_factory=list)
    recommended_route: str = ""
    recommended_action: SnapshotRecommendedAction = "observe"
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _degraded_implies_reasons(self) -> "ControlPlaneSnapshot":
        # Fail-fast / no silent degradation: a degraded snapshot must say WHY,
        # and a snapshot carrying degradation reasons must be flagged degraded.
        if self.degraded and not self.degradation_reasons:
            raise ValueError(
                "degraded ControlPlaneSnapshot must carry degradation_reasons"
            )
        if self.degradation_reasons and not self.degraded:
            object.__setattr__(self, "degraded", True)
        # A truncated snapshot must record which lists were truncated.
        if self.truncated and not self.omitted_counts:
            raise ValueError(
                "truncated ControlPlaneSnapshot must carry omitted_counts"
            )
        return self


class SupervisorDigest(BaseModel):
    """A bounded supervisor classification derived from a control-plane
    snapshot (doc 10 § "Proposed Interfaces/Types").

    The typed classifier mapping that *produces* a :class:`SupervisorDigest`
    from a :class:`ControlPlaneSnapshot` is a later Slice 10 sub-slice; 10a
    only delivers the contract. ``snapshot_version`` records the exact snapshot
    state the classification was derived from (audit replay). ``slack_dedupe_key``
    seeds the Slice-10 Slack dedupe store.
    """

    feature_id: str
    group_idx: int | None
    snapshot_version: str
    classification: SupervisorClassification
    confidence: float
    facts: list[str] = Field(default_factory=list)
    inference: str = ""
    recommended_action: SnapshotRecommendedAction
    recommended_route: str = ""
    failure_signature_hashes: list[str] = Field(default_factory=list)
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    slack_dedupe_key: str
    suppress_until: datetime | None = None

    @field_validator("confidence")
    @classmethod
    def _confidence_in_unit_range(cls, value: float) -> float:
        if not 0.0 <= value <= 1.0:
            raise ValueError("supervisor digest confidence must be within [0, 1]")
        return value


# ── Deterministic snapshot version digest ──────────────────────────────────


def control_plane_snapshot_version(cursors: list[SnapshotCursor]) -> str:
    """Return the stable snapshot version digest over typed table cursors.

    doc 10 § "Proposed Interfaces/Types": the version is "a stable digest over
    max ids and max ``updated_at`` values from ``execution_attempts``,
    ``typed_failures``, ``failure_route_budgets``, ``merge_queue_items``,
    ``evidence_nodes``, ``workspace_snapshots``, ``sandbox_leases``, and
    ``runtime_workspace_bindings``. It must not hash artifact bodies."

    The digest is taken over the SORTED ``(table, max_id, max_updated_at)``
    triples only — so it is order-independent and cheap, and a budget-only or
    sandbox-only update advances the version even when an underlying failure
    row does not change (doc 10's explicit requirement).

    ``stable_digest`` is imported lazily from ``execution_control.models`` so
    this module stays a leaf import (no ``execution_control`` -> ``snapshots``
    edge becomes a cycle; mirrors the Slice 09a ``regroup_overlay`` lazy-import
    discipline).
    """

    from ....execution_control.models import stable_digest

    material = sorted(
        (
            cursor.table,
            int(cursor.max_id),
            cursor.max_updated_at.isoformat()
            if cursor.max_updated_at is not None
            else "",
        )
        for cursor in cursors
    )
    return stable_digest(material)
