"""Slice 18 first sub-slice -- unit tests for the foundational
``execution_control/counterfactual_replay.py`` typed-shape module.

Covers the 3 doc-18:61 + doc-18:91 + doc-18:95 Literals + the 3
doc-18:63-96 typed BaseModels + the canonical-JSON helper functions:

- :data:`ReplayMode` -- 3 values per doc-18:61.
- :data:`RiskChange` -- 4 values per doc-18:91.
- :data:`RecommendedNextStep` -- 4 values per doc-18:95.
- :class:`ReplayCorpus` -- 6 fields per doc-18:63-69.
- :class:`CounterfactualScenario` -- 6 fields per doc-18:71-77;
  Slice 17 1st sub-slice ``PolicyConsumer`` consumption on
  ``affected_consumers`` (NOT redefined; DIRECT annotation-identity
  assertion via ``is``).
- :class:`CounterfactualResult` -- 16 fields per doc-18:79-96; Slice
  13a shared ``GovernanceEvidenceRef`` consumption on
  ``policy_provenance_refs`` (NOT redefined; DIRECT annotation-identity
  assertion via ``is``); Slice 16 ``GovernanceFinding.idempotency_key``
  by-name reference on ``supporting_finding_ids: list[str]``.
- :func:`compute_counterfactual_idempotency_key` +
  :func:`canonical_counterfactual_dict` -- canonical-JSON + SHA-256
  helpers mirroring Slice 13A ``compute_completeness_digest`` + Slice
  14 ``compute_payload_sha256`` + Slice 15
  ``compute_scorecard_digest`` + Slice 16 1st sub-slice
  ``compute_finding_idempotency_key`` + ``canonical_finding_dict`` +
  Slice 17 1st sub-slice
  ``compute_policy_recommendation_idempotency_key`` +
  ``canonical_policy_recommendation_dict`` discipline.

Every model enforces ``extra="forbid"`` (typo-d kwargs ->
``ValidationError``). Every Literal range is enforced (per Pydantic
``Literal`` validator).

**Slice 14 P3-V3-2 addressed (DIRECT annotation-identity assertion).**
Per the implementer prompt § "Non-negotiables" the Slice 13a shared
model identity is enforced via a DIRECT
``get_args(get_origin(... .annotation))`` decomposition + ``is``
comparison rather than the indirect value-set + namespace assertions
used in Slice 14 1st sub-slice tests at
``tests/test_execution_control_commit_provenance.py:718``. This is the
stronger pattern V3 reviewer flagged in the Slice 14 close-out
(P3-V3-2 CARRY) + the pattern Slice 15 1st sub-slice + Slice 16 1st
sub-slice + Slice 17 1st sub-slice adopted; this Slice 18 1st sub-slice
continues the pattern verbatim for BOTH the Slice 13a
``GovernanceEvidenceRef`` REUSE on
``CounterfactualResult.policy_provenance_refs`` AND the Slice 17
``PolicyConsumer`` REUSE on
``CounterfactualScenario.affected_consumers``.

**Slice 13A awareness asserted (doc-18:186-249).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` / ``ExactEvidenceManifest``
redefinition. The counterfactual-replay module exposes the typed
surface that future Slice 18 sub-slices wire to the Slice 13A typed
shapes; this 1st sub-slice enforces the no-redefinition discipline at
the test-file level.

**Doc-18:123-129 persistence + artifact compatibility awareness.** No
1st-sub-slice consumer activation wiring; no consumer-owned artifact-
key literals; replay results are review/governance artifacts only,
never runtime policy authority (mirrors the Slice 17 7th sub-slice
activation-boundary discipline).

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 +
Slice 16 + Slice 17 modules + tests remain byte-identical.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.counterfactual_replay import (
    CounterfactualResult,
    CounterfactualScenario,
    RecommendedNextStep,
    ReplayCorpus,
    ReplayMode,
    RiskChange,
    canonical_counterfactual_dict,
    compute_counterfactual_idempotency_key,
)
from iriai_build_v2.execution_control.policy_recommendation import (
    PolicyConsumer,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 3 Literals + 3 typed
    BaseModels + 2 canonical-JSON helpers.

    Per doc-18:60-96 the surface is:
    * 3 Literals (``ReplayMode`` 3-value + ``RiskChange`` 4-value +
      ``RecommendedNextStep`` 4-value).
    * 3 typed BaseModels (``ReplayCorpus`` 6-field +
      ``CounterfactualScenario`` 6-field + ``CounterfactualResult``
      16-field).
    * 2 canonical-JSON helpers
      (``compute_counterfactual_idempotency_key`` +
      ``canonical_counterfactual_dict``).

    Total: 8 exported names.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    expected = {
        "ReplayMode",
        "RiskChange",
        "RecommendedNextStep",
        "ReplayCorpus",
        "CounterfactualScenario",
        "CounterfactualResult",
        "compute_counterfactual_idempotency_key",
        "canonical_counterfactual_dict",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 8
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-18:186-249 the Slice 18 module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes the
    Slice 13a shared model via direct import.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    # Either the module does NOT re-export GovernanceEvidenceRef OR (if
    # it does, e.g. via an internal import) the re-exported symbol IS
    # the Slice 13a shared class (identity).
    assert getattr(mod, "GovernanceEvidenceRef", None) is None or (
        mod.GovernanceEvidenceRef is GovernanceEvidenceRef  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_policy_consumer() -> None:
    """Per the Slice 17 no-second-source-of-truth discipline + doc-18:75
    the Slice 18 module MUST NOT redefine :data:`PolicyConsumer` -- it
    consumes the Slice 17 1st sub-slice shared 6-value Literal via
    direct import.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    # Either the module does NOT re-export PolicyConsumer OR (if it
    # does, e.g. via an internal import) the re-exported symbol IS the
    # Slice 17 shared Literal (identity).
    assert getattr(mod, "PolicyConsumer", None) is None or (
        mod.PolicyConsumer is PolicyConsumer  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_governance_finding() -> None:
    """Per doc-18:94 + doc-18:186-249 the Slice 18 module references
    Slice 16 :class:`GovernanceFinding` by-name via the
    ``supporting_finding_ids: list[str]`` field (NOT by re-defining the
    typed BaseModel locally; the by-name reference shape lives in
    doc-18:94 verbatim).
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    # The Slice 18 module does NOT re-export or redefine the Slice 16
    # GovernanceFinding typed BaseModel (the by-name reference shape
    # is sufficient per doc-18:94).
    assert getattr(mod, "GovernanceFinding", None) is None


def test_module_does_not_redefine_governance_metric_value() -> None:
    """Per doc-18:186-249 the Slice 18 module does NOT redefine Slice
    15 :class:`GovernanceMetricValue` (the Slice 15 metrics surface is
    consumed by the future Slice 18 5th sub-slice metrics-comparator,
    not the 1st sub-slice typed-shape foundation).
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "GovernanceMetricValue", None) is None


def test_module_does_not_redefine_governance_policy_recommendation() -> None:
    """Per the Slice 17 no-second-source-of-truth discipline the Slice
    18 module does NOT redefine Slice 17
    :class:`GovernancePolicyRecommendation` (the Slice 17 recommendation
    surface is consumed by the future Slice 18 7th sub-slice citation
    hook via the by-name ``counterfactual_result_refs: list[str]``
    field on :class:`GovernancePolicyRecommendation`, not the 1st
    sub-slice typed-shape foundation).
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "GovernancePolicyRecommendation", None) is None


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-18:186-249 the Slice 18 module MUST NOT redefine
    :data:`CompletenessState` -- it consumes the Slice 13A shared
    Literal via direct import in subsequent sub-slices (not this 1st
    sub-slice which exposes only the typed-shape foundation).
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "CompletenessState", None) is None


def test_module_does_not_redefine_evidence_completeness() -> None:
    """Per doc-18:186-249 the Slice 18 module MUST NOT redefine
    :class:`EvidenceCompleteness` -- it consumes the Slice 13A shared
    BaseModel via direct import in subsequent sub-slices.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "EvidenceCompleteness", None) is None


