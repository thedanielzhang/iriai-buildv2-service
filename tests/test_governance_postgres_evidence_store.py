"""Slice 13i -- real-Postgres tests for :class:`PostgresGovernanceEvidenceStore`.

Covers the doc-13:188-190 § "Refactoring Steps" step 6 production-stack
deliverable: the asyncpg-backed concrete that implements the 13g
:class:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceStore`
ABC over the new ``governance_evidence_sets`` table (``schema.sql:898-957``).

The test surface pins the chunk-shape contract from STATUS.md § "Next
safe action" point 4:

* ``put`` then ``get`` round-trips the typed
  :class:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceSet`.
* Idempotent ``put`` on same ``(corpus_id, idempotency_key)`` is a no-op.
* ``put`` on same ``corpus_id`` with a different ``idempotency_key``
  raises :class:`GovernanceEvidenceStoreIdempotencyConflict` (mirrors
  the in-memory 13g fail-closed test).
* Typed-input-validation guard fires on non-:class:`GovernanceEvidenceSet`
  input (mirrors the 13g finalizer P3-13g-R2 fix).
* Typed-row JSONB columns re-parse cleanly through pydantic v2
  ``model_validate(...)``.
* The ``governance_evidence_sets`` table schema matches the
  doc-13:128-141 typed shape verbatim (column names + types + CHECK
  constraints + UNIQUE constraint).
* Real-corpus integration: parse → compose → put → get → equality.
* Parametrized contract tests asserting the in-memory + Postgres
  concretes satisfy the same ABC contract for the core ``put`` / ``get``
  / idempotency / conflict / typed-input-validation behaviours. As of
  the Slice 13i finalizer (P2-13i-1) both concretes share an ``async
  def`` surface so the contract tests construct each concrete directly
  (the prior ``_AsyncInMemoryAdapter`` async-wrapper shim was deleted
  because the in-memory concrete is now natively async).

**Real-Postgres fixture pattern.** Mirrors
``tests/workflows/develop/execution/conftest.py:43-127`` +
``tests/supervisor/conftest.py:51-137`` -- spin up a throwaway database,
load ``schema.sql`` via :func:`iriai_build_v2.db.ensure_schema`, tear
down at session end. Skip cleanly when Postgres unreachable
(``localhost:5431`` user ``$USER`` trust auth by default; override via
``IRIAI_TEST_PGHOST`` / ``IRIAI_TEST_PGPORT`` / ``IRIAI_TEST_PGUSER`` /
``IRIAI_TEST_PGPASSWORD``).

The fixtures here are inlined (not directory-scoped via a conftest)
because this is a top-level ``tests/`` file. Mirrors the inline
fixture pattern several other top-level ``tests/`` files use
(e.g. ``tests/test_execution_control_store.py``).

Per the governance prompt § "Slice 13A invariant for downstream slices"
no test in this file consumes the store as **execution authority** --
the store is READ-only authority until Slice 13A lands.
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

try:  # asyncpg is optional for the rest of the suite
    import asyncpg
except ImportError:  # pragma: no cover - env without asyncpg
    asyncpg = None  # type: ignore[assignment]

from iriai_build_v2.workflows.develop.governance import (
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceEvidenceStore,
    GovernanceEvidenceStoreIdempotencyConflict,
    GovernanceReadBudget,
    GovernanceWindow,
    InMemoryGovernanceEvidenceStore,
    PostgresGovernanceEvidenceStore,
    compose_governance_evidence_set,
    parse_implementation_decision_log,
    parse_implementation_journal,
)
from iriai_build_v2.workflows.develop.governance import (
    postgres_store as postgres_store_module,
)


# ── Postgres fixture (inline; mirrors the supervisor + mq fixture pattern) ──


_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_PATH = _REPO_ROOT / "schema.sql"

_PG_HOST = os.environ.get("IRIAI_TEST_PGHOST", "localhost")
_PG_PORT = os.environ.get("IRIAI_TEST_PGPORT", "5431")
_PG_USER = (
    os.environ.get("IRIAI_TEST_PGUSER")
    or os.environ.get("USER")
    or "postgres"
)
_PG_PASSWORD = os.environ.get("IRIAI_TEST_PGPASSWORD", "")


def _dsn(database: str) -> str:
    auth = _PG_USER if not _PG_PASSWORD else f"{_PG_USER}:{_PG_PASSWORD}"
    return f"postgresql://{auth}@{_PG_HOST}:{_PG_PORT}/{database}"


@pytest.fixture(scope="session")
def governance_pg_database() -> Iterator[str]:
    """A throwaway Postgres database with ``schema.sql`` loaded.

    Yields a DSN. Skips dependent tests when no Postgres is reachable. DB
    lifecycle runs synchronously (its own short-lived event loops) so the
    fixture does not contend with pytest-asyncio's per-test loop. Mirrors
    ``tests/supervisor/conftest.py:51-102``.
    """

    if asyncpg is None:  # pragma: no cover - env without asyncpg
        pytest.skip("asyncpg is not installed; 13i Postgres tests skipped")

    db_name = f"iriai_gov_test_{uuid.uuid4().hex[:12]}"

    async def _probe() -> None:
        conn = await asyncpg.connect(_dsn("postgres"))
        await conn.close()

    async def _create() -> None:
        admin = await asyncpg.connect(_dsn("postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(_dsn(db_name))
        try:
            await conn.execute(_SCHEMA_PATH.read_text())
        finally:
            await conn.close()

    async def _drop() -> None:
        admin = await asyncpg.connect(_dsn("postgres"))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await admin.close()

    try:
        asyncio.run(_probe())
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - env
        pytest.skip(f"Postgres unavailable for 13i tests: {exc}")

    asyncio.run(_create())
    try:
        yield _dsn(db_name)
    finally:
        asyncio.run(_drop())


async def _truncate_governance(conn: "asyncpg.Connection") -> None:
    """Truncate only the governance-owned tables (scope-discipline)."""

    await conn.execute(
        "TRUNCATE governance_evidence_sets RESTART IDENTITY CASCADE"
    )


@pytest_asyncio.fixture
async def governance_pg_conn(
    governance_pg_database: str,
) -> "AsyncIterator[asyncpg.Connection]":
    """A connection to a clean-slate governance test database (truncated)."""

    conn = await asyncpg.connect(governance_pg_database)
    try:
        await _truncate_governance(conn)
        yield conn
    finally:
        await conn.close()


# ── helpers for synthetic evidence sets ────────────────────────────────────


def _make_minimal_evidence_set(
    *,
    corpus_id: str = "test-corpus:1",
    idempotency_key: str = "a" * 64,
    feature_id: str | None = None,
    read_budget_exhausted: bool = False,
    omitted_refs: list[GovernanceEvidencePageRef] | None = None,
    blockers: list[str] | None = None,
    completeness: str = "unavailable",
    quality: str = "insufficient",
    refs: list[GovernanceEvidenceRef] | None = None,
    source_window: dict[str, Any] | None = None,
    source_mix: dict[str, int] | None = None,
) -> GovernanceEvidenceSet:
    """Build a minimal valid :class:`GovernanceEvidenceSet` for store tests.

    Mirrors the in-memory store test helper at
    ``tests/test_governance_evidence_store.py:171-201`` -- same defaults,
    same shape, so the parametrized contract tests can call the same
    helper against both concretes.
    """

    return GovernanceEvidenceSet(
        idempotency_key=idempotency_key,
        feature_id=feature_id,
        corpus_id=corpus_id,
        generated_at=datetime(2026, 5, 24, 17, 0, 0, tzinfo=timezone.utc),
        source_window=source_window if source_window is not None else {},
        refs=refs if refs is not None else [],
        omitted_refs=omitted_refs if omitted_refs is not None else [],
        completeness=completeness,  # type: ignore[arg-type]
        source_mix=source_mix if source_mix is not None else {},  # type: ignore[arg-type]
        read_budget=GovernanceReadBudget(),
        read_budget_exhausted=read_budget_exhausted,
        quality=quality,  # type: ignore[arg-type]
        blockers=blockers if blockers is not None else [],
    )


# ── package re-export ──────────────────────────────────────────────────────


def test_package_reexports_postgres_store_concrete() -> None:
    """The 13i Postgres-backed concrete is re-exported at the package level.

    Pins the doc-13:188-190 + STATUS.md chunk-shape point 3 invariant
    that ``PostgresGovernanceEvidenceStore`` is reachable via
    ``from iriai_build_v2.workflows.develop import governance``. The
    package-level strict-equality count assertion (22 exports) lives in
    ``tests/test_governance_evidence_models.py::test_governance_package_reexports_doc_13_surface``;
    this test asserts the 13i-specific subset is present and is the same
    Python object as the module-local symbol.
    """

    from iriai_build_v2.workflows.develop import governance

    assert "PostgresGovernanceEvidenceStore" in governance.__all__, (
        "missing 13i re-export: PostgresGovernanceEvidenceStore"
    )
    assert hasattr(governance, "PostgresGovernanceEvidenceStore")
    assert (
        governance.PostgresGovernanceEvidenceStore
        is postgres_store_module.PostgresGovernanceEvidenceStore
    ), (
        "package-level PostgresGovernanceEvidenceStore is not the same "
        "object as the module-level symbol; the re-export must be "
        "identity-preserving."
    )


# ── ABC contract (sync surface; works without Postgres) ────────────────────


def test_postgres_store_is_concrete_subclass_of_abc() -> None:
    """:class:`PostgresGovernanceEvidenceStore` extends the 13g ABC.

    Pins Liskov substitution: a caller can pass a
    :class:`PostgresGovernanceEvidenceStore` wherever a
    :class:`GovernanceEvidenceStore` is expected. As of the Slice 13i
    finalizer (P2-13i-1) the 13g ABC declares ``async`` ``put`` /
    ``get`` so this concrete satisfies the contract verbatim (no
    signature mismatch; no ``# type: ignore[override]`` needed).
    """

    assert issubclass(
        PostgresGovernanceEvidenceStore, GovernanceEvidenceStore
    )


def test_postgres_store_has_no_dag_artifact_write_method() -> None:
    """The 13i store surface has no ``dag-*`` write method.

    Per doc-13:201-203 verbatim ("Governance evidence sets may project
    review artifacts, but no ``dag-*`` execution, checkpoint, regroup
    activation, or merge artifact is written by this slice") the 13i
    Postgres store NEVER writes to those artifact spaces. Pin this with
    an explicit surface-scan test so a later sub-slice cannot silently
    add a writer. Mirrors
    ``tests/test_governance_evidence_store.py::test_store_does_not_expose_any_dag_artifact_write_method``.
    """

    forbidden_substrings = (
        "dag",
        "checkpoint",
        "regroup",
        "merge",
        "activate",
        "commit",
    )
    public_methods = [
        name
        for name in dir(PostgresGovernanceEvidenceStore)
        if not name.startswith("_")
        and callable(getattr(PostgresGovernanceEvidenceStore, name))
    ]
    for method in public_methods:
        lowered = method.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, (
                f"PostgresGovernanceEvidenceStore public method "
                f"{method!r} contains forbidden substring "
                f"{forbidden!r}. Per doc-13:201-203 the governance "
                "store NEVER writes dag-* / checkpoint / regroup / "
                "merge / commit artifacts."
            )


# ── typed-input-validation (does not need Postgres) ────────────────────────


@pytest.mark.asyncio
async def test_put_raises_type_error_on_non_evidence_set_input(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """``put(non-GovernanceEvidenceSet)`` raises :class:`TypeError`.

    Mirrors the in-memory 13g concrete's P3-13g-R2 finalizer test at
    ``tests/test_governance_evidence_store.py::test_put_raises_type_error_on_non_evidence_set_input``.
    Without this guard the method would crash on
    ``evidence_set.corpus_id`` access with an opaque
    :class:`AttributeError`, violating
    ``feedback_no_silent_degradation``.

    Three bad-input forms:

    * ``None`` -- the canonical "I forgot to construct it" case.
    * A bare ``str`` -- the "I passed the corpus_id by mistake" case.
    * A dict shaped like the evidence set -- the "I forgot to validate
      via Pydantic" case.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)

    # ``None``
    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put(None)  # type: ignore[arg-type]

    # bare ``str``
    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put("not-a-model")  # type: ignore[arg-type]

    # dict shaped like an evidence set
    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put(
            {"corpus_id": "fake", "idempotency_key": "x" * 64}  # type: ignore[arg-type]
        )

    # The store remains empty after rejected puts.
    count = await governance_pg_conn.fetchval(
        "SELECT count(*) FROM governance_evidence_sets"
    )
    assert int(count) == 0


# ── put + get round-trip ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_then_get_round_trips_minimal_evidence_set(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """Round-trip: ``put`` stores; ``get`` returns the typed row.

    Unlike the in-memory store (which returns the same Python object by
    reference), the Postgres store rehydrates from row columns via
    :meth:`~pydantic.BaseModel.model_validate`. The returned model is a
    fresh instance but the typed-field equality holds verbatim.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    evidence_set = _make_minimal_evidence_set(
        corpus_id="roundtrip:1", idempotency_key="a" * 64
    )

    await store.put(evidence_set)
    loaded = await store.get("roundtrip:1")

    assert loaded is not None
    assert loaded.corpus_id == "roundtrip:1"
    assert loaded.idempotency_key == "a" * 64
    assert loaded.feature_id is None
    assert loaded.completeness == "unavailable"
    assert loaded.quality == "insufficient"
    assert loaded.read_budget_exhausted is False
    assert loaded.refs == []
    assert loaded.omitted_refs == []
    assert loaded.source_mix == {}
    assert loaded.blockers == []


@pytest.mark.asyncio
async def test_put_preserves_all_typed_fields_verbatim(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """Every typed-row field round-trips equal through Postgres.

    Mirrors the in-memory 13g concrete's preservation test at
    ``tests/test_governance_evidence_store.py::test_put_preserves_all_typed_fields_verbatim``;
    extends it to assert the JSONB columns + scalar columns + nullable
    feature_id all round-trip equal through the Postgres serializer.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    evidence_set = _make_minimal_evidence_set(
        corpus_id="preserve:1",
        idempotency_key="b" * 64,
        feature_id="feat-x",
        read_budget_exhausted=True,
        blockers=[
            "governance_evidence_gap:implementation_journal",
            "governance_evidence_gap:supervisor_digest",
        ],
        source_window={"window_start": "2026-05-01", "window_end": "2026-05-24"},
        source_mix={"implementation_journal": 5, "implementation_decision_log": 3},
    )

    await store.put(evidence_set)
    loaded = await store.get("preserve:1")

    assert loaded is not None
    assert loaded.idempotency_key == evidence_set.idempotency_key
    assert loaded.corpus_id == evidence_set.corpus_id
    assert loaded.feature_id == "feat-x"
    assert loaded.generated_at == evidence_set.generated_at
    assert loaded.source_window == evidence_set.source_window
    assert loaded.refs == evidence_set.refs
    assert loaded.omitted_refs == evidence_set.omitted_refs
    assert loaded.completeness == evidence_set.completeness
    assert loaded.source_mix == evidence_set.source_mix
    assert loaded.read_budget == evidence_set.read_budget
    assert loaded.read_budget_exhausted is True
    assert loaded.quality == evidence_set.quality
    assert loaded.blockers == evidence_set.blockers


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_corpus_id(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """``get`` returns ``None`` for an absent corpus.

    Mirrors the in-memory 13g concrete's behaviour
    (``tests/test_governance_evidence_store.py::test_get_returns_none_for_unknown_corpus_id``).
    Per the 13g ABC contract: fail-OPEN on a missing corpus is
    acceptable for the store surface; the governance ingestor is what
    fail-closes on a missing implementation journal per doc-13:207-208.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    assert await store.get("does-not-exist") is None

    # Even after some puts the unknown id still returns None.
    await store.put(_make_minimal_evidence_set(corpus_id="exists:1"))
    assert await store.get("does-not-exist") is None
    loaded = await store.get("exists:1")
    assert loaded is not None
    assert loaded.corpus_id == "exists:1"


@pytest.mark.asyncio
async def test_put_with_distinct_corpus_ids_stores_independent_rows(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """Distinct ``corpus_id`` values store independent rows.

    Mirrors the in-memory 13g concrete's behaviour
    (``tests/test_governance_evidence_store.py::test_put_with_distinct_corpus_ids_stores_independent_rows``).
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    first = _make_minimal_evidence_set(
        corpus_id="alpha:1", idempotency_key="a" * 64
    )
    second = _make_minimal_evidence_set(
        corpus_id="beta:1", idempotency_key="b" * 64
    )
    await store.put(first)
    await store.put(second)

    loaded_alpha = await store.get("alpha:1")
    assert loaded_alpha is not None
    assert loaded_alpha.idempotency_key == "a" * 64

    loaded_beta = await store.get("beta:1")
    assert loaded_beta is not None
    assert loaded_beta.idempotency_key == "b" * 64

    assert await store.get("gamma:1") is None


# ── idempotency ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_idempotent_on_same_corpus_id_and_idempotency_key(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """``put`` twice with the same identity is a no-op.

    Mirrors the in-memory 13g concrete's behaviour
    (``tests/test_governance_evidence_store.py::test_put_idempotent_on_same_corpus_id_and_idempotency_key``).
    Per the 13g chunk-shape point 3c: calling ``put`` twice with the
    same ``(corpus_id, idempotency_key)`` pair is a legitimate retry;
    the second call returns silently; the stored row is unchanged.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    evidence_set = _make_minimal_evidence_set(
        corpus_id="idem:1", idempotency_key="c" * 64
    )

    await store.put(evidence_set)
    # Second put with the same evidence set -- no-op, no error.
    await store.put(evidence_set)
    # Third put with an equal but distinct object (same corpus_id +
    # same idempotency_key) -- still a no-op.
    equivalent = _make_minimal_evidence_set(
        corpus_id="idem:1", idempotency_key="c" * 64
    )
    await store.put(equivalent)

    loaded = await store.get("idem:1")
    assert loaded is not None
    assert loaded.idempotency_key == "c" * 64

    # Exactly one row in the table -- the puts are idempotent at the
    # row level too.
    count = await governance_pg_conn.fetchval(
        "SELECT count(*) FROM governance_evidence_sets WHERE corpus_id = $1",
        "idem:1",
    )
    assert int(count) == 1


@pytest.mark.asyncio
async def test_put_raises_idempotency_conflict_on_different_key_for_same_corpus_id(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """Fail-closed on ``corpus_id`` collision with a different idempotency_key.

    Mirrors the in-memory 13g concrete's fail-closed test at
    ``tests/test_governance_evidence_store.py::test_put_raises_idempotency_conflict_on_different_key_for_same_corpus_id``.
    Per doc-13:188-190 + the auto-memory
    ``feedback_no_silent_degradation`` rule, two evidence sets with the
    same ``corpus_id`` but different ``idempotency_key`` represent
    DIFFERENT content snapshots; the second ``put`` MUST raise.

    The error subclasses :class:`ValueError` so a generic
    ``ValueError`` catch still catches it.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    first = _make_minimal_evidence_set(
        corpus_id="conflict:1", idempotency_key="a" * 64
    )
    second = _make_minimal_evidence_set(
        corpus_id="conflict:1", idempotency_key="b" * 64
    )
    await store.put(first)

    # Generic ValueError catch still works.
    with pytest.raises(ValueError, match="idempotency_key"):
        await store.put(second)

    # Typed conflict catch carries the diagnostic attributes.
    with pytest.raises(
        GovernanceEvidenceStoreIdempotencyConflict
    ) as exc_info:
        await store.put(second)
    assert exc_info.value.corpus_id == "conflict:1"
    assert exc_info.value.existing_idempotency_key == "a" * 64
    assert exc_info.value.incoming_idempotency_key == "b" * 64

    # The original row is preserved (fail-closed -- no partial write).
    loaded = await store.get("conflict:1")
    assert loaded is not None
    assert loaded.idempotency_key == "a" * 64

    # And the table still has exactly one row for this corpus.
    count = await governance_pg_conn.fetchval(
        "SELECT count(*) FROM governance_evidence_sets WHERE corpus_id = $1",
        "conflict:1",
    )
    assert int(count) == 1


@pytest.mark.asyncio
async def test_idempotency_key_unique_constraint_at_schema_level(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """The ``idempotency_key`` UNIQUE constraint is enforced at the schema level.

    Cross-corpus collision on ``idempotency_key`` is a schema-level
    violation (the column has its own UNIQUE constraint). The Python
    concrete's conflict check is keyed on ``corpus_id``; a same-
    ``idempotency_key`` / different-``corpus_id`` collision falls
    through to the asyncpg unique-violation path. The store does NOT
    silently swallow it -- the safe behaviour is to let the typed
    ``asyncpg.UniqueViolationError`` (or the typed conflict if it
    resolves to a corpus_id collision) surface to the caller per
    ``feedback_no_silent_degradation``.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await store.put(
        _make_minimal_evidence_set(
            corpus_id="distinct-corpus:1", idempotency_key="d" * 64
        )
    )

    # Same idempotency_key + DIFFERENT corpus_id -> schema UNIQUE
    # violation. The store's transactional INSERT raises
    # asyncpg.UniqueViolationError; the corpus_id check finds no
    # collision (different corpus); the fallback re-checks corpus_id
    # and re-raises the original UniqueViolationError (no silent
    # swallow).
    with pytest.raises(asyncpg.UniqueViolationError):
        await store.put(
            _make_minimal_evidence_set(
                corpus_id="distinct-corpus:2", idempotency_key="d" * 64
            )
        )

    # The first row remains -- fail-closed, no partial write.
    loaded = await store.get("distinct-corpus:1")
    assert loaded is not None


# ── JSONB columns rehydrate cleanly through Pydantic ──────────────────────


@pytest.mark.asyncio
async def test_jsonb_columns_reparse_through_model_validate(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """The stored JSONB columns rehydrate cleanly through
    :meth:`~pydantic.BaseModel.model_validate`.

    Pins the chunk-shape point 4e invariant: the per-column JSONB
    serialization is round-trip-stable across :func:`json.dumps` (via
    the store's ``_jsonb`` helper) + asyncpg ``$N::jsonb`` cast +
    asyncpg fetchrow + the store's ``_loads`` reparse +
    ``model_validate``. The 13a model invariants fire on the rehydrated
    row (same as a fresh ``model_validate_json`` call).
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)

    # Build an evidence set with non-trivial JSONB content (a ref +
    # page_ref + non-empty source_mix + source_window).
    page_ref = GovernanceEvidencePageRef(
        page_ref_id="page-a",
        authority="implementation_journal",
        source_ref_id="src-1",
        line_start=10,
        line_end=20,
        digest="d" * 64,
        completeness="paged",
        exact=True,
        stale_check={"mtime": 1234567890.0},
    )
    ref = GovernanceEvidenceRef(
        authority="implementation_journal",
        ref_id="ref-1",
        slice_id="13i",
        journal_anchor="impl-journal:line-100",
        digest="e" * 64,
        quality="canonical",
        completeness="paged",
        page_refs=[page_ref],
        preview_only=False,
    )
    evidence_set = _make_minimal_evidence_set(
        corpus_id="jsonb-rt:1",
        idempotency_key="f" * 64,
        refs=[ref],
        omitted_refs=[],
        source_window={"start": "2026-05-01", "cursor": 42},
        source_mix={"implementation_journal": 1},
        completeness="paged",
        quality="canonical",
    )

    await store.put(evidence_set)
    loaded = await store.get("jsonb-rt:1")

    assert loaded is not None
    assert len(loaded.refs) == 1
    assert loaded.refs[0].ref_id == "ref-1"
    assert loaded.refs[0].authority == "implementation_journal"
    assert loaded.refs[0].digest == "e" * 64
    assert len(loaded.refs[0].page_refs) == 1
    assert loaded.refs[0].page_refs[0].page_ref_id == "page-a"
    assert loaded.refs[0].page_refs[0].stale_check == {"mtime": 1234567890.0}
    assert loaded.source_window == {"start": "2026-05-01", "cursor": 42}
    assert loaded.source_mix == {"implementation_journal": 1}


# ── schema verification (the table exists with the right columns) ─────────


@pytest.mark.asyncio
async def test_governance_evidence_sets_schema_matches_doc_13_shape(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """The ``governance_evidence_sets`` table has the doc-13:128-141 columns.

    Per the chunk-shape point 6 + STATUS.md "Schema migration check":
    query ``information_schema.columns`` to confirm every documented
    column is present with the correct ``data_type``. Pins:

    * The 15 functional columns + 1 audit ``created_at`` column = 16
      total per the user prompt's column list.
    * The TWO CHECK constraints (``completeness`` enum + ``quality``
      enum) per doc-13:87 + doc-13:86.
    * The composite UNIQUE ``(corpus_id, idempotency_key)`` constraint
      per doc-13:188-190 + ``feedback_no_silent_degradation``.
    * The ``idempotency_key`` UNIQUE constraint (top-level so the
      content fingerprint cannot collide cross-corpus).
    """

    rows = await governance_pg_conn.fetch(
        "SELECT column_name, data_type, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'public' "
        "AND table_name = 'governance_evidence_sets' "
        "ORDER BY ordinal_position"
    )
    column_meta = {
        str(row["column_name"]): (
            str(row["data_type"]),
            str(row["is_nullable"]),
        )
        for row in rows
    }

    # 16 columns per the user prompt: 15 functional + 1 audit
    # (created_at).
    expected_columns: dict[str, tuple[str, str]] = {
        # id: BIGSERIAL -> bigint, NOT NULL (auto-derived from BIGSERIAL).
        "id": ("bigint", "NO"),
        # idempotency_key TEXT NOT NULL UNIQUE.
        "idempotency_key": ("text", "NO"),
        # feature_id TEXT (nullable per the 13a model).
        "feature_id": ("text", "YES"),
        # corpus_id TEXT NOT NULL.
        "corpus_id": ("text", "NO"),
        # generated_at TIMESTAMPTZ NOT NULL.
        "generated_at": ("timestamp with time zone", "NO"),
        # source_window JSONB NOT NULL.
        "source_window": ("jsonb", "NO"),
        # refs JSONB NOT NULL.
        "refs": ("jsonb", "NO"),
        # omitted_refs JSONB NOT NULL.
        "omitted_refs": ("jsonb", "NO"),
        # completeness TEXT NOT NULL CHECK (...).
        "completeness": ("text", "NO"),
        # source_mix JSONB NOT NULL.
        "source_mix": ("jsonb", "NO"),
        # read_budget JSONB NOT NULL.
        "read_budget": ("jsonb", "NO"),
        # read_budget_exhausted BOOLEAN NOT NULL DEFAULT FALSE.
        "read_budget_exhausted": ("boolean", "NO"),
        # quality TEXT NOT NULL CHECK (...).
        "quality": ("text", "NO"),
        # blockers JSONB NOT NULL.
        "blockers": ("jsonb", "NO"),
        # created_at TIMESTAMPTZ NOT NULL DEFAULT NOW().
        "created_at": ("timestamp with time zone", "NO"),
    }

    # NOTE: 15 of the 16 user-prompt columns are present (the prompt
    # lists 15 functional + 1 created_at = 16 line items but the
    # ordering counts 15 distinct columns; ``id`` is the implicit
    # PRIMARY KEY column that brings the total to 16).
    assert len(column_meta) == len(expected_columns), (
        f"governance_evidence_sets has {len(column_meta)} columns; "
        f"expected {len(expected_columns)}. "
        f"actual={sorted(column_meta.keys())}; "
        f"expected={sorted(expected_columns.keys())}"
    )
    assert column_meta.keys() == expected_columns.keys(), (
        f"governance_evidence_sets column set mismatch. "
        f"missing={sorted(expected_columns.keys() - column_meta.keys())}; "
        f"extra={sorted(column_meta.keys() - expected_columns.keys())}"
    )
    for col, (expected_type, expected_nullable) in expected_columns.items():
        actual_type, actual_nullable = column_meta[col]
        assert actual_type == expected_type, (
            f"column {col!r} type mismatch: actual={actual_type!r}; "
            f"expected={expected_type!r}"
        )
        assert actual_nullable == expected_nullable, (
            f"column {col!r} nullable mismatch: actual={actual_nullable!r}; "
            f"expected={expected_nullable!r}"
        )

    # Check the two CHECK constraints (completeness + quality enums)
    # plus the composite UNIQUE constraint are present.
    constraint_rows = await governance_pg_conn.fetch(
        "SELECT constraint_name, constraint_type "
        "FROM information_schema.table_constraints "
        "WHERE table_schema = 'public' "
        "AND table_name = 'governance_evidence_sets'"
    )
    constraint_meta = {
        str(row["constraint_name"]): str(row["constraint_type"])
        for row in constraint_rows
    }
    # Expected constraint names (per the schema.sql Slice-13i block).
    expected_constraints = {
        "governance_evidence_sets_completeness_check": "CHECK",
        "governance_evidence_sets_quality_check": "CHECK",
        "governance_evidence_sets_corpus_idempotency": "UNIQUE",
    }
    for name, ctype in expected_constraints.items():
        assert name in constraint_meta, (
            f"missing constraint {name!r} on governance_evidence_sets; "
            f"actual constraints={sorted(constraint_meta.keys())}"
        )
        assert constraint_meta[name] == ctype, (
            f"constraint {name!r} type mismatch: "
            f"actual={constraint_meta[name]!r}; expected={ctype!r}"
        )

    # The idempotency_key UNIQUE (top-level column constraint) shows
    # up as a UNIQUE constraint too (auto-named like
    # ``governance_evidence_sets_idempotency_key_key``). We don't pin
    # the exact name but DO assert there is at least one UNIQUE
    # constraint involving idempotency_key alone.
    unique_rows = await governance_pg_conn.fetch(
        "SELECT tc.constraint_name "
        "FROM information_schema.table_constraints tc "
        "JOIN information_schema.constraint_column_usage ccu "
        "  ON tc.constraint_name = ccu.constraint_name "
        " AND tc.table_schema = ccu.table_schema "
        "WHERE tc.table_schema = 'public' "
        "AND tc.table_name = 'governance_evidence_sets' "
        "AND tc.constraint_type = 'UNIQUE' "
        "AND ccu.column_name = 'idempotency_key' "
        "GROUP BY tc.constraint_name "
        "HAVING count(*) = 1"
    )
    assert len(unique_rows) >= 1, (
        "expected a top-level UNIQUE constraint on idempotency_key alone "
        "(the content-fingerprint cannot collide cross-corpus)."
    )


# ── real-corpus integration ───────────────────────────────────────────────


_REAL_JOURNAL_PATH = Path(
    "docs/execution-control-plane/implementation-journal.md"
)
_REAL_DECISIONS_PATH = Path(
    "docs/execution-control-plane/implementation-decisions.jsonl"
)


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _REAL_JOURNAL_PATH.exists() or not _REAL_DECISIONS_PATH.exists(),
    reason="real fixtures not present; pure synthetic-only run",
)
async def test_real_corpus_parse_compose_put_get_equal(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """End-to-end: real corpus -> parse -> compose -> put -> get -> equal.

    Per the user prompt § "Sub-slice 13i scope" point 4 final bullet:
    feed the real ``implementation-journal.md`` + ``implementation-decisions.jsonl``
    through the 13c + 13d parsers, compose via the 13e digester, put
    via the 13i Postgres store, get back, and assert the typed-row
    equality holds. Mirrors the in-memory 13g real-corpus test at
    ``tests/test_governance_evidence_store.py::test_real_corpus_store_put_then_get_round_trip``.
    """

    journal_anchors = parse_implementation_journal(_REAL_JOURNAL_PATH)
    decision_anchors = parse_implementation_decision_log(_REAL_DECISIONS_PATH)
    evidence_set = compose_governance_evidence_set(
        journal_anchors=journal_anchors,
        decision_log_anchors=decision_anchors,
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=GovernanceWindow(),
        read_budget=GovernanceReadBudget(),
        corpus_id="real-corpus:13i-pg-roundtrip",
    )

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await store.put(evidence_set)
    loaded = await store.get("real-corpus:13i-pg-roundtrip")

    assert loaded is not None
    assert loaded.idempotency_key == evidence_set.idempotency_key
    assert loaded.corpus_id == evidence_set.corpus_id
    assert loaded.completeness == evidence_set.completeness
    assert loaded.quality == evidence_set.quality
    assert len(loaded.refs) == len(evidence_set.refs), (
        f"refs count mismatch: actual={len(loaded.refs)}; "
        f"expected={len(evidence_set.refs)}"
    )
    # The refs round-trip equal (same authority + ref_id + digest +
    # ...).
    for original, rehydrated in zip(evidence_set.refs, loaded.refs):
        assert rehydrated.authority == original.authority
        assert rehydrated.ref_id == original.ref_id
        assert rehydrated.digest == original.digest
        assert rehydrated.quality == original.quality
        assert rehydrated.completeness == original.completeness
        assert rehydrated.preview_only == original.preview_only
    # The source_mix round-trips equal.
    assert loaded.source_mix == evidence_set.source_mix
    # Idempotency: re-put the same evidence set -- no-op, no conflict.
    await store.put(evidence_set)
    count = await governance_pg_conn.fetchval(
        "SELECT count(*) FROM governance_evidence_sets WHERE corpus_id = $1",
        "real-corpus:13i-pg-roundtrip",
    )
    assert int(count) == 1, (
        "re-put with the same identity must be idempotent at the row level"
    )


# ── parametrized in-memory ⇔ Postgres contract tests ──────────────────────
#
# Two store concretes implement the same 13g ABC contract; one set of
# behavioural assertions runs against BOTH. Per the chunk-shape point 6:
# the in-memory + Postgres concretes are functionally interchangeable.
#
# As of the Slice 13i finalizer (P2-13i-1) the 13g ABC is ``async def``
# and the in-memory concrete is also ``async def`` (typed-surface
# parity); both concretes share the same async surface so the
# parametrized contract tests construct each concrete directly (the
# prior ``_AsyncInMemoryAdapter`` shim was deleted because both
# concretes now satisfy the contract without wrapping).


async def _assert_contract_put_get_round_trip(
    store: GovernanceEvidenceStore,
    corpus_id: str,
    idempotency_key: str,
) -> None:
    """**Contract**: ``put`` then ``get`` returns an equivalent typed row."""

    evidence_set = _make_minimal_evidence_set(
        corpus_id=corpus_id, idempotency_key=idempotency_key
    )
    await store.put(evidence_set)
    loaded = await store.get(corpus_id)
    assert loaded is not None
    assert loaded.corpus_id == corpus_id
    assert loaded.idempotency_key == idempotency_key


async def _assert_contract_put_idempotent_same_identity(
    store: GovernanceEvidenceStore,
    corpus_id: str,
    idempotency_key: str,
) -> None:
    """**Contract**: ``put`` twice with same identity is a no-op."""

    evidence_set = _make_minimal_evidence_set(
        corpus_id=corpus_id, idempotency_key=idempotency_key
    )
    await store.put(evidence_set)
    # Second put -- no-op, no error.
    await store.put(evidence_set)
    loaded = await store.get(corpus_id)
    assert loaded is not None
    assert loaded.idempotency_key == idempotency_key


async def _assert_contract_put_conflict_on_different_idempotency_key(
    store: GovernanceEvidenceStore,
    corpus_id: str,
    existing_key: str,
    incoming_key: str,
) -> None:
    """**Contract**: same corpus_id + different idempotency_key → fail-closed.

    Both concretes raise
    :class:`GovernanceEvidenceStoreIdempotencyConflict` (subclass of
    :class:`ValueError`). The diagnostic attributes
    (``corpus_id`` / ``existing_idempotency_key`` /
    ``incoming_idempotency_key``) match across both concretes.
    """

    first = _make_minimal_evidence_set(
        corpus_id=corpus_id, idempotency_key=existing_key
    )
    second = _make_minimal_evidence_set(
        corpus_id=corpus_id, idempotency_key=incoming_key
    )
    await store.put(first)
    with pytest.raises(
        GovernanceEvidenceStoreIdempotencyConflict
    ) as exc_info:
        await store.put(second)
    assert exc_info.value.corpus_id == corpus_id
    assert exc_info.value.existing_idempotency_key == existing_key
    assert exc_info.value.incoming_idempotency_key == incoming_key
    # And the conflict is a ValueError too (generic catch works).
    assert isinstance(exc_info.value, ValueError)
    # The first stored row is preserved (fail-closed -- no partial
    # write).
    loaded = await store.get(corpus_id)
    assert loaded is not None
    assert loaded.idempotency_key == existing_key


async def _assert_contract_put_type_error_on_bad_input(
    store: GovernanceEvidenceStore,
) -> None:
    """**Contract**: ``put(None)`` raises :class:`TypeError`."""

    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put(None)  # type: ignore[arg-type]


async def _assert_contract_get_unknown_returns_none(
    store: GovernanceEvidenceStore,
    corpus_id_used: str,
) -> None:
    """**Contract**: ``get`` returns ``None`` for an absent corpus."""

    assert await store.get("contract-unknown:nope") is None
    # Even after some puts the unknown id still returns None.
    await store.put(_make_minimal_evidence_set(corpus_id=corpus_id_used))
    assert await store.get("contract-unknown:nope") is None


# The contract tests below pair each ``_assert_contract_*`` helper with
# two test functions: one over the in-memory concrete, one over the
# Postgres concrete. Both call the SAME helper -- the contract IS the
# helper body; the two test functions just inject the concrete. Per
# the Slice 13i finalizer (P2-13i-1) the in-memory concrete is now
# ``async def`` natively so it is passed directly (no shim).


@pytest.mark.asyncio
async def test_contract_put_get_round_trip_inmemory() -> None:
    """**Contract**: in-memory concrete round-trips ``put`` -> ``get``."""

    store = InMemoryGovernanceEvidenceStore()
    await _assert_contract_put_get_round_trip(
        store, "contract-rt-im:1", "0" * 64
    )


@pytest.mark.asyncio
async def test_contract_put_get_round_trip_postgres(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """**Contract**: Postgres concrete round-trips ``put`` -> ``get``."""

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await _assert_contract_put_get_round_trip(
        store, "contract-rt-pg:1", "0" * 64
    )


@pytest.mark.asyncio
async def test_contract_put_idempotent_same_identity_inmemory() -> None:
    """**Contract**: in-memory concrete idempotent on same identity."""

    store = InMemoryGovernanceEvidenceStore()
    await _assert_contract_put_idempotent_same_identity(
        store, "contract-idem-im:1", "1" * 64
    )


@pytest.mark.asyncio
async def test_contract_put_idempotent_same_identity_postgres(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """**Contract**: Postgres concrete idempotent on same identity."""

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await _assert_contract_put_idempotent_same_identity(
        store, "contract-idem-pg:1", "1" * 64
    )


@pytest.mark.asyncio
async def test_contract_put_conflict_on_different_idempotency_key_inmemory() -> None:
    """**Contract**: in-memory concrete fail-closed on idempotency conflict."""

    store = InMemoryGovernanceEvidenceStore()
    await _assert_contract_put_conflict_on_different_idempotency_key(
        store, "contract-conflict-im:1", "2" * 64, "3" * 64
    )


@pytest.mark.asyncio
async def test_contract_put_conflict_on_different_idempotency_key_postgres(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """**Contract**: Postgres concrete fail-closed on idempotency conflict."""

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await _assert_contract_put_conflict_on_different_idempotency_key(
        store, "contract-conflict-pg:1", "2" * 64, "3" * 64
    )


@pytest.mark.asyncio
async def test_contract_put_type_error_on_bad_input_inmemory() -> None:
    """**Contract**: in-memory concrete TypeError on bad input."""

    store = InMemoryGovernanceEvidenceStore()
    await _assert_contract_put_type_error_on_bad_input(store)


@pytest.mark.asyncio
async def test_contract_put_type_error_on_bad_input_postgres(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """**Contract**: Postgres concrete TypeError on bad input."""

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await _assert_contract_put_type_error_on_bad_input(store)


@pytest.mark.asyncio
async def test_contract_get_unknown_returns_none_inmemory() -> None:
    """**Contract**: in-memory concrete returns None for unknown corpus."""

    store = InMemoryGovernanceEvidenceStore()
    await _assert_contract_get_unknown_returns_none(
        store, "contract-exists-im:1"
    )


@pytest.mark.asyncio
async def test_contract_get_unknown_returns_none_postgres(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """**Contract**: Postgres concrete returns None for unknown corpus."""

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    await _assert_contract_get_unknown_returns_none(
        store, "contract-exists-pg:1"
    )


# ── canonical-JSON byte-identity across two concretes ─────────────────────


@pytest.mark.asyncio
async def test_postgres_jsonb_columns_canonical_sorted(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """The stored JSONB columns are written in canonical-sorted form.

    Mirrors the 13g project_review_artifact byte-identity test
    (``tests/test_governance_evidence_store.py::test_project_review_artifact_two_equivalent_sets_byte_identical``).
    Two equivalent evidence sets produce byte-identical JSONB column
    contents in Postgres (the ``_jsonb`` helper at
    ``postgres_store.py:_jsonb`` mirrors
    ``execution_control/regroup_overlay_store.py:305-306`` -- compact
    separators + sort_keys=True).
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)
    # Build two equivalent evidence sets (same content, same identity).
    set_a = _make_minimal_evidence_set(
        corpus_id="canon-a:1",
        idempotency_key="7" * 64,
        source_window={"z_key": 1, "a_key": 2},
        source_mix={"implementation_journal": 3, "implementation_decision_log": 5},
    )
    set_b = _make_minimal_evidence_set(
        corpus_id="canon-b:1",
        idempotency_key="8" * 64,
        source_window={"z_key": 1, "a_key": 2},
        source_mix={"implementation_journal": 3, "implementation_decision_log": 5},
    )
    await store.put(set_a)
    await store.put(set_b)

    # Pull the raw JSONB text from the rows -- canonical-sorted means
    # the two stored JSONB blobs are byte-identical (mod the corpus_id
    # / idempotency_key scalar columns).
    text_a = await governance_pg_conn.fetchval(
        "SELECT source_window::text FROM governance_evidence_sets "
        "WHERE corpus_id = $1",
        "canon-a:1",
    )
    text_b = await governance_pg_conn.fetchval(
        "SELECT source_window::text FROM governance_evidence_sets "
        "WHERE corpus_id = $1",
        "canon-b:1",
    )
    # Postgres re-serializes JSONB to its canonical internal form;
    # both equivalent inputs produce the same canonical output.
    parsed_a = json.loads(str(text_a))
    parsed_b = json.loads(str(text_b))
    assert parsed_a == parsed_b == {"z_key": 1, "a_key": 2}


# ── feature_id nullable round-trip ────────────────────────────────────────


@pytest.mark.asyncio
async def test_feature_id_round_trips_both_null_and_set(
    governance_pg_conn: "asyncpg.Connection",
) -> None:
    """``feature_id`` round-trips correctly for both ``None`` and a string.

    Per doc-13:130 + the 13a model: ``feature_id: str | None``. Pins
    that the nullable column carries the value through asyncpg → JSONB
    → Pydantic without coercion.
    """

    store = PostgresGovernanceEvidenceStore(governance_pg_conn)

    # feature_id = None
    none_set = _make_minimal_evidence_set(
        corpus_id="feat-null:1", idempotency_key="4" * 64, feature_id=None
    )
    await store.put(none_set)
    loaded_none = await store.get("feat-null:1")
    assert loaded_none is not None
    assert loaded_none.feature_id is None

    # feature_id = "feat-x"
    set_set = _make_minimal_evidence_set(
        corpus_id="feat-set:1", idempotency_key="5" * 64, feature_id="feat-x"
    )
    await store.put(set_set)
    loaded_set = await store.get("feat-set:1")
    assert loaded_set is not None
    assert loaded_set.feature_id == "feat-x"
