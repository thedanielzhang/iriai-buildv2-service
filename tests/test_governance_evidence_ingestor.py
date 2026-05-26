"""Slice 13b -- unit tests for the GovernanceEvidenceIngestor surface.

Covers the doc-13:153-171 ``GovernanceEvidenceIngestor`` ABC + the
``DefaultGovernanceEvidenceIngestor`` pure-typed default implementation
across the 4 invariants × 3 methods called out in the sub-slice contract:

- **Bounded-read invariant** (``LIMIT cap + 1`` split): each method asks
  the underlying bounded reader for at most ``cap + 1`` rows; when the
  +1 sentinel is present, the returned
  :class:`GovernanceEvidenceSet.completeness` is the doc-13a:128-133
  non-``"complete"`` state (``"paged"``) and ``read_budget_exhausted``
  is ``True``; the sentinel row is dropped, never silently truncated.

- **Statement-timeout-forwarded invariant**: the
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceReadBudget`
  ``statement_timeout_ms`` (or the constructor default) is forwarded to
  every bounded-reader call (verified by recording the reader kwargs).

- **`max_chars_per_ref` truncation discipline**: when a per-row text
  field exceeds the cap, the returned
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceRef`
  sets ``preview_only=True`` AND ``completeness="preview_only"``, and
  the corresponding
  :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidencePageRef`
  carries ``completeness="preview_only"`` + ``exact=False`` -- the
  13a ``_exact_completeness_consistency`` +
  ``_preview_only_completeness_consistency`` cross-validators
  (``models.py``) round-trip cleanly through this contract.

- **No-mutation invariant**: a recording-proxy reader fake asserts that
  none of the 3 ingestor methods invoke any write/insert/update/delete
  method on the bounded reader -- the only call observed is the reader
  itself.

Plus a round-trip sanity check that the ``GovernanceEvidenceSet`` the
ingestor returns serialises and re-parses identically (no spurious
truncation in the pydantic round-trip).

Per the governance prompt § "Non-Negotiables" / § "Bounded reads" the
ingestor is read-only; the tests assert that property explicitly.
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import MagicMock

import pytest

from iriai_build_v2.workflows.develop import governance
from iriai_build_v2.workflows.develop.governance import (
    BoundedReader,
    BoundedReadResult,
    CompletenessState,
    DefaultGovernanceEvidenceIngestor,
    EvidenceAuthority,
    GovernanceEvidenceIngestor,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceEvidenceSlice,
    GovernanceReadBudget,
    GovernanceWindow,
)
from iriai_build_v2.workflows.develop.governance import ingestor as ingestor_module


# ── package surface (Slice 13b -- doc-13:178-181 step 2 deliverable) ───────


def test_governance_package_reexports_13b_ingestor_surface() -> None:
    """The package re-exports the 13b ingestor surface in ``__all__``.

    Per the sub-slice 13b contract the package adds 6 new exports:
    ``GovernanceEvidenceIngestor`` (ABC), ``DefaultGovernanceEvidenceIngestor``
    (default impl), ``GovernanceWindow`` + ``GovernanceEvidenceSlice``
    (ABC-signature shapes), and ``BoundedReader`` + ``BoundedReadResult``
    (bounded-reader port).
    """

    expected_13b = {
        "GovernanceEvidenceIngestor",
        "DefaultGovernanceEvidenceIngestor",
        "GovernanceWindow",
        "GovernanceEvidenceSlice",
        "BoundedReader",
        "BoundedReadResult",
    }
    assert expected_13b.issubset(set(governance.__all__))
    for name in expected_13b:
        assert hasattr(governance, name)
        assert hasattr(ingestor_module, name)
        assert getattr(governance, name) is getattr(ingestor_module, name)


# ── ABC contract (doc-13:153-171) ──────────────────────────────────────────


def test_abc_cannot_be_instantiated_directly() -> None:
    """The doc-13:156-171 ``GovernanceEvidenceIngestor`` is abstract.

    Constructing the base class directly MUST raise ``TypeError`` -- this is
    the standard Python ABC contract that proves the abstract methods are
    flagged as such on the class.
    """

    with pytest.raises(TypeError):
        GovernanceEvidenceIngestor()  # type: ignore[abstract]


def test_abc_has_doc_13_three_abstract_methods() -> None:
    """The ABC exposes exactly the doc-13:157-170 three abstract methods
    with the doc-13:155-171 verbatim parameter signatures.

    ``ingest_feature_window`` / ``ingest_implementation_artifacts`` /
    ``resolve_ref`` -- no more, no less. Defence against API drift.

    Slice 13m (P3-13b-3 closure) -- tightened from a method-name
    membership check to a full ``inspect.signature`` audit. For each
    of the 3 doc-13:155-171 abstract methods, this test verifies the
    parameter NAME + parameter KIND (``POSITIONAL_OR_KEYWORD`` vs
    ``KEYWORD_ONLY``) + parameter DEFAULT + parameter ANNOTATION +
    return ANNOTATION, byte-for-byte against the doc-13:155-171
    spec. Rejects any future signature drift (parameter rename,
    default mutation, keyword/positional reshuffle, annotation
    change) at the test surface so a doc-spec-violating change
    fails this test loudly instead of silently shipping.
    """

    expected = {
        "ingest_feature_window",
        "ingest_implementation_artifacts",
        "resolve_ref",
    }
    actual = set(GovernanceEvidenceIngestor.__abstractmethods__)
    assert actual == expected
    # And each is an async def -- doc-13:157, 164, 170 specify ``async``.
    for name in expected:
        method = getattr(GovernanceEvidenceIngestor, name)
        assert inspect.iscoroutinefunction(method), (
            f"GovernanceEvidenceIngestor.{name} must be async per doc-13:157-170"
        )

    # Slice 13m (P3-13b-3 closure) -- ``inspect.signature`` byte-pin for
    # each of the 3 doc-13:155-171 abstract methods. The expected
    # signature schema below mirrors doc-13:155-171 verbatim:
    #
    # * Each method's first parameter is ``self`` (POSITIONAL_OR_KEYWORD,
    #   no default, no annotation -- the implicit method-self convention).
    # * The doc-13:155-171 positional-or-keyword parameters
    #   (``feature_id`` / ``window`` / ``slice_ids`` / ``ref``) appear
    #   BEFORE the ``*`` boundary as POSITIONAL_OR_KEYWORD.
    # * The doc-13:155-171 keyword-only parameters (``budget`` /
    #   ``max_chars``) appear AFTER the ``*`` boundary as KEYWORD_ONLY.
    # * No parameter carries a default value (doc-13:155-171 spec has
    #   no defaults).
    # * Each parameter annotation matches the doc-13:155-171 verbatim
    #   type spec.
    # * The return annotation is doc-13:163 / doc-13:169 / doc-13:170
    #   verbatim.
    PoK = inspect.Parameter.POSITIONAL_OR_KEYWORD
    KO = inspect.Parameter.KEYWORD_ONLY
    EMPTY = inspect.Parameter.empty
    expected_signatures: dict[
        str,
        tuple[list[tuple[str, inspect._ParameterKind, object, object]], object],
    ] = {
        # doc-13:157-163 -- ingest_feature_window(self, feature_id: str,
        # window: GovernanceWindow, *, budget: GovernanceReadBudget)
        # -> GovernanceEvidenceSet
        "ingest_feature_window": (
            [
                ("self", PoK, EMPTY, EMPTY),
                ("feature_id", PoK, EMPTY, str),
                ("window", PoK, EMPTY, GovernanceWindow),
                ("budget", KO, EMPTY, GovernanceReadBudget),
            ],
            GovernanceEvidenceSet,
        ),
        # doc-13:164-169 -- ingest_implementation_artifacts(self,
        # slice_ids: list[str], *, budget: GovernanceReadBudget)
        # -> GovernanceEvidenceSet
        "ingest_implementation_artifacts": (
            [
                ("self", PoK, EMPTY, EMPTY),
                ("slice_ids", PoK, EMPTY, list[str]),
                ("budget", KO, EMPTY, GovernanceReadBudget),
            ],
            GovernanceEvidenceSet,
        ),
        # doc-13:170 -- resolve_ref(self, ref: GovernanceEvidenceRef, *,
        # max_chars: int) -> GovernanceEvidenceSlice
        "resolve_ref": (
            [
                ("self", PoK, EMPTY, EMPTY),
                ("ref", PoK, EMPTY, GovernanceEvidenceRef),
                ("max_chars", KO, EMPTY, int),
            ],
            GovernanceEvidenceSlice,
        ),
    }
    for name, (expected_params, expected_return) in expected_signatures.items():
        method = getattr(GovernanceEvidenceIngestor, name)
        # ``from __future__ import annotations`` in ``ingestor.py`` makes
        # all annotations strings at runtime; ``eval_str=True`` resolves
        # the strings to the actual classes (Python 3.10+) so the
        # equality check below compares classes-to-classes rather than
        # strings-to-classes. The eval'd lookup uses the ingestor
        # module's globals as the source so qualified names like
        # ``GovernanceWindow`` / ``GovernanceReadBudget`` resolve against
        # the names the module actually imports.
        sig = inspect.signature(
            method, eval_str=True, globals=vars(ingestor_module)
        )
        params = list(sig.parameters.values())
        assert len(params) == len(expected_params), (
            f"GovernanceEvidenceIngestor.{name} parameter count drift: "
            f"expected {len(expected_params)}, got {len(params)} "
            f"({[p.name for p in params]})"
        )
        for actual_param, (
            exp_name,
            exp_kind,
            exp_default,
            exp_annotation,
        ) in zip(params, expected_params):
            assert actual_param.name == exp_name, (
                f"GovernanceEvidenceIngestor.{name} parameter name drift: "
                f"expected {exp_name!r}, got {actual_param.name!r}"
            )
            assert actual_param.kind == exp_kind, (
                f"GovernanceEvidenceIngestor.{name}({exp_name}) kind drift: "
                f"expected {exp_kind!r}, got {actual_param.kind!r}"
            )
            assert actual_param.default is exp_default, (
                f"GovernanceEvidenceIngestor.{name}({exp_name}) default "
                f"drift: expected {exp_default!r}, got "
                f"{actual_param.default!r}"
            )
            assert actual_param.annotation == exp_annotation, (
                f"GovernanceEvidenceIngestor.{name}({exp_name}) annotation "
                f"drift: expected {exp_annotation!r}, got "
                f"{actual_param.annotation!r}"
            )
        assert sig.return_annotation == expected_return, (
            f"GovernanceEvidenceIngestor.{name} return annotation drift: "
            f"expected {expected_return!r}, got {sig.return_annotation!r}"
        )


# ── Default impl construction (positive caps required) ─────────────────────


@pytest.mark.parametrize(
    "kwarg",
    ["limit_cap", "statement_timeout_ms", "max_chars_per_ref"],
)
@pytest.mark.parametrize("bad", [0, -1, -1000])
def test_default_impl_rejects_non_positive_constructor_cap(
    kwarg: str, bad: int
) -> None:
    """All three constructor caps MUST be positive integers.

    Doc-13:89-95 + the auto-memory ``feedback_no_silent_degradation`` rule:
    a zero or negative cap silently disables the bounded-read discipline.
    """

    reader = _make_fake_reader([])
    with pytest.raises(ValueError):
        DefaultGovernanceEvidenceIngestor(reader, **{kwarg: bad})


def test_default_impl_uses_doc_13_defaults_when_unspecified() -> None:
    """Constructor defaults match the doc-13:89-95
    :class:`GovernanceReadBudget` defaults so an ingestor created with only
    a reader is still bounded."""

    reader = _make_fake_reader([])
    ingestor = DefaultGovernanceEvidenceIngestor(reader)
    # Mirror the doc-13:89-95 defaults so a later sub-slice that tightens
    # one of these gets a failing test instead of a silent drift.
    assert ingestor._limit_cap == 500
    assert ingestor._statement_timeout_ms == 10_000
    assert ingestor._max_chars_per_ref == 40_000


# ── Bounded-read invariant: LIMIT cap + 1 (doc-13:217-220) ─────────────────


@pytest.mark.asyncio
async def test_ingest_feature_window_reads_cap_plus_one() -> None:
    """``ingest_feature_window`` asks the reader for ``cap + 1`` rows.

    Doc-10 ``_typed_bounded`` precedent at
    ``execution_control/store.py:1449-1458``: the +1 sentinel row is the
    explicit truncation signal. The ingestor MUST pass ``cap + 1`` so the
    reader can surface the sentinel; the ingestor splits at ``cap`` +
    drops the sentinel.
    """

    rows = _summary_rows(count=3)
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, limit_cap=2)
    budget = GovernanceReadBudget(
        max_event_rows=2,
        max_artifact_summary_rows=2,
        max_ref_resolutions=2,
        max_chars_per_ref=4000,
        max_serialized_output_bytes=10_000,
        statement_timeout_ms=1_500,
    )

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    # The reader was called with limit = cap + 1 = 3 (not 2).
    assert reader.calls, "reader was not invoked"
    last_call_kwargs = reader.calls[-1].kwargs
    assert last_call_kwargs["limit"] == 3
    # The +1 sentinel triggered the truncation signal -- only ``cap`` (=2)
    # refs returned (not 3), and the corpus is paged.
    assert len(result.refs) == 2
    assert result.completeness == "paged"
    assert result.read_budget_exhausted is True
    # An exact paged omitted-ref records the cap overflow per doc-13:217-220.
    assert len(result.omitted_refs) == 1
    overflow = result.omitted_refs[0]
    assert overflow.completeness == "paged"
    assert overflow.exact is True
    # And the sentinel row is dropped, not silently absorbed -- the 3rd
    # row's ref_id is absent from the returned refs.
    returned_ids = {ref.ref_id for ref in result.refs}
    assert rows[2]["ref_id"] not in returned_ids


@pytest.mark.asyncio
async def test_ingest_feature_window_complete_when_under_cap() -> None:
    """When the reader returns ``<= cap`` rows, the corpus is ``complete``."""

    rows = _summary_rows(count=2)
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, limit_cap=5)
    budget = GovernanceReadBudget(max_event_rows=5)

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    assert len(result.refs) == 2
    assert result.completeness == "complete"
    assert result.read_budget_exhausted is False
    assert result.omitted_refs == []


@pytest.mark.asyncio
async def test_ingest_implementation_artifacts_returns_parser_based_evidence_set(
    tmp_path,
) -> None:
    """``ingest_implementation_artifacts`` returns a parser-projected
    :class:`GovernanceEvidenceSet` per sub-slice 13f (doc-13:188-200).

    Sub-slice 13f REPLACES the 13b stub reader-based code path with the
    13c+13d parsers + 13e digester. The prior 13b test
    ``test_ingest_implementation_artifacts_reads_cap_plus_one`` (which
    encoded the stub reader ``LIMIT cap+1`` invariant for this method) is
    inherently misaligned with the new contract -- the new method does not
    invoke the reader at all; the parsers read from disk directly. The
    bounded-read invariant for ``ingest_feature_window`` (which still uses
    the reader-based ``_build_evidence_set`` helper) remains pinned by the
    original ``test_ingest_feature_window_reads_cap_plus_one`` and is not
    regressed by 13f. This test verifies the new parser-based path against
    a synthetic fixture pair.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )
    budget = GovernanceReadBudget()

    result = await ingestor.ingest_implementation_artifacts(
        ["13a", "13b"], budget=budget
    )

    # The parser-based path does NOT invoke the bounded reader for the
    # journal/decision-log authorities -- the 13c+13d parsers read from
    # disk directly. Per the sub-slice 13f contract this is intentional:
    # the reader is the typed-journal (Postgres) authority, NOT the
    # journal-file authority.
    assert reader.calls == []
    # And the result is a typed GovernanceEvidenceSet with parser-projected
    # refs (NOT reader-projected rows).
    assert isinstance(result, GovernanceEvidenceSet)
    assert len(result.refs) > 0
    # Every ref's authority is one of the two journal/decision-log
    # authorities the 13e digester projects from anchors.
    for ref in result.refs:
        assert ref.authority in (
            "implementation_journal",
            "implementation_decision_log",
        )
    # Corpus id encodes the slice_ids filter (sub-slice 13f point 1+4).
    assert result.corpus_id == "implementation-artifacts:13a,13b"


