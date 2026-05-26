"""Slice 18 second sub-slice -- unit tests for the replay corpus loader
+ scenario definition builder at
``execution_control/counterfactual_replay_loader.py``.

Covers the doc-18:111 step 1 + doc-18:112 step 2 typed-shape consumer +
structural projection wiring:

* :class:`ReplayCorpusLoaderInputs` typed inputs BaseModel (extra-
  forbid; bounded-input defaults; typed Slice 13a evidence-ref REUSE;
  Slice 18 1st sub-slice :data:`ReplayMode` REUSE).
* :class:`ReplayCorpusLoaderResult` typed result BaseModel (extra-
  forbid; corpus / gap_findings / idempotency_key).
* :class:`ReplayCorpusLoaderGap` typed gap projection BaseModel
  (extra-forbid; failure_id Literal range).
* :class:`ReplayCorpusLoader.load(...)` -- the projection method:
  * Happy-path -> typed :class:`ReplayCorpus` emitted with refs-only
    projection per doc-18:186-249.
  * Bounded-input check -> typed gap on
    ``evidence_set_refs_exceeded_bound`` /
    ``implementation_anchor_refs_exceeded_bound`` per doc-18:150.
  * Empty-corpus_id / empty-feature_ids -> typed gap per
    ``feedback_no_silent_degradation``.
  * Loader NEVER raises on input (fail-closed; typed gap projection
    on construction failure).
* :class:`ScenarioDefinitionInputs` typed inputs BaseModel (extra-
  forbid; typed Slice 17 1st sub-slice :data:`PolicyConsumer` REUSE
  via :attr:`affected_consumers`; typed Slice 18 1st sub-slice
  :class:`ReplayCorpus` REUSE via :attr:`corpus`).
* :class:`ScenarioDefinitionResult` typed result BaseModel (extra-
  forbid; scenario / validation_invalidated_by / gap_findings /
  idempotency_key).
* :class:`ScenarioDefinitionGap` typed gap projection BaseModel
  (extra-forbid; failure_id Literal range; SAME id as loader gap).
* :class:`ScenarioDefinitionBuilder.build(...)` -- the build method:
  * Happy-path -> typed :class:`CounterfactualScenario` emitted with
    typed validity_limits + assumptions.
  * Missing required evidence -> typed scenario STILL emitted +
    populated ``validation_invalidated_by`` per doc-18:134-135.
  * Empty-scenario_id / empty-affected_consumers -> typed gap.
  * Builder NEVER raises on input.
* DIRECT annotation-identity REUSE assertions for Slice 13a
  :class:`GovernanceEvidenceRef` + Slice 18 1st sub-slice
  :class:`ReplayCorpus` / :class:`CounterfactualScenario` /
  :data:`ReplayMode` + Slice 17 1st sub-slice :data:`PolicyConsumer`
  (via the scenario's :attr:`affected_consumers`).
* Failure-router 4-add-point validation
  (``replay_corpus_or_scenario_load_failed`` registered under
  EXISTING ``evidence_corruption`` failure_class with REUSED
  ``retry_governance_projection`` NON-blocking RouteAction; mirrors
  Slice 17 2nd/3rd/4th/5th/6th sub-slice precedent).
* :func:`compute_corpus_loader_idempotency_key` +
  :func:`compute_scenario_idempotency_key` -- the deterministic
  SHA-256-derived idempotency-key helpers (mirror the Slice 18 1st
  sub-slice
  :func:`compute_counterfactual_idempotency_key` discipline verbatim).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
Pydantic field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 + Slice 18 1st sub-slice modules + tests remain
byte-identical.

**Slice 13A awareness asserted (doc-18:186-249).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition. The loader + builder consume Slice 13a typed
``GovernanceEvidenceRef`` directly and emit the refs-only projection
onto the typed Slice 18 1st sub-slice ``ReplayCorpus`` shape; no raw
artifact body hydration per doc-18:186-249.

**Doc-18:123-129 persistence + artifact compatibility awareness.** No
2nd-sub-slice consumer activation wiring; no consumer-owned artifact-
key literals; replay results are review/governance artifacts only,
never runtime policy authority.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualScenario,
    ReplayCorpus,
    ReplayMode,
)
from iriai_build_v2.execution_control.counterfactual_replay_loader import (
    DEFAULT_MAX_EVIDENCE_SET_REFS,
    DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS,
    REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
    ReplayCorpusLoader,
    ReplayCorpusLoaderGap,
    ReplayCorpusLoaderInputs,
    ReplayCorpusLoaderResult,
    ScenarioDefinitionBuilder,
    ScenarioDefinitionGap,
    ScenarioDefinitionInputs,
    ScenarioDefinitionResult,
    compute_corpus_loader_idempotency_key,
    compute_scenario_idempotency_key,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    PolicyConsumer,
)
from iriai_build_v2.workflows.develop.execution.failure_router import (
    FAILURE_TYPES,
    ROUTE_TABLE,
    FailureType,
    _RETRYABLE_FAILURE_TYPES,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── fixtures ────────────────────────────────────────────────────────────────


def _ref(ref_id: str = "ref-1", **overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified :class:`GovernanceEvidenceRef` for tests."""

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id=ref_id,
        digest="d" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def _corpus_inputs(**overrides: object) -> ReplayCorpusLoaderInputs:
    base: dict[str, object] = dict(
        corpus_id="corpus-1",
        feature_ids=["8ac124d6"],
        evidence_set_refs=[_ref("ev-1")],
        implementation_anchor_refs=[_ref("anchor-1")],
        mode="summary_replay",
        validity_limits=["sample_size<10"],
    )
    base.update(overrides)
    return ReplayCorpusLoaderInputs(**base)  # type: ignore[arg-type]


