"""Slice 19 2nd sub-slice -- unit tests for the typed snapshot API at
``execution_control/governance_snapshot_api.py``.

Covers the doc-19:151 step 2 typed snapshot API surface:

* :data:`SNAPSHOT_API_FAILURE_ID` -- the typed failure id
  (``governance_snapshot_api_failed``) registered under the EXISTING
  ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` action.
* :class:`SnapshotAPIInputs` typed inputs BaseModel (extra-forbid;
  budget overrides default to Slice 19 1st sub-slice constants).
* :class:`SnapshotAPICorpus` typed corpus BaseModel (extra-forbid;
  carries pre-fetched bounded typed Slice 16/17/18 rows).
* :class:`SnapshotAPIGap` typed gap BaseModel (extra-forbid; carries
  reason + observed_at + optional evidence_payload).
* :class:`SnapshotAPIResult` typed result BaseModel (extra-forbid;
  snapshot | None + gap_findings list).
* :class:`GovernanceSnapshotAPI.build_snapshot(...)` -- the
  projection method:
  * Happy-path -> typed :class:`GovernanceSnapshot` emitted.
  * Bounded-reads discipline (LIMIT cap+1 truncation across 4 list
    dimensions; truncated=True; omitted_counts).
  * snapshot_digest reproducibility (same inputs -> same digest;
    different bounded inputs -> different digest).
  * Empty corpus_id -> typed gap on ``corpus_id_empty``.
  * API NEVER raises (typed gap projection on construction failure).
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :data:`CompletenessState` + :data:`EvidenceQuality` + Slice 16
  :class:`GovernanceFinding` + Slice 17
  :class:`GovernancePolicyRecommendation` + Slice 18
  :class:`CounterfactualResult` + Slice 19 1st sub-slice
  :class:`GovernanceSnapshot`.
* Failure-router 4-pure-data add discipline (Slice 14 2nd sub-slice
  precedent verbatim).
* AC1/AC2/AC6/AC7 enforcement tests per doc-19 § Acceptance
  Criteria.

Per the implementer prompt § "Non-Negotiables" -- fail-closed on
every Pydantic field validator; no executor wiring outside this
slice's own acceptance tests; the Slice 13a + Slice 13A + Slice 14 +
Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 1st sub-slice
modules + tests remain READ-ONLY this sub-slice.
"""

from __future__ import annotations

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
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS,
    GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS,
    GovernanceSnapshot,
    compute_governance_snapshot_digest,
)
from iriai_build_v2.execution_control.governance_snapshot_api import (
    SNAPSHOT_API_FAILURE_ID,
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
    EvidenceQuality,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
)


# --- Fixture builders (mirror Slice 19 1st sub-slice fixtures) -------------


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-19-2",
        digest="b" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    base: dict[str, object] = dict(
        page_ref_id="page-ref-19-2-1",
        authority="typed_journal",
        source_ref_id="ref-19-2",
        digest="c" * 64,
        completeness="complete",
        exact=True,
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)


def _finding(**overrides: object) -> GovernanceFinding:
    base: dict[str, object] = dict(
        idempotency_key="finding-key-19-2-a",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk", "runtime": "claude-sdk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-27#slice-19-2nd"],
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
        idempotency_key="recommendation-key-19-2-a",
        recommendation_id="rec-19-2-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-19-2-a"],
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
        result_id="result-19-2-1",
        result_version="v1",
        scenario_id="scenario-19-2-1",
        corpus_id="corpus-19-2",
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
        supporting_finding_ids=["finding-key-19-2-a"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)


def _api() -> GovernanceSnapshotAPI:
    return GovernanceSnapshotAPI()


# --- Section 1: module surface --------------------------------------------


def test_failure_id_literal_value() -> None:
    assert SNAPSHOT_API_FAILURE_ID == "governance_snapshot_api_failed"


def test_failure_id_typed_literal_annotation() -> None:
    # The typed Literal is the source of truth -- ensure the runtime
    # value matches the literal range.
    from iriai_build_v2.execution_control import governance_snapshot_api

    module_typed = typing.get_type_hints(
        governance_snapshot_api, include_extras=True
    )
    # The module-level constant carries the typed Literal; resolve via
    # the gap class's failure_id field annotation (one source of truth
    # for the Literal range).
    gap_hints = typing.get_type_hints(SnapshotAPIGap)
    assert typing.get_origin(gap_hints["failure_id"]) is typing.Literal
    args = typing.get_args(gap_hints["failure_id"])
    assert args == ("governance_snapshot_api_failed",)


def test_all_exports() -> None:
    from iriai_build_v2.execution_control import governance_snapshot_api as m

    expected = {
        "SNAPSHOT_API_FAILURE_ID",
        "SnapshotAPIInputs",
        "SnapshotAPIResult",
        "SnapshotAPIGap",
        "SnapshotAPICorpus",
        "GovernanceSnapshotAPI",
    }
    assert set(m.__all__) == expected


def test_no_re_export_from_execution_control_init() -> None:
    """Per the implementer prompt: no re-exports from
    ``execution_control/__init__.py``.
    """

    from iriai_build_v2 import execution_control

    pkg_all = set(getattr(execution_control, "__all__", []))
    snapshot_api_names = {
        "SNAPSHOT_API_FAILURE_ID",
        "SnapshotAPIInputs",
        "SnapshotAPIResult",
        "SnapshotAPIGap",
        "SnapshotAPICorpus",
        "GovernanceSnapshotAPI",
    }
    assert snapshot_api_names.isdisjoint(pkg_all)


# --- Section 2: SnapshotAPIInputs ------------------------------------------