@pytest.mark.asyncio
async def test_resolve_ref_reads_cap_plus_one_for_single_row() -> None:
    """``resolve_ref`` asks the reader for ``limit = 2`` (1 + sentinel).

    A duplicate row past the single expected row signals a ref-id
    collision -- the resolve forces ``preview_only=True`` so a downstream
    authoritative consumer cannot silently treat the slice as exact.
    """

    rows = [
        {"body": "the canonical body"},
        {"body": "a duplicate row past the single-row expectation"},
    ]
    reader = _RecordingReader(rows=rows, authority="git_provenance")
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=200)
    ref = _make_ref(authority="git_provenance")

    slice_ = await ingestor.resolve_ref(ref, max_chars=100)

    # The reader is asked for ``limit = 2`` (1 expected + 1 sentinel) so
    # a duplicate row past the single-row expectation is observable.
    assert reader.calls[-1].kwargs["limit"] == 2
    # Duplicate row past the single-row expectation forces preview_only.
    assert slice_.preview_only is True
    # And the embedded page-ref records the preview-only state per the
    # Slice 13A invariant precursor (doc-13a:24, 109-118).
    assert len(slice_.pages) == 1
    assert slice_.pages[0].completeness == "preview_only"
    assert slice_.pages[0].exact is False


# ── Statement-timeout-forwarded invariant (doc-13:95) ──────────────────────


@pytest.mark.asyncio
async def test_ingest_feature_window_forwards_statement_timeout_ms() -> None:
    """The per-call ``statement_timeout_ms`` is CLAMPED DOWN to the
    constructor cap when the budget exceeds the constructor.

    Doc-13:95 + the doc-13 § "Bounded reads" non-negotiable: every read
    forwards ``SET LOCAL statement_timeout``. The per-call
    ``GovernanceReadBudget.statement_timeout_ms`` field IS the caller's
    tightening knob (doc-13:95 lists it on ``GovernanceReadBudget`` not
    on a constructor-only struct); the constructor cap is the ceiling.
    A caller cannot widen the timeout by passing a larger per-call
    budget than the constructor cap -- the clamp mirrors the Slice-10a
    ``_clamp_budget_to_ceiling`` precedent at
    ``workflows/develop/execution/snapshots.py:202-214``.
    """

    reader = _RecordingReader(rows=_summary_rows(count=1))
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader, statement_timeout_ms=2_500
    )
    # Budget exceeds constructor cap -- the constructor cap wins.
    budget = GovernanceReadBudget(statement_timeout_ms=9_999)

    await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    assert reader.calls[-1].kwargs["statement_timeout_ms"] == 2_500


@pytest.mark.asyncio
async def test_ingest_feature_window_honours_smaller_budget_statement_timeout_ms() -> None:
    """A per-call budget BELOW the constructor cap WINS (caller may
    tighten, never widen).

    Doc-13:95 lists ``statement_timeout_ms`` on the
    :class:`GovernanceReadBudget` shape itself, so a caller passing
    ``budget.statement_timeout_ms`` < ``ctor.statement_timeout_ms`` must
    have its tighter value honoured. Clamp-DOWN discipline mirrors
    ``_clamp_budget_to_ceiling`` at
    ``workflows/develop/execution/snapshots.py:202-214``.
    """

    reader = _RecordingReader(rows=_summary_rows(count=1))
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader, statement_timeout_ms=10_000
    )
    # Budget below constructor cap -- the budget value wins.
    budget = GovernanceReadBudget(statement_timeout_ms=1_000)

    await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    assert reader.calls[-1].kwargs["statement_timeout_ms"] == 1_000


@pytest.mark.asyncio
async def test_ingest_implementation_artifacts_propagates_budget_to_evidence_set(
    tmp_path,
) -> None:
    """``ingest_implementation_artifacts`` propagates the per-call
    :class:`GovernanceReadBudget` verbatim onto
    :attr:`GovernanceEvidenceSet.read_budget`.

    Sub-slice 13f REPLACES the 13b stub reader-based code path; the prior
    13b test ``test_ingest_implementation_artifacts_forwards_statement_timeout_ms``
    encoded the stub reader-forwarding invariant for this method which is
    inherently misaligned with the new contract (the new method does not
    invoke the reader). The forwarding invariant for ``ingest_feature_window``
    remains pinned by the original
    ``test_ingest_feature_window_forwards_statement_timeout_ms``.

    The 13f test asserts the new budget-propagation invariant: the
    per-call budget the digester sees via ``read_budget=budget`` is the
    same budget that lands on the returned set's ``read_budget`` field.
    Per the 13e digester contract this is a verbatim copy -- the
    implementation-artifact path has no DB I/O so the budget is not
    consumed; it is preserved for downstream consumers (Slice-15 metrics
    + Slice-16 finding engine) that read the budget to gate further
    queries.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )
    budget = GovernanceReadBudget(
        max_event_rows=123,
        max_artifact_summary_rows=456,
        max_ref_resolutions=7,
        max_chars_per_ref=8_000,
        max_serialized_output_bytes=500_000,
        statement_timeout_ms=2_500,
    )

    result = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=budget
    )

    # Every budget field round-trips verbatim onto the result.
    assert result.read_budget == budget
    assert result.read_budget.max_event_rows == 123
    assert result.read_budget.statement_timeout_ms == 2_500


@pytest.mark.asyncio
async def test_resolve_ref_forwards_statement_timeout_ms() -> None:
    """``resolve_ref`` forwards the same ``statement_timeout_ms``."""

    reader = _RecordingReader(rows=[{"body": "short"}], authority="typed_journal")
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader, statement_timeout_ms=4_125
    )
    ref = _make_ref()

    await ingestor.resolve_ref(ref, max_chars=100)

    assert reader.calls[-1].kwargs["statement_timeout_ms"] == 4_125


# ── max_chars_per_ref truncation discipline (doc-13:93 + doc-13a:24) ──────


@pytest.mark.asyncio
async def test_ingest_feature_window_truncates_oversize_row_to_preview_only() -> None:
    """When a per-row body exceeds ``max_chars_per_ref``, the returned
    ref+page set ``preview_only=True`` + ``completeness="preview_only"``.

    This is the cross-validation invariant the 13a finalizer enforced via
    ``_preview_only_completeness_consistency`` on
    :class:`GovernanceEvidenceRef` and
    ``_exact_completeness_consistency`` on
    :class:`GovernanceEvidencePageRef` (``models.py``, doc-13a:24, 109-118).
    The ingestor must produce shapes that pass both cross-validators.
    """

    long_body = "x" * 5000
    rows = [
        {"ref_id": "row-1", "digest": "sha256:1", "summary": long_body},
    ]
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=100)
    budget = GovernanceReadBudget(max_chars_per_ref=100)

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    assert len(result.refs) == 1
    ref = result.refs[0]
    # The cross-validator at models.py:_preview_only_completeness_consistency
    # requires preview_only + completeness to agree bidirectionally; the
    # ref already exists, so the validator already accepted it -- but assert
    # the invariant explicitly so a regression surfaces here too.
    assert ref.preview_only is True
    assert ref.completeness == "preview_only"
    assert len(ref.page_refs) == 1
    page = ref.page_refs[0]
    # The cross-validator at models.py:_exact_completeness_consistency
    # requires exact=False whenever completeness="preview_only".
    assert page.completeness == "preview_only"
    assert page.exact is False


@pytest.mark.asyncio
async def test_ingest_feature_window_under_max_chars_stays_exact() -> None:
    """A per-row body BELOW ``max_chars_per_ref`` stays
    ``completeness="complete"`` + ``exact=True``.

    Defence against false-firing the truncation contract -- the cross-
    validators must also pass on the legitimate complete-exact case.
    """

    rows = [{"ref_id": "row-1", "digest": "sha256:1", "summary": "short"}]
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=1000)
    budget = GovernanceReadBudget(max_chars_per_ref=1000)

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    ref = result.refs[0]
    assert ref.preview_only is False
    assert ref.completeness == "complete"
    page = ref.page_refs[0]
    assert page.completeness == "complete"
    assert page.exact is True


@pytest.mark.asyncio
async def test_resolve_ref_truncates_body_to_max_chars() -> None:
    """``resolve_ref`` truncates the body at ``max_chars`` and sets
    ``preview_only=True`` + the embedded page-ref's exact=False."""

    long_body = "y" * 2000
    reader = _RecordingReader(
        rows=[{"body": long_body}], authority="typed_journal"
    )
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=500)
    ref = _make_ref()

    slice_ = await ingestor.resolve_ref(ref, max_chars=500)

    assert isinstance(slice_, GovernanceEvidenceSlice)
    assert len(slice_.body) == 500
    assert slice_.truncated_to_chars == 500
    assert slice_.preview_only is True
    page = slice_.pages[0]
    assert page.completeness == "preview_only"
    assert page.exact is False


@pytest.mark.asyncio
async def test_resolve_ref_under_max_chars_stays_exact() -> None:
    """A body BELOW ``max_chars`` stays ``preview_only=False`` + exact=True."""

    reader = _RecordingReader(rows=[{"body": "tiny"}], authority="typed_journal")
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=500)
    ref = _make_ref()

    slice_ = await ingestor.resolve_ref(ref, max_chars=500)

    assert slice_.body == "tiny"
    assert slice_.truncated_to_chars is None
    assert slice_.preview_only is False
    assert slice_.pages[0].completeness == "complete"
    assert slice_.pages[0].exact is True


@pytest.mark.asyncio
async def test_resolve_ref_max_chars_clamped_down_to_constructor_cap() -> None:
    """A caller cannot widen the truncation discipline by passing a larger
    ``max_chars`` than the constructor cap.

    Mirrors the Slice-10a ``_clamp_budget_to_ceiling`` precedent at
    ``workflows/develop/execution/snapshots.py:202-214`` -- a caller may
    request a tighter cap, never a wider one.
    """

    long_body = "z" * 5000
    reader = _RecordingReader(rows=[{"body": long_body}], authority="typed_journal")
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=100)
    ref = _make_ref()

    # Caller asks for 10_000 but the constructor cap is 100 -- effective cap is 100.
    slice_ = await ingestor.resolve_ref(ref, max_chars=10_000)

    assert len(slice_.body) == 100
    assert slice_.truncated_to_chars == 100
    assert slice_.preview_only is True


# ── No-mutation invariant (governance prompt § "Non-Negotiables") ──────────


@pytest.mark.asyncio
async def test_no_mutation_invoked_on_reader_during_ingest_feature_window() -> None:
    """The ingestor invokes ONLY the reader callable -- no
    ``record`` / ``insert`` / ``update`` / ``delete`` / ``commit`` method
    is called on the reader.

    The governance prompt § "Non-Negotiables" mandates analytical /
    advisory / read-only behaviour. A ``MagicMock`` reader with a spec
    that allows arbitrary attribute access would silently swallow a
    spurious write call; the recording-reader fake captures every call
    and the test asserts no method other than ``__call__`` was invoked.
    """

    reader = _RecordingReader(rows=_summary_rows(count=2))
    ingestor = DefaultGovernanceEvidenceIngestor(reader)
    budget = GovernanceReadBudget()

    await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    # Only the reader CALL itself was invoked (no .record / .insert /
    # .update / .delete / .commit / .write attribute access).
    assert reader.attribute_accesses == [], (
        "ingestor must not access any non-call attribute on the reader; "
        f"observed: {reader.attribute_accesses}"
    )


@pytest.mark.asyncio
async def test_no_mutation_invoked_on_reader_during_ingest_implementation_artifacts(
    tmp_path,
) -> None:
    """Same no-mutation invariant for ``ingest_implementation_artifacts``.

    Sub-slice 13f REWIRES this method to use the 13c+13d parsers + 13e
    digester (no reader involvement at all). The no-mutation invariant on
    the reader is now tautologically satisfied -- the method does not even
    access the reader -- but the test setup is updated to pass
    ``journal_path`` + ``decisions_path`` so the new method body executes
    successfully. The invariant claim (no write attribute access on the
    reader) is unchanged from 13b.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=_summary_rows(count=1))
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )

    assert reader.attribute_accesses == []
    # And under the 13f wiring the reader itself is also not invoked --
    # the parsers read from disk directly. This is a strengthening of the
    # 13b invariant: no method call AND no attribute access on the reader.
    assert reader.calls == []


@pytest.mark.asyncio
async def test_no_mutation_invoked_on_reader_during_resolve_ref() -> None:
    """Same no-mutation invariant for ``resolve_ref``."""

    reader = _RecordingReader(rows=[{"body": "ok"}], authority="typed_journal")
    ingestor = DefaultGovernanceEvidenceIngestor(reader)

    await ingestor.resolve_ref(_make_ref(), max_chars=100)

    assert reader.attribute_accesses == []


@pytest.mark.asyncio
async def test_no_mutation_via_magicmock_spec_inspection() -> None:
    """A second, MagicMock-based assertion: the only attribute the
    ingestor accesses on the reader is the call itself.

    Belt-and-suspenders with the recording-reader fake above -- if the
    recording fake misses a write path, the MagicMock's
    ``method_calls`` log catches it.
    """

    # MagicMock that returns a BoundedReadResult when called as a function.
    reader = MagicMock(spec=BoundedReader)
    reader.return_value = BoundedReadResult(
        rows=_summary_rows(count=1), authority="typed_journal"
    )
    ingestor = DefaultGovernanceEvidenceIngestor(reader)

    await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=GovernanceReadBudget()
    )

    # The MagicMock recorded a single call (the reader invocation itself).
    assert reader.call_count == 1
    # And no method calls beyond the call itself -- spec=BoundedReader
    # restricts the attribute surface, but we double-check the
    # ``method_calls`` log too.
    assert reader.method_calls == []


# ── Round-trip (doc-13:128-141 + doc-13a:24) ──────────────────────────────


@pytest.mark.asyncio
async def test_ingest_feature_window_round_trip_clean() -> None:
    """The returned :class:`GovernanceEvidenceSet` round-trips
    ``model_dump_json`` -> ``model_validate_json`` identically.

    The 13a cross-validators at ``models.py`` run on
    ``model_validate_json`` too, so a successful round-trip proves the
    ingestor never produced a shape that violates the 13a invariants.
    """

    rows = _summary_rows(count=2)
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, limit_cap=5)

    result = await ingestor.ingest_feature_window(
        "feat-1",
        GovernanceWindow(start_iso="2026-05-24T00:00:00Z"),
        budget=GovernanceReadBudget(),
    )

    payload = result.model_dump_json()
    rebuilt = GovernanceEvidenceSet.model_validate_json(payload)
    assert rebuilt == result
    # And the rebuilt payload itself is byte-identical -- defence against
    # field-ordering / null-pruning drift.
    assert rebuilt.model_dump_json() == payload


@pytest.mark.asyncio
async def test_ingest_with_truncation_round_trip_clean() -> None:
    """A truncated (``preview_only``) corpus also round-trips cleanly -- the
    cross-validators do NOT false-fire on the legitimate truncation case.
    """

    long_body = "q" * 1000
    rows = [
        {"ref_id": "row-1", "digest": "sha256:row-1", "summary": long_body},
    ]
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, max_chars_per_ref=100)

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=GovernanceReadBudget(max_chars_per_ref=100)
    )

    payload = result.model_dump_json()
    rebuilt = GovernanceEvidenceSet.model_validate_json(payload)
    assert rebuilt == result


# ── Sync + async reader (doc-13 doesn't constrain which) ───────────────────


@pytest.mark.asyncio
async def test_default_impl_accepts_sync_reader() -> None:
    """A synchronous reader callable is accepted (doc-13:153-171 doesn't
    constrain reader sync/async).

    Lets test fakes stay plain functions while a production reader can be
    an asyncpg-backed coroutine. ``inspect.isawaitable`` is the dispatcher.
    """

    def sync_reader(
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        return BoundedReadResult(
            rows=_summary_rows(count=1, authority=authority),
            authority=authority,
        )

    ingestor = DefaultGovernanceEvidenceIngestor(sync_reader)
    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=GovernanceReadBudget()
    )
    assert len(result.refs) == 1


@pytest.mark.asyncio
async def test_default_impl_accepts_async_reader() -> None:
    """An async reader callable is accepted -- mirrors the production
    asyncpg-backed reader shape."""

    async def async_reader(
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        return BoundedReadResult(
            rows=_summary_rows(count=1, authority=authority),
            authority=authority,
        )

    ingestor = DefaultGovernanceEvidenceIngestor(async_reader)
    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=GovernanceReadBudget()
    )
    assert len(result.refs) == 1


@pytest.mark.asyncio
async def test_default_impl_rejects_non_bounded_read_result_from_reader() -> None:
    """Defence in depth: a misbehaving reader that returns something other
    than a ``BoundedReadResult`` raises ``TypeError`` -- no silent
    coercion."""

    def bad_reader(*args: Any, **kwargs: Any) -> Any:
        return {"rows": [], "authority": "typed_journal"}  # dict, not the model

    ingestor = DefaultGovernanceEvidenceIngestor(bad_reader)
    with pytest.raises(TypeError, match="BoundedReadResult"):
        await ingestor.ingest_feature_window(
            "feat-1", GovernanceWindow(), budget=GovernanceReadBudget()
        )


@pytest.mark.asyncio
async def test_resolve_ref_rejects_non_positive_max_chars() -> None:
    """``resolve_ref`` rejects ``max_chars <= 0`` -- no silent degradation.

    Converted from the deprecated
    ``asyncio.get_event_loop().run_until_complete()`` pattern (carry
    P3-13b-2) to ``@pytest.mark.asyncio`` by the Slice 13i finalizer.
    The deprecated pattern broke when interleaved with the new async
    test surface from the Slice 13i finalizer (P2-13i-1) -- pytest-
    asyncio's STRICT mode does not leave a current event loop after
    its async tests, so ``asyncio.get_event_loop()`` raised
    ``RuntimeError: There is no current event loop in thread`` on the
    very first non-async test that ran after the new in-memory store
    async tests. Closes carry P3-13b-2.
    """

    reader = _make_fake_reader([])
    ingestor = DefaultGovernanceEvidenceIngestor(reader)
    with pytest.raises(ValueError):
        await ingestor.resolve_ref(_make_ref(), max_chars=0)


# ── Sub-slice 13f -- parser-based ingest_implementation_artifacts wiring ──
#
# Doc-13:188-200 + STATUS.md § "Next safe action" 8-point chunk shape. The
# 13f wiring replaces the 13b stub reader-based code path for
# ``ingest_implementation_artifacts`` with the 13c+13d parsers + 13e
# digester. The tests below pin the new contract.


@pytest.mark.asyncio
async def test_13f_happy_path_on_synthetic_fixtures(tmp_path) -> None:
    """End-to-end happy path on synthetic journal + JSONL fixtures.

    Per sub-slice 13f point 1 (doc-13:188-200) the rewired method reads the
    13c+13d parsers' output and composes via
    :func:`compose_governance_evidence_set`. The returned set carries
    parser-projected refs (NOT reader-projected rows); the digester's
    set-level invariants (completeness / quality / idempotency_key) hold by
    construction.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The result is a fully populated GovernanceEvidenceSet -- not the
    # 13b stub's reader-bounded shape.
    assert isinstance(result, GovernanceEvidenceSet)
    # The digester's set-level idempotency_key is a 64-char SHA-256 hex.
    assert len(result.idempotency_key) == 64
    assert all(c in "0123456789abcdef" for c in result.idempotency_key)
    # Every parser-emitted authority survived dedup.
    authorities = {ref.authority for ref in result.refs}
    assert "implementation_journal" in authorities
    assert "implementation_decision_log" in authorities
    # Sub-slice 13f point 5 -- the budget is propagated verbatim.
    assert result.read_budget == GovernanceReadBudget()
    # The corpus id encodes the empty filter as the ``*`` wildcard.
    assert result.corpus_id == "implementation-artifacts:*"


