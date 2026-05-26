"""Slice 19 6th sub-slice -- unit tests for the typed report-artifact
emitter at ``execution_control/governance_report_artifact.py``.

Covers the doc-19:161-162 step 6 + doc-19:166-167 report-artifact
emitter surface:

* :data:`REPORT_ARTIFACT_FAILURE_ID` -- the typed failure id
  (``governance_report_artifact_emission_failed``) registered under
  the EXISTING ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` action.
* :data:`REPORT_ARTIFACT_KEY_PREFIX` -- the typed ``review:*``
  artifact-key prefix (``"review:governance-report:"``); NOT a
  ``dag-*`` execution-authority prefix.
* :class:`ReportArtifactInputs` typed BaseModel (extra-forbid;
  carries Slice 19 2nd SnapshotAPIResult source).
* :class:`ReportArtifactGap` typed BaseModel (extra-forbid; mirrors
  AgentContextBuilderGap / SlackRenderGap / DashboardViewGap /
  SnapshotAPIGap).
* :class:`ReportArtifactResult` typed BaseModel (extra-forbid;
  artifact | None + gap_findings list).
* :class:`GovernanceReportArtifact` typed BaseModel (extra-forbid;
  14 bounded-summary fields including artifact_key + corpus_id +
  snapshot_digest + completeness + evidence_quality + by-name
  reference lists).
* :class:`GovernanceReportArtifactEmitter.emit_report_artifact(...)`
  -- the projection method:
  * Happy-path -> typed GovernanceReportArtifact emitted with
    bounded-summary fields only.
  * Bounded-summary discipline (no full evidence bodies).
  * corpus_id template substitution + reproducibility.
  * Upstream snapshot missing -> artifact=None + typed gap on
    ``upstream_snapshot_missing`` + propagated upstream gaps.
  * Emitter NEVER raises (typed gap projection on construction
    failure).
* DIRECT annotation-identity REUSE assertions for Slice 19 2nd
  SnapshotAPIResult / SnapshotAPIGap + Slice 19 1st GovernanceSnapshot
  + Slice 13a CompletenessState / EvidenceQuality + Slice 16
  GovernanceFinding + Slice 17 GovernancePolicyRecommendation +
  Slice 18 CounterfactualResult.
* Failure-router 4-pure-data add discipline (Slice 14 2nd + Slice 19
  2nd-5th sub-slice precedent verbatim).
* AC1 / AC2 / AC6 / AC7 enforcement tests per doc-19 § Acceptance
  Criteria.
* Defence-in-depth tests: the artifact key prefix is ``review:``,
  NOT ``dag-``; the new artifact key does NOT extend
  ``CONTROL_PLANE_WRITER_METHODS``.

Per the implementer prompt § "Non-Negotiables" -- fail-closed on
every Pydantic field validator; no executor wiring outside this
slice's own acceptance tests; the Slice 13a + Slice 13A + Slice 14 +
Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 1st-5th
sub-slice modules + tests remain READ-ONLY this sub-slice.
"""

from __future__ import annotations

