"""Slice 16 2nd sub-slice -- governance finding rule loader + emitter.

This module is the substantive analytical engine that consumes the Slice
16 1st sub-slice typed-shape foundation (:mod:`.finding_engine`) and
emits typed :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
records via deterministic rule application. Per doc-16:155-169 §
Refactoring Steps this 2nd sub-slice lands **steps 2 + 3 + 4 + 7**:

* **Step 2** (doc-16:158) -- dedupe keys from finding class +
  feature/window + affected scope + evidence digest + rule version,
  consumed via the REUSED 1st sub-slice
  :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
  helper.
* **Step 3** (doc-16:159-161) -- at-least-one-primary canonical
  evidence-ref invariant; evidence-gap kinds explicitly allowed empty;
  the emitter projects a typed
  :class:`FindingRuleEmissionGap` rather than silently degrading per
  the auto-memory ``feedback_no_silent_degradation`` rule.
* **Step 4** (doc-16:162-163) -- product/workflow separation enforced
  via the typed ``product_defect_related`` + ``workflow_related``
  boolean flags on :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`;
  the emitter guards against ``product_defect_cluster`` kinds without
  ``workflow_related=True`` when the caller asks for a workflow-policy
  recommendation.
* **Step 7** (doc-16:168-169 + doc-16:215-217) -- suppression/expiry
  metadata via new sibling typed shapes :class:`FindingSuppressionPolicy`
  + :class:`FindingExpiryPolicy` consumed by the emitter on the rule
  version supersede path.

**Steps 5 + 6 deferred to subsequent sub-slices** (implementation-plan
deviation rules consuming Slice 13
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
+ journal/decision-log parsers in Slice 16 3rd sub-slice; scorecard
persistence + bounded review projection at
``review:governance-findings:{corpus_id}`` in Slice 16 4th sub-slice
mirroring the Slice 15 4th sub-slice
:class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
pattern).

**Non-blocking observer contract** (doc-14:242-243 inherited by every
governance projection observer). The :class:`FindingRuleEngine`
``emit_finding`` surface NEVER raises a structural failure to the
caller; any rule-application failure projects onto a typed
:class:`FindingRuleEmissionGap` accumulated on the
:attr:`FindingRuleEngine.gap_findings` property. The corresponding
typed failure id :data:`FINDING_RULE_EMISSION_FAILURE_ID`
(``finding_rule_emission_failed``) registers under the EXISTING
``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` with
the EXISTING NON-blocking RouteAction ``retry_governance_projection``
(REUSED from Slice 14 2nd sub-slice + Slice 15 2nd + 4th sub-slices;
NOT a new action).

**REUSE discipline** (doc-13a:285-287 step 9 + doc-16:201-291).

* :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding` +
  :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule` +
  :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind` +
  :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity` +
  :data:`~iriai_build_v2.execution_control.finding_engine.FindingCausalRole` +
  :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES` +
  :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
  -- IMPORTED DIRECTLY from the 1st sub-slice (NOT redefined).
* :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- IMPORTED DIRECTLY from Slice 13a (NOT redefined).

All new BaseModels carry ``model_config = ConfigDict(extra="forbid")``
per the auto-memory ``feedback_no_silent_degradation`` rule -- typo-d
kwargs raise a typed :class:`ValidationError` at construction rather
than being silently absorbed.

Per the auto-memory ``feedback_no_overengineer_use_library`` rule the
emitter mirrors the Slice 15 4th sub-slice
:class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter`
single-class-with-public-method-and-gap-accumulator precedent verbatim;
no new abstractions are introduced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from iriai_build_v2.execution_control.finding_engine import (
    REQUIRED_V1_FINDING_CLASS_NAMES,
    FindingCausalRole,
    FindingKind,
    FindingRule,
    FindingSeverity,
    GovernanceFinding,
    compute_finding_idempotency_key,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed suppression / expiry shapes (doc-16:168-169 + doc-16:215-217).
    "FindingSuppressionPolicy",
    "FindingExpiryPolicy",
    # Typed inputs / outputs (mirrors Slice 15 2nd + 4th sub-slice precedent).
    "FindingRuleEmissionInputs",
    "FindingRuleEmissionGap",
    # The NEW typed failure id under EXISTING evidence_corruption
    # failure_class (per chunk-shape point; REUSES Slice 14 2nd sub-slice
    # retry_governance_projection NON-blocking RouteAction).
    "FINDING_RULE_EMISSION_FAILURE_ID",
    # Evidence-gap kinds allowed to emit with empty primary_evidence_refs
    # per doc-16:159-161.
    "EVIDENCE_GAP_FINDING_KINDS",
    # The 16-entry v1 rule tuple built from REQUIRED_V1_FINDING_CLASS_NAMES
    # (per doc-16:120-137 + doc-16:155-156 step 1).
    "REQUIRED_V1_FINDING_RULES",
    # Loader helper (returns the tuple; mirrors Slice 15 helper precedent).
    "load_required_v1_finding_rules",
    # Class-name -> FindingKind mapping (per doc-16:122-137 + the
    # FindingKind 14-value taxonomy at doc-16:63-78).
    "CLASS_NAME_TO_FINDING_KIND",
    # The emitter class.
    "FindingRuleEngine",
]


# --- Typed failure id (doc-15:140-145 + doc-14:242-243 NON-BLOCKING) --------


FINDING_RULE_EMISSION_FAILURE_ID: Literal["finding_rule_emission_failed"] = (
    "finding_rule_emission_failed"
)
"""Doc-16 + doc-14:242-243 -- the typed failure id the finding rule
engine projects onto when a structural rule-application failure occurs.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice +
Slice 15 2nd + 4th sub-slices; NOT a new route action).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 16 pattern matches the Slice 14 + Slice 15 non-blocking
governance projection observer.

The Slice 14 + Slice 15 precedents are the source-of-truth for the
non-blocking governance-projection failure-routing discipline:

