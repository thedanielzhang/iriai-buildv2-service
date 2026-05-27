"""Slice 13A third sub-slice -- compatibility adapter for the accepted Slice
05 ``PromptContextBundle``.

This module implements the **13A-owned compatibility wrapper** specified at
``docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md:194-211``
(the doc-13a:194-211 "Add a 13A-owned compatibility wrapper for accepted
Slice 05 ``PromptContextBundle`` records" section). It is the first
downstream consumer adapter built on top of the Slice 13A second sub-slice's
foundational :mod:`iriai_build_v2.execution_control.completeness` typed
shapes; per doc-13a § Refactoring Steps step 3 (doc-13a:266-268) it derives
:class:`AuthoritativePromptContextBundle` from existing Slice 05
``PromptContextBundle`` records **without changing accepted Slice 05
interfaces in-place**.

The accepted Slice 05 ``PromptContextBundle`` typed shape lives at
``src/iriai_build_v2/workflows/develop/execution/dispatcher.py:229-239``
under the ``_DispatcherModel`` base
(``ConfigDict(extra="allow", arbitrary_types_allowed=True,
from_attributes=True)``). Its 10 fields are: ``prompt_ref:int`` /
``prompt_sha256:str`` / ``prompt_summary:str`` /
``context_file_refs:list[int]`` / ``context_file_paths:list[str]`` /
``context_sha256:str`` / ``included_contract_ids:list[int]`` /
``included_evidence_ids:list[int]`` / ``excluded_evidence_ids:list[int]``
/ ``truncation_notes:list[str]``.

**Change-control non-negotiable.** Per doc-13a:42-46 + doc-13a:124-126
the adapter is the **wrapper**; this module MUST NOT edit
``dispatcher.py:229-239`` in-place. The adapter reads the legacy bundle
(via Pydantic field access; the legacy ``_DispatcherModel`` base is
``extra="allow"`` so existing field semantics are preserved) and emits
the new :class:`AuthoritativePromptContextBundle` shape. Existing Slice
05 callers continue to read the legacy shape unchanged; new authoritative
consumers (subsequent 13A sub-slices for dispatcher / verifier / gate /
snapshot / supervisor wiring per doc-13a:269-282 steps 4-7) read the 13A
wrapper.

**Exact-vs-preview boundary rule.** The adapter sets
``completeness.state`` per doc-13a:115-118 + doc-13a:303-310:

* ``state="complete"`` (default; fully-resolved legacy bundle has all 10
  fields populated and ``truncation_notes`` is empty) +
  ``authority="execution_authority"`` -- the consumer may treat the
  bundle as authoritative.
* ``state="paged"`` when the legacy bundle's ``truncation_notes`` is
  non-empty -- the semantic context was bounded; the consumer may treat
  the bundle as authoritative only when exact page refs are supplied in
  ``excluded_evidence_refs`` and carried into completeness. Dispatch
  consumers fail closed when paged completeness has no page refs.
* ``state="preview_only"`` when the legacy bundle carries
  ``prompt_summary`` (rendered as ``display_prompt_summary`` on the
  wrapper per doc-13a:201) without ``context_file_refs`` (legacy
  fallback) -- the consumer MUST NOT treat preview-only evidence as
  authoritative per the Slice 13A invariant doc-13a:18-23.
* ``state="unavailable"`` when the legacy bundle is missing required
  fields (``prompt_ref`` / ``prompt_sha256`` / ``context_sha256``) -- the
  adapter raises :class:`MissingPromptContextFieldError` (typed exception,
  NOT silent degrade per the auto-memory ``feedback_no_silent_degradation``
  rule).

**Display-metadata preservation.** Per doc-13a:213-215 "Existing
``truncation_notes`` remains readable for compatibility, but it is
display metadata only. New authoritative consumers must read the 13A
``EvidenceCompleteness`` / ``AuthoritativeContextRef`` wrapper." The
adapter preserves the legacy ``truncation_notes`` verbatim on the
wrapper's ``truncation_notes`` field as display metadata; the
**authoritative** completeness signal is the
:class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
record on the ``completeness`` field. Consumers MUST NOT use
``truncation_notes`` as a routing / dispatch / verification authority.

**Namespace decision (doc-13a:194-196).** This module lives at
``src/iriai_build_v2/execution_control/prompt_context_adapter.py``
alongside ``completeness.py`` per the doc-13a:194-196 "Add a 13A-owned
compatibility wrapper" wording. It is **NOT re-exported** from
``src/iriai_build_v2/execution_control/__init__.py`` (precedent: the
Slice 13A second sub-slice did NOT touch ``__init__.py``; consumers in
subsequent 13A sub-slices wire up the package-level re-exports).

**Implementation discipline.** Stdlib (``typing``) + Pydantic v2 +
:mod:`iriai_build_v2.execution_control.completeness` (the second
sub-slice's foundational typed shapes; READ-ONLY consumer) + the Slice
05 ``PromptContextBundle`` typed shape at
:mod:`iriai_build_v2.workflows.develop.execution.dispatcher` (READ-ONLY
consumer; the dispatcher module is the accepted Slice 05 plan output)
only. NO imports from ``governance/`` (the governance layer consumes
execution-control surfaces, not the reverse). NO imports from other
parts of ``execution_control/`` beyond ``completeness``.
"""

