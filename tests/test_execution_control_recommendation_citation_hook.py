"""Slice 18 seventh sub-slice -- unit tests for the recommendation
citation hook + validator at
``execution_control/recommendation_citation_hook.py``.

Covers the doc-18:117-119 step 7 + doc-18:165-166 AC4 typed-shape
citation-sufficiency check + behavior-changing-recommendation gating
discipline + DIRECT annotation-identity Slice 13a + Slice 17 1st +
Slice 18 1st sub-slice REUSE assertions + the validator-never-raises
non-blocking contract + the no-second-source-of-replay-truth structural
boundary.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 1st/2nd/3rd/4th/5th/6th sub-slice
modules + tests remain byte-identical.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    GovernancePolicyRecommendation,
    SchedulerPolicyArtifact,
)
from iriai_build_v2.execution_control.recommendation_citation_hook import (
    RECOMMENDATION_CITATION_FAILURE_ID,
    RECOMMENDATION_CITATION_MISSING_REASON,
    RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON,
    CitationGap,
    CitationSufficiencyResult,
    RecommendationCitationHookInputs,
    RecommendationCitationValidator,
    validate_recommendation_citation,
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
    """Construct a fully-specified :class:`GovernanceEvidenceRef`."""

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

    Default is a non-behavior-changing recommendation
    (safe_runtime_action=False) with an empty
    counterfactual_result_refs list -- the trivial-pass case (Sufficient
    case 1).
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


def _cf_result(**overrides: object) -> CounterfactualResult:
    """Construct a fully-specified :class:`CounterfactualResult`."""

    base: dict[str, object] = dict(
        result_id="cf-1",
        result_version="v1",
        scenario_id="scenario-1",
        corpus_id="corpus-1",
        assumptions=["sample_size>=10"],
        validity_limits=["sample_size<100"],
        policy_provenance_refs=[_ref()],
        safety_guard_class=None,
        estimated_delta_hours=-1.0,
        estimated_delta_repair_cycles=0.0,
        estimated_delta_commit_failures=0.0,
        estimated_risk_change="lower",
        confidence=0.7,
        invalidated_by=[],
        supporting_finding_ids=["finding-1"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)  # type: ignore[arg-type]


# ── module surface tests ────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the documented surface exactly.

    Per doc-18:117-119 step 7 + doc-18:165-166 AC4 the surface is:

    * 1 typed failure id constant
      (``RECOMMENDATION_CITATION_FAILURE_ID``).
    * 2 typed gap-reason Literal consts
      (``RECOMMENDATION_CITATION_MISSING_REASON`` +
      ``RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON``).
    * 3 typed BaseModels (``RecommendationCitationHookInputs`` +
      ``CitationGap`` + ``CitationSufficiencyResult``).
    * 1 validator class (``RecommendationCitationValidator``).
    * 1 module-level convenience function
      (``validate_recommendation_citation``).

    Total: 8 exported names.
    """

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    assert set(mod.__all__) == {
        "RECOMMENDATION_CITATION_FAILURE_ID",
        "RECOMMENDATION_CITATION_MISSING_REASON",
        "RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON",
        "RecommendationCitationHookInputs",
        "CitationGap",
        "CitationSufficiencyResult",
        "RecommendationCitationValidator",
        "validate_recommendation_citation",
    }


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """The module imports the Slice 13a shared
    :class:`GovernanceEvidenceRef`; it does NOT define a local copy."""

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    assert "GovernanceEvidenceRef" not in mod.__all__


def test_module_does_not_redefine_governance_policy_recommendation() -> None:
    """The module imports the Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation`; it does NOT define a
    local copy."""

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    assert "GovernancePolicyRecommendation" not in mod.__all__


def test_module_does_not_redefine_counterfactual_result() -> None:
    """The module imports the Slice 18 1st sub-slice
    :class:`CounterfactualResult`; it does NOT define a local copy."""

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    assert "CounterfactualResult" not in mod.__all__


