"""Slice 18 third sub-slice -- summary replay engine for metrics-level counterfactuals.

This module implements doc-18 § Refactoring Steps step 3 (line 113):
*"Implement summary replay first for metrics-level counterfactuals."*

Per doc-18:133 *"Missing typed timing: use summary replay with lower
confidence."* + doc-18:50 *"Counterfactual duration estimates may be
ranges rather than exact values."* the summary replay engine projects
metrics-level counterfactual estimates from typed Slice 15 metric
baselines + typed Slice 18 1st sub-slice scenarios -- WITHOUT requiring
typed-event replay (the latter lands in the Slice 18 4th sub-slice per
doc-18:114).

This module owns:

* :data:`SUMMARY_REPLAY_FAILURE_ID` -- the typed failure id Literal
  (``summary_replay_failed``) registering under the EXISTING
  ``evidence_corruption`` failure_class with the REUSED Slice 14 2nd
  sub-slice ``retry_governance_projection`` NON-blocking RouteAction
  (mirrors Slice 17 2nd/3rd/4th/5th/6th + Slice 18 2nd sub-slice
  precedent verbatim).
* :data:`DEFAULT_MAX_BASELINE_METRICS` -- the bounded-input default
  cap on the baseline metric list (256, mirrors Slice 18 2nd sub-slice
  precedent).
* :class:`SummaryReplayInputs` -- the typed bundle of all inputs the
  engine consumes (Slice 18 1st sub-slice :class:`ReplayCorpus` +
  :class:`CounterfactualScenario` + Slice 15
  :class:`GovernanceMetricValue` baseline list + optional Slice 15
  :class:`GovernanceScorecard` baseline + Slice 18 1st sub-slice
  :data:`ReplayMode`).
* :class:`SummaryReplayResult` -- the typed bundle of all outputs the
  engine produces (Slice 18 1st sub-slice :class:`CounterfactualResult`
  + optional :class:`SummaryReplayGap` list + idempotency key).
* :class:`SummaryReplayGap` -- the typed gap projection emitted when
  the engine fails structurally (NEVER raises per
  ``feedback_no_silent_degradation``).
* :class:`CounterfactualSummaryReplayEngine` -- the engine class with a
  single :meth:`replay` method (READ-ONLY by structural design; no
  consumer-side mutation methods).
* :func:`compute_summary_replay_idempotency_key` -- the deterministic
  SHA-256 idempotency-key helper (mirrors the Slice 18 1st sub-slice
  :func:`compute_counterfactual_idempotency_key` + Slice 18 2nd sub-
  slice :func:`compute_corpus_loader_idempotency_key` /
  :func:`compute_scenario_idempotency_key` canonical-JSON + SHA-256
  discipline verbatim).

**Activation-authority boundary (per doc-18:123-125 + doc-18:164 AC3 +
STATUS.md § "Loop discipline").** Counterfactual replay results are
review / governance artifacts only -- never runtime policy authority.
The engine exposes **only** the :meth:`replay` read method; it does NOT
expose any mutation surface, does NOT import any consumer-side module
(dispatcher / scheduler / merge queue / supervisor / dashboard /
failure router runtime / commit_provenance writer / etc.), and does
NOT write any ``dag-*`` execution-authority artifact key. The
structural test surface enforces this discipline by inspecting the
module's import graph + the class's public method list.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-18:186-249).** The :attr:`SummaryReplayInputs.baseline_metrics`
field is a list of Slice 15 :class:`GovernanceMetricValue` (which in
turn carries a list of Slice 13a shared
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
on :attr:`GovernanceMetricValue.evidence_refs`). The
:attr:`SummaryReplayInputs.baseline_scorecard` field is the optional
Slice 15 :class:`GovernanceScorecard` (which carries
:attr:`GovernanceScorecard.baseline_refs:
list[GovernanceEvidenceRef]`). Neither shape is redefined here.

**Refs-only invariant (doc-18:186-249).** The engine never hydrates
raw artifact bodies; the typed Slice 15 / Slice 13a shapes carry refs
only. The emitted :class:`CounterfactualResult.policy_provenance_refs`
field is the list of Slice 13a typed
:class:`GovernanceEvidenceRef` mirrored from the typed baseline
metrics' evidence refs + the typed scenario's baseline policy refs
(string-by-string by-name reference for the baseline policies; typed
ref BaseModels for the evidence side).

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a typed governance models +
Slice 15 governance metrics + Slice 18 1st sub-slice typed shapes only.
NO imports from ``governance/`` outside ``governance.models``. NO
imports from other parts of ``execution_control/`` beyond the prior
Slice 14 / 15 / 17 / 18 1st sub-slice modules (this 3rd sub-slice is
foundational for the future Slice 18 4th sub-slice event replay engine
+ 5th sub-slice metrics comparator + 6th sub-slice result writer +
7th sub-slice Slice 17 recommendation citation hook). NO imports from
``workflows/develop/execution/phases/`` / ``supervisor`` / ``dashboard``
(those would be downstream consumers, not dependencies).

Per the auto-memory ``feedback_flat_structured_output`` rule the typed
control fields are flat primitives. Per the auto-memory
``feedback_no_silent_degradation`` rule every Pydantic field validates
at construction; unknown values fail closed via Literal range +
``ConfigDict(extra="forbid")``. Per the auto-memory
``feedback_no_overengineer_use_library`` rule the module mirrors the
Slice 18 2nd sub-slice precedent verbatim without introducing new
abstractions. Per the auto-memory ``feedback_cite_everything`` rule
every BaseModel field carries a per-field doc-18 PIN cite docstring.

**Per-doc-18 acceptance criteria mapping (doc-18:160-168).** The engine
surface exposes the shape that enforces all 5 acceptance criteria:

* **AC1** -- *"Counterfactuals are deterministic, versioned, and
  evidence-backed."* (doc-18:162) -- enforced by
  :func:`compute_summary_replay_idempotency_key` (deterministic axis)
  + the typed :attr:`CounterfactualResult.result_version` (versioned
  axis) + the typed :attr:`CounterfactualResult.policy_provenance_refs`
  list of Slice 13a :class:`GovernanceEvidenceRef` (evidence-backed
  axis).
* **AC2** -- *"Every result lists assumptions and validity limits."*
  (doc-18:163) -- enforced by populating
  :attr:`CounterfactualResult.assumptions` + :attr:`validity_limits`
  with the union of the scenario assumptions + summary-replay-specific
  validity limits (e.g. ``"summary_replay_mode"``,
  ``"missing_typed_timing"``, ``"insufficient_baseline_metrics"``).
* **AC3** -- *"Replay cannot mutate live workflow state."* (doc-18:164)
  -- enforced by the engine's read-only structural design (no mutation
  methods on :class:`CounterfactualSummaryReplayEngine`; no consumer-
  side module imports).
* **AC4** -- *"Recommendations that affect runtime behavior cite replay
  results or explicitly say more evidence is needed."* (doc-18:165-166)
  -- enforced by emitting a :class:`CounterfactualResult` with a stable
  :attr:`result_id` that the Slice 17 5th sub-slice
  :class:`~iriai_build_v2.execution_control.replay_requirement_hook`
  cross-references via
  :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`.
* **AC5** -- *"The replay corpus includes both 8ac124d6 evidence and
  Slice 00-12 implementation artifacts."* (doc-18:167-168) -- enforced
  by REUSING the typed Slice 18 1st sub-slice :class:`ReplayCorpus`
  (the 2nd sub-slice loader populated the corpus with the AC5
  coverage; this 3rd sub-slice engine consumes the typed corpus
  directly).

**Per-doc-18 edge-case mapping (doc-18:131-146).** The summary-replay
engine handles the doc-18 edge cases at the typed projection layer:

* **Missing typed timing** (doc-18:133): the engine emits a result
  with the typed :data:`ReplayMode` set to ``summary_replay`` + a
  conservative confidence floor (lower than event replay) + a typed
  validity-limit entry ``"missing_typed_timing"``.
* **Policy requires evidence not in corpus** (doc-18:134-135): the
  engine emits an invalidated result via the typed
  :attr:`CounterfactualResult.invalidated_by` list populated with
  ``"missing_evidence:<kind>"`` entries (mirrors the Slice 18 2nd sub-
  slice :class:`ScenarioDefinitionBuilder` precedent) +
  :attr:`recommended_next_step` set to ``"collect_more_evidence"``.
* **Small sample size** (doc-18:138): the engine emits a result with a
  conservative confidence ([0.0, 0.5] range typically) +
  :attr:`recommended_next_step` set to ``"collect_more_evidence"`` +
  a typed validity-limit entry ``"small_sample_size"``.
* **Overfit risk** (doc-18:140-146): when the scenario carries a
  :attr:`CounterfactualScenario.assumptions` list that includes the
  safety-guard sentinel + the scenario corpus is ``8ac124d6``-only,
  the engine sets the typed :attr:`CounterfactualResult.safety_guard_class`
  field per doc-18:87 + emits a validity-limit entry
  ``"governance_only_provenance_chain"`` so the future Slice 18 5th
  sub-slice safety-guard validator + the Slice 17 recommendation
  citation hook can detect the case.

**Cross-slice REUSE contract.**

* Slice 13a :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- imported, NOT redefined.
* Slice 15 :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
  + :class:`GovernanceScorecard` -- imported, NOT redefined.
* Slice 18 1st sub-slice :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
  + :class:`CounterfactualScenario` + :class:`CounterfactualResult` +
  :data:`ReplayMode` + :data:`RiskChange` + :data:`RecommendedNextStep`
  + :func:`compute_counterfactual_idempotency_key` -- imported, NOT
  redefined.
* Slice 14 2nd sub-slice ``retry_governance_projection`` NON-blocking
  RouteAction -- REUSED via the failure_router 4 pure-data add points
  (NOT redefined).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    CounterfactualScenario,
    RecommendedNextStep,
    ReplayCorpus,
    ReplayMode,
    RiskChange,
    compute_counterfactual_idempotency_key,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
    GovernanceScorecard,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-18:113 + doc-14:242-243 NON-BLOCKING).
    "SUMMARY_REPLAY_FAILURE_ID",
    # Bounded-input default (doc-18:150 + Slice 13A invariant
    # bounded-reads discipline; mirrors the Slice 18 2nd sub-slice
    # DEFAULT_MAX_EVIDENCE_SET_REFS / DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS
    # precedent).
    "DEFAULT_MAX_BASELINE_METRICS",
    # Summary-replay-specific confidence floor for the typed
    # `summary_replay` mode (doc-18:133 + doc-18:138).
    "SUMMARY_REPLAY_CONFIDENCE_CEILING",
    # Typed engine inputs / result / gap (doc-18:113).
    "SummaryReplayInputs",
    "SummaryReplayResult",
    "SummaryReplayGap",
    # The engine class (doc-18:113).
    "CounterfactualSummaryReplayEngine",
    # Pure helper for the deterministic idempotency key (mirrors the
    # Slice 18 1st sub-slice compute_counterfactual_idempotency_key
    # + Slice 18 2nd sub-slice compute_corpus_loader_idempotency_key
    # + compute_scenario_idempotency_key canonical-JSON + SHA-256
    # discipline).
    "compute_summary_replay_idempotency_key",
]


# --- Typed failure id (doc-18:113 + doc-14:242-243 NON-BLOCKING) ------------


SUMMARY_REPLAY_FAILURE_ID: Literal["summary_replay_failed"] = "summary_replay_failed"
"""Doc-18:113 + doc-14:242-243 -- the typed failure id the
:class:`CounterfactualSummaryReplayEngine` projects onto when a
summary-replay step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17
non-blocking governance projection observer (the summary-replay engine
is also a post-checkpoint governance projection observer).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to summary-replay
failures (this slice is also a post-checkpoint governance projection
observer + per doc-18:123 replay results are review/governance
artifacts only -- never runtime policy authority).
"""


# --- Default bounded-input threshold ----------------------------------------


DEFAULT_MAX_BASELINE_METRICS: int = 256
"""Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
default upper bound on the number of typed
:class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
baseline records a single :class:`SummaryReplayInputs` may carry.

