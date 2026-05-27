"""Slice 16 3rd-B sub-slice -- unit tests for the
``execution_control/finding_reviewer_test_failure_engine.py`` governance
reviewer-finding + late-test-failure engine module.

Covers (per doc-16:164-165 § Refactoring Steps step 5 remaining
categories; THIS SUB-SLICE owns reviewer-finding +
late-test-failure rules consuming the 13c + 13d parsers; the 3rd-A
sub-slice owns ``accepted_plan_deviation`` + ``implementation_journal_gap``):

- The 2 new typed shapes :class:`ReviewerTestFailureAnchorBundle` +
  :class:`ReviewerTestFailureParseGap` (both carry
  ``ConfigDict(extra="forbid")``).
- The typed :data:`FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID` Literal.
- The pure helpers :func:`parse_reviewer_test_failure_anchors` +
  :func:`compute_reviewer_finding_inputs` +
  :func:`compute_late_test_failure_inputs`.
- The :class:`FindingReviewerTestFailureEngine` -- end-to-end against
  the REUSED Slice 16 2nd sub-slice
  :class:`~iriai_build_v2.execution_control.finding_rule_engine.FindingRuleEngine`
  emitter (the 7-guard logic is NOT re-implemented; this engine
  delegates; mirrors Slice 16 3rd-A sub-slice
  :class:`~iriai_build_v2.execution_control.finding_plan_deviation_engine.FindingPlanDeviationEngine`
  pattern verbatim).
- Failure router wiring -- NEW typed failure id
  ``finding_reviewer_test_failure_parse_failed`` under EXISTING
  ``evidence_corruption`` failure_class with REUSED Slice 14 2nd
  sub-slice ``retry_governance_projection`` NON-blocking RouteAction.
- DIRECT annotation-identity REUSE assertions (the stronger P3-V3-2
  pattern Slice 14 V3 reviewer flagged + Slice 15 + Slice 16 1st-3rd-A
  sub-slices adopted): every typed-shape field annotation that
  references a Slice 13a / Slice 13c / Slice 13d / Slice 16 1st-3rd-A
  sub-slice typed shape is asserted via ``get_origin`` + ``get_args``
  decomposition + ``is`` identity comparison.
- doc-16:201-291 Slice 13A awareness: no local redefinition of Slice
  13A typed shapes.

Per the implementer prompt § "Non-negotiables" -- fail-closed on every
field validator; no executor wiring outside this slice's own acceptance
tests; the Slice 13a + Slice 13c + Slice 13d + Slice 13A + Slice 14 +
Slice 15 + 16 1st + 2nd + 3rd-A sub-slice modules + tests remain byte-
identical (only the ``failure_router.py`` pure-data add lands).
"""

from __future__ import annotations

import inspect
from pathlib import Path
from typing import get_args, get_origin, get_type_hints

import pytest
from pydantic import ValidationError

