"""Slice 13d -- pure-typed JSONL decision-log parser.

This module owns the doc-13:184-185 § "Refactoring Steps" step 4 deliverable:

> Add a JSONL decision-log parser that rejects malformed rows and records line
> numbers as evidence anchors.

The parser is **pure-typed**: text-in (a file path OR a string body) and
:class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`-
list-out per the doc-13:143-150 typed shape. It uses only the standard library
(``json`` + ``pathlib``) -- no third-party deps, no executor wiring, no
consumption of the typed evidence as **execution authority**. Per the
governance prompt § "Slice 13A invariant for downstream slices" no governance
ingestor that influences dispatch / verify / merge / checkpoint / route /
scheduler / policy may consume this parser's typed output as execution
authority until Slice 13A's evidence-completeness invariant lands; until then
the parser exists to populate the 13b ingestor's
``ingest_implementation_artifacts`` path in display-only mode (the wiring
itself lands in a later sub-slice once the doc-13:184-185 JSONL decision-log
parser AND the doc-13:182-183 markdown journal parser are both in place; this
is the sibling deliverable to ``journal_parser.py`` that completes the
journal+decision-log pair the ingestor will compose).

The parser mirrors :mod:`.journal_parser` (the 13c sibling) in API shape +
docstring style for consistency. The differences between the two parsers
boil down to which of the 13a model's two line-anchor fields they populate:

* **13c journal parser**: parses ``implementation-journal.md``; every emitted
  anchor carries ``line_start=<markdown-line-no>`` + ``decision_log_line=None``.
* **13d decision-log parser** (this module): parses
  ``implementation-decisions.jsonl``; every emitted anchor carries
  ``line_start=None`` + ``decision_log_line=<jsonl-row-no>``.

Per the 13a ``ImplementationArtifactAnchor._line_positive_when_present`` field
validator (``models.py:437-447``) ``line_start`` must be ``>= 1`` when present;
``line_start=0`` would raise ``ValidationError``. The 13d parser therefore
uses ``line_start=None`` -- the only fail-closed choice the typed shape
accepts to signal "this anchor is a decision-log-only anchor, not a journal
anchor" while preserving the bidirectional ``13c⊕13d`` distinction
(``decision_log_line=None`` ⇔ 13c-emitted; ``line_start=None`` ⇔ 13d-emitted).
The 13a model is FROZEN for 13d; the bidirectional invariant lives in the
13d parser docstring + the 13d test suite.

The recognised JSONL grammar is the shape Slices 00-12 + the BOOTSTRAP
governance iteration established in ``implementation-decisions.jsonl``
itself. Real-data analysis (1133 rows as of the 13c finalizer iteration)
shows two eras of row shape:

- **Pre-BOOTSTRAP era (rows 1-1123)**: rows carry ``stage=None``; ``event``
  is the dominant taxonomy field with 11+ distinct values (``test``,
  ``review``, ``patch``, ``dispatch``, ``acceptance``, ``resume``,
  ``complete``, ``decision``, ``blocker``, ``starting``,
  ``change_control``, ``remediation_start``, ``remediation_complete``,
  ``implementation_complete``, ``governance_phase_bootstrap``).
- **Post-BOOTSTRAP governance era (rows 1124+)**: rows carry both ``event``
  AND ``stage`` (the triad pattern -- ``implementer_before`` /
  ``implementer_after`` / ``finalizer_before`` / ``finalizer_after``).
  ``sub_slice`` is populated; older rows lack it.

The parser supports both eras with the same code path; the stage→event
mapping table below picks one consistent normalisation.

**Stage→event mapping (DOCUMENTED choice).** Per the implementer prompt's
"Pick a mapping and DOCUMENT IT" instruction:

* ``stage`` ending in ``_before`` → ``"starting"`` (e.g. ``implementer_before``
  / ``finalizer_before`` / future triad ``_before`` variants).
* ``stage`` ending in ``_after`` → ``"complete"`` (the triad ``_after``
  variants signal the end of a phase chunk).
* ``stage`` containing ``finding`` → ``"finding"`` (future-proofing: no real
  row uses ``stage=<...>finding<...>`` today, but the prompt names this
  mapping explicitly).
* When no ``stage`` mapping fires, fall through to ``event``:
    - ``event in {"starting", "complete", "accepted", "finding",
      "test_result"}`` → pass-through verbatim.
    - ``event == "acceptance"`` → ``"accepted"`` (the real journal's plural
      form; ``acceptance`` rows mark slice acceptance per the journal
      protocol).
    - ``event == "test"`` → ``"test_result"`` (the real journal's short form;
      ``test`` rows record targeted-test runs).
    - any other ``event`` value (``dispatch``, ``patch``, ``review``,
      ``resume``, ``decision``, ``blocker``, ``change_control``,
      ``remediation_start``, ``remediation_complete``,
      ``implementation_complete``, ``governance_phase_bootstrap``,
      ``implementer_before``, ``implementer_after``, ``finalizer_before``,
      ``finalizer_after``) → normalised to ``"decision"`` (the
      catch-all class the user prompt names as the only allowed
      extension beyond the 13c 6-value taxonomy).
* When neither ``stage`` nor ``event`` yields ``accepted`` directly, the
  ``summary`` field is scanned for an explicit ``ACCEPTED`` token (uppercase
  keyword, mirroring the 13c journal parser's
  ``_RESOLVED_MARKER_RE`` case-sensitivity discipline). A row whose summary
  begins with ``Slice 13a ACCEPTED ...`` (the canonical journal pattern)
  is upgraded to ``event="accepted"`` even though its source ``event``
  field is ``"acceptance"`` / ``None``. This is the ``accepted`` heuristic
  the user prompt names; it is applied AFTER the stage / event lookups so
  it never silently overrides a more-specific mapping.

  .. note::
     **TODO(slice-13e or 15)** -- a more discriminating heuristic is
     needed: anchor to start-of-summary or to a ``Slice X ACCEPTED``
     shape. The current heuristic over-matches descriptive prose by
     design (e.g. a row whose summary contains the word ``ACCEPTED`` in
     non-acceptance context like ``"discussing ACCEPTED versus REJECTED
     options"`` will also be upgraded to ``event="accepted"``). This
     behavior is pinned by
     :func:`test_accepted_keyword_in_descriptive_prose_upgrades_per_documented_heuristic`
     in ``tests/test_governance_decision_log_parser.py`` so any future
     refinement is a deliberate breaking change with a clear test to
     update. Refinement deferred to 13e or Slice 15 when a real
     downstream consumer (metrics / finding engine) needs the tighter
     mapping.

The 13d event taxonomy is therefore the 6-value 13c taxonomy plus the
catch-all ``decision`` value = **7 values total** (``starting`` / ``complete``
/ ``accepted`` / ``finding`` / ``test_result`` / ``subagent`` / ``decision``).
The 13d parser itself never emits ``"subagent"`` (subagent IDs live in the
markdown journal, not in the decision log; the 13c parser is the
``subagent`` source). The 6-value 13c taxonomy + the 1-value 13d extension
= 7 distinct values the consumer slice can expect.

**slice_id resolution (per STATUS.md § "Next safe action" Chunk-shape
point 1).** ``slice_id = row["sub_slice"]`` when ``sub_slice`` is present
and non-empty; otherwise ``slice_id = row["slice"]``. Real-data analysis:
``sub_slice`` is only populated for the 10 most recent governance triad
rows (13a/13b/13c). For older rows, the slug like
``00-evidence-fixtures-and-compatibility-inventory`` is the slice_id; the
13a ``_non_empty_anchor_id_fields`` validator (renamed from
``_non_empty_identifier`` in Slice 13m per P3-13a-3) accepts it
(non-empty + non-whitespace).

**Per-finding-owning-slice attribution (mirrors 13c P1-13c-R1 discipline).**
Doc-13:46-47 (drift / findings as quality signals) + doc-13a:24, 109-118
(no silent loss) mandate that each finding id be attributed to its OWN
owning slice, NOT to the surrounding row's slice. Findings are extracted
from ``row["summary"]`` via the ``P[123]-<slice>-<n>`` regex (the same
shape the 13c parser uses) with a named ``slice_id`` capture group. When
the finding's owning slice matches the row's slice_id, the finding is
added to the row's main anchor's ``open_findings``. When it differs, a
separate ``event="finding"`` anchor is emitted with
``slice_id=<finding-owning-slice>`` so the typed cite preserves the
correct ownership. Per-row dedup is applied before construction (8 real
rows contain duplicate finding-id mentions in the summary; the 13a
``_open_findings_dedup_and_non_empty`` validator at
``models.py:449-469`` would reject duplicates).

**Fail-closed discipline (doc-13:184 verbatim + auto-memory
``feedback_no_silent_degradation``).** The parser distinguishes three
line classes:

1. A non-empty line that parses as a JSON object AND projects to a
   valid :class:`ImplementationArtifactAnchor` → emit the anchor(s).
2. A non-empty line that does not parse as JSON, or parses to a
   non-object (e.g. a bare list / string / number) → raise typed
   :class:`ValueError` with the 1-indexed line number. This is the
   doc-13:184 "rejects malformed rows" verbatim contract.
3. A blank / whitespace-only line → graceful skip. The JSONL format
   intentionally allows blank trailing newlines; rejecting them would
   over-strict for legitimate files. This is the only relaxation; every
   non-empty-non-blank line is gated by class (1) or class (2).

Out of scope for 13d (per STATUS.md § "Next safe action"):

- The evidence-set digesting at doc-13:186-187 § "Refactoring Steps"
  step 5; the natural 13e sub-slice.
- Wiring into
  :meth:`~iriai_build_v2.workflows.develop.governance.ingestor.DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts`;
  later sub-slice once both this parser AND the 13c
  :func:`~iriai_build_v2.workflows.develop.governance.journal_parser.parse_implementation_journal`
  are composed.
- Consumption of the typed evidence as **execution authority** (still
  gated on Slice 13A landing per the governance prompt § "Slice 13A
  invariant for downstream slices").
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

from .models import FINDING_ID_REGEX, ImplementationArtifactAnchor, JournalEventName


__all__ = ["parse_implementation_decision_log"]


# --- Recognised event tags (doc-13:148 + the 13d catch-all extension) -------
#
# Doc-13:148 spells the field verbatim as ``event: str`` (free-form string).
# Slice 13l (P3-13d-1 closure) tightened
# :attr:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor.event`
# from ``str`` to the typed :data:`JournalEventName` Literal alias (the
# 7-value union of 13c's 6 values + 13d's ``decision`` 7th value); see
# ``models.py`` § "Slice 13l: typed event taxonomy" for the rationale.
# The module-level constants below are now typed against the same alias
# so the parser cannot emit a string the typed 13a model would reject;
# Pydantic still re-validates at construction time, but the typed
# constants make the parser-side contract assertion explicit and
# discoverable at import time.
#
# 13c taxonomy: starting / complete / accepted / finding / test_result /
# subagent (6 values). 13d added the ``decision`` catch-all per the
# original 13d implementer prompt's "or extend with `decision`"
# instruction. Slice 13l (P3-13d-1 closure) formalised the union as the
# typed :data:`JournalEventName` Literal: the 13c 6-value subset + the
# 13d ``decision`` 7th value = 7 distinct values the consumer slice can
# expect. The 13d parser still never emits ``"subagent"`` (subagent IDs
# live in the markdown journal, not in the decision log; the 13c parser
# is the ``subagent`` source). Literal subset is widening-safe: the 6
# decision-log-parser values + the 1 catch-all that the parser actually
# emits are all in the 7-value union.
_EVENT_STARTING: JournalEventName = "starting"
_EVENT_COMPLETE: JournalEventName = "complete"
_EVENT_ACCEPTED: JournalEventName = "accepted"
_EVENT_FINDING: JournalEventName = "finding"
_EVENT_TEST_RESULT: JournalEventName = "test_result"
_EVENT_DECISION: JournalEventName = "decision"


# Verbatim event-value pass-through set (no normalisation).
_EVENT_PASSTHROUGH: frozenset[str] = frozenset(
    {
        _EVENT_STARTING,
        _EVENT_COMPLETE,
        _EVENT_ACCEPTED,
        _EVENT_FINDING,
        _EVENT_TEST_RESULT,
    }
)


# Event-value rename table: the real journal's source names → the 13c-aligned
# canonical tags. Slice 13l (P3-13d-1 closure) tightened the value type to
# :data:`JournalEventName` so the renamed-to value is statically known to
# satisfy the typed 13a :attr:`ImplementationArtifactAnchor.event` Literal.
# The key type stays ``str`` (the source vocabulary is arbitrary user
# input from the JSONL row's ``event`` field).
_EVENT_RENAME: dict[str, JournalEventName] = {
    "acceptance": _EVENT_ACCEPTED,
    "test": _EVENT_TEST_RESULT,
}


# --- Regex grammars ---------------------------------------------------------
#
# Finding-id shape: ``P[123]-<slice>-<n>`` where slice can include hyphens
# (e.g. ``P3-13b-2``, ``P1-08e-3a-1``). The leading word boundary plus the
# ``P`` letter make this distinguishable from generic identifiers. The
# ``slice_id`` named group enables per-finding-owning-slice attribution
# (mirrors 13c P1-13c-R1 discipline at ``journal_parser.py:260-262``).
# The trailing index segment (``-\d+``) is captured separately so the
# slice-id group does not absorb it -- for ``P1-08e-3a-1`` the slice-id
# is ``08e-3a`` and the index is ``1``.
#
# Per the Slice 13A first-sub-slice reviewer P2-A3-1 finalizer
# remediation, the canonical regex now lives in
# :data:`models.FINDING_ID_REGEX` as the single source of truth shared
# with ``journal_parser`` + ``completeness_scanner``. The in-module
# alias :data:`_FINDING_ID_RE` is preserved verbatim for in-module
# readability + minimal diff impact on the existing downstream
# consumers in this file.
_FINDING_ID_RE: re.Pattern[str] = FINDING_ID_REGEX


# The ACCEPTED-in-summary heuristic. Uppercase ``ACCEPTED`` token bounded by
# word boundaries so a prose mention like "we accepted the patch" (lowercase)
# never false-fires. Mirrors the 13c ``_RESOLVED_MARKER_RE`` case-sensitivity
# discipline at ``journal_parser.py:268-270``.
_ACCEPTED_SUMMARY_RE: re.Pattern[str] = re.compile(r"\bACCEPTED\b")


# --- Public surface ---------------------------------------------------------


def _map_event(row: dict[str, object]) -> JournalEventName:
    """Project a raw JSONL row to a canonical 13d event tag.

    Per the module docstring § "Stage→event mapping" the precedence is:

    1. ``stage`` suffix ``_before`` → ``"starting"``
    2. ``stage`` suffix ``_after`` → ``"complete"``
    3. ``stage`` substring ``finding`` → ``"finding"``
    4. ``event`` field verbatim if in the 5-value pass-through set
       (``starting`` / ``complete`` / ``accepted`` / ``finding`` /
       ``test_result``)
    5. ``event`` field via rename table (``acceptance`` → ``accepted``;
       ``test`` → ``test_result``)
    6. Any other ``event`` value (``dispatch`` / ``patch`` / ``review`` /
       etc.) → ``"decision"`` (the catch-all class)
    7. ACCEPTED-in-summary heuristic ONLY when steps 1-6 did not yield
       ``"accepted"`` -- upgrades the projection to ``"accepted"`` when
       the summary carries an uppercase ``ACCEPTED`` token (the
       canonical journal pattern)
    8. Stage / event / summary all empty → ``"decision"`` (fail-closed
       default; the row still carries a slice_id + line number so it
       remains anchorable, but is classed as the generic ``decision``
       event)

    Doc-13:148 names the field as ``event: str`` (free-form); the 13a
    model (after the 13l Literal tightening) accepts only the 7-value
    :data:`JournalEventName` Literal union. The taxonomy here is the
    consumer-side convention -- the typed model NOW enforces it, and
    the parser also projects to the typed set so the Slice-15 metrics
    layer sees a deterministic set.

    Slice 13m (P3-13l-1 closure) -- tightened the return type from
    ``-> str`` to ``-> JournalEventName``. The runtime invariant
    (every projected value is one of the 7 :data:`JournalEventName`
    members) is enforced by control-flow + Pydantic at construction
    (the :attr:`ImplementationArtifactAnchor.event` field has been
    Literal-typed since Slice 13l, so any out-of-bounds projection
    would fail closed with a typed ``ValidationError``). The static
    tightening requires a ``cast(JournalEventName, event_s)`` at the
    pass-through branch (line of step-4 below) because ``event_s`` is
    statically ``str`` (narrowed from ``object`` via ``isinstance``),
    not ``JournalEventName``; the membership-test against the
    :data:`_EVENT_PASSTHROUGH` frozenset (whose elements are themselves
    :data:`JournalEventName`-typed) makes the cast sound at runtime.
    The other branches all assign either a typed :data:`_EVENT_*`
    constant (already :data:`JournalEventName`-typed) or a
    :data:`_EVENT_RENAME` dict value (also typed); no cast needed
    there.
    """

    stage = row.get("stage")
    event = row.get("event")
    summary = row.get("summary", "")

    # Coerce non-string types defensively (the JSONL spec allows any JSON
    # primitive in a field; the typed model expects strings; we accept None
    # / wrong-type and treat as missing).
    stage_s = stage if isinstance(stage, str) else None
    event_s = event if isinstance(event, str) else None
    summary_s = summary if isinstance(summary, str) else ""

    # Per the docstring step 4-5-6 the ``projected`` local is one of:
    # * a typed :data:`JournalEventName` member (assigned from a
    #   :data:`_EVENT_*` constant or a :data:`_EVENT_RENAME` dict value
    #   or a :func:`cast`-narrowed pass-through value); OR
    # * ``None`` while the function is still deciding (steps 1-6 may
    #   leave the projection unset until the step 7-8 fallback fires).
    # The terminal ``return`` is unconditionally a :data:`JournalEventName`
    # member (step 8 fail-closes to :data:`_EVENT_DECISION`).
    projected: JournalEventName | None

    # Step 1-3: stage-based mapping.
    if stage_s is not None:
        if stage_s.endswith("_before"):
            projected = _EVENT_STARTING
        elif stage_s.endswith("_after"):
            projected = _EVENT_COMPLETE
        elif "finding" in stage_s:
            projected = _EVENT_FINDING
        else:
            projected = None
    else:
        projected = None

    # Step 4-5-6: event-based fallback when stage did not classify.
    if projected is None:
        if event_s is None or not event_s:
            # Fall through to the ACCEPTED-summary heuristic + fail-closed
            # default below.
            projected = None
        elif event_s in _EVENT_PASSTHROUGH:
            # Slice 13m (P3-13l-1) -- ``event_s`` is statically ``str``
            # (narrowed from ``object`` via the ``isinstance(event, str)``
            # check above); the membership-test against the
            # :data:`_EVENT_PASSTHROUGH` frozenset (whose elements ARE
            # :data:`JournalEventName`-typed constants) is the runtime
            # guarantee that makes the cast sound. Pydantic's Literal
            # validator at :attr:`ImplementationArtifactAnchor.event`
            # construction re-enforces the invariant.
            projected = cast(JournalEventName, event_s)
        elif event_s in _EVENT_RENAME:
            projected = _EVENT_RENAME[event_s]
        else:
            # Step 6: any unrecognised event value normalises to the
            # catch-all ``decision`` class. Per the user prompt this is the
            # only allowed extension beyond the 13c 6-value taxonomy.
            projected = _EVENT_DECISION

    # Step 7: ACCEPTED-in-summary heuristic. ONLY upgrades the projection
    # when it would otherwise miss the canonical journal pattern (a row
    # whose summary begins ``Slice 13a ACCEPTED ...`` should land
    # event="accepted" even if its raw event was ``acceptance`` -- the
    # rename already handles that, but the heuristic also catches the
    # event=None / event="decision" case where the acceptance signal lives
    # only in the summary text). Per the docstring this fires AFTER the
    # stage / event lookups so it never silently overrides a more-specific
    # mapping.
    #
    # TODO(slice-13e or 15): more discriminating heuristic needed --
    # anchor to start-of-summary or to a ``Slice X ACCEPTED`` shape.
    # The current heuristic over-matches descriptive prose by design
    # (e.g. ``"discussing ACCEPTED versus REJECTED options"`` is also
    # upgraded). The pinning regression test
    # ``test_accepted_keyword_in_descriptive_prose_upgrades_per_documented_heuristic``
    # in tests/test_governance_decision_log_parser.py locks in this
    # behavior so any refinement is a deliberate breaking change.
    # Refinement is out of scope for 13d remediation; it belongs in 13e
    # or 15 when a real downstream consumer needs the tighter mapping.
    if projected != _EVENT_ACCEPTED and projected != _EVENT_FINDING:
        if _ACCEPTED_SUMMARY_RE.search(summary_s):
            projected = _EVENT_ACCEPTED

    # Step 8: stage / event / summary all yielded nothing -- fail-closed
    # default. The row remains anchorable (it still has a slice_id + line
    # number) but is classed as the generic ``decision`` event. This is
    # the only path that could silently degrade if the typed model
    # rejected an empty event; the 13a
    # ``_non_empty_anchor_id_fields`` validator (renamed from
    # ``_non_empty_identifier`` in Slice 13m per P3-13a-3) would catch
    # an empty string, so we explicitly fall back to the ``decision``
    # constant.
    if projected is None:
        projected = _EVENT_DECISION

    return projected


def _resolve_slice_id(row: dict[str, object], *, line_no: int) -> str:
    """Project a raw JSONL row to its canonical slice_id.

    Per STATUS.md § "Next safe action" Chunk-shape point 1: ``slice_id =
    row["sub_slice"]`` when ``sub_slice`` is present and non-empty;
    otherwise ``slice_id = row["slice"]``. Real-data analysis: ``sub_slice``
    is only populated for the 10 most recent governance triad rows; older
    rows use ``slice`` only. The 13a ``_non_empty_anchor_id_fields``
    validator (renamed from ``_non_empty_identifier`` in Slice 13m per
    P3-13a-3) rejects empty strings, so we raise a typed
    :class:`ValueError` with the line number when both fields are
    absent / empty -- fail-closed per the auto-memory
    ``feedback_no_silent_degradation`` rule.
    """

    sub_slice = row.get("sub_slice")
    if isinstance(sub_slice, str) and sub_slice.strip():
        return sub_slice

    slice_field = row.get("slice")
    if isinstance(slice_field, str) and slice_field.strip():
        return slice_field

    raise ValueError(
        f"decision_log_parser: line {line_no} has neither a non-empty "
        f"``sub_slice`` nor a non-empty ``slice`` field; cannot project "
        f"to an ImplementationArtifactAnchor. Fail-closed per the "
        f"no-silent-degradation rule. Row keys: {sorted(row.keys())!r}"
    )


def parse_implementation_decision_log(
    path: Path | str,
    *,
    body: str | None = None,
) -> list[ImplementationArtifactAnchor]:
    """Parse ``implementation-decisions.jsonl`` JSONL into typed anchors.

    The function reads the JSONL body from ``path`` unless ``body`` is
    supplied (the test-only escape hatch -- the ingestor caller in a later
    sub-slice always passes a real path). It produces one
    :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
    per recognised row plus zero-or-more per-finding-owning-slice anchors
    per the 13c P1-13c-R1 discipline. The anchor list is in source-order
    (1-indexed JSONL row order) -- a stable order so downstream metrics
    consumers see a deterministic sequence.

    Per the module docstring § "Stage→event mapping" the parser:

    * Resolves ``slice_id`` per STATUS.md § "Next safe action" Chunk-shape
      point 1 (``sub_slice`` when present, else ``slice``).
    * Maps the canonical 13d event tag per the documented stage→event
      precedence (stage suffix / stage substring / event field /
      ACCEPTED-in-summary heuristic).
    * Sets ``accepted=True`` iff the projected event is ``"accepted"``.
    * Sets ``line_start=None`` per the 13d-vs-13c bidirectional
      distinction (decision-log anchors have no markdown line).
    * Sets ``decision_log_line=<1-indexed-row#>`` per doc-13:147; this is
      the field 13d populates and 13c leaves ``None``.
    * Extracts finding ids from ``row["summary"]``; same-slice findings
      populate the row's ``open_findings``; cross-slice findings emit
      their own ``event="finding"`` anchor with
      ``slice_id=<finding-owning-slice>`` per the 13c P1-13c-R1
      discipline (doc-13:46-47 / doc-13a:24, 109-118).

    :param path: the JSONL source path; recorded into
        :attr:`ImplementationArtifactAnchor.journal_path` (as ``str(path)``)
        whether or not ``body`` is also supplied. Per doc-13:147 the
        ``journal_path`` field is the stable cross-process freshness
        anchor; using ``str(path)`` keeps the contract uniform across
        ``Path`` and pre-stringified inputs. The 13a model is FROZEN for
        13d so the field name is shared between the 13c markdown parser
        and the 13d JSONL parser -- the same field stores either the
        markdown journal path OR the JSONL decision-log path depending
        on which parser emitted the anchor; the bidirectional
        ``13c⊕13d`` distinction (``decision_log_line=None`` ⇔ 13c;
        ``line_start=None`` ⇔ 13d) keeps the two anchor types
        distinguishable at the typed-surface level.
    :param body: optional pre-loaded JSONL body; when ``None`` (the
        production caller path) the function reads ``path`` from disk.
        When supplied, the function does NOT touch the filesystem -- the
        test suite uses this to inject synthetic JSONL without managing
        tmpfiles. The ``path`` argument is still recorded on every
        emitted anchor so the typed anchor cite is stable.
    :returns: a list of :class:`ImplementationArtifactAnchor` rows, in
        source-order. May be empty (empty file / no recognised rows);
        per the fail-closed discipline an empty list is a legitimate
        result, not an error.
    :raises ValueError: per doc-13:184 ("rejects malformed rows" verbatim)
        + the auto-memory ``feedback_no_silent_degradation`` rule:

        * a non-empty line that does not parse as JSON → typed
          :class:`ValueError` with the 1-indexed line number;
        * a line that parses to a non-object (bare list / string /
          number) → typed :class:`ValueError` with the 1-indexed line
          number + the offending JSON type;
        * a row that lacks both ``sub_slice`` and ``slice`` → typed
          :class:`ValueError` with the 1-indexed line number (the 13a
          ``_non_empty_anchor_id_fields`` validator -- renamed from
          ``_non_empty_identifier`` in Slice 13m per P3-13a-3 -- would
          reject an empty slice_id; we raise earlier with a clearer
          message);
        * any other 13a model validation failure on the projected
          anchor fields → the underlying :class:`ValueError` /
          :class:`ValidationError` propagates verbatim.
    """

    # Doc-13:147 -- journal_path is the stable cross-process freshness
    # anchor. Always coerce to str so the 13a model validator sees a
    # non-empty identifier (a bare Path object would still str() to a
    # non-empty value, but the explicit coercion makes the contract clear
    # at the call site).
    journal_path_str = str(path)

    if body is None:
        # Doc-13:184-185 -- the parser is the ingestor's
        # ``ingest_implementation_artifacts`` feeder (paired with the 13c
        # markdown parser), so the production path reads from disk. Per
        # the governance prompt § "Bounded reads" the caller is
        # responsible for budget enforcement; the parser itself is
        # bounded by the JSONL body size which the caller already chose
        # to read.
        body = Path(path).read_text(encoding="utf-8")

    anchors: list[ImplementationArtifactAnchor] = []

    # Split on ``\n`` to preserve 1-indexed line numbers exactly. The
    # JSONL spec says one JSON object per line; blank lines are
    # tolerated (as trailing newlines or visual separation).
    lines = body.split("\n")

    for idx, raw_line in enumerate(lines):
        line_no = idx + 1  # 1-indexed per doc-13:147 (decision_log_line).

        # Class (3): blank / whitespace-only lines are graceful-skip.
        # The JSONL format intentionally allows trailing newlines; the
        # parser does not raise on them. Every non-blank line is gated
        # by class (1) or class (2) below.
        if not raw_line.strip():
            continue

        # Class (2): a non-empty line that does not parse as JSON, or
        # parses to a non-object, raises typed ValueError with the line
        # number. This is the doc-13:184 "rejects malformed rows"
        # verbatim contract + the auto-memory feedback_no_silent_degradation
        # rule.
        try:
            parsed = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(
                f"decision_log_parser: line {line_no} is not valid JSON "
                f"(malformed row per doc-13:184 'rejects malformed rows' "
                f"verbatim contract; fail-closed per the "
                f"no-silent-degradation rule): {exc.msg} at column "
                f"{exc.colno}"
            ) from exc

        if not isinstance(parsed, dict):
            raise ValueError(
                f"decision_log_parser: line {line_no} parses to a "
                f"non-object JSON value (type={type(parsed).__name__!r}); "
                f"every JSONL row must be a JSON object per the doc-13:184 "
                f"rejects-malformed-rows contract."
            )

        # Class (1): row parses to a JSON object. Project to one main
        # anchor + zero-or-more per-finding-owning-slice anchors.
        row: dict[str, object] = parsed

        # Resolve slice_id per STATUS.md § "Next safe action" Chunk-shape
        # point 1; raises typed ValueError when both sub_slice + slice
        # are missing.
        row_slice_id = _resolve_slice_id(row, line_no=line_no)

        # Project the canonical 13d event tag per the documented
        # stage→event precedence.
        row_event = _map_event(row)

        # accepted=True iff the projected event is "accepted" (doc-13:149).
        row_accepted = row_event == _EVENT_ACCEPTED

        # Per-finding-owning-slice attribution (mirrors 13c P1-13c-R1
        # discipline at journal_parser.py:474-507).
        #
        # Doc-13:46-47 (drift / findings as quality signals) +
        # doc-13a:24, 109-118 (no silent loss): each finding id is
        # attributed to its OWN owning slice. Same-slice findings
        # populate the row's open_findings; cross-slice findings emit
        # their own event="finding" anchor with slice_id=<finding-
        # owning-slice>.
        summary_str = row.get("summary", "")
        if not isinstance(summary_str, str):
            summary_str = ""

        # Per-row dedup: 8 real rows contain duplicate finding-id
        # mentions in summary; the 13a _open_findings_dedup_and_non_empty
        # validator at models.py:449-469 would reject duplicates, so we
        # dedup BEFORE construction.
        same_slice_findings: list[str] = []
        same_slice_seen: set[str] = set()
        cross_slice_groups: dict[str, list[str]] = {}
        cross_slice_seen: dict[str, set[str]] = {}

        for finding_match in _FINDING_ID_RE.finditer(summary_str):
            finding_id = finding_match.group("finding_id")
            finding_owning_slice = finding_match.group("slice_id")

            if finding_owning_slice == row_slice_id:
                # Same-slice finding -- contributes to row open_findings.
                if finding_id not in same_slice_seen:
                    same_slice_seen.add(finding_id)
                    same_slice_findings.append(finding_id)
            else:
                # Cross-slice finding -- emit its own anchor under the
                # finding's owning slice (mirrors 13c P1-13c-R1).
                seen = cross_slice_seen.setdefault(finding_owning_slice, set())
                if finding_id not in seen:
                    seen.add(finding_id)
                    cross_slice_groups.setdefault(
                        finding_owning_slice, []
                    ).append(finding_id)

        # Emit the row's main anchor. Per doc-13:143-150 + the module
        # docstring § "13c-vs-13d bidirectional invariant":
        #   * line_start=None (decision log has no markdown line);
        #   * decision_log_line=<1-indexed-row#> (the field 13d
        #     populates per doc-13:147; 13c leaves it None).
        # The 13a _line_positive_when_present field validator at
        # models.py:437-447 rejects line_start < 1; line_start=None is
        # the only fail-closed choice the typed shape accepts.
        anchors.append(
            ImplementationArtifactAnchor(
                slice_id=row_slice_id,
                journal_path=journal_path_str,
                line_start=None,
                decision_log_line=line_no,
                event=row_event,
                accepted=row_accepted,
                open_findings=list(same_slice_findings),
            )
        )

        # Emit one event="finding" anchor per cross-slice
        # finding-owning-slice. Per 13c P1-13c-R1 discipline these
        # anchors carry slice_id=<finding-owning-slice> so the typed
        # cite preserves the correct ownership.
        for owning_slice, finding_ids in cross_slice_groups.items():
            anchors.append(
                ImplementationArtifactAnchor(
                    slice_id=owning_slice,
                    journal_path=journal_path_str,
                    line_start=None,
                    decision_log_line=line_no,
                    event=_EVENT_FINDING,
                    accepted=False,
                    open_findings=list(finding_ids),
                )
            )

    return anchors
