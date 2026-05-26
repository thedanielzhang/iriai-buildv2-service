"""Slice 18 first sub-slice -- foundational counterfactual replay and simulation typed-shape module.

This module owns the doc-18 "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/18-counterfactual-replay-and-simulation.md:60-96``):

* :data:`ReplayMode` -- 3-value Literal alias (doc-18:61): the replay
  mode a :class:`ReplayCorpus` declares. Values: ``event_replay`` /
  ``summary_replay`` / ``hybrid``.
* :data:`RiskChange` -- 4-value Literal alias (doc-18:91): the
  estimated-risk-change taxonomy a :class:`CounterfactualResult`
  declares. Values: ``lower`` / ``same`` / ``higher`` / ``unknown``.
* :data:`RecommendedNextStep` -- 4-value Literal alias (doc-18:95): the
  recommended-next-step taxonomy a :class:`CounterfactualResult`
  declares. Values: ``discard`` / ``collect_more_evidence`` /
  ``draft_policy`` / ``implementation_plan``.
* :class:`ReplayCorpus` -- the 6-field replay-corpus record shape
  (doc-18:63-69): ``corpus_id`` + ``feature_ids`` +
  ``evidence_set_ids`` + ``implementation_anchor_ids`` + ``mode`` +
  ``validity_limits``.
* :class:`CounterfactualScenario` -- the 6-field scenario record shape
  (doc-18:71-77): ``scenario_id`` + ``policy_under_test`` +
  ``baseline_policy_refs`` + ``affected_consumers`` +
  ``required_evidence_kinds`` + ``assumptions``.
* :class:`CounterfactualResult` -- the 16-field result record shape
  (doc-18:79-96): ``result_id`` + ``result_version`` + ``scenario_id``
  + ``corpus_id`` + ``assumptions`` + ``validity_limits`` +
  ``policy_provenance_refs`` + ``safety_guard_class`` +
  ``estimated_delta_hours`` + ``estimated_delta_repair_cycles`` +
  ``estimated_delta_commit_failures`` + ``estimated_risk_change`` +
  ``confidence`` + ``invalidated_by`` + ``supporting_finding_ids`` +
  ``recommended_next_step``.

Plus the canonical-JSON helpers
:func:`compute_counterfactual_idempotency_key` +
:func:`canonical_counterfactual_dict` mirroring the Slice 13A
``compute_completeness_digest`` + Slice 14 ``compute_payload_sha256`` +
Slice 15 ``compute_scorecard_digest`` + Slice 16 1st sub-slice
``compute_finding_idempotency_key`` + ``canonical_finding_dict`` +
Slice 17 1st sub-slice
``compute_policy_recommendation_idempotency_key`` +
``canonical_policy_recommendation_dict`` canonical-JSON + SHA-256
discipline verbatim.

It is the **cross-cutting typed foundation** that subsequent Slice 18
sub-slices (the replay corpus loader + scenario definitions + summary
replay + event replay + baseline-vs-scenario comparator + counterfactual
result writer + Slice 17 recommendation citation hook per doc-18 §
Refactoring Steps steps 1-7 at doc-18:111-119) build on; this first
sub-slice does NOT yet wire these typed shapes into any executor /
loader / metrics-comparator / scenario-emitter consumer -- that wiring
lands in subsequent sub-slices.

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only -- they do NOT mutate executor /
control-plane / product state, take merge or checkpoint authority, or
force policy activation. Counterfactual replay results are review /
governance artifacts (per doc-18:123 *"Replay results are
review/governance artifacts only."* + doc-18:124-125 *"Replay must not
write `dag-*` execution authority artifacts or active policy markers."*)
and never change execution state.

**Doc-18 acceptance binding** (doc-18:160-168): the typed shapes here
expose the surface that future Slice 18 sub-slices enforce the 5
acceptance criteria at:

* **AC1** -- *"Counterfactuals are deterministic, versioned, and
  evidence-backed."* (doc-18:162) -- enforced by the typed
  ``result_version`` + ``policy_provenance_refs`` fields + the
  :func:`compute_counterfactual_idempotency_key` canonical-JSON +
  SHA-256 helper.
* **AC2** -- *"Every result lists assumptions and validity limits."*
  (doc-18:163) -- enforced by the typed ``assumptions: list[str]`` +
  ``validity_limits: list[str]`` fields (per doc-18:84-85).
* **AC3** -- *"Replay cannot mutate live workflow state."*
  (doc-18:164) -- enforced by the read-only typed-shape design (no
  mutation methods on any BaseModel) + the doc-18:123-125 persistence
  discipline future Slice 18 sub-slices land at the loader / writer
  layer.
* **AC4** -- *"Recommendations that affect runtime behavior cite
  replay results or explicitly say more evidence is needed."*
  (doc-18:165-166) -- enforced by the Slice 17 5th sub-slice
  :class:`~iriai_build_v2.execution_control.replay_requirement_hook`
  cross-reference + this 1st sub-slice's
  :attr:`CounterfactualResult.result_id` typed identifier that the
  Slice 17 recommendation surface cites via
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`.
* **AC5** -- *"The replay corpus includes both 8ac124d6 evidence and
  Slice 00-12 implementation artifacts."* (doc-18:167-168) -- enforced
  by the typed :attr:`ReplayCorpus.feature_ids: list[str]` +
  :attr:`ReplayCorpus.implementation_anchor_ids: list[str]` fields
  (per doc-18:65 + doc-18:67) the future Slice 18 2nd sub-slice
  corpus-loader populates from the Slice 00 ``8ac124d6`` fixture +
  the Slice 00-12 implementation journal anchors.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-18:186-249 Slice 13A Shared Completeness Model Dependency).** The
:attr:`CounterfactualResult.policy_provenance_refs` field is a list of
Slice 13a :class:`GovernanceEvidenceRef` (imported from
:mod:`iriai_build_v2.workflows.develop.governance.models`). The
:class:`GovernanceEvidenceRef` type is NOT redefined here -- per
doc-13a:285-287 step 9 (*"Update governance Slices 13-20 and context
Slice 21 to depend on this shared completeness model instead of
redefining authority semantics locally"*) this module consumes the
shared model directly. This is a stronger contract than doc-18:86
``list[str]``; per the implementer-prompt typed-REUSE binding the
governance-evidence-ref surface IS the Slice 13a typed BaseModel, NOT
a free-form string list.

Per doc-18:186-249 future Slice 18 sub-slices that emit replay results
consume the Slice 13A shared :data:`CompletenessState` +
:class:`EvidenceCompleteness` typed shapes from
:mod:`iriai_build_v2.execution_control.completeness`; the
loader-specific scanner consumes
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
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition.

**Slice 17 dependency reconciliation.** The
:attr:`CounterfactualScenario.affected_consumers` field is a list of
Slice 17 :data:`PolicyConsumer` (imported from
:mod:`iriai_build_v2.execution_control.policy_recommendation`). The
:data:`PolicyConsumer` Literal is NOT redefined here -- per the
no-second-source-of-truth discipline this module consumes the shared
Slice 17 6-value taxonomy directly so a counterfactual scenario's
"affected consumers" classification is the same 6 values the Slice 17
recommendation surface uses (per doc-17:65 +
``policy_recommendation.py:246-253``).

**By-name reference contracts.** Per doc-18:94 the
:attr:`CounterfactualResult.supporting_finding_ids` field is
``list[str]`` (just strings; NOT typed BaseModels):

* :attr:`supporting_finding_ids: list[str]` carries
  :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
  string references back to Slice 16 1st sub-slice findings (per
  doc-16:83 ``idempotency_key: str`` at
  ``finding_engine.py:443``).

The by-name reference shape is the documented doc-18:94 contract;
this 1st sub-slice does NOT import the typed Slice 16
:class:`GovernanceFinding` BaseModel (the ``list[str]`` field type is
sufficient per doc-18:94; the by-name reference discipline mirrors the
Slice 17 1st sub-slice
:attr:`GovernancePolicyRecommendation.source_finding_ids: list[str]`
pattern at ``policy_recommendation.py:743``).

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 17 1st sub-slice
module (``.policy_recommendation``) only. NO imports from
``governance/`` outside ``governance.models`` (this module is
foundational; the governance layer consumes execution-control surfaces,
not the reverse). NO imports from other parts of ``execution_control/``
beyond Slice 17 (this module is foundational for the future Slice 18
loader + scenario emitter + result writer; the existing Slice 00-16
``execution_control`` modules are NOT modified). NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.policy_recommendation` (Slice
17 1st sub-slice) + :mod:`iriai_build_v2.execution_control.finding_engine`
(Slice 16 1st sub-slice) +
:mod:`iriai_build_v2.execution_control.governance_metrics` (Slice 15
1st sub-slice) +
:mod:`iriai_build_v2.execution_control.commit_provenance` (Slice 14
1st sub-slice) +
:mod:`iriai_build_v2.execution_control.completeness` (Slice 13A 2nd
sub-slice): ``BaseModel`` subclasses with ``ConfigDict(extra="forbid")``
so typo-d kwargs fail closed as a typed ``ValidationError`` rather
than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``float`` / ``bool`` /
``list`` / ``dict``). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 + Slice 15 + Slice 16 + Slice 17 1st sub-slice precedent
verbatim without introducing new abstractions.

**Activation-authority boundary** (per STATUS.md § "Loop discipline" +
doc-17:178-179). Counterfactual replay results are review /
governance artifacts only -- never runtime policy authority. This is
the same boundary the Slice 17 7th sub-slice activation-boundary test
surface enforces for the Slice 13-17 governance modules; the Slice 18
1st sub-slice typed-shape module honours the boundary at the typed
surface (no activation methods; no consumer-state mutation; results
are read-only descriptors that Slice 17 ``counterfactual_result_refs``
cites by id).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from iriai_build_v2.execution_control.policy_recommendation import (
    PolicyConsumer,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Doc-18:61 -- the 3-value ReplayMode Literal.
    "ReplayMode",
    # Doc-18:91 -- the 4-value RiskChange Literal alias for
    # CounterfactualResult.estimated_risk_change.
    "RiskChange",
    # Doc-18:95 -- the 4-value RecommendedNextStep Literal alias for
    # CounterfactualResult.recommended_next_step.
    "RecommendedNextStep",
    # Doc-18:63-69 -- the 6-field ReplayCorpus BaseModel.
    "ReplayCorpus",
    # Doc-18:71-77 -- the 6-field CounterfactualScenario BaseModel.
    "CounterfactualScenario",
    # Doc-18:79-96 -- the 16-field CounterfactualResult BaseModel.
    "CounterfactualResult",
    # Helpers mirroring Slice 13A's compute_completeness_digest +
    # Slice 14's compute_payload_sha256 + Slice 15's
    # compute_scorecard_digest + Slice 16 1st sub-slice's
    # compute_finding_idempotency_key + canonical_finding_dict +
    # Slice 17 1st sub-slice's
    # compute_policy_recommendation_idempotency_key +
    # canonical_policy_recommendation_dict canonical-JSON discipline.
    "compute_counterfactual_idempotency_key",
    "canonical_counterfactual_dict",
]


# --- ReplayMode 3-value Literal (doc-18:61) ---------------------------------


ReplayMode = Literal[
    "event_replay",
    "summary_replay",
    "hybrid",
]
"""Doc-18:61 -- the 3-value replay mode taxonomy for a
:class:`ReplayCorpus`.

