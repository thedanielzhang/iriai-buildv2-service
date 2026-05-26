"""Slice 16 2nd sub-slice -- unit tests for the
``execution_control/finding_rule_engine.py`` governance finding rule
loader + emitter module.

Covers:

- The 4 new typed shapes :class:`FindingSuppressionPolicy` +
  :class:`FindingExpiryPolicy` + :class:`FindingRuleEmissionInputs` +
  :class:`FindingRuleEmissionGap` (all carry
  ``ConfigDict(extra="forbid")``).
- The typed :data:`FINDING_RULE_EMISSION_FAILURE_ID` Literal.
- The typed :data:`EVIDENCE_GAP_FINDING_KINDS` 3-value tuple per
  doc-16:159-161.
- The 16-entry :data:`REQUIRED_V1_FINDING_RULES` tuple + the
  :data:`CLASS_NAME_TO_FINDING_KIND` mapping per doc-16:122-137 +
  doc-16:63-78.
- The :func:`load_required_v1_finding_rules` loader helper.
- The :class:`FindingRuleEngine` emitter (steps 2 + 3 + 4 + 7 of
  doc-16:155-169): deterministic idempotency_key via the REUSED 1st
  sub-slice
  :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`;
  at-least-one-primary invariant (doc-16:159-161); product/workflow
  separation (doc-16:162-163); suppression / expiry (doc-16:168-169);
  confidence threshold + non-blocking observer (doc-14:242-243);
  per-call gap_findings accumulator reset.
- Failure router wiring -- NEW typed failure id
  ``finding_rule_emission_failed`` under EXISTING
  ``evidence_corruption`` failure_class with REUSED Slice 14 2nd
  sub-slice ``retry_governance_projection`` NON-blocking RouteAction.
- DIRECT annotation-identity REUSE assertions (the stronger P3-V3-2
  pattern Slice 14 V3 reviewer flagged + Slice 15 + Slice 16 1st
  sub-slices adopted): every emitter input/output typed shape's
  field annotation that references a 1st sub-slice / Slice 13a typed
  shape is asserted via ``get_origin`` + ``get_args`` decomposition
  + ``is`` identity comparison.
- doc-16:201-291 Slice 13A awareness: no local redefinition of
  Slice 13A typed shapes.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13A + Slice 14 + Slice 15 + 16
1st sub-slice modules + tests remain byte-identical (only the
``failure_router.py`` pure-data add lands).
"""

from __future__ import annotations

import importlib
import inspect
from datetime import datetime, timedelta, timezone
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
    compute_finding_idempotency_key,
)
from iriai_build_v2.execution_control.finding_rule_engine import (
    CLASS_NAME_TO_FINDING_KIND,
    EVIDENCE_GAP_FINDING_KINDS,
    FINDING_RULE_EMISSION_FAILURE_ID,
    REQUIRED_V1_FINDING_RULES,
    FindingExpiryPolicy,
    FindingRuleEmissionGap,
    FindingRuleEmissionInputs,
    FindingRuleEngine,
    FindingSuppressionPolicy,
    load_required_v1_finding_rules,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _evidence_ref(
    *,
    digest: str = "0" * 64,
    ref_id: str = "evidence-ref-abc123",
) -> GovernanceEvidenceRef:
    return GovernanceEvidenceRef(
        authority="typed_journal",
        ref_id=ref_id,
        digest=digest,
        quality="canonical",
        completeness="complete",
    )


def _inputs(**overrides: Any) -> FindingRuleEmissionInputs:
    """Construct fully-specified :class:`FindingRuleEmissionInputs` for tests."""

    rule = overrides.pop("rule", None)
    if rule is None:
        rule = FindingRule(
            rule_id="commit_hygiene_loop_v1",
            version="v1",
            required_metric_names=[],
            required_evidence_kinds=[],
            min_confidence=0.5,
            emits_kind="workflow_inefficiency",
        )
    base: dict[str, Any] = dict(
        rule=rule,
        class_name="commit_hygiene_loop",
        severity="medium",
        confidence=0.85,
        feature_id="8ac124d6",
        affected_scope={"lane": "high_risk"},
        primary_evidence_refs=[_evidence_ref()],
        supporting_evidence_refs=[],
        implementation_log_anchors=[],
        metric_refs=["commit_failures_per_task"],
        recommended_action_display="Tighten commit retry budget.",
        safe_runtime_action=False,
        requires_policy_artifact=True,
        product_defect_related=False,
        workflow_related=True,
        causal_role="primary",
    )
    base.update(overrides)
    return FindingRuleEmissionInputs(**base)


# ── module surface ─────────────────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The module ``__all__`` carries the documented 10-name surface."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    expected = {
        "FindingSuppressionPolicy",
        "FindingExpiryPolicy",
        "FindingRuleEmissionInputs",
        "FindingRuleEmissionGap",
        "FINDING_RULE_EMISSION_FAILURE_ID",
        "EVIDENCE_GAP_FINDING_KINDS",
        "REQUIRED_V1_FINDING_RULES",
        "load_required_v1_finding_rules",
        "CLASS_NAME_TO_FINDING_KIND",
        "FindingRuleEngine",
    }
    assert set(mod.__all__) == expected
    assert len(mod.__all__) == len(expected)


def test_module_does_not_redefine_governance_finding() -> None:
    """The module REUSES Slice 16 1st sub-slice
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
    via direct import (does not redefine).
    """

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert getattr(mod, "GovernanceFinding") is GovernanceFinding


def test_module_does_not_redefine_finding_rule() -> None:
    """The module REUSES Slice 16 1st sub-slice
    :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
    via direct import (does not redefine).
    """

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert getattr(mod, "FindingRule") is FindingRule


def test_module_does_not_redefine_governance_evidence_ref() -> None:
    """The module REUSES Slice 13a
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    via direct import (does not redefine; per doc-13a:285-287 step 9 +
    doc-16:201-291).
    """

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert getattr(mod, "GovernanceEvidenceRef") is GovernanceEvidenceRef


