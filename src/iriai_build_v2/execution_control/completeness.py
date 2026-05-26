"""Slice 13A second sub-slice -- foundational completeness typed-shape module.

This module owns the 6 doc-13a "Proposed Interfaces And Types" typed shapes
(``docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md:127-192``)
plus the ``compute_completeness_digest`` helper mentioned at doc-13a:264.
It is the **cross-cutting typed foundation** that subsequent Slice 13A
sub-slices (steps 3-9 per doc-13a:266-279) build on; this second sub-slice
does NOT yet wire these typed shapes into dispatcher / verifier / gate /
snapshot / supervisor consumers -- that wiring lands in subsequent
sub-slices via 13A-owned compatibility adapters per doc-13a:120-256.

Per the doc-13a:42-55 change-control rule the module:

* MUST NOT consume these typed shapes as **execution authority** outside this
  slice's own acceptance tests.
* MUST NOT wire into dispatcher / verifier / gate / snapshot / supervisor
  consumers (that is a future Slice 13A sub-slice).
* MUST NOT be re-exported from ``governance/__init__.py`` (this is the
  execution-control namespace, not governance).

The Slice 13A invariant (doc-13a:18-23) is:

> If a component can influence dispatch, verification, merge, checkpoint,
> routing, scheduler feedback, or policy recommendation, it must consume
> exact cited evidence or an exact paged manifest. Lossy summaries and
> previews are display-only.

This module provides the typed surface that future Slice 13A sub-slices
use to enforce that invariant; the typed shapes themselves are analytical
/ descriptive (they describe completeness state + evidence authority +
paged exact references) and the **consumption rules** that enforce the
invariant land in the dispatcher / verifier / gate / snapshot / supervisor
adapters that future Slice 13A sub-slices wire up.

**Namespace decision (doc-13a:263-265).** This module is the execution-
control namespace foundation: ``Add completeness.py under the execution-
control package``. The 5-value :data:`EvidenceAuthority` Literal here
(``execution_authority`` / ``gate_authority`` / ``routing_authority`` /
``advisory`` / ``display_only``) is **INTENTIONALLY DISTINCT** from the
9-value ``EvidenceAuthority`` Literal at
:mod:`iriai_build_v2.workflows.develop.governance.models` (doc-13:74-84;
the 7 typed-first authorities + 2 legacy fallbacks for governance
evidence sourcing). The two typed aliases share the name by design --
the doc-13a:135-141 wording explicitly defines a separate execution-
authority taxonomy distinct from the doc-13:74-84 governance-evidence-
source taxonomy. Future Slice 13A sub-slices that wire compatibility
adapters between the two namespaces MUST disambiguate via fully-
qualified imports
(``from iriai_build_v2.execution_control.completeness import EvidenceAuthority``
vs ``from iriai_build_v2.workflows.develop.governance.models import EvidenceAuthority``
or namespace-aliased imports), NOT via name collision.

**Implementation discipline.** Stdlib (``hashlib`` + ``json`` + ``typing``) +
Pydantic v2 only. NO imports from ``governance/`` (this module is
foundational; the governance layer consumes execution-control surfaces, not
the reverse). NO imports from other parts of ``execution_control/`` (this
module is foundational for the future 13A compatibility adapters; the
existing Slice 00-12 ``execution_control`` modules are NOT modified).

The Pydantic v2 idiom mirrors
:mod:`iriai_build_v2.workflows.develop.governance.models` (Slice 13a):
``Literal`` enums at module head; ``BaseModel`` subclasses with
``ConfigDict(extra="forbid")`` so typo-d kwargs fail closed as a typed
``ValidationError`` rather than being silently absorbed.

The :func:`compute_completeness_digest` helper uses the same canonical-
JSON discipline as
:func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
+ :func:`...evidence_set._sha256_hex` (doc-13:201-204): ``json.dumps(...,
sort_keys=True, separators=(",", ":"))`` then ``hashlib.sha256(...).hexdigest()``.
Two runs over the same logical input produce the same hex digest across
processes / restarts / Python versions / platforms.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


__all__ = [
    # Literal enums (doc-13a:128-141).
    "CompletenessState",
    "EvidenceAuthority",
    # The 4 doc-13a:143-192 typed shapes.
    "EvidencePageRef",
    "EvidenceCompleteness",
    "ExactEvidenceManifest",
    "AuthoritativeContextRef",
    # The digest helper (doc-13a:264 "shared models above plus digest helpers").
    "compute_completeness_digest",
]


# --- Literal enums (doc-13a:128-141) ----------------------------------------


CompletenessState = Literal[
    "complete",
    "paged",
    "preview_only",
    "unavailable",
]
"""Doc-13a:128-133 -- the 4-value completeness-state enum.

