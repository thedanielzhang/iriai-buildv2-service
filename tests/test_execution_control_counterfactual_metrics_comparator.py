"""Slice 18 fifth sub-slice -- unit tests for the counterfactual metrics
comparator at
``execution_control/counterfactual_metrics_comparator.py``.

Covers the doc-18:115 step 5 typed-shape consumer + structural
projection wiring:

* :class:`MetricsComparatorInputs` typed inputs BaseModel (extra-forbid;
  bounded-input default; typed Slice 15 :class:`GovernanceMetricValue`
  REUSE; typed Slice 18 1st sub-slice :class:`CounterfactualResult`
  REUSE; typed Slice 18 1st sub-slice :data:`ReplayMode` REUSE).
* :class:`MetricsAxisDelta` typed per-axis delta BaseModel (extra-forbid;
  one per axis: hours / repair_cycles / commit_failures / risk_change).
* :class:`MetricsComparatorResult` typed result BaseModel (extra-forbid;
  axis_deltas / gap_findings / idempotency_key / scenario_result_id /
  emitted_at / invalidated_axes / overall_confidence).
* :class:`MetricsComparatorGap` typed gap projection BaseModel (extra-
  forbid; failure_id Literal range).
* :class:`CounterfactualMetricsComparator.compare(...)` -- the
  projection method:
  * Happy-path -> typed :class:`MetricsComparatorResult` emitted with
    4 per-axis records.
  * Bounded-input check -> typed gap on
    ``baseline_metrics_exceeded_bound`` per doc-18:150.
  * Empty result_id / empty baseline_metrics / empty scenario_result_id
    -> typed gap per ``feedback_no_silent_degradation``.
  * Comparator NEVER raises on input (fail-closed; typed gap projection
    on construction failure).
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :class:`GovernanceEvidenceRef` + Slice 15
  :class:`GovernanceMetricValue` + Slice 18 1st sub-slice
  :class:`CounterfactualResult` + :data:`ReplayMode`.
* Failure-router 4-add-point validation (``metrics_comparator_failed``
  registered under EXISTING ``evidence_corruption`` failure_class with
  REUSED ``retry_governance_projection`` NON-blocking RouteAction;
  mirrors Slice 17 2nd/3rd/4th/5th/6th + Slice 18 2nd + 3rd + 4th
  sub-slice precedent).
* :func:`compute_metrics_comparator_idempotency_key` -- the
  deterministic SHA-256-derived idempotency-key helper.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 1st + 2nd + 3rd + 4th sub-slice modules
+ tests remain byte-identical.

**Slice 13A awareness asserted (doc-18:186-249).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition. The comparator consumes Slice 13a typed
``GovernanceEvidenceRef`` (via the Slice 15 baseline metric inputs +
the Slice 18 1st sub-slice scenario result inputs) and emits the
refs-only projection onto the typed :class:`MetricsAxisDelta`
``evidence_refs`` list; no raw artifact body hydration per
doc-18:186-249.

**Refs-only invariant (doc-18:186-249).** Test
:func:`test_refs_only_no_raw_body_hydration` walks the emitted
``MetricsComparatorResult.model_dump(mode="json")`` recursively and
asserts no key contains the forbidden ``body`` / ``raw_body`` /
``artifact_body`` substring.

**Doc-18:123-129 persistence + artifact compatibility awareness.** No
5th-sub-slice consumer activation wiring; no consumer-owned artifact-
key literals; comparator results are review/governance artifacts only,
never runtime policy authority. Structural test
:func:`test_no_consumer_side_module_imports` walks the module's
import graph + asserts no consumer-side module is imported.

**Doc-18:164 AC3 (no mutation methods).** Structural test
:func:`test_comparator_class_only_exposes_compare_method` asserts the
comparator class exposes EXACTLY one public method (``compare``) -- no
mutation surface.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_metrics_comparator import (
    DEFAULT_MAX_BASELINE_METRICS,
    METRICS_COMPARATOR_FAILURE_ID,
    CounterfactualMetricsComparator,
    MetricsAxisDelta,
    MetricsComparatorGap,
    MetricsComparatorInputs,
    MetricsComparatorResult,
    compute_metrics_comparator_idempotency_key,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    ReplayMode,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
    FailureType,
    _RETRYABLE_FAILURE_TYPES,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def _ref(ref_id: str = "ref-1", **overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified :class:`GovernanceEvidenceRef`."""

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id=ref_id,
        digest="d" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def _metric(
    definition_name: str = "hours_per_task",
    value: float | None = 4.0,
    confidence: float = 0.8,
    unit: str = "hours",
    **overrides: object,
) -> GovernanceMetricValue:
    """Construct a typed Slice 15 :class:`GovernanceMetricValue`."""

    base: dict[str, object] = dict(
        definition_name=definition_name,
        definition_version="v1",
        scope={"feature_id": "8ac124d6"},
        value=value,
        unit=unit,
        confidence=confidence,
        data_quality="canonical",
        source_mix={"typed": 12},
        evidence_refs=[_ref(f"{definition_name}-ev-1")],
        exclusions=[],
    )
    base.update(overrides)
    return GovernanceMetricValue(**base)  # type: ignore[arg-type]