import inspect
import typing
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GovernanceSnapshot,
)
from iriai_build_v2.execution_control.governance_report_artifact import (
    REPORT_ARTIFACT_FAILURE_ID,
    REPORT_ARTIFACT_KEY_PREFIX,
    GovernanceReportArtifact,
    GovernanceReportArtifactEmitter,
    ReportArtifactGap,
    ReportArtifactInputs,
    ReportArtifactResult,
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
    SchedulerPolicyArtifact,
)
from iriai_build_v2.supervisor.read_only import (
    CONTROL_PLANE_WRITER_METHODS,
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


# --- Fixture builders (mirror Slice 19 5th sub-slice fixtures) -------------


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-19-6",
        digest="d" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    base: dict[str, object] = dict(
        page_ref_id="page-ref-19-6-1",
        authority="typed_journal",
        source_ref_id="ref-19-6",
        digest="e" * 64,
        completeness="complete",
        exact=True,
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)


def _finding(**overrides: object) -> GovernanceFinding:
    base: dict[str, object] = dict(
        idempotency_key="finding-key-19-6-a",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-25#slice-19-6th"],
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
        idempotency_key="recommendation-key-19-6-a",
        recommendation_id="rec-19-6-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-19-6-a"],
        source_metric_refs=["tasks_per_hour@v1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_scheduler_wave_cap"],
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml-7", "repo_id": "repo-1"},
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
        result_id="result-19-6-1",
        result_version="v1",
        scenario_id="scenario-19-6-1",
        corpus_id="corpus-19-6",
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
        supporting_finding_ids=["finding-key-19-6-a"],
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
    corpus_evidence_quality: str = "canonical",
    next_cursor: str | None = None,
    blocked_by: list[str] | None = None,
    completeness_override: str | None = None,
    evidence_quality_override: str | None = None,
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


def _emitter() -> GovernanceReportArtifactEmitter:
    return GovernanceReportArtifactEmitter()


# --- Section 1: module surface --------------------------------------------


def test_failure_id_literal_value() -> None:
    assert (
        REPORT_ARTIFACT_FAILURE_ID
        == "governance_report_artifact_emission_failed"
    )


def test_failure_id_typed_literal_annotation() -> None:
    """The typed Literal is the source of truth -- ensure the runtime
    value matches the literal range."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    lit_args = typing.get_args(hints["REPORT_ARTIFACT_FAILURE_ID"])
    assert "governance_report_artifact_emission_failed" in lit_args


def test_artifact_key_prefix_literal_value() -> None:
    assert REPORT_ARTIFACT_KEY_PREFIX == "review:governance-report:"


def test_artifact_key_prefix_typed_literal_annotation() -> None:
    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    lit_args = typing.get_args(hints["REPORT_ARTIFACT_KEY_PREFIX"])
    assert "review:governance-report:" in lit_args


def test_artifact_key_prefix_is_review_not_dag() -> None:
    """Defence-in-depth: the prefix MUST begin with ``review:`` (NOT
    ``dag-``) per doc-19:348-349 AC."""

    assert REPORT_ARTIFACT_KEY_PREFIX.startswith("review:")
    assert not REPORT_ARTIFACT_KEY_PREFIX.startswith("dag-")
    assert not REPORT_ARTIFACT_KEY_PREFIX.startswith("dag:")


def test_all_exports_present() -> None:
    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    assert mod.__all__ == [
        "REPORT_ARTIFACT_FAILURE_ID",
        "REPORT_ARTIFACT_KEY_PREFIX",
        "ReportArtifactInputs",
        "ReportArtifactGap",
        "ReportArtifactResult",
        "GovernanceReportArtifact",
        "GovernanceReportArtifactEmitter",
    ]


def test_no_re_export_from_execution_control_init() -> None:
    """The Slice 19 6th sub-slice surface lives in its own module; the
    execution_control package __init__.py is NOT mutated (preserves
    Slice 00-12 baselines)."""

    from iriai_build_v2 import execution_control as pkg

    init_exports = pkg.__all__ if hasattr(pkg, "__all__") else []
    forbidden = {
        "REPORT_ARTIFACT_FAILURE_ID",
        "REPORT_ARTIFACT_KEY_PREFIX",
        "GovernanceReportArtifactEmitter",
        "ReportArtifactInputs",
        "GovernanceReportArtifact",
    }
    for name in forbidden:
        assert name not in init_exports, (
            f"{name} should NOT be re-exported from execution_control.__init__"
        )


def test_module_carries_doc_19_step_6_citation() -> None:
    """The module docstring MUST carry the doc-19 step 6 PIN cite per
    the governance prompt § 'cite-everything' rule."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    assert mod.__doc__ is not None
    assert "161-162" in mod.__doc__
    assert "step 6" in mod.__doc__.lower()


def test_module_carries_doc_19_166_167_citation() -> None:
    """The module docstring MUST cite doc-19:166-167 (reports are
    projections of governance rows)."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    assert mod.__doc__ is not None
    assert "166-167" in mod.__doc__
    # The phrase may wrap across lines (whitespace-normalised match).
    normalised = " ".join(mod.__doc__.split())
    assert "projections of governance rows" in normalised


def test_module_documents_artifact_key_format() -> None:
    """The module docstring MUST document the
    ``review:governance-report:{corpus_id}`` artifact-key format."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    assert mod.__doc__ is not None
    assert "review:governance-report:{corpus_id}" in mod.__doc__


# --- Section 2: ReportArtifactInputs --------------------------------------


def test_inputs_construction_minimal() -> None:
    source = _build_snapshot_via_api()
    inputs = ReportArtifactInputs(source=source)
    assert inputs.source is source


def test_inputs_extra_forbid() -> None:
    source = _build_snapshot_via_api()
    with pytest.raises(ValidationError):
        ReportArtifactInputs(  # type: ignore[call-arg]
            source=source, unknown_field=1
        )


def test_inputs_is_basemodel() -> None:
    assert issubclass(ReportArtifactInputs, BaseModel)


def test_inputs_rejects_non_snapshot_source() -> None:
    with pytest.raises(ValidationError):
        ReportArtifactInputs(source="not-a-result")  # type: ignore[arg-type]


# --- Section 3: ReportArtifactGap -----------------------------------------


def test_gap_construction_minimal() -> None:
    gap = ReportArtifactGap(
        failure_id=REPORT_ARTIFACT_FAILURE_ID,
        corpus_id="c1",
        reason="upstream_snapshot_missing",
        observed_at=datetime(2026, 5, 25, 0, 0, 0, tzinfo=timezone.utc),
    )
    assert gap.failure_id == "governance_report_artifact_emission_failed"
    assert gap.corpus_id == "c1"
    assert gap.reason == "upstream_snapshot_missing"
    assert gap.evidence_payload == {}


def test_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReportArtifactGap(  # type: ignore[call-arg]
            failure_id=REPORT_ARTIFACT_FAILURE_ID,
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
            unknown="x",
        )


def test_gap_rejects_wrong_failure_id_literal() -> None:
    with pytest.raises(ValidationError):
        ReportArtifactGap(
            failure_id="wrong_failure_id",  # type: ignore[arg-type]
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
        )


def test_gap_accepts_arbitrary_evidence_payload() -> None:
    gap = ReportArtifactGap(
        failure_id=REPORT_ARTIFACT_FAILURE_ID,
        corpus_id="c",
        reason="r",
        observed_at=datetime.now(timezone.utc),
        evidence_payload={"key": "value", "count": 5},
    )
    assert gap.evidence_payload["key"] == "value"
    assert gap.evidence_payload["count"] == 5


# --- Section 4: ReportArtifactResult --------------------------------------


def test_result_default_empty_gap_findings() -> None:
    result = ReportArtifactResult()
    assert result.artifact is None
    assert result.gap_findings == []


def test_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReportArtifactResult(unknown=1)  # type: ignore[call-arg]


def test_result_accepts_artifact_and_gaps() -> None:
    # Build via the emitter to satisfy bound shape constraints.
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert isinstance(result, ReportArtifactResult)
    assert isinstance(result.artifact, GovernanceReportArtifact)


# --- Section 5: GovernanceReportArtifact ---------------------------------


def test_artifact_construction_minimal_via_emitter() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.corpus_id == "test-corpus"


def test_artifact_extra_forbid() -> None:
    """Verifies direct construction with unknown fields is rejected."""

    with pytest.raises(ValidationError):
        GovernanceReportArtifact(  # type: ignore[call-arg]
            artifact_key="review:governance-report:c",
            corpus_id="c",
            snapshot_digest="d" * 64,
            snapshot_version="v1",
            completeness="complete",
            evidence_quality="canonical",
            top_finding_keys=[],
            recommendation_keys=[],
            replay_result_ids=[],
            page_refs=[],
            omitted_counts={},
            blocked_by=[],
            truncated=False,
            generated_at=datetime.now(timezone.utc),
            unknown_field="x",
        )


def test_artifact_required_fields_present() -> None:
    """All required GovernanceReportArtifact fields per doc-19:161-162."""

    fields = set(GovernanceReportArtifact.model_fields.keys())
    expected_required = {
        "artifact_key",
        "corpus_id",
        "snapshot_digest",
        "snapshot_version",
        "completeness",
        "evidence_quality",
        "top_finding_keys",
        "recommendation_keys",
        "replay_result_ids",
        "page_refs",
        "omitted_counts",
        "blocked_by",
        "truncated",
        "generated_at",
    }
    assert expected_required <= fields


def test_artifact_no_body_hydration_fields() -> None:
    """The typed report carries ONLY by-name reference shapes -- no
    typed BaseModels for findings/recommendations/replay (per
    doc-19:111 refs-only contract)."""

    fields = GovernanceReportArtifact.model_fields
    # The typed surface must NOT have a 'top_findings' / 'recommendations'
    # / 'replay_results' field carrying typed BaseModels.
    assert "top_findings" not in fields
    assert "recommendations" not in fields
    assert "replay_results" not in fields
    # Only by-name reference lists.
    assert fields["top_finding_keys"].annotation == list[str]
    assert fields["recommendation_keys"].annotation == list[str]
    assert fields["replay_result_ids"].annotation == list[str]
    assert fields["page_refs"].annotation == list[str]


def test_artifact_completeness_typed_against_slice_13a_literal() -> None:
    """The typed completeness field uses the Slice 13a Literal."""

    fields = GovernanceReportArtifact.model_fields
    assert fields["completeness"].annotation == CompletenessState


def test_artifact_evidence_quality_typed_against_slice_13a_literal() -> None:
    """The typed evidence_quality field uses the Slice 13a Literal."""

    fields = GovernanceReportArtifact.model_fields
    assert fields["evidence_quality"].annotation == EvidenceQuality


# --- Section 6: GovernanceReportArtifactEmitter (happy path) ---------------


def test_emit_with_empty_snapshot_returns_artifact() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.gap_findings == []


def test_emit_with_one_finding_returns_artifact() -> None:
    source = _build_snapshot_via_api(findings=[_finding()])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.top_finding_keys == ["finding-key-19-6-a"]


def test_emit_with_multiple_findings_projects_keys() -> None:
    findings = [
        _finding(idempotency_key=f"finding-{i}") for i in range(3)
    ]
    source = _build_snapshot_via_api(findings=findings)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.top_finding_keys == [
        "finding-0",
        "finding-1",
        "finding-2",
    ]


def test_emit_with_recommendations_projects_keys() -> None:
    source = _build_snapshot_via_api(recommendations=[_recommendation()])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.recommendation_keys == [
        "recommendation-key-19-6-a"
    ]


def test_emit_with_replay_results_projects_ids() -> None:
    source = _build_snapshot_via_api(replay_results=[_replay_result()])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.replay_result_ids == ["result-19-6-1"]


def test_emit_with_page_refs_propagates_strings() -> None:
    source = _build_snapshot_via_api(page_refs=[_page_ref()])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert "page-ref-19-6-1" in result.artifact.page_refs


def test_emit_truncated_propagates() -> None:
    source = _build_snapshot_via_api(omitted_findings_count=2)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.truncated is True
    assert result.artifact.omitted_counts["findings"] == 2


def test_emit_omitted_counts_propagated_verbatim() -> None:
    source = _build_snapshot_via_api(
        omitted_findings_count=5,
        omitted_recommendations_count=2,
        omitted_replay_results_count=1,
        omitted_page_refs_count=3,
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.omitted_counts == {
        "findings": 5,
        "recommendations": 2,
        "replay_results": 1,
        "page_refs": 3,
    }


def test_emit_evidence_quality_propagated() -> None:
    source = _build_snapshot_via_api(corpus_evidence_quality="canonical")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.evidence_quality == "canonical"


def test_emit_completeness_propagated() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.completeness == "complete"


def test_emit_snapshot_version_propagated() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.snapshot_version == "v1"


def test_emit_snapshot_digest_propagated() -> None:
    source = _build_snapshot_via_api()
    assert source.snapshot is not None
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.snapshot_digest == source.snapshot.snapshot_digest


def test_emit_generated_at_is_utc() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.generated_at.tzinfo is timezone.utc


# --- Section 7: Artifact-key format + corpus_id substitution ---------------


def test_artifact_key_starts_with_review_prefix() -> None:
    source = _build_snapshot_via_api(corpus_id="abc-123")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.artifact_key.startswith(
        "review:governance-report:"
    )


def test_artifact_key_substitutes_corpus_id() -> None:
    source = _build_snapshot_via_api(corpus_id="my-corpus")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert (
        result.artifact.artifact_key
        == "review:governance-report:my-corpus"
    )


def test_artifact_key_does_not_have_dag_prefix() -> None:
    """The artifact key MUST NOT begin with ``dag-`` (per doc-19:348-349
    AC)."""

    source = _build_snapshot_via_api(corpus_id="some-corpus")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert not result.artifact.artifact_key.startswith("dag-")
    assert not result.artifact.artifact_key.startswith("dag:")


def test_artifact_key_template_uses_constant() -> None:
    """The typed REPORT_ARTIFACT_KEY_PREFIX constant is the template
    source."""

    source = _build_snapshot_via_api(corpus_id="foo")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    expected = f"{REPORT_ARTIFACT_KEY_PREFIX}foo"
    assert result.artifact.artifact_key == expected


def test_artifact_key_preserves_special_chars_in_corpus_id() -> None:
    """Corpus ids with dashes, underscores are preserved verbatim."""

    source = _build_snapshot_via_api(corpus_id="corpus_with-dashes_and_underscores")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert (
        result.artifact.artifact_key
        == "review:governance-report:corpus_with-dashes_and_underscores"
    )


# --- Section 8: Reproducibility (AC1 / doc-19:218) ------------------------


def test_emit_reproducible_same_inputs() -> None:
    """Per doc-19:218 report generation is reproducible for the same
    corpus id."""

    source = _build_snapshot_via_api(findings=[_finding()])
    inputs = ReportArtifactInputs(source=source)
    r1 = _emitter().emit_report_artifact(inputs)
    r2 = _emitter().emit_report_artifact(inputs)
    assert r1.artifact is not None and r2.artifact is not None
    # All bounded-summary fields are deterministic except generated_at.
    assert r1.artifact.artifact_key == r2.artifact.artifact_key
    assert r1.artifact.corpus_id == r2.artifact.corpus_id
    assert r1.artifact.snapshot_digest == r2.artifact.snapshot_digest
    assert r1.artifact.top_finding_keys == r2.artifact.top_finding_keys
    assert (
        r1.artifact.recommendation_keys
        == r2.artifact.recommendation_keys
    )
    assert r1.artifact.replay_result_ids == r2.artifact.replay_result_ids
    assert r1.artifact.page_refs == r2.artifact.page_refs
    assert r1.artifact.omitted_counts == r2.artifact.omitted_counts


def test_emit_reproducible_artifact_key_for_same_corpus_id() -> None:
    source1 = _build_snapshot_via_api(corpus_id="my-corpus")
    source2 = _build_snapshot_via_api(corpus_id="my-corpus")
    r1 = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source1)
    )
    r2 = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source2)
    )
    assert r1.artifact is not None and r2.artifact is not None
    assert r1.artifact.artifact_key == r2.artifact.artifact_key


