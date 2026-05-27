"""Slice 17 third sub-slice -- per-consumer policy validation interface.

This module owns the **doc-17 § Refactoring Steps step 3** verbatim:

    *"Add a policy validation interface per consumer. Validation
    proves the artifact can be understood, not that it should be
    activated."* (doc-17:170-171)

Per the Slice 17 1st sub-slice typed-shape foundation
(:mod:`iriai_build_v2.execution_control.policy_recommendation`) + the
Slice 17 2nd sub-slice recommendation builder
(:mod:`iriai_build_v2.execution_control.recommendation_builder`) this
validation interface consumes the typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
records the builder emits + their typed
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.proposed_policy_artifact`
union members (one of the 6 doc-17:107-145 consumer-specific
``*PolicyArtifact`` BaseModels) and validates per-consumer policy-shape
rules per doc-17:208-210.

**Per-consumer validation rules (doc-17:208-210).** The interface
dispatches by the recommendation's typed
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.consumer`
classification (one of the 6 doc-17:65
:data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
values) to one of 6 private per-consumer validation methods. Each
method enforces the doc-17:208-210 rule(s) for its consumer:

* **failure_router** (doc-17:209): *"Failure-router consumer validation
  rejects untested route changes."* The validator rejects artifacts
  whose
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.FailureRouterPolicyArtifact.required_tests`
  list is empty (untested route change) AND rejects artifacts whose
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.requires_tests`
  list at the recommendation level is empty (per doc-17:218 *"Behavior-
  changing recommendations require explicit policy artifacts and
  tests."*).
* **scheduler** (doc-17:208): *"Scheduler consumer validation rejects
  policies that violate dependency, write-set, barrier, or safety
  constraints."* The validator rejects artifacts whose
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact.guardrails`
  list is empty (no audit-trail surface for the dependency/write-set/
  barrier/safety constraints per doc-17:120 *"Per doc-17:208 ...
  Scheduler consumer validation rejects policies that violate
  dependency, write-set, barrier, or safety constraints. ... the
  guardrails are the typed audit-trail surface for the validation
  contract."*).
* **supervisor** (doc-17:210 + doc-17:155): *"Supervisor/dashboard
  consume summaries without mutation capability."* The validator
  rejects artifacts whose
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.SupervisorPolicyArtifact.read_only`
  field is not exactly ``True`` (defense-in-depth: the typed
  ``Literal[True] = True`` already enforces this at construction; the
  validator cross-checks at the validation layer per
  ``feedback_harden_all_code_paths``).
* **dashboard** (doc-17:210 + doc-17:155): same as supervisor
  read-only.
* **planning** (doc-17:156): *"Planning consumes historical
  recommendations as context for future DAG design."* The validator
  rejects artifacts whose
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.PlanningPolicyArtifact.advisory_only`
  field is not exactly ``True`` (defense-in-depth same as supervisor/
  dashboard).
* **merge_queue** (doc-17:144 + doc-17:157-158): *"Merge queue
  consumes only explicit consumer-owned `activated` policy artifacts
  covered by merge queue tests."* The validator rejects artifacts
  whose
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.MergeQueuePolicyArtifact.required_queue_tests`
  list is empty (no merge queue tests cover the artifact).

**Cross-cutting validation rules (per-recommendation, NOT per-
artifact).** Beyond the per-consumer artifact-shape rules above, the
validator additionally enforces:

* **Artifact-vs-consumer cross-check** (doc-17:88-95): the typed
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.proposed_policy_artifact`
  must be an instance of the consumer-specific BaseModel per the
  doc-17:147-158 consumer contracts (e.g. ``consumer="scheduler"``
  requires
  :class:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact`).
  This catches mis-emitted recommendations (e.g. a builder bug that
  produces a
  :class:`~iriai_build_v2.execution_control.policy_recommendation.FailureRouterPolicyArtifact`
  on a recommendation tagged ``consumer="scheduler"``).

**Boundary: NO consumer-side activation authority (doc-17:170-171).** Per
*"Validation proves the artifact can be understood, not that it should
be activated."* the validator is read-only with respect to consumer
state: it returns a typed
:class:`ValidationResult` record carrying ``is_valid: bool`` +
``violations: list[ValidationViolation]`` and does NOT mutate any
consumer state. Activation belongs to the consumer-owned policy record
per doc-17:159-163; this validator does NOT participate in that path.

**Boundary: NO consumer-module imports.** Per the implementer-prompt
boundary the validator does NOT import any consumer-side module
(supervisor / dashboard / scheduler / planning / merge_queue / Slice
07 failure_router beyond the 4 pure-data add points for the typed
failure id). Validation rules operate over the typed
``*PolicyArtifact`` BaseModel SURFACE only -- the per-consumer
validator functions take a typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
+ its typed artifact and emit typed
:class:`ValidationViolation` projections WITHOUT coupling to any
consumer internals.

**Fail-closed semantics (feedback_no_silent_degradation).** The
validator NEVER raises on input. On a structural internal failure
(e.g. an unmapped consumer value the typed
:data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
Literal range somehow lets through), the validator emits a typed
:class:`ValidationResult` with ``is_valid=False`` and a typed
:class:`ValidationViolation` carrying the failure reason; the
corresponding typed failure id
:data:`POLICY_VALIDATION_FAILURE_ID`
(``policy_validation_failed``) registers under the EXISTING
``evidence_corruption`` failure_class with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd sub-slice precedent verbatim).

**Implementation discipline.** Stdlib (``json``) + Pydantic v2 + Slice 13a
modules (``..workflows.develop.governance.models``) + Slice 17 1st
sub-slice (:mod:`iriai_build_v2.execution_control.policy_recommendation`)
only. NO imports from ``governance/`` outside ``governance.models``.
NO imports from other parts of ``execution_control/`` beyond
``policy_recommendation`` (this module is the typed validation layer
over the Slice 17 1st sub-slice typed surfaces; the 2nd sub-slice
``recommendation_builder`` is NOT imported because the validator
operates over the typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
SURFACE not the builder's internal projection -- the validator is a
downstream observer of the recommendation BaseModel, not of the
builder's pipeline). NO imports from ``workflows/develop/execution/phases/``
/ ``supervisor`` / ``dashboard`` (those would be consumer-side
activation surfaces, not validation dependencies; explicitly excluded
per the no-consumer-module-import boundary).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.policy_recommendation` (Slice
17 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.recommendation_builder` (Slice
17 2nd sub-slice) verbatim: ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``bool`` / ``list``).
Per the auto-memory ``feedback_no_silent_degradation`` rule every
Pydantic field validates at construction; unknown values fail closed
via Literal range + ``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 + Slice 15 + Slice 16 + Slice 17 1st + 2nd sub-slice
precedent verbatim without introducing new abstractions.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.policy_recommendation import (
    DashboardPolicyArtifact,
    FailureRouterPolicyArtifact,
    GovernancePolicyRecommendation,
    MergeQueuePolicyArtifact,
    PlanningPolicyArtifact,
    PolicyConsumer,
    SchedulerPolicyArtifact,
    SupervisorPolicyArtifact,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-17:170-171 + doc-14:242-243 NON-BLOCKING).
    "POLICY_VALIDATION_FAILURE_ID",
    # Typed BaseModels (mirrors Slice 17 2nd sub-slice
    # RecommendationBuilderInputs / RecommendationBuilderResult /
    # RecommendationBuilderEmissionGap precedent).
    "ValidationViolation",
    "ValidationResult",
    # The dispatch class (doc-17:170-171 step 3) and the module-level
    # convenience function that wraps the default dispatch.
    "PolicyValidationInterface",
    "validate_recommendation",
]


# --- Typed failure id (doc-17:170-171 + doc-14:242-243 NON-BLOCKING) --------


POLICY_VALIDATION_FAILURE_ID: Literal[
    "policy_validation_failed"
] = "policy_validation_failed"
"""Doc-17:170-171 + doc-14:242-243 -- the typed failure id the policy
validation interface projects onto when an internal validation step
fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 pattern matches the Slice 14 + Slice 15 + Slice 16 non-blocking
governance projection observer (the validation interface is also a
post-checkpoint governance projection observer).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
+ Slice 17 2nd sub-slice precedents are the source-of-truth for the
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

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to policy
validation failures (this slice is also a post-checkpoint governance
projection observer).
"""


# --- Typed ValidationViolation BaseModel ------------------------------------


class ValidationViolation(BaseModel):
    """Doc-17:170-171 + doc-17:208-210 -- typed per-rule violation
    projection.

    A :class:`ValidationViolation` is the typed audit record the
    per-consumer validators emit when a per-consumer policy-shape rule
    rejects an artifact. The shape carries:

    * ``consumer`` -- the typed consumer the violation applies to
      (one of the 6 :data:`PolicyConsumer` values per doc-17:65).
    * ``rule_name`` -- the typed rule-name string (e.g.
      ``"failure_router_required_tests_must_be_non_empty"`` for the
      doc-17:209 rule).
    * ``violation_message`` -- the typed human-readable violation
      message (e.g. ``"FailureRouterPolicyArtifact.required_tests
      list is empty; doc-17:209 rejects untested route changes."``).
    * ``evidence_refs`` -- optional list of Slice 13a shared
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      evidence references the validator cites for the violation.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-17:240-289).** :attr:`evidence_refs` is a list of Slice 13a
    shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here). Per doc-13a:285-287 step 9 the shared model is
    the authority for governance evidence-ref semantics.
    """

    # ``extra="forbid"`` aligns with the Slice 17 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/policy_recommendation.py:376
    # + the Slice 17 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/recommendation_builder.py
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    consumer: PolicyConsumer
    """Doc-17:65 -- the typed
    :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
    the violation applies to. Per Pydantic Literal validation the
    field accepts only one of the 6 values; unknown values fail closed
    with a typed ``ValidationError``."""

    rule_name: str
    """The typed rule-name string the violation references (e.g.
    ``"failure_router_required_tests_must_be_non_empty"`` for the
    doc-17:209 rule, ``"scheduler_guardrails_must_be_non_empty"`` for
    the doc-17:208 rule, ``"supervisor_read_only_must_be_true"`` for
    the doc-17:210 defense-in-depth check)."""

    violation_message: str
    """The typed human-readable violation message (e.g.
    ``"FailureRouterPolicyArtifact.required_tests list is empty;
    doc-17:209 rejects untested route changes."``). Per doc-17:170
    the validator's purpose is to prove the artifact CAN be understood;
    the message is the typed surface that lets the future Slice 17 4th
    sub-slice decision-record writer + Slice 19 governance agent
    reporting surface explain the rejection."""

    evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """Optional list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the validator cites for the violation.

    Defaults to empty list -- per doc-17:170-171 the validator is
    advisory; it does NOT mandate evidence refs on every violation
    (the violation is itself derived from the typed artifact surface
    which carries its own evidence chain via the Slice 17 1st
    sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_finding_ids`
    + :attr:`source_metric_refs` fields)."""


# --- Typed ValidationResult BaseModel ---------------------------------------


class ValidationResult(BaseModel):
    """Doc-17:170-171 -- typed per-recommendation validation outcome.

    A :class:`ValidationResult` is the typed audit record the
    :class:`PolicyValidationInterface.validate_recommendation` method
    emits per recommendation. The shape carries:

    * ``recommendation_id`` -- the typed reference back to the
      :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
      the validation applies to.
    * ``consumer`` -- the typed consumer the recommendation targets
      (echoed from the recommendation for the audit trail; redundant
      with the per-violation ``consumer`` field but at the result
      level for the common case of all-violations-share-consumer).
    * ``is_valid`` -- typed boolean for whether the recommendation
      passes all per-consumer rules.
    * ``violations`` -- the typed list of
      :class:`ValidationViolation` records for rules the recommendation
      violated. Empty when ``is_valid=True``.
    * ``validated_at`` -- optional typed timestamp; defaults to UTC
      now.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per doc-17:170-171 *"Validation proves the artifact can be
    understood, not that it should be activated."* -- a
    :class:`ValidationResult` with ``is_valid=True`` does NOT grant
    the artifact activation authority; activation is consumer-owned
    per doc-17:159-163. The result is read-only with respect to
    consumer state.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    """Doc-17:77 + doc-17:170-171 -- the typed reference back to the
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.recommendation_id`
    the validation applies to."""

    consumer: PolicyConsumer
    """Doc-17:65 -- the typed consumer the recommendation targets
    (echoed from the recommendation for the audit trail). Per
    Pydantic Literal validation the field accepts only one of the 6
    values; unknown values fail closed with a typed
    ``ValidationError``."""

    is_valid: bool
    """Doc-17:170-171 -- the typed boolean for whether the
    recommendation passes all per-consumer rules.

    Per doc-17:170-171 *"Validation proves the artifact can be
    understood, not that it should be activated."* a ``True`` value
    does NOT grant the artifact activation authority; activation is
    consumer-owned per doc-17:159-163. This is the typed surface that
    lets the future Slice 17 4th sub-slice decision-record writer +
    Slice 17 6th sub-slice consumer read APIs distinguish
    understood-but-not-activated artifacts from rejected artifacts."""

    violations: list[ValidationViolation] = Field(default_factory=list)
    """Doc-17:208-210 -- the typed list of
    :class:`ValidationViolation` records for rules the recommendation
    violated.

    Empty (``[]``) when ``is_valid=True``; populated with one or more
    typed violations when ``is_valid=False``. Per
    ``feedback_never_truncate_decisions`` the validator emits ALL
    failing rules per recommendation, not just the first; the consumer-
    side audit surface gets the complete list of reasons the
    recommendation was rejected."""

    validated_at: datetime | None = None
    """Optional typed validation timestamp. Defaults to ``None`` when
    not provided so the dispatch class can fill it at validation time
    per
    :meth:`PolicyValidationInterface.validate_recommendation`. Per
    the Pydantic v2 + Slice 13A + Slice 14 + Slice 15 + Slice 16 +
    Slice 17 1st/2nd sub-slice canonical-JSON discipline the datetime
    field projects to ISO-8601 string under
    :meth:`BaseModel.model_dump` with ``mode='json'``."""


# --- Per-consumer artifact-type mapping (doc-17:147-158 cross-check) --------


_CONSUMER_TO_ARTIFACT_TYPE: dict[PolicyConsumer, type[BaseModel]] = {
    "failure_router": FailureRouterPolicyArtifact,
    "scheduler": SchedulerPolicyArtifact,
    "supervisor": SupervisorPolicyArtifact,
    "dashboard": DashboardPolicyArtifact,
    "planning": PlanningPolicyArtifact,
    "merge_queue": MergeQueuePolicyArtifact,
}
"""Doc-17:147-158 + doc-17:107-145 -- the per-consumer expected typed
artifact BaseModel mapping.

Each :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
value maps to the typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.FailureRouterPolicyArtifact`
/
:class:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact`
/ etc. BaseModel its recommendations must carry. The
:meth:`PolicyValidationInterface._cross_check_artifact_type` step uses
this table to detect mis-emitted recommendations (e.g. a builder bug
that produces a
:class:`FailureRouterPolicyArtifact` on a recommendation tagged
``consumer="scheduler"``).

Total: **6 of 6** consumer values mapped per doc-17:65.
"""


# --- Per-consumer validation functions (doc-17:208-210) ---------------------
#
# Each function takes the typed
# :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
# and the typed consumer-specific artifact and emits a list of typed
# :class:`ValidationViolation` records for any per-consumer policy-shape
# rule the artifact violates. The functions ground their rules on the
# doc-17:208-210 per-consumer contracts WITHOUT importing any
# consumer-side module (per the no-consumer-module-import boundary).
#
# Per the prompt's "per-consumer routing correctness" requirement each
# function is private (underscore-prefixed); the public surface is
# :class:`PolicyValidationInterface.validate_recommendation`.


def _validate_failure_router_artifact(
    recommendation: GovernancePolicyRecommendation,
    artifact: FailureRouterPolicyArtifact,
) -> list[ValidationViolation]:
    """Doc-17:209 -- *"Failure-router consumer validation rejects
    untested route changes."*

    The validator rejects artifacts whose
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.FailureRouterPolicyArtifact.required_tests`
    list is empty (untested route change). Additionally rejects
    artifacts whose recommendation-level
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.requires_tests`
    list is empty (per doc-17:218 *"Behavior-changing recommendations
    require explicit policy artifacts and tests."*).

    Returns an empty list when no violation is found; returns a typed
    list of one or two :class:`ValidationViolation` records when one or
    both rules reject.
    """

    violations: list[ValidationViolation] = []
    if not artifact.required_tests:
        violations.append(
            ValidationViolation(
                consumer="failure_router",
                rule_name="failure_router_required_tests_must_be_non_empty",
                violation_message=(
                    "FailureRouterPolicyArtifact.required_tests list is "
                    "empty; doc-17:209 rejects untested route changes."
                ),
            )
        )
    if not recommendation.requires_tests:
        violations.append(
            ValidationViolation(
                consumer="failure_router",
                rule_name="failure_router_recommendation_requires_tests_must_be_non_empty",
                violation_message=(
                    "GovernancePolicyRecommendation.requires_tests list is "
                    "empty for a failure_router recommendation; doc-17:218 "
                    "requires explicit tests for behavior-changing "
                    "recommendations."
                ),
            )
        )
    return violations


def _validate_scheduler_artifact(
    recommendation: GovernancePolicyRecommendation,
    artifact: SchedulerPolicyArtifact,
) -> list[ValidationViolation]:
    """Doc-17:208 -- *"Scheduler consumer validation rejects policies
    that violate dependency, write-set, barrier, or safety constraints."*

    The validator rejects artifacts whose
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact.guardrails`
    list is empty (per doc-17:120 the guardrails are the typed audit-
    trail surface for the dependency/write-set/barrier/safety
    constraints; an empty guardrails list means the artifact has no
    declared safety constraints).

    Returns an empty list when no violation is found; returns a typed
    list of one :class:`ValidationViolation` record when the rule
    rejects.
    """

    violations: list[ValidationViolation] = []
    if not artifact.guardrails:
        violations.append(
            ValidationViolation(
                consumer="scheduler",
                rule_name="scheduler_guardrails_must_be_non_empty",
                violation_message=(
                    "SchedulerPolicyArtifact.guardrails list is empty; "
                    "doc-17:208 requires the typed audit-trail surface for "
                    "dependency/write-set/barrier/safety constraints; "
                    "doc-17:120 names the guardrails field as that surface."
                ),
            )
        )
    return violations


def _validate_supervisor_artifact(
    recommendation: GovernancePolicyRecommendation,
    artifact: SupervisorPolicyArtifact,
) -> list[ValidationViolation]:
    """Doc-17:210 + doc-17:155 -- *"Supervisor/dashboard consume
    summaries without mutation capability."*

    The validator rejects artifacts whose
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.SupervisorPolicyArtifact.read_only`
    field is not exactly ``True``. Per the typed
    ``Literal[True] = True`` at the Slice 17 1st sub-slice surface
    this rule is defense-in-depth (the typed shape already enforces
    this at construction; the validator cross-checks at the validation
    layer per ``feedback_harden_all_code_paths``).

    Returns an empty list when no violation is found; returns a typed
    list of one :class:`ValidationViolation` record when the rule
    rejects.
    """

    violations: list[ValidationViolation] = []
    if artifact.read_only is not True:
        violations.append(
            ValidationViolation(
                consumer="supervisor",
                rule_name="supervisor_read_only_must_be_true",
                violation_message=(
                    "SupervisorPolicyArtifact.read_only is not True; "
                    "doc-17:210 + doc-17:155 require supervisor "
                    "consumers to be read-only; the typed Literal[True] "
                    "at policy_recommendation.SupervisorPolicyArtifact "
                    "enforces this at construction (defense-in-depth)."
                ),
            )
        )
    return violations


def _validate_dashboard_artifact(
    recommendation: GovernancePolicyRecommendation,
    artifact: DashboardPolicyArtifact,
) -> list[ValidationViolation]:
    """Doc-17:210 + doc-17:155 -- *"Supervisor/dashboard consume
    summaries without mutation capability."*

    The validator rejects artifacts whose
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.DashboardPolicyArtifact.read_only`
    field is not exactly ``True``. Per the typed
    ``Literal[True] = True`` at the Slice 17 1st sub-slice surface
    this rule is defense-in-depth (the typed shape already enforces
    this at construction; the validator cross-checks at the validation
    layer per ``feedback_harden_all_code_paths``).

    Returns an empty list when no violation is found; returns a typed
    list of one :class:`ValidationViolation` record when the rule
    rejects.
    """

    violations: list[ValidationViolation] = []
    if artifact.read_only is not True:
        violations.append(
            ValidationViolation(
                consumer="dashboard",
                rule_name="dashboard_read_only_must_be_true",
                violation_message=(
                    "DashboardPolicyArtifact.read_only is not True; "
                    "doc-17:210 + doc-17:155 require dashboard "
                    "consumers to be read-only; the typed Literal[True] "
                    "at policy_recommendation.DashboardPolicyArtifact "
                    "enforces this at construction (defense-in-depth)."
                ),
            )
        )
    return violations


def _validate_planning_artifact(
    recommendation: GovernancePolicyRecommendation,
    artifact: PlanningPolicyArtifact,
) -> list[ValidationViolation]:
    """Doc-17:156 -- *"Planning consumes historical recommendations as
    context for future DAG design."*

    The validator rejects artifacts whose
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.PlanningPolicyArtifact.advisory_only`
    field is not exactly ``True``. Per the typed
    ``Literal[True] = True`` at the Slice 17 1st sub-slice surface
    this rule is defense-in-depth (the typed shape already enforces
    this at construction; the validator cross-checks at the validation
    layer per ``feedback_harden_all_code_paths``).

    Returns an empty list when no violation is found; returns a typed
    list of one :class:`ValidationViolation` record when the rule
    rejects.
    """

    violations: list[ValidationViolation] = []
    if artifact.advisory_only is not True:
        violations.append(
            ValidationViolation(
                consumer="planning",
                rule_name="planning_advisory_only_must_be_true",
                violation_message=(
                    "PlanningPolicyArtifact.advisory_only is not True; "
                    "doc-17:156 requires planning consumers to be "
                    "advisory-only; the typed Literal[True] at "
                    "policy_recommendation.PlanningPolicyArtifact "
                    "enforces this at construction (defense-in-depth)."
                ),
            )
        )
    return violations


def _validate_merge_queue_artifact(
    recommendation: GovernancePolicyRecommendation,
    artifact: MergeQueuePolicyArtifact,
) -> list[ValidationViolation]:
    """Doc-17:144 + doc-17:157-158 -- *"Merge queue consumes only
    explicit consumer-owned `activated` policy artifacts covered by
    merge queue tests."*

    The validator rejects artifacts whose
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.MergeQueuePolicyArtifact.required_queue_tests`
    list is empty (no merge queue tests cover the artifact). Per
    doc-17:144 the required_queue_tests field is the typed audit-trail
    surface for the doc-17:157-158 *"covered by merge queue tests"*
    contract.

    Returns an empty list when no violation is found; returns a typed
    list of one :class:`ValidationViolation` record when the rule
    rejects.
    """

    violations: list[ValidationViolation] = []
    if not artifact.required_queue_tests:
        violations.append(
            ValidationViolation(
                consumer="merge_queue",
                rule_name="merge_queue_required_queue_tests_must_be_non_empty",
                violation_message=(
                    "MergeQueuePolicyArtifact.required_queue_tests list "
                    "is empty; doc-17:144 + doc-17:157-158 require merge "
                    "queue tests to cover the artifact before it can be "
                    "activated by the Slice 08 + 12 consumer."
                ),
            )
        )
    return violations


# --- PolicyValidationInterface dispatch class -------------------------------


class PolicyValidationInterface:
    """Doc-17:170-171 step 3 -- per-consumer policy validation interface.

    A :class:`PolicyValidationInterface` instance exposes the
    :meth:`validate_recommendation` method that dispatches a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    to its consumer-specific validator and returns a typed
    :class:`ValidationResult`.

    Per doc-17:170-171 *"Add a policy validation interface per
    consumer. Validation proves the artifact can be understood, not
    that it should be activated."* the validator is read-only with
    respect to consumer state: it returns a typed result record and
    does NOT mutate any consumer state. Activation belongs to the
    consumer-owned policy record per doc-17:159-163; this validator
    does NOT participate in that path.

    **Boundary: NO consumer-side activation authority.** The validator
    does NOT call any consumer-side activation method; the
    :class:`ValidationResult` is informational only. Per doc-17:170-171
    *"Validation proves the artifact can be understood, not that it
    should be activated."*.

    **Boundary: NO consumer-module imports.** The validator does NOT
    import any consumer-side module (supervisor / dashboard / scheduler
    / planning / merge_queue). Validation rules operate over the typed
    ``*PolicyArtifact`` BaseModel SURFACE only.

    **Fail-closed semantics (feedback_no_silent_degradation).** The
    validator NEVER raises on input. On a structural internal failure
    (e.g. an unmapped consumer value the typed Literal range somehow
    lets through), the validator emits a typed
    :class:`ValidationResult` with ``is_valid=False`` and a typed
    :class:`ValidationViolation` carrying the failure reason.

    Mirrors the Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.GovernanceFindingWriter`
    + the Slice 17 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilder`
    pattern: a typed bundle-driven projection class without state.

    Per the auto-memory ``feedback_no_overengineer_use_library`` rule
    the class is stateless and exposes a single method; per the
    auto-memory ``feedback_no_silent_degradation`` rule every Pydantic
    construction is wrapped in a typed gap projection.
    """

    def __init__(self) -> None:
        """Construct a :class:`PolicyValidationInterface`.

        The constructor takes no arguments per the
        ``feedback_no_overengineer_use_library`` rule: the validator
        is stateless. Future per-consumer rule overrides (per the
        doc-17:170-171 *"per consumer"* extension point) live on the
        bound :meth:`validate_recommendation` method via the typed
        :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        consumer dispatch.
        """

        # Stateless per the prompt's "Validator NEVER raises" +
        # "Validator GRANTS NO ACTIVATION AUTHORITY" boundary.
        pass

    def validate_recommendation(
        self,
        recommendation: GovernancePolicyRecommendation,
    ) -> ValidationResult:
        """Doc-17:170-171 step 3 -- validate a typed
        :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        per its consumer's policy-shape rules per doc-17:208-210.

        Dispatch:

        * The recommendation's typed ``consumer`` field selects the
          per-consumer validation function from the 6 doc-17:107-145
          consumer-specific BaseModel rule set.
        * The validator first cross-checks that the recommendation's
          typed
          :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.proposed_policy_artifact`
          is an instance of the consumer-specific BaseModel per
          doc-17:147-158 (catches mis-emitted recommendations).
        * The validator then calls the per-consumer validation
          function with the typed recommendation + typed artifact and
          aggregates all returned :class:`ValidationViolation`
          records.
        * The result is a typed :class:`ValidationResult` with
          ``is_valid = (len(violations) == 0)``.

        Per doc-17:170-171 *"Validation proves the artifact can be
        understood, not that it should be activated."* the
        :class:`ValidationResult` does NOT grant activation
        authority; the consumer-owned activation surface is unaffected
        by this method.

        Per ``feedback_no_silent_degradation`` the method NEVER raises:
        a structural internal failure (e.g. an unmapped consumer
        value) emits a typed :class:`ValidationResult` with
        ``is_valid=False`` and a typed :class:`ValidationViolation`
        carrying the failure reason.

        Returns a typed :class:`ValidationResult`.
        """

        try:
            consumer = recommendation.consumer
            artifact = recommendation.proposed_policy_artifact

            # Cross-check: the typed artifact must be an instance of the
            # consumer-specific BaseModel per doc-17:147-158.
            expected_type = _CONSUMER_TO_ARTIFACT_TYPE.get(consumer)
            if expected_type is None:
                # Defense-in-depth: the typed PolicyConsumer Literal
                # range should already preclude this branch. If we
                # somehow reach it, emit a typed gap projection.
                return ValidationResult(
                    recommendation_id=recommendation.recommendation_id,
                    consumer=consumer,
                    is_valid=False,
                    violations=[
                        ValidationViolation(
                            consumer=consumer,
                            rule_name="policy_validation_internal_unmapped_consumer",
                            violation_message=(
                                f"PolicyConsumer value {consumer!r} has no "
                                "validator mapping in "
                                "_CONSUMER_TO_ARTIFACT_TYPE; this is a typed "
                                "gap projection per "
                                "POLICY_VALIDATION_FAILURE_ID."
                            ),
                        )
                    ],
                    validated_at=datetime.now(tz=timezone.utc),
                )

            if not isinstance(artifact, expected_type):
                # Mis-emitted recommendation: the consumer field
                # disagrees with the proposed_policy_artifact's typed
                # shape. Emit a typed cross-check violation.
                return ValidationResult(
                    recommendation_id=recommendation.recommendation_id,
                    consumer=consumer,
                    is_valid=False,
                    violations=[
                        ValidationViolation(
                            consumer=consumer,
                            rule_name="policy_validation_artifact_type_mismatch",
                            violation_message=(
                                f"GovernancePolicyRecommendation.consumer "
                                f"is {consumer!r} but "
                                f"proposed_policy_artifact is "
                                f"{type(artifact).__name__}; doc-17:147-158 "
                                f"requires the artifact type to match the "
                                f"consumer (expected "
                                f"{expected_type.__name__})."
                            ),
                        )
                    ],
                    validated_at=datetime.now(tz=timezone.utc),
                )

            # Dispatch to the per-consumer validator. The dispatch is
            # closed over the 6 PolicyConsumer Literal values; the
            # per-consumer validators are pure functions over the typed
            # (recommendation, artifact) tuple.
            violations: list[ValidationViolation] = []
            if consumer == "failure_router":
                assert isinstance(artifact, FailureRouterPolicyArtifact)
                violations = _validate_failure_router_artifact(
                    recommendation, artifact
                )
            elif consumer == "scheduler":
                assert isinstance(artifact, SchedulerPolicyArtifact)
                violations = _validate_scheduler_artifact(
                    recommendation, artifact
                )
            elif consumer == "supervisor":
                assert isinstance(artifact, SupervisorPolicyArtifact)
                violations = _validate_supervisor_artifact(
                    recommendation, artifact
                )
            elif consumer == "dashboard":
                assert isinstance(artifact, DashboardPolicyArtifact)
                violations = _validate_dashboard_artifact(
                    recommendation, artifact
                )
            elif consumer == "planning":
                assert isinstance(artifact, PlanningPolicyArtifact)
                violations = _validate_planning_artifact(
                    recommendation, artifact
                )
            elif consumer == "merge_queue":
                assert isinstance(artifact, MergeQueuePolicyArtifact)
                violations = _validate_merge_queue_artifact(
                    recommendation, artifact
                )

            return ValidationResult(
                recommendation_id=recommendation.recommendation_id,
                consumer=consumer,
                is_valid=(len(violations) == 0),
                violations=violations,
                validated_at=datetime.now(tz=timezone.utc),
            )

        except Exception as exc:
            # Structural internal failure (e.g. an unexpected
            # ValidationError from a typed ValidationResult construction
            # in the per-consumer dispatch above). Per
            # feedback_no_silent_degradation the validator NEVER
            # raises: emit a typed ValidationResult with is_valid=False
            # and a typed ValidationViolation carrying the failure
            # reason. Per doc-14:242-243 this projects onto the
            # NON-blocking typed failure id
            # POLICY_VALIDATION_FAILURE_ID under EXISTING
            # evidence_corruption failure_class with REUSED
            # retry_governance_projection action (registered in the
            # Slice 17 3rd sub-slice failure_router 4 pure-data add
            # points).
            try:
                recommendation_id = recommendation.recommendation_id
                consumer_for_gap: PolicyConsumer = recommendation.consumer
            except Exception:
                # If even reading the recommendation's own fields
                # raises, fall back to a placeholder. The typed
                # PolicyConsumer Literal does not have a "_unknown"
                # value so we pick a stable choice for the typed gap
                # projection -- "failure_router" is the most
                # conservative consumer (rejecting an untested route
                # change is the safest disposition).
                recommendation_id = "unknown_recommendation_id"
                consumer_for_gap = "failure_router"
            return ValidationResult(
                recommendation_id=recommendation_id,
                consumer=consumer_for_gap,
                is_valid=False,
                violations=[
                    ValidationViolation(
                        consumer=consumer_for_gap,
                        rule_name="policy_validation_internal_exception",
                        violation_message=(
                            f"PolicyValidationInterface.validate_recommendation "
                            f"raised internally: {type(exc).__name__}: {exc!s}; "
                            "this is a typed gap projection per "
                            "POLICY_VALIDATION_FAILURE_ID."
                        ),
                    )
                ],
                validated_at=datetime.now(tz=timezone.utc),
            )


# --- Module-level convenience function --------------------------------------


def validate_recommendation(
    recommendation: GovernancePolicyRecommendation,
) -> ValidationResult:
    """Doc-17:170-171 step 3 -- module-level convenience wrapper for
    :meth:`PolicyValidationInterface.validate_recommendation` against
    a default-constructed :class:`PolicyValidationInterface` instance.

    Callers that need only the per-consumer rule set (the default)
    can use this module-level function without constructing the
    interface explicitly. Callers that need to extend the interface
    (e.g. future Slice 19 governance agent reporting that may add
    per-rule callbacks) should construct a
    :class:`PolicyValidationInterface` directly.

    Mirrors the
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    module-level pattern (Slice 17 1st sub-slice) +
    :func:`~iriai_build_v2.execution_control.recommendation_builder.compute_recommendation_id`
    pattern (Slice 17 2nd sub-slice).
    """

    return PolicyValidationInterface().validate_recommendation(recommendation)
