"""Slice 13c -- pure-typed implementation-journal markdown parser.

This module owns the doc-13:182-183 § "Refactoring Steps" step 3 deliverable:

> Add an implementation-journal parser that produces anchors from markdown
> headings, bullet lines, subagent IDs, test result lines, and acceptance
> notes.

The parser is **pure-typed**: text-in (a file path OR a string body) and
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`-
list-out per the doc-13:143-150 typed shape. It uses only the standard
library (``re`` + ``pathlib``) -- no third-party deps, no executor wiring,
no consumption of the typed evidence as **execution authority**. Per the
governance prompt § "Slice 13A invariant for downstream slices" no
governance ingestor that influences dispatch / verify / merge / checkpoint /
route / scheduler / policy may consume this parser's typed output as
execution authority until Slice 13A's evidence-completeness invariant
lands; until then the parser exists to populate the 13b ingestor's
``ingest_implementation_artifacts`` path in display-only mode (the wiring
itself lands in a later sub-slice once the doc-13:184-185 JSONL
decision-log parser is also in place).

The recognised markdown grammar is the shape Slices 00-12 + the BOOTSTRAP
governance iteration established in ``implementation-journal.md`` itself:

- **Headings** (level 2 ``##``): one of
  ``## YYYY-MM-DD -- Slice <id> STARTING [-- suffix]``,
  ``## YYYY-MM-DD -- Slice <id> COMPLETE [-- suffix]``,
  ``## YYYY-MM-DD -- Slice <id> ACCEPTED [-- suffix]``,
  ``## Slice <id> STARTING|COMPLETE|ACCEPTED [-- suffix] (YYYY-MM-DD)``
  (the early-Slice-08 dated-trailing variant). The em-dash separator is
  the U+2014 character (the journal uses it consistently); ASCII ``--``
  is also accepted for forward-compat.
  **Dual-keyword headings** (P2-13c-R1 remediation): a heading whose
  text carries TWO ``Slice <id> <state>`` clauses separated by a
  separator (em-dash / ASCII ``--`` / ``+``) is recognised as TWO
  anchors at the same ``line_start``. The canonical real-journal
  example is ``## Slice 08g COMPLETE -- Slice 08 ACCEPTED
  (2026-05-21)`` (journal line 14522) which now emits both the
  Slice 08g COMPLETE anchor AND the Slice 08 ACCEPTED anchor. A
  heading that splits into a state token without a leading
  ``Slice <id>`` (e.g. ``Slice 12c STARTING + COMPLETE``) emits only
  the anchors that carry an explicit slice id.
- **Bullets** (level 1 ``- ``): ANY non-empty bullet line in the
  immediately-following section (until the next ``##`` heading or
  end-of-file). Bullets are scanned for finding ids and subagent UUIDs.
- **Subagent UUIDs** (P2-13c-R2 remediation): the
  ``019eXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`` UUIDv7 shape that the
  orchestrator stamps onto each dispatched subagent. Each matched UUID
  in a section body emits a separate ``event="subagent"`` anchor
  carrying the UUID in ``open_findings`` (Option A in the reviewer's
  remediation matrix -- the LESS invasive contract extension; the 13a
  ``ImplementationArtifactAnchor`` is NOT extended; the event taxonomy
  grows from 5 -> 6 values). Per doc-13:182-183 ("Add an
  implementation-journal parser that produces anchors from markdown
  headings, bullet lines, subagent IDs, test result lines, and
  acceptance notes") subagent IDs are explicitly part of the parser
  surface; the per-UUID anchor is the typed citation for that
  requirement.
- **Test result lines**: ``N passed`` (optionally followed by ``in
  TIMEs`` and ``0 failed`` / ``0 errors`` / ``0 skipped``); emitted as a
  separate ``event="test_result"`` anchor so the Slice-15 metrics layer
  can count test-result anchors per slice without needing to re-parse the
  full markdown.
- **Finding ids** (P1-13c-R1 remediation): the ``P[123]-<slice>-<n>``
  shape (e.g. ``P1-13b-1``, ``P3-13b-2``). Finding ids carry their
  OWN slice id (regex group 2) which may differ from the surrounding
  heading's slice (e.g. a ``P3-1b-1`` mentioned under a
  ``## Slice 09e-1b ...`` heading is owned by Slice 1b, not 09e-1b).
  Per doc-13:46-47 (drift / findings as quality signals) and
  doc-13a:24, 109-118 (no silent loss) the parser MUST attribute each
  finding to its OWN owning slice, NOT to the heading's slice:
  - When the finding's owning slice EQUALS the heading slice, the
    finding is added to the heading anchor's ``open_findings`` (unless
    a ``RESOLVED`` / ``CLOSED`` / ``FIXED`` marker is on the SAME
    bullet line). A per-line ``event="finding"`` anchor is ALSO
    emitted for citation density.
  - When the finding's owning slice DIFFERS, the finding is NOT added
    to the heading anchor's ``open_findings`` -- it would mis-attribute
    the finding to a sibling slice. Instead a separate
    ``event="finding"`` anchor is emitted with
    ``slice_id=<finding-owning-slice>`` so the typed cite preserves the
    finding's true owning slice.
  The 13a ``_open_findings_dedup_and_non_empty`` validator at
  ``models.py:_open_findings_dedup_and_non_empty`` enforces dedup +
  non-emptiness on the ``open_findings`` list.

The fail-closed discipline is the auto-memory
``feedback_no_silent_degradation`` rule (P2-13c-R3 remediation). The
parser distinguishes three line classes:

1. A heading that matches one of the heading regexes AND projects
   to a valid :class:`ImplementationArtifactAnchor` -> emit the
   anchor(s).
2. A heading that matches one of the heading regexes but the 13a
   model's ID-field validators (``_non_empty_anchor_id_fields`` /
   ``_line_positive_when_present`` /
   ``_open_findings_dedup_and_non_empty``) reject the
   projection -> raise the underlying :class:`ValueError` per the
   ``feedback_no_silent_degradation`` rule. The regex anchors include
   ``\\b`` boundaries + bounded slice-id length so a malformed slice
   id from the regex itself is rare; the canonical fail-closed path
   is a slice-id that the regex matches but the 13a validator
   subsequently rejects.
3. Any other line (prose, bullet text, JSONL row, code-fence body)
   -> graceful skip. The journal contains a lot of prose that is
   intentionally NOT an anchor; raising on every non-matching line
   would render the parser unusable.

Note: per the reviewer (P3-13c-3 carried) the parser currently only
recognises the ``STARTING / COMPLETE / ACCEPTED`` state vocabulary;
other heading vocabularies in the real journal
(``IMPLEMENTATION / REVIEW / REMEDIATION / SIX-VECTOR REVIEW /
START / BRIEF``) are silently skipped per class (3) above.
Doc-13:182-183 spells "acceptance notes" plural which implies
broader heading coverage; the gap is carried to the Slice 15 metrics
consumer slice that has actual downstream consumption.

Out of scope for 13c (per STATUS.md § "Next safe action"):

- The JSONL decision-log parser at doc-13:184-185; the natural 13d
  sub-slice. Every anchor this 13c parser emits carries
  ``decision_log_line=None``.
- Wiring into
  :meth:`~iriai_build_v2.workflows.develop.governance.ingestor.DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts`;
  later sub-slice.
- Consumption of the typed evidence as **execution authority** (still
  gated on Slice 13A landing).
"""

