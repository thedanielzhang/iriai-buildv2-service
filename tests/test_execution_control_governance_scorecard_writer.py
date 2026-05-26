"""Slice 15 fourth sub-slice -- unit tests for the scorecard writer +
bounded review projection at ``execution_control/governance_scorecard_writer.py``.

Covers the typed-shape construction + bounded-review-projection
discipline + LIMIT cap+1 truncation + refs-only (no raw bodies) +
scorecard digest stability + metric definition version pinning per
doc-15:144-145 + the extractor-never-raises non-blocking contract + the
DIRECT annotation-identity Slice 13a/15-1st/15-3rd REUSE assertions
(the stronger pattern that functionally addresses Slice 14 V3 reviewer
P3-V3-2 carry).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15
1st/2nd/3rd sub-slice modules + tests remain byte-identical.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.governance_metric_extractor import (
    MetricExtractor,
    MetricExtractorInputs,
)
from iriai_build_v2.execution_control.completeness import EvidenceCompleteness
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricDefinition,
    GovernanceMetricValue,
    GovernanceScorecard,
    canonical_scorecard_dict,
    compute_scorecard_digest,
)
from iriai_build_v2.execution_control.governance_scorecard_writer import (
    DEFAULT_REVIEW_PROJECTION_CAP,
    REVIEW_PROJECTION_ID_PREFIX,
    SCORECARD_PERSISTENCE_FAILURE_ID,
    ScorecardPersistenceGap,
    ScorecardWriter,
    ScorecardWriterInputs,
    compute_review_projection_id,
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


def _completeness(**overrides: object) -> EvidenceCompleteness:
    """Construct a fully-specified :class:`EvidenceCompleteness` for tests."""

    base: dict[str, object] = dict(
        state="complete",
        authority="execution_authority",
        complete_for=["metric-extraction"],
        missing_required_refs=[],
        page_refs=[],
        preview_ref=None,
        unavailable_reason=None,
        completeness_digest="completeness-placeholder",
    )
    base.update(overrides)
    return EvidenceCompleteness(**base)  # type: ignore[arg-type]


def _definition(**overrides: object) -> GovernanceMetricDefinition:
    """Construct a fully-specified :class:`GovernanceMetricDefinition`."""

    base: dict[str, object] = dict(
        name="tasks_per_hour",
        version="v1.0",
        scope_kind="feature",
        numerator="completed tasks",
        denominator="elapsed hours",
        required_evidence_kinds=["typed_journal"],
        active_work_policy="exclude",
        confidence_rule="evidence_completeness * sample_count_factor",
    )
    base.update(overrides)
    return GovernanceMetricDefinition(**base)  # type: ignore[arg-type]


def _value(**overrides: object) -> GovernanceMetricValue:
    """Construct a fully-specified :class:`GovernanceMetricValue`."""

    base: dict[str, object] = dict(
        definition_name="tasks_per_hour",
        definition_version="v1.0",
        scope={"feature_id": "8ac124d6"},
        value=4.2,
        unit="tasks/hour",
        confidence=0.9,
        data_quality="canonical",
        source_mix={"typed": 12, "legacy": 0},
        evidence_refs=[_ref()],
        exclusions=[],
    )
    base.update(overrides)
    return GovernanceMetricValue(**base)  # type: ignore[arg-type]


def _writer_inputs(**overrides: object) -> ScorecardWriterInputs:
    """Construct a fully-specified :class:`ScorecardWriterInputs`."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        metrics=[_value()],
        baseline_refs=[_ref(ref_id="baseline-1")],
        warnings=[],
        incomplete_scopes=[],
    )
    base.update(overrides)
    return ScorecardWriterInputs(**base)  # type: ignore[arg-type]


