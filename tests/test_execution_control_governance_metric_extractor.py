"""Slice 15 second sub-slice -- unit tests for the metric extractor at
``execution_control/governance_metric_extractor.py``.

Covers the typed-shape construction + bounded-read discipline + typed-vs-legacy
``source_mix`` + ``data_quality`` projection + active-work policy enforcement +
sample-count threshold + the doc-13a:269-272 fail-closed gate + the
extractor-never-raises non-blocking contract + the DIRECT annotation-identity
Slice 13A/13a/14 REUSE assertions (the stronger pattern that functionally
addresses Slice 14 V3 reviewer P3-V3-2 carry).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every Pydantic
field validator; no executor wiring outside this slice's own acceptance
tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 1st sub-slice modules
+ tests remain byte-identical.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.completeness import (
    CompletenessState,
    EvidenceCompleteness,
)
from iriai_build_v2.execution_control.dispatcher_prompt_context import (
    AuthoritativePromptContextRouting,
)
from iriai_build_v2.execution_control.governance_metric_extractor import (
    ACTIVE_WORK_EXCLUDED_EXCLUSION,
    GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID,
    INSUFFICIENT_SAMPLES_EXCLUSION,
    PROMPT_CONTEXT_INCOMPLETE_EXCLUSION,
    GovernanceMetricExtractionGap,
    MetricExtractor,
    MetricExtractorInputs,
    TaskShapeInputs,
)
from iriai_build_v2.execution_control.governance_metrics import (
    GovernanceMetricDefinition,
    GovernanceMetricValue,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    FailureType,
    _RETRYABLE_FAILURE_TYPES,
    _ROUTE_ROWS,
)
from iriai_build_v2.workflows.develop.governance.models import (
    EvidenceQuality,
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
    """Construct a fully-specified :class:`GovernanceMetricDefinition` for tests."""

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


def _inputs(**overrides: object) -> MetricExtractorInputs:
    """Construct a fully-specified :class:`MetricExtractorInputs` for tests.

    Defaults to 5 typed refs (above the
    :data:`MetricExtractor.DEFAULT_MIN_SAMPLE_COUNT` threshold of 3) +
    a single definition + a fresh completeness state.
    """

    base: dict[str, object] = dict(
        corpus_id="corpus-1",
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


def _routing_blocked() -> AuthoritativePromptContextRouting:
    """Construct an :class:`AuthoritativePromptContextRouting` that triggers
    the doc-13a:269-272 fail-closed gate."""

    return AuthoritativePromptContextRouting(
        should_invoke_runtime=False,
        typed_failure_class="runtime_context",
        typed_failure_type="context_incomplete",
        unavailable_reason="prompt context missing required field",
        missing_field_names=("test_field",),
    )


def _routing_open() -> AuthoritativePromptContextRouting:
    """Construct an :class:`AuthoritativePromptContextRouting` that does
    NOT trigger the fail-closed gate."""

    return AuthoritativePromptContextRouting(should_invoke_runtime=True)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface() -> None:
    """The module ``__all__`` carries the typed inputs + typed task-shape
    bundle + gap finding + failure id + 3 exclusion sentinels + the
    :class:`MetricExtractor`.

    Per the 3rd sub-slice the 4th defensive sentinel
    ``PLACEHOLDER_ARITHMETIC_EXCLUSION`` was REMOVED from BOTH the emit
    path AND the ``__all__`` AND the module-level constants now that
    real numerator / denominator arithmetic + calibrated confidence land
    (doc-15:125-132 steps 4-5 + AC4 doc-15:181). The
    :class:`TaskShapeInputs` typed bundle is the 3rd sub-slice's new
    public surface for the complexity-adjustment input (doc-15:125-130
    step 4).
    """

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    expected = {
        "MetricExtractorInputs",
        "TaskShapeInputs",
        "GovernanceMetricExtractionGap",
        "GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID",
        "PROMPT_CONTEXT_INCOMPLETE_EXCLUSION",
        "INSUFFICIENT_SAMPLES_EXCLUSION",
        "ACTIVE_WORK_EXCLUDED_EXCLUSION",
        "MetricExtractor",
    }
    assert set(mod.__all__) == expected
    for name in expected:
        assert hasattr(mod, name)
    # Sentinel REMOVED per 3rd sub-slice scope.
    assert "PLACEHOLDER_ARITHMETIC_EXCLUSION" not in set(mod.__all__)
    assert not hasattr(mod, "PLACEHOLDER_ARITHMETIC_EXCLUSION")


def test_module_does_not_redefine_evidence_quality() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the extractor module
    MUST NOT redefine :data:`EvidenceQuality` -- it consumes the Slice 13a
    shared Literal via import only."""

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert "EvidenceQuality" not in set(mod.__all__)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the extractor module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes
    the Slice 13a shared model via import only."""

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert "GovernanceEvidenceRef" not in set(mod.__all__)


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-13a:285-287 step 9 + doc-15:201-264 the extractor module
    MUST NOT redefine :data:`CompletenessState` -- it consumes the Slice
    13A 2nd-sub-slice Literal via import only."""

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert "CompletenessState" not in set(mod.__all__)


def test_module_does_not_redefine_evidence_completeness() -> None:
    """Per doc-15:201-264 the extractor module MUST NOT redefine
    :class:`EvidenceCompleteness` -- it consumes the Slice 13A
    2nd-sub-slice shared model via import only."""

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert "EvidenceCompleteness" not in set(mod.__all__)


def test_module_does_not_redefine_governance_metric_value() -> None:
    """Per doc-15:64-97 the extractor module MUST NOT redefine
    :class:`GovernanceMetricValue` -- it consumes the Slice 15
    1st-sub-slice shared model via import only."""

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert "GovernanceMetricValue" not in set(mod.__all__)
    assert "GovernanceMetricDefinition" not in set(mod.__all__)


def test_module_import_discipline_no_implementation_py() -> None:
    """The extractor MUST NOT import implementation.py (would invert the
    foundational-module dependency direction)."""

    import iriai_build_v2.execution_control.governance_metric_extractor as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    assert "from iriai_build_v2.workflows.develop.phases.implementation" not in text


def test_module_import_discipline_only_allowed_imports() -> None:
    """Per the implementer prompt § "Non-negotiables" the extractor module
    imports ONLY from stdlib + Pydantic v2 + Slice 13a governance.models +
    Slice 13A completeness/dispatcher_prompt_context + Slice 15 1st-sub-slice
    governance_metrics. NO imports from governance/ outside models, NO
    imports from workflows/develop/execution/phases/, supervisor, or
    dashboard."""

    import iriai_build_v2.execution_control.governance_metric_extractor as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()

    # Forbidden imports.
    assert "from iriai_build_v2.workflows.develop.execution.phases" not in text
    assert "from iriai_build_v2.supervisor" not in text
    assert "import iriai_build_v2.dashboard" not in text
    # Permitted imports (sanity).
    assert "from iriai_build_v2.execution_control.completeness" in text
    assert "from iriai_build_v2.execution_control.governance_metrics" in text
    assert "from iriai_build_v2.workflows.develop.governance.models" in text


def test_package_init_does_not_re_export_extractor() -> None:
    """Mirrors Slice 15 1st sub-slice + Slice 14 + Slice 13A precedent:
    ``governance_metric_extractor.py`` is consumed via fully-qualified
    imports, NOT re-exported through the ``execution_control`` package."""

    from iriai_build_v2 import execution_control as pkg

    if hasattr(pkg, "__all__"):
        assert "governance_metric_extractor" not in set(pkg.__all__)
        assert "MetricExtractor" not in set(pkg.__all__)
        assert "MetricExtractorInputs" not in set(pkg.__all__)


# ── MetricExtractorInputs typed bundle (chunk-shape point 2) ───────────────


def test_inputs_accepts_all_required_fields() -> None:
    """The 6 required fields + 1 optional populate cleanly."""

    inputs = _inputs()
    assert inputs.corpus_id == "corpus-1"
    assert len(inputs.definitions) == 1
    assert len(inputs.evidence_set_refs) == 5
    assert inputs.completeness_state.state == "complete"
    assert inputs.active_work_filter == "exclude"
    assert inputs.freshness_window_hours == 168.0
    assert inputs.prompt_context_routing is None


def test_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _inputs(unknown_field="oops")  # type: ignore[arg-type]


def test_inputs_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    inputs = _inputs()
    serialised = inputs.model_dump_json()
    restored = MetricExtractorInputs.model_validate_json(serialised)
    assert restored == inputs


def test_inputs_accepts_optional_prompt_context_routing() -> None:
    """The optional ``prompt_context_routing`` field carries the typed
    Slice 13A 4th sub-slice :class:`AuthoritativePromptContextRouting`."""

    routing = _routing_blocked()
    inputs = _inputs(prompt_context_routing=routing)
    assert inputs.prompt_context_routing is routing


@pytest.mark.parametrize(
    "filter_value",
    ["exclude", "status_only", "separate"],
)
def test_inputs_active_work_filter_accepts_all_3_values(filter_value: str) -> None:
    """Per doc-15:123-124 the 3-value active-work filter Literal accepts
    all 3 doc-15:75 values."""

    inputs = _inputs(active_work_filter=filter_value)
    assert inputs.active_work_filter == filter_value


def test_inputs_active_work_filter_rejects_unknown() -> None:
    """Unknown active-work filter values fail closed."""

    with pytest.raises(ValidationError):
        _inputs(active_work_filter="not_a_filter")  # type: ignore[arg-type]


def test_inputs_completeness_state_is_typed_evidence_completeness() -> None:
    """Per doc-15:201-264 the ``completeness_state`` field is the typed
    Slice 13A :class:`EvidenceCompleteness` (NOT a raw dict)."""

    inputs = _inputs()
    assert isinstance(inputs.completeness_state, EvidenceCompleteness)


