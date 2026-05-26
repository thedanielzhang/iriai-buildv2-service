"""Slice 17 fifth sub-slice -- replay requirement hooks (typed cross-slice
reference shape + validator) for behavior-changing governance
recommendations.

Per ``docs/execution-control-plane/17-policy-recommendation-interface.md``
§ Refactoring Steps step 5 (line 173-174): *"Add replay requirement
hooks so any behavior-changing recommendation can point to Slice 18
counterfactual results."*

Per **doc-17:159-163**: *"`activated` is deliberately not a
`GovernancePolicyRecommendation.status`. Activation belongs to a
separate consumer-owned policy record with its own schema, tests,
replay proof, rollback plan, and audit trail. Governance
recommendations can propose or be accepted for review, but cannot
become runtime policy by changing their own row status."* -- the
replay-requirement hook is the typed gating surface that the Slice 17
recommendation consumer-side activation contracts cite when deciding
whether a behavior-changing recommendation has the required replay
proof; this module owns ONLY the typed cross-reference shape (the
:class:`ReplayRequirementHook` BaseModel) and the typed validator
(:class:`ReplayRequirementValidator`); it does NOT own the replay
results themselves, which remain owned by Slice 18 per
doc-17:225-226 + the doc-17 § Cross-Slice Dependencies enumeration.

Per **doc-17:225-226** (Rollback And Rollback Notes): *"Activated
policy rollback belongs to the owning consumer, not the governance
analyzer."* -- the same boundary applies to the replay-result-source-
of-truth: Slice 18 (counterfactual replay) owns the actual replay
result records; this module owns the typed cross-slice reference (the
list of ``counterfactual_result_refs`` ref-strings on a
:class:`GovernancePolicyRecommendation`) and the typed validator that
checks the cross-reference is populated for behavior-changing
recommendations.

Per **doc-17:60** (Blocking Deviations enumeration): *"A
recommendation lacks source findings, confidence, or owner component."*
-- the replay-requirement hook EXTENDS this blocking-deviation taxonomy
to behavior-changing recommendations (``safe_runtime_action=True``) that
lack the cross-slice replay reference (an empty
``counterfactual_result_refs`` list); the typed validator rejects such
recommendations with the typed
:class:`ReplayRequirementValidationGap` shape.

Per **doc-17:198-200** (Edge Cases And Failure Handling): *"Safe
runtime action false: recommendation can be reported but not consumed
by runtime policy without a later implementation plan."* -- this is
the typed motivation for the
:attr:`GovernancePolicyRecommendation.safe_runtime_action`-keyed
gating: the replay-requirement check is GATED on
``safe_runtime_action=True`` (the recommendation declares itself
safe-to-activate-as-runtime-action; the typed validator then checks
the replay reference is populated). Non-behavior-changing
recommendations (``safe_runtime_action=False``) pass the
replay-requirement check trivially (they cannot be consumed by runtime
policy at all per doc-17:198-200; the replay reference is not
required).

Per **doc-17:75-97** (GovernancePolicyRecommendation shape) the typed
fields the validator consumes are:

* :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.safe_runtime_action`
  -- ``bool`` at doc-17:86 (typed flag for runtime-action safety).
* :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
  -- ``list[str]`` at doc-17:82 (the typed cross-slice ref-string list
  referencing Slice 18 counterfactual replay results).
* :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
  -- ``str`` at doc-17:77 (the typed recommendation identifier echoed
  in the validation result for the audit trail).

Per **doc-17:217** (Acceptance Criteria): *"Recommendation generation
has no direct mutation authority."* -- the validator GRANTS NO
CONSUMER-SIDE ACTIVATION AUTHORITY; a passing :class:`ReplayRequirement
ValidationResult` does NOT activate the recommendation's proposed
policy artifact; activation remains consumer-owned per doc-17:159-163.
The validator is informational only: it returns the typed
:class:`ReplayRequirementValidationResult` record and does NOT mutate
any consumer state.

**No second source of replay truth (doc-17:159-163 + doc-17:225-226 +
Cross-Slice Dependencies enumeration).** Per the structural boundary
this module:

* Does NOT load, fetch, or construct any Slice 18 counterfactual
  replay result records.
* Does NOT define a local typed Slice-18-result BaseModel; the
  cross-reference shape is the typed ``list[str]`` of ref-strings
  carried by the recommendation.
* Does NOT import any Slice 18 module (Slice 18 has not landed at
  this iteration; even when it lands, this module's typed surface
  remains the cross-reference-shape only).
* Does NOT execute, replay, or simulate any counterfactual scenario.

The typed cross-slice reference shape is the list of ref-strings
(``list[str]``) the recommendation carries; the validator's job is to
check the list is non-empty for behavior-changing recommendations.
Slice 18 owns the typed shape of the actual replay result record + the
fetch / load / construct surface.

**Fail-closed semantics (feedback_no_silent_degradation).** The
validator NEVER raises on input. On a structural internal failure
(e.g. an unexpected
:class:`pydantic.ValidationError` from a typed
:class:`ReplayRequirementValidationResult` construction), the validator
emits a typed :class:`ReplayRequirementValidationResult` with
``is_valid=False`` and a typed :class:`ReplayRequirementValidationGap`
carrying the failure reason; the corresponding typed failure id
:data:`REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID`
(``replay_requirement_validation_failed``) registers under the EXISTING
``evidence_corruption`` failure_class with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th sub-slice precedent
verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 2nd/3rd/4th non-blocking governance projection observer (the
replay-requirement validator is also a post-checkpoint governance
projection observer).

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-17:240-289).** :attr:`ReplayRequirementValidationGap.evidence_refs`
is a list of Slice 13a shared
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
(imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
redefined here). Per doc-13a:285-287 step 9 the shared model is the
authority for governance evidence-ref semantics; the typed cross-slice
reference shape on the gap consumes the Slice 13a shape directly.

**Implementation discipline.** Stdlib (``datetime``) + Pydantic v2 +
Slice 13a modules (``..workflows.develop.governance.models``) + Slice
17 1st sub-slice (``.policy_recommendation``) only. NO imports from
``governance/`` outside ``governance.models``. NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be consumer-side activation surfaces, not validation
dependencies). NO imports from Slice 18 modules (Slice 18 has not
landed at this iteration; even when it lands, the boundary remains
the same -- no second source of replay truth). NO imports from Slice
17 2nd/3rd/4th sub-slice modules (the replay-requirement validator is
a downstream observer of the recommendation BaseModel surface, not of
the builder/validator/writer pipelines). NO mutation of any existing
``execution_control/`` module (per the implementer prompt
§ "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.policy_recommendation` (Slice
17 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.policy_validation_interface`
(Slice 17 3rd sub-slice) +
:mod:`iriai_build_v2.execution_control.decision_record_writer` (Slice
17 4th sub-slice) verbatim: ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``bool`` / ``list`` /
``datetime`` / Literal). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field
validates at construction; unknown values fail closed via Literal range
+ ``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 17 3rd sub-slice :class:`PolicyValidationInterface` precedent
verbatim without introducing new abstractions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-17:173-174 + doc-14:242-243 NON-BLOCKING).
    "REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID",
    # Typed gap-reason Literal const (extracted for the failure id row
    # explanation + the validator's gap construction).
    "REPLAY_REQUIREMENT_MISSING_REASON",
    # Typed BaseModels (mirrors Slice 17 3rd sub-slice ValidationViolation
    # + ValidationResult precedent + Slice 17 4th sub-slice
    # DecisionPersistenceGap + DecisionWriterResult precedent).
    "ReplayRequirementHook",
    "ReplayRequirementValidationGap",
    "ReplayRequirementValidationResult",
    # The validator class (doc-17:173-174 step 5) and the module-level
    # convenience function that wraps the default dispatch.
    "ReplayRequirementValidator",
    "validate_replay_requirement",
]


# --- Typed failure id (doc-17:173-174 + doc-14:242-243 NON-BLOCKING) --------


REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID: Literal[
    "replay_requirement_validation_failed"
] = "replay_requirement_validation_failed"
"""Doc-17:173-174 + doc-14:242-243 -- the typed failure id the
replay-requirement validator projects onto when an internal validation
step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th sub-slice precedent
verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 5th pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 2nd + 3rd + 4th sub-slice non-blocking governance projection
observer (the replay-requirement validator is also a post-checkpoint
governance projection observer).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
+ Slice 17 2nd / 3rd / 4th sub-slice precedents are the
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

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to replay-requirement
validation failures (this slice is also a post-checkpoint governance
projection observer).
"""


# --- Typed gap-reason Literal const -----------------------------------------


REPLAY_REQUIREMENT_MISSING_REASON: Literal[
    "replay_requirement_missing"
] = "replay_requirement_missing"
"""Doc-17:173-174 -- the typed gap-reason for a behavior-changing
recommendation that lacks the cross-slice Slice 18 counterfactual
replay reference.

