"""Slice 16 first sub-slice -- unit tests for the foundational
``execution_control/finding_engine.py`` typed-shape module.

Covers the 3 doc-16:62-80 Literals + the 2 doc-16:82-113 typed shapes +
the :data:`REQUIRED_V1_FINDING_CLASS_NAMES` 16-name tuple + the
canonical-JSON helper functions:

- :data:`FindingSeverity` -- 5 values per doc-16:62.
- :data:`FindingKind` -- 14 values per doc-16:63-78.
- :data:`FindingCausalRole` -- 4 values per doc-16:80.
- :class:`GovernanceFinding` -- 19+ fields per doc-16:82-104; Slice 13a
  shared ``GovernanceEvidenceRef`` consumption on
  ``primary_evidence_refs`` + ``supporting_evidence_refs`` (NOT
  redefined; DIRECT annotation-identity assertion via ``is``).
- :class:`FindingRule` -- 6 fields per doc-16:106-113.
- :data:`REQUIRED_V1_FINDING_CLASS_NAMES` -- 16-name tuple per
  doc-16:120-137 verbatim.
- :func:`compute_finding_idempotency_key` + :func:`canonical_finding_dict`
  -- canonical-JSON + SHA-256 helpers mirroring Slice 13A
  ``compute_completeness_digest`` + Slice 14 ``compute_payload_sha256``
  + Slice 15 ``compute_scorecard_digest`` discipline.

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
(P3-V3-2 CARRY) + the pattern Slice 15 1st sub-slice adopted at
``tests/test_execution_control_governance_metrics.py:578``.

**Slice 13A awareness asserted (doc-16:201-291).** No local
``CompletenessState`` / ``EvidenceCompleteness`` /
``AuthoritativePromptContextRouting`` /
``AuthoritativeGateCompanionRecord`` / ``AuthoritativeGateProofRow`` /
``AuthoritativeSnapshotListFieldCompleteness`` /
``AuthoritativeSnapshotClassifierRouting`` redefinition. The
finding-engine module exposes the typed surface that future Slice 16
sub-slices wire to the Slice 13A typed shapes; this 1st sub-slice
enforces the no-redefinition discipline at the test-file level.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15
modules + tests remain byte-identical.
"""

from __future__ import annotations