def test_inputs_evidence_set_refs_is_typed_governance_evidence_ref_list() -> None:
    """Per doc-15:141-142 + doc-15:182 AC5 the ``evidence_set_refs`` field
    is the typed Slice 13a :class:`GovernanceEvidenceRef` list (NOT a
    raw dict list, NOT raw artifact bodies)."""

    inputs = _inputs()
    for ref in inputs.evidence_set_refs:
        assert isinstance(ref, GovernanceEvidenceRef)


def test_inputs_definitions_is_typed_governance_metric_definition_list() -> None:
    """Per doc-15:68-76 the ``definitions`` field is the typed Slice 15
    :class:`GovernanceMetricDefinition` list."""

    inputs = _inputs()
    for d in inputs.definitions:
        assert isinstance(d, GovernanceMetricDefinition)


def test_inputs_freshness_window_hours_accepts_float() -> None:
    """Freshness window accepts a float (hours)."""

    inputs = _inputs(freshness_window_hours=72.5)
    assert inputs.freshness_window_hours == 72.5


def test_inputs_accepts_empty_definitions_list() -> None:
    """Empty definitions list is valid (the extractor returns empty result)."""

    inputs = _inputs(definitions=[])
    assert inputs.definitions == []


def test_inputs_accepts_empty_evidence_refs_list() -> None:
    """Empty evidence-refs list is valid (the extractor will emit
    value=None per the sample-count threshold)."""

    inputs = _inputs(evidence_set_refs=[])
    assert inputs.evidence_set_refs == []


# ── Slice 13A/13a/14 REUSE direct annotation-identity ─────────────────────
# This is the STRONGER pattern that functionally addresses Slice 14 V3
# reviewer P3-V3-2 carry (indirect Slice 13A shared model identity).


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

    DIRECT annotation-identity assertion via ``get_origin`` +
    ``get_args``.
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


def test_inputs_prompt_context_routing_annotation_includes_slice_13a_routing() -> None:
    """The ``prompt_context_routing`` field annotation is the Slice 13A
    4th sub-slice :class:`AuthoritativePromptContextRouting` (NOT a
    local redefinition)."""

    annotation = MetricExtractorInputs.model_fields["prompt_context_routing"].annotation
    # Union: AuthoritativePromptContextRouting | None.
    args = get_args(annotation)
    assert AuthoritativePromptContextRouting in args
    assert type(None) in args


# ── GovernanceMetricExtractionGap typed shape (chunk-shape point 4) ────────


def _gap(**overrides: object) -> GovernanceMetricExtractionGap:
    """Construct a fully-specified :class:`GovernanceMetricExtractionGap`."""

    base: dict[str, object] = dict(
        failure_id="governance_metric_extraction_failed",
        corpus_id="corpus-1",
        definition_name="tasks_per_hour",
        definition_version="v1.0",
        scope_kind="feature",
        reason="evidence_refs_missing",
        evidence_payload={"detail": "test"},
    )
    base.update(overrides)
    return GovernanceMetricExtractionGap(**base)  # type: ignore[arg-type]


def test_gap_finding_accepts_all_fields() -> None:
    """All 7 fields populate cleanly."""

    gap = _gap()
    assert gap.failure_id == "governance_metric_extraction_failed"
    assert gap.corpus_id == "corpus-1"
    assert gap.definition_name == "tasks_per_hour"
    assert gap.definition_version == "v1.0"
    assert gap.scope_kind == "feature"
    assert gap.reason == "evidence_refs_missing"
    assert gap.evidence_payload == {"detail": "test"}


def test_gap_finding_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _gap(unknown_field="oops")  # type: ignore[arg-type]


def test_gap_finding_failure_id_literal_rejects_unknown() -> None:
    """The ``failure_id`` Literal restricts to the one typed value."""

    with pytest.raises(ValidationError):
        _gap(failure_id="not_a_failure_id")  # type: ignore[arg-type]


def test_gap_finding_evidence_payload_defaults_to_empty_dict() -> None:
    """``evidence_payload`` defaults to empty dict."""

    gap = GovernanceMetricExtractionGap(
        failure_id="governance_metric_extraction_failed",
        corpus_id="corpus-1",
        definition_name="tasks_per_hour",
        definition_version="v1.0",
        scope_kind="feature",
        reason="r",
    )
    assert gap.evidence_payload == {}


def test_gap_finding_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    gap = _gap()
    serialised = gap.model_dump_json()
    restored = GovernanceMetricExtractionGap.model_validate_json(serialised)
    assert restored == gap


# ── failure-id constants + failure_router registration (point 4) ───────────


def test_failure_id_constant_value() -> None:
    """The typed failure-id constant carries the expected value."""

    assert GOVERNANCE_METRIC_EXTRACTION_FAILURE_ID == "governance_metric_extraction_failed"


def test_failure_router_registers_new_typed_failure_id() -> None:
    """The NEW failure id ``governance_metric_extraction_failed`` is
    registered in the Slice 07 failure router."""

    assert "governance_metric_extraction_failed" in FAILURE_TYPES
    assert "governance_metric_extraction_failed" in get_args(FailureType)


def test_failure_router_route_under_evidence_corruption_with_retry_governance_projection() -> None:
    """Per doc-15:140-145 + doc-14:242-243 the NEW failure id routes
    under the EXISTING ``evidence_corruption`` failure_class to the
    EXISTING ``retry_governance_projection`` NON-blocking RouteAction
    (REUSED from Slice 14 2nd sub-slice)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_metric_extraction_failed"
    ]
    assert len(matches) == 1
    type_pol, route_pol = matches[0]
    assert type_pol.failure_class == "evidence_corruption"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_new_id_is_retryable_not_deterministic() -> None:
    """Per the Slice 14 precedent the NEW failure id is observer-transient
    (retryable; NOT deterministic)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_metric_extraction_failed"
    ]
    assert len(matches) == 1
    type_pol, _ = matches[0]
    assert type_pol.retryable
    assert not type_pol.deterministic


def test_failure_router_new_id_in_retryable_set() -> None:
    """The NEW failure id is in :data:`_RETRYABLE_FAILURE_TYPES`."""

    assert "governance_metric_extraction_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_is_non_blocking() -> None:
    """The route is non-blocking (NOT ``quiesce``)."""

    matches = [
        row for row in _ROUTE_ROWS if row[0].failure_type == "governance_metric_extraction_failed"
    ]
    assert len(matches) == 1
    type_pol, route_pol = matches[0]
    assert route_pol.action != "quiesce"
    assert route_pol.action != "operator_required"
    assert route_pol.action == "retry_governance_projection"


# ── exclusion sentinel constants ───────────────────────────────────────────


def test_prompt_context_incomplete_exclusion_constant_value() -> None:
    """The doc-13a:269-272 fail-closed-gate sentinel carries the
    expected value."""

    assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION == "prompt_context_incomplete"


def test_insufficient_samples_exclusion_constant_value() -> None:
    """The doc-15:148-150 sample-count sentinel carries the
    expected value."""

    assert INSUFFICIENT_SAMPLES_EXCLUSION == "insufficient_samples_excluded"


def test_active_work_excluded_exclusion_constant_value() -> None:
    """The doc-15:123-124 active-work-excluded sentinel carries the
    expected value."""

    assert ACTIVE_WORK_EXCLUDED_EXCLUSION == "active_work_excluded"


# ── doc-13a:269-272 fail-closed gate (chunk-shape point 3) ─────────────────


def test_extract_fail_closed_gate_triggers_on_routing_blocked() -> None:
    """Per doc-13a:269-272 + the auto-memory ``feedback_no_silent_degradation``
    rule: when the prompt_context_routing reports
    ``should_invoke_runtime=False`` + ``typed_failure_type="context_incomplete"``
    the extractor MUST emit ``value=None`` + add the
    :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` sentinel to every value."""

    extractor = MetricExtractor()
    inputs = _inputs(prompt_context_routing=_routing_blocked())
    results = extractor.extract(inputs)

    assert len(results) == 1
    v = results[0]
    assert v.value is None
    assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION in v.exclusions
    assert v.confidence == 0.0
    assert v.data_quality == "insufficient"


def test_extract_fail_closed_gate_blocks_multiple_definitions() -> None:
    """The fail-closed gate triggers per-definition; every emitted value
    carries the sentinel."""

    extractor = MetricExtractor()
    definitions = [
        _definition(name="tasks_per_hour"),
        _definition(name="repair_cycles_per_task", denominator="completed tasks"),
        _definition(name="merge_queue_wait_hours", denominator="completed tasks"),
    ]
    inputs = _inputs(definitions=definitions, prompt_context_routing=_routing_blocked())
    results = extractor.extract(inputs)
    assert len(results) == 3
    for v in results:
        assert v.value is None
        assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION in v.exclusions


def test_extract_fail_closed_gate_does_not_trigger_on_routing_open() -> None:
    """When the routing reports ``should_invoke_runtime=True`` the
    fail-closed gate does NOT trigger; the extractor consumes the
    evidence normally."""

    extractor = MetricExtractor()
    inputs = _inputs(prompt_context_routing=_routing_open())
    results = extractor.extract(inputs)
    for v in results:
        assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION not in v.exclusions


def test_extract_fail_closed_gate_does_not_trigger_on_routing_none() -> None:
    """When the routing is None (default) the fail-closed gate does NOT
    trigger; the extractor consumes the evidence normally."""

    extractor = MetricExtractor()
    inputs = _inputs(prompt_context_routing=None)
    results = extractor.extract(inputs)
    for v in results:
        assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION not in v.exclusions