from __future__ import annotations

import re
from pathlib import Path

from .models import FINDING_ID_REGEX, ImplementationArtifactAnchor, JournalEventName


__all__ = ["parse_implementation_journal"]


# --- Recognised event tags (doc-13:148 -- the "event" Literal field) --------
#
# Doc-13:148 spells the field verbatim as ``event: str`` (free-form string).
# Slice 13l (P3-13c-1 closure) tightened
# :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.event`
# from ``str`` to the typed :data:`JournalEventName` Literal alias (the
# 7-value union of 13c's 6 values + 13d's ``decision`` 7th value); see
# ``models.py`` § "Slice 13l: typed event taxonomy" for the rationale.
# The module-level constants below are now typed against the same alias
# so the parser cannot emit a string the typed 13a model would reject;
# Pydantic still re-validates at construction time, but the typed
# constants make the parser-side contract assertion explicit and
# discoverable at import time (a future drift between the parser and
# the Literal would fail at the typed-surface boundary AND at the
# constant declaration).
#
# Per the 13c finalizer P2-13c-R2 remediation the taxonomy grew from 5 -> 6
# values to add ``subagent`` for the doc-13:182-183 "subagent IDs" parser
# surface. Slice 13l (P3-13c-1) tightened the static type from ``str`` to
# the :data:`JournalEventName` Literal alias. The journal parser still
# emits only the 6-value subset (``starting`` / ``complete`` / ``accepted``
# / ``finding`` / ``test_result`` / ``subagent``); the 7th value
# ``decision`` is emitted exclusively by the 13d decision-log parser.
# Literal subset is widening-safe: the 6 values the journal parser uses
# are a subset of the 7 values the Literal admits, so every emit is
# typed-valid.
_EVENT_STARTING: JournalEventName = "starting"
_EVENT_COMPLETE: JournalEventName = "complete"
_EVENT_ACCEPTED: JournalEventName = "accepted"
_EVENT_FINDING: JournalEventName = "finding"
_EVENT_TEST_RESULT: JournalEventName = "test_result"
_EVENT_SUBAGENT: JournalEventName = "subagent"