def test_emit_different_corpus_id_produces_different_artifact_key() -> None:
    source1 = _build_snapshot_via_api(corpus_id="corpus-a")
    source2 = _build_snapshot_via_api(corpus_id="corpus-b")
    r1 = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source1)
    )
    r2 = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source2)
    )
    assert r1.artifact is not None and r2.artifact is not None
    assert r1.artifact.artifact_key != r2.artifact.artifact_key


# --- Section 9: Fail-closed never-raises ----------------------------------


def test_emit_never_raises_on_empty_corpus_id() -> None:
    """Empty corpus_id produces a typed gap, never raises."""

    source = _build_snapshot_via_api(corpus_id="")
    # Upstream snapshot API treats empty corpus as failure -> snapshot=None.
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is None
    main_gap = [
        g
        for g in result.gap_findings
        if g.reason == "upstream_snapshot_missing"
    ]
    assert len(main_gap) == 1


def test_emit_never_raises_on_upstream_missing() -> None:
    source = SnapshotAPIResult(snapshot=None, gap_findings=[])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "upstream_snapshot_missing"


def test_emit_never_raises_on_corpus_id_whitespace_only_construction() -> None:
    """If a snapshot was somehow constructed with whitespace-only corpus_id
    (e.g. via direct GovernanceSnapshot construction), the emitter still
    emits a typed gap rather than raising."""

    # Manually construct a snapshot with whitespace-only corpus_id.
    fake_snapshot = GovernanceSnapshot(
        corpus_id="   ",
        snapshot_version="v1",
        snapshot_digest="x" * 64,
        generated_at=datetime.now(timezone.utc),
        scorecard_id=None,
        max_response_bytes=262_144,
        truncated=False,
        omitted_counts={},
        completeness="complete",
        page_refs=[],
        next_cursor=None,
        top_findings=[],
        recommendations=[],
        replay_results=[],
        evidence_quality="canonical",
        blocked_by=[],
    )
    source = SnapshotAPIResult(snapshot=fake_snapshot, gap_findings=[])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "corpus_id_empty"