def test_extract_fail_closed_gate_emits_no_evidence_refs() -> None:
    """Per the fail-closed rule the extractor MUST NOT consume the
    evidence; the emitted value carries an empty ``evidence_refs`` list."""

    extractor = MetricExtractor()
    inputs = _inputs(prompt_context_routing=_routing_blocked())
    results = extractor.extract(inputs)
    for v in results:
        assert v.evidence_refs == []


# ── typed-vs-legacy source_mix (doc-15:151-153) ────────────────────────────


def test_extract_source_mix_typed_only() -> None:
    """When all refs are typed (one of the 7 typed-first authorities)
    the source_mix dict carries only the typed count."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="compatibility_projection"),
            _ref(ref_id="r3", authority="git_provenance"),
            _ref(ref_id="r4", authority="implementation_journal"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.source_mix == {"typed": 4}


def test_extract_source_mix_legacy_only() -> None:
    """When all refs are legacy (one of the 2 legacy-fallback authorities)
    the source_mix dict carries only the legacy count."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="legacy_event"),
            _ref(ref_id="r2", authority="legacy_event"),
            _ref(ref_id="r3", authority="legacy_artifact_summary"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.source_mix == {"legacy": 3}


def test_extract_source_mix_typed_and_legacy_mixed() -> None:
    """Per doc-15:151-153 mixed typed + legacy evidence sets
    ``data_quality="derived"`` + ``source_mix={"typed": n, "legacy": m}``."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
            _ref(ref_id="r3", authority="legacy_event"),
            _ref(ref_id="r4", authority="legacy_artifact_summary"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.source_mix == {"typed": 2, "legacy": 2}
    assert v.data_quality == "derived"


def test_extract_source_mix_with_no_refs_is_empty_dict() -> None:
    """When the extractor has no refs after filtering, ``source_mix`` is
    the empty dict default."""

    extractor = MetricExtractor()
    inputs = _inputs(evidence_set_refs=[])
    results = extractor.extract(inputs)
    v = results[0]
    assert v.source_mix == {}


# ── data_quality projection (doc-15:131-132 + doc-15:151-153) ──────────────


def test_data_quality_canonical_for_typed_only_complete_fresh() -> None:
    """Typed-only + state=complete + fresh refs -> data_quality=canonical."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
            _ref(ref_id="r3", authority="typed_journal"),
        ],
        completeness_state=_completeness(state="complete"),
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "canonical"


def test_data_quality_derived_for_mixed_typed_and_legacy() -> None:
    """Mixed typed + legacy refs -> data_quality=derived per doc-15:151-153."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="legacy_event"),
            _ref(ref_id="r3", authority="legacy_event"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "derived"


def test_data_quality_sampled_for_legacy_only() -> None:
    """Legacy-only refs -> data_quality=sampled (per Slice 13a quality
    projection doc-13:173-175)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="legacy_event"),
            _ref(ref_id="r2", authority="legacy_event"),
            _ref(ref_id="r3", authority="legacy_artifact_summary"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "sampled"


def test_data_quality_advisory_for_preview_only_completeness_state() -> None:
    """state=preview_only + typed refs -> data_quality=advisory (Slice 13A
    invariant doc-13a:18-23)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
            _ref(ref_id="r3", authority="typed_journal"),
        ],
        completeness_state=_completeness(state="preview_only"),
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "advisory"


def test_data_quality_insufficient_for_unavailable_completeness_state() -> None:
    """state=unavailable -> data_quality=insufficient per doc-13a:307-310."""

    extractor = MetricExtractor()
    inputs = _inputs(
        completeness_state=_completeness(state="unavailable", unavailable_reason="r"),
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "insufficient"


def test_data_quality_derived_for_paged_completeness_state() -> None:
    """state=paged + typed refs -> data_quality=derived (paged is
    authoritative but spans multiple pages)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
            _ref(ref_id="r3", authority="typed_journal"),
        ],
        completeness_state=_completeness(state="paged"),
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "derived"


def test_data_quality_insufficient_for_no_refs() -> None:
    """No refs at all -> data_quality=insufficient (defence-in-depth)."""

    extractor = MetricExtractor()
    inputs = _inputs(evidence_set_refs=[])
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "insufficient"


def test_data_quality_stale_for_all_typed_refs_older_than_window() -> None:
    """All typed refs older than the freshness window -> data_quality=stale."""

    stale = datetime.now(timezone.utc) - timedelta(hours=200)
    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal", created_at=stale),
            _ref(ref_id="r2", authority="typed_journal", created_at=stale),
            _ref(ref_id="r3", authority="typed_journal", created_at=stale),
        ],
        completeness_state=_completeness(state="complete"),
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "stale"


def test_data_quality_canonical_when_at_least_one_ref_fresh() -> None:
    """If at least one typed ref is fresh -> data_quality=canonical
    (per the conservative freshness rule)."""

    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    stale = datetime.now(timezone.utc) - timedelta(hours=500)
    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal", created_at=fresh),
            _ref(ref_id="r2", authority="typed_journal", created_at=stale),
            _ref(ref_id="r3", authority="typed_journal", created_at=stale),
        ],
        completeness_state=_completeness(state="complete"),
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.data_quality == "canonical"


# ── active-work policy enforcement (doc-15:123-124) ────────────────────────


def _active_ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct an active-work-flagged ref (preview_only=True +
    completeness=preview_only)."""

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="active-ref",
        digest="sha256:zzz",
        quality="canonical",
        completeness="preview_only",
        preview_only=True,
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def test_active_work_policy_exclude_drops_active_refs() -> None:
    """Per doc-15:123-124 active_work_policy="exclude" drops active-work
    refs from the numerator + denominator; the emitted value carries the
    ACTIVE_WORK_EXCLUDED_EXCLUSION sentinel."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="completed-1", authority="typed_journal"),
            _ref(ref_id="completed-2", authority="typed_journal"),
            _ref(ref_id="completed-3", authority="typed_journal"),
            _active_ref(ref_id="active-1"),
            _active_ref(ref_id="active-2"),
        ],
        definitions=[_definition(active_work_policy="exclude")],
        active_work_filter="exclude",
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert ACTIVE_WORK_EXCLUDED_EXCLUSION in v.exclusions
    # 3 completed refs survive; 2 active refs are dropped.
    assert v.source_mix == {"typed": 3}


def test_active_work_policy_status_only_keeps_active_refs() -> None:
    """Per doc-15:123-124 active_work_policy="status_only" includes
    active-work refs in the extracted value's evidence_refs list (the
    upstream status surface flags them via preview_only)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="completed-1", authority="typed_journal"),
            _ref(ref_id="completed-2", authority="typed_journal"),
            _ref(ref_id="completed-3", authority="typed_journal"),
            _active_ref(ref_id="active-1"),
        ],
        definitions=[_definition(active_work_policy="status_only")],
        active_work_filter="status_only",
    )
    results = extractor.extract(inputs)
    v = results[0]
    # All 4 refs are retained (preview_only refs survive).
    assert len(v.evidence_refs) == 4
    assert ACTIVE_WORK_EXCLUDED_EXCLUSION not in v.exclusions


def test_active_work_policy_separate_keeps_active_refs() -> None:
    """Per doc-15:123-124 active_work_policy="separate" keeps active refs
    for the future Slice 15 sub-slice that splits into per-scope values."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="completed-1", authority="typed_journal"),
            _ref(ref_id="completed-2", authority="typed_journal"),
            _ref(ref_id="completed-3", authority="typed_journal"),
            _active_ref(ref_id="active-1"),
        ],
        definitions=[_definition(active_work_policy="separate")],
        active_work_filter="separate",
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert len(v.evidence_refs) == 4
    assert ACTIVE_WORK_EXCLUDED_EXCLUSION not in v.exclusions


def test_active_work_policy_corpus_filter_safety_net() -> None:
    """The corpus-level filter is a safety net: when EITHER side requests
    exclude, the extractor drops active refs."""

    extractor = MetricExtractor()
    # Per-definition is status_only, but corpus is exclude -> active refs
    # are dropped per the safety net.
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="completed-1", authority="typed_journal"),
            _ref(ref_id="completed-2", authority="typed_journal"),
            _ref(ref_id="completed-3", authority="typed_journal"),
            _active_ref(ref_id="active-1"),
        ],
        definitions=[_definition(active_work_policy="status_only")],
        active_work_filter="exclude",
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert ACTIVE_WORK_EXCLUDED_EXCLUSION in v.exclusions


def test_active_work_excluded_sentinel_only_present_when_drops_active() -> None:
    """The active-work-excluded sentinel only appears when there is at
    least one active ref that was dropped."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
            _ref(ref_id="r3", authority="typed_journal"),
        ],
        definitions=[_definition(active_work_policy="exclude")],
        active_work_filter="exclude",
    )
    results = extractor.extract(inputs)
    v = results[0]
    # No active refs to drop -> no sentinel.
    assert ACTIVE_WORK_EXCLUDED_EXCLUSION not in v.exclusions


# ── sample-count threshold (doc-15:148-150) ────────────────────────────────


def test_sample_count_below_threshold_emits_value_none() -> None:
    """Per doc-15:148-150 the insufficient-sample case emits value=None
    + conservative confidence + the INSUFFICIENT_SAMPLES_EXCLUSION
    sentinel."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value is None
    assert v.confidence == 0.0
    assert INSUFFICIENT_SAMPLES_EXCLUSION in v.exclusions