from iriai_build_v2.execution_control.finding_engine import (
    FindingCausalRole,
    FindingKind,
    FindingSeverity,
    GovernanceFinding,
    REQUIRED_V1_FINDING_CLASS_NAMES,
)
from iriai_build_v2.execution_control.finding_reviewer_test_failure_engine import (
    FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
    FindingReviewerTestFailureEngine,
    ReviewerTestFailureAnchorBundle,
    ReviewerTestFailureParseGap,
    compute_late_test_failure_inputs,
    compute_reviewer_finding_inputs,
    parse_reviewer_test_failure_anchors,
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


def _journal_anchor(
    *,
    slice_id: str = "16-third-B",
    event: JournalEventName = "finding",
    accepted: bool = False,
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


def _decision_log_anchor(
    *,
    slice_id: str = "16-third-B",
    event: JournalEventName = "test_result",
    accepted: bool = False,
    decision_log_line: int | None = 42,
    line_start: int | None = None,
    journal_path: str = "docs/execution-control-plane/implementation-decisions.jsonl",
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


def _rule_evidence_conflict() -> FindingRule:
    for rule in REQUIRED_V1_FINDING_RULES:
        if rule.rule_id == "governance_evidence_conflict_v1":
            return rule
    raise AssertionError("Expected governance_evidence_conflict_v1 in v1 rules")


# ── Module surface tests (6) ───────────────────────────────────────────────


def test_module_all_lists_documented_surface_exactly() -> None:
    """The ``__all__`` list pins the module's typed surface."""

    from iriai_build_v2.execution_control import (
        finding_reviewer_test_failure_engine as mod,
    )

    assert set(mod.__all__) == {
        "FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID",
        "ReviewerTestFailureAnchorBundle",
        "ReviewerTestFailureParseGap",
        "parse_reviewer_test_failure_anchors",
        "compute_reviewer_finding_inputs",
        "compute_late_test_failure_inputs",
        "FindingReviewerTestFailureEngine",
    }


def test_module_does_not_redefine_slice13a_or_slice16_typed_shapes() -> None:
    """The 3rd-B sub-slice MUST NOT redefine any Slice 13a / Slice 13c /
    Slice 13d / Slice 16 1st-3rd-A sub-slice typed shape."""

    from iriai_build_v2.execution_control import (
        finding_reviewer_test_failure_engine as mod,
    )

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
    # The Slice 16 3rd-A sub-slice typed shapes MUST NOT be redefined.
    for forbidden in (
        "FINDING_PLAN_DEVIATION_FAILURE_ID",
        "PlanDeviationAnchorBundle",
        "PlanDeviationEmissionPlan",
        "PlanDeviationParseGap",
        "parse_plan_deviation_anchors",
        "compute_accepted_plan_deviation_inputs",
        "compute_implementation_journal_gap_inputs",
        "FindingPlanDeviationEngine",
    ):
        assert forbidden not in mod.__all__, (
            f"{forbidden} must NOT be in __all__ -- it is REUSED via the 3rd-A "
            f"sub-slice"
        )


def test_module_import_discipline_no_implementation_py() -> None:
    """The 3rd-B sub-slice module MUST NOT import from ``implementation.py``."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_reviewer_test_failure_engine.py"
    ).read_text()
    assert "from iriai_build_v2.workflows.develop.implementation" not in src
    assert "import implementation" not in src


def test_module_import_discipline_no_failure_router() -> None:
    """The 3rd-B sub-slice module MUST NOT import from ``failure_router.py``."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_reviewer_test_failure_engine.py"
    ).read_text()
    assert "from iriai_build_v2.workflows.develop.execution.failure_router" not in src
    assert "import failure_router" not in src


def test_module_import_discipline_no_third_A_sub_slice() -> None:
    """The 3rd-B sub-slice module MUST NOT import from the 3rd-A
    sub-slice module -- each sub-slice is foundational + independent."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_reviewer_test_failure_engine.py"
    ).read_text()
    # Check for actual import statements (not docstring mentions) at the
    # top of the file. Allow docstring mentions for cross-reference.
    import re

    import_re = re.compile(
        r"^(\s*)(?:from\s+\S*finding_plan_deviation_engine\s+import|"
        r"import\s+\S*finding_plan_deviation_engine)",
        re.MULTILINE,
    )
    assert import_re.search(src) is None, (
        "Slice 16 3rd-A sub-slice finding_plan_deviation_engine MUST NOT "
        "be imported in the 3rd-B sub-slice (each sub-slice is "
        "foundational + independent)"
    )


def test_package_init_does_not_re_export_finding_reviewer_test_failure_engine() -> None:
    """The execution_control package ``__init__.py`` MUST NOT re-export
    the 3rd-B sub-slice module per the Slice 13A/14/15/16-1st/16-2nd/16-3rd-A
    precedent."""

    from iriai_build_v2 import execution_control as pkg

    assert "FindingReviewerTestFailureEngine" not in pkg.__all__
    assert "parse_reviewer_test_failure_anchors" not in pkg.__all__
    assert "ReviewerTestFailureAnchorBundle" not in pkg.__all__


# ── FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID tests (2) ─────────────────────


def test_finding_reviewer_test_failure_failure_id_exact_value() -> None:
    """The typed Literal carries the expected value verbatim."""

    assert (
        FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID
        == "finding_reviewer_test_failure_parse_failed"
    )


def test_finding_reviewer_test_failure_failure_id_registered_in_failure_router() -> None:
    """The new typed failure id is registered in the failure_router
    typed surface."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    assert FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID in fr.FAILURE_TYPES


# ── ReviewerTestFailureParseGap typed-shape tests (5) ──────────────────────


def test_reviewer_test_failure_parse_gap_required_fields() -> None:
    """The typed gap carries the required typed fields."""

    gap = ReviewerTestFailureParseGap(
        failure_id="finding_reviewer_test_failure_parse_failed",
        source_path="/tmp/journal.md",
        source_kind="journal",
        reason="journal_file_missing",
    )
    assert gap.failure_id == "finding_reviewer_test_failure_parse_failed"
    assert gap.source_path == "/tmp/journal.md"
    assert gap.source_kind == "journal"
    assert gap.reason == "journal_file_missing"
    assert gap.anchor_kind == ""
    assert gap.evidence_payload == {}


def test_reviewer_test_failure_parse_gap_extra_forbid() -> None:
    """``ConfigDict(extra="forbid")`` rejects unknown kwargs."""

    with pytest.raises(ValidationError):
        ReviewerTestFailureParseGap(
            failure_id="finding_reviewer_test_failure_parse_failed",
            source_path="/tmp/journal.md",
            source_kind="journal",
            reason="journal_file_missing",
            unknown_field="forbidden",
        )


def test_reviewer_test_failure_parse_gap_rejects_wrong_failure_id() -> None:
    """The Literal type rejects a wrong failure_id value."""

    with pytest.raises(ValidationError):
        ReviewerTestFailureParseGap(
            failure_id="wrong_failure_id",
            source_path="/tmp/journal.md",
            source_kind="journal",
            reason="journal_file_missing",
        )


def test_reviewer_test_failure_parse_gap_rejects_wrong_source_kind() -> None:
    """The source_kind Literal rejects a value outside the 2-value set."""

    with pytest.raises(ValidationError):
        ReviewerTestFailureParseGap(
            failure_id="finding_reviewer_test_failure_parse_failed",
            source_path="/tmp/journal.md",
            source_kind="not_a_valid_kind",
            reason="journal_file_missing",
        )


def test_reviewer_test_failure_parse_gap_json_roundtrip() -> None:
    """JSON serialization round-trips cleanly."""

    gap = ReviewerTestFailureParseGap(
        failure_id="finding_reviewer_test_failure_parse_failed",
        source_path="/tmp/journal.md",
        source_kind="decision_log",
        reason="unexpected_parser_exception",
        anchor_kind="test_result",
        evidence_payload={"error_type": "ValueError", "error_detail": "bad"},
    )
    json_str = gap.model_dump_json()
    restored = ReviewerTestFailureParseGap.model_validate_json(json_str)
    assert restored == gap


# ── ReviewerTestFailureAnchorBundle typed-shape tests (4) ──────────────────


def test_reviewer_test_failure_anchor_bundle_required_fields_default_empty() -> None:
    """Both path fields required; lists default to empty."""

    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="/tmp/journal.md",
        decision_log_path="/tmp/decisions.jsonl",
    )
    assert bundle.journal_path == "/tmp/journal.md"
    assert bundle.decision_log_path == "/tmp/decisions.jsonl"
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert bundle.parse_gaps == []


def test_reviewer_test_failure_anchor_bundle_extra_forbid() -> None:
    """``ConfigDict(extra="forbid")`` rejects unknown kwargs."""

    with pytest.raises(ValidationError):
        ReviewerTestFailureAnchorBundle(
            journal_path="/tmp/journal.md",
            decision_log_path="/tmp/decisions.jsonl",
            unknown_field="forbidden",
        )


def test_reviewer_test_failure_anchor_bundle_carries_typed_anchors() -> None:
    """The bundle carries the typed Slice 13a anchors verbatim."""

    a = _journal_anchor()
    b = _decision_log_anchor()
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="/tmp/journal.md",
        decision_log_path="/tmp/decisions.jsonl",
        journal_anchors=[a],
        decision_log_anchors=[b],
    )
    assert bundle.journal_anchors == [a]
    assert bundle.decision_log_anchors == [b]
    assert isinstance(bundle.journal_anchors[0], ImplementationArtifactAnchor)
    assert isinstance(bundle.decision_log_anchors[0], ImplementationArtifactAnchor)


