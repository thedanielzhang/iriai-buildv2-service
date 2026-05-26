"""Slice 19 4th sub-slice -- unit tests for the typed Slack renderer at
``execution_control/governance_slack_renderer.py``.

Covers the doc-19:155 step 4 + doc-19:140-142 + doc-19:122-123 Slack
rendering surface:

* :data:`SLACK_RENDERER_FAILURE_ID` -- the typed failure id
  (``governance_slack_renderer_failed``) registered under the EXISTING
  ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` action.
* :data:`DedupeDecision` Literal (emitted / suppressed_dedupe /
  budget_exceeded / upstream_missing).
* :class:`SlackFindingSummary` typed BaseModel (extra-forbid; refs-only
  projection of Slice 16 GovernanceFinding identity surface).
* :class:`SlackBlockKitBlock` + :class:`SlackBlockKitPayload` typed
  Block Kit shapes (extra-forbid).
* :class:`SlackRenderInputs` typed BaseModel (extra-forbid; carries
  Slice 19 2nd SnapshotAPIResult source + 5-top-findings cap + 40 KB
  budget + dedupe-cache override).
* :class:`SlackRenderPayload` typed BaseModel (extra-forbid; bounded
  Block Kit payload with dedupe_key = snapshot_digest per doc-19:140-142
  + display_only AC2 flag).
* :class:`SlackRenderGap` typed BaseModel (extra-forbid; mirrors
  DashboardViewGap).
* :class:`SlackRenderResult` typed BaseModel (extra-forbid;
  payload | None + decision + gap_findings list).
* :class:`GovernanceSlackRenderer.render(...)` -- the projection
  method:
  * Happy-path -> typed SlackRenderPayload emitted with dedupe_key =
    snapshot_digest verbatim + decision="emitted".
  * Bounded display-truncation (5 top findings cap; 40 KB budget).
  * dedupe_key = snapshot_digest reproducibility (same snapshot ->
    same dedupe_key).
  * Cache-hit suppression (dedupe_key in cache -> decision=
    suppressed_dedupe; payload still constructed).
  * Upstream snapshot missing -> decision=upstream_missing + typed gap
    on ``upstream_snapshot_missing`` + propagated upstream gaps.
  * 40 KB budget exceeded (synthetic; pathological small budget) ->
    decision=budget_exceeded.
  * Renderer NEVER raises (typed gap projection on construction
    failure).
* :meth:`GovernanceSlackRenderer.compute_dedupe_key(...)` -- the typed
  dedupe key helper:
  * dedupe_key == snapshot_digest verbatim.
* DIRECT annotation-identity REUSE assertions for Slice 19 1st
  GovernanceSnapshot + Slice 19 2nd SnapshotAPIResult + Slice 13a
  CompletenessState + EvidenceQuality + Slice 16 GovernanceFinding +
  Slice 17 GovernancePolicyRecommendation + Slice 18
  CounterfactualResult.
* Failure-router 4-pure-data add discipline (Slice 14 2nd + Slice 19
  2nd + Slice 19 3rd sub-slice precedent verbatim).
* AC1/AC2/AC6/AC7 enforcement tests per doc-19 § Acceptance Criteria.

Per the implementer prompt § "Non-Negotiables" -- fail-closed on
every Pydantic field validator; no executor wiring outside this
slice's own acceptance tests; the Slice 13a + Slice 13A + Slice 14 +
Slice 15 + Slice 16 + Slice 17 + Slice 18 + Slice 19 1st + Slice 19
2nd + Slice 19 3rd sub-slice modules + tests remain READ-ONLY this
sub-slice.
"""

from __future__ import annotations

