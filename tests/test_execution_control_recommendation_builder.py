"""Slice 17 second sub-slice -- unit tests for the recommendation
builder at ``execution_control/recommendation_builder.py``.

Covers the doc-17:168-169 step 2 typed-shape consumer + structural
emitter wiring:

* :class:`RecommendationBuilderInputs` typed inputs BaseModel (extra-
  forbid; defaults).
* :class:`RecommendationBuilderResult` typed result BaseModel (extra-
  forbid; defaults).
* :class:`RecommendationBuilderEmissionGap` typed gap projection
  BaseModel (extra-forbid; failure_id Literal range).
* :class:`RecommendationBuilder.build_recommendations(...)` -- the
  emission method:
  * High-confidence finding -> typed recommendation emitted with
    ``status="draft"`` (per doc-17:166-167; one per consumer).
  * Below-threshold finding -> NO recommendation + idempotency_key
    recorded in ``refused_findings`` (per doc-17:204
    refuse-and-no-emit option a).
  * Conflicting recommendations (same consumer + same scope) -> BOTH
    ``status="draft"`` (per doc-17:194-195).
  * Builder NEVER raises (fail-closed; typed gap projection on
    construction failure per ``feedback_no_silent_degradation``).
  * Per-consumer routing (one test per consumer; verify the right
    consumer-specific BaseModel lands in ``policy_artifact``).
  * 14 of 14 FindingKind routing coverage.
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :class:`GovernanceEvidenceRef` + Slice 16
  :class:`GovernanceFinding` + Slice 17 1st sub-slice
  :class:`GovernancePolicyRecommendation` /
  :class:`PolicyRecommendationDecision` / per-consumer
  ``*PolicyArtifact`` BaseModels.
* ``source_finding_ids`` populated from
  :attr:`GovernanceFinding.idempotency_key` (string list per
  doc-17:80-81).
* ``source_metric_refs`` populated from :attr:`GovernanceFinding.metric_refs`
  string list (per doc-17:81).
* Failure-router 4-add-point validation
  (``recommendation_builder_emission_failed`` registered under
  EXISTING ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` NON-blocking RouteAction; mirrors
  Slice 16 2nd/3rd-A/3rd-B/4th sub-slice precedent).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 1st sub-slice modules + tests remain byte-identical.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.finding_engine import (
    FindingKind,
    GovernanceFinding,
    compute_finding_idempotency_key,
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
from iriai_build_v2.execution_control.recommendation_builder import (
    DEFAULT_MIN_CONFIDENCE_THRESHOLD,
    RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID,
    RecommendationBuilder,
    RecommendationBuilderEmissionGap,
    RecommendationBuilderInputs,
    RecommendationBuilderResult,
    compute_recommendation_id,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    FailureType,
    _RETRYABLE_FAILURE_TYPES,
    _ROUTE_ROWS,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def _ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified :class:`GovernanceEvidenceRef` for tests."""

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-1",
        digest="sha256:bbb",
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def _finding(**overrides: object) -> GovernanceFinding:
    """Construct a fully-specified :class:`GovernanceFinding` for tests.

    Default shape: a workflow-related ``workflow_inefficiency`` finding
    (routes to ``scheduler`` per the default routing table) with
    confidence 0.85 (above the default 0.7 threshold) +
    ``requires_policy_artifact=True``.
    """

    primary = [_ref(ref_id="primary-1", digest="sha256:p1")]
    supporting = [_ref(ref_id="supporting-1", digest="sha256:s1")]
    idempotency_key = compute_finding_idempotency_key(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_digests=["sha256:p1"],
        rule_version="v1",
    )
    base: dict[str, object] = dict(
        idempotency_key=idempotency_key,
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "default"},
        primary_evidence_refs=primary,
        supporting_evidence_refs=supporting,
        implementation_log_anchors=["journal:1:1"],
        metric_refs=["commit_hygiene_loops_per_window"],
        estimated_lost_hours=2.5,
        estimated_retry_impact=0.15,
        recommended_action_display="Review commit hygiene policy.",
        recommendation_draft_ref=None,
        safe_runtime_action=False,
        requires_policy_artifact=True,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
    )
    base.update(overrides)
    return GovernanceFinding(**base)  # type: ignore[arg-type]