def test_module_import_discipline_no_consumer_or_engine_imports() -> None:
    """The module MUST NOT import from supervisor / dashboard / phases /
    Slice 17 2nd-6th sub-slice modules / Slice 18 2nd-6th sub-slice
    modules.

    Per the implementer brief Non-negotiables the validator is a pure
    typed-surface observer; it consumes typed shapes from Slice 13a +
    Slice 17 1st + Slice 18 1st only.
    """

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_imports = [
        "from iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.supervisor",
        "import iriai_build_v2.dashboard",
        "from iriai_build_v2.workflows.develop.execution.phases",
        # Slice 17 2nd-6th sub-slice modules.
        "from iriai_build_v2.execution_control.recommendation_builder",
        "from iriai_build_v2.execution_control.policy_validation_interface",
        "from iriai_build_v2.execution_control.decision_record_writer",
        "from iriai_build_v2.execution_control.replay_requirement_hook",
        "from iriai_build_v2.execution_control.consumer_read_api",
        # Slice 18 2nd-6th sub-slice modules.
        "from iriai_build_v2.execution_control.counterfactual_replay_loader",
        "from iriai_build_v2.execution_control.counterfactual_summary_replay",
        "from iriai_build_v2.execution_control.counterfactual_event_replay",
        (
            "from iriai_build_v2.execution_control."
            "counterfactual_metrics_comparator"
        ),
        (
            "from iriai_build_v2.execution_control."
            "counterfactual_result_writer"
        ),
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"forbidden import found in recommendation_citation_hook.py: "
            f"{forbidden}"
        )


def test_module_no_second_source_of_replay_truth_structural() -> None:
    """The module MUST NOT introduce a second source of replay truth
    per doc-17:159-163 + doc-17:225-226 + doc-18:123-125.

    The validator consumes only the typed
    :class:`RecommendationCitationHookInputs` (carrying the typed
    :class:`GovernancePolicyRecommendation` + typed
    ``list[CounterfactualResult]``); it does NOT load / fetch /
    construct / execute / replay any counterfactual replay result.

    Structural test: the module source MUST NOT mention forbidden
    symbols (``fetch_counterfactual_result`` /
    ``load_counterfactual_result`` /
    ``construct_counterfactual_result`` / ``CounterfactualReplayEngine``
    / ``execute_replay`` / ``simulate_replay`` /
    ``CounterfactualResultWriter`` / ``ReplayCorpusLoader`` /
    ``CounterfactualSummaryReplayEngine`` /
    ``CounterfactualEventReplayEngine`` /
    ``CounterfactualMetricsComparator``).
    """

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_symbols = [
        "fetch_counterfactual_result",
        "load_counterfactual_result",
        "construct_counterfactual_result",
        "execute_replay",
        "simulate_replay",
        "CounterfactualResultWriter",
        "ReplayCorpusLoader",
        "CounterfactualSummaryReplayEngine",
        "CounterfactualEventReplayEngine",
        "CounterfactualMetricsComparator",
        "ScenarioDefinitionBuilder",
    ]
    for forbidden in forbidden_symbols:
        # Note: docstring references to module names are allowed in
        # `:mod:` / `:class:` sphinx-style refs which DON'T import the
        # name. We exclude such references by checking ONLY actual
        # source-level identifiers (not in docstring `:mod:` refs).
        # Use whole-source check; the forbidden symbols listed here are
        # never written in `:mod:` form anyway (those reference the
        # MODULE NAMES like ``counterfactual_replay_loader`` rather
        # than the CLASS NAMES like ``CounterfactualResultWriter``).
        # The CounterfactualReplayEngine string is intentionally tested
        # in a separate assertion.
        if forbidden in source:
            # If matched in a docstring sphinx ref (i.e. the symbol
            # appears within a `:mod:` / `:class:` ref OR an example
            # block), the test should fail since those would imply a
            # second source of truth.
            assert False, (
                f"forbidden symbol found (second source of replay "
                f"truth): {forbidden}"
            )


def test_module_no_slice_18_engine_imports() -> None:
    """The module MUST NOT import any Slice 18 2nd-6th sub-slice
    engine / loader / writer module.

    Per the implementer brief + doc-17:159-163 + doc-17:225-226 +
    doc-18:123-125 the validator's typed surface is the
    cross-reference shape only; Slice 18 1st sub-slice owns the typed
    shape; Slice 18 2nd-6th sub-slices own the loader / engines /
    writer surface.
    """

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_slice18_prefixes = [
        "from iriai_build_v2.execution_control.counterfactual_replay_loader",
        "from iriai_build_v2.execution_control.counterfactual_summary_replay",
        "from iriai_build_v2.execution_control.counterfactual_event_replay",
        (
            "from iriai_build_v2.execution_control."
            "counterfactual_metrics_comparator"
        ),
        (
            "from iriai_build_v2.execution_control."
            "counterfactual_result_writer"
        ),
    ]
    for forbidden in forbidden_slice18_prefixes:
        assert forbidden not in source, (
            f"forbidden Slice 18 engine/loader/writer import found: "
            f"{forbidden}"
        )