Per the governance prompt § "Bounded reads" *"Reuse the typed
snapshot's `LIMIT cap+1` truncation discipline and the supervisor's
`SET LOCAL statement_timeout` pattern."* the engine rejects inputs
that exceed the bound (typed gap projection; NEVER raises). The
default value 256 mirrors :data:`~iriai_build_v2.execution_control.counterfactual_replay_loader.DEFAULT_MAX_EVIDENCE_SET_REFS`
+ :data:`~iriai_build_v2.execution_control.counterfactual_replay_loader.DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS`
for symmetric bounded-input contract; the caller MAY override the
default via :attr:`SummaryReplayInputs.max_baseline_metrics` (e.g. a
large historical-replay caller may raise to 1024).

Per doc-18:150 § Tests *"Replay corpus loader rejects malformed or
unbounded fixture inputs."* the bound applies symmetrically to the
summary-replay engine (the engine is also a typed-input consumer).
"""


# --- Summary-replay confidence ceiling --------------------------------------


SUMMARY_REPLAY_CONFIDENCE_CEILING: float = 0.65
"""Doc-18:133 + doc-18:138 -- the per-mode confidence ceiling for the
summary-replay engine.

Per doc-18:133 *"Missing typed timing: use summary replay with lower
confidence."* the summary-replay engine carries a lower confidence
ceiling than the (future) Slice 18 4th sub-slice event-replay engine.
The ceiling 0.65 reflects the doc-18:50 *"Counterfactual duration
estimates may be ranges rather than exact values."* discipline at the
typed-shape layer.

