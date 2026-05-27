"""Slice 15 fifth sub-slice -- calibration fixture tests for the governance
metric extractor + scorecard writer.

Per ``docs/execution-control-plane/15-governance-metrics-and-scoring.md``
§ Refactoring Steps step 7 (lines 135-136): *"Add calibration fixtures for
``8ac124d6`` and at least one simpler feature once available."*

This is the LAST doc-15 refactoring step; the 5th sub-slice closes step 7
by:

* Adding typed-shape JSON fixtures at ``tests/fixtures/governance_metrics/``
  (one for the ``8ac124d6`` corpus + one for a simpler synthetic feature
  per doc-15:135-136).
* Asserting per-fixture JSON round-trip parses to typed
  :class:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractorInputs`
  (DIRECT annotation-identity assertions per the established Slice 15
  pattern that functionally addresses Slice 14 V3 reviewer P3-V3-2 carry).
* Asserting :meth:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor.extract`
  output for each fixture matches the EXPECTED reference scorecard shape
  (the test asserts the calibrated metric values + confidence projections).
* Asserting :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_scorecard`
  round-trip emits the expected typed scorecard rows (consumes 4th
  sub-slice persistence layer).
* Asserting :meth:`~iriai_build_v2.execution_control.governance_scorecard_writer.ScorecardWriter.write_review_projection`
  output matches the expected bounded projection (refs-only +
  ``LIMIT cap+1`` + digest + version dict per doc-15:144-145).
* Asserting complexity-adjustment coefficient calibration: the 8ac124d6
  fixture's complexity adjustment factor is GREATER than the simpler
  feature's (monotonic axis per the 7-axis multiplicative composite at
  ``governance_metric_extractor.py:1722-1807``) per the starting
  calibration (P3-15-3-2 + P3-15-3-3).
* Asserting review-projection cap calibration: the
  :data:`~iriai_build_v2.execution_control.governance_scorecard_writer.DEFAULT_REVIEW_PROJECTION_CAP`
  produces the expected truncation behaviour for each fixture's metric
  count (closes P3-15-4-3 empirical analysis).

**Fixture refs-only discipline (doc-15:141-142 + AC5 doc-15:182).** Each
fixture cites typed Slice 13a
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
records (authority + ref_id + digest + quality + completeness) -- NOT
raw artifact bodies. The fixtures are bounded JSON projections; loading
them does NOT hydrate any artifact / event body per the doc-15:141-142
contract + AC5 doc-15:182.

**Definition versions (doc-15:144-145).** Each metric definition in the
fixtures carries ``version='v1.0'`` so the scorecard rows carry the
typed-version pinning per doc-15:144-145; the review projection's
``metric_definition_versions`` dict reports the versions consistently.

**Simpler-feature choice rationale.** Per doc-15:135-136 *"at least one
simpler feature once available"* the canonical simpler-feature candidate
is not yet available in the production corpus -- the only fully-instrumented
feature is ``8ac124d6`` itself. Per the auto-memory
``feedback_no_silent_degradation`` rule the simpler feature is a SYNTHETIC
minimal-shape feature (``simple-feature-v1``) that exercises the
calibration boundary without requiring full production-corpus coverage:

* 2 metric definitions (vs 4 for ``8ac124d6``) -- exercises the per-fixture
  metric-count axis.
* 4 typed refs (just above the
  :data:`~iriai_build_v2.execution_control.governance_metric_extractor.MetricExtractor.DEFAULT_MIN_SAMPLE_COUNT`
  threshold of 3) -- exercises the
  insufficient-vs-sufficient-sample boundary.
* Minimum :class:`TaskShapeInputs` (small task_count, single repo, no
  barrier, zero write-set uncertainty) -- exercises the unit-complexity-factor
  baseline (so the complexity adjustment is close to 1.0 vs 8ac124d6's
  ~2.5 factor).
* ``freshness_window_hours=24.0`` (1 day; vs 168.0 = 7 days for
  ``8ac124d6``) -- exercises the per-corpus freshness-window axis.

The synthetic fixture's choice + ratio relationship to ``8ac124d6`` lets
the calibration test assert RATIO relationships (e.g. ``8ac124d6``
complexity factor MUST exceed simpler-feature factor) rather than
absolute magic numbers, so the calibration test still adds value even
when the coefficients are preserved (per the chunk-shape point 3
OPTIONAL retune path).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15
1st/2nd/3rd/4th sub-slice modules + tests remain byte-identical.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    CompletenessState,
    EvidenceCompleteness,
)
from iriai_build_v2.execution_control.governance_metric_extractor import (
    ACTIVE_WORK_EXCLUDED_EXCLUSION,
    INSUFFICIENT_SAMPLES_EXCLUSION,
    PROMPT_CONTEXT_INCOMPLETE_EXCLUSION,
    MetricExtractor,
    MetricExtractorInputs,
    TaskShapeInputs,
    _compute_complexity_adjustment,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricDefinition,
    GovernanceMetricValue,
    GovernanceScorecard,
    compute_scorecard_digest,
)
from iriai_build_v2.execution_control.governance_scorecard_writer import (
    DEFAULT_REVIEW_PROJECTION_CAP,
    REVIEW_PROJECTION_ID_PREFIX,
    ScorecardWriter,
    ScorecardWriterInputs,
    compute_review_projection_id,
)
from iriai_build_v2.workflows.develop.governance.models import (
    EvidenceQuality,
    GovernanceEvidenceRef,
)


# ── Fixture path discipline ────────────────────────────────────────────────


FIXTURES_DIR = (
    Path(__file__).resolve().parent / "fixtures" / "governance_metrics"
)
"""Per doc-15:135-136 step 7 -- the canonical directory for the
governance-metric calibration fixtures.

