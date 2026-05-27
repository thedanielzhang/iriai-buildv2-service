from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import get_args

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.governance_acceptance import (
    REQUIRED_ACCEPTANCE_SLICE_IDS,
    REQUIRED_GOVERNANCE_SURFACES,
    GovernanceAcceptanceCollector,
    GovernanceAcceptanceInputs,
    GovernanceAcceptanceResult,
    GovernanceAdoptionRecord,
    GovernanceRequiredTestCommand,
    GovernanceSliceImplementationEvidence,
    GovernanceSurface,
    build_governance_adoption_record,
    governance_acceptance_artifact_refs,
    plan_governance_adoption_rollback,
)
from iriai_build_v2.workflows.develop.governance.models import (
    ImplementationArtifactAnchor,
)


def _journal_anchor(slice_id: str = "20-governance-acceptance") -> ImplementationArtifactAnchor:
    return ImplementationArtifactAnchor(
        slice_id=slice_id,
        journal_path="docs/execution-control-plane/implementation-journal.md",
        line_start=1,
        decision_log_line=1,
        event="accepted",
        accepted=True,
        open_findings=[],
    )


def _slice_evidence(
    slice_id: str,
    **overrides: object,
) -> GovernanceSliceImplementationEvidence:
    anchor = _journal_anchor(slice_id)
    values: dict[str, object] = {
        "slice_id": slice_id,
        "accepted": True,
        "journal_refs": [anchor],
        "decision_log_refs": [anchor],
        "reviewer_dispatch_refs": [anchor],
        "test_output_refs": [anchor],
        "accepted_deviations_reviewed": True,
        "unresolved_review_findings": [],
    }
    values.update(overrides)
    return GovernanceSliceImplementationEvidence(**values)


def _required_test(
    command: str = "tests/test_execution_control_governance_acceptance.py",
    **overrides: object,
) -> GovernanceRequiredTestCommand:
    values: dict[str, object] = {
        "command": command,
        "passed": True,
        "exact_required_command": True,
        "required_command": None,
        "accepted_deviation_ref": None,
    }
    values.update(overrides)
    return GovernanceRequiredTestCommand(**values)


def _passing_inputs(**overrides: object) -> GovernanceAcceptanceInputs:
    values: dict[str, object] = {
        "candidate_id": "slice20-candidate",
        "candidate_commit": "abc1234",
        "prerequisite_control_plane_landing_id": "slice00-12-landing",
        "slices_00_12_complete": True,
        "required_13a_remediation_complete": True,
        "thirteen_a_all_steps_satisfied": True,
        "thirteen_a_authority_boundary_preserved": True,
        "thirteen_a_tests_green": True,
        "required_19a_reassessment_complete": True,
        "governance_slices_13_19_complete": True,
        "slice_evidence": [
            _slice_evidence(slice_id)
            for slice_id in REQUIRED_ACCEPTANCE_SLICE_IDS
        ],
        "evidence_model_result": "passed",
        "provenance_result": "passed",
        "metrics_result": "passed",
        "findings_result": "passed",
        "recommendation_result": "passed",
        "replay_result": "passed",
        "reporting_result": "passed",
        "implementation_journal_audit_result": "passed",
        "implementation_journal_audit_refs": [_journal_anchor()],
        "missing_journal_items": [],
        "unresolved_review_findings": [],
        "required_tests": ["tests/test_execution_control_governance_acceptance.py"],
        "required_test_results": [_required_test()],
        "required_tests_passed": True,
        "recommendations_have_mutation_authority": False,
        "bounded_read_body_scan_detected": False,
        "replay_corpus_complete": True,
        "replay_feature_ids": ["8ac124d6"],
        "active_feature_mutated": False,
        "reporting_surface_available": True,
        "task_execute_agent_context_enabled": False,
        "read_only_surfaces_available": list(REQUIRED_GOVERNANCE_SURFACES),
    }
    values.update(overrides)
    return GovernanceAcceptanceInputs(**values)


def _accepted_result() -> GovernanceAcceptanceResult:
    return GovernanceAcceptanceCollector().evaluate(_passing_inputs())