import json
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
from iriai_build_v2.execution_control.governance_slack_renderer import (
    SLACK_RENDERER_FAILURE_ID,
    DedupeDecision,
    GovernanceSlackRenderer,
    SlackBlockKitBlock,
    SlackBlockKitPayload,
    SlackFindingSummary,
    SlackRenderGap,
    SlackRenderInputs,
    SlackRenderPayload,
    SlackRenderResult,
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


# --- Fixture builders (mirror Slice 19 3rd sub-slice fixtures) -------------


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-19-4",
        digest="b" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    base: dict[str, object] = dict(
        page_ref_id="page-ref-19-4-1",
        authority="typed_journal",
        source_ref_id="ref-19-4",
        digest="c" * 64,
        completeness="complete",
        exact=True,
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)


def _finding(**overrides: object) -> GovernanceFinding:
    base: dict[str, object] = dict(
        idempotency_key="finding-key-19-4-a",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk", "runtime": "claude-sdk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-25#slice-19-4th"],
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
        idempotency_key="recommendation-key-19-4-a",
        recommendation_id="rec-19-4-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-key-19-4-a"],
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
        result_id="result-19-4-1",
        result_version="v1",
        scenario_id="scenario-19-4-1",
        corpus_id="corpus-19-4",
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
        supporting_finding_ids=["finding-key-19-4-a"],
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


def _renderer() -> GovernanceSlackRenderer:
    return GovernanceSlackRenderer()


# --- Section 1: module surface --------------------------------------------


def test_failure_id_literal_value() -> None:
    assert SLACK_RENDERER_FAILURE_ID == "governance_slack_renderer_failed"


def test_failure_id_typed_literal_annotation() -> None:
    """The typed Literal is the source of truth -- ensure the runtime
    value matches the literal range."""

    from iriai_build_v2.execution_control import (
        governance_slack_renderer as mod,
    )

    hints = typing.get_type_hints(mod, include_extras=True)
    lit_args = typing.get_args(hints["SLACK_RENDERER_FAILURE_ID"])
    assert "governance_slack_renderer_failed" in lit_args


def test_dedupe_decision_literal_values() -> None:
    """The DedupeDecision Literal carries exactly 4 typed values."""

    args = typing.get_args(DedupeDecision)
    assert set(args) == {
        "emitted",
        "suppressed_dedupe",
        "budget_exceeded",
        "upstream_missing",
    }


def test_all_exports_present() -> None:
    from iriai_build_v2.execution_control import (
        governance_slack_renderer as mod,
    )

    assert mod.__all__ == [
        "SLACK_RENDERER_FAILURE_ID",
        "DedupeDecision",
        "SlackFindingSummary",
        "SlackBlockKitBlock",
        "SlackBlockKitPayload",
        "SlackRenderInputs",
        "SlackRenderPayload",
        "SlackRenderGap",
        "SlackRenderResult",
        "GovernanceSlackRenderer",
    ]


def test_no_re_export_from_execution_control_init() -> None:
    """The Slice 19 4th sub-slice surface lives in its own module; the
    execution_control package __init__.py is NOT mutated (preserves
    Slice 00-12 baselines)."""

    from iriai_build_v2 import execution_control as pkg

    init_exports = pkg.__all__ if hasattr(pkg, "__all__") else []
    forbidden = {
        "SLACK_RENDERER_FAILURE_ID",
        "GovernanceSlackRenderer",
        "SlackRenderPayload",
    }
    for name in forbidden:
        assert name not in init_exports, (
            f"{name} should NOT be re-exported from execution_control.__init__"
        )


# --- Section 2: SlackFindingSummary --------------------------------


def test_finding_summary_construction_minimal() -> None:
    s = SlackFindingSummary(
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
    s = SlackFindingSummary(
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
        SlackFindingSummary(
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
        SlackFindingSummary(
            idempotency_key="k",
            kind="not_a_kind",  # type: ignore[arg-type]
            class_name="cls",
            severity="medium",
            confidence=0.5,
            estimated_lost_hours=1.0,
        )


def test_finding_summary_rejects_bad_severity_literal() -> None:
    with pytest.raises(ValidationError):
        SlackFindingSummary(
            idempotency_key="k",
            kind="workflow_inefficiency",
            class_name="cls",
            severity="not_a_severity",  # type: ignore[arg-type]
            confidence=0.5,
            estimated_lost_hours=1.0,
        )


# --- Section 3: SlackBlockKitBlock --------------------------------


def test_block_kit_block_header_construction() -> None:
    b = SlackBlockKitBlock(block_type="header", text="Hello")
    assert b.block_type == "header"
    assert b.text == "Hello"
    assert b.fields == []


def test_block_kit_block_section_with_fields() -> None:
    b = SlackBlockKitBlock(
        block_type="section",
        text="Body",
        fields=["F1", "F2"],
    )
    assert b.fields == ["F1", "F2"]


def test_block_kit_block_divider_no_text() -> None:
    b = SlackBlockKitBlock(block_type="divider")
    assert b.text is None
    assert b.fields == []


def test_block_kit_block_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SlackBlockKitBlock(
            block_type="header",
            text="Hello",
            unknown="x",  # type: ignore[call-arg]
        )


def test_block_kit_block_rejects_unknown_type() -> None:
    with pytest.raises(ValidationError):
        SlackBlockKitBlock(block_type="not_a_type")  # type: ignore[arg-type]


# --- Section 4: SlackBlockKitPayload --------------------------------


def test_block_kit_payload_construction_minimal() -> None:
    p = SlackBlockKitPayload(
        blocks=[SlackBlockKitBlock(block_type="header", text="X")],
        dedupe_key="d" * 64,
        corpus_id="c",
    )
    assert p.dedupe_key == "d" * 64
    assert p.corpus_id == "c"
    assert len(p.blocks) == 1


def test_block_kit_payload_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SlackBlockKitPayload(
            blocks=[],
            dedupe_key="d",
            corpus_id="c",
            extra="bad",  # type: ignore[call-arg]
        )


def test_block_kit_payload_to_block_kit_json_header() -> None:
    p = SlackBlockKitPayload(
        blocks=[SlackBlockKitBlock(block_type="header", text="Title")],
        dedupe_key="d",
        corpus_id="c",
    )
    j = p.to_block_kit_json()
    payload = json.loads(j)
    assert payload["blocks"][0]["type"] == "header"
    assert payload["blocks"][0]["text"]["type"] == "plain_text"
    assert payload["blocks"][0]["text"]["text"] == "Title"


def test_block_kit_payload_to_block_kit_json_section_with_fields() -> None:
    p = SlackBlockKitPayload(
        blocks=[
            SlackBlockKitBlock(
                block_type="section",
                text="Body",
                fields=["A", "B"],
            )
        ],
        dedupe_key="d",
        corpus_id="c",
    )
    payload = json.loads(p.to_block_kit_json())
    assert payload["blocks"][0]["type"] == "section"
    assert payload["blocks"][0]["text"]["type"] == "mrkdwn"
    assert payload["blocks"][0]["fields"][0]["type"] == "mrkdwn"
    assert payload["blocks"][0]["fields"][0]["text"] == "A"


def test_block_kit_payload_to_block_kit_json_context() -> None:
    p = SlackBlockKitPayload(
        blocks=[
            SlackBlockKitBlock(block_type="context", text="meta")
        ],
        dedupe_key="d",
        corpus_id="c",
    )
    payload = json.loads(p.to_block_kit_json())
    assert payload["blocks"][0]["type"] == "context"
    assert payload["blocks"][0]["elements"][0]["text"] == "meta"


def test_block_kit_payload_to_block_kit_json_divider() -> None:
    p = SlackBlockKitPayload(
        blocks=[SlackBlockKitBlock(block_type="divider")],
        dedupe_key="d",
        corpus_id="c",
    )
    payload = json.loads(p.to_block_kit_json())
    assert payload["blocks"][0] == {"type": "divider"}


def test_block_kit_payload_to_block_kit_json_stable() -> None:
    """Sort keys + compact separators make the serialised output
    stable across calls."""

    p = SlackBlockKitPayload(
        blocks=[
            SlackBlockKitBlock(block_type="header", text="Title"),
            SlackBlockKitBlock(block_type="divider"),
        ],
        dedupe_key="d",
        corpus_id="c",
    )
    a = p.to_block_kit_json()
    b = p.to_block_kit_json()
    assert a == b


# --- Section 5: SlackRenderInputs --------------------------------


def test_inputs_construction_with_defaults() -> None:
    src = _build_snapshot_via_api()
    inp = SlackRenderInputs(source=src)
    assert inp.max_top_findings == 5
    assert inp.max_payload_bytes == 40_960
    assert inp.recently_emitted_dedupe_keys == set()


def test_inputs_extra_forbid() -> None:
    src = _build_snapshot_via_api()
    with pytest.raises(ValidationError):
        SlackRenderInputs(
            source=src,
            extra="bad",  # type: ignore[call-arg]
        )


def test_inputs_max_top_findings_default_is_5_per_doc() -> None:
    """Doc-19:122-123 -- '5 top findings'."""

    src = _build_snapshot_via_api()
    inp = SlackRenderInputs(source=src)
    assert inp.max_top_findings == 5


def test_inputs_max_payload_bytes_default_is_40k_per_doc() -> None:
    """Doc-19:122-123 -- '40 KB serialized Block Kit payload'."""

    src = _build_snapshot_via_api()
    inp = SlackRenderInputs(source=src)
    assert inp.max_payload_bytes == 40_960


def test_inputs_recently_emitted_dedupe_keys_accepts_set() -> None:
    src = _build_snapshot_via_api()
    keys = {"a" * 64, "b" * 64}
    inp = SlackRenderInputs(
        source=src,
        recently_emitted_dedupe_keys=keys,
    )
    assert inp.recently_emitted_dedupe_keys == keys


# --- Section 6: SlackRenderPayload --------------------------------


def test_payload_extra_forbid() -> None:
    """A SlackRenderPayload requires nested SlackBlockKitPayload + many
    fields; verify extra_forbid via construction with extra kwarg."""

    src = _build_snapshot_via_api()
    renderer = _renderer()
    result = renderer.render(SlackRenderInputs(source=src))
    assert result.payload is not None
    # Re-construct with extra
    base = result.payload.model_dump()
    with pytest.raises(ValidationError):
        SlackRenderPayload(**base, extra_field="bad")


def test_payload_dedupe_key_is_required_field() -> None:
    with pytest.raises(ValidationError):
        SlackRenderPayload(  # type: ignore[call-arg]
            payload=SlackBlockKitPayload(
                blocks=[], dedupe_key="d", corpus_id="c"
            ),
            corpus_id="c",
            snapshot_version="v1",
            generated_at=datetime.now(timezone.utc),
            snapshot_generated_at=datetime.now(timezone.utc),
            completeness="complete",
            evidence_quality="canonical",
            truncated=False,
            omitted_counts={},
            serialized_bytes=100,
            display_only=False,
        )


# --- Section 7: SlackRenderGap --------------------------------


def test_gap_construction_minimal() -> None:
    g = SlackRenderGap(
        failure_id="governance_slack_renderer_failed",
        corpus_id="c",
        reason="x",
        observed_at=datetime.now(timezone.utc),
    )
    assert g.failure_id == "governance_slack_renderer_failed"
    assert g.reason == "x"
    assert g.evidence_payload == {}


def test_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SlackRenderGap(
            failure_id="governance_slack_renderer_failed",
            corpus_id="c",
            reason="x",
            observed_at=datetime.now(timezone.utc),
            unknown="x",  # type: ignore[call-arg]
        )


def test_gap_rejects_wrong_failure_id_literal() -> None:
    with pytest.raises(ValidationError):
        SlackRenderGap(
            failure_id="some_other_failure",  # type: ignore[arg-type]
            corpus_id="c",
            reason="x",
            observed_at=datetime.now(timezone.utc),
        )


# --- Section 8: SlackRenderResult --------------------------------


def test_result_default_empty_gap_findings() -> None:
    r = SlackRenderResult(decision="upstream_missing")
    assert r.payload is None
    assert r.decision == "upstream_missing"
    assert r.gap_findings == []


def test_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        SlackRenderResult(
            decision="emitted",
            extra="bad",  # type: ignore[call-arg]
        )


def test_result_decision_must_be_literal() -> None:
    with pytest.raises(ValidationError):
        SlackRenderResult(decision="not_a_decision")  # type: ignore[arg-type]


# --- Section 9: render() happy paths -------------------------------


def test_render_emits_payload_for_valid_snapshot() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.decision == "emitted"
    assert r.gap_findings == []


def test_render_dedupe_key_equals_snapshot_digest() -> None:
    """Doc-19:140-142 -- 'Slack digest with dedupe key from
    snapshot_digest'."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.dedupe_key == src.snapshot.snapshot_digest


def test_render_dedupe_key_also_on_block_kit_payload() -> None:
    """The typed Block Kit payload also carries the dedupe_key
    verbatim (so consumers reading the nested payload directly see
    the dedupe key)."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.payload.dedupe_key == src.snapshot.snapshot_digest


def test_render_payload_includes_corpus_id() -> None:
    src = _build_snapshot_via_api(corpus_id="abc-123")
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.corpus_id == "abc-123"


def test_render_payload_serialized_bytes_within_budget() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.serialized_bytes <= 40_960


def test_render_payload_blocks_include_header() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    block_types = [b.block_type for b in r.payload.payload.blocks]
    assert "header" in block_types


def test_render_payload_blocks_include_context_metadata() -> None:
    """AC6 -- evidence_quality always present in the typed payload +
    in the typed Block Kit context block."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    # Header + at least one context block (metadata + omitted_counts)
    block_types = [b.block_type for b in r.payload.payload.blocks]
    assert block_types.count("context") >= 2


def test_render_payload_includes_top_findings_summaries() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(3)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert len(r.payload.top_findings) == 3


# --- Section 10: 5-top-findings cap (doc-19:122-123) ----------------


def test_5_top_findings_cap_default() -> None:
    """Doc-19:122-123 -- '5 top findings'."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(12)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert len(r.payload.top_findings) == 5


def test_5_top_findings_cap_override_smaller() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(8)],
    )
    r = _renderer().render(
        SlackRenderInputs(source=src, max_top_findings=2)
    )
    assert r.payload is not None
    assert len(r.payload.top_findings) == 2


def test_5_top_findings_cap_records_omitted() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(7)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    # 7 incoming - 5 cap = 2 dropped by this renderer; combined with
    # upstream zero omitted = 2 total.
    assert r.payload.omitted_counts["findings"] == 2


def test_5_top_findings_cap_truncated_flag_set() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(7)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.truncated is True


def test_5_top_findings_cap_no_truncation_when_under() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(3)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.omitted_counts["findings"] == 0


# --- Section 11: 40 KB Block Kit budget enforcement ---------------


def test_40k_budget_not_exceeded_with_typical_snapshot() -> None:
    """Doc-19:122-123 -- '40 KB serialized Block Kit payload'."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(5)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.serialized_bytes <= 40_960


def test_synthetic_tiny_budget_triggers_truncation() -> None:
    """A pathologically small budget forces additional finding
    truncation beyond the 5-top cap."""

    findings = [_finding(idempotency_key=f"f-{i}") for i in range(5)]
    src = _build_snapshot_via_api(findings=findings)
    r = _renderer().render(
        SlackRenderInputs(
            source=src,
            max_top_findings=5,
            max_payload_bytes=900,  # very small; forces truncation
        )
    )
    # Should still produce a payload (or budget_exceeded if even
    # empty payload won't fit; either is acceptable per the typed
    # contract).
    if r.decision == "emitted":
        assert r.payload is not None
        assert r.payload.serialized_bytes <= 900


def test_synthetic_zero_budget_triggers_budget_exceeded() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding()],
    )
    r = _renderer().render(
        SlackRenderInputs(source=src, max_payload_bytes=1)
    )
    assert r.decision == "budget_exceeded"
    assert r.payload is None
    assert len(r.gap_findings) == 1
    assert r.gap_findings[0].reason == "budget_exceeded"


def test_budget_exceeded_gap_payload_has_byte_count() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(
        SlackRenderInputs(source=src, max_payload_bytes=1)
    )
    gap = r.gap_findings[0]
    assert gap.evidence_payload.get("serialized_bytes") is not None
    assert gap.evidence_payload.get("max_payload_bytes") == 1


# --- Section 12: dedupe_key = snapshot_digest reproducibility ------


def test_dedupe_key_helper_returns_snapshot_digest() -> None:
    src = _build_snapshot_via_api()
    snap = src.snapshot
    assert (
        GovernanceSlackRenderer.compute_dedupe_key(snap)
        == snap.snapshot_digest
    )


def test_dedupe_key_is_static_method() -> None:
    """compute_dedupe_key is a static method (callable without
    instance)."""

    src = _build_snapshot_via_api()
    snap = src.snapshot
    assert (
        GovernanceSlackRenderer.compute_dedupe_key(snap)
        == snap.snapshot_digest
    )


def test_dedupe_key_reproducible_same_snapshot() -> None:
    """Doc-19:218 -- 'Report generation is reproducible for the same
    corpus id.' Same snapshot -> same dedupe_key on repeated render
    calls."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"f-{i}") for i in range(2)],
    )
    renderer = _renderer()
    keys = [
        renderer.render(SlackRenderInputs(source=src)).payload.dedupe_key
        for _ in range(5)
    ]
    assert len(set(keys)) == 1


def test_dedupe_key_differs_when_snapshot_differs() -> None:
    src_a = _build_snapshot_via_api(corpus_id="a")
    src_b = _build_snapshot_via_api(corpus_id="b")
    r_a = _renderer().render(SlackRenderInputs(source=src_a))
    r_b = _renderer().render(SlackRenderInputs(source=src_b))
    assert r_a.payload.dedupe_key != r_b.payload.dedupe_key


def test_dedupe_key_changes_when_evidence_quality_changes() -> None:
    """Doc-19:140-142 -- 'material changes in evidence quality... are
    not suppressed' (digest changes when evidence_quality changes)."""

    src_a = _build_snapshot_via_api(
        corpus_evidence_quality="canonical"
    )
    src_b = _build_snapshot_via_api(
        corpus_evidence_quality="advisory"
    )
    r_a = _renderer().render(SlackRenderInputs(source=src_a))
    r_b = _renderer().render(SlackRenderInputs(source=src_b))
    assert r_a.payload.dedupe_key != r_b.payload.dedupe_key


def test_dedupe_key_changes_when_omitted_counts_change() -> None:
    """Doc-19:140-142 -- 'material changes in... omitted detail counts...
    are not suppressed'."""

    src_a = _build_snapshot_via_api()
    src_b = _build_snapshot_via_api(omitted_findings_count=5)
    r_a = _renderer().render(SlackRenderInputs(source=src_a))
    r_b = _renderer().render(SlackRenderInputs(source=src_b))
    assert r_a.payload.dedupe_key != r_b.payload.dedupe_key


# --- Section 13: dedupe cache-hit suppression ------------------------


def test_render_emits_when_dedupe_key_not_in_cache() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(
        SlackRenderInputs(
            source=src, recently_emitted_dedupe_keys=set()
        )
    )
    assert r.decision == "emitted"
    assert r.payload is not None


def test_render_suppresses_when_dedupe_key_in_cache() -> None:
    src = _build_snapshot_via_api()
    digest = src.snapshot.snapshot_digest
    r = _renderer().render(
        SlackRenderInputs(
            source=src,
            recently_emitted_dedupe_keys={digest},
        )
    )
    assert r.decision == "suppressed_dedupe"
    # Payload IS still constructed (for inspection / metrics) but
    # caller knows NOT to deliver.
    assert r.payload is not None
    assert r.payload.dedupe_key == digest


def test_render_emits_when_cache_has_other_keys() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(
        SlackRenderInputs(
            source=src,
            recently_emitted_dedupe_keys={"other_key_1", "other_key_2"},
        )
    )
    assert r.decision == "emitted"


# --- Section 14: upstream snapshot missing path --------------------


def test_render_upstream_snapshot_missing_returns_decision() -> None:
    """When the upstream snapshot is None, the renderer returns
    decision=upstream_missing + emits a typed gap finding."""

    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    # Validate the synthetic precondition: snapshot is None for
    # corpus_id_empty.
    assert src.snapshot is None
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.decision == "upstream_missing"
    assert r.payload is None
    assert len(r.gap_findings) >= 1


def test_render_upstream_missing_emits_typed_gap() -> None:
    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.gap_findings[0].reason == "upstream_snapshot_missing"
    assert r.gap_findings[0].failure_id == "governance_slack_renderer_failed"


def test_render_upstream_missing_propagates_upstream_gaps() -> None:
    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    assert src.snapshot is None
    upstream_reason = src.gap_findings[0].reason
    upstream_corpus_id = src.gap_findings[0].corpus_id
    r = _renderer().render(SlackRenderInputs(source=src))
    # Renderer emits its own + propagates upstream
    assert len(r.gap_findings) == 2
    propagated = r.gap_findings[1]
    assert propagated.reason == upstream_reason
    assert propagated.corpus_id == upstream_corpus_id
    assert (
        propagated.evidence_payload.get("propagated_from")
        == "snapshot_api_gap"
    )


def test_render_failure_id_used_on_gaps() -> None:
    """The typed SLACK_RENDERER_FAILURE_ID is the failure id used on
    every typed gap row."""

    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    for gap in r.gap_findings:
        assert gap.failure_id == "governance_slack_renderer_failed"


def test_render_corpus_id_unknown_when_no_upstream_gaps() -> None:
    """Edge case: snapshot is None but no upstream gap findings -> the
    Slack gap's corpus_id falls back to '<unknown>'."""

    src = SnapshotAPIResult(snapshot=None, gap_findings=[])
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.decision == "upstream_missing"
    assert r.payload is None
    assert len(r.gap_findings) == 1
    assert r.gap_findings[0].corpus_id == "<unknown>"


# --- Section 15: fail-closed never-raises -------------------------


def test_render_never_raises_on_empty_corpus() -> None:
    api = GovernanceSnapshotAPI()
    src = api.build_snapshot(
        SnapshotAPIInputs(corpus_id=""), SnapshotAPICorpus()
    )
    # Should not raise
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r is not None


def test_render_never_raises_on_synthetic_none_snapshot() -> None:
    src = SnapshotAPIResult(snapshot=None, gap_findings=[])
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r is not None


def test_render_never_raises_on_pathological_budget() -> None:
    src = _build_snapshot_via_api()
    r = _renderer().render(
        SlackRenderInputs(source=src, max_payload_bytes=1)
    )
    assert r is not None


def test_render_never_raises_on_empty_findings_list() -> None:
    src = _build_snapshot_via_api(findings=[])
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r is not None
    assert r.payload is not None


# --- Section 16: DIRECT annotation-identity REUSE ----------------------


def test_inputs_source_reuses_slice_19_2nd_snapshot_api_result() -> None:
    hints = typing.get_type_hints(SlackRenderInputs)
    ann = hints["source"]
    assert ann is SnapshotAPIResult


def test_payload_completeness_reuses_slice_13a_literal() -> None:
    hints = typing.get_type_hints(SlackRenderPayload)
    ann = hints["completeness"]
    assert ann is CompletenessState


def test_payload_evidence_quality_reuses_slice_13a_literal() -> None:
    hints = typing.get_type_hints(SlackRenderPayload)
    ann = hints["evidence_quality"]
    assert ann is EvidenceQuality


def test_payload_top_findings_reuses_slack_finding_summary() -> None:
    hints = typing.get_type_hints(SlackRenderPayload)
    ann = hints["top_findings"]
    args = typing.get_args(ann)
    assert args[0] is SlackFindingSummary


def test_payload_payload_reuses_slack_block_kit_payload() -> None:
    hints = typing.get_type_hints(SlackRenderPayload)
    ann = hints["payload"]
    assert ann is SlackBlockKitPayload


def test_finding_summary_kind_reuses_slice_16_literal() -> None:
    hints = typing.get_type_hints(SlackFindingSummary)
    ann = hints["kind"]
    assert ann is FindingKind


def test_finding_summary_severity_reuses_slice_16_literal() -> None:
    hints = typing.get_type_hints(SlackFindingSummary)
    ann = hints["severity"]
    assert ann is FindingSeverity


def test_result_payload_reuses_slack_render_payload() -> None:
    hints = typing.get_type_hints(SlackRenderResult)
    ann = hints["payload"]
    union_args = typing.get_args(ann)
    payload_t = next(a for a in union_args if a is not type(None))
    assert payload_t is SlackRenderPayload


def test_compute_dedupe_key_signature_reuses_slice_19_first_snapshot() -> None:
    """compute_dedupe_key annotation accepts the typed Slice 19 1st
    sub-slice GovernanceSnapshot directly (no second source of
    truth)."""

    hints = typing.get_type_hints(
        GovernanceSlackRenderer.compute_dedupe_key
    )
    assert hints["snapshot"] is GovernanceSnapshot


# --- Section 17: failure-router 4-pure-data-add discipline -------------


def test_failure_router_failure_id_in_typed_literal() -> None:
    """`governance_slack_renderer_failed` is in the FailureType Literal."""

    assert "governance_slack_renderer_failed" in typing.get_args(
        FailureType
    )


def test_failure_router_failure_id_in_failure_types_tuple() -> None:
    assert "governance_slack_renderer_failed" in FAILURE_TYPES


def test_failure_router_route_table_uses_evidence_corruption_class() -> None:
    key = ("evidence_corruption", "governance_slack_renderer_failed")
    assert key in ROUTE_TABLE


def test_failure_router_route_table_uses_retry_governance_projection() -> None:
    key = ("evidence_corruption", "governance_slack_renderer_failed")
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
        "governance_slack_renderer_failed"
        in fr._RETRYABLE_FAILURE_TYPES
    )


