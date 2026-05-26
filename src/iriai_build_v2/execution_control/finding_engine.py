"""Slice 16 first sub-slice -- foundational governance finding engine + taxonomy typed-shape module.

This module owns the doc-16 "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/16-finding-engine-and-taxonomy.md:62-113``):

* :data:`FindingSeverity` -- 5-value Literal alias (doc-16:62): the
  severity ladder a governance finding declares. Values: ``info`` /
  ``low`` / ``medium`` / ``high`` / ``critical``.
* :data:`FindingKind` -- 14-value Literal alias (doc-16:63-78): the
  kind taxonomy a governance finding declares. Spans workflow-related
  drag (``workflow_inefficiency`` / ``unsafe_route`` /
  ``stale_projection`` / ``over_verification`` / ``under_verification`` /
  ``task_contract_weakness`` / ``scheduler_mismatch`` /
  ``runtime_instability`` / ``merge_queue_drag``), evidence/provenance
  gaps (``provenance_gap`` / ``governance_evidence_conflict``),
  implementation drift (``implementation_plan_deviation``),
  resource/safety risk (``resource_safety_risk``), and product defects
  (``product_defect_cluster``).
* :data:`FindingCausalRole` -- 4-value Literal alias (doc-16:80): the
  causal role a finding plays in its corpus. Values: ``primary`` /
  ``contributing`` / ``symptom`` / ``unknown``.
* :class:`GovernanceFinding` -- the 19+ field finding record shape
  (doc-16:82-104): idempotency_key + kind + class_name + severity +
  confidence + feature_id + affected_scope + primary_evidence_refs +
  supporting_evidence_refs + implementation_log_anchors + metric_refs +
  estimated_lost_hours + estimated_retry_impact +
  recommended_action_display + recommendation_draft_ref +
  safe_runtime_action + requires_policy_artifact +
  product_defect_related + workflow_related + causal_role +
  primary_cause_finding_id + linked_finding_ids.
* :class:`FindingRule` -- the 6-field rule shape (doc-16:106-113):
  rule_id + version + required_metric_names + required_evidence_kinds +
  min_confidence + emits_kind.

Plus the :data:`REQUIRED_V1_FINDING_CLASS_NAMES` 16-name tuple
(doc-16:120-137) -- the v1 finding-class contract subsequent Slice 16
sub-slices ground rule loading on.

It is the **cross-cutting typed foundation** that subsequent Slice 16
sub-slices (rule loader + finding emitter + dedupe key engine +
primary-vs-supporting evidence rules + product/workflow separation +
implementation-plan deviation rules + scorecard persistence at
``review:governance-findings:{corpus_id}`` + suppression/expiry metadata
per doc-16 § Refactoring Steps steps 2-7 at doc-16:158-169) build on;
this first sub-slice does NOT yet wire these typed shapes into any
executor / checkpoint / merge-queue / governance-projection /
supervisor-classifier consumer -- that wiring lands in subsequent
sub-slices.

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only -- they do NOT mutate executor /
control-plane / product state, take merge or checkpoint authority, or
force policy activation. Governance findings are derived rows (per
doc-16:174 *"Findings are derived governance records and never write
execution `dag-*` authority artifacts"*) and never change execution
state. Per doc-16:117 *"`recommended_action_display` is non-executable
report text. Runtime or workflow consumers must ignore it for policy
changes."* -- the recommendation surface is display-only; behaviour
changes require an explicit Slice 17 policy artifact + tests + owner
review + later activation by the owning component.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-16:201-291 Slice 13A Shared Completeness Model Dependency).** The
:attr:`GovernanceFinding.primary_evidence_refs` +
:attr:`GovernanceFinding.supporting_evidence_refs` fields are lists of
Slice 13a :class:`GovernanceEvidenceRef` (imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`). NEITHER
type is redefined here -- per doc-13a:285-287 step 9 (*"Update
governance Slices 13-20 and context Slice 21 to depend on this shared
completeness model instead of redefining authority semantics
locally"*) this module consumes the shared model directly.

Per doc-16:201-291 (§ "Slice 13A Shared Completeness Model
Dependency") the future finding-engine sub-slices that emit the
``implementation_journal_gap`` finding kind (per doc-16:277-281)
consume the Slice 13A shared :data:`CompletenessState` +
:class:`EvidenceCompleteness` typed shapes from
:mod:`iriai_build_v2.execution_control.completeness`; the prompt-context
gap classifier consumes :class:`AuthoritativePromptContextRouting` from
:mod:`iriai_build_v2.execution_control.dispatcher_prompt_context`; the
gate-derived gap classifier consumes
:class:`AuthoritativeGateCompanionRecord` +
:class:`AuthoritativeGateProofRow` from
:mod:`iriai_build_v2.execution_control.gate_companion`; the
snapshot-derived gap classifier consumes
:class:`AuthoritativeSnapshotListFieldCompleteness` +
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

Per doc-16:283-287 the **P3-13A-6-3 dead-until-wired binding
statement** is CLOSED: the composite adapter chain is wired into a
real consumer site at ``dashboard.py:1568`` per the Slice 13A 8th
sub-slice 13An-2 finalizer landing. Finding rules MAY now treat the
Slice 13A typed completeness shapes as execution authority for the
evidence-gap classifier; this first sub-slice exposes the typed
surface but the wiring + classifier rules land in subsequent
sub-slices per doc-16:155-169.

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) only. NO imports from
``governance/`` outside ``governance.models`` (this module is
foundational; the governance layer consumes execution-control surfaces,
not the reverse). NO imports from other parts of ``execution_control/``
(this module is foundational for the future Slice 16 rule loader; the
existing Slice 00-15 ``execution_control`` modules are NOT modified).
NO imports from ``workflows/develop/execution/phases/`` / ``supervisor``
/ ``dashboard`` (those would be downstream consumers, not
dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.governance_metrics` (Slice 15
1st sub-slice) + :mod:`iriai_build_v2.execution_control.commit_provenance`
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
Slice 14 + Slice 15 1st sub-slice precedent verbatim without
introducing new abstractions.
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
    # Doc-16:62 -- the 5-value severity Literal.
    "FindingSeverity",
    # Doc-16:63-78 -- the 14-value kind Literal.
    "FindingKind",
    # Doc-16:80 -- the 4-value causal-role Literal.
    "FindingCausalRole",
    # Doc-16:82-104 -- the 19+ field GovernanceFinding BaseModel.
    "GovernanceFinding",
    # Doc-16:106-113 -- the 6-field FindingRule BaseModel.
    "FindingRule",
    # Doc-16:120-137 -- the 16-name required-v1-finding-classes tuple.
    "REQUIRED_V1_FINDING_CLASS_NAMES",
    # Helpers mirroring Slice 13A's compute_completeness_digest +
    # Slice 14's compute_payload_sha256 + Slice 15's
    # compute_scorecard_digest canonical-JSON discipline.
    "compute_finding_idempotency_key",
    "canonical_finding_dict",
]


# --- FindingSeverity 5-value Literal (doc-16:62) ----------------------------


FindingSeverity = Literal[
    "info",
    "low",
    "medium",
    "high",
    "critical",
]
"""Doc-16:62 -- the 5-value severity ladder for governance findings.

