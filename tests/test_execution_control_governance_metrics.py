"""Slice 15 first sub-slice -- unit tests for the foundational
``execution_control/governance_metrics.py`` typed-shape module.

Covers the 3 doc-15:64-97 typed shapes + the
:data:`MetricScopeKind` Literal + the :data:`REQUIRED_V1_METRIC_NAMES`
tuple + the helper digest functions:

- :data:`MetricScopeKind` -- 8 values per doc-15:66.
- :class:`GovernanceMetricDefinition` -- 9 fields per doc-15:68-76;
  ``active_work_policy`` 3-value Literal validation.
- :class:`GovernanceMetricValue` -- 10 fields per doc-15:78-88; Slice 13a
  shared ``EvidenceQuality`` consumption (NOT redefined; DIRECT
  annotation-identity assertion); Slice 13a shared ``GovernanceEvidenceRef``
  consumption (NOT redefined; DIRECT annotation-identity assertion).
- :class:`GovernanceScorecard` -- 6 fields per doc-15:90-97; Slice 13a
  shared ``GovernanceEvidenceRef`` consumption on ``baseline_refs``.
- :data:`REQUIRED_V1_METRIC_NAMES` -- 15-name tuple per doc-15:99-115.
- :func:`compute_scorecard_digest` + :func:`canonical_scorecard_dict` --
  canonical-JSON + SHA-256 helpers mirroring Slice 13A
  ``compute_completeness_digest`` + Slice 14 ``compute_payload_sha256``.

Every model enforces ``extra="forbid"`` (typo-d kwargs ->
``ValidationError``). Every Literal range is enforced (per Pydantic
``Literal`` validator).

**Slice 14 P3-V3-2 addressed (DIRECT annotation-identity assertion).**
Per the implementer prompt § "Non-negotiables" the Slice 13a shared
model identity is enforced via a DIRECT
``model_fields["data_quality"].annotation is EvidenceQuality`` assertion
rather than the indirect value-set + namespace assertions used in
Slice 14 1st sub-slice tests at
``tests/test_execution_control_commit_provenance.py:718``. This is the
stronger pattern V3 reviewer flagged in the Slice 14 close-out
(P3-V3-2 CARRY); future Slice 15 sub-slices can reuse the pattern when
consuming additional Slice 13A typed shapes.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 modules + tests
remain byte-identical.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.governance_metrics import (
    REQUIRED_V1_METRIC_NAMES,
    GovernanceMetricDefinition,
    GovernanceMetricValue,
    GovernanceScorecard,
    MetricScopeKind,
    canonical_scorecard_dict,
    compute_scorecard_digest,
)
from iriai_build_v2.workflows.develop.governance.models import (
    EvidenceQuality,
    GovernanceEvidenceRef,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 3 typed shapes + ``MetricScopeKind``
    Literal + ``REQUIRED_V1_METRIC_NAMES`` tuple + 2 digest helpers.

    Per doc-15:64-97 the 3 typed shapes are
    ``GovernanceMetricDefinition`` + ``GovernanceMetricValue`` +
    ``GovernanceScorecard``. Plus the ``MetricScopeKind`` 8-value
    Literal per doc-15:66 + the ``REQUIRED_V1_METRIC_NAMES`` 15-name
    tuple per doc-15:99-115 + the ``compute_scorecard_digest`` +
    ``canonical_scorecard_dict`` helpers. Total: 7 exported names.
    """

    from iriai_build_v2.execution_control import governance_metrics as mod

    expected = {
        "MetricScopeKind",
        "GovernanceMetricDefinition",
        "GovernanceMetricValue",
        "GovernanceScorecard",
        "REQUIRED_V1_METRIC_NAMES",
        "compute_scorecard_digest",
        "canonical_scorecard_dict",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 7
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_evidence_quality() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the Slice 15 module
    MUST NOT redefine :data:`EvidenceQuality` -- it consumes the Slice 13a
    shared Literal via import only.

    A re-definition would create a second authority shape and violate
    the dependency reconciliation contract.
    """

    from iriai_build_v2.execution_control import governance_metrics as mod

    # The module does NOT export its own EvidenceQuality.
    assert "EvidenceQuality" not in set(mod.__all__)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the Slice 15 module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes
    the Slice 13a shared model via import only.
    """

    from iriai_build_v2.execution_control import governance_metrics as mod

    assert "GovernanceEvidenceRef" not in set(mod.__all__)


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the Slice 15 module
    MUST NOT redefine :data:`CompletenessState` -- the future
    metric-extractor sub-slices will consume it via import from
    :mod:`iriai_build_v2.execution_control.completeness`.

    This sub-slice does not yet wire the extractor; the REUSE
    discipline is enforced here at the test-file level by asserting
    no local ``CompletenessState`` redefinition.
    """

    from iriai_build_v2.execution_control import governance_metrics as mod

    assert "CompletenessState" not in set(mod.__all__)
    assert not hasattr(mod, "CompletenessState")
    assert not hasattr(mod, "_CompletenessState")


def test_module_import_discipline_no_implementation_py() -> None:
    """Per the implementer prompt § "Non-negotiables" the typed-shape
    module MUST NOT import ``implementation.py`` (the typed-shape module
    is foundational; ``implementation.py`` would be a downstream
    consumer, not a dependency)."""

    import iriai_build_v2.execution_control.governance_metrics as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in text
    assert "from iriai_build_v2.workflows.develop.execution.implementation" not in text


def test_module_import_discipline_no_failure_router() -> None:
    """Per the implementer prompt § "MUST NOT DO" the typed-shape module
    MUST NOT import the failure_router (no new failure ids this
    sub-slice)."""

    import iriai_build_v2.execution_control.governance_metrics as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in text


def test_module_import_discipline_no_other_execution_control_modules() -> None:
    """Per the implementer prompt § "MUST NOT DO" the typed-shape module
    MUST NOT mutate or import other execution_control/ modules.

    Per doc-13a:285-287 step 9 + doc-15:201-264 future metric-extractor
    sub-slices will import from
    :mod:`iriai_build_v2.execution_control.completeness`; this
    sub-slice does not yet pre-empt that wiring.
    """

    import iriai_build_v2.execution_control.governance_metrics as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    # No imports from sibling execution_control modules (this is the
    # foundational typed-shape module).
    assert "from iriai_build_v2.execution_control.completeness" not in text
    assert "from iriai_build_v2.execution_control.commit_provenance" not in text
    assert "from iriai_build_v2.execution_control.merge_queue_store" not in text


def test_package_init_does_not_re_export_governance_metrics() -> None:
    """Mirrors Slice 13A 2nd sub-slice + Slice 14 1st sub-slice
    precedents: ``governance_metrics.py`` is consumed via fully-qualified
    imports, NOT re-exported through the ``execution_control`` package
    ``__all__``."""

    from iriai_build_v2 import execution_control as pkg

    if hasattr(pkg, "__all__"):
        assert "governance_metrics" not in set(pkg.__all__)
        assert "GovernanceMetricDefinition" not in set(pkg.__all__)
        assert "GovernanceMetricValue" not in set(pkg.__all__)
        assert "GovernanceScorecard" not in set(pkg.__all__)


# ── MetricScopeKind (doc-15:66) ────────────────────────────────────────────


def test_metric_scope_kind_is_8_value_literal() -> None:
    """Per doc-15:66 the ``MetricScopeKind`` Literal has 8 values."""

    members = set(get_args(MetricScopeKind))
    expected = {
        "feature",
        "effective_group",
        "task",
        "lane",
        "repo",
        "runtime",
        "verifier",
        "policy",
    }
    assert members == expected
    assert len(members) == 8


@pytest.mark.parametrize(
    "scope_kind",
    ["feature", "effective_group", "task", "lane", "repo", "runtime", "verifier", "policy"],
)
def test_metric_scope_kind_accepts_all_8_values(scope_kind: str) -> None:
    """Each of the 8 doc-15:66 values populates cleanly via
    :class:`GovernanceMetricDefinition`."""

    d = _definition(scope_kind=scope_kind)
    assert d.scope_kind == scope_kind


def test_metric_scope_kind_rejects_unknown_value() -> None:
    """Per doc-15:66 the ``MetricScopeKind`` Literal enforces the
    8-value set; unknown values fail closed at construction."""

    with pytest.raises(ValidationError):
        _definition(scope_kind="not_a_scope")


# ── REQUIRED_V1_METRIC_NAMES (doc-15:99-115) ───────────────────────────────


def test_required_v1_metric_names_is_15_value_tuple() -> None:
    """Per doc-15:99-115 the v1 contract requires exactly 15 metric names."""

    assert len(REQUIRED_V1_METRIC_NAMES) == 15
    assert isinstance(REQUIRED_V1_METRIC_NAMES, tuple)


def test_required_v1_metric_names_matches_doc_15_99_115() -> None:
    """The 15 required-v1 metric names match doc-15:101-115 verbatim."""

    expected = (
        "tasks_per_hour",
        "complexity_adjusted_tasks_per_hour",
        "hours_per_task",
        "repair_cycles_per_task",
        "verification_cost_per_task",
        "commit_failures_per_task",
        "stale_context_events_per_task",
        "workspace_unblocks_per_task",
        "runtime_failures_per_attempt",
        "merge_queue_wait_hours",
        "checkpoint_duration_hours",
        "workflow_drag_hours",
        "operator_required_escalations",
        "plan_deviation_count",
        "resolved_p1_p2_review_findings",
    )
    assert REQUIRED_V1_METRIC_NAMES == expected


def test_required_v1_metric_names_are_unique() -> None:
    """The 15 required-v1 metric names contain no duplicates."""

    assert len(set(REQUIRED_V1_METRIC_NAMES)) == 15


def test_required_v1_metric_names_all_strings() -> None:
    """All 15 required-v1 metric names are non-empty strings."""

    for name in REQUIRED_V1_METRIC_NAMES:
        assert isinstance(name, str)
        assert name
        assert name.strip() == name


def test_required_v1_metric_names_are_snake_case() -> None:
    """All 15 required-v1 metric names use snake_case (lowercase + underscores
    only)."""

    for name in REQUIRED_V1_METRIC_NAMES:
        assert name.replace("_", "").islower() or name.replace("_", "").isalnum()
        assert " " not in name
        assert "-" not in name


# ── GovernanceMetricDefinition (doc-15:68-76) ──────────────────────────────


def _definition(**overrides: object) -> GovernanceMetricDefinition:
    """Construct a fully-specified :class:`GovernanceMetricDefinition` for tests."""

    base: dict[str, object] = dict(
        name="tasks_per_hour",
        version="v1.0",
        scope_kind="feature",
        numerator="completed tasks",
        denominator="elapsed hours",
        required_evidence_kinds=["typed_journal", "implementation_journal"],
        active_work_policy="exclude",
        confidence_rule="evidence_completeness * sample_count_factor",
    )
    base.update(overrides)
    return GovernanceMetricDefinition(**base)


def test_definition_accepts_all_9_fields() -> None:
    """The 9 doc-15:68-76 fields all populate cleanly."""

    d = _definition()
    assert d.name == "tasks_per_hour"
    assert d.version == "v1.0"
    assert d.scope_kind == "feature"
    assert d.numerator == "completed tasks"
    assert d.denominator == "elapsed hours"
    assert d.required_evidence_kinds == ["typed_journal", "implementation_journal"]
    assert d.active_work_policy == "exclude"
    assert d.confidence_rule == "evidence_completeness * sample_count_factor"


def test_definition_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _definition(unknown_field="oops")  # type: ignore[arg-type]


def test_definition_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    d = _definition()
    serialised = d.model_dump_json()
    restored = GovernanceMetricDefinition.model_validate_json(serialised)
    assert restored == d


@pytest.mark.parametrize(
    "policy",
    ["exclude", "status_only", "separate"],
)
def test_definition_active_work_policy_accepts_all_3_values(policy: str) -> None:
    """Per doc-15:75 + doc-15:127-129 step 3 the 3-value Literal enforces
    the active-work policy taxonomy."""

    d = _definition(active_work_policy=policy)
    assert d.active_work_policy == policy


def test_definition_active_work_policy_rejects_unknown_value() -> None:
    """The 3-value Literal fails closed on unknown values."""

    with pytest.raises(ValidationError):
        _definition(active_work_policy="include")


def test_definition_required_evidence_kinds_accepts_empty_list() -> None:
    """The ``required_evidence_kinds`` list MAY be empty (e.g. when the
    metric is a pure derived computation with no required evidence
    kinds)."""

    d = _definition(required_evidence_kinds=[])
    assert d.required_evidence_kinds == []


# ── GovernanceMetricValue (doc-15:78-88) ───────────────────────────────────


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified Slice 13a :class:`GovernanceEvidenceRef`
    for tests.

    Per doc-13:97-111 the ref carries the required ``authority`` +
    ``ref_id`` + ``digest`` + ``quality`` + ``completeness`` fields plus
    optional context fields.
    """

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-1",
        digest="a" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _value(**overrides: object) -> GovernanceMetricValue:
    """Construct a fully-specified :class:`GovernanceMetricValue` for tests."""

    base: dict[str, object] = dict(
        definition_name="tasks_per_hour",
        definition_version="v1.0",
        scope={"feature_id": "8ac124d6"},
        value=4.2,
        unit="tasks/hour",
        confidence=0.9,
        data_quality="canonical",
        source_mix={"typed": 12, "legacy": 0},
        evidence_refs=[_evidence_ref()],
        exclusions=[],
    )
    base.update(overrides)
    return GovernanceMetricValue(**base)


def test_value_accepts_all_10_fields() -> None:
    """The 10 doc-15:78-88 fields all populate cleanly."""

    v = _value()
    assert v.definition_name == "tasks_per_hour"
    assert v.definition_version == "v1.0"
    assert v.scope == {"feature_id": "8ac124d6"}
    assert v.value == pytest.approx(4.2)
    assert v.unit == "tasks/hour"
    assert v.confidence == pytest.approx(0.9)
    assert v.data_quality == "canonical"
    assert v.source_mix == {"typed": 12, "legacy": 0}
    assert len(v.evidence_refs) == 1
    assert isinstance(v.evidence_refs[0], GovernanceEvidenceRef)
    assert v.exclusions == []


def test_value_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _value(unknown_field="oops")  # type: ignore[arg-type]


def test_value_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    v = _value()
    serialised = v.model_dump_json()
    restored = GovernanceMetricValue.model_validate_json(serialised)
    assert restored == v


def test_value_value_field_accepts_int() -> None:
    """Per doc-15:82 the ``value`` field accepts ``float | int | None``;
    int is one of the 3 accepted types."""

    v = _value(value=42)
    assert v.value == 42
    assert isinstance(v.value, int)


def test_value_value_field_accepts_float() -> None:
    """Per doc-15:82 the ``value`` field accepts ``float | int | None``;
    float is one of the 3 accepted types."""

    v = _value(value=3.14)
    assert v.value == pytest.approx(3.14)


def test_value_value_field_accepts_none() -> None:
    """Per doc-15:82 + doc-15:149-150 (insufficient-sample case) the
    ``value`` field accepts None."""

    v = _value(value=None)
    assert v.value is None


def test_value_source_mix_defaults_to_empty_dict() -> None:
    """Per doc-15:86 the ``source_mix`` field defaults to an empty dict
    (non-derived metrics may omit it)."""

    # Construct without source_mix
    v = GovernanceMetricValue(
        definition_name="tasks_per_hour",
        definition_version="v1.0",
        scope={"feature_id": "8ac124d6"},
        value=4.2,
        unit="tasks/hour",
        confidence=0.9,
        data_quality="canonical",
        evidence_refs=[_evidence_ref()],
        exclusions=[],
    )
    assert v.source_mix == {}


def test_value_evidence_refs_accepts_empty_list() -> None:
    """The ``evidence_refs`` list MAY be empty (e.g. for an
    insufficient-sample metric per doc-15:149-150)."""

    v = _value(evidence_refs=[])
    assert v.evidence_refs == []


def test_value_exclusions_accepts_populated_list() -> None:
    """The ``exclusions`` list carries exclusion-reason strings per
    doc-15:155-156 (overlapping failures + double-counting prevention)."""

    v = _value(
        exclusions=["active_work_excluded", "preview_only_evidence_excluded"]
    )
    assert v.exclusions == [
        "active_work_excluded",
        "preview_only_evidence_excluded",
    ]


@pytest.mark.parametrize(
    "quality",
    ["canonical", "derived", "sampled", "advisory", "stale", "insufficient"],
)
def test_value_data_quality_accepts_all_6_evidence_quality_values(quality: str) -> None:
    """Per doc-15:85 + Slice 13a doc-13:86 the ``data_quality`` field
    accepts all 6 :data:`EvidenceQuality` Literal values."""

    v = _value(data_quality=quality)
    assert v.data_quality == quality


def test_value_data_quality_rejects_unknown_value() -> None:
    """Per Slice 13a doc-13:86 the ``EvidenceQuality`` Literal enforces
    the 6-value set; unknown values fail closed."""

    with pytest.raises(ValidationError):
        _value(data_quality="not_a_quality")


# ── Slice 13a shared model identity (DIRECT annotation-identity) ───────────


def test_value_data_quality_annotation_is_slice_13a_evidence_quality() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-15:201-264).

    Per the implementer prompt § "Non-negotiables" this is the STRONGER
    pattern Slice 14 V3 reviewer flagged in P3-V3-2 CARRY at
    ``tests/test_execution_control_commit_provenance.py:718``: the
    :attr:`GovernanceMetricValue.data_quality` field MUST be typed
    against the Slice 13a shared
    :data:`~iriai_build_v2.workflows.develop.governance.models.EvidenceQuality`
    Literal via DIRECT annotation identity (``is`` comparison) rather
    than indirect value-set + namespace assertions.
    """

    annotation = GovernanceMetricValue.model_fields["data_quality"].annotation
    # DIRECT annotation-identity assertion (the stronger pattern).
    assert annotation is EvidenceQuality
    # And the 6 doc-13:86 values match exactly.
    assert set(get_args(annotation)) == {
        "canonical",
        "derived",
        "sampled",
        "advisory",
        "stale",
        "insufficient",
    }


def test_value_evidence_refs_annotation_is_list_of_slice_13a_governance_evidence_ref() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-15:201-264).

    The :attr:`GovernanceMetricValue.evidence_refs` field MUST be typed
    against ``list[GovernanceEvidenceRef]`` where
    :class:`GovernanceEvidenceRef` IS the Slice 13a shared model
    imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models` -- NOT
    redefined here. This is the stronger pattern V3 reviewer flagged in
    P3-V3-2 CARRY: assert the annotation resolves to
    ``list[GovernanceEvidenceRef]`` via DIRECT identity comparison on
    the list element type.
    """

    annotation = GovernanceMetricValue.model_fields["evidence_refs"].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 13a shared class via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_scorecard_baseline_refs_annotation_is_list_of_slice_13a_governance_evidence_ref() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-15:201-264).

    The :attr:`GovernanceScorecard.baseline_refs` field MUST be typed
    against ``list[GovernanceEvidenceRef]`` where
    :class:`GovernanceEvidenceRef` IS the Slice 13a shared model.
    """

    annotation = GovernanceScorecard.model_fields["baseline_refs"].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 13a shared class via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


# ── GovernanceScorecard (doc-15:90-97) ─────────────────────────────────────


def _scorecard(**overrides: object) -> GovernanceScorecard:
    """Construct a fully-specified :class:`GovernanceScorecard` for tests."""

    base: dict[str, object] = dict(
        corpus_id="8ac124d6",
        generated_at=datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc),
        metrics=[_value()],
        baseline_refs=[_evidence_ref()],
        incomplete_scopes=[],
        warnings=[],
    )
    base.update(overrides)
    return GovernanceScorecard(**base)


def test_scorecard_accepts_all_6_fields() -> None:
    """The 6 doc-15:90-97 fields all populate cleanly."""

    s = _scorecard()
    assert s.corpus_id == "8ac124d6"
    assert s.generated_at == datetime(2026, 5, 24, 12, 0, 0, tzinfo=timezone.utc)
    assert len(s.metrics) == 1
    assert isinstance(s.metrics[0], GovernanceMetricValue)
    assert len(s.baseline_refs) == 1
    assert isinstance(s.baseline_refs[0], GovernanceEvidenceRef)
    assert s.incomplete_scopes == []
    assert s.warnings == []


def test_scorecard_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _scorecard(unknown_field="oops")  # type: ignore[arg-type]


def test_scorecard_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    s = _scorecard()
    serialised = s.model_dump_json()
    restored = GovernanceScorecard.model_validate_json(serialised)
    assert restored == s


def test_scorecard_metrics_accepts_empty_list() -> None:
    """The ``metrics`` list MAY be empty (e.g. for a scorecard skeleton
    before metrics are computed)."""

    s = _scorecard(metrics=[])
    assert s.metrics == []


def test_scorecard_incomplete_scopes_accepts_populated_list() -> None:
    """Per doc-15:95 + doc-15:148-150 + doc-15:163-164 the
    ``incomplete_scopes`` list carries dict descriptors of scopes that
    lacked sufficient evidence."""

    s = _scorecard(
        incomplete_scopes=[
            {"scope_kind": "feature", "scope_id": "abc", "reason": "missing_typed_evidence"},
            {"scope_kind": "lane", "scope_id": "ml-1", "reason": "insufficient_samples"},
        ]
    )
    assert len(s.incomplete_scopes) == 2
    assert s.incomplete_scopes[0]["scope_kind"] == "feature"


def test_scorecard_warnings_accepts_populated_list() -> None:
    """Per doc-15:96 the ``warnings`` list carries free-form warning
    strings."""

    s = _scorecard(
        warnings=["legacy_heavy_corpus", "stale_baseline"]
    )
    assert s.warnings == ["legacy_heavy_corpus", "stale_baseline"]


# ── canonical_scorecard_dict + compute_scorecard_digest helpers ────────────


def test_canonical_scorecard_dict_is_json_serialisable() -> None:
    """The ``canonical_scorecard_dict`` projection produces a JSON-safe
    dict (datetime serialises to ISO-8601 string via ``mode='json'``)."""

    s = _scorecard()
    raw = canonical_scorecard_dict(s)
    import json as _json

    serialised = _json.dumps(raw, sort_keys=True, separators=(",", ":"))
    assert isinstance(serialised, str)
    assert "2026-05-24" in serialised


def test_compute_scorecard_digest_is_deterministic() -> None:
    """Two calls with byte-identical scorecards produce byte-identical
    digests (cross-process freshness contract)."""

    s = _scorecard()
    d1 = compute_scorecard_digest(s)
    d2 = compute_scorecard_digest(s)
    assert d1 == d2
    # SHA-256 hex is 64 chars.
    assert len(d1) == 64


def test_compute_scorecard_digest_differs_on_corpus_id_change() -> None:
    """A change in any field (e.g. ``corpus_id``) changes the digest."""

    s1 = _scorecard(corpus_id="8ac124d6")
    s2 = _scorecard(corpus_id="other-corpus")
    assert compute_scorecard_digest(s1) != compute_scorecard_digest(s2)


def test_compute_scorecard_digest_differs_on_metrics_change() -> None:
    """A change in the metrics list changes the digest."""

    s1 = _scorecard(metrics=[_value(value=4.2)])
    s2 = _scorecard(metrics=[_value(value=8.4)])
    assert compute_scorecard_digest(s1) != compute_scorecard_digest(s2)


def test_compute_scorecard_digest_roundtrip_via_json() -> None:
    """Digest computed before serialisation equals digest computed after
    ``model_dump_json -> model_validate_json`` roundtrip."""

    s = _scorecard()
    d1 = compute_scorecard_digest(s)
    restored = GovernanceScorecard.model_validate_json(s.model_dump_json())
    d2 = compute_scorecard_digest(restored)
    assert d1 == d2


# ── doc-15:201-264 Slice 13A consumption awareness ─────────────────────────


def test_doc_15_201_264_no_local_completeness_state_redefinition() -> None:
    """Per doc-15:201-264 Slice 13A Shared Completeness Model Dependency:
    the metric extractor that consumes :class:`EvidenceCompleteness`
    MUST NOT redefine the :data:`CompletenessState` Literal locally;
    the typed-shape foundation module here MUST NOT pre-empt that
    discipline by carrying its own Literal.

    The future metric-extractor sub-slices will consume the Slice 13A
    shared :data:`CompletenessState` from
    :mod:`iriai_build_v2.execution_control.completeness` via import.
    """

    import iriai_build_v2.execution_control.governance_metrics as mod

    # The module does NOT carry its own CompletenessState alias.
    assert not hasattr(mod, "CompletenessState")
    # The module does NOT carry its own EvidenceCompleteness alias.
    assert not hasattr(mod, "EvidenceCompleteness")
    # The module does NOT carry its own ExactEvidenceManifest alias.
    assert not hasattr(mod, "ExactEvidenceManifest")
    # The module does NOT carry its own AuthoritativeContextRef alias.
    assert not hasattr(mod, "AuthoritativeContextRef")


def test_doc_15_201_264_evidence_quality_is_imported_from_slice_13a() -> None:
    """Per doc-15:201-264 + doc-13a:285-287 step 9 the metric module
    consumes the Slice 13a :data:`EvidenceQuality` via direct import
    (NOT a re-declared local alias).
    """

    import iriai_build_v2.execution_control.governance_metrics as mod
    import iriai_build_v2.workflows.develop.governance.models as gov_mod

    # The module imports EvidenceQuality at module load (visible as a
    # module attribute via the import statement).
    assert hasattr(mod, "EvidenceQuality")
    # And it IS the Slice 13a shared Literal (identity assertion).
    assert mod.EvidenceQuality is gov_mod.EvidenceQuality


def test_doc_15_201_264_governance_evidence_ref_is_imported_from_slice_13a() -> None:
    """Per doc-15:201-264 + doc-13a:285-287 step 9 the metric module
    consumes the Slice 13a :class:`GovernanceEvidenceRef` via direct
    import (NOT a re-declared local class).
    """

    import iriai_build_v2.execution_control.governance_metrics as mod
    import iriai_build_v2.workflows.develop.governance.models as gov_mod

    # The module imports GovernanceEvidenceRef at module load.
    assert hasattr(mod, "GovernanceEvidenceRef")
    # And it IS the Slice 13a shared class (identity assertion).
    assert mod.GovernanceEvidenceRef is gov_mod.GovernanceEvidenceRef


# ── ConfigDict discipline parameterized across all 3 BaseModels ────────────


@pytest.mark.parametrize(
    "model_cls",
    [GovernanceMetricDefinition, GovernanceMetricValue, GovernanceScorecard],
)
def test_all_three_base_models_carry_extra_forbid(model_cls: type) -> None:
    """Per the implementer prompt § "Non-negotiables" all 3 doc-15:68-97
    BaseModels carry ``ConfigDict(extra="forbid")``."""

    cfg = model_cls.model_config
    assert cfg.get("extra") == "forbid"


# ── Slice 14 P3-V3-2 lineage (stronger pattern verification) ───────────────


def test_slice_14_p3_v3_2_stronger_pattern_evidence_quality_annotation_identity() -> None:
    """Per the implementer prompt § "MUST DO" item 7 (stronger
    annotation-identity pattern), this sub-slice's Slice 13a shared
    model identity test uses DIRECT
    ``model_fields[...].annotation is EvidenceQuality`` rather than the
    indirect value-set + namespace assertions Slice 14 1st sub-slice
    used at ``tests/test_execution_control_commit_provenance.py:718``
    (carried as P3-V3-2).

    This test re-pins the stronger pattern at the test-surface
    boundary so future Slice 15 sub-slices can reuse the pattern when
    consuming additional Slice 13A typed shapes.
    """

    # The DIRECT annotation-identity assertion proves the typed-surface
    # boundary at construction time -- not via runtime value coercion.
    assert (
        GovernanceMetricValue.model_fields["data_quality"].annotation
        is EvidenceQuality
    )
