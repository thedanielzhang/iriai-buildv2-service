"""Slice 17 first sub-slice -- foundational governance policy recommendation interface typed-shape module.

This module owns the doc-17 "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/17-policy-recommendation-interface.md:62-145``):

* :data:`PolicyConsumer` -- 6-value Literal alias (doc-17:65): the
  consumer taxonomy a governance policy recommendation targets. Values:
  ``scheduler`` / ``failure_router`` / ``supervisor`` / ``dashboard`` /
  ``planning`` / ``merge_queue``.
* :data:`PolicyRecommendationStatus` -- 6-value Literal alias
  (doc-17:66-73): the lifecycle-status ladder a governance policy
  recommendation declares. Values: ``draft`` / ``reviewed`` / ``accepted``
  / ``rejected`` / ``needs_more_evidence`` / ``superseded``. Per
  doc-17:159-163 ``activated`` is deliberately NOT a value of this
  Literal -- activation belongs to a separate consumer-owned policy
  record with its own schema, tests, replay proof, rollback plan, and
  audit trail.
* :class:`GovernancePolicyRecommendation` -- the 14-field recommendation
  record shape (doc-17:75-97): idempotency_key + recommendation_id +
  consumer + status + source_finding_ids + source_metric_refs +
  counterfactual_result_refs + confidence + expected_impact + risk_level
  + safe_runtime_action + requires_tests + proposed_policy_artifact +
  activation_requirements + rollback_requirements.
* :class:`PolicyRecommendationDecision` -- the 5-field decision record
  shape (doc-17:99-105): recommendation_id + decision + decided_by +
  decided_at + rationale + evidence_refs.
* :class:`FailureRouterPolicyArtifact` -- the 7-field failure-router
  policy artifact shape (doc-17:107-114): failure_class + failure_type +
  action + route_budget_key + max_attempts + idempotency_key_template +
  required_tests.
* :class:`SchedulerPolicyArtifact` -- the 4-field scheduler policy
  artifact shape (doc-17:116-120): policy_kind + scope + value +
  guardrails.
* :class:`SupervisorPolicyArtifact` -- the 4-field supervisor policy
  artifact shape (doc-17:122-126): policy_kind + scope + value +
  read_only (typed ``Literal[True] = True``).
* :class:`DashboardPolicyArtifact` -- the 4-field dashboard policy
  artifact shape (doc-17:128-132): policy_kind + scope + value +
  read_only (typed ``Literal[True] = True``).
* :class:`PlanningPolicyArtifact` -- the 4-field planning policy
  artifact shape (doc-17:134-138): policy_kind + scope + value +
  advisory_only (typed ``Literal[True] = True``).
* :class:`MergeQueuePolicyArtifact` -- the 4-field merge-queue policy
  artifact shape (doc-17:140-144): policy_kind + scope + value +
  required_queue_tests.

Plus the canonical-JSON helpers :func:`compute_policy_recommendation_idempotency_key`
+ :func:`canonical_policy_recommendation_dict` mirroring the Slice 13A
``compute_completeness_digest`` + Slice 14 ``compute_payload_sha256`` +
Slice 15 ``compute_scorecard_digest`` + Slice 16 1st sub-slice
``compute_finding_idempotency_key`` + ``canonical_finding_dict``
canonical-JSON + SHA-256 discipline verbatim.

It is the **cross-cutting typed foundation** that subsequent Slice 17
sub-slices (the recommendation builders + per-consumer validation
interface + decision-record writer + replay requirement hooks + consumer
read APIs per doc-17 § Refactoring Steps steps 2-7 at doc-17:168-179)
build on; this first sub-slice does NOT yet wire these typed shapes
into any executor / checkpoint / merge-queue / governance-projection /
supervisor-classifier / scheduler consumer -- that wiring lands in
subsequent sub-slices.

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only -- they do NOT mutate executor /
control-plane / product state, take merge or checkpoint authority, or
force policy activation. Governance recommendations are derived rows
(per doc-17:174 *"Findings are derived governance records and never
write execution `dag-*` authority artifacts."* + doc-17:182-188
*"Store recommendations as typed governance rows and project review
artifacts such as review:governance-recommendations:{corpus_id}. Do
not write `dag-regroup-active:*`, route-budget state, supervisor
actions, or merge queue state from governance recommendation
generation. If a consumer later activates a policy, it writes its own
activation artifact and references the recommendation id."*) and
never change execution state.

**Consumer-contract bindings per doc-17:147-163.** Per doc-17:159-163
*"`activated` is deliberately not a `GovernancePolicyRecommendation.status`.
Activation belongs to a separate consumer-owned policy record with its
own schema, tests, replay proof, rollback plan, and audit trail.
Governance recommendations can propose or be accepted for review, but
cannot become runtime policy by changing their own row status."* --
this 1st sub-slice exposes the typed surface that enforces this
binding at construction (the :data:`PolicyRecommendationStatus`
Literal does NOT include ``activated`` as a value); the
runtime-policy-activation surface is consumer-owned and lives in the
Slice 07 (failure router) / Slice 09 (scheduler) / Slice 10
(supervisor/dashboard) / Slice 12 (atomic landing / adoption) consumer
modules.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-17:240-289 Slice 13A Shared Completeness Model Dependency).** The
:attr:`PolicyRecommendationDecision.evidence_refs` field is a list of
Slice 13a :class:`GovernanceEvidenceRef` (imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`). The
:class:`GovernanceEvidenceRef` type is NOT redefined here -- per
doc-13a:285-287 step 9 (*"Update governance Slices 13-20 and context
Slice 21 to depend on this shared completeness model instead of
redefining authority semantics locally"*) this module consumes the
shared model directly.

Per doc-17:240-289 the future Slice 17 sub-slices that emit
recommendations consume the Slice 13A shared :data:`CompletenessState`
+ :class:`EvidenceCompleteness` typed shapes from
:mod:`iriai_build_v2.execution_control.completeness`; the
consumer-specific validation interface consumes
:class:`AuthoritativePromptContextRouting` from
:mod:`iriai_build_v2.execution_control.dispatcher_prompt_context` +
:class:`AuthoritativeGateProofRow` from
:mod:`iriai_build_v2.execution_control.gate_companion` +
:class:`AuthoritativeSnapshotClassifierRouting` from
:mod:`iriai_build_v2.execution_control.snapshot_companion`. This first
sub-slice does NOT yet pre-empt that wiring (the typed-shape
foundation exposes the surface that future sub-slices consume); the
REUSE discipline is enforced at the test-file level by asserting no
local ``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` redefinition.

Per doc-17:283-289 the **P3-13A-6-3 dead-until-wired binding
statement** is CLOSED (per the Slice 13A 8th sub-slice 13An-2
finalizer landing at ``dashboard.py:1568``); policy recommendations
that would activate runtime behavior may now treat the Slice 13A
typed completeness shapes as execution authority; this first
sub-slice exposes the typed surface but the wiring + validation rules
land in subsequent sub-slices per doc-17:165-179.

**By-name reference contracts.** Per doc-17:80-81 the
:attr:`GovernancePolicyRecommendation.source_finding_ids` +
:attr:`GovernancePolicyRecommendation.source_metric_refs` fields are
``list[str]`` (just strings; NOT typed BaseModels):

* :attr:`source_finding_ids: list[str]` carries
  :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
  string references back to Slice 16 1st sub-slice findings (per
  doc-16:83 ``idempotency_key: str`` at
  ``finding_engine.py:443``).
* :attr:`source_metric_refs: list[str]` carries
  :attr:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
  ref strings back to Slice 15 1st sub-slice metric values (per
  doc-15:78-88 the metric value carries definition_name + scope
  fields, and the ref string is the canonical name reference per
  the Slice 16 1st sub-slice ``metric_refs: list[str]`` pattern at
  ``finding_engine.py:574``).

The by-name reference shape is the documented doc-17:80-81 contract;
this 1st sub-slice does NOT import the typed Slice 15
:class:`GovernanceMetricValue` + Slice 16
:class:`GovernanceFinding` BaseModels (the ``list[str]`` field type
is sufficient per doc-17:80-81; the by-name reference discipline
mirrors the Slice 16 1st sub-slice
:attr:`GovernanceFinding.metric_refs: list[str]` pattern at
``finding_engine.py:574``).

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) only. NO imports from
``governance/`` outside ``governance.models`` (this module is
foundational; the governance layer consumes execution-control surfaces,
not the reverse). NO imports from other parts of ``execution_control/``
(this module is foundational for the future Slice 17 builders; the
existing Slice 00-16 ``execution_control`` modules are NOT modified).
NO imports from ``workflows/develop/execution/phases/`` / ``supervisor``
/ ``dashboard`` (those would be downstream consumers, not
dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.finding_engine` (Slice 16 1st
sub-slice) + :mod:`iriai_build_v2.execution_control.governance_metrics`
(Slice 15 1st sub-slice) + :mod:`iriai_build_v2.execution_control.commit_provenance`
(Slice 14 1st sub-slice) + :mod:`iriai_build_v2.execution_control.completeness`
(Slice 13A 2nd sub-slice): ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``float`` / ``bool`` /
``list`` / ``dict``). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 + Slice 15 + Slice 16 1st sub-slice precedent verbatim
without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Doc-17:65 -- the 6-value consumer Literal.
    "PolicyConsumer",
    # Doc-17:66-73 -- the 6-value status Literal (activated is
    # deliberately NOT a value per doc-17:159-163).
    "PolicyRecommendationStatus",
    # Doc-17:75-97 -- the 14-field GovernancePolicyRecommendation
    # BaseModel.
    "GovernancePolicyRecommendation",
    # Doc-17:99-105 -- the 5-field PolicyRecommendationDecision
    # BaseModel.
    "PolicyRecommendationDecision",
    # Doc-17:107-114 -- the 7-field FailureRouterPolicyArtifact
    # BaseModel (Slice 07 consumer).
    "FailureRouterPolicyArtifact",
    # Doc-17:116-120 -- the 4-field SchedulerPolicyArtifact BaseModel
    # (Slice 09 consumer).
    "SchedulerPolicyArtifact",
    # Doc-17:122-126 -- the 4-field SupervisorPolicyArtifact BaseModel
    # (Slice 10 consumer; read-only).
    "SupervisorPolicyArtifact",
    # Doc-17:128-132 -- the 4-field DashboardPolicyArtifact BaseModel
    # (Slice 10 consumer; read-only).
    "DashboardPolicyArtifact",
    # Doc-17:134-138 -- the 4-field PlanningPolicyArtifact BaseModel
    # (future planning consumer; advisory-only).
    "PlanningPolicyArtifact",
    # Doc-17:140-144 -- the 4-field MergeQueuePolicyArtifact BaseModel
    # (Slice 08 + 12 consumer).
    "MergeQueuePolicyArtifact",
    # Helpers mirroring Slice 13A's compute_completeness_digest +
    # Slice 14's compute_payload_sha256 + Slice 15's
    # compute_scorecard_digest + Slice 16 1st sub-slice's
    # compute_finding_idempotency_key + canonical_finding_dict
    # canonical-JSON discipline.
    "compute_policy_recommendation_idempotency_key",
    "canonical_policy_recommendation_dict",
]


# --- PolicyConsumer 6-value Literal (doc-17:65) -----------------------------


PolicyConsumer = Literal[
    "scheduler",
    "failure_router",
    "supervisor",
    "dashboard",
    "planning",
    "merge_queue",
]
"""Doc-17:65 -- the 6-value consumer taxonomy for governance policy
recommendations.

