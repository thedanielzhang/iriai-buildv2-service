"""Slice 18 fifth sub-slice -- baseline-vs-scenario metrics comparator.

This module implements doc-18 § Refactoring Steps step 5 (line 115):
*"Compare baseline vs scenario outcomes using Slice 15 metrics."*

Per doc-18:115 the comparator consumes:

* A list of typed Slice 15
  :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
  baseline records (the per-axis baseline measurements).
* A typed Slice 18 1st sub-slice
  :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
  scenario result (the per-axis estimated deltas emitted by the Slice
  18 3rd sub-slice
  :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.CounterfactualSummaryReplayEngine`
  OR the Slice 18 4th sub-slice
  :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.CounterfactualEventReplayEngine`).

And emits a typed :class:`MetricsComparatorResult` that exposes
per-axis delta records carrying:

* :attr:`MetricsAxisDelta.baseline_value` -- the typed baseline metric
  central value (per doc-15:82 + doc-18:88-90).
* :attr:`MetricsAxisDelta.scenario_estimated_delta` -- the typed
  scenario-estimated delta (per doc-18:88-90, e.g.
  :attr:`CounterfactualResult.estimated_delta_hours`).
* :attr:`MetricsAxisDelta.confidence` -- the typed per-axis confidence
  score (derived from baseline-metric confidence + scenario-result
  confidence; per doc-18:92 + doc-15:84).
* :attr:`MetricsAxisDelta.validity_limits` -- the typed per-axis
  validity-limit string list (per doc-18:85; carried as the union of
  baseline-metric exclusions + scenario-result validity_limits).
* :attr:`MetricsAxisDelta.invalidated` -- typed optional boolean
  marker (set to ``True`` when the per-axis projection cannot produce
  a usable estimate, e.g. baseline metric value is ``None`` OR
  scenario delta is ``None`` OR evidence is missing per
  doc-18:134-138).

This module owns:

* :data:`METRICS_COMPARATOR_FAILURE_ID` -- the typed failure id
  Literal (``metrics_comparator_failed``) registering under the
  EXISTING ``evidence_corruption`` failure_class with the REUSED
  Slice 14 2nd sub-slice ``retry_governance_projection`` NON-blocking
  RouteAction (mirrors Slice 17 2nd/3rd/4th/5th/6th + Slice 18 2nd/
  3rd/4th sub-slice precedent verbatim).
* :data:`DEFAULT_MAX_BASELINE_METRICS` -- the bounded-input default
  cap on the baseline metric list count (256; mirrors Slice 18 3rd
  sub-slice
  :data:`~iriai_build_v2.execution_control.counterfactual_summary_replay.DEFAULT_MAX_BASELINE_METRICS`
  for symmetric bounded-input contract).
* :class:`MetricsComparatorInputs` -- the typed bundle of all inputs
  the comparator consumes (baseline metric list + scenario
  :class:`CounterfactualResult` + result_id / result_version / mode).
* :class:`MetricsAxisDelta` -- the typed per-axis delta record (one
  per axis: ``hours`` / ``repair_cycles`` / ``commit_failures`` /
  ``risk_change``).
* :class:`MetricsComparatorResult` -- the typed bundle of all outputs
  the comparator produces (per-axis delta record list + optional
  gap_findings list + idempotency key + emitted_at timestamp).
* :class:`MetricsComparatorGap` -- the typed gap projection emitted
  when the comparator fails structurally (NEVER raises per
  ``feedback_no_silent_degradation``).
* :class:`CounterfactualMetricsComparator` -- the comparator class
  with a single :meth:`compare` method (READ-ONLY by structural
  design; no consumer-side mutation methods).
* :func:`compute_metrics_comparator_idempotency_key` -- the
  deterministic SHA-256 idempotency-key helper (mirrors the Slice 18
  3rd sub-slice
  :func:`~iriai_build_v2.execution_control.counterfactual_summary_replay.compute_summary_replay_idempotency_key`
  + Slice 18 4th sub-slice
  :func:`~iriai_build_v2.execution_control.counterfactual_event_replay.compute_event_replay_idempotency_key`
  canonical-JSON + SHA-256 discipline verbatim).

**Activation-authority boundary (per doc-18:123-125 + doc-18:164 AC3 +
STATUS.md § "Loop discipline").** Counterfactual replay results are
review / governance artifacts only -- never runtime policy authority.
The comparator exposes **only** the :meth:`compare` read method; it
does NOT expose any mutation surface, does NOT import any consumer-
side module (dispatcher / scheduler / merge queue / supervisor /
dashboard / failure router runtime / commit_provenance writer / etc.),
and does NOT write any ``dag-*`` execution-authority artifact key.
The structural test surface enforces this discipline by inspecting
the module's import graph + the class's public method list.

**Slice 13a dependency reconciliation (doc-13a:285-287 step 9;
doc-18:186-249).** The comparator consumes the typed Slice 13a
:class:`GovernanceEvidenceRef` records refs-only -- no raw artifact
bodies are hydrated. The per-axis delta records carry typed-ref
pointers back to the baseline metrics' typed
:attr:`GovernanceMetricValue.evidence_refs` (refs-only; no raw body
hydration).

**Refs-only invariant (doc-18:186-249).** The comparator never
hydrates raw artifact bodies; the typed Slice 15 + Slice 18 1st sub-
slice typed inputs carry only summary fields (ids / counts / bounded
samples / citations) per the Slice 10a invariant
(:mod:`iriai_build_v2.workflows.develop.execution.snapshots` § "All
payloads are SUMMARY-ONLY").

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` +
``datetime``) + Pydantic v2 + Slice 13a typed governance models +
Slice 15 governance metrics + Slice 18 1st sub-slice typed shapes
only. NO imports from ``governance/`` outside ``governance.models``.
NO imports from other parts of ``execution_control/`` beyond the
prior Slice 14 / 15 / 17 / 18 1st sub-slice modules (this 5th sub-
slice is foundational for the future Slice 18 6th sub-slice typed-
governance-row result writer + 7th sub-slice Slice 17 recommendation
citation hook). NO imports from ``workflows/develop/execution/phases/``
/ ``supervisor`` / ``dashboard`` (those would be downstream consumers,
not dependencies).

**Doc-18 acceptance binding** (doc-18:160-168): the comparator
honours the 5 acceptance criteria:

* **AC1** -- *"Counterfactuals are deterministic, versioned, and
  evidence-backed."* (doc-18:162) -- HONOURED via the
  :func:`compute_metrics_comparator_idempotency_key` canonical-JSON
  + SHA-256 helper (the "deterministic" axis) + the
  :attr:`MetricsComparatorInputs.result_version` typed field (the
  "versioned" axis) + the per-axis evidence-refs carried on the typed
  :class:`MetricsAxisDelta` records (the "evidence-backed" axis).
* **AC2** -- *"Every result lists assumptions and validity limits."*
  (doc-18:163) -- HONOURED via the typed
  :attr:`MetricsAxisDelta.validity_limits: list[str]` field populated
  per axis from the union of baseline-metric exclusions + scenario-
  result validity_limits.
* **AC3** -- *"Replay cannot mutate live workflow state."*
  (doc-18:164) -- HONOURED via the read-only typed-shape design (no
  mutation methods on the BaseModel + the
  :class:`CounterfactualMetricsComparator` class exposing exactly one
  public method :meth:`compare`).
* **AC4** -- *"Recommendations that affect runtime behavior cite
  replay results or explicitly say more evidence is needed."*
  (doc-18:165-166) -- HONOURED via the typed
  :attr:`MetricsComparatorResult.scenario_result_id` field that the
  Slice 17 recommendation citation hook can cross-reference (the
  result id is carried through from the input
  :attr:`CounterfactualResult.result_id`).
* **AC5** -- *"The replay corpus includes both 8ac124d6 evidence and
  Slice 00-12 implementation artifacts."* (doc-18:167-168) --
  HONOURED via the per-axis evidence-refs carried on
  :attr:`MetricsAxisDelta.evidence_refs` (refs-only union of the
  baseline metric evidence_refs + the scenario
  :attr:`CounterfactualResult.policy_provenance_refs`).

**Cross-slice REUSE contract.**

* Slice 13a :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  -- imported, NOT redefined.
* Slice 15 :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
  -- imported, NOT redefined.
* Slice 18 1st sub-slice
  :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
  + :data:`ReplayMode` -- imported, NOT redefined.
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
    ReplayMode,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


__all__ = [
    # Typed failure id (doc-18:115 + doc-14:242-243 NON-BLOCKING).
    "METRICS_COMPARATOR_FAILURE_ID",
    # Bounded-input default (doc-18:150 + Slice 13A invariant bounded-
    # reads discipline; mirrors the Slice 18 3rd sub-slice
    # DEFAULT_MAX_BASELINE_METRICS = 256 verbatim).
    "DEFAULT_MAX_BASELINE_METRICS",
    # Typed inputs / per-axis delta / result / gap (doc-18:115).
    "MetricsComparatorInputs",
    "MetricsAxisDelta",
    "MetricsComparatorResult",
    "MetricsComparatorGap",
    # The comparator class (doc-18:115).
    "CounterfactualMetricsComparator",
    # Pure helper for the deterministic idempotency key (mirrors the
    # Slice 18 1st / 2nd / 3rd / 4th sub-slice canonical-JSON +
    # SHA-256 discipline).
    "compute_metrics_comparator_idempotency_key",
]


# --- Typed failure id (doc-18:115 + doc-14:242-243 NON-BLOCKING) ------------


METRICS_COMPARATOR_FAILURE_ID: Literal[
    "metrics_comparator_failed"
] = "metrics_comparator_failed"
"""Doc-18:115 + doc-14:242-243 -- the typed failure id the
:class:`CounterfactualMetricsComparator` projects onto when a
comparator step fails structurally.

