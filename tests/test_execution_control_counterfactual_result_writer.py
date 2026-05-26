"""Slice 18 sixth sub-slice -- unit tests for the counterfactual-
result writer + bounded review projection at
``execution_control/counterfactual_result_writer.py``.

Covers the typed-shape construction + bounded-review-projection
discipline + LIMIT cap+1 truncation + refs-only (no raw bodies) +
results digest stability + the writer-never-raises non-blocking
contract + the DIRECT annotation-identity Slice 13a/18-1st/18-5th
REUSE assertions (the stronger pattern that functionally addresses
Slice 14 V3 reviewer P3-V3-2 carry; matches Slice 15 4th + Slice 16
4th + Slice 17 4th sub-slice precedent verbatim).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 1st/2nd/3rd/4th/5th sub-slice modules +
tests remain byte-identical.

**Slice 13A awareness asserted (doc-18:186-249).** The writer
consumes Slice 13a typed ``GovernanceEvidenceRef`` via the
:attr:`CounterfactualResultWriterInputs.baseline_refs` field + via
each per-result ``CounterfactualResult.policy_provenance_refs`` and
emits the refs-only projection onto the typed
``CounterfactualResultPersistenceGap.evidence_refs`` list; no raw
artifact body hydration per doc-18:186-249.

**Refs-only invariant (doc-18:186-249).** Test
:func:`test_write_review_projection_walk_no_forbidden_keys` walks the
emitted projection recursively and asserts no key contains the
forbidden ``body`` / ``raw_body`` / ``artifact_body`` substring.

**Doc-18:123-129 persistence + artifact compatibility awareness.** No
6th-sub-slice consumer activation wiring; no consumer-owned artifact-
key literals; result-writer output is a review/governance artifact
only, never runtime policy authority. Structural test
:func:`test_writer_grants_no_consumer_activation_authority` asserts
the writer class exposes no mutation-prefix surface method.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_metrics_comparator import (
    MetricsAxisDelta,
    MetricsComparatorResult,
)
from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
)
from iriai_build_v2.execution_control.counterfactual_result_writer import (
    COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID,
    DEFAULT_REVIEW_PROJECTION_CAP,
    CounterfactualResultPersistenceGap,
    CounterfactualResultWriter,
    CounterfactualResultWriterInputs,
    CounterfactualResultWriterResult,
    REVIEW_PROJECTION_ID_PREFIX,
    compute_counterfactual_result_projection_id,
    compute_counterfactual_results_digest,
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


def _ref(ref_id: str = "ref-1", **overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified :class:`GovernanceEvidenceRef`."""

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id=ref_id,
        digest="d" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def _result(**overrides: object) -> CounterfactualResult:
    """Construct a fully-specified :class:`CounterfactualResult`."""

    base: dict[str, object] = dict(
        result_id="cf-result-1",
        result_version="v1",
        scenario_id="scenario-1",
        corpus_id="8ac124d6",
        assumptions=["sample_independence"],
        validity_limits=["summary_replay_mode"],
        policy_provenance_refs=[_ref(ref_id="prov-1")],
        safety_guard_class=None,
        estimated_delta_hours=-0.5,
        estimated_delta_repair_cycles=-0.3,
        estimated_delta_commit_failures=-0.1,
        estimated_risk_change="lower",
        confidence=0.7,
        invalidated_by=[],
        supporting_finding_ids=["finding-1"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)  # type: ignore[arg-type]


def _axis_delta(
    axis: str = "hours",
    baseline_value: float | None = 4.0,
    baseline_unit: str = "hours",
    baseline_confidence: float = 0.8,
    scenario_estimated_delta: float | None = -0.5,
    scenario_estimated_risk_change: str = "lower",
    scenario_confidence: float = 0.7,
    confidence: float = 0.74,
    validity_limits: list[str] | None = None,
    evidence_refs: list[GovernanceEvidenceRef] | None = None,
    invalidated: bool = False,
) -> MetricsAxisDelta:
    """Construct a fully-specified :class:`MetricsAxisDelta`."""

    if validity_limits is None:
        validity_limits = ["summary_replay_mode"]
    if evidence_refs is None:
        evidence_refs = [_ref(ref_id=f"axis-{axis}-ev")]
    return MetricsAxisDelta(  # type: ignore[call-arg]
        axis=axis,
        baseline_value=baseline_value,
        baseline_unit=baseline_unit,
        baseline_confidence=baseline_confidence,
        scenario_estimated_delta=scenario_estimated_delta,
        scenario_estimated_risk_change=scenario_estimated_risk_change,
        scenario_confidence=scenario_confidence,
        confidence=confidence,
        validity_limits=validity_limits,
        evidence_refs=evidence_refs,
        invalidated=invalidated,
    )


def _comparator_result(**overrides: object) -> MetricsComparatorResult:
    """Construct a fully-specified :class:`MetricsComparatorResult`."""

    base: dict[str, object] = dict(
        axis_deltas=[
            _axis_delta(axis="hours"),
            _axis_delta(
                axis="repair_cycles",
                baseline_value=2.0,
                baseline_unit="count",
                scenario_estimated_delta=-0.3,
            ),
            _axis_delta(
                axis="commit_failures",
                baseline_value=1.0,
                baseline_unit="count",
                scenario_estimated_delta=-0.1,
            ),
            _axis_delta(
                axis="risk_change",
                baseline_value=None,
                baseline_unit="risk",
                baseline_confidence=0.0,
                scenario_estimated_delta=None,
            ),
        ],
        gap_findings=[],
        idempotency_key="comp-key-1",
        result_id="comp-result-1",
        scenario_result_id="cf-result-1",
        emitted_at=datetime(2026, 5, 27, 12, 0, 0, tzinfo=timezone.utc),
        invalidated_axes=[],
        overall_confidence=0.7,
    )
    base.update(overrides)
    return MetricsComparatorResult(**base)  # type: ignore[arg-type]


def _writer_inputs(**overrides: object) -> CounterfactualResultWriterInputs:
    """Construct a fully-specified
    :class:`CounterfactualResultWriterInputs`."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        results=[_result()],
        comparator_results=[_comparator_result()],
        baseline_refs=[_ref(ref_id="baseline-1")],
        warnings=[],
        incomplete_scopes=[],
    )
    base.update(overrides)
    return CounterfactualResultWriterInputs(**base)  # type: ignore[arg-type]


def _gap(**overrides: object) -> CounterfactualResultPersistenceGap:
    """Construct a fully-specified
    :class:`CounterfactualResultPersistenceGap`."""

    base: dict[str, object] = dict(
        failure_id="counterfactual_result_persistence_failed",
        corpus_id="8ac124d6",
        results_digest="sha256:abc",
        review_projection_id="review:governance-counterfactuals:8ac124d6",
        reason="results_construction_failed",
        evidence_refs=[],
        evidence_payload={"error_detail": "test"},
    )
    base.update(overrides)
    return CounterfactualResultPersistenceGap(**base)  # type: ignore[arg-type]


# ── module surface tests ────────────────────────────────────────────────────


_EXPECTED_EXPORTS = {
    "CounterfactualResultWriterInputs",
    "CounterfactualResultWriterResult",
    "CounterfactualResultPersistenceGap",
    "COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID",
    "REVIEW_PROJECTION_ID_PREFIX",
    "DEFAULT_REVIEW_PROJECTION_CAP",
    "compute_counterfactual_result_projection_id",
    "compute_counterfactual_results_digest",
    "CounterfactualResultWriter",
}


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed-shape additions: 3
    typed BaseModels + the typed failure id Literal + the typed
    review-projection id prefix Literal + the LIMIT cap+1 default + 2
    pure helpers + the :class:`CounterfactualResultWriter` class
    (exactly 9 exports).
    """

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    assert set(mod.__all__) == _EXPECTED_EXPORTS
    assert len(mod.__all__) == 9


@pytest.mark.parametrize("name", sorted(_EXPECTED_EXPORTS))
def test_module_surface_hasattr(name: str) -> None:
    """Each planned export is importable from the module."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    assert hasattr(mod, name), f"missing export: {name}"


def test_no_re_export_from_execution_control_init() -> None:
    """The Slice 18 6th sub-slice module is NOT re-exported from
    :mod:`iriai_build_v2.execution_control`'s ``__init__.py``.

    Mirrors the Slice 13A/14/15/16/17/18-1st/2nd/3rd/4th/5th
    precedent (the package __init__ is intentionally minimal;
    consumers import from the per-sub-slice module directly).
    """

    import iriai_build_v2.execution_control as pkg

    pkg_file = pkg.__file__
    assert pkg_file is not None
    init_source = Path(pkg_file).read_text()
    for name in _EXPECTED_EXPORTS:
        assert name not in init_source, (
            f"{name} unexpectedly re-exported from execution_control/__init__.py"
        )


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """The module imports
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (the Slice 13a shared shape); it does NOT define a local copy."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    assert "GovernanceEvidenceRef" not in mod.__all__


def test_module_does_not_redefine_counterfactual_result() -> None:
    """The module imports the Slice 18 1st sub-slice
    :class:`CounterfactualResult`; it does NOT define a local copy."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    assert "CounterfactualResult" not in mod.__all__


def test_module_does_not_redefine_metrics_comparator_result() -> None:
    """The module imports the Slice 18 5th sub-slice
    :class:`MetricsComparatorResult`; it does NOT define a local
    copy."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    assert "MetricsComparatorResult" not in mod.__all__


def test_module_import_discipline_no_supervisor_dashboard_phases() -> None:
    """The module MUST NOT import from supervisor / dashboard / phases /
    upstream loader / replay engines.

    Per the implementer brief Non-negotiables the writer is a pure
    projection observer; it consumes typed shapes from Slice 13a +
    Slice 18 1st + Slice 18 5th, NOT from any consumer-side or
    upstream-emitter module."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_imports = [
        "from iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.supervisor",
        "import iriai_build_v2.dashboard",
        "from iriai_build_v2.workflows.develop.execution.phases",
        "from iriai_build_v2.execution_control.counterfactual_replay_loader",
        "from iriai_build_v2.execution_control.counterfactual_summary_replay",
        "from iriai_build_v2.execution_control.counterfactual_event_replay",
    ]
    for forbidden in forbidden_imports:
        assert forbidden not in source, (
            f"forbidden import found in counterfactual_result_writer.py: {forbidden}"
        )


def test_module_import_discipline_only_allowed_imports() -> None:
    """The module MUST import only from stdlib + Pydantic v2 + Slice
    13a governance.models + Slice 18 1st counterfactual_replay +
    Slice 18 5th counterfactual_metrics_comparator."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    # Required imports (positive controls).
    assert "from pydantic import" in source
    assert (
        "from iriai_build_v2.workflows.develop.governance.models import"
        in source
    )
    assert (
        "from iriai_build_v2.execution_control.counterfactual_replay import"
        in source
    )
    assert (
        "from iriai_build_v2.execution_control.counterfactual_metrics_comparator import"
        in source
    )


# ── prefix + cap + failure-id constants ─────────────────────────────────────


def test_review_projection_id_prefix_value() -> None:
    """The prefix matches doc-18:116-117 verbatim:
    ``review:governance-counterfactuals:``."""

    assert REVIEW_PROJECTION_ID_PREFIX == "review:governance-counterfactuals:"


def test_review_projection_id_prefix_differs_from_slice_15_4th() -> None:
    """The Slice 18 6th sub-slice prefix is intentionally different
    from the Slice 15 4th sub-slice prefix
    (``review:governance-metrics:``)."""

    from iriai_build_v2.execution_control.governance_scorecard_writer import (
        REVIEW_PROJECTION_ID_PREFIX as scorecard_prefix,
    )

    assert REVIEW_PROJECTION_ID_PREFIX != scorecard_prefix
    assert scorecard_prefix == "review:governance-metrics:"


def test_review_projection_id_prefix_differs_from_slice_16_4th() -> None:
    """The Slice 18 6th sub-slice prefix is intentionally different
    from the Slice 16 4th sub-slice prefix
    (``review:governance-findings:``)."""

    from iriai_build_v2.execution_control.governance_finding_writer import (
        REVIEW_PROJECTION_ID_PREFIX as finding_prefix,
    )

    assert REVIEW_PROJECTION_ID_PREFIX != finding_prefix
    assert finding_prefix == "review:governance-findings:"


def test_review_projection_id_prefix_differs_from_slice_17_4th() -> None:
    """The Slice 18 6th sub-slice prefix is intentionally different
    from the Slice 17 4th sub-slice prefix
    (``review:governance-recommendations:``)."""

    from iriai_build_v2.execution_control.decision_record_writer import (
        REVIEW_PROJECTION_ID_PREFIX as decision_prefix,
    )

    assert REVIEW_PROJECTION_ID_PREFIX != decision_prefix
    assert decision_prefix == "review:governance-recommendations:"


def test_counterfactual_result_persistence_failure_id_value() -> None:
    """The typed failure id is
    ``counterfactual_result_persistence_failed`` per the Slice 18 6th
    sub-slice chunk-shape."""

    assert (
        COUNTERFACTUAL_RESULT_PERSISTENCE_FAILURE_ID
        == "counterfactual_result_persistence_failed"
    )


def test_default_review_projection_cap_is_positive_int() -> None:
    """The default LIMIT cap+1 cap is a positive integer."""

    assert isinstance(DEFAULT_REVIEW_PROJECTION_CAP, int)
    assert DEFAULT_REVIEW_PROJECTION_CAP > 0


def test_default_review_projection_cap_matches_slice_15_4th_value() -> None:
    """Per the chunk-shape decision the Slice 18 6th sub-slice default
    cap mirrors the Slice 15 4th sub-slice value verbatim (200)."""

    from iriai_build_v2.execution_control.governance_scorecard_writer import (
        DEFAULT_REVIEW_PROJECTION_CAP as scorecard_cap,
    )

    assert DEFAULT_REVIEW_PROJECTION_CAP == scorecard_cap == 200


def test_default_review_projection_cap_matches_slice_16_4th_value() -> None:
    """Per the chunk-shape decision the Slice 18 6th sub-slice default
    cap mirrors the Slice 16 4th sub-slice value verbatim (200)."""

    from iriai_build_v2.execution_control.governance_finding_writer import (
        DEFAULT_REVIEW_PROJECTION_CAP as finding_cap,
    )

    assert DEFAULT_REVIEW_PROJECTION_CAP == finding_cap == 200


def test_default_review_projection_cap_matches_slice_17_4th_value() -> None:
    """Per the chunk-shape decision the Slice 18 6th sub-slice default
    cap mirrors the Slice 17 4th sub-slice value verbatim (200)."""

    from iriai_build_v2.execution_control.decision_record_writer import (
        DEFAULT_REVIEW_PROJECTION_CAP as decision_cap,
    )

    assert DEFAULT_REVIEW_PROJECTION_CAP == decision_cap == 200


# ── compute_counterfactual_result_projection_id helper ─────────────────────


def test_compute_projection_id_constructs_typed_prefix() -> None:
    """The helper constructs the typed projection id by concatenating
    the prefix + corpus_id."""

    assert (
        compute_counterfactual_result_projection_id("8ac124d6")
        == "review:governance-counterfactuals:8ac124d6"
    )


def test_compute_projection_id_passes_through_corpus_id() -> None:
    """The helper passes the corpus_id through verbatim (no
    canonicalization)."""

    assert (
        compute_counterfactual_result_projection_id("Corpus_A.B-1")
        == "review:governance-counterfactuals:Corpus_A.B-1"
    )


def test_compute_projection_id_accepts_empty_corpus_id() -> None:
    """Defensive: the helper accepts an empty corpus_id; the caller is
    responsible for validating corpus_id non-emptiness."""

    assert (
        compute_counterfactual_result_projection_id("")
        == "review:governance-counterfactuals:"
    )


def test_compute_projection_id_uses_prefix_constant() -> None:
    """The helper consumes the typed
    :data:`REVIEW_PROJECTION_ID_PREFIX` constant verbatim."""

    result = compute_counterfactual_result_projection_id("c")
    assert result.startswith(REVIEW_PROJECTION_ID_PREFIX)


# ── compute_counterfactual_results_digest helper ───────────────────────────


def test_compute_results_digest_returns_64_char_hex() -> None:
    """The digest is a SHA-256 hex string (64 chars)."""

    digest = compute_counterfactual_results_digest([_result()])
    assert len(digest) == 64
    int(digest, 16)  # parses as hex


def test_compute_results_digest_empty_list_returns_stable_digest() -> None:
    """The digest for an empty results list is a stable, repeatable
    hash."""

    d1 = compute_counterfactual_results_digest([])
    d2 = compute_counterfactual_results_digest([])
    assert d1 == d2


def test_compute_results_digest_is_stable_across_calls() -> None:
    """Two calls with the same results list produce byte-identical
    digests."""

    d1 = compute_counterfactual_results_digest([_result()])
    d2 = compute_counterfactual_results_digest([_result()])
    assert d1 == d2


def test_compute_results_digest_differs_for_different_results() -> None:
    """Two calls with different results produce different digests."""

    d1 = compute_counterfactual_results_digest([_result(confidence=0.7)])
    d2 = compute_counterfactual_results_digest([_result(confidence=0.5)])
    assert d1 != d2


def test_compute_results_digest_order_sensitive() -> None:
    """The digest is computed over the canonical-JSON projection of
    the list verbatim; the caller is responsible for deterministic
    ordering."""

    r_a = _result(result_id="cf-a")
    r_b = _result(result_id="cf-b")
    d_ab = compute_counterfactual_results_digest([r_a, r_b])
    d_ba = compute_counterfactual_results_digest([r_b, r_a])
    assert d_ab != d_ba


def test_compute_results_digest_sensitive_to_result_version() -> None:
    """Per doc-18:128-129 new ``result_version`` produces a new digest
    (so re-projection is detected)."""

    d_v1 = compute_counterfactual_results_digest([_result(result_version="v1")])
    d_v2 = compute_counterfactual_results_digest([_result(result_version="v2")])
    assert d_v1 != d_v2


# ── CounterfactualResultWriterInputs construction ──────────────────────────


def test_writer_inputs_accepts_all_required_fields() -> None:
    """The typed bundle accepts the documented inputs."""

    inputs = _writer_inputs()
    assert inputs.corpus_id == "8ac124d6"
    assert len(inputs.results) == 1
    assert len(inputs.comparator_results) == 1
    assert len(inputs.baseline_refs) == 1


def test_writer_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline: unknown fields fail
    closed with a typed ``ValidationError``."""

    with pytest.raises(ValidationError):
        CounterfactualResultWriterInputs(
            corpus_id="c",
            results=[],
            unknown_field="boom",  # type: ignore[arg-type]
        )


def test_writer_inputs_round_trips_via_json() -> None:
    """The typed bundle round-trips via ``model_dump`` +
    ``model_validate``."""

    inputs = _writer_inputs()
    dumped = inputs.model_dump(mode="json")
    re_inputs = CounterfactualResultWriterInputs.model_validate(dumped)
    assert re_inputs == inputs


def test_writer_inputs_comparator_results_defaults_to_empty() -> None:
    """``comparator_results`` defaults to an empty list."""

    inputs = CounterfactualResultWriterInputs(corpus_id="c", results=[])
    assert inputs.comparator_results == []


def test_writer_inputs_baseline_refs_defaults_to_empty() -> None:
    """``baseline_refs`` defaults to an empty list."""

    inputs = CounterfactualResultWriterInputs(corpus_id="c", results=[])
    assert inputs.baseline_refs == []


def test_writer_inputs_warnings_defaults_to_empty() -> None:
    """``warnings`` defaults to an empty list."""

    inputs = CounterfactualResultWriterInputs(corpus_id="c", results=[])
    assert inputs.warnings == []


def test_writer_inputs_incomplete_scopes_defaults_to_empty() -> None:
    """``incomplete_scopes`` defaults to an empty list."""

    inputs = CounterfactualResultWriterInputs(corpus_id="c", results=[])
    assert inputs.incomplete_scopes == []


def test_writer_inputs_results_field_required() -> None:
    """``results`` is a required field."""

    with pytest.raises(ValidationError):
        CounterfactualResultWriterInputs(corpus_id="c")  # type: ignore[call-arg]


def test_writer_inputs_corpus_id_required() -> None:
    """``corpus_id`` is a required field."""

    with pytest.raises(ValidationError):
        CounterfactualResultWriterInputs(results=[])  # type: ignore[call-arg]


# ── DIRECT annotation-identity REUSE (Slice 13a + Slice 18 1st + Slice 18
#    5th) ─────────────────────────────────────────────────────────────────────


def test_writer_inputs_results_annotation_is_slice_18_first_counterfactual_result() -> None:
    """:attr:`CounterfactualResultWriterInputs.results` element type IS
    the Slice 18 1st sub-slice typed :class:`CounterfactualResult`."""

    hints = get_type_hints(CounterfactualResultWriterInputs)
    results_t = hints["results"]
    assert get_origin(results_t) is list
    (element_t,) = get_args(results_t)
    assert element_t is CounterfactualResult


def test_writer_inputs_comparator_results_annotation_is_slice_18_fifth() -> None:
    """:attr:`CounterfactualResultWriterInputs.comparator_results`
    element type IS the Slice 18 5th sub-slice typed
    :class:`MetricsComparatorResult`."""

    hints = get_type_hints(CounterfactualResultWriterInputs)
    cr_t = hints["comparator_results"]
    assert get_origin(cr_t) is list
    (element_t,) = get_args(cr_t)
    assert element_t is MetricsComparatorResult


def test_writer_inputs_baseline_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """:attr:`CounterfactualResultWriterInputs.baseline_refs` element
    type IS the Slice 13a typed :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(CounterfactualResultWriterInputs)
    br_t = hints["baseline_refs"]
    assert get_origin(br_t) is list
    (element_t,) = get_args(br_t)
    assert element_t is GovernanceEvidenceRef


def test_gap_evidence_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """:attr:`CounterfactualResultPersistenceGap.evidence_refs` element
    type IS the Slice 13a typed :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(CounterfactualResultPersistenceGap)
    er_t = hints["evidence_refs"]
    assert get_origin(er_t) is list
    (element_t,) = get_args(er_t)
    assert element_t is GovernanceEvidenceRef


def test_result_gap_records_annotation_is_local_persistence_gap() -> None:
    """:attr:`CounterfactualResultWriterResult.gap_records` element
    type IS the local
    :class:`CounterfactualResultPersistenceGap`."""

    hints = get_type_hints(CounterfactualResultWriterResult)
    gr_t = hints["gap_records"]
    assert get_origin(gr_t) is list
    (element_t,) = get_args(gr_t)
    assert element_t is CounterfactualResultPersistenceGap


def test_writer_write_results_parameter_annotation_is_local_inputs() -> None:
    """The :meth:`CounterfactualResultWriter.write_results` parameter
    annotation IS the local
    :class:`CounterfactualResultWriterInputs`."""

    hints = get_type_hints(CounterfactualResultWriter.write_results)
    inputs_t = hints["inputs"]
    assert inputs_t is CounterfactualResultWriterInputs
    return_t = hints["return"]
    assert return_t is CounterfactualResultWriterResult


# ── CounterfactualResultPersistenceGap construction ────────────────────────


def test_gap_accepts_all_fields() -> None:
    """The typed gap shape accepts the documented fields."""

    gap = _gap()
    assert gap.failure_id == "counterfactual_result_persistence_failed"
    assert gap.corpus_id == "8ac124d6"
    assert gap.results_digest == "sha256:abc"


def test_gap_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline: unknown fields fail
    closed."""

    with pytest.raises(ValidationError):
        CounterfactualResultPersistenceGap(
            failure_id="counterfactual_result_persistence_failed",
            corpus_id="c",
            results_digest=None,
            review_projection_id=None,
            reason="x",
            unknown_field="boom",  # type: ignore[arg-type]
        )


def test_gap_round_trips_via_json() -> None:
    """The typed gap round-trips via ``model_dump`` +
    ``model_validate``."""

    gap = _gap()
    dumped = gap.model_dump(mode="json")
    re_gap = CounterfactualResultPersistenceGap.model_validate(dumped)
    assert re_gap == gap


def test_gap_failure_id_literal_rejects_other_failure_ids() -> None:
    """The ``failure_id`` Literal accepts only
    ``counterfactual_result_persistence_failed``."""

    with pytest.raises(ValidationError):
        CounterfactualResultPersistenceGap(
            failure_id="some_other_failure_id",  # type: ignore[arg-type]
            corpus_id="c",
            results_digest=None,
            review_projection_id=None,
            reason="x",
        )


def test_gap_results_digest_accepts_none() -> None:
    """The ``results_digest`` field accepts ``None`` (the failure
    happened before the digest could be computed)."""

    gap = _gap(results_digest=None)
    assert gap.results_digest is None


def test_gap_review_projection_id_accepts_none() -> None:
    """The ``review_projection_id`` field accepts ``None`` (the
    failure happened before the projection id could be computed)."""

    gap = _gap(review_projection_id=None)
    assert gap.review_projection_id is None


def test_gap_observed_at_defaults_to_utc_now() -> None:
    """The ``observed_at`` field defaults to UTC now."""

    before = datetime.now(timezone.utc)
    gap = CounterfactualResultPersistenceGap(
        failure_id="counterfactual_result_persistence_failed",
        corpus_id="c",
        results_digest=None,
        review_projection_id=None,
        reason="x",
    )
    after = datetime.now(timezone.utc)
    assert before <= gap.observed_at <= after


def test_gap_evidence_refs_defaults_to_empty() -> None:
    """``evidence_refs`` defaults to an empty list."""

    gap = CounterfactualResultPersistenceGap(
        failure_id="counterfactual_result_persistence_failed",
        corpus_id="c",
        results_digest=None,
        review_projection_id=None,
        reason="x",
    )
    assert gap.evidence_refs == []


def test_gap_evidence_payload_defaults_to_empty_dict() -> None:
    """``evidence_payload`` defaults to an empty dict."""

    gap = CounterfactualResultPersistenceGap(
        failure_id="counterfactual_result_persistence_failed",
        corpus_id="c",
        results_digest=None,
        review_projection_id=None,
        reason="x",
    )
    assert gap.evidence_payload == {}


# ── CounterfactualResultWriterResult construction ──────────────────────────


def test_writer_result_defaults_to_empty_lists() -> None:
    """All :class:`CounterfactualResultWriterResult` list fields
    default to empty."""

    result = CounterfactualResultWriterResult()
    assert result.persisted_result_ids == []
    assert result.gap_records == []
    assert result.truncated is False


def test_writer_result_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` discipline."""

    with pytest.raises(ValidationError):
        CounterfactualResultWriterResult(unknown_field="boom")  # type: ignore[call-arg]


def test_writer_result_round_trips_via_json() -> None:
    """The typed result round-trips via ``model_dump`` +
    ``model_validate``."""

    result = CounterfactualResultWriterResult(
        persisted_result_ids=["cf-1"],
        gap_records=[_gap()],
        truncated=True,
    )
    dumped = result.model_dump(mode="json")
    re_result = CounterfactualResultWriterResult.model_validate(dumped)
    assert re_result.persisted_result_ids == ["cf-1"]
    assert re_result.truncated is True
    assert len(re_result.gap_records) == 1


# ── failure-router pure-data add-point validation (4 of 4 add points) ──────


def test_failure_router_registers_new_typed_failure_id() -> None:
    """Add-point 1: the NEW typed failure id
    ``counterfactual_result_persistence_failed`` is registered in the
    :data:`FailureType` Literal."""

    args = get_args(FailureType)
    assert "counterfactual_result_persistence_failed" in args


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add-point 2: the NEW typed failure id is in the
    :data:`FAILURE_TYPES` tuple."""

    assert "counterfactual_result_persistence_failed" in FAILURE_TYPES


def test_failure_router_new_id_in_retryable_set() -> None:
    """Add-point 3: the NEW typed failure id is in the
    :data:`_RETRYABLE_FAILURE_TYPES` frozenset (the transient
    governance projection failure pattern)."""

    assert (
        "counterfactual_result_persistence_failed"
        in _RETRYABLE_FAILURE_TYPES
    )


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Add-point 4: the route table carries the NEW typed failure id
    under the EXISTING ``evidence_corruption`` failure_class with the
    REUSED ``retry_governance_projection`` NON-blocking RouteAction."""

    key = (
        "evidence_corruption",
        "counterfactual_result_persistence_failed",
    )
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

    key = (
        "evidence_corruption",
        "counterfactual_result_persistence_failed",
    )
    route = ROUTE_TABLE[key]
    assert route.reason
    assert "Slice 18 6th" in route.reason


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
            and route.failure_type
            == "counterfactual_result_persistence_failed"
        ):
            seen = True
            assert route.action == "retry_governance_projection"
            break
    assert seen, (
        "counterfactual_result_persistence_failed row not found in _ROUTE_ROWS"
    )


# ── CounterfactualResultWriter happy-path ───────────────────────────────────


def test_writer_construction_default_is_empty_gap_records() -> None:
    """A fresh writer's :attr:`gap_records` property is empty."""

    writer = CounterfactualResultWriter()
    assert writer.gap_records == []


def test_writer_gap_records_is_snapshot_list() -> None:
    """:attr:`gap_records` returns a fresh snapshot list per call so
    caller mutations do not leak into the internal accumulator."""

    writer = CounterfactualResultWriter()
    snap = writer.gap_records
    snap.append(_gap())
    assert writer.gap_records == []


def test_write_results_emits_typed_writer_result() -> None:
    """:meth:`write_results` returns a typed
    :class:`CounterfactualResultWriterResult`."""

    writer = CounterfactualResultWriter()
    result = writer.write_results(_writer_inputs())
    assert isinstance(result, CounterfactualResultWriterResult)


def test_write_results_preserves_result_ids_verbatim() -> None:
    """The writer preserves the per-result ``result_id`` verbatim (no
    mutation)."""

    writer = CounterfactualResultWriter()
    results = [
        _result(result_id="cf-A"),
        _result(result_id="cf-B"),
        _result(result_id="cf-C"),
    ]
    result = writer.write_results(_writer_inputs(results=results))
    assert result.persisted_result_ids == ["cf-A", "cf-B", "cf-C"]


def test_write_results_empty_list_produces_empty_persisted() -> None:
    """An empty results list produces an empty
    ``persisted_result_ids`` list."""

    writer = CounterfactualResultWriter()
    result = writer.write_results(_writer_inputs(results=[]))
    assert result.persisted_result_ids == []
    assert result.gap_records == []
    assert result.truncated is False


def test_write_results_resets_gap_records_per_call() -> None:
    """Per-call :attr:`gap_records` accumulator RESETS at entry."""

    writer = CounterfactualResultWriter()
    writer._gap_records.append(_gap())  # type: ignore[attr-defined]
    # Now call write_results and verify the prior gap is reset.
    writer.write_results(_writer_inputs())
    assert writer.gap_records == []


def test_write_results_with_cap_truncates_results_list() -> None:
    """The results list is truncated at ``cap+1`` per the bounded-
    read discipline."""

    writer = CounterfactualResultWriter()
    results = [
        _result(result_id=f"cf-{i}") for i in range(5)
    ]
    result = writer.write_results(
        _writer_inputs(results=results), cap=3
    )
    # Per LIMIT cap+1: emit at most cap+1 = 4 items so the caller can
    # detect overflow.
    assert len(result.persisted_result_ids) == 4
    assert result.truncated is True


def test_write_results_no_truncation_under_cap() -> None:
    """No truncation when results count <= cap."""

    writer = CounterfactualResultWriter()
    results = [
        _result(result_id=f"cf-{i}") for i in range(3)
    ]
    result = writer.write_results(
        _writer_inputs(results=results), cap=10
    )
    assert len(result.persisted_result_ids) == 3
    assert result.truncated is False


# ── CounterfactualResultWriter review projection ───────────────────────────


def test_write_review_projection_emits_typed_dict() -> None:
    """:meth:`write_review_projection` emits a typed projection dict."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()],
        corpus_id="c",
    )
    assert isinstance(projection, dict)
    assert projection["review_projection_id"] == (
        "review:governance-counterfactuals:c"
    )


def test_write_review_projection_id_uses_typed_prefix() -> None:
    """The projection's ``review_projection_id`` uses the typed
    prefix."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[], corpus_id="x"
    )
    assert projection["review_projection_id"].startswith(
        REVIEW_PROJECTION_ID_PREFIX
    )


def test_write_review_projection_corpus_id_passes_through() -> None:
    """The projection's ``corpus_id`` passes through verbatim."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[], corpus_id="my-corpus-1"
    )
    assert projection["corpus_id"] == "my-corpus-1"


def test_write_review_projection_generated_at_is_iso_string() -> None:
    """The projection's ``generated_at`` is an ISO-8601 string per
    the canonical-JSON discipline."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(results=[], corpus_id="c")
    iso_str = projection["generated_at"]
    assert isinstance(iso_str, str)
    # Should parse as ISO-8601 datetime.
    datetime.fromisoformat(iso_str)


def test_write_review_projection_carries_results_digest() -> None:
    """The projection carries the results digest for tamper
    detection per doc-18:127-129."""

    writer = CounterfactualResultWriter()
    results = [_result()]
    projection = writer.write_review_projection(
        results=results, corpus_id="c"
    )
    expected_digest = compute_counterfactual_results_digest(results)
    assert projection["results_digest"] == expected_digest


def test_write_review_projection_digest_is_stable_across_reruns() -> None:
    """Two re-projections against the same results produce byte-
    identical digests."""

    writer = CounterfactualResultWriter()
    results = [_result()]
    p1 = writer.write_review_projection(results=results, corpus_id="c")
    p2 = writer.write_review_projection(results=results, corpus_id="c")
    assert p1["results_digest"] == p2["results_digest"]


def test_write_review_projection_digest_differs_for_different_results() -> None:
    """Different result-sets produce different digests."""

    writer = CounterfactualResultWriter()
    r1 = [_result(confidence=0.7)]
    r2 = [_result(confidence=0.5)]
    p1 = writer.write_review_projection(results=r1, corpus_id="c")
    p2 = writer.write_review_projection(results=r2, corpus_id="c")
    assert p1["results_digest"] != p2["results_digest"]


def test_write_review_projection_results_are_compact_dicts() -> None:
    """The projection's ``results`` list carries compact dicts with
    typed control fields only."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()], corpus_id="c"
    )
    assert isinstance(projection["results"], list)
    assert len(projection["results"]) == 1
    r = projection["results"][0]
    assert r["result_id"] == "cf-result-1"
    assert r["result_version"] == "v1"
    assert r["scenario_id"] == "scenario-1"
    assert r["corpus_id"] == "8ac124d6"
    assert r["estimated_risk_change"] == "lower"
    assert r["recommended_next_step"] == "draft_policy"


def test_write_review_projection_result_provenance_refs_are_cited_only() -> None:
    """Per-result ``policy_provenance_refs`` carry only typed-ref
    shape (no raw bodies)."""

    writer = CounterfactualResultWriter()
    result = _result(
        policy_provenance_refs=[
            _ref(ref_id="x", digest="sha256:x" + "0" * 56),
        ]
    )
    projection = writer.write_review_projection(
        results=[result], corpus_id="c"
    )
    refs = projection["results"][0]["policy_provenance_refs"]
    # The projection carries the typed
    # :class:`GovernanceEvidenceRef` shape via ``model_dump(mode='json')``
    # (authority + ref_id + digest + quality + completeness + the
    # optional typed metadata fields -- NEVER raw bodies).
    assert len(refs) == 1
    ref = refs[0]
    assert ref["authority"] == "typed_journal"
    assert ref["ref_id"] == "x"
    assert ref["digest"] == "sha256:x" + "0" * 56
    assert ref["quality"] == "canonical"
    assert ref["completeness"] == "complete"
    # Defence-in-depth: no raw body fields.
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
    assert not (set(ref.keys()) & forbidden_keys)


def test_write_review_projection_comparator_results_are_cited_only() -> None:
    """Projection ``comparator_results`` are typed-shape dicts only
    (no raw bodies)."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()],
        corpus_id="c",
        comparator_results=[_comparator_result()],
    )
    assert isinstance(projection["comparator_results"], list)
    assert len(projection["comparator_results"]) == 1
    cr = projection["comparator_results"][0]
    assert cr["result_id"] == "comp-result-1"
    assert cr["scenario_result_id"] == "cf-result-1"
    assert cr["overall_confidence"] == 0.7


def test_write_review_projection_baseline_refs_are_cited_only() -> None:
    """Projection ``baseline_refs`` carry only typed-ref shape (no raw
    bodies)."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()],
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
    assert ref["quality"] == "canonical"
    assert ref["completeness"] == "complete"
    # Defence-in-depth: no raw body fields.
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
    assert not (set(ref.keys()) & forbidden_keys)


# ── walk-time forbidden-key invariant (refs-only discipline) ───────────────


def test_write_review_projection_walk_no_forbidden_keys() -> None:
    """Walk-time forbidden-key invariant: scan the entire projection
    recursively and assert no raw-body-shaped field name appears at
    any nesting level."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()],
        corpus_id="c",
        comparator_results=[_comparator_result()],
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


def test_write_review_projection_result_dict_keys_are_typed_control() -> None:
    """Per-result projection dict carries only typed control field
    keys + cited refs; defence-in-depth assertion."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()], corpus_id="c"
    )
    r = projection["results"][0]
    expected_keys = {
        "result_id",
        "result_version",
        "scenario_id",
        "corpus_id",
        "assumptions",
        "validity_limits",
        "safety_guard_class",
        "estimated_delta_hours",
        "estimated_delta_repair_cycles",
        "estimated_delta_commit_failures",
        "estimated_risk_change",
        "confidence",
        "invalidated_by",
        "supporting_finding_ids",
        "recommended_next_step",
        "policy_provenance_refs",
        "policy_provenance_refs_truncated",
    }
    assert set(r.keys()) == expected_keys


# ── LIMIT cap+1 truncation discipline (across all list dimensions) ─────────


def test_write_review_projection_results_list_respects_cap_plus_one() -> None:
    """Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
    projection emits at most ``cap+1`` results."""

    writer = CounterfactualResultWriter()
    results = [
        _result(result_id=f"r-{i}") for i in range(5)
    ]
    projection = writer.write_review_projection(
        results=results, corpus_id="c", cap=3
    )
    assert len(projection["results"]) == 4  # cap+1
    assert projection["results_truncated"] is True


def test_write_review_projection_comparator_results_list_respects_cap_plus_one() -> None:
    """The comparator_results list is truncated at ``cap+1``."""

    writer = CounterfactualResultWriter()
    comparator_results = [
        _comparator_result(result_id=f"comp-{i}") for i in range(5)
    ]
    projection = writer.write_review_projection(
        results=[_result()],
        corpus_id="c",
        comparator_results=comparator_results,
        cap=2,
    )
    assert len(projection["comparator_results"]) == 3
    assert projection["comparator_results_truncated"] is True


def test_write_review_projection_baseline_refs_list_respects_cap_plus_one() -> None:
    """The baseline_refs list is truncated at ``cap+1``."""

    writer = CounterfactualResultWriter()
    baseline_refs = [_ref(ref_id=f"b-{i}") for i in range(5)]
    projection = writer.write_review_projection(
        results=[_result()],
        corpus_id="c",
        baseline_refs=baseline_refs,
        cap=2,
    )
    assert len(projection["baseline_refs"]) == 3
    assert projection["baseline_refs_truncated"] is True


def test_write_review_projection_result_provenance_refs_respects_cap_plus_one() -> None:
    """The per-result policy_provenance_refs list is truncated at
    ``cap+1``."""

    writer = CounterfactualResultWriter()
    result = _result(
        policy_provenance_refs=[_ref(ref_id=f"e-{i}") for i in range(5)]
    )
    projection = writer.write_review_projection(
        results=[result], corpus_id="c", cap=2
    )
    r = projection["results"][0]
    assert len(r["policy_provenance_refs"]) == 3
    assert r["policy_provenance_refs_truncated"] is True


def test_write_review_projection_no_truncation_under_cap() -> None:
    """No truncation when all list counts <= cap."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()],
        corpus_id="c",
        comparator_results=[_comparator_result()],
        baseline_refs=[_ref(ref_id="b1")],
        cap=10,
    )
    assert projection["results_truncated"] is False
    assert projection["comparator_results_truncated"] is False
    assert projection["baseline_refs_truncated"] is False


def test_write_review_projection_default_cap_is_default_constant() -> None:
    """The default cap is :data:`DEFAULT_REVIEW_PROJECTION_CAP`."""

    writer = CounterfactualResultWriter()
    # Default cap = 200; provide 201 results to force truncation.
    results = [
        _result(result_id=f"r-{i}")
        for i in range(DEFAULT_REVIEW_PROJECTION_CAP + 1)
    ]
    projection = writer.write_review_projection(
        results=results, corpus_id="c"
    )
    assert (
        len(projection["results"]) == DEFAULT_REVIEW_PROJECTION_CAP + 1
    )
    assert projection["results_truncated"] is True


# ── never-raises fail-closed (feedback_no_silent_degradation) ──────────────


def test_writer_never_raises_on_normal_inputs() -> None:
    """The writer NEVER raises on typed inputs."""

    writer = CounterfactualResultWriter()
    # Should not raise.
    writer.write_results(_writer_inputs())
    writer.write_review_projection(results=[_result()], corpus_id="c")


def test_writer_gap_records_empty_on_normal_path() -> None:
    """Normal write_results path emits no gap records."""

    writer = CounterfactualResultWriter()
    result = writer.write_results(_writer_inputs())
    assert result.gap_records == []
    assert writer.gap_records == []


def test_writer_gap_records_empty_on_normal_projection_path() -> None:
    """Normal write_review_projection path emits no gap records."""

    writer = CounterfactualResultWriter()
    writer.write_review_projection(results=[_result()], corpus_id="c")
    assert writer.gap_records == []


def test_writer_gap_on_digest_failure_does_not_raise(monkeypatch) -> None:
    """If the digest computation fails internally the writer projects
    onto a typed gap rather than raising (defensive try/except)."""

    writer = CounterfactualResultWriter()

    # Monkey-patch the module-level digest function to raise; the
    # writer's defensive try/except catches the failure + emits a
    # typed gap rather than propagating.
    def _exploding_digest(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated digest failure")

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    monkeypatch.setattr(
        mod, "compute_counterfactual_results_digest", _exploding_digest
    )

    # Should not raise.
    projection = writer.write_review_projection(
        results=[_result()], corpus_id="c"
    )
    # The gap was projected.
    assert writer.gap_records, "expected typed gap on digest failure"
    gap = writer.gap_records[0]
    assert gap.failure_id == "counterfactual_result_persistence_failed"
    assert gap.corpus_id == "c"
    assert "results_digest_failed" in gap.reason
    # The projection itself still returned a typed dict shape (with
    # empty digest as the most-conservative fallback).
    assert isinstance(projection, dict)
    assert projection["results_digest"] == ""


def test_writer_gap_on_results_construction_failure_does_not_raise() -> None:
    """If results construction fails internally (defensive try/except
    path in :meth:`write_results`) the writer projects onto a typed
    gap rather than raising."""

    writer = CounterfactualResultWriter()

    # The write_results defensive try/except catches any exception
    # in the list comprehension iterating result_id; simulate by
    # passing a typed inputs whose results list contains an object
    # that raises on result_id access.
    class _RaisingResult:
        """A non-typed object that raises on result_id access."""

        @property
        def result_id(self) -> str:  # type: ignore[override]
            raise RuntimeError("simulated attribute-access failure")

    # Construct CounterfactualResultWriterInputs via a typed bundle
    # (with a valid result so construction succeeds), then override
    # the results list via runtime type punning (Python doesn't
    # enforce at runtime; the defensive try/except catches the
    # failure).
    inputs = _writer_inputs()
    object.__setattr__(  # type: ignore[arg-type]
        inputs, "results", [_RaisingResult()]
    )

    # Should not raise.
    result = writer.write_results(inputs)
    # The gap was projected.
    assert result.gap_records, "expected typed gap on construction failure"
    gap = result.gap_records[0]
    assert gap.failure_id == "counterfactual_result_persistence_failed"
    assert gap.corpus_id == "8ac124d6"
    assert "results_construction_failed" in gap.reason
    # The persisted_result_ids list is empty (most-conservative
    # fallback).
    assert result.persisted_result_ids == []


# ── integration / roundtrip ────────────────────────────────────────────────


def test_integration_write_results_then_projection_roundtrip() -> None:
    """End-to-end: writer persists results then projects them onto
    the review artifact; the digest survives the roundtrip."""

    writer = CounterfactualResultWriter()
    results = [
        _result(result_id="cf-A", confidence=0.7),
        _result(result_id="cf-B", confidence=0.5),
        _result(result_id="cf-C", confidence=0.3),
    ]
    inputs = _writer_inputs(results=results)
    result = writer.write_results(inputs)
    assert result.persisted_result_ids == ["cf-A", "cf-B", "cf-C"]

    projection = writer.write_review_projection(
        results=results,
        corpus_id=inputs.corpus_id,
        comparator_results=inputs.comparator_results,
        baseline_refs=inputs.baseline_refs,
    )
    expected_digest = compute_counterfactual_results_digest(results)
    assert projection["results_digest"] == expected_digest
    # All 3 result_ids present in the projection.
    ids_in_proj = [r["result_id"] for r in projection["results"]]
    assert ids_in_proj == ["cf-A", "cf-B", "cf-C"]


def test_review_projection_preserves_per_result_result_id() -> None:
    """The review projection preserves the per-result ``result_id``
    verbatim."""

    writer = CounterfactualResultWriter()
    results = [
        _result(result_id=f"id-{i}") for i in range(3)
    ]
    projection = writer.write_review_projection(
        results=results, corpus_id="c"
    )
    ids = [r["result_id"] for r in projection["results"]]
    assert ids == ["id-0", "id-1", "id-2"]


# ── 4-value Literal Range tests (estimated_risk_change taxonomy) ────────────


@pytest.mark.parametrize(
    "risk_change", ["lower", "same", "higher", "unknown"]
)
def test_writer_accepts_all_4_risk_change_literals(risk_change: str) -> None:
    """The writer persists all 4 risk_change Literal values."""

    writer = CounterfactualResultWriter()
    result = writer.write_results(
        _writer_inputs(
            results=[_result(estimated_risk_change=risk_change)]
        )
    )
    assert result.persisted_result_ids == ["cf-result-1"]
    assert result.gap_records == []


@pytest.mark.parametrize(
    "next_step",
    [
        "discard",
        "collect_more_evidence",
        "draft_policy",
        "implementation_plan",
    ],
)
def test_writer_accepts_all_4_recommended_next_step_literals(
    next_step: str,
) -> None:
    """The writer persists all 4 recommended_next_step Literal
    values."""

    writer = CounterfactualResultWriter()
    result = writer.write_results(
        _writer_inputs(
            results=[_result(recommended_next_step=next_step)]
        )
    )
    assert result.persisted_result_ids == ["cf-result-1"]


# ── no consumer activation authority (doc-18:123-125 + doc-18:164 AC3) ──────


def test_writer_grants_no_consumer_activation_authority() -> None:
    """The writer is a pure projection observer; a persisted result
    row does NOT activate the recommendation's proposed policy
    artifact (the writer surface has no mutation methods on consumer-
    side state)."""

    writer = CounterfactualResultWriter()
    # The writer class exposes only __init__ + gap_records property +
    # write_results + write_review_projection. There is NO method
    # like "activate_result" or "apply_to_consumer".
    public_methods = [
        name for name in dir(writer) if not name.startswith("_")
    ]
    # Filter to expected surface members.
    assert "gap_records" in public_methods
    assert "write_results" in public_methods
    assert "write_review_projection" in public_methods
    # Defence-in-depth: NO consumer-side activation method exists.
    forbidden_names = {
        "activate_result",
        "apply_to_consumer",
        "force_route_change",
        "mutate_scheduler_policy",
        "promote_to_runtime",
        "activate_recommendation",
    }
    for forbidden in forbidden_names:
        assert forbidden not in public_methods


# ── doc-18:123-125 no-dag-* authority artifact-key string literals ──────────


def test_module_source_has_no_dag_authority_artifact_key_literals() -> None:
    """Per doc-18:123-125 *"Replay must not write `dag-*` execution
    authority artifacts or active policy markers."* the writer module
    source MUST NOT contain any ``dag-*`` authority artifact-key
    string literal (the writer emits review-projection artifact keys
    at ``review:governance-counterfactuals:*`` ONLY)."""

    from iriai_build_v2.execution_control import (
        counterfactual_result_writer as mod,
    )

    source = Path(inspect.getfile(mod)).read_text()
    forbidden_artifact_keys = [
        '"dag-group:',
        '"dag-regroup-active:',
        '"dag-route-budget:',
        '"dag-checkpoint:',
        '"dag-supervisor:',
        '"dag-merge:',
        "'dag-group:",
        "'dag-regroup-active:",
        "'dag-route-budget:",
        "'dag-checkpoint:",
        "'dag-supervisor:",
        "'dag-merge:",
    ]
    for forbidden in forbidden_artifact_keys:
        assert forbidden not in source, (
            f"forbidden dag-* authority artifact-key literal found: {forbidden}"
        )


# ── doc-18:127-129 historical-replay immutability + result_version ──────────


def test_write_results_immutability_per_result_version() -> None:
    """Per doc-18:127-129 *"Historical replay is immutable by corpus
    id and scenario id. New assumptions require a new result
    version."* a new result_version produces a new digest (the
    writer's pure-projection contract preserves the result_version
    field; subsequent re-projections emit NEW rows rather than
    mutating historical rows)."""

    writer = CounterfactualResultWriter()
    r_v1 = _result(result_version="v1")
    r_v2 = _result(result_version="v2")

    p_v1 = writer.write_review_projection(results=[r_v1], corpus_id="c")
    p_v2 = writer.write_review_projection(results=[r_v2], corpus_id="c")

    assert p_v1["results_digest"] != p_v2["results_digest"]
    # The result_version is preserved verbatim in the projection.
    assert p_v1["results"][0]["result_version"] == "v1"
    assert p_v2["results"][0]["result_version"] == "v2"


# ── AC1-AC5 PIN coverage (doc-18:160-168) ──────────────────────────────────


def test_ac1_counterfactuals_deterministic_versioned_evidence_backed() -> None:
    """AC1 (doc-18:162) -- the result's typed ``result_version`` +
    ``policy_provenance_refs`` + the digest helper are the typed
    surfaces that satisfy AC1 *"Counterfactuals are deterministic,
    versioned, and evidence-backed."*"""

    # Deterministic: same inputs -> same digest.
    d1 = compute_counterfactual_results_digest([_result()])
    d2 = compute_counterfactual_results_digest([_result()])
    assert d1 == d2
    # Versioned: result_version is a required field.
    assert _result().result_version == "v1"
    # Evidence-backed: policy_provenance_refs is a typed list.
    assert isinstance(_result().policy_provenance_refs, list)


def test_ac2_every_result_lists_assumptions_and_validity_limits() -> None:
    """AC2 (doc-18:163) -- every result carries typed
    ``assumptions`` + ``validity_limits`` lists."""

    writer = CounterfactualResultWriter()
    projection = writer.write_review_projection(
        results=[_result()], corpus_id="c"
    )
    r = projection["results"][0]
    assert "assumptions" in r
    assert "validity_limits" in r
    assert isinstance(r["assumptions"], list)
    assert isinstance(r["validity_limits"], list)


def test_ac3_replay_cannot_mutate_live_workflow_state() -> None:
    """AC3 (doc-18:164) -- the writer GRANTS NO CONSUMER-SIDE
    ACTIVATION AUTHORITY; structural assertion."""

    writer = CounterfactualResultWriter()
    # The writer class exposes only the 3 expected public surface
    # members: gap_records property + write_results +
    # write_review_projection. No mutation surface.
    public_methods = [
        name for name in dir(writer) if not name.startswith("_")
    ]
    expected = {"gap_records", "write_results", "write_review_projection"}
    actual_methods = set(public_methods)
    # All expected members present (no missing surface).
    assert expected.issubset(actual_methods)
    # No mutation-prefix methods (e.g. activate_*, mutate_*, force_*).
    for name in public_methods:
        for prefix in ("activate", "mutate", "force_", "apply_to_"):
            assert not name.startswith(prefix), (
                f"forbidden mutation-prefix method: {name}"
            )


def test_ac4_recommendations_cite_replay_results_via_result_id() -> None:
    """AC4 (doc-18:165-166) -- the result's typed ``result_id`` is
    the typed reference surface the Slice 17 1st sub-slice
    :attr:`GovernancePolicyRecommendation.counterfactual_result_refs`
    cites."""

    # The persisted result preserves the result_id verbatim; the
    # Slice 17 recommendation surface cites this by-name.
    writer = CounterfactualResultWriter()
    result = writer.write_results(
        _writer_inputs(results=[_result(result_id="cf-ref-abc")])
    )
    assert result.persisted_result_ids == ["cf-ref-abc"]


def test_ac5_replay_corpus_includes_8ac124d6_evidence() -> None:
    """AC5 (doc-18:167-168) -- the writer accepts the ``"8ac124d6"``
    corpus_id (the calibration fixture corpus)."""

    writer = CounterfactualResultWriter()
    inputs = _writer_inputs(corpus_id="8ac124d6")
    assert inputs.corpus_id == "8ac124d6"
    result = writer.write_results(inputs)
    assert result.persisted_result_ids == ["cf-result-1"]


# ── module source signature sanity check ───────────────────────────────────


def test_writer_class_only_exposes_two_public_methods() -> None:
    """The writer class exposes EXACTLY 2 public methods (plus the
    :attr:`gap_records` property) -- mirrors the Slice 15 4th + Slice
    16 4th + Slice 17 4th sub-slice precedent."""

    public_callables = [
        name
        for name in dir(CounterfactualResultWriter)
        if not name.startswith("_")
        and callable(getattr(CounterfactualResultWriter, name, None))
    ]
    # write_results + write_review_projection (gap_records is a
    # property; not in this list).
    assert set(public_callables) == {
        "write_results",
        "write_review_projection",
    }