def test_inputs_construction_minimal() -> None:
    inputs = SnapshotAPIInputs(corpus_id="corpus-test-1")
    assert inputs.corpus_id == "corpus-test-1"
    assert inputs.snapshot_version == "v1"
    assert inputs.scorecard_id is None
    assert inputs.cursor is None
    # Defaults must match Slice 19 1st sub-slice budget constants.
    assert (
        inputs.max_response_bytes
        == GOVERNANCE_SNAPSHOT_DEFAULT_MAX_BYTES
    )
    assert inputs.max_findings == GOVERNANCE_SNAPSHOT_DEFAULT_MAX_FINDINGS
    assert (
        inputs.max_recommendations
        == GOVERNANCE_SNAPSHOT_DEFAULT_MAX_RECOMMENDATIONS
    )
    assert (
        inputs.max_replay_results
        == GOVERNANCE_SNAPSHOT_DEFAULT_MAX_REPLAY_RESULTS
    )
    assert inputs.completeness_override is None
    assert inputs.evidence_quality_override is None


def test_inputs_construction_all_overrides() -> None:
    inputs = SnapshotAPIInputs(
        corpus_id="corpus-test-2",
        snapshot_version="v2",
        scorecard_id="scorecard-99",
        cursor="cursor:page=2",
        max_response_bytes=1024,
        max_findings=3,
        max_recommendations=2,
        max_replay_results=1,
        max_page_refs=5,
        completeness_override="paged",
        evidence_quality_override="advisory",
    )
    assert inputs.snapshot_version == "v2"
    assert inputs.scorecard_id == "scorecard-99"
    assert inputs.cursor == "cursor:page=2"
    assert inputs.max_response_bytes == 1024
    assert inputs.max_findings == 3
    assert inputs.max_recommendations == 2
    assert inputs.max_replay_results == 1
    assert inputs.max_page_refs == 5
    assert inputs.completeness_override == "paged"
    assert inputs.evidence_quality_override == "advisory"


def test_inputs_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SnapshotAPIInputs(corpus_id="c", unknown_kwarg=True)  # type: ignore[call-arg]


def test_inputs_completeness_override_literal_range() -> None:
    """Literal validation on completeness_override accepts only
    Slice 13a CompletenessState values (4 values)."""

    for value in ("complete", "paged", "preview_only", "unavailable"):
        i = SnapshotAPIInputs(
            corpus_id="c", completeness_override=value  # type: ignore[arg-type]
        )
        assert i.completeness_override == value
    with pytest.raises(ValidationError):
        SnapshotAPIInputs(
            corpus_id="c", completeness_override="bogus"  # type: ignore[arg-type]
        )


def test_inputs_evidence_quality_override_literal_range() -> None:
    """Literal validation on evidence_quality_override accepts only
    Slice 13a EvidenceQuality values (6 values)."""

    for value in (
        "canonical",
        "derived",
        "sampled",
        "advisory",
        "stale",
        "insufficient",
    ):
        i = SnapshotAPIInputs(
            corpus_id="c",
            evidence_quality_override=value,  # type: ignore[arg-type]
        )
        assert i.evidence_quality_override == value
    with pytest.raises(ValidationError):
        SnapshotAPIInputs(
            corpus_id="c",
            evidence_quality_override="bogus",  # type: ignore[arg-type]
        )


# --- Section 3: SnapshotAPICorpus ------------------------------------------


def test_corpus_construction_minimal() -> None:
    c = SnapshotAPICorpus()
    assert c.findings == []
    assert c.recommendations == []
    assert c.replay_results == []
    assert c.page_refs == []
    assert c.corpus_evidence_quality == "canonical"
    assert c.omitted_findings_count == 0
    assert c.omitted_recommendations_count == 0
    assert c.omitted_replay_results_count == 0
    assert c.omitted_page_refs_count == 0
    assert c.next_cursor is None
    assert c.blocked_by == []


def test_corpus_construction_populated() -> None:
    c = SnapshotAPICorpus(
        findings=[_finding()],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        page_refs=[_page_ref()],
        corpus_evidence_quality="sampled",
        omitted_findings_count=5,
        omitted_recommendations_count=2,
        omitted_replay_results_count=1,
        omitted_page_refs_count=3,
        next_cursor="cursor:next",
        blocked_by=["stale_evidence:8ac124d6"],
    )
    assert len(c.findings) == 1
    assert len(c.recommendations) == 1
    assert len(c.replay_results) == 1
    assert len(c.page_refs) == 1
    assert c.corpus_evidence_quality == "sampled"
    assert c.omitted_findings_count == 5
    assert c.blocked_by == ["stale_evidence:8ac124d6"]


def test_corpus_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SnapshotAPICorpus(unknown=True)  # type: ignore[call-arg]


# --- Section 4: SnapshotAPIGap ---------------------------------------------


def test_gap_construction_minimal() -> None:
    g = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="corpus-1",
        reason="corpus_id_empty",
        observed_at=datetime(2026, 5, 25, tzinfo=timezone.utc),
    )
    assert g.failure_id == "governance_snapshot_api_failed"
    assert g.evidence_payload == {}


def test_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SnapshotAPIGap(
            failure_id="governance_snapshot_api_failed",
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
            unknown=True,  # type: ignore[call-arg]
        )


def test_gap_failure_id_literal_rejects_other_strings() -> None:
    with pytest.raises(ValidationError):
        SnapshotAPIGap(
            failure_id="other_failure",  # type: ignore[arg-type]
            corpus_id="c",
            reason="r",
            observed_at=datetime.now(timezone.utc),
        )


def test_gap_evidence_payload_round_trip() -> None:
    payload = {"exception_type": "ValueError", "exception_message": "bad"}
    g = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="c",
        reason="snapshot_construction_failed",
        observed_at=datetime.now(timezone.utc),
        evidence_payload=payload,
    )
    assert g.evidence_payload == payload


# --- Section 5: SnapshotAPIResult ------------------------------------------


def test_result_construction_minimal() -> None:
    r = SnapshotAPIResult()
    assert r.snapshot is None
    assert r.gap_findings == []


def test_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SnapshotAPIResult(unknown=True)  # type: ignore[call-arg]


# --- Section 6: build_snapshot happy path ---------------------------------