def _corpus(**overrides: object) -> ReplayCorpus:
    base: dict[str, object] = dict(
        corpus_id="corpus-1",
        feature_ids=["8ac124d6"],
        evidence_set_ids=["ev-1"],
        implementation_anchor_ids=["anchor-1"],
        mode="summary_replay",
        validity_limits=[],
    )
    base.update(overrides)
    return ReplayCorpus(**base)  # type: ignore[arg-type]


def _scenario_inputs(**overrides: object) -> ScenarioDefinitionInputs:
    base: dict[str, object] = dict(
        scenario_id="scenario-1",
        policy_under_test={"policy_kind": "wave_cap", "value": {"wave_cap": 7}},
        baseline_policy_refs=["baseline-policy-1"],
        affected_consumers=["scheduler"],
        required_evidence_kinds=["ev-1"],
        assumptions=["product_defect_independent_of_wave_size"],
        validity_limits=[],
        corpus=_corpus(),
    )
    base.update(overrides)
    return ScenarioDefinitionInputs(**base)  # type: ignore[arg-type]


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_exports_count() -> None:
    from iriai_build_v2.execution_control import counterfactual_replay_loader as mod

    assert len(mod.__all__) == 13, (
        f"Expected 13 __all__ exports, got {len(mod.__all__)}: {mod.__all__}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID",
        "DEFAULT_MAX_EVIDENCE_SET_REFS",
        "DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS",
        "ReplayCorpusLoaderInputs",
        "ReplayCorpusLoaderResult",
        "ReplayCorpusLoaderGap",
        "ScenarioDefinitionInputs",
        "ScenarioDefinitionResult",
        "ScenarioDefinitionGap",
        "ReplayCorpusLoader",
        "ScenarioDefinitionBuilder",
        "compute_corpus_loader_idempotency_key",
        "compute_scenario_idempotency_key",
    ],
)
def test_module_surface_hasattr(name: str) -> None:
    from iriai_build_v2.execution_control import counterfactual_replay_loader as mod

    assert hasattr(mod, name), f"counterfactual_replay_loader missing {name!r}"


def test_no_re_export_from_execution_control_init() -> None:
    """Per Slice 13A/14/15/16/17/18-1st precedent, this module is NOT
    re-exported from ``execution_control/__init__.py``."""

    import iriai_build_v2.execution_control as init_module

    source = init_module.__file__
    if source is None:
        pytest.skip("execution_control/__init__.py has no __file__")
    with open(source) as f:
        body = f.read()
    assert "counterfactual_replay_loader" not in body


# ── Slice 13A / Slice 17 / Slice 18 1st sub-slice no-redefinition ────────


@pytest.mark.parametrize(
    "shape_name",
    [
        "CompletenessState",
        "EvidenceCompleteness",
        "AuthoritativeContextRef",
        "EvidencePageRef",
        "ExactEvidenceManifest",
        "AuthoritativePromptContextRouting",
        "AuthoritativeGateCompanionRecord",
        "AuthoritativeGateProofRow",
        "AuthoritativeSnapshotListFieldCompleteness",
        "AuthoritativeSnapshotClassifierRouting",
        # Slice 13a shared shapes
        "GovernanceEvidenceRef",
        "GovernanceEvidenceSet",
        # Slice 17 1st sub-slice
        "PolicyConsumer",
        "GovernancePolicyRecommendation",
        # Slice 18 1st sub-slice
        "ReplayCorpus",
        "CounterfactualScenario",
        "CounterfactualResult",
        "ReplayMode",
        "RiskChange",
        "RecommendedNextStep",
    ],
)
def test_no_local_redefinition(shape_name: str) -> None:
    """The Slice 18 2nd sub-slice module MUST NOT redefine any of the
    prior typed shapes. The shape MAY appear in the module namespace
    via direct import; this test asserts the IMPORTED shape's
    `__module__` is NOT this 2nd sub-slice module's."""

    from iriai_build_v2.execution_control import counterfactual_replay_loader as mod

    if hasattr(mod, shape_name):
        shape = getattr(mod, shape_name)
        if hasattr(shape, "__module__"):
            assert shape.__module__ != mod.__name__, (
                f"Slice 18 2nd sub-slice REDEFINES {shape_name!r} (must REUSE)"
            )


# ── ReplayCorpusLoaderInputs ──────────────────────────────────────────────


def test_replay_corpus_loader_inputs_construction_round_trip() -> None:
    inputs = _corpus_inputs()
    assert inputs.corpus_id == "corpus-1"
    assert inputs.feature_ids == ["8ac124d6"]
    assert len(inputs.evidence_set_refs) == 1
    assert inputs.evidence_set_refs[0].ref_id == "ev-1"
    assert inputs.mode == "summary_replay"


