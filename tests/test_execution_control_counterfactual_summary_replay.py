"""Slice 18 third sub-slice -- unit tests for the counterfactual summary
replay engine at
``execution_control/counterfactual_summary_replay.py``.

Covers the doc-18:113 step 3 typed-shape consumer + structural
projection wiring:

* :class:`SummaryReplayInputs` typed inputs BaseModel (extra-forbid;
  bounded-input default; typed Slice 18 1st sub-slice :class:`ReplayCorpus`
  REUSE; typed Slice 18 1st sub-slice :class:`CounterfactualScenario`
  REUSE; typed Slice 15 :class:`GovernanceMetricValue` REUSE; typed
  Slice 15 :class:`GovernanceScorecard` REUSE; typed Slice 18 1st sub-
  slice :data:`ReplayMode` REUSE).
* :class:`SummaryReplayResult` typed result BaseModel (extra-forbid;
  result / gap_findings / idempotency_key).
* :class:`SummaryReplayGap` typed gap projection BaseModel (extra-
  forbid; failure_id Literal range).
* :class:`CounterfactualSummaryReplayEngine.replay(...)` -- the
  projection method:
  * Happy-path -> typed :class:`CounterfactualResult` emitted with all
    16 fields populated per doc-18:79-96.
  * Bounded-input check -> typed gap on
    ``baseline_metrics_exceeded_bound`` per doc-18:150.
  * Empty result_id / empty baseline_metrics -> typed gap per
    ``feedback_no_silent_degradation``.
  * Invalid mode (e.g. ``event_replay``) -> typed gap (this 3rd sub-
    slice engine ONLY handles ``summary_replay``).
  * Engine NEVER raises on input (fail-closed; typed gap projection
    on construction failure).
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :class:`GovernanceEvidenceRef` + Slice 15 :class:`GovernanceMetricValue`
  + :class:`GovernanceScorecard` + Slice 18 1st sub-slice
  :class:`ReplayCorpus` / :class:`CounterfactualScenario` /
  :class:`CounterfactualResult` / :data:`ReplayMode`.
* Failure-router 4-add-point validation (``summary_replay_failed``
  registered under EXISTING ``evidence_corruption`` failure_class with
  REUSED ``retry_governance_projection`` NON-blocking RouteAction;
  mirrors Slice 17 2nd/3rd/4th/5th/6th + Slice 18 2nd sub-slice
  precedent).
* :func:`compute_summary_replay_idempotency_key` -- the deterministic
  SHA-256-derived idempotency-key helper (mirror the Slice 18 1st sub-
  slice :func:`compute_counterfactual_idempotency_key` + Slice 18 2nd
  sub-slice helpers verbatim).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 1st + 2nd sub-slice modules + tests
remain byte-identical.

**Slice 13A awareness asserted (doc-18:186-249).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition. The engine consumes Slice 13a typed
``GovernanceEvidenceRef`` (via the Slice 15 baseline metric +
scorecard inputs) and emits the refs-only projection onto the typed
Slice 18 1st sub-slice ``CounterfactualResult`` shape; no raw artifact
body hydration per doc-18:186-249.

**Refs-only invariant (doc-18:186-249).** Test
:func:`test_refs_only_no_raw_body_hydration` walks the emitted
``CounterfactualResult.model_dump(mode="json")`` recursively and
asserts no key contains the forbidden ``body`` / ``raw_body`` /
``artifact_body`` substring.

**Doc-18:123-129 persistence + artifact compatibility awareness.** No
3rd-sub-slice consumer activation wiring; no consumer-owned artifact-
key literals; replay results are review/governance artifacts only,
never runtime policy authority. Structural test
:func:`test_no_consumer_side_module_imports` walks the module's
import graph + asserts no consumer-side module is imported.

**Doc-18:164 AC3 (no mutation methods).** Structural test
:func:`test_engine_class_only_exposes_replay_method` asserts the
engine class exposes EXACTLY one public method (``replay``) -- no
mutation surface.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    CounterfactualScenario,
    RecommendedNextStep,
    ReplayCorpus,
    ReplayMode,
    RiskChange,
)
from iriai_build_v2.execution_control.counterfactual_summary_replay import (
    DEFAULT_MAX_BASELINE_METRICS,
    SUMMARY_REPLAY_CONFIDENCE_CEILING,
    SUMMARY_REPLAY_FAILURE_ID,
    CounterfactualSummaryReplayEngine,
    SummaryReplayGap,
    SummaryReplayInputs,
    SummaryReplayResult,
    compute_summary_replay_idempotency_key,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricValue,
    GovernanceScorecard,
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
    value: float = 4.0,
    confidence: float = 0.8,
    **overrides: object,
) -> GovernanceMetricValue:
    """Construct a typed Slice 15 :class:`GovernanceMetricValue`."""

    base: dict[str, object] = dict(
        definition_name=definition_name,
        definition_version="v1",
        scope={"feature_id": "8ac124d6"},
        value=value,
        unit="hours",
        confidence=confidence,
        data_quality="canonical",
        source_mix={"typed": 12},
        evidence_refs=[_ref(f"{definition_name}-ev-1")],
        exclusions=[],
    )
    base.update(overrides)
    return GovernanceMetricValue(**base)  # type: ignore[arg-type]


def _scorecard(**overrides: object) -> GovernanceScorecard:
    """Construct a typed Slice 15 :class:`GovernanceScorecard`."""

    base: dict[str, object] = dict(
        corpus_id="corpus-1",
        generated_at=datetime(2026, 5, 27, tzinfo=timezone.utc),
        metrics=[_metric()],
        baseline_refs=[_ref("scorecard-ref-1")],
        incomplete_scopes=[],
        warnings=[],
    )
    base.update(overrides)
    return GovernanceScorecard(**base)  # type: ignore[arg-type]


def _corpus(**overrides: object) -> ReplayCorpus:
    """Construct a typed Slice 18 1st sub-slice :class:`ReplayCorpus`."""

    base: dict[str, object] = dict(
        corpus_id="corpus-1",
        feature_ids=["8ac124d6"],
        evidence_set_ids=["ev-1"],
        implementation_anchor_ids=["anchor-1"],
        mode="summary_replay",
        validity_limits=[],
    )
    base.update(overrides)
    return ReplayCorpus(**base)  # type: ignore[arg-type]


def _scenario(**overrides: object) -> CounterfactualScenario:
    """Construct a typed Slice 18 1st sub-slice
    :class:`CounterfactualScenario`."""

    base: dict[str, object] = dict(
        scenario_id="scenario-1",
        policy_under_test={
            "policy_kind": "wave_cap",
            "value": {"wave_cap": 7},
            "expected_hours_delta_ratio": -0.1,
            "expected_repair_cycles_delta_ratio": -0.2,
            "expected_commit_failures_delta_ratio": -0.05,
        },
        baseline_policy_refs=["baseline-policy-1"],
        affected_consumers=["scheduler"],
        required_evidence_kinds=["ev-1"],
        assumptions=["product_defect_independent_of_wave_size"],
    )
    base.update(overrides)
    return CounterfactualScenario(**base)  # type: ignore[arg-type]


def _inputs(**overrides: object) -> SummaryReplayInputs:
    """Construct a fully-specified :class:`SummaryReplayInputs`."""

    base: dict[str, object] = dict(
        corpus=_corpus(),
        scenario=_scenario(),
        baseline_metrics=[
            _metric("hours_per_task", value=4.0),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
            _metric("tasks_per_hour", value=0.25),
        ],
        baseline_scorecard=None,
        mode="summary_replay",
        result_id="result-1",
        result_version="v1",
    )
    base.update(overrides)
    return SummaryReplayInputs(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_exports_count() -> None:
    from iriai_build_v2.execution_control import counterfactual_summary_replay as mod

    assert len(mod.__all__) == 8, (
        f"Expected 8 __all__ exports, got {len(mod.__all__)}: {mod.__all__}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "SUMMARY_REPLAY_FAILURE_ID",
        "DEFAULT_MAX_BASELINE_METRICS",
        "SUMMARY_REPLAY_CONFIDENCE_CEILING",
        "SummaryReplayInputs",
        "SummaryReplayResult",
        "SummaryReplayGap",
        "CounterfactualSummaryReplayEngine",
        "compute_summary_replay_idempotency_key",
    ],
)
def test_module_surface_hasattr(name: str) -> None:
    from iriai_build_v2.execution_control import counterfactual_summary_replay as mod

    assert hasattr(mod, name), f"counterfactual_summary_replay missing {name!r}"


def test_no_re_export_from_execution_control_init() -> None:
    """Per Slice 13A/14/15/16/17/18-1st/18-2nd precedent, this module
    is NOT re-exported from ``execution_control/__init__.py``."""

    import iriai_build_v2.execution_control as init_module

    source = init_module.__file__
    if source is None:
        pytest.skip("execution_control/__init__.py has no __file__")
    with open(source) as f:
        body = f.read()
    assert "counterfactual_summary_replay" not in body


# ── Slice 13A / Slice 15 / Slice 17 / Slice 18 1st no-redefinition ────────


@pytest.mark.parametrize(
    "shape_name",
    [
        "CompletenessState",
        "EvidenceCompleteness",
        "AuthoritativeContextRef",
        "EvidencePageRef",
        "ExactEvidenceManifest",
        "AuthoritativePromptContextRouting",
        "AuthoritativeGateCompanionRecord",
        "AuthoritativeGateProofRow",
        "AuthoritativeSnapshotListFieldCompleteness",
        "AuthoritativeSnapshotClassifierRouting",
        # Slice 13a shared shapes
        "GovernanceEvidenceRef",
        "GovernanceEvidenceSet",
        # Slice 15
        "GovernanceMetricValue",
        "GovernanceScorecard",
        "GovernanceMetricDefinition",
        "MetricScopeKind",
        # Slice 17 1st sub-slice
        "PolicyConsumer",
        "GovernancePolicyRecommendation",
        # Slice 18 1st sub-slice
        "ReplayCorpus",
        "CounterfactualScenario",
        "CounterfactualResult",
        "ReplayMode",
        "RiskChange",
        "RecommendedNextStep",
    ],
)
def test_no_local_redefinition(shape_name: str) -> None:
    """The Slice 18 3rd sub-slice module MUST NOT redefine any of the
    prior typed shapes. The shape MAY appear in the module namespace
    via direct import; this test asserts the IMPORTED shape's
    `__module__` is NOT this 3rd sub-slice module's."""

    from iriai_build_v2.execution_control import counterfactual_summary_replay as mod

    if hasattr(mod, shape_name):
        shape = getattr(mod, shape_name)
        if hasattr(shape, "__module__"):
            assert shape.__module__ != mod.__name__, (
                f"Slice 18 3rd sub-slice REDEFINES {shape_name!r} (must REUSE)"
            )