Each finding declares a severity from this 5-value set so consumers
(Slice 17 policy recommender + Slice 19 governance reporter + the
existing Slice 10 supervisor classifier + Slice 07 typed failure
router) can sort + filter + escalate consistently.

The 5 values land verbatim from doc-16:62:

* ``info`` -- informational; non-actionable.
* ``low`` -- low-impact drag; advisory.
* ``medium`` -- moderate drag; consider policy.
* ``high`` -- significant drag; recommend policy.
* ``critical`` -- safety/correctness risk; block-until-resolved.

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- FindingKind 14-value Literal (doc-16:63-78) ----------------------------


FindingKind = Literal[
    "workflow_inefficiency",
    "unsafe_route",
    "stale_projection",
    "over_verification",
    "under_verification",
    "task_contract_weakness",
    "scheduler_mismatch",
    "runtime_instability",
    "merge_queue_drag",
    "provenance_gap",
    "implementation_plan_deviation",
    "resource_safety_risk",
    "product_defect_cluster",
    "governance_evidence_conflict",
]
"""Doc-16:63-78 -- the 14-value kind taxonomy for governance findings.

Each finding declares a ``kind`` from this 14-value set; the kind is
the coarse-grained taxonomy that consumers (Slice 17 policy recommender
+ Slice 19 governance reporter + the existing Slice 10 supervisor
classifier + Slice 07 typed failure router) route on. Per doc-16:10-11
*"Findings must distinguish product defects, workflow drag, unsafe
workflow behavior, implementation-plan drift, and evidence gaps."* --
the kind taxonomy is the typed surface that enforces this distinction.

The 14 values land verbatim from doc-16:63-78:

* **Workflow-related drag (9)**: ``workflow_inefficiency`` /
  ``unsafe_route`` / ``stale_projection`` / ``over_verification`` /
  ``under_verification`` / ``task_contract_weakness`` /
  ``scheduler_mismatch`` / ``runtime_instability`` /
  ``merge_queue_drag``.
* **Evidence / provenance gaps (2)**: ``provenance_gap`` /
  ``governance_evidence_conflict``.
* **Implementation drift (1)**: ``implementation_plan_deviation``.
* **Resource / safety risk (1)**: ``resource_safety_risk``.
* **Product defects (1)**: ``product_defect_cluster``.

Per doc-16:163 *"Product defect clusters can be observed, but
workflow policy recommendations must cite workflow-related causes."* --
``product_defect_cluster`` findings carry
:attr:`GovernanceFinding.product_defect_related=True` and DO NOT feed
workflow policy recommendations; the
:attr:`GovernanceFinding.workflow_related` flag is the typed product
vs workflow separation.

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- FindingCausalRole 4-value Literal (doc-16:80) --------------------------


FindingCausalRole = Literal[
    "primary",
    "contributing",
    "symptom",
    "unknown",
]
"""Doc-16:80 -- the 4-value causal-role taxonomy for governance findings.

