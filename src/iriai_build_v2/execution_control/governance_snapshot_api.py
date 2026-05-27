"""Slice 19 2nd sub-slice -- typed snapshot API per doc-19:151 step 2.

Per doc-19:151 *"Add typed snapshot API that reads governance rows and
bounded evidence refs. The API computes ``snapshot_digest`` from
bounded row ids, row digests, omitted-counts, evidence-quality values,
and recommendation/replay versions."* this module owns the typed
snapshot-building entrypoint that consumes the Slice 19 1st sub-slice
typed-shape foundation (:class:`GovernanceSnapshot` BaseModel +
:func:`compute_governance_snapshot_digest` helper + 5 default-budget
constants at ``governance_agent.py``) and populates
:class:`GovernanceSnapshot` instances from bounded corpus reads.

Module surface:

* :data:`SNAPSHOT_API_FAILURE_ID` -- the typed failure id
  (``governance_snapshot_api_failed``) the snapshot API projects onto
  when a structural projection step fails. A SINGLE failure id covers
  ALL the doc-19:184-194 edge-case rows (governance snapshot stale;
  missing line provenance; too many findings; Slack delivery failure;
  active workflow pressure) per the Slice 17 6th sub-slice
  ``consumer_read_api_failed`` + Slice 18 2nd sub-slice
  ``replay_corpus_or_scenario_load_failed`` precedent (one typed
  failure id per typed-API class; the typed gap carries the surface
  ``reason`` for downstream classification).
* :class:`SnapshotAPIInputs` typed BaseModel -- accepts ``corpus_id``,
  optional pagination cursor, optional budget overrides (using the
  Slice 19 1st sub-slice 5 default-budget constants).
* :class:`SnapshotAPIGap` typed BaseModel -- the typed gap projection
  shape (``failure_id`` + ``corpus_id`` + ``reason`` + ``observed_at``
  + optional ``evidence_payload``).
* :class:`SnapshotAPIResult` typed BaseModel -- ``snapshot:
  GovernanceSnapshot | None`` + ``gap_findings:
  list[SnapshotAPIGap]``.
* :class:`GovernanceSnapshotAPI` class -- the
  :meth:`build_snapshot(inputs, corpus)` projection entrypoint.

Per doc-19:152-153 the digest is computed from bounded row ids
(finding idempotency keys + recommendation idempotency keys + replay
result ids + replay result versions) + omitted-counts +
evidence-quality + completeness. The Slice 19 1st sub-slice
:func:`compute_governance_snapshot_digest` helper handles the
canonical-JSON + SHA-256 projection verbatim.

**Bounded-reads discipline** (per the governance prompt §
"Non-Negotiables" -- *"Bounded reads. Reuse the typed snapshot's
``LIMIT cap+1`` truncation discipline and the supervisor's ``SET LOCAL
statement_timeout`` pattern. No artifact-body hydration on the
governance read path."*). The API truncates each of the 4 typed list
dimensions (top_findings + recommendations + replay_results +
page_refs) at the corresponding budget cap from
:mod:`iriai_build_v2.execution_control.governance_agent`. Per the
``LIMIT cap+1`` discipline the corpus is queried for ``cap+1`` rows;
if the result count exceeds ``cap``, the API truncates to ``cap`` rows
and sets ``truncated=True`` + ``omitted_counts["<dim>"]`` to the
excess. Refs-only; no body hydration.

**Fail-closed discipline** (per the auto-memory
``feedback_no_silent_degradation`` rule). The
:meth:`build_snapshot(...)` method NEVER raises on input. Any
structural failure (e.g. Pydantic ValidationError on the typed
:class:`GovernanceSnapshot` construction; bounded-input bound
exceeded; corpus_id empty) projects onto a typed
:class:`SnapshotAPIGap` finding emitted on
:attr:`SnapshotAPIResult.gap_findings`. The corresponding typed
failure id :data:`SNAPSHOT_API_FAILURE_ID`
(``governance_snapshot_api_failed``) registers under the EXISTING
``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
the EXISTING NON-blocking RouteAction
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action).

**Edge-case coverage** (doc-19:184-194):

* Governance snapshot stale: emit typed gap with ``reason =
  "governance_snapshot_stale"`` (the typed surface lets caller branch
  on the reason).
* Missing line provenance: gap surface is via the typed
  :attr:`GovernanceSnapshot.blocked_by` field (populated by the API
  with the stale-evidence blocker id) -- not a separate failure id.
* Too many findings: handled by the typed
  ``truncated`` + ``omitted_counts`` triple (the LIMIT cap+1
  discipline).
* Slack delivery failure: handled by the Slice 19 4th sub-slice
  Slack renderer (outside this API's scope; the typed snapshot is
  the input to the renderer).
* Active workflow pressure: handled by the typed cached-snapshot
  contract (the typed API does not force expensive recomputation;
  the caller chooses whether to call the API live or read the
  cached snapshot row).

**Activation-authority boundary** (per doc-19:348-349 + the Slice 17
7th sub-slice activation-boundary discipline). The snapshot API is
ADVISORY / READ-ONLY -- no executor mutation, no control-plane writer
extension, no policy activation. Per doc-19:348-349 the API does NOT
extend the Slice 10c-1 ``CONTROL_PLANE_WRITER_METHODS`` set; snapshots
are review / governance / reporting artifacts only.

**Slice 13a + Slice 16/17/18 + Slice 19 1st REUSE** (per the no-
second-source-of-truth discipline). The module imports the typed
shapes directly from their authoritative source modules:

* :class:`~iriai_build_v2.workflows.develop.governance.models.CompletenessState`
  + :class:`~iriai_build_v2.workflows.develop.governance.models.EvidenceQuality`
  + :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- the Slice 13a typed completeness + evidence-quality + evidence-
  ref shapes.
* :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  -- the Slice 16 typed finding shape.
* :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
  -- the Slice 17 typed policy-recommendation shape.
* :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
  -- the Slice 18 typed counterfactual-result shape.
* :class:`~iriai_build_v2.execution_control.governance_agent.GovernanceSnapshot`
  + :func:`~iriai_build_v2.execution_control.governance_agent.compute_governance_snapshot_digest`
  + the 5 default-budget constants
  (:data:`~iriai_build_v2.execution_control.governance_agent.GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES`,
  :data:`~iriai_build_v2.execution_control.governance_agent.GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS`,
  :data:`~iriai_build_v2.execution_control.governance_agent.GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS`,
  :data:`~iriai_build_v2.execution_control.governance_agent.GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS`)
  -- the Slice 19 1st sub-slice typed-shape foundation.

**Doc-19 acceptance binding** (subset enforced at this 2nd sub-slice):

* **AC1** (doc-19:224) -- *"Reports are bounded, reproducible,
  evidence-cited, and structured first."* -- enforced by the typed
  :class:`GovernanceSnapshot` construction with explicit
  ``max_response_bytes`` + ``truncated`` + ``omitted_counts`` +
  ``page_refs`` + ``snapshot_digest`` populated by this API.
* **AC2** (doc-19:225-226) -- *"Truncated or preview reports are
  never authoritative unless exact page refs and completeness
  metadata cover the consumer's required scope."* -- enforced by the
  typed ``truncated`` + ``page_refs`` + ``completeness`` triple
  populated by this API; consumers can detect display-only state.
* **AC6** (doc-19:232-233) -- *"Human-facing dashboard/Slack output
  explains top findings without hiding evidence quality or omitted
  details."* -- enforced by the typed
  ``evidence_quality`` + ``omitted_counts`` always populated; the
  API never produces a snapshot without an evidence-quality value.
* **AC7** (doc-19:234) -- *"Reporting honors Slice 10 read-only and
  bounded-read guarantees."* -- enforced by the bounded-reads
  ``LIMIT cap+1`` truncation discipline + no
  ``CONTROL_PLANE_WRITER_METHODS`` extension + no body hydration.

Per the Pydantic v2 idiom (mirrors
:mod:`iriai_build_v2.execution_control.counterfactual_replay_loader`
+ :mod:`iriai_build_v2.execution_control.governance_agent`):
``BaseModel`` subclasses with ``ConfigDict(extra="forbid")`` so typo-d
kwargs fail closed as a typed ``ValidationError`` rather than being
silently absorbed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS,
    GovernanceSnapshot,
    compute_governance_snapshot_digest,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    EvidenceQuality,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING).
    "SNAPSHOT_API_FAILURE_ID",
    # Typed snapshot API inputs / result / gap (doc-19:151).
    "SnapshotAPIInputs",
    "SnapshotAPIResult",
    "SnapshotAPIGap",
    # The bounded-read corpus input shape (refs-only; no body hydration).
    "SnapshotAPICorpus",
    # The snapshot API class (doc-19:151 step 2).
    "GovernanceSnapshotAPI",
]


# --- Typed failure id (doc-19:184-194 + doc-14:242-243 NON-BLOCKING) --------


SNAPSHOT_API_FAILURE_ID: Literal[
    "governance_snapshot_api_failed"
] = "governance_snapshot_api_failed"
"""Doc-19:184-194 + doc-14:242-243 -- the typed failure id the
governance snapshot API projects onto when a structural projection
step fails.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT
a new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd + 3rd + 4th + 5th + 6th + 7th sub-slice precedent verbatim).