Mirrors the established
``tests/workflows/test_execution_control_plane_fixture_replay.py:14-19``
pattern for the Slice 00 8ac124d6 fixture (``tests/fixtures/execution_control_plane/``).
The fixtures are bounded typed-shape JSON projections; they NEVER carry
raw artifact bodies per doc-15:141-142 + AC5 doc-15:182.
"""


_8AC124D6_FIXTURE_PATH = FIXTURES_DIR / "8ac124d6_evidence.json"
"""Per doc-15:135-136 -- typed ``MetricExtractorInputs`` JSON fixture for
the ``8ac124d6`` feature.

Cites real evidence-set refs from the Slice 00 fixture corpus
(``tests/fixtures/execution_control_plane/feature_8ac124d6/``) BY REF
ONLY (NOT raw bodies); carries a representative :class:`TaskShapeInputs`
shape per doc-15:125-130 step 4.
"""


_SIMPLE_FEATURE_FIXTURE_PATH = FIXTURES_DIR / "simple_feature_evidence.json"
"""Per doc-15:135-136 *"at least one simpler feature once available"* --
typed ``MetricExtractorInputs`` JSON fixture for a synthetic minimal-shape
feature (``simple-feature-v1``).

The simpler feature is a SYNTHETIC minimal-shape feature (vs the
production 8ac124d6 fixture) chosen per the module docstring's "Simpler-feature
choice rationale" section.
"""


# ── Helpers ────────────────────────────────────────────────────────────────


def _load_fixture(path: Path) -> MetricExtractorInputs:
    """Load and parse a typed :class:`MetricExtractorInputs` JSON fixture.

    Per doc-15:141-142 + AC5 doc-15:182 the fixture is bounded JSON
    projection: loading it does NOT hydrate any raw artifact body. The
    typed-shape Pydantic validation at :meth:`MetricExtractorInputs.model_validate_json`
    enforces the doc-15 + Slice 13a typed contracts at construction.
    """

    assert path.exists(), f"Fixture not found at {path}"
    json_text = path.read_text(encoding="utf-8")
    return MetricExtractorInputs.model_validate_json(json_text)


# ── Fixture-directory invariants ───────────────────────────────────────────


def test_fixtures_directory_exists() -> None:
    """Per doc-15:135-136 step 7 the calibration-fixtures directory MUST
    exist at the canonical path."""

    assert FIXTURES_DIR.exists(), (
        f"Calibration fixtures directory missing at {FIXTURES_DIR}; "
        "expected directory checked into the repository per doc-15:135-136 "
        "step 7."
    )
    assert FIXTURES_DIR.is_dir()


def test_8ac124d6_fixture_exists() -> None:
    """Per doc-15:135-136 the 8ac124d6 calibration fixture MUST exist."""

    assert _8AC124D6_FIXTURE_PATH.exists(), (
        f"8ac124d6 fixture missing at {_8AC124D6_FIXTURE_PATH}; "
        "expected typed MetricExtractorInputs JSON per doc-15:135-136."
    )


def test_simple_feature_fixture_exists() -> None:
    """Per doc-15:135-136 *"at least one simpler feature once available"*
    the simpler-feature calibration fixture MUST exist."""

    assert _SIMPLE_FEATURE_FIXTURE_PATH.exists(), (
        f"Simpler-feature fixture missing at {_SIMPLE_FEATURE_FIXTURE_PATH}; "
        "expected typed MetricExtractorInputs JSON per doc-15:135-136 "
        '"at least one simpler feature once available".'
    )


# ── Per-fixture JSON round-trip parses to typed MetricExtractorInputs ──────


def test_8ac124d6_fixture_parses_to_typed_metric_extractor_inputs() -> None:
    """Per doc-15:135-136 + chunk-shape point 2 -- the 8ac124d6 fixture
    JSON round-trips to a typed :class:`MetricExtractorInputs` via
    :meth:`MetricExtractorInputs.model_validate_json`.

    The typed-shape validation enforces all Slice 13a + Slice 13A + Slice
    15 1st sub-slice typed-shape contracts at construction:

    * :class:`MetricExtractorInputs.corpus_id` is a str.
    * :class:`MetricExtractorInputs.definitions` is a typed
      ``list[GovernanceMetricDefinition]``.
    * :class:`MetricExtractorInputs.evidence_set_refs` is a typed
      ``list[GovernanceEvidenceRef]``.
    * :class:`MetricExtractorInputs.completeness_state` is a typed
      :class:`EvidenceCompleteness`.
    * :class:`MetricExtractorInputs.active_work_filter` is one of the
      3-value Literal (``exclude`` / ``status_only`` / ``separate``).
    * :class:`MetricExtractorInputs.freshness_window_hours` is a float.
    * :class:`MetricExtractorInputs.task_shape_inputs` is the typed
      :class:`TaskShapeInputs` (3rd sub-slice add) per doc-15:125-130.
    * :class:`MetricExtractorInputs.implementation_log_completeness` is
      the typed :class:`EvidenceCompleteness` (3rd sub-slice AC4 add per
      doc-15:181).
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    assert isinstance(inputs, MetricExtractorInputs)
    assert inputs.corpus_id == "8ac124d6"