Each finding declares a ``causal_role`` from this 4-value set so the
finding graph can distinguish root causes from symptoms. Per
doc-16:187-190 *"Product defect plus workflow drag: emit separate
linked findings; set ``causal_role``, ``primary_cause_finding_id``,
and ``linked_finding_ids`` so downstream recommendations can act only
on the workflow-related primary or contributing cause."* -- the causal
role is the typed surface that lets the Slice 17 policy recommender
filter to actionable causes rather than re-recommending symptoms.

The 4 values land verbatim from doc-16:80:

* ``primary`` -- the root cause finding; downstream recommendations
  act on this.
* ``contributing`` -- a contributing cause; downstream recommendations
  may act on this in addition to the primary.
* ``symptom`` -- an observed symptom whose root cause is elsewhere;
  downstream recommendations MUST NOT act on this directly.
* ``unknown`` -- the causal role is not yet determined; treat as
  advisory only.

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- REQUIRED_V1_FINDING_CLASS_NAMES 16-name tuple (doc-16:120-137) ---------


REQUIRED_V1_FINDING_CLASS_NAMES: tuple[str, ...] = (
    # Doc-16:122 -- commit hygiene loop class.
    "commit_hygiene_loop",
    # Doc-16:123 -- ACL/writeability drag class.
    "acl_or_writeability_drag",
    # Doc-16:124 -- worktree alias drift class.
    "worktree_alias_drift",
    # Doc-16:125 -- stale context projection class.
    "stale_context_projection",
    # Doc-16:126 -- runtime provider instability class.
    "runtime_provider_instability",
    # Doc-16:127 -- merge-queue wait/retry drag class.
    "merge_queue_wait_or_retry_drag",
    # Doc-16:128 -- over-verification on low-risk lanes class.
    "over_verification_low_risk_lane",
    # Doc-16:129 -- under-verification on high-risk lanes class.
    "under_verification_high_risk_lane",
    # Doc-16:130 -- scheduler wave too small class.
    "scheduler_wave_too_small",
    # Doc-16:131 -- scheduler wave too large class.
    "scheduler_wave_too_large",
    # Doc-16:132 -- task contract ambiguity class.
    "task_contract_ambiguity",
    # Doc-16:133 -- line provenance gap class.
    "line_provenance_gap",
    # Doc-16:134 -- implementation journal gap class.
    "implementation_journal_gap",
    # Doc-16:135 -- accepted plan deviation class.
    "accepted_plan_deviation",
    # Doc-16:136 -- resource budget pressure class.
    "resource_budget_pressure",
    # Doc-16:137 -- governance evidence conflict class.
    "governance_evidence_conflict",
)
"""Doc-16:120-137 -- the 16-name tuple of required v1 finding class names.

This tuple is the typed contract subsequent Slice 16 sub-slices ground
the finding-rule loader + emitter on. Each name corresponds to a
:class:`FindingRule` that the (future) rule loader registers; the
tuple's 16-name set is the minimum surface a v1 finding engine must
cover.

The 16 names span 5 categories:

1. **Workflow drag (10)**: ``commit_hygiene_loop`` (doc-16:122),
   ``acl_or_writeability_drag`` (doc-16:123),
   ``worktree_alias_drift`` (doc-16:124),
   ``stale_context_projection`` (doc-16:125),
   ``runtime_provider_instability`` (doc-16:126),
   ``merge_queue_wait_or_retry_drag`` (doc-16:127),
   ``over_verification_low_risk_lane`` (doc-16:128),
   ``under_verification_high_risk_lane`` (doc-16:129),
   ``scheduler_wave_too_small`` (doc-16:130),
   ``scheduler_wave_too_large`` (doc-16:131).
2. **Task contract weakness (1)**: ``task_contract_ambiguity``
   (doc-16:132).
3. **Provenance / evidence gaps (3)**: ``line_provenance_gap``
   (doc-16:133), ``implementation_journal_gap`` (doc-16:134),
   ``governance_evidence_conflict`` (doc-16:137).
4. **Implementation drift (1)**: ``accepted_plan_deviation``
   (doc-16:135).
5. **Resource safety (1)**: ``resource_budget_pressure`` (doc-16:136).

Per doc-16:139-151 the legacy process-improvement class migration
table maps prior class names onto these canonical 16 (e.g. legacy
``commit_hygiene_loops`` -> canonical ``commit_hygiene_loop``); the
canonical 16-name set is the cross-version v1 contract.

Per doc-16:46-51 *"Compatible deviations: Finding class names can
differ from this plan if a migration table maps old names and tests
assert one canonical emitted class per condition. Some findings may
start advisory-only if the slice records missing evidence and blocks
policy consumption."* -- subsequent sub-slices MAY introduce
additional class names; the 16-name tuple is the v1 minimum surface
contract.

Per doc-16:152-160 *"Blocking deviations: Findings can be emitted
without evidence refs or log anchors. Findings merge product defects
and workflow failures into one class. A finding can directly mutate
scheduler, router, supervisor, or executor state."* -- the typed
surface enforces these blocking-deviation guards at construction:
:attr:`GovernanceFinding.primary_evidence_refs` is a typed list
(empty-list emitter discipline lives in subsequent sub-slices);
:attr:`GovernanceFinding.workflow_related` +
:attr:`GovernanceFinding.product_defect_related` are typed booleans
(product vs workflow separation); and the typed surface never exposes
mutation hooks (per the governance prompt § "Non-Negotiables" the
typed shapes are analytical / advisory / read-only).
"""