def test_module_does_not_redefine_authoritative_prompt_context_routing() -> None:
    """Per doc-18:186-249 + doc-18:211-218 the Slice 18 module MUST
    NOT redefine :class:`AuthoritativePromptContextRouting` -- it
    consumes the Slice 13A shared BaseModel via direct import in
    subsequent sub-slices.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "AuthoritativePromptContextRouting", None) is None


def test_module_does_not_redefine_authoritative_gate_proof_row() -> None:
    """Per doc-18:186-249 + doc-18:219-225 the Slice 18 module MUST
    NOT redefine :class:`AuthoritativeGateProofRow` -- it consumes the
    Slice 13A shared BaseModel via direct import in subsequent
    sub-slices.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "AuthoritativeGateProofRow", None) is None


def test_module_does_not_redefine_authoritative_snapshot_classifier_routing() -> None:
    """Per doc-18:186-249 + doc-18:226-232 the Slice 18 module MUST
    NOT redefine :class:`AuthoritativeSnapshotClassifierRouting` -- it
    consumes the Slice 13A shared BaseModel via direct import in
    subsequent sub-slices.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "AuthoritativeSnapshotClassifierRouting", None) is None


def test_module_does_not_redefine_exact_evidence_manifest() -> None:
    """Per doc-18:200-202 the Slice 18 module MUST NOT redefine
    :class:`ExactEvidenceManifest` -- the shared
    ``ExactEvidenceManifest`` is the source-of-truth shape for the
    *"Replay corpus loader rejects malformed or unbounded fixture
    inputs"* acceptance test; the future Slice 18 2nd sub-slice corpus
    loader consumes the Slice 13A typed shape via direct import.
    """

    from iriai_build_v2.execution_control import counterfactual_replay as mod

    assert getattr(mod, "ExactEvidenceManifest", None) is None


def test_module_import_discipline_no_implementation_py() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 18 1st sub-slice module MUST NOT import from the legacy
    ``implementation.py`` Slice 00-12 monolith (this module is
    foundational; the Slice 00-12 monolith is the runtime authority
    layer and is downstream of Slice 18's typed-shape surface).
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.workflows.develop.execution.implementation",
        "import iriai_build_v2.workflows.develop.execution.implementation",
    )
    for f in forbidden:
        assert f not in src, (
            f"counterfactual_replay.py must not import from the Slice "
            f"00-12 implementation.py monolith; found {f!r}"
        )


def test_module_import_discipline_no_failure_router() -> None:
    """Per the implementer prompt § "Non-negotiables" the Slice 18 1st
    sub-slice module MUST NOT import from
    ``workflows.develop.execution.failure_router`` (the typed failure
    router is the Slice 07 runtime authority; Slice 18 1st sub-slice
    is a pure typed-shape foundation that does not yet emit any failure
    ids).
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.workflows.develop.execution.failure_router",
        "import iriai_build_v2.workflows.develop.execution.failure_router",
    )
    for f in forbidden:
        assert f not in src, (
            f"counterfactual_replay.py must not import from "
            f"failure_router.py; found {f!r}"
        )


def test_module_import_discipline_no_other_execution_control_modules() -> None:
    """Per the implementer prompt § "Implementation discipline" the
    Slice 18 1st sub-slice module MUST NOT import from other parts of
    ``execution_control/`` beyond the Slice 17 1st sub-slice
    ``policy_recommendation`` module (which supplies the
    :data:`PolicyConsumer` REUSE) and the Slice 13a shared model at
    ``workflows.develop.governance.models`` (which supplies the
    :class:`GovernanceEvidenceRef` REUSE).

    The existing Slice 00-16 ``execution_control`` modules + the
    Slice 17 2nd-6th sub-slice modules are READ-ONLY; subsequent
    Slice 18 sub-slices wire it to additional Slice 13A / Slice 15 /
    Slice 16 / Slice 17 modules.
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.execution_control.commit_provenance",
        "from iriai_build_v2.execution_control.completeness",
        "from iriai_build_v2.execution_control.dispatcher_prompt_context",
        "from iriai_build_v2.execution_control.gate_companion",
        "from iriai_build_v2.execution_control.snapshot_companion",
        "from iriai_build_v2.execution_control.governance_metrics",
        "from iriai_build_v2.execution_control.governance_metric_extractor",
        "from iriai_build_v2.execution_control.governance_scorecard_writer",
        "from iriai_build_v2.execution_control.finding_engine",
        "from iriai_build_v2.execution_control.finding_rule_engine",
        "from iriai_build_v2.execution_control.finding_plan_deviation_engine",
        "from iriai_build_v2.execution_control.finding_reviewer_test_failure_engine",
        "from iriai_build_v2.execution_control.governance_finding_writer",
        "from iriai_build_v2.execution_control.recommendation_builder",
        "from iriai_build_v2.execution_control.policy_validation_interface",
        "from iriai_build_v2.execution_control.decision_record_writer",
        "from iriai_build_v2.execution_control.replay_requirement_hook",
        "from iriai_build_v2.execution_control.consumer_read_api",
        "from iriai_build_v2.execution_control.store",
        "from iriai_build_v2.execution_control.atomic_landing",
        "from iriai_build_v2.execution_control.adoption",
        "from iriai_build_v2.execution_control.startup",
    )
    for f in forbidden:
        assert f not in src, (
            f"counterfactual_replay.py 1st sub-slice must not import "
            f"from other execution_control modules; found {f!r}. "
            f"Subsequent Slice 18 sub-slices may add these imports per "
            f"doc-18:111-119."
        )


def test_module_import_discipline_no_supervisor_or_dashboard() -> None:
    """Per the governance prompt § "Non-Negotiables" + STATUS.md §
    "Loop discipline" activation-authority-boundary note the Slice 18
    typed-shape module MUST NOT import from ``supervisor`` or
    ``dashboard`` (those are downstream consumers of the
    counterfactual-replay surface, not dependencies).
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    forbidden = (
        "from iriai_build_v2.supervisor",
        "import iriai_build_v2.supervisor",
        "from iriai_build_v2.dashboard",
        "import iriai_build_v2.dashboard",
        "from dashboard",
        "import dashboard",
    )
    for f in forbidden:
        assert f not in src, (
            f"counterfactual_replay.py must not import from supervisor "
            f"or dashboard; found {f!r}"
        )