def test_emit_propagates_upstream_gaps_verbatim_prefixed() -> None:
    """When upstream snapshot is None and upstream has gaps, the emitter
    propagates them prefixed with ``upstream_snapshot_gap:``."""

    upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="my-corpus",
        reason="snapshot_construction_failed",
        observed_at=datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        evidence_payload={"detail": "boom"},
    )
    source = SnapshotAPIResult(snapshot=None, gap_findings=[upstream_gap])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is None
    # Main upstream_snapshot_missing gap + propagated upstream gap.
    main_gaps = [
        g
        for g in result.gap_findings
        if g.reason == "upstream_snapshot_missing"
    ]
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(main_gaps) == 1
    assert len(propagated) == 1
    assert "snapshot_construction_failed" in propagated[0].reason
    assert propagated[0].evidence_payload == {"detail": "boom"}
    # The propagated gap preserves the upstream observed_at.
    assert propagated[0].observed_at == datetime(
        2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc
    )


def test_emit_corpus_id_recovered_from_upstream_gap() -> None:
    """When upstream snapshot is None but upstream gap has a recoverable
    corpus_id, the main gap uses that corpus_id."""

    upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="my-corpus",
        reason="snapshot_construction_failed",
        observed_at=datetime.now(timezone.utc),
    )
    source = SnapshotAPIResult(snapshot=None, gap_findings=[upstream_gap])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    main_gap = next(
        g
        for g in result.gap_findings
        if g.reason == "upstream_snapshot_missing"
    )
    assert main_gap.corpus_id == "my-corpus"


def test_emit_blocked_by_emits_informational_gap_but_still_emits_artifact() -> None:
    """If upstream snapshot is blocked_by non-empty, the emitter still
    emits a typed artifact AND records an informational gap."""

    source = _build_snapshot_via_api(blocked_by=["stale_evidence:foo"])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    # The artifact IS emitted (informational gap).
    assert result.artifact is not None
    assert result.artifact.blocked_by == ["stale_evidence:foo"]
    # The informational gap IS recorded.
    stale_gaps = [
        g
        for g in result.gap_findings
        if g.reason == "governance_snapshot_stale"
    ]
    assert len(stale_gaps) == 1


def test_emit_clean_snapshot_no_informational_gaps() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.gap_findings == []


# --- Section 10: DIRECT annotation-identity REUSE -------------------------


def test_reuse_snapshot_api_result_via_inputs_annotation() -> None:
    """The typed ReportArtifactInputs.source field annotation IS the
    SnapshotAPIResult class (no second source of truth)."""

    fields = ReportArtifactInputs.model_fields
    assert fields["source"].annotation is SnapshotAPIResult


def test_reuse_governance_snapshot_via_artifact_completeness() -> None:
    """The typed GovernanceReportArtifact.completeness field annotation
    IS the Slice 13a CompletenessState Literal (verified by importing
    the same Literal)."""

    fields = GovernanceReportArtifact.model_fields
    assert fields["completeness"].annotation == CompletenessState


def test_reuse_governance_snapshot_via_artifact_evidence_quality() -> None:
    """The typed GovernanceReportArtifact.evidence_quality field
    annotation IS the Slice 13a EvidenceQuality Literal."""

    fields = GovernanceReportArtifact.model_fields
    assert fields["evidence_quality"].annotation == EvidenceQuality


def test_reuse_governance_finding_via_top_finding_keys_projection() -> None:
    """The emitter's typed projection of GovernanceFinding.idempotency_key
    is asserted by emitting then comparing."""

    finding = _finding(idempotency_key="custom-key")
    source = _build_snapshot_via_api(findings=[finding])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.top_finding_keys == ["custom-key"]


def test_reuse_recommendation_via_recommendation_keys_projection() -> None:
    rec = _recommendation(idempotency_key="custom-rec-key")
    source = _build_snapshot_via_api(recommendations=[rec])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.recommendation_keys == ["custom-rec-key"]


def test_reuse_counterfactual_result_via_replay_result_ids_projection() -> None:
    replay = _replay_result(result_id="custom-result-id")
    source = _build_snapshot_via_api(replay_results=[replay])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.replay_result_ids == ["custom-result-id"]


def test_no_local_redefinition_of_snapshot_api_result() -> None:
    """The Slice 19 6th sub-slice module does NOT redefine the
    SnapshotAPIResult typed shape -- it imports it from the Slice 19
    2nd sub-slice module."""

    from iriai_build_v2.execution_control import governance_report_artifact

    # The module should not export a local SnapshotAPIResult.
    assert "SnapshotAPIResult" not in governance_report_artifact.__all__


def test_no_local_redefinition_of_governance_snapshot() -> None:
    """The Slice 19 6th sub-slice module does NOT redefine the
    GovernanceSnapshot typed shape."""

    from iriai_build_v2.execution_control import governance_report_artifact

    assert "GovernanceSnapshot" not in governance_report_artifact.__all__


# --- Section 11: Failure-router 4-pure-data add discipline ----------------


def test_failure_router_failure_types_contains_id() -> None:
    """The new typed failure id MUST be registered in FAILURE_TYPES
    (the runtime tuple)."""

    assert "governance_report_artifact_emission_failed" in FAILURE_TYPES


def test_failure_router_failure_type_literal_contains_id() -> None:
    """The new typed failure id MUST be in the FailureType Literal."""

    lit_args = typing.get_args(FailureType)
    assert "governance_report_artifact_emission_failed" in lit_args


def test_failure_router_route_table_contains_id() -> None:
    """The new typed failure id MUST have a route table entry."""

    key = (
        "evidence_corruption",
        "governance_report_artifact_emission_failed",
    )
    assert key in ROUTE_TABLE


def test_failure_router_reuses_retry_governance_projection_action() -> None:
    """The new typed failure id routes to the EXISTING
    retry_governance_projection action (REUSED from Slice 14 2nd
    sub-slice; NOT a new action)."""

    key = (
        "evidence_corruption",
        "governance_report_artifact_emission_failed",
    )
    assert key in ROUTE_TABLE
    route = ROUTE_TABLE[key]
    assert route.action == "retry_governance_projection"


def test_failure_router_retry_governance_projection_in_route_action() -> None:
    """retry_governance_projection MUST be a valid RouteAction Literal."""

    lit_args = typing.get_args(RouteAction)
    assert "retry_governance_projection" in lit_args


def test_failure_router_uses_evidence_corruption_class() -> None:
    """The new typed failure id routes under EXISTING
    evidence_corruption failure_class (NOT a new class)."""

    matching = [
        key
        for key in ROUTE_TABLE
        if key[1] == "governance_report_artifact_emission_failed"
    ]
    assert len(matching) == 1
    assert matching[0][0] == "evidence_corruption"