# --- The 2 doc-16:82-113 typed shapes ---------------------------------------


class GovernanceFinding(BaseModel):
    """Doc-16:82-104 -- the governance finding record shape.

    A governance finding is the deterministic + typed +
    deduped + versioned + evidence-backed signal the (future) Slice 16
    rule engine emits over an evidence set + metrics scorecard. Per
    doc-16 § "Acceptance Criteria":

    * *"Findings are deterministic, typed, deduped, versioned, and
      evidence-backed."* (doc-16:207)
    * *"Every finding distinguishes workflow-related and product-related
      impact."* (doc-16:208) -- enforced by
      :attr:`workflow_related` + :attr:`product_defect_related`.
    * *"Every governance recommendation has a source finding and
      confidence threshold."* (doc-16:209) -- enforced by
      :attr:`confidence` (float) +
      :attr:`requires_policy_artifact` (bool) +
      :attr:`recommendation_draft_ref` (str | None).
    * *"Implementation-plan drift is visible as a governance finding
      class."* (doc-16:210) -- enforced by the
      :data:`FindingKind` = ``implementation_plan_deviation`` value.
    * *"No finding directly mutates workflow state."* (doc-16:211) --
      enforced by the typed surface (the BaseModel has no mutation
      methods; the recommended-action field is non-executable display
      text per doc-16:117).

    Per doc-16:117 *"`recommended_action_display` is non-executable
    report text. Runtime or workflow consumers must ignore it for
    policy changes. Any behavior-changing proposal must be represented
    as a separate Slice 17 recommendation draft with its own evidence
    refs, review state, and consumer-owned activation path."* --
    :attr:`recommended_action_display` is display-only;
    :attr:`recommendation_draft_ref` is the typed reference to a future
    Slice 17 recommendation draft.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-16:201-291).** :attr:`primary_evidence_refs` +
    :attr:`supporting_evidence_refs` are lists of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from :mod:`iriai_build_v2.workflows.develop.governance.models`;
    NOT redefined here).

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    """Doc-16:83 -- the deterministic dedupe key for the finding.

    Per doc-16:158 *"Add dedupe keys from finding class,
    feature/window, affected scope, evidence digest, and rule version."*
    the idempotency key is the stable hash subsequent sub-slices use to
    deduplicate findings across reruns. Per doc-16:178 *"Finding ids
    are stable across reruns when input evidence and rule version do
    not change."* the key is sticky across reruns when the underlying
    rule + evidence + metric inputs are unchanged.

    The :func:`compute_finding_idempotency_key` helper produces the
    canonical SHA-256-derived key from the finding's logical inputs
    (kind + class_name + feature_id + affected_scope + primary
    evidence-ref digests + rule version).
    """

    kind: FindingKind
    """Doc-16:84 -- the coarse-grained taxonomy classification from
    the 14-value :data:`FindingKind` Literal (doc-16:63-78).

    Per Pydantic Literal validation the field accepts only one of the
    14 values; unknown values fail closed with a typed
    ``ValidationError``."""

    class_name: str
    """Doc-16:85 -- the canonical fine-grained class name from the
    16-value :data:`REQUIRED_V1_FINDING_CLASS_NAMES` v1 contract
    (doc-16:120-137; e.g. ``commit_hygiene_loop`` /
    ``stale_context_projection`` / ``runtime_provider_instability``).

    Per doc-16:46-51 compatible deviations: class names can differ
    from the v1 contract if a migration table maps old names and tests
    assert one canonical emitted class per condition; the canonical
    set is :data:`REQUIRED_V1_FINDING_CLASS_NAMES`. The field is a
    free-form ``str`` here so future Slice 16 sub-slices can introduce
    additional class names without re-versioning the BaseModel; the
    16-name v1 contract is enforced by the (future) rule loader."""

    severity: FindingSeverity
    """Doc-16:86 -- the severity ladder classification from the
    5-value :data:`FindingSeverity` Literal (doc-16:62).

    Per Pydantic Literal validation the field accepts only one of the
    5 values; unknown values fail closed with a typed
    ``ValidationError``."""

    confidence: float
    """Doc-16:87 -- the confidence score in the finding's correctness
    (0.0 = no confidence, 1.0 = full confidence).

    Per doc-16:193 *"Low confidence: findings may be reported but
    cannot feed policy recommendations."* the confidence threshold
    gates whether a finding feeds the Slice 17 recommendation
    interface. Per doc-16:111 + doc-16:209 every recommendation cites
    its source finding's confidence."""

    feature_id: str | None
    """Doc-16:88 -- the feature id the finding's evidence corpus is
    scoped to (e.g. ``"8ac124d6"`` for the canonical calibration
    fixture; future feature ids for production findings). ``None`` for
    cross-feature findings that span the entire Slice 00-12 corpus."""

    affected_scope: dict[str, Any]
    """Doc-16:89 -- the dict of scope dimensions the finding's impact
    spans (e.g. ``{"lane": "high_risk", "runtime": "claude-sdk",
    "policy": "merge_queue"}``).

    Per doc-16:158 the affected scope is one of the dedupe-key inputs
    so two findings with the same kind + class + feature but different
    affected scopes are emitted as distinct findings. Free-form
    ``dict[str, Any]`` so future Slice 16 sub-slices can introduce new
    scope dimensions without re-versioning the BaseModel."""

    primary_evidence_refs: list[GovernanceEvidenceRef]
    """Doc-16:90 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    primary canonical evidence references.

    Per doc-16:159-161 *"Add primary-vs-supporting evidence rules.
    Every finding needs at least one primary canonical evidence ref
    unless it is explicitly an evidence-gap finding."* the primary
    evidence refs are the canonical evidence the finding grounds on;
    subsequent Slice 16 sub-slices enforce the at-least-one-primary
    invariant at emitter time (this 1st sub-slice exposes the typed
    surface; the invariant lives in the rule engine).

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-16:201-291).** This field is the list of Slice 13a shared
    :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per the auto-memory ``feedback_cite_everything``
    rule + Slice 13A invariant *"Findings cite exact evidence (per
    Slice 13A)."* (doc-16:301) the typed surface enforces the
    refs-only no-raw-body-hydration discipline at construction."""

    supporting_evidence_refs: list[GovernanceEvidenceRef]
    """Doc-16:91 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    supporting evidence references.

    Per doc-16:159-161 supporting evidence augments the primary
    evidence (e.g. correlated signals, follow-up confirmations); a
    finding MAY have zero supporting refs. The supporting refs do NOT
    satisfy the at-least-one-primary invariant.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-16:201-291).** This field is the list of Slice 13a shared
    :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here."""

    implementation_log_anchors: list[str]
    """Doc-16:92 -- the list of implementation-log anchor strings
    (e.g. journal headings, decision-log line numbers per the Slice 13
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    discipline).

    Per doc-16:163-165 *"Add implementation-plan deviation rules over
    journal anchors, reviewer findings, accepted deviations, and late
    test failures."* the anchors are the typed reference back to the
    implementation journal + decision log that lets the (future) Slice
    16 rule engine emit ``implementation_plan_deviation`` +
    ``accepted_plan_deviation`` findings.

    Per doc-16:191-192 *"Missing implementation logs: emit
    ``implementation_journal_gap`` and block plan-vs-actual
    recommendations."* an empty anchor list (combined with a
    completeness signal from the Slice 13A shared model) is the typed
    trigger for the ``implementation_journal_gap`` finding kind."""

    metric_refs: list[str]
    """Doc-16:93 -- the list of metric NAMES (just strings, per
    doc-16:93 ``metric_refs: list[str]``) the finding's confidence
    + severity grounds on.

    Per doc-16:222 *"Slice 15 supplies metric refs and confidence."*
    the metric refs are the typed reference back to the Slice 15
    :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    records by name (not the typed BaseModel; just the name string per
    doc-16:93). The 15-name v1 metric contract lives at
    :data:`~iriai_build_v2.execution_control.governance_metrics.REQUIRED_V1_METRIC_NAMES`."""

    estimated_lost_hours: float | None
    """Doc-16:94 -- the estimated workflow hours lost to the
    finding's underlying drag (e.g. estimated retry hours for a
    ``runtime_provider_instability`` finding). ``None`` if not
    quantified.

    Per doc-16:94 the estimated lost hours is a float; subsequent
    Slice 16 sub-slices may attach a confidence-interval pair (low /
    high) per the doc-15 fixture-based calibration pattern."""

    estimated_retry_impact: float | None
    """Doc-16:95 -- the estimated retry-budget impact the finding's
    underlying drag consumes (e.g. fraction of total retry budget).
    ``None`` if not quantified.

    Per doc-16:95 the estimated retry impact is a float; subsequent
    Slice 16 sub-slices may tighten to a typed shape (e.g.
    ``RetryImpactDescriptor`` per the doc-15
    :data:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    pattern)."""

    recommended_action_display: str
    """Doc-16:96 -- the non-executable display text for the
    recommended action.

    Per doc-16:117 *"`recommended_action_display` is non-executable
    report text. Runtime or workflow consumers must ignore it for
    policy changes."* the field is display-only; subsequent Slice 17
    sub-slices use :attr:`recommendation_draft_ref` to carry the
    typed reference to a behaviour-changing policy artifact (with its
    own evidence refs + review state + consumer-owned activation
    path).

    The free-form ``str`` so future Slice 19 reporting can render the
    field directly in CLI / Slack / report output (per doc-19's
    structured-records-primary + prose-reports-secondary discipline)."""

    recommendation_draft_ref: str | None = None
    """Doc-16:97 -- the typed reference to a Slice 17 recommendation
    draft (e.g. ``"recommendation:draft:abc123"``).

    Per doc-16:117 the recommendation draft is the only path to a
    behaviour-changing policy artifact; the typed reference here is
    the link a Slice 17 consumer follows to materialise the draft.
    ``None`` if no recommendation draft has been authored for this
    finding yet (the default for advisory-only findings per
    doc-16:46-51)."""

    safe_runtime_action: bool
    """Doc-16:98 -- ``True`` if the finding's recommended action is
    safe to take at runtime (i.e. the action does not require an
    explicit Slice 17 policy artifact + tests + owner review).

    Per doc-16:117 the recommended-action display is non-executable;
    this typed flag lets future Slice 17 consumers filter to actions
    that have a safe-runtime path (e.g. retrying a known-flaky
    runtime provider) vs actions that require explicit policy
    activation (e.g. tightening a scheduler wave size)."""

    requires_policy_artifact: bool
    """Doc-16:99 -- ``True`` if the finding's recommended action
    requires an explicit Slice 17 policy artifact + tests + owner
    review + later activation by the owning component.

    Per doc-16:117 *"Any behavior-changing proposal must be
    represented as a separate Slice 17 recommendation draft with its
    own evidence refs, review state, and consumer-owned activation
    path."* this flag is the typed surface enforcing the
    no-direct-mutation discipline. Mutually compatible with
    :attr:`safe_runtime_action` (a finding may be both safe to act on
    at runtime AND require a policy artifact for the runtime to even
    consider acting)."""

    product_defect_related: bool
    """Doc-16:100 -- ``True`` if the finding's evidence cites a
    product defect (i.e. a defect in the produced product code, not a
    workflow drag).

    Per doc-16:163 *"Product defect clusters can be observed, but
    workflow policy recommendations must cite workflow-related
    causes."* product-defect findings DO NOT feed workflow policy
    recommendations; the typed flag is the cross-product separation
    enforcer."""

    workflow_related: bool
    """Doc-16:101 -- ``True`` if the finding's evidence cites a
    workflow drag (i.e. a drag in the workflow itself, not a product
    defect).

    Per doc-16:163 + doc-16:208 *"Every finding distinguishes
    workflow-related and product-related impact."* the workflow flag
    is the typed surface enforcing this distinction; a finding MAY
    have both :attr:`product_defect_related=True` and
    :attr:`workflow_related=True` (a product defect that also
    consumes workflow retry budget); the combined case is handled by
    the typed :attr:`causal_role` +
    :attr:`primary_cause_finding_id` + :attr:`linked_finding_ids`
    fields per doc-16:187-190."""

    causal_role: FindingCausalRole
    """Doc-16:102 -- the causal-role classification from the 4-value
    :data:`FindingCausalRole` Literal (doc-16:80).

    Per doc-16:187-190 *"Product defect plus workflow drag: emit
    separate linked findings; set ``causal_role``,
    ``primary_cause_finding_id``, and ``linked_finding_ids`` so
    downstream recommendations can act only on the workflow-related
    primary or contributing cause."* the causal role is the typed
    surface enforcing this primary-vs-symptom separation."""

    primary_cause_finding_id: str | None = None
    """Doc-16:103 -- the typed reference to the primary cause finding
    (by idempotency_key) when this finding is a symptom or
    contributing cause.

    Per doc-16:187-190 the primary cause id lets downstream consumers
    follow the causal chain from a symptom back to its root cause
    finding; ``None`` for findings whose :attr:`causal_role` is
    ``primary`` (the finding IS the primary cause)."""

    linked_finding_ids: list[str] = Field(default_factory=list)
    """Doc-16:104 -- the list of typed references to linked findings
    (by idempotency_key) that share the same underlying drag pattern.

    Per doc-16:187-190 the linked-finding ids let downstream consumers
    surface clusters of related findings (e.g. a product defect plus
    its workflow-drag symptoms); the typed default empty-list is the
    safe-default for findings that have no linked siblings."""