def test_acceptance_result_shape_matches_doc_20() -> None:
    result = _accepted_result()

    assert result.candidate_id == "slice20-candidate"
    assert result.candidate_commit == "abc1234"
    assert result.passed is True
    assert result.prerequisite_control_plane_landing_id == "slice00-12-landing"
    assert result.evidence_model_result == "passed"
    assert result.provenance_result == "passed"
    assert result.metrics_result == "passed"
    assert result.findings_result == "passed"
    assert result.recommendation_result == "passed"
    assert result.replay_result == "passed"
    assert result.reporting_result == "passed"
    assert result.implementation_journal_audit_result == "passed"
    assert result.implementation_journal_audit_refs == [_journal_anchor()]
    assert result.missing_journal_items == []
    assert result.unresolved_review_findings == []
    assert result.required_tests == [
        "tests/test_execution_control_governance_acceptance.py"
    ]
    assert result.blockers == []


@pytest.mark.parametrize(
    ("field_name", "expected_blocker"),
    [
        ("evidence_model_result", "evidence_model_result_failed"),
        ("provenance_result", "provenance_result_failed"),
        ("metrics_result", "metrics_result_failed"),
        ("findings_result", "findings_result_failed"),
        ("recommendation_result", "recommendation_result_failed"),
        ("replay_result", "replay_result_failed"),
        ("reporting_result", "reporting_result_failed"),
        (
            "implementation_journal_audit_result",
            "implementation_journal_audit_result_failed",
        ),
    ],
)
def test_acceptance_fails_closed_when_any_axis_fails(
    field_name: str, expected_blocker: str
) -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(**{field_name: "failed"})
    )

    assert result.passed is False
    assert expected_blocker in result.blockers


def test_acceptance_fails_when_journal_section_missing() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(missing_journal_items=["Slice 18 acceptance note"])
    )

    assert result.passed is False
    assert "missing_journal_item:Slice 18 acceptance note" in result.blockers


def test_acceptance_fails_without_journal_audit_refs() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(implementation_journal_audit_refs=[])
    )

    assert result.passed is False
    assert "implementation_journal_audit_refs_missing" in result.blockers


def test_acceptance_fails_with_unresolved_review_findings() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(unresolved_review_findings=["P2 bounded-read regression"])
    )

    assert result.passed is False
    assert "unresolved_review_finding:P2 bounded-read regression" in result.blockers


def test_acceptance_fails_with_namespaced_unresolved_p1_p2_findings() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(unresolved_review_findings=["19A-P2-007", "19A-P1-001"])
    )

    assert result.passed is False
    assert "unresolved_review_finding:19A-P2-007" in result.blockers
    assert "unresolved_review_finding:19A-P1-001" in result.blockers


def test_acceptance_fails_when_required_tests_not_green() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(required_tests_passed=False)
    )

    assert result.passed is False
    assert "required_tests_not_passed" in result.blockers


def test_acceptance_fails_when_required_test_command_is_missing() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(required_tests=[], required_test_results=[])
    )

    assert result.passed is False
    assert "required_tests_missing" in result.blockers


def test_acceptance_fails_when_required_test_command_fails() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(required_test_results=[_required_test(passed=False)])
    )

    assert result.passed is False
    assert (
        "required_test_failed:tests/test_execution_control_governance_acceptance.py"
        in result.blockers
    )


def test_acceptance_fails_for_semantic_alias_without_accepted_deviation() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(
            required_test_results=[
                _required_test(
                    command="pytest governance acceptance shard",
                    exact_required_command=False,
                )
            ]
        )
    )

    assert result.passed is False
    assert (
        "required_test_non_exact_without_deviation:"
        "pytest governance acceptance shard"
        in result.blockers
    )


def test_acceptance_allows_reviewed_required_test_deviation() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(
            required_tests=["python -m pytest -q"],
            required_test_results=[
                _required_test(
                    command="pytest governance acceptance shard",
                    exact_required_command=False,
                    required_command="python -m pytest -q",
                    accepted_deviation_ref="review:accepted-test-deviation:slice20",
                )
            ],
        )
    )

    assert result.passed is True
    assert result.required_tests == ["python -m pytest -q"]