def test_replay_corpus_loader_inputs_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReplayCorpusLoaderInputs(  # type: ignore[call-arg]
            corpus_id="corpus-1",
            feature_ids=["8ac124d6"],
            evidence_set_refs=[],
            implementation_anchor_refs=[],
            mode="summary_replay",
            validity_limits=[],
            unknown_field="rejected",
        )


def test_replay_corpus_loader_inputs_defaults() -> None:
    inputs = ReplayCorpusLoaderInputs(
        corpus_id="corpus-1",
        feature_ids=["8ac124d6"],
        evidence_set_refs=[],
        implementation_anchor_refs=[],
        mode="summary_replay",
    )
    assert inputs.validity_limits == []
    assert inputs.max_evidence_set_refs == DEFAULT_MAX_EVIDENCE_SET_REFS
    assert inputs.max_implementation_anchor_refs == DEFAULT_MAX_IMPLEMENTATION_ANCHOR_REFS


def test_replay_corpus_loader_inputs_mode_annotation_is_replay_mode() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :data:`ReplayMode`."""

    hints = get_type_hints(ReplayCorpusLoaderInputs)
    annotation = hints["mode"]
    # ReplayMode is a Literal alias; the annotation identity should match.
    assert annotation is ReplayMode


def test_replay_corpus_loader_inputs_max_bounds_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ReplayCorpusLoaderInputs(
            corpus_id="corpus-1",
            feature_ids=["8ac124d6"],
            evidence_set_refs=[],
            implementation_anchor_refs=[],
            mode="summary_replay",
            max_evidence_set_refs=0,
        )


def test_replay_corpus_loader_inputs_evidence_set_refs_uses_governance_evidence_ref() -> None:
    """DIRECT annotation-identity REUSE for Slice 13a
    :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(ReplayCorpusLoaderInputs)
    annotation = hints["evidence_set_refs"]
    # list[GovernanceEvidenceRef] -> args is (GovernanceEvidenceRef,).
    args = get_args(annotation)
    assert args == (GovernanceEvidenceRef,)


def test_replay_corpus_loader_inputs_implementation_anchor_refs_uses_governance_evidence_ref() -> None:
    """DIRECT annotation-identity REUSE for Slice 13a
    :class:`GovernanceEvidenceRef`."""

    hints = get_type_hints(ReplayCorpusLoaderInputs)
    annotation = hints["implementation_anchor_refs"]
    args = get_args(annotation)
    assert args == (GovernanceEvidenceRef,)


# ── ReplayCorpusLoaderResult ──────────────────────────────────────────────


def test_replay_corpus_loader_result_construction_with_corpus() -> None:
    result = ReplayCorpusLoaderResult(
        corpus=_corpus(),
        gap_findings=[],
        idempotency_key="abc123",
    )
    assert result.corpus is not None
    assert result.corpus.corpus_id == "corpus-1"
    assert result.gap_findings == []
    assert result.idempotency_key == "abc123"


def test_replay_corpus_loader_result_construction_without_corpus() -> None:
    result = ReplayCorpusLoaderResult(corpus=None, gap_findings=[], idempotency_key="x")
    assert result.corpus is None


def test_replay_corpus_loader_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReplayCorpusLoaderResult(  # type: ignore[call-arg]
            corpus=None,
            gap_findings=[],
            idempotency_key="x",
            unknown_field="rejected",
        )


def test_replay_corpus_loader_result_corpus_annotation_uses_replay_corpus() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`ReplayCorpus`."""

    hints = get_type_hints(ReplayCorpusLoaderResult)
    annotation = hints["corpus"]
    # ReplayCorpus | None -> the union should contain ReplayCorpus.
    args = get_args(annotation)
    assert ReplayCorpus in args


# ── ReplayCorpusLoaderGap ─────────────────────────────────────────────────


def test_replay_corpus_loader_gap_construction_round_trip() -> None:
    gap = ReplayCorpusLoaderGap(
        failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
        corpus_id="corpus-1",
        reason="evidence_set_refs_exceeded_bound",
        observed_at=datetime.now(timezone.utc),
        evidence_refs=["ev-1"],
        evidence_payload={"received_count": 300},
    )
    assert gap.failure_id == "replay_corpus_or_scenario_load_failed"
    assert gap.corpus_id == "corpus-1"
    assert gap.evidence_refs == ["ev-1"]
    assert gap.evidence_payload == {"received_count": 300}


def test_replay_corpus_loader_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ReplayCorpusLoaderGap(  # type: ignore[call-arg]
            failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
            corpus_id="corpus-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
            unknown_field="rejected",
        )


def test_replay_corpus_loader_gap_failure_id_literal_range_rejects_unknown() -> None:
    with pytest.raises(ValidationError):
        ReplayCorpusLoaderGap(  # type: ignore[arg-type]
            failure_id="other_failure",
            corpus_id="corpus-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
        )


# ── ReplayCorpusLoader happy-path ─────────────────────────────────────────


def test_replay_corpus_loader_load_happy_path() -> None:
    """Doc-18:111 step 1 happy-path: typed evidence-set refs + Slice 00
    feature_ids -> typed :class:`ReplayCorpus` constructed correctly."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(
        evidence_set_refs=[_ref("ev-1"), _ref("ev-2")],
        implementation_anchor_refs=[_ref("anchor-1"), _ref("anchor-2")],
    )
    result = loader.load(inputs)
    assert result.corpus is not None, f"Expected corpus, got gap: {result.gap_findings}"
    assert result.corpus.corpus_id == "corpus-1"
    assert result.corpus.feature_ids == ["8ac124d6"]
    assert result.corpus.evidence_set_ids == ["ev-1", "ev-2"]
    assert result.corpus.implementation_anchor_ids == ["anchor-1", "anchor-2"]
    assert result.corpus.mode == "summary_replay"
    assert result.corpus.validity_limits == ["sample_size<10"]
    assert result.gap_findings == []
    assert len(result.idempotency_key) == 64  # SHA-256 hex