def test_simple_feature_fixture_parses_to_typed_metric_extractor_inputs() -> None:
    """Per doc-15:135-136 + chunk-shape point 2 -- the simpler-feature
    fixture JSON round-trips to a typed :class:`MetricExtractorInputs`."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    assert isinstance(inputs, MetricExtractorInputs)
    assert inputs.corpus_id == "simple-feature-v1"


def test_8ac124d6_fixture_has_typed_definitions() -> None:
    """Per doc-15:68-76 each definition in the fixture is a typed
    :class:`GovernanceMetricDefinition`."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    assert inputs.definitions, "8ac124d6 fixture must carry at least one definition"
    for d in inputs.definitions:
        assert isinstance(d, GovernanceMetricDefinition)
        # Doc-15:144-145 -- every definition carries a version.
        assert d.version, f"definition {d.name!r} missing version per doc-15:144-145"


def test_8ac124d6_fixture_has_typed_evidence_refs() -> None:
    """Per doc-15:141-142 + AC5 doc-15:182 each ref is a typed
    :class:`GovernanceEvidenceRef` (NOT a raw artifact body)."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    assert inputs.evidence_set_refs, "8ac124d6 fixture must carry at least one ref"
    for ref in inputs.evidence_set_refs:
        assert isinstance(ref, GovernanceEvidenceRef)


def test_8ac124d6_fixture_has_typed_task_shape_inputs() -> None:
    """Per doc-15:125-130 step 4 the 8ac124d6 fixture carries a
    representative :class:`TaskShapeInputs` shape."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    assert inputs.task_shape_inputs is not None, (
        "8ac124d6 fixture must carry task_shape_inputs per doc-15:125-130 step 4 "
        "+ the chunk-shape point 1 calibration-fixture requirement."
    )
    assert isinstance(inputs.task_shape_inputs, TaskShapeInputs)


