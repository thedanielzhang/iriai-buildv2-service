"""Slice 19 5th sub-slice -- unit tests for the typed agent-context
builder at ``execution_control/governance_agent_context_builder.py``.

Covers the doc-19:157-160 step 5 + doc-19:124-127 + doc-19:144-146
agent-context-builder surface:

* :data:`AGENT_CONTEXT_BUILDER_FAILURE_ID` -- the typed failure id
  (``governance_agent_context_builder_failed``) registered under the
  EXISTING ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` action.
* :class:`AgentContextScope` typed BaseModel (extra-forbid; 4 scoping
  axes: task_id / repo_id / path / line_start + line_end).
* :class:`AgentContextBuilderInputs` typed BaseModel (extra-forbid;
  carries Slice 19 2nd SnapshotAPIResult + Slice 14 LineProvenanceResult
  list + parallel paths/ranges + scope + max_prompt_chars).
* :class:`AgentContextBuilderGap` typed BaseModel (extra-forbid;
  mirrors SlackRenderGap / DashboardViewGap / SnapshotAPIGap).
* :class:`AgentContextBuilderResult` typed BaseModel (extra-forbid;
  context | None + gap_findings list).
* :class:`GovernanceAgentContextBuilder.build(...)` -- the projection
  method:
  * Happy-path -> typed GovernanceAgentContext emitted with
    task/repo scope applied + bounded under the effective max
    prompt char cap.
  * Scope-filtering (task_id / repo_id / path / line range).
  * 20 000 char budget enforcement + iterative truncation.
  * Upstream snapshot missing -> context=None + typed gap on
    ``upstream_snapshot_missing`` + propagated upstream gaps.
  * Renderer NEVER raises (typed gap projection on construction
    failure).
  * Slice 21-conditional ContextLayerPackageSummary DEFERRED.
* DIRECT annotation-identity REUSE assertions for Slice 19 1st
  GovernanceAgentContext + Slice 19 2nd SnapshotAPIResult /
  SnapshotAPIGap + Slice 13a CompletenessState + Slice 16
  GovernanceFinding + Slice 17 GovernancePolicyRecommendation +
  Slice 14 LineProvenanceResult.
* Failure-router 4-pure-data add discipline (Slice 14 2nd + Slice 19
  2nd + 3rd + 4th sub-slice precedent verbatim).
* AC1 / AC2 / AC3 / AC5 / AC6 / AC7 enforcement tests per doc-19 §
  Acceptance Criteria.

Per the implementer prompt § "Non-Negotiables" -- fail-closed on
every Pydantic field validator; no executor wiring outside this
slice's own acceptance tests; the Slice 13a + Slice 13A + Slice 14 +
Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 1st + 2nd + 3rd
+ 4th sub-slice modules + tests remain READ-ONLY this sub-slice.
"""

from __future__ import annotations

import typing
from datetime import datetime, timezone

import pytest
from pydantic import BaseModel, ValidationError