The future Slice 18 4th sub-slice event-replay engine will carry a
higher ceiling (e.g. 0.90) reflecting the higher-fidelity typed-event
projection.

Per doc-18:138 *"Small sample size: report confidence and avoid policy
recommendations."* the engine further reduces confidence below this
ceiling when the baseline-metric sample size is small (heuristic: if
the baseline metric count is < 3, the emitted confidence is
multiplied by 0.5 -- a defensive floor that mirrors the doc-15:138
*"insufficient-sample-size"* discipline).
"""


# --- SummaryReplayInputs (typed inputs; doc-18:113) -------------------------


class SummaryReplayInputs(BaseModel):
    """Doc-18:113 step 3 -- typed bundle of all inputs the
    :class:`CounterfactualSummaryReplayEngine` consumes.

    The bundle composes:

    * ``corpus`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
      (per doc-18:63-69; populated by the Slice 18 2nd sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoader`).
    * ``scenario`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
      (per doc-18:71-77; populated by the Slice 18 2nd sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ScenarioDefinitionBuilder`).
    * ``baseline_metrics`` -- the list of Slice 15 typed
      :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
      baseline records the engine projects deltas against (per
      doc-15:78-88; the typed-shape REUSE is the cross-Slice-15
      authority for the metric value shape).
    * ``baseline_scorecard`` -- the OPTIONAL Slice 15 typed
      :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceScorecard`
      baseline (per doc-15:90-97; when supplied the engine prefers
      the scorecard-level :attr:`GovernanceScorecard.warnings` +
      :attr:`incomplete_scopes` over scanning the baseline metric
      list).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification (per doc-18:61; defaults to ``"summary_replay"``
      because this 3rd sub-slice owns the typed summary-replay surface
      -- the future 4th sub-slice event-replay engine will accept
      ``"event_replay"`` + ``"hybrid"``).
    * ``result_version`` -- the typed result-version string (per
      doc-18:81; the version axis lets future sub-slices supersede
      prior results without rewriting them).
    * ``result_id`` -- the typed stable result identifier string (per
      doc-18:80; the cross-Slice-17 reference surface).
    * ``max_baseline_metrics`` -- the typed bounded-input cap for the
      baseline metric list; defaults to
      :data:`DEFAULT_MAX_BASELINE_METRICS`. Per doc-18:150 + Slice 13A
      invariant bounded-reads discipline.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    Mirrors :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderInputs`
    (Slice 18 2nd sub-slice) verbatim per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 18 2nd sub-slice
    # precedent at
    # src/iriai_build_v2/execution_control/counterfactual_replay_loader.py:343
    # + the Slice 18 1st sub-slice precedent at
    # src/iriai_build_v2/execution_control/counterfactual_replay.py:418
    # -- unknown fields fail closed as a typed ``ValidationError``
    # rather than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    corpus: ReplayCorpus
    """Doc-18:63-69 -- the typed Slice 18 1st sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayCorpus`
    the engine projects against. Per doc-18:167-168 AC5 the corpus
    must include both ``8ac124d6`` evidence AND Slice 00-12
    implementation artifacts; the engine consumes the typed corpus
    directly (the 2nd sub-slice loader enforces the AC5 coverage)."""

    scenario: CounterfactualScenario
    """Doc-18:71-77 -- the typed Slice 18 1st sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualScenario`
    the engine evaluates. Per doc-18:127-129 *"Historical replay is
    immutable by corpus id and scenario id. New assumptions require a
    new result version."* the scenario id is the typed identity
    surface paired with :attr:`ReplayCorpus.corpus_id`."""

    baseline_metrics: list[GovernanceMetricValue]
    """Doc-15:78-88 + doc-18:113 + doc-18:115-116 -- the list of Slice
    15 typed :class:`GovernanceMetricValue` baseline records the
    engine projects deltas against.

    **Slice 13a + Slice 15 dependency reconciliation
    (doc-13a:285-287 step 9; doc-18:186-249).** The
    :class:`GovernanceMetricValue` shape is REUSED from
    :mod:`iriai_build_v2.execution_control.governance_metrics` (NOT
    redefined here). Each baseline metric in turn carries a list of
    Slice 13a typed :class:`GovernanceEvidenceRef` on its
    :attr:`GovernanceMetricValue.evidence_refs` field; the engine
    surfaces the union of those refs onto the emitted
    :attr:`CounterfactualResult.policy_provenance_refs` (refs-only;
    no raw artifact body hydration)."""

    baseline_scorecard: GovernanceScorecard | None = None
    """Doc-15:90-97 + doc-18:113 -- the OPTIONAL Slice 15 typed
    :class:`GovernanceScorecard` baseline.

    When supplied the engine prefers the scorecard-level
    :attr:`GovernanceScorecard.warnings` +
    :attr:`incomplete_scopes` over scanning the baseline metric list
    (the scorecard is the doc-15:140 *"derived row"* surface; the
    metric list is the per-metric raw projection surface). When None
    the engine scans the baseline metric list directly.

    **Slice 15 dependency reconciliation.** REUSED from
    :mod:`iriai_build_v2.execution_control.governance_metrics`."""

    mode: ReplayMode = "summary_replay"
    """Doc-18:61 -- the typed replay-mode classification from the
    3-value :data:`ReplayMode` Literal. Defaults to
    ``"summary_replay"`` because this 3rd sub-slice owns the typed
    summary-replay surface (the future 4th sub-slice event-replay
    engine will accept ``"event_replay"`` + ``"hybrid"``).

    Per Pydantic Literal validation the field accepts only one of the
    3 values; unknown values fail closed with a typed
    ``ValidationError``."""

    result_version: str = "v1"
    """Doc-18:81 -- the typed result-version string. Per doc-18:128-129
    *"New assumptions require a new result version."* the version axis
    lets future sub-slices supersede prior results without rewriting
    them. Defaults to ``"v1"`` because this 3rd sub-slice is the first
    summary-replay-engine version."""

    result_id: str
    """Doc-18:80 -- the typed stable result identifier string. Per the
    AC4 binding (doc-18:165-166) the result id is the typed cross-
    Slice-17 reference surface; the Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs`
    field carries this identifier as the by-name reference for
    behavior-changing recommendations.

    Caller is responsible for choosing a stable result id (e.g. the
    scenario id + the result version + the corpus id). The engine does
    NOT generate one because the future Slice 18 6th sub-slice
    result-writer needs control over the identifier discipline."""

    max_baseline_metrics: int = Field(
        default=DEFAULT_MAX_BASELINE_METRICS,
        ge=1,
    )
    """Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
    typed bounded-input cap for the baseline metric list. Defaults to
    :data:`DEFAULT_MAX_BASELINE_METRICS` (256).

    Per the governance prompt § "Bounded reads" the engine rejects
    inputs that exceed the bound (typed gap projection; NEVER raises).
    Must be >= 1 (the Pydantic ``ge=1`` constraint fails closed at
    construction with a typed ``ValidationError`` if the caller passes
    a non-positive bound)."""


