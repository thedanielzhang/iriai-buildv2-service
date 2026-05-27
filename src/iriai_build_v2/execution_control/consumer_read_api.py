"""Slice 17 sixth sub-slice -- consumer read APIs (typed surface for
runtime consumers to query accepted-but-not-activated governance policy
recommendations).

Per ``docs/execution-control-plane/17-policy-recommendation-interface.md``
§ Refactoring Steps step 6 (line 175-177): *"Add consumer read APIs
that return accepted-but-not-activated policy artifacts separately from
consumer-owned activated policy. Runtime consumers must ignore non-
activated governance recommendations."*

This module owns the typed read-API surface that lets per-consumer
modules (Slice 07 failure_router / Slice 09 scheduler / Slice 10
supervisor + dashboard / Slice 08 + 12 merge queue / Planning) query
the accepted-but-not-activated typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
records separately from their consumer-owned activated policy records.

Per **doc-17:159-163**: *"`activated` is deliberately not a
`GovernancePolicyRecommendation.status`. Activation belongs to a
separate consumer-owned policy record with its own schema, tests,
replay proof, rollback plan, and audit trail. Governance
recommendations can propose or be accepted for review, but cannot
become runtime policy by changing their own row status."* -- the
read-API surfaces the typed ``accepted`` status records WITHOUT
granting them runtime activation authority; activation remains
consumer-owned. The read-API is the typed read-only surface that lets
consumer-side activation flows decide which accepted-but-not-activated
recommendations they want to bind to their own activation evidence.

Per **doc-17:175-177** (the step-6 charter VERBATIM): the read-API
returns *"accepted-but-not-activated policy artifacts separately from
consumer-owned activated policy"* -- the typed
:class:`ConsumerReadAPIResult` carries ONLY the typed governance
recommendation records (NEVER consumer-owned activated policy
records); the runtime consumer is responsible for the activation
record (consumer-owned).

Per **doc-17:175-177 final sentence VERBATIM**: *"Runtime consumers
must ignore non-activated governance recommendations."* -- the
read-API exposes the accepted-but-not-activated typed records SO that
consumers can record their ignore-or-promote-to-activation decision in
their own audit trail; the read-API itself does NOT activate anything.

Per **doc-17:217** (Acceptance Criteria): *"Recommendation generation
has no direct mutation authority."* -- the read-API GRANTS NO
CONSUMER-SIDE ACTIVATION AUTHORITY; the typed
:class:`ConsumerReadAPIResult` is informational only and does NOT
mutate any consumer state. The :class:`GovernanceReadAPI` class
exposes ONLY a ``query_recommendations`` method; there is no
``activate_recommendation`` / ``mutate_consumer`` / ``write_*`` method
on the surface.

Per **doc-17:65** the typed
:data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
6-value Literal is the filter axis for the per-consumer read-API; the
read-API takes a typed ``consumer`` field on
:class:`ConsumerReadAPIInputs` and filters the candidate
recommendation list by that value.

Per **doc-17:66-73** the typed
:data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationStatus`
6-value Literal is the secondary filter axis; the read-API takes a
typed ``status_filter`` field on :class:`ConsumerReadAPIInputs` that
defaults to ``"accepted"`` (per doc-17:175-177 the default surfaces
the accepted-but-not-activated records); callers MAY pass any of the
6 Literal values OR ``None`` (to retrieve all statuses).

Per **doc-17:147-158** the per-consumer contracts are honoured by the
read-API: the filter axis is the typed ``consumer`` field on the
recommendation; the read-API is consumer-agnostic in shape (a single
typed class + method serves all 6 consumers). Per-consumer activation
contracts remain consumer-owned and are NOT executed by the read-API.

**Activation-authority boundary (doc-17:217 + doc-17:159-163 +
doc-17:175-177).** The read-API has NO authority to activate
recommendations. Verified both functionally (the
:class:`GovernanceReadAPI` class exposes ONLY a typed
``query_recommendations`` method; no ``activate_*`` / ``write_*`` /
``mutate_*`` method) and structurally (this module does NOT import
any consumer module: NO ``..workflows.develop.execution.failure_router``
import, NO ``..supervisor`` / ``..dashboard`` / ``..planning`` /
``..workflows.develop.merge_queue`` / ``..workflows.develop.scheduler``
imports). The boundary is enforced at the module-import level so the
test suite can structurally verify it.

**Bounded reads (IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded
reads").** The read-API applies the typed snapshot's ``LIMIT cap+1``
truncation discipline: callers pass a typed ``limit`` field (defaults
to :data:`DEFAULT_READ_API_CAP` = 100) and the read-API returns at
most ``limit`` recommendations with ``truncated=True`` when the
candidate count exceeded the cap. This mirrors the Slice 14 2nd /
Slice 15 4th / Slice 16 4th / Slice 17 4th sub-slice writer
``LIMIT cap+1`` truncation pattern verbatim.

**Fail-closed semantics (feedback_no_silent_degradation).** The
read-API NEVER raises on input. On a structural internal failure
(e.g. an unexpected :class:`pydantic.ValidationError` from a typed
:class:`ConsumerReadAPIResult` construction), the read-API emits a
typed :class:`ConsumerReadAPIResult` with an empty
``recommendations`` list, ``truncated=False``, and a populated
``gap_records`` list carrying a typed :class:`ConsumerReadAPIGap`
with the failure reason. The corresponding typed failure id
:data:`CONSUMER_READ_API_FAILURE_ID`
(``consumer_read_api_failed``) registers under the EXISTING
``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router`
with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th sub-slice
precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 2nd/3rd/4th/5th sub-slice non-blocking governance projection
observer (the read-API is also a post-checkpoint governance
projection observer: it is queried AFTER the recommendation
projection lands).

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-17:240-289).** This module's typed shapes carry NO Slice 13a
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
fields directly -- the read-API is a thin projection over the typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
records (which themselves carry the Slice 13a evidence chain via the
typed ``source_finding_ids`` / ``source_metric_refs`` fields). The
Slice 13a dependency reconciliation is transitively honoured because
the typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
records the read-API surfaces already enforce the Slice 13a refs-only
discipline at construction.

**Implementation discipline.** Stdlib (``datetime``) + Pydantic v2 +
Slice 17 1st sub-slice (``.policy_recommendation``) only. NO imports
from ``governance/`` (this module is downstream of governance.models
but consumes only typed shapes via the Slice 17 1st sub-slice
re-exports). NO imports from any consumer module
(``failure_router`` / ``scheduler`` / ``supervisor`` / ``dashboard`` /
``planning`` / ``merge_queue``). NO imports from Slice 17 2nd / 3rd /
4th / 5th sub-slice modules (the read-API is a thin projection over
the Slice 17 1st sub-slice typed-shape foundation; the
builder / validator / writer / hook surfaces are NOT consumed). NO
mutation of any existing ``execution_control/`` module (per the
implementer prompt § "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.policy_recommendation` (Slice
17 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.policy_validation_interface`
(Slice 17 3rd sub-slice) +
:mod:`iriai_build_v2.execution_control.decision_record_writer` (Slice
17 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.replay_requirement_hook` (Slice
17 5th sub-slice) verbatim: ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``int`` / ``bool`` /
``list`` / ``datetime`` / Literal). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field
validates at construction; unknown values fail closed via Literal range
+ ``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 17 3rd / 4th / 5th sub-slice precedent verbatim without
introducing new abstractions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    PolicyConsumer,
    PolicyRecommendationStatus,
)


__all__ = [
    # Typed failure id (doc-17:175-177 + doc-14:242-243 NON-BLOCKING).
    "CONSUMER_READ_API_FAILURE_ID",
    # Bounded-reads cap (IMPLEMENTATION_PROMPT_GOVERNANCE.md § Bounded reads).
    "DEFAULT_READ_API_CAP",
    # Typed BaseModels (mirrors Slice 17 3rd/4th/5th sub-slice precedent).
    "ConsumerReadAPIInputs",
    "ConsumerReadAPIGap",
    "ConsumerReadAPIResult",
    # Read-API class (doc-17:175-177 step 6).
    "GovernanceReadAPI",
]


# --- Typed failure id (doc-17:175-177 + doc-14:242-243 NON-BLOCKING) --------


CONSUMER_READ_API_FAILURE_ID: Literal[
    "consumer_read_api_failed"
] = "consumer_read_api_failed"
"""Doc-17:175-177 + doc-14:242-243 -- the typed failure id the
consumer read-API projects onto when an internal query step fails
structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th sub-slice
precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 6th pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 2nd + 3rd + 4th + 5th sub-slice non-blocking governance
projection observer (the read-API is also a post-checkpoint
governance projection observer: it is queried AFTER the
recommendation projection lands).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
+ Slice 17 2nd / 3rd / 4th / 5th sub-slice precedents are the
source-of-truth for the non-blocking governance-projection
failure-routing discipline:

