"""Sub-slice 13k -- doc-13 § Acceptance Criteria pinning tests.

Doc-13:245-254 (verbatim 5-bullet "Acceptance Criteria" section) sets the
contract every governance evidence set MUST satisfy:

    - Every governance evidence set cites stable typed ids, compatibility
      projection ids, Git provenance refs, or implementation-log anchors.
      [bullet (a)]
    - Evidence quality is explicit and conservative. [bullet (b)]
    - No default governance path calls unbounded artifact/event body APIs.
      [bullet (c)]
    - Implementation journals/logs are first-class evidence for plan-vs-
      actual analysis. [bullet (d)]
    - Missing Slice 00-12 acceptance or unresolved P1/P2 findings blocks
      governance acceptance. [bullet (e)]

The existing 308-test governance suite (post-13j) already pins bullets (b)
and (d):

- **(b) explicit-conservative-quality** -- pinned by
  ``test_governance_evidence_set_digester.py`` via the 5 ``_project_quality``
  branches at ``evidence_set.py:540-599``
  (``test_any_preview_only_ref_yields_preview_only_and_insufficient`` +
  ``test_legacy_only_refs_yield_insufficient_quality`` +
  ``test_empty_input_with_budget_exhausted_yields_unavailable_and_insufficient``
  + the 5 ``_project_quality`` branches enumerated in
  ``evidence_set.py:540-599``).
- **(d) implementation-journals-first-class** -- pinned by
  ``test_governance_evidence_set_digester.py:1222`` /
  ``test_journal_anchors_project_to_implementation_journal_authority_refs``
  + ``test_decision_log_anchors_project_to_implementation_decision_log_authority_refs``
  + the 5 ``_anchor_to_ref`` branches at ``evidence_set.py:716-742``.

This file pins the remaining 3 bullets:

- **(a) typed-ID-citation invariant** (per doc-13:247-248) -- every
  :attr:`GovernanceEvidenceRef.ref_id` produced by the default ingestor
  AND the evidence-set digester resolves to one of the recognised shapes
  (typed id, projection id, Git provenance ref, implementation-log
  anchor). Free-form strings fail closed. Implemented via a regex-based
  shape classifier that fails on any unrecognised shape across all 9
  doc-13:74-84 :data:`EvidenceAuthority` values.

- **(c) no-unbounded-body invariant** (per doc-13:250) -- none of the
  bounded readers invoked across the 4 lane types (primary
  ``typed_journal`` lane + ``supervisor_digest`` lane + ``resource_snapshot``
  lane + ``legacy_event`` lane + ``legacy_artifact_summary`` lane) call
  any unbounded artifact/event body API. Implemented via the existing
  :class:`_RecordingReader` precedent at
  ``tests/test_governance_evidence_ingestor.py:1448`` -- every
  ``__getattr__`` access on the reader is captured and the test fails
  closed if any write/insert/update/commit/body-hydrate-shaped attribute
  is touched during an end-to-end ingest call that exercises all 4 lanes.

- **(e) missing-Slice-00-12-acceptance-blocks-governance invariant**
  (per doc-13:253-254) -- the governance digester emits a fail-closed
  evidence set with ``quality="insufficient"`` + ``blockers`` populated
  when Slice 00-12 acceptance evidence is missing / unresolved P1/P2
  findings are present. **Slice 13A first sub-slice landed this
  enforcement** (P3-13e-3 + criterion (e) closure): the 13e digester's
  :func:`_project_blockers` was unfrozen + extended to emit
  ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
  blockers for each unresolved P1 / P2 finding in any anchor's
  ``open_findings`` list; :func:`_project_quality` distinguishes the
  hard non-legacy blocker class from the soft legacy-authority class
  and forces ``quality="insufficient"`` on the hard class per
  doc-13:217. This test was promoted from
  ``@pytest.mark.xfail(strict=True)`` to a regular passing assertion.

Per the implementer prompt § non-negotiables: stdlib + governance siblings
imports only; tests are honest (no ``assert True``, no swallowed
exceptions); each pinning test cites doc-13:245-254 verbatim.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from iriai_build_v2.workflows.develop.governance import (
    BoundedReader,
    BoundedReadResult,
    DefaultGovernanceEvidenceIngestor,
    EvidenceAuthority,
    EvidenceQuality,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
    GovernanceWindow,
    ImplementationArtifactAnchor,
    compose_governance_evidence_set,
)


# ── Shape classifier (doc-13:247-248 acceptance criterion (a)) ────────────


# Per ``evidence_set.py:660-684`` (``_anchor_ref_id``), every anchor-derived
# ref_id has the shape ``<authority>:<slice_id>:<journal_path>:<event>:L<line>``
# (the 5-component colon-separated form). The 9-value
# :data:`EvidenceAuthority` enum at ``models.py:62-72`` enumerates every
# accepted authority prefix; the prefix MUST be one of those 9 values.
_ANCHOR_REF_ID_RE: re.Pattern[str] = re.compile(
    r"^(?P<authority>"
    r"typed_journal|"
    r"compatibility_projection|"
    r"git_provenance|"
    r"implementation_journal|"
    r"implementation_decision_log|"
    r"supervisor_digest|"
    r"resource_snapshot|"
    r"legacy_event|"
    r"legacy_artifact_summary"
    r"):"
    # slice_id segment (e.g. ``13a``, ``13e``, ``11d``); permit alphanumerics
    # + dashes per the canonical doc-N slice naming.
    r"(?P<slice_id>[\w\-]+):"
    # journal_path segment; allow alphanumerics, dashes, dots, slashes,
    # underscores.
    r"(?P<journal_path>[\w\-./]+):"
    # event segment; allow alphanumerics + underscores (the 7 doc-13d /
    # 6 doc-13c event constants).
    r"(?P<event>[\w_]+):"
    # ``L`` literal followed by an integer line/row number OR ``L?`` for
    # the "no line" anchor projection.
    r"L(?P<line>\d+|\?)$"
)


# A row-derived ref_id (built by ``ingestor.py::_project_row_to_ref``) reads
# from ``row.get("ref_id") or row.get("id") or "unknown"``. The shape of the
# row id depends on the underlying typed source:
# - typed_journal / supervisor_digest / resource_snapshot rows come from
#   ``ExecutionControlStore`` / ``ControlPlaneSnapshot`` / supervisor digest
#   tables -- the ``ref_id`` is the typed row's primary key projected to a
#   string (e.g. ``"row-0"`` in synthetic tests, integer-shaped strings in
#   production);
# - compatibility_projection rows come from the Slice-01 typed projection
#   table -- the ``ref_id`` is the projection id (e.g. ``"dag-12345"``);
# - git_provenance rows carry a commit-hash or refspec id (e.g.
#   ``"deadbeef"`` or ``"refs/heads/main"``);
# - legacy_event / legacy_artifact_summary rows come from the pre-Slice-01
#   ``events`` / ``artifacts`` tables -- the ``ref_id`` is a legacy
#   integer-shaped string or a typed prefix (e.g. ``"legacy-event:row-0"``).
#
# Doc-13:247-248 mandates "stable typed ids, compatibility projection ids,
# Git provenance refs, or implementation-log anchors" -- per the
# ``feedback_no_silent_degradation`` rule the fallback ``"unknown"`` MUST
# fail the shape check (it's a free-form string indicating the upstream
# row had no id column).
#
# The accepted row-derived shape: ``[a-zA-Z][\w:./\-]+`` -- starts with an
# alpha char, then a stable identifier chars set (the "stable typed ids"
# contract). Empty strings + leading-whitespace strings + the ``"unknown"``
# fallback all fail closed.
_ROW_DERIVED_REF_ID_RE: re.Pattern[str] = re.compile(
    r"^[a-zA-Z][\w:./\-]+$"
)


_AUTHORITY_PREFIX_RE: re.Pattern[str] = re.compile(
    r"^(typed_journal|compatibility_projection|git_provenance|"
    r"implementation_journal|implementation_decision_log|"
    r"supervisor_digest|resource_snapshot|"
    r"legacy_event|legacy_artifact_summary):"
)


def _classify_ref_id(ref_id: str) -> str:
    """Classify ``ref_id`` against the doc-13:247-248 accepted shapes.

    Returns one of:
    - ``"anchor"`` -- the 5-component colon-separated implementation-log
      anchor form built by ``evidence_set.py::_anchor_ref_id``.
    - ``"row"`` -- the row-derived stable-typed-id form built by
      ``ingestor.py::_project_row_to_ref``.
    - ``"free_form"`` -- an unrecognised shape (e.g. the ``"unknown"``
      fallback or a malformed free-form string). The acceptance-criterion
      (a) invariant fails closed on this return value.

    The classifier is pure (no IO); the test below calls it on every
    :attr:`GovernanceEvidenceRef.ref_id` produced by the end-to-end ingest.
    """

    if not isinstance(ref_id, str):
        return "free_form"
    # Empty / whitespace-only / "unknown" all fail closed per the
    # doc-13:247-248 stable-typed-id contract.
    if not ref_id or ref_id.strip() != ref_id:
        return "free_form"
    if ref_id == "unknown":
        return "free_form"

    # An authority-prefixed string MUST match the full 5-component anchor
    # form -- a partial/malformed anchor (e.g.
    # ``"implementation_journal:incomplete"``) is a free-form violation by
    # construction (it claims an authority prefix without the full anchor
    # shape required by ``evidence_set.py:660-684::_anchor_ref_id``).
    if _AUTHORITY_PREFIX_RE.match(ref_id):
        if _ANCHOR_REF_ID_RE.match(ref_id):
            return "anchor"
        return "free_form"

    if _ROW_DERIVED_REF_ID_RE.match(ref_id):
        return "row"
    return "free_form"


# ── Shape classifier self-tests (test-honesty discipline) ──────────────────


def test_shape_classifier_recognises_anchor_form() -> None:
    """The classifier accepts the canonical anchor form built by
    ``evidence_set.py:660-684::_anchor_ref_id`` for the implementation-log
    authorities (doc-13:247-248 implementation-log-anchor class)."""

    # implementation_journal anchor: 5-component colon-separated with L<line>.
    assert (
        _classify_ref_id(
            "implementation_journal:13e:implementation-journal.md:complete:L42"
        )
        == "anchor"
    )
    # implementation_decision_log anchor: 5-component with L<line>.
    assert (
        _classify_ref_id(
            "implementation_decision_log:13e:"
            "implementation-decisions.jsonl:starting:L1138"
        )
        == "anchor"
    )
    # "L?" for the no-line anchor projection (per
    # ``evidence_set.py:680``: ``line_str = f"L{line}" if line is not None
    # else "L?"``).
    assert (
        _classify_ref_id(
            "implementation_journal:13a:journal.md:starting:L?"
        )
        == "anchor"
    )


def test_shape_classifier_recognises_row_derived_form() -> None:
    """The classifier accepts the row-derived stable-typed-id form built by
    ``ingestor.py:1429-1499::_project_row_to_ref`` (doc-13:247-248 typed-id
    + projection-id + Git-provenance-ref classes)."""

    # typed_journal row id.
    assert _classify_ref_id("row-0") == "row"
    # compatibility_projection id (e.g. dag-12345).
    assert _classify_ref_id("dag-12345") == "row"
    # git_provenance commit hash (40-hex).
    assert _classify_ref_id("a" * 40) == "row"
    # legacy_event prefixed id (per ``_make_legacy_event_rows`` at
    # ``tests/test_governance_evidence_ingestor.py:2541``).
    assert _classify_ref_id("legacy-event:row-0") == "row"
    # legacy_artifact_summary prefixed id.
    assert _classify_ref_id("legacy-artifact-summary:row-0") == "row"


def test_shape_classifier_rejects_free_form_strings() -> None:
    """The classifier fails closed on free-form / fallback / malformed
    strings per the doc-13:247-248 stable-typed-id contract + the auto-
    memory ``feedback_no_silent_degradation`` rule.

    The ``"unknown"`` fallback in ``ingestor.py:1461`` MUST be flagged as
    a violation (it's the explicit "row had no id column" sentinel)."""

    # The "unknown" fallback from ``_project_row_to_ref`` fail-closed.
    assert _classify_ref_id("unknown") == "free_form"
    # Empty string.
    assert _classify_ref_id("") == "free_form"
    # Leading whitespace.
    assert _classify_ref_id(" leading-space") == "free_form"
    # Trailing whitespace.
    assert _classify_ref_id("trailing-space ") == "free_form"
    # A free-form natural-language string.
    assert _classify_ref_id("this is a free-form sentence") == "free_form"
    # An authority-shaped string that lacks the full 5-component form.
    assert _classify_ref_id("implementation_journal:incomplete") == "free_form"


# ── Helpers (synthetic fixtures mirroring the ingestor test patterns) ──────


def _empty_window() -> GovernanceWindow:
    return GovernanceWindow()


def _default_budget() -> GovernanceReadBudget:
    return GovernanceReadBudget()


def _make_summary_rows(
    count: int, *, prefix: str = "row"
) -> list[dict[str, Any]]:
    """Build ``count`` synthetic summary rows mirroring the bounded-reader
    row shape doc-13 § "Bounded reads" expects (ids / digests / counts /
    bounded text)."""

    return [
        {
            "ref_id": f"{prefix}-{i}",
            "id": i,
            "digest": f"sha256:{prefix}-{i}",
            "summary": f"summary for {prefix} row {i}",
        }
        for i in range(count)
    ]


def _write_synthetic_fixtures(tmp_path: Path) -> tuple[Path, Path]:
    """Mirror the ``tests/test_governance_evidence_ingestor.py::
    _write_synthetic_fixtures`` helper -- a minimal journal markdown +
    decision-log JSONL pair that the 13c + 13d parsers can read."""

    journal_path = tmp_path / "implementation-journal.md"
    decisions_path = tmp_path / "implementation-decisions.jsonl"
    journal_path.write_text(
        "# Implementation Journal\n"
        "\n"
        "## 2026-05-24 -- Slice 13a STARTING\n"
        "\n"
        "- The 13a sub-slice begins.\n"
        "\n"
        "## 2026-05-24 -- Slice 13a COMPLETE\n"
        "\n"
        "- Pure model definitions landed.\n"
        "- 75 passed for the 13a targeted tests.\n",
        encoding="utf-8",
    )
    rows = [
        {
            "timestamp": "2026-05-24T00:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13a",
            "stage": "implementer_before",
            "event": "implementer_before",
            "summary": "Slice 13a STARTING -- implementer BEFORE.",
        },
        {
            "timestamp": "2026-05-24T01:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13a",
            "stage": "implementer_after",
            "event": "implementer_after",
            "summary": "Slice 13a COMPLETE -- implementer AFTER. ACCEPTED.",
        },
    ]
    decisions_path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    return journal_path, decisions_path


# ── Recording reader: captures every attribute access (no-unbounded-body) ──


class _AcceptanceRecordingReader:
    """A read-only bounded-reader fake that records EVERY attribute access.

    Mirrors the ``_RecordingReader`` precedent at
    ``tests/test_governance_evidence_ingestor.py:1448`` but exposes a clean
    surface so the acceptance-criterion (c) test can assert across all 4
    lane types without depending on the ingestor-suite's private helpers.

    The class deliberately defines ONLY ``__call__`` and the underscore
    bookkeeping attrs; any other attribute access raises ``AttributeError``
    and is recorded in ``attribute_accesses`` so a violation (e.g. the
    ingestor attempting ``reader.fetch_event_body(...)`` or
    ``reader.hydrate_artifact(...)``) fails the test loudly.

    Doc-13:250 (acceptance criterion (c)): "No default governance path
    calls unbounded artifact/event body APIs."
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        authority: EvidenceAuthority,
    ) -> None:
        # Underscore-prefixed names are stored via ``object.__setattr__``
        # so they bypass the ``__getattr__`` instrumentation.
        object.__setattr__(self, "_rows", list(rows))
        object.__setattr__(self, "_authority", authority)
        object.__setattr__(self, "calls", 0)
        object.__setattr__(self, "attribute_accesses", [])

    def __call__(
        self,
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        # Each row in ``_rows`` is treated as a SUMMARY-only row (the
        # doc-13:191-192 "Use summaries and selected slices only" contract).
        # The reader never receives a request for a multi-megabyte body
        # field; the row shape is bounded by construction.
        object.__setattr__(self, "calls", self.calls + 1)
        return BoundedReadResult(
            rows=list(self._rows), authority=self._authority
        )

    def __getattr__(self, name: str) -> Any:
        # ``__getattr__`` fires only for undefined attributes; underscore
        # names + ``calls`` + ``attribute_accesses`` are stored on the
        # instance dict so they hit normal lookup first.
        #
        # The list of "unbounded body" attribute names is the set every
        # governance ingestor MUST avoid per doc-13:250. We record them ALL
        # so the test surfaces any future addition that violates the
        # bounded-read contract.
        self.attribute_accesses.append(name)
        raise AttributeError(
            f"_AcceptanceRecordingReader has no attribute {name!r}; "
            "doc-13:250 forbids any unbounded artifact/event body API "
            "call on the governance read path."
        )


# ── Acceptance criterion (a): typed-ID-citation invariant ─────────────────


@pytest.mark.asyncio
async def test_acceptance_criterion_a_every_ref_id_resolves_to_typed_shape(
    tmp_path,
) -> None:
    """Doc-13:247-248 (acceptance criterion (a)) verbatim: "Every
    governance evidence set cites stable typed ids, compatibility
    projection ids, Git provenance refs, or implementation-log anchors."

    The end-to-end ingest exercises ALL 4 lanes (primary typed_journal
    lane + supervisor_digest lane + resource_snapshot lane + the 2 legacy
    lanes), composes the evidence set via the FROZEN 13e digester, and
    asserts that every ``GovernanceEvidenceRef.ref_id`` (including
    anchor-derived refs from the 13c + 13d parsers) classifies as either
    an ``"anchor"`` shape or a ``"row"`` shape -- never ``"free_form"``.

    The classifier (``_classify_ref_id`` above) explicitly rejects:
    - the ``"unknown"`` fallback from ``ingestor.py:1461::_project_row_to_ref``
      (which would indicate the upstream row had no id column);
    - empty / whitespace-only / malformed strings;
    - any string not matching the 5-component anchor form OR the
      stable-typed-id pattern.

    A violation = the doc-13:247-248 contract has regressed.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)

    # Primary reader: empty rows so the parser-based path drives anchor
    # production. The 13c + 13d parsers emit anchors from the synthetic
    # journal markdown + JSONL above.
    primary_reader = _AcceptanceRecordingReader(rows=[], authority="typed_journal")
    supervisor_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(2, prefix="sup"),
        authority="supervisor_digest",
    )
    resource_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(2, prefix="res"),
        authority="resource_snapshot",
    )
    legacy_event_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(2, prefix="legacy-event"),
        authority="legacy_event",
    )
    legacy_artifact_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(2, prefix="legacy-artifact-summary"),
        authority="legacy_artifact_summary",
    )

    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=_default_budget()
    )

    # The composed set has refs from all 4 lanes.
    assert len(result.refs) > 0, (
        "Expected the end-to-end ingest to produce refs from the 4 "
        "configured lanes; got empty refs list."
    )

    # Every ref_id MUST classify as anchor OR row -- never free_form.
    free_form_violations: list[tuple[str, str]] = []
    for ref in result.refs:
        shape = _classify_ref_id(ref.ref_id)
        if shape == "free_form":
            free_form_violations.append((ref.authority, ref.ref_id))

    assert not free_form_violations, (
        f"Doc-13:247-248 acceptance criterion (a) violated: "
        f"{len(free_form_violations)} ref_id(s) classify as free_form. "
        f"Every GovernanceEvidenceRef.ref_id MUST resolve to one of "
        f"{{typed id, projection id, Git provenance ref, implementation-"
        f"log anchor}}. Violations: {free_form_violations}"
    )

    # Belt-and-suspenders: the page-refs embedded under each ref also use
    # the same shape contract (page_ref_id is built from ref_id at
    # ``ingestor.py:1472``: ``f"{ref_id}:row-page"``). A page-ref id
    # that does not at minimum START with a recognised ref_id prefix is
    # a free-form violation by inheritance.
    page_ref_violations: list[tuple[str, str]] = []
    for ref in result.refs:
        for page_ref in ref.page_refs:
            # Strip the ``":row-page"`` suffix (or any colon-separated
            # paging suffix) and re-classify the parent id. A page-ref
            # id always has the parent ref_id as a prefix; the suffix is
            # a paging tag.
            parent_id = page_ref.page_ref_id.rsplit(":", 1)[0]
            shape = _classify_ref_id(parent_id)
            if shape == "free_form":
                page_ref_violations.append(
                    (page_ref.authority, page_ref.page_ref_id)
                )

    assert not page_ref_violations, (
        f"Doc-13:247-248 acceptance criterion (a) violated on page refs: "
        f"{page_ref_violations}"
    )


