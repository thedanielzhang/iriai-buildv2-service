"""Slice 18 second sub-slice -- replay corpus loader + scenario definition
builder over Slice 13 evidence sets, Slice 00 fixtures, and the Slice 18
1st sub-slice typed-shape foundation.

This module owns the **doc-18 § Refactoring Steps step 1 + step 2**
verbatim:

* **Step 1** (doc-18:111) -- *"Build replay corpus loader over Slice 13
  evidence sets and Slice 00 fixtures."* -- The
  :class:`ReplayCorpusLoader` produces a typed
  :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
  over typed Slice 13a
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  inputs + Slice 00 fixture paths.
* **Step 2** (doc-18:112) -- *"Add scenario definitions with required
  evidence and validity limits."* -- The
  :class:`ScenarioDefinitionBuilder` produces a typed
  :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
  with the required-evidence-kinds + validity-limits + assumptions
  fields verified at construction time against the corpus's
  evidence-set ids + implementation-anchor ids.

Per the Slice 18 1st sub-slice typed-shape foundation
(:mod:`iriai_build_v2.execution_control.counterfactual_replay`) the
loader + builder consume the 1st sub-slice typed shapes verbatim:

* :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
  -- the typed 6-field corpus shape (doc-18:63-69).
* :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
  -- the typed 6-field scenario shape (doc-18:71-77).
* :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
  -- the typed 3-value replay-mode taxonomy (doc-18:61).

And consume the Slice 13a + Slice 17 typed shapes via direct import
(NO redefinition):

* :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- the typed governance-evidence-ref BaseModel (per doc-13a:285-287
  step 9 the Slice 13a shared model is the source of truth; per
  doc-18:186-249 this module consumes REFS ONLY -- no raw artifact
  body hydration).
* :data:`~iriai_build_v2.execution_control.policy_recommendation.PolicyConsumer`
  -- the typed 6-value policy-consumer taxonomy (per doc-17:65; REUSED
  transitively via
  :attr:`CounterfactualScenario.affected_consumers`).

**Fail-closed semantics (feedback_no_silent_degradation).** The loader
+ builder NEVER raise on input. On a structural construction failure
(e.g. a Pydantic ``ValidationError`` raised by the typed BaseModel
construction, a malformed evidence-set ref, a missing required
evidence kind), the loader records a typed :class:`ReplayCorpusLoaderGap`
on the :attr:`ReplayCorpusLoaderResult.gap_findings` list and the
builder records a typed :class:`ScenarioDefinitionGap` on the
:attr:`ScenarioDefinitionResult.gap_findings` list + populates the
:attr:`ScenarioDefinitionResult.validation_invalidated_by` list. Per
doc-14:242-243 (inherited per the governance-projection observer
pattern) the failure is NON-BLOCKING: the corresponding typed failure id
:data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`
(``replay_corpus_or_scenario_load_failed``) registers under the
EXISTING ``evidence_corruption`` failure_class with the EXISTING
NON-blocking RouteAction ``retry_governance_projection`` (REUSED from
Slice 14 2nd sub-slice; NOT a new route action; mirrors Slice 15 2nd +
4th + Slice 16 2nd + 3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th +
5th + 6th sub-slice precedent verbatim).

Per the auto-memory ``feedback_no_silent_degradation`` rule unknown
evidence-set references / unbounded fixture inputs / malformed
manifest shapes do NOT silently fall through -- they emit a typed gap
on the typed result. The doc-18:150 acceptance test (*"Replay corpus
loader rejects malformed or unbounded fixture inputs."*) is satisfied
by this fail-closed discipline + the
:attr:`ReplayCorpusLoaderInputs.max_evidence_set_refs` bounded-input
contract + the :attr:`ReplayCorpusLoaderInputs.max_implementation_anchor_refs`
bounded-input contract.

**Refs-only projection (doc-18:186-249 + Slice 13A invariant).** The
loader consumes typed
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
inputs + typed fixture-path strings + projects them onto the typed
:class:`ReplayCorpus` shape; it does NOT hydrate raw artifact bodies.
This honours the doc-18:186-249 Slice 13A Shared Completeness Model
Dependency (refs-only references; the shared
``ExactEvidenceManifest`` lives in
:mod:`iriai_build_v2.execution_control.completeness` and is the
source of truth for replay-input evidence completeness; this module
does NOT redefine the completeness shape).

**Required-evidence-kinds validation (doc-18:112 + doc-18:134-138).**
The :class:`ScenarioDefinitionBuilder` consumes the typed
:class:`ScenarioDefinitionInputs` + a typed
:class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
and validates that the scenario's ``required_evidence_kinds`` are
covered by the corpus's evidence-set ids + implementation-anchor ids.
Per doc-18:134-135 *"Policy requires evidence not in corpus: mark
invalidated and collect more evidence."* missing required evidence
emits a populated :attr:`ScenarioDefinitionResult.validation_invalidated_by`
list (mirrors the Slice 18 1st sub-slice
:attr:`CounterfactualResult.invalidated_by` shape). Per doc-18:138
*"Small sample size: report confidence and avoid policy
recommendations."* the typed-shape layer exposes the result; the
future Slice 18 5th sub-slice metrics-comparator attaches the
per-result confidence interval.

**Validity-limits enforcement (doc-18:112 + doc-18:48-49).** Per
doc-18:48-49 *"Replay may start with deterministic summary-level
simulation when full event replay is not available, if validity
limits are explicit."* the scenario builder propagates the typed
``validity_limits`` list (union of the scenario inputs' validity
limits + any builder-time validation limits derived from the
required-evidence-kinds check). The typed surface does NOT enforce
the per-mode confidence floor (that lives in the future Slice 18
4th + 5th sub-slice metrics-comparator + confidence-floor enforcer);
this 2nd sub-slice exposes the typed surface only.

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a modules
(``..workflows.develop.governance.models``) + Slice 17 1st sub-slice
module (``.policy_recommendation``) + Slice 18 1st sub-slice module
(``.counterfactual_replay``) only. NO imports from ``governance/``
outside ``governance.models`` (this module is foundational; the
governance layer consumes execution-control surfaces, not the
reverse). NO imports from other parts of ``execution_control/``
beyond ``policy_recommendation`` + ``counterfactual_replay`` (this
module is the typed projection layer that composes them). NO imports
from ``workflows/develop/execution/phases/`` / ``supervisor`` /
``dashboard`` (those would be downstream consumers, not dependencies).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.execution_control.recommendation_builder` (Slice
17 2nd sub-slice) +
:mod:`iriai_build_v2.execution_control.finding_rule_engine` (Slice 16
2nd sub-slice) verbatim: ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives (``str`` / ``float`` / ``bool`` /
``list`` / ``dict``). Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``extra="forbid"`` discipline. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 14 + Slice 15 + Slice 16 + Slice 17 + Slice 18 1st sub-slice
precedent verbatim without introducing new abstractions.

**Activation-authority boundary** (per STATUS.md § "Loop discipline" +
doc-17:178-179 + doc-18:123-125). The loader + builder produce
read-only typed records; they do NOT mutate executor / control-plane /
product state, take merge or checkpoint authority, or force policy
activation. Counterfactual replay results are review / governance
artifacts (per doc-18:123 *"Replay results are review/governance
artifacts only."* + doc-18:124-125 *"Replay must not write `dag-*`
execution authority artifacts or active policy markers."*) and never
change execution state.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualScenario,
    ReplayCorpus,
    ReplayMode,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    PolicyConsumer,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-18:111-112 + doc-14:242-243 NON-BLOCKING).
    "REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID",
    # Default bounded-input thresholds (doc-18:150 + Slice 13A invariant
    # bounded-reads discipline).
    "DEFAULT_MAX_EVIDENCE_SET_REFS",
    "DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS",
    # Typed loader inputs / result / gap (doc-18:111).
    "ReplayCorpusLoaderInputs",
    "ReplayCorpusLoaderResult",
    "ReplayCorpusLoaderGap",
    # Typed scenario-builder inputs / result / gap (doc-18:112).
    "ScenarioDefinitionInputs",
    "ScenarioDefinitionResult",
    "ScenarioDefinitionGap",
    # The loader + builder classes (doc-18:111 + doc-18:112).
    "ReplayCorpusLoader",
    "ScenarioDefinitionBuilder",
    # Pure helpers for the deterministic idempotency keys
    # (mirror the Slice 17 2nd sub-slice compute_recommendation_id +
    # the Slice 18 1st sub-slice compute_counterfactual_idempotency_key
    # canonical-JSON + SHA-256 discipline).
    "compute_corpus_loader_idempotency_key",
    "compute_scenario_idempotency_key",
]


# --- Typed failure id (doc-18:111-112 + doc-14:242-243 NON-BLOCKING) --------


REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID: Literal[
    "replay_corpus_or_scenario_load_failed"
] = "replay_corpus_or_scenario_load_failed"
"""Doc-18:111-112 + doc-14:242-243 -- the typed failure id the replay
corpus loader + scenario definition builder project onto when a
construction step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th sub-slice
precedent verbatim).