Registers under the EXISTING ``evidence_corruption`` failure_class in
:mod:`iriai_build_v2.workflows.develop.execution.failure_router` (NOT a
new failure_class) with the EXISTING NON-blocking
:data:`~iriai_build_v2.workflows.develop.execution.failure_router.RouteAction`
``retry_governance_projection`` (REUSED from Slice 14 2nd sub-slice;
NOT a new route action; mirrors Slice 15 2nd + 4th + Slice 16 2nd +
3rd-A + 3rd-B + 4th + Slice 17 2nd + 3rd + 4th + 5th + 6th + Slice 18
2nd + 3rd + 4th sub-slice precedent verbatim).

This is INTENTIONALLY DIFFERENT from the prior Slice 13A typed ids
``list_field_incomplete`` + ``classifier_rule_blocked`` (also under
``evidence_corruption``) which route to ``quiesce`` -- the Slice 13A
pattern is a fail-closed safety stop for required gate evidence; the
Slice 18 pattern matches the Slice 14 + Slice 15 + Slice 16 + Slice 17
non-blocking governance projection observer (the metrics comparator is
also a post-checkpoint governance projection observer).

Per doc-14:242-243 *"Governance provenance projection failures never
block ``dag-group:*`` checkpointing, merge queue integration, or
resume"* -- the same non-blocking contract applies to metrics-
comparator failures (this slice is also a post-checkpoint governance
projection observer + per doc-18:123 replay results are review/
governance artifacts only -- never runtime policy authority).
"""


# --- Default bounded-input threshold ----------------------------------------


DEFAULT_MAX_BASELINE_METRICS: int = 256
"""Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
default upper bound on the number of typed
:class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
baseline records a single :class:`MetricsComparatorInputs` may carry.

Per the governance prompt § "Bounded reads" *"Reuse the typed
snapshot's `LIMIT cap+1` truncation discipline and the supervisor's
`SET LOCAL statement_timeout` pattern."* the comparator rejects inputs
that exceed the bound (typed gap projection; NEVER raises). The
default value 256 mirrors :data:`~iriai_build_v2.execution_control.counterfactual_summary_replay.DEFAULT_MAX_BASELINE_METRICS`
verbatim for symmetric bounded-input contract; the caller MAY override
the default via :attr:`MetricsComparatorInputs.max_baseline_metrics`
(e.g. a large historical-comparison caller may raise to 1024).

Per doc-18:150 § Tests *"Replay corpus loader rejects malformed or
unbounded fixture inputs."* the bound applies symmetrically to the
metrics comparator (the comparator is also a typed-input consumer).
"""


# --- The 4-axis taxonomy ----------------------------------------------------


_AXIS_HOURS: Literal["hours"] = "hours"
"""Doc-18:88 -- the typed axis identifier for the
:attr:`CounterfactualResult.estimated_delta_hours` per-axis comparison.
"""

_AXIS_REPAIR_CYCLES: Literal["repair_cycles"] = "repair_cycles"
"""Doc-18:89 -- the typed axis identifier for the
:attr:`CounterfactualResult.estimated_delta_repair_cycles` per-axis
comparison.
"""

_AXIS_COMMIT_FAILURES: Literal["commit_failures"] = "commit_failures"
"""Doc-18:90 -- the typed axis identifier for the
:attr:`CounterfactualResult.estimated_delta_commit_failures` per-axis
comparison.
"""

_AXIS_RISK_CHANGE: Literal["risk_change"] = "risk_change"
"""Doc-18:91 -- the typed axis identifier for the
:attr:`CounterfactualResult.estimated_risk_change` per-axis
comparison.
"""

MetricsAxis = Literal[
    "hours",
    "repair_cycles",
    "commit_failures",
    "risk_change",
]
"""The 4-value typed axis taxonomy for the per-axis delta records.

Maps to the doc-18:88-91 :class:`CounterfactualResult` typed delta
fields:

* ``hours`` -- :attr:`CounterfactualResult.estimated_delta_hours`
  (doc-18:88).
* ``repair_cycles`` --
  :attr:`CounterfactualResult.estimated_delta_repair_cycles`
  (doc-18:89).
* ``commit_failures`` --
  :attr:`CounterfactualResult.estimated_delta_commit_failures`
  (doc-18:90).
* ``risk_change`` --
  :attr:`CounterfactualResult.estimated_risk_change` (doc-18:91).

Pydantic Literal validation: unknown values fail closed at construction
with a typed ``ValidationError`` per the
``feedback_no_silent_degradation`` rule.
"""


# --- Metric-name -> axis mapping (doc-15:99-115 + doc-18:88-90) -------------


_HOURS_METRIC_NAMES: frozenset[str] = frozenset(
    {
        # Per doc-15:103 + doc-15:110-112 hour-unit metrics.
        "hours_per_task",
        "workflow_drag_hours",
        "checkpoint_duration_hours",
        "merge_queue_wait_hours",
    }
)
"""Doc-15:103 + doc-15:110-112 + doc-18:88 -- the typed set of Slice 15
metric definition names that carry an hours unit (mapped to the
``hours`` axis on the per-axis delta record).

