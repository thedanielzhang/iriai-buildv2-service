"""Slice 19 3rd sub-slice -- unit tests for the typed dashboard view at
``execution_control/governance_dashboard_view.py``.

Covers the doc-19:152 step 3 + doc-19:170-171 dashboard view surface:

* :data:`DASHBOARD_VIEW_FAILURE_ID` -- the typed failure id
  (``governance_dashboard_view_failed``) registered under the EXISTING
  ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` action.
* :class:`DashboardFindingSummary` typed BaseModel (extra-forbid;
  refs-only projection of Slice 16 GovernanceFinding identity surface).
* :class:`DashboardRecommendationSummary` typed BaseModel (extra-
  forbid; refs-only projection of Slice 17 GovernancePolicyRecommendation
  identity surface).
* :class:`DashboardReplayResultSummary` typed BaseModel (extra-forbid;
  refs-only projection of Slice 18 CounterfactualResult identity
  surface).
* :class:`DashboardViewInputs` typed BaseModel (extra-forbid; carries
  the typed Slice 19 2nd sub-slice SnapshotAPIResult source + optional
  caller-side display budget overrides).
* :class:`DashboardViewPayload` typed BaseModel (extra-forbid; bounded
  dashboard payload with etag = snapshot_digest per doc-19:170-171 +
  display_only AC2 flag per doc-19:225-226).
* :class:`DashboardViewGap` typed BaseModel (extra-forbid; mirrors
  SnapshotAPIGap).
* :class:`DashboardViewResult` typed BaseModel (extra-forbid;
  payload | None + gap_findings list).
* :class:`GovernanceDashboardView.render(...)` -- the projection
  method:
  * Happy-path -> typed DashboardViewPayload emitted with etag =
    snapshot_digest verbatim.
  * Bounded display-truncation discipline (LIMIT cap+1 across 5
    dimensions: findings + recommendations + replay_results +
    page_refs + blocked_by).
  * ETag = snapshot_digest reproducibility (same snapshot -> same
    etag).
  * Upstream snapshot missing -> typed gap on
    ``upstream_snapshot_missing`` + propagated upstream gaps.
  * View NEVER raises (typed gap projection on construction failure).
* :meth:`GovernanceDashboardView.compute_etag(...)` -- the typed
  ETag helper:
  * etag == snapshot_digest verbatim.
* DIRECT annotation-identity REUSE assertions for Slice 19 1st
  sub-slice :class:`GovernanceSnapshot` + Slice 19 2nd sub-slice
  :class:`SnapshotAPIResult` + Slice 13a :data:`CompletenessState` +
  :data:`EvidenceQuality` + Slice 16 :class:`GovernanceFinding` +
  Slice 17 :class:`GovernancePolicyRecommendation` + Slice 18
  :class:`CounterfactualResult`.
* Failure-router 4-pure-data add discipline (Slice 14 2nd sub-slice +
  Slice 19 2nd sub-slice precedent verbatim).
* AC1/AC2/AC6/AC7 enforcement tests per doc-19 § Acceptance Criteria.

Per the implementer prompt § "Non-Negotiables" -- fail-closed on
every Pydantic field validator; no executor wiring outside this
slice's own acceptance tests; the Slice 13a + Slice 13A + Slice 14 +
Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 1st + Slice 19
2nd sub-slice modules + tests remain READ-ONLY this sub-slice.
"""

from __future__ import annotations

import typing
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    RiskChange,
)
from iriai_build_v2.execution_control.finding_engine import (
    FindingKind,
    FindingSeverity,
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GovernanceSnapshot,
    compute_governance_snapshot_digest,
)
from iriai_build_v2.execution_control.governance_dashboard_view import (
    DASHBOARD_VIEW_FAILURE_ID,
    DashboardFindingSummary,
    DashboardRecommendationSummary,
    DashboardReplayResultSummary,
    DashboardViewGap,
    DashboardViewInputs,
    DashboardViewPayload,
    DashboardViewResult,
    GovernanceDashboardView,
)
from iriai_build_v2.execution_control.governance_snapshot_api import (
    GovernanceSnapshotAPI,
    SnapshotAPICorpus,
    SnapshotAPIGap,
    SnapshotAPIInputs,
    SnapshotAPIResult,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    PolicyConsumer,
    PolicyRecommendationStatus,
    SchedulerPolicyArtifact,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
    FailureType,
    RouteAction,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    EvidenceQuality,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
)


# --- Fixture builders (mirror Slice 19 2nd sub-slice fixtures) -------------


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-19-3",
        digest="b" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    base: dict[str, object] = dict(
        page_ref_id="page-ref-19-3-1",
        authority="typed_journal",
        source_ref_id="ref-19-3",
        digest="c" * 64,
        completeness="complete",
        exact=True,
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)


def _finding(**overrides: object) -> GovernanceFinding:
    base: dict[str, object] = dict(
        idempotency_key="finding-key-19-3-a",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk", "runtime": "claude-sdk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-25#slice-19-3rd"],
        metric_refs=["tasks_per_hour"],
        estimated_lost_hours=3.5,
        estimated_retry_impact=0.12,
        recommended_action_display="Consider tightening commit retry budget.",
        recommendation_draft_ref=None,
        safe_runtime_action=False,
        requires_policy_artifact=True,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
        primary_cause_finding_id=None,
        linked_finding_ids=[],
    )
    base.update(overrides)
    return GovernanceFinding(**base)


def _recommendation(
    **overrides: object,
) -> GovernancePolicyRecommendation:
    base: dict[str, object] = dict(
        idempotency_key="recommendation-key-19-3-a",
        recommendation_id="rec-19-3-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-19-3-a"],
        source_metric_refs=["tasks_per_hour@v1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_scheduler_wave_cap"],
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml-7"},
            value={"wave_cap": 7},
            guardrails=["max_concurrent_tasks_per_lane"],
        ),
        activation_requirements=["replay_against_8ac124d6_passes"],
        rollback_requirements=["revert_to_prior_wave_cap"],
    )
    base.update(overrides)
    return GovernancePolicyRecommendation(**base)