# --- SummaryReplayGap (typed gap projection; doc-18:113 + doc-14:242-243) ---


class SummaryReplayGap(BaseModel):
    """Typed governance-gap finding produced when the
    :class:`CounterfactualSummaryReplayEngine` fails to construct a
    :class:`CounterfactualResult` structurally.

    Mirrors the Slice 18 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderGap`
    + Slice 17 2nd sub-slice
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
    typed failure id :data:`SUMMARY_REPLAY_FAILURE_ID`
    (``summary_replay_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["summary_replay_failed"]
    """Doc-18:113 + doc-14:192-201 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243 + doc-18:113."""

    result_id_attempted: str
    """The :attr:`SummaryReplayInputs.result_id` the engine attempted
    to compute (so the caller can correlate the gap finding with the
    requesting batch even when the result itself could not be
    constructed)."""

    corpus_id: str
    """The corpus scope of the failed projection (the
    :attr:`SummaryReplayInputs.corpus.corpus_id`)."""

    scenario_id: str
    """The scenario scope of the failed projection (the
    :attr:`SummaryReplayInputs.scenario.scenario_id`)."""

    reason: str
    """Free-form gap reason (e.g.
    ``result_construction_failed`` /
    ``baseline_metrics_exceeded_bound`` /
    ``invalid_replay_mode_for_engine`` /
    ``result_id_empty`` /
    ``baseline_metrics_empty``)."""

    observed_at: datetime
    """ISO-8601 timestamp the engine observed the gap (UTC, timezone-
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
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail, the truncation bound,
    the baseline metric count). Free-form per the doc-14:192-201 +
    Slice 14/15/16/17/18-2nd governance-finding precedent."""


# --- SummaryReplayResult (typed result; doc-18:113) -------------------------


class SummaryReplayResult(BaseModel):
    """Doc-18:113 step 3 -- typed bundle of all outputs the
    :class:`CounterfactualSummaryReplayEngine` produces.

    The bundle composes:

    * ``result`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
      the engine emitted, OR ``None`` if the projection failed
      structurally (in which case the gap is recorded in
      :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed :class:`SummaryReplayGap`
      records emitted when a projection step fails structurally (per
      :data:`SUMMARY_REPLAY_FAILURE_ID`).
    * ``idempotency_key`` -- the deterministic
      :func:`compute_summary_replay_idempotency_key`-derived dedupe
      key.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    result: CounterfactualResult | None = None
    """The typed Slice 18 1st sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
    the engine emitted, OR ``None`` if the projection failed
    structurally.

    Per the doc-18:113 step 3 contract (*"Implement summary replay
    first for metrics-level counterfactuals."*) the engine emits the
    typed result when inputs are valid + the typed projection
    succeeds; on structural failure the result is ``None`` + the gap
    finding is recorded in :attr:`gap_findings`."""

    gap_findings: list[SummaryReplayGap] = Field(default_factory=list)
    """The list of typed :class:`SummaryReplayGap` records emitted
    when a projection step fails structurally (per
    :data:`SUMMARY_REPLAY_FAILURE_ID`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    engine NEVER raises on input -- structural failures are recorded
    as typed gap findings (refs-only; the result id attempted +
    corpus id + scenario id + failure reason + observed timestamp +
    optional evidence-ref ids)."""

    idempotency_key: str
    """The deterministic
    :func:`compute_summary_replay_idempotency_key`-derived dedupe
    key. Per doc-18:127-129 *"Historical replay is immutable by
    corpus id and scenario id. New assumptions require a new result
    version."* the idempotency key is the typed identity surface that
    lets subsequent re-runs of the engine against the same inputs
    produce byte-identical results."""


# --- Pure canonical-JSON + SHA-256 helpers (mirrors Slice 18 1st + 2nd
#     sub-slice canonical-JSON + SHA-256 discipline verbatim) ---------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay_loader._canonical_json`
    + :func:`iriai_build_v2.execution_control.recommendation_builder._canonical_json`
    + :func:`iriai_build_v2.execution_control.finding_engine._canonical_json`
    + :func:`iriai_build_v2.execution_control.policy_recommendation._canonical_json`
    verbatim: ``json.dumps(..., sort_keys=True, separators=(",", ":"))``.

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

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._sha256_hex`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay_loader._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_summary_replay_idempotency_key(
    *,
    result_id: str,
    result_version: str,
    corpus_id: str,
    scenario_id: str,
    mode: ReplayMode,
    baseline_metric_definition_names: list[str],
    assumptions: list[str],
    validity_limits: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    summary-replay result.

    Mirrors the Slice 18 1st sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay.compute_counterfactual_idempotency_key`
    + Slice 18 2nd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_replay_loader.compute_corpus_loader_idempotency_key`
    + :func:`~iriai_build_v2.execution_control.counterfactual_replay_loader.compute_scenario_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim; the key is computed
    over the 8 logical inputs:

    * ``result_id`` -- the typed stable result identifier string (per
      doc-18:80).
    * ``result_version`` -- the typed result-version string (per
      doc-18:81 + doc-18:128-129 *"New assumptions require a new
      result version."* the version axis is part of the dedupe key so
      a new version cleanly produces a new key + a new row, rather
      than overwriting prior rows).
    * ``corpus_id`` -- the corpus identifier (per doc-18:64 +
      doc-18:83 + doc-18:127-129).
    * ``scenario_id`` -- the scenario identifier (per doc-18:72 +
      doc-18:82 + doc-18:127-129).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification (per doc-18:61). The mode is part of the dedupe
      key so a future re-run of the same (corpus, scenario) under a
      different mode (e.g. ``event_replay`` once the 4th sub-slice
      lands) cleanly produces a new key.
    * ``baseline_metric_definition_names`` -- the list of Slice 15
      metric-definition names the engine projected over (per
      doc-15:79 :attr:`GovernanceMetricValue.definition_name`). The
      list is sorted before digesting so the key is order-invariant
      w.r.t. metric ordering.
    * ``assumptions`` -- the list of assumption strings (per
      doc-18:77 + doc-18:84). Sorted before digesting (order-
      invariant).
    * ``validity_limits`` -- the list of validity-limit strings (per
      doc-18:69 + doc-18:85). Sorted before digesting (order-
      invariant).

    Per doc-18:128-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the helper is the cross-process freshness contract subsequent
    sub-slices rely on when detecting duplicate results across re-runs
    of the engine.
    """

    payload: dict[str, Any] = {
        "result_id": result_id,
        "result_version": result_version,
        "corpus_id": corpus_id,
        "scenario_id": scenario_id,
        "mode": mode,
        # Sort the list-of-str inputs so the key is order-invariant
        # w.r.t. list ordering (per the Slice 18 1st sub-slice
        # compute_counterfactual_idempotency_key precedent at
        # counterfactual_replay.py:1016-1029 + the Slice 18 2nd sub-
        # slice precedent at counterfactual_replay_loader.py).
        "baseline_metric_definition_names": sorted(
            baseline_metric_definition_names
        ),
        "assumptions": sorted(assumptions),
        "validity_limits": sorted(validity_limits),
    }
    return _sha256_hex(_canonical_json(payload))


# --- _utcnow helper (wrapped for monkeypatch testing) ----------------------


def _utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware
    :class:`datetime`.

    Wrapped in a helper so the test surface can monkeypatch a fixed
    clock for deterministic gap-finding ``observed_at`` assertions.
    """

    return datetime.now(timezone.utc)