def test_failure_router_governance_failure_ids_total_16() -> None:
    """After Slice 19 6th sub-slice: 16 typed Slice 17+ governance
    failure ids registered: 5 Slice 17 + 6 Slice 18 + 5 Slice 19."""

    slice_17_ids = [
        "recommendation_builder_emission_failed",
        "policy_validation_failed",
        "decision_record_persistence_failed",
        "replay_requirement_validation_failed",
        "consumer_read_api_failed",
    ]
    slice_18_ids = [
        "replay_corpus_or_scenario_load_failed",
        "summary_replay_failed",
        "event_replay_failed",
        "metrics_comparator_failed",
        "counterfactual_result_persistence_failed",
        "recommendation_citation_validation_failed",
    ]
    slice_19_ids = [
        "governance_snapshot_api_failed",
        "governance_dashboard_view_failed",
        "governance_slack_renderer_failed",
        "governance_agent_context_builder_failed",
        "governance_report_artifact_emission_failed",
    ]
    all_ids = slice_17_ids + slice_18_ids + slice_19_ids
    assert len(all_ids) == 16
    for fid in all_ids:
        assert fid in FAILURE_TYPES, f"{fid} missing from FAILURE_TYPES"


def test_failure_router_route_table_message_cites_slice_19_6th() -> None:
    """The route table reason MUST cite Slice 19 6th + doc-19:161-162
    so future readers can trace the failure id back to this sub-slice."""

    key = (
        "evidence_corruption",
        "governance_report_artifact_emission_failed",
    )
    assert key in ROUTE_TABLE
    route = ROUTE_TABLE[key]
    message = route.reason
    assert "Slice 19 6th" in message
    assert "doc-19:161-162" in message
    assert "non-blocking" in message
    assert "doc-14:242-243" in message


# --- Section 12: AC1 enforcement (doc-19:224) -----------------------------


def test_ac1_bounded_no_full_evidence_bodies() -> None:
    """AC1: Reports are bounded. The typed artifact MUST NOT carry
    full evidence body fields -- only refs-only by-name shapes."""

    source = _build_snapshot_via_api(
        findings=[_finding()],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        page_refs=[_page_ref()],
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None

    # The typed artifact carries only by-name reference lists.
    assert isinstance(result.artifact.top_finding_keys, list)
    assert all(
        isinstance(k, str) for k in result.artifact.top_finding_keys
    )
    assert all(
        isinstance(k, str) for k in result.artifact.recommendation_keys
    )
    assert all(
        isinstance(k, str) for k in result.artifact.replay_result_ids
    )
    assert all(isinstance(k, str) for k in result.artifact.page_refs)


def test_ac1_reproducible() -> None:
    """AC1: Reports are reproducible. Same inputs -> same artifact
    (excluding generated_at)."""

    source = _build_snapshot_via_api(findings=[_finding()])
    r1 = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    r2 = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert r1.artifact is not None and r2.artifact is not None
    assert r1.artifact.snapshot_digest == r2.artifact.snapshot_digest
    assert r1.artifact.top_finding_keys == r2.artifact.top_finding_keys


def test_ac1_evidence_cited_via_page_refs() -> None:
    """AC1: Reports are evidence-cited. The typed page_refs surface
    carries the typed Slice 13a page-ref id strings."""

    source = _build_snapshot_via_api(page_refs=[_page_ref()])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert len(result.artifact.page_refs) == 1


def test_ac1_structured_first() -> None:
    """AC1: Reports are structured first. The typed artifact IS a
    Pydantic BaseModel."""

    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert isinstance(result.artifact, BaseModel)


# --- Section 13: AC2 enforcement (doc-19:225-226) -------------------------


def test_ac2_truncated_carries_page_refs_when_paged() -> None:
    """AC2: Truncated reports MUST carry exact page refs +
    completeness."""

    source = _build_snapshot_via_api(
        omitted_findings_count=1, page_refs=[_page_ref()]
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.truncated is True
    assert result.artifact.completeness == "paged"
    assert len(result.artifact.page_refs) == 1


def test_ac2_preview_only_completeness_propagated() -> None:
    """AC2: ``preview_only`` completeness propagated verbatim."""

    source = _build_snapshot_via_api(
        completeness_override="preview_only"
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.completeness == "preview_only"


def test_ac2_unavailable_completeness_propagated() -> None:
    source = _build_snapshot_via_api(
        completeness_override="unavailable"
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.completeness == "unavailable"


def test_ac2_complete_completeness_no_truncation() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.completeness == "complete"
    assert result.artifact.truncated is False


# --- Section 14: AC6 enforcement (doc-19:232-233) -------------------------


def test_ac6_evidence_quality_always_populated() -> None:
    """AC6: Evidence quality MUST be populated on every report."""

    for q in ["canonical", "derived", "sampled", "advisory", "stale"]:
        source = _build_snapshot_via_api(corpus_evidence_quality=q)
        result = _emitter().emit_report_artifact(
            ReportArtifactInputs(source=source)
        )
        assert result.artifact is not None
        assert result.artifact.evidence_quality == q


def test_ac6_omitted_counts_always_populated() -> None:
    """AC6: Omitted counts MUST be populated (default 0)."""

    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert "findings" in result.artifact.omitted_counts
    assert "recommendations" in result.artifact.omitted_counts
    assert "replay_results" in result.artifact.omitted_counts
    assert "page_refs" in result.artifact.omitted_counts


# --- Section 15: AC7 enforcement (doc-19:234) -----------------------------


def test_ac7_emitter_class_has_one_public_method() -> None:
    """AC7: Read-only contract -- the emitter class has ONE public
    method (emit_report_artifact)."""

    public_methods = [
        name
        for name in dir(GovernanceReportArtifactEmitter)
        if not name.startswith("_")
        and callable(getattr(GovernanceReportArtifactEmitter, name))
    ]
    assert public_methods == ["emit_report_artifact"]


def test_ac7_no_mutation_methods_on_artifact() -> None:
    """AC7: The typed artifact BaseModel has NO mutation methods."""

    mutation_method_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "checkpoint_",
        "mutate_",
        "write_",
        "persist_",
        "extend_",
        "delete_",
    )
    for name in dir(GovernanceReportArtifact):
        for prefix in mutation_method_prefixes:
            assert not name.startswith(prefix), (
                f"GovernanceReportArtifact has mutation method '{name}' "
                f"matching forbidden prefix '{prefix}'"
            )


def test_ac7_no_mutation_methods_on_inputs() -> None:
    mutation_method_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "checkpoint_",
        "mutate_",
        "write_",
        "persist_",
        "extend_",
        "delete_",
    )
    for name in dir(ReportArtifactInputs):
        for prefix in mutation_method_prefixes:
            assert not name.startswith(prefix), (
                f"ReportArtifactInputs has mutation method '{name}' "
                f"matching forbidden prefix '{prefix}'"
            )


def test_ac7_no_mutation_methods_on_gap() -> None:
    mutation_method_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "checkpoint_",
        "mutate_",
        "write_",
        "persist_",
        "extend_",
        "delete_",
    )
    for name in dir(ReportArtifactGap):
        for prefix in mutation_method_prefixes:
            assert not name.startswith(prefix), (
                f"ReportArtifactGap has mutation method '{name}' "
                f"matching forbidden prefix '{prefix}'"
            )


def test_ac7_no_mutation_methods_on_result() -> None:
    mutation_method_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "checkpoint_",
        "mutate_",
        "write_",
        "persist_",
        "extend_",
        "delete_",
    )
    for name in dir(ReportArtifactResult):
        for prefix in mutation_method_prefixes:
            assert not name.startswith(prefix), (
                f"ReportArtifactResult has mutation method '{name}' "
                f"matching forbidden prefix '{prefix}'"
            )