def _replay_result(**overrides: object) -> CounterfactualResult:
    base: dict[str, object] = dict(
        result_id="result-19-3-1",
        result_version="v1",
        scenario_id="scenario-19-3-1",
        corpus_id="corpus-19-3",
        assumptions=["product_defect_independent_of_wave_size"],
        validity_limits=["sample_size<10"],
        policy_provenance_refs=[_evidence_ref()],
        safety_guard_class=None,
        estimated_delta_hours=-2.5,
        estimated_delta_repair_cycles=-0.5,
        estimated_delta_commit_failures=-0.1,
        estimated_risk_change="lower",
        confidence=0.65,
        invalidated_by=[],
        supporting_finding_ids=["finding-key-19-3-a"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)


def _build_snapshot_via_api(
    *,
    corpus_id: str = "test-corpus",
    findings: list[GovernanceFinding] | None = None,
    recommendations: list[GovernancePolicyRecommendation] | None = None,
    replay_results: list[CounterfactualResult] | None = None,
    page_refs: list[GovernanceEvidencePageRef] | None = None,
    omitted_findings_count: int = 0,
    omitted_recommendations_count: int = 0,
    omitted_replay_results_count: int = 0,
    omitted_page_refs_count: int = 0,
    corpus_evidence_quality: EvidenceQuality = "canonical",
    next_cursor: str | None = None,
    blocked_by: list[str] | None = None,
    completeness_override: CompletenessState | None = None,
    evidence_quality_override: EvidenceQuality | None = None,
    max_findings: int = 20,
    max_recommendations: int = 10,
    max_replay_results: int = 10,
    max_page_refs: int = 10,
) -> SnapshotAPIResult:
    """Build a typed SnapshotAPIResult via the typed snapshot API."""

    api = GovernanceSnapshotAPI()
    inputs = SnapshotAPIInputs(
        corpus_id=corpus_id,
        max_findings=max_findings,
        max_recommendations=max_recommendations,
        max_replay_results=max_replay_results,
        max_page_refs=max_page_refs,
        completeness_override=completeness_override,
        evidence_quality_override=evidence_quality_override,
    )
    corpus = SnapshotAPICorpus(
        findings=findings or [],
        recommendations=recommendations or [],
        replay_results=replay_results or [],
        page_refs=page_refs or [],
        omitted_findings_count=omitted_findings_count,
        omitted_recommendations_count=omitted_recommendations_count,
        omitted_replay_results_count=omitted_replay_results_count,
        omitted_page_refs_count=omitted_page_refs_count,
        corpus_evidence_quality=corpus_evidence_quality,
        next_cursor=next_cursor,
        blocked_by=blocked_by or [],
    )
    return api.build_snapshot(inputs, corpus)


def _view() -> GovernanceDashboardView:
    return GovernanceDashboardView()


# --- Section 1: module surface --------------------------------------------


def test_failure_id_literal_value() -> None:
    assert DASHBOARD_VIEW_FAILURE_ID == "governance_dashboard_view_failed"


def test_failure_id_typed_literal_annotation() -> None:
    """The typed Literal is the source of truth -- ensure the runtime
    value matches the literal range."""

    from iriai_build_v2.execution_control import (
        governance_dashboard_view as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    lit_args = typing.get_args(hints["DASHBOARD_VIEW_FAILURE_ID"])
    assert "governance_dashboard_view_failed" in lit_args


def test_all_exports_present() -> None:
    from iriai_build_v2.execution_control import (
        governance_dashboard_view as mod,
    )

    assert mod.__all__ == [
        "DASHBOARD_VIEW_FAILURE_ID",
        "DashboardFindingSummary",
        "DashboardRecommendationSummary",
        "DashboardReplayResultSummary",
        "DashboardViewInputs",
        "DashboardViewPayload",
        "DashboardViewGap",
        "DashboardViewResult",
        "GovernanceDashboardView",
    ]


def test_no_re_export_from_execution_control_init() -> None:
    """The Slice 19 3rd sub-slice surface lives in its own module; the
    execution_control package __init__.py is NOT mutated (preserves
    Slice 00-12 baselines)."""

    from iriai_build_v2 import execution_control as pkg

    init_exports = pkg.__all__ if hasattr(pkg, "__all__") else []
    forbidden = {
        "DASHBOARD_VIEW_FAILURE_ID",
        "GovernanceDashboardView",
        "DashboardViewPayload",
    }
    for name in forbidden:
        assert name not in init_exports, (
            f"{name} should NOT be re-exported from execution_control.__init__"
        )


# --- Section 2: DashboardFindingSummary --------------------------------


def test_finding_summary_construction_minimal() -> None:
    s = DashboardFindingSummary(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="cls",
        severity="medium",
        confidence=0.5,
        estimated_lost_hours=1.0,
    )
    assert s.idempotency_key == "k"
    assert s.kind == "workflow_inefficiency"
    assert s.class_name == "cls"
    assert s.severity == "medium"
    assert s.confidence == 0.5
    assert s.estimated_lost_hours == 1.0


def test_finding_summary_construction_with_none_lost_hours() -> None:
    s = DashboardFindingSummary(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="cls",
        severity="medium",
        confidence=0.5,
        estimated_lost_hours=None,
    )
    assert s.estimated_lost_hours is None


def test_finding_summary_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DashboardFindingSummary(
            idempotency_key="k",
            kind="workflow_inefficiency",
            class_name="cls",
            severity="medium",
            confidence=0.5,
            estimated_lost_hours=1.0,
            extra_field="bad",  # type: ignore[call-arg]
        )


def test_finding_summary_rejects_bad_kind_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardFindingSummary(
            idempotency_key="k",
            kind="not_a_kind",  # type: ignore[arg-type]
            class_name="cls",
            severity="medium",
            confidence=0.5,
            estimated_lost_hours=1.0,
        )


def test_finding_summary_rejects_bad_severity_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardFindingSummary(
            idempotency_key="k",
            kind="workflow_inefficiency",
            class_name="cls",
            severity="not_a_severity",  # type: ignore[arg-type]
            confidence=0.5,
            estimated_lost_hours=1.0,
        )


# --- Section 3: DashboardRecommendationSummary -------------------------


def test_recommendation_summary_construction_minimal() -> None:
    s = DashboardRecommendationSummary(
        idempotency_key="ik",
        recommendation_id="rid",
        consumer="scheduler",
        status="draft",
        confidence=0.5,
    )
    assert s.idempotency_key == "ik"
    assert s.recommendation_id == "rid"
    assert s.consumer == "scheduler"
    assert s.status == "draft"
    assert s.confidence == 0.5


def test_recommendation_summary_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DashboardRecommendationSummary(
            idempotency_key="ik",
            recommendation_id="rid",
            consumer="scheduler",
            status="draft",
            confidence=0.5,
            extra_field="bad",  # type: ignore[call-arg]
        )


def test_recommendation_summary_rejects_bad_consumer_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardRecommendationSummary(
            idempotency_key="ik",
            recommendation_id="rid",
            consumer="not_a_consumer",  # type: ignore[arg-type]
            status="draft",
            confidence=0.5,
        )


def test_recommendation_summary_rejects_bad_status_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardRecommendationSummary(
            idempotency_key="ik",
            recommendation_id="rid",
            consumer="scheduler",
            status="not_a_status",  # type: ignore[arg-type]
            confidence=0.5,
        )


# --- Section 4: DashboardReplayResultSummary ---------------------------


def test_replay_result_summary_construction_minimal() -> None:
    s = DashboardReplayResultSummary(
        result_id="rid",
        result_version="v1",
        scenario_id="sid",
        estimated_delta_hours=-1.5,
        estimated_risk_change="lower",
        confidence=0.7,
    )
    assert s.result_id == "rid"
    assert s.result_version == "v1"
    assert s.scenario_id == "sid"
    assert s.estimated_delta_hours == -1.5
    assert s.estimated_risk_change == "lower"
    assert s.confidence == 0.7


def test_replay_result_summary_construction_with_none_delta() -> None:
    s = DashboardReplayResultSummary(
        result_id="rid",
        result_version="v1",
        scenario_id="sid",
        estimated_delta_hours=None,
        estimated_risk_change="same",
        confidence=0.7,
    )
    assert s.estimated_delta_hours is None


def test_replay_result_summary_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DashboardReplayResultSummary(
            result_id="rid",
            result_version="v1",
            scenario_id="sid",
            estimated_delta_hours=-1.5,
            estimated_risk_change="lower",
            confidence=0.7,
            extra_field="bad",  # type: ignore[call-arg]
        )


def test_replay_result_summary_rejects_bad_risk_change_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardReplayResultSummary(
            result_id="rid",
            result_version="v1",
            scenario_id="sid",
            estimated_delta_hours=-1.5,
            estimated_risk_change="not_a_risk",  # type: ignore[arg-type]
            confidence=0.7,
        )


# --- Section 5: DashboardViewInputs construction -----------------------


def test_inputs_construction_minimal() -> None:
    src = _build_snapshot_via_api()
    inputs = DashboardViewInputs(source=src)
    assert inputs.source is src
    # Defaults per doc.
    assert inputs.max_display_findings == 10
    assert inputs.max_display_recommendations == 5
    assert inputs.max_display_replay_results == 5
    assert inputs.max_display_page_refs == 5
    assert inputs.max_display_blocked_by == 10


def test_inputs_construction_all_overrides() -> None:
    src = _build_snapshot_via_api()
    inputs = DashboardViewInputs(
        source=src,
        max_display_findings=3,
        max_display_recommendations=2,
        max_display_replay_results=4,
        max_display_page_refs=1,
        max_display_blocked_by=7,
    )
    assert inputs.max_display_findings == 3
    assert inputs.max_display_recommendations == 2
    assert inputs.max_display_replay_results == 4
    assert inputs.max_display_page_refs == 1
    assert inputs.max_display_blocked_by == 7


def test_inputs_extra_forbid() -> None:
    src = _build_snapshot_via_api()
    with pytest.raises(ValidationError):
        DashboardViewInputs(
            source=src,
            extra="bad",  # type: ignore[call-arg]
        )


# --- Section 6: DashboardViewPayload construction ----------------------


def test_payload_construction_minimal_valid() -> None:
    p = DashboardViewPayload(
        etag="e" * 64,
        corpus_id="c",
        snapshot_version="v1",
        generated_at=datetime.now(timezone.utc),
        snapshot_generated_at=datetime.now(timezone.utc),
        completeness="complete",
        evidence_quality="canonical",
        max_response_bytes=1024,
        truncated=False,
        omitted_counts={"findings": 0},
        page_refs=[],
        display_only=False,
    )
    assert p.etag == "e" * 64
    assert p.corpus_id == "c"
    assert p.snapshot_version == "v1"
    assert p.completeness == "complete"
    assert p.evidence_quality == "canonical"
    assert p.display_only is False
    assert p.scorecard_id is None
    assert p.next_cursor is None
    assert p.findings == []
    assert p.recommendations == []
    assert p.replay_results == []
    assert p.blocked_by == []


def test_payload_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DashboardViewPayload(
            etag="e",
            corpus_id="c",
            snapshot_version="v1",
            generated_at=datetime.now(timezone.utc),
            snapshot_generated_at=datetime.now(timezone.utc),
            completeness="complete",
            evidence_quality="canonical",
            max_response_bytes=1024,
            truncated=False,
            omitted_counts={},
            page_refs=[],
            display_only=False,
            extra="bad",  # type: ignore[call-arg]
        )


def test_payload_rejects_bad_completeness_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardViewPayload(
            etag="e",
            corpus_id="c",
            snapshot_version="v1",
            generated_at=datetime.now(timezone.utc),
            snapshot_generated_at=datetime.now(timezone.utc),
            completeness="not_a_state",  # type: ignore[arg-type]
            evidence_quality="canonical",
            max_response_bytes=1024,
            truncated=False,
            omitted_counts={},
            page_refs=[],
            display_only=False,
        )


def test_payload_rejects_bad_evidence_quality_literal() -> None:
    with pytest.raises(ValidationError):
        DashboardViewPayload(
            etag="e",
            corpus_id="c",
            snapshot_version="v1",
            generated_at=datetime.now(timezone.utc),
            snapshot_generated_at=datetime.now(timezone.utc),
            completeness="complete",
            evidence_quality="not_a_quality",  # type: ignore[arg-type]
            max_response_bytes=1024,
            truncated=False,
            omitted_counts={},
            page_refs=[],
            display_only=False,
        )


# --- Section 7: DashboardViewGap construction --------------------------


def test_gap_construction_minimal() -> None:
    g = DashboardViewGap(
        failure_id="governance_dashboard_view_failed",
        corpus_id="c",
        reason="payload_construction_failed",
        observed_at=datetime.now(timezone.utc),
    )
    assert g.failure_id == "governance_dashboard_view_failed"
    assert g.corpus_id == "c"
    assert g.reason == "payload_construction_failed"
    assert g.evidence_payload == {}


def test_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DashboardViewGap(
            failure_id="governance_dashboard_view_failed",
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
            extra="bad",  # type: ignore[call-arg]
        )


def test_gap_failure_id_literal_rejects_other_strings() -> None:
    with pytest.raises(ValidationError):
        DashboardViewGap(
            failure_id="some_other_id",  # type: ignore[arg-type]
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
        )


def test_gap_evidence_payload_round_trip() -> None:
    g = DashboardViewGap(
        failure_id="governance_dashboard_view_failed",
        corpus_id="c",
        reason="r",
        observed_at=datetime.now(timezone.utc),
        evidence_payload={"k": "v", "n": 42},
    )
    assert g.evidence_payload == {"k": "v", "n": 42}


# --- Section 8: DashboardViewResult construction -----------------------


def test_result_construction_minimal() -> None:
    r = DashboardViewResult()
    assert r.payload is None
    assert r.gap_findings == []


def test_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        DashboardViewResult(extra="bad")  # type: ignore[call-arg]


# --- Section 9: render() happy paths -----------------------------------


def test_render_minimal_happy_path() -> None:
    """Render a minimal empty snapshot -> typed payload with etag =
    snapshot_digest verbatim."""

    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.gap_findings == []
    assert result.payload.etag == src.snapshot.snapshot_digest
    assert result.payload.corpus_id == "test-corpus"


def test_render_populated_happy_path() -> None:
    """Render a populated snapshot -> typed payload with bounded
    typed summaries (refs-only projection)."""

    src = _build_snapshot_via_api(
        findings=[_finding()],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        page_refs=[_page_ref()],
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert len(result.payload.findings) == 1
    assert len(result.payload.recommendations) == 1
    assert len(result.payload.replay_results) == 1
    assert result.payload.page_refs == ["page-ref-19-3-1"]
    # Refs-only: summary types only.
    assert isinstance(
        result.payload.findings[0], DashboardFindingSummary
    )
    assert isinstance(
        result.payload.recommendations[0],
        DashboardRecommendationSummary,
    )
    assert isinstance(
        result.payload.replay_results[0],
        DashboardReplayResultSummary,
    )


def test_render_propagates_next_cursor() -> None:
    src = _build_snapshot_via_api(next_cursor="nc-123")
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.next_cursor == "nc-123"


def test_render_propagates_blocked_by() -> None:
    src = _build_snapshot_via_api(
        blocked_by=["stale_evidence:8ac124d6"],
        evidence_quality_override="stale",
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert "stale_evidence:8ac124d6" in result.payload.blocked_by
    assert result.payload.evidence_quality == "stale"


def test_render_preserves_scorecard_id() -> None:
    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", scorecard_id="sc-42"),
        SnapshotAPICorpus(),
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.scorecard_id == "sc-42"


def test_render_preserves_snapshot_version() -> None:
    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", snapshot_version="v3"),
        SnapshotAPICorpus(),
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.snapshot_version == "v3"


def test_render_preserves_max_response_bytes() -> None:
    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(
            corpus_id="c", max_response_bytes=999_999
        ),
        SnapshotAPICorpus(),
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.max_response_bytes == 999_999


def test_render_preserves_snapshot_generated_at() -> None:
    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert (
        result.payload.snapshot_generated_at
        == src.snapshot.generated_at
    )


# --- Section 10: ETag = snapshot_digest discipline ----------------------


def test_etag_equals_snapshot_digest_helper() -> None:
    src = _build_snapshot_via_api()
    etag = GovernanceDashboardView.compute_etag(src.snapshot)
    assert etag == src.snapshot.snapshot_digest


def test_etag_in_payload_equals_snapshot_digest() -> None:
    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.etag == src.snapshot.snapshot_digest


def test_etag_reproducible_same_snapshot() -> None:
    """Same snapshot -> same etag (doc-19:218 reproducibility)."""

    src1 = _build_snapshot_via_api(
        findings=[_finding(idempotency_key="k1")],
    )
    src2 = _build_snapshot_via_api(
        findings=[_finding(idempotency_key="k1")],
    )
    # Snapshots produced by the same inputs have the same digest.
    assert (
        src1.snapshot.snapshot_digest
        == src2.snapshot.snapshot_digest
    )
    view = _view()
    r1 = view.render(DashboardViewInputs(source=src1))
    r2 = view.render(DashboardViewInputs(source=src2))
    assert r1.payload is not None
    assert r2.payload is not None
    assert r1.payload.etag == r2.payload.etag


def test_etag_differs_when_snapshot_differs() -> None:
    src1 = _build_snapshot_via_api(corpus_id="a")
    src2 = _build_snapshot_via_api(corpus_id="b")
    view = _view()
    r1 = view.render(DashboardViewInputs(source=src1))
    r2 = view.render(DashboardViewInputs(source=src2))
    assert r1.payload is not None
    assert r2.payload is not None
    assert r1.payload.etag != r2.payload.etag


def test_compute_etag_is_static_method() -> None:
    """compute_etag is a static helper -- callers can invoke without
    constructing a view instance."""

    src = _build_snapshot_via_api()
    # Direct static-method call (no instance).
    etag = GovernanceDashboardView.compute_etag(src.snapshot)
    assert etag == src.snapshot.snapshot_digest


def test_compute_etag_directly_via_helper() -> None:
    """The typed snapshot_digest helper is the upstream source of
    truth; compute_etag returns its value verbatim."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key="kk")],
    )
    digest = compute_governance_snapshot_digest(
        corpus_id=src.snapshot.corpus_id,
        snapshot_version=src.snapshot.snapshot_version,
        scorecard_id=src.snapshot.scorecard_id,
        finding_idempotency_keys=[
            f.idempotency_key for f in src.snapshot.top_findings
        ],
        recommendation_idempotency_keys=[],
        replay_result_ids=[],
        replay_result_versions=[],
        omitted_counts=src.snapshot.omitted_counts,
        evidence_quality=src.snapshot.evidence_quality,
        completeness=src.snapshot.completeness,
    )
    assert GovernanceDashboardView.compute_etag(src.snapshot) == digest


# --- Section 11: bounded display-truncation discipline -----------------


def test_render_truncates_findings_below_snapshot_cap() -> None:
    """Dashboard display cap is tighter than snapshot cap; dashboard
    truncates further on top of the snapshot's own truncation."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(8)],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=3)
    )
    assert result.payload is not None
    assert len(result.payload.findings) == 3
    # Dashboard truncated 5 (8 - 3) on top of snapshot's 0.
    assert result.payload.omitted_counts["findings"] == 5
    assert result.payload.truncated is True


def test_render_truncates_recommendations() -> None:
    src = _build_snapshot_via_api(
        recommendations=[
            _recommendation(idempotency_key=f"r-{i}") for i in range(7)
        ],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(
            source=src, max_display_recommendations=2
        )
    )
    assert result.payload is not None
    assert len(result.payload.recommendations) == 2
    assert result.payload.omitted_counts["recommendations"] == 5


def test_render_truncates_replay_results() -> None:
    src = _build_snapshot_via_api(
        replay_results=[
            _replay_result(result_id=f"r-{i}") for i in range(7)
        ],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(
            source=src, max_display_replay_results=2
        )
    )
    assert result.payload is not None
    assert len(result.payload.replay_results) == 2
    assert result.payload.omitted_counts["replay_results"] == 5


def test_render_truncates_page_refs() -> None:
    src = _build_snapshot_via_api(
        page_refs=[
            _page_ref(page_ref_id=f"p-{i:02d}") for i in range(7)
        ],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_page_refs=2)
    )
    assert result.payload is not None
    assert len(result.payload.page_refs) == 2
    assert result.payload.omitted_counts["page_refs"] == 5


def test_render_truncates_blocked_by() -> None:
    src = _build_snapshot_via_api(
        blocked_by=[f"blocker-{i}" for i in range(7)],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_blocked_by=2)
    )
    assert result.payload is not None
    assert len(result.payload.blocked_by) == 2
    assert result.payload.omitted_counts["blocked_by"] == 5


def test_render_multi_level_truncation_accumulates() -> None:
    """Upstream snapshot truncation + dashboard display truncation
    are both counted in the dashboard payload's omitted_counts."""

    # Upstream snapshot truncated 3 findings; dashboard further
    # truncates the remaining.
    src = _build_snapshot_via_api(
        findings=[
            _finding(idempotency_key=f"k-{i}") for i in range(6)
        ],
        # Snapshot truncates to 4 (max_findings=4)
        max_findings=4,
        # And upstream pre-truncated 3 more
        omitted_findings_count=3,
    )
    # Snapshot omitted_counts["findings"] = (6 - 4) + 3 = 5
    assert src.snapshot.omitted_counts["findings"] == 5
    view = _view()
    # Dashboard truncates further: 4 -> 2; omits 2 more
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=2)
    )
    assert result.payload is not None
    assert len(result.payload.findings) == 2
    # Dashboard accumulates: snapshot's 5 + dashboard's 2 = 7
    assert result.payload.omitted_counts["findings"] == 7


