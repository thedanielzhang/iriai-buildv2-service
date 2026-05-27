"""Slice 13d -- unit tests for the JSONL decision-log parser.

Covers the doc-13:184-185 § "Refactoring Steps" step 4 deliverable
("Add a JSONL decision-log parser that rejects malformed rows and records
line numbers as evidence anchors.") for
:func:`iriai_build_v2.workflows.develop.governance.parse_implementation_decision_log`.

Each emitted
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
is round-tripped through the 13a model invariants
(the ID-field validators ``_non_empty_anchor_id_fields`` /
``_line_positive_when_present`` /
``_open_findings_dedup_and_non_empty``) so the parser cannot silently
emit a row the typed shape would reject.

Per the governance prompt § "Non-Negotiables" the parser fails closed
(``ValueError`` on malformed JSON / non-object rows / missing slice
fields, with the 1-indexed line number per doc-13:184 verbatim) and
never silently degrades. Per the prompt § "Bounded reads" the parser is
pure-typed (text-in / typed-rows-out): no file I/O is exercised in the
tests except the dedicated ``tmp_path`` smoke + the real-JSONL smoke
test -- the ``body=`` escape hatch supplies synthetic JSONL directly.

The bidirectional ``13c⊕13d`` invariant (``decision_log_line=None`` ⇔
13c-emitted; ``line_start=None`` ⇔ 13d-emitted) is asserted on every
emitted 13d anchor: ``line_start is None`` and
``decision_log_line == <jsonl-row-no>``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop import governance
from iriai_build_v2.workflows.develop.governance import (
    ImplementationArtifactAnchor,
    parse_implementation_decision_log,
)
from iriai_build_v2.workflows.develop.governance import (
    decision_log_parser as decision_log_parser_module,
)


# ── Real JSONL fixture path ────────────────────────────────────────────────


_REAL_JSONL_PATH = Path(
    "docs/execution-control-plane/implementation-decisions.jsonl"
)


# ── package surface ────────────────────────────────────────────────────────


def test_package_reexports_parse_implementation_decision_log() -> None:
    """The 13d parser surface is re-exported at the package level.

    The package-level strict-equality assertion lives in
    ``tests/test_governance_evidence_models.py::
    test_governance_package_reexports_doc_13_surface``; this test
    asserts the 13d-specific subset is present and is the same Python
    object as the module-local symbol.
    """

    assert "parse_implementation_decision_log" in governance.__all__
    assert hasattr(governance, "parse_implementation_decision_log")
    assert (
        governance.parse_implementation_decision_log
        is decision_log_parser_module.parse_implementation_decision_log
    )


def test_module_all_lists_exactly_parse_implementation_decision_log() -> None:
    """``decision_log_parser.__all__`` is exactly the doc-13:184-185 surface."""

    assert list(decision_log_parser_module.__all__) == [
        "parse_implementation_decision_log"
    ]


# ── per-row parsing (synthetic 3-row JSONL) ────────────────────────────────


def test_three_row_synthetic_jsonl_emits_three_main_anchors() -> None:
    """A 3-row JSONL produces 3 main anchors with ``decision_log_line``
    populated as the 1-indexed row number.

    Per doc-13:147 ``decision_log_line`` is the JSONL line number; per
    doc-13:184 each row produces one evidence anchor. The 13a
    ``_line_positive_when_present`` validator enforces the 1-indexing
    (zero / negative line numbers raise).
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "row 1"}\n'
        '{"slice": "01", "event": "patch", "summary": "row 2"}\n'
        '{"slice": "02", "event": "test", "summary": "row 3"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/test.jsonl", body=body)
    assert len(anchors) == 3
    # decision_log_line is 1-indexed and matches the source row order.
    assert [a.decision_log_line for a in anchors] == [1, 2, 3]
    # All three anchors carry the 13d bidirectional invariant:
    # line_start=None + decision_log_line=<row#>.
    for anchor in anchors:
        assert anchor.line_start is None
        assert anchor.decision_log_line is not None
        assert anchor.decision_log_line >= 1