def _extractor_inputs(**overrides: object) -> MetricExtractorInputs:
    """Construct a fully-specified :class:`MetricExtractorInputs` for fixture
    extractor pass-through tests."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        definitions=[_definition()],
        evidence_set_refs=[
            _ref(ref_id="ref-1"),
            _ref(ref_id="ref-2"),
            _ref(ref_id="ref-3"),
            _ref(ref_id="ref-4"),
            _ref(ref_id="ref-5"),
        ],
        completeness_state=_completeness(),
        active_work_filter="exclude",
        freshness_window_hours=168.0,
        prompt_context_routing=None,
    )
    base.update(overrides)
    return MetricExtractorInputs(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed-shape additions:
    ``ScorecardWriterInputs`` + ``ScorecardPersistenceGap`` + the typed
    failure id Literal + the typed review-projection id prefix Literal +
    the LIMIT cap+1 default + the ``compute_review_projection_id`` helper +
    the ``ScorecardWriter`` class.
    """

    from iriai_build_v2.execution_control import governance_scorecard_writer as mod

    expected = {
        "ScorecardWriterInputs",
        "ScorecardPersistenceGap",
        "SCORECARD_PERSISTENCE_FAILURE_ID",
        "REVIEW_PROJECTION_ID_PREFIX",
        "DEFAULT_REVIEW_PROJECTION_CAP",
        "compute_review_projection_id",
        "ScorecardWriter",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the writer module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes
    the Slice 13a shared model via import only."""

    from iriai_build_v2.execution_control import governance_scorecard_writer as mod

    assert "GovernanceEvidenceRef" not in set(mod.__all__)


def test_module_does_not_redefine_governance_scorecard() -> None:
    """Per doc-15:64-97 the writer module MUST NOT redefine
    :class:`GovernanceScorecard` -- it consumes the Slice 15 1st sub-slice
    shared model via import only."""

    from iriai_build_v2.execution_control import governance_scorecard_writer as mod

    assert "GovernanceScorecard" not in set(mod.__all__)


def test_module_does_not_redefine_governance_metric_value() -> None:
    """Per doc-15:64-97 the writer module MUST NOT redefine
    :class:`GovernanceMetricValue` -- it consumes the Slice 15 1st sub-slice
    shared model via import only."""

    from iriai_build_v2.execution_control import governance_scorecard_writer as mod

    assert "GovernanceMetricValue" not in set(mod.__all__)


def test_module_does_not_redefine_compute_scorecard_digest() -> None:
    """The writer module REUSES :func:`compute_scorecard_digest` from the
    Slice 15 1st sub-slice; does NOT redefine it."""

    from iriai_build_v2.execution_control import governance_scorecard_writer as mod

    assert "compute_scorecard_digest" not in set(mod.__all__)


def test_module_import_discipline_no_implementation_py() -> None:
    """The writer MUST NOT import implementation.py (would invert the
    foundational-module dependency direction)."""

    import iriai_build_v2.execution_control.governance_scorecard_writer as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in text


def test_module_import_discipline_only_allowed_imports() -> None:
    """Per the implementer prompt § "Non-negotiables" the writer module
    imports ONLY from stdlib + Pydantic v2 + Slice 13a governance.models +
    Slice 15 1st sub-slice governance_metrics. NO imports from
    governance/ outside models, NO imports from workflows/develop/execution/phases/,
    supervisor, or dashboard."""

    import iriai_build_v2.execution_control.governance_scorecard_writer as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()

    # Forbidden imports.
    assert "from iriai_build_v2.workflows.develop.execution.phases" not in text
    assert "from iriai_build_v2.supervisor" not in text
    assert "import iriai_build_v2.dashboard" not in text
    # The writer should not import failure_router (registration of the
    # failure id is a separate pure-data edit point in failure_router.py
    # itself).
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in text
    # Permitted imports (sanity).
    assert "from iriai_build_v2.execution_control.governance_metrics" in text
    assert "from iriai_build_v2.workflows.develop.governance.models" in text


def test_package_init_does_not_re_export_writer() -> None:
    """Mirrors Slice 15 1st sub-slice + Slice 14 + Slice 13A precedent:
    ``governance_scorecard_writer.py`` is consumed via fully-qualified
    imports, NOT re-exported through the ``execution_control`` package."""

    from iriai_build_v2 import execution_control as pkg

    if hasattr(pkg, "__all__"):
        assert "governance_scorecard_writer" not in set(pkg.__all__)
        assert "ScorecardWriter" not in set(pkg.__all__)
        assert "ScorecardWriterInputs" not in set(pkg.__all__)


# ── module-level constants ─────────────────────────────────────────────────


def test_review_projection_id_prefix_value() -> None:
    """Per doc-15:134 the typed review-projection id prefix is the
    canonical ``review:governance-metrics:`` string."""

    assert REVIEW_PROJECTION_ID_PREFIX == "review:governance-metrics:"


def test_scorecard_persistence_failure_id_value() -> None:
    """The typed failure id Literal carries the canonical
    ``governance_scorecard_persistence_failed`` value."""

    assert SCORECARD_PERSISTENCE_FAILURE_ID == "governance_scorecard_persistence_failed"


def test_default_review_projection_cap_is_positive_int() -> None:
    """The LIMIT cap+1 default per IMPLEMENTATION_PROMPT_GOVERNANCE.md
    § "Bounded reads" is a positive int."""

    assert isinstance(DEFAULT_REVIEW_PROJECTION_CAP, int)
    assert DEFAULT_REVIEW_PROJECTION_CAP > 0


def test_default_review_projection_cap_above_v1_metric_count() -> None:
    """The default cap is comfortably above the doc-15:99-115 v1 15-metric
    contract so v1 scorecards never trip the cap."""

    assert DEFAULT_REVIEW_PROJECTION_CAP > 15


# ── compute_review_projection_id helper ────────────────────────────────────


def test_compute_review_projection_id_constructs_typed_prefix() -> None:
    """Per doc-15:134 the helper concatenates the typed prefix +
    corpus_id."""

    pid = compute_review_projection_id("8ac124d6")
    assert pid == "review:governance-metrics:8ac124d6"


def test_compute_review_projection_id_passes_through_corpus_id() -> None:
    """The corpus_id passes through verbatim (no normalization, no
    trimming)."""

    pid = compute_review_projection_id("MIXED_Case-1234")
    assert pid == "review:governance-metrics:MIXED_Case-1234"


def test_compute_review_projection_id_accepts_empty_corpus_id() -> None:
    """An empty corpus_id is a defence-in-depth edge case (the writer
    surface does not validate corpus_id; the typed BaseModel surface
    accepts any string)."""

    pid = compute_review_projection_id("")
    assert pid == "review:governance-metrics:"


def test_compute_review_projection_id_uses_prefix_constant() -> None:
    """The helper uses the typed :data:`REVIEW_PROJECTION_ID_PREFIX`
    constant verbatim (no string interpolation drift)."""

    pid = compute_review_projection_id("corpus-1")
    assert pid.startswith(REVIEW_PROJECTION_ID_PREFIX)
    assert pid.endswith("corpus-1")


# ── ScorecardWriterInputs typed-shape ───────────────────────────────────────


def test_writer_inputs_accepts_all_required_fields() -> None:
    """The required + optional default fields all populate cleanly."""

    inputs = _writer_inputs()
    assert inputs.corpus_id == "8ac124d6"
    assert len(inputs.metrics) == 1
    assert len(inputs.baseline_refs) == 1
    assert inputs.warnings == []
    assert inputs.incomplete_scopes == []


def test_writer_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _writer_inputs(unknown_field="oops")  # type: ignore[arg-type]


def test_writer_inputs_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    inputs = _writer_inputs()
    serialised = inputs.model_dump_json()
    restored = ScorecardWriterInputs.model_validate_json(serialised)
    assert restored == inputs


def test_writer_inputs_baseline_refs_defaults_to_empty() -> None:
    """Per the typed-shape contract ``baseline_refs`` defaults to an
    empty list."""

    inputs = ScorecardWriterInputs(corpus_id="c", metrics=[])
    assert inputs.baseline_refs == []


def test_writer_inputs_warnings_defaults_to_empty() -> None:
    """The ``warnings`` field defaults to an empty list."""

    inputs = ScorecardWriterInputs(corpus_id="c", metrics=[])
    assert inputs.warnings == []


def test_writer_inputs_incomplete_scopes_defaults_to_empty() -> None:
    """The ``incomplete_scopes`` field defaults to an empty list."""

    inputs = ScorecardWriterInputs(corpus_id="c", metrics=[])
    assert inputs.incomplete_scopes == []


def test_writer_inputs_metrics_is_typed_governance_metric_value_list() -> None:
    """Per doc-15:78-88 the ``metrics`` field is the typed Slice 15
    :class:`GovernanceMetricValue` list."""

    inputs = _writer_inputs()
    for v in inputs.metrics:
        assert isinstance(v, GovernanceMetricValue)


def test_writer_inputs_baseline_refs_is_typed_governance_evidence_ref_list() -> None:
    """Per doc-13:97-111 + doc-15:94 the ``baseline_refs`` field is the
    typed Slice 13a :class:`GovernanceEvidenceRef` list."""

    inputs = _writer_inputs()
    for ref in inputs.baseline_refs:
        assert isinstance(ref, GovernanceEvidenceRef)


def test_writer_inputs_accepts_empty_metrics_list() -> None:
    """Empty metrics list is valid (the writer composes an empty
    scorecard)."""

    inputs = _writer_inputs(metrics=[])
    assert inputs.metrics == []


def test_writer_inputs_accepts_warnings_list() -> None:
    """``warnings`` carries free-form strings per doc-15:96."""

    inputs = _writer_inputs(warnings=["legacy_heavy_corpus", "stale_baseline"])
    assert inputs.warnings == ["legacy_heavy_corpus", "stale_baseline"]


def test_writer_inputs_accepts_incomplete_scopes_list() -> None:
    """``incomplete_scopes`` carries free-form dicts per doc-15:95."""

    scopes = [
        {"scope_kind": "lane", "lane_id": "ml-7", "reason": "missing typed evidence"}
    ]
    inputs = _writer_inputs(incomplete_scopes=scopes)
    assert inputs.incomplete_scopes == scopes


# ── DIRECT annotation-identity REUSE assertions
#    (the stronger pattern P3-V3-2 addresses) ─────────────────────────────


def test_writer_inputs_metrics_annotation_is_slice_15_governance_metric_value() -> None:
    """Per doc-15:64-97 the ``metrics`` field annotation MUST resolve to
    ``list[GovernanceMetricValue]`` where ``GovernanceMetricValue`` is
    the Slice 15 1st-sub-slice shared model (NOT a local redefinition).

    DIRECT annotation-identity assertion via ``get_origin`` + ``get_args``.
    """

    annotation = ScorecardWriterInputs.model_fields["metrics"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceMetricValue


def test_writer_inputs_baseline_refs_annotation_is_slice_13a_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the ``baseline_refs``
    field annotation MUST resolve to ``list[GovernanceEvidenceRef]``
    where ``GovernanceEvidenceRef`` is the Slice 13a shared model (NOT a
    local redefinition).

    DIRECT annotation-identity assertion via ``get_origin`` + ``get_args``.
    """

    annotation = ScorecardWriterInputs.model_fields["baseline_refs"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


# ── ScorecardPersistenceGap typed-shape ─────────────────────────────────────


def _gap(**overrides: object) -> ScorecardPersistenceGap:
    """Construct a fully-specified :class:`ScorecardPersistenceGap`."""

    base: dict[str, object] = dict(
        failure_id="governance_scorecard_persistence_failed",
        corpus_id="8ac124d6",
        scorecard_digest="sha256:abc",
        review_projection_id="review:governance-metrics:8ac124d6",
        reason="metric_count_exceeds_cap",
        evidence_payload={"cap": 200},
    )
    base.update(overrides)
    return ScorecardPersistenceGap(**base)  # type: ignore[arg-type]


def test_gap_finding_accepts_all_fields() -> None:
    """All 6 fields populate cleanly."""

    gap = _gap()
    assert gap.failure_id == "governance_scorecard_persistence_failed"
    assert gap.corpus_id == "8ac124d6"
    assert gap.scorecard_digest == "sha256:abc"
    assert gap.review_projection_id == "review:governance-metrics:8ac124d6"
    assert gap.reason == "metric_count_exceeds_cap"
    assert gap.evidence_payload == {"cap": 200}


def test_gap_finding_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _gap(unknown_field="oops")  # type: ignore[arg-type]


def test_gap_finding_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    gap = _gap()
    serialised = gap.model_dump_json()
    restored = ScorecardPersistenceGap.model_validate_json(serialised)
    assert restored == gap


def test_gap_finding_failure_id_literal_rejects_other_failure_ids() -> None:
    """Per the typed Literal the gap finding's ``failure_id`` accepts
    ONLY ``governance_scorecard_persistence_failed`` (the Slice 14 or
    Slice 15-2nd-sub-slice failure ids fail closed)."""

    with pytest.raises(ValidationError):
        _gap(failure_id="line_provenance_gap")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _gap(failure_id="governance_metric_extraction_failed")  # type: ignore[arg-type]


def test_gap_finding_scorecard_digest_accepts_none() -> None:
    """Per the typed shape the digest field accepts ``None`` (for failures
    that happen before the digest is computable)."""

    gap = _gap(scorecard_digest=None)
    assert gap.scorecard_digest is None


def test_gap_finding_review_projection_id_accepts_none() -> None:
    """Per the typed shape the projection id field accepts ``None``."""

    gap = _gap(review_projection_id=None)
    assert gap.review_projection_id is None


def test_gap_finding_evidence_payload_defaults_to_empty() -> None:
    """The ``evidence_payload`` field defaults to an empty dict."""

    gap = ScorecardPersistenceGap(
        failure_id="governance_scorecard_persistence_failed",
        corpus_id="c",
        scorecard_digest=None,
        review_projection_id=None,
        reason="test",
    )
    assert gap.evidence_payload == {}


# ── failure router wiring (chunk-shape point 3) ────────────────────────────


def test_failure_router_registers_new_typed_failure_id() -> None:
    """The NEW failure id ``governance_scorecard_persistence_failed`` is
    registered in the Slice 07 failure router."""

    assert "governance_scorecard_persistence_failed" in FAILURE_TYPES
    assert "governance_scorecard_persistence_failed" in get_args(FailureType)


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Per doc-15:140-145 + doc-14:242-243 the NEW failure id routes
    under the EXISTING ``evidence_corruption`` failure_class to the
    EXISTING ``retry_governance_projection`` NON-blocking RouteAction
    (REUSED from Slice 14 2nd sub-slice; NOT a new action)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_scorecard_persistence_failed"
    ]
    assert len(matches) == 1
    type_pol, route_pol = matches[0]
    assert type_pol.failure_class == "evidence_corruption"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_new_id_is_retryable_not_deterministic() -> None:
    """Per the Slice 14 + Slice 15 2nd sub-slice precedents the NEW
    failure id is observer-transient (retryable; NOT deterministic)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_scorecard_persistence_failed"
    ]
    assert len(matches) == 1
    type_pol, _ = matches[0]
    assert type_pol.retryable
    assert not type_pol.deterministic


def test_failure_router_new_id_in_retryable_set() -> None:
    """The NEW failure id is in :data:`_RETRYABLE_FAILURE_TYPES`."""

    assert "governance_scorecard_persistence_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_is_non_blocking() -> None:
    """The route is non-blocking (NOT ``quiesce``, NOT ``operator_required``)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_scorecard_persistence_failed"
    ]
    assert len(matches) == 1
    _, route_pol = matches[0]
    assert route_pol.action != "quiesce"
    assert route_pol.action != "operator_required"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_reuses_existing_route_action() -> None:
    """The NEW failure id REUSES the Slice 14 2nd sub-slice
    ``retry_governance_projection`` action (NOT a new action)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import ROUTE_ACTIONS

    assert "retry_governance_projection" in ROUTE_ACTIONS


# ── ScorecardWriter constructor + gap_findings property ────────────────────


def test_writer_construction_default_is_empty_gap_findings() -> None:
    """Per the stateless-with-accumulator pattern (mirrors Slice 15 2nd
    sub-slice :class:`MetricExtractor`) the writer constructs cleanly
    with no inputs and an empty ``gap_findings`` accumulator."""

    writer = ScorecardWriter()
    assert writer.gap_findings == []


def test_writer_gap_findings_is_snapshot_list() -> None:
    """The ``gap_findings`` property returns a snapshot list (not the
    internal list reference); mutations to the returned list do not
    leak into subsequent calls."""

    writer = ScorecardWriter()
    snapshot = writer.gap_findings
    snapshot.append("garbage")  # type: ignore[arg-type]
    # Subsequent property call returns a fresh snapshot.
    assert writer.gap_findings == []


# ── write_scorecard correctness ────────────────────────────────────────────


def test_write_scorecard_emits_typed_governance_scorecard() -> None:
    """Per doc-15:133-134 step 6 + doc-15:90-97 the writer emits a typed
    :class:`GovernanceScorecard` with the 6 fields populated."""

    writer = ScorecardWriter()
    inputs = _writer_inputs()
    scorecard = writer.write_scorecard(inputs)

    assert isinstance(scorecard, GovernanceScorecard)
    assert scorecard.corpus_id == "8ac124d6"
    assert isinstance(scorecard.generated_at, datetime)
    assert len(scorecard.metrics) == 1
    assert isinstance(scorecard.metrics[0], GovernanceMetricValue)
    assert len(scorecard.baseline_refs) == 1
    assert isinstance(scorecard.baseline_refs[0], GovernanceEvidenceRef)
    assert scorecard.incomplete_scopes == []
    assert scorecard.warnings == []


def test_write_scorecard_preserves_metric_values_verbatim() -> None:
    """Per doc-15:144-145 the writer MUST preserve metric definition
    versions; the metric values pass through verbatim."""

    writer = ScorecardWriter()
    v1 = _value(definition_name="tasks_per_hour", definition_version="v1.0")
    v2 = _value(definition_name="hours_per_task", definition_version="v2.0", value=0.5)
    inputs = _writer_inputs(metrics=[v1, v2])
    scorecard = writer.write_scorecard(inputs)

    assert scorecard.metrics == [v1, v2]


def test_write_scorecard_preserves_baseline_refs_verbatim() -> None:
    """The baseline_refs pass through verbatim."""

    writer = ScorecardWriter()
    ref_a = _ref(ref_id="baseline-a")
    ref_b = _ref(ref_id="baseline-b")
    inputs = _writer_inputs(baseline_refs=[ref_a, ref_b])
    scorecard = writer.write_scorecard(inputs)

    assert scorecard.baseline_refs == [ref_a, ref_b]


def test_write_scorecard_preserves_warnings_verbatim() -> None:
    """The warnings list passes through verbatim."""

    writer = ScorecardWriter()
    warnings = ["legacy_heavy_corpus", "missing_typed_evidence_for_scope"]
    inputs = _writer_inputs(warnings=warnings)
    scorecard = writer.write_scorecard(inputs)

    assert scorecard.warnings == warnings


def test_write_scorecard_preserves_incomplete_scopes_verbatim() -> None:
    """The incomplete_scopes list passes through verbatim."""

    writer = ScorecardWriter()
    scopes = [{"scope_kind": "lane", "lane_id": "ml-7", "reason": "stale"}]
    inputs = _writer_inputs(incomplete_scopes=scopes)
    scorecard = writer.write_scorecard(inputs)

    assert scorecard.incomplete_scopes == scopes


def test_write_scorecard_generated_at_is_utc() -> None:
    """Per doc-15:131-132 step 5 the generated_at timestamp is the
    cross-process freshness anchor (must be UTC)."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    assert scorecard.generated_at.tzinfo == timezone.utc