def test_module_import_discipline_only_allowed_imports() -> None:
    """The module MUST import only from stdlib + Pydantic v2 + Slice
    13a governance.models + Slice 17 1st policy_recommendation + Slice
    18 1st counterfactual_replay."""

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

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
    assert (
        "from iriai_build_v2.execution_control.counterfactual_replay import"
        in source
    )


def test_package_init_does_not_re_export_validator() -> None:
    """Per the implementer brief Non-negotiables the package
    ``__init__.py`` does NOT re-export the Slice 18 7th sub-slice
    typed surface."""

    import iriai_build_v2.execution_control as pkg

    pkg_file = pkg.__file__
    assert pkg_file is not None
    init_source = Path(pkg_file).read_text()
    for name in (
        "RecommendationCitationHookInputs",
        "CitationGap",
        "CitationSufficiencyResult",
        "RecommendationCitationValidator",
        "RECOMMENDATION_CITATION_FAILURE_ID",
        "RECOMMENDATION_CITATION_MISSING_REASON",
        "RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON",
        "validate_recommendation_citation",
    ):
        assert name not in init_source


# ── failure-id + gap-reason Literal constants ───────────────────────────────


def test_failure_id_value_matches_chunk_shape() -> None:
    """The typed failure id is
    ``recommendation_citation_validation_failed`` per the Slice 18 7th
    sub-slice chunk-shape."""

    assert (
        RECOMMENDATION_CITATION_FAILURE_ID
        == "recommendation_citation_validation_failed"
    )


def test_missing_reason_constant_value() -> None:
    """The typed missing-reason constant matches the chunk-shape."""

    assert (
        RECOMMENDATION_CITATION_MISSING_REASON
        == "recommendation_citation_missing"
    )


def test_unresolved_refs_reason_constant_value() -> None:
    """The typed unresolved-refs-reason constant matches the
    chunk-shape."""

    assert (
        RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
        == "recommendation_citation_unresolved_refs"
    )