* :mod:`iriai_build_v2.execution_control.commit_provenance_writer`
  (Slice 14 2nd) defines ``line_provenance_gap`` +
  ``governance_evidence_conflict``.
* :mod:`iriai_build_v2.execution_control.governance_metric_extractor`
  (Slice 15 2nd) defines ``governance_metric_extraction_failed``.
* :mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
  (Slice 15 4th) defines ``governance_scorecard_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.finding_rule_engine`
  (Slice 16 2nd) defines ``finding_rule_emission_failed``.
* :mod:`iriai_build_v2.execution_control.finding_plan_deviation_engine`
  (Slice 16 3rd-A) defines ``finding_plan_deviation_parse_failed``.
* :mod:`iriai_build_v2.execution_control.finding_reviewer_test_failure_engine`
  (Slice 16 3rd-B) defines ``finding_reviewer_test_failure_parse_failed``.
* :mod:`iriai_build_v2.execution_control.governance_finding_writer`
  (Slice 16 4th) defines ``governance_finding_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.recommendation_builder`
  (Slice 17 2nd) defines ``recommendation_builder_emission_failed``.
* :mod:`iriai_build_v2.execution_control.policy_validation_interface`
  (Slice 17 3rd) defines ``policy_validation_failed``.
* :mod:`iriai_build_v2.execution_control.decision_record_writer`
  (Slice 17 4th) defines ``decision_record_persistence_failed``.