def test_acceptance_criterion_a_digester_only_path_journal_anchors() -> None:
    """Doc-13:247-248 (acceptance criterion (a)) -- the digester-only path
    (no ingestor) produces anchor-shaped ref_ids for the 13c + 13d journal
    + decision-log anchors. Mirrors the
    ``evidence_set.py:660-684::_anchor_ref_id`` projection.

    This is a focused unit-test complement to the end-to-end ingestor
    test above: it pins the anchor-shape contract at the digester surface
    so a future evidence_set.py edit that changes the projection shape
    fails this test loudly.
    """

    journal_anchor = ImplementationArtifactAnchor(
        slice_id="13e",
        journal_path="implementation-journal.md",
        line_start=42,
        decision_log_line=None,
        event="complete",
        accepted=False,
        open_findings=[],
    )
    decision_anchor = ImplementationArtifactAnchor(
        slice_id="13e",
        journal_path="implementation-decisions.jsonl",
        line_start=None,
        decision_log_line=1138,
        event="starting",
        accepted=False,
        open_findings=[],
    )

    result = compose_governance_evidence_set(
        journal_anchors=[journal_anchor],
        decision_log_anchors=[decision_anchor],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    assert len(result.refs) == 2

    # Both ref_ids MUST classify as anchor shape (the doc-13:247-248
    # implementation-log-anchor class).
    for ref in result.refs:
        shape = _classify_ref_id(ref.ref_id)
        assert shape == "anchor", (
            f"Doc-13:247-248 acceptance criterion (a) violated at the "
            f"digester surface: ref_id {ref.ref_id!r} (authority "
            f"{ref.authority!r}) does not classify as anchor "
            f"(got {shape!r})."
        )


# ── Acceptance criterion (c): no-unbounded-body invariant ─────────────────


@pytest.mark.asyncio
async def test_acceptance_criterion_c_no_unbounded_body_apis_across_all_lanes(
    tmp_path,
) -> None:
    """Doc-13:250 (acceptance criterion (c)) verbatim: "No default
    governance path calls unbounded artifact/event body APIs."

    The end-to-end ingest exercises ALL 4 lane types (primary
    ``typed_journal`` + ``supervisor_digest`` + ``resource_snapshot`` +
    ``legacy_event`` + ``legacy_artifact_summary``) and asserts that on
    EVERY lane the recording reader observes ONLY the ``__call__``
    invocation -- no body-shaped attribute access (e.g.
    ``fetch_event_body`` / ``hydrate_artifact`` / ``get_full_body``).

    The recording reader's ``__getattr__`` fires on EVERY undefined
    attribute access; the assertion below is that
    ``attribute_accesses == []`` for every lane reader. Any future
    ingestor code path that attempts to invoke an unbounded-body API
    will trip the ``__getattr__`` recording first AND raise
    ``AttributeError`` -- the test fails closed twice over.

    The bounded-read invariant lives in ``ingestor.py:919-956``
    (``_invoke_reader``) + ``ingestor.py:1015-1027``
    (``_invoke_reader_with``); both forward the ``LIMIT cap+1`` sentinel
    + ``SET LOCAL statement_timeout`` to the reader and never request a
    body field beyond ``budget.max_chars_per_ref``.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)

    # All 4 lane readers are recording-only; any attribute access beyond
    # the call invocation fails closed.
    primary_reader = _AcceptanceRecordingReader(rows=[], authority="typed_journal")
    supervisor_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(3, prefix="sup"),
        authority="supervisor_digest",
    )
    resource_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(3, prefix="res"),
        authority="resource_snapshot",
    )
    legacy_event_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(3, prefix="legacy-event"),
        authority="legacy_event",
    )
    legacy_artifact_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(3, prefix="legacy-artifact-summary"),
        authority="legacy_artifact_summary",
    )

    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    # ingest_implementation_artifacts touches all 5 augmenting + legacy lanes
    # + the parser-based journal + decision-log path.
    await ingestor.ingest_implementation_artifacts(
        [], budget=_default_budget()
    )

    # Primary reader is NOT invoked on the implementation-artifact path
    # (the parser-based 13f wiring uses the journal/decision-log files,
    # not the primary reader); it's the augmenting + legacy lanes that
    # consume their respective readers.
    assert primary_reader.attribute_accesses == [], (
        f"Doc-13:250 violated on primary (typed_journal) lane: "
        f"{primary_reader.attribute_accesses}"
    )
    # The 4 augmenting + legacy lane readers MUST have been invoked and
    # MUST have observed zero attribute accesses beyond the call.
    for lane_name, reader in (
        ("supervisor_digest", supervisor_reader),
        ("resource_snapshot", resource_reader),
        ("legacy_event", legacy_event_reader),
        ("legacy_artifact_summary", legacy_artifact_reader),
    ):
        assert reader.calls > 0, (
            f"Lane {lane_name} reader was never invoked; the "
            f"ingest_implementation_artifacts wiring may have regressed."
        )
        assert reader.attribute_accesses == [], (
            f"Doc-13:250 acceptance criterion (c) violated on the "
            f"{lane_name} lane: unbounded-body / write-shaped attribute "
            f"access observed: {reader.attribute_accesses}"
        )


@pytest.mark.asyncio
async def test_acceptance_criterion_c_no_unbounded_body_apis_during_ingest_feature_window(
    tmp_path,
) -> None:
    """Doc-13:250 (acceptance criterion (c)) -- the other ingestor surface
    (``ingest_feature_window``) likewise calls ONLY the bounded-read
    ``__call__``; no body-shaped attribute access.

    The 13b ``ingest_feature_window`` path goes through the primary
    ``BoundedReader`` (no legacy lanes); this test pins the same invariant
    on that surface so a future edit to ``ingest_feature_window`` that
    introduces an unbounded read fails closed.
    """

    primary_reader = _AcceptanceRecordingReader(
        rows=_make_summary_rows(5, prefix="feat"),
        authority="typed_journal",
    )
    ingestor = DefaultGovernanceEvidenceIngestor(primary_reader)

    await ingestor.ingest_feature_window(
        feature_id="feat-13k",
        window=_empty_window(),
        budget=_default_budget(),
    )

    assert primary_reader.calls > 0, (
        "Primary reader was never invoked by ingest_feature_window."
    )
    assert primary_reader.attribute_accesses == [], (
        f"Doc-13:250 acceptance criterion (c) violated on "
        f"ingest_feature_window: {primary_reader.attribute_accesses}"
    )


# ── Acceptance criterion (e): missing-Slice-00-12-acceptance ENFORCED ─────
#
# Slice 13A first sub-slice (P3-13e-3 + criterion (e) closure) promoted
# this test from ``@pytest.mark.xfail(strict=True)`` to a regular passing
# assertion. The enforcement lives in evidence_set.py:_project_blockers
# (emits ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
# blockers for each unresolved P1 / P2 finding) + evidence_set.py:
# _project_quality (forces ``insufficient`` on the non-legacy
# governance_evidence_gap class per doc-13:217 fail-closed projection).


def _make_typed_only_ref(
    *,
    ref_id: str,
    digest: str,
    quality: EvidenceQuality = "canonical",
) -> GovernanceEvidenceRef:
    """Build a synthetic typed-only ref the digester treats as canonical
    (no preview_only; complete; typed authority)."""

    return GovernanceEvidenceRef(
        authority="typed_journal",
        ref_id=ref_id,
        digest=digest,
        quality=quality,
        completeness="complete",
        preview_only=False,
    )


def test_acceptance_criterion_e_missing_slice_00_12_acceptance_blocks_governance() -> None:
    """Doc-13:253-254 (acceptance criterion (e)) verbatim: "Missing
    Slice 00-12 acceptance or unresolved P1/P2 findings blocks governance
    acceptance."

    **Slice 13A first sub-slice (P3-13e-3 + criterion (e) closure).**
    The 13k precursor marked this test ``xfail(strict=True)`` pending
    Slice 13A formal enforcement; the Slice 13A first sub-slice lands
    the enforcement in the FROZEN-unfreeze 13e digester
    :func:`_project_blockers` + :func:`_project_quality`:

    - :func:`_project_blockers` emits canonical
      ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
      blockers for every unresolved P1 / P2 finding ID in any
      :class:`ImplementationArtifactAnchor.open_findings` list.
    - :func:`_project_quality` distinguishes "hard" (non-legacy
      class) governance_evidence_gap blockers from "soft" (legacy-
      authority class) blockers and forces ``quality="insufficient"``
      on the hard class per doc-13:217 fail-closed projection.

    Simulate a typed-only complete evidence set with NO Slice 00-12
    acceptance evidence + ONE unresolved P1 finding cited in the
    ``open_findings`` of an :class:`ImplementationArtifactAnchor`. The
    contract says the resulting set MUST have ``quality="insufficient"``
    + ``blockers`` populated with a canonical
    ``governance_evidence_gap`` blocker string naming the unresolved
    P1 finding per doc-13:209-210 verbatim form.
    """

    # Build a typed-only complete set with an UNRESOLVED P1 finding cited
    # in the anchor's ``open_findings``. The synthetic anchor represents
    # a Slice-00-12 anchor whose acceptance has NOT been recorded.
    anchor_with_p1_finding = ImplementationArtifactAnchor(
        slice_id="07",
        journal_path="implementation-journal.md",
        line_start=100,
        decision_log_line=None,
        event="finding",
        accepted=False,
        # Unresolved P1 finding cited per doc-13:253-254 (acceptance
        # criterion (e)).
        open_findings=["P1-07-A: unresolved typed-failure-router blocker"],
    )

    composed = compose_governance_evidence_set(
        journal_anchors=[anchor_with_p1_finding],
        decision_log_anchors=[],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    # Per doc-13:253-254, the composed set MUST fail closed:
    # 1. quality == "insufficient" because Slice 00-12 acceptance is
    #    missing AND an unresolved P1 finding is present.
    assert composed.quality == "insufficient", (
        f"Doc-13:253-254 acceptance criterion (e) requires "
        f"quality='insufficient' when Slice 00-12 acceptance is missing "
        f"or unresolved P1/P2 findings are present; got "
        f"{composed.quality!r}. Slice 13A first sub-slice enforces "
        f"this via the non-legacy governance_evidence_gap class in "
        f"evidence_set.py:_project_quality."
    )
    # 2. blockers MUST be populated with a canonical
    #    ``governance_evidence_gap`` blocker (per doc-13:209-210) naming
    #    the missing Slice-00-12 acceptance OR the unresolved P1 finding.
    assert composed.blockers, (
        f"Doc-13:253-254 acceptance criterion (e) requires non-empty "
        f"blockers when governance acceptance is blocked; got "
        f"{composed.blockers!r}."
    )
    # 3. At least one blocker MUST reference either the Slice-00-12
    #    acceptance gap OR the unresolved P1 finding (doc-13:209-210
    #    canonical ``governance_evidence_gap`` shape).
    assert any(
        "governance_evidence_gap" in b or "P1-07-A" in b
        for b in composed.blockers
    ), (
        f"Doc-13:253-254 acceptance criterion (e) requires at least one "
        f"canonical governance_evidence_gap blocker; got "
        f"{composed.blockers!r}."
    )
    # 4. Slice 13A first sub-slice -- positive assertion that the
    #    blocker is the canonical ``governance_evidence_gap:<auth>:<ref_id>``
    #    shape per doc-13:209-210 (NOT the prior bespoke
    #    ``governance_evidence_legacy_authority:`` form). At least one
    #    blocker MUST start with the canonical prefix.
    assert any(
        b.startswith("governance_evidence_gap:")
        for b in composed.blockers
    ), (
        f"Doc-13:209-210 + Slice 13A first sub-slice (P3-13e-3 closure) "
        f"requires the canonical 'governance_evidence_gap:<...>' prefix "
        f"on the blocker; got {composed.blockers!r}."
    )
    # 5. Slice 13A first sub-slice -- the open-findings sub-class
    #    blocker carries the unresolved finding id (so a Slice-15
    #    metrics / Slice-16 finding-engine consumer can match on the
    #    finding ID without re-scanning the anchor surface).
    assert any(
        b.startswith("governance_evidence_gap:open_findings:")
        and "P1-07-A" in b
        for b in composed.blockers
    ), (
        f"Slice 13A first sub-slice doc-13:253-254 criterion (e) "
        f"requires an open-findings-class blocker naming the unresolved "
        f"P1 finding id; got {composed.blockers!r}."
    )


# ── Pinning sanity: existing (b) + (d) coverage explicitly cited ─────────


def test_acceptance_criterion_b_pinned_by_existing_digester_quality_tests() -> None:
    """Doc-13:249 (acceptance criterion (b)) -- "Evidence quality is
    explicit and conservative" is pinned by 3 existing tests in
    ``tests/test_governance_evidence_set_digester.py`` (the 33-test 13e
    digester suite). This test is a thin recapitulation that cites them
    explicitly so the doc-13:245-254 acceptance-criteria coverage map is
    discoverable from this file.

    The 3 pinning tests (asserted to exist by importing them and
    confirming they are callable):
    - ``test_any_preview_only_ref_yields_preview_only_and_insufficient``
      (preview -> insufficient; covers the 13A invariant precursor);
    - ``test_legacy_only_refs_yield_insufficient_quality``
      (legacy-only -> insufficient; covers the doc-13:173-175
      legacy-quality discipline);
    - ``test_empty_input_with_budget_exhausted_yields_unavailable_and_insufficient``
      (empty + exhausted -> unavailable + insufficient).
    """

    # Import the existing pinning tests by module + attribute lookup so
    # this file fails closed if any of them are renamed or deleted in a
    # future maintenance pass.
    from tests import test_governance_evidence_set_digester as digester_tests

    pinning_test_names = (
        "test_any_preview_only_ref_yields_preview_only_and_insufficient",
        "test_legacy_only_refs_yield_insufficient_quality",
        "test_empty_input_with_budget_exhausted_yields_unavailable_and_insufficient",
    )
    for test_name in pinning_test_names:
        assert hasattr(digester_tests, test_name), (
            f"Doc-13:249 acceptance criterion (b) pinning test "
            f"{test_name!r} is missing from "
            f"tests/test_governance_evidence_set_digester.py; the (b) "
            f"coverage map has regressed."
        )
        assert callable(getattr(digester_tests, test_name)), (
            f"Doc-13:249 pinning test {test_name!r} is no longer callable."
        )


def test_acceptance_criterion_d_pinned_by_existing_anchor_to_ref_tests() -> None:
    """Doc-13:251-252 (acceptance criterion (d)) -- "Implementation
    journals/logs are first-class evidence for plan-vs-actual analysis"
    is pinned by 2 existing tests in
    ``tests/test_governance_evidence_set_digester.py``. Recapitulated
    here so the (a)-(e) coverage map is discoverable from this file.
    """

    from tests import test_governance_evidence_set_digester as digester_tests

    pinning_test_names = (
        "test_journal_anchors_project_to_implementation_journal_authority_refs",
        "test_decision_log_anchors_project_to_implementation_decision_log_authority_refs",
    )
    for test_name in pinning_test_names:
        assert hasattr(digester_tests, test_name), (
            f"Doc-13:251-252 acceptance criterion (d) pinning test "
            f"{test_name!r} is missing from "
            f"tests/test_governance_evidence_set_digester.py; the (d) "
            f"coverage map has regressed."
        )
        assert callable(getattr(digester_tests, test_name)), (
            f"Doc-13:251-252 pinning test {test_name!r} is no longer "
            f"callable."
        )