def test_package_init_does_not_re_export_counterfactual_replay() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 + Slice 17
    precedent (no-re-export discipline) the
    ``src/iriai_build_v2/execution_control/__init__.py`` MUST NOT
    re-export the Slice 18 1st sub-slice module's typed shapes.
    Consumers import directly from
    :mod:`iriai_build_v2.execution_control.counterfactual_replay`.
    """

    from iriai_build_v2 import execution_control as pkg

    forbidden_re_exports = (
        "ReplayMode",
        "RiskChange",
        "RecommendedNextStep",
        "ReplayCorpus",
        "CounterfactualScenario",
        "CounterfactualResult",
        "compute_counterfactual_idempotency_key",
        "canonical_counterfactual_dict",
    )
    for name in forbidden_re_exports:
        assert name not in pkg.__all__, (
            f"execution_control/__init__.py must not re-export Slice "
            f"18 module symbol {name!r} (per Slice 13A/14/15/16/17 "
            f"no-re-export discipline)"
        )
        assert not hasattr(pkg, name), (
            f"execution_control/__init__.py must not re-export Slice "
            f"18 module symbol {name!r} (per Slice 13A/14/15/16/17 "
            f"no-re-export discipline)"
        )


# ── ReplayMode (doc-18:61) ─────────────────────────────────────────────────


def test_replay_mode_is_3_value_literal() -> None:
    """Per doc-18:61 :data:`ReplayMode` is exactly the 3-value Literal
    verbatim.
    """

    args = get_args(ReplayMode)
    assert len(args) == 3
    assert set(args) == {"event_replay", "summary_replay", "hybrid"}


@pytest.mark.parametrize(
    "mode",
    ["event_replay", "summary_replay", "hybrid"],
)
def test_replay_mode_accepts_all_3_values(mode: str) -> None:
    """Per doc-18:61 every one of the 3 Literal values is constructible
    on a :class:`ReplayCorpus`.
    """

    c = _corpus(mode=mode)
    assert c.mode == mode


def test_replay_mode_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown mode value fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _corpus(mode="random_made_up_mode")


# ── RiskChange (doc-18:91) ─────────────────────────────────────────────────


def test_risk_change_is_4_value_literal() -> None:
    """Per doc-18:91 :data:`RiskChange` is exactly the 4-value Literal
    verbatim.
    """

    args = get_args(RiskChange)
    assert len(args) == 4
    assert set(args) == {"lower", "same", "higher", "unknown"}


@pytest.mark.parametrize(
    "risk_change", ["lower", "same", "higher", "unknown"],
)
def test_risk_change_accepts_all_4_values(risk_change: str) -> None:
    """Per doc-18:91 every one of the 4 Literal values is constructible
    on a :class:`CounterfactualResult`.
    """

    r = _result(estimated_risk_change=risk_change)
    assert r.estimated_risk_change == risk_change


def test_risk_change_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown risk-change value
    fails closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _result(estimated_risk_change="catastrophic")


# ── RecommendedNextStep (doc-18:95) ────────────────────────────────────────


def test_recommended_next_step_is_4_value_literal() -> None:
    """Per doc-18:95 :data:`RecommendedNextStep` is exactly the 4-value
    Literal verbatim.
    """

    args = get_args(RecommendedNextStep)
    assert len(args) == 4
    assert set(args) == {
        "discard",
        "collect_more_evidence",
        "draft_policy",
        "implementation_plan",
    }


@pytest.mark.parametrize(
    "step",
    [
        "discard",
        "collect_more_evidence",
        "draft_policy",
        "implementation_plan",
    ],
)
def test_recommended_next_step_accepts_all_4_values(step: str) -> None:
    """Per doc-18:95 every one of the 4 Literal values is constructible
    on a :class:`CounterfactualResult`.
    """

    r = _result(recommended_next_step=step)
    assert r.recommended_next_step == step


def test_recommended_next_step_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown next-step value fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _result(recommended_next_step="random_step")


# ── Test fixtures / constructors ────────────────────────────────────────────


def _evidence_ref(**overrides: object) -> GovernanceEvidenceRef:
    """Construct a fully-specified Slice 13a :class:`GovernanceEvidenceRef`
    for tests.
    """

    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-18-1",
        digest="a" * 64,
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)


def _corpus(**overrides: object) -> ReplayCorpus:
    """Construct a fully-specified :class:`ReplayCorpus` for tests."""

    base: dict[str, object] = dict(
        corpus_id="corpus-18-1",
        feature_ids=["8ac124d6"],
        evidence_set_ids=["evidence-set-1"],
        implementation_anchor_ids=["journal-anchor-slice-12-12f"],
        mode="summary_replay",
        validity_limits=["sample_size<10"],
    )
    base.update(overrides)
    return ReplayCorpus(**base)


def _scenario(**overrides: object) -> CounterfactualScenario:
    """Construct a fully-specified :class:`CounterfactualScenario` for
    tests.
    """

    base: dict[str, object] = dict(
        scenario_id="scenario-18-1",
        policy_under_test={
            "policy_kind": "wave_cap",
            "scope": {"lane_id": "ml-7"},
            "value": {"wave_cap": 7},
        },
        baseline_policy_refs=["rec-17-prior-1"],
        affected_consumers=["scheduler"],
        required_evidence_kinds=["typed_attempt"],
        assumptions=["product_defect_independent_of_wave_size"],
    )
    base.update(overrides)
    return CounterfactualScenario(**base)


def _result(**overrides: object) -> CounterfactualResult:
    """Construct a fully-specified :class:`CounterfactualResult` for
    tests.
    """

    base: dict[str, object] = dict(
        result_id="result-18-1",
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=["product_defect_independent_of_wave_size"],
        validity_limits=["sample_size<10"],
        policy_provenance_refs=[_evidence_ref()],
        safety_guard_class=None,
        estimated_delta_hours=-2.5,
        estimated_delta_repair_cycles=-0.5,
        estimated_delta_commit_failures=-0.1,
        estimated_risk_change="lower",
        confidence=0.65,
        invalidated_by=[],
        supporting_finding_ids=["finding-key-abc123"],
        recommended_next_step="draft_policy",
    )
    base.update(overrides)
    return CounterfactualResult(**base)


# ── ReplayCorpus (doc-18:63-69) ────────────────────────────────────────────


def test_corpus_accepts_all_6_fields() -> None:
    """The 6 doc-18:64-69 fields all populate cleanly on a
    fully-specified :class:`ReplayCorpus`.
    """

    c = _corpus()
    assert c.corpus_id == "corpus-18-1"
    assert c.feature_ids == ["8ac124d6"]
    assert c.evidence_set_ids == ["evidence-set-1"]
    assert c.implementation_anchor_ids == ["journal-anchor-slice-12-12f"]
    assert c.mode == "summary_replay"
    assert c.validity_limits == ["sample_size<10"]


def test_corpus_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _corpus(unknown_field="oops")  # type: ignore[arg-type]


def test_corpus_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed :class:`ReplayCorpus` -> JSON
    -> :class:`ReplayCorpus` round-trip is value-equivalent.
    """

    c = _corpus()
    j = c.model_dump_json()
    c2 = ReplayCorpus.model_validate_json(j)
    assert c == c2


def test_corpus_mode_annotation_is_direct_replay_mode_literal() -> None:
    """**Slice 18 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`ReplayCorpus.mode` field MUST be typed against the
    DIRECT :data:`ReplayMode` Literal (NOT a re-aliased copy).
    """

    annotation = ReplayCorpus.model_fields["mode"].annotation
    assert annotation is ReplayMode
    assert set(get_args(annotation)) == {
        "event_replay",
        "summary_replay",
        "hybrid",
    }


def test_corpus_accepts_8ac124d6_feature_id() -> None:
    """**Doc-18:167-168 AC5 binding statement enforcement.** Per
    doc-18:167-168 *"The replay corpus includes both 8ac124d6 evidence
    and Slice 00-12 implementation artifacts."* the typed surface
    MUST accept ``"8ac124d6"`` as a feature id (the canonical fixture
    coverage anchor).
    """

    c = _corpus(feature_ids=["8ac124d6"])
    assert "8ac124d6" in c.feature_ids