def test_module_does_not_redefine_completeness_state() -> None:
    """The module does NOT redefine Slice 13a / Slice 13A
    ``CompletenessState`` (per doc-16:201-291 awareness)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert not hasattr(mod, "CompletenessState")


def test_module_does_not_redefine_evidence_completeness() -> None:
    """The module does NOT redefine Slice 13A
    ``EvidenceCompleteness`` (per doc-16:201-291 awareness)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert not hasattr(mod, "EvidenceCompleteness")


def test_module_does_not_redefine_authoritative_prompt_context_routing() -> None:
    """The module does NOT redefine Slice 13A 4th sub-slice
    ``AuthoritativePromptContextRouting`` (per doc-16:201-291)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert not hasattr(mod, "AuthoritativePromptContextRouting")


def test_module_does_not_redefine_authoritative_gate_companion_record() -> None:
    """The module does NOT redefine Slice 13A 5th sub-slice
    ``AuthoritativeGateCompanionRecord`` (per doc-16:201-291)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert not hasattr(mod, "AuthoritativeGateCompanionRecord")


def test_module_does_not_redefine_authoritative_snapshot_classifier_routing() -> None:
    """The module does NOT redefine Slice 13A 6th sub-slice
    ``AuthoritativeSnapshotClassifierRouting`` (per doc-16:201-291)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert not hasattr(mod, "AuthoritativeSnapshotClassifierRouting")


def test_module_does_not_redefine_compute_finding_idempotency_key() -> None:
    """The module REUSES the 1st sub-slice
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    helper via direct import (does not redefine)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    assert getattr(mod, "compute_finding_idempotency_key") is compute_finding_idempotency_key


def test_module_source_text_no_local_class_name_to_kind_table_duplication() -> None:
    """The CLASS_NAME_TO_FINDING_KIND mapping is the SINGLE source-of-
    truth for the v1 class-name -> kind contract. Subsequent rule
    versions must extend (not redefine) this mapping."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    source = inspect.getsource(mod)
    # Only ONE definition of CLASS_NAME_TO_FINDING_KIND must exist.
    assert source.count("CLASS_NAME_TO_FINDING_KIND") >= 2  # at least one in __all__ and the definition
    assert source.count("CLASS_NAME_TO_FINDING_KIND: dict") == 1


def test_module_import_discipline_no_implementation_py() -> None:
    """The module does NOT import the Slice 00-12 ``implementation.py``."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    source = inspect.getsource(mod)
    assert "from iriai_build_v2.workflows.develop.implementation" not in source
    assert "import iriai_build_v2.workflows.develop.implementation" not in source


def test_module_import_discipline_no_failure_router() -> None:
    """The module does NOT import the failure_router (the failure id is
    a typed Literal; the router consumption lives in the router itself).
    The module's docstrings MAY mention the router by name, but the
    module MUST NOT import it.
    """

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    source = inspect.getsource(mod)
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in source
    assert "import iriai_build_v2.workflows.develop.execution.failure_router" not in source


def test_package_init_does_not_re_export_finding_rule_engine() -> None:
    """The execution_control package ``__init__.py`` does NOT re-export
    ``finding_rule_engine`` (per Slice 13A + Slice 14 + Slice 15 + Slice
    16 1st sub-slice precedent)."""

    import iriai_build_v2.execution_control as pkg

    assert "finding_rule_engine" not in pkg.__all__
    assert "FindingRuleEngine" not in pkg.__all__
    assert "FindingSuppressionPolicy" not in pkg.__all__
    assert "FindingExpiryPolicy" not in pkg.__all__
    assert "FindingRuleEmissionInputs" not in pkg.__all__
    assert "FindingRuleEmissionGap" not in pkg.__all__


# ── FINDING_RULE_EMISSION_FAILURE_ID typed Literal ─────────────────────────


def test_finding_rule_emission_failure_id_exact_value() -> None:
    """The typed failure id is exactly the documented value."""

    assert FINDING_RULE_EMISSION_FAILURE_ID == "finding_rule_emission_failed"