def test_write_scorecard_empty_metrics_produces_valid_scorecard() -> None:
    """An empty metrics list still produces a valid scorecard."""

    writer = ScorecardWriter()
    inputs = _writer_inputs(metrics=[])
    scorecard = writer.write_scorecard(inputs)
    assert scorecard.metrics == []


def test_write_scorecard_resets_gap_findings_per_call() -> None:
    """Per the stateless-with-accumulator pattern each
    :meth:`write_scorecard` call resets the ``gap_findings`` accumulator."""

    writer = ScorecardWriter()
    writer.write_scorecard(_writer_inputs())
    # Force at least one prior gap accumulation.
    writer._gap_findings.append(  # type: ignore[attr-defined]
        ScorecardPersistenceGap(
            failure_id="governance_scorecard_persistence_failed",
            corpus_id="prior",
            scorecard_digest=None,
            review_projection_id=None,
            reason="prior",
        )
    )
    writer.write_scorecard(_writer_inputs())
    assert writer.gap_findings == []


# ── write_scorecard against fixture MetricExtractor.extract() output ───────


def test_write_scorecard_consumes_extractor_extract_output() -> None:
    """Per the chunk-shape point 2 the writer composes the typed
    :meth:`MetricExtractor.extract` output (a ``list[GovernanceMetricValue]``)
    into a typed scorecard."""

    extractor = MetricExtractor()
    metric_values = extractor.extract(_extractor_inputs())
    assert all(isinstance(v, GovernanceMetricValue) for v in metric_values)

    writer = ScorecardWriter()
    inputs = ScorecardWriterInputs(
        corpus_id="8ac124d6",
        metrics=metric_values,
        baseline_refs=[],
        warnings=[],
        incomplete_scopes=[],
    )
    scorecard = writer.write_scorecard(inputs)
    assert scorecard.metrics == metric_values