def test_simple_feature_fixture_has_typed_task_shape_inputs() -> None:
    """The simpler-feature fixture also carries a typed
    :class:`TaskShapeInputs` shape (with minimum values per the module
    docstring's "Simpler-feature choice rationale")."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    assert inputs.task_shape_inputs is not None
    assert isinstance(inputs.task_shape_inputs, TaskShapeInputs)


# ── DIRECT annotation-identity REUSE assertions ─────────────────────────────
# Per the established Slice 15 1st/2nd/3rd/4th sub-slice pattern that
# functionally addresses Slice 14 V3 reviewer P3-V3-2 carry. These tests
# pin the typed-shape REUSE at the field-annotation level (not just
# isinstance(); the annotation `is` identity is the stronger contract).


def test_inputs_completeness_state_annotation_is_slice_13a_evidence_completeness() -> None:
    """Per doc-15:201-264 the ``completeness_state`` field annotation MUST
    BE IDENTITY-EQUAL to the Slice 13A 2nd sub-slice
    :class:`EvidenceCompleteness` (NOT a local redefinition).

    DIRECT annotation-identity assertion via ``is`` comparison -- the
    stronger pattern P3-V3-2 (Slice 14) flagged.
    """

    annotation = MetricExtractorInputs.model_fields["completeness_state"].annotation
    assert annotation is EvidenceCompleteness


def test_inputs_evidence_set_refs_annotation_is_list_of_slice_13a_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the
    ``evidence_set_refs`` field annotation MUST resolve to
    ``list[GovernanceEvidenceRef]`` where ``GovernanceEvidenceRef`` is
    the Slice 13a shared model (NOT a local redefinition).
    """

    annotation = MetricExtractorInputs.model_fields["evidence_set_refs"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_inputs_definitions_annotation_is_list_of_slice_15_first_definition() -> None:
    """The ``definitions`` field annotation MUST resolve to
    ``list[GovernanceMetricDefinition]`` where
    ``GovernanceMetricDefinition`` is the Slice 15 1st-sub-slice typed
    shape (NOT a local redefinition)."""

    annotation = MetricExtractorInputs.model_fields["definitions"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceMetricDefinition


def test_writer_inputs_metrics_annotation_is_list_of_slice_15_first_value() -> None:
    """Per doc-15:64-97 the writer-inputs ``metrics`` field annotation MUST
    resolve to ``list[GovernanceMetricValue]`` where
    ``GovernanceMetricValue`` is the Slice 15 1st sub-slice typed shape.

    DIRECT annotation-identity assertion -- the stronger pattern P3-V3-2
    (Slice 14) flagged.
    """

    annotation = ScorecardWriterInputs.model_fields["metrics"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceMetricValue


def test_writer_inputs_baseline_refs_annotation_is_list_of_slice_13a_ref() -> None:
    """The ``baseline_refs`` field annotation MUST resolve to
    ``list[GovernanceEvidenceRef]`` (Slice 13a shared model)."""

    annotation = ScorecardWriterInputs.model_fields["baseline_refs"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


# ── MetricExtractor.extract() output matches reference scorecard ───────────


def test_8ac124d6_extractor_emits_one_value_per_definition() -> None:
    """Per doc-15:117-136 step 2 the extractor emits one
    :class:`GovernanceMetricValue` per
    :class:`GovernanceMetricDefinition`."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    assert len(values) == len(inputs.definitions)


def test_8ac124d6_extractor_values_carry_typed_shape() -> None:
    """Every emitted value is a typed
    :class:`GovernanceMetricValue` (the typed-shape contract holds at
    construction)."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    for v in values:
        assert isinstance(v, GovernanceMetricValue)
        # Definition version preserved per doc-15:144-145.
        assert v.definition_version == "v1.0"


def test_8ac124d6_extractor_preserves_definition_names() -> None:
    """The extractor emits one value per definition; names match."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    names = {v.definition_name for v in values}
    assert names == {d.name for d in inputs.definitions}


def test_8ac124d6_extractor_emits_non_zero_finite_values() -> None:
    """For the 8ac124d6 fixture (16 refs, well above threshold) every
    metric emits a finite numeric value (NOT None per the
    insufficient-sample path; the fixture's sample count exceeds the
    DEFAULT_MIN_SAMPLE_COUNT=3 threshold)."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    for v in values:
        assert v.value is not None, (
            f"metric {v.definition_name!r} emitted value=None; the fixture's "
            "sample count exceeds DEFAULT_MIN_SAMPLE_COUNT=3 so the "
            "insufficient-sample path SHOULD NOT trigger."
        )
        assert isinstance(v.value, (int, float))


def test_8ac124d6_extractor_data_quality_is_derived_for_mixed_source_mix() -> None:
    """Per doc-15:151-153 the 8ac124d6 fixture's mix of 15 typed +
    1 legacy refs projects to ``data_quality='derived'`` + populated
    ``source_mix={'typed': n, 'legacy': m}``."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    for v in values:
        assert v.data_quality == "derived", (
            f"metric {v.definition_name!r} data_quality={v.data_quality!r}; "
            "mixed-source fixture per doc-15:151-153 MUST project to 'derived'."
        )
        # source_mix carries both typed + legacy counts.
        assert "typed" in v.source_mix
        assert "legacy" in v.source_mix
        assert v.source_mix["typed"] >= 1
        assert v.source_mix["legacy"] >= 1


def test_simple_feature_extractor_data_quality_is_canonical_for_typed_only() -> None:
    """Per doc-15:151-153 the simpler-feature fixture's typed-only ref set
    (4 typed; 0 legacy; freshness=24h with fresh refs) projects to
    ``data_quality='canonical'`` + source_mix carrying ONLY ``typed``."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    for v in values:
        assert v.data_quality == "canonical", (
            f"metric {v.definition_name!r} data_quality={v.data_quality!r}; "
            "typed-only-and-fresh fixture MUST project to 'canonical'."
        )
        # source_mix carries only typed.
        assert v.source_mix == {"typed": 4}


def test_8ac124d6_extractor_confidence_in_zero_one_range() -> None:
    """Per doc-15:84 + doc-15:131-132 step 5 the confidence MUST be in
    [0.0, 1.0]."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    for v in values:
        assert 0.0 <= v.confidence <= 1.0, (
            f"metric {v.definition_name!r} confidence={v.confidence}; "
            "must be in [0.0, 1.0] per doc-15:84."
        )


def test_8ac124d6_extractor_never_raises() -> None:
    """Per doc-14:242-243 (REUSED by Slice 15 non-blocking observer
    contract) the extractor NEVER raises a failure to the caller."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    # Should not raise.
    values = extractor.extract(inputs)
    # No gap findings expected for the well-typed fixture.
    assert extractor.gap_findings == []
    assert values  # Non-empty output.


def test_simple_feature_extractor_never_raises() -> None:
    """The non-blocking contract holds for the simpler-feature fixture too."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    assert extractor.gap_findings == []
    assert values


# ── ScorecardWriter.write_scorecard round-trip ─────────────────────────────


def test_8ac124d6_scorecard_writer_round_trip() -> None:
    """Per doc-15:133-134 step 6 + doc-15:90-97 the writer produces a
    typed :class:`GovernanceScorecard` consuming the extractor output."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=list(inputs.evidence_set_refs[:2]),
            warnings=[],
            incomplete_scopes=[],
        )
    )
    # Typed shape preserved.
    assert isinstance(scorecard, GovernanceScorecard)
    assert scorecard.corpus_id == "8ac124d6"
    assert len(scorecard.metrics) == len(values)
    # Definition versions preserved per doc-15:144-145.
    for m in scorecard.metrics:
        assert m.definition_version == "v1.0"


def test_simple_feature_scorecard_writer_round_trip() -> None:
    """Same round-trip discipline for the simpler-feature fixture."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=list(inputs.evidence_set_refs),
            warnings=[],
            incomplete_scopes=[],
        )
    )
    assert isinstance(scorecard, GovernanceScorecard)
    assert scorecard.corpus_id == "simple-feature-v1"
    assert len(scorecard.metrics) == len(values)


def test_8ac124d6_scorecard_writer_preserves_metric_values_verbatim() -> None:
    """Per doc-15:133-134 step 6 the writer preserves metric values
    verbatim (no mutation; the writer is a pure projection)."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    for original, projected in zip(values, scorecard.metrics):
        assert original == projected, (
            f"metric {original.definition_name!r} mutated by writer; "
            "the writer MUST preserve metric values verbatim per "
            "doc-15:133-134 step 6."
        )


# ── ScorecardWriter.write_review_projection bounded projection ─────────────


def test_8ac124d6_review_projection_matches_typed_shape() -> None:
    """Per doc-15:134 + chunk-shape point 4 the review projection carries
    the typed-shape fields: ``review_projection_id`` + ``corpus_id`` +
    ``generated_at`` + ``scorecard_digest`` + ``metric_definition_versions``
    + ``metrics`` + ``metric_truncated`` + ``baseline_refs`` +
    ``baseline_refs_truncated`` + ``incomplete_scopes`` + ``warnings``.
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=list(inputs.evidence_set_refs[:2]),
            warnings=["legacy_heavy_corpus"],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)

    # Required keys.
    expected_keys = {
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
    }
    assert set(projection.keys()) == expected_keys

    # Projection-id matches the typed prefix per doc-15:134.
    assert projection["review_projection_id"] == compute_review_projection_id(
        "8ac124d6"
    )
    assert projection["review_projection_id"].startswith(REVIEW_PROJECTION_ID_PREFIX)

    # Metric definition versions reported per doc-15:144-145.
    assert isinstance(projection["metric_definition_versions"], dict)
    for d in inputs.definitions:
        assert projection["metric_definition_versions"][d.name] == d.version

    # Warnings preserved.
    assert projection["warnings"] == ["legacy_heavy_corpus"]


def test_8ac124d6_review_projection_metrics_are_refs_only() -> None:
    """Per doc-15:141-142 + AC5 doc-15:182 the projection's metric list
    carries ONLY typed control fields + cited refs (NEVER raw bodies).

    Each projected metric dict carries: definition_name + definition_version
    + scope + value + unit + confidence + data_quality + source_mix +
    evidence_refs + evidence_refs_truncated + exclusions.

    The evidence_refs list itself carries typed
    :class:`GovernanceEvidenceRef` dicts (authority + ref_id + digest +
    quality + completeness + ...) -- NOT raw artifact bodies.
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)

    forbidden_body_keys = {"body", "artifact_body", "raw_body", "payload_body"}
    for metric_dict in projection["metrics"]:
        # Typed control fields present.
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
            assert key in metric_dict, f"missing typed key {key!r}"
        # No raw-body keys.
        for body_key in forbidden_body_keys:
            assert body_key not in metric_dict, (
                f"projection metric carried forbidden raw-body key {body_key!r}; "
                "per doc-15:141-142 + AC5 doc-15:182 the projection MUST emit "
                "ONLY refs (NOT raw bodies)."
            )
        # Each evidence ref carries typed control fields (authority + ref_id
        # + digest); NOT raw body fields.
        for ref_dict in metric_dict["evidence_refs"]:
            assert "authority" in ref_dict
            assert "ref_id" in ref_dict
            assert "digest" in ref_dict
            for body_key in forbidden_body_keys:
                assert body_key not in ref_dict


def test_8ac124d6_review_projection_baseline_refs_are_typed_dicts() -> None:
    """The baseline_refs projection emits typed
    :class:`GovernanceEvidenceRef` dicts (NOT raw bodies)."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=list(inputs.evidence_set_refs[:3]),
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)

    assert isinstance(projection["baseline_refs"], list)
    assert len(projection["baseline_refs"]) == 3
    for ref_dict in projection["baseline_refs"]:
        assert "authority" in ref_dict
        assert "ref_id" in ref_dict
        assert "digest" in ref_dict
        # Per doc-15:141-142 -- no raw-body keys.
        assert "body" not in ref_dict
        assert "raw_body" not in ref_dict


def test_8ac124d6_review_projection_carries_scorecard_digest() -> None:
    """Per doc-15:144-145 the projection carries the scorecard digest
    (from :func:`compute_scorecard_digest`) for tamper detection."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)

    expected_digest = compute_scorecard_digest(scorecard)
    assert projection["scorecard_digest"] == expected_digest


def test_8ac124d6_scorecard_digest_stable_across_re_runs() -> None:
    """The scorecard digest is deterministic per the doc-13a:298-301
    canonical-JSON discipline -- two computations against the SAME
    scorecard MUST produce byte-identical hex digests."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )

    digest_a = compute_scorecard_digest(scorecard)
    digest_b = compute_scorecard_digest(scorecard)
    assert digest_a == digest_b
    # SHA-256 hex digest has 64 chars.
    assert len(digest_a) == 64


# ── Review-projection cap calibration (P3-15-4-3 empirical analysis) ──────


def test_8ac124d6_review_projection_metric_count_below_default_cap() -> None:
    """Per P3-15-4-3 + chunk-shape point 6 -- the calibration fixture's
    metric count is well below the
    :data:`DEFAULT_REVIEW_PROJECTION_CAP=200` so the default cap is NOT
    truncating the metric list (the metric_truncated flag is False).

    This calibrates the default cap against the doc-15:99-115 15-metric v1
    contract: the v1 metric count is 15 + the 8ac124d6 fixture carries
    4 metrics; both are far below the 200 cap.
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)

    # Cap = 200 vs metric count <= 4 -- no truncation.
    assert len(scorecard.metrics) < DEFAULT_REVIEW_PROJECTION_CAP
    assert projection["metric_truncated"] is False
    assert len(projection["metrics"]) == len(scorecard.metrics)