def test_finding_rule_emission_failure_id_in_failure_router_taxonomy() -> None:
    """The typed failure id is registered in the failure_router."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        FAILURE_TYPES,
    )

    assert "finding_rule_emission_failed" in FAILURE_TYPES


# ── EVIDENCE_GAP_FINDING_KINDS (doc-16:159-161) ────────────────────────────


def test_evidence_gap_finding_kinds_membership() -> None:
    """The 3-value tuple matches the doc-16:159-161 evidence-gap allowance."""

    assert EVIDENCE_GAP_FINDING_KINDS == (
        "provenance_gap",
        "governance_evidence_conflict",
        "implementation_plan_deviation",
    )


def test_evidence_gap_finding_kinds_is_subset_of_finding_kind() -> None:
    """Every entry in :data:`EVIDENCE_GAP_FINDING_KINDS` is a valid
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`
    (per the 14-value taxonomy at doc-16:63-78)."""

    valid = set(get_args(FindingKind))
    for kind in EVIDENCE_GAP_FINDING_KINDS:
        assert kind in valid


def test_evidence_gap_finding_kinds_no_duplicates() -> None:
    """The tuple has no duplicates (each evidence-gap kind appears once)."""

    assert len(EVIDENCE_GAP_FINDING_KINDS) == len(set(EVIDENCE_GAP_FINDING_KINDS))


# ── CLASS_NAME_TO_FINDING_KIND (doc-16:122-137) ────────────────────────────


def test_class_name_to_finding_kind_covers_all_16_required_v1_names() -> None:
    """Every entry in :data:`REQUIRED_V1_FINDING_CLASS_NAMES` is a key in
    :data:`CLASS_NAME_TO_FINDING_KIND` (per doc-16:122-137)."""

    for name in REQUIRED_V1_FINDING_CLASS_NAMES:
        assert name in CLASS_NAME_TO_FINDING_KIND


def test_class_name_to_finding_kind_no_extra_keys() -> None:
    """:data:`CLASS_NAME_TO_FINDING_KIND` carries EXACTLY 16 keys (one
    per :data:`REQUIRED_V1_FINDING_CLASS_NAMES`)."""

    assert len(CLASS_NAME_TO_FINDING_KIND) == 16
    assert set(CLASS_NAME_TO_FINDING_KIND.keys()) == set(REQUIRED_V1_FINDING_CLASS_NAMES)


def test_class_name_to_finding_kind_values_are_all_finding_kind() -> None:
    """Every value in :data:`CLASS_NAME_TO_FINDING_KIND` is a valid
    :data:`~iriai_build_v2.execution_control.finding_engine.FindingKind`."""

    valid = set(get_args(FindingKind))
    for kind in CLASS_NAME_TO_FINDING_KIND.values():
        assert kind in valid


def test_class_name_to_kind_provenance_gap_classes() -> None:
    """The 2 provenance-gap class names map to ``provenance_gap`` kind
    per doc-16:133-134 + the FindingKind taxonomy at doc-16:73."""

    assert CLASS_NAME_TO_FINDING_KIND["line_provenance_gap"] == "provenance_gap"
    assert CLASS_NAME_TO_FINDING_KIND["implementation_journal_gap"] == "provenance_gap"


def test_class_name_to_kind_scheduler_classes() -> None:
    """The 2 scheduler-wave class names both map to ``scheduler_mismatch``
    kind per doc-16:130-131 + the FindingKind taxonomy at doc-16:70."""

    assert CLASS_NAME_TO_FINDING_KIND["scheduler_wave_too_small"] == "scheduler_mismatch"
    assert CLASS_NAME_TO_FINDING_KIND["scheduler_wave_too_large"] == "scheduler_mismatch"


# ── REQUIRED_V1_FINDING_RULES (doc-16:120-137 + step 1) ─────────────────────


def test_required_v1_finding_rules_is_16_entry_tuple() -> None:
    """The rule tuple carries exactly 16 entries (matches the
    :data:`REQUIRED_V1_FINDING_CLASS_NAMES` size per doc-16:120-137)."""

    assert isinstance(REQUIRED_V1_FINDING_RULES, tuple)
    assert len(REQUIRED_V1_FINDING_RULES) == 16


def test_required_v1_finding_rules_one_per_class_name() -> None:
    """Each rule's ``rule_id`` aligns with a class_name from
    :data:`REQUIRED_V1_FINDING_CLASS_NAMES` via the ``{class_name}_v1``
    pattern."""

    for class_name, rule in zip(REQUIRED_V1_FINDING_CLASS_NAMES, REQUIRED_V1_FINDING_RULES):
        assert rule.rule_id == f"{class_name}_v1"


def test_required_v1_finding_rules_all_version_v1() -> None:
    """Every v1 rule carries ``version="v1"`` per doc-16:215-217."""

    for rule in REQUIRED_V1_FINDING_RULES:
        assert rule.version == "v1"


def test_required_v1_finding_rules_min_confidence_conservative() -> None:
    """Every v1 rule carries the conservative ``min_confidence=0.5``
    starting calibration per doc-16:111-112; subsequent versions may
    tighten."""

    for rule in REQUIRED_V1_FINDING_RULES:
        assert rule.min_confidence == 0.5


def test_required_v1_finding_rules_emits_kind_matches_mapping() -> None:
    """Each rule's ``emits_kind`` aligns with
    :data:`CLASS_NAME_TO_FINDING_KIND` per doc-16:122-137 + doc-16:63-78."""

    for class_name, rule in zip(REQUIRED_V1_FINDING_CLASS_NAMES, REQUIRED_V1_FINDING_RULES):
        assert rule.emits_kind == CLASS_NAME_TO_FINDING_KIND[class_name]


def test_required_v1_finding_rules_empty_required_metric_names_v1() -> None:
    """V1 rules carry empty required_metric_names per the doc-16:108-109
    starting calibration; subsequent versions tighten per measured corpus."""

    for rule in REQUIRED_V1_FINDING_RULES:
        assert rule.required_metric_names == []


def test_required_v1_finding_rules_empty_required_evidence_kinds_v1() -> None:
    """V1 rules carry empty required_evidence_kinds (the
    at-least-one-primary invariant lives in the emitter per
    doc-16:159-161)."""

    for rule in REQUIRED_V1_FINDING_RULES:
        assert rule.required_evidence_kinds == []


# ── load_required_v1_finding_rules helper ──────────────────────────────────


def test_load_required_v1_finding_rules_returns_tuple_identity() -> None:
    """The loader returns :data:`REQUIRED_V1_FINDING_RULES` BY IDENTITY
    (same tuple object on every call)."""

    assert load_required_v1_finding_rules() is REQUIRED_V1_FINDING_RULES


def test_load_required_v1_finding_rules_idempotent() -> None:
    """Calling the loader twice returns the same tuple."""

    a = load_required_v1_finding_rules()
    b = load_required_v1_finding_rules()
    assert a is b
    assert len(a) == 16


# ── FindingSuppressionPolicy typed-shape ───────────────────────────────────


def test_suppression_policy_accepts_required_fields() -> None:
    """The typed shape constructs cleanly with the required fields."""

    now = datetime.now(timezone.utc)
    p = FindingSuppressionPolicy(
        rule_id="commit_hygiene_loop_v1",
        rule_version="v1",
        suppression_reason="superseded_by_v2",
        suppressed_at=now,
    )
    assert p.rule_id == "commit_hygiene_loop_v1"
    assert p.rule_version == "v1"
    assert p.suppression_reason == "superseded_by_v2"
    assert p.suppressed_at == now


def test_suppression_policy_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    now = datetime.now(timezone.utc)
    with pytest.raises(ValidationError):
        FindingSuppressionPolicy(
            rule_id="r1",
            rule_version="v1",
            suppression_reason="x",
            suppressed_at=now,
            typoed_field=True,  # type: ignore[call-arg]
        )


def test_suppression_policy_round_trip_via_json() -> None:
    """The typed shape round-trips via ``model_dump_json`` ->
    ``model_validate_json``."""

    now = datetime.now(timezone.utc)
    p = FindingSuppressionPolicy(
        rule_id="r1",
        rule_version="v1",
        suppression_reason="x",
        suppressed_at=now,
    )
    raw = p.model_dump_json()
    restored = FindingSuppressionPolicy.model_validate_json(raw)
    assert restored == p


def test_suppression_policy_requires_rule_id() -> None:
    """Required field validation."""

    with pytest.raises(ValidationError):
        FindingSuppressionPolicy(  # type: ignore[call-arg]
            rule_version="v1",
            suppression_reason="x",
            suppressed_at=datetime.now(timezone.utc),
        )


def test_suppression_policy_requires_suppressed_at_datetime() -> None:
    """The ``suppressed_at`` field is typed as datetime."""

    with pytest.raises(ValidationError):
        FindingSuppressionPolicy(
            rule_id="r1",
            rule_version="v1",
            suppression_reason="x",
            suppressed_at="not-a-datetime",  # type: ignore[arg-type]
        )


# ── FindingExpiryPolicy typed-shape ────────────────────────────────────────


def test_expiry_policy_accepts_required_fields() -> None:
    """The typed shape constructs cleanly with the required fields."""

    future = datetime.now(timezone.utc) + timedelta(days=30)
    p = FindingExpiryPolicy(
        rule_id="commit_hygiene_loop_v1",
        rule_version="v1",
        expires_at=future,
    )
    assert p.rule_id == "commit_hygiene_loop_v1"
    assert p.expires_at == future
    assert p.expiry_reason == ""  # default empty


def test_expiry_policy_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        FindingExpiryPolicy(
            rule_id="r1",
            rule_version="v1",
            expires_at=datetime.now(timezone.utc),
            typoed_field=True,  # type: ignore[call-arg]
        )


def test_expiry_policy_round_trip_via_json() -> None:
    """The typed shape round-trips via ``model_dump_json`` ->
    ``model_validate_json``."""

    future = datetime.now(timezone.utc) + timedelta(days=30)
    p = FindingExpiryPolicy(
        rule_id="r1",
        rule_version="v1",
        expires_at=future,
        expiry_reason="policy_window_expired",
    )
    raw = p.model_dump_json()
    restored = FindingExpiryPolicy.model_validate_json(raw)
    assert restored == p


def test_expiry_policy_is_expired_true_when_past() -> None:
    """The :meth:`is_expired` helper returns True when ``now >= expires_at``."""

    past = datetime.now(timezone.utc) - timedelta(days=1)
    p = FindingExpiryPolicy(rule_id="r1", rule_version="v1", expires_at=past)
    assert p.is_expired() is True


def test_expiry_policy_is_expired_false_when_future() -> None:
    """The :meth:`is_expired` helper returns False when ``now < expires_at``."""

    future = datetime.now(timezone.utc) + timedelta(days=30)
    p = FindingExpiryPolicy(rule_id="r1", rule_version="v1", expires_at=future)
    assert p.is_expired() is False


def test_expiry_policy_is_expired_accepts_now_override() -> None:
    """The :meth:`is_expired` helper accepts an explicit ``now`` parameter."""

    fixed = datetime(2026, 1, 1, tzinfo=timezone.utc)
    after = datetime(2026, 1, 2, tzinfo=timezone.utc)
    before = datetime(2025, 12, 31, tzinfo=timezone.utc)
    p = FindingExpiryPolicy(rule_id="r1", rule_version="v1", expires_at=fixed)
    assert p.is_expired(now=after) is True
    assert p.is_expired(now=before) is False


# ── FindingRuleEmissionInputs typed-shape ──────────────────────────────────


def test_emission_inputs_accepts_required_fields() -> None:
    """The typed shape constructs cleanly with required + default fields."""

    inputs = _inputs()
    assert inputs.class_name == "commit_hygiene_loop"
    assert inputs.severity == "medium"
    assert inputs.confidence == 0.85


def test_emission_inputs_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        _inputs(typoed_field=True)


def test_emission_inputs_confidence_bound_at_one() -> None:
    """``confidence`` field is bounded to ``[0.0, 1.0]``."""

    with pytest.raises(ValidationError):
        _inputs(confidence=1.5)


def test_emission_inputs_confidence_bound_at_zero() -> None:
    """``confidence`` field rejects negative values."""

    with pytest.raises(ValidationError):
        _inputs(confidence=-0.1)


def test_emission_inputs_round_trip_via_json() -> None:
    """The typed shape round-trips via ``model_dump_json`` ->
    ``model_validate_json``."""

    inputs = _inputs()
    raw = inputs.model_dump_json()
    restored = FindingRuleEmissionInputs.model_validate_json(raw)
    assert restored == inputs


def test_emission_inputs_carries_rule_object() -> None:
    """The ``rule`` field carries a typed
    :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`."""

    inputs = _inputs()
    assert isinstance(inputs.rule, FindingRule)


# ── FindingRuleEmissionGap typed-shape ─────────────────────────────────────


def test_emission_gap_accepts_required_fields() -> None:
    """The typed gap shape constructs cleanly with required fields."""

    g = FindingRuleEmissionGap(
        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
        rule_id="r1",
        rule_version="v1",
        class_name="commit_hygiene_loop",
        attempted_idempotency_key=None,
        reason="at_least_one_primary_invariant_violated",
    )
    assert g.failure_id == "finding_rule_emission_failed"
    assert g.evidence_payload == {}


def test_emission_gap_extra_forbid_rejects_unknown_field() -> None:
    """``ConfigDict(extra="forbid")`` -- typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        FindingRuleEmissionGap(
            failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
            rule_id="r1",
            rule_version="v1",
            class_name="commit_hygiene_loop",
            attempted_idempotency_key=None,
            reason="x",
            typoed_field=True,  # type: ignore[call-arg]
        )


