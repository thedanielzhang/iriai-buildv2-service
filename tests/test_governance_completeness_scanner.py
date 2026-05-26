"""Slice 13A first sub-slice -- unit tests for the completeness scanner.

Covers the Slice 13A first sub-slice enforcement scanner deliverable
per STATUS.md § "Next safe action" point 4 + 6 for
:func:`iriai_build_v2.workflows.develop.governance.scan_governance_completeness`
+ the typed report
:class:`iriai_build_v2.workflows.develop.governance.CompletenessScanReport`.

Per the implementer prompt point 6 the test surface covers:

(a) Scanner detects missing slice-acceptance markers (positive test).
(b) Negative test: an accepted slice with ``0 P1 / 0 P2`` is reported
    CLEAN (``is_complete=True``).
(c) Scanner detects unresolved P1/P2 findings in journal tail.
(d) Scanner detects ``governance_evidence_gap`` blockers in a
    :class:`GovernanceEvidenceSet`.
(e) :class:`CompletenessScanReport` is typed Pydantic; out-of-bounds
    values fail closed.
(f) Real-corpus integration: scanner runs against live STATUS.md +
    journal + JSONL; expected ``is_complete=True`` (Slice 00-12 +
    Slice 13 all accepted; no unresolved P1/P2).
(g) Scanner's typed-input validation (``scan_governance_completeness(
    None, ...)`` -> TypeError).
(h) Scanner output round-trips via Pydantic
    ``model_dump_json`` -> ``model_validate_json``.

Per the implementer prompt § "Non-negotiables" the scanner fails closed
on every code path (typed TypeError on None/wrong-shape input; typed
FileNotFoundError on missing journal_path or STATUS.md). Per the
prompt § "Bounded reads" the scanner reads only the journal tail
window + the STATUS.md path; neither read triggers an unbounded
artifact body hydration.

Doc citations:

* doc-13:209-210 (canonical ``governance_evidence_gap`` blocker form)
* doc-13:217 (read-budget-exhausted: insufficient / derived quality
  projection)
* doc-13:247-254 (5 acceptance criteria; criterion (e) verbatim
  "Missing Slice 00-12 acceptance or unresolved P1/P2 findings blocks
  governance acceptance")
* doc-13a:24, 109-118 (Slice 13A invariant)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop.governance import (
    CompletenessScanReport,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
    InMemoryGovernanceEvidenceStore,
    scan_governance_completeness,
)
from iriai_build_v2.workflows.develop.governance import (
    completeness_scanner as scanner_module,
)


# ── helpers ────────────────────────────────────────────────────────────────


def _write_minimal_journal_and_status(
    tmp_path: Path,
    *,
    journal_text: str,
    status_text: str,
) -> Path:
    """Write a minimal journal + STATUS.md pair under ``tmp_path``.

    Returns the journal path. STATUS.md is placed in the same
    directory so the scanner can locate it via
    :func:`scanner_module._project_root_for`.
    """

    journal_path = tmp_path / "implementation-journal.md"
    status_path = tmp_path / "STATUS.md"
    journal_path.write_text(journal_text, encoding="utf-8")
    status_path.write_text(status_text, encoding="utf-8")
    return journal_path


def _all_accepted_status_text() -> str:
    """Build a STATUS.md text that marks every Slice 00-12 as accepted.

    Mirrors the real STATUS.md § "Completed (Slices 00-12)" + per-
    slice bullets. The 13 slice IDs (00..12) each get a recognisable
    "Slice <id> ACCEPTED" marker that
    :func:`scanner_module._detect_missing_acceptance` greps for.
    """

    bullets = "\n".join(
        f"- Slice {slice_id} (synthetic): **ACCEPTED.**"
        for slice_id in (
            "00",
            "01",
            "02",
            "03",
            "04",
            "05",
            "06",
            "07",
            "08",
            "09",
            "10",
            "11",
            "12",
        )
    )
    return f"# STATUS\n\n## Completed\n\n{bullets}\n"


def _make_minimal_evidence_set(
    *,
    corpus_id: str,
    idempotency_key: str | None = None,
    blockers: list[str] | None = None,
) -> GovernanceEvidenceSet:
    """Build a minimal valid :class:`GovernanceEvidenceSet` for scanner tests."""

    return GovernanceEvidenceSet(
        idempotency_key=idempotency_key or ("a" * 64),
        feature_id=None,
        corpus_id=corpus_id,
        generated_at=datetime(2026, 5, 24, 17, 0, 0, tzinfo=timezone.utc),
        source_window={},
        refs=[],
        omitted_refs=[],
        completeness="unavailable",
        source_mix={},
        read_budget=GovernanceReadBudget(),
        read_budget_exhausted=False,
        quality="insufficient",
        blockers=blockers if blockers is not None else [],
    )


# ── package surface ────────────────────────────────────────────────────────


def test_package_reexports_scanner_surface() -> None:
    """The Slice 13A first sub-slice scanner surface is re-exported at
    the package level.

    Both :func:`scan_governance_completeness` and
    :class:`CompletenessScanReport` MUST be re-exported from
    :mod:`iriai_build_v2.workflows.develop.governance` per the package
    ``__all__`` 24 -> 26 bump.
    """

    from iriai_build_v2.workflows.develop import governance

    assert "scan_governance_completeness" in governance.__all__
    assert "CompletenessScanReport" in governance.__all__
    assert (
        governance.scan_governance_completeness
        is scanner_module.scan_governance_completeness
    )
    assert (
        governance.CompletenessScanReport
        is scanner_module.CompletenessScanReport
    )


# ── (a) Missing slice-acceptance markers (positive detection) ─────────────


@pytest.mark.asyncio
async def test_scanner_detects_missing_slice_acceptance_markers(
    tmp_path,
) -> None:
    """Per the implementer prompt point 6 (a) + doc-13:253-254 verbatim
    ("Missing Slice 00-12 acceptance or unresolved P1/P2 findings blocks
    governance acceptance").

    A journal + STATUS.md pair that omits the acceptance marker for one
    of the required Slice 00-12 IDs -> the scanner reports that ID in
    :attr:`CompletenessScanReport.missing_acceptance` + sets
    ``is_complete=False``.
    """

    # STATUS.md accepts every slice EXCEPT 07.
    status_text = (
        "# STATUS\n\n"
        + "\n".join(
            f"- Slice {sid}: **ACCEPTED.**"
            for sid in (
                "00",
                "01",
                "02",
                "03",
                "04",
                "05",
                "06",
                # 07 deliberately OMITTED -- scanner must detect.
                "08",
                "09",
                "10",
                "11",
                "12",
            )
        )
    )
    # Journal also omits Slice 07 acceptance.
    journal_text = (
        "# Implementation Journal\n\n"
        "## 2026-05-21 -- Slice 06 ACCEPTED\n\n"
        "- Slice 06 closure.\n\n"
        "## 2026-05-21 -- Slice 08 ACCEPTED\n\n"
        "- Slice 08 closure.\n"
    )
    journal_path = _write_minimal_journal_and_status(
        tmp_path, journal_text=journal_text, status_text=status_text
    )

    store = InMemoryGovernanceEvidenceStore()
    # Put a corpus row so the evidence-gap branch is clean.
    await store.put(_make_minimal_evidence_set(corpus_id="corpus:1"))

    report = await scan_governance_completeness(
        "corpus:1", store, journal_path
    )

    assert isinstance(report, CompletenessScanReport)
    assert "07" in report.missing_acceptance, (
        f"Expected scanner to detect missing Slice 07 acceptance per "
        f"doc-13:253-254; got missing_acceptance={report.missing_acceptance!r}"
    )
    # is_complete fails closed.
    assert report.is_complete is False


# ── (b) Accepted slice + 0 P1 / 0 P2 -> clean ─────────────────────────────


@pytest.mark.asyncio
async def test_scanner_reports_clean_when_all_accepted_and_no_findings(
    tmp_path,
) -> None:
    """Per the implementer prompt point 6 (b) -- negative test.

    A STATUS.md that accepts every Slice 00-12 + a journal tail with
    zero P1/P2 finding mentions + a stored evidence_set with empty
    blockers -> the scanner reports ``is_complete=True`` with all
    three detection lists empty.
    """

    status_text = _all_accepted_status_text()
    journal_text = (
        "# Implementation Journal\n\n"
        "## 2026-05-23 -- Slice 12 ACCEPTED\n\n"
        "- Slice 12 closure; **0 P1 / 0 P2** in slice-end review.\n\n"
        "- All gates GREEN.\n"
    )
    journal_path = _write_minimal_journal_and_status(
        tmp_path, journal_text=journal_text, status_text=status_text
    )

    store = InMemoryGovernanceEvidenceStore()
    await store.put(_make_minimal_evidence_set(corpus_id="clean:1"))

    report = await scan_governance_completeness(
        "clean:1", store, journal_path
    )

    assert report.missing_acceptance == []
    # The journal line "0 P1 / 0 P2" mentions P1 / P2 in a status
    # context, but the SAME LINE check requires a status marker word
    # (CLOSED / RESOLVED / FIXED / APPLIED / REMEDIATED / DISMISSED).
    # "0 P1 / 0 P2" has neither, so the scanner SHOULD report it as
    # unresolved IF the regex matches "P1" + "P2" as standalone IDs.
    # The regex requires ``P[12]-<scope>`` (hyphenated) shape so
    # "0 P1 / 0 P2" does NOT match (no hyphen + scope).
    assert report.unresolved_findings == [], (
        f"Expected zero unresolved findings on a clean journal; got "
        f"{report.unresolved_findings!r}"
    )
    assert report.evidence_gaps == []
    assert report.is_complete is True


# ── (c) Unresolved P1/P2 findings ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanner_detects_unresolved_p1_p2_findings_in_journal_tail(
    tmp_path,
) -> None:
    """Per the implementer prompt point 6 (c) + doc-13:253-254.

    Journal tail with a P1 / P2 finding ID on a line WITHOUT a status
    marker (CLOSED / FIXED / RESOLVED / APPLIED / REMEDIATED /
    DISMISSED) -> scanner reports that finding in
    :attr:`CompletenessScanReport.unresolved_findings`.

    Conversely a finding ID on a line WITH a status marker is treated
    as resolved (the journal convention is to mention the status on
    the same line as the finding ID).
    """

    # Per the Slice 13A first-sub-slice finalizer reviewer P2-A3-1
    # remediation, the scanner uses the canonical journal-parser
    # ``FINDING_ID_REGEX`` from :data:`models.FINDING_ID_REGEX` which
    # requires a trailing ``-<digit>`` index segment (e.g. ``P1-07-1``
    # not ``P1-07-A``). The test fixture below uses the canonical
    # ``P[123]-<slice>-<digit>`` shape mirroring the real-journal
    # canonical form (``P1-13b-1``, ``P2-13h-1``, ...).
    status_text = _all_accepted_status_text()
    journal_text = (
        "# Implementation Journal\n\n"
        "## 2026-05-21 -- Slice 07 P1 finding raised\n\n"
        # Unresolved -- no status marker on the same line.
        "- **P1-07-1** -- unresolved typed-failure-router blocker.\n"
        # Resolved -- has CLOSED marker on the same line.
        "- **P2-07-2** -- CLOSED by in-slice remediator (FIXED).\n"
        # Resolved -- has FIXED marker.
        "- **P1-07-3** -- FIXED in finalizer pass.\n"
        # P3 should NOT count (only P1/P2 are blocking).
        "- **P3-07-4** -- carried.\n"
    )
    journal_path = _write_minimal_journal_and_status(
        tmp_path, journal_text=journal_text, status_text=status_text
    )

    store = InMemoryGovernanceEvidenceStore()
    await store.put(_make_minimal_evidence_set(corpus_id="findings:1"))

    report = await scan_governance_completeness(
        "findings:1", store, journal_path
    )

    assert "P1-07-1" in report.unresolved_findings, (
        f"Expected scanner to detect unresolved P1-07-1; got "
        f"unresolved_findings={report.unresolved_findings!r}"
    )
    # P2-07-2 has CLOSED marker -> resolved, should NOT be in
    # unresolved list.
    assert "P2-07-2" not in report.unresolved_findings
    # P1-07-3 has FIXED marker -> resolved.
    assert "P1-07-3" not in report.unresolved_findings
    # P3-07-4 is non-blocking (P3 severity) -> never reported as
    # unresolved per the P[12]-only match-result filter in
    # ``completeness_scanner._detect_unresolved_findings``.
    assert "P3-07-4" not in report.unresolved_findings
    assert report.is_complete is False


# ── (d) governance_evidence_gap blockers in evidence set ──────────────────


@pytest.mark.asyncio
async def test_scanner_detects_governance_evidence_gap_blockers_in_set(
    tmp_path,
) -> None:
    """Per the implementer prompt point 6 (d) + doc-13:209-210 verbatim.

    A stored :class:`GovernanceEvidenceSet` carrying a non-legacy
    ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
    blocker -> the scanner reports that blocker in
    :attr:`CompletenessScanReport.evidence_gaps` + sets
    ``is_complete=False``.

    The legacy-authority class blocker
    (``governance_evidence_gap:legacy_event:...``) is filtered OUT
    per the
    :func:`evidence_set._is_non_legacy_gap_blocker` discipline -- it
    is informational, not a hard fail-closed signal.
    """

    status_text = _all_accepted_status_text()
    # Empty journal tail (no findings).
    journal_text = "# Implementation Journal\n\n## All clean\n"
    journal_path = _write_minimal_journal_and_status(
        tmp_path, journal_text=journal_text, status_text=status_text
    )

    store = InMemoryGovernanceEvidenceStore()
    evidence_set = _make_minimal_evidence_set(
        corpus_id="gaps:1",
        blockers=[
            # Hard non-legacy blocker (open-findings class).
            "governance_evidence_gap:open_findings:07:P1-07-A",
            # Soft legacy-authority class blocker -- scanner filters out.
            "governance_evidence_gap:legacy_event:ref-legacy-1",
            "governance_evidence_gap:legacy_artifact_summary:ref-legacy-2",
        ],
    )
    await store.put(evidence_set)

    report = await scan_governance_completeness("gaps:1", store, journal_path)

    # The hard blocker MUST appear.
    assert any(
        "open_findings:07:P1-07-A" in gap
        for gap in report.evidence_gaps
    ), (
        f"Expected scanner to surface the hard open-findings blocker; "
        f"got evidence_gaps={report.evidence_gaps!r}"
    )
    # The legacy-authority blockers MUST be filtered out (soft class).
    assert not any(
        "legacy_event" in gap or "legacy_artifact_summary" in gap
        for gap in report.evidence_gaps
    ), (
        f"Expected legacy-authority blockers to be filtered out "
        f"(soft class); got evidence_gaps={report.evidence_gaps!r}"
    )
    assert report.is_complete is False


@pytest.mark.asyncio
async def test_scanner_emits_synthetic_marker_when_corpus_missing(
    tmp_path,
) -> None:
    """When the store has no row for the requested ``corpus_id`` the
    scanner emits a synthetic
    ``governance_evidence_gap:missing_corpus:<id>`` marker so the
    report's ``is_complete`` flag fails closed.

    The missing-corpus case is a scanner-detectable evidence gap, NOT
    a typed-surface contract violation that should raise; the scanner
    can be invoked safely even when the corpus has not yet been
    ingested.
    """

    status_text = _all_accepted_status_text()
    journal_text = "# Implementation Journal\n\n## All clean\n"
    journal_path = _write_minimal_journal_and_status(
        tmp_path, journal_text=journal_text, status_text=status_text
    )

    store = InMemoryGovernanceEvidenceStore()
    # Do NOT put any corpus -- the scanner should still return a
    # report with the synthetic marker.

    report = await scan_governance_completeness("missing:99", store, journal_path)

    assert any(
        "missing_corpus:missing:99" in gap
        for gap in report.evidence_gaps
    ), (
        f"Expected synthetic missing_corpus marker; got "
        f"evidence_gaps={report.evidence_gaps!r}"
    )
    assert report.is_complete is False


# ── (e) Typed Pydantic round-trip + out-of-bounds rejection ───────────────


def test_completeness_scan_report_is_typed_pydantic_model() -> None:
    """The report is a Pydantic :class:`BaseModel` subclass with the four
    documented fields per the implementer prompt point 4.

    Out-of-bounds values fail closed at construction per
    ``feedback_no_silent_degradation``.
    """

    # Type check.
    from pydantic import BaseModel

    assert issubclass(CompletenessScanReport, BaseModel)

    # Valid instance.
    report = CompletenessScanReport(
        missing_acceptance=["07"],
        unresolved_findings=["P1-07-A"],
        evidence_gaps=["governance_evidence_gap:open_findings:07:P1-07-A"],
        is_complete=False,
    )
    assert report.missing_acceptance == ["07"]
    assert report.unresolved_findings == ["P1-07-A"]
    assert report.evidence_gaps == [
        "governance_evidence_gap:open_findings:07:P1-07-A"
    ]
    assert report.is_complete is False

    # Out-of-bounds: extra field is rejected (extra='forbid').
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        CompletenessScanReport(
            missing_acceptance=[],
            unresolved_findings=[],
            evidence_gaps=[],
            is_complete=True,
            unknown_field="bogus",  # type: ignore[call-arg]
        )

    # Out-of-bounds: empty string in a list is rejected by the
    # _entries_are_non_empty_strings validator.
    with pytest.raises(ValidationError):
        CompletenessScanReport(
            missing_acceptance=[""],
            unresolved_findings=[],
            evidence_gaps=[],
            is_complete=False,
        )

    # Out-of-bounds: whitespace-only string is rejected.
    with pytest.raises(ValidationError):
        CompletenessScanReport(
            missing_acceptance=[],
            unresolved_findings=["   "],
            evidence_gaps=[],
            is_complete=False,
        )

    # Out-of-bounds: non-string entry is rejected.
    with pytest.raises(ValidationError):
        CompletenessScanReport(
            missing_acceptance=[],
            unresolved_findings=[],
            evidence_gaps=[42],  # type: ignore[list-item]
            is_complete=False,
        )

    # Out-of-bounds: non-bool is_complete is rejected.
    with pytest.raises(ValidationError):
        CompletenessScanReport(
            missing_acceptance=[],
            unresolved_findings=[],
            evidence_gaps=[],
            is_complete="not-a-bool",  # type: ignore[arg-type]
        )


# ── (f) Real-corpus integration ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanner_real_corpus_against_live_status_and_journal() -> None:
    """Per the implementer prompt point 6 (f) -- real-corpus integration.

    Run the scanner against the live ``STATUS.md`` + the live
    ``implementation-journal.md`` at
    ``docs/execution-control-plane/``. Per STATUS.md the Slice 00-12
    bundle is ACCEPTED + the Slice 13 governance phase is ACCEPTED;
    every required Slice 00-12 acceptance marker MUST be present.

    Per the Slice 13A first-sub-slice finalizer P3-A6-1 remediation
    this test was strengthened to also assert
    :attr:`CompletenessScanReport.is_complete` is ``False`` on the
    live corpus, with the explicit reason documented inline: the
    live journal tail contains a class of STRUCTURAL false positives
    that the current scanner's same-line-status-marker heuristic
    cannot filter -- descriptive-text mentions of finding IDs
    embedded in narrative prose, synthetic test-fixture markdown,
    and historical reviewer entries (e.g. ``P1-13b-1`` cited as
    test-fixture text under a 13j journal entry; ``P2-13a-1`` etc.
    mentioned in historical implementer/reviewer narratives without
    an inline ``CLOSED`` / ``FIXED`` / ``RESOLVED`` marker on the
    same line).

    **This is the EXPECTED CURRENT BEHAVIOR** per the Slice 13A
    first-sub-slice finalizer carry
    :data:`P3-13A-1` (12 structural false positives carried with a
    binding-on-future-slices statement). The carry's binding
    statement, recorded in STATUS.md § "Carried-P3 ledger" + the
    journal AFTER entry, requires future Slice 13A sub-slices to
    design downstream consumers to either:

    * (a) Ignore :attr:`is_complete` and consume the
      :attr:`unresolved_findings` / :attr:`evidence_gaps` lists
      directly (so the scanner becomes a presence-tagged report,
      not a hard gate), OR
    * (b) Add journal-section-aware filtering (e.g. skip findings
      cited inside synthetic test-fixture code blocks; skip
      historical "## CLOSED in this iteration" sections; skip the
      "## Carried-P3 ledger" + "## Remaining" sections) to reduce
      the false-positive count toward zero.

    The Slice 13A first sub-slice INTENTIONALLY ships the scanner
    as a conservative over-reporter per the auto-memory
    ``feedback_no_silent_degradation`` rule (better to over-report
    unresolved findings than to silently drop a real blocker). The
    P2-A3-1 finalizer reduced the regex-noise class from 4 false
    positives to 0; the remaining structural class is carried to
    P3-13A-1.

    The real-corpus check pins the missing-acceptance + evidence-gap
    detection on the live corpus; the unresolved-findings detection
    is exercised deterministically by the synthetic test
    :func:`test_scanner_detects_unresolved_p1_p2_findings_in_journal_tail`
    above.
    """

    repo_root = Path(__file__).resolve().parent.parent
    journal_path = (
        repo_root
        / "docs"
        / "execution-control-plane"
        / "implementation-journal.md"
    )
    if not journal_path.exists():
        pytest.skip(
            f"Live implementation journal not present at "
            f"{journal_path}; real-corpus integration test skipped."
        )

    store = InMemoryGovernanceEvidenceStore()
    # Put a clean evidence set so the evidence-gap branch is empty.
    await store.put(_make_minimal_evidence_set(corpus_id="live-corpus:1"))

    report = await scan_governance_completeness(
        "live-corpus:1", store, journal_path
    )

    # The live STATUS.md + journal MUST have every Slice 00-12
    # acceptance marker. Per STATUS.md the Slice 00-12 bundle is
    # ACCEPTED.
    assert report.missing_acceptance == [], (
        f"Expected zero missing Slice 00-12 acceptance markers on the "
        f"live corpus; got {report.missing_acceptance!r}. Per STATUS.md "
        f"the Slice 00-12 bundle is ACCEPTED."
    )
    # Evidence gaps from the synthetic corpus are empty (the stored
    # evidence set has no blockers).
    assert report.evidence_gaps == [], (
        f"Expected zero evidence gaps from the clean synthetic corpus; "
        f"got {report.evidence_gaps!r}"
    )
    # Slice 13A first-sub-slice finalizer P3-A6-1 strengthening:
    # pin the EXPECTED CURRENT BEHAVIOR that is_complete is False on
    # the live corpus because of the structural-false-positive class
    # carried as P3-13A-1. This assertion documents the carry as
    # part of the test surface; a future sub-slice that closes
    # P3-13A-1 via (a) journal-section-aware filtering will need to
    # update this assertion (the structural false positives
    # disappear) OR (b) refactoring downstream consumers to ignore
    # ``is_complete`` will leave this assertion intact (the scanner
    # remains conservative but consumers stop gating on it).
    assert report.is_complete is False, (
        f"Slice 13A first-sub-slice carry P3-13A-1 invariant: the live "
        f"corpus is_complete is EXPECTED to be False because of the "
        f"structural class of false positives (descriptive-text "
        f"mentions of finding IDs embedded in narrative prose, "
        f"synthetic test-fixture markdown, and historical entries) "
        f"that the same-line-status-marker heuristic cannot filter. "
        f"Got is_complete={report.is_complete!r}; "
        f"unresolved_findings={report.unresolved_findings!r}. If this "
        f"assertion is flipping to True, the structural class has "
        f"been reduced to zero -- update STATUS.md § 'Carried-P3 "
        f"ledger' P3-13A-1 closure + remove this assertion."
    )
    # Additional sanity: the unresolved-findings list MUST be
    # non-empty (the structural class produces several entries on
    # the live corpus). A zero-entry list would mean the scanner
    # silently degraded -- fail closed per
    # feedback_no_silent_degradation.
    assert len(report.unresolved_findings) > 0, (
        f"P3-13A-1 carry invariant: the live corpus MUST surface "
        f"at least one unresolved finding from the structural-class "
        f"false positives (descriptive-text mentions of canonical "
        f"P[12]-<slice>-<digit> IDs without same-line status "
        f"markers). A zero-count would indicate the scanner has "
        f"silently degraded. Got "
        f"unresolved_findings={report.unresolved_findings!r}."
    )


# ── (g) Typed-input validation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_scanner_rejects_none_corpus_id_with_typeerror() -> None:
    """Per the implementer prompt point 6 (g) +
    ``feedback_no_silent_degradation``.

    A ``None`` corpus_id (or any non-string) MUST raise typed
    :class:`TypeError` at the API entry boundary. Mirrors the
    :meth:`InMemoryGovernanceEvidenceStore.put` precedent at
    ``store.py:340-360``.

    The scanner is ``async def`` per the 13i finalizer (the store
    surface is uniformly async); the typed-input TypeError fires
    synchronously the moment the returned coroutine is awaited (a
    ``raise`` before any ``await`` propagates immediately on
    coroutine drive).
    """

    store = InMemoryGovernanceEvidenceStore()
    # Use a path that doesn't need to exist -- the TypeError fires
    # BEFORE the file check.
    bogus_path = Path("/nonexistent/journal.md")

    with pytest.raises(TypeError):
        await scan_governance_completeness(None, store, bogus_path)  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        await scan_governance_completeness(42, store, bogus_path)  # type: ignore[arg-type]

    # Empty / whitespace-only corpus_id rejected.
    with pytest.raises(TypeError):
        await scan_governance_completeness("", store, bogus_path)

    with pytest.raises(TypeError):
        await scan_governance_completeness("   ", store, bogus_path)


@pytest.mark.asyncio
async def test_scanner_rejects_non_store_argument_with_typeerror(
    tmp_path,
) -> None:
    """A non-:class:`GovernanceEvidenceStore` ``store`` argument MUST
    raise typed :class:`TypeError` at the API entry boundary."""

    journal_path = _write_minimal_journal_and_status(
        tmp_path,
        journal_text="# x\n",
        status_text="# x\n",
    )

    with pytest.raises(TypeError):
        await scan_governance_completeness(
            "corpus:1", "not-a-store", journal_path  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError):
        await scan_governance_completeness(
            "corpus:1", None, journal_path  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_scanner_rejects_non_path_journal_argument_with_typeerror() -> None:
    """A non-:class:`~pathlib.Path` ``journal_path`` MUST raise typed
    :class:`TypeError` at the API entry boundary."""

    store = InMemoryGovernanceEvidenceStore()

    with pytest.raises(TypeError):
        await scan_governance_completeness(
            "corpus:1", store, "/not/a/path/string"  # type: ignore[arg-type]
        )

    with pytest.raises(TypeError):
        await scan_governance_completeness(
            "corpus:1", store, None  # type: ignore[arg-type]
        )


@pytest.mark.asyncio
async def test_scanner_raises_file_not_found_on_missing_journal_path(
    tmp_path,
) -> None:
    """Per ``feedback_no_silent_degradation`` a missing
    ``journal_path`` raises typed :class:`FileNotFoundError` (the
    scanner cannot decide completeness without the journal anchor
    surface; silently degrading to ``is_complete=True`` would mask a
    governance evidence gap).
    """

    store = InMemoryGovernanceEvidenceStore()
    missing_path = tmp_path / "does-not-exist.md"

    with pytest.raises(FileNotFoundError):
        await scan_governance_completeness("corpus:1", store, missing_path)


@pytest.mark.asyncio
async def test_scanner_raises_file_not_found_on_missing_status_md(
    tmp_path,
) -> None:
    """STATUS.md must exist next to the journal; a journal-without-
    STATUS-md pair raises typed :class:`FileNotFoundError`.
    """

    journal_path = tmp_path / "implementation-journal.md"
    journal_path.write_text("# x\n", encoding="utf-8")
    # Intentionally do NOT create STATUS.md.

    store = InMemoryGovernanceEvidenceStore()
    with pytest.raises(FileNotFoundError):
        await scan_governance_completeness("corpus:1", store, journal_path)


# ── (h) Pydantic JSON round-trip ──────────────────────────────────────────


def test_completeness_scan_report_round_trips_via_pydantic_json() -> None:
    """Per the implementer prompt point 6 (h) -- the report MUST round-
    trip via Pydantic ``model_dump_json`` -> ``model_validate_json``.

    The round-trip preserves every field byte-for-byte, so a future
    storage layer can serialise the report to JSON + reload it
    without loss.
    """

    original = CompletenessScanReport(
        missing_acceptance=["00", "07", "12"],
        unresolved_findings=["P1-07-A", "P2-13h-1"],
        evidence_gaps=[
            "governance_evidence_gap:open_findings:07:P1-07-A",
            "governance_evidence_gap:missing_corpus:bogus:99",
        ],
        is_complete=False,
    )

    serialised = original.model_dump_json()
    # Sanity: the serialised form is parseable JSON.
    parsed = json.loads(serialised)
    assert isinstance(parsed, dict)
    assert set(parsed.keys()) == {
        "missing_acceptance",
        "unresolved_findings",
        "evidence_gaps",
        "is_complete",
    }

    # Round-trip equality.
    restored = CompletenessScanReport.model_validate_json(serialised)
    assert restored == original
    assert restored.missing_acceptance == original.missing_acceptance
    assert restored.unresolved_findings == original.unresolved_findings
    assert restored.evidence_gaps == original.evidence_gaps
    assert restored.is_complete == original.is_complete


# ── Additional defensive coverage ─────────────────────────────────────────


def test_scanner_module_all_lists_exactly_documented_surface() -> None:
    """``completeness_scanner.__all__`` is exactly the documented
    Slice 13A first sub-slice surface (2 symbols)."""

    assert sorted(scanner_module.__all__) == sorted(
        ["CompletenessScanReport", "scan_governance_completeness"]
    )


def test_p1_p2_finding_id_regex_recognises_canonical_forms() -> None:
    """Defensive unit test: the scanner's internal
    :data:`_P1_P2_FINDING_ID_RE` (the alias of the shared
    :data:`models.FINDING_ID_REGEX` per the Slice 13A first-sub-slice
    finalizer P2-A3-1 remediation) recognises the canonical
    journal-parser shapes per the convention at
    ``journal_parser.py:282-284``.

    The canonical shape is ``P[123]-<slice>-<n>`` where ``<slice>`` is
    a 1-to-3-digit-plus-optional-lowercase-letter form (e.g. ``13b``,
    ``08e-3a``, ``11d``) and ``<n>`` is a trailing integer index.
    Forms missing the trailing ``-<digit>`` segment or carrying
    uppercase letters in the scope segment are intentionally rejected
    so the scanner does not false-fire on descriptive prose like
    ``P2-RISK`` or ``P1-finding``.

    Note: the regex matches P[123]; the scanner filters to P[12] at
    the match-result level via the ``_BLOCKING_SEVERITIES`` frozenset
    (see ``completeness_scanner._detect_unresolved_findings``)."""

    pattern = scanner_module._P1_P2_FINDING_ID_RE

    # Standard canonical journal-parser shapes with trailing -<digit>.
    assert pattern.search("P1-07-1 finding")
    assert pattern.search("P2-13h-1 was fixed")
    assert pattern.search("P3-13a-3 carried")  # P3 matches regex; filtered at consumer level.
    assert pattern.search("P1-08e-3a-1 multi-segment slice")
    # Plain "P1" without scope is NOT a finding ID per the
    # journal_parser convention (the regex requires ``<scope>-<digit>``).
    assert pattern.search("0 P1 / 0 P2 in slice") is None
    # Forms missing the trailing -<digit> are NOT canonical -- the
    # journal-parser shape requires the index segment.
    assert pattern.search("P1-07-A") is None  # 'A' is not a digit.
    assert pattern.search("P2-RISK") is None  # No trailing digit + uppercase scope.
    assert pattern.search("P1-1") is None  # Slice must be digit-prefix; '1' alone is the trailing index expected.


def test_finding_id_regex_rejects_known_pure_regex_noise_false_positives() -> None:
    """Slice 13A first-sub-slice finalizer P2-A3-1 closure -- defensive
    regex-layer assertion that the scanner REJECTS the four classes of
    pure-regex-noise false positives that the prior scanner-local laxer
    regex admitted on the live corpus.

    The prior scanner-local laxer regex was
    ``r"\\b(?P<severity>P[12])-(?P<scope>[A-Za-z0-9_]+)(?:-(?P<seq>[A-Za-z0-9_]+))?\\b"``
    which matched any ``P[12]-<alphanumeric>`` shape. This admitted
    four classes of pure-regex-noise false positives:

    1. ``P1-P2`` -- from phrases like ``"0 P1 / 0 P2 in slice-end review"``.
    2. ``P1-RISK`` -- from historical reviewer prose mentioning a risk class.
    3. ``P1-finding`` -- from descriptive sentences "the P1-finding above ...".
    4. ``P2-finding`` -- same descriptive form for P2.

    Per the P2-A3-1 finalizer remediation the scanner now consumes the
    canonical journal-parser :data:`models.FINDING_ID_REGEX` which
    requires the trailing ``-<digit>`` index segment AND lowercase-only
    letters in the scope segment; all four noise classes are
    structurally rejected at the regex layer.

    This test pins the rejection at the regex layer (without going
    through the full scanner) so a future drift in the shared regex
    that re-admitted any of the four noise classes would fail this
    test loudly. The single-source-of-truth invariant is reinforced
    by also asserting the scanner's internal alias points at the
    shared regex from :mod:`.models` (identity, not just equality).
    """

    pattern = scanner_module._P1_P2_FINDING_ID_RE

    # Identity check: the scanner's internal alias IS the shared
    # ``models.FINDING_ID_REGEX`` (not a copy). A future drift that
    # re-introduced a scanner-local regex would fail this identity
    # assertion + the rejection assertions below would also break.
    from iriai_build_v2.workflows.develop.governance.models import (
        FINDING_ID_REGEX as _SHARED_REGEX,
    )
    assert pattern is _SHARED_REGEX, (
        "Slice 13A first-sub-slice finalizer P2-A3-1 invariant: the "
        "scanner's internal _P1_P2_FINDING_ID_RE MUST be the IDENTICAL "
        "shared regex from models.FINDING_ID_REGEX (single source of "
        "truth). A scanner-local copy would re-introduce the drift "
        "risk P2-A3-1 closed."
    )

    # Rejection 1: ``P1-P2`` -- from phrases like "0 P1 / 0 P2".
    assert pattern.search("0 P1 / 0 P2 in slice-end review") is None, (
        "P1-P2 must NOT be matched -- it's the noise from '0 P1 / 0 P2' "
        "phrases the scanner false-fired on prior to P2-A3-1 closure."
    )
    # Also check the substring in isolation -- defence in depth.
    assert pattern.search("P1-P2") is None
    assert pattern.search("P2-P1") is None

    # Rejection 2: ``P1-RISK`` / ``P2-RISK`` -- from historical
    # reviewer prose. Uppercase scope segment rejected at the regex
    # layer.
    assert pattern.search("P1-RISK was raised") is None
    assert pattern.search("P2-RISK was raised") is None
    assert pattern.search("a P1-RISK class blocker") is None

    # Rejection 3: ``P1-finding`` -- from descriptive sentences. The
    # canonical regex requires the slice segment to start with a digit
    # and end with a trailing -<digit> index, so ``-finding`` (no
    # digits) is structurally rejected.
    assert pattern.search("the P1-finding above") is None
    assert pattern.search("a P1-finding class") is None

    # Rejection 4: ``P2-finding`` -- same descriptive form for P2.
    assert pattern.search("the P2-finding above") is None
    assert pattern.search("a P2-finding class") is None

    # Sanity: real canonical finding IDs ARE still matched -- the
    # rejection is structural, not blanket.
    assert pattern.search("P1-13b-1") is not None
    assert pattern.search("P2-13h-1") is not None
    assert pattern.search("P1-08e-3a-1") is not None


def test_acceptance_marker_regex_recognises_canonical_forms() -> None:
    """Defensive unit test: the scanner's internal
    :data:`_ACCEPTANCE_MARKER_RE` recognises the canonical journal +
    STATUS.md acceptance shapes."""

    pattern = scanner_module._ACCEPTANCE_MARKER_RE

    # Journal heading form.
    match = pattern.search("## Slice 07 ACCEPTED (2026-05-21)")
    assert match is not None and match.group("slice_id") == "07"
    # Date-prefixed journal heading form.
    match = pattern.search("## 2026-05-22 -- Slice 10 ACCEPTED")
    assert match is not None and match.group("slice_id") == "10"
    # STATUS.md bullet form.
    match = pattern.search("- Slice 08 (Durable Merge Queue): **ACCEPTED.**")
    assert match is not None and match.group("slice_id") == "08"
    # Lowercase pre-Slice-07 form.
    match = pattern.search("Slice 00 accepted")
    assert match is not None and match.group("slice_id") == "00"