def test_build_snapshot_minimal_happy_path() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="corpus-h-1"),
        SnapshotAPICorpus(),
    )
    assert result.snapshot is not None
    assert result.gap_findings == []
    s = result.snapshot
    assert s.corpus_id == "corpus-h-1"
    assert s.snapshot_version == "v1"
    assert s.scorecard_id is None
    assert s.truncated is False
    assert s.omitted_counts == {
        "findings": 0,
        "recommendations": 0,
        "replay_results": 0,
        "page_refs": 0,
    }
    assert s.completeness == "complete"
    assert s.evidence_quality == "canonical"
    assert s.top_findings == []
    assert s.recommendations == []
    assert s.replay_results == []
    assert s.page_refs == []
    assert s.next_cursor is None
    assert s.blocked_by == []
    # snapshot_digest must be a 64-char hex SHA-256
    assert isinstance(s.snapshot_digest, str)
    assert len(s.snapshot_digest) == 64
    int(s.snapshot_digest, 16)  # parses as hex


def test_build_snapshot_populated_happy_path() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="corpus-h-2",
        snapshot_version="v2",
        scorecard_id="sc-1",
    )
    corpus = SnapshotAPICorpus(
        findings=[_finding(), _finding(idempotency_key="finding-key-19-2-b")],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        page_refs=[_page_ref(), _page_ref(page_ref_id="page-ref-19-2-2")],
        corpus_evidence_quality="derived",
        next_cursor="cursor:7",
        blocked_by=["x"],
    )
    result = api.build_snapshot(inputs, corpus)
    assert result.snapshot is not None
    s = result.snapshot
    assert len(s.top_findings) == 2
    assert len(s.recommendations) == 1
    assert len(s.replay_results) == 1
    assert s.page_refs == ["page-ref-19-2-1", "page-ref-19-2-2"]
    assert s.next_cursor == "cursor:7"
    assert s.blocked_by == ["x"]
    assert s.evidence_quality == "derived"
    assert s.scorecard_id == "sc-1"
    assert s.snapshot_version == "v2"
    # Generated at must be timezone-aware UTC.
    assert s.generated_at.tzinfo is not None


def test_build_snapshot_propagates_next_cursor() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c"),
        SnapshotAPICorpus(next_cursor="abc"),
    )
    assert result.snapshot is not None
    assert result.snapshot.next_cursor == "abc"


def test_build_snapshot_propagates_blocked_by() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c"),
        SnapshotAPICorpus(blocked_by=["a", "b"]),
    )
    assert result.snapshot is not None
    assert result.snapshot.blocked_by == ["a", "b"]


# --- Section 7: Bounded-reads / truncation --------------------------------


def test_build_snapshot_truncates_findings() -> None:
    api = _api()
    findings = [
        _finding(idempotency_key=f"k-{i:03d}") for i in range(5)
    ]
    inputs = SnapshotAPIInputs(corpus_id="c", max_findings=3)
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(findings=findings, page_refs=[_page_ref()]),
    )
    s = result.snapshot
    assert s is not None
    assert len(s.top_findings) == 3
    assert s.truncated is True
    assert s.omitted_counts["findings"] == 2
    assert s.completeness == "paged"


def test_build_snapshot_truncates_recommendations() -> None:
    api = _api()
    recs = [
        _recommendation(idempotency_key=f"r-{i:03d}", recommendation_id=f"rec-{i}")
        for i in range(4)
    ]
    inputs = SnapshotAPIInputs(corpus_id="c", max_recommendations=2)
    result = api.build_snapshot(
        inputs, SnapshotAPICorpus(recommendations=recs)
    )
    s = result.snapshot
    assert s is not None
    assert len(s.recommendations) == 2
    assert s.truncated is True
    assert s.omitted_counts["recommendations"] == 2


def test_build_snapshot_truncates_replay_results() -> None:
    api = _api()
    replays = [
        _replay_result(result_id=f"res-{i}") for i in range(6)
    ]
    inputs = SnapshotAPIInputs(corpus_id="c", max_replay_results=4)
    result = api.build_snapshot(
        inputs, SnapshotAPICorpus(replay_results=replays)
    )
    s = result.snapshot
    assert s is not None
    assert len(s.replay_results) == 4
    assert s.omitted_counts["replay_results"] == 2
    assert s.truncated is True


def test_build_snapshot_truncates_page_refs() -> None:
    api = _api()
    page_refs = [
        _page_ref(page_ref_id=f"p-{i:03d}") for i in range(5)
    ]
    inputs = SnapshotAPIInputs(corpus_id="c", max_page_refs=2)
    result = api.build_snapshot(
        inputs, SnapshotAPICorpus(page_refs=page_refs)
    )
    s = result.snapshot
    assert s is not None
    assert s.page_refs == ["p-000", "p-001"]
    assert s.omitted_counts["page_refs"] == 3
    assert s.truncated is True


def test_build_snapshot_truncates_all_4_dimensions() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c",
        max_findings=1,
        max_recommendations=1,
        max_replay_results=1,
        max_page_refs=1,
    )
    corpus = SnapshotAPICorpus(
        findings=[
            _finding(idempotency_key=f"k-{i}") for i in range(3)
        ],
        recommendations=[
            _recommendation(
                idempotency_key=f"r-{i}", recommendation_id=f"rid-{i}"
            )
            for i in range(3)
        ],
        replay_results=[
            _replay_result(result_id=f"res-{i}") for i in range(3)
        ],
        page_refs=[
            _page_ref(page_ref_id=f"p-{i}") for i in range(3)
        ],
    )
    result = api.build_snapshot(inputs, corpus)
    s = result.snapshot
    assert s is not None
    assert s.omitted_counts == {
        "findings": 2,
        "recommendations": 2,
        "replay_results": 2,
        "page_refs": 2,
    }
    assert s.truncated is True
    assert s.completeness == "paged"