def test_anchor_carries_required_13a_fields_per_row() -> None:
    """Every emitted anchor populates the 7 doc-13:143-150 fields.

    ``slice_id``, ``journal_path``, ``line_start`` (None for 13d),
    ``decision_log_line`` (the row#), ``event``, ``accepted`` (False
    unless event=accepted), ``open_findings`` (deduplicated list).
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "test row"}\n'
    )
    anchors = parse_implementation_decision_log(
        "docs/execution-control-plane/implementation-decisions.jsonl",
        body=body,
    )
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.slice_id == "00"
    assert anchor.journal_path == (
        "docs/execution-control-plane/implementation-decisions.jsonl"
    )
    assert anchor.line_start is None
    assert anchor.decision_log_line == 1
    assert anchor.event == "decision"  # dispatch -> decision (catch-all)
    assert anchor.accepted is False
    assert anchor.open_findings == []


# ── real-shape rows (sample 1-2 per stage from the real JSONL) ─────────────


def test_real_shape_row_pre_bootstrap_dispatch_event() -> None:
    """A real pre-BOOTSTRAP ``event="dispatch"`` row (Slice 00) projects
    to ``event="decision"`` via the catch-all.

    Verbatim shape from ``implementation-decisions.jsonl`` row 2.
    """

    body = json.dumps(
        {
            "timestamp": "2026-05-20T05:32:42Z",
            "slice": "00-evidence-fixtures-and-compatibility-inventory",
            "event": "dispatch",
            "summary": (
                "Dispatched read-only discovery subagents for Slice 00 "
                "fixture/test patterns and compatibility consumer inventory."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "decision"
    assert anchor.slice_id == "00-evidence-fixtures-and-compatibility-inventory"
    assert anchor.accepted is False


def test_real_shape_row_pre_bootstrap_acceptance_event() -> None:
    """A real pre-BOOTSTRAP ``event="acceptance"`` row projects to
    ``event="accepted"`` via the rename table + the ACCEPTED-in-summary
    heuristic.

    Verbatim shape from row 19 (Slice 00 acceptance).
    """

    body = json.dumps(
        {
            "timestamp": "2026-05-21T18:11:32Z",
            "slice": "00-evidence-fixtures-and-compatibility-inventory",
            "event": "acceptance",
            "summary": (
                "Accepted Slice 00 after targeted tests and independent "
                "reviewer re-runs reported no open P1/P2 findings."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "accepted"
    assert anchor.accepted is True


def test_real_shape_row_pre_bootstrap_test_event() -> None:
    """A real pre-BOOTSTRAP ``event="test"`` row projects to
    ``event="test_result"`` via the rename table.

    Verbatim shape from row 9 (Slice 00 targeted test run).
    """

    body = json.dumps(
        {
            "timestamp": "2026-05-20T05:55:00Z",
            "slice": "00-evidence-fixtures-and-compatibility-inventory",
            "event": "test",
            "summary": (
                "Slice 00 targeted replay and compatibility-index tests "
                "passed in the main workspace."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "test_result"
    assert anchor.accepted is False


def test_real_shape_row_governance_triad_stage_implementer_before() -> None:
    """A real governance-era row with ``stage="implementer_before"`` maps
    to ``event="starting"`` per the stage-suffix rule.

    Verbatim shape from row 1126 (Slice 13b implementer BEFORE entry).
    """

    body = json.dumps(
        {
            "timestamp": "2026-05-24T05:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13b",
            "stage": "implementer_before",
            "event": "implementer_before",
            "summary": (
                "Slice 13b implementer BEFORE entry. Scope: "
                "GovernanceEvidenceIngestor ABC + ..."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    # Stage-suffix mapping wins over event-field mapping.
    assert anchor.event == "starting"
    assert anchor.accepted is False
    # sub_slice takes priority over slice per the STATUS.md chunk rule.
    assert anchor.slice_id == "13b"


def test_real_shape_row_governance_triad_stage_finalizer_after() -> None:
    """A real governance-era row with ``stage="finalizer_after"`` maps to
    ``event="complete"`` per the stage-suffix rule.

    Verbatim shape from row 1129 (Slice 13b finalizer AFTER entry).
    However the summary contains ``ACCEPTED``, so the ACCEPTED-in-summary
    heuristic upgrades the projection to ``event="accepted"``.
    """

    body = json.dumps(
        {
            "timestamp": "2026-05-24T08:37:31Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13b",
            "stage": "finalizer_after",
            "event": "finalizer_after",
            "summary": (
                "Slice 13b finalizer COMPLETE. All reviewer-mandated "
                "remediations applied; gates GREEN; sub-slice 13b "
                "ACCEPTED. ..."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    # Stage-suffix maps to "complete", but ACCEPTED-in-summary upgrades
    # to "accepted" per the documented precedence (step 7 of the
    # mapping table; only fires when steps 1-6 did not yield
    # "accepted").
    assert anchor.event == "accepted"
    assert anchor.accepted is True
    assert anchor.slice_id == "13b"


# ── stage→event mapping coverage ───────────────────────────────────────────


def test_stage_suffix_before_maps_to_starting() -> None:
    """``stage="implementer_before"`` / ``"finalizer_before"`` /
    ``"reviewer_before"`` all map to ``event="starting"`` via the
    stage-suffix rule.
    """

    body = (
        '{"slice": "13a", "stage": "implementer_before", "summary": "x"}\n'
        '{"slice": "13a", "stage": "reviewer_before", "summary": "x"}\n'
        '{"slice": "13a", "stage": "finalizer_before", "summary": "x"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 3
    for anchor in anchors:
        assert anchor.event == "starting"
        assert anchor.accepted is False


def test_stage_suffix_after_maps_to_complete() -> None:
    """``stage="implementer_after"`` / ``"finalizer_after"`` /
    ``"reviewer_after"`` all map to ``event="complete"`` via the
    stage-suffix rule. (When summary lacks ``ACCEPTED`` the projection
    stays ``"complete"``.)
    """

    body = (
        '{"slice": "13a", "stage": "implementer_after", "summary": "x"}\n'
        '{"slice": "13a", "stage": "reviewer_after", "summary": "x"}\n'
        '{"slice": "13a", "stage": "finalizer_after", "summary": "x"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 3
    for anchor in anchors:
        assert anchor.event == "complete"
        assert anchor.accepted is False


def test_summary_accepted_upgrades_event_to_accepted() -> None:
    """A row whose summary carries an uppercase ``ACCEPTED`` token is
    upgraded to ``event="accepted"`` even when its source event field
    would map to something else.

    The ACCEPTED-in-summary heuristic fires ONLY when the stage / event
    mapping did not already yield ``"accepted"`` -- step 7 of the
    documented precedence.
    """

    body = (
        '{"slice": "13a", "event": "decision", '
        '"summary": "Slice 13a ACCEPTED with the remediations."}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "accepted"
    assert anchor.accepted is True


def test_lowercase_accepted_in_summary_does_not_upgrade() -> None:
    """A lowercase "accepted" in the summary does NOT trigger the
    upgrade -- the heuristic is case-sensitive on the keyword.

    Mirrors the 13c parser's case-sensitivity discipline
    (``_RESOLVED_MARKER_RE``); prose mentions of "accepted" should NOT
    flip the projection.
    """

    body = (
        '{"slice": "13a", "event": "dispatch", '
        '"summary": "we discussed how the change might be accepted later"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    # event="dispatch" -> "decision" (catch-all); ACCEPTED-in-summary
    # does NOT fire on lowercase "accepted".
    assert anchor.event == "decision"
    assert anchor.accepted is False


def test_accepted_keyword_in_descriptive_prose_upgrades_per_documented_heuristic() -> None:
    """A row whose ``summary`` contains the literal token ``ACCEPTED`` in
    *descriptive prose* (NOT an acceptance row) is still upgraded to
    ``event="accepted"`` by the documented heuristic.

    Pins the over-match-by-design behavior documented in the parser
    docstring at ``decision_log_parser.py:89-115`` and in the inline
    comment at the mapping-step at ``decision_log_parser.py:308-336``.
    The heuristic searches for an uppercase ``ACCEPTED`` token bounded
    by word boundaries anywhere in the summary; it does not anchor to
    start-of-summary or to a ``Slice X ACCEPTED`` shape. Per the
    docstring this is a documented design choice deferred to 13e or
    Slice 15 when a real downstream consumer (metrics / finding
    engine) needs a tighter mapping.

    Companion test:
    :func:`test_accepted_keyword_in_real_acceptance_row_upgrades_per_documented_heuristic`
    demonstrates the same heuristic catches a real acceptance row
    (``"Slice 13d ACCEPTED"``). Both tests must pass; the heuristic
    intentionally does not distinguish the two cases.

    TODO(slice-13e or 15): if the consumer slice needs a tighter
    heuristic (anchor to start-of-summary or to ``Slice X ACCEPTED``
    shape), update the parser AND this test together so the
    behavior-change is deliberate.
    """

    # The summary contains ACCEPTED in descriptive (non-acceptance) prose.
    # The implementer's own AFTER row in the journal contains this exact
    # shape ("...documents ACCEPTED-in-summary upgrade discipline...");
    # the row below is a synthetic minimum repro.
    body = (
        '{"slice": "13d", "event": "decision", '
        '"summary": "discussing ACCEPTED versus REJECTED options"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    # Documented behavior: the heuristic over-matches descriptive prose
    # by design; the row is upgraded to event="accepted" even though
    # the prose mentions ACCEPTED in a non-acceptance context.
    assert anchor.event == "accepted"
    assert anchor.accepted is True


def test_accepted_keyword_in_real_acceptance_row_upgrades_per_documented_heuristic() -> None:
    """A real acceptance row whose ``summary`` carries the canonical
    ``Slice <id> ACCEPTED`` shape is upgraded to ``event="accepted"``
    by the same heuristic.

    Companion to
    :func:`test_accepted_keyword_in_descriptive_prose_upgrades_per_documented_heuristic`:
    both rows hit the SAME code path; the heuristic catches the real
    case alongside the descriptive-prose case. The pinning pair
    demonstrates the over-match is by design (catches real cases) AND
    pins the over-match (also catches descriptive prose).
    """

    body = (
        '{"slice": "13d", "event": "decision", '
        '"summary": "Slice 13d ACCEPTED"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "accepted"
    assert anchor.accepted is True


def test_stage_substring_finding_maps_to_finding() -> None:
    """A row whose ``stage`` *contains* the substring ``"finding"`` (and
    does NOT end in ``_before`` / ``_after``) maps to ``event="finding"``
    per step 3 of the documented stage→event precedence.

    Exercises the ``"finding" in stage_s`` branch at
    ``decision_log_parser.py:280-288`` that is otherwise reached only
    indirectly. The synthetic row uses
    ``stage="reviewer_finding_logged"`` -- not a real journal row
    (the real JSONL today has no ``finding``-substring stage), but the
    branch is future-proofing the prompt names explicitly.
    """

    body = (
        '{"slice": "13a", "stage": "reviewer_finding_logged", '
        '"summary": "reviewer logged a P3 finding"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    # Stage-substring "finding" maps to event="finding"; the row's
    # accepted=False (finding events are not acceptance signals).
    assert anchor.event == "finding"
    assert anchor.accepted is False


def test_unrecognised_event_value_maps_to_decision_catch_all() -> None:
    """Any event value not in the pass-through / rename sets maps to
    ``"decision"`` (the documented catch-all class).

    Real-data values that take this path: ``dispatch`` / ``patch`` /
    ``review`` / ``resume`` / ``decision`` / ``blocker`` /
    ``change_control`` / ``remediation_start`` / ``remediation_complete``
    / ``implementation_complete`` / ``governance_phase_bootstrap`` /
    triad event values (``implementer_before`` / etc.) when there is
    no stage field.
    """

    values_to_test = [
        "dispatch", "patch", "review", "resume",
        "decision", "blocker", "change_control",
        "remediation_start", "remediation_complete",
        "implementation_complete", "governance_phase_bootstrap",
    ]
    rows = [
        json.dumps({"slice": "00", "event": v, "summary": "x"})
        for v in values_to_test
    ]
    body = "\n".join(rows) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == len(values_to_test)
    for anchor in anchors:
        assert anchor.event == "decision"
        assert anchor.accepted is False


def test_pass_through_event_values_preserved_verbatim() -> None:
    """The 5 pass-through event values (``starting`` / ``complete`` /
    ``accepted`` / ``finding`` / ``test_result``) survive verbatim
    through the mapping.
    """

    pass_through = [
        ("starting", False),
        ("complete", False),
        ("accepted", True),
        ("finding", False),
        ("test_result", False),
    ]
    rows = [
        json.dumps({"slice": "00", "event": v, "summary": "x"})
        for v, _accepted in pass_through
    ]
    body = "\n".join(rows) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == len(pass_through)
    for anchor, (expected_event, expected_accepted) in zip(
        anchors, pass_through, strict=True
    ):
        assert anchor.event == expected_event
        assert anchor.accepted is expected_accepted


# ── per-finding-owning-slice attribution (mirrors 13c P1-13c-R1) ───────────


def test_cross_slice_finding_emits_separate_anchor_with_owning_slice() -> None:
    """A row whose ``slice=13d`` but ``summary`` mentions ``P3-13a-2``
    emits a separate ``event="finding"`` anchor with ``slice_id=13a``
    (mirrors 13c P1-13c-R1 discipline).

    Per the user prompt: "a row whose slice=13d but summary mentions
    P3-13a-2 -> emit a separate finding anchor with slice_id=13a,
    mirroring 13c's discipline". The main row anchor still emits with
    the row's slice_id; cross-slice findings emit their own anchor
    under the finding's owning slice.

    Cites: doc-13:46-47 (drift/findings as quality signals);
    doc-13a:24, 109-118 (no silent loss).
    """

    body = json.dumps(
        {
            "slice": "13d",
            "event": "decision",
            "summary": (
                "Carries P3-13a-2 docstring count nit from 13a "
                "and own P1-13d-1 finding."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)

    # Two anchors: the main 13d anchor + the cross-slice 13a finding anchor.
    assert len(anchors) == 2

    # Main row anchor.
    main = next(a for a in anchors if a.event == "decision")
    assert main.slice_id == "13d"
    # Same-slice finding (P1-13d-1) populates open_findings.
    assert main.open_findings == ["P1-13d-1"]
    assert main.decision_log_line == 1

    # Cross-slice finding anchor.
    cross = next(a for a in anchors if a.event == "finding")
    assert cross.slice_id == "13a"  # finding's OWN owning slice
    assert cross.open_findings == ["P3-13a-2"]
    assert cross.decision_log_line == 1
    assert cross.accepted is False


def test_same_slice_finding_populates_main_open_findings() -> None:
    """When the finding's owning slice MATCHES the row's slice_id, the
    finding is added to the main anchor's ``open_findings`` (no separate
    anchor emitted).

    The 13c P1-13c-R1 discipline only emits cross-slice anchors; the
    same-slice path is the simpler default.
    """

    body = json.dumps(
        {
            "slice": "13a",
            "event": "decision",
            "summary": "P3-13a-2 and P2-13a-1 are open findings.",
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.slice_id == "13a"
    assert set(anchor.open_findings) == {"P3-13a-2", "P2-13a-1"}


def test_duplicate_finding_id_in_row_summary_dedupes_to_one_entry() -> None:
    """The parser dedupes finding ids before constructing the anchor so
    the 13a ``_open_findings_dedup_and_non_empty`` validator does not
    reject.

    Real-data symptom: 8 of 1133 rows contain duplicate finding-id
    mentions in the summary (e.g. ``P2-10b-1`` mentioned twice in the
    same row). The parser must dedup BEFORE construction.
    """

    body = json.dumps(
        {
            "slice": "10b",
            "event": "decision",
            "summary": (
                "P2-10b-1 flagged by reviewer; "
                "the same P2-10b-1 will be remediated."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.open_findings == ["P2-10b-1"]


def test_multiple_cross_slice_findings_group_by_owning_slice() -> None:
    """Multiple cross-slice findings with the SAME owning slice group
    into ONE anchor; findings with DIFFERENT owning slices emit one
    anchor each.

    Real-journal pattern: a row whose summary lists multiple carried
    P3s from different slices.
    """

    body = json.dumps(
        {
            "slice": "13b",
            "event": "decision",
            "summary": (
                "Carried: P3-13a-2, P3-13a-3 (both from 13a); "
                "P3-12c-1 (from 12c)."
            ),
        }
    ) + "\n"
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)

    # Main 13b anchor + 13a cross-slice anchor + 12c cross-slice anchor = 3.
    assert len(anchors) == 3
    main = next(a for a in anchors if a.event == "decision")
    assert main.slice_id == "13b"
    assert main.open_findings == []

    finding_anchors = [a for a in anchors if a.event == "finding"]
    assert len(finding_anchors) == 2
    by_slice = {a.slice_id: a for a in finding_anchors}
    assert set(by_slice) == {"13a", "12c"}
    # Both 13a findings group into a single 13a anchor.
    assert set(by_slice["13a"].open_findings) == {"P3-13a-2", "P3-13a-3"}
    assert by_slice["12c"].open_findings == ["P3-12c-1"]


# ── fail-closed semantics (doc-13:184 verbatim) ────────────────────────────


def test_malformed_json_line_raises_value_error_with_line_number() -> None:
    """A non-empty line that does not parse as JSON raises typed
    :class:`ValueError` with the 1-indexed line number.

    Doc-13:184 verbatim: "rejects malformed rows and records line
    numbers as evidence anchors". The line number must be in the error
    message so a downstream consumer can pinpoint the malformed row
    without re-scanning the file.
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "ok row 1"}\n'
        'not a json object on row 2\n'
        '{"slice": "01", "event": "patch", "summary": "ok row 3"}\n'
    )
    with pytest.raises(ValueError) as exc:
        parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    # Error message must carry the 1-indexed line number (row 2).
    assert "line 2" in str(exc.value)
    # Error message must signal the doc-13:184 contract.
    assert "malformed" in str(exc.value).lower() or "not valid json" in str(exc.value).lower()


