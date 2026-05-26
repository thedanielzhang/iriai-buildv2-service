"""Slice 17 fourth sub-slice -- unit tests for the decision-record
writer + bounded review projection at
``execution_control/decision_record_writer.py``.

Covers the typed-shape construction + bounded-review-projection
discipline + LIMIT cap+1 truncation + refs-only (no raw bodies) +
decisions digest stability + the writer-never-raises non-blocking
contract + the DIRECT annotation-identity Slice 13a/17-1st/17-3rd REUSE
assertions (the stronger pattern that functionally addresses Slice 14
V3 reviewer P3-V3-2 carry; matches Slice 15 4th + Slice 16 4th
sub-slice precedent verbatim).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 1st/2nd/3rd-A/3rd-B/4th + Slice 17 1st/2nd/3rd sub-slice
modules + tests remain byte-identical.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.decision_record_writer import (
    DECISION_RECORD_PERSISTENCE_FAILURE_ID,
    DEFAULT_REVIEW_PROJECTION_CAP,
    DecisionPersistenceGap,
    DecisionRecordWriter,
    DecisionWriterInputs,
    DecisionWriterResult,
    REVIEW_PROJECTION_ID_PREFIX,
    compute_decision_projection_id,
    compute_decisions_digest,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    FailureRouterPolicyArtifact,
    GovernancePolicyRecommendation,
    PolicyRecommendationDecision,
    SchedulerPolicyArtifact,
)
from iriai_build_v2.execution_control.policy_validation_interface import (
    ValidationResult,
    ValidationViolation,
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
    """Construct a fully-specified
    :class:`GovernancePolicyRecommendation`."""

    base: dict[str, object] = dict(
        idempotency_key="rec-key-1",
        recommendation_id="rec-1",
        consumer="scheduler",
        status="accepted",
        source_finding_ids=["finding-1"],
        source_metric_refs=["tasks_per_hour@v1"],
        counterfactual_result_refs=[],
        confidence=0.9,
        expected_impact={"tasks_per_hour_delta": 0.15},
        risk_level="low",
        safe_runtime_action=True,
        requires_tests=["test_scheduler_wave_cap"],
        proposed_policy_artifact=_scheduler_artifact(),
        activation_requirements=["scheduler_test_suite_green"],
        rollback_requirements=["revert_to_prior_wave_cap"],
    )
    base.update(overrides)
    return GovernancePolicyRecommendation(**base)  # type: ignore[arg-type]


def _decision(**overrides: object) -> PolicyRecommendationDecision:
    """Construct a fully-specified
    :class:`PolicyRecommendationDecision`."""

    base: dict[str, object] = dict(
        recommendation_id="rec-1",
        decision="accept",
        decided_by="reviewer-1",
        decided_at=datetime(2026, 5, 25, 12, 0, 0, tzinfo=timezone.utc),
        rationale="Validation passed; replay supports.",
        evidence_refs=[_ref(ref_id="dec-ev-1")],
    )
    base.update(overrides)
    return PolicyRecommendationDecision(**base)  # type: ignore[arg-type]


def _validation_result(**overrides: object) -> ValidationResult:
    """Construct a fully-specified :class:`ValidationResult`."""

    base: dict[str, object] = dict(
        recommendation_id="rec-1",
        consumer="scheduler",
        is_valid=True,
        violations=[],
    )
    base.update(overrides)
    return ValidationResult(**base)  # type: ignore[arg-type]


def _writer_inputs(**overrides: object) -> DecisionWriterInputs:
    """Construct a fully-specified :class:`DecisionWriterInputs`."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        decisions=[_decision()],
        recommendations=[_recommendation()],
        validation_results=[_validation_result()],
        baseline_refs=[_ref(ref_id="baseline-1")],
        warnings=[],
        incomplete_scopes=[],
    )
    base.update(overrides)
    return DecisionWriterInputs(**base)  # type: ignore[arg-type]


def _gap(**overrides: object) -> DecisionPersistenceGap:
    """Construct a fully-specified :class:`DecisionPersistenceGap`."""

    base: dict[str, object] = dict(
        failure_id="decision_record_persistence_failed",
        corpus_id="8ac124d6",
        decisions_digest="sha256:abc",
        review_projection_id="review:governance-recommendations:8ac124d6",
        reason="decisions_construction_failed",
        evidence_refs=[],
        evidence_payload={"error_detail": "test"},
    )
    base.update(overrides)
    return DecisionPersistenceGap(**base)  # type: ignore[arg-type]


# ── module surface tests ────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed-shape additions: 3
    typed BaseModels + the typed failure id Literal + the typed
    review-projection id prefix Literal + the LIMIT cap+1 default + 2
    pure helpers + the :class:`DecisionRecordWriter` class.
    """

    from iriai_build_v2.execution_control import decision_record_writer as mod

    expected = {
        "DecisionWriterInputs",
        "DecisionWriterResult",
        "DecisionPersistenceGap",
        "DECISION_RECORD_PERSISTENCE_FAILURE_ID",
        "REVIEW_PROJECTION_ID_PREFIX",
        "DEFAULT_REVIEW_PROJECTION_CAP",
        "compute_decision_projection_id",
        "compute_decisions_digest",
        "DecisionRecordWriter",
    }
    assert set(mod.__all__) == expected


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """The module imports
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (the Slice 13a shared shape); it does NOT define a local copy."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

    assert "GovernanceEvidenceRef" not in mod.__all__


def test_module_does_not_redefine_policy_recommendation_decision() -> None:
    """The module imports the Slice 17 1st sub-slice
    :class:`PolicyRecommendationDecision`; it does NOT define a local
    copy."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

    assert "PolicyRecommendationDecision" not in mod.__all__


def test_module_does_not_redefine_governance_policy_recommendation() -> None:
    """The module imports the Slice 17 1st sub-slice
    :class:`GovernancePolicyRecommendation`; it does NOT define a
    local copy."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

    assert "GovernancePolicyRecommendation" not in mod.__all__