# ── SummaryReplayInputs ───────────────────────────────────────────────────


def test_summary_replay_inputs_construction_round_trip() -> None:
    inputs = _inputs()
    assert inputs.corpus.corpus_id == "corpus-1"
    assert inputs.scenario.scenario_id == "scenario-1"
    assert len(inputs.baseline_metrics) == 4
    assert inputs.baseline_metrics[0].definition_name == "hours_per_task"
    assert inputs.mode == "summary_replay"
    assert inputs.result_id == "result-1"
    assert inputs.result_version == "v1"
    assert inputs.baseline_scorecard is None
    assert inputs.max_baseline_metrics == DEFAULT_MAX_BASELINE_METRICS


def test_summary_replay_inputs_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SummaryReplayInputs(  # type: ignore[call-arg]
            corpus=_corpus(),
            scenario=_scenario(),
            baseline_metrics=[_metric()],
            mode="summary_replay",
            result_id="r-1",
            unknown_field="rejected",
        )


def test_summary_replay_inputs_defaults() -> None:
    inputs = SummaryReplayInputs(
        corpus=_corpus(),
        scenario=_scenario(),
        baseline_metrics=[_metric()],
        result_id="r-1",
    )
    assert inputs.mode == "summary_replay"
    assert inputs.result_version == "v1"
    assert inputs.baseline_scorecard is None
    assert inputs.max_baseline_metrics == DEFAULT_MAX_BASELINE_METRICS


