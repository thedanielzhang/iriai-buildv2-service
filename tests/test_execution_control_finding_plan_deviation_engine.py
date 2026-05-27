"""Slice 16 3rd-A sub-slice -- unit tests for the
``execution_control/finding_plan_deviation_engine.py`` governance
implementation-plan deviation engine module.

Covers (per doc-16:164-165 § Refactoring Steps step 5; THIS SUB-SLICE owns
``accepted_plan_deviation`` (doc-16:135) + ``implementation_journal_gap``
(doc-16:134 + doc-16:191-192) classes; the 3rd-B sub-slice will own
reviewer-findings + late-test-failure rules consuming the 13c + 13d
parsers):

- The 3 new typed shapes :class:`PlanDeviationAnchorBundle` +
  :class:`PlanDeviationEmissionPlan` + :class:`PlanDeviationParseGap`
  (all carry ``ConfigDict(extra="forbid")``).
- The typed :data:`FINDING_PLAN_DEVIATION_FAILURE_ID` Literal.
- The pure helpers :func:`parse_plan_deviation_anchors` +
  :func:`compute_accepted_plan_deviation_inputs` +
  :func:`compute_implementation_journal_gap_inputs`.
- The :class:`FindingPlanDeviationEngine` -- end-to-end against the
  REUSED Slice 16 2nd sub-slice
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
  emitter (the 7-guard logic is NOT re-implemented; this engine
  delegates).
- Failure router wiring -- NEW typed failure id
  ``finding_plan_deviation_parse_failed`` under EXISTING
  ``evidence_corruption`` failure_class with REUSED Slice 14 2nd
  sub-slice ``retry_governance_projection`` NON-blocking RouteAction.
- DIRECT annotation-identity REUSE assertions (the stronger P3-V3-2
  pattern Slice 14 V3 reviewer flagged + Slice 15 + Slice 16 1st + 2nd
  sub-slices adopted): every typed-shape field annotation that
  references a Slice 13a / Slice 16 1st + 2nd sub-slice typed shape is
  asserted via ``get_origin`` + ``get_args`` decomposition + ``is``
  identity comparison.
- doc-16:201-291 Slice 13A awareness: no local redefinition of Slice 13A
  typed shapes.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own
acceptance tests; the Slice 13a + Slice 13c + Slice 13A + Slice 14 +
Slice 15 + 16 1st + 2nd sub-slice modules + tests remain byte-identical
(only the ``failure_router.py`` pure-data add lands).
"""

from __future__ import annotations