def test_emission_gap_rejects_wrong_failure_id() -> None:
    """The Literal failure_id rejects values other than the documented one."""

    with pytest.raises(ValidationError):
        FindingRuleEmissionGap(
            failure_id="some_other_id",  # type: ignore[arg-type]
            rule_id="r1",
            rule_version="v1",
            class_name="commit_hygiene_loop",
            attempted_idempotency_key=None,
            reason="x",
        )


def test_emission_gap_round_trip_via_json() -> None:
    """The typed shape round-trips via JSON."""

    g = FindingRuleEmissionGap(
        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
        rule_id="r1",
        rule_version="v1",
        class_name="commit_hygiene_loop",
        attempted_idempotency_key="abc",
        reason="x",
        evidence_payload={"k": "v"},
    )
    raw = g.model_dump_json()
    restored = FindingRuleEmissionGap.model_validate_json(raw)
    assert restored == g


def test_emission_gap_attempted_idempotency_key_optional() -> None:
    """``attempted_idempotency_key`` may be ``None`` (when the failure
    happened pre-computation per the doc-16 contract)."""

    g = FindingRuleEmissionGap(
        failure_id=FINDING_RULE_EMISSION_FAILURE_ID,
        rule_id="r1",
        rule_version="v1",
        class_name="commit_hygiene_loop",
        attempted_idempotency_key=None,
        reason="x",
    )
    assert g.attempted_idempotency_key is None