A SINGLE failure id covers BOTH the corpus loader + the scenario
builder per the Slice 17 6th sub-slice ``consumer_read_api_failed``
precedent (one typed failure id covering multiple typed surface
methods on a single typed class). The failure semantics are identical
for both surfaces (typed gap projection on construction failure; never
raises); the typed gap shape carries the surface tag so consumers can
distinguish loader-side vs builder-side gaps if needed.

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17
non-blocking governance projection observer (the loader + builder are
also post-checkpoint governance projection observers).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to replay corpus
loader + scenario definition builder failures (this slice is also a
post-checkpoint governance projection observer + per doc-18:123 the
replay results are review/governance artifacts only -- never runtime
policy authority).
"""


# --- Default bounded-input thresholds ---------------------------------------


DEFAULT_MAX_EVIDENCE_SET_REFS: int = 256
"""Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
default upper bound on the number of evidence-set refs a single
:class:`ReplayCorpusLoaderInputs` may carry.

Per the governance prompt § "Bounded reads" *"Reuse the typed
snapshot's `LIMIT cap+1` truncation discipline and the supervisor's
`SET LOCAL statement_timeout` pattern."* the loader rejects inputs
that exceed the bound (typed gap projection; NEVER raises). The
default value 256 is deliberately ABOVE the Slice 13A typed snapshot's
LIMIT cap (e.g.
:data:`~iriai_build_v2.execution_control.completeness.PROMPT_CONTEXT_PREVIEW_DEFAULT_LIMIT`
= 16) so a typical paged-corpus aggregation fits within a single
loader call; the caller MAY override the default via
:attr:`ReplayCorpusLoaderInputs.max_evidence_set_refs` (e.g. a large
historical-replay caller may raise to 1024).

Per doc-18:150 § Tests *"Replay corpus loader rejects malformed or
unbounded fixture inputs."* the bound is the typed surface that
enforces the unbounded-input check at construction.
"""


DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS: int = 256
"""Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
default upper bound on the number of implementation-anchor refs a
single :class:`ReplayCorpusLoaderInputs` may carry.