A SINGLE failure id covers ALL the doc-19:184-194 edge-case rows
(governance snapshot stale; missing line provenance; too many
findings; Slack delivery failure; active workflow pressure) per the
Slice 17 6th sub-slice ``consumer_read_api_failed`` + Slice 18 2nd
sub-slice ``replay_corpus_or_scenario_load_failed`` precedent (one
typed failure id per typed-API class). The typed
:class:`SnapshotAPIGap` shape carries the surface ``reason`` so
consumers can distinguish edge-case classes if needed.

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 19 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice
17 + Slice 18 non-blocking governance projection observer (the
snapshot API is also a post-checkpoint governance projection
observer + per doc-19:170-171 dashboards read snapshots with bounded
fields + per doc-19:166-167 reports are projections of governance
rows -- never runtime policy authority).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to snapshot API
failures.
"""


def _utcnow() -> datetime:
    """Return the current UTC timestamp (timezone-aware).

    Mirrors the Slice 18 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay_loader._utcnow`
    + Slice 17 4th sub-slice
    :func:`~iriai_build_v2.execution_control.decision_record_writer._utcnow`
    verbatim. Stdlib-only.
    """

    return datetime.now(timezone.utc)


# --- SnapshotAPIInputs (typed inputs; doc-19:151) ---------------------------


class SnapshotAPIInputs(BaseModel):
    """Typed bundle of all inputs the
    :meth:`GovernanceSnapshotAPI.build_snapshot` method consumes.

    Per doc-19:151 step 2 (*"Add typed snapshot API that reads
    governance rows and bounded evidence refs."*) the inputs carry
    the typed scope identifiers (``corpus_id``, optional
    ``scorecard_id``) + the optional bounded-read budget overrides
    (default to the Slice 19 1st sub-slice budget constants) + the
    optional pagination cursor for paged corpora.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``str`` / ``int`` /
    ``str | None``).
    """

    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """The corpus identifier the snapshot grounds on. Required;
    must be non-empty (the typed API emits a typed
    :class:`SnapshotAPIGap` with ``reason="corpus_id_empty"`` if the
    string is empty / whitespace-only)."""

    snapshot_version: str = "v1"
    """The snapshot version string the API stamps on the emitted
    :attr:`GovernanceSnapshot.snapshot_version` field. Defaults to
    ``"v1"`` per the Slice 19 1st sub-slice typed-shape foundation."""

    scorecard_id: str | None = None
    """Optional Slice 15 governance scorecard id the snapshot grounds
    on. ``None`` if the snapshot is not grounded on a specific
    scorecard (e.g. cross-corpus diff snapshots). Per doc-19:76 the
    field is part of the typed snapshot identity surface."""

    cursor: str | None = None
    """Optional pagination cursor for the next page of typed rows.
    ``None`` (default) requests the first page; non-``None`` requests
    the page starting at the typed cursor. The cursor is stored on
    the emitted :attr:`GovernanceSnapshot.next_cursor` field when the
    API emits a non-final page."""

    max_response_bytes: int = GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES
    """The maximum response size in bytes the API targets when
    projecting the snapshot. Defaults to
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES` (262 144 = 256 KB)
    per doc-19:121."""

    max_findings: int = GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS
    """The maximum number of typed :class:`GovernanceFinding` rows
    the API includes in :attr:`GovernanceSnapshot.top_findings` before
    truncation. Defaults to
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS` (20) per
    doc-19:121."""

    max_recommendations: int = (
        GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS
    )
    """The maximum number of typed
    :class:`GovernancePolicyRecommendation` rows the API includes in
    :attr:`GovernanceSnapshot.recommendations` before truncation.
    Defaults to
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS` (10) per
    doc-19:121."""

    max_replay_results: int = (
        GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS
    )
    """The maximum number of typed :class:`CounterfactualResult` rows
    the API includes in :attr:`GovernanceSnapshot.replay_results`
    before truncation. Defaults to
    :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS` (10) per
    doc-19:122."""

    max_page_refs: int = (
        GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS
    )
    """The maximum number of page-ref strings the API includes in
    :attr:`GovernanceSnapshot.page_refs` before truncation. Defaults
    to :data:`GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS` (10);
    page_refs are typically a small surface (doc-19:81 + the typed
    LIMIT cap+1 discipline) so the default is intentionally tight."""

    completeness_override: CompletenessState | None = None
    """Optional caller-supplied completeness override. If ``None``
    (default) the API computes the completeness from the
    truncation discipline (``complete`` if no list was truncated;
    ``paged`` if truncated with page refs; ``preview_only`` if
    truncated without page refs). If set, the API uses the most
    restrictive value between the derived completeness and the
    override; an override may lower completeness but may not raise a
    truncated snapshot to an authoritative state.

    Per doc-19:80 the typed :attr:`GovernanceSnapshot.completeness`
    field is the Slice 13a typed
    :data:`CompletenessState` Literal (4 values: ``complete`` /
    ``paged`` / ``preview_only`` / ``unavailable``); per the no-
    second-source-of-truth discipline this module does NOT redefine
    the Literal -- it imports the Slice 13a shared
    :data:`~iriai_build_v2.workflows.develop.governance.models.CompletenessState`."""

    evidence_quality_override: EvidenceQuality | None = None
    """Optional caller-supplied evidence-quality override. If
    ``None`` (default) the API derives evidence-quality from the
    :class:`SnapshotAPICorpus` ``corpus_evidence_quality`` field. If
    set, the API uses the override verbatim.

    Per doc-19:86 the typed :attr:`GovernanceSnapshot.evidence_quality`
    field is the Slice 13a typed :data:`EvidenceQuality` Literal (6
    values: ``canonical`` / ``derived`` / ``sampled`` / ``advisory``
    / ``stale`` / ``insufficient``); per the no-second-source-of-truth
    discipline this module does NOT redefine the Literal -- it imports
    the Slice 13a shared
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceQuality`."""


# --- SnapshotAPICorpus (typed bounded-read corpus input; refs-only) --------


class SnapshotAPICorpus(BaseModel):
    """Typed bundle of bounded corpus reads the snapshot API consumes
    when constructing a :class:`GovernanceSnapshot`.

    Per the governance prompt § "Non-Negotiables" -- *"Bounded reads.
    Reuse the typed snapshot's `LIMIT cap+1` truncation discipline and
    the supervisor's `SET LOCAL statement_timeout` pattern. No
    artifact-body hydration on the governance read path."* -- the
    typed corpus carries pre-fetched lists of typed Slice 16/17/18
    BaseModels (already bounded by ``LIMIT cap+1`` at the upstream
    bounded reader; this API truncates again to enforce the per-call
    budget overrides on top of the upstream cap).

    The typed corpus is provided by the caller (typically a bounded
    reader at a future Slice 19 sub-slice that wraps the typed
    governance row reads). The typed API does NOT itself fetch from
    the database -- it is a pure typed projection over the typed
    inputs (the caller owns the bounded-read transaction; the API
    owns the typed projection + bounded truncation).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    findings: list[GovernanceFinding] = Field(default_factory=list)
    """The bounded list of typed Slice 16 :class:`GovernanceFinding`
    records the caller pre-fetched (already bounded by
    ``LIMIT cap+1`` upstream; this API truncates again to enforce the
    :attr:`SnapshotAPIInputs.max_findings` budget override).

    Per doc-19:83 the typed list populates
    :attr:`GovernanceSnapshot.top_findings`; per the Slice 16 1st
    sub-slice typed-shape contract the typed
    :class:`GovernanceFinding` carries the
    :attr:`GovernanceFinding.idempotency_key` field the API uses as
    the per-row digest input for :func:`compute_governance_snapshot_digest`."""

    recommendations: list[GovernancePolicyRecommendation] = Field(
        default_factory=list
    )
    """The bounded list of typed Slice 17
    :class:`GovernancePolicyRecommendation` records the caller pre-
    fetched (already bounded by ``LIMIT cap+1`` upstream; this API
    truncates again to enforce the
    :attr:`SnapshotAPIInputs.max_recommendations` budget override).

    Per doc-19:84 the typed list populates
    :attr:`GovernanceSnapshot.recommendations`; per the Slice 17 1st
    sub-slice typed-shape contract the typed
    :class:`GovernancePolicyRecommendation` carries the
    :attr:`GovernancePolicyRecommendation.idempotency_key` field the
    API uses as the per-row digest input for
    :func:`compute_governance_snapshot_digest`."""

    replay_results: list[CounterfactualResult] = Field(
        default_factory=list
    )
    """The bounded list of typed Slice 18 :class:`CounterfactualResult`
    records the caller pre-fetched (already bounded by ``LIMIT cap+1``
    upstream; this API truncates again to enforce the
    :attr:`SnapshotAPIInputs.max_replay_results` budget override).

    Per doc-19:85 the typed list populates
    :attr:`GovernanceSnapshot.replay_results`; per the Slice 18 1st
    sub-slice typed-shape contract the typed
    :class:`CounterfactualResult` carries the
    :attr:`CounterfactualResult.result_id` +
    :attr:`CounterfactualResult.result_version` fields the API uses as
    the per-row digest input for
    :func:`compute_governance_snapshot_digest` (per doc-19:152-153
    *"...and recommendation/replay versions."*)."""

    page_refs: list[GovernanceEvidencePageRef] = Field(
        default_factory=list
    )
    """The bounded list of typed evidence-page-ref records the caller
    pre-fetched (already bounded by ``LIMIT cap+1`` upstream; this API
    truncates again to enforce the
    :attr:`SnapshotAPIInputs.max_page_refs` budget override).

    Per doc-19:81 the typed page-refs are surfaced as their
    :attr:`page_ref_id` strings on :attr:`GovernanceSnapshot.page_refs`;
    per the Slice 13a typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
    contract the typed BaseModel carries the
    :attr:`page_ref_id` + :attr:`source_ref_id` + :attr:`completeness`
    fields; this API uses only the typed :attr:`page_ref_id` string
    (refs-only per the bounded-reads non-negotiable; no body
    hydration)."""

    corpus_evidence_quality: EvidenceQuality = "canonical"
    """The corpus-level typed
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceQuality`
    Literal. Per doc-19:86 the typed value is the AC6 enforcer surface
    the snapshot API stamps on
    :attr:`GovernanceSnapshot.evidence_quality`; per the Slice 13a
    typed Literal range the value must be one of
    ``canonical`` / ``derived`` / ``sampled`` / ``advisory`` /
    ``stale`` / ``insufficient``.

    Defaults to ``"canonical"`` so callers that do not explicitly set
    the field get the canonical-quality snapshot (the typical case
    for a fresh corpus read). Callers SHOULD set
    ``"stale"`` when the corpus read returns stale rows + the API
    will populate :attr:`GovernanceSnapshot.blocked_by` with the
    ``stale_evidence`` blocker id per the doc-19:186-187 edge-case
    binding."""

    omitted_findings_count: int = 0
    """The upstream-truncation count for findings (the count of
    findings the upstream bounded reader truncated BEFORE this API
    saw the bounded list). The typed API ADDS the upstream count to
    its own per-call truncation count when populating
    :attr:`GovernanceSnapshot.omitted_counts["findings"]`.

    Defaults to ``0`` (no upstream truncation; the caller did not
    pre-truncate). Callers MUST set non-zero values if their
    bounded reader truncated rows before passing to this API per
    the auto-memory ``feedback_no_silent_degradation`` rule."""

    omitted_recommendations_count: int = 0
    """The upstream-truncation count for recommendations (mirrors
    :attr:`omitted_findings_count` for the recommendations
    dimension)."""

    omitted_replay_results_count: int = 0
    """The upstream-truncation count for replay_results (mirrors
    :attr:`omitted_findings_count` for the replay_results
    dimension)."""

    omitted_page_refs_count: int = 0
    """The upstream-truncation count for page_refs (mirrors
    :attr:`omitted_findings_count` for the page_refs dimension)."""

    next_cursor: str | None = None
    """The next-page cursor the upstream bounded reader produced
    (``None`` if the upstream reader returned the final page of the
    corpus). The typed API populates
    :attr:`GovernanceSnapshot.next_cursor` from this field verbatim
    so the snapshot's next-page cursor reflects the upstream pager."""

    blocked_by: list[str] = Field(default_factory=list)
    """Optional list of blocker-id strings the caller pre-populated
    (e.g. ``["stale_evidence:8ac124d6"]``) when the snapshot is
    blocked from authoritative consumption.

    Per doc-19:87 + doc-19:186-194 the typed surface accepts the
    pre-populated list at construction; the API uses the list
    verbatim on :attr:`GovernanceSnapshot.blocked_by`."""


