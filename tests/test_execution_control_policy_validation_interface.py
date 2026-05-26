"""Slice 17 third sub-slice -- unit tests for the per-consumer policy
validation interface at
``execution_control/policy_validation_interface.py``.

Covers the doc-17:170-171 step 3 typed-shape consumer + per-consumer
validation rule wiring:

* :class:`ValidationViolation` typed BaseModel (extra-forbid;
  consumer Literal range; evidence_refs default).
* :class:`ValidationResult` typed BaseModel (extra-forbid;
  consumer Literal range; violations + is_valid + validated_at).
* :class:`PolicyValidationInterface.validate_recommendation` -- the
  dispatch method:

  * Per-consumer happy-path validation (one test per consumer; 6
    tests).
  * Per-consumer rejection-path validation (one test per consumer
    rule per doc-17:208-210; 6+ tests).
  * Cross-consumer artifact-type mismatch detection.
  * Validator NEVER raises (fail-closed; typed gap projection on
    internal failure per ``feedback_no_silent_degradation``).
  * Validator GRANTS NO ACTIVATION AUTHORITY (structural test that
    the validator does NOT import any consumer-side module).

* :func:`validate_recommendation` module-level convenience function.
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :class:`GovernanceEvidenceRef` + Slice 17 1st sub-slice
  :class:`GovernancePolicyRecommendation` /
  :class:`PolicyConsumer` / per-consumer ``*PolicyArtifact``
  BaseModels.
* Failure-router 4-add-point validation
  (``policy_validation_failed`` registered under EXISTING
  ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` NON-blocking RouteAction; mirrors
  Slice 16 2nd/3rd-A/3rd-B/4th + Slice 17 2nd sub-slice precedent).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 1st + 2nd sub-slice modules + tests remain byte-
identical.
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

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
from iriai_build_v2.execution_control.policy_validation_interface import (
    POLICY_VALIDATION_FAILURE_ID,
    PolicyValidationInterface,
    ValidationResult,
    ValidationViolation,
    validate_recommendation,
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


def _recommendation(
    *,
    consumer: PolicyConsumer,
    artifact: (
        SchedulerPolicyArtifact
        | FailureRouterPolicyArtifact
        | SupervisorPolicyArtifact
        | DashboardPolicyArtifact
        | PlanningPolicyArtifact
        | MergeQueuePolicyArtifact
    ),
    **overrides: object,
) -> GovernancePolicyRecommendation:
    """Construct a fully-specified :class:`GovernancePolicyRecommendation`."""

    base: dict[str, object] = dict(
        idempotency_key="sha256:rec-1",
        recommendation_id=f"recommendation:{consumer}:abc:def",
        consumer=consumer,
        status="draft",
        source_finding_ids=["sha256:finding-1"],
        source_metric_refs=["metric_ref_1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_route_change_replay"],
        proposed_policy_artifact=artifact,
        activation_requirements=["replay_against_8ac124d6_passes"],
        rollback_requirements=["revert_to_prior_state"],
    )
    base.update(overrides)
    return GovernancePolicyRecommendation(**base)  # type: ignore[arg-type]


# ── module surface ──────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the documented surface exactly.

    Per doc-17:170-171 step 3 the surface is:

    * 1 typed failure id constant (``POLICY_VALIDATION_FAILURE_ID``).
    * 2 typed BaseModels (``ValidationViolation`` +
      ``ValidationResult``).
    * 1 interface class (``PolicyValidationInterface``).
    * 1 module-level convenience function (``validate_recommendation``).

    Total: 5 exported names.
    """

    from iriai_build_v2.execution_control import policy_validation_interface as mod

    assert mod.__all__ == [
        "POLICY_VALIDATION_FAILURE_ID",
        "ValidationViolation",
        "ValidationResult",
        "PolicyValidationInterface",
        "validate_recommendation",
    ]
    assert len(mod.__all__) == 5


def test_module_does_not_redefine_slice_13a_governance_evidence_ref() -> None:
    """The module MUST NOT redefine the Slice 13a typed
    :class:`GovernanceEvidenceRef`.

    Per doc-13a:285-287 step 9 the shared model is the authority for
    governance evidence-ref semantics; the Slice 17 3rd sub-slice
    module imports the typed shape directly.

    The DIRECT annotation-identity test asserts the
    :attr:`ValidationViolation.evidence_refs` field's element type is
    the Slice 13a shared :class:`GovernanceEvidenceRef` BaseModel
    (NOT a re-defined local copy).
    """

    hints = get_type_hints(ValidationViolation)
    evidence_refs_t = hints["evidence_refs"]
    assert get_origin(evidence_refs_t) is list
    (element_t,) = get_args(evidence_refs_t)
    # DIRECT annotation-identity assertion: the typed element type IS
    # the Slice 13a shared GovernanceEvidenceRef BaseModel.
    assert element_t is GovernanceEvidenceRef