from iriai_build_v2.execution_control.commit_provenance import (
    LineProvenanceResult,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.finding_engine import (
    GovernanceFinding,
)
from iriai_build_v2.execution_control.governance_agent import (
    GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP,
    GovernanceAgentContext,
)
from iriai_build_v2.execution_control.governance_agent_context_builder import (
    AGENT_CONTEXT_BUILDER_FAILURE_ID,
    AgentContextBuilderGap,
    AgentContextBuilderInputs,
    AgentContextBuilderResult,
    AgentContextScope,
    GovernanceAgentContextBuilder,
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
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
    FailureType,
    RouteAction,
)
from iriai_build_v2.workflows.develop.governance.models import (
    CompletenessState,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
)


# --- Fixture builders (mirror Slice 19 4th sub-slice fixtures) -------------


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-19-5",
        digest="d" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    base: dict[str, object] = dict(
        page_ref_id="page-ref-19-5-1",
        authority="typed_journal",
        source_ref_id="ref-19-5",
        digest="e" * 64,
        completeness="complete",
        exact=True,
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)


def _finding(**overrides: object) -> GovernanceFinding:
    base: dict[str, object] = dict(
        idempotency_key="finding-key-19-5-a",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={
            "lane": "high_risk",
            "runtime": "claude-sdk",
            "task_id": "task-1",
            "repo_id": "repo-1",
        },
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-25#slice-19-5th"],
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
        idempotency_key="recommendation-key-19-5-a",
        recommendation_id="rec-19-5-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-19-5-a"],
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
        result_id="result-19-5-1",
        result_version="v1",
        scenario_id="scenario-19-5-1",
        corpus_id="corpus-19-5",
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
        supporting_finding_ids=["finding-key-19-5-a"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)


def _line_provenance_result(
    **overrides: object,
) -> LineProvenanceResult:
    base: dict[str, object] = dict(
        commit_hashes=["a" * 40, "b" * 40],
        task_ids=["task-1", "task-2"],
        provenance_payload_refs=[
            "refs/iriai/provenance/abc",
            "refs/iriai/provenance/def",
        ],
        page_refs=[],
        completeness="complete",
        completeness_digest="f" * 64,
        confidence=0.92,
        gaps=[],
    )
    base.update(overrides)
    return LineProvenanceResult(**base)


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


def _builder() -> GovernanceAgentContextBuilder:
    return GovernanceAgentContextBuilder()


# --- Section 1: module surface --------------------------------------------


def test_failure_id_literal_value() -> None:
    assert (
        AGENT_CONTEXT_BUILDER_FAILURE_ID
        == "governance_agent_context_builder_failed"
    )


def test_failure_id_typed_literal_annotation() -> None:
    """The typed Literal is the source of truth -- ensure the runtime
    value matches the literal range."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    lit_args = typing.get_args(hints["AGENT_CONTEXT_BUILDER_FAILURE_ID"])
    assert "governance_agent_context_builder_failed" in lit_args


def test_all_exports_present() -> None:
    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    assert mod.__all__ == [
        "AGENT_CONTEXT_BUILDER_FAILURE_ID",
        "AgentContextScope",
        "AgentContextBuilderInputs",
        "AgentContextBuilderGap",
        "AgentContextBuilderResult",
        "GovernanceAgentContextBuilder",
    ]


def test_no_re_export_from_execution_control_init() -> None:
    """The Slice 19 5th sub-slice surface lives in its own module; the
    execution_control package __init__.py is NOT mutated (preserves
    Slice 00-12 baselines)."""

    from iriai_build_v2 import execution_control as pkg

    init_exports = pkg.__all__ if hasattr(pkg, "__all__") else []
    forbidden = {
        "AGENT_CONTEXT_BUILDER_FAILURE_ID",
        "GovernanceAgentContextBuilder",
        "AgentContextBuilderInputs",
        "AgentContextScope",
    }
    for name in forbidden:
        assert name not in init_exports, (
            f"{name} should NOT be re-exported from execution_control.__init__"
        )


def test_module_carries_doc_19_step_5_citation() -> None:
    """The module docstring must carry the doc-19 step 5 PIN cite per
    the governance prompt § 'cite-everything' rule."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    assert mod.__doc__ is not None
    assert "157-160" in mod.__doc__
    assert "step 5" in mod.__doc__.lower()


def test_module_documents_slice_21_deferral() -> None:
    """The module docstring must document the Slice 21-conditional
    ContextLayerPackageSummary deferral per doc-19:89-101 +
    doc-19:125-127."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    assert mod.__doc__ is not None
    assert "DEFERRED" in mod.__doc__
    assert "ContextLayerPackageSummary" in mod.__doc__
    assert "Slice 21" in mod.__doc__


# --- Section 2: AgentContextScope -----------------------------------------


def test_scope_construction_defaults() -> None:
    scope = AgentContextScope()
    assert scope.task_id is None
    assert scope.repo_id is None
    assert scope.path is None
    assert scope.line_start is None
    assert scope.line_end is None


def test_scope_construction_full() -> None:
    scope = AgentContextScope(
        task_id="t1",
        repo_id="r1",
        path="src/foo.py",
        line_start=10,
        line_end=20,
    )
    assert scope.task_id == "t1"
    assert scope.repo_id == "r1"
    assert scope.path == "src/foo.py"
    assert scope.line_start == 10
    assert scope.line_end == 20


def test_scope_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        AgentContextScope(unknown_field="x")  # type: ignore[call-arg]


def test_scope_is_basemodel() -> None:
    assert issubclass(AgentContextScope, BaseModel)


def test_scope_accepts_partial_fields() -> None:
    scope = AgentContextScope(task_id="t1")
    assert scope.task_id == "t1"
    assert scope.repo_id is None


# --- Section 3: AgentContextBuilderInputs ---------------------------------


def test_inputs_construction_minimal() -> None:
    source = _build_snapshot_via_api()
    inputs = AgentContextBuilderInputs(
        source=source,
        scope=AgentContextScope(),
    )
    assert inputs.source is source
    assert inputs.scope.task_id is None
    assert inputs.line_provenance_results == []
    assert inputs.line_provenance_paths == []
    assert inputs.line_provenance_line_ranges == []
    assert (
        inputs.max_prompt_chars
        == GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP
    )


def test_inputs_extra_forbid() -> None:
    source = _build_snapshot_via_api()
    with pytest.raises(ValidationError):
        AgentContextBuilderInputs(  # type: ignore[call-arg]
            source=source,
            scope=AgentContextScope(),
            unknown_field=1,
        )


def test_inputs_max_prompt_chars_default_matches_cap() -> None:
    """Per doc-19:124-127 the default cap equals the
    GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP 20 000 char."""

    source = _build_snapshot_via_api()
    inputs = AgentContextBuilderInputs(
        source=source, scope=AgentContextScope()
    )
    assert inputs.max_prompt_chars == 20_000


def test_inputs_accepts_custom_max_prompt_chars() -> None:
    source = _build_snapshot_via_api()
    inputs = AgentContextBuilderInputs(
        source=source,
        scope=AgentContextScope(),
        max_prompt_chars=5_000,
    )
    assert inputs.max_prompt_chars == 5_000


def test_inputs_line_provenance_default_empty() -> None:
    source = _build_snapshot_via_api()
    inputs = AgentContextBuilderInputs(
        source=source, scope=AgentContextScope()
    )
    assert inputs.line_provenance_results == []
    assert inputs.line_provenance_paths == []
    assert inputs.line_provenance_line_ranges == []


def test_inputs_accepts_line_provenance_with_paths_and_ranges() -> None:
    source = _build_snapshot_via_api()
    lpr = _line_provenance_result()
    inputs = AgentContextBuilderInputs(
        source=source,
        scope=AgentContextScope(),
        line_provenance_results=[lpr],
        line_provenance_paths=["src/foo.py"],
        line_provenance_line_ranges=[(10, 20)],
    )
    assert len(inputs.line_provenance_results) == 1
    assert inputs.line_provenance_paths == ["src/foo.py"]
    assert inputs.line_provenance_line_ranges == [(10, 20)]


# --- Section 4: AgentContextBuilderGap ------------------------------------


def test_gap_construction_minimal() -> None:
    gap = AgentContextBuilderGap(
        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
        corpus_id="c1",
        reason="upstream_snapshot_missing",
        observed_at=datetime(2026, 5, 25, 0, 0, 0, tzinfo=timezone.utc),
    )
    assert gap.failure_id == "governance_agent_context_builder_failed"
    assert gap.corpus_id == "c1"
    assert gap.reason == "upstream_snapshot_missing"
    assert gap.evidence_payload == {}


def test_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        AgentContextBuilderGap(  # type: ignore[call-arg]
            failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
            unknown="x",
        )


def test_gap_rejects_wrong_failure_id_literal() -> None:
    with pytest.raises(ValidationError):
        AgentContextBuilderGap(
            failure_id="wrong_failure_id",  # type: ignore[arg-type]
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
        )


def test_gap_accepts_arbitrary_evidence_payload() -> None:
    gap = AgentContextBuilderGap(
        failure_id=AGENT_CONTEXT_BUILDER_FAILURE_ID,
        corpus_id="c",
        reason="r",
        observed_at=datetime.now(timezone.utc),
        evidence_payload={"key": "value", "count": 5},
    )
    assert gap.evidence_payload["key"] == "value"
    assert gap.evidence_payload["count"] == 5


# --- Section 5: AgentContextBuilderResult --------------------------------


def test_result_default_empty_gap_findings() -> None:
    result = AgentContextBuilderResult()
    assert result.context is None
    assert result.gap_findings == []


def test_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        AgentContextBuilderResult(unknown="x")  # type: ignore[call-arg]


def test_result_context_optional() -> None:
    result = AgentContextBuilderResult(context=None)
    assert result.context is None


# --- Section 6: build() happy path ---------------------------------------


def test_build_emits_context_for_valid_snapshot() -> None:
    source = _build_snapshot_via_api(findings=[_finding()])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert isinstance(result.context, GovernanceAgentContext)


def test_build_preserves_completeness_from_snapshot() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert source.snapshot is not None
    assert result.context.completeness == source.snapshot.completeness


def test_build_includes_findings_in_relevant_findings() -> None:
    finding = _finding()
    source = _build_snapshot_via_api(findings=[finding])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_findings) == 1
    assert result.context.relevant_findings[0].idempotency_key == finding.idempotency_key


def test_build_includes_recommendations_in_policy_guidance() -> None:
    rec = _recommendation()
    source = _build_snapshot_via_api(recommendations=[rec])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert len(result.context.policy_guidance) == 1
    assert result.context.policy_guidance[0].idempotency_key == rec.idempotency_key


def test_build_propagates_page_refs() -> None:
    pr = _page_ref()
    source = _build_snapshot_via_api(page_refs=[pr])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.page_refs == [pr.page_ref_id]
    assert result.context.omitted_detail_refs == [pr.page_ref_id]


def test_build_scope_carried_to_context_task_id() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope(task_id="t-99")
        )
    )
    assert result.context is not None
    assert result.context.task_id == "t-99"


def test_build_scope_carried_to_context_repo_id() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope(repo_id="r-99")
        )
    )
    assert result.context is not None
    assert result.context.repo_id == "r-99"


def test_build_returns_empty_gap_findings_on_clean_path() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.gap_findings == []


# --- Section 7: Scope filtering (task / repo / path / line range) ---------


def test_scope_filters_findings_by_task_id() -> None:
    f1 = _finding(
        idempotency_key="f-task-1",
        affected_scope={"task_id": "task-1", "repo_id": "repo-1"},
    )
    f2 = _finding(
        idempotency_key="f-task-2",
        affected_scope={"task_id": "task-2", "repo_id": "repo-1"},
    )
    source = _build_snapshot_via_api(findings=[f1, f2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(task_id="task-1"),
        )
    )
    assert result.context is not None
    keys = {f.idempotency_key for f in result.context.relevant_findings}
    assert keys == {"f-task-1"}


def test_scope_keeps_findings_without_task_id_when_filtering() -> None:
    """Cross-task findings (no task_id in affected_scope) are kept
    when scope.task_id is set -- they're broader-scope findings that
    apply to the task."""

    f1 = _finding(
        idempotency_key="f-cross",
        affected_scope={"repo_id": "repo-1"},  # no task_id
    )
    f2 = _finding(
        idempotency_key="f-task-2",
        affected_scope={"task_id": "task-2"},
    )
    source = _build_snapshot_via_api(findings=[f1, f2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(task_id="task-1"),
        )
    )
    assert result.context is not None
    keys = {f.idempotency_key for f in result.context.relevant_findings}
    assert keys == {"f-cross"}


def test_scope_filters_findings_by_repo_id() -> None:
    f1 = _finding(
        idempotency_key="f-repo-1",
        affected_scope={"repo_id": "repo-1"},
    )
    f2 = _finding(
        idempotency_key="f-repo-2",
        affected_scope={"repo_id": "repo-2"},
    )
    source = _build_snapshot_via_api(findings=[f1, f2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(repo_id="repo-1"),
        )
    )
    assert result.context is not None
    keys = {f.idempotency_key for f in result.context.relevant_findings}
    assert keys == {"f-repo-1"}


def test_scope_filters_recommendations_by_repo_id() -> None:
    r1 = _recommendation(
        idempotency_key="r-repo-1",
        recommendation_id="rec-1",
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml", "repo_id": "repo-1"},
            value={"wave_cap": 5},
            guardrails=["g"],
        ),
    )
    r2 = _recommendation(
        idempotency_key="r-repo-2",
        recommendation_id="rec-2",
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml", "repo_id": "repo-2"},
            value={"wave_cap": 5},
            guardrails=["g"],
        ),
    )
    source = _build_snapshot_via_api(recommendations=[r1, r2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(repo_id="repo-1"),
        )
    )
    assert result.context is not None
    keys = {r.idempotency_key for r in result.context.policy_guidance}
    assert keys == {"r-repo-1"}


def test_scope_keeps_recommendations_without_repo_id_in_scope() -> None:
    """Cross-repo recommendations (proposed_policy_artifact.scope without
    a repo_id key) are kept when scope.repo_id is set -- they're
    broader-scope guidance."""

    r1 = _recommendation(
        idempotency_key="r-no-repo",
        recommendation_id="rec-no-repo",
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml"},  # no repo_id
            value={"wave_cap": 5},
            guardrails=["g"],
        ),
    )
    r2 = _recommendation(
        idempotency_key="r-repo-2",
        recommendation_id="rec-2",
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml", "repo_id": "repo-2"},
            value={"wave_cap": 5},
            guardrails=["g"],
        ),
    )
    source = _build_snapshot_via_api(recommendations=[r1, r2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(repo_id="repo-1"),
        )
    )
    assert result.context is not None
    keys = {r.idempotency_key for r in result.context.policy_guidance}
    assert keys == {"r-no-repo"}


def test_scope_filters_line_provenance_by_task_id() -> None:
    lpr1 = _line_provenance_result(task_ids=["task-1", "task-2"])
    lpr2 = _line_provenance_result(task_ids=["task-3"])
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(task_id="task-1"),
            line_provenance_results=[lpr1, lpr2],
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_line_provenance) == 1


def test_scope_filters_line_provenance_by_path() -> None:
    lpr1 = _line_provenance_result(task_ids=["task-1"])
    lpr2 = _line_provenance_result(task_ids=["task-1"])
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(path="src/foo.py"),
            line_provenance_results=[lpr1, lpr2],
            line_provenance_paths=["src/foo.py", "src/bar.py"],
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_line_provenance) == 1


def test_scope_filters_line_provenance_by_line_range() -> None:
    lpr1 = _line_provenance_result()
    lpr2 = _line_provenance_result()
    lpr3 = _line_provenance_result()
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(
                path="src/foo.py", line_start=10, line_end=20
            ),
            line_provenance_results=[lpr1, lpr2, lpr3],
            line_provenance_paths=[
                "src/foo.py",
                "src/foo.py",
                "src/foo.py",
            ],
            line_provenance_line_ranges=[
                (5, 15),  # overlaps with 10-20
                (100, 200),  # outside
                (18, 25),  # overlaps with 10-20
            ],
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_line_provenance) == 2


def test_scope_passes_through_when_all_none() -> None:
    """A scope with all-None axes is a pass-through (no filtering)."""

    f1 = _finding(idempotency_key="f-1", affected_scope={"task_id": "t1"})
    f2 = _finding(idempotency_key="f-2", affected_scope={"task_id": "t2"})
    source = _build_snapshot_via_api(findings=[f1, f2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),  # all None
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_findings) == 2


def test_scope_filter_records_omitted_counts() -> None:
    """Findings filtered out by scope show up in omitted_counts."""

    f1 = _finding(idempotency_key="f-1", affected_scope={"task_id": "t1"})
    f2 = _finding(idempotency_key="f-2", affected_scope={"task_id": "t2"})
    f3 = _finding(idempotency_key="f-3", affected_scope={"task_id": "t1"})
    source = _build_snapshot_via_api(findings=[f1, f2, f3])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(task_id="t1"),
        )
    )
    assert result.context is not None
    assert result.context.omitted_counts["findings"] == 1  # f2 omitted
    assert result.context.truncated is True


def test_scope_filter_no_omissions_when_nothing_filtered() -> None:
    f1 = _finding(idempotency_key="f-1", affected_scope={"task_id": "t1"})
    f2 = _finding(idempotency_key="f-2", affected_scope={"task_id": "t1"})
    source = _build_snapshot_via_api(findings=[f1, f2])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(task_id="t1"),
        )
    )
    assert result.context is not None
    assert result.context.omitted_counts["findings"] == 0
    assert result.context.truncated is False


# --- Section 8: Budget enforcement (20 000 char cap) ----------------------


def test_build_respects_default_max_prompt_chars_cap() -> None:
    """Default max_prompt_chars equals the doc-19:124-127 20 000 char
    hard cap."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert (
        result.context.max_prompt_chars
        == GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP
    )


