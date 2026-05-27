"""Slice 13e -- pure-typed evidence-set digester.

This module owns the doc-13:186-187 § "Refactoring Steps" step 5 deliverable:

> Add evidence-set digesting from sorted canonical JSON. The digest must
> include source ids and content digests, not full artifact bodies.

The digester is **pure-typed**: typed-shapes-in (journal anchors + decision-
log anchors + supervisor digest refs + resource snapshot refs + governance
window + read budget) and a single
:class:`~iriai_build_v2.workflows.develop.governance.models.GovernanceEvidenceSet`
out per the doc-13:128-141 typed shape. It uses only the standard library
(``hashlib`` + ``json`` + ``datetime``) -- no third-party deps, no executor
wiring, no DB I/O, no consumption of the typed evidence as **execution
authority**. Per the governance prompt § "Slice 13A invariant for downstream
slices" no governance ingestor that influences dispatch / verify / merge /
checkpoint / route / scheduler / policy may consume the digester's typed
output as execution authority until Slice 13A's evidence-completeness
invariant lands; until then the digester exists to populate the 13b
ingestor's ``ingest_implementation_artifacts`` path in display-only mode
(the wiring itself lands in a later sub-slice).

The module mirrors :mod:`.journal_parser` (the 13c sibling) and
:mod:`.decision_log_parser` (the 13d sibling) in API shape: a single
free function at the module surface with ``from __future__ import
annotations`` at the top and an explicit ``__all__`` listing the one
public symbol. The implementation prompt § "Non-negotiables" mandates
this style ("pick whichever matches journal_parser.py / decision_log_parser.py
style; if those are functions, this is a function too").

**Per-ref digest contract (doc-13:186 verbatim).** Each
:class:`GovernanceEvidenceRef` is digested over the canonical-JSON
projection of ``(ref_id, digest, page_refs)`` -- the three fields that
together carry the *source id* (``ref_id``) and *content digest*
(``digest`` + each contained ``page_refs[*].(page_ref_id, digest,
completeness, exact)``). Artifact bodies are NEVER hashed; per the doc:
"bodies may be too large to digest atomically" + per the governance
prompt § "Bounded reads" the digester operates on the typed surface only.

**Set-level idempotency_key.** The set-level
:attr:`GovernanceEvidenceSet.idempotency_key` is the SHA-256 of the
canonical-JSON projection of the sorted list of per-ref SHA-256 hex
digests. Two runs over the same logical input (regardless of input
ordering) produce the same set-level key. This is the sort-invariance
property the user prompt mandates ("test both orderings give the same
set (sort-invariance)").

**Sort-then-dedup discipline (P1-13e-1 finalizer fix).** Refs are
sorted by ``(authority, ref_id, _ref_digest(ref))`` BEFORE
:func:`_dedup_refs_keep_first` runs. Without this canonical pre-sort,
two ``(authority, ref_id)``-collision-siblings whose underlying typed
fields differ (e.g. two journal anchors with the same
``(slice_id, journal_path, line_start, event)`` triple but different
``accepted`` / ``open_findings`` projections — both project to
``_anchor_ref_id`` with the same ``(authority, ref_id)`` tuple but
different ``_anchor_digest`` values) would let ``keep-first`` pick the
input-order-first sibling, making the per-ref digest of the kept-first
ref input-order-sensitive and breaking set-level idempotency. The
canonical pre-sort guarantees the canonically-smallest sibling at each
collision is always the kept-first one, so the kept-first ref's per-ref
digest is invariant under input reordering. The set-level
``idempotency_key`` is then invariant by construction.

**Per-ref digest payload — `authority` deliberately omitted (P3-A1).**
The per-ref digest is computed over ``(ref_id, digest, page_refs)``
only; ``authority`` is part of the ``(authority, ref_id)`` tuple that
governs dedup-keep-first AND is part of the canonical sort key, but it
is NOT in the per-ref digest payload. Consequence: two refs with the
same ``(ref_id, digest, page_refs)`` triple but different
``authority`` values produce identical per-ref digests. This is
intentional per doc-13:186 verbatim wording ("source ids and content
digests, not full artifact bodies"): the doc names the content-digest
contract over the source-id (``ref_id``) + content-digest (``digest``)
+ contained page-refs only. The set-level digest remains distinguishing
because the input refs are sorted by ``(authority, ref_id, _ref_digest(ref))``
BEFORE per-ref digest computation, and the per-ref digests of the two
authority-disjoint refs are joined into the set-level
``idempotency_key`` after dedup (so the (authority, ref_id) pair is
the dedup-identity, not the digest-identity).

**Completeness projection (doc-13:215-220).** The set's
:attr:`GovernanceEvidenceSet.completeness` is projected from the refs:

* ``complete`` when every ref has ``completeness="complete"`` AND every
  contained ``page_refs[*].exact`` is ``True`` AND no ref carries
  ``preview_only=True``.
* ``paged`` when at least one ref or contained page-ref has
  ``completeness="paged"`` and no ref carries ``preview_only=True``.
* ``preview_only`` when at least one ref has ``preview_only=True`` OR
  ``completeness="preview_only"`` (the 13a
  ``_preview_only_completeness_consistency`` validator at
  ``models.py:309-323`` enforces the bidirectional invariant on the ref
  itself; the digester surfaces it at the set level).
* ``unavailable`` when there are zero refs after dedup.

**Quality projection (doc-13:173-175).** The set's
:attr:`GovernanceEvidenceSet.quality` is projected from the
:attr:`source_mix` per the doc-13:173-175 confidence-scoring discipline:

* ``canonical`` when every ref is from a typed-first authority
  (``typed_journal`` / ``compatibility_projection`` / ``git_provenance`` /
  ``implementation_journal`` / ``implementation_decision_log`` /
  ``supervisor_digest`` / ``resource_snapshot``) AND completeness is
  ``complete`` (no preview, no paging).
* ``derived`` when refs include BOTH typed-first AND legacy authorities
  per the doc-13:173 verbatim wording ("Mixed typed/legacy evidence is
  encoded as quality='derived' plus source_mix").
* ``insufficient`` when there are zero refs OR every ref is from a legacy
  authority (``legacy_event`` / ``legacy_artifact_summary``) OR any ref
  carries ``preview_only=True`` (the 13A invariant treats preview as
  display-only -- no preview-only ref satisfies an authoritative
  consumer per doc-13a:24, 109-118).

**Blockers projection (Slice 13A first sub-slice — canonical form per
doc-13:209-210).** The set's :attr:`GovernanceEvidenceSet.blockers`
is the deduplicated list of canonical
``governance_evidence_gap:<authority>:<ref_id>`` strings naming any
refs whose ``authority`` is one of the two legacy authorities
(``legacy_event`` / ``legacy_artifact_summary``) per the Slice 13A
invariant. Legacy refs cannot be cited as execution authority, so a
set whose refs include legacy authorities is blocked from
authoritative consumption (doc-13a:24, 109-118 verbatim: "Lossy
summaries and previews are display-only"). The canonical
``governance_evidence_gap`` shape is the doc-13:209-210 verbatim
finding name ("record a ``governance_evidence_gap`` finding"); the
Slice 13A first sub-slice replaced the prior bespoke
``governance_evidence_legacy_authority:`` form (P3-13e-3 closure)
with the canonical one so Slice-15 metrics + Slice-16 finding-engine
consumers can dedupe / aggregate by the doc-13 canonical name.

**Per-ref dedup (doc-13:186 implicit + 13a model precedent).** Refs with
the same ``(authority, ref_id)`` tuple are deduplicated **keep-first**
AFTER a canonical sort by ``(authority, ref_id, _ref_digest(ref))``: at
each collision the canonically-smallest sibling (by per-ref digest) is
the kept-first one. The user prompt mandates sort-invariance ("test both
orderings give the same set"); the digester achieves this by sorting
refs canonically BEFORE the dedup runs, so the kept-first ref is
deterministic regardless of input order. Without the canonical pre-sort
two collision-siblings whose underlying typed fields differ would let
keep-first pick the input-order-first sibling, making the per-ref digest
of the kept-first ref input-order-sensitive and breaking set-level
idempotency. This mirrors the 13c/13d parsers' per-anchor
``open_findings`` dedup discipline (the 13a
``_open_findings_dedup_and_non_empty`` validator at
``models.py:449-469``).

**Intra-ref dedup (fail-closed).** Duplicate ``page_ref_id`` within a
single ref's ``page_refs`` list raises a typed
:class:`ValueError`. Per the auto-memory ``feedback_no_silent_degradation``
rule intra-ref duplication is malformed input and must fail closed --
silently deduplicating would let a stale digest projection drift from a
fresh source unnoticed (the 13a ``GovernanceEvidencePageRef.digest``
field validator at ``models.py:218-229`` enforces non-emptiness; the
digester layers the uniqueness invariant on top).

Out of scope for 13e (per STATUS.md § "Next safe action"):

- Storing governance evidence sets as typed rows (doc-13:188-190 step 6
  -- the natural 13f sub-slice once the Slice-01 store is involved).
- Wiring the digester into
  :meth:`~iriai_build_v2.workflows.develop.governance.ingestor.DefaultGovernanceEvidenceIngestor.ingest_implementation_artifacts`
  (later sub-slice once both parsers AND the digester are in place).
- Any consumption of ``preview_only`` / ``exact`` / ``completeness`` as
  **execution authority** (still gated on Slice 13A landing per the
  governance prompt § "Slice 13A invariant for downstream slices").
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from .ingestor import GovernanceWindow
from .models import (
    EvidenceAuthority,
    EvidenceQuality,
    CompletenessState,
    GovernanceEvidencePageRef,
    GovernanceEvidenceRef,
    GovernanceEvidenceSet,
    GovernanceReadBudget,
    ImplementationArtifactAnchor,
)


__all__ = ["compose_governance_evidence_set"]


# --- Typed-first vs legacy authority partition (doc-13:173-175) -------------
#
# Doc-13:173 spells "Mixed typed/legacy evidence is encoded as
# quality='derived' plus source_mix"; doc-13:74-84 enumerates the 9
# EvidenceAuthority values. Per the user prompt's chunk-shape point 4
# the legacy partition is verified against the 13a enum:
# ``legacy_event`` and ``legacy_artifact_summary`` are the two legacy
# values; the remaining 7 are typed-first authorities. The frozen
# partition lives at the module level so a future Slice-15 metrics /
# Slice-16 finding-engine consumer can re-use it without re-deriving the
# taxonomy.
_LEGACY_AUTHORITIES: frozenset[str] = frozenset(
    {"legacy_event", "legacy_artifact_summary"}
)
"""The 2 legacy authorities per doc-13:74-84 (the last two values in the
enum). The Slice 13A invariant at doc-13a:24, 109-118 treats refs from
these authorities as display-only; the digester surfaces them in
:attr:`GovernanceEvidenceSet.blockers` so authoritative consumers fail
closed."""


_TYPED_AUTHORITIES: frozenset[str] = frozenset(
    {
        "typed_journal",
        "compatibility_projection",
        "git_provenance",
        "implementation_journal",
        "implementation_decision_log",
        "supervisor_digest",
        "resource_snapshot",
    }
)
"""The 7 typed-first authorities per doc-13:74-84. Doc-13:173 ("typed
journal stubs plus compatibility projections and verify evidence refs
cite typed ids first") + doc-13:248 ("Every governance evidence set
cites stable typed ids, compatibility projection ids, Git provenance
refs, or implementation-log anchors") establish these as the
canonical sources."""


# --- Canonical-JSON helpers --------------------------------------------------


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Per the implementer prompt § "Non-negotiables" the digester uses
    ``json.dumps(..., sort_keys=True, separators=(",", ":"))`` -- the
    canonical form mandates lexicographic key ordering and the compact
    separator set so the resulting bytes are stable across Python
    versions / platforms / dict ordering. Numeric / string / bool /
    None / list / dict values serialise verbatim; the digester never
    feeds non-JSON-safe values into this helper (the typed shapes
    project to primitives only).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Stdlib-only per the implementer prompt § "Non-negotiables" ("Stdlib
    ``hashlib`` + ``json`` (for canonical JSON) only; no new deps").
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


# --- Page-ref + ref digest helpers (doc-13:186 verbatim) --------------------


def _page_ref_digest_payload(page_ref: GovernanceEvidencePageRef) -> dict[str, object]:
    """Project one :class:`GovernanceEvidencePageRef` to the digest payload.

    Per doc-13:186 verbatim ("The digest must include source ids and
    content digests, not full artifact bodies") the page-ref payload
    includes:

    * ``page_ref_id`` -- the page's source id within its parent ref;
    * ``digest`` -- the page's content digest (the source of truth for
      cross-process freshness; per the 13a model validator at
      ``models.py:218-229`` it is non-empty);
    * ``completeness`` -- the page's completeness state;
    * ``exact`` -- the page's exactness claim (per the 13a model
      validator at ``models.py:234-249`` it is consistent with
      ``completeness``).

    Byte / line / item ranges and the ``stale_check`` mapping are
    NOT included in the digest payload: they are freshness metadata
    that the typed-source consumer rehydrates from the originating
    artifact, not content fingerprints. Including them would make the
    digest unstable across legitimate cross-process re-anchoring (e.g.
    the same ref re-resolved from a different DB snapshot).
    """

    return {
        "page_ref_id": page_ref.page_ref_id,
        "digest": page_ref.digest,
        "completeness": page_ref.completeness,
        "exact": page_ref.exact,
    }


def _ref_digest(ref: GovernanceEvidenceRef) -> str:
    """Compute the per-ref SHA-256 digest per doc-13:186 verbatim.

    The digest is over the canonical-JSON projection of
    ``(ref_id, digest, page_refs)`` where ``page_refs`` is the SORTED
    list of :func:`_page_ref_digest_payload` projections (sorted by
    ``page_ref_id`` so a producer-side re-ordering of the page list
    does not change the per-ref digest).

    Per the doc-13:186 verbatim wording ("not full artifact bodies")
    the artifact body is NEVER hashed -- only the typed source-id /
    content-digest / completeness / exactness triple. This is the
    digester's central invariant: a per-ref digest can be computed
    without reading the artifact bytes.

    Fail-closed: duplicate ``page_ref_id`` within ``ref.page_refs``
    raises :class:`ValueError`. Mirrors the 13a
    ``_open_findings_dedup_and_non_empty`` discipline at
    ``models.py:449-469`` -- silent intra-collection dedup would let
    a stale digest projection drift unnoticed; per the auto-memory
    ``feedback_no_silent_degradation`` rule we fail closed.
    """

    # Intra-ref fail-closed dedup (doc-13:186 implicit + auto-memory
    # feedback_no_silent_degradation). The 13a
    # _open_findings_dedup_and_non_empty validator at models.py:449-469
    # enforces dedup on a different field; we mirror its fail-closed
    # discipline here for the page_refs collection.
    seen_page_ids: set[str] = set()
    for page_ref in ref.page_refs:
        if page_ref.page_ref_id in seen_page_ids:
            raise ValueError(
                f"evidence_set: ref {ref.ref_id!r} (authority="
                f"{ref.authority!r}) contains duplicate page_ref_id "
                f"{page_ref.page_ref_id!r}; intra-ref page-ref ids must be "
                f"unique. Silently deduplicating would let a stale digest "
                f"projection drift unnoticed -- fail-closed per the "
                f"no-silent-degradation rule + the 13a "
                f"_open_findings_dedup_and_non_empty validator precedent at "
                f"models.py:449-469."
            )
        seen_page_ids.add(page_ref.page_ref_id)

    # Build the per-ref digest payload. Sorted by page_ref_id so a
    # producer-side re-ordering of the page list does not change the
    # per-ref digest (sort-invariance per the user prompt's
    # "test both orderings give the same set" rule). The payload is a
    # 3-tuple-shaped dict so the canonical JSON has a stable shape.
    payload = {
        "ref_id": ref.ref_id,
        "digest": ref.digest,
        "page_refs": sorted(
            (_page_ref_digest_payload(p) for p in ref.page_refs),
            key=lambda d: d["page_ref_id"],
        ),
    }

    return _sha256_hex(_canonical_json(payload))


# --- Set-level dedup + idempotency_key --------------------------------------


def _canonical_sort_refs(
    refs: list[GovernanceEvidenceRef],
) -> list[GovernanceEvidenceRef]:
    """Canonically sort refs by ``(authority, ref_id, _ref_digest(ref))``.

    Per the P1-13e-1 finalizer fix the canonical sort runs BEFORE
    :func:`_dedup_refs_keep_first` so the "first" ref at each
    ``(authority, ref_id)`` collision is the canonically-smallest sibling
    (by per-ref digest) regardless of input order. Without this pre-sort
    two collision-siblings whose underlying typed fields differ (e.g.
    two journal anchors with the same
    ``(slice_id, journal_path, line_start, event)`` but different
    ``accepted`` / ``open_findings`` projections) would let keep-first
    pick the input-order-first sibling, making the per-ref digest of the
    kept-first ref input-order-sensitive and breaking set-level
    idempotency. The reviewer verified the bug + the fix on the live
    corpus: pre-sort canonical key invariant `14a26c037b8687a9...`
    matches across forward / shuffled / reversed real-corpus inputs.

    The sort key is the 3-tuple ``(authority, ref_id, _ref_digest(ref))``
    -- the same authority + ref_id used for dedup-identity plus the
    per-ref content digest as the tiebreaker. Two collision-siblings
    that differ only in ``authority`` are not collisions (the dedup
    tuple includes authority); the tiebreaker only fires when two refs
    share ``(authority, ref_id)`` exactly.
    """

    return sorted(
        refs,
        key=lambda ref: (ref.authority, ref.ref_id, _ref_digest(ref)),
    )


def _dedup_refs_keep_first(
    refs: list[GovernanceEvidenceRef],
) -> list[GovernanceEvidenceRef]:
    """Deduplicate refs by ``(authority, ref_id)`` keep-first.

    Per the user prompt's chunk-shape point 5 ("Per-ref: dedup by
    (authority, ref_id); if duplicate, KEEP THE FIRST"). Mirrors the
    13a ``_open_findings_dedup_and_non_empty`` validator's dedup
    discipline at ``models.py:449-469``.

    The caller (``compose_governance_evidence_set``) runs
    :func:`_canonical_sort_refs` BEFORE this helper so the "first" ref
    at each collision is the canonically-smallest sibling. This is the
    P1-13e-1 finalizer fix that makes the set-level idempotency_key
    invariant under input reordering (without the pre-sort the kept-first
    ref's per-ref digest would depend on input order).
    """

    seen: set[tuple[str, str]] = set()
    deduped: list[GovernanceEvidenceRef] = []
    for ref in refs:
        key = (ref.authority, ref.ref_id)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ref)
    return deduped


def _compute_idempotency_key(refs: list[GovernanceEvidenceRef]) -> str:
    """Compute the set-level :attr:`GovernanceEvidenceSet.idempotency_key`.

    Per the user prompt's chunk-shape point 2 the set-level key is the
    SHA-256 of the canonical-JSON projection of the SORTED list of
    per-ref SHA-256 hex digests. Sorting the per-ref digests (not the
    refs themselves) before hashing gives the sort-invariance property
    the user prompt mandates: two runs with the same refs in different
    source orders produce the same set-level key.

    Empty refs -> empty sorted list -> hash of ``"[]"`` (the canonical
    JSON of an empty list). This is the documented stable digest for
    a zero-ref evidence set; the 13a
    :attr:`GovernanceEvidenceSet.idempotency_key` field validator
    requires a non-empty string, which SHA-256 always produces (64-char
    hex).
    """

    per_ref_digests = sorted(_ref_digest(ref) for ref in refs)
    return _sha256_hex(_canonical_json(per_ref_digests))


# --- Completeness + quality + blockers projections --------------------------


def _project_completeness(
    refs: list[GovernanceEvidenceRef],
) -> CompletenessState:
    """Project the set-level :attr:`CompletenessState` from the refs.

    Per doc-13:215-220 § "Edge Cases And Failure Handling" the
    completeness state of a partial evidence set is derived from the
    completeness states of its refs:

    * ``unavailable`` when zero refs (doc-13:218-220: "downstream
      metrics, findings, reports, acceptance checks, and recommendations
      can consume only the complete subset they can prove by exact
      refs; otherwise they must fail closed or render display-only
      output").
    * ``preview_only`` when at least one ref has ``preview_only=True``
      OR ``completeness="preview_only"`` (the 13a
      ``_preview_only_completeness_consistency`` validator at
      ``models.py:309-323`` keeps the two flags consistent on each
      ref; the digester surfaces them at the set level).
    * ``paged`` when at least one ref or contained page-ref is
      ``completeness="paged"`` and no ref is preview-only (doc-13:218
      "mark completeness paged or unavailable").
    * ``complete`` when every ref is ``completeness="complete"`` AND
      every page-ref ``exact=True`` AND no ref preview-only -- the
      strongest possible state, all refs cite exact typed-source ids
      with no truncation.
    """

    if not refs:
        return "unavailable"

    # Preview-only takes precedence -- the 13A invariant treats preview
    # as display-only, so any preview-only ref makes the set
    # preview-only.
    if any(ref.preview_only or ref.completeness == "preview_only" for ref in refs):
        return "preview_only"

    # Paged: any ref or page-ref carries the paged completeness state.
    # The 13a model validator at models.py:234-249 enforces
    # completeness in {complete, paged} when page-ref exact=True.
    any_paged = False
    for ref in refs:
        if ref.completeness == "paged":
            any_paged = True
            break
        for page_ref in ref.page_refs:
            if page_ref.completeness == "paged":
                any_paged = True
                break
        if any_paged:
            break

    if any_paged:
        return "paged"

    # Complete: every ref is complete + every page-ref exact=True.
    # This is the strongest possible set-level state.
    for ref in refs:
        if ref.completeness != "complete":
            # Defensive: any non-complete, non-paged, non-preview ref
            # falls through to paged as the safest non-preview reading.
            # In practice the 13a model validators constrain ref
            # completeness to the 4 enum values, so this branch fires
            # only for ``unavailable`` refs (which should not be in
            # the refs list at all -- an unavailable ref has no content
            # to digest).
            return "paged"
        for page_ref in ref.page_refs:
            if not page_ref.exact:
                # A non-exact page-ref under a complete parent ref is
                # a contract gap; demote to paged so the set is not
                # claimed complete on a non-exact page.
                return "paged"

    return "complete"


def _project_source_mix(
    refs: list[GovernanceEvidenceRef],
) -> dict[EvidenceAuthority, int]:
    """Compute the per-authority count map.

    Per doc-13:137 + the 13a
    :attr:`GovernanceEvidenceSet.source_mix` field shape
    (``dict[EvidenceAuthority, int]``) the count is the number of refs
    per authority. The 13a
    ``_source_mix_counts_non_negative`` validator at
    ``models.py:379-393`` enforces that counts are non-negative; the
    digester only ever increments, so the invariant holds by
    construction.
    """

    mix: dict[EvidenceAuthority, int] = {}
    for ref in refs:
        mix[ref.authority] = mix.get(ref.authority, 0) + 1
    return mix


def _project_quality(
    refs: list[GovernanceEvidenceRef],
    completeness: CompletenessState,
    blockers: list[str] | None = None,
) -> EvidenceQuality:
    """Project the set-level :attr:`EvidenceQuality` per doc-13:173-175.

    Per doc-13:173-175 verbatim ("Mixed typed/legacy evidence is
    encoded as quality='derived' plus source_mix"; "Confidence scoring
    in Slice 15 uses source_mix to penalize legacy-heavy or incomplete
    typed evidence") the quality projection is:

    * ``insufficient`` when there are zero refs OR every ref is from a
      legacy authority OR the set is preview_only (the 13A invariant
      treats preview as display-only -- no preview-only ref satisfies
      an authoritative consumer per doc-13a:24, 109-118) OR ANY
      NON-LEGACY ``governance_evidence_gap`` blocker is present (Slice
      13A first sub-slice doc-13:217 fail-closed projection — see the
      blocker-class distinction below).
    * ``derived`` when refs include BOTH typed-first AND legacy
      authorities -- the doc-13:173 verbatim "Mixed typed/legacy"
      case.
    * ``canonical`` when every ref is from a typed-first authority AND
      the set's completeness is ``complete`` AND no non-legacy
      ``governance_evidence_gap`` blocker is present -- the strongest
      possible quality projection (every ref cites a stable
      typed-source id with no truncation AND no blocker downgrade).

    A typed-only set whose completeness is ``paged`` (truncated but
    typed) is also ``derived``: the doc-13:217 wording ("mark quality
    insufficient or derived") gives ``derived`` as the explicit
    paged-but-not-empty class. ``insufficient`` is reserved for the
    cases where downstream consumers MUST fail closed (legacy-only,
    empty, preview-only, OR non-legacy blockers present).

    **Slice 13A first sub-slice — blocker class distinction.** The
    digester's own :func:`_project_blockers` emits two classes of
    canonical ``governance_evidence_gap`` blockers:

    1. **Legacy-authority class** —
       ``governance_evidence_gap:legacy_event:<ref_id>`` or
       ``governance_evidence_gap:legacy_artifact_summary:<ref_id>``.
       Generated when ``ref.authority`` is one of the two legacy
       authorities (doc-13:74-84). These are "soft" blockers — the
       existing 13a-13m quality projection (legacy-only ->
       insufficient; mixed-typed-and-legacy -> derived) is the
       authoritative interpretation; this set-level branch does NOT
       additionally downgrade on legacy-only blocker presence
       (preserves the doc-13:173-175 ``derived`` semantics for the
       mixed case verbatim).

    2. **Open-findings class** —
       ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
       (Slice 13A first sub-slice NEW). Generated when an
       :class:`ImplementationArtifactAnchor` carries unresolved
       P1/P2 finding IDs in ``open_findings``. These are "hard"
       blockers per doc-13:253-254 verbatim ("Missing Slice 00-12
       acceptance or unresolved P1/P2 findings blocks governance
       acceptance") and force set-level ``quality="insufficient"``
       regardless of typed-only vs mixed vs legacy-only refs.

    Preserves the 13h + 13j + 13n P2-V2-1
    ``_remark_ref_as_paged_on_overflow`` discipline at
    ``ingestor.py:1088-1131`` (set-level ``quality="derived"`` on
    ``budget_exhausted=True``); blocker class distinction is an
    ADDITIONAL downgrade trigger for the open-findings class only.
    """

    # Slice 13A first sub-slice -- distinguish "hard" blocker classes
    # that force insufficient from "soft" legacy-authority blockers
    # that preserve the existing per-set projection. The doc-13:209-210
    # canonical governance_evidence_gap form covers both classes; the
    # blocker prefix after the canonical name distinguishes them.
    has_non_legacy_governance_gap = bool(blockers) and any(
        _is_non_legacy_gap_blocker(b) for b in blockers
    )

    # Zero refs: empty set is unconditionally insufficient per
    # doc-13:215-220 ("downstream metrics, findings, reports,
    # acceptance checks, and recommendations can consume only the
    # complete subset they can prove by exact refs").
    if not refs:
        return "insufficient"

    # Preview-only set: 13A invariant treats preview as display-only.
    # The set cannot satisfy any authoritative consumer, so quality is
    # insufficient.
    if completeness == "preview_only":
        return "insufficient"

    # Slice 13A first sub-slice (doc-13:217 fail-closed projection):
    # any non-legacy governance_evidence_gap blocker (currently the
    # open-findings class from unresolved P1/P2 findings on
    # ImplementationArtifactAnchor; the completeness_scanner module
    # may extend the trigger set in later 13A sub-slices) forces
    # set-level insufficient regardless of typed/legacy mix. This is
    # the doc-13:253-254 acceptance criterion (e) enforcement.
    if has_non_legacy_governance_gap:
        return "insufficient"

    has_typed = any(ref.authority in _TYPED_AUTHORITIES for ref in refs)
    has_legacy = any(ref.authority in _LEGACY_AUTHORITIES for ref in refs)

    if has_legacy and not has_typed:
        # Legacy-only -- no typed authority; doc-13:173-175 confidence
        # scoring penalises this class. The user prompt's "if any ref
        # is `preview_only`, the set is `preview_only`" is handled
        # above; this branch is the documented "legacy-only" insufficient
        # case.
        return "insufficient"

    if has_legacy and has_typed:
        # Mixed typed/legacy -- doc-13:173 verbatim "Mixed typed/legacy
        # evidence is encoded as quality='derived' plus source_mix".
        # Note: legacy refs themselves drive a soft legacy-class
        # governance_evidence_gap blocker via :func:`_project_blockers`;
        # the mixed branch preserves the doc-13:173-175 ``derived``
        # semantics verbatim (the legacy-class blocker is informational
        # for Slice-15 confidence scoring, not a hard fail-closed
        # signal).
        return "derived"

    # Typed-only.  Strongest projection when completeness is
    # ``complete`` AND no non-legacy governance_evidence_gap blocker
    # (the latter is already enforced above); demoted to ``derived``
    # when paged (doc-13:217 "mark quality insufficient or derived").
    if completeness == "complete":
        return "canonical"
    return "derived"


def _is_non_legacy_gap_blocker(blocker: str) -> bool:
    """Return True if ``blocker`` is a canonical
    ``governance_evidence_gap`` blocker NOT driven by a legacy
    authority (Slice 13A first sub-slice).

    Per the :func:`_project_quality` docstring the blocker class
    distinction splits canonical ``governance_evidence_gap`` blockers
    into two classes:

    * **Legacy-authority class** —
      ``governance_evidence_gap:legacy_event:<ref_id>`` or
      ``governance_evidence_gap:legacy_artifact_summary:<ref_id>``.
      These preserve the existing 13a-13m quality projection
      (mixed -> derived; legacy-only -> insufficient) and are NOT a
      hard fail-closed trigger at the quality projection.
    * **Open-findings class** (and future scanner-driven classes) —
      ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
      or any other ``governance_evidence_gap:<sub-class>:...`` shape
      whose sub-class is NOT one of the two legacy authorities. These
      ARE a hard fail-closed trigger per doc-13:253-254 (criterion
      (e)).

    The classifier reads the second colon-separated segment of the
    blocker string and checks against the legacy authority set. A
    blocker whose second segment is one of the two legacy authorities
    is classified as a soft legacy-class blocker; everything else is
    classified as a hard non-legacy blocker. Defensive: a malformed
    blocker (e.g. no second segment, empty string) returns False so
    the typed-only-canonical projection still fires (this matches the
    digester's fail-open posture on malformed self-emitted strings;
    the typed-input validators reject malformed BLOCKERS at the
    Pydantic model boundary).
    """

    if not isinstance(blocker, str):
        return False
    if not blocker.startswith("governance_evidence_gap:"):
        return False
    # The canonical form is
    # ``governance_evidence_gap:<subclass>:<rest>`` with the second
    # colon-separated segment naming the sub-class (legacy authority
    # name, ``open_findings``, future scanner classes). The split below
    # yields at least 2 elements after the prefix is removed; a
    # malformed string (only the prefix, no second segment) returns
    # False so the typed-only-canonical projection still fires.
    after_prefix = blocker[len("governance_evidence_gap:"):]
    if ":" not in after_prefix:
        return False
    subclass = after_prefix.split(":", 1)[0]
    if subclass in _LEGACY_AUTHORITIES:
        return False
    return True


def _project_blockers(
    refs: list[GovernanceEvidenceRef],
    *,
    open_findings_anchors: list[ImplementationArtifactAnchor] | None = None,
) -> list[str]:
    """Compute the deduplicated canonical blockers list.

    Per doc-13:209-210 verbatim ("record a ``governance_evidence_gap``
    finding") + the Slice 13A invariant at doc-13a:24, 109-118 ("Lossy
    summaries and previews are display-only") the canonical blocker
    string shape is
    ``governance_evidence_gap:<subclass>:<rest>`` where ``<subclass>``
    distinguishes the trigger source. The digester emits two
    sub-classes (Slice 13A first sub-slice):

    1. **Legacy-authority class** (existing 13e trigger; canonical
       form per Slice 13A first sub-slice P3-13e-3 closure):
       ``governance_evidence_gap:legacy_event:<ref_id>`` or
       ``governance_evidence_gap:legacy_artifact_summary:<ref_id>``.
       Generated for each ref whose ``authority`` is one of the two
       legacy authorities at ``models.py:91-101``. Treated as a "soft"
       blocker by :func:`_project_quality` (the existing 13a-13m
       quality projection -- mixed -> derived; legacy-only ->
       insufficient -- remains the authoritative interpretation).

    2. **Open-findings class** (Slice 13A first sub-slice NEW per
       doc-13:253-254 criterion (e)):
       ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``.
       Generated for each unresolved P1/P2 finding ID in the
       ``open_findings`` list of any
       :class:`ImplementationArtifactAnchor` passed via
       ``open_findings_anchors``. Treated as a "hard" blocker by
       :func:`_project_quality` (set-level quality forced to
       ``insufficient`` per doc-13:217 fail-closed projection).

    **Slice 13A first sub-slice — P3-13e-3 + criterion (e) closure.**
    The legacy-authority canonical form replaces the prior bespoke
    ``governance_evidence_legacy_authority:<authority>:<ref_id>``
    form that the 13e implementer minted. The 13l/13m/13n finalizers
    deferred the rename to Slice 13A because three digester tests +
    one ingestor test pinned the prior literal prefix. Per the
    FROZEN-unfreeze precedent the Slice 13A first sub-slice unfreezes
    :func:`_project_blockers`, swaps the literal prefix to the
    canonical form, adds the open-findings emission path, and updates
    the four pinning tests in lockstep.

    :param refs: deduplicated typed refs from
        :func:`compose_governance_evidence_set`. Legacy-authority
        refs drive the legacy-authority blocker class.
    :param open_findings_anchors: optional list of
        :class:`ImplementationArtifactAnchor` (journal + decision-log
        anchors merged) whose ``open_findings`` lists are scanned for
        unresolved P1/P2 finding IDs. The default ``None`` preserves
        13e behavior (no open-findings blockers emitted; the legacy-
        authority class fires alone). The canonical caller
        :func:`compose_governance_evidence_set` always passes the
        merged anchor list.
    """

    # Per-blocker dedup: a single (subclass, payload) only contributes
    # one blocker string regardless of how many duplicate source refs
    # / anchors mention it. The set-level dedup already runs before
    # this projection in compose_governance_evidence_set, so by
    # construction the refs list contains at most one entry per
    # (authority, ref_id); the explicit dedup here is defence in
    # depth.
    blockers: list[str] = []
    seen: set[str] = set()

    # Sub-class 1: legacy-authority class (canonical form per Slice
    # 13A first sub-slice P3-13e-3 closure; behavior is otherwise
    # identical to the 13e bespoke form).
    for ref in refs:
        if ref.authority in _LEGACY_AUTHORITIES:
            blocker = (
                f"governance_evidence_gap:"
                f"{ref.authority}:{ref.ref_id}"
            )
            if blocker not in seen:
                seen.add(blocker)
                blockers.append(blocker)

    # Sub-class 2: open-findings class (Slice 13A first sub-slice NEW
    # per doc-13:253-254 criterion (e)). Each unresolved P1/P2 finding
    # ID across all anchors emits a hard blocker. The
    # ImplementationArtifactAnchor.open_findings field is already
    # deduplicated + non-empty-validated at the 13a model layer
    # (``models.py:_open_findings_dedup_and_non_empty``), so the
    # canonical blocker is per (slice_id, finding_id).
    if open_findings_anchors is not None:
        for anchor in open_findings_anchors:
            if not anchor.open_findings:
                continue
            for finding_id in anchor.open_findings:
                if not _is_unresolved_p1_p2_finding(finding_id):
                    continue
                blocker = (
                    f"governance_evidence_gap:open_findings:"
                    f"{anchor.slice_id}:{finding_id}"
                )
                if blocker not in seen:
                    seen.add(blocker)
                    blockers.append(blocker)

    return blockers


def _is_unresolved_p1_p2_finding(finding_id: str) -> bool:
    """Return True if ``finding_id`` is a P1 / P2 finding identifier.

    Per the doc-13:253-254 acceptance criterion (e) text ("Missing
    Slice 00-12 acceptance or unresolved P1/P2 findings blocks
    governance acceptance") the digester treats any finding ID with
    the P1 / P2 severity prefix as a hard fail-closed signal. Per the
    journal_parser conventions at
    ``src/iriai_build_v2/workflows/develop/governance/journal_parser.py:282-290``
    finding IDs follow the shape ``P<severity>-<scope>-<seq>`` (e.g.
    ``P1-07-A``, ``P2-13h-1``, ``P3-V1-1``). Only P1 + P2 trigger
    the hard fail-closed blocker; P3 findings are non-blocking
    maintainability/clarity items per the reviewer severities at
    ``IMPLEMENTATION_PROMPT_GOVERNANCE.md:206-216``.

    The classifier is intentionally conservative: any string starting
    with ``P1-`` or ``P2-`` qualifies. Per the auto-memory
    ``feedback_no_silent_degradation`` rule a malformed / non-string
    finding-id falls through to False (the 13a Pydantic
    ``open_findings`` field validator already rejects empty strings;
    this classifier is a downstream check for the specific severity
    prefix).
    """

    if not isinstance(finding_id, str):
        return False
    # Trim leading whitespace defensively; the 13a model validator
    # rejects whitespace-only strings, but does not strip leading
    # whitespace on otherwise-valid strings.
    stripped = finding_id.lstrip()
    return stripped.startswith("P1-") or stripped.startswith("P2-")


# --- Anchor -> ref projection ----------------------------------------------
#
# The journal + decision-log parsers (13c / 13d) emit
# :class:`ImplementationArtifactAnchor` rows, not
# :class:`GovernanceEvidenceRef` rows. The digester is the natural
# bridge: it projects each anchor to a typed-first ref that the
# downstream Slice-15 metrics / Slice-16 finding-engine consumer can
# read as authoritative typed evidence (after Slice 13A lands).
#
# Per doc-13:74-84 the journal authority is ``implementation_journal``
# (the 4th typed-first value) and the decision-log authority is
# ``implementation_decision_log`` (the 5th). Per doc-13:97-111 each
# emitted ref carries the anchor's ``slice_id``, ``journal_path``,
# and either ``line_start`` (journal) or ``decision_log_line``
# (decision log) projected into the typed-source-id and digest
# fields.


def _anchor_ref_id(
    anchor: ImplementationArtifactAnchor, authority: EvidenceAuthority
) -> str:
    """Project an anchor to a stable ``ref_id`` string.

    The ref_id encodes the anchor's authority + slice + journal path +
    line number so two different anchors from the same source line
    (e.g. a journal heading + a finding bullet on the same line) get
    distinct ref_ids. The shape is
    ``<authority>:<slice_id>:<journal_path>:<event>:L<line>`` with
    ``L<line>`` being either the markdown line (13c) or the JSONL row
    (13d). The 13a ``_non_empty_identifier`` validator on ``ref_id``
    at ``models.py:293-301`` rejects empty strings; the constructed
    shape is always non-empty.
    """

    if authority == "implementation_journal":
        line = anchor.line_start
    else:
        line = anchor.decision_log_line
    line_str = f"L{line}" if line is not None else "L?"
    return (
        f"{authority}:{anchor.slice_id}:{anchor.journal_path}:"
        f"{anchor.event}:{line_str}"
    )


def _anchor_digest(anchor: ImplementationArtifactAnchor) -> str:
    """Compute the per-anchor SHA-256 hex digest of the canonical
    anchor projection.

    Per doc-13:186 verbatim ("source ids and content digests, not full
    artifact bodies") the digest is over the anchor's typed fields
    (``slice_id``, ``journal_path``, ``line_start``, ``decision_log_line``,
    ``event``, ``accepted``, ``open_findings``) -- the 7 doc-13:143-150
    fields. No artifact body is hashed.

    ``open_findings`` is sorted before hashing so a producer-side
    re-ordering of the findings list does not change the anchor
    digest. The 13a ``_open_findings_dedup_and_non_empty`` validator
    at ``models.py:449-469`` already rejects duplicate finding ids;
    the sort here is a stable canonicalisation, not a deduplication.
    """

    payload = {
        "slice_id": anchor.slice_id,
        "journal_path": anchor.journal_path,
        "line_start": anchor.line_start,
        "decision_log_line": anchor.decision_log_line,
        "event": anchor.event,
        "accepted": anchor.accepted,
        "open_findings": sorted(anchor.open_findings),
    }
    return _sha256_hex(_canonical_json(payload))


def _anchor_to_ref(
    anchor: ImplementationArtifactAnchor, authority: EvidenceAuthority
) -> GovernanceEvidenceRef:
    """Project an :class:`ImplementationArtifactAnchor` to a
    :class:`GovernanceEvidenceRef`.

    Per doc-13:97-111 the ref carries the anchor's ``slice_id`` +
    journal anchor + content digest. The projection sets
    ``quality="canonical"`` (the anchor is a typed-first source per
    doc-13:74-84) and ``completeness="complete"`` (each anchor is a
    single-line atomic citation -- no paging). ``page_refs`` is empty
    (the anchor IS the single page; paging would only apply if the
    anchor referenced a multi-page artifact body, which the typed
    surface does not).
    """

    return GovernanceEvidenceRef(
        authority=authority,
        ref_id=_anchor_ref_id(anchor, authority),
        slice_id=anchor.slice_id,
        journal_anchor=f"{anchor.journal_path}:{anchor.event}",
        digest=_anchor_digest(anchor),
        quality="canonical",
        completeness="complete",
        page_refs=[],
        preview_only=False,
    )


# --- Public surface ---------------------------------------------------------


def compose_governance_evidence_set(
    *,
    journal_anchors: list[ImplementationArtifactAnchor],
    decision_log_anchors: list[ImplementationArtifactAnchor],
    supervisor_digest_refs: list[GovernanceEvidenceRef],
    resource_snapshot_refs: list[GovernanceEvidenceRef],
    window: GovernanceWindow,
    read_budget: GovernanceReadBudget,
    corpus_id: str = "governance-evidence-set",
    feature_id: str | None = None,
    omitted_refs: list[GovernanceEvidencePageRef] | None = None,
    read_budget_exhausted: bool = False,
) -> GovernanceEvidenceSet:
    """Compose a typed :class:`GovernanceEvidenceSet` from the four
    typed input lanes.

    Per doc-13:186-187 § "Refactoring Steps" step 5 the digester is the
    composition step that turns the parser + ingestor outputs into a
    typed corpus-level evidence record the Slice-15 metrics /
    Slice-16 finding-engine consumer reads. The signature accepts the
    four input lanes the doc-13:186-187 pipeline produces:

    * ``journal_anchors`` -- the 13c
      :func:`~iriai_build_v2.workflows.develop.governance.parse_implementation_journal`
      output (one anchor per recognised markdown anchor).
    * ``decision_log_anchors`` -- the 13d
      :func:`~iriai_build_v2.workflows.develop.governance.parse_implementation_decision_log`
      output (one anchor per recognised JSONL row plus zero-or-more
      cross-slice finding anchors).
    * ``supervisor_digest_refs`` -- typed refs the 13b ingestor's
      :meth:`~iriai_build_v2.workflows.develop.governance.GovernanceEvidenceIngestor.resolve_ref`
      surface produces from supervisor digest sources.
    * ``resource_snapshot_refs`` -- typed refs the 13b ingestor
      produces from resource snapshot sources.

    The four lanes are concatenated (in the documented order: journal
    anchors first, then decision-log anchors, then supervisor digests,
    then resource snapshots), deduplicated keep-first by
    ``(authority, ref_id)``, then digested per the doc-13:186 verbatim
    contract. The completeness / quality / source_mix / blockers
    projections fire on the deduplicated ref list.

    :param journal_anchors: 13c parser output -- one anchor per
        recognised markdown heading / finding / subagent UUID /
        test-result line. Empty list is legitimate.
    :param decision_log_anchors: 13d parser output -- one anchor per
        recognised JSONL row plus zero-or-more cross-slice finding
        anchors. Empty list is legitimate.
    :param supervisor_digest_refs: typed refs from supervisor digest
        sources. Empty list is legitimate.
    :param resource_snapshot_refs: typed refs from resource snapshot
        sources. Empty list is legitimate.
    :param window: the typed :class:`GovernanceWindow` describing the
        time / cursor scope of the evidence set. Stored verbatim on
        the result's ``source_window`` field (as a dict projection).
    :param read_budget: the typed :class:`GovernanceReadBudget` the
        upstream readers honoured. Stored verbatim on the result.
    :param corpus_id: the stable corpus identity. Defaults to
        ``"governance-evidence-set"`` -- the deferred ingestor will
        pass a richer corpus id once typed-row storage is wired
        (doc-13:188-190 step 6).
    :param feature_id: the optional feature scope. Defaults to
        ``None`` -- the deferred ingestor will pass the typed feature
        id once feature-window ingestion is wired (doc-13:160).
    :param omitted_refs: the optional list of exact page-refs that
        could not be included in the set (doc-13:215-218 "populate
        omitted_refs as exact page refs when possible"). Defaults
        to empty.
    :param read_budget_exhausted: whether the upstream readers'
        budget tripped during ingest (doc-13:215-220). Defaults to
        ``False``.
    :returns: a fully populated :class:`GovernanceEvidenceSet`. The
        ``idempotency_key`` is deterministic across the same logical
        input regardless of input ordering (sort-invariance per the
        user prompt's chunk-shape point 5).
    :raises ValueError: when an input ref contains duplicate
        ``page_ref_id`` within its ``page_refs`` list. Per the
        auto-memory ``feedback_no_silent_degradation`` rule intra-ref
        duplication is malformed input and fails closed (silent
        dedup would let a stale digest projection drift unnoticed).
        The 13a :class:`GovernanceEvidenceRef` /
        :class:`GovernanceEvidencePageRef` model validators handle
        single-ref field validation; the digester's intra-ref check
        layers the page-id uniqueness invariant on top.
    """

    # Combine the four input lanes in the documented order: journal,
    # decision log, supervisor digests, resource snapshots. The journal
    # + decision-log lanes are first projected from anchors to typed
    # refs (the digester is the natural bridge per the module docstring);
    # the supervisor / resource-snapshot lanes arrive pre-projected from
    # the 13b ingestor's resolve_ref surface.
    journal_refs = [
        _anchor_to_ref(anchor, "implementation_journal")
        for anchor in journal_anchors
    ]
    decision_refs = [
        _anchor_to_ref(anchor, "implementation_decision_log")
        for anchor in decision_log_anchors
    ]
    combined: list[GovernanceEvidenceRef] = (
        journal_refs
        + decision_refs
        + list(supervisor_digest_refs)
        + list(resource_snapshot_refs)
    )

    # P1-13e-1 finalizer fix: canonical sort BEFORE dedup so the
    # "first" ref at each (authority, ref_id) collision is the
    # canonically-smallest sibling (by per-ref digest) regardless of
    # input order. Without this pre-sort two collision-siblings whose
    # underlying typed fields differ would let keep-first pick the
    # input-order-first sibling, making the kept-first ref's per-ref
    # digest input-order-sensitive and breaking set-level idempotency.
    # Reviewer verified the bug on the live corpus (~10 collisions
    # among 685 journal anchors) and verified the fix produces an
    # invariant key across forward / shuffled / reversed inputs.
    canonical = _canonical_sort_refs(combined)

    # Per-ref dedup keep-first by (authority, ref_id) -- user prompt's
    # chunk-shape point 5. The dedup runs AFTER the canonical sort so
    # the kept-first ref is deterministic; the set-level idempotency_key
    # reflects the deduplicated ref list and is invariant under input
    # reordering.
    deduped = _dedup_refs_keep_first(canonical)

    # Set-level idempotency_key per doc-13:186 verbatim. Sort-invariant
    # by construction (the per-ref digests are sorted before hashing
    # in _compute_idempotency_key).
    idempotency_key = _compute_idempotency_key(deduped)

    # Completeness projection per doc-13:215-220. The four-state
    # completeness enum (complete / paged / preview_only / unavailable)
    # drives both the set-level invariant and the quality projection
    # downstream.
    completeness = _project_completeness(deduped)

    # source_mix per doc-13:137 -- per-authority count map. The 13a
    # _source_mix_counts_non_negative validator at models.py:379-393
    # rejects negative counts; the digester only ever increments so
    # the invariant holds by construction.
    source_mix = _project_source_mix(deduped)

    # Blockers projection -- two sub-classes per Slice 13A first
    # sub-slice:
    #
    # 1. Legacy-authority class: refs whose authority is legacy
    #    surface as canonical
    #    ``governance_evidence_gap:<authority>:<ref_id>`` strings per
    #    doc-13:209-210 ("record a ``governance_evidence_gap``
    #    finding") + the Slice 13A invariant at doc-13a:24, 109-118.
    #    P3-13e-3 closure -- the prior bespoke
    #    ``governance_evidence_legacy_authority:*`` shape is replaced
    #    mechanically with the canonical doc-13:209-210 form.
    #
    # 2. Open-findings class: anchors whose ``open_findings`` list
    #    contains unresolved P1 / P2 finding IDs surface as canonical
    #    ``governance_evidence_gap:open_findings:<slice_id>:<finding_id>``
    #    strings per doc-13:253-254 acceptance criterion (e) ("Missing
    #    Slice 00-12 acceptance or unresolved P1/P2 findings blocks
    #    governance acceptance"). The journal + decision-log anchors
    #    are merged into a single list passed to
    #    :func:`_project_blockers` so a finding cited in either lane
    #    triggers the blocker.
    #
    # Projected BEFORE quality (Slice 13A first sub-slice ordering
    # change) so the quality projection can tie itself to the
    # canonical governance_evidence_gap blocker presence per
    # doc-13:217 ("downstream metrics ... must fail closed").
    open_findings_anchors = (
        list(journal_anchors) + list(decision_log_anchors)
    )
    blockers = _project_blockers(
        deduped, open_findings_anchors=open_findings_anchors
    )

    # Quality projection per doc-13:173-175. Combines source_mix +
    # completeness + blockers to project the 6-value EvidenceQuality
    # enum. The blockers list (Slice 13A first sub-slice tie-in) lets
    # the projection downgrade to ``insufficient`` when any canonical
    # ``governance_evidence_gap`` blocker is present per doc-13:217.
    quality = _project_quality(deduped, completeness, blockers)

    # source_window projection -- the typed GovernanceWindow is stored
    # as a dict projection per the 13a GovernanceEvidenceSet
    # source_window: dict[str, Any] field shape (doc-13:133).
    source_window = window.model_dump(mode="json")

    # generated_at -- the digester's own timestamp; the set-level
    # idempotency_key is invariant across generated_at re-stamping (the
    # key is computed from the refs only, not from generated_at), so
    # two digester runs over the same refs at different times produce
    # the same idempotency_key but different generated_at timestamps.
    generated_at = datetime.now(timezone.utc)

    return GovernanceEvidenceSet(
        idempotency_key=idempotency_key,
        feature_id=feature_id,
        corpus_id=corpus_id,
        generated_at=generated_at,
        source_window=source_window,
        refs=deduped,
        omitted_refs=list(omitted_refs) if omitted_refs is not None else [],
        completeness=completeness,
        source_mix=source_mix,
        read_budget=read_budget,
        read_budget_exhausted=read_budget_exhausted,
        quality=quality,
        blockers=blockers,
    )