def test_module_does_not_redefine_slice_17_first_policy_consumer() -> None:
    """The module MUST NOT redefine the Slice 17 1st sub-slice typed
    :data:`PolicyConsumer` Literal alias.

    Per ``feedback_no_overengineer_use_library`` the typed shape is
    consumed via direct import; the validator module annotates its
    typed fields with the same Literal alias from the Slice 17 1st
    sub-slice surface.
    """

    hints_violation = get_type_hints(ValidationViolation)
    hints_result = get_type_hints(ValidationResult)
    # The consumer fields on both BaseModels are the same Literal
    # alias from the Slice 17 1st sub-slice surface.
    assert hints_violation["consumer"] is hints_result["consumer"]
    # And both are the PolicyConsumer Literal alias.
    assert hints_violation["consumer"] is PolicyConsumer
    assert hints_result["consumer"] is PolicyConsumer


def test_module_does_not_import_any_consumer_module() -> None:
    """The validator module MUST NOT import any consumer-side module.

    Per the implementer-prompt boundary the validator does NOT import
    supervisor / dashboard / scheduler / planning / merge_queue / Slice
    07 failure_router beyond the typed failure id (no functional
    import; the 4 pure-data add points to failure_router.py validate
    at module-import without coupling to the failure_router runtime).
    Validation rules operate over the typed ``*PolicyArtifact``
    BaseModel SURFACE only.

    This test scans the validator module's source for forbidden
    import statements; the test fails if a consumer-side module is
    imported (a structural acceptance gate).
    """

    from iriai_build_v2.execution_control import policy_validation_interface as mod

    source_path = mod.__file__
    assert source_path is not None
    with open(source_path, "r", encoding="utf-8") as f:
        source = f.read()

    forbidden_imports = [
        # Supervisor consumer-side modules.
        "from iriai_build_v2.supervisor",
        "import iriai_build_v2.supervisor",
        # Dashboard consumer-side module.
        "from iriai_build_v2 import dashboard",
        "import dashboard\n",
        # Scheduler consumer-side module (Slice 09 regroup overlay
        # + scheduler feedback).
        "from iriai_build_v2.workflows.develop.execution.regroup",
        "import iriai_build_v2.workflows.develop.execution.regroup",
        # Planning consumer-side module (future-roadmapped).
        "from iriai_build_v2.workflows.develop.planning",
        "import iriai_build_v2.workflows.develop.planning",
        # Merge_queue consumer-side modules (Slice 08 + Slice 12).
        "from iriai_build_v2.execution_control.atomic_landing",
        "import iriai_build_v2.execution_control.atomic_landing",
        "from iriai_build_v2.workflows.develop.execution.merge_queue",
        "import iriai_build_v2.workflows.develop.execution.merge_queue",
        # Failure_router consumer-side module (Slice 07; the 4 pure-data
        # add points are pure-data; the validator does NOT functionally
        # import the runtime module).
        "from iriai_build_v2.workflows.develop.execution.failure_router",
        "import iriai_build_v2.workflows.develop.execution.failure_router",
        # Recommendation builder (Slice 17 2nd; this would be a
        # downstream-of-validator dependency, breaks the dependency
        # direction).
        "from iriai_build_v2.execution_control.recommendation_builder",
        "import iriai_build_v2.execution_control.recommendation_builder",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"validator module imports consumer-side module: {forbidden!r}"
        )


# ── ValidationViolation typed shape ─────────────────────────────────────────


def test_validation_violation_construction_minimal_defaults() -> None:
    """:class:`ValidationViolation` constructs with minimal required
    fields and default-empty evidence_refs."""

    violation = ValidationViolation(
        consumer="scheduler",
        rule_name="scheduler_guardrails_must_be_non_empty",
        violation_message="empty guardrails list",
    )
    assert violation.consumer == "scheduler"
    assert violation.rule_name == "scheduler_guardrails_must_be_non_empty"
    assert violation.violation_message == "empty guardrails list"
    assert violation.evidence_refs == []


