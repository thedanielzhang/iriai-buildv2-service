"""Slice 13A first sub-slice -- governance completeness scanner.

This module owns the Slice 13A first sub-slice enforcement scanner
deliverable per STATUS.md § "Next safe action" point 4:

> Add the enforcement scanner surface under
> ``src/iriai_build_v2/workflows/develop/governance/completeness_scanner.py``
> (NEW module). The scanner detects:
> (a) missing Slice 00-12 acceptance markers (cross-references
> ``STATUS.md`` + the JSONL ``slice_end_finalizer_after`` rows);
> (b) unresolved P1/P2 findings across the journal tail and active
> restart pointer (greps ``implementation-journal.md`` for the P1/P2
> forms emitted by reviewer subagents and ``STATUS.md`` for active
> reassessment finding ids);
> (c) any ``governance_evidence_gap`` blocker present in a
> :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceSet`
> consumed by a downstream slice (the 13A invariant guard).
>
> Surface: pure-typed
> :func:`scan_governance_completeness(corpus_id, store, journal_path) ->
> CompletenessScanReport` free function. NO executor wiring; no
> consumption as execution authority outside Slice 13A's own acceptance
> tests.

**Doc citations.**

* doc-13:209-210 -- the canonical ``governance_evidence_gap`` blocker
  finding name ("record a ``governance_evidence_gap`` finding") that
  the 13e digester now emits per the Slice 13A first sub-slice
  P3-13e-3 closure.
* doc-13:247-254 -- the 5-bullet acceptance criteria block; criterion
  (e) verbatim "Missing Slice 00-12 acceptance or unresolved P1/P2
  findings blocks governance acceptance" is the scanner's central
  invariant.
* doc-13:217 -- the doc-13 fail-closed projection ("downstream
  metrics, findings, reports, acceptance checks, and recommendations
  can consume only the complete subset they can prove by exact refs;
  otherwise they must fail closed").
* doc-13a:1-389 -- the Slice 13A invariant doc; the scanner is the
  cross-cutting enforcement that prevents lossy summaries from
  reaching dispatch / verify / merge / checkpoint / route / scheduler
  / policy code paths.
* doc-13a:24 + 109-118 -- the verbatim invariant ("Lossy summaries
  and previews are display-only") + the blocking-deviation list (one
  of which is "A verifier, gate, router, merge queue, scheduler,
  supervisor classifier, or governance recommender acts on a
  truncated list without fetching exact pages or marking the decision
  degraded/unknown").

**Fail-closed semantics.** Per the auto-memory
``feedback_no_silent_degradation`` rule every scanner code path
fails closed:

* Typed-input validation at the entry boundary raises a typed
  :class:`TypeError` on ``None`` / wrong-shape arguments (mirrors the
  13g ``InMemoryGovernanceEvidenceStore.put`` precedent).
* Missing ``journal_path`` raises a typed :class:`FileNotFoundError`
  (the scanner cannot decide completeness without the journal anchor
  surface; silently degrading to "is_complete=True" would mask a
  governance evidence gap).
* Missing corpus row in the store raises nothing -- the scanner
  reports ``evidence_gaps=["governance_evidence_gap:missing_corpus:<id>"]``
  + ``is_complete=False`` (the corpus-not-found case is a
  scanner-detectable evidence gap, not a typed-surface contract
  violation).

**Bounded reads.** Per the governance prompt § "Bounded reads" the
scanner reads only the journal tail (the last N bytes / lines) and
the STATUS.md path. Neither read triggers an unbounded artifact body
hydration; both files are bounded by repository size invariants
(STATUS.md is overwritten in-place per the cheap O(1) restart pointer
discipline; the journal grows append-only but the tail-window read is
bounded by ``_JOURNAL_TAIL_BYTES``).

**Stdlib + governance siblings + Pydantic only.** Per the implementer
prompt § "Non-negotiables" the scanner uses only:

* :mod:`re` + :mod:`pathlib` from the stdlib.
* The sibling ``.models`` + ``.store`` for the typed-row contract.
* Pydantic v2 for the typed :class:`CompletenessScanReport` shape.

NO new dependency; NO importation of execution-control / supervisor /
dashboard surfaces.

**Slice 13A scope discipline.** Per the implementer prompt the
scanner is NOT wired into any execution-authority consumer outside
Slice 13A's own acceptance tests; it is a pure-typed read-only
projection that future 13A sub-slices (15-19) may consume as
governance authority once the cross-cutting invariant has settled.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import FINDING_ID_REGEX, GovernanceEvidenceSet
from .store import GovernanceEvidenceStore


__all__ = [
    "CompletenessScanReport",
    "scan_governance_completeness",
]


# --- Tunables ---------------------------------------------------------------
#
# All tunables are module-level constants for static-analysis
# discoverability + test reproducibility. A future 13A sub-slice may
# expose them via a typed Pydantic configuration model; today they are
# fixed at sensible defaults that the real-corpus integration test
# exercises.


_JOURNAL_TAIL_BYTES: int = 512_000
"""The journal-tail byte window the scanner reads from the end of the
journal markdown.