import importlib
import inspect
from pathlib import Path
from typing import Any, get_args, get_origin

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.finding_engine import (
    FindingCausalRole,
    FindingKind,
    FindingSeverity,
    GovernanceFinding,
    REQUIRED_V1_FINDING_CLASS_NAMES,
)
from iriai_build_v2.execution_control.finding_plan_deviation_engine import (
    FINDING_PLAN_DEVIATION_FAILURE_ID,
    FindingPlanDeviationEngine,
    PlanDeviationAnchorBundle,
    PlanDeviationEmissionPlan,
    PlanDeviationParseGap,
    compute_accepted_plan_deviation_inputs,
    compute_implementation_journal_gap_inputs,
    parse_plan_deviation_anchors,
)
from iriai_build_v2.execution_control.finding_rule_engine import (
    CLASS_NAME_TO_FINDING_KIND,
    EVIDENCE_GAP_FINDING_KINDS,
    REQUIRED_V1_FINDING_RULES,
    FindingRule,
    FindingRuleEmissionGap,
    FindingRuleEmissionInputs,
    FindingRuleEngine,
)
from iriai_build_v2.workflows.develop.governance.models import (
    GovernanceEvidenceRef,
    ImplementationArtifactAnchor,
    JournalEventName,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _anchor(
    *,
    slice_id: str = "16-third-A",
    event: JournalEventName = "accepted",
    accepted: bool = True,
    line_start: int | None = 100,
    decision_log_line: int | None = None,
    journal_path: str = "docs/execution-control-plane/implementation-journal.md",
    open_findings: list[str] | None = None,
) -> ImplementationArtifactAnchor:
    return ImplementationArtifactAnchor(
        slice_id=slice_id,
        journal_path=journal_path,
        line_start=line_start,
        decision_log_line=decision_log_line,
        event=event,
        accepted=accepted,
        open_findings=open_findings or [],
    )


def _rule_accepted() -> FindingRule:
    for rule in REQUIRED_V1_FINDING_RULES:
        if rule.rule_id == "accepted_plan_deviation_v1":
            return rule
    raise AssertionError("Expected accepted_plan_deviation_v1 in v1 rules")


def _rule_gap() -> FindingRule:
    for rule in REQUIRED_V1_FINDING_RULES:
        if rule.rule_id == "implementation_journal_gap_v1":
            return rule
    raise AssertionError("Expected implementation_journal_gap_v1 in v1 rules")


# ── Module surface tests (5) ───────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The ``__all__`` list pins the module's typed surface."""

    from iriai_build_v2.execution_control import finding_plan_deviation_engine as mod

    assert set(mod.__all__) == {
        "FINDING_PLAN_DEVIATION_FAILURE_ID",
        "PlanDeviationAnchorBundle",
        "PlanDeviationEmissionPlan",
        "PlanDeviationParseGap",
        "parse_plan_deviation_anchors",
        "compute_accepted_plan_deviation_inputs",
        "compute_implementation_journal_gap_inputs",
        "FindingPlanDeviationEngine",
    }


def test_module_does_not_redefine_slice13a_or_slice16_typed_shapes() -> None:
    """The 3rd-A sub-slice MUST NOT redefine any Slice 13a / Slice 16 1st
    / 2nd sub-slice typed shape."""

    from iriai_build_v2.execution_control import finding_plan_deviation_engine as mod

    members = {
        name: obj
        for name, obj in inspect.getmembers(mod)
        if not name.startswith("_")
    }
    # The Slice 13a typed shapes MUST NOT be redefined in this module.
    for forbidden in (
        "ImplementationArtifactAnchor",
        "JournalEventName",
        "GovernanceEvidenceRef",
    ):
        assert forbidden not in mod.__all__, (
            f"{forbidden} must NOT be in __all__ -- it is REUSED via direct import"
        )
    # The Slice 16 1st + 2nd sub-slice typed shapes MUST NOT be redefined.
    for forbidden in (
        "GovernanceFinding",
        "FindingRule",
        "FindingKind",
        "FindingSeverity",
        "FindingCausalRole",
        "FindingRuleEmissionInputs",
        "FindingRuleEmissionGap",
        "FindingRuleEngine",
        "compute_finding_idempotency_key",
        "CLASS_NAME_TO_FINDING_KIND",
        "EVIDENCE_GAP_FINDING_KINDS",
        "REQUIRED_V1_FINDING_RULES",
        "REQUIRED_V1_FINDING_CLASS_NAMES",
        "FINDING_RULE_EMISSION_FAILURE_ID",
    ):
        assert forbidden not in mod.__all__, (
            f"{forbidden} must NOT be in __all__ -- it is REUSED via direct import"
        )


def test_module_import_discipline_no_implementation_py() -> None:
    """The 3rd-A sub-slice module MUST NOT import from ``implementation.py``."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_plan_deviation_engine.py"
    ).read_text()
    assert "from iriai_build_v2.workflows.develop.implementation" not in src
    assert "import implementation" not in src


def test_module_import_discipline_no_failure_router() -> None:
    """The 3rd-A sub-slice module MUST NOT import from ``failure_router.py``."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_plan_deviation_engine.py"
    ).read_text()
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in src
    assert "import failure_router" not in src