def test_replay_corpus_loader_load_idempotency_key_deterministic() -> None:
    """Doc-18:127-129 + AC1 -- the idempotency key is deterministic per
    inputs."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs()
    r1 = loader.load(inputs)
    r2 = loader.load(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_replay_corpus_loader_load_refs_only_projection() -> None:
    """Doc-18:186-249 + Slice 13A invariant: loader extracts only the
    typed :attr:`GovernanceEvidenceRef.ref_id` strings; NO raw artifact
    body hydration. The typed Slice 18 1st sub-slice
    :class:`ReplayCorpus.evidence_set_ids` field is documented as
    ``list[str]`` per doc-18:66."""

    loader = ReplayCorpusLoader()
    ref = _ref(ref_id="ev-XXX", digest="0" * 64)
    inputs = _corpus_inputs(evidence_set_refs=[ref])
    result = loader.load(inputs)
    assert result.corpus is not None
    # Only the ref_id string is projected; the BaseModel is NOT embedded.
    assert result.corpus.evidence_set_ids == ["ev-XXX"]
    # Type-check: the typed list field is list[str], not list[GovernanceEvidenceRef].
    for ref_id in result.corpus.evidence_set_ids:
        assert isinstance(ref_id, str)


def test_replay_corpus_loader_load_ac5_8ac124d6_inclusion() -> None:
    """Doc-18:167-168 AC5: corpus includes 8ac124d6 evidence + Slice
    00-12 implementation artifacts."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(
        feature_ids=["8ac124d6"],
        implementation_anchor_refs=[
            _ref("slice-00-anchor"),
            _ref("slice-12-anchor"),
        ],
    )
    result = loader.load(inputs)
    assert result.corpus is not None
    assert "8ac124d6" in result.corpus.feature_ids
    assert "slice-00-anchor" in result.corpus.implementation_anchor_ids
    assert "slice-12-anchor" in result.corpus.implementation_anchor_ids


# ── ReplayCorpusLoader fail-closed ────────────────────────────────────────