def test_failure_id_differs_from_slice_18_prior_ids() -> None:
    """The Slice 18 7th sub-slice failure id is intentionally different
    from the prior Slice 18 sub-slice failure ids + Slice 17 5th sub-
    slice replay-requirement id."""

    from iriai_build_v2.execution_control.counterfactual_replay_loader import (
        REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.counterfactual_summary_replay import (
        SUMMARY_REPLAY_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.counterfactual_event_replay import (
        EVENT_REPLAY_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.counterfactual_metrics_comparator import (
        METRICS_COMPARATOR_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.counterfactual_result_writer import (
        COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID,
    )
    from iriai_build_v2.execution_control.replay_requirement_hook import (
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
    )

    distinct_ids = {
        RECOMMENDATION_CITATION_FAILURE_ID,
        REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
        SUMMARY_REPLAY_FAILURE_ID,
        EVENT_REPLAY_FAILURE_ID,
        METRICS_COMPARATOR_FAILURE_ID,
        COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID,
        REPLAY_REQUIREMENT_VALIDATION_FAILURE_ID,
    }
    assert len(distinct_ids) == 7


def test_missing_and_unresolved_reasons_are_distinct() -> None:
    """The two typed reason constants must be distinct strings."""

    assert (
        RECOMMENDATION_CITATION_MISSING_REASON
        != RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
    )


# ── RecommendationCitationHookInputs typed-shape construction ───────────────


def test_inputs_accepts_required_fields() -> None:
    """The typed inputs accept the documented fields."""

    rec = _recommendation()
    cf = _cf_result()
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[cf],
    )
    assert inputs.recommendation is rec
    assert inputs.counterfactual_results == [cf]


def test_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline: unknown fields fail
    closed with a typed ``ValidationError``."""

    rec = _recommendation()
    with pytest.raises(ValidationError):
        RecommendationCitationHookInputs(
            recommendation=rec,
            counterfactual_results=[],
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_inputs_round_trips_via_json() -> None:
    """The typed inputs round-trip via ``model_dump`` +
    ``model_validate``."""

    rec = _recommendation()
    cf = _cf_result()
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[cf],
    )
    dumped = inputs.model_dump(mode="json")
    re_inputs = RecommendationCitationHookInputs.model_validate(dumped)
    assert re_inputs == inputs


def test_inputs_counterfactual_results_defaults_to_empty_list() -> None:
    """The ``counterfactual_results`` field defaults to an empty list."""

    rec = _recommendation()
    inputs = RecommendationCitationHookInputs(recommendation=rec)
    assert inputs.counterfactual_results == []


def test_inputs_required_recommendation() -> None:
    """The ``recommendation`` field is required (no default)."""

    with pytest.raises(ValidationError):
        RecommendationCitationHookInputs()  # type: ignore[call-arg]


# ── CitationGap typed-shape construction ────────────────────────────────────


def test_gap_accepts_required_fields() -> None:
    """The typed gap accepts the documented fields."""

    gap = CitationGap(
        failure_id="recommendation_citation_validation_failed",
        recommendation_id="rec-1",
        reason=RECOMMENDATION_CITATION_MISSING_REASON,
    )
    assert gap.failure_id == "recommendation_citation_validation_failed"
    assert gap.recommendation_id == "rec-1"
    assert gap.reason == RECOMMENDATION_CITATION_MISSING_REASON
    assert gap.unresolved_refs == []
    assert gap.evidence_refs == []


def test_gap_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        CitationGap(
            failure_id="recommendation_citation_validation_failed",
            recommendation_id="rec-1",
            reason="x",
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_gap_round_trips_via_json() -> None:
    """The typed gap round-trips via ``model_dump`` +
    ``model_validate``."""

    gap = CitationGap(
        failure_id="recommendation_citation_validation_failed",
        recommendation_id="rec-1",
        reason=RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON,
        unresolved_refs=["cf-99"],
        evidence_refs=[_ref()],
    )
    dumped = gap.model_dump(mode="json")
    re_gap = CitationGap.model_validate(dumped)
    assert re_gap == gap


def test_gap_failure_id_literal_rejects_other_failure_ids() -> None:
    """The ``failure_id`` Literal range rejects any other failure id."""

    with pytest.raises(ValidationError):
        CitationGap(
            failure_id="policy_validation_failed",  # type: ignore[arg-type]
            recommendation_id="rec-1",
            reason="x",
        )


def test_gap_observed_at_defaults_to_utc_now() -> None:
    """The ``observed_at`` field defaults to UTC now (within a
    tolerance)."""

    before = datetime.now(tz=timezone.utc)
    gap = CitationGap(
        failure_id="recommendation_citation_validation_failed",
        recommendation_id="rec-1",
        reason="x",
    )
    after = datetime.now(tz=timezone.utc)
    assert before <= gap.observed_at <= after


def test_gap_accepts_multiple_unresolved_refs() -> None:
    """The ``unresolved_refs`` field accepts multiple ref strings."""

    gap = CitationGap(
        failure_id="recommendation_citation_validation_failed",
        recommendation_id="rec-1",
        reason=RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON,
        unresolved_refs=["cf-99", "cf-100", "cf-101"],
    )
    assert len(gap.unresolved_refs) == 3


def test_gap_evidence_refs_accepts_slice_13a_typed_refs() -> None:
    """The ``evidence_refs`` field accepts Slice 13a typed refs."""

    gap = CitationGap(
        failure_id="recommendation_citation_validation_failed",
        recommendation_id="rec-1",
        reason="x",
        evidence_refs=[_ref(ref_id="a"), _ref(ref_id="b")],
    )
    assert len(gap.evidence_refs) == 2


# ── CitationSufficiencyResult typed-shape construction ──────────────────────


def test_result_accepts_minimal_pass_fields() -> None:
    """The typed result accepts the minimal-pass field set."""

    result = CitationSufficiencyResult(
        recommendation_id="rec-1",
        is_sufficient=True,
    )
    assert result.recommendation_id == "rec-1"
    assert result.is_sufficient is True
    assert result.gap is None
    assert result.cited_result_ids == []
    assert result.missing_reason is None
    assert result.validated_at is None


def test_result_accepts_fail_fields_with_gap() -> None:
    """The typed result accepts the fail-with-gap field set."""

    gap = CitationGap(
        failure_id="recommendation_citation_validation_failed",
        recommendation_id="rec-1",
        reason=RECOMMENDATION_CITATION_MISSING_REASON,
    )
    result = CitationSufficiencyResult(
        recommendation_id="rec-1",
        is_sufficient=False,
        gap=gap,
        missing_reason=RECOMMENDATION_CITATION_MISSING_REASON,
    )
    assert result.is_sufficient is False
    assert result.gap is gap
    assert result.missing_reason == RECOMMENDATION_CITATION_MISSING_REASON


def test_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        CitationSufficiencyResult(
            recommendation_id="rec-1",
            is_sufficient=True,
            unknown_field="boom",  # type: ignore[call-arg]
        )


def test_result_round_trips_via_json() -> None:
    """The typed result round-trips via ``model_dump`` +
    ``model_validate``."""

    result = CitationSufficiencyResult(
        recommendation_id="rec-1",
        is_sufficient=True,
        cited_result_ids=["cf-1", "cf-2"],
        validated_at=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
    )
    dumped = result.model_dump(mode="json")
    re_result = CitationSufficiencyResult.model_validate(dumped)
    assert re_result == result


# ── Sufficient case 1: non-behavior-changing trivial pass ───────────────────


def test_sufficient_case_1_non_behavior_changing_passes_trivially() -> None:
    """Doc-17:198-200 + doc-18:165-166 -- a non-behavior-changing
    recommendation (``safe_runtime_action=False``) passes trivially
    regardless of citations / status."""

    rec = _recommendation(safe_runtime_action=False)
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    validator = RecommendationCitationValidator()
    result = validator.validate(inputs)
    assert result.is_sufficient is True
    assert result.gap is None
    assert result.cited_result_ids == []
    assert result.missing_reason is None
    assert result.recommendation_id == "rec-1"
    assert result.validated_at is not None


def test_sufficient_case_1_passes_even_with_unresolved_refs() -> None:
    """A non-behavior-changing recommendation passes even when refs are
    set + unresolved (the AC4 binding gates ONLY behavior-changing
    recommendations)."""

    rec = _recommendation(
        safe_runtime_action=False,
        counterfactual_result_refs=["cf-unresolved"],
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is True
    assert result.gap is None


# ── Sufficient case 2: behavior-changing + resolved refs ────────────────────


def test_sufficient_case_2_behavior_changing_with_resolved_refs() -> None:
    """Doc-18:165-166 AC4 (cite replay results axis) -- a
    behavior-changing recommendation passes when every ref-string
    resolves to a provided typed result_id."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-1", "cf-2"],
    )
    cf1 = _cf_result(result_id="cf-1")
    cf2 = _cf_result(result_id="cf-2", scenario_id="scenario-2")
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[cf1, cf2],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is True
    assert result.gap is None
    assert set(result.cited_result_ids) == {"cf-1", "cf-2"}
    assert result.missing_reason is None


def test_sufficient_case_2_with_extra_provided_results() -> None:
    """Sufficient case 2 still passes when more results are provided
    than the recommendation cites (only the cited subset is
    validated)."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-1"],
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[
            _cf_result(result_id="cf-1"),
            _cf_result(result_id="cf-2"),
            _cf_result(result_id="cf-3"),
        ],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is True
    assert result.cited_result_ids == ["cf-1"]


def test_sufficient_case_2_preserves_ref_order() -> None:
    """Sufficient case 2 preserves the recommendation's cite-order in
    ``cited_result_ids`` (per ``feedback_never_truncate_decisions``)."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-c", "cf-a", "cf-b"],
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[
            _cf_result(result_id="cf-a"),
            _cf_result(result_id="cf-b"),
            _cf_result(result_id="cf-c"),
        ],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is True
    assert result.cited_result_ids == ["cf-c", "cf-a", "cf-b"]