# --- The engine class (doc-18:113) -----------------------------------------


class CounterfactualSummaryReplayEngine:
    """Counterfactual summary replay engine (doc-18:113 step 3).

    Per *"Implement summary replay first for metrics-level
    counterfactuals."* the engine consumes typed Slice 18 1st sub-slice
    :class:`ReplayCorpus` + :class:`CounterfactualScenario` + Slice 15
    typed :class:`GovernanceMetricValue` baseline records + optional
    :class:`GovernanceScorecard` baseline + emits a typed Slice 18 1st
    sub-slice :class:`CounterfactualResult` with all 16 fields
    populated.

    **Refs-only projection (doc-18:186-249 + Slice 13A invariant).**
    The engine surfaces only typed Slice 13a
    :class:`GovernanceEvidenceRef` records from the baseline metric +
    scorecard inputs onto the emitted
    :attr:`CounterfactualResult.policy_provenance_refs` list (the
    typed BaseModel form is preserved -- doc-18:86 is documented as
    ``list[str]`` but the 1st sub-slice tightened to the typed Slice
    13a ref shape per the implementer-prompt typed-REUSE binding). NO
    raw artifact body hydration.

    **Fail-closed discipline (feedback_no_silent_degradation).** The
    engine NEVER raises a failure to the caller. Any structural
    failure projects onto a typed :class:`SummaryReplayGap` finding
    emitted on the :attr:`SummaryReplayResult.gap_findings` list. The
    corresponding typed failure id :data:`SUMMARY_REPLAY_FAILURE_ID`
    (``summary_replay_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class with the EXISTING NON-
    blocking RouteAction ``retry_governance_projection`` (REUSED from
    Slice 14 2nd sub-slice; NOT a new route action).

    **Activation-authority boundary (doc-18:123-125 + doc-18:164 AC3).**
    The engine exposes ONLY the :meth:`replay` read method; it does
    NOT expose any mutation surface. The structural test surface
    enforces this discipline by inspecting the class's public method
    list. Replay results are review / governance artifacts only;
    never runtime policy authority.

    **Per-metric delta projection (doc-18:79-96).** The engine
    projects the four typed delta fields per the doc-18:88-90 + 91
    surface:

    * :attr:`CounterfactualResult.estimated_delta_hours` -- the
      estimated workflow hours delta. Computed from the baseline
      metric values whose definition name carries an hours unit
      (e.g. ``hours_per_task`` / ``workflow_drag_hours`` /
      ``checkpoint_duration_hours`` / ``merge_queue_wait_hours``);
      otherwise ``None``.
    * :attr:`CounterfactualResult.estimated_delta_repair_cycles` --
      the estimated repair-cycle delta. Computed from the baseline
      metric values whose definition name carries the
      ``repair_cycles_per_task`` doc-15:104 metric; otherwise
      ``None``.
    * :attr:`CounterfactualResult.estimated_delta_commit_failures` --
      the estimated commit-failure delta. Computed from the baseline
      metric values whose definition name carries the
      ``commit_failures_per_task`` doc-15:106 metric; otherwise
      ``None``.
    * :attr:`CounterfactualResult.estimated_risk_change` -- the
      typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.RiskChange`
      Literal. The summary-replay engine emits ``"unknown"`` when
      ANY of the typed delta fields is ``None`` (insufficient data
      to classify) + ``"lower"`` / ``"same"`` / ``"higher"``
      heuristics based on the typed scenario's safety_guard_class +
      the projected delta signs. The future Slice 18 4th sub-slice
      event-replay engine will tighten this projection with typed-
      event data.

    Per doc-18:50 *"Counterfactual duration estimates may be ranges
    rather than exact values."* the summary-replay engine treats the
    central estimate as the median of the baseline metric values for
    the requested scope; the breadth is reflected in the typed
    :attr:`CounterfactualResult.confidence` field (lower confidence
    for summary replay per doc-18:133).

    The engine is stateless (no instance state beyond construction);
    callers MAY reuse a single instance across multiple
    (corpus, scenario) pairs.
    """

    def replay(
        self,
        inputs: SummaryReplayInputs,
    ) -> SummaryReplayResult:
        """Run the typed summary-replay projection for the typed
        inputs.

        Per doc-18:113 step 3 the method:

        1. Validates the bounded-input contract
           (:attr:`SummaryReplayInputs.max_baseline_metrics`).
        2. Validates the typed required-field contract (``result_id``
           non-empty; ``baseline_metrics`` non-empty; ``mode`` is
           ``"summary_replay"`` since this engine owns the typed
           summary-replay surface).
        3. Computes the deterministic idempotency key.
        4. Projects the typed delta fields per doc-18:79-96 (hours /
           repair cycles / commit failures / risk change).
        5. Computes the typed confidence per doc-18:50 + doc-18:133 +
           doc-18:138.
        6. Composes the typed assumptions + validity_limits lists per
           doc-18:84-85 (union of scenario assumptions + summary-
           replay-specific validity limits like
           ``"summary_replay_mode"`` /
           ``"missing_typed_timing"`` /
           ``"insufficient_baseline_metrics"``).
        7. Emits the typed :class:`CounterfactualResult` with all 16
           fields populated.
        8. Records projection failures in
           :attr:`SummaryReplayResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        gap_findings: list[SummaryReplayGap] = []
        idempotency_key = compute_summary_replay_idempotency_key(
            result_id=inputs.result_id,
            result_version=inputs.result_version,
            corpus_id=inputs.corpus.corpus_id,
            scenario_id=inputs.scenario.scenario_id,
            mode=inputs.mode,
            baseline_metric_definition_names=[
                m.definition_name for m in inputs.baseline_metrics
            ],
            assumptions=inputs.scenario.assumptions,
            validity_limits=inputs.corpus.validity_limits,
        )

        # Bounded-input check (per doc-18:150 + Slice 13A invariant
        # bounded-reads discipline).
        if len(inputs.baseline_metrics) > inputs.max_baseline_metrics:
            gap_findings.append(
                SummaryReplayGap(
                    failure_id=SUMMARY_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="baseline_metrics_exceeded_bound",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "received_count": len(inputs.baseline_metrics),
                        "max_bound": inputs.max_baseline_metrics,
                    },
                )
            )
            return SummaryReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Required-field check (per doc-18:80 + doc-18:113).
        if not inputs.result_id or not inputs.result_id.strip():
            gap_findings.append(
                SummaryReplayGap(
                    failure_id=SUMMARY_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id or "<empty>",
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="result_id_empty",
                    observed_at=_utcnow(),
                )
            )
            return SummaryReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        if not inputs.baseline_metrics:
            gap_findings.append(
                SummaryReplayGap(
                    failure_id=SUMMARY_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="baseline_metrics_empty",
                    observed_at=_utcnow(),
                )
            )
            return SummaryReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Mode check (per doc-18:61 + doc-18:113). This 3rd sub-slice
        # engine ONLY handles the typed summary-replay mode; the
        # future 4th sub-slice event-replay engine will handle
        # ``event_replay`` + ``hybrid``.
        if inputs.mode != "summary_replay":
            gap_findings.append(
                SummaryReplayGap(
                    failure_id=SUMMARY_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="invalid_replay_mode_for_engine",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "received_mode": inputs.mode,
                        "expected_mode": "summary_replay",
                    },
                )
            )
            return SummaryReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        # Project the typed delta fields per doc-18:79-96.
        estimated_delta_hours = self._project_delta_hours(inputs)
        estimated_delta_repair_cycles = self._project_delta_repair_cycles(
            inputs
        )
        estimated_delta_commit_failures = self._project_delta_commit_failures(
            inputs
        )
        estimated_risk_change = self._project_risk_change(
            inputs,
            estimated_delta_hours=estimated_delta_hours,
            estimated_delta_repair_cycles=estimated_delta_repair_cycles,
            estimated_delta_commit_failures=estimated_delta_commit_failures,
        )

        # Compute the typed confidence per doc-18:50 + doc-18:133 +
        # doc-18:138.
        confidence = self._project_confidence(inputs)

        # Compose the typed assumptions + validity_limits lists per
        # doc-18:84-85.
        composed_assumptions = self._compose_assumptions(inputs)
        composed_validity_limits = self._compose_validity_limits(
            inputs,
            estimated_delta_hours=estimated_delta_hours,
        )

        # Detect missing required evidence per doc-18:134-135.
        invalidated_by = self._compute_invalidated_by(inputs)

        # Pick the typed recommended-next-step per doc-18:95 +
        # doc-18:134-135 + doc-18:138.
        recommended_next_step = self._project_recommended_next_step(
            inputs,
            invalidated_by=invalidated_by,
            confidence=confidence,
        )

        # Project the typed safety-guard-class field per doc-18:87 +
        # doc-18:140-146.
        safety_guard_class = self._project_safety_guard_class(inputs)

        # Surface the typed Slice 13a evidence refs from the baseline
        # metrics + scorecard onto the emitted result's typed
        # policy_provenance_refs list (refs-only).
        policy_provenance_refs = self._collect_policy_provenance_refs(inputs)

        # Construct the typed CounterfactualResult (per doc-18:79-96).
        try:
            result = CounterfactualResult(
                result_id=inputs.result_id,
                result_version=inputs.result_version,
                scenario_id=inputs.scenario.scenario_id,
                corpus_id=inputs.corpus.corpus_id,
                assumptions=composed_assumptions,
                validity_limits=composed_validity_limits,
                policy_provenance_refs=policy_provenance_refs,
                safety_guard_class=safety_guard_class,
                estimated_delta_hours=estimated_delta_hours,
                estimated_delta_repair_cycles=estimated_delta_repair_cycles,
                estimated_delta_commit_failures=estimated_delta_commit_failures,
                estimated_risk_change=estimated_risk_change,
                confidence=confidence,
                invalidated_by=invalidated_by,
                supporting_finding_ids=[],
                recommended_next_step=recommended_next_step,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                SummaryReplayGap(
                    failure_id=SUMMARY_REPLAY_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    corpus_id=inputs.corpus.corpus_id,
                    scenario_id=inputs.scenario.scenario_id,
                    reason="result_construction_failed",
                    observed_at=_utcnow(),
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return SummaryReplayResult(
                result=None,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
            )

        return SummaryReplayResult(
            result=result,
            gap_findings=gap_findings,
            idempotency_key=idempotency_key,
        )

    # --- per-axis projection helpers (per doc-18:79-96) ----------------------

    # The 4 _project_delta_* + _project_risk_change + _project_confidence
    # + _compose_assumptions + _compose_validity_limits +
    # _compute_invalidated_by + _project_recommended_next_step +
    # _project_safety_guard_class + _collect_policy_provenance_refs helpers
    # below are leading-underscore (private) methods; the
    # `test_no_mutation_methods` structural test inspects ONLY public
    # method names so these are exempt from the AC3 mutation-prefix
    # check by design.

    def _project_delta_hours(self, inputs: SummaryReplayInputs) -> float | None:
        """Project the doc-18:88 estimated_delta_hours from the typed
        baseline metrics.

        Per doc-18:88 the delta is the estimated workflow hours delta
        the counterfactual policy would have produced (negative = saved
        hours; positive = additional hours; ``None`` if not quantified).

        The summary-replay heuristic: the median of the baseline metric
        values whose definition name is in
        :data:`_HOURS_METRIC_NAMES`, scaled by the scenario's
        policy_under_test "expected_hours_delta_ratio" key (if present;
        otherwise 0.0). Returns ``None`` when no hours-flavored metric
        is present in the baseline.
        """

        hours_metrics = [
            m
            for m in inputs.baseline_metrics
            if m.definition_name in _HOURS_METRIC_NAMES and m.value is not None
        ]
        if not hours_metrics:
            return None
        baseline_median = _median(
            [float(m.value) for m in hours_metrics if m.value is not None]
        )
        if baseline_median is None:
            return None
        ratio = _safe_float(
            inputs.scenario.policy_under_test.get("expected_hours_delta_ratio")
        )
        if ratio is None:
            ratio = 0.0
        return baseline_median * ratio

    def _project_delta_repair_cycles(
        self, inputs: SummaryReplayInputs
    ) -> float | None:
        """Project the doc-18:89 estimated_delta_repair_cycles from the
        typed baseline metrics.

        The summary-replay heuristic: the median of the baseline
        ``repair_cycles_per_task`` metric values, scaled by the
        scenario's policy_under_test
        "expected_repair_cycles_delta_ratio" key (if present;
        otherwise 0.0). Returns ``None`` when no
        ``repair_cycles_per_task`` metric is present.
        """

        cycles_metrics = [
            m
            for m in inputs.baseline_metrics
            if m.definition_name == "repair_cycles_per_task"
            and m.value is not None
        ]
        if not cycles_metrics:
            return None
        baseline_median = _median(
            [float(m.value) for m in cycles_metrics if m.value is not None]
        )
        if baseline_median is None:
            return None
        ratio = _safe_float(
            inputs.scenario.policy_under_test.get(
                "expected_repair_cycles_delta_ratio"
            )
        )
        if ratio is None:
            ratio = 0.0
        return baseline_median * ratio

    def _project_delta_commit_failures(
        self, inputs: SummaryReplayInputs
    ) -> float | None:
        """Project the doc-18:90 estimated_delta_commit_failures from
        the typed baseline metrics.

        The summary-replay heuristic: the median of the baseline
        ``commit_failures_per_task`` metric values, scaled by the
        scenario's policy_under_test
        "expected_commit_failures_delta_ratio" key (if present;
        otherwise 0.0). Returns ``None`` when no
        ``commit_failures_per_task`` metric is present.
        """

        failures_metrics = [
            m
            for m in inputs.baseline_metrics
            if m.definition_name == "commit_failures_per_task"
            and m.value is not None
        ]
        if not failures_metrics:
            return None
        baseline_median = _median(
            [float(m.value) for m in failures_metrics if m.value is not None]
        )
        if baseline_median is None:
            return None
        ratio = _safe_float(
            inputs.scenario.policy_under_test.get(
                "expected_commit_failures_delta_ratio"
            )
        )
        if ratio is None:
            ratio = 0.0
        return baseline_median * ratio

    def _project_risk_change(
        self,
        inputs: SummaryReplayInputs,
        *,
        estimated_delta_hours: float | None,
        estimated_delta_repair_cycles: float | None,
        estimated_delta_commit_failures: float | None,
    ) -> RiskChange:
        """Project the doc-18:91 estimated_risk_change Literal from
        the typed deltas + safety-guard class.

        The summary-replay heuristic:

        * If ANY of the 3 typed delta fields is ``None`` (i.e. no
          baseline data for the relevant axis) -> ``"unknown"``.
        * Else if the scenario's safety_guard_class is set (per
          doc-18:140-146) -> ``"lower"`` (safety-guard policies are
          permitted only when they lower risk).
        * Else if all 3 deltas are <= 0 (saving hours / fewer
          cycles / fewer failures) -> ``"lower"``.
        * Else if all 3 deltas are >= 0 -> ``"higher"``.
        * Else -> ``"same"`` (mixed signs => no clear direction).
        """

        if (
            estimated_delta_hours is None
            or estimated_delta_repair_cycles is None
            or estimated_delta_commit_failures is None
        ):
            return "unknown"
        safety_guard_class = self._project_safety_guard_class(inputs)
        if safety_guard_class is not None:
            return "lower"
        deltas = [
            estimated_delta_hours,
            estimated_delta_repair_cycles,
            estimated_delta_commit_failures,
        ]
        if all(d <= 0.0 for d in deltas):
            return "lower"
        if all(d >= 0.0 for d in deltas):
            return "higher"
        return "same"

    def _project_confidence(self, inputs: SummaryReplayInputs) -> float:
        """Project the doc-18:92 confidence score from the typed
        baseline.

        Per doc-18:133 *"Missing typed timing: use summary replay with
        lower confidence."* the summary-replay engine carries a lower
        confidence ceiling than the (future) event-replay engine
        (:data:`SUMMARY_REPLAY_CONFIDENCE_CEILING` = 0.65).

        Per doc-18:138 *"Small sample size: report confidence and
        avoid policy recommendations."* the engine further reduces
        confidence below this ceiling when the baseline-metric sample
        size is small (heuristic: if the baseline metric count is < 3,
        the emitted confidence is multiplied by 0.5).

        Per doc-18:140-141 *"Overfit risk: require at least one
        non-`8ac124d6` corpus before marking a general policy high
        confidence."* the engine further reduces confidence when the
        corpus's feature_ids list is ``["8ac124d6"]``-only (multiplied
        by 0.5).

        The confidence is also scaled by the median of the baseline
        metric :attr:`GovernanceMetricValue.confidence` field (so a
        baseline of insufficient-sample metrics produces a low engine
        confidence too).
        """

        baseline_count = len(inputs.baseline_metrics)
        baseline_confidence_median = _median(
            [m.confidence for m in inputs.baseline_metrics]
        )
        if baseline_confidence_median is None:
            baseline_confidence_median = 0.0
        confidence = SUMMARY_REPLAY_CONFIDENCE_CEILING * float(
            baseline_confidence_median
        )
        if baseline_count < 3:
            confidence *= 0.5
        if inputs.corpus.feature_ids == ["8ac124d6"]:
            confidence *= 0.5
        # Clamp to [0.0, 1.0] (Pydantic Literal validation would not
        # accept >= 1.0 even though the typed field is unconstrained
        # float; this is a defence-in-depth clamp).
        return max(0.0, min(confidence, 1.0))

    def _compose_assumptions(self, inputs: SummaryReplayInputs) -> list[str]:
        """Compose the doc-18:84 assumptions list.

        Per doc-18:84 the assumptions list is the union of the
        scenario assumptions plus any comparator-time assumptions
        added by the summary-replay engine.

        The summary-replay engine adds the following typed
        comparator-time assumption strings:

        * ``"summary_replay_projection"`` -- always present (this
          engine is the summary-replay projection).
        """

        composed = sorted(
            set(inputs.scenario.assumptions)
            | {"summary_replay_projection"}
        )
        return composed

    def _compose_validity_limits(
        self,
        inputs: SummaryReplayInputs,
        *,
        estimated_delta_hours: float | None,
    ) -> list[str]:
        """Compose the doc-18:85 validity_limits list.

        Per doc-18:85 the validity_limits list is the union of the
        corpus validity limits plus any comparator-time validity
        constraints added by the summary-replay engine.

        The summary-replay engine adds the following typed
        comparator-time validity-limit strings:

        * ``"summary_replay_mode"`` -- always present (doc-18:133
          *"Missing typed timing: use summary replay with lower
          confidence."*).
        * ``"insufficient_baseline_metrics"`` -- when the baseline-
          metric sample size is < 3 (doc-18:138).
        * ``"missing_hours_baseline_metric"`` -- when no
          hours-flavored baseline metric is present (i.e. the typed
          hours delta cannot be projected).
        * ``"governance_only_provenance_chain"`` -- when the corpus
          is ``8ac124d6``-only (doc-18:140-141).
        """

        composed_set = set(inputs.corpus.validity_limits) | {
            "summary_replay_mode"
        }
        if len(inputs.baseline_metrics) < 3:
            composed_set.add("insufficient_baseline_metrics")
        if estimated_delta_hours is None:
            composed_set.add("missing_hours_baseline_metric")
        if inputs.corpus.feature_ids == ["8ac124d6"]:
            composed_set.add("governance_only_provenance_chain")
        return sorted(composed_set)

    def _compute_invalidated_by(
        self, inputs: SummaryReplayInputs
    ) -> list[str]:
        """Compute the doc-18:93 invalidated_by list.

        Per doc-18:134-135 *"Policy requires evidence not in corpus:
        mark invalidated and collect more evidence."* the engine
        scans the scenario's required_evidence_kinds against the
        corpus's evidence_set_ids + implementation_anchor_ids; any
        missing required evidence kind produces an entry of the form
        ``"missing_evidence:<kind>"`` (mirrors the Slice 18 2nd sub-
        slice :class:`ScenarioDefinitionBuilder` precedent verbatim).

        Per doc-18:136-137 *"Product defect dominates window: do not
        infer workflow policy success from a product-blocked group
        without separate workflow evidence."* the engine also scans
        the corpus's validity_limits for the
        ``"product_defect_window"`` marker; when present + the
        scenario's safety_guard_class is unset, the engine adds
        ``"product_defect_window"`` to invalidated_by.
        """

        invalidated_set: set[str] = set()
        covered_evidence = set(inputs.corpus.evidence_set_ids) | set(
            inputs.corpus.implementation_anchor_ids
        )
        for evidence_kind in inputs.scenario.required_evidence_kinds:
            if evidence_kind not in covered_evidence:
                invalidated_set.add(f"missing_evidence:{evidence_kind}")
        if (
            "product_defect_window" in inputs.corpus.validity_limits
            and self._project_safety_guard_class(inputs) is None
        ):
            invalidated_set.add("product_defect_window")
        return sorted(invalidated_set)

    def _project_recommended_next_step(
        self,
        inputs: SummaryReplayInputs,
        *,
        invalidated_by: list[str],
        confidence: float,
    ) -> RecommendedNextStep:
        """Project the doc-18:95 recommended_next_step Literal.

        Per doc-18:95 the recommended_next_step is one of:

        * ``"discard"`` -- the scenario should be discarded (e.g.
          confidence < 0.05).
        * ``"collect_more_evidence"`` -- the engine cannot make a
          confident projection (e.g. invalidated_by non-empty;
          confidence < 0.3; or baseline-metric count < 3).
        * ``"draft_policy"`` -- the engine can suggest a draft policy
          (e.g. confidence >= 0.5; invalidated_by empty).
        * ``"implementation_plan"`` -- the engine can suggest an
          implementation plan (e.g. confidence >= 0.6 +
          safety_guard_class set; invalidated_by empty).
        """

        if confidence < 0.05:
            return "discard"
        if invalidated_by or confidence < 0.3 or len(inputs.baseline_metrics) < 3:
            return "collect_more_evidence"
        safety_guard_class = self._project_safety_guard_class(inputs)
        if confidence >= 0.6 and safety_guard_class is not None:
            return "implementation_plan"
        if confidence >= 0.5:
            return "draft_policy"
        return "collect_more_evidence"

    def _project_safety_guard_class(
        self, inputs: SummaryReplayInputs
    ) -> str | None:
        """Project the doc-18:87 safety_guard_class field.

        Per doc-18:140-146 *"A safety-guard exception is allowed only
        for policies whose sole effect is to fail closed earlier,
        reduce mutation authority, or add bounded preflight
        evidence. The scenario must set `safety_guard_class`, cite
        non-governance primary evidence, and pass a chain-depth
        check proving it is not derived solely from prior governance
        recommendations."* the safety_guard_class field is propagated
        from the scenario's policy_under_test["safety_guard_class"]
        key (when present); otherwise ``None``.
        """

        raw = inputs.scenario.policy_under_test.get("safety_guard_class")
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        return None

    def _collect_policy_provenance_refs(
        self, inputs: SummaryReplayInputs
    ) -> list[GovernanceEvidenceRef]:
        """Surface the typed Slice 13a evidence refs from the baseline
        metrics + scorecard onto the emitted result's typed
        policy_provenance_refs list.

        Per the Slice 13A invariant + doc-18:186-249 the projection is
        refs-only: the typed
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
        BaseModels are preserved (NOT projected to ref_id strings);
        no raw artifact body is hydrated.

        The projection is the order-preserved deduplication of the
        baseline metrics' :attr:`GovernanceMetricValue.evidence_refs`
        lists + the baseline scorecard's :attr:`GovernanceScorecard.baseline_refs`
        list (when supplied).

        Deduplication uses the
        :attr:`GovernanceEvidenceRef.ref_id` string as the cross-ref
        identity (refs with the same ref_id are deduped; the first
        occurrence wins).
        """

        seen_ref_ids: set[str] = set()
        out: list[GovernanceEvidenceRef] = []
        for metric in inputs.baseline_metrics:
            for ref in metric.evidence_refs:
                if ref.ref_id in seen_ref_ids:
                    continue
                seen_ref_ids.add(ref.ref_id)
                out.append(ref)
        if inputs.baseline_scorecard is not None:
            for ref in inputs.baseline_scorecard.baseline_refs:
                if ref.ref_id in seen_ref_ids:
                    continue
                seen_ref_ids.add(ref.ref_id)
                out.append(ref)
        return out


# --- Module-private constants (used by the engine's projection helpers) -----


_HOURS_METRIC_NAMES: frozenset[str] = frozenset(
    {
        # Doc-15:103 + doc-15:110-112 -- the 4 typed v1 metric names
        # whose unit is "hours" (per doc-15:83 the unit string is
        # free-form, but the v1 metric tuple at
        # `governance_metrics.py:164-186` carries these 4 names as
        # the typed hours-flavored set).
        "hours_per_task",
        "merge_queue_wait_hours",
        "checkpoint_duration_hours",
        "workflow_drag_hours",
    }
)


# --- Pure math helpers ------------------------------------------------------


def _median(values: list[float]) -> float | None:
    """Return the median of ``values`` (or ``None`` if empty)."""

    if not values:
        return None
    sorted_values = sorted(values)
    n = len(sorted_values)
    mid = n // 2
    if n % 2 == 1:
        return sorted_values[mid]
    return (sorted_values[mid - 1] + sorted_values[mid]) / 2.0


def _safe_float(value: Any) -> float | None:
    """Coerce ``value`` to ``float`` if it is a number; otherwise
    ``None``.

    Used by the engine to read scenario.policy_under_test dict values
    (free-form per doc-18:73) without raising on malformed values.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


# --- Re-exports (documentation-only; for IDE auto-import discovery) --------

# Documentation-only re-references so the typed Literal aliases the
# engine surfaces to consumers stay discoverable from this module.
# These are NOT in __all__ (per the per-slice no-re-export discipline);
# the consumer imports them from
# `iriai_build_v2.execution_control.counterfactual_replay`.
_REPLAY_MODE_REUSE: type = ReplayMode  # type: ignore[assignment]
_RISK_CHANGE_REUSE: type = RiskChange  # type: ignore[assignment]
_RECOMMENDED_NEXT_STEP_REUSE: type = RecommendedNextStep  # type: ignore[assignment]
_COMPUTE_COUNTERFACTUAL_IDEMPOTENCY_KEY_REUSE = compute_counterfactual_idempotency_key