def test_reviewer_test_failure_anchor_bundle_json_roundtrip() -> None:
    """JSON serialization round-trips cleanly with typed anchors."""

    a = _journal_anchor()
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="/tmp/journal.md",
        decision_log_path="/tmp/decisions.jsonl",
        journal_anchors=[a],
    )
    json_str = bundle.model_dump_json()
    restored = ReviewerTestFailureAnchorBundle.model_validate_json(json_str)
    assert restored.journal_path == bundle.journal_path
    assert restored.decision_log_path == bundle.decision_log_path
    assert len(restored.journal_anchors) == 1
    assert restored.journal_anchors[0] == a


# ── parse_reviewer_test_failure_anchors tests (7) ──────────────────────────


def test_parse_reviewer_test_failure_anchors_parses_inline_bodies() -> None:
    """Pre-loaded bodies skip disk read and project to typed bundle."""

    # Use a lowercase finding-id (the FINDING_ID_REGEX in models.py:254
    # uses ``[a-z]`` not ``[A-Z]`` for the slice suffix; P3-16-3b-1
    # parses while P3-16-3B-1 does not). The finding-id MUST appear in
    # a heading section (after a ``## YYYY-MM-DD -- Slice <id> <STATE>``
    # heading) for the Slice 13c parser to emit a per-line finding
    # anchor per ``journal_parser.py:497-535``.
    journal_body = (
        "## 2026-05-20 — Slice 16 COMPLETE\n"
        "\n"
        "Some content with P3-16-3b-1 finding mentioned in prose.\n"
        "\n"
        "## 2026-05-25 — Slice 16 ACCEPTED\n"
        "\n"
        "336 passed in 59.14s\n"
        "\n"
        "Finalised.\n"
    )
    decision_log_body = ""  # empty decision log parses to empty anchors
    bundle = parse_reviewer_test_failure_anchors(
        "docs/test-journal.md",
        "docs/test-decisions.jsonl",
        journal_body=journal_body,
        decision_log_body=decision_log_body,
    )
    assert bundle.journal_path == "docs/test-journal.md"
    assert bundle.decision_log_path == "docs/test-decisions.jsonl"
    assert bundle.parse_gaps == []
    # Expect at least one finding anchor + one test_result anchor.
    events = {a.event for a in bundle.journal_anchors}
    assert "finding" in events
    assert "test_result" in events