* :mod:`iriai_build_v2.execution_control.commit_provenance_writer`
  (Slice 14 2nd) defines ``line_provenance_gap`` +
  ``governance_evidence_conflict``.
* :mod:`iriai_build_v2.execution_control.governance_metric_extractor`
  (Slice 15 2nd) defines ``governance_metric_extraction_failed``.
* :mod:`iriai_build_v2.execution_control.governance_scorecard_writer`
  (Slice 15 4th) defines ``governance_scorecard_persistence_failed``.
* This module (Slice 16 2nd) defines ``finding_rule_emission_failed``.

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to governance
finding rule emission failures (this slice is also a post-checkpoint
governance projection observer).
"""


# --- Evidence-gap finding kinds (doc-16:159-161) ----------------------------


EVIDENCE_GAP_FINDING_KINDS: tuple[FindingKind, ...] = (
    # Doc-16:160-161 + doc-16:191-192 -- "Missing implementation logs:
    # emit `implementation_journal_gap` and block plan-vs-actual
    # recommendations." The provenance_gap kind covers
    # implementation_journal_gap + line_provenance_gap (per the
    # REQUIRED_V1_FINDING_CLASS_NAMES taxonomy at doc-16:133-134) where
    # the primary evidence is itself missing, so the
    # at-least-one-primary invariant cannot be satisfied by definition.
    "provenance_gap",
    # Doc-16:183-184 -- "Conflicting evidence: lower confidence and
    # emit a `governance_evidence_conflict` finding if conflict affects
    # a policy decision." Conflict findings can validly emit with empty
    # primary_evidence_refs when the conflict itself IS the gap (e.g.
    # the typed_journal disagrees with the compatibility_projection
    # over the canonical authority; either side cannot be the primary).
    "governance_evidence_conflict",
    # Doc-16:135 -- `accepted_plan_deviation` may be emitted from a
    # bare implementation-log anchor with NO primary canonical
    # evidence (the journal anchor itself is the implementation_log
    # anchor, recorded via :attr:`GovernanceFinding.implementation_log_anchors`
    # NOT via :attr:`GovernanceFinding.primary_evidence_refs`).
    "implementation_plan_deviation",
)
"""Doc-16:159-161 -- the typed tuple of :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
values that may legitimately emit a :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
with empty :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.primary_evidence_refs`.

Per doc-16:159-161 *"Every finding needs at least one primary canonical
evidence ref unless it is explicitly an evidence-gap finding."* the
3-value tuple above is the canonical evidence-gap allowance set. The
:meth:`FindingRuleEngine.emit_finding` surface enforces the
at-least-one-primary invariant for all OTHER kinds (per the
``feedback_no_silent_degradation`` rule the violation projects onto a
typed :class:`FindingRuleEmissionGap` rather than silently dropping the
finding).

The 3 kinds are:

* ``provenance_gap`` -- covers ``implementation_journal_gap`` +
  ``line_provenance_gap`` per the
  :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
  taxonomy at doc-16:133-134 (the missing implementation log / git
  note IS the gap).
* ``governance_evidence_conflict`` -- covers conflicting-evidence
  findings per doc-16:183-184 (either side cannot be primary by
  construction).
* ``implementation_plan_deviation`` -- covers ``accepted_plan_deviation``
  per doc-16:135 (journal anchor is recorded on
  ``implementation_log_anchors``, not ``primary_evidence_refs``).
"""


# --- Class-name -> FindingKind mapping (doc-16:122-137 + doc-16:63-78) ------


CLASS_NAME_TO_FINDING_KIND: dict[str, FindingKind] = {
    # Workflow drag (10) -- doc-16:122-131; the 9 workflow-related
    # FindingKind values at doc-16:64-72.
    "commit_hygiene_loop": "workflow_inefficiency",
    "acl_or_writeability_drag": "workflow_inefficiency",
    "worktree_alias_drift": "workflow_inefficiency",
    "stale_context_projection": "stale_projection",
    "runtime_provider_instability": "runtime_instability",
    "merge_queue_wait_or_retry_drag": "merge_queue_drag",
    "over_verification_low_risk_lane": "over_verification",
    "under_verification_high_risk_lane": "under_verification",
    "scheduler_wave_too_small": "scheduler_mismatch",
    "scheduler_wave_too_large": "scheduler_mismatch",
    # Task contract weakness (1) -- doc-16:132.
    "task_contract_ambiguity": "task_contract_weakness",
    # Provenance / evidence gaps (3) -- doc-16:133-134, 137 ->
    # FindingKind ``provenance_gap`` + ``governance_evidence_conflict``
    # per doc-16:73, 77.
    "line_provenance_gap": "provenance_gap",
    "implementation_journal_gap": "provenance_gap",
    "governance_evidence_conflict": "governance_evidence_conflict",
    # Implementation drift (1) -- doc-16:135.
    "accepted_plan_deviation": "implementation_plan_deviation",
    # Resource safety (1) -- doc-16:136.
    "resource_budget_pressure": "resource_safety_risk",
}
"""Doc-16:122-137 + doc-16:63-78 -- the typed mapping from each
:data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
entry to its corresponding :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`.

This mapping is the typed surface the :data:`REQUIRED_V1_FINDING_RULES`
tuple grounds on. Each of the 16 canonical class names maps to exactly
one of the 14 FindingKind values per doc-16:63-78 (multiple class
names may share a kind, e.g. both
``scheduler_wave_too_small`` and ``scheduler_wave_too_large`` map to
``scheduler_mismatch``).

Per doc-16:155-156 step 1 *"Convert existing process-improvement logic
into versioned finding rules after the governance evidence and metric
layers exist."* this mapping is the cross-version v1 contract subsequent
sub-slices ground their rule loader on. Per doc-16:215-217 future rule
versions (``v2`` / ``v3`` / etc.) MAY tighten / split the mapping
(e.g. distinguishing ``scheduler_wave_too_small`` -> a new
``scheduler_wave_too_small`` FindingKind value) provided the new kinds
are added to the 14-value Literal at doc-16:63-78 first.