A replay corpus declares a ``mode`` from this 3-value set so the
(future) Slice 18 corpus loader + scenario emitter + summary / event
replay engines (per doc-18:111-119 § Refactoring Steps steps 1-7) can
select the right replay engine and the right validity-limit discipline
for the corpus.

The 3 values land verbatim from doc-18:61:

* ``event_replay`` -- full typed-event replay over a corpus where typed
  attempt, gate, failure, queue, and checkpoint transitions are
  available (per doc-18:114-115 step 4). Highest-fidelity replay; the
  replay-result confidence floor for this mode is the highest.
* ``summary_replay`` -- deterministic summary-level replay for
  metrics-level counterfactuals (per doc-18:113 step 3) where typed
  timing or fine-grained event records are not available. Per
  doc-18:133 *"Missing typed timing: use summary replay with lower
  confidence."* this mode carries a lower confidence floor than
  event replay; the typed-shape layer does NOT enforce the floor
  (that lives in the future Slice 18 4th + 5th sub-slice
  metrics-comparator + confidence-floor enforcer).
* ``hybrid`` -- a mixed mode where some portions of the corpus use
  event replay and others use summary replay. Per doc-18:48-49 *"Replay
  may start with deterministic summary-level simulation when full event
  replay is not available, if validity limits are explicit."* the
  hybrid mode is the typed surface for the compatible-deviation case;
  the future Slice 18 corpus loader populates the validity-limits
  list with the per-segment fallback rationale.

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- RiskChange 4-value Literal (doc-18:91) ---------------------------------


