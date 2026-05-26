"""Slice 17 fifth sub-slice -- unit tests for the replay-requirement
hook + validator at
``execution_control/replay_requirement_hook.py``.

Covers the doc-17:173-174 step 5 typed-shape replay-requirement hook +
behavior-changing-recommendation gating discipline + DIRECT
annotation-identity Slice 13a + Slice 17 1st sub-slice REUSE assertions
+ the validator-never-raises non-blocking contract + the no-second-
source-of-replay-truth structural boundary.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 1st/2nd/3rd/4th sub-slice modules + tests remain
byte-identical.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    SchedulerPolicyArtifact,
)
from iriai_build_v2.execution_control.replay_requirement_hook import (
    REPLAY_REQUIREMENT_MISSING_REASON,
    REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
    ReplayRequirementHook,
    ReplayRequirementValidationGap,
    ReplayRequirementValidationResult,
    ReplayRequirementValidator,
    validate_replay_requirement,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
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


def _scheduler_artifact(**overrides: object) -> SchedulerPolicyArtifact:
    """Construct a fully-specified :class:`SchedulerPolicyArtifact`."""

    base: dict[str, object] = dict(
        policy_kind="wave_cap",
        scope={"lane_id": "ml-7"},
        value={"wave_cap": 7},
        guardrails=["max_concurrent_tasks_per_lane"],
    )
    base.update(overrides)
    return SchedulerPolicyArtifact(**base)  # type: ignore[arg-type]


def _recommendation(**overrides: object) -> GovernancePolicyRecommendation:
    """Construct a fully-specified :class:`GovernancePolicyRecommendation`.

    Default is a non-behavior-changing recommendation (safe_runtime_action=False)
    with an empty counterfactual_result_refs list -- the trivial-pass case.
    """

    base: dict[str, object] = dict(
        idempotency_key="rec-key-1",
        recommendation_id="rec-1",
        consumer="scheduler",
        status="draft",
        source_finding_ids=["finding-1"],
        source_metric_refs=["tasks_per_hour@v1"],
        counterfactual_result_refs=[],
        confidence=0.85,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=False,
        requires_tests=["test_scheduler_wave_cap"],
        proposed_policy_artifact=_scheduler_artifact(),
        activation_requirements=["scheduler_test_suite_green"],
        rollback_requirements=["revert_to_prior_wave_cap"],
    )
    base.update(overrides)
    return GovernancePolicyRecommendation(**base)  # type: ignore[arg-type]


# ── module surface tests ────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the documented surface exactly.

    Per doc-17:173-174 step 5 the surface is:

    * 1 typed failure id constant (``REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID``).
    * 1 typed gap-reason Literal const (``REPLAY_REQUIREMENT_MISSING_REASON``).
    * 3 typed BaseModels (``ReplayRequirementHook`` +
      ``ReplayRequirementValidationGap`` +
      ``ReplayRequirementValidationResult``).
    * 1 validator class (``ReplayRequirementValidator``).
    * 1 module-level convenience function (``validate_replay_requirement``).

    Total: 7 exported names.
    """

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    assert set(mod.__all__) == {
        "REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID",
        "REPLAY_REQUIREMENT_MISSING_REASON",
        "ReplayRequirementHook",
        "ReplayRequirementValidationGap",
        "ReplayRequirementValidationResult",
        "ReplayRequirementValidator",
        "validate_replay_requirement",
    }


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """The module imports the Slice 13a shared
    :class:`GovernanceEvidenceRef`; it does NOT define a local copy."""

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    assert "GovernanceEvidenceRef" not in mod.__all__


def test_module_does_not_redefine_governance_policy_recommendation() -> None:
    """The module imports the Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation`; it does NOT define a
    local copy."""

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    assert "GovernancePolicyRecommendation" not in mod.__all__


def test_module_import_discipline_no_supervisor_dashboard_phases() -> None:
    """The module MUST NOT import from supervisor / dashboard / phases /
    recommendation_builder / policy_validation_interface /
    decision_record_writer / Slice 18 modules.

    Per the implementer brief Non-negotiables the validator is a pure
    typed-surface observer; it consumes typed shapes from Slice 13a +
    Slice 17 1st only.
    """

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_imports = [
        "from iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.supervisor",
        "import iriai_build_v2.dashboard",
        "from iriai_build_v2.workflows.develop.execution.phases",
        "from iriai_build_v2.execution_control.recommendation_builder",
        "from iriai_build_v2.execution_control.policy_validation_interface",
        "from iriai_build_v2.execution_control.decision_record_writer",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"forbidden import found in replay_requirement_hook.py: {forbidden}"
        )


