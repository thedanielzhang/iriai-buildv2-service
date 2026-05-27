"""Slice 13a -- unit tests for the governance evidence model.

Covers the 8 doc-13 pure typed shapes
(``docs/execution-control-plane/13-governance-evidence-model.md:68-151``):

- The 3 ``Literal`` enums (``EvidenceAuthority``, ``EvidenceQuality``,
  ``CompletenessState``) -- exact members + rejection of unknown values
  routed through the BaseModel containers that use them.
- The 5 ``BaseModel`` classes:
  - :class:`GovernanceReadBudget` -- doc-13:90-95 verbatim default
    integers, positive-budget invariant.
  - :class:`GovernanceEvidencePageRef` -- required ``exact: bool``
    (Slice 13A invariant precursor), non-empty identifier invariants.
  - :class:`GovernanceEvidenceRef` -- typed-source contract,
    ``preview_only`` default-False, page-refs default-empty.
  - :class:`GovernanceEvidenceSet` -- corpus-identity invariants,
    ``source_mix`` non-negative-count invariant, defaults for
    ``read_budget_exhausted`` + ``source_mix``.
  - :class:`ImplementationArtifactAnchor` -- 1-indexed line number
    invariant, ``open_findings`` dedup + non-empty invariant.

Every model round-trips ``model_dump_json`` -> ``model_validate_json``
identically.

Per the governance prompt § "Non-Negotiables" -- no silent degradation,
fail-closed defaults; per the auto-memory ``feedback_no_silent_degradation``
+ ``feedback_verify_changes`` -- every doc-13 invariant is paired with a
test that the invariant fires on bad input.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from iriai_build_v2.workflows.develop import governance
from iriai_build_v2.workflows.develop.governance import (
    CompletenessState,
    EvidenceAuthority,
    EvidenceQuality,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
    ImplementationArtifactAnchor,
)
from iriai_build_v2.workflows.develop.governance import models as governance_models


# ── package surface ────────────────────────────────────────────────────────


def test_governance_package_reexports_doc_13_surface() -> None:
    """The ``models`` module owns the 8 doc-13:68-151 typed shapes verbatim
    PLUS the 2 Slice 13m TypedDict tightenings (P3-13a-4 closure); the
    package ``__init__`` re-exports them all PLUS the sub-slice 13b
    ingestor surface PLUS the sub-slice 13c implementation-journal parser
    surface PLUS the sub-slice 13d JSONL decision-log parser surface PLUS
    the sub-slice 13e evidence-set digester surface PLUS the sub-slice 13g
    typed-row store + review-artifact projection + typed conflict
    surfaces PLUS the sub-slice 13i Postgres-backed concrete PLUS the
    Slice 13A first sub-slice completeness scanner surfaces, totalling
    **exactly 26 documented exports**.

    Strict equality on ``governance.__all__`` (not subset) so a future
    sub-slice that silently re-exports an unrelated symbol fails this
    test loudly. Mirrors the 13a strict-equality discipline; restored
    after the 13b implementer relaxed it during the package surface
    expansion (reviewer P1-13b-1); extended in 13c to cover the
    journal-parser export (``parse_implementation_journal``); extended
    in 13d to cover the decision-log-parser export
    (``parse_implementation_decision_log``); extended in 13e to cover the
    evidence-set-digester export (``compose_governance_evidence_set``);
    extended in 13g to cover the typed-row store + review-artifact
    projection exports (``GovernanceEvidenceStore`` +
    ``InMemoryGovernanceEvidenceStore`` + ``project_review_artifact``);
    further extended by the 13g finalizer to cover the typed conflict
    error (``GovernanceEvidenceStoreIdempotencyConflict``) per
    P3-13g-R3 mirroring the failure_router precedent at
    ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:1912``;
    extended in 13i to cover the Postgres-backed concrete
    (``PostgresGovernanceEvidenceStore``) -- the asyncpg-backed
    :class:`GovernanceEvidenceStore` implementation over the new
    ``governance_evidence_sets`` table at ``schema.sql:898-957``;
    extended in 13m to cover the 2 TypedDict tightenings
    (``EvidenceSetSourceWindow`` + ``EvidencePageRefStaleCheck``)
    that close P3-13a-4 -- the doc-13:113-126 + doc-13:128-141
    ``dict[str, Any]`` shapes are now typed via ``total=False``
    TypedDicts naming the documented producer-side keys; extended in
    the Slice 13A first sub-slice to cover the completeness scanner
    surfaces (``scan_governance_completeness`` free function +
    ``CompletenessScanReport`` typed report) per STATUS.md § "Next
    safe action" point 4 + doc-13:209-210 (canonical
    ``governance_evidence_gap`` blocker form).

    13a (8 doc-13 typed shapes -- 3 enums + 5 BaseModels) + 13b
    (6 ingestor-surface shapes -- ABC + default impl + 2 ABC-signature
    shapes + 2 bounded-reader port shapes) + 13c (1 journal-parser
    surface) + 13d (1 decision-log-parser surface) + 13e (1
    evidence-set-digester surface) + 13g (4 typed-row store +
    review-artifact projection + typed conflict surfaces) + 13i (1
    Postgres-backed concrete) + 13m (2 TypedDict tightenings) +
    Slice 13A first sub-slice (2 completeness scanner surfaces) =
    26 exports total.
    """

    expected_models = {
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
        # Sub-slice 13m (P3-13a-4 closure) -- TypedDict tightenings of
        # the doc-13:113-126 + doc-13:128-141 ``dict[str, Any]`` shapes.
        # Both are ``total=False`` so all keys are optional per the
        # conservative interpretation of doc-13 wording (which does NOT
        # enumerate the accepted keys); runtime semantics unchanged.
        "EvidenceSetSourceWindow",
        "EvidencePageRefStaleCheck",
    }
    expected_ingestor_13b = {
        # Sub-slice 13b -- ABC + default implementation (doc-13:153-171).
        "GovernanceEvidenceIngestor",
        "DefaultGovernanceEvidenceIngestor",
        # Sub-slice 13b -- ABC-signature shapes the ABC requires.
        "GovernanceWindow",
        "GovernanceEvidenceSlice",
        # Sub-slice 13b -- bounded-reader constructor-injection port.
        "BoundedReader",
        "BoundedReadResult",
    }
    expected_journal_parser_13c = {
        # Sub-slice 13c -- doc-13:182-183 § "Refactoring Steps" step 3
        # ("Add an implementation-journal parser that produces anchors
        # from markdown headings, bullet lines, subagent IDs, test
        # result lines, and acceptance notes").
        "parse_implementation_journal",
    }
    expected_decision_log_parser_13d = {
        # Sub-slice 13d -- doc-13:184-185 § "Refactoring Steps" step 4
        # ("Add a JSONL decision-log parser that rejects malformed rows
        # and records line numbers as evidence anchors"). Fills the
        # previously-always-None decision_log_line field on
        # ImplementationArtifactAnchor; 13c anchors have
        # decision_log_line=None, 13d anchors have line_start=None and
        # decision_log_line=<row#>.
        "parse_implementation_decision_log",
    }
    expected_evidence_set_13e = {
        # Sub-slice 13e -- doc-13:186-187 § "Refactoring Steps" step 5
        # ("Add evidence-set digesting from sorted canonical JSON. The
        # digest must include source ids and content digests, not full
        # artifact bodies"). The pure-typed digester composes the four
        # input lanes (journal anchors + decision-log anchors +
        # supervisor-digest refs + resource-snapshot refs) into a fully
        # populated 13a GovernanceEvidenceSet with the doc-13:186
        # verbatim per-ref digest contract + sort-invariant set-level
        # idempotency_key.
        "compose_governance_evidence_set",
    }
    expected_store_13g = {
        # Sub-slice 13g -- doc-13:188-190 § "Refactoring Steps" step 6
        # ("Store governance evidence sets as typed rows once the Slice
        # 01 store exists, and project bounded review artifacts such as
        # review:governance-evidence:{corpus_id}"). The typed-row store
        # ABC + the in-memory concrete impl + the bounded review-artifact
        # projection helper land the doc-13:188-190 contract; the store
        # is READ-only authority until Slice 13A (no consumer treats its
        # output as execution authority) and NEVER writes dag-* execution
        # / checkpoint / regroup activation / merge artifacts per
        # doc-13:201-203 verbatim.
        "GovernanceEvidenceStore",
        "InMemoryGovernanceEvidenceStore",
        "project_review_artifact",
        # Sub-slice 13g finalizer (P3-13g-R3) -- typed conflict error
        # raised by ``put`` on ``corpus_id`` collision with a different
        # ``idempotency_key``. Promoted from module-local to package-
        # level per the failure_router precedent at
        # ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:1912``
        # where ``IdempotencyConflict`` is also in ``__all__`` --
        # consumers can ``except GovernanceEvidenceStoreIdempotencyConflict``
        # without reaching into the sibling ``.store`` module.
        "GovernanceEvidenceStoreIdempotencyConflict",
    }
    expected_postgres_store_13i = {
        # Sub-slice 13i -- doc-13:188-190 § "Refactoring Steps" step 6
        # production-stack half ("Store governance evidence sets as
        # typed rows once the Slice 01 store exists"). The asyncpg-
        # backed concrete that implements the 13g
        # GovernanceEvidenceStore ABC over the new
        # governance_evidence_sets table (schema.sql:898-957). Async
        # by necessity (asyncpg cannot be invoked synchronously); the
        # 13g ABC's sync method signatures are overridden with
        # ``async def`` here per the abc.ABCMeta-presence rule.
        # Idempotent on (corpus_id, idempotency_key); fail-closed on
        # corpus_id collision with a different idempotency_key via the
        # typed GovernanceEvidenceStoreIdempotencyConflict mirroring
        # the in-memory 13g concrete. Mirrors the
        # src/iriai_build_v2/execution_control/regroup_overlay_store.py
        # asyncpg-backed-concrete precedent: connection-bound
        # constructor; canonical-JSON _jsonb serializer
        # (regroup_overlay_store.py:305-306); transactional INSERT
        # idempotency + fail-closed conflict raise pattern
        # (regroup_overlay_store.py:382-435 + 602-621); typed-input-
        # validation TypeError guard mirroring 13g store.py:299-326
        # (P3-13g-R2).
        "PostgresGovernanceEvidenceStore",
    }
    expected_completeness_scanner_13A_first = {
        # Slice 13A first sub-slice -- completeness scanner surface
        # per STATUS.md § "Next safe action" point 4 + the Slice 13A
        # invariant at doc-13a:24, 109-118. The scanner detects:
        # (a) missing Slice 00-12 acceptance markers (cross-references
        # STATUS.md + the JSONL slice_end_finalizer_after rows);
        # (b) unresolved P1/P2 findings across the journal tail;
        # (c) any governance_evidence_gap blocker (non-legacy class)
        # in a consumed GovernanceEvidenceSet. Emits a typed
        # CompletenessScanReport with is_complete=True iff every
        # detection list is empty. Pure-typed free function; stdlib +
        # governance siblings + Pydantic only; no executor wiring; no
        # consumption as execution authority OUTSIDE Slice 13A's own
        # acceptance tests per the implementer prompt § non-negotiables.
        "CompletenessScanReport",
        "scan_governance_completeness",
    }
    expected_package_all = (
        expected_models
        | expected_ingestor_13b
        | expected_journal_parser_13c
        | expected_decision_log_parser_13d
        | expected_evidence_set_13e
        | expected_store_13g
        | expected_postgres_store_13i
        | expected_completeness_scanner_13A_first
    )
    # 8 13a typed shapes + 6 13b ingestor-surface shapes + 1 13c
    # journal-parser surface + 1 13d decision-log-parser surface + 1
    # 13e evidence-set-digester surface + 4 13g store + projection +
    # typed conflict surfaces + 1 13i Postgres-backed concrete + 2 13m
    # TypedDict tightenings + 2 Slice 13A first sub-slice completeness
    # scanner surfaces = 26 total.
    assert len(expected_package_all) == 26
    # The models module is the pure 13a typed-model scaffolding -- its
    # ``__all__`` MUST stay exactly the 8 doc-13 shapes (doc-13:179
    # "with pure model definitions and no executor hooks") PLUS the 2
    # Slice 13m TypedDict tightenings (P3-13a-4 closure) that name
    # the documented producer-side keys for the
    # :attr:`GovernanceEvidenceSet.source_window` +
    # :attr:`GovernanceEvidencePageRef.stale_check` fields = 10 total.
    assert set(governance_models.__all__) == expected_models
    # STRICT equality on the package -- the 26-element documented surface
    # must match byte-for-byte so a future silent re-export of an
    # unrelated symbol fails this test loudly (reviewer P1-13b-1; the
    # 13c + 13d + 13e + 13g extensions keep the strict-equality
    # discipline; the 13g finalizer further bumped the count 20 -> 21
    # per P3-13g-R3; the 13i implementer bumped 21 -> 22 by adding the
    # Postgres-backed PostgresGovernanceEvidenceStore concrete; the
    # 13m implementer bumped 22 -> 24 by adding the 2 TypedDict
    # tightenings that close P3-13a-4; the Slice 13A first sub-slice
    # implementer bumps 24 -> 26 by adding the 2 completeness scanner
    # surfaces per STATUS.md § "Next safe action" point 4).
    assert set(governance.__all__) == expected_package_all
    for name in expected_models:
        # Each typed-model export is reachable from both the package and
        # the models module, and they are the same Python object.
        assert hasattr(governance, name)
        assert hasattr(governance_models, name)
        assert getattr(governance, name) is getattr(governance_models, name)


# ── EvidenceAuthority (doc-13:74-84) ───────────────────────────────────────


_EVIDENCE_AUTHORITY_MEMBERS = (
    "typed_journal",
    "compatibility_projection",
    "git_provenance",
    "implementation_journal",
    "implementation_decision_log",
    "supervisor_digest",
    "resource_snapshot",
    "legacy_event",
    "legacy_artifact_summary",
)


def test_evidence_authority_has_doc_13_members_exactly() -> None:
    """``EvidenceAuthority`` mirrors the doc-13:74-84 9-value enum verbatim."""

    assert EvidenceAuthority.__args__ == _EVIDENCE_AUTHORITY_MEMBERS
    assert len(_EVIDENCE_AUTHORITY_MEMBERS) == 9


# ── EvidenceQuality (doc-13:86) ────────────────────────────────────────────


_EVIDENCE_QUALITY_MEMBERS = (
    "canonical",
    "derived",
    "sampled",
    "advisory",
    "stale",
    "insufficient",
)


def test_evidence_quality_has_doc_13_members_exactly() -> None:
    """``EvidenceQuality`` mirrors the doc-13:86 6-value enum verbatim."""

    assert EvidenceQuality.__args__ == _EVIDENCE_QUALITY_MEMBERS
    assert len(_EVIDENCE_QUALITY_MEMBERS) == 6


# ── CompletenessState (doc-13:87) ──────────────────────────────────────────


_COMPLETENESS_STATE_MEMBERS = (
    "complete",
    "paged",
    "preview_only",
    "unavailable",
)


def test_completeness_state_has_doc_13_members_exactly() -> None:
    """``CompletenessState`` mirrors the doc-13:87 4-value enum verbatim."""

    assert CompletenessState.__args__ == _COMPLETENESS_STATE_MEMBERS
    assert len(_COMPLETENESS_STATE_MEMBERS) == 4


# ── GovernanceReadBudget (doc-13:89-95) ────────────────────────────────────


def test_governance_read_budget_defaults_match_doc_13_verbatim() -> None:
    """All six defaults are the doc-13:90-95 verbatim integers."""

    budget = GovernanceReadBudget()
    assert budget.max_event_rows == 500
    assert budget.max_artifact_summary_rows == 5_000
    assert budget.max_ref_resolutions == 20
    assert budget.max_chars_per_ref == 40_000
    assert budget.max_serialized_output_bytes == 2_000_000
    assert budget.statement_timeout_ms == 10_000


def test_governance_read_budget_round_trip() -> None:
    """``model_dump_json`` -> ``model_validate_json`` is identity."""

    budget = GovernanceReadBudget(
        max_event_rows=10,
        max_artifact_summary_rows=20,
        max_ref_resolutions=3,
        max_chars_per_ref=100,
        max_serialized_output_bytes=4096,
        statement_timeout_ms=500,
    )
    serialised = budget.model_dump_json()
    restored = GovernanceReadBudget.model_validate_json(serialised)
    assert restored == budget
    # And the round-trip payload itself is byte-identical.
    assert restored.model_dump_json() == serialised


@pytest.mark.parametrize(
    "field",
    [
        "max_event_rows",
        "max_artifact_summary_rows",
        "max_ref_resolutions",
        "max_chars_per_ref",
        "max_serialized_output_bytes",
        "statement_timeout_ms",
    ],
)
@pytest.mark.parametrize("bad", [0, -1, -500])
def test_governance_read_budget_rejects_non_positive_field(
    field: str, bad: int
) -> None:
    """Zero / negative budgets fail closed -- no silent degradation."""

    kwargs = {field: bad}
    with pytest.raises(ValidationError) as exc:
        GovernanceReadBudget(**kwargs)
    assert "positive integers" in str(exc.value)


# ── GovernanceEvidencePageRef (doc-13:113-126) ─────────────────────────────


def _page_ref(**overrides: object) -> GovernanceEvidencePageRef:
    base: dict[str, object] = dict(
        page_ref_id="page-1",
        authority="typed_journal",
        source_ref_id="src-1",
        digest="sha256:aaa",
        completeness="complete",
        exact=True,
        stale_check={"file_mtime": 1.0},
    )
    base.update(overrides)
    return GovernanceEvidencePageRef(**base)  # type: ignore[arg-type]


def test_page_ref_exact_is_required_no_default() -> None:
    """Slice 13A invariant precursor: ``exact`` has no default.

    Per the governance prompt § "Slice 13A invariant for downstream slices"
    every construction site must declare exactness explicitly. A missing
    ``exact`` value MUST raise (not default to ``True`` / ``False``).
    """

    with pytest.raises(ValidationError) as exc:
        GovernanceEvidencePageRef(
            page_ref_id="page-1",
            authority="typed_journal",
            source_ref_id="src-1",
            digest="sha256:aaa",
            completeness="complete",
            # Deliberately omit ``exact``.
            stale_check={},
        )
    # Confirm ``exact`` is the field that failed (not some other required
    # field) so the test really exercises the no-default contract.
    errors = exc.value.errors()
    assert any(err["loc"] == ("exact",) for err in errors)


def test_page_ref_round_trip_with_optional_ranges() -> None:
    """Optional byte/line/item ranges survive round-trip."""

    ref = _page_ref(
        byte_start=0,
        byte_end=4096,
        line_start=1,
        line_end=200,
        item_start=0,
        item_end=42,
        exact=False,
        completeness="paged",
        stale_check={"commit": "abc123", "row_version": 7},
    )
    restored = GovernanceEvidencePageRef.model_validate_json(
        ref.model_dump_json()
    )
    assert restored == ref


@pytest.mark.parametrize(
    "field", ["page_ref_id", "source_ref_id", "digest"]
)
@pytest.mark.parametrize("bad", ["", "   "])
def test_page_ref_rejects_empty_identifiers(field: str, bad: str) -> None:
    """Empty page-ref-id / source-ref-id / digest fail closed."""

    with pytest.raises(ValidationError):
        _page_ref(**{field: bad})


def test_page_ref_rejects_unknown_authority() -> None:
    """``EvidenceAuthority`` literal rejects unknown values."""

    with pytest.raises(ValidationError):
        _page_ref(authority="not_a_real_authority")  # type: ignore[arg-type]


def test_page_ref_rejects_unknown_completeness() -> None:
    """``CompletenessState`` literal rejects unknown values."""

    with pytest.raises(ValidationError):
        _page_ref(completeness="green")  # type: ignore[arg-type]


# ── GovernanceEvidenceRef (doc-13:97-111) ──────────────────────────────────


def _ref(**overrides: object) -> GovernanceEvidenceRef:
    base: dict[str, object] = dict(
        authority="typed_journal",
        ref_id="ref-1",
        digest="sha256:bbb",
        quality="canonical",
        completeness="complete",
    )
    base.update(overrides)
    return GovernanceEvidenceRef(**base)  # type: ignore[arg-type]


def test_ref_required_field_set() -> None:
    """The 5 doc-13:98-109 required fields are required.

    All other fields (feature_id / slice_id / artifact_id / event_id /
    commit_hash / journal_anchor / created_at) are optional.
    """

    minimum = _ref()
    assert minimum.authority == "typed_journal"
    assert minimum.ref_id == "ref-1"
    assert minimum.digest == "sha256:bbb"
    assert minimum.quality == "canonical"
    assert minimum.completeness == "complete"
    # Defaults per doc-13:100-111.
    assert minimum.feature_id is None
    assert minimum.slice_id is None
    assert minimum.artifact_id is None
    assert minimum.event_id is None
    assert minimum.commit_hash is None
    assert minimum.journal_anchor is None
    assert minimum.created_at is None
    assert minimum.page_refs == []
    assert minimum.preview_only is False


def test_ref_round_trip_with_page_refs() -> None:
    """Nested page refs survive ``model_dump_json`` round-trip."""

    page = _page_ref(exact=False, completeness="paged")
    ref = _ref(
        feature_id="feat-1",
        slice_id="13a",
        artifact_id=42,
        event_id=99,
        commit_hash="deadbeef",
        journal_anchor="journal#13a",
        created_at=datetime(2026, 5, 24, 1, 0, 0, tzinfo=timezone.utc),
        completeness="paged",
        page_refs=[page],
        preview_only=False,
    )
    restored = GovernanceEvidenceRef.model_validate_json(ref.model_dump_json())
    assert restored == ref
    assert len(restored.page_refs) == 1
    assert restored.page_refs[0] == page


def test_ref_default_page_refs_are_independent_lists() -> None:
    """Default-factory list is not shared across instances."""

    a = _ref()
    b = _ref()
    a.page_refs.append(_page_ref())
    assert a.page_refs != b.page_refs
    assert b.page_refs == []


@pytest.mark.parametrize("field", ["ref_id", "digest"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_ref_rejects_empty_identifiers(field: str, bad: str) -> None:
    """Empty ref_id / digest fail closed."""

    with pytest.raises(ValidationError):
        _ref(**{field: bad})


def test_ref_rejects_unknown_quality() -> None:
    """``EvidenceQuality`` literal rejects unknown values."""

    with pytest.raises(ValidationError):
        _ref(quality="provisional")  # type: ignore[arg-type]


def test_ref_rejects_unknown_authority() -> None:
    """``EvidenceAuthority`` literal rejects unknown values."""

    with pytest.raises(ValidationError):
        _ref(authority="unknown_source")  # type: ignore[arg-type]


# ── GovernanceEvidenceSet (doc-13:128-141) ─────────────────────────────────


def _evidence_set(**overrides: object) -> GovernanceEvidenceSet:
    base: dict[str, object] = dict(
        idempotency_key="idem-1",
        feature_id=None,
        corpus_id="corpus-1",
        generated_at=datetime(2026, 5, 24, 1, 0, 0, tzinfo=timezone.utc),
        source_window={"start": 1, "end": 2},
        refs=[],
        omitted_refs=[],
        completeness="complete",
        read_budget=GovernanceReadBudget(),
        quality="canonical",
        blockers=[],
    )
    base.update(overrides)
    return GovernanceEvidenceSet(**base)  # type: ignore[arg-type]


def test_evidence_set_required_field_set_and_defaults() -> None:
    """All 11 doc-13:129-141 required fields are required; the 2 default
    fields apply."""

    s = _evidence_set()
    assert s.idempotency_key == "idem-1"
    assert s.feature_id is None
    assert s.corpus_id == "corpus-1"
    # Doc-13:137 / 139 defaults.
    assert s.source_mix == {}
    assert s.read_budget_exhausted is False


def test_evidence_set_round_trip_with_nested_refs() -> None:
    """Nested refs + omitted page-refs survive round-trip."""

    page = _page_ref(exact=False, completeness="paged")
    ref = _ref(completeness="paged", page_refs=[page])
    s = _evidence_set(
        feature_id="feat-1",
        refs=[ref],
        omitted_refs=[page],
        completeness="paged",
        source_mix={"typed_journal": 1, "legacy_event": 2},
        read_budget_exhausted=True,
        quality="derived",
        blockers=["read_budget_exhausted", "missing_implementation_journal"],
    )
    restored = GovernanceEvidenceSet.model_validate_json(s.model_dump_json())
    assert restored == s


@pytest.mark.parametrize("field", ["idempotency_key", "corpus_id"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_evidence_set_rejects_empty_identity(field: str, bad: str) -> None:
    """Empty ``idempotency_key`` / ``corpus_id`` fail closed."""

    with pytest.raises(ValidationError):
        _evidence_set(**{field: bad})


def test_evidence_set_rejects_negative_source_mix_count() -> None:
    """``source_mix`` counts must be >= 0; negative counts fail closed."""

    with pytest.raises(ValidationError) as exc:
        _evidence_set(source_mix={"typed_journal": -1})
    assert ">= 0" in str(exc.value)


def test_evidence_set_rejects_unknown_authority_in_source_mix() -> None:
    """``source_mix`` keys are typed as ``EvidenceAuthority``."""

    with pytest.raises(ValidationError):
        _evidence_set(source_mix={"made_up_authority": 1})


def test_evidence_set_rejects_unknown_quality() -> None:
    """``EvidenceQuality`` literal rejects unknown values."""

    with pytest.raises(ValidationError):
        _evidence_set(quality="great")  # type: ignore[arg-type]


def test_evidence_set_rejects_unknown_completeness() -> None:
    """``CompletenessState`` literal rejects unknown values."""

    with pytest.raises(ValidationError):
        _evidence_set(completeness="partial")  # type: ignore[arg-type]


# ── ImplementationArtifactAnchor (doc-13:143-150) ──────────────────────────


def _anchor(**overrides: object) -> ImplementationArtifactAnchor:
    base: dict[str, object] = dict(
        slice_id="13a",
        journal_path="docs/execution-control-plane/implementation-journal.md",
        line_start=None,
        decision_log_line=None,
        event="starting",
        accepted=False,
        open_findings=[],
    )
    base.update(overrides)
    return ImplementationArtifactAnchor(**base)  # type: ignore[arg-type]


def test_anchor_required_field_set() -> None:
    """All 7 doc-13:144-150 fields are required (optionals stay nullable)."""

    a = _anchor()
    assert a.slice_id == "13a"
    assert a.line_start is None
    assert a.decision_log_line is None
    assert a.event == "starting"
    assert a.accepted is False
    assert a.open_findings == []


def test_anchor_round_trip() -> None:
    """Round-trip survives populated line numbers + open findings.

    Slice 13l (P3-13c-1 + P3-13d-1 closure) note: ``event`` is now typed
    against the :data:`JournalEventName` Literal alias (7-value union).
    The original test used ``event="acceptance"`` which is a real
    decision-log SOURCE-vocabulary value but NOT in the typed 7-value
    union (the 13d decision-log parser's ``_EVENT_RENAME`` table at
    ``decision_log_parser.py:234`` converts source ``"acceptance"`` →
    canonical ``"accepted"`` before constructing the anchor, so the
    raw ``"acceptance"`` string never appears in a real-world emitted
    anchor). Updated to ``"accepted"`` to use the canonical post-rename
    value — the round-trip discipline (populated event + accepted=True
    + populated open_findings + line numbers) is preserved.
    """

    a = _anchor(
        line_start=35857,
        decision_log_line=1121,
        event="accepted",
        accepted=True,
        open_findings=["P3-12c-1", "P3-13a-1"],
    )
    restored = ImplementationArtifactAnchor.model_validate_json(
        a.model_dump_json()
    )
    assert restored == a


@pytest.mark.parametrize("field", ["slice_id", "journal_path", "event"])
@pytest.mark.parametrize("bad", ["", "   "])
def test_anchor_rejects_empty_identifiers(field: str, bad: str) -> None:
    """Empty slice_id / journal_path / event fail closed."""

    with pytest.raises(ValidationError):
        _anchor(**{field: bad})


@pytest.mark.parametrize("field", ["line_start", "decision_log_line"])
@pytest.mark.parametrize("bad", [0, -1, -100])
def test_anchor_rejects_non_positive_line_numbers(field: str, bad: int) -> None:
    """Line numbers are 1-indexed; zero/negative values fail closed."""

    with pytest.raises(ValidationError) as exc:
        _anchor(**{field: bad})
    assert ">= 1" in str(exc.value)


def test_anchor_rejects_duplicate_open_findings() -> None:
    """``open_findings`` is canonical -- duplicates fail closed."""

    with pytest.raises(ValidationError) as exc:
        _anchor(open_findings=["P3-1", "P3-1"])
    assert "duplicate" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   "])
def test_anchor_rejects_empty_open_findings_entry(bad: str) -> None:
    """Empty finding ids in ``open_findings`` fail closed."""

    with pytest.raises(ValidationError):
        _anchor(open_findings=[bad])


# ── Doc-13 § "Pure typed-model scaffolding" boundary -----------------------


def test_governance_models_module_exposes_no_executor_hooks() -> None:
    """Doc-13:179 -- "with pure model definitions and no executor hooks".

    The ``models`` module's public ``__all__`` must contain ONLY the 8 doc-13
    typed shapes, NOT a ``GovernanceEvidenceIngestor`` -- the ingestor lives
    in the sibling ``ingestor`` module (sub-slice 13b) so the typed-model
    scaffolding stays pure-typed. The package ``__init__`` re-exports both
    surfaces (per sub-slice 13b), but the ``models`` module boundary
    enforces doc-13:179 verbatim.
    """

    assert "GovernanceEvidenceIngestor" not in governance_models.__all__
    # The module attribute on ``models`` itself is genuinely absent (not
    # just hidden from ``__all__``); doc-13:179 forbids it from the typed-
    # model scaffolding module.
    assert not hasattr(governance_models, "GovernanceEvidenceIngestor")
    # The package DOES re-export the ingestor as of sub-slice 13b -- that
    # is the doc-13:178-181 step-2 deliverable.
    assert "GovernanceEvidenceIngestor" in governance.__all__
    assert hasattr(governance, "GovernanceEvidenceIngestor")


# ── Slice 13A invariant -- preview_only / exact vs completeness ───────────
#
# Doc-13a:24, 109-118 -- "Lossy summaries and previews are display-only";
# blocking deviation: "a deterministic summary is treated as satisfying
# required evidence unless it is a typed proof row with a digest and exact
# page refs back to the source." The two cross-validators in models.py
# enforce that the typed surface refuses inconsistent combinations so a
# later sub-slice cannot admit a preview-only record to an authoritative
# consumer based on either field alone.


def test_ref_rejects_preview_only_true_with_non_preview_completeness() -> None:
    """``Ref(preview_only=True, completeness="complete")`` fails closed.

    Slice 13A invariant (doc-13a:24, 109-118): preview_only=True must agree
    with completeness="preview_only" -- otherwise an authoritative consumer
    that keys off ``completeness`` would silently admit a preview-only ref.
    """

    with pytest.raises(ValidationError) as exc:
        _ref(preview_only=True, completeness="complete")
    assert "preview_only" in str(exc.value)
    assert "preview_only" in str(exc.value) and "completeness" in str(exc.value)


def test_page_ref_rejects_exact_true_with_preview_only_completeness() -> None:
    """``PageRef(exact=True, completeness="preview_only")`` fails closed.

    Slice 13A invariant (doc-13a:24, 109-118): a preview-only page cannot
    simultaneously claim exact evidence -- the typed surface refuses the
    contradiction at construction time.
    """

    with pytest.raises(ValidationError) as exc:
        _page_ref(exact=True, completeness="preview_only")
    assert "preview_only" in str(exc.value) or "preview-only" in str(exc.value)


def test_page_ref_accepts_paged_and_exact_happy_path() -> None:
    """``PageRef(exact=True, completeness="paged")`` is the legitimate
    paged-exact evidence shape and must NOT be rejected.

    Confirms the new ``_exact_completeness_consistency`` validator does not
    false-fire on the doc-13a § "exact paged manifest" case (line 22-23 /
    the Slice 13A invariant proper).
    """

    ref = _page_ref(exact=True, completeness="paged")
    assert ref.exact is True
    assert ref.completeness == "paged"
    # And the round-trip still works -- the validator runs on
    # ``model_validate_json`` too.
    restored = GovernanceEvidencePageRef.model_validate_json(
        ref.model_dump_json()
    )
    assert restored == ref


# ── extra='forbid' invariant ──────────────────────────────────────────────
#
# All 5 BaseModels carry ``model_config = ConfigDict(extra="forbid")`` per
# the sibling executor model precedent at workflows/develop/execution/
# verification.py:74 and failure_router.py:576. This catches typo-d field
# names at construction time instead of silently absorbing them.


def test_all_five_models_reject_unknown_fields() -> None:
    """Each of the 5 BaseModels rejects unknown field names at construction.

    All 5 carry ``model_config = ConfigDict(extra="forbid")`` so a typo-d
    field name fails closed instead of being silently absorbed.
    """

    # Each factory raises ValidationError when constructed with an extra
    # ``unknown_field`` kwarg. Bundled into one test (rather than a
    # parametrize) per the finalizer contract that 13a adds exactly 1 new
    # extra='forbid' test on top of the 3 new cross-validation tests
    # (target total: 71 + 3 + 1 = 75).
    factories = [
        ("GovernanceReadBudget", lambda: GovernanceReadBudget(unknown_field=1)),
        ("GovernanceEvidencePageRef", lambda: _page_ref(unknown_field=1)),
        ("GovernanceEvidenceRef", lambda: _ref(unknown_field=1)),
        ("GovernanceEvidenceSet", lambda: _evidence_set(unknown_field=1)),
        (
            "ImplementationArtifactAnchor",
            lambda: _anchor(unknown_field=1),
        ),
    ]
    for name, build in factories:
        with pytest.raises(ValidationError, match="(?i)extra"):
            build()
        # And confirm the message identifies the offending field.
        try:
            build()
        except ValidationError as exc:
            assert "unknown_field" in str(exc), (
                f"{name}: ValidationError did not mention the unknown field "
                f"name in message: {exc}"
            )
