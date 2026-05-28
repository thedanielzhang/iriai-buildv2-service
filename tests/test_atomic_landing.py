"""Slice 12b -- tests for the readiness gates infrastructure.

Covers the doc-12 release-control contracts and supporting surfaces:

- ``AtomicLandingGateResult`` typed contract (positive + negative; the
  fail-closed defaults + the go-requires evaluation).
- ``WorkflowImprovementMetrics`` typed contract.
- ``ReadinessGateEvidence`` + ``ReadinessGateEvidenceSurface``.
- ``CiTestMatrixRow`` + ``CiTestMatrixResult``.
- ``MetricsCollector`` + the doc-12 success-metric formulas.
- The module-level constants tracking the doc-12 enums verbatim.
- The no-back-import guard against
  ``workflows.develop.phases.implementation``.

Per the prompt: "Test surface must be COMPREHENSIVE (this is a critical-path
contract)."
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control import atomic_landing
from iriai_build_v2.execution_control.atomic_landing import (
    FORBIDDEN_PARTIAL_CONTROLS,
    REQUIRED_CI_TEST_GROUPS,
    REQUIRED_READINESS_GATES,
    WORKFLOW_DRAG_FAILURE_CLASSES,
    AtomicLandingGateResult,
    BaselineMetricsSource,
    CiTestMatrixResult,
    CiTestMatrixRow,
    InFlightAdoptionRecord,
    LegacyArtifactMetricsSource,
    MetricsCollectionInputs,
    MetricsCollector,
    ReadinessGateEvidence,
    ReadinessGateEvidenceSurface,
    TypedStateMetricsSource,
    WorkflowImprovementMetrics,
    compute_task_complexity_weight,
    evaluate_metrics_success,
)


# ── module constants ────────────────────────────────────────────────────────


def test_required_readiness_gates_match_doc_12() -> None:
    """REQUIRED_READINESS_GATES contains the 10 doc-12 gates verbatim."""

    assert REQUIRED_READINESS_GATES == (
        "atomic_enablement",
        "schema_and_journal",
        "workspace_and_contracts",
        "sandbox_and_dispatcher",
        "verification_and_routing",
        "merge_and_checkpoint",
        "post_dag_business_gates",
        "consumers",
        "resource_safety",
        "operations",
    )
    # 10 gates exactly.
    assert len(REQUIRED_READINESS_GATES) == 10


def test_required_ci_test_groups_match_doc_12() -> None:
    """REQUIRED_CI_TEST_GROUPS contains the 11 doc-12 test groups verbatim."""

    assert REQUIRED_CI_TEST_GROUPS == (
        "static_import_and_syntax",
        "expanded_verification_and_regroup",
        "quiesce_and_resume_safety",
        "workspace_authority",
        "contracts_and_sandbox",
        "dispatcher_gates_and_router",
        "merge_queue_and_checkpoint_proof",
        "post_dag_and_post_test_compatibility",
        "supervisor_and_dashboard_read_models",
        "planning_compatibility",
        "full_regression",
    )
    assert len(REQUIRED_CI_TEST_GROUPS) == 11


def test_forbidden_partial_controls_match_doc_12_table() -> None:
    """FORBIDDEN_PARTIAL_CONTROLS contains the 10 per-slice controls (NOT
    IRIAI_EXEC_CONTROL_PLANE_ENABLED -- that is the ONLY product-
    authoritative switch per doc 12)."""

    assert FORBIDDEN_PARTIAL_CONTROLS == frozenset(
        {
            "IRIAI_EXEC_JOURNAL_SHADOW",
            "IRIAI_WORKSPACE_AUTHORITY_BLOCKING",
            "IRIAI_TASK_CONTRACTS_BLOCKING",
            "IRIAI_SANDBOX_CAPTURE_SHADOW",
            "IRIAI_RUNTIME_DISPATCHER_V2",
            "IRIAI_VERIFICATION_GRAPH_V2",
            "IRIAI_FAILURE_ROUTER_V2",
            "IRIAI_MERGE_QUEUE_V2",
            "IRIAI_REGROUP_OVERLAY_V2",
            "IRIAI_SUPERVISOR_TYPED_SNAPSHOT",
        }
    )
    # Critical: IRIAI_EXEC_CONTROL_PLANE_ENABLED is NOT in the forbidden set.
    assert "IRIAI_EXEC_CONTROL_PLANE_ENABLED" not in FORBIDDEN_PARTIAL_CONTROLS


def test_workflow_drag_failure_classes_match_doc_12() -> None:
    """WORKFLOW_DRAG_FAILURE_CLASSES contains the 7 doc-12 failure classes."""

    assert WORKFLOW_DRAG_FAILURE_CLASSES == (
        "worktree_alias",
        "acl_workability",
        "stale_projection",
        "commit_hygiene",
        "runtime_provider",
        "merge_conflict",
        "checkpoint_contradiction",
    )
    assert len(WORKFLOW_DRAG_FAILURE_CLASSES) == 7


# ── readiness gate evidence ─────────────────────────────────────────────────


def _gate_passed(
    gate: str = "atomic_enablement",
    candidate: str = "abc123",
    summary: str = "all checks green",
) -> ReadinessGateEvidence:
    return ReadinessGateEvidence(
        gate=gate,  # type: ignore[arg-type]
        status="passed",
        candidate_commit=candidate,
        recorded_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        summary=summary,
        evidence_refs=["evidence:1"],
        proof_owner="exec-control-plane",
    )


def test_readiness_gate_evidence_passed_round_trip() -> None:
    evidence = _gate_passed()
    dumped = evidence.model_dump_json()
    reloaded = ReadinessGateEvidence.model_validate_json(dumped)
    assert reloaded.gate == "atomic_enablement"
    assert reloaded.status == "passed"
    assert reloaded.candidate_commit == "abc123"
    assert reloaded.no_go_reasons == []


def test_readiness_gate_evidence_rejects_empty_candidate() -> None:
    with pytest.raises(ValidationError, match="candidate_commit"):
        ReadinessGateEvidence(
            gate="atomic_enablement",
            status="passed",
            candidate_commit="",
            recorded_at=datetime.now(timezone.utc),
        )


def test_readiness_gate_evidence_non_passed_requires_reasons() -> None:
    """Fail-closed: a missing/failed/stale gate must say WHY."""

    for status in ("failed", "missing", "stale"):
        with pytest.raises(ValidationError, match="no_go_reasons"):
            ReadinessGateEvidence(
                gate="atomic_enablement",
                status=status,  # type: ignore[arg-type]
                candidate_commit="abc123",
                recorded_at=datetime.now(timezone.utc),
                no_go_reasons=[],
            )


def test_readiness_gate_evidence_passed_rejects_reasons() -> None:
    """A passed gate carrying no-go reasons is contradictory; reject."""

    with pytest.raises(ValidationError, match="must not carry no_go_reasons"):
        ReadinessGateEvidence(
            gate="atomic_enablement",
            status="passed",
            candidate_commit="abc123",
            recorded_at=datetime.now(timezone.utc),
            no_go_reasons=["spurious"],
        )


def test_readiness_gate_evidence_invalid_status_rejected() -> None:
    """The status enum is locked to the doc-12 four-tuple."""

    with pytest.raises(ValidationError):
        ReadinessGateEvidence(
            gate="atomic_enablement",
            status="unknown",  # type: ignore[arg-type]
            candidate_commit="abc123",
            recorded_at=datetime.now(timezone.utc),
        )


def test_readiness_gate_evidence_unknown_gate_rejected() -> None:
    """The gate enum is locked to the doc-12 10-tuple."""

    with pytest.raises(ValidationError):
        ReadinessGateEvidence(
            gate="not_a_real_gate",  # type: ignore[arg-type]
            status="passed",
            candidate_commit="abc123",
            recorded_at=datetime.now(timezone.utc),
        )


def test_readiness_gate_evidence_surface_round_trip() -> None:
    surface = ReadinessGateEvidenceSurface(
        candidate_commit="abc123",
        deploy_artifact_id="deploy:42",
        generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        gates=[_gate_passed(gate=name) for name in REQUIRED_READINESS_GATES],
    )
    assert len(surface.gates) == 10
    result_map = surface.gate_results_map()
    assert set(result_map.keys()) == set(REQUIRED_READINESS_GATES)
    for status in result_map.values():
        assert status == "passed"


def test_readiness_gate_evidence_surface_missing_gate_reports_missing() -> None:
    """Fail-closed: when a required gate is not provided, the surface reports
    it as ``missing``."""

    surface = ReadinessGateEvidenceSurface(
        candidate_commit="abc123",
        deploy_artifact_id="deploy:42",
        generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        gates=[_gate_passed(gate="atomic_enablement")],
    )
    result_map = surface.gate_results_map()
    assert result_map["atomic_enablement"] == "passed"
    # Every other required gate is missing.
    for name in REQUIRED_READINESS_GATES:
        if name == "atomic_enablement":
            continue
        assert result_map[name] == "missing"


def test_readiness_gate_evidence_surface_rejects_candidate_mismatch() -> None:
    """Doc 12: proof must share the candidate commit with the deploy artifact."""

    with pytest.raises(ValidationError, match="candidate_commit"):
        ReadinessGateEvidenceSurface(
            candidate_commit="abc123",
            deploy_artifact_id="deploy:42",
            generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
            gates=[
                ReadinessGateEvidence(
                    gate="atomic_enablement",
                    status="passed",
                    candidate_commit="other-commit",  # MISMATCH
                    recorded_at=datetime.now(timezone.utc),
                )
            ],
        )


def test_readiness_gate_evidence_surface_rejects_empty_identity() -> None:
    with pytest.raises(ValidationError, match="non-empty"):
        ReadinessGateEvidenceSurface(
            candidate_commit="",
            deploy_artifact_id="deploy:42",
            generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )

    with pytest.raises(ValidationError, match="non-empty"):
        ReadinessGateEvidenceSurface(
            candidate_commit="abc123",
            deploy_artifact_id="",
            generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        )


# ── CI/test matrix ─────────────────────────────────────────────────────────


def _row_passed(
    group: str = "static_import_and_syntax",
    command: str = "python -m compileall -q src/iriai_build_v2 dashboard.py",
) -> CiTestMatrixRow:
    return CiTestMatrixRow(
        test_group=group,  # type: ignore[arg-type]
        command=command,
        candidate_commit="abc123",
        run_id="ci:1",
        verdict="passed",
        freshness="fresh",
        started_at=datetime(2026, 5, 23, 10, 0, tzinfo=timezone.utc),
        finished_at=datetime(2026, 5, 23, 10, 5, tzinfo=timezone.utc),
        pass_count=100,
    )


def test_ci_test_matrix_row_round_trip() -> None:
    row = _row_passed()
    dumped = row.model_dump_json()
    reloaded = CiTestMatrixRow.model_validate_json(dumped)
    assert reloaded.test_group == "static_import_and_syntax"
    assert reloaded.verdict == "passed"
    assert reloaded.freshness == "fresh"


def test_ci_test_matrix_row_rejects_empty_identity() -> None:
    for missing_field in ("command", "candidate_commit", "run_id"):
        kwargs = {
            "test_group": "static_import_and_syntax",
            "command": "x",
            "candidate_commit": "y",
            "run_id": "z",
            "verdict": "passed",
            "freshness": "fresh",
            "started_at": datetime.now(timezone.utc),
        }
        kwargs[missing_field] = ""
        with pytest.raises(ValidationError):
            CiTestMatrixRow(**kwargs)  # type: ignore[arg-type]


def test_ci_test_matrix_row_non_passing_requires_summary() -> None:
    """Fail-closed: a non-passing row must record WHY."""

    for verdict in ("failed", "missing", "skipped"):
        with pytest.raises(ValidationError, match="failure_summary"):
            CiTestMatrixRow(
                test_group="static_import_and_syntax",
                command="x",
                candidate_commit="y",
                run_id="z",
                verdict=verdict,  # type: ignore[arg-type]
                freshness="unknown",
                started_at=datetime.now(timezone.utc),
                failure_summary="",
            )


def test_ci_test_matrix_row_passed_rejects_stale() -> None:
    """A stale row can never be 'passed' per doc 12 freshness rule."""

    with pytest.raises(ValidationError, match="freshness='fresh'"):
        CiTestMatrixRow(
            test_group="static_import_and_syntax",
            command="x",
            candidate_commit="y",
            run_id="z",
            verdict="passed",
            freshness="stale",
            started_at=datetime.now(timezone.utc),
        )

    with pytest.raises(ValidationError, match="freshness='fresh'"):
        CiTestMatrixRow(
            test_group="static_import_and_syntax",
            command="x",
            candidate_commit="y",
            run_id="z",
            verdict="passed",
            freshness="unknown",
            started_at=datetime.now(timezone.utc),
        )


def test_ci_test_matrix_row_rejects_negative_counts() -> None:
    for field in ("pass_count", "fail_count", "skipped_count"):
        kwargs = {
            "test_group": "static_import_and_syntax",
            "command": "x",
            "candidate_commit": "y",
            "run_id": "z",
            "verdict": "passed",
            "freshness": "fresh",
            "started_at": datetime.now(timezone.utc),
        }
        kwargs[field] = -1
        with pytest.raises(ValidationError):
            CiTestMatrixRow(**kwargs)  # type: ignore[arg-type]


def test_ci_test_matrix_row_finished_at_before_started_rejected() -> None:
    started = datetime.now(timezone.utc)
    finished = started - timedelta(minutes=5)
    with pytest.raises(ValidationError, match="finished_at"):
        CiTestMatrixRow(
            test_group="static_import_and_syntax",
            command="x",
            candidate_commit="y",
            run_id="z",
            verdict="passed",
            freshness="fresh",
            started_at=started,
            finished_at=finished,
        )


def test_ci_test_matrix_result_all_required_groups_passed_when_complete() -> None:
    result = CiTestMatrixResult(
        candidate_commit="abc123",
        matrix_run_id="ci:1",
        generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        rows=[_row_passed(group=name) for name in REQUIRED_CI_TEST_GROUPS],
    )
    assert result.all_required_groups_passed() is True
    assert result.missing_or_failing_groups() == []


def test_ci_test_matrix_result_missing_group_fails_closed() -> None:
    """Fail-closed: omitting any required test group fails the matrix."""

    result = CiTestMatrixResult(
        candidate_commit="abc123",
        matrix_run_id="ci:1",
        generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        rows=[_row_passed(group=REQUIRED_CI_TEST_GROUPS[0])],  # Only one row.
    )
    assert result.all_required_groups_passed() is False
    blockers = result.missing_or_failing_groups()
    # 10 of 11 required groups are missing.
    assert len(blockers) == len(REQUIRED_CI_TEST_GROUPS) - 1
    for name in REQUIRED_CI_TEST_GROUPS[1:]:
        assert f"{name}:missing" in blockers


def test_ci_test_matrix_result_rejects_row_candidate_mismatch() -> None:
    """All rows must share the candidate commit with the matrix."""

    other_row = _row_passed()
    other_row = other_row.model_copy(update={"candidate_commit": "OTHER"})
    with pytest.raises(ValidationError, match="candidate_commit"):
        CiTestMatrixResult(
            candidate_commit="abc123",
            matrix_run_id="ci:1",
            generated_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
            rows=[other_row],
        )


# ── AtomicLandingGateResult ─────────────────────────────────────────────────


def _all_gates_passed_map() -> dict[str, str]:
    return {name: "passed" for name in REQUIRED_READINESS_GATES}


def _passing_landing_kwargs(**override) -> dict[str, object]:
    base = dict(
        candidate_id="cand-1",
        candidate_commit="abc123",
        deploy_artifact_id="deploy:42",
        passed=True,
        required_tests=list(REQUIRED_CI_TEST_GROUPS),
        required_gate_results=_all_gates_passed_map(),
        ci_matrix_run_id="ci:1",
        metrics_snapshot_id=42,
        operational_decision="go",
        decided_by="release-owner",
        decided_at=datetime(2026, 5, 23, tzinfo=timezone.utc),
        rollback_runbook_id="runbook:rollback-12",
        forbidden_partial_controls_enabled=[],
        blockers=[],
    )
    base.update(override)
    return base


def test_atomic_landing_gate_result_defaults_to_no_go() -> None:
    """Fail-closed: minimum-info construction is a no-go."""

    result = AtomicLandingGateResult(
        candidate_id="cand-1",
        candidate_commit="abc123",
        deploy_artifact_id="deploy:42",
    )
    assert result.passed is False
    assert result.operational_decision == "no_go"
    assert result.required_gate_results == {}
    assert result.ci_matrix_run_id is None
    assert result.metrics_snapshot_id is None
    assert result.decided_by is None
    assert result.decided_at is None
    assert result.rollback_runbook_id is None
    assert result.forbidden_partial_controls_enabled == []
    assert result.blockers == []
    passed, blockers = result.evaluate_go_requires()
    assert passed is False
    # Every required gate is missing.
    for name in REQUIRED_READINESS_GATES:
        assert any(f"required_gate:{name}" in b for b in blockers)
    # The non-gate go-requires are all present in blockers.
    assert "ci_matrix_run_id:missing" in blockers
    assert "metrics_snapshot_id:missing" in blockers
    assert "decided_by:missing" in blockers
    assert "decided_at:missing" in blockers
    assert "rollback_runbook_id:missing" in blockers


def test_atomic_landing_gate_result_passed_true_requires_full_signals() -> None:
    """A landing record that claims passed=True with every required signal
    present is constructible and round-trips."""

    result = AtomicLandingGateResult(**_passing_landing_kwargs())
    assert result.passed is True
    assert result.operational_decision == "go"
    passed, blockers = result.evaluate_go_requires()
    assert passed is True
    assert blockers == []

    # Round-trip.
    dumped = result.model_dump_json()
    reloaded = AtomicLandingGateResult.model_validate_json(dumped)
    assert reloaded.passed is True
    assert reloaded.operational_decision == "go"


def test_atomic_landing_gate_result_passed_true_with_missing_gate_rejected() -> None:
    """A landing record cannot assert passed=True while a required gate is
    missing -- fail-closed."""

    bad_gates = _all_gates_passed_map()
    del bad_gates["operations"]  # remove one required gate
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(required_gate_results=bad_gates))


def test_atomic_landing_gate_result_passed_true_with_failed_gate_rejected() -> None:
    """A failed gate forces passed=False at validation time."""

    bad_gates = _all_gates_passed_map()
    bad_gates["operations"] = "failed"
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(required_gate_results=bad_gates))


def test_atomic_landing_gate_result_passed_true_with_stale_gate_rejected() -> None:
    bad_gates = _all_gates_passed_map()
    bad_gates["operations"] = "stale"
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(required_gate_results=bad_gates))


def test_atomic_landing_gate_result_passed_true_without_ci_matrix_rejected() -> None:
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(ci_matrix_run_id=None))


def test_atomic_landing_gate_result_passed_true_without_metrics_snapshot_rejected() -> None:
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(metrics_snapshot_id=None))


def test_atomic_landing_gate_result_passed_true_without_decided_by_rejected() -> None:
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(decided_by=None))


def test_atomic_landing_gate_result_passed_true_without_decided_at_rejected() -> None:
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(decided_at=None))


def test_atomic_landing_gate_result_passed_true_without_rollback_runbook_rejected() -> None:
    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(**_passing_landing_kwargs(rollback_runbook_id=None))


def test_atomic_landing_gate_result_passed_true_with_forbidden_partial_rejected() -> None:
    """No per-slice control may be used as production authority."""

    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(
            **_passing_landing_kwargs(
                forbidden_partial_controls_enabled=["IRIAI_MERGE_QUEUE_V2"]
            )
        )


def test_atomic_landing_gate_result_passed_true_with_blockers_rejected() -> None:
    """Any caller blocker fails closed."""

    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(
            **_passing_landing_kwargs(blockers=["queue_drain:incomplete"])
        )


def test_atomic_landing_gate_result_passed_true_requires_go_decision() -> None:
    """passed=True without operational_decision='go' is contradictory."""

    with pytest.raises(ValidationError, match="operational_decision"):
        AtomicLandingGateResult(**_passing_landing_kwargs(operational_decision="no_go"))


def test_atomic_landing_gate_result_go_decision_requires_signals() -> None:
    """operational_decision='go' alone with no signals is rejected."""

    with pytest.raises(ValidationError, match="go-requires"):
        AtomicLandingGateResult(
            candidate_id="cand-1",
            candidate_commit="abc123",
            deploy_artifact_id="deploy:42",
            passed=False,
            operational_decision="go",
        )


def test_atomic_landing_gate_result_no_go_helper() -> None:
    """The ``no_go`` classmethod is a fail-closed no-go constructor."""

    result = AtomicLandingGateResult.no_go(
        candidate_id="cand-1",
        candidate_commit="abc123",
        deploy_artifact_id="deploy:42",
        blockers=["workspace_dirty:repo-A"],
    )
    assert result.passed is False
    assert result.operational_decision == "no_go"
    assert result.blockers == ["workspace_dirty:repo-A"]


def test_atomic_landing_gate_result_rejects_empty_identity() -> None:
    for field in ("candidate_id", "candidate_commit", "deploy_artifact_id"):
        kwargs = {
            "candidate_id": "cand-1",
            "candidate_commit": "abc123",
            "deploy_artifact_id": "deploy:42",
        }
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            AtomicLandingGateResult(**kwargs)


def test_atomic_landing_gate_result_invalid_gate_status_rejected() -> None:
    """The required_gate_results values are locked to the doc-12 enum."""

    bad_gates = _all_gates_passed_map()
    bad_gates["operations"] = "unknown"  # type: ignore[assignment]
    with pytest.raises(ValidationError):
        AtomicLandingGateResult(**_passing_landing_kwargs(required_gate_results=bad_gates))


def test_atomic_landing_gate_result_invalid_decision_rejected() -> None:
    """The operational_decision is locked to {'go', 'no_go'}."""

    with pytest.raises(ValidationError):
        AtomicLandingGateResult(
            candidate_id="cand-1",
            candidate_commit="abc123",
            deploy_artifact_id="deploy:42",
            operational_decision="maybe",  # type: ignore[arg-type]
        )


def test_atomic_landing_gate_result_evaluate_go_requires_lists_all_blockers() -> None:
    """A no-go result reports every doc-12 go-requires bullet that fails."""

    result = AtomicLandingGateResult(
        candidate_id="cand-1",
        candidate_commit="abc123",
        deploy_artifact_id="deploy:42",
        passed=False,
        operational_decision="no_go",
        required_gate_results={
            "atomic_enablement": "passed",
            "schema_and_journal": "failed",
            # remaining 8 gates intentionally missing
        },
        forbidden_partial_controls_enabled=["IRIAI_FAILURE_ROUTER_V2"],
        blockers=["workspace_dirty"],
    )
    passed, blockers = result.evaluate_go_requires()
    assert passed is False
    assert "required_gate:schema_and_journal:failed" in blockers
    # Eight missing gates.
    missing_gate_blockers = [b for b in blockers if b.endswith(":missing")]
    # Includes the 8 missing required gates + the 5 non-gate missing.
    assert any("required_gate:operations:missing" in b for b in blockers)
    assert "ci_matrix_run_id:missing" in blockers
    assert "metrics_snapshot_id:missing" in blockers
    assert "decided_by:missing" in blockers
    assert "decided_at:missing" in blockers
    assert "rollback_runbook_id:missing" in blockers
    # Forbidden control + caller blocker.
    assert "forbidden_partial_control:IRIAI_FAILURE_ROUTER_V2" in blockers
    assert "caller_blocker:workspace_dirty" in blockers


# ── WorkflowImprovementMetrics ─────────────────────────────────────────────


def _metrics_kwargs(**override) -> dict[str, object]:
    base = dict(
        feature_id="feature-1",
        candidate_id="cand-1",
        validation_corpus_id="corpus-1",
        retry_cycles_per_task=0.2,
        commit_failures_per_task=0.03,
        stale_projection_count=0,
        alias_or_acl_failures=0,
        checkpoint_safety_regressions=0,
        workflow_drag_hours=0.5,
        tasks_per_hour=10.0,
        operator_required_escalations=0,
        db_rss_regression_pct=2.0,
        postgres_bytes_growth_pct=3.0,
        complexity_adjusted_tasks_per_hour=8.0,
        baseline_retry_cycles_per_task=1.0,
        baseline_commit_failures_per_task=0.1,
        baseline_stale_projection_count=5,
        baseline_workflow_drag_hours=2.0,
        baseline_complexity_adjusted_tasks_per_hour=8.0,
    )
    base.update(override)
    return base


def test_workflow_improvement_metrics_round_trip() -> None:
    metrics = WorkflowImprovementMetrics(**_metrics_kwargs())
    dumped = metrics.model_dump_json()
    reloaded = WorkflowImprovementMetrics.model_validate_json(dumped)
    assert reloaded.retry_cycles_per_task == 0.2
    assert reloaded.baseline_retry_cycles_per_task == 1.0


def test_workflow_improvement_metrics_rejects_empty_identity() -> None:
    for field in ("feature_id", "candidate_id", "validation_corpus_id"):
        kwargs = _metrics_kwargs()
        kwargs[field] = ""
        with pytest.raises(ValidationError):
            WorkflowImprovementMetrics(**kwargs)


def test_workflow_improvement_metrics_rejects_negative_counts() -> None:
    """Every non-negative field rejects negatives."""

    negative_fields = (
        "retry_cycles_per_task",
        "commit_failures_per_task",
        "stale_projection_count",
        "alias_or_acl_failures",
        "checkpoint_safety_regressions",
        "workflow_drag_hours",
        "tasks_per_hour",
        "operator_required_escalations",
        "complexity_adjusted_tasks_per_hour",
        "baseline_retry_cycles_per_task",
        "baseline_commit_failures_per_task",
        "baseline_stale_projection_count",
        "baseline_workflow_drag_hours",
        "baseline_complexity_adjusted_tasks_per_hour",
    )
    for field in negative_fields:
        kwargs = _metrics_kwargs()
        kwargs[field] = -0.1 if "per" in field or "hours" in field or "pct" in field else -1
        with pytest.raises(ValidationError):
            WorkflowImprovementMetrics(**kwargs)


# ── compute_task_complexity_weight ─────────────────────────────────────────


def test_compute_task_complexity_weight_baseline_is_one() -> None:
    """A trivial task is weight 1.0."""

    weight = compute_task_complexity_weight()
    assert weight == 1.0


def test_compute_task_complexity_weight_each_term_contributes() -> None:
    weight = compute_task_complexity_weight(
        backend_repo_count=1,
        cross_repo_flag=False,
        generated_output_flag=False,
        unknown_write_set_flag=False,
        verification_gate_count=0,
    )
    assert weight == pytest.approx(1.25)

    weight = compute_task_complexity_weight(
        cross_repo_flag=True,
    )
    assert weight == pytest.approx(1.25)

    weight = compute_task_complexity_weight(
        verification_gate_count=2,
    )
    assert weight == pytest.approx(1.2)


def test_compute_task_complexity_weight_capped_at_2_5() -> None:
    """Per doc 12: 'capped at 2.5'."""

    weight = compute_task_complexity_weight(
        backend_repo_count=10,
        cross_repo_flag=True,
        generated_output_flag=True,
        unknown_write_set_flag=True,
        verification_gate_count=20,
    )
    assert weight == 2.5


def test_compute_task_complexity_weight_rejects_negative_counts() -> None:
    with pytest.raises(ValueError):
        compute_task_complexity_weight(backend_repo_count=-1)
    with pytest.raises(ValueError):
        compute_task_complexity_weight(verification_gate_count=-1)


# ── MetricsCollector ───────────────────────────────────────────────────────


def _typed_state_kwargs(**override) -> dict[str, object]:
    base = dict(
        completed_task_count=100,
        typed_retry_count=20,
        commit_failure_count=3,
        stale_projection_count=0,
        alias_or_acl_failures=0,
        checkpoint_safety_regressions=0,
        operator_required_escalations=0,
        workflow_drag_seconds_by_class={
            "worktree_alias": 600.0,
            "acl_workability": 0.0,
            "stale_projection": 0.0,
            "commit_hygiene": 1200.0,
            "runtime_provider": 0.0,
            "merge_conflict": 0.0,
            "checkpoint_contradiction": 0.0,
        },
        wall_clock_hours=10.0,
        task_complexity_weight_sum=120.0,
        db_rss_median_bytes=1_000_000_000,
        postgres_bytes=5_000_000_000,
    )
    base.update(override)
    return base


def _legacy_source_kwargs(**override) -> dict[str, object]:
    base = dict(
        completed_task_count=80,
        retry_event_count=50,
        commit_failure_artifact_count=10,
        stale_projection_artifact_count=4,
        workflow_drag_seconds_by_class={},
        wall_clock_hours=12.0,
        task_complexity_weight_sum=100.0,
    )
    base.update(override)
    return base


def _baseline_kwargs(**override) -> dict[str, object]:
    base = dict(
        baseline_label="8ac124d6",
        completed_task_count=50,
        retry_count=50,
        commit_failure_count=10,
        stale_projection_count=10,
        workflow_drag_hours=5.0,
        wall_clock_hours=10.0,
        task_complexity_weight_sum=60.0,
        db_rss_median_bytes=950_000_000,
        postgres_bytes=4_800_000_000,
    )
    base.update(override)
    return base


def test_metrics_collector_collect_round_trip() -> None:
    inputs = MetricsCollectionInputs(
        feature_id="feature-1",
        candidate_id="cand-1",
        validation_corpus_id="corpus-1",
        typed_state=TypedStateMetricsSource(**_typed_state_kwargs()),
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=BaselineMetricsSource(**_baseline_kwargs()),
    )
    collector = MetricsCollector(inputs=inputs)
    metrics = collector.collect()
    assert isinstance(metrics, WorkflowImprovementMetrics)
    assert metrics.feature_id == "feature-1"
    assert metrics.candidate_id == "cand-1"
    assert metrics.validation_corpus_id == "corpus-1"


def test_metrics_collector_retry_cycles_per_task_formula() -> None:
    """retry_cycles_per_task = typed_retry_count / completed_task_count."""

    typed = TypedStateMetricsSource(
        **_typed_state_kwargs(typed_retry_count=25, completed_task_count=100)
    )
    inputs = MetricsCollectionInputs(
        feature_id="f",
        candidate_id="c",
        validation_corpus_id="v",
        typed_state=typed,
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=BaselineMetricsSource(**_baseline_kwargs()),
    )
    metrics = MetricsCollector(inputs=inputs).collect()
    assert metrics.retry_cycles_per_task == pytest.approx(0.25)


def test_metrics_collector_commit_failures_per_task_formula() -> None:
    typed = TypedStateMetricsSource(
        **_typed_state_kwargs(commit_failure_count=5, completed_task_count=100)
    )
    inputs = MetricsCollectionInputs(
        feature_id="f",
        candidate_id="c",
        validation_corpus_id="v",
        typed_state=typed,
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=BaselineMetricsSource(**_baseline_kwargs()),
    )
    metrics = MetricsCollector(inputs=inputs).collect()
    assert metrics.commit_failures_per_task == pytest.approx(0.05)


def test_metrics_collector_workflow_drag_hours_sums_doc_12_classes() -> None:
    """workflow_drag_hours sums seconds_by_class over the 7 doc-12 classes,
    converted to hours."""

    typed = TypedStateMetricsSource(
        **_typed_state_kwargs(
            workflow_drag_seconds_by_class={
                "worktree_alias": 3600.0,  # 1 hour
                "acl_workability": 1800.0,  # 0.5 hour
                "stale_projection": 0.0,
                "commit_hygiene": 7200.0,  # 2 hours
                "runtime_provider": 0.0,
                "merge_conflict": 0.0,
                "checkpoint_contradiction": 0.0,
                # An out-of-scope class -- should NOT be summed.
                "not_a_real_class": 99999.0,
            }
        )
    )
    inputs = MetricsCollectionInputs(
        feature_id="f",
        candidate_id="c",
        validation_corpus_id="v",
        typed_state=typed,
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=BaselineMetricsSource(**_baseline_kwargs()),
    )
    metrics = MetricsCollector(inputs=inputs).collect()
    assert metrics.workflow_drag_hours == pytest.approx(3.5)


def test_metrics_collector_zero_denominator_safe_ratio() -> None:
    """retry_cycles_per_task / commit_failures_per_task return 0.0 when the
    denominator (completed_task_count) is 0."""

    typed = TypedStateMetricsSource(
        **_typed_state_kwargs(
            completed_task_count=0,
            typed_retry_count=10,
            commit_failure_count=5,
            wall_clock_hours=0.0,
            task_complexity_weight_sum=0.0,
        )
    )
    inputs = MetricsCollectionInputs(
        feature_id="f",
        candidate_id="c",
        validation_corpus_id="v",
        typed_state=typed,
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=BaselineMetricsSource(**_baseline_kwargs()),
    )
    metrics = MetricsCollector(inputs=inputs).collect()
    assert metrics.retry_cycles_per_task == 0.0
    assert metrics.commit_failures_per_task == 0.0
    assert metrics.complexity_adjusted_tasks_per_hour == 0.0


def test_metrics_collector_complexity_adjusted_tasks_per_hour_formula() -> None:
    """complexity_adjusted_tasks_per_hour = completed_task_count /
    (task_complexity_weight_sum * wall_clock_hours)."""

    typed = TypedStateMetricsSource(
        **_typed_state_kwargs(
            completed_task_count=120,
            wall_clock_hours=10.0,
            task_complexity_weight_sum=60.0,
        )
    )
    inputs = MetricsCollectionInputs(
        feature_id="f",
        candidate_id="c",
        validation_corpus_id="v",
        typed_state=typed,
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=BaselineMetricsSource(**_baseline_kwargs()),
    )
    metrics = MetricsCollector(inputs=inputs).collect()
    assert metrics.complexity_adjusted_tasks_per_hour == pytest.approx(120.0 / (60.0 * 10.0))


def test_metrics_collector_db_rss_regression_pct_formula() -> None:
    """db_rss_regression_pct = 100 * (current - baseline) / baseline."""

    typed = TypedStateMetricsSource(
        **_typed_state_kwargs(db_rss_median_bytes=1_100_000_000)
    )
    baseline = BaselineMetricsSource(
        **_baseline_kwargs(db_rss_median_bytes=1_000_000_000)
    )
    inputs = MetricsCollectionInputs(
        feature_id="f",
        candidate_id="c",
        validation_corpus_id="v",
        typed_state=typed,
        legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
        baseline_8ac124d6=baseline,
    )
    metrics = MetricsCollector(inputs=inputs).collect()
    assert metrics.db_rss_regression_pct == pytest.approx(10.0)


def test_metrics_collector_baseline_label_must_be_8ac124d6() -> None:
    """Fail-closed: only the doc-00 8ac124d6 baseline is accepted."""

    with pytest.raises(ValidationError, match="8ac124d6"):
        MetricsCollectionInputs(
            feature_id="f",
            candidate_id="c",
            validation_corpus_id="v",
            typed_state=TypedStateMetricsSource(**_typed_state_kwargs()),
            legacy_artifacts=LegacyArtifactMetricsSource(**_legacy_source_kwargs()),
            baseline_8ac124d6=BaselineMetricsSource(
                **_baseline_kwargs(baseline_label="other")
            ),
        )


def test_metrics_collector_does_not_introduce_persistence() -> None:
    """Per the prompt: 'the metrics collector should reuse the typed
    snapshot baseline from Slice 10a, NOT introduce new persistence.'

    Verify the collector is a PURE function: no DB / IO / file / network
    imports.
    """

    source_path = Path(atomic_landing.__file__)
    text = source_path.read_text(encoding="utf-8")
    for forbidden in (
        "import asyncpg",
        "import psycopg",
        "from asyncpg",
        "from psycopg",
        "open(",
        "with open(",
        ".execute(",
        "fetchval(",
        "fetchrow(",
    ):
        assert forbidden not in text, (
            f"atomic_landing.py contains forbidden persistence/IO call: "
            f"{forbidden!r}"
        )


def test_metrics_collector_reuses_existing_summary_primitives_via_source() -> None:
    """Verify that the doc-12 metrics surface re-uses existing summary types
    rather than re-introducing dataclass fields.

    The collector takes :class:`TypedStateMetricsSource` as input -- a
    summary-only type -- and does not directly query the typed snapshot
    store. Callers project from the Slice-10a typed snapshot into this
    summary type.
    """

    source_path = Path(atomic_landing.__file__)
    text = source_path.read_text(encoding="utf-8")
    # The module must not directly import ExecutionControlStore (that would
    # create a persistence dependency).
    assert "from .store import ExecutionControlStore" not in text
    # The module must not import the snapshots Pydantic models directly --
    # the collector is a pure function of its summary inputs (callers
    # project from ControlPlaneSnapshot into TypedStateMetricsSource).
    assert "from ..workflows.develop.execution.snapshots import" not in text


# ── evaluate_metrics_success ───────────────────────────────────────────────


def test_evaluate_metrics_success_passes_with_solid_improvements() -> None:
    """A candidate that beats every doc-12 threshold returns passed=True."""

    metrics = WorkflowImprovementMetrics(**_metrics_kwargs())
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is True
    assert reasons == []


def test_evaluate_metrics_success_fails_on_retry_cycles_regression() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(retry_cycles_per_task=1.0, baseline_retry_cycles_per_task=1.0)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("retry_cycles_per_task" in r for r in reasons)


def test_evaluate_metrics_success_small_baseline_uses_0_25_threshold() -> None:
    """When baseline_retry_cycles_per_task is small, the threshold is 0.25."""

    # 0.3 > 0.25: fail.
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(
            retry_cycles_per_task=0.3,
            baseline_retry_cycles_per_task=0.1,
        )
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("retry_cycles_per_task" in r for r in reasons)

    # 0.20 < 0.25 with same baseline -- pass.
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(
            retry_cycles_per_task=0.20,
            baseline_retry_cycles_per_task=0.1,
        )
    )
    passed, reasons = evaluate_metrics_success(metrics)
    # passes the retry portion (no retry-related reason).
    assert not any("retry_cycles_per_task" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_commit_failures_regression() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(
            commit_failures_per_task=0.5,
            baseline_commit_failures_per_task=0.5,
        )
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("commit_failures_per_task" in r for r in reasons)


def test_evaluate_metrics_success_small_commit_baseline_uses_0_05_threshold() -> None:
    """When baseline_commit_failures_per_task is small, the threshold is 0.05."""

    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(
            commit_failures_per_task=0.06,
            baseline_commit_failures_per_task=0.01,
        )
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert any("commit_failures_per_task" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_stale_projection_regression() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(stale_projection_count=10, baseline_stale_projection_count=10)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("stale_projection_count" in r for r in reasons)


def test_evaluate_metrics_success_small_stale_baseline_uses_1_threshold() -> None:
    """When baseline_stale_projection_count < 3, the threshold is 1."""

    # 2 > 1: fail
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(stale_projection_count=2, baseline_stale_projection_count=1)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert any("stale_projection_count" in r for r in reasons)

    # 1 <= 1: pass the stale check
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(stale_projection_count=1, baseline_stale_projection_count=1)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert not any("stale_projection_count" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_workflow_drag_regression() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(workflow_drag_hours=10.0, baseline_workflow_drag_hours=10.0)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("workflow_drag_hours" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_catph_regression() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(
            complexity_adjusted_tasks_per_hour=5.0,
            baseline_complexity_adjusted_tasks_per_hour=10.0,
        )
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("complexity_adjusted_tasks_per_hour" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_checkpoint_safety_regression() -> None:
    """checkpoint_safety_regressions MUST be 0."""

    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(checkpoint_safety_regressions=1)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("checkpoint_safety_regressions" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_operator_escalation() -> None:
    """operator_required_escalations MUST be 0 for resolvable classes."""

    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(operator_required_escalations=1)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("operator_required_escalations" in r for r in reasons)


def test_evaluate_metrics_success_fails_on_alias_or_acl_failure() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(alias_or_acl_failures=1)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("alias_or_acl_failures" in r for r in reasons)


def test_evaluate_metrics_success_fails_at_or_above_10_pct_db_regression() -> None:
    """Doc 12: 'must stay under 10%'."""

    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(db_rss_regression_pct=10.0)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("db_rss_regression_pct" in r for r in reasons)


def test_evaluate_metrics_success_fails_at_or_above_10_pct_pg_growth() -> None:
    metrics = WorkflowImprovementMetrics(
        **_metrics_kwargs(postgres_bytes_growth_pct=10.0)
    )
    passed, reasons = evaluate_metrics_success(metrics)
    assert passed is False
    assert any("postgres_bytes_growth_pct" in r for r in reasons)


# ── module-level structural pins ────────────────────────────────────────────


def test_atomic_landing_module_has_no_back_import_to_implementation() -> None:
    """The new module must not back-import from
    ``workflows.develop.phases.implementation`` (compatibility flows point
    IN, never OUT). Mirrors the Slice 11/12a-1 back-import guards."""

    source_path = Path(atomic_landing.__file__)
    text = source_path.read_text(encoding="utf-8")
    forbidden_phrases = (
        "from iriai_build_v2.workflows.develop.phases.implementation",
        "from ..workflows.develop.phases.implementation",
        "from ...workflows.develop.phases.implementation",
        "import iriai_build_v2.workflows.develop.phases.implementation",
    )
    for phrase in forbidden_phrases:
        assert phrase not in text, (
            f"atomic_landing.py contains forbidden back-import: {phrase!r}"
        )


def test_atomic_landing_module_does_not_import_control_plane_runtime() -> None:
    """Per doc 12: 'This slice defines release-control interfaces, NOT
    executor runtime interfaces.' The release-control module must not
    import the runtime quiesce primitives or the ExecutionControlPlane
    facade -- they are distinct concerns.
    """

    source_path = Path(atomic_landing.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert "from ..workflows.develop.execution.control_plane import" not in text
    assert "import iriai_build_v2.workflows.develop.execution.control_plane" not in text


def test_atomic_landing_module_all_export_is_complete() -> None:
    """Every name exported from the module's ``__all__`` is bound at the
    module level."""

    for name in atomic_landing.__all__:
        assert hasattr(atomic_landing, name), (
            f"name {name!r} is in __all__ but not bound at module level"
        )


def test_atomic_landing_module_exports_doc_12_typed_contracts() -> None:
    """The doc-12 release-control Pydantic contracts are exported."""

    assert "AtomicLandingGateResult" in atomic_landing.__all__
    assert "WorkflowImprovementMetrics" in atomic_landing.__all__
    assert "ReadinessGateEvidence" in atomic_landing.__all__
    assert "ReadinessGateEvidenceSurface" in atomic_landing.__all__
    assert "CiTestMatrixRow" in atomic_landing.__all__
    assert "CiTestMatrixResult" in atomic_landing.__all__
    assert "MetricsCollector" in atomic_landing.__all__


def test_atomic_landing_module_does_not_import_phase_modules() -> None:
    """Per the prompt hard rule: modules MUST NOT import from
    ``phases/implementation.py``."""

    source_path = Path(atomic_landing.__file__)
    text = source_path.read_text(encoding="utf-8")
    for forbidden in (
        "from iriai_build_v2.workflows.develop.phases",
        "from ..workflows.develop.phases",
        "from ...workflows.develop.phases",
    ):
        assert forbidden not in text, (
            f"atomic_landing.py imports from forbidden phase module: {forbidden!r}"
        )


def test_atomic_landing_co_locates_with_slice_10f_startup_module() -> None:
    """The new module sits next to Slice 10f's ``execution_control/startup.py``
    (the related typed-control-plane startup guard surface)."""

    landing_path = Path(atomic_landing.__file__)
    startup_path = landing_path.parent / "startup.py"
    assert startup_path.is_file(), (
        f"Slice 10f startup guard module should exist at {startup_path}"
    )


# ── slice-12b/12c/12d boundary pins (typed contracts that must be present) ──


def test_in_flight_adoption_record_is_defined() -> None:
    """Doc 12 Section "Proposed Interfaces/Types" defines
    ``InFlightAdoptionRecord``. Slice 12d LANDED the typed contract in
    ``atomic_landing.py`` (alongside ``AtomicLandingGateResult`` +
    ``WorkflowImprovementMetrics``).

    Originally (Slice 12b through Slice 12c) this test pinned the
    deferral boundary by asserting ``InFlightAdoptionRecord`` was NOT
    yet defined; Slice 12d FLIPPED it to an active presence + shape
    assertion (so the test continues to enforce the contract -- Slice
    12b's defer-boundary becomes Slice 12d's live presence check).
    """

    # Presence on the module + in __all__.
    assert hasattr(atomic_landing, "InFlightAdoptionRecord")
    assert "InFlightAdoptionRecord" in atomic_landing.__all__

    # Shape: the doc-12 verbatim field list (lines 126-141) PLUS the
    # Slice-12d operator-context fields the brief enumerates.
    cls = atomic_landing.InFlightAdoptionRecord
    fields = set(cls.model_fields.keys())
    # Doc-12 verbatim fields:
    assert "feature_id" in fields
    assert "candidate_commit" in fields
    assert "deploy_artifact_id" in fields
    assert "legacy_root_dag_artifact_id" in fields
    assert "legacy_root_dag_sha256" in fields
    assert "completed_checkpoint_range" in fields
    assert "next_effective_group_idx" in fields
    assert "active_regroup_artifact_ids" in fields
    assert "workspace_snapshot_ids" in fields
    assert "projection_digest" in fields
    assert "adoption_marker_artifact_id" in fields
    assert "adopted_at" in fields
    assert "rollback_disposition" in fields
    assert "blockers" in fields
    # Slice-12d additive operator-context fields:
    assert "feature_state_at_adoption" in fields
    assert "adopted_by" in fields
    assert "landing_gate_result_id" in fields
    assert "pre_adoption_baseline" in fields
    assert "notes" in fields


def test_iriai_exec_control_plane_enabled_not_yet_owned() -> None:
    """Doc 12: 'IRIAI_EXEC_CONTROL_PLANE_ENABLED is the only product-
    authoritative production switch'. Slice 12c owns the env-flag + startup
    guard wiring; Slice 12b only defines the typed contract surface.

    The flag NAME is the single string that names the production switch,
    but no live ``os.environ.get`` of that name should appear in this
    module -- the runtime read is the Slice-12c entrypoint.
    """

    source_path = Path(atomic_landing.__file__)
    text = source_path.read_text(encoding="utf-8")
    assert (
        "os.environ.get('IRIAI_EXEC_CONTROL_PLANE_ENABLED'" not in text
        and 'os.environ.get("IRIAI_EXEC_CONTROL_PLANE_ENABLED"' not in text
    ), "Slice 12b must not consume IRIAI_EXEC_CONTROL_PLANE_ENABLED -- Slice 12c owns the env-flag wiring"


# ── Slice-12d InFlightAdoptionRecord Pydantic validation ────────────────────


def _adoption_record_kwargs(**overrides: object) -> dict[str, object]:
    """Helper -- a minimal valid InFlightAdoptionRecord kwargs dict."""

    base: dict[str, object] = {
        "feature_id": "feat0001",
        "candidate_commit": "abc123deadbeef",
        "deploy_artifact_id": "artifact-2026-05-23",
        "legacy_root_dag_artifact_id": 4242,
        "legacy_root_dag_sha256": "f" * 64,
        "completed_checkpoint_range": (0, 3),
        "next_effective_group_idx": 4,
        "active_regroup_artifact_ids": [1001, 1002],
        "workspace_snapshot_ids": [2001],
        "projection_digest": "p" * 64,
        "adopted_at": datetime(2026, 5, 23, 17, 0, tzinfo=timezone.utc),
    }
    base.update(overrides)
    return base


def test_in_flight_adoption_record_happy_path() -> None:
    """A minimally-populated record validates cleanly with the doc-12
    default rollback disposition (the conservative option)."""

    record = InFlightAdoptionRecord(**_adoption_record_kwargs())
    assert record.status == "adopted"
    assert record.feature_id == "feat0001"
    assert record.candidate_commit == "abc123deadbeef"
    assert record.deploy_artifact_id == "artifact-2026-05-23"
    assert record.completed_checkpoint_range == (0, 3)
    assert record.next_effective_group_idx == 4
    # Fail-closed default: the conservative rollback disposition.
    assert record.rollback_disposition == "legacy_resume_before_next_group"
    # Operator-context defaults: empty.
    assert record.feature_state_at_adoption == ""
    assert record.adopted_by == ""
    assert record.landing_gate_result_id == ""
    assert record.pre_adoption_baseline == {}
    assert record.notes == ""
    assert record.blockers == []
    assert record.adoption_marker_artifact_id is None


def test_in_flight_adoption_record_serializable_round_trip() -> None:
    """The record round-trips through ``model_dump_json`` /
    ``model_validate_json`` -- this is the artifact-body shape the adoption
    marker writes + the resume guard reads."""

    record = InFlightAdoptionRecord(
        **_adoption_record_kwargs(
            adopted_by="operator-alice",
            landing_gate_result_id="alg-result-42",
            notes="adopted at first safe boundary after dag-group:3 commit-proof",
            pre_adoption_baseline={"completed_groups": 3, "queue_depth": 0},
        )
    )
    raw = record.model_dump_json()
    rebuilt = InFlightAdoptionRecord.model_validate_json(raw)
    assert rebuilt == record
    assert json.loads(raw)["status"] == "adopted"
    assert rebuilt.adopted_by == "operator-alice"
    assert rebuilt.landing_gate_result_id == "alg-result-42"
    assert rebuilt.pre_adoption_baseline == {
        "completed_groups": 3,
        "queue_depth": 0,
    }


@pytest.mark.parametrize(
    "field",
    [
        "feature_id",
        "candidate_commit",
        "deploy_artifact_id",
        "legacy_root_dag_sha256",
        "projection_digest",
    ],
)
def test_in_flight_adoption_record_rejects_blank_strings(field: str) -> None:
    """The non-empty validator fires on every string-required field."""

    kwargs = _adoption_record_kwargs(**{field: "   "})
    with pytest.raises(ValidationError):
        InFlightAdoptionRecord(**kwargs)


def test_in_flight_adoption_record_rejects_inverted_checkpoint_range() -> None:
    """``completed_checkpoint_range`` with end < start is rejected (the
    model_validator enforces the closed-range invariant)."""

    kwargs = _adoption_record_kwargs(completed_checkpoint_range=(5, 2))
    with pytest.raises(ValidationError) as excinfo:
        InFlightAdoptionRecord(**kwargs)
    message = str(excinfo.value)
    assert "completed_checkpoint_range" in message
    assert "end" in message and "start" in message


def test_in_flight_adoption_record_rejects_negative_checkpoint_endpoints() -> None:
    """``completed_checkpoint_range`` entries must be >= 0 (the
    model_validator enforces the non-negative invariant)."""

    kwargs = _adoption_record_kwargs(completed_checkpoint_range=(-1, 3))
    with pytest.raises(ValidationError):
        InFlightAdoptionRecord(**kwargs)


def test_in_flight_adoption_record_rejects_negative_marker_id() -> None:
    """``adoption_marker_artifact_id`` must be >= 0 when set."""

    kwargs = _adoption_record_kwargs(adoption_marker_artifact_id=-5)
    with pytest.raises(ValidationError):
        InFlightAdoptionRecord(**kwargs)


def test_in_flight_adoption_record_accepts_marker_id() -> None:
    """``adoption_marker_artifact_id`` accepts a non-negative int."""

    record = InFlightAdoptionRecord(
        **_adoption_record_kwargs(adoption_marker_artifact_id=99887)
    )
    assert record.adoption_marker_artifact_id == 99887


def test_in_flight_adoption_record_rejects_invalid_rollback_disposition() -> None:
    """``rollback_disposition`` is a doc-12 Literal -- only the two
    documented values are valid."""

    kwargs = _adoption_record_kwargs(rollback_disposition="never_rollback")
    with pytest.raises(ValidationError):
        InFlightAdoptionRecord(**kwargs)


def test_in_flight_adoption_record_accepts_both_rollback_dispositions() -> None:
    """Both doc-12 rollback dispositions are accepted."""

    a = InFlightAdoptionRecord(
        **_adoption_record_kwargs(
            rollback_disposition="legacy_resume_before_next_group"
        )
    )
    assert a.rollback_disposition == "legacy_resume_before_next_group"

    b = InFlightAdoptionRecord(
        **_adoption_record_kwargs(
            rollback_disposition="control_plane_only_after_next_attempt"
        )
    )
    assert b.rollback_disposition == "control_plane_only_after_next_attempt"


def test_in_flight_adoption_record_rejects_negative_legacy_dag_artifact_id() -> None:
    """``legacy_root_dag_artifact_id`` must be >= 0."""

    kwargs = _adoption_record_kwargs(legacy_root_dag_artifact_id=-1)
    with pytest.raises(ValidationError):
        InFlightAdoptionRecord(**kwargs)


def test_in_flight_adoption_record_rejects_negative_next_group_idx() -> None:
    """``next_effective_group_idx`` must be >= 0."""

    kwargs = _adoption_record_kwargs(next_effective_group_idx=-1)
    with pytest.raises(ValidationError):
        InFlightAdoptionRecord(**kwargs)