# --- Regex grammars (doc-13:182-183) ---------------------------------------
#
# Heading shape A: ``## YYYY-MM-DD <DASH> Slice <slice-id> <STATE> [...]``
# where ``<DASH>`` is the U+2014 em-dash the journal uses consistently
# (also accepts the ASCII ``--`` fallback for forward-compat). Captures the
# slice id segment (digits + optional alpha suffix, e.g. "13a", "13b", "11d",
# "08c-1") and the state keyword.
#
# Heading shape B: the early-Slice-08 ``## Slice <slice-id> <STATE> [...]
# (YYYY-MM-DD)`` variant (e.g. ``## Slice 08c-1 COMPLETE -- real-Postgres
# test fixture (2026-05-21)``). The grammar permits the date at either end
# so the parser recognises both eras of the journal.
#
# Heading shape C: the multi-slice top-level marker
# ``## YYYY-MM-DD <DASH> Governance Layer (Slices 13-19) STARTING [...]``
# (the BOOTSTRAP iteration heading at journal line ~35857). The parser
# does NOT emit anchors for these markers (they reference no single slice);
# they are matched only to be deliberately skipped, so the parser does not
# false-fire a ValueError on the multi-slice variant.

# The state keyword vocabulary the parser recognises. ``ACCEPTED`` is the
# only one that sets ``accepted=True``; the other three set False.
#
# Slice 13l (P3-13c-1 closure) tightened the value type from ``str`` to
# :data:`JournalEventName` so the typed-surface contract is preserved
# end-to-end -- ``_STATE_TO_EVENT["STARTING"]`` returns a value the
# 13a :class:`ImplementationArtifactAnchor.event` Literal will accept.
_STATE_TO_EVENT: dict[str, JournalEventName] = {
    "STARTING": _EVENT_STARTING,
    "COMPLETE": _EVENT_COMPLETE,
    "ACCEPTED": _EVENT_ACCEPTED,
}

# Date in ISO-8601 ``YYYY-MM-DD`` form.
_DATE_RE: str = r"(?P<date>\d{4}-\d{2}-\d{2})"

# Slice id: 1-3 digits, optional single alpha letter suffix
# (e.g. "13", "13a", "11d", "08e"), optional dash-and-digit sub-id
# (e.g. "08c-1", "08e-3a", "12a-1"). Excludes the multi-slice ``13-19``
# range marker -- that variant is matched separately by ``_HEADING_RE_RANGE``
# and intentionally skipped.
_SLICE_ID_RE: str = r"(?P<slice_id>\d{1,3}[a-z]?(?:-[\da-z]+)?)"

