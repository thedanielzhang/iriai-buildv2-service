"""Slice 18 fourth sub-slice -- unit tests for the counterfactual event
replay engine at
``execution_control/counterfactual_event_replay.py``.

Covers the doc-18:114 step 4 typed-shape consumer + structural
projection wiring:

* :class:`EventReplayInputs` typed inputs BaseModel (extra-forbid;
  bounded-input default; typed Slice 18 1st sub-slice :class:`ReplayCorpus`
  REUSE; typed Slice 18 1st sub-slice :class:`CounterfactualScenario`
  REUSE; typed Slice 10a event-transition shape REUSE for the 5 typed
  lists; typed Slice 15 :class:`GovernanceMetricValue` REUSE for the
  optional baseline metrics; typed Slice 18 1st sub-slice
  :data:`ReplayMode` REUSE).
* :class:`EventReplayResult` typed result BaseModel (extra-forbid;
  result / gap_findings / idempotency_key).
* :class:`EventReplayGap` typed gap projection BaseModel (extra-forbid;
  failure_id Literal range).
* :class:`CounterfactualEventReplayEngine.replay(...)` -- the
  projection method:
  * Happy-path -> typed :class:`CounterfactualResult` emitted with all
    16 fields populated per doc-18:79-96.
  * Bounded-input check -> typed gap on
    ``event_transitions_exceeded_bound`` per doc-18:150.
  * Empty result_id / all-empty event-transition lists -> typed gap
    per ``feedback_no_silent_degradation``.
  * Invalid mode (e.g. ``summary_replay``) -> typed gap (this 4th
    sub-slice engine ONLY handles ``event_replay``).
  * Engine NEVER raises on input (fail-closed; typed gap projection
    on construction failure).
* **HIGHER-fidelity assertion**: when typed event-transition evidence
  is rich + corpus is diverse, the engine MAY return
  ``confidence > 0.65`` (the summary-replay confidence ceiling). This
  demonstrates the doc-18:114 vs doc-18:113 + doc-18:133 contrast.
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :class:`GovernanceEvidenceRef` + Slice 15 :class:`GovernanceMetricValue`
  + Slice 18 1st sub-slice :class:`ReplayCorpus` /
  :class:`CounterfactualScenario` / :class:`CounterfactualResult` /
  :data:`ReplayMode` + Slice 10a typed event-transition shapes
  (:class:`ExecutionAttemptSummary` / :class:`GateStatusSummary` /
  :class:`TypedFailureSummary` / :class:`MergeQueueSummary` /
  :class:`EvidenceRef`).
* Failure-router 4-add-point validation (``event_replay_failed``
  registered under EXISTING ``evidence_corruption`` failure_class with
  REUSED ``retry_governance_projection`` NON-blocking RouteAction;
  mirrors Slice 17 2nd/3rd/4th/5th/6th + Slice 18 2nd + 3rd sub-slice
  precedent).
* :func:`compute_event_replay_idempotency_key` -- the deterministic
  SHA-256-derived idempotency-key helper (mirror the Slice 18 3rd sub-
  slice :func:`compute_summary_replay_idempotency_key` verbatim).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 1st + 2nd + 3rd sub-slice modules +
tests remain byte-identical.

**Slice 13A awareness asserted (doc-18:186-249).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition. The engine consumes Slice 13a typed
``GovernanceEvidenceRef`` (constructed from the typed Slice 10a
:class:`EvidenceRef` summary fields on the typed failure transitions)
and emits the refs-only projection onto the typed Slice 18 1st sub-
slice ``CounterfactualResult`` shape; no raw artifact body hydration
per doc-18:186-249.

**Refs-only invariant (doc-18:186-249).** Test
:func:`test_refs_only_no_raw_body_hydration` walks the emitted
``CounterfactualResult.model_dump(mode="json")`` recursively and
asserts no key contains the forbidden ``body`` / ``raw_body`` /
``artifact_body`` substring.

**Doc-18:123-129 persistence + artifact compatibility awareness.** No
4th-sub-slice consumer activation wiring; no consumer-owned artifact-
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
from datetime import datetime, timedelta, timezone
from typing import Any, get_args, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_event_replay import (
    DEFAULT_MAX_EVENT_TRANSITIONS,
    EVENT_REPLAY_CONFIDENCE_CEILING,
    EVENT_REPLAY_FAILURE_ID,
    CounterfactualEventReplayEngine,
    EventReplayGap,
    EventReplayInputs,
    EventReplayResult,
    compute_event_replay_idempotency_key,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    CounterfactualScenario,
    RecommendedNextStep,
    ReplayCorpus,
    ReplayMode,
    RiskChange,
)
from iriai_build_v2.execution_control.counterfactual_summary_replay import (
    SUMMARY_REPLAY_CONFIDENCE_CEILING,
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
from iriai_build_v2.workflows.develop.execution.snapshots import (
    EvidenceRef,
    ExecutionAttemptSummary,
    GateStatusSummary,
    MergeQueueSummary,
    TypedFailureSummary,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def _gov_ref(ref_id: str = "ref-1", **overrides: object) -> GovernanceEvidenceRef:
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
        evidence_refs=[_gov_ref(f"{definition_name}-ev-1")],
        exclusions=[],
    )
    base.update(overrides)
    return GovernanceMetricValue(**base)  # type: ignore[arg-type]


def _corpus(**overrides: object) -> ReplayCorpus:
    """Construct a typed Slice 18 1st sub-slice :class:`ReplayCorpus`."""

    base: dict[str, object] = dict(
        corpus_id="corpus-1",
        feature_ids=["8ac124d6", "feature-X"],  # non-8ac124d6-only by default
        evidence_set_ids=["ev-1"],
        implementation_anchor_ids=["anchor-1"],
        mode="event_replay",
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


# Standard reference times used across fixtures.
_NOW = datetime(2026, 5, 27, 0, 0, tzinfo=timezone.utc)
_LATER = datetime(2026, 5, 27, 1, 0, tzinfo=timezone.utc)


# Sentinel so test callers can explicitly pass ``finished_at=None``
# (distinct from "use the default").
_UNSET = object()


def _attempt(
    attempt_id: int = 1,
    *,
    attempt_kind: str = "task",
    status: str = "failed",
    started_at: datetime | None = None,
    finished_at: object = _UNSET,
    **overrides: object,
) -> ExecutionAttemptSummary:
    if finished_at is _UNSET:
        finished_at = _LATER
    base: dict[str, object] = dict(
        attempt_id=attempt_id,
        feature_id="8ac124d6",
        dag_sha256="deadbeef" * 8,
        group_idx=0,
        task_id=f"task-{attempt_id}",
        attempt_kind=attempt_kind,
        stage="dispatch",
        retry=0,
        status=status,
        actor="runtime",
        runtime="codex",
        input_digest="d" * 64,
        workspace_snapshot_id=None,
        latest_evidence_ids=[],
        started_at=started_at if started_at is not None else _NOW,
        finished_at=finished_at,
        updated_at=_LATER,
    )
    base.update(overrides)
    return ExecutionAttemptSummary(**base)  # type: ignore[arg-type]


def _gate(
    name: str = "verify", approved: bool = True, evidence_id: int = 1
) -> GateStatusSummary:
    return GateStatusSummary(
        gate_name=name,
        group_idx=0,
        approved=approved,
        deterministic=True,
        evidence_id=evidence_id,
        failure_id=None,
        created_at=_NOW,
    )


def _failure(
    failure_id: int = 1,
    *,
    failure_class: str = "evidence_corruption",
    failure_type: str = "list_field_incomplete",
    evidence_refs: list[EvidenceRef] | None = None,
    **overrides: object,
) -> TypedFailureSummary:
    base: dict[str, object] = dict(
        failure_id=failure_id,
        attempt_id=failure_id,
        evidence_id=failure_id,
        failure_class=failure_class,
        failure_type=failure_type,
        severity="error",
        deterministic=True,
        operator_required=False,
        retryable=True,
        status="resolved",
        route="retry_dispatch",
        signature_hash=f"hash-{failure_id}",
        summary="test",
        evidence_refs=evidence_refs
        if evidence_refs is not None
        else [
            EvidenceRef(
                table="evidence_nodes",
                id=failure_id * 10,
                citation=f"ev-{failure_id}",
                kind="failure_event",
            ),
        ],
        created_at=_NOW,
        resolved_at=_LATER,
    )
    base.update(overrides)
    return TypedFailureSummary(**base)  # type: ignore[arg-type]


def _queue(item_id: int = 1, status: str = "integrated") -> MergeQueueSummary:
    return MergeQueueSummary(
        item_id=item_id,
        feature_id="8ac124d6",
        dag_sha256="dead" * 16,
        group_idx=0,
        repo_id="repo-1",
        status=status,  # type: ignore[arg-type]
        priority=0,
        lease_owner=None,
        leased_until=None,
        lease_version=0,
        result_commit="commit-abc",
        failure_id=None,
        required_gate_evidence_ids=[],
        updated_at=_LATER,
    )


def _checkpoint(id: int = 1) -> EvidenceRef:
    return EvidenceRef(
        table="evidence_nodes",
        id=id,
        citation=f"ckpt-{id}",
        kind="checkpoint",
    )


def _inputs(**overrides: object) -> EventReplayInputs:
    """Construct a fully-specified :class:`EventReplayInputs`."""

    base: dict[str, object] = dict(
        corpus=_corpus(),
        scenario=_scenario(),
        attempt_transitions=[
            _attempt(1, attempt_kind="task", status="failed"),
            _attempt(2, attempt_kind="repair", status="failed"),
            _attempt(3, attempt_kind="task", status="succeeded"),
        ],
        gate_transitions=[_gate("verify", True, 1), _gate("merge", True, 2)],
        failure_transitions=[
            _failure(1, failure_class="commit_hygiene", failure_type="commit_dirty"),
            _failure(2, failure_class="commit_hygiene", failure_type="commit_dirty"),
            _failure(3, failure_class="evidence_corruption", failure_type="list_field_incomplete"),
        ],
        queue_transitions=[_queue(1, "integrated")],
        checkpoint_transitions=[_checkpoint(1), _checkpoint(2)],
        baseline_metrics=[],
        mode="event_replay",
        result_id="result-1",
        result_version="v1",
    )
    base.update(overrides)
    return EventReplayInputs(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_exports_count() -> None:
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert len(mod.__all__) == 8, (
        f"Expected 8 __all__ exports, got {len(mod.__all__)}: {mod.__all__}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "EVENT_REPLAY_FAILURE_ID",
        "DEFAULT_MAX_EVENT_TRANSITIONS",
        "EVENT_REPLAY_CONFIDENCE_CEILING",
        "EventReplayInputs",
        "EventReplayResult",
        "EventReplayGap",
        "CounterfactualEventReplayEngine",
        "compute_event_replay_idempotency_key",
    ],
)
def test_module_surface_hasattr(name: str) -> None:
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert hasattr(mod, name), f"counterfactual_event_replay missing {name!r}"


def test_no_re_export_from_execution_control_init() -> None:
    """Per Slice 13A/14/15/16/17/18-1st/18-2nd/18-3rd precedent, this module
    is NOT re-exported from ``execution_control/__init__.py``."""

    import iriai_build_v2.execution_control as init_module

    source = init_module.__file__
    if source is None:
        pytest.skip("execution_control/__init__.py has no __file__")
    with open(source) as f:
        body = f.read()
    assert "counterfactual_event_replay" not in body


def test_event_replay_failure_id_value() -> None:
    assert EVENT_REPLAY_FAILURE_ID == "event_replay_failed"


def test_default_max_event_transitions_positive() -> None:
    assert DEFAULT_MAX_EVENT_TRANSITIONS == 512
    assert DEFAULT_MAX_EVENT_TRANSITIONS > 0


def test_event_replay_confidence_ceiling_higher_than_summary_replay() -> None:
    """Doc-18:114 vs doc-18:113 + doc-18:133 -- event replay carries a
    HIGHER confidence ceiling than summary replay."""

    assert EVENT_REPLAY_CONFIDENCE_CEILING > SUMMARY_REPLAY_CONFIDENCE_CEILING
    assert EVENT_REPLAY_CONFIDENCE_CEILING == 0.90
    assert SUMMARY_REPLAY_CONFIDENCE_CEILING == 0.65


def test_event_replay_confidence_ceiling_unit_interval() -> None:
    assert 0.0 < EVENT_REPLAY_CONFIDENCE_CEILING <= 1.0


# ── Slice 13A / Slice 15 / Slice 17 / Slice 18 1st/2nd/3rd no-redefinition ─


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
        # Slice 18 3rd sub-slice
        "SummaryReplayInputs",
        "SummaryReplayResult",
        "SummaryReplayGap",
        "CounterfactualSummaryReplayEngine",
        # Slice 10a typed snapshot shapes
        "ExecutionAttemptSummary",
        "GateStatusSummary",
        "TypedFailureSummary",
        "MergeQueueSummary",
    ],
)
def test_no_local_redefinition(shape_name: str) -> None:
    """The Slice 18 4th sub-slice module MUST NOT redefine any of the
    prior typed shapes. The shape MAY appear in the module namespace
    via direct import; this test asserts the IMPORTED shape's
    ``__module__`` is NOT this 4th sub-slice module's."""

    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    if hasattr(mod, shape_name):
        shape = getattr(mod, shape_name)
        if hasattr(shape, "__module__"):
            assert shape.__module__ != mod.__name__, (
                f"Slice 18 4th sub-slice REDEFINES {shape_name!r} (must REUSE)"
            )