from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, ConfigDict, Field

# Slice 13A second sub-slice's foundational typed shapes (READ-ONLY consumer).
# Citations per doc-13a:127-192 (typed shapes) + doc-13a:264 (digest helper).
from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    EvidenceAuthority,
    EvidenceCompleteness,
    EvidencePageRef,
    compute_completeness_digest,
)

# Accepted Slice 05 PromptContextBundle (READ-ONLY consumer). Per doc-13a:42-46
# + 124-126 the adapter is a wrapper; this module MUST NOT edit the legacy
# typed shape at dispatcher.py:229-239 in-place.
from iriai_build_v2.workflows.develop.execution.dispatcher import (
    PromptContextBundle,
)


__all__ = [
    # The 13A-owned compatibility wrapper Pydantic BaseModel (doc-13a:198-211).
    "AuthoritativePromptContextBundle",
    # The typed exception raised on missing-required-field legacy bundles
    # (fail-closed; NOT silent degrade per feedback_no_silent_degradation).
    "MissingPromptContextFieldError",
    # The adapter function (doc-13a:266-268 step 3 + doc-13a:194-211 wrapper).
    "derive_authoritative_prompt_context_bundle",
]


# --- Typed exception (fail-closed contract) --------------------------------


class MissingPromptContextFieldError(ValueError):
    """Raised when a legacy Slice 05 ``PromptContextBundle`` is missing a
    required field the adapter needs to derive an
    :class:`AuthoritativePromptContextBundle`.

    Per the auto-memory ``feedback_no_silent_degradation`` rule: the
    adapter MUST NOT silently emit a degraded wrapper when required
    fields are missing. It raises this typed exception so the caller can
    classify the failure (the doc-13a:115-118 + doc-13a:303-310
    ``state="unavailable"`` semantics carry through to the typed-failure
    router via subsequent Slice 13A sub-slices that wire dispatch
    consumers).

    The exception carries ``missing_field_names`` so callers can render a
    precise error message + route a typed-failure record. Inherits
    :class:`ValueError` so any caller that already catches
    :class:`ValueError` for malformed-input handling sees the failure
    (mirrors the
    :class:`iriai_build_v2.workflows.develop.governance.evidence_store.GovernanceEvidenceStoreIdempotencyConflict`
    sibling precedent which also inherits :class:`ValueError`).
    """

    def __init__(self, missing_field_names: Sequence[str]) -> None:
        # Defensive copy to a tuple so the public attribute is immutable.
        self.missing_field_names: tuple[str, ...] = tuple(missing_field_names)
        joined = ", ".join(self.missing_field_names)
        super().__init__(
            f"legacy PromptContextBundle is missing required field(s) "
            f"for the 13A compatibility wrapper: {joined}"
        )


# --- The 13A-owned compatibility wrapper (doc-13a:198-211) -----------------