def test_build_hard_clamps_max_prompt_chars_to_cap() -> None:
    """Even if the caller asks for a larger budget, the builder
    HARD-CLAMPS to the doc-19:124-127 20 000 char hard cap."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=1_000_000,
        )
    )
    assert result.context is not None
    assert result.context.max_prompt_chars == 20_000


def test_build_accepts_smaller_max_prompt_chars() -> None:
    """A caller-provided smaller cap is honoured."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=10_000,
        )
    )
    assert result.context is not None
    assert result.context.max_prompt_chars == 10_000


def test_build_envelope_within_budget_for_empty_snapshot() -> None:
    """An empty snapshot produces an envelope well under 20 000 chars."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    serialised = result.context.model_dump_json()
    assert len(serialised) <= 20_000


def test_build_truncates_findings_when_budget_exceeded() -> None:
    """When the rank-sorted findings would exceed the budget, the
    builder truncates the list to fit."""

    # Use a small budget to force truncation.
    findings = [
        _finding(idempotency_key=f"f-{i}", affected_scope={"task_id": "t"})
        for i in range(20)
    ]
    source = _build_snapshot_via_api(findings=findings)
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=3_000,  # small budget
        )
    )
    assert result.context is not None
    serialised = result.context.model_dump_json()
    assert len(serialised) <= 3_000
    assert result.context.truncated is True
    assert result.context.omitted_counts["findings"] > 0


def test_build_truncation_sets_truncated_flag() -> None:
    findings = [_finding(idempotency_key=f"f-{i}") for i in range(20)]
    source = _build_snapshot_via_api(findings=findings)
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=3_000,
        )
    )
    assert result.context is not None
    assert result.context.truncated is True


def test_build_truncation_records_omitted_findings_count() -> None:
    findings = [_finding(idempotency_key=f"f-{i}") for i in range(20)]
    source = _build_snapshot_via_api(findings=findings)
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=3_000,
        )
    )
    assert result.context is not None
    included = len(result.context.relevant_findings)
    omitted = result.context.omitted_counts["findings"]
    assert included + omitted == 20


def test_build_ranks_findings_by_severity_first() -> None:
    """Higher-severity findings are included first per doc-19:190-191."""

    f_low = _finding(
        idempotency_key="f-low",
        severity="low",
        confidence=0.5,
        estimated_lost_hours=0.5,
    )
    f_high = _finding(
        idempotency_key="f-high",
        severity="critical",
        confidence=0.5,
        estimated_lost_hours=0.5,
    )
    source = _build_snapshot_via_api(findings=[f_low, f_high])
    # Set a budget large enough for exactly one finding.
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=2_700,  # synthetic tight budget
        )
    )
    assert result.context is not None
    # If only one fits, it should be the critical one.
    if len(result.context.relevant_findings) == 1:
        assert (
            result.context.relevant_findings[0].idempotency_key == "f-high"
        )


def test_build_ranks_findings_by_confidence_second() -> None:
    f_low_conf = _finding(
        idempotency_key="f-low-c",
        severity="medium",
        confidence=0.1,
        estimated_lost_hours=1.0,
    )
    f_high_conf = _finding(
        idempotency_key="f-high-c",
        severity="medium",
        confidence=0.9,
        estimated_lost_hours=1.0,
    )
    source = _build_snapshot_via_api(findings=[f_low_conf, f_high_conf])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    # Both fit in the default 20 000 cap; ensure they're both included
    # with the rank-ordering preserved.
    assert len(result.context.relevant_findings) == 2
    assert result.context.relevant_findings[0].idempotency_key == "f-high-c"
    assert result.context.relevant_findings[1].idempotency_key == "f-low-c"


def test_build_returns_none_when_envelope_alone_exceeds_budget() -> None:
    """Defensive guard: when even the empty envelope exceeds the cap,
    the builder returns context=None + prompt_budget_exceeded gap."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=10,  # tiny budget
        )
    )
    assert result.context is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "prompt_budget_exceeded"