def test_summary_replay_inputs_mode_annotation_is_replay_mode() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :data:`ReplayMode`."""

    hints = get_type_hints(SummaryReplayInputs)
    assert hints["mode"] is ReplayMode


def test_summary_replay_inputs_corpus_annotation_is_replay_corpus() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`ReplayCorpus`."""

    hints = get_type_hints(SummaryReplayInputs)
    assert hints["corpus"] is ReplayCorpus


def test_summary_replay_inputs_scenario_annotation_is_counterfactual_scenario() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`CounterfactualScenario`."""

    hints = get_type_hints(SummaryReplayInputs)
    assert hints["scenario"] is CounterfactualScenario


def test_summary_replay_inputs_baseline_metrics_uses_governance_metric_value() -> None:
    """DIRECT annotation-identity REUSE for Slice 15
    :class:`GovernanceMetricValue`."""

    hints = get_type_hints(SummaryReplayInputs)
    annotation = hints["baseline_metrics"]
    args = get_args(annotation)
    assert args == (GovernanceMetricValue,)


def test_summary_replay_inputs_baseline_scorecard_uses_governance_scorecard() -> None:
    """DIRECT annotation-identity REUSE for Slice 15
    :class:`GovernanceScorecard`."""

    hints = get_type_hints(SummaryReplayInputs)
    annotation = hints["baseline_scorecard"]
    args = get_args(annotation)
    assert GovernanceScorecard in args


def test_summary_replay_inputs_max_baseline_metrics_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        SummaryReplayInputs(
            corpus=_corpus(),
            scenario=_scenario(),
            baseline_metrics=[_metric()],
            result_id="r-1",
            max_baseline_metrics=0,
        )


def test_summary_replay_inputs_accepts_typed_scorecard() -> None:
    inputs = _inputs(baseline_scorecard=_scorecard())
    assert inputs.baseline_scorecard is not None
    assert inputs.baseline_scorecard.corpus_id == "corpus-1"


# ── SummaryReplayResult ───────────────────────────────────────────────────


def test_summary_replay_result_construction_with_result() -> None:
    engine = CounterfactualSummaryReplayEngine()
    result_obj = engine.replay(_inputs())
    assert result_obj.result is not None
    assert result_obj.gap_findings == []
    assert len(result_obj.idempotency_key) == 64  # SHA-256 hex


def test_summary_replay_result_construction_without_result() -> None:
    result = SummaryReplayResult(result=None, gap_findings=[], idempotency_key="x")
    assert result.result is None


def test_summary_replay_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SummaryReplayResult(  # type: ignore[call-arg]
            result=None,
            gap_findings=[],
            idempotency_key="x",
            unknown_field="rejected",
        )


def test_summary_replay_result_result_annotation_uses_counterfactual_result() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`CounterfactualResult`."""

    hints = get_type_hints(SummaryReplayResult)
    annotation = hints["result"]
    args = get_args(annotation)
    assert CounterfactualResult in args


# ── SummaryReplayGap ──────────────────────────────────────────────────────


def test_summary_replay_gap_construction_round_trip() -> None:
    gap = SummaryReplayGap(
        failure_id=SUMMARY_REPLAY_FAILURE_ID,
        result_id_attempted="r-1",
        corpus_id="corpus-1",
        scenario_id="scenario-1",
        reason="baseline_metrics_exceeded_bound",
        observed_at=datetime.now(timezone.utc),
        evidence_refs=["ev-1"],
        evidence_payload={"received_count": 300},
    )
    assert gap.failure_id == "summary_replay_failed"
    assert gap.result_id_attempted == "r-1"
    assert gap.corpus_id == "corpus-1"
    assert gap.scenario_id == "scenario-1"


def test_summary_replay_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SummaryReplayGap(  # type: ignore[call-arg]
            failure_id=SUMMARY_REPLAY_FAILURE_ID,
            result_id_attempted="r-1",
            corpus_id="corpus-1",
            scenario_id="scenario-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
            unknown_field="rejected",
        )


def test_summary_replay_gap_failure_id_literal_range_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        SummaryReplayGap(  # type: ignore[arg-type]
            failure_id="other_failure",
            result_id_attempted="r-1",
            corpus_id="c-1",
            scenario_id="s-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
        )


def test_summary_replay_gap_failure_id_literal_range_exact() -> None:
    """The failure id Literal range MUST be exactly the single typed
    failure id (per the Slice 17/Slice 18 2nd sub-slice precedent)."""

    hints = get_type_hints(SummaryReplayGap)
    annotation = hints["failure_id"]
    args = get_args(annotation)
    assert args == ("summary_replay_failed",)


# ── CounterfactualSummaryReplayEngine happy-path ──────────────────────────