The 5 evidence-gap class names that emit one of the 3
:data:`EVIDENCE_GAP_FINDING_KINDS` kinds are:

* ``line_provenance_gap`` + ``implementation_journal_gap`` ->
  ``provenance_gap``.
* ``governance_evidence_conflict`` -> ``governance_evidence_conflict``.
* ``accepted_plan_deviation`` -> ``implementation_plan_deviation``.
"""


# --- Suppression + expiry typed shapes (doc-16:168-169 + doc-16:215-217) ----


class FindingSuppressionPolicy(BaseModel):
    """Doc-16:168-169 + doc-16:215-217 -- typed suppression policy.

    Per doc-16:168-169 *"Add suppression/expiry metadata so old findings
    do not keep driving future recommendations after the underlying
    policy changes."* the suppression policy carries the typed contract
    the rule engine consumes when deciding whether to suppress a rule's
    emission. Per doc-16:215-217 *"If a finding rule is bad, release a
    new rule version and mark prior findings superseded rather than
    rewriting history."* the suppression policy ALSO covers the
    rule-version supersede path -- a suppression policy can target a
    specific :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.rule_id`
    + :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.version`
    pair to mark all findings emitted by that rule version as
    superseded.

    The suppression policy is NON-mutating: it does NOT rewrite
    historical findings. The :class:`FindingRuleEngine` consumes the
    policy at emission time and SKIPS the emission (projecting onto a
    typed :class:`FindingRuleEmissionGap` so the caller can audit the
    suppression) rather than emitting a finding that would be
    immediately suppressed.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    """The :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.rule_id`
    this policy targets. The :class:`FindingRuleEngine` matches the
    policy against the rule at emission time."""

    rule_version: str
    """The :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.version`
    this policy targets. Per doc-16:215-217 a new rule version produces
    a DISTINCT dedupe key (per :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`)
    so historical findings under the prior version remain auditable; the
    suppression policy SUPERSEDES the prior version by suppressing its
    future emissions."""

    suppression_reason: str
    """Free-form reason string (e.g.
    ``"superseded_by_v2"`` / ``"false_positive_on_legacy_corpus"`` /
    ``"policy_no_longer_active"``). The reason is carried on the typed
    :class:`FindingRuleEmissionGap` for audit purposes; it is NOT a
    Literal so callers can carry rich free-form context."""

    suppressed_at: datetime
    """The timestamp at which the suppression took effect; per doc-16:215-217
    the timestamp is the audit anchor that lets downstream consumers see
    when the rule version was retired. Per the Pydantic v2 + Slice 13a
    +Slice 13A + Slice 14 + Slice 15 precedent the datetime is timezone-
    aware (UTC) so cross-process serialisation is stable."""