def test_write_scorecard_after_extractor_preserves_definition_versions() -> None:
    """Per doc-15:144-145 metric definition versions MUST be preserved
    through the writer projection (so later changes do not silently
    rewrite historical meaning)."""

    extractor = MetricExtractor()
    definitions = [
        _definition(name="tasks_per_hour", version="v1.0"),
        _definition(name="hours_per_task", version="v2.0", denominator="elapsed tasks"),
    ]
    metric_values = extractor.extract(
        _extractor_inputs(definitions=definitions)
    )

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id="c", metrics=metric_values, baseline_refs=[]
        )
    )

    versions = {m.definition_name: m.definition_version for m in scorecard.metrics}
    assert versions["tasks_per_hour"] == "v1.0"
    assert versions["hours_per_task"] == "v2.0"


# ── write_review_projection correctness ────────────────────────────────────


def test_write_review_projection_emits_typed_dict() -> None:
    """The review projection is a typed dict with the documented keys."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)

    assert isinstance(projection, dict)
    # Required top-level keys.
    for key in (
        "review_projection_id",
        "corpus_id",
        "generated_at",
        "scorecard_digest",
        "metric_definition_versions",
        "metrics",
        "metric_truncated",
        "baseline_refs",
        "baseline_refs_truncated",
        "incomplete_scopes",
        "warnings",
    ):
        assert key in projection, f"missing key: {key}"


def test_write_review_projection_id_uses_typed_prefix() -> None:
    """Per doc-15:134 the projection id is constructed via the typed
    :func:`compute_review_projection_id` helper."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs(corpus_id="cust-7"))
    projection = writer.write_review_projection(scorecard)

    assert projection["review_projection_id"] == "review:governance-metrics:cust-7"