Each recommendation declares a ``consumer`` from this 6-value set so
the (future) Slice 17 recommendation builder + consumer read APIs can
route + filter by consumer-specific validation rules. Per doc-17:147-158
each consumer owns its activation surface; governance recommendations
are advisory inputs only.

The 6 values land verbatim from doc-17:65:

* ``scheduler`` -- Slice 09 regroup overlay + scheduler feedback
  consumer. Per doc-17:148-150 scheduler runtime behavior consumes
  only consumer-owned ``activated`` policy records; ``accepted``
  governance recommendations are reviewed/staged evidence and may
  not change scheduling by themselves.
* ``failure_router`` -- Slice 07 typed failure router consumer. Per
  doc-17:151-154 failure-router runtime behavior consumes only
  consumer-owned ``activated`` route-budget or route-priority policy
  records with replay coverage; ``accepted`` governance
  recommendations are review inputs only.
* ``supervisor`` -- Slice 10 supervisor consumer (read-only). Per
  doc-17:155 supervisor and dashboard consume advisory summaries and
  must remain read-only.
* ``dashboard`` -- Slice 10 dashboard consumer (read-only). Per
  doc-17:155 same as supervisor.
* ``planning`` -- future planning consumer (advisory-only). Per
  doc-17:156 planning consumes historical recommendations as context
  for future DAG design.