def test_sample_count_at_threshold_emits_typed_value() -> None:
    """At-or-above threshold the extractor emits a REAL typed value
    (float | int) per doc-15:125-132 steps 4-5.

    Per the 3rd sub-slice the at-or-above-threshold emit path returns
    real numerator / denominator arithmetic (NOT the 2nd-sub-slice
    placeholder ``value=None`` + ``PLACEHOLDER_ARITHMETIC_EXCLUSION``
    sentinel). The :data:`INSUFFICIENT_SAMPLES_EXCLUSION` sentinel
    MUST NOT be present (the sample count IS at-threshold).

    With the default fixture (definition with denominator="elapsed hours"
    + freshness_window_hours=168.0; 3 typed-journal refs +
    required_evidence_kinds=["typed_journal"]):

    * numerator = 3 (refs with authority in required_evidence_kinds)
    * denominator = 168.0 (freshness_window_hours)
    * value = 3 / 168.0 ≈ 0.0179

    The 3rd sub-slice REMOVED the ``PLACEHOLDER_ARITHMETIC_EXCLUSION``
    sentinel; this test verifies the sentinel is fully absent from the
    public surface as the typed-measurement contract.
    """

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
            _ref(ref_id="r2", authority="typed_journal"),
            _ref(ref_id="r3", authority="typed_journal"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    # Real typed value (float | int) per doc-15:82.
    assert v.value is not None
    assert isinstance(v.value, (int, float))
    assert v.value > 0
    assert INSUFFICIENT_SAMPLES_EXCLUSION not in v.exclusions
    # The expected ratio is 3 typed-journal refs / 168 freshness hours.
    assert v.value == pytest.approx(3.0 / 168.0)


def test_sample_count_zero_emits_value_none() -> None:
    """No refs at all -> value=None + the threshold sentinel."""

    extractor = MetricExtractor()
    inputs = _inputs(evidence_set_refs=[])
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value is None
    assert INSUFFICIENT_SAMPLES_EXCLUSION in v.exclusions


def test_sample_count_threshold_emits_evidence_refs_for_diagnosis() -> None:
    """Even when value=None the emitted ``evidence_refs`` carries the
    retained refs so downstream consumers can diagnose insufficient
    sample sets."""

    extractor = MetricExtractor()
    refs = [
        _ref(ref_id="r1", authority="typed_journal"),
        _ref(ref_id="r2", authority="typed_journal"),
    ]
    inputs = _inputs(evidence_set_refs=refs)
    results = extractor.extract(inputs)
    v = results[0]
    assert len(v.evidence_refs) == 2


# ── extractor never raises (doc-14:242-243 non-blocking discipline) ────────


def test_extractor_returns_empty_list_for_empty_definitions() -> None:
    """No definitions -> empty result list (NOT a raise)."""

    extractor = MetricExtractor()
    inputs = _inputs(definitions=[])
    results = extractor.extract(inputs)
    assert results == []


def test_extractor_extract_returns_list_of_governance_metric_value() -> None:
    """The ``extract`` method returns a typed
    ``list[GovernanceMetricValue]`` per chunk-shape point 3."""

    extractor = MetricExtractor()
    inputs = _inputs()
    results = extractor.extract(inputs)
    for v in results:
        assert isinstance(v, GovernanceMetricValue)


def test_extractor_extract_one_value_per_definition() -> None:
    """One :class:`GovernanceMetricValue` per :class:`GovernanceMetricDefinition`."""

    extractor = MetricExtractor()
    definitions = [
        _definition(name="tasks_per_hour"),
        _definition(name="hours_per_task", denominator="completed tasks"),
        _definition(name="workflow_drag_hours"),
        _definition(name="repair_cycles_per_task", denominator="completed tasks"),
    ]
    inputs = _inputs(definitions=definitions)
    results = extractor.extract(inputs)
    assert len(results) == 4
    names = [v.definition_name for v in results]
    assert set(names) == {
        "tasks_per_hour",
        "hours_per_task",
        "workflow_drag_hours",
        "repair_cycles_per_task",
    }


def test_extractor_extract_carries_definition_version_per_doc_15_144_145() -> None:
    """Per doc-15:144-145 scorecards must include metric definition
    versions; the extractor carries ``definition_version`` from the
    definition onto each emitted value."""

    extractor = MetricExtractor()
    inputs = _inputs(definitions=[_definition(version="v2.5")])
    results = extractor.extract(inputs)
    assert results[0].definition_version == "v2.5"


def test_extractor_gap_findings_is_empty_initially() -> None:
    """A fresh extractor has an empty ``gap_findings`` list."""

    extractor = MetricExtractor()
    assert extractor.gap_findings == []


def test_extractor_gap_findings_is_a_copy_not_internal_reference() -> None:
    """Per the immutable-public-surface discipline ``gap_findings`` returns
    a fresh list each call (so mutations don't leak into internal state)."""

    extractor = MetricExtractor()
    first = extractor.gap_findings
    second = extractor.gap_findings
    assert first is not second


def test_extractor_extract_clears_prior_gap_findings() -> None:
    """A subsequent ``extract`` call resets the accumulator."""

    extractor = MetricExtractor()
    inputs = _inputs()
    extractor.extract(inputs)
    extractor.extract(inputs)
    assert extractor.gap_findings == []  # No structural failures in either call.


# ── bounded-read discipline (doc-15:141-142 + AC5 doc-15:182) ──────────────


def test_extractor_only_consumes_ref_metadata_not_raw_bodies() -> None:
    """Per doc-15:141-142 + AC5 doc-15:182 the extractor consumes refs by
    metadata only -- not raw artifact bodies. The
    :class:`GovernanceEvidenceRef` typed shape enforces this at the
    construction-time contract (the typed shape carries ``ref_id`` +
    ``digest`` + ``authority`` etc., NOT a raw body field)."""

    # Sanity check: the typed shape has no raw-body fields.
    fields = set(GovernanceEvidenceRef.model_fields)
    assert "raw_body" not in fields
    assert "body" not in fields
    assert "content" not in fields
    assert "payload_body" not in fields


def test_extractor_does_not_call_artifact_body_helpers() -> None:
    """The extractor module does NOT import any helper that hydrates raw
    artifact bodies."""

    import iriai_build_v2.execution_control.governance_metric_extractor as mod

    src = mod.__file__
    assert src is not None
    with open(src) as f:
        text = f.read()
    # Forbidden body-hydrating helpers from prior slices.
    assert "load_artifact_body" not in text
    assert "fetch_raw_body" not in text
    assert "hydrate_payload" not in text


def test_extractor_carries_evidence_refs_on_emitted_value() -> None:
    """The emitted ``evidence_refs`` carries the typed Slice 13a
    :class:`GovernanceEvidenceRef` list (NOT raw bodies)."""

    extractor = MetricExtractor()
    refs = [
        _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
    ]
    inputs = _inputs(evidence_set_refs=refs)
    results = extractor.extract(inputs)
    v = results[0]
    for ref in v.evidence_refs:
        assert isinstance(ref, GovernanceEvidenceRef)


# ── scope projection (doc-15:81) ──────────────────────────────────────────


def test_extracted_value_scope_carries_corpus_id_and_scope_kind() -> None:
    """Per doc-15:81 the ``scope`` dict is typed-key (corpus_id +
    scope_kind for v1)."""

    extractor = MetricExtractor()
    inputs = _inputs(corpus_id="8ac124d6", definitions=[_definition(scope_kind="lane")])
    results = extractor.extract(inputs)
    v = results[0]
    assert v.scope["corpus_id"] == "8ac124d6"
    assert v.scope["scope_kind"] == "lane"


# ── confidence scoring (doc-15:131-132 step 5) -- 2nd-sub-slice placeholder ─


def test_confidence_is_zero_for_insufficient_samples() -> None:
    """Insufficient sample count -> confidence=0.0 per doc-15:148-150
    (conservative + blocks policy recommendations)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[_ref(ref_id="r1", authority="typed_journal")],
    )
    results = extractor.extract(inputs)
    assert results[0].confidence == 0.0


def test_confidence_higher_for_canonical_typed_evidence() -> None:
    """canonical data_quality + above-threshold sample count -> higher
    confidence than insufficient."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
    )
    results = extractor.extract(inputs)
    assert results[0].confidence > 0.0


def test_confidence_lower_for_legacy_only_sampled_quality() -> None:
    """legacy-only refs -> data_quality=sampled -> lower confidence than
    canonical."""

    extractor = MetricExtractor()
    typed_inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
    )
    legacy_inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="legacy_event") for i in range(5)
        ],
    )
    typed_results = extractor.extract(typed_inputs)
    legacy_results = extractor.extract(legacy_inputs)
    assert typed_results[0].confidence > legacy_results[0].confidence


def test_confidence_lower_for_derived_quality_at_equal_high_sample_count() -> None:
    """derived data_quality (mixed typed + legacy) -> lower confidence
    than canonical at the same high sample count (both above the
    sample-count cap so the ramp is saturated)."""

    extractor = MetricExtractor()
    canonical_inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)
        ],
    )
    derived_inputs = _inputs(
        evidence_set_refs=(
            [_ref(ref_id=f"t{i}", authority="typed_journal") for i in range(15)]
            + [_ref(ref_id=f"l{i}", authority="legacy_event") for i in range(15)]
        ),
    )
    canonical_results = extractor.extract(canonical_inputs)
    derived_results = extractor.extract(derived_inputs)
    # canonical at 30 samples => 0.9 (saturated); derived flat 0.6.
    assert canonical_results[0].confidence > derived_results[0].confidence


# ── 3rd sub-slice: sentinel REMOVED; the 5 finalizer-added sentinel ────────
# tests (test_placeholder_arithmetic_exclusion_constant_value +
# test_placeholder_arithmetic_exclusion_in_module_all +
# test_placeholder_arithmetic_exclusion_emitted_on_above_threshold_path +
# test_placeholder_arithmetic_exclusion_NOT_emitted_on_below_threshold_path +
# test_placeholder_arithmetic_exclusion_NOT_emitted_on_fail_closed_path)
# are DELETED per the 3rd sub-slice scope (the sentinel is gone).
# The full-removal assertion lives in
# test_module_all_lists_documented_surface (which now asserts
# ``PLACEHOLDER_ARITHMETIC_EXCLUSION`` is NOT in __all__ and NOT a
# module attribute).