# --- Section 9: Upstream snapshot missing path ----------------------------


def test_upstream_snapshot_missing_returns_none_context() -> None:
    """When the upstream SnapshotAPIResult carries no snapshot, the
    builder emits context=None + upstream_snapshot_missing gap."""

    # Build a SnapshotAPIResult with snapshot=None by passing an empty
    # corpus_id (snapshot API fails closed on corpus_id_empty).
    source = _build_snapshot_via_api(corpus_id="")
    assert source.snapshot is None
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is None


def test_upstream_snapshot_missing_emits_typed_gap() -> None:
    source = _build_snapshot_via_api(corpus_id="")
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    # At least one gap should be the upstream_snapshot_missing gap.
    reasons = [g.reason for g in result.gap_findings]
    assert "upstream_snapshot_missing" in reasons


def test_upstream_snapshot_missing_propagates_upstream_gaps() -> None:
    """Upstream gaps are lifted into the typed builder gap shape so the
    caller sees a single typed gap surface."""

    source = _build_snapshot_via_api(corpus_id="")
    assert len(source.gap_findings) >= 1  # upstream has corpus_id_empty
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    # The upstream gap should be propagated alongside our own.
    propagated_reasons = [
        g.reason for g in result.gap_findings if "upstream_snapshot_gap:" in g.reason
    ]
    assert len(propagated_reasons) >= 1


def test_upstream_snapshot_missing_all_gaps_use_typed_failure_id() -> None:
    source = _build_snapshot_via_api(corpus_id="")
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    for gap in result.gap_findings:
        assert gap.failure_id == AGENT_CONTEXT_BUILDER_FAILURE_ID


# --- Section 10: Parallel-list-length-mismatch path -----------------------


def test_parallel_paths_length_mismatch_returns_gap() -> None:
    source = _build_snapshot_via_api()
    lpr = _line_provenance_result()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(path="src/foo.py"),
            line_provenance_results=[lpr],
            line_provenance_paths=["a", "b"],  # length 2 != 1
        )
    )
    assert result.context is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "parallel_list_length_mismatch"


def test_parallel_ranges_length_mismatch_returns_gap() -> None:
    source = _build_snapshot_via_api()
    lpr = _line_provenance_result()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            line_provenance_results=[lpr],
            line_provenance_line_ranges=[(1, 5), (6, 10)],  # length 2 != 1
        )
    )
    assert result.context is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "parallel_list_length_mismatch"


# --- Section 11: Fail-closed (never raises) -------------------------------


def test_build_never_raises_on_empty_inputs() -> None:
    """The build() method MUST NEVER raise; verify with edge inputs."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    # No exception -> pass.
    assert result is not None


def test_build_never_raises_on_corpus_id_empty_upstream() -> None:
    source = _build_snapshot_via_api(corpus_id="")
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result is not None
    assert isinstance(result, AgentContextBuilderResult)


def test_build_never_raises_on_tiny_budget() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=1,
        )
    )
    assert result is not None


def test_build_never_raises_on_max_budget() -> None:
    source = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(20)],
        recommendations=[
            _recommendation(
                idempotency_key=f"r-{i}", recommendation_id=f"rec-{i}"
            )
            for i in range(10)
        ],
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result is not None


# --- Section 12: DIRECT annotation-identity REUSE assertions --------------


def test_reuses_governance_agent_context_directly() -> None:
    """The typed AgentContextBuilderResult.context field is annotated
    against the Slice 19 1st sub-slice GovernanceAgentContext typed
    BaseModel verbatim (no second source of truth)."""

    hints = typing.get_type_hints(AgentContextBuilderResult)
    union_args = typing.get_args(hints["context"])
    assert GovernanceAgentContext in union_args


def test_reuses_snapshot_api_result_directly() -> None:
    hints = typing.get_type_hints(AgentContextBuilderInputs)
    assert hints["source"] is SnapshotAPIResult


def test_reuses_agent_context_scope_directly() -> None:
    hints = typing.get_type_hints(AgentContextBuilderInputs)
    assert hints["scope"] is AgentContextScope


def test_reuses_line_provenance_result_directly() -> None:
    hints = typing.get_type_hints(AgentContextBuilderInputs)
    # The annotation is list[LineProvenanceResult]; check via
    # typing.get_args.
    list_arg = typing.get_args(hints["line_provenance_results"])
    assert LineProvenanceResult in list_arg


def test_reuses_governance_agent_context_max_prompt_chars_cap() -> None:
    """The constant is imported from the Slice 19 1st sub-slice
    module; verify import-identity."""

    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP as builder_cap,
    )

    assert builder_cap == GOVERNANCE_AGENT_CONTEXT_MAX_PROMPT_CHARS_CAP
    assert builder_cap == 20_000


def test_no_local_redefinition_of_reused_classes() -> None:
    """The module MUST NOT define its own GovernanceAgentContext /
    GovernanceFinding / GovernancePolicyRecommendation /
    LineProvenanceResult / SnapshotAPIResult / SnapshotAPIGap /
    CompletenessState."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    # All these must be REFERENCES to the Slice 13a/14/16/17/19-1st/
    # 19-2nd typed shapes; the module must NOT redefine them.
    assert mod.GovernanceAgentContext is GovernanceAgentContext
    assert mod.GovernanceFinding is GovernanceFinding
    assert mod.GovernancePolicyRecommendation is GovernancePolicyRecommendation
    assert mod.LineProvenanceResult is LineProvenanceResult
    assert mod.SnapshotAPIResult is SnapshotAPIResult
    assert mod.SnapshotAPIGap is SnapshotAPIGap