* ``merge_queue`` -- Slice 08 + Slice 12 merge queue consumer. Per
  doc-17:157-158 merge queue consumes only explicit consumer-owned
  ``activated`` policy artifacts covered by merge queue tests.

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- PolicyRecommendationStatus 6-value Literal (doc-17:66-73) --------------


PolicyRecommendationStatus = Literal[
    "draft",
    "reviewed",
    "accepted",
    "rejected",
    "needs_more_evidence",
    "superseded",
]
"""Doc-17:66-73 -- the 6-value lifecycle-status ladder for governance
policy recommendations.

Each recommendation declares a ``status`` from this 6-value set; the
status is the typed surface that lets the (future) Slice 17
recommendation lifecycle workflow distinguish in-flight drafts from
accepted-for-review records from rejected/superseded historical
records.

Per **doc-17:159-163** the typed surface deliberately DOES NOT include
``activated`` as a value:

    *"`activated` is deliberately not a
    `GovernancePolicyRecommendation.status`. Activation belongs to a
    separate consumer-owned policy record with its own schema, tests,
    replay proof, rollback plan, and audit trail. Governance
    recommendations can propose or be accepted for review, but cannot
    become runtime policy by changing their own row status."*

The activation surface is owned by the Slice 07 (failure router) /
Slice 09 (scheduler) / Slice 10 (supervisor/dashboard) / Slice 12
(atomic landing / adoption) consumer modules; a separate
consumer-owned policy artifact records the activation with its own
audit trail and rollback plan.

The 6 values land verbatim from doc-17:66-73:

* ``draft`` -- newly emitted recommendation; not yet reviewed.
* ``reviewed`` -- the recommendation has been reviewed by an
  authorised governance reviewer; may still be pending acceptance.
* ``accepted`` -- the recommendation is accepted for review but is
  NOT yet activated (per doc-17:159-163 activation is consumer-owned).
* ``rejected`` -- the recommendation is rejected; the typed audit
  record carries the rejection rationale via
  :class:`PolicyRecommendationDecision`.
* ``needs_more_evidence`` -- the recommendation cannot be acted on
  until more evidence is gathered (per doc-17:190-193 the
  low-confidence finding case).
* ``superseded`` -- the recommendation was superseded by a later
  recommendation (per doc-17:225-226 rollback / supersede path).

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- 6 consumer-specific policy artifact BaseModels (doc-17:107-145) --------


class FailureRouterPolicyArtifact(BaseModel):
    """Doc-17:107-114 -- the failure-router policy artifact shape (Slice
    07 consumer).

    A failure-router policy artifact represents a proposed change to
    the Slice 07 typed failure router's route table (per
    ``src/iriai_build_v2/workflows/develop/execution/failure_router.py``):
    a failure_class + failure_type pair with an action + route budget
    + retry attempts + idempotency key template + required tests.

    Per doc-17:151-154 *"Failure-router runtime behavior consumes only
    consumer-owned `activated` route-budget or route-priority policy
    records with replay coverage. `accepted` governance
    recommendations are review inputs only."* -- the artifact's
    activation surface is owned by Slice 07; this artifact records the
    proposed shape but does NOT activate it.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    failure_class: str
    """Doc-17:108 -- the failure class string (per the Slice 07 typed
    failure router's failure_class taxonomy; e.g. ``"runtime_failure"``
    / ``"evidence_corruption"``)."""

    failure_type: str
    """Doc-17:109 -- the failure type / id string within the class (per
    the Slice 07 typed failure router's typed failure ids; e.g.
    ``"runtime_provider_outage"``)."""

    action: Literal[
        "retry",
        "repair",
        "queue_recovery",
        "quiesce",
        "operator_required",
    ]
    """Doc-17:110 -- the typed action the failure router would take
    for the (failure_class, failure_type) pair if this policy artifact
    were activated. The 5 values land verbatim from doc-17:110."""

    route_budget_key: str
    """Doc-17:111 -- the route budget key string the failure router
    would scope retry budgets against (per Slice 07 typed budget keys;
    e.g. ``"runtime_failure/runtime_provider_outage"``)."""

    max_attempts: int
    """Doc-17:112 -- the maximum number of retry attempts before the
    failure router escalates per the action's terminal disposition."""

    idempotency_key_template: str
    """Doc-17:113 -- the idempotency-key template string the failure
    router would use to dedupe retries (e.g.
    ``"runtime_failure/{task_id}/{attempt_window}"``)."""

    required_tests: list[str]
    """Doc-17:114 -- the list of test names that MUST pass before this
    policy artifact can be activated by the Slice 07 consumer (per the
    doc-17:152 *"covered by merge queue tests"* + the doc-17:209
    *"Failure-router consumer validation rejects untested route
    changes"* discipline)."""