def test_build_snapshot_no_truncation_marks_complete() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    corpus = SnapshotAPICorpus(findings=[_finding()])
    result = api.build_snapshot(inputs, corpus)
    s = result.snapshot
    assert s is not None
    assert s.truncated is False
    assert s.completeness == "complete"


def test_build_snapshot_upstream_omitted_counts_added() -> None:
    """Multi-level truncation: upstream omitted counts are ADDED to
    this API's per-call omitted counts so consumers see total
    truncation."""

    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c", max_findings=2)
    findings = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    corpus = SnapshotAPICorpus(
        findings=findings,
        omitted_findings_count=10,  # upstream pre-truncated 10 more
        omitted_recommendations_count=5,
        omitted_replay_results_count=0,
        omitted_page_refs_count=7,
    )
    result = api.build_snapshot(inputs, corpus)
    s = result.snapshot
    assert s is not None
    # 1 truncated this API + 10 upstream = 11.
    assert s.omitted_counts["findings"] == 11
    assert s.omitted_counts["recommendations"] == 5
    assert s.omitted_counts["replay_results"] == 0
    assert s.omitted_counts["page_refs"] == 7
    assert s.truncated is True


def test_build_snapshot_upstream_truncated_with_page_refs_is_paged() -> None:
    """Even with no per-call truncation, upstream-truncated rows
    must make truncated=True + completeness=paged when page refs cover
    the paged evidence."""

    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    corpus = SnapshotAPICorpus(
        omitted_findings_count=3,
        page_refs=[_page_ref()],
    )
    result = api.build_snapshot(inputs, corpus)
    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.completeness == "paged"
    assert s.omitted_counts["findings"] == 3


def test_build_snapshot_truncated_without_page_refs_is_display_only() -> None:
    """19A-P2-004: omitted rows without page refs cannot remain
    authoritative.

    A truncated snapshot that exposes no page refs is display-only, even if
    the caller attempts to raise completeness via an override.
    """

    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c",
        max_findings=0,
        completeness_override="complete",
    )
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(findings=[_finding()]),
    )
    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.page_refs == []
    assert s.completeness == "preview_only"
    assert (
        "governance_snapshot_truncated_without_page_refs" in s.blocked_by
    )


def test_build_snapshot_truncated_with_non_exact_page_refs_is_display_only() -> None:
    """19A-P2-013: non-exact refs cannot back paged snapshot authority."""

    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c",
        max_findings=0,
        completeness_override="complete",
    )
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            findings=[_finding()],
            page_refs=[
                _page_ref(
                    page_ref_id="preview-page",
                    completeness="preview_only",
                    exact=False,
                )
            ],
        ),
    )
    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.page_refs == ["preview-page"]
    assert s.completeness == "preview_only"
    assert (
        "governance_snapshot_truncated_without_page_refs" in s.blocked_by
    )


# --- Section 8: completeness override + evidence_quality override --------


def test_completeness_override_wins() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c", completeness_override="preview_only"
    )
    result = api.build_snapshot(inputs, SnapshotAPICorpus())
    s = result.snapshot
    assert s is not None
    assert s.completeness == "preview_only"


def test_completeness_override_cannot_raise_truncated_snapshot() -> None:
    """19A-P2-004: caller override cannot raise derived truncation state."""

    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c",
        max_findings=0,
        completeness_override="complete",
    )
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            findings=[_finding()],
            page_refs=[_page_ref()],
        ),
    )
    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.page_refs == ["page-ref-19-2-1"]
    assert s.completeness == "paged"


def test_completeness_override_can_lower_truncated_snapshot() -> None:
    """Overrides still may lower completeness for stale/display-only callers."""

    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c",
        max_findings=0,
        completeness_override="unavailable",
    )
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            findings=[_finding()],
            page_refs=[_page_ref()],
        ),
    )
    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.completeness == "unavailable"


def test_evidence_quality_override_wins() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(
        corpus_id="c", evidence_quality_override="stale"
    )
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(corpus_evidence_quality="canonical"),
    )
    s = result.snapshot
    assert s is not None
    assert s.evidence_quality == "stale"


def test_evidence_quality_default_from_corpus() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c"),
        SnapshotAPICorpus(corpus_evidence_quality="advisory"),
    )
    s = result.snapshot
    assert s is not None
    assert s.evidence_quality == "advisory"


def test_completeness_derived_paged_when_truncated() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c", max_findings=0)
    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            findings=[_finding()],
            page_refs=[_page_ref()],
        ),
    )
    s = result.snapshot
    assert s is not None
    assert s.completeness == "paged"


# --- Section 9: snapshot_digest reproducibility ---------------------------


def test_digest_reproducible_same_inputs() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="cd-1", snapshot_version="v1")
    corpus = SnapshotAPICorpus(
        findings=[_finding(idempotency_key="kk-1")],
        recommendations=[_recommendation(idempotency_key="rr-1")],
        replay_results=[_replay_result(result_id="rid-1")],
    )
    a = api.build_snapshot(inputs, corpus).snapshot
    b = api.build_snapshot(inputs, corpus).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest == b.snapshot_digest