def test_parse_reviewer_test_failure_anchors_missing_journal_projects_typed_gap() -> None:
    """A missing journal file fails CLOSED with a typed parse gap for
    the journal parser; NEVER raises."""

    bundle = parse_reviewer_test_failure_anchors(
        "/nonexistent/journal.md",
        "/nonexistent/decisions.jsonl",
    )
    assert bundle.journal_path == "/nonexistent/journal.md"
    assert bundle.decision_log_path == "/nonexistent/decisions.jsonl"
    assert bundle.journal_anchors == []
    # Both parsers fail with file-missing; expect 2 gaps.
    assert len(bundle.parse_gaps) == 2
    sources = {g.source_kind for g in bundle.parse_gaps}
    assert sources == {"journal", "decision_log"}
    for gap in bundle.parse_gaps:
        assert gap.failure_id == FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID


def test_parse_reviewer_test_failure_anchors_only_journal_missing(tmp_path: Path) -> None:
    """Only journal missing -> only journal gap; decision_log_anchors
    parses successfully from empty body."""

    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text("")  # empty file -> empty anchors
    bundle = parse_reviewer_test_failure_anchors(
        "/nonexistent/journal.md",
        decisions_path,
    )
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert len(bundle.parse_gaps) == 1
    gap = bundle.parse_gaps[0]
    assert gap.source_kind == "journal"
    assert gap.reason == "journal_file_missing"


def test_parse_reviewer_test_failure_anchors_only_decision_log_missing(
    tmp_path: Path,
) -> None:
    """Only decision_log missing -> only decision_log gap; journal_anchors
    parses successfully from empty body."""

    journal_path = tmp_path / "journal.md"
    journal_path.write_text("")  # empty file -> empty anchors
    bundle = parse_reviewer_test_failure_anchors(
        journal_path,
        "/nonexistent/decisions.jsonl",
    )
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert len(bundle.parse_gaps) == 1
    gap = bundle.parse_gaps[0]
    assert gap.source_kind == "decision_log"
    assert gap.reason == "decision_log_file_missing"


def test_parse_reviewer_test_failure_anchors_empty_paths_project_typed_gaps() -> None:
    """Empty paths for BOTH journal + decision_log fail CLOSED with
    typed gaps; NEVER raises."""

    bundle = parse_reviewer_test_failure_anchors("", "")
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert len(bundle.parse_gaps) == 2
    reasons = {g.reason for g in bundle.parse_gaps}
    assert reasons == {"source_path_empty"}


def test_parse_reviewer_test_failure_anchors_empty_bodies_parse_clean() -> None:
    """Empty bodies parse cleanly to empty anchors (legitimate result)."""

    bundle = parse_reviewer_test_failure_anchors(
        "docs/journal.md",
        "docs/decisions.jsonl",
        journal_body="",
        decision_log_body="",
    )
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert bundle.parse_gaps == []


def test_parse_reviewer_test_failure_anchors_never_raises_unexpected() -> None:
    """The pure-helper NEVER raises an unexpected exception per the
    feedback_no_silent_degradation rule."""

    # Path with NUL byte raises a ValueError on Linux file ops; ensure
    # the typed bundle still constructs with parse gaps.
    bundle = parse_reviewer_test_failure_anchors(
        "/tmp/has\x00nul/journal.md",
        "/tmp/has\x00nul/decisions.jsonl",
    )
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert len(bundle.parse_gaps) >= 1
    for gap in bundle.parse_gaps:
        assert gap.failure_id == FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID


# ── compute_reviewer_finding_inputs tests (5) ──────────────────────────────


def test_compute_reviewer_finding_inputs_basic() -> None:
    a = _journal_anchor(event="finding", open_findings=["P3-16-3B-1"])
    rule = _rule_evidence_conflict()
    inputs = compute_reviewer_finding_inputs(a, rule)
    assert inputs.rule == rule
    assert inputs.class_name == "governance_evidence_conflict"
    assert inputs.severity == "medium"  # default
    assert inputs.confidence == 0.7  # default
    assert inputs.requires_policy_artifact is False
    assert inputs.workflow_related is True
    assert inputs.product_defect_related is False
    assert inputs.causal_role == "contributing"


def test_compute_reviewer_finding_inputs_log_anchor_uses_line_start() -> None:
    a = _journal_anchor(line_start=42, decision_log_line=None)
    inputs = compute_reviewer_finding_inputs(a, _rule_evidence_conflict())
    assert len(inputs.implementation_log_anchors) == 1
    assert inputs.implementation_log_anchors[0].endswith(":42")


def test_compute_reviewer_finding_inputs_emits_empty_primary_refs() -> None:
    """The governance_evidence_conflict class is in EVIDENCE_GAP_FINDING_KINDS;
    primary_evidence_refs may be empty per doc-16:159-161."""

    a = _journal_anchor(event="finding")
    inputs = compute_reviewer_finding_inputs(a, _rule_evidence_conflict())
    assert inputs.primary_evidence_refs == []
    # Confirm the kind is in the allowance set.
    kind = CLASS_NAME_TO_FINDING_KIND[inputs.class_name]
    assert kind in EVIDENCE_GAP_FINDING_KINDS