def test_non_object_json_line_raises_value_error_with_line_number() -> None:
    """A line that parses to a non-object JSON value (bare list /
    string / number) raises typed :class:`ValueError` with the line
    number + the offending JSON type.

    Every JSONL row in this contract must be a JSON OBJECT; bare
    primitives or lists are malformed per the doc-13:184 contract.
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "ok"}\n'
        '[1, 2, 3]\n'
    )
    with pytest.raises(ValueError) as exc:
        parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert "line 2" in str(exc.value)
    assert "list" in str(exc.value) or "non-object" in str(exc.value)


def test_row_without_slice_or_sub_slice_raises_value_error() -> None:
    """A row that lacks both ``sub_slice`` and ``slice`` raises typed
    :class:`ValueError` with the 1-indexed line number.

    The 13a ID-field validator ``_non_empty_anchor_id_fields`` on
    :class:`ImplementationArtifactAnchor` would reject an empty
    ``slice_id``; the parser raises EARLIER with a clearer message
    that names the missing fields.
    """

    body = '{"event": "dispatch", "summary": "no slice fields"}\n'
    with pytest.raises(ValueError) as exc:
        parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert "line 1" in str(exc.value)


def test_blank_lines_are_graceful_skip_not_an_error() -> None:
    """Blank / whitespace-only lines are graceful-skip; they do not
    raise.

    The JSONL format intentionally allows blank trailing newlines and
    visual separation. Rejecting them would be over-strict for
    legitimate files (the real
    ``implementation-decisions.jsonl`` does not have blank lines, but
    test fixtures and future appenders might).
    """

    body = (
        '\n'
        '\n'
        '{"slice": "00", "event": "dispatch", "summary": "row 3"}\n'
        '\n'
        '   \n'  # whitespace-only line
        '{"slice": "01", "event": "patch", "summary": "row 6"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert len(anchors) == 2
    # Line numbers reflect the SOURCE line, not the index after
    # filtering blanks: row 3 is on line 3, row 6 is on line 6.
    assert [a.decision_log_line for a in anchors] == [3, 6]


# ── edge cases ─────────────────────────────────────────────────────────────


def test_empty_body_returns_empty_list() -> None:
    """An empty body produces an empty anchor list -- not an error.

    Per the fail-closed discipline an empty list is the legitimate
    result; the parser does not raise on empty input.
    """

    anchors = parse_implementation_decision_log(
        "/tmp/empty.jsonl", body=""
    )
    assert anchors == []


def test_only_blank_lines_body_returns_empty_list() -> None:
    """A body with ONLY blank / whitespace lines produces an empty
    anchor list (every line is graceful-skip).
    """

    body = "\n\n   \n\n\t\n"
    anchors = parse_implementation_decision_log(
        "/tmp/blanks.jsonl", body=body
    )
    assert anchors == []


def test_path_argument_is_recorded_verbatim_via_str_coercion() -> None:
    """``Path`` inputs are coerced to ``str`` for the
    ``journal_path`` field per doc-13:147 (the stable cross-process
    freshness anchor).

    The parser exposes a unified ``str`` shape downstream so consumers
    can compare paths textually without caring whether the caller
    passed a string or a :class:`pathlib.Path`. The 13a model field
    is named ``journal_path`` but is FROZEN to also store the JSONL
    path for 13d-emitted anchors (the bidirectional ``13c⊕13d``
    invariant keeps them distinguishable by which line field is
    populated).
    """

    body = '{"slice": "00", "event": "dispatch", "summary": "x"}\n'
    path_obj = Path("docs/execution-control-plane/implementation-decisions.jsonl")
    anchors = parse_implementation_decision_log(path_obj, body=body)
    assert len(anchors) == 1
    assert anchors[0].journal_path == str(path_obj)


# ── 13c⊕13d bidirectional invariant ────────────────────────────────────────


def test_every_emitted_anchor_has_line_start_none_and_decision_log_line_set() -> None:
    """Every 13d-emitted anchor carries ``line_start=None`` and
    ``decision_log_line=<row#>``.

    This is the bidirectional ``13c⊕13d`` invariant documented in the
    13d parser module docstring: 13c-emitted anchors have
    ``decision_log_line=None``; 13d-emitted anchors have
    ``line_start=None``. The two anchor types are distinguishable at
    the typed-surface level by which line field is populated.

    The 13a ``_line_positive_when_present`` validator rejects
    ``line_start < 1``; ``line_start=None`` is the only fail-closed
    choice the typed shape accepts.
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "row 1"}\n'
        '{"slice": "01", "event": "acceptance", "summary": "ACCEPTED row 2"}\n'
        '{"slice": "02", "stage": "finalizer_after", "summary": "ACCEPTED row 3"}\n'
        '{"slice": "03", "event": "decision", "summary": "P3-04-1 cross-slice"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert anchors, "fixture should produce at least one anchor"
    for anchor in anchors:
        assert anchor.line_start is None, (
            f"13d anchor must have line_start=None; got "
            f"line_start={anchor.line_start!r} on {anchor!r}"
        )
        assert anchor.decision_log_line is not None
        assert anchor.decision_log_line >= 1