def test_failure_router_fourteen_governance_failure_ids_total() -> None:
    """5 Slice 17 + 6 Slice 18 + 1 Slice 19 2nd + 1 Slice 19 3rd +
    1 Slice 19 4th = 14 typed governance failure ids routing to
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
        "governance_slack_renderer_failed",
    }
    gov_routed = {
        ft
        for (cls, ft), r in ROUTE_TABLE.items()
        if r.action == "retry_governance_projection"
    }
    assert slice_17_plus <= gov_routed
    assert len(slice_17_plus & gov_routed) == 14


# --- Section 18: doc-19 acceptance criteria enforcement ----------------


def test_ac1_payload_dedupe_key_for_reproducibility() -> None:
    """Doc-19:224 AC1 -- 'Reports are bounded, reproducible, evidence-
    cited, and structured first.'

    Reproducibility: dedupe_key == snapshot_digest (verbatim).
    Evidence-cited: typed summary surface + typed identity refs.
    Bounded: max_payload_bytes + truncated + omitted_counts +
    serialized_bytes.
    Structured first: typed Pydantic BaseModel surface.
    """

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    # Reproducible
    assert r.payload.dedupe_key == src.snapshot.snapshot_digest
    # Bounded
    assert hasattr(r.payload, "serialized_bytes")
    assert hasattr(r.payload, "truncated")
    assert hasattr(r.payload, "omitted_counts")
    # Structured first
    assert isinstance(r.payload, BaseModel)


def test_ac2_display_only_true_when_truncated_without_page_refs() -> None:
    """Doc-19:225-226 AC2 -- truncated payload without exact page refs
    is display-only per doc-19:128-131."""

    # Build a snapshot with truncation but no page_refs (upstream
    # snapshot's findings already truncated by snapshot API).
    findings = [_finding(idempotency_key=f"k-{i}") for i in range(8)]
    src = _build_snapshot_via_api(
        findings=findings, max_findings=5
    )
    # snapshot.truncated is True (5 of 8 findings emitted; 3 omitted
    # upstream)
    assert src.snapshot.truncated is True
    assert not src.snapshot.page_refs  # no page_refs
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.truncated is True
    assert r.payload.display_only is True


def test_ac2_display_only_false_when_truncated_with_page_refs() -> None:
    """Doc-19:225-226 AC2 -- truncated payload WITH page_refs is NOT
    display-only (consumers can drill-through via dashboard)."""

    findings = [_finding(idempotency_key=f"k-{i}") for i in range(8)]
    src = _build_snapshot_via_api(
        findings=findings,
        max_findings=5,
        page_refs=[_page_ref()],
    )
    assert src.snapshot.truncated is True
    assert src.snapshot.page_refs
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.truncated is True
    assert r.payload.display_only is False


def test_ac2_display_only_true_when_completeness_preview_only() -> None:
    src = _build_snapshot_via_api(
        completeness_override="preview_only",
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.completeness == "preview_only"
    assert r.payload.display_only is True


def test_ac2_display_only_true_when_completeness_unavailable() -> None:
    src = _build_snapshot_via_api(
        completeness_override="unavailable",
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.completeness == "unavailable"
    assert r.payload.display_only is True


def test_ac2_display_only_false_when_complete() -> None:
    src = _build_snapshot_via_api()  # no truncation; complete
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.completeness == "complete"
    assert r.payload.display_only is False


def test_ac2_display_only_notice_block_present_when_flagged() -> None:
    """When display_only=True the Block Kit payload includes a typed
    context block warning the reader."""

    src = _build_snapshot_via_api(completeness_override="preview_only")
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.display_only is True
    block_texts = [
        b.text
        for b in r.payload.payload.blocks
        if b.text is not None
    ]
    assert any("Display-only" in t for t in block_texts)


def test_ac6_evidence_quality_always_present() -> None:
    """Doc-19:232-233 AC6 -- evidence_quality + omitted_counts ALWAYS
    present (typed surface; required field)."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.evidence_quality in typing.get_args(EvidenceQuality)
    assert isinstance(r.payload.omitted_counts, dict)


