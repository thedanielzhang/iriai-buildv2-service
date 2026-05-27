"""Slice 18 seventh sub-slice -- recommendation citation hook (typed
cross-slice validator) for behavior-changing governance recommendations
citing Slice 18 counterfactual replay results.

Per ``docs/execution-control-plane/18-counterfactual-replay-and-simulation.md``
§ Refactoring Steps step 7 (lines 117-119): *"Require Slice 17
recommendations to cite counterfactual results for any behavior-changing
policy."*

Per **doc-18:165-166** § Acceptance Criteria AC4:
*"Recommendations that affect runtime behavior cite replay results or
explicitly say more evidence is needed."*

The 7th sub-slice cross-slice citation hook is the typed validator
surface that pins AC4 operationally: it consumes the typed Slice 17
1st sub-slice :class:`GovernancePolicyRecommendation` + the typed
Slice 18 1st sub-slice :class:`CounterfactualResult` list and emits a
typed :class:`CitationSufficiencyResult` declaring whether the
recommendation's citations are sufficient per the AC4 binding.

Per **doc-17:159-163** + **doc-17:225-226** + the Cross-Slice
Dependencies enumeration the validator OWNS NO replay-result truth: it
consumes ONLY the typed cross-reference list (the
``counterfactual_result_refs: list[str]`` field from the
recommendation) + the typed
:class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
list provided as input. Slice 18 1st/2nd/3rd/4th/5th/6th sub-slices
own the typed shape + the fetch / load / construct / write surface.
This validator's typed surface is the citation-sufficiency check ONLY.

This is the cross-slice counterpart of the Slice 17 5th sub-slice
:class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
(which checks that behavior-changing recommendations carry NON-EMPTY
``counterfactual_result_refs`` at the Slice 17 surface) -- the Slice
18 7th sub-slice validator extends the contract one step further:
the ref-strings the recommendation cites MUST resolve to actual
typed :class:`CounterfactualResult.result_id` values in the input list
OR the recommendation MUST explicitly signal
``status="needs_more_evidence"`` (per doc-18:165-166 AC4 verbatim).

Per **doc-18:165-166** the AC4 contract has TWO equally-valid axes:

1. **Cite replay results.** Behavior-changing recommendations
   (``safe_runtime_action=True``) MUST cite at least one Slice 18
   :attr:`CounterfactualResult.result_id` via the
   ``counterfactual_result_refs`` list, AND every cited ref-string
   MUST resolve to a provided result_id.
2. **Explicitly say more evidence is needed.** Behavior-changing
   recommendations MAY omit citations IF AND ONLY IF the
   recommendation's ``status`` is ``"needs_more_evidence"`` (per
   doc-17:339-341 + doc-17:301; the typed status surface includes
   ``needs_more_evidence`` precisely for this AC4 binding).

Non-behavior-changing recommendations (``safe_runtime_action=False``)
pass trivially per **doc-17:198-200** *"Safe runtime action false:
recommendation can be reported but not consumed by runtime policy
without a later implementation plan."* -- the AC4 binding gates ONLY
behavior-changing recommendations.

Per **doc-17:217** (Acceptance Criteria): *"Recommendation generation
has no direct mutation authority."* + **doc-18:123-125** (Persistence
And Artifact Compatibility): *"Replay results are review/governance
artifacts only. Replay must not write `dag-*` execution authority
artifacts or active policy markers."* -- the citation validator GRANTS
NO CONSUMER-SIDE ACTIVATION AUTHORITY; a passing typed
:class:`CitationSufficiencyResult` does NOT activate the
recommendation's proposed policy artifact; activation remains
consumer-owned per doc-17:159-163. The validator is informational only:
it returns the typed :class:`CitationSufficiencyResult` record and
does NOT mutate any consumer state.

**No second source of replay truth (doc-17:159-163 + doc-17:225-226 +
doc-18:123-125 + Cross-Slice Dependencies enumeration).** Per the
structural boundary this module:

* Does NOT load, fetch, or construct any
  :class:`CounterfactualResult` records (consumes the typed list
  provided as input).
* Does NOT execute, replay, or simulate any counterfactual scenario.
* Does NOT import Slice 18 2nd-6th sub-slice engines / loaders /
  writers (loader / scenario builder / summary-replay engine /
  event-replay engine / metrics-comparator / result-writer). Only
  the Slice 18 1st sub-slice typed shape
  :class:`CounterfactualResult` is consumed via direct import.
* Does NOT define a local typed Slice-18-result BaseModel; the typed
  result shape lives in Slice 18 1st sub-slice per doc-18:79-96.

The typed cross-slice reference shape is the
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs:
list[str]` carried by the recommendation; the validator's job is to
check the list's ref-strings resolve to provided
:attr:`CounterfactualResult.result_id` values OR the recommendation
status is ``"needs_more_evidence"``.

**Fail-closed semantics (feedback_no_silent_degradation).** The
validator NEVER raises on input. On a structural internal failure
(e.g. an unexpected :class:`pydantic.ValidationError` from a typed
:class:`CitationSufficiencyResult` construction), the validator emits
a typed :class:`CitationSufficiencyResult` with ``is_sufficient=False``
and a typed :class:`CitationGap` carrying the failure reason; the
corresponding typed failure id
:data:`RECOMMENDATION_CITATION_FAILURE_ID`
(``recommendation_citation_validation_failed``) registers under the
EXISTING ``evidence_corruption`` failure_class with the EXISTING
NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd + 3rd + 4th + 5th + 6th sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 7th pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 + Slice 18 prior non-blocking governance projection observer
(the citation validator is also a post-checkpoint governance
projection observer).

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-18:186-249).** :attr:`CitationGap.evidence_refs` is a list of
Slice 13a shared
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
(imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
redefined here). Per doc-13a:285-287 step 9 the shared model is the
authority for governance evidence-ref semantics; the typed gap on the
result consumes the Slice 13a shape directly.

**Implementation discipline.** Stdlib (``datetime``) + Pydantic v2 +
Slice 13a modules (``..workflows.develop.governance.models``) + Slice
17 1st sub-slice (``.policy_recommendation``) + Slice 18 1st sub-slice
(``.counterfactual_replay``) only. NO imports from ``governance/``
outside ``governance.models``. NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be consumer-side activation surfaces, not validation
dependencies). NO imports from Slice 18 2nd-6th sub-slice modules (the
citation validator is a downstream observer of the result shape only,
not of the loader / engines / writer pipelines). NO imports from Slice
17 2nd-6th sub-slice modules (the citation validator is a downstream
observer of the recommendation BaseModel surface, not of the
builder / validator / writer pipelines). NO mutation of any existing
``execution_control/`` module (per the implementer prompt
§ "Non-negotiables").

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.policy_recommendation` (Slice
17 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.replay_requirement_hook` (Slice
17 5th sub-slice) +
:mod:`iriai_build_v2.execution_control.counterfactual_replay` (Slice
18 1st sub-slice) verbatim: ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``bool`` / ``list`` /
``datetime`` / Literal). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field
validates at construction; unknown values fail closed via Literal range
+ ``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 17 5th sub-slice
:class:`ReplayRequirementValidator` precedent verbatim without
introducing new abstractions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-18:117-119 + doc-14:242-243 NON-BLOCKING).
    "RECOMMENDATION_CITATION_FAILURE_ID",
    # Typed gap-reason Literal const (extracted for the failure id row
    # explanation + the validator's gap construction).
    "RECOMMENDATION_CITATION_MISSING_REASON",
    "RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON",
    # Typed BaseModels (mirrors Slice 17 5th sub-slice
    # ReplayRequirementValidationGap + ReplayRequirementValidationResult
    # precedent verbatim).
    "RecommendationCitationHookInputs",
    "CitationGap",
    "CitationSufficiencyResult",
    # The validator class (doc-18:117-119 step 7) and the module-level
    # convenience function that wraps the default dispatch.
    "RecommendationCitationValidator",
    "validate_recommendation_citation",
]


# --- Typed failure id (doc-18:117-119 + doc-14:242-243 NON-BLOCKING) --------


RECOMMENDATION_CITATION_FAILURE_ID: Literal[
    "recommendation_citation_validation_failed"
] = "recommendation_citation_validation_failed"
"""Doc-18:117-119 + doc-14:242-243 -- the typed failure id the
recommendation-citation validator projects onto when an internal
validation step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd + 3rd + 4th + 5th + 6th sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 7th pattern matches the Slice 14 + Slice 15 + Slice 16 +
Slice 17 + Slice 18 prior sub-slice non-blocking governance projection
observer (the citation validator is also a post-checkpoint governance
projection observer).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
+ Slice 17 2nd / 3rd / 4th / 5th / 6th + Slice 18 2nd / 3rd / 4th /
5th / 6th sub-slice precedents are the source-of-truth for the
non-blocking governance-projection failure-routing discipline:

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
* :mod:`iriai_build_v2.execution_control.consumer_read_api`
  (Slice 17 6th) defines ``consumer_read_api_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_replay_loader`
  (Slice 18 2nd) defines ``replay_corpus_or_scenario_load_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_summary_replay`
  (Slice 18 3rd) defines ``summary_replay_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_event_replay`
  (Slice 18 4th) defines ``event_replay_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_metrics_comparator`
  (Slice 18 5th) defines ``metrics_comparator_failed``.
