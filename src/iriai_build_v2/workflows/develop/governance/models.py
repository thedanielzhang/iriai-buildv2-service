"""Slice 13a -- pure typed-model scaffolding for the governance package.

This module owns the 8 doc-13 "Proposed Interfaces And Types" pure typed
shapes (``docs/execution-control-plane/13-governance-evidence-model.md:68-151``).
It is the **pure typed-model scaffolding** for the governance layer; it
contains zero executor hooks per doc-13:179 ("Add the governance package with
pure model definitions and no executor hooks").

Per the governance prompt § "Non-Negotiables" the typed shapes here are
analytical / advisory / read-only. They do NOT mutate executor / control-plane
/ product state, take merge or checkpoint authority, force policy activation,
or escalate to broad product repair.

Per the governance prompt § "Slice 13A invariant for downstream slices" the
:class:`GovernanceEvidencePageRef` ``exact: bool`` field is required (no
default value) so every constructor site must declare its exactness claim
explicitly. This is the typed-surface precursor to the Slice 13A acceptance
rule ("Lossy summaries and previews are display-only"); Slice 13A will layer
the cross-cutting :class:`~iriai_build_v2.execution_control...
EvidenceCompleteness` / ``AuthoritativeContextRef`` wrappers on top, and no
governance ingestor that influences dispatch / verify / merge / checkpoint /
route / scheduler / policy may consume this typed model as execution authority
until Slice 13A lands.

The :class:`GovernanceEvidenceIngestor` surface (doc-13:153-171) is OUT OF
SCOPE for Slice 13a; it lands in a later Slice-13 sub-slice once the Slice
13A invariant has settled in the typed model.

The pydantic v2 idiom here matches
:mod:`iriai_build_v2.execution_control.atomic_landing` (Slice 12b):
``Literal`` enums at module head; ``BaseModel`` subclasses with
``field_validator`` / ``model_validator(mode="after")`` for invariants;
``Field(default_factory=list)`` / ``Field(default_factory=dict)`` for
container defaults so independent instances do not share mutable state.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal

# Per pydantic v2's documented requirement (see
# ``https://errors.pydantic.dev/2.12/u/typed-dict-version``) the Pydantic
# schema generator requires ``typing_extensions.TypedDict`` (not
# ``typing.TypedDict``) on Python < 3.12 so the per-key annotations land
# in the runtime schema. The project still targets Python 3.11+; using
# the typing_extensions backport ensures Pydantic can build the
# annotated-schema for the field-level TypedDict references at
# ``GovernanceEvidenceSet.source_window`` +
# ``GovernanceEvidencePageRef.stale_check``.
from typing_extensions import TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


__all__ = [
    # Literal enums (doc-13:74-87).
    "EvidenceAuthority",
    "EvidenceQuality",
    "CompletenessState",
    # The 5 doc-13 typed shapes (doc-13:89-151).
    "GovernanceReadBudget",
    "GovernanceEvidencePageRef",
    "GovernanceEvidenceRef",
    "GovernanceEvidenceSet",
    "ImplementationArtifactAnchor",
    # Slice 13m (P3-13a-4 closure) -- TypedDict tightenings of the 13a
    # ``source_window`` + ``stale_check`` ``dict[str, Any]`` fields.
    "EvidenceSetSourceWindow",
    "EvidencePageRefStaleCheck",
]
# Slice 13l note: ``JournalEventName`` (defined below) is intentionally
# NOT in ``__all__``. The strict-equality assertion at
# ``tests/test_governance_evidence_models.py:213`` pins the models
# module's __all__ to exactly the 8 doc-13:68-151 typed shapes (plus
# the 2 Slice 13m TypedDict tightenings = 10 total post-13m); the
# parallel package-level strict-equality assertion at
# ``tests/test_governance_evidence_models.py:221`` pins the 22 doc-13
# exports (24 post-13m). ``JournalEventName`` is an internal typed
# surface consumed by the two sibling parser modules
# (``journal_parser`` + ``decision_log_parser``) via direct
# ``from .models import JournalEventName`` imports. Per the auto-memory
# ``feedback_no_overengineer_use_library`` rule we keep the module
# surface narrow and use Python's native module-attribute discovery
# rather than minting a new public name.


# --- Literal enums (doc-13:74-87) -------------------------------------------


EvidenceAuthority = Literal[
    "typed_journal",
    "compatibility_projection",
    "git_provenance",
    "implementation_journal",
    "implementation_decision_log",
    "supervisor_digest",
    "resource_snapshot",
    "legacy_event",
    "legacy_artifact_summary",
]
"""Doc-13:74-84 -- the 9 source-of-truth tags for governance evidence.

The doc lists typed-first authorities (``typed_journal``,
``compatibility_projection``, ``git_provenance``, ``implementation_journal``,
``implementation_decision_log``, ``supervisor_digest``, ``resource_snapshot``)
and the two legacy-fallback authorities (``legacy_event``,
``legacy_artifact_summary``) that Slice-15 confidence scoring uses to
penalise legacy-heavy or incomplete typed evidence (doc-13:173-175)."""


EvidenceQuality = Literal[
    "canonical",
    "derived",
    "sampled",
    "advisory",
    "stale",
    "insufficient",
]
"""Doc-13:86 -- the 6-value evidence-quality enum.