def test_digest_changes_on_corpus_id() -> None:
    api = _api()
    base_corpus = SnapshotAPICorpus()
    a = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="x"), base_corpus
    ).snapshot
    b = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="y"), base_corpus
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_snapshot_version() -> None:
    api = _api()
    corpus = SnapshotAPICorpus()
    a = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", snapshot_version="v1"), corpus
    ).snapshot
    b = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", snapshot_version="v2"), corpus
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_scorecard_id() -> None:
    api = _api()
    corpus = SnapshotAPICorpus()
    a = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c"), corpus
    ).snapshot
    b = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", scorecard_id="sc"), corpus
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_findings() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    a = api.build_snapshot(
        inputs, SnapshotAPICorpus()
    ).snapshot
    b = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(findings=[_finding(idempotency_key="k-x")]),
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_recommendations() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    a = api.build_snapshot(
        inputs, SnapshotAPICorpus()
    ).snapshot
    b = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            recommendations=[_recommendation(idempotency_key="r-x")]
        ),
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_replay_results() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    a = api.build_snapshot(inputs, SnapshotAPICorpus()).snapshot
    b = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            replay_results=[_replay_result(result_id="rid-x")]
        ),
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_replay_version() -> None:
    """Doc-19:152-153: '...and recommendation/replay versions.'"""

    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    a = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            replay_results=[_replay_result(result_version="v1")]
        ),
    ).snapshot
    b = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            replay_results=[_replay_result(result_version="v2")]
        ),
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_omitted_counts() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    a = api.build_snapshot(inputs, SnapshotAPICorpus()).snapshot
    b = api.build_snapshot(
        inputs, SnapshotAPICorpus(omitted_findings_count=3)
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_evidence_quality() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c")
    a = api.build_snapshot(
        inputs, SnapshotAPICorpus(corpus_evidence_quality="canonical")
    ).snapshot
    b = api.build_snapshot(
        inputs, SnapshotAPICorpus(corpus_evidence_quality="stale")
    ).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_changes_on_completeness() -> None:
    api = _api()
    inputs_a = SnapshotAPIInputs(
        corpus_id="c", completeness_override="complete"
    )
    inputs_b = SnapshotAPIInputs(
        corpus_id="c", completeness_override="paged"
    )
    a = api.build_snapshot(inputs_a, SnapshotAPICorpus()).snapshot
    b = api.build_snapshot(inputs_b, SnapshotAPICorpus()).snapshot
    assert a is not None and b is not None
    assert a.snapshot_digest != b.snapshot_digest


def test_digest_matches_helper_directly() -> None:
    """The API's snapshot_digest must equal the Slice 19 1st sub-slice
    helper output for the same logical inputs."""

    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="cd-2", scorecard_id="sc-2")
    f1 = _finding(idempotency_key="fk-a")
    f2 = _finding(idempotency_key="fk-b")
    r1 = _recommendation(idempotency_key="rk-a")
    rp1 = _replay_result(result_id="rid-a", result_version="vA")
    corpus = SnapshotAPICorpus(
        findings=[f1, f2], recommendations=[r1], replay_results=[rp1]
    )
    result = api.build_snapshot(inputs, corpus)
    s = result.snapshot
    assert s is not None
    expected = compute_governance_snapshot_digest(
        corpus_id="cd-2",
        snapshot_version="v1",
        scorecard_id="sc-2",
        finding_idempotency_keys=["fk-a", "fk-b"],
        recommendation_idempotency_keys=["rk-a"],
        replay_result_ids=["rid-a"],
        replay_result_versions=["vA"],
        omitted_counts={
            "findings": 0,
            "recommendations": 0,
            "replay_results": 0,
            "page_refs": 0,
        },
        evidence_quality="canonical",
        completeness="complete",
    )
    assert s.snapshot_digest == expected


# --- Section 10: fail-closed (never raises) -------------------------------


def test_build_snapshot_never_raises_on_empty_corpus_id() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    assert result.snapshot is None
    assert len(result.gap_findings) == 1
    g = result.gap_findings[0]
    assert g.reason == "corpus_id_empty"
    assert g.failure_id == "governance_snapshot_api_failed"
    assert g.corpus_id == "<empty>"


def test_build_snapshot_never_raises_on_whitespace_corpus_id() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="   "), SnapshotAPICorpus()
    )
    assert result.snapshot is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "corpus_id_empty"


def test_build_snapshot_never_raises_on_negative_max_findings() -> None:
    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="c", max_findings=-1)
    result = api.build_snapshot(
        inputs, SnapshotAPICorpus(findings=[_finding()])
    )
    # negative cap treated as 0 (truncate everything; informational).
    assert result.snapshot is not None
    assert result.snapshot.top_findings == []
    assert result.snapshot.omitted_counts["findings"] == 1


def test_gap_observed_at_is_utc() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    g = result.gap_findings[0]
    assert g.observed_at.tzinfo is not None
    assert g.observed_at.tzinfo.utcoffset(g.observed_at).total_seconds() == 0


# --- Section 11: DIRECT annotation-identity REUSE -------------------------


def test_inputs_reuses_slice_13a_completeness_state() -> None:
    """Inputs.completeness_override field annotation REUSES Slice 13a
    typed Literal directly (no second copy)."""

    hints = typing.get_type_hints(SnapshotAPIInputs)
    # completeness_override: CompletenessState | None
    ann = hints["completeness_override"]
    union_args = typing.get_args(ann)
    # CompletenessState is a Literal; assert it's exactly the Slice 13a
    # Literal in the union (alongside None).
    completeness_lit = next(
        a for a in union_args if a is not type(None)
    )
    assert completeness_lit is CompletenessState


def test_inputs_reuses_slice_13a_evidence_quality() -> None:
    hints = typing.get_type_hints(SnapshotAPIInputs)
    ann = hints["evidence_quality_override"]
    union_args = typing.get_args(ann)
    quality_lit = next(a for a in union_args if a is not type(None))
    assert quality_lit is EvidenceQuality


def test_corpus_reuses_slice_16_governance_finding() -> None:
    hints = typing.get_type_hints(SnapshotAPICorpus)
    ann = hints["findings"]
    args = typing.get_args(ann)
    assert args[0] is GovernanceFinding


def test_corpus_reuses_slice_17_policy_recommendation() -> None:
    hints = typing.get_type_hints(SnapshotAPICorpus)
    ann = hints["recommendations"]
    args = typing.get_args(ann)
    assert args[0] is GovernancePolicyRecommendation


def test_corpus_reuses_slice_18_counterfactual_result() -> None:
    hints = typing.get_type_hints(SnapshotAPICorpus)
    ann = hints["replay_results"]
    args = typing.get_args(ann)
    assert args[0] is CounterfactualResult