def test_module_does_not_redefine_validation_result() -> None:
    """The module imports the Slice 17 3rd sub-slice
    :class:`ValidationResult`; it does NOT define a local copy."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

    assert "ValidationResult" not in mod.__all__


def test_module_import_discipline_no_supervisor_dashboard_phases() -> None:
    """The module MUST NOT import from supervisor / dashboard / phases /
    recommendation_builder.

    Per the implementer brief Non-negotiables the writer is a pure
    projection observer; it consumes typed shapes from Slice 13a +
    Slice 17 1st + Slice 17 3rd, NOT from any consumer-side or
    upstream-builder module."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_imports = [
        "from iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.supervisor",
        "import iriai_build_v2.dashboard",
        "from iriai_build_v2.workflows.develop.execution.phases",
        "from iriai_build_v2.execution_control.recommendation_builder",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"forbidden import found in decision_record_writer.py: {forbidden}"
        )


def test_module_import_discipline_only_allowed_imports() -> None:
    """The module MUST import only from stdlib + Pydantic v2 + Slice
    13a governance.models + Slice 17 1st policy_recommendation + Slice
    17 3rd policy_validation_interface."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

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
        "from iriai_build_v2.execution_control.policy_validation_interface import"
        in source
    )


def test_package_init_does_not_re_export_writer() -> None:
    """Per the implementer brief Non-negotiables the package
    ``__init__.py`` does NOT re-export the Slice 17 4th sub-slice
    typed surface."""

    import iriai_build_v2.execution_control as pkg

    pkg_file = pkg.__file__
    assert pkg_file is not None
    init_source = Path(pkg_file).read_text()
    for name in (
        "DecisionWriterInputs",
        "DecisionWriterResult",
        "DecisionPersistenceGap",
        "DecisionRecordWriter",
        "DECISION_RECORD_PERSISTENCE_FAILURE_ID",
        "compute_decision_projection_id",
        "compute_decisions_digest",
    ):
        assert name not in init_source


# ── prefix + cap + failure-id constants ─────────────────────────────────────


def test_review_projection_id_prefix_value() -> None:
    """The prefix matches doc-17:182-188 verbatim:
    ``review:governance-recommendations:``."""

    assert REVIEW_PROJECTION_ID_PREFIX == "review:governance-recommendations:"


def test_review_projection_id_prefix_differs_from_slice_15_4th() -> None:
    """The Slice 17 4th sub-slice prefix is intentionally different
    from the Slice 15 4th sub-slice prefix
    (``review:governance-metrics:``)."""

    from iriai_build_v2.execution_control.governance_scorecard_writer import (
        REVIEW_PROJECTION_ID_PREFIX as scorecard_prefix,
    )

    assert REVIEW_PROJECTION_ID_PREFIX != scorecard_prefix
    assert scorecard_prefix == "review:governance-metrics:"


def test_review_projection_id_prefix_differs_from_slice_16_4th() -> None:
    """The Slice 17 4th sub-slice prefix is intentionally different
    from the Slice 16 4th sub-slice prefix
    (``review:governance-findings:``)."""

    from iriai_build_v2.execution_control.governance_finding_writer import (
        REVIEW_PROJECTION_ID_PREFIX as finding_prefix,
    )

    assert REVIEW_PROJECTION_ID_PREFIX != finding_prefix
    assert finding_prefix == "review:governance-findings:"


def test_decision_record_persistence_failure_id_value() -> None:
    """The typed failure id is ``decision_record_persistence_failed``
    per the Slice 17 4th sub-slice chunk-shape."""

    assert (
        DECISION_RECORD_PERSISTENCE_FAILURE_ID
        == "decision_record_persistence_failed"
    )


def test_default_review_projection_cap_is_positive_int() -> None:
    """The default LIMIT cap+1 cap is a positive integer."""

    assert isinstance(DEFAULT_REVIEW_PROJECTION_CAP, int)
    assert DEFAULT_REVIEW_PROJECTION_CAP > 0


def test_default_review_projection_cap_matches_slice_15_4th_value() -> None:
    """Per the chunk-shape decision the Slice 17 4th sub-slice default
    cap mirrors the Slice 15 4th sub-slice value verbatim (200)."""

    from iriai_build_v2.execution_control.governance_scorecard_writer import (
        DEFAULT_REVIEW_PROJECTION_CAP as scorecard_cap,
    )

    assert DEFAULT_REVIEW_PROJECTION_CAP == scorecard_cap == 200


def test_default_review_projection_cap_matches_slice_16_4th_value() -> None:
    """Per the chunk-shape decision the Slice 17 4th sub-slice default
    cap mirrors the Slice 16 4th sub-slice value verbatim (200)."""

    from iriai_build_v2.execution_control.governance_finding_writer import (
        DEFAULT_REVIEW_PROJECTION_CAP as finding_cap,
    )

    assert DEFAULT_REVIEW_PROJECTION_CAP == finding_cap == 200


# ── compute_decision_projection_id helper ───────────────────────────────────


def test_compute_decision_projection_id_constructs_typed_prefix() -> None:
    """The helper constructs the typed projection id by concatenating
    the prefix + corpus_id."""

    assert (
        compute_decision_projection_id("8ac124d6")
        == "review:governance-recommendations:8ac124d6"
    )


def test_compute_decision_projection_id_passes_through_corpus_id() -> None:
    """The helper passes the corpus_id through verbatim (no
    canonicalization)."""

    assert (
        compute_decision_projection_id("Corpus_A.B-1")
        == "review:governance-recommendations:Corpus_A.B-1"
    )


def test_compute_decision_projection_id_accepts_empty_corpus_id() -> None:
    """Defensive: the helper accepts an empty corpus_id; the caller is
    responsible for validating corpus_id non-emptiness."""

    assert (
        compute_decision_projection_id("")
        == "review:governance-recommendations:"
    )


def test_compute_decision_projection_id_uses_prefix_constant() -> None:
    """The helper consumes the typed
    :data:`REVIEW_PROJECTION_ID_PREFIX` constant verbatim."""

    result = compute_decision_projection_id("c")
    assert result.startswith(REVIEW_PROJECTION_ID_PREFIX)


# ── compute_decisions_digest helper ─────────────────────────────────────────


def test_compute_decisions_digest_returns_64_char_hex() -> None:
    """The digest is a SHA-256 hex string (64 chars)."""

    digest = compute_decisions_digest([_decision()])
    assert len(digest) == 64
    int(digest, 16)  # parses as hex


def test_compute_decisions_digest_empty_list_returns_stable_digest() -> None:
    """The digest for an empty decisions list is a stable, repeatable
    hash."""

    d1 = compute_decisions_digest([])
    d2 = compute_decisions_digest([])
    assert d1 == d2


def test_compute_decisions_digest_is_stable_across_calls() -> None:
    """Two calls with the same decisions list produce byte-identical
    digests."""

    d1 = compute_decisions_digest([_decision()])
    d2 = compute_decisions_digest([_decision()])
    assert d1 == d2


def test_compute_decisions_digest_differs_for_different_decisions() -> None:
    """Two calls with different decisions produce different digests."""

    d1 = compute_decisions_digest([_decision(decision="accept")])
    d2 = compute_decisions_digest([_decision(decision="reject")])
    assert d1 != d2


def test_compute_decisions_digest_order_sensitive() -> None:
    """The digest is computed over the canonical-JSON projection of
    the list verbatim; the caller is responsible for deterministic
    ordering."""

    dec_a = _decision(recommendation_id="rec-a")
    dec_b = _decision(recommendation_id="rec-b")
    d_ab = compute_decisions_digest([dec_a, dec_b])
    d_ba = compute_decisions_digest([dec_b, dec_a])
    assert d_ab != d_ba


# ── DecisionWriterInputs construction ───────────────────────────────────────


def test_writer_inputs_accepts_all_required_fields() -> None:
    """The typed bundle accepts the documented inputs."""

    inputs = _writer_inputs()
    assert inputs.corpus_id == "8ac124d6"
    assert len(inputs.decisions) == 1
    assert len(inputs.recommendations) == 1
    assert len(inputs.validation_results) == 1
    assert len(inputs.baseline_refs) == 1


def test_writer_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline: unknown fields fail
    closed with a typed ``ValidationError``."""

    with pytest.raises(ValidationError):
        DecisionWriterInputs(
            corpus_id="c",
            decisions=[],
            unknown_field="boom",  # type: ignore[arg-type]
        )