* :mod:`iriai_build_v2.execution_control.counterfactual_result_writer`
  (Slice 18 6th) defines ``counterfactual_result_persistence_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to
recommendation-citation validation failures (this slice is also a
post-checkpoint governance projection observer + per doc-18:123-125
replay results are review/governance artifacts only -- never runtime
policy authority).
"""


# --- Typed gap-reason Literal consts ----------------------------------------


RECOMMENDATION_CITATION_MISSING_REASON: Literal[
    "recommendation_citation_missing"
] = "recommendation_citation_missing"
"""Doc-18:117-119 + doc-18:165-166 -- the typed gap-reason for a
behavior-changing recommendation that lacks the cross-slice Slice 18
counterfactual replay citation AND does NOT explicitly signal
``status="needs_more_evidence"``.

Per **doc-18:117-119 step 7** + **doc-18:165-166 AC4** the citation
validator emits a typed :class:`CitationGap` with this reason when the
input recommendation has:

* ``safe_runtime_action=True`` (behavior-changing per doc-17:86 +
  doc-17:198-200), AND
* ``counterfactual_result_refs`` is empty (no citation per doc-17:82
  ref-string list), AND
* ``status`` is NOT ``"needs_more_evidence"`` (no explicit AC4
  more-evidence-needed signal per doc-17:339-341 +
  doc-17:301).