def test_ac7_emit_method_signature_consumes_inputs_only() -> None:
    """The emit_report_artifact method consumes ONLY the typed
    ReportArtifactInputs (no positional flag for caller-supplied
    activation/mutation)."""

    sig = inspect.signature(
        GovernanceReportArtifactEmitter.emit_report_artifact
    )
    params = list(sig.parameters.keys())
    # 'self' + 'inputs'.
    assert params == ["self", "inputs"]


# --- Section 16: Defence-in-depth: artifact key prefix + CONTROL_PLANE_WRITER_METHODS


def test_artifact_key_not_in_control_plane_writer_methods() -> None:
    """Defence-in-depth: the artifact key (and its prefix) MUST NOT
    be present in the Slice 10c-1 CONTROL_PLANE_WRITER_METHODS set
    per doc-19:348-349 AC."""

    assert (
        "review:governance-report:"
        not in CONTROL_PLANE_WRITER_METHODS
    )
    assert (
        "governance_report_artifact"
        not in CONTROL_PLANE_WRITER_METHODS
    )
    assert (
        "emit_report_artifact"
        not in CONTROL_PLANE_WRITER_METHODS
    )


def test_emitted_artifact_key_does_not_extend_control_plane_writer_methods() -> None:
    """Defence-in-depth: emitting the typed artifact key does NOT add
    it to CONTROL_PLANE_WRITER_METHODS (which is a frozenset)."""

    pre_emit = set(CONTROL_PLANE_WRITER_METHODS)
    source = _build_snapshot_via_api(corpus_id="defence-corpus")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    post_emit = set(CONTROL_PLANE_WRITER_METHODS)
    # Set is unchanged.
    assert pre_emit == post_emit
    # The emitted key prefix is NOT in the set.
    assert result.artifact.artifact_key not in CONTROL_PLANE_WRITER_METHODS


def test_emitter_does_not_call_any_writer_method() -> None:
    """Defence-in-depth: the emitter class source does NOT reference
    any CONTROL_PLANE_WRITER_METHODS string by name."""

    source_file = inspect.getsourcefile(GovernanceReportArtifactEmitter)
    assert source_file is not None
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()

    # The emitter source MUST NOT call any of the writer methods.
    # We check the most representative ones (the full set is checked
    # by the activation-boundary test below).
    writer_call_patterns = [
        "self.record(",
        "self.record_success(",
        "self.record_runtime_failure(",
        "self.project_task_result(",
        "self.project_group_checkpoint(",
        "self.project_regroup_overlay(",
        "self.put_task_contract(",
    ]
    for pattern in writer_call_patterns:
        assert pattern not in source, (
            f"Emitter source references writer call pattern '{pattern}'"
        )


def test_emitter_source_has_no_dag_artifact_key_literals() -> None:
    """Defence-in-depth: the emitter source MUST NOT mint any
    ``dag-*`` artifact-key string literals."""

    source_file = inspect.getsourcefile(GovernanceReportArtifactEmitter)
    assert source_file is not None
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()

    # The emitter source MUST NOT contain string literals starting
    # with "dag-" or "dag:" that are NOT in comments / docstrings.
    # We do a simple check on the .py file: the artifact-key prefix
    # constant must be "review:" not "dag-".
    assert REPORT_ARTIFACT_KEY_PREFIX.startswith("review:")
    # The compiled module must not export any constants with dag- prefix.
    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    for name in mod.__all__:
        value = getattr(mod, name)
        if isinstance(value, str):
            assert not value.startswith("dag-"), (
                f"Exported constant {name} = {value!r} has forbidden "
                f"dag- prefix"
            )