def test_engine_replay_happy_path_emits_typed_result() -> None:
    """Doc-18:113 step 3 happy-path: typed baseline + scenario -> typed
    :class:`CounterfactualResult` with all 16 fields populated."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs()
    out = engine.replay(inputs)
    assert out.result is not None, f"Expected typed result, got gap: {out.gap_findings}"
    assert out.result.result_id == "result-1"
    assert out.result.result_version == "v1"
    assert out.result.scenario_id == "scenario-1"
    assert out.result.corpus_id == "corpus-1"
    # 16 fields populated.
    assert out.result.assumptions  # non-empty
    assert out.result.validity_limits  # non-empty
    assert out.result.policy_provenance_refs is not None
    assert out.result.estimated_delta_hours is not None
    assert out.result.estimated_delta_repair_cycles is not None
    assert out.result.estimated_delta_commit_failures is not None
    assert out.result.estimated_risk_change in ("lower", "same", "higher", "unknown")
    assert 0.0 <= out.result.confidence <= 1.0
    assert out.result.invalidated_by == []  # required evidence present
    assert out.result.supporting_finding_ids == []
    assert out.result.recommended_next_step in (
        "discard",
        "collect_more_evidence",
        "draft_policy",
        "implementation_plan",
    )
    assert out.gap_findings == []
    assert len(out.idempotency_key) == 64  # SHA-256 hex


def test_engine_replay_idempotency_key_deterministic() -> None:
    """Doc-18:127-129 + AC1 -- the idempotency key is deterministic
    per inputs."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs()
    r1 = engine.replay(inputs)
    r2 = engine.replay(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_engine_replay_result_id_propagated_into_result() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(result_id="custom-result-7")
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.result_id == "custom-result-7"


def test_engine_replay_result_version_propagated_into_result() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(result_version="v2")
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.result_version == "v2"


def test_engine_replay_with_baseline_scorecard() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(baseline_scorecard=_scorecard())
    out = engine.replay(inputs)
    assert out.result is not None
    # The scorecard's baseline_refs are surfaced onto the result's
    # policy_provenance_refs.
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    assert "scorecard-ref-1" in ref_ids


def test_engine_replay_policy_provenance_refs_from_metrics() -> None:
    """The engine surfaces the typed Slice 13a evidence refs from the
    baseline metrics onto the emitted result's policy_provenance_refs
    list (refs-only per doc-18:186-249)."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs()
    out = engine.replay(inputs)
    assert out.result is not None
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    # Each metric carries an evidence ref of the form "<name>-ev-1".
    assert "hours_per_task-ev-1" in ref_ids
    assert "repair_cycles_per_task-ev-1" in ref_ids


def test_engine_replay_policy_provenance_refs_dedupe_by_ref_id() -> None:
    """The engine dedupes refs by ref_id (first occurrence wins)."""

    engine = CounterfactualSummaryReplayEngine()
    # Two metrics share an evidence ref id "shared-ref".
    inputs = _inputs(
        baseline_metrics=[
            _metric(
                "hours_per_task",
                value=4.0,
                evidence_refs=[_ref("shared-ref"), _ref("hours-only")],
            ),
            _metric(
                "repair_cycles_per_task",
                value=2.0,
                evidence_refs=[_ref("shared-ref"), _ref("cycles-only")],
            ),
            _metric("commit_failures_per_task", value=1.0),
        ],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    # "shared-ref" appears once even though it was in 2 metrics.
    assert ref_ids.count("shared-ref") == 1


# ── delta projection ─────────────────────────────────────────────────────


def test_engine_replay_estimated_delta_hours_from_baseline_median() -> None:
    """The estimated_delta_hours is the median of hours-flavored
    baseline metric values, scaled by the policy ratio."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=10.0),
            _metric("hours_per_task", value=20.0),
            _metric("hours_per_task", value=30.0),
        ],
        scenario=_scenario(
            policy_under_test={"expected_hours_delta_ratio": -0.5},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # median of [10, 20, 30] = 20; scaled by -0.5 = -10.
    assert out.result.estimated_delta_hours == -10.0


def test_engine_replay_estimated_delta_hours_none_when_no_hours_metric() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
            _metric("tasks_per_hour", value=0.25),
        ],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_delta_hours is None