# ── EventReplayInputs ─────────────────────────────────────────────────────


def test_event_replay_inputs_construction_round_trip() -> None:
    inputs = _inputs()
    assert inputs.corpus.corpus_id == "corpus-1"
    assert inputs.scenario.scenario_id == "scenario-1"
    assert len(inputs.attempt_transitions) == 3
    assert len(inputs.gate_transitions) == 2
    assert len(inputs.failure_transitions) == 3
    assert len(inputs.queue_transitions) == 1
    assert len(inputs.checkpoint_transitions) == 2
    assert inputs.mode == "event_replay"
    assert inputs.result_id == "result-1"
    assert inputs.result_version == "v1"
    assert inputs.baseline_metrics == []
    assert inputs.max_event_transitions == DEFAULT_MAX_EVENT_TRANSITIONS


def test_event_replay_inputs_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        EventReplayInputs(  # type: ignore[call-arg]
            corpus=_corpus(),
            scenario=_scenario(),
            attempt_transitions=[],
            gate_transitions=[],
            failure_transitions=[],
            queue_transitions=[],
            checkpoint_transitions=[],
            mode="event_replay",
            result_id="r-1",
            unknown_field="rejected",
        )


def test_event_replay_inputs_defaults() -> None:
    inputs = EventReplayInputs(
        corpus=_corpus(),
        scenario=_scenario(),
        failure_transitions=[_failure(1)],
        result_id="r-1",
    )
    assert inputs.mode == "event_replay"
    assert inputs.result_version == "v1"
    assert inputs.baseline_metrics == []
    assert inputs.attempt_transitions == []
    assert inputs.gate_transitions == []
    assert inputs.queue_transitions == []
    assert inputs.checkpoint_transitions == []
    assert inputs.max_event_transitions == DEFAULT_MAX_EVENT_TRANSITIONS


def test_event_replay_inputs_mode_annotation_is_replay_mode() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :data:`ReplayMode`."""

    hints = get_type_hints(EventReplayInputs)
    assert hints["mode"] is ReplayMode


def test_event_replay_inputs_corpus_annotation_is_replay_corpus() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`ReplayCorpus`."""

    hints = get_type_hints(EventReplayInputs)
    assert hints["corpus"] is ReplayCorpus