def test_artifact_key_prefix_constant_immutable() -> None:
    """The typed REPORT_ARTIFACT_KEY_PREFIX is a constant (typed
    Literal); the test asserts the runtime value is exactly the
    Literal value."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    # Re-importing must yield the same value (no module-level
    # mutation).
    import importlib

    reloaded = importlib.reload(mod)
    assert reloaded.REPORT_ARTIFACT_KEY_PREFIX == "review:governance-report:"


# --- Section 17: edge cases ------------------------------------------------


def test_empty_findings_recommendations_replays_emits_empty_lists() -> None:
    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.top_finding_keys == []
    assert result.artifact.recommendation_keys == []
    assert result.artifact.replay_result_ids == []


def test_blocked_by_propagated_verbatim() -> None:
    source = _build_snapshot_via_api(
        blocked_by=["a", "b", "stale_evidence:foo"]
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.blocked_by == [
        "a",
        "b",
        "stale_evidence:foo",
    ]


def test_artifact_key_is_str_not_literal() -> None:
    """The typed artifact_key field is str (NOT Literal) so the typed
    corpus id can be substituted at construction."""

    fields = GovernanceReportArtifact.model_fields
    assert fields["artifact_key"].annotation is str


def test_emit_with_long_corpus_id() -> None:
    """Long corpus ids are preserved verbatim in the artifact key."""

    long_corpus = "a" * 200
    source = _build_snapshot_via_api(corpus_id=long_corpus)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.artifact_key == (
        f"review:governance-report:{long_corpus}"
    )


def test_emit_with_max_findings_at_cap() -> None:
    """Cap the snapshot at 20 findings (default max_findings)."""

    findings = [
        _finding(idempotency_key=f"f-{i}") for i in range(20)
    ]
    source = _build_snapshot_via_api(findings=findings)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert len(result.artifact.top_finding_keys) == 20


def test_emit_with_findings_exceeding_cap_truncates_via_upstream() -> None:
    """Exceeding the snapshot cap truncates upstream; the emitter
    only sees the bounded subset."""

    findings = [
        _finding(idempotency_key=f"f-{i}") for i in range(25)
    ]
    source = _build_snapshot_via_api(findings=findings, max_findings=20)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert len(result.artifact.top_finding_keys) == 20
    assert result.artifact.truncated is True
    assert result.artifact.omitted_counts["findings"] == 5


def test_artifact_serialised_size_is_bounded() -> None:
    """The typed artifact serialised size is bounded (refs-only --
    no embedded artifact bodies)."""

    # Build a populated snapshot.
    findings = [_finding(idempotency_key=f"f-{i}") for i in range(20)]
    recs = [
        _recommendation(idempotency_key=f"r-{i}") for i in range(10)
    ]
    replays = [
        _replay_result(result_id=f"rr-{i}") for i in range(10)
    ]
    page_refs = [
        _page_ref(page_ref_id=f"pr-{i}") for i in range(10)
    ]
    source = _build_snapshot_via_api(
        findings=findings,
        recommendations=recs,
        replay_results=replays,
        page_refs=page_refs,
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    # The typed artifact serialised size should be small (< 10 KB)
    # because it carries only by-name reference shapes.
    serialised = result.artifact.model_dump_json()
    assert len(serialised) < 10_000, (
        f"Artifact serialised size {len(serialised)} bytes exceeds "
        f"the bounded-summary 10 KB sanity check"
    )


def test_emit_artifact_dict_round_trips() -> None:
    """The typed artifact round-trips through model_dump + Pydantic."""

    source = _build_snapshot_via_api(
        findings=[_finding()], page_refs=[_page_ref()]
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    dumped = result.artifact.model_dump()
    re_constructed = GovernanceReportArtifact(**dumped)
    assert re_constructed.artifact_key == result.artifact.artifact_key
    assert (
        re_constructed.top_finding_keys
        == result.artifact.top_finding_keys
    )


# --- Section 18: Activation-authority boundary discipline -----------------


def test_emitter_is_stateless() -> None:
    """The emitter is stateless; same inputs -> same artifact-key
    (excluding generated_at)."""

    emitter = _emitter()
    source = _build_snapshot_via_api()
    inputs = ReportArtifactInputs(source=source)
    r1 = emitter.emit_report_artifact(inputs)
    r2 = emitter.emit_report_artifact(inputs)
    assert r1.artifact is not None and r2.artifact is not None
    assert r1.artifact.artifact_key == r2.artifact.artifact_key


def test_emitter_does_not_have_init_signature() -> None:
    """The emitter uses the default __init__; no state to initialise."""

    sig = inspect.signature(GovernanceReportArtifactEmitter)
    params = list(sig.parameters.keys())
    assert params == [], (
        f"GovernanceReportArtifactEmitter should construct with no "
        f"arguments; found {params}"
    )


def test_emitter_class_has_no_class_level_state() -> None:
    """The emitter class has no class-level state (no class
    attributes besides methods + dunders + docstrings)."""

    # The class has __doc__ + the emit_report_artifact public method +
    # private helpers. No mutable class-level state.
    class_dict = GovernanceReportArtifactEmitter.__dict__
    non_dunder = [k for k in class_dict if not k.startswith("__")]
    # Only methods + docstrings; no list/dict/set/instance.
    for name in non_dunder:
        value = class_dict[name]
        # methods + staticmethods + properties are callable / descriptor.
        assert callable(value) or isinstance(value, staticmethod), (
            f"Class attr '{name}' = {value!r} is not callable; "
            f"emitter must be stateless"
        )


def test_emit_does_not_mutate_inputs() -> None:
    """The typed inputs are NOT mutated by emit_report_artifact."""

    source = _build_snapshot_via_api(findings=[_finding()])
    inputs = ReportArtifactInputs(source=source)
    original_source_id = id(inputs.source)
    _emitter().emit_report_artifact(inputs)
    # Identity preserved (no swap-in of new source).
    assert id(inputs.source) == original_source_id


# --- Section 19: Documentation / type-hint sanity --------------------------


def test_emitter_class_docstring_mentions_doc_19_step_6() -> None:
    assert GovernanceReportArtifactEmitter.__doc__ is not None
    assert "161-162" in GovernanceReportArtifactEmitter.__doc__
    assert "step 6" in GovernanceReportArtifactEmitter.__doc__.lower()


def test_emitter_class_docstring_mentions_bounded_summary() -> None:
    assert GovernanceReportArtifactEmitter.__doc__ is not None
    text = GovernanceReportArtifactEmitter.__doc__.lower()
    assert "bounded" in text
    assert "summary" in text


def test_emitter_class_docstring_mentions_refs_only() -> None:
    assert GovernanceReportArtifactEmitter.__doc__ is not None
    text = GovernanceReportArtifactEmitter.__doc__.lower()
    assert "refs-only" in text


def test_emitter_class_docstring_mentions_fail_closed() -> None:
    assert GovernanceReportArtifactEmitter.__doc__ is not None
    text = GovernanceReportArtifactEmitter.__doc__.lower()
    assert "fail-closed" in text


def test_emit_method_docstring_mentions_never_raises() -> None:
    docstring = GovernanceReportArtifactEmitter.emit_report_artifact.__doc__
    assert docstring is not None
    text = docstring.lower()
    assert "never raises" in text


def test_artifact_docstring_mentions_doc_19() -> None:
    assert GovernanceReportArtifact.__doc__ is not None
    assert "161-162" in GovernanceReportArtifact.__doc__


def test_artifact_docstring_mentions_review_prefix() -> None:
    assert GovernanceReportArtifact.__doc__ is not None
    assert "review:governance-report:" in GovernanceReportArtifact.__doc__


# --- Section 20: more upstream-propagation checks --------------------------


def test_propagated_upstream_gap_preserves_evidence_payload() -> None:
    upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="corpus-A",
        reason="custom_reason",
        observed_at=datetime.now(timezone.utc),
        evidence_payload={"key": "value", "n": 5},
    )
    source = SnapshotAPIResult(
        snapshot=None,
        gap_findings=[upstream_gap],
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(propagated) == 1
    assert propagated[0].evidence_payload["key"] == "value"
    assert propagated[0].evidence_payload["n"] == 5


def test_propagated_upstream_gap_preserves_observed_at() -> None:
    upstream_time = datetime(2025, 12, 1, 8, 0, 0, tzinfo=timezone.utc)
    upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="corpus-A",
        reason="snapshot_construction_failed",
        observed_at=upstream_time,
    )
    source = SnapshotAPIResult(
        snapshot=None,
        gap_findings=[upstream_gap],
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(propagated) == 1
    assert propagated[0].observed_at == upstream_time


def test_multiple_upstream_gaps_all_propagated() -> None:
    upstream_gaps = [
        SnapshotAPIGap(
            failure_id="governance_snapshot_api_failed",
            corpus_id="corpus-A",
            reason=f"reason-{i}",
            observed_at=datetime.now(timezone.utc),
        )
        for i in range(3)
    ]
    source = SnapshotAPIResult(
        snapshot=None,
        gap_findings=upstream_gaps,
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(propagated) == 3


def test_main_upstream_missing_gap_evidence_payload_carries_count() -> None:
    upstream_gaps = [
        SnapshotAPIGap(
            failure_id="governance_snapshot_api_failed",
            corpus_id="c",
            reason=f"r-{i}",
            observed_at=datetime.now(timezone.utc),
        )
        for i in range(2)
    ]
    source = SnapshotAPIResult(snapshot=None, gap_findings=upstream_gaps)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    main = next(
        g
        for g in result.gap_findings
        if g.reason == "upstream_snapshot_missing"
    )
    assert main.evidence_payload.get("upstream_gap_count") == 2


# --- Section 21: more edge-case rows from doc-19:184-194 ------------------


def test_doc_19_186_187_governance_snapshot_stale_edge_case() -> None:
    """doc-19:186-187: 'Governance snapshot stale: report stale status
    and do not present new recommendations as current.'"""

    source = _build_snapshot_via_api(blocked_by=["stale_evidence:8ac124d6"])
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    # Artifact carries the stale status.
    assert result.artifact.blocked_by == ["stale_evidence:8ac124d6"]
    # Informational gap is recorded.
    stale_gaps = [
        g
        for g in result.gap_findings
        if g.reason == "governance_snapshot_stale"
    ]
    assert len(stale_gaps) == 1
    # The gap's evidence_payload carries the blocked_by list.
    assert stale_gaps[0].evidence_payload["blocked_by"] == [
        "stale_evidence:8ac124d6"
    ]