Per doc-17:173-174 step 5 the replay-requirement validator emits a
typed :class:`ReplayRequirementValidationGap` with this reason when the
input recommendation has ``safe_runtime_action=True`` AND the
``counterfactual_result_refs`` list is empty.

The Literal-typed constant lets downstream consumers (e.g. the future
Slice 17 6th sub-slice consumer read APIs + the Slice 19 governance
agent reporting surface) pattern-match the reason string against a
typed value rather than a free-form string.
"""


# --- Typed ReplayRequirementHook BaseModel ----------------------------------


class ReplayRequirementHook(BaseModel):
    """Doc-17:173-174 -- typed cross-slice reference shape composing a
    governance recommendation's typed reference to Slice 18
    counterfactual replay results.

    A :class:`ReplayRequirementHook` is the typed audit record carrying
    the typed cross-slice reference shape per doc-17:173-174 step 5:

    * ``recommendation_id`` -- the typed reference back to the
      :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
      the hook applies to.
    * ``counterfactual_result_refs`` -- the typed ``list[str]`` of
      Slice 18 counterfactual-replay result ref-strings the
      recommendation cites. This list is the typed cross-slice
      reference shape (NOT the typed replay-result BaseModel itself --
      that shape lives in Slice 18 per doc-17:159-163 + 225-226).
    * ``validated_at`` -- optional typed timestamp recording when the
      hook was constructed (mirrors the Slice 17 3rd sub-slice
      :attr:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult.validated_at`
      shape).
    * ``validator_version`` -- optional typed validator-version string
      recording which version of the validator produced the hook
      (for future audit-trail provenance; defaults to ``None``).

    Per **doc-17:159-163** + **doc-17:225-226** the typed shape carries
    ONLY the cross-slice reference (the list of ref-strings); it does
    NOT carry the actual replay result records. Slice 18 owns the typed
    shape of the actual replay result + the fetch / load / construct
    surface.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives (``str`` / ``list[str]``
    / ``datetime`` / ``str | None``).
    """

    # ``extra="forbid"`` aligns with the Slice 17 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/policy_recommendation.py:376
    # + the Slice 17 3rd sub-slice precedent at
    # src/iriai_build_v2/execution_control/policy_validation_interface.py:294
    # + the Slice 17 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/decision_record_writer.py:616
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    """Doc-17:77 + doc-17:173-174 -- the typed reference back to the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
    the hook applies to."""

    counterfactual_result_refs: list[str]
    """Doc-17:82 + doc-17:173-174 -- the typed list of Slice 18
    counterfactual-replay result ref-strings the recommendation cites.

    Per **doc-17:82** the recommendation's
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
    field is the source-of-truth for this list; the hook composes the
    same typed shape verbatim (the hook is the typed cross-slice
    reference shape, NOT a duplicate of the recommendation field).

    Per **doc-17:159-163 + doc-17:225-226** the list carries the
    ref-strings only (NOT the typed replay-result BaseModels); Slice
    18 owns the typed shape of the actual replay-result + the
    fetch / load / construct surface. Per the
    ``feedback_no_overengineer_use_library`` rule the cross-slice
    reference shape is a flat ``list[str]`` (mirroring the Slice 17
    1st sub-slice :attr:`GovernancePolicyRecommendation.counterfactual_result_refs`
    shape verbatim).

    The list MAY be empty for non-behavior-changing recommendations
    (``safe_runtime_action=False``); the :class:`ReplayRequirementValidator`
    rejects only behavior-changing recommendations (``safe_runtime_action=True``)
    whose list is empty (per doc-17:173-174 + doc-17:60 +
    doc-17:198-200)."""

    validated_at: datetime | None = None
    """Doc-17:173-174 -- the optional typed timestamp recording when
    the hook was constructed.

    Defaults to ``None`` so the caller can defer the timestamp
    assignment to a deterministic point in the pipeline (per the
    Slice 17 3rd sub-slice :attr:`ValidationResult.validated_at`
    pattern). Per the Pydantic v2 + Slice 13A + Slice 14 + Slice 15 +
    Slice 16 + Slice 17 1st/2nd/3rd/4th sub-slice canonical-JSON
    discipline the datetime field projects to ISO-8601 string under
    :meth:`BaseModel.model_dump` with ``mode='json'``."""

    validator_version: str | None = None
    """Doc-17:173-174 -- the optional typed validator-version string
    recording which version of the
    :class:`ReplayRequirementValidator` produced the hook.

    Defaults to ``None`` so callers MAY omit the version when the
    validator surface is stable (e.g. the v1 surface this sub-slice
    introduces); future surface evolution can populate the field for
    audit-trail provenance. Per the
    ``feedback_flat_structured_output`` rule the field is a flat
    primitive (``str``)."""


# --- Typed ReplayRequirementValidationGap BaseModel -------------------------


class ReplayRequirementValidationGap(BaseModel):
    """Doc-17:173-174 + doc-14:242-243 -- typed governance-gap shape
    produced when the replay-requirement validator rejects a
    behavior-changing recommendation OR fails to validate the
    recommendation structurally.

    Mirrors the Slice 17 4th sub-slice
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
    per doc-17:173-174 governance-projection discipline) the gap is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id
    :data:`REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID`
    (``replay_requirement_validation_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-17:240-289).** :attr:`evidence_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["replay_requirement_validation_failed"]
    """Doc-17:173-174 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-17:173-174."""

    recommendation_id: str
    """The typed reference back to the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
    the gap applies to (mirrors the
    :attr:`ReplayRequirementHook.recommendation_id` shape)."""

    reason: str
    """Free-form gap reason. The Slice 17 5th sub-slice v1 surface
    emits two distinct reasons:

    * :data:`REPLAY_REQUIREMENT_MISSING_REASON`
      (``"replay_requirement_missing"``) -- the recommendation has
      ``safe_runtime_action=True`` but ``counterfactual_result_refs``
      is empty (the typed cross-slice reference is missing per
      doc-17:173-174).
    * ``"replay_requirement_internal_exception:<ExceptionName>"`` --
      a structural internal failure (e.g. an unexpected
      :class:`pydantic.ValidationError` during
      :class:`ReplayRequirementValidationResult` construction); per
      the ``feedback_no_silent_degradation`` rule the validator
      NEVER raises -- the typed gap projection carries the failure
      reason instead.

    The free-form ``str`` (rather than a Literal) lets future
    sub-slices add reasons without bumping the typed surface; the
    primary v1 reasons are bound to the
    :data:`REPLAY_REQUIREMENT_MISSING_REASON` Literal const."""

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """Doc-17:173-174 + doc-13:201-204 -- the typed observation
    timestamp for the gap. Defaults to UTC now; projects to ISO-8601
    string under :meth:`BaseModel.model_dump` with ``mode='json'``."""

    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Optional list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the gap cites.

    Defaults to empty list -- per doc-17:173-174 the gap is advisory;
    it does NOT mandate evidence refs on every gap (the gap is itself
    derived from the typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    surface which carries its own typed evidence chain via the
    :attr:`source_finding_ids` + :attr:`source_metric_refs` fields)."""


# --- Typed ReplayRequirementValidationResult BaseModel ----------------------


class ReplayRequirementValidationResult(BaseModel):
    """Doc-17:173-174 -- typed per-recommendation replay-requirement
    validation outcome.

    A :class:`ReplayRequirementValidationResult` is the typed audit
    record the
    :meth:`ReplayRequirementValidator.validate` method emits per
    recommendation. The shape carries:

    * ``recommendation_id`` -- the typed reference back to the
      :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
      the validation applies to.
    * ``is_valid`` -- typed boolean for whether the recommendation
      passes the replay-requirement check.
    * ``gap`` -- optional typed :class:`ReplayRequirementValidationGap`
      record when the recommendation FAILS the check (or when a
      structural internal failure occurs). Defaults to ``None`` for
      passing recommendations.
    * ``reasons`` -- optional typed list of human-readable reason
      strings explaining the result; populated with at least one
      reason when ``is_valid=False`` (per the
      ``feedback_never_truncate_decisions`` rule the validator
      preserves the FULL list of reasons rather than the first only).
    * ``validated_at`` -- optional typed timestamp; defaults to
      ``None`` so the dispatch class fills it at validation time
      (mirrors the Slice 17 3rd sub-slice
      :attr:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult.validated_at`
      pattern).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per **doc-17:170-171** *"Validation proves the artifact can be
    understood, not that it should be activated."* (the parent
    invariant for the Slice 17 3rd sub-slice :class:`ValidationResult`
    + this 5th sub-slice :class:`ReplayRequirementValidationResult`)
    -- a :class:`ReplayRequirementValidationResult` with
    ``is_valid=True`` does NOT grant the artifact activation
    authority; activation is consumer-owned per doc-17:159-163. The
    result is read-only with respect to consumer state.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    """Doc-17:77 + doc-17:173-174 -- the typed reference back to the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
    the validation applies to."""

    is_valid: bool
    """Doc-17:173-174 -- the typed boolean for whether the
    recommendation passes the replay-requirement check.

    Per doc-17:173-174 *"Add replay requirement hooks so any
    behavior-changing recommendation can point to Slice 18
    counterfactual results."* a behavior-changing recommendation
    (``safe_runtime_action=True``) passes only when its
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
    list is non-empty; non-behavior-changing recommendations
    (``safe_runtime_action=False``) pass trivially (per
    doc-17:198-200).

    Per doc-17:170-171 a ``True`` value does NOT grant the artifact
    activation authority; activation is consumer-owned per
    doc-17:159-163."""

    gap: ReplayRequirementValidationGap | None = None
    """Doc-17:173-174 + doc-14:242-243 -- the optional typed
    :class:`ReplayRequirementValidationGap` record when the
    recommendation FAILS the check.

    Defaults to ``None`` for passing recommendations
    (``is_valid=True``); populated with a typed gap when
    ``is_valid=False`` (either the replay-requirement-missing case
    per doc-17:173-174 OR a structural internal failure per
    ``feedback_no_silent_degradation``)."""

    reasons: list[str] = Field(default_factory=list)
    """Doc-17:173-174 -- the typed list of human-readable reason
    strings explaining the result.

    Empty (``[]``) when ``is_valid=True``; populated with one or more
    typed reasons when ``is_valid=False``. Per
    ``feedback_never_truncate_decisions`` the validator emits ALL
    reasons rather than the first only; the consumer-side audit
    surface gets the complete list of reasons the recommendation
    failed the check."""

    validated_at: datetime | None = None
    """Doc-17:173-174 -- the optional typed validation timestamp.
    Defaults to ``None`` when not provided so the dispatch class can
    fill it at validation time (mirrors the Slice 17 3rd sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_validation_interface.ValidationResult.validated_at`
    pattern)."""


# --- ReplayRequirementValidator dispatch class ------------------------------


class ReplayRequirementValidator:
    """Doc-17:173-174 step 5 -- replay-requirement validator for
    behavior-changing governance recommendations.

    A :class:`ReplayRequirementValidator` instance exposes the
    :meth:`validate` method that consumes a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    and returns a typed
    :class:`ReplayRequirementValidationResult` carrying the
    replay-requirement check outcome.

    Per doc-17:173-174 *"Add replay requirement hooks so any
    behavior-changing recommendation can point to Slice 18
    counterfactual results."* the validator is the typed gating
    surface that checks behavior-changing recommendations
    (``safe_runtime_action=True``) have the required cross-slice
    Slice 18 counterfactual replay reference (non-empty
    ``counterfactual_result_refs`` list).

    **Boundary: NO consumer-side activation authority (doc-17:217 +
    doc-17:159-163).** The validator does NOT call any consumer-side
    activation method; the :class:`ReplayRequirementValidationResult`
    is informational only. Per doc-17:170-171 *"Validation proves the
    artifact can be understood, not that it should be activated."* the
    typed result does NOT grant activation authority; activation
    belongs to the consumer-owned policy record per doc-17:159-163.

    **Boundary: NO second source of replay truth (doc-17:159-163 +
    doc-17:225-226 + Cross-Slice Dependencies enumeration).** The
    validator does NOT load, fetch, or construct any Slice 18
    counterfactual replay result records; it consumes ONLY the typed
    ref-string list from the recommendation. Slice 18 owns the typed
    shape of the actual replay-result + the fetch / load / construct
    surface; this validator's typed surface is the cross-reference
    shape only.

    **Boundary: NO consumer-module imports.** The validator does NOT
    import any consumer-side module (supervisor / dashboard /
    scheduler / planning / merge_queue / Slice 07 failure_router
    beyond the 4 pure-data add points for the typed failure id).
    Validation rules operate over the typed
    :class:`GovernancePolicyRecommendation` BaseModel SURFACE only.

    **Boundary: NO Slice 18 imports.** Slice 18 has not landed at this
    iteration; even when it lands, the validator does NOT import any
    Slice 18 module (no second source of replay truth per
    doc-17:159-163 + 225-226). Validation operates over the typed
    ``list[str]`` ref-string shape from the recommendation.

    **Fail-closed semantics (feedback_no_silent_degradation).** The
    validator NEVER raises on input. On a structural internal failure
    (e.g. an unexpected
    :class:`pydantic.ValidationError` from a typed
    :class:`ReplayRequirementValidationResult` construction), the
    validator emits a typed
    :class:`ReplayRequirementValidationResult` with ``is_valid=False``
    and a typed :class:`ReplayRequirementValidationGap` carrying the
    failure reason.

    Mirrors the Slice 17 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
    + Slice 17 4th sub-slice
    :class:`~iriai_build_v2.execution_control.decision_record_writer.DecisionRecordWriter`
    pattern: a typed bundle-driven projection class without state.

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the class is stateless and exposes a single method; per the
    auto-memory ``feedback_no_silent_degradation`` rule every Pydantic
    construction is wrapped in a typed gap projection.

    Example usage::

        from iriai_build_v2.execution_control.replay_requirement_hook import (
            ReplayRequirementValidator,
        )
        from iriai_build_v2.execution_control.policy_recommendation import (
            GovernancePolicyRecommendation,
        )

        validator = ReplayRequirementValidator()
        result = validator.validate(recommendation)
        if not result.is_valid:
            # The recommendation is behavior-changing but lacks the
            # cross-slice Slice 18 counterfactual replay reference;
            # the typed gap projection routes through
            # REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID.
            ...
    """

    def __init__(self) -> None:
        """Construct a :class:`ReplayRequirementValidator`.

        The constructor takes no arguments per the
        ``feedback_no_overengineer_use_library`` rule: the validator
        is stateless. Future per-recommendation rule overrides (per
        the doc-17:173-174 *"per any behavior-changing recommendation"*
        extension point) live on the bound :meth:`validate` method via
        the typed
        :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        dispatch.

        Mirrors the Slice 17 3rd sub-slice
        :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
        constructor verbatim.
        """

        # Stateless per the prompt's "Validator NEVER raises" +
        # "Validator GRANTS NO ACTIVATION AUTHORITY" + "No second
        # source of replay truth" boundaries.
        pass

    def validate(
        self,
        recommendation: GovernancePolicyRecommendation,
    ) -> ReplayRequirementValidationResult:
        """Doc-17:173-174 step 5 -- validate a typed
        :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        against the replay-requirement check.

        Dispatch logic:

        * If the recommendation's typed
          :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.safe_runtime_action`
          is ``False`` (or missing): pass trivially (per
          doc-17:198-200 a non-behavior-changing recommendation
          cannot be consumed by runtime policy at all; the replay
          reference is not required) -- return
          :class:`ReplayRequirementValidationResult` with
          ``is_valid=True``, ``gap=None``, empty ``reasons``.

        * If ``safe_runtime_action=True`` AND
          :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
          is empty: REJECT (per doc-17:173-174 + doc-17:60 the typed
          cross-slice reference MUST be populated for behavior-
          changing recommendations) -- return
          :class:`ReplayRequirementValidationResult` with
          ``is_valid=False``, ``gap`` populated with a typed
          :class:`ReplayRequirementValidationGap`
          (``reason=REPLAY_REQUIREMENT_MISSING_REASON``).

        * If ``safe_runtime_action=True`` AND
          ``counterfactual_result_refs`` is non-empty: PASS (the typed
          cross-slice reference is populated) -- return
          :class:`ReplayRequirementValidationResult` with
          ``is_valid=True``, ``gap=None``, empty ``reasons``.

        Per **doc-17:159-163 + doc-17:225-226 + Cross-Slice
        Dependencies enumeration** the validator does NOT load /
        fetch / construct any Slice 18 counterfactual replay result
        records; it consumes ONLY the typed ref-string list from the
        recommendation. Slice 18 owns the typed shape of the actual
        replay-result + the fetch / load / construct surface; this
        validator's typed surface is the cross-reference shape only.

        Per **doc-17:217** + **doc-17:170-171** the validator GRANTS
        NO consumer-side activation authority; a passing result does
        NOT activate the recommendation's proposed policy artifact;
        activation is consumer-owned per doc-17:159-163.

        Per ``feedback_no_silent_degradation`` the method NEVER raises:
        a structural internal failure (e.g. an unexpected
        :class:`pydantic.ValidationError` during typed
        :class:`ReplayRequirementValidationResult` construction)
        emits a typed
        :class:`ReplayRequirementValidationResult` with
        ``is_valid=False`` and a typed
        :class:`ReplayRequirementValidationGap` carrying the failure
        reason. The validator preserves all reasons per the
        ``feedback_never_truncate_decisions`` rule.

        Returns a typed :class:`ReplayRequirementValidationResult`.
        """

        try:
            recommendation_id = recommendation.recommendation_id
            safe_runtime_action = recommendation.safe_runtime_action
            counterfactual_result_refs = list(
                recommendation.counterfactual_result_refs
            )

            # Pass-trivially path: non-behavior-changing recommendation
            # cannot be consumed by runtime policy (per doc-17:198-200);
            # the replay reference is not required.
            if not safe_runtime_action:
                return ReplayRequirementValidationResult(
                    recommendation_id=recommendation_id,
                    is_valid=True,
                    gap=None,
                    reasons=[],
                    validated_at=datetime.now(tz=timezone.utc),
                )

            # Behavior-changing path: check the cross-slice reference
            # is populated. Per doc-17:173-174 + doc-17:60 the typed
            # cross-slice reference MUST be populated for behavior-
            # changing recommendations.
            if not counterfactual_result_refs:
                gap = ReplayRequirementValidationGap(
                    failure_id=REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
                    recommendation_id=recommendation_id,
                    reason=REPLAY_REQUIREMENT_MISSING_REASON,
                )
                return ReplayRequirementValidationResult(
                    recommendation_id=recommendation_id,
                    is_valid=False,
                    gap=gap,
                    reasons=[
                        (
                            "GovernancePolicyRecommendation.counterfactual_result_refs "
                            "list is empty for a behavior-changing recommendation "
                            "(safe_runtime_action=True); doc-17:173-174 requires "
                            "behavior-changing recommendations to point to Slice 18 "
                            "counterfactual results; doc-17:60 enumerates the "
                            "lacking-source-evidence case as a blocking deviation."
                        ),
                    ],
                    validated_at=datetime.now(tz=timezone.utc),
                )

            # Behavior-changing AND populated path: PASS. Per
            # doc-17:159-163 + 225-226 the validator does NOT load /
            # fetch / construct the actual replay results; only checks
            # the typed cross-reference is populated.
            return ReplayRequirementValidationResult(
                recommendation_id=recommendation_id,
                is_valid=True,
                gap=None,
                reasons=[],
                validated_at=datetime.now(tz=timezone.utc),
            )

        except Exception as exc:
            # Structural internal failure (e.g. an unexpected
            # ValidationError from a typed
            # ReplayRequirementValidationResult construction above, or
            # the recommendation's typed fields raise unexpectedly).
            # Per feedback_no_silent_degradation the validator NEVER
            # raises: emit a typed
            # ReplayRequirementValidationResult with is_valid=False
            # and a typed ReplayRequirementValidationGap carrying the
            # failure reason. Per doc-14:242-243 this projects onto
            # the NON-blocking typed failure id
            # REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID under EXISTING
            # evidence_corruption failure_class with REUSED
            # retry_governance_projection action (registered in the
            # Slice 17 5th sub-slice failure_router 4 pure-data add
            # points).
            try:
                recommendation_id = recommendation.recommendation_id
            except Exception:
                # If even reading the recommendation's own id raises,
                # fall back to a placeholder so the typed gap
                # projection's recommendation_id field still validates.
                recommendation_id = "unknown_recommendation_id"
            gap = ReplayRequirementValidationGap(
                failure_id=REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
                recommendation_id=recommendation_id,
                reason=(
                    f"replay_requirement_internal_exception:"
                    f"{type(exc).__name__}"
                ),
            )
            return ReplayRequirementValidationResult(
                recommendation_id=recommendation_id,
                is_valid=False,
                gap=gap,
                reasons=[
                    (
                        f"ReplayRequirementValidator.validate raised internally: "
                        f"{type(exc).__name__}: {exc!s}; this is a typed gap "
                        "projection per REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID."
                    ),
                ],
                validated_at=datetime.now(tz=timezone.utc),
            )


# --- Module-level convenience function --------------------------------------


def validate_replay_requirement(
    recommendation: GovernancePolicyRecommendation,
) -> ReplayRequirementValidationResult:
    """Doc-17:173-174 step 5 -- module-level convenience wrapper for
    :meth:`ReplayRequirementValidator.validate` against a
    default-constructed :class:`ReplayRequirementValidator` instance.

    Callers that need only the default replay-requirement check (no
    per-recommendation overrides) can use this module-level function
    without constructing the validator explicitly. Callers that need
    to extend the validator (e.g. future Slice 17 6th/7th sub-slice
    consumer read APIs that may add per-rule callbacks) should
    construct a :class:`ReplayRequirementValidator` directly.

    Mirrors the
    :func:`~iriai_build_v2.execution_control.policy_validation_interface.validate_recommendation`
    module-level pattern (Slice 17 3rd sub-slice) +
    :func:`~iriai_build_v2.execution_control.recommendation_builder.compute_recommendation_id`
    pattern (Slice 17 2nd sub-slice) +
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    module-level pattern (Slice 17 1st sub-slice).
    """

    return ReplayRequirementValidator().validate(recommendation)
