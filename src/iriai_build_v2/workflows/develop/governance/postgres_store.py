"""Slice 13i -- Postgres-backed :class:`GovernanceEvidenceStore` concrete.

This module owns the doc-13:188-190 § "Refactoring Steps" step 6
deliverable verbatim:

    Store governance evidence sets as typed rows once the Slice 01 store
    exists, and project bounded review artifacts such as
    ``review:governance-evidence:{corpus_id}``.

The 13g sub-slice landed the abstract :class:`GovernanceEvidenceStore`
contract (``put`` + ``get``) + the dict-backed
:class:`~iriai_build_v2.workflows.develop.governance.store.InMemoryGovernanceEvidenceStore`
concrete + the
:func:`~iriai_build_v2.workflows.develop.governance.store.project_review_artifact`
bounded review-artifact projection helper +
:class:`~iriai_build_v2.workflows.develop.governance.store.GovernanceEvidenceStoreIdempotencyConflict`
typed error.

The 13i sub-slice closes step 6 by adding
:class:`PostgresGovernanceEvidenceStore` -- the Postgres-backed concrete
the production stack will use over the new ``governance_evidence_sets``
table (``schema.sql:898-957``). The Postgres concrete satisfies the same
13g ABC contract identically (idempotent on
:attr:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceSet.idempotency_key`;
fail-closed on a ``corpus_id`` collision with a different
``idempotency_key`` per the auto-memory
``feedback_no_silent_degradation`` rule; ``put(None)`` raises
:class:`TypeError` per the 13g finalizer's P3-13g-R2 fix).

**Async contract.** The 13g ABC declares ``async`` ``put`` / ``get``
signatures (as of the 13i finalizer; see ``store.py`` module docstring
§ "Async contract (Slice 13i finalizer)"). The in-memory concrete is
also ``async def`` (typed-surface alignment; its bodies do no IO).
:class:`PostgresGovernanceEvidenceStore` MUST be async because
:mod:`asyncpg` cannot be invoked synchronously; the async ABC promotion
landed by the 13i finalizer eliminated the prior Liskov-substitution
risk where overriding a sync ABC method with ``async def`` silently
returned a coroutine instead of raising / writing. The sibling Postgres
stores (``RegroupOverlayStore`` at
``execution_control/regroup_overlay_store.py:320-329``;
``MergeQueueStore``) are all async with the same pattern. Callers
``await store.put(...)`` / ``await store.get(...)`` uniformly across
both concretes; the parametrized 13i contract tests construct the
in-memory concrete directly (no shim needed since both share the same
async surface).

**Precedent mirrored.** Closely follows
``src/iriai_build_v2/execution_control/regroup_overlay_store.py``:

* Connection-bound async store (:class:`RegroupOverlayStore.__init__` at
  ``regroup_overlay_store.py:329-330`` — takes an :class:`asyncpg.Connection`
  via the constructor).
* Canonical-JSON projection helper (:func:`_jsonb` at
  ``regroup_overlay_store.py:305-306`` —
  ``json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)``).
* Transactional ``INSERT`` with idempotency-key uniqueness +
  ``asyncpg.UniqueViolationError`` fallback to identity-resolution
  (``regroup_overlay_store.py:382-435``).
* In-transaction fail-closed conflict check (the
  ``record_validation`` pattern at ``regroup_overlay_store.py:602-621`` —
  ``SELECT ... WHERE digest <> $`` to detect a different fingerprint
  for the same identity, then raise the typed conflict).
* Typed-input-validation guard (mirrors 13g
  ``store.py:299-326`` — :class:`TypeError` raise on
  non-:class:`GovernanceEvidenceSet` input).

The schema migration runs via :func:`iriai_build_v2.db.ensure_schema`
at ``db.py:32-35`` — the whole of ``schema.sql`` is executed by
``await conn.execute(sql)``; the new ``governance_evidence_sets`` block
at ``schema.sql:898-957`` is loaded by that single call (no new
migration framework introduced).

**Out of scope for 13i** (per STATUS.md § "Next safe action" point 8):

* Any wiring into :class:`~iriai_build_v2.workflows.develop.phases.ImplementationPhase`
  that calls :meth:`put` at acceptance time (deferred to a later
  sub-slice).
* Any consumption of the typed evidence as **execution authority**
  (still gated on Slice 13A's evidence-completeness invariant).
* The doc-13 § Refactoring Steps step 7 legacy event/artifact bounded
  ingestion (deferred to a later sub-slice).
* Edits to ``models.py`` / ``ingestor.py`` / ``journal_parser.py`` /
  ``decision_log_parser.py`` / ``evidence_set.py`` / ``store.py``
  source (all FROZEN for 13i; only ``__init__.py`` is touched to add
  the new re-export).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from .models import GovernanceEvidenceSet
from .store import (
    GovernanceEvidenceStore,
    GovernanceEvidenceStoreIdempotencyConflict,
)


__all__ = [
    # Sub-slice 13i -- Postgres-backed concrete that implements the
    # 13g GovernanceEvidenceStore ABC over the ``governance_evidence_sets``
    # table (doc-13:188-190 step 6).
    "PostgresGovernanceEvidenceStore",
]


# --- JSON helpers ----------------------------------------------------------
#
# ``_jsonb`` mirrors the sibling Postgres-store helper at
# ``src/iriai_build_v2/execution_control/regroup_overlay_store.py:305-306``
# verbatim: ``json.dumps(value, sort_keys=True, separators=(",", ":"),
# default=str)``. Two equivalent evidence-set projections render to
# byte-identical JSONB bytes (the property the 13g
# :func:`~iriai_build_v2.workflows.develop.governance.project_review_artifact`
# helper also pins; here we use the same canonical form so the JSONB
# bytes the typed-row store writes are byte-identical to the canonical
# bytes the review-artifact projection emits, modulo top-level vs
# per-column ordering).


def _jsonb(value: Any) -> str:
    """Canonical-JSON serialiser for the JSONB columns.

    Mirrors ``execution_control/regroup_overlay_store.py:305-306`` verbatim:
    ``sort_keys=True`` + compact separators + ``default=str`` (the
    ``default=str`` handles :class:`~datetime.datetime` / other
    non-JSON-safe primitives that survive
    :meth:`~pydantic.BaseModel.model_dump` ``mode="json"`` — defence in
    depth even though pydantic's mode='json' should not emit any).
    """

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Any, default: Any) -> Any:
    """Best-effort JSONB-column loader.

    Mirrors ``execution_control/regroup_overlay_store.py:309-314`` —
    asyncpg returns JSONB as ``str``/``bytes`` by default (unless a
    type codec is registered), so we always re-parse before returning
    the typed shape to the caller. ``default`` is returned for ``NULL``
    columns (the schema has none nullable, but the helper is defensive).
    """

    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value


# --- Postgres-backed concrete ---------------------------------------------


class PostgresGovernanceEvidenceStore(GovernanceEvidenceStore):
    """Postgres-backed :class:`GovernanceEvidenceStore` concrete (Slice 13i).

    Connection-bound async store that persists typed
    :class:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceSet`
    rows in the ``governance_evidence_sets`` table per doc-13:188-190
    step 6 verbatim ("Store governance evidence sets as typed rows once
    the Slice 01 store exists, and project bounded review artifacts such
    as ``review:governance-evidence:{corpus_id}``").

    Mirrors the
    :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore`
    precedent: each caller holds its own store over its own
    :class:`asyncpg.Connection`. The store NEVER acquires its own pool /
    connection; the caller owns the lifecycle (the 13g surface is
    advisory / read-only and does not multiplex over a pool).

    **Async surface.** ``put`` / ``get`` are ``async def`` because
    :mod:`asyncpg` cannot be invoked synchronously. As of the 13i
    finalizer the 13g ABC's ``put`` / ``get`` are also ``async def`` —
    the in-memory concrete was promoted alongside the ABC for typed-
    surface parity (its bodies remain pure-Python, no IO). The async-
    ABC promotion eliminated the prior Liskov-substitution risk where
    overriding a sync ABC method with ``async def`` silently returned
    a coroutine instead of raising / writing (a polymorphic caller
    typed against the sync ABC would silently drop the side-effect by
    never awaiting the returned coroutine). Callers ``await`` the
    methods on BOTH concretes uniformly; the parametrized 13i contract
    tests use the in-memory concrete directly (no shim needed).

    **Idempotency contract** (mirrors the in-memory 13g concrete
    verbatim):

    * Same ``(corpus_id, idempotency_key)`` -- no-op (the existing row
      is the same content; storing it again is a legitimate retry).
    * Same ``corpus_id`` but different ``idempotency_key`` --
      :class:`GovernanceEvidenceStoreIdempotencyConflict` (fail-closed
      per the auto-memory ``feedback_no_silent_degradation`` rule). The
      in-transaction ``SELECT idempotency_key FROM ... WHERE corpus_id
      = $`` check is the semantic guard; the composite UNIQUE constraint
      on ``(corpus_id, idempotency_key)`` is the typed-row-level
      belt-and-suspenders safety net.
    * New ``corpus_id`` -- the row is inserted.

    **Typed-input-validation guard.** ``put(None)`` / ``put("foo")`` /
    ``put({...})`` raises :class:`TypeError` mirroring the 13g
    in-memory store's P3-13g-R2 fix at ``store.py:299-326``. Without
    this guard the method would crash on attribute access with an
    opaque :class:`AttributeError`, violating the
    ``feedback_no_silent_degradation`` rule.

    **Read-only authority discipline (Slice 13A invariant).** The store
    NEVER writes to ``dag-*`` execution / checkpoint / regroup
    activation / merge artifact spaces per doc-13:201-203 verbatim;
    the surface holds typed governance evidence rows only. The
    governance layer is analytical / advisory / read-only until Slice
    13A's evidence-completeness invariant lands.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        """Bind the store to a caller-owned :class:`asyncpg.Connection`.

        Mirrors :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore.__init__`
        at ``regroup_overlay_store.py:329-330`` -- the store is
        connection-bound, never pool-bound, so the caller owns lifecycle
        (acquire / release / commit on the surrounding transaction).
        """

        self._conn = conn

    # NOTE: as of the 13i finalizer the 13g ABC ``put`` / ``get`` are
    # ``async def`` (Liskov-parity with this concrete); the
    # ``# type: ignore[override]`` markers from the initial 13i
    # implementation were removed because the signatures now match
    # verbatim (see ``store.py`` module docstring § "Async contract
    # (Slice 13i finalizer)"). Callers ``await`` these methods on
    # BOTH concretes uniformly.

    async def put(self, evidence_set: GovernanceEvidenceSet) -> None:
        """Store ``evidence_set`` idempotently on
        ``(corpus_id, idempotency_key)`` per doc-13:188-190 step 6.

        See :meth:`GovernanceEvidenceStore.put` for the full ABC contract.

        **Idempotency contract** (mirrors the 13g in-memory store
        verbatim):

        * Same ``(corpus_id, idempotency_key)`` -- no-op (legitimate
          retry / idempotent reuse).
        * Same ``corpus_id`` but different ``idempotency_key`` --
          :class:`GovernanceEvidenceStoreIdempotencyConflict`
          (fail-closed per ``feedback_no_silent_degradation``).
        * New ``corpus_id`` -- the row is inserted.

        :param evidence_set: The typed evidence set to store.
        :raises TypeError: When ``evidence_set`` is not a
            :class:`GovernanceEvidenceSet` instance (mirrors 13g
            in-memory store's P3-13g-R2 guard at
            ``store.py:299-326``).
        :raises GovernanceEvidenceStoreIdempotencyConflict: When
            ``evidence_set.corpus_id`` already exists in the table
            with a different ``idempotency_key``.
        """

        # Fail-fast typed-input validation at the API entry boundary per
        # ``feedback_no_silent_degradation`` -- without this guard a
        # ``put(None)`` or ``put("not-a-model")`` would crash on
        # ``.corpus_id`` attribute access below with an opaque
        # ``AttributeError`` instead of a typed ``TypeError``. Mirrors
        # the 13g finalizer fix at ``store.py:299-326`` (P3-13g-R2).
        if evidence_set is None or not isinstance(
            evidence_set, GovernanceEvidenceSet
        ):
            raise TypeError(
                "PostgresGovernanceEvidenceStore.put requires a "
                "GovernanceEvidenceSet instance; got "
                f"{type(evidence_set).__name__!r} (value={evidence_set!r}). "
                "The store accepts the typed-row contract only -- "
                "callers must construct or load a GovernanceEvidenceSet "
                "via the 13e digester (compose_governance_evidence_set) "
                "or the 13b ingestor "
                "(DefaultGovernanceEvidenceIngestor.ingest_*) before "
                "calling put."
            )

        # Canonical-JSON projection of the typed model (mode="json"
        # produces JSON-safe primitives; ``_jsonb`` ensures
        # sort_keys + compact separators for byte-identical JSONB).
        dumped = evidence_set.model_dump(mode="json")

        try:
            async with self._conn.transaction():
                # Fail-closed conflict check inside the transaction
                # (mirrors regroup_overlay_store.py:602-621 — the
                # ``SELECT ... WHERE digest <> $`` pattern). Detecting
                # the conflict before the INSERT lets us raise the
                # typed Python error with attribute-level
                # ``existing_idempotency_key`` / ``incoming_idempotency_key``
                # rather than the opaque asyncpg UniqueViolationError
                # the composite UNIQUE constraint would otherwise raise.
                existing_key = await self._conn.fetchval(
                    "SELECT idempotency_key FROM governance_evidence_sets "
                    "WHERE corpus_id = $1 LIMIT 1",
                    evidence_set.corpus_id,
                )
                if existing_key is not None:
                    if existing_key != evidence_set.idempotency_key:
                        # Fail-closed per doc-13:188-190 +
                        # feedback_no_silent_degradation. Mirrors
                        # regroup_overlay_store.py:615-621 — typed
                        # raise on digest mismatch within a transaction.
                        raise GovernanceEvidenceStoreIdempotencyConflict(
                            evidence_set.corpus_id,
                            str(existing_key),
                            evidence_set.idempotency_key,
                        )
                    # Same (corpus_id, idempotency_key) -- no-op
                    # (legitimate retry / idempotent reuse).
                    return

                # New row -- INSERT. The asyncpg.UniqueViolationError
                # fallback below handles the race where a concurrent
                # transaction inserted the same idempotency_key first.
                await self._conn.execute(
                    "INSERT INTO governance_evidence_sets "
                    "(idempotency_key, feature_id, corpus_id, "
                    " generated_at, source_window, refs, omitted_refs, "
                    " completeness, source_mix, read_budget, "
                    " read_budget_exhausted, quality, blockers) "
                    "VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, "
                    " $7::jsonb, $8, $9::jsonb, $10::jsonb, $11, "
                    " $12, $13::jsonb)",
                    evidence_set.idempotency_key,
                    evidence_set.feature_id,
                    evidence_set.corpus_id,
                    evidence_set.generated_at,
                    _jsonb(dumped["source_window"]),
                    _jsonb(dumped["refs"]),
                    _jsonb(dumped["omitted_refs"]),
                    evidence_set.completeness,
                    _jsonb(dumped["source_mix"]),
                    _jsonb(dumped["read_budget"]),
                    evidence_set.read_budget_exhausted,
                    evidence_set.quality,
                    _jsonb(dumped["blockers"]),
                )
        except asyncpg.UniqueViolationError:
            # A concurrent INSERT created the same row first. Resolve
            # idempotently: the duplicate idempotency_key means the same
            # content fingerprint (the SELECT above re-runs in a fresh
            # transaction to confirm the corpus_id collision is on the
            # SAME idempotency_key — if not, the second-attempt
            # conflict check raises the typed conflict). Mirrors
            # regroup_overlay_store.py:424-434 idempotent-resolution
            # pattern.
            existing_key = await self._conn.fetchval(
                "SELECT idempotency_key FROM governance_evidence_sets "
                "WHERE corpus_id = $1 LIMIT 1",
                evidence_set.corpus_id,
            )
            if existing_key is None:  # pragma: no cover - non-idem-key race
                raise
            if existing_key != evidence_set.idempotency_key:
                raise GovernanceEvidenceStoreIdempotencyConflict(
                    evidence_set.corpus_id,
                    str(existing_key),
                    evidence_set.idempotency_key,
                )
            # Same identity landed concurrently -- legitimate idempotent
            # retry, no-op.
            return

    async def get(self, corpus_id: str) -> GovernanceEvidenceSet | None:
        """Load the stored :class:`GovernanceEvidenceSet` by ``corpus_id``.

        See :meth:`GovernanceEvidenceStore.get` for the full ABC contract.
        Returns ``None`` when no row matches (fail-OPEN on a missing
        corpus is acceptable for the store surface; the governance
        ingestor is what fail-closes on a missing implementation
        journal per doc-13:207-208).

        The typed row is rebuilt from the row's columns via
        :meth:`~pydantic.BaseModel.model_validate` -- the JSONB
        columns are re-parsed via :func:`_loads` and the scalar
        columns flow through verbatim. The
        :class:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceSet`
        model validators fire on rebuild (same invariants as a fresh
        ``model_validate_json`` call).

        :param corpus_id: The typed-row identity to look up.
        :returns: The stored :class:`GovernanceEvidenceSet`, or
            ``None`` when no row matches.
        """

        row = await self._conn.fetchrow(
            "SELECT idempotency_key, feature_id, corpus_id, generated_at, "
            "source_window, refs, omitted_refs, completeness, source_mix, "
            "read_budget, read_budget_exhausted, quality, blockers "
            "FROM governance_evidence_sets "
            "WHERE corpus_id = $1 "
            "ORDER BY id DESC LIMIT 1",
            corpus_id,
        )
        if row is None:
            return None

        # Rebuild the typed model from the row's columns. The JSONB
        # columns are re-parsed via _loads (asyncpg returns JSONB as
        # str/bytes by default unless a type codec is registered).
        rebuilt: dict[str, Any] = {
            "idempotency_key": row["idempotency_key"],
            "feature_id": row["feature_id"],
            "corpus_id": row["corpus_id"],
            "generated_at": row["generated_at"],
            "source_window": _loads(row["source_window"], {}),
            "refs": _loads(row["refs"], []),
            "omitted_refs": _loads(row["omitted_refs"], []),
            "completeness": row["completeness"],
            "source_mix": _loads(row["source_mix"], {}),
            "read_budget": _loads(row["read_budget"], {}),
            "read_budget_exhausted": bool(row["read_budget_exhausted"]),
            "quality": row["quality"],
            "blockers": _loads(row["blockers"], []),
        }
        return GovernanceEvidenceSet.model_validate(rebuilt)