RiskChange = Literal[
    "lower",
    "same",
    "higher",
    "unknown",
]
"""Doc-18:91 -- the 4-value estimated-risk-change taxonomy for a
:class:`CounterfactualResult`.

A counterfactual result declares an :attr:`estimated_risk_change` from
this 4-value set so the (future) Slice 18 6th sub-slice
typed-governance-row writer + the Slice 17 recommendation citation
hook can sort + filter + escalate consistently with the doc-17:217 +
doc-18:163 acceptance criteria for behavior-changing recommendations.

The 4 values land verbatim from doc-18:91:

* ``lower`` -- the counterfactual policy lowers the estimated risk
  vs the baseline. Per doc-18:140-146 the safety-guard exception
  chain-depth check is permitted for policies whose sole effect is to
  fail closed earlier, reduce mutation authority, or add bounded
  preflight evidence.
* ``same`` -- the counterfactual policy produces approximately the
  same estimated risk as the baseline (within the confidence
  interval). Per doc-18:138 *"Small sample size: report confidence
  and avoid policy recommendations."* the typed-shape layer exposes
  the result; the future Slice 18 5th sub-slice metrics-comparator
  attaches the per-result confidence interval.
* ``higher`` -- the counterfactual policy raises the estimated risk
  vs the baseline. Per doc-18:50 *"Counterfactual duration estimates
  may be ranges rather than exact values."* the typed-shape layer
  does NOT enforce a single-value estimate; the
  :attr:`CounterfactualResult.estimated_delta_*` fields carry the
  central estimate, and confidence carries the breadth.
* ``unknown`` -- the estimated risk change is not yet determined;
  treat as advisory only. Per doc-18:133-137 the various
  edge-case rows (missing typed timing / policy requires evidence
  not in corpus / product defect dominates window) map onto the
  ``unknown`` value when the comparator cannot produce a usable
  estimate.

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- RecommendedNextStep 4-value Literal (doc-18:95) ------------------------


RecommendedNextStep = Literal[
    "discard",
    "collect_more_evidence",
    "draft_policy",
    "implementation_plan",
]
"""Doc-18:95 -- the 4-value recommended-next-step taxonomy for a
:class:`CounterfactualResult`.

A counterfactual result declares a :attr:`recommended_next_step` from
this 4-value set so the (future) Slice 18 7th sub-slice Slice 17
recommendation citation hook + the Slice 19 governance reporter can
route + filter + escalate consistently.

The 4 values land verbatim from doc-18:95:

* ``discard`` -- the counterfactual result indicates the scenario is
  not viable as a policy candidate. Per doc-18:54 *"Replay silently
  drops evidence gaps."* the typed surface enforces the
  ``discard`` next-step at the structured-result-row layer (the
  scenario is recorded but no Slice 17 recommendation is emitted).
* ``collect_more_evidence`` -- the counterfactual result is
  inconclusive (e.g. small sample size / missing typed timing /
  evidence-completeness gap). Per doc-18:134-138 the various
  edge-case rows map onto this value.