def test_replay_corpus_loader_load_evidence_set_refs_exceeded_bound() -> None:
    """Doc-18:150 + Slice 13A bounded-reads: loader emits typed gap
    when evidence-set refs exceed bound."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(
        evidence_set_refs=[_ref(f"ev-{i}") for i in range(10)],
        max_evidence_set_refs=5,
    )
    result = loader.load(inputs)
    assert result.corpus is None
    assert len(result.gap_findings) == 1
    gap = result.gap_findings[0]
    assert gap.failure_id == "replay_corpus_or_scenario_load_failed"
    assert gap.reason == "evidence_set_refs_exceeded_bound"
    assert gap.evidence_payload["received_count"] == 10
    assert gap.evidence_payload["max_bound"] == 5


def test_replay_corpus_loader_load_implementation_anchor_refs_exceeded_bound() -> None:
    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(
        implementation_anchor_refs=[_ref(f"a-{i}") for i in range(10)],
        max_implementation_anchor_refs=5,
    )
    result = loader.load(inputs)
    assert result.corpus is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "implementation_anchor_refs_exceeded_bound"


def test_replay_corpus_loader_load_empty_feature_ids() -> None:
    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(feature_ids=[])
    result = loader.load(inputs)
    assert result.corpus is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "feature_ids_empty"


def test_replay_corpus_loader_load_never_raises_on_arbitrary_input() -> None:
    """`feedback_no_silent_degradation` fail-closed: loader NEVER raises.

    Construction of inputs may raise (Pydantic validation); but once we
    have valid typed inputs, the loader handles all internal failures
    via typed gap projection."""

    loader = ReplayCorpusLoader()
    # Valid inputs always result in a typed result (NEVER raises).
    inputs = _corpus_inputs()
    result = loader.load(inputs)
    assert isinstance(result, ReplayCorpusLoaderResult)


# ── ScenarioDefinitionInputs ──────────────────────────────────────────────


def test_scenario_definition_inputs_construction_round_trip() -> None:
    inputs = _scenario_inputs()
    assert inputs.scenario_id == "scenario-1"
    assert inputs.affected_consumers == ["scheduler"]
    assert inputs.required_evidence_kinds == ["ev-1"]
    assert inputs.corpus.corpus_id == "corpus-1"


def test_scenario_definition_inputs_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ScenarioDefinitionInputs(  # type: ignore[call-arg]
            scenario_id="scenario-1",
            policy_under_test={},
            baseline_policy_refs=[],
            affected_consumers=["scheduler"],
            required_evidence_kinds=[],
            assumptions=[],
            corpus=_corpus(),
            unknown_field="rejected",
        )


def test_scenario_definition_inputs_affected_consumers_annotation_is_policy_consumer() -> None:
    """DIRECT annotation-identity REUSE for Slice 17 1st sub-slice
    :data:`PolicyConsumer`."""

    hints = get_type_hints(ScenarioDefinitionInputs)
    annotation = hints["affected_consumers"]
    # list[PolicyConsumer] -> args is (PolicyConsumer,).
    args = get_args(annotation)
    assert args == (PolicyConsumer,)


def test_scenario_definition_inputs_corpus_annotation_uses_replay_corpus() -> None:
    """DIRECT annotation-identity REUSE for Slice 18 1st sub-slice
    :class:`ReplayCorpus`."""

    hints = get_type_hints(ScenarioDefinitionInputs)
    annotation = hints["corpus"]
    assert annotation is ReplayCorpus


@pytest.mark.parametrize(
    "consumer",
    ["scheduler", "failure_router", "supervisor", "dashboard", "planning", "merge_queue"],
)
def test_scenario_definition_inputs_accepts_all_6_policy_consumers(consumer: str) -> None:
    """Slice 17 1st sub-slice :data:`PolicyConsumer` 6-value Literal:
    all 6 values accepted on :attr:`affected_consumers`."""

    inputs = _scenario_inputs(affected_consumers=[consumer])
    assert inputs.affected_consumers == [consumer]


def test_scenario_definition_inputs_rejects_unknown_consumer() -> None:
    with pytest.raises(ValidationError):
        _scenario_inputs(affected_consumers=["unknown_consumer"])


# ── ScenarioDefinitionResult ──────────────────────────────────────────────


def test_scenario_definition_result_construction_round_trip() -> None:
    scenario = CounterfactualScenario(
        scenario_id="scenario-1",
        policy_under_test={"k": "v"},
        baseline_policy_refs=["baseline-1"],
        affected_consumers=["scheduler"],
        required_evidence_kinds=["ev-1"],
        assumptions=["x"],
    )
    result = ScenarioDefinitionResult(
        scenario=scenario,
        validation_invalidated_by=["missing_evidence:foo"],
        gap_findings=[],
        idempotency_key="abc",
    )
    assert result.scenario is not None
    assert result.scenario.scenario_id == "scenario-1"
    assert result.validation_invalidated_by == ["missing_evidence:foo"]


def test_scenario_definition_result_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ScenarioDefinitionResult(  # type: ignore[call-arg]
            scenario=None,
            validation_invalidated_by=[],
            gap_findings=[],
            idempotency_key="x",
            unknown_field="rejected",
        )


# ── ScenarioDefinitionGap ─────────────────────────────────────────────────


def test_scenario_definition_gap_construction_round_trip() -> None:
    gap = ScenarioDefinitionGap(
        failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
        scenario_id="scenario-1",
        corpus_id="corpus-1",
        reason="scenario_construction_failed",
        observed_at=datetime.now(timezone.utc),
    )
    assert gap.failure_id == "replay_corpus_or_scenario_load_failed"
    assert gap.scenario_id == "scenario-1"


def test_scenario_definition_gap_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        ScenarioDefinitionGap(  # type: ignore[call-arg]
            failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
            scenario_id="scenario-1",
            corpus_id="corpus-1",
            reason="x",
            observed_at=datetime.now(timezone.utc),
            unknown_field="rejected",
        )


def test_scenario_definition_gap_scenario_id_optional() -> None:
    gap = ScenarioDefinitionGap(
        failure_id=REPLAY_CORPUS_OR_SCENARIO_LOAD_FAILURE_ID,
        scenario_id=None,
        corpus_id="corpus-1",
        reason="x",
        observed_at=datetime.now(timezone.utc),
    )
    assert gap.scenario_id is None


# ── ScenarioDefinitionBuilder happy-path ──────────────────────────────────


def test_scenario_definition_builder_build_happy_path() -> None:
    """Doc-18:112 step 2 happy-path: typed policy + assumptions +
    validity_limits -> typed :class:`CounterfactualScenario`
    constructed correctly."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs()
    result = builder.build(inputs)
    assert result.scenario is not None, f"Expected scenario, got gap: {result.gap_findings}"
    assert result.scenario.scenario_id == "scenario-1"
    assert result.scenario.policy_under_test == {
        "policy_kind": "wave_cap",
        "value": {"wave_cap": 7},
    }
    assert result.scenario.affected_consumers == ["scheduler"]
    assert result.scenario.required_evidence_kinds == ["ev-1"]
    assert result.scenario.assumptions == [
        "product_defect_independent_of_wave_size",
    ]
    assert result.gap_findings == []
    assert result.validation_invalidated_by == []
    assert len(result.idempotency_key) == 64