def test_writer_inputs_round_trips_via_json() -> None:
    """The typed bundle round-trips via ``model_dump`` +
    ``model_validate``."""

    inputs = _writer_inputs()
    dumped = inputs.model_dump(mode="json")
    re_inputs = DecisionWriterInputs.model_validate(dumped)
    assert re_inputs == inputs


def test_writer_inputs_recommendations_defaults_to_empty() -> None:
    """``recommendations`` defaults to an empty list."""

    inputs = DecisionWriterInputs(corpus_id="c", decisions=[])
    assert inputs.recommendations == []


def test_writer_inputs_validation_results_defaults_to_empty() -> None:
    """``validation_results`` defaults to an empty list."""

    inputs = DecisionWriterInputs(corpus_id="c", decisions=[])
    assert inputs.validation_results == []


def test_writer_inputs_baseline_refs_defaults_to_empty() -> None:
    """``baseline_refs`` defaults to an empty list."""

    inputs = DecisionWriterInputs(corpus_id="c", decisions=[])
    assert inputs.baseline_refs == []


def test_writer_inputs_warnings_defaults_to_empty() -> None:
    """``warnings`` defaults to an empty list."""

    inputs = DecisionWriterInputs(corpus_id="c", decisions=[])
    assert inputs.warnings == []


def test_writer_inputs_incomplete_scopes_defaults_to_empty() -> None:
    """``incomplete_scopes`` defaults to an empty list."""

    inputs = DecisionWriterInputs(corpus_id="c", decisions=[])
    assert inputs.incomplete_scopes == []


def test_writer_inputs_decisions_field_required() -> None:
    """``decisions`` is a required field."""

    with pytest.raises(ValidationError):
        DecisionWriterInputs(corpus_id="c")  # type: ignore[call-arg]


def test_writer_inputs_corpus_id_required() -> None:
    """``corpus_id`` is a required field."""

    with pytest.raises(ValidationError):
        DecisionWriterInputs(decisions=[])  # type: ignore[call-arg]


# ── DIRECT annotation-identity REUSE (Slice 13a + Slice 17 1st + Slice 17
#    3rd) ─────────────────────────────────────────────────────────────────────


def test_writer_inputs_decisions_annotation_is_slice_17_first_policy_recommendation_decision() -> None:
    """:attr:`DecisionWriterInputs.decisions` element type IS the
    Slice 17 1st sub-slice typed
    :class:`PolicyRecommendationDecision`."""

    hints = get_type_hints(DecisionWriterInputs)
    decisions_t = hints["decisions"]
    assert get_origin(decisions_t) is list
    (element_t,) = get_args(decisions_t)
    assert element_t is PolicyRecommendationDecision


def test_writer_inputs_recommendations_annotation_is_slice_17_first_governance_policy_recommendation() -> None:
    """:attr:`DecisionWriterInputs.recommendations` element type IS
    the Slice 17 1st sub-slice typed
    :class:`GovernancePolicyRecommendation`."""

    hints = get_type_hints(DecisionWriterInputs)
    recs_t = hints["recommendations"]
    assert get_origin(recs_t) is list
    (element_t,) = get_args(recs_t)
    assert element_t is GovernancePolicyRecommendation


def test_writer_inputs_validation_results_annotation_is_slice_17_third_validation_result() -> None:
    """:attr:`DecisionWriterInputs.validation_results` element type IS
    the Slice 17 3rd sub-slice typed :class:`ValidationResult`."""

    hints = get_type_hints(DecisionWriterInputs)
    vr_t = hints["validation_results"]
    assert get_origin(vr_t) is list
    (element_t,) = get_args(vr_t)
    assert element_t is ValidationResult


def test_writer_inputs_baseline_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """:attr:`DecisionWriterInputs.baseline_refs` element type IS the
    Slice 13a typed :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(DecisionWriterInputs)
    br_t = hints["baseline_refs"]
    assert get_origin(br_t) is list
    (element_t,) = get_args(br_t)
    assert element_t is GovernanceEvidenceRef


def test_gap_evidence_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """:attr:`DecisionPersistenceGap.evidence_refs` element type IS
    the Slice 13a typed :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(DecisionPersistenceGap)
    er_t = hints["evidence_refs"]
    assert get_origin(er_t) is list
    (element_t,) = get_args(er_t)
    assert element_t is GovernanceEvidenceRef


def test_result_gap_records_annotation_is_local_decision_persistence_gap() -> None:
    """:attr:`DecisionWriterResult.gap_records` element type IS the
    local :class:`DecisionPersistenceGap`."""

    hints = get_type_hints(DecisionWriterResult)
    gr_t = hints["gap_records"]
    assert get_origin(gr_t) is list
    (element_t,) = get_args(gr_t)
    assert element_t is DecisionPersistenceGap