* :mod:`iriai_build_v2.execution_control.replay_requirement_hook`
  (Slice 17 5th) defines ``replay_requirement_validation_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to consumer
read-API failures (this slice is also a post-checkpoint governance
projection observer).
"""


# --- Bounded-reads cap (IMPLEMENTATION_PROMPT_GOVERNANCE.md § Bounded reads)


DEFAULT_READ_API_CAP: int = 100
"""IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the
default LIMIT cap for the consumer read-API.

Per IMPLEMENTATION_PROMPT_GOVERNANCE.md *"Reuse the typed snapshot's
`LIMIT cap+1` truncation discipline and the supervisor's
`SET LOCAL statement_timeout` pattern."* the read-API surfaces at
most ``DEFAULT_READ_API_CAP`` recommendations per query when the
caller does NOT override the cap; the read-API filters candidates +
returns ``truncated=True`` when the candidate count exceeded the cap.

Value: 100. Per the auto-memory ``feedback_quality_over_speed`` rule
the cap favors responsiveness (per-consumer dashboards / per-consumer
runtime filters should return promptly) over raw count (no consumer
needs the full 200-row writer batch the Slice 14 2nd / Slice 15 4th /
Slice 16 4th / Slice 17 4th sub-slice writer caps target -- those caps
target persistence batches; this cap targets per-query read scope).

Callers MAY override the cap via the typed
:attr:`ConsumerReadAPIInputs.limit` field; the read-API applies the
caller-provided cap with the same LIMIT cap+1 truncation discipline.