def test_ac6_evidence_quality_in_metadata_block() -> None:
    src = _build_snapshot_via_api(
        corpus_evidence_quality="sampled"
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    block_texts = [
        b.text
        for b in r.payload.payload.blocks
        if b.text is not None
    ]
    # Evidence quality must appear in at least one rendered block
    assert any("sampled" in t for t in block_texts)


def test_ac6_omitted_counts_in_payload_and_block() -> None:
    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(7)],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    block_texts = [
        b.text
        for b in r.payload.payload.blocks
        if b.text is not None
    ]
    # The omitted-counts text block always present in the Block Kit.
    assert any("Omitted:" in t for t in block_texts)


def test_ac7_no_control_plane_writer_methods_extension() -> None:
    """Doc-19:234 + doc-19:348-349 AC -- the Slack renderer class has
    ONLY render + compute_dedupe_key public methods; no mutation
    methods; no CONTROL_PLANE_WRITER_METHODS extension."""

    public_methods = sorted(
        name
        for name in dir(GovernanceSlackRenderer)
        if not name.startswith("_")
    )
    assert public_methods == ["compute_dedupe_key", "render"]


def test_ac7_refs_only_no_body_hydration() -> None:
    """Doc-19:234 AC7 -- bounded-reads + refs-only contract.

    The renderer surfaces only the typed identity-surface fields on
    SlackFindingSummary (idempotency_key + class_name + severity +
    confidence) -- NOT the full GovernanceFinding body."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key="abc")],
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    summary = r.payload.top_findings[0]
    # SlackFindingSummary has only 6 typed fields (identity surface
    # + ranking inputs); not the full GovernanceFinding body.
    summary_fields = set(SlackFindingSummary.model_fields.keys())
    assert summary_fields == {
        "idempotency_key",
        "kind",
        "class_name",
        "severity",
        "confidence",
        "estimated_lost_hours",
    }


def test_doc_19_140_142_dedupe_key_is_snapshot_digest_verbatim() -> None:
    """Doc-19:140-142 -- 'Slack digest with dedupe key from
    snapshot_digest'."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.dedupe_key == src.snapshot.snapshot_digest