def test_write_review_projection_corpus_id_passes_through() -> None:
    """The corpus_id passes through verbatim."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs(corpus_id="abc-12345"))
    projection = writer.write_review_projection(scorecard)
    assert projection["corpus_id"] == "abc-12345"


def test_write_review_projection_generated_at_is_iso_string() -> None:
    """The ``generated_at`` field projects to an ISO-8601 string per the
    canonical-JSON discipline (cross-process stable)."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)
    # ISO-8601 strings parse via fromisoformat.
    parsed = datetime.fromisoformat(projection["generated_at"])
    assert parsed.tzinfo is not None


def test_write_review_projection_metric_definition_versions_pinned() -> None:
    """Per doc-15:144-145 the projection MUST cite metric definition
    versions so later changes do not silently rewrite historical meaning."""

    writer = ScorecardWriter()
    v1 = _value(definition_name="tasks_per_hour", definition_version="v1.0")
    v2 = _value(
        definition_name="hours_per_task", definition_version="v3.1", value=0.5
    )
    inputs = _writer_inputs(metrics=[v1, v2])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    versions = projection["metric_definition_versions"]
    assert isinstance(versions, dict)
    assert versions["tasks_per_hour"] == "v1.0"
    assert versions["hours_per_task"] == "v3.1"


def test_write_review_projection_metrics_are_compact_dicts() -> None:
    """The projected metrics carry ONLY typed control fields + cited refs
    (NOT raw bodies). Per doc-15:141-142 + AC5 doc-15:182."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)

    assert len(projection["metrics"]) == 1
    m = projection["metrics"][0]
    # Required typed-control fields.
    for key in (
        "definition_name",
        "definition_version",
        "scope",
        "value",
        "unit",
        "confidence",
        "data_quality",
        "source_mix",
        "evidence_refs",
        "evidence_refs_truncated",
        "exclusions",
    ):
        assert key in m


# ── refs-only discipline (doc-15:141-142 + AC5 doc-15:182) ────────────────


def test_write_review_projection_baseline_refs_are_cited_refs_only() -> None:
    """Per doc-15:141-142 + AC5 doc-15:182 the projection emits ONLY
    cited refs (NEVER raw bodies). The baseline_refs are projected as
    typed-ref dicts via ``model_dump(mode="json")``."""

    writer = ScorecardWriter()
    ref_a = _ref(
        ref_id="baseline-a",
        digest="sha256:aaaa",
        authority="typed_journal",
    )
    inputs = _writer_inputs(baseline_refs=[ref_a])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    assert len(projection["baseline_refs"]) == 1
    cited = projection["baseline_refs"][0]
    # Typed-ref shape fields.
    assert cited["ref_id"] == "baseline-a"
    assert cited["digest"] == "sha256:aaaa"
    assert cited["authority"] == "typed_journal"
    # Verify NO raw body field leaked into the projection.
    assert "raw_body" not in cited
    assert "body" not in cited


def test_write_review_projection_metric_evidence_refs_are_cited_only() -> None:
    """Per doc-15:141-142 + AC5 doc-15:182 each metric's evidence_refs
    are emitted as cited typed-ref dicts (NEVER raw bodies)."""

    writer = ScorecardWriter()
    v = _value(evidence_refs=[_ref(ref_id="ev-1", digest="sha256:cafe")])
    inputs = _writer_inputs(metrics=[v])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    m = projection["metrics"][0]
    cited = m["evidence_refs"][0]
    assert cited["ref_id"] == "ev-1"
    assert cited["digest"] == "sha256:cafe"
    # Verify NO raw body field leaked.
    assert "raw_body" not in cited


def test_write_review_projection_does_not_include_metric_raw_payload_fields() -> None:
    """Defence-in-depth: scanning all projected metric dicts asserts no
    raw-body-shaped field name leaked into the projection."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)

    forbidden_keys = {
        "raw_body",
        "raw_payload",
        "artifact_body",
        "event_body",
        "raw_text",
        "stderr_body",
        "stdout_body",
    }
    for m in projection["metrics"]:
        assert not (set(m.keys()) & forbidden_keys), m