def test_module_import_discipline_no_decision_log_parser() -> None:
    """The 3rd-A sub-slice module MUST NOT import the Slice 13d
    decision-log parser -- that surface is consumed by the 3rd-B
    sub-slice."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_plan_deviation_engine.py"
    ).read_text()
    # Check for actual imports (not docstring mentions): no `import ...
    # decision_log_parser` or `from ... decision_log_parser import ...`
    # statements may appear.
    import re

    import_re = re.compile(
        r"^(\s*)(?:from\s+\S*decision_log_parser\s+import|import\s+\S*decision_log_parser)",
        re.MULTILINE,
    )
    assert import_re.search(src) is None, (
        "Slice 13d decision_log_parser MUST NOT be imported in the 3rd-A "
        "sub-slice (deferred to 3rd-B sub-slice)"
    )


def test_package_init_does_not_re_export_finding_plan_deviation_engine() -> None:
    """The execution_control package ``__init__.py`` MUST NOT re-export
    the 3rd-A sub-slice module per the Slice 13A/14/15/16-1st/16-2nd
    precedent."""

    from iriai_build_v2 import execution_control as pkg

    assert "FindingPlanDeviationEngine" not in pkg.__all__
    assert "parse_plan_deviation_anchors" not in pkg.__all__
    assert "PlanDeviationAnchorBundle" not in pkg.__all__


# ── FINDING_PLAN_DEVIATION_FAILURE_ID tests (2) ─────────────────────────────


def test_finding_plan_deviation_failure_id_exact_value() -> None:
    """The typed Literal carries the expected value verbatim."""

    assert FINDING_PLAN_DEVIATION_FAILURE_ID == "finding_plan_deviation_parse_failed"


def test_finding_plan_deviation_failure_id_registered_in_failure_router() -> None:
    """The NEW typed failure id is registered in the failure router."""

    from iriai_build_v2.workflows.develop.execution.failure_router import FAILURE_TYPES

    assert FINDING_PLAN_DEVIATION_FAILURE_ID in FAILURE_TYPES


# ── PlanDeviationParseGap typed-shape tests (5) ─────────────────────────────


def test_plan_deviation_parse_gap_required_fields() -> None:
    gap = PlanDeviationParseGap(
        failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
        journal_path="docs/execution-control-plane/implementation-journal.md",
        reason="journal_file_missing",
    )
    assert gap.failure_id == "finding_plan_deviation_parse_failed"
    assert gap.journal_path == (
        "docs/execution-control-plane/implementation-journal.md"
    )
    assert gap.reason == "journal_file_missing"
    assert gap.anchor_kind == ""
    assert gap.evidence_payload == {}


def test_plan_deviation_parse_gap_extra_forbid() -> None:
    """The ``ConfigDict(extra="forbid")`` ensures typo-d kwargs fail closed."""

    with pytest.raises(ValidationError):
        PlanDeviationParseGap(
            failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
            journal_path="x",
            reason="r",
            unknown_field="oops",  # type: ignore[call-arg]
        )


def test_plan_deviation_parse_gap_rejects_wrong_failure_id() -> None:
    """The typed Literal field rejects out-of-bounds failure ids."""

    with pytest.raises(ValidationError):
        PlanDeviationParseGap(
            failure_id="wrong_id",  # type: ignore[arg-type]
            journal_path="x",
            reason="r",
        )


def test_plan_deviation_parse_gap_carries_evidence_payload() -> None:
    gap = PlanDeviationParseGap(
        failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
        journal_path="x",
        reason="journal_parse_validation_error",
        anchor_kind="accepted",
        evidence_payload={
            "error_type": "ValidationError",
            "error_detail": "bad anchor",
        },
    )
    assert gap.evidence_payload == {
        "error_type": "ValidationError",
        "error_detail": "bad anchor",
    }


def test_plan_deviation_parse_gap_json_roundtrip() -> None:
    gap = PlanDeviationParseGap(
        failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
        journal_path="x",
        reason="r",
        anchor_kind="accepted",
        evidence_payload={"k": "v"},
    )
    payload = gap.model_dump(mode="json")
    restored = PlanDeviationParseGap.model_validate(payload)
    assert restored == gap


# ── PlanDeviationAnchorBundle typed-shape tests (4) ────────────────────────


def test_plan_deviation_anchor_bundle_required_fields_only_journal_path() -> None:
    """Only ``journal_path`` is required; ``anchors`` + ``parse_gaps``
    default to empty."""

    bundle = PlanDeviationAnchorBundle(journal_path="x")
    assert bundle.journal_path == "x"
    assert bundle.anchors == []
    assert bundle.parse_gaps == []


def test_plan_deviation_anchor_bundle_extra_forbid() -> None:
    with pytest.raises(ValidationError):
        PlanDeviationAnchorBundle(
            journal_path="x", unknown_field="oops"  # type: ignore[call-arg]
        )


def test_plan_deviation_anchor_bundle_carries_typed_anchors() -> None:
    a = _anchor()
    bundle = PlanDeviationAnchorBundle(
        journal_path=a.journal_path,
        anchors=[a],
    )
    assert bundle.anchors[0] is a or bundle.anchors[0] == a


def test_plan_deviation_anchor_bundle_json_roundtrip() -> None:
    a = _anchor()
    bundle = PlanDeviationAnchorBundle(
        journal_path=a.journal_path,
        anchors=[a],
        parse_gaps=[
            PlanDeviationParseGap(
                failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                journal_path=a.journal_path,
                reason="r",
            )
        ],
    )
    payload = bundle.model_dump(mode="json")
    restored = PlanDeviationAnchorBundle.model_validate(payload)
    assert restored == bundle


# ── PlanDeviationEmissionPlan typed-shape tests (4) ────────────────────────


def test_plan_deviation_emission_plan_required_fields() -> None:
    rule = _rule_accepted()
    plan = PlanDeviationEmissionPlan(
        rule=rule,
        class_name="accepted_plan_deviation",
        severity="low",
        confidence=0.8,
        recommended_action_display="Review",
    )
    assert plan.rule is rule or plan.rule == rule
    assert plan.severity == "low"
    assert plan.causal_role == "contributing"
    assert plan.safe_runtime_action is False
    assert plan.requires_policy_artifact is False


def test_plan_deviation_emission_plan_extra_forbid() -> None:
    rule = _rule_accepted()
    with pytest.raises(ValidationError):
        PlanDeviationEmissionPlan(
            rule=rule,
            class_name="x",
            severity="low",
            confidence=0.5,
            recommended_action_display="x",
            unknown_field="oops",  # type: ignore[call-arg]
        )


def test_plan_deviation_emission_plan_confidence_bounds() -> None:
    rule = _rule_accepted()
    # Lower bound at 0.0
    plan_low = PlanDeviationEmissionPlan(
        rule=rule,
        class_name="x",
        severity="info",
        confidence=0.0,
        recommended_action_display="x",
    )
    assert plan_low.confidence == 0.0
    # Upper bound at 1.0
    plan_hi = PlanDeviationEmissionPlan(
        rule=rule,
        class_name="x",
        severity="critical",
        confidence=1.0,
        recommended_action_display="x",
    )
    assert plan_hi.confidence == 1.0
    # Out of bounds rejects
    with pytest.raises(ValidationError):
        PlanDeviationEmissionPlan(
            rule=rule,
            class_name="x",
            severity="info",
            confidence=1.1,
            recommended_action_display="x",
        )


def test_plan_deviation_emission_plan_carries_typed_source_anchor() -> None:
    rule = _rule_accepted()
    a = _anchor()
    plan = PlanDeviationEmissionPlan(
        rule=rule,
        class_name="accepted_plan_deviation",
        severity="low",
        confidence=0.8,
        source_anchor=a,
        recommended_action_display="x",
    )
    assert plan.source_anchor is a or plan.source_anchor == a


# ── parse_plan_deviation_anchors tests (5) ─────────────────────────────────


def test_parse_plan_deviation_anchors_parses_inline_markdown_body() -> None:
    """Pre-loaded body skips disk read and projects to typed bundle."""

    body = (
        "## 2026-05-20 -- Slice 16 STARTING (governance layer)\n"
        "\n"
        "Some content.\n"
        "\n"
        "## 2026-05-25 -- Slice 16 ACCEPTED\n"
        "\n"
        "Finalised.\n"
    )
    bundle = parse_plan_deviation_anchors(
        "docs/test-journal.md", body=body
    )
    assert bundle.journal_path == "docs/test-journal.md"
    assert bundle.parse_gaps == []
    # Expect at least one ACCEPTED + one STARTING anchor.
    events = {a.event for a in bundle.anchors}
    assert "starting" in events
    assert "accepted" in events


def test_parse_plan_deviation_anchors_missing_file_projects_typed_gap() -> None:
    """A missing journal file fails CLOSED with a typed parse gap;
    NEVER raises."""

    bundle = parse_plan_deviation_anchors("/nonexistent/path/to/file.md")
    assert bundle.journal_path == "/nonexistent/path/to/file.md"
    assert bundle.anchors == []
    assert len(bundle.parse_gaps) == 1
    gap = bundle.parse_gaps[0]
    assert gap.failure_id == FINDING_PLAN_DEVIATION_FAILURE_ID
    assert gap.reason == "journal_file_missing"


def test_parse_plan_deviation_anchors_empty_path_projects_typed_gap() -> None:
    """An empty journal_path fails CLOSED with a typed parse gap;
    NEVER raises."""

    bundle = parse_plan_deviation_anchors("")
    assert bundle.anchors == []
    assert len(bundle.parse_gaps) == 1
    gap = bundle.parse_gaps[0]
    assert gap.failure_id == FINDING_PLAN_DEVIATION_FAILURE_ID
    assert gap.reason == "journal_path_empty"


def test_parse_plan_deviation_anchors_empty_body_projects_empty_anchors() -> None:
    """Empty body parses cleanly to empty anchors (a legitimate result
    per the Slice 13c parser contract; the 3rd-A sub-slice engine
    treats this as the implementation_journal_gap trigger)."""

    bundle = parse_plan_deviation_anchors(
        "docs/empty-journal.md", body=""
    )
    assert bundle.anchors == []
    # No parse gap -- empty body is a legitimate parse result per
    # journal_parser.py:367 + the implementer's docstring.
    assert bundle.parse_gaps == []


def test_parse_plan_deviation_anchors_never_raises_unexpected_exception() -> None:
    """The pure-helper NEVER raises an unexpected exception per the
    feedback_no_silent_degradation rule."""

    # Path with NUL byte raises a ValueError on Linux file ops; ensure
    # the typed bundle still constructs with a parse gap.
    bundle = parse_plan_deviation_anchors("/tmp/has\x00nul/file.md")
    assert bundle.anchors == []
    assert len(bundle.parse_gaps) >= 1
    gap = bundle.parse_gaps[0]
    assert gap.failure_id == FINDING_PLAN_DEVIATION_FAILURE_ID


# ── compute_accepted_plan_deviation_inputs tests (5) ────────────────────────


def test_compute_accepted_plan_deviation_inputs_basic() -> None:
    a = _anchor()
    rule = _rule_accepted()
    inputs = compute_accepted_plan_deviation_inputs(a, rule)
    assert inputs.rule is rule or inputs.rule == rule
    assert inputs.class_name == "accepted_plan_deviation"
    assert inputs.severity == "low"  # default
    assert inputs.confidence == 0.8  # default
    assert inputs.requires_policy_artifact is False
    assert inputs.workflow_related is True
    assert inputs.product_defect_related is False
    assert inputs.causal_role == "contributing"


def test_compute_accepted_plan_deviation_inputs_log_anchor_uses_line_start() -> None:
    a = _anchor(line_start=42, decision_log_line=None)
    inputs = compute_accepted_plan_deviation_inputs(a, _rule_accepted())
    # Per the helper docstring the implementation_log_anchors list
    # carries the canonical journal-path-plus-line-anchor string.
    assert len(inputs.implementation_log_anchors) == 1
    assert ":42" in inputs.implementation_log_anchors[0]
    assert a.journal_path in inputs.implementation_log_anchors[0]


def test_compute_accepted_plan_deviation_inputs_log_anchor_uses_decision_log_line() -> (
    None
):
    a = _anchor(line_start=None, decision_log_line=17, event="decision")
    inputs = compute_accepted_plan_deviation_inputs(a, _rule_accepted())
    assert len(inputs.implementation_log_anchors) == 1
    assert "#L17" in inputs.implementation_log_anchors[0]


def test_compute_accepted_plan_deviation_inputs_emits_empty_primary_refs() -> None:
    """Per doc-16:159-161 + EVIDENCE_GAP_FINDING_KINDS the accepted plan
    deviation kind is allowed to emit with empty primary_evidence_refs;
    the journal anchor goes on implementation_log_anchors."""

    inputs = compute_accepted_plan_deviation_inputs(_anchor(), _rule_accepted())
    assert inputs.primary_evidence_refs == []
    assert inputs.supporting_evidence_refs == []
    # The rule maps to implementation_plan_deviation kind which IS in
    # EVIDENCE_GAP_FINDING_KINDS.
    assert "implementation_plan_deviation" in EVIDENCE_GAP_FINDING_KINDS


def test_compute_accepted_plan_deviation_inputs_affected_scope_carries_anchor_meta() -> (
    None
):
    a = _anchor(slice_id="16-third-A", event="accepted")
    inputs = compute_accepted_plan_deviation_inputs(a, _rule_accepted())
    assert inputs.affected_scope == {
        "slice_id": "16-third-A",
        "event": "accepted",
    }


# ── compute_implementation_journal_gap_inputs tests (4) ────────────────────


def test_compute_implementation_journal_gap_inputs_basic() -> None:
    rule = _rule_gap()
    inputs = compute_implementation_journal_gap_inputs(
        "docs/missing-journal.md", rule
    )
    assert inputs.rule is rule or inputs.rule == rule
    assert inputs.class_name == "implementation_journal_gap"
    assert inputs.severity == "high"  # default per doc-16:191-192
    assert inputs.confidence == 0.9  # default
    assert inputs.requires_policy_artifact is False
    assert inputs.workflow_related is True
    assert inputs.causal_role == "primary"


def test_compute_implementation_journal_gap_inputs_with_missing_event() -> None:
    rule = _rule_gap()
    inputs = compute_implementation_journal_gap_inputs(
        "docs/x.md", rule, missing_event="accepted"
    )
    assert inputs.affected_scope == {
        "journal_path": "docs/x.md",
        "missing_event": "accepted",
    }
    # The recommended_action_display mentions the missing event.
    assert "accepted" in inputs.recommended_action_display


def test_compute_implementation_journal_gap_inputs_emits_empty_primary_refs() -> None:
    """Per doc-16:159-161 + EVIDENCE_GAP_FINDING_KINDS the
    provenance_gap kind is allowed to emit with empty
    primary_evidence_refs."""

    inputs = compute_implementation_journal_gap_inputs(
        "docs/x.md", _rule_gap()
    )
    assert inputs.primary_evidence_refs == []
    # The rule maps to provenance_gap kind which IS in
    # EVIDENCE_GAP_FINDING_KINDS.
    assert "provenance_gap" in EVIDENCE_GAP_FINDING_KINDS


def test_compute_implementation_journal_gap_inputs_records_journal_path_as_anchor() -> (
    None
):
    inputs = compute_implementation_journal_gap_inputs(
        "docs/x.md", _rule_gap()
    )
    assert inputs.implementation_log_anchors == ["docs/x.md"]


# ── FindingPlanDeviationEngine end-to-end tests (10) ───────────────────────


def test_engine_default_rule_lookup_covers_third_A_classes() -> None:
    engine = FindingPlanDeviationEngine()
    assert engine.rule_lookup == {
        "accepted_plan_deviation": "accepted_plan_deviation_v1",
        "implementation_journal_gap": "implementation_journal_gap_v1",
    }


def test_engine_process_anchors_emits_accepted_plan_deviation_finding() -> None:
    """End-to-end: an accepted heading anchor emits a typed
    GovernanceFinding via the REUSED 2nd sub-slice engine."""

    bundle = PlanDeviationAnchorBundle(
        journal_path="docs/j.md",
        anchors=[_anchor(slice_id="13a", event="accepted", accepted=True)],
    )
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    assert len(findings) == 1
    finding = findings[0]
    assert isinstance(finding, GovernanceFinding)
    assert finding.kind == "implementation_plan_deviation"
    assert finding.class_name == "accepted_plan_deviation"
    assert finding.workflow_related is True
    assert finding.product_defect_related is False
    # The implementation log anchor carries the journal path + line.
    assert finding.implementation_log_anchors


def test_engine_process_anchors_skips_non_accepted_heading_anchors() -> None:
    """Non-accepted-event anchors do NOT emit accepted_plan_deviation
    findings."""

    bundle = PlanDeviationAnchorBundle(
        journal_path="docs/j.md",
        anchors=[
            _anchor(slice_id="13a", event="starting", accepted=False),
            _anchor(slice_id="13b", event="complete", accepted=False),
        ],
    )
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    assert findings == []


def test_engine_process_anchors_emits_journal_gap_when_bundle_has_no_anchors() -> None:
    """Per doc-16:191-192: missing implementation logs emit
    implementation_journal_gap and block plan-vs-actual recommendations."""

    bundle = PlanDeviationAnchorBundle(journal_path="docs/empty.md")
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.kind == "provenance_gap"
    assert finding.class_name == "implementation_journal_gap"
    assert finding.causal_role == "primary"
    assert finding.severity == "high"
    assert finding.workflow_related is True


def test_engine_process_anchors_does_not_emit_gap_when_bundle_has_anchors() -> None:
    """The gap rule fires only when the bundle has NO anchors."""

    bundle = PlanDeviationAnchorBundle(
        journal_path="docs/j.md",
        anchors=[_anchor(slice_id="13a", event="starting", accepted=False)],
    )
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    # No accepted heading and bundle has anchors -> no findings.
    assert findings == []


def test_engine_process_anchors_carries_parse_gaps_from_bundle() -> None:
    """Parse gaps from the bundle are carried to the engine's
    :attr:`parse_gaps` accumulator."""

    bundle = PlanDeviationAnchorBundle(
        journal_path="docs/x.md",
        anchors=[],
        parse_gaps=[
            PlanDeviationParseGap(
                failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                journal_path="docs/x.md",
                reason="journal_file_missing",
            )
        ],
    )
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    plan_engine.process_anchors(bundle, rule_engine)
    assert len(plan_engine.parse_gaps) == 1
    assert plan_engine.parse_gaps[0].reason == "journal_file_missing"


def test_engine_process_anchors_per_call_accumulator_reset() -> None:
    """Each call RESETS the gap_findings + parse_gaps accumulators per
    the Slice 15 2nd + 4th sub-slice + Slice 16 2nd sub-slice
    precedent."""

    bundle1 = PlanDeviationAnchorBundle(
        journal_path="docs/x.md",
        parse_gaps=[
            PlanDeviationParseGap(
                failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                journal_path="docs/x.md",
                reason="journal_file_missing",
            )
        ],
    )
    bundle2 = PlanDeviationAnchorBundle(
        journal_path="docs/y.md",
        anchors=[_anchor(slice_id="13a", event="accepted", accepted=True)],
    )
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    plan_engine.process_anchors(bundle1, rule_engine)
    assert len(plan_engine.parse_gaps) == 1
    plan_engine.process_anchors(bundle2, rule_engine)
    # Second call must have RESET parse_gaps to bundle2's (empty) list.
    assert plan_engine.parse_gaps == []


def test_engine_process_anchors_non_blocking_never_raises() -> None:
    """The engine NEVER raises a structural failure per
    doc-14:242-243; it returns the empty list + gap finding instead."""

    bundle = PlanDeviationAnchorBundle(
        journal_path="docs/j.md",
        anchors=[_anchor(slice_id="13a", event="accepted", accepted=True)],
    )
    rule_engine = FindingRuleEngine()
    # Force a rule lookup failure by overriding the map to a non-existent rule_id.
    plan_engine = FindingPlanDeviationEngine(
        rule_lookup={
            "accepted_plan_deviation": "nonexistent_rule_v99",
            "implementation_journal_gap": "implementation_journal_gap_v1",
        }
    )
    # Must NOT raise.
    findings = plan_engine.process_anchors(bundle, rule_engine)
    assert findings == []
    # The rule-not-found projects onto a typed gap finding via the
    # REUSED 2nd sub-slice FindingRuleEmissionGap shape.
    assert any(
        isinstance(g, FindingRuleEmissionGap)
        and g.reason == "rule_id_not_found_in_v1_rules"
        for g in plan_engine.gap_findings
    )


def test_engine_process_anchors_defensive_copy_properties() -> None:
    """The :attr:`gap_findings` + :attr:`parse_gaps` + :attr:`rule_lookup`
    properties return DEFENSIVE copies per the Slice 16 2nd sub-slice
    precedent."""

    bundle = PlanDeviationAnchorBundle(
        journal_path="docs/x.md",
        parse_gaps=[
            PlanDeviationParseGap(
                failure_id=FINDING_PLAN_DEVIATION_FAILURE_ID,
                journal_path="docs/x.md",
                reason="r",
            )
        ],
    )
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    plan_engine.process_anchors(bundle, rule_engine)

    # Mutate the returned list and verify the internal state is not affected.
    snapshot = plan_engine.parse_gaps
    snapshot.clear()
    assert len(plan_engine.parse_gaps) == 1

    snapshot_rules = plan_engine.rule_lookup
    snapshot_rules.clear()
    assert plan_engine.rule_lookup != {}


def test_engine_process_anchors_idempotency_key_stable_across_reemit() -> None:
    """Re-emit with identical bundle inputs produces identical
    idempotency_keys (per doc-16:178 + REUSED 2nd sub-slice 7-guard
    logic)."""

    a = _anchor(slice_id="13a", event="accepted", accepted=True, line_start=100)
    bundle = PlanDeviationAnchorBundle(journal_path="docs/j.md", anchors=[a])
    rule_engine_1 = FindingRuleEngine()
    rule_engine_2 = FindingRuleEngine()
    plan_engine_1 = FindingPlanDeviationEngine()
    plan_engine_2 = FindingPlanDeviationEngine()
    findings_1 = plan_engine_1.process_anchors(bundle, rule_engine_1)
    findings_2 = plan_engine_2.process_anchors(bundle, rule_engine_2)
    assert len(findings_1) == 1
    assert len(findings_2) == 1
    assert findings_1[0].idempotency_key == findings_2[0].idempotency_key


# ── DIRECT annotation-identity REUSE assertions (6) ─────────────────────────


def test_reuse_assertion_bundle_anchors_is_typed_anchor_list() -> None:
    """The ``anchors`` field annotation IS ``list[ImplementationArtifactAnchor]``
    by direct identity (the stronger P3-V3-2 pattern)."""

    fields = PlanDeviationAnchorBundle.model_fields
    annotation = fields["anchors"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is ImplementationArtifactAnchor


def test_reuse_assertion_bundle_parse_gaps_is_typed_parse_gap_list() -> None:
    fields = PlanDeviationAnchorBundle.model_fields
    annotation = fields["parse_gaps"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is PlanDeviationParseGap


def test_reuse_assertion_emission_plan_rule_is_finding_rule() -> None:
    fields = PlanDeviationEmissionPlan.model_fields
    assert fields["rule"].annotation is FindingRule


def test_reuse_assertion_emission_plan_severity_is_finding_severity() -> None:
    fields = PlanDeviationEmissionPlan.model_fields
    assert fields["severity"].annotation is FindingSeverity


def test_reuse_assertion_emission_plan_causal_role_is_finding_causal_role() -> None:
    fields = PlanDeviationEmissionPlan.model_fields
    assert fields["causal_role"].annotation is FindingCausalRole


def test_reuse_assertion_emission_plan_source_anchor_is_anchor_or_none() -> None:
    fields = PlanDeviationEmissionPlan.model_fields
    annotation = fields["source_anchor"].annotation
    # Pydantic v2 stores `ImplementationArtifactAnchor | None` as a
    # typing.Optional[ImplementationArtifactAnchor]. Decompose via
    # get_args to assert direct identity.
    args = get_args(annotation)
    assert ImplementationArtifactAnchor in args
    assert type(None) in args


# ── failure_router wiring tests (5) ─────────────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    from typing import get_args as _ga

    from iriai_build_v2.workflows.develop.execution.failure_router import FailureType

    assert FINDING_PLAN_DEVIATION_FAILURE_ID in _ga(FailureType)


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    from iriai_build_v2.workflows.develop.execution.failure_router import FAILURE_TYPES

    assert FINDING_PLAN_DEVIATION_FAILURE_ID in FAILURE_TYPES


def test_failure_router_retryable_includes_new_id() -> None:
    from iriai_build_v2.workflows.develop.execution.failure_router import (
        _RETRYABLE_FAILURE_TYPES,
    )

    assert FINDING_PLAN_DEVIATION_FAILURE_ID in _RETRYABLE_FAILURE_TYPES


def test_failure_router_route_under_evidence_corruption_to_retry_governance() -> None:
    """The route registers under EXISTING ``evidence_corruption`` with
    REUSED ``retry_governance_projection`` action (NOT a new action)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import _ROUTE_ROWS

    matches = [
        (policy, route)
        for policy, route in _ROUTE_ROWS
        if route.failure_type == FINDING_PLAN_DEVIATION_FAILURE_ID
    ]
    assert len(matches) == 1
    policy, route = matches[0]
    assert route.failure_class == "evidence_corruption"
    assert route.action == "retry_governance_projection"
    # Non-blocking: NOT quiesce or operator_required.
    assert route.action not in {"quiesce", "operator_required"}
    # Retryable + not deterministic.
    assert policy.retryable is True
    assert policy.deterministic is False