* ``complete`` -- the evidence is fully present in a single page; the consumer
  may treat it as authoritative.
* ``paged`` -- the evidence is present but split across multiple exact pages;
  the consumer must traverse the pages to gather the full content but may
  still treat it as authoritative.
* ``preview_only`` -- a display-summary preview is present, but no exact
  page-refs back it; per the Slice 13A invariant (doc-13a:18-23) the consumer
  MUST NOT treat preview-only evidence as authoritative.
* ``unavailable`` -- the required evidence cannot be paged exactly; per the
  doc-13a:307-310 edge-case rule the consumer must route
  ``runtime_context/context_incomplete`` or ``verifier_context/context_incomplete``
  and fail closed.

Note this is INTENTIONALLY distinct from but value-compatible with
``iriai_build_v2.workflows.develop.governance.models.CompletenessState``
(doc-13:87) -- both are 4-value Literals with the same members. The
duplication mirrors the doc-13a:120-256 cross-cutting wording that introduces
the type fresh in the execution-control namespace; future Slice 13A
sub-slices may consolidate via re-export (NOT in this sub-slice's scope per
the change-control rule doc-13a:42-55).
"""


EvidenceAuthority = Literal[
    "execution_authority",
    "gate_authority",
    "routing_authority",
    "advisory",
    "display_only",
]
"""Doc-13a:135-141 -- the 5-value execution-control authority taxonomy.

This is **INTENTIONALLY DISTINCT** from the 9-value
``EvidenceAuthority`` Literal at
:mod:`iriai_build_v2.workflows.develop.governance.models` (doc-13:74-84).
The two typed aliases share the name by design -- the doc-13a:135-141
wording explicitly defines a separate execution-authority taxonomy:

* ``execution_authority`` -- the evidence drives executor decisions (DAG
  expansion, dispatcher invocation, runtime selection).
* ``gate_authority`` -- the evidence drives gate approval (atomic landing,
  merge-queue admission, verifier completion).
* ``routing_authority`` -- the evidence drives typed-failure-router
  classification + downstream policy selection.
* ``advisory`` -- the evidence informs but does NOT drive any authoritative
  decision; consumers may render it but classifier rules MUST fail closed
  per the Slice 13A invariant doc-13a:18-23.
* ``display_only`` -- the evidence is purely for dashboard / supervisor
  display surfaces; per doc-13a:99-106 + doc-13a:111-115 lossy summaries +
  previews are display-only.

The 9-value governance variant covers the source-of-truth tags for
governance EVIDENCE SOURCING (typed_journal / compatibility_projection /
git_provenance / implementation_journal / implementation_decision_log /
supervisor_digest / resource_snapshot / legacy_event /
legacy_artifact_summary). The 5-value execution-control variant covers
DECISION-AUTHORITY CLASSIFICATION. Disambiguate via fully-qualified or
namespace-aliased imports:

    from iriai_build_v2.execution_control.completeness import (
        EvidenceAuthority as ExecutionAuthority,
    )
    from iriai_build_v2.workflows.develop.governance.models import (
        EvidenceAuthority as GovernanceEvidenceAuthority,
    )