def test_corpus_accepts_slice_00_12_implementation_anchor_ids() -> None:
    """**Doc-18:167-168 AC5 binding statement enforcement (Slice 00-12
    side).** Per doc-18:167-168 the typed surface MUST accept Slice
    00-12 implementation anchor ids (the future Slice 18 2nd sub-slice
    corpus loader populates this from the Slice 00-12 implementation
    journal anchors).
    """

    anchors = [
        "journal-anchor-slice-07-07a",
        "journal-anchor-slice-08-08g",
        "journal-anchor-slice-12-12f",
    ]
    c = _corpus(implementation_anchor_ids=anchors)
    assert c.implementation_anchor_ids == anchors


def test_corpus_accepts_empty_validity_limits() -> None:
    """Per doc-18:69 the typed surface accepts the empty
    validity-limits list at construction (the future Slice 18 2nd
    sub-slice corpus loader populates the list with per-segment
    rationale; the typed-shape layer does not pre-emptively enforce
    a minimum).
    """

    c = _corpus(validity_limits=[])
    assert c.validity_limits == []


def test_corpus_accepts_multiple_validity_limits() -> None:
    """Per doc-18:48-49 *"Replay may start with deterministic
    summary-level simulation when full event replay is not available,
    if validity limits are explicit."* the validity-limits list
    accepts multiple per-segment rationale entries.
    """

    limits = [
        "sample_size<10",
        "product_defect_window",
        "missing_typed_timing",
    ]
    c = _corpus(validity_limits=limits)
    assert c.validity_limits == limits


# ── CounterfactualScenario (doc-18:71-77) ──────────────────────────────────


def test_scenario_accepts_all_6_fields() -> None:
    """The 6 doc-18:72-77 fields all populate cleanly on a
    fully-specified :class:`CounterfactualScenario`.
    """

    s = _scenario()
    assert s.scenario_id == "scenario-18-1"
    assert s.policy_under_test == {
        "policy_kind": "wave_cap",
        "scope": {"lane_id": "ml-7"},
        "value": {"wave_cap": 7},
    }
    assert s.baseline_policy_refs == ["rec-17-prior-1"]
    assert s.affected_consumers == ["scheduler"]
    assert s.required_evidence_kinds == ["typed_attempt"]
    assert s.assumptions == ["product_defect_independent_of_wave_size"]


def test_scenario_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _scenario(unknown_field="oops")  # type: ignore[arg-type]


def test_scenario_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed :class:`CounterfactualScenario`
    -> JSON -> :class:`CounterfactualScenario` round-trip is value-
    equivalent.
    """

    s = _scenario()
    j = s.model_dump_json()
    s2 = CounterfactualScenario.model_validate_json(j)
    assert s == s2


def test_scenario_affected_consumers_annotation_is_list_of_direct_policy_consumer() -> None:
    """**Slice 17 dependency reconciliation DIRECT annotation-identity
    assertion** (P3-V3-2 stronger pattern).

    Per the implementer prompt § "Non-negotiables" this is the STRONGER
    pattern Slice 14 V3 reviewer flagged in P3-V3-2 CARRY + the pattern
    Slice 15 1st sub-slice + Slice 16 1st sub-slice + Slice 17 1st
    sub-slice adopted: the
    :attr:`CounterfactualScenario.affected_consumers` field MUST be
    typed against ``list[PolicyConsumer]`` where :data:`PolicyConsumer`
    IS the Slice 17 1st sub-slice shared 6-value Literal imported from
    :mod:`iriai_build_v2.execution_control.policy_recommendation` --
    NOT redefined here. Assert the annotation resolves to
    ``list[PolicyConsumer]`` via DIRECT identity comparison on the
    list element type.
    """

    annotation = CounterfactualScenario.model_fields[
        "affected_consumers"
    ].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 17 shared Literal via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is PolicyConsumer


def test_scenario_affected_consumers_is_imported_from_slice_17() -> None:
    """Per the Slice 17 no-second-source-of-truth discipline the
    :data:`PolicyConsumer` consumed by Slice 18 IS the Slice 17 shared
    Literal (identity-equal via the direct import) -- NOT a second
    copy.
    """

    # The Slice 17 import path.
    from iriai_build_v2.execution_control.policy_recommendation import (
        PolicyConsumer as Slice17PolicyConsumer,
    )

    # The Slice 18 binding (via the annotation on ``affected_consumers``).
    args = get_args(
        CounterfactualScenario.model_fields["affected_consumers"].annotation
    )
    assert len(args) == 1
    # The list element type IS the Slice 17 shared Literal (identity).
    assert args[0] is Slice17PolicyConsumer


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
def test_scenario_affected_consumers_accepts_all_6_slice_17_consumer_values(
    consumer: str,
) -> None:
    """Per doc-18:75 + Slice 17 doc-17:65 each one of the 6 Slice 17
    consumer values is constructible on
    :attr:`CounterfactualScenario.affected_consumers`.
    """

    s = _scenario(affected_consumers=[consumer])
    assert s.affected_consumers == [consumer]


def test_scenario_affected_consumers_rejects_unknown_consumer_value() -> None:
    """Per Pydantic Literal validation an unknown consumer value fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _scenario(affected_consumers=["random_made_up_consumer"])


def test_scenario_affected_consumers_accepts_multiple_consumers() -> None:
    """A cross-cutting scenario may affect multiple consumers per
    doc-18:75; the typed surface accepts the multi-consumer list.
    """

    s = _scenario(affected_consumers=["failure_router", "merge_queue"])
    assert s.affected_consumers == ["failure_router", "merge_queue"]


def test_scenario_policy_under_test_accepts_dict_str_any() -> None:
    """Per doc-18:73 :attr:`CounterfactualScenario.policy_under_test` is
    ``dict[str, Any]`` (free-form so the scenario emitter can produce a
    policy candidate of any consumer-specific shape).
    """

    annotation = CounterfactualScenario.model_fields[
        "policy_under_test"
    ].annotation
    assert get_origin(annotation) is dict
    args = get_args(annotation)
    assert args[0] is str
    # Second arg is Any (or object); accept either.
    assert args[1] in (Any, object)


def test_scenario_assumptions_accepts_empty_list() -> None:
    """Per doc-18:77 the typed surface accepts the empty
    assumptions list at construction (the at-least-one-assumption
    invariant lives in the future Slice 18 3rd sub-slice scenario
    emitter when the scenario is a safety-guard exception per
    doc-18:140-146).
    """

    s = _scenario(assumptions=[])
    assert s.assumptions == []


# ── CounterfactualResult (doc-18:79-96) ────────────────────────────────────


def test_result_accepts_all_16_fields() -> None:
    """The 16 doc-18:80-95 fields all populate cleanly on a
    fully-specified :class:`CounterfactualResult`.
    """

    r = _result()
    assert r.result_id == "result-18-1"
    assert r.result_version == "v1"
    assert r.scenario_id == "scenario-18-1"
    assert r.corpus_id == "corpus-18-1"
    assert r.assumptions == ["product_defect_independent_of_wave_size"]
    assert r.validity_limits == ["sample_size<10"]
    assert len(r.policy_provenance_refs) == 1
    assert isinstance(r.policy_provenance_refs[0], GovernanceEvidenceRef)
    assert r.safety_guard_class is None
    assert r.estimated_delta_hours == -2.5
    assert r.estimated_delta_repair_cycles == -0.5
    assert r.estimated_delta_commit_failures == -0.1
    assert r.estimated_risk_change == "lower"
    assert r.confidence == 0.65
    assert r.invalidated_by == []
    assert r.supporting_finding_ids == ["finding-key-abc123"]
    assert r.recommended_next_step == "draft_policy"