class AuthoritativePromptContextBundle(BaseModel):
    """Doc-13a:198-211 -- the 13A-owned compatibility wrapper for the
    accepted Slice 05 ``PromptContextBundle``.

    Carries the 12 doc-13a:198-211 wrapper fields verbatim plus the legacy
    display-only ``truncation_notes`` per doc-13a:213-215 ("Existing
    ``truncation_notes`` remains readable for compatibility, but it is
    display metadata only"). New authoritative consumers MUST read the
    :data:`completeness` + :data:`context_manifest_ref` fields, NOT
    :data:`truncation_notes` (which remains display metadata only).

    The wrapper mirrors the accepted Slice 05 ``PromptContextBundle`` field
    set at ``dispatcher.py:229-239`` so existing callers can derive the
    wrapper from the legacy bundle without ambiguity. The
    :data:`display_prompt_summary` field renames the legacy
    ``prompt_summary`` per doc-13a:201 (the doc-13a wording explicitly
    names the field ``display_prompt_summary`` to signal display-only
    semantics; the underlying string content carries through unchanged).

    Per the Slice 13A invariant doc-13a:18-23: if the consumer can
    influence dispatch, verification, merge, checkpoint, routing,
    scheduler feedback, or policy recommendation, it MUST consume
    :data:`completeness` (the typed
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    record) -- a ``preview_only`` or ``unavailable`` state MUST NOT drive
    authoritative decisions.
    """

    # extra='forbid' aligns with the sibling completeness.py shapes and
    # forbids typo-d kwargs from being silently absorbed (typed
    # ValidationError instead). This is the doc-13a:194-211 wrapper, NOT
    # the legacy Slice 05 PromptContextBundle (which keeps its
    # extra='allow' base for compatibility per doc-13a:42-46).
    model_config = ConfigDict(extra="forbid")

    # --- Identity + content digest (doc-13a:199-200) ----------------------

    prompt_ref: int
    """Doc-13a:199 -- the prompt artifact ref (typed-row primary key)."""

    prompt_sha256: str
    """Doc-13a:200 -- SHA-256 hex digest of the prompt content."""

    # --- Display preview (doc-13a:201) ------------------------------------

    display_prompt_summary: str
    """Doc-13a:201 -- the display-only prompt summary string.

    Renamed from the legacy Slice 05 ``prompt_summary`` field at
    ``dispatcher.py:229-239`` per doc-13a:201; the underlying string
    content carries through unchanged. Per doc-13a:99-106 +
    doc-13a:111-115 (the doc-13a-cited "Compatible deviations" +
    "Blocking deviations" rules) display previews are display-only and
    MUST NOT drive authoritative decisions.
    """

    # --- Authoritative context reference (doc-13a:202) --------------------

    context_manifest_ref: AuthoritativeContextRef
    """Doc-13a:202 -- the lightweight
    :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    pointing at an
    :class:`~iriai_build_v2.execution_control.completeness.ExactEvidenceManifest`
    for authoritative decision-making.

    The consumer drives authoritative decisions only when the ref's
    ``authority`` + the completeness's ``state`` satisfy the Slice 13A
    invariant (doc-13a:18-23) for the consumer's decision scope.
    """

    # --- Legacy context fields preserved (doc-13a:203-208) ----------------

    context_file_refs: list[int]
    """Doc-13a:203 -- the list of context-file artifact refs (typed-row
    primary keys). Mirrors the legacy Slice 05 field verbatim at
    ``dispatcher.py:229-239``."""

    context_file_paths: list[str]
    """Doc-13a:204 -- the list of context-file paths (display strings,
    for dashboard / debug rendering). Mirrors the legacy Slice 05 field
    verbatim."""

    context_sha256: str
    """Doc-13a:205 -- SHA-256 hex digest of the canonical concatenation
    of context-file contents. Mirrors the legacy Slice 05 field
    verbatim."""

    included_contract_ids: list[int]
    """Doc-13a:206 -- the list of contract artifact ids included in the
    bundle. Mirrors the legacy Slice 05 field verbatim."""

    included_evidence_ids: list[int]
    """Doc-13a:207 -- the list of evidence-node ids included in the
    bundle. Mirrors the legacy Slice 05 field verbatim."""

    excluded_evidence_ids: list[int]
    """Doc-13a:208 -- the list of evidence-node ids that were excluded
    from the bundle (e.g. exceeded budget / off-scope). Mirrors the
    legacy Slice 05 field verbatim."""

    # --- New 13A excluded evidence page-refs (doc-13a:209) ----------------

    excluded_evidence_refs: list[EvidencePageRef]
    """Doc-13a:209 -- the list of
    :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef`
    records for evidence that was excluded from the bundle.

    Per the Slice 13A invariant doc-13a:18-23 + doc-13a:303-310: the
    consumer MUST be able to identify excluded evidence by exact page
    refs (so the consumer can either fetch the excluded pages on demand
    or route ``runtime_context/context_incomplete`` if the excluded
    evidence is required).
    """

    # --- Completeness record (doc-13a:210) --------------------------------

    completeness: EvidenceCompleteness
    """Doc-13a:210 -- the per-decision
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    contract.

    Per the Slice 13A invariant doc-13a:18-23: this is the
    **authoritative** completeness signal. A consumer that can influence
    dispatch / verify / merge / checkpoint / route / scheduler / policy
    MUST consume this field, NOT :data:`truncation_notes` (which is
    display metadata only per doc-13a:213-215).
    """

    # --- Legacy display metadata preserved (doc-13a:213-215) --------------

    truncation_notes: list[str]
    """Doc-13a:213-215 -- the legacy Slice 05 ``truncation_notes`` field
    preserved verbatim as **display metadata only**.

    Per doc-13a:213-215: "Existing ``truncation_notes`` remains readable
    for compatibility, but it is display metadata only. New authoritative
    consumers must read the 13A
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    / :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    wrapper."

    Per doc-13a:110-111 the legacy ``truncation_notes`` field was
    "the only indication that task context is incomplete" -- post-13A it
    is no longer authority; the typed :data:`completeness` record above
    is. The adapter preserves the legacy field verbatim so dashboard /
    debug / display surfaces continue to render the legacy notes (per
    doc-13a:213-215 "Existing ``truncation_notes`` remains readable for
    compatibility").
    """