def test_compute_reviewer_finding_inputs_affected_scope_carries_open_findings() -> None:
    """The affected_scope dict records the open_findings list + the
    finding_source marker."""

    a = _journal_anchor(
        event="finding", open_findings=["P1-16-3B-1", "P2-16-3B-2"]
    )
    inputs = compute_reviewer_finding_inputs(a, _rule_evidence_conflict())
    assert inputs.affected_scope["slice_id"] == a.slice_id
    assert inputs.affected_scope["event"] == "finding"
    assert inputs.affected_scope["open_findings"] == ["P1-16-3B-1", "P2-16-3B-2"]
    assert inputs.affected_scope["finding_source"] == "reviewer"


def test_compute_reviewer_finding_inputs_recommended_action_display_default() -> None:
    """The default recommended_action_display references doc-16:183-184
    + the open findings (when present)."""

    a = _journal_anchor(event="finding", open_findings=["P1-16-3B-1"])
    inputs = compute_reviewer_finding_inputs(a, _rule_evidence_conflict())
    assert "governance_evidence_conflict" in inputs.recommended_action_display
    assert "P1-16-3B-1" in inputs.recommended_action_display


# ── compute_late_test_failure_inputs tests (4) ─────────────────────────────


def test_compute_late_test_failure_inputs_basic() -> None:
    a = _journal_anchor(event="test_result", open_findings=[])
    inputs = compute_late_test_failure_inputs(a, _rule_evidence_conflict())
    assert inputs.class_name == "governance_evidence_conflict"
    assert inputs.severity == "high"  # default
    assert inputs.confidence == 0.8  # default
    assert inputs.requires_policy_artifact is False
    assert inputs.workflow_related is True
    assert inputs.causal_role == "contributing"


def test_compute_late_test_failure_inputs_log_anchor_uses_decision_log_line() -> None:
    """For decision-log-emitted anchors, the log_anchor uses
    journal_path + '#L<decision_log_line>'."""

    a = _decision_log_anchor(line_start=None, decision_log_line=137)
    inputs = compute_late_test_failure_inputs(a, _rule_evidence_conflict())
    assert len(inputs.implementation_log_anchors) == 1
    assert inputs.implementation_log_anchors[0].endswith("#L137")


def test_compute_late_test_failure_inputs_source_corpus_detection() -> None:
    """The affected_scope.source_corpus field reflects which parser
    emitted the anchor (per the 13c⊕13d bidirectional invariant)."""

    journal_anchor = _journal_anchor(
        event="test_result", line_start=100, decision_log_line=None
    )
    decision_log_anchor = _decision_log_anchor(
        event="test_result", line_start=None, decision_log_line=42
    )
    journal_inputs = compute_late_test_failure_inputs(
        journal_anchor, _rule_evidence_conflict()
    )
    decision_log_inputs = compute_late_test_failure_inputs(
        decision_log_anchor, _rule_evidence_conflict()
    )
    assert journal_inputs.affected_scope["source_corpus"] == "journal"
    assert decision_log_inputs.affected_scope["source_corpus"] == "decision_log"
    assert journal_inputs.affected_scope["finding_source"] == "late_test_failure"
    assert decision_log_inputs.affected_scope["finding_source"] == "late_test_failure"


def test_compute_late_test_failure_inputs_emits_empty_primary_refs() -> None:
    """The governance_evidence_conflict class is in EVIDENCE_GAP_FINDING_KINDS;
    primary_evidence_refs may be empty per doc-16:159-161 -- a late
    test failure is the observation itself + the conflict between it +
    prior acceptance is the finding."""

    a = _journal_anchor(event="test_result")
    inputs = compute_late_test_failure_inputs(a, _rule_evidence_conflict())
    assert inputs.primary_evidence_refs == []
    kind = CLASS_NAME_TO_FINDING_KIND[inputs.class_name]
    assert kind in EVIDENCE_GAP_FINDING_KINDS


# ── FindingReviewerTestFailureEngine end-to-end tests (10) ─────────────────


def test_engine_default_rule_lookup_covers_shared_class() -> None:
    """The default rule lookup maps governance_evidence_conflict to
    the v1 rule."""

    engine = FindingReviewerTestFailureEngine()
    assert engine.rule_lookup == {
        "governance_evidence_conflict": "governance_evidence_conflict_v1",
    }


def test_engine_process_anchors_emits_reviewer_finding() -> None:
    """A finding anchor in journal_anchors yields one emitted
    governance_evidence_conflict finding."""

    a = _journal_anchor(event="finding", open_findings=["P3-16-3B-1"])
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        journal_anchors=[a],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.class_name == "governance_evidence_conflict"
    assert finding.kind == "governance_evidence_conflict"
    assert finding.affected_scope["finding_source"] == "reviewer"


def test_engine_process_anchors_emits_late_test_failure_finding() -> None:
    """A test_result anchor in journal_anchors yields one emitted
    governance_evidence_conflict finding (late test failure variant)."""

    a = _journal_anchor(event="test_result")
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        journal_anchors=[a],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.class_name == "governance_evidence_conflict"
    assert finding.affected_scope["finding_source"] == "late_test_failure"


def test_engine_process_anchors_emits_decision_log_test_failure() -> None:
    """A test_result anchor in decision_log_anchors yields one emitted
    governance_evidence_conflict finding."""

    a = _decision_log_anchor(event="test_result")
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        decision_log_anchors=[a],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert len(findings) == 1
    assert findings[0].affected_scope["source_corpus"] == "decision_log"