def test_corpus_reuses_slice_13a_governance_evidence_page_ref() -> None:
    hints = typing.get_type_hints(SnapshotAPICorpus)
    ann = hints["page_refs"]
    args = typing.get_args(ann)
    assert args[0] is GovernanceEvidencePageRef


def test_result_reuses_slice_19_first_governance_snapshot() -> None:
    hints = typing.get_type_hints(SnapshotAPIResult)
    ann = hints["snapshot"]
    union_args = typing.get_args(ann)
    snapshot_t = next(a for a in union_args if a is not type(None))
    assert snapshot_t is GovernanceSnapshot


def test_corpus_evidence_quality_field_reuses_slice_13a_literal() -> None:
    hints = typing.get_type_hints(SnapshotAPICorpus)
    ann = hints["corpus_evidence_quality"]
    assert ann is EvidenceQuality


# --- Section 12: failure_router 4-pure-data-add discipline ---------------


def test_failure_router_failure_id_registered_in_literal() -> None:
    """`governance_snapshot_api_failed` is in the FailureType Literal."""

    assert "governance_snapshot_api_failed" in typing.get_args(FailureType)


def test_failure_router_failure_id_in_failure_types_tuple() -> None:
    assert "governance_snapshot_api_failed" in FAILURE_TYPES


def test_failure_router_route_table_uses_evidence_corruption_class() -> None:
    key = ("evidence_corruption", "governance_snapshot_api_failed")
    assert key in ROUTE_TABLE


def test_failure_router_route_table_uses_retry_governance_projection() -> None:
    key = ("evidence_corruption", "governance_snapshot_api_failed")
    route = ROUTE_TABLE[key]
    assert route.action == "retry_governance_projection"


def test_failure_router_route_action_in_typed_literal() -> None:
    """retry_governance_projection (REUSED from Slice 14 2nd sub-slice)."""

    assert "retry_governance_projection" in typing.get_args(RouteAction)


# --- Section 13: doc-19 acceptance criteria enforcement ------------------


def test_ac1_snapshot_carries_digest_for_reproducibility() -> None:
    """Doc-19:224 AC1 -- 'Reports are bounded, reproducible,
    evidence-cited, and structured first.'

    Reproducibility is enforced by snapshot_digest being deterministic
    (tested above); evidence-cited is enforced by typed Slice 16/17/18
    REUSE (tested above); bounded is enforced by max_response_bytes +
    truncated + omitted_counts (tested above); structured first is
    enforced by the typed Pydantic BaseModel surface (tested above).
    """

    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="ac1"), SnapshotAPICorpus()
    )
    s = result.snapshot
    assert s is not None
    # AC1 surface: bounded fields present.
    assert hasattr(s, "max_response_bytes")
    assert hasattr(s, "truncated")
    assert hasattr(s, "omitted_counts")
    assert hasattr(s, "snapshot_digest")
    # AC1 surface: structured first.
    assert isinstance(s, BaseModel)


def test_ac2_truncated_snapshot_carries_completeness_state() -> None:
    """Doc-19:225-226 AC2 -- 'Truncated or preview reports are never
    authoritative unless exact page refs and completeness metadata
    cover the consumer's required scope.'

    Enforced by the typed truncated + page_refs + completeness triple.
    """

    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="ac2", max_findings=1)
    corpus = SnapshotAPICorpus(
        findings=[
            _finding(idempotency_key=f"k-{i}") for i in range(3)
        ],
        page_refs=[_page_ref()],
    )
    result = api.build_snapshot(inputs, corpus)
    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    # AC2: completeness state must signal paged when truncated.
    assert s.completeness == "paged"
    # AC2: page refs present so the consumer can drill in.
    assert s.page_refs


def test_ac6_evidence_quality_always_present() -> None:
    """Doc-19:232-233 AC6 -- 'Human-facing dashboard/Slack output
    explains top findings without hiding evidence quality or omitted
    details.'

    Enforced by typed evidence_quality + omitted_counts always present
    on the typed GovernanceSnapshot.
    """

    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="ac6"), SnapshotAPICorpus()
    )
    s = result.snapshot
    assert s is not None
    assert s.evidence_quality in typing.get_args(EvidenceQuality)
    assert isinstance(s.omitted_counts, dict)
    # The 4 axes are always populated.
    assert set(s.omitted_counts.keys()) == {
        "findings",
        "recommendations",
        "replay_results",
        "page_refs",
    }


def test_ac7_no_control_plane_writer_methods_extension() -> None:
    """Doc-19:234 + doc-19:348-349 AC -- 'Supervisor/dashboard
    read-only contract preserved (no governance writer extends the
    Slice 10c-1 CONTROL_PLANE_WRITER_METHODS set).'

    Enforced by the typed API class being a pure projection (no
    mutation methods; no executor-state write methods; no
    CONTROL_PLANE_WRITER_METHODS extension).
    """

    public_methods = [
        name
        for name in dir(GovernanceSnapshotAPI)
        if not name.startswith("_")
    ]
    assert public_methods == ["build_snapshot"]


def test_ac7_refs_only_no_body_hydration() -> None:
    """Doc-19:234 AC7 -- bounded-reads + refs-only contract.

    The API surfaces only typed page_ref_id strings (not full page-ref
    bodies) on GovernanceSnapshot.page_refs per doc-19:81.
    """

    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="ac7"),
        SnapshotAPICorpus(page_refs=[_page_ref()]),
    )
    s = result.snapshot
    assert s is not None
    assert s.page_refs == ["page-ref-19-2-1"]
    # str (not BaseModel) is the refs-only surface contract.
    assert all(isinstance(p, str) for p in s.page_refs)


# --- Section 14: edge-case row coverage (doc-19:184-194) -----------------


def test_gap_supports_governance_snapshot_stale_reason() -> None:
    """Doc-19:186-187 edge-case -- 'Governance snapshot stale: report
    stale status and do not present new recommendations as current.'

    The typed SnapshotAPIGap supports the free-form reason string so
    the caller can emit the edge-case reason.
    """

    g = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="c",
        reason="governance_snapshot_stale",
        observed_at=datetime.now(timezone.utc),
    )
    assert g.reason == "governance_snapshot_stale"