def test_render_no_truncation_marks_complete() -> None:
    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.truncated is False
    assert result.payload.completeness == "complete"


def test_render_truncated_marks_truncated_true() -> None:
    src = _build_snapshot_via_api(
        findings=[
            _finding(idempotency_key=f"k-{i}") for i in range(8)
        ],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=3)
    )
    assert result.payload is not None
    assert result.payload.truncated is True


def test_render_preserves_completeness_from_snapshot() -> None:
    src = _build_snapshot_via_api(
        completeness_override="preview_only",
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.completeness == "preview_only"


def test_render_findings_ordering_preserved() -> None:
    src = _build_snapshot_via_api(
        findings=[
            _finding(idempotency_key=f"k-{i:02d}") for i in range(5)
        ],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=3)
    )
    assert result.payload is not None
    assert [f.idempotency_key for f in result.payload.findings] == [
        "k-00",
        "k-01",
        "k-02",
    ]


# --- Section 12: fail-closed / never-raises -----------------------------


def test_render_never_raises_on_empty_corpus_snapshot_missing() -> None:
    """When upstream snapshot API returned None snapshot + gap, the
    view emits a typed gap finding but does NOT raise."""

    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""),  # empty -> upstream gap
        SnapshotAPICorpus(),
    )
    assert src.snapshot is None
    assert len(src.gap_findings) == 1
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is None
    assert len(result.gap_findings) >= 1
    # First gap is dashboard's own upstream_snapshot_missing.
    assert (
        result.gap_findings[0].reason == "upstream_snapshot_missing"
    )