def test_result_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _result(unknown_field="oops")  # type: ignore[arg-type]


def test_result_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed :class:`CounterfactualResult`
    -> JSON -> :class:`CounterfactualResult` round-trip is value-
    equivalent (including the typed Slice 13a
    :class:`GovernanceEvidenceRef` evidence-refs).
    """

    r = _result()
    j = r.model_dump_json()
    r2 = CounterfactualResult.model_validate_json(j)
    assert r == r2


def test_result_safety_guard_class_defaults_to_none() -> None:
    """Per doc-18:87 :attr:`safety_guard_class` defaults to ``None``
    (no safety-guard exception by default); the typed surface enforces
    the default at construction.
    """

    r = CounterfactualResult(
        result_id="result-18-2",
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        policy_provenance_refs=[_evidence_ref()],
        # safety_guard_class deliberately omitted -- should default to None.
        estimated_delta_hours=None,
        estimated_delta_repair_cycles=None,
        estimated_delta_commit_failures=None,
        estimated_risk_change="unknown",
        confidence=0.0,
        invalidated_by=["missing_evidence:typed_attempt"],
        supporting_finding_ids=[],
        recommended_next_step="collect_more_evidence",
    )
    assert r.safety_guard_class is None


def test_result_safety_guard_class_accepts_string() -> None:
    """Per doc-18:87 + doc-18:140-146 the safety-guard-class accepts
    typed strings (e.g. ``"fail_closed_earlier"``).
    """

    r = _result(safety_guard_class="fail_closed_earlier")
    assert r.safety_guard_class == "fail_closed_earlier"


def test_result_estimated_delta_hours_accepts_none() -> None:
    """Per doc-18:88 :attr:`estimated_delta_hours` is ``float | None``;
    ``None`` is a valid value (the metric is not quantified).
    """

    r = _result(estimated_delta_hours=None)
    assert r.estimated_delta_hours is None


def test_result_estimated_delta_repair_cycles_accepts_none() -> None:
    """Per doc-18:89 :attr:`estimated_delta_repair_cycles` is
    ``float | None``; ``None`` is a valid value.
    """

    r = _result(estimated_delta_repair_cycles=None)
    assert r.estimated_delta_repair_cycles is None


def test_result_estimated_delta_commit_failures_accepts_none() -> None:
    """Per doc-18:90 :attr:`estimated_delta_commit_failures` is
    ``float | None``; ``None`` is a valid value.
    """

    r = _result(estimated_delta_commit_failures=None)
    assert r.estimated_delta_commit_failures is None


def test_result_estimated_risk_change_annotation_is_direct_risk_change_literal() -> None:
    """**Slice 18 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`CounterfactualResult.estimated_risk_change` field MUST
    be typed against the DIRECT :data:`RiskChange` Literal (NOT a
    re-aliased copy).
    """

    annotation = CounterfactualResult.model_fields[
        "estimated_risk_change"
    ].annotation
    assert annotation is RiskChange
    assert set(get_args(annotation)) == {
        "lower",
        "same",
        "higher",
        "unknown",
    }


def test_result_recommended_next_step_annotation_is_direct_recommended_next_step_literal() -> None:
    """**Slice 18 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`CounterfactualResult.recommended_next_step` field MUST
    be typed against the DIRECT :data:`RecommendedNextStep` Literal
    (NOT a re-aliased copy).
    """

    annotation = CounterfactualResult.model_fields[
        "recommended_next_step"
    ].annotation
    assert annotation is RecommendedNextStep
    assert set(get_args(annotation)) == {
        "discard",
        "collect_more_evidence",
        "draft_policy",
        "implementation_plan",
    }


def test_result_assumptions_accepts_empty_list() -> None:
    """Per doc-18:84 the typed surface accepts the empty assumptions
    list at construction. Per doc-18:163 AC2 *"Every result lists
    assumptions and validity limits."* the future Slice 18 6th
    sub-slice writer enforces the AC2 invariant; the typed-shape layer
    accepts the empty list.
    """

    r = _result(assumptions=[])
    assert r.assumptions == []


def test_result_validity_limits_accepts_empty_list() -> None:
    """Per doc-18:85 the typed surface accepts the empty validity-limits
    list at construction. Per doc-18:163 AC2 the future Slice 18 6th
    sub-slice writer enforces the AC2 invariant.
    """

    r = _result(validity_limits=[])
    assert r.validity_limits == []


def test_result_invalidated_by_accepts_empty_list() -> None:
    """Per doc-18:93 the typed surface accepts the empty
    invalidated-by list at construction (the typical case for a
    valid result; the invalidation-reason list is only populated for
    invalidated results per doc-18:134-137 edge cases).
    """

    r = _result(invalidated_by=[])
    assert r.invalidated_by == []


def test_result_invalidated_by_accepts_multiple_reasons() -> None:
    """Per doc-18:134-137 multiple invalidation reasons may combine on
    one result (e.g. missing typed timing + product defect window +
    governance-only provenance chain).
    """

    reasons = [
        "missing_evidence:typed_attempt",
        "product_defect_window",
        "governance_only_provenance_chain",
    ]
    r = _result(invalidated_by=reasons)
    assert r.invalidated_by == reasons


def test_result_supporting_finding_ids_accepts_string_list() -> None:
    """Per doc-18:94 :attr:`supporting_finding_ids` is ``list[str]``
    (NOT a list of the typed Slice 16 :class:`GovernanceFinding`
    BaseModel; just the :attr:`GovernanceFinding.idempotency_key`
    STRINGS per the by-name reference contract).
    """

    annotation = CounterfactualResult.model_fields[
        "supporting_finding_ids"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is str


def test_result_supporting_finding_ids_accepts_empty_list() -> None:
    """Per doc-18:94 the typed surface accepts the empty
    supporting-finding-ids list at construction (some results may not
    cite supporting findings, e.g. when the result is
    ``invalidated_by`` an evidence gap).
    """

    r = _result(supporting_finding_ids=[])
    assert r.supporting_finding_ids == []


def test_result_supporting_finding_ids_references_slice_16_idempotency_key_string_shape() -> None:
    """Per the implementer prompt § "Reuse contracts" the
    :attr:`supporting_finding_ids` field references Slice 16
    :attr:`GovernanceFinding.idempotency_key` STRINGS (per
    ``finding_engine.py:443`` the field is typed as ``str``); the
    typed surface enforces the by-name reference shape at construction.
    """

    from iriai_build_v2.execution_control.finding_engine import (
        GovernanceFinding,
    )

    # Slice 16 GovernanceFinding.idempotency_key field is typed as str.
    finding_id_annotation = GovernanceFinding.model_fields[
        "idempotency_key"
    ].annotation
    assert finding_id_annotation is str

    # Slice 18 supporting_finding_ids field is typed as list[str] (matching
    # the by-name reference shape).
    annotation = CounterfactualResult.model_fields[
        "supporting_finding_ids"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert args[0] is str

    # A real Slice 16 finding idempotency_key string round-trips into
    # the Slice 18 supporting_finding_ids field.
    r = _result(supporting_finding_ids=["finding-key-abc123"])
    assert r.supporting_finding_ids == ["finding-key-abc123"]


def test_result_confidence_accepts_floats_between_0_and_1() -> None:
    """Per doc-18:92 :attr:`confidence` is a float; the typical range
    is 0.0 to 1.0. The typed surface accepts any float (the per-mode
    confidence-floor enforcement lives in the future Slice 18 5th
    sub-slice metrics-comparator).
    """

    for v in (0.0, 0.25, 0.5, 0.75, 1.0):
        r = _result(confidence=v)
        assert r.confidence == v


# ── Slice 13a shared model identity (DIRECT annotation-identity) ───────────


def test_result_policy_provenance_refs_annotation_is_list_of_slice_13a_governance_evidence_ref() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-18:186-249).

    Per the implementer prompt § "Non-negotiables" this is the STRONGER
    pattern Slice 14 V3 reviewer flagged in P3-V3-2 CARRY + the pattern
    Slice 15 1st sub-slice + Slice 16 1st sub-slice + Slice 17 1st
    sub-slice adopted: the
    :attr:`CounterfactualResult.policy_provenance_refs` field MUST be
    typed against ``list[GovernanceEvidenceRef]`` where
    :class:`GovernanceEvidenceRef` IS the Slice 13a shared model
    imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models` -- NOT
    redefined here. Assert the annotation resolves to
    ``list[GovernanceEvidenceRef]`` via DIRECT identity comparison on
    the list element type.

    Per the implementer-prompt typed-REUSE binding this is a STRONGER
    contract than doc-18:86 ``list[str]`` -- the governance-evidence-
    ref surface IS the Slice 13a typed BaseModel.
    """

    annotation = CounterfactualResult.model_fields[
        "policy_provenance_refs"
    ].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 13a shared class via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_result_policy_provenance_refs_is_imported_from_slice_13a() -> None:
    """Per doc-13a:285-287 step 9 + doc-18:186-249 the
    :class:`GovernanceEvidenceRef` consumed by Slice 18 IS the Slice 13a
    shared class (identity-equal via the direct import) -- NOT a
    second copy.
    """

    # The Slice 13a import path.
    from iriai_build_v2.workflows.develop.governance.models import (
        GovernanceEvidenceRef as Slice13aGovernanceEvidenceRef,
    )

    # The Slice 18 binding (via the annotation on
    # ``policy_provenance_refs``).
    args = get_args(
        CounterfactualResult.model_fields[
            "policy_provenance_refs"
        ].annotation
    )
    assert len(args) == 1
    # The list element type IS the Slice 13a class (identity).
    assert args[0] is Slice13aGovernanceEvidenceRef