def test_validation_violation_with_evidence_refs() -> None:
    """:class:`ValidationViolation` carries the typed evidence refs."""

    ref = _ref()
    violation = ValidationViolation(
        consumer="failure_router",
        rule_name="failure_router_required_tests_must_be_non_empty",
        violation_message="untested route change",
        evidence_refs=[ref],
    )
    assert len(violation.evidence_refs) == 1
    assert violation.evidence_refs[0] is ref


def test_validation_violation_extra_forbid_rejects_typo() -> None:
    """:class:`ValidationViolation` rejects typo-d kwargs."""

    with pytest.raises(ValidationError):
        ValidationViolation(
            consumer="scheduler",
            rule_name="rule",
            violation_message="msg",
            evidence_referencez=[],  # typo
        )  # type: ignore[call-arg]


def test_validation_violation_rejects_unknown_consumer() -> None:
    """:class:`ValidationViolation` rejects unknown consumer Literal."""

    with pytest.raises(ValidationError):
        ValidationViolation(
            consumer="not_a_consumer",  # type: ignore[arg-type]
            rule_name="rule",
            violation_message="msg",
        )


# ── ValidationResult typed shape ────────────────────────────────────────────


def test_validation_result_construction_minimal_defaults() -> None:
    """:class:`ValidationResult` constructs with minimal required
    fields and default-empty violations + None validated_at."""

    result = ValidationResult(
        recommendation_id="rec-1",
        consumer="scheduler",
        is_valid=True,
    )
    assert result.recommendation_id == "rec-1"
    assert result.consumer == "scheduler"
    assert result.is_valid is True
    assert result.violations == []
    assert result.validated_at is None


def test_validation_result_with_violations() -> None:
    """:class:`ValidationResult` carries the typed violations list."""

    violation = ValidationViolation(
        consumer="scheduler",
        rule_name="rule",
        violation_message="msg",
    )
    result = ValidationResult(
        recommendation_id="rec-2",
        consumer="scheduler",
        is_valid=False,
        violations=[violation],
    )
    assert result.is_valid is False
    assert len(result.violations) == 1
    assert result.violations[0] is violation


def test_validation_result_with_validated_at() -> None:
    """:class:`ValidationResult` carries the typed validated_at timestamp."""

    now = datetime(2026, 5, 25, 12, 0, 0)
    result = ValidationResult(
        recommendation_id="rec-3",
        consumer="scheduler",
        is_valid=True,
        validated_at=now,
    )
    assert result.validated_at == now


def test_validation_result_extra_forbid_rejects_typo() -> None:
    """:class:`ValidationResult` rejects typo-d kwargs."""

    with pytest.raises(ValidationError):
        ValidationResult(
            recommendation_id="rec-4",
            consumer="scheduler",
            is_valid=True,
            violatonz=[],  # typo
        )  # type: ignore[call-arg]


def test_validation_result_rejects_unknown_consumer() -> None:
    """:class:`ValidationResult` rejects unknown consumer Literal."""

    with pytest.raises(ValidationError):
        ValidationResult(
            recommendation_id="rec-5",
            consumer="not_a_consumer",  # type: ignore[arg-type]
            is_valid=True,
        )


# ── per-consumer happy-path validation (doc-17:208-210) ─────────────────────