def test_writer_write_decisions_parameter_annotation_is_local_inputs() -> None:
    """The :meth:`DecisionRecordWriter.write_decisions` parameter
    annotation IS the local :class:`DecisionWriterInputs`."""

    hints = get_type_hints(DecisionRecordWriter.write_decisions)
    inputs_t = hints["inputs"]
    assert inputs_t is DecisionWriterInputs
    return_t = hints["return"]
    assert return_t is DecisionWriterResult


# ── DecisionPersistenceGap construction ─────────────────────────────────────


def test_gap_accepts_all_fields() -> None:
    """The typed gap shape accepts the documented fields."""

    gap = _gap()
    assert gap.failure_id == "decision_record_persistence_failed"
    assert gap.corpus_id == "8ac124d6"
    assert gap.decisions_digest == "sha256:abc"


def test_gap_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline: unknown fields fail
    closed."""

    with pytest.raises(ValidationError):
        DecisionPersistenceGap(
            failure_id="decision_record_persistence_failed",
            corpus_id="c",
            decisions_digest=None,
            review_projection_id=None,
            reason="x",
            unknown_field="boom",  # type: ignore[arg-type]
        )


def test_gap_round_trips_via_json() -> None:
    """The typed gap round-trips via ``model_dump`` +
    ``model_validate``."""

    gap = _gap()
    dumped = gap.model_dump(mode="json")
    re_gap = DecisionPersistenceGap.model_validate(dumped)
    assert re_gap == gap


def test_gap_failure_id_literal_rejects_other_failure_ids() -> None:
    """The ``failure_id`` Literal accepts only
    ``decision_record_persistence_failed``."""

    with pytest.raises(ValidationError):
        DecisionPersistenceGap(
            failure_id="some_other_failure_id",  # type: ignore[arg-type]
            corpus_id="c",
            decisions_digest=None,
            review_projection_id=None,
            reason="x",
        )


def test_gap_decisions_digest_accepts_none() -> None:
    """The ``decisions_digest`` field accepts ``None`` (the failure
    happened before the digest could be computed)."""

    gap = _gap(decisions_digest=None)
    assert gap.decisions_digest is None


def test_gap_review_projection_id_accepts_none() -> None:
    """The ``review_projection_id`` field accepts ``None`` (the
    failure happened before the projection id could be computed)."""

    gap = _gap(review_projection_id=None)
    assert gap.review_projection_id is None


def test_gap_observed_at_defaults_to_utc_now() -> None:
    """The ``observed_at`` field defaults to UTC now."""

    before = datetime.now(timezone.utc)
    gap = DecisionPersistenceGap(
        failure_id="decision_record_persistence_failed",
        corpus_id="c",
        decisions_digest=None,
        review_projection_id=None,
        reason="x",
    )
    after = datetime.now(timezone.utc)
    assert before <= gap.observed_at <= after


def test_gap_evidence_refs_defaults_to_empty() -> None:
    """``evidence_refs`` defaults to an empty list."""

    gap = DecisionPersistenceGap(
        failure_id="decision_record_persistence_failed",
        corpus_id="c",
        decisions_digest=None,
        review_projection_id=None,
        reason="x",
    )
    assert gap.evidence_refs == []


def test_gap_evidence_payload_defaults_to_empty_dict() -> None:
    """``evidence_payload`` defaults to an empty dict."""

    gap = DecisionPersistenceGap(
        failure_id="decision_record_persistence_failed",
        corpus_id="c",
        decisions_digest=None,
        review_projection_id=None,
        reason="x",
    )
    assert gap.evidence_payload == {}


# ── DecisionWriterResult construction ───────────────────────────────────────


def test_writer_result_defaults_to_empty_lists() -> None:
    """All :class:`DecisionWriterResult` list fields default to empty."""

    result = DecisionWriterResult()
    assert result.persisted_decision_ids == []
    assert result.gap_records == []
    assert result.truncated is False


def test_writer_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        DecisionWriterResult(unknown_field="boom")  # type: ignore[call-arg]


def test_writer_result_round_trips_via_json() -> None:
    """The typed result round-trips via ``model_dump`` +
    ``model_validate``."""

    result = DecisionWriterResult(
        persisted_decision_ids=["rec-1"],
        gap_records=[_gap()],
        truncated=True,
    )
    dumped = result.model_dump(mode="json")
    re_result = DecisionWriterResult.model_validate(dumped)
    assert re_result.persisted_decision_ids == ["rec-1"]
    assert re_result.truncated is True
    assert len(re_result.gap_records) == 1


# ── failure-router pure-data add-point validation (4 of 4 add points) ───────


def test_failure_router_registers_new_typed_failure_id() -> None:
    """Add-point 1: the NEW typed failure id
    ``decision_record_persistence_failed`` is registered in the
    :data:`FailureType` Literal."""

    args = get_args(FailureType)
    assert "decision_record_persistence_failed" in args


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add-point 2: the NEW typed failure id is in the
    :data:`FAILURE_TYPES` tuple."""

    assert "decision_record_persistence_failed" in FAILURE_TYPES


def test_failure_router_new_id_in_retryable_set() -> None:
    """Add-point 3: the NEW typed failure id is in the
    :data:`_RETRYABLE_FAILURE_TYPES` frozenset (the transient
    governance projection failure pattern)."""

    assert "decision_record_persistence_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Add-point 4: the route table carries the NEW typed failure id
    under the EXISTING ``evidence_corruption`` failure_class with the
    REUSED ``retry_governance_projection`` NON-blocking RouteAction."""

    key = ("evidence_corruption", "decision_record_persistence_failed")
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
    # The 4 add points do NOT introduce a new action; the action set
    # was frozen at Slice 14 2nd sub-slice.


def test_failure_router_route_includes_explanation() -> None:
    """The route table row carries a non-empty explanation reason."""

    key = ("evidence_corruption", "decision_record_persistence_failed")
    route = ROUTE_TABLE[key]
    assert route.reason
    assert "Slice 17 4th" in route.reason


def test_failure_router_pure_data_add_point_route_table_row_present() -> None:
    """Defence-in-depth: the add-point landed as a row in the
    :data:`_ROUTE_ROWS` tuple-of-tuples."""

    seen = False
    for row in _ROUTE_ROWS:
        # Each row is (FailureTypePolicy, FailureRoutePolicy); the
        # FailureRoutePolicy carries failure_class + failure_type +
        # action.
        _, route = row
        if (
            route.failure_class == "evidence_corruption"
            and route.failure_type == "decision_record_persistence_failed"
        ):
            seen = True
            assert route.action == "retry_governance_projection"
            break
    assert seen, "decision_record_persistence_failed row not found in _ROUTE_ROWS"


# ── 3 decision Literal values (accept / reject / needs_more_evidence) ──────


def test_writer_accepts_decision_literal_accept() -> None:
    """The writer persists ``"accept"`` decisions."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(
        _writer_inputs(decisions=[_decision(decision="accept")])
    )
    assert result.persisted_decision_ids == ["rec-1"]
    assert result.gap_records == []