def test_scenario_definition_builder_build_idempotency_key_deterministic() -> None:
    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs()
    r1 = builder.build(inputs)
    r2 = builder.build(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_scenario_definition_builder_returns_scenario_for_all_6_consumers() -> None:
    builder = ScenarioDefinitionBuilder()
    for consumer in ("scheduler", "failure_router", "supervisor", "dashboard", "planning", "merge_queue"):
        result = builder.build(_scenario_inputs(affected_consumers=[consumer]))
        assert result.scenario is not None
        assert result.scenario.affected_consumers == [consumer]


# ── ScenarioDefinitionBuilder validity-limits enforcement ─────────────────


def test_scenario_definition_builder_missing_required_evidence_emits_invalidated_by() -> None:
    """Doc-18:112 + doc-18:134-135: required evidence kinds verified at
    scenario construction time against the corpus's evidence-set ids +
    implementation-anchor ids; missing evidence populates the
    :attr:`validation_invalidated_by` list."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(
        required_evidence_kinds=["typed_attempt", "typed_failure"],
        corpus=_corpus(
            evidence_set_ids=["ev-1"],
            implementation_anchor_ids=["anchor-1"],
        ),
    )
    result = builder.build(inputs)
    # Scenario is STILL emitted (per the typed-shape audit-trail
    # discipline; the validation_invalidated_by list carries the gaps).
    assert result.scenario is not None
    assert "missing_evidence:typed_attempt" in result.validation_invalidated_by
    assert "missing_evidence:typed_failure" in result.validation_invalidated_by


def test_scenario_definition_builder_evidence_covered_by_implementation_anchors() -> None:
    """Doc-18:112: implementation-anchor ids also count toward
    required-evidence coverage."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(
        required_evidence_kinds=["anchor-1"],
        corpus=_corpus(
            evidence_set_ids=[],
            implementation_anchor_ids=["anchor-1"],
        ),
    )
    result = builder.build(inputs)
    assert result.scenario is not None
    assert result.validation_invalidated_by == []