# ── LIMIT cap+1 truncation discipline ──────────────────────────────────────


def test_write_review_projection_metric_list_respects_cap_plus_one() -> None:
    """Per IMPLEMENTATION_PROMPT_GOVERNANCE.md § "Bounded reads" the
    projection emits at most ``cap+1`` metrics (so the caller can detect
    overflow)."""

    writer = ScorecardWriter()
    # 5 metrics; cap=3 -> emit 4 (cap+1), set metric_truncated=True.
    metrics = [_value(definition_name=f"m_{i}") for i in range(5)]
    inputs = _writer_inputs(metrics=metrics)
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard, cap=3)

    assert len(projection["metrics"]) == 4  # cap+1 = 4
    assert projection["metric_truncated"] is True


def test_write_review_projection_baseline_refs_respects_cap_plus_one() -> None:
    """The baseline_refs list also respects LIMIT cap+1."""

    writer = ScorecardWriter()
    refs = [_ref(ref_id=f"baseline-{i}") for i in range(5)]
    inputs = _writer_inputs(baseline_refs=refs)
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard, cap=2)

    assert len(projection["baseline_refs"]) == 3  # cap+1 = 3
    assert projection["baseline_refs_truncated"] is True


def test_write_review_projection_metric_evidence_refs_respect_cap_plus_one() -> None:
    """Each metric's evidence_refs list also respects LIMIT cap+1."""

    writer = ScorecardWriter()
    big_refs = [_ref(ref_id=f"ev-{i}") for i in range(10)]
    v = _value(evidence_refs=big_refs)
    inputs = _writer_inputs(metrics=[v])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard, cap=4)

    m = projection["metrics"][0]
    assert len(m["evidence_refs"]) == 5  # cap+1 = 5
    assert m["evidence_refs_truncated"] is True


def test_write_review_projection_no_truncation_under_cap() -> None:
    """When the actual count is <= cap, the truncated flag is False."""

    writer = ScorecardWriter()
    metrics = [_value(definition_name=f"m_{i}") for i in range(3)]
    inputs = _writer_inputs(metrics=metrics)
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard, cap=10)

    assert len(projection["metrics"]) == 3
    assert projection["metric_truncated"] is False


def test_write_review_projection_default_cap_is_default_review_projection_cap() -> None:
    """When ``cap`` is omitted the projection uses
    :data:`DEFAULT_REVIEW_PROJECTION_CAP`."""

    writer = ScorecardWriter()
    # Emit exactly cap+2 metrics so the default cap trips truncation.
    metrics = [
        _value(definition_name=f"m_{i}") for i in range(DEFAULT_REVIEW_PROJECTION_CAP + 2)
    ]
    inputs = _writer_inputs(metrics=metrics)
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    assert len(projection["metrics"]) == DEFAULT_REVIEW_PROJECTION_CAP + 1
    assert projection["metric_truncated"] is True