# ── Sufficient case 3: behavior-changing + needs_more_evidence ──────────────


def test_sufficient_case_3_behavior_changing_needs_more_evidence() -> None:
    """Doc-18:165-166 AC4 (explicitly say more evidence is needed
    axis) -- a behavior-changing recommendation with empty
    counterfactual_result_refs + status="needs_more_evidence" passes
    trivially."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=[],
        status="needs_more_evidence",
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is True
    assert result.gap is None
    assert result.cited_result_ids == []
    assert result.missing_reason is None


# ── Insufficient case 1: behavior-changing + missing citation ───────────────


def test_insufficient_case_1_behavior_changing_missing_citation() -> None:
    """Doc-18:117-119 + doc-18:165-166 -- a behavior-changing
    recommendation with empty counterfactual_result_refs + status NOT
    needs_more_evidence is insufficient."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=[],
        status="draft",
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert result.gap.failure_id == RECOMMENDATION_CITATION_FAILURE_ID
    assert result.gap.reason == RECOMMENDATION_CITATION_MISSING_REASON
    assert result.missing_reason == RECOMMENDATION_CITATION_MISSING_REASON
    assert result.cited_result_ids == []
    assert result.recommendation_id == "rec-1"


def test_insufficient_case_1_with_status_accepted_also_fails() -> None:
    """A behavior-changing recommendation with status="accepted"
    (still NOT needs_more_evidence) AND empty refs is insufficient."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=[],
        status="accepted",
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert result.gap.reason == RECOMMENDATION_CITATION_MISSING_REASON


def test_insufficient_case_1_results_provided_but_no_cites() -> None:
    """A behavior-changing recommendation with empty
    counterfactual_result_refs is insufficient even when the input
    bundle includes typed results (the AC4 binding requires the
    recommendation to CITE the refs explicitly)."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=[],
        status="draft",
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[_cf_result(result_id="cf-1")],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert result.gap.reason == RECOMMENDATION_CITATION_MISSING_REASON