* ``draft_policy`` -- the counterfactual result supports drafting a
  Slice 17 policy recommendation (per doc-17:165-166 + doc-17:218
  the behavior-changing recommendation MUST cite the typed
  :attr:`CounterfactualResult.result_id` via
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`).
* ``implementation_plan`` -- the counterfactual result is
  strong enough to advance directly to an implementation plan
  (per doc-18:117-119 the Slice 17 6th sub-slice
  ``activation_requirements`` surface carries the per-consumer
  activation contract).

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- ReplayCorpus (doc-18:63-69) --------------------------------------------


class ReplayCorpus(BaseModel):
    """Doc-18:63-69 -- the replay corpus record shape.

    A replay corpus is the typed descriptor for the
    historical-execution evidence the (future) Slice 18 2nd sub-slice
    summary / event replay engines run against. Per doc-18 §
    "Acceptance Criteria":

    * **AC5** *"The replay corpus includes both 8ac124d6 evidence and
      Slice 00-12 implementation artifacts."* (doc-18:167-168) --
      enforced by the typed :attr:`feature_ids: list[str]` +
      :attr:`implementation_anchor_ids: list[str]` fields the future
      corpus loader populates from the Slice 00 ``8ac124d6`` fixture
      + the Slice 00-12 implementation journal anchors.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    Per doc-18:127-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the :attr:`corpus_id` field is the typed identity surface the
    (future) Slice 18 writer uses to enforce the immutability + new-
    version-on-new-assumptions discipline.
    """

    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """Doc-18:64 -- the stable corpus identifier string. Per
    doc-18:127-129 *"Historical replay is immutable by corpus id and
    scenario id."* the corpus id is the typed identity surface the
    Slice 18 result-writer uses to enforce the immutability +
    new-version-on-new-assumptions discipline."""

    feature_ids: list[str]
    """Doc-18:65 -- the list of feature ids the corpus spans (e.g.
    ``["8ac124d6"]`` for the canonical Slice 00 fixture; future feature
    ids for production-evidence corpora).

    Per doc-18:167-168 *"The replay corpus includes both 8ac124d6
    evidence and Slice 00-12 implementation artifacts."* the
    feature-ids list is the typed surface that enforces the AC5
    coverage contract at the corpus level."""

    evidence_set_ids: list[str]
    """Doc-18:66 -- the list of Slice 13 evidence-set ids the corpus
    grounds on (per the Slice 13 typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceSet`
    contract).

    Per doc-18:178 *"Slice 13 supplies evidence sets."* the
    evidence-set-ids list is the typed reference back to Slice 13
    evidence sets; per doc-18:200-202 *"The shared
    ``ExactEvidenceManifest`` is the source-of-truth shape for the
    `Replay corpus loader rejects malformed or unbounded fixture
    inputs` acceptance test."* the future Slice 18 2nd sub-slice
    corpus loader resolves each evidence-set id via the Slice 13a
    shared evidence-set BaseModel + the Slice 13A typed manifest."""

    implementation_anchor_ids: list[str]
    """Doc-18:67 -- the list of Slice 13 implementation-artifact-anchor
    ids the corpus cites (per the Slice 13 typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    contract).

    Per doc-18:126-127 *"Replay inputs include implementation-log
    anchors so accepted deviations and review findings can explain
    why a policy did or did not work."* the implementation-anchor-ids
    list is the typed reference back to the Slice 00-12 implementation
    journal anchors that the future Slice 18 2nd sub-slice corpus
    loader includes per the AC5 coverage contract."""

    mode: ReplayMode
    """Doc-18:68 -- the typed replay-mode classification from the
    3-value :data:`ReplayMode` Literal (doc-18:61). Per Pydantic
    Literal validation the field accepts only one of the 3 values;
    unknown values fail closed with a typed ``ValidationError``."""

    validity_limits: list[str]
    """Doc-18:69 -- the list of validity-limit strings the corpus
    declares (e.g. ``["sample_size<10"]`` /
    ``["product_defect_window"]``).

    Per doc-18:48-49 *"Replay may start with deterministic
    summary-level simulation when full event replay is not available,
    if validity limits are explicit."* the validity-limits list is
    the typed surface that enforces the compatible-deviation
    discipline: the future Slice 18 2nd sub-slice corpus loader
    populates the list with the per-segment fallback rationale + the
    per-evidence-gap caveat (per doc-18:54 *"Replay silently drops
    evidence gaps."* the validity-limits list MUST carry the gap
    citation rather than being elided silently)."""


# --- CounterfactualScenario (doc-18:71-77) ----------------------------------


class CounterfactualScenario(BaseModel):
    """Doc-18:71-77 -- the counterfactual scenario record shape.

    A counterfactual scenario is the typed descriptor for the proposed
    policy + baseline-policy + affected-consumer + required-evidence-
    kinds + assumptions tuple the (future) Slice 18 3rd sub-slice
    scenario emitter populates over a :class:`ReplayCorpus`. Per
    doc-18:111-119 § Refactoring Steps step 2 the scenario emitter
    consumes evidence sets + scenario templates per doc-18:98-107.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 17 dependency reconciliation.** The
    :attr:`affected_consumers` field is a list of Slice 17 1st
    sub-slice :data:`PolicyConsumer` -- imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation`,
    NOT redefined. The Slice 17 6-value PolicyConsumer taxonomy is the
    source of truth for the "affected consumers" classification.

    **Doc-18:140-146 safety-guard binding awareness.** Per
    doc-18:140-146 *"Overfit risk: require at least one non-`8ac124d6`
    corpus before marking a general policy high confidence. A
    safety-guard exception is allowed only for policies whose sole
    effect is to fail closed earlier, reduce mutation authority, or
    add bounded preflight evidence. The scenario must set
    `safety_guard_class`, cite non-governance primary evidence, and
    pass a chain-depth check proving it is not derived solely from
    prior governance recommendations."* -- the safety-guard
    enforcement lives on :class:`CounterfactualResult` (the
    :attr:`CounterfactualResult.safety_guard_class` field per
    doc-18:87); the scenario carries the typed assumptions list that
    the safety-guard chain-depth check resolves at the future Slice 18
    5th sub-slice metrics-comparator + safety-guard validator.
    """

    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    """Doc-18:72 -- the stable scenario identifier string. Per
    doc-18:127-129 *"Historical replay is immutable by corpus id and
    scenario id. New assumptions require a new result version."* the
    scenario id is the typed identity surface paired with the corpus
    id."""

    policy_under_test: dict[str, Any]
    """Doc-18:73 -- the proposed-policy dict the scenario evaluates
    against the corpus (e.g. ``{"policy_kind": "wave_cap", "scope":
    {"lane_id": "ml-7"}, "value": {"wave_cap": 7}}``).

    Per doc-18:73 the field is ``dict[str, Any]`` -- free-form so the
    scenario emitter can produce a policy candidate of any consumer-
    specific shape (the typed Slice 17
    :class:`~iriai_build_v2.execution_control.policy_recommendation.SchedulerPolicyArtifact`
    / :class:`FailureRouterPolicyArtifact` / etc.) without the typed-
    shape layer pre-emptively narrowing the value shape. The
    consumer-specific narrowing happens at the Slice 17 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.policy_validation_interface`
    per-consumer validator."""

    baseline_policy_refs: list[str]
    """Doc-18:74 -- the list of baseline-policy ref strings the
    scenario compares against (e.g. prior recommendation ids or
    activated-policy artifact ids).

    Per doc-18:115-116 *"Compare baseline vs scenario outcomes using
    Slice 15 metrics."* the baseline-policy-refs list is the typed
    reference back to the prior policy state the (future) Slice 18
    5th sub-slice metrics-comparator uses as the comparator anchor."""

    affected_consumers: list[PolicyConsumer]
    """Doc-18:75 -- the list of Slice 17 1st sub-slice
    :data:`PolicyConsumer` values the scenario's policy-under-test
    would affect (e.g. ``["scheduler"]`` for a scheduler wave-cap
    policy; ``["failure_router", "merge_queue"]`` for a cross-cutting
    policy).

    **Slice 17 dependency reconciliation.** The element type is the
    Slice 17 1st sub-slice :data:`PolicyConsumer` 6-value Literal --
    imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation` (NOT
    redefined here). Per Pydantic Literal validation any element value
    that is not one of the 6 Slice 17 consumer values fails closed at
    construction with a typed ``ValidationError``.

    Per doc-18:147-158 § Tests *"Counterfactual scenarios with missing
    evidence return invalidated results."* -- the typed surface
    accepts the list (the at-least-one-affected-consumer invariant
    lives in the future Slice 18 3rd sub-slice scenario emitter)."""

    required_evidence_kinds: list[str]
    """Doc-18:76 -- the list of evidence-kind strings the scenario
    requires (e.g. ``["typed_attempt", "typed_failure",
    "typed_checkpoint"]``).

    Per doc-18:134-135 *"Policy requires evidence not in corpus:
    mark invalidated and collect more evidence."* the
    required-evidence-kinds list is the typed surface that lets the
    future Slice 18 4th sub-slice corpus-vs-scenario validator detect
    when the scenario cannot be evaluated against the corpus + emit
    an invalidated :class:`CounterfactualResult` with the typed
    :attr:`CounterfactualResult.invalidated_by` list populated with
    the missing-evidence-kind citations."""

    assumptions: list[str]
    """Doc-18:77 -- the list of assumption strings the scenario
    declares (e.g. ``["product_defect_independent_of_wave_size",
    "no_priority_inversion"]``).

    Per doc-18:140-146 *"Overfit risk: require at least one
    non-`8ac124d6` corpus before marking a general policy high
    confidence."* the assumptions list is the typed surface the
    future Slice 18 5th sub-slice safety-guard validator consults
    when running the chain-depth check (per the doc-18:144-146
    *"chain-depth check proving it is not derived solely from prior
    governance recommendations"* binding)."""


# --- CounterfactualResult (doc-18:79-96) ------------------------------------


class CounterfactualResult(BaseModel):
    """Doc-18:79-96 -- the counterfactual result record shape.

    A counterfactual result is the typed advisory record the (future)
    Slice 18 6th sub-slice typed-governance-row writer emits when the
    metrics-comparator (5th sub-slice) compares the
    :class:`CounterfactualScenario` against the baseline using Slice
    15 metrics. Per doc-18 § "Acceptance Criteria":

    * **AC1** -- *"Counterfactuals are deterministic, versioned, and
      evidence-backed."* (doc-18:162) -- enforced by the typed
      :attr:`result_version` (the "versioned" axis) +
      :attr:`policy_provenance_refs` (the "evidence-backed" axis) +
      the :func:`compute_counterfactual_idempotency_key` canonical-
      JSON + SHA-256 helper (the "deterministic" axis).
    * **AC2** -- *"Every result lists assumptions and validity
      limits."* (doc-18:163) -- enforced by the typed
      :attr:`assumptions: list[str]` + :attr:`validity_limits:
      list[str]` fields (per doc-18:84-85).
    * **AC3** -- *"Replay cannot mutate live workflow state."*
      (doc-18:164) -- enforced by the read-only typed-shape design
      (no mutation methods on the BaseModel) + the doc-18:123-125
      persistence discipline future Slice 18 sub-slices land at the
      loader / writer layer.
    * **AC4** -- *"Recommendations that affect runtime behavior cite
      replay results or explicitly say more evidence is needed."*
      (doc-18:165-166) -- enforced by the Slice 17 5th sub-slice
      :class:`~iriai_build_v2.execution_control.replay_requirement_hook`
      cross-reference + this :attr:`result_id` typed identifier that
      the Slice 17 recommendation surface cites via
      :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`.

    Per the auto-memory ``feedback_no_silent_degradation`` rule
    ``ConfigDict(extra="forbid")`` ensures typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-18:186-249).** :attr:`policy_provenance_refs` is a list of
    Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`; NOT
    redefined here). Per doc-13a:285-287 step 9 the shared model is
    the authority for governance evidence-ref semantics; future Slice
    18 sub-slices populate this list using the Slice 13a typed shape
    directly. This is a STRONGER contract than doc-18:86
    ``list[str]`` -- per the implementer-prompt typed-REUSE binding
    the governance-evidence-ref surface IS the Slice 13a typed
    BaseModel.

    **By-name reference contract (Slice 16 GovernanceFinding).** Per
    doc-18:94 :attr:`supporting_finding_ids` is ``list[str]`` carrying
    Slice 16
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    string references back to Slice 16 1st sub-slice findings (per
    doc-16:83 ``idempotency_key: str`` at ``finding_engine.py:443``).
    The by-name reference shape mirrors the Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_finding_ids: list[str]`
    pattern at ``policy_recommendation.py:743`` verbatim.
    """

    model_config = ConfigDict(extra="forbid")

    result_id: str
    """Doc-18:80 -- the stable result identifier string. Per the
    AC4 binding (doc-18:165-166) the result id is the typed
    cross-Slice-17 reference surface; the Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs:
    list[str]` field carries this identifier as the by-name reference
    for behavior-changing recommendations."""

    result_version: str
    """Doc-18:81 -- the versioned result-version string (per
    doc-18:128-129 *"New assumptions require a new result version."*
    the version axis lets future Slice 18 sub-slices supersede prior
    results without rewriting them).

    Per doc-18:162 AC1 *"Counterfactuals are deterministic, versioned,
    and evidence-backed."* the result-version is the "versioned" axis
    of the AC1 triple."""

    scenario_id: str
    """Doc-18:82 -- the typed reference back to the
    :attr:`CounterfactualScenario.scenario_id` the result evaluates.
    Per doc-18:127-129 the scenario id is the immutability anchor
    paired with :attr:`corpus_id`."""

    corpus_id: str
    """Doc-18:83 -- the typed reference back to the
    :attr:`ReplayCorpus.corpus_id` the result evaluates against. Per
    doc-18:127-129 *"Historical replay is immutable by corpus id and
    scenario id."* the corpus id is the immutability anchor."""

    assumptions: list[str]
    """Doc-18:84 -- the list of assumption strings the result carries
    (typically the union of the scenario assumptions plus any
    additional comparator-time assumptions).

    Per doc-18:163 AC2 *"Every result lists assumptions and validity
    limits."* the assumptions list is the typed surface that enforces
    the AC2 contract at the typed-result layer."""

    validity_limits: list[str]
    """Doc-18:85 -- the list of validity-limit strings the result
    carries (typically a union of the corpus validity limits plus any
    additional comparator-time validity constraints, e.g.
    ``["sample_size<10"]`` / ``["product_defect_window"]``).

    Per doc-18:163 AC2 *"Every result lists assumptions and validity
    limits."* the validity-limits list is the typed surface that
    enforces the AC2 contract at the typed-result layer."""

    policy_provenance_refs: list[GovernanceEvidenceRef]
    """Doc-18:86 -- the list of Slice 13a shared
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    policy-provenance evidence references.

    **Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
    doc-18:186-249).** This field is the list of Slice 13a shared
    :class:`GovernanceEvidenceRef` -- imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models`, NOT
    redefined here. Per doc-13a:285-287 step 9 the shared model is the
    authority for governance evidence-ref semantics; future Slice 18
    sub-slices populate this list using the Slice 13a typed shape
    directly.

    Per the implementer-prompt typed-REUSE binding the
    governance-evidence-ref surface IS the Slice 13a typed BaseModel
    (a STRONGER contract than doc-18:86 ``list[str]``); this mirrors
    the Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.PolicyRecommendationDecision.evidence_refs:
    list[GovernanceEvidenceRef]` pattern at
    ``policy_recommendation.py:956``.

    Per doc-18:162 AC1 *"Counterfactuals are deterministic, versioned,
    and evidence-backed."* the policy-provenance-refs list is the
    "evidence-backed" axis of the AC1 triple. Per the Slice 13A
    invariant + doc-18:186-249 the typed surface enforces the
    refs-only no-raw-body-hydration discipline at construction (the
    :class:`GovernanceEvidenceRef` BaseModel validates the typed-source
    contract; no raw evidence body is embedded in the result row)."""

    safety_guard_class: str | None = None
    """Doc-18:87 -- the optional safety-guard-class string (e.g.
    ``"fail_closed_earlier"`` / ``"reduce_mutation_authority"`` /
    ``"bounded_preflight_evidence"``).

    Per doc-18:140-146 *"Overfit risk: require at least one
    non-`8ac124d6` corpus before marking a general policy high
    confidence. A safety-guard exception is allowed only for policies
    whose sole effect is to fail closed earlier, reduce mutation
    authority, or add bounded preflight evidence. The scenario must
    set `safety_guard_class`, cite non-governance primary evidence,
    and pass a chain-depth check proving it is not derived solely
    from prior governance recommendations."* -- the safety-guard-class
    is the typed surface that enforces the doc-18:140-146 safety-guard
    exception discipline at the result layer; the future Slice 18 5th
    sub-slice safety-guard validator consults the typed field +
    cross-checks the :attr:`policy_provenance_refs` against the
    governance-only-provenance chain-depth check.

    Defaults to ``None`` (no safety-guard exception) per the doc-18:87
    ``safety_guard_class: str | None = None`` shape verbatim."""

    estimated_delta_hours: float | None
    """Doc-18:88 -- the estimated workflow hours delta the
    counterfactual policy would have produced (negative = saved
    hours; positive = additional hours; ``None`` if not quantified).

    Per doc-18:50 *"Counterfactual duration estimates may be ranges
    rather than exact values."* the typed-shape layer carries the
    central estimate as a ``float | None`` and the breadth via the
    :attr:`confidence` field; future Slice 18 sub-slices MAY tighten
    to a typed confidence-interval shape (e.g. ``EstimatedDelta``
    with ``low`` / ``mid`` / ``high`` ``float`` fields per the
    doc-15 fixture-based calibration pattern)."""

    estimated_delta_repair_cycles: float | None
    """Doc-18:89 -- the estimated repair-cycle delta the
    counterfactual policy would have produced (negative = fewer
    cycles; positive = additional cycles; ``None`` if not quantified).

    Per doc-18:50 the typed-shape layer carries the central estimate
    as a ``float | None``."""

    estimated_delta_commit_failures: float | None
    """Doc-18:90 -- the estimated commit-failure delta the
    counterfactual policy would have produced (negative = fewer
    failures; positive = additional failures; ``None`` if not
    quantified).

    Per doc-18:50 the typed-shape layer carries the central estimate
    as a ``float | None``."""

    estimated_risk_change: RiskChange
    """Doc-18:91 -- the typed risk-change classification from the
    4-value :data:`RiskChange` Literal (doc-18:91). Per Pydantic
    Literal validation the field accepts only one of the 4 values;
    unknown values fail closed with a typed ``ValidationError``."""

    confidence: float
    """Doc-18:92 -- the confidence score in the result's correctness
    (0.0 = no confidence, 1.0 = full confidence).

    Per doc-18:138 *"Small sample size: report confidence and avoid
    policy recommendations."* + doc-18:140-141 *"Overfit risk: require
    at least one non-`8ac124d6` corpus before marking a general
    policy high confidence."* the confidence is the typed surface that
    enforces the doc-18:138 + doc-18:140-141 disciplines at the
    result-row layer; the future Slice 18 5th sub-slice
    metrics-comparator + safety-guard validator enforce the per-mode
    confidence floors."""

    invalidated_by: list[str]
    """Doc-18:93 -- the list of invalidation-reason strings (e.g.
    ``["missing_evidence:typed_attempt"]`` /
    ``["product_defect_window"]`` /
    ``["governance_only_provenance_chain"]``).

    Per doc-18:134-135 *"Policy requires evidence not in corpus:
    mark invalidated and collect more evidence."* + doc-18:136-137
    *"Product defect dominates window: do not infer workflow policy
    success from a product-blocked group without separate workflow
    evidence."* the invalidated-by list is the typed audit-trail
    surface that lets reviewers + the Slice 17 recommendation
    citation hook detect non-actionable results."""

    supporting_finding_ids: list[str]
    """Doc-18:94 -- the list of Slice 16
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    string references the result cites as supporting findings.

    Per doc-18:94 the field is ``list[str]`` (just the string ids;
    NOT the typed BaseModel). The by-name reference shape mirrors the
    Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_finding_ids:
    list[str]` pattern at ``policy_recommendation.py:743`` verbatim
    and the Slice 16 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.metric_refs:
    list[str]` pattern at ``finding_engine.py:574`` verbatim.

    Per doc-18:180 *"Slice 16 supplies findings."* the
    supporting-finding-ids list is the typed reference back to Slice 16
    findings (by-name); the future Slice 18 6th sub-slice
    result-writer enforces the at-least-one-supporting-finding
    invariant when the result's
    :attr:`recommended_next_step` is ``draft_policy`` /
    ``implementation_plan`` (per doc-18:117-119 step 6 + doc-18:140-146
    safety-guard binding)."""

    recommended_next_step: RecommendedNextStep
    """Doc-18:95 -- the typed recommended-next-step classification
    from the 4-value :data:`RecommendedNextStep` Literal (doc-18:95).
    Per Pydantic Literal validation the field accepts only one of the
    4 values; unknown values fail closed with a typed
    ``ValidationError``."""


# --- Counterfactual idempotency-key helpers (mirrors Slice 13A
#     compute_completeness_digest + Slice 14 compute_payload_sha256 +
#     Slice 15 compute_scorecard_digest + Slice 16 1st sub-slice
#     compute_finding_idempotency_key + Slice 17 1st sub-slice
#     compute_policy_recommendation_idempotency_key canonical-JSON
#     discipline) ------------------------------------------------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.completeness._canonical_json`
    + :func:`iriai_build_v2.execution_control.commit_provenance._canonical_json`
    + :func:`iriai_build_v2.execution_control.governance_metrics._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._canonical_json`
    + :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates
    lexicographic key ordering and the compact separator set so the
    resulting bytes are stable across Python versions / platforms /
    dict ordering.

    Per the P3-15-1-1 + P3-16-1-1 + P3-17-1-2 lineage the
    ``default=str`` superset is benign because the canonical
    projections this module computes go through
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
    + :func:`iriai_build_v2.execution_control.policy_recommendation._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def canonical_counterfactual_dict(
    result: CounterfactualResult,
) -> dict[str, Any]:
    """Project a :class:`CounterfactualResult` to its canonical-JSON
    dict representation.

    This helper produces the canonical-dict projection used by
    :func:`compute_counterfactual_idempotency_key` (when computing a
    result's deterministic dedupe key from its logical inputs) and by
    subsequent Slice 18 sub-slices when persisting result rows at
    ``review:governance-counterfactuals:{corpus_id}`` per doc-18:117-119
    step 6.

    The projection uses :meth:`BaseModel.model_dump` with ``mode='json'``
    so any nested ``datetime`` field on the typed Slice 13a
    :class:`GovernanceEvidenceRef` evidence-ref entries projects to its
    ISO-8601 string form (cross-process stable). The resulting dict is
    the input to :func:`compute_counterfactual_idempotency_key`; both
    helpers use :func:`_canonical_json` for deterministic serialisation.

    Mirrors the Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.canonical_finding_dict`
    + Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.canonical_policy_recommendation_dict`
    patterns verbatim.
    """

    return result.model_dump(mode="json")


def compute_counterfactual_idempotency_key(
    *,
    result_version: str,
    scenario_id: str,
    corpus_id: str,
    assumptions: list[str],
    validity_limits: list[str],
    supporting_finding_ids: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    :class:`CounterfactualResult`.

    Mirrors the Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    + Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    patterns verbatim; the key is computed over the 6 logical inputs:

    * ``result_version`` -- the versioned result-version string (per
      doc-18:81 + doc-18:128-129 *"New assumptions require a new
      result version."* the version axis is part of the dedupe key
      so a new version cleanly produces a new key + a new row,
      rather than overwriting prior rows).
    * ``scenario_id`` -- the stable scenario identifier (per
      doc-18:82). The scenario is one of the immutability anchors per
      doc-18:127-129.
    * ``corpus_id`` -- the stable corpus identifier (per doc-18:83 +
      :attr:`ReplayCorpus.corpus_id`). The corpus is the other
      immutability anchor.
    * ``assumptions`` -- the list of assumption strings (per
      doc-18:84). The list is sorted before digesting so the key is
      order-invariant w.r.t. assumption ordering.
    * ``validity_limits`` -- the list of validity-limit strings (per
      doc-18:85). The list is sorted before digesting so the key is
      order-invariant w.r.t. validity-limit ordering.
    * ``supporting_finding_ids`` -- the list of Slice 16
      :class:`GovernanceFinding.idempotency_key` strings (per
      doc-18:94). The list is sorted before digesting so the key is
      order-invariant w.r.t. finding-id ordering.

    Per doc-18:128-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the helper is the cross-process freshness contract subsequent
    sub-slices rely on when detecting duplicate results across reruns
    of the comparator + result writer.

    Per doc-18:162 AC1 *"Counterfactuals are deterministic, versioned,
    and evidence-backed."* the helper is the "deterministic" axis of
    the AC1 triple; the result-version is the "versioned" axis; and
    the :attr:`CounterfactualResult.policy_provenance_refs` is the
    "evidence-backed" axis.

    Mirrors the Slice 13A
    :func:`~iriai_build_v2.execution_control.completeness.compute_completeness_digest`
    + Slice 14
    :func:`~iriai_build_v2.execution_control.commit_provenance.compute_payload_sha256`
    + Slice 15
    :func:`~iriai_build_v2.execution_control.governance_metrics.compute_scorecard_digest`
    + Slice 16 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    + Slice 17 1st sub-slice
    :func:`~iriai_build_v2.execution_control.policy_recommendation.compute_policy_recommendation_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim.
    """

    payload: dict[str, Any] = {
        "result_version": result_version,
        "scenario_id": scenario_id,
        "corpus_id": corpus_id,
        # Sort the list-of-str inputs so the key is order-invariant
        # w.r.t. list ordering (per the Slice 16 1st sub-slice
        # compute_finding_idempotency_key precedent at
        # finding_engine.py:895-906 + the Slice 17 1st sub-slice
        # compute_policy_recommendation_idempotency_key precedent at
        # policy_recommendation.py:1100-1110).
        "assumptions": sorted(assumptions),
        "validity_limits": sorted(validity_limits),
        "supporting_finding_ids": sorted(supporting_finding_ids),
    }
    return _sha256_hex(_canonical_json(payload))