class SchedulerPolicyArtifact(BaseModel):
    """Doc-17:116-120 -- the scheduler policy artifact shape (Slice 09
    consumer).

    A scheduler policy artifact represents a proposed change to the
    Slice 09 scheduler feedback projection (per
    ``docs/execution-control-plane/09-regroup-overlay-and-scheduler-feedback.md``):
    a policy_kind + scope + value + guardrails 4-tuple.

    Per doc-17:148-150 *"Scheduler runtime behavior consumes only
    consumer-owned `activated` policy records. `accepted` governance
    recommendations are reviewed/staged evidence and may not change
    scheduling by themselves."* -- the artifact's activation surface
    is owned by Slice 09; this artifact records the proposed shape but
    does NOT activate it.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    policy_kind: Literal["wave_cap", "barrier", "lane_priority"]
    """Doc-17:117 -- the 3-value policy-kind Literal verbatim from
    doc-17:117."""

    scope: dict[str, str]
    """Doc-17:118 -- the scope-dimensions dict (e.g.
    ``{"lane_id": "ml-7"}`` / ``{"feature_id": "8ac124d6"}``). Per
    doc-15:81 the metric-value scope dict pattern; the scheduler
    artifact uses the same free-form key/value strings so the
    scheduler-policy surface can carry rich scope dimensions without a
    frozen schema."""

    value: dict[str, Any]
    """Doc-17:119 -- the typed policy-value dict (e.g.
    ``{"wave_cap": 7}`` / ``{"barrier_predicate": "all_high_risk_done"}``
    / ``{"priority": 100}``). Free-form ``dict[str, Any]`` so the
    consumer-side validation interface (per doc-17:171 step 3) can
    enforce per-policy-kind value shape rather than the typed-shape
    layer pre-emptively narrowing it."""

    guardrails: list[str]
    """Doc-17:120 -- the list of guardrail strings the scheduler
    consumer MUST honour when activating this policy (e.g.
    ``["max_concurrent_tasks_per_lane"]`` /
    ``["no_priority_inversion"]``). Per doc-17:208 *"Scheduler
    consumer validation rejects policies that violate dependency,
    write-set, barrier, or safety constraints."* the guardrails are
    the typed audit-trail surface for the validation contract."""


class SupervisorPolicyArtifact(BaseModel):
    """Doc-17:122-126 -- the supervisor policy artifact shape (Slice 10
    consumer; read-only).

    A supervisor policy artifact represents a proposed change to the
    Slice 10 supervisor classification / dedupe / digest-priority
    surface (per ``docs/execution-control-plane/10-supervisor-dashboard-integration.md``).
    Per doc-17:155 *"Supervisor and dashboard consume advisory
    summaries and must remain read-only."* the artifact's
    ``read_only`` field is typed as ``Literal[True] = True`` so the
    typed surface enforces the read-only invariant at construction --
    no consumer can construct a supervisor artifact with
    ``read_only=False``.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    policy_kind: Literal[
        "classification_hint", "dedupe", "digest_priority"
    ]
    """Doc-17:123 -- the 3-value policy-kind Literal verbatim from
    doc-17:123."""

    scope: dict[str, str]
    """Doc-17:124 -- the scope-dimensions dict (same free-form pattern
    as the scheduler artifact)."""

    value: dict[str, Any]
    """Doc-17:125 -- the typed policy-value dict. Free-form ``dict[str,
    Any]`` so per-policy-kind value shape lives in the consumer-side
    validation interface."""

    read_only: Literal[True] = True
    """Doc-17:126 -- the typed read-only invariant. Per doc-17:155
    *"Supervisor and dashboard consume advisory summaries and must
    remain read-only."* the field is typed as ``Literal[True] = True``
    so the typed surface enforces the read-only invariant at
    construction (a typo-d ``read_only=False`` raises a Pydantic
    ``ValidationError`` per the Literal range + ``extra="forbid"``
    discipline)."""