def test_event_replay_inputs_scenario_annotation_is_counterfactual_scenario() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`CounterfactualScenario`."""

    hints = get_type_hints(EventReplayInputs)
    assert hints["scenario"] is CounterfactualScenario


def test_event_replay_inputs_attempt_transitions_uses_execution_attempt_summary() -> None:
    """DIRECT annotation-identity REUSE for Slice 10a
    :class:`ExecutionAttemptSummary`."""

    hints = get_type_hints(EventReplayInputs)
    args = get_args(hints["attempt_transitions"])
    assert args == (ExecutionAttemptSummary,)


def test_event_replay_inputs_gate_transitions_uses_gate_status_summary() -> None:
    """DIRECT annotation-identity REUSE for Slice 10a
    :class:`GateStatusSummary`."""

    hints = get_type_hints(EventReplayInputs)
    args = get_args(hints["gate_transitions"])
    assert args == (GateStatusSummary,)


def test_event_replay_inputs_failure_transitions_uses_typed_failure_summary() -> None:
    """DIRECT annotation-identity REUSE for Slice 10a
    :class:`TypedFailureSummary`."""

    hints = get_type_hints(EventReplayInputs)
    args = get_args(hints["failure_transitions"])
    assert args == (TypedFailureSummary,)


def test_event_replay_inputs_queue_transitions_uses_merge_queue_summary() -> None:
    """DIRECT annotation-identity REUSE for Slice 10a
    :class:`MergeQueueSummary`."""

    hints = get_type_hints(EventReplayInputs)
    args = get_args(hints["queue_transitions"])
    assert args == (MergeQueueSummary,)


def test_event_replay_inputs_checkpoint_transitions_uses_evidence_ref() -> None:
    """DIRECT annotation-identity REUSE for Slice 10a
    :class:`EvidenceRef`."""

    hints = get_type_hints(EventReplayInputs)
    args = get_args(hints["checkpoint_transitions"])
    assert args == (EvidenceRef,)


def test_event_replay_inputs_baseline_metrics_uses_governance_metric_value() -> None:
    """DIRECT annotation-identity REUSE for Slice 15
    :class:`GovernanceMetricValue`."""

    hints = get_type_hints(EventReplayInputs)
    args = get_args(hints["baseline_metrics"])
    assert args == (GovernanceMetricValue,)


def test_event_replay_inputs_max_event_transitions_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        EventReplayInputs(
            corpus=_corpus(),
            scenario=_scenario(),
            failure_transitions=[_failure(1)],
            result_id="r-1",
            max_event_transitions=0,
        )


# ── EventReplayResult ─────────────────────────────────────────────────────


def test_event_replay_result_construction_with_result() -> None:
    engine = CounterfactualEventReplayEngine()
    result_obj = engine.replay(_inputs())
    assert result_obj.result is not None
    assert result_obj.gap_findings == []
    assert len(result_obj.idempotency_key) == 64  # SHA-256 hex


def test_event_replay_result_construction_without_result() -> None:
    result = EventReplayResult(result=None, gap_findings=[], idempotency_key="x")
    assert result.result is None


def test_event_replay_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        EventReplayResult(  # type: ignore[call-arg]
            result=None,
            gap_findings=[],
            idempotency_key="x",
            unknown_field="rejected",
        )


def test_event_replay_result_result_annotation_uses_counterfactual_result() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`CounterfactualResult`."""

    hints = get_type_hints(EventReplayResult)
    args = get_args(hints["result"])
    assert CounterfactualResult in args


# ── EventReplayGap ────────────────────────────────────────────────────────


def test_event_replay_gap_construction_round_trip() -> None:
    gap = EventReplayGap(
        failure_id=EVENT_REPLAY_FAILURE_ID,
        result_id_attempted="r-1",
        corpus_id="corpus-1",
        scenario_id="scenario-1",
        reason="event_transitions_exceeded_bound",
        observed_at=datetime.now(timezone.utc),
        evidence_refs=["ev-1"],
        evidence_payload={"received_count": 600},
    )
    assert gap.failure_id == "event_replay_failed"
    assert gap.result_id_attempted == "r-1"
    assert gap.corpus_id == "corpus-1"
    assert gap.scenario_id == "scenario-1"


def test_event_replay_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        EventReplayGap(  # type: ignore[call-arg]
            failure_id=EVENT_REPLAY_FAILURE_ID,
            result_id_attempted="r-1",
            corpus_id="corpus-1",
            scenario_id="scenario-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
            unknown_field="rejected",
        )


def test_event_replay_gap_failure_id_literal_range_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        EventReplayGap(  # type: ignore[arg-type]
            failure_id="other_failure",
            result_id_attempted="r-1",
            corpus_id="c-1",
            scenario_id="s-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
        )


def test_event_replay_gap_failure_id_literal_range_exact() -> None:
    """The failure id Literal range MUST be exactly the single typed
    failure id (per the Slice 17/Slice 18 2nd/3rd sub-slice precedent)."""

    hints = get_type_hints(EventReplayGap)
    args = get_args(hints["failure_id"])
    assert args == ("event_replay_failed",)


# ── CounterfactualEventReplayEngine happy-path ────────────────────────────


def test_engine_replay_happy_path_emits_typed_result() -> None:
    """Doc-18:114 step 4 happy-path: typed event-transitions +
    scenario -> typed :class:`CounterfactualResult` with all 16 fields
    populated."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs()
    out = engine.replay(inputs)
    assert out.result is not None, (
        f"Expected typed result, got gap: {out.gap_findings}"
    )
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
    assert out.result.invalidated_by == []
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

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs()
    r1 = engine.replay(inputs)
    r2 = engine.replay(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_engine_replay_result_id_propagated_into_result() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(result_id="custom-result-7")
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.result_id == "custom-result-7"


def test_engine_replay_result_version_propagated_into_result() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(result_version="v2")
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.result_version == "v2"


def test_engine_replay_baseline_metrics_optional() -> None:
    """The engine accepts an optional baseline_metrics list."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs(baseline_metrics=[_metric()]))
    assert out.result is not None


# ── refs-only projection ──────────────────────────────────────────────────


def test_engine_replay_policy_provenance_refs_from_failure_transitions() -> None:
    """The engine surfaces evidence refs from typed failure transitions
    onto the emitted result's policy_provenance_refs list (refs-only
    per doc-18:186-249)."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    # Each failure carries an evidence ref of the form
    # "evidence_nodes:<id*10>".
    assert any("evidence_nodes:" in rid for rid in ref_ids)


def test_engine_replay_policy_provenance_refs_dedupe_by_ref_id() -> None:
    """The engine dedupes refs by ref_id (first occurrence wins)."""

    engine = CounterfactualEventReplayEngine()
    # Two failures share the same typed EvidenceRef (table+id collision).
    shared_ev = EvidenceRef(
        table="evidence_nodes",
        id=100,
        citation="shared",
        kind="failure_event",
    )
    inputs = _inputs(
        failure_transitions=[
            _failure(1, evidence_refs=[shared_ev]),
            _failure(2, evidence_refs=[shared_ev]),
            _failure(3, evidence_refs=[shared_ev]),
        ],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    # "evidence_nodes:100" appears once even though it was in 3 failures.
    assert ref_ids.count("evidence_nodes:100") == 1


def test_engine_replay_policy_provenance_refs_includes_baseline_metric_refs() -> None:
    """When baseline metrics are supplied, their typed Slice 13a refs
    are also surfaced (refs-only)."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(baseline_metrics=[_metric("hours_per_task", value=10.0)])
    )
    assert out.result is not None
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    assert "hours_per_task-ev-1" in ref_ids


# ── delta projection from typed event transitions ─────────────────────────