def test_scheduler_happy_path_with_guardrails_is_valid() -> None:
    """Doc-17:208 -- scheduler artifact with non-empty guardrails passes."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["max_concurrent_tasks_per_lane"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    assert result.violations == []
    assert result.consumer == "scheduler"
    assert result.recommendation_id == rec.recommendation_id


def test_failure_router_happy_path_with_required_tests_is_valid() -> None:
    """Doc-17:209 -- failure_router artifact with non-empty required_tests passes."""

    artifact = FailureRouterPolicyArtifact(
        failure_class="runtime_provider",
        failure_type="runtime_provider_outage",
        action="retry",
        route_budget_key="runtime_provider/runtime_provider_outage",
        max_attempts=3,
        idempotency_key_template="runtime_provider/{task_id}/{attempt_window}",
        required_tests=["test_runtime_provider_outage_replay"],
    )
    rec = _recommendation(consumer="failure_router", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    assert result.violations == []


def test_supervisor_happy_path_with_read_only_true_is_valid() -> None:
    """Doc-17:210 -- supervisor artifact with read_only=True (default) passes."""

    artifact = SupervisorPolicyArtifact(
        policy_kind="classification_hint",
        scope={"lane_id": "ml-7"},
        value={"finding_kind": "product_defect_cluster"},
    )
    # read_only defaults to True per doc-17:126.
    assert artifact.read_only is True
    rec = _recommendation(consumer="supervisor", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    assert result.violations == []


def test_dashboard_happy_path_with_read_only_true_is_valid() -> None:
    """Doc-17:210 -- dashboard artifact with read_only=True (default) passes."""

    artifact = DashboardPolicyArtifact(
        policy_kind="view_priority",
        scope={"feature_id": "8ac124d6"},
        value={"finding_kind": "provenance_gap"},
    )
    assert artifact.read_only is True
    rec = _recommendation(consumer="dashboard", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    assert result.violations == []


def test_planning_happy_path_with_advisory_only_true_is_valid() -> None:
    """Doc-17:156 -- planning artifact with advisory_only=True (default) passes."""

    artifact = PlanningPolicyArtifact(
        policy_kind="contract_template_hint",
        scope={"feature_id": "8ac124d6"},
        value={"class_name": "task_contract_weakness"},
    )
    assert artifact.advisory_only is True
    rec = _recommendation(consumer="planning", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    assert result.violations == []


def test_merge_queue_happy_path_with_required_queue_tests_is_valid() -> None:
    """Doc-17:144 + doc-17:157-158 -- merge_queue artifact with required_queue_tests passes."""

    artifact = MergeQueuePolicyArtifact(
        policy_kind="recovery_budget",
        scope={"lane_id": "ml-7"},
        value={"class_name": "merge_queue_drag"},
        required_queue_tests=["test_merge_queue_recovery_replay"],
    )
    rec = _recommendation(consumer="merge_queue", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    assert result.violations == []


# ── per-consumer rejection-path validation (doc-17:208-210) ─────────────────


def test_scheduler_rejection_path_empty_guardrails() -> None:
    """Doc-17:208 -- scheduler artifact with EMPTY guardrails rejected."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=[],  # empty -> rejection
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert isinstance(v, ValidationViolation)
    assert v.consumer == "scheduler"
    assert v.rule_name == "scheduler_guardrails_must_be_non_empty"
    assert "doc-17:208" in v.violation_message