"""


# --- Pure typed shapes (doc-13a:143-192) ------------------------------------


class EvidencePageRef(BaseModel):
    """Doc-13a:143-160 -- per-page exact reference for one page of evidence.

    The page-ref carries the 7-value ``source_kind`` Literal (``typed_row`` /
    ``artifact`` / ``event`` / ``file`` / ``diff`` / ``provider_record`` /
    ``projection``), an opaque ``source_id`` (int or str depending on the
    source kind), the page's content ``sha256``, optional byte / line / item
    ranges (``start`` / ``end`` / ``item_count`` / ``bytes``), and a
    free-form ``reason`` describing why the page was selected.

    Per the doc-13a:298-301 page-ref stability rule: page-refs MUST be
    stable across resume; re-fetching a page by ``ref_id`` MUST return the
    same ``sha256`` or fail as stale/corrupt.

    Per doc-13a:153-154 the ``start`` / ``end`` fields are Optional
    (``int | None = None``) -- a complete-in-single-page evidence may omit
    range markers.
    """

    # extra='forbid' aligns with the sibling governance model precedent at
    # src/iriai_build_v2/workflows/develop/governance/models.py:485 and the
    # sibling executor models at
    # src/iriai_build_v2/workflows/develop/execution/verification.py:74 +
    # failure_router.py:576 -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    ref_id: str
    """Doc-13a:144 -- stable cross-process page identifier."""

    source_kind: Literal[
        "typed_row",
        "artifact",
        "event",
        "file",
        "diff",
        "provider_record",
        "projection",
    ]
    """Doc-13a:145-153 -- the 7-value source-kind enumeration.

    Names the kind of underlying source the page-ref cites. The exact
    interpretation of ``source_id`` depends on ``source_kind``: typed
    rows + events use integer ids (typed-row primary keys); artifacts +
    files + diffs + provider records + projections may use either int
    or str ids depending on the producer.
    """

    source_id: int | str
    """Doc-13a:154 -- opaque source identifier; int or str depending on
    ``source_kind``."""

    sha256: str
    """Doc-13a:155 -- page-content digest. Per doc-13a:298-301 the digest
    is the cross-process freshness contract; re-fetching the page by
    ``ref_id`` MUST return this same ``sha256`` or fail as stale/corrupt."""

    start: int | None = None
    """Doc-13a:156 -- optional start offset (byte or line; producer's
    choice). Per doc-13a:153-154 the range markers are Optional (a
    complete-in-single-page evidence may omit them)."""

    end: int | None = None
    """Doc-13a:157 -- optional end offset (byte or line; producer's
    choice). Paired with ``start`` for range-bound page slicing."""

    item_count: int | None = None
    """Doc-13a:158 -- optional count of items in the page (e.g. row count
    for typed-row pages, line count for file/diff pages)."""

    bytes: int | None = None
    """Doc-13a:159 -- optional byte length of the page content."""

    reason: str
    """Doc-13a:160 -- free-form reason describing why this page was
    selected (e.g. ``required-evidence-for-gate-X``,
    ``preview-for-display``, ``optional-context``)."""


class EvidenceCompleteness(BaseModel):
    """Doc-13a:162-170 -- the per-decision completeness contract.

    Carries the :data:`CompletenessState` for one decision scope plus the
    :data:`EvidenceAuthority` taxonomy class of the decision, the list of
    decision identifiers the completeness covers (``complete_for``), the
    list of required page-refs that are missing (``missing_required_refs``),
    the list of resolved page-refs (``page_refs``), an optional preview
    reference (``preview_ref``), an optional reason when the state is
    ``unavailable``, and a deterministic ``completeness_digest`` that
    cross-process consumers can compare for byte-identical match.
    """

    # extra='forbid' aligns with the EvidencePageRef precedent above.
    model_config = ConfigDict(extra="forbid")

    state: CompletenessState
    """Doc-13a:163 -- the completeness state for this decision scope."""

    authority: EvidenceAuthority
    """Doc-13a:164 -- the authority class of the decision (5-value
    execution-control taxonomy)."""

    complete_for: list[str]
    """Doc-13a:165 -- the list of decision identifiers (or decision-scope
    names) the completeness covers. Empty list is valid (a completeness
    record may exist without claiming coverage)."""

    missing_required_refs: list[EvidencePageRef] = Field(default_factory=list)
    """Doc-13a:166 -- the list of page-refs that the consumer required
    but the producer could not resolve. Empty by default; populated when
    the state is ``unavailable`` or when ``paged`` evidence has some
    pages missing."""

    page_refs: list[EvidencePageRef] = Field(default_factory=list)
    """Doc-13a:167 -- the list of resolved page-refs. Empty by default;
    populated when the state is ``complete`` (single page) or ``paged``
    (multiple pages)."""

    preview_ref: EvidencePageRef | None = None
    """Doc-13a:168 -- optional preview page-ref for display surfaces.
    Per doc-13a:111-115 + doc-13a:18-23 (Slice 13A invariant) preview
    refs are display-only and MUST NOT be treated as authoritative."""

    unavailable_reason: str | None = None
    """Doc-13a:169 -- optional reason string when the state is
    ``unavailable``; None for other states."""

    completeness_digest: str
    """Doc-13a:170 -- the deterministic SHA-256 hex digest computed by
    :func:`compute_completeness_digest` over the canonical-JSON
    projection of (state, authority, complete_for, missing_required_refs,
    page_refs, preview_ref, unavailable_reason). Two runs over the same
    logical content produce byte-identical digests across processes /
    restarts / Python versions / platforms."""


class ExactEvidenceManifest(BaseModel):
    """Doc-13a:172-184 -- the manifest naming a complete set of exact
    page-refs for a dispatcher / verifier / gate scope.

    The manifest is the authoritative descriptor a dispatcher / verifier /
    gate consumes; per the Slice 13A invariant doc-13a:18-23 the manifest
    is the carrier of exact evidence or exact paged manifest that allows
    a consumer to drive an authoritative decision.

    The 11 fields land verbatim from doc-13a:172-184: ``manifest_id`` /
    ``manifest_digest`` / ``feature_id`` / ``dag_sha256`` /
    ``group_idx`` (optional) / ``task_ids`` / ``selection_scope`` /
    ``completeness`` (an :class:`EvidenceCompleteness`) /
    ``required_page_refs`` / ``optional_page_refs`` /
    ``display_preview_ref`` (optional) / ``advisory_only``.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    """Doc-13a:173 -- stable cross-process manifest identifier."""

    manifest_digest: str
    """Doc-13a:174 -- SHA-256 hex digest over the manifest's typed content
    (NOT the artifact bodies); see doc-13a:298-301 stability rule."""

    feature_id: str
    """Doc-13a:175 -- the feature this manifest scopes to."""

    dag_sha256: str
    """Doc-13a:176 -- SHA-256 hex digest of the canonical DAG the manifest
    covers; ties the manifest to a specific DAG version."""

    group_idx: int | None
    """Doc-13a:177 -- optional group index within the DAG; None for
    feature-level manifests that span all groups."""

    task_ids: list[str]
    """Doc-13a:178 -- the list of task ids the manifest covers."""

    selection_scope: list[str]
    """Doc-13a:179 -- the list of selection-scope names (decision scopes
    the manifest carries evidence for)."""

    completeness: EvidenceCompleteness
    """Doc-13a:180 -- the per-decision completeness record. Per the
    Slice 13A invariant doc-13a:18-23 the consumer drives authoritative
    decisions only when ``completeness.state in {"complete", "paged"}``."""

    required_page_refs: list[EvidencePageRef]
    """Doc-13a:181 -- the list of required page-refs. Empty list is valid
    (the manifest may carry only optional context)."""

    optional_page_refs: list[EvidencePageRef]
    """Doc-13a:182 -- the list of optional page-refs (additional context
    the consumer may use but does NOT require for an authoritative
    decision)."""

    display_preview_ref: EvidencePageRef | None = None
    """Doc-13a:183 -- optional preview ref for display surfaces. Per
    doc-13a:111-115 preview refs are display-only."""

    advisory_only: bool
    """Doc-13a:184 -- when True, the manifest is advisory only; consumers
    MUST NOT drive authoritative decisions from this manifest even if
    ``completeness.state in {"complete", "paged"}``. Per doc-13a:50-54
    the governance / context layer may use advisory manifests for
    planning + read-only analysis but MUST NOT enable execution-authority
    semantics until Slice 13A has fully landed."""