def test_result_policy_provenance_refs_accepts_multiple_evidence_refs() -> None:
    """Per doc-18:86 the typed surface accepts multiple Slice 13a
    evidence refs (the typical case for a result that cites several
    evidence sources).
    """

    refs = [
        _evidence_ref(ref_id="ref-18-a"),
        _evidence_ref(ref_id="ref-18-b"),
        _evidence_ref(ref_id="ref-18-c"),
    ]
    r = _result(policy_provenance_refs=refs)
    assert len(r.policy_provenance_refs) == 3


# ── Slice 14 P3-V3-2 lineage (stronger pattern; meta-assertion) ────────────


def test_slice_14_p3_v3_2_stronger_pattern_evidence_ref_annotation_identity() -> None:
    """Meta-test re-pinning the stronger P3-V3-2 pattern (Slice 13a
    REUSE).

    Per the Slice 14 close-out (P3-V3-2 CARRY) + the Slice 15 1st
    sub-slice + Slice 16 1st sub-slice + Slice 17 1st sub-slice (which
    all adopted the stronger pattern) the Slice 18 1st sub-slice
    continues the stronger pattern of asserting Slice 13a shared model
    identity via DIRECT
    ``get_args(... .annotation)[0] is GovernanceEvidenceRef``
    comparison rather than the indirect value-set + namespace
    assertions used in Slice 14 1st sub-slice tests at
    ``tests/test_execution_control_commit_provenance.py:718``.

    This meta-test is the cross-Slice-15 / Slice-16 / Slice-17 /
    Slice-18 anchor that future Slice 18 sub-slices grow against; the
    pattern is now the canonical REUSE-assertion idiom across the
    governance phase.
    """

    annotation = CounterfactualResult.model_fields[
        "policy_provenance_refs"
    ].annotation
    # The stronger pattern (DIRECT identity, NOT indirect value-set).
    assert get_args(annotation)[0] is GovernanceEvidenceRef


def test_slice_18_p3_v3_2_stronger_pattern_policy_consumer_annotation_identity() -> None:
    """Meta-test re-pinning the stronger P3-V3-2 pattern (Slice 17
    REUSE).

    Per the implementer prompt the Slice 18 1st sub-slice extends the
    stronger P3-V3-2 pattern to the Slice 17 REUSE on
    :attr:`CounterfactualScenario.affected_consumers` -- the DIRECT
    annotation-identity assertion via
    ``get_args(... .annotation)[0] is PolicyConsumer``.
    """

    annotation = CounterfactualScenario.model_fields[
        "affected_consumers"
    ].annotation
    # The stronger pattern (DIRECT identity, NOT indirect value-set).
    assert get_args(annotation)[0] is PolicyConsumer


# ── ConfigDict(extra="forbid") discipline (3 BaseModels) ───────────────────


@pytest.mark.parametrize(
    "model_cls",
    [
        ReplayCorpus,
        CounterfactualScenario,
        CounterfactualResult,
    ],
)
def test_all_3_base_models_carry_extra_forbid(model_cls: type) -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 + Slice 17
    precedent all Slice 18 typed BaseModels carry ``model_config =
    ConfigDict(extra="forbid")`` so typo-d kwargs raise a typed
    ``ValidationError`` rather than being silently absorbed.
    """

    assert model_cls.model_config.get("extra") == "forbid"


# ── canonical_counterfactual_dict +
#    compute_counterfactual_idempotency_key ──────────────────────────────────


def test_canonical_counterfactual_dict_is_json_serialisable() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 + Slice 16 + Slice 17
    canonical-JSON discipline the canonical-dict projection of a
    :class:`CounterfactualResult` is JSON-serialisable.
    """

    import json as _json

    r = _result()
    d = canonical_counterfactual_dict(r)
    assert isinstance(d, dict)
    # The dict roundtrips through json.dumps (proves all values
    # serialise).
    _json.dumps(d, sort_keys=True)


def test_canonical_counterfactual_dict_is_deterministic() -> None:
    """Two calls with the same logical :class:`CounterfactualResult`
    MUST produce equal canonical dicts (the cross-process determinism
    contract :func:`compute_counterfactual_idempotency_key` relies on).
    """

    r1 = _result()
    r2 = _result()
    assert canonical_counterfactual_dict(r1) == (
        canonical_counterfactual_dict(r2)
    )


def test_canonical_counterfactual_dict_round_trip_stable() -> None:
    """A :class:`CounterfactualResult` -> canonical dict -> JSON -> dict
    round-trip preserves the canonical dict (no key reordering).
    """

    import json as _json

    r = _result()
    d = canonical_counterfactual_dict(r)
    j = _json.dumps(d, sort_keys=True)
    d2 = _json.loads(j)
    assert d == d2


def test_compute_counterfactual_idempotency_key_is_deterministic() -> None:
    """The helper MUST produce identical keys for identical inputs
    (the cross-process freshness contract mirroring Slice 17 1st
    sub-slice :func:`compute_policy_recommendation_idempotency_key`).
    """

    args: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=["product_defect_independent_of_wave_size"],
        validity_limits=["sample_size<10"],
        supporting_finding_ids=["finding-key-abc123"],
    )
    k1 = compute_counterfactual_idempotency_key(**args)  # type: ignore[arg-type]
    k2 = compute_counterfactual_idempotency_key(**args)  # type: ignore[arg-type]
    assert k1 == k2
    # And the key is a 64-char SHA-256 hex digest.
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)