# ── round-trip through 13a model validators ────────────────────────────────


def test_every_emitted_anchor_round_trips_through_13a_model_validators() -> None:
    """Every emitted :class:`ImplementationArtifactAnchor` survives a
    ``model_dump_json`` -> ``model_validate_json`` round trip cleanly.

    This is the structural proof that the parser cannot silently
    construct a row the 13a typed shape would reject -- if any field
    violated the ID-field validators (``_non_empty_anchor_id_fields``
    / ``_line_positive_when_present`` /
    ``_open_findings_dedup_and_non_empty``) the round-trip would
    raise.
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "P3-00-1 own"}\n'
        '{"slice": "01", "event": "test", "summary": "tests passed"}\n'
        '{"slice": "13b", "sub_slice": "13b", "stage": "finalizer_after", '
        '"event": "finalizer_after", "summary": "Slice 13b ACCEPTED; '
        'carries P3-13a-2 from 13a."}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    assert anchors, "fixture should produce at least one anchor"
    for anchor in anchors:
        serialised = anchor.model_dump_json()
        restored = ImplementationArtifactAnchor.model_validate_json(
            serialised
        )
        assert restored == anchor


# ── source ordering + line numbering ───────────────────────────────────────


def test_multi_row_source_order_preserved() -> None:
    """Multiple rows in source order produce anchors in source order
    (1-indexed ``decision_log_line`` matches the source row number).

    The parser docstring promises stable source-ordering so downstream
    metrics consumers see a deterministic anchor sequence.
    """

    body = (
        '{"slice": "00", "event": "dispatch", "summary": "row 1"}\n'
        '{"slice": "01", "event": "patch", "summary": "row 2"}\n'
        '{"slice": "02", "event": "review", "summary": "row 3"}\n'
        '{"slice": "03", "event": "test", "summary": "row 4"}\n'
    )
    anchors = parse_implementation_decision_log("/tmp/r.jsonl", body=body)
    # Main anchors only (no cross-slice findings in this fixture).
    main_anchors = [a for a in anchors if a.event != "finding"]
    assert [a.decision_log_line for a in main_anchors] == [1, 2, 3, 4]
    assert [a.slice_id for a in main_anchors] == ["00", "01", "02", "03"]


# ── tmp_path file read (production path) ───────────────────────────────────


def test_parser_reads_real_file_when_body_omitted(tmp_path: Path) -> None:
    """When ``body=`` is omitted the parser reads from ``path`` via
    ``Path.read_text(encoding="utf-8")``.

    This is the production caller path; the ingestor in a later
    sub-slice will pass a real path and expect the parser to do the
    file read itself.
    """

    jsonl_file = tmp_path / "implementation-decisions.jsonl"
    jsonl_file.write_text(
        '{"slice": "00", "event": "dispatch", "summary": "test file row"}\n',
        encoding="utf-8",
    )
    anchors = parse_implementation_decision_log(jsonl_file)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.slice_id == "00"
    assert anchor.event == "decision"
    assert anchor.journal_path == str(jsonl_file)


def test_body_argument_does_not_touch_filesystem() -> None:
    """When ``body=`` is supplied the parser does NOT read from ``path``
    -- the path is recorded into ``journal_path`` but the filesystem is
    untouched.

    This is the test-only escape hatch the parser docstring promises;
    the production caller always omits ``body=`` and the parser reads
    from disk in that path.
    """

    nonexistent_path = "/nonexistent/path/to/implementation-decisions.jsonl"
    body = '{"slice": "00", "event": "dispatch", "summary": "x"}\n'
    anchors = parse_implementation_decision_log(nonexistent_path, body=body)
    assert len(anchors) == 1
    assert anchors[0].journal_path == nonexistent_path
    assert anchors[0].event == "decision"


# ── real JSONL smoke test (~1133+ rows) ────────────────────────────────────


def test_real_jsonl_parses_cleanly_with_anchor_count_matching_rows() -> None:
    """Smoke test: the actual
    ``docs/execution-control-plane/implementation-decisions.jsonl``
    parses cleanly (no ValueError) and produces an anchor count that
    is >= the JSONL row count (each row produces one main anchor plus
    zero-or-more cross-slice finding anchors).

    Per the prompt this is the real-shape smoke test. Anchor count
    should approximately match the JSONL row count (each row produces
    one anchor) plus extras for cross-slice findings.
    """

    # The real JSONL is the production fixture; the parser is invoked
    # with the production path so the journal_path is recorded
    # verbatim.
    anchors = parse_implementation_decision_log(_REAL_JSONL_PATH)

    # Count the source rows by reading the file ourselves.
    raw_lines = _REAL_JSONL_PATH.read_text(encoding="utf-8").split("\n")
    non_blank_rows = sum(1 for line in raw_lines if line.strip())

    # Every non-blank row produces at least one anchor at that row's line.
    # Some primary rows legitimately project to event="finding" when their
    # stage contains the parser-reserved finding substring, so row coverage is
    # pinned by decision_log_line rather than by filtering event names.
    anchor_lines = {a.decision_log_line for a in anchors}
    assert anchor_lines == set(range(1, non_blank_rows + 1))

    # Total anchor count is >= row count (the finding anchors are
    # additional).
    assert len(anchors) >= non_blank_rows

    # Event distribution must use ONLY the 13d 7-value taxonomy
    # (starting / complete / accepted / finding / test_result /
    # subagent / decision). The 13d parser never emits "subagent" --
    # that lives in the markdown journal only.
    allowed_events = {
        "starting", "complete", "accepted",
        "finding", "test_result", "decision",
    }
    seen_events = {a.event for a in anchors}
    assert seen_events <= allowed_events, (
        f"unexpected event tag(s) {seen_events - allowed_events!r} "
        f"in real-JSONL parser output"
    )

    # Every emitted anchor carries the 13d bidirectional invariant.
    for anchor in anchors:
        assert anchor.line_start is None
        assert anchor.decision_log_line is not None
        assert 1 <= anchor.decision_log_line <= len(raw_lines)
        assert anchor.journal_path == str(_REAL_JSONL_PATH)


# ── existing-13c invariant: 13d does NOT extend the 13a model ──────────────


def test_13d_does_not_touch_13a_model_or_13c_parser() -> None:
    """The 13d parser composes the 13a
    :class:`ImplementationArtifactAnchor` model verbatim without
    extending it; the 13c
    :func:`parse_implementation_journal` continues to emit anchors
    with ``decision_log_line=None``.

    Per STATUS.md § "Next safe action" Chunk-shape point 6: the 13d
    parser must NOT edit ``governance/models.py``,
    ``governance/ingestor.py``, or ``governance/journal_parser.py``
    source. This test asserts the contract preservation by importing
    the 13c parser and confirming an anchor it emits still has
    ``decision_log_line=None``.
    """

    # Round-trip a synthetic 13c anchor: it has line_start set and
    # decision_log_line=None (the previously-always-None field that
    # 13d fills).
    from iriai_build_v2.workflows.develop.governance import (
        parse_implementation_journal,
    )

    journal_body = (
        "## 2026-05-24 — Slice 13d STARTING — implementer BEFORE\n"
    )
    journal_anchors = parse_implementation_journal(
        "implementation-journal.md", body=journal_body
    )
    assert len(journal_anchors) == 1
    journal_anchor = journal_anchors[0]
    # 13c anchors have line_start populated + decision_log_line=None.
    assert journal_anchor.line_start == 1
    assert journal_anchor.decision_log_line is None

    # And the 13d anchor has the inverse shape.
    log_body = '{"slice": "13d", "event": "dispatch", "summary": "x"}\n'
    log_anchors = parse_implementation_decision_log(
        "implementation-decisions.jsonl", body=log_body
    )
    assert len(log_anchors) == 1
    log_anchor = log_anchors[0]
    assert log_anchor.line_start is None
    assert log_anchor.decision_log_line == 1