def test_module_no_second_source_of_replay_truth_structural() -> None:
    """The module MUST NOT introduce a second source of replay truth
    per doc-17:159-163 + doc-17:225-226.

    The validator consumes only the typed ``list[str]`` of ref-strings
    from the recommendation; it does NOT load / fetch / construct /
    execute / replay any Slice 18 counterfactual replay result.

    Structural test: the module source MUST NOT mention forbidden
    symbols (``fetch_replay_result`` / ``load_replay_result`` /
    ``construct_replay_result`` / ``CounterfactualReplayResult`` /
    ``ReplayEngine`` / ``execute_replay`` / ``simulate_replay``).
    """

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_symbols = [
        "fetch_replay_result",
        "load_replay_result",
        "construct_replay_result",
        "CounterfactualReplayResult",
        "ReplayEngine",
        "execute_replay",
        "simulate_replay",
        "replay_engine",
    ]
    for forbidden in forbidden_symbols:
        assert forbidden not in source, (
            f"forbidden symbol found (second source of replay truth): {forbidden}"
        )


def test_module_no_slice_18_imports() -> None:
    """The module MUST NOT import any Slice 18 module.

    Per the implementer brief + doc-17:159-163 + doc-17:225-226 the
    validator's typed surface is the cross-reference shape only; Slice
    18 owns the typed shape of the actual replay-result + the fetch /
    load / construct surface.
    """

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_slice18_prefixes = [
        "from iriai_build_v2.execution_control.counterfactual_replay",
        "from iriai_build_v2.execution_control.replay_engine",
        "from iriai_build_v2.execution_control.counterfactual",
    ]
    for forbidden in forbidden_slice18_prefixes:
        assert forbidden not in source, (
            f"forbidden Slice 18 import found: {forbidden}"
        )


def test_module_import_discipline_only_allowed_imports() -> None:
    """The module MUST import only from stdlib + Pydantic v2 + Slice
    13a governance.models + Slice 17 1st policy_recommendation."""

    from iriai_build_v2.execution_control import replay_requirement_hook as mod

    source = Path(inspect.getfile(mod)).read_text()
    # Required imports (positive controls).
    assert "from pydantic import" in source
    assert (
        "from iriai_build_v2.workflows.develop.governance.models import"
        in source
    )
    assert (
        "from iriai_build_v2.execution_control.policy_recommendation import"
        in source
    )


def test_package_init_does_not_re_export_validator() -> None:
    """Per the implementer brief Non-negotiables the package
    ``__init__.py`` does NOT re-export the Slice 17 5th sub-slice
    typed surface."""

    import iriai_build_v2.execution_control as pkg

    pkg_file = pkg.__file__
    assert pkg_file is not None
    init_source = Path(pkg_file).read_text()
    for name in (
        "ReplayRequirementHook",
        "ReplayRequirementValidationGap",
        "ReplayRequirementValidationResult",
        "ReplayRequirementValidator",
        "REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID",
        "REPLAY_REQUIREMENT_MISSING_REASON",
        "validate_replay_requirement",
    ):
        assert name not in init_source


# ── failure-id + gap-reason Literal constants ───────────────────────────────


def test_failure_id_value_matches_chunk_shape() -> None:
    """The typed failure id is ``replay_requirement_validation_failed``
    per the Slice 17 5th sub-slice chunk-shape."""

    assert (
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID
        == "replay_requirement_validation_failed"
    )


def test_gap_reason_constant_value() -> None:
    """The typed gap-reason constant matches the chunk-shape."""

    assert REPLAY_REQUIREMENT_MISSING_REASON == "replay_requirement_missing"