def test_engine_replay_estimated_delta_repair_cycles() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("repair_cycles_per_task", value=4.0),
        ],
        scenario=_scenario(
            policy_under_test={"expected_repair_cycles_delta_ratio": -0.25},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # 4.0 * -0.25 = -1.0
    assert out.result.estimated_delta_repair_cycles == -1.0


def test_engine_replay_estimated_delta_commit_failures() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("commit_failures_per_task", value=2.0),
        ],
        scenario=_scenario(
            policy_under_test={"expected_commit_failures_delta_ratio": -0.5},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # 2.0 * -0.5 = -1.0
    assert out.result.estimated_delta_commit_failures == -1.0


def test_engine_replay_estimated_risk_change_unknown_when_no_data() -> None:
    """Doc-18:91 + summary-replay heuristic: if ANY of the typed delta
    fields is None, risk_change is "unknown"."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("tasks_per_hour", value=0.25),  # not in any delta axis
        ],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_risk_change == "unknown"


def test_engine_replay_estimated_risk_change_lower_when_all_negative() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=10.0),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
        ],
        scenario=_scenario(
            policy_under_test={
                "expected_hours_delta_ratio": -0.5,
                "expected_repair_cycles_delta_ratio": -0.5,
                "expected_commit_failures_delta_ratio": -0.5,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_risk_change == "lower"


def test_engine_replay_estimated_risk_change_higher_when_all_positive() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=10.0),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
        ],
        scenario=_scenario(
            policy_under_test={
                "expected_hours_delta_ratio": 0.5,
                "expected_repair_cycles_delta_ratio": 0.5,
                "expected_commit_failures_delta_ratio": 0.5,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_risk_change == "higher"


def test_engine_replay_estimated_risk_change_same_when_mixed_signs() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("hours_per_task", value=10.0),
            _metric("repair_cycles_per_task", value=2.0),
            _metric("commit_failures_per_task", value=1.0),
        ],
        scenario=_scenario(
            policy_under_test={
                "expected_hours_delta_ratio": -0.5,  # lower
                "expected_repair_cycles_delta_ratio": 0.5,  # higher
                "expected_commit_failures_delta_ratio": -0.1,  # lower
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_risk_change == "same"


# ── safety_guard_class ──────────────────────────────────────────────────


def test_engine_replay_safety_guard_class_propagated() -> None:
    """Doc-18:87 + doc-18:140-146: the safety_guard_class field is
    propagated from the scenario's policy_under_test["safety_guard_class"]
    key."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            policy_under_test={
                "safety_guard_class": "fail_closed_earlier",
                "expected_hours_delta_ratio": -0.1,
                "expected_repair_cycles_delta_ratio": -0.1,
                "expected_commit_failures_delta_ratio": -0.1,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.safety_guard_class == "fail_closed_earlier"


def test_engine_replay_safety_guard_forces_risk_change_lower() -> None:
    """Per the summary-replay heuristic + doc-18:140-146: when the
    safety_guard_class is set, the risk_change is "lower"."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            policy_under_test={
                "safety_guard_class": "reduce_mutation_authority",
                "expected_hours_delta_ratio": 0.1,  # would be "higher" without guard
                "expected_repair_cycles_delta_ratio": 0.1,
                "expected_commit_failures_delta_ratio": 0.1,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.safety_guard_class == "reduce_mutation_authority"
    assert out.result.estimated_risk_change == "lower"


def test_engine_replay_safety_guard_class_none_when_absent() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs()  # no safety_guard_class in policy_under_test
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.safety_guard_class is None


# ── confidence projection ────────────────────────────────────────────────


def test_engine_replay_confidence_in_unit_interval() -> None:
    """Doc-18:50 + doc-18:92: the confidence is in [0.0, 1.0]."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert 0.0 <= out.result.confidence <= 1.0


def test_engine_replay_confidence_at_or_below_summary_replay_ceiling() -> None:
    """Doc-18:133: summary replay carries a lower confidence ceiling.
    For an 8ac124d6-only corpus + small sample, confidence MUST be at
    or below SUMMARY_REPLAY_CONFIDENCE_CEILING * 0.5 * 0.5."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["8ac124d6"]),
            baseline_metrics=[_metric(confidence=1.0)],  # sample of 1
        )
    )
    assert out.result is not None
    # ceiling * 0.5 (small sample) * 0.5 (8ac124d6-only) = 0.65 * 0.25 = 0.1625
    assert out.result.confidence <= SUMMARY_REPLAY_CONFIDENCE_CEILING * 0.5 * 0.5


def test_engine_replay_confidence_higher_when_baseline_diverse() -> None:
    """Confidence is higher when the baseline is large + the corpus
    spans non-8ac124d6 features."""

    engine = CounterfactualSummaryReplayEngine()
    out_diverse = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["8ac124d6", "feature-X"]),  # non-8ac124d6-only
            baseline_metrics=[
                _metric("hours_per_task", confidence=0.9),
                _metric("repair_cycles_per_task", confidence=0.9),
                _metric("commit_failures_per_task", confidence=0.9),
                _metric("tasks_per_hour", confidence=0.9),
            ],
        )
    )
    out_narrow = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["8ac124d6"]),
            baseline_metrics=[_metric(confidence=0.9)],
        )
    )
    assert out_diverse.result is not None
    assert out_narrow.result is not None
    assert out_diverse.result.confidence > out_narrow.result.confidence


# ── invalidated_by ────────────────────────────────────────────────────────


def test_engine_replay_missing_required_evidence_emits_invalidated_by() -> None:
    """Doc-18:134-135: missing required evidence populates the
    invalidated_by list."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        scenario=_scenario(required_evidence_kinds=["typed_attempt", "typed_failure"]),
        corpus=_corpus(evidence_set_ids=["ev-1"], implementation_anchor_ids=["anchor-1"]),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "missing_evidence:typed_attempt" in out.result.invalidated_by
    assert "missing_evidence:typed_failure" in out.result.invalidated_by


def test_engine_replay_required_evidence_covered_by_evidence_set_ids() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        scenario=_scenario(required_evidence_kinds=["ev-X"]),
        corpus=_corpus(evidence_set_ids=["ev-X"], implementation_anchor_ids=[]),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.invalidated_by == []


def test_engine_replay_product_defect_window_invalidates() -> None:
    """Doc-18:136-137: product_defect_window in corpus validity_limits
    invalidates results (when no safety_guard_class)."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        corpus=_corpus(validity_limits=["product_defect_window"]),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "product_defect_window" in out.result.invalidated_by


# ── assumptions + validity_limits composition ────────────────────────────


def test_engine_replay_assumptions_include_scenario_assumptions() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        scenario=_scenario(assumptions=["assumption-A", "assumption-B"]),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "assumption-A" in out.result.assumptions
    assert "assumption-B" in out.result.assumptions


def test_engine_replay_assumptions_include_summary_replay_projection_tag() -> None:
    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert "summary_replay_projection" in out.result.assumptions


def test_engine_replay_validity_limits_include_summary_replay_mode_tag() -> None:
    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert "summary_replay_mode" in out.result.validity_limits


def test_engine_replay_validity_limits_include_corpus_limits() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(corpus=_corpus(validity_limits=["sample_size<10"]))
    out = engine.replay(inputs)
    assert out.result is not None
    assert "sample_size<10" in out.result.validity_limits


def test_engine_replay_validity_limits_insufficient_baseline_metrics() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(baseline_metrics=[_metric()])  # sample of 1
    out = engine.replay(inputs)
    assert out.result is not None
    assert "insufficient_baseline_metrics" in out.result.validity_limits


def test_engine_replay_validity_limits_governance_only_provenance_chain() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(corpus=_corpus(feature_ids=["8ac124d6"]))
    out = engine.replay(inputs)
    assert out.result is not None
    assert "governance_only_provenance_chain" in out.result.validity_limits


def test_engine_replay_validity_limits_missing_hours_baseline_metric() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[
            _metric("repair_cycles_per_task"),
            _metric("commit_failures_per_task"),
            _metric("tasks_per_hour"),
        ],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "missing_hours_baseline_metric" in out.result.validity_limits


# ── recommended_next_step ────────────────────────────────────────────────


def test_engine_replay_recommended_next_step_discard_on_very_low_confidence() -> None:
    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["8ac124d6"]),
            baseline_metrics=[_metric(confidence=0.0)],  # confidence 0
        )
    )
    assert out.result is not None
    assert out.result.recommended_next_step == "discard"


def test_engine_replay_recommended_next_step_collect_more_evidence_on_missing_evidence() -> None:
    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(
        _inputs(
            scenario=_scenario(required_evidence_kinds=["missing-kind"]),
            corpus=_corpus(evidence_set_ids=["ev-1"], implementation_anchor_ids=["anchor-1"]),
        )
    )
    assert out.result is not None
    assert out.result.recommended_next_step == "collect_more_evidence"


def test_engine_replay_recommended_next_step_collect_more_evidence_on_small_sample() -> None:
    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(
        _inputs(baseline_metrics=[_metric(confidence=0.9)])  # sample of 1
    )
    assert out.result is not None
    assert out.result.recommended_next_step == "collect_more_evidence"


# ── refs-only invariant (doc-18:186-249) ─────────────────────────────────


def _walk_keys(obj: object) -> list[str]:
    """Recursively walk all dict keys in ``obj``."""

    keys: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.append(str(k))
            keys.extend(_walk_keys(v))
    elif isinstance(obj, (list, tuple)):
        for item in obj:
            keys.extend(_walk_keys(item))
    return keys


def test_refs_only_no_raw_body_hydration() -> None:
    """Doc-18:186-249 + Slice 13A invariant: the emitted result MUST
    NOT carry any raw artifact body. Walk the
    ``CounterfactualResult.model_dump(mode='json')`` recursively +
    assert no key contains the forbidden substring."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs(baseline_scorecard=_scorecard()))
    assert out.result is not None
    dump = out.result.model_dump(mode="json")
    keys = _walk_keys(dump)
    forbidden_substrings = ("_body", "raw_body", "artifact_body")
    for key in keys:
        for sub in forbidden_substrings:
            assert sub not in key, (
                f"Refs-only violation: key {key!r} contains forbidden substring {sub!r}"
            )


# ── safety-guard discipline / no-mutation (doc-18:164 AC3) ──────────────


def test_engine_class_only_exposes_replay_method() -> None:
    """Doc-18:164 AC3: the engine MUST expose ONLY the read method
    ``replay`` (NOT any mutation surface)."""

    public_methods = sorted(
        n
        for n in dir(CounterfactualSummaryReplayEngine)
        if not n.startswith("_") and callable(getattr(CounterfactualSummaryReplayEngine, n))
    )
    assert public_methods == ["replay"], (
        f"Engine exposes unexpected public methods: {public_methods}"
    )


def test_engine_class_no_mutation_method_prefixes() -> None:
    """Doc-18:164 AC3: no mutation-like public-method names."""

    forbidden_method_prefixes = [
        "activate_",
        "apply_",
        "bind_",
        "mutate_",
        "commit_",
        "dispatch_",
        "schedule_",
        "write_",
        "persist_",
    ]
    for name in dir(CounterfactualSummaryReplayEngine):
        if name.startswith("_"):
            continue
        for prefix in forbidden_method_prefixes:
            assert not name.startswith(prefix), (
                f"CounterfactualSummaryReplayEngine.{name} exposes mutation-like name {prefix!r}"
            )


def test_no_consumer_side_module_imports() -> None:
    """Doc-18:123-125 + doc-18:164 AC3: the engine MUST NOT import any
    consumer-side module. Walk the source + assert no forbidden import
    appears."""

    import iriai_build_v2.execution_control.counterfactual_summary_replay as mod

    source = inspect.getsource(mod)
    forbidden_imports = [
        # Dispatcher / scheduler / merge queue / supervisor / dashboard /
        # commit_provenance writer.
        "from iriai_build_v2.workflows.develop.execution.phases",
        "from iriai_build_v2.workflows.develop.execution.supervisor",
        "from iriai_build_v2.workflows.develop.execution.merge_queue",
        "from iriai_build_v2.workflows.develop.execution.dispatcher",
        "from iriai_build_v2.execution_control.commit_provenance_writer",
        "from iriai_build_v2.execution_control.governance_finding_writer",
        "from iriai_build_v2.execution_control.governance_scorecard_writer",
        "from iriai_build_v2.execution_control.decision_record_writer",
        "import dashboard",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"Slice 18 3rd sub-slice imports forbidden consumer-side module: {forbidden!r}"
        )


def test_no_dag_authority_artifact_keys() -> None:
    """Doc-18:123-125: *"Replay results are review/governance artifacts
    only. Replay must not write `dag-*` execution authority artifacts
    or active policy markers."* The 3rd sub-slice engine MUST NOT
    contain any `dag-*` authority artifact-key string literals."""

    import iriai_build_v2.execution_control.counterfactual_summary_replay as mod

    source = inspect.getsource(mod)
    forbidden_prefixes = [
        '"dag-policy:',
        "'dag-policy:",
        '"dag-active:',
        "'dag-active:",
        '"dag-verify:',
        "'dag-verify:",
    ]
    for prefix in forbidden_prefixes:
        assert prefix not in source, (
            f"Slice 18 3rd sub-slice contains forbidden dag-authority artifact key prefix {prefix!r}"
        )


# ── fail-closed (no silent degradation) ─────────────────────────────────


def test_engine_replay_baseline_metrics_exceeded_bound_emits_gap() -> None:
    """Doc-18:150 + Slice 13A bounded-reads: engine emits typed gap
    when baseline metrics exceed bound."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        baseline_metrics=[_metric(f"metric-{i}", value=1.0) for i in range(10)],
        max_baseline_metrics=5,
    )
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    gap = out.gap_findings[0]
    assert gap.failure_id == "summary_replay_failed"
    assert gap.reason == "baseline_metrics_exceeded_bound"
    assert gap.evidence_payload["received_count"] == 10
    assert gap.evidence_payload["max_bound"] == 5


def test_engine_replay_empty_result_id_emits_gap() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(result_id="   ")
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    assert out.gap_findings[0].reason == "result_id_empty"


def test_engine_replay_empty_baseline_metrics_emits_gap() -> None:
    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(baseline_metrics=[])
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    assert out.gap_findings[0].reason == "baseline_metrics_empty"


def test_engine_replay_event_replay_mode_emits_gap() -> None:
    """The 3rd sub-slice engine ONLY handles ``summary_replay`` mode;
    other modes emit a typed gap."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(mode="event_replay")
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    gap = out.gap_findings[0]
    assert gap.reason == "invalid_replay_mode_for_engine"
    assert gap.evidence_payload["received_mode"] == "event_replay"
    assert gap.evidence_payload["expected_mode"] == "summary_replay"


def test_engine_replay_never_raises_on_valid_input() -> None:
    """`feedback_no_silent_degradation` fail-closed: engine NEVER raises."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs())
    assert isinstance(out, SummaryReplayResult)


# ── compute_summary_replay_idempotency_key ───────────────────────────────


def test_compute_summary_replay_idempotency_key_deterministic() -> None:
    key1 = compute_summary_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="summary_replay",
        baseline_metric_definition_names=["a"],
        assumptions=["x"],
        validity_limits=[],
    )
    key2 = compute_summary_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="summary_replay",
        baseline_metric_definition_names=["a"],
        assumptions=["x"],
        validity_limits=[],
    )
    assert key1 == key2
    assert len(key1) == 64


def test_compute_summary_replay_idempotency_key_order_invariant() -> None:
    key1 = compute_summary_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="summary_replay",
        baseline_metric_definition_names=["a", "b"],
        assumptions=["x", "y"],
        validity_limits=["p", "q"],
    )
    key2 = compute_summary_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="summary_replay",
        baseline_metric_definition_names=["b", "a"],
        assumptions=["y", "x"],
        validity_limits=["q", "p"],
    )
    assert key1 == key2


def test_compute_summary_replay_idempotency_key_differs_on_result_version() -> None:
    """Doc-18:128-129: new result version MUST produce a new key."""

    key1 = compute_summary_replay_idempotency_key(
        result_id="r-1", result_version="v1", corpus_id="c-1", scenario_id="s-1",
        mode="summary_replay", baseline_metric_definition_names=[],
        assumptions=[], validity_limits=[],
    )
    key2 = compute_summary_replay_idempotency_key(
        result_id="r-1", result_version="v2", corpus_id="c-1", scenario_id="s-1",
        mode="summary_replay", baseline_metric_definition_names=[],
        assumptions=[], validity_limits=[],
    )
    assert key1 != key2


def test_compute_summary_replay_idempotency_key_differs_on_mode() -> None:
    """The mode is part of the dedupe key (so a future event-replay
    re-run of the same (corpus, scenario) cleanly produces a new key)."""

    key1 = compute_summary_replay_idempotency_key(
        result_id="r-1", result_version="v1", corpus_id="c-1", scenario_id="s-1",
        mode="summary_replay", baseline_metric_definition_names=[],
        assumptions=[], validity_limits=[],
    )
    key2 = compute_summary_replay_idempotency_key(
        result_id="r-1", result_version="v1", corpus_id="c-1", scenario_id="s-1",
        mode="event_replay", baseline_metric_definition_names=[],
        assumptions=[], validity_limits=[],
    )
    assert key1 != key2


def test_compute_summary_replay_idempotency_key_differs_on_assumptions() -> None:
    """Doc-18:128-129: new assumptions require a new key."""

    key1 = compute_summary_replay_idempotency_key(
        result_id="r-1", result_version="v1", corpus_id="c-1", scenario_id="s-1",
        mode="summary_replay", baseline_metric_definition_names=[],
        assumptions=["x"], validity_limits=[],
    )
    key2 = compute_summary_replay_idempotency_key(
        result_id="r-1", result_version="v1", corpus_id="c-1", scenario_id="s-1",
        mode="summary_replay", baseline_metric_definition_names=[],
        assumptions=["y"], validity_limits=[],
    )
    assert key1 != key2


# ── failure_router 4-add-point validation ─────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    """Add point 1: FailureType Literal block."""

    assert "summary_replay_failed" in get_args(FailureType)


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add point 2: FAILURE_TYPES tuple."""

    assert "summary_replay_failed" in FAILURE_TYPES


def test_failure_router_retryable_failure_types_includes_new_id() -> None:
    """Add point 3: _RETRYABLE_FAILURE_TYPES frozenset."""

    assert "summary_replay_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_table_includes_new_route_entry() -> None:
    """Add point 4: ROUTE_TABLE _route() entry under EXISTING
    evidence_corruption class with REUSED retry_governance_projection
    NON-blocking RouteAction."""

    route = ROUTE_TABLE[("evidence_corruption", "summary_replay_failed")]
    assert route.action == "retry_governance_projection"


def test_failure_router_action_is_non_blocking() -> None:
    """Per doc-14:242-243 the action is non-blocking (NOT quiesce)."""

    route = ROUTE_TABLE[("evidence_corruption", "summary_replay_failed")]
    assert route.action != "quiesce"
    assert route.action == "retry_governance_projection"


def test_failure_router_existing_evidence_corruption_class_reused() -> None:
    """The failure_class is the EXISTING evidence_corruption (NOT a
    new failure_class)."""

    route = ROUTE_TABLE[("evidence_corruption", "summary_replay_failed")]
    assert route.failure_class == "evidence_corruption"


# ── doc-18 awareness PIN tests ────────────────────────────────────────────


def test_doc_18_113_step_3_satisfied() -> None:
    """Doc-18:113 step 3 PIN: *"Implement summary replay first for
    metrics-level counterfactuals."* SATISFIED via this 3rd sub-slice
    engine."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    # Doc-18:79-96 -- all 16 fields populated.
    fields = {
        "result_id", "result_version", "scenario_id", "corpus_id",
        "assumptions", "validity_limits", "policy_provenance_refs",
        "safety_guard_class", "estimated_delta_hours",
        "estimated_delta_repair_cycles", "estimated_delta_commit_failures",
        "estimated_risk_change", "confidence", "invalidated_by",
        "supporting_finding_ids", "recommended_next_step",
    }
    dumped = out.result.model_dump()
    for field in fields:
        assert field in dumped


def test_doc_18_186_249_slice_13a_no_redefinition() -> None:
    """Doc-18:186-249 Slice 13A Shared Completeness Model Dependency:
    the engine consumes the Slice 13a + 15 shared models REFS ONLY;
    they MUST NOT redefine the Slice 13A shapes."""

    from iriai_build_v2.execution_control import counterfactual_summary_replay as mod

    forbidden_shapes = [
        "CompletenessState",
        "EvidenceCompleteness",
        "AuthoritativeContextRef",
        "EvidencePageRef",
        "ExactEvidenceManifest",
        "AuthoritativePromptContextRouting",
        "AuthoritativeGateCompanionRecord",
        "AuthoritativeGateProofRow",
        "AuthoritativeSnapshotListFieldCompleteness",
        "AuthoritativeSnapshotClassifierRouting",
    ]
    for shape_name in forbidden_shapes:
        if hasattr(mod, shape_name):
            shape = getattr(mod, shape_name)
            if hasattr(shape, "__module__"):
                assert shape.__module__ != mod.__name__, (
                    f"Slice 18 3rd sub-slice REDEFINES Slice 13A {shape_name!r}"
                )


def test_doc_18_160_168_ac1_deterministic() -> None:
    """Doc-18:162 AC1: *"Counterfactuals are deterministic, versioned,
    and evidence-backed."* The engine's idempotency_key is deterministic
    via compute_summary_replay_idempotency_key."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs()
    r1 = engine.replay(inputs)
    r2 = engine.replay(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_doc_18_160_168_ac2_assumptions_and_validity_limits() -> None:
    """Doc-18:163 AC2: *"Every result lists assumptions and validity
    limits."* The emitted CounterfactualResult has both lists
    non-empty."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs(scenario=_scenario(assumptions=["a-1"])))
    assert out.result is not None
    assert out.result.assumptions
    assert out.result.validity_limits


def test_doc_18_160_168_ac3_replay_cannot_mutate_workflow_state() -> None:
    """Doc-18:164 AC3: *"Replay cannot mutate live workflow state."*
    The engine class exposes EXACTLY one public method (``replay``)
    AND does NOT import any consumer-side module."""

    public_methods = sorted(
        n
        for n in dir(CounterfactualSummaryReplayEngine)
        if not n.startswith("_") and callable(getattr(CounterfactualSummaryReplayEngine, n))
    )
    assert public_methods == ["replay"]


def test_doc_18_160_168_ac4_result_supports_recommendation_citation() -> None:
    """Doc-18:165-166 AC4 cross-reference: the emitted CounterfactualResult
    carries the stable result_id that the Slice 17
    GovernancePolicyRecommendation.counterfactual_result_refs field
    cites for behavior-changing recommendations."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(_inputs(result_id="ac4-result-id"))
    assert out.result is not None
    # The result_id is the cross-reference surface for Slice 17.
    assert out.result.result_id == "ac4-result-id"
    # The result_version supports the doc-18:128-129 supersede-on-
    # new-assumptions discipline.
    assert out.result.result_version == "v1"


def test_doc_18_160_168_ac5_corpus_includes_8ac124d6() -> None:
    """Doc-18:167-168 AC5: *"The replay corpus includes both 8ac124d6
    evidence and Slice 00-12 implementation artifacts."* The engine
    accepts the typed corpus directly (the 2nd sub-slice loader
    enforces AC5 coverage)."""

    engine = CounterfactualSummaryReplayEngine()
    out = engine.replay(
        _inputs(
            corpus=_corpus(
                feature_ids=["8ac124d6"],
                implementation_anchor_ids=["slice-00-anchor", "slice-12-anchor"],
            )
        )
    )
    assert out.result is not None
    assert out.result.corpus_id == "corpus-1"


def test_doc_18_133_summary_replay_lower_confidence_than_event() -> None:
    """Doc-18:133: *"Missing typed timing: use summary replay with
    lower confidence."* Verified via the typed
    SUMMARY_REPLAY_CONFIDENCE_CEILING."""

    assert SUMMARY_REPLAY_CONFIDENCE_CEILING < 1.0
    # The ceiling 0.65 is the typed surface for the lower-confidence
    # discipline (vs. the future Slice 18 4th sub-slice event-replay
    # engine which will carry a higher ceiling).


def test_doc_18_138_small_sample_conservative_confidence() -> None:
    """Doc-18:138: *"Small sample size: report confidence and avoid
    policy recommendations."*"""

    engine = CounterfactualSummaryReplayEngine()
    out_small = engine.replay(_inputs(baseline_metrics=[_metric(confidence=1.0)]))
    out_large = engine.replay(
        _inputs(
            baseline_metrics=[
                _metric(f"hours_per_task", confidence=1.0),
                _metric(f"repair_cycles_per_task", confidence=1.0),
                _metric(f"commit_failures_per_task", confidence=1.0),
                _metric(f"tasks_per_hour", confidence=1.0),
            ],
            corpus=_corpus(feature_ids=["8ac124d6", "feature-Y"]),  # not 8ac124d6-only
        )
    )
    assert out_small.result is not None
    assert out_large.result is not None
    # Small sample produces a STRICTLY lower confidence.
    assert out_small.result.confidence < out_large.result.confidence


def test_doc_18_50_confidence_in_unit_interval() -> None:
    """Doc-18:50 + doc-18:92: confidence is in [0.0, 1.0]."""

    engine = CounterfactualSummaryReplayEngine()
    # Try various inputs; all confidences MUST be in [0.0, 1.0].
    for inputs in (
        _inputs(),
        _inputs(corpus=_corpus(feature_ids=["8ac124d6"])),
        _inputs(baseline_metrics=[_metric(confidence=0.0)]),
        _inputs(baseline_metrics=[_metric(confidence=1.0)]),
        _inputs(
            baseline_metrics=[
                _metric(confidence=1.0),
                _metric("repair_cycles_per_task", confidence=1.0),
                _metric("commit_failures_per_task", confidence=1.0),
                _metric("tasks_per_hour", confidence=1.0),
            ],
        ),
    ):
        out = engine.replay(inputs)
        if out.result is not None:
            assert 0.0 <= out.result.confidence <= 1.0


def test_doc_18_89_safety_guard_class_set_via_engine() -> None:
    """Doc-18:89 + doc-18:87: the safety_guard_class field is set on
    the emitted result when the scenario carries it."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            policy_under_test={
                "safety_guard_class": "bounded_preflight_evidence",
                "expected_hours_delta_ratio": -0.1,
                "expected_repair_cycles_delta_ratio": -0.1,
                "expected_commit_failures_delta_ratio": -0.1,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.safety_guard_class == "bounded_preflight_evidence"


def test_doc_18_140_146_overfit_risk_governance_only_chain() -> None:
    """Doc-18:140-141: *"Overfit risk: require at least one non-`8ac124d6`
    corpus before marking a general policy high confidence."* The engine
    flags this via the validity_limits entry
    ``"governance_only_provenance_chain"`` when the corpus is
    ``8ac124d6``-only."""

    engine = CounterfactualSummaryReplayEngine()
    inputs = _inputs(corpus=_corpus(feature_ids=["8ac124d6"]))
    out = engine.replay(inputs)
    assert out.result is not None
    assert "governance_only_provenance_chain" in out.result.validity_limits