# ── DIRECT annotation-identity REUSE assertions (stronger P3-V3-2) ─────────


def test_emission_inputs_rule_annotation_identity_reuse() -> None:
    """:attr:`FindingRuleEmissionInputs.rule` annotation IS the Slice 16
    1st sub-slice :class:`~iriai_build_v2.execution_control.finding_engine.FindingRule`
    BaseModel (direct identity, NOT a value-set comparison)."""

    annotation = FindingRuleEmissionInputs.model_fields["rule"].annotation
    assert annotation is FindingRule


def test_emission_inputs_severity_annotation_identity_reuse() -> None:
    """:attr:`FindingRuleEmissionInputs.severity` annotation IS the 1st
    sub-slice :data:`FindingSeverity` Literal alias (direct identity)."""

    annotation = FindingRuleEmissionInputs.model_fields["severity"].annotation
    assert annotation is FindingSeverity


def test_emission_inputs_causal_role_annotation_identity_reuse() -> None:
    """:attr:`FindingRuleEmissionInputs.causal_role` annotation IS the
    1st sub-slice :data:`FindingCausalRole` Literal alias."""

    annotation = FindingRuleEmissionInputs.model_fields["causal_role"].annotation
    assert annotation is FindingCausalRole


def test_emission_inputs_primary_evidence_refs_annotation_identity_reuse() -> None:
    """:attr:`FindingRuleEmissionInputs.primary_evidence_refs` annotation
    decomposes via get_origin + get_args to the direct-identity Slice
    13a :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
    (per the stronger P3-V3-2 pattern)."""

    annotation = FindingRuleEmissionInputs.model_fields[
        "primary_evidence_refs"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceEvidenceRef


def test_emission_inputs_supporting_evidence_refs_annotation_identity_reuse() -> None:
    """:attr:`FindingRuleEmissionInputs.supporting_evidence_refs`
    annotation decomposes to the direct-identity Slice 13a
    :class:`GovernanceEvidenceRef` (per the stronger P3-V3-2 pattern)."""

    annotation = FindingRuleEmissionInputs.model_fields[
        "supporting_evidence_refs"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert args[0] is GovernanceEvidenceRef


def test_emission_gap_failure_id_annotation_matches_failure_id_literal() -> None:
    """:attr:`FindingRuleEmissionGap.failure_id` annotation is a
    :class:`typing.Literal` whose only valid value matches
    :data:`FINDING_RULE_EMISSION_FAILURE_ID`."""

    annotation = FindingRuleEmissionGap.model_fields["failure_id"].annotation
    values = get_args(annotation)
    assert values == ("finding_rule_emission_failed",)


# ── failure_router wiring (NEW failure id) ─────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    """The :data:`FailureType` Literal carries the NEW typed failure id."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        FailureType,
    )

    assert "finding_rule_emission_failed" in get_args(FailureType)


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """The :data:`FAILURE_TYPES` tuple carries the NEW typed failure id."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        FAILURE_TYPES,
    )

    assert "finding_rule_emission_failed" in FAILURE_TYPES


def test_failure_router_retryable_failure_types_includes_new_id() -> None:
    """The new failure id is registered in
    ``_RETRYABLE_FAILURE_TYPES`` per the Slice 14 + Slice 15 precedent
    (non-blocking governance projection observer; retryable)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _RETRYABLE_FAILURE_TYPES,
    )

    assert "finding_rule_emission_failed" in _RETRYABLE_FAILURE_TYPES


def test_failure_router_routes_finding_rule_emission_to_retry_governance_projection() -> None:
    """The new failure id under EXISTING ``evidence_corruption``
    failure_class routes to the EXISTING (REUSED) ``retry_governance_projection``
    NON-blocking RouteAction (NOT a new action). Mirrors the Slice 15
    4th sub-slice precedent at
    ``tests/test_execution_control_governance_scorecard_writer.py::test_failure_router_route_under_evidence_corruption_with_retry_governance_projection``.
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _ROUTE_ROWS,
    )

    matches = [
        row
        for row in _ROUTE_ROWS
        if row[0].failure_type == "finding_rule_emission_failed"
    ]
    assert len(matches) == 1
    type_pol, route_pol = matches[0]
    assert type_pol.failure_class == "evidence_corruption"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_finding_rule_emission_route_is_non_blocking() -> None:
    """The new route is non-blocking (NOT ``quiesce``, NOT
    ``operator_required``)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _ROUTE_ROWS,
    )

    matches = [
        row
        for row in _ROUTE_ROWS
        if row[0].failure_type == "finding_rule_emission_failed"
    ]
    assert len(matches) == 1
    _, route_pol = matches[0]
    assert route_pol.action != "quiesce"
    assert route_pol.action != "operator_required"
    assert route_pol.action == "retry_governance_projection"