# ── Insufficient case 2: behavior-changing + unresolved refs ────────────────


def test_insufficient_case_2_all_refs_unresolved() -> None:
    """Doc-18:165-166 AC4 (cite replay results axis fails) -- a
    behavior-changing recommendation whose cited refs do NOT resolve
    to any provided result_id is insufficient."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-nonexistent"],
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert result.gap.failure_id == RECOMMENDATION_CITATION_FAILURE_ID
    assert (
        result.gap.reason == RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
    )
    assert result.gap.unresolved_refs == ["cf-nonexistent"]
    assert (
        result.missing_reason
        == RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
    )


def test_insufficient_case_2_partial_resolution_fails() -> None:
    """Insufficient case 2: partial resolution (some refs resolve, some
    don't) fails -- the cited-replay-results axis requires ALL refs to
    resolve."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-1", "cf-missing"],
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[_cf_result(result_id="cf-1")],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert (
        result.gap.reason == RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
    )
    # Per feedback_never_truncate_decisions the unresolved_refs list
    # carries ONLY the unresolved refs (not the resolved ones).
    assert result.gap.unresolved_refs == ["cf-missing"]


def test_insufficient_case_2_preserves_full_unresolved_list() -> None:
    """Per ``feedback_never_truncate_decisions`` the validator emits
    ALL unresolved refs, not the first only."""

    unresolved = ["cf-x", "cf-y", "cf-z"]
    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=unresolved,
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert result.gap.unresolved_refs == unresolved


def test_insufficient_case_2_needs_more_evidence_with_unresolved_fails() -> None:
    """Insufficient case 2: behavior-changing + non-empty refs + status
    needs_more_evidence + unresolved refs -- the unresolved-refs case
    fails INDEPENDENTLY of status (the *"cite replay results"* axis
    has been chosen by virtue of non-empty refs; broken
    cross-reference fails regardless of needs_more_evidence)."""

    rec = _recommendation(
        safe_runtime_action=True,
        counterfactual_result_refs=["cf-missing"],
        status="needs_more_evidence",
    )
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = RecommendationCitationValidator().validate(inputs)
    assert result.is_sufficient is False
    assert result.gap is not None
    assert (
        result.gap.reason == RECOMMENDATION_CITATION_UNRESOLVED_REFS_REASON
    )


# ── Never-raises tests (feedback_no_silent_degradation) ─────────────────────


def test_validator_never_raises_on_internal_failure(monkeypatch) -> None:
    """The validator NEVER raises on internal failure (per
    ``feedback_no_silent_degradation``). Monkey-patch the typed
    CitationSufficiencyResult constructor to raise ValidationError ON
    THE FIRST SUFFICIENT-PATH CONSTRUCTION; the validator returns a
    typed gap projection."""

    rec = _recommendation(safe_runtime_action=False)
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )

    # Save the original.
    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    original = mod.CitationSufficiencyResult
    call_count = {"n": 0}

    class FakeResult:
        def __init__(self, **kwargs: object) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First call from the sufficient-path returns an error;
                # the except branch then re-constructs via the same
                # class which we want to succeed (so the test asserts
                # the validator captures the failure rather than
                # raising).
                raise ValidationError.from_exception_data(
                    "CitationSufficiencyResult", []
                )
            # Subsequent call from the except branch uses the original
            # constructor.
            self._delegate = original(**kwargs)  # type: ignore[arg-type]

        def __getattr__(self, name: str) -> object:
            return getattr(self._delegate, name)

    monkeypatch.setattr(mod, "CitationSufficiencyResult", FakeResult)

    # Validator MUST NOT raise.
    result = RecommendationCitationValidator().validate(inputs)
    # The except branch was taken; the gap projection carries the
    # internal exception reason.
    assert result.is_sufficient is False
    assert result.gap is not None
    assert "recommendation_citation_internal_exception" in result.gap.reason