def test_render_propagates_upstream_gap_findings_verbatim() -> None:
    """Upstream gap findings are mirrored onto typed DashboardViewGap
    records (same reason, same corpus_id, same observed_at)."""

    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="   "),  # whitespace -> gap
        SnapshotAPICorpus(),
    )
    assert src.snapshot is None
    upstream_reason = src.gap_findings[0].reason
    upstream_corpus_id = src.gap_findings[0].corpus_id
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    # Dashboard emits its own + propagates upstream
    assert len(result.gap_findings) == 2
    propagated = result.gap_findings[1]
    assert propagated.reason == upstream_reason
    assert propagated.corpus_id == upstream_corpus_id
    assert (
        propagated.evidence_payload.get("propagated_from")
        == "snapshot_api_gap"
    )


def test_render_dashboard_view_failure_id_used() -> None:
    """The typed DASHBOARD_VIEW_FAILURE_ID is the failure id used on
    every typed gap row."""

    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    for gap in result.gap_findings:
        assert gap.failure_id == "governance_dashboard_view_failed"


def test_render_corpus_id_unknown_when_no_upstream_gaps() -> None:
    """Edge case: snapshot is None but no upstream gap findings -> the
    dashboard gap's corpus_id falls back to '<unknown>'."""

    # Construct a synthetic SnapshotAPIResult with None snapshot + no
    # upstream gaps (this is structurally invalid for the snapshot API
    # but valid for the dashboard view's input contract).
    src = SnapshotAPIResult(snapshot=None, gap_findings=[])
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].corpus_id == "<unknown>"