def test_gap_supports_active_workflow_pressure_reason() -> None:
    """Doc-19:193-194 edge-case -- 'Active workflow pressure: reporting
    returns cached snapshots instead of forcing expensive
    recomputation.'
    """

    g = SnapshotAPIGap(
        failure_id="governance_snapshot_api_failed",
        corpus_id="c",
        reason="active_workflow_pressure",
        observed_at=datetime.now(timezone.utc),
    )
    assert g.reason == "active_workflow_pressure"


def test_blocked_by_propagates_for_stale_evidence() -> None:
    """Doc-19:186-187 edge-case interplay with the typed blocked_by
    field per doc-19:87.
    """

    api = _api()
    corpus = SnapshotAPICorpus(blocked_by=["stale_evidence:8ac124d6"])
    result = api.build_snapshot(
        SnapshotAPIInputs(
            corpus_id="c", evidence_quality_override="stale"
        ),
        corpus,
    )
    s = result.snapshot
    assert s is not None
    assert "stale_evidence:8ac124d6" in s.blocked_by
    assert s.evidence_quality == "stale"


# --- Section 15: activation-authority boundary ---------------------------


def test_module_has_no_mutation_methods() -> None:
    """Per the Slice 17 7th sub-slice activation-boundary discipline +
    doc-19:348-349 AC the governance snapshot API has NO mutation
    methods on any of its typed shapes; the typed surface is pure
    descriptors.
    """

    forbidden_method_prefixes = (
        "activate_",
        "approve_",
        "merge_",
        "checkpoint_",
        "mutate_",
        "write_",
        "persist_",
    )
    # Allow Pydantic v2 built-ins (update_forward_refs / model_*).
    pydantic_builtins = frozenset({
        "update_forward_refs",
    })
    for cls in (
        SnapshotAPIInputs,
        SnapshotAPICorpus,
        SnapshotAPIResult,
        SnapshotAPIGap,
    ):
        for name in dir(cls):
            if name.startswith("_") or name in pydantic_builtins:
                continue
            if name.startswith("model_"):
                # Pydantic v2 model_* helpers (model_dump, model_copy,
                # model_validate, etc.) are not state mutations on the
                # typed shape.
                continue
            assert not name.startswith(forbidden_method_prefixes), (
                f"{cls.__name__}.{name} resembles a mutation method "
                "(per AC7 + AC: doc-19:348-349)"
            )


def test_no_dag_artifact_key_literals() -> None:
    """Per doc-19:348-349 the typed snapshot API does NOT mint
    dag-execution-authority artifact-key string literals.
    """

    import inspect

    from iriai_build_v2.execution_control import (
        governance_snapshot_api as mod,
    )

    src = inspect.getsource(mod)
    # The doc-19 module-level docstring may mention `dag-group:*` as
    # a documentation reference (NOT a string literal artifact key).
    # The structural check ensures no actual Python string literal
    # for a `dag-*` artifact key exists.
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
            f"governance_snapshot_api.py (per doc-19:348-349 AC)."
        )


# --- Section 16: helper-level (truncation) tests --------------------------


def test_truncate_helpers_handle_negative_cap() -> None:
    api = _api()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    truncated, omitted = api._truncate_findings(rows, -5)
    assert truncated == []
    assert omitted == 3


def test_truncate_helpers_handle_zero_cap() -> None:
    api = _api()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    truncated, omitted = api._truncate_findings(rows, 0)
    assert truncated == []
    assert omitted == 3


def test_truncate_helpers_handle_exact_cap() -> None:
    api = _api()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    truncated, omitted = api._truncate_findings(rows, 3)
    assert len(truncated) == 3
    assert omitted == 0


def test_truncate_helpers_handle_overcap() -> None:
    api = _api()
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(5)]
    truncated, omitted = api._truncate_findings(rows, 2)
    assert len(truncated) == 2
    assert omitted == 3


def test_truncate_page_refs_extracts_string_ids() -> None:
    api = _api()
    page_refs = [_page_ref(page_ref_id=f"pp-{i}") for i in range(4)]
    ids, omitted = api._truncate_page_refs(page_refs, 2)
    assert ids == ["pp-0", "pp-1"]
    assert omitted == 2


# --- Section 17: GovernanceSnapshotAPI instance is stateless ---------------


def test_api_instance_can_be_reused() -> None:
    api = _api()
    r1 = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="a"), SnapshotAPICorpus()
    )
    r2 = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="b"), SnapshotAPICorpus()
    )
    assert r1.snapshot is not None
    assert r2.snapshot is not None
    assert r1.snapshot.corpus_id == "a"
    assert r2.snapshot.corpus_id == "b"
    # Digests differ because corpus_id differs.
    assert r1.snapshot.snapshot_digest != r2.snapshot.snapshot_digest


def test_api_has_no_instance_state() -> None:
    api = _api()
    assert vars(api) == {}


# --- Section 18: typed snapshot fields populated end-to-end --------------


def test_snapshot_fields_complete_after_build() -> None:
    """Every doc-19:71-87 field on GovernanceSnapshot is populated by
    the API."""

    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(
            corpus_id="c",
            snapshot_version="v3",
            scorecard_id="sc-3",
            max_response_bytes=2048,
        ),
        SnapshotAPICorpus(
            findings=[_finding()],
            recommendations=[_recommendation()],
            replay_results=[_replay_result()],
            page_refs=[_page_ref()],
            corpus_evidence_quality="sampled",
            next_cursor="nc",
            blocked_by=["bb"],
        ),
    )
    s = result.snapshot
    assert s is not None
    for field_name in (
        "corpus_id",
        "snapshot_version",
        "snapshot_digest",
        "generated_at",
        "scorecard_id",
        "max_response_bytes",
        "truncated",
        "omitted_counts",
        "completeness",
        "page_refs",
        "next_cursor",
        "top_findings",
        "recommendations",
        "replay_results",
        "evidence_quality",
        "blocked_by",
    ):
        # All fields are present (Pydantic surface contract).
        assert hasattr(s, field_name), f"missing field: {field_name}"