def test_compute_counterfactual_idempotency_key_differs_on_result_version_change() -> None:
    """**Doc-18:128-129 binding statement enforcement.** Per
    doc-18:128-129 *"New assumptions require a new result version."*
    a different :attr:`result_version` MUST produce a different key
    (the version axis is part of the dedupe key).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["result_version"] = "v2"
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_counterfactual_idempotency_key_differs_on_scenario_id_change() -> None:
    """A different :attr:`scenario_id` MUST produce a different key
    (the scenario id is one of the immutability anchors per
    doc-18:127-129).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["scenario_id"] = "scenario-18-2"
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_counterfactual_idempotency_key_differs_on_corpus_id_change() -> None:
    """A different :attr:`corpus_id` MUST produce a different key (the
    corpus id is one of the immutability anchors per doc-18:127-129).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["corpus_id"] = "corpus-18-2"
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_counterfactual_idempotency_key_differs_on_assumptions_change() -> None:
    """A different :attr:`assumptions` list MUST produce a different
    key (the dedupe-key dimension).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=["assumption-a"],
        validity_limits=[],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["assumptions"] = ["assumption-b"]
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_counterfactual_idempotency_key_differs_on_validity_limits_change() -> None:
    """A different :attr:`validity_limits` list MUST produce a different
    key (the dedupe-key dimension).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=["limit-a"],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["validity_limits"] = ["limit-b"]
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_counterfactual_idempotency_key_differs_on_supporting_finding_ids_change() -> None:
    """A different :attr:`supporting_finding_ids` list MUST produce a
    different key (the dedupe-key dimension).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        supporting_finding_ids=["finding-a"],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["supporting_finding_ids"] = ["finding-b"]
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_counterfactual_idempotency_key_is_order_invariant_on_assumptions() -> None:
    """Per the Slice 17 1st sub-slice
    :func:`compute_policy_recommendation_idempotency_key`
    order-invariance pattern the key MUST be order-invariant w.r.t.
    assumptions ordering (the helper sorts the list before digesting).
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=["assumption-a", "assumption-b", "assumption-c"],
        validity_limits=[],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["assumptions"] = ["assumption-c", "assumption-b", "assumption-a"]
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_counterfactual_idempotency_key_is_order_invariant_on_validity_limits() -> None:
    """The key MUST be order-invariant w.r.t. validity-limits ordering.
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=["limit-a", "limit-b", "limit-c"],
        supporting_finding_ids=[],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["validity_limits"] = ["limit-c", "limit-a", "limit-b"]
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_counterfactual_idempotency_key_is_order_invariant_on_supporting_finding_ids() -> None:
    """The key MUST be order-invariant w.r.t. supporting-finding-id
    ordering.
    """

    base: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        supporting_finding_ids=["finding-a", "finding-b", "finding-c"],
    )
    k1 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    base["supporting_finding_ids"] = ["finding-c", "finding-b", "finding-a"]
    k2 = compute_counterfactual_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_counterfactual_idempotency_key_accepts_empty_lists() -> None:
    """The helper MUST accept all-empty list inputs and produce a
    stable key (the typed empty-list case per doc-18:84-85 + 94).
    """

    args: dict[str, object] = dict(
        result_version="v1",
        scenario_id="scenario-18-1",
        corpus_id="corpus-18-1",
        assumptions=[],
        validity_limits=[],
        supporting_finding_ids=[],
    )
    k = compute_counterfactual_idempotency_key(**args)  # type: ignore[arg-type]
    assert isinstance(k, str)
    assert len(k) == 64


# ── doc-18:186-249 Slice 13A consumption awareness ─────────────────────────


def test_doc_18_186_249_no_local_completeness_state_redefinition() -> None:
    """Per doc-18:186-249 Slice 13A Shared Completeness Model Dependency:
    Slice 18 future sub-slices consume the shared
    :data:`CompletenessState` Literal + :class:`EvidenceCompleteness`
    BaseModel from :mod:`iriai_build_v2.execution_control.completeness`,
    NOT a local redefinition.

    This 1st sub-slice does not yet wire those typed shapes into the
    counterfactual-replay surface (that lives in subsequent sub-slices);
    the discipline is asserted at the test-file level by checking the
    module's source for no local ``CompletenessState`` /
    ``Authoritative*`` class statements.
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    forbidden_redefinitions = (
        "class CompletenessState",
        "class EvidenceCompleteness",
        "class AuthoritativePromptContextRouting",
        "class AuthoritativeGateCompanionRecord",
        "class AuthoritativeGateProofRow",
        "class AuthoritativeSnapshotListFieldCompleteness",
        "class AuthoritativeSnapshotClassifierRouting",
        "class ExactEvidenceManifest",
        "class AuthoritativeContextRef",
        "class EvidencePageRef",
        # Slice 17 / Slice 16 / Slice 15 typed BaseModel redefinitions
        # also forbidden (the by-name reference / typed REUSE shapes
        # are sufficient per doc-18:75 + doc-18:94).
        "class GovernanceFinding",
        "class GovernanceMetricValue",
        "class GovernancePolicyRecommendation",
        "class PolicyRecommendationDecision",
    )
    for forbidden in forbidden_redefinitions:
        assert forbidden not in src, (
            f"counterfactual_replay.py must not redefine the Slice "
            f"13A or Slice 15 or Slice 16 or Slice 17 shared shape "
            f"{forbidden!r}; consume it via direct import per "
            f"doc-13a:285-287 step 9 + doc-18:186-249 + doc-18:94 "
            f"by-name reference contract."
        )

    # Also forbid local Literal aliases of the Slice 13A / Slice 17
    # shape names.
    forbidden_aliases = (
        "CompletenessState = Literal",
        "PolicyConsumer = Literal",
        "PolicyRecommendationStatus = Literal",
    )
    for forbidden in forbidden_aliases:
        assert forbidden not in src, (
            f"counterfactual_replay.py must not locally re-alias the "
            f"Slice 13A or Slice 17 shared shape {forbidden!r}."
        )


# ── doc-18:123-129 persistence + replay-results-only awareness ─────────────


def test_doc_18_123_129_no_consumer_activation_wiring_in_1st_sub_slice() -> None:
    """**Doc-18:123-125 persistence + artifact compatibility awareness
    assertion.**

    Per doc-18:123-125 *"Replay results are review/governance artifacts
    only. Replay must not write `dag-*` execution authority artifacts
    or active policy markers."* + STATUS.md § "Loop discipline"
    activation-authority-boundary note *"replay results are
    review/governance artifacts only, never runtime policy authority"*
    -- the Slice 18 1st sub-slice MUST NOT wire any consumer activation
    rules; the typed-shape foundation exposes only the audit-trail
    surface for the replay-result lifecycle.

    This test asserts the 1st sub-slice is pure typed-shape; the
    module source does NOT carry any consumer activation logic (no
    ``activate_*`` / ``apply_*`` / ``commit_*`` function definitions
    that would mutate a consumer surface).
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    # Forbidden top-level function names that would imply
    # consumer-activation wiring (the 1st sub-slice is pure typed-shape;
    # activation belongs to the consumer-owned modules per
    # doc-17:147-163 + the Slice 17 7th sub-slice activation-boundary
    # discipline).
    forbidden_activation_function_defs = (
        "def activate_result",
        "def activate_scenario",
        "def activate_corpus",
        "def activate_policy",
        "def apply_result",
        "def apply_scenario",
        "def apply_counterfactual",
        "def commit_result",
        "def commit_scenario",
        "def commit_counterfactual",
        "def write_route_table_update",
        "def write_scheduler_policy",
        "def write_merge_queue_policy",
        "def mutate_recommendation",
    )
    for f in forbidden_activation_function_defs:
        assert f not in src, (
            f"counterfactual_replay.py 1st sub-slice must not wire "
            f"consumer activation; found {f!r}. Per doc-18:123-125 "
            f"replay results are review/governance artifacts only, "
            f"never runtime policy authority."
        )