def test_scenario_definition_builder_evidence_covered_by_evidence_set_ids() -> None:
    """Doc-18:112: evidence-set ids count toward required-evidence
    coverage."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(
        required_evidence_kinds=["ev-X"],
        corpus=_corpus(
            evidence_set_ids=["ev-X"],
            implementation_anchor_ids=[],
        ),
    )
    result = builder.build(inputs)
    assert result.scenario is not None
    assert result.validation_invalidated_by == []


# ── ScenarioDefinitionBuilder fail-closed ─────────────────────────────────


def test_scenario_definition_builder_empty_scenario_id_emits_gap() -> None:
    """The builder NEVER raises; empty scenario_id produces a typed
    gap."""

    builder = ScenarioDefinitionBuilder()
    # An empty string is a Pydantic-valid str; the builder applies the
    # empty-string check inside the build method.
    inputs = _scenario_inputs(scenario_id="   ")
    result = builder.build(inputs)
    assert result.scenario is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "scenario_id_empty"


def test_scenario_definition_builder_empty_affected_consumers_emits_gap() -> None:
    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(affected_consumers=[])
    result = builder.build(inputs)
    assert result.scenario is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "empty_affected_consumers"


def test_scenario_definition_builder_never_raises() -> None:
    """`feedback_no_silent_degradation`: builder NEVER raises on input."""

    builder = ScenarioDefinitionBuilder()
    # Even on a corpus with no evidence, the builder emits a typed
    # scenario + populated validation_invalidated_by.
    inputs = _scenario_inputs(
        required_evidence_kinds=["a", "b", "c"],
        corpus=_corpus(evidence_set_ids=[], implementation_anchor_ids=[]),
    )
    result = builder.build(inputs)
    assert isinstance(result, ScenarioDefinitionResult)


# ── compute_corpus_loader_idempotency_key ─────────────────────────────────


def test_compute_corpus_loader_idempotency_key_deterministic() -> None:
    key1 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1",
        feature_ids=["a"],
        evidence_set_ref_ids=["ev-1"],
        implementation_anchor_ref_ids=["a-1"],
        mode="summary_replay",
        validity_limits=[],
    )
    key2 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1",
        feature_ids=["a"],
        evidence_set_ref_ids=["ev-1"],
        implementation_anchor_ref_ids=["a-1"],
        mode="summary_replay",
        validity_limits=[],
    )
    assert key1 == key2
    assert len(key1) == 64  # SHA-256 hex


def test_compute_corpus_loader_idempotency_key_order_invariant() -> None:
    key1 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1",
        feature_ids=["a", "b"],
        evidence_set_ref_ids=["ev-1", "ev-2"],
        implementation_anchor_ref_ids=["a-1", "a-2"],
        mode="summary_replay",
        validity_limits=["x", "y"],
    )
    key2 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1",
        feature_ids=["b", "a"],
        evidence_set_ref_ids=["ev-2", "ev-1"],
        implementation_anchor_ref_ids=["a-2", "a-1"],
        mode="summary_replay",
        validity_limits=["y", "x"],
    )
    assert key1 == key2


def test_compute_corpus_loader_idempotency_key_differs_on_corpus_id() -> None:
    key1 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1",
        feature_ids=[],
        evidence_set_ref_ids=[],
        implementation_anchor_ref_ids=[],
        mode="summary_replay",
        validity_limits=[],
    )
    key2 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-2",
        feature_ids=[],
        evidence_set_ref_ids=[],
        implementation_anchor_ref_ids=[],
        mode="summary_replay",
        validity_limits=[],
    )
    assert key1 != key2


def test_compute_corpus_loader_idempotency_key_differs_on_mode() -> None:
    key1 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1", feature_ids=[], evidence_set_ref_ids=[],
        implementation_anchor_ref_ids=[], mode="summary_replay", validity_limits=[],
    )
    key2 = compute_corpus_loader_idempotency_key(
        corpus_id="corpus-1", feature_ids=[], evidence_set_ref_ids=[],
        implementation_anchor_ref_ids=[], mode="event_replay", validity_limits=[],
    )
    assert key1 != key2


# ── compute_scenario_idempotency_key ──────────────────────────────────────


def test_compute_scenario_idempotency_key_deterministic() -> None:
    key1 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=["a"], assumptions=["x"], validity_limits=[],
    )
    key2 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=["a"], assumptions=["x"], validity_limits=[],
    )
    assert key1 == key2
    assert len(key1) == 64


def test_compute_scenario_idempotency_key_order_invariant() -> None:
    key1 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=["a", "b"], assumptions=["x", "y"],
        validity_limits=["p", "q"],
    )
    key2 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=["b", "a"], assumptions=["y", "x"],
        validity_limits=["q", "p"],
    )
    assert key1 == key2


def test_compute_scenario_idempotency_key_differs_on_assumptions() -> None:
    """Doc-18:128-129 -- new assumptions require a new key (which then
    triggers a new result version)."""

    key1 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=[], assumptions=["x"], validity_limits=[],
    )
    key2 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=[], assumptions=["y"], validity_limits=[],
    )
    assert key1 != key2


def test_compute_scenario_idempotency_key_differs_on_scenario_id() -> None:
    key1 = compute_scenario_idempotency_key(
        scenario_id="s-1", corpus_id="c-1",
        required_evidence_kinds=[], assumptions=[], validity_limits=[],
    )
    key2 = compute_scenario_idempotency_key(
        scenario_id="s-2", corpus_id="c-1",
        required_evidence_kinds=[], assumptions=[], validity_limits=[],
    )
    assert key1 != key2


# ── failure_router 4-add-point validation ─────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    """Add point 1: FailureType Literal block."""

    assert "replay_corpus_or_scenario_load_failed" in get_args(FailureType)


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """Add point 2: FAILURE_TYPES tuple."""

    assert "replay_corpus_or_scenario_load_failed" in FAILURE_TYPES


def test_failure_router_retryable_failure_types_includes_new_id() -> None:
    """Add point 3: _RETRYABLE_FAILURE_TYPES frozenset."""

    assert "replay_corpus_or_scenario_load_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_table_includes_new_route_entry() -> None:
    """Add point 4: ROUTE_TABLE _route() entry under EXISTING
    evidence_corruption class with REUSED retry_governance_projection
    NON-blocking RouteAction."""

    route = ROUTE_TABLE[("evidence_corruption", "replay_corpus_or_scenario_load_failed")]
    assert route.action == "retry_governance_projection"


def test_failure_router_action_is_non_blocking_retry_governance_projection() -> None:
    """Per doc-14:242-243 the action is non-blocking
    (retry_governance_projection NOT quiesce)."""

    route = ROUTE_TABLE[("evidence_corruption", "replay_corpus_or_scenario_load_failed")]
    assert route.action == "retry_governance_projection"
    assert route.action != "quiesce"


def test_failure_router_existing_evidence_corruption_class_reused() -> None:
    """Per the Slice 14-17 precedent the failure_class is the EXISTING
    evidence_corruption class (NOT a new failure_class)."""

    route = ROUTE_TABLE[("evidence_corruption", "replay_corpus_or_scenario_load_failed")]
    assert route.failure_class == "evidence_corruption"


# ── doc-18 awareness PIN tests ────────────────────────────────────────────


def test_doc_18_186_249_slice_13a_no_redefinition() -> None:
    """Doc-18:186-249 Slice 13A Shared Completeness Model Dependency:
    the loader + builder consume the Slice 13a shared model REFS ONLY;
    they MUST NOT redefine the Slice 13A shapes."""

    from iriai_build_v2.execution_control import counterfactual_replay_loader as mod

    forbidden_shapes = [
        "CompletenessState",
        "EvidenceCompleteness",
        "AuthoritativeContextRef",
        "EvidencePageRef",
        "ExactEvidenceManifest",
        "AuthoritativePromptContextRouting",
        "AuthoritativeGateCompanionRecord",
        "AuthoritativeGateProofRow",
        "AuthoritativeSnapshotListFieldCompleteness",
        "AuthoritativeSnapshotClassifierRouting",
    ]
    for shape_name in forbidden_shapes:
        if hasattr(mod, shape_name):
            shape = getattr(mod, shape_name)
            if hasattr(shape, "__module__"):
                assert shape.__module__ != mod.__name__, (
                    f"Slice 18 2nd sub-slice REDEFINES Slice 13A {shape_name!r}"
                )


def test_doc_18_123_125_no_dag_authority_artifact_keys() -> None:
    """Doc-18:123-125: *"Replay results are review/governance artifacts
    only. Replay must not write `dag-*` execution authority artifacts
    or active policy markers."* The 2nd sub-slice loader + builder MUST
    NOT contain any `dag-*` authority artifact-key string literals."""

    import inspect
    from iriai_build_v2.execution_control import counterfactual_replay_loader as mod

    source = inspect.getsource(mod)
    # Allow `dag-` in comments / docstrings (purely documentation); only
    # check that no string literal starts with `dag-`.
    forbidden_prefixes = [
        '"dag-policy:',
        "'dag-policy:",
        '"dag-active:',
        "'dag-active:",
        '"dag-verify:',
        "'dag-verify:",
    ]
    for prefix in forbidden_prefixes:
        assert prefix not in source, (
            f"Slice 18 2nd sub-slice contains forbidden dag-authority artifact key prefix {prefix!r}"
        )


def test_doc_18_160_168_ac1_deterministic() -> None:
    """Doc-18:162 AC1: *"Counterfactuals are deterministic, versioned,
    and evidence-backed."* The loader's idempotency_key is deterministic
    via the typed compute_corpus_loader_idempotency_key helper."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs()
    r1 = loader.load(inputs)
    r2 = loader.load(inputs)
    assert r1.idempotency_key == r2.idempotency_key