class FindingExpiryPolicy(BaseModel):
    """Doc-16:168-169 + doc-16:215-217 -- typed expiry policy.

    Per doc-16:168-169 *"so old findings do not keep driving future
    recommendations after the underlying policy changes."* the expiry
    policy carries the typed contract the rule engine consumes when
    deciding whether a finding has expired (no longer authoritative for
    downstream recommendation consumption). Per doc-16:215-217 the
    expiry policy + suppression policy together implement the rule
    version supersede path.

    The expiry policy is NON-mutating: it does NOT rewrite historical
    findings. The :class:`FindingRuleEngine` consumes the policy at
    emission time and SKIPS the emission (projecting onto a typed
    :class:`FindingRuleEmissionGap` so the caller can audit the expiry)
    when the current time is past :attr:`expires_at`.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    :class:`ValidationError` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    """The :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.rule_id`
    this policy targets."""

    rule_version: str
    """The :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.version`
    this policy targets."""

    expires_at: datetime
    """The timestamp at which the rule version's findings expire. Per
    doc-16:168-169 expiry is the typed knob that prevents stale findings
    from driving future recommendations after the underlying policy
    changes; the :class:`FindingRuleEngine` skips emission when the
    current time is past this value. Per the Pydantic v2 + Slice 13a +
    Slice 13A + Slice 14 + Slice 15 precedent the datetime is timezone-
    aware (UTC) so cross-process comparison is stable."""

    expiry_reason: str = ""
    """Optional free-form reason string (e.g.
    ``"policy_window_expired"`` / ``"corpus_archived"``). Default empty
    string per the typed default contract (the expiry datetime is the
    primary contract; the reason is an audit-only annotation)."""

    def is_expired(self, *, now: datetime | None = None) -> bool:
        """Return ``True`` when ``now >= expires_at`` (default ``now``
        is :func:`datetime.now` in UTC).

        Per doc-16:168-169 this helper is the typed equality the
        :class:`FindingRuleEngine` uses at emission time. The default
        ``now=None`` resolves to :func:`datetime.now` with UTC tz so
        cross-process comparison stays stable.
        """

        if now is None:
            now = datetime.now(timezone.utc)
        return now >= self.expires_at


# --- Typed inputs / outputs (mirrors Slice 15 4th sub-slice precedent) ------


class FindingRuleEmissionInputs(BaseModel):
    """Typed bundle of all inputs the :meth:`FindingRuleEngine.emit_finding`
    surface consumes per doc-16:155-169 § Refactoring Steps 2 + 3 + 4.

    The bundle composes:

    * ``rule`` -- the :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
      from the v1 rule loader (or a future versioned rule) that the
      emitter applies. The rule's :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.version`
      is carried on the emitted finding's idempotency key per doc-16:158.
    * ``class_name`` -- the canonical fine-grained class name from the
      :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
      tuple (or a future-version superset). The class_name is what the
      downstream Slice 17 policy layer routes on.
    * ``severity`` -- the typed :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
      from the rule application.
    * ``confidence`` -- the typed float in ``[0.0, 1.0]``; the
      emitter compares against
      :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.min_confidence`
      and projects onto a typed gap finding when below threshold (per
      doc-16:193 + the auto-memory ``feedback_no_silent_degradation``
      rule).
    * ``feature_id`` -- the feature scope (``None`` for cross-feature
      findings).
    * ``affected_scope`` -- the scope-dimensions dict (per
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`).
    * ``primary_evidence_refs`` -- the list of Slice 13a typed evidence
      refs the emitter consumes for the at-least-one-primary invariant
      per doc-16:159-161.
    * ``supporting_evidence_refs`` -- the list of supporting evidence
      refs (validly empty per the 1st sub-slice typed surface).
    * ``implementation_log_anchors`` -- the list of journal anchor
      strings (per doc-16:164-165; defaults to empty).
    * ``metric_refs`` -- the list of metric name strings (per
      doc-16:93).
    * ``recommended_action_display`` -- the non-executable display text
      (per doc-16:115-118 the text is for human consumption only;
      runtime/workflow consumers MUST ignore it for policy changes).
    * ``safe_runtime_action`` -- the typed boolean per
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.safe_runtime_action`.
    * ``requires_policy_artifact`` -- the typed boolean per
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.requires_policy_artifact`.
    * ``product_defect_related`` + ``workflow_related`` -- the typed
      booleans per doc-16:162-163 product/workflow separation.
    * ``causal_role`` -- the typed
      :data:`~iriai_build_v2.execution_control.finding_engine.FindingCausalRole`.
    * Optional ``estimated_lost_hours`` + ``estimated_retry_impact``
      + ``recommendation_draft_ref`` + ``primary_cause_finding_id`` +
      ``linked_finding_ids`` -- propagated to the emitted
      :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
      verbatim.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via
    ``ConfigDict(extra="forbid")``.
    """

    model_config = ConfigDict(extra="forbid")

    rule: FindingRule
    """The typed :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
    the emitter applies. The rule's
    :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.version`
    is carried on the emitted finding's idempotency key per doc-16:158."""

    class_name: str
    """The canonical fine-grained class name (per
    :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
    or a future-version superset)."""

    severity: FindingSeverity
    """The typed :data:`~iriai_build_v2.execution_control.finding_engine.FindingSeverity`
    from rule application."""

    confidence: float = Field(ge=0.0, le=1.0)
    """The typed float in ``[0.0, 1.0]``. The emitter compares against
    :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.min_confidence`
    per doc-16:193."""

    feature_id: str | None = None
    """The feature scope (``None`` for cross-feature findings)."""

    affected_scope: dict[str, Any] = Field(default_factory=dict)
    """The scope-dimensions dict per
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.affected_scope`."""

    primary_evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """The list of Slice 13a typed evidence refs for the
    at-least-one-primary invariant per doc-16:159-161. Default empty
    list per the 1st sub-slice surface; the emitter enforces the
    at-least-one-primary invariant for non-gap kinds."""

    supporting_evidence_refs: list[GovernanceEvidenceRef] = Field(default_factory=list)
    """The list of supporting evidence refs (validly empty per the 1st
    sub-slice typed surface)."""

    implementation_log_anchors: list[str] = Field(default_factory=list)
    """The list of journal anchor strings per doc-16:164-165."""

    metric_refs: list[str] = Field(default_factory=list)
    """The list of metric name strings per doc-16:93 (names, NOT typed
    Slice 15 :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    references)."""

    estimated_lost_hours: float | None = None
    """Optional propagation to
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.estimated_lost_hours`."""

    estimated_retry_impact: float | None = None
    """Optional propagation to
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.estimated_retry_impact`."""

    recommended_action_display: str
    """The non-executable display text per doc-16:115-118."""

    recommendation_draft_ref: str | None = None
    """Optional propagation to
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.recommendation_draft_ref`."""

    safe_runtime_action: bool
    """The typed boolean per
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.safe_runtime_action`."""

    requires_policy_artifact: bool
    """The typed boolean per
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.requires_policy_artifact`."""

    product_defect_related: bool
    """The typed boolean per doc-16:162-163 product/workflow separation."""

    workflow_related: bool
    """The typed boolean per doc-16:162-163 product/workflow separation."""

    causal_role: FindingCausalRole
    """The typed :data:`~iriai_build_v2.execution_control.finding_engine.FindingCausalRole`."""

    primary_cause_finding_id: str | None = None
    """Optional propagation to
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.primary_cause_finding_id`."""

    linked_finding_ids: list[str] = Field(default_factory=list)
    """Optional propagation to
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.linked_finding_ids`."""


class FindingRuleEmissionGap(BaseModel):
    """Typed governance-gap finding produced when the
    :meth:`FindingRuleEngine.emit_finding` surface fails to emit a
    finding structurally (e.g. at-least-one-primary invariant
    violation; product/workflow separation violation; suppressed /
    expired rule; below-threshold confidence; construction failure).

    Mirrors the Slice 14
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding`
    + Slice 15 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.governance_metric_extractor.GovernanceMetricExtractionGap`
    + Slice 15 4th sub-slice
    :class:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardPersistenceGap`
    shapes verbatim per the chunk-shape contract.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED here
    per doc-15:140-145 + doc-16 governance-projection discipline) the
    gap finding is NON-blocking: the caller MUST NOT propagate it to
    the executor / checkpoint / merge-queue / resume code paths. The
    corresponding typed failure id :data:`FINDING_RULE_EMISSION_FAILURE_ID`
    (``finding_rule_emission_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
    NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["finding_rule_emission_failed"]
    """Doc-16 + doc-14:192-201 -- the typed failure id. Registers under
    the EXISTING ``evidence_corruption`` failure_class with NON-blocking
    routing per doc-14:242-243."""

    rule_id: str
    """The :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.rule_id`
    that failed to emit. The id lets downstream consumers correlate the
    gap with the rule version that produced it."""

    rule_version: str
    """The :attr:`~iriai_build_v2.execution_control.finding_engine.FindingRule.version`
    that failed to emit. Per doc-16:215-217 the rule version is the
    audit anchor for the rule version supersede path."""

    class_name: str
    """The canonical fine-grained class name (per
    :class:`FindingRuleEmissionInputs.class_name`) that failed to emit."""

    attempted_idempotency_key: str | None
    """The deterministic idempotency_key (per
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`)
    the emitter computed BEFORE the failure was detected, or ``None``
    when the failure happened pre-computation (e.g. construction
    failure). Preserving the key lets downstream consumers correlate
    the gap with the historical finding (if any) that would have been
    produced."""

    reason: str
    """Free-form gap reason naming the specific failure mode (e.g.
    ``"at_least_one_primary_invariant_violated"`` /
    ``"product_workflow_separation_violated"`` /
    ``"suppressed_by_policy"`` /
    ``"expired_by_policy"`` /
    ``"confidence_below_min_threshold"`` /
    ``"construction_failed"``)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the suppression policy reason, the threshold the confidence missed,
    the construction error type). Free-form per the doc-14:192-201 +
    doc-15 + doc-16 governance-finding contract."""


# --- 16-entry v1 rule tuple (doc-16:120-137 + doc-16:155-156 step 1) --------


def _build_required_v1_finding_rules() -> tuple[FindingRule, ...]:
    """Build the canonical 16-entry typed rule tuple from the 1st
    sub-slice :data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
    contract + the :data:`CLASS_NAME_TO_FINDING_KIND` mapping.

    Per doc-16:155-156 step 1 *"Convert existing process-improvement
    logic into versioned finding rules after the governance evidence
    and metric layers exist."* the rule tuple is the v1 cross-version
    contract; subsequent rule versions (``v2`` / ``v3``) MAY add /
    tighten rules while preserving the v1 16-rule set per
    doc-16:215-217.

    Each rule carries the conservative v1 calibration:

    * ``rule_id`` = ``"{class_name}_v1"`` -- the stable rule identifier.
    * ``version`` = ``"v1"`` -- the typed version per doc-16:108.
    * ``required_metric_names`` = empty list -- v1 rules do NOT
      enforce a specific metric ref requirement (the metric refs are
      advisory on the emitted finding via
      :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.metric_refs`);
      subsequent rule versions MAY tighten by adding required metric
      names from the Slice 15
      :data:`~iriai_build_v2.execution_control.governance_metrics.REQUIRED_V1_METRIC_NAMES`
      tuple.
    * ``required_evidence_kinds`` = empty list -- v1 rules do NOT
      enforce a specific evidence-authority requirement (the
      at-least-one-primary invariant lives in the emitter per
      doc-16:159-161); subsequent rule versions MAY tighten by adding
      required Slice 13a
      :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceAuthority`
      values.
    * ``min_confidence`` = ``0.5`` -- the conservative v1 default per
      doc-16:111-112 (findings below threshold are reported but cannot
      feed policy recommendations per doc-16:193); subsequent versions
      MAY tighten per-rule per measured-corpus calibration.
    * ``emits_kind`` = the typed FindingKind from
      :data:`CLASS_NAME_TO_FINDING_KIND` (per doc-16:122-137 +
      doc-16:63-78).

    The function is INTERNAL (per the module ``__all__`` only the
    pre-built tuple :data:`REQUIRED_V1_FINDING_RULES` is exported);
    callers consume the tuple via :func:`load_required_v1_finding_rules`
    or the direct module attribute access.
    """

    rules: list[FindingRule] = []
    for class_name in REQUIRED_V1_FINDING_CLASS_NAMES:
        kind = CLASS_NAME_TO_FINDING_KIND[class_name]
        rules.append(
            FindingRule(
                rule_id=f"{class_name}_v1",
                version="v1",
                required_metric_names=[],
                required_evidence_kinds=[],
                min_confidence=0.5,
                emits_kind=kind,
            )
        )
    return tuple(rules)


REQUIRED_V1_FINDING_RULES: tuple[FindingRule, ...] = _build_required_v1_finding_rules()
"""Doc-16:120-137 + doc-16:155-156 step 1 -- the canonical 16-entry
typed rule tuple subsequent Slice 16 sub-slices ground the rule loader
+ emitter on.

Each entry is a :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
instance built from the 1st sub-slice
:data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
16-name tuple + the :data:`CLASS_NAME_TO_FINDING_KIND` mapping. Per
doc-16:215-217 future rule versions emit DISTINCT typed
:class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
instances (with new ``version`` strings) so the rule version supersede
path produces distinct dedupe keys via
:func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`.

