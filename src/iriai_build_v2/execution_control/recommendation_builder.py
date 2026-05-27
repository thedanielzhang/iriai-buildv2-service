"""Slice 17 second sub-slice -- recommendation builder that converts
high-confidence governance findings into consumer-specific draft policy
artifacts.

This module owns the **doc-17 § Refactoring Steps step 2** verbatim:

    *"Add recommendation builders that convert high-confidence findings
    into consumer-specific draft policy artifacts."* (doc-17:168-169)

Per the Slice 17 1st sub-slice typed-shape foundation
(:mod:`iriai_build_v2.execution_control.policy_recommendation`) the
recommendation builder consumes:

* The Slice 16 1st sub-slice typed
  :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
  BaseModels with ``requires_policy_artifact=True`` and ``confidence >=
  min_threshold`` (per doc-17:204 *"Recommendation builder refuses
  findings below confidence threshold."*).
* A typed routing config (the per-consumer mapping table).
* A typed minimum-confidence threshold (default
  :data:`DEFAULT_MIN_CONFIDENCE_THRESHOLD`).

And emits typed
:class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
records carrying typed
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.proposed_policy_artifact`
union members (one of the 6 doc-17:107-145 consumer-specific
``*PolicyArtifact`` BaseModels) -- always with
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.status`
= ``"draft"`` per doc-17:166-167 (the typed
:data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationStatus`
Literal range enforces this at construction).

**Below-threshold finding behaviour (doc-17:190-193 + doc-17:204).** Per
doc-17:204 the builder REFUSES findings below the confidence threshold.
This module implements **option (a): refuse-and-no-emit** -- no
recommendation is emitted for below-threshold findings; the refused
finding's
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
is recorded in
:attr:`RecommendationBuilderResult.refused_findings` so the caller has
a typed audit trail of which findings were refused. The alternative
**option (b): emit status="needs_more_evidence"** (per doc-17:190-193
*"Low-confidence finding: no recommendation, or recommendation status
`needs_more_evidence`."*) is documented as a FUTURE per-builder-config
opt-in (NOT the default in this sub-slice).

**Conflicting-recommendations behaviour (doc-17:194-195).** Per
doc-17:194-195 *"Conflicting recommendations for one consumer: mark both
draft and require human or policy owner decision."* the builder emits
BOTH recommendations as :attr:`status` = ``"draft"`` when 2+ findings
route to the same consumer with the same ``affected_scope`` dict
(per :func:`_scope_key` identity); the conflicting recommendations are
NOT coalesced (the human/policy-owner decision lives in the future
Slice 17 4th sub-slice decision-record writer per doc-17:172).

**Per-consumer routing (doc-17:147-163 + the doc-16
:data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
14-value Literal).** The
:data:`_DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING` table maps each
finding kind to its owning consumer per the cross-slice consumer
contracts in doc-17:147-158:

* ``failure_router`` -- runtime instability + unsafe routes +
  resource safety risk (doc-17:151-154 Slice 07 consumer).
* ``scheduler`` -- workflow inefficiency + over/under-verification +
  scheduler mismatch (doc-17:148-150 Slice 09 consumer).
* ``supervisor`` -- product defect clusters + plan deviation +
  governance evidence conflict (doc-17:155 Slice 10 consumer
  read-only).
* ``dashboard`` -- stale projection + provenance gap (doc-17:155
  Slice 10 consumer read-only).
* ``planning`` -- task contract weakness (doc-17:156 advisory-only).
* ``merge_queue`` -- merge queue drag (doc-17:157-158 Slice 08 + 12
  consumer).

Per doc-16:163 *"Product defect clusters can be observed, but workflow
policy recommendations must cite workflow-related causes."* product-
defect findings (``product_defect_related=True`` +
``workflow_related=False``) are routed to the supervisor read-only
display ONLY; they do NOT produce executable workflow policy artifacts
(the supervisor + dashboard consumers carry typed
``read_only: Literal[True] = True`` so the typed surface enforces this
at construction per the Slice 17 1st sub-slice
:class:`~iriai_build_v2.execution_control.policy_recommendation.SupervisorPolicyArtifact.read_only`
and
:class:`~iriai_build_v2.execution_control.policy_recommendation.DashboardPolicyArtifact.read_only`
typed surfaces).

**Per-consumer artifact construction.** Each consumer has a private
:func:`_build_<consumer>_artifact` factory function that constructs the
typed consumer-specific
:class:`~iriai_build_v2.execution_control.policy_recommendation.FailureRouterPolicyArtifact`
/
:class:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact`
/ etc. BaseModel from the finding's typed fields. The factory functions
ground their fields on the finding's
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.kind`
+
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.class_name`
+
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`
+
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.metric_refs`
+
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.recommended_action_display`
+
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.requires_policy_artifact`
fields per doc-17:107-145.

**Fail-closed semantics (feedback_no_silent_degradation).** The builder
NEVER raises on input. On a structural construction failure (e.g. a
Pydantic ``ValidationError`` raised by the typed artifact BaseModel),
the builder records a typed
:class:`RecommendationBuilderEmissionGap` in the
:attr:`RecommendationBuilderResult.gap_findings` list and continues
processing subsequent findings; the gap-finding shape carries the
failed finding's :attr:`idempotency_key` + the failure reason + the
target consumer so the caller has a typed audit trail of which findings
failed to produce a recommendation. Per doc-14:242-243 (inherited per
the governance-projection observer pattern) the failure is
NON-BLOCKING: the corresponding typed failure id
:data:`RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID`
(``recommendation_builder_emission_failed``) registers under the
EXISTING ``evidence_corruption`` failure_class with the EXISTING
NON-blocking RouteAction ``retry_governance_projection`` (REUSED from
Slice 14 2nd sub-slice; NOT a new route action; mirrors Slice 15 2nd +
4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th sub-slice precedent verbatim).

**Source-finding-ids + source-metric-refs (doc-17:80-81).** The
recommendation builder populates the typed
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_finding_ids`
field from the finding's
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
string (per doc-17:80 by-name reference contract -- the field is
``list[str]``, NOT the typed BaseModel) and the typed
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_metric_refs`
field from the finding's typed
:attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.metric_refs`
list (per doc-17:81 by-name reference contract -- the field is
``list[str]``, the Slice 16 1st sub-slice already projects metric
references as strings per doc-16:93).

**Implementation discipline.** Stdlib (``json``) + Pydantic v2 + Slice 13a
modules (``..workflows.develop.governance.models``) + Slice 16 1st
sub-slice (:mod:`iriai_build_v2.execution_control.finding_engine`) +
Slice 17 1st sub-slice
(:mod:`iriai_build_v2.execution_control.policy_recommendation`) only.
NO imports from ``governance/`` outside ``governance.models``. NO
imports from other parts of ``execution_control/`` beyond
``finding_engine`` + ``policy_recommendation`` (this module is the
typed projection layer that composes them). NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.governance_finding_writer`
(Slice 16 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
(Slice 15 4th sub-slice) +
:mod:`iriai_build_v2.execution_control.commit_provenance_writer`
(Slice 14 2nd sub-slice) verbatim: ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``float`` / ``bool`` /
``list`` / ``dict``). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 + Slice 15 + Slice 16 + Slice 17 1st sub-slice precedent
verbatim without introducing new abstractions.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.finding_engine import (
    FindingKind,
    GovernanceFinding,
)
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


__all__ = [
    # Typed failure id (doc-17:204 + doc-14:242-243 NON-BLOCKING).
    "RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID",
    # Default minimum-confidence threshold (doc-17:204).
    "DEFAULT_MIN_CONFIDENCE_THRESHOLD",
    # Typed inputs / result / gap (mirrors Slice 16 4th sub-slice
    # FindingWriterInputs / FindingPersistenceGap precedent).
    "RecommendationBuilderInputs",
    "RecommendationBuilderResult",
    "RecommendationBuilderEmissionGap",
    # Pure helper for recommendation_id construction.
    "compute_recommendation_id",
    # The builder class (doc-17:168-169 step 2).
    "RecommendationBuilder",
]


# --- Typed failure id (doc-17:204 + doc-14:242-243 NON-BLOCKING) ------------


RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID: Literal[
    "recommendation_builder_emission_failed"
] = "recommendation_builder_emission_failed"
"""Doc-17:204 + doc-14:242-243 -- the typed failure id the recommendation
builder projects onto when a per-finding emission step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 17 pattern matches the Slice 14 + Slice 15 + Slice 16 non-blocking
governance projection observer (the recommendation builder is also a
post-checkpoint governance projection observer).