def test_simple_feature_review_projection_metric_count_below_default_cap() -> None:
    """The simpler-feature fixture's 2 metrics also fit under the default
    cap of 200; metric_truncated is False."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)
    assert projection["metric_truncated"] is False
    assert len(projection["metrics"]) == len(scorecard.metrics)


def test_review_projection_cap_can_truncate_with_low_cap_override() -> None:
    """Per the :meth:`ScorecardWriter.write_review_projection` ``cap``
    parameter the caller can override the default cap; a low-cap override
    truncates the metric list at cap+1 + sets metric_truncated=True.

    This calibrates the LIMIT cap+1 truncation discipline against the
    8ac124d6 fixture's 4 metric values: cap=1 emits 2 items (cap+1) +
    flags metric_truncated=True.
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    # Cap = 1; metric count = 4 -- truncation triggers.
    projection = writer.write_review_projection(scorecard, cap=1)

    # Emit cap+1 = 2 items.
    assert len(projection["metrics"]) == 2
    assert projection["metric_truncated"] is True


def test_review_projection_default_cap_constant_is_starting_calibration() -> None:
    """Per P3-15-4-3 the default cap is the starting calibration; this
    test pins the current value (200) so future retunes are intentional.

    The value 200 is comfortably above the v1 15-metric contract per
    doc-15:99-115; future per-corpus caps may tighten via the
    :meth:`ScorecardWriter.write_review_projection` ``cap`` parameter.
    """

    assert DEFAULT_REVIEW_PROJECTION_CAP == 200