# State token: one of STARTING / COMPLETE / ACCEPTED.
_STATE_RE: str = r"(?P<state>STARTING|COMPLETE|ACCEPTED)"

# Dash separator: U+2014 em-dash OR ASCII double-hyphen (forward-compat).
_DASH_RE: str = r"(?:—|--)"

# Heading shape A (current era): ``## YYYY-MM-DD <DASH> Slice <id> <STATE> [...]``.
# Anchored at start of line; tolerates trailing freeform suffix (e.g.
# ``-- implementer BEFORE entry``). The leading ``##`` is part of the
# match so a level-3 ``###`` cannot false-fire (the leading ``##\s+``
# requires exactly two ``#`` followed by whitespace).
_HEADING_RE_DATED: re.Pattern[str] = re.compile(
    r"^##\s+" + _DATE_RE + r"\s+" + _DASH_RE + r"\s+Slice\s+" + _SLICE_ID_RE
    + r"\s+" + _STATE_RE + r"(?:\s.*)?$"
)

# Heading shape B (early-Slice-08 era):
# ``## Slice <id> <STATE> [-- suffix] (YYYY-MM-DD)``.
_HEADING_RE_TRAILING_DATE: re.Pattern[str] = re.compile(
    r"^##\s+Slice\s+" + _SLICE_ID_RE + r"\s+" + _STATE_RE
    + r"(?:\s+" + _DASH_RE + r"\s+.*?)?\s*\(" + _DATE_RE + r"\)\s*$"
)

# Heading shape C (multi-slice range marker -- DELIBERATELY SKIPPED):
# ``## YYYY-MM-DD <DASH> Governance Layer (Slices 13-19) STARTING [...]``.
# We match this so the parser can short-circuit on it without trying to
# project a single slice id from a range like ``13-19``.
_HEADING_RE_RANGE: re.Pattern[str] = re.compile(
    r"^##\s+" + _DATE_RE + r"\s+" + _DASH_RE + r"\s+.*\(Slices\s+\d+\D+\d+\).*$"
)