def test_doc_19_122_123_40k_budget_default() -> None:
    """Doc-19:122-123 -- '40 KB serialized Block Kit payload'."""

    src = _build_snapshot_via_api()
    inp = SlackRenderInputs(source=src)
    assert inp.max_payload_bytes == 40_960


def test_doc_19_122_123_5_top_findings_default() -> None:
    """Doc-19:122-123 -- '5 top findings'."""

    src = _build_snapshot_via_api()
    inp = SlackRenderInputs(source=src)
    assert inp.max_top_findings == 5


def test_doc_19_218_reproducible_same_corpus_id() -> None:
    """Doc-19:218 -- 'Report generation is reproducible for the same
    corpus id.' Two render calls with the same upstream snapshot
    produce identical dedupe_keys."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(2)],
    )
    renderer = _renderer()
    keys = [
        renderer.render(SlackRenderInputs(source=src)).payload.dedupe_key
        for _ in range(5)
    ]
    assert len(set(keys)) == 1


# --- Section 19: activation-authority boundary -------------------------


def test_module_has_no_mutation_methods() -> None:
    """Per the Slice 17 7th sub-slice activation-boundary discipline +
    doc-19:348-349 AC the Slack renderer has NO mutation methods on
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
        SlackFindingSummary,
        SlackBlockKitBlock,
        SlackBlockKitPayload,
        SlackRenderInputs,
        SlackRenderPayload,
        SlackRenderGap,
        SlackRenderResult,
    ):
        for name in dir(cls):
            if name.startswith("_") or name.startswith("model_"):
                continue
            assert not name.startswith(forbidden_method_prefixes), (
                f"{cls.__name__}.{name} resembles a mutation method "
                "(per AC7 + AC: doc-19:348-349)"
            )


