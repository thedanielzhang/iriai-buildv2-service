"""Slice 13e -- unit tests for the evidence-set digester.

Covers the doc-13:186-187 § "Refactoring Steps" step 5 deliverable
("Add evidence-set digesting from sorted canonical JSON. The digest
must include source ids and content digests, not full artifact bodies")
for
:func:`iriai_build_v2.workflows.develop.governance.compose_governance_evidence_set`.

Each emitted :class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceSet`
is round-tripped through the 13a model invariants
(the ID-field validators ``_non_empty_set_id_fields`` /
``_source_mix_counts_non_negative`` and the contained
:class:`GovernanceEvidenceRef` validators) so the digester cannot
silently emit a set the typed shape would reject.

Per the governance prompt § "Non-Negotiables" the digester fails closed
(``ValueError`` on intra-ref duplicate ``page_ref_id``) and never silently
degrades. Per the prompt § "Bounded reads" the digester is pure-typed
(typed-shapes-in / typed-shape-out): no file I/O is exercised in the
synthetic tests; the real-anchor round-trip test reads the live
``implementation-journal.md`` + ``implementation-decisions.jsonl`` via
the 13c + 13d parsers to confirm the digester operates on real-shape
input without raising.

Doc-13:186 verbatim ("The digest must include source ids and content
digests, not full artifact bodies") is the central invariant: the
per-ref digest is over the ``(ref_id, digest, page_refs)`` projection,
NEVER over artifact bodies. The
``test_per_ref_digest_does_not_include_artifact_body_content`` test
proves this by showing two refs that differ only in (hypothetical)
artifact body content but share the same ``(ref_id, digest, page_refs)``
projection produce identical per-ref digests.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from iriai_build_v2.workflows.develop import governance
from iriai_build_v2.workflows.develop.governance import (
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
    GovernanceWindow,
    ImplementationArtifactAnchor,
    compose_governance_evidence_set,
    parse_implementation_decision_log,
    parse_implementation_journal,
)
from iriai_build_v2.workflows.develop.governance import (
    evidence_set as evidence_set_module,
)


# ── package surface ────────────────────────────────────────────────────────


def test_package_reexports_compose_governance_evidence_set() -> None:
    """The 13e digester surface is re-exported at the package level.

    The package-level strict-equality assertion lives in
    ``tests/test_governance_evidence_models.py::
    test_governance_package_reexports_doc_13_surface``; this test
    asserts the 13e-specific subset is present and is the same Python
    object as the module-local symbol.
    """

    assert "compose_governance_evidence_set" in governance.__all__
    assert hasattr(governance, "compose_governance_evidence_set")
    assert (
        governance.compose_governance_evidence_set
        is evidence_set_module.compose_governance_evidence_set
    )


def test_module_all_lists_exactly_compose_governance_evidence_set() -> None:
    """``evidence_set.__all__`` is exactly the doc-13:186-187 surface."""

    assert list(evidence_set_module.__all__) == [
        "compose_governance_evidence_set"
    ]


# ── helpers for synthetic refs ────────────────────────────────────────────


def _make_ref(
    *,
    authority: str = "typed_journal",
    ref_id: str = "ref-1",
    digest: str = "sha256:digest1",
    quality: str = "canonical",
    completeness: str = "complete",
    page_refs: list[GovernanceEvidencePageRef] | None = None,
    preview_only: bool = False,
) -> GovernanceEvidenceRef:
    """Build a :class:`GovernanceEvidenceRef` with sensible defaults."""

    return GovernanceEvidenceRef(
        authority=authority,  # type: ignore[arg-type]
        ref_id=ref_id,
        digest=digest,
        quality=quality,  # type: ignore[arg-type]
        completeness=completeness,  # type: ignore[arg-type]
        page_refs=page_refs if page_refs is not None else [],
        preview_only=preview_only,
    )


def _make_page_ref(
    *,
    page_ref_id: str = "page-1",
    authority: str = "typed_journal",
    source_ref_id: str = "ref-1",
    digest: str = "sha256:page-digest",
    completeness: str = "complete",
    exact: bool = True,
) -> GovernanceEvidencePageRef:
    """Build a :class:`GovernanceEvidencePageRef` with sensible defaults."""

    return GovernanceEvidencePageRef(
        page_ref_id=page_ref_id,
        authority=authority,  # type: ignore[arg-type]
        source_ref_id=source_ref_id,
        digest=digest,
        completeness=completeness,  # type: ignore[arg-type]
        exact=exact,
        stale_check={"freshness": "synthetic"},
    )


def _empty_window() -> GovernanceWindow:
    return GovernanceWindow()


def _default_budget() -> GovernanceReadBudget:
    return GovernanceReadBudget()


def _compose_with_refs(
    refs: list[GovernanceEvidenceRef],
    *,
    omitted_refs: list[GovernanceEvidencePageRef] | None = None,
    read_budget_exhausted: bool = False,
) -> GovernanceEvidenceSet:
    """Compose an evidence set with the supplied refs as supervisor-digest
    refs (the simplest typed-first path that does not require synthesising
    journal/decision-log anchors)."""

    return compose_governance_evidence_set(
        journal_anchors=[],
        decision_log_anchors=[],
        supervisor_digest_refs=refs,
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
        omitted_refs=omitted_refs,
        read_budget_exhausted=read_budget_exhausted,
    )


# ── empty input ───────────────────────────────────────────────────────────


def test_empty_input_yields_unavailable_completeness_and_empty_source_mix() -> None:
    """Per the user prompt's chunk-shape point 8 (empty input -> unavailable
    + empty refs + empty source_mix) and doc-13:215-220.

    No refs, no anchors -> the set is unavailable (no content to cite),
    source_mix is empty (no authorities counted), quality is insufficient
    (the 13A invariant treats zero-evidence as unable to satisfy any
    authoritative consumer per doc-13:218-220), blockers is empty
    (no legacy refs to surface).
    """

    result = compose_governance_evidence_set(
        journal_anchors=[],
        decision_log_anchors=[],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    assert result.refs == []
    assert result.source_mix == {}
    assert result.completeness == "unavailable"
    assert result.quality == "insufficient"
    assert result.blockers == []
    # idempotency_key is the SHA-256 of canonical JSON of empty list.
    expected_key = hashlib.sha256(b"[]").hexdigest()
    assert result.idempotency_key == expected_key


# ── completeness projection (doc-13:215-220) ──────────────────────────────


def test_all_exact_input_yields_complete_completeness_and_canonical_quality() -> None:
    """Per the user prompt's chunk-shape point 8 (all-exact -> complete +
    canonical) and doc-13:215-220 / doc-13:173-175.

    Every ref is from a typed-first authority, completeness="complete",
    page_refs[*].exact=True, no preview_only -> the strongest possible
    set-level projection.
    """

    refs = [
        _make_ref(
            authority="typed_journal",
            ref_id="ref-A",
            digest="sha256:digest-A",
            completeness="complete",
            page_refs=[],
        ),
        _make_ref(
            authority="git_provenance",
            ref_id="ref-B",
            digest="sha256:digest-B",
            completeness="complete",
            page_refs=[],
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.completeness == "complete"
    assert result.quality == "canonical"
    assert result.source_mix == {"typed_journal": 1, "git_provenance": 1}
    assert result.blockers == []


def test_mixed_exact_and_paged_yields_paged_completeness_and_derived_quality() -> None:
    """Per the user prompt's chunk-shape point 8 (mixed exact + paged ->
    paged + min quality) and doc-13:215-220 / doc-13:173-175.

    A paged ref alongside a complete ref -> the set is paged (the
    strongest no-preview state below complete) and quality is derived
    (per doc-13:217 "mark quality insufficient or derived" -- derived
    is the typed-only-but-truncated case).
    """

    refs = [
        _make_ref(
            authority="typed_journal",
            ref_id="ref-A",
            digest="sha256:digest-A",
            completeness="complete",
        ),
        _make_ref(
            authority="git_provenance",
            ref_id="ref-B",
            digest="sha256:digest-B",
            completeness="paged",
            page_refs=[
                _make_page_ref(
                    page_ref_id="page-B1",
                    source_ref_id="ref-B",
                    completeness="paged",
                    exact=True,
                )
            ],
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.completeness == "paged"
    assert result.quality == "derived"
    assert result.blockers == []


def test_any_preview_only_ref_yields_preview_only_and_insufficient() -> None:
    """Per the user prompt's chunk-shape point 8 (any preview_only ->
    preview_only + insufficient) and doc-13a:24, 109-118.

    A preview-only ref makes the set preview-only AND insufficient
    (the 13A invariant treats preview as display-only -- no
    preview-only set satisfies an authoritative consumer).
    """

    refs = [
        _make_ref(
            authority="typed_journal",
            ref_id="ref-A",
            digest="sha256:digest-A",
            completeness="complete",
        ),
        _make_ref(
            authority="supervisor_digest",
            ref_id="ref-B",
            digest="sha256:digest-B",
            completeness="preview_only",
            preview_only=True,
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.completeness == "preview_only"
    assert result.quality == "insufficient"
    # The preview-only ref is included in the count even though it
    # cannot be cited as execution authority -- source_mix is the
    # raw per-authority count.
    assert result.source_mix == {"typed_journal": 1, "supervisor_digest": 1}


# ── idempotency_key determinism + sort-invariance ─────────────────────────


def test_same_input_same_idempotency_key_is_deterministic() -> None:
    """Per the user prompt's chunk-shape point 8 (same input -> same key).

    Two compositions over the same logical input produce identical
    idempotency_keys. Confirms the digester does not include
    non-deterministic state (e.g. generated_at) in the key.
    """

    refs = [
        _make_ref(authority="typed_journal", ref_id="ref-1", digest="sha256:d1"),
        _make_ref(authority="git_provenance", ref_id="ref-2", digest="sha256:d2"),
    ]
    result_a = _compose_with_refs(refs)
    result_b = _compose_with_refs(refs)

    assert result_a.idempotency_key == result_b.idempotency_key
    # generated_at may differ across runs but the key MUST NOT depend on it.
    # The test does not assert equality of generated_at because the two
    # composes happen at different wall-clock times.


def test_different_input_orderings_yield_same_idempotency_key_sort_invariant() -> None:
    """Per the user prompt's chunk-shape point 8 (different ordering ->
    same idempotency_key, sort-invariant).

    Reordering the input refs MUST NOT change the set-level
    idempotency_key -- the digester sorts the per-ref digests before
    hashing.
    """

    ref_1 = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
    )
    ref_2 = _make_ref(
        authority="git_provenance", ref_id="ref-2", digest="sha256:d2"
    )
    ref_3 = _make_ref(
        authority="supervisor_digest", ref_id="ref-3", digest="sha256:d3"
    )

    forward = _compose_with_refs([ref_1, ref_2, ref_3])
    reversed_order = _compose_with_refs([ref_3, ref_2, ref_1])
    shuffled = _compose_with_refs([ref_2, ref_1, ref_3])

    assert forward.idempotency_key == reversed_order.idempotency_key
    assert forward.idempotency_key == shuffled.idempotency_key


def test_different_input_refs_yield_different_idempotency_key() -> None:
    """Changing a ref (even just its digest) MUST change the
    idempotency_key, otherwise the digest projection is lossy."""

    ref_a = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
    )
    ref_a_modified = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:DIFFERENT"
    )

    result_a = _compose_with_refs([ref_a])
    result_a_modified = _compose_with_refs([ref_a_modified])

    assert result_a.idempotency_key != result_a_modified.idempotency_key


# ── per-ref dedup keep-first ──────────────────────────────────────────────


def test_per_ref_dedup_keep_first_by_authority_and_ref_id() -> None:
    """Per the user prompt's chunk-shape point 5 (per-ref dedup keep-first).

    Two refs with the same ``(authority, ref_id)`` tuple but different
    ``digest`` values -> exactly ONE ref is kept after dedup. Per the
    P1-13e-1 finalizer fix the kept ref is the canonically-smallest
    sibling (by per-ref digest) -- deterministic regardless of input
    order. Both input orderings produce the SAME kept ref AND the SAME
    set-level idempotency_key.
    """

    ref_a = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:digest-A"
    )
    ref_b = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:digest-B"
    )

    result_forward = _compose_with_refs([ref_a, ref_b])
    result_reversed = _compose_with_refs([ref_b, ref_a])

    # Exactly ONE ref after dedup (collision on (authority, ref_id)).
    assert len(result_forward.refs) == 1
    assert len(result_reversed.refs) == 1
    # The kept ref is the SAME in both orderings -- canonical keep-first
    # picks the canonically-smallest sibling deterministically.
    assert result_forward.refs[0].digest == result_reversed.refs[0].digest
    # The kept digest is one of the two inputs (not invented).
    assert result_forward.refs[0].digest in {
        "sha256:digest-A",
        "sha256:digest-B",
    }


def test_per_ref_dedup_keeps_canonical_first_under_any_input_ordering() -> None:
    """Per the P1-13e-1 finalizer fix: keep-first is keep-CANONICAL-first.

    The digester canonically sorts refs by
    ``(authority, ref_id, _ref_digest(ref))`` BEFORE
    :func:`_dedup_refs_keep_first` runs. So at each
    ``(authority, ref_id)`` collision the kept ref is the
    canonically-smallest sibling (by per-ref digest), regardless of
    input order. Both compositions over the same logical SET of refs
    produce:

    1. the SAME kept ref at each collision, AND
    2. the SAME set-level :attr:`idempotency_key`.

    This is the sort-invariance the user prompt mandates ("test both
    orderings give the same set"). Without the canonical pre-sort the
    kept-first ref's digest would depend on input order and the
    idempotency_key would differ.

    (Replaces the pre-fix test that asserted ``forward != reversed`` --
    that assertion was honest about a bug the reviewer caught: the
    set-level idempotency_key MUST be invariant under input reordering
    per the orchestrator prompt's chunk-shape point 2.)
    """

    # Two refs with same (authority, ref_id) tuple but different digests.
    ref_smaller_digest = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:AAA"
    )
    ref_larger_digest = _make_ref(
        authority="typed_journal", ref_id="ref-1", digest="sha256:ZZZ"
    )

    forward = _compose_with_refs([ref_smaller_digest, ref_larger_digest])
    reversed_order = _compose_with_refs([ref_larger_digest, ref_smaller_digest])

    # Both orderings end up with exactly one ref after dedup -- the
    # canonically-smallest sibling at the collision.
    assert len(forward.refs) == 1
    assert len(reversed_order.refs) == 1
    # The kept ref is the SAME in both orderings (the
    # canonically-smallest per the (authority, ref_id, _ref_digest) sort).
    assert forward.refs[0].ref_id == reversed_order.refs[0].ref_id
    assert forward.refs[0].digest == reversed_order.refs[0].digest
    # The set-level idempotency_key is invariant under input reordering --
    # this is the P1-13e-1 sort-invariance contract.
    assert forward.idempotency_key == reversed_order.idempotency_key


def test_idempotency_key_invariant_under_collision_with_differing_anchor_fields() -> None:
    """P1-13e-1 minimal reproducer + regression pin.

    Two :class:`ImplementationArtifactAnchor` rows with identical
    ``(slice_id, journal_path, line_start, event)`` but different
    ``(accepted, open_findings)`` project (via
    :func:`_anchor_to_ref`) to two refs with the same
    ``(authority, ref_id)`` tuple BUT different ``_anchor_digest``
    values (the digest includes ``accepted`` + ``open_findings``).

    BEFORE the P1-13e-1 fix: ``[anchor_a, anchor_b]`` yielded a
    different ``idempotency_key`` than ``[anchor_b, anchor_a]`` because
    the kept-first ref's per-ref digest differed by input order.

    AFTER the P1-13e-1 fix: the canonical pre-sort makes the kept-first
    ref deterministic, so the set-level ``idempotency_key`` is invariant
    under input reordering.

    This is the EXACT bug the reviewer minimally reproduced (real corpus
    has ~10 such collisions among 685 journal anchors).
    """

    # Two anchors with identical (slice_id, journal_path, line_start, event)
    # but different (accepted, open_findings). Both project to refs with the
    # SAME (authority="implementation_journal", ref_id=...) tuple BUT
    # different _anchor_digest values.
    anchor_a = ImplementationArtifactAnchor(
        slice_id="13e",
        journal_path="implementation-journal.md",
        line_start=42,
        decision_log_line=None,
        event="finding",
        accepted=False,
        open_findings=["finding-A1"],
    )
    anchor_b = ImplementationArtifactAnchor(
        slice_id="13e",
        journal_path="implementation-journal.md",
        line_start=42,
        decision_log_line=None,
        event="finding",
        accepted=True,  # differs from anchor_a
        open_findings=["finding-B1", "finding-B2"],  # differs from anchor_a
    )

    set_forward = compose_governance_evidence_set(
        journal_anchors=[anchor_a, anchor_b],
        decision_log_anchors=[],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )
    set_reversed = compose_governance_evidence_set(
        journal_anchors=[anchor_b, anchor_a],
        decision_log_anchors=[],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    # Both orderings produce exactly ONE ref after dedup (collision).
    assert len(set_forward.refs) == 1
    assert len(set_reversed.refs) == 1
    # The kept-first ref is the same canonically-smallest sibling in
    # both orderings.
    assert set_forward.refs[0].digest == set_reversed.refs[0].digest
    # The set-level idempotency_key is invariant under input reordering --
    # this is the P1-13e-1 contract. BEFORE the fix the two keys
    # differed; AFTER the fix they match byte-for-byte.
    assert set_forward.idempotency_key == set_reversed.idempotency_key


def test_idempotency_key_invariant_under_full_permutation_real_corpus_subsample() -> None:
    """P1-13e-1 real-corpus subsample regression.

    Takes the first 50 journal anchors + first 50 decision-log anchors
    via the live 13c + 13d parsers, composes the evidence set forward,
    then composes again after applying ``random.seed(99); random.shuffle()``
    to BOTH input lanes; asserts the two ``idempotency_key``s match
    byte-for-byte.

    BEFORE the P1-13e-1 fix this test would fail on the real corpus
    because the journal-anchor subsample contains
    ``(authority, ref_id)``-collision siblings whose underlying
    ``(accepted, open_findings)`` fields differ -- the kept-first ref's
    per-ref digest was input-order-sensitive.

    AFTER the P1-13e-1 fix the canonical pre-sort makes the kept-first
    ref deterministic; the key is invariant under input reordering.

    Skipped only when the live fixtures are absent (mirrors the
    ``test_real_anchors_compose_yields_stable_idempotency_key_across_two_runs``
    skip guard).
    """

    import random

    if not (_REAL_JOURNAL_PATH.exists() and _REAL_DECISIONS_PATH.exists()):
        pytest.skip("real fixtures not present; pure synthetic-only run")

    all_journal = parse_implementation_journal(_REAL_JOURNAL_PATH)
    all_decisions = parse_implementation_decision_log(_REAL_DECISIONS_PATH)

    # Small subsample (first 50 + first 50) to keep the test fast while
    # still exercising the real-corpus collision shape.
    journal_subsample = list(all_journal[:50])
    decisions_subsample = list(all_decisions[:50])

    set_forward = compose_governance_evidence_set(
        journal_anchors=journal_subsample,
        decision_log_anchors=decisions_subsample,
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    # Apply random.seed(99); random.shuffle() to BOTH lanes; the
    # idempotency_key MUST be invariant under this permutation per the
    # P1-13e-1 contract.
    journal_shuffled = list(journal_subsample)
    decisions_shuffled = list(decisions_subsample)
    random.seed(99)
    random.shuffle(journal_shuffled)
    random.shuffle(decisions_shuffled)

    set_shuffled = compose_governance_evidence_set(
        journal_anchors=journal_shuffled,
        decision_log_anchors=decisions_shuffled,
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    assert set_forward.idempotency_key == set_shuffled.idempotency_key, (
        "Sort-invariance broken under full permutation on the real-corpus "
        "subsample -- the P1-13e-1 regression has resurfaced. The "
        "set-level idempotency_key MUST be invariant under input "
        "reordering per the orchestrator prompt's chunk-shape point 2 "
        "(see the module docstring's 'Sort-then-dedup discipline' note "
        "at evidence_set.py:~57-77 for the canonical pre-sort that "
        "guarantees this invariant)."
    )


# ── intra-ref page_ref_id duplicate fails closed ──────────────────────────


def test_intra_ref_duplicate_page_ref_id_raises_value_error() -> None:
    """Per the user prompt's chunk-shape point 5 (intra-ref duplicate
    page_ref_id -> ValueError; do NOT silently dedup).

    Duplicate ``page_ref_id`` within a single ref's ``page_refs`` list
    is malformed input and fails closed (mirrors the 13a
    ``_open_findings_dedup_and_non_empty`` discipline at
    ``models.py:449-469``).
    """

    duplicate_page_id = "page-DUPLICATE"
    ref_with_dup = _make_ref(
        authority="typed_journal",
        ref_id="ref-1",
        digest="sha256:d1",
        completeness="paged",
        page_refs=[
            _make_page_ref(
                page_ref_id=duplicate_page_id,
                source_ref_id="ref-1",
                completeness="paged",
                exact=True,
            ),
            _make_page_ref(
                page_ref_id=duplicate_page_id,  # DUPLICATE
                source_ref_id="ref-1",
                digest="sha256:different-page-digest",
                completeness="paged",
                exact=True,
            ),
        ],
    )

    with pytest.raises(ValueError) as exc_info:
        _compose_with_refs([ref_with_dup])

    assert duplicate_page_id in str(exc_info.value)
    assert "duplicate page_ref_id" in str(exc_info.value)


# ── Pydantic round-trip ───────────────────────────────────────────────────


def test_composed_set_round_trips_through_pydantic_re_validation() -> None:
    """Per the user prompt's chunk-shape point 8 (round-trip: feed composed
    set through Pydantic re-validation; assert no warnings/errors).

    The composed set survives ``model_dump_json`` ->
    ``model_validate_json`` cleanly, proving the digester's outputs
    satisfy every 13a model validator on its own emission.
    """

    refs = [
        _make_ref(
            authority="typed_journal",
            ref_id="ref-A",
            digest="sha256:dA",
            completeness="complete",
        ),
        _make_ref(
            authority="git_provenance",
            ref_id="ref-B",
            digest="sha256:dB",
            completeness="paged",
            page_refs=[
                _make_page_ref(
                    page_ref_id="page-B1",
                    source_ref_id="ref-B",
                    completeness="paged",
                    exact=True,
                )
            ],
        ),
    ]
    composed = _compose_with_refs(refs)

    serialised = composed.model_dump_json()
    restored = GovernanceEvidenceSet.model_validate_json(serialised)

    assert restored.idempotency_key == composed.idempotency_key
    assert restored.completeness == composed.completeness
    assert restored.quality == composed.quality
    assert restored.source_mix == composed.source_mix
    assert restored.blockers == composed.blockers
    assert len(restored.refs) == len(composed.refs)


# ── legacy authority refs in blockers ─────────────────────────────────────


def test_legacy_authority_refs_appear_in_blockers() -> None:
    """Per the user prompt's chunk-shape point 4 / point 8 (legacy-authority
    refs -> present in blockers) and doc-13a:24, 109-118.

    Refs with ``authority in {'legacy_event', 'legacy_artifact_summary'}``
    are surfaced in :attr:`GovernanceEvidenceSet.blockers` per the Slice
    13A invariant (legacy refs cannot be cited as execution authority).

    **Slice 13A first sub-slice (P3-13e-3 closure).** The canonical
    blocker shape is ``governance_evidence_gap:<authority>:<ref_id>``
    per doc-13:209-210 verbatim ("record a ``governance_evidence_gap``
    finding"). The prior bespoke
    ``governance_evidence_legacy_authority:<authority>:<ref_id>`` form
    was a 13e implementer-minted shape that the 13l/13m/13n finalizers
    deferred to Slice 13A; this test is updated mechanically to the
    canonical form per the Slice 13A first sub-slice migration.
    """

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-typed", digest="sha256:dT"
        ),
        _make_ref(
            authority="legacy_event",
            ref_id="ref-legacy-event-1",
            digest="sha256:dLE",
        ),
        _make_ref(
            authority="legacy_artifact_summary",
            ref_id="ref-legacy-artifact-1",
            digest="sha256:dLA",
        ),
    ]
    result = _compose_with_refs(refs)

    assert len(result.blockers) == 2
    assert any("legacy_event" in b for b in result.blockers)
    assert any("legacy_artifact_summary" in b for b in result.blockers)
    assert any("ref-legacy-event-1" in b for b in result.blockers)
    assert any("ref-legacy-artifact-1" in b for b in result.blockers)
    # Slice 13A first sub-slice -- canonical governance_evidence_gap
    # shape per doc-13:209-210.
    for blocker in result.blockers:
        assert blocker.startswith("governance_evidence_gap:"), (
            f"legacy ref blocker MUST start with the canonical "
            f"'governance_evidence_gap:' shape per doc-13:209-210 (got "
            f"{blocker!r})"
        )


def test_typed_only_refs_yield_empty_blockers_list() -> None:
    """All-typed-first authorities -> no blockers (the strongest typed
    projection per doc-13:74-84)."""

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-T1", digest="sha256:d1"
        ),
        _make_ref(
            authority="compatibility_projection",
            ref_id="ref-CP1",
            digest="sha256:d2",
        ),
        _make_ref(
            authority="implementation_journal",
            ref_id="ref-IJ1",
            digest="sha256:d3",
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.blockers == []


def test_mixed_typed_and_legacy_yields_derived_quality() -> None:
    """Per doc-13:173 verbatim ("Mixed typed/legacy evidence is encoded
    as quality='derived' plus source_mix").

    A set with BOTH typed-first AND legacy authorities -> quality is
    ``derived`` (not ``canonical``, not ``insufficient``); blockers list
    the legacy refs in the canonical
    ``governance_evidence_gap:<authority>:<ref_id>`` shape per
    doc-13:209-210 (Slice 13A first sub-slice P3-13e-3 closure).
    The legacy-authority class is a "soft" blocker -- the existing
    doc-13:173-175 mixed-typed-and-legacy ``derived`` projection
    remains authoritative.
    """

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-typed", digest="sha256:dT"
        ),
        _make_ref(
            authority="legacy_event",
            ref_id="ref-legacy",
            digest="sha256:dL",
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.quality == "derived"
    assert len(result.blockers) == 1
    assert "legacy_event" in result.blockers[0]
    # Slice 13A first sub-slice -- canonical governance_evidence_gap
    # shape per doc-13:209-210.
    assert result.blockers[0].startswith("governance_evidence_gap:")


def test_legacy_only_refs_yield_insufficient_quality() -> None:
    """Per doc-13:173-175 + the user prompt's chunk-shape point 4.

    Legacy-only -> no typed authority -> quality is ``insufficient``;
    blockers list every legacy ref in the canonical
    ``governance_evidence_gap:<authority>:<ref_id>`` shape per
    doc-13:209-210 (Slice 13A first sub-slice P3-13e-3 closure).
    """

    refs = [
        _make_ref(
            authority="legacy_event", ref_id="ref-L1", digest="sha256:dL1"
        ),
        _make_ref(
            authority="legacy_artifact_summary",
            ref_id="ref-L2",
            digest="sha256:dL2",
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.quality == "insufficient"
    assert len(result.blockers) == 2
    # Slice 13A first sub-slice -- canonical governance_evidence_gap
    # shape per doc-13:209-210.
    for blocker in result.blockers:
        assert blocker.startswith("governance_evidence_gap:"), (
            f"legacy ref blocker MUST start with the canonical "
            f"'governance_evidence_gap:' shape per doc-13:209-210 (got "
            f"{blocker!r})"
        )


# ── doc-13:186 verbatim: digest does NOT include artifact body content ──


def test_per_ref_digest_does_not_include_artifact_body_content() -> None:
    """Per doc-13:186 verbatim ("The digest must include source ids and
    content digests, not full artifact bodies").

    Two refs with identical ``(ref_id, digest, page_refs)`` triples but
    (notionally) different artifact bodies produce identical per-ref
    digests. The digester reads the typed surface only -- the artifact
    body is NEVER hashed.

    The test proves this by computing the digester's per-ref digest
    directly via the module-private :func:`_ref_digest` and asserting
    it depends ONLY on the typed fields the doc-13:186 contract
    enumerates.
    """

    # Two refs with identical (ref_id, digest, page_refs) but different
    # OPTIONAL metadata fields (slice_id, journal_anchor, etc. -- fields
    # that would be derived from artifact body content). The doc-13:186
    # contract says the digest is over ``source ids and content
    # digests'' only; optional metadata fields are not part of the
    # cross-process digest contract.
    ref_a = _make_ref(
        authority="typed_journal",
        ref_id="ref-shared",
        digest="sha256:shared-digest",
    )
    ref_b = GovernanceEvidenceRef(
        authority="typed_journal",
        ref_id="ref-shared",
        digest="sha256:shared-digest",
        slice_id="13e",  # added metadata (would derive from body)
        journal_anchor="implementation-journal.md:complete",  # added metadata
        quality="canonical",
        completeness="complete",
        page_refs=[],
        preview_only=False,
    )

    digest_a = evidence_set_module._ref_digest(ref_a)
    digest_b = evidence_set_module._ref_digest(ref_b)

    assert digest_a == digest_b, (
        "Per-ref digest MUST depend only on (ref_id, digest, page_refs) "
        "per doc-13:186 verbatim; adding optional metadata fields like "
        "slice_id / journal_anchor must NOT change the digest."
    )


def test_per_ref_digest_changes_when_page_refs_change() -> None:
    """Per doc-13:186 verbatim ("source ids and content digests").

    Two refs with the same ``(ref_id, digest)`` but DIFFERENT
    ``page_refs`` MUST produce different per-ref digests (the page_refs
    are part of the doc-13:186 ``(ref_id, digest, page_refs)`` digest
    payload).
    """

    page_a = _make_page_ref(
        page_ref_id="page-1",
        digest="sha256:page-A-digest",
        completeness="paged",
        exact=True,
    )
    page_b = _make_page_ref(
        page_ref_id="page-1",
        digest="sha256:page-B-DIFFERENT",
        completeness="paged",
        exact=True,
    )

    ref_with_page_a = _make_ref(
        authority="typed_journal",
        ref_id="ref-shared",
        digest="sha256:shared",
        completeness="paged",
        page_refs=[page_a],
    )
    ref_with_page_b = _make_ref(
        authority="typed_journal",
        ref_id="ref-shared",
        digest="sha256:shared",
        completeness="paged",
        page_refs=[page_b],
    )

    digest_a = evidence_set_module._ref_digest(ref_with_page_a)
    digest_b = evidence_set_module._ref_digest(ref_with_page_b)

    assert digest_a != digest_b


def test_per_ref_digest_is_invariant_under_page_ref_reordering() -> None:
    """Reordering the page_refs list within a single ref MUST NOT change
    the per-ref digest (the digester sorts page_refs by page_ref_id
    before hashing per the module docstring)."""

    page_1 = _make_page_ref(
        page_ref_id="page-1",
        digest="sha256:p1",
        completeness="paged",
        exact=True,
    )
    page_2 = _make_page_ref(
        page_ref_id="page-2",
        digest="sha256:p2",
        completeness="paged",
        exact=True,
    )

    ref_forward = _make_ref(
        authority="typed_journal",
        ref_id="ref-shared",
        digest="sha256:shared",
        completeness="paged",
        page_refs=[page_1, page_2],
    )
    ref_reverse = _make_ref(
        authority="typed_journal",
        ref_id="ref-shared",
        digest="sha256:shared",
        completeness="paged",
        page_refs=[page_2, page_1],
    )

    assert (
        evidence_set_module._ref_digest(ref_forward)
        == evidence_set_module._ref_digest(ref_reverse)
    )


# ── real-shape anchors: live journal + decision log ────────────────────────


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
def test_real_anchors_compose_yields_stable_idempotency_key_across_two_runs() -> None:
    """Per the user prompt's chunk-shape point 8 (real-shape: feed real
    anchors from the live ``implementation-journal.md`` +
    ``implementation-decisions.jsonl``, confirm digest is stable across
    two runs / idempotency).

    Reads the live fixtures via the 13c + 13d parsers, composes the
    evidence set TWICE, asserts the idempotency_key is byte-identical.
    Confirms the digester operates on real-shape input without raising
    AND that the key is reproducible across compose calls.
    """

    journal_anchors = parse_implementation_journal(_REAL_JOURNAL_PATH)
    decision_anchors = parse_implementation_decision_log(_REAL_DECISIONS_PATH)

    first = compose_governance_evidence_set(
        journal_anchors=journal_anchors,
        decision_log_anchors=decision_anchors,
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )
    second = compose_governance_evidence_set(
        journal_anchors=journal_anchors,
        decision_log_anchors=decision_anchors,
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    assert first.idempotency_key == second.idempotency_key, (
        "Real-anchor digester MUST be idempotent across two compose "
        "calls over the same parsed anchor lists."
    )
    # Real-anchor compose produces a non-empty refs list (the live
    # journal + decisions have many anchors).
    assert len(first.refs) > 0
    # Completeness is complete (the parser anchors are single-line
    # atomic citations).
    assert first.completeness in ("complete", "paged")
    # **Slice 13A first sub-slice (criterion (e) enforcement).** The
    # live journal contains hundreds of P1/P2 finding IDs in heading-
    # clause + per-finding anchor ``open_findings`` lists per the 13c
    # parser convention at ``journal_parser.py:507-527``. The Slice
    # 13A first sub-slice digester emits canonical
    # ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
    # blockers for each such finding + downgrades the set-level
    # ``quality`` to ``insufficient`` per doc-13:217 fail-closed
    # projection. The set is typed-first only (no legacy authorities)
    # but the open-findings blockers (hard non-legacy class) force the
    # set to ``insufficient``. The idempotency_key invariant is
    # preserved across runs (the new blockers are deterministic
    # projections of the same parsed anchor list).
    assert first.quality == "insufficient", (
        f"Slice 13A first sub-slice criterion (e) enforcement: live "
        f"journal P1/P2 findings drive open-findings-class blockers + "
        f"set-level insufficient. Got quality={first.quality!r}."
    )
    assert first.blockers, (
        "Slice 13A first sub-slice criterion (e) enforcement: live "
        "journal contains P1/P2 findings that drive non-empty "
        "governance_evidence_gap:open_findings:* blockers."
    )
    # Every blocker MUST be the canonical governance_evidence_gap form
    # per doc-13:209-210 (P3-13e-3 closure).
    for blocker in first.blockers:
        assert blocker.startswith("governance_evidence_gap:"), (
            f"Slice 13A first sub-slice P3-13e-3 closure: blocker MUST "
            f"start with the canonical 'governance_evidence_gap:' "
            f"shape per doc-13:209-210 (got {blocker!r})"
        )


# ── empty-but-budget-exhausted: completeness=unavailable + insufficient ──


def test_empty_input_with_budget_exhausted_yields_unavailable_and_insufficient() -> None:
    """Per doc-13:215-220 ("Read budget exhausted: return the partial
    evidence set with read_budget_exhausted=True ... mark completeness
    paged or unavailable").

    Zero refs + read_budget_exhausted=True -> unavailable + insufficient
    (the user prompt's chunk-shape point 8 "empty refs + budget
    exhausted" case is the same as the plain empty-input case but with
    the budget-exhausted flag set).
    """

    result = compose_governance_evidence_set(
        journal_anchors=[],
        decision_log_anchors=[],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
        read_budget_exhausted=True,
    )

    assert result.completeness == "unavailable"
    assert result.quality == "insufficient"
    assert result.read_budget_exhausted is True
    assert result.refs == []
    assert result.blockers == []


def test_read_budget_exhausted_flag_propagates_to_result() -> None:
    """The ``read_budget_exhausted`` constructor argument propagates
    verbatim to the result's :attr:`read_budget_exhausted` field, even
    when the set contains refs (per doc-13:215-218 the digester surfaces
    the upstream readers' budget-exhausted signal as a typed field on
    the set; downstream consumers gate their authority claim on it)."""

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
        ),
    ]

    not_exhausted = _compose_with_refs(refs, read_budget_exhausted=False)
    exhausted = _compose_with_refs(refs, read_budget_exhausted=True)

    assert not_exhausted.read_budget_exhausted is False
    assert exhausted.read_budget_exhausted is True
    # The flag does NOT influence the idempotency_key -- it is a
    # set-level metadata flag, not part of the per-ref content digest
    # contract. Two compositions over the same refs with different
    # exhaustion flags produce the same key.
    assert not_exhausted.idempotency_key == exhausted.idempotency_key


# ── source_mix counts (doc-13:137 + 13a validator) ─────────────────────────


def test_source_mix_counts_refs_per_authority() -> None:
    """Per doc-13:137 + the 13a ``_source_mix_counts_non_negative``
    validator at ``models.py:379-393``.

    The ``source_mix`` map counts the number of refs per authority
    after dedup.
    """

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-T1", digest="sha256:d1"
        ),
        _make_ref(
            authority="typed_journal", ref_id="ref-T2", digest="sha256:d2"
        ),
        _make_ref(
            authority="typed_journal", ref_id="ref-T3", digest="sha256:d3"
        ),
        _make_ref(
            authority="git_provenance",
            ref_id="ref-G1",
            digest="sha256:dG1",
        ),
        _make_ref(
            authority="supervisor_digest",
            ref_id="ref-S1",
            digest="sha256:dS1",
        ),
    ]
    result = _compose_with_refs(refs)

    assert result.source_mix == {
        "typed_journal": 3,
        "git_provenance": 1,
        "supervisor_digest": 1,
    }


def test_source_mix_after_dedup_is_post_dedup_count() -> None:
    """Per-ref dedup runs BEFORE source_mix counting so a duplicate
    ref is counted only once."""

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:FIRST"
        ),
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:DUP"
        ),
        _make_ref(
            authority="typed_journal",
            ref_id="ref-2",
            digest="sha256:second",
        ),
    ]
    result = _compose_with_refs(refs)

    # 3 source refs -> 2 deduped refs (ref-1 dedup keeps first) -> count 2.
    assert result.source_mix == {"typed_journal": 2}
    assert len(result.refs) == 2


# ── corpus_id + feature_id + omitted_refs propagation ─────────────────────


def test_corpus_id_and_feature_id_propagate_to_result() -> None:
    """The ``corpus_id`` and ``feature_id`` kwargs propagate verbatim to
    the result. The 13a model validates non-emptiness of ``corpus_id``
    (per the ``_non_empty_set_id_fields`` ID-field validator on
    :class:`GovernanceEvidenceSet`); ``feature_id`` is optional
    (``str | None``).
    """

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
        ),
    ]
    result = compose_governance_evidence_set(
        journal_anchors=[],
        decision_log_anchors=[],
        supervisor_digest_refs=refs,
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
        corpus_id="my-corpus-id-13e",
        feature_id="feature-abc123",
    )

    assert result.corpus_id == "my-corpus-id-13e"
    assert result.feature_id == "feature-abc123"


def test_omitted_refs_propagate_to_result_default_empty() -> None:
    """The ``omitted_refs`` kwarg propagates verbatim; defaults to empty
    list (per doc-13:216-218: 'populate omitted_refs as exact page refs
    when possible'; an empty list is legitimate when nothing was
    omitted)."""

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
        ),
    ]
    omitted = [
        _make_page_ref(
            page_ref_id="omitted-page-1",
            source_ref_id="ref-omitted",
            completeness="paged",
            exact=True,
        ),
    ]

    with_omitted = _compose_with_refs(refs, omitted_refs=omitted)
    without_omitted = _compose_with_refs(refs)

    assert len(with_omitted.omitted_refs) == 1
    assert with_omitted.omitted_refs[0].page_ref_id == "omitted-page-1"
    assert without_omitted.omitted_refs == []


# ── source_window projection from GovernanceWindow ─────────────────────────


def test_source_window_projects_governance_window_to_dict() -> None:
    """The ``source_window`` field is the dict projection of the input
    :class:`GovernanceWindow` (per the 13a
    :attr:`GovernanceEvidenceSet.source_window: dict[str, Any]` field
    shape at doc-13:133)."""

    window = GovernanceWindow(
        start_cursor="cursor-START",
        end_cursor="cursor-END",
        start_iso="2026-05-24T00:00:00Z",
        end_iso="2026-05-24T23:59:59Z",
        selectors={"feature_id": "abc123"},
    )

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
        ),
    ]
    result = compose_governance_evidence_set(
        journal_anchors=[],
        decision_log_anchors=[],
        supervisor_digest_refs=refs,
        resource_snapshot_refs=[],
        window=window,
        read_budget=_default_budget(),
    )

    assert result.source_window["start_cursor"] == "cursor-START"
    assert result.source_window["end_cursor"] == "cursor-END"
    assert result.source_window["selectors"] == {"feature_id": "abc123"}


# ── anchor projection: 13c + 13d anchors yield typed-first refs ───────────


def test_journal_anchors_project_to_implementation_journal_authority_refs() -> None:
    """The digester's anchor-to-ref bridge projects 13c journal anchors to
    refs with ``authority="implementation_journal"`` (per the doc-13:74-84
    enum mapping) and ``quality="canonical"`` /
    ``completeness="complete"`` (each anchor is a single-line atomic
    citation, no paging)."""

    journal_anchor = ImplementationArtifactAnchor(
        slice_id="13e",
        journal_path="implementation-journal.md",
        line_start=42,
        decision_log_line=None,
        event="complete",
        accepted=False,
        open_findings=[],
    )

    result = compose_governance_evidence_set(
        journal_anchors=[journal_anchor],
        decision_log_anchors=[],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    assert len(result.refs) == 1
    assert result.refs[0].authority == "implementation_journal"
    assert result.refs[0].quality == "canonical"
    assert result.refs[0].completeness == "complete"
    # The slice_id is propagated from the anchor.
    assert result.refs[0].slice_id == "13e"


def test_decision_log_anchors_project_to_implementation_decision_log_authority_refs() -> None:
    """13d decision-log anchors project to refs with
    ``authority="implementation_decision_log"``."""

    decision_anchor = ImplementationArtifactAnchor(
        slice_id="13e",
        journal_path="implementation-decisions.jsonl",
        line_start=None,
        decision_log_line=1138,
        event="starting",
        accepted=False,
        open_findings=[],
    )

    result = compose_governance_evidence_set(
        journal_anchors=[],
        decision_log_anchors=[decision_anchor],
        supervisor_digest_refs=[],
        resource_snapshot_refs=[],
        window=_empty_window(),
        read_budget=_default_budget(),
    )

    assert len(result.refs) == 1
    assert result.refs[0].authority == "implementation_decision_log"
    assert result.refs[0].quality == "canonical"
    assert result.refs[0].completeness == "complete"


# ── ConfigDict extra='forbid' on GovernanceEvidenceSet preserved ──────────


def test_composed_set_does_not_introduce_extra_fields_on_governance_evidence_set() -> None:
    """The 13a :class:`GovernanceEvidenceSet` declares
    ``model_config = ConfigDict(extra='forbid')`` -- the digester's
    constructor call MUST NOT pass any kwarg the model does not declare.

    A successful round-trip with the strict ``extra='forbid'`` config
    proves the digester does not silently extend the typed surface
    (the 13a model is FROZEN for 13e per the user prompt's
    non-negotiables)."""

    refs = [
        _make_ref(
            authority="typed_journal", ref_id="ref-1", digest="sha256:d1"
        ),
    ]
    composed = _compose_with_refs(refs)

    # ``model_dump`` projects only the declared fields; ``extra='forbid'``
    # rejects construction with unknown kwargs. The fact that the
    # composition above succeeded with the 13a model config intact is
    # the test.
    fields_in_dump = set(composed.model_dump().keys())
    expected_fields = {
        "idempotency_key",
        "feature_id",
        "corpus_id",
        "generated_at",
        "source_window",
        "refs",
        "omitted_refs",
        "completeness",
        "source_mix",
        "read_budget",
        "read_budget_exhausted",
        "quality",
        "blockers",
    }
    assert fields_in_dump == expected_fields
