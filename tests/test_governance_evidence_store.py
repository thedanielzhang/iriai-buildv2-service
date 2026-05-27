"""Slice 13g -- unit tests for the typed-row store + review-artifact projection.

Covers the doc-13:188-190 § "Refactoring Steps" step 6 deliverable
("Store governance evidence sets as typed rows once the Slice 01 store
exists, and project bounded review artifacts such as
``review:governance-evidence:{corpus_id}``") for the
:mod:`iriai_build_v2.workflows.develop.governance.store` module:

* :class:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceStore`
  -- the typed-row store ABC (``async`` ``put`` + ``get`` abstract methods).
* :class:`~iriai_build_v2.workflows.develop.governance.InMemoryGovernanceEvidenceStore`
  -- the in-memory concrete implementation (also ``async``; bodies are
  pure-Python, no IO).
* :func:`~iriai_build_v2.workflows.develop.governance.project_review_artifact`
  -- the bounded review-artifact projection helper (sync; pure-functional).

The test surface pins the chunk-shape contract from STATUS.md § "Next safe
action" point 3:

* ABC method signatures via :func:`inspect.signature` against the
  doc-13:188-190 contract + :func:`inspect.iscoroutinefunction` to pin
  the async-ABC contract landed by the Slice 13i finalizer (P2-13i-1
  Liskov-substitution remediation).
* ``put`` + ``get`` round-trip returns the same typed evidence set.
* ``put`` idempotent on :attr:`GovernanceEvidenceSet.idempotency_key` --
  same key is a no-op; different key on the same ``corpus_id`` raises
  :class:`GovernanceEvidenceStoreIdempotencyConflict` (fail-closed).
* ``get`` returns ``None`` for unknown ``corpus_id``.
* :func:`project_review_artifact` returns the doc-13:189-190 verbatim
  ``review:governance-evidence:{corpus_id}`` key + canonical-sorted JSON
  body; two equivalent sets project to BYTE-IDENTICAL bytes.
* Real-corpus round-trip: live ``implementation-journal.md`` + JSONL
  decisions are parsed -> composed -> projected -> serialised -> re-parsed
  -> re-validated through the 13a model invariants.
* Bounded-read invariant (doc-13:215-220): when the source set is
  ``read_budget_exhausted=True``, the projection preserves the flag verbatim.

Per the governance prompt § "Slice 13A invariant for downstream slices" no
test in this file consumes the store as **execution authority** -- the store
is READ-only authority until Slice 13A lands. Per the prompt § "Bounded
reads" the projection helper is bounded by the typed-surface size; it never
hydrates artifact bodies (doc-13:186 verbatim).

**Async surface (Slice 13i finalizer).** The 13g ABC's ``put`` / ``get``
were promoted to ``async def`` by the Slice 13i finalizer (P2-13i-1) so
the Postgres-backed concrete (which MUST be async because
:mod:`asyncpg` cannot be invoked synchronously) and the in-memory
concrete share an identical typed-surface signature. Every test that
calls ``store.put(...)`` / ``store.get(...)`` is decorated
``@pytest.mark.asyncio`` and uses ``await``.
"""

from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop import governance
from iriai_build_v2.workflows.develop.governance import (
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceEvidenceStore,
    GovernanceReadBudget,
    GovernanceWindow,
    InMemoryGovernanceEvidenceStore,
    compose_governance_evidence_set,
    parse_implementation_decision_log,
    parse_implementation_journal,
    project_review_artifact,
)
from iriai_build_v2.workflows.develop.governance import store as store_module


# ── package surface ────────────────────────────────────────────────────────


def test_package_reexports_store_surface() -> None:
    """The 3 13g store surfaces are re-exported at the package level.

    The package-level strict-equality assertion lives in
    ``tests/test_governance_evidence_models.py::
    test_governance_package_reexports_doc_13_surface``; this test
    asserts the 13g-specific subset is present and is the same Python
    object as the module-local symbol.
    """

    for name in (
        "GovernanceEvidenceStore",
        "InMemoryGovernanceEvidenceStore",
        "project_review_artifact",
    ):
        assert name in governance.__all__, f"missing 13g re-export: {name}"
        assert hasattr(governance, name), f"package missing attr: {name}"
        assert getattr(governance, name) is getattr(store_module, name), (
            f"package-level {name} is not the same object as the module-level "
            "symbol; the re-export must be identity-preserving."
        )