This reason is INTENTIONALLY DIFFERENT from the Slice 17 5th sub-slice
:data:`~iriai_build_v2.execution_control.replay_requirement_hook.REPLAY_REQUIREMENT_MISSING_REASON`
(``"replay_requirement_missing"``): the Slice 17 5th sub-slice
validator checks ONLY the non-empty-refs invariant at the recommendation
surface; the Slice 18 7th sub-slice validator extends the AC4 contract
ONE step further to either require the refs resolve to provided typed
:class:`CounterfactualResult.result_id` values OR explicit
``"needs_more_evidence"`` status (the *"or explicitly say more
evidence is needed"* axis of doc-18:165-166 AC4 verbatim).

The Literal-typed constant lets downstream consumers (e.g. the Slice
19 governance agent reporting surface) pattern-match the reason string
against a typed value rather than a free-form string.
"""


RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON: Literal[
    "recommendation_citation_unresolved_refs"
] = "recommendation_citation_unresolved_refs"
"""Doc-18:117-119 + doc-18:165-166 -- the typed gap-reason for a
behavior-changing recommendation whose ``counterfactual_result_refs``
ref-strings do NOT all resolve to provided typed
:attr:`CounterfactualResult.result_id` values.

Per **doc-18:117-119 step 7** + **doc-18:165-166 AC4** the citation
validator emits a typed :class:`CitationGap` with this reason when the
input recommendation has:

* ``safe_runtime_action=True`` (behavior-changing per doc-17:86 +
  doc-17:198-200), AND
* ``counterfactual_result_refs`` is non-empty (citation provided per
  doc-17:82 ref-string list), AND
* at least one ref-string does NOT resolve to a typed
  :attr:`CounterfactualResult.result_id` in the provided
  :attr:`RecommendationCitationHookInputs.counterfactual_results` list
  (broken cross-reference; the typed cross-slice contract requires
  every cited ref to resolve to a provided result_id per the
  *"cite replay results"* axis of doc-18:165-166 AC4).

This reason captures the broken-cross-reference case which is
structurally distinct from the missing-citation case
(:data:`RECOMMENDATION_CITATION_MISSING_REASON`).

The Literal-typed constant lets downstream consumers pattern-match the
reason string against a typed value rather than a free-form string.
"""


# --- Typed RecommendationCitationHookInputs BaseModel -----------------------


class RecommendationCitationHookInputs(BaseModel):
    """Doc-18:117-119 + doc-18:165-166 -- typed input bundle for the
    :class:`RecommendationCitationValidator.validate` method.

    A :class:`RecommendationCitationHookInputs` carries the typed input
    pair the citation validator consumes:

    * ``recommendation`` -- the typed Slice 17 1st sub-slice
      :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
      under validation (the field surface carrying
      ``safe_runtime_action`` + ``counterfactual_result_refs`` +
      ``status`` + ``recommendation_id``).
    * ``counterfactual_results`` -- the typed list of Slice 18 1st
      sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
      records (the typed shape the recommendation's
      ``counterfactual_result_refs`` resolve against per doc-18:80
      :attr:`CounterfactualResult.result_id` cross-reference).

    Per **doc-17:159-163** + **doc-17:225-226** + **doc-18:123-125** +
    **Cross-Slice Dependencies enumeration** the typed input bundle
    carries the typed :class:`CounterfactualResult` list provided BY
    THE CALLER (Slice 19 governance agent / Slice 17 consumer);
    this module does NOT load / fetch / construct the list itself.
    Slice 18 1st/2nd/3rd/4th/5th/6th sub-slices own the typed shape +
    the fetch / load / construct / write surface.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives + typed BaseModels
    (``GovernancePolicyRecommendation`` /
    ``list[CounterfactualResult]``); no nested structured-output
    signaling.
    """

    # ``extra="forbid"`` aligns with the Slice 17 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/policy_recommendation.py:706
    # + the Slice 17 5th sub-slice precedent at
    # src/iriai_build_v2/execution_control/replay_requirement_hook.py:340
    # + the Slice 18 1st sub-slice precedent at
    # src/iriai_build_v2/execution_control/counterfactual_replay.py:673
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    recommendation: GovernancePolicyRecommendation
    """Doc-17:75-97 + doc-18:117-119 -- the typed Slice 17 1st sub-slice
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    under citation-sufficiency validation.

    Per **doc-17:159-163** the typed BaseModel is the source-of-truth
    surface; the citation validator reads the typed fields
    (``safe_runtime_action`` per doc-17:86 +
    ``counterfactual_result_refs`` per doc-17:82 + ``status`` per
    doc-17:79 + ``recommendation_id`` per doc-17:77) WITHOUT mutation.
    """

    counterfactual_results: list[CounterfactualResult] = Field(
        default_factory=list
    )
    """Doc-18:79-96 + doc-18:117-119 -- the typed list of Slice 18 1st
    sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
    records the recommendation's
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
    resolve against.

    Per **doc-18:80** the typed :attr:`CounterfactualResult.result_id`
    is the cross-reference handle; the validator checks each ref-string
    in the recommendation's ``counterfactual_result_refs`` resolves to
    one of the typed result_id values in this list.

    Per **doc-17:159-163** + **doc-17:225-226** + **doc-18:123-125** +
    Cross-Slice Dependencies enumeration the typed list is provided BY
    THE CALLER (Slice 19 governance agent / Slice 17 consumer); this
    module does NOT load / fetch / construct the list itself. Slice 18
    1st/2nd/3rd/4th/5th/6th sub-slices own the typed shape + the fetch
    / load / construct / write surface.

    Defaults to empty list -- per the AC4 binding the empty-list case
    is the structural counterpart to the missing-citation case (the
    behavior-changing recommendation has no result to cite); the typed
    surface fails closed at the validator dispatch rather than the
    input construction.
    """


# --- Typed CitationGap BaseModel --------------------------------------------


class CitationGap(BaseModel):
    """Doc-18:117-119 + doc-18:165-166 + doc-14:242-243 -- typed
    governance-gap shape produced when the citation validator rejects a
    behavior-changing recommendation OR fails to validate the
    recommendation structurally.

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
    per doc-18:117-119 governance-projection discipline) the gap is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`RECOMMENDATION_CITATION_FAILURE_ID`
    (``recommendation_citation_validation_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-18:186-249).** :attr:`evidence_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here).
    """

    # ``extra="forbid"`` aligns with the sibling typed shapes.
    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["recommendation_citation_validation_failed"]
    """Doc-18:117-119 + doc-14:192-201 -- the typed failure id.
    Registers under the EXISTING ``evidence_corruption`` failure_class
    with NON-blocking routing per doc-14:242-243 + doc-18:117-119."""

    recommendation_id: str
    """The typed reference back to the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
    the gap applies to (mirrors the Slice 17 5th sub-slice
    :attr:`ReplayRequirementValidationGap.recommendation_id` shape)."""

    reason: str
    """Free-form gap reason. The Slice 18 7th sub-slice v1 surface
    emits three distinct reasons:

    * :data:`RECOMMENDATION_CITATION_MISSING_REASON`
      (``"recommendation_citation_missing"``) -- the recommendation has
      ``safe_runtime_action=True`` AND empty
      ``counterfactual_result_refs`` AND status is NOT
      ``"needs_more_evidence"`` (the AC4 binding fails on BOTH axes
      per doc-18:165-166).
    * :data:`RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON`
      (``"recommendation_citation_unresolved_refs"``) -- the
      recommendation has ``safe_runtime_action=True`` AND non-empty
      ``counterfactual_result_refs`` AND at least one ref-string does
      NOT resolve to a provided typed
      :attr:`CounterfactualResult.result_id` (broken cross-reference;
      the *"cite replay results"* axis of doc-18:165-166 AC4 fails).
    * ``"recommendation_citation_internal_exception:<ExceptionName>"``
      -- a structural internal failure (e.g. an unexpected
      :class:`pydantic.ValidationError` during
      :class:`CitationSufficiencyResult` construction); per the
      ``feedback_no_silent_degradation`` rule the validator NEVER
      raises -- the typed gap projection carries the failure reason
      instead.

    The free-form ``str`` (rather than a Literal) lets future
    sub-slices add reasons without bumping the typed surface; the
    primary v1 reasons are bound to the
    :data:`RECOMMENDATION_CITATION_MISSING_REASON` +
    :data:`RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON` Literal
    consts."""

    observed_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    """Doc-18:117-119 + doc-13:201-204 -- the typed observation
    timestamp for the gap. Defaults to UTC now; projects to ISO-8601
    string under :meth:`BaseModel.model_dump` with ``mode='json'``."""

    unresolved_refs: list[str] = Field(default_factory=list)
    """Optional list of ref-strings the recommendation cited that did
    NOT resolve to a provided typed
    :attr:`CounterfactualResult.result_id`.

    Populated when ``reason`` is
    :data:`RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON` -- carries
    the exact ref-strings the caller can pass back to the upstream
    Slice 18 result-loader / writer to diagnose the broken
    cross-reference. Defaults to empty list for the missing-citation
    case (no refs to report) and the structural-internal-failure case.

    Per the ``feedback_never_truncate_decisions`` rule the gap emits
    the FULL list of unresolved refs rather than the first only; the
    downstream audit surface gets the complete list."""

    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Optional list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the gap cites.

    Defaults to empty list -- per doc-18:117-119 the gap is advisory;
    it does NOT mandate evidence refs on every gap (the gap is itself
    derived from the typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    + typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
    surfaces which carry their own typed evidence chains via the
    :attr:`GovernancePolicyRecommendation.source_finding_ids` +
    :attr:`source_metric_refs` +
    :attr:`CounterfactualResult.policy_provenance_refs` fields)."""


# --- Typed CitationSufficiencyResult BaseModel ------------------------------


class CitationSufficiencyResult(BaseModel):
    """Doc-18:117-119 + doc-18:165-166 -- typed per-recommendation
    citation-sufficiency outcome.

    A :class:`CitationSufficiencyResult` is the typed audit record the
    :meth:`RecommendationCitationValidator.validate` method emits per
    recommendation. The shape carries:

    * ``recommendation_id`` -- the typed reference back to the
      :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
      the validation applies to.
    * ``is_sufficient`` -- typed boolean for whether the
      recommendation's citations meet the doc-18:165-166 AC4 binding.
    * ``cited_result_ids`` -- optional typed list of typed
      :attr:`CounterfactualResult.result_id` values the recommendation
      successfully cited (populated when ``is_sufficient=True`` AND the
      recommendation is behavior-changing; empty for the
      non-behavior-changing-trivial-pass case + the
      needs-more-evidence-trivial-pass case + the insufficient cases).
    * ``missing_reason`` -- optional typed reason string explaining
      why the citation is insufficient (populated when
      ``is_sufficient=False``).
    * ``gap`` -- optional typed :class:`CitationGap` record when the
      recommendation FAILS the check (or when a structural internal
      failure occurs). Defaults to ``None`` for passing recommendations.
    * ``validated_at`` -- optional typed timestamp; defaults to
      ``None`` so the dispatch class fills it at validation time
      (mirrors the Slice 17 5th sub-slice
      :attr:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidationResult.validated_at`
      pattern).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per **doc-17:170-171** *"Validation proves the artifact can be
    understood, not that it should be activated."* (the parent
    invariant for the Slice 17 3rd sub-slice :class:`ValidationResult`
    + Slice 17 5th sub-slice :class:`ReplayRequirementValidationResult`
    + this 7th sub-slice :class:`CitationSufficiencyResult`)
    -- a :class:`CitationSufficiencyResult` with ``is_sufficient=True``
    does NOT grant the artifact activation authority; activation is
    consumer-owned per doc-17:159-163. The result is read-only with
    respect to consumer state.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    """Doc-17:77 + doc-18:117-119 -- the typed reference back to the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
    the validation applies to."""

    is_sufficient: bool
    """Doc-18:117-119 + doc-18:165-166 -- the typed boolean for whether
    the recommendation's citations satisfy AC4.

    Per **doc-18:165-166 AC4** *"Recommendations that affect runtime
    behavior cite replay results or explicitly say more evidence is
    needed."* the typed boolean is ``True`` in three SUFFICIENT cases:

    1. **Non-behavior-changing** (``safe_runtime_action=False``):
       trivially sufficient (no replay required per doc-17:198-200).
    2. **Behavior-changing + cited refs resolve**
       (``safe_runtime_action=True`` AND non-empty
       ``counterfactual_result_refs`` AND every ref-string resolves to
       a typed :attr:`CounterfactualResult.result_id` in the provided
       :attr:`RecommendationCitationHookInputs.counterfactual_results`
       list): sufficient on the *"cite replay results"* axis.
    3. **Behavior-changing + explicit needs-more-evidence**
       (``safe_runtime_action=True`` AND empty
       ``counterfactual_result_refs`` AND
       ``status="needs_more_evidence"``): sufficient on the *"or
       explicitly say more evidence is needed"* axis.

    Two INSUFFICIENT cases (``is_sufficient=False``):

    1. **Behavior-changing + missing citation + no needs-more-evidence
       signal** (``safe_runtime_action=True`` AND empty
       ``counterfactual_result_refs`` AND status is NOT
       ``"needs_more_evidence"``): the AC4 binding fails on BOTH
       axes; gap reason
       :data:`RECOMMENDATION_CITATION_MISSING_REASON`.
    2. **Behavior-changing + unresolved refs**
       (``safe_runtime_action=True`` AND non-empty
       ``counterfactual_result_refs`` AND at least one ref does NOT
       resolve to a provided result_id): the *"cite replay results"*
       axis fails; gap reason
       :data:`RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON`.

    Per doc-17:170-171 a ``True`` value does NOT grant the artifact
    activation authority; activation is consumer-owned per
    doc-17:159-163."""

    cited_result_ids: list[str] = Field(default_factory=list)
    """Doc-18:80 + doc-18:117-119 -- the typed list of
    :attr:`CounterfactualResult.result_id` values the recommendation
    successfully cited.

    Populated when ``is_sufficient=True`` AND the recommendation is
    behavior-changing AND the cited refs resolved (sufficient case 2);
    empty for the non-behavior-changing trivial-pass case (sufficient
    case 1) + the needs-more-evidence trivial-pass case (sufficient
    case 3) + both insufficient cases.

    Per the ``feedback_never_truncate_decisions`` rule the validator
    emits ALL successfully-cited result_ids rather than the first only;
    the consumer-side audit surface gets the complete list."""

    missing_reason: str | None = None
    """Doc-18:117-119 + doc-18:165-166 -- the typed reason string
    explaining why the citation is insufficient.

    Populated with one of the typed reason constants when
    ``is_sufficient=False``
    (:data:`RECOMMENDATION_CITATION_MISSING_REASON` or
    :data:`RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON` or
    ``"recommendation_citation_internal_exception:<ExceptionName>"``);
    defaults to ``None`` for passing recommendations
    (``is_sufficient=True``)."""

    gap: CitationGap | None = None
    """Doc-18:117-119 + doc-14:242-243 -- the optional typed
    :class:`CitationGap` record when the recommendation FAILS the
    check.

    Defaults to ``None`` for passing recommendations
    (``is_sufficient=True``); populated with a typed gap when
    ``is_sufficient=False`` (either the missing-citation case OR the
    unresolved-refs case OR a structural internal failure per
    ``feedback_no_silent_degradation``)."""

    validated_at: datetime | None = None
    """Doc-18:117-119 -- the optional typed validation timestamp.
    Defaults to ``None`` when not provided so the dispatch class can
    fill it at validation time (mirrors the Slice 17 5th sub-slice
    :attr:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidationResult.validated_at`
    pattern)."""