# --- The 13A-owned compatibility adapter (doc-13a:266-268 step 3) ----------


def _has_truncation(legacy_bundle: PromptContextBundle) -> bool:
    """True iff the legacy bundle's ``truncation_notes`` is non-empty.

    Per doc-13a:115-118 + doc-13a:303-310: a non-empty
    ``truncation_notes`` signals that the semantic context was bounded;
    the adapter then sets :data:`EvidenceCompleteness.state` to
    ``"paged"``. Runtime dispatch treats that as authoritative only
    when exact page refs are carried into completeness.
    """

    return bool(legacy_bundle.truncation_notes)


def _has_context_file_refs(legacy_bundle: PromptContextBundle) -> bool:
    """True iff the legacy bundle carries non-empty ``context_file_refs``.

    Per doc-13a:115-118: a legacy bundle with ``prompt_summary`` but no
    ``context_file_refs`` is a legacy fallback (display preview only); the
    adapter then sets :data:`EvidenceCompleteness.state` to
    ``"preview_only"``.
    """

    return bool(legacy_bundle.context_file_refs)


def _missing_required_field_names(
    legacy_bundle: PromptContextBundle,
) -> tuple[str, ...]:
    """Return the tuple of required-field names the adapter cannot find on
    the legacy bundle.

    The 3 required fields are ``prompt_ref`` / ``prompt_sha256`` /
    ``context_sha256`` (the digest + ref identity fields that anchor the
    authoritative context). The legacy Slice 05 ``PromptContextBundle``
    base ``_DispatcherModel`` is ``extra="allow"`` so the Pydantic
    constructor itself does not fail closed on missing fields beyond
    Pydantic's own required-field validation -- the adapter adds the
    explicit emptiness check (``not value`` for str + 0-equivalence for
    int) to catch the legacy-fallback case where a bundle carries
    placeholder values (e.g. empty ``prompt_sha256``) instead of raising
    Pydantic's own ``ValidationError``.

    The ``prompt_ref`` legacy field is ``int`` so 0 / None is treated as
    missing (the dispatcher allocates positive ids per
    ``dispatcher.py:380`` so a 0 value indicates an unfilled placeholder).
    The ``prompt_sha256`` / ``context_sha256`` legacy fields are ``str``
    so empty string is treated as missing.
    """

    missing: list[str] = []
    # ``prompt_ref`` is int -- 0 / None / negative is treated as missing.
    if not isinstance(legacy_bundle.prompt_ref, int) or legacy_bundle.prompt_ref <= 0:
        missing.append("prompt_ref")
    # ``prompt_sha256`` is str -- empty / whitespace-only is treated as missing.
    if (
        not isinstance(legacy_bundle.prompt_sha256, str)
        or not legacy_bundle.prompt_sha256.strip()
    ):
        missing.append("prompt_sha256")
    # ``context_sha256`` is str -- empty / whitespace-only is treated as missing.
    if (
        not isinstance(legacy_bundle.context_sha256, str)
        or not legacy_bundle.context_sha256.strip()
    ):
        missing.append("context_sha256")
    return tuple(missing)