# ── ABC pin ────────────────────────────────────────────────────────────────


def test_governance_evidence_store_is_an_abc() -> None:
    """``GovernanceEvidenceStore`` is an ``abc.ABC`` and cannot be
    instantiated directly.

    Pins the doc-13:188-190 contract that the typed-row store is an
    abstract surface; the in-memory implementation is one concrete realisation
    (the Postgres-backed implementation is a later sub-slice).
    """

    import abc

    assert isinstance(GovernanceEvidenceStore, abc.ABCMeta), (
        "GovernanceEvidenceStore must be an abc.ABCMeta (i.e. extends abc.ABC) "
        "so concrete implementations are forced to implement put + get; "
        "the bare ABC must be uninstantiable."
    )
    with pytest.raises(TypeError, match="abstract"):
        GovernanceEvidenceStore()  # type: ignore[abstract]


def test_governance_evidence_store_abc_has_put_and_get_abstract() -> None:
    """``GovernanceEvidenceStore`` declares ``put`` and ``get`` abstract.

    Mirrors the 13b ``test_abc_has_doc_13_three_abstract_methods`` discipline
    in ``tests/test_governance_evidence_ingestor.py``: pin the exact abstract
    method set so a later sub-slice cannot silently drop the doc-13:188-190
    contract.
    """

    expected_abstract = {"put", "get"}
    actual_abstract = set(GovernanceEvidenceStore.__abstractmethods__)
    assert actual_abstract == expected_abstract, (
        f"GovernanceEvidenceStore.__abstractmethods__ must be exactly "
        f"{expected_abstract}; actual={actual_abstract}. The doc-13:188-190 "
        "contract names two abstract methods (put + get); deviation breaks "
        "the typed-row store contract."
    )


def test_abc_method_signatures_match_doc_13_contract() -> None:
    """``put`` and ``get`` carry the doc-13:188-190 verbatim signatures.

    Uses :func:`inspect.signature` so a parameter name / annotation drift in a
    later sub-slice fails this test loudly (the 13b carry P3-13b-3 noted that
    the ingestor ABC test did not verify signatures; the 13g ABC closes that
    coverage gap up front).

    Also pins the async-ABC contract via
    :func:`inspect.iscoroutinefunction` — the Slice 13i finalizer
    promoted the ABC's ``put`` / ``get`` to ``async def`` (P2-13i-1)
    so the Postgres concrete satisfies Liskov substitution verbatim
    (the prior sync-ABC + async-Postgres-override combination silently
    dropped side-effects when a polymorphic caller never awaited the
    returned coroutine). The pin guards against a later sub-slice
    silently reverting either method to ``def``.
    """

    put_sig = inspect.signature(GovernanceEvidenceStore.put)
    put_params = list(put_sig.parameters.keys())
    assert put_params == ["self", "evidence_set"], (
        f"put signature must be (self, evidence_set); actual={put_params}"
    )
    # The return annotation is None (no value returned -- the store mutates
    # in-place + raises on conflict). With ``from __future__ import
    # annotations`` the annotation is the string ``'None'`` (PEP 563
    # stringified annotations), not the Python ``None`` singleton; both
    # cases satisfy the "return None" contract.
    put_return = put_sig.return_annotation
    assert put_return is None or put_return == "None" or put_return is type(None), (
        f"put must return None; actual return_annotation="
        f"{put_return!r}"
    )

    get_sig = inspect.signature(GovernanceEvidenceStore.get)
    get_params = list(get_sig.parameters.keys())
    assert get_params == ["self", "corpus_id"], (
        f"get signature must be (self, corpus_id); actual={get_params}"
    )

    # Async-ABC pin per the Slice 13i finalizer (P2-13i-1). Both
    # abstract methods MUST be coroutine functions so the Postgres
    # concrete satisfies the contract verbatim (no Liskov-substitution
    # regression).
    assert inspect.iscoroutinefunction(GovernanceEvidenceStore.put), (
        "GovernanceEvidenceStore.put must be `async def` per the Slice "
        "13i finalizer P2-13i-1 remediation; without async the Postgres "
        "concrete's async override silently breaks Liskov substitution "
        "(a polymorphic caller would receive a coroutine instead of the "
        "typed TypeError / write side-effect)."
    )
    assert inspect.iscoroutinefunction(GovernanceEvidenceStore.get), (
        "GovernanceEvidenceStore.get must be `async def` per the Slice "
        "13i finalizer P2-13i-1 remediation; without async the Postgres "
        "concrete's async override silently breaks Liskov substitution."
    )


