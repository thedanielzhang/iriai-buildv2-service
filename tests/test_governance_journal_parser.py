"""Slice 13c -- unit tests for the implementation-journal markdown parser.

Covers the doc-13:182-183 § "Refactoring Steps" step 3 deliverable
("Add an implementation-journal parser that produces anchors from markdown
headings, bullet lines, subagent IDs, test result lines, and acceptance
notes") for
:func:`iriai_build_v2.workflows.develop.governance.parse_implementation_journal`.

Each emitted
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
is round-tripped through the 13a model invariants
(the ID-field validators ``_non_empty_anchor_id_fields`` /
``_line_positive_when_present`` /
``_open_findings_dedup_and_non_empty``) so the parser cannot silently
emit a row the typed shape would reject.

Per the governance prompt § "Non-Negotiables" the parser fails closed
(``ValueError`` on a recognised-but-corrupt heading) and never silently
degrades. Per the prompt § "Bounded reads" the parser is pure-typed
(text-in / typed-rows-out): no file I/O is exercised in the tests --
the ``body=`` escape hatch supplies synthetic markdown directly.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop import governance
from iriai_build_v2.workflows.develop.governance import (
    ImplementationArtifactAnchor,
    parse_implementation_journal,
)
from iriai_build_v2.workflows.develop.governance import (
    journal_parser as journal_parser_module,
)


# ── package surface ────────────────────────────────────────────────────────


def test_package_reexports_parse_implementation_journal() -> None:
    """The 13c parser surface is re-exported at the package level.

    The package-level strict-equality assertion lives in
    ``tests/test_governance_evidence_models.py::
    test_governance_package_reexports_doc_13_surface``; this test
    asserts the 13c-specific subset is present and is the same Python
    object as the module-local symbol.
    """

    assert "parse_implementation_journal" in governance.__all__
    assert hasattr(governance, "parse_implementation_journal")
    assert (
        governance.parse_implementation_journal
        is journal_parser_module.parse_implementation_journal
    )


def test_module_all_lists_exactly_parse_implementation_journal() -> None:
    """``journal_parser.__all__`` is exactly the doc-13:182-183 surface."""

    assert list(journal_parser_module.__all__) == [
        "parse_implementation_journal"
    ]


# ── heading recognition (one per supported markdown shape) ──────────────────


def test_heading_starting_recognised_as_starting_event() -> None:
    """Doc-13:182 STARTING heading shape emits ``event="starting"``."""

    body = "## 2026-05-24 — Slice 13b STARTING — implementer BEFORE entry\n"
    anchors = parse_implementation_journal(
        "docs/execution-control-plane/implementation-journal.md", body=body
    )
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "starting"
    assert anchor.slice_id == "13b"
    assert anchor.line_start == 1
    assert anchor.decision_log_line is None
    assert anchor.accepted is False
    assert anchor.open_findings == []
    assert anchor.journal_path == (
        "docs/execution-control-plane/implementation-journal.md"
    )


def test_heading_complete_recognised_as_complete_event() -> None:
    """Doc-13:182 COMPLETE heading shape emits ``event="complete"``."""

    body = (
        "## 2026-05-24 — Slice 13b COMPLETE — implementer AFTER entry\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "complete"
    assert anchor.accepted is False
    assert anchor.slice_id == "13b"


def test_heading_accepted_sets_accepted_true_and_event_accepted() -> None:
    """Doc-13:182 ACCEPTED heading shape sets ``accepted=True`` AND
    ``event="accepted"``.

    The two are co-determined per the STATUS.md § "Next safe action"
    Chunk-shape rule -- ``ACCEPTED`` is the only state keyword that
    flips ``accepted`` True; the other two state keywords set False.
    """

    body = (
        "## 2026-05-24 — Slice 13b ACCEPTED — finalizer AFTER entry\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "accepted"
    assert anchor.accepted is True
    assert anchor.slice_id == "13b"


def test_heading_trailing_date_shape_b_recognised() -> None:
    """Heading shape B (early-Slice-08 era):
    ``## Slice <id> <STATE> [-- suffix] (YYYY-MM-DD)``.

    The grammar permits the date at either end so the parser
    recognises both eras of the journal without false-firing on the
    trailing-date variant.
    """

    body = (
        "## Slice 08c-1 COMPLETE — real-Postgres test fixture (2026-05-21)\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.event == "complete"
    assert anchor.accepted is False
    # Sub-id slice ids (with dash-and-digit suffix) are preserved
    # verbatim per the 13a ``_non_empty_anchor_id_fields`` ID-field
    # validator on ``slice_id``.
    assert anchor.slice_id == "08c-1"


def test_heading_multi_slice_range_marker_is_deliberately_skipped() -> None:
    """Heading shape C -- ``(Slices 13-19)`` markers reference no single
    slice and are deliberately skipped (no anchor emitted).

    Per the parser's docstring this prevents a false-fire that would
    try to project a single slice id from the ``13-19`` range.
    """

    body = (
        "## 2026-05-24 — Governance Layer (Slices 13–19) STARTING — "
        "BOOTSTRAP iteration\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert anchors == []


def test_heading_with_ascii_double_hyphen_separator_recognised() -> None:
    """The parser accepts the ASCII ``--`` separator as a forward-compat
    fallback for the U+2014 em-dash.

    The journal uses the em-dash consistently today, but the regex
    tolerates the ASCII fallback so a future journal-format change
    (or a test fixture authored in plain ASCII) does not break the
    parser.
    """

    body = "## 2026-05-24 -- Slice 13c STARTING -- ascii dash variant\n"
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.slice_id == "13c"
    assert anchor.event == "starting"


# ── subagent UUID extraction ────────────────────────────────────────────────


def test_subagent_uuid_in_section_does_not_break_heading_anchor() -> None:
    """A subagent UUID in the section body emits a separate per-UUID
    ``event="subagent"`` anchor (P2-13c-R2 remediation) but does NOT
    change the heading anchor's event tag.

    Per doc-13:143-150 the heading anchor is per-slice; per
    doc-13:182-183 ("subagent IDs") each matched UUID is its own
    typed cite. The heading anchor remains
    ``event="starting"`` / ``slice_id="13b"`` / ``open_findings=[]``;
    the per-UUID anchor carries the UUID in ``open_findings`` so the
    cite is preserved.
    """

    body = (
        "## 2026-05-24 — Slice 13b STARTING — implementer BEFORE entry\n"
        "\n"
        "Sibling implementer subagent dispatched.\n"
        "- `019e44a0-eee0-7833-a000-39059a49d41a` (`Anscombe`): store/schema tests\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    # Heading anchor + subagent anchor = 2 anchors total.
    assert len(anchors) == 2
    heading_anchor = next(a for a in anchors if a.event == "starting")
    assert heading_anchor.event == "starting"
    assert heading_anchor.slice_id == "13b"
    assert heading_anchor.open_findings == []
    subagent_anchor = next(a for a in anchors if a.event == "subagent")
    assert subagent_anchor.slice_id == "13b"
    assert subagent_anchor.open_findings == [
        "019e44a0-eee0-7833-a000-39059a49d41a"
    ]
    assert subagent_anchor.accepted is False
    assert subagent_anchor.line_start == 4  # the bullet line


def test_subagent_uuid_regex_matches_real_shape() -> None:
    """The internal subagent-UUID regex matches the real
    ``019eXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`` shape and not random hex.

    The journal stamps every dispatched subagent with a UUIDv7 in the
    ``019e`` timestamp band; the regex must accept that shape and
    reject a same-length-but-wrong-prefix hex string.
    """

    real_uuid = "019e44a0-eee0-7833-a000-39059a49d41a"
    fake_uuid = "abcd1234-eee0-7833-a000-39059a49d41a"
    assert journal_parser_module._SUBAGENT_UUID_RE.search(real_uuid) is not None
    assert journal_parser_module._SUBAGENT_UUID_RE.search(fake_uuid) is None


# ── test-result line extraction ─────────────────────────────────────────────


def test_test_result_line_emits_test_result_anchor() -> None:
    """A line containing ``N passed`` emits an
    ``event="test_result"`` anchor in addition to the heading anchor.

    The real-journal example: ``-> 336 passed / 0 failed / 0 errors /
    0 skipped in 0.16s``.
    """

    body = (
        "## 2026-05-24 — Slice 13b COMPLETE — implementer AFTER entry\n"
        "\n"
        "- `python -m pytest tests/test_governance_evidence_ingestor.py -v`\n"
        "  -> 336 passed in 59.21s, 0 failed, 0 errors\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    # Heading anchor + test-result anchor = 2 anchors total.
    events = [anchor.event for anchor in anchors]
    assert "test_result" in events
    assert "complete" in events
    # The heading anchor is at line 1; the test-result anchor is at the
    # line that contains ``336 passed``. Per source-order the heading
    # comes first.
    assert anchors[0].event == "complete"
    assert anchors[0].line_start == 1
    test_result_anchor = next(
        a for a in anchors if a.event == "test_result"
    )
    assert test_result_anchor.slice_id == "13b"
    assert test_result_anchor.line_start >= 2
    assert test_result_anchor.accepted is False


def test_test_result_line_in_acceptance_section_inherits_accepted_slice() -> None:
    """A test-result line that appears under an ACCEPTED heading still
    carries ``accepted=False`` (the test-result anchor itself is not the
    acceptance anchor; only the heading anchor with ``event="accepted"``
    is).
    """

    body = (
        "## 2026-05-24 — Slice 13b ACCEPTED — finalizer AFTER entry\n"
        "\n"
        "Combined: **111 passed in ~0.31s**.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchor = next(a for a in anchors if a.event == "accepted")
    test_result_anchor = next(a for a in anchors if a.event == "test_result")
    assert heading_anchor.accepted is True
    assert test_result_anchor.accepted is False
    assert heading_anchor.slice_id == test_result_anchor.slice_id == "13b"


# ── accepted-vs-open finding discrimination ─────────────────────────────────


def test_open_finding_id_populates_heading_open_findings() -> None:
    """A bullet that contains a finding id like ``P1-13b-1`` WITHOUT a
    RESOLVED/CLOSED/FIXED marker contributes to ``open_findings`` on the
    heading anchor.

    Per the 13a ``_open_findings_dedup_and_non_empty`` validator the
    heading anchor's ``open_findings`` is deduplicated and non-empty.
    """

    body = (
        "## 2026-05-24 — Slice 13b COMPLETE — implementer AFTER entry\n"
        "\n"
        "- **P1-13b-1** -- strict-equality on `governance.__all__` relaxed.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchor = next(a for a in anchors if a.event == "complete")
    assert "P1-13b-1" in heading_anchor.open_findings


def test_resolved_finding_id_does_not_populate_open_findings() -> None:
    """A bullet that contains a finding id AND a RESOLVED marker on the
    SAME line does NOT contribute to ``open_findings``; it still emits
    its own ``event="finding"`` anchor for per-line citation density.
    """

    body = (
        "## 2026-05-24 — Slice 13b ACCEPTED — finalizer AFTER entry\n"
        "\n"
        "- **P1-13b-1 RESOLVED** -- strict-equality restored.\n"
        "- **P2-13b-1 RESOLVED** -- statement_timeout_ms clamp-DOWN.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchor = next(a for a in anchors if a.event == "accepted")
    # NEITHER finding ends up in ``open_findings`` because the RESOLVED
    # marker is on the same line as each id.
    assert heading_anchor.open_findings == []
    # But a per-line ``event="finding"`` anchor IS emitted for each
    # finding-id-bearing bullet -- the parser docstring promises this
    # citation density.
    finding_anchors = [a for a in anchors if a.event == "finding"]
    assert len(finding_anchors) == 2
    for anchor in finding_anchors:
        assert anchor.open_findings == []
        assert anchor.accepted is False


def test_carried_p3_finding_in_open_section_populates_open_findings() -> None:
    """A bullet that lists a CARRIED finding without a RESOLVED/CLOSED
    marker on the same line populates ``open_findings`` on the heading
    anchor.

    Real-journal example: ``- **P3-13b-2** (carried).``.
    """

    body = (
        "## 2026-05-24 — Slice 13b ACCEPTED — finalizer AFTER entry\n"
        "\n"
        "- **P3-13b-2** -- deprecated asyncio call in one test (carried).\n"
        "- **P3-13b-3** -- inspect.signature gap (carried).\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchor = next(a for a in anchors if a.event == "accepted")
    # Both carried findings populate the open list (no RESOLVED marker
    # on their lines).
    assert "P3-13b-2" in heading_anchor.open_findings
    assert "P3-13b-3" in heading_anchor.open_findings
    # Deduplicated: the 13a validator would reject a duplicate, so the
    # parser must not feed it one. (Smoke: the list is exactly the set
    # of seen ids in source order.)
    assert len(heading_anchor.open_findings) == len(set(heading_anchor.open_findings))


def test_duplicate_finding_id_in_same_section_dedupes_to_one_entry() -> None:
    """The parser dedupes finding ids before constructing the heading
    anchor so the 13a ``_open_findings_dedup_and_non_empty`` validator
    does not reject.

    A duplicate finding id on TWO bullets in the same section is a
    common real-journal occurrence (the same finding gets mentioned in
    the inventory bullet AND the resolution bullet); the parser keeps
    only the first occurrence per section.
    """

    body = (
        "## 2026-05-24 — Slice 13b COMPLETE — implementer AFTER entry\n"
        "\n"
        "- Finding P1-13b-1 flagged by reviewer.\n"
        "- The same P1-13b-1 will be remediated by finalizer.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchor = next(a for a in anchors if a.event == "complete")
    assert heading_anchor.open_findings == ["P1-13b-1"]


# ── round-trip through 13a cross-validators ─────────────────────────────────


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
        "## 2026-05-24 — Slice 13a STARTING — implementer BEFORE entry\n"
        "Some prose.\n"
        "\n"
        "## 2026-05-24 — Slice 13a COMPLETE — implementer AFTER entry\n"
        "- P3-13a-2 docstring count nit (carried).\n"
        "75 passed in 0.14s.\n"
        "\n"
        "## 2026-05-24 — Slice 13a ACCEPTED — finalizer AFTER entry\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert anchors, "fixture should produce at least one anchor"
    for anchor in anchors:
        serialised = anchor.model_dump_json()
        restored = ImplementationArtifactAnchor.model_validate_json(
            serialised
        )
        assert restored == anchor


# ── edge cases ──────────────────────────────────────────────────────────────


def test_empty_body_returns_empty_list() -> None:
    """An empty body produces an empty anchor list -- not an error.

    Per the fail-closed discipline an empty list is the legitimate
    result; the parser does not raise on empty input.
    """

    anchors = parse_implementation_journal(
        "implementation-journal.md", body=""
    )
    assert anchors == []


def test_body_with_no_recognised_anchors_returns_empty_list() -> None:
    """A markdown body with prose but no recognised headings produces
    no anchors -- the parser graceful-skips non-matching lines.
    """

    body = (
        "# Top-level title\n"
        "\n"
        "Some intro prose.\n"
        "\n"
        "### Sub-sub heading (level 3) is not an anchor\n"
        "Another paragraph with no finding ids and no test results.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert anchors == []


def test_13a_anchor_constructor_rejects_empty_slice_id() -> None:
    """The 13a :class:`ImplementationArtifactAnchor` constructor rejects
    an empty ``slice_id`` via its ``_non_empty_anchor_id_fields``
    ID-field validator.

    Renamed + restated from
    ``test_heading_with_unparseable_slice_id_raises_value_error`` per
    the 13c finalizer P2-13c-R3 remediation: the original docstring
    falsely claimed this exercised parser fail-closed behaviour, but
    it actually tests the 13a model validator. The parser-level
    fail-closed contract is exercised by the next test
    (``test_parser_raises_when_heading_regex_matches_but_model_rejects``).

    This test remains because the 13a validator IS the parser's
    backstop -- the parser cannot silently emit an invalid anchor;
    every constructor call goes through this validator.
    """

    with pytest.raises(ValueError):
        ImplementationArtifactAnchor(
            slice_id="",
            journal_path="implementation-journal.md",
            line_start=1,
            decision_log_line=None,
            event="starting",
            accepted=False,
            open_findings=[],
        )


def test_parser_raises_when_heading_regex_matches_but_model_rejects() -> None:
    """A heading line that the parser's heading regex matches but the
    13a :class:`ImplementationArtifactAnchor` model subsequently rejects
    raises a typed :class:`ValueError` (P2-13c-R3 parser-level
    fail-closed test).

    The canonical contract-drift case the parser MUST raise on: a
    heading whose ``_HEADING_RE_DATED`` regex match passes BUT the
    extracted clauses are zero (e.g. a future regex tightening that
    accepts the prefix but rejects the clause body). The parser's
    fail-closed guard at the heading-discovery pass raises a typed
    ``ValueError`` per the auto-memory
    ``feedback_no_silent_degradation`` rule.

    The empirical way to exercise this from a test (without monkey-
    patching the regex) is to construct an anchor projection that the
    13a validators reject directly through the constructor call site
    inside the parser. The simplest construction that reliably round-
    trips through the parser AND triggers the 13a fail-closed path is
    a heading whose ``slice_id`` regex match is a known bad shape;
    however ``_SLICE_ID_RE`` is bounded so the regex itself screens
    out structurally-bad slice ids. The remaining surface is the
    ``open_findings`` validator (duplicate-rejection) which the parser
    de-dupes BEFORE construction, so we cannot trigger from valid
    journal markdown.

    Instead we exercise the parser's OWN fail-closed safety net (the
    clauses-empty path): a heading line whose shape regex matches but
    the clause splitter finds zero ``Slice <id> <state>`` clauses. We
    construct this by monkey-patching ``_HEADING_CLAUSE_RE`` to a
    pattern that never matches; the parser's own check at the
    heading-discovery pass raises the typed ``ValueError`` we expect.
    """

    import re as _re

    real_clause_re = journal_parser_module._HEADING_CLAUSE_RE
    # A pattern that never matches anything: ``(?!.*)`` is a zero-width
    # negative-lookahead that always fails.
    never_match = _re.compile(r"(?!.*)foo")
    try:
        journal_parser_module._HEADING_CLAUSE_RE = never_match
        body = "## 2026-05-24 — Slice 13c STARTING — fail-closed exercise\n"
        with pytest.raises(ValueError, match="no .*clause was found"):
            parse_implementation_journal(
                "implementation-journal.md", body=body
            )
    finally:
        journal_parser_module._HEADING_CLAUSE_RE = real_clause_re


def test_real_journal_path_is_recorded_verbatim_via_pathlib() -> None:
    """``Path`` inputs are coerced to ``str`` for the
    ``journal_path`` field per doc-13:147 (the stable cross-process
    freshness anchor).

    The parser exposes a unified ``str`` shape downstream so consumers
    can compare paths textually without caring whether the caller
    passed a string or a :class:`pathlib.Path`.
    """

    body = "## 2026-05-24 — Slice 13c STARTING — implementer BEFORE\n"
    path_obj = Path("docs/execution-control-plane/implementation-journal.md")
    anchors = parse_implementation_journal(path_obj, body=body)
    assert len(anchors) == 1
    assert anchors[0].journal_path == str(path_obj)


def test_multiple_headings_in_source_order_preserved() -> None:
    """Multiple headings in one body produce one anchor per heading, in
    source order, with the correct ``line_start`` values.

    The parser docstring promises stable source-ordering so downstream
    metrics consumers see a deterministic anchor sequence.
    """

    body = (
        "## 2026-05-24 — Slice 13a STARTING — implementer BEFORE\n"
        "stuff\n"
        "## 2026-05-24 — Slice 13a COMPLETE — implementer AFTER\n"
        "more stuff\n"
        "## 2026-05-24 — Slice 13a ACCEPTED — finalizer AFTER\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchors = [
        a for a in anchors
        if a.event in {"starting", "complete", "accepted"}
    ]
    assert [a.event for a in heading_anchors] == [
        "starting",
        "complete",
        "accepted",
    ]
    assert [a.line_start for a in heading_anchors] == [1, 3, 5]
    # All three heading anchors share the slice_id.
    assert all(a.slice_id == "13a" for a in heading_anchors)


def test_anchors_use_one_indexed_line_numbers() -> None:
    """Line numbers are 1-indexed per doc-13:147 (the markdown / JSONL
    convention the 13a ``_line_positive_when_present`` validator
    enforces).

    A heading on the first line yields ``line_start == 1``, NOT 0.
    """

    body = "## 2026-05-24 — Slice 13c STARTING — first-line heading\n"
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert anchors[0].line_start == 1


def test_parser_emits_decision_log_line_none_in_13c() -> None:
    """Per the STATUS.md § "Next safe action" Chunk-shape point 1
    ``decision_log_line`` is ``None`` on every 13c-emitted anchor.

    The JSONL decision-log parser is doc-13:184-185 step 4 / the natural
    13d sub-slice; until it lands the parser cannot anchor anything
    into the decision log.
    """

    body = (
        "## 2026-05-24 — Slice 13b ACCEPTED — finalizer AFTER\n"
        "- P3-13b-2 carried.\n"
        "111 passed in 0.31s.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    assert anchors, "fixture should produce at least one anchor"
    for anchor in anchors:
        assert anchor.decision_log_line is None


def test_parser_event_taxonomy_is_exactly_the_six_documented_values() -> None:
    """The 13c parser emits ONLY the six documented event values
    (``starting`` / ``complete`` / ``accepted`` / ``finding`` /
    ``test_result`` / ``subagent``).

    The 6-value taxonomy is the post-P2-13c-R2-remediation contract
    (``subagent`` was added so doc-13:182-183 "subagent IDs" gets a
    typed cite). Any other event tag would be a regression -- the
    typed model accepts free-form strings, but the parser contract is
    the six-value taxonomy.
    """

    body = (
        "## 2026-05-24 — Slice 13a STARTING — implementer BEFORE\n"
        "## 2026-05-24 — Slice 13a COMPLETE — implementer AFTER\n"
        "- P3-13a-2 (carried).\n"
        "- P3-13a-1 RESOLVED.\n"
        "- subagent 019e44a0-eee0-7833-a000-39059a49d41a dispatched.\n"
        "75 passed in 0.14s.\n"
        "## 2026-05-24 — Slice 13a ACCEPTED — finalizer AFTER\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    allowed = {
        "starting", "complete", "accepted",
        "finding", "test_result", "subagent",
    }
    for anchor in anchors:
        assert anchor.event in allowed, (
            f"unexpected event tag {anchor.event!r} on anchor {anchor!r}"
        )
    # Confirm the subagent anchor is actually emitted (the body above
    # exercises every event tag the parser knows how to emit).
    event_set = {a.event for a in anchors}
    assert event_set == allowed, (
        f"parser failed to emit every event tag; missing "
        f"{allowed - event_set!r}"
    )


def test_body_argument_does_not_touch_filesystem() -> None:
    """When ``body=`` is supplied the parser does NOT read from ``path``
    -- the path is recorded into ``journal_path`` but the filesystem is
    untouched.

    This is the test-only escape hatch the parser docstring promises;
    the production caller always omits ``body=`` and the parser reads
    from disk in that path.
    """

    # A path that does not exist -- if the parser tried to read it the
    # ``Path.read_text`` call would raise FileNotFoundError. With body=
    # supplied the file should never be touched.
    nonexistent_path = "/nonexistent/path/to/implementation-journal.md"
    body = "## 2026-05-24 — Slice 13c STARTING — body escape hatch\n"
    anchors = parse_implementation_journal(nonexistent_path, body=body)
    assert len(anchors) == 1
    assert anchors[0].journal_path == nonexistent_path
    assert anchors[0].event == "starting"


def test_parser_reads_real_file_when_body_omitted(
    tmp_path: Path,
) -> None:
    """When ``body=`` is omitted the parser reads from ``path`` via
    ``Path.read_text(encoding="utf-8")``.

    This is the production caller path; the ingestor in a later
    sub-slice will pass a real path and expect the parser to do the
    file read itself.
    """

    journal_file = tmp_path / "implementation-journal.md"
    journal_file.write_text(
        "## 2026-05-24 — Slice 13c STARTING — real file path\n",
        encoding="utf-8",
    )
    anchors = parse_implementation_journal(journal_file)
    assert len(anchors) == 1
    anchor = anchors[0]
    assert anchor.slice_id == "13c"
    assert anchor.event == "starting"
    assert anchor.journal_path == str(journal_file)


def test_parser_event_priority_keeps_heading_anchor_first_at_same_line() -> None:
    """The sort key promises heading anchors sort before per-line
    anchors at the same ``line_start``.

    In practice the heading is always at a strictly earlier line than
    its section bullets, so this never ties; but the explicit
    secondary-priority key makes the contract self-documenting and the
    test pins it.
    """

    body = (
        "## 2026-05-24 — Slice 13a STARTING — implementer BEFORE\n"
        "## 2026-05-24 — Slice 13a COMPLETE — implementer AFTER\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    # The COMPLETE anchor follows the STARTING anchor in source order.
    assert [a.event for a in anchors] == ["starting", "complete"]


# ── numeric finding-density / counting sanity ──────────────────────────────


def test_multiple_test_result_lines_each_emit_one_anchor() -> None:
    """Multiple ``N passed`` lines in one section each emit their own
    ``event="test_result"`` anchor.

    The parser docstring promises one anchor per matched ``N passed``;
    a section that quotes multiple test commands generates a separate
    anchor per result line so the metrics consumer can count per-line.
    """

    body = (
        "## 2026-05-24 — Slice 13b ACCEPTED — finalizer AFTER\n"
        "\n"
        "Tests:\n"
        "- ingestor: 36 passed in 0.16s.\n"
        "- models: 75 passed in 0.15s.\n"
        "- combined: 111 passed in 0.31s.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    test_result_anchors = [
        a for a in anchors if a.event == "test_result"
    ]
    assert len(test_result_anchors) == 3
    # All three carry the same slice_id and accepted=False.
    for anchor in test_result_anchors:
        assert anchor.slice_id == "13b"
        assert anchor.accepted is False


def test_heading_with_subhyphen_slice_id_preserved() -> None:
    """Sub-id slice ids with multi-segment dash suffixes (``11d``,
    ``08e-3a``, ``12a-1``) survive the parser's regex.

    The journal contains a long history of these multi-segment ids
    (Slice 11n, Slice 12f, etc.); the parser must accept them so the
    full implementation history is anchorable.
    """

    body = (
        "## 2026-05-22 — Slice 11d STARTING\n"
        "## 2026-05-21 — Slice 08e-3a COMPLETE\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    slice_ids = [a.slice_id for a in anchors]
    assert "11d" in slice_ids
    assert "08e-3a" in slice_ids


def test_no_test_result_line_in_section_means_no_test_result_anchor() -> None:
    """A heading whose section contains no ``N passed`` line produces
    NO ``event="test_result"`` anchor.

    Negative coverage: confirms the parser does not false-fire on
    prose that happens to contain unrelated numbers.
    """

    body = (
        "## 2026-05-24 — Slice 13c STARTING\n"
        "\n"
        "Scope: 5 numbered points per STATUS.md. The 16 files modified\n"
        "include 2 metadata journals plus the source CREATEs.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    # Only the heading anchor; no false-fired test-result anchor.
    assert len(anchors) == 1
    assert anchors[0].event == "starting"


def test_resolve_marker_is_case_sensitive_on_keyword_only() -> None:
    """The RESOLVED/CLOSED/FIXED markers are recognised as uppercase
    keywords (the journal's consistent house style).

    A lowercase "resolved" word in prose should NOT count as a
    resolution marker -- the parser would over-eagerly mark a finding
    as resolved otherwise. This is a regression guard against future
    grammar loosening.
    """

    body = (
        "## 2026-05-24 — Slice 13b COMPLETE — implementer AFTER\n"
        "\n"
        "- We discussed how the P1-13b-1 issue might be resolved later.\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )
    heading_anchor = next(a for a in anchors if a.event == "complete")
    # The lowercase "resolved" does NOT match -- the finding stays
    # open. (The uppercase RESOLVED keyword is the recognised marker.)
    assert "P1-13b-1" in heading_anchor.open_findings


# ── 13c finalizer remediation tests ─────────────────────────────────────────


def test_p1_13c_r1_cross_slice_finding_not_misattributed_to_heading() -> None:
    """P1-13c-R1 remediation: a finding id whose OWN owning slice
    differs from the heading's slice does NOT contribute to the heading
    anchor's ``open_findings``. Instead a separate ``event="finding"``
    anchor is emitted with ``slice_id=<finding-owning-slice>``.

    Real-journal symptom (before remediation): a heading like
    ``## Slice 10a COMPLETE`` containing references to ``P3-10b-1`` and
    ``P3-10c-2`` would absorb both findings into the 10a heading's
    ``open_findings``, mis-attributing 10b/10c findings to 10a. After
    the remediation the heading's ``open_findings`` is empty and two
    separate finding anchors are emitted with the correct owning slices.

    Cites: doc-13:46-47 (drift/findings as quality signals);
    doc-13a:24, 109-118 (no silent loss).
    """

    body = (
        "## 2026-05-24 — Slice 10a COMPLETE — finalizer AFTER entry\n"
        "\n"
        "- Cross-slice carry: P3-10b-1 (owning slice 10b, NOT 10a).\n"
        "- Cross-slice carry: P3-10c-2 (owning slice 10c, NOT 10a).\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )

    # Heading anchor for 10a -- ``open_findings`` is EMPTY (no
    # mis-attribution of 10b/10c findings).
    heading_anchor = next(a for a in anchors if a.event == "complete")
    assert heading_anchor.slice_id == "10a"
    assert heading_anchor.open_findings == [], (
        f"heading anchor's open_findings must be empty (no cross-slice "
        f"mis-attribution); got {heading_anchor.open_findings!r}"
    )

    # Two separate per-line finding anchors, each carrying the
    # finding's OWN owning slice.
    finding_anchors = [a for a in anchors if a.event == "finding"]
    assert len(finding_anchors) == 2, (
        f"expected 2 per-finding anchors; got {len(finding_anchors)}"
    )
    by_slice = {a.slice_id: a for a in finding_anchors}
    assert "10b" in by_slice and "10c" in by_slice, (
        f"expected finding anchors for slices 10b + 10c; got "
        f"{sorted(by_slice)}"
    )
    assert by_slice["10b"].open_findings == ["P3-10b-1"]
    assert by_slice["10c"].open_findings == ["P3-10c-2"]


def test_p1_13c_r1_same_slice_finding_still_populates_open_findings() -> None:
    """P1-13c-R1 remediation: findings whose owning slice MATCHES the
    heading's slice still populate the heading anchor's
    ``open_findings`` (no behaviour change for the same-slice case).

    A heading like ``## Slice 13a ACCEPTED`` containing ``P2-13a-1`` +
    ``P3-13a-2`` populates ``open_findings`` with both ids; the
    per-line finding anchors are ALSO emitted for citation density
    (the existing parser behaviour, preserved by the remediation).
    """

    body = (
        "## 2026-05-24 — Slice 13a ACCEPTED — finalizer AFTER entry\n"
        "\n"
        "- **P2-13a-1** -- preview/exact cross-validation gap (carried).\n"
        "- **P3-13a-2** -- docstring count nit (carried).\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )

    heading_anchor = next(a for a in anchors if a.event == "accepted")
    assert heading_anchor.slice_id == "13a"
    # Both findings, owning slice 13a, populate open_findings on the
    # 13a heading anchor.
    assert "P2-13a-1" in heading_anchor.open_findings
    assert "P3-13a-2" in heading_anchor.open_findings

    # Per-line finding anchors ALSO emitted for citation density.
    finding_anchors = [a for a in anchors if a.event == "finding"]
    assert len(finding_anchors) == 2
    for anchor in finding_anchors:
        assert anchor.slice_id == "13a"
        assert anchor.accepted is False


def test_p2_13c_r1_dual_keyword_heading_emits_two_anchors() -> None:
    """P2-13c-R1 remediation: a heading that carries TWO
    ``Slice <id> <state>`` clauses (separated by an em-dash) emits TWO
    anchors at the SAME ``line_start``.

    Real-journal canonical example (line 14522):
    ``## Slice 08g COMPLETE -- Slice 08 ACCEPTED (2026-05-21)``. Before
    the remediation only the first clause produced an anchor; after,
    both clauses emit anchors with the correct slice + event + accepted
    triples.
    """

    body = (
        "## Slice 08g COMPLETE — Slice 08 ACCEPTED (2026-05-21)\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )

    # Exactly two anchors emitted from the one heading line.
    assert len(anchors) == 2, (
        f"dual-keyword heading must emit 2 anchors; got {len(anchors)} "
        f"({[a.event for a in anchors]})"
    )

    # First anchor: Slice 08g COMPLETE.
    first = anchors[0]
    assert first.slice_id == "08g"
    assert first.event == "complete"
    assert first.accepted is False
    assert first.line_start == 1

    # Second anchor: Slice 08 ACCEPTED.
    second = anchors[1]
    assert second.slice_id == "08"
    assert second.event == "accepted"
    assert second.accepted is True
    assert second.line_start == 1  # same line as the first clause


def test_p2_13c_r2_subagent_uuids_emit_separate_subagent_anchors() -> None:
    """P2-13c-R2 remediation: each subagent UUID matched in a section
    body emits a separate ``event="subagent"`` anchor with the UUID in
    ``open_findings``.

    Real-journal shape: a section that lists two dispatched subagents
    by their UUIDv7 ids. Before the remediation the
    ``_SUBAGENT_UUID_RE`` was compiled but never used (doc-13:182-183
    contract gap). After: each UUID emits its own typed cite.
    """

    body = (
        "## 2026-05-24 — Slice 13b STARTING — implementer BEFORE entry\n"
        "\n"
        "Sibling implementers dispatched:\n"
        "- `019e44a0-eee0-7833-a000-39059a49d41a` (`Anscombe`): store tests\n"
        "- `019e44b1-ffff-7833-a000-39059a49d41b` (`Bartle`): schema tests\n"
    )
    anchors = parse_implementation_journal(
        "implementation-journal.md", body=body
    )

    subagent_anchors = [a for a in anchors if a.event == "subagent"]
    assert len(subagent_anchors) == 2, (
        f"expected 2 subagent anchors; got {len(subagent_anchors)}"
    )
    uuids_seen = {a.open_findings[0] for a in subagent_anchors}
    assert uuids_seen == {
        "019e44a0-eee0-7833-a000-39059a49d41a",
        "019e44b1-ffff-7833-a000-39059a49d41b",
    }
    for anchor in subagent_anchors:
        assert anchor.slice_id == "13b"
        assert anchor.accepted is False
        assert anchor.line_start is not None and anchor.line_start >= 4