def test_module_imports_completeness_state_from_slice_13a() -> None:
    """The module imports the Slice 13a CompletenessState Literal
    directly (no re-definition)."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    assert mod.CompletenessState is CompletenessState


# --- Section 13: Failure-router 4-pure-data add discipline ----------------


def test_failure_router_failure_types_contains_id() -> None:
    """The new typed failure id MUST be registered in FAILURE_TYPES
    (the runtime tuple)."""

    assert "governance_agent_context_builder_failed" in FAILURE_TYPES


def test_failure_router_failure_type_literal_contains_id() -> None:
    """The new typed failure id MUST be in the FailureType Literal."""

    lit_args = typing.get_args(FailureType)
    assert "governance_agent_context_builder_failed" in lit_args


def test_failure_router_route_table_contains_id() -> None:
    """The new typed failure id MUST have a route table entry."""

    key = ("evidence_corruption", "governance_agent_context_builder_failed")
    assert key in ROUTE_TABLE


def test_failure_router_reuses_retry_governance_projection_action() -> None:
    """The new typed failure id routes to the EXISTING
    retry_governance_projection action (REUSED from Slice 14 2nd
    sub-slice; NOT a new action)."""

    key = ("evidence_corruption", "governance_agent_context_builder_failed")
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
        if key[1] == "governance_agent_context_builder_failed"
    ]
    assert len(matching) == 1
    assert matching[0][0] == "evidence_corruption"


def test_failure_router_governance_failure_ids_total_15() -> None:
    """After Slice 19 5th sub-slice: 15 typed Slice 17+ governance
    failure ids registered: 5 Slice 17 + 6 Slice 18 + 4 Slice 19."""

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
    ]
    all_ids = slice_17_ids + slice_18_ids + slice_19_ids
    assert len(all_ids) == 15
    for fid in all_ids:
        assert fid in FAILURE_TYPES, f"{fid} missing from FAILURE_TYPES"


# --- Section 14: doc-19 AC enforcement ------------------------------------


def test_ac1_bounded_reproducible_evidence_cited_structured_first() -> None:
    """AC1 (doc-19:224): Reports are bounded, reproducible,
    evidence-cited, and structured first.

    * Bounded: max_prompt_chars enforced.
    * Reproducible: same inputs -> same context (pure function).
    * Evidence-cited: page_refs + omitted_detail_refs surfaces.
    * Structured first: Pydantic BaseModel.
    """

    source = _build_snapshot_via_api(
        findings=[_finding()], page_refs=[_page_ref()]
    )
    inputs = AgentContextBuilderInputs(
        source=source, scope=AgentContextScope()
    )
    result1 = _builder().build(inputs)
    result2 = _builder().build(inputs)
    assert result1.context is not None and result2.context is not None
    # Bounded
    assert (
        len(result1.context.model_dump_json())
        <= result1.context.max_prompt_chars
    )
    # Reproducible
    # We compare modulo task_id/repo_id ordering; the typed surface is
    # deterministic for the same inputs (excluding any timestamps in
    # the typed surface).
    # The typed GovernanceAgentContext does not carry a timestamp on
    # itself (per the Slice 19 1st sub-slice typed shape) -- only the
    # inner Slice 16 / Slice 17 / Slice 18 records carry timestamps,
    # but those come from the upstream snapshot verbatim.
    assert result1.context.relevant_findings == result2.context.relevant_findings
    assert result1.context.page_refs == result2.context.page_refs
    # Evidence-cited
    assert len(result1.context.page_refs) == 1
    # Structured first
    assert isinstance(result1.context, BaseModel)


def test_ac2_truncated_carries_page_refs_when_paged() -> None:
    """AC2 (doc-19:225-226): Truncated reports MUST carry exact page
    refs and completeness metadata."""

    page_ref = _page_ref()
    source = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(20)],
        page_refs=[page_ref],
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            max_prompt_chars=3_000,
        )
    )
    assert result.context is not None
    assert result.context.truncated is True
    assert page_ref.page_ref_id in result.context.page_refs


def test_ac2_preview_only_completeness_propagated() -> None:
    """AC2: preview_only completeness from upstream is propagated."""

    source = _build_snapshot_via_api(
        completeness_override="preview_only"
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.completeness == "preview_only"


def test_ac3_compact_governance_context_at_task_execute_time() -> None:
    """AC3 (doc-19:227): Workflow agents can receive compact governance
    context. Verified by the typed GovernanceAgentContext shape itself."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope(task_id="t-task-1")
        )
    )
    assert result.context is not None
    assert isinstance(result.context, GovernanceAgentContext)
    assert result.context.task_id == "t-task-1"


def test_ac5_policy_guidance_authority_is_advisory_only() -> None:
    """AC5 (doc-19:230-231): Workflow agents receive governance policy
    guidance only as advisory context.

    Enforced by the typed policy_guidance_authority: Literal['advisory_only']
    field default."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.policy_guidance_authority == "advisory_only"


def test_ac5_policy_guidance_authority_cannot_be_overridden() -> None:
    """AC5: the typed Literal default cannot be overridden at
    construction (Pydantic Literal rejects other values).

    This is a property of the Slice 19 1st sub-slice typed shape; we
    verify it by trying to construct the typed shape with a different
    value."""

    with pytest.raises(ValidationError):
        GovernanceAgentContext(
            task_id=None,
            repo_id=None,
            relevant_findings=[],
            relevant_line_provenance=[],
            policy_guidance=[],
            policy_guidance_authority="overridden",  # type: ignore[arg-type]
            omitted_detail_refs=[],
            omitted_counts={},
            completeness="complete",
            page_refs=[],
            truncated=False,
            max_prompt_chars=20_000,
        )


def test_ac6_omitted_counts_always_populated() -> None:
    """AC6 (doc-19:232-233): the typed omitted_counts surface is always
    populated so consumers see the full omission picture."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    # omitted_counts is a dict; check it has the expected keys.
    assert "findings" in result.context.omitted_counts
    assert "recommendations" in result.context.omitted_counts
    assert "line_provenance" in result.context.omitted_counts


def test_ac7_builder_class_has_one_public_method() -> None:
    """AC7 (doc-19:234) + doc-19:348-349: read-only contract preserved.
    The builder class has exactly ONE public method (build)."""

    public_methods = [
        m
        for m in dir(GovernanceAgentContextBuilder)
        if not m.startswith("_")
    ]
    assert public_methods == ["build"], (
        f"GovernanceAgentContextBuilder must have exactly 1 public method "
        f"(build); found: {public_methods}"
    )


def test_ac7_no_mutation_methods_on_basemodels() -> None:
    """AC7: no mutation methods (activate_/approve_/merge_/mutate_/
    write_/persist_/checkpoint_) on the typed BaseModels."""

    typed_models = [
        AgentContextScope,
        AgentContextBuilderInputs,
        AgentContextBuilderGap,
        AgentContextBuilderResult,
    ]
    forbidden_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "mutate_",
        "write_",
        "persist_",
        "checkpoint_",
    )
    for model in typed_models:
        for attr_name in dir(model):
            if attr_name.startswith(forbidden_prefixes):
                raise AssertionError(
                    f"{model.__name__} has forbidden mutation method "
                    f"{attr_name!r}"
                )