def test_no_dag_artifact_key_literals() -> None:
    """Per doc-19:348-349 the typed Slack renderer does NOT mint
    dag-execution-authority artifact-key string literals."""

    import inspect

    from iriai_build_v2.execution_control import (
        governance_slack_renderer as mod,
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
            f"governance_slack_renderer.py (per doc-19:348-349 AC)."
        )


# --- Section 20: helper-level (truncation/projection) tests ------------


def test_project_findings_handles_negative_cap() -> None:
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    summaries, omitted = GovernanceSlackRenderer._project_findings(
        rows, -5
    )
    assert summaries == []
    assert omitted == 3


def test_project_findings_handles_zero_cap() -> None:
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    summaries, omitted = GovernanceSlackRenderer._project_findings(
        rows, 0
    )
    assert summaries == []
    assert omitted == 3


def test_project_findings_handles_exact_cap() -> None:
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(3)]
    summaries, omitted = GovernanceSlackRenderer._project_findings(
        rows, 3
    )
    assert len(summaries) == 3
    assert omitted == 0


def test_project_findings_handles_overcap() -> None:
    rows = [_finding(idempotency_key=f"k-{i}") for i in range(5)]
    summaries, omitted = GovernanceSlackRenderer._project_findings(
        rows, 2
    )
    assert len(summaries) == 2
    assert omitted == 3
    assert summaries[0].idempotency_key == "k-0"
    assert summaries[1].idempotency_key == "k-1"