@pytest.mark.asyncio
async def test_13f_fail_closed_when_journal_path_unset(tmp_path) -> None:
    """``ingest_implementation_artifacts`` raises ``ValueError`` when
    ``journal_path`` is not configured at construction time.

    Per the auto-memory ``feedback_no_silent_degradation`` rule + sub-slice
    13f point 2: an unconfigured path is a hard configuration error, NOT
    an "empty evidence set" recoverable state.
    """

    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text("", encoding="utf-8")
    reader = _make_fake_reader([])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        # journal_path deliberately omitted.
        decisions_path=decisions_path,
    )

    with pytest.raises(ValueError, match="journal_path"):
        await ingestor.ingest_implementation_artifacts(
            ["13a"], budget=GovernanceReadBudget()
        )


@pytest.mark.asyncio
async def test_13f_fail_closed_when_decisions_path_unset(tmp_path) -> None:
    """``ingest_implementation_artifacts`` raises ``ValueError`` when
    ``decisions_path`` is not configured at construction time."""

    journal_path = tmp_path / "journal.md"
    journal_path.write_text("", encoding="utf-8")
    reader = _make_fake_reader([])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        # decisions_path deliberately omitted.
    )

    with pytest.raises(ValueError, match="decisions_path"):
        await ingestor.ingest_implementation_artifacts(
            ["13a"], budget=GovernanceReadBudget()
        )


@pytest.mark.asyncio
async def test_13f_fail_closed_when_journal_path_missing_on_disk(
    tmp_path,
) -> None:
    """``ingest_implementation_artifacts`` raises ``ValueError`` when the
    configured ``journal_path`` does not exist on disk.

    Per doc-13:207-208 ("Missing implementation journal: mark the
    evidence set ``insufficient`` and block governance acceptance") +
    ``feedback_no_silent_degradation``. The 13f wiring fails LOUDER than
    the doc spec (typed ValueError) so the caller cannot proceed with a
    misconfigured ingestor; a later sub-slice that needs the "marked
    insufficient" semantics can catch the ValueError and project it.
    """

    journal_path = tmp_path / "does-not-exist.md"  # NOT created.
    decisions_path = tmp_path / "decisions.jsonl"
    decisions_path.write_text("", encoding="utf-8")
    reader = _make_fake_reader([])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    with pytest.raises(ValueError, match="journal_path"):
        await ingestor.ingest_implementation_artifacts(
            ["13a"], budget=GovernanceReadBudget()
        )


@pytest.mark.asyncio
async def test_13f_fail_closed_when_decisions_path_missing_on_disk(
    tmp_path,
) -> None:
    """``ingest_implementation_artifacts`` raises ``ValueError`` when the
    configured ``decisions_path`` does not exist on disk.

    Per doc-13:209-210 ("Malformed JSONL decision row: record a
    ``governance_evidence_gap`` finding") + ``feedback_no_silent_degradation``.
    """

    journal_path = tmp_path / "journal.md"
    journal_path.write_text("", encoding="utf-8")
    decisions_path = tmp_path / "does-not-exist.jsonl"  # NOT created.
    reader = _make_fake_reader([])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    with pytest.raises(ValueError, match="decisions_path"):
        await ingestor.ingest_implementation_artifacts(
            ["13a"], budget=GovernanceReadBudget()
        )


@pytest.mark.asyncio
async def test_13f_slice_ids_filter_restricts_anchors(tmp_path) -> None:
    """Per sub-slice 13f point 3 the ``slice_ids`` filter is deterministic:
    a non-empty list selects exactly those slices.

    The synthetic fixtures span ``13a``, ``13b``, ``13c``. Filtering for
    ``["13a"]`` MUST drop the 13b + 13c anchors from the result.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result_filtered = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )
    result_unfiltered = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # Per-ref dedup discipline applies, so the strict subset relation is
    # over (authority, ref_id) tuples.
    filtered_keys = {(r.authority, r.ref_id) for r in result_filtered.refs}
    unfiltered_keys = {(r.authority, r.ref_id) for r in result_unfiltered.refs}
    # Filtered is a strict subset (the fixture has 13b + 13c anchors that
    # the filter must drop).
    assert filtered_keys < unfiltered_keys
    # And every filtered ref's slice_id is exactly "13a" (the parsers
    # populate the ref slice_id from the anchor slice_id via the 13e
    # _anchor_to_ref projection at evidence_set.py:732-742).
    for ref in result_filtered.refs:
        assert ref.slice_id == "13a"


@pytest.mark.asyncio
async def test_13f_empty_slice_ids_includes_all_anchors(tmp_path) -> None:
    """Per sub-slice 13f point 3 an empty ``slice_ids=[]`` list means
    "all slices" (no filter).

    Symmetric to the prior filtering test: the unfiltered call surfaces
    every parser-emitted slice.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    slices = {ref.slice_id for ref in result.refs}
    # The synthetic fixture covers 13a, 13b, 13c; the empty filter
    # surfaces all three.
    assert "13a" in slices
    assert "13b" in slices
    assert "13c" in slices


@pytest.mark.asyncio
async def test_13f_idempotency_key_invariant_across_repeat_calls(
    tmp_path,
) -> None:
    """Per sub-slice 13f point 1+5 (which leans on the 13e digester's
    canonical-sort-then-dedup discipline at
    ``evidence_set.py:_canonical_sort_refs``): two calls with the same
    input on the same ingestor produce the same
    :attr:`GovernanceEvidenceSet.idempotency_key`.

    The 13e digester's idempotency_key is invariant under input
    reordering by construction; the 13f wiring inherits the invariant
    because two reads of the same files via the same parsers produce
    identical anchor lists.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result_a = await ingestor.ingest_implementation_artifacts(
        ["13a", "13b"], budget=GovernanceReadBudget()
    )
    result_b = await ingestor.ingest_implementation_artifacts(
        ["13a", "13b"], budget=GovernanceReadBudget()
    )

    # Two calls -> same idempotency_key -> same set-level content digest.
    assert result_a.idempotency_key == result_b.idempotency_key
    # And the ref count + every (authority, ref_id) pair matches.
    assert len(result_a.refs) == len(result_b.refs)
    a_keys = sorted((r.authority, r.ref_id) for r in result_a.refs)
    b_keys = sorted((r.authority, r.ref_id) for r in result_b.refs)
    assert a_keys == b_keys


@pytest.mark.asyncio
async def test_13f_source_window_projection_carries_slice_ids(
    tmp_path,
) -> None:
    """Per sub-slice 13f point 4 the :class:`GovernanceWindow` projection
    carries ``selectors={"slice_ids": list(slice_ids)}``; the 13e digester
    surfaces this on :attr:`GovernanceEvidenceSet.source_window`.

    The source_window dict is the typed cite for "what slice_ids did this
    set scope to?" -- a downstream consumer can read it without re-running
    the ingest.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result = await ingestor.ingest_implementation_artifacts(
        ["13a", "13c"], budget=GovernanceReadBudget()
    )

    assert "selectors" in result.source_window
    assert result.source_window["selectors"]["slice_ids"] == ["13a", "13c"]
    # The journal-anchor lane has no time-window contract, so the
    # start/end fields are None per the 13f projection.
    assert result.source_window["start_iso"] is None
    assert result.source_window["end_iso"] is None
    assert result.source_window["start_cursor"] is None
    assert result.source_window["end_cursor"] is None


@pytest.mark.asyncio
async def test_13f_round_trip_through_model_dump_json(tmp_path) -> None:
    """The 13f-emitted :class:`GovernanceEvidenceSet` round-trips
    ``model_dump_json`` -> ``model_validate_json`` cleanly.

    The 13a + 13e cross-validators run on ``model_validate_json``; a
    successful round-trip proves the 13f wiring produces a shape that
    satisfies every constraint at the typed-surface boundary.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )

    payload = result.model_dump_json()
    rebuilt = GovernanceEvidenceSet.model_validate_json(payload)
    assert rebuilt == result
    # Byte-identical re-serialisation (defence against field-ordering
    # drift) -- the same set of fields in the same canonical order.
    assert rebuilt.model_dump_json() == payload


@pytest.mark.asyncio
async def test_13f_real_corpus_smoke_test() -> None:
    """End-to-end smoke test on the live ``docs/execution-control-plane/{
    implementation-journal.md, implementation-decisions.jsonl}``.

    Skipped only when the live fixtures are absent. This is the most
    important test of the 13f wiring: it proves the parsers + digester +
    ingestor compose end-to-end on the real implementation logs (~38k
    journal lines + ~1.1k decision-log rows). The 13e finalizer's
    real-corpus spot-check verified the digester surface; this test
    extends the verification to the full ingestor surface.
    """

    repo_root = _find_repo_root()
    journal_path = (
        repo_root
        / "docs"
        / "execution-control-plane"
        / "implementation-journal.md"
    )
    decisions_path = (
        repo_root
        / "docs"
        / "execution-control-plane"
        / "implementation-decisions.jsonl"
    )
    if not journal_path.exists() or not decisions_path.exists():
        pytest.skip(
            "live implementation-journal.md / implementation-decisions.jsonl "
            "fixtures not present at the canonical repo path"
        )

    reader = _RecordingReader(rows=[])
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The live corpus has many anchors; assert the set is non-empty and
    # the idempotency_key is a valid SHA-256 hex.
    assert len(result.refs) > 100
    assert len(result.idempotency_key) == 64
    # Round-trip on the live corpus too -- a malformed real-row would
    # surface here as a model_validate_json failure.
    payload = result.model_dump_json()
    GovernanceEvidenceSet.model_validate_json(payload)


@pytest.mark.asyncio
async def test_13f_constructor_accepts_str_path_or_path_object(
    tmp_path,
) -> None:
    """The ``journal_path`` / ``decisions_path`` constructor kwargs accept
    both ``str`` and ``pathlib.Path`` inputs.

    The 13f wiring coerces ``str`` -> ``Path`` at construction time so the
    downstream parser sees a uniform type. Tests can pass either form
    without worrying about coercion at the call site.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(rows=[])
    # Pass as str on purpose.
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=str(journal_path),
        decisions_path=str(decisions_path),
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    assert len(result.refs) > 0