def test_failure_router_action_is_not_a_new_action() -> None:
    """The REUSED action is in the existing ROUTE_ACTIONS tuple
    (NOT a new RouteAction)."""

    from iriai_build_v2.workflows.develop.execution.failure_router import ROUTE_ACTIONS

    assert "retry_governance_projection" in ROUTE_ACTIONS


# ── Slice 13A REUSE / no redefinition assertions (3) ────────────────────────


def test_no_local_redefinition_of_slice13a_completeness_shapes() -> None:
    """The 3rd-A sub-slice module MUST NOT define
    ``CompletenessState`` / ``EvidenceCompleteness`` /
    ``AuthoritativePromptContextRouting`` /
    ``AuthoritativeGateCompanionRecord`` /
    ``AuthoritativeSnapshotClassifierRouting`` locally per
    doc-16:201-291."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_plan_deviation_engine.py"
    ).read_text()
    assert "class CompletenessState" not in src
    assert "class EvidenceCompleteness" not in src
    assert "class AuthoritativePromptContextRouting" not in src
    assert "class AuthoritativeGateCompanionRecord" not in src
    assert "class AuthoritativeSnapshotClassifierRouting" not in src


def test_class_name_mapping_includes_both_third_A_classes() -> None:
    """The REUSED Slice 16 2nd sub-slice CLASS_NAME_TO_FINDING_KIND
    mapping covers both classes this sub-slice owns."""

    assert CLASS_NAME_TO_FINDING_KIND["accepted_plan_deviation"] == (
        "implementation_plan_deviation"
    )
    assert CLASS_NAME_TO_FINDING_KIND["implementation_journal_gap"] == (
        "provenance_gap"
    )


def test_third_A_classes_are_in_v1_required_class_names() -> None:
    """Both classes this sub-slice owns are in the Slice 16 1st sub-slice
    REQUIRED_V1_FINDING_CLASS_NAMES tuple."""

    assert "accepted_plan_deviation" in REQUIRED_V1_FINDING_CLASS_NAMES
    assert "implementation_journal_gap" in REQUIRED_V1_FINDING_CLASS_NAMES


# ── doc-16 step coverage / end-to-end across 2 v1 rules ─────────────────────


def test_end_to_end_accepted_plan_deviation_finding_shape() -> None:
    """End-to-end: a complete journal corpus with both events emits the
    expected typed surface."""

    body = (
        "## 2026-05-25 -- Slice 16 ACCEPTED\n"
        "Done.\n"
    )
    bundle = parse_plan_deviation_anchors("docs/j.md", body=body)
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    # At least one accepted finding.
    accepted = [f for f in findings if f.class_name == "accepted_plan_deviation"]
    assert len(accepted) == 1
    f = accepted[0]
    assert f.kind == "implementation_plan_deviation"
    assert f.severity == "low"
    assert f.workflow_related is True
    assert f.causal_role == "contributing"


def test_end_to_end_implementation_journal_gap_finding_shape() -> None:
    """End-to-end: a missing/empty journal emits the typed gap finding
    with the expected shape."""

    bundle = parse_plan_deviation_anchors("docs/missing.md", body="")
    assert bundle.anchors == []
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    gaps = [f for f in findings if f.class_name == "implementation_journal_gap"]
    assert len(gaps) == 1
    f = gaps[0]
    assert f.kind == "provenance_gap"
    assert f.severity == "high"
    assert f.causal_role == "primary"
    assert f.workflow_related is True
    assert f.implementation_log_anchors == ["docs/missing.md"]


def test_end_to_end_parse_failure_path_does_not_emit_findings() -> None:
    """When the journal file is missing the engine sees an empty-anchor
    bundle and emits ONLY the gap finding; the parse gap is also
    carried."""

    bundle = parse_plan_deviation_anchors("/nonexistent/never/there.md")
    assert bundle.anchors == []
    assert len(bundle.parse_gaps) == 1
    rule_engine = FindingRuleEngine()
    plan_engine = FindingPlanDeviationEngine()
    findings = plan_engine.process_anchors(bundle, rule_engine)
    # The gap rule fires because anchors is empty.
    assert len(findings) == 1
    assert findings[0].class_name == "implementation_journal_gap"
    # And the parse gap is carried.
    assert len(plan_engine.parse_gaps) == 1
    assert plan_engine.parse_gaps[0].reason == "journal_file_missing"