from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.finding_engine import (
    REQUIRED_V1_FINDING_CLASS_NAMES,
    FindingCausalRole,
    FindingKind,
    FindingRule,
    FindingSeverity,
    GovernanceFinding,
    canonical_finding_dict,
    compute_finding_idempotency_key,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the 3 Literals + 2 typed shapes +
    :data:`REQUIRED_V1_FINDING_CLASS_NAMES` tuple + 2 helpers.

    Per doc-16:62-113 the surface is:
    * 3 Literals (``FindingSeverity`` 5-value + ``FindingKind`` 14-value
      + ``FindingCausalRole`` 4-value).
    * 2 typed BaseModels (``GovernanceFinding`` 19+ fields +
      ``FindingRule`` 6 fields).
    * 1 tuple (:data:`REQUIRED_V1_FINDING_CLASS_NAMES` 16 values per
      doc-16:120-137).
    * 2 canonical-JSON helpers (``compute_finding_idempotency_key`` +
      ``canonical_finding_dict``).

    Total: 8 exported names.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    expected = {
        "FindingSeverity",
        "FindingKind",
        "FindingCausalRole",
        "GovernanceFinding",
        "FindingRule",
        "REQUIRED_V1_FINDING_CLASS_NAMES",
        "compute_finding_idempotency_key",
        "canonical_finding_dict",
    }
    assert set(mod.__all__) == expected
    assert len(expected) == 8
    for name in expected:
        assert hasattr(mod, name)


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """Per doc-13a:285-287 step 9 + doc-16:201-291 the Slice 16 module
    MUST NOT redefine :class:`GovernanceEvidenceRef` -- it consumes the
    Slice 13a shared model via direct import.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    # Module level: the only ``GovernanceEvidenceRef`` symbol in the
    # module namespace IS the Slice 13a one (imported via the
    # ``from .. import`` at the top of the module).
    assert getattr(mod, "GovernanceEvidenceRef", None) is None or (
        mod.GovernanceEvidenceRef is GovernanceEvidenceRef  # type: ignore[attr-defined]
    )


def test_module_does_not_redefine_completeness_state() -> None:
    """Per doc-16:201-291 Slice 13A Shared Completeness Model Dependency:
    the Slice 16 typed-shape module MUST NOT redefine the Slice 13A
    shared :data:`CompletenessState` Literal (4 values: ``complete`` /
    ``paged`` / ``preview_only`` / ``unavailable``).

    Future Slice 16 sub-slices that emit the
    ``implementation_journal_gap`` finding kind consume the Slice 13A
    shared shape from :mod:`iriai_build_v2.execution_control.completeness`
    via direct import.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    # No local ``CompletenessState`` symbol in the module namespace.
    assert getattr(mod, "CompletenessState", None) is None


def test_module_does_not_redefine_evidence_completeness() -> None:
    """Per doc-16:201-291 Slice 13A Shared Completeness Model Dependency:
    the Slice 16 typed-shape module MUST NOT redefine the Slice 13A
    shared :class:`EvidenceCompleteness` BaseModel.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    assert getattr(mod, "EvidenceCompleteness", None) is None


def test_module_does_not_redefine_authoritative_prompt_context_routing() -> None:
    """Per doc-16:256-261 Slice 13A 4th sub-slice prompt-context adapter
    dependency: the Slice 16 typed-shape module MUST NOT redefine the
    Slice 13A 4th sub-slice :class:`AuthoritativePromptContextRouting`
    BaseModel.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    assert getattr(mod, "AuthoritativePromptContextRouting", None) is None


def test_module_does_not_redefine_authoritative_gate_companion_record() -> None:
    """Per doc-16:262-267 Slice 13A 5th sub-slice gate-companion adapter
    dependency: the Slice 16 typed-shape module MUST NOT redefine the
    Slice 13A 5th sub-slice :class:`AuthoritativeGateCompanionRecord`
    BaseModel.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    assert getattr(mod, "AuthoritativeGateCompanionRecord", None) is None


def test_module_does_not_redefine_authoritative_gate_proof_row() -> None:
    """Per doc-16:262-267 Slice 13A 5th sub-slice gate-companion adapter
    dependency: the Slice 16 typed-shape module MUST NOT redefine the
    Slice 13A 5th sub-slice :class:`AuthoritativeGateProofRow`
    BaseModel.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    assert getattr(mod, "AuthoritativeGateProofRow", None) is None


def test_module_does_not_redefine_authoritative_snapshot_list_field_completeness() -> None:
    """Per doc-16:268-275 Slice 13A 6th sub-slice snapshot-companion
    adapter dependency: the Slice 16 typed-shape module MUST NOT
    redefine the Slice 13A 6th sub-slice
    :class:`AuthoritativeSnapshotListFieldCompleteness` BaseModel.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    assert getattr(mod, "AuthoritativeSnapshotListFieldCompleteness", None) is None


def test_module_does_not_redefine_authoritative_snapshot_classifier_routing() -> None:
    """Per doc-16:268-275 Slice 13A 6th sub-slice snapshot-companion
    adapter dependency: the Slice 16 typed-shape module MUST NOT
    redefine the Slice 13A 6th sub-slice
    :class:`AuthoritativeSnapshotClassifierRouting` BaseModel.
    """

    from iriai_build_v2.execution_control import finding_engine as mod

    assert getattr(mod, "AuthoritativeSnapshotClassifierRouting", None) is None


def test_module_import_discipline_no_implementation_py() -> None:
    """Per the implementer prompt § "Non-negotiables" the typed-shape
    module MUST NOT import from ``implementation.py`` -- that's the
    Slice 11 boundary module and would invert the dependency direction.
    """

    import iriai_build_v2.execution_control.finding_engine as mod

    src = open(mod.__file__).read()
    # No top-level import statement that mentions ``implementation``.
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") or stripped.startswith("import "):
            assert "implementation" not in stripped, (
                f"finding_engine.py must not import implementation.py "
                f"-- found {stripped!r}"
            )


def test_module_import_discipline_no_failure_router() -> None:
    """Per the implementer prompt § "Non-negotiables" the typed-shape
    module MUST NOT import from ``failure_router.py`` -- this 1st
    sub-slice has NO new failure ids and the typed shapes are pure
    BaseModels.
    """

    import iriai_build_v2.execution_control.finding_engine as mod

    src = open(mod.__file__).read()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") or stripped.startswith("import "):
            assert "failure_router" not in stripped, (
                f"finding_engine.py must not import failure_router "
                f"-- found {stripped!r}"
            )


def test_module_import_discipline_no_other_execution_control_modules() -> None:
    """Per the implementer prompt § "Non-negotiables" the typed-shape
    module MUST NOT import from other ``execution_control/`` modules --
    those would be downstream consumers, not dependencies.

    The only allowed sibling import is via the Slice 13a
    ``governance.models`` direct import (per doc-13a:285-287 step 9 +
    doc-16:201-291). Slice 13A shared shapes (``completeness.py``,
    ``dispatcher_prompt_context.py``, ``gate_companion.py``,
    ``snapshot_companion.py``) + Slice 14 (``commit_provenance*.py``) +
    Slice 15 (``governance_metrics.py`` etc.) are consumed by future
    Slice 16 sub-slices (the rule loader + emitter) -- this 1st
    sub-slice is the typed-shape foundation.
    """

    import iriai_build_v2.execution_control.finding_engine as mod

    src = open(mod.__file__).read()
    forbidden_siblings = (
        "execution_control.completeness",
        "execution_control.dispatcher_prompt_context",
        "execution_control.gate_companion",
        "execution_control.snapshot_companion",
        "execution_control.commit_provenance",
        "execution_control.governance_metrics",
        "execution_control.governance_metric_extractor",
        "execution_control.governance_scorecard_writer",
        "execution_control.store",
        "execution_control.atomic_landing",
        "execution_control.startup",
        "execution_control.adoption",
        "execution_control.models",
    )
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith("from ") or stripped.startswith("import "):
            for forbidden in forbidden_siblings:
                assert forbidden not in stripped, (
                    f"finding_engine.py (Slice 16 1st sub-slice typed-shape "
                    f"foundation) must not import sibling "
                    f"{forbidden} -- found {stripped!r}"
                )


def test_package_init_does_not_re_export_finding_engine() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 precedent the package
    ``execution_control/__init__.py`` does NOT re-export
    ``finding_engine.py`` -- consumers use the fully-qualified import
    (``from iriai_build_v2.execution_control.finding_engine import ...``).
    """

    from iriai_build_v2 import execution_control as pkg

    forbidden = {
        "FindingSeverity",
        "FindingKind",
        "FindingCausalRole",
        "GovernanceFinding",
        "FindingRule",
        "REQUIRED_V1_FINDING_CLASS_NAMES",
        "compute_finding_idempotency_key",
        "canonical_finding_dict",
    }
    for name in forbidden:
        assert name not in getattr(pkg, "__all__", []), (
            f"execution_control/__init__.py must not re-export {name!r} "
            f"per the Slice 13A + Slice 14 + Slice 15 precedent."
        )


# ── FindingSeverity (doc-16:62) ────────────────────────────────────────────


def test_finding_severity_is_5_value_literal() -> None:
    """Per doc-16:62 the :data:`FindingSeverity` Literal carries exactly
    5 values: ``info`` / ``low`` / ``medium`` / ``high`` / ``critical``.
    """

    args = get_args(FindingSeverity)
    assert len(args) == 5
    assert set(args) == {"info", "low", "medium", "high", "critical"}


@pytest.mark.parametrize(
    "severity",
    ["info", "low", "medium", "high", "critical"],
)
def test_finding_severity_accepts_all_5_values(severity: str) -> None:
    """Per doc-16:62 every one of the 5 Literal values is constructible
    on :attr:`GovernanceFinding.severity`.
    """

    f = _finding(severity=severity)
    assert f.severity == severity


def test_finding_severity_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown severity fails closed
    at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _finding(severity="catastrophic")


# ── FindingKind (doc-16:63-78) ─────────────────────────────────────────────


def test_finding_kind_is_14_value_literal() -> None:
    """Per doc-16:63-78 the :data:`FindingKind` Literal carries exactly
    14 values covering workflow drag, evidence/provenance gaps,
    implementation drift, resource/safety risk, and product defects.
    """

    args = get_args(FindingKind)
    assert len(args) == 14
    assert set(args) == {
        "workflow_inefficiency",
        "unsafe_route",
        "stale_projection",
        "over_verification",
        "under_verification",
        "task_contract_weakness",
        "scheduler_mismatch",
        "runtime_instability",
        "merge_queue_drag",
        "provenance_gap",
        "implementation_plan_deviation",
        "resource_safety_risk",
        "product_defect_cluster",
        "governance_evidence_conflict",
    }


@pytest.mark.parametrize(
    "kind",
    [
        "workflow_inefficiency",
        "unsafe_route",
        "stale_projection",
        "over_verification",
        "under_verification",
        "task_contract_weakness",
        "scheduler_mismatch",
        "runtime_instability",
        "merge_queue_drag",
        "provenance_gap",
        "implementation_plan_deviation",
        "resource_safety_risk",
        "product_defect_cluster",
        "governance_evidence_conflict",
    ],
)
def test_finding_kind_accepts_all_14_values(kind: str) -> None:
    """Per doc-16:63-78 every one of the 14 Literal values is
    constructible on :attr:`GovernanceFinding.kind`.
    """

    f = _finding(kind=kind)
    assert f.kind == kind


def test_finding_kind_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown kind fails closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _finding(kind="random_made_up_kind")


@pytest.mark.parametrize(
    "kind",
    [
        "workflow_inefficiency",
        "unsafe_route",
        "stale_projection",
        "over_verification",
        "under_verification",
        "task_contract_weakness",
        "scheduler_mismatch",
        "runtime_instability",
        "merge_queue_drag",
        "provenance_gap",
        "implementation_plan_deviation",
        "resource_safety_risk",
        "product_defect_cluster",
        "governance_evidence_conflict",
    ],
)
def test_finding_rule_emits_kind_accepts_all_14_values(kind: str) -> None:
    """Per doc-16:112-113 :attr:`FindingRule.emits_kind` is the same
    14-value :data:`FindingKind` Literal as :attr:`GovernanceFinding.kind`.
    """

    r = _rule(emits_kind=kind)
    assert r.emits_kind == kind


# ── FindingCausalRole (doc-16:80) ──────────────────────────────────────────


def test_finding_causal_role_is_4_value_literal() -> None:
    """Per doc-16:80 the :data:`FindingCausalRole` Literal carries
    exactly 4 values: ``primary`` / ``contributing`` / ``symptom`` /
    ``unknown``.
    """

    args = get_args(FindingCausalRole)
    assert len(args) == 4
    assert set(args) == {"primary", "contributing", "symptom", "unknown"}


@pytest.mark.parametrize(
    "role",
    ["primary", "contributing", "symptom", "unknown"],
)
def test_finding_causal_role_accepts_all_4_values(role: str) -> None:
    """Per doc-16:80 every one of the 4 Literal values is constructible
    on :attr:`GovernanceFinding.causal_role`.
    """

    f = _finding(causal_role=role)
    assert f.causal_role == role


def test_finding_causal_role_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown causal role fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _finding(causal_role="root_cause")


# ── REQUIRED_V1_FINDING_CLASS_NAMES (doc-16:120-137) ───────────────────────


def test_required_v1_finding_class_names_is_16_value_tuple() -> None:
    """Per doc-16:120-137 the v1 contract is exactly 16 finding class
    names.
    """

    assert isinstance(REQUIRED_V1_FINDING_CLASS_NAMES, tuple)
    assert len(REQUIRED_V1_FINDING_CLASS_NAMES) == 16


def test_required_v1_finding_class_names_matches_doc_16_120_137_verbatim() -> None:
    """Per doc-16:120-137 the v1 finding class names are exactly the
    following 16, in order.
    """

    expected = (
        "commit_hygiene_loop",
        "acl_or_writeability_drag",
        "worktree_alias_drift",
        "stale_context_projection",
        "runtime_provider_instability",
        "merge_queue_wait_or_retry_drag",
        "over_verification_low_risk_lane",
        "under_verification_high_risk_lane",
        "scheduler_wave_too_small",
        "scheduler_wave_too_large",
        "task_contract_ambiguity",
        "line_provenance_gap",
        "implementation_journal_gap",
        "accepted_plan_deviation",
        "resource_budget_pressure",
        "governance_evidence_conflict",
    )
    assert REQUIRED_V1_FINDING_CLASS_NAMES == expected


def test_required_v1_finding_class_names_are_unique() -> None:
    """The 16 v1 finding class names MUST be unique (no duplicates)."""

    assert len(set(REQUIRED_V1_FINDING_CLASS_NAMES)) == len(
        REQUIRED_V1_FINDING_CLASS_NAMES
    )


def test_required_v1_finding_class_names_all_strings() -> None:
    """All 16 v1 finding class names MUST be ``str`` instances."""

    for name in REQUIRED_V1_FINDING_CLASS_NAMES:
        assert isinstance(name, str)


def test_required_v1_finding_class_names_are_snake_case() -> None:
    """All 16 v1 finding class names MUST be snake_case (lowercase +
    underscores + no spaces / hyphens / camelCase).
    """

    for name in REQUIRED_V1_FINDING_CLASS_NAMES:
        assert name == name.lower(), f"{name!r} is not lowercase"
        assert " " not in name, f"{name!r} contains spaces"
        assert "-" not in name, f"{name!r} contains hyphens"


# ── GovernanceFinding helpers + constants ──────────────────────────────────


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


def _finding(**overrides: object) -> GovernanceFinding:
    """Construct a fully-specified :class:`GovernanceFinding` for tests."""

    base: dict[str, object] = dict(
        idempotency_key="finding-key-abc123",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk", "runtime": "claude-sdk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=["journal:2026-05-24#slice-15-1st"],
        metric_refs=["tasks_per_hour", "repair_cycles_per_task"],
        estimated_lost_hours=3.5,
        estimated_retry_impact=0.12,
        recommended_action_display="Consider tightening commit retry budget.",
        recommendation_draft_ref=None,
        safe_runtime_action=False,
        requires_policy_artifact=True,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
        primary_cause_finding_id=None,
        linked_finding_ids=[],
    )
    base.update(overrides)
    return GovernanceFinding(**base)


def _rule(**overrides: object) -> FindingRule:
    """Construct a fully-specified :class:`FindingRule` for tests."""

    base: dict[str, object] = dict(
        rule_id="commit_hygiene_loop_v1",
        version="v1",
        required_metric_names=["commit_failures_per_task"],
        required_evidence_kinds=["git_provenance"],
        min_confidence=0.7,
        emits_kind="workflow_inefficiency",
    )
    base.update(overrides)
    return FindingRule(**base)


# ── GovernanceFinding (doc-16:82-104) ──────────────────────────────────────


def test_finding_accepts_all_19_plus_fields() -> None:
    """The 19+ doc-16:82-104 fields all populate cleanly on a
    fully-specified :class:`GovernanceFinding`.

    Per doc-16:82-104 the surface is:
    idempotency_key, kind, class_name, severity, confidence, feature_id,
    affected_scope, primary_evidence_refs, supporting_evidence_refs,
    implementation_log_anchors, metric_refs, estimated_lost_hours,
    estimated_retry_impact, recommended_action_display,
    recommendation_draft_ref, safe_runtime_action,
    requires_policy_artifact, product_defect_related, workflow_related,
    causal_role, primary_cause_finding_id, linked_finding_ids = 22 fields.
    """

    f = _finding()
    assert f.idempotency_key == "finding-key-abc123"
    assert f.kind == "workflow_inefficiency"
    assert f.class_name == "commit_hygiene_loop"
    assert f.severity == "medium"
    assert f.confidence == 0.85
    assert f.feature_id == "8ac124d6"
    assert f.affected_scope == {"lane": "high_risk", "runtime": "claude-sdk"}
    assert len(f.primary_evidence_refs) == 1
    assert isinstance(f.primary_evidence_refs[0], GovernanceEvidenceRef)
    assert f.supporting_evidence_refs == []
    assert f.implementation_log_anchors == ["journal:2026-05-24#slice-15-1st"]
    assert f.metric_refs == ["tasks_per_hour", "repair_cycles_per_task"]
    assert f.estimated_lost_hours == 3.5
    assert f.estimated_retry_impact == 0.12
    assert f.recommended_action_display == (
        "Consider tightening commit retry budget."
    )
    assert f.recommendation_draft_ref is None
    assert f.safe_runtime_action is False
    assert f.requires_policy_artifact is True
    assert f.product_defect_related is False
    assert f.workflow_related is True
    assert f.causal_role == "primary"
    assert f.primary_cause_finding_id is None
    assert f.linked_finding_ids == []


def test_finding_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _finding(unknown_field="oops")  # type: ignore[arg-type]


def test_finding_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed
    :class:`GovernanceFinding` -> JSON -> :class:`GovernanceFinding`
    round-trip is value-equivalent.
    """

    f = _finding()
    j = f.model_dump_json()
    f2 = GovernanceFinding.model_validate_json(j)
    assert f == f2


def test_finding_feature_id_accepts_none() -> None:
    """Per doc-16:88 :attr:`GovernanceFinding.feature_id` is
    ``str | None`` -- cross-feature findings carry ``None``.
    """

    f = _finding(feature_id=None)
    assert f.feature_id is None


def test_finding_estimated_lost_hours_accepts_none() -> None:
    """Per doc-16:94 :attr:`GovernanceFinding.estimated_lost_hours` is
    ``float | None`` -- non-quantified findings carry ``None``.
    """

    f = _finding(estimated_lost_hours=None)
    assert f.estimated_lost_hours is None


def test_finding_estimated_retry_impact_accepts_none() -> None:
    """Per doc-16:95 :attr:`GovernanceFinding.estimated_retry_impact` is
    ``float | None`` -- non-quantified findings carry ``None``.
    """

    f = _finding(estimated_retry_impact=None)
    assert f.estimated_retry_impact is None


def test_finding_recommendation_draft_ref_defaults_to_none() -> None:
    """Per doc-16:97 :attr:`GovernanceFinding.recommendation_draft_ref`
    defaults to ``None`` (no recommendation draft authored yet -- the
    advisory-only default per doc-16:46-51).
    """

    base: dict[str, object] = dict(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.5,
        feature_id=None,
        affected_scope={},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=[],
        metric_refs=[],
        estimated_lost_hours=None,
        estimated_retry_impact=None,
        recommended_action_display="display only",
        safe_runtime_action=True,
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="unknown",
    )
    f = GovernanceFinding(**base)
    assert f.recommendation_draft_ref is None


def test_finding_primary_cause_finding_id_defaults_to_none() -> None:
    """Per doc-16:103 :attr:`GovernanceFinding.primary_cause_finding_id`
    defaults to ``None`` (the finding IS the primary cause unless it
    points elsewhere).
    """

    base: dict[str, object] = dict(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.5,
        feature_id=None,
        affected_scope={},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=[],
        metric_refs=[],
        estimated_lost_hours=None,
        estimated_retry_impact=None,
        recommended_action_display="display only",
        safe_runtime_action=True,
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
    )
    f = GovernanceFinding(**base)
    assert f.primary_cause_finding_id is None


def test_finding_linked_finding_ids_defaults_to_empty_list() -> None:
    """Per doc-16:104 :attr:`GovernanceFinding.linked_finding_ids`
    defaults to ``[]`` (no linked siblings).
    """

    base: dict[str, object] = dict(
        idempotency_key="k",
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.5,
        feature_id=None,
        affected_scope={},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=[],
        metric_refs=[],
        estimated_lost_hours=None,
        estimated_retry_impact=None,
        recommended_action_display="display only",
        safe_runtime_action=True,
        requires_policy_artifact=False,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
    )
    f = GovernanceFinding(**base)
    assert f.linked_finding_ids == []


def test_finding_affected_scope_accepts_empty_dict() -> None:
    """Per doc-16:89 :attr:`GovernanceFinding.affected_scope` is a
    free-form ``dict[str, Any]``; the empty dict is valid (e.g. for
    feature-wide findings without lane/runtime dimensions).
    """

    f = _finding(affected_scope={})
    assert f.affected_scope == {}


def test_finding_primary_evidence_refs_accepts_empty_list() -> None:
    """Per doc-16:90 the typed surface accepts the empty list at
    construction. Per doc-16:159-161 the at-least-one-primary-ref
    invariant lives in the (future) Slice 16 emitter, NOT in this
    typed-shape foundation; the BaseModel itself accepts the empty list
    so evidence-gap findings (per doc-16:161 *"unless it is explicitly
    an evidence-gap finding"*) can be constructed.
    """

    f = _finding(primary_evidence_refs=[])
    assert f.primary_evidence_refs == []


def test_finding_supporting_evidence_refs_accepts_empty_list() -> None:
    """Per doc-16:91 supporting evidence refs MAY be empty; a finding's
    primary refs satisfy the at-least-one-primary invariant.
    """

    f = _finding(supporting_evidence_refs=[])
    assert f.supporting_evidence_refs == []


def test_finding_implementation_log_anchors_accepts_empty_list() -> None:
    """Per doc-16:92 + doc-16:191-192 an empty log-anchor list is the
    typed trigger condition for the ``implementation_journal_gap``
    finding kind; the BaseModel accepts the empty list at construction.
    """

    f = _finding(implementation_log_anchors=[])
    assert f.implementation_log_anchors == []


def test_finding_metric_refs_accepts_empty_list() -> None:
    """Per doc-16:93 :attr:`GovernanceFinding.metric_refs` is
    ``list[str]`` (just metric NAMES; not the typed BaseModel); the
    empty list is valid for evidence-only findings that do not ground
    on metrics.
    """

    f = _finding(metric_refs=[])
    assert f.metric_refs == []


def test_finding_metric_refs_accepts_string_names_only() -> None:
    """Per doc-16:93 :attr:`GovernanceFinding.metric_refs` is
    ``list[str]`` (NOT a list of the typed
    :class:`~iriai_build_v2.execution_control.governance_metrics.GovernanceMetricValue`
    BaseModel; just metric NAMES).
    """

    annotation = GovernanceFinding.model_fields["metric_refs"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is str


def test_finding_product_defect_related_and_workflow_related_compatible() -> None:
    """Per doc-16:100-101 a finding MAY have both
    :attr:`product_defect_related=True` and :attr:`workflow_related=True`
    (the combined case per doc-16:187-190 is handled via the typed
    :attr:`causal_role` + :attr:`linked_finding_ids` fields).
    """

    f = _finding(product_defect_related=True, workflow_related=True)
    assert f.product_defect_related is True
    assert f.workflow_related is True


# ── Slice 13a shared model identity (DIRECT annotation-identity) ───────────


def test_finding_primary_evidence_refs_annotation_is_list_of_slice_13a_governance_evidence_ref() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-16:201-291).

    Per the implementer prompt § "Non-negotiables" this is the STRONGER
    pattern Slice 14 V3 reviewer flagged in P3-V3-2 CARRY at
    ``tests/test_execution_control_commit_provenance.py:718`` + the
    pattern Slice 15 1st sub-slice adopted at
    ``tests/test_execution_control_governance_metrics.py:578``:
    the :attr:`GovernanceFinding.primary_evidence_refs` field MUST be
    typed against ``list[GovernanceEvidenceRef]`` where
    :class:`GovernanceEvidenceRef` IS the Slice 13a shared model
    imported from
    :mod:`iriai_build_v2.workflows.develop.governance.models` -- NOT
    redefined here. Assert the annotation resolves to
    ``list[GovernanceEvidenceRef]`` via DIRECT identity comparison on
    the list element type.
    """

    annotation = GovernanceFinding.model_fields["primary_evidence_refs"].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 13a shared class via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_finding_supporting_evidence_refs_annotation_is_list_of_slice_13a_governance_evidence_ref() -> None:
    """**Slice 13a dependency reconciliation DIRECT annotation-identity
    assertion** (doc-13a:285-287 step 9 + doc-16:201-291).

    The :attr:`GovernanceFinding.supporting_evidence_refs` field MUST be
    typed against ``list[GovernanceEvidenceRef]`` where
    :class:`GovernanceEvidenceRef` IS the Slice 13a shared model. Same
    pattern as :attr:`primary_evidence_refs` (the stronger P3-V3-2
    pattern).
    """

    annotation = GovernanceFinding.model_fields["supporting_evidence_refs"].annotation
    # The annotation IS a parameterised list.
    assert get_origin(annotation) is list
    # The list element type IS the Slice 13a shared class via DIRECT
    # identity assertion (the stronger pattern).
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_finding_kind_annotation_is_direct_finding_kind_literal() -> None:
    """**Slice 16 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`GovernanceFinding.kind` field MUST be typed against the
    DIRECT :data:`FindingKind` Literal (NOT a re-aliased copy). This
    enforces the cross-Slice-16 surface invariant: the
    :attr:`FindingRule.emits_kind` field uses the same Literal alias so
    rule outputs are typed against the same Literal as finding
    declarations.
    """

    finding_annotation = GovernanceFinding.model_fields["kind"].annotation
    rule_emits_annotation = FindingRule.model_fields["emits_kind"].annotation
    # Both fields reference the SAME Literal (annotation identity).
    assert finding_annotation is FindingKind
    assert rule_emits_annotation is FindingKind
    # And the underlying Literal carries exactly the 14 values.
    assert set(get_args(finding_annotation)) == {
        "workflow_inefficiency",
        "unsafe_route",
        "stale_projection",
        "over_verification",
        "under_verification",
        "task_contract_weakness",
        "scheduler_mismatch",
        "runtime_instability",
        "merge_queue_drag",
        "provenance_gap",
        "implementation_plan_deviation",
        "resource_safety_risk",
        "product_defect_cluster",
        "governance_evidence_conflict",
    }


def test_finding_severity_annotation_is_direct_finding_severity_literal() -> None:
    """**Slice 16 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`GovernanceFinding.severity` field MUST be typed against
    the DIRECT :data:`FindingSeverity` Literal (NOT a re-aliased copy).
    """

    annotation = GovernanceFinding.model_fields["severity"].annotation
    assert annotation is FindingSeverity
    assert set(get_args(annotation)) == {
        "info",
        "low",
        "medium",
        "high",
        "critical",
    }


def test_finding_causal_role_annotation_is_direct_finding_causal_role_literal() -> None:
    """**Slice 16 typed-surface DIRECT annotation-identity assertion**.

    The :attr:`GovernanceFinding.causal_role` field MUST be typed
    against the DIRECT :data:`FindingCausalRole` Literal (NOT a
    re-aliased copy).
    """

    annotation = GovernanceFinding.model_fields["causal_role"].annotation
    assert annotation is FindingCausalRole
    assert set(get_args(annotation)) == {
        "primary",
        "contributing",
        "symptom",
        "unknown",
    }


# ── FindingRule (doc-16:106-113) ───────────────────────────────────────────


def test_rule_accepts_all_6_fields() -> None:
    """The 6 doc-16:107-113 fields all populate cleanly on a
    fully-specified :class:`FindingRule`.
    """

    r = _rule()
    assert r.rule_id == "commit_hygiene_loop_v1"
    assert r.version == "v1"
    assert r.required_metric_names == ["commit_failures_per_task"]
    assert r.required_evidence_kinds == ["git_provenance"]
    assert r.min_confidence == 0.7
    assert r.emits_kind == "workflow_inefficiency"


def test_rule_extra_forbid_rejects_unknown_field() -> None:
    """Per ``ConfigDict(extra="forbid")`` typo-d kwargs fail closed at
    construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _rule(unknown_field="oops")  # type: ignore[arg-type]


def test_rule_round_trips_via_json() -> None:
    """Per the Pydantic v2 idiom a typed
    :class:`FindingRule` -> JSON -> :class:`FindingRule` round-trip is
    value-equivalent.
    """

    r = _rule()
    j = r.model_dump_json()
    r2 = FindingRule.model_validate_json(j)
    assert r == r2


def test_rule_required_metric_names_accepts_empty_list() -> None:
    """Per doc-16:109 the typed surface accepts the empty list (rules
    that ground on evidence kinds only do not require any metric
    refs).
    """

    r = _rule(required_metric_names=[])
    assert r.required_metric_names == []


def test_rule_required_evidence_kinds_accepts_empty_list() -> None:
    """Per doc-16:110 the typed surface accepts the empty list (rules
    that ground on metric refs only do not require any evidence
    kinds).
    """

    r = _rule(required_evidence_kinds=[])
    assert r.required_evidence_kinds == []


def test_rule_emits_kind_rejects_unknown_value() -> None:
    """Per Pydantic Literal validation an unknown ``emits_kind`` fails
    closed at construction with a typed ``ValidationError``.
    """

    with pytest.raises(ValidationError):
        _rule(emits_kind="random_made_up_kind")


# ── ConfigDict(extra="forbid") discipline ──────────────────────────────────


@pytest.mark.parametrize(
    "model_cls",
    [GovernanceFinding, FindingRule],
)
def test_all_two_base_models_carry_extra_forbid(model_cls: type) -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 precedent all Slice 16
    typed BaseModels carry ``model_config = ConfigDict(extra="forbid")``
    so typo-d kwargs raise a typed ``ValidationError`` rather than
    being silently absorbed.
    """

    assert model_cls.model_config.get("extra") == "forbid"


# ── canonical_finding_dict + compute_finding_idempotency_key ───────────────


def test_canonical_finding_dict_is_json_serialisable() -> None:
    """Per the Slice 13A + Slice 14 + Slice 15 canonical-JSON discipline
    the canonical-dict projection of a :class:`GovernanceFinding` is
    JSON-serialisable (any nested ``datetime`` on the Slice 13a
    ``GovernanceEvidenceRef`` entries projects to ISO-8601 string).
    """

    import json as _json

    f = _finding()
    d = canonical_finding_dict(f)
    assert isinstance(d, dict)
    # The dict roundtrips through json.dumps (proves all values
    # serialise; any nested datetime lowered to ISO-8601 string).
    _json.dumps(d, sort_keys=True)


def test_canonical_finding_dict_is_deterministic() -> None:
    """Two calls with the same logical :class:`GovernanceFinding` MUST
    produce equal canonical dicts (the cross-process determinism
    contract :func:`compute_finding_idempotency_key` relies on).
    """

    f1 = _finding()
    f2 = _finding()
    assert canonical_finding_dict(f1) == canonical_finding_dict(f2)


def test_canonical_finding_dict_round_trip_stable() -> None:
    """A :class:`GovernanceFinding` -> canonical dict -> JSON -> dict
    round-trip preserves the canonical dict (no key reordering).
    """

    import json as _json

    f = _finding()
    d = canonical_finding_dict(f)
    j = _json.dumps(d, sort_keys=True)
    d2 = _json.loads(j)
    assert d == d2


def test_compute_finding_idempotency_key_is_deterministic() -> None:
    """Per doc-16:178 *"Finding ids are stable across reruns when input
    evidence and rule version do not change."* the helper MUST produce
    identical keys for identical inputs.
    """

    args: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1", "digest-2"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**args)  # type: ignore[arg-type]
    k2 = compute_finding_idempotency_key(**args)  # type: ignore[arg-type]
    assert k1 == k2
    # And the key is a 64-char SHA-256 hex digest.
    assert len(k1) == 64
    assert all(c in "0123456789abcdef" for c in k1)


def test_compute_finding_idempotency_key_differs_on_kind_change() -> None:
    """A different :data:`FindingKind` MUST produce a different key
    (the dedupe-key dimension per doc-16:158).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["kind"] = "unsafe_route"
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_finding_idempotency_key_differs_on_class_name_change() -> None:
    """A different :attr:`GovernanceFinding.class_name` MUST produce a
    different key (the dedupe-key dimension per doc-16:158).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["class_name"] = "stale_context_projection"
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_finding_idempotency_key_differs_on_feature_id_change() -> None:
    """A different :attr:`GovernanceFinding.feature_id` MUST produce a
    different key (the dedupe-key dimension per doc-16:158).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["feature_id"] = "different-feature"
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_finding_idempotency_key_differs_on_affected_scope_change() -> None:
    """A different :attr:`GovernanceFinding.affected_scope` MUST produce
    a different key (the dedupe-key dimension per doc-16:158).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["affected_scope"] = {"lane": "low_risk"}
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_finding_idempotency_key_differs_on_evidence_digest_change() -> None:
    """A different primary-evidence-digest list MUST produce a different
    key (the dedupe-key dimension per doc-16:158).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["primary_evidence_digests"] = ["digest-2"]
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_finding_idempotency_key_differs_on_rule_version_change() -> None:
    """Per doc-16:215-217 a rule version bump MUST produce a different
    key so older findings can be superseded rather than overwritten
    (the rollback/supersede path).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["rule_version"] = "v2"
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 != k2


def test_compute_finding_idempotency_key_is_order_invariant_on_evidence_digests() -> None:
    """Per the doc-16:178 cross-process determinism contract the key
    MUST be order-invariant w.r.t. evidence-ref ordering (the helper
    sorts the digest list before digesting).
    """

    base: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_digests=["digest-1", "digest-2", "digest-3"],
        rule_version="v1",
    )
    k1 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    base["primary_evidence_digests"] = ["digest-3", "digest-2", "digest-1"]
    k2 = compute_finding_idempotency_key(**base)  # type: ignore[arg-type]
    assert k1 == k2


def test_compute_finding_idempotency_key_accepts_none_feature_id() -> None:
    """Per doc-16:88 cross-feature findings carry ``feature_id=None``;
    the helper MUST accept ``None`` and produce a stable key.
    """

    args: dict[str, object] = dict(
        kind="workflow_inefficiency",
        class_name="commit_hygiene_loop",
        feature_id=None,
        affected_scope={},
        primary_evidence_digests=[],
        rule_version="v1",
    )
    k = compute_finding_idempotency_key(**args)  # type: ignore[arg-type]
    assert isinstance(k, str)
    assert len(k) == 64


# ── doc-16:201-291 Slice 13A consumption awareness ─────────────────────────


def test_doc_16_201_291_no_local_completeness_state_redefinition() -> None:
    """Per doc-16:201-291 Slice 13A Shared Completeness Model Dependency:
    Slice 16 future sub-slices consume the shared
    :data:`CompletenessState` Literal + :class:`EvidenceCompleteness`
    BaseModel from :mod:`iriai_build_v2.execution_control.completeness`,
    NOT a local redefinition.

    This 1st sub-slice does not yet wire those typed shapes into the
    finding-engine rule loader (that lives in subsequent sub-slices);
    the discipline is asserted at the test-file level by checking the
    module's source for no local ``CompletenessState`` / ``Authoritative*``
    class statements.
    """

    import iriai_build_v2.execution_control.finding_engine as mod

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
    )
    for forbidden in forbidden_redefinitions:
        assert forbidden not in src, (
            f"finding_engine.py must not redefine the Slice 13A shared "
            f"shape {forbidden!r}; consume it via direct import per "
            f"doc-13a:285-287 step 9 + doc-16:201-291."
        )

    # Also forbid local Literal aliases of the Slice 13A shape names
    # (the doc-16:201-291 binding statement rejects local redefinition
    # at the typed-shape level, not just at the class-statement level).
    forbidden_aliases = (
        "CompletenessState = Literal",
    )
    for forbidden in forbidden_aliases:
        assert forbidden not in src, (
            f"finding_engine.py must not locally re-alias the Slice 13A "
            f"shared shape {forbidden!r}."
        )