# Subagent UUIDv7 stamp shape: ``019eXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX``
# (per STATUS.md § "Next safe action" Chunk-shape point 1). The leading
# ``019e`` prefix is the UUIDv7 timestamp band the orchestrator currently
# stamps; this anchors the regex against accidental hex-string false
# positives elsewhere in the markdown body.
_SUBAGENT_UUID_RE: re.Pattern[str] = re.compile(
    r"\b019e[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)

# Test result line shape: ``N passed`` (anywhere in the line), optionally
# followed by other counts. Recognised in prose AND in fenced/inline code.
# Examples from the real journal:
#   ``-> 336 passed / 0 failed / 0 errors / 0 skipped in 0.16s``
#   ``-> **336 passed in 59.21s**``
#   ``all passing (16 passed in 4.02s).``
# The regex captures the numeric count so a later metrics consumer can
# aggregate without re-parsing.
_TEST_RESULT_RE: re.Pattern[str] = re.compile(
    r"\b(?P<count>\d+)\s+passed\b"
)

# Finding-id shape: ``P[123]-<slice>-<n>`` where slice can include hyphens
# (e.g. ``P3-13b-2``, ``P1-08e-3a-1``). The leading word boundary plus the
# ``P`` letter make this distinguishable from generic identifiers.
#
# Per the 13c finalizer P1-13c-R1 remediation the ``slice_id`` group is
# named so the parser can extract the finding's OWN owning slice for
# correct cross-slice attribution (doc-13:46-47 drift/findings as quality
# signals; doc-13a:24, 109-118 no silent loss). The trailing index segment
# (``-\d+``) is captured separately so the slice-id group does not absorb
# it -- for ``P1-08e-3a-1`` the slice-id is ``08e-3a`` and the index is
# ``1``.
#
# Per the Slice 13A first-sub-slice reviewer P2-A3-1 finalizer
# remediation, the canonical regex now lives in
# :data:`models.FINDING_ID_REGEX` as the single source of truth shared
# with ``decision_log_parser`` + ``completeness_scanner``. The
# in-module alias :data:`_FINDING_ID_RE` is preserved verbatim for
# in-module readability + minimal diff impact on the existing
# downstream consumers in this file.
_FINDING_ID_RE: re.Pattern[str] = FINDING_ID_REGEX

# Markers that flip a finding from "open" -> "resolved/closed" on the SAME
# bullet line. The match is case-insensitive on the keyword itself; the
# typical journal phrasing is ``RESOLVED`` / ``CLOSED`` / ``FIXED`` /
# ``CLOSED-as-non-issue``.
_RESOLVED_MARKER_RE: re.Pattern[str] = re.compile(
    r"\b(?:RESOLVED|CLOSED|FIXED)\b"
)


# Dual-keyword heading splitter (P2-13c-R1 remediation). A heading like
# ``## Slice 08g COMPLETE -- Slice 08 ACCEPTED (2026-05-21)`` carries TWO
# ``Slice <id> <state>`` clauses that the parser must emit as TWO anchors.
# The splitter scans the heading text for every occurrence of the
# ``Slice <id> <state>`` pattern (anywhere in the line, not just after the
# leading date/dash). Each match becomes one anchor. A heading that
# carries only one ``Slice <id> <state>`` clause emits one anchor (no
# behaviour change). A heading whose secondary segment is a bare state
# without a leading ``Slice <id>`` token (e.g. ``Slice 12c STARTING +
# COMPLETE (retroactive)``) emits only the anchors that carry an explicit
# slice id, since a state without an owning slice has no anchor target.
_HEADING_CLAUSE_RE: re.Pattern[str] = re.compile(
    r"\bSlice\s+" + _SLICE_ID_RE + r"\s+" + _STATE_RE + r"\b"
)


# --- Public surface ---------------------------------------------------------


def parse_implementation_journal(
    path: Path | str,
    *,
    body: str | None = None,
) -> list[ImplementationArtifactAnchor]:
    """Parse ``implementation-journal.md`` markdown into typed anchors.

    The function reads the markdown body from ``path`` unless ``body`` is
    supplied (the test-only escape hatch -- the ingestor caller in a later
    sub-slice always passes a real path). It produces one
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    per recognised anchor per doc-13:182-183 (heading anchors,
    finding anchors, subagent anchors, test-result anchors). The anchor
    list is in source-order -- a stable order so downstream metrics
    consumers see a deterministic sequence.

    Per the 13c finalizer remediation the parser:

    * **P1-13c-R1** attributes each finding to the finding's OWN owning
      slice (parsed from the finding-id regex group), NOT to the
      surrounding heading's slice. Cross-slice finding mentions emit
      their own ``event="finding"`` anchor with the correct slice
      id; they do NOT contribute to the heading anchor's
      ``open_findings`` list.
    * **P2-13c-R1** emits ONE anchor per ``Slice <id> <state>`` clause
      found in a heading line. Dual-keyword headings (e.g.
      ``## Slice 08g COMPLETE -- Slice 08 ACCEPTED (2026-05-21)``)
      emit two anchors at the same ``line_start``.
    * **P2-13c-R2** emits ONE ``event="subagent"`` anchor per
      ``019eXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX`` UUIDv7 matched in a
      heading section body, with the UUID in ``open_findings``.

    :param path: the journal source path; recorded into
        :attr:`ImplementationArtifactAnchor.journal_path` (as ``str(path)``)
        whether or not ``body`` is also supplied. Per doc-13:147 the
        ``journal_path`` field is the stable cross-process freshness
        anchor; using ``str(path)`` keeps the contract uniform across
        ``Path`` and pre-stringified inputs.
    :param body: optional pre-loaded markdown body; when ``None`` (the
        production caller path) the function reads ``path`` from disk.
        When supplied, the function does NOT touch the filesystem -- the
        test suite uses this to inject synthetic markdown without
        managing tmpfiles. The ``path`` argument is still recorded on
        every emitted anchor so the typed anchor cite is stable.
    :returns: a list of :class:`ImplementationArtifactAnchor` rows, in
        source-order. May be empty (empty file / no recognised anchors);
        per the fail-closed discipline an empty list is a legitimate
        result, not an error.
    :raises ValueError: when a recognised heading exists but the
        derived :class:`ImplementationArtifactAnchor` rejects the
        candidate fields (e.g. an empty slice id segment, an open
        finding id that the 13a ``_open_findings_dedup_and_non_empty``
        validator rejects). Per the auto-memory
        ``feedback_no_silent_degradation`` rule the parser fails closed
        with a typed error rather than silently degrading.
    """

    # Doc-13:147 -- journal_path is the stable cross-process freshness
    # anchor. Always coerce to str so the 13a model validator sees a
    # non-empty identifier (a bare Path object would still str() to a
    # non-empty value, but the explicit coercion makes the contract clear
    # at the call site).
    journal_path_str = str(path)

    if body is None:
        # Doc-13:182-183 -- the parser is the ingestor's
        # ``ingest_implementation_artifacts`` feeder, so the production
        # path reads from disk. Per the governance prompt § "Bounded
        # reads" the caller is responsible for budget enforcement; the
        # parser itself is bounded by the markdown body size which the
        # caller already chose to read.
        body = Path(path).read_text(encoding="utf-8")

    anchors: list[ImplementationArtifactAnchor] = []

    # Split on ``\n`` to preserve 1-indexed line numbers exactly. The
    # markdown is parsed in two passes:
    #   1. find every level-2 heading and project it to one or more
    #      anchors (per P2-13c-R1, dual-keyword headings emit multiple
    #      anchors that share the same ``line_start``);
    #   2. for each heading section, scan the bullets between this
    #      heading and the next heading (or EOF) for finding ids,
    #      subagent UUIDs, and test-result lines.
    lines = body.split("\n")

    # First pass: identify heading line indices + the (slice / event /
    # accepted) tuples each heading projects. A single heading may
    # project MULTIPLE tuples per P2-13c-R1 (dual-keyword headings).
    # ``heading_records`` is the per-heading-line ordered list; per-line
    # ``heading_clauses`` is the list of (slice_id, event, accepted)
    # tuples that line projects.
    heading_records: list[tuple[int, list[tuple[str, str, bool]]]] = []
    for idx, line in enumerate(lines):
        line_no = idx + 1  # 1-indexed per doc-13:147 (line_start).

        # Doc-13:182 + the parser docstring -- ``Slices 13-19``
        # multi-slice markers (heading shape C) deliberately produce no
        # anchor. Match BEFORE shape A so the date+dash prefix does not
        # false-fire as a single-slice anchor.
        if _HEADING_RE_RANGE.match(line):
            continue

        # Shape A / Shape B recognition. A non-match here means this is
        # not a recognised heading line -- graceful skip per the parser
        # docstring class-(3) discipline (most journal lines are prose).
        if (
            _HEADING_RE_DATED.match(line) is None
            and _HEADING_RE_TRAILING_DATE.match(line) is None
        ):
            continue

        # P2-13c-R1 -- scan the heading text for EVERY ``Slice <id>
        # <state>`` clause it contains. A single-clause heading yields
        # one tuple; a dual-keyword heading yields two. The
        # ``_HEADING_CLAUSE_RE`` ignores the leading ``## YYYY-MM-DD --``
        # prefix and the trailing ``(YYYY-MM-DD)`` suffix; the heading
        # shape regexes A / B above are the gatekeeper that confirms the
        # LINE is a heading at all.
        clauses: list[tuple[str, str, bool]] = []
        for clause_match in _HEADING_CLAUSE_RE.finditer(line):
            clause_slice = clause_match.group("slice_id")
            clause_state = clause_match.group("state")
            clauses.append(
                (clause_slice, _STATE_TO_EVENT[clause_state], clause_state == "ACCEPTED")
            )

        # Fail-closed safety net (P2-13c-R3): the shape regex matched but
        # the clause splitter found zero clauses. This would only happen
        # under a regex contract drift between the two patterns; treat
        # it as a typed parser error rather than silently emitting nothing.
        if not clauses:
            raise ValueError(
                f"journal_parser: line {line_no} matched a heading shape "
                f"regex but no ``Slice <id> <state>`` clause was found "
                f"in the heading text. This is a parser contract drift; "
                f"fail-closed per the no-silent-degradation rule. "
                f"Heading: {line!r}"
            )

        heading_records.append((line_no, clauses))

    # Sentinel for "everything after the last heading" -- use len(lines)
    # so the slice [start, end) covers every remaining line.
    sentinel_end = len(lines)

    # Second pass: for each heading, scan its section for findings +
    # test-result lines + subagent UUIDs and emit the anchors. A heading
    # with multiple clauses (P2-13c-R1) shares its section's per-line
    # anchors -- each clause-anchor sees the SAME open-findings set for
    # its OWN slice_id, so a finding owned by clause A's slice goes onto
    # clause A's heading and NOT clause B's heading (P1-13c-R1).
    for record_idx, (line_no, clauses) in enumerate(heading_records):
        next_line_no = (
            heading_records[record_idx + 1][0]
            if record_idx + 1 < len(heading_records)
            else sentinel_end + 1
        )

        # Per-clause-slice open-findings tracking: each clause's
        # ``open_findings`` is the deduplicated list of findings whose
        # OWN slice matches that clause's slice. The set is keyed by the
        # clause's slice id so a dual-keyword heading (e.g.
        # ``Slice 08g COMPLETE -- Slice 08 ACCEPTED``) attributes each
        # finding to the correct sub-clause.
        per_clause_findings: dict[str, list[str]] = {
            clause[0]: [] for clause in clauses
        }
        per_clause_seen: dict[str, set[str]] = {
            clause[0]: set() for clause in clauses
        }

        # The section body is the lines strictly AFTER the heading and
        # strictly BEFORE the next heading (or EOF).
        section_start_idx = line_no  # heading is at idx line_no - 1, so
        # the section starts at idx line_no.
        section_end_idx = next_line_no - 1

        for section_idx in range(section_start_idx, section_end_idx):
            if section_idx >= len(lines):
                break
            section_line = lines[section_idx]
            section_line_no = section_idx + 1  # 1-indexed.

            # Finding-id detection -- a bullet/prose line that contains
            # a finding id. Per P1-13c-R1 (doc-13:46-47 drift/findings
            # as quality signals; doc-13a:24, 109-118 no silent loss):
            # the finding's OWN owning slice (regex group "slice_id")
            # determines attribution, NOT the surrounding heading's
            # slice. A per-line ``event="finding"`` anchor is emitted
            # with the finding's OWN slice so the typed cite preserves
            # the correct ownership; only findings whose owning slice
            # MATCHES one of this heading's clause slices contribute to
            # that clause's ``open_findings`` list.
            for finding_match in _FINDING_ID_RE.finditer(section_line):
                finding_id = finding_match.group("finding_id")
                finding_owning_slice = finding_match.group("slice_id")
                resolved = _RESOLVED_MARKER_RE.search(section_line) is not None

                # Emit the per-line ``event="finding"`` anchor with the
                # finding's OWN owning slice. ``open_findings`` carries
                # the finding id when the finding is OPEN; the empty
                # list when the finding is RESOLVED/CLOSED/FIXED on the
                # same bullet line.
                anchors.append(
                    ImplementationArtifactAnchor(
                        slice_id=finding_owning_slice,
                        journal_path=journal_path_str,
                        line_start=section_line_no,
                        decision_log_line=None,
                        event=_EVENT_FINDING,
                        accepted=False,
                        open_findings=(
                            [] if resolved else [finding_id]
                        ),
                    )
                )

                # If the finding is RESOLVED on this line, it doesn't
                # contribute to ANY clause's open_findings.
                if resolved:
                    continue

                # P1-13c-R1 attribution: ONLY add the finding to a
                # clause's ``open_findings`` when the finding's owning
                # slice matches that clause's slice. A cross-slice
                # finding (finding_owning_slice differs from every
                # clause slice) is captured by the per-line anchor above
                # and NOT mis-attributed to any heading.
                if finding_owning_slice in per_clause_seen:
                    if finding_id not in per_clause_seen[finding_owning_slice]:
                        per_clause_seen[finding_owning_slice].add(finding_id)
                        per_clause_findings[finding_owning_slice].append(finding_id)

            # Subagent UUID detection (P2-13c-R2 remediation). Per
            # doc-13:182-183 ("subagent IDs") each matched UUID emits a
            # separate ``event="subagent"`` anchor with the UUID in
            # ``open_findings``. The anchor inherits the FIRST clause's
            # slice id when the heading has multiple clauses -- the UUID
            # is a per-dispatch artifact, not a per-finding artifact, so
            # attributing it to the heading's dominant clause is
            # consistent with doc-13:143-150 (the anchor is per-slice,
            # not per-subagent).
            for uuid_match in _SUBAGENT_UUID_RE.finditer(section_line):
                subagent_uuid = uuid_match.group(0)
                anchors.append(
                    ImplementationArtifactAnchor(
                        slice_id=clauses[0][0],
                        journal_path=journal_path_str,
                        line_start=section_line_no,
                        decision_log_line=None,
                        event=_EVENT_SUBAGENT,
                        accepted=False,
                        open_findings=[subagent_uuid],
                    )
                )

            # Test-result line detection -- ``N passed`` in any context.
            # Emit a separate event="test_result" anchor per matched
            # line so a future metrics consumer can count passes per
            # slice. The line MAY contain multiple counts (e.g.
            # ``336 passed`` + ``0 failed``); we emit one anchor per
            # matched ``N passed`` to keep the anchor list deterministic.
            # The anchor inherits the FIRST clause's slice id (same
            # reasoning as the subagent anchor above).
            if _TEST_RESULT_RE.search(section_line):
                anchors.append(
                    ImplementationArtifactAnchor(
                        slice_id=clauses[0][0],
                        journal_path=journal_path_str,
                        line_start=section_line_no,
                        decision_log_line=None,
                        event=_EVENT_TEST_RESULT,
                        accepted=False,
                        open_findings=[],
                    )
                )

        # Emit one heading anchor per clause. The 13a
        # ``_open_findings_dedup_and_non_empty`` validator will reject
        # duplicates -- because we kept ``per_clause_seen`` we never
        # feed it a duplicate. Each clause's heading anchor shares the
        # SAME ``line_start`` (P2-13c-R1: dual-keyword headings
        # collapse to one source line but emit multiple anchors).
        for (clause_slice, clause_event, clause_accepted) in clauses:
            anchors.append(
                ImplementationArtifactAnchor(
                    slice_id=clause_slice,
                    journal_path=journal_path_str,
                    line_start=line_no,
                    decision_log_line=None,
                    event=clause_event,
                    accepted=clause_accepted,
                    open_findings=list(per_clause_findings[clause_slice]),
                )
            )

    # Source-order sort: the per-finding / per-test-result anchors share
    # the heading's slice but live at later line_start values; sort by
    # (line_start, event-priority) so the deterministic order downstream
    # consumers see is "heading first, then per-section anchors in line
    # order".  Heading anchors sort BEFORE per-section anchors at the
    # same line_start (the heading anchor lives at its own heading line
    # which strictly precedes any section bullet line, so the natural
    # numeric sort already gives the right order; the explicit key
    # documents the invariant).
    def _sort_key(
        anchor: ImplementationArtifactAnchor,
    ) -> tuple[int, int]:
        # Heading events sort before per-line events at the same line.
        # This will never tie in practice -- the heading is always at
        # its own line_start -- but the explicit secondary key makes
        # the deterministic order self-documenting.
        priority = 0 if anchor.event in {
            _EVENT_STARTING, _EVENT_COMPLETE, _EVENT_ACCEPTED
        } else 1
        return ((anchor.line_start or 0), priority)

    anchors.sort(key=_sort_key)

    return anchors