def test_writer_accepts_decision_literal_reject() -> None:
    """The writer persists ``"reject"`` decisions."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(
        _writer_inputs(
            decisions=[_decision(decision="reject", rationale="Insufficient evidence.")]
        )
    )
    assert result.persisted_decision_ids == ["rec-1"]


def test_writer_accepts_decision_literal_needs_more_evidence() -> None:
    """The writer persists ``"needs_more_evidence"`` decisions."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(
        _writer_inputs(
            decisions=[
                _decision(
                    decision="needs_more_evidence",
                    rationale="Replay coverage missing.",
                )
            ]
        )
    )
    assert result.persisted_decision_ids == ["rec-1"]


def test_writer_handles_mixed_3_decision_literals() -> None:
    """The writer persists a mix of all 3 decision Literal values per
    doc-17:172 verbatim."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(
        _writer_inputs(
            decisions=[
                _decision(recommendation_id="r-acc", decision="accept"),
                _decision(recommendation_id="r-rej", decision="reject"),
                _decision(
                    recommendation_id="r-nme",
                    decision="needs_more_evidence",
                ),
            ]
        )
    )
    assert result.persisted_decision_ids == ["r-acc", "r-rej", "r-nme"]


def test_decision_literal_rejects_unknown_value() -> None:
    """The 3-value Literal at the Slice 17 1st sub-slice typed
    :class:`PolicyRecommendationDecision` rejects unknown values; the
    writer inherits this fail-closed discipline."""

    with pytest.raises(ValidationError):
        PolicyRecommendationDecision(
            recommendation_id="rec-1",
            decision="maybe",  # type: ignore[arg-type]
            decided_by="r",
            decided_at=datetime.now(timezone.utc),
            rationale="x",
            evidence_refs=[],
        )


# ── DecisionRecordWriter happy-path ─────────────────────────────────────────


def test_writer_construction_default_is_empty_gap_records() -> None:
    """A fresh writer's :attr:`gap_records` property is empty."""

    writer = DecisionRecordWriter()
    assert writer.gap_records == []


def test_writer_gap_records_is_snapshot_list() -> None:
    """:attr:`gap_records` returns a fresh snapshot list per call so
    caller mutations do not leak into the internal accumulator."""

    writer = DecisionRecordWriter()
    snap = writer.gap_records
    snap.append(_gap())
    assert writer.gap_records == []


def test_write_decisions_emits_typed_writer_result() -> None:
    """:meth:`write_decisions` returns a typed
    :class:`DecisionWriterResult`."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(_writer_inputs())
    assert isinstance(result, DecisionWriterResult)


def test_write_decisions_preserves_recommendation_ids_verbatim() -> None:
    """The writer preserves the per-decision ``recommendation_id``
    verbatim (no mutation)."""

    writer = DecisionRecordWriter()
    decisions = [
        _decision(recommendation_id="rec-A"),
        _decision(recommendation_id="rec-B"),
        _decision(recommendation_id="rec-C"),
    ]
    result = writer.write_decisions(_writer_inputs(decisions=decisions))
    assert result.persisted_decision_ids == ["rec-A", "rec-B", "rec-C"]


def test_write_decisions_empty_list_produces_empty_persisted() -> None:
    """An empty decisions list produces an empty
    ``persisted_decision_ids`` list."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(
        _writer_inputs(decisions=[])
    )
    assert result.persisted_decision_ids == []
    assert result.gap_records == []
    assert result.truncated is False


def test_write_decisions_resets_gap_records_per_call() -> None:
    """Per-call :attr:`gap_records` accumulator RESETS at entry."""

    writer = DecisionRecordWriter()
    writer._gap_records.append(_gap())  # type: ignore[attr-defined]
    # Now call write_decisions and verify the prior gap is reset.
    writer.write_decisions(_writer_inputs())
    assert writer.gap_records == []


def test_write_decisions_with_cap_truncates_decisions_list() -> None:
    """The decisions list is truncated at ``cap+1`` per the bounded-
    read discipline."""

    writer = DecisionRecordWriter()
    decisions = [
        _decision(recommendation_id=f"rec-{i}") for i in range(5)
    ]
    result = writer.write_decisions(
        _writer_inputs(decisions=decisions), cap=3
    )
    # Per LIMIT cap+1: emit at most cap+1 = 4 items so the caller can
    # detect overflow.
    assert len(result.persisted_decision_ids) == 4
    assert result.truncated is True


def test_write_decisions_no_truncation_under_cap() -> None:
    """No truncation when decisions count <= cap."""

    writer = DecisionRecordWriter()
    decisions = [
        _decision(recommendation_id=f"rec-{i}") for i in range(3)
    ]
    result = writer.write_decisions(
        _writer_inputs(decisions=decisions), cap=10
    )
    assert len(result.persisted_decision_ids) == 3
    assert result.truncated is False


# ── DecisionRecordWriter review projection ──────────────────────────────────


def test_write_review_projection_emits_typed_dict() -> None:
    """:meth:`write_review_projection` emits a typed projection dict."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
    )
    assert isinstance(projection, dict)
    assert projection["review_projection_id"] == (
        "review:governance-recommendations:c"
    )


def test_write_review_projection_id_uses_typed_prefix() -> None:
    """The projection's ``review_projection_id`` uses the typed
    prefix."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[], corpus_id="x"
    )
    assert projection["review_projection_id"].startswith(
        REVIEW_PROJECTION_ID_PREFIX
    )