# ── helpers for synthetic evidence sets ────────────────────────────────────


def _make_minimal_evidence_set(
    *,
    corpus_id: str = "test-corpus:1",
    idempotency_key: str = "a" * 64,
    read_budget_exhausted: bool = False,
    omitted_refs: list[GovernanceEvidencePageRef] | None = None,
    blockers: list[str] | None = None,
) -> GovernanceEvidenceSet:
    """Build a minimal valid :class:`GovernanceEvidenceSet` for store tests.

    Default content is empty (zero refs / source_mix); completeness is
    ``unavailable`` per the 13a / 13e contract for empty sets; quality is
    ``insufficient``. The store does not care about content shape (it stores
    typed rows verbatim); these defaults keep the test inputs minimal.
    """

    return GovernanceEvidenceSet(
        idempotency_key=idempotency_key,
        feature_id=None,
        corpus_id=corpus_id,
        generated_at=datetime(2026, 5, 24, 17, 0, 0, tzinfo=timezone.utc),
        source_window={},
        refs=[],
        omitted_refs=omitted_refs if omitted_refs is not None else [],
        completeness="unavailable",
        source_mix={},
        read_budget=GovernanceReadBudget(),
        read_budget_exhausted=read_budget_exhausted,
        quality="insufficient",
        blockers=blockers if blockers is not None else [],
    )