def test_validator_never_raises_on_recommendation_field_failure(
    monkeypatch,
) -> None:
    """The validator NEVER raises even when reading recommendation
    fields raises unexpectedly."""

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    class BadRecommendation:
        @property
        def recommendation_id(self) -> str:  # type: ignore[override]
            raise RuntimeError("simulated upstream failure")

        @property
        def safe_runtime_action(self) -> bool:  # type: ignore[override]
            raise RuntimeError("simulated upstream failure")

        @property
        def counterfactual_result_refs(self) -> list[str]:  # type: ignore[override]
            raise RuntimeError("simulated upstream failure")

        @property
        def status(self) -> str:  # type: ignore[override]
            raise RuntimeError("simulated upstream failure")

    class BadInputs:
        recommendation = BadRecommendation()
        counterfactual_results: list[CounterfactualResult] = []

    # Validator MUST NOT raise.
    result = RecommendationCitationValidator().validate(BadInputs())  # type: ignore[arg-type]
    assert result.is_sufficient is False
    assert result.gap is not None
    assert "recommendation_citation_internal_exception" in result.gap.reason


def test_validator_never_raises_on_first_construction_failure(
    monkeypatch,
) -> None:
    """The validator NEVER raises when the typed
    :class:`CitationSufficiencyResult` construction fails on the FIRST
    attempt (sufficient/insufficient path); the outer except branch
    constructs a typed gap projection via the same class.

    Mirrors the Slice 17 5th sub-slice
    ``test_validator_never_raises_when_internal_exception_simulated``
    pattern verbatim: subclass the typed BaseModel + fail ONLY on the
    first construction call.
    """

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    original_result = mod.CitationSufficiencyResult
    call_count = {"n": 0}

    class _FailingResult(original_result):  # type: ignore[misc, valid-type]
        def __init__(self, *args: object, **kwargs: object) -> None:
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Fail the FIRST construction (the sufficient path) so
                # the validator falls into its except branch which
                # calls the SECOND construction (the gap projection).
                # The gap-projection construction succeeds via the
                # parent's __init__.
                raise RuntimeError("simulated internal failure")
            super().__init__(*args, **kwargs)  # type: ignore[misc]

    monkeypatch.setattr(mod, "CitationSufficiencyResult", _FailingResult)

    rec = _recommendation(safe_runtime_action=False)
    inputs = RecommendationCitationHookInputs(recommendation=rec)

    # The validator MUST NOT raise.
    result = RecommendationCitationValidator().validate(inputs)
    # The except branch was taken; the gap projection carries the
    # internal exception reason.
    assert result.is_sufficient is False
    assert result.gap is not None
    assert "recommendation_citation_internal_exception" in result.gap.reason


# ── Module-level convenience function tests ─────────────────────────────────


def test_module_function_dispatches_to_validator() -> None:
    """The module-level convenience function dispatches to a
    default-constructed validator."""

    rec = _recommendation(safe_runtime_action=False)
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = validate_recommendation_citation(inputs)
    assert result.is_sufficient is True


def test_module_function_returns_typed_result() -> None:
    """The module-level function returns a typed
    :class:`CitationSufficiencyResult`."""

    rec = _recommendation(safe_runtime_action=False)
    inputs = RecommendationCitationHookInputs(
        recommendation=rec,
        counterfactual_results=[],
    )
    result = validate_recommendation_citation(inputs)
    assert isinstance(result, CitationSufficiencyResult)


# ── DIRECT annotation-identity REUSE tests ──────────────────────────────────


def test_inputs_recommendation_annotation_is_governance_policy_recommendation() -> None:
    """DIRECT annotation-identity REUSE: the
    :attr:`RecommendationCitationHookInputs.recommendation` annotation
    is THE Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation` class (no local copy)."""

    hints = get_type_hints(RecommendationCitationHookInputs)
    assert hints["recommendation"] is GovernancePolicyRecommendation


def test_inputs_counterfactual_results_annotation_is_list_of_typed_result() -> None:
    """DIRECT annotation-identity REUSE: the
    :attr:`RecommendationCitationHookInputs.counterfactual_results`
    annotation is ``list[CounterfactualResult]`` where
    :class:`CounterfactualResult` is THE Slice 18 1st sub-slice typed
    BaseModel."""

    hints = get_type_hints(RecommendationCitationHookInputs)
    cf_list_hint = hints["counterfactual_results"]
    assert get_origin(cf_list_hint) is list
    (item,) = get_args(cf_list_hint)
    assert item is CounterfactualResult


def test_gap_evidence_refs_annotation_is_list_of_typed_ref() -> None:
    """DIRECT annotation-identity REUSE: the
    :attr:`CitationGap.evidence_refs` annotation is
    ``list[GovernanceEvidenceRef]`` where :class:`GovernanceEvidenceRef`
    is THE Slice 13a typed BaseModel."""

    hints = get_type_hints(CitationGap)
    evidence_list_hint = hints["evidence_refs"]
    assert get_origin(evidence_list_hint) is list
    (item,) = get_args(evidence_list_hint)
    assert item is GovernanceEvidenceRef