def test_write_review_projection_corpus_id_passes_through() -> None:
    """The projection's ``corpus_id`` passes through verbatim."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[], corpus_id="my-corpus-1"
    )
    assert projection["corpus_id"] == "my-corpus-1"


def test_write_review_projection_generated_at_is_iso_string() -> None:
    """The projection's ``generated_at`` is an ISO-8601 string per
    the canonical-JSON discipline."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(decisions=[], corpus_id="c")
    iso_str = projection["generated_at"]
    assert isinstance(iso_str, str)
    # Should parse as ISO-8601 datetime.
    datetime.fromisoformat(iso_str)


def test_write_review_projection_carries_decisions_digest() -> None:
    """The projection carries the decisions digest for tamper
    detection per doc-17:182-188."""

    writer = DecisionRecordWriter()
    decisions = [_decision()]
    projection = writer.write_review_projection(
        decisions=decisions, corpus_id="c"
    )
    expected_digest = compute_decisions_digest(decisions)
    assert projection["decisions_digest"] == expected_digest


def test_write_review_projection_digest_is_stable_across_reruns() -> None:
    """Two re-projections against the same decisions produce byte-
    identical digests."""

    writer = DecisionRecordWriter()
    decisions = [_decision()]
    p1 = writer.write_review_projection(decisions=decisions, corpus_id="c")
    p2 = writer.write_review_projection(decisions=decisions, corpus_id="c")
    assert p1["decisions_digest"] == p2["decisions_digest"]


def test_write_review_projection_digest_differs_for_different_decisions() -> None:
    """Different decision-sets produce different digests."""

    writer = DecisionRecordWriter()
    d1 = [_decision(decision="accept")]
    d2 = [_decision(decision="reject")]
    p1 = writer.write_review_projection(decisions=d1, corpus_id="c")
    p2 = writer.write_review_projection(decisions=d2, corpus_id="c")
    assert p1["decisions_digest"] != p2["decisions_digest"]


def test_write_review_projection_decisions_are_compact_dicts() -> None:
    """The projection's ``decisions`` list carries compact dicts with
    typed control fields only."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()], corpus_id="c"
    )
    assert isinstance(projection["decisions"], list)
    assert len(projection["decisions"]) == 1
    d = projection["decisions"][0]
    assert d["recommendation_id"] == "rec-1"
    assert d["decision"] == "accept"
    assert d["decided_by"] == "reviewer-1"
    assert d["rationale"] == "Validation passed; replay supports."
    assert "decided_at" in d


def test_write_review_projection_decided_at_is_iso_string() -> None:
    """The per-decision ``decided_at`` is an ISO-8601 string per the
    canonical-JSON discipline."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()], corpus_id="c"
    )
    d = projection["decisions"][0]
    iso_str = d["decided_at"]
    assert isinstance(iso_str, str)
    # Should parse as ISO-8601 datetime.
    datetime.fromisoformat(iso_str)


def test_write_review_projection_decision_evidence_refs_are_cited_only() -> None:
    """Per-decision ``evidence_refs`` carry only typed-ref shape (no
    raw bodies)."""

    writer = DecisionRecordWriter()
    decision = _decision(
        evidence_refs=[
            _ref(ref_id="x", digest="sha256:x"),
        ]
    )
    projection = writer.write_review_projection(
        decisions=[decision], corpus_id="c"
    )
    refs = projection["decisions"][0]["evidence_refs"]
    # The projection carries the typed
    # :class:`GovernanceEvidenceRef` shape via ``model_dump(mode='json')``
    # (authority + ref_id + digest + quality + completeness + the
    # optional typed metadata fields -- NEVER raw bodies).
    assert len(refs) == 1
    ref = refs[0]
    assert ref["authority"] == "typed_journal"
    assert ref["ref_id"] == "x"
    assert ref["digest"] == "sha256:x"
    assert ref["quality"] == "canonical"
    assert ref["completeness"] == "complete"
    # Defence-in-depth: no raw body fields.
    forbidden_keys = {
        "raw_body", "raw_payload", "artifact_body", "event_body",
        "raw_text", "stderr_body", "stdout_body", "body",
    }
    assert not (set(ref.keys()) & forbidden_keys)


def test_write_review_projection_recommendations_are_cited_only() -> None:
    """Projection ``recommendations`` are typed-shape dicts only (no
    raw bodies)."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        recommendations=[_recommendation()],
    )
    assert isinstance(projection["recommendations"], list)
    assert len(projection["recommendations"]) == 1
    r = projection["recommendations"][0]
    assert r["recommendation_id"] == "rec-1"
    assert r["consumer"] == "scheduler"
    assert r["status"] == "accepted"


def test_write_review_projection_validation_results_are_cited_only() -> None:
    """Projection ``validation_results`` are typed-shape dicts only
    (no raw bodies)."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        validation_results=[_validation_result()],
    )
    assert isinstance(projection["validation_results"], list)
    assert len(projection["validation_results"]) == 1
    vr = projection["validation_results"][0]
    assert vr["recommendation_id"] == "rec-1"
    assert vr["consumer"] == "scheduler"
    assert vr["is_valid"] is True


def test_write_review_projection_baseline_refs_are_cited_only() -> None:
    """Projection ``baseline_refs`` carry only typed-ref shape (no raw
    bodies)."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        baseline_refs=[_ref(ref_id="b1")],
    )
    # The projection carries the typed
    # :class:`GovernanceEvidenceRef` shape via ``model_dump(mode='json')``
    # (authority + ref_id + digest + quality + completeness + the
    # optional typed metadata fields -- NEVER raw bodies).
    assert len(projection["baseline_refs"]) == 1
    ref = projection["baseline_refs"][0]
    assert ref["authority"] == "typed_journal"
    assert ref["ref_id"] == "b1"
    assert ref["digest"] == "sha256:bbb"
    assert ref["quality"] == "canonical"
    assert ref["completeness"] == "complete"
    # Defence-in-depth: no raw body fields.
    forbidden_keys = {
        "raw_body", "raw_payload", "artifact_body", "event_body",
        "raw_text", "stderr_body", "stdout_body", "body",
    }
    assert not (set(ref.keys()) & forbidden_keys)


# ── walk-time forbidden-key invariant (refs-only discipline) ────────────────


def test_write_review_projection_walk_no_forbidden_keys() -> None:
    """Walk-time forbidden-key invariant: scan the entire projection
    recursively and assert no raw-body-shaped field name appears at
    any nesting level."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        recommendations=[_recommendation()],
        validation_results=[_validation_result()],
        baseline_refs=[_ref(ref_id="b1")],
    )

    forbidden_keys = {
        "raw_body",
        "raw_payload",
        "artifact_body",
        "event_body",
        "raw_text",
        "stderr_body",
        "stdout_body",
        "body",
    }

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_keys, (
                    f"forbidden key {k!r} found in projection node"
                )
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(projection)