Mixed typed/legacy evidence is encoded as ``quality="derived"`` plus the
:attr:`GovernanceEvidenceSet.source_mix` map, not as a separate
``EvidenceQuality`` literal (doc-13:173-175)."""


CompletenessState = Literal[
    "complete",
    "paged",
    "preview_only",
    "unavailable",
]
"""Doc-13:87 -- the 4-value completeness-state enum.

The Slice 13A invariant treats ``preview_only`` as display-only: anything
that can influence dispatch / verify / merge / checkpoint / route / scheduler
/ policy must consume ``complete`` or ``paged`` evidence (per the governance
prompt § "Slice 13A invariant for downstream slices")."""


# --- Slice 13l: typed event taxonomy (P3-13c-1 + P3-13d-1 closure) ----------
#
# Doc-13:148 spells :attr:`ImplementationArtifactAnchor.event` verbatim as
# ``event: str`` (free-form string). The 13c implementation-journal parser
# (``journal_parser.py:151-156``) emits a 6-value vocabulary (``starting``
# / ``complete`` / ``accepted`` / ``finding`` / ``test_result`` /
# ``subagent``); the 13d JSONL decision-log parser
# (``decision_log_parser.py:199-204``) emits the same 6 plus a 7th
# ``decision`` catch-all (since the decision log has rich event vocabulary
# the 13c markdown parser never sees). Together the union is **7 values**.
#
# 13c carried the gap as **P3-13c-1** (event constants as module-level
# ``str`` not typed ``Literal``); 13d carried the 7th-value mismatch as
# **P3-13d-1** (the 13d ``decision`` value not in the 13c 6-value
# taxonomy, so the bidirectional ``13c ⊕ 13d`` typed surface had no
# unifying type). Slice 13l closes both by widening to the true 7-value
# union via this typed Literal alias.
#
# Per the auto-memory ``feedback_no_silent_degradation`` rule, tightening
# :attr:`ImplementationArtifactAnchor.event` from ``str`` to
# :data:`JournalEventName` makes Pydantic re-validate at construction;
# any anchor constructed with a value outside the 7 fails closed with a
# typed ``ValidationError``. This is mechanical type-narrowing only --
# no parser emits any value outside the 7-value set today, so no
# behavior change is expected; the Literal is a typed-surface assertion
# of the existing emit contract.
JournalEventName = Literal[
    # 13c implementation-journal parser values (P3-13c-1 closure):
    # 6 module-level constants at ``journal_parser.py:151-156``.
    "starting",
    "complete",
    "accepted",
    "finding",
    "test_result",
    "subagent",
    # 13d JSONL decision-log parser value (P3-13d-1 closure):
    # the 7th value at ``decision_log_parser.py:204``; the decision log
    # has a richer source vocabulary (``dispatch``, ``patch``, ``review``,
    # etc.) that the 13d parser normalises to this catch-all class.
    "decision",
]
"""Slice 13l -- the **7-value union** event taxonomy for
:attr:`ImplementationArtifactAnchor.event`.

This Literal is the typed-surface contract both parsers populate:

* The 13c implementation-journal parser
  (:mod:`iriai_build_v2.workflows.develop.governance.journal_parser`)
  emits 6 values: ``starting`` / ``complete`` / ``accepted`` /
  ``finding`` / ``test_result`` / ``subagent``.
* The 13d JSONL decision-log parser
  (:mod:`iriai_build_v2.workflows.develop.governance.decision_log_parser`)
  emits the same 6 plus ``decision`` (the catch-all class for the rich
  decision-log source vocabulary).

Together = **7 values**. The
:attr:`ImplementationArtifactAnchor.event` field is typed against this
union; Pydantic fails closed on any out-of-bounds value at construction.