def test_non_exact_required_test_deviation_must_be_review_ref() -> None:
    with pytest.raises(ValidationError):
        _required_test(
            command="pytest governance acceptance shard",
            exact_required_command=False,
            required_command="python -m pytest -q",
            accepted_deviation_ref="not-a-review-ref",
        )


def test_required_test_results_must_cover_required_command_list() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(
            required_tests=["python -m pytest -q"],
            required_test_results=[_required_test(command="pytest unrelated")],
        )
    )

    assert result.passed is False
    assert "required_test_result_missing:python -m pytest -q" in result.blockers


def test_non_exact_required_test_needs_covered_command() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(
            required_tests=["python -m pytest -q"],
            required_test_results=[
                _required_test(
                    command="pytest governance acceptance shard",
                    exact_required_command=False,
                    accepted_deviation_ref="review:accepted-test-deviation:slice20",
                )
            ],
        )
    )

    assert result.passed is False
    assert (
        "required_test_non_exact_without_required_command:"
        "pytest governance acceptance shard"
        in result.blockers
    )


def test_acceptance_fails_when_recommendation_has_mutation_authority() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(recommendations_have_mutation_authority=True)
    )

    assert result.passed is False
    assert "recommendation_mutation_authority_detected" in result.blockers


def test_acceptance_fails_when_bounded_read_tests_detect_body_scans() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(bounded_read_body_scan_detected=True)
    )

    assert result.passed is False
    assert "unbounded_body_scan_detected" in result.blockers


def test_acceptance_fails_when_replay_corpus_is_incomplete() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(replay_corpus_complete=False)
    )

    assert result.passed is False
    assert "replay_corpus_incomplete" in result.blockers


def test_acceptance_fails_when_replay_missing_active_feature_provenance() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(replay_feature_ids=["other-feature"])
    )

    assert result.passed is False
    assert "replay_missing_feature:8ac124d6" in result.blockers


def test_acceptance_fails_when_active_feature_was_mutated() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(active_feature_mutated=True)
    )

    assert result.passed is False
    assert "active_feature_mutated:8ac124d6" in result.blockers


def test_acceptance_fails_when_reporting_surface_is_unavailable() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(reporting_surface_available=False)
    )

    assert result.passed is False
    assert "reporting_surface_unavailable" in result.blockers


def test_task_execute_agent_context_pre_slice_21_blocks_acceptance() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(task_execute_agent_context_enabled=True)
    )

    assert result.passed is False
    assert "task_execute_agent_context_pre_slice_21_enabled" in result.blockers


def test_control_plane_and_governance_prerequisites_fail_closed() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(
            slices_00_12_complete=False,
            governance_slices_13_19_complete=False,
        )
    )

    assert result.passed is False
    assert "control_plane_landing_incomplete" in result.blockers
    assert "governance_slices_13_19_incomplete" in result.blockers


def test_thirteen_a_preconditions_fail_closed() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(
            required_13a_remediation_complete=False,
            thirteen_a_all_steps_satisfied=False,
            thirteen_a_authority_boundary_preserved=False,
            thirteen_a_tests_green=False,
        )
    )

    assert result.passed is False
    assert "required_13a_remediation_incomplete" in result.blockers
    assert "slice_13a_steps_not_satisfied" in result.blockers
    assert "slice_13a_authority_boundary_reopened" in result.blockers
    assert "slice_13a_tests_not_green" in result.blockers


def test_acceptance_fails_when_19a_reassessment_is_incomplete() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(required_19a_reassessment_complete=False)
    )

    assert result.passed is False
    assert "slice_19a_reassessment_incomplete" in result.blockers


def test_acceptance_fails_when_any_required_slice_evidence_is_missing() -> None:
    evidence = [
        _slice_evidence(slice_id)
        for slice_id in REQUIRED_ACCEPTANCE_SLICE_IDS
        if slice_id != "18"
    ]

    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(slice_evidence=evidence)
    )

    assert result.passed is False
    assert "missing_slice_evidence:18" in result.blockers