class DashboardPolicyArtifact(BaseModel):
    """Doc-17:128-132 -- the dashboard policy artifact shape (Slice 10
    consumer; read-only).

    A dashboard policy artifact represents a proposed change to the
    Slice 10 dashboard surface (per
    ``docs/execution-control-plane/10-supervisor-dashboard-integration.md``).
    Per doc-17:155 *"Supervisor and dashboard consume advisory
    summaries and must remain read-only."* the artifact's
    ``read_only`` field is typed as ``Literal[True] = True`` (same
    pattern as :class:`SupervisorPolicyArtifact`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    policy_kind: Literal[
        "view_priority", "alert_threshold", "panel_visibility"
    ]
    """Doc-17:129 -- the 3-value policy-kind Literal verbatim from
    doc-17:129."""

    scope: dict[str, str]
    """Doc-17:130 -- the scope-dimensions dict (same free-form pattern
    as the supervisor artifact)."""

    value: dict[str, Any]
    """Doc-17:131 -- the typed policy-value dict. Free-form ``dict[str,
    Any]`` so per-policy-kind value shape lives in the consumer-side
    validation interface."""

    read_only: Literal[True] = True
    """Doc-17:132 -- the typed read-only invariant (same as
    :attr:`SupervisorPolicyArtifact.read_only`)."""


class PlanningPolicyArtifact(BaseModel):
    """Doc-17:134-138 -- the planning policy artifact shape (future
    planning consumer; advisory-only).

    A planning policy artifact represents a proposed change to the
    future planning consumer surface (per the
    governance-prompt-roadmapped per-feature planning module). Per
    doc-17:156 *"Planning consumes historical recommendations as
    context for future DAG design."* the artifact's ``advisory_only``
    field is typed as ``Literal[True] = True`` so the typed surface
    enforces the advisory-only invariant at construction -- planning
    artifacts can shape future DAG design context but cannot become
    runtime policy by themselves.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    policy_kind: Literal[
        "future_dag_hint", "contract_template_hint"
    ]
    """Doc-17:135 -- the 2-value policy-kind Literal verbatim from
    doc-17:135."""

    scope: dict[str, str]
    """Doc-17:136 -- the scope-dimensions dict (same free-form pattern
    as the supervisor artifact)."""

    value: dict[str, Any]
    """Doc-17:137 -- the typed policy-value dict. Free-form ``dict[str,
    Any]`` so per-policy-kind value shape lives in the consumer-side
    validation interface."""

    advisory_only: Literal[True] = True
    """Doc-17:138 -- the typed advisory-only invariant. Per doc-17:156
    the field is typed as ``Literal[True] = True`` so the typed surface
    enforces the advisory-only invariant at construction (a typo-d
    ``advisory_only=False`` raises a Pydantic ``ValidationError`` per
    the Literal range + ``extra="forbid"`` discipline)."""


