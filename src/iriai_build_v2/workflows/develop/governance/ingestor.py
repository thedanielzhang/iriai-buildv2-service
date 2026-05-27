"""Slice 13b -- ``GovernanceEvidenceIngestor`` skeleton + bounded readers.

This module owns the doc-13:153-171 ``GovernanceEvidenceIngestor`` abstract
contract plus a default pure-typed implementation
(:class:`DefaultGovernanceEvidenceIngestor`) that takes its bounded-reader
dependencies via constructor injection. It lands the doc-13:178-181
§ "Refactoring Steps" step 2 deliverable ("Add bounded readers over typed
journal summaries, compatibility projection summaries, supervisor digests,
resource snapshots, and implementation logs").

Per the governance prompt § "Non-Negotiables" the ingestor surface is
analytical / advisory / **read-only**: it never mutates executor /
control-plane / product state, never takes merge or checkpoint authority,
never forces policy activation, and never invokes any write/insert/update/
delete method on the bounded readers it composes (the test suite asserts
this via a recording-proxy fake).

Per the governance prompt § "Slice 13A invariant for downstream slices" no
governance ingestor that influences dispatch / verify / merge / checkpoint /
route / scheduler / policy may consume the typed model as **execution
authority** until Slice 13A's evidence-completeness invariant lands. The
ingestor produces the typed surface here so later sub-slices can wire it up;
it does NOT itself dispatch / verify / merge / checkpoint / route /
schedule / activate policy.

Bounded-read precedent mirrored from
``src/iriai_build_v2/execution_control/store.py:1449-1458``
(:func:`_typed_bounded` -- the ``LIMIT cap + 1`` split that drops the
sentinel row and signals truncation) and
``src/iriai_build_v2/execution_control/store.py:3909-3928``
(:meth:`ExecutionControlStore._set_local_statement_timeout` --
``SET LOCAL statement_timeout`` at the store boundary). The default
ingestor enforces ``LIMIT cap + 1`` *above* the reader (the reader is asked
for ``cap + 1`` rows; the +1 sentinel signals truncation and is dropped)
and forwards ``statement_timeout_ms`` to the reader on every call.

Doc-13:153-171 names two function-signature shapes that the doc's
§ "Proposed Interfaces And Types" list does not enumerate verbatim:
:class:`GovernanceWindow` (the ``window`` parameter of
:meth:`GovernanceEvidenceIngestor.ingest_feature_window`) and
:class:`GovernanceEvidenceSlice` (the return type of
:meth:`GovernanceEvidenceIngestor.resolve_ref`). They live here in
``ingestor.py`` rather than ``models.py`` so the 13a-accepted
8-typed-shape boundary in :mod:`.models` is preserved; both are pure
pydantic v2 ``BaseModel`` shapes with
``model_config = ConfigDict(extra="forbid")`` (mirrors the 13a
:class:`GovernanceReadBudget` precedent at ``models.py:115-175``).
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Awaitable, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import (
    EvidenceAuthority,
    EvidenceQuality,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
)


__all__ = [
    # ABC + default implementation (doc-13:153-171).
    "GovernanceEvidenceIngestor",
    "DefaultGovernanceEvidenceIngestor",
    # Function-signature shapes the ABC requires (doc-13:153-171; not in the
    # 13a § "Proposed Interfaces And Types" 8-shape list at doc-13:68-151,
    # so they live in ingestor.py rather than models.py).
    "GovernanceWindow",
    "GovernanceEvidenceSlice",
    # Bounded-reader callable port (the constructor-injected dependency
    # surface the default ingestor composes over typed contracts).
    "BoundedReader",
    "BoundedReadResult",
]


# --- Function-signature shapes the ABC requires (doc-13:153-171) ------------


class GovernanceWindow(BaseModel):
    """The time / cursor window for one
    :meth:`GovernanceEvidenceIngestor.ingest_feature_window` call.

    Doc-13:160 names this type as the ``window`` parameter without
    enumerating its fields. The 13b skeleton encodes only the minimum
    contract that downstream sub-slices need (an ISO-8601 / cursor pair
    that the bounded reader can translate into a stable
    feature-(and-optional-cursor)-scoped query). Doc-13:113-126 already
    uses ``dict[str, Any]`` for window / stale-check metadata on the typed
    shapes; this skeleton keeps the same shape so the surface is uniform
    and a later policy-adapter slice can tighten the type as the carried
    P3-13a-4 ledger entry notes.

    All four fields are optional so an empty :class:`GovernanceWindow` is a
    legitimate "full feature scope" request; the bounded reader is still
    responsible for honouring the :class:`GovernanceReadBudget` caps the
    ingestor forwards.
    """

    # extra='forbid' mirrors the 13a ConfigDict precedent at
    # workflows/develop/governance/models.py:115-175 (and the sibling
    # executor models at workflows/develop/execution/verification.py:74 /
    # failure_router.py:576) -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    start_cursor: str | None = None
    end_cursor: str | None = None
    start_iso: str | None = None
    end_iso: str | None = None
    selectors: dict[str, Any] = Field(default_factory=dict)


class GovernanceEvidenceSlice(BaseModel):
    """A bounded slice returned by
    :meth:`GovernanceEvidenceIngestor.resolve_ref`.

    Doc-13:170 names this type as the return value of ``resolve_ref``
    without enumerating its fields. The 13b skeleton encodes the minimum
    contract: the originating :class:`GovernanceEvidenceRef` (so a consumer
    can audit which ref this slice came from), the resolved per-page
    :class:`GovernanceEvidencePageRef` records (each carrying their own
    ``completeness`` + ``exact`` flags, validated by the 13a cross-
    validator), and the raw text body (truncated to ``max_chars`` per the
    :meth:`GovernanceEvidenceIngestor.resolve_ref` parameter and the
    governance prompt § "Bounded reads" non-negotiable).

    The default ingestor asks the source reader to enforce the
    ``max_chars`` cap before returning the row body. When the source
    reader returns an already-bounded preview, ``truncated_to_chars`` is
    set to the cap and ``preview_only`` is ``True``; the corresponding
    :class:`GovernanceEvidencePageRef` carries
    ``completeness="preview_only"`` + ``exact=False`` per the Slice 13A
    invariant precursor at ``models.py:_exact_completeness_consistency``
    (doc-13a:24, 109-118). If a reader ignores the requested cap and
    returns an over-budget body, the slice is marked unavailable and the
    over-budget body is not surfaced to callers.
    """

    model_config = ConfigDict(extra="forbid")

    source_ref: GovernanceEvidenceRef
    pages: list[GovernanceEvidencePageRef]
    body: str
    truncated_to_chars: int | None = None
    preview_only: bool = False


# --- Bounded-reader port (the constructor-injected dependency) --------------


class BoundedReadResult(BaseModel):
    """The typed result the injected :class:`BoundedReader` returns.

    The default :class:`DefaultGovernanceEvidenceIngestor` always asks the
    reader for ``cap + 1`` rows; if the reader returns more than ``cap``
    rows the ingestor splits at ``cap``, drops the +1 sentinel, and sets
    the returned :class:`GovernanceEvidenceSet` completeness state to
    ``"paged"`` + ``read_budget_exhausted=True`` (mirrors the doc-10
    ``_typed_bounded`` truncation contract at
    ``execution_control/store.py:1449-1458``).
    """

    model_config = ConfigDict(extra="forbid")

    rows: list[dict[str, Any]]
    authority: EvidenceAuthority

    @field_validator("rows")
    @classmethod
    def _rows_is_list(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # Defence in depth: the reader contract is a list of dict rows. A
        # non-list / non-dict payload would silently break the bounded-read
        # discipline downstream.
        if not isinstance(value, list):
            raise ValueError("BoundedReadResult.rows must be a list[dict]")
        for row in value:
            if not isinstance(row, dict):
                raise ValueError("BoundedReadResult.rows entries must be dict")
        return value


@runtime_checkable
class BoundedReader(Protocol):
    """The bounded-reader callable port the ingestor composes.

    A reader is invoked with ``(authority, *, limit, statement_timeout_ms,
    selectors)`` and returns a :class:`BoundedReadResult` (or an awaitable
    yielding one). The reader is responsible for the actual typed-source
    read (whether it backs ``ExecutionControlStore`` /
    ``ControlPlaneSnapshot`` / supervisor digests / implementation-journal
    file slurps / etc.); the ingestor handles the
    bounded-read accounting (``cap + 1`` split, truncation signal) +
    typed-output composition.

    The reader MUST be read-only: the ingestor never invokes any write /
    insert / update / delete method on it (the test suite asserts this via
    a recording-proxy fake). The injected reader is a function/callable, so
    "no mutation" means "the ingestor never calls anything other than the
    reader itself."
    """

    def __call__(
        self,
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult | Awaitable[BoundedReadResult]: ...


# --- The ABC (doc-13:153-171) -----------------------------------------------


class GovernanceEvidenceIngestor(ABC):
    """Doc-13:156-171 -- the abstract governance-evidence ingestor contract.

    Three bounded async methods compose typed-source reads into the
    :class:`GovernanceEvidenceSet` corpus the Slice-15 metrics layer +
    Slice-16 finding engine read from. Per the governance prompt
    § "Non-Negotiables":

    - **Analytical, advisory, read-only**: no write/insert/update/delete
      method may be invoked on any bounded reader.
    - **Reuse Slice 01-12 typed contracts**: implementations read from the
      existing typed-source surface (``ExecutionControlStore``,
      ``ControlPlaneSnapshot``, ``SupervisorObservationDigest``, the
      implementation journal + decision log) -- they do NOT introduce a
      second journal, second projection authority, or second event
      taxonomy.
    - **Bounded reads**: reuse the typed snapshot's ``LIMIT cap + 1``
      truncation discipline and the supervisor's
      ``SET LOCAL statement_timeout`` pattern. No artifact-body hydration
      on the governance read path.
    - **Slice 13A invariant for downstream slices**: no ingestor output
      may be consumed as **execution authority** until Slice 13A's
      evidence-completeness invariant lands; until then, the typed
      ``preview_only`` / ``exact`` / ``completeness`` flags are display-
      only.
    """

    @abstractmethod
    async def ingest_feature_window(
        self,
        feature_id: str,
        window: GovernanceWindow,
        *,
        budget: GovernanceReadBudget,
    ) -> GovernanceEvidenceSet:
        """Doc-13:157-163 -- bounded ingest of one feature window.

        The implementation MUST honour ``budget.max_event_rows`` /
        ``budget.max_artifact_summary_rows`` (``LIMIT cap + 1``),
        ``budget.statement_timeout_ms`` (forwarded to the bounded
        reader), and ``budget.max_chars_per_ref`` (per-row text truncation
        with ``completeness="preview_only"`` + ``exact=False``). Returns
        a :class:`GovernanceEvidenceSet` whose ``completeness`` is
        ``"paged"`` if the bounded read tripped the cap and
        ``"complete"`` otherwise.
        """

    @abstractmethod
    async def ingest_implementation_artifacts(
        self,
        slice_ids: list[str],
        *,
        budget: GovernanceReadBudget,
    ) -> GovernanceEvidenceSet:
        """Doc-13:164-169 -- bounded ingest of implementation-log artifacts.

        Same bounded-read contract as :meth:`ingest_feature_window`. The
        ``slice_ids`` parameter scopes the read to a list of slice
        identifiers (e.g. ``["13a", "13b"]``); doc-13:182-185 specifies
        the implementation-journal parser + decision-log JSONL parser
        that produce
        :class:`~iriai_build_v2.workflows.develop.governance.models.ImplementationArtifactAnchor`
        records, but those parsers themselves are out of scope for 13b
        (later Slice-13 sub-slice).
        """

    @abstractmethod
    async def resolve_ref(
        self,
        ref: GovernanceEvidenceRef,
        *,
        max_chars: int,
    ) -> GovernanceEvidenceSlice:
        """Doc-13:170 -- resolve one :class:`GovernanceEvidenceRef` to a
        bounded :class:`GovernanceEvidenceSlice`.

        The implementation MUST enforce the per-row text body cap at
        ``max_chars`` at the reader/source boundary. When the source
        reader returns an already-bounded preview, the returned slice
        carries ``truncated_to_chars=max_chars`` +
        ``preview_only=True``, and the embedded
        :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
        records set ``completeness="preview_only"`` + ``exact=False`` per
        the Slice 13A invariant precursor at
        ``models.py:_exact_completeness_consistency``. If the reader
        returns an over-budget body despite the requested bound, the
        implementation must fail closed or mark the slice unavailable.
        """


# --- Default pure-typed implementation -------------------------------------


# Doc-13 does not spec the default impl's read-window selectors; the
# constructor cap defaults below mirror the
# :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceReadBudget`
# defaults at ``models.py:115-175`` so an implementation that omits a
# per-call budget is still bounded.
_DEFAULT_LIMIT_CAP: int = 500
_DEFAULT_STATEMENT_TIMEOUT_MS: int = 10_000
_DEFAULT_MAX_CHARS_PER_REF: int = 40_000


class DefaultGovernanceEvidenceIngestor(GovernanceEvidenceIngestor):
    """Doc-13:178-181 step 2 -- the default pure-typed ingestor implementation.

    Takes the bounded :class:`BoundedReader` dependency via constructor
    injection so the test surface can fake it and the ingestor never
    imports ``ExecutionControlStore`` directly (which would create a
    circular dep and lock the ingestor to a single backing store). The
    constructor knobs (``limit_cap`` / ``statement_timeout_ms`` /
    ``max_chars_per_ref``) default to the doc-13:89-95
    :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceReadBudget`
    defaults so an ingestor created with only a reader is still bounded.

    Per-call ``budget`` arguments override the constructor defaults but
    are CLAMPED DOWN per-field: a caller cannot widen the bounded-read
    discipline by passing a larger per-call budget than the constructor
    cap. This mirrors the Slice-10a
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshotQuery`
    ``_clamp_budget_to_ceiling`` precedent at
    ``workflows/develop/execution/snapshots.py:202-214``.
    """

    def __init__(
        self,
        reader: BoundedReader,
        *,
        limit_cap: int = _DEFAULT_LIMIT_CAP,
        statement_timeout_ms: int = _DEFAULT_STATEMENT_TIMEOUT_MS,
        max_chars_per_ref: int = _DEFAULT_MAX_CHARS_PER_REF,
        journal_path: Path | str | None = None,
        decisions_path: Path | str | None = None,
        supervisor_digest_reader: BoundedReader | None = None,
        resource_snapshot_reader: BoundedReader | None = None,
        legacy_event_reader: BoundedReader | None = None,
        legacy_artifact_summary_reader: BoundedReader | None = None,
    ) -> None:
        # Fail-closed on non-positive caps (no silent degradation -- doc-13:89-95
        # and the auto-memory ``feedback_no_silent_degradation`` rule).
        if limit_cap <= 0:
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor limit_cap must be positive "
                "(doc-13:89-95 bounded-read contract)"
            )
        if statement_timeout_ms <= 0:
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor statement_timeout_ms must "
                "be positive (doc-13:89-95 bounded-read contract)"
            )
        if max_chars_per_ref <= 0:
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor max_chars_per_ref must be "
                "positive (doc-13:89-95 bounded-read contract)"
            )
        self._reader = reader
        self._limit_cap = int(limit_cap)
        self._statement_timeout_ms = int(statement_timeout_ms)
        self._max_chars_per_ref = int(max_chars_per_ref)
        # Sub-slice 13f (doc-13:188-200) -- optional journal + decision-log
        # paths the rewired ``ingest_implementation_artifacts`` reads via
        # the 13c+13d parsers. Stored as ``Path`` objects (coerced from
        # str at construction time) so the production caller can supply
        # either form. ``None`` means the caller did not configure the
        # implementation-artifact path; calling
        # ``ingest_implementation_artifacts`` in that state raises a
        # typed ``ValueError`` per ``feedback_no_silent_degradation``.
        self._journal_path: Path | None = (
            Path(journal_path) if journal_path is not None else None
        )
        self._decisions_path: Path | None = (
            Path(decisions_path) if decisions_path is not None else None
        )
        # Sub-slice 13h (doc-13:178-181 step 2; doc-13:80-81 EvidenceAuthority
        # enum members) -- the two NEW bounded-reader lanes for the
        # supervisor-digest + resource-snapshot authorities. Mirrors the 13b
        # ``BoundedReader`` protocol at ``ingestor.py:186-212`` verbatim:
        # ``(authority, *, limit, statement_timeout_ms, selectors)`` ->
        # ``BoundedReadResult``; sync or async; never invoked for write/
        # insert/update/delete per the governance prompt § "Non-Negotiables".
        #
        # **Optional readers, empty-default semantics.** Doc-13 does NOT mark
        # supervisor_digest + resource_snapshot as REQUIRED on the
        # implementation-artifact path; they are AUGMENTING authorities
        # alongside the canonical journal + decision-log anchors. Doc-13:207-
        # 208 ("Missing implementation journal: mark the evidence set
        # ``insufficient``") is specifically about the journal path -- 13f
        # already fail-closes-loudly on missing ``journal_path`` /
        # ``decisions_path``. Per ``feedback_no_silent_degradation`` ("Never
        # silently degrade when REQUIRED services fail") the auto-memory
        # qualifier "required" applies: doc-13 does not make these readers
        # required for this method. When the reader is ``None`` the
        # corresponding lane stays empty in the composed evidence set (13f
        # baseline behaviour preserved); when configured, every row the
        # reader yields becomes one ``GovernanceEvidenceRef`` via
        # :meth:`_project_row_to_ref` with the appropriate authority.
        #
        # The cap on the new lanes is ``min(budget.max_ref_resolutions,
        # ctor_cap)`` -- each row IS a ref so the doc-13:92 cap
        # ``max_ref_resolutions`` is conceptually correct (the doc-13:90
        # ``max_event_rows`` cap is for event-row reads on
        # :meth:`ingest_feature_window`).
        self._supervisor_digest_reader: BoundedReader | None = (
            supervisor_digest_reader
        )
        self._resource_snapshot_reader: BoundedReader | None = (
            resource_snapshot_reader
        )
        # Sub-slice 13j (doc-13:191-192 § "Refactoring Steps" step 7 verbatim
        # -- "Keep legacy event/artifact ingestion read-only and bounded. Use
        # summaries and selected slices only.") + doc-13:74-84
        # ``EvidenceAuthority`` enum members ``legacy_event`` +
        # ``legacy_artifact_summary`` (the 2 LEGACY values in the 9-value
        # authority enum). The 13j sub-slice closes the FINAL doc-13
        # § Refactoring Steps deliverable by adding the two NEW bounded-
        # reader lanes for the legacy authorities. The 13b/13f/13h ingestor
        # lanes populated the 7 TYPED-FIRST authorities (typed_journal /
        # compatibility_projection / git_provenance / implementation_journal /
        # implementation_decision_log / supervisor_digest / resource_snapshot);
        # 13j populates the 2 remaining legacy authorities so the
        # ``EvidenceAuthority`` surface is fully wired.
        #
        # **Optional readers, empty-default semantics** (consistent with the
        # 13h decision per doc-13:178-181 step 2). Doc-13:191-192 (the step 7
        # verbatim mandate) does NOT mark legacy_event +
        # legacy_artifact_summary as REQUIRED on the implementation-artifact
        # path; they are AUGMENTING authorities alongside the canonical
        # journal + decision-log anchors. Doc-13:207-208 ("Missing
        # implementation journal: mark the evidence set ``insufficient``") is
        # journal-path specific -- 13f already fail-closes-loudly on missing
        # ``journal_path`` / ``decisions_path``. Per
        # ``feedback_no_silent_degradation`` ("Never silently degrade when
        # REQUIRED services fail") the auto-memory qualifier "required"
        # applies: doc-13 does not make these readers required for this
        # method. When the reader is ``None`` the corresponding legacy lane
        # stays empty in the composed evidence set (13b/13f/13h baseline
        # behaviour preserved); when configured, every row the reader yields
        # becomes one ``GovernanceEvidenceRef`` via
        # :meth:`_project_row_to_ref` with the appropriate legacy authority
        # AND ``quality="derived"`` per doc-13:173-175 verbatim ("Mixed
        # typed/legacy evidence is encoded as ``quality='derived'`` plus
        # source_mix, not as a separate ``EvidenceQuality`` literal").
        #
        # **Read-only / no-mutation discipline** (governance prompt
        # § "Non-Negotiables" verbatim "Governance is analytical, advisory,
        # read-only.") -- the legacy readers are subject to the same
        # invariant as the existing 7 typed-first readers: the ingestor
        # never invokes any write/insert/update/delete method on them. The
        # test suite asserts this via the same ``_RecordingReader``
        # recording-proxy fake the 13b/13f/13h suite uses.
        #
        # **Advisory-only** per doc-13:223-224 ("Slack or prose-only
        # supervisor evidence: mark advisory unless linked to typed
        # observation or decision records") -- legacy refs cannot be cited
        # as execution authority. The FROZEN 13e digester's
        # ``_project_blockers`` at ``evidence_set.py:602-639`` emits a
        # ``governance_evidence_legacy_authority:<authority>:<ref_id>``
        # blocker string for every legacy ref, surfacing the Slice 13A
        # invariant constraint to downstream authoritative consumers.
        self._legacy_event_reader: BoundedReader | None = (
            legacy_event_reader
        )
        self._legacy_artifact_summary_reader: BoundedReader | None = (
            legacy_artifact_summary_reader
        )

    # ── public API ──────────────────────────────────────────────────────────

    async def ingest_feature_window(
        self,
        feature_id: str,
        window: GovernanceWindow,
        *,
        budget: GovernanceReadBudget,
    ) -> GovernanceEvidenceSet:
        # Doc-13:157-163. The feature-window ingest queries the typed-journal
        # authority via the injected reader. Per doc-13:182-183 the
        # implementation-journal parser is later sub-slice work; here we
        # bound the read and project the rows into typed refs.
        selectors = self._feature_window_selectors(feature_id, window)
        budget_caps = self._effective_caps(budget)
        result = await self._invoke_reader(
            authority="typed_journal",
            selectors=selectors,
            budget_caps=budget_caps,
        )
        return self._build_evidence_set(
            feature_id=feature_id,
            corpus_id=f"feature-window:{feature_id}",
            authority="typed_journal",
            reader_result=result,
            budget=budget,
            budget_caps=budget_caps,
            source_window={
                "start_cursor": window.start_cursor,
                "end_cursor": window.end_cursor,
                "start_iso": window.start_iso,
                "end_iso": window.end_iso,
                "selectors": dict(window.selectors),
            },
        )

    async def ingest_implementation_artifacts(
        self,
        slice_ids: list[str],
        *,
        budget: GovernanceReadBudget,
    ) -> GovernanceEvidenceSet:
        # Doc-13:164-169 + doc-13:188-200 (sub-slice 13f wiring per STATUS.md
        # § "Next safe action"). The implementation-artifact ingest reads the
        # local ``implementation-journal.md`` + ``implementation-decisions.jsonl``
        # files via the 13c+13d parsers (doc-13:182-185 § "Refactoring Steps"
        # steps 3-4) and composes the resulting typed anchors into a
        # :class:`GovernanceEvidenceSet` via the 13e digester
        # (doc-13:186-187 step 5). The reader-based path the 13b skeleton
        # used for this method is REPLACED here -- per the chunk-shape
        # contract the 13c+13d parsers ARE the journal/decision-log
        # authorities; the injected ``BoundedReader`` is the
        # typed-journal (Postgres) authority, NOT the journal-file
        # authority. ``ingest_feature_window`` continues to use the
        # reader-based ``_build_evidence_set`` path against the typed
        # journal.
        if not isinstance(slice_ids, list):
            raise TypeError("slice_ids must be a list[str]")

        # Sub-slice 13f point 2 -- fail-closed on missing paths per the
        # ``feedback_no_silent_degradation`` auto-memory rule and doc-13:184
        # ("rejects malformed rows" / fail-closed discipline carried to
        # missing-file). Two distinct failure modes:
        #   1. The caller never configured the path (constructor kwarg
        #      was None);
        #   2. The caller configured a path that does not exist on disk.
        # Both are surfaced as ``ValueError`` with the offending path so a
        # later sub-slice that wires the canonical paths sees a clear
        # error rather than an empty evidence set.
        if self._journal_path is None:
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts "
                "requires a configured ``journal_path`` (per doc-13:188-200 "
                "sub-slice 13f). The constructor was invoked without "
                "``journal_path=...``; pass the path to the local "
                "implementation-journal.md file. Fail-closed per the "
                "feedback_no_silent_degradation rule."
            )
        if self._decisions_path is None:
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts "
                "requires a configured ``decisions_path`` (per doc-13:188-200 "
                "sub-slice 13f). The constructor was invoked without "
                "``decisions_path=...``; pass the path to the local "
                "implementation-decisions.jsonl file. Fail-closed per the "
                "feedback_no_silent_degradation rule."
            )
        if not self._journal_path.exists():
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts "
                f"configured journal_path does not exist: {self._journal_path}. "
                "The 13c parser cannot read from a missing file; fail-closed "
                "per the feedback_no_silent_degradation rule (doc-13:184 "
                "'rejects malformed rows' / doc-13:207-208 'Missing "
                "implementation journal: mark the evidence set ``insufficient``')."
            )
        if not self._decisions_path.exists():
            raise ValueError(
                "DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts "
                f"configured decisions_path does not exist: {self._decisions_path}. "
                "The 13d parser cannot read from a missing file; fail-closed "
                "per the feedback_no_silent_degradation rule (doc-13:184 "
                "'rejects malformed rows' / doc-13:209-210 'Malformed JSONL "
                "decision row: record a ``governance_evidence_gap`` finding')."
            )

        # Sub-slice 13f point 3 -- the 13c+13d parsers emit one anchor per
        # recognised journal heading / finding / subagent UUID / test-result
        # line (13c) plus one anchor per recognised JSONL row plus zero-or-
        # more cross-slice finding anchors (13d). The lazy import keeps the
        # 13b ABC + signature shapes (this module) decoupled from the 13c+13d
        # parsers + 13e digester (which transitively import from this
        # module: ``evidence_set.py`` imports :class:`GovernanceWindow`
        # from here, so a top-level import would be circular).
        from .decision_log_parser import parse_implementation_decision_log
        from .evidence_set import compose_governance_evidence_set
        from .journal_parser import parse_implementation_journal

        journal_anchors = parse_implementation_journal(self._journal_path)
        decision_log_anchors = parse_implementation_decision_log(
            self._decisions_path
        )

        # Sub-slice 13f point 3 (continued) -- deterministic ``slice_ids``
        # filter. An empty list means "all slices" (no filter); a non-empty
        # list selects exactly those slices via membership-test against a
        # ``set`` for O(1) lookup. The 13c+13d parsers emit anchors whose
        # ``slice_id`` field is the canonical id (e.g. ``13a`` / ``13b``
        # / ``11d`` / ``08c-1``); the filter is exact-match by string.
        if slice_ids:
            slice_id_set = set(slice_ids)
            journal_anchors = [
                anchor
                for anchor in journal_anchors
                if anchor.slice_id in slice_id_set
            ]
            decision_log_anchors = [
                anchor
                for anchor in decision_log_anchors
                if anchor.slice_id in slice_id_set
            ]

        # Sub-slice 13f point 4 -- the ``GovernanceWindow`` projection for
        # the implementation-artifact path. ``start_iso`` / ``end_iso`` are
        # None (the journal anchors carry their own line-level provenance;
        # the markdown / JSONL paths have no time-window contract).
        # ``selectors`` carries the ``slice_ids`` so the digester's
        # ``source_window`` field reflects the projection unambiguously.
        window = GovernanceWindow(
            selectors={"slice_ids": list(slice_ids)},
        )

        # Sub-slice 13f point 1+4 -- stable corpus id mirroring the prior
        # 13b skeleton's shape so a downstream consumer that keyed off
        # the corpus id continues to find the same shape.
        corpus_id = (
            "implementation-artifacts:" + ",".join(slice_ids)
            if slice_ids
            else "implementation-artifacts:*"
        )

        # Sub-slice 13h (doc-13:178-181 step 2 verbatim "Add bounded readers
        # over typed journal summaries, compatibility projection summaries,
        # supervisor digests, resource snapshots, and implementation logs"
        # + doc-13:80-81 EvidenceAuthority enum members) -- the
        # supervisor-digest + resource-snapshot lanes are now populated by
        # the two new ``BoundedReader`` injection points (constructor
        # kwargs ``supervisor_digest_reader`` / ``resource_snapshot_reader``).
        # Closes the 13f P3-13f-3 carry: the two ``compose_governance_evidence_set``
        # kwargs were receiving empty lists since 13f.
        #
        # **Optional readers, empty-default**: when the reader is ``None``
        # the corresponding lane stays empty (13f baseline preserved). When
        # configured, the reader is invoked via the existing
        # :meth:`_invoke_reader` helper -- same ``+1`` sentinel discipline,
        # same ``SET LOCAL statement_timeout`` forwarding, same sync/async
        # dispatch via ``inspect.isawaitable``. Per-call statement-timeout
        # + max-chars-per-ref clamp via the existing :meth:`_effective_caps`
        # helper. The row-limit cap is ``min(budget.max_ref_resolutions,
        # ctor_cap)`` per doc-13:92 -- each yielded row becomes one
        # :class:`GovernanceEvidenceRef`, so the ref-count cap is the
        # appropriate bound (not the event-row cap ``max_event_rows`` which
        # governs :meth:`ingest_feature_window`).
        supervisor_digest_refs: list[GovernanceEvidenceRef] = []
        resource_snapshot_refs: list[GovernanceEvidenceRef] = []
        # The augmenting-lane overflows are accumulated into a single
        # ``omitted_refs`` list passed to the digester; the per-call lane
        # exhaustion flag is OR-ed into a single ``read_budget_exhausted``
        # signal that the digester surfaces on the composed set
        # (doc-13:215-220 verbatim "Read budget exhausted: return the
        # partial evidence set with ``read_budget_exhausted=True``, populate
        # ``omitted_refs`` as exact page refs when possible").
        augmenting_omitted_refs: list[GovernanceEvidencePageRef] = []
        augmenting_read_budget_exhausted = False

        if self._supervisor_digest_reader is not None:
            (
                lane_refs,
                lane_omitted_refs,
                lane_budget_exhausted,
            ) = await self._ingest_augmenting_lane(
                reader=self._supervisor_digest_reader,
                authority="supervisor_digest",
                budget=budget,
                corpus_id=corpus_id,
                slice_ids=slice_ids,
            )
            supervisor_digest_refs = lane_refs
            augmenting_omitted_refs.extend(lane_omitted_refs)
            augmenting_read_budget_exhausted = (
                augmenting_read_budget_exhausted or lane_budget_exhausted
            )

        if self._resource_snapshot_reader is not None:
            (
                lane_refs,
                lane_omitted_refs,
                lane_budget_exhausted,
            ) = await self._ingest_augmenting_lane(
                reader=self._resource_snapshot_reader,
                authority="resource_snapshot",
                budget=budget,
                corpus_id=corpus_id,
                slice_ids=slice_ids,
            )
            resource_snapshot_refs = lane_refs
            augmenting_omitted_refs.extend(lane_omitted_refs)
            augmenting_read_budget_exhausted = (
                augmenting_read_budget_exhausted or lane_budget_exhausted
            )

        # Sub-slice 13j (doc-13:191-192 § "Refactoring Steps" step 7 verbatim
        # "Keep legacy event/artifact ingestion read-only and bounded. Use
        # summaries and selected slices only.") + doc-13:74-84
        # ``EvidenceAuthority`` enum members ``legacy_event`` +
        # ``legacy_artifact_summary`` + doc-13:173-175 verbatim ("Mixed
        # typed/legacy evidence is encoded as ``quality='derived'`` plus
        # source_mix"). The 13j sub-slice adds the two NEW bounded-reader
        # lanes for the legacy authorities. Like the 13h supervisor +
        # resource lanes above, the legacy lanes are AUGMENTING (empty-
        # default when unset; bounded-read invariant when configured;
        # overflow surfaces ``read_budget_exhausted=True`` + an exact
        # ``omitted_refs`` page-ref per doc-13:215-220).
        #
        # **FROZEN 13e digester signature preserved.** The FROZEN
        # ``compose_governance_evidence_set`` signature at
        # ``evidence_set.py:748-760`` accepts 4 typed input lanes
        # (journal_anchors / decision_log_anchors / supervisor_digest_refs /
        # resource_snapshot_refs). The 13e ``_project_source_mix`` at
        # ``evidence_set.py:513-531`` counts by ``ref.authority`` (NOT by
        # input-list position) so refs flow through to the correct
        # ``source_mix`` bucket regardless of which input kwarg they came
        # from. The 13e ``_project_quality`` at ``evidence_set.py:534-599``
        # has a ``has_legacy and has_typed`` -> ``derived`` branch
        # (``evidence_set.py:589-592``) plus a legacy-only -> ``insufficient``
        # branch (``evidence_set.py:581-587``) that already handles the
        # legacy-authority projection. The 13e ``_project_blockers`` at
        # ``evidence_set.py:602-639`` emits a
        # ``governance_evidence_legacy_authority:<authority>:<ref_id>``
        # blocker string for every legacy ref (Slice 13A invariant per
        # doc-13a:24, 109-118).
        #
        # **Producer-side packing.** legacy_event refs are packed onto the
        # existing ``supervisor_digest_refs`` augmenting kwarg list;
        # legacy_artifact_summary refs are packed onto the existing
        # ``resource_snapshot_refs`` augmenting kwarg list. The 13e digester
        # canonically sorts then dedups by ``(authority, ref_id)`` at
        # ``evidence_set.py:848-872`` so the packing order does not affect
        # the composed set's idempotency_key (sort-invariance per the
        # P1-13e-1 finalizer fix). The FROZEN digester signature is
        # preserved verbatim; the alternative of adding new
        # ``legacy_event_refs`` + ``legacy_artifact_summary_refs`` kwargs
        # would require editing FROZEN ``evidence_set.py`` (out of 13j
        # scope per the user-prompt non-negotiable "Do NOT edit the 6
        # FROZEN governance source files").
        legacy_event_refs: list[GovernanceEvidenceRef] = []
        legacy_artifact_summary_refs: list[GovernanceEvidenceRef] = []

        if self._legacy_event_reader is not None:
            (
                lane_refs,
                lane_omitted_refs,
                lane_budget_exhausted,
            ) = await self._ingest_legacy_lane(
                reader=self._legacy_event_reader,
                authority="legacy_event",
                budget=budget,
                corpus_id=corpus_id,
                slice_ids=slice_ids,
            )
            legacy_event_refs = lane_refs
            augmenting_omitted_refs.extend(lane_omitted_refs)
            augmenting_read_budget_exhausted = (
                augmenting_read_budget_exhausted or lane_budget_exhausted
            )

        if self._legacy_artifact_summary_reader is not None:
            (
                lane_refs,
                lane_omitted_refs,
                lane_budget_exhausted,
            ) = await self._ingest_legacy_lane(
                reader=self._legacy_artifact_summary_reader,
                authority="legacy_artifact_summary",
                budget=budget,
                corpus_id=corpus_id,
                slice_ids=slice_ids,
            )
            legacy_artifact_summary_refs = lane_refs
            augmenting_omitted_refs.extend(lane_omitted_refs)
            augmenting_read_budget_exhausted = (
                augmenting_read_budget_exhausted or lane_budget_exhausted
            )

        # Sub-slice 13j producer-side packing (see citation block above for
        # the FROZEN-digester-signature rationale). The legacy refs are
        # concatenated into the existing augmenting kwarg lists; the 13e
        # digester's per-ref projections operate on ``ref.authority`` (not
        # on input-list position) so the source_mix / quality / blockers
        # projections fire correctly.
        composed_supervisor_digest_refs = (
            supervisor_digest_refs + legacy_event_refs
        )
        composed_resource_snapshot_refs = (
            resource_snapshot_refs + legacy_artifact_summary_refs
        )

        # Sub-slice 13f point 1(c) -- delegate to the 13e digester with
        # the parser-emitted anchors and the window/budget projections.
        # The digester does its own canonical-sort-then-dedup discipline
        # (P1-13e-1 finalizer fix at ``evidence_set.py:_canonical_sort_refs``)
        # so the set-level ``idempotency_key`` is invariant under input
        # reordering by construction.
        #
        # Sub-slice 13h closes the 13f P3-13f-3 carry: the previously-empty
        # ``supervisor_digest_refs`` / ``resource_snapshot_refs`` lanes are
        # now populated when the corresponding readers are configured
        # (doc-13:178-181 step 2 + doc-13:80-81 EvidenceAuthority enum
        # members). The per-lane bounded-read overflow flag + omitted_refs
        # are forwarded to the digester so the composed set's
        # ``read_budget_exhausted`` + ``omitted_refs`` reflect the new
        # lanes' truncation (doc-13:215-220 verbatim).
        return compose_governance_evidence_set(
            journal_anchors=journal_anchors,
            decision_log_anchors=decision_log_anchors,
            supervisor_digest_refs=composed_supervisor_digest_refs,
            resource_snapshot_refs=composed_resource_snapshot_refs,
            window=window,
            read_budget=budget,
            corpus_id=corpus_id,
            feature_id=None,
            omitted_refs=augmenting_omitted_refs,
            read_budget_exhausted=augmenting_read_budget_exhausted,
        )

    async def resolve_ref(
        self,
        ref: GovernanceEvidenceRef,
        *,
        max_chars: int,
    ) -> GovernanceEvidenceSlice:
        # Doc-13:170. The resolve_ref path reads the source body for one
        # already-cited ref. Per the governance prompt § "Bounded reads",
        # the max-char cap is sent to the source reader so the source
        # boundary can return an already-bounded body. The fallback guard
        # below marks the slice unavailable if the reader still returns an
        # over-budget body.
        if max_chars <= 0:
            raise ValueError(
                "resolve_ref max_chars must be positive "
                "(doc-13:93 max_chars_per_ref bounded-read contract)"
            )
        # Clamp DOWN to the constructor cap -- a caller cannot widen the
        # truncation discipline by asking for more characters than the
        # ingestor was constructed with. Mirrors the Slice-10a
        # ``_clamp_budget_to_ceiling`` precedent at
        # workflows/develop/execution/snapshots.py:202-214.
        effective_cap = min(int(max_chars), self._max_chars_per_ref)
        # We invoke the same reader with a single-row selector keyed by the
        # ref's authority / source ids. The selector includes the body cap
        # so the reader can enforce the bound before returning the row.
        selectors: dict[str, Any] = {
            "ref_id": ref.ref_id,
            "authority": ref.authority,
            "feature_id": ref.feature_id,
            "artifact_id": ref.artifact_id,
            "event_id": ref.event_id,
            "commit_hash": ref.commit_hash,
            "journal_anchor": ref.journal_anchor,
            "max_chars": effective_cap,
            "body_max_chars": effective_cap,
            "body_slice": {"byte_start": 0, "max_chars": effective_cap},
        }
        result = await self._invoke_reader(
            authority=ref.authority,
            selectors=selectors,
            # ``resolve_ref`` is a single-row read; ``LIMIT 2`` (cap + 1)
            # so a duplicate row would surface as a paged-truncation signal.
            budget_caps={"limit": 1},
        )
        rows = result.rows
        first_row: dict[str, Any] = rows[0] if rows else {}
        body_text = ""
        if rows:
            # The reader's first row carries the source-bounded body; extra
            # rows past the first signal a ref-id collision and force
            # preview_only.
            body_text = str(first_row.get("body") or "")
        if len(body_text) > effective_cap:
            page_ref = GovernanceEvidencePageRef(
                page_ref_id=f"{ref.ref_id}:slice",
                authority=ref.authority,
                source_ref_id=ref.ref_id,
                byte_start=0,
                byte_end=0,
                digest=ref.digest,
                completeness="unavailable",
                exact=False,
                stale_check={
                    "ref_digest": ref.digest,
                    "source_row_count": len(rows),
                    "max_chars": effective_cap,
                    "body_max_chars": effective_cap,
                    "observed_body_chars": len(body_text),
                    "over_budget_body_returned": True,
                },
            )
            return GovernanceEvidenceSlice(
                source_ref=ref,
                pages=[page_ref],
                body="",
                truncated_to_chars=effective_cap,
                preview_only=True,
            )

        source_truncated = bool(
            first_row.get("body_truncated")
            or first_row.get("preview_only")
            or first_row.get("truncated_to_chars") is not None
        )
        declared_truncated_to_chars: int | None = None
        if first_row.get("truncated_to_chars") is not None:
            try:
                declared_truncated_to_chars = min(
                    int(first_row["truncated_to_chars"]), effective_cap
                )
            except (TypeError, ValueError):
                declared_truncated_to_chars = effective_cap
        truncated_body = body_text
        preview_only_flag = source_truncated or len(rows) > 1
        # Per doc-13a:24, 109-118 (Slice 13A invariant precursor) the
        # returned page-ref's completeness + exact MUST agree with
        # preview_only=True when the body was truncated. The cross-validator
        # at models.py:_exact_completeness_consistency enforces the contract
        # at construction time -- we honour it here so the page-ref
        # validates.
        if preview_only_flag:
            page_completeness = "preview_only"
            page_exact = False
        else:
            page_completeness = "complete"
            page_exact = True
        page_ref = GovernanceEvidencePageRef(
            page_ref_id=f"{ref.ref_id}:slice",
            authority=ref.authority,
            source_ref_id=ref.ref_id,
            byte_start=0,
            byte_end=len(truncated_body),
            digest=ref.digest,
            completeness=page_completeness,
            exact=page_exact,
            stale_check={"ref_digest": ref.digest, "source_row_count": len(rows)},
        )
        if declared_truncated_to_chars is not None:
            resolved_truncated_to_chars = declared_truncated_to_chars
        elif source_truncated:
            resolved_truncated_to_chars = effective_cap
        else:
            resolved_truncated_to_chars = None
        return GovernanceEvidenceSlice(
            source_ref=ref,
            pages=[page_ref],
            body=truncated_body,
            truncated_to_chars=resolved_truncated_to_chars,
            preview_only=preview_only_flag,
        )

    # ── private helpers ─────────────────────────────────────────────────────

    async def _invoke_reader(
        self,
        *,
        authority: EvidenceAuthority,
        selectors: dict[str, Any],
        budget_caps: dict[str, int],
    ) -> BoundedReadResult:
        # The reader contract is sync-or-async; doc-13 does not constrain
        # which, so we accept both via ``inspect.isawaitable``. Honouring
        # both lets a test fake be a plain function while a production
        # reader can be an asyncpg-backed coroutine.
        limit_with_sentinel = int(budget_caps.get("limit", self._limit_cap)) + 1
        # ``statement_timeout_ms`` is the clamped per-call value from
        # ``_effective_caps`` (doc-13:95 -- the per-call budget field is
        # the caller's tightening knob; the constructor cap is the ceiling).
        # Default to the constructor cap when the caller's helper omits it
        # (the single ``resolve_ref`` single-row read path does this since
        # its signature carries no GovernanceReadBudget argument).
        effective_timeout_ms = int(
            budget_caps.get("statement_timeout_ms", self._statement_timeout_ms)
        )
        # Doc-13:153-171 + doc-13 § "Bounded reads": every read forwards
        # the SET LOCAL statement_timeout to the underlying reader and the
        # ``cap + 1`` sentinel so truncation is explicit, not silent.
        outcome = self._reader(
            authority,
            limit=limit_with_sentinel,
            statement_timeout_ms=effective_timeout_ms,
            selectors=selectors,
        )
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if not isinstance(outcome, BoundedReadResult):
            raise TypeError(
                "BoundedReader must return a BoundedReadResult "
                f"(got {type(outcome).__name__})"
            )
        return outcome

    async def _ingest_augmenting_lane(
        self,
        *,
        reader: BoundedReader,
        authority: EvidenceAuthority,
        budget: GovernanceReadBudget,
        corpus_id: str,
        slice_ids: list[str],
    ) -> tuple[
        list[GovernanceEvidenceRef],
        list[GovernanceEvidencePageRef],
        bool,
    ]:
        """Sub-slice 13h (doc-13:178-181 step 2 + doc-13:80-81) -- invoke
        one of the two NEW augmenting bounded-reader lanes
        (``supervisor_digest`` / ``resource_snapshot``) and project the
        yielded rows to typed :class:`GovernanceEvidenceRef` instances.

        Mirrors the ``_build_evidence_set`` row-projection contract used by
        :meth:`ingest_feature_window` (doc-10 ``_typed_bounded`` truncation
        at ``execution_control/store.py:1449-1458`` -- LIMIT cap + 1; drop
        sentinel on overflow; signal ``read_budget_exhausted=True`` +
        record an exact ``omitted_refs`` page-ref with the cap-overflow
        item range per doc-13:215-220 verbatim).

        The cap on this lane is ``min(budget.max_ref_resolutions,
        ctor_cap)`` because each yielded row becomes one
        :class:`GovernanceEvidenceRef` -- the doc-13:92 ``max_ref_resolutions``
        cap is the conceptually correct bound for the row->ref projection
        (doc-13:90 ``max_event_rows`` governs event-row reads on
        :meth:`ingest_feature_window` where one row is one event, not one
        ref).

        Returns a 3-tuple ``(refs, omitted_refs, read_budget_exhausted)``
        so the caller can aggregate across the supervisor + resource
        lanes before delegating to the 13e digester.

        :param reader: the configured ``BoundedReader`` for this lane.
        :param authority: ``"supervisor_digest"`` or ``"resource_snapshot"``.
        :param budget: per-call :class:`GovernanceReadBudget` (caller's
            tightening knob; clamped DOWN to the constructor caps).
        :param corpus_id: the implementation-artifact corpus id; used as
            the prefix for the cap-overflow page-ref id so the omitted-ref
            cite is unambiguous about which lane was truncated.
        :param slice_ids: the slice-ids filter; forwarded to the reader as
            a ``selectors["slice_ids"]`` value so a Slice-10-backed reader
            can scope its read.
        :returns: tuple of (refs, omitted_refs, read_budget_exhausted).
        """

        # Clamp DOWN per the existing :meth:`_effective_caps` discipline.
        # The helper returns ``statement_timeout_ms`` + ``max_chars_per_ref``
        # clamped to the ceiling; for the row-limit we compute
        # ``min(budget.max_ref_resolutions, ctor_cap)`` inline (doc-13:92
        # is the appropriate cap for the row->ref projection).
        budget_caps = self._effective_caps(budget)
        max_refs_cap = min(int(budget.max_ref_resolutions), self._limit_cap)
        # Reuse :meth:`_invoke_reader` verbatim per the user-prompt non-
        # negotiable: "Reuse ``_invoke_reader`` and ``_effective_caps`` --
        # do NOT invent new bounded-read code." The +1 sentinel for
        # overflow detection is added by ``_invoke_reader`` itself.
        reader_result = await self._invoke_reader_with(
            reader=reader,
            authority=authority,
            selectors={"slice_ids": list(slice_ids)},
            budget_caps={
                "limit": max_refs_cap,
                "statement_timeout_ms": budget_caps["statement_timeout_ms"],
            },
        )
        raw_rows = reader_result.rows
        budget_exhausted = len(raw_rows) > max_refs_cap
        capped_rows = raw_rows[:max_refs_cap] if budget_exhausted else raw_rows
        max_chars = budget_caps["max_chars_per_ref"]
        refs = [
            self._project_row_to_ref(authority, row, max_chars)
            for row in capped_rows
        ]
        # P2-13h-1 finalizer fix (doc-13:217-218 + doc-13a:24, 109-118) --
        # when this lane overflows the bounded-read cap, mark each KEPT
        # ref as ``completeness="paged"`` (and each contained page-ref
        # ``exact=False``) so the FROZEN 13e digester's
        # ``_project_completeness`` at ``evidence_set.py:436-510`` sees at
        # least one paged ref and projects the set-level completeness to
        # ``"paged"`` (and the typed-only+paged branch in
        # ``_project_quality`` at ``evidence_set.py:597-599`` projects
        # ``quality="derived"``). Without this remarking the set-level
        # completeness stays ``"complete"`` and quality stays
        # ``"canonical"`` on overflow, violating doc-13:217-218 "mark
        # quality insufficient or derived" + "mark completeness paged or
        # unavailable". 13h is the FIRST caller through the digester that
        # can set ``read_budget_exhausted=True``, so this latent gap is
        # now triggerable. ``preview_only`` stays ``False`` because these
        # refs are REAL data (just paginated), NOT preview-only. The 13a
        # ``_exact_completeness_consistency`` cross-validator at
        # ``models.py:234-249`` allows ``paged + exact=False``; the 13a
        # ``_preview_only_completeness_consistency`` validator at
        # ``models.py:309-323`` allows ``preview_only=False +
        # completeness="paged"``. Producer-side fix only; FROZEN
        # ``evidence_set.py`` UNTOUCHED.
        if budget_exhausted:
            refs = [
                self._remark_ref_as_paged_on_overflow(ref) for ref in refs
            ]
        # Doc-13:215-220 -- partial sets carry an exact ``omitted_refs``
        # page-ref recording the truncated suffix. The lane-prefixed
        # ``page_ref_id`` lets the consumer disambiguate which augmenting
        # lane overflowed.
        omitted_refs: list[GovernanceEvidencePageRef] = []
        if budget_exhausted:
            omitted_refs.append(
                GovernanceEvidencePageRef(
                    page_ref_id=f"{corpus_id}:{authority}:cap-overflow",
                    authority=authority,
                    source_ref_id=f"{corpus_id}:{authority}:cap-overflow",
                    item_start=max_refs_cap,
                    item_end=len(raw_rows),
                    digest=f"sha256:{authority}:cap-overflow:{max_refs_cap}",
                    completeness="paged",
                    exact=True,
                    stale_check={
                        "limit_cap": max_refs_cap,
                        "observed_row_count": len(raw_rows),
                        "authority": authority,
                    },
                )
            )
        return refs, omitted_refs, budget_exhausted

    @staticmethod
    def _remark_ref_as_paged_on_overflow(
        ref: GovernanceEvidenceRef,
    ) -> GovernanceEvidenceRef:
        """P2-13h-1 finalizer fix (doc-13:217-218 + doc-13a:24, 109-118)
        -- rebuild ``ref`` with ``completeness="paged"`` and each
        contained ``page_ref.exact=False``.

        Pydantic v2 models are immutable-by-convention here (the
        :class:`GovernanceEvidenceRef` / :class:`GovernanceEvidencePageRef`
        cross-validators run at construction time per the 13a model
        idiom at ``models.py:115-175``). We use ``model_copy(update=...)``
        to produce new typed instances that re-validate via the same
        cross-validators -- defence-in-depth that the post-overflow
        marking still satisfies the 13a invariants
        (``paged + exact=False`` is allowed; ``preview_only=False +
        completeness="paged"`` is allowed).
        """

        # Rebuild each contained page_ref with ``exact=False``. The 13a
        # ``_exact_completeness_consistency`` validator at
        # ``models.py:234-249`` rejects ``completeness="preview_only" +
        # exact=True`` -- the page-ref completeness must agree with the
        # new ref-level completeness, so we also force the page-ref
        # completeness to ``"paged"`` to keep the validator's
        # ``exact + completeness in {complete, paged}`` invariant
        # honoured for any page-ref already projected as ``complete``.
        remarked_page_refs = [
            page_ref.model_copy(
                update={"completeness": "paged", "exact": False}
            )
            for page_ref in ref.page_refs
        ]
        # ``preview_only`` stays ``False`` -- these refs ARE real data
        # (just paginated), NOT preview-only. The 13a
        # ``_preview_only_completeness_consistency`` validator at
        # ``models.py:309-323`` allows ``preview_only=False +
        # completeness="paged"``.
        return ref.model_copy(
            update={
                "completeness": "paged",
                "page_refs": remarked_page_refs,
            }
        )

    async def _ingest_legacy_lane(
        self,
        *,
        reader: BoundedReader,
        authority: EvidenceAuthority,
        budget: GovernanceReadBudget,
        corpus_id: str,
        slice_ids: list[str],
    ) -> tuple[
        list[GovernanceEvidenceRef],
        list[GovernanceEvidencePageRef],
        bool,
    ]:
        """Sub-slice 13j (doc-13:191-192 § "Refactoring Steps" step 7
        verbatim + doc-13:74-84 ``EvidenceAuthority`` enum + doc-13:173-175
        verbatim) -- invoke one of the two NEW legacy bounded-reader lanes
        (``legacy_event`` / ``legacy_artifact_summary``) and project the
        yielded rows to typed :class:`GovernanceEvidenceRef` instances
        with ``quality="derived"``.

        Mirrors :meth:`_ingest_augmenting_lane` (13h) verbatim in shape:
        same ``min(budget.max_ref_resolutions, ctor_cap)`` row cap, same
        ``+1`` sentinel discipline via :meth:`_invoke_reader_with`, same
        overflow remarking via :meth:`_remark_ref_as_paged_on_overflow`,
        same exact ``omitted_refs`` page-ref on cap-overflow, same
        ``(refs, omitted_refs, read_budget_exhausted)`` 3-tuple return
        shape so the caller can aggregate uniformly across the supervisor
        / resource / legacy_event / legacy_artifact_summary lanes.

        The KEY DIFFERENCE from :meth:`_ingest_augmenting_lane` is the
        ``quality="derived"`` projection on every legacy ref. Doc-13:173-
        175 verbatim mandates "Mixed typed/legacy evidence is encoded as
        ``quality='derived'`` plus source_mix, not as a separate
        ``EvidenceQuality`` literal"; the per-ref ``quality="derived"``
        keeps the producer-side projection aligned with the FROZEN 13e
        digester's set-level ``_project_quality`` "has_legacy" branch at
        ``evidence_set.py:589-592`` (mixed typed/legacy -> set
        ``derived``) and the "legacy-only" branch at
        ``evidence_set.py:581-587`` (legacy-only -> set
        ``insufficient``). The FROZEN 13e digester's ``_project_blockers``
        at ``evidence_set.py:602-639`` emits a
        ``governance_evidence_legacy_authority:<authority>:<ref_id>``
        blocker string for every legacy ref (Slice 13A invariant per
        doc-13a:24, 109-118 -- legacy refs are advisory only per
        doc-13:223-224 and cannot be cited as execution authority).

        Per the user-prompt non-negotiable "Reuse ``_invoke_reader_with``
        + ``_effective_caps`` -- do NOT invent new bounded-read code" the
        helper re-uses the 13h primitives verbatim. The new lane MUST NOT
        introduce a parallel bounded-read accounting code path.

        :param reader: the configured ``BoundedReader`` for this legacy
            lane.
        :param authority: ``"legacy_event"`` or
            ``"legacy_artifact_summary"`` (the 2 LEGACY values in the
            doc-13:74-84 ``EvidenceAuthority`` enum).
        :param budget: per-call :class:`GovernanceReadBudget` (caller's
            tightening knob; clamped DOWN to the constructor caps via
            :meth:`_effective_caps`).
        :param corpus_id: the implementation-artifact corpus id; used as
            the prefix for the cap-overflow page-ref id so the omitted-
            ref cite is unambiguous about which legacy lane was
            truncated.
        :param slice_ids: the slice-ids filter; forwarded to the reader
            as a ``selectors["slice_ids"]`` value so a legacy-event-table
            reader can scope its read.
        :returns: tuple of (refs, omitted_refs, read_budget_exhausted).
            Each ref has ``authority`` set per the input + ``quality``
            set to ``"derived"`` per doc-13:173-175 verbatim.
        """

        # Clamp DOWN per the existing :meth:`_effective_caps` discipline
        # (re-used verbatim from the 13h _ingest_augmenting_lane code path
        # per the user-prompt non-negotiable "Reuse ``_invoke_reader_with``
        # + ``_effective_caps``"). The helper returns
        # ``statement_timeout_ms`` + ``max_chars_per_ref`` clamped to the
        # ceiling; for the row-limit we compute
        # ``min(budget.max_ref_resolutions, ctor_cap)`` inline (doc-13:92
        # is the appropriate cap for the row->ref projection -- each
        # yielded row IS a ref so the ref-count cap is the conceptually
        # correct bound, NOT the event-row cap ``max_event_rows`` which
        # governs :meth:`ingest_feature_window`).
        budget_caps = self._effective_caps(budget)
        max_refs_cap = min(int(budget.max_ref_resolutions), self._limit_cap)
        # Reuse :meth:`_invoke_reader_with` verbatim per the user-prompt
        # non-negotiable. The +1 sentinel for overflow detection is added
        # by ``_invoke_reader_with`` itself.
        reader_result = await self._invoke_reader_with(
            reader=reader,
            authority=authority,
            selectors={"slice_ids": list(slice_ids)},
            budget_caps={
                "limit": max_refs_cap,
                "statement_timeout_ms": budget_caps["statement_timeout_ms"],
            },
        )
        raw_rows = reader_result.rows
        budget_exhausted = len(raw_rows) > max_refs_cap
        capped_rows = raw_rows[:max_refs_cap] if budget_exhausted else raw_rows
        max_chars = budget_caps["max_chars_per_ref"]
        # Sub-slice 13j (doc-13:173-175 verbatim) -- every legacy ref
        # carries ``quality="derived"`` so the producer-side projection is
        # aligned with the 13e digester's set-level ``_project_quality``
        # has_legacy / legacy-only branches. The optional ``quality``
        # kwarg added to :meth:`_project_row_to_ref` is the minimum-diff
        # extension that preserves the existing 13b/13f/13h call-site
        # ``quality="canonical"`` default.
        refs = [
            self._project_row_to_ref(authority, row, max_chars, quality="derived")
            for row in capped_rows
        ]
        # Re-use the P2-13h-1 finalizer remarking helper verbatim
        # (doc-13:217-218 + doc-13a:24, 109-118): on lane overflow each
        # kept ref's completeness is remarked to "paged" and each
        # contained page-ref's exact is forced to False so the FROZEN 13e
        # digester's ``_project_completeness`` at
        # ``evidence_set.py:436-510`` sees a "paged" ref and projects the
        # set-level completeness to "paged" (and the legacy + paged combo
        # at ``evidence_set.py:589-592`` still projects quality "derived"
        # -- the producer-side quality is already "derived" so the set-
        # level projection is unchanged).
        if budget_exhausted:
            refs = [
                self._remark_ref_as_paged_on_overflow(ref) for ref in refs
            ]
        # Doc-13:215-220 -- partial sets carry an exact ``omitted_refs``
        # page-ref recording the truncated suffix. The lane-prefixed
        # ``page_ref_id`` lets the consumer disambiguate which legacy
        # lane overflowed.
        omitted_refs: list[GovernanceEvidencePageRef] = []
        if budget_exhausted:
            omitted_refs.append(
                GovernanceEvidencePageRef(
                    page_ref_id=f"{corpus_id}:{authority}:cap-overflow",
                    authority=authority,
                    source_ref_id=f"{corpus_id}:{authority}:cap-overflow",
                    item_start=max_refs_cap,
                    item_end=len(raw_rows),
                    digest=f"sha256:{authority}:cap-overflow:{max_refs_cap}",
                    completeness="paged",
                    exact=True,
                    stale_check={
                        "limit_cap": max_refs_cap,
                        "observed_row_count": len(raw_rows),
                        "authority": authority,
                    },
                )
            )
        return refs, omitted_refs, budget_exhausted

    async def _invoke_reader_with(
        self,
        *,
        reader: BoundedReader,
        authority: EvidenceAuthority,
        selectors: dict[str, Any],
        budget_caps: dict[str, int],
    ) -> BoundedReadResult:
        """Sub-slice 13h -- the same :meth:`_invoke_reader` contract for an
        arbitrary :class:`BoundedReader` instance rather than the
        constructor-injected primary reader.

        :meth:`_invoke_reader` hard-codes ``self._reader``; the new
        augmenting lanes need to invoke a *different* reader per lane. This
        thin wrapper preserves the exact +1 sentinel + statement-timeout-
        forwarding + sync/async-dispatch + non-``BoundedReadResult``
        ``TypeError`` discipline of :meth:`_invoke_reader` but parameterises
        the reader. We do NOT factor the original :meth:`_invoke_reader`
        out into a callable-only form because 13b/13f tests pin the
        ``self._reader`` call shape (would touch other tests / FROZEN
        contracts beyond 13h scope).
        """

        limit_with_sentinel = int(budget_caps.get("limit", self._limit_cap)) + 1
        effective_timeout_ms = int(
            budget_caps.get("statement_timeout_ms", self._statement_timeout_ms)
        )
        outcome = reader(
            authority,
            limit=limit_with_sentinel,
            statement_timeout_ms=effective_timeout_ms,
            selectors=selectors,
        )
        if inspect.isawaitable(outcome):
            outcome = await outcome
        if not isinstance(outcome, BoundedReadResult):
            raise TypeError(
                "BoundedReader must return a BoundedReadResult "
                f"(got {type(outcome).__name__})"
            )
        return outcome

    def _effective_caps(self, budget: GovernanceReadBudget) -> dict[str, int]:
        # Clamp DOWN to the constructor cap -- a caller cannot widen the
        # bounded-read discipline by passing a larger per-call budget than
        # the constructor cap. Mirrors the Slice-10a
        # ``_clamp_budget_to_ceiling`` precedent at
        # workflows/develop/execution/snapshots.py:202-214.
        # ``statement_timeout_ms`` is clamped here too per doc-13:95
        # (``GovernanceReadBudget.statement_timeout_ms`` is a per-call
        # budget field, not a constructor-only knob); the clamp keeps the
        # caller from raising the ceiling but lets it tighten the timeout.
        return {
            "limit": min(int(budget.max_event_rows), self._limit_cap),
            "max_chars_per_ref": min(
                int(budget.max_chars_per_ref), self._max_chars_per_ref
            ),
            "statement_timeout_ms": min(
                int(budget.statement_timeout_ms), self._statement_timeout_ms
            ),
        }

    @staticmethod
    def _feature_window_selectors(
        feature_id: str, window: GovernanceWindow
    ) -> dict[str, Any]:
        return {
            "feature_id": feature_id,
            "start_cursor": window.start_cursor,
            "end_cursor": window.end_cursor,
            "start_iso": window.start_iso,
            "end_iso": window.end_iso,
            **window.selectors,
        }

    def _build_evidence_set(
        self,
        *,
        feature_id: str | None,
        corpus_id: str,
        authority: EvidenceAuthority,
        reader_result: BoundedReadResult,
        budget: GovernanceReadBudget,
        budget_caps: dict[str, int],
        source_window: dict[str, Any],
    ) -> GovernanceEvidenceSet:
        # Doc-10 ``_typed_bounded`` truncation contract mirrored: the +1
        # sentinel row triggers ``paged`` completeness + drops the sentinel.
        # See execution_control/store.py:1449-1458.
        limit_cap = int(budget_caps["limit"])
        max_chars = int(budget_caps["max_chars_per_ref"])
        raw_rows = reader_result.rows
        budget_exhausted = len(raw_rows) > limit_cap
        capped_rows = raw_rows[:limit_cap] if budget_exhausted else raw_rows
        refs: list[GovernanceEvidenceRef] = []
        for row in capped_rows:
            refs.append(self._project_row_to_ref(authority, row, max_chars))
        # P2-V2-1 finalizer fix (doc-13:217-218 verbatim "mark quality
        # insufficient or derived" + "mark completeness paged or
        # unavailable"). The typed_journal ``ingest_feature_window`` lane
        # bypasses the 13e digester (which only runs in
        # ``ingest_implementation_artifacts``) and constructs the set
        # directly here, so we must apply the SAME overflow-remarking
        # discipline used by the 13h augmenting lanes + 13j legacy lanes
        # via :meth:`_remark_ref_as_paged_on_overflow` (defined above at
        # ``ingestor.py:1088-1131``) so the kept refs carry
        # ``completeness="paged"`` (+ each page-ref ``exact=False``) on
        # budget exhaustion. Without this fix the set would be internally
        # inconsistent: ``set.completeness="paged"`` BUT every
        # ``refs[*].completeness="complete"``. Producer-side fix only;
        # FROZEN ``evidence_set.py`` UNTOUCHED.
        if budget_exhausted:
            refs = [
                self._remark_ref_as_paged_on_overflow(ref) for ref in refs
            ]
        # Doc-13:215-220 -- partial evidence sets carry
        # read_budget_exhausted=True + omitted_refs (exact page refs when
        # possible). In 13b skeleton form we project a single omitted
        # exact-paged ref signalling "there are more rows past the cap"; the
        # later sub-slices that wire the reader to a real source can populate
        # this list with per-row exact page refs.
        omitted_refs: list[GovernanceEvidencePageRef] = []
        if budget_exhausted:
            omitted_refs.append(
                GovernanceEvidencePageRef(
                    page_ref_id=f"{corpus_id}:cap-overflow",
                    authority=authority,
                    source_ref_id=f"{corpus_id}:cap-overflow",
                    item_start=limit_cap,
                    item_end=len(raw_rows),
                    digest=f"sha256:cap-overflow:{limit_cap}",
                    completeness="paged",
                    exact=True,
                    stale_check={
                        "limit_cap": limit_cap,
                        "observed_row_count": len(raw_rows),
                    },
                )
            )
        completeness = "paged" if budget_exhausted else "complete"
        # P2-V2-1 finalizer fix (doc-13:217-218 verbatim "mark quality
        # insufficient or derived" on budget exhaustion). The 13e
        # digester's ``_project_quality`` typed-only branch at
        # ``evidence_set.py:594-599`` demotes typed-only-but-paged to
        # ``"derived"``; this lane bypasses the digester so we mirror
        # the same projection here. A clean typed-only read (no
        # overflow) stays ``"canonical"`` -- doc-13:173-175 mixed
        # typed/legacy is encoded as ``quality="derived"`` + source_mix
        # but this lane is typed-only by construction (single
        # ``typed_journal`` authority).
        quality: EvidenceQuality = "derived" if budget_exhausted else "canonical"
        blockers: list[str] = []
        if budget_exhausted:
            blockers.append("read_budget_exhausted")
        return GovernanceEvidenceSet(
            idempotency_key=f"{corpus_id}:{authority}",
            feature_id=feature_id,
            corpus_id=corpus_id,
            generated_at=_utc_now(),
            source_window=source_window,
            refs=refs,
            omitted_refs=omitted_refs,
            completeness=completeness,
            source_mix={authority: len(refs)} if refs else {},
            read_budget=budget,
            read_budget_exhausted=budget_exhausted,
            quality=quality,
            blockers=blockers,
        )

    def _project_row_to_ref(
        self,
        authority: EvidenceAuthority,
        row: dict[str, Any],
        max_chars: int,
        *,
        quality: EvidenceQuality = "canonical",
    ) -> GovernanceEvidenceRef:
        # The reader returns SUMMARY-only rows (doc-13 § "Bounded reads"
        # non-negotiable + the auto-memory ``feedback_no_silent_degradation``
        # rule); a per-row text field that exceeds the cap is the explicit
        # truncation signal that forces preview_only + exact=False on the
        # corresponding page ref. The cross-validator at
        # models.py:_preview_only_completeness_consistency
        # (doc-13a:24, 109-118) enforces the contract at construction time.
        #
        # Sub-slice 13j (doc-13:173-175 verbatim "Mixed typed/legacy
        # evidence is encoded as ``quality='derived'`` plus source_mix, not
        # as a separate ``EvidenceQuality`` literal") -- the optional
        # ``quality`` keyword-only parameter lets the new legacy lanes
        # (``_ingest_legacy_lane``) pass ``"derived"`` per ref so the
        # ref-level quality is aligned with the 13e digester's set-level
        # ``_project_quality`` "has_legacy" branch
        # (``evidence_set.py:589-592``). The default ``"canonical"``
        # preserves the 13b/13f/13h call-site behaviour for the typed-
        # first authorities (typed_journal / supervisor_digest /
        # resource_snapshot) -- those refs are NOT legacy so the doc-13:173-
        # 175 derived-quality rule does NOT apply on a per-ref basis at
        # the producer layer (the digester demotes typed-only-but-paged
        # sets to ``derived`` at the SET LEVEL via the typed-only +
        # completeness=paged branch at ``evidence_set.py:597-599``; that
        # is a different concern).
        ref_id = str(row.get("ref_id") or row.get("id") or "unknown")
        digest = str(row.get("digest") or f"sha256:row:{ref_id}")
        summary = str(row.get("summary") or row.get("body") or "")
        body_truncated = len(summary) > max_chars
        preview_only = body_truncated
        completeness = "preview_only" if preview_only else "complete"
        # Slice 13A invariant precursor (doc-13a:24, 109-118): when the
        # per-row body exceeds max_chars the page ref records the
        # preview-only state explicitly so a downstream authoritative
        # consumer cannot silently treat the truncated text as exact.
        page_ref = GovernanceEvidencePageRef(
            page_ref_id=f"{ref_id}:row-page",
            authority=authority,
            source_ref_id=ref_id,
            byte_start=0,
            byte_end=min(len(summary), max_chars),
            digest=digest,
            completeness=completeness,
            exact=not preview_only,
            stale_check={
                "row_byte_length": len(summary),
                "max_chars_per_ref": max_chars,
            },
        )
        return GovernanceEvidenceRef(
            authority=authority,
            ref_id=ref_id,
            feature_id=row.get("feature_id"),
            slice_id=row.get("slice_id"),
            artifact_id=row.get("artifact_id"),
            event_id=row.get("event_id"),
            commit_hash=row.get("commit_hash"),
            journal_anchor=row.get("journal_anchor"),
            digest=digest,
            quality=quality,
            completeness=completeness,
            page_refs=[page_ref],
            preview_only=preview_only,
        )


def _utc_now() -> Any:
    """Local helper for the ``generated_at`` timestamp.

    Lives in ``ingestor.py`` (not ``models.py``) so the typed-model
    scaffolding stays pure-typed; the ingestor is the surface that
    introduces clock dependency.
    """

    from datetime import datetime, timezone

    return datetime.now(timezone.utc)