class AuthoritativeContextRef(BaseModel):
    """Doc-13a:186-192 -- the lightweight reference a consumer carries
    to point at an :class:`ExactEvidenceManifest` for authoritative
    decision-making.

    The ref carries the manifest's stable identifier + digest + the
    completeness's digest + the list of decision scopes the consumer
    requires complete coverage of + the authority class the consumer
    operates under. Future Slice 13A sub-slices (steps 3-6 per
    doc-13a:266-279) will wire compatibility adapters that derive an
    ``AuthoritativeContextRef`` from existing Slice 05
    ``PromptContextBundle`` records without changing accepted Slice 05
    interfaces in-place.
    """

    # extra='forbid' aligns with the sibling shapes above.
    model_config = ConfigDict(extra="forbid")

    manifest_id: str
    """Doc-13a:187 -- the manifest this context ref points at."""

    manifest_digest: str
    """Doc-13a:188 -- the manifest's digest; ties the ref to a specific
    manifest version (a manifest digest change MUST invalidate this ref
    so consumers re-fetch)."""

    completeness_digest: str
    """Doc-13a:189 -- the manifest's completeness digest; ties the ref
    to a specific completeness state (a completeness digest change MUST
    invalidate this ref so consumers re-evaluate authority)."""

    required_complete_for: list[str]
    """Doc-13a:190 -- the list of decision scopes the consumer requires
    complete coverage of. The consumer drives authoritative decisions
    only when every entry here appears in the manifest's
    ``completeness.complete_for`` AND ``completeness.state in
    {"complete", "paged"}``."""

    authority: EvidenceAuthority
    """Doc-13a:191 -- the authority class the consumer operates under.
    Per the Slice 13A invariant doc-13a:18-23 the consumer drives
    authoritative decisions only when the authority + completeness +
    coverage combination satisfies the invariant; ``advisory`` /
    ``display_only`` consumers MUST NOT drive authoritative decisions
    regardless of completeness state."""