# --- Section 13: DIRECT annotation-identity REUSE ----------------------


def test_inputs_source_reuses_slice_19_2nd_snapshot_api_result() -> None:
    hints = typing.get_type_hints(DashboardViewInputs)
    ann = hints["source"]
    assert ann is SnapshotAPIResult


def test_payload_completeness_reuses_slice_13a_literal() -> None:
    hints = typing.get_type_hints(DashboardViewPayload)
    ann = hints["completeness"]
    assert ann is CompletenessState


def test_payload_evidence_quality_reuses_slice_13a_literal() -> None:
    hints = typing.get_type_hints(DashboardViewPayload)
    ann = hints["evidence_quality"]
    assert ann is EvidenceQuality


def test_payload_findings_reuses_dashboard_finding_summary() -> None:
    hints = typing.get_type_hints(DashboardViewPayload)
    ann = hints["findings"]
    args = typing.get_args(ann)
    assert args[0] is DashboardFindingSummary


def test_payload_recommendations_reuses_dashboard_recommendation_summary() -> None:
    hints = typing.get_type_hints(DashboardViewPayload)
    ann = hints["recommendations"]
    args = typing.get_args(ann)
    assert args[0] is DashboardRecommendationSummary


def test_payload_replay_results_reuses_dashboard_replay_result_summary() -> None:
    hints = typing.get_type_hints(DashboardViewPayload)
    ann = hints["replay_results"]
    args = typing.get_args(ann)
    assert args[0] is DashboardReplayResultSummary