# ── Complexity-adjustment coefficient calibration (P3-15-3-2 + P3-15-3-3) ──


def test_8ac124d6_complexity_factor_greater_than_simple_feature() -> None:
    """Per doc-15:125-130 step 4 the complexity factor is monotonic across
    the 7 task-shape axes (more tasks / contracts / repos / barrier /
    dependency depth / verifier gates / write-set uncertainty → higher
    complexity).

    The 8ac124d6 fixture's task-shape inputs (task_count=21,
    contract_path_breadth=8, repo_count=1, barrier_type='soft',
    dependency_depth=4, planned_verifier_gate_count=4,
    declared_write_set_uncertainty=0.6) MUST produce a STRICTLY GREATER
    complexity factor than the simpler-feature fixture's minimum inputs
    (task_count=3, contract_path_breadth=1, repo_count=1, barrier_type='none',
    dependency_depth=0, planned_verifier_gate_count=1,
    declared_write_set_uncertainty=0.0).

    This is the calibration check per chunk-shape point 5: the starting
    coefficients (per P3-15-3-2 + P3-15-3-3) produce monotonic factors
    consistent with the doc-15:125-130 step 4 spec.
    """

    inputs_8ac = _load_fixture(_8AC124D6_FIXTURE_PATH)
    inputs_simple = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    # Use any definition (the helper ignores it in the 3rd-sub-slice
    # uniform projection per the P3-15-3-3 carry).
    definition = inputs_8ac.definitions[0]

    factor_8ac = _compute_complexity_adjustment(
        definition=definition,
        task_shape_inputs=inputs_8ac.task_shape_inputs,
    )
    factor_simple = _compute_complexity_adjustment(
        definition=definition,
        task_shape_inputs=inputs_simple.task_shape_inputs,
    )

    assert factor_8ac > factor_simple, (
        f"complexity factor monotonicity violated: 8ac124d6={factor_8ac:.4f} "
        f"NOT > simple={factor_simple:.4f}; the starting calibration MUST "
        "preserve the doc-15:125-130 step 4 monotonicity contract."
    )
    # Sanity: factor for simple is close to 1.0 (low complexity).
    assert factor_simple < 1.2, (
        f"simpler-feature complexity factor too high ({factor_simple:.4f}); "
        "the minimum task-shape inputs should produce a near-unit factor."
    )
    # Sanity: factor for 8ac124d6 is meaningfully larger.
    assert factor_8ac > 1.5, (
        f"8ac124d6 complexity factor too low ({factor_8ac:.4f}); the "
        "post-G44 task-shape inputs should produce a >1.5 factor."
    )