def test_failure_router_finding_rule_emission_is_retryable_not_deterministic() -> None:
    """Per the Slice 14 + Slice 15 + 16 precedent the NEW failure id is
    observer-transient (retryable; NOT deterministic)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _ROUTE_ROWS,
    )

    matches = [
        row
        for row in _ROUTE_ROWS
        if row[0].failure_type == "finding_rule_emission_failed"
    ]
    assert len(matches) == 1
    type_pol, _ = matches[0]
    assert type_pol.retryable
    assert not type_pol.deterministic


def test_failure_router_action_is_not_a_new_action() -> None:
    """The route action is REUSED from Slice 14 2nd sub-slice (NOT a new
    action). The :data:`ROUTE_ACTIONS` tuple is byte-identical to the
    Slice 15 4th sub-slice baseline (no new entries this 2nd sub-slice).
    """

    from iriai_build_v2.workflows.develop.execution.failure_router import (
        ROUTE_ACTIONS,
    )

    # 14 actions per Slice 14 2nd sub-slice baseline (the +1 for
    # retry_governance_projection from Slice 14; no further additions
    # from Slice 15 + 16 -- they all REUSE).
    assert "retry_governance_projection" in ROUTE_ACTIONS
    # The Slice 16 2nd sub-slice MUST NOT introduce a new action.
    new_actions = {
        "retry_finding_emission",
        "retry_rule_application",
        "finding_emission_action",
    }
    for action in new_actions:
        assert action not in ROUTE_ACTIONS


# ── FindingRuleEngine emitter correctness ──────────────────────────────────


def test_engine_emit_finding_returns_typed_finding() -> None:
    """The emitter returns a typed
    :class:`~iriai_build_v2.execution_control.finding_engine.GovernanceFinding`
    for valid inputs."""

    engine = FindingRuleEngine()
    inputs = _inputs()
    finding = engine.emit_finding(inputs)
    assert finding is not None
    assert isinstance(finding, GovernanceFinding)
    assert finding.kind == "workflow_inefficiency"
    assert finding.class_name == "commit_hygiene_loop"
    assert finding.severity == "medium"
    assert finding.confidence == 0.85
    assert engine.gap_findings == []


def test_engine_emit_finding_deterministic_idempotency_key() -> None:
    """Two consecutive emits with identical inputs produce identical
    idempotency_keys per doc-16:178."""

    engine = FindingRuleEngine()
    inputs = _inputs()
    a = engine.emit_finding(inputs)
    b = engine.emit_finding(inputs)
    assert a is not None and b is not None
    assert a.idempotency_key == b.idempotency_key


def test_engine_emit_finding_key_matches_helper() -> None:
    """The emitter computes the idempotency_key via the REUSED
    :func:`~iriai_build_v2.execution_control.finding_engine.compute_finding_idempotency_key`
    helper (per doc-16:158)."""

    engine = FindingRuleEngine()
    inputs = _inputs()
    finding = engine.emit_finding(inputs)
    assert finding is not None

    expected = compute_finding_idempotency_key(
        kind=inputs.rule.emits_kind,
        class_name=inputs.class_name,
        feature_id=inputs.feature_id,
        affected_scope=inputs.affected_scope,
        primary_evidence_digests=[ref.digest for ref in inputs.primary_evidence_refs],
        rule_version=inputs.rule.version,
    )
    assert finding.idempotency_key == expected


def test_engine_emit_finding_key_differs_on_rule_version_change() -> None:
    """Different rule_versions produce different idempotency_keys per
    doc-16:215-217 (rule version supersede path)."""

    engine = FindingRuleEngine()

    rule_v1 = FindingRule(
        rule_id="r1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="workflow_inefficiency",
    )
    rule_v2 = FindingRule(
        rule_id="r1",
        version="v2",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="workflow_inefficiency",
    )
    a = engine.emit_finding(_inputs(rule=rule_v1))
    b = engine.emit_finding(_inputs(rule=rule_v2))
    assert a is not None and b is not None
    assert a.idempotency_key != b.idempotency_key


def test_engine_emit_finding_key_differs_on_feature_id_change() -> None:
    """Different feature_ids produce different idempotency_keys per
    doc-16:158."""

    engine = FindingRuleEngine()
    a = engine.emit_finding(_inputs(feature_id="feat-a"))
    b = engine.emit_finding(_inputs(feature_id="feat-b"))
    assert a is not None and b is not None
    assert a.idempotency_key != b.idempotency_key


def test_engine_emit_finding_key_differs_on_evidence_digest_change() -> None:
    """Different primary evidence digests produce different idempotency_keys
    per doc-16:158."""

    engine = FindingRuleEngine()
    ref_a = _evidence_ref(digest="a" * 64)
    ref_b = _evidence_ref(digest="b" * 64)
    a = engine.emit_finding(_inputs(primary_evidence_refs=[ref_a]))
    b = engine.emit_finding(_inputs(primary_evidence_refs=[ref_b]))
    assert a is not None and b is not None
    assert a.idempotency_key != b.idempotency_key


def test_engine_emit_finding_key_differs_on_class_name_change() -> None:
    """Different class_names produce different idempotency_keys per
    doc-16:158."""

    engine = FindingRuleEngine()
    a = engine.emit_finding(_inputs(class_name="commit_hygiene_loop"))
    b = engine.emit_finding(_inputs(class_name="acl_or_writeability_drag"))
    assert a is not None and b is not None
    assert a.idempotency_key != b.idempotency_key


def test_engine_emit_finding_at_least_one_primary_violation_emits_gap() -> None:
    """Non-evidence-gap kind with empty primary_evidence_refs emits a
    typed :class:`FindingRuleEmissionGap` per doc-16:159-161 +
    feedback_no_silent_degradation."""

    engine = FindingRuleEngine()
    inputs = _inputs(primary_evidence_refs=[])
    result = engine.emit_finding(inputs)
    assert result is None
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "at_least_one_primary_invariant_violated"
    assert gap.failure_id == FINDING_RULE_EMISSION_FAILURE_ID


def test_engine_emit_finding_evidence_gap_kind_allowed_empty_primary() -> None:
    """Evidence-gap kinds may emit with empty primary_evidence_refs per
    doc-16:159-161 (the 3-value tuple at :data:`EVIDENCE_GAP_FINDING_KINDS`)."""

    engine = FindingRuleEngine()
    gap_rule = FindingRule(
        rule_id="implementation_journal_gap_v1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="provenance_gap",
    )
    inputs = _inputs(
        rule=gap_rule,
        class_name="implementation_journal_gap",
        primary_evidence_refs=[],
    )
    finding = engine.emit_finding(inputs)
    assert finding is not None
    assert finding.kind == "provenance_gap"
    assert finding.primary_evidence_refs == []


@pytest.mark.parametrize("gap_kind", list(EVIDENCE_GAP_FINDING_KINDS))
def test_engine_each_evidence_gap_kind_allows_empty_primary(gap_kind: FindingKind) -> None:
    """Every entry in :data:`EVIDENCE_GAP_FINDING_KINDS` allows emission
    with empty primary_evidence_refs."""

    engine = FindingRuleEngine()
    rule = FindingRule(
        rule_id=f"some_{gap_kind}_v1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind=gap_kind,
    )
    inputs = _inputs(rule=rule, primary_evidence_refs=[])
    finding = engine.emit_finding(inputs)
    assert finding is not None
    assert finding.kind == gap_kind


def test_engine_emit_finding_product_workflow_separation_violated_emits_gap() -> None:
    """``product_defect_cluster`` kind without ``product_defect_related=True``
    emits a typed gap finding per doc-16:162-163."""

    engine = FindingRuleEngine()
    rule = FindingRule(
        rule_id="some_product_defect_v1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="product_defect_cluster",
    )
    inputs = _inputs(rule=rule, product_defect_related=False)
    result = engine.emit_finding(inputs)
    assert result is None
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "product_workflow_separation_violated"


def test_engine_emit_finding_workflow_policy_without_workflow_related_cause_emits_gap() -> None:
    """``product_defect_cluster`` kind + requires_policy_artifact=True
    + workflow_related=False emits a typed gap finding per doc-16:163
    *"workflow policy recommendations must cite workflow-related causes"*."""

    engine = FindingRuleEngine()
    rule = FindingRule(
        rule_id="some_product_defect_v1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="product_defect_cluster",
    )
    inputs = _inputs(
        rule=rule,
        product_defect_related=True,
        workflow_related=False,
        requires_policy_artifact=True,
    )
    result = engine.emit_finding(inputs)
    assert result is None
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "product_workflow_separation_violated"


def test_engine_emit_finding_product_defect_with_proper_flags_emits() -> None:
    """``product_defect_cluster`` kind + product_defect_related=True +
    workflow_related=True + requires_policy_artifact=True emits a
    typed finding (the doc-16:162-163 conditions are satisfied)."""

    engine = FindingRuleEngine()
    rule = FindingRule(
        rule_id="some_product_defect_v1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="product_defect_cluster",
    )
    inputs = _inputs(
        rule=rule,
        product_defect_related=True,
        workflow_related=True,
        requires_policy_artifact=True,
    )
    finding = engine.emit_finding(inputs)
    assert finding is not None
    assert finding.kind == "product_defect_cluster"
    assert finding.product_defect_related is True
    assert finding.workflow_related is True


def test_engine_emit_finding_advisory_below_threshold_emits() -> None:
    """Below-threshold confidence + requires_policy_artifact=False emits
    the typed finding (advisory mode per doc-16:193)."""

    engine = FindingRuleEngine()
    rule = FindingRule(
        rule_id="r1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.7,
        emits_kind="workflow_inefficiency",
    )
    inputs = _inputs(
        rule=rule,
        confidence=0.4,
        requires_policy_artifact=False,
    )
    finding = engine.emit_finding(inputs)
    assert finding is not None
    assert finding.confidence == 0.4
    assert engine.gap_findings == []


def test_engine_emit_finding_below_threshold_with_policy_emits_gap() -> None:
    """Below-threshold confidence + requires_policy_artifact=True emits
    a typed gap finding per doc-16:193 *"Low confidence: findings may be
    reported but cannot feed policy recommendations."*"""

    engine = FindingRuleEngine()
    rule = FindingRule(
        rule_id="r1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.7,
        emits_kind="workflow_inefficiency",
    )
    inputs = _inputs(
        rule=rule,
        confidence=0.4,
        requires_policy_artifact=True,
    )
    result = engine.emit_finding(inputs)
    assert result is None
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "confidence_below_min_threshold"