# ── put + get round-trip ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_then_get_returns_equivalent_evidence_set() -> None:
    """Round-trip: ``put`` stores; ``get`` returns the identical typed row.

    The store holds typed objects by reference (no serialisation round-trip
    in the in-memory tier); ``get`` returns the SAME Python object that was
    stored. This is the simplest contract the doc-13:188-190 step 6
    typed-row store satisfies.

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
    evidence_set = _make_minimal_evidence_set(corpus_id="roundtrip:1")
    await store.put(evidence_set)
    loaded = await store.get("roundtrip:1")
    assert loaded is not None
    assert loaded is evidence_set, (
        "In-memory store returns the stored object by reference (no copy)."
    )
    assert loaded.corpus_id == "roundtrip:1"
    assert loaded.idempotency_key == "a" * 64


@pytest.mark.asyncio
async def test_put_preserves_all_typed_fields_verbatim() -> None:
    """The store does not mutate / re-derive any typed-row field.

    Pins that ``put`` + ``get`` is a pure-typed round-trip; the store is a
    plain dict-by-corpus_id, not a re-projection layer (re-projection at
    storage time would risk losing the digester's content fingerprint).

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
    evidence_set = _make_minimal_evidence_set(
        corpus_id="preserve:1",
        read_budget_exhausted=True,
        blockers=["governance_evidence_gap:implementation_journal"],
    )
    await store.put(evidence_set)
    loaded = await store.get("preserve:1")
    assert loaded is not None
    # Every typed-row field is preserved verbatim.
    assert loaded.idempotency_key == evidence_set.idempotency_key
    assert loaded.corpus_id == evidence_set.corpus_id
    assert loaded.generated_at == evidence_set.generated_at
    assert loaded.source_window == evidence_set.source_window
    assert loaded.refs == evidence_set.refs
    assert loaded.omitted_refs == evidence_set.omitted_refs
    assert loaded.completeness == evidence_set.completeness
    assert loaded.source_mix == evidence_set.source_mix
    assert loaded.read_budget == evidence_set.read_budget
    assert loaded.read_budget_exhausted == evidence_set.read_budget_exhausted
    assert loaded.quality == evidence_set.quality
    assert loaded.blockers == evidence_set.blockers


# ── idempotency ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_put_idempotent_on_same_corpus_id_and_idempotency_key() -> None:
    """``put`` twice with the same evidence set is a no-op.

    Per the 13g chunk-shape point 3c: calling ``put`` twice with the same
    ``(corpus_id, idempotency_key)`` pair is a legitimate retry. The second
    call returns silently; the stored row is unchanged.

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
    evidence_set = _make_minimal_evidence_set(corpus_id="idem:1")
    await store.put(evidence_set)
    # Second put with the same object -- no-op, no error.
    await store.put(evidence_set)
    # Third put with an equal but distinct object (same corpus_id +
    # same idempotency_key) -- still a no-op.
    equivalent = _make_minimal_evidence_set(corpus_id="idem:1")
    await store.put(equivalent)
    loaded = await store.get("idem:1")
    assert loaded is not None
    # The store retains the FIRST stored object (the equivalent put is
    # a no-op; the second put with the same identity does not overwrite).
    assert loaded is evidence_set


@pytest.mark.asyncio
async def test_put_raises_idempotency_conflict_on_different_key_for_same_corpus_id() -> None:
    """Fail-closed on ``corpus_id`` collision with a different idempotency_key.

    Per doc-13:188-190 + the auto-memory ``feedback_no_silent_degradation``
    rule, two evidence sets with the same ``corpus_id`` but different
    ``idempotency_key`` represent DIFFERENT content snapshots. The second
    ``put`` MUST raise; silently overwriting would lose evidence.

    The error subclasses :class:`ValueError` so a generic ``ValueError``
    catch still catches it (mirrors the
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.IdempotencyConflict`
    precedent at ``failure_router.py:555-568``).

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
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
        store_module.GovernanceEvidenceStoreIdempotencyConflict
    ) as exc_info:
        await store.put(second)
    assert exc_info.value.corpus_id == "conflict:1"
    assert exc_info.value.existing_idempotency_key == "a" * 64
    assert exc_info.value.incoming_idempotency_key == "b" * 64

    # The original row is preserved (fail-closed -- no partial write).
    loaded = await store.get("conflict:1")
    assert loaded is not None
    assert loaded.idempotency_key == "a" * 64


@pytest.mark.asyncio
async def test_get_returns_none_for_unknown_corpus_id() -> None:
    """``get`` returns ``None`` for an absent corpus.

    Per the 13g chunk-shape point 3d: fail-OPEN on a missing corpus is
    acceptable for the store surface (the governance ingestor is what
    fail-closes on a missing implementation journal per doc-13:207-208).

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
    assert await store.get("does-not-exist") is None
    # Even after some puts the unknown id still returns None.
    await store.put(_make_minimal_evidence_set(corpus_id="exists:1"))
    assert await store.get("does-not-exist") is None
    assert await store.get("exists:1") is not None


@pytest.mark.asyncio
async def test_put_with_distinct_corpus_ids_stores_independent_rows() -> None:
    """Distinct ``corpus_id`` values store independent rows.

    Pins the dict-by-corpus_id contract: two evidence sets with different
    corpus ids do NOT interact (no shared mutation, no idempotency
    collision).

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
    first = _make_minimal_evidence_set(
        corpus_id="alpha:1", idempotency_key="a" * 64
    )
    second = _make_minimal_evidence_set(
        corpus_id="beta:1", idempotency_key="b" * 64
    )
    await store.put(first)
    await store.put(second)
    assert await store.get("alpha:1") is first
    assert await store.get("beta:1") is second
    # Cross-corpus reads return None.
    assert await store.get("gamma:1") is None


# ── project_review_artifact ────────────────────────────────────────────────


def test_project_review_artifact_returns_doc_13_key_shape() -> None:
    """The artifact key matches the doc-13:189-190 verbatim shape.

    Doc-13:189-190 names the key as
    ``review:governance-evidence:{corpus_id}``; the projection helper
    interpolates ``evidence_set.corpus_id`` into the literal prefix.
    """

    evidence_set = _make_minimal_evidence_set(corpus_id="key-shape:42")
    artifact_key, artifact_value = project_review_artifact(evidence_set)
    assert artifact_key == "review:governance-evidence:key-shape:42"
    # The value is a non-empty canonical-JSON string (asserted in detail in
    # the next test).
    assert isinstance(artifact_value, str)
    assert artifact_value.startswith("{") and artifact_value.endswith("}")


def test_project_review_artifact_value_is_canonical_sorted_json() -> None:
    """The artifact value is canonical-sorted JSON (sort_keys + compact sep).

    Canonical-JSON means: lexicographic key ordering + compact separators
    (no spaces). Two equivalent evidence sets project to BYTE-IDENTICAL
    bytes; this is the property the chunk-shape point 3g pins.
    """

    evidence_set = _make_minimal_evidence_set(corpus_id="canon:1")
    _, artifact_value = project_review_artifact(evidence_set)
    # Re-parse the JSON -- canonical JSON is valid JSON.
    parsed = json.loads(artifact_value)
    assert parsed["corpus_id"] == "canon:1"
    assert parsed["idempotency_key"] == "a" * 64
    # Canonical form has no spaces after ":" / ",".
    assert ": " not in artifact_value
    assert ", " not in artifact_value
    # Top-level keys are sorted lexicographically.
    decoded_keys = [k for k in parsed.keys()]
    assert decoded_keys == sorted(decoded_keys), (
        "canonical JSON must have sorted top-level keys"
    )


def test_project_review_artifact_two_equivalent_sets_byte_identical() -> None:
    """Two equivalent evidence sets project to BYTE-IDENTICAL JSON bytes.

    The canonical-JSON discipline (sort_keys + compact separators) plus
    Pydantic's model_dump(mode="json") (which produces JSON-safe primitives
    deterministically) guarantees byte-identity. This is the chunk-shape
    point 3g pin.
    """

    set_a = _make_minimal_evidence_set(
        corpus_id="byte-identical:1", idempotency_key="f" * 64
    )
    set_b = _make_minimal_evidence_set(
        corpus_id="byte-identical:1", idempotency_key="f" * 64
    )
    _, value_a = project_review_artifact(set_a)
    _, value_b = project_review_artifact(set_b)
    assert value_a == value_b, (
        "Two equivalent evidence sets MUST project to byte-identical "
        "canonical-JSON values (the digester's content fingerprint is the "
        "set-level identity; canonical-JSON projection MUST preserve it)."
    )
    # And the bytes are identical too.
    assert value_a.encode("utf-8") == value_b.encode("utf-8")


def test_project_review_artifact_value_round_trips_through_model_validate_json() -> None:
    """The projected JSON re-parses cleanly via
    :meth:`~pydantic.BaseModel.model_validate_json`.

    Per chunk-shape point 3f: round-trip the projection body through
    Pydantic's typed validator so we know the canonical-JSON projection is
    a valid encoding of the original typed row.
    """

    evidence_set = _make_minimal_evidence_set(
        corpus_id="round-trip-json:1", read_budget_exhausted=False
    )
    _, artifact_value = project_review_artifact(evidence_set)
    # Re-validate via Pydantic -- raises if the JSON is malformed or fails
    # the 13a model invariants.
    rehydrated = GovernanceEvidenceSet.model_validate_json(artifact_value)
    assert rehydrated.corpus_id == evidence_set.corpus_id
    assert rehydrated.idempotency_key == evidence_set.idempotency_key
    assert rehydrated.read_budget_exhausted == evidence_set.read_budget_exhausted
    assert rehydrated.completeness == evidence_set.completeness
    assert rehydrated.quality == evidence_set.quality


def test_project_review_artifact_preserves_read_budget_exhausted_flag() -> None:
    """The ``read_budget_exhausted=True`` flag is preserved in the projection.

    Per chunk-shape point 3i + doc-13:215-220: the bounded-read invariant
    requires that downstream consumers can detect partial reads via
    ``read_budget_exhausted`` + ``omitted_refs``. The projection helper
    preserves both fields verbatim so a consumer reading the canonical-JSON
    body sees the same partial-read signal as the original typed row.
    """

    evidence_set = _make_minimal_evidence_set(
        corpus_id="bounded:1", read_budget_exhausted=True
    )
    _, artifact_value = project_review_artifact(evidence_set)
    parsed = json.loads(artifact_value)
    assert parsed["read_budget_exhausted"] is True, (
        "The read_budget_exhausted flag MUST be preserved verbatim in the "
        "review-artifact projection per doc-13:215-220."
    )
    # And re-validation pins the typed-side preservation.
    rehydrated = GovernanceEvidenceSet.model_validate_json(artifact_value)
    assert rehydrated.read_budget_exhausted is True


def test_project_review_artifact_does_not_mutate_evidence_set() -> None:
    """The projection helper is pure: it does not mutate its input.

    A side-effecting projection helper would let a downstream consumer's
    "project" call silently re-derive fields the digester intended to pin.
    Per the governance prompt § "Non-Negotiables" the governance surface is
    advisory / read-only.
    """

    evidence_set = _make_minimal_evidence_set(corpus_id="immut:1")
    original_dump = evidence_set.model_dump(mode="json")
    project_review_artifact(evidence_set)
    after_dump = evidence_set.model_dump(mode="json")
    assert original_dump == after_dump, (
        "project_review_artifact MUST NOT mutate its input; the helper is "
        "pure-functional."
    )


# ── real-corpus round-trip ─────────────────────────────────────────────────


_REAL_JOURNAL_PATH = Path(
    "docs/execution-control-plane/implementation-journal.md"
)
_REAL_DECISIONS_PATH = Path(
    "docs/execution-control-plane/implementation-decisions.jsonl"
)


@pytest.mark.skipif(
    not _REAL_JOURNAL_PATH.exists() or not _REAL_DECISIONS_PATH.exists(),
    reason="real fixtures not present; pure synthetic-only run",
)
def test_real_corpus_round_trip_parse_compose_project_reparse_revalidate() -> None:
    """End-to-end: real corpus -> parse -> compose -> project -> parse -> validate.

    Per chunk-shape point 3h: feed the real
    ``implementation-journal.md`` + ``implementation-decisions.jsonl`` through
    the 13c + 13d parsers, compose via the 13e digester, project via
    :func:`project_review_artifact`, re-parse the canonical JSON, and
    re-validate through the 13a model invariants. This pins the round-trip
    invariant on the real-shape input the governance phase actually consumes.
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
        corpus_id="real-corpus:13g-roundtrip",
    )
    # Project.
    artifact_key, artifact_value = project_review_artifact(evidence_set)
    assert artifact_key == "review:governance-evidence:real-corpus:13g-roundtrip"
    # Re-parse.
    parsed = json.loads(artifact_value)
    assert parsed["corpus_id"] == "real-corpus:13g-roundtrip"
    assert parsed["idempotency_key"] == evidence_set.idempotency_key
    # Re-validate -- the canonical-JSON projection re-hydrates via Pydantic
    # without raising (no 13a model invariant violation).
    rehydrated = GovernanceEvidenceSet.model_validate_json(artifact_value)
    assert rehydrated.idempotency_key == evidence_set.idempotency_key
    assert rehydrated.corpus_id == evidence_set.corpus_id
    assert rehydrated.completeness == evidence_set.completeness
    assert rehydrated.quality == evidence_set.quality
    # The real corpus has many refs.
    assert len(rehydrated.refs) > 0, (
        "Real corpus produces a non-empty refs list; if this fires the "
        "13c/13d parsers stopped producing anchors."
    )
    # Projecting the SAME evidence_set twice yields byte-identical artifacts
    # (project_review_artifact is pure). Note: two separate compose calls
    # would NOT yield byte-identical bytes because the 13e digester stamps
    # generated_at=datetime.now(timezone.utc) per call (the 13e digester at
    # evidence_set.py:905-910 documents this -- the idempotency_key is
    # invariant across re-stamping, but the surrounding JSON is not). The
    # 13e digester's idempotency_key invariance under re-stamp is tested in
    # ``tests/test_governance_evidence_set_digester.py::
    # test_real_anchors_compose_yields_stable_idempotency_key_across_two_runs``;
    # the 13g projection's byte-identity invariance over a fixed evidence
    # set is what this test pins.
    _, second_value_same_set = project_review_artifact(evidence_set)
    assert artifact_value == second_value_same_set, (
        "Two project_review_artifact calls over the SAME evidence set MUST "
        "yield byte-identical canonical-JSON bytes (the 13g projection is "
        "canonical-sorted + pure-functional)."
    )
    # And the second compose run's idempotency_key is byte-identical to the
    # first (pinning the 13e digester's idempotency_key invariance on the
    # real corpus from the 13g consumer side).
    second_set = compose_governance_evidence_set(
        journal_anchors=journal_anchors,
        decision_log_anchors=decision_anchors,
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=GovernanceWindow(),
        read_budget=GovernanceReadBudget(),
        corpus_id="real-corpus:13g-roundtrip",
    )
    assert second_set.idempotency_key == evidence_set.idempotency_key, (
        "13e digester idempotency_key MUST be invariant across re-runs "
        "on the same input (the 13g projection consumes this invariant)."
    )


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _REAL_JOURNAL_PATH.exists() or not _REAL_DECISIONS_PATH.exists(),
    reason="real fixtures not present; pure synthetic-only run",
)
async def test_real_corpus_store_put_then_get_round_trip() -> None:
    """Real-corpus evidence set survives a ``put`` -> ``get`` store cycle.

    Confirms the in-memory store accepts the real-corpus typed row + returns
    it intact (the typed-row surface is robust against the live corpus's
    real shape -- many refs / mixed authorities / non-empty source_mix).

    Async per the Slice 13i finalizer (P2-13i-1).
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
        corpus_id="real-corpus:13g-store-roundtrip",
    )
    store = InMemoryGovernanceEvidenceStore()
    await store.put(evidence_set)
    loaded = await store.get("real-corpus:13g-store-roundtrip")
    assert loaded is not None
    assert loaded is evidence_set
    assert loaded.idempotency_key == evidence_set.idempotency_key
    assert len(loaded.refs) == len(evidence_set.refs)


# ── read-only authority discipline (Slice 13A invariant) ──────────────────


def test_store_does_not_expose_any_dag_artifact_write_method() -> None:
    """The store surface has no ``dag-*`` write method.

    Per doc-13:201-203 verbatim ("Governance evidence sets may project
    review artifacts, but no ``dag-*`` execution, checkpoint, regroup
    activation, or merge artifact is written by this slice") the 13g store
    NEVER writes to those artifact spaces. Pin this with an explicit
    surface-scan test so a later sub-slice cannot silently add a writer.
    """

    store = InMemoryGovernanceEvidenceStore()
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
        for name in dir(store)
        if not name.startswith("_") and callable(getattr(store, name))
    ]
    for method in public_methods:
        lowered = method.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, (
                f"InMemoryGovernanceEvidenceStore public method {method!r} "
                f"contains forbidden substring {forbidden!r}. Per "
                "doc-13:201-203 the governance store NEVER writes dag-* / "
                "checkpoint / regroup / merge / commit artifacts."
            )


def test_inmemory_store_is_concrete_subclass_of_abc() -> None:
    """``InMemoryGovernanceEvidenceStore`` is a concrete
    :class:`GovernanceEvidenceStore`.

    Pins the inheritance chain so a consumer can pass an
    :class:`InMemoryGovernanceEvidenceStore` wherever a
    :class:`GovernanceEvidenceStore` is expected (Liskov substitution).
    """

    assert issubclass(InMemoryGovernanceEvidenceStore, GovernanceEvidenceStore)
    instance = InMemoryGovernanceEvidenceStore()
    assert isinstance(instance, GovernanceEvidenceStore)


# ── idempotency conflict error shape ──────────────────────────────────────


def test_idempotency_conflict_error_is_value_error_subclass() -> None:
    """The conflict error subclasses :class:`ValueError`.

    A consumer that does not import the typed error class can still catch
    the conflict via a bare ``ValueError`` (mirrors
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.IdempotencyConflict`
    at ``failure_router.py:555-568``).
    """

    assert issubclass(
        store_module.GovernanceEvidenceStoreIdempotencyConflict, ValueError
    )


@pytest.mark.asyncio
async def test_idempotency_conflict_error_message_names_corpus_and_keys() -> None:
    """The error message names the corpus_id + both idempotency keys.

    A reviewer reading a failed-put traceback should see the corpus and the
    two conflicting content fingerprints without having to read the store's
    state.

    Async per the Slice 13i finalizer (P2-13i-1).
    """

    store = InMemoryGovernanceEvidenceStore()
    await store.put(_make_minimal_evidence_set(
        corpus_id="diag:1", idempotency_key="1" * 64
    ))
    with pytest.raises(ValueError) as exc_info:
        await store.put(_make_minimal_evidence_set(
            corpus_id="diag:1", idempotency_key="2" * 64
        ))
    message = str(exc_info.value)
    assert "diag:1" in message
    assert "1" * 64 in message
    assert "2" * 64 in message
    assert "idempotency_key" in message


# ── typed-input validation (P3-13g-R2 finalizer remediation) ──────────────


@pytest.mark.asyncio
async def test_put_raises_type_error_on_non_evidence_set_input() -> None:
    """``put(non-GovernanceEvidenceSet)`` raises :class:`TypeError`.

    Per the 13g finalizer P3-13g-R2 remediation: the API-entry boundary
    of :meth:`InMemoryGovernanceEvidenceStore.put` fails fast with a
    typed :class:`TypeError` when the caller passes ``None`` or a
    non-:class:`GovernanceEvidenceSet` value. Without this guard the
    method would crash on ``evidence_set.corpus_id`` access with an
    opaque :class:`AttributeError`, violating the
    ``feedback_no_silent_degradation`` rule.

    Pins behavior for three bad-input forms:

    * ``None`` -- the most common "I forgot to construct it" case.
    * A bare ``str`` -- the most common "I passed the corpus_id by
      mistake" case.
    * A dict shaped like an evidence set -- the most common "I forgot to
      validate via Pydantic" case.

    Behavior on valid input is unchanged (the type-check is a pure
    entry-boundary guard); the rest of the test suite pins the valid-
    input contract.

    **Async surface (Slice 13i finalizer P2-13i-1).** ``put`` is now
    ``async def``; the typed :class:`TypeError` early-raise still fires
    synchronously the moment the returned coroutine is awaited (the
    ``raise`` is reached before any ``await`` would otherwise yield
    control). This pin guards against a regression where the type-check
    is silently lost (e.g. by moving it past an early ``await``).
    """

    store = InMemoryGovernanceEvidenceStore()

    # ``None`` -- the canonical bad-input case from the P3-13g-R2 prompt.
    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put(None)  # type: ignore[arg-type]

    # A bare ``str`` -- "I passed the corpus_id by mistake" case.
    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put("not-a-model")  # type: ignore[arg-type]

    # A dict shaped like the evidence set -- "I forgot to validate via
    # Pydantic" case. The dict has the right keys but is NOT a typed
    # GovernanceEvidenceSet; the type-check rejects it before the
    # corpus_id lookup.
    with pytest.raises(TypeError, match="GovernanceEvidenceSet"):
        await store.put({"corpus_id": "fake", "idempotency_key": "x" * 64})  # type: ignore[arg-type]

    # The store remains empty after the three rejected puts -- the
    # type-check is a pure pre-write guard (fail-fast; no partial state).
    assert await store.get("not-a-model") is None
    assert await store.get("fake") is None

    # And a valid put on the same store still works (the type-check does
    # not poison the store instance).
    valid = _make_minimal_evidence_set(corpus_id="after-typeerror:1")
    await store.put(valid)
    assert await store.get("after-typeerror:1") is valid