The Slice 14 + Slice 15 2nd / 4th + Slice 16 2nd / 3rd-A / 3rd-B / 4th
sub-slice precedents are the source-of-truth for the non-blocking
governance-projection failure-routing discipline:

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

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to recommendation
builder emission failures (this slice is also a post-checkpoint
governance projection observer).
"""


# --- Default minimum-confidence threshold (doc-17:204) ----------------------


DEFAULT_MIN_CONFIDENCE_THRESHOLD: float = 0.7
"""Doc-17:204 -- the default minimum-confidence threshold the
recommendation builder applies when filtering findings.

Per *"Recommendation builder refuses findings below confidence
threshold."* the threshold gates which findings are eligible to feed
a recommendation. The default value 0.7 is deliberately ABOVE the
Slice 16 2nd sub-slice
:attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.min_confidence`
calibration anchor of 0.5 because:

1. The rule engine's 0.5 threshold gates which findings are emitted at
   all; the recommendation builder's 0.7 threshold gates which
   already-emitted findings additionally qualify to feed a policy
   recommendation. This is a two-stage discipline: the rule engine is
   permissive (emits findings for governance review even at borderline
   confidence per doc-16:193 *"Low confidence: findings may be reported
   but cannot feed policy recommendations."*) but the recommendation
   builder is strict (refuses below-threshold findings from feeding
   policy recommendations per doc-17:204).
2. The doc-15 calibration anchors (per the Slice 15 5th sub-slice
   fixture-based calibration discipline) place a Pareto-justifiable
   recommendation-relevant signal threshold at 0.7; below this, the
   risk of recommending an artifact that fails consumer validation
   (doc-17:208-210) outweighs the audit cost of refusing.

The caller MAY override this default per
:attr:`RecommendationBuilderInputs.min_confidence_threshold` (e.g. a
calibration-mode caller may set 0.5 to mirror the rule engine; a
production-mode caller may tighten to 0.85 for high-risk consumers).

Per doc-17:190-193 *"Low-confidence finding: no recommendation, or
recommendation status `needs_more_evidence`."* the alternative
``status="needs_more_evidence"`` projection is documented as a FUTURE
per-builder-config opt-in (NOT the default in this sub-slice).
"""


# --- Per-consumer routing table (doc-17:147-163 + doc-16 FindingKind) -------


_DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING: dict[FindingKind, PolicyConsumer] = {
    # Workflow drag categories (doc-16:209 + doc-17:148-150 scheduler).
    "workflow_inefficiency": "scheduler",
    "over_verification": "scheduler",
    "under_verification": "scheduler",
    "scheduler_mismatch": "scheduler",
    # Unsafe route + runtime instability + resource safety risk
    # (doc-17:151-154 failure_router).
    "unsafe_route": "failure_router",
    "runtime_instability": "failure_router",
    "resource_safety_risk": "failure_router",
    # Merge queue drag (doc-17:157-158 merge_queue).
    "merge_queue_drag": "merge_queue",
    # Task contract weakness (doc-17:156 planning).
    "task_contract_weakness": "planning",
    # Provenance gap + stale projection (doc-17:155 dashboard read-only).
    "stale_projection": "dashboard",
    "provenance_gap": "dashboard",
    # Implementation drift + governance evidence conflict + product
    # defect cluster (doc-17:155 supervisor read-only). Per doc-16:163
    # product defect clusters are observed but workflow policy
    # recommendations must cite workflow-related causes; they are
    # routed to the supervisor read-only display ONLY.
    "implementation_plan_deviation": "supervisor",
    "governance_evidence_conflict": "supervisor",
    "product_defect_cluster": "supervisor",
}
"""Doc-17:147-163 + doc-16:63-78 -- the default per-finding-kind ->
consumer routing table.

Each
:data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
value (14 total per doc-16:63-78) maps to one
:data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
value (6 total per doc-17:65) per the cross-slice consumer contracts
in doc-17:147-158:

* ``failure_router`` (3 kinds) -- ``unsafe_route`` +
  ``runtime_instability`` + ``resource_safety_risk``. Per doc-17:151-154
  failure-router runtime behavior consumes only consumer-owned
  ``activated`` route-budget or route-priority policy records with
  replay coverage; the recommendation builder produces ``draft``
  artifacts (per doc-17:166-167).
* ``scheduler`` (4 kinds) -- ``workflow_inefficiency`` +
  ``over_verification`` + ``under_verification`` +
  ``scheduler_mismatch``. Per doc-17:148-150 scheduler runtime
  behavior consumes only consumer-owned ``activated`` policy records.
* ``supervisor`` (3 kinds) -- ``implementation_plan_deviation`` +
  ``governance_evidence_conflict`` + ``product_defect_cluster``. Per
  doc-17:155 supervisor consumes advisory summaries and must remain
  read-only (the typed
  :class:`~iriai_build_v2.execution_control.policy_recommendation.SupervisorPolicyArtifact.read_only`
  ``Literal[True] = True`` enforces this at construction).
* ``dashboard`` (2 kinds) -- ``stale_projection`` + ``provenance_gap``.
  Per doc-17:155 dashboard consumes advisory summaries and must remain
  read-only (the typed
  :class:`~iriai_build_v2.execution_control.policy_recommendation.DashboardPolicyArtifact.read_only`
  ``Literal[True] = True`` enforces this at construction).
* ``planning`` (1 kind) -- ``task_contract_weakness``. Per doc-17:156
  planning consumes historical recommendations as context for future
  DAG design.
* ``merge_queue`` (1 kind) -- ``merge_queue_drag``. Per doc-17:157-158
  merge queue consumes only consumer-owned ``activated`` policy
  artifacts covered by merge queue tests.

Total: **14 of 14** FindingKind values routed per doc-16:63-78. The
caller MAY override this default per
:attr:`RecommendationBuilderInputs.finding_kind_to_consumer_routing`
(e.g. a corpus-specific calibration may route ``workflow_inefficiency``
to ``failure_router`` instead of ``scheduler``).
"""


# --- Typed inputs (mirrors Slice 16 4th sub-slice FindingWriterInputs) ------


class RecommendationBuilderInputs(BaseModel):
    """Doc-17:168-169 step 2 -- typed bundle of all inputs the
    recommendation builder consumes.

    The bundle composes:

    * ``corpus_id`` -- the corpus identifier the findings group against
      (e.g. ``"8ac124d6"`` for the calibration fixture per
      doc-16:195-204; future feature ids for production findings).
    * ``findings`` -- the list of
      :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
      records the builder filters + emits recommendations for. The
      builder filters to ``requires_policy_artifact=True`` AND
      ``confidence >= min_confidence_threshold`` per doc-17:204.
    * ``min_confidence_threshold`` -- the typed minimum-confidence
      threshold the builder applies; defaults to
      :data:`DEFAULT_MIN_CONFIDENCE_THRESHOLD`. Per doc-17:204.
    * ``finding_kind_to_consumer_routing`` -- the typed per-finding-kind
      -> consumer routing table; defaults to
      :data:`_DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING`. The caller MAY
      override this default with corpus-specific routing.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    Mirrors :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingWriterInputs`
    (Slice 16 4th sub-slice) +
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriterInputs`
    (Slice 15 4th sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 17 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/policy_recommendation.py:376
    # + the Slice 16 4th sub-slice precedent at
    # src/iriai_build_v2/execution_control/governance_finding_writer.py:531
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """The corpus identifier the findings group against (e.g.
    ``"8ac124d6"`` for the calibration fixture per doc-16:195-204;
    future feature ids for production findings)."""

    findings: list[GovernanceFinding]
    """Doc-17:168-169 step 2 -- the list of
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
    records the builder filters + emits recommendations for.

    The builder filters to ``requires_policy_artifact=True`` AND
    ``confidence >= min_confidence_threshold`` per doc-17:204.
    Below-threshold findings are recorded in
    :attr:`RecommendationBuilderResult.refused_findings` per the
    refuse-and-no-emit discipline."""

    min_confidence_threshold: float = DEFAULT_MIN_CONFIDENCE_THRESHOLD
    """Doc-17:204 -- the minimum-confidence threshold the builder
    applies. Defaults to :data:`DEFAULT_MIN_CONFIDENCE_THRESHOLD`
    (0.7).

    Per doc-17:204 *"Recommendation builder refuses findings below
    confidence threshold."* the threshold gates which findings are
    eligible to feed a recommendation. The caller MAY override the
    default (e.g. calibration-mode 0.5 to mirror the rule engine;
    production-mode 0.85 for high-risk consumers)."""

    finding_kind_to_consumer_routing: dict[FindingKind, PolicyConsumer] = Field(
        default_factory=lambda: dict(_DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING),
    )
    """Doc-17:147-163 + doc-16:63-78 -- the per-finding-kind -> consumer
    routing table. Defaults to
    :data:`_DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING` (which covers
    all 14 of 14 FindingKind values).

    The caller MAY override this default with corpus-specific routing
    (e.g. a calibration may route ``workflow_inefficiency`` to
    ``failure_router`` instead of ``scheduler``). The builder uses the
    routing table to dispatch each qualifying finding to the
    consumer-specific artifact factory."""


# --- Typed result (mirrors Slice 16 4th sub-slice FindingPersistenceGap +
#     the per-builder result aggregation pattern) ----------------------------


class RecommendationBuilderResult(BaseModel):
    """Doc-17:168-169 step 2 -- typed bundle of all outputs the
    recommendation builder produces.

    The bundle composes:

    * ``recommendations`` -- the list of typed
      :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
      records the builder emitted (all with
      :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.status`
      = ``"draft"`` per doc-17:166-167).
    * ``refused_findings`` -- the list of
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
      strings for findings refused per doc-17:204 (below the confidence
      threshold) or per the ``requires_policy_artifact=False`` filter.
    * ``gap_findings`` -- the list of typed
      :class:`RecommendationBuilderEmissionGap` records emitted when a
      per-finding emission step fails structurally (per
      :data:`RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID`).

    Per the doc-17:194-195 *"Conflicting recommendations for one
    consumer: mark both draft and require human or policy owner
    decision."* contract the builder DOES emit BOTH recommendations as
    ``status="draft"`` when 2+ findings route to the same consumer with
    the same ``affected_scope``; the conflicting recommendations are
    NOT coalesced.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    recommendations: list[GovernancePolicyRecommendation] = Field(
        default_factory=list,
    )
    """The list of typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    records the builder emitted (all with
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.status`
    = ``"draft"`` per doc-17:166-167).

    Empty by default (callers MAY construct a
    :class:`RecommendationBuilderResult` directly without invoking the
    builder, e.g. in tests)."""

    refused_findings: list[str] = Field(default_factory=list)
    """The list of
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    strings for findings refused per doc-17:204 (below the
    ``min_confidence_threshold``) or per the
    ``requires_policy_artifact=False`` filter.

    Per the doc-17:190-193 + doc-17:204 *"Recommendation builder refuses
    findings below confidence threshold."* contract the refused-finding
    ids are the typed audit trail of which findings did NOT qualify
    for a recommendation; subsequent Slice 17 sub-slices (decision-
    record writer + consumer read APIs + Slice 18 counterfactual replay
    + Slice 19 governance reporting) MAY use this list to surface
    refused findings in the audit log + dashboard."""

    gap_findings: list[RecommendationBuilderEmissionGap] = Field(
        default_factory=list,
    )
    """The list of typed
    :class:`RecommendationBuilderEmissionGap` records emitted when a
    per-finding emission step fails structurally (per
    :data:`RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    builder NEVER raises on input -- structural failures are recorded
    as typed gap findings (refs-only; the finding's
    :attr:`idempotency_key` + failure reason + target consumer). The
    gap-finding shape is the typed audit trail subsequent sub-slices
    use to detect + retry the failed emissions per doc-14:242-243
    (governance-projection failures NEVER block checkpointing / merge
    queue / resume)."""