def _builder_inputs(**overrides: object) -> RecommendationBuilderInputs:
    """Construct a fully-specified :class:`RecommendationBuilderInputs`."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        findings=[_finding()],
    )
    base.update(overrides)
    return RecommendationBuilderInputs(**base)  # type: ignore[arg-type]


def _gap(**overrides: object) -> RecommendationBuilderEmissionGap:
    """Construct a fully-specified :class:`RecommendationBuilderEmissionGap`."""

    base: dict[str, object] = dict(
        failure_id="recommendation_builder_emission_failed",
        corpus_id="8ac124d6",
        finding_idempotency_key="sha256:finding-1",
        target_consumer="scheduler",
        reason="recommendation_construction_failed",
        evidence_payload={"detail": "ValidationError"},
    )
    base.update(overrides)
    return RecommendationBuilderEmissionGap(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the documented surface exactly.

    Per doc-17:168-169 step 2 the surface is:
    * 1 typed failure id constant (``RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID``).
    * 1 typed minimum-confidence threshold constant
      (``DEFAULT_MIN_CONFIDENCE_THRESHOLD``).
    * 3 typed BaseModels (``RecommendationBuilderInputs`` +
      ``RecommendationBuilderResult`` +
      ``RecommendationBuilderEmissionGap``).
    * 1 pure helper (``compute_recommendation_id``).
    * 1 builder class (``RecommendationBuilder``).

    Total: 7 exported names.
    """

    from iriai_build_v2.execution_control import recommendation_builder as mod

    expected = {
        "RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID",
        "DEFAULT_MIN_CONFIDENCE_THRESHOLD",
        "RecommendationBuilderInputs",
        "RecommendationBuilderResult",
        "RecommendationBuilderEmissionGap",
        "compute_recommendation_id",
        "RecommendationBuilder",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 7
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_slice_13a_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-17:240-289 the Slice 17 module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes the
    Slice 13a shared model via direct import (transitively through the
    Slice 17 1st sub-slice + Slice 16 1st sub-slice typed surfaces).
    """

    from iriai_build_v2.execution_control import recommendation_builder as mod

    # Either the module does NOT re-export GovernanceEvidenceRef OR (if
    # it does, e.g. via an internal import) the re-exported symbol IS
    # the Slice 13a shared class (identity).
    assert getattr(mod, "GovernanceEvidenceRef", None) is None or (
        mod.GovernanceEvidenceRef is GovernanceEvidenceRef  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_slice_16_governance_finding() -> None:
    """The Slice 17 2nd sub-slice module MUST NOT redefine the Slice 16
    typed :class:`GovernanceFinding` -- it consumes the typed BaseModel
    directly via import.
    """

    from iriai_build_v2.execution_control import recommendation_builder as mod

    # Either the module does NOT re-export GovernanceFinding OR (if it
    # does, e.g. via an internal import) the re-exported symbol IS the
    # Slice 16 1st sub-slice class (identity).
    assert getattr(mod, "GovernanceFinding", None) is None or (
        mod.GovernanceFinding is GovernanceFinding  # type: ignore[attr-defined]
    )


# ── RecommendationBuilderInputs construction + extra-forbid ────────────────


def test_inputs_construction_minimal_defaults() -> None:
    """Construct :class:`RecommendationBuilderInputs` with only the
    required fields; defaults populate ``min_confidence_threshold`` +
    ``finding_kind_to_consumer_routing``."""

    inputs = RecommendationBuilderInputs(
        corpus_id="8ac124d6",
        findings=[_finding()],
    )
    assert inputs.corpus_id == "8ac124d6"
    assert len(inputs.findings) == 1
    assert inputs.min_confidence_threshold == DEFAULT_MIN_CONFIDENCE_THRESHOLD
    assert inputs.min_confidence_threshold == 0.7
    # Default routing covers all 14 FindingKind values per doc-16:63-78.
    expected_kinds = set(get_args(FindingKind))
    assert set(inputs.finding_kind_to_consumer_routing.keys()) == expected_kinds


def test_inputs_construction_with_overrides() -> None:
    """The caller MAY override ``min_confidence_threshold`` +
    ``finding_kind_to_consumer_routing``."""

    inputs = RecommendationBuilderInputs(
        corpus_id="custom-corpus",
        findings=[],
        min_confidence_threshold=0.5,
        finding_kind_to_consumer_routing={
            "workflow_inefficiency": "failure_router",
            "unsafe_route": "failure_router",
        },
    )
    assert inputs.min_confidence_threshold == 0.5
    assert (
        inputs.finding_kind_to_consumer_routing["workflow_inefficiency"]
        == "failure_router"
    )


def test_inputs_extra_forbid_rejects_typo() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs raise
    ``ValidationError`` per :data:`feedback_no_silent_degradation`."""

    with pytest.raises(ValidationError):
        RecommendationBuilderInputs(
            corpus_id="8ac124d6",
            findings=[],
            typoed_field="oops",  # type: ignore[call-arg]
        )


def test_inputs_rejects_unknown_finding_kind_in_routing() -> None:
    """The typed :attr:`finding_kind_to_consumer_routing` field is
    typed as :data:`dict[FindingKind, PolicyConsumer]` so unknown
    finding kinds fail closed via Literal range."""

    with pytest.raises(ValidationError):
        RecommendationBuilderInputs(
            corpus_id="8ac124d6",
            findings=[],
            finding_kind_to_consumer_routing={
                "unknown_finding_kind": "scheduler",  # type: ignore[dict-item]
            },
        )


def test_inputs_rejects_unknown_consumer_in_routing() -> None:
    """The typed :attr:`finding_kind_to_consumer_routing` field is
    typed as :data:`dict[FindingKind, PolicyConsumer]` so unknown
    consumers fail closed via Literal range."""

    with pytest.raises(ValidationError):
        RecommendationBuilderInputs(
            corpus_id="8ac124d6",
            findings=[],
            finding_kind_to_consumer_routing={
                "workflow_inefficiency": "unknown_consumer",  # type: ignore[dict-item]
            },
        )


# ── RecommendationBuilderResult construction + extra-forbid ────────────────


def test_result_construction_defaults() -> None:
    """Construct :class:`RecommendationBuilderResult` with all default
    empty lists."""

    result = RecommendationBuilderResult()
    assert result.recommendations == []
    assert result.refused_findings == []
    assert result.gap_findings == []


def test_result_extra_forbid_rejects_typo() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs raise
    ``ValidationError``."""

    with pytest.raises(ValidationError):
        RecommendationBuilderResult(typoed_field="oops")  # type: ignore[call-arg]


# ── RecommendationBuilderEmissionGap construction + extra-forbid ──────────


def test_gap_construction() -> None:
    """Construct :class:`RecommendationBuilderEmissionGap` with all
    required fields."""

    gap = _gap()
    assert (
        gap.failure_id == "recommendation_builder_emission_failed"
    )
    assert gap.corpus_id == "8ac124d6"
    assert gap.target_consumer == "scheduler"
    assert gap.reason == "recommendation_construction_failed"


def test_gap_extra_forbid_rejects_typo() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs raise
    ``ValidationError``."""

    with pytest.raises(ValidationError):
        _gap(typoed_field="oops")


def test_gap_rejects_invalid_failure_id() -> None:
    """The typed :attr:`failure_id` field is typed as
    ``Literal["recommendation_builder_emission_failed"]`` so other
    values fail closed."""

    with pytest.raises(ValidationError):
        _gap(failure_id="some_other_failure")


def test_gap_accepts_none_target_consumer() -> None:
    """The typed :attr:`target_consumer` field is ``PolicyConsumer | None``
    so ``None`` is a valid value (per the unmapped-finding-kind case)."""

    gap = _gap(target_consumer=None, reason="unmapped_finding_kind")
    assert gap.target_consumer is None


def test_gap_accepts_none_finding_idempotency_key() -> None:
    """The typed :attr:`finding_idempotency_key` field is ``str | None``
    so ``None`` is a valid value (per the corrupt-bundle case)."""

    gap = _gap(finding_idempotency_key=None, reason="corrupt_bundle")
    assert gap.finding_idempotency_key is None


# ── High-confidence finding -> typed recommendation emission ───────────────


def test_high_confidence_workflow_finding_emits_scheduler_recommendation() -> None:
    """A high-confidence (>=0.7) workflow_inefficiency finding with
    requires_policy_artifact=True emits exactly ONE typed
    :class:`GovernancePolicyRecommendation` routed to ``scheduler``
    with ``status="draft"`` per doc-17:166-167."""

    builder = RecommendationBuilder()
    inputs = _builder_inputs(findings=[_finding()])
    result = builder.build_recommendations(inputs)

    assert len(result.recommendations) == 1
    assert len(result.refused_findings) == 0
    assert len(result.gap_findings) == 0
    rec = result.recommendations[0]
    assert isinstance(rec, GovernancePolicyRecommendation)
    assert rec.status == "draft"
    assert rec.consumer == "scheduler"
    # The proposed_policy_artifact is the typed SchedulerPolicyArtifact
    # per the doc-17:107-145 consumer-specific BaseModel.
    assert isinstance(rec.proposed_policy_artifact, SchedulerPolicyArtifact)


def test_failure_router_finding_emits_failure_router_recommendation() -> None:
    """A high-confidence ``runtime_instability`` finding routes to
    ``failure_router`` and emits a typed
    :class:`FailureRouterPolicyArtifact` per doc-17:107-114."""

    finding = _finding(
        kind="runtime_instability",
        class_name="runtime_provider_instability",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.consumer == "failure_router"
    assert isinstance(
        rec.proposed_policy_artifact, FailureRouterPolicyArtifact
    )
    # Per the failure-class mapping the Slice 07 failure_class is
    # `runtime_provider` for the `runtime_instability` kind.
    assert rec.proposed_policy_artifact.failure_class == "runtime_provider"
    assert (
        rec.proposed_policy_artifact.failure_type
        == "runtime_provider_instability"
    )
    assert rec.proposed_policy_artifact.action == "retry"


def test_supervisor_finding_emits_supervisor_recommendation() -> None:
    """A high-confidence ``implementation_plan_deviation`` finding
    routes to ``supervisor`` (read-only) and emits a typed
    :class:`SupervisorPolicyArtifact` per doc-17:122-126."""

    finding = _finding(
        kind="implementation_plan_deviation",
        class_name="accepted_plan_deviation",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.consumer == "supervisor"
    assert isinstance(
        rec.proposed_policy_artifact, SupervisorPolicyArtifact
    )
    # Per doc-17:126 the typed read_only field is Literal[True] = True
    # (the typed surface enforces this at construction).
    assert rec.proposed_policy_artifact.read_only is True


def test_dashboard_finding_emits_dashboard_recommendation() -> None:
    """A high-confidence ``provenance_gap`` finding routes to
    ``dashboard`` (read-only) and emits a typed
    :class:`DashboardPolicyArtifact` per doc-17:128-132."""

    finding = _finding(
        kind="provenance_gap",
        class_name="line_provenance_gap",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.consumer == "dashboard"
    assert isinstance(rec.proposed_policy_artifact, DashboardPolicyArtifact)
    assert rec.proposed_policy_artifact.read_only is True


def test_planning_finding_emits_planning_recommendation() -> None:
    """A high-confidence ``task_contract_weakness`` finding routes to
    ``planning`` (advisory-only) and emits a typed
    :class:`PlanningPolicyArtifact` per doc-17:134-138."""

    finding = _finding(
        kind="task_contract_weakness",
        class_name="task_contract_ambiguity",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.consumer == "planning"
    assert isinstance(rec.proposed_policy_artifact, PlanningPolicyArtifact)
    # Per doc-17:138 the typed advisory_only field is Literal[True] = True.
    assert rec.proposed_policy_artifact.advisory_only is True


def test_merge_queue_finding_emits_merge_queue_recommendation() -> None:
    """A high-confidence ``merge_queue_drag`` finding routes to
    ``merge_queue`` and emits a typed
    :class:`MergeQueuePolicyArtifact` per doc-17:140-144."""

    finding = _finding(
        kind="merge_queue_drag",
        class_name="merge_queue_wait_or_retry_drag",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 1
    rec = result.recommendations[0]
    assert rec.consumer == "merge_queue"
    assert isinstance(
        rec.proposed_policy_artifact, MergeQueuePolicyArtifact
    )


# ── Per-consumer artifact-shape correctness (6 tests) ──────────────────────


def test_scheduler_artifact_carries_finding_scope() -> None:
    """The :class:`SchedulerPolicyArtifact.scope` carries the finding's
    affected_scope dict per doc-17:118."""

    finding = _finding(affected_scope={"lane": "ml-7", "wave": "wave-3"})
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert isinstance(rec.proposed_policy_artifact, SchedulerPolicyArtifact)
    assert rec.proposed_policy_artifact.scope == {"lane": "ml-7", "wave": "wave-3"}


def test_failure_router_artifact_carries_route_budget_key() -> None:
    """The :class:`FailureRouterPolicyArtifact.route_budget_key` is
    ``{failure_class}/{failure_type}`` per doc-17:111."""

    finding = _finding(
        kind="runtime_instability",
        class_name="runtime_provider_instability",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert isinstance(
        rec.proposed_policy_artifact, FailureRouterPolicyArtifact
    )
    assert (
        rec.proposed_policy_artifact.route_budget_key
        == "runtime_provider/runtime_provider_instability"
    )
    # Required tests reflect the finding's metric_refs (per doc-17:114).
    assert rec.proposed_policy_artifact.required_tests == [
        "commit_hygiene_loops_per_window"
    ]


def test_supervisor_artifact_carries_finding_class_value() -> None:
    """The :class:`SupervisorPolicyArtifact.value` dict carries the
    finding's class metadata (per doc-17:125)."""

    finding = _finding(
        kind="governance_evidence_conflict",
        class_name="governance_evidence_conflict",
        recommended_action_display="Manual evidence review.",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert isinstance(rec.proposed_policy_artifact, SupervisorPolicyArtifact)
    val = rec.proposed_policy_artifact.value
    assert val["finding_kind"] == "governance_evidence_conflict"
    assert val["class_name"] == "governance_evidence_conflict"
    assert val["recommended_display"] == "Manual evidence review."


def test_dashboard_artifact_default_policy_kind_is_view_priority() -> None:
    """The :class:`DashboardPolicyArtifact.policy_kind` defaults to
    ``"view_priority"`` per the doc-17:129 taxonomy."""

    finding = _finding(
        kind="stale_projection",
        class_name="stale_context_projection",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert isinstance(rec.proposed_policy_artifact, DashboardPolicyArtifact)
    assert rec.proposed_policy_artifact.policy_kind == "view_priority"


def test_planning_artifact_default_policy_kind_is_contract_template_hint() -> None:
    """The :class:`PlanningPolicyArtifact.policy_kind` defaults to
    ``"contract_template_hint"`` per the doc-17:135 taxonomy."""

    finding = _finding(
        kind="task_contract_weakness",
        class_name="task_contract_ambiguity",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert isinstance(rec.proposed_policy_artifact, PlanningPolicyArtifact)
    assert (
        rec.proposed_policy_artifact.policy_kind == "contract_template_hint"
    )


def test_merge_queue_artifact_carries_required_queue_tests() -> None:
    """The :class:`MergeQueuePolicyArtifact.required_queue_tests` is
    the finding's metric_refs (per doc-17:144)."""

    finding = _finding(
        kind="merge_queue_drag",
        class_name="merge_queue_wait_or_retry_drag",
        metric_refs=["merge_queue_wait_time", "merge_queue_retry_count"],
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert isinstance(
        rec.proposed_policy_artifact, MergeQueuePolicyArtifact
    )
    assert rec.proposed_policy_artifact.required_queue_tests == [
        "merge_queue_wait_time",
        "merge_queue_retry_count",
    ]


# ── Below-threshold finding behaviour (doc-17:204) ─────────────────────────


def test_below_threshold_finding_is_refused_and_no_emit() -> None:
    """A finding with confidence below the default threshold (0.7)
    is refused-and-no-emit per doc-17:204; the finding's
    idempotency_key is recorded in ``refused_findings``."""

    finding = _finding(confidence=0.5)
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 0
    assert len(result.refused_findings) == 1
    assert finding.idempotency_key in result.refused_findings
    assert len(result.gap_findings) == 0


def test_threshold_at_exact_boundary_emits() -> None:
    """A finding with confidence == threshold is INCLUDED per the
    ``>=`` semantics in :class:`RecommendationBuilder`.

    Per doc-17:204 *"Recommendation builder refuses findings BELOW
    confidence threshold."* the boundary case is INCLUSIVE (>= not >).
    """

    finding = _finding(confidence=0.7)
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 1
    assert len(result.refused_findings) == 0


def test_overridden_threshold_changes_qualification() -> None:
    """The caller MAY override the threshold per
    :attr:`RecommendationBuilderInputs.min_confidence_threshold`."""

    # confidence 0.6 is below default 0.7 but above override 0.5.
    finding = _finding(confidence=0.6)
    builder = RecommendationBuilder()
    inputs_strict = _builder_inputs(findings=[finding])  # default 0.7
    inputs_loose = _builder_inputs(
        findings=[finding], min_confidence_threshold=0.5,
    )
    result_strict = builder.build_recommendations(inputs_strict)
    result_loose = builder.build_recommendations(inputs_loose)
    # Strict: refused.
    assert len(result_strict.recommendations) == 0
    assert len(result_strict.refused_findings) == 1
    # Loose: emitted.
    assert len(result_loose.recommendations) == 1
    assert len(result_loose.refused_findings) == 0


# ── requires_policy_artifact=False finding behaviour ────────────────────────


def test_requires_policy_artifact_false_is_refused() -> None:
    """A finding with ``requires_policy_artifact=False`` is refused
    even at high confidence per doc-16:99 + doc-17:218."""

    finding = _finding(confidence=0.9, requires_policy_artifact=False)
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 0
    assert len(result.refused_findings) == 1
    assert finding.idempotency_key in result.refused_findings


def test_high_confidence_advisory_only_finding_is_refused() -> None:
    """A finding with ``requires_policy_artifact=False`` AND confidence
    >= threshold is refused; the requires_policy_artifact filter is the
    first-stage discipline."""

    finding = _finding(
        confidence=0.95,
        requires_policy_artifact=False,
        recommended_action_display="Advisory only -- no policy change.",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 0
    assert len(result.refused_findings) == 1


# ── Conflicting recommendations (doc-17:194-195) ────────────────────────────


def test_conflicting_recommendations_both_status_draft() -> None:
    """Per doc-17:194-195 *"Conflicting recommendations for one
    consumer: mark both draft and require human or policy owner
    decision."* the builder emits BOTH recommendations as
    ``status="draft"`` when 2+ findings route to the same consumer +
    same scope.
    """

    # 2 findings -> same kind (scheduler) + same scope.
    f1 = _finding(
        idempotency_key="sha256:f1",
        affected_scope={"lane": "ml-7"},
    )
    f2 = _finding(
        idempotency_key="sha256:f2",
        affected_scope={"lane": "ml-7"},
        class_name="scheduler_wave_too_small",
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[f1, f2]))

    assert len(result.recommendations) == 2
    # Both are status="draft" per doc-17:194-195.
    for rec in result.recommendations:
        assert rec.status == "draft"
        assert rec.consumer == "scheduler"
    # Per doc-17:194-195 the conflicting recommendations are NOT
    # coalesced; they are emitted as separate records so the
    # human/policy-owner decision (future Slice 17 4th sub-slice) can
    # distinguish them.
    assert (
        result.recommendations[0].idempotency_key
        != result.recommendations[1].idempotency_key
    )


def test_non_conflicting_recommendations_both_status_draft() -> None:
    """Two findings -> different consumers each emit one
    ``status="draft"`` recommendation (no conflict)."""

    f1 = _finding(idempotency_key="sha256:f1")  # scheduler
    f2 = _finding(
        idempotency_key="sha256:f2",
        kind="runtime_instability",
        class_name="runtime_provider_instability",
    )  # failure_router
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[f1, f2]))

    assert len(result.recommendations) == 2
    consumers = {rec.consumer for rec in result.recommendations}
    assert consumers == {"scheduler", "failure_router"}
    for rec in result.recommendations:
        assert rec.status == "draft"


# ── Builder NEVER raises (fail-closed) ──────────────────────────────────────


def test_builder_does_not_raise_on_unmapped_finding_kind() -> None:
    """If the routing table does NOT cover a finding's kind, the
    builder records a typed gap finding instead of raising per
    :data:`feedback_no_silent_degradation`."""

    finding = _finding(kind="workflow_inefficiency")
    builder = RecommendationBuilder()
    # Custom routing that does NOT include workflow_inefficiency.
    inputs = _builder_inputs(
        findings=[finding],
        finding_kind_to_consumer_routing={
            "runtime_instability": "failure_router",
        },
    )
    result = builder.build_recommendations(inputs)

    assert len(result.recommendations) == 0
    assert len(result.gap_findings) == 1
    gap = result.gap_findings[0]
    assert (
        gap.failure_id == RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID
    )
    assert gap.target_consumer is None
    assert gap.reason == "unmapped_finding_kind"
    assert gap.finding_idempotency_key == finding.idempotency_key
    assert gap.evidence_payload["finding_kind"] == "workflow_inefficiency"


def test_builder_does_not_raise_on_construction_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the per-consumer artifact construction raises, the builder
    records a typed gap finding instead of propagating the exception
    per :data:`feedback_no_silent_degradation`.

    Patches the scheduler factory to raise a controlled exception,
    verifies the gap-finding shape.
    """

    from iriai_build_v2.execution_control import recommendation_builder

    def boom(finding: GovernanceFinding) -> SchedulerPolicyArtifact:
        raise ValueError("induced construction failure")

    monkeypatch.setitem(
        recommendation_builder._CONSUMER_ARTIFACT_FACTORIES,
        "scheduler",
        boom,
    )

    finding = _finding()  # scheduler kind
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))

    assert len(result.recommendations) == 0
    assert len(result.gap_findings) == 1
    gap = result.gap_findings[0]
    assert gap.failure_id == RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID
    assert gap.target_consumer == "scheduler"
    assert gap.reason == "recommendation_construction_failed"
    assert gap.finding_idempotency_key == finding.idempotency_key
    assert "ValueError" in gap.evidence_payload["exception_type"]
    assert "induced construction failure" in gap.evidence_payload[
        "exception_message"
    ]


# ── DIRECT annotation-identity REUSE assertions ────────────────────────────


def test_inputs_findings_annotation_is_slice_16_governance_finding() -> None:
    """The :attr:`RecommendationBuilderInputs.findings` field is typed
    as ``list[GovernanceFinding]`` -- DIRECT annotation-identity
    REUSE of the Slice 16 1st sub-slice typed BaseModel."""

    findings_anno = RecommendationBuilderInputs.model_fields[
        "findings"
    ].annotation
    # The annotation is `list[GovernanceFinding]`.
    args = get_args(findings_anno)
    assert len(args) == 1
    # DIRECT identity: the Slice 16 1st sub-slice GovernanceFinding.
    assert args[0] is GovernanceFinding


def test_result_recommendations_annotation_is_slice_17_first_recommendation() -> None:
    """The :attr:`RecommendationBuilderResult.recommendations` field is
    typed as ``list[GovernancePolicyRecommendation]`` -- DIRECT
    annotation-identity REUSE of the Slice 17 1st sub-slice typed
    BaseModel."""

    recs_anno = RecommendationBuilderResult.model_fields[
        "recommendations"
    ].annotation
    args = get_args(recs_anno)
    assert len(args) == 1
    assert args[0] is GovernancePolicyRecommendation


def test_result_gap_findings_annotation_is_local_gap_class() -> None:
    """The :attr:`RecommendationBuilderResult.gap_findings` field is
    typed as ``list[RecommendationBuilderEmissionGap]`` -- DIRECT
    annotation-identity to the local gap class."""

    gaps_anno = RecommendationBuilderResult.model_fields[
        "gap_findings"
    ].annotation
    args = get_args(gaps_anno)
    assert len(args) == 1
    assert args[0] is RecommendationBuilderEmissionGap


def test_per_consumer_artifact_factories_return_slice_17_first_basemodels() -> None:
    """Each per-consumer artifact factory returns the typed Slice 17
    1st sub-slice ``*PolicyArtifact`` BaseModel."""

    finding_scheduler = _finding(kind="workflow_inefficiency")
    finding_failure = _finding(
        kind="runtime_instability",
        class_name="runtime_provider_instability",
    )
    finding_supervisor = _finding(
        kind="implementation_plan_deviation",
        class_name="accepted_plan_deviation",
    )
    finding_dashboard = _finding(
        kind="provenance_gap", class_name="line_provenance_gap"
    )
    finding_planning = _finding(
        kind="task_contract_weakness",
        class_name="task_contract_ambiguity",
    )
    finding_merge = _finding(
        kind="merge_queue_drag",
        class_name="merge_queue_wait_or_retry_drag",
    )

    builder = RecommendationBuilder()
    for finding, expected_type in [
        (finding_scheduler, SchedulerPolicyArtifact),
        (finding_failure, FailureRouterPolicyArtifact),
        (finding_supervisor, SupervisorPolicyArtifact),
        (finding_dashboard, DashboardPolicyArtifact),
        (finding_planning, PlanningPolicyArtifact),
        (finding_merge, MergeQueuePolicyArtifact),
    ]:
        result = builder.build_recommendations(
            _builder_inputs(findings=[finding])
        )
        assert len(result.recommendations) == 1
        rec = result.recommendations[0]
        # DIRECT identity: the Slice 17 1st sub-slice typed BaseModel.
        assert isinstance(rec.proposed_policy_artifact, expected_type)


# ── source_finding_ids + source_metric_refs population ────────────────────


def test_source_finding_ids_populated_from_finding_idempotency_key() -> None:
    """The typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_finding_ids`
    field is populated from the finding's
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.idempotency_key`
    string per doc-17:80 by-name reference."""

    finding = _finding(idempotency_key="sha256:finding-A")
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert rec.source_finding_ids == ["sha256:finding-A"]
    # Per doc-17:80 the field is list[str], NOT typed BaseModel.
    assert all(isinstance(item, str) for item in rec.source_finding_ids)


def test_source_metric_refs_populated_from_finding_metric_refs() -> None:
    """The typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.source_metric_refs`
    field is populated from the finding's
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.metric_refs`
    string list per doc-17:81 by-name reference."""

    finding = _finding(
        metric_refs=["tasks_per_hour@v1", "commit_failures_per_task@v1.2"],
    )
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert rec.source_metric_refs == [
        "tasks_per_hour@v1",
        "commit_failures_per_task@v1.2",
    ]
    # Per doc-17:81 the field is list[str], NOT typed BaseModel.
    assert all(isinstance(item, str) for item in rec.source_metric_refs)


# ── FindingKind coverage (14 of 14) ────────────────────────────────────────


def test_default_routing_covers_all_14_finding_kinds() -> None:
    """The default routing table covers all 14 of 14 FindingKind values
    per doc-16:63-78 (no holes)."""

    from iriai_build_v2.execution_control.recommendation_builder import (
        _DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING,
    )

    expected_kinds = set(get_args(FindingKind))
    assert len(expected_kinds) == 14
    assert (
        set(_DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING.keys()) == expected_kinds
    )


def test_all_14_finding_kinds_route_to_a_consumer() -> None:
    """For each of the 14 FindingKind values, a high-confidence finding
    routes to exactly one consumer."""

    from iriai_build_v2.execution_control.recommendation_builder import (
        _DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING,
    )

    builder = RecommendationBuilder()
    for kind in get_args(FindingKind):
        finding = _finding(kind=kind)
        result = builder.build_recommendations(
            _builder_inputs(findings=[finding])
        )
        # Every kind should produce exactly 1 recommendation (or 1 gap
        # if construction fails; this test verifies the routing exists).
        if len(result.recommendations) == 1:
            assert (
                result.recommendations[0].consumer
                == _DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING[kind]
            )
        else:
            # If construction fails, the gap MUST cite the expected
            # consumer (the routing was successful but the per-consumer
            # artifact construction failed for some reason -- this is
            # acceptable but the routing itself MUST be present).
            assert len(result.gap_findings) == 1
            assert (
                result.gap_findings[0].target_consumer
                == _DEFAULT_FINDING_KIND_TO_CONSUMER_ROUTING[kind]
            )


# ── compute_recommendation_id pure helper ──────────────────────────────────


def test_compute_recommendation_id_deterministic() -> None:
    """The :func:`compute_recommendation_id` helper is deterministic --
    identical inputs produce identical outputs across calls."""

    id1 = compute_recommendation_id(
        consumer="scheduler",
        source_finding_idempotency_key="sha256:abc",
        affected_scope_digest="sha256:def",
    )
    id2 = compute_recommendation_id(
        consumer="scheduler",
        source_finding_idempotency_key="sha256:abc",
        affected_scope_digest="sha256:def",
    )
    assert id1 == id2


def test_compute_recommendation_id_varies_by_consumer() -> None:
    """Different consumers produce different recommendation_ids."""

    id_scheduler = compute_recommendation_id(
        consumer="scheduler",
        source_finding_idempotency_key="sha256:abc",
        affected_scope_digest="sha256:def",
    )
    id_failure_router = compute_recommendation_id(
        consumer="failure_router",
        source_finding_idempotency_key="sha256:abc",
        affected_scope_digest="sha256:def",
    )
    assert id_scheduler != id_failure_router
    assert id_scheduler.startswith("recommendation:scheduler:")
    assert id_failure_router.startswith("recommendation:failure_router:")


# ── Failure-router 4-add-point registration ────────────────────────────────


def test_failure_router_registers_new_failure_id_in_failure_types_tuple() -> None:
    """The NEW typed failure id
    ``recommendation_builder_emission_failed`` is registered in
    :data:`FAILURE_TYPES`."""

    assert "recommendation_builder_emission_failed" in FAILURE_TYPES


def test_failure_router_registers_new_failure_id_in_failure_type_literal() -> None:
    """The NEW typed failure id is in the :data:`FailureType` Literal
    value-set."""

    assert "recommendation_builder_emission_failed" in get_args(FailureType)


def test_failure_router_registers_new_failure_id_as_retryable() -> None:
    """The NEW typed failure id is registered as retryable in
    :data:`_RETRYABLE_FAILURE_TYPES` (NON-blocking governance
    projection observer)."""

    assert "recommendation_builder_emission_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_routes_new_failure_id_to_retry_governance_projection() -> None:
    """The NEW typed failure id routes to the REUSED
    ``retry_governance_projection`` action under the EXISTING
    ``evidence_corruption`` failure_class (mirrors Slice 16 2nd /
    3rd-A / 3rd-B / 4th sub-slice precedent)."""

    matches = [
        row for row in _ROUTE_ROWS
        if row[0].failure_type == "recommendation_builder_emission_failed"
    ]
    assert len(matches) == 1
    ftp, frp = matches[0]
    assert ftp.failure_class == "evidence_corruption"
    assert ftp.failure_type == "recommendation_builder_emission_failed"
    assert frp.action == "retry_governance_projection"


def test_recommendation_builder_emission_failure_id_constant_matches_route() -> None:
    """The module-level
    :data:`RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID` constant matches
    the registered failure type string."""

    assert (
        RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID
        == "recommendation_builder_emission_failed"
    )
    assert (
        RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID in FAILURE_TYPES
    )


# ── Empty / edge cases ──────────────────────────────────────────────────────


def test_builder_handles_empty_findings_list() -> None:
    """An empty findings list yields an empty result with no
    recommendations, refused_findings, or gap_findings."""

    builder = RecommendationBuilder()
    result = builder.build_recommendations(
        _builder_inputs(findings=[])
    )
    assert result.recommendations == []
    assert result.refused_findings == []
    assert result.gap_findings == []


def test_recommendation_status_is_always_draft_per_doc_17_166_167() -> None:
    """Per doc-17:166-167 every newly-emitted recommendation has
    ``status="draft"``."""

    findings = [
        _finding(idempotency_key=f"sha256:f{i}", confidence=0.9)
        for i in range(3)
    ]
    builder = RecommendationBuilder()
    result = builder.build_recommendations(
        _builder_inputs(findings=findings)
    )
    for rec in result.recommendations:
        assert rec.status == "draft"


def test_recommendation_carries_finding_confidence_as_float() -> None:
    """The recommendation's typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.confidence`
    field carries the source finding's confidence as a float."""

    finding = _finding(confidence=0.85)
    builder = RecommendationBuilder()
    result = builder.build_recommendations(_builder_inputs(findings=[finding]))
    rec = result.recommendations[0]
    assert rec.confidence == 0.85
    assert isinstance(rec.confidence, float)


def test_recommendation_carries_typed_risk_level_from_severity() -> None:
    """The recommendation's typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.risk_level`
    is derived from the finding's
    :attr:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding.severity`."""

    builder = RecommendationBuilder()
    for severity, expected_risk in [
        ("info", "low"),
        ("low", "low"),
        ("medium", "medium"),
        ("high", "high"),
        ("critical", "high"),
    ]:
        finding = _finding(severity=severity)
        result = builder.build_recommendations(
            _builder_inputs(findings=[finding])
        )
        assert len(result.recommendations) == 1
        rec = result.recommendations[0]
        assert rec.risk_level == expected_risk


def test_recommendation_idempotency_key_is_deterministic() -> None:
    """The recommendation's typed
    :attr:`~iriai_build_v2.execution_control.policy_recommendation.GovernancePolicyRecommendation.idempotency_key`
    is deterministic: a re-run with the same finding produces the
    same recommendation idempotency_key."""

    builder = RecommendationBuilder()
    finding = _finding()
    result1 = builder.build_recommendations(
        _builder_inputs(findings=[finding])
    )
    result2 = builder.build_recommendations(
        _builder_inputs(findings=[finding])
    )
    assert (
        result1.recommendations[0].idempotency_key
        == result2.recommendations[0].idempotency_key
    )


def test_default_min_confidence_threshold_constant_value() -> None:
    """The default min-confidence threshold constant is 0.7 per
    doc-17:204 + the Slice 16 2nd sub-slice min_confidence=0.5
    precedent (tighter for the recommendation builder than the rule
    engine)."""

    assert DEFAULT_MIN_CONFIDENCE_THRESHOLD == 0.7
    assert isinstance(DEFAULT_MIN_CONFIDENCE_THRESHOLD, float)