def test_snapshot_max_response_bytes_passed_through() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(
            corpus_id="c", max_response_bytes=99_999
        ),
        SnapshotAPICorpus(),
    )
    s = result.snapshot
    assert s is not None
    assert s.max_response_bytes == 99_999


def test_snapshot_serialized_payload_honors_max_response_bytes() -> None:
    api = _api()
    findings = [
        _finding(
            idempotency_key=f"k-byte-{i}",
            recommended_action_display="x" * 5000,
        )
        for i in range(8)
    ]
    inputs = SnapshotAPIInputs(
        corpus_id="byte-budget",
        max_response_bytes=6000,
        max_findings=8,
    )

    result = api.build_snapshot(inputs, SnapshotAPICorpus(findings=findings))

    s = result.snapshot
    assert s is not None
    assert len(s.model_dump_json().encode("utf-8")) <= s.max_response_bytes
    assert s.truncated is True
    assert s.omitted_counts["findings"] > 0


def test_snapshot_irreducible_payload_over_budget_fails_closed() -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="byte-budget", max_response_bytes=1),
        SnapshotAPICorpus(),
    )

    assert result.snapshot is None
    assert len(result.gap_findings) == 1
    gap = result.gap_findings[0]
    assert gap.failure_id == SNAPSHOT_API_FAILURE_ID
    assert gap.reason == "serialized_response_budget_exceeded"
    assert gap.evidence_payload is not None
    assert gap.evidence_payload["serialized_bytes"] > gap.evidence_payload[
        "max_response_bytes"
    ]


@pytest.mark.parametrize("max_response_bytes", [0, -1])
def test_snapshot_non_positive_max_response_bytes_fails_closed(
    max_response_bytes: int,
) -> None:
    api = _api()
    result = api.build_snapshot(
        SnapshotAPIInputs(
            corpus_id="byte-budget",
            max_response_bytes=max_response_bytes,
        ),
        SnapshotAPICorpus(),
    )

    assert result.snapshot is None
    assert len(result.gap_findings) == 1
    gap = result.gap_findings[0]
    assert gap.failure_id == SNAPSHOT_API_FAILURE_ID
    assert gap.reason == "max_response_bytes_non_positive"
    assert gap.evidence_payload == {"max_response_bytes": max_response_bytes}


def test_snapshot_serialized_trimming_preserves_lowered_completeness() -> None:
    api = _api()
    findings = [
        _finding(
            idempotency_key=f"k-lowered-{i}",
            recommended_action_display="x" * 5000,
        )
        for i in range(8)
    ]
    inputs = SnapshotAPIInputs(
        corpus_id="byte-budget",
        max_response_bytes=6000,
        max_findings=8,
        completeness_override="unavailable",
    )

    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(findings=findings, page_refs=[_page_ref()]),
    )

    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.completeness == "unavailable"


def test_snapshot_serialized_trimming_preserves_non_exact_preview_only() -> None:
    api = _api()
    findings = [
        _finding(
            idempotency_key=f"k-preview-{i}",
            recommended_action_display="x" * 5000,
        )
        for i in range(8)
    ]
    inputs = SnapshotAPIInputs(
        corpus_id="byte-budget",
        max_response_bytes=6000,
        max_findings=8,
        completeness_override="complete",
    )

    result = api.build_snapshot(
        inputs,
        SnapshotAPICorpus(
            findings=findings,
            page_refs=[
                _page_ref(
                    page_ref_id="preview-page",
                    completeness="preview_only",
                    exact=False,
                )
            ],
        ),
    )

    s = result.snapshot
    assert s is not None
    assert s.truncated is True
    assert s.page_refs == ["preview-page"]
    assert s.completeness == "preview_only"
    assert (
        "governance_snapshot_truncated_without_page_refs" in s.blocked_by
    )


# --- Section 19: ordering preserved ---------------------------------------


def test_findings_ordering_preserved_under_truncation() -> None:
    api = _api()
    rows = [_finding(idempotency_key=f"k-{i:02d}") for i in range(5)]
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", max_findings=3),
        SnapshotAPICorpus(findings=rows),
    )
    s = result.snapshot
    assert s is not None
    assert [f.idempotency_key for f in s.top_findings] == [
        "k-00",
        "k-01",
        "k-02",
    ]


def test_page_refs_ordering_preserved_under_truncation() -> None:
    api = _api()
    rows = [_page_ref(page_ref_id=f"p-{i:02d}") for i in range(5)]
    result = api.build_snapshot(
        SnapshotAPIInputs(corpus_id="c", max_page_refs=3),
        SnapshotAPICorpus(page_refs=rows),
    )
    s = result.snapshot
    assert s is not None
    assert s.page_refs == ["p-00", "p-01", "p-02"]


# --- Section 20: snapshot reproducibility for same corpus id -------------


def test_doc_19_218_reproducible_same_corpus_id() -> None:
    """Doc-19:218 Tests -- 'Report generation is reproducible for the
    same corpus id.'

    Two API calls with the same corpus_id + same corpus + same inputs
    produce identical digests.
    """

    api = _api()
    inputs = SnapshotAPIInputs(corpus_id="reproducible-1")
    corpus = SnapshotAPICorpus(
        findings=[
            _finding(idempotency_key=f"k-{i}") for i in range(2)
        ],
        recommendations=[
            _recommendation(idempotency_key=f"r-{i}") for i in range(2)
        ],
        replay_results=[
            _replay_result(result_id=f"rid-{i}") for i in range(2)
        ],
    )
    digests = [
        api.build_snapshot(inputs, corpus).snapshot.snapshot_digest
        for _ in range(5)
    ]
    assert len(set(digests)) == 1