def test_doc_18_123_125_no_local_dag_authority_artifact_keys() -> None:
    """**Doc-18:123-125 persistence + artifact compatibility awareness.**

    Per doc-18:123-125 *"Replay results are review/governance artifacts
    only. Replay must not write `dag-*` execution authority artifacts
    or active policy markers."* -- the Slice 18 1st sub-slice
    typed-shape module MUST NOT carry any
    ``dag-*`` execution-authority artifact-key literals (those would
    imply the typed-shape module is writing consumer authority
    artifacts, which violates doc-18:123-125).

    The (future) Slice 18 6th sub-slice result writer at
    ``review:governance-counterfactuals:{corpus_id}`` may carry the
    review artifact-key literal; this 1st sub-slice is pure typed-shape
    and MUST NOT carry consumer-authority artifact keys.
    """

    import iriai_build_v2.execution_control.counterfactual_replay as mod

    src = open(mod.__file__).read()
    forbidden_consumer_artifact_keys = (
        '"dag-regroup-active:',
        '"dag-regroup-overlay:',
        '"route-budget:',
        '"supervisor-action:',
        '"merge-queue:',
    )
    for f in forbidden_consumer_artifact_keys:
        assert f not in src, (
            f"counterfactual_replay.py 1st sub-slice must not carry "
            f"consumer-owned artifact-key literal {f!r}; per "
            f"doc-18:123-125 the replay surface does NOT write "
            f"consumer authority artifacts."
        )


# ── doc-18:160-168 Acceptance Criteria awareness ───────────────────────────


def test_doc_18_162_ac1_deterministic_versioned_evidence_backed_surface_present() -> None:
    """**Doc-18:162 AC1 binding statement awareness.** Per doc-18:162
    *"Counterfactuals are deterministic, versioned, and
    evidence-backed."* the typed surface MUST expose all 3 axes:

    * **Deterministic** -- via
      :func:`compute_counterfactual_idempotency_key` (canonical-JSON
      + SHA-256 helper).
    * **Versioned** -- via :attr:`CounterfactualResult.result_version`
      (typed string field per doc-18:81).
    * **Evidence-backed** -- via
      :attr:`CounterfactualResult.policy_provenance_refs` (typed
      Slice 13a :class:`GovernanceEvidenceRef` list per doc-18:86).

    This test PINs the 3-axis surface at the typed-shape layer; the
    future Slice 18 6th sub-slice writer enforces the AC1 contract
    operationally.
    """

    # 1. Deterministic axis -- the helper exists + is callable.
    assert callable(compute_counterfactual_idempotency_key)

    # 2. Versioned axis -- result_version is a typed str field.
    annotation = CounterfactualResult.model_fields[
        "result_version"
    ].annotation
    assert annotation is str

    # 3. Evidence-backed axis -- policy_provenance_refs is a typed
    # list of Slice 13a GovernanceEvidenceRef.
    pp_annotation = CounterfactualResult.model_fields[
        "policy_provenance_refs"
    ].annotation
    assert get_origin(pp_annotation) is list
    assert get_args(pp_annotation)[0] is GovernanceEvidenceRef


def test_doc_18_163_ac2_assumptions_and_validity_limits_surface_present() -> None:
    """**Doc-18:163 AC2 binding statement awareness.** Per doc-18:163
    *"Every result lists assumptions and validity limits."* the typed
    surface MUST expose both fields on :class:`CounterfactualResult`:

    * :attr:`assumptions: list[str]` per doc-18:84.
    * :attr:`validity_limits: list[str]` per doc-18:85.

    The typed-shape layer accepts the empty list at construction; the
    future Slice 18 6th sub-slice writer enforces the AC2
    non-empty-list invariant operationally.
    """

    # Both fields exist on CounterfactualResult.
    assert "assumptions" in CounterfactualResult.model_fields
    assert "validity_limits" in CounterfactualResult.model_fields

    # Both are typed as list[str].
    a_annotation = CounterfactualResult.model_fields["assumptions"].annotation
    assert get_origin(a_annotation) is list
    assert get_args(a_annotation)[0] is str

    v_annotation = CounterfactualResult.model_fields[
        "validity_limits"
    ].annotation
    assert get_origin(v_annotation) is list
    assert get_args(v_annotation)[0] is str


def test_doc_18_164_ac3_no_mutation_methods_on_any_basemodel() -> None:
    """**Doc-18:164 AC3 binding statement awareness.** Per doc-18:164
    *"Replay cannot mutate live workflow state."* the typed surface
    MUST NOT expose mutation methods on any of the 3 BaseModels (the
    read-only typed-shape design is the AC3 axis at the typed-shape
    layer; the persistence-side AC3 enforcement lives in the future
    Slice 18 6th sub-slice writer).
    """

    forbidden_method_prefixes = (
        "activate_",
        "apply_",
        "commit_",
        "mutate_",
        "dispatch_",
        "schedule_",
        "write_route_table",
        "write_scheduler_policy",
        "write_merge_queue_policy",
    )
    for model_cls in (
        ReplayCorpus,
        CounterfactualScenario,
        CounterfactualResult,
    ):
        for attr in dir(model_cls):
            # Skip private + dunder + Pydantic-internal methods.
            if attr.startswith("_"):
                continue
            # Skip Pydantic BaseModel standard methods (model_*, etc.).
            if attr.startswith("model_"):
                continue
            # Skip Pydantic BaseModel standard methods (copy, dict, etc.).
            if attr in ("copy", "dict", "json", "construct"):
                continue
            for forbidden in forbidden_method_prefixes:
                assert not attr.startswith(forbidden), (
                    f"{model_cls.__name__} must not expose mutation "
                    f"method {attr!r} (per doc-18:164 AC3 *Replay "
                    f"cannot mutate live workflow state*)."
                )


def test_doc_18_167_168_ac5_feature_ids_and_implementation_anchors_present() -> None:
    """**Doc-18:167-168 AC5 binding statement awareness.** Per
    doc-18:167-168 *"The replay corpus includes both 8ac124d6 evidence
    and Slice 00-12 implementation artifacts."* the typed surface MUST
    expose both fields on :class:`ReplayCorpus`:

    * :attr:`feature_ids: list[str]` per doc-18:65 (carries
      ``"8ac124d6"`` for the canonical Slice 00 fixture).
    * :attr:`implementation_anchor_ids: list[str]` per doc-18:67
      (carries Slice 00-12 journal anchor strings).

    The typed-shape layer exposes both fields; the future Slice 18 2nd
    sub-slice corpus loader enforces the AC5 coverage contract
    operationally.
    """

    # Both fields exist on ReplayCorpus.
    assert "feature_ids" in ReplayCorpus.model_fields
    assert "implementation_anchor_ids" in ReplayCorpus.model_fields

    # Both are typed as list[str].
    f_annotation = ReplayCorpus.model_fields["feature_ids"].annotation
    assert get_origin(f_annotation) is list
    assert get_args(f_annotation)[0] is str

    i_annotation = ReplayCorpus.model_fields[
        "implementation_anchor_ids"
    ].annotation
    assert get_origin(i_annotation) is list
    assert get_args(i_annotation)[0] is str