def test_ac7_no_dag_artifact_key_string_literals_in_module() -> None:
    """AC7 + doc-19:348-349: no dag-* execution-authority
    artifact-key string literals in the module source."""

    from pathlib import Path

    src_path = Path(
        "src/iriai_build_v2/execution_control/governance_agent_context_builder.py"
    )
    src = src_path.read_text()
    # Allow doc-N citations like "dag-group:*" inside docstrings as
    # context references; verify no actual string literal mints dag-*
    # artifact keys via assignment / function calls.
    forbidden_patterns = [
        '"dag-commit',
        '"dag-group:',
        '"dag-merge',
        '"dag-route',
        "'dag-commit",
        "'dag-group:",
        "'dag-merge",
        "'dag-route",
    ]
    for pattern in forbidden_patterns:
        # Strip docstring-only contexts (where dag-* may appear as
        # citations) is non-trivial; for this test we check no
        # string-literal assignment or call argument mints dag-* keys.
        # The simplest enforceable rule: NO occurrences anywhere in
        # the module source.
        assert pattern not in src, (
            f"Module source must not mint dag-* artifact-key string "
            f"literals; found {pattern!r}"
        )


def test_ac7_does_not_extend_control_plane_writer_methods() -> None:
    """AC7 + doc-19:348-349: governance writer MUST NOT extend the
    Slice 10c-1 CONTROL_PLANE_WRITER_METHODS set.

    Verified by ensuring the module does not import or reference
    CONTROL_PLANE_WRITER_METHODS (any import would be suspicious; any
    update / mutation would be a violation)."""

    from pathlib import Path

    src_path = Path(
        "src/iriai_build_v2/execution_control/governance_agent_context_builder.py"
    )
    src = src_path.read_text()
    # The token may appear only in docstring citations; check no
    # actual code path (e.g. CONTROL_PLANE_WRITER_METHODS.add) is
    # invoked.
    forbidden_calls = [
        "CONTROL_PLANE_WRITER_METHODS.add(",
        "CONTROL_PLANE_WRITER_METHODS.update(",
        "CONTROL_PLANE_WRITER_METHODS.extend(",
        "CONTROL_PLANE_WRITER_METHODS |=",
    ]
    for pattern in forbidden_calls:
        assert pattern not in src, (
            f"Module source must not extend CONTROL_PLANE_WRITER_METHODS; "
            f"found {pattern!r}"
        )


# --- Section 15: Slice 21-conditional ContextLayerPackageSummary deferral


def test_context_does_not_carry_context_package_field_yet() -> None:
    """Per doc-19:89-101 + doc-19:125-127 the ContextLayerPackageSummary
    field is DEFERRED to Slice 21. The Slice 19 1st sub-slice
    GovernanceAgentContext typed shape does NOT include a
    context_package attribute; this 5th sub-slice builder does NOT
    populate it."""

    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    # The typed shape MUST NOT have a context_package field at this
    # 5th sub-slice (Slice 21 will tighten the typed surface).
    assert not hasattr(result.context, "context_package")