Slice 13l closes deferred carries **P3-13c-1** (event constants as
module-level ``str`` rather than typed ``Literal``) and **P3-13d-1**
(the 7th-value taxonomy mismatch between 13c's 6-value module
constants and 13d's 7-value set including ``decision``). Mechanical
type-narrowing only: no parser emits any value outside the 7-value
set today, so no behavior change is expected; the Literal is the
typed-surface assertion of the existing emit contract."""


# --- Slice 13A first-sub-slice finalizer: shared finding-ID regex ----------
#
# Per the reviewer P2-A3-1 (Slice 13A first sub-slice finalizer) the
# canonical journal-parser finding-ID grammar
# (``P[123]-<slice>-<n>`` with required trailing ``-N`` index segment) is
# the single source of truth for what counts as a real finding ID across
# the governance layer. Prior to this finalizer the same grammar was
# duplicated locally in three sibling modules:
#
# * ``journal_parser._FINDING_ID_RE`` -- the canonical 3-severity strict
#   form (``P[123]-<slice>-<n>`` with required trailing ``-N`` index).
# * ``decision_log_parser._FINDING_ID_RE`` -- byte-identical copy of the
#   journal_parser regex.
# * ``completeness_scanner._P1_P2_FINDING_ID_RE`` -- a LAXER 2-severity
#   variant (``P[12]-<scope>`` with OPTIONAL ``-<seq>``) that admitted
#   four classes of pure-regex-noise false positives on the live corpus:
#   ``P1-P2`` (from phrases like "0 P1 / 0 P2"), ``P1-RISK`` / ``P2-RISK``
#   (from historical reviewer prose), ``P1-finding`` / ``P2-finding``
#   (from descriptive sentences "the P2-finding above ...").
#
# Per the Slice 13A first-sub-slice reviewer P2-A3-1 the three sibling
# modules now consume this shared constant; the scanner additionally
# filters consumed matches to P1/P2 only at the match-result level
# (criterion (e) per doc-13:253-254 only treats P1/P2 as blocking; P3 is
# maintainability/clarity per ``IMPLEMENTATION_PROMPT_GOVERNANCE.md:206-216``
# and explicitly NOT blocking).
#
# The regex is identical to the prior ``journal_parser._FINDING_ID_RE``
# definition verbatim -- the only change is its module home. Per the
# auto-memory ``feedback_no_refactor`` rule the regex source string is
# preserved byte-for-byte from the journal_parser canonical form to
# avoid silently introducing a grammar drift.
#
# Per the ``feedback_no_overengineer_use_library`` rule + the
# ``JournalEventName`` precedent above, this constant is **internal**:
# it is NOT in :data:`__all__` because it is consumed only by the three
# sibling modules in this package (``journal_parser`` +
# ``decision_log_parser`` + ``completeness_scanner``) via direct
# ``from .models import FINDING_ID_REGEX`` imports. The
# strict-equality assertion at
# ``tests/test_governance_evidence_models.py:213`` pins the models
# module's ``__all__`` to the doc-13:68-151 typed shapes only; this
# regex is an internal typed-surface helper, not a doc-13 exported
# contract.
FINDING_ID_REGEX: re.Pattern[str] = re.compile(
    r"\b(?P<finding_id>P[123]-(?P<slice_id>\d{1,3}[a-z]?(?:-[\da-z]+)*)-\d+)\b"
)
"""Slice 13A first-sub-slice finalizer (P2-A3-1 closure) -- the shared
canonical finding-ID regex consumed by the three governance sibling
modules:

* :mod:`~iriai_build_v2.workflows.develop.governance.journal_parser`
  (markdown bullet + heading body finding-ID extraction).
* :mod:`~iriai_build_v2.workflows.develop.governance.decision_log_parser`
  (JSONL summary-field finding-ID extraction).
* :mod:`~iriai_build_v2.workflows.develop.governance.completeness_scanner`
  (criterion (e) unresolved-P1/P2 detection; the scanner filters
  consumed matches to P1/P2 only at the match-result level since
  doc-13:253-254 criterion (e) only treats P1/P2 as blocking).

Grammar: ``P[123]-<slice>-<n>`` where ``<slice>`` is the same
1-to-3-digit-plus-optional-letter-suffix-plus-optional-sub-id form the
journal parser recognises (e.g. ``13a``, ``08e-3a``, ``11d``); ``<n>``
is the trailing index integer. The named groups are:

* ``finding_id`` -- the full ``P[123]-<slice>-<n>`` string (e.g.
  ``P1-13b-1``, ``P2-V2-1``, ``P3-08e-3a-1``).
* ``slice_id`` -- the owning slice segment (e.g. ``13b``, ``V2``,
  ``08e-3a``).

The word-boundary anchors ``\\b`` reject the four pure-regex-noise
classes the prior scanner-local laxer regex admitted: ``P1-P2`` (no
trailing ``-N`` index), ``P1-RISK`` (no trailing ``-N`` index),
``P1-finding`` (no trailing ``-N`` index), ``P2-finding`` (same).
The defensive regex test at
``tests/test_governance_completeness_scanner.py::test_finding_id_regex_rejects_known_pure_regex_noise_false_positives``
pins this rejection."""


# --- Slice 13m: TypedDict tightenings (P3-13a-4 closure) --------------------
#
# Doc-13:113-126 spells :attr:`GovernanceEvidencePageRef.stale_check` and
# doc-13:128-141 spells :attr:`GovernanceEvidenceSet.source_window`
# verbatim as ``dict[str, Any]``. Neither doc passage enumerates the
# accepted keys today; per the doc-13 wording the keys are extensible
# producer-side metadata (file mtime, commit hash, row version, cursor
# bounds, ISO timestamps, selector filters, etc.). Slice 13a carried
# this as **P3-13a-4** (cosmetic type-narrowing gap; the typed surface
# was permissive ``dict[str, Any]`` rather than a TypedDict capturing
# the observed key set).
#
# Slice 13m closes the carry by introducing two ``TypedDict`` shapes
# with ``total=False`` (every key optional). The ``total=False`` choice
# is **deliberately conservative**: doc-13:113-126 + doc-13:128-141 do
# NOT enumerate keys, so making any key required would be a doc
# overreach. The TypedDicts name the keys observed across the 13a-13l
# implementer + test fixture surfaces; future fields can be added by
# extending the class without breaking the typed-surface contract.
#
# Per the auto-memory ``feedback_no_silent_degradation`` rule, the
# Pydantic v2 runtime continues to admit any dict shape (since
# ``TypedDict`` is a static-typing-only construct that Pydantic v2
# treats permissively for ``total=False`` cases); the upgrade is a
# discoverability + tooling improvement rather than a runtime
# tightening. Pydantic re-validates the OUTER container on
# construction; the TypedDict-typed annotation makes the documented
# producer-side keys discoverable to static analysis without changing
# the runtime accept-any behavior.
#
# Keys grepped from the 13a-13l test + production surfaces:
#
# * :class:`EvidenceSetSourceWindow` (5 keys) -- the
#   ``GovernanceWindow.model_dump(mode="json")`` projection at
#   ``evidence_set.py:903`` + the equivalent inline dict at
#   ``ingestor.py:509-515`` populate these. Test fixtures additionally
#   exercise free-form keys like ``window_start`` / ``window_end`` /
#   ``cursor`` / ``z_key`` / ``a_key`` -- ``total=False`` plus the
#   permissive Pydantic runtime semantics admit those without
#   complaint.
# * :class:`EvidencePageRefStaleCheck` (12 keys) -- the production
#   page-ref constructors at ``ingestor.py:908`` + ``ingestor.py:1079-1083``
#   + ``ingestor.py:1274-1278`` + ``ingestor.py:1398-1401`` +
#   ``ingestor.py:1481-1484`` populate the producer-side keys
#   (``ref_digest`` / ``source_row_count`` / ``limit_cap`` /
#   ``observed_row_count`` / ``authority`` / ``row_byte_length`` /
#   ``max_chars_per_ref``); test fixtures additionally exercise
#   ``file_mtime`` / ``mtime`` / ``commit`` / ``row_version`` /
#   ``freshness``.
#
# Both TypedDicts are exported via ``models.__all__`` + the package
# ``__init__.__all__`` so consumers can ``from
# iriai_build_v2.workflows.develop.governance import
# EvidenceSetSourceWindow`` for type annotations on their own
# producer-side helpers.


class EvidenceSetSourceWindow(TypedDict, total=False):
    """TypedDict tightening for
    :attr:`GovernanceEvidenceSet.source_window` (P3-13a-4 closure).

    Doc-13:128-141 spells the field as ``dict[str, Any]``; this
    TypedDict names the 5 keys observed in the 13a-13l producer + test
    surfaces. All keys are optional (``total=False``) per the
    conservative interpretation of doc-13:128-141 wording that does
    NOT enumerate the accepted keys; future fields can be added by
    extending the class.

    The ``__pydantic_config__`` ``extra="allow"`` setting preserves
    the doc-13:133 ``dict[str, Any]`` dict-permissive runtime
    behavior: producers can populate ANY key (not just the 5
    documented ones), and Pydantic will admit the dict without
    complaint. The TypedDict serves as a static-analysis +
    documentation tool naming the documented producer-side keys,
    NOT as a runtime gate. This preserves backward compatibility
    with the 13a-13l producer + test surfaces (which exercise
    free-form keys like ``window_start`` / ``window_end`` / ``cursor``
    / ``z_key`` / ``a_key``).

    The 5 documented keys map verbatim to the
    :class:`~iriai_build_v2.workflows.develop.governance.ingestor.GovernanceWindow`
    fields the producer projects via ``model_dump(mode="json")`` (see
    ``evidence_set.py:903`` + ``ingestor.py:509-515``).
    """

    # Per pydantic v2 ``extra='allow'`` on a TypedDict admits keys
    # beyond the documented ones; the 13a-13l producer + test surfaces
    # exercise free-form keys (see the test fixtures at
    # ``tests/test_governance_evidence_models.py:537`` etc.). This
    # preserves the doc-13:133 ``dict[str, Any]`` permissive runtime
    # behavior; the TypedDict is a static-analysis tool, not a
    # runtime gate.
    __pydantic_config__ = ConfigDict(extra="allow")

    start_cursor: str | None
    end_cursor: str | None
    start_iso: str | None
    end_iso: str | None
    selectors: dict[str, Any]


class EvidencePageRefStaleCheck(TypedDict, total=False):
    """TypedDict tightening for
    :attr:`GovernanceEvidencePageRef.stale_check` (P3-13a-4 closure).

    Doc-13:113-126 spells the field as ``dict[str, Any]``; this
    TypedDict names the 12 keys observed in the 13a-13l producer + test
    surfaces (7 producer-side keys from the 5 ``GovernanceEvidencePageRef``
    constructor sites in ``ingestor.py`` + 5 test-fixture keys from
    ``tests/test_governance_evidence_models.py`` +
    ``tests/test_governance_evidence_set_digester.py`` +
    ``tests/test_governance_postgres_evidence_store.py``). All keys
    are optional (``total=False``) per the conservative interpretation
    of doc-13:113-126 wording that does NOT enumerate the accepted
    keys; future fields can be added by extending the class.

    The ``__pydantic_config__`` ``extra="allow"`` setting preserves
    the doc-13:126 ``dict[str, Any]`` dict-permissive runtime
    behavior: producers can populate ANY key (not just the 12
    documented ones), and Pydantic will admit the dict without
    complaint. The TypedDict serves as a static-analysis +
    documentation tool naming the documented producer-side keys,
    NOT as a runtime gate.

    Producer-side keys (from ``ingestor.py`` construction sites):

    * ``ref_digest`` -- the ref's content digest (resolve_ref lane).
    * ``source_row_count`` -- the count of rows the slice was derived
      from (resolve_ref lane).
    * ``limit_cap`` -- the bounded-read cap that was tripped
      (cap-overflow lanes).
    * ``observed_row_count`` -- the row count past the cap
      (cap-overflow lanes).
    * ``authority`` -- the :data:`EvidenceAuthority` that overflowed
      (cap-overflow lanes).
    * ``row_byte_length`` -- the raw row body byte length
      (typed_journal / supervisor / resource lanes).
    * ``max_chars_per_ref`` -- the per-ref char cap that was applied
      (typed_journal / supervisor / resource lanes).

    Test-fixture keys (test surfaces only):

    * ``file_mtime`` -- file modification timestamp.
    * ``mtime`` -- modification timestamp (variant).
    * ``commit`` -- git commit SHA.
    * ``row_version`` -- typed row version sentinel.
    * ``freshness`` -- free-form freshness tag.
    """

    # Per pydantic v2 ``extra='allow'`` on a TypedDict admits keys
    # beyond the documented ones; the 13a-13l producer + test surfaces
    # exercise free-form keys (see the test fixtures at
    # ``tests/test_governance_evidence_models.py:358`` etc.). This
    # preserves the doc-13:126 ``dict[str, Any]`` permissive runtime
    # behavior; the TypedDict is a static-analysis tool, not a
    # runtime gate.
    __pydantic_config__ = ConfigDict(extra="allow")

    ref_digest: str
    source_row_count: int
    limit_cap: int
    observed_row_count: int
    authority: str
    row_byte_length: int
    max_chars_per_ref: int
    file_mtime: float
    mtime: float
    commit: str
    row_version: int
    freshness: str


# --- Pure typed shapes (doc-13:89-151) --------------------------------------


class GovernanceReadBudget(BaseModel):
    """Bounded-read budget for the governance evidence layer (doc-13:89-95).

    Every default integer below is the doc-13 verbatim default. Per the
    governance prompt § "Bounded reads" these caps reuse the typed snapshot's
    ``LIMIT cap+1`` truncation discipline and the supervisor's
    ``SET LOCAL statement_timeout`` pattern, so the typed shape is the
    bounded-read contract that governance ingestors (deferred to a later
    sub-slice) honour.

    All six fields are validated as positive integers: a zero or negative
    budget would silently disable the bounded-read discipline, which the
    governance prompt § "Non-Negotiables" rule "no silent degradation"
    forbids.
    """

    # Per the sibling executor models at
    # src/iriai_build_v2/workflows/develop/execution/verification.py:74 and
    # src/iriai_build_v2/workflows/develop/execution/failure_router.py:576:
    # unknown fields fail closed so typo-d kwargs raise ValidationError
    # instead of being silently absorbed.
    model_config = ConfigDict(extra="forbid")

    max_event_rows: int = 500
    """Doc-13:90 -- maximum event rows per ingest call."""

    max_artifact_summary_rows: int = 5_000
    """Doc-13:91 -- maximum artifact-summary rows per ingest call."""

    max_ref_resolutions: int = 20
    """Doc-13:92 -- maximum ``resolve_ref`` calls per ingest call."""

    max_chars_per_ref: int = 40_000
    """Doc-13:93 -- maximum characters returned per resolved ref."""

    max_serialized_output_bytes: int = 2_000_000
    """Doc-13:94 -- maximum serialised governance-evidence-set bytes."""

    statement_timeout_ms: int = 10_000
    """Doc-13:95 -- ``SET LOCAL statement_timeout`` value in ms."""

    @field_validator(
        "max_event_rows",
        "max_artifact_summary_rows",
        "max_ref_resolutions",
        "max_chars_per_ref",
        "max_serialized_output_bytes",
        "statement_timeout_ms",
    )
    @classmethod
    def _positive_budget(cls, value: int) -> int:
        # Fail closed: a zero / negative budget would silently disable the
        # bounded-read discipline the governance prompt § "Bounded reads"
        # mandates. Per the auto-memory "no silent degradation" rule we raise
        # a typed ValidationError instead of coercing.
        if value <= 0:
            raise ValueError(
                "GovernanceReadBudget fields must be positive integers "
                "(doc-13:89-95 bounded-read contract)"
            )
        return value


class GovernanceEvidencePageRef(BaseModel):
    """Paged-exact descriptor for one page of a governance evidence ref.

    Per doc-13:113-126 the page ref carries optional byte / line / item
    ranges plus the required ``digest``, ``completeness``, and ``exact``
    fields, plus a ``stale_check`` mapping the deferred ingestor surface
    populates with stable cross-process freshness metadata (e.g. file
    mtime, commit hash, row version).

    **Slice 13A invariant precursor (governance prompt § "Slice 13A
    invariant for downstream slices").** ``exact`` is a REQUIRED bool with
    NO default. Every constructor must declare its exactness claim
    explicitly so a preview-only page cannot be silently misread as exact
    at the typed-surface boundary. Slice 13A will add the cross-cutting
    ``EvidenceCompleteness`` / ``AuthoritativeContextRef`` wrappers on
    top; Slice 13a does NOT pre-empt that work.
    """

    # extra='forbid' aligns with the sibling executor models at
    # workflows/develop/execution/verification.py:74 /
    # failure_router.py:576 -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    page_ref_id: str
    authority: EvidenceAuthority
    source_ref_id: str
    byte_start: int | None = None
    byte_end: int | None = None
    line_start: int | None = None
    line_end: int | None = None
    item_start: int | None = None
    item_end: int | None = None
    digest: str
    completeness: CompletenessState
    exact: bool
    """Doc-13:125 -- REQUIRED bool, no default. Slice 13A invariant
    precursor: every construction site must declare exactness explicitly."""

    stale_check: EvidencePageRefStaleCheck | None = None
    """Slice 13m (P3-13a-4 closure) -- tightened from the doc-13:126
    ``dict[str, Any]`` shape to the typed
    :class:`EvidencePageRefStaleCheck` TypedDict (with ``| None`` to
    preserve nullability for producer sites that omit the field
    entirely). The ``total=False`` TypedDict keeps all keys optional
    per the conservative interpretation of doc-13:113-126 wording
    that does NOT enumerate the accepted keys; runtime semantics are
    unchanged (Pydantic v2 admits any dict shape for the TypedDict-
    typed annotation, preserving the doc-13:126 dict-permissive
    runtime behavior). The default ``= None`` preserves the legacy
    no-stale-check construction path."""

    @field_validator("page_ref_id", "source_ref_id", "digest")
    @classmethod
    def _non_empty_page_ref_id_fields(cls, value: str) -> str:
        # Slice 13m (P3-13a-3 closure) -- renamed from the original
        # ``_non_empty_identifier`` to a per-class name to eliminate
        # the cross-class name collision (4 classes in this module
        # previously defined a classmethod literally named
        # ``_non_empty_identifier``; the rename makes each per-class
        # validator individually discoverable). The classmethod is
        # private (single-underscore prefix) + accessed only via
        # Pydantic's ``field_validator`` decorator registration; the
        # rename is safe + has no external import surface.
        #
        # An empty page-ref-id / source-ref-id / digest defeats the
        # cross-process freshness contract the deferred ingestor
        # surface relies on (doc-13:124, "digest").
        if not value or not value.strip():
            raise ValueError(
                "GovernanceEvidencePageRef page_ref_id / source_ref_id / "
                "digest must be non-empty (doc-13:113-126)"
            )
        return value

    # Slice 13A invariant -- doc-13a:24, 109-118: lossy summaries / previews
    # are display-only and cannot satisfy authoritative consumers, so the
    # ``exact`` flag and the ``completeness`` tag must agree.
    @model_validator(mode="after")
    def _exact_completeness_consistency(self) -> "GovernanceEvidencePageRef":
        if self.exact and self.completeness not in ("complete", "paged"):
            raise ValueError(
                "GovernanceEvidencePageRef exact=True requires "
                "completeness in {'complete', 'paged'} "
                f"(got completeness={self.completeness!r}) -- Slice 13A "
                "invariant precursor (doc-13a:24, 109-118)"
            )
        if self.completeness == "preview_only" and self.exact:
            raise ValueError(
                "GovernanceEvidencePageRef completeness='preview_only' "
                "requires exact=False (preview pages are display-only) -- "
                "Slice 13A invariant precursor (doc-13a:24, 109-118)"
            )
        return self


class GovernanceEvidenceRef(BaseModel):
    """Per-ref descriptor for one governance evidence record (doc-13:97-111).

    Each ref cites a stable typed-source id (typed_journal id / compatibility
    projection id / git provenance ref / implementation-log anchor) per
    doc-13:248 "Every governance evidence set cites stable typed ids,
    compatibility projection ids, Git provenance refs, or implementation-log
    anchors."

    ``page_refs`` is the exact paged-evidence list when ``completeness`` is
    ``"paged"``; it is the empty list when ``completeness`` is
    ``"complete"`` (single-page evidence) or ``"unavailable"`` /
    ``"preview_only"`` (no paged evidence exists).

    ``preview_only=True`` flags refs that exist solely for display; per the
    governance prompt § "Slice 13A invariant for downstream slices" no
    authoritative consumer may act on a preview-only ref.
    """

    # extra='forbid' aligns with the sibling executor models at
    # workflows/develop/execution/verification.py:74 /
    # failure_router.py:576 -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    authority: EvidenceAuthority
    ref_id: str
    feature_id: str | None = None
    slice_id: str | None = None
    artifact_id: int | None = None
    event_id: int | None = None
    commit_hash: str | None = None
    journal_anchor: str | None = None
    created_at: datetime | None = None
    digest: str
    quality: EvidenceQuality
    completeness: CompletenessState
    page_refs: list[GovernanceEvidencePageRef] = Field(default_factory=list)
    preview_only: bool = False
    """Doc-13:111 -- defaults to False; Slice 13A invariant: preview-only
    refs are display-only and never satisfy authoritative consumers."""

    @field_validator("ref_id", "digest")
    @classmethod
    def _non_empty_ref_id_fields(cls, value: str) -> str:
        # Slice 13m (P3-13a-3 closure) -- renamed from the original
        # ``_non_empty_identifier`` to a per-class name to eliminate
        # the cross-class name collision. See the matching note on
        # :meth:`GovernanceEvidencePageRef._non_empty_page_ref_id_fields`.
        if not value or not value.strip():
            raise ValueError(
                "GovernanceEvidenceRef ref_id / digest must be non-empty "
                "(doc-13:97-111 typed-source contract)"
            )
        return value

    # Slice 13A invariant -- doc-13a:24, 109-118: preview-only refs are
    # display-only, so the ``preview_only`` flag and the ``completeness``
    # tag must agree bidirectionally; otherwise a later sub-slice could
    # admit a preview-only ref to an authoritative consumer based on the
    # completeness tag alone (or vice versa).
    @model_validator(mode="after")
    def _preview_only_completeness_consistency(self) -> "GovernanceEvidenceRef":
        if self.preview_only and self.completeness != "preview_only":
            raise ValueError(
                "GovernanceEvidenceRef preview_only=True requires "
                "completeness='preview_only' "
                f"(got completeness={self.completeness!r}) -- Slice 13A "
                "invariant (doc-13a:24, 109-118)"
            )
        if self.completeness == "preview_only" and not self.preview_only:
            raise ValueError(
                "GovernanceEvidenceRef completeness='preview_only' requires "
                "preview_only=True -- Slice 13A invariant "
                "(doc-13a:24, 109-118)"
            )
        return self


class GovernanceEvidenceSet(BaseModel):
    """Corpus-level governance evidence container (doc-13:128-141).

    Identified by an ``idempotency_key`` (per doc-13:129 — a deterministic
    digest the deferred ingestor surface computes from sorted canonical
    JSON, doc-13:182-185). The set is the unit the Slice-15 metrics layer
    and Slice-16 finding engine read from; doc-13:206-220 specifies the
    "partial evidence set" semantics enforced here:

    * ``read_budget_exhausted=True`` records that the bounded-read budget
      tripped during ingest (doc-13:217-220);
    * ``omitted_refs`` carries the exact :class:`GovernanceEvidencePageRef`
      objects the ingest could not include (doc-13:218);
    * ``blockers`` lists the gating reasons that prevent downstream
      governance consumers from treating the set as authoritative.

    ``source_mix`` is the per-authority count map Slice-15 confidence
    scoring uses to penalise legacy-heavy or incomplete typed evidence
    (doc-13:173-175). Its keys are :data:`EvidenceAuthority` values.
    """

    # extra='forbid' aligns with the sibling executor models at
    # workflows/develop/execution/verification.py:74 /
    # failure_router.py:576 -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    idempotency_key: str
    feature_id: str | None
    corpus_id: str
    generated_at: datetime
    source_window: EvidenceSetSourceWindow
    """Slice 13m (P3-13a-4 closure) -- tightened from the doc-13:133
    ``dict[str, Any]`` shape to the typed
    :class:`EvidenceSetSourceWindow` TypedDict. The ``total=False``
    TypedDict keeps all keys optional per the conservative
    interpretation of doc-13:128-141 wording that does NOT enumerate
    the accepted keys; runtime semantics are unchanged (Pydantic v2
    admits any dict shape for the TypedDict-typed annotation,
    preserving the doc-13:133 dict-permissive runtime behavior). The
    producer-side ``GovernanceWindow.model_dump(mode="json")``
    projection at ``evidence_set.py:903`` populates the documented 5
    keys verbatim."""
    refs: list[GovernanceEvidenceRef]
    omitted_refs: list[GovernanceEvidencePageRef]
    completeness: CompletenessState
    source_mix: dict[EvidenceAuthority, int] = Field(default_factory=dict)
    read_budget: GovernanceReadBudget
    read_budget_exhausted: bool = False
    quality: EvidenceQuality
    blockers: list[str]

    @field_validator("idempotency_key", "corpus_id")
    @classmethod
    def _non_empty_set_id_fields(cls, value: str) -> str:
        # Slice 13m (P3-13a-3 closure) -- renamed from the original
        # ``_non_empty_identifier`` to a per-class name to eliminate
        # the cross-class name collision. See the matching note on
        # :meth:`GovernanceEvidencePageRef._non_empty_page_ref_id_fields`.
        #
        # Doc-13:129/131 -- idempotency_key + corpus_id are the stable
        # identities the deferred ingestor surface uses to dedupe and
        # reconstruct evidence sets. Empty strings would silently collide.
        if not value or not value.strip():
            raise ValueError(
                "GovernanceEvidenceSet idempotency_key / corpus_id must be "
                "non-empty (doc-13:128-141 corpus-identity contract)"
            )
        return value

    @field_validator("source_mix")
    @classmethod
    def _source_mix_counts_non_negative(
        cls, value: dict[str, int]
    ) -> dict[str, int]:
        # Doc-13:137 -- source_mix is a per-authority COUNT map; negative
        # counts are a typed-surface contract violation, not a recoverable
        # state.
        for authority, count in value.items():
            if count < 0:
                raise ValueError(
                    f"GovernanceEvidenceSet source_mix[{authority!r}]={count} "
                    "must be >= 0 (doc-13:137 per-authority count map)"
                )
        return value


class ImplementationArtifactAnchor(BaseModel):
    """Per-slice anchor into the governance-side projection of the
    implementation journal + decision log (doc-13:143-150).

    Doc-13:46-47 makes the implementation journal and the decision log
    first-class governance evidence ("plan-vs-actual drift, reviewer
    findings, accepted deviations, and test evidence are themselves
    workflow quality signals"). Each anchor records the slice id, the
    journal path, the optional line-start anchor into the markdown
    journal, the optional decision-log JSONL line number, the canonical
    event tag, an ``accepted`` flag, and a list of open finding ids.

    ``open_findings`` is the deduplicated list of P1 / P2 / P3 finding
    ids still tracked by the slice — empty when the slice is fully
    accepted with no open findings. Duplicates are rejected so the anchor
    is canonical.
    """

    # extra='forbid' aligns with the sibling executor models at
    # workflows/develop/execution/verification.py:74 /
    # failure_router.py:576 -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    slice_id: str
    journal_path: str
    line_start: int | None
    decision_log_line: int | None
    event: JournalEventName
    """Slice 13l (P3-13c-1 + P3-13d-1 closure) -- tightened from the
    doc-13:148 ``event: str`` shape to the typed 7-value
    :data:`JournalEventName` Literal alias defined above. Pydantic
    re-validates at construction; any anchor constructed with a value
    outside the 7-value union (``starting`` / ``complete`` / ``accepted``
    / ``finding`` / ``test_result`` / ``subagent`` / ``decision``) fails
    closed with a typed ``ValidationError`` per the auto-memory
    ``feedback_no_silent_degradation`` rule. Mechanical type-narrowing
    only: no parser emits any value outside the 7-value set today, so no
    behavior change is expected; the Literal is the typed-surface
    assertion of the existing emit contract."""
    accepted: bool
    open_findings: list[str]

    @field_validator("slice_id", "journal_path")
    @classmethod
    def _non_empty_anchor_id_fields(cls, value: str) -> str:
        # Slice 13m (P3-13a-3 closure) -- renamed from the original
        # ``_non_empty_identifier`` to a per-class name to eliminate
        # the cross-class name collision. See the matching note on
        # :meth:`GovernanceEvidencePageRef._non_empty_page_ref_id_fields`.
        #
        # Slice 13m (P3-13l-2 closure, bundled with P3-13a-3) -- the
        # ``event`` field is REMOVED from the validator's field tuple.
        # Per the Slice 13l Literal tightening
        # (:attr:`ImplementationArtifactAnchor.event: JournalEventName`),
        # Pydantic's Literal validator runs FIRST and rejects empty /
        # whitespace strings with its own typed ``ValidationError``
        # (the 7-value union does NOT admit ``""`` / ``"   "`` / any
        # whitespace-bearing token). The previous ``_non_empty_identifier``
        # body was a documented no-op on the ``event`` field; the
        # Slice 13l in-file comment noted this and kept the field for
        # symmetry. Slice 13m narrows the field tuple to the 2 fields
        # the validator actually polices (``slice_id`` + ``journal_path``);
        # the rename + narrow is a single coordinated mechanical edit
        # that closes both P3-13a-3 + P3-13l-2 carries. Behavior-
        # equivalent (the Literal validator catches the same empty /
        # whitespace ``event`` cases the dropped validator branch
        # would have caught).
        if not value or not value.strip():
            raise ValueError(
                "ImplementationArtifactAnchor slice_id / journal_path "
                "must be non-empty (doc-13:143-150)"
            )
        return value

    @field_validator("line_start", "decision_log_line")
    @classmethod
    def _line_positive_when_present(cls, value: int | None) -> int | None:
        # Markdown / JSONL line numbers are 1-indexed; a zero or negative
        # line silently breaks the journal-anchor freshness contract.
        if value is not None and value < 1:
            raise ValueError(
                "ImplementationArtifactAnchor line_start / decision_log_line "
                "must be >= 1 when present (1-indexed markdown / JSONL lines)"
            )
        return value

    @field_validator("open_findings")
    @classmethod
    def _open_findings_dedup_and_non_empty(cls, value: list[str]) -> list[str]:
        # Doc-13:150 -- the anchor is canonical; duplicates would let the
        # same finding be double-counted by Slice-15 metrics and Slice-16
        # findings. Empty strings are rejected too: an unnamed finding is
        # not a tracking record.
        seen: set[str] = set()
        for finding in value:
            if not finding or not finding.strip():
                raise ValueError(
                    "ImplementationArtifactAnchor open_findings entries must "
                    "be non-empty"
                )
            if finding in seen:
                raise ValueError(
                    f"ImplementationArtifactAnchor open_findings contains "
                    f"duplicate finding id {finding!r}"
                )
            seen.add(finding)
        return value