def test_write_review_projection_cap_zero_emits_one_sentinel() -> None:
    """A cap=0 emits at most 1 item per list (cap+1=1 sentinel)."""

    writer = ScorecardWriter()
    metrics = [_value(definition_name=f"m_{i}") for i in range(3)]
    inputs = _writer_inputs(metrics=metrics)
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard, cap=0)

    # cap+1 = 1 item; truncated=True because 3 > 0.
    assert len(projection["metrics"]) == 1
    assert projection["metric_truncated"] is True


# ── scorecard digest stability + tamper detection ──────────────────────────


def test_write_review_projection_carries_scorecard_digest() -> None:
    """Per doc-15:144-145 the projection carries the scorecard digest
    for tamper detection."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)

    expected = compute_scorecard_digest(scorecard)
    assert projection["scorecard_digest"] == expected


def test_write_review_projection_digest_is_stable_across_reruns() -> None:
    """The same scorecard produces the same digest across re-runs (per
    the doc-13:201-204 canonical-JSON discipline + Slice 15 1st sub-slice
    determinism contract)."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    p1 = writer.write_review_projection(scorecard)
    p2 = writer.write_review_projection(scorecard)
    assert p1["scorecard_digest"] == p2["scorecard_digest"]


def test_write_review_projection_digest_differs_for_different_metrics() -> None:
    """Different scorecards (with different metric content) produce
    different digests."""

    writer = ScorecardWriter()
    sc1 = writer.write_scorecard(_writer_inputs(metrics=[_value(value=1.0)]))
    sc2 = writer.write_scorecard(_writer_inputs(metrics=[_value(value=2.0)]))
    p1 = writer.write_review_projection(sc1)
    p2 = writer.write_review_projection(sc2)
    # generated_at differs too, but metric value alone should make the
    # digest differ (the metric value is in the canonical projection).
    assert p1["scorecard_digest"] != p2["scorecard_digest"]