def test_engine_process_anchors_skips_non_relevant_events() -> None:
    """Anchors with event values OTHER than 'finding' / 'test_result'
    do NOT emit findings (the engine filters by event)."""

    starting_anchor = _journal_anchor(event="starting", accepted=False)
    accepted_anchor = _journal_anchor(event="accepted", accepted=True)
    complete_anchor = _journal_anchor(event="complete", accepted=False)
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        journal_anchors=[starting_anchor, accepted_anchor, complete_anchor],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert findings == []


def test_engine_process_anchors_both_rules_can_fire_from_same_bundle() -> None:
    """A bundle with BOTH a finding anchor AND a test_result anchor
    yields TWO emitted findings."""

    finding_anchor = _journal_anchor(
        event="finding", open_findings=["P3-16-3B-1"], line_start=100
    )
    test_anchor = _journal_anchor(
        event="test_result", line_start=200
    )
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        journal_anchors=[finding_anchor, test_anchor],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert len(findings) == 2
    finding_sources = [f.affected_scope["finding_source"] for f in findings]
    assert "reviewer" in finding_sources
    assert "late_test_failure" in finding_sources


def test_engine_process_anchors_carries_parse_gaps_from_bundle() -> None:
    """The engine carries the bundle's parse_gaps onto its own parse_gaps
    property after process_anchors runs."""

    gap = ReviewerTestFailureParseGap(
        failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
        source_path="/tmp/journal.md",
        source_kind="journal",
        reason="journal_file_missing",
    )
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="/tmp/journal.md",
        decision_log_path="/tmp/decisions.jsonl",
        parse_gaps=[gap],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    engine.process_anchors(bundle, rule_engine)
    assert engine.parse_gaps == [gap]


def test_engine_process_anchors_per_call_accumulator_reset() -> None:
    """Each call to process_anchors RESETS the gap_findings + parse_gaps
    accumulators."""

    # First call with a parse gap.
    gap = ReviewerTestFailureParseGap(
        failure_id=FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID,
        source_path="/tmp/journal.md",
        source_kind="journal",
        reason="journal_file_missing",
    )
    bundle1 = ReviewerTestFailureAnchorBundle(
        journal_path="/tmp/journal.md",
        decision_log_path="/tmp/decisions.jsonl",
        parse_gaps=[gap],
    )
    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    engine.process_anchors(bundle1, rule_engine)
    assert engine.parse_gaps == [gap]

    # Second call with NO parse gap; the prior gap is cleared.
    bundle2 = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
    )
    engine.process_anchors(bundle2, rule_engine)
    assert engine.parse_gaps == []


def test_engine_process_anchors_non_blocking_never_raises() -> None:
    """The engine NEVER raises; a missing rule projects to gap finding."""

    a = _journal_anchor(event="finding")
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        journal_anchors=[a],
    )
    engine = FindingReviewerTestFailureEngine(
        rule_lookup={"governance_evidence_conflict": "nonexistent_rule_id"},
    )
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert findings == []
    assert len(engine.gap_findings) == 1
    gap = engine.gap_findings[0]
    assert gap.reason == "rule_id_not_found_in_v1_rules"


def test_engine_process_anchors_defensive_copy_properties() -> None:
    """The gap_findings + parse_gaps + rule_lookup properties return
    defensive copies (caller mutation does NOT affect engine state)."""

    engine = FindingReviewerTestFailureEngine()
    rule_lookup_copy = engine.rule_lookup
    rule_lookup_copy["governance_evidence_conflict"] = "mutated"
    assert engine.rule_lookup["governance_evidence_conflict"] == (
        "governance_evidence_conflict_v1"
    )

    gap_findings_copy = engine.gap_findings
    gap_findings_copy.append("mutated")
    assert engine.gap_findings == []

    parse_gaps_copy = engine.parse_gaps
    parse_gaps_copy.append("mutated")
    assert engine.parse_gaps == []


def test_engine_process_anchors_idempotency_key_stable_across_reemit() -> None:
    """Re-emitting the same anchor produces the same finding
    idempotency_key (the REUSED 2nd sub-slice engine's deterministic
    key per doc-16:158)."""

    a = _journal_anchor(event="finding", line_start=100, open_findings=["P3-16-3B-1"])
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
        journal_anchors=[a],
    )
    engine1 = FindingReviewerTestFailureEngine()
    engine2 = FindingReviewerTestFailureEngine()
    findings1 = engine1.process_anchors(bundle, FindingRuleEngine())
    findings2 = engine2.process_anchors(bundle, FindingRuleEngine())
    assert len(findings1) == 1
    assert len(findings2) == 1
    assert findings1[0].idempotency_key == findings2[0].idempotency_key


# ── DIRECT annotation-identity REUSE assertions (6) ────────────────────────