def derive_authoritative_prompt_context_bundle(
    legacy_bundle: PromptContextBundle,
    *,
    manifest_id: str,
    manifest_digest: str,
    feature_id: str,
    dag_sha256: str,
    task_id: str,
    excluded_evidence_refs: list[EvidencePageRef] | None = None,
    authority: EvidenceAuthority = "execution_authority",
) -> AuthoritativePromptContextBundle:
    """Derive an :class:`AuthoritativePromptContextBundle` from an accepted
    Slice 05 :class:`~iriai_build_v2.workflows.develop.execution.dispatcher.PromptContextBundle`
    record.

    Implements doc-13a § Refactoring Steps step 3 (doc-13a:266-268) +
    the doc-13a:194-211 wrapper spec. Per the doc-13a:42-46 + doc-13a:124-126
    change-control rule the adapter is a **wrapper**; it MUST NOT edit
    the legacy bundle (the legacy bundle is read-only as far as this
    function is concerned -- Pydantic field reads do not mutate the
    source instance).

    **Exact-vs-preview boundary** (doc-13a:115-118 + doc-13a:303-310):

    * Missing required fields (``prompt_ref`` / ``prompt_sha256`` /
      ``context_sha256``) -> raise :class:`MissingPromptContextFieldError`
      (fail-closed; the doc-13a:307-310 ``state="unavailable"`` semantics
      carry through to the typed-failure router via subsequent Slice 13A
      sub-slices that wire dispatch consumers).
    * Non-empty ``truncation_notes`` -> ``completeness.state="paged"``
      (the consumer may treat the bundle as authoritative only when
      exact page refs are supplied via :data:`excluded_evidence_refs`;
      dispatch consumers fail closed when paged completeness has no
      page refs).
    * Empty ``context_file_refs`` AND non-empty ``prompt_summary`` ->
      ``completeness.state="preview_only"`` (legacy fallback; per the
      Slice 13A invariant doc-13a:18-23 the consumer MUST NOT treat
      preview-only evidence as authoritative).
    * Default (all required fields populated; ``truncation_notes`` empty;
      ``context_file_refs`` non-empty) -> ``completeness.state="complete"``
      + ``completeness.authority="execution_authority"`` (the consumer
      may treat the bundle as authoritative).

    The keyword-only arguments after ``legacy_bundle`` supply the
    manifest identity + scope identity that the legacy Slice 05 bundle
    does NOT carry (the legacy bundle predates the doc-13a:127-192 typed
    surface). Future Slice 13A sub-slices that wire dispatch / verifier /
    gate / snapshot / supervisor consumers will supply these values from
    the consumer-side typed sources (e.g. the dispatcher's typed
    :class:`~iriai_build_v2.workflows.develop.execution.dispatcher.DispatchRequest`
    record at ``dispatcher.py:166-226``).

    The ``excluded_evidence_refs`` argument is optional (defaults to an
    empty list); future Slice 13A sub-slices may supply non-empty
    refs from the dispatch-side typed sources. The legacy bundle's
    ``excluded_evidence_ids`` is preserved verbatim on the wrapper for
    compatibility -- the new ``excluded_evidence_refs`` is the typed
    surface consumers should use.

    The ``authority`` argument defaults to ``"execution_authority"`` per
    doc-13a:115-118 (the doc-13a wording permits the default fully-
    resolved legacy bundle to claim execution authority); future Slice
    13A sub-slices that wire gate / routing consumers may pass
    ``"gate_authority"`` / ``"routing_authority"`` per their specific
    decision scope.
    """

    # --- Step 1: fail-closed on missing required fields ------------------
    missing = _missing_required_field_names(legacy_bundle)
    if missing:
        raise MissingPromptContextFieldError(missing)

    # --- Step 2: classify the completeness state per doc-13a:115-118 -----
    has_truncation = _has_truncation(legacy_bundle)
    has_context_file_refs = _has_context_file_refs(legacy_bundle)

    if has_truncation:
        # Doc-13a:115-118 + doc-13a:303-310: bounded semantic context.
        # Runtime consumers treat paged context as authoritative only when
        # exact page refs are carried into completeness.
        completeness_state = "paged"
        completeness_authority = authority
    elif not has_context_file_refs:
        # Doc-13a:115-118: legacy fallback (display preview only); the
        # consumer MUST NOT treat preview-only evidence as authoritative.
        completeness_state = "preview_only"
        # Force display_only authority for preview_only state -- per the
        # Slice 13A invariant doc-13a:18-23 + doc-13a:111-115 (Blocking
        # deviations) a preview cannot carry execution authority.
        completeness_authority = "display_only"
    else:
        # Default: fully-resolved legacy bundle; the consumer may treat
        # the bundle as authoritative.
        completeness_state = "complete"
        completeness_authority = authority

    # --- Step 3: build the per-decision completeness record --------------
    # The complete_for list names the decision scope this completeness
    # covers; for the legacy Slice 05 bundle the scope is the task_id +
    # the feature_id (the legacy bundle is task-scoped per dispatcher.py).
    complete_for = [f"task:{task_id}", f"feature:{feature_id}"]

    # excluded_evidence_refs defaults to empty list per the keyword arg
    # signature; the legacy bundle's excluded_evidence_ids is preserved
    # verbatim on the wrapper for compatibility but the typed page-refs
    # are the new authoritative surface.
    resolved_excluded_refs: list[EvidencePageRef] = list(excluded_evidence_refs or [])

    # Build the completeness digest deterministically via the second
    # sub-slice's helper -- two adapter calls with the same logical input
    # produce byte-identical digests.
    completeness_digest = compute_completeness_digest(
        state=completeness_state,
        authority=completeness_authority,
        complete_for=complete_for,
        missing_required_refs=[],
        page_refs=resolved_excluded_refs,
        preview_ref=None,
        unavailable_reason=None,
    )

    completeness = EvidenceCompleteness(
        state=completeness_state,
        authority=completeness_authority,
        complete_for=complete_for,
        missing_required_refs=[],
        page_refs=resolved_excluded_refs,
        preview_ref=None,
        unavailable_reason=None,
        completeness_digest=completeness_digest,
    )

    # --- Step 4: build the authoritative context ref ----------------------
    context_manifest_ref = AuthoritativeContextRef(
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
        completeness_digest=completeness_digest,
        required_complete_for=complete_for,
        authority=completeness_authority,
    )

    # --- Step 5: assemble the wrapper -------------------------------------
    # Field renames per doc-13a:201:
    #   legacy prompt_summary -> wrapper display_prompt_summary
    # Field carry-overs (verbatim from the legacy Slice 05 bundle):
    #   prompt_ref, prompt_sha256, context_file_refs, context_file_paths,
    #   context_sha256, included_contract_ids, included_evidence_ids,
    #   excluded_evidence_ids, truncation_notes
    # Field renames per doc-13a:209-210 (NEW typed surfaces):
    #   excluded_evidence_refs (typed page-refs; new),
    #   completeness (typed EvidenceCompleteness; new)
    # Reference per doc-13a:202 (NEW typed surface):
    #   context_manifest_ref (typed AuthoritativeContextRef; new)
    # Display metadata preserved per doc-13a:213-215:
    #   truncation_notes (legacy display string list; preserved verbatim)
    _ = dag_sha256  # Reserved for future-slice ExactEvidenceManifest wiring.
    return AuthoritativePromptContextBundle(
        prompt_ref=legacy_bundle.prompt_ref,
        prompt_sha256=legacy_bundle.prompt_sha256,
        display_prompt_summary=legacy_bundle.prompt_summary,
        context_manifest_ref=context_manifest_ref,
        context_file_refs=list(legacy_bundle.context_file_refs),
        context_file_paths=list(legacy_bundle.context_file_paths),
        context_sha256=legacy_bundle.context_sha256,
        included_contract_ids=list(legacy_bundle.included_contract_ids),
        included_evidence_ids=list(legacy_bundle.included_evidence_ids),
        excluded_evidence_ids=list(legacy_bundle.excluded_evidence_ids),
        excluded_evidence_refs=resolved_excluded_refs,
        completeness=completeness,
        # Display metadata preserved verbatim per doc-13a:213-215.
        truncation_notes=list(legacy_bundle.truncation_notes),
    )