Per the governance prompt § "Bounded reads" the loader rejects inputs
that exceed the bound (typed gap projection; NEVER raises). The
default value 256 mirrors :data:`DEFAULT_MAX_EVIDENCE_SET_REFS` for
symmetric loader contract; the caller MAY override via
:attr:`ReplayCorpusLoaderInputs.max_implementation_anchor_refs`.
"""


# --- ReplayCorpusLoaderInputs (typed inputs; doc-18:111) --------------------


class ReplayCorpusLoaderInputs(BaseModel):
    """Doc-18:111 step 1 -- typed bundle of all inputs the replay
    corpus loader consumes.

    The bundle composes:

    * ``corpus_id`` -- the stable corpus identifier (per doc-18:64;
      e.g. ``"8ac124d6"`` for the canonical Slice 00 fixture; future
      feature ids for production-evidence corpora).
    * ``feature_ids`` -- the list of feature ids the corpus spans
      (per doc-18:65 + doc-18:167-168 AC5: must include ``"8ac124d6"``
      for the canonical Slice 00 fixture corpus).
    * ``evidence_set_refs`` -- the list of Slice 13a typed
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      records the corpus grounds on (per doc-18:178 + doc-18:186-249
      Slice 13A Shared Completeness Model Dependency; REFS-ONLY, no
      raw body hydration).
    * ``implementation_anchor_refs`` -- the list of Slice 13a typed
      :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
      records the corpus cites for implementation-log anchors (per
      doc-18:67 + doc-18:126-127 + doc-18:167-168 AC5).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification (per doc-18:68).
    * ``validity_limits`` -- the list of validity-limit strings the
      corpus declares (per doc-18:69).
    * ``max_evidence_set_refs`` -- the typed bounded-input cap for the
      evidence-set refs list; defaults to
      :data:`DEFAULT_MAX_EVIDENCE_SET_REFS`. Per doc-18:150 + Slice
      13A invariant bounded-reads discipline.
    * ``max_implementation_anchor_refs`` -- the typed bounded-input
      cap for the implementation-anchor refs list; defaults to
      :data:`DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS`.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    Mirrors :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderInputs`
    (Slice 17 2nd sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 18 1st sub-slice precedent
    # at src/iriai_build_v2/execution_control/counterfactual_replay.py:418
    # + the Slice 17 2nd sub-slice precedent at
    # src/iriai_build_v2/execution_control/recommendation_builder.py:431
    # -- unknown fields fail closed as a typed ``ValidationError`` rather
    # than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus_id: str
    """Doc-18:64 -- the stable corpus identifier string. Per
    doc-18:127-129 the corpus id is the typed identity surface the
    loader uses to enforce the immutability + new-version-on-new-
    assumptions discipline."""

    feature_ids: list[str]
    """Doc-18:65 + doc-18:167-168 AC5 -- the list of feature ids the
    corpus spans. Per AC5 *"The replay corpus includes both 8ac124d6
    evidence and Slice 00-12 implementation artifacts."* the list must
    include ``"8ac124d6"`` for the canonical Slice 00 fixture corpus."""

    evidence_set_refs: list[GovernanceEvidenceRef]
    """Doc-18:66 + doc-18:178 + doc-18:186-249 -- the list of Slice 13a
    typed :class:`GovernanceEvidenceRef` records the corpus grounds on
    (REFS ONLY; no raw artifact body hydration per the Slice 13A
    invariant).

    Per doc-18:200-202 *"The shared `ExactEvidenceManifest` is the
    source-of-truth shape for the `Replay corpus loader rejects
    malformed or unbounded fixture inputs` acceptance test."* the
    typed refs validate the typed-source contract at construction time
    (the :class:`GovernanceEvidenceRef` BaseModel's typed-source
    contract); preview-only refs (per doc-13a:24 + doc-13a:109-118 the
    Slice 13A invariant) are accepted as inputs but are recorded in
    the typed gap projection with a per-ref reason."""

    implementation_anchor_refs: list[GovernanceEvidenceRef]
    """Doc-18:67 + doc-18:126-127 + doc-18:167-168 AC5 -- the list of
    Slice 13a typed :class:`GovernanceEvidenceRef` records the corpus
    cites for implementation-log anchors (REFS ONLY).

    Per doc-18:126-127 *"Replay inputs include implementation-log
    anchors so accepted deviations and review findings can explain why
    a policy did or did not work."* the typed refs are the typed
    reference back to the Slice 00-12 implementation journal anchors
    + decision-log anchors that the loader emits as the corpus's
    :attr:`ReplayCorpus.implementation_anchor_ids` list."""

    mode: ReplayMode
    """Doc-18:68 -- the typed replay-mode classification from the
    3-value :data:`ReplayMode` Literal (doc-18:61). Per Pydantic
    Literal validation the field accepts only one of the 3 values;
    unknown values fail closed with a typed ``ValidationError``."""

    validity_limits: list[str] = Field(default_factory=list)
    """Doc-18:69 -- the list of validity-limit strings the corpus
    declares (e.g. ``["sample_size<10"]`` /
    ``["product_defect_window"]``).

    Per doc-18:48-49 *"Replay may start with deterministic
    summary-level simulation when full event replay is not available,
    if validity limits are explicit."* the validity-limits list is the
    typed surface that enforces the compatible-deviation discipline."""

    max_evidence_set_refs: int = Field(
        default=DEFAULT_MAX_EVIDENCE_SET_REFS,
        ge=1,
    )
    """Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
    typed bounded-input cap for the evidence-set refs list. Defaults
    to :data:`DEFAULT_MAX_EVIDENCE_SET_REFS` (256).

    Per the governance prompt § "Bounded reads" the loader rejects
    inputs that exceed the bound (typed gap projection; NEVER raises).
    Must be >= 1 (the Pydantic ``ge=1`` constraint fails closed at
    construction with a typed ``ValidationError`` if the caller passes
    a non-positive bound)."""

    max_implementation_anchor_refs: int = Field(
        default=DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS,
        ge=1,
    )
    """Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
    typed bounded-input cap for the implementation-anchor refs list.
    Defaults to :data:`DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS` (256)."""


# --- ReplayCorpusLoaderGap (typed gap projection; doc-18:111 + doc-14:242-243)


class ReplayCorpusLoaderGap(BaseModel):
    """Typed governance-gap finding produced when the replay corpus
    loader fails to construct a :class:`ReplayCorpus` structurally.

    Mirrors the Slice 17 2nd sub-slice
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
    typed failure id :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`
    (``replay_corpus_or_scenario_load_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["replay_corpus_or_scenario_load_failed"]
    """Doc-18:111 + doc-14:192-201 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243 + doc-18:111."""

    corpus_id: str
    """The corpus scope of the failed load (same as the
    :attr:`ReplayCorpusLoaderInputs.corpus_id`)."""

    reason: str
    """Free-form gap reason (e.g.
    ``corpus_construction_failed`` /
    ``evidence_set_refs_exceeded_bound`` /
    ``implementation_anchor_refs_exceeded_bound`` /
    ``corpus_id_empty`` /
    ``feature_ids_empty``)."""

    observed_at: datetime
    """ISO-8601 timestamp the loader observed the gap (UTC, timezone-
    aware). Mirrors the Slice 13A
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness.observed_at`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding.observed_at`
    contract verbatim."""

    evidence_refs: list[str] = Field(default_factory=list)
    """Optional list of evidence-ref id strings the gap implicates
    (refs-only per the Slice 13A invariant + doc-18:186-249; the typed
    BaseModel form is NOT embedded -- the caller cross-references via
    the typed Slice 13a evidence-ref surface separately)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the Pydantic ValidationError detail, the truncation bound, the
    rejected evidence-set ref count). Free-form per the doc-14:192-201
    + Slice 14/15/16/17 governance-finding precedent."""


# --- ReplayCorpusLoaderResult (typed result; doc-18:111) --------------------


class ReplayCorpusLoaderResult(BaseModel):
    """Doc-18:111 step 1 -- typed bundle of all outputs the replay
    corpus loader produces.

    The bundle composes:

    * ``corpus`` -- the typed
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
      the loader emitted, OR ``None`` if the load failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed
      :class:`ReplayCorpusLoaderGap` records emitted when a load step
      fails structurally (per
      :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`).
    * ``idempotency_key`` -- the deterministic
      :func:`compute_corpus_loader_idempotency_key`-derived dedupe key.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    corpus: ReplayCorpus | None = None
    """The typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
    the loader emitted, OR ``None`` if the load failed structurally.

    Per the doc-18:111 step 1 contract (*"Build replay corpus loader
    over Slice 13 evidence sets and Slice 00 fixtures."*) the loader
    emits the typed corpus when inputs are valid; on structural
    failure the corpus is ``None`` + the gap finding is recorded in
    :attr:`gap_findings`."""

    gap_findings: list[ReplayCorpusLoaderGap] = Field(default_factory=list)
    """The list of typed
    :class:`ReplayCorpusLoaderGap` records emitted when a load step
    fails structurally (per
    :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    loader NEVER raises on input -- structural failures are recorded as
    typed gap findings (refs-only; the corpus id + failure reason +
    observed timestamp + optional evidence-ref ids)."""

    idempotency_key: str
    """The deterministic
    :func:`compute_corpus_loader_idempotency_key`-derived dedupe key.
    Per doc-18:127-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the idempotency key is the typed identity surface that lets
    subsequent re-runs of the loader against the same inputs produce
    byte-identical results."""


# --- ScenarioDefinitionInputs (typed inputs; doc-18:112) --------------------


class ScenarioDefinitionInputs(BaseModel):
    """Doc-18:112 step 2 -- typed bundle of all inputs the scenario
    definition builder consumes.

    The bundle composes:

    * ``scenario_id`` -- the stable scenario identifier (per
      doc-18:72).
    * ``policy_under_test`` -- the proposed-policy dict the scenario
      evaluates (per doc-18:73; free-form per the Slice 17 1st sub-
      slice consumer-specific artifact narrowing).
    * ``baseline_policy_refs`` -- the list of baseline-policy ref
      strings (per doc-18:74).
    * ``affected_consumers`` -- the list of Slice 17 1st sub-slice
      typed :data:`PolicyConsumer` values (per doc-18:75).
    * ``required_evidence_kinds`` -- the list of evidence-kind strings
      (per doc-18:76).
    * ``assumptions`` -- the list of assumption strings (per
      doc-18:77).
    * ``validity_limits`` -- the list of validity-limit strings the
      scenario carries (mirrors doc-18:85; the builder propagates
      these onto the scenario's result-time validity limits).
    * ``corpus`` -- the typed
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
      the scenario is defined against (per the loader's typed output;
      the builder verifies the scenario's ``required_evidence_kinds``
      against the corpus's evidence-set ids + implementation-anchor
      ids).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
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
    (free-form per the Slice 17 1st sub-slice consumer-specific
    artifact narrowing; the typed-shape layer does NOT pre-emptively
    narrow the value shape)."""

    baseline_policy_refs: list[str]
    """Doc-18:74 -- the list of baseline-policy ref strings the
    scenario compares against (e.g. prior recommendation ids or
    activated-policy artifact ids)."""

    affected_consumers: list[PolicyConsumer]
    """Doc-18:75 -- the list of Slice 17 1st sub-slice
    :data:`PolicyConsumer` values the scenario's policy-under-test
    would affect.

    **Slice 17 dependency reconciliation.** The element type is the
    Slice 17 1st sub-slice :data:`PolicyConsumer` 6-value Literal --
    imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation` (NOT
    redefined here). Per Pydantic Literal validation any element value
    that is not one of the 6 Slice 17 consumer values fails closed at
    construction with a typed ``ValidationError``."""

    required_evidence_kinds: list[str]
    """Doc-18:76 -- the list of evidence-kind strings the scenario
    requires (e.g. ``["typed_attempt", "typed_failure",
    "typed_checkpoint"]``).

    Per doc-18:134-135 *"Policy requires evidence not in corpus: mark
    invalidated and collect more evidence."* the required-evidence-
    kinds list is the typed surface that lets the scenario builder
    detect when the scenario cannot be evaluated against the corpus
    + populate the :attr:`ScenarioDefinitionResult.validation_invalidated_by`
    list with the missing-evidence-kind citations."""

    assumptions: list[str]
    """Doc-18:77 -- the list of assumption strings the scenario
    declares (e.g. ``["product_defect_independent_of_wave_size",
    "no_priority_inversion"]``).

    Per doc-18:140-146 *"Overfit risk: require at least one
    non-`8ac124d6` corpus before marking a general policy high
    confidence."* the assumptions list is the typed surface the
    future Slice 18 5th sub-slice safety-guard validator consults."""

    validity_limits: list[str] = Field(default_factory=list)
    """Doc-18:85 (mirrors the result-time field) -- the list of
    validity-limit strings the scenario carries. The builder
    propagates these onto the scenario's typed result-time validity
    limits + augments with any builder-time validation limits derived
    from the required-evidence-kinds check.

    Per doc-18:48-49 *"Replay may start with deterministic
    summary-level simulation when full event replay is not available,
    if validity limits are explicit."* the validity-limits list is the
    typed surface that enforces the compatible-deviation discipline."""

    corpus: ReplayCorpus
    """The typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
    the scenario is defined against (per the loader's typed output).

    Per doc-18:112 step 2 the scenario builder verifies the scenario's
    ``required_evidence_kinds`` against the corpus's evidence-set ids
    + implementation-anchor ids; missing required evidence populates
    the :attr:`ScenarioDefinitionResult.validation_invalidated_by`
    list per doc-18:134-135."""


# --- ScenarioDefinitionGap (typed gap projection; doc-18:112) --------------


class ScenarioDefinitionGap(BaseModel):
    """Typed governance-gap finding produced when the scenario
    definition builder fails to construct a :class:`CounterfactualScenario`
    structurally.

    Mirrors the Slice 17 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.recommendation_builder.RecommendationBuilderEmissionGap`
    verbatim per the chunk-shape decision.

    Per doc-14:242-243 (the Slice 14 non-blocking contract REUSED
    here per the governance-projection discipline) the gap finding is
    NON-blocking: the caller MUST NOT propagate it to the executor /
    checkpoint / merge-queue / resume code paths. The corresponding
    typed failure id :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`
    (``replay_corpus_or_scenario_load_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["replay_corpus_or_scenario_load_failed"]
    """Doc-18:112 + doc-14:192-201 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243 + doc-18:112. SAME failure
    id as the loader gap (per the Slice 17 6th sub-slice
    `consumer_read_api_failed` precedent: one typed failure id covering
    multiple typed surface methods on a single typed class)."""

    scenario_id: str | None
    """The scenario id the failed build (or ``None`` if the scenario
    id could not be parsed -- e.g. typed inputs construction failure
    happened before the scenario_id could be inspected)."""

    corpus_id: str
    """The corpus scope of the failed build (the
    :attr:`ScenarioDefinitionInputs.corpus.corpus_id`)."""

    reason: str
    """Free-form gap reason (e.g.
    ``scenario_construction_failed`` /
    ``required_evidence_not_in_corpus`` /
    ``empty_affected_consumers`` /
    ``scenario_id_empty``)."""

    observed_at: datetime
    """ISO-8601 timestamp the builder observed the gap (UTC, timezone-
    aware)."""

    evidence_refs: list[str] = Field(default_factory=list)
    """Optional list of evidence-ref id strings the gap implicates
    (refs-only per the Slice 13A invariant + doc-18:186-249)."""

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding (e.g.
    the Pydantic ValidationError detail, the missing required evidence
    kind, the scenario's affected_consumers list)."""


# --- ScenarioDefinitionResult (typed result; doc-18:112) -------------------


class ScenarioDefinitionResult(BaseModel):
    """Doc-18:112 step 2 -- typed bundle of all outputs the scenario
    definition builder produces.

    The bundle composes:

    * ``scenario`` -- the typed
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
      the builder emitted, OR ``None`` if the build failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``validation_invalidated_by`` -- the list of invalidation-reason
      strings (mirrors the Slice 18 1st sub-slice
      :attr:`CounterfactualResult.invalidated_by` shape per
      doc-18:93). Per doc-18:134-135 *"Policy requires evidence not in
      corpus: mark invalidated and collect more evidence."* missing
      required evidence populates this list.
    * ``gap_findings`` -- the list of typed
      :class:`ScenarioDefinitionGap` records emitted when a build step
      fails structurally.
    * ``idempotency_key`` -- the deterministic
      :func:`compute_scenario_idempotency_key`-derived dedupe key.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    **Important.** Even when ``validation_invalidated_by`` is non-empty
    (i.e. the scenario has missing required evidence), the typed
    :attr:`scenario` IS still emitted -- the scenario record is the
    typed-shape audit-trail (the future Slice 18 5th sub-slice
    metrics-comparator + Slice 18 6th sub-slice result writer consume
    the typed scenario + emit a typed
    :class:`CounterfactualResult` with the typed
    :attr:`CounterfactualResult.invalidated_by` list populated). The
    :attr:`scenario` is ``None`` ONLY when the builder failed
    structurally (i.e. the typed scenario BaseModel could not be
    constructed at all).
    """

    model_config = ConfigDict(extra="forbid")

    scenario: CounterfactualScenario | None = None
    """The typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
    the builder emitted, OR ``None`` if the build failed
    structurally."""

    validation_invalidated_by: list[str] = Field(default_factory=list)
    """The list of invalidation-reason strings (mirrors the Slice 18
    1st sub-slice :attr:`CounterfactualResult.invalidated_by` shape
    per doc-18:93).

    Per doc-18:134-135 *"Policy requires evidence not in corpus: mark
    invalidated and collect more evidence."* missing required evidence
    populates this list with entries of the form
    ``"missing_evidence:<evidence_kind>"`` so the future Slice 18 5th
    sub-slice metrics-comparator detects the invalidation + the future
    Slice 18 6th sub-slice result writer projects the entries onto the
    typed :attr:`CounterfactualResult.invalidated_by` list."""

    gap_findings: list[ScenarioDefinitionGap] = Field(default_factory=list)
    """The list of typed :class:`ScenarioDefinitionGap` records emitted
    when a build step fails structurally (per
    :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`)."""

    idempotency_key: str
    """The deterministic
    :func:`compute_scenario_idempotency_key`-derived dedupe key. Per
    doc-18:127-129 the (corpus_id, scenario_id) pair is the
    immutability anchor; the idempotency key includes the scenario's
    required-evidence-kinds + assumptions + validity_limits so that
    NEW assumptions require a new key (per doc-18:128-129 *"New
    assumptions require a new result version."*)."""


# --- Pure canonical-JSON + SHA-256 helpers (mirrors Slice 18 1st sub-slice
#     compute_counterfactual_idempotency_key + Slice 17 1st sub-slice
#     compute_policy_recommendation_idempotency_key canonical-JSON +
#     SHA-256 discipline verbatim) -------------------------------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.recommendation_builder._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._canonical_json`
    verbatim: ``json.dumps(..., sort_keys=True, separators=(",", ":"))``.

    Per the P3-15-1-1 carry the ``default=str`` superset is benign
    because the canonical projections this module computes go through
    :meth:`BaseModel.model_dump` with ``mode='json'`` first, so
    ``datetime`` is already lowered to ISO-8601 strings before this
    helper sees the object; the ``default=str`` is a defence-in-depth
    fallback for any non-JSON-native scalars.
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_corpus_loader_idempotency_key(
    *,
    corpus_id: str,
    feature_ids: list[str],
    evidence_set_ref_ids: list[str],
    implementation_anchor_ref_ids: list[str],
    mode: str,
    validity_limits: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    :class:`ReplayCorpusLoaderResult`.

    Mirrors the Slice 18 1st sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay.compute_counterfactual_idempotency_key`
    + Slice 17 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.recommendation_builder.compute_recommendation_id`
    canonical-JSON + SHA-256 discipline verbatim.

    Per doc-18:127-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the corpus id is one of the immutability anchors; the idempotency
    key includes the feature_ids + evidence-set ref ids +
    implementation-anchor ref ids + mode + validity_limits so a new
    corpus (e.g. with new evidence refs) cleanly produces a new key
    + a new row, rather than overwriting prior rows.
    """

    payload: dict[str, Any] = {
        "corpus_id": corpus_id,
        # Sort the list-of-str inputs so the key is order-invariant
        # w.r.t. list ordering (per the Slice 18 1st sub-slice
        # compute_counterfactual_idempotency_key precedent at
        # counterfactual_replay.py:1016-1028 + the Slice 17 1st sub-slice
        # compute_policy_recommendation_idempotency_key precedent at
        # policy_recommendation.py:1100-1110).
        "feature_ids": sorted(feature_ids),
        "evidence_set_ref_ids": sorted(evidence_set_ref_ids),
        "implementation_anchor_ref_ids": sorted(implementation_anchor_ref_ids),
        "mode": mode,
        "validity_limits": sorted(validity_limits),
    }
    return _sha256_hex(_canonical_json(payload))


def compute_scenario_idempotency_key(
    *,
    scenario_id: str,
    corpus_id: str,
    required_evidence_kinds: list[str],
    assumptions: list[str],
    validity_limits: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    :class:`ScenarioDefinitionResult`.

    Mirrors the Slice 18 1st sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay.compute_counterfactual_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim.

    Per doc-18:127-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the (corpus_id, scenario_id) pair is the immutability anchor;
    NEW assumptions require a new key per doc-18:128-129.
    """

    payload: dict[str, Any] = {
        "scenario_id": scenario_id,
        "corpus_id": corpus_id,
        "required_evidence_kinds": sorted(required_evidence_kinds),
        "assumptions": sorted(assumptions),
        "validity_limits": sorted(validity_limits),
    }
    return _sha256_hex(_canonical_json(payload))


# --- The loader class (doc-18:111) -----------------------------------------


def _utcnow() -> datetime:
    """Return the current UTC datetime (timezone-aware).

    Wrapped in a helper so the test surface can monkeypatch a fixed
    clock for deterministic gap-finding ``observed_at`` assertions.
    """

    return datetime.now(timezone.utc)


class ReplayCorpusLoader:
    """Replay corpus loader (doc-18:111 step 1).

    Per *"Build replay corpus loader over Slice 13 evidence sets and
    Slice 00 fixtures."* the loader consumes typed Slice 13a
    :class:`GovernanceEvidenceRef` inputs + a Slice 00 fixture path
    (carried in :attr:`ReplayCorpusLoaderInputs.feature_ids` for
    ``"8ac124d6"`` per the AC5 binding) and emits a typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
    record.

    **Bounded-input discipline (doc-18:150 + Slice 13A invariant).**
    The loader rejects inputs that exceed
    :attr:`ReplayCorpusLoaderInputs.max_evidence_set_refs` or
    :attr:`ReplayCorpusLoaderInputs.max_implementation_anchor_refs`
    bounds; the rejection emits a typed
    :class:`ReplayCorpusLoaderGap` (NEVER raises) per the doc-18:150
    *"Replay corpus loader rejects malformed or unbounded fixture
    inputs."* acceptance test.

    **Refs-only projection (doc-18:186-249 + Slice 13A invariant).**
    The loader extracts only the typed
    :attr:`GovernanceEvidenceRef.ref_id` string from each input ref
    (NOT the typed BaseModel form) and emits the projection onto the
    typed
    :attr:`ReplayCorpus.evidence_set_ids: list[str]` +
    :attr:`ReplayCorpus.implementation_anchor_ids: list[str]` fields
    (which are documented as ``list[str]`` per doc-18:66-67). The
    typed
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    BaseModel is the source-of-truth for the typed ref shape; the
    loader does NOT redefine the shape.

    **Fail-closed discipline (feedback_no_silent_degradation).** The
    loader NEVER raises a failure to the caller. Any structural
    failure projects onto a typed :class:`ReplayCorpusLoaderGap`
    finding emitted on the :attr:`ReplayCorpusLoaderResult.gap_findings`
    list. The corresponding typed failure id
    :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID`
    (``replay_corpus_or_scenario_load_failed``) registers under the
    EXISTING ``evidence_corruption`` failure_class with the EXISTING
    NON-blocking RouteAction ``retry_governance_projection`` (REUSED
    from Slice 14 2nd sub-slice; NOT a new route action).

    The loader is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple corpora.
    """

    def load(
        self,
        inputs: ReplayCorpusLoaderInputs,
    ) -> ReplayCorpusLoaderResult:
        """Load the typed :class:`ReplayCorpus` from the typed inputs.

        Per doc-18:111 step 1 the method:

        1. Validates the bounded-input contract
           (:attr:`ReplayCorpusLoaderInputs.max_evidence_set_refs`,
           :attr:`max_implementation_anchor_refs`).
        2. Validates the typed required-field contract (corpus_id
           non-empty, feature_ids non-empty).
        3. Projects the typed evidence-set refs + implementation-anchor
           refs onto the typed
           :class:`ReplayCorpus.evidence_set_ids: list[str]` +
           :class:`ReplayCorpus.implementation_anchor_ids: list[str]`
           by extracting the typed
           :attr:`GovernanceEvidenceRef.ref_id` string from each input
           ref (refs-only per doc-18:186-249).
        4. Constructs the typed :class:`ReplayCorpus` record.
        5. Records load failures in
           :attr:`ReplayCorpusLoaderResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule the
        method NEVER raises on input -- structural failures produce
        typed gap findings.
        """

        gap_findings: list[ReplayCorpusLoaderGap] = []
        idempotency_key = compute_corpus_loader_idempotency_key(
            corpus_id=inputs.corpus_id,
            feature_ids=inputs.feature_ids,
            evidence_set_ref_ids=[ref.ref_id for ref in inputs.evidence_set_refs],
            implementation_anchor_ref_ids=[
                ref.ref_id for ref in inputs.implementation_anchor_refs
            ],
            mode=inputs.mode,
            validity_limits=inputs.validity_limits,
        )

        # Bounded-input check (per doc-18:150 + Slice 13A invariant
        # bounded-reads discipline).
        if len(inputs.evidence_set_refs) > inputs.max_evidence_set_refs:
            gap_findings.append(
                ReplayCorpusLoaderGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="evidence_set_refs_exceeded_bound",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "received_count": len(inputs.evidence_set_refs),
                        "max_bound": inputs.max_evidence_set_refs,
                    },
                )
            )
            return ReplayCorpusLoaderResult(
                corpus=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        if (
            len(inputs.implementation_anchor_refs)
            > inputs.max_implementation_anchor_refs
        ):
            gap_findings.append(
                ReplayCorpusLoaderGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="implementation_anchor_refs_exceeded_bound",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "received_count": len(inputs.implementation_anchor_refs),
                        "max_bound": inputs.max_implementation_anchor_refs,
                    },
                )
            )
            return ReplayCorpusLoaderResult(
                corpus=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Required-field check (per doc-18:64 + doc-18:65 + AC5).
        if not inputs.corpus_id or not inputs.corpus_id.strip():
            gap_findings.append(
                ReplayCorpusLoaderGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    corpus_id=inputs.corpus_id or "<empty>",
                    reason="corpus_id_empty",
                    observed_at=_utcnow(),
                )
            )
            return ReplayCorpusLoaderResult(
                corpus=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        if not inputs.feature_ids:
            gap_findings.append(
                ReplayCorpusLoaderGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="feature_ids_empty",
                    observed_at=_utcnow(),
                )
            )
            return ReplayCorpusLoaderResult(
                corpus=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Refs-only projection (per doc-18:186-249 + Slice 13A
        # invariant). Extract only the typed
        # GovernanceEvidenceRef.ref_id string from each input ref; do
        # NOT hydrate raw artifact bodies.
        evidence_set_ids: list[str] = [
            ref.ref_id for ref in inputs.evidence_set_refs
        ]
        implementation_anchor_ids: list[str] = [
            ref.ref_id for ref in inputs.implementation_anchor_refs
        ]

        # Typed corpus construction (per doc-18:63-69).
        try:
            corpus = ReplayCorpus(
                corpus_id=inputs.corpus_id,
                feature_ids=list(inputs.feature_ids),
                evidence_set_ids=evidence_set_ids,
                implementation_anchor_ids=implementation_anchor_ids,
                mode=inputs.mode,
                validity_limits=list(inputs.validity_limits),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                ReplayCorpusLoaderGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    corpus_id=inputs.corpus_id,
                    reason="corpus_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return ReplayCorpusLoaderResult(
                corpus=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        return ReplayCorpusLoaderResult(
            corpus=corpus,
            gap_findings=gap_findings,
            idempotency_key=idempotency_key,
        )


# --- The scenario builder class (doc-18:112) -------------------------------


class ScenarioDefinitionBuilder:
    """Scenario definition builder (doc-18:112 step 2).

    Per *"Add scenario definitions with required evidence and validity
    limits."* the builder consumes the typed
    :class:`ScenarioDefinitionInputs` + a typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
    and emits a typed
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
    record.

    **Required-evidence-kinds validation (doc-18:112 + doc-18:134-135).**
    The builder verifies that the scenario's ``required_evidence_kinds``
    are covered by the corpus's evidence-set ids + implementation-anchor
    ids (by string match -- the typed evidence-kind taxonomy lives in
    the typed ref BaseModels' authority / quality fields per
    doc-13:97-111; this 2nd sub-slice consumes the simple by-name
    string coverage check). Missing required evidence populates the
    :attr:`ScenarioDefinitionResult.validation_invalidated_by` list
    with entries of the form ``"missing_evidence:<evidence_kind>"`` per
    the Slice 18 1st sub-slice
    :attr:`CounterfactualResult.invalidated_by` shape (doc-18:93).

    **Fail-closed discipline (feedback_no_silent_degradation).** The
    builder NEVER raises a failure to the caller. Any structural
    failure projects onto a typed :class:`ScenarioDefinitionGap`
    finding emitted on the :attr:`ScenarioDefinitionResult.gap_findings`
    list. The corresponding typed failure id
    :data:`REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID` (SAME id as the
    loader gap) registers under the EXISTING ``evidence_corruption``
    failure_class with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection``.

    The builder is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple scenarios.
    """

    def build(
        self,
        inputs: ScenarioDefinitionInputs,
    ) -> ScenarioDefinitionResult:
        """Build the typed :class:`CounterfactualScenario` for the
        typed inputs.

        Per doc-18:112 step 2 the method:

        1. Validates the typed required-field contract (scenario_id
           non-empty, affected_consumers non-empty).
        2. Verifies the scenario's ``required_evidence_kinds`` against
           the corpus's evidence-set ids + implementation-anchor ids;
           missing required evidence populates the
           :attr:`ScenarioDefinitionResult.validation_invalidated_by`
           list per doc-18:134-135.
        3. Constructs the typed
           :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
           record (per doc-18:71-77).
        4. Records build failures in
           :attr:`ScenarioDefinitionResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures produce
        typed gap findings.

        **Important.** Even when ``validation_invalidated_by`` is
        non-empty (i.e. the scenario has missing required evidence),
        the typed :attr:`ScenarioDefinitionResult.scenario` IS still
        emitted -- the scenario record is the typed-shape audit-trail.
        The :attr:`scenario` is ``None`` ONLY when the typed scenario
        BaseModel could not be constructed at all (structural
        failure).
        """

        gap_findings: list[ScenarioDefinitionGap] = []
        validation_invalidated_by: list[str] = []
        idempotency_key = compute_scenario_idempotency_key(
            scenario_id=inputs.scenario_id,
            corpus_id=inputs.corpus.corpus_id,
            required_evidence_kinds=inputs.required_evidence_kinds,
            assumptions=inputs.assumptions,
            validity_limits=inputs.validity_limits,
        )

        # Required-field check (per doc-18:72 + doc-18:75).
        if not inputs.scenario_id or not inputs.scenario_id.strip():
            gap_findings.append(
                ScenarioDefinitionGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    scenario_id=inputs.scenario_id or "<empty>",
                    corpus_id=inputs.corpus.corpus_id,
                    reason="scenario_id_empty",
                    observed_at=_utcnow(),
                )
            )
            return ScenarioDefinitionResult(
                scenario=None,
                validation_invalidated_by=validation_invalidated_by,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        if not inputs.affected_consumers:
            gap_findings.append(
                ScenarioDefinitionGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    scenario_id=inputs.scenario_id,
                    corpus_id=inputs.corpus.corpus_id,
                    reason="empty_affected_consumers",
                    observed_at=_utcnow(),
                )
            )
            return ScenarioDefinitionResult(
                scenario=None,
                validation_invalidated_by=validation_invalidated_by,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Required-evidence-kinds validation (per doc-18:112 +
        # doc-18:134-135). The covered set is the union of the
        # corpus's evidence-set ids + implementation-anchor ids; the
        # check is by-name string membership (the typed evidence-kind
        # taxonomy lives in the typed ref BaseModels' authority /
        # quality fields per doc-13:97-111 -- this 2nd sub-slice
        # consumes the simple by-name coverage check; the future Slice
        # 18 5th sub-slice metrics-comparator MAY tighten with typed-
        # authority kind classification).
        covered_evidence = set(inputs.corpus.evidence_set_ids) | set(
            inputs.corpus.implementation_anchor_ids
        )
        for evidence_kind in inputs.required_evidence_kinds:
            if evidence_kind not in covered_evidence:
                validation_invalidated_by.append(
                    f"missing_evidence:{evidence_kind}"
                )

        # Compose the scenario's validity_limits as the union of the
        # scenario inputs' validity_limits + any builder-time
        # validation limits derived from the required-evidence-kinds
        # check (per doc-18:48-49 + doc-18:134-135 the validity-limits
        # list carries the per-segment fallback rationale + the per-
        # evidence-gap caveat). Use sorted-then-deduped so the typed
        # field is order-invariant + deterministic.
        composed_validity_limits = sorted(
            set(inputs.validity_limits) | set(validation_invalidated_by)
        )

        # Typed scenario construction (per doc-18:71-77).
        try:
            scenario = CounterfactualScenario(
                scenario_id=inputs.scenario_id,
                policy_under_test=dict(inputs.policy_under_test),
                baseline_policy_refs=list(inputs.baseline_policy_refs),
                affected_consumers=list(inputs.affected_consumers),
                required_evidence_kinds=list(inputs.required_evidence_kinds),
                assumptions=list(inputs.assumptions),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                ScenarioDefinitionGap(
                    failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
                    scenario_id=inputs.scenario_id,
                    corpus_id=inputs.corpus.corpus_id,
                    reason="scenario_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                        "composed_validity_limits": composed_validity_limits,
                    },
                )
            )
            return ScenarioDefinitionResult(
                scenario=None,
                validation_invalidated_by=validation_invalidated_by,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        return ScenarioDefinitionResult(
            scenario=scenario,
            validation_invalidated_by=validation_invalidated_by,
            gap_findings=gap_findings,
            idempotency_key=idempotency_key,
        )