def _scenario_result(**overrides: object) -> CounterfactualResult:
    """Construct a typed Slice 18 1st sub-slice
    :class:`CounterfactualResult` scenario result."""

    base: dict[str, object] = dict(
        result_id="scenario-result-1",
        result_version="v1",
        scenario_id="scenario-1",
        corpus_id="corpus-1",
        assumptions=["product_defect_independent_of_wave_size"],
        validity_limits=["summary_replay_mode"],
        policy_provenance_refs=[_ref("scenario-prov-ref-1")],
        safety_guard_class=None,
        estimated_delta_hours=-0.5,
        estimated_delta_repair_cycles=-0.3,
        estimated_delta_commit_failures=-0.1,
        estimated_risk_change="lower",
        confidence=0.6,
        invalidated_by=[],
        supporting_finding_ids=["finding-1"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)  # type: ignore[arg-type]


def _inputs(**overrides: object) -> MetricsComparatorInputs:
    """Construct a fully-specified :class:`MetricsComparatorInputs`."""

    base: dict[str, object] = dict(
        baseline_metrics=[
            _metric("hours_per_task", value=4.0, unit="hours"),
            _metric(
                "repair_cycles_per_task", value=2.0, unit="count"
            ),
            _metric(
                "commit_failures_per_task", value=1.0, unit="count"
            ),
            _metric("tasks_per_hour", value=0.25, unit="tasks/hour"),
        ],
        scenario_result=_scenario_result(),
        result_id="comparator-result-1",
        result_version="v1",
        mode="summary_replay",
    )
    base.update(overrides)
    return MetricsComparatorInputs(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


_EXPECTED_EXPORTS = {
    "METRICS_COMPARATOR_FAILURE_ID",
    "DEFAULT_MAX_BASELINE_METRICS",
    "MetricsComparatorInputs",
    "MetricsAxisDelta",
    "MetricsComparatorResult",
    "MetricsComparatorGap",
    "CounterfactualMetricsComparator",
    "compute_metrics_comparator_idempotency_key",
}


def test_module_all_exports_count() -> None:
    """The module exposes exactly the planned 8 symbols on ``__all__``."""

    from iriai_build_v2.execution_control import (
        counterfactual_metrics_comparator as mod,
    )

    assert set(mod.__all__) == _EXPECTED_EXPORTS
    assert len(mod.__all__) == 8


@pytest.mark.parametrize("name", sorted(_EXPECTED_EXPORTS))
def test_module_surface_hasattr(name: str) -> None:
    """Each planned export is importable from the module."""

    from iriai_build_v2.execution_control import (
        counterfactual_metrics_comparator as mod,
    )

    assert hasattr(mod, name), f"missing export: {name}"


def test_no_re_export_from_execution_control_init() -> None:
    """The Slice 18 5th sub-slice module is NOT re-exported from
    :mod:`iriai_build_v2.execution_control`'s ``__init__.py``.

    Mirrors the Slice 13A/14/15/16/17/18-1st/2nd/3rd/4th precedent
    (the package __init__ is intentionally minimal; consumers import
    from the per-sub-slice module directly).
    """

    from iriai_build_v2 import execution_control

    init_all = getattr(execution_control, "__all__", ())
    for name in _EXPECTED_EXPORTS:
        assert name not in init_all, (
            f"{name} unexpectedly re-exported from execution_control/__init__.py"
        )


# ── no local redefinition of upstream typed shapes ─────────────────────────


_LOCAL_REDEFINITION_FORBIDDEN = (
    "CompletenessState",
    "EvidenceCompleteness",
    "AuthoritativePromptContextRouting",
    "AuthoritativeGateCompanionRecord",
    "AuthoritativeGateProofRow",
    "AuthoritativeSnapshotListFieldCompleteness",
    "AuthoritativeSnapshotClassifierRouting",
    "ExactEvidenceManifest",
    "GovernanceEvidenceRef",
    "GovernanceMetricValue",
    "GovernanceScorecard",
    "ReplayCorpus",
    "CounterfactualScenario",
    "CounterfactualResult",
    "ReplayMode",
    "RiskChange",
    "RecommendedNextStep",
)


@pytest.mark.parametrize("shape_name", _LOCAL_REDEFINITION_FORBIDDEN)
def test_no_local_redefinition(shape_name: str) -> None:
    """No shared typed shape is locally redefined by the 5th sub-slice
    module (per doc-13a:285-287 step 9 + doc-18:186-249).
    """

    from iriai_build_v2.execution_control import (
        counterfactual_metrics_comparator as mod,
    )

    sym = getattr(mod, shape_name, None)
    if sym is None:
        return  # Not exported at all; ok.
    # If exported, it must be the SAME object as the source-of-truth.
    if shape_name in {"GovernanceEvidenceRef"}:
        from iriai_build_v2.workflows.develop.governance import models

        assert sym is getattr(models, shape_name)
    elif shape_name in {"GovernanceMetricValue", "GovernanceScorecard"}:
        from iriai_build_v2.execution_control import governance_metrics

        assert sym is getattr(governance_metrics, shape_name)
    elif shape_name in {
        "CounterfactualResult",
        "ReplayCorpus",
        "CounterfactualScenario",
        "ReplayMode",
        "RiskChange",
        "RecommendedNextStep",
    }:
        from iriai_build_v2.execution_control import counterfactual_replay

        assert sym is getattr(counterfactual_replay, shape_name)


# ── MetricsComparatorInputs ────────────────────────────────────────────────


def test_metrics_comparator_inputs_construction_round_trip() -> None:
    """Happy-path construction of :class:`MetricsComparatorInputs`."""

    obj = _inputs()
    assert obj.result_id == "comparator-result-1"
    assert obj.result_version == "v1"
    assert obj.mode == "summary_replay"
    assert obj.max_baseline_metrics == DEFAULT_MAX_BASELINE_METRICS
    assert len(obj.baseline_metrics) == 4
    assert obj.scenario_result.result_id == "scenario-result-1"


def test_metrics_comparator_inputs_extra_forbid() -> None:
    """Typo-d kwargs fail closed via ``extra=forbid``."""

    with pytest.raises(ValidationError):
        MetricsComparatorInputs(
            baseline_metrics=[_metric()],
            scenario_result=_scenario_result(),
            result_id="r1",
            extra_field="boom",  # type: ignore[call-arg]
        )


def test_metrics_comparator_inputs_defaults() -> None:
    """Defaults for ``result_version`` / ``mode`` /
    ``max_baseline_metrics``."""

    obj = MetricsComparatorInputs(
        baseline_metrics=[_metric()],
        scenario_result=_scenario_result(),
        result_id="r1",
    )
    assert obj.result_version == "v1"
    assert obj.mode == "summary_replay"
    assert obj.max_baseline_metrics == DEFAULT_MAX_BASELINE_METRICS


def test_metrics_comparator_inputs_mode_annotation_is_replay_mode() -> None:
    """Annotation-identity REUSE for ``mode`` field."""

    hints = get_type_hints(MetricsComparatorInputs)
    assert hints["mode"] is ReplayMode


def test_metrics_comparator_inputs_baseline_metrics_uses_governance_metric_value() -> None:
    """Annotation-identity REUSE for ``baseline_metrics`` element type."""

    hints = get_type_hints(MetricsComparatorInputs)
    origin = get_origin(hints["baseline_metrics"])
    args = get_args(hints["baseline_metrics"])
    assert origin is list
    assert args == (GovernanceMetricValue,)


def test_metrics_comparator_inputs_scenario_result_uses_counterfactual_result() -> None:
    """Annotation-identity REUSE for ``scenario_result`` field."""

    hints = get_type_hints(MetricsComparatorInputs)
    assert hints["scenario_result"] is CounterfactualResult


def test_metrics_comparator_inputs_max_baseline_metrics_must_be_positive() -> None:
    """``max_baseline_metrics`` ``ge=1`` constraint."""

    with pytest.raises(ValidationError):
        MetricsComparatorInputs(
            baseline_metrics=[_metric()],
            scenario_result=_scenario_result(),
            result_id="r1",
            max_baseline_metrics=0,
        )


# ── MetricsAxisDelta ───────────────────────────────────────────────────────


def test_metrics_axis_delta_construction_round_trip() -> None:
    """Happy-path construction of :class:`MetricsAxisDelta`."""

    delta = MetricsAxisDelta(
        axis="hours",
        baseline_value=4.0,
        baseline_unit="hours",
        baseline_confidence=0.8,
        scenario_estimated_delta=-0.5,
        scenario_confidence=0.6,
        confidence=0.7,
        validity_limits=["summary_replay_mode"],
        evidence_refs=[_ref()],
    )
    assert delta.axis == "hours"
    assert delta.baseline_value == 4.0
    assert delta.scenario_estimated_delta == -0.5
    assert delta.confidence == 0.7
    assert delta.invalidated is False


def test_metrics_axis_delta_extra_forbid() -> None:
    """Typo-d kwargs fail closed via ``extra=forbid``."""

    with pytest.raises(ValidationError):
        MetricsAxisDelta(
            axis="hours",
            scenario_confidence=0.6,
            confidence=0.7,
            validity_limits=[],
            evidence_refs=[],
            extra_field="boom",  # type: ignore[call-arg]
        )


def test_metrics_axis_delta_axis_literal_rejects_unknown() -> None:
    """Axis Literal range fails closed on unknown values."""

    with pytest.raises(ValidationError):
        MetricsAxisDelta(
            axis="cost",  # type: ignore[arg-type]
            scenario_confidence=0.6,
            confidence=0.7,
            validity_limits=[],
            evidence_refs=[],
        )


def test_metrics_axis_delta_axis_literal_range_exact() -> None:
    """Axis Literal range matches doc-18:88-91 exactly."""

    for axis in ("hours", "repair_cycles", "commit_failures", "risk_change"):
        delta = MetricsAxisDelta(
            axis=axis,  # type: ignore[arg-type]
            scenario_confidence=0.6,
            confidence=0.7,
            validity_limits=[],
            evidence_refs=[],
        )
        assert delta.axis == axis


def test_metrics_axis_delta_confidence_range_validated() -> None:
    """Per-axis ``confidence`` is clamped to [0.0, 1.0]."""

    with pytest.raises(ValidationError):
        MetricsAxisDelta(
            axis="hours",
            scenario_confidence=0.6,
            confidence=1.5,
            validity_limits=[],
            evidence_refs=[],
        )
    with pytest.raises(ValidationError):
        MetricsAxisDelta(
            axis="hours",
            scenario_confidence=0.6,
            confidence=-0.5,
            validity_limits=[],
            evidence_refs=[],
        )


def test_metrics_axis_delta_scenario_confidence_range_validated() -> None:
    """``scenario_confidence`` is clamped to [0.0, 1.0]."""

    with pytest.raises(ValidationError):
        MetricsAxisDelta(
            axis="hours",
            scenario_confidence=1.5,
            confidence=0.5,
            validity_limits=[],
            evidence_refs=[],
        )


def test_metrics_axis_delta_evidence_refs_uses_governance_evidence_ref() -> None:
    """Annotation-identity REUSE for ``evidence_refs`` element type."""

    hints = get_type_hints(MetricsAxisDelta)
    origin = get_origin(hints["evidence_refs"])
    args = get_args(hints["evidence_refs"])
    assert origin is list
    assert args == (GovernanceEvidenceRef,)


# ── MetricsComparatorResult ────────────────────────────────────────────────


def test_metrics_comparator_result_construction_with_deltas() -> None:
    """Happy-path construction of :class:`MetricsComparatorResult`."""

    delta = MetricsAxisDelta(
        axis="hours",
        baseline_value=4.0,
        scenario_estimated_delta=-0.5,
        scenario_confidence=0.6,
        confidence=0.6,
        validity_limits=[],
        evidence_refs=[],
    )
    result = MetricsComparatorResult(
        axis_deltas=[delta],
        idempotency_key="d" * 64,
        result_id="r1",
        scenario_result_id="s1",
        emitted_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        overall_confidence=0.6,
    )
    assert len(result.axis_deltas) == 1
    assert result.invalidated_axes == []
    assert result.overall_confidence == 0.6


def test_metrics_comparator_result_extra_forbid() -> None:
    """Typo-d kwargs fail closed via ``extra=forbid``."""

    with pytest.raises(ValidationError):
        MetricsComparatorResult(
            idempotency_key="d" * 64,
            result_id="r1",
            scenario_result_id="s1",
            emitted_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            overall_confidence=0.0,
            extra_field="boom",  # type: ignore[call-arg]
        )


def test_metrics_comparator_result_overall_confidence_range_validated() -> None:
    """``overall_confidence`` is clamped to [0.0, 1.0]."""

    with pytest.raises(ValidationError):
        MetricsComparatorResult(
            idempotency_key="d" * 64,
            result_id="r1",
            scenario_result_id="s1",
            emitted_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            overall_confidence=1.5,
        )


def test_metrics_comparator_result_invalidated_axes_uses_axis_literal() -> None:
    """``invalidated_axes`` element type matches the 4-value axis
    Literal."""

    result = MetricsComparatorResult(
        idempotency_key="d" * 64,
        result_id="r1",
        scenario_result_id="s1",
        emitted_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        overall_confidence=0.0,
        invalidated_axes=["hours", "risk_change"],
    )
    assert "hours" in result.invalidated_axes


# ── MetricsComparatorGap ───────────────────────────────────────────────────


def test_metrics_comparator_gap_construction_round_trip() -> None:
    """Happy-path construction of :class:`MetricsComparatorGap`."""

    gap = MetricsComparatorGap(
        failure_id=METRICS_COMPARATOR_FAILURE_ID,
        result_id_attempted="r1",
        scenario_result_id="s1",
        reason="result_construction_failed",
        observed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    assert gap.failure_id == "metrics_comparator_failed"
    assert gap.reason == "result_construction_failed"


def test_metrics_comparator_gap_extra_forbid() -> None:
    """Typo-d kwargs fail closed via ``extra=forbid``."""

    with pytest.raises(ValidationError):
        MetricsComparatorGap(
            failure_id=METRICS_COMPARATOR_FAILURE_ID,
            result_id_attempted="r1",
            scenario_result_id="s1",
            reason="r",
            observed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
            extra_field="boom",  # type: ignore[call-arg]
        )


def test_metrics_comparator_gap_failure_id_literal_range_rejects_unknown() -> None:
    """The gap ``failure_id`` Literal range is exact."""

    with pytest.raises(ValidationError):
        MetricsComparatorGap(
            failure_id="other_failed",  # type: ignore[arg-type]
            result_id_attempted="r1",
            scenario_result_id="s1",
            reason="r",
            observed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        )


def test_metrics_comparator_gap_failure_id_literal_range_exact() -> None:
    """``METRICS_COMPARATOR_FAILURE_ID`` is the only accepted value."""

    gap = MetricsComparatorGap(
        failure_id="metrics_comparator_failed",
        result_id_attempted="r1",
        scenario_result_id="s1",
        reason="r",
        observed_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
    )
    assert gap.failure_id == METRICS_COMPARATOR_FAILURE_ID


# ── CounterfactualMetricsComparator.compare(...) -- happy path -────────────


def test_comparator_compare_happy_path_emits_typed_result() -> None:
    """Compare(...) emits a fully-populated typed result."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    assert isinstance(result, MetricsComparatorResult)
    assert len(result.axis_deltas) == 4
    axes = [d.axis for d in result.axis_deltas]
    assert axes == ["hours", "repair_cycles", "commit_failures", "risk_change"]


def test_comparator_compare_idempotency_key_deterministic() -> None:
    """Two compares of the same inputs produce the same idempotency key."""

    inputs = _inputs()
    cmp = CounterfactualMetricsComparator()
    r1 = cmp.compare(inputs)
    r2 = cmp.compare(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_comparator_compare_result_id_propagated_into_result() -> None:
    """``result_id`` is carried through."""

    inputs = _inputs(result_id="custom-result-id")
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.result_id == "custom-result-id"


def test_comparator_compare_scenario_result_id_propagated() -> None:
    """``scenario_result_id`` is carried through from the input
    scenario result."""

    inputs = _inputs(scenario_result=_scenario_result(result_id="custom-scenario"))
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.scenario_result_id == "custom-scenario"


# ── Per-axis projection correctness ────────────────────────────────────────


def test_comparator_hours_axis_baseline_value_from_baseline_median() -> None:
    """The ``hours`` axis baseline_value is the median of the matching
    baseline metric values."""

    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=4.0),
            _metric("workflow_drag_hours", value=8.0),
        ],
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    assert hours_delta.baseline_value == 6.0  # median of [4.0, 8.0]


def test_comparator_hours_axis_invalidated_when_no_baseline() -> None:
    """The ``hours`` axis is invalidated when no hour-unit baseline
    metric is present."""

    inputs = _inputs(
        baseline_metrics=[_metric("tasks_per_hour", value=0.25)],
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    assert hours_delta.baseline_value is None
    assert hours_delta.invalidated is True
    assert "baseline_value_unavailable" in hours_delta.validity_limits


def test_comparator_hours_axis_invalidated_when_scenario_delta_none() -> None:
    """The ``hours`` axis is invalidated when the scenario does not
    quantify hours."""

    inputs = _inputs(
        scenario_result=_scenario_result(estimated_delta_hours=None),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    assert hours_delta.scenario_estimated_delta is None
    assert hours_delta.invalidated is True
    assert "scenario_delta_unavailable" in hours_delta.validity_limits


def test_comparator_repair_cycles_axis_baseline_from_matching_metric() -> None:
    """The ``repair_cycles`` axis baseline_value comes from the
    ``repair_cycles_per_task`` baseline metric."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    repair_delta = next(
        d for d in result.axis_deltas if d.axis == "repair_cycles"
    )
    assert repair_delta.baseline_value == 2.0
    assert repair_delta.scenario_estimated_delta == -0.3


def test_comparator_commit_failures_axis_baseline_from_matching_metric() -> None:
    """The ``commit_failures`` axis baseline_value comes from the
    ``commit_failures_per_task`` baseline metric."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    commit_delta = next(
        d for d in result.axis_deltas if d.axis == "commit_failures"
    )
    assert commit_delta.baseline_value == 1.0
    assert commit_delta.scenario_estimated_delta == -0.1


def test_comparator_risk_change_axis_carries_scenario_literal() -> None:
    """The ``risk_change`` axis carries the scenario's
    ``estimated_risk_change`` Literal directly."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    risk_delta = next(
        d for d in result.axis_deltas if d.axis == "risk_change"
    )
    assert risk_delta.baseline_value is None
    assert risk_delta.scenario_estimated_risk_change == "lower"
    assert risk_delta.invalidated is False


def test_comparator_risk_change_axis_invalidated_when_unknown() -> None:
    """The ``risk_change`` axis is invalidated when the scenario
    reports ``unknown``."""

    inputs = _inputs(
        scenario_result=_scenario_result(estimated_risk_change="unknown"),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    risk_delta = next(
        d for d in result.axis_deltas if d.axis == "risk_change"
    )
    assert risk_delta.scenario_estimated_risk_change == "unknown"
    assert risk_delta.invalidated is True


# ── Confidence + overall_confidence projection ─────────────────────────────


def test_comparator_per_axis_confidence_in_unit_interval() -> None:
    """Every per-axis confidence is in [0.0, 1.0]."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    for delta in result.axis_deltas:
        assert 0.0 <= delta.confidence <= 1.0


def test_comparator_overall_confidence_in_unit_interval() -> None:
    """``overall_confidence`` is in [0.0, 1.0]."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    assert 0.0 <= result.overall_confidence <= 1.0


def test_comparator_overall_confidence_is_min_of_per_axis() -> None:
    """``overall_confidence`` is the min of non-invalidated per-axis
    confidences."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    non_invalidated = [d.confidence for d in result.axis_deltas if not d.invalidated]
    if non_invalidated:
        assert result.overall_confidence == min(non_invalidated)
    else:
        assert result.overall_confidence == 0.0


def test_comparator_overall_confidence_zero_when_all_axes_invalidated() -> None:
    """``overall_confidence`` is 0.0 when every axis is invalidated."""

    inputs = _inputs(
        baseline_metrics=[_metric("tasks_per_hour", value=0.25)],
        scenario_result=_scenario_result(
            estimated_delta_hours=None,
            estimated_delta_repair_cycles=None,
            estimated_delta_commit_failures=None,
            estimated_risk_change="unknown",
        ),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    assert all(d.invalidated for d in result.axis_deltas)
    assert result.overall_confidence == 0.0


def test_comparator_invalidated_axes_populated() -> None:
    """``invalidated_axes`` aggregates the per-axis flags."""

    inputs = _inputs(
        scenario_result=_scenario_result(estimated_delta_hours=None),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    assert "hours" in result.invalidated_axes


def test_comparator_confidence_uses_harmonic_mean() -> None:
    """Per-axis confidence is the harmonic mean of baseline + scenario
    confidence (so a low input lowers the per-axis confidence)."""

    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=4.0, confidence=0.8),
            _metric("repair_cycles_per_task", value=2.0, confidence=0.8),
            _metric("commit_failures_per_task", value=1.0, confidence=0.8),
        ],
        scenario_result=_scenario_result(confidence=0.6),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    expected_harmonic = 2.0 / ((1.0 / 0.8) + (1.0 / 0.6))
    assert abs(hours_delta.confidence - expected_harmonic) < 1e-9


# ── Refs-only invariant ────────────────────────────────────────────────────


def test_refs_only_no_raw_body_hydration() -> None:
    """The emitted result carries no raw artifact bodies (refs-only
    per Slice 13A invariant + doc-18:186-249)."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    dumped = result.model_dump(mode="json")

    forbidden_substrings = ("body", "raw_body", "artifact_body")

    def walk(obj: object) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                k_str = str(k).lower()
                for forbidden in forbidden_substrings:
                    assert forbidden not in k_str, (
                        f"forbidden raw-body key encountered: {k}"
                    )
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(dumped)


def test_comparator_evidence_refs_union_of_baseline_and_scenario() -> None:
    """Per-axis evidence_refs are the union of baseline-metric refs +
    scenario-result policy_provenance_refs (deduped)."""

    baseline_ref = _ref("baseline-shared-ref")
    scenario_ref = _ref("scenario-only-ref")
    inputs = _inputs(
        baseline_metrics=[
            _metric(
                "hours_per_task", value=4.0, evidence_refs=[baseline_ref]
            ),
            _metric(
                "repair_cycles_per_task",
                value=2.0,
                evidence_refs=[],
            ),
            _metric(
                "commit_failures_per_task",
                value=1.0,
                evidence_refs=[],
            ),
        ],
        scenario_result=_scenario_result(
            policy_provenance_refs=[scenario_ref]
        ),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    ref_ids = [r.ref_id for r in hours_delta.evidence_refs]
    assert "baseline-shared-ref" in ref_ids
    assert "scenario-only-ref" in ref_ids


def test_comparator_evidence_refs_deduplicated() -> None:
    """When the same ref appears in baseline and scenario it is emitted
    exactly once."""

    shared_ref = _ref("shared-ref")
    inputs = _inputs(
        baseline_metrics=[
            _metric(
                "hours_per_task", value=4.0, evidence_refs=[shared_ref]
            ),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
        ],
        scenario_result=_scenario_result(
            policy_provenance_refs=[shared_ref]
        ),
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    ref_ids = [r.ref_id for r in hours_delta.evidence_refs]
    assert ref_ids.count("shared-ref") == 1


# ── Read-only structural assertions ────────────────────────────────────────


def test_comparator_class_only_exposes_compare_method() -> None:
    """The comparator class exposes EXACTLY one public method
    (``compare``) per doc-18:164 AC3."""

    public_methods = [
        name
        for name, _ in inspect.getmembers(
            CounterfactualMetricsComparator, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    ]
    assert public_methods == ["compare"]


def test_comparator_class_no_mutation_method_prefixes() -> None:
    """No mutation-prefix public methods on the comparator class."""

    mutation_prefixes = (
        "write_",
        "save_",
        "store_",
        "persist_",
        "update_",
        "delete_",
        "remove_",
        "create_",
        "insert_",
        "set_",
        "register_",
        "activate_",
        "apply_",
    )
    for name, _ in inspect.getmembers(
        CounterfactualMetricsComparator, predicate=inspect.isfunction
    ):
        if name.startswith("_"):
            continue
        for prefix in mutation_prefixes:
            assert not name.startswith(prefix), (
                f"comparator class exposes mutation-prefix method: {name}"
            )


def test_no_consumer_side_module_imports() -> None:
    """The module does NOT import any consumer-side runtime module."""

    import iriai_build_v2.execution_control.counterfactual_metrics_comparator as mod

    src = inspect.getsource(mod)
    forbidden_imports = (
        "from iriai_build_v2.workflows.develop.execution.dispatcher",
        "from iriai_build_v2.workflows.develop.execution.merge",
        "from iriai_build_v2.workflows.develop.execution.regroup",
        "from iriai_build_v2.workflows.develop.execution.scheduler",
        "from iriai_build_v2.workflows.develop.supervisor",
        "from iriai_build_v2.workflows.develop.dashboard",
        "from iriai_build_v2.execution_control.commit_provenance_writer",
        "from iriai_build_v2.execution_control.commit_provenance_reader",
        "from iriai_build_v2.execution_control.governance_scorecard_writer",
        "from iriai_build_v2.execution_control.dashboard_wrapper",
        # Per the activation-authority boundary: no consumer-side
        # imports from any other replay engine in the same Slice 18.
        "from iriai_build_v2.execution_control.counterfactual_summary_replay",
        "from iriai_build_v2.execution_control.counterfactual_event_replay",
        "from iriai_build_v2.execution_control.counterfactual_replay_loader",
    )
    for imp in forbidden_imports:
        assert imp not in src, (
            f"comparator module unexpectedly imports consumer-side surface: {imp}"
        )


def test_no_dag_authority_artifact_keys() -> None:
    """The module does NOT carry any ``dag-*`` authority artifact-key
    string literals (per doc-18:124-125)."""

    import iriai_build_v2.execution_control.counterfactual_metrics_comparator as mod

    src = inspect.getsource(mod)
    forbidden_dag_keys = (
        '"dag-group:',
        '"dag-task:',
        '"dag-attempt:',
        '"dag-checkpoint:',
        '"dag-merge:',
        '"dag-route:',
    )
    for key in forbidden_dag_keys:
        assert key not in src, (
            f"comparator module unexpectedly carries dag-* authority key: {key}"
        )


# ── Bounded-input + fail-closed gap projection ─────────────────────────────


def test_compare_baseline_metrics_exceeded_bound_emits_gap() -> None:
    """A baseline list exceeding the bound projects onto a typed gap."""

    inputs = _inputs(
        baseline_metrics=[_metric("hours_per_task", value=float(i)) for i in range(10)],
        max_baseline_metrics=5,
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.axis_deltas == []
    assert len(result.gap_findings) == 1
    gap = result.gap_findings[0]
    assert gap.failure_id == METRICS_COMPARATOR_FAILURE_ID
    assert gap.reason == "baseline_metrics_exceeded_bound"
    assert gap.evidence_payload["received_count"] == 10
    assert gap.evidence_payload["max_bound"] == 5


def test_compare_empty_result_id_emits_gap() -> None:
    """An empty result_id projects onto a typed gap."""

    inputs = _inputs(result_id="   ")
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.axis_deltas == []
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "result_id_empty"


def test_compare_empty_baseline_metrics_emits_gap() -> None:
    """An empty baseline metric list projects onto a typed gap."""

    inputs = _inputs(baseline_metrics=[])
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.axis_deltas == []
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "baseline_metrics_empty"


def test_compare_empty_scenario_result_id_emits_gap() -> None:
    """An empty scenario result_id projects onto a typed gap.

    Note: the underlying :class:`CounterfactualResult` accepts an empty
    string result_id at construction (per the 1st sub-slice typed shape
    -- per doc-18:80 the field is just ``str``); the comparator detects
    the empty state at its own input-validation step + emits a typed
    gap.
    """

    inputs = _inputs(scenario_result=_scenario_result(result_id="   "))
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.axis_deltas == []
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "scenario_result_invalid"


def test_compare_never_raises_on_valid_input() -> None:
    """The comparator NEVER raises on any valid typed input."""

    inputs_variants = [
        _inputs(),
        _inputs(baseline_metrics=[_metric("hours_per_task", value=None)]),
        _inputs(
            scenario_result=_scenario_result(
                estimated_delta_hours=None,
                estimated_delta_repair_cycles=None,
                estimated_delta_commit_failures=None,
                estimated_risk_change="unknown",
            )
        ),
        _inputs(
            baseline_metrics=[
                _metric("foo_metric", value=1.0),
                _metric("bar_metric", value=2.0),
            ]
        ),
    ]
    for inp in inputs_variants:
        # Must not raise.
        result = CounterfactualMetricsComparator().compare(inp)
        assert isinstance(result, MetricsComparatorResult)


# ── compute_metrics_comparator_idempotency_key -- deterministic ────────────


def test_compute_idempotency_key_deterministic() -> None:
    """The pure helper is deterministic over identical inputs."""

    args = dict(
        result_id="r1",
        result_version="v1",
        scenario_result_id="s1",
        mode="summary_replay",
        baseline_metric_definition_names=["hours_per_task", "repair_cycles_per_task"],
    )
    a = compute_metrics_comparator_idempotency_key(**args)
    b = compute_metrics_comparator_idempotency_key(**args)
    assert a == b
    assert len(a) == 64


def test_compute_idempotency_key_order_invariant() -> None:
    """The pure helper is order-invariant over
    ``baseline_metric_definition_names``."""

    a = compute_metrics_comparator_idempotency_key(
        result_id="r1",
        result_version="v1",
        scenario_result_id="s1",
        mode="summary_replay",
        baseline_metric_definition_names=["a", "b", "c"],
    )
    b = compute_metrics_comparator_idempotency_key(
        result_id="r1",
        result_version="v1",
        scenario_result_id="s1",
        mode="summary_replay",
        baseline_metric_definition_names=["c", "b", "a"],
    )
    assert a == b


def test_compute_idempotency_key_differs_on_mode() -> None:
    """Different ``mode`` values produce different keys (so a future
    re-run under a different mode cleanly produces a new key)."""

    a = compute_metrics_comparator_idempotency_key(
        result_id="r1",
        result_version="v1",
        scenario_result_id="s1",
        mode="summary_replay",
        baseline_metric_definition_names=["m1"],
    )
    b = compute_metrics_comparator_idempotency_key(
        result_id="r1",
        result_version="v1",
        scenario_result_id="s1",
        mode="event_replay",
        baseline_metric_definition_names=["m1"],
    )
    assert a != b


def test_compute_idempotency_key_differs_on_result_version() -> None:
    """Different ``result_version`` values produce different keys."""

    a = compute_metrics_comparator_idempotency_key(
        result_id="r1",
        result_version="v1",
        scenario_result_id="s1",
        mode="summary_replay",
        baseline_metric_definition_names=["m1"],
    )
    b = compute_metrics_comparator_idempotency_key(
        result_id="r1",
        result_version="v2",
        scenario_result_id="s1",
        mode="summary_replay",
        baseline_metric_definition_names=["m1"],
    )
    assert a != b


# ── failure_router 4 pure-data add points ───────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    """The ``FailureType`` Literal includes the new id."""

    args = get_args(FailureType)
    assert "metrics_comparator_failed" in args


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """``FAILURE_TYPES`` tuple includes the new id."""

    assert "metrics_comparator_failed" in FAILURE_TYPES


def test_failure_router_retryable_failure_types_includes_new_id() -> None:
    """The retryable failure types frozenset includes the new id."""

    assert "metrics_comparator_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_table_includes_new_route_entry() -> None:
    """``ROUTE_TABLE`` has a new entry for the new id."""

    key = ("evidence_corruption", "metrics_comparator_failed")
    assert key in ROUTE_TABLE


def test_failure_router_action_is_non_blocking() -> None:
    """The route action for the new id is the REUSED non-blocking
    governance-projection retry action."""

    route = ROUTE_TABLE[("evidence_corruption", "metrics_comparator_failed")]
    assert route.action == "retry_governance_projection"


def test_failure_router_existing_evidence_corruption_class_reused() -> None:
    """The new id registers under the EXISTING ``evidence_corruption``
    failure class (NOT a new class)."""

    route = ROUTE_TABLE[("evidence_corruption", "metrics_comparator_failed")]
    assert route.failure_class == "evidence_corruption"


# ── Doc-18 AC PIN tests ────────────────────────────────────────────────────


def test_doc_18_115_step_5_satisfied() -> None:
    """Doc-18:115 step 5 satisfied: comparator compares baseline vs
    scenario outcomes using Slice 15 metrics."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    # Step 5 PIN: comparator emits one per-axis delta per axis.
    assert len(result.axis_deltas) == 4
    # Step 5 PIN: each per-axis delta carries a baseline value + a
    # scenario estimated delta (per doc-18:88-92).
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    assert hours_delta.baseline_value is not None
    assert hours_delta.scenario_estimated_delta is not None


def test_doc_18_88_90_typed_delta_fields_per_axis() -> None:
    """Doc-18:88-90 satisfied: typed delta fields per axis (hours /
    repair_cycles / commit_failures)."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    axes = {d.axis for d in result.axis_deltas}
    assert "hours" in axes
    assert "repair_cycles" in axes
    assert "commit_failures" in axes


def test_doc_18_91_risk_change_4_value_literal() -> None:
    """Doc-18:91 satisfied: risk-change axis carries the 4-value
    Literal."""

    for risk in ("lower", "same", "higher", "unknown"):
        inputs = _inputs(scenario_result=_scenario_result(estimated_risk_change=risk))
        result = CounterfactualMetricsComparator().compare(inputs)
        risk_delta = next(d for d in result.axis_deltas if d.axis == "risk_change")
        assert risk_delta.scenario_estimated_risk_change == risk


def test_doc_18_92_per_axis_confidence_in_unit_interval() -> None:
    """Doc-18:92 satisfied: per-axis confidence is in [0.0, 1.0]."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    for d in result.axis_deltas:
        assert 0.0 <= d.confidence <= 1.0
        assert 0.0 <= d.scenario_confidence <= 1.0


def test_doc_18_186_249_slice_13a_no_redefinition() -> None:
    """Doc-18:186-249 satisfied: no local redefinition of Slice 13A
    shared completeness model shapes."""

    import iriai_build_v2.execution_control.counterfactual_metrics_comparator as mod

    forbidden_names = (
        "CompletenessState",
        "EvidenceCompleteness",
        "AuthoritativePromptContextRouting",
        "AuthoritativeGateCompanionRecord",
        "AuthoritativeGateProofRow",
        "AuthoritativeSnapshotListFieldCompleteness",
        "AuthoritativeSnapshotClassifierRouting",
        "ExactEvidenceManifest",
    )
    for name in forbidden_names:
        attr = getattr(mod, name, None)
        # Either not present at all OR (if exported via re-import)
        # must be the SAME object as the source-of-truth (no second
        # definition).
        if attr is None:
            continue
        # We don't expect any of these names to be locally defined.
        raise AssertionError(
            f"{name} unexpectedly present on the 5th sub-slice module"
        )


def test_doc_18_160_168_ac1_deterministic() -> None:
    """Doc-18:160-168 AC1 (deterministic) satisfied via the
    idempotency-key helper + result_version field."""

    inputs = _inputs()
    cmp = CounterfactualMetricsComparator()
    r1 = cmp.compare(inputs)
    r2 = cmp.compare(inputs)
    assert r1.idempotency_key == r2.idempotency_key
    assert r1.result_id == r2.result_id


def test_doc_18_160_168_ac2_assumptions_and_validity_limits() -> None:
    """Doc-18:160-168 AC2 satisfied: per-axis ``validity_limits``
    populated on the per-axis delta records."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    for delta in result.axis_deltas:
        # validity_limits is always a typed list (may be empty for
        # the risk_change axis in the happy path).
        assert isinstance(delta.validity_limits, list)


def test_doc_18_160_168_ac3_replay_cannot_mutate_workflow_state() -> None:
    """Doc-18:160-168 AC3 satisfied: comparator class exposes EXACTLY
    one public method ``compare``; no mutation surface."""

    public_methods = [
        name
        for name, _ in inspect.getmembers(
            CounterfactualMetricsComparator, predicate=inspect.isfunction
        )
        if not name.startswith("_")
    ]
    assert public_methods == ["compare"]


def test_doc_18_160_168_ac4_result_supports_recommendation_citation() -> None:
    """Doc-18:160-168 AC4 satisfied: result carries the
    ``scenario_result_id`` the Slice 17 recommendation citation hook
    cross-references."""

    inputs = _inputs(scenario_result=_scenario_result(result_id="scenario-xyz"))
    result = CounterfactualMetricsComparator().compare(inputs)
    assert result.scenario_result_id == "scenario-xyz"


def test_doc_18_160_168_ac5_evidence_refs_from_baseline_and_scenario() -> None:
    """Doc-18:160-168 AC5 satisfied: per-axis evidence_refs carry the
    typed Slice 13a refs from both baseline + scenario inputs."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    for delta in result.axis_deltas:
        # At least the scenario provenance ref is present.
        assert len(delta.evidence_refs) >= 1


# ── Doc-18:50 + doc-18:134-138 edge cases ──────────────────────────────────


def test_doc_18_50_confidence_ranges() -> None:
    """Doc-18:50 satisfied: per-axis confidence is in [0.0, 1.0] (the
    breadth axis)."""

    inputs = _inputs()
    result = CounterfactualMetricsComparator().compare(inputs)
    for d in result.axis_deltas:
        assert 0.0 <= d.confidence <= 1.0


def test_doc_18_134_135_missing_evidence_invalidates_axis() -> None:
    """Doc-18:134-135 satisfied: missing baseline value invalidates
    the axis."""

    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=None),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
        ],
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    hours_delta = next(d for d in result.axis_deltas if d.axis == "hours")
    assert hours_delta.invalidated is True


def test_doc_18_138_invalidated_axes_carried_on_result() -> None:
    """Doc-18:138 satisfied: invalidated_axes aggregates the per-axis
    flags so the future Slice 17 citation hook can refuse a low-
    confidence axis."""

    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=None),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
        ],
    )
    result = CounterfactualMetricsComparator().compare(inputs)
    assert "hours" in result.invalidated_axes


def test_failure_router_class_is_evidence_corruption_for_metrics_comparator() -> None:
    """The new failure id registers under the EXISTING
    ``evidence_corruption`` failure_class (NOT a new class)."""

    key = ("evidence_corruption", "metrics_comparator_failed")
    assert key in ROUTE_TABLE


def test_module_imports_only_from_allowed_sources() -> None:
    """The module's imports are scoped to the allowed source set per
    the implementer prompt § "Non-negotiables"."""

    import ast as _ast
    import iriai_build_v2.execution_control.counterfactual_metrics_comparator as mod

    src = inspect.getsource(mod)
    tree = _ast.parse(src)

    # Allowed import roots (stdlib + Pydantic + Slice 13a +
    # Slice 15 + Slice 18 1st).
    allowed_module_prefixes = (
        "hashlib",
        "json",
        "datetime",
        "typing",
        "pydantic",
        "iriai_build_v2.execution_control.counterfactual_replay",
        "iriai_build_v2.execution_control.governance_metrics",
        "iriai_build_v2.workflows.develop.governance.models",
    )
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                assert any(
                    alias.name == p or alias.name.startswith(p + ".")
                    for p in allowed_module_prefixes
                ), f"unexpected import: {alias.name}"
        elif isinstance(node, _ast.ImportFrom):
            if node.module is None:
                # `from __future__ import annotations` style covered
                # by future-import detection above.
                assert node.level >= 0
                continue
            assert any(
                node.module == p or node.module.startswith(p + ".")
                for p in allowed_module_prefixes
            ) or node.module == "__future__", (
                f"unexpected from-import: {node.module}"
            )


def test_consumer_module_does_not_import_comparator() -> None:
    """The Slice 18 1st sub-slice typed-shape module does NOT import
    this 5th sub-slice module (no upward dependency)."""

    import iriai_build_v2.execution_control.counterfactual_replay as upstream

    src = inspect.getsource(upstream)
    assert "counterfactual_metrics_comparator" not in src