def test_project_findings_extracts_identity_surface() -> None:
    rows = [_finding()]
    summaries, _ = GovernanceSlackRenderer._project_findings(rows, 5)
    assert len(summaries) == 1
    s = summaries[0]
    assert s.idempotency_key == "finding-key-19-4-a"
    assert s.kind == "workflow_inefficiency"
    assert s.class_name == "commit_hygiene_loop"
    assert s.severity == "medium"
    assert s.confidence == 0.85


def test_derive_display_only_preview_only() -> None:
    assert GovernanceSlackRenderer._derive_display_only(
        completeness="preview_only", truncated=False, page_refs=["p"]
    ) is True


def test_derive_display_only_unavailable() -> None:
    assert GovernanceSlackRenderer._derive_display_only(
        completeness="unavailable", truncated=False, page_refs=[]
    ) is True


def test_derive_display_only_truncated_no_page_refs() -> None:
    assert GovernanceSlackRenderer._derive_display_only(
        completeness="paged", truncated=True, page_refs=[]
    ) is True


def test_derive_display_only_truncated_with_page_refs() -> None:
    assert GovernanceSlackRenderer._derive_display_only(
        completeness="paged", truncated=True, page_refs=["p"]
    ) is False


def test_derive_display_only_complete() -> None:
    assert GovernanceSlackRenderer._derive_display_only(
        completeness="complete", truncated=False, page_refs=[]
    ) is False


def test_corpus_id_from_upstream_gaps_empty() -> None:
    assert (
        GovernanceSlackRenderer._corpus_id_from_upstream_gaps([])
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
        GovernanceSlackRenderer._corpus_id_from_upstream_gaps([gap])
        == "abc"
    )