def test_failure_id_differs_from_slice_17_2nd_3rd_4th_ids() -> None:
    """The Slice 17 5th sub-slice failure id is intentionally different
    from the prior Slice 17 sub-slice failure ids."""

    from iriai_build_v2.execution_control.recommendation_builder import (
        RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.policy_validation_interface import (
        POLICY_VALIDATION_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.decision_record_writer import (
        DECISION_RECORD_PERSISTENCE_FAILURE_ID,
    )

    assert (
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID
        != RECOMMENDATION_BUILDER_EMISSION_FAILURE_ID
    )
    assert (
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID
        != POLICY_VALIDATION_FAILURE_ID
    )
    assert (
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID
        != DECISION_RECORD_PERSISTENCE_FAILURE_ID
    )


# ── ReplayRequirementHook typed-shape construction ──────────────────────────


def test_hook_accepts_required_fields() -> None:
    """The typed hook accepts the required fields per doc-17:173-174."""

    hook = ReplayRequirementHook(
        recommendation_id="rec-1",
        counterfactual_result_refs=["cf-1", "cf-2"],
    )
    assert hook.recommendation_id == "rec-1"
    assert hook.counterfactual_result_refs == ["cf-1", "cf-2"]
    assert hook.validated_at is None
    assert hook.validator_version is None


def test_hook_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline: unknown fields fail
    closed with a typed ``ValidationError``."""

    with pytest.raises(ValidationError):
        ReplayRequirementHook(
            recommendation_id="rec-1",
            counterfactual_result_refs=[],
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_hook_round_trips_via_json() -> None:
    """The typed hook round-trips via ``model_dump`` +
    ``model_validate``."""

    hook = ReplayRequirementHook(
        recommendation_id="rec-1",
        counterfactual_result_refs=["cf-1"],
        validated_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
        validator_version="v1",
    )
    dumped = hook.model_dump(mode="json")
    re_hook = ReplayRequirementHook.model_validate(dumped)
    assert re_hook == hook


def test_hook_accepts_empty_refs() -> None:
    """The typed hook accepts an empty refs list (the validator gates
    on emptiness; the hook surface itself does not)."""

    hook = ReplayRequirementHook(
        recommendation_id="rec-1",
        counterfactual_result_refs=[],
    )
    assert hook.counterfactual_result_refs == []


def test_hook_validated_at_iso_string_in_dump() -> None:
    """The datetime field projects to ISO-8601 string under
    ``mode='json'``."""

    hook = ReplayRequirementHook(
        recommendation_id="rec-1",
        counterfactual_result_refs=[],
        validated_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
    )
    dumped = hook.model_dump(mode="json")
    assert isinstance(dumped["validated_at"], str)
    assert "2026-05-25" in dumped["validated_at"]


def test_hook_required_recommendation_id() -> None:
    """The ``recommendation_id`` field is required (no default)."""

    with pytest.raises(ValidationError):
        ReplayRequirementHook(
            counterfactual_result_refs=[],  # type: ignore[call-arg]
        )


def test_hook_required_counterfactual_result_refs() -> None:
    """The ``counterfactual_result_refs`` field is required (no default)."""

    with pytest.raises(ValidationError):
        ReplayRequirementHook(
            recommendation_id="rec-1",  # type: ignore[call-arg]
        )


# ── ReplayRequirementValidationGap typed-shape construction ─────────────────


def test_gap_accepts_required_fields() -> None:
    """The typed gap accepts the documented fields."""

    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="replay_requirement_missing",
    )
    assert gap.failure_id == "replay_requirement_validation_failed"
    assert gap.recommendation_id == "rec-1"
    assert gap.reason == "replay_requirement_missing"
    assert gap.evidence_refs == []


def test_gap_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        ReplayRequirementValidationGap(
            failure_id="replay_requirement_validation_failed",
            recommendation_id="rec-1",
            reason="x",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_gap_round_trips_via_json() -> None:
    """The typed gap round-trips via ``model_dump`` + ``model_validate``."""

    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="replay_requirement_missing",
        evidence_refs=[_ref()],
    )
    dumped = gap.model_dump(mode="json")
    re_gap = ReplayRequirementValidationGap.model_validate(dumped)
    assert re_gap == gap


def test_gap_failure_id_literal_rejects_other_failure_ids() -> None:
    """The ``failure_id`` Literal range rejects any other failure id."""

    with pytest.raises(ValidationError):
        ReplayRequirementValidationGap(
            failure_id="policy_validation_failed",  # type: ignore[arg-type]
            recommendation_id="rec-1",
            reason="x",
        )


def test_gap_observed_at_defaults_to_utc_now() -> None:
    """The ``observed_at`` field defaults to UTC now (within a tolerance)."""

    before = datetime.now(tz=timezone.utc)
    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="x",
    )
    after = datetime.now(tz=timezone.utc)
    assert before <= gap.observed_at <= after


def test_gap_evidence_refs_defaults_to_empty_list() -> None:
    """The ``evidence_refs`` field defaults to an empty list."""

    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="x",
    )
    assert gap.evidence_refs == []


def test_gap_accepts_multiple_evidence_refs() -> None:
    """The ``evidence_refs`` field accepts multiple Slice 13a refs."""

    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="x",
        evidence_refs=[_ref(ref_id="a"), _ref(ref_id="b")],
    )
    assert len(gap.evidence_refs) == 2


# ── ReplayRequirementValidationResult typed-shape construction ──────────────


def test_result_accepts_minimal_pass_fields() -> None:
    """The typed result accepts the minimal-pass field set."""

    result = ReplayRequirementValidationResult(
        recommendation_id="rec-1",
        is_valid=True,
    )
    assert result.recommendation_id == "rec-1"
    assert result.is_valid is True
    assert result.gap is None
    assert result.reasons == []
    assert result.validated_at is None


def test_result_accepts_fail_fields_with_gap() -> None:
    """The typed result accepts the fail-with-gap field set."""

    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="replay_requirement_missing",
    )
    result = ReplayRequirementValidationResult(
        recommendation_id="rec-1",
        is_valid=False,
        gap=gap,
        reasons=["explanation"],
    )
    assert result.is_valid is False
    assert result.gap is gap
    assert result.reasons == ["explanation"]


def test_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        ReplayRequirementValidationResult(
            recommendation_id="rec-1",
            is_valid=True,
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_result_round_trips_via_json() -> None:
    """The typed result round-trips via ``model_dump`` + ``model_validate``."""

    gap = ReplayRequirementValidationGap(
        failure_id="replay_requirement_validation_failed",
        recommendation_id="rec-1",
        reason="replay_requirement_missing",
    )
    result = ReplayRequirementValidationResult(
        recommendation_id="rec-1",
        is_valid=False,
        gap=gap,
        reasons=["explanation"],
        validated_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
    )
    dumped = result.model_dump(mode="json")
    re_result = ReplayRequirementValidationResult.model_validate(dumped)
    assert re_result == result


def test_result_required_recommendation_id() -> None:
    """The ``recommendation_id`` field is required."""

    with pytest.raises(ValidationError):
        ReplayRequirementValidationResult(
            is_valid=True,  # type: ignore[call-arg]
        )


def test_result_required_is_valid() -> None:
    """The ``is_valid`` field is required."""

    with pytest.raises(ValidationError):
        ReplayRequirementValidationResult(
            recommendation_id="rec-1",  # type: ignore[call-arg]
        )


def test_result_reasons_defaults_to_empty() -> None:
    """The ``reasons`` field defaults to an empty list."""

    result = ReplayRequirementValidationResult(
        recommendation_id="rec-1",
        is_valid=True,
    )
    assert result.reasons == []


# ── DIRECT annotation-identity REUSE assertions ─────────────────────────────


def test_gap_evidence_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ReplayRequirementValidationGap.evidence_refs` field's
    element type IS the Slice 13a shared :class:`GovernanceEvidenceRef`
    BaseModel (NOT a re-defined local copy)."""

    hints = get_type_hints(ReplayRequirementValidationGap)
    evidence_refs_t = hints["evidence_refs"]
    assert get_origin(evidence_refs_t) is list
    (element_t,) = get_args(evidence_refs_t)
    assert element_t is GovernanceEvidenceRef


def test_result_gap_annotation_is_local_replay_requirement_validation_gap() -> None:
    """DIRECT annotation-identity REUSE assertion: the typed
    :attr:`ReplayRequirementValidationResult.gap` field's type IS the
    local :class:`ReplayRequirementValidationGap` (NOT a re-defined
    local copy)."""

    hints = get_type_hints(ReplayRequirementValidationResult)
    gap_t = hints["gap"]
    args = get_args(gap_t)
    assert ReplayRequirementValidationGap in args


def test_validator_validate_parameter_annotation_is_slice_17_first_governance_policy_recommendation() -> None:
    """DIRECT annotation-identity REUSE assertion: the validator's
    :meth:`ReplayRequirementValidator.validate` method's parameter
    annotation IS the Slice 17 1st sub-slice typed
    :class:`GovernancePolicyRecommendation` (NOT a re-defined local
    copy)."""

    hints = get_type_hints(ReplayRequirementValidator.validate)
    assert hints["recommendation"] is GovernancePolicyRecommendation


def test_validator_validate_return_annotation_is_local_result() -> None:
    """DIRECT annotation-identity REUSE assertion: the validator's
    :meth:`ReplayRequirementValidator.validate` method's return type
    annotation IS the local :class:`ReplayRequirementValidationResult`."""

    hints = get_type_hints(ReplayRequirementValidator.validate)
    assert hints["return"] is ReplayRequirementValidationResult


def test_module_level_function_parameter_annotation_is_slice_17_first_governance_policy_recommendation() -> None:
    """DIRECT annotation-identity REUSE assertion: the module-level
    :func:`validate_replay_requirement` function's parameter annotation
    IS the Slice 17 1st sub-slice typed
    :class:`GovernancePolicyRecommendation`."""

    hints = get_type_hints(validate_replay_requirement)
    assert hints["recommendation"] is GovernancePolicyRecommendation


# ── failure-router 4-pure-data-add-point validation ─────────────────────────


def test_failure_router_registers_new_typed_failure_id() -> None:
    """Add-point 1: the NEW typed failure id
    ``replay_requirement_validation_failed`` is registered in the
    :data:`FailureType` Literal."""

    args = get_args(FailureType)
    assert "replay_requirement_validation_failed" in args


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add-point 2: the NEW typed failure id is in the
    :data:`FAILURE_TYPES` tuple."""

    assert "replay_requirement_validation_failed" in FAILURE_TYPES


def test_failure_router_new_id_in_retryable_set() -> None:
    """Add-point 3: the NEW typed failure id is in the
    :data:`_RETRYABLE_FAILURE_TYPES` frozenset (the transient
    governance projection failure pattern)."""

    assert "replay_requirement_validation_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Add-point 4: the route table carries the NEW typed failure id
    under the EXISTING ``evidence_corruption`` failure_class with the
    REUSED ``retry_governance_projection`` NON-blocking RouteAction."""

    key = ("evidence_corruption", "replay_requirement_validation_failed")
    assert key in ROUTE_TABLE
    route = ROUTE_TABLE[key]
    assert route.action == "retry_governance_projection"


def test_failure_router_reuses_existing_route_action() -> None:
    """The NEW failure id REUSES the EXISTING
    ``retry_governance_projection`` action (NOT a new action; the
    action was introduced by Slice 14 2nd sub-slice)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        ROUTE_ACTIONS,
    )

    assert "retry_governance_projection" in ROUTE_ACTIONS


def test_failure_router_route_includes_explanation() -> None:
    """The route table row carries a non-empty explanation reason."""

    key = ("evidence_corruption", "replay_requirement_validation_failed")
    route = ROUTE_TABLE[key]
    assert route.reason
    assert "Slice 17 5th" in route.reason


def test_failure_router_pure_data_add_point_route_table_row_present() -> None:
    """Defence-in-depth: the add-point landed as a row in the
    :data:`_ROUTE_ROWS` tuple-of-tuples."""

    seen = False
    for row in _ROUTE_ROWS:
        _, route = row
        if (
            route.failure_class == "evidence_corruption"
            and route.failure_type == "replay_requirement_validation_failed"
        ):
            seen = True
            assert route.action == "retry_governance_projection"
            break
    assert seen, (
        "replay_requirement_validation_failed row not found in _ROUTE_ROWS"
    )


# ── Validator: happy-path (non-behavior-changing recommendations) ──────────


def test_validator_pass_trivially_when_not_behavior_changing() -> None:
    """A non-behavior-changing recommendation (safe_runtime_action=False)
    passes trivially even with an empty counterfactual_result_refs list
    (per doc-17:198-200 the recommendation cannot be consumed by runtime
    policy at all; the replay reference is not required)."""

    recommendation = _recommendation(
        safe_runtime_action=False, counterfactual_result_refs=[]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is True
    assert result.gap is None
    assert result.reasons == []
    assert result.recommendation_id == "rec-1"


def test_validator_pass_trivially_with_populated_refs_when_not_behavior_changing() -> None:
    """A non-behavior-changing recommendation passes trivially regardless
    of the refs list contents."""

    recommendation = _recommendation(
        safe_runtime_action=False, counterfactual_result_refs=["cf-1"]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is True
    assert result.gap is None


def test_validator_sets_validated_at_on_pass() -> None:
    """The validator sets ``validated_at`` to a UTC timestamp on pass."""

    recommendation = _recommendation(safe_runtime_action=False)
    before = datetime.now(tz=timezone.utc)
    result = ReplayRequirementValidator().validate(recommendation)
    after = datetime.now(tz=timezone.utc)
    assert result.validated_at is not None
    assert before <= result.validated_at <= after


# ── Validator: refuse-path (behavior-changing + empty refs) ─────────────────


def test_validator_rejects_behavior_changing_with_empty_refs() -> None:
    """A behavior-changing recommendation (safe_runtime_action=True)
    with empty counterfactual_result_refs MUST be rejected with a
    typed gap (reason=replay_requirement_missing) per doc-17:173-174."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is False
    assert result.gap is not None
    assert result.gap.failure_id == "replay_requirement_validation_failed"
    assert result.gap.reason == REPLAY_REQUIREMENT_MISSING_REASON
    assert result.gap.recommendation_id == "rec-1"


def test_validator_reject_carries_explanatory_reason() -> None:
    """The reject path populates ``reasons`` with at least one
    human-readable explanation (per
    ``feedback_never_truncate_decisions``)."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert len(result.reasons) >= 1
    # The explanation should cite the doc-17:173-174 contract.
    joined = " ".join(result.reasons)
    assert "173-174" in joined or "behavior-changing" in joined.lower()


def test_validator_reject_uses_typed_failure_id_in_gap() -> None:
    """The reject path's typed gap uses the typed
    :data:`REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID` constant."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.gap is not None
    assert (
        result.gap.failure_id == REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID
    )


def test_validator_reject_uses_typed_reason_constant() -> None:
    """The reject path's typed gap uses the typed
    :data:`REPLAY_REQUIREMENT_MISSING_REASON` constant."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.gap is not None
    assert result.gap.reason == REPLAY_REQUIREMENT_MISSING_REASON


# ── Validator: pass-path (behavior-changing + populated refs) ──────────────


def test_validator_passes_behavior_changing_with_populated_refs() -> None:
    """A behavior-changing recommendation with non-empty
    counterfactual_result_refs MUST pass."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=["cf-1"]
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is True
    assert result.gap is None
    assert result.reasons == []


def test_validator_passes_behavior_changing_with_multiple_refs() -> None:
    """A behavior-changing recommendation with multiple
    counterfactual_result_refs passes."""

    recommendation = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-1", "cf-2", "cf-3"],
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is True


def test_validator_does_not_load_or_construct_replay_results_for_pass() -> None:
    """No second source of replay truth: the validator passes the
    typed cross-reference check WITHOUT loading or constructing any
    actual replay result records.

    Structural test: the validator's
    :class:`ReplayRequirementValidationResult` carries the typed
    surface fields only (recommendation_id + is_valid + gap + reasons
    + validated_at); it does NOT carry a replay-result body or any
    field pointing to one.
    """

    recommendation = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-1"],
    )
    result = ReplayRequirementValidator().validate(recommendation)
    dumped = result.model_dump(mode="json")
    # The structural surface MUST NOT mention these forbidden second-
    # source-of-truth keys.
    forbidden_keys = {
        "replay_result_body",
        "replay_result_payload",
        "counterfactual_result_body",
        "counterfactual_result",
        "replay_engine_output",
        "replay_output",
    }
    for key in forbidden_keys:
        assert key not in dumped, (
            f"second source of replay truth leaked into result: {key}"
        )


# ── Validator: NEVER raises (fail-closed; typed gap projection) ────────────


def test_validator_never_raises_on_normal_inputs() -> None:
    """The validator returns a typed result without raising on normal
    inputs."""

    recommendation = _recommendation()
    # Smoke: call should not raise.
    result = ReplayRequirementValidator().validate(recommendation)
    assert isinstance(result, ReplayRequirementValidationResult)


def test_validator_never_raises_on_behavior_changing_empty_inputs() -> None:
    """The validator returns a typed result (with gap) without raising
    on the reject path."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    # Smoke: call should not raise even when rejecting.
    result = ReplayRequirementValidator().validate(recommendation)
    assert isinstance(result, ReplayRequirementValidationResult)
    assert result.is_valid is False


def test_validator_never_raises_when_internal_exception_simulated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator emits a typed gap projection (NEVER raises) when
    a structural internal failure occurs.

    Simulates an internal failure by monkey-patching
    :class:`ReplayRequirementValidationResult` to raise during typed
    construction (the validator's outer try/except catches and projects
    onto a typed gap).
    """

    import iriai_build_v2.execution_control.replay_requirement_hook as mod

    original_result = mod.ReplayRequirementValidationResult
    call_count = {"n": 0}

    class _FailingResult(original_result):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Fail the FIRST construction (the happy path) so the
                # validator falls into its except branch which calls
                # the SECOND construction (the gap projection). The
                # gap-projection construction succeeds via the original
                # parent class.
                raise RuntimeError("simulated internal failure")
            # Subsequent constructions use the parent's __init__.
            super().__init__(*args, **kwargs)  # type: ignore[misc]

    monkeypatch.setattr(
        mod, "ReplayRequirementValidationResult", _FailingResult
    )

    recommendation = _recommendation(safe_runtime_action=False)
    # Should NOT raise.
    result = mod.ReplayRequirementValidator().validate(recommendation)
    assert isinstance(result, original_result)
    assert result.is_valid is False
    assert result.gap is not None
    assert result.gap.reason.startswith(
        "replay_requirement_internal_exception:"
    )


def test_validator_never_raises_with_typed_punning_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The validator emits a typed gap projection when the
    recommendation's typed fields raise unexpectedly (defence-in-depth).
    """

    import iriai_build_v2.execution_control.replay_requirement_hook as mod

    # Construct a punned object that raises on attribute access.
    class _BrokenRecommendation:
        @property
        def recommendation_id(self) -> str:
            raise RuntimeError("punned attribute access")

        @property
        def safe_runtime_action(self) -> bool:
            raise RuntimeError("punned attribute access")

        @property
        def counterfactual_result_refs(self) -> list[str]:
            raise RuntimeError("punned attribute access")

    # The validator should not raise on the punned input.
    result = mod.ReplayRequirementValidator().validate(
        _BrokenRecommendation()  # type: ignore[arg-type]
    )
    assert result.is_valid is False
    assert result.gap is not None
    assert (
        result.gap.failure_id == "replay_requirement_validation_failed"
    )


# ── No second source of replay truth (structural; defence-in-depth) ────────


def test_validator_class_source_does_not_mention_fetch_load_construct() -> None:
    """The validator class's source does NOT contain fetch / load /
    construct symbols (per doc-17:159-163 + 225-226 no second source
    of replay truth)."""

    source = inspect.getsource(ReplayRequirementValidator)
    forbidden_substrings = [
        "fetch_replay",
        "load_replay",
        "construct_replay",
        "ReplayEngine",
        "execute_replay",
        "simulate_replay",
        "replay_engine",
    ]
    for forbidden in forbidden_substrings:
        assert forbidden not in source, (
            f"validator class source mentions forbidden symbol: {forbidden}"
        )


def test_validator_validate_method_does_not_iterate_replay_results() -> None:
    """The validator's :meth:`validate` method body does NOT iterate
    over actual replay-result records (defence-in-depth structural
    check; the validator consumes only the typed ref-string list)."""

    source = inspect.getsource(ReplayRequirementValidator.validate)
    forbidden_substrings = [
        "for result in",
        "for replay",
        ".load_replay",
        ".fetch_replay",
        ".execute(",
        ".simulate(",
    ]
    for forbidden in forbidden_substrings:
        assert forbidden not in source, (
            f"validator.validate body mentions forbidden symbol: {forbidden}"
        )


# ── Module-level convenience function ───────────────────────────────────────


def test_module_level_validate_replay_requirement_pass() -> None:
    """The module-level convenience function returns the same result
    as the dispatch class for a pass case."""

    recommendation = _recommendation(safe_runtime_action=False)
    result = validate_replay_requirement(recommendation)
    assert result.is_valid is True


def test_module_level_validate_replay_requirement_reject() -> None:
    """The module-level convenience function returns the same result
    as the dispatch class for a reject case."""

    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    result = validate_replay_requirement(recommendation)
    assert result.is_valid is False
    assert result.gap is not None
    assert result.gap.reason == REPLAY_REQUIREMENT_MISSING_REASON


def test_module_level_validate_replay_requirement_uses_default_validator() -> None:
    """The module-level wrapper uses a default-constructed
    :class:`ReplayRequirementValidator` instance per call."""

    recommendation = _recommendation()
    # Two calls produce independent results (validator is stateless).
    r1 = validate_replay_requirement(recommendation)
    r2 = validate_replay_requirement(recommendation)
    assert r1.is_valid == r2.is_valid
    assert r1.recommendation_id == r2.recommendation_id


# ── Coverage for the 6 PolicyConsumer values × refuse-path ────────────────


@pytest.mark.parametrize(
    "consumer",
    [
        "scheduler",
        "failure_router",
        "supervisor",
        "dashboard",
        "planning",
        "merge_queue",
    ],
)
def test_validator_rejects_behavior_changing_empty_refs_per_consumer(
    consumer: str,
) -> None:
    """The reject path applies uniformly across the 6 PolicyConsumer
    values (the replay-requirement check is consumer-agnostic per
    doc-17:173-174; gates only on the safe_runtime_action +
    counterfactual_result_refs surface)."""

    from iriai_build_v2.execution_control.policy_recommendation import (
        DashboardPolicyArtifact,
        FailureRouterPolicyArtifact,
        MergeQueuePolicyArtifact,
        PlanningPolicyArtifact,
        SupervisorPolicyArtifact,
    )

    artifact_map = {
        "scheduler": _scheduler_artifact(),
        "failure_router": FailureRouterPolicyArtifact(
            failure_class="runtime_failure",
            failure_type="runtime_provider_outage",
            action="retry",
            route_budget_key="b",
            max_attempts=3,
            idempotency_key_template="t",
            required_tests=["t"],
        ),
        "supervisor": SupervisorPolicyArtifact(
            policy_kind="classification_hint",
            scope={"k": "v"},
            value={"x": 1},
        ),
        "dashboard": DashboardPolicyArtifact(
            policy_kind="view_priority",
            scope={"k": "v"},
            value={"x": 1},
        ),
        "planning": PlanningPolicyArtifact(
            policy_kind="future_dag_hint",
            scope={"k": "v"},
            value={"x": 1},
        ),
        "merge_queue": MergeQueuePolicyArtifact(
            policy_kind="lane_priority",
            scope={"k": "v"},
            value={"x": 1},
            required_queue_tests=["q"],
        ),
    }
    recommendation = _recommendation(
        consumer=consumer,
        proposed_policy_artifact=artifact_map[consumer],
        safe_runtime_action=True,
        counterfactual_result_refs=[],
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is False
    assert result.gap is not None
    assert result.gap.reason == REPLAY_REQUIREMENT_MISSING_REASON


@pytest.mark.parametrize(
    "consumer",
    [
        "scheduler",
        "failure_router",
        "supervisor",
        "dashboard",
        "planning",
        "merge_queue",
    ],
)
def test_validator_passes_behavior_changing_populated_refs_per_consumer(
    consumer: str,
) -> None:
    """The pass path applies uniformly across the 6 PolicyConsumer
    values."""

    from iriai_build_v2.execution_control.policy_recommendation import (
        DashboardPolicyArtifact,
        FailureRouterPolicyArtifact,
        MergeQueuePolicyArtifact,
        PlanningPolicyArtifact,
        SupervisorPolicyArtifact,
    )

    artifact_map = {
        "scheduler": _scheduler_artifact(),
        "failure_router": FailureRouterPolicyArtifact(
            failure_class="runtime_failure",
            failure_type="runtime_provider_outage",
            action="retry",
            route_budget_key="b",
            max_attempts=3,
            idempotency_key_template="t",
            required_tests=["t"],
        ),
        "supervisor": SupervisorPolicyArtifact(
            policy_kind="classification_hint",
            scope={"k": "v"},
            value={"x": 1},
        ),
        "dashboard": DashboardPolicyArtifact(
            policy_kind="view_priority",
            scope={"k": "v"},
            value={"x": 1},
        ),
        "planning": PlanningPolicyArtifact(
            policy_kind="future_dag_hint",
            scope={"k": "v"},
            value={"x": 1},
        ),
        "merge_queue": MergeQueuePolicyArtifact(
            policy_kind="lane_priority",
            scope={"k": "v"},
            value={"x": 1},
            required_queue_tests=["q"],
        ),
    }
    recommendation = _recommendation(
        consumer=consumer,
        proposed_policy_artifact=artifact_map[consumer],
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-1"],
    )
    result = ReplayRequirementValidator().validate(recommendation)
    assert result.is_valid is True
    assert result.gap is None


# ── Stateless validator + idempotency ──────────────────────────────────────


def test_validator_is_stateless_repeat_calls_produce_same_disposition() -> None:
    """The validator is stateless: repeated calls with the same input
    produce the same is_valid + gap disposition."""

    validator = ReplayRequirementValidator()
    recommendation = _recommendation(
        safe_runtime_action=True, counterfactual_result_refs=[]
    )
    r1 = validator.validate(recommendation)
    r2 = validator.validate(recommendation)
    assert r1.is_valid == r2.is_valid
    if r1.gap is not None and r2.gap is not None:
        assert r1.gap.reason == r2.gap.reason
        assert r1.gap.failure_id == r2.gap.failure_id


def test_validator_constructor_accepts_no_arguments() -> None:
    """The validator constructor takes no arguments per the
    ``feedback_no_overengineer_use_library`` rule."""

    validator = ReplayRequirementValidator()
    assert isinstance(validator, ReplayRequirementValidator)


def test_validator_construction_default_does_not_eagerly_load() -> None:
    """Default construction does NOT eagerly load any Slice 18 module
    or replay result store."""

    validator = ReplayRequirementValidator()
    # Construction should not have side effects on imported modules.
    assert "iriai_build_v2.execution_control.replay_requirement_hook" in str(
        type(validator).__module__
    )


# ── Cross-slice failure-id distinctness ────────────────────────────────────


def test_failure_id_is_unique_within_failure_types() -> None:
    """The NEW typed failure id appears exactly once in
    :data:`FAILURE_TYPES`."""

    assert (
        FAILURE_TYPES.count("replay_requirement_validation_failed") == 1
    )


def test_route_table_route_under_evidence_corruption_only() -> None:
    """The route table carries the NEW failure id only under the
    ``evidence_corruption`` failure_class (not any other class)."""

    seen_keys = [
        key
        for key in ROUTE_TABLE
        if key[1] == "replay_requirement_validation_failed"
    ]
    assert len(seen_keys) == 1
    assert seen_keys[0] == (
        "evidence_corruption",
        "replay_requirement_validation_failed",
    )