def test_acceptance_fails_when_required_slice_lacks_journal_review_or_tests() -> None:
    evidence = [
        _slice_evidence(
            "16",
            journal_refs=[],
            decision_log_refs=[],
            reviewer_dispatch_refs=[],
            test_output_refs=[],
            accepted_deviations_reviewed=False,
            unresolved_review_findings=["P2-16-1", "P3-16-2"],
        )
        if slice_id == "16"
        else _slice_evidence(slice_id)
        for slice_id in REQUIRED_ACCEPTANCE_SLICE_IDS
    ]

    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(slice_evidence=evidence)
    )

    assert result.passed is False
    assert "missing_slice_journal_refs:16" in result.blockers
    assert "missing_slice_decision_log_refs:16" in result.blockers
    assert "missing_slice_reviewer_dispatch_refs:16" in result.blockers
    assert "missing_slice_test_output_refs:16" in result.blockers
    assert "accepted_deviation_review_missing:16" in result.blockers
    assert "unresolved_review_finding:P2-16-1" in result.blockers
    assert "unresolved_review_finding:P3-16-2" not in result.blockers


def test_acceptance_fails_when_any_required_surface_is_unavailable() -> None:
    result = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(read_only_surfaces_available=["dashboard"])
    )

    assert result.passed is False
    assert "surface_unavailable:new_feature_analysis" in result.blockers
    assert "surface_unavailable:agent_context" in result.blockers
    assert "surface_unavailable:supervisor_digest" in result.blockers
    assert "surface_unavailable:cli_reporting" in result.blockers


@pytest.mark.parametrize(
    "field_name",
    [
        "required_19a_reassessment_complete",
        "recommendations_have_mutation_authority",
        "bounded_read_body_scan_detected",
        "replay_corpus_complete",
        "replay_feature_ids",
        "active_feature_mutated",
        "reporting_surface_available",
        "task_execute_agent_context_enabled",
        "read_only_surfaces_available",
    ],
)
def test_acceptance_inputs_require_explicit_gate_evidence(field_name: str) -> None:
    values = _passing_inputs().model_dump()
    values.pop(field_name)

    with pytest.raises(ValidationError):
        GovernanceAcceptanceInputs(**values)


def test_adoption_record_enables_complete_required_surfaces_read_only() -> None:
    adopted_at = datetime(2026, 5, 26, tzinfo=timezone.utc)

    record = build_governance_adoption_record(
        _accepted_result(),
        adopted_at=adopted_at,
        enabled_surfaces=list(reversed(REQUIRED_GOVERNANCE_SURFACES)),
        rollback_disposition="disable read-only governance surfaces",
    )

    assert record is not None
    assert record.candidate_id == "slice20-candidate"
    assert record.adopted_at == adopted_at
    assert record.all_read_only_surfaces_enabled is True
    assert tuple(record.enabled_surfaces) == REQUIRED_GOVERNANCE_SURFACES
    assert record.runtime_policy_mutation_allowed == "never"


def test_adoption_record_is_not_written_for_failed_acceptance() -> None:
    failed = GovernanceAcceptanceCollector().evaluate(
        _passing_inputs(missing_journal_items=["Slice 15 reviewer note"])
    )

    record = build_governance_adoption_record(
        failed,
        adopted_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        enabled_surfaces=REQUIRED_GOVERNANCE_SURFACES,
        rollback_disposition="disable read-only governance surfaces",
    )

    assert record is None


def test_adoption_record_requires_complete_required_surface_set() -> None:
    adopted_at = datetime(2026, 5, 26, tzinfo=timezone.utc)
    missing_cli = REQUIRED_GOVERNANCE_SURFACES[:-1]

    record = build_governance_adoption_record(
        _accepted_result(),
        adopted_at=adopted_at,
        enabled_surfaces=missing_cli,
        rollback_disposition="disable read-only governance surfaces",
    )

    assert record is None
    with pytest.raises(ValidationError):
        GovernanceAdoptionRecord(
            candidate_id="slice20-candidate",
            adopted_at=adopted_at,
            all_read_only_surfaces_enabled=True,
            enabled_surfaces=list(missing_cli),
            runtime_policy_mutation_allowed="never",
            rollback_disposition="disable read-only governance surfaces",
        )