@pytest.mark.asyncio
async def test_13f_reader_untouched_during_implementation_artifacts(
    tmp_path,
) -> None:
    """Belt-and-suspenders: the 13f wiring's ``ingest_implementation_artifacts``
    NEVER invokes the bounded reader (the parsers read from disk directly).

    The reader is the typed-journal (Postgres) authority; the
    implementation-artifact path uses the 13c+13d parsers as the journal-file
    authorities per the sub-slice 13f contract. This test is the explicit
    pin against a future regression that reintroduces a reader call on
    this path.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    reader = _RecordingReader(
        rows=[{"ref_id": "should-never-be-read", "summary": "x"}]
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # Reader was never invoked.
    assert reader.calls == []
    # And no ref carries the would-be reader row's id (defence-in-depth).
    for ref in result.refs:
        assert ref.ref_id != "should-never-be-read"


def _find_repo_root() -> Any:
    """Locate the repo root via the canonical
    ``docs/execution-control-plane/`` marker.

    Mirrors the 13c/13d test-suite pattern for resolving the live journal
    + decision-log fixtures without hardcoding an absolute path that
    would break on a different developer's machine.
    """

    from pathlib import Path

    current = Path(__file__).resolve()
    for parent in [current.parent, *current.parents]:
        if (parent / "docs" / "execution-control-plane").is_dir():
            return parent
    raise RuntimeError(
        "cannot locate repo root from test_governance_evidence_ingestor.py"
    )


# ── helpers ────────────────────────────────────────────────────────────────


def _write_synthetic_fixtures(tmp_path) -> tuple[Any, Any]:
    """Write a minimal but exercises-the-grammar pair of journal + JSONL
    fixtures to ``tmp_path`` and return ``(journal_path, decisions_path)``.

    The journal fixture covers the 13c parser's heading + bullet + finding
    + test-result + subagent grammars across three slice ids (``13a`` /
    ``13b`` / ``13c``) so the ``slice_ids`` filter has visible boundaries.
    The decisions fixture covers the 13d parser's stage->event mapping
    (``implementer_before`` -> ``starting``; ``implementer_after`` ->
    ``complete``) plus an ``ACCEPTED`` summary keyword that exercises the
    13d ACCEPTED-in-summary heuristic.

    Returns ``(journal_path, decisions_path)`` as ``pathlib.Path`` objects
    so callers can pass them to ``DefaultGovernanceEvidenceIngestor(...,
    journal_path=..., decisions_path=...)``.
    """

    journal_path = tmp_path / "implementation-journal.md"
    decisions_path = tmp_path / "implementation-decisions.jsonl"

    # 13c parser grammar (per journal_parser.py):
    # - Heading shape A: ``## YYYY-MM-DD -- Slice <id> STATE [...]``
    # - Bullet section: anything indented as ``- ``
    # - Finding id: ``P[123]-<slice>-<n>`` (P1-13a-1 / P3-13b-2)
    # - Test result: ``N passed`` anywhere in a line
    # - Subagent UUID: ``019eXXXX-XXXX-XXXX-XXXX-XXXXXXXXXXXX``
    journal_path.write_text(
        "# Test fixture for the 13f wiring smoke test\n"
        "\n"
        "## 2026-05-24 -- Slice 13a STARTING\n"
        "\n"
        "- Initial scope: pure typed-model scaffolding.\n"
        "- Subagent 019e1234-5678-9abc-def0-123456789abc dispatched.\n"
        "- Test result line: 75 passed in 0.10s.\n"
        "\n"
        "## 2026-05-24 -- Slice 13a ACCEPTED\n"
        "\n"
        "- All gates green; 0 P1 / 0 P2.\n"
        "- Carried P3-13a-3 (validator name collision -- benign).\n"
        "\n"
        "## 2026-05-24 -- Slice 13b STARTING\n"
        "\n"
        "- Ingestor skeleton + bounded readers.\n"
        "- P1-13b-1 raised; remediated in-iteration.\n"
        "\n"
        "## 2026-05-24 -- Slice 13c COMPLETE\n"
        "\n"
        "- Markdown parser landed.\n"
        "- 37 passed for the 13c targeted tests.\n",
        encoding="utf-8",
    )

    # 13d parser grammar (per decision_log_parser.py):
    # - One JSON object per line; blank lines skipped.
    # - ``slice_id`` from ``sub_slice`` (preferred) else ``slice``.
    # - ``stage`` ending ``_before`` -> ``starting``.
    # - ``stage`` ending ``_after`` -> ``complete``.
    # - ``ACCEPTED`` token in ``summary`` upgrades to ``event="accepted"``.
    import json as _json
    rows = [
        {
            "timestamp": "2026-05-24T00:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13a",
            "stage": "implementer_before",
            "event": "implementer_before",
            "summary": "Slice 13a STARTING -- implementer BEFORE.",
        },
        {
            "timestamp": "2026-05-24T01:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13a",
            "stage": "implementer_after",
            "event": "implementer_after",
            "summary": "Slice 13a COMPLETE -- implementer AFTER. ACCEPTED.",
        },
        {
            "timestamp": "2026-05-24T02:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13b",
            "stage": "implementer_before",
            "event": "implementer_before",
            "summary": "Slice 13b STARTING.",
        },
        {
            "timestamp": "2026-05-24T03:00:00Z",
            "phase": "governance-layer",
            "slice": "13-governance-evidence-model",
            "sub_slice": "13c",
            "stage": "finalizer_after",
            "event": "finalizer_after",
            "summary": "Slice 13c finalizer ACCEPTED. Carry P3-13c-1.",
        },
    ]
    decisions_path.write_text(
        "\n".join(_json.dumps(row) for row in rows) + "\n",
        encoding="utf-8",
    )

    return journal_path, decisions_path


def _summary_rows(
    count: int, authority: EvidenceAuthority = "typed_journal"
) -> list[dict[str, Any]]:
    """Build ``count`` summary rows the recording-reader returns.

    Each row mirrors the SUMMARY-only column shape doc-13 § "Bounded reads"
    expects (ids / digests / counts / bounded samples / citations -- no
    artifact bodies).
    """

    return [
        {
            "ref_id": f"row-{i}",
            "id": i,
            "digest": f"sha256:row-{i}",
            "summary": f"summary for row {i}",
            "authority": authority,
        }
        for i in range(count)
    ]


def _make_ref(
    authority: EvidenceAuthority = "typed_journal",
) -> GovernanceEvidenceRef:
    return GovernanceEvidenceRef(
        authority=authority,
        ref_id="ref-under-test",
        digest="sha256:ref-under-test",
        quality="canonical",
        completeness="complete",
    )


class _ReaderCall:
    """Captured (args, kwargs) tuple for one bounded-reader invocation."""

    __slots__ = ("args", "kwargs")

    def __init__(self, args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        self.args = args
        self.kwargs = kwargs


class _RecordingReader:
    """A read-only bounded-reader fake that captures every invocation.

    Records:
    - every ``__call__`` (args + kwargs) so tests can assert the
      ingestor passed ``limit = cap + 1`` and ``statement_timeout_ms``
      correctly;
    - every NON-call attribute access so the no-mutation invariant test
      can assert the ingestor never touched a write-shaped method
      (e.g. ``.record`` / ``.insert`` / ``.commit``).

    The class deliberately does NOT define any other attribute -- a
    governance ingestor that tries to call ``reader.record(...)`` will
    trip the ``__getattr__`` recording first, so the no-mutation test
    surfaces the violation explicitly.
    """

    def __init__(
        self,
        rows: list[dict[str, Any]],
        authority: EvidenceAuthority = "typed_journal",
    ) -> None:
        # Underscore-prefixed names are stored via ``object.__setattr__``
        # so they bypass the ``__getattr__`` instrumentation.
        object.__setattr__(self, "_rows", list(rows))
        object.__setattr__(self, "_authority", authority)
        object.__setattr__(self, "calls", [])
        object.__setattr__(self, "attribute_accesses", [])

    def __call__(
        self,
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        # Record the call so tests can inspect the kwargs.
        self.calls.append(
            _ReaderCall(
                args=(authority,),
                kwargs={
                    "limit": limit,
                    "statement_timeout_ms": statement_timeout_ms,
                    "selectors": dict(selectors),
                },
            )
        )
        return BoundedReadResult(rows=list(self._rows), authority=self._authority)

    def __getattr__(self, name: str) -> Any:
        # Underscore-prefixed and known public names bypass this; anything
        # else (an ingestor attempting reader.record / reader.insert / etc.)
        # is recorded and raises AttributeError so the no-mutation test
        # fails loudly.
        # NOTE: ``__getattr__`` is only invoked when normal attribute
        # lookup fails, so this fires exclusively on undefined attributes.
        self.attribute_accesses.append(name)
        raise AttributeError(
            f"_RecordingReader has no attribute {name!r}; the governance "
            "ingestor is read-only and must not invoke any write/insert/"
            "update/delete/commit method on the bounded reader."
        )


def _make_fake_reader(rows: list[dict[str, Any]]) -> BoundedReader:
    """A minimal synchronous reader closure for tests that only exercise
    constructor validation (no reader invocations expected)."""

    def reader(
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        return BoundedReadResult(rows=list(rows), authority=authority)

    return reader


# ── Sub-slice 13h -- supervisor digest + resource snapshot bounded-reader lanes
#
# Doc-13:178-181 step 2 verbatim ("Add bounded readers over typed journal
# summaries, compatibility projection summaries, supervisor digests, resource
# snapshots, and implementation logs") + doc-13:80-81 (EvidenceAuthority enum
# members ``supervisor_digest`` + ``resource_snapshot``). The 13h sub-slice
# closes the 13f P3-13f-3 carry: the ``supervisor_digest_refs`` /
# ``resource_snapshot_refs`` lanes of ``compose_governance_evidence_set`` were
# receiving empty lists on the implementation-artifact path since 13f; 13h
# adds the two NEW ``BoundedReader`` injection points that populate them.
#
# Decision (per implementer journal BEFORE entry): **optional readers, empty-
# default**. Doc-13:207-208 fail-closed semantics apply specifically to the
# implementation journal (already 13f-fail-closed); supervisor digest +
# resource snapshot are AUGMENTING authorities -- when the reader is unset
# the lane stays empty (13f baseline preserved). When configured, the
# bounded-read invariant fires: ``min(budget.max_ref_resolutions, ctor_cap)``
# (doc-13:92) per ref since each yielded row IS a ref. Overflow surfaces
# as ``read_budget_exhausted=True`` + an exact ``omitted_refs`` page-ref
# per doc-13:215-220.


def _make_supervisor_digest_rows(
    count: int, *, slice_id: str | None = "13a"
) -> list[dict[str, Any]]:
    """Build ``count`` synthetic supervisor-digest rows matching the Slice 10
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.SupervisorDigest`
    surface (``feature_id`` / ``group_idx`` / ``snapshot_version`` /
    ``classification`` / ``confidence`` / ``recommended_action`` /
    ``slack_dedupe_key``) projected into the bounded-reader row shape the
    13b :class:`BoundedReadResult` exposes -- ``list[dict[str, Any]]``.

    Each row carries:
    - ``ref_id``: a unique deterministic id (the supervisor's
      ``slack_dedupe_key`` is the natural stable cite per
      ``workflows/develop/execution/snapshots.py:538``);
    - ``digest``: the doc-13:97-111 per-ref content digest (precursor to
      the Slice 13A invariant);
    - ``feature_id`` / ``slice_id``: optional 13a-typed cross-cites;
    - ``summary``: a short bounded text the 13b ``_project_row_to_ref``
      projects into the per-row page-ref (doc-13:97-111).

    The 13b ``_project_row_to_ref`` helper accepts any authority value and
    projects the same generic field set, so the supervisor-digest rows do
    NOT need a custom projector -- they just need the standard fields.
    """

    return [
        {
            "ref_id": f"supervisor-digest:row-{i}",
            "id": i,
            "digest": f"sha256:supervisor-digest:row-{i}",
            "summary": f"supervisor digest row {i}",
            "feature_id": "feat-13h",
            "slice_id": slice_id,
            # Mirrors the Slice 10 SupervisorDigest shape at
            # workflows/develop/execution/snapshots.py:516-547. The bounded
            # reader's row shape is a flat dict; the typed validators run
            # at the SupervisorDigest construction site, not on the bounded-
            # reader row.
            "snapshot_version": f"snapshot-v{i}",
            "classification": "ok",
            "confidence": 0.95,
        }
        for i in range(count)
    ]


def _make_resource_snapshot_rows(
    count: int, *, slice_id: str | None = "13a"
) -> list[dict[str, Any]]:
    """Build ``count`` synthetic resource-snapshot rows.

    No typed ``ResourceSnapshot`` class exists in Slice 10 today; the
    bounded reader's row shape is therefore the generic doc-13:97-111
    field set (ref_id / digest / summary / feature_id / slice_id) plus the
    natural resource-snapshot fields (cpu_percent / mem_percent / observed_at
    mirroring ``supervisor/models.py:StaleCodexInvocation:180-186`` --
    those are the closest typed resource fields in the Slice 10 surface).
    """

    return [
        {
            "ref_id": f"resource-snapshot:row-{i}",
            "id": i,
            "digest": f"sha256:resource-snapshot:row-{i}",
            "summary": f"resource snapshot row {i}",
            "feature_id": "feat-13h",
            "slice_id": slice_id,
            "cpu_percent": 0.1 * i,
            "mem_percent": 0.05 * i,
        }
        for i in range(count)
    ]


def _make_synthetic_supervisor_digest_reader(
    rows: list[dict[str, Any]],
) -> _RecordingReader:
    """A :class:`_RecordingReader` pre-loaded with the synthetic supervisor-
    digest rows + the ``"supervisor_digest"`` authority pin."""

    return _RecordingReader(rows=rows, authority="supervisor_digest")


def _make_synthetic_resource_snapshot_reader(
    rows: list[dict[str, Any]],
) -> _RecordingReader:
    """A :class:`_RecordingReader` pre-loaded with the synthetic resource-
    snapshot rows + the ``"resource_snapshot"`` authority pin."""

    return _RecordingReader(rows=rows, authority="resource_snapshot")


# ── 13h: constructor accepts the two new kwargs ────────────────────────────


def test_13h_constructor_accepts_supervisor_digest_and_resource_snapshot_readers() -> None:
    """Per sub-slice 13h point 1 the constructor accepts two new
    optional ``BoundedReader`` kwargs:
    ``supervisor_digest_reader`` and ``resource_snapshot_reader``.

    Mirrors the 13b ``BoundedReader`` protocol at
    ``ingestor.py:186-212``; both default to ``None`` so an unconfigured
    ingestor still constructs successfully (13b/13f baseline preserved).
    """

    primary_reader = _make_fake_reader([])
    supervisor_reader = _make_fake_reader([])
    resource_reader = _make_fake_reader([])

    # Both kwargs supplied -- the constructor accepts without error.
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
    )
    assert ingestor._supervisor_digest_reader is supervisor_reader
    assert ingestor._resource_snapshot_reader is resource_reader

    # Either or both omitted -- defaults to None per the empty-default
    # decision (doc-13:207-208 fail-closed semantics apply to journal_path
    # only; supervisor_digest + resource_snapshot are augmenting).
    ingestor_no_kwargs = DefaultGovernanceEvidenceIngestor(primary_reader)
    assert ingestor_no_kwargs._supervisor_digest_reader is None
    assert ingestor_no_kwargs._resource_snapshot_reader is None

    ingestor_only_supervisor = DefaultGovernanceEvidenceIngestor(
        primary_reader, supervisor_digest_reader=supervisor_reader
    )
    assert ingestor_only_supervisor._supervisor_digest_reader is supervisor_reader
    assert ingestor_only_supervisor._resource_snapshot_reader is None


# ── 13h: both readers populate the new lanes end-to-end ────────────────────


@pytest.mark.asyncio
async def test_13h_ingest_implementation_artifacts_populates_supervisor_digest_lane(
    tmp_path,
) -> None:
    """Per sub-slice 13h point 2: when the ``supervisor_digest_reader`` is
    configured, the composed evidence set's ``refs`` list includes
    ``authority="supervisor_digest"`` refs.

    The 13f wiring's ``compose_governance_evidence_set`` call previously
    passed ``supervisor_digest_refs=[]`` verbatim; 13h replaces that with
    the bounded-reader output. The reader-yielded rows are projected via
    the existing ``_project_row_to_ref`` helper which preserves the
    per-row ``ref_id`` / ``digest`` / ``feature_id`` / ``slice_id``.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_rows = _make_supervisor_digest_rows(count=3)
    supervisor_reader = _make_synthetic_supervisor_digest_reader(supervisor_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The supervisor reader WAS invoked (the primary reader was NOT --
    # 13f wiring preserved).
    assert supervisor_reader.calls, "supervisor_digest_reader was not invoked"
    assert primary_reader.calls == []
    # And the composed set includes supervisor_digest refs.
    supervisor_refs = [
        ref for ref in result.refs if ref.authority == "supervisor_digest"
    ]
    assert len(supervisor_refs) == 3
    supervisor_ref_ids = {ref.ref_id for ref in supervisor_refs}
    assert supervisor_ref_ids == {
        "supervisor-digest:row-0",
        "supervisor-digest:row-1",
        "supervisor-digest:row-2",
    }
    # The doc-13:97-111 per-ref fields are preserved verbatim.
    for ref in supervisor_refs:
        assert ref.feature_id == "feat-13h"
        assert ref.slice_id == "13a"
        assert ref.digest.startswith("sha256:supervisor-digest:")
        assert ref.completeness == "complete"
        assert ref.preview_only is False


@pytest.mark.asyncio
async def test_13h_ingest_implementation_artifacts_populates_resource_snapshot_lane(
    tmp_path,
) -> None:
    """Per sub-slice 13h point 2: symmetric for the resource-snapshot
    reader. ``compose_governance_evidence_set`` receives the populated
    ``resource_snapshot_refs`` list instead of the 13f ``[]`` baseline.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    resource_rows = _make_resource_snapshot_rows(count=4)
    resource_reader = _make_synthetic_resource_snapshot_reader(resource_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        resource_snapshot_reader=resource_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    assert resource_reader.calls, "resource_snapshot_reader was not invoked"
    assert primary_reader.calls == []
    resource_refs = [
        ref for ref in result.refs if ref.authority == "resource_snapshot"
    ]
    assert len(resource_refs) == 4
    resource_ref_ids = {ref.ref_id for ref in resource_refs}
    assert resource_ref_ids == {
        "resource-snapshot:row-0",
        "resource-snapshot:row-1",
        "resource-snapshot:row-2",
        "resource-snapshot:row-3",
    }


@pytest.mark.asyncio
async def test_13h_both_readers_populate_both_lanes(tmp_path) -> None:
    """When both readers are configured, both lanes are populated; the
    composed set's ``source_mix`` includes BOTH new authorities.

    Per sub-slice 13h point 4(e) the ``source_mix`` projection in the 13e
    digester (``evidence_set.py:_project_source_mix``) now surfaces
    non-zero counts for ``supervisor_digest`` + ``resource_snapshot``
    alongside the existing ``implementation_journal`` +
    ``implementation_decision_log`` counts the 13f wiring already produced.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_reader = _make_synthetic_supervisor_digest_reader(
        _make_supervisor_digest_rows(count=2)
    )
    resource_reader = _make_synthetic_resource_snapshot_reader(
        _make_resource_snapshot_rows(count=3)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # source_mix records BOTH new authorities (plus the existing 13f
    # implementation_journal + implementation_decision_log lanes).
    assert "supervisor_digest" in result.source_mix
    assert "resource_snapshot" in result.source_mix
    assert result.source_mix["supervisor_digest"] == 2
    assert result.source_mix["resource_snapshot"] == 3
    # Implementation_journal + implementation_decision_log lanes are still
    # populated by the 13f wiring (preserved baseline).
    assert "implementation_journal" in result.source_mix
    assert "implementation_decision_log" in result.source_mix
    # And the supervisor + resource refs are all present at the ref-list
    # level (the dedup discipline in the 13e digester is keep-first by
    # (authority, ref_id); no collisions in this fixture).
    supervisor_refs = [
        ref for ref in result.refs if ref.authority == "supervisor_digest"
    ]
    resource_refs = [
        ref for ref in result.refs if ref.authority == "resource_snapshot"
    ]
    assert len(supervisor_refs) == 2
    assert len(resource_refs) == 3


# ── 13h: unset readers default to empty lists ──────────────────────────────


@pytest.mark.asyncio
async def test_13h_unset_readers_default_to_empty_lanes(tmp_path) -> None:
    """Per sub-slice 13h point 3 (the optional-readers / empty-default
    decision): when both readers are unset, the composed set carries the
    13f baseline ref-list (only ``implementation_journal`` +
    ``implementation_decision_log`` refs) -- no exception, no spurious
    calls, no supervisor / resource refs.

    Per the auto-memory ``feedback_no_silent_degradation`` rule: the KEY
    qualifier "REQUIRED" applies. Doc-13:207-208 names the journal/decision-
    log paths as REQUIRED on this method (13f fail-closes-loudly on
    missing); supervisor_digest + resource_snapshot are AUGMENTING --
    doc-13:178-181 lists them alongside the canonical lanes without
    marking them required. The empty-default preserves the 13f baseline.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    # NO supervisor_digest_reader / resource_snapshot_reader passed.
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    # Construction succeeds; no eager validation fail.
    assert ingestor._supervisor_digest_reader is None
    assert ingestor._resource_snapshot_reader is None

    # ingest_implementation_artifacts succeeds; the 13f baseline ref-list
    # is preserved.
    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # No supervisor / resource refs in the composed set.
    assert all(
        ref.authority != "supervisor_digest" for ref in result.refs
    ), "unset supervisor_digest_reader produced supervisor_digest refs"
    assert all(
        ref.authority != "resource_snapshot" for ref in result.refs
    ), "unset resource_snapshot_reader produced resource_snapshot refs"
    # source_mix does not carry the missing authorities.
    assert "supervisor_digest" not in result.source_mix
    assert "resource_snapshot" not in result.source_mix
    # The 13f baseline (implementation_journal + implementation_decision_log
    # refs) is preserved -- the source_mix still surfaces those authorities.
    assert "implementation_journal" in result.source_mix
    assert "implementation_decision_log" in result.source_mix


# ── 13h: bounded-read cap on the new lanes (doc-13:92 + doc-13:215-220) ────


@pytest.mark.asyncio
async def test_13h_supervisor_digest_lane_caps_at_max_ref_resolutions(
    tmp_path,
) -> None:
    """Per sub-slice 13h point 6 (test-honesty bounded-read invariant):
    the supervisor-digest lane MUST cap at ``min(budget.max_ref_resolutions,
    ctor_cap)`` refs. A reader producing 100 rows with
    ``budget.max_ref_resolutions=2`` yields EXACTLY 2 supervisor_digest
    refs + ``read_budget_exhausted=True`` + ``omitted_refs`` populated
    with the truncated suffix per doc-13:215-220.

    Each yielded row IS a ``GovernanceEvidenceRef`` (one ref per row), so
    the doc-13:92 ``max_ref_resolutions`` cap is the conceptually correct
    bound for this lane (NOT doc-13:90 ``max_event_rows`` which governs
    event-row reads on :meth:`ingest_feature_window`).
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_rows = _make_supervisor_digest_rows(count=100)
    supervisor_reader = _make_synthetic_supervisor_digest_reader(supervisor_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
    )
    # Tight per-call budget: max_ref_resolutions=2 forces the cap.
    budget = GovernanceReadBudget(max_ref_resolutions=2)

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=budget
    )

    # The reader was asked for limit = cap + 1 = 3 (per the +1 sentinel
    # discipline mirrored at ingestor.py:_invoke_reader_with).
    assert supervisor_reader.calls
    last_call_kwargs = supervisor_reader.calls[-1].kwargs
    assert last_call_kwargs["limit"] == 3
    # And the composed set has EXACTLY 2 supervisor_digest refs (the cap;
    # the +1 sentinel was dropped, not silently absorbed).
    supervisor_refs = [
        ref for ref in result.refs if ref.authority == "supervisor_digest"
    ]
    assert len(supervisor_refs) == 2
    # The composed set carries read_budget_exhausted=True per doc-13:215-220.
    assert result.read_budget_exhausted is True
    # And the omitted_refs list carries the cap-overflow exact page-ref.
    supervisor_omitted = [
        ref
        for ref in result.omitted_refs
        if ref.authority == "supervisor_digest"
    ]
    assert len(supervisor_omitted) == 1
    overflow = supervisor_omitted[0]
    assert overflow.completeness == "paged"
    assert overflow.exact is True
    # The overflow page-ref records the exact truncated suffix range
    # (item_start = cap; item_end = full observed row count).
    assert overflow.item_start == 2
    assert overflow.item_end == 100
    # The dropped suffix is NOT present in the returned supervisor refs
    # (defence against silent truncation).
    returned_ids = {ref.ref_id for ref in supervisor_refs}
    assert "supervisor-digest:row-2" not in returned_ids
    assert "supervisor-digest:row-99" not in returned_ids


@pytest.mark.asyncio
async def test_13h_resource_snapshot_lane_caps_at_max_ref_resolutions(
    tmp_path,
) -> None:
    """Symmetric bounded-read invariant test for the resource-snapshot
    lane. A reader producing 100 rows with ``budget.max_ref_resolutions=2``
    yields EXACTLY 2 resource_snapshot refs + ``read_budget_exhausted=True``
    + ``omitted_refs`` populated.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    resource_rows = _make_resource_snapshot_rows(count=100)
    resource_reader = _make_synthetic_resource_snapshot_reader(resource_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        resource_snapshot_reader=resource_reader,
    )
    budget = GovernanceReadBudget(max_ref_resolutions=2)

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=budget
    )

    resource_refs = [
        ref for ref in result.refs if ref.authority == "resource_snapshot"
    ]
    assert len(resource_refs) == 2
    assert result.read_budget_exhausted is True
    resource_omitted = [
        ref
        for ref in result.omitted_refs
        if ref.authority == "resource_snapshot"
    ]
    assert len(resource_omitted) == 1
    assert resource_omitted[0].item_start == 2
    assert resource_omitted[0].item_end == 100


@pytest.mark.asyncio
async def test_13h_supervisor_lane_uses_ctor_cap_when_budget_exceeds_it(
    tmp_path,
) -> None:
    """The ``min(budget.max_ref_resolutions, ctor_cap)`` clamp DOWN
    discipline: a caller cannot widen the bounded-read cap by passing
    a larger ``max_ref_resolutions`` than the constructor's ``limit_cap``.

    Mirrors the Slice-10a ``_clamp_budget_to_ceiling`` precedent at
    ``workflows/develop/execution/snapshots.py:202-214`` -- the constructor
    cap is the ceiling; the per-call budget is the tightening knob.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_rows = _make_supervisor_digest_rows(count=20)
    supervisor_reader = _make_synthetic_supervisor_digest_reader(supervisor_rows)
    # Constructor cap = 3 (below the budget's 100).
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        limit_cap=3,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
    )
    # Budget tries to widen to 100; clamp DOWN to 3 (the constructor cap).
    budget = GovernanceReadBudget(max_ref_resolutions=100)

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=budget
    )

    supervisor_refs = [
        ref for ref in result.refs if ref.authority == "supervisor_digest"
    ]
    # Exactly 3 refs (the constructor cap; not 100; not 20).
    assert len(supervisor_refs) == 3
    assert result.read_budget_exhausted is True


# ── 13h: P2-13h-1 finalizer fix -- lane overflow propagates to set-level ───
# completeness + quality (doc-13:217-218 + doc-13a:24, 109-118).
#
# Reviewer-mandated remediation: when a 13h augmenting lane overflows the
# bounded-read cap, the kept refs are now marked ``completeness="paged"``
# (and each contained page-ref ``exact=False``) so the FROZEN 13e
# digester's ``_project_completeness`` at ``evidence_set.py:436-510`` sees
# at least one paged ref and projects the set-level completeness to
# ``"paged"``. The typed-only+paged branch in ``_project_quality`` at
# ``evidence_set.py:597-599`` projects ``quality="derived"`` per the
# doc-13:217 verbatim "mark quality insufficient or derived".
#
# Without this remarking the set-level completeness would stay
# ``"complete"`` and quality would stay ``"canonical"`` on overflow,
# violating doc-13:217-218 ("mark quality insufficient or derived" +
# "mark completeness paged or unavailable"). 13h is the FIRST caller
# through the digester that can set ``read_budget_exhausted=True``, so
# this latent gap was first triggerable here.


@pytest.mark.asyncio
async def test_supervisor_lane_overflow_projects_paged_completeness_to_set(
    tmp_path,
) -> None:
    """P2-13h-1 finalizer fix: when the supervisor-digest lane overflows
    the bounded-read cap (``read_budget_exhausted=True``), the composed
    set's ``completeness`` MUST project to ``"paged"`` (NOT ``"complete"``)
    and ``quality`` MUST project to ``"derived"`` (NOT ``"canonical"``)
    per doc-13:217-218 ("mark quality insufficient or derived" + "mark
    completeness paged or unavailable").

    The fix marks each KEPT ``GovernanceEvidenceRef`` for the overflowing
    lane as ``completeness="paged"`` (and each contained page-ref
    ``exact=False``) so the FROZEN 13e digester's
    ``_project_completeness`` reads ref-level ``completeness="paged"``
    and correctly projects the set-level completeness. Existing 13h
    invariants (``read_budget_exhausted=True`` + populated
    ``omitted_refs`` + kept-ref count == cap) are preserved.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_rows = _make_supervisor_digest_rows(count=100)
    supervisor_reader = _make_synthetic_supervisor_digest_reader(supervisor_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
    )
    # Tight per-call budget: max_ref_resolutions=2 forces the cap; the
    # reader yields 100 rows so the lane overflows.
    budget = GovernanceReadBudget(max_ref_resolutions=2)

    # Empty slice-ids filter -> no journal/decision-log anchors compete
    # with the supervisor lane's overflow signal (synthetic fixtures are
    # filtered out via the [] slice_ids filter on the journal/decision
    # paths). Net effect: only the 2 kept supervisor_digest refs survive
    # AND they carry the paged remarking from the lane-overflow path.
    result = await ingestor.ingest_implementation_artifacts(
        ["does-not-exist"], budget=budget
    )

    # P2-13h-1 fix: set-level completeness projects to "paged"
    # (doc-13:217-218 "mark completeness paged or unavailable").
    assert result.completeness == "paged", (
        f"P2-13h-1 fix: lane overflow MUST project set-level completeness "
        f"to 'paged' per doc-13:217-218; got {result.completeness!r}"
    )
    # P2-13h-1 fix: set-level quality projects to "derived"
    # (doc-13:217 "mark quality insufficient or derived"; typed-only+paged
    # branch at evidence_set.py:597-599).
    assert result.quality == "derived", (
        f"P2-13h-1 fix: lane overflow MUST project set-level quality to "
        f"'derived' per doc-13:217 (typed-only+paged branch at "
        f"evidence_set.py:597-599); got {result.quality!r}"
    )

    # The 2 kept supervisor refs MUST carry completeness="paged" (the
    # remarking the producer-side fix at ingestor.py:_ingest_augmenting_lane
    # applies on overflow).
    supervisor_refs = [
        ref for ref in result.refs if ref.authority == "supervisor_digest"
    ]
    assert len(supervisor_refs) == 2
    for ref in supervisor_refs:
        assert ref.completeness == "paged", (
            f"P2-13h-1 fix: kept supervisor_digest refs MUST be remarked "
            f"completeness='paged' on overflow; got {ref.completeness!r}"
        )
        # preview_only stays False (these refs ARE real data, just
        # paginated). The 13a _preview_only_completeness_consistency
        # validator at models.py:309-323 allows
        # preview_only=False + completeness="paged".
        assert ref.preview_only is False
        # Each contained page-ref must agree: exact=False (the 13a
        # _exact_completeness_consistency at models.py:234-249 allows
        # paged+exact=False; the producer-side remarking forces this
        # so authoritative consumers cannot silently treat the kept
        # ref as exact under overflow).
        for page_ref in ref.page_refs:
            assert page_ref.exact is False
            assert page_ref.completeness == "paged"

    # Existing 13h invariants preserved: read_budget_exhausted=True +
    # omitted_refs populated with the truncated-suffix cap-overflow
    # page-ref per doc-13:215-220.
    assert result.read_budget_exhausted is True
    supervisor_omitted = [
        ref
        for ref in result.omitted_refs
        if ref.authority == "supervisor_digest"
    ]
    assert len(supervisor_omitted) == 1
    overflow = supervisor_omitted[0]
    assert overflow.completeness == "paged"
    assert overflow.exact is True
    assert overflow.item_start == 2
    assert overflow.item_end == 100


@pytest.mark.asyncio
async def test_resource_lane_overflow_projects_paged_completeness_to_set(
    tmp_path,
) -> None:
    """Symmetric P2-13h-1 finalizer fix: when the resource-snapshot lane
    overflows the bounded-read cap (``read_budget_exhausted=True``), the
    composed set's ``completeness`` MUST project to ``"paged"`` (NOT
    ``"complete"``) and ``quality`` MUST project to ``"derived"`` (NOT
    ``"canonical"``) per doc-13:217-218.

    Same producer-side remarking discipline as the supervisor-lane test
    above; the symmetric test pins that BOTH 13h lanes honour the
    overflow propagation contract (no asymmetric treatment between
    supervisor_digest and resource_snapshot per doc-13:80-81).
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    resource_rows = _make_resource_snapshot_rows(count=100)
    resource_reader = _make_synthetic_resource_snapshot_reader(resource_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        resource_snapshot_reader=resource_reader,
    )
    budget = GovernanceReadBudget(max_ref_resolutions=2)

    # Filter out the synthetic journal/decision-log anchors so only the
    # overflowing resource_snapshot lane drives the set-level projection.
    result = await ingestor.ingest_implementation_artifacts(
        ["does-not-exist"], budget=budget
    )

    # P2-13h-1 fix: set-level completeness + quality projections.
    assert result.completeness == "paged", (
        f"P2-13h-1 fix: lane overflow MUST project set-level completeness "
        f"to 'paged' per doc-13:217-218; got {result.completeness!r}"
    )
    assert result.quality == "derived", (
        f"P2-13h-1 fix: lane overflow MUST project set-level quality to "
        f"'derived' per doc-13:217; got {result.quality!r}"
    )

    # Kept refs remarked completeness="paged".
    resource_refs = [
        ref for ref in result.refs if ref.authority == "resource_snapshot"
    ]
    assert len(resource_refs) == 2
    for ref in resource_refs:
        assert ref.completeness == "paged", (
            f"P2-13h-1 fix: kept resource_snapshot refs MUST be remarked "
            f"completeness='paged' on overflow; got {ref.completeness!r}"
        )
        assert ref.preview_only is False
        for page_ref in ref.page_refs:
            assert page_ref.exact is False
            assert page_ref.completeness == "paged"

    # Existing 13h invariants preserved.
    assert result.read_budget_exhausted is True
    resource_omitted = [
        ref
        for ref in result.omitted_refs
        if ref.authority == "resource_snapshot"
    ]
    assert len(resource_omitted) == 1
    assert resource_omitted[0].item_start == 2
    assert resource_omitted[0].item_end == 100


# ── 13h: idempotency_key stability across reruns ───────────────────────────


@pytest.mark.asyncio
async def test_13h_idempotency_key_stable_across_reruns_with_new_lanes(
    tmp_path,
) -> None:
    """Per sub-slice 13h point 4(f): the digester's set-level
    ``idempotency_key`` is invariant across reruns when the new lanes are
    populated.

    Two consecutive ``ingest_implementation_artifacts`` calls on the same
    ingestor with the same readers + same fixtures MUST produce byte-
    identical ``idempotency_key`` values. This pins the 13e digester's
    sort-invariance guarantee end-to-end through the 13h-added lanes.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_reader = _make_synthetic_supervisor_digest_reader(
        _make_supervisor_digest_rows(count=4)
    )
    resource_reader = _make_synthetic_resource_snapshot_reader(
        _make_resource_snapshot_rows(count=5)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
    )

    result_a = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )
    result_b = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )

    # Two runs -> byte-identical idempotency_key.
    assert result_a.idempotency_key == result_b.idempotency_key
    # And every (authority, ref_id) pair matches.
    a_keys = sorted((ref.authority, ref.ref_id) for ref in result_a.refs)
    b_keys = sorted((ref.authority, ref.ref_id) for ref in result_b.refs)
    assert a_keys == b_keys
    # Defence-in-depth: source_mix per-authority counts also match.
    assert result_a.source_mix == result_b.source_mix


# ── 13h: round-trip cleanly with the new lanes ─────────────────────────────


@pytest.mark.asyncio
async def test_13h_evidence_set_with_new_lanes_round_trips_clean(
    tmp_path,
) -> None:
    """A composed evidence set with populated supervisor + resource lanes
    round-trips ``model_dump_json`` -> ``model_validate_json`` identically.

    The 13a + 13e cross-validators run on ``model_validate_json`` -- the
    round-trip proves the 13h additions produce shapes that satisfy every
    typed-surface constraint (no validator false-fires on the new lane).
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_reader = _make_synthetic_supervisor_digest_reader(
        _make_supervisor_digest_rows(count=2)
    )
    resource_reader = _make_synthetic_resource_snapshot_reader(
        _make_resource_snapshot_rows(count=2)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    payload = result.model_dump_json()
    rebuilt = GovernanceEvidenceSet.model_validate_json(payload)
    assert rebuilt == result
    assert rebuilt.model_dump_json() == payload


# ── 13h: statement-timeout-forwarded invariant on the new lanes ────────────


@pytest.mark.asyncio
async def test_13h_supervisor_lane_forwards_statement_timeout_ms(
    tmp_path,
) -> None:
    """The new lanes honour the same ``statement_timeout_ms`` clamp
    discipline as :meth:`ingest_feature_window` -- the per-call budget is
    clamped DOWN to the constructor cap and forwarded verbatim to the
    bounded reader (mirrors the existing
    ``test_ingest_feature_window_forwards_statement_timeout_ms`` test).
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_reader = _make_synthetic_supervisor_digest_reader(
        _make_supervisor_digest_rows(count=1)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        statement_timeout_ms=2_500,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
    )
    # Budget exceeds constructor cap; the constructor cap wins (2_500).
    budget = GovernanceReadBudget(statement_timeout_ms=9_999)

    await ingestor.ingest_implementation_artifacts([], budget=budget)

    assert supervisor_reader.calls
    assert (
        supervisor_reader.calls[-1].kwargs["statement_timeout_ms"] == 2_500
    )


# ── 13h: no-mutation invariant preserved on the new lanes ──────────────────


@pytest.mark.asyncio
async def test_13h_new_lane_readers_no_write_attribute_access(
    tmp_path,
) -> None:
    """Belt-and-suspenders: the new ``supervisor_digest_reader`` and
    ``resource_snapshot_reader`` are read-only -- the ingestor invokes the
    reader callable ONLY, never an ``.insert`` / ``.update`` / ``.write``
    / ``.commit`` attribute (governance prompt § "Non-Negotiables").

    Mirrors the existing ``test_no_mutation_invoked_on_reader_during_*``
    invariant tests using the same ``_RecordingReader`` write-detection
    pattern.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    supervisor_reader = _make_synthetic_supervisor_digest_reader(
        _make_supervisor_digest_rows(count=2)
    )
    resource_reader = _make_synthetic_resource_snapshot_reader(
        _make_resource_snapshot_rows(count=2)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
        resource_snapshot_reader=resource_reader,
    )

    await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The PRIMARY reader was never invoked (13f wiring preserves this).
    assert primary_reader.calls == []
    assert primary_reader.attribute_accesses == []
    # The new-lane readers were invoked AT LEAST once and NO write-shaped
    # attribute was accessed on either.
    assert supervisor_reader.calls
    assert resource_reader.calls
    assert supervisor_reader.attribute_accesses == []
    assert resource_reader.attribute_accesses == []


# ── 13h: sync + async new-lane readers (mirrors 13b sync/async tests) ──────


@pytest.mark.asyncio
async def test_13h_new_lane_readers_accept_async_callables(
    tmp_path,
) -> None:
    """Sub-slice 13h sanity: the new lanes go through the same
    :meth:`_invoke_reader_with` async-or-sync dispatch as the rest of the
    ingestor (``inspect.isawaitable``). An async reader is accepted and
    its rows surface verbatim in the composed set.

    Mirrors the existing ``test_default_impl_accepts_async_reader`` test
    pattern but exercises the 13h-added invocation path.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])

    async def supervisor_reader(
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        return BoundedReadResult(
            rows=_make_supervisor_digest_rows(count=2),
            authority=authority,
        )

    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=supervisor_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    supervisor_refs = [
        ref for ref in result.refs if ref.authority == "supervisor_digest"
    ]
    assert len(supervisor_refs) == 2


@pytest.mark.asyncio
async def test_13h_new_lane_rejects_non_bounded_read_result(
    tmp_path,
) -> None:
    """Defence in depth: a misbehaving new-lane reader that returns
    something other than a :class:`BoundedReadResult` raises ``TypeError``
    -- no silent coercion. Mirrors the existing
    ``test_default_impl_rejects_non_bounded_read_result_from_reader``.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])

    def bad_supervisor_reader(*args: Any, **kwargs: Any) -> Any:
        # Returns a dict, not the typed model.
        return {"rows": [], "authority": "supervisor_digest"}

    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        supervisor_digest_reader=bad_supervisor_reader,
    )

    with pytest.raises(TypeError, match="BoundedReadResult"):
        await ingestor.ingest_implementation_artifacts(
            [], budget=GovernanceReadBudget()
        )


# ── Sub-slice 13j -- legacy event + legacy artifact summary bounded-reader lanes
#
# Doc-13:191-192 verbatim ("Keep legacy event/artifact ingestion read-only and
# bounded. Use summaries and selected slices only.") + doc-13:74-84
# (``EvidenceAuthority`` enum members ``legacy_event`` +
# ``legacy_artifact_summary`` -- the 2 LEGACY values in the 9-value authority
# enum) + doc-13:173-175 verbatim ("Mixed typed/legacy evidence is encoded as
# ``quality='derived'`` plus source_mix, not as a separate ``EvidenceQuality``
# literal"). The 13j sub-slice is the SEVENTH and FINAL doc-13 § Refactoring
# Steps deliverable; once landed all 7 doc-13 steps are satisfied.
#
# Decision (per implementer journal BEFORE entry): **optional readers, empty-
# default** (mirrors the 13h decision per doc-13:178-181). Doc-13:207-208
# fail-closed semantics apply specifically to the implementation journal /
# decision log (already 13f-fail-closed); legacy_event + legacy_artifact_summary
# are AUGMENTING authorities -- when the reader is unset the lane stays empty
# (13b/13f/13h baseline preserved). When configured, the bounded-read invariant
# fires: ``min(budget.max_ref_resolutions, ctor_cap)`` (doc-13:92) per ref
# since each yielded row IS a ref. Overflow surfaces as
# ``read_budget_exhausted=True`` + an exact ``omitted_refs`` page-ref per
# doc-13:215-220.
#
# Quality discipline (doc-13:173-175 verbatim): every legacy ref carries
# ``quality="derived"`` so the producer-side projection is aligned with the
# FROZEN 13e digester's set-level ``_project_quality`` "has_legacy" branch at
# ``evidence_set.py:589-592`` (mixed typed/legacy -> set ``derived``) and the
# "legacy-only" branch at ``evidence_set.py:581-587`` (legacy-only -> set
# ``insufficient``).
#
# FROZEN-digester decision: legacy_event refs are appended to the existing
# ``supervisor_digest_refs`` augmenting kwarg list; legacy_artifact_summary
# refs are appended to the existing ``resource_snapshot_refs`` augmenting
# kwarg list. The FROZEN 13e digester ``_project_source_mix`` counts by
# ``ref.authority`` (NOT by input-list position) so refs project correctly
# to the right source_mix bucket regardless of which input kwarg they came
# from. The alternative of adding new ``legacy_event_refs`` +
# ``legacy_artifact_summary_refs`` kwargs would require editing FROZEN
# ``evidence_set.py`` (out of 13j scope).


def _make_legacy_event_rows(
    count: int, *, slice_id: str | None = "13a"
) -> list[dict[str, Any]]:
    """Build ``count`` synthetic legacy-event rows projected into the bounded-
    reader row shape the 13b :class:`BoundedReadResult` exposes
    (``list[dict[str, Any]]``).

    Each row mirrors the SUMMARY-only column shape doc-13 § "Bounded reads"
    + doc-13:191-192 verbatim ("Use summaries and selected slices only.")
    mandates: ids / digests / counts / bounded text -- no artifact bodies.
    The legacy_event authority surfaces rows from the pre-Slice-01 ``events``
    table; the 13j scope cites them as the doc-13:74-84 ``legacy_event``
    authority + the doc-13:173-175 ``quality="derived"`` projection.
    """

    return [
        {
            "ref_id": f"legacy-event:row-{i}",
            "id": i,
            "digest": f"sha256:legacy-event:row-{i}",
            "summary": f"legacy event row {i}",
            "feature_id": "feat-13j",
            "slice_id": slice_id,
            # Legacy event_id column projected from the pre-Slice-01 events
            # table. doc-13:97-111 GovernanceEvidenceRef carries event_id +
            # artifact_id optional cross-cite columns; the legacy_event lane
            # uses event_id.
            "event_id": 1000 + i,
            "event_type": "legacy.event",
        }
        for i in range(count)
    ]


def _make_legacy_artifact_summary_rows(
    count: int, *, slice_id: str | None = "13a"
) -> list[dict[str, Any]]:
    """Build ``count`` synthetic legacy-artifact-summary rows.

    Doc-13:191-192 verbatim ("Use summaries and selected slices only.")
    mandates SUMMARY-only ingestion; the artifact-body hydration code path
    is FORBIDDEN on the legacy lane. The bounded reader's row shape is
    therefore a flat dict with ids / digests / bounded text only -- no
    multi-megabyte body fields.
    """

    return [
        {
            "ref_id": f"legacy-artifact-summary:row-{i}",
            "id": i,
            "digest": f"sha256:legacy-artifact-summary:row-{i}",
            "summary": f"legacy artifact summary row {i}",
            "feature_id": "feat-13j",
            "slice_id": slice_id,
            # Legacy artifact_id column projected from the pre-Slice-01
            # artifacts table. doc-13:97-111 GovernanceEvidenceRef carries
            # artifact_id; the legacy_artifact_summary lane uses it.
            "artifact_id": 2000 + i,
            "artifact_kind": "legacy.artifact",
        }
        for i in range(count)
    ]


def _make_synthetic_legacy_event_reader(
    rows: list[dict[str, Any]],
) -> _RecordingReader:
    """A :class:`_RecordingReader` pre-loaded with the synthetic legacy-event
    rows + the ``"legacy_event"`` authority pin."""

    return _RecordingReader(rows=rows, authority="legacy_event")


def _make_synthetic_legacy_artifact_summary_reader(
    rows: list[dict[str, Any]],
) -> _RecordingReader:
    """A :class:`_RecordingReader` pre-loaded with the synthetic legacy-
    artifact-summary rows + the ``"legacy_artifact_summary"`` authority pin."""

    return _RecordingReader(rows=rows, authority="legacy_artifact_summary")


# ── 13j: constructor accepts the two new legacy reader kwargs ──────────────


def test_13j_constructor_accepts_legacy_event_and_legacy_artifact_summary_readers() -> None:
    """Per sub-slice 13j point 2 the constructor accepts two NEW optional
    ``BoundedReader`` kwargs: ``legacy_event_reader`` and
    ``legacy_artifact_summary_reader``.

    Mirrors the 13b ``BoundedReader`` protocol at ``ingestor.py:186-212``
    and the 13h supervisor/resource pair; both default to ``None`` so an
    unconfigured ingestor still constructs successfully (13b/13f/13h
    baseline preserved -- the empty-default decision per doc-13:178-181 is
    consistent across all 4 AUGMENTING authorities).
    """

    primary_reader = _make_fake_reader([])
    legacy_event_reader = _make_fake_reader([])
    legacy_artifact_summary_reader = _make_fake_reader([])

    # Both kwargs supplied -- the constructor accepts without error.
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_summary_reader,
    )
    assert ingestor._legacy_event_reader is legacy_event_reader
    assert ingestor._legacy_artifact_summary_reader is legacy_artifact_summary_reader

    # Either or both omitted -- defaults to None per the empty-default
    # decision (doc-13:207-208 fail-closed semantics apply to journal_path
    # only; legacy_event + legacy_artifact_summary are augmenting per
    # doc-13:178-181 / doc-13:191-192).
    ingestor_no_kwargs = DefaultGovernanceEvidenceIngestor(primary_reader)
    assert ingestor_no_kwargs._legacy_event_reader is None
    assert ingestor_no_kwargs._legacy_artifact_summary_reader is None

    ingestor_only_event = DefaultGovernanceEvidenceIngestor(
        primary_reader, legacy_event_reader=legacy_event_reader
    )
    assert ingestor_only_event._legacy_event_reader is legacy_event_reader
    assert ingestor_only_event._legacy_artifact_summary_reader is None

    ingestor_only_artifact = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        legacy_artifact_summary_reader=legacy_artifact_summary_reader,
    )
    assert ingestor_only_artifact._legacy_event_reader is None
    assert (
        ingestor_only_artifact._legacy_artifact_summary_reader
        is legacy_artifact_summary_reader
    )


# ── 13j: both legacy readers populate the new lanes end-to-end ─────────────


@pytest.mark.asyncio
async def test_13j_ingest_implementation_artifacts_populates_legacy_event_lane(
    tmp_path,
) -> None:
    """Per sub-slice 13j point 3 (the WIRE step): when the
    ``legacy_event_reader`` is configured, the composed evidence set's
    ``refs`` list includes ``authority="legacy_event"`` refs.

    The reader-yielded rows are projected via the existing
    ``_project_row_to_ref`` helper (extended in 13j with the optional
    ``quality`` kwarg) which preserves the per-row
    ``ref_id`` / ``digest`` / ``feature_id`` / ``slice_id`` /
    ``event_id`` cross-cite fields and sets ``quality="derived"``
    per doc-13:173-175 verbatim.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_rows = _make_legacy_event_rows(count=3)
    legacy_event_reader = _make_synthetic_legacy_event_reader(legacy_event_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The legacy_event reader WAS invoked (the primary reader was NOT --
    # 13f wiring preserved).
    assert legacy_event_reader.calls, "legacy_event_reader was not invoked"
    assert primary_reader.calls == []
    # And the composed set includes legacy_event refs.
    legacy_event_refs = [
        ref for ref in result.refs if ref.authority == "legacy_event"
    ]
    assert len(legacy_event_refs) == 3
    legacy_event_ref_ids = {ref.ref_id for ref in legacy_event_refs}
    assert legacy_event_ref_ids == {
        "legacy-event:row-0",
        "legacy-event:row-1",
        "legacy-event:row-2",
    }
    # Doc-13:97-111 per-ref fields preserved verbatim (the producer-side
    # legacy lane re-uses the same _project_row_to_ref helper as the
    # typed-first lanes so the per-row id / digest / feature_id /
    # slice_id / event_id projection is uniform).
    for ref in legacy_event_refs:
        assert ref.feature_id == "feat-13j"
        assert ref.slice_id == "13a"
        assert ref.digest.startswith("sha256:legacy-event:")
        assert ref.completeness == "complete"
        assert ref.preview_only is False


@pytest.mark.asyncio
async def test_13j_ingest_implementation_artifacts_populates_legacy_artifact_summary_lane(
    tmp_path,
) -> None:
    """Per sub-slice 13j point 3: symmetric for the legacy-artifact-summary
    reader. The composed evidence set's ``refs`` list includes
    ``authority="legacy_artifact_summary"`` refs.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_artifact_rows = _make_legacy_artifact_summary_rows(count=4)
    legacy_artifact_reader = _make_synthetic_legacy_artifact_summary_reader(
        legacy_artifact_rows
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    assert legacy_artifact_reader.calls, (
        "legacy_artifact_summary_reader was not invoked"
    )
    assert primary_reader.calls == []
    legacy_artifact_refs = [
        ref
        for ref in result.refs
        if ref.authority == "legacy_artifact_summary"
    ]
    assert len(legacy_artifact_refs) == 4
    legacy_artifact_ref_ids = {ref.ref_id for ref in legacy_artifact_refs}
    assert legacy_artifact_ref_ids == {
        "legacy-artifact-summary:row-0",
        "legacy-artifact-summary:row-1",
        "legacy-artifact-summary:row-2",
        "legacy-artifact-summary:row-3",
    }


@pytest.mark.asyncio
async def test_13j_both_legacy_readers_populate_source_mix_with_both_authorities(
    tmp_path,
) -> None:
    """When both legacy readers are configured, the composed set's
    ``source_mix`` includes BOTH legacy authorities with the correct
    counts per doc-13:74-84 + doc-13:137.

    This is the central source_mix-projection invariant for 13j: the
    FROZEN 13e digester's ``_project_source_mix`` at
    ``evidence_set.py:513-531`` counts by ``ref.authority``, so refs
    flowing through the existing ``supervisor_digest_refs`` /
    ``resource_snapshot_refs`` augmenting kwarg lists (per the FROZEN-
    digester decision documented in the journal BEFORE entry) project to
    the correct ``source_mix`` buckets regardless of input-list position.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_reader = _make_synthetic_legacy_event_reader(
        _make_legacy_event_rows(count=2)
    )
    legacy_artifact_reader = _make_synthetic_legacy_artifact_summary_reader(
        _make_legacy_artifact_summary_rows(count=3)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # source_mix records BOTH legacy authorities (plus the existing 13f
    # implementation_journal + implementation_decision_log lanes).
    assert "legacy_event" in result.source_mix
    assert "legacy_artifact_summary" in result.source_mix
    assert result.source_mix["legacy_event"] == 2
    assert result.source_mix["legacy_artifact_summary"] == 3
    # The 13f baseline (implementation_journal + implementation_decision_log)
    # is preserved.
    assert "implementation_journal" in result.source_mix
    assert "implementation_decision_log" in result.source_mix


# ── 13j: empty-default preserves 13h/13f baseline ──────────────────────────


@pytest.mark.asyncio
async def test_13j_unset_legacy_readers_preserve_baseline(tmp_path) -> None:
    """Per sub-slice 13j point 2 (the empty-default decision): when both
    legacy readers are unset, the composed set carries the 13h baseline
    ref-list (only typed-first refs from implementation_journal +
    implementation_decision_log + optionally supervisor_digest +
    resource_snapshot) -- no exception, no spurious calls, no legacy refs.

    Per the auto-memory ``feedback_no_silent_degradation`` rule: the KEY
    qualifier "REQUIRED" applies. Doc-13:207-208 names the journal/decision-
    log paths as REQUIRED on this method (13f fail-closes-loudly on
    missing); legacy_event + legacy_artifact_summary are AUGMENTING per
    doc-13:178-181 / doc-13:191-192 -- the empty-default preserves the
    13b/13f/13h baseline.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    # NO legacy_event_reader / legacy_artifact_summary_reader passed.
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
    )

    # Construction succeeds; no eager validation fail.
    assert ingestor._legacy_event_reader is None
    assert ingestor._legacy_artifact_summary_reader is None

    # ingest_implementation_artifacts succeeds; the 13h baseline ref-list
    # is preserved (typed-first only).
    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # No legacy refs in the composed set.
    assert all(
        ref.authority != "legacy_event" for ref in result.refs
    ), "unset legacy_event_reader produced legacy_event refs"
    assert all(
        ref.authority != "legacy_artifact_summary" for ref in result.refs
    ), "unset legacy_artifact_summary_reader produced legacy_artifact_summary refs"
    # source_mix does not carry the missing authorities.
    assert "legacy_event" not in result.source_mix
    assert "legacy_artifact_summary" not in result.source_mix
    # The 13f baseline (implementation_journal + implementation_decision_log
    # refs) is preserved -- the source_mix still surfaces those authorities.
    assert "implementation_journal" in result.source_mix
    assert "implementation_decision_log" in result.source_mix


# ── 13j: bounded-read cap on the legacy lanes (doc-13:92 + doc-13:215-220) ─


@pytest.mark.asyncio
async def test_13j_legacy_event_lane_caps_at_max_ref_resolutions(
    tmp_path,
) -> None:
    """Per sub-slice 13j point 5 (test-honesty bounded-read invariant):
    the legacy_event lane MUST cap at
    ``min(budget.max_ref_resolutions, ctor_cap)`` refs. A reader producing
    100 rows with ``budget.max_ref_resolutions=2`` yields EXACTLY 2
    legacy_event refs + ``read_budget_exhausted=True`` + ``omitted_refs``
    populated with the truncated suffix per doc-13:215-220.

    Each yielded row IS a ``GovernanceEvidenceRef`` (one ref per row), so
    the doc-13:92 ``max_ref_resolutions`` cap is the conceptually correct
    bound for this lane (NOT doc-13:90 ``max_event_rows`` which governs
    event-row reads on :meth:`ingest_feature_window`). Doc-13:191-192
    verbatim ("Use summaries and selected slices only.") explicitly
    forbids unbounded body hydration -- the cap is the bounded-read
    backstop.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_rows = _make_legacy_event_rows(count=100)
    legacy_event_reader = _make_synthetic_legacy_event_reader(legacy_event_rows)
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
    )
    # Tight per-call budget: max_ref_resolutions=2 forces the cap.
    budget = GovernanceReadBudget(max_ref_resolutions=2)

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=budget
    )

    # The reader was asked for limit = cap + 1 = 3 (per the +1 sentinel
    # discipline mirrored at ingestor.py:_invoke_reader_with).
    assert legacy_event_reader.calls
    last_call_kwargs = legacy_event_reader.calls[-1].kwargs
    assert last_call_kwargs["limit"] == 3
    # And the composed set has EXACTLY 2 legacy_event refs (the cap; the
    # +1 sentinel was dropped, not silently absorbed).
    legacy_event_refs = [
        ref for ref in result.refs if ref.authority == "legacy_event"
    ]
    assert len(legacy_event_refs) == 2
    # The composed set carries read_budget_exhausted=True per doc-13:215-220.
    assert result.read_budget_exhausted is True
    # And the omitted_refs list carries the cap-overflow exact page-ref.
    legacy_omitted = [
        ref
        for ref in result.omitted_refs
        if ref.authority == "legacy_event"
    ]
    assert len(legacy_omitted) == 1
    overflow = legacy_omitted[0]
    assert overflow.completeness == "paged"
    assert overflow.exact is True
    # The overflow page-ref records the exact truncated suffix range
    # (item_start = cap; item_end = full observed row count).
    assert overflow.item_start == 2
    assert overflow.item_end == 100
    # The dropped suffix is NOT present in the returned legacy_event refs
    # (defence against silent truncation per doc-13:191-192 + the auto-
    # memory feedback_no_silent_degradation rule).
    returned_ids = {ref.ref_id for ref in legacy_event_refs}
    assert "legacy-event:row-2" not in returned_ids
    assert "legacy-event:row-99" not in returned_ids
    # On overflow each KEPT ref is remarked to completeness="paged" + each
    # contained page_ref.exact=False per P2-13h-1 finalizer precedent
    # (doc-13:217-218 + doc-13a:24, 109-118). The legacy lane re-uses the
    # same _remark_ref_as_paged_on_overflow helper as the 13h supervisor/
    # resource lanes.
    for ref in legacy_event_refs:
        assert ref.completeness == "paged", (
            f"legacy_event ref {ref.ref_id} not remarked to paged on overflow"
        )
        for page_ref in ref.page_refs:
            assert page_ref.exact is False, (
                f"legacy_event page_ref {page_ref.page_ref_id} not "
                "remarked to exact=False on overflow"
            )


# ── 13j: quality discipline -- every legacy ref MUST carry quality="derived" ─


@pytest.mark.asyncio
async def test_13j_legacy_refs_carry_quality_derived_per_doc_13_173_175(
    tmp_path,
) -> None:
    """Per sub-slice 13j point 5 (quality discipline): every legacy ref
    MUST carry ``quality="derived"`` (never ``"canonical"``) per
    doc-13:173-175 verbatim ("Mixed typed/legacy evidence is encoded as
    ``quality='derived'`` plus source_mix, not as a separate
    ``EvidenceQuality`` literal").

    This is the central PRODUCER-SIDE quality invariant for the 13j
    legacy lanes. The 13b/13f/13h typed-first lanes use the default
    ``quality="canonical"`` because typed-first authorities are the
    strongest possible projection; doc-13:173-175 says legacy authorities
    are encoded as ``derived``, so the producer-side projection must
    match.

    The FROZEN 13e digester's set-level ``_project_quality`` at
    ``evidence_set.py:534-599`` has the corresponding set-level rules:
    - ``has_legacy and has_typed`` -> set quality=derived
      (``evidence_set.py:589-592``);
    - legacy-only (``has_legacy and not has_typed``) -> set
      quality=insufficient (``evidence_set.py:581-587``).
    The per-ref quality being ``derived`` keeps the producer-side
    projection internally consistent with the FROZEN set-level
    projection.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_reader = _make_synthetic_legacy_event_reader(
        _make_legacy_event_rows(count=5)
    )
    legacy_artifact_reader = _make_synthetic_legacy_artifact_summary_reader(
        _make_legacy_artifact_summary_rows(count=4)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # Every legacy ref carries quality="derived" per doc-13:173-175.
    legacy_refs = [
        ref
        for ref in result.refs
        if ref.authority in ("legacy_event", "legacy_artifact_summary")
    ]
    assert len(legacy_refs) == 9  # 5 legacy_event + 4 legacy_artifact_summary
    for ref in legacy_refs:
        assert ref.quality == "derived", (
            f"legacy ref {ref.ref_id} (authority={ref.authority!r}) MUST "
            f"carry quality='derived' per doc-13:173-175 verbatim "
            f"(got quality={ref.quality!r})"
        )
        # Defence: legacy refs must NOT have quality=canonical (that
        # would be a contract violation per doc-13:173-175).
        assert ref.quality != "canonical"

    # Set-level quality projection: per the Slice 13A first sub-slice
    # the projection now also accounts for non-legacy
    # governance_evidence_gap blockers (open-findings class per
    # doc-13:253-254 criterion (e)). The synthetic fixture journal at
    # ``_write_synthetic_fixtures`` (line 1443) contains
    # ``P1-13b-1 raised; remediated in-iteration.`` which the 13c
    # parser puts into open_findings (``remediated`` is NOT in the
    # parser's RESOLVED/CLOSED/FIXED marker set per
    # ``journal_parser.py:290-292``); the digester emits a hard
    # ``governance_evidence_gap:open_findings:13b:P1-13b-1`` blocker
    # and forces set-level quality="insufficient" per doc-13:217.
    #
    # This is the Slice 13A first sub-slice criterion (e) enforcement:
    # ``Missing Slice 00-12 acceptance or unresolved P1/P2 findings
    # blocks governance acceptance``. The legacy refs themselves still
    # surface their canonical legacy-class blockers (soft class) for
    # Slice-15 confidence scoring; the set-level projection is the
    # most-conservative reading.
    assert result.quality == "insufficient", (
        f"Slice 13A first sub-slice criterion (e) enforcement: the "
        f"synthetic journal fixture contains an unresolved P1-13b-1 "
        f"finding (``remediated`` is not a parser-recognised "
        f"resolution marker per journal_parser.py:290-292); the 13e "
        f"digester emits a hard open-findings blocker + forces "
        f"set-level quality=insufficient per doc-13:217. Got "
        f"quality={result.quality!r}."
    )

    # Set-level blockers projection: the 13e digester _project_blockers
    # at evidence_set.py:602-639 emits canonical
    # ``governance_evidence_gap:<authority>:<ref_id>`` blocker strings
    # for every legacy ref + canonical
    # ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
    # blockers for every unresolved P1/P2 finding per the Slice 13A
    # invariant at doc-13a:24, 109-118 + doc-13:253-254. The legacy-
    # class blocker count is one entry per legacy ref (9 = 5
    # legacy_event + 4 legacy_artifact_summary); the open-findings
    # blockers depend on the parser's per-anchor + per-line finding
    # projection. The combined count is therefore >= 9.
    #
    # **Slice 13A first sub-slice (P3-13e-3 closure).** The canonical
    # ``governance_evidence_gap:`` shape per doc-13:209-210 replaces
    # the prior bespoke ``governance_evidence_legacy_authority:`` form
    # the 13e implementer minted. This test is updated mechanically
    # in lockstep with the source rename.
    legacy_blockers = [
        b
        for b in result.blockers
        if b.startswith(
            "governance_evidence_gap:legacy_event:"
        )
        or b.startswith(
            "governance_evidence_gap:legacy_artifact_summary:"
        )
    ]
    assert len(legacy_blockers) == 9, (
        f"expected 9 legacy-class blockers (5 legacy_event + 4 "
        f"legacy_artifact_summary); got {len(legacy_blockers)}: "
        f"{legacy_blockers!r}"
    )
    for blocker in result.blockers:
        assert blocker.startswith(
            "governance_evidence_gap:"
        ), (
            f"every blocker MUST start with the canonical "
            f"'governance_evidence_gap:' shape per doc-13:209-210 "
            f"(got {blocker!r})"
        )


# ── 13j: legacy-only -> set quality="insufficient" ─────────────────────────


@pytest.mark.asyncio
async def test_13j_legacy_only_no_journal_projection_yields_set_quality_insufficient(
    tmp_path,
) -> None:
    """Per sub-slice 13j the FROZEN 13e digester ``_project_quality``
    branch ``has_legacy and not has_typed`` -> set
    ``quality="insufficient"`` (``evidence_set.py:581-587``) fires when
    the legacy lanes are populated but the slice_ids filter excludes
    the typed-first journal + decision-log anchors so the only refs in
    the composed set are legacy.

    Filtering the journal anchors via ``slice_ids=["nonexistent"]``
    leaves zero journal_anchors + zero decision_log_anchors (the 13f
    deterministic slice_ids filter at ``ingestor.py:551-562``) while
    still invoking the legacy reader (which is not slice-scoped at the
    reader layer -- it receives the slice_ids in selectors but the
    test reader echoes its full row list verbatim). The composed set
    contains only legacy refs -> set quality="insufficient" per the
    FROZEN digester branch.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_reader = _make_synthetic_legacy_event_reader(
        _make_legacy_event_rows(count=3)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
    )

    # slice_ids filter selects no journal / decision-log anchors but the
    # legacy reader still yields its rows (it echoes verbatim regardless
    # of selectors).
    result = await ingestor.ingest_implementation_artifacts(
        ["nonexistent-slice"], budget=GovernanceReadBudget()
    )

    # Only legacy refs in the composed set.
    assert all(
        ref.authority == "legacy_event" for ref in result.refs
    )
    assert len(result.refs) == 3
    # Per doc-13:173-175 + the FROZEN _project_quality legacy-only branch
    # at evidence_set.py:581-587 -> set quality="insufficient".
    assert result.quality == "insufficient", (
        f"legacy-only composed set MUST project to set quality='insufficient' "
        f"(got {result.quality!r}) per the FROZEN 13e digester "
        f"_project_quality has_legacy AND NOT has_typed branch at "
        f"evidence_set.py:581-587"
    )


# ── 13j: idempotency_key stability across reruns with legacy lanes ─────────


@pytest.mark.asyncio
async def test_13j_idempotency_key_stable_across_reruns_with_legacy_lanes(
    tmp_path,
) -> None:
    """Per sub-slice 13j point 5 (idempotency invariant): the composed
    set's ``idempotency_key`` is stable across two reruns with identical
    inputs when the legacy lanes are populated.

    The FROZEN 13e digester at ``evidence_set.py:411-430``
    (``_compute_idempotency_key``) uses canonical-sort+SHA256 over the
    per-ref digests so two runs with the same logical input produce the
    same set-level key. This test wires both legacy lanes alongside the
    13f journal / decision-log lanes and pins the sort-invariance
    guarantee end-to-end through the 13j-added lanes.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_reader = _make_synthetic_legacy_event_reader(
        _make_legacy_event_rows(count=3)
    )
    legacy_artifact_reader = _make_synthetic_legacy_artifact_summary_reader(
        _make_legacy_artifact_summary_rows(count=4)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    result_a = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )
    result_b = await ingestor.ingest_implementation_artifacts(
        ["13a"], budget=GovernanceReadBudget()
    )

    assert result_a.idempotency_key == result_b.idempotency_key
    # Sanity: the key is the 64-char SHA-256 hex per the
    # _compute_idempotency_key contract at evidence_set.py:411-430.
    assert len(result_a.idempotency_key) == 64
    # The composed set's refs list is also identical by content (the
    # canonical sort + dedup discipline in the FROZEN 13e digester at
    # evidence_set.py:849-872 guarantees this).
    assert len(result_a.refs) == len(result_b.refs)
    for ref_a, ref_b in zip(result_a.refs, result_b.refs):
        assert ref_a.ref_id == ref_b.ref_id
        assert ref_a.authority == ref_b.authority
        assert ref_a.quality == ref_b.quality


# ── 13j: round-trip cleanly with the new legacy lanes ──────────────────────


@pytest.mark.asyncio
async def test_13j_evidence_set_with_legacy_lanes_round_trips_clean(
    tmp_path,
) -> None:
    """Per sub-slice 13j point 5 (round-trip invariant): the composed
    :class:`GovernanceEvidenceSet` from the wired legacy lanes serialises
    via ``model_dump(mode='json')`` and re-parses via
    ``GovernanceEvidenceSet.model_validate(...)`` without loss.

    The 13a cross-validators (``_exact_completeness_consistency`` +
    ``_preview_only_completeness_consistency``) all fire at re-parse
    time; a clean round-trip proves the 13j additions produce shapes
    that satisfy every 13a invariant including the legacy-authority +
    derived-quality combination.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_reader = _make_synthetic_legacy_event_reader(
        _make_legacy_event_rows(count=2)
    )
    legacy_artifact_reader = _make_synthetic_legacy_artifact_summary_reader(
        _make_legacy_artifact_summary_rows(count=2)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The result re-parses cleanly through the 13a cross-validators.
    dumped = result.model_dump(mode="json")
    reparsed = GovernanceEvidenceSet.model_validate(dumped)
    assert reparsed.idempotency_key == result.idempotency_key
    assert reparsed.quality == result.quality
    assert len(reparsed.refs) == len(result.refs)


# ── 13j: no-mutation invariant preserved on the legacy lane readers ────────


@pytest.mark.asyncio
async def test_13j_legacy_lane_readers_no_write_attribute_access(
    tmp_path,
) -> None:
    """Belt-and-suspenders: the new ``legacy_event_reader`` and
    ``legacy_artifact_summary_reader`` are read-only -- the ingestor
    invokes the reader callable itself and NEVER any
    ``.record`` / ``.insert`` / ``.update`` / ``.delete`` / ``.commit``
    side-effect method.

    Per the governance prompt § "Non-Negotiables" verbatim: "Governance
    is analytical, advisory, read-only. No governance component mutates
    executor/control-plane/product state, takes merge or checkpoint
    authority, forces policy activation, or escalates to broad product
    repair."

    Doc-13:191-192 verbatim ("Keep legacy event/artifact ingestion read-
    only and bounded") explicitly names the read-only discipline for the
    legacy lanes. The recording-proxy fake asserts the invariant by
    recording every attribute access; any access to an undefined
    attribute (i.e. any name other than ``__call__`` /
    underscore-prefixed) is captured + raised as ``AttributeError``.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])
    legacy_event_reader = _make_synthetic_legacy_event_reader(
        _make_legacy_event_rows(count=2)
    )
    legacy_artifact_reader = _make_synthetic_legacy_artifact_summary_reader(
        _make_legacy_artifact_summary_rows(count=2)
    )
    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=legacy_event_reader,
        legacy_artifact_summary_reader=legacy_artifact_reader,
    )

    await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    # The recording-reader's attribute_accesses list captures any
    # attempt to read an undefined attribute on the reader. The legacy
    # lanes invoke the reader directly (callable __call__) without
    # accessing any other attribute -- so the list is empty.
    assert legacy_event_reader.attribute_accesses == [], (
        "legacy_event_reader had unexpected non-call attribute access: "
        f"{legacy_event_reader.attribute_accesses}"
    )
    assert legacy_artifact_reader.attribute_accesses == [], (
        "legacy_artifact_summary_reader had unexpected non-call attribute "
        f"access: {legacy_artifact_reader.attribute_accesses}"
    )


# ── 13j: sync + async legacy readers (mirrors 13h sync/async tests) ────────


@pytest.mark.asyncio
async def test_13j_legacy_readers_accept_async_callables(tmp_path) -> None:
    """Sub-slice 13j sanity: the legacy lanes go through the same
    :meth:`_invoke_reader_with` helper as 13h, which honours both sync
    and async readers via ``inspect.isawaitable``. This test pins the
    async-reader branch for the legacy lane (the sync branch is
    exercised by every other 13j test via ``_RecordingReader.__call__``).
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])

    async def async_legacy_event_reader(
        authority: EvidenceAuthority,
        *,
        limit: int,
        statement_timeout_ms: int,
        selectors: dict[str, Any],
    ) -> BoundedReadResult:
        return BoundedReadResult(
            rows=_make_legacy_event_rows(count=2),
            authority="legacy_event",
        )

    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=async_legacy_event_reader,
    )

    result = await ingestor.ingest_implementation_artifacts(
        [], budget=GovernanceReadBudget()
    )

    legacy_event_refs = [
        ref for ref in result.refs if ref.authority == "legacy_event"
    ]
    assert len(legacy_event_refs) == 2
    # Every legacy ref carries quality="derived" even when the reader
    # is async -- the quality discipline is producer-side and does not
    # depend on the reader's sync/async kind.
    for ref in legacy_event_refs:
        assert ref.quality == "derived"


# ── 13j: defence in depth -- non-BoundedReadResult from the legacy lane ────


@pytest.mark.asyncio
async def test_13j_legacy_lane_rejects_non_bounded_read_result(
    tmp_path,
) -> None:
    """Defence in depth: a misbehaving legacy-lane reader that returns
    something other than a :class:`BoundedReadResult` raises
    ``TypeError`` -- no silent coercion. Mirrors the existing 13h
    ``test_13h_new_lane_rejects_non_bounded_read_result``.
    """

    journal_path, decisions_path = _write_synthetic_fixtures(tmp_path)
    primary_reader = _RecordingReader(rows=[])

    def bad_legacy_event_reader(*args: Any, **kwargs: Any) -> Any:
        # Returns a dict, not the typed model.
        return {"rows": [], "authority": "legacy_event"}

    ingestor = DefaultGovernanceEvidenceIngestor(
        primary_reader,
        journal_path=journal_path,
        decisions_path=decisions_path,
        legacy_event_reader=bad_legacy_event_reader,
    )

    with pytest.raises(TypeError, match="BoundedReadResult"):
        await ingestor.ingest_implementation_artifacts(
            [], budget=GovernanceReadBudget()
        )


# ── P2-V2-1: typed_journal lane overflow remarking discipline ──────────────


@pytest.mark.asyncio
async def test_ingest_feature_window_overflow_projects_paged_completeness_to_set() -> None:
    """P2-V2-1 finalizer fix (doc-13:217-218 verbatim): when the
    typed_journal ``ingest_feature_window`` lane overflows the
    bounded-read cap (``read_budget_exhausted=True``), the composed set
    MUST project ``completeness="paged"`` AND ``quality="derived"``
    (NOT ``"canonical"``) per doc-13:217 ("mark quality insufficient
    or derived" + "mark completeness paged or unavailable"). The 2
    kept refs MUST carry ``completeness="paged"`` (and each contained
    page-ref ``exact=False``) per the same producer-side overflow
    remarking discipline the 13h augmenting lanes + 13j legacy lanes
    apply via :meth:`_remark_ref_as_paged_on_overflow` at
    ``ingestor.py:1088-1131``.

    Without this fix the set would be internally inconsistent
    (``set.completeness="paged"`` + ``set.quality="canonical"`` BUT
    every ``refs[*].completeness="complete"``) -- the V2 contract
    integrity reviewer reproduced exactly that state empirically.

    Mirrors :func:`test_supervisor_lane_overflow_projects_paged_completeness_to_set`
    (the 13h augmenting-lane variant) but for the typed_journal lane
    that constructs the set directly via ``_build_evidence_set``
    (bypassing the FROZEN 13e digester).
    """

    # 100 rows overflows the bounded-read cap of 2; mirrors the 13h
    # supervisor-lane overflow shape (100-row reader + max=2 budget).
    rows = _summary_rows(count=100)
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, limit_cap=2)
    budget = GovernanceReadBudget(max_event_rows=2)

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    # P2-V2-1 fix: set-level completeness projects to "paged"
    # (doc-13:217-218 "mark completeness paged or unavailable").
    assert result.completeness == "paged", (
        f"P2-V2-1 fix: typed_journal lane overflow MUST project "
        f"set-level completeness to 'paged' per doc-13:217-218; got "
        f"{result.completeness!r}"
    )
    # P2-V2-1 fix: set-level quality projects to "derived" (NOT
    # "canonical") -- doc-13:217 "mark quality insufficient or
    # derived"; mirrors the 13e digester's typed-only+paged branch at
    # ``evidence_set.py:594-599``.
    assert result.quality == "derived", (
        f"P2-V2-1 fix: typed_journal lane overflow MUST project "
        f"set-level quality to 'derived' per doc-13:217 (NOT "
        f"'canonical'); got {result.quality!r}"
    )

    # The 2 kept refs MUST carry completeness="paged" (the remarking
    # the producer-side fix at ingestor.py:_build_evidence_set applies
    # on overflow via _remark_ref_as_paged_on_overflow).
    assert len(result.refs) == 2
    for ref in result.refs:
        assert ref.completeness == "paged", (
            f"P2-V2-1 fix: kept typed_journal refs MUST be remarked "
            f"completeness='paged' on overflow; got {ref.completeness!r}"
        )
        # preview_only stays False (these refs ARE real data, just
        # paginated). The 13a _preview_only_completeness_consistency
        # validator at models.py:309-323 allows
        # preview_only=False + completeness="paged".
        assert ref.preview_only is False
        # Each contained page-ref must agree: exact=False (the 13a
        # _exact_completeness_consistency at models.py:234-249 allows
        # paged+exact=False; the producer-side remarking forces this
        # so authoritative consumers cannot silently treat the kept
        # ref as exact under overflow).
        for page_ref in ref.page_refs:
            assert page_ref.exact is False
            assert page_ref.completeness == "paged"

    # Existing 13b/13f invariants preserved: read_budget_exhausted=True +
    # omitted_refs populated with the truncated-suffix cap-overflow
    # page-ref per doc-13:215-220.
    assert result.read_budget_exhausted is True
    assert len(result.omitted_refs) == 1
    overflow = result.omitted_refs[0]
    assert overflow.completeness == "paged"
    assert overflow.exact is True
    assert overflow.item_start == 2
    assert overflow.item_end == 100


@pytest.mark.asyncio
async def test_ingest_feature_window_no_overflow_preserves_canonical_quality() -> None:
    """P2-V2-1 counterpart: when the typed_journal
    ``ingest_feature_window`` lane does NOT overflow (reader yields
    <= cap rows), the producer-side overflow remarking MUST NOT
    trigger -- the set stays ``completeness="complete"`` +
    ``quality="canonical"`` and per-ref ``completeness="complete"``.

    Confirms the P2-V2-1 fix is strictly additive (mechanical
    extension; no behavior change for the non-overflow path).
    """

    # 2 rows under the cap of 5; no overflow.
    rows = _summary_rows(count=2)
    reader = _RecordingReader(rows=rows)
    ingestor = DefaultGovernanceEvidenceIngestor(reader, limit_cap=5)
    budget = GovernanceReadBudget(max_event_rows=5)

    result = await ingestor.ingest_feature_window(
        "feat-1", GovernanceWindow(), budget=budget
    )

    # P2-V2-1 fix non-overflow path: set-level projections stay
    # at the clean typed-only values per doc-13:173-175 (typed-only
    # + complete -> canonical/complete).
    assert result.completeness == "complete"
    assert result.quality == "canonical", (
        f"P2-V2-1 fix non-overflow path: typed-only + complete MUST "
        f"stay quality='canonical' (no remarking trigger); got "
        f"{result.quality!r}"
    )
    assert result.read_budget_exhausted is False
    assert result.omitted_refs == []

    # The 2 kept refs MUST stay completeness="complete" (no remarking).
    assert len(result.refs) == 2
    for ref in result.refs:
        assert ref.completeness == "complete", (
            f"P2-V2-1 fix non-overflow path: kept refs MUST NOT be "
            f"remarked on the no-overflow path; got "
            f"{ref.completeness!r}"
        )
        assert ref.preview_only is False
        for page_ref in ref.page_refs:
            assert page_ref.exact is True
            assert page_ref.completeness == "complete"