# ── 2nd-sub-slice combined integration tests ───────────────────────────────


def test_combined_fail_closed_gate_takes_precedence_over_other_signals() -> None:
    """When the doc-13a:269-272 fail-closed gate triggers it overrides
    all other signals (sample count, source mix, data quality)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(10)
        ],
        prompt_context_routing=_routing_blocked(),
    )
    results = extractor.extract(inputs)
    v = results[0]
    # All signals indicate canonical + sufficient + typed-only, but the
    # fail-closed gate dominates.
    assert v.value is None
    assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION in v.exclusions
    assert INSUFFICIENT_SAMPLES_EXCLUSION not in v.exclusions
    assert v.data_quality == "insufficient"


def test_combined_typed_legacy_mix_excludes_active_work() -> None:
    """Mixed typed + legacy evidence + active-work exclude -> the
    extractor reports a typed-vs-legacy source_mix + the
    ACTIVE_WORK_EXCLUDED_EXCLUSION sentinel."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="t1", authority="typed_journal"),
            _ref(ref_id="t2", authority="typed_journal"),
            _ref(ref_id="t3", authority="typed_journal"),
            _ref(ref_id="l1", authority="legacy_event"),
            _active_ref(ref_id="active-1", authority="typed_journal"),
            _active_ref(ref_id="active-2", authority="legacy_event"),
        ],
        definitions=[_definition(active_work_policy="exclude")],
        active_work_filter="exclude",
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.source_mix == {"typed": 3, "legacy": 1}
    assert v.data_quality == "derived"
    assert ACTIVE_WORK_EXCLUDED_EXCLUSION in v.exclusions


def test_combined_routing_open_consumes_evidence_normally() -> None:
    """Routing reports should_invoke_runtime=True -> the extractor consumes
    evidence normally + emits a REAL typed value (float | int) per
    doc-15:125-132 steps 4-5.

    Per the 3rd sub-slice the at-or-above-threshold emit path returns
    real numerator / denominator arithmetic (the
    ``PLACEHOLDER_ARITHMETIC_EXCLUSION`` sentinel was REMOVED). The
    :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` sentinel MUST NOT be
    present (the routing is OPEN; the fail-closed gate does NOT
    trigger).
    """

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        prompt_context_routing=_routing_open(),
    )
    results = extractor.extract(inputs)
    v = results[0]
    # Real typed value (float | int) per doc-15:82.
    assert v.value is not None
    assert isinstance(v.value, (int, float))
    assert v.value > 0
    assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION not in v.exclusions


# ── ConfigDict discipline ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "model_cls",
    [MetricExtractorInputs, GovernanceMetricExtractionGap],
)
def test_all_models_carry_extra_forbid(model_cls: type) -> None:
    """Every Pydantic BaseModel in the extractor module carries
    ``ConfigDict(extra="forbid")``."""

    config = model_cls.model_config
    assert config.get("extra") == "forbid"


# ── Slice 14 P3-V3-2 stronger pattern re-pin ───────────────────────────────


def test_slice_14_p3_v3_2_stronger_pattern_for_extractor_inputs() -> None:
    """The DIRECT annotation-identity assertion pattern from Slice 15
    1st sub-slice carries through to the 2nd sub-slice typed shapes.

    Verifies the Slice 13a EvidenceQuality identity through the imported
    surface (the extractor doesn't carry it on a direct field, but the
    GovernanceMetricValue it returns uses it -- and our test suite
    exercises that path through the integration tests above)."""

    from iriai_build_v2.workflows.develop.governance import models as gov_mod
    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    # The extractor module re-imports EvidenceQuality + GovernanceEvidenceRef
    # from the Slice 13a module; the imports are identity-equal to the
    # source-of-truth.
    assert mod.EvidenceQuality is gov_mod.EvidenceQuality
    assert mod.GovernanceEvidenceRef is gov_mod.GovernanceEvidenceRef


def test_slice_13a_evidence_completeness_identity_through_extractor() -> None:
    """The Slice 13A 2nd sub-slice EvidenceCompleteness is identity-equal
    in the extractor module."""

    from iriai_build_v2.execution_control import completeness as comp_mod
    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert mod.EvidenceCompleteness is comp_mod.EvidenceCompleteness


def test_slice_15_first_governance_metric_value_identity_through_extractor() -> None:
    """The Slice 15 1st sub-slice GovernanceMetricValue is identity-equal
    in the extractor module."""

    from iriai_build_v2.execution_control import governance_metrics as metrics_mod
    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert mod.GovernanceMetricValue is metrics_mod.GovernanceMetricValue
    assert mod.GovernanceMetricDefinition is metrics_mod.GovernanceMetricDefinition


def test_slice_13a_authoritative_prompt_context_routing_identity() -> None:
    """The Slice 13A 4th sub-slice AuthoritativePromptContextRouting is
    identity-equal in the extractor module."""

    from iriai_build_v2.execution_control import dispatcher_prompt_context as dpc_mod
    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    assert mod.AuthoritativePromptContextRouting is dpc_mod.AuthoritativePromptContextRouting


# ── value-field types (doc-15:82) ─────────────────────────────────────────


def test_extracted_value_value_field_accepts_none() -> None:
    """Per doc-15:82 + doc-15:148-150 the ``value`` field is
    ``float | int | None``; None is the insufficient-sample case."""

    extractor = MetricExtractor()
    inputs = _inputs(evidence_set_refs=[])
    results = extractor.extract(inputs)
    assert results[0].value is None


def test_extracted_value_data_quality_is_evidence_quality_literal() -> None:
    """Per doc-15:85 the ``data_quality`` field is the Slice 13a
    EvidenceQuality Literal."""

    extractor = MetricExtractor()
    inputs = _inputs()
    results = extractor.extract(inputs)
    v = results[0]
    # Verify the value is one of the 6 EvidenceQuality Literal values.
    assert v.data_quality in get_args(EvidenceQuality)


# ── unit projection helper ─────────────────────────────────────────────────


def test_unit_for_definition_per_doc_15_83() -> None:
    """The unit string is projected from the definition's denominator
    per doc-15:83."""

    extractor = MetricExtractor()
    # denominator="elapsed hours" -> tasks/hour
    inputs = _inputs(definitions=[_definition(denominator="elapsed hours")])
    results = extractor.extract(inputs)
    assert results[0].unit == "tasks/hour"
    # denominator="completed tasks" -> count/task
    inputs = _inputs(definitions=[_definition(denominator="completed tasks")])
    results = extractor.extract(inputs)
    assert results[0].unit == "count/task"


# ────────────────────────────────────────────────────────────────────────────
# 3rd sub-slice -- doc-15:125-132 steps 4-5 + AC4 doc-15:176-181 coverage
#
# Real numerator / denominator arithmetic per doc-15:125-130 step 4 +
# calibrated 5-arg confidence projection per doc-15:131-132 step 5 +
# implementation-log completeness AC4 signal per doc-15:176-181 + complexity
# adjustment from pre-execution task-shape inputs only per doc-15:127-130.
# ────────────────────────────────────────────────────────────────────────────


# ── TaskShapeInputs typed bundle (doc-15:125-130 step 4) ──────────────────


def _task_shape(**overrides: object) -> TaskShapeInputs:
    """Construct a fully-specified :class:`TaskShapeInputs` for tests."""

    base: dict[str, object] = dict(
        task_count=10,
        contract_path_breadth=3,
        repo_count=1,
        barrier_type="none",
        dependency_depth=0,
        planned_verifier_gate_count=2,
        declared_write_set_uncertainty=0.2,
    )
    base.update(overrides)
    return TaskShapeInputs(**base)  # type: ignore[arg-type]


def test_task_shape_inputs_accepts_all_required_fields() -> None:
    """The 7 doc-15:125-130 step 4 fields populate cleanly."""

    ts = _task_shape()
    assert ts.task_count == 10
    assert ts.contract_path_breadth == 3
    assert ts.repo_count == 1
    assert ts.barrier_type == "none"
    assert ts.dependency_depth == 0
    assert ts.planned_verifier_gate_count == 2
    assert ts.declared_write_set_uncertainty == 0.2


def test_task_shape_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _task_shape(unknown_field="oops")  # type: ignore[arg-type]


def test_task_shape_inputs_carries_extra_forbid_config() -> None:
    """Verify the typed-shape contract."""

    assert TaskShapeInputs.model_config.get("extra") == "forbid"


@pytest.mark.parametrize(
    "barrier_value",
    ["none", "soft", "hard"],
)
def test_task_shape_inputs_barrier_type_accepts_all_3_values(barrier_value: str) -> None:
    """Per doc-15:127 the barrier-type Literal accepts the 3 values."""

    ts = _task_shape(barrier_type=barrier_value)
    assert ts.barrier_type == barrier_value


def test_task_shape_inputs_barrier_type_rejects_unknown() -> None:
    """Unknown barrier values fail closed."""

    with pytest.raises(ValidationError):
        _task_shape(barrier_type="not_a_barrier")  # type: ignore[arg-type]


def test_task_shape_inputs_repo_count_rejects_zero() -> None:
    """``repo_count`` must be >= 1 (the corpus always has at least 1 repo)."""

    with pytest.raises(ValidationError):
        _task_shape(repo_count=0)


def test_task_shape_inputs_negative_counts_rejected() -> None:
    """Non-negative validators on count fields fail closed."""

    with pytest.raises(ValidationError):
        _task_shape(task_count=-1)
    with pytest.raises(ValidationError):
        _task_shape(contract_path_breadth=-1)
    with pytest.raises(ValidationError):
        _task_shape(dependency_depth=-1)
    with pytest.raises(ValidationError):
        _task_shape(planned_verifier_gate_count=-1)