# --- Typed gap projection (mirrors Slice 16 4th sub-slice FindingPersistenceGap +
#     Slice 15 4th sub-slice ScorecardPersistenceGap + Slice 14 2nd sub-slice
#     CommitProvenanceGapFinding precedent) ---------------------------------


class RecommendationBuilderEmissionGap(BaseModel):
    """Typed governance-gap finding produced when the recommendation
    builder fails to emit a recommendation for a finding structurally.

    Mirrors the Slice 16 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_finding_writer.FindingPersistenceGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-16:174-176 governance-projection discipline) the finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID`
    (``recommendation_builder_emission_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["recommendation_builder_emission_failed"]
    """Doc-17:204 + doc-14:192-201 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243 + doc-17:204."""

    corpus_id: str
    """The corpus scope of the failed emission (same as the
    :attr:`RecommendationBuilderInputs.corpus_id`)."""

    finding_idempotency_key: str | None
    """The
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    of the finding whose recommendation emission failed (refs-only;
    NOT the typed BaseModel per doc-17:80 + the Slice 13A invariant).

    ``None`` if the failure happened before the finding could be
    inspected (e.g. a corrupt findings-list bundle)."""

    target_consumer: PolicyConsumer | None
    """The
    :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
    the emission targeted, or ``None`` if the failure happened before
    the consumer could be determined (e.g. a missing routing-table
    entry for the finding's kind)."""

    reason: str
    """Free-form gap reason (e.g.
    ``policy_artifact_construction_failed``,
    ``unmapped_finding_kind``,
    ``recommendation_construction_failed``)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the Pydantic ValidationError detail, the finding kind, the routing
    table override). Free-form per the doc-14:192-201 + doc-16 + doc-17
    governance-finding contract."""


# --- Forward-ref resolution (Pydantic v2) -----------------------------------


# The :class:`RecommendationBuilderResult` field
# :attr:`gap_findings: list[RecommendationBuilderEmissionGap]` carries a
# forward reference to :class:`RecommendationBuilderEmissionGap` (defined
# below in source order). Rebuild the model so Pydantic resolves the
# forward reference at module-import time.
RecommendationBuilderResult.model_rebuild()


# --- Pure helper for recommendation_id construction (mirrors Slice 16
#     compute_finding_idempotency_key + Slice 14 compute_payload_sha256
#     canonical-JSON discipline) ----------------------------------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._canonical_json`
    verbatim: ``json.dumps(..., sort_keys=True, separators=(",", ":"))``
    -- the canonical form mandates lexicographic key ordering and the
    compact separator set so the resulting bytes are stable across
    Python versions / platforms / dict ordering.

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

    Mirrors :func:`iriai_build_v2.execution_control.finding_engine._sha256_hex`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_recommendation_id(
    *,
    consumer: PolicyConsumer,
    source_finding_idempotency_key: str,
    affected_scope_digest: str,
) -> str:
    """Compute the deterministic recommendation_id for a
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`.

    The recommendation_id is constructed from 3 logical inputs:

    * ``consumer`` -- the typed
      :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
      classification (one of the 6 values per doc-17:65).
    * ``source_finding_idempotency_key`` -- the typed
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
      string of the source finding (refs-only per doc-17:80).
    * ``affected_scope_digest`` -- the typed SHA-256 hex digest of the
      finding's
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`
      dict (canonical-JSON serialised).

    Per doc-17:188 *"If a consumer later activates a policy, it writes
    its own activation artifact and references the recommendation id."*
    the recommendation_id is the typed cross-consumer reference surface;
    the deterministic construction lets subsequent re-runs of the
    recommendation builder against the same finding-set produce
    byte-identical recommendation_ids (per doc-16:178 *"Finding ids
    are stable across reruns when input evidence and rule version do
    not change."* applied transitively).

    The format is ``"recommendation:{consumer}:{key_prefix}:{scope_digest_prefix}"``
    -- the prefixes are SHA-256 hex digest first 16 characters for
    compactness; the full digest is recorded in the typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.idempotency_key`
    via the Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    helper.
    """

    key_digest = _sha256_hex(source_finding_idempotency_key)
    return (
        f"recommendation:{consumer}:"
        f"{key_digest[:16]}:{affected_scope_digest[:16]}"
    )


def _affected_scope_digest(affected_scope: dict[str, Any]) -> str:
    """Compute the canonical SHA-256 hex digest of an
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`
    dict.

    The dict is serialised via :func:`_canonical_json` so the resulting
    digest is order-invariant w.r.t. key ordering + stable across
    Python versions / platforms.
    """

    return _sha256_hex(_canonical_json(affected_scope))


def _scope_key(affected_scope: dict[str, Any]) -> str:
    """Compute a stable scope key for the conflict-detection step
    (doc-17:194-195).

    Per doc-17:194-195 *"Conflicting recommendations for one consumer:
    mark both draft..."* the conflict detector groups recommendations
    by (consumer, scope_key); when 2+ findings produce recommendations
    for the same (consumer, scope_key) pair the builder emits BOTH as
    ``status="draft"`` (per doc-17:166-167 the default for new
    recommendations; the typed Literal range enforces this at
    construction). This sub-slice does NOT yet wire the
    human/policy-owner decision (that lives in the Slice 17 4th
    sub-slice decision-record writer per doc-17:172).

    Returns the canonical-JSON serialisation of ``affected_scope`` so
    the key is order-invariant + stable.
    """

    return _canonical_json(affected_scope)


# --- Per-consumer artifact factory functions (doc-17:107-145) ---------------
#
# Each factory function takes the typed
# :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
# and returns the typed consumer-specific
# :class:`~iriai_build_v2.execution_control.policy_recommendation.*PolicyArtifact`
# BaseModel. The functions ground their fields on the finding's typed
# fields (kind, class_name, affected_scope, metric_refs,
# recommended_action_display, requires_policy_artifact).
#
# Per the prompt's "Optional per-consumer builder helpers" note these
# factories are private (underscore-prefixed); the public surface is
# :class:`RecommendationBuilder.build_recommendations`.


def _build_failure_router_artifact(
    finding: GovernanceFinding,
) -> FailureRouterPolicyArtifact:
    """Doc-17:107-114 -- construct a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.FailureRouterPolicyArtifact`
    from a Slice 16 typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`.

    Per doc-17:107-114 the artifact carries 7 fields: failure_class +
    failure_type + action + route_budget_key + max_attempts +
    idempotency_key_template + required_tests. The factory grounds the
    fields on the finding's typed surface:

    * ``failure_class`` -- mapped from the finding's ``kind`` per the
      Slice 07 failure-class taxonomy (e.g. ``runtime_instability`` ->
      ``runtime_provider``; ``unsafe_route`` -> ``contract_violation``;
      ``resource_safety_risk`` -> ``resource_exhausted``).
    * ``failure_type`` -- mapped from the finding's ``class_name`` (the
      typed Slice 16 1st sub-slice
      :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
      string; e.g. ``"runtime_provider_instability"``).
    * ``action`` -- defaults to ``"retry"`` per the doc-07 typed
      action ladder (the consumer-side validation interface per the
      future Slice 17 3rd sub-slice may override).
    * ``route_budget_key`` -- ``"{failure_class}/{failure_type}"`` per
      the Slice 07 typed budget-key shape.
    * ``max_attempts`` -- defaults to 3 (the Slice 07 NON-blocking
      retry budget mid-band).
    * ``idempotency_key_template`` -- the typed template string the
      failure router would use to dedupe retries.
    * ``required_tests`` -- the finding's
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.metric_refs`
      list verbatim (the metric names are the typed audit-tests the
      consumer-side validation interface enforces; the future Slice 17
      3rd sub-slice may add per-consumer required-tests).
    """

    failure_class = _FINDING_KIND_TO_FAILURE_CLASS.get(
        finding.kind, "unknown"
    )
    failure_type = finding.class_name
    route_budget_key = f"{failure_class}/{failure_type}"
    return FailureRouterPolicyArtifact(
        failure_class=failure_class,
        failure_type=failure_type,
        action="retry",
        route_budget_key=route_budget_key,
        max_attempts=3,
        idempotency_key_template=(
            f"{failure_class}/{{task_id}}/{{attempt_window}}"
        ),
        required_tests=list(finding.metric_refs),
    )


def _build_scheduler_artifact(
    finding: GovernanceFinding,
) -> SchedulerPolicyArtifact:
    """Doc-17:116-120 -- construct a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact`
    from a Slice 16 typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`.

    Per doc-17:116-120 the artifact carries 4 fields: policy_kind +
    scope + value + guardrails. The factory grounds the fields on the
    finding's typed surface:

    * ``policy_kind`` -- one of ``"wave_cap"`` / ``"barrier"`` /
      ``"lane_priority"`` per the Slice 09 scheduler taxonomy. The
      mapping follows the finding's kind:
      ``"scheduler_mismatch"`` -> ``"barrier"``;
      ``"over_verification"`` / ``"under_verification"`` /
      ``"workflow_inefficiency"`` -> ``"wave_cap"``.
    * ``scope`` -- the finding's ``affected_scope`` dict; per
      doc-17:118 the scope-dimensions dict has free-form string
      values.
    * ``value`` -- a typed proposal dict (free-form per doc-17:119).
    * ``guardrails`` -- the finding's metric_refs (the audit-trail
      surface for the validation contract per doc-17:208 *"Scheduler
      consumer validation rejects policies that violate dependency,
      write-set, barrier, or safety constraints."*).
    """

    policy_kind = _SCHEDULER_FINDING_KIND_TO_POLICY_KIND.get(
        finding.kind, "wave_cap"
    )
    # Scope dict per doc-17:118 has free-form string values; cast the
    # finding's affected_scope dict to dict[str, str] preserving the
    # original keys (str(...) values for non-string scalars; the future
    # consumer-side validation interface per doc-17:170-171 step 3 may
    # tighten the value shape).
    scope: dict[str, str] = {
        str(k): str(v) for k, v in finding.affected_scope.items()
    }
    return SchedulerPolicyArtifact(
        policy_kind=policy_kind,
        scope=scope,
        value={"proposed_class_name": finding.class_name},
        guardrails=list(finding.metric_refs),
    )


def _build_supervisor_artifact(
    finding: GovernanceFinding,
) -> SupervisorPolicyArtifact:
    """Doc-17:122-126 -- construct a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.SupervisorPolicyArtifact`
    from a Slice 16 typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`.

    Per doc-17:122-126 the artifact carries 4 fields: policy_kind +
    scope + value + read_only (typed ``Literal[True] = True`` enforced
    at construction). The factory grounds the fields on the finding's
    typed surface:

    * ``policy_kind`` -- one of ``"classification_hint"`` /
      ``"dedupe"`` / ``"digest_priority"`` per the Slice 10
      supervisor taxonomy. Per doc-16:163 product-defect findings are
      routed to ``"classification_hint"`` per the supervisor read-only
      display.
    * ``scope`` -- the finding's ``affected_scope`` dict (free-form
      string values per doc-17:124).
    * ``value`` -- a typed proposal dict (free-form per doc-17:125).
    * ``read_only`` -- always ``True`` per doc-17:126 (the typed
      Literal range enforces this at construction; the Slice 17 1st
      sub-slice
      :class:`~iriai_build_v2.execution_control.policy_recommendation.SupervisorPolicyArtifact.read_only`
      defaults to ``True``).
    """

    scope: dict[str, str] = {
        str(k): str(v) for k, v in finding.affected_scope.items()
    }
    return SupervisorPolicyArtifact(
        policy_kind="classification_hint",
        scope=scope,
        value={
            "finding_kind": finding.kind,
            "class_name": finding.class_name,
            "recommended_display": finding.recommended_action_display,
        },
        # read_only defaults to True per doc-17:126 (Literal[True] =
        # True at the typed surface); pass through explicitly for
        # clarity.
    )


def _build_dashboard_artifact(
    finding: GovernanceFinding,
) -> DashboardPolicyArtifact:
    """Doc-17:128-132 -- construct a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.DashboardPolicyArtifact`
    from a Slice 16 typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`.

    Per doc-17:128-132 the artifact carries 4 fields: policy_kind +
    scope + value + read_only (typed ``Literal[True] = True`` enforced
    at construction). The factory grounds the fields on the finding's
    typed surface:

    * ``policy_kind`` -- one of ``"view_priority"`` /
      ``"alert_threshold"`` / ``"panel_visibility"`` per the Slice 10
      dashboard taxonomy. ``provenance_gap`` / ``stale_projection``
      findings map to ``"view_priority"`` (the dashboard read-only
      display surfaces the gap signal).
    * ``scope`` -- the finding's ``affected_scope`` dict.
    * ``value`` -- a typed proposal dict.
    * ``read_only`` -- always ``True`` per doc-17:132 (the typed
      Literal range enforces this at construction).
    """

    scope: dict[str, str] = {
        str(k): str(v) for k, v in finding.affected_scope.items()
    }
    return DashboardPolicyArtifact(
        policy_kind="view_priority",
        scope=scope,
        value={
            "finding_kind": finding.kind,
            "class_name": finding.class_name,
        },
        # read_only defaults to True per doc-17:132.
    )


def _build_planning_artifact(
    finding: GovernanceFinding,
) -> PlanningPolicyArtifact:
    """Doc-17:134-138 -- construct a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.PlanningPolicyArtifact`
    from a Slice 16 typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`.

    Per doc-17:134-138 the artifact carries 4 fields: policy_kind +
    scope + value + advisory_only (typed ``Literal[True] = True``
    enforced at construction). The factory grounds the fields on the
    finding's typed surface:

    * ``policy_kind`` -- one of ``"future_dag_hint"`` /
      ``"contract_template_hint"`` per the future planning consumer
      taxonomy. ``task_contract_weakness`` findings map to
      ``"contract_template_hint"`` (the planning consumer surfaces
      the contract-template signal).
    * ``scope`` -- the finding's ``affected_scope`` dict.
    * ``value`` -- a typed proposal dict.
    * ``advisory_only`` -- always ``True`` per doc-17:138 (the typed
      Literal range enforces this at construction).
    """

    scope: dict[str, str] = {
        str(k): str(v) for k, v in finding.affected_scope.items()
    }
    return PlanningPolicyArtifact(
        policy_kind="contract_template_hint",
        scope=scope,
        value={
            "class_name": finding.class_name,
            "recommended_display": finding.recommended_action_display,
        },
        # advisory_only defaults to True per doc-17:138.
    )


def _build_merge_queue_artifact(
    finding: GovernanceFinding,
) -> MergeQueuePolicyArtifact:
    """Doc-17:140-144 -- construct a typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.MergeQueuePolicyArtifact`
    from a Slice 16 typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`.

    Per doc-17:140-144 the artifact carries 4 fields: policy_kind +
    scope + value + required_queue_tests. The factory grounds the
    fields on the finding's typed surface:

    * ``policy_kind`` -- one of ``"lane_priority"`` /
      ``"recovery_budget"`` / ``"commit_gate_hint"`` per the Slice 08
      + Slice 12 merge-queue taxonomy. ``merge_queue_drag`` findings
      map to ``"recovery_budget"`` (the merge-queue consumer surfaces
      the queue-recovery signal).
    * ``scope`` -- the finding's ``affected_scope`` dict.
    * ``value`` -- a typed proposal dict.
    * ``required_queue_tests`` -- the finding's metric_refs (the
      audit-trail surface for the validation contract per
      doc-17:144 + doc-17:157-158).
    """

    scope: dict[str, str] = {
        str(k): str(v) for k, v in finding.affected_scope.items()
    }
    return MergeQueuePolicyArtifact(
        policy_kind="recovery_budget",
        scope=scope,
        value={"class_name": finding.class_name},
        required_queue_tests=list(finding.metric_refs),
    )


# --- Per-consumer artifact factory dispatch table ---------------------------


_CONSUMER_ARTIFACT_FACTORIES: dict[
    PolicyConsumer,
    Any,  # callable: GovernanceFinding -> <consumer-specific artifact>
] = {
    "failure_router": _build_failure_router_artifact,
    "scheduler": _build_scheduler_artifact,
    "supervisor": _build_supervisor_artifact,
    "dashboard": _build_dashboard_artifact,
    "planning": _build_planning_artifact,
    "merge_queue": _build_merge_queue_artifact,
}
"""The per-consumer artifact factory dispatch table.

Each :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
value maps to its private factory function. The
:meth:`RecommendationBuilder.build_recommendations` method dispatches
to the factory per the consumer determined by the routing table.
"""


# --- Per-finding-kind -> failure_class mapping (Slice 07 taxonomy bridge) ---


_FINDING_KIND_TO_FAILURE_CLASS: dict[FindingKind, str] = {
    "runtime_instability": "runtime_provider",
    "unsafe_route": "contract_violation",
    "resource_safety_risk": "resource_exhausted",
    # Other kinds NOT in this mapping (they route to non-
    # failure_router consumers per the routing table); the
    # ``"unknown"`` fallback in _build_failure_router_artifact handles
    # the (uncommon) case of a routing-override sending a non-
    # failure_router kind to failure_router.
}
"""Doc-17:108-109 -- the per-finding-kind -> Slice 07 failure_class
mapping used by :func:`_build_failure_router_artifact`.

Only the 3 finding kinds that the default routing table sends to
``failure_router`` (``runtime_instability`` + ``unsafe_route`` +
``resource_safety_risk``) have entries; other kinds fall through to
``"unknown"`` per the Slice 07 typed taxonomy fallback.
"""


# --- Per-scheduler-finding-kind -> policy_kind mapping ----------------------


_SCHEDULER_FINDING_KIND_TO_POLICY_KIND: dict[
    FindingKind, Literal["wave_cap", "barrier", "lane_priority"]
] = {
    "scheduler_mismatch": "barrier",
    "workflow_inefficiency": "wave_cap",
    "over_verification": "wave_cap",
    "under_verification": "wave_cap",
}
"""Doc-17:117 -- the per-scheduler-finding-kind ->
:attr:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact.policy_kind`
mapping used by :func:`_build_scheduler_artifact`.

Default fallback (in :func:`_build_scheduler_artifact`) is ``"wave_cap"``.
"""


# --- The builder class (doc-17:168-169 step 2) ------------------------------


class RecommendationBuilder:
    """Recommendation builder that converts high-confidence governance
    findings into consumer-specific draft policy artifacts
    (doc-17:168-169 step 2).

    Per *"Add recommendation builders that convert high-confidence
    findings into consumer-specific draft policy artifacts."* the
    builder consumes the Slice 16 1st sub-slice typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
    BaseModels + the Slice 17 1st sub-slice typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    BaseModel + the 6 doc-17:107-145 consumer-specific
    ``*PolicyArtifact`` BaseModels (all FROZEN in Slice 17 1st
    sub-slice) and emits typed
    :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
    records carrying typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.proposed_policy_artifact`
    union members per consumer.

    **Confidence-threshold discipline (doc-17:204).** The builder
    refuses findings below
    :attr:`RecommendationBuilderInputs.min_confidence_threshold`. The
    refused finding's
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    is recorded in :attr:`RecommendationBuilderResult.refused_findings`.

    **Requires-policy-artifact discipline (doc-16:99 + doc-17:218).**
    The builder also filters out findings with
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.requires_policy_artifact`
    = ``False`` -- those findings are observed but do NOT require a
    behavior-changing policy artifact (per doc-16:99 + doc-17:218).
    The filtered finding's idempotency_key is also recorded in
    :attr:`RecommendationBuilderResult.refused_findings`.

    **Conflict-detection discipline (doc-17:194-195).** The builder
    emits BOTH recommendations as ``status="draft"`` when 2+ findings
    route to the same consumer with the same
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`
    dict (per :func:`_scope_key` identity). The conflicting
    recommendations are NOT coalesced (the human/policy-owner decision
    lives in the Slice 17 4th sub-slice decision-record writer per
    doc-17:172).

    **Per-consumer routing (doc-17:147-163).** The builder uses the
    typed
    :attr:`RecommendationBuilderInputs.finding_kind_to_consumer_routing`
    table to dispatch each qualifying finding to the consumer-specific
    artifact factory. The default routing covers all 14 of 14
    FindingKind values per doc-16:63-78.

    **Fail-closed discipline (feedback_no_silent_degradation).** The
    builder NEVER raises a failure to the caller. Any structural
    failure projects onto a typed
    :class:`RecommendationBuilderEmissionGap` finding emitted on the
    :attr:`RecommendationBuilderResult.gap_findings` list. The
    corresponding typed failure id
    :data:`RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID`
    (``recommendation_builder_emission_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class with the EXISTING
    NON-blocking RouteAction ``retry_governance_projection`` (REUSED
    from Slice 14 2nd sub-slice; NOT a new route action).

    The builder is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple corpora.
    """

    def build_recommendations(
        self,
        inputs: RecommendationBuilderInputs,
    ) -> RecommendationBuilderResult:
        """Build the typed recommendation set for the typed inputs.

        Per doc-17:168-169 step 2 the method:

        1. Filters findings to ``requires_policy_artifact=True`` AND
           ``confidence >= min_confidence_threshold`` per doc-17:204.
        2. For each qualifying finding, dispatches to the consumer-
           specific artifact factory per the routing table.
        3. Constructs the typed
           :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
           record with
           :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.status`
           = ``"draft"`` per doc-17:166-167.
        4. Records refused findings in
           :attr:`RecommendationBuilderResult.refused_findings`.
        5. Records emission failures in
           :attr:`RecommendationBuilderResult.gap_findings` per
           feedback_no_silent_degradation.

        Per doc-17:194-195 conflicting recommendations (same consumer
        + same scope) are BOTH emitted as ``status="draft"`` (the
        typed Literal range enforces this at construction).

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        recommendations: list[GovernancePolicyRecommendation] = []
        refused_findings: list[str] = []
        gap_findings: list[RecommendationBuilderEmissionGap] = []

        for finding in inputs.findings:
            # Filter 1: requires_policy_artifact (per doc-16:99 +
            # doc-17:218). Findings observed but not requiring a
            # behavior-changing policy artifact are recorded as
            # refused (the caller has a typed audit trail).
            if not finding.requires_policy_artifact:
                refused_findings.append(finding.idempotency_key)
                continue

            # Filter 2: confidence threshold (per doc-17:204).
            # Refuse-and-no-emit: no recommendation is emitted; the
            # finding's idempotency_key is recorded in
            # refused_findings.
            if finding.confidence < inputs.min_confidence_threshold:
                refused_findings.append(finding.idempotency_key)
                continue

            # Filter 3: per-consumer routing. If the finding's kind is
            # not in the routing table, emit a typed gap finding.
            consumer = inputs.finding_kind_to_consumer_routing.get(
                finding.kind
            )
            if consumer is None:
                gap_findings.append(
                    RecommendationBuilderEmissionGap(
                        failure_id=(
                            RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID
                        ),
                        corpus_id=inputs.corpus_id,
                        finding_idempotency_key=finding.idempotency_key,
                        target_consumer=None,
                        reason="unmapped_finding_kind",
                        evidence_payload={"finding_kind": finding.kind},
                    )
                )
                continue

            # Per-consumer artifact construction (typed).
            try:
                artifact = self._construct_artifact(consumer, finding)
                recommendation = self._construct_recommendation(
                    consumer, finding, artifact
                )
            except (ValidationError, ValueError, KeyError, TypeError) as exc:
                gap_findings.append(
                    RecommendationBuilderEmissionGap(
                        failure_id=(
                            RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID
                        ),
                        corpus_id=inputs.corpus_id,
                        finding_idempotency_key=finding.idempotency_key,
                        target_consumer=consumer,
                        reason="recommendation_construction_failed",
                        evidence_payload={
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc)[:500],
                        },
                    )
                )
                continue

            recommendations.append(recommendation)

        return RecommendationBuilderResult(
            recommendations=recommendations,
            refused_findings=refused_findings,
            gap_findings=gap_findings,
        )

    def _construct_artifact(
        self,
        consumer: PolicyConsumer,
        finding: GovernanceFinding,
    ) -> (
        SchedulerPolicyArtifact
        | FailureRouterPolicyArtifact
        | SupervisorPolicyArtifact
        | DashboardPolicyArtifact
        | PlanningPolicyArtifact
        | MergeQueuePolicyArtifact
    ):
        """Dispatch to the per-consumer artifact factory for the typed
        consumer-specific artifact construction.

        Per the per-consumer factory dispatch table
        :data:`_CONSUMER_ARTIFACT_FACTORIES` the typed factory
        constructs the doc-17:107-145 consumer-specific BaseModel.
        """

        factory = _CONSUMER_ARTIFACT_FACTORIES[consumer]
        return factory(finding)

    def _construct_recommendation(
        self,
        consumer: PolicyConsumer,
        finding: GovernanceFinding,
        artifact: (
            SchedulerPolicyArtifact
            | FailureRouterPolicyArtifact
            | SupervisorPolicyArtifact
            | DashboardPolicyArtifact
            | PlanningPolicyArtifact
            | MergeQueuePolicyArtifact
        ),
    ) -> GovernancePolicyRecommendation:
        """Construct the typed
        :class:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation`
        from the typed consumer-specific artifact + the typed source
        finding.

        Per doc-17:75-97 the recommendation carries 14+ fields:
        idempotency_key + recommendation_id + consumer + status +
        source_finding_ids + source_metric_refs +
        counterfactual_result_refs + confidence + expected_impact +
        risk_level + safe_runtime_action + requires_tests +
        proposed_policy_artifact + activation_requirements +
        rollback_requirements.

        Per doc-17:166-167 the recommendation is always emitted with
        :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.status`
        = ``"draft"`` (the typed Literal range enforces this at
        construction).
        """

        affected_scope_digest = _affected_scope_digest(finding.affected_scope)
        recommendation_id = compute_recommendation_id(
            consumer=consumer,
            source_finding_idempotency_key=finding.idempotency_key,
            affected_scope_digest=affected_scope_digest,
        )
        # Source finding ids (per doc-17:80 by-name reference).
        source_finding_ids = [finding.idempotency_key]
        # Source metric refs (per doc-17:81 by-name reference); the
        # finding's metric_refs are already strings per doc-16:93.
        source_metric_refs = list(finding.metric_refs)
        # Counterfactual result refs (per doc-17:82); empty until the
        # Slice 17 5th sub-slice wires replay requirement hooks per
        # doc-17:173-174.
        counterfactual_result_refs: list[str] = []
        # Idempotency key (per doc-17:76); computed from the logical
        # inputs via the Slice 17 1st sub-slice helper.
        from iriai_build_v2.execution_control.policy_recommendation import (
            compute_policy_recommendation_idempotency_key,
        )
        idempotency_key = compute_policy_recommendation_idempotency_key(
            consumer=consumer,
            recommendation_id=recommendation_id,
            source_finding_ids=source_finding_ids,
            source_metric_refs=source_metric_refs,
            counterfactual_result_refs=counterfactual_result_refs,
        )
        # Risk level (per doc-17:85); derived from the finding's
        # severity (Slice 16 1st sub-slice
        # :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`).
        risk_level = _SEVERITY_TO_RISK_LEVEL.get(finding.severity, "low")
        # Expected impact (per doc-17:84); typed default with the
        # finding's estimated lost hours + retry impact.
        expected_impact: dict[str, float] = {}
        if finding.estimated_lost_hours is not None:
            expected_impact["estimated_lost_hours"] = float(
                finding.estimated_lost_hours
            )
        if finding.estimated_retry_impact is not None:
            expected_impact["estimated_retry_impact"] = float(
                finding.estimated_retry_impact
            )
        # Required tests (per doc-17:87); the finding's metric_refs
        # are the audit-trail surface for the validation contract.
        requires_tests = list(finding.metric_refs)
        # Activation + rollback requirements (per doc-17:96-97);
        # typed audit fields the consumer's activation artifact must
        # satisfy. This sub-slice records placeholder requirements;
        # the future Slice 17 3rd sub-slice per-consumer validation
        # interface MAY enrich these.
        activation_requirements = [
            f"consumer_validation_passes:{consumer}",
        ]
        rollback_requirements = [
            f"consumer_rollback_supported:{consumer}",
        ]
        return GovernancePolicyRecommendation(
            idempotency_key=idempotency_key,
            recommendation_id=recommendation_id,
            consumer=consumer,
            # Per doc-17:166-167 the status is ALWAYS "draft" for
            # newly-emitted recommendations; subsequent reviewer +
            # consumer-owned activation decisions change the status
            # via the Slice 17 4th sub-slice decision-record writer.
            status="draft",
            source_finding_ids=source_finding_ids,
            source_metric_refs=source_metric_refs,
            counterfactual_result_refs=counterfactual_result_refs,
            confidence=float(finding.confidence),
            expected_impact=expected_impact,
            risk_level=risk_level,
            safe_runtime_action=bool(finding.safe_runtime_action),
            requires_tests=requires_tests,
            proposed_policy_artifact=artifact,
            activation_requirements=activation_requirements,
            rollback_requirements=rollback_requirements,
        )


# --- Per-severity -> risk_level mapping -------------------------------------


_SEVERITY_TO_RISK_LEVEL: dict[str, Literal["low", "medium", "high"]] = {
    "info": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "critical": "high",
}
"""Doc-17:85 + doc-16:62 -- the per-finding-severity ->
:attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.risk_level`
mapping used by :meth:`RecommendationBuilder._construct_recommendation`.

The Slice 16 1st sub-slice
:data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
5-value Literal (``info`` / ``low`` / ``medium`` / ``high`` /
``critical`` per ``finding_engine.py:176-182``) collapses to the
doc-17:85 3-value
``risk_level: Literal["low", "medium", "high"]``.
"""