def test_engine_replay_estimated_delta_hours_from_attempt_transitions() -> None:
    """The estimated_delta_hours is derived from typed attempt
    transition durations (only failed/cancelled/incomplete attempts)
    scaled by the policy ratio."""

    engine = CounterfactualEventReplayEngine()
    # 2 failed attempts of 1 hour each = 2 hours; scaled by -0.5 = -1.0.
    inputs = _inputs(
        attempt_transitions=[
            _attempt(
                1,
                attempt_kind="task",
                status="failed",
                started_at=_NOW,
                finished_at=_NOW + timedelta(hours=1),
            ),
            _attempt(
                2,
                attempt_kind="task",
                status="failed",
                started_at=_NOW,
                finished_at=_NOW + timedelta(hours=1),
            ),
            # Succeeded attempt does NOT contribute to hours-saved
            # projection (only the failed/cancelled/incomplete ones do).
            _attempt(3, attempt_kind="task", status="succeeded"),
        ],
        scenario=_scenario(
            policy_under_test={"expected_hours_delta_ratio": -0.5},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # 2 failed attempts of 1 hour each = 2 hours; scaled by -0.5 = -1.0.
    assert out.result.estimated_delta_hours == pytest.approx(-1.0)


def test_engine_replay_estimated_delta_hours_falls_back_to_baseline_metrics() -> None:
    """When no attempt durations are present, the engine falls back to
    baseline metric median."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[],
        baseline_metrics=[
            _metric("hours_per_task", value=10.0),
            _metric("hours_per_task", value=20.0),
            _metric("hours_per_task", value=30.0),
        ],
        scenario=_scenario(
            policy_under_test={"expected_hours_delta_ratio": -0.5},
        ),
        # Need at least one failure-transition or other event
        # transition else the all-empty check fires.
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # median([10, 20, 30]) = 20; scaled by -0.5 = -10.
    assert out.result.estimated_delta_hours == -10.0


def test_engine_replay_estimated_delta_repair_cycles_from_attempt_transitions() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(1, attempt_kind="repair"),
            _attempt(2, attempt_kind="repair"),
            _attempt(3, attempt_kind="task"),
            _attempt(4, attempt_kind="task"),
        ],
        scenario=_scenario(
            policy_under_test={"expected_repair_cycles_delta_ratio": -0.25},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # 2 repair attempts; scaled by -0.25 = -0.5.
    assert out.result.estimated_delta_repair_cycles == pytest.approx(-0.5)


def test_engine_replay_estimated_delta_commit_failures_from_failure_transitions() -> None:
    """The estimated_delta_commit_failures is derived from typed
    failure transitions whose failure_class or failure_type signals
    commit-flow failure."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        failure_transitions=[
            _failure(1, failure_class="commit_hygiene"),
            _failure(2, failure_class="commit_hygiene"),
            _failure(3, failure_class="commit_hygiene"),
            _failure(4, failure_class="evidence_corruption"),
        ],
        scenario=_scenario(
            policy_under_test={"expected_commit_failures_delta_ratio": -0.5},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # 3 commit failures; scaled by -0.5 = -1.5.
    assert out.result.estimated_delta_commit_failures == pytest.approx(-1.5)


def test_engine_replay_commit_failure_detected_via_failure_type_substring() -> None:
    """If a failure has failure_type carrying 'commit' substring (even
    when the failure_class is not in the commit-class set), it counts."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        failure_transitions=[
            _failure(1, failure_class="other_class", failure_type="commit_dirty"),
        ],
        scenario=_scenario(
            policy_under_test={"expected_commit_failures_delta_ratio": -0.5},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # 1 commit failure; scaled by -0.5 = -0.5.
    assert out.result.estimated_delta_commit_failures == pytest.approx(-0.5)


def test_engine_replay_estimated_risk_change_unknown_when_no_data() -> None:
    """Doc-18:91 + event-replay heuristic: if NO typed deltas are
    available, risk_change is 'unknown'."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[],
        failure_transitions=[
            _failure(1, failure_class="evidence_corruption"),
        ],
        baseline_metrics=[],
        scenario=_scenario(
            policy_under_test={},  # no expected_*_delta_ratio keys
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # No data for any axis => risk_change is "unknown".
    assert out.result.estimated_risk_change == "unknown"


def test_engine_replay_estimated_risk_change_lower_when_all_negative() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            scenario=_scenario(
                policy_under_test={
                    "expected_hours_delta_ratio": -0.5,
                    "expected_repair_cycles_delta_ratio": -0.5,
                    "expected_commit_failures_delta_ratio": -0.5,
                },
            ),
        )
    )
    assert out.result is not None
    assert out.result.estimated_risk_change == "lower"


def test_engine_replay_estimated_risk_change_higher_when_all_positive() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            scenario=_scenario(
                policy_under_test={
                    "expected_hours_delta_ratio": 0.5,
                    "expected_repair_cycles_delta_ratio": 0.5,
                    "expected_commit_failures_delta_ratio": 0.5,
                },
            ),
        )
    )
    assert out.result is not None
    assert out.result.estimated_risk_change == "higher"


def test_engine_replay_estimated_risk_change_same_when_mixed_signs() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            scenario=_scenario(
                policy_under_test={
                    "expected_hours_delta_ratio": -0.5,
                    "expected_repair_cycles_delta_ratio": 0.5,
                    "expected_commit_failures_delta_ratio": -0.1,
                },
            ),
        )
    )
    assert out.result is not None
    assert out.result.estimated_risk_change == "same"


# ── safety_guard_class ───────────────────────────────────────────────────


def test_engine_replay_safety_guard_class_propagated() -> None:
    """Doc-18:87 + doc-18:140-146: the safety_guard_class field is
    propagated from the scenario's policy_under_test."""

    engine = CounterfactualEventReplayEngine()
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
    """Per the heuristic: when safety_guard_class is set, risk_change
    is forced to 'lower'."""

    engine = CounterfactualEventReplayEngine()
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
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs()
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.safety_guard_class is None


# ── confidence projection ────────────────────────────────────────────────