class FindingRule(BaseModel):
    """Doc-16:106-113 -- the finding rule definition shape.

    A finding rule is the typed declaration the (future) Slice 16
    rule loader registers; each rule emits exactly one
    :data:`FindingKind` value (per doc-16:113 ``emits_kind:
    FindingKind``) when its input contract (required metric names +
    required evidence kinds + min confidence) is satisfied. Per
    doc-16:155-156 *"Convert existing process-improvement logic into
    versioned finding rules after the governance evidence and metric
    layers exist."* the rule shape is the cross-version surface the
    rule loader grounds on.

    Per doc-16:215-217 *"Rollback disables finding generation and
    leaves existing finding artifacts for audit. If a finding rule is
    bad, release a new rule version and mark prior findings
    superseded rather than rewriting history."* the rule version is
    the typed knob the rollback story relies on; older finding rows
    keep their original :attr:`FindingRule.version` reference so the
    audit story is preserved.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    model_config = ConfigDict(extra="forbid")

    rule_id: str
    """Doc-16:107 -- the stable rule identifier (e.g.
    ``"commit_hygiene_loop_v1"``); used as the dedupe key input per
    doc-16:158."""

    version: str
    """Doc-16:108 -- the rule version string (e.g. ``"v1"`` /
    ``"v1.1"``). Per doc-16:215-217 version bumps are the rollback /
    supersede path; older findings keep their original version
    reference so the audit story is preserved."""

    required_metric_names: list[str]
    """Doc-16:109 -- the list of metric NAMES the rule requires (per
    the Slice 15
    :data:`~iriai_build_v2.execution_control.governance_metrics.REQUIRED_V1_METRIC_NAMES`
    contract). The rule MUST find all required metrics in the input
    scorecard or it emits a typed gap finding rather than its primary
    kind."""

    required_evidence_kinds: list[str]
    """Doc-16:110 -- the list of evidence kind / authority strings
    the rule requires (per the Slice 13a
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceAuthority`
    9-value Literal). The rule MUST find all required evidence kinds
    in the input evidence set or it emits a typed gap finding rather
    than its primary kind."""

    min_confidence: float
    """Doc-16:111 -- the minimum confidence threshold (0.0 - 1.0)
    above which the rule emits its primary kind. Per doc-16:193
    *"Low confidence: findings may be reported but cannot feed policy
    recommendations."* findings below the threshold are reported but
    do not feed the Slice 17 recommender."""

    emits_kind: FindingKind
    """Doc-16:112-113 -- the kind classification from the 14-value
    :data:`FindingKind` Literal (doc-16:63-78) the rule emits when its
    input contract is satisfied. Per Pydantic Literal validation
    unknown values fail closed at construction with a typed
    ``ValidationError``."""


# --- Finding idempotency-key helpers (mirrors Slice 13A
#     compute_completeness_digest + Slice 14 compute_payload_sha256 +
#     Slice 15 compute_scorecard_digest canonical-JSON discipline) ------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_metrics._canonical_json`
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
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_finding_dict(finding: GovernanceFinding) -> dict[str, Any]:
    """Project a :class:`GovernanceFinding` to its canonical-JSON dict
    representation.

    This helper produces the canonical-dict projection used by
    :func:`compute_finding_idempotency_key` (when computing a finding's
    deterministic dedupe key from its logical inputs) and by
    subsequent Slice 16 sub-slices when persisting finding rows at
    ``review:governance-findings:{corpus_id}`` per doc-16:166-167.

    The projection uses :meth:`BaseModel.model_dump` with ``mode='json'``
    so any nested ``datetime`` field on the typed Slice 13a
    :class:`GovernanceEvidenceRef` evidence-ref entries projects to its
    ISO-8601 string form (cross-process stable). The resulting dict is
    the input to :func:`compute_finding_idempotency_key`; both helpers
    use :func:`_canonical_json` for deterministic serialisation.
    """

    return finding.model_dump(mode="json")


def compute_finding_idempotency_key(
    *,
    kind: FindingKind,
    class_name: str,
    feature_id: str | None,
    affected_scope: dict[str, Any],
    primary_evidence_digests: list[str],
    rule_version: str,
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    :class:`GovernanceFinding`.

    Per doc-16:158 *"Add dedupe keys from finding class, feature/window,
    affected scope, evidence digest, and rule version."* the key is
    computed over the 6 logical inputs:

    * ``kind`` -- the coarse-grained taxonomy classification.
    * ``class_name`` -- the canonical fine-grained class name.
    * ``feature_id`` -- the feature id scope (``None`` for
      cross-feature findings).
    * ``affected_scope`` -- the scope-dimensions dict.
    * ``primary_evidence_digests`` -- the list of primary evidence-ref
      digest strings (per the Slice 13a
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef.digest`
      field). The list is sorted before digesting so the key is
      order-invariant w.r.t. evidence-ref ordering.
    * ``rule_version`` -- the Slice 16 rule version that emitted this
      finding (per :attr:`FindingRule.version`); per doc-16:215-217
      version bumps produce distinct keys so older findings can be
      superseded rather than overwritten.

    Per doc-16:178 *"Finding ids are stable across reruns when input
    evidence and rule version do not change."* the helper is the
    cross-process freshness contract subsequent sub-slices rely on
    when detecting duplicate findings.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    + Slice 15
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    canonical-JSON + SHA-256 discipline verbatim.
    """

    payload: dict[str, Any] = {
        "kind": kind,
        "class_name": class_name,
        "feature_id": feature_id,
        "affected_scope": affected_scope,
        # Sort the digest list so the key is order-invariant w.r.t.
        # evidence-ref ordering (per the doc-16:158 + doc-16:178
        # determinism contract).
        "primary_evidence_digests": sorted(primary_evidence_digests),
        "rule_version": rule_version,
    }
    return _sha256_hex(_canonical_json(payload))