def test_engine_emit_finding_suppression_policy_skips_emission() -> None:
    """A matching :class:`FindingSuppressionPolicy` SKIPS emission and
    produces a typed gap finding per doc-16:168-169."""

    rule = FindingRule(
        rule_id="r1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="workflow_inefficiency",
    )
    policy = FindingSuppressionPolicy(
        rule_id="r1",
        rule_version="v1",
        suppression_reason="superseded_by_v2",
        suppressed_at=datetime.now(timezone.utc),
    )
    engine = FindingRuleEngine(suppression_policies=[policy])
    inputs = _inputs(rule=rule)
    result = engine.emit_finding(inputs)
    assert result is None
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "suppressed_by_policy"
    assert gap.evidence_payload["suppression_reason"] == "superseded_by_v2"


def test_engine_emit_finding_suppression_policy_version_precise() -> None:
    """A v1 suppression policy does NOT suppress v2 emissions per
    doc-16:215-217."""

    rule_v2 = FindingRule(
        rule_id="r1",
        version="v2",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="workflow_inefficiency",
    )
    policy_v1 = FindingSuppressionPolicy(
        rule_id="r1",
        rule_version="v1",
        suppression_reason="superseded_by_v2",
        suppressed_at=datetime.now(timezone.utc),
    )
    engine = FindingRuleEngine(suppression_policies=[policy_v1])
    finding = engine.emit_finding(_inputs(rule=rule_v2))
    assert finding is not None
    assert engine.gap_findings == []


def test_engine_emit_finding_expired_policy_skips_emission() -> None:
    """A matched + expired :class:`FindingExpiryPolicy` SKIPS emission
    and produces a typed gap finding per doc-16:168-169."""

    rule = FindingRule(
        rule_id="r1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="workflow_inefficiency",
    )
    expired = FindingExpiryPolicy(
        rule_id="r1",
        rule_version="v1",
        expires_at=datetime.now(timezone.utc) - timedelta(days=1),
        expiry_reason="policy_window_expired",
    )
    engine = FindingRuleEngine(expiry_policies=[expired])
    result = engine.emit_finding(_inputs(rule=rule))
    assert result is None
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "expired_by_policy"