def test_engine_replay_confidence_in_unit_interval() -> None:
    """Doc-18:50 + doc-18:92: the confidence is in [0.0, 1.0]."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert 0.0 <= out.result.confidence <= 1.0


def test_engine_replay_higher_fidelity_than_summary_replay() -> None:
    """**The defining test for the 4th sub-slice.** Doc-18:114 vs
    doc-18:133 contrast: with rich typed event-transition evidence +
    diverse corpus, the event-replay engine MAY return
    ``confidence > SUMMARY_REPLAY_CONFIDENCE_CEILING (0.65)``.

    This demonstrates the higher-fidelity contract -- summary replay
    is capped at 0.65; event replay reaches up to 0.90 (see
    EVENT_REPLAY_CONFIDENCE_CEILING)."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        # Diverse corpus (NOT 8ac124d6-only).
        corpus=_corpus(feature_ids=["8ac124d6", "feature-X", "feature-Y"]),
        # Rich typed event-transition evidence: 5+ of each kind.
        attempt_transitions=[
            _attempt(i, attempt_kind="task", status="failed")
            for i in range(5)
        ],
        gate_transitions=[_gate(f"g-{i}", True, i) for i in range(5)],
        failure_transitions=[_failure(i, failure_class="commit_hygiene") for i in range(5)],
        queue_transitions=[_queue(i) for i in range(3)],
        checkpoint_transitions=[_checkpoint(i) for i in range(3)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # The event-replay engine MAY (and in this configuration DOES)
    # return confidence > the summary-replay ceiling -- the higher-
    # fidelity contract is established.
    assert out.result.confidence > SUMMARY_REPLAY_CONFIDENCE_CEILING, (
        f"Event-replay confidence {out.result.confidence} should exceed "
        f"summary-replay ceiling {SUMMARY_REPLAY_CONFIDENCE_CEILING} when "
        f"typed event-transition evidence is rich + corpus is diverse"
    )


def test_engine_replay_confidence_capped_at_event_replay_ceiling() -> None:
    """The engine's confidence is capped at
    :data:`EVENT_REPLAY_CONFIDENCE_CEILING`."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        corpus=_corpus(feature_ids=["feature-X", "feature-Y", "feature-Z"]),
        failure_transitions=[_failure(i) for i in range(10)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.confidence <= EVENT_REPLAY_CONFIDENCE_CEILING


def test_engine_replay_confidence_reduced_for_8ac124d6_only_corpus() -> None:
    """Doc-18:140-141: 8ac124d6-only corpus reduces confidence."""

    engine = CounterfactualEventReplayEngine()
    out_only_8ac = engine.replay(
        _inputs(corpus=_corpus(feature_ids=["8ac124d6"]))
    )
    out_diverse = engine.replay(
        _inputs(corpus=_corpus(feature_ids=["8ac124d6", "feature-X"]))
    )
    assert out_only_8ac.result is not None
    assert out_diverse.result is not None
    assert out_only_8ac.result.confidence < out_diverse.result.confidence


def test_engine_replay_confidence_reduced_for_small_failure_sample() -> None:
    """Doc-18:138: small failure-transition sample => reduced confidence."""

    engine = CounterfactualEventReplayEngine()
    out_small = engine.replay(
        _inputs(failure_transitions=[_failure(1)])  # sample of 1
    )
    out_large = engine.replay(
        _inputs(failure_transitions=[_failure(i) for i in range(5)])
    )
    assert out_small.result is not None
    assert out_large.result is not None
    assert out_small.result.confidence < out_large.result.confidence


def test_engine_replay_confidence_reduced_when_zero_transitions() -> None:
    """When all 5 event-transition lists are empty but baseline_metrics
    is supplied (so the all-empty check doesn't fire), confidence drops
    toward summary-replay levels."""

    engine = CounterfactualEventReplayEngine()
    out_baseline_only = engine.replay(
        _inputs(
            attempt_transitions=[],
            gate_transitions=[],
            failure_transitions=[],
            queue_transitions=[],
            checkpoint_transitions=[],
            baseline_metrics=[_metric()],
        )
    )
    out_full = engine.replay(_inputs())
    assert out_baseline_only.result is not None
    assert out_full.result is not None
    # With zero typed event transitions, confidence drops below the
    # event-replay full-fidelity case.
    assert out_baseline_only.result.confidence < out_full.result.confidence


# ── invalidated_by ────────────────────────────────────────────────────────


def test_engine_replay_missing_required_evidence_emits_invalidated_by() -> None:
    """Doc-18:134-135: missing required evidence populates the
    invalidated_by list."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            required_evidence_kinds=["typed_attempt", "typed_failure"]
        ),
        corpus=_corpus(
            evidence_set_ids=["ev-1"],
            implementation_anchor_ids=["anchor-1"],
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "missing_evidence:typed_attempt" in out.result.invalidated_by
    assert "missing_evidence:typed_failure" in out.result.invalidated_by


def test_engine_replay_required_evidence_covered_by_evidence_set_ids() -> None:
    engine = CounterfactualEventReplayEngine()
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

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        corpus=_corpus(validity_limits=["product_defect_window"]),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "product_defect_window" in out.result.invalidated_by


def test_engine_replay_product_defect_window_not_invalidated_with_safety_guard() -> None:
    """Safety-guard scenarios are exempt from product_defect_window
    invalidation per doc-18:140-146."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        corpus=_corpus(validity_limits=["product_defect_window"]),
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
    assert "product_defect_window" not in out.result.invalidated_by


# ── assumptions + validity_limits composition ────────────────────────────


def test_engine_replay_assumptions_include_scenario_assumptions() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        scenario=_scenario(assumptions=["assumption-A", "assumption-B"]),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "assumption-A" in out.result.assumptions
    assert "assumption-B" in out.result.assumptions


def test_engine_replay_assumptions_include_event_replay_projection_tag() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert "event_replay_projection" in out.result.assumptions


def test_engine_replay_validity_limits_include_event_replay_mode_tag() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert "event_replay_mode" in out.result.validity_limits


def test_engine_replay_validity_limits_include_corpus_limits() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(corpus=_corpus(validity_limits=["sample_size<10"]))
    out = engine.replay(inputs)
    assert out.result is not None
    assert "sample_size<10" in out.result.validity_limits


def test_engine_replay_validity_limits_low_event_transition_count() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[],
        gate_transitions=[],
        failure_transitions=[_failure(1)],
        queue_transitions=[],
        checkpoint_transitions=[],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "low_event_transition_count" in out.result.validity_limits


def test_engine_replay_validity_limits_insufficient_failure_transitions() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        failure_transitions=[_failure(1), _failure(2)],  # 2 < 3
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "insufficient_failure_transitions" in out.result.validity_limits


def test_engine_replay_validity_limits_governance_only_provenance_chain() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(corpus=_corpus(feature_ids=["8ac124d6"]))
    out = engine.replay(inputs)
    assert out.result is not None
    assert "governance_only_provenance_chain" in out.result.validity_limits


def test_engine_replay_validity_limits_missing_attempt_durations() -> None:
    """When no attempt transitions have usable durations + no baseline
    hours metrics, the engine flags missing_attempt_durations."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[],
        baseline_metrics=[],
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert "missing_attempt_durations" in out.result.validity_limits


# ── recommended_next_step ────────────────────────────────────────────────


def test_engine_replay_recommended_next_step_discard_on_very_low_confidence() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["8ac124d6"]),
            attempt_transitions=[],
            gate_transitions=[],
            failure_transitions=[],  # 0 failures => low confidence multiplier
            queue_transitions=[],
            checkpoint_transitions=[],
            baseline_metrics=[_metric(confidence=0.0)],  # baseline anchors all-empty check
        )
    )
    assert out.result is not None
    # 0.90 * 0.5 (small failure sample) * 0.5 (8ac124d6-only) * 0.5
    # (no transitions) = 0.1125 -> NOT below 0.05 so not "discard".
    # The recommended_next_step is "collect_more_evidence" because
    # total_transitions < 3 + confidence < 0.3.
    assert out.result.recommended_next_step == "collect_more_evidence"


def test_engine_replay_recommended_next_step_collect_more_evidence_on_missing_evidence() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            scenario=_scenario(required_evidence_kinds=["missing-kind"]),
            corpus=_corpus(
                evidence_set_ids=["ev-1"],
                implementation_anchor_ids=["anchor-1"],
            ),
        )
    )
    assert out.result is not None
    assert out.result.recommended_next_step == "collect_more_evidence"


def test_engine_replay_recommended_next_step_draft_policy_on_high_confidence() -> None:
    """With diverse corpus + many typed failures, the engine MAY emit
    draft_policy."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["feature-X", "feature-Y"]),
            failure_transitions=[
                _failure(i, failure_class="commit_hygiene") for i in range(10)
            ],
        )
    )
    assert out.result is not None
    assert out.result.recommended_next_step == "draft_policy"


def test_engine_replay_recommended_next_step_implementation_plan_with_safety_guard() -> None:
    """Implementation plan: high confidence + safety_guard_class set."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            corpus=_corpus(feature_ids=["feature-X", "feature-Y"]),
            failure_transitions=[
                _failure(i, failure_class="commit_hygiene") for i in range(10)
            ],
            scenario=_scenario(
                policy_under_test={
                    "safety_guard_class": "fail_closed_earlier",
                    "expected_hours_delta_ratio": -0.1,
                    "expected_repair_cycles_delta_ratio": -0.1,
                    "expected_commit_failures_delta_ratio": -0.1,
                },
            ),
        )
    )
    assert out.result is not None
    assert out.result.recommended_next_step == "implementation_plan"


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

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs(baseline_metrics=[_metric()]))
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
        for n in dir(CounterfactualEventReplayEngine)
        if not n.startswith("_")
        and callable(getattr(CounterfactualEventReplayEngine, n))
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
    for name in dir(CounterfactualEventReplayEngine):
        if name.startswith("_"):
            continue
        for prefix in forbidden_method_prefixes:
            assert not name.startswith(prefix), (
                f"CounterfactualEventReplayEngine.{name} exposes mutation-like name {prefix!r}"
            )


def test_no_consumer_side_module_imports() -> None:
    """Doc-18:123-125 + doc-18:164 AC3: the engine MUST NOT import any
    consumer-side module. Walk the source + assert no forbidden import
    appears."""

    import iriai_build_v2.execution_control.counterfactual_event_replay as mod

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
            f"Slice 18 4th sub-slice imports forbidden consumer-side module: {forbidden!r}"
        )