Mirrors the Slice 18 3rd sub-slice
:mod:`iriai_build_v2.execution_control.counterfactual_summary_replay`
+ Slice 18 4th sub-slice
:mod:`iriai_build_v2.execution_control.counterfactual_event_replay`
projection helpers verbatim per the no-second-source-of-truth
discipline.
"""

_REPAIR_CYCLES_METRIC_NAME: str = "repair_cycles_per_task"
"""Doc-15:104 + doc-18:89 -- the typed Slice 15 metric definition name
that carries the repair-cycles axis (mapped to the ``repair_cycles``
axis on the per-axis delta record).
"""

_COMMIT_FAILURES_METRIC_NAME: str = "commit_failures_per_task"
"""Doc-15:106 + doc-18:90 -- the typed Slice 15 metric definition name
that carries the commit-failures axis (mapped to the ``commit_failures``
axis on the per-axis delta record).
"""


# --- MetricsComparatorInputs (typed inputs; doc-18:115) ---------------------


class MetricsComparatorInputs(BaseModel):
    """Doc-18:115 step 5 -- typed bundle of all inputs the
    :class:`CounterfactualMetricsComparator` consumes.

    The bundle composes:

    * ``baseline_metrics`` -- the list of Slice 15 typed
      :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
      baseline records the comparator projects per-axis deltas against
      (per doc-15:78-88; the typed-shape REUSE is the cross-Slice-15
      authority for the metric value shape).
    * ``scenario_result`` -- the typed Slice 18 1st sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_replay.CounterfactualResult`
      scenario result the comparator evaluates the baseline against
      (per doc-18:79-96; populated by the Slice 18 3rd sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.CounterfactualSummaryReplayEngine`
      OR the Slice 18 4th sub-slice
      :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.CounterfactualEventReplayEngine`).
    * ``result_id`` -- the typed stable comparator-result identifier
      string (per doc-18:80 cross-Slice-17 reference surface; the
      caller is responsible for choosing a stable id, e.g. the
      scenario result id + the comparator version).
    * ``result_version`` -- the typed result-version string (per
      doc-18:81; the version axis lets future sub-slices supersede
      prior results without rewriting them).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification carried from the scenario result (per doc-18:61;
      defaults to the scenario result's mode -- carried through for the
      idempotency key dedupe).
    * ``max_baseline_metrics`` -- the typed bounded-input cap for the
      baseline metric list; defaults to
      :data:`DEFAULT_MAX_BASELINE_METRICS`. Per doc-18:150 + Slice 13A
      invariant bounded-reads discipline.

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    Mirrors :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayInputs`
    (Slice 18 3rd sub-slice) + :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.EventReplayInputs`
    (Slice 18 4th sub-slice) per the chunk-shape decision.
    """

    # ``extra="forbid"`` aligns with the Slice 18 3rd / 4th sub-slice
    # precedent at counterfactual_summary_replay.py:376 +
    # counterfactual_event_replay.py -- unknown fields fail closed as
    # a typed ``ValidationError`` rather than being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    baseline_metrics: list[GovernanceMetricValue]
    """Doc-15:78-88 + doc-18:115 -- the list of Slice 15 typed
    :class:`GovernanceMetricValue` baseline records the comparator
    projects per-axis deltas against.

    **Slice 13a + Slice 15 dependency reconciliation
    (doc-13a:285-287 step 9; doc-18:186-249).** The
    :class:`GovernanceMetricValue` shape is REUSED from
    :mod:`iriai_build_v2.execution_control.governance_metrics` (NOT
    redefined here). Each baseline metric in turn carries a list of
    Slice 13a typed :class:`GovernanceEvidenceRef` on its
    :attr:`GovernanceMetricValue.evidence_refs` field; the comparator
    surfaces those refs onto the per-axis delta records' typed
    :attr:`MetricsAxisDelta.evidence_refs` field (refs-only; no raw
    artifact body hydration).
    """

    scenario_result: CounterfactualResult
    """Doc-18:79-96 + doc-18:115 -- the typed Slice 18 1st sub-slice
    :class:`CounterfactualResult` scenario result the comparator
    evaluates the baseline against.

    **Slice 18 1st sub-slice dependency reconciliation.** REUSED from
    :mod:`iriai_build_v2.execution_control.counterfactual_replay`
    (NOT redefined here). The scenario result carries the per-axis
    estimated deltas (per doc-18:88-90) the comparator pairs with the
    baseline metrics for the per-axis delta records.
    """

    result_id: str
    """Doc-18:80 -- the typed stable comparator-result identifier
    string. Per the AC4 binding (doc-18:165-166) the result id is the
    typed cross-Slice-17 reference surface.

    Caller is responsible for choosing a stable result id (e.g. the
    scenario result id + the comparator version). The comparator does
    NOT generate one because the future Slice 18 6th sub-slice
    result-writer needs control over the identifier discipline.
    """

    result_version: str = "v1"
    """Doc-18:81 -- the typed result-version string. Per doc-18:128-129
    *"New assumptions require a new result version."* the version axis
    lets future sub-slices supersede prior results without rewriting
    them. Defaults to ``"v1"`` because this 5th sub-slice is the first
    comparator-engine version.
    """

    mode: ReplayMode = "summary_replay"
    """Doc-18:61 -- the typed replay-mode classification from the
    3-value :data:`ReplayMode` Literal. Defaults to ``"summary_replay"``
    so the comparator can be invoked with bare baseline metrics +
    scenario result without forcing the caller to plumb the scenario
    engine's mode through.

    Per Pydantic Literal validation the field accepts only one of the
    3 values; unknown values fail closed with a typed
    ``ValidationError``.
    """

    max_baseline_metrics: int = Field(
        default=DEFAULT_MAX_BASELINE_METRICS,
        ge=1,
    )
    """Doc-18:150 + Slice 13A invariant bounded-reads discipline -- the
    typed bounded-input cap for the baseline metric list. Defaults to
    :data:`DEFAULT_MAX_BASELINE_METRICS` (256).

    Per the governance prompt § "Bounded reads" the comparator rejects
    inputs that exceed the bound (typed gap projection; NEVER raises).
    Must be >= 1 (the Pydantic ``ge=1`` constraint fails closed at
    construction with a typed ``ValidationError`` if the caller passes
    a non-positive bound).
    """


# --- MetricsAxisDelta (typed per-axis record; doc-18:88-92) -----------------


class MetricsAxisDelta(BaseModel):
    """Doc-18:88-92 -- the per-axis delta record the
    :class:`CounterfactualMetricsComparator` emits per axis.

    Each per-axis record carries:

    * :attr:`axis` -- the typed 4-value :data:`MetricsAxis` Literal
      identifying which axis (``hours`` / ``repair_cycles`` /
      ``commit_failures`` / ``risk_change``).
    * :attr:`baseline_value` -- the typed baseline central value (per
      doc-15:82 :attr:`GovernanceMetricValue.value`; ``None`` when the
      baseline metric is insufficient or the axis is non-numeric like
      ``risk_change``).
    * :attr:`baseline_unit` -- the typed baseline unit string (per
      doc-15:83 :attr:`GovernanceMetricValue.unit`; ``None`` for non-
      numeric axes).
    * :attr:`scenario_estimated_delta` -- the typed scenario-estimated
      delta value (per doc-18:88-90 the central estimate from the
      :class:`CounterfactualResult`; ``None`` when the scenario does
      not quantify the axis).
    * :attr:`scenario_estimated_risk_change` -- the typed scenario
      risk-change classification (per doc-18:91 the 4-value
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.RiskChange`
      Literal; populated only for the ``risk_change`` axis).
    * :attr:`confidence` -- the typed per-axis confidence score in
      [0.0, 1.0] (per doc-18:92 + doc-15:84; computed as the harmonic
      mean of the baseline-metric confidence + the scenario-result
      confidence so a low-confidence input lowers the per-axis
      confidence).
    * :attr:`validity_limits` -- the typed per-axis validity-limit
      string list (per doc-18:85; carried as the union of baseline-
      metric exclusions + scenario-result validity_limits +
      comparator-specific limits like
      ``"baseline_value_unavailable"`` /
      ``"scenario_delta_unavailable"``).
    * :attr:`evidence_refs` -- the typed list of Slice 13a
      :class:`GovernanceEvidenceRef` records the axis grounds on (per
      doc-18:186-249; union of the baseline-metric evidence_refs +
      the scenario-result policy_provenance_refs).
    * :attr:`invalidated` -- the typed optional boolean marker (per
      doc-18:134-138; set to ``True`` when the per-axis projection
      cannot produce a usable estimate, e.g. baseline value None OR
      scenario delta None OR evidence missing).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.

    **Slice 13a + Slice 15 + Slice 18 1st dependency reconciliation
    (doc-13a:285-287 step 9; doc-18:186-249).** The
    :class:`GovernanceEvidenceRef` shape is REUSED from
    :mod:`iriai_build_v2.workflows.develop.governance.models` (NOT
    redefined here).
    """

    model_config = ConfigDict(extra="forbid")

    axis: MetricsAxis
    """Doc-18:88-91 -- the typed 4-value :data:`MetricsAxis` Literal.

    Per Pydantic Literal validation the field accepts only one of the
    4 values; unknown values fail closed with a typed
    ``ValidationError``.
    """

    baseline_value: float | int | None = None
    """Doc-15:82 -- the typed baseline central value from the matching
    :class:`GovernanceMetricValue.value`.

    ``None`` when:

    * The baseline metric for this axis is missing from the input list.
    * The matching baseline metric has :attr:`value` is ``None`` (per
      doc-15:149-150 the insufficient-sample case).
    * The axis is non-numeric (``risk_change`` carries
      :attr:`scenario_estimated_risk_change` instead).
    """

    baseline_unit: str | None = None
    """Doc-15:83 -- the typed baseline unit string from the matching
    :class:`GovernanceMetricValue.unit`.

    ``None`` when the baseline metric for this axis is missing or the
    axis is non-numeric (``risk_change``).
    """

    baseline_confidence: float | None = None
    """Doc-15:84 -- the typed baseline confidence score in [0.0, 1.0]
    from the matching :class:`GovernanceMetricValue.confidence`.

    ``None`` when the baseline metric for this axis is missing.
    """

    scenario_estimated_delta: float | None = None
    """Doc-18:88-90 -- the typed scenario-estimated delta value from
    the matching :class:`CounterfactualResult` typed delta field
    (``estimated_delta_hours`` / ``estimated_delta_repair_cycles`` /
    ``estimated_delta_commit_failures``).

    ``None`` when the scenario does not quantify the axis OR the axis
    is ``risk_change`` (which uses
    :attr:`scenario_estimated_risk_change` instead).
    """

    scenario_estimated_risk_change: (
        Literal["lower", "same", "higher", "unknown"] | None
    ) = None
    """Doc-18:91 -- the typed scenario risk-change classification from
    the :class:`CounterfactualResult.estimated_risk_change` typed
    Literal.

    Populated only for the ``risk_change`` axis. The 4-value Literal
    mirrors :data:`~iriai_build_v2.execution_control.counterfactual_replay.RiskChange`
    verbatim (NOT redefined here; the Literal is inlined for
    field-level Pydantic validation per the auto-memory
    ``feedback_flat_structured_output`` rule).
    """

    scenario_confidence: float = Field(ge=0.0, le=1.0)
    """Doc-18:92 -- the typed scenario confidence score in [0.0, 1.0]
    carried from the input scenario result.

    Per Pydantic ``ge=0.0`` + ``le=1.0`` validation the field accepts
    only values in [0.0, 1.0]; out-of-range values fail closed with a
    typed ``ValidationError``.
    """

    confidence: float = Field(ge=0.0, le=1.0)
    """Doc-18:92 + doc-15:84 -- the typed per-axis confidence score in
    [0.0, 1.0] (the harmonic mean of the baseline-metric confidence +
    the scenario-result confidence so a low-confidence input lowers
    the per-axis confidence).

    Per Pydantic ``ge=0.0`` + ``le=1.0`` validation the field accepts
    only values in [0.0, 1.0]; out-of-range values fail closed with a
    typed ``ValidationError``.
    """

    validity_limits: list[str]
    """Doc-18:85 -- the typed per-axis validity-limit string list
    (carried as the union of baseline-metric exclusions + scenario-
    result validity_limits + comparator-specific limits like
    ``"baseline_value_unavailable"`` /
    ``"scenario_delta_unavailable"``).
    """

    evidence_refs: list[GovernanceEvidenceRef]
    """Doc-18:186-249 -- the typed list of Slice 13a
    :class:`GovernanceEvidenceRef` records the axis grounds on (union
    of the baseline-metric :attr:`evidence_refs` + the scenario-result
    :attr:`policy_provenance_refs`; refs-only; no raw artifact body
    hydration).
    """

    invalidated: bool = False
    """Doc-18:134-138 -- the typed optional boolean marker.

    Set to ``True`` when the per-axis projection cannot produce a
    usable estimate. Triggers:

    * Baseline metric value is ``None`` (per doc-15:149-150
      insufficient-sample).
    * Scenario delta is ``None`` (per doc-18:50 *"Counterfactual
      duration estimates may be ranges rather than exact values."*).
    * Evidence is missing (per doc-18:134-135).

    Mirrors :attr:`CounterfactualResult.invalidated_by: list[str]`
    (doc-18:93) at the per-axis granularity; the
    :class:`MetricsComparatorResult` aggregates these per-axis flags
    into the top-level :attr:`MetricsComparatorResult.invalidated_axes`
    list.
    """


# --- MetricsComparatorGap (typed gap projection; doc-18:115 +
#     doc-14:242-243) ------------------------------------------------------


class MetricsComparatorGap(BaseModel):
    """Typed governance-gap finding produced when the
    :class:`CounterfactualMetricsComparator` fails to construct a
    :class:`MetricsComparatorResult` structurally.

    Mirrors the Slice 18 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_replay_loader.ReplayCorpusLoaderGap`
    + Slice 18 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.SummaryReplayGap`
    + Slice 18 4th sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.EventReplayGap`
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
    typed failure id :data:`METRICS_COMPARATOR_FAILURE_ID`
    (``metrics_comparator_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    with the EXISTING NON-blocking RouteAction
    ``retry_governance_projection`` (REUSED from Slice 14 2nd
    sub-slice; NOT a new route action).
    """

    model_config = ConfigDict(extra="forbid")

    failure_id: Literal["metrics_comparator_failed"]
    """Doc-18:115 + doc-14:192-201 -- the typed failure id. Registers
    under the EXISTING ``evidence_corruption`` failure_class with
    NON-blocking routing per doc-14:242-243 + doc-18:115.
    """

    result_id_attempted: str
    """The :attr:`MetricsComparatorInputs.result_id` the comparator
    attempted to compute (so the caller can correlate the gap finding
    with the requesting batch even when the result itself could not be
    constructed).
    """

    scenario_result_id: str
    """The scope of the failed projection (the
    :attr:`MetricsComparatorInputs.scenario_result.result_id`).
    """

    reason: str
    """Free-form gap reason (e.g.
    ``result_construction_failed`` /
    ``baseline_metrics_exceeded_bound`` /
    ``result_id_empty`` /
    ``baseline_metrics_empty`` /
    ``scenario_result_invalid``).
    """

    observed_at: datetime
    """ISO-8601 timestamp the comparator observed the gap (UTC,
    timezone-aware). Mirrors the Slice 13A
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness.observed_at`
    + Slice 14 2nd sub-slice
    :class:`~iriai_build_v2.execution_control.commit_provenance_writer.CommitProvenanceGapFinding.observed_at`
    contract verbatim.
    """

    evidence_refs: list[str] = Field(default_factory=list)
    """Optional list of evidence-ref id strings the gap implicates
    (refs-only per the Slice 13A invariant + doc-18:186-249; the typed
    BaseModel form is NOT embedded -- the caller cross-references via
    the typed Slice 13a evidence-ref surface separately).
    """

    evidence_payload: dict[str, Any] = Field(default_factory=dict)
    """Extra context the caller may use to enrich the gap finding
    (e.g. the Pydantic ValidationError detail, the truncation bound,
    the baseline metric count). Free-form per the doc-14:192-201 +
    Slice 14/15/16/17/18 governance-finding precedent.
    """


# --- MetricsComparatorResult (typed result; doc-18:115) ---------------------


class MetricsComparatorResult(BaseModel):
    """Doc-18:115 step 5 -- typed bundle of all outputs the
    :class:`CounterfactualMetricsComparator` produces.

    The bundle composes:

    * ``axis_deltas`` -- the list of typed :class:`MetricsAxisDelta`
      per-axis delta records (one per axis: ``hours`` /
      ``repair_cycles`` / ``commit_failures`` / ``risk_change``); OR
      empty when the projection failed structurally (in which case the
      gap is recorded in :attr:`gap_findings`).
    * ``gap_findings`` -- the list of typed
      :class:`MetricsComparatorGap` records emitted when a projection
      step fails structurally (per
      :data:`METRICS_COMPARATOR_FAILURE_ID`).
    * ``idempotency_key`` -- the deterministic
      :func:`compute_metrics_comparator_idempotency_key`-derived
      dedupe key.
    * ``result_id`` -- carried-through identifier from the input.
    * ``scenario_result_id`` -- carried-through scenario id from
      the input.
    * ``emitted_at`` -- the typed ISO-8601 timestamp the comparator
      emitted the result (UTC, timezone-aware).
    * ``invalidated_axes`` -- the typed list of axis Literal values
      whose per-axis :attr:`MetricsAxisDelta.invalidated` is ``True``
      (aggregates the per-axis flags for convenient cross-checking).
    * ``overall_confidence`` -- the typed [0.0, 1.0] confidence score
      across all axes (the minimum non-invalidated per-axis confidence,
      OR 0.0 when all axes are invalidated).

    Per the auto-memory ``feedback_flat_structured_output`` rule the
    typed control fields are flat primitives. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every Pydantic field
    validates at construction; unknown fields fail closed via the
    ``ConfigDict(extra="forbid")`` discipline.
    """

    model_config = ConfigDict(extra="forbid")

    axis_deltas: list[MetricsAxisDelta] = Field(default_factory=list)
    """The list of typed :class:`MetricsAxisDelta` per-axis delta
    records the comparator emitted (one per axis); OR empty when the
    projection failed structurally (in which case the gap is recorded
    in :attr:`gap_findings`).
    """

    gap_findings: list[MetricsComparatorGap] = Field(default_factory=list)
    """The list of typed :class:`MetricsComparatorGap` records emitted
    when a projection step fails structurally (per
    :data:`METRICS_COMPARATOR_FAILURE_ID`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    comparator NEVER raises on input -- structural failures are
    recorded as typed gap findings (refs-only; the result id attempted
    + scenario result id + failure reason + observed timestamp +
    optional evidence-ref ids).
    """

    idempotency_key: str
    """The deterministic
    :func:`compute_metrics_comparator_idempotency_key`-derived dedupe
    key. Per doc-18:127-129 *"Historical replay is immutable by corpus
    id and scenario id. New assumptions require a new result
    version."* the idempotency key is the typed identity surface that
    lets subsequent re-runs of the comparator against the same inputs
    produce byte-identical results.
    """

    result_id: str
    """Doc-18:80 -- the typed stable comparator-result identifier
    string (carried through from the input
    :attr:`MetricsComparatorInputs.result_id`).
    """

    scenario_result_id: str
    """Doc-18:80 -- the typed scenario result identifier (carried
    through from the input
    :attr:`MetricsComparatorInputs.scenario_result.result_id`). Per
    AC4 binding (doc-18:165-166) the scenario_result_id is the typed
    cross-Slice-17 reference surface; the Slice 17 1st sub-slice
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.counterfactual_result_refs:
    list[str]` field carries this identifier as the by-name reference
    for behavior-changing recommendations.
    """

    emitted_at: datetime
    """ISO-8601 timestamp the comparator emitted the result (UTC,
    timezone-aware). Mirrors the Slice 18 3rd sub-slice + Slice 18 4th
    sub-slice :attr:`SummaryReplayGap.observed_at` /
    :attr:`EventReplayGap.observed_at` contract.
    """

    invalidated_axes: list[MetricsAxis] = Field(default_factory=list)
    """The list of typed :data:`MetricsAxis` Literal values whose
    per-axis :attr:`MetricsAxisDelta.invalidated` is ``True``.

    Aggregates the per-axis flags for convenient cross-checking by
    callers (e.g. the future Slice 18 7th sub-slice Slice 17
    recommendation citation hook MUST refuse to cite a comparator
    result whose ``hours`` axis is invalidated for a wave-size policy
    recommendation per the doc-18:138 + doc-18:140-146 disciplines).
    """

    overall_confidence: float = Field(ge=0.0, le=1.0)
    """Doc-18:92 -- the typed [0.0, 1.0] overall confidence score
    across all axes (the minimum non-invalidated per-axis confidence,
    OR 0.0 when all axes are invalidated).

    Per Pydantic ``ge=0.0`` + ``le=1.0`` validation the field accepts
    only values in [0.0, 1.0]; out-of-range values fail closed with a
    typed ``ValidationError``.

    The minimum is INTENTIONALLY conservative: a single low-confidence
    axis drags the overall confidence down so the future Slice 18 7th
    sub-slice Slice 17 recommendation citation hook can use this
    single field as the authoritative confidence-floor surface.
    """


# --- Pure canonical-JSON + SHA-256 helpers (mirrors Slice 18 1st/2nd/3rd/4th
#     sub-slice canonical-JSON + SHA-256 discipline verbatim) ---------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.execution_control.counterfactual_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_replay_loader._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_summary_replay._canonical_json`
    + :func:`iriai_build_v2.execution_control.counterfactual_event_replay._canonical_json`
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
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def compute_metrics_comparator_idempotency_key(
    *,
    result_id: str,
    result_version: str,
    scenario_result_id: str,
    mode: ReplayMode,
    baseline_metric_definition_names: list[str],
) -> str:
    """Compute the deterministic SHA-256-derived idempotency key for a
    metrics-comparator result.

    Mirrors the Slice 18 3rd sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_summary_replay.compute_summary_replay_idempotency_key`
    + Slice 18 4th sub-slice
    :func:`~iriai_build_v2.execution_control.counterfactual_event_replay.compute_event_replay_idempotency_key`
    canonical-JSON + SHA-256 discipline verbatim; the key is computed
    over the 5 logical inputs:

    * ``result_id`` -- the typed stable comparator-result identifier
      string (per doc-18:80).
    * ``result_version`` -- the typed result-version string (per
      doc-18:81 + doc-18:128-129 *"New assumptions require a new
      result version."* the version axis is part of the dedupe key so
      a new version cleanly produces a new key + a new row, rather
      than overwriting prior rows).
    * ``scenario_result_id`` -- the scenario result identifier (per
      doc-18:80 + doc-18:127-129).
    * ``mode`` -- the typed
      :data:`~iriai_build_v2.execution_control.counterfactual_replay.ReplayMode`
      classification (per doc-18:61). The mode is part of the dedupe
      key so a future re-run of the same scenario under a different
      mode (e.g. ``event_replay`` once the 4th sub-slice lands)
      cleanly produces a new key.
    * ``baseline_metric_definition_names`` -- the list of Slice 15
      metric-definition names the comparator projected over (per
      doc-15:79 :attr:`GovernanceMetricValue.definition_name`). The
      list is sorted before digesting so the key is order-invariant
      w.r.t. metric ordering.

    Per doc-18:128-129 *"Historical replay is immutable by corpus id
    and scenario id. New assumptions require a new result version."*
    the helper is the cross-process freshness contract subsequent
    sub-slices rely on when detecting duplicate results across re-runs
    of the comparator.
    """

    payload: dict[str, Any] = {
        "result_id": result_id,
        "result_version": result_version,
        "scenario_result_id": scenario_result_id,
        "mode": mode,
        # Sort the list-of-str inputs so the key is order-invariant
        # w.r.t. list ordering.
        "baseline_metric_definition_names": sorted(
            baseline_metric_definition_names
        ),
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


# --- The comparator class (doc-18:115) -----------------------------------------


class CounterfactualMetricsComparator:
    """Counterfactual metrics comparator (doc-18:115 step 5).

    Per *"Compare baseline vs scenario outcomes using Slice 15
    metrics."* the comparator consumes typed Slice 15
    :class:`GovernanceMetricValue` baseline records + a typed Slice 18
    1st sub-slice :class:`CounterfactualResult` scenario result (emitted
    by the Slice 18 3rd sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_summary_replay.CounterfactualSummaryReplayEngine`
    OR the Slice 18 4th sub-slice
    :class:`~iriai_build_v2.execution_control.counterfactual_event_replay.CounterfactualEventReplayEngine`)
    + emits a typed :class:`MetricsComparatorResult` with one
    :class:`MetricsAxisDelta` per axis (``hours`` / ``repair_cycles`` /
    ``commit_failures`` / ``risk_change``).

    **Refs-only projection (doc-18:186-249 + Slice 13A invariant).**
    The comparator surfaces only typed Slice 13a
    :class:`GovernanceEvidenceRef` records from the baseline metric +
    scenario-result inputs onto the per-axis
    :attr:`MetricsAxisDelta.evidence_refs` list (the typed BaseModel
    form is preserved; no raw artifact body hydration).

    **Fail-closed discipline (feedback_no_silent_degradation).** The
    comparator NEVER raises a failure to the caller. Any structural
    failure projects onto a typed :class:`MetricsComparatorGap` finding
    emitted on the :attr:`MetricsComparatorResult.gap_findings` list.
    The corresponding typed failure id
    :data:`METRICS_COMPARATOR_FAILURE_ID`
    (``metrics_comparator_failed``) registers under the EXISTING
    ``evidence_corruption`` failure_class with the EXISTING NON-
    blocking RouteAction ``retry_governance_projection`` (REUSED from
    Slice 14 2nd sub-slice; NOT a new route action).

    **Activation-authority boundary (doc-18:123-125 + doc-18:164 AC3).**
    The comparator exposes ONLY the :meth:`compare` read method; it
    does NOT expose any mutation surface. The structural test surface
    enforces this discipline by inspecting the class's public method
    list. Comparator results are review / governance artifacts only;
    never runtime policy authority.

    **Per-axis projection (doc-18:88-92).** The comparator projects
    one typed :class:`MetricsAxisDelta` record per axis using:

    * ``hours`` -- the scenario result's
      :attr:`CounterfactualResult.estimated_delta_hours` field paired
      with the baseline metric whose
      :attr:`GovernanceMetricValue.definition_name` is in
      :data:`_HOURS_METRIC_NAMES` (median of values when multiple
      baseline metrics match).
    * ``repair_cycles`` -- the scenario result's
      :attr:`CounterfactualResult.estimated_delta_repair_cycles` field
      paired with the baseline metric whose
      :attr:`GovernanceMetricValue.definition_name` is
      ``repair_cycles_per_task``.
    * ``commit_failures`` -- the scenario result's
      :attr:`CounterfactualResult.estimated_delta_commit_failures`
      field paired with the baseline metric whose
      :attr:`GovernanceMetricValue.definition_name` is
      ``commit_failures_per_task``.
    * ``risk_change`` -- the scenario result's
      :attr:`CounterfactualResult.estimated_risk_change` Literal (no
      baseline; the axis carries the typed Literal directly).

    Per doc-18:50 *"Counterfactual duration estimates may be ranges
    rather than exact values."* the comparator treats per-axis
    confidence as the harmonic mean of the baseline + scenario
    confidences (so a low input lowers the per-axis confidence).

    The comparator is stateless (no instance state beyond
    construction); callers MAY reuse a single instance across multiple
    (baseline, scenario) pairs.
    """

    def compare(
        self,
        inputs: MetricsComparatorInputs,
    ) -> MetricsComparatorResult:
        """Run the typed metrics-comparator projection for the typed
        inputs.

        Per doc-18:115 step 5 the method:

        1. Validates the bounded-input contract
           (:attr:`MetricsComparatorInputs.max_baseline_metrics`).
        2. Validates the typed required-field contract (``result_id``
           non-empty; ``baseline_metrics`` non-empty; the scenario
           result is structurally valid).
        3. Computes the deterministic idempotency key.
        4. Projects one typed :class:`MetricsAxisDelta` record per axis
           per doc-18:88-92.
        5. Aggregates the per-axis invalidated flags into
           :attr:`MetricsComparatorResult.invalidated_axes` +
           computes :attr:`overall_confidence`.
        6. Emits the typed :class:`MetricsComparatorResult` with all
           fields populated.
        7. Records projection failures in
           :attr:`MetricsComparatorResult.gap_findings` per
           ``feedback_no_silent_degradation``.

        Per the auto-memory ``feedback_no_silent_degradation`` rule
        the method NEVER raises on input -- structural failures
        produce typed gap findings.
        """

        gap_findings: list[MetricsComparatorGap] = []
        emitted_at = _utcnow()
        idempotency_key = compute_metrics_comparator_idempotency_key(
            result_id=inputs.result_id,
            result_version=inputs.result_version,
            scenario_result_id=inputs.scenario_result.result_id,
            mode=inputs.mode,
            baseline_metric_definition_names=[
                m.definition_name for m in inputs.baseline_metrics
            ],
        )

        # Bounded-input check (per doc-18:150 + Slice 13A invariant
        # bounded-reads discipline).
        if len(inputs.baseline_metrics) > inputs.max_baseline_metrics:
            gap_findings.append(
                MetricsComparatorGap(
                    failure_id=METRICS_COMPARATOR_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    scenario_result_id=inputs.scenario_result.result_id,
                    reason="baseline_metrics_exceeded_bound",
                    observed_at=emitted_at,
                    evidence_payload={
                        "received_count": len(inputs.baseline_metrics),
                        "max_bound": inputs.max_baseline_metrics,
                    },
                )
            )
            return self._empty_result_with_gaps(
                inputs=inputs,
                idempotency_key=idempotency_key,
                emitted_at=emitted_at,
                gap_findings=gap_findings,
            )

        # Required-field check (per doc-18:80 + doc-18:115).
        if not inputs.result_id or not inputs.result_id.strip():
            gap_findings.append(
                MetricsComparatorGap(
                    failure_id=METRICS_COMPARATOR_FAILURE_ID,
                    result_id_attempted=inputs.result_id or "<empty>",
                    scenario_result_id=inputs.scenario_result.result_id,
                    reason="result_id_empty",
                    observed_at=emitted_at,
                )
            )
            return self._empty_result_with_gaps(
                inputs=inputs,
                idempotency_key=idempotency_key,
                emitted_at=emitted_at,
                gap_findings=gap_findings,
            )

        if not inputs.baseline_metrics:
            gap_findings.append(
                MetricsComparatorGap(
                    failure_id=METRICS_COMPARATOR_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    scenario_result_id=inputs.scenario_result.result_id,
                    reason="baseline_metrics_empty",
                    observed_at=emitted_at,
                )
            )
            return self._empty_result_with_gaps(
                inputs=inputs,
                idempotency_key=idempotency_key,
                emitted_at=emitted_at,
                gap_findings=gap_findings,
            )

        if (
            not inputs.scenario_result.result_id
            or not inputs.scenario_result.result_id.strip()
        ):
            gap_findings.append(
                MetricsComparatorGap(
                    failure_id=METRICS_COMPARATOR_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    scenario_result_id=inputs.scenario_result.result_id
                    or "<empty>",
                    reason="scenario_result_invalid",
                    observed_at=emitted_at,
                    evidence_payload={
                        "missing_field": "scenario_result.result_id",
                    },
                )
            )
            return self._empty_result_with_gaps(
                inputs=inputs,
                idempotency_key=idempotency_key,
                emitted_at=emitted_at,
                gap_findings=gap_findings,
            )

        # Project per-axis delta records.
        axis_deltas: list[MetricsAxisDelta] = []
        try:
            axis_deltas.append(self._project_axis_hours(inputs))
            axis_deltas.append(self._project_axis_repair_cycles(inputs))
            axis_deltas.append(self._project_axis_commit_failures(inputs))
            axis_deltas.append(self._project_axis_risk_change(inputs))
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                MetricsComparatorGap(
                    failure_id=METRICS_COMPARATOR_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    scenario_result_id=inputs.scenario_result.result_id,
                    reason="result_construction_failed",
                    observed_at=emitted_at,
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return self._empty_result_with_gaps(
                inputs=inputs,
                idempotency_key=idempotency_key,
                emitted_at=emitted_at,
                gap_findings=gap_findings,
            )

        # Aggregate per-axis flags + compute overall confidence.
        invalidated_axes = [d.axis for d in axis_deltas if d.invalidated]
        overall_confidence = self._compute_overall_confidence(axis_deltas)

        try:
            result = MetricsComparatorResult(
                axis_deltas=axis_deltas,
                gap_findings=gap_findings,
                idempotency_key=idempotency_key,
                result_id=inputs.result_id,
                scenario_result_id=inputs.scenario_result.result_id,
                emitted_at=emitted_at,
                invalidated_axes=invalidated_axes,
                overall_confidence=overall_confidence,
            )
        except (ValidationError, ValueError, TypeError) as exc:
            gap_findings.append(
                MetricsComparatorGap(
                    failure_id=METRICS_COMPARATOR_FAILURE_ID,
                    result_id_attempted=inputs.result_id,
                    scenario_result_id=inputs.scenario_result.result_id,
                    reason="result_construction_failed",
                    observed_at=emitted_at,
                    evidence_payload={
                        "exception_type": type(exc).__name__,
                        "exception_message": str(exc)[:500],
                    },
                )
            )
            return self._empty_result_with_gaps(
                inputs=inputs,
                idempotency_key=idempotency_key,
                emitted_at=emitted_at,
                gap_findings=gap_findings,
            )

        return result

    # --- per-axis projection helpers ----------------------------------------

    def _project_axis_hours(
        self, inputs: MetricsComparatorInputs
    ) -> MetricsAxisDelta:
        """Project the ``hours`` per-axis delta record (doc-18:88).

        Pairs the scenario's
        :attr:`CounterfactualResult.estimated_delta_hours` field with
        the median of the baseline metric values whose definition name
        is in :data:`_HOURS_METRIC_NAMES` (median of values when
        multiple baseline metrics match per doc-15:103 + doc-15:110-112).
        """

        scenario_delta = inputs.scenario_result.estimated_delta_hours
        hours_metrics = [
            m
            for m in inputs.baseline_metrics
            if m.definition_name in _HOURS_METRIC_NAMES
        ]
        baseline_value = _median_metric_value(hours_metrics)
        baseline_unit = _representative_unit(hours_metrics)
        baseline_confidence = _representative_confidence(hours_metrics)
        invalidated = baseline_value is None or scenario_delta is None
        validity_limits = self._compose_axis_validity_limits(
            inputs=inputs,
            baseline_metrics=hours_metrics,
            invalidated_due_to_baseline=baseline_value is None,
            invalidated_due_to_scenario=scenario_delta is None,
        )
        evidence_refs = self._collect_axis_evidence_refs(
            inputs=inputs, baseline_metrics=hours_metrics
        )
        confidence = self._compute_axis_confidence(
            baseline_confidence=baseline_confidence,
            scenario_confidence=inputs.scenario_result.confidence,
            invalidated=invalidated,
        )
        return MetricsAxisDelta(
            axis=_AXIS_HOURS,
            baseline_value=baseline_value,
            baseline_unit=baseline_unit,
            baseline_confidence=baseline_confidence,
            scenario_estimated_delta=scenario_delta,
            scenario_estimated_risk_change=None,
            scenario_confidence=inputs.scenario_result.confidence,
            confidence=confidence,
            validity_limits=validity_limits,
            evidence_refs=evidence_refs,
            invalidated=invalidated,
        )

    def _project_axis_repair_cycles(
        self, inputs: MetricsComparatorInputs
    ) -> MetricsAxisDelta:
        """Project the ``repair_cycles`` per-axis delta record
        (doc-18:89).
        """

        scenario_delta = inputs.scenario_result.estimated_delta_repair_cycles
        repair_metrics = [
            m
            for m in inputs.baseline_metrics
            if m.definition_name == _REPAIR_CYCLES_METRIC_NAME
        ]
        baseline_value = _median_metric_value(repair_metrics)
        baseline_unit = _representative_unit(repair_metrics)
        baseline_confidence = _representative_confidence(repair_metrics)
        invalidated = baseline_value is None or scenario_delta is None
        validity_limits = self._compose_axis_validity_limits(
            inputs=inputs,
            baseline_metrics=repair_metrics,
            invalidated_due_to_baseline=baseline_value is None,
            invalidated_due_to_scenario=scenario_delta is None,
        )
        evidence_refs = self._collect_axis_evidence_refs(
            inputs=inputs, baseline_metrics=repair_metrics
        )
        confidence = self._compute_axis_confidence(
            baseline_confidence=baseline_confidence,
            scenario_confidence=inputs.scenario_result.confidence,
            invalidated=invalidated,
        )
        return MetricsAxisDelta(
            axis=_AXIS_REPAIR_CYCLES,
            baseline_value=baseline_value,
            baseline_unit=baseline_unit,
            baseline_confidence=baseline_confidence,
            scenario_estimated_delta=scenario_delta,
            scenario_estimated_risk_change=None,
            scenario_confidence=inputs.scenario_result.confidence,
            confidence=confidence,
            validity_limits=validity_limits,
            evidence_refs=evidence_refs,
            invalidated=invalidated,
        )

    def _project_axis_commit_failures(
        self, inputs: MetricsComparatorInputs
    ) -> MetricsAxisDelta:
        """Project the ``commit_failures`` per-axis delta record
        (doc-18:90).
        """

        scenario_delta = inputs.scenario_result.estimated_delta_commit_failures
        commit_metrics = [
            m
            for m in inputs.baseline_metrics
            if m.definition_name == _COMMIT_FAILURES_METRIC_NAME
        ]
        baseline_value = _median_metric_value(commit_metrics)
        baseline_unit = _representative_unit(commit_metrics)
        baseline_confidence = _representative_confidence(commit_metrics)
        invalidated = baseline_value is None or scenario_delta is None
        validity_limits = self._compose_axis_validity_limits(
            inputs=inputs,
            baseline_metrics=commit_metrics,
            invalidated_due_to_baseline=baseline_value is None,
            invalidated_due_to_scenario=scenario_delta is None,
        )
        evidence_refs = self._collect_axis_evidence_refs(
            inputs=inputs, baseline_metrics=commit_metrics
        )
        confidence = self._compute_axis_confidence(
            baseline_confidence=baseline_confidence,
            scenario_confidence=inputs.scenario_result.confidence,
            invalidated=invalidated,
        )
        return MetricsAxisDelta(
            axis=_AXIS_COMMIT_FAILURES,
            baseline_value=baseline_value,
            baseline_unit=baseline_unit,
            baseline_confidence=baseline_confidence,
            scenario_estimated_delta=scenario_delta,
            scenario_estimated_risk_change=None,
            scenario_confidence=inputs.scenario_result.confidence,
            confidence=confidence,
            validity_limits=validity_limits,
            evidence_refs=evidence_refs,
            invalidated=invalidated,
        )

    def _project_axis_risk_change(
        self, inputs: MetricsComparatorInputs
    ) -> MetricsAxisDelta:
        """Project the ``risk_change`` per-axis delta record
        (doc-18:91).

        Unlike the 3 numeric axes the ``risk_change`` axis has no
        baseline metric; the typed scenario
        :attr:`CounterfactualResult.estimated_risk_change` Literal is
        carried directly.
        """

        risk_change = inputs.scenario_result.estimated_risk_change
        invalidated = risk_change == "unknown"
        validity_limits = self._compose_axis_validity_limits(
            inputs=inputs,
            baseline_metrics=[],
            invalidated_due_to_baseline=False,
            invalidated_due_to_scenario=invalidated,
        )
        evidence_refs = list(inputs.scenario_result.policy_provenance_refs)
        confidence = self._compute_axis_confidence(
            baseline_confidence=None,
            scenario_confidence=inputs.scenario_result.confidence,
            invalidated=invalidated,
        )
        return MetricsAxisDelta(
            axis=_AXIS_RISK_CHANGE,
            baseline_value=None,
            baseline_unit=None,
            baseline_confidence=None,
            scenario_estimated_delta=None,
            scenario_estimated_risk_change=risk_change,
            scenario_confidence=inputs.scenario_result.confidence,
            confidence=confidence,
            validity_limits=validity_limits,
            evidence_refs=evidence_refs,
            invalidated=invalidated,
        )

    # --- shared helpers -----------------------------------------------------

    def _compose_axis_validity_limits(
        self,
        *,
        inputs: MetricsComparatorInputs,
        baseline_metrics: list[GovernanceMetricValue],
        invalidated_due_to_baseline: bool,
        invalidated_due_to_scenario: bool,
    ) -> list[str]:
        """Compose the typed per-axis validity_limits list (per
        doc-18:85).

        Carries the union of:

        * Scenario result validity_limits (per doc-18:85).
        * Baseline-metric exclusions (per doc-15:88; the metric
          extractor emits exclusions like
          ``"active_work_excluded"`` /
          ``"preview_only_evidence_excluded"`` /
          ``"insufficient_sample_excluded"``).
        * Comparator-specific limits (e.g.
          ``"baseline_value_unavailable"`` when the baseline metric
          for this axis is missing or has ``value=None``;
          ``"scenario_delta_unavailable"`` when the scenario does not
          quantify the axis).
        """

        limits: list[str] = []
        limits.extend(inputs.scenario_result.validity_limits)
        for metric in baseline_metrics:
            limits.extend(metric.exclusions)
        if invalidated_due_to_baseline:
            limits.append("baseline_value_unavailable")
        if invalidated_due_to_scenario:
            limits.append("scenario_delta_unavailable")
        # Dedupe preserving order so the typed limits list is stable
        # across re-runs against identical inputs.
        seen: set[str] = set()
        deduped: list[str] = []
        for limit in limits:
            if limit not in seen:
                seen.add(limit)
                deduped.append(limit)
        return deduped

    def _collect_axis_evidence_refs(
        self,
        *,
        inputs: MetricsComparatorInputs,
        baseline_metrics: list[GovernanceMetricValue],
    ) -> list[GovernanceEvidenceRef]:
        """Collect the typed Slice 13a evidence-ref list for an axis
        (refs-only; no raw artifact body hydration).

        Per doc-18:186-249 the comparator surfaces only typed Slice 13a
        :class:`GovernanceEvidenceRef` records: the union of the
        baseline-metric :attr:`evidence_refs` + the scenario-result
        :attr:`policy_provenance_refs`.

        Deduped by typed (authority, ref_id, digest) tuple so the same
        ref appearing in both surfaces is emitted exactly once on the
        per-axis record.
        """

        seen: set[tuple[str, str, str]] = set()
        deduped: list[GovernanceEvidenceRef] = []
        for metric in baseline_metrics:
            for ref in metric.evidence_refs:
                key = (ref.authority, ref.ref_id, ref.digest)
                if key not in seen:
                    seen.add(key)
                    deduped.append(ref)
        for ref in inputs.scenario_result.policy_provenance_refs:
            key = (ref.authority, ref.ref_id, ref.digest)
            if key not in seen:
                seen.add(key)
                deduped.append(ref)
        return deduped

    def _compute_axis_confidence(
        self,
        *,
        baseline_confidence: float | None,
        scenario_confidence: float,
        invalidated: bool,
    ) -> float:
        """Compute the typed per-axis confidence score in [0.0, 1.0].

        Per doc-18:92 + doc-15:84 the per-axis confidence is the
        harmonic mean of the baseline-metric confidence + the scenario-
        result confidence so a low-confidence input lowers the per-axis
        confidence.

        When the baseline confidence is None (e.g. for the
        ``risk_change`` axis or when no matching baseline metric
        exists) the per-axis confidence falls back to the scenario
        confidence.

        When the axis is invalidated the per-axis confidence is
        clamped to 0.0 (the future Slice 18 7th sub-slice Slice 17
        recommendation citation hook MUST refuse a 0.0-confidence
        axis).
        """

        if invalidated:
            return 0.0
        if baseline_confidence is None:
            return _clamp_unit(scenario_confidence)
        # Harmonic mean of two confidences in [0.0, 1.0]. Defensive
        # against zero inputs (harmonic mean is undefined if any input
        # is 0; treat as 0.0 if either is 0.0).
        if baseline_confidence <= 0.0 or scenario_confidence <= 0.0:
            return 0.0
        harmonic = 2.0 / ((1.0 / baseline_confidence) + (1.0 / scenario_confidence))
        return _clamp_unit(harmonic)

    def _compute_overall_confidence(
        self, axis_deltas: list[MetricsAxisDelta]
    ) -> float:
        """Compute the typed overall [0.0, 1.0] confidence across all
        axes.

        The minimum non-invalidated per-axis confidence, OR 0.0 when
        all axes are invalidated. The minimum is INTENTIONALLY
        conservative: a single low-confidence axis drags the overall
        confidence down so the future Slice 18 7th sub-slice Slice 17
        recommendation citation hook can use this single field as the
        authoritative confidence-floor surface.
        """

        non_invalidated = [d for d in axis_deltas if not d.invalidated]
        if not non_invalidated:
            return 0.0
        return min(d.confidence for d in non_invalidated)

    def _empty_result_with_gaps(
        self,
        *,
        inputs: MetricsComparatorInputs,
        idempotency_key: str,
        emitted_at: datetime,
        gap_findings: list[MetricsComparatorGap],
    ) -> MetricsComparatorResult:
        """Construct an empty :class:`MetricsComparatorResult` carrying
        the typed gap findings (used when the projection fails
        structurally and the per-axis records cannot be produced).
        """

        return MetricsComparatorResult(
            axis_deltas=[],
            gap_findings=gap_findings,
            idempotency_key=idempotency_key,
            result_id=inputs.result_id or "<empty>",
            scenario_result_id=inputs.scenario_result.result_id or "<empty>",
            emitted_at=emitted_at,
            invalidated_axes=[],
            overall_confidence=0.0,
        )


# --- module-level pure helpers ---------------------------------------------


def _median_metric_value(
    metrics: list[GovernanceMetricValue],
) -> float | None:
    """Return the median of a list of typed
    :class:`GovernanceMetricValue` values (filtering out None values
    per the doc-15:149-150 insufficient-sample case).

    Returns ``None`` when no metric carries a numeric value.
    """

    numeric_values: list[float] = []
    for metric in metrics:
        if metric.value is None:
            continue
        try:
            numeric_values.append(float(metric.value))
        except (TypeError, ValueError):
            continue
    if not numeric_values:
        return None
    numeric_values.sort()
    n = len(numeric_values)
    if n % 2 == 1:
        return numeric_values[n // 2]
    return (numeric_values[(n // 2) - 1] + numeric_values[n // 2]) / 2.0


def _representative_unit(
    metrics: list[GovernanceMetricValue],
) -> str | None:
    """Return the first non-empty :attr:`GovernanceMetricValue.unit`
    from the list, or ``None`` when the list is empty.
    """

    for metric in metrics:
        if metric.unit:
            return metric.unit
    return None


def _representative_confidence(
    metrics: list[GovernanceMetricValue],
) -> float | None:
    """Return the median confidence of the list of typed
    :class:`GovernanceMetricValue` records (per doc-15:84).

    Returns ``None`` when the list is empty.
    """

    confidences = [
        float(m.confidence) for m in metrics if m.confidence is not None
    ]
    if not confidences:
        return None
    confidences.sort()
    n = len(confidences)
    if n % 2 == 1:
        return confidences[n // 2]
    return (confidences[(n // 2) - 1] + confidences[n // 2]) / 2.0


def _clamp_unit(value: float) -> float:
    """Clamp a float value into the [0.0, 1.0] unit interval (defensive
    against floating-point drift in the per-axis confidence
    computation).
    """

    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value