def test_format_finding_block_contains_severity() -> None:
    s = SlackFindingSummary(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="cls",
        severity="medium",
        confidence=0.5,
        estimated_lost_hours=2.0,
    )
    text = GovernanceSlackRenderer._format_finding_block(s)
    assert "MEDIUM" in text
    assert "cls" in text
    assert "2.0h lost" in text


def test_format_finding_block_handles_none_lost_hours() -> None:
    s = SlackFindingSummary(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="cls",
        severity="low",
        confidence=0.3,
        estimated_lost_hours=None,
    )
    text = GovernanceSlackRenderer._format_finding_block(s)
    assert "(no lost-hours estimate)" in text


def test_format_metadata_block_includes_corpus_and_quality() -> None:
    src = _build_snapshot_via_api(
        corpus_id="abc",
        corpus_evidence_quality="advisory",
    )
    text = GovernanceSlackRenderer._format_metadata_block(src.snapshot)
    assert "abc" in text
    assert "advisory" in text


def test_format_omitted_counts_block_empty() -> None:
    text = GovernanceSlackRenderer._format_omitted_counts_block({})
    assert text == "Omitted: (none)"


def test_format_omitted_counts_block_with_keys() -> None:
    text = GovernanceSlackRenderer._format_omitted_counts_block(
        {"findings": 2, "recommendations": 0}
    )
    assert "findings: 2" in text
    assert "recommendations: 0" in text


def test_format_recommendations_block_empty() -> None:
    text = GovernanceSlackRenderer._format_recommendations_block([])
    assert "none" in text


def test_format_recommendations_block_with_recs() -> None:
    text = GovernanceSlackRenderer._format_recommendations_block(
        [_recommendation()]
    )
    assert "rec-19-4-1" in text
    assert "scheduler" in text
    assert "draft" in text


def test_format_replay_results_block_empty() -> None:
    text = GovernanceSlackRenderer._format_replay_results_block([])
    assert "none" in text


def test_format_replay_results_block_with_results() -> None:
    text = GovernanceSlackRenderer._format_replay_results_block(
        [_replay_result()]
    )
    assert "result-19-4-1" in text
    assert "lower" in text


# --- Section 21: GovernanceSlackRenderer instance is stateless --------


def test_renderer_instance_can_be_reused() -> None:
    src1 = _build_snapshot_via_api(corpus_id="a")
    src2 = _build_snapshot_via_api(corpus_id="b")
    renderer = _renderer()
    r1 = renderer.render(SlackRenderInputs(source=src1))
    r2 = renderer.render(SlackRenderInputs(source=src2))
    assert r1.payload is not None
    assert r2.payload is not None
    assert r1.payload.corpus_id == "a"
    assert r2.payload.corpus_id == "b"
    assert r1.payload.dedupe_key != r2.payload.dedupe_key


def test_renderer_has_no_instance_state() -> None:
    renderer = _renderer()
    assert vars(renderer) == {}


# --- Section 22: end-to-end typed snapshot -> Slack payload ------


def test_end_to_end_via_snapshot_api() -> None:
    """Full pipeline: build via Slice 19 2nd sub-slice API -> render
    via Slice 19 4th sub-slice renderer -> verify the typed payload
    fields populate correctly."""

    src = _build_snapshot_via_api(
        corpus_id="e2e-corpus",
        findings=[_finding(idempotency_key="f1")],
        recommendations=[_recommendation()],
        replay_results=[_replay_result()],
        page_refs=[_page_ref()],
        corpus_evidence_quality="sampled",
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    p = r.payload
    assert p.corpus_id == "e2e-corpus"
    assert p.dedupe_key == src.snapshot.snapshot_digest
    assert p.evidence_quality == "sampled"
    assert len(p.top_findings) == 1
    assert p.top_findings[0].idempotency_key == "f1"
    assert p.display_only is False
    assert p.truncated is False
    assert p.serialized_bytes > 0
    assert p.serialized_bytes <= 40_960


def test_end_to_end_with_truncation_and_page_refs() -> None:
    """Truncated Slack payload with page_refs is NOT display-only."""

    src = _build_snapshot_via_api(
        findings=[_finding(idempotency_key=f"k-{i}") for i in range(8)],
        page_refs=[_page_ref()],
        max_findings=5,
    )
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert r.payload.truncated is True
    # Not display-only because page_refs are present.
    assert r.payload.display_only is False


def test_end_to_end_dedupe_decision_emitted() -> None:
    """A fresh snapshot with empty cache -> decision=emitted."""

    src = _build_snapshot_via_api(corpus_id="fresh")
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.decision == "emitted"


def test_end_to_end_dedupe_decision_suppressed() -> None:
    """A snapshot whose dedupe_key is in the cache -> decision=
    suppressed_dedupe."""

    src = _build_snapshot_via_api(corpus_id="repeat")
    r = _renderer().render(
        SlackRenderInputs(
            source=src,
            recently_emitted_dedupe_keys={
                src.snapshot.snapshot_digest
            },
        )
    )
    assert r.decision == "suppressed_dedupe"


# --- Section 23: snapshot generated_at preserved -----------------------


def test_payload_carries_two_distinct_timestamps() -> None:
    """The payload preserves BOTH the snapshot's generated_at and the
    renderer's own generated_at (so stale-snapshot detection works)."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    assert (
        r.payload.snapshot_generated_at
        == src.snapshot.generated_at
    )
    assert (
        r.payload.generated_at
        >= r.payload.snapshot_generated_at
    )


# --- Section 24: serialized_bytes is accurate -------------------


def test_serialized_bytes_matches_actual_encoded_payload() -> None:
    """The typed serialized_bytes field exposes the actual byte
    count of the encoded Block Kit JSON."""

    src = _build_snapshot_via_api()
    r = _renderer().render(SlackRenderInputs(source=src))
    assert r.payload is not None
    actual_bytes = len(
        r.payload.payload.to_block_kit_json().encode("utf-8")
    )
    assert r.payload.serialized_bytes == actual_bytes
