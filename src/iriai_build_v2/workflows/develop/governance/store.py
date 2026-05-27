"""Slice 13g -- typed-row storage of :class:`GovernanceEvidenceSet`.

This module owns the doc-13:188-190 § "Refactoring Steps" step 6 deliverable
verbatim:

    Store governance evidence sets as typed rows once the Slice 01 store
    exists, and project bounded review artifacts such as
    ``review:governance-evidence:{corpus_id}``.

The 13g sub-slice lands two surfaces:

1. :class:`GovernanceEvidenceStore` -- the typed-row store ABC. Two
   ``async`` methods: :meth:`~GovernanceEvidenceStore.put` (idempotent on
   :attr:`GovernanceEvidenceSet.idempotency_key`; fail-closed on a
   ``corpus_id`` collision with a different idempotency key) and
   :meth:`~GovernanceEvidenceStore.get` (returns the typed row by
   ``corpus_id``, or ``None`` when absent).

2. :class:`InMemoryGovernanceEvidenceStore` -- the in-memory concrete
   implementation. Dict-backed; suitable for tests and for the 13g sub-slice's
   read-only authority surface. The Postgres-backed implementation is a later
   sub-slice (out of scope for 13g per STATUS.md § "Next safe action" point 6).

**Async contract (Slice 13i finalizer).** Both methods are ``async def``.
The 13g initial implementation declared them sync because the in-memory
concrete has no IO; the 13i sub-slice landed a Postgres-backed concrete
that MUST be async (``asyncpg`` cannot be invoked synchronously). The
13i reviewer flagged that overriding a sync ABC method with ``async def``
silently breaks Liskov substitution: a polymorphic caller typed against
:class:`GovernanceEvidenceStore` calling ``store.put(None)`` would get
a coroutine returned (instead of the typed :class:`TypeError`), and a
``store.put(evidence_set)`` would return a coroutine that is never
awaited and so silently drops the write. The 13i finalizer (P2-13i-1)
promoted the ABC + in-memory concrete to ``async def`` so the Postgres
concrete satisfies the contract verbatim and all callers ``await``
uniformly. The in-memory bodies remain pure-Python (no IO); the async
keyword is purely typed-surface alignment. The typed-input
:class:`TypeError` early-raise still fires *synchronously* the moment
the returned coroutine is awaited (a ``raise`` before any ``await`` /
IO is awaitable and propagates as soon as the coroutine is driven).

Plus the bounded review-artifact projection helper:

3. :func:`project_review_artifact` -- returns the
   ``review:governance-evidence:{corpus_id}`` key + canonical-sorted JSON body
   per doc-13:189-190 verbatim. The body is the canonical-sorted JSON of the
   full evidence set; the projection helper is purely read-only and does not
   write to any execution-control or DAG artifact space per doc-13:201-203
   verbatim: "Governance evidence sets may project review artifacts, but no
   ``dag-*`` execution, checkpoint, regroup activation, or merge artifact is
   written by this slice."

**Read-only authority discipline (Slice 13A invariant).** Per the governance
prompt § "Slice 13A invariant for downstream slices" no governance ingestor /
store consumer that influences dispatch / verify / merge / checkpoint /
route / scheduler / policy may consume the typed evidence as **execution
authority** until Slice 13A's evidence-completeness invariant lands. The 13g
store is READ-only authority -- its only writers are the governance ingestor
itself (via :meth:`put`) and its only readers are display-only consumers
(Slice-15 metrics, Slice-16 finding engine, Slice-19 reporting) until Slice
13A lands. The store NEVER writes ``dag-*`` execution / checkpoint / regroup
activation / merge artifacts per doc-13:201-203.

**Fail-closed idempotency.** Two evidence sets with the same ``corpus_id``
but different :attr:`GovernanceEvidenceSet.idempotency_key` values represent
DIFFERENT content snapshots (the 13e digester computes ``idempotency_key`` as
the SHA-256 of the sorted per-ref digest list -- it is the content-fingerprint
of the set). Storing a second set with the same ``corpus_id`` but a different
content fingerprint would silently overwrite the first, losing evidence and
breaking downstream reproducibility. Per the auto-memory
``feedback_no_silent_degradation`` rule + doc-13:215-220 partial-evidence
invariant the conflict is FAIL-CLOSED:
:meth:`GovernanceEvidenceStore.put` raises
:class:`GovernanceEvidenceStoreIdempotencyConflict` (a typed
:class:`ValueError` subclass) when a ``corpus_id`` already exists with a
different ``idempotency_key``. A second ``put`` with the SAME
``(corpus_id, idempotency_key)`` pair is a no-op (idempotent reuse).

**Precedent mirrored.** The in-memory store + typed-conflict-on-mismatch
discipline mirrors:

- :class:`~iriai_build_v2.workflows.develop.execution.failure_router.InMemoryFailureRouterPort`
  at ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:718-749``
  -- dict-based storage, idempotency-key conflict raises a typed
  :class:`~iriai_build_v2.workflows.develop.execution.failure_router.IdempotencyConflict`
  at ``failure_router.py:555-568, 733-737``.
- The canonical-JSON projection helper mirrors ``_jsonb`` at
  ``src/iriai_build_v2/execution_control/regroup_overlay_store.py:306``:
  ``json.dumps(value, sort_keys=True, separators=(",", ":"))``. Two
  equivalent evidence sets project to byte-identical bytes.

**Out of scope for 13g** (per STATUS.md § "Next safe action" point 6):

- Any wiring into :class:`~iriai_build_v2.workflows.develop.phases.ImplementationPhase`
  that calls :meth:`put` at acceptance time (much later sub-slice).
- The Postgres implementation of :class:`GovernanceEvidenceStore` (the
  in-memory implementation is sufficient for the 13g typed-row contract;
  Postgres lands in a later sub-slice with the actual Slice-01 Postgres
  typed-journal seam).
- Any consumption of the typed evidence as **execution authority** (still
  gated on Slice 13A).
- Edits to ``models.py``, ``ingestor.py``, ``journal_parser.py``,
  ``decision_log_parser.py``, ``evidence_set.py`` source (all FROZEN for
  13g).
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod

from .models import GovernanceEvidenceSet


__all__ = [
    # Sub-slice 13g -- typed-row store ABC + in-memory concrete impl
    # (doc-13:188-190 step 6).
    "GovernanceEvidenceStore",
    "InMemoryGovernanceEvidenceStore",
    # Sub-slice 13g -- bounded review-artifact projection helper
    # (doc-13:189-190 verbatim "review:governance-evidence:{corpus_id}").
    "project_review_artifact",
    # Sub-slice 13g -- typed conflict error raised by ``put`` on a
    # ``corpus_id`` collision with a different ``idempotency_key``.
    # **Re-exported at the package level** (governance/__init__.py)
    # per the failure_router precedent at
    # ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:1912``
    # where ``IdempotencyConflict`` is also in ``__all__`` -- consumers
    # can ``except GovernanceEvidenceStoreIdempotencyConflict`` without
    # reaching into the sibling ``.store`` module. The 13g finalizer
    # promoted this from module-local to package-level per P3-13g-R3.
    "GovernanceEvidenceStoreIdempotencyConflict",
]


# --- Errors ----------------------------------------------------------------


class GovernanceEvidenceStoreIdempotencyConflict(ValueError):
    """Raised by :meth:`GovernanceEvidenceStore.put` on a corpus collision.

    Fail-closed when a caller attempts to :meth:`~GovernanceEvidenceStore.put`
    an evidence set whose ``corpus_id`` already exists in the store with a
    DIFFERENT :attr:`GovernanceEvidenceSet.idempotency_key`. Per the 13e
    digester contract the ``idempotency_key`` is the SHA-256 of the sorted
    per-ref digest list -- it is the content-fingerprint of the set. Two sets
    sharing a ``corpus_id`` but disagreeing on ``idempotency_key`` are
    different content snapshots; silently overwriting the first would lose
    evidence and break downstream reproducibility, which violates the
    auto-memory ``feedback_no_silent_degradation`` rule + doc-13:215-220
    partial-evidence invariant.

    Subclasses :class:`ValueError` so callers can either catch the typed
    error specifically or fall through to a generic ``ValueError`` catch.

    **Precedent divergence (P3-13g-R1 carry, documented here per the 13g
    finalizer).** The two sibling typed-conflict precedents inherit from
    typed runtime / control-plane error bases, not from :class:`ValueError`:

    * :class:`~iriai_build_v2.workflows.develop.execution.failure_router.IdempotencyConflict`
      at ``failure_router.py:555-568`` inherits from
      ``FailureRouterError(RuntimeError)``.
    * :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`
      inherits from ``RegroupOverlayStoreError(ExecutionControlError)``.

    The 13g choice of :class:`ValueError` is a **deliberate semantic
    divergence**: this conflict signals "the input you passed conflicts
    with stored content" — i.e. an input-classification failure that fits
    the :class:`ValueError` semantics ("right type, wrong value") rather
    than a generic runtime / IO / control-plane failure. Callers
    structuring their error handling around input validation (e.g. an
    HTTP-API boundary that maps :class:`ValueError` to ``HTTP 400``) get
    the right shape by default.

    **Future-alignment point.** If a later sub-slice introduces a
    governance-error base (e.g. ``GovernanceError(ExecutionControlError)``)
    parallel to ``FailureRouterError`` / ``RegroupOverlayStoreError``,
    align this class there at that point — either by multiple inheritance
    (``GovernanceEvidenceStoreIdempotencyConflict(GovernanceError,
    ValueError)``) or by re-rooting under the new base if the
    :class:`ValueError` semantic is no longer the primary signal. Until
    that base exists, the :class:`ValueError` inheritance is the
    semantically correct choice and no behavior change is required.
    """

    def __init__(
        self,
        corpus_id: str,
        existing_idempotency_key: str,
        incoming_idempotency_key: str,
    ) -> None:
        super().__init__(
            f"GovernanceEvidenceStore corpus_id={corpus_id!r} already has "
            f"a stored evidence set with idempotency_key="
            f"{existing_idempotency_key!r}; the incoming evidence set has "
            f"a DIFFERENT idempotency_key={incoming_idempotency_key!r}. "
            "Per doc-13:188-190 the corpus_id is the typed-row identity "
            "and the idempotency_key is the content-fingerprint; a "
            "conflict on the (corpus_id, idempotency_key) pair is a "
            "content mismatch and must fail-closed per the no-silent-"
            "degradation rule. Re-ingest the corpus to obtain a fresh "
            "idempotency_key, or delete the existing row before storing "
            "the new content."
        )
        # Attribute-level access mirrors
        # failure_router.IdempotencyConflict.__init__ at failure_router.py:561-568
        # so consumers can introspect the conflict programmatically without
        # parsing the message string.
        self.corpus_id = corpus_id
        self.existing_idempotency_key = existing_idempotency_key
        self.incoming_idempotency_key = incoming_idempotency_key


# --- ABC -------------------------------------------------------------------


class GovernanceEvidenceStore(ABC):
    """Doc-13:188-190 step 6 -- typed-row store for :class:`GovernanceEvidenceSet`.

    Two ``async`` methods compose the doc-13:188-190 verbatim contract:

    * :meth:`put` -- bounded write. Idempotent on
      :attr:`GovernanceEvidenceSet.idempotency_key`: a second ``put`` with
      the same ``(corpus_id, idempotency_key)`` pair is a no-op (returns
      silently); a ``put`` whose ``corpus_id`` already exists with a
      DIFFERENT ``idempotency_key`` raises
      :class:`GovernanceEvidenceStoreIdempotencyConflict` (fail-closed per
      ``feedback_no_silent_degradation``). The store NEVER writes to the
      ``dag-*`` execution / checkpoint / regroup activation / merge artifact
      spaces per doc-13:201-203 verbatim.

    * :meth:`get` -- bounded read. Returns the typed
      :class:`GovernanceEvidenceSet` row by ``corpus_id``, or ``None`` when
      absent. The returned row's
      :attr:`~GovernanceEvidenceSet.read_budget_exhausted` /
      :attr:`~GovernanceEvidenceSet.omitted_refs` fields project the
      partial-read invariant per doc-13:215-220 (the store preserves these
      fields verbatim; it does NOT re-derive them).

    Per the governance prompt § "Slice 13A invariant for downstream slices"
    no consumer of this store may treat its output as **execution authority**
    until Slice 13A's evidence-completeness invariant lands. Until then the
    store's read surface is consumable by Slice-15 metrics, Slice-16 finding
    engine, and Slice-19 reporting in display-only mode.

    **Async contract (Slice 13i finalizer).** Both methods are ``async def``
    so the Postgres-backed concrete (which MUST be async because
    :mod:`asyncpg` cannot be invoked synchronously) and the in-memory
    concrete share an identical typed-surface signature. The in-memory
    concrete's bodies are pure-Python and do no IO; the ``async`` keyword
    is a typed-surface alignment for Liskov substitution. A polymorphic
    caller can ``await store.put(...)`` / ``await store.get(...)`` without
    knowing which concrete it holds.
    """

    @abstractmethod
    async def put(self, evidence_set: GovernanceEvidenceSet) -> None:
        """Doc-13:188-190 -- store a typed :class:`GovernanceEvidenceSet` row.

        **Idempotency contract:**

        * Same ``(corpus_id, idempotency_key)`` -- no-op (the existing row
          is the same content; storing it again is a legitimate retry).
        * Same ``corpus_id`` but different ``idempotency_key`` --
          :class:`GovernanceEvidenceStoreIdempotencyConflict` (fail-closed
          per ``feedback_no_silent_degradation``).
        * New ``corpus_id`` -- the row is inserted.

        Concrete implementations MUST preserve the partial-read invariant
        (doc-13:215-220): the
        :attr:`~GovernanceEvidenceSet.read_budget_exhausted` /
        :attr:`~GovernanceEvidenceSet.omitted_refs` fields are stored
        verbatim and returned verbatim by :meth:`get`.

        :param evidence_set: The typed evidence set to store.
        :raises GovernanceEvidenceStoreIdempotencyConflict: When
            ``evidence_set.corpus_id`` already exists in the store with a
            different ``idempotency_key``.
        """

    @abstractmethod
    async def get(self, corpus_id: str) -> GovernanceEvidenceSet | None:
        """Doc-13:188-190 -- load a typed :class:`GovernanceEvidenceSet` row.

        Returns the stored :class:`GovernanceEvidenceSet` for ``corpus_id``,
        or ``None`` when no row matches. Fail-OPEN on a missing corpus is
        acceptable for the store surface; the governance ingestor is what
        fail-closes on a missing implementation journal (doc-13:207-208).

        :param corpus_id: The typed-row identity to look up.
        :returns: The stored :class:`GovernanceEvidenceSet`, or ``None``.
        """


# --- Concrete in-memory implementation -------------------------------------


class InMemoryGovernanceEvidenceStore(GovernanceEvidenceStore):
    """In-memory :class:`GovernanceEvidenceStore` for tests + Slice-13g surface.

    Dict-backed concrete implementation suitable for the 13g typed-row contract
    + the test surface. The Postgres-backed implementation is a later sub-slice
    (out of scope for 13g per STATUS.md § "Next safe action" point 6).

    Mirrors the
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.InMemoryFailureRouterPort`
    precedent at ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:718-749``:
    dict-based storage keyed on the idempotency-identity, idempotency-key
    conflict raises a typed :class:`ValueError` subclass.

    The store holds typed :class:`GovernanceEvidenceSet` objects directly
    (not their serialised form). Pydantic models are immutable-ish by
    convention but the store is robust against in-place mutation by callers:
    the dict holds the model object reference; if a caller mutates the
    returned object the store reflects the mutation. Consumers that need a
    detached snapshot should call :meth:`~pydantic.BaseModel.model_copy`
    after :meth:`get`.
    """

    def __init__(self) -> None:
        # corpus_id -> GovernanceEvidenceSet. Single-keyed dict mirrors the
        # InMemoryFailureRouterPort pattern at failure_router.py:723
        # (`failures_by_key: dict[str, int]`).
        self._by_corpus_id: dict[str, GovernanceEvidenceSet] = {}

    async def put(self, evidence_set: GovernanceEvidenceSet) -> None:
        """Store ``evidence_set`` idempotently on
        ``(corpus_id, idempotency_key)``.

        See :meth:`GovernanceEvidenceStore.put` for the full contract.

        **Async surface (Slice 13i finalizer).** ``async def`` mirrors the
        ABC's async contract for Liskov parity with the Postgres concrete;
        the body is pure-Python and does no IO (no ``await``). The typed
        :class:`TypeError` early-raise still fires synchronously the
        moment the returned coroutine is awaited (the ``raise`` is
        reached before any ``await`` would otherwise yield control).
        """

        # Fail-fast typed-input validation at the API entry boundary per
        # ``feedback_no_silent_degradation`` -- without this guard a
        # ``put(None)`` or ``put("not-a-model")`` would crash on
        # ``.corpus_id`` attribute access below with an opaque
        # ``AttributeError`` instead of a typed ``TypeError``. The 13g
        # finalizer added this guard per P3-13g-R2 (mirrors the Pydantic
        # convention of fail-closed on bad input at the typed boundary).
        if evidence_set is None or not isinstance(
            evidence_set, GovernanceEvidenceSet
        ):
            raise TypeError(
                "GovernanceEvidenceStore.put requires a "
                "GovernanceEvidenceSet instance; got "
                f"{type(evidence_set).__name__!r} (value={evidence_set!r}). "
                "The store accepts the typed-row contract only -- "
                "callers must construct or load a GovernanceEvidenceSet "
                "via the 13e digester (compose_governance_evidence_set) "
                "or the 13b ingestor "
                "(DefaultGovernanceEvidenceIngestor.ingest_*) before "
                "calling put."
            )

        existing = self._by_corpus_id.get(evidence_set.corpus_id)
        if existing is not None:
            if existing.idempotency_key != evidence_set.idempotency_key:
                # Fail-closed per doc-13:188-190 + feedback_no_silent_degradation.
                # Mirrors failure_router.py:733-737 (IdempotencyConflict raise
                # on input_digest mismatch under a same idempotency_key).
                raise GovernanceEvidenceStoreIdempotencyConflict(
                    evidence_set.corpus_id,
                    existing.idempotency_key,
                    evidence_set.idempotency_key,
                )
            # Same (corpus_id, idempotency_key) -- no-op (legitimate retry /
            # idempotent reuse).
            return
        self._by_corpus_id[evidence_set.corpus_id] = evidence_set

    async def get(self, corpus_id: str) -> GovernanceEvidenceSet | None:
        """Load the stored :class:`GovernanceEvidenceSet` by ``corpus_id``.

        See :meth:`GovernanceEvidenceStore.get` for the full contract.

        **Async surface (Slice 13i finalizer).** ``async def`` mirrors the
        ABC's async contract for Liskov parity with the Postgres concrete;
        the body is pure-Python and does no IO (no ``await``).
        """

        return self._by_corpus_id.get(corpus_id)


# --- Bounded review-artifact projection helper -----------------------------


def project_review_artifact(
    evidence_set: GovernanceEvidenceSet,
) -> tuple[str, str]:
    """Project ``evidence_set`` to a bounded review artifact.

    Per doc-13:189-190 verbatim ("project bounded review artifacts such as
    ``review:governance-evidence:{corpus_id}``") this helper returns:

    * ``(artifact_key, artifact_value)`` where
      ``artifact_key == f"review:governance-evidence:{evidence_set.corpus_id}"``
      -- the doc-13:189-190 review-artifact key shape verbatim;
    * ``artifact_value`` is the canonical-sorted JSON projection of the full
      evidence set (via
      :meth:`~pydantic.BaseModel.model_dump` with ``mode="json"`` +
      :func:`json.dumps` with ``sort_keys=True, separators=(",", ":")``)
      mirroring the ``_jsonb`` helper at
      ``src/iriai_build_v2/execution_control/regroup_overlay_store.py:306``.
      Two equivalent evidence sets project to BYTE-IDENTICAL bytes.

    The projection is **read-only**: it does NOT write to any execution-
    control or DAG artifact space per doc-13:201-203 verbatim ("Governance
    evidence sets may project review artifacts, but no ``dag-*`` execution,
    checkpoint, regroup activation, or merge artifact is written by this
    slice"). The returned artifact value is bounded by the size of the
    evidence set's typed surface; per doc-13:186 the digester operates on
    source ids + content digests only (NEVER artifact bodies), so the
    review-artifact value is a bounded typed-surface projection -- never an
    unbounded artifact-body hydration.

    The returned :attr:`~GovernanceEvidenceSet.read_budget_exhausted` /
    :attr:`~GovernanceEvidenceSet.omitted_refs` fields are preserved verbatim
    in the projection so downstream consumers can detect partial reads per
    doc-13:215-220.

    :param evidence_set: The typed evidence set to project.
    :returns: ``(artifact_key, artifact_value_canonical_json)`` tuple.
    """

    artifact_key = f"review:governance-evidence:{evidence_set.corpus_id}"
    # model_dump(mode="json") projects Pydantic types (datetime, etc.) to
    # JSON-safe primitives so json.dumps does not need a `default=` callback.
    # The canonical-JSON form (sort_keys=True + compact separators) mirrors
    # _jsonb at execution_control/regroup_overlay_store.py:306 -- two
    # equivalent sets project to byte-identical bytes (the property the
    # 13g test surface pins).
    artifact_value = json.dumps(
        evidence_set.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
    )
    return artifact_key, artifact_value