def test_task_shape_inputs_write_set_uncertainty_bounds() -> None:
    """``declared_write_set_uncertainty`` in [0.0, 1.0]."""

    # Boundary OK.
    _task_shape(declared_write_set_uncertainty=0.0)
    _task_shape(declared_write_set_uncertainty=1.0)
    with pytest.raises(ValidationError):
        _task_shape(declared_write_set_uncertainty=-0.1)
    with pytest.raises(ValidationError):
        _task_shape(declared_write_set_uncertainty=1.1)


def test_task_shape_inputs_round_trips_via_json() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    ts = _task_shape()
    serialised = ts.model_dump_json()
    restored = TaskShapeInputs.model_validate_json(serialised)
    assert restored == ts


# ── MetricExtractorInputs new optional fields (3rd sub-slice) ──────────────


def test_inputs_task_shape_inputs_defaults_to_none() -> None:
    """The new optional ``task_shape_inputs`` field defaults to None."""

    inputs = _inputs()
    assert inputs.task_shape_inputs is None


def test_inputs_implementation_log_completeness_defaults_to_none() -> None:
    """The new optional ``implementation_log_completeness`` field defaults to None."""

    inputs = _inputs()
    assert inputs.implementation_log_completeness is None


def test_inputs_accepts_task_shape_inputs() -> None:
    """The ``task_shape_inputs`` field accepts a typed
    :class:`TaskShapeInputs`."""

    ts = _task_shape()
    inputs = _inputs(task_shape_inputs=ts)
    assert inputs.task_shape_inputs is ts


def test_inputs_accepts_implementation_log_completeness() -> None:
    """The ``implementation_log_completeness`` field accepts a typed
    :class:`EvidenceCompleteness`."""

    impl_log = _completeness(state="complete", complete_for=["impl-log"])
    inputs = _inputs(implementation_log_completeness=impl_log)
    assert inputs.implementation_log_completeness is impl_log


# ── Real arithmetic: numerator / denominator (doc-15 step 4) ───────────────


def test_arithmetic_value_is_real_float_not_none_when_above_threshold() -> None:
    """The at-or-above-threshold path emits a real ``float | int`` value
    (NOT None -- the value-None case is reserved for fail-closed +
    insufficient-sample paths)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value is not None
    assert isinstance(v.value, (int, float))


def test_arithmetic_value_for_default_definition() -> None:
    """Default definition: 5 typed_journal refs, denominator="elapsed hours",
    freshness_window_hours=168 -> value = 5 / 168."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value == pytest.approx(5.0 / 168.0)


def test_arithmetic_numerator_counts_only_required_evidence_kinds() -> None:
    """Per doc-15:74 the numerator counts refs whose authority is in
    :attr:`required_evidence_kinds`."""

    extractor = MetricExtractor()
    # Definition requires only typed_journal; 3 typed + 2 legacy refs
    # -> numerator = 3 typed (denominator = freshness 168).
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="t1", authority="typed_journal"),
            _ref(ref_id="t2", authority="typed_journal"),
            _ref(ref_id="t3", authority="typed_journal"),
            _ref(ref_id="l1", authority="legacy_event"),
            _ref(ref_id="l2", authority="legacy_event"),
        ],
        definitions=[
            _definition(required_evidence_kinds=["typed_journal"], denominator="elapsed hours"),
        ],
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value == pytest.approx(3.0 / 168.0)


def test_arithmetic_numerator_falls_back_to_all_refs_when_required_empty() -> None:
    """When ``required_evidence_kinds`` is empty the numerator falls back
    to the total retained-ref count (so the extractor still emits a typed
    value)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="t1", authority="typed_journal"),
            _ref(ref_id="l1", authority="legacy_event"),
            _ref(ref_id="t2", authority="typed_journal"),
        ],
        definitions=[
            _definition(required_evidence_kinds=[], denominator="elapsed hours"),
        ],
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    v = results[0]
    # Total retained refs = 3; freshness window = 168.
    assert v.value == pytest.approx(3.0 / 168.0)


def test_arithmetic_denominator_hour_returns_freshness_window() -> None:
    """Denominator containing "hour" -> freshness_window_hours."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(10)
        ],
        definitions=[_definition(denominator="elapsed hours")],
        freshness_window_hours=72.0,
    )
    results = extractor.extract(inputs)
    v = results[0]
    # 10 / 72 = 0.139.
    assert v.value == pytest.approx(10.0 / 72.0)


def test_arithmetic_denominator_task_counts_task_scope_refs() -> None:
    """Denominator containing "task" -> count of task-scope refs."""

    extractor = MetricExtractor()
    # 5 typed_journal refs are task-scope; the numerator + denominator both
    # apply to refs matching required_evidence_kinds.
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        definitions=[
            _definition(
                denominator="completed tasks",
                required_evidence_kinds=["typed_journal"],
            )
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    # numerator = 5; denominator = 5 task-scope refs; value = 1.0.
    assert v.value == pytest.approx(1.0)


def test_arithmetic_denominator_attempt_counts_attempt_scope_refs() -> None:
    """Denominator containing "attempt" -> count of attempt-scope refs."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(4)
        ],
        definitions=[
            _definition(
                denominator="dispatched attempts",
                required_evidence_kinds=["typed_journal"],
            ),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    # numerator = 4; denominator = 4 attempt-scope refs; value = 1.0.
    assert v.value == pytest.approx(1.0)


def test_arithmetic_denominator_default_uses_max_sample_count() -> None:
    """A denominator that does not match any class (no "hour", "task",
    or "attempt") falls back to max(retained_count, 1)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(7)
        ],
        definitions=[
            _definition(
                denominator="something_else",
                required_evidence_kinds=["typed_journal"],
            ),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    # numerator = 7 typed_journal; denominator = 7 sample count; value = 1.
    assert v.value == pytest.approx(1.0)


def test_arithmetic_division_by_zero_falls_back_to_zero_not_none() -> None:
    """When denominator is 0 (e.g. no task-scope refs but
    denominator="completed tasks"), the value falls back to 0.0 (NOT None)."""

    extractor = MetricExtractor()
    # 5 legacy_event refs -> none qualify as task-scope (task-scope
    # requires typed_journal/implementation_journal/_decision_log).
    # required_evidence_kinds covers both so numerator > 0.
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="legacy_event") for i in range(5)
        ],
        definitions=[
            _definition(
                denominator="completed tasks",
                required_evidence_kinds=["legacy_event"],
            ),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    # denominator = 0; numerator = 5; per division-by-zero rule -> 0.0.
    assert v.value == 0.0


def test_arithmetic_value_is_float_type_not_int() -> None:
    """The arithmetic always returns ``float`` (division produces float)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert isinstance(v.value, float)


# ── Complexity adjustment (doc-15:125-130 step 4) ─────────────────────────


def test_complexity_adjustment_default_unit_factor_when_task_shape_none() -> None:
    """When ``task_shape_inputs`` is None (default) the complexity factor
    is 1.0 (no adjustment); arithmetic identical to baseline."""

    extractor = MetricExtractor()
    inputs_with = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        task_shape_inputs=None,
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour")],
    )
    inputs_without = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour")],
    )
    r_with = extractor.extract(inputs_with)
    r_without = extractor.extract(inputs_without)
    assert r_with[0].value == r_without[0].value


def test_complexity_adjustment_applies_only_to_complexity_adjusted_metrics() -> None:
    """Per the per-metric semantics rule only metrics whose name starts
    with ``complexity_adjusted_`` receive the complexity factor in the
    denominator; other metrics are unaffected."""

    extractor = MetricExtractor()
    ts = _task_shape(
        task_count=10, contract_path_breadth=5, repo_count=3,
        barrier_type="hard", dependency_depth=2,
        planned_verifier_gate_count=5, declared_write_set_uncertainty=0.5,
    )
    # tasks_per_hour: NOT complexity-adjusted; baseline value.
    base_inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        task_shape_inputs=ts,
        definitions=[_definition(name="tasks_per_hour", denominator="elapsed hours")],
    )
    base_results = extractor.extract(base_inputs)
    # Expected: 5 / 168 (no complexity adjustment).
    assert base_results[0].value == pytest.approx(5.0 / 168.0)


def test_complexity_adjustment_changes_complexity_adjusted_value() -> None:
    """For ``complexity_adjusted_tasks_per_hour`` a non-trivial task-shape
    bundle changes the value compared to the unit-factor baseline."""

    extractor = MetricExtractor()
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)]
    # Baseline (None task_shape -> unit factor).
    baseline_inputs = _inputs(
        evidence_set_refs=refs,
        task_shape_inputs=None,
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour", denominator="elapsed hours")],
    )
    baseline_results = extractor.extract(baseline_inputs)
    baseline_value = baseline_results[0].value
    # Higher complexity (hard barrier, dependency_depth, uncertainty).
    high_complexity = _task_shape(
        task_count=10, contract_path_breadth=5, repo_count=3,
        barrier_type="hard", dependency_depth=5,
        planned_verifier_gate_count=10, declared_write_set_uncertainty=1.0,
    )
    high_inputs = _inputs(
        evidence_set_refs=refs,
        task_shape_inputs=high_complexity,
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour", denominator="elapsed hours")],
    )
    high_results = extractor.extract(high_inputs)
    # Complexity adjustment increases denominator -> reduces value.
    assert high_results[0].value < baseline_value


def test_complexity_adjustment_barrier_hard_higher_than_soft() -> None:
    """Hard barrier -> higher complexity factor -> lower complexity-adjusted
    value than soft barrier."""

    extractor = MetricExtractor()
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)]
    base = dict(
        task_count=0, contract_path_breadth=0, repo_count=1,
        dependency_depth=0, planned_verifier_gate_count=0,
        declared_write_set_uncertainty=0.0,
    )
    soft_inputs = _inputs(
        evidence_set_refs=refs,
        task_shape_inputs=TaskShapeInputs(**base, barrier_type="soft"),
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour", denominator="elapsed hours")],
    )
    hard_inputs = _inputs(
        evidence_set_refs=refs,
        task_shape_inputs=TaskShapeInputs(**base, barrier_type="hard"),
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour", denominator="elapsed hours")],
    )
    soft_r = extractor.extract(soft_inputs)
    hard_r = extractor.extract(hard_inputs)
    assert hard_r[0].value < soft_r[0].value


def test_complexity_adjustment_repo_count_increases_complexity() -> None:
    """Per doc-15:126 more repos -> higher complexity factor -> lower
    complexity-adjusted value."""

    extractor = MetricExtractor()
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)]
    base = dict(
        task_count=0, contract_path_breadth=0,
        barrier_type="none", dependency_depth=0,
        planned_verifier_gate_count=0,
        declared_write_set_uncertainty=0.0,
    )
    one_repo_inputs = _inputs(
        evidence_set_refs=refs,
        task_shape_inputs=TaskShapeInputs(**base, repo_count=1),
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour", denominator="elapsed hours")],
    )
    many_repo_inputs = _inputs(
        evidence_set_refs=refs,
        task_shape_inputs=TaskShapeInputs(**base, repo_count=5),
        definitions=[_definition(name="complexity_adjusted_tasks_per_hour", denominator="elapsed hours")],
    )
    one_r = extractor.extract(one_repo_inputs)
    many_r = extractor.extract(many_repo_inputs)
    assert many_r[0].value < one_r[0].value


def test_complexity_adjustment_does_not_use_observed_failure_classes() -> None:
    """Per doc-15:127-130 the TaskShapeInputs typed surface does NOT
    carry observed failure classes (stale projection, commit hygiene,
    provider instability, queue drag); those remain workflow-drag
    metrics."""

    field_names = set(TaskShapeInputs.model_fields)
    # The 7 doc-15:125-130 step 4 axes — and ONLY those.
    expected = {
        "task_count",
        "contract_path_breadth",
        "repo_count",
        "barrier_type",
        "dependency_depth",
        "planned_verifier_gate_count",
        "declared_write_set_uncertainty",
    }
    assert field_names == expected
    # Defensive: forbidden observed-failure fields are NOT present.
    assert "stale_projection_count" not in field_names
    assert "commit_hygiene_failures" not in field_names
    assert "provider_instability_rate" not in field_names
    assert "queue_drag_hours" not in field_names


# ── Calibrated confidence (doc-15:131-132 step 5 + AC4) ────────────────────


def test_confidence_calibrated_canonical_at_sample_count_cap_is_one() -> None:
    """30+ typed_journal refs + canonical quality + complete completeness
    + fresh + impl_log=None -> all 5 contributions are 1.0 -> composite 1.0."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)
        ],
        completeness_state=_completeness(state="complete"),
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    assert results[0].confidence == pytest.approx(1.0)