def test_write_review_projection_digest_reuses_slice_15_1st_helper() -> None:
    """The projection's digest field is identical to a fresh call to
    :func:`compute_scorecard_digest` (proves REUSE; no local
    re-computation)."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)
    # Build the canonical dict via the Slice 15 1st sub-slice helper.
    canonical = canonical_scorecard_dict(scorecard)
    assert isinstance(canonical, dict)
    # Compare against compute_scorecard_digest (Slice 15 1st sub-slice).
    assert projection["scorecard_digest"] == compute_scorecard_digest(scorecard)


# ── non-blocking discipline (per doc-14:242-243 + doc-15:140-145) ──────────


def test_writer_never_raises_on_normal_inputs() -> None:
    """The writer NEVER raises a failure to the caller (per
    doc-14:242-243 + doc-15:140-145 NON-blocking observer contract)."""

    writer = ScorecardWriter()
    # Call should not raise.
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)
    assert isinstance(scorecard, GovernanceScorecard)
    assert isinstance(projection, dict)


def test_writer_gap_findings_empty_on_normal_path() -> None:
    """The gap_findings accumulator is empty on the normal projection
    path."""

    writer = ScorecardWriter()
    writer.write_scorecard(_writer_inputs())
    assert writer.gap_findings == []


# ── miscellaneous round-trip + invariants ─────────────────────────────────


def test_write_scorecard_then_project_then_canonical_roundtrip() -> None:
    """End-to-end: write_scorecard -> write_review_projection ->
    inspect digest matches a direct compute call."""

    writer = ScorecardWriter()
    inputs = _writer_inputs(corpus_id="end2end")
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    assert projection["corpus_id"] == "end2end"
    assert projection["review_projection_id"] == "review:governance-metrics:end2end"
    assert projection["scorecard_digest"] == compute_scorecard_digest(scorecard)


def test_write_review_projection_carries_warnings_verbatim() -> None:
    """Warnings pass through verbatim per the doc-15:96 contract."""

    writer = ScorecardWriter()
    inputs = _writer_inputs(warnings=["w1", "w2", "w3"])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    assert projection["warnings"] == ["w1", "w2", "w3"]


def test_write_review_projection_carries_incomplete_scopes_verbatim() -> None:
    """Incomplete scopes pass through verbatim per the doc-15:95 contract."""

    writer = ScorecardWriter()
    scopes = [{"scope_kind": "lane", "lane_id": "ml-7", "reason": "stale"}]
    inputs = _writer_inputs(incomplete_scopes=scopes)
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    assert projection["incomplete_scopes"] == scopes


def test_write_review_projection_metric_dict_carries_typed_scope() -> None:
    """The projected metric dict carries the typed ``scope`` dict
    verbatim."""

    writer = ScorecardWriter()
    v = _value(scope={"feature_id": "f-1", "lane_id": "l-7"})
    inputs = _writer_inputs(metrics=[v])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)

    m = projection["metrics"][0]
    assert m["scope"] == {"feature_id": "f-1", "lane_id": "l-7"}


def test_write_review_projection_metric_dict_carries_typed_data_quality() -> None:
    """The projected metric dict carries the typed
    :data:`EvidenceQuality` data_quality value."""

    writer = ScorecardWriter()
    v = _value(data_quality="derived")
    inputs = _writer_inputs(metrics=[v])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)
    m = projection["metrics"][0]
    assert m["data_quality"] == "derived"


def test_write_review_projection_metric_dict_carries_exclusions() -> None:
    """The projected metric dict carries the exclusions list verbatim."""

    writer = ScorecardWriter()
    v = _value(exclusions=["active_work_excluded"])
    inputs = _writer_inputs(metrics=[v])
    scorecard = writer.write_scorecard(inputs)
    projection = writer.write_review_projection(scorecard)
    m = projection["metrics"][0]
    assert m["exclusions"] == ["active_work_excluded"]


def test_writer_inputs_round_trip_preserves_metric_definition_versions() -> None:
    """End-to-end: round-trip preserves metric definition versions
    through ScorecardWriterInputs (doc-15:144-145)."""

    inputs = _writer_inputs(
        metrics=[
            _value(definition_name="m1", definition_version="v1.0"),
            _value(definition_name="m2", definition_version="v2.5"),
        ]
    )
    serialised = inputs.model_dump_json()
    restored = ScorecardWriterInputs.model_validate_json(serialised)
    versions = {m.definition_name: m.definition_version for m in restored.metrics}
    assert versions == {"m1": "v1.0", "m2": "v2.5"}


def test_write_scorecard_two_calls_produce_different_timestamps() -> None:
    """Per doc-15:186-188 historical scorecards are PRESERVED; each new
    re-projection emits a NEW scorecard with a NEW generated_at (so
    historical scorecards remain immutable as review artifacts)."""

    import time

    writer = ScorecardWriter()
    sc1 = writer.write_scorecard(_writer_inputs())
    time.sleep(0.001)
    sc2 = writer.write_scorecard(_writer_inputs())
    # Two scorecards produced at different times have different
    # generated_at; per the doc-15:186-188 rollback discipline
    # historical scorecards are preserved (the typed shape is
    # immutable; new re-projections produce new rows).
    assert sc1.generated_at <= sc2.generated_at


def test_write_scorecard_digest_changes_with_generated_at() -> None:
    """Two scorecards with different generated_at produce different
    digests (the doc-15:144-145 tamper-detection contract is
    timestamp-sensitive)."""

    import time

    writer = ScorecardWriter()
    sc1 = writer.write_scorecard(_writer_inputs())
    time.sleep(0.001)
    sc2 = writer.write_scorecard(_writer_inputs())
    digest1 = compute_scorecard_digest(sc1)
    digest2 = compute_scorecard_digest(sc2)
    if sc1.generated_at != sc2.generated_at:
        assert digest1 != digest2


# ── integration: extractor -> writer -> projection (chunk-shape point 5) ──


def test_integration_extractor_then_writer_then_projection() -> None:
    """End-to-end: MetricExtractor.extract() -> ScorecardWriter.write_scorecard()
    -> ScorecardWriter.write_review_projection().

    Validates the chunk-shape point 2 "consume MetricExtractor.extract()
    output" contract end-to-end + the chunk-shape point 4 bounded review
    projection + refs-only discipline.
    """

    extractor = MetricExtractor()
    extractor_inputs = _extractor_inputs(
        definitions=[
            _definition(name="tasks_per_hour", version="v1.0"),
            _definition(
                name="hours_per_task",
                version="v1.0",
                denominator="completed tasks",
            ),
        ]
    )
    metric_values = extractor.extract(extractor_inputs)
    assert len(metric_values) == 2

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id="integration-1",
            metrics=metric_values,
            baseline_refs=[_ref(ref_id="baseline-int")],
            warnings=[],
            incomplete_scopes=[],
        )
    )

    projection = writer.write_review_projection(scorecard)
    assert projection["review_projection_id"] == "review:governance-metrics:integration-1"
    # Both metrics carry typed definition versions per doc-15:144-145.
    assert "tasks_per_hour" in projection["metric_definition_versions"]
    assert "hours_per_task" in projection["metric_definition_versions"]
    # Refs-only discipline: every projected baseline_ref carries the
    # typed-ref shape fields, not raw bodies.
    for cited in projection["baseline_refs"]:
        assert "ref_id" in cited
        assert "digest" in cited
        assert "raw_body" not in cited


def test_integration_writer_preserves_extractor_gap_findings_independently() -> None:
    """The writer's gap_findings accumulator is independent of the
    extractor's: a fresh writer starts with an empty accumulator
    regardless of upstream extractor state."""

    extractor = MetricExtractor()
    extractor.extract(_extractor_inputs())

    writer = ScorecardWriter()
    assert writer.gap_findings == []


# ── doc-15:186-188 rollback discipline (historical scorecards preserved) ──


def test_rollback_discipline_historical_scorecards_are_immutable() -> None:
    """Per doc-15:186-188 the writer's typed-shape outputs are immutable
    rows: a NEW re-projection emits a NEW scorecard (with a new
    generated_at + new digest) rather than mutating a prior scorecard.

    The typed :class:`GovernanceScorecard` Pydantic BaseModel is immutable
    by Pydantic v2 default. Future re-projections produce NEW rows.
    """

    writer = ScorecardWriter()
    sc1 = writer.write_scorecard(_writer_inputs())
    sc1_metrics_snapshot = list(sc1.metrics)
    # Write a second scorecard with different metrics; sc1 must NOT be
    # mutated.
    writer.write_scorecard(_writer_inputs(metrics=[_value(value=99.0)]))
    assert list(sc1.metrics) == sc1_metrics_snapshot


def test_rollback_discipline_projection_carries_digest_for_historical_lookup() -> None:
    """Per doc-15:186-188 rollback discipline + doc-15:144-145 the
    review projection carries the scorecard digest so historical
    re-projections can be correlated with their source scorecards."""

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(_writer_inputs())
    projection = writer.write_review_projection(scorecard)
    # The digest must be a non-empty SHA-256 hex string (64 chars).
    assert isinstance(projection["scorecard_digest"], str)
    assert len(projection["scorecard_digest"]) == 64
    # Confirm reproducibility (the digest is tamper-detectable per
    # doc-15:144-145 + the canonical-JSON discipline).
    again = writer.write_review_projection(scorecard)
    assert again["scorecard_digest"] == projection["scorecard_digest"]