def test_adoption_record_rejects_unknown_or_task_execute_surface() -> None:
    record = build_governance_adoption_record(
        _accepted_result(),
        adopted_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        enabled_surfaces=[
            *REQUIRED_GOVERNANCE_SURFACES,
            "task_execute_agent_context",  # type: ignore[list-item]
        ],
        rollback_disposition="disable read-only governance surfaces",
    )

    assert record is None


def test_adoption_record_rejects_partial_adoption_flag() -> None:
    with pytest.raises(ValidationError):
        GovernanceAdoptionRecord(
            candidate_id="slice20-candidate",
            adopted_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
            all_read_only_surfaces_enabled=False,
            enabled_surfaces=list(REQUIRED_GOVERNANCE_SURFACES),
            runtime_policy_mutation_allowed="never",
            rollback_disposition="disable read-only governance surfaces",
        )


def test_rollback_disables_surfaces_without_mutating_execution_state() -> None:
    record = build_governance_adoption_record(
        _accepted_result(),
        adopted_at=datetime(2026, 5, 26, tzinfo=timezone.utc),
        enabled_surfaces=REQUIRED_GOVERNANCE_SURFACES,
        rollback_disposition="disable read-only governance surfaces",
    )
    assert record is not None

    rollback = plan_governance_adoption_rollback(record)

    assert tuple(rollback.disabled_surfaces) == REQUIRED_GOVERNANCE_SURFACES
    assert rollback.runtime_policy_mutation_allowed == "never"
    assert rollback.execution_state_mutated is False
    assert rollback.audit_history_preserved is True
    assert rollback.review_artifacts_preserved is True


def test_artifact_refs_are_review_refs_only() -> None:
    refs = governance_acceptance_artifact_refs("slice20-candidate")

    assert refs.acceptance == "review:governance-acceptance:slice20-candidate"
    assert refs.journal_audit == "review:governance-journal-audit:slice20-candidate"
    assert refs.replay_corpus == "review:governance-replay-corpus:slice20-candidate"
    assert refs.adoption == "review:governance-adoption:slice20-candidate"


def test_governance_surface_literal_matches_source_doc() -> None:
    assert get_args(GovernanceSurface) == REQUIRED_GOVERNANCE_SURFACES


@pytest.mark.parametrize(
    ("model_type", "kwargs"),
    [
        (
            GovernanceAcceptanceInputs,
            {
                **_passing_inputs().model_dump(),
                "unexpected_field": "nope",
            },
        ),
        (
            GovernanceAcceptanceResult,
            {
                **_accepted_result().model_dump(),
                "unexpected_field": "nope",
            },
        ),
        (
            GovernanceAdoptionRecord,
            {
                "candidate_id": "slice20-candidate",
                "adopted_at": datetime(2026, 5, 26, tzinfo=timezone.utc),
                "all_read_only_surfaces_enabled": True,
                "enabled_surfaces": list(REQUIRED_GOVERNANCE_SURFACES),
                "runtime_policy_mutation_allowed": "never",
                "rollback_disposition": "disable read-only governance surfaces",
                "unexpected_field": "nope",
            },
        ),
    ],
)
def test_models_forbid_extra_fields(model_type: type, kwargs: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        model_type(**kwargs)


def test_passed_result_rejects_failed_axis_or_missing_required_evidence() -> None:
    with pytest.raises(ValidationError):
        GovernanceAcceptanceResult(
            **{
                **_accepted_result().model_dump(),
                "metrics_result": "failed",
            }
        )
    with pytest.raises(ValidationError):
        GovernanceAcceptanceResult(
            **{
                **_accepted_result().model_dump(),
                "implementation_journal_audit_refs": [],
            }
        )


def test_public_surface_exposes_no_mutation_authority_methods() -> None:
    forbidden_prefixes = (
        "activate",
        "approve",
        "checkpoint",
        "delete",
        "execute",
        "insert",
        "merge",
        "migrate",
        "mutate",
        "persist",
        "rewrite",
        "save",
        "update",
        "write",
    )
    public_methods = [
        name
        for name, _ in inspect.getmembers(GovernanceAcceptanceCollector, inspect.isfunction)
        if not name.startswith("_")
    ]

    assert public_methods == ["evaluate"]
    assert not any(
        method_name.startswith(forbidden_prefixes) for method_name in public_methods
    )