def test_confidence_calibrated_caps_at_one() -> None:
    """100 refs (above the sample-count cap of 30) saturates at 1.0."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(100)
        ],
    )
    results = extractor.extract(inputs)
    assert results[0].confidence == pytest.approx(1.0)


def test_confidence_calibrated_zero_when_completeness_unavailable() -> None:
    """state="unavailable" -> completeness_factor=0.0 -> composite=0.0
    (geometric mean: any zero drops the composite to zero)."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(10)
        ],
        completeness_state=_completeness(state="unavailable", unavailable_reason="r"),
    )
    results = extractor.extract(inputs)
    assert results[0].confidence == 0.0


def test_confidence_calibrated_zero_when_impl_log_unavailable_per_ac4() -> None:
    """Per AC4 doc-15:181: implementation-log completeness=unavailable ->
    impl_log_factor=0.0 -> composite=0.0 (the geometric mean penalty
    is the doc-15:157-158 incomplete-journal rule)."""

    extractor = MetricExtractor()
    impl_log = _completeness(
        state="unavailable",
        complete_for=[],
        unavailable_reason="journal incomplete",
    )
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)
        ],
        implementation_log_completeness=impl_log,
    )
    results = extractor.extract(inputs)
    assert results[0].confidence == 0.0


def test_confidence_calibrated_lower_when_impl_log_preview_only_per_ac4() -> None:
    """Per AC4 doc-15:181: implementation-log completeness=preview_only ->
    impl_log_factor=0.3 -> composite reduces vs the baseline (1.0)."""

    extractor = MetricExtractor()
    impl_log = _completeness(state="preview_only", complete_for=[])
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)]
    high_inputs = _inputs(
        evidence_set_refs=refs,
        implementation_log_completeness=None,  # impl_log_factor = 1.0.
    )
    low_inputs = _inputs(
        evidence_set_refs=refs,
        implementation_log_completeness=impl_log,  # impl_log_factor = 0.3.
    )
    high_results = extractor.extract(high_inputs)
    low_results = extractor.extract(low_inputs)
    assert low_results[0].confidence < high_results[0].confidence


def test_confidence_calibrated_lower_when_impl_log_paged_per_ac4() -> None:
    """Per AC4: implementation-log state=paged -> impl_log_factor=0.7 ->
    composite reduces vs the baseline (1.0)."""

    extractor = MetricExtractor()
    impl_log = _completeness(state="paged", complete_for=["impl-log"])
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)]
    high_inputs = _inputs(
        evidence_set_refs=refs,
        implementation_log_completeness=None,
    )
    low_inputs = _inputs(
        evidence_set_refs=refs,
        implementation_log_completeness=impl_log,
    )
    high_results = extractor.extract(high_inputs)
    low_results = extractor.extract(low_inputs)
    assert low_results[0].confidence < high_results[0].confidence


def test_confidence_calibrated_impl_log_complete_does_not_penalise() -> None:
    """Per AC4: implementation-log state=complete -> impl_log_factor=1.0
    -> composite unchanged vs the None default (also 1.0)."""

    extractor = MetricExtractor()
    impl_log = _completeness(state="complete", complete_for=["impl-log"])
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)]
    none_inputs = _inputs(
        evidence_set_refs=refs,
        implementation_log_completeness=None,
    )
    complete_inputs = _inputs(
        evidence_set_refs=refs,
        implementation_log_completeness=impl_log,
    )
    none_r = extractor.extract(none_inputs)
    complete_r = extractor.extract(complete_inputs)
    assert none_r[0].confidence == pytest.approx(complete_r[0].confidence)


def test_confidence_calibrated_lower_when_completeness_paged() -> None:
    """completeness=paged -> completeness_factor=0.7 -> composite reduces."""

    extractor = MetricExtractor()
    refs = [_ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)]
    complete_inputs = _inputs(
        evidence_set_refs=refs,
        completeness_state=_completeness(state="complete"),
    )
    paged_inputs = _inputs(
        evidence_set_refs=refs,
        completeness_state=_completeness(state="paged"),
    )
    complete_r = extractor.extract(complete_inputs)
    paged_r = extractor.extract(paged_inputs)
    assert paged_r[0].confidence < complete_r[0].confidence


def test_confidence_calibrated_freshness_axis_all_fresh_is_full() -> None:
    """All refs fresh (within window) -> freshness_factor=1.0."""

    extractor = MetricExtractor()
    fresh = datetime.now(timezone.utc) - timedelta(hours=1)
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal", created_at=fresh)
            for i in range(30)
        ],
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    # All 5 contributions at full -> composite=1.0.
    assert results[0].confidence == pytest.approx(1.0)


def test_confidence_calibrated_freshness_axis_all_stale_within_2x_is_half() -> None:
    """All refs stale but within 2x window -> freshness_factor=0.5
    (data_quality is also "stale" because all typed refs are stale)."""

    extractor = MetricExtractor()
    stale = datetime.now(timezone.utc) - timedelta(hours=200)
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal", created_at=stale)
            for i in range(30)
        ],
        freshness_window_hours=168.0,
    )
    results = extractor.extract(inputs)
    # data_quality=stale (factor 0.2) + freshness_factor=0.5 +
    # completeness=1.0 + sample=1.0 + impl_log=1.0.
    # Geometric mean = (1*1*0.5*0.2*1)^(1/5) = 0.1^0.2 ≈ 0.631.
    assert 0.0 < results[0].confidence < 1.0


def test_confidence_calibrated_freshness_axis_very_stale_is_lowest() -> None:
    """All refs >2x stale -> freshness_factor=0.2 (lower than 0.5)."""

    extractor = MetricExtractor()
    very_stale = datetime.now(timezone.utc) - timedelta(hours=500)
    inputs_very_stale = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal", created_at=very_stale)
            for i in range(30)
        ],
        freshness_window_hours=168.0,
    )
    stale_within_2x = datetime.now(timezone.utc) - timedelta(hours=200)
    inputs_stale = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal", created_at=stale_within_2x)
            for i in range(30)
        ],
        freshness_window_hours=168.0,
    )
    very_stale_r = extractor.extract(inputs_very_stale)
    stale_r = extractor.extract(inputs_stale)
    # freshness_factor very_stale=0.2 vs stale=0.5 -> very_stale < stale.
    assert very_stale_r[0].confidence < stale_r[0].confidence


def test_confidence_calibrated_sample_count_ramp_below_cap() -> None:
    """Sample-count linear ramp between threshold and cap."""

    extractor = MetricExtractor()
    # At threshold (3) -> sample_count_factor=0.0 -> composite=0.0.
    inputs_threshold = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(3)
        ],
    )
    # At cap (30) -> sample_count_factor=1.0 -> composite=1.0.
    inputs_cap = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)
        ],
    )
    # Mid-range (10) -> partial ramp.
    inputs_mid = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(10)
        ],
    )
    r_threshold = extractor.extract(inputs_threshold)
    r_mid = extractor.extract(inputs_mid)
    r_cap = extractor.extract(inputs_cap)
    # Monotonic ramp: threshold < mid < cap.
    assert r_threshold[0].confidence < r_mid[0].confidence
    assert r_mid[0].confidence < r_cap[0].confidence