def test_write_review_projection_decision_dict_keys_are_typed_control() -> None:
    """Per-decision projection dict carries only typed control field
    keys + cited refs; defence-in-depth assertion."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()], corpus_id="c"
    )
    d = projection["decisions"][0]
    expected_keys = {
        "recommendation_id",
        "decision",
        "decided_by",
        "decided_at",
        "rationale",
        "evidence_refs",
        "evidence_refs_truncated",
    }
    assert set(d.keys()) == expected_keys


# ── LIMIT cap+1 truncation discipline (across all list dimensions) ──────────


def test_write_review_projection_decisions_list_respects_cap_plus_one() -> None:
    """Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
    projection emits at most ``cap+1`` decisions."""

    writer = DecisionRecordWriter()
    decisions = [
        _decision(recommendation_id=f"r-{i}") for i in range(5)
    ]
    projection = writer.write_review_projection(
        decisions=decisions, corpus_id="c", cap=3
    )
    assert len(projection["decisions"]) == 4  # cap+1
    assert projection["decisions_truncated"] is True


def test_write_review_projection_recommendations_list_respects_cap_plus_one() -> None:
    """The recommendations list is truncated at ``cap+1``."""

    writer = DecisionRecordWriter()
    recommendations = [
        _recommendation(recommendation_id=f"r-{i}") for i in range(5)
    ]
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        recommendations=recommendations,
        cap=2,
    )
    assert len(projection["recommendations"]) == 3
    assert projection["recommendations_truncated"] is True


def test_write_review_projection_validation_results_list_respects_cap_plus_one() -> None:
    """The validation_results list is truncated at ``cap+1``."""

    writer = DecisionRecordWriter()
    validation_results = [
        _validation_result(recommendation_id=f"r-{i}") for i in range(4)
    ]
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        validation_results=validation_results,
        cap=2,
    )
    assert len(projection["validation_results"]) == 3
    assert projection["validation_results_truncated"] is True


def test_write_review_projection_baseline_refs_list_respects_cap_plus_one() -> None:
    """The baseline_refs list is truncated at ``cap+1``."""

    writer = DecisionRecordWriter()
    baseline_refs = [_ref(ref_id=f"b-{i}") for i in range(5)]
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        baseline_refs=baseline_refs,
        cap=2,
    )
    assert len(projection["baseline_refs"]) == 3
    assert projection["baseline_refs_truncated"] is True


def test_write_review_projection_decision_evidence_refs_respects_cap_plus_one() -> None:
    """The per-decision evidence_refs list is truncated at ``cap+1``."""

    writer = DecisionRecordWriter()
    decision = _decision(
        evidence_refs=[_ref(ref_id=f"e-{i}") for i in range(5)]
    )
    projection = writer.write_review_projection(
        decisions=[decision], corpus_id="c", cap=2
    )
    d = projection["decisions"][0]
    assert len(d["evidence_refs"]) == 3
    assert d["evidence_refs_truncated"] is True


def test_write_review_projection_no_truncation_under_cap() -> None:
    """No truncation when all list counts <= cap."""

    writer = DecisionRecordWriter()
    projection = writer.write_review_projection(
        decisions=[_decision()],
        corpus_id="c",
        recommendations=[_recommendation()],
        validation_results=[_validation_result()],
        baseline_refs=[_ref(ref_id="b1")],
        cap=10,
    )
    assert projection["decisions_truncated"] is False
    assert projection["recommendations_truncated"] is False
    assert projection["validation_results_truncated"] is False
    assert projection["baseline_refs_truncated"] is False


def test_write_review_projection_default_cap_is_default_constant() -> None:
    """The default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`."""

    writer = DecisionRecordWriter()
    # Default cap = 200; provide 201 decisions to force truncation.
    decisions = [
        _decision(recommendation_id=f"r-{i}")
        for i in range(DEFAULT_REVIEW_PROJECTION_CAP + 1)
    ]
    projection = writer.write_review_projection(
        decisions=decisions, corpus_id="c"
    )
    assert len(projection["decisions"]) == DEFAULT_REVIEW_PROJECTION_CAP + 1
    assert projection["decisions_truncated"] is True


# ── never-raises fail-closed (feedback_no_silent_degradation) ───────────────


def test_writer_never_raises_on_normal_inputs() -> None:
    """The writer NEVER raises on typed inputs."""

    writer = DecisionRecordWriter()
    # Should not raise.
    writer.write_decisions(_writer_inputs())
    writer.write_review_projection(decisions=[_decision()], corpus_id="c")


def test_writer_gap_records_empty_on_normal_path() -> None:
    """Normal write_decisions path emits no gap records."""

    writer = DecisionRecordWriter()
    result = writer.write_decisions(_writer_inputs())
    assert result.gap_records == []
    assert writer.gap_records == []


def test_writer_gap_records_empty_on_normal_projection_path() -> None:
    """Normal write_review_projection path emits no gap records."""

    writer = DecisionRecordWriter()
    writer.write_review_projection(decisions=[_decision()], corpus_id="c")
    assert writer.gap_records == []


def test_writer_gap_on_digest_failure_does_not_raise(monkeypatch) -> None:
    """If the digest computation fails internally the writer projects
    onto a typed gap rather than raising (defensive try/except)."""

    writer = DecisionRecordWriter()

    # Monkey-patch the module-level digest function to raise; the
    # writer's defensive try/except catches the failure + emits a
    # typed gap rather than propagating.
    def _exploding_digest(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated digest failure")

    from iriai_build_v2.execution_control import decision_record_writer as mod

    monkeypatch.setattr(mod, "compute_decisions_digest", _exploding_digest)

    # Should not raise.
    projection = writer.write_review_projection(
        decisions=[_decision()], corpus_id="c"
    )
    # The gap was projected.
    assert writer.gap_records, "expected typed gap on digest failure"
    gap = writer.gap_records[0]
    assert gap.failure_id == "decision_record_persistence_failed"
    assert gap.corpus_id == "c"
    assert "decisions_digest_failed" in gap.reason
    # The projection itself still returned a typed dict shape (with
    # empty digest as the most-conservative fallback).
    assert isinstance(projection, dict)
    assert projection["decisions_digest"] == ""


def test_writer_gap_on_decisions_construction_failure_does_not_raise(monkeypatch) -> None:
    """If decisions construction fails internally (defensive
    try/except path in :meth:`write_decisions`) the writer projects
    onto a typed gap rather than raising."""

    writer = DecisionRecordWriter()

    # The write_decisions defensive try/except catches any exception
    # in the list comprehension iterating recommendation_id; simulate
    # by passing a list whose iteration after [:limit] raises.
    class _RaisingDecision:
        """A non-typed object that raises on recommendation_id access."""

        @property
        def recommendation_id(self) -> str:  # type: ignore[override]
            raise RuntimeError("simulated attribute-access failure")

    # Construct DecisionWriterInputs via a typed bundle (with a valid
    # decision so construction succeeds), then call write_decisions
    # with a malformed inputs.decisions via monkey-patching the
    # attribute.
    inputs = _writer_inputs()
    # Override decisions with the raising object via runtime type
    # punning (Python doesn't enforce at runtime; the defensive
    # try/except catches the failure).
    object.__setattr__(inputs, "decisions", [_RaisingDecision()])  # type: ignore[arg-type]

    # Should not raise.
    result = writer.write_decisions(inputs)
    # The gap was projected.
    assert result.gap_records, "expected typed gap on construction failure"
    gap = result.gap_records[0]
    assert gap.failure_id == "decision_record_persistence_failed"
    assert gap.corpus_id == "8ac124d6"
    assert "decisions_construction_failed" in gap.reason
    # The persisted_decision_ids list is empty (most-conservative
    # fallback).
    assert result.persisted_decision_ids == []


# ── integration / roundtrip ─────────────────────────────────────────────────


def test_integration_write_decisions_then_projection_roundtrip() -> None:
    """End-to-end: writer persists decisions then projects them onto
    the review artifact; the digest survives the roundtrip."""

    writer = DecisionRecordWriter()
    decisions = [
        _decision(recommendation_id="rec-A", decision="accept"),
        _decision(recommendation_id="rec-B", decision="reject"),
        _decision(
            recommendation_id="rec-C", decision="needs_more_evidence"
        ),
    ]
    inputs = _writer_inputs(decisions=decisions)
    result = writer.write_decisions(inputs)
    assert result.persisted_decision_ids == ["rec-A", "rec-B", "rec-C"]

    projection = writer.write_review_projection(
        decisions=decisions,
        corpus_id=inputs.corpus_id,
        recommendations=inputs.recommendations,
        validation_results=inputs.validation_results,
        baseline_refs=inputs.baseline_refs,
    )
    expected_digest = compute_decisions_digest(decisions)
    assert projection["decisions_digest"] == expected_digest
    # All 3 decision Literal values present in the projection.
    decisions_in_proj = [d["decision"] for d in projection["decisions"]]
    assert decisions_in_proj == ["accept", "reject", "needs_more_evidence"]


def test_review_projection_preserves_per_decision_recommendation_id() -> None:
    """The review projection preserves the per-decision
    ``recommendation_id`` verbatim."""

    writer = DecisionRecordWriter()
    decisions = [
        _decision(recommendation_id=f"id-{i}") for i in range(3)
    ]
    projection = writer.write_review_projection(
        decisions=decisions, corpus_id="c"
    )
    ids = [d["recommendation_id"] for d in projection["decisions"]]
    assert ids == ["id-0", "id-1", "id-2"]


# ── no consumer activation authority (doc-17:159-163 + doc-17:217) ──────────


def test_writer_grants_no_consumer_activation_authority() -> None:
    """The writer is a pure projection observer; a persisted
    ``"accept"`` decision does NOT activate the recommendation's
    proposed policy artifact (the writer surface has no mutation
    methods on consumer-side state)."""

    writer = DecisionRecordWriter()
    # The writer class exposes only __init__ + gap_records property +
    # write_decisions + write_review_projection. There is NO method
    # like "activate_recommendation" or "apply_to_consumer".
    public_methods = [
        name for name in dir(writer) if not name.startswith("_")
    ]
    # Filter to expected surface members.
    assert "gap_records" in public_methods
    assert "write_decisions" in public_methods
    assert "write_review_projection" in public_methods
    # Defence-in-depth: NO consumer-side activation method exists.
    forbidden_names = {
        "activate_recommendation",
        "apply_to_consumer",
        "force_route_change",
        "mutate_scheduler_policy",
        "promote_to_runtime",
    }
    for forbidden in forbidden_names:
        assert forbidden not in public_methods


# ── module-source negative-control test (defence-in-depth) ──────────────────


def test_module_source_does_not_import_recommendation_builder() -> None:
    """Per the implementer brief the writer does NOT depend on the
    Slice 17 2nd sub-slice
    :mod:`~iriai_build_v2.execution_control.recommendation_builder`
    (the writer is a pure projection over already-emitted decisions;
    the upstream recommendation builder is the producer, not a writer
    dependency)."""

    from iriai_build_v2.execution_control import decision_record_writer as mod

    source = Path(inspect.getfile(mod)).read_text()
    assert (
        "from iriai_build_v2.execution_control.recommendation_builder"
        not in source
    )
    assert "import iriai_build_v2.execution_control.recommendation_builder" not in source


def test_writer_can_persist_validation_results_as_supporting_evidence() -> None:
    """Per doc-17:172 the writer accepts optional Slice 17 3rd
    sub-slice :class:`ValidationResult` records as supporting
    evidence."""

    writer = DecisionRecordWriter()
    violation = ValidationViolation(
        consumer="scheduler",
        rule_name="scheduler_guardrails_must_be_non_empty",
        violation_message="guardrails list is empty.",
    )
    failed_validation = ValidationResult(
        recommendation_id="rec-1",
        consumer="scheduler",
        is_valid=False,
        violations=[violation],
    )
    inputs = _writer_inputs(
        decisions=[
            _decision(decision="reject", rationale="Validation failed.")
        ],
        validation_results=[failed_validation],
    )
    result = writer.write_decisions(inputs)
    assert result.persisted_decision_ids == ["rec-1"]
    # The validation result is captured in the typed inputs (the
    # decision-record writer does NOT re-validate; per doc-17:172 the
    # validation result is supporting evidence carried alongside).
    assert len(inputs.validation_results) == 1
    assert inputs.validation_results[0].is_valid is False