The implementation journal is append-only + grows steadily; reading
the entire file on every scan would be unbounded. The tail window is
sized to comfortably include the last ~3-5 slice acceptance windows
(13a-13n is ~7000 lines; STATUS.md historical reference points to
slice-end finalizer rows that span ~50-200 lines each). 512KB is a
conservative cap that captures the post-Slice-12 governance phase
content + the Slice 13A first sub-slice's own journal entries.
"""


# Per doc-13:253-254 + the journal_parser convention at
# ``src/iriai_build_v2/workflows/develop/governance/journal_parser.py:282-284``
# finding IDs follow the canonical ``P[123]-<slice>-<n>`` shape (e.g.
# ``P1-07-A``, ``P2-13h-1``, ``P3-V1-1``, ``P1-08e-3a-1``). Only P1 + P2
# are blocking per criterion (e); P3 is maintainability/clarity per the
# reviewer severities at ``IMPLEMENTATION_PROMPT_GOVERNANCE.md:206-216``.
#
# Per the Slice 13A first-sub-slice reviewer P2-A3-1 finalizer
# remediation, the canonical regex now lives in
# :data:`models.FINDING_ID_REGEX` as the single source of truth shared
# with ``journal_parser`` + ``decision_log_parser``. The scanner-local
# alias :data:`_P1_P2_FINDING_ID_RE` preserves the prior in-module
# readability + import-time discoverability; the P[12] filtering happens
# at the match-result level (the shared regex matches P[123]; the
# scanner inspects the captured ``finding_id`` group to filter out P3
# severity at consumption).
#
# Rationale for the single-source-of-truth refactor: the prior
# scanner-local laxer regex
# (``r"\b(?P<severity>P[12])-(?P<scope>[A-Za-z0-9_]+)(?:-(?P<seq>[A-Za-z0-9_]+))?\b"``)
# admitted four classes of pure-regex-noise false positives on the live
# corpus: ``P1-P2`` (from phrases like "0 P1 / 0 P2"), ``P1-RISK``
# (from historical reviewer prose), ``P1-finding`` / ``P2-finding``
# (from descriptive sentences). The canonical journal_parser shape
# requires a trailing ``-N`` index segment which structurally rejects
# all four noise classes. The defensive regex test at
# ``tests/test_governance_completeness_scanner.py::test_finding_id_regex_rejects_known_pure_regex_noise_false_positives``
# pins this rejection.
_P1_P2_FINDING_ID_RE: re.Pattern[str] = FINDING_ID_REGEX
"""Module-internal alias of :data:`models.FINDING_ID_REGEX` preserved
for in-module readability + import-time discoverability. The shared
regex matches P[123]; this scanner filters consumed matches to P[12]
only at the match-result level (criterion (e) per doc-13:253-254 only
treats P1/P2 as blocking; P3 is maintainability/clarity per
``IMPLEMENTATION_PROMPT_GOVERNANCE.md:206-216`` and explicitly NOT
blocking)."""


_REASSESSMENT_FINDING_ID_RE: re.Pattern[str] = re.compile(
    r"\b(?P<finding_id>(?P<slice_id>\d{1,3}[A-Z])-(?P<severity>P[123])-\d{3})\b"
)
"""Scanner-local regex for active reassessment ids such as
``19A-P1-003``.