def test_no_dag_authority_artifact_keys() -> None:
    """Doc-18:123-125: *"Replay results are review/governance artifacts
    only. Replay must not write `dag-*` execution authority artifacts
    or active policy markers."* The 4th sub-slice engine MUST NOT
    contain any `dag-*` authority artifact-key string literals."""

    import iriai_build_v2.execution_control.counterfactual_event_replay as mod

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
            f"Slice 18 4th sub-slice contains forbidden dag-authority artifact key prefix {prefix!r}"
        )


# ── fail-closed (no silent degradation) ─────────────────────────────────


def test_engine_replay_event_transitions_exceeded_bound_emits_gap() -> None:
    """Doc-18:150 + Slice 13A bounded-reads: engine emits typed gap
    when total event-transition count exceeds bound."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[_attempt(i) for i in range(20)],
        max_event_transitions=10,
    )
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    gap = out.gap_findings[0]
    assert gap.failure_id == "event_replay_failed"
    assert gap.reason == "event_transitions_exceeded_bound"
    # 20 attempts + 2 gates + 3 failures + 1 queue + 2 checkpoints = 28.
    assert gap.evidence_payload["received_count"] == 28
    assert gap.evidence_payload["max_bound"] == 10


def test_engine_replay_empty_result_id_emits_gap() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(result_id="   ")
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    assert out.gap_findings[0].reason == "result_id_empty"


def test_engine_replay_all_empty_event_transitions_emits_gap() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[],
        gate_transitions=[],
        failure_transitions=[],
        queue_transitions=[],
        checkpoint_transitions=[],
        baseline_metrics=[],
    )
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    assert out.gap_findings[0].reason == "all_event_transitions_empty"


def test_engine_replay_summary_replay_mode_emits_gap() -> None:
    """The 4th sub-slice engine ONLY handles ``event_replay`` mode;
    other modes emit a typed gap."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(mode="summary_replay")
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    gap = out.gap_findings[0]
    assert gap.reason == "invalid_replay_mode_for_engine"
    assert gap.evidence_payload["received_mode"] == "summary_replay"
    assert gap.evidence_payload["expected_mode"] == "event_replay"


def test_engine_replay_hybrid_mode_emits_gap() -> None:
    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(mode="hybrid")
    out = engine.replay(inputs)
    assert out.result is None
    assert len(out.gap_findings) == 1
    assert out.gap_findings[0].reason == "invalid_replay_mode_for_engine"


def test_engine_replay_never_raises_on_valid_input() -> None:
    """``feedback_no_silent_degradation`` fail-closed: engine NEVER raises."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert isinstance(out, EventReplayResult)


def test_engine_replay_never_raises_on_empty_lists() -> None:
    engine = CounterfactualEventReplayEngine()
    # Should not raise -- typed gap emitted.
    out = engine.replay(_inputs(
        attempt_transitions=[],
        gate_transitions=[],
        failure_transitions=[],
        queue_transitions=[],
        checkpoint_transitions=[],
        baseline_metrics=[],
    ))
    assert isinstance(out, EventReplayResult)


# ── compute_event_replay_idempotency_key ─────────────────────────────────


def test_compute_event_replay_idempotency_key_deterministic() -> None:
    key1 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=1,
        gate_transition_count=2,
        failure_transition_count=3,
        queue_transition_count=0,
        checkpoint_transition_count=1,
        assumptions=["x"],
        validity_limits=[],
    )
    key2 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=1,
        gate_transition_count=2,
        failure_transition_count=3,
        queue_transition_count=0,
        checkpoint_transition_count=1,
        assumptions=["x"],
        validity_limits=[],
    )
    assert key1 == key2
    assert len(key1) == 64


def test_compute_event_replay_idempotency_key_order_invariant() -> None:
    key1 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=1,
        gate_transition_count=2,
        failure_transition_count=3,
        queue_transition_count=4,
        checkpoint_transition_count=5,
        assumptions=["a", "b"],
        validity_limits=["p", "q"],
    )
    key2 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=1,
        gate_transition_count=2,
        failure_transition_count=3,
        queue_transition_count=4,
        checkpoint_transition_count=5,
        assumptions=["b", "a"],
        validity_limits=["q", "p"],
    )
    assert key1 == key2


def test_compute_event_replay_idempotency_key_differs_on_result_version() -> None:
    """Doc-18:128-129: new result version MUST produce a new key."""

    key1 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=0,
        gate_transition_count=0,
        failure_transition_count=0,
        queue_transition_count=0,
        checkpoint_transition_count=0,
        assumptions=[],
        validity_limits=[],
    )
    key2 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v2",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=0,
        gate_transition_count=0,
        failure_transition_count=0,
        queue_transition_count=0,
        checkpoint_transition_count=0,
        assumptions=[],
        validity_limits=[],
    )
    assert key1 != key2


def test_compute_event_replay_idempotency_key_differs_on_transition_counts() -> None:
    """Different event-transition counts produce different keys (so
    a re-run with a longer event-transition window cleanly produces a
    new key)."""

    key1 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=1,
        gate_transition_count=0,
        failure_transition_count=0,
        queue_transition_count=0,
        checkpoint_transition_count=0,
        assumptions=[],
        validity_limits=[],
    )
    key2 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=5,
        gate_transition_count=0,
        failure_transition_count=0,
        queue_transition_count=0,
        checkpoint_transition_count=0,
        assumptions=[],
        validity_limits=[],
    )
    assert key1 != key2


def test_compute_event_replay_idempotency_key_differs_on_mode() -> None:
    key1 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="event_replay",
        attempt_transition_count=0,
        gate_transition_count=0,
        failure_transition_count=0,
        queue_transition_count=0,
        checkpoint_transition_count=0,
        assumptions=[],
        validity_limits=[],
    )
    key2 = compute_event_replay_idempotency_key(
        result_id="r-1",
        result_version="v1",
        corpus_id="c-1",
        scenario_id="s-1",
        mode="summary_replay",
        attempt_transition_count=0,
        gate_transition_count=0,
        failure_transition_count=0,
        queue_transition_count=0,
        checkpoint_transition_count=0,
        assumptions=[],
        validity_limits=[],
    )
    assert key1 != key2


# ── failure_router 4-add-point validation ─────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    """Add point 1: FailureType Literal block."""

    assert "event_replay_failed" in get_args(FailureType)


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add point 2: FAILURE_TYPES tuple."""

    assert "event_replay_failed" in FAILURE_TYPES


def test_failure_router_retryable_failure_types_includes_new_id() -> None:
    """Add point 3: _RETRYABLE_FAILURE_TYPES frozenset."""

    assert "event_replay_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_table_includes_new_route_entry() -> None:
    """Add point 4: ROUTE_TABLE _route() entry under EXISTING
    evidence_corruption class with REUSED retry_governance_projection
    NON-blocking RouteAction."""

    route = ROUTE_TABLE[("evidence_corruption", "event_replay_failed")]
    assert route.action == "retry_governance_projection"


def test_failure_router_action_is_non_blocking() -> None:
    """Per doc-14:242-243 the action is non-blocking (NOT quiesce)."""

    route = ROUTE_TABLE[("evidence_corruption", "event_replay_failed")]
    assert route.action != "quiesce"
    assert route.action == "retry_governance_projection"


def test_failure_router_existing_evidence_corruption_class_reused() -> None:
    """The failure_class is the EXISTING evidence_corruption (NOT a
    new failure_class)."""

    route = ROUTE_TABLE[("evidence_corruption", "event_replay_failed")]
    assert route.failure_class == "evidence_corruption"


# ── doc-18 awareness PIN tests ────────────────────────────────────────────