Mirrors the Slice 14 2nd sub-slice
:data:`~iriai_build_v2.execution_control.commit_provenance_writer.DEFAULT_PROVENANCE_WRITER_CAP`
+ Slice 15 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_scorecard_writer.DEFAULT_SCORECARD_WRITER_CAP`
+ Slice 16 4th sub-slice
:data:`~iriai_build_v2.execution_control.governance_finding_writer.DEFAULT_FINDING_WRITER_CAP`
+ Slice 17 4th sub-slice
:data:`~iriai_build_v2.execution_control.decision_record_writer.DEFAULT_REVIEW_PROJECTION_CAP`
typed-cap precedent verbatim (different value -- the read scope is
narrower than the persistence batch scope per the
``feedback_quality_over_speed`` discipline -- but the same typed-cap
discipline)."""


# --- Typed ConsumerReadAPIInputs BaseModel ----------------------------------


class ConsumerReadAPIInputs(BaseModel):
    """Doc-17:175-177 -- typed inputs bundle for the consumer read-API
    query.

    A :class:`ConsumerReadAPIInputs` is the typed input the
    :meth:`GovernanceReadAPI.query_recommendations` method consumes.
    The shape carries:

    * ``consumer`` -- the typed
      :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
      value the caller is filtering by (one of the 6 doc-17:65 Literal
      values).
    * ``corpus_id`` -- the corpus identifier the read-API scopes the
      query to (e.g. ``"8ac124d6"`` for the calibration fixture per
      doc-17:236-237; future feature ids for production queries).
      This is the typed audit-trail surface that lets the read-API
      record which corpus the query was scoped to (e.g. a typed
      :class:`ConsumerReadAPIGap` records the ``corpus_id`` for the
      failure trace).
    * ``limit`` -- the typed cap on the number of recommendations the
      read-API returns. Defaults to
      :data:`DEFAULT_READ_API_CAP` (100). The read-API applies the
      caller-provided cap with the LIMIT cap+1 truncation discipline
      per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads".
    * ``status_filter`` -- optional typed
      :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationStatus`
      value the read-API additionally filters by. Defaults to
      ``"accepted"`` so the read-API by default surfaces the
      accepted-but-not-activated typed records per the doc-17:175-177
      charter VERBATIM. Callers MAY pass any of the 6 doc-17:66-73
      Literal values to retrieve records with a specific status; OR
      ``None`` to retrieve all statuses (the read-API does NOT
      filter by status when ``status_filter is None``).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``str`` / ``int`` /
    Literal / Optional Literal).
    """

    # ``extra="forbid"`` aligns with the Slice 17 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/policy_recommendation.py:376
    # + the Slice 17 3rd sub-slice precedent at
    # src/iriai_build_v2/execution_control/policy_validation_interface.py:294
    # + the Slice 17 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/decision_record_writer.py:616
    # + the Slice 17 5th sub-slice precedent at
    # src/iriai_build_v2/execution_control/replay_requirement_hook.py:340
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    consumer: PolicyConsumer
    """Doc-17:65 -- the typed
    :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
    the read-API is filtering by. Per Pydantic Literal validation the
    field accepts only one of the 6 doc-17:65 values; unknown values
    fail closed with a typed ``ValidationError``.

    The 6 valid values are: ``scheduler`` / ``failure_router`` /
    ``supervisor`` / ``dashboard`` / ``planning`` / ``merge_queue``.
    Per doc-17:147-158 each consumer owns its activation surface; the
    read-API surfaces the typed ``accepted`` records WITHOUT granting
    them runtime activation authority (activation remains
    consumer-owned)."""

    corpus_id: str
    """The corpus identifier the read-API scopes the query to (e.g.
    ``"8ac124d6"`` for the calibration fixture per doc-17:236-237;
    future feature ids for production queries).

    The corpus identifier is recorded in the typed
    :class:`ConsumerReadAPIGap` (when emitted) for the failure-trace
    audit surface; the read-API does NOT validate the corpus_id
    against any catalog (the validation happens upstream when the
    typed recommendation rows are constructed)."""

    limit: int = DEFAULT_READ_API_CAP
    """IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the
    typed cap on the number of recommendations the read-API returns.

    Defaults to :data:`DEFAULT_READ_API_CAP` (100). The read-API
    applies the cap with the LIMIT cap+1 truncation discipline:
    callers see at most ``limit`` recommendations with
    ``truncated=True`` when the candidate count exceeded the cap.

    Callers MAY override the cap (e.g. a smaller cap for a quick
    dashboard preview; a larger cap for an audit-trail dump). Per the
    auto-memory ``feedback_no_silent_degradation`` rule the cap MUST
    be a positive integer; Pydantic enforces ``int`` typing at
    construction (negative or zero caps are NOT validated here -- the
    read-API treats them as "no records returned"; the typed surface
    documents the convention)."""

    status_filter: PolicyRecommendationStatus | None = "accepted"
    """Doc-17:66-73 + doc-17:175-177 -- optional typed
    :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationStatus`
    value the read-API additionally filters by.

    Defaults to ``"accepted"`` so the read-API by default surfaces
    the accepted-but-not-activated typed records per the
    doc-17:175-177 charter VERBATIM: *"Add consumer read APIs that
    return accepted-but-not-activated policy artifacts separately
    from consumer-owned activated policy."*

    Callers MAY pass any of the 6 doc-17:66-73 Literal values
    (``draft`` / ``reviewed`` / ``accepted`` / ``rejected`` /
    ``needs_more_evidence`` / ``superseded``) to retrieve records with
    a specific status; OR ``None`` to retrieve all statuses (the
    read-API does NOT filter by status when ``status_filter is
    None``).

    Per doc-17:159-163 ``activated`` is deliberately NOT a value of
    the :data:`PolicyRecommendationStatus` Literal -- activation
    belongs to a separate consumer-owned policy record. The read-API
    surfaces only the typed governance recommendation rows; it does
    NOT surface any consumer-owned activated policy record."""


# --- Typed ConsumerReadAPIGap BaseModel -------------------------------------


class ConsumerReadAPIGap(BaseModel):
    """Doc-17:175-177 + doc-14:242-243 -- typed governance-gap shape
    produced when the consumer read-API fails to query the
    accepted-but-not-activated typed recommendation rows structurally.

    Mirrors the Slice 17 5th sub-slice
    :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidationGap`
    + Slice 17 4th sub-slice
    :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionPersistenceGap`
    + Slice 17 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationViolation`
    + Slice 17 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderEmissionGap`
    + Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    gap shapes verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-17:175-177 governance-projection discipline) the gap is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id
    :data:`CONSUMER_READ_API_FAILURE_ID`
    (``consumer_read_api_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    The shape carries:

    * ``failure_id`` -- typed Literal const matching
      :data:`CONSUMER_READ_API_FAILURE_ID`.
    * ``reason`` -- free-form failure-reason string carrying the
      typed exception type + message for the audit trail.
    * ``observed_at`` -- typed UTC observation timestamp (defaults to
      UTC now).
    * ``consumer`` -- optional typed
      :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
      the failed query was scoped to; defaults to ``None`` when even
      the consumer field could not be extracted from the typed inputs.
    * ``corpus_id`` -- optional typed corpus identifier the failed
      query was scoped to; defaults to ``None`` when even the
      corpus_id could not be extracted from the typed inputs.
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["consumer_read_api_failed"]
    """Doc-17:175-177 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-17:175-177."""

    reason: str
    """Free-form gap reason. The Slice 17 6th sub-slice v1 surface
    emits the reason string in the form
    ``"consumer_read_api_internal_exception:<ExceptionName>:<message>"``
    where ``<ExceptionName>`` is the typed Python exception type that
    raised internally (e.g. ``ValidationError`` from a typed
    :class:`ConsumerReadAPIResult` construction) and ``<message>`` is
    the typed exception message; per the
    ``feedback_no_silent_degradation`` rule the read-API NEVER raises
    -- the typed gap projection carries the failure reason instead.

    The free-form ``str`` (rather than a Literal) lets future
    sub-slices add reasons without bumping the typed surface."""

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """Doc-17:175-177 + doc-13:201-204 -- the typed observation
    timestamp for the gap. Defaults to UTC now; projects to ISO-8601
    string under :meth:`BaseModel.model_dump` with ``mode='json'``."""

    consumer: PolicyConsumer | None = None
    """Doc-17:65 -- optional typed
    :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
    the failed query was scoped to.

    Defaults to ``None`` so the typed gap projection can still be
    constructed even when the read-API failed to extract the typed
    consumer field from the typed
    :class:`ConsumerReadAPIInputs` (e.g. the inputs themselves raised
    during construction). When non-``None``, the field carries the
    same value as the typed
    :attr:`ConsumerReadAPIInputs.consumer` field for the audit
    trail."""

    corpus_id: str | None = None
    """Optional typed corpus identifier the failed query was scoped
    to.

    Defaults to ``None`` so the typed gap projection can still be
    constructed even when the read-API failed to extract the typed
    corpus_id field from the typed
    :class:`ConsumerReadAPIInputs`. When non-``None``, the field
    carries the same value as the typed
    :attr:`ConsumerReadAPIInputs.corpus_id` field for the audit
    trail."""


# --- Typed ConsumerReadAPIResult BaseModel ----------------------------------


class ConsumerReadAPIResult(BaseModel):
    """Doc-17:175-177 -- typed per-query consumer read-API outcome.

    A :class:`ConsumerReadAPIResult` is the typed audit record the
    :meth:`GovernanceReadAPI.query_recommendations` method emits per
    query. The shape carries:

    * ``consumer`` -- the typed
      :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
      the query was scoped to (echoed from the typed
      :class:`ConsumerReadAPIInputs` for the audit trail).
    * ``recommendations`` -- the typed list of
      :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
      records matching the typed consumer + status filters. Bounded by
      the LIMIT cap+1 truncation discipline: at most ``inputs.limit``
      records.
    * ``truncated`` -- typed boolean for whether the candidate count
      exceeded the cap (so callers can decide whether to paginate or
      raise the cap). Per the LIMIT cap+1 discipline ``truncated=True``
      when the candidate count was strictly greater than the cap.
    * ``gap_records`` -- the typed list of
      :class:`ConsumerReadAPIGap` records when the query failed
      structurally. Defaults to empty list for successful queries;
      populated with one or more typed gaps when the read-API
      projected onto the typed
      :data:`CONSUMER_READ_API_FAILURE_ID` failure id.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per **doc-17:217** + **doc-17:159-163** + **doc-17:175-177**: a
    :class:`ConsumerReadAPIResult` is INFORMATIONAL ONLY -- the
    consumer that receives the result is responsible for its own
    activation decision (per doc-17:175-177 *"Runtime consumers must
    ignore non-activated governance recommendations"*); the read-API
    does NOT activate any recommendation, mutate any consumer state,
    or grant the consumer any activation authority.
    """

    model_config = ConfigDict(extra="forbid")

    consumer: PolicyConsumer
    """Doc-17:65 -- the typed
    :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
    the query was scoped to (echoed from the typed
    :class:`ConsumerReadAPIInputs` for the audit trail)."""

    recommendations: list[GovernancePolicyRecommendation] = Field(
        default_factory=list
    )
    """Doc-17:175-177 + doc-17:75-97 -- the typed list of
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    records matching the typed consumer + status filters.

    Bounded by the LIMIT cap+1 truncation discipline: at most
    ``inputs.limit`` records per query. Defaults to empty list when
    no records match the filters OR when the query failed
    structurally (in which case ``gap_records`` is non-empty).

    Per doc-17:175-177 the typed records are the
    accepted-but-not-activated governance recommendations (when the
    default ``status_filter="accepted"`` is in effect); callers MAY
    override the filter to retrieve records with other statuses (per
    the typed
    :attr:`ConsumerReadAPIInputs.status_filter` field).

    Per the Slice 13A invariant the typed records carry typed evidence
    references via the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_finding_ids`
    + :attr:`source_metric_refs` /
    :attr:`counterfactual_result_refs` fields (refs-only; NEVER raw
    bodies)."""

    truncated: bool = False
    """IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" -- the
    typed boolean for whether the candidate count exceeded the cap.

    Per the LIMIT cap+1 truncation discipline ``truncated=True`` when
    the candidate count was strictly greater than the cap; the read-API
    returns at most ``inputs.limit`` records and sets ``truncated=True``
    so the caller can decide whether to paginate or raise the cap.
    Defaults to ``False`` for queries that fit within the cap.

    Mirrors the Slice 14 2nd / Slice 15 4th / Slice 16 4th / Slice 17
    4th sub-slice writer ``truncated`` field discipline verbatim."""

    gap_records: list[ConsumerReadAPIGap] = Field(default_factory=list)
    """Doc-17:175-177 + doc-14:242-243 -- the typed list of
    :class:`ConsumerReadAPIGap` records when the query failed
    structurally.

    Defaults to empty list for successful queries; populated with one
    or more typed gaps when the read-API projected onto the typed
    :data:`CONSUMER_READ_API_FAILURE_ID` failure id. Per the
    ``feedback_no_silent_degradation`` rule the read-API NEVER
    raises -- the typed gap projection carries the failure reason
    instead.

    Mirrors the Slice 14 2nd / Slice 15 4th / Slice 16 4th / Slice 17
    4th sub-slice writer ``gap_records`` field discipline verbatim."""


# --- GovernanceReadAPI dispatch class ---------------------------------------


class GovernanceReadAPI:
    """Doc-17:175-177 step 6 -- consumer read-API for governance
    policy recommendations.

    A :class:`GovernanceReadAPI` instance exposes the
    :meth:`query_recommendations` method that consumes a typed
    :class:`ConsumerReadAPIInputs` + a candidate list of typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    records and returns a typed :class:`ConsumerReadAPIResult` carrying
    the filtered + bounded records.

    Per doc-17:175-177 *"Add consumer read APIs that return
    accepted-but-not-activated policy artifacts separately from
    consumer-owned activated policy. Runtime consumers must ignore
    non-activated governance recommendations."* the read-API is the
    typed read-only surface that lets per-consumer modules query the
    accepted-but-not-activated typed governance recommendation records
    separately from their consumer-owned activated policy records.

    **Boundary: NO consumer-side activation authority (doc-17:217 +
    doc-17:159-163 + doc-17:175-177).** The read-API does NOT call any
    consumer-side activation method; the
    :class:`ConsumerReadAPIResult` is informational only. Per
    doc-17:175-177 *"Runtime consumers must ignore non-activated
    governance recommendations"* -- the consumer that receives the
    result is responsible for its own ignore-or-promote-to-activation
    decision; the read-API does NOT activate any recommendation,
    mutate any consumer state, or grant the consumer any activation
    authority.

    **Boundary: NO consumer-module imports.** The read-API does NOT
    import any consumer-side module (no
    ``..workflows.develop.execution.failure_router`` import beyond
    the 4 pure-data add points for the typed failure id; no
    ``..supervisor`` / ``..dashboard`` / ``..planning`` /
    ``..workflows.develop.merge_queue`` /
    ``..workflows.develop.scheduler`` imports). The read-API operates
    over the typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    BaseModel SURFACE only.

    **Boundary: NO mutation methods.** The :class:`GovernanceReadAPI`
    class exposes ONLY the typed ``query_recommendations`` read
    method; there is no ``activate_recommendation`` /
    ``mutate_consumer`` / ``write_*`` method on the surface.
    Functionally verified: the public method set is exactly
    ``{"query_recommendations"}``.

    **Bounded reads (IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded
    reads").** The read-API applies the typed snapshot's LIMIT cap+1
    truncation discipline: callers pass a typed
    :attr:`ConsumerReadAPIInputs.limit` (defaults to
    :data:`DEFAULT_READ_API_CAP` = 100); the read-API returns at most
    ``limit`` recommendations with ``truncated=True`` when the
    candidate count exceeded the cap.

    **Fail-closed semantics (feedback_no_silent_degradation).** The
    read-API NEVER raises on input. On a structural internal failure
    (e.g. an unexpected :class:`pydantic.ValidationError` from a typed
    :class:`ConsumerReadAPIResult` construction), the read-API emits a
    typed :class:`ConsumerReadAPIResult` with an empty
    ``recommendations`` list, ``truncated=False``, and a populated
    ``gap_records`` list carrying a typed :class:`ConsumerReadAPIGap`
    with the failure reason.

    Mirrors the Slice 17 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
    + Slice 17 4th sub-slice
    :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
    + Slice 17 5th sub-slice
    :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
    pattern: a typed bundle-driven projection class without state.

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the class is stateless and exposes a single method; per the
    auto-memory ``feedback_no_silent_degradation`` rule every Pydantic
    construction is wrapped in a typed gap projection.

    Example usage::

        from iriai_build_v2.execution_control.consumer_read_api import (
            ConsumerReadAPIInputs,
            GovernanceReadAPI,
        )

        read_api = GovernanceReadAPI()
        inputs = ConsumerReadAPIInputs(
            consumer="failure_router",
            corpus_id="8ac124d6",
        )
        # Candidates come from the upstream typed recommendation
        # projection (Slice 17 1st sub-slice typed-shape foundation +
        # Slice 17 2nd sub-slice builder + Slice 17 4th sub-slice
        # decision-record writer); the read-API does NOT load the
        # candidates itself (it is a pure projection over typed
        # records the caller provides).
        result = read_api.query_recommendations(inputs, candidates)
        if result.truncated:
            # Paginate or raise the cap.
            ...
        for recommendation in result.recommendations:
            # The runtime consumer decides whether to promote the typed
            # recommendation to its own consumer-owned activated policy
            # record per doc-17:175-177 *"Runtime consumers must ignore
            # non-activated governance recommendations."*; the read-API
            # does NOT activate the recommendation.
            ...
    """

    def __init__(self) -> None:
        """Construct a :class:`GovernanceReadAPI`.

        The constructor takes no arguments per the
        ``feedback_no_overengineer_use_library`` rule: the read-API
        is stateless. Future per-corpus rule overrides (per the
        doc-17:175-177 *"per consumer"* extension point) live on the
        bound :meth:`query_recommendations` method via the typed
        :class:`ConsumerReadAPIInputs` dispatch.

        Mirrors the Slice 17 3rd sub-slice
        :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
        + Slice 17 5th sub-slice
        :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
        constructor verbatim.
        """

        # Stateless per the prompt's "No mutation methods" +
        # "NEVER raises" + "No consumer-module imports" boundaries.
        pass

    def query_recommendations(
        self,
        inputs: ConsumerReadAPIInputs,
        candidates: list[GovernancePolicyRecommendation],
    ) -> ConsumerReadAPIResult:
        """Doc-17:175-177 step 6 -- query the typed candidate
        recommendation list for records matching the typed consumer +
        status filters on the typed
        :class:`ConsumerReadAPIInputs`.

        Filter logic:

        * Filter by ``consumer``: only typed
          :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
          records whose ``consumer`` field exactly matches
          ``inputs.consumer`` (one of the 6 doc-17:65 Literal values)
          are kept.
        * Filter by ``status``: when ``inputs.status_filter`` is
          non-``None``, only records whose ``status`` field exactly
          matches ``inputs.status_filter`` (one of the 6 doc-17:66-73
          Literal values) are kept; when
          ``inputs.status_filter is None``, the read-API does NOT
          filter by status.
        * Apply LIMIT cap+1 truncation: the read-API counts the
          matching candidates; when the count is strictly greater than
          ``inputs.limit``, the read-API returns at most
          ``inputs.limit`` records and sets ``truncated=True``;
          otherwise ``truncated=False``.

        Per **doc-17:175-177**: the default ``status_filter="accepted"``
        surfaces the accepted-but-not-activated typed records. Per
        doc-17:159-163 ``activated`` is deliberately NOT a value of
        the typed :data:`PolicyRecommendationStatus` Literal --
        activation belongs to a separate consumer-owned policy record;
        the read-API surfaces only the typed governance recommendation
        rows.

        Per **doc-17:217 + doc-17:159-163 + doc-17:175-177** the
        read-API GRANTS NO consumer-side activation authority; the
        typed :class:`ConsumerReadAPIResult` is informational only.

        Per ``feedback_no_silent_degradation`` the method NEVER
        raises: a structural internal failure (e.g. an unexpected
        :class:`pydantic.ValidationError` during typed
        :class:`ConsumerReadAPIResult` construction) emits a typed
        :class:`ConsumerReadAPIResult` with an empty
        ``recommendations`` list, ``truncated=False``, and a populated
        ``gap_records`` list carrying a typed
        :class:`ConsumerReadAPIGap` with the failure reason.

        Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
        LIMIT cap+1 truncation discipline is honoured at every query.

        Returns a typed :class:`ConsumerReadAPIResult`.
        """

        try:
            # Defensive: extract the typed inputs fields up front so we
            # have valid values for the typed
            # :class:`ConsumerReadAPIResult` + :class:`ConsumerReadAPIGap`
            # constructions below.
            consumer = inputs.consumer
            corpus_id = inputs.corpus_id
            limit = inputs.limit
            status_filter = inputs.status_filter

            # Defensive: a non-positive cap means "no records returned"
            # (the typed surface documents this convention; the
            # implementation honours it without raising).
            if limit <= 0:
                return ConsumerReadAPIResult(
                    consumer=consumer,
                    recommendations=[],
                    truncated=False,
                    gap_records=[],
                )

            # Filter pass 1: by typed consumer field. Per doc-17:147-158
            # each consumer owns its activation surface; the typed
            # consumer field is the typed filter axis.
            consumer_matched: list[GovernancePolicyRecommendation] = [
                recommendation
                for recommendation in candidates
                if recommendation.consumer == consumer
            ]

            # Filter pass 2: by typed status field (when the typed
            # filter is non-None). Per doc-17:175-177 the default
            # surfaces the accepted-but-not-activated records.
            if status_filter is None:
                status_matched: list[GovernancePolicyRecommendation] = list(
                    consumer_matched
                )
            else:
                status_matched = [
                    recommendation
                    for recommendation in consumer_matched
                    if recommendation.status == status_filter
                ]

            # Apply LIMIT cap+1 truncation per
            # IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads":
            # callers see at most ``limit`` records with
            # ``truncated=True`` when the candidate count exceeded the
            # cap.
            candidate_count = len(status_matched)
            truncated = candidate_count > limit
            bounded = status_matched[:limit]

            return ConsumerReadAPIResult(
                consumer=consumer,
                recommendations=bounded,
                truncated=truncated,
                gap_records=[],
            )

        except Exception as exc:
            # Structural internal failure (e.g. an unexpected
            # ValidationError from a typed ConsumerReadAPIResult
            # construction above, or the typed inputs / candidates
            # fields raise unexpectedly). Per
            # feedback_no_silent_degradation the read-API NEVER
            # raises: emit a typed ConsumerReadAPIResult with an empty
            # recommendations list + truncated=False + a populated
            # gap_records list carrying the typed
            # ConsumerReadAPIGap. Per doc-14:242-243 this projects onto
            # the NON-blocking typed failure id
            # CONSUMER_READ_API_FAILURE_ID under EXISTING
            # evidence_corruption failure_class with REUSED
            # retry_governance_projection action (registered in the
            # Slice 17 6th sub-slice failure_router 4 pure-data add
            # points).

            # Defensive: try to extract the typed consumer + corpus_id
            # for the typed gap projection; fall back to None when even
            # the typed inputs raise unexpectedly so the typed gap
            # projection can still be constructed.
            fallback_consumer: PolicyConsumer | None
            try:
                fallback_consumer = inputs.consumer
            except Exception:
                fallback_consumer = None

            fallback_corpus_id: str | None
            try:
                fallback_corpus_id = inputs.corpus_id
            except Exception:
                fallback_corpus_id = None

            gap = ConsumerReadAPIGap(
                failure_id=CONSUMER_READ_API_FAILURE_ID,
                reason=(
                    f"consumer_read_api_internal_exception:"
                    f"{type(exc).__name__}:{exc!s}"
                ),
                consumer=fallback_consumer,
                corpus_id=fallback_corpus_id,
            )

            # Defensive construction: if even the typed
            # ConsumerReadAPIResult construction raises (e.g. the
            # fallback_consumer is None and the result requires a
            # typed PolicyConsumer), fall back to a default consumer
            # so the typed result can still be constructed. Per
            # feedback_harden_all_code_paths the read-API NEVER
            # raises.
            try:
                return ConsumerReadAPIResult(
                    consumer=(
                        fallback_consumer
                        if fallback_consumer is not None
                        else "failure_router"
                    ),
                    recommendations=[],
                    truncated=False,
                    gap_records=[gap],
                )
            except Exception:
                # Last-resort defensive fallback: construct a minimal
                # typed result with the typed gap so the read-API
                # NEVER raises per feedback_no_silent_degradation.
                return ConsumerReadAPIResult(
                    consumer="failure_router",
                    recommendations=[],
                    truncated=False,
                    gap_records=[gap],
                )