def test_finding_summary_kind_reuses_slice_16_literal() -> None:
    hints = typing.get_type_hints(DashboardFindingSummary)
    ann = hints["kind"]
    assert ann is FindingKind


def test_finding_summary_severity_reuses_slice_16_literal() -> None:
    hints = typing.get_type_hints(DashboardFindingSummary)
    ann = hints["severity"]
    assert ann is FindingSeverity


def test_recommendation_summary_consumer_reuses_slice_17_literal() -> None:
    hints = typing.get_type_hints(DashboardRecommendationSummary)
    ann = hints["consumer"]
    assert ann is PolicyConsumer


def test_recommendation_summary_status_reuses_slice_17_literal() -> None:
    hints = typing.get_type_hints(DashboardRecommendationSummary)
    ann = hints["status"]
    assert ann is PolicyRecommendationStatus


def test_replay_summary_risk_change_reuses_slice_18_literal() -> None:
    hints = typing.get_type_hints(DashboardReplayResultSummary)
    ann = hints["estimated_risk_change"]
    assert ann is RiskChange


def test_result_payload_reuses_dashboard_view_payload() -> None:
    hints = typing.get_type_hints(DashboardViewResult)
    ann = hints["payload"]
    union_args = typing.get_args(ann)
    payload_t = next(a for a in union_args if a is not type(None))
    assert payload_t is DashboardViewPayload


def test_compute_etag_signature_reuses_slice_19_first_snapshot() -> None:
    """compute_etag annotation accepts the typed Slice 19 1st sub-slice
    GovernanceSnapshot directly (no second source of truth)."""

    hints = typing.get_type_hints(GovernanceDashboardView.compute_etag)
    assert hints["snapshot"] is GovernanceSnapshot


# --- Section 14: failure-router 4-pure-data-add discipline -------------


def test_failure_router_failure_id_in_typed_literal() -> None:
    """`governance_dashboard_view_failed` is in the FailureType Literal."""

    assert "governance_dashboard_view_failed" in typing.get_args(
        FailureType
    )


def test_failure_router_failure_id_in_failure_types_tuple() -> None:
    assert "governance_dashboard_view_failed" in FAILURE_TYPES


def test_failure_router_route_table_uses_evidence_corruption_class() -> None:
    key = ("evidence_corruption", "governance_dashboard_view_failed")
    assert key in ROUTE_TABLE


def test_failure_router_route_table_uses_retry_governance_projection() -> None:
    key = ("evidence_corruption", "governance_dashboard_view_failed")
    route = ROUTE_TABLE[key]
    assert route.action == "retry_governance_projection"


def test_failure_router_route_action_in_typed_literal() -> None:
    """retry_governance_projection (REUSED from Slice 14 2nd sub-slice;
    NOT a new action)."""

    assert "retry_governance_projection" in typing.get_args(RouteAction)


def test_failure_router_includes_retryable_set() -> None:
    """Verifies new failure id is in the _RETRYABLE_FAILURE_TYPES set
    (the typed retry-budget surface). Imports the private set
    structurally to enforce the 4-add discipline."""

    from iriai_build_v2.workflows.develop.execution import (
        failure_router as fr,
    )

    assert (
        "governance_dashboard_view_failed"
        in fr._RETRYABLE_FAILURE_TYPES
    )


def test_failure_router_thirteen_governance_failure_ids_total() -> None:
    """5 Slice 17 + 6 Slice 18 + 1 Slice 19 2nd + 1 Slice 19 3rd =
    13 typed governance failure ids routing to
    retry_governance_projection (Slice 17+ governance failure ids;
    excludes Slice 14/15/16 governance projection observer ids)."""

    slice_17_plus = {
        "recommendation_builder_emission_failed",
        "policy_validation_failed",
        "decision_record_persistence_failed",
        "replay_requirement_validation_failed",
        "consumer_read_api_failed",
        "replay_corpus_or_scenario_load_failed",
        "summary_replay_failed",
        "event_replay_failed",
        "metrics_comparator_failed",
        "counterfactual_result_persistence_failed",
        "recommendation_citation_validation_failed",
        "governance_snapshot_api_failed",
        "governance_dashboard_view_failed",
    }
    gov_routed = {
        ft
        for (cls, ft), r in ROUTE_TABLE.items()
        if r.action == "retry_governance_projection"
    }
    assert slice_17_plus <= gov_routed
    assert len(slice_17_plus & gov_routed) == 13


# --- Section 15: doc-19 acceptance criteria enforcement ----------------