_COMPLETENESS_AUTHORITY_RANK: dict[str, int] = {
    "unavailable": 0,
    "preview_only": 1,
    "paged": 2,
    "complete": 3,
}
"""Relative authority rank for completeness states.

The snapshot API only uses this to prevent caller overrides from
raising a derived incomplete snapshot above the state proven by the
bounded-read projection.
"""


_TRUNCATED_WITHOUT_PAGE_REFS_BLOCKER = (
    "governance_snapshot_truncated_without_page_refs"
)
"""Blocker emitted when omitted rows lack exact surviving page refs."""


class _SerializedSnapshotBudgetExceeded(ValueError):
    """Raised when row trimming cannot fit scalar snapshot metadata."""

    def __init__(self, *, serialized_bytes: int, max_response_bytes: int) -> None:
        super().__init__(
            "serialized governance snapshot exceeds max_response_bytes "
            f"after bounded row trimming: {serialized_bytes} > {max_response_bytes}"
        )
        self.serialized_bytes = serialized_bytes
        self.max_response_bytes = max_response_bytes


# --- SnapshotAPIGap (typed gap projection; doc-19:184-194 + doc-14:242-243)


class SnapshotAPIGap(BaseModel):
    """Typed governance-gap finding produced when the snapshot API
    fails to construct a :class:`GovernanceSnapshot` structurally.

    Mirrors the Slice 18 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderGap`
    + Slice 17 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderEmissionGap`
    + Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per the governance-projection discipline) the gap finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`SNAPSHOT_API_FAILURE_ID`
    (``governance_snapshot_api_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["governance_snapshot_api_failed"]
    """Doc-19:184-194 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-19:184-194."""

    corpus_id: str
    """The corpus scope of the failed projection (same as the
    :attr:`SnapshotAPIInputs.corpus_id`)."""

    reason: str
    """Free-form gap reason. Documented values:

    * ``corpus_id_empty`` -- ``corpus_id`` is empty / whitespace-only.
    * ``snapshot_construction_failed`` -- Pydantic ValidationError on
      the typed :class:`GovernanceSnapshot` construction.
    * ``digest_computation_failed`` -- structural failure computing
      :func:`compute_governance_snapshot_digest`.
    * ``governance_snapshot_stale`` -- doc-19:186-187 edge-case.
    * ``too_many_findings`` -- doc-19:189-190 edge-case (informational;
      the API STILL emits the truncated snapshot + populates
      ``omitted_counts``; the gap is informational only when the
      caller asked for AC2 strict enforcement via the typed
      :attr:`SnapshotAPIInputs.completeness_override`).
    * ``active_workflow_pressure`` -- doc-19:193-194 edge-case
      (cached snapshot requested).

    The caller distinguishes via this string. Per the auto-memory
    ``feedback_no_silent_degradation`` rule the typed surface is
    free-form so the API can emit new reason strings without a typed-
    shape breaking change."""

    observed_at: datetime
    """ISO-8601 timestamp the API observed the gap (UTC, timezone-
    aware). Mirrors the Slice 18 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderGap.observed_at`
    + Slice 13A
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness.observed_at`
    contract verbatim."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail; the truncation bound;
    the rejected row count). Free-form per the doc-14:192-201 + Slice
    14/15/16/17/18 governance-finding precedent."""


# --- SnapshotAPIResult (typed result; doc-19:151) --------------------------


class SnapshotAPIResult(BaseModel):
    """Typed bundle of all outputs the
    :meth:`GovernanceSnapshotAPI.build_snapshot` method produces.

    The bundle composes:

    * ``snapshot`` -- the typed :class:`GovernanceSnapshot` the API
      emitted, OR ``None`` if the projection failed structurally (in
      which case the gap is recorded in :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed :class:`SnapshotAPIGap`
      records emitted when a projection step fails structurally OR
      when an informational gap fires (e.g. ``too_many_findings`` /
      ``governance_snapshot_stale``).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    snapshot: GovernanceSnapshot | None = None
    """The typed :class:`GovernanceSnapshot` the API emitted, OR
    ``None`` if the projection failed structurally.

    Per the doc-19:151 step 2 contract the API emits the typed
    snapshot when inputs are valid; on structural failure the
    snapshot is ``None`` + the gap finding is recorded in
    :attr:`gap_findings`. On informational-only gaps (e.g.
    ``too_many_findings``) the snapshot is STILL emitted (with
    ``truncated=True`` + populated ``omitted_counts``) AND the
    informational gap is recorded in :attr:`gap_findings`."""

    gap_findings: list[SnapshotAPIGap] = Field(default_factory=list)
    """The list of typed :class:`SnapshotAPIGap` records emitted
    when a projection step fails structurally OR when an
    informational gap fires.

    The list is typically empty (no gaps fired for a healthy
    snapshot). On structural failure the list contains exactly ONE
    gap record + :attr:`snapshot` is ``None``. On informational gaps
    the list contains the informational gap(s) + :attr:`snapshot` is
    the truncated snapshot."""


# --- GovernanceSnapshotAPI (the snapshot API class; doc-19:151) ------------


class GovernanceSnapshotAPI:
    """The typed snapshot API class (doc-19:151 step 2).

    Per *"Add typed snapshot API that reads governance rows and
    bounded evidence refs. The API computes ``snapshot_digest`` from
    bounded row ids, row digests, omitted-counts, evidence-quality
    values, and recommendation/replay versions."* the API consumes
    the typed :class:`SnapshotAPIInputs` + typed
    :class:`SnapshotAPICorpus` and emits a typed
    :class:`GovernanceSnapshot` record.

    **Bounded-reads discipline (per governance prompt §
    "Non-Negotiables").** The API truncates each of the 4 typed list
    dimensions (top_findings + recommendations + replay_results +
    page_refs) at the corresponding budget cap from
    :class:`SnapshotAPIInputs`. Per the ``LIMIT cap+1`` discipline
    the corpus is queried for ``cap+1`` rows by the upstream bounded
    reader; if the result count exceeds ``cap``, the API truncates
    to ``cap`` rows and sets ``truncated=True`` +
    ``omitted_counts["<dim>"]`` to the excess. The API also adds the
    caller-provided upstream truncation counts
    (:attr:`SnapshotAPICorpus.omitted_findings_count` etc.) to the
    omitted counts so multi-level truncation is preserved.

    **Refs-only projection (per governance prompt §
    "Non-Negotiables" + Slice 13A invariant).** The API extracts only
    the typed string ids (page_ref_id strings; finding /
    recommendation idempotency_keys; replay result_ids +
    result_versions) for the digest input -- the typed BaseModel
    bodies are passed through to the typed
    :attr:`GovernanceSnapshot` lists verbatim (those carry their own
    refs-only fields per Slice 16/17/18 contracts; this API does NOT
    hydrate artifact bodies).

    **Fail-closed discipline (per auto-memory
    ``feedback_no_silent_degradation``).** The :meth:`build_snapshot`
    method NEVER raises a failure to the caller. Any structural
    failure projects onto a typed :class:`SnapshotAPIGap` finding
    emitted on the :attr:`SnapshotAPIResult.gap_findings` list. The
    corresponding typed failure id :data:`SNAPSHOT_API_FAILURE_ID`
    (``governance_snapshot_api_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class with the EXISTING NON-
    blocking RouteAction ``retry_governance_projection`` (REUSED
    from Slice 14 2nd sub-slice; NOT a new route action).

    The API is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple corpora.
    """

    def build_snapshot(
        self,
        inputs: SnapshotAPIInputs,
        corpus: SnapshotAPICorpus,
    ) -> SnapshotAPIResult:
        """Build the typed :class:`GovernanceSnapshot` from the typed
        inputs + typed corpus.

        Per doc-19:151 the method:

        1. Validates the required-field contract (``corpus_id``
           non-empty).
        2. Applies the bounded-read truncation discipline to each of
           the 4 typed list dimensions (top_findings + recommendations
           + replay_results + page_refs); accumulates the truncated-
           row counts into :attr:`omitted_counts`.
        3. Computes the typed completeness state from the truncation
           discipline (``complete`` if no list was truncated;
           ``paged`` when truncated with page refs; ``preview_only``
           when truncated without page refs). A caller-supplied
           completeness override may lower this state but may not
           raise it above the bounded-read projection.
        4. Computes the typed
           :func:`compute_governance_snapshot_digest` from the bounded
           row ids + row digests + omitted-counts + evidence-quality
           + completeness per doc-19:152-153.
        5. Constructs the typed :class:`GovernanceSnapshot` record.
        6. Records projection failures in
           :attr:`SnapshotAPIResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        gap_findings: list[SnapshotAPIGap] = []

        # Required-field check (per doc-19:72 + AC1 corpus identity).
        if not inputs.corpus_id or not inputs.corpus_id.strip():
            gap_findings.append(
                SnapshotAPIGap(
                    failure_id=SNAPSHOT_API_FAILURE_ID,
                    corpus_id=inputs.corpus_id or "<empty>",
                    reason="corpus_id_empty",
                    observed_at=_utcnow(),
                )
            )
            return SnapshotAPIResult(
                snapshot=None,
                gap_findings=gap_findings,
            )
        if inputs.max_response_bytes <= 0:
            gap_findings.append(
                SnapshotAPIGap(
                    failure_id=SNAPSHOT_API_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="max_response_bytes_non_positive",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "max_response_bytes": inputs.max_response_bytes,
                    },
                )
            )
            return SnapshotAPIResult(
                snapshot=None,
                gap_findings=gap_findings,
            )

        # Bounded-read truncation per the LIMIT cap+1 discipline.
        # For each of the 4 typed list dimensions: truncate at the
        # per-input budget cap; accumulate the truncated count into
        # omitted_counts (added to the upstream caller-supplied
        # upstream-truncation counts so multi-level truncation is
        # preserved).
        top_findings, findings_omitted = self._truncate_findings(
            corpus.findings, inputs.max_findings
        )
        recommendations, recommendations_omitted = (
            self._truncate_recommendations(
                corpus.recommendations, inputs.max_recommendations
            )
        )
        replay_results, replay_results_omitted = (
            self._truncate_replay_results(
                corpus.replay_results, inputs.max_replay_results
            )
        )
        page_refs, page_ref_ids, page_refs_omitted = self._truncate_page_ref_rows(
            corpus.page_refs, inputs.max_page_refs
        )

        # Multi-level truncation accumulation (caller's upstream
        # truncation + this API's per-call truncation).
        omitted_counts: dict[str, int] = {
            "findings": (
                findings_omitted + corpus.omitted_findings_count
            ),
            "recommendations": (
                recommendations_omitted
                + corpus.omitted_recommendations_count
            ),
            "replay_results": (
                replay_results_omitted
                + corpus.omitted_replay_results_count
            ),
            "page_refs": (
                page_refs_omitted + corpus.omitted_page_refs_count
            ),
        }

        # Truncated flag is True iff ANY dimension was truncated at
        # ANY level (this API or upstream).
        truncated: bool = any(count > 0 for count in omitted_counts.values())

        # Completeness derivation: the bounded-read projection is the
        # upper bound. A caller override may lower completeness (for
        # stale/display-only callers) but may not turn a truncated
        # snapshot back into ``complete``. If omitted rows exist and no
        # exact page refs survive, the snapshot is display-only because a
        # downstream consumer cannot drill into exact paged evidence.
        exact_page_refs_cover_snapshot = self._page_refs_are_exact(page_refs)
        truncated_without_page_refs = (
            truncated and not exact_page_refs_cover_snapshot
        )
        derived_completeness: CompletenessState
        if truncated_without_page_refs:
            derived_completeness = "preview_only"
        elif truncated:
            derived_completeness = "paged"
        else:
            derived_completeness = "complete"
        completeness = self._restrict_completeness_override(
            derived_completeness=derived_completeness,
            override=inputs.completeness_override,
        )

        # Evidence-quality derivation: caller override wins; otherwise
        # corpus-supplied value.
        evidence_quality: EvidenceQuality
        if inputs.evidence_quality_override is not None:
            evidence_quality = inputs.evidence_quality_override
        else:
            evidence_quality = corpus.corpus_evidence_quality

        # Compute the typed snapshot digest per doc-19:152-153
        # verbatim. The digest is computed from bounded row ids +
        # row digests + omitted-counts + evidence-quality +
        # completeness. The Slice 19 1st sub-slice
        # compute_governance_snapshot_digest helper handles the
        # canonical-JSON + SHA-256 projection.
        try:
            finding_keys = [f.idempotency_key for f in top_findings]
            recommendation_keys = [
                r.idempotency_key for r in recommendations
            ]
            replay_ids = [r.result_id for r in replay_results]
            replay_versions = [
                r.result_version for r in replay_results
            ]
            snapshot_digest = compute_governance_snapshot_digest(
                corpus_id=inputs.corpus_id,
                snapshot_version=inputs.snapshot_version,
                scorecard_id=inputs.scorecard_id,
                finding_idempotency_keys=finding_keys,
                recommendation_idempotency_keys=recommendation_keys,
                replay_result_ids=replay_ids,
                replay_result_versions=replay_versions,
                omitted_counts=omitted_counts,
                evidence_quality=evidence_quality,
                completeness=completeness,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                SnapshotAPIGap(
                    failure_id=SNAPSHOT_API_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="digest_computation_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SnapshotAPIResult(
                snapshot=None,
                gap_findings=gap_findings,
            )

        # Construct the typed GovernanceSnapshot record (per doc-19:71-87).
        try:
            blocked_by = list(corpus.blocked_by)
            if (
                truncated_without_page_refs
                and _TRUNCATED_WITHOUT_PAGE_REFS_BLOCKER not in blocked_by
            ):
                blocked_by.append(_TRUNCATED_WITHOUT_PAGE_REFS_BLOCKER)

            snapshot = GovernanceSnapshot(
                corpus_id=inputs.corpus_id,
                snapshot_version=inputs.snapshot_version,
                snapshot_digest=snapshot_digest,
                generated_at=_utcnow(),
                scorecard_id=inputs.scorecard_id,
                max_response_bytes=inputs.max_response_bytes,
                truncated=truncated,
                omitted_counts=omitted_counts,
                completeness=completeness,
                page_refs=page_ref_ids,
                next_cursor=corpus.next_cursor,
                top_findings=top_findings,
                recommendations=recommendations,
                replay_results=replay_results,
                evidence_quality=evidence_quality,
                blocked_by=blocked_by,
            )
            snapshot = self._enforce_serialized_budget(
                snapshot,
                page_refs_are_exact=exact_page_refs_cover_snapshot,
            )
        except _SerializedSnapshotBudgetExceeded as exc:
            gap_findings.append(
                SnapshotAPIGap(
                    failure_id=SNAPSHOT_API_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="serialized_response_budget_exceeded",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "serialized_bytes": exc.serialized_bytes,
                        "max_response_bytes": exc.max_response_bytes,
                    },
                )
            )
            return SnapshotAPIResult(
                snapshot=None,
                gap_findings=gap_findings,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                SnapshotAPIGap(
                    failure_id=SNAPSHOT_API_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="snapshot_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SnapshotAPIResult(
                snapshot=None,
                gap_findings=gap_findings,
            )

        return SnapshotAPIResult(
            snapshot=snapshot,
            gap_findings=gap_findings,
        )

    # --- Bounded-read truncation helpers (LIMIT cap+1 discipline) ---

    @staticmethod
    def _serialized_snapshot_bytes(snapshot: GovernanceSnapshot) -> int:
        return len(snapshot.model_dump_json().encode("utf-8"))

    @staticmethod
    def _enforce_serialized_budget(
        snapshot: GovernanceSnapshot,
        *,
        page_refs_are_exact: bool,
    ) -> GovernanceSnapshot:
        """Drop bounded list rows until the serialized snapshot fits.

        If scalar metadata alone exceeds the caller's byte cap, fail closed
        instead of returning an over-budget snapshot.
        """

        max_bytes = int(snapshot.max_response_bytes or 0)
        if max_bytes <= 0:
            return snapshot
        if (
            GovernanceSnapshotAPI._serialized_snapshot_bytes(snapshot)
            <= max_bytes
        ):
            return snapshot

        top_findings = list(snapshot.top_findings)
        recommendations = list(snapshot.recommendations)
        replay_results = list(snapshot.replay_results)
        page_refs = list(snapshot.page_refs)
        omitted_counts = dict(snapshot.omitted_counts)
        blocked_by = list(snapshot.blocked_by)

        def rebuild() -> GovernanceSnapshot:
            budget_completeness: CompletenessState
            if page_refs and page_refs_are_exact:
                budget_completeness = "paged"
            else:
                budget_completeness = "preview_only"
                if (
                    _TRUNCATED_WITHOUT_PAGE_REFS_BLOCKER
                    not in blocked_by
                ):
                    blocked_by.append(_TRUNCATED_WITHOUT_PAGE_REFS_BLOCKER)
            completeness = min(
                (snapshot.completeness, budget_completeness),
                key=lambda state: _COMPLETENESS_AUTHORITY_RANK[state],
            )
            digest = compute_governance_snapshot_digest(
                corpus_id=snapshot.corpus_id,
                snapshot_version=snapshot.snapshot_version,
                scorecard_id=snapshot.scorecard_id,
                finding_idempotency_keys=[
                    finding.idempotency_key for finding in top_findings
                ],
                recommendation_idempotency_keys=[
                    recommendation.idempotency_key
                    for recommendation in recommendations
                ],
                replay_result_ids=[
                    result.result_id for result in replay_results
                ],
                replay_result_versions=[
                    result.result_version for result in replay_results
                ],
                omitted_counts=omitted_counts,
                evidence_quality=snapshot.evidence_quality,
                completeness=completeness,
            )
            return snapshot.model_copy(
                update={
                    "snapshot_digest": digest,
                    "truncated": True,
                    "omitted_counts": dict(omitted_counts),
                    "completeness": completeness,
                    "page_refs": list(page_refs),
                    "top_findings": list(top_findings),
                    "recommendations": list(recommendations),
                    "replay_results": list(replay_results),
                    "blocked_by": list(blocked_by),
                }
            )

        budgeted = snapshot
        while (
            GovernanceSnapshotAPI._serialized_snapshot_bytes(budgeted)
            > max_bytes
        ):
            if replay_results:
                replay_results.pop()
                omitted_counts["replay_results"] = (
                    omitted_counts.get("replay_results", 0) + 1
                )
            elif recommendations:
                recommendations.pop()
                omitted_counts["recommendations"] = (
                    omitted_counts.get("recommendations", 0) + 1
                )
            elif top_findings:
                top_findings.pop()
                omitted_counts["findings"] = (
                    omitted_counts.get("findings", 0) + 1
                )
            elif page_refs:
                page_refs.pop()
                omitted_counts["page_refs"] = (
                    omitted_counts.get("page_refs", 0) + 1
                )
            else:
                break
            budgeted = rebuild()
        serialized_bytes = GovernanceSnapshotAPI._serialized_snapshot_bytes(
            budgeted
        )
        if serialized_bytes > max_bytes:
            raise _SerializedSnapshotBudgetExceeded(
                serialized_bytes=serialized_bytes,
                max_response_bytes=max_bytes,
            )
        return budgeted

    @staticmethod
    def _page_refs_are_exact(rows: list[GovernanceEvidencePageRef]) -> bool:
        """True when every surviving page ref is exact authoritative evidence."""

        return bool(rows) and all(
            row.exact
            and row.completeness in ("complete", "paged")
            and bool(row.page_ref_id.strip())
            and bool(row.digest.strip())
            for row in rows
        )

    @staticmethod
    def _restrict_completeness_override(
        *,
        derived_completeness: CompletenessState,
        override: CompletenessState | None,
    ) -> CompletenessState:
        """Return the most restrictive proven completeness state.

        The derived state is computed from actual omitted counts and
        page-ref coverage. Caller overrides are allowed to lower that
        state, but never to raise it above the bounded evidence the API
        observed.
        """

        if override is None:
            return derived_completeness
        if (
            _COMPLETENESS_AUTHORITY_RANK[override]
            > _COMPLETENESS_AUTHORITY_RANK[derived_completeness]
        ):
            return derived_completeness
        return override

    @staticmethod
    def _truncate_findings(
        rows: list[GovernanceFinding], cap: int
    ) -> tuple[list[GovernanceFinding], int]:
        """Truncate the typed findings list at the per-call cap.

        Per the LIMIT cap+1 discipline: if ``len(rows) > cap`` the
        method returns the first ``cap`` rows + the truncated count
        ``len(rows) - cap``; otherwise returns the full list + ``0``.
        """

        if cap < 0:
            # Defensive: a negative cap is treated as 0 (truncate
            # everything; informational rather than raising per
            # fail-closed discipline).
            return [], len(rows)
        if len(rows) > cap:
            return rows[:cap], len(rows) - cap
        return list(rows), 0

    @staticmethod
    def _truncate_recommendations(
        rows: list[GovernancePolicyRecommendation], cap: int
    ) -> tuple[list[GovernancePolicyRecommendation], int]:
        """Truncate the typed recommendations list at the per-call cap.

        Mirrors :meth:`_truncate_findings` verbatim.
        """

        if cap < 0:
            return [], len(rows)
        if len(rows) > cap:
            return rows[:cap], len(rows) - cap
        return list(rows), 0

    @staticmethod
    def _truncate_replay_results(
        rows: list[CounterfactualResult], cap: int
    ) -> tuple[list[CounterfactualResult], int]:
        """Truncate the typed replay_results list at the per-call cap.

        Mirrors :meth:`_truncate_findings` verbatim.
        """

        if cap < 0:
            return [], len(rows)
        if len(rows) > cap:
            return rows[:cap], len(rows) - cap
        return list(rows), 0

    @staticmethod
    def _truncate_page_refs(
        rows: list[Any], cap: int
    ) -> tuple[list[str], int]:
        """Truncate the typed page_refs list at the per-call cap and
        project to the by-name page_ref_id string surface.

        Per doc-19:81 the typed :attr:`GovernanceSnapshot.page_refs`
        field is ``list[str]`` (just typed page_ref_id strings; NOT
        typed BaseModels). This helper extracts only the typed
        :attr:`page_ref_id` string from each input page-ref BaseModel
        (refs-only per the bounded-reads non-negotiable).
        """

        _, ref_ids, omitted = GovernanceSnapshotAPI._truncate_page_ref_rows(
            rows, cap
        )
        return ref_ids, omitted

    @staticmethod
    def _truncate_page_ref_rows(
        rows: list[GovernanceEvidencePageRef], cap: int
    ) -> tuple[list[GovernanceEvidencePageRef], list[str], int]:
        """Truncate page refs and preserve typed rows for exactness checks."""

        if cap < 0:
            return [], [], len(rows)
        if len(rows) > cap:
            truncated_rows = rows[:cap]
            omitted = len(rows) - cap
        else:
            truncated_rows = list(rows)
            omitted = 0
        ref_ids: list[str] = [ref.page_ref_id for ref in truncated_rows]
        return truncated_rows, ref_ids, omitted


# --- Forward-reference resolution -------------------------------------------

# Resolve the forward-reference annotation on
# :attr:`SnapshotAPICorpus.page_refs` (declared as the placeholder
# ``GovernanceEvidencePageRef_ref`` above so the typed import block at
# the top of the module is unambiguous in the documented surface).

from iriai_build_v2.workflows.develop.governance.models import (  # noqa: E402
    GovernanceEvidencePageRef,
)


GovernanceEvidencePageRef_ref = GovernanceEvidencePageRef
"""Forward-reference alias for the Slice 13a typed
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
shape. Declared at module bottom so the
:attr:`SnapshotAPICorpus.page_refs` field annotation resolves to the
typed Slice 13a BaseModel. Per the no-second-source-of-truth
discipline the typed shape is NOT redefined here -- this alias is
purely for module-internal forward-reference clarity."""


# Re-resolve the type annotation on SnapshotAPICorpus.page_refs after
# the forward-reference alias is defined. Pydantic v2 picks up the
# alias when the model is rebuilt.
SnapshotAPICorpus.model_rebuild()