class MergeQueuePolicyArtifact(BaseModel):
    """Doc-17:140-144 -- the merge-queue policy artifact shape (Slice 08
    + Slice 12 consumer).

    A merge-queue policy artifact represents a proposed change to the
    Slice 08 durable merge queue + Slice 12 atomic landing surface (per
    ``docs/execution-control-plane/08-durable-merge-queue.md`` +
    ``docs/execution-control-plane/12-rollout-and-acceptance-matrix.md``).
    Per doc-17:157-158 *"Merge queue consumes only explicit
    consumer-owned `activated` policy artifacts covered by merge queue
    tests."* the artifact's activation surface is owned by Slice 08 +
    Slice 12; this artifact records the proposed shape with its
    required-queue-tests audit trail but does NOT activate it.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    policy_kind: Literal[
        "lane_priority", "recovery_budget", "commit_gate_hint"
    ]
    """Doc-17:141 -- the 3-value policy-kind Literal verbatim from
    doc-17:141."""

    scope: dict[str, str]
    """Doc-17:142 -- the scope-dimensions dict (same free-form pattern
    as the supervisor artifact)."""

    value: dict[str, Any]
    """Doc-17:143 -- the typed policy-value dict. Free-form ``dict[str,
    Any]`` so per-policy-kind value shape lives in the consumer-side
    validation interface."""

    required_queue_tests: list[str]
    """Doc-17:144 -- the list of required merge-queue test names that
    MUST pass before this policy artifact can be activated by the
    Slice 08 + Slice 12 consumer (per doc-17:157-158 *"covered by
    merge queue tests"*)."""


# --- GovernancePolicyRecommendation (doc-17:75-97) --------------------------


class GovernancePolicyRecommendation(BaseModel):
    """Doc-17:75-97 -- the governance policy recommendation record
    shape.

    A governance policy recommendation is the typed advisory contract
    that lets scheduler feedback, failure routing, supervisor,
    dashboard, planning, and merge-queue consumers consume governance
    findings without granting the governance analyzer direct mutation
    authority. Per doc-17 § "Acceptance Criteria":

    * *"Governance recommendations are typed, evidence-backed, and
      consumer-scoped."* (doc-17:216) -- enforced by the typed
      ``consumer`` + ``source_finding_ids`` + ``source_metric_refs``
      + ``proposed_policy_artifact`` fields.
    * *"Recommendation generation has no direct mutation authority."*
      (doc-17:217) -- enforced by the typed surface (the BaseModel
      has no mutation methods; the writer is a pure projection per
      the doc-17:182-188 *"Store recommendations as typed governance
      rows..."* discipline).
    * *"Behavior-changing recommendations require explicit policy
      artifacts and tests."* (doc-17:218) -- enforced by the typed
      ``proposed_policy_artifact`` union + the ``requires_tests``
      list.
    * *"Consumers can ignore or reject recommendations with durable
      rationale."* (doc-17:219) -- enforced by the separate
      :class:`PolicyRecommendationDecision` typed shape carrying
      ``rationale`` + ``evidence_refs``.
    * *"The interface is compatible with Slices 07, 09, 10, and 12
      implementation logs."* (doc-17:220) -- enforced by the typed
      :data:`PolicyConsumer` Literal value-set (6 consumer values).

    Per doc-17:159-163 *"`activated` is deliberately not a
    `GovernancePolicyRecommendation.status`."* the typed
    :data:`PolicyRecommendationStatus` Literal does NOT include
    ``activated`` as a value; activation is consumer-owned (Slice 07
    / 09 / 10 / 12).

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-17:240-289).** The :attr:`source_finding_ids` field carries
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    string references back to Slice 16 1st sub-slice findings (per
    doc-17:80 ``source_finding_ids: list[str]`` -- the by-name
    reference shape; NOT the typed BaseModel). The
    :attr:`source_metric_refs` field carries
    :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    ref strings back to Slice 15 1st sub-slice metric values (per
    doc-17:81 ``source_metric_refs: list[str]`` -- the by-name
    reference shape; NOT the typed BaseModel). The by-name reference
    discipline mirrors the Slice 16 1st sub-slice
    :attr:`GovernanceFinding.metric_refs: list[str]` pattern at
    ``finding_engine.py:574``.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    """Doc-17:76 -- the deterministic dedupe key for the recommendation.

    Mirrors the Slice 16 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    pattern at ``finding_engine.py:443``; the key is the stable hash
    subsequent Slice 17 sub-slices use to deduplicate recommendations
    across reruns. The
    :func:`compute_policy_recommendation_idempotency_key` helper
    produces the canonical SHA-256-derived key from the recommendation's
    logical inputs (consumer + recommendation_id + sorted source
    finding ids + sorted source metric refs + sorted counterfactual
    result refs)."""

    recommendation_id: str
    """Doc-17:77 -- the stable recommendation identifier string. Per
    doc-17:188 *"If a consumer later activates a policy, it writes
    its own activation artifact and references the recommendation
    id."* the recommendation id is the typed reference the
    consumer-owned activation artifact links back to."""

    consumer: PolicyConsumer
    """Doc-17:78 -- the typed consumer taxonomy classification from the
    6-value :data:`PolicyConsumer` Literal (doc-17:65). Per Pydantic
    Literal validation the field accepts only one of the 6 values;
    unknown values fail closed with a typed ``ValidationError``."""

    status: PolicyRecommendationStatus
    """Doc-17:79 -- the typed lifecycle-status classification from the
    6-value :data:`PolicyRecommendationStatus` Literal (doc-17:66-73).
    Per Pydantic Literal validation the field accepts only one of the
    6 values; unknown values (including the deliberately-excluded
    ``activated`` value per doc-17:159-163) fail closed with a typed
    ``ValidationError``."""

    source_finding_ids: list[str]
    """Doc-17:80 -- the list of Slice 16
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    string references the recommendation grounds on.

    Per doc-17:80 the field is ``list[str]`` (just the string ids; NOT
    the typed BaseModel). The by-name reference shape mirrors the
    Slice 16 1st sub-slice
    :attr:`GovernanceFinding.metric_refs: list[str]` pattern at
    ``finding_engine.py:574`` and the Slice 17 doc:80-81
    contract verbatim.

    Per doc-17:217 + doc-17:60 a recommendation that lacks source
    finding ids is a doc-17:60 blocking-deviation case (*"A
    recommendation lacks source findings, confidence, or owner
    component."* is doc-17:60 blocking-deviation). The list MAY be
    empty in this 1st sub-slice (the at-least-one-source-finding
    invariant lives in the future Slice 17 2nd sub-slice
    recommendation builder + per-consumer validation interface)."""

    source_metric_refs: list[str]
    """Doc-17:81 -- the list of Slice 15
    :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    ref strings the recommendation grounds on.

    Per doc-17:81 the field is ``list[str]`` (just the ref strings;
    NOT the typed BaseModel). The by-name reference shape mirrors the
    Slice 16 1st sub-slice
    :attr:`GovernanceFinding.metric_refs: list[str]` pattern at
    ``finding_engine.py:574`` and the Slice 17 doc:80-81
    contract verbatim.

    Per doc-15:79-80 the Slice 15
    :class:`GovernanceMetricValue.definition_name` +
    :attr:`GovernanceMetricValue.definition_version` carry the
    by-name reference shape; the Slice 17 recommendation surface uses
    the ref-string projection (e.g.
    ``"tasks_per_hour@v1"`` / ``"commit_failures_per_task@v1.2"``)."""

    counterfactual_result_refs: list[str]
    """Doc-17:82 -- the list of Slice 18 counterfactual-replay result
    ref strings the recommendation cites as replay evidence.

    Per doc-17:173-174 *"Add replay requirement hooks so any
    behavior-changing recommendation can point to Slice 18
    counterfactual results."* the field is the typed reference back to
    Slice 18 counterfactual replay outputs; this 1st sub-slice does
    NOT yet wire the Slice 18 replay surface (that lands in Slice 17
    5th sub-slice per doc-17:173-174). The list MAY be empty for
    recommendations that do not propose behavior changes (per
    doc-17:198-200 the safe-runtime-action=false case)."""

    confidence: float
    """Doc-17:83 -- the confidence score in the recommendation's
    correctness (0.0 = no confidence, 1.0 = full confidence).

    Per doc-17:204 *"Recommendation builder refuses findings below
    confidence threshold."* the confidence threshold gates whether a
    finding feeds a recommendation; per doc-17:60 a recommendation
    that lacks confidence is a doc-17:60 blocking-deviation case."""

    expected_impact: dict[str, float]
    """Doc-17:84 -- the typed expected-impact dict mapping impact
    dimension names to expected magnitudes (e.g.
    ``{"tasks_per_hour_delta": 0.15, "commit_failure_rate_delta":
    -0.05}``).

    Per doc-17:206 *"Recommendation includes source finding ids,
    metric refs, expected impact, activation requirements, and
    rollback requirements."* the expected-impact dict is a required
    audit field; the (future) Slice 17 recommendation builder + per-
    consumer validation interface enforces a controlled vocabulary
    over the impact-dimension names."""

    risk_level: Literal["low", "medium", "high"]
    """Doc-17:85 -- the 3-value risk-level Literal classification. Per
    Pydantic Literal validation the field accepts only one of the 3
    values; unknown values fail closed with a typed
    ``ValidationError``."""

    safe_runtime_action: bool
    """Doc-17:86 -- the typed boolean flag for whether the
    recommendation's proposed policy artifact is safe to activate as a
    runtime action.

    Per doc-17:198-200 *"Safe runtime action false: recommendation
    can be reported but not consumed by runtime policy without a
    later implementation plan."* the flag gates whether the
    (future) Slice 17 6th sub-slice consumer read API exposes the
    recommendation as an accepted-but-not-activated policy artifact."""

    requires_tests: list[str]
    """Doc-17:87 -- the list of test names that MUST pass before the
    proposed policy artifact can be activated by the consumer
    (mirroring the consumer-specific ``required_tests`` /
    ``required_queue_tests`` fields on the per-consumer policy artifact
    BaseModels; per doc-17:218 *"Behavior-changing recommendations
    require explicit policy artifacts and tests."*)."""

    proposed_policy_artifact: (
        SchedulerPolicyArtifact
        | FailureRouterPolicyArtifact
        | SupervisorPolicyArtifact
        | DashboardPolicyArtifact
        | PlanningPolicyArtifact
        | MergeQueuePolicyArtifact
    )
    """Doc-17:88-95 -- the typed proposed-policy-artifact union (6
    consumer-specific BaseModels per doc-17:107-145).

    Per doc-17:88-95 the union is the typed surface that lets the
    (future) Slice 17 recommendation builder produce a typed artifact
    of the correct consumer-specific shape; the consumer-specific
    validation interface (per doc-17:170-171 step 3) consumes the
    typed artifact to enforce per-consumer policy-shape rules.

    Per Pydantic v2 discriminated-union semantics the field accepts
    any one of the 6 typed BaseModels; the typed surface does NOT
    enforce a discriminator field at construction (the consumer
    field is the implicit discriminator; per the future Slice 17
    2nd sub-slice recommendation builder the discriminator
    cross-check lives in the builder + per-consumer validation
    interface)."""

    activation_requirements: list[str]
    """Doc-17:96 -- the list of activation-requirement strings the
    consumer MUST satisfy before activating this recommendation's
    proposed policy artifact (e.g.
    ``["replay_against_8ac124d6_passes", "scheduler_test_suite_green"]``).

    Per doc-17:206 the activation requirements are part of the
    required-audit-fields set the recommendation builder emits; the
    consumer-owned activation artifact references these requirements
    in its audit trail."""

    rollback_requirements: list[str]
    """Doc-17:97 -- the list of rollback-requirement strings the
    consumer MUST satisfy when rolling back an activated policy
    artifact (e.g.
    ``["disable_route_via_failure_router_admin_api", "revert_to_prior_route_table_version"]``).

    Per doc-17:206 + doc-17:222-226 the rollback requirements are
    part of the required-audit-fields set the recommendation builder
    emits; per doc-17:225-226 *"Activated policy rollback belongs to
    the owning consumer, not the governance analyzer."* the rollback
    surface is consumer-owned; the recommendation records the proposed
    rollback shape but does NOT execute it."""


# --- PolicyRecommendationDecision (doc-17:99-105) ---------------------------


class PolicyRecommendationDecision(BaseModel):
    """Doc-17:99-105 -- the policy recommendation decision record shape.

    A policy recommendation decision is the typed audit record carrying
    the decision (accept / reject / needs_more_evidence) + decider +
    decided-at timestamp + rationale + evidence refs the (future)
    Slice 17 4th sub-slice decision-record writer emits when a
    governance reviewer reviews a recommendation.

    Per doc-17:172 *"Add decision records for accept/reject/needs-more-
    evidence."* the typed surface owns the 5 doc-17:100-105 fields;
    this 1st sub-slice exposes the typed shape but does NOT yet wire
    the writer to a real consumer site (that lives in subsequent
    sub-slices per doc-17:172).

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
    the authority for governance evidence-ref semantics; future Slice
    17 sub-slices populate this list using the Slice 13a typed shape
    directly.
    """

    model_config = ConfigDict(extra="forbid")

    recommendation_id: str
    """Doc-17:100 -- the typed reference back to the
    :attr:`GovernancePolicyRecommendation.recommendation_id` the
    decision applies to."""

    decision: Literal["accept", "reject", "needs_more_evidence"]
    """Doc-17:101 -- the 3-value decision Literal verbatim from
    doc-17:101. Per Pydantic Literal validation the field accepts only
    one of the 3 values; unknown values fail closed with a typed
    ``ValidationError``."""

    decided_by: str
    """Doc-17:102 -- the typed decider identifier string (e.g. the
    governance reviewer's typed-source id)."""

    decided_at: datetime
    """Doc-17:103 -- the typed decision timestamp. Per the Pydantic v2
    + Slice 13A + Slice 14 + Slice 15 + Slice 16 canonical-JSON
    discipline the datetime field projects to ISO-8601 string under
    :meth:`BaseModel.model_dump` with ``mode='json'``."""

    rationale: str
    """Doc-17:104 -- the typed decision-rationale string. Per
    doc-17:219 *"Consumers can ignore or reject recommendations with
    durable rationale."* the rationale is the typed audit-trail
    surface that lets future reviewers understand the original
    decision."""

    evidence_refs: list[GovernanceEvidenceRef]
    """Doc-17:105 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    evidence references the decision cites.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-17:240-289).** This field is the list of Slice 13a shared
    :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared model is the
    authority for governance evidence-ref semantics; future Slice 17
    sub-slices populate this list using the Slice 13a typed shape
    directly.

    Per the Slice 13A invariant + doc-17:240-253 the typed surface
    enforces the refs-only no-raw-body-hydration discipline at
    construction (the :class:`GovernanceEvidenceRef` BaseModel
    validates the typed-source contract; no raw evidence body is
    embedded in the decision row)."""


# --- Recommendation idempotency-key helpers (mirrors Slice 13A
#     compute_completeness_digest + Slice 14 compute_payload_sha256 +
#     Slice 15 compute_scorecard_digest + Slice 16 1st sub-slice
#     compute_finding_idempotency_key canonical-JSON discipline) ------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_metrics._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates
    lexicographic key ordering and the compact separator set so the
    resulting bytes are stable across Python versions / platforms /
    dict ordering.

    Per the P3-15-1-1 carry the ``default=str`` superset is benign
    because the canonical projections this module computes go through
    :meth:`BaseModel.model_dump` with ``mode='json'`` first, so
    ``datetime`` is already lowered to ISO-8601 strings before this
    helper sees the object; the ``default=str`` is a defence-in-depth
    fallback for any non-JSON-native scalars (e.g. ``Path`` objects in
    test fixtures).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.completeness._sha256_hex`
    + :func:`iriai_build_v2.execution_control.commit_provenance._sha256_hex`
    + :func:`iriai_build_v2.execution_control.governance_metrics._sha256_hex`
    + :func:`iriai_build_v2.execution_control.finding_engine._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_policy_recommendation_dict(
    recommendation: GovernancePolicyRecommendation,
) -> dict[str, Any]:
    """Project a :class:`GovernancePolicyRecommendation` to its
    canonical-JSON dict representation.

    This helper produces the canonical-dict projection used by
    :func:`compute_policy_recommendation_idempotency_key` (when
    computing a recommendation's deterministic dedupe key from its
    logical inputs) and by subsequent Slice 17 sub-slices when
    persisting recommendation rows at
    ``review:governance-recommendations:{corpus_id}`` per
    doc-17:182-188.

    The projection uses :meth:`BaseModel.model_dump` with ``mode='json'``
    so any nested ``datetime`` field on the typed proposed-policy-
    artifact union projects to its ISO-8601 string form (cross-process
    stable). The resulting dict is the input to
    :func:`compute_policy_recommendation_idempotency_key`; both helpers
    use :func:`_canonical_json` for deterministic serialisation.

    Mirrors the Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.canonical_finding_dict`
    pattern verbatim.
    """

    return recommendation.model_dump(mode="json")


def compute_policy_recommendation_idempotency_key(
    *,
    consumer: PolicyConsumer,
    recommendation_id: str,
    source_finding_ids: list[str],
    source_metric_refs: list[str],
    counterfactual_result_refs: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    :class:`GovernancePolicyRecommendation`.

    Mirrors the Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    pattern verbatim; the key is computed over the 5 logical inputs:

    * ``consumer`` -- the typed consumer taxonomy classification.
    * ``recommendation_id`` -- the stable recommendation identifier.
    * ``source_finding_ids`` -- the list of Slice 16
      :class:`GovernanceFinding.idempotency_key` strings; the list is
      sorted before digesting so the key is order-invariant w.r.t.
      finding-id ordering.
    * ``source_metric_refs`` -- the list of Slice 15
      :class:`GovernanceMetricValue` ref strings; the list is sorted
      before digesting so the key is order-invariant w.r.t.
      metric-ref ordering.
    * ``counterfactual_result_refs`` -- the list of Slice 18
      counterfactual-replay result ref strings; the list is sorted
      before digesting so the key is order-invariant w.r.t.
      replay-ref ordering.

    Per doc-17:188 *"If a consumer later activates a policy, it writes
    its own activation artifact and references the recommendation
    id."* the recommendation id is the typed cross-consumer reference
    surface; the idempotency key is the typed dedupe surface
    subsequent sub-slices use to detect duplicate recommendations
    across reruns of the recommendation builder.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    + Slice 15
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    + Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim.
    """

    payload: dict[str, Any] = {
        "consumer": consumer,
        "recommendation_id": recommendation_id,
        # Sort the source-id lists so the key is order-invariant w.r.t.
        # source-id ordering (per the Slice 16 1st sub-slice precedent
        # at compute_finding_idempotency_key + the doc-17:80-82
        # by-name reference discipline).
        "source_finding_ids": sorted(source_finding_ids),
        "source_metric_refs": sorted(source_metric_refs),
        "counterfactual_result_refs": sorted(counterfactual_result_refs),
    }
    return _sha256_hex(_canonical_json(payload))