def test_ac1_payload_etag_for_reproducibility() -> None:
    """Doc-19:224 AC1 -- 'Reports are bounded, reproducible, evidence-
    cited, and structured first.'

    Reproducibility: etag == snapshot_digest (verbatim).
    Evidence-cited: typed summary surface + typed page_refs.
    Bounded: max_response_bytes + truncated + omitted_counts.
    Structured first: typed Pydantic BaseModel surface.
    """

    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    # Reproducible
    assert result.payload.etag == src.snapshot.snapshot_digest
    # Bounded
    assert hasattr(result.payload, "max_response_bytes")
    assert hasattr(result.payload, "truncated")
    assert hasattr(result.payload, "omitted_counts")
    # Structured first
    assert isinstance(result.payload, BaseModel)


def test_ac2_display_only_true_when_truncated_without_page_refs() -> None:
    """Doc-19:225-226 AC2 -- truncated payload without exact page refs
    is display-only per doc-19:128-131."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(8)],
        # No page_refs
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=3)
    )
    assert result.payload is not None
    assert result.payload.truncated is True
    assert result.payload.page_refs == []
    assert result.payload.display_only is True


def test_ac2_display_only_false_when_truncated_with_page_refs() -> None:
    """Doc-19:225-226 AC2 -- truncated payload WITH page_refs is NOT
    display-only (consumers can drill-through)."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(8)],
        page_refs=[_page_ref()],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=3)
    )
    assert result.payload is not None
    assert result.payload.truncated is True
    assert result.payload.page_refs
    assert result.payload.display_only is False


def test_ac2_display_only_true_when_completeness_preview_only() -> None:
    src = _build_snapshot_via_api(
        completeness_override="preview_only",
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.completeness == "preview_only"
    assert result.payload.display_only is True


def test_ac2_display_only_true_when_completeness_unavailable() -> None:
    src = _build_snapshot_via_api(
        completeness_override="unavailable",
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.completeness == "unavailable"
    assert result.payload.display_only is True


def test_ac2_display_only_false_when_complete() -> None:
    src = _build_snapshot_via_api()  # no truncation; complete
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.completeness == "complete"
    assert result.payload.display_only is False


def test_ac6_evidence_quality_always_present() -> None:
    """Doc-19:232-233 AC6 -- evidence_quality + omitted_counts ALWAYS
    present (typed surface; required field)."""

    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.evidence_quality in typing.get_args(
        EvidenceQuality
    )
    assert isinstance(result.payload.omitted_counts, dict)


def test_ac6_omitted_counts_always_populates_5_keys() -> None:
    """The 5 typed truncation dimensions are always tracked in
    omitted_counts (findings + recommendations + replay_results +
    page_refs + blocked_by)."""

    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert set(result.payload.omitted_counts.keys()) == {
        "findings",
        "recommendations",
        "replay_results",
        "page_refs",
        "blocked_by",
    }


def test_ac7_no_control_plane_writer_methods_extension() -> None:
    """Doc-19:234 + doc-19:348-349 AC -- the dashboard view class has
    ONLY render + compute_etag public methods; no mutation methods;
    no CONTROL_PLANE_WRITER_METHODS extension."""

    public_methods = sorted(
        name
        for name in dir(GovernanceDashboardView)
        if not name.startswith("_")
    )
    assert public_methods == ["compute_etag", "render"]


def test_ac7_refs_only_no_body_hydration() -> None:
    """Doc-19:234 AC7 -- bounded-reads + refs-only contract.

    The view surfaces only typed page_ref_id strings (not full page-ref
    bodies) on DashboardViewPayload.page_refs per doc-19:81."""

    src = _build_snapshot_via_api(page_refs=[_page_ref()])
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.page_refs == ["page-ref-19-3-1"]
    assert all(isinstance(p, str) for p in result.payload.page_refs)


def test_doc_19_170_171_etag_is_snapshot_digest_verbatim() -> None:
    """Doc-19:170-171 -- 'The ETag seed is snapshot_digest.'

    The typed surface enforces etag == snapshot_digest structurally."""

    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert result.payload.etag == src.snapshot.snapshot_digest


def test_doc_19_218_reproducible_same_corpus_id() -> None:
    """Doc-19:218 Tests -- 'Report generation is reproducible for the
    same corpus id.'

    Two render calls with the same upstream snapshot produce identical
    etags."""

    src = _build_snapshot_via_api(
        findings=[
            _finding(idempotency_key=f"k-{i}") for i in range(2)
        ],
    )
    view = _view()
    etags = [
        view.render(DashboardViewInputs(source=src)).payload.etag
        for _ in range(5)
    ]
    assert len(set(etags)) == 1


# --- Section 16: activation-authority boundary -------------------------


def test_module_has_no_mutation_methods() -> None:
    """Per the Slice 17 7th sub-slice activation-boundary discipline +
    doc-19:348-349 AC the dashboard view has NO mutation methods on
    any of its typed shapes."""

    forbidden_method_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "checkpoint_",
        "mutate_",
        "write_",
        "persist_",
    )
    for cls in (
        DashboardFindingSummary,
        DashboardRecommendationSummary,
        DashboardReplayResultSummary,
        DashboardViewInputs,
        DashboardViewPayload,
        DashboardViewGap,
        DashboardViewResult,
    ):
        for name in dir(cls):
            if name.startswith("_") or name.startswith("model_"):
                continue
            assert not name.startswith(forbidden_method_prefixes), (
                f"{cls.__name__}.{name} resembles a mutation method "
                "(per AC7 + AC: doc-19:348-349)"
            )


def test_no_dag_artifact_key_literals() -> None:
    """Per doc-19:348-349 the typed dashboard view does NOT mint
    dag-execution-authority artifact-key string literals."""

    import inspect

    from iriai_build_v2.execution_control import (
        governance_dashboard_view as mod,
    )

    src = inspect.getsource(mod)
    forbidden_artifact_keys = (
        '"dag-group:',
        '"dag-task-spec:',
        '"dag-task:',
        '"dag-evidence:',
        '"dag-commit-proof:',
    )
    for bad in forbidden_artifact_keys:
        assert bad not in src, (
            f"Forbidden dag-* artifact key literal {bad!r} found in "
            f"governance_dashboard_view.py (per doc-19:348-349 AC)."
        )


# --- Section 17: helper-level (truncation/projection) tests ------------


def test_project_findings_handles_negative_cap() -> None:
    view = _view()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    summaries, omitted = view._project_findings(rows, -5)
    assert summaries == []
    assert omitted == 3


def test_project_findings_handles_zero_cap() -> None:
    view = _view()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    summaries, omitted = view._project_findings(rows, 0)
    assert summaries == []
    assert omitted == 3


def test_project_findings_handles_exact_cap() -> None:
    view = _view()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    summaries, omitted = view._project_findings(rows, 3)
    assert len(summaries) == 3
    assert omitted == 0


def test_project_findings_handles_overcap() -> None:
    view = _view()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(5)]
    summaries, omitted = view._project_findings(rows, 2)
    assert len(summaries) == 2
    assert omitted == 3
    # Ordering preserved
    assert summaries[0].idempotency_key == "k-0"
    assert summaries[1].idempotency_key == "k-1"