def test_reuse_assertion_bundle_journal_anchors_is_typed_anchor_list() -> None:
    """The bundle.journal_anchors field is annotated as
    ``list[ImplementationArtifactAnchor]`` (REUSED from Slice 13a)."""

    annotation = ReviewerTestFailureAnchorBundle.model_fields["journal_anchors"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is ImplementationArtifactAnchor


def test_reuse_assertion_bundle_decision_log_anchors_is_typed_anchor_list() -> None:
    """The bundle.decision_log_anchors field is annotated as
    ``list[ImplementationArtifactAnchor]`` (REUSED from Slice 13a)."""

    annotation = ReviewerTestFailureAnchorBundle.model_fields[
        "decision_log_anchors"
    ].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is ImplementationArtifactAnchor


def test_reuse_assertion_bundle_parse_gaps_is_typed_parse_gap_list() -> None:
    """The bundle.parse_gaps field is annotated as
    ``list[ReviewerTestFailureParseGap]``."""

    annotation = ReviewerTestFailureAnchorBundle.model_fields["parse_gaps"].annotation
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is ReviewerTestFailureParseGap


def test_reuse_assertion_helper_returns_finding_rule_emission_inputs() -> None:
    """The compute_reviewer_finding_inputs + compute_late_test_failure_inputs
    helpers return the REUSED Slice 16 2nd sub-slice
    :class:`FindingRuleEmissionInputs` type.

    Uses :func:`typing.get_type_hints` to resolve string annotations
    (the source module uses ``from __future__ import annotations`` so
    ``inspect.signature`` returns string annotations; ``get_type_hints``
    resolves them against the module globals)."""

    hints_reviewer = get_type_hints(compute_reviewer_finding_inputs)
    hints_late = get_type_hints(compute_late_test_failure_inputs)
    assert hints_reviewer["return"] is FindingRuleEmissionInputs
    assert hints_late["return"] is FindingRuleEmissionInputs


def test_reuse_assertion_process_anchors_returns_governance_finding_list() -> None:
    """The FindingReviewerTestFailureEngine.process_anchors method returns
    ``list[GovernanceFinding]`` (REUSED from Slice 16 1st sub-slice).

    Uses :func:`typing.get_type_hints` to resolve string annotations
    (the source module uses ``from __future__ import annotations``)."""

    hints = get_type_hints(FindingReviewerTestFailureEngine.process_anchors)
    annotation = hints["return"]
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is GovernanceFinding


def test_reuse_assertion_engine_gap_findings_is_typed_emission_gap_list() -> None:
    """The engine.gap_findings property returns
    ``list[FindingRuleEmissionGap]`` (REUSED from Slice 16 2nd sub-slice).

    Uses :func:`typing.get_type_hints` to resolve string annotations
    (the source module uses ``from __future__ import annotations``)."""

    engine = FindingReviewerTestFailureEngine()
    bundle = ReviewerTestFailureAnchorBundle(
        journal_path="docs/journal.md",
        decision_log_path="docs/decisions.jsonl",
    )
    engine.process_anchors(bundle, FindingRuleEngine())
    # Property returns an empty list when no gaps occurred; verify type.
    assert isinstance(engine.gap_findings, list)
    # Verify the typed property annotation by introspecting the getter
    # via get_type_hints (resolves string annotations from the module).
    fget = FindingReviewerTestFailureEngine.gap_findings.fget
    hints = get_type_hints(fget)
    annotation = hints["return"]
    assert get_origin(annotation) is list
    args = get_args(annotation)
    assert len(args) == 1
    assert args[0] is FindingRuleEmissionGap


# ── failure_router wiring tests (5) ────────────────────────────────────────


def test_failure_router_failure_type_literal_includes_new_id() -> None:
    """The FailureType Literal includes the new typed failure id."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    from typing import get_args

    failure_type_args = get_args(fr.FailureType)
    assert FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID in failure_type_args


def test_failure_router_failure_types_tuple_includes_new_id() -> None:
    """The FAILURE_TYPES tuple includes the new typed failure id."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    assert FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID in fr.FAILURE_TYPES


def test_failure_router_retryable_includes_new_id() -> None:
    """The _RETRYABLE_FAILURE_TYPES frozenset includes the new typed
    failure id (the failure is retryable per the non-blocking observer
    contract)."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    assert FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID in fr._RETRYABLE_FAILURE_TYPES


def test_failure_router_route_under_evidence_corruption_to_retry_governance() -> None:
    """The new typed failure id routes under EVIDENCE_CORRUPTION ->
    retry_governance_projection (NON-blocking; REUSES Slice 14 2nd
    sub-slice action; NOT a new action)."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    route_key = ("evidence_corruption", FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID)
    assert route_key in fr.ROUTE_TABLE
    route = fr.ROUTE_TABLE[route_key]
    assert route.action == "retry_governance_projection"
    # The action MUST NOT be quiesce -- this is a non-blocking observer
    # per doc-14:242-243.
    assert route.action != "quiesce"
    # The action MUST NOT be operator_required -- this is a non-blocking
    # observer per doc-14:242-243.
    assert route.action != "operator_required"


def test_failure_router_action_is_not_a_new_action() -> None:
    """The route uses an EXISTING RouteAction; the 3rd-B sub-slice does
    NOT introduce a new action (REUSES Slice 14 2nd sub-slice's
    retry_governance_projection)."""

    from iriai_build_v2.workflows.develop.execution import failure_router as fr

    route_key = ("evidence_corruption", FINDING_REVIEWER_TEST_FAILURE_FAILURE_ID)
    route = fr.ROUTE_TABLE[route_key]
    # Confirm the action is one of the EXISTING ROUTE_ACTIONS entries.
    assert route.action in fr.ROUTE_ACTIONS
    # The 3rd-B sub-slice MUST NOT introduce a new action; verify the
    # action equals the precise REUSED Slice 14 2nd sub-slice action.
    assert route.action == "retry_governance_projection"


# ── Slice 13A REUSE / no redefinition assertions (3) ───────────────────────


def test_no_local_redefinition_of_slice13a_completeness_shapes() -> None:
    """The 3rd-B sub-slice MUST NOT redefine Slice 13A typed completeness
    shapes (per doc-13a:285-287 + doc-16:201-291)."""

    src = Path(
        "src/iriai_build_v2/execution_control/finding_reviewer_test_failure_engine.py"
    ).read_text()

    # The 13A typed completeness shapes are READ-ONLY references in this
    # module; no local class declarations may shadow them.
    for forbidden in (
        "class CompletenessState",
        "class EvidenceCompleteness",
        "class AuthoritativePromptContextRouting",
        "class AuthoritativeGateCompanionRecord",
        "class AuthoritativeSnapshotClassifierRouting",
    ):
        assert forbidden not in src, (
            f"{forbidden!r} must NOT be redefined locally in the 3rd-B "
            f"sub-slice module"
        )


def test_class_name_mapping_includes_governance_evidence_conflict() -> None:
    """The shared class name 'governance_evidence_conflict' is in the
    REUSED 2nd sub-slice CLASS_NAME_TO_FINDING_KIND mapping (per
    finding_rule_engine.py:228)."""

    assert "governance_evidence_conflict" in CLASS_NAME_TO_FINDING_KIND
    assert (
        CLASS_NAME_TO_FINDING_KIND["governance_evidence_conflict"]
        == "governance_evidence_conflict"
    )


def test_shared_class_name_is_in_v1_required_class_names() -> None:
    """The shared class name 'governance_evidence_conflict' is in the
    REUSED 1st sub-slice REQUIRED_V1_FINDING_CLASS_NAMES tuple (per
    doc-16:120-137)."""

    assert "governance_evidence_conflict" in REQUIRED_V1_FINDING_CLASS_NAMES


# ── doc-16 end-to-end / parse-failure code path (3) ────────────────────────


def test_end_to_end_reviewer_finding_typed_surface() -> None:
    """End-to-end: a finding anchor -> typed inputs -> emitted
    GovernanceFinding with the expected typed shape."""

    a = _journal_anchor(event="finding", open_findings=["P3-16-3B-1"])
    rule_engine = FindingRuleEngine()
    rule = _rule_evidence_conflict()
    inputs = compute_reviewer_finding_inputs(a, rule)
    finding = rule_engine.emit_finding(inputs)
    assert finding is not None
    assert finding.class_name == "governance_evidence_conflict"
    assert finding.kind == "governance_evidence_conflict"
    assert finding.affected_scope["finding_source"] == "reviewer"
    # Per doc-16:159-161 -- governance_evidence_conflict is allowed
    # empty primary refs.
    assert finding.primary_evidence_refs == []
    # The journal anchor is recorded on implementation_log_anchors.
    assert len(finding.implementation_log_anchors) == 1


def test_end_to_end_late_test_failure_typed_surface() -> None:
    """End-to-end: a test_result anchor -> typed inputs -> emitted
    GovernanceFinding with the expected typed shape."""

    a = _decision_log_anchor(event="test_result", decision_log_line=42)
    rule_engine = FindingRuleEngine()
    rule = _rule_evidence_conflict()
    inputs = compute_late_test_failure_inputs(a, rule)
    finding = rule_engine.emit_finding(inputs)
    assert finding is not None
    assert finding.class_name == "governance_evidence_conflict"
    assert finding.kind == "governance_evidence_conflict"
    assert finding.affected_scope["finding_source"] == "late_test_failure"
    assert finding.affected_scope["source_corpus"] == "decision_log"


def test_end_to_end_parse_failure_path_does_not_emit_findings() -> None:
    """A parse failure projects a typed gap; the engine emits NO
    findings + carries the parse gap onto its own parse_gaps."""

    bundle = parse_reviewer_test_failure_anchors(
        "/nonexistent/journal.md",
        "/nonexistent/decisions.jsonl",
    )
    assert bundle.journal_anchors == []
    assert bundle.decision_log_anchors == []
    assert len(bundle.parse_gaps) == 2

    engine = FindingReviewerTestFailureEngine()
    rule_engine = FindingRuleEngine()
    findings = engine.process_anchors(bundle, rule_engine)
    assert findings == []
    assert len(engine.parse_gaps) == 2
    # No rule-emission gaps either (the parse failure short-circuits
    # the per-anchor emission code paths since there are no anchors).
    assert engine.gap_findings == []