def test_engine_emit_finding_unexpired_policy_emits() -> None:
    """A matched + unexpired :class:`FindingExpiryPolicy` does NOT skip
    emission."""

    rule = FindingRule(
        rule_id="r1",
        version="v1",
        required_metric_names=[],
        required_evidence_kinds=[],
        min_confidence=0.5,
        emits_kind="workflow_inefficiency",
    )
    future = FindingExpiryPolicy(
        rule_id="r1",
        rule_version="v1",
        expires_at=datetime.now(timezone.utc) + timedelta(days=365),
    )
    engine = FindingRuleEngine(expiry_policies=[future])
    finding = engine.emit_finding(_inputs(rule=rule))
    assert finding is not None
    assert engine.gap_findings == []


def test_engine_emit_finding_per_call_gap_findings_accumulator_reset() -> None:
    """The :attr:`gap_findings` accumulator RESETS at every
    :meth:`emit_finding` call (mirrors Slice 15 2nd + 4th sub-slice
    precedent)."""

    engine = FindingRuleEngine()
    bad = _inputs(primary_evidence_refs=[])
    good = _inputs()

    engine.emit_finding(bad)
    assert len(engine.gap_findings) == 1

    engine.emit_finding(good)
    # Per-call accumulator reset; the good emission produces zero gaps.
    assert engine.gap_findings == []


def test_engine_emit_finding_non_blocking_never_raises_on_invariant_violation() -> None:
    """The emitter NEVER raises a structural failure to the caller per
    doc-14:242-243 + Slice 15 precedent."""

    engine = FindingRuleEngine()
    # at-least-one-primary violation
    bad = _inputs(primary_evidence_refs=[])
    # The call MUST NOT raise.
    result = engine.emit_finding(bad)
    assert result is None


def test_engine_gap_findings_property_returns_copy() -> None:
    """The :attr:`gap_findings` property returns a defensive copy so
    callers cannot mutate the engine's accumulator from outside."""

    engine = FindingRuleEngine()
    engine.emit_finding(_inputs(primary_evidence_refs=[]))
    snapshot = engine.gap_findings
    snapshot.clear()
    # The engine's internal state is unaffected.
    assert len(engine.gap_findings) == 1


def test_engine_suppression_policies_property_returns_copy() -> None:
    """The :attr:`suppression_policies` property returns a defensive copy."""

    policy = FindingSuppressionPolicy(
        rule_id="r1",
        rule_version="v1",
        suppression_reason="x",
        suppressed_at=datetime.now(timezone.utc),
    )
    engine = FindingRuleEngine(suppression_policies=[policy])
    snapshot = engine.suppression_policies
    snapshot.clear()
    # The engine's internal state is unaffected.
    assert len(engine.suppression_policies) == 1


def test_engine_expiry_policies_property_returns_copy() -> None:
    """The :attr:`expiry_policies` property returns a defensive copy."""

    policy = FindingExpiryPolicy(
        rule_id="r1",
        rule_version="v1",
        expires_at=datetime.now(timezone.utc) + timedelta(days=30),
    )
    engine = FindingRuleEngine(expiry_policies=[policy])
    snapshot = engine.expiry_policies
    snapshot.clear()
    assert len(engine.expiry_policies) == 1


def test_engine_empty_construction_no_policies() -> None:
    """The engine constructs with no suppression / expiry policies."""

    engine = FindingRuleEngine()
    assert engine.suppression_policies == []
    assert engine.expiry_policies == []
    assert engine.gap_findings == []


# ── doc-16:201-291 Slice 13A awareness (source-text scan) ─────────────────


def test_module_source_text_no_local_completeness_state_redefinition() -> None:
    """Source-text scan: the module does NOT define a local
    ``CompletenessState`` shape (per doc-16:201-291 awareness; the
    Slice 13a shared shape is the source of truth)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    source = inspect.getsource(mod)
    # The module may MENTION CompletenessState in a docstring; but it
    # must not DEFINE a Literal / class with that name.
    assert "CompletenessState = Literal" not in source
    assert "class CompletenessState" not in source


def test_module_source_text_no_local_evidence_completeness_redefinition() -> None:
    """Source-text scan: no local ``EvidenceCompleteness`` definition."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    source = inspect.getsource(mod)
    assert "class EvidenceCompleteness" not in source


# ── Engine end-to-end correctness across the 16 v1 rules ──────────────────


@pytest.mark.parametrize(
    "class_name", list(REQUIRED_V1_FINDING_CLASS_NAMES)
)
def test_engine_emits_for_each_required_v1_class(class_name: str) -> None:
    """The engine successfully emits a typed finding for each of the 16
    v1 class names (with appropriately shaped inputs per the
    evidence-gap allowance)."""

    # Find the matching rule.
    rule = next(
        r for r in REQUIRED_V1_FINDING_RULES if r.rule_id == f"{class_name}_v1"
    )
    # Evidence-gap kinds allowed empty primary; others require one.
    primary_refs = []
    if rule.emits_kind not in EVIDENCE_GAP_FINDING_KINDS:
        primary_refs = [_evidence_ref()]
    engine = FindingRuleEngine()
    inputs = _inputs(
        rule=rule,
        class_name=class_name,
        primary_evidence_refs=primary_refs,
        product_defect_related=(class_name == "resource_budget_pressure"),
    )
    finding = engine.emit_finding(inputs)
    # Every v1 class must emit cleanly.
    assert finding is not None, f"v1 class {class_name} failed to emit"
    assert finding.class_name == class_name
    assert finding.kind == rule.emits_kind


# ── Source-text scan: engine does not import implementation.py ─────────────


def test_engine_source_does_not_couple_to_implementation_py() -> None:
    """Source-text scan: the engine does NOT import the Slice 00-12
    ``implementation.py`` monolith (per the per-purpose adapter
    discipline)."""

    import iriai_build_v2.execution_control.finding_rule_engine as mod

    source = inspect.getsource(mod)
    assert "implementation.py" not in source
    assert "from iriai_build_v2.workflows.develop.implementation" not in source