def test_project_recommendations_extracts_identity_surface() -> None:
    view = _view()
    rows = [_recommendation()]
    summaries, omitted = view._project_recommendations(rows, 5)
    assert len(summaries) == 1
    assert summaries[0].idempotency_key == "recommendation-key-19-3-a"
    assert summaries[0].recommendation_id == "rec-19-3-1"
    assert summaries[0].consumer == "scheduler"
    assert summaries[0].status == "draft"


def test_project_replay_results_extracts_identity_surface() -> None:
    view = _view()
    rows = [_replay_result()]
    summaries, omitted = view._project_replay_results(rows, 5)
    assert len(summaries) == 1
    assert summaries[0].result_id == "result-19-3-1"
    assert summaries[0].result_version == "v1"
    assert summaries[0].scenario_id == "scenario-19-3-1"
    assert summaries[0].estimated_risk_change == "lower"


def test_truncate_string_list_handles_negative_cap() -> None:
    view = _view()
    rows = ["a", "b", "c"]
    truncated, omitted = view._truncate_string_list(rows, -1)
    assert truncated == []
    assert omitted == 3


def test_truncate_string_list_handles_overcap() -> None:
    view = _view()
    rows = ["a", "b", "c", "d", "e"]
    truncated, omitted = view._truncate_string_list(rows, 2)
    assert truncated == ["a", "b"]
    assert omitted == 3


def test_derive_display_only_preview_only() -> None:
    assert GovernanceDashboardView._derive_display_only(
        completeness="preview_only", truncated=False, page_refs=["p"]
    ) is True


def test_derive_display_only_unavailable() -> None:
    assert GovernanceDashboardView._derive_display_only(
        completeness="unavailable", truncated=False, page_refs=[]
    ) is True


def test_derive_display_only_truncated_no_page_refs() -> None:
    assert GovernanceDashboardView._derive_display_only(
        completeness="paged", truncated=True, page_refs=[]
    ) is True


def test_derive_display_only_truncated_with_page_refs() -> None:
    assert GovernanceDashboardView._derive_display_only(
        completeness="paged", truncated=True, page_refs=["p"]
    ) is False


def test_derive_display_only_complete() -> None:
    assert GovernanceDashboardView._derive_display_only(
        completeness="complete", truncated=False, page_refs=[]
    ) is False


def test_corpus_id_from_upstream_gaps_empty() -> None:
    assert (
        GovernanceDashboardView._corpus_id_from_upstream_gaps([])
        == "<unknown>"
    )


def test_corpus_id_from_upstream_gaps_first() -> None:
    gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="abc",
        reason="x",
        observed_at=datetime.now(timezone.utc),
    )
    assert (
        GovernanceDashboardView._corpus_id_from_upstream_gaps([gap])
        == "abc"
    )


# --- Section 18: GovernanceDashboardView instance is stateless --------


def test_view_instance_can_be_reused() -> None:
    src1 = _build_snapshot_via_api(corpus_id="a")
    src2 = _build_snapshot_via_api(corpus_id="b")
    view = _view()
    r1 = view.render(DashboardViewInputs(source=src1))
    r2 = view.render(DashboardViewInputs(source=src2))
    assert r1.payload is not None
    assert r2.payload is not None
    assert r1.payload.corpus_id == "a"
    assert r2.payload.corpus_id == "b"
    assert r1.payload.etag != r2.payload.etag


def test_view_has_no_instance_state() -> None:
    view = _view()
    assert vars(view) == {}


# --- Section 19: end-to-end typed snapshot -> dashboard payload ------


def test_end_to_end_via_snapshot_api() -> None:
    """Full pipeline: build via Slice 19 2nd sub-slice API -> render
    via Slice 19 3rd sub-slice view -> verify the typed payload
    fields populate correctly."""

    src = _build_snapshot_via_api(
        corpus_id="e2e-corpus",
        findings=[_finding(idempotency_key="f1")],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        page_refs=[_page_ref()],
        corpus_evidence_quality="sampled",
    )
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    p = result.payload
    assert p.corpus_id == "e2e-corpus"
    assert p.etag == src.snapshot.snapshot_digest
    assert p.evidence_quality == "sampled"
    assert len(p.findings) == 1
    assert p.findings[0].idempotency_key == "f1"
    assert len(p.recommendations) == 1
    assert len(p.replay_results) == 1
    assert p.page_refs == ["page-ref-19-3-1"]
    assert p.display_only is False
    assert p.truncated is False


def test_end_to_end_with_truncation_and_page_refs() -> None:
    """Truncated dashboard payload with page_refs is NOT display-only."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(8)],
        page_refs=[_page_ref()],
    )
    view = _view()
    result = view.render(
        DashboardViewInputs(source=src, max_display_findings=2)
    )
    assert result.payload is not None
    assert result.payload.truncated is True
    assert result.payload.page_refs
    # Not display-only because page_refs are present.
    assert result.payload.display_only is False


# --- Section 20: snapshot generated_at preserved -----------------------


def test_payload_carries_two_distinct_timestamps() -> None:
    """The payload preserves BOTH the snapshot's generated_at and the
    view's own generated_at (so stale-snapshot detection works)."""

    src = _build_snapshot_via_api()
    view = _view()
    result = view.render(DashboardViewInputs(source=src))
    assert result.payload is not None
    assert (
        result.payload.snapshot_generated_at
        == src.snapshot.generated_at
    )
    # View's generated_at is set after the snapshot.
    assert (
        result.payload.generated_at
        >= result.payload.snapshot_generated_at
    )