def test_slice_21_deferral_documented_in_module() -> None:
    """The module MUST document the Slice 21-conditional deferral."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    assert "Slice 21" in mod.__doc__
    assert "DEFERRED" in mod.__doc__


def test_slice_21_deferral_documented_in_builder_class() -> None:
    """The builder class docstring MUST document the Slice 21-conditional
    deferral."""

    assert GovernanceAgentContextBuilder.__doc__ is not None
    assert "Slice 21" in GovernanceAgentContextBuilder.__doc__
    assert "DEFERRED" in GovernanceAgentContextBuilder.__doc__


# --- Section 16: Activation-authority boundary ---------------------------


def test_builder_is_stateless() -> None:
    """The builder is stateless (no instance state beyond
    construction); reusing the same instance across builds produces
    consistent results."""

    builder = _builder()
    source = _build_snapshot_via_api()
    inputs = AgentContextBuilderInputs(
        source=source, scope=AgentContextScope()
    )
    r1 = builder.build(inputs)
    r2 = builder.build(inputs)
    assert r1.context is not None and r2.context is not None
    assert r1.context.task_id == r2.context.task_id


def test_builder_does_not_have_init_signature() -> None:
    """The builder uses the default __init__; no state to initialise.

    Verified via the class-level signature (since inspect.signature on
    the class resolves to the meaningful constructor signature, while
    inspect.signature on __init__ may return the default object.__init__
    signature with *args/**kwargs)."""

    import inspect

    # Class-level signature (the meaningful one).
    sig = inspect.signature(GovernanceAgentContextBuilder)
    params = list(sig.parameters.keys())
    assert params == [], (
        f"GovernanceAgentContextBuilder should construct with no "
        f"arguments; found {params}"
    )


# --- Section 17: edge cases ------------------------------------------------


def test_empty_findings_empty_recommendations_works() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.relevant_findings == []
    assert result.context.policy_guidance == []
    assert result.context.relevant_line_provenance == []


def test_empty_line_provenance_with_scope_path_does_not_crash() -> None:
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(path="src/foo.py"),
        )
    )
    # Even with no line provenance and scope.path set, the builder
    # MUST NOT crash; the context is returned empty.
    assert result.context is not None
    assert result.context.relevant_line_provenance == []


def test_line_provenance_without_path_metadata_passes_through() -> None:
    """If line_provenance_results is non-empty but
    line_provenance_paths is empty, the builder does NOT filter by
    path (treats rows as 'no path metadata')."""

    source = _build_snapshot_via_api()
    lpr = _line_provenance_result()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(path="src/foo.py"),
            line_provenance_results=[lpr],
            # line_provenance_paths intentionally empty
        )
    )
    assert result.context is not None
    # Row should be included since no path metadata to filter on.
    assert len(result.context.relevant_line_provenance) == 1


def test_line_provenance_without_range_metadata_passes_through() -> None:
    """If line_provenance_line_ranges is empty but scope has line
    bounds, the builder does NOT filter by line range."""

    source = _build_snapshot_via_api()
    lpr = _line_provenance_result()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(
                path="src/foo.py", line_start=1, line_end=5
            ),
            line_provenance_results=[lpr],
            line_provenance_paths=["src/foo.py"],
            # line_provenance_line_ranges intentionally empty
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_line_provenance) == 1


def test_line_provenance_no_intersection_excluded() -> None:
    """Line provenance whose range does NOT intersect the scope range
    is excluded."""

    source = _build_snapshot_via_api()
    lpr = _line_provenance_result()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(
                path="src/foo.py", line_start=100, line_end=200
            ),
            line_provenance_results=[lpr],
            line_provenance_paths=["src/foo.py"],
            line_provenance_line_ranges=[(1, 10)],  # outside
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_line_provenance) == 0


def test_omitted_counts_include_upstream_truncation() -> None:
    """Upstream omitted-findings count is added to the typed
    GovernanceAgentContext.omitted_counts."""

    source = _build_snapshot_via_api(
        omitted_findings_count=5
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.omitted_counts["findings"] >= 5


def test_omitted_counts_include_upstream_replay_results() -> None:
    """Upstream replay_results omissions are preserved per the AC6
    visibility contract."""

    source = _build_snapshot_via_api(
        omitted_replay_results_count=3
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.omitted_counts["replay_results"] == 3


def test_omitted_counts_include_upstream_page_refs() -> None:
    """Upstream page_refs omissions are preserved."""

    source = _build_snapshot_via_api(
        omitted_page_refs_count=2
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.omitted_counts["page_refs"] == 2


def test_upstream_truncated_propagates_to_context() -> None:
    """If the upstream snapshot is truncated, the context is also
    truncated."""

    source = _build_snapshot_via_api(omitted_findings_count=1)
    assert source.snapshot is not None
    assert source.snapshot.truncated is True
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.truncated is True


# --- Section 18: more reproducibility tests -------------------------------


def test_build_reproducible_same_inputs() -> None:
    """Per doc-19:218: report generation is reproducible for the same
    corpus id."""

    source1 = _build_snapshot_via_api(findings=[_finding()])
    inputs = AgentContextBuilderInputs(
        source=source1, scope=AgentContextScope()
    )
    r1 = _builder().build(inputs)
    r2 = _builder().build(inputs)
    assert r1.context is not None and r2.context is not None
    assert (
        r1.context.relevant_findings == r2.context.relevant_findings
    )


def test_build_relevant_findings_preserves_finding_identity() -> None:
    """The typed Slice 16 finding records are passed through verbatim
    (no re-creation; identity preserved)."""

    finding = _finding()
    source = _build_snapshot_via_api(findings=[finding])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    # The Slice 19 2nd sub-slice snapshot API may re-create the
    # finding via the typed Pydantic shape; identity is NOT guaranteed.
    # But the typed values MUST be preserved.
    assert result.context.relevant_findings[0].idempotency_key == finding.idempotency_key
    assert result.context.relevant_findings[0].severity == finding.severity


# --- Section 19: line_provenance serialised to dict[str, Any] -------------


def test_line_provenance_serialised_to_dict() -> None:
    """The typed GovernanceAgentContext.relevant_line_provenance field
    is list[dict[str, Any]] per the Slice 19 1st sub-slice typed-shape
    foundation; the builder serialises typed LineProvenanceResult rows
    via model_dump(mode='json')."""

    lpr = _line_provenance_result()
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            line_provenance_results=[lpr],
        )
    )
    assert result.context is not None
    assert len(result.context.relevant_line_provenance) == 1
    lpr_dict = result.context.relevant_line_provenance[0]
    assert isinstance(lpr_dict, dict)
    assert lpr_dict["commit_hashes"] == lpr.commit_hashes
    assert lpr_dict["task_ids"] == lpr.task_ids


def test_line_provenance_dict_includes_completeness() -> None:
    lpr = _line_provenance_result(completeness="paged")
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(),
            line_provenance_results=[lpr],
        )
    )
    assert result.context is not None
    lpr_dict = result.context.relevant_line_provenance[0]
    assert lpr_dict["completeness"] == "paged"


# --- Section 20: documentation / type-hint sanity -------------------------


def test_builder_class_docstring_mentions_doc_19_step_5() -> None:
    assert GovernanceAgentContextBuilder.__doc__ is not None
    assert "157-160" in GovernanceAgentContextBuilder.__doc__
    assert "step 5" in GovernanceAgentContextBuilder.__doc__.lower()


def test_builder_class_docstring_mentions_4_scoping_axes() -> None:
    assert GovernanceAgentContextBuilder.__doc__ is not None
    text = GovernanceAgentContextBuilder.__doc__.lower()
    assert "task" in text
    assert "repo" in text
    assert "path" in text
    assert "line" in text


def test_failure_id_docstring_cites_doc_19_184_194() -> None:
    """The typed failure id docstring cites doc-19:184-194 edge-case
    rows + doc-14:242-243 NON-blocking contract."""

    from iriai_build_v2.execution_control import (
        governance_agent_context_builder as mod,
    )

    # Find the docstring via the module attribute reference (Python
    # exposes module-level constant docstrings as the trailing
    # string literal in source; check the module source).
    from pathlib import Path

    src_path = Path(
        "src/iriai_build_v2/execution_control/governance_agent_context_builder.py"
    )
    src = src_path.read_text()
    assert "doc-19:184-194" in src
    assert "doc-14:242-243" in src
    assert "NON-blocking" in src or "NON-BLOCKING" in src


# --- Section 21: scope-edge cases ------------------------------------------


def test_scope_with_partial_line_range_no_filtering() -> None:
    """If only line_start is set (line_end is None), no per-line
    filtering occurs."""

    lpr = _line_provenance_result()
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(
                path="src/foo.py", line_start=10, line_end=None
            ),
            line_provenance_results=[lpr],
            line_provenance_paths=["src/foo.py"],
            line_provenance_line_ranges=[(100, 200)],  # would NOT match if filtering
        )
    )
    assert result.context is not None
    # Should be included since line filtering doesn't fire without
    # both bounds.
    assert len(result.context.relevant_line_provenance) == 1


def test_findings_with_empty_affected_scope_kept() -> None:
    """Findings whose affected_scope is an empty dict are KEPT (broad
    cross-scope findings)."""

    f1 = _finding(idempotency_key="f-empty", affected_scope={})
    source = _build_snapshot_via_api(findings=[f1])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(task_id="t-99"),
        )
    )
    assert result.context is not None
    keys = {f.idempotency_key for f in result.context.relevant_findings}
    assert "f-empty" in keys


def test_recommendation_with_artifact_no_repo_id_kept() -> None:
    """A recommendation whose proposed_policy_artifact scope dict does
    NOT carry a repo_id key is KEPT (broad cross-repo)."""

    r1 = _recommendation(
        idempotency_key="r-no-repo",
        recommendation_id="rec-no-repo",
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"lane_id": "ml"},  # no repo_id
            value={"wave_cap": 5},
            guardrails=["g"],
        ),
    )
    source = _build_snapshot_via_api(recommendations=[r1])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(repo_id="repo-99"),
        )
    )
    assert result.context is not None
    keys = {r.idempotency_key for r in result.context.policy_guidance}
    assert "r-no-repo" in keys


def test_omitted_detail_refs_mirrors_page_refs() -> None:
    """The typed omitted_detail_refs is populated from the upstream
    page_refs (refs-only drilldown surface per doc-19:111 + doc-19:114)."""

    pr = _page_ref()
    source = _build_snapshot_via_api(page_refs=[pr])
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    assert result.context is not None
    assert result.context.omitted_detail_refs == [pr.page_ref_id]
    assert result.context.page_refs == [pr.page_ref_id]


# --- Section 22: severity ranking helper -----------------------------------


def test_severity_weights_table_keys() -> None:
    """The severity weights table covers all 5 doc-16 severity levels."""

    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        _SEVERITY_WEIGHTS,
    )

    assert set(_SEVERITY_WEIGHTS.keys()) == {
        "critical",
        "high",
        "medium",
        "low",
        "info",
    }


def test_severity_weights_descending() -> None:
    """The severity weights are strictly descending from critical to
    info."""

    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        _SEVERITY_WEIGHTS,
    )

    assert (
        _SEVERITY_WEIGHTS["critical"]
        > _SEVERITY_WEIGHTS["high"]
        > _SEVERITY_WEIGHTS["medium"]
        > _SEVERITY_WEIGHTS["low"]
        > _SEVERITY_WEIGHTS["info"]
    )


def test_finding_rank_key_severity_weight() -> None:
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        _finding_rank_key,
    )

    f_critical = _finding(severity="critical")
    f_low = _finding(severity="low")
    assert _finding_rank_key(f_critical) > _finding_rank_key(f_low)


def test_finding_rank_key_includes_confidence() -> None:
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        _finding_rank_key,
    )

    f_high = _finding(severity="medium", confidence=0.95)
    f_low = _finding(severity="medium", confidence=0.10)
    # Same severity; higher confidence ranks higher.
    assert _finding_rank_key(f_high) > _finding_rank_key(f_low)


def test_finding_rank_key_includes_lost_hours() -> None:
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        _finding_rank_key,
    )

    f_more_hours = _finding(
        severity="medium", confidence=0.5, estimated_lost_hours=10.0
    )
    f_less_hours = _finding(
        severity="medium", confidence=0.5, estimated_lost_hours=1.0
    )
    assert _finding_rank_key(f_more_hours) > _finding_rank_key(
        f_less_hours
    )


def test_finding_rank_key_handles_none_lost_hours() -> None:
    from iriai_build_v2.execution_control.governance_agent_context_builder import (
        _finding_rank_key,
    )

    f = _finding(estimated_lost_hours=None)
    key = _finding_rank_key(f)
    assert key[2] == 0.0


# --- Section 23: corpus_id propagation ------------------------------------


def test_corpus_id_extracted_from_snapshot() -> None:
    source = _build_snapshot_via_api(corpus_id="corpus-A")
    # Sanity: snapshot has the right corpus_id.
    assert source.snapshot is not None
    assert source.snapshot.corpus_id == "corpus-A"


def test_corpus_id_in_propagated_upstream_gap() -> None:
    """Propagated upstream gaps carry the corpus_id (when one is
    recoverable from the snapshot or the upstream gap)."""

    source = _build_snapshot_via_api(corpus_id="")
    # The upstream snapshot API uses '<empty>' as the corpus_id
    # sentinel when corpus_id is empty/whitespace; the builder
    # preserves the upstream sentinel verbatim in propagated gaps.
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    main_gap = [
        g
        for g in result.gap_findings
        if g.reason == "upstream_snapshot_missing"
    ][0]
    # The corpus_id is recovered from the first upstream gap finding
    # (per _extract_corpus_id fallback); the upstream API emits
    # '<empty>' as the sentinel.
    assert main_gap.corpus_id == "<empty>"


def test_builder_corpus_id_falls_back_to_first_upstream_gap() -> None:
    """When upstream snapshot is None AND upstream has at least one
    gap finding, the corpus_id falls back to the first gap's
    corpus_id."""

    # Manually create a SnapshotAPIResult with no snapshot and a fake
    # upstream gap carrying a non-empty corpus_id.
    fake_upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="upstream-corpus",
        reason="snapshot_construction_failed",
        observed_at=datetime.now(timezone.utc),
    )
    source = SnapshotAPIResult(
        snapshot=None,
        gap_findings=[fake_upstream_gap],
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    main_gap = [
        g
        for g in result.gap_findings
        if g.reason == "upstream_snapshot_missing"
    ][0]
    assert main_gap.corpus_id == "upstream-corpus"


# --- Section 24: more failure-router checks --------------------------------


def test_failure_router_route_table_message_cites_slice_19_5th() -> None:
    """The route table reason MUST cite Slice 19 5th + doc-19:157-160
    so future readers can trace the failure id back to this sub-slice."""

    key = ("evidence_corruption", "governance_agent_context_builder_failed")
    assert key in ROUTE_TABLE
    route = ROUTE_TABLE[key]
    message = route.reason
    assert "Slice 19 5th" in message
    assert "doc-19:157-160" in message
    assert "non-blocking" in message
    assert "doc-14:242-243" in message


# --- Section 25: more upstream-propagation checks --------------------------


def test_propagated_upstream_gap_preserves_reason_with_prefix() -> None:
    """Propagated upstream gaps' reason strings are prefixed with
    'upstream_snapshot_gap:' so consumers can distinguish them from
    builder-emitted gaps."""

    source = _build_snapshot_via_api(corpus_id="")
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(propagated) >= 1
    # The propagated reason includes the original upstream reason.
    assert "corpus_id_empty" in propagated[0].reason


def test_propagated_upstream_gap_preserves_observed_at() -> None:
    """Propagated upstream gaps preserve the upstream observed_at
    timestamp."""

    upstream_time = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    fake_upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="corpus-A",
        reason="snapshot_construction_failed",
        observed_at=upstream_time,
    )
    source = SnapshotAPIResult(
        snapshot=None,
        gap_findings=[fake_upstream_gap],
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(propagated) >= 1
    assert propagated[0].observed_at == upstream_time


def test_propagated_upstream_gap_preserves_evidence_payload() -> None:
    fake_upstream_gap = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="corpus-A",
        reason="custom_reason",
        observed_at=datetime.now(timezone.utc),
        evidence_payload={"key": "value", "n": 5},
    )
    source = SnapshotAPIResult(
        snapshot=None,
        gap_findings=[fake_upstream_gap],
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source, scope=AgentContextScope()
        )
    )
    propagated = [
        g
        for g in result.gap_findings
        if g.reason.startswith("upstream_snapshot_gap:")
    ]
    assert len(propagated) >= 1
    assert propagated[0].evidence_payload["key"] == "value"
    assert propagated[0].evidence_payload["n"] == 5


# --- Section 26: end-to-end multi-axis tests -------------------------------


def test_end_to_end_task_repo_scope_with_findings_and_recommendations() -> None:
    """Realistic scenario: a task-execute agent asks for context
    scoped to (task_id, repo_id); the builder returns relevant
    findings + recommendations."""

    f_match = _finding(
        idempotency_key="f-match",
        affected_scope={"task_id": "t-prod", "repo_id": "r-prod"},
    )
    f_miss = _finding(
        idempotency_key="f-miss",
        affected_scope={"task_id": "t-dev", "repo_id": "r-prod"},
    )
    r_match = _recommendation(
        idempotency_key="r-match",
        recommendation_id="rec-match",
        proposed_policy_artifact=SchedulerPolicyArtifact(
            policy_kind="wave_cap",
            scope={"repo_id": "r-prod"},
            value={"wave_cap": 5},
            guardrails=["g"],
        ),
    )
    source = _build_snapshot_via_api(
        findings=[f_match, f_miss], recommendations=[r_match]
    )
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(
                task_id="t-prod", repo_id="r-prod"
            ),
        )
    )
    assert result.context is not None
    assert result.context.task_id == "t-prod"
    assert result.context.repo_id == "r-prod"
    f_keys = {f.idempotency_key for f in result.context.relevant_findings}
    assert f_keys == {"f-match"}
    r_keys = {r.idempotency_key for r in result.context.policy_guidance}
    assert r_keys == {"r-match"}


def test_end_to_end_path_line_scope_with_provenance() -> None:
    """A bug-fix agent asks for context scoped to (path, line_range);
    the builder returns matching line-provenance results."""

    lpr_match = _line_provenance_result(task_ids=["t-prod"])
    lpr_miss = _line_provenance_result(task_ids=["t-prod"])
    source = _build_snapshot_via_api()
    result = _builder().build(
        AgentContextBuilderInputs(
            source=source,
            scope=AgentContextScope(
                path="src/foo.py", line_start=10, line_end=20
            ),
            line_provenance_results=[lpr_match, lpr_miss],
            line_provenance_paths=["src/foo.py", "src/bar.py"],
            line_provenance_line_ranges=[(5, 25), (1, 100)],
        )
    )
    assert result.context is not None
    # Only lpr_match matches (path=src/foo.py + range overlaps 10-20).
    assert len(result.context.relevant_line_provenance) == 1