def test_failure_router_rejection_path_empty_required_tests() -> None:
    """Doc-17:209 -- failure_router artifact with EMPTY required_tests rejected."""

    artifact = FailureRouterPolicyArtifact(
        failure_class="runtime_provider",
        failure_type="runtime_provider_outage",
        action="retry",
        route_budget_key="runtime_provider/runtime_provider_outage",
        max_attempts=3,
        idempotency_key_template="runtime_provider/{task_id}/{attempt_window}",
        required_tests=[],  # empty -> rejection
    )
    rec = _recommendation(consumer="failure_router", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    # Two violations: (1) artifact.required_tests empty;
    # (2) recommendation.requires_tests non-empty so only (1) emitted
    # ... unless we also forced it empty. The fixture default carries
    # requires_tests=["test_route_change_replay"] so only (1).
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.consumer == "failure_router"
    assert v.rule_name == "failure_router_required_tests_must_be_non_empty"
    assert "doc-17:209" in v.violation_message


def test_failure_router_rejection_path_empty_recommendation_requires_tests() -> None:
    """Doc-17:218 -- failure_router recommendation with EMPTY
    requires_tests rejected as untested route change."""

    artifact = FailureRouterPolicyArtifact(
        failure_class="runtime_provider",
        failure_type="runtime_provider_outage",
        action="retry",
        route_budget_key="runtime_provider/runtime_provider_outage",
        max_attempts=3,
        idempotency_key_template="runtime_provider/{task_id}/{attempt_window}",
        required_tests=["artifact_test"],  # non-empty
    )
    rec = _recommendation(
        consumer="failure_router",
        artifact=artifact,
        requires_tests=[],  # empty -> rejection at recommendation level
    )
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.consumer == "failure_router"
    assert (
        v.rule_name
        == "failure_router_recommendation_requires_tests_must_be_non_empty"
    )
    assert "doc-17:218" in v.violation_message


def test_failure_router_rejection_path_both_empty() -> None:
    """Doc-17:209 + doc-17:218 -- failure_router with BOTH empty -> TWO violations.

    Per ``feedback_never_truncate_decisions`` the validator emits ALL
    failing rules per recommendation, not just the first.
    """

    artifact = FailureRouterPolicyArtifact(
        failure_class="runtime_provider",
        failure_type="runtime_provider_outage",
        action="retry",
        route_budget_key="runtime_provider/runtime_provider_outage",
        max_attempts=3,
        idempotency_key_template="x/{task_id}/{attempt_window}",
        required_tests=[],
    )
    rec = _recommendation(
        consumer="failure_router",
        artifact=artifact,
        requires_tests=[],
    )
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 2
    rule_names = {v.rule_name for v in result.violations}
    assert (
        "failure_router_required_tests_must_be_non_empty" in rule_names
    )
    assert (
        "failure_router_recommendation_requires_tests_must_be_non_empty"
        in rule_names
    )


def test_merge_queue_rejection_path_empty_required_queue_tests() -> None:
    """Doc-17:144 + doc-17:157-158 -- merge_queue with EMPTY required_queue_tests rejected."""

    artifact = MergeQueuePolicyArtifact(
        policy_kind="recovery_budget",
        scope={"lane_id": "ml-7"},
        value={"class_name": "merge_queue_drag"},
        required_queue_tests=[],  # empty -> rejection
    )
    rec = _recommendation(consumer="merge_queue", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.consumer == "merge_queue"
    assert (
        v.rule_name == "merge_queue_required_queue_tests_must_be_non_empty"
    )
    assert "doc-17:144" in v.violation_message
    assert "doc-17:157-158" in v.violation_message


# ── cross-consumer artifact-type mismatch detection (doc-17:147-158) ────────


def test_artifact_type_mismatch_scheduler_consumer_with_failure_router_artifact() -> None:
    """Doc-17:147-158 -- scheduler consumer with failure_router artifact
    is rejected as a type mismatch.

    Catches mis-emitted recommendations (e.g. a builder bug that
    produces a :class:`FailureRouterPolicyArtifact` on a recommendation
    tagged ``consumer="scheduler"``).
    """

    # Construct a failure_router artifact but tag the recommendation
    # as consumer="scheduler".
    artifact = FailureRouterPolicyArtifact(
        failure_class="runtime_provider",
        failure_type="runtime_provider_outage",
        action="retry",
        route_budget_key="x/y",
        max_attempts=3,
        idempotency_key_template="x/{task_id}/{attempt_window}",
        required_tests=["test_x"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.rule_name == "policy_validation_artifact_type_mismatch"
    assert "FailureRouterPolicyArtifact" in v.violation_message
    assert "SchedulerPolicyArtifact" in v.violation_message


def test_artifact_type_mismatch_supervisor_with_dashboard_artifact() -> None:
    """Doc-17:147-158 -- supervisor consumer with dashboard artifact rejected."""

    artifact = DashboardPolicyArtifact(
        policy_kind="view_priority",
        scope={"feature_id": "x"},
        value={},
    )
    rec = _recommendation(consumer="supervisor", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 1
    assert result.violations[0].rule_name == "policy_validation_artifact_type_mismatch"


# ── validator NEVER raises (feedback_no_silent_degradation) ─────────────────


def test_validator_does_not_raise_on_internal_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator NEVER raises; structural internal failures emit a
    typed :class:`ValidationResult` with ``is_valid=False`` + a typed
    :class:`ValidationViolation`.

    We simulate an internal failure by monkeypatching the per-consumer
    validator to raise an unexpected exception. The dispatch must
    catch this and emit a typed gap projection.
    """

    from iriai_build_v2.execution_control import policy_validation_interface as mod

    def boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("induced boom")

    monkeypatch.setattr(mod, "_validate_scheduler_artifact", boom)

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["x"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    # Must not raise.
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert isinstance(result, ValidationResult)
    assert result.is_valid is False
    assert len(result.violations) == 1
    v = result.violations[0]
    assert v.rule_name == "policy_validation_internal_exception"
    assert "induced boom" in v.violation_message


def test_validator_never_raises_typed_failure_id_constant_matches() -> None:
    """The typed failure id constant
    :data:`POLICY_VALIDATION_FAILURE_ID` matches the documented
    string verbatim."""

    assert POLICY_VALIDATION_FAILURE_ID == "policy_validation_failed"


# ── validator GRANTS NO ACTIVATION AUTHORITY (boundary check) ───────────────


def test_validator_grants_no_activation_authority_no_consumer_state_mutation() -> None:
    """Structural test: the validator does NOT call any consumer-side
    activation method.

    Per doc-17:170-171 *"Validation proves the artifact can be
    understood, not that it should be activated."* the validator
    returns a typed :class:`ValidationResult` and does NOT mutate
    any consumer state. We assert this structurally: there is no
    consumer-side module in ``sys.modules`` after a fresh
    ``validate_recommendation`` invocation (modulo what was already
    imported by test fixtures).

    This is a defense-in-depth check; the actual structural import
    boundary is enforced by
    :func:`test_module_does_not_import_any_consumer_module` above.
    """

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["x"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is True
    # The validator did not import consumer-side modules during
    # validation (i.e. validating a recommendation does not pull in
    # new consumer modules at runtime).
    # Note: dashboard.py may have been imported by other test fixtures;
    # the structural import test
    # `test_module_does_not_import_any_consumer_module` covers the
    # source-level boundary check.
    forbidden_runtime_imports = {
        "iriai_build_v2.workflows.develop.planning",
    }
    for name in forbidden_runtime_imports:
        assert name not in sys.modules, (
            f"validator pulled in forbidden consumer-side module at runtime: {name}"
        )


def test_validator_repeated_invocations_are_pure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator is stateless: repeated invocations on the same
    recommendation produce structurally-equal results (modulo
    validated_at timestamps)."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["x"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    interface = PolicyValidationInterface()
    r1 = interface.validate_recommendation(rec)
    r2 = interface.validate_recommendation(rec)
    assert r1.recommendation_id == r2.recommendation_id
    assert r1.consumer == r2.consumer
    assert r1.is_valid == r2.is_valid
    assert r1.violations == r2.violations


# ── annotation-identity REUSE (Slice 13a + Slice 17 1st) ────────────────────


def test_validation_result_consumer_annotation_is_slice_17_first_policy_consumer() -> None:
    """:attr:`ValidationResult.consumer` annotation IS the Slice 17 1st
    sub-slice typed :data:`PolicyConsumer` Literal."""

    hints = get_type_hints(ValidationResult)
    consumer_t = hints["consumer"]
    assert consumer_t is PolicyConsumer


def test_validation_violation_consumer_annotation_is_slice_17_first_policy_consumer() -> None:
    """:attr:`ValidationViolation.consumer` annotation IS the Slice 17 1st
    sub-slice typed :data:`PolicyConsumer` Literal."""

    hints = get_type_hints(ValidationViolation)
    consumer_t = hints["consumer"]
    assert consumer_t is PolicyConsumer


def test_validation_violation_evidence_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """:attr:`ValidationViolation.evidence_refs` element type IS the
    Slice 13a typed :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(ValidationViolation)
    evidence_refs_t = hints["evidence_refs"]
    assert get_origin(evidence_refs_t) is list
    (element_t,) = get_args(evidence_refs_t)
    assert element_t is GovernanceEvidenceRef


def test_validate_recommendation_accepts_slice_17_first_recommendation_basemodel() -> None:
    """The dispatch method's parameter annotation IS the Slice 17 1st
    sub-slice typed :class:`GovernancePolicyRecommendation`."""

    hints = get_type_hints(PolicyValidationInterface.validate_recommendation)
    rec_t = hints["recommendation"]
    assert rec_t is GovernancePolicyRecommendation
    # And the return type is the local typed ValidationResult.
    return_t = hints["return"]
    assert return_t is ValidationResult


# ── per-consumer rule coverage (6 of 6 consumers) ───────────────────────────


def test_all_six_consumers_have_validators() -> None:
    """The 6 doc-17:65 :data:`PolicyConsumer` values each have a
    validator function in the module."""

    from iriai_build_v2.execution_control import policy_validation_interface as mod

    consumer_values = get_args(PolicyConsumer)
    assert len(consumer_values) == 6
    for consumer in consumer_values:
        validator_name = f"_validate_{consumer}_artifact"
        assert hasattr(mod, validator_name), (
            f"missing per-consumer validator: {validator_name}"
        )


def test_all_six_consumers_round_trip_through_dispatch() -> None:
    """Each of the 6 consumers can be dispatched to its validator
    without raising."""

    cases: list[
        tuple[
            PolicyConsumer,
            (
                SchedulerPolicyArtifact
                | FailureRouterPolicyArtifact
                | SupervisorPolicyArtifact
                | DashboardPolicyArtifact
                | PlanningPolicyArtifact
                | MergeQueuePolicyArtifact
            ),
        ]
    ] = [
        (
            "scheduler",
            SchedulerPolicyArtifact(
                policy_kind="wave_cap",
                scope={"lane_id": "ml-7"},
                value={"wave_cap": 7},
                guardrails=["x"],
            ),
        ),
        (
            "failure_router",
            FailureRouterPolicyArtifact(
                failure_class="runtime_provider",
                failure_type="runtime_provider_outage",
                action="retry",
                route_budget_key="x/y",
                max_attempts=3,
                idempotency_key_template="x/{task_id}/{attempt_window}",
                required_tests=["x"],
            ),
        ),
        (
            "supervisor",
            SupervisorPolicyArtifact(
                policy_kind="classification_hint",
                scope={"lane_id": "ml-7"},
                value={},
            ),
        ),
        (
            "dashboard",
            DashboardPolicyArtifact(
                policy_kind="view_priority",
                scope={"feature_id": "x"},
                value={},
            ),
        ),
        (
            "planning",
            PlanningPolicyArtifact(
                policy_kind="contract_template_hint",
                scope={"feature_id": "x"},
                value={},
            ),
        ),
        (
            "merge_queue",
            MergeQueuePolicyArtifact(
                policy_kind="recovery_budget",
                scope={"lane_id": "ml-7"},
                value={},
                required_queue_tests=["x"],
            ),
        ),
    ]
    interface = PolicyValidationInterface()
    for consumer, artifact in cases:
        rec = _recommendation(consumer=consumer, artifact=artifact)
        result = interface.validate_recommendation(rec)
        assert isinstance(result, ValidationResult)
        assert result.consumer == consumer
        assert result.is_valid is True


# ── module-level convenience function ───────────────────────────────────────


def test_validate_recommendation_module_level_function() -> None:
    """The module-level :func:`validate_recommendation` wraps the
    default :class:`PolicyValidationInterface` instance."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["x"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = validate_recommendation(rec)
    assert isinstance(result, ValidationResult)
    assert result.is_valid is True


def test_validate_recommendation_module_level_function_returns_violations() -> None:
    """The module-level function returns typed violations on rejection."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=[],  # empty -> rejection
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = validate_recommendation(rec)
    assert result.is_valid is False
    assert len(result.violations) == 1


# ── failure_router 4-add-point validation ───────────────────────────────────


def test_failure_router_registers_new_failure_id_in_failure_types_tuple() -> None:
    """Add point 1 of 4: the new typed failure id
    ``policy_validation_failed`` is registered in the
    :data:`FAILURE_TYPES` tuple."""

    assert "policy_validation_failed" in FAILURE_TYPES


def test_failure_router_registers_new_failure_id_in_failure_type_literal() -> None:
    """Add point 2 of 4: the new typed failure id is registered in
    the :data:`FailureType` Literal range."""

    assert "policy_validation_failed" in get_args(FailureType)


def test_failure_router_registers_new_failure_id_as_retryable() -> None:
    """Add point 3 of 4: the new typed failure id is registered in
    :data:`_RETRYABLE_FAILURE_TYPES`."""

    assert "policy_validation_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_routes_new_failure_id_to_retry_governance_projection() -> None:
    """Add point 4 of 4: the new typed failure id routes to the
    EXISTING ``retry_governance_projection`` NON-blocking RouteAction
    (REUSED from Slice 14 2nd sub-slice; mirrors Slice 16 2nd/3rd-A/
    3rd-B/4th + Slice 17 2nd sub-slice precedent verbatim) under the
    EXISTING ``evidence_corruption`` failure_class."""

    # Find the route row for (evidence_corruption, policy_validation_failed).
    matching = [
        row
        for row in _ROUTE_ROWS
        if row[0].failure_class == "evidence_corruption"
        and row[0].failure_type == "policy_validation_failed"
    ]
    assert len(matching) == 1, (
        f"expected exactly 1 route row for (evidence_corruption, "
        f"policy_validation_failed); got {len(matching)}"
    )
    policy, route_policy = matching[0]
    assert route_policy.action == "retry_governance_projection"


def test_policy_validation_failure_id_constant_matches_route() -> None:
    """The typed failure id constant
    :data:`POLICY_VALIDATION_FAILURE_ID` matches the failure_router
    registration verbatim."""

    assert POLICY_VALIDATION_FAILURE_ID == "policy_validation_failed"
    assert POLICY_VALIDATION_FAILURE_ID in FAILURE_TYPES
    assert POLICY_VALIDATION_FAILURE_ID in get_args(FailureType)
    assert POLICY_VALIDATION_FAILURE_ID in _RETRYABLE_FAILURE_TYPES


# ── ConfigDict(extra="forbid") discipline ───────────────────────────────────


def test_all_new_basemodels_extra_forbid_discipline() -> None:
    """Per ``feedback_no_silent_degradation`` every new BaseModel has
    ``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed."""

    for cls in (ValidationViolation, ValidationResult):
        assert cls.model_config.get("extra") == "forbid", (
            f"{cls.__name__} must have ConfigDict(extra='forbid')"
        )


# ── empty recommendation handling ───────────────────────────────────────────


def test_validator_handles_recommendation_with_no_evidence_refs_in_violation() -> None:
    """Per doc-17:170-171 the violation's evidence_refs default is
    empty list; the validator's per-consumer rules emit violations
    without populating evidence_refs (the violation message itself
    is the audit trail)."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=[],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.is_valid is False
    for v in result.violations:
        # The per-consumer validators emit violations without
        # populating evidence_refs.
        assert v.evidence_refs == []


# ── audit-trail fields (recommendation_id + consumer + validated_at) ────────


def test_validation_result_recommendation_id_matches_input() -> None:
    """The :attr:`ValidationResult.recommendation_id` field echoes the
    input recommendation's id."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["x"],
    )
    rec = _recommendation(
        consumer="scheduler",
        artifact=artifact,
        recommendation_id="recommendation:scheduler:abc:def",
    )
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.recommendation_id == "recommendation:scheduler:abc:def"


def test_validation_result_consumer_matches_input() -> None:
    """The :attr:`ValidationResult.consumer` field echoes the input
    recommendation's consumer."""

    artifact = SupervisorPolicyArtifact(
        policy_kind="classification_hint",
        scope={"lane_id": "ml-7"},
        value={},
    )
    rec = _recommendation(consumer="supervisor", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.consumer == "supervisor"


def test_validation_result_validated_at_is_populated_by_dispatch() -> None:
    """The dispatch method populates :attr:`ValidationResult.validated_at`
    at validation time (not None)."""

    artifact = SchedulerPolicyArtifact(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["x"],
    )
    rec = _recommendation(consumer="scheduler", artifact=artifact)
    result = PolicyValidationInterface().validate_recommendation(rec)
    assert result.validated_at is not None
    assert isinstance(result.validated_at, datetime)


# ── supervisor & dashboard read_only typed cross-check ──────────────────────


def test_supervisor_read_only_is_enforced_by_typed_shape() -> None:
    """The Slice 17 1st sub-slice :class:`SupervisorPolicyArtifact` typed
    ``Literal[True]`` already enforces read_only at construction.

    This test verifies the typed shape (Pydantic ValidationError) is
    the first line of defense; the validator's
    :func:`_validate_supervisor_artifact` is the second.
    """

    # Constructing with read_only=False raises a typed ValidationError.
    with pytest.raises(ValidationError):
        SupervisorPolicyArtifact(
            policy_kind="classification_hint",
            scope={"lane_id": "ml-7"},
            value={},
            read_only=False,  # type: ignore[arg-type]
        )


def test_dashboard_read_only_is_enforced_by_typed_shape() -> None:
    """The Slice 17 1st sub-slice :class:`DashboardPolicyArtifact` typed
    ``Literal[True]`` already enforces read_only at construction."""

    with pytest.raises(ValidationError):
        DashboardPolicyArtifact(
            policy_kind="view_priority",
            scope={"feature_id": "x"},
            value={},
            read_only=False,  # type: ignore[arg-type]
        )


def test_planning_advisory_only_is_enforced_by_typed_shape() -> None:
    """The Slice 17 1st sub-slice :class:`PlanningPolicyArtifact` typed
    ``Literal[True]`` already enforces advisory_only at construction."""

    with pytest.raises(ValidationError):
        PlanningPolicyArtifact(
            policy_kind="contract_template_hint",
            scope={"feature_id": "x"},
            value={},
            advisory_only=False,  # type: ignore[arg-type]
        )


# ── PolicyValidationInterface stateless construction ────────────────────────


def test_policy_validation_interface_construction_takes_no_args() -> None:
    """:class:`PolicyValidationInterface` constructs without arguments
    (stateless per ``feedback_no_overengineer_use_library``)."""

    interface = PolicyValidationInterface()
    assert isinstance(interface, PolicyValidationInterface)