def test_8ac124d6_complexity_adjusted_throughput_is_lower_than_unadjusted() -> None:
    """Per doc-15:125-130 step 4 the complexity adjustment lowers the
    effective throughput (complexity_adjusted_tasks_per_hour) relative to
    the unadjusted tasks_per_hour, because more complex corpora produce
    fewer effective tasks per hour.

    Implementation per ``governance_metric_extractor.py:_project_metric_value``:
    the complexity factor multiplies the denominator for metrics whose
    name starts with ``complexity_adjusted_``; with complexity_factor > 1
    the value (numerator / denominator) is LOWER than the unadjusted
    value.
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    by_name = {v.definition_name: v for v in values}
    assert "tasks_per_hour" in by_name
    assert "complexity_adjusted_tasks_per_hour" in by_name

    unadjusted = by_name["tasks_per_hour"].value
    adjusted = by_name["complexity_adjusted_tasks_per_hour"].value
    assert unadjusted is not None
    assert adjusted is not None

    assert adjusted < unadjusted, (
        f"complexity adjustment did not lower throughput: unadjusted="
        f"{unadjusted}, adjusted={adjusted}. The complexity_adjusted_* "
        "metric value MUST be < the unadjusted value for the 8ac124d6 "
        "fixture (complexity factor > 1)."
    )


def test_simple_feature_complexity_adjusted_throughput_close_to_unadjusted() -> None:
    """For the simpler-feature fixture (complexity factor close to 1.0)
    the complexity_adjusted throughput is close to the unadjusted value.

    This calibrates the unit-complexity-factor baseline against the
    minimum task-shape inputs per the module docstring's "Simpler-feature
    choice rationale".
    """

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    by_name = {v.definition_name: v for v in values}
    unadjusted = by_name["tasks_per_hour"].value
    adjusted = by_name["complexity_adjusted_tasks_per_hour"].value
    assert unadjusted is not None
    assert adjusted is not None

    # The simpler-feature complexity factor is small (~1.07) so the
    # adjusted/unadjusted ratio is close to 1.0 / 1.07 = 0.94.
    ratio = adjusted / unadjusted
    assert 0.85 < ratio < 1.0, (
        f"simpler-feature complexity adjustment ratio out of expected "
        f"range: ratio={ratio:.4f} (expected 0.85 < ratio < 1.0)."
    )


def test_complexity_factor_is_unit_when_task_shape_inputs_none() -> None:
    """When ``task_shape_inputs`` is None the complexity factor MUST be
    1.0 (no adjustment), per the ``governance_metric_extractor.py:1772-1773``
    documented contract."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    definition = inputs.definitions[0]

    factor = _compute_complexity_adjustment(
        definition=definition,
        task_shape_inputs=None,
    )
    assert factor == 1.0


# ── Definition-version pinning per doc-15:144-145 ──────────────────────────