# --- Digest helpers (doc-13a:264 "shared models above plus digest helpers") -


def _canonical_json(obj: object) -> str:
    """Produce a canonical-JSON serialisation of ``obj``.

    Mirrors :func:`iriai_build_v2.workflows.develop.governance.evidence_set._canonical_json`
    (doc-13:201-204) verbatim: ``json.dumps(..., sort_keys=True,
    separators=(",", ":"))`` -- the canonical form mandates
    lexicographic key ordering and the compact separator set so the
    resulting bytes are stable across Python versions / platforms /
    dict ordering. Numeric / string / bool / None / list / dict
    values serialise verbatim; the digester never feeds non-JSON-safe
    values into this helper (the typed shapes project to primitives
    only).
    """

    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def _sha256_hex(payload: str) -> str:
    """Return the SHA-256 hex digest of ``payload`` (UTF-8 encoded).

    Mirrors :func:`iriai_build_v2.workflows.develop.governance.evidence_set._sha256_hex`
    verbatim. Stdlib-only per the implementer prompt § "Non-negotiables"
    (``hashlib`` + ``json`` only).
    """

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _page_ref_digest_payload(page_ref: EvidencePageRef) -> dict[str, object]:
    """Project one :class:`EvidencePageRef` to its digest payload.

    Includes every field that carries cross-process semantic content:
    ``ref_id`` + ``source_kind`` + ``source_id`` + ``sha256`` +
    ``start`` + ``end`` + ``item_count`` + ``bytes`` + ``reason``.

    All 9 fields are included because the page-ref is itself the
    cross-process freshness contract (doc-13a:298-301); a change in any
    field changes the digest. This is intentionally MORE permissive than
    the governance per-ref-digest projection at
    :func:`.evidence_set._page_ref_digest_payload` which omits range
    markers because the governance digest's freshness contract is the
    intra-set per-ref ``digest`` field; the execution-control page-ref
    has its own ``sha256`` field that bundles content but the producer-
    side range markers describe a different slice of the same source,
    so range changes MUST change the digest.
    """

    return {
        "ref_id": page_ref.ref_id,
        "source_kind": page_ref.source_kind,
        "source_id": page_ref.source_id,
        "sha256": page_ref.sha256,
        "start": page_ref.start,
        "end": page_ref.end,
        "item_count": page_ref.item_count,
        "bytes": page_ref.bytes,
        "reason": page_ref.reason,
    }