The 16 entries align 1:1 with
:data:`~iriai_build_v2.execution_control.finding_engine.REQUIRED_V1_FINDING_CLASS_NAMES`
(same order; same class name suffix on each ``rule_id``).
"""


def load_required_v1_finding_rules() -> tuple[FindingRule, ...]:
    """Return the canonical 16-entry typed v1 rule tuple (per
    :data:`REQUIRED_V1_FINDING_RULES`).

    This helper is the stable typed-loader surface subsequent Slice 16
    sub-slices ground their rule-engine wiring on. Per doc-16:155-156
    step 1 *"Convert existing process-improvement logic into versioned
    finding rules after the governance evidence and metric layers
    exist."* the loader is the typed-loader contract.

    Returns the tuple BY IDENTITY (the same tuple object on every call;
    the underlying :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
    instances are immutable). The helper is the typed-loader surface
    that mirrors the Slice 15
    :func:`~iriai_build_v2.execution_control.governance_scorecard_writer.compute_review_projection_id`
    helper precedent (pure function; no side effects).
    """

    return REQUIRED_V1_FINDING_RULES


# --- The finding rule engine (chunk-shape point) ----------------------------


class FindingRuleEngine:
    """Governance finding rule engine + emitter (doc-16:155-169 §
    Refactoring Steps 2 + 3 + 4 + 7).

    Per *"Convert existing process-improvement logic into versioned
    finding rules after the governance evidence and metric layers
    exist."* (doc-16:155-156) the engine consumes:

    1. A typed :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
       from the v1 rule loader (or a future versioned rule) -- the
       typed rule shape pins the dedupe-key version per doc-16:158.
    2. A typed :class:`FindingRuleEmissionInputs` bundle carrying the
       rule application's typed outputs (severity / confidence /
       evidence refs / scope / metric refs / etc.).
    3. Optional :class:`FindingSuppressionPolicy` +
       :class:`FindingExpiryPolicy` collections per doc-16:168-169 +
       doc-16:215-217 -- the typed knobs for the rule version
       supersede path.

    And projects them onto:

    1. A typed :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
       with a deterministic
       :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
       computed via
       :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
       (the 1st sub-slice helper).
    2. Or, on rule-application failure, a typed
       :class:`FindingRuleEmissionGap` accumulated on
       :attr:`gap_findings`.

    **At-least-one-primary invariant** (doc-16:159-161). For every kind
    OUTSIDE :data:`EVIDENCE_GAP_FINDING_KINDS` the emitter requires
    ``len(inputs.primary_evidence_refs) >= 1``; a violation projects
    onto a typed :class:`FindingRuleEmissionGap` with
    ``reason="at_least_one_primary_invariant_violated"`` per the
    auto-memory ``feedback_no_silent_degradation`` rule (NOT silently
    dropped).

    **Product/workflow separation** (doc-16:162-163). The emitter
    enforces:

    * If the rule's ``emits_kind`` is ``"product_defect_cluster"`` the
      emitter requires ``inputs.product_defect_related=True`` (a
      violation projects onto a typed gap finding).
    * If the inputs ask for a workflow-policy recommendation
      (``inputs.requires_policy_artifact=True``) AND the
      ``emits_kind`` is ``"product_defect_cluster"`` the emitter
      requires ``inputs.workflow_related=True`` (per doc-16:163
      *"workflow policy recommendations must cite workflow-related
      causes"*); a violation projects onto a typed gap finding.

    **Suppression / expiry** (doc-16:168-169 + doc-16:215-217). The
    emitter consumes the optional
    :class:`FindingSuppressionPolicy` + :class:`FindingExpiryPolicy`
    collections at emission time. A match (same ``rule_id`` +
    ``rule_version``) SKIPS the emission and projects onto a typed
    :class:`FindingRuleEmissionGap` with
    ``reason="suppressed_by_policy"`` or ``"expired_by_policy"`` so
    the caller can audit the supersede path.

    **Confidence threshold** (doc-16:111-112 + doc-16:193). The emitter
    requires ``inputs.confidence >= rule.min_confidence``; a violation
    projects onto a typed gap finding with
    ``reason="confidence_below_min_threshold"`` per doc-16:193 *"Low
    confidence: findings may be reported but cannot feed policy
    recommendations."* -- the engine still emits the finding when
    ``inputs.requires_policy_artifact=False`` (i.e. advisory mode);
    the gap-finding pathway only fires when
    ``inputs.requires_policy_artifact=True`` and the threshold is missed
    (so policy recommendations cannot be silently fed by below-threshold
    findings).

    **Non-blocking observer contract** (doc-14:242-243 inherited via
    Slice 15 2nd + 4th sub-slice precedent). The engine NEVER raises a
    structural failure to the caller; every failure projects onto a
    typed :class:`FindingRuleEmissionGap`.

    **Per-call accumulator reset** (mirrors Slice 15 2nd + 4th sub-slice
    pattern). Each call to :meth:`emit_finding` RESETS the
    :attr:`gap_findings` accumulator so per-call gap findings remain
    bounded; callers that need cross-call accumulation should snapshot
    the property after each call.

    Example usage::

        from iriai_build_v2.execution_control.finding_rule_engine \\
            import FindingRuleEngine, FindingRuleEmissionInputs, \\
                   load_required_v1_finding_rules

        engine = FindingRuleEngine()
        rules = load_required_v1_finding_rules()
        for rule in rules:
            inputs = FindingRuleEmissionInputs(
                rule=rule,
                class_name="commit_hygiene_loop",
                severity="medium",
                confidence=0.85,
                feature_id="8ac124d6",
                affected_scope={"lane": "high_risk"},
                primary_evidence_refs=[some_evidence_ref],
                supporting_evidence_refs=[],
                implementation_log_anchors=[],
                metric_refs=["commit_failures_per_task"],
                recommended_action_display="Tighten commit retry budget.",
                safe_runtime_action=False,
                requires_policy_artifact=True,
                product_defect_related=False,
                workflow_related=True,
                causal_role="primary",
            )
            finding = engine.emit_finding(inputs)
            if finding is not None:
                # finding emitted; caller persists it.
                ...
            for gap in engine.gap_findings:
                # rule failed to emit; caller logs the gap.
                ...
    """

    def __init__(
        self,
        *,
        suppression_policies: list[FindingSuppressionPolicy] | None = None,
        expiry_policies: list[FindingExpiryPolicy] | None = None,
    ) -> None:
        """Construct a finding rule engine.

        :param suppression_policies: optional list of typed
            :class:`FindingSuppressionPolicy` entries the engine
            consumes at emission time per doc-16:168-169. A match
            (same ``rule_id`` + ``rule_version``) SKIPS the emission
            and projects onto a typed :class:`FindingRuleEmissionGap`
            with ``reason="suppressed_by_policy"``. Default ``None``
            (no suppression).
        :param expiry_policies: optional list of typed
            :class:`FindingExpiryPolicy` entries the engine consumes
            at emission time per doc-16:168-169. A match with
            :meth:`FindingExpiryPolicy.is_expired` returning ``True``
            SKIPS the emission and projects onto a typed gap finding
            with ``reason="expired_by_policy"``. Default ``None`` (no
            expiry).

        The engine is stateless aside from the :attr:`gap_findings`
        accumulator the :meth:`emit_finding` surface populates. Each
        call RESETS the accumulator per the Slice 15 2nd + 4th sub-slice
        precedent.
        """

        self._suppression_policies: list[FindingSuppressionPolicy] = list(
            suppression_policies or []
        )
        self._expiry_policies: list[FindingExpiryPolicy] = list(expiry_policies or [])
        self._gap_findings: list[FindingRuleEmissionGap] = []

    @property
    def gap_findings(self) -> list[FindingRuleEmissionGap]:
        """The list of :class:`FindingRuleEmissionGap` findings the
        most-recent :meth:`emit_finding` call produced.

        Per the Slice 14 + Slice 15 2nd + 4th sub-slice precedents the
        engine NEVER raises a failure to the caller -- every structural
        failure projects onto a typed gap finding.
        """

        return list(self._gap_findings)

    @property
    def suppression_policies(self) -> list[FindingSuppressionPolicy]:
        """The configured suppression policies (read-only view; the
        engine consumes the policies at emission time per doc-16:168-169).
        """

        return list(self._suppression_policies)

    @property
    def expiry_policies(self) -> list[FindingExpiryPolicy]:
        """The configured expiry policies (read-only view; the engine
        consumes the policies at emission time per doc-16:168-169).
        """

        return list(self._expiry_policies)

    def emit_finding(
        self,
        inputs: FindingRuleEmissionInputs,
        *,
        now: datetime | None = None,
    ) -> GovernanceFinding | None:
        """Apply ``inputs.rule`` to ``inputs`` and return either a
        typed :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
        (on successful emission) or ``None`` (on suppressed / expired /
        invariant-violation / construction-failure projection -- the
        gap finding is accumulated on :attr:`gap_findings`).

        Per doc-16:158 the deterministic dedupe key is computed via
        :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
        over the 6 logical inputs (kind / class_name / feature_id /
        affected_scope / primary evidence digests / rule_version) so
        re-emits with identical inputs produce identical keys per
        doc-16:178.

        Per doc-14:242-243 NEVER raises a failure to the caller. Any
        structural failure projects onto a typed
        :class:`FindingRuleEmissionGap` accumulated on
        :attr:`gap_findings`.

        The method RESETS the :attr:`gap_findings` accumulator at
        entry; per-call gap findings remain bounded.

        :param inputs: the typed input bundle.
        :param now: optional datetime override for the expiry check
            (defaults to :func:`datetime.now` in UTC). Test-only
            seam; production callers leave ``None`` so the engine
            uses live clock time.
        """

        # Reset per-call accumulator (mirrors Slice 15 2nd + 4th sub-slice
        # MetricExtractor / ScorecardWriter pattern).
        self._gap_findings = []

        rule = inputs.rule
        emits_kind = rule.emits_kind

        # ── 1. Suppression policy check (doc-16:168-169 + doc-16:215-217) ──
        suppression = self._lookup_suppression(rule.rule_id, rule.version)
        if suppression is not None:
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    class_name=inputs.class_name,
                    attempted_idempotency_key=None,
                    reason="suppressed_by_policy",
                    evidence_payload={
                        "suppression_reason": suppression.suppression_reason,
                        "suppressed_at": suppression.suppressed_at.isoformat(),
                    },
                )
            )
            return None

        # ── 2. Expiry policy check (doc-16:168-169 + doc-16:215-217) ──
        expiry = self._lookup_expiry(rule.rule_id, rule.version)
        if expiry is not None and expiry.is_expired(now=now):
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    class_name=inputs.class_name,
                    attempted_idempotency_key=None,
                    reason="expired_by_policy",
                    evidence_payload={
                        "expiry_reason": expiry.expiry_reason,
                        "expires_at": expiry.expires_at.isoformat(),
                    },
                )
            )
            return None

        # ── 3. At-least-one-primary invariant (doc-16:159-161) ──
        # Per the auto-memory feedback_no_silent_degradation rule we
        # emit a typed gap finding rather than silently dropping the
        # emission. Per doc-16:160-161 the EVIDENCE_GAP_FINDING_KINDS
        # tuple lists the kinds explicitly allowed empty.
        if (
            emits_kind not in EVIDENCE_GAP_FINDING_KINDS
            and len(inputs.primary_evidence_refs) == 0
        ):
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    class_name=inputs.class_name,
                    attempted_idempotency_key=None,
                    reason="at_least_one_primary_invariant_violated",
                    evidence_payload={
                        "emits_kind": emits_kind,
                        "primary_evidence_ref_count": 0,
                        "evidence_gap_kinds": list(EVIDENCE_GAP_FINDING_KINDS),
                    },
                )
            )
            return None

        # ── 4. Product/workflow separation (doc-16:162-163) ──
        # Per doc-16:163 "workflow policy recommendations must cite
        # workflow-related causes." We require:
        # (a) product_defect_cluster kinds MUST carry product_defect_related=True
        # (the typed flag is the assertion the caller has identified the
        # finding as a product defect).
        # (b) If the caller asks for a policy artifact (workflow policy
        # recommendation) AND the kind is product_defect_cluster, the
        # caller MUST also assert workflow_related=True (i.e. the product
        # defect has a workflow-related cause that justifies the
        # workflow policy recommendation).
        if emits_kind == "product_defect_cluster":
            if not inputs.product_defect_related:
                self._gap_findings.append(
                    FindingRuleEmissionGap(
                        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                        rule_id=rule.rule_id,
                        rule_version=rule.version,
                        class_name=inputs.class_name,
                        attempted_idempotency_key=None,
                        reason="product_workflow_separation_violated",
                        evidence_payload={
                            "violation": "product_defect_cluster_without_product_defect_related",
                            "emits_kind": emits_kind,
                            "product_defect_related": False,
                        },
                    )
                )
                return None
            if inputs.requires_policy_artifact and not inputs.workflow_related:
                self._gap_findings.append(
                    FindingRuleEmissionGap(
                        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                        rule_id=rule.rule_id,
                        rule_version=rule.version,
                        class_name=inputs.class_name,
                        attempted_idempotency_key=None,
                        reason="product_workflow_separation_violated",
                        evidence_payload={
                            "violation": "workflow_policy_without_workflow_related_cause",
                            "emits_kind": emits_kind,
                            "requires_policy_artifact": True,
                            "workflow_related": False,
                        },
                    )
                )
                return None

        # ── 5. Confidence threshold (doc-16:111-112 + doc-16:193) ──
        # Per doc-16:193 "Low confidence: findings may be reported but
        # cannot feed policy recommendations." We emit a typed gap
        # finding ONLY when the caller asks for a policy artifact and
        # the confidence is below threshold (so the gap signals "you
        # asked for a policy artifact but the confidence is too low"
        # without silently dropping the finding). For advisory-only
        # emissions (requires_policy_artifact=False) we proceed to emit
        # the typed finding even when below threshold so the caller
        # still sees the advisory signal.
        if (
            inputs.requires_policy_artifact
            and inputs.confidence < rule.min_confidence
        ):
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    class_name=inputs.class_name,
                    attempted_idempotency_key=None,
                    reason="confidence_below_min_threshold",
                    evidence_payload={
                        "confidence": inputs.confidence,
                        "min_confidence": rule.min_confidence,
                        "requires_policy_artifact": True,
                    },
                )
            )
            return None

        # ── 6. Compute the deterministic idempotency_key (doc-16:158) ──
        # Per doc-16:158 the key is computed over the 6 logical inputs
        # (kind / class_name / feature_id / affected_scope / primary
        # evidence digests / rule_version) so re-emits with identical
        # inputs produce identical keys per doc-16:178.
        primary_evidence_digests = [
            ref.digest for ref in inputs.primary_evidence_refs
        ]
        try:
            idempotency_key = compute_finding_idempotency_key(
                kind=emits_kind,
                class_name=inputs.class_name,
                feature_id=inputs.feature_id,
                affected_scope=inputs.affected_scope,
                primary_evidence_digests=primary_evidence_digests,
                rule_version=rule.version,
            )
        except Exception as exc:  # pragma: no cover -- defence-in-depth
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    class_name=inputs.class_name,
                    attempted_idempotency_key=None,
                    reason="idempotency_key_computation_failed",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            )
            return None

        # ── 7. Construct the typed finding ──
        try:
            finding = GovernanceFinding(
                idempotency_key=idempotency_key,
                kind=emits_kind,
                class_name=inputs.class_name,
                severity=inputs.severity,
                confidence=inputs.confidence,
                feature_id=inputs.feature_id,
                affected_scope=dict(inputs.affected_scope),
                primary_evidence_refs=list(inputs.primary_evidence_refs),
                supporting_evidence_refs=list(inputs.supporting_evidence_refs),
                implementation_log_anchors=list(inputs.implementation_log_anchors),
                metric_refs=list(inputs.metric_refs),
                estimated_lost_hours=inputs.estimated_lost_hours,
                estimated_retry_impact=inputs.estimated_retry_impact,
                recommended_action_display=inputs.recommended_action_display,
                recommendation_draft_ref=inputs.recommendation_draft_ref,
                safe_runtime_action=inputs.safe_runtime_action,
                requires_policy_artifact=inputs.requires_policy_artifact,
                product_defect_related=inputs.product_defect_related,
                workflow_related=inputs.workflow_related,
                causal_role=inputs.causal_role,
                primary_cause_finding_id=inputs.primary_cause_finding_id,
                linked_finding_ids=list(inputs.linked_finding_ids),
            )
        except Exception as exc:  # pragma: no cover -- defence-in-depth
            # Per doc-14:242-243 the engine NEVER raises a failure to
            # the caller; even a structural construction failure (which
            # should never happen given the typed inputs) projects onto
            # a typed gap finding.
            self._gap_findings.append(
                FindingRuleEmissionGap(
                    failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
                    rule_id=rule.rule_id,
                    rule_version=rule.version,
                    class_name=inputs.class_name,
                    attempted_idempotency_key=idempotency_key,
                    reason="construction_failed",
                    evidence_payload={
                        "error_type": type(exc).__name__,
                        "error_detail": str(exc)[:500],
                    },
                )
            )
            return None

        return finding

    # ── private helpers ─────────────────────────────────────────────────────

    def _lookup_suppression(
        self,
        rule_id: str,
        rule_version: str,
    ) -> FindingSuppressionPolicy | None:
        """Return the first matching :class:`FindingSuppressionPolicy`
        for ``(rule_id, rule_version)`` or ``None`` when no match.

        Per doc-16:168-169 + doc-16:215-217 the suppression policy
        matches on the exact ``rule_id`` + ``rule_version`` pair so the
        supersede path is version-precise (a v1 suppression does NOT
        affect v2 emissions; per doc-16:215-217 the rule version is the
        typed knob that distinguishes superseded findings).
        """

        for policy in self._suppression_policies:
            if policy.rule_id == rule_id and policy.rule_version == rule_version:
                return policy
        return None

    def _lookup_expiry(
        self,
        rule_id: str,
        rule_version: str,
    ) -> FindingExpiryPolicy | None:
        """Return the first matching :class:`FindingExpiryPolicy` for
        ``(rule_id, rule_version)`` or ``None`` when no match.

        Per doc-16:168-169 the expiry policy matches on the exact
        ``rule_id`` + ``rule_version`` pair so the supersede path is
        version-precise.
        """

        for policy in self._expiry_policies:
            if policy.rule_id == rule_id and policy.rule_version == rule_version:
                return policy
        return None