The shared ``FINDING_ID_REGEX`` remains the single source of truth for
canonical journal/decision-log ids shaped like ``P1-13a-1``. Slice 19A
reopened governance acceptance with a source-doc-local id shape whose
severity appears in the middle. The scanner consumes that shape only
from ``STATUS.md`` so active reassessment blockers fail closed without
re-admitting historical journal-tail false positives.
"""


# The 2 severity codes the scanner treats as blocking per
# doc-13:253-254 + ``IMPLEMENTATION_PROMPT_GOVERNANCE.md:206-216``.
# P3 findings (maintainability / clarity) are explicitly NOT blocking
# and are filtered out of the scanner's ``unresolved_findings`` list at
# the match-result level.
_BLOCKING_SEVERITIES: frozenset[str] = frozenset({"P1", "P2"})


# Per the journal convention (see e.g. journal entries for Slices
# 13c-13n) a finding ID is "closed" / "resolved" / "fixed" / "applied"
# when one of these status markers appears in its immediate vicinity
# (same line, prefix, or suffix). The scanner treats any P1/P2 finding
# id appearing UNDER one of these status markers in the journal tail as
# RESOLVED. This is a conservative heuristic: per the
# ``feedback_no_silent_degradation`` rule a finding without a status
# marker is reported as unresolved.
#
# The markers are matched as WHOLE WORDS (regex word-boundaries) to
# avoid false positives like "RESOLVED" matching inside "UNRESOLVED".
# The journal convention is "P1-07-A CLOSED" / "P2-07-B FIXED" / etc.
_RESOLVED_STATUS_MARKERS: frozenset[str] = frozenset(
    {
        "CLOSED",
        "RESOLVED",
        "FIXED",
        "APPLIED",
        "REMEDIATED",
        "DISMISSED",
    }
)


# Compiled word-boundary regex for status-marker detection. The
# alternation is sorted longest-first so the regex engine prefers the
# longest match (e.g. "REMEDIATED" wins over the empty alternative)
# even though all current markers are single words.
_RESOLVED_STATUS_MARKER_RE: re.Pattern[str] = re.compile(
    r"\b(?:" + "|".join(sorted(_RESOLVED_STATUS_MARKERS, key=len, reverse=True)) + r")\b"
)


# Per STATUS.md the expected Slice 00-12 acceptance range. These are
# the 13 slices the governance phase requires accepted per
# doc-13:253-254. The scanner cross-references the journal tail +
# STATUS.md + the corpus-id store to confirm each one has an
# acceptance marker.
_REQUIRED_SLICE_00_12_IDS: tuple[str, ...] = (
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


# Per the journal convention an acceptance marker is one of:
#
#   ``## Slice <id> ACCEPTED``
#   ``## YYYY-MM-DD — Slice <id> ACCEPTED``
#   ``Slice <id> accepted`` (lowercase variant for the older
#                            pre-Slice-07 entries)
#   ``Slice <id>: **ACCEPTED.**`` (STATUS.md bullet form)
#
# The scanner accepts any of these shapes (a permissive regex over the
# union); the conservative interpretation is that a slice WITHOUT any
# of these markers in the journal tail OR STATUS.md is "missing
# acceptance" per doc-13:253-254.
_ACCEPTANCE_MARKER_RE: re.Pattern[str] = re.compile(
    r"Slice\s+(?P<slice_id>[0-9]{1,2})\s*"
    r"(?:[A-Z()][A-Za-z0-9()\s]*)?"
    r"[\s:.*]*\s*"
    r"(?:ACCEPTED|accepted)\b"
)


# Per STATUS.md the canonical range-acceptance form is
# ``Slices 00–06: **ACCEPTED**`` (en-dash or hyphen) or ``Slice 00–12
# ... ACCEPTED``. The range form bundles a contiguous span of slice IDs
# under a single acceptance marker; the scanner expands the range to
# the individual slice IDs covered when matching against the required
# set. Real STATUS.md examples:
#
#   - "Slices 00–06: **ACCEPTED** (prior sessions)."
#   - "The Slice 00-12 acceptance window remains closed + ACCEPTED."
#
# The regex supports both en-dash (U+2013) + ASCII hyphen separators
# and both ``Slice`` / ``Slices`` forms. The acceptance marker must
# appear within ~120 chars of the range so the regex does not
# over-match unrelated mentions.
_RANGE_ACCEPTANCE_MARKER_RE: re.Pattern[str] = re.compile(
    r"Slices?\s+(?P<start>[0-9]{1,2})\s*[-–]+\s*(?P<end>[0-9]{1,2})"
    r"[\s:.*A-Za-z()]{0,200}?"
    r"(?:ACCEPTED|accepted)\b"
)


# --- Typed report model -----------------------------------------------------


class CompletenessScanReport(BaseModel):
    """Typed report from :func:`scan_governance_completeness`.

    Per the implementer prompt point 4 + 6 the scanner emits a
    Pydantic-typed report with four fields:

    * :attr:`missing_acceptance` -- list of Slice 00-12 IDs that lack
      an acceptance marker in either STATUS.md or the journal tail.
      Empty when every required slice has an acceptance marker.
    * :attr:`unresolved_findings` -- list of P1 / P2 finding IDs from
      the journal tail that lack a CLOSED / RESOLVED / FIXED / APPLIED
      status marker nearby. Empty when every P1/P2 finding has a
      status marker.
    * :attr:`evidence_gaps` -- list of ``governance_evidence_gap:*``
      blocker strings from the consumed :class:`GovernanceEvidenceSet`,
      filtered to NON-LEGACY classes (the legacy-authority class is
      informational per the 13e quality projection discipline; only
      hard non-legacy blockers count as evidence gaps per
      doc-13:253-254 + doc-13:217). Also includes the synthetic
      ``governance_evidence_gap:missing_corpus:<id>`` marker when the
      store has no row for the requested ``corpus_id``.
    * :attr:`is_complete` -- ``True`` iff all three lists above are
      empty. The Slice 13A invariant gate fires fail-closed when this
      is ``False``.

    Per the auto-memory ``feedback_no_silent_degradation`` rule every
    field is strictly typed (``list[str]`` / ``bool``); the model
    config is ``extra="forbid"`` so unknown fields fail closed at
    construction.
    """

    # extra='forbid' aligns with the sibling executor models at
    # workflows/develop/execution/verification.py:74 /
    # failure_router.py:576 -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    missing_acceptance: list[str] = Field(default_factory=list)
    """Slice IDs (e.g. ``"07"``, ``"12"``) lacking an acceptance
    marker in STATUS.md or the journal tail. Empty list when every
    required slice is accepted."""

    unresolved_findings: list[str] = Field(default_factory=list)
    """P1 / P2 finding IDs from the journal tail that lack a CLOSED /
    RESOLVED / FIXED / APPLIED status marker nearby. Empty when every
    P1/P2 finding has a status marker."""

    evidence_gaps: list[str] = Field(default_factory=list)
    """``governance_evidence_gap:*`` blocker strings (non-legacy class
    only) from the consumed :class:`GovernanceEvidenceSet`, plus the
    synthetic ``governance_evidence_gap:missing_corpus:<id>`` marker
    when the store has no row for the requested corpus."""

    is_complete: bool
    """``True`` iff :attr:`missing_acceptance` +
    :attr:`unresolved_findings` + :attr:`evidence_gaps` are all empty.
    The Slice 13A invariant gate fires fail-closed when this is
    ``False``."""

    @field_validator("missing_acceptance", "unresolved_findings", "evidence_gaps")
    @classmethod
    def _entries_are_non_empty_strings(cls, value: list[str]) -> list[str]:
        # Per feedback_no_silent_degradation: a list with empty / non-
        # string entries is malformed input. Reject at the typed
        # surface.
        for entry in value:
            if not isinstance(entry, str):
                raise ValueError(
                    f"CompletenessScanReport entries MUST be non-empty "
                    f"strings; got {entry!r} (type "
                    f"{type(entry).__name__!r})."
                )
            if not entry or not entry.strip():
                raise ValueError(
                    "CompletenessScanReport entries MUST be non-empty + "
                    "non-whitespace strings; got "
                    f"{entry!r}."
                )
        return value


# --- STATUS.md cross-reference ---------------------------------------------


def _project_root_for(journal_path: Path) -> Path:
    """Resolve the project root containing the journal path.

    The journal at
    ``docs/execution-control-plane/implementation-journal.md`` is
    rooted under the project's ``docs/execution-control-plane/``
    directory; the project root is two parents up. The scanner uses
    the project root to locate STATUS.md (which lives next to the
    journal) without requiring a separate ``status_path`` parameter.

    :param journal_path: an absolute :class:`~pathlib.Path` to the
        ``implementation-journal.md`` file. Per the typed-input
        validation in :func:`scan_governance_completeness` the path
        is guaranteed to be a :class:`~pathlib.Path` instance.
    :returns: the directory containing the journal (the
        ``docs/execution-control-plane/`` directory).
    """

    return journal_path.parent


def _read_journal_tail(journal_path: Path) -> str:
    """Read the last :data:`_JOURNAL_TAIL_BYTES` of the journal markdown.

    The implementation journal is append-only + grows steadily;
    reading the entire file on every scan would be unbounded. The
    tail window is sized to comfortably include the last ~3-5 slice
    acceptance windows.

    :raises FileNotFoundError: when ``journal_path`` does not exist.
        Per ``feedback_no_silent_degradation`` the scanner cannot
        decide completeness without the journal anchor surface;
        silently degrading to "is_complete=True" would mask a
        governance evidence gap.
    """

    if not journal_path.exists():
        raise FileNotFoundError(
            f"completeness_scanner: journal_path {journal_path!r} does "
            f"not exist. The scanner requires the implementation "
            f"journal as the canonical source for slice acceptance + "
            f"finding-status anchors per doc-13:253-254 + "
            f"feedback_no_silent_degradation."
        )

    journal_bytes = journal_path.stat().st_size
    with journal_path.open("rb") as handle:
        if journal_bytes > _JOURNAL_TAIL_BYTES:
            handle.seek(journal_bytes - _JOURNAL_TAIL_BYTES)
        return handle.read().decode("utf-8", errors="replace")


def _read_status_md(journal_path: Path) -> str:
    """Read STATUS.md from the same directory as the journal.

    STATUS.md is the cheap O(1) restart pointer per
    ``docs/execution-control-plane/STATUS.md:1-5`` ("This file is
    overwritten at the end of every loop iteration"). The scanner
    reads it as the canonical source for the
    Slice 00-12 ACCEPTED ledger. Missing STATUS.md is a hard
    fail-closed: the scanner cannot decide acceptance completeness
    without it.

    :raises FileNotFoundError: when ``STATUS.md`` does not exist next
        to the journal.
    """

    status_path = _project_root_for(journal_path) / "STATUS.md"
    if not status_path.exists():
        raise FileNotFoundError(
            f"completeness_scanner: STATUS.md not found at "
            f"{status_path!r}. The scanner requires STATUS.md as the "
            f"canonical source for the Slice 00-12 ACCEPTED ledger per "
            f"feedback_no_silent_degradation."
        )
    return status_path.read_text(encoding="utf-8")


# --- Missing-acceptance detection ------------------------------------------


def _detect_missing_acceptance(
    *, status_md_text: str, journal_tail_text: str
) -> list[str]:
    """Detect Slice 00-12 IDs lacking an acceptance marker.

    Per doc-13:253-254 + the implementer prompt point 4 (a) the
    scanner cross-references STATUS.md + the journal tail against the
    expected :data:`_REQUIRED_SLICE_00_12_IDS`. A slice is reported as
    "missing acceptance" iff NEITHER STATUS.md NOR the journal tail
    contains a matching acceptance marker for it.

    The acceptance marker regex :data:`_ACCEPTANCE_MARKER_RE` is
    permissive (matches both ``Slice 07 ACCEPTED`` and
    ``Slice 07: **ACCEPTED.**`` shapes); the conservative
    interpretation is that a slice WITHOUT any of these markers in
    either file is missing acceptance.

    :returns: sorted list of slice IDs (zero-padded 2-digit form) that
        lack an acceptance marker. Empty when every required slice is
        accepted.
    """

    # Per-slice acceptance markers (the canonical journal heading + the
    # STATUS.md bullet shapes).
    accepted_per_slice: set[str] = {
        match.group("slice_id").zfill(2)
        for match in _ACCEPTANCE_MARKER_RE.finditer(status_md_text)
    } | {
        match.group("slice_id").zfill(2)
        for match in _ACCEPTANCE_MARKER_RE.finditer(journal_tail_text)
    }

    # Range acceptance markers (the STATUS.md range form "Slices
    # 00–06: ACCEPTED" + "Slice 00–12 ACCEPTED"). Each match expands
    # to every slice ID in the inclusive [start, end] range. Without
    # this expansion the pre-Slice-07 slices (00..06) -- which the
    # current STATUS.md accepts only via the range form -- would be
    # reported as missing.
    range_matches = list(_RANGE_ACCEPTANCE_MARKER_RE.finditer(status_md_text)) + list(
        _RANGE_ACCEPTANCE_MARKER_RE.finditer(journal_tail_text)
    )
    accepted_from_ranges: set[str] = set()
    for match in range_matches:
        try:
            start = int(match.group("start"))
            end = int(match.group("end"))
        except ValueError:
            continue
        if start > end:
            # Malformed -- defensive skip.
            continue
        for slice_int in range(start, end + 1):
            accepted_from_ranges.add(f"{slice_int:02d}")

    accepted = accepted_per_slice | accepted_from_ranges

    missing = [
        slice_id
        for slice_id in _REQUIRED_SLICE_00_12_IDS
        if slice_id not in accepted
    ]
    return sorted(missing)


# --- Unresolved-finding detection -----------------------------------------


def _detect_unresolved_findings(
    *,
    journal_tail_text: str,
    include_reassessment_ids: bool = False,
) -> list[str]:
    """Detect P1 / P2 finding IDs lacking a status marker.

    Per doc-13:253-254 + the implementer prompt point 4 (b) the
    scanner greps the journal tail for the canonical
    ``P[12]-<scope>-<seq>`` finding-ID shape (per the
    journal_parser convention at ``journal_parser.py:282-290``) and
    reports as "unresolved" any finding ID that lacks one of the
    :data:`_RESOLVED_STATUS_MARKERS` within the same line.

    The scanner is INTENTIONALLY CONSERVATIVE: a finding without a
    status marker on its own line is reported as unresolved. This
    matches the ``feedback_no_silent_degradation`` rule + the
    doc-13:253-254 fail-closed posture (better to over-report
    unresolved findings than to silently drop a real blocker).

    :param include_reassessment_ids: when true, also detect active
        reassessment ids shaped like ``19A-P1-003``. This is used for
        ``STATUS.md`` only; historical journal tails keep the original
        canonical-id scan to avoid stale narrative false positives.
    :returns: sorted deduplicated list of P1/P2 finding IDs lacking a
        status marker. Empty when every P1/P2 finding has a status
        marker.
    """

    unresolved: set[str] = set()

    # The scan is line-by-line so the status marker can be checked
    # against the SAME line as the finding ID. A finding ID + status
    # marker on different lines is NOT treated as resolved (the
    # convention is to mention the status on the same line as the
    # finding, e.g. "P2-V2-1 CLOSED" or "P2-13i-1 FIXED").
    for line in journal_tail_text.splitlines():
        line_upper = line.upper()
        # Word-boundary match prevents "RESOLVED" inside "UNRESOLVED"
        # from suppressing the finding.
        if _RESOLVED_STATUS_MARKER_RE.search(line_upper):
            continue

        for match in _P1_P2_FINDING_ID_RE.finditer(line):
            # The shared :data:`models.FINDING_ID_REGEX` matches P[123];
            # the scanner only treats P1/P2 as blocking per
            # doc-13:253-254 criterion (e). The severity is the first
            # two characters of the matched ``finding_id`` group (the
            # shared regex anchors the leading ``P`` + single digit).
            finding_id = match.group("finding_id")
            severity = finding_id[:2]
            if severity not in _BLOCKING_SEVERITIES:
                # P3 findings are maintainability/clarity per
                # ``IMPLEMENTATION_PROMPT_GOVERNANCE.md:206-216`` and
                # explicitly NOT blocking. Filter at the match-result
                # level.
                continue
            unresolved.add(finding_id)

        if include_reassessment_ids:
            for match in _REASSESSMENT_FINDING_ID_RE.finditer(line):
                severity = match.group("severity")
                if severity not in _BLOCKING_SEVERITIES:
                    continue
                unresolved.add(match.group("finding_id"))

    return sorted(unresolved)


# --- Evidence-gap detection ------------------------------------------------


def _detect_evidence_gaps(
    evidence_set: GovernanceEvidenceSet | None, *, corpus_id: str
) -> list[str]:
    """Detect non-legacy ``governance_evidence_gap:*`` blockers.

    Per the implementer prompt point 4 (c) the scanner inspects the
    consumed :class:`GovernanceEvidenceSet` for any
    ``governance_evidence_gap`` blocker (per doc-13:209-210 verbatim
    + the Slice 13A first sub-slice P3-13e-3 closure). Per the 13e
    digester's blocker class distinction
    (:func:`evidence_set._is_non_legacy_gap_blocker`) the legacy-
    authority class is informational and does NOT count as a hard
    evidence gap; only non-legacy classes (currently
    ``open_findings``; future scanner-driven classes) are reported.

    When the store has no row for ``corpus_id`` the scanner emits a
    synthetic ``governance_evidence_gap:missing_corpus:<id>`` marker
    so the report's :attr:`is_complete` flag fails closed (the
    missing-corpus case is a scanner-detectable evidence gap, not a
    typed-surface contract violation that should raise).

    :returns: sorted deduplicated list of evidence-gap blocker
        strings. Empty when the evidence set has zero non-legacy
        ``governance_evidence_gap`` blockers + the corpus exists.
    """

    if evidence_set is None:
        return [f"governance_evidence_gap:missing_corpus:{corpus_id}"]

    gaps: list[str] = []
    seen: set[str] = set()
    for blocker in evidence_set.blockers:
        if not isinstance(blocker, str):
            # Defence in depth: a non-string blocker is a typed-
            # surface contract violation (the 13a Pydantic
            # ``blockers: list[str]`` field rejects non-str at
            # construction) but the scanner is robust against
            # malformed in-flight rows.
            continue
        if not blocker.startswith("governance_evidence_gap:"):
            continue
        # Apply the legacy-class filter mirroring
        # evidence_set._is_non_legacy_gap_blocker without taking a
        # private import (the public canonical form is documented
        # verbatim in the evidence_set module docstring).
        after_prefix = blocker[len("governance_evidence_gap:"):]
        if ":" not in after_prefix:
            # Malformed -- defensive skip.
            continue
        subclass = after_prefix.split(":", 1)[0]
        # The 2 legacy authorities per doc-13:91-101.
        if subclass in ("legacy_event", "legacy_artifact_summary"):
            continue
        if blocker not in seen:
            seen.add(blocker)
            gaps.append(blocker)
    return sorted(gaps)


# --- Public surface ---------------------------------------------------------


async def scan_governance_completeness(
    corpus_id: str,
    store: GovernanceEvidenceStore,
    journal_path: Path,
) -> CompletenessScanReport:
    """Scan governance completeness per doc-13:253-254 acceptance
    criterion (e).

    Per STATUS.md § "Next safe action" point 4 the scanner emits a
    typed :class:`CompletenessScanReport` covering three orthogonal
    detection paths:

    1. **Missing Slice 00-12 acceptance markers** -- cross-references
       STATUS.md + the journal tail against the expected
       :data:`_REQUIRED_SLICE_00_12_IDS`. Slice IDs lacking an
       acceptance marker in EITHER file are reported in
       :attr:`CompletenessScanReport.missing_acceptance`.

    2. **Unresolved P1/P2 findings** -- greps the journal tail for
       the canonical ``P[12]-<scope>-<seq>`` finding-ID shape and
       greps STATUS.md for active reassessment ids such as
       ``19A-P1-003``. Any finding ID lacking a CLOSED / RESOLVED /
       FIXED / APPLIED status marker on the same line is reported in
       :attr:`CompletenessScanReport.unresolved_findings`.

    3. **`governance_evidence_gap` blockers** -- inspects the
       consumed :class:`GovernanceEvidenceSet` (loaded from ``store``
       by ``corpus_id``) for non-legacy ``governance_evidence_gap``
       blockers. Reported in
       :attr:`CompletenessScanReport.evidence_gaps`. When the store
       has no row for ``corpus_id`` the scanner emits the synthetic
       ``governance_evidence_gap:missing_corpus:<id>`` marker.

    :attr:`CompletenessScanReport.is_complete` is ``True`` iff all
    three lists above are empty. Per the auto-memory
    ``feedback_no_silent_degradation`` rule every code path fails
    closed:

    * Typed-input validation at the entry boundary raises
      :class:`TypeError` on ``None`` / wrong-shape arguments.
    * Missing ``journal_path`` or STATUS.md raises
      :class:`FileNotFoundError`.
    * Missing corpus row in the store is reported as an evidence gap
      (not raised) so the scanner can be invoked safely even when the
      corpus has not yet been ingested.

    **Async surface.** The scanner is ``async def`` because the
    underlying :class:`GovernanceEvidenceStore` surface is uniformly
    async per the Slice 13i finalizer (P2-13i-1 Liskov-substitution
    remediation: the Postgres concrete MUST be async because asyncpg
    cannot be invoked synchronously; the in-memory concrete shares
    the same async signature for Liskov parity). Callers ``await`` the
    scanner uniformly regardless of which concrete store they hold.
    The typed-input :class:`TypeError` early-raise still fires
    synchronously the moment the returned coroutine is awaited (a
    ``raise`` before any ``await`` propagates as soon as the coroutine
    is driven; see the InMemoryGovernanceEvidenceStore.put precedent
    at ``store.py:340-360`` for the same idiom).

    :param corpus_id: the typed-row identity to look up in ``store``.
        Per :meth:`GovernanceEvidenceStore.get` the lookup is bounded
        + idempotent.
    :param store: a :class:`GovernanceEvidenceStore` (in-memory or
        Postgres-backed) holding the corpus's typed
        :class:`GovernanceEvidenceSet`.
    :param journal_path: absolute :class:`~pathlib.Path` to the
        ``implementation-journal.md`` file. STATUS.md is located
        adjacent to it (same directory).
    :returns: a typed :class:`CompletenessScanReport` with the three
        detection lists + the ``is_complete`` summary flag.
    :raises TypeError: when ``corpus_id`` is not a ``str``, ``store``
        is not a :class:`GovernanceEvidenceStore` instance, or
        ``journal_path`` is not a :class:`~pathlib.Path` instance.
    :raises FileNotFoundError: when ``journal_path`` or the adjacent
        STATUS.md does not exist.
    """

    # Fail-fast typed-input validation at the API entry boundary per
    # feedback_no_silent_degradation. Mirrors the
    # InMemoryGovernanceEvidenceStore.put(...) precedent at
    # ``store.py:340-360`` which raises TypeError on None / wrong-shape
    # input. The raise fires the moment the returned coroutine is
    # awaited (raise before any await propagates immediately on
    # coroutine drive).
    if not isinstance(corpus_id, str):
        raise TypeError(
            f"completeness_scanner.scan_governance_completeness requires "
            f"corpus_id: str; got {type(corpus_id).__name__!r} "
            f"(value={corpus_id!r})."
        )
    if not corpus_id or not corpus_id.strip():
        raise TypeError(
            "completeness_scanner.scan_governance_completeness requires "
            "a non-empty corpus_id (whitespace-only is also rejected)."
        )
    if not isinstance(store, GovernanceEvidenceStore):
        raise TypeError(
            f"completeness_scanner.scan_governance_completeness requires "
            f"store: GovernanceEvidenceStore; got "
            f"{type(store).__name__!r} (value={store!r})."
        )
    if not isinstance(journal_path, Path):
        raise TypeError(
            f"completeness_scanner.scan_governance_completeness requires "
            f"journal_path: pathlib.Path; got "
            f"{type(journal_path).__name__!r} (value={journal_path!r})."
        )

    # Bounded file reads. Both helpers raise typed FileNotFoundError
    # on missing inputs per feedback_no_silent_degradation. The file
    # reads are synchronous (stdlib; no IO concurrency benefit) so the
    # async surface does NOT need to thread-pool them.
    journal_tail_text = _read_journal_tail(journal_path)
    status_md_text = _read_status_md(journal_path)

    # Cross-reference acceptance markers + finding statuses against
    # the journal tail + STATUS.md.
    missing_acceptance = _detect_missing_acceptance(
        status_md_text=status_md_text,
        journal_tail_text=journal_tail_text,
    )
    unresolved_findings = sorted(
        set(_detect_unresolved_findings(journal_tail_text=journal_tail_text))
        | set(
            _detect_unresolved_findings(
                journal_tail_text=status_md_text,
                include_reassessment_ids=True,
            )
        )
    )

    # Evidence-set gap detection. The store.get(...) coroutine is
    # awaited natively per the 13i finalizer async-store contract.
    evidence_set = await store.get(corpus_id)

    evidence_gaps = _detect_evidence_gaps(evidence_set, corpus_id=corpus_id)

    is_complete = (
        not missing_acceptance
        and not unresolved_findings
        and not evidence_gaps
    )

    return CompletenessScanReport(
        missing_acceptance=missing_acceptance,
        unresolved_findings=unresolved_findings,
        evidence_gaps=evidence_gaps,
        is_complete=is_complete,
    )