# --- RecommendationCitationValidator dispatch class -------------------------


class RecommendationCitationValidator:
    """Doc-18:117-119 step 7 -- recommendation citation validator for
    behavior-changing governance recommendations citing Slice 18
    counterfactual replay results.

    A :class:`RecommendationCitationValidator` instance exposes the
    :meth:`validate` method that consumes a typed
    :class:`RecommendationCitationHookInputs` bundle and returns a
    typed :class:`CitationSufficiencyResult` carrying the
    citation-sufficiency check outcome.

    Per **doc-18:117-119 step 7** *"Require Slice 17 recommendations
    to cite counterfactual results for any behavior-changing policy."*
    + **doc-18:165-166 AC4** *"Recommendations that affect runtime
    behavior cite replay results or explicitly say more evidence is
    needed."* the validator is the typed gating surface that checks
    behavior-changing recommendations (``safe_runtime_action=True``)
    satisfy the AC4 binding on at least one of two axes:

    1. **Cite replay results.** Non-empty
       ``counterfactual_result_refs`` AND every ref-string resolves
       to a typed :attr:`CounterfactualResult.result_id` in the
       provided list.
    2. **Explicitly say more evidence is needed.** Empty
       ``counterfactual_result_refs`` AND
       ``status="needs_more_evidence"``.

    **Boundary: NO consumer-side activation authority (doc-17:217 +
    doc-17:159-163 + doc-18:123-125).** The validator does NOT call
    any consumer-side activation method; the
    :class:`CitationSufficiencyResult` is informational only. Per
    doc-17:170-171 *"Validation proves the artifact can be understood,
    not that it should be activated."* the typed result does NOT grant
    activation authority; activation belongs to the consumer-owned
    policy record per doc-17:159-163.

    **Boundary: NO second source of replay truth (doc-17:159-163 +
    doc-17:225-226 + doc-18:123-125 + Cross-Slice Dependencies
    enumeration).** The validator does NOT load, fetch, or construct
    any :class:`CounterfactualResult` records; it consumes ONLY the
    typed list provided as input via
    :attr:`RecommendationCitationHookInputs.counterfactual_results`.
    Slice 18 1st/2nd/3rd/4th/5th/6th sub-slices own the typed shape +
    the fetch / load / construct / write surface; this validator's
    typed surface is the citation-sufficiency check ONLY.

    **Boundary: NO consumer-module imports.** The validator does NOT
    import any consumer-side module (supervisor / dashboard /
    scheduler / planning / merge_queue / Slice 07 failure_router
    beyond the 4 pure-data add points for the typed failure id).
    Validation rules operate over the typed
    :class:`RecommendationCitationHookInputs` BaseModel SURFACE only.

    **Boundary: NO Slice 18 engine/loader/writer imports.** Only the
    Slice 18 1st sub-slice typed shape
    :class:`CounterfactualResult` is consumed via direct import. The
    validator does NOT import Slice 18 2nd sub-slice loader / 3rd
    sub-slice summary-replay engine / 4th sub-slice event-replay
    engine / 5th sub-slice metrics-comparator / 6th sub-slice
    result-writer (no second source of replay truth per doc-17:159-163
    + 225-226 + doc-18:123-125).

    **Fail-closed semantics (feedback_no_silent_degradation).** The
    validator NEVER raises on input. On a structural internal failure
    (e.g. an unexpected :class:`pydantic.ValidationError` from a typed
    :class:`CitationSufficiencyResult` construction), the validator
    emits a typed :class:`CitationSufficiencyResult` with
    ``is_sufficient=False`` and a typed :class:`CitationGap` carrying
    the failure reason.

    Mirrors the Slice 17 5th sub-slice
    :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
    + Slice 17 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
    pattern: a typed bundle-driven projection class without state.

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the class is stateless and exposes a single method; per the
    auto-memory ``feedback_no_silent_degradation`` rule every Pydantic
    construction is wrapped in a typed gap projection.

    Example usage::

        from iriai_build_v2.execution_control.recommendation_citation_hook import (
            RecommendationCitationHookInputs,
            RecommendationCitationValidator,
        )

        validator = RecommendationCitationValidator()
        inputs = RecommendationCitationHookInputs(
            recommendation=recommendation,
            counterfactual_results=replay_results,
        )
        result = validator.validate(inputs)
        if not result.is_sufficient:
            # The recommendation is behavior-changing but lacks the
            # cross-slice Slice 18 counterfactual replay citation (or
            # the cited refs do NOT resolve); the typed gap projection
            # routes through RECOMMENDATION_CITATION_FAILURE_ID.
            ...
    """

    def __init__(self) -> None:
        """Construct a :class:`RecommendationCitationValidator`.

        The constructor takes no arguments per the
        ``feedback_no_overengineer_use_library`` rule: the validator
        is stateless. Future per-recommendation rule overrides (per
        the doc-18:117-119 step 7 *"per any behavior-changing
        recommendation"* extension point) live on the bound
        :meth:`validate` method via the typed
        :class:`RecommendationCitationHookInputs` dispatch.

        Mirrors the Slice 17 5th sub-slice
        :class:`~iriai_build_v2.execution_control.replay_requirement_hook.ReplayRequirementValidator`
        + Slice 17 3rd sub-slice
        :class:`~iriai_build_v2.execution_control.policy_validation_interface.PolicyValidationInterface`
        constructor verbatim.
        """

        # Stateless per the prompt's "Validator NEVER raises" +
        # "Validator GRANTS NO ACTIVATION AUTHORITY" + "No second
        # source of replay truth" boundaries.
        pass

    def validate(
        self,
        inputs: RecommendationCitationHookInputs,
    ) -> CitationSufficiencyResult:
        """Doc-18:117-119 step 7 + doc-18:165-166 AC4 -- validate a
        typed :class:`RecommendationCitationHookInputs` bundle against
        the AC4 binding.

        Dispatch logic (3 sufficient + 2 insufficient cases):

        * **Sufficient case 1: non-behavior-changing recommendation.**
          If the recommendation's typed
          :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.safe_runtime_action`
          is ``False``: pass trivially (per doc-17:198-200 a
          non-behavior-changing recommendation cannot be consumed by
          runtime policy at all; the AC4 binding does NOT apply) --
          return :class:`CitationSufficiencyResult` with
          ``is_sufficient=True``, ``gap=None``, empty
          ``cited_result_ids``, ``missing_reason=None``.

        * **Sufficient case 2: behavior-changing + cited refs resolve.**
          If ``safe_runtime_action=True`` AND
          :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
          is non-empty AND every ref-string resolves to a typed
          :attr:`CounterfactualResult.result_id` in
          :attr:`RecommendationCitationHookInputs.counterfactual_results`:
          PASS on the *"cite replay results"* axis of doc-18:165-166
          AC4 -- return :class:`CitationSufficiencyResult` with
          ``is_sufficient=True``, ``cited_result_ids`` populated with
          the resolved ref-string list, ``gap=None``,
          ``missing_reason=None``.

        * **Sufficient case 3: behavior-changing + explicit
          needs-more-evidence.** If ``safe_runtime_action=True`` AND
          ``counterfactual_result_refs`` is empty AND
          :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.status`
          is ``"needs_more_evidence"``: PASS on the *"or explicitly
          say more evidence is needed"* axis of doc-18:165-166 AC4 --
          return :class:`CitationSufficiencyResult` with
          ``is_sufficient=True``, ``gap=None``, empty
          ``cited_result_ids``, ``missing_reason=None``.

        * **Insufficient case 1: behavior-changing + missing
          citation + no needs-more-evidence signal.** If
          ``safe_runtime_action=True`` AND
          ``counterfactual_result_refs`` is empty AND status is NOT
          ``"needs_more_evidence"``: REJECT (per doc-18:117-119 + AC4
          BOTH axes fail) -- return
          :class:`CitationSufficiencyResult` with
          ``is_sufficient=False``, ``gap`` populated with a typed
          :class:`CitationGap`
          (``reason=RECOMMENDATION_CITATION_MISSING_REASON``),
          ``missing_reason`` set to the same reason.

        * **Insufficient case 2: behavior-changing + unresolved
          refs.** If ``safe_runtime_action=True`` AND
          ``counterfactual_result_refs`` is non-empty AND at least
          one ref-string does NOT resolve to a provided
          :attr:`CounterfactualResult.result_id`: REJECT (per
          doc-18:117-119 + AC4 the *"cite replay results"* axis
          fails; broken cross-reference) -- return
          :class:`CitationSufficiencyResult` with
          ``is_sufficient=False``, ``gap`` populated with a typed
          :class:`CitationGap`
          (``reason=RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON``,
          ``unresolved_refs`` populated with the FULL list of
          unresolved ref-strings per
          ``feedback_never_truncate_decisions``).

        Per **doc-17:159-163 + doc-17:225-226 + doc-18:123-125 +
        Cross-Slice Dependencies enumeration** the validator does NOT
        load / fetch / construct any :class:`CounterfactualResult`
        records; it consumes ONLY the typed list provided as input via
        :attr:`RecommendationCitationHookInputs.counterfactual_results`.
        Slice 18 1st/2nd/3rd/4th/5th/6th sub-slices own the typed
        shape + the fetch / load / construct / write surface; this
        validator's typed surface is the citation-sufficiency check
        ONLY.

        Per **doc-17:217** + **doc-17:170-171** + **doc-18:123-125**
        the validator GRANTS NO consumer-side activation authority;
        a passing result does NOT activate the recommendation's
        proposed policy artifact; activation is consumer-owned per
        doc-17:159-163.

        Per ``feedback_no_silent_degradation`` the method NEVER raises:
        a structural internal failure (e.g. an unexpected
        :class:`pydantic.ValidationError` during typed
        :class:`CitationSufficiencyResult` construction) emits a typed
        :class:`CitationSufficiencyResult` with
        ``is_sufficient=False`` and a typed :class:`CitationGap`
        carrying the failure reason. The validator preserves all
        unresolved refs per the
        ``feedback_never_truncate_decisions`` rule.

        Returns a typed :class:`CitationSufficiencyResult`.
        """

        try:
            recommendation = inputs.recommendation
            counterfactual_results = list(inputs.counterfactual_results)
            recommendation_id = recommendation.recommendation_id
            safe_runtime_action = recommendation.safe_runtime_action
            counterfactual_result_refs = list(
                recommendation.counterfactual_result_refs
            )
            status = recommendation.status

            # Sufficient case 1: non-behavior-changing recommendation.
            # Per doc-17:198-200 the AC4 binding does NOT apply; pass
            # trivially.
            if not safe_runtime_action:
                return CitationSufficiencyResult(
                    recommendation_id=recommendation_id,
                    is_sufficient=True,
                    cited_result_ids=[],
                    missing_reason=None,
                    gap=None,
                    validated_at=datetime.now(tz=timezone.utc),
                )

            # Pre-compute the set of typed CounterfactualResult.result_id
            # values for the cross-reference resolution check (per
            # doc-18:80 the typed result_id is the cross-reference
            # handle).
            provided_result_ids: set[str] = {
                result.result_id for result in counterfactual_results
            }

            # Behavior-changing path: check the AC4 binding on BOTH axes
            # per doc-18:165-166.
            if counterfactual_result_refs:
                # The recommendation provides citations; check every
                # ref-string resolves to a provided typed result_id.
                # Per feedback_never_truncate_decisions the validator
                # collects the FULL list of unresolved refs rather than
                # the first only.
                unresolved_refs: list[str] = [
                    ref
                    for ref in counterfactual_result_refs
                    if ref not in provided_result_ids
                ]

                if unresolved_refs:
                    # Insufficient case 2: at least one cited ref does
                    # NOT resolve to a provided typed result_id (broken
                    # cross-reference; the *"cite replay results"* axis
                    # of AC4 fails).
                    gap = CitationGap(
                        failure_id=RECOMMENDATION_CITATION_FAILURE_ID,
                        recommendation_id=recommendation_id,
                        reason=RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON,
                        unresolved_refs=unresolved_refs,
                    )
                    return CitationSufficiencyResult(
                        recommendation_id=recommendation_id,
                        is_sufficient=False,
                        cited_result_ids=[],
                        missing_reason=(
                            RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
                        ),
                        gap=gap,
                        validated_at=datetime.now(tz=timezone.utc),
                    )

                # Sufficient case 2: behavior-changing + every cited
                # ref-string resolves to a provided typed result_id.
                # PASS on the *"cite replay results"* axis of AC4.
                return CitationSufficiencyResult(
                    recommendation_id=recommendation_id,
                    is_sufficient=True,
                    cited_result_ids=list(counterfactual_result_refs),
                    missing_reason=None,
                    gap=None,
                    validated_at=datetime.now(tz=timezone.utc),
                )

            # The recommendation does NOT provide citations (empty
            # counterfactual_result_refs). Check the *"or explicitly
            # say more evidence is needed"* axis of AC4 (per
            # doc-18:165-166 + doc-17:339-341 + doc-17:301 the typed
            # status surface includes "needs_more_evidence" precisely
            # for this AC4 binding).
            if status == "needs_more_evidence":
                # Sufficient case 3: behavior-changing + explicit
                # needs-more-evidence. PASS on the *"or explicitly say
                # more evidence is needed"* axis of AC4.
                return CitationSufficiencyResult(
                    recommendation_id=recommendation_id,
                    is_sufficient=True,
                    cited_result_ids=[],
                    missing_reason=None,
                    gap=None,
                    validated_at=datetime.now(tz=timezone.utc),
                )

            # Insufficient case 1: behavior-changing + missing
            # citation + no needs-more-evidence signal. Per AC4 BOTH
            # axes fail; emit the typed gap with the
            # RECOMMENDATION_CITATION_MISSING_REASON.
            gap = CitationGap(
                failure_id=RECOMMENDATION_CITATION_FAILURE_ID,
                recommendation_id=recommendation_id,
                reason=RECOMMENDATION_CITATION_MISSING_REASON,
            )
            return CitationSufficiencyResult(
                recommendation_id=recommendation_id,
                is_sufficient=False,
                cited_result_ids=[],
                missing_reason=RECOMMENDATION_CITATION_MISSING_REASON,
                gap=gap,
                validated_at=datetime.now(tz=timezone.utc),
            )

        except Exception as exc:
            # Structural internal failure (e.g. an unexpected
            # ValidationError from a typed CitationSufficiencyResult
            # construction above, or the recommendation's typed fields
            # raise unexpectedly). Per feedback_no_silent_degradation
            # the validator NEVER raises: emit a typed
            # CitationSufficiencyResult with is_sufficient=False and a
            # typed CitationGap carrying the failure reason. Per
            # doc-14:242-243 this projects onto the NON-blocking typed
            # failure id RECOMMENDATION_CITATION_FAILURE_ID under
            # EXISTING evidence_corruption failure_class with REUSED
            # retry_governance_projection action (registered in the
            # Slice 18 7th sub-slice failure_router 4 pure-data add
            # points).
            try:
                recommendation_id = inputs.recommendation.recommendation_id
            except Exception:
                # If even reading the recommendation's own id raises,
                # fall back to a placeholder so the typed gap
                # projection's recommendation_id field still validates.
                recommendation_id = "unknown_recommendation_id"
            reason = (
                f"recommendation_citation_internal_exception:"
                f"{type(exc).__name__}"
            )
            gap = CitationGap(
                failure_id=RECOMMENDATION_CITATION_FAILURE_ID,
                recommendation_id=recommendation_id,
                reason=reason,
            )
            return CitationSufficiencyResult(
                recommendation_id=recommendation_id,
                is_sufficient=False,
                cited_result_ids=[],
                missing_reason=reason,
                gap=gap,
                validated_at=datetime.now(tz=timezone.utc),
            )