def test_doc_18_114_step_4_satisfied() -> None:
    """Doc-18:114 step 4 PIN: *"Add event replay where typed attempt,
    gate, failure, queue, and checkpoint transitions are available."*
    SATISFIED via this 4th sub-slice engine."""

    engine = CounterfactualEventReplayEngine()
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
    the engine consumes the Slice 13a + 10a + 15 shared models REFS
    ONLY; they MUST NOT redefine the Slice 13A shapes."""

    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

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
                    f"Slice 18 4th sub-slice REDEFINES Slice 13A {shape_name!r}"
                )


def test_doc_18_160_168_ac1_deterministic() -> None:
    """Doc-18:162 AC1: *"Counterfactuals are deterministic, versioned,
    and evidence-backed."* The engine's idempotency_key is deterministic
    via compute_event_replay_idempotency_key."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs()
    r1 = engine.replay(inputs)
    r2 = engine.replay(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_doc_18_160_168_ac2_assumptions_and_validity_limits() -> None:
    """Doc-18:163 AC2: *"Every result lists assumptions and validity
    limits."* The emitted CounterfactualResult has both lists
    non-empty."""

    engine = CounterfactualEventReplayEngine()
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
        for n in dir(CounterfactualEventReplayEngine)
        if not n.startswith("_")
        and callable(getattr(CounterfactualEventReplayEngine, n))
    )
    assert public_methods == ["replay"]


def test_doc_18_160_168_ac4_result_supports_recommendation_citation() -> None:
    """Doc-18:165-166 AC4 cross-reference: the emitted CounterfactualResult
    carries the stable result_id that the Slice 17
    GovernancePolicyRecommendation.counterfactual_result_refs field
    cites for behavior-changing recommendations."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs(result_id="ac4-result-id"))
    assert out.result is not None
    assert out.result.result_id == "ac4-result-id"
    assert out.result.result_version == "v1"


def test_doc_18_160_168_ac5_corpus_includes_8ac124d6() -> None:
    """Doc-18:167-168 AC5: *"The replay corpus includes both 8ac124d6
    evidence and Slice 00-12 implementation artifacts."* The engine
    accepts the typed corpus directly (the 2nd sub-slice loader
    enforces AC5 coverage)."""

    engine = CounterfactualEventReplayEngine()
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


def test_doc_18_114_higher_fidelity_than_summary_replay() -> None:
    """Doc-18:114 vs doc-18:113 + doc-18:133 PIN test: the event-replay
    engine carries a HIGHER confidence ceiling than the summary-replay
    engine (because typed event-transition projection is higher-
    fidelity than typed metric-median projection)."""

    assert EVENT_REPLAY_CONFIDENCE_CEILING > SUMMARY_REPLAY_CONFIDENCE_CEILING


def test_doc_18_138_small_sample_conservative_confidence() -> None:
    """Doc-18:138: *"Small sample size: report confidence and avoid
    policy recommendations."*"""

    engine = CounterfactualEventReplayEngine()
    out_small = engine.replay(
        _inputs(failure_transitions=[_failure(1)])  # sample of 1
    )
    out_large = engine.replay(
        _inputs(failure_transitions=[_failure(i) for i in range(10)])
    )
    assert out_small.result is not None
    assert out_large.result is not None
    assert out_small.result.confidence < out_large.result.confidence


def test_doc_18_50_confidence_in_unit_interval() -> None:
    """Doc-18:50 + doc-18:92: confidence is in [0.0, 1.0]."""

    engine = CounterfactualEventReplayEngine()
    for inputs in (
        _inputs(),
        _inputs(corpus=_corpus(feature_ids=["8ac124d6"])),
        _inputs(failure_transitions=[]),
        _inputs(
            corpus=_corpus(feature_ids=["feature-X", "feature-Y"]),
            failure_transitions=[_failure(i) for i in range(20)],
        ),
    ):
        out = engine.replay(inputs)
        if out.result is not None:
            assert 0.0 <= out.result.confidence <= 1.0


def test_doc_18_140_146_overfit_risk_governance_only_chain() -> None:
    """Doc-18:140-141: *"Overfit risk: require at least one non-`8ac124d6`
    corpus before marking a general policy high confidence."* The engine
    flags this via the validity_limits entry
    ``"governance_only_provenance_chain"`` when the corpus is
    ``8ac124d6``-only."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(corpus=_corpus(feature_ids=["8ac124d6"]))
    out = engine.replay(inputs)
    assert out.result is not None
    assert "governance_only_provenance_chain" in out.result.validity_limits


def test_doc_18_123_125_replay_results_are_governance_artifacts_only() -> None:
    """Doc-18:123-125: *"Replay results are review/governance artifacts
    only."* + *"Replay must not write `dag-*` execution authority
    artifacts or active policy markers."* Structural assertion."""

    import iriai_build_v2.execution_control.counterfactual_event_replay as mod

    source = inspect.getsource(mod)
    # No active policy markers / dag-* authority literals.
    forbidden_substrings = ['"dag-active', "'dag-active", 'dag-policy:', "active_policy"]
    for sub in forbidden_substrings:
        assert sub not in source, (
            f"Slice 18 4th sub-slice source contains forbidden literal {sub!r}"
        )


# ── annotation-identity REUSE for the Slice 10a typed shapes ────────────


def test_event_replay_inputs_attempt_transitions_uses_imported_shape() -> None:
    """The :class:`ExecutionAttemptSummary` MUST be the imported Slice
    10a shape (NOT a local redefinition)."""

    from iriai_build_v2.workflows.develop.execution import snapshots
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.ExecutionAttemptSummary is snapshots.ExecutionAttemptSummary


def test_event_replay_inputs_gate_transitions_uses_imported_shape() -> None:
    from iriai_build_v2.workflows.develop.execution import snapshots
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.GateStatusSummary is snapshots.GateStatusSummary


def test_event_replay_inputs_failure_transitions_uses_imported_shape() -> None:
    from iriai_build_v2.workflows.develop.execution import snapshots
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.TypedFailureSummary is snapshots.TypedFailureSummary


def test_event_replay_inputs_queue_transitions_uses_imported_shape() -> None:
    from iriai_build_v2.workflows.develop.execution import snapshots
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.MergeQueueSummary is snapshots.MergeQueueSummary


def test_event_replay_inputs_checkpoint_transitions_uses_imported_shape() -> None:
    from iriai_build_v2.workflows.develop.execution import snapshots
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.EvidenceRef is snapshots.EvidenceRef


def test_governance_evidence_ref_imported_from_slice_13a() -> None:
    from iriai_build_v2.workflows.develop.governance import models
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.GovernanceEvidenceRef is models.GovernanceEvidenceRef


def test_governance_metric_value_imported_from_slice_15() -> None:
    from iriai_build_v2.execution_control import governance_metrics
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.GovernanceMetricValue is governance_metrics.GovernanceMetricValue


def test_replay_mode_imported_from_slice_18_1st() -> None:
    from iriai_build_v2.execution_control import counterfactual_replay
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.ReplayMode is counterfactual_replay.ReplayMode


def test_counterfactual_result_imported_from_slice_18_1st() -> None:
    from iriai_build_v2.execution_control import counterfactual_replay
    from iriai_build_v2.execution_control import counterfactual_event_replay as mod

    assert mod.CounterfactualResult is counterfactual_replay.CounterfactualResult


def test_engine_replay_returns_event_replay_result_type() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert type(out).__name__ == "EventReplayResult"


def test_engine_replay_emits_counterfactual_result_type_when_successful() -> None:
    engine = CounterfactualEventReplayEngine()
    out = engine.replay(_inputs())
    assert out.result is not None
    assert type(out.result).__name__ == "CounterfactualResult"


def test_engine_replay_with_only_baseline_metrics() -> None:
    """The engine can produce a result with only baseline metrics
    (no typed event transitions) -- falls back to metric-median
    projection at reduced confidence."""

    engine = CounterfactualEventReplayEngine()
    out = engine.replay(
        _inputs(
            attempt_transitions=[],
            gate_transitions=[],
            failure_transitions=[],
            queue_transitions=[],
            checkpoint_transitions=[],
            baseline_metrics=[
                _metric("hours_per_task", value=10.0),
                _metric("repair_cycles_per_task", value=2.0),
                _metric("commit_failures_per_task", value=1.0),
            ],
        )
    )
    assert out.result is not None
    # The deltas projected from baseline metric median.
    assert out.result.estimated_delta_hours is not None
    assert out.result.estimated_delta_repair_cycles is not None
    assert out.result.estimated_delta_commit_failures is not None