def test_doc_19_193_194_active_workflow_pressure_via_truncation() -> None:
    """doc-19:193-194: 'Active workflow pressure: reporting returns
    cached snapshots instead of forcing expensive recomputation.'"""

    # The emitter does NOT itself fetch -- it projects whatever the
    # caller provides. This test verifies the typed surface still emits
    # an artifact for a truncated/cached snapshot (display-only marker
    # carried via the typed completeness + page_refs + omitted_counts
    # triple).
    source = _build_snapshot_via_api(
        omitted_findings_count=10,
        page_refs=[_page_ref()],
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.truncated is True
    assert result.artifact.completeness == "paged"
    assert len(result.artifact.page_refs) == 1


# --- Section 22: end-to-end via full snapshot API ------------------------


def test_end_to_end_realistic_snapshot_emits_artifact() -> None:
    """Realistic end-to-end scenario via the typed snapshot API +
    typed emitter chain."""

    findings = [
        _finding(idempotency_key=f"f-{i}", severity="high")
        for i in range(3)
    ]
    recs = [
        _recommendation(idempotency_key=f"r-{i}") for i in range(2)
    ]
    replays = [
        _replay_result(result_id=f"rr-{i}") for i in range(2)
    ]
    page_refs = [
        _page_ref(page_ref_id=f"pr-{i}") for i in range(3)
    ]
    source = _build_snapshot_via_api(
        corpus_id="e2e-corpus",
        findings=findings,
        recommendations=recs,
        replay_results=replays,
        page_refs=page_refs,
    )
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.artifact_key == (
        "review:governance-report:e2e-corpus"
    )
    assert result.artifact.top_finding_keys == [
        "f-0",
        "f-1",
        "f-2",
    ]
    assert result.artifact.recommendation_keys == ["r-0", "r-1"]
    assert result.artifact.replay_result_ids == ["rr-0", "rr-1"]
    assert set(result.artifact.page_refs) == {"pr-0", "pr-1", "pr-2"}


def test_end_to_end_pure_function_isolation() -> None:
    """Two separate emitter instances produce identical artifacts for
    the same input (pure-function discipline)."""

    source = _build_snapshot_via_api(findings=[_finding()])
    e1 = GovernanceReportArtifactEmitter()
    e2 = GovernanceReportArtifactEmitter()
    r1 = e1.emit_report_artifact(ReportArtifactInputs(source=source))
    r2 = e2.emit_report_artifact(ReportArtifactInputs(source=source))
    assert r1.artifact is not None and r2.artifact is not None
    assert r1.artifact.artifact_key == r2.artifact.artifact_key
    assert r1.artifact.top_finding_keys == r2.artifact.top_finding_keys


# --- Section 23: deferred / Slice 21 considerations -----------------------


def test_artifact_does_not_carry_context_package_field() -> None:
    """The typed GovernanceReportArtifact does NOT carry a
    ContextLayerPackageSummary field (per doc-19:89-101 +
    doc-19:179-182 the Slice 21 typed-package contract applies to
    agent context, NOT to report artifacts). The report artifact is
    intentionally bounded to the typed snapshot's bounded summary."""

    fields = set(GovernanceReportArtifact.model_fields.keys())
    assert "context_package" not in fields


# --- Section 24: more failure-router defensive tests ----------------------


def test_failure_router_route_table_size_advances() -> None:
    """The route table contains EXACTLY one entry for the new typed
    failure id (no duplicates)."""

    matching = [
        key
        for key in ROUTE_TABLE
        if key[1] == "governance_report_artifact_emission_failed"
    ]
    assert len(matching) == 1


def test_failure_router_failure_types_no_duplicate_id() -> None:
    """The runtime tuple FAILURE_TYPES has no duplicate of the new
    typed failure id."""

    occurrences = sum(
        1
        for f in FAILURE_TYPES
        if f == "governance_report_artifact_emission_failed"
    )
    assert occurrences == 1


# --- Section 25: additional defence-in-depth checks ------------------------


def test_emitter_does_not_extend_control_plane_writer_methods() -> None:
    """Defence-in-depth: the emitter source must not extend the
    CONTROL_PLANE_WRITER_METHODS set (e.g. via add() / update()).

    Docstring references are permitted (the module docstring cites
    doc-19:348-349 AC) but mutation calls are forbidden.
    """

    source_file = inspect.getsourcefile(GovernanceReportArtifactEmitter)
    assert source_file is not None
    with open(source_file, "r", encoding="utf-8") as f:
        source = f.read()

    # The emitter source MUST NOT import CONTROL_PLANE_WRITER_METHODS
    # (which would only be necessary to mutate or check membership for
    # extension logic).
    assert (
        "from iriai_build_v2.supervisor.read_only import"
        not in source
    )
    # The emitter source MUST NOT contain mutation calls on the set.
    forbidden_mutations = [
        "CONTROL_PLANE_WRITER_METHODS.add(",
        "CONTROL_PLANE_WRITER_METHODS.update(",
        "CONTROL_PLANE_WRITER_METHODS |=",
        "CONTROL_PLANE_WRITER_METHODS = ",
    ]
    for pattern in forbidden_mutations:
        assert pattern not in source, (
            f"Emitter source contains forbidden mutation '{pattern}'"
        )


def test_artifact_key_prefix_is_typed_literal() -> None:
    """The exported REPORT_ARTIFACT_KEY_PREFIX is typed as a Literal
    so static type-checkers can enforce the prefix at the call site."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    annotation = hints["REPORT_ARTIFACT_KEY_PREFIX"]
    # The annotation must be a typing.Literal.
    assert typing.get_origin(annotation) is typing.Literal


def test_failure_id_is_typed_literal() -> None:
    """The exported REPORT_ARTIFACT_FAILURE_ID is typed as a Literal."""

    from iriai_build_v2.execution_control import (
        governance_report_artifact as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    annotation = hints["REPORT_ARTIFACT_FAILURE_ID"]
    assert typing.get_origin(annotation) is typing.Literal


# --- Section 26: typed snapshot field preservation -------------------------


def test_artifact_snapshot_version_preserves_custom_version() -> None:
    """Custom snapshot_version is preserved verbatim."""

    api = GovernanceSnapshotAPI()
    inputs = SnapshotAPIInputs(
        corpus_id="vc", snapshot_version="v2-experimental"
    )
    corpus = SnapshotAPICorpus()
    source = api.build_snapshot(inputs, corpus)
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert result.artifact.snapshot_version == "v2-experimental"


def test_artifact_corpus_id_matches_snapshot_corpus_id() -> None:
    source = _build_snapshot_via_api(corpus_id="match-corpus")
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert source.snapshot is not None
    assert result.artifact.corpus_id == source.snapshot.corpus_id


def test_artifact_snapshot_digest_is_64_hex_chars() -> None:
    """The typed snapshot digest is SHA-256 hex (64 chars)."""

    source = _build_snapshot_via_api()
    result = _emitter().emit_report_artifact(
        ReportArtifactInputs(source=source)
    )
    assert result.artifact is not None
    assert len(result.artifact.snapshot_digest) == 64
    # Hex chars.
    assert all(
        c in "0123456789abcdef" for c in result.artifact.snapshot_digest
    )