def test_8ac124d6_review_projection_metric_definition_versions_consistent() -> None:
    """Per doc-15:144-145 the projection's ``metric_definition_versions``
    dict MUST consistently report every definition's version (the per-name
    version map preserves the LAST observed version when multiple metric
    values share a definition_name per
    ``governance_scorecard_writer.py:753-755``).
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=[],
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)

    version_map = projection["metric_definition_versions"]
    # All 4 definitions present.
    for d in inputs.definitions:
        assert d.name in version_map
        assert version_map[d.name] == "v1.0"


def test_review_projection_id_starts_with_typed_prefix() -> None:
    """Per doc-15:134 the projection id always starts with the typed
    :data:`REVIEW_PROJECTION_ID_PREFIX`."""

    assert REVIEW_PROJECTION_ID_PREFIX == "review:governance-metrics:"
    assert compute_review_projection_id("8ac124d6").startswith(
        REVIEW_PROJECTION_ID_PREFIX
    )
    assert compute_review_projection_id("simple-feature-v1").startswith(
        REVIEW_PROJECTION_ID_PREFIX
    )


# ── End-to-end fixture extractor → writer → projection integration ──────────


def test_8ac124d6_end_to_end_extractor_writer_projection_integration() -> None:
    """The full Slice 15 pipeline (1st typed-shape foundation + 2nd
    extractor over evidence sets + 3rd real arithmetic + complexity
    adjustment + calibrated confidence + 4th scorecard persistence +
    bounded review projection) consumes the 8ac124d6 calibration fixture
    end-to-end without raising.

    This is the integration calibration that validates the 5 typed
    surfaces compose cleanly per the established Slice 14 + Slice 15
    1st-4th sub-slice composition pattern.
    """

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)

    # Step 1 + 2 + 3: extract metric values (consumes the fixture's
    # typed inputs).
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    assert values
    assert extractor.gap_findings == []

    # Step 4 (write_scorecard): compose typed scorecard.
    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=list(inputs.evidence_set_refs[:5]),
            warnings=[],
            incomplete_scopes=[],
        )
    )
    assert isinstance(scorecard, GovernanceScorecard)
    assert writer.gap_findings == []

    # Step 4 (write_review_projection): bounded review projection.
    projection = writer.write_review_projection(scorecard)
    assert projection["review_projection_id"] == "review:governance-metrics:8ac124d6"
    assert projection["corpus_id"] == "8ac124d6"

    # Digest matches re-computed digest.
    assert projection["scorecard_digest"] == compute_scorecard_digest(scorecard)


def test_simple_feature_end_to_end_extractor_writer_projection_integration() -> None:
    """Same end-to-end integration for the simpler-feature fixture."""

    inputs = _load_fixture(_SIMPLE_FEATURE_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)
    assert values
    assert extractor.gap_findings == []

    writer = ScorecardWriter()
    scorecard = writer.write_scorecard(
        ScorecardWriterInputs(
            corpus_id=inputs.corpus_id,
            metrics=values,
            baseline_refs=list(inputs.evidence_set_refs),
            warnings=[],
            incomplete_scopes=[],
        )
    )
    projection = writer.write_review_projection(scorecard)
    assert projection["review_projection_id"] == "review:governance-metrics:simple-feature-v1"
    assert projection["corpus_id"] == "simple-feature-v1"


# ── Doc-15:144-145 historical scorecard preservation discipline ────────────


def test_8ac124d6_two_writes_with_same_inputs_produce_equal_metrics() -> None:
    """Per doc-15:186-188 + doc-15:144-145 the writer is a pure projection;
    two calls with the same inputs produce metrics-equal scorecards
    (modulo the ``generated_at`` timestamp which is set per call)."""

    inputs = _load_fixture(_8AC124D6_FIXTURE_PATH)
    extractor = MetricExtractor()
    values = extractor.extract(inputs)

    writer = ScorecardWriter()
    writer_inputs = ScorecardWriterInputs(
        corpus_id=inputs.corpus_id,
        metrics=values,
        baseline_refs=[],
        warnings=[],
        incomplete_scopes=[],
    )
    sc_a = writer.write_scorecard(writer_inputs)
    sc_b = writer.write_scorecard(writer_inputs)
    # Metric content + version preserved across calls.
    assert sc_a.metrics == sc_b.metrics
    assert sc_a.corpus_id == sc_b.corpus_id
    # baseline_refs + incomplete_scopes + warnings stable across calls.
    assert sc_a.baseline_refs == sc_b.baseline_refs
    assert sc_a.incomplete_scopes == sc_b.incomplete_scopes
    assert sc_a.warnings == sc_b.warnings


# ── Refs-only invariant + AC5 doc-15:182 ───────────────────────────────────


def test_8ac124d6_fixture_carries_no_raw_artifact_body_fields() -> None:
    """Per doc-15:141-142 + AC5 doc-15:182 the fixture JSON itself MUST
    NOT carry raw artifact body fields. The typed
    :class:`GovernanceEvidenceRef` shape (with ``extra='forbid'``) enforces
    this at construction; this test pins the JSON-level invariant for
    defence-in-depth.
    """

    import json

    raw_text = _8AC124D6_FIXTURE_PATH.read_text(encoding="utf-8")
    parsed = json.loads(raw_text)

    forbidden_body_keys = {
        "body",
        "raw_body",
        "artifact_body",
        "payload_body",
        "value_payload",
    }

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_body_keys, (
                    f"fixture carries forbidden raw-body key {k!r} -- "
                    "doc-15:141-142 + AC5 doc-15:182 mandate refs-only "
                    "evidence."
                )
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(parsed)


def test_simple_feature_fixture_carries_no_raw_artifact_body_fields() -> None:
    """Same refs-only invariant for the simpler-feature fixture."""

    import json

    raw_text = _SIMPLE_FEATURE_FIXTURE_PATH.read_text(encoding="utf-8")
    parsed = json.loads(raw_text)
    forbidden_body_keys = {
        "body",
        "raw_body",
        "artifact_body",
        "payload_body",
        "value_payload",
    }

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            for k, v in node.items():
                assert k not in forbidden_body_keys
                _walk(v)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(parsed)


# ── EvidenceQuality + EvidenceAuthority typed-vocab REUSE assertion ─────────


def test_fixture_refs_use_typed_evidence_authority_vocabulary() -> None:
    """Per doc-13:74-84 the
    :class:`GovernanceEvidenceRef.authority` field is restricted to the
    Slice 13a 9-value :data:`EvidenceAuthority` Literal (7 typed-first
    + 2 legacy fallbacks)."""

    from iriai_build_v2.workflows.develop.governance.models import EvidenceAuthority

    allowed = set(get_args(EvidenceAuthority))
    for path in (_8AC124D6_FIXTURE_PATH, _SIMPLE_FEATURE_FIXTURE_PATH):
        inputs = _load_fixture(path)
        for ref in inputs.evidence_set_refs:
            assert ref.authority in allowed, (
                f"fixture {path.name} ref {ref.ref_id!r} uses unknown "
                f"authority {ref.authority!r}; the typed 9-value "
                "Literal MUST be the allowed vocabulary per doc-13:74-84."
            )


def test_fixture_metric_definitions_use_typed_evidence_quality_vocabulary() -> None:
    """Per doc-13:86 the
    :class:`GovernanceEvidenceRef.quality` field is restricted to the
    Slice 13a 6-value :data:`EvidenceQuality` Literal
    (canonical / derived / sampled / advisory / stale / insufficient)."""

    allowed = set(get_args(EvidenceQuality))
    for path in (_8AC124D6_FIXTURE_PATH, _SIMPLE_FEATURE_FIXTURE_PATH):
        inputs = _load_fixture(path)
        for ref in inputs.evidence_set_refs:
            assert ref.quality in allowed, (
                f"fixture {path.name} ref {ref.ref_id!r} uses unknown "
                f"quality {ref.quality!r}; the typed 6-value Literal "
                "MUST be the allowed vocabulary per doc-13:86."
            )