def compute_completeness_digest(
    *,
    state: CompletenessState,
    authority: EvidenceAuthority,
    complete_for: list[str],
    missing_required_refs: list[EvidencePageRef],
    page_refs: list[EvidencePageRef],
    preview_ref: EvidencePageRef | None,
    unavailable_reason: str | None,
) -> str:
    """Compute the deterministic SHA-256 hex digest for an
    :class:`EvidenceCompleteness` value.

    Doc-13a:264 ("shared models above plus digest helpers") names the
    digest helper; this implementation mirrors the canonical-JSON +
    SHA-256 discipline at
    :mod:`iriai_build_v2.workflows.develop.governance.evidence_set`
    (doc-13:201-204) verbatim.

    The digest payload is a 7-key dict naming every input field; each
    page-ref is projected via :func:`_page_ref_digest_payload` to a
    deterministic 9-key sub-dict. The payload is serialised via
    :func:`_canonical_json` (``json.dumps(..., sort_keys=True,
    separators=(",", ":"))``) and hashed via :func:`_sha256_hex`
    (``hashlib.sha256(...).hexdigest()``).

    **Determinism contract.** Two calls with the same logical input
    (regardless of dict-key insertion order or sub-dict-key order) MUST
    produce byte-identical hex digests. This is the cross-process
    freshness contract subsequent Slice 13A sub-slices rely on when
    consumers compare ``AuthoritativeContextRef.completeness_digest``
    to detect manifest staleness.

    **List-order sensitivity.** Per doc-13a:165 + doc-13a:166-167 the
    ``complete_for`` + ``missing_required_refs`` + ``page_refs`` lists
    are producer-ordered; the digest IS sensitive to list element order
    (a list re-ordering changes the digest). Producers that wish to
    achieve order-invariance MUST sort their lists canonically before
    calling this helper.

    The keyword-only signature mirrors the
    :class:`EvidenceCompleteness` field set so a producer can populate
    the digest at construction time:

        completeness_digest = compute_completeness_digest(
            state=state,
            authority=authority,
            complete_for=complete_for,
            missing_required_refs=missing_required_refs,
            page_refs=page_refs,
            preview_ref=preview_ref,
            unavailable_reason=unavailable_reason,
        )
        return EvidenceCompleteness(
            state=state,
            authority=authority,
            complete_for=complete_for,
            missing_required_refs=missing_required_refs,
            page_refs=page_refs,
            preview_ref=preview_ref,
            unavailable_reason=unavailable_reason,
            completeness_digest=completeness_digest,
        )
    """

    payload = {
        "state": state,
        "authority": authority,
        "complete_for": list(complete_for),
        "missing_required_refs": [
            _page_ref_digest_payload(p) for p in missing_required_refs
        ],
        "page_refs": [_page_ref_digest_payload(p) for p in page_refs],
        "preview_ref": (
            _page_ref_digest_payload(preview_ref) if preview_ref is not None else None
        ),
        "unavailable_reason": unavailable_reason,
    }
    return _sha256_hex(_canonical_json(payload))