def test_doc_16_201_291_governance_evidence_ref_is_imported_from_slice_13a() -> None:
    """Per doc-13a:285-287 step 9 + doc-16:201-291 the
    :class:`GovernanceEvidenceRef` consumed by Slice 16 IS the Slice 13a
    shared class (identity-equal via the direct import) -- NOT a
    second copy.
    """

    # The Slice 13a import path.
    from iriai_build_v2.workflows.develop.governance.models import (
        GovernanceEvidenceRef as Slice13aGovernanceEvidenceRef,
    )

    # The Slice 16 finding-engine binding (via the annotation on
    # ``primary_evidence_refs``).
    args = get_args(GovernanceFinding.model_fields["primary_evidence_refs"].annotation)
    assert len(args) == 1
    # The list element type IS the Slice 13a class (identity).
    assert args[0] is Slice13aGovernanceEvidenceRef


# ── Slice 14 P3-V3-2 lineage (stronger pattern; meta-assertion) ────────────


def test_slice_14_p3_v3_2_stronger_pattern_evidence_ref_annotation_identity() -> None:
    """Meta-test re-pinning the stronger P3-V3-2 pattern.

    Per the Slice 14 close-out (P3-V3-2 CARRY) + the Slice 15 1st
    sub-slice (which adopted the stronger pattern; meta-pin at
    ``tests/test_execution_control_governance_metrics.py:834``) the
    Slice 16 1st sub-slice continues the stronger pattern of asserting
    Slice 13a shared model identity via DIRECT
    ``get_args(... .annotation)[0] is GovernanceEvidenceRef``
    comparison rather than the indirect value-set + namespace assertions
    used in Slice 14 1st sub-slice tests at
    ``tests/test_execution_control_commit_provenance.py:718``.

    This meta-test is the cross-Slice-15 / Slice-16 anchor that future
    Slice 16 sub-slices grow against; the pattern is now the canonical
    REUSE-assertion idiom across the governance phase.
    """

    annotation = GovernanceFinding.model_fields["primary_evidence_refs"].annotation
    # The stronger pattern (DIRECT identity, NOT indirect value-set).
    assert get_args(annotation)[0] is GovernanceEvidenceRef