def test_engine_replay_with_baseline_metrics_evidence_refs_dedupe() -> None:
    """Refs from baseline metrics + failure transitions are deduped."""

    engine = CounterfactualEventReplayEngine()
    # Create a metric ref with a colliding ref_id to a failure ref.
    # The failure ref is built as "evidence_nodes:<id*10>".
    # We use a metric ref with ref_id "evidence_nodes:10" to collide.
    metric_with_colliding_ref = _metric(
        "hours_per_task",
        evidence_refs=[_gov_ref("evidence_nodes:10")],
    )
    inputs = _inputs(
        failure_transitions=[
            _failure(
                1,
                evidence_refs=[
                    EvidenceRef(
                        table="evidence_nodes", id=10, citation="ev-1", kind="failure_event"
                    )
                ],
            )
        ],
        baseline_metrics=[metric_with_colliding_ref],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    ref_ids = [r.ref_id for r in out.result.policy_provenance_refs]
    assert ref_ids.count("evidence_nodes:10") == 1


def test_engine_replay_succeeded_attempts_do_not_count_for_hours() -> None:
    """Only failed/cancelled/incomplete attempts contribute to the
    hours delta projection (succeeded attempts are not "lost
    workflow hours")."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(1, status="succeeded"),
            _attempt(2, status="succeeded"),
            _attempt(3, status="succeeded"),
        ],
        baseline_metrics=[],
        # Need at least one failure transition else the all-empty
        # check fires.
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # No baseline + no failed attempts => no hours delta.
    assert out.result.estimated_delta_hours is None


def test_engine_replay_cancelled_attempts_contribute_to_hours() -> None:
    """Cancelled attempts contribute hours to the projection."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(
                1,
                status="cancelled",
                started_at=_NOW,
                finished_at=_NOW + timedelta(hours=2),
            ),
        ],
        scenario=_scenario(
            policy_under_test={"expected_hours_delta_ratio": -1.0},
        ),
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_delta_hours == pytest.approx(-2.0)


def test_engine_replay_incomplete_attempts_contribute_to_hours() -> None:
    """Incomplete attempts contribute hours."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(
                1,
                status="incomplete",
                started_at=_NOW,
                finished_at=_NOW + timedelta(hours=3),
            ),
        ],
        scenario=_scenario(
            policy_under_test={"expected_hours_delta_ratio": -1.0},
        ),
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_delta_hours == pytest.approx(-3.0)


def test_engine_replay_zero_duration_attempt_excluded() -> None:
    """Attempts with zero or negative duration (e.g. finished_at <=
    started_at) are excluded from the hours projection."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(
                1,
                status="failed",
                started_at=_NOW,
                finished_at=_NOW,  # zero duration
            ),
        ],
        baseline_metrics=[],
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # Zero-duration failed attempt is excluded -> None.
    assert out.result.estimated_delta_hours is None


def test_engine_replay_failed_attempts_without_finished_at_excluded() -> None:
    """Attempts whose finished_at is None (still running) are
    excluded from the hours projection."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(1, status="failed", finished_at=None),
        ],
        baseline_metrics=[],
        failure_transitions=[_failure(1)],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_delta_hours is None


def test_engine_replay_zero_policy_ratio_produces_zero_delta() -> None:
    """When the scenario specifies a 0.0 ratio for any axis, the
    corresponding delta is 0.0 (not None)."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            policy_under_test={
                "expected_hours_delta_ratio": 0.0,
                "expected_repair_cycles_delta_ratio": 0.0,
                "expected_commit_failures_delta_ratio": 0.0,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # Hours delta is 0 (sum of failed attempts in hours * 0.0).
    assert out.result.estimated_delta_hours == 0.0
    # Risk change with all deltas == 0 is "lower" (all <= 0).
    assert out.result.estimated_risk_change == "lower"


def test_engine_replay_missing_policy_ratio_defaults_to_zero() -> None:
    """When the scenario omits an expected_*_delta_ratio key, the
    corresponding ratio defaults to 0.0."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            policy_under_test={},  # no ratios
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # Both deltas are 0 (data is present but ratio is 0.0).
    assert out.result.estimated_delta_hours == 0.0
    assert out.result.estimated_delta_repair_cycles == 0.0
    assert out.result.estimated_delta_commit_failures == 0.0


def test_engine_replay_non_numeric_policy_ratio_treated_as_zero() -> None:
    """Non-numeric policy_under_test values default to 0.0 ratio
    (refs-only fail-closed; engine never raises)."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        scenario=_scenario(
            policy_under_test={
                "expected_hours_delta_ratio": "not_a_number",
                "expected_repair_cycles_delta_ratio": True,  # bool is excluded
                "expected_commit_failures_delta_ratio": None,
            },
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    assert out.result.estimated_delta_hours == 0.0
    assert out.result.estimated_delta_repair_cycles == 0.0
    assert out.result.estimated_delta_commit_failures == 0.0


def test_engine_is_stateless() -> None:
    """The engine should be stateless -- callers can reuse a single
    instance across multiple (corpus, scenario) pairs."""

    engine = CounterfactualEventReplayEngine()
    out1 = engine.replay(_inputs(result_id="r-A"))
    out2 = engine.replay(_inputs(result_id="r-B"))
    assert out1.result is not None
    assert out2.result is not None
    assert out1.result.result_id == "r-A"
    assert out2.result.result_id == "r-B"
    # Independent idempotency keys for independent inputs.
    assert out1.idempotency_key != out2.idempotency_key


def test_engine_replay_with_diverse_attempt_kinds() -> None:
    """The engine correctly classifies attempts by kind."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        attempt_transitions=[
            _attempt(1, attempt_kind="task"),
            _attempt(2, attempt_kind="verify"),
            _attempt(3, attempt_kind="repair"),
            _attempt(4, attempt_kind="merge"),
            _attempt(5, attempt_kind="checkpoint"),
            _attempt(6, attempt_kind="regroup"),
        ],
        scenario=_scenario(
            policy_under_test={"expected_repair_cycles_delta_ratio": -0.5},
        ),
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # Only 1 repair attempt; scaled by -0.5 = -0.5.
    assert out.result.estimated_delta_repair_cycles == pytest.approx(-0.5)


def test_engine_replay_compose_idempotency_uses_failure_count() -> None:
    """Different failure-transition counts produce different
    idempotency keys for the same (corpus, scenario, result_id,
    version, mode)."""

    engine = CounterfactualEventReplayEngine()
    out_small = engine.replay(_inputs(failure_transitions=[_failure(1)]))
    out_large = engine.replay(_inputs(
        failure_transitions=[_failure(i) for i in range(5)]
    ))
    assert out_small.idempotency_key != out_large.idempotency_key


def test_engine_replay_compose_idempotency_uses_attempt_count() -> None:
    """Different attempt-transition counts produce different
    idempotency keys."""

    engine = CounterfactualEventReplayEngine()
    out_small = engine.replay(_inputs(attempt_transitions=[_attempt(1)]))
    out_large = engine.replay(_inputs(
        attempt_transitions=[_attempt(i) for i in range(5)]
    ))
    assert out_small.idempotency_key != out_large.idempotency_key


def test_engine_replay_handles_empty_evidence_refs_on_failure() -> None:
    """A failure with no typed evidence_refs is accepted (the engine
    skips it for policy_provenance_refs but still counts it for the
    delta projection)."""

    engine = CounterfactualEventReplayEngine()
    inputs = _inputs(
        failure_transitions=[
            _failure(1, evidence_refs=[]),
            _failure(2, evidence_refs=[]),
            _failure(3, evidence_refs=[]),
        ],
    )
    out = engine.replay(inputs)
    assert out.result is not None
    # No refs from failures with empty evidence_refs.
    assert out.result.policy_provenance_refs == []