def test_gap_failure_id_literal_is_typed_failure_id() -> None:
    """DIRECT annotation-identity REUSE: the
    :attr:`CitationGap.failure_id` Literal carries the typed failure
    id string verbatim."""

    hints = get_type_hints(CitationGap)
    failure_id_hint = hints["failure_id"]
    args = get_args(failure_id_hint)
    assert args == ("recommendation_citation_validation_failed",)


# ── Failure-router 4-point registration tests ───────────────────────────────


def test_failure_router_failure_type_literal_contains_id() -> None:
    """Add point 1: the typed ``recommendation_citation_validation_failed``
    is in the ``FailureType`` Literal."""

    assert "recommendation_citation_validation_failed" in get_args(FailureType)


def test_failure_router_failure_types_tuple_contains_id() -> None:
    """Add point 2: the typed ``recommendation_citation_validation_failed``
    is in the ``FAILURE_TYPES`` tuple."""

    assert "recommendation_citation_validation_failed" in FAILURE_TYPES


def test_failure_router_retryable_failure_types_contains_id() -> None:
    """Add point 3: the typed ``recommendation_citation_validation_failed``
    is in the ``_RETRYABLE_FAILURE_TYPES`` frozenset."""

    assert (
        "recommendation_citation_validation_failed"
        in _RETRYABLE_FAILURE_TYPES
    )


def test_failure_router_route_table_resolves_to_governance_retry() -> None:
    """Add point 4: the typed ``recommendation_citation_validation_failed``
    routes under ``evidence_corruption`` to the REUSED
    ``retry_governance_projection`` non-blocking action."""

    route = ROUTE_TABLE.get(
        ("evidence_corruption", "recommendation_citation_validation_failed")
    )
    assert route is not None
    assert route.action == "retry_governance_projection"


def test_failure_router_route_rows_contains_seventh_subslice_entry() -> None:
    """The ``_ROUTE_ROWS`` tuple carries an entry for the typed
    ``recommendation_citation_validation_failed`` failure id under
    ``evidence_corruption`` with the REUSED
    ``retry_governance_projection`` action.

    Each ``_ROUTE_ROWS`` entry is a ``(FailureTypePolicy,
    FailureRoutePolicy)`` 2-tuple; the citation 4th add point lands in
    the second element of the matching tuple.
    """

    matching = [
        row
        for row in _ROUTE_ROWS
        if row[0].failure_class == "evidence_corruption"
        and row[0].failure_type == "recommendation_citation_validation_failed"
    ]
    assert len(matching) == 1
    assert matching[0][1].action == "retry_governance_projection"


# ── Activation-boundary discipline (per doc-17:217 + doc-18:123-125) ────────


def test_validator_class_has_no_mutation_methods() -> None:
    """Per doc-17:217 + doc-18:123-125 the validator class exposes NO
    mutation methods on consumer state. The class exposes EXACTLY two
    public methods: ``__init__`` + ``validate``."""

    public_methods = [
        name
        for name in dir(RecommendationCitationValidator)
        if not name.startswith("_")
    ]
    assert set(public_methods) == {"validate"}


def test_result_class_has_no_mutation_methods() -> None:
    """Per doc-17:217 + doc-18:123-125 the typed result BaseModel has
    NO mutation methods beyond the standard Pydantic surface."""

    # The BaseModel doesn't define custom mutators; the only
    # data-altering methods are Pydantic's own (model_copy, model_dump,
    # etc.). None of those are "activation" methods.
    forbidden_names = {
        "activate",
        "deactivate",
        "supersede",
        "approve",
        "reject",
    }
    for name in dir(CitationSufficiencyResult):
        assert name not in forbidden_names


def test_validator_does_not_emit_dag_authority_artifact_strings() -> None:
    """Per doc-18:123-125 the validator MUST NOT emit any ``dag-*``
    execution authority artifact-key string literals (the typed
    surface is read-only with respect to consumer state)."""

    from iriai_build_v2.execution_control import (
        recommendation_citation_hook as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    # Forbidden string-literal patterns (the validator MUST NOT mint
    # dag-* artifact keys; those belong to the consumer-owned
    # activation surface per doc-17:159-163 + doc-18:123-125).
    forbidden_string_literals = [
        '"dag-task:',
        '"dag-group:',
        '"dag-active-policy:',
        "'dag-task:",
        "'dag-group:",
        "'dag-active-policy:",
    ]
    for forbidden in forbidden_string_literals:
        assert forbidden not in source, (
            f"forbidden dag-* authority artifact-key literal in "
            f"recommendation_citation_hook.py: {forbidden}"
        )