def test_confidence_calibrated_5_input_signature() -> None:
    """Sanity: the calibrated confidence accepts 5 inputs (Evidence
    Completeness + sample_count + freshness_window_hours + data_quality
    + implementation_log_completeness) per doc-15:131-132 step 5 + AC4."""

    # Direct call to the helper for the typed-signature assertion.
    from iriai_build_v2.execution_control.governance_metric_extractor import (
        _compute_confidence,
    )
    import inspect

    sig = inspect.signature(_compute_confidence)
    # The helper accepts exactly the 5 calibrated inputs + retained_refs
    # (the 6th carries the typed Slice 13a refs so the freshness
    # projection has the per-ref data it needs).
    params = set(sig.parameters)
    assert "completeness" in params
    assert "sample_count" in params
    assert "freshness_window_hours" in params
    assert "data_quality" in params
    assert "implementation_log_completeness" in params


def test_confidence_clipped_to_zero_one_range() -> None:
    """Per doc-15:84 the confidence is in [0.0, 1.0]."""

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
    )
    results = extractor.extract(inputs)
    assert 0.0 <= results[0].confidence <= 1.0


def test_confidence_geometric_mean_zero_propagates() -> None:
    """Any single zero contribution drops the composite to zero (geometric
    mean property)."""

    extractor = MetricExtractor()
    # impl_log unavailable -> impl_log_factor=0.0 -> composite=0.0.
    impl_log = _completeness(state="unavailable", complete_for=[], unavailable_reason="r")
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)
        ],
        completeness_state=_completeness(state="complete"),
        implementation_log_completeness=impl_log,
    )
    results = extractor.extract(inputs)
    assert results[0].confidence == 0.0


# ── Fail-closed gate PRESERVATION (doc-13a:269-272) ────────────────────────


def test_fail_closed_gate_still_emits_value_none_after_3rd_sub_slice() -> None:
    """Per the 3rd sub-slice scope: the doc-13a:269-272 fail-closed
    gate continues to emit ``value=None`` + the
    :data:`PROMPT_CONTEXT_INCOMPLETE_EXCLUSION` sentinel — that's the
    LEGITIMATE fail-closed semantics, DIFFERENT from the (removed) 2nd-
    sub-slice placeholder arithmetic.

    The 3rd sub-slice removes ONLY the arithmetic placeholder, NOT the
    fail-closed ``value=None``.
    """

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(30)
        ],
        prompt_context_routing=_routing_blocked(),
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value is None
    assert PROMPT_CONTEXT_INCOMPLETE_EXCLUSION in v.exclusions
    assert v.data_quality == "insufficient"
    assert v.confidence == 0.0


def test_insufficient_sample_path_still_emits_value_none_after_3rd_sub_slice() -> None:
    """Per the 3rd sub-slice scope: the doc-15:148-150 insufficient-sample
    path continues to emit ``value=None`` + the
    :data:`INSUFFICIENT_SAMPLES_EXCLUSION` sentinel.
    """

    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id="r1", authority="typed_journal"),
        ],
    )
    results = extractor.extract(inputs)
    v = results[0]
    assert v.value is None
    assert INSUFFICIENT_SAMPLES_EXCLUSION in v.exclusions


# ── Sentinel REMOVED assertions (3rd sub-slice scope) ──────────────────────


def test_placeholder_arithmetic_exclusion_sentinel_fully_removed_from_module() -> None:
    """The :data:`PLACEHOLDER_ARITHMETIC_EXCLUSION` sentinel was REMOVED
    from BOTH the module-level constants AND the ``__all__`` per the
    3rd sub-slice scope."""

    from iriai_build_v2.execution_control import governance_metric_extractor as mod

    # Not in __all__.
    assert "PLACEHOLDER_ARITHMETIC_EXCLUSION" not in set(mod.__all__)
    # Not a module-level constant.
    assert not hasattr(mod, "PLACEHOLDER_ARITHMETIC_EXCLUSION")


def test_placeholder_arithmetic_exclusion_sentinel_not_emitted_on_any_path() -> None:
    """The :data:`PLACEHOLDER_ARITHMETIC_EXCLUSION` sentinel string is
    NOT emitted on any path post-3rd-sub-slice."""

    sentinel = "placeholder_arithmetic_pending_3rd_sub_slice"

    extractor = MetricExtractor()
    # at-or-above threshold path
    above = extractor.extract(
        _inputs(
            evidence_set_refs=[
                _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
            ],
        )
    )
    assert sentinel not in above[0].exclusions
    # below-threshold path
    below = extractor.extract(
        _inputs(
            evidence_set_refs=[_ref(ref_id="r1", authority="typed_journal")],
        )
    )
    assert sentinel not in below[0].exclusions
    # fail-closed path
    fc = extractor.extract(
        _inputs(
            evidence_set_refs=[
                _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
            ],
            prompt_context_routing=_routing_blocked(),
        )
    )
    assert sentinel not in fc[0].exclusions


# ── Multi-metric integration: 15 REQUIRED_V1_METRIC_NAMES ──────────────────


def test_extractor_handles_all_15_required_v1_metric_names() -> None:
    """The extractor projects each of the 15 doc-15:101-115 v1 metric
    names onto a typed GovernanceMetricValue with real arithmetic."""

    from iriai_build_v2.execution_control.governance_metrics import (
        REQUIRED_V1_METRIC_NAMES,
    )

    # Build a definition per required name; pick reasonable
    # numerators/denominators per metric class.
    name_to_denom = {
        "tasks_per_hour": "elapsed hours",
        "complexity_adjusted_tasks_per_hour": "elapsed hours",
        "hours_per_task": "completed tasks",
        "repair_cycles_per_task": "completed tasks",
        "verification_cost_per_task": "completed tasks",
        "commit_failures_per_task": "completed tasks",
        "stale_context_events_per_task": "completed tasks",
        "workspace_unblocks_per_task": "completed tasks",
        "runtime_failures_per_attempt": "dispatched attempts",
        "merge_queue_wait_hours": "elapsed hours",
        "checkpoint_duration_hours": "elapsed hours",
        "workflow_drag_hours": "elapsed hours",
        "operator_required_escalations": "count",
        "plan_deviation_count": "count",
        "resolved_p1_p2_review_findings": "count",
    }
    definitions = [
        _definition(name=name, denominator=name_to_denom[name])
        for name in REQUIRED_V1_METRIC_NAMES
    ]
    extractor = MetricExtractor()
    inputs = _inputs(
        evidence_set_refs=[
            _ref(ref_id=f"r{i}", authority="typed_journal") for i in range(5)
        ],
        definitions=definitions,
    )
    results = extractor.extract(inputs)
    # One value per definition (15 total).
    assert len(results) == 15
    for v in results:
        # Every emitted value carries a typed float | int per doc-15:82.
        assert v.value is not None
        assert isinstance(v.value, (int, float))


# ── Helper-function direct tests (doc-15:125-132) ──────────────────────────


def test_compute_numerator_returns_zero_for_no_refs() -> None:
    """Direct helper call: empty retained_refs -> numerator=0.0."""

    from iriai_build_v2.execution_control.governance_metric_extractor import (
        _compute_numerator,
    )

    definition = _definition(required_evidence_kinds=["typed_journal"])
    result = _compute_numerator(
        definition=definition,
        retained_refs=[],
        freshness_window_hours=168.0,
    )
    assert result == 0.0


def test_compute_denominator_returns_freshness_window_for_hours() -> None:
    """Direct helper call: denominator with "hour" -> freshness window."""

    from iriai_build_v2.execution_control.governance_metric_extractor import (
        _compute_denominator,
    )

    definition = _definition(denominator="elapsed hours")
    result = _compute_denominator(
        definition=definition,
        retained_refs=[],
        freshness_window_hours=72.0,
    )
    assert result == 72.0


def test_compute_complexity_adjustment_default_unit_when_none() -> None:
    """Direct helper call: ``task_shape_inputs=None`` -> 1.0 unit factor."""

    from iriai_build_v2.execution_control.governance_metric_extractor import (
        _compute_complexity_adjustment,
    )

    definition = _definition(name="complexity_adjusted_tasks_per_hour")
    result = _compute_complexity_adjustment(
        definition=definition,
        task_shape_inputs=None,
    )
    assert result == 1.0


def test_compute_complexity_adjustment_higher_for_more_complex_corpus() -> None:
    """Direct helper call: a high-complexity bundle returns a factor > 1.0."""

    from iriai_build_v2.execution_control.governance_metric_extractor import (
        _compute_complexity_adjustment,
    )

    definition = _definition(name="complexity_adjusted_tasks_per_hour")
    low_complexity = _task_shape(
        task_count=0, contract_path_breadth=0, repo_count=1,
        barrier_type="none", dependency_depth=0,
        planned_verifier_gate_count=0, declared_write_set_uncertainty=0.0,
    )
    high_complexity = _task_shape(
        task_count=20, contract_path_breadth=10, repo_count=5,
        barrier_type="hard", dependency_depth=5,
        planned_verifier_gate_count=10, declared_write_set_uncertainty=1.0,
    )
    low = _compute_complexity_adjustment(
        definition=definition, task_shape_inputs=low_complexity
    )
    high = _compute_complexity_adjustment(
        definition=definition, task_shape_inputs=high_complexity
    )
    assert low == pytest.approx(1.0)
    assert high > low
    assert high > 1.0