# --- Module-level convenience function --------------------------------------


def validate_recommendation_citation(
    inputs: RecommendationCitationHookInputs,
) -> CitationSufficiencyResult:
    """Doc-18:117-119 step 7 -- module-level convenience wrapper for
    :meth:`RecommendationCitationValidator.validate` against a
    default-constructed :class:`RecommendationCitationValidator`
    instance.

    Callers that need only the default citation-sufficiency check (no
    per-recommendation overrides) can use this module-level function
    without constructing the validator explicitly. Callers that need
    to extend the validator (e.g. future Slice 19 governance agent
    reporting surface that may add per-rule callbacks) should construct
    a :class:`RecommendationCitationValidator` directly.

    Mirrors the
    :func:`~iriai_build_v2.execution_control.replay_requirement_hook.validate_replay_requirement`
    module-level pattern (Slice 17 5th sub-slice) +
    :func:`~iriai_build_v2.execution_control.policy_validation_interface.validate_recommendation`
    module-level pattern (Slice 17 3rd sub-slice) +
    :func:`~iriai_build_v2.execution_control.recommendation_builder.compute_recommendation_id`
    pattern (Slice 17 2nd sub-slice) +
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    module-level pattern (Slice 17 1st sub-slice).
    """

    return RecommendationCitationValidator().validate(inputs)