def test_doc_18_160_168_ac2_assumptions_and_validity_limits() -> None:
    """Doc-18:163 AC2: *"Every result lists assumptions and validity
    limits."* The scenario builder emits a typed
    :class:`CounterfactualScenario` with the typed
    :attr:`assumptions: list[str]` field populated."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(assumptions=["assumption_a", "assumption_b"])
    result = builder.build(inputs)
    assert result.scenario is not None
    assert "assumption_a" in result.scenario.assumptions
    assert "assumption_b" in result.scenario.assumptions


def test_doc_18_160_168_ac3_no_mutation_methods() -> None:
    """Doc-18:164 AC3: *"Replay cannot mutate live workflow state."*
    The loader + builder classes MUST NOT expose mutation-like method
    names."""

    forbidden_method_prefixes = [
        "activate_",
        "apply_",
        "bind_",
        "mutate_",
        "commit_",
        "dispatch_",
        "schedule_",
        "write_",
        "persist_",
    ]
    for cls in (ReplayCorpusLoader, ScenarioDefinitionBuilder):
        for name in dir(cls):
            if name.startswith("_"):
                continue
            for prefix in forbidden_method_prefixes:
                assert not name.startswith(prefix), (
                    f"{cls.__name__}.{name} exposes mutation-like name {prefix!r}"
                )


def test_doc_18_165_166_ac4_scenario_carries_provenance() -> None:
    """Doc-18:165-166 AC4 cross-reference: the typed scenario carries
    baseline_policy_refs + assumptions so subsequent recommendations
    can cite the typed scenario as provenance."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(baseline_policy_refs=["baseline-policy-A"])
    result = builder.build(inputs)
    assert result.scenario is not None
    assert "baseline-policy-A" in result.scenario.baseline_policy_refs


def test_doc_18_167_168_ac5_corpus_includes_8ac124d6_and_anchors() -> None:
    """Doc-18:167-168 AC5: *"The replay corpus includes both 8ac124d6
    evidence and Slice 00-12 implementation artifacts."* The typed
    loader emits a corpus whose feature_ids contains 8ac124d6 + whose
    implementation_anchor_ids contains Slice 00-12 anchors."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(
        feature_ids=["8ac124d6"],
        implementation_anchor_refs=[
            _ref("slice-00-fixture-anchor"),
            _ref("slice-07-acceptance-anchor"),
            _ref("slice-12-acceptance-anchor"),
        ],
    )
    result = loader.load(inputs)
    assert result.corpus is not None
    assert "8ac124d6" in result.corpus.feature_ids
    assert "slice-00-fixture-anchor" in result.corpus.implementation_anchor_ids


def test_doc_18_150_test_bounded_input_rejection() -> None:
    """Doc-18:150: *"Replay corpus loader rejects malformed or
    unbounded fixture inputs."* SATISFIED via the
    max_evidence_set_refs + max_implementation_anchor_refs bounded-
    input contract + typed gap projection."""

    loader = ReplayCorpusLoader()
    inputs = _corpus_inputs(
        evidence_set_refs=[_ref(f"e-{i}") for i in range(20)],
        max_evidence_set_refs=10,
    )
    result = loader.load(inputs)
    assert result.corpus is None
    assert len(result.gap_findings) == 1
    assert result.gap_findings[0].reason == "evidence_set_refs_exceeded_bound"


def test_doc_18_153_test_missing_evidence_returns_invalidated_results() -> None:
    """Doc-18:153: *"Counterfactual scenarios with missing evidence
    return invalidated results."* SATISFIED via the scenario builder's
    validation_invalidated_by list."""

    builder = ScenarioDefinitionBuilder()
    inputs = _scenario_inputs(
        required_evidence_kinds=["missing_kind"],
        corpus=_corpus(evidence_set_ids=["different"], implementation_anchor_ids=[]),
    )
    result = builder.build(inputs)
    assert result.scenario is not None
    assert any(
        "missing_evidence:missing_kind" in s
        for s in result.validation_invalidated_by
    )
