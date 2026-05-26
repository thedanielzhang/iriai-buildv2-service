"""Slice 13A fifth sub-slice -- gate companion record + typed proof row.

This module implements **doc-13a Refactoring Steps step 5 + step 6**
co-bundled into one sub-slice
(``docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md:273-278``):

    Step 5 (doc-13a:273-275): Add a 13A gate companion record so
    model verifier input is either complete for the gate scope or
    exactly paged. A gate may not approve from ``preview_only``
    evidence after 13A is enabled.

    Step 6 (doc-13a:276-278): Replace any deterministic-summary
    escape hatch in post-13A gates with explicit typed proof rows. A
    summary can satisfy a required gate only if the proof row states
    the exact source digest, page refs, proof algorithm, and
    verification time.

It is the **SECOND executor wiring** of the 13A typed surfaces (after
the fourth sub-slice's dispatcher wiring); per the auto-memory
``feedback_no_refactor`` rule the wiring lands as a **NEW opt-in code
path** on top of the accepted Slice 06 gate / verifier boundary. Per
doc-13a:42-46 + doc-13a:124-126 the accepted Slice 06
``GateRunner.run_preflight`` / ``ContextPackageBuilder.build`` /
``GraphApprovalProof`` shapes at
``src/iriai_build_v2/workflows/develop/execution/gates.py:768`` /
``:526`` + ``src/iriai_build_v2/workflows/develop/execution/verification.py:254``
remain **byte-identical**; this module exposes a NEW opt-in
:class:`AuthoritativeGateCompanionPort` that wraps the legacy gate
boundary from OUTSIDE (NOT a ``GateRunner`` constructor parameter --
the ``GateRunner`` constructor is accepted Slice 06 and READ-ONLY).
When the consumer wires the new port, it routes through
:class:`AuthoritativeGateCompanionRecord` (per doc-13a:273-275) +
:class:`AuthoritativeGateProofRow` (per doc-13a:276-278) instead of
treating the raw gate result as authoritative.

**Co-bundle decision** (recorded in the BEFORE journal entry +
JSONL row). Step 5 + step 6 land as ONE sub-slice because:

1. Sibling typed shapes share base imports
   (:class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
   + :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
   + :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef`).
2. Shared typed failure ids -- both step 5 (gate companion record
   ``state="preview_only"``) and step 6 (proof row missing required
   field) route to the ``verifier_context`` failure_class.
3. Doc-13a:276-278 step 6 spec is **conditional on existence** of an
   escape hatch -- the live-grep NEGATIVE finding (no escape hatch
   exists in current execution code) reduces step 6 to **adding**
   the typed :class:`AuthoritativeGateProofRow` shape so future
   consumers MUST use it. The closest match is the **defensive
   rejection** at
   ``src/iriai_build_v2/workflows/develop/phases/implementation.py:13491``
   (``"checkpoint requires aggregate graph proof, not summary-only
   approval."``) which is the OPPOSITE of an escape hatch.

**Change-control non-negotiables** (doc-13a:42-46 + 124-126 +
auto-memory ``feedback_no_refactor``):

* This module MUST NOT edit
  ``src/iriai_build_v2/workflows/develop/execution/gates.py`` /
  ``verification.py`` / ``post_dag_gates.py`` in-place; the new opt-in
  surface wraps the legacy gate boundary externally.
* The legacy :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunResult`
  shape at ``gates.py:747`` + :class:`~iriai_build_v2.workflows.develop.execution.verification.GraphApprovalProof`
  shape at ``verification.py:254`` are preserved **verbatim**; the new
  shapes carry the legacy payloads via composition (NOT replacement).

**Fail-closed contract** (doc-13a:273-275 + doc-13a:276-278 + auto-memory
``feedback_no_silent_degradation``):

* When the gate-companion-record state is ``"preview_only"`` (per
  doc-13a:273-275 "A gate may not approve from ``preview_only``
  evidence after 13A is enabled"), :func:`derive_gate_companion`
  raises :class:`MissingGateCompanionFieldError` carrying the typed
  failure id ``verifier_context/companion_record_unavailable``.
* When the gate-companion-record's ``required_complete_for`` cannot be
  satisfied by the manifest's ``complete_for`` (per doc-13a:273-275
  "model verifier input is either complete for the gate scope or
  exactly paged"), :func:`derive_gate_companion` raises
  :class:`MissingGateCompanionFieldError`.
* When :func:`derive_proof_row` cannot find one or more of the 4
  mandatory fields per doc-13a:276-278 (``source_digest`` +
  ``page_refs`` + ``proof_algorithm`` + ``verification_time``), the
  helper raises :class:`MissingProofRowFieldError` carrying the typed
  failure id ``verifier_context/proof_row_required``.
* The two typed failure ids land as a pure-data add on the Slice 07
  router at
  ``src/iriai_build_v2/workflows/develop/execution/failure_router.py``
  (NEW enumerators on ``FailureType`` Literal + entries on
  ``FAILURE_TYPES`` tuple + entries on ``_DETERMINISTIC_FAILURE_TYPES``
  set + ``_route(...)`` rows routing to ``quiesce``).

**Implementation discipline** (stdlib + Pydantic + the in-package
sanctioned surfaces only):

* Stdlib (``typing``) + Pydantic v2 +
  :mod:`iriai_build_v2.execution_control.completeness` (the second
  sub-slice's foundational typed shapes; READ-ONLY consumer) +
  :mod:`iriai_build_v2.execution_control.prompt_context_adapter` (the
  third sub-slice's compatibility adapter; READ-ONLY consumer).
* NO imports from ``governance/`` (the governance layer consumes
  execution-control surfaces, not the reverse).
* NO imports from other parts of ``execution_control/`` beyond
  ``completeness`` + ``prompt_context_adapter``.
* NO imports from ``workflows/develop/execution/`` (the legacy gate /
  verifier surfaces are the WRAPPED boundary, not a dependency of
  the new module; the consumer-side wiring lives in the future
  Slice 13A sub-slice that wires this module into the verifier /
  checkpoint consumer).

**Namespace decision** (doc-13a:273-278 + execution_control namespace
precedent from the second + third + fourth sub-slices). This module
lives at ``src/iriai_build_v2/execution_control/gate_companion.py``
alongside ``completeness.py`` + ``prompt_context_adapter.py`` +
``dispatcher_prompt_context.py`` per the doc-13a:273-278 ownership
wording. It is **NOT re-exported** from
``src/iriai_build_v2/execution_control/__init__.py`` (precedent: the
Slice 13A second + third + fourth sub-slices did NOT touch
``__init__.py``).
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

# Slice 13A second sub-slice foundational typed shapes (READ-ONLY consumer).
# Citations per doc-13a:127-192 (typed shapes) + doc-13a:264 (digest helper).
from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    EvidenceAuthority,
    EvidenceCompleteness,
    EvidencePageRef,
    compute_completeness_digest,
)

# Slice 13A third sub-slice compatibility adapter (READ-ONLY consumer).
# Per doc-13a:273-275 the gate companion record projects the
# AuthoritativePromptContextBundle's manifest_ref + completeness onto
# the gate scope; the adapter provides the upstream typed surface.
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
)


__all__ = [
    # Step 5 (doc-13a:273-275) typed shapes + helpers + port.
    "AuthoritativeGateCompanionRecord",
    "AuthoritativeGateApprovalRouting",
    "AuthoritativeGateCompanionPort",
    "LegacyGateCompanionAdapter",
    "derive_gate_companion",
    # Step 6 (doc-13a:276-278) typed shapes + helpers.
    "AuthoritativeGateProofRow",
    "derive_proof_row",
    # Fail-closed typed exceptions (per feedback_no_silent_degradation).
    "MissingGateCompanionFieldError",
    "MissingProofRowFieldError",
]


# --- Fail-closed typed exceptions ------------------------------------------


class MissingGateCompanionFieldError(ValueError):
    """Raised when an :class:`AuthoritativeGateCompanionRecord` cannot
    be derived from the given inputs.

    Per the auto-memory ``feedback_no_silent_degradation`` rule + per
    doc-13a:273-275 ("A gate may not approve from ``preview_only``
    evidence after 13A is enabled"): the helper MUST NOT silently
    return a degraded companion record when the inputs do not satisfy
    the gate-scope completeness contract. It raises this typed
    exception so the caller can route the typed failure id
    ``verifier_context/companion_record_unavailable`` (registered at
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`).

    The exception carries ``missing_field_names`` (the tuple of fields
    the helper could not find) + ``unavailable_reason`` (the
    classifier reason the upstream ``EvidenceCompleteness`` reported,
    if any) + ``gate_scope_id`` (the gate scope the helper was trying
    to derive a companion for). Inherits :class:`ValueError` so any
    caller that already catches :class:`ValueError` for malformed-input
    handling sees the failure (mirrors the
    :class:`~iriai_build_v2.execution_control.prompt_context_adapter.MissingPromptContextFieldError`
    sibling precedent which also inherits :class:`ValueError`).
    """

    def __init__(
        self,
        missing_field_names: Sequence[str],
        *,
        unavailable_reason: str | None = None,
        gate_scope_id: str | None = None,
    ) -> None:
        # Defensive copy to a tuple so the public attribute is immutable.
        self.missing_field_names: tuple[str, ...] = tuple(missing_field_names)
        self.unavailable_reason = unavailable_reason
        self.gate_scope_id = gate_scope_id
        joined = ", ".join(self.missing_field_names) if self.missing_field_names else "(none)"
        reason_clause = f"; reason: {unavailable_reason}" if unavailable_reason else ""
        scope_clause = f"; gate_scope_id: {gate_scope_id}" if gate_scope_id else ""
        super().__init__(
            f"gate companion record is unavailable for the gate scope: "
            f"missing field(s): {joined}{reason_clause}{scope_clause}"
        )


class MissingProofRowFieldError(ValueError):
    """Raised when an :class:`AuthoritativeGateProofRow` cannot be
    derived because one or more of the 4 mandatory fields per
    doc-13a:276-278 (``source_digest`` + ``page_refs`` +
    ``proof_algorithm`` + ``verification_time``) is missing or empty.

    Per the auto-memory ``feedback_no_silent_degradation`` rule + per
    doc-13a:276-278 ("A summary can satisfy a required gate only if
    the proof row states the exact source digest, page refs, proof
    algorithm, and verification time"): the helper MUST NOT silently
    emit a typed proof row with placeholder / empty mandatory fields.
    It raises this typed exception so the caller can route the typed
    failure id ``verifier_context/proof_row_required`` (registered at
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`).

    The exception carries ``missing_field_names`` (the tuple of the 4
    mandatory fields the helper could not find or that were empty)
    plus an optional ``summary_digest`` (the proof-row's summary
    digest, when known) for cross-process observability. Inherits
    :class:`ValueError` per the sibling :class:`MissingGateCompanionFieldError`
    + :class:`~iriai_build_v2.execution_control.prompt_context_adapter.MissingPromptContextFieldError`
    precedents.
    """

    def __init__(
        self,
        missing_field_names: Sequence[str],
        *,
        summary_digest: str | None = None,
    ) -> None:
        # Defensive copy to a tuple so the public attribute is immutable.
        self.missing_field_names: tuple[str, ...] = tuple(missing_field_names)
        self.summary_digest = summary_digest
        joined = ", ".join(self.missing_field_names) if self.missing_field_names else "(none)"
        digest_clause = (
            f"; summary_digest: {summary_digest}" if summary_digest else ""
        )
        super().__init__(
            f"typed proof row is required but mandatory field(s) are missing: "
            f"{joined}{digest_clause}"
        )


# --- Step 5 typed shapes (doc-13a:273-275) ----------------------------------


class AuthoritativeGateApprovalRouting(BaseModel):
    """Doc-13a:273-275 -- the routing decision derived from the
    authoritative gate companion record.

    Carries the typed signal the gate consumer needs to either approve
    the gate (state="complete" / "paged") or record the typed failure
    id ``verifier_context/companion_record_unavailable`` and NOT
    approve the gate (state="preview_only" / "unavailable" / helper
    raised :class:`MissingGateCompanionFieldError`).

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    routing is **fail-closed**: when ``should_approve_gate=False`` the
    gate consumer MUST record the typed failure id and MUST NOT
    approve the gate. The typed failure id is the Slice 13A
    fifth-sub-slice failure ``verifier_context/companion_record_unavailable``
    (registered in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    per this iteration's chunk shape point 2 ADD).
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # in ``completeness.py`` + ``prompt_context_adapter.py`` +
    # ``dispatcher_prompt_context.py`` -- unknown fields fail closed.
    model_config = ConfigDict(extra="forbid")

    should_approve_gate: bool
    """When True, gate consumer proceeds to approve the gate with the
    authoritative companion record attached. When False, gate consumer
    records the typed failure id and does NOT approve per
    doc-13a:273-275."""

    typed_failure_class: Literal["verifier_context"] | None = None
    """The typed-failure router ``failure_class`` when
    ``should_approve_gate=False``; ``None`` when
    ``should_approve_gate=True``. Currently only the single value
    ``verifier_context`` is supported (doc-13a:273-275 +
    doc-13a:276-278; the ``verifier_context`` failure_class already
    exists at ``failure_router.py:34-35``)."""

    typed_failure_type: (
        Literal["companion_record_unavailable", "proof_row_required"] | None
    ) = None
    """The typed-failure router ``failure_type`` when
    ``should_approve_gate=False``; ``None`` when
    ``should_approve_gate=True``. The two typed failure ids registered
    in :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    per the Slice 13A fifth sub-slice are
    ``companion_record_unavailable`` (per doc-13a:273-275 fail-closed
    on ``state="preview_only"``) + ``proof_row_required`` (per
    doc-13a:276-278 fail-closed on missing mandatory proof-row
    fields)."""

    unavailable_reason: str | None = None
    """Human-readable reason when ``should_approve_gate=False``;
    rendered into the typed-failure record details for downstream
    observability (dashboard / supervisor / governance). ``None``
    when ``should_approve_gate=True``."""

    missing_field_names: tuple[str, ...] = Field(default_factory=tuple)
    """The missing-required-field names the helper raised on, per the
    :class:`MissingGateCompanionFieldError` / :class:`MissingProofRowFieldError`
    typed exceptions. Empty tuple when ``should_approve_gate=True``
    (no validation error was raised)."""


class AuthoritativeGateCompanionRecord(BaseModel):
    """Doc-13a:273-275 -- the 13A gate companion record.

    Carries the typed completeness contract for one gate scope plus the
    gate input digest + the routing decision. The 6 fields project the
    Slice 13A second sub-slice's foundational
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    + :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    onto the gate scope:

    * :data:`gate_scope_id` -- the gate scope this companion record
      covers (e.g. ``"gate:atomic_landing"`` /
      ``"gate:code_review:g3"``); per doc-13a:273-275 the gate
      companion record is per-gate-scope (each gate may have its own
      companion record).
    * :data:`gate_input_digest` -- SHA-256 hex digest over the gate's
      input payload (typically the
      :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRequest`'s
      ``idempotency_key`` + ``manifest_digest`` + bounded-context
      ``package_hash``); the digest ties the companion record to a
      specific gate input.
    * :data:`completeness` -- the typed
      :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
      record covering the gate scope; per the Slice 13A invariant
      doc-13a:18-23 the gate consumer drives gate approval only when
      ``completeness.state in {"complete", "paged"}`` AND
      ``completeness.authority in {"execution_authority",
      "gate_authority"}``.
    * :data:`context_manifest_ref` -- the typed
      :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
      pointing at the
      :class:`~iriai_build_v2.execution_control.completeness.ExactEvidenceManifest`
      the gate consumer reads.
    * :data:`approval_routing` -- the typed
      :class:`AuthoritativeGateApprovalRouting` carrying the
      should_approve_gate decision derived from the completeness
      state.
    * :data:`proof_rows` -- the list of typed
      :class:`AuthoritativeGateProofRow` records carrying any
      deterministic-summary proof rows per doc-13a:276-278 ("A
      summary can satisfy a required gate only if the proof row
      states the exact source digest, page refs, proof algorithm,
      and verification time"). Empty list is valid -- a gate scope
      may have NO deterministic-summary proof rows attached (the
      typed companion record alone satisfies the gate).

    Per the Slice 13A invariant doc-13a:18-23: if the consumer can
    influence dispatch / verification / merge / checkpoint / routing /
    scheduler feedback / policy recommendation, it MUST consume
    :data:`completeness` (the typed
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    record) + :data:`approval_routing` -- a ``preview_only`` or
    ``unavailable`` state MUST drive ``approval_routing.should_approve_gate=False``.
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # -- unknown fields fail closed (the gate consumer cannot smuggle
    # an undocumented field through the companion record).
    model_config = ConfigDict(extra="forbid")

    gate_scope_id: str
    """Doc-13a:273-275 -- the gate scope this companion record covers
    (e.g. ``"gate:atomic_landing"`` / ``"gate:code_review:g3"``); per
    doc-13a:273-275 the gate companion record is per-gate-scope."""

    gate_input_digest: str
    """SHA-256 hex digest over the gate's input payload. Ties the
    companion record to a specific gate input; per the Slice 13A
    invariant doc-13a:298-301 the digest is the cross-process
    freshness contract: re-deriving the companion record for the same
    gate input MUST produce the same ``gate_input_digest`` (or fail
    as stale/corrupt)."""

    completeness: EvidenceCompleteness
    """Doc-13a:273-275 -- the typed completeness record for the gate
    scope. Per the Slice 13A invariant doc-13a:18-23 + doc-13a:273-275
    the gate consumer drives gate approval only when
    ``completeness.state in {"complete", "paged"}`` AND
    ``completeness.authority`` covers the gate scope."""

    context_manifest_ref: AuthoritativeContextRef
    """Doc-13a:273-275 -- the lightweight
    :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    pointing at an
    :class:`~iriai_build_v2.execution_control.completeness.ExactEvidenceManifest`
    for the gate scope."""

    approval_routing: AuthoritativeGateApprovalRouting
    """Doc-13a:273-275 -- the typed routing decision derived from
    :data:`completeness`. Per the auto-memory
    ``feedback_no_silent_degradation`` rule: ``state="preview_only"``
    or ``state="unavailable"`` MUST produce
    ``approval_routing.should_approve_gate=False`` + the typed failure
    id ``verifier_context/companion_record_unavailable``."""

    proof_rows: list["AuthoritativeGateProofRow"] = Field(default_factory=list)
    """Doc-13a:276-278 -- the list of typed proof rows attached to
    the gate companion record. Empty list is valid (a gate scope
    may have NO deterministic-summary proof rows attached; the typed
    companion record alone satisfies the gate). Per doc-13a:276-278
    each proof row carries the 4 mandatory fields (``source_digest``
    + ``page_refs`` + ``proof_algorithm`` + ``verification_time``)
    that allow a deterministic summary to satisfy a required gate."""


# --- Step 6 typed shape (doc-13a:276-278) -----------------------------------


class AuthoritativeGateProofRow(BaseModel):
    """Doc-13a:276-278 -- the typed proof row that allows a
    deterministic summary to satisfy a required gate.

    Per doc-13a:276-278: "Replace any deterministic-summary escape
    hatch in post-13A gates with explicit typed proof rows. A summary
    can satisfy a required gate only if the proof row states the
    exact source digest, page refs, proof algorithm, and verification
    time."

    The 4 mandatory fields are:

    1. :data:`source_digest` -- the SHA-256 hex digest of the source
       evidence the summary was derived from. Per doc-13a:298-301 the
       digest is the cross-process freshness contract: re-deriving
       the proof row for the same source MUST produce the same
       digest (or fail as stale/corrupt).
    2. :data:`page_refs` -- the list of typed
       :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef`
       records identifying the exact pages of source evidence the
       summary covers.
    3. :data:`proof_algorithm` -- the name of the algorithm used to
       derive the summary from the source evidence (e.g.
       ``"sha256_concatenation"`` / ``"merkle_root"`` /
       ``"deterministic_aggregate_v1"``). Per doc-13a:276-278 the
       algorithm name MUST be stated so cross-process consumers can
       re-verify the summary.
    4. :data:`verification_time` -- the ISO-8601 UTC timestamp the
       proof row was verified at (e.g. ``"2026-05-26T09:30:00Z"``).
       Per doc-13a:276-278 the verification time MUST be stated so
       cross-process consumers can decide whether to accept stale
       proof rows.

    Plus optional :data:`summary_digest` (the SHA-256 hex digest of
    the deterministic summary text itself; useful for observability)
    and optional :data:`proof_metadata` (a free-form dict carrying
    algorithm-specific metadata).

    Per the Slice 13A invariant doc-13a:18-23: the proof row is the
    ONLY surface that allows a deterministic summary to satisfy a
    required gate; lossy summaries / previews without a proof row are
    display-only.
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # -- unknown fields fail closed (a malformed proof row that adds
    # an undocumented field cannot pass validation).
    model_config = ConfigDict(extra="forbid")

    source_digest: str
    """Doc-13a:276-278 -- SHA-256 hex digest of the source evidence
    the summary was derived from. MUST be non-empty; an empty digest
    raises :class:`MissingProofRowFieldError` from
    :func:`derive_proof_row` per the fail-closed rule."""

    page_refs: list[EvidencePageRef]
    """Doc-13a:276-278 -- list of typed
    :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef`
    records identifying the exact pages of source evidence the summary
    covers. MUST be non-empty; an empty list raises
    :class:`MissingProofRowFieldError` from :func:`derive_proof_row`
    per the fail-closed rule (a proof row without page refs cannot
    point at any source evidence)."""

    proof_algorithm: str
    """Doc-13a:276-278 -- the name of the algorithm used to derive
    the summary from the source evidence (e.g.
    ``"sha256_concatenation"``). MUST be non-empty; an empty string
    raises :class:`MissingProofRowFieldError` from
    :func:`derive_proof_row` per the fail-closed rule."""

    verification_time: str
    """Doc-13a:276-278 -- ISO-8601 UTC timestamp the proof row was
    verified at (e.g. ``"2026-05-26T09:30:00Z"``). MUST be non-empty;
    an empty string raises :class:`MissingProofRowFieldError` from
    :func:`derive_proof_row` per the fail-closed rule."""

    summary_digest: str | None = None
    """Optional SHA-256 hex digest of the deterministic summary text
    itself; useful for observability. NOT in the doc-13a:276-278
    mandatory field set, so empty / None does NOT raise
    :class:`MissingProofRowFieldError`."""

    proof_metadata: dict[str, Any] = Field(default_factory=dict)
    """Optional free-form dict carrying algorithm-specific metadata
    (e.g. tree depth for merkle proofs; partition count for
    deterministic aggregates). NOT in the doc-13a:276-278 mandatory
    field set, so empty dict does NOT raise
    :class:`MissingProofRowFieldError`."""


# --- Step 5 helper (doc-13a:273-275) ----------------------------------------


def _gate_scope_id_from_arg(gate_scope_id: str | None) -> str:
    """Validate that the gate_scope_id is non-empty.

    Per doc-13a:273-275 the gate companion record is per-gate-scope;
    an empty / whitespace-only gate_scope_id cannot identify a gate.
    """

    if not isinstance(gate_scope_id, str) or not gate_scope_id.strip():
        raise MissingGateCompanionFieldError(
            ["gate_scope_id"],
            unavailable_reason="gate_scope_id is required",
            gate_scope_id=None,
        )
    return gate_scope_id


def _gate_input_digest_from_arg(gate_input_digest: str | None) -> str:
    """Validate that the gate_input_digest is non-empty.

    Per the Slice 13A invariant doc-13a:298-301 the digest is the
    cross-process freshness contract; an empty / whitespace-only
    digest cannot tie the companion record to a specific gate input.
    """

    if (
        not isinstance(gate_input_digest, str)
        or not gate_input_digest.strip()
    ):
        raise MissingGateCompanionFieldError(
            ["gate_input_digest"],
            unavailable_reason="gate_input_digest is required",
            gate_scope_id=None,
        )
    return gate_input_digest


def derive_gate_companion(
    authoritative_bundle: AuthoritativePromptContextBundle,
    *,
    gate_scope_id: str,
    gate_input_digest: str,
    proof_rows: Sequence[AuthoritativeGateProofRow] = (),
) -> AuthoritativeGateCompanionRecord:
    """Derive an :class:`AuthoritativeGateCompanionRecord` from an
    :class:`~iriai_build_v2.execution_control.prompt_context_adapter.AuthoritativePromptContextBundle`.

    Implements **doc-13a Refactoring Steps step 5** verbatim
    (doc-13a:273-275): "Add a 13A gate companion record so model
    verifier input is either complete for the gate scope or exactly
    paged. A gate may not approve from ``preview_only`` evidence after
    13A is enabled."

    **Fail-closed contract** (doc-13a:273-275 + auto-memory
    ``feedback_no_silent_degradation``):

    * Empty / whitespace-only ``gate_scope_id`` ->
      :class:`MissingGateCompanionFieldError`.
    * Empty / whitespace-only ``gate_input_digest`` ->
      :class:`MissingGateCompanionFieldError`.
    * Bundle's ``completeness.state == "preview_only"`` ->
      :class:`MissingGateCompanionFieldError` (per doc-13a:273-275
      "A gate may not approve from ``preview_only`` evidence after
      13A is enabled"). The third sub-slice adapter already forces
      ``authority="display_only"`` on ``preview_only`` state per
      doc-13a:18-23 + 111-115; this helper's check is the gate-side
      enforcement of the same invariant.
    * Bundle's ``completeness.state == "unavailable"`` ->
      :class:`MissingGateCompanionFieldError` (per doc-13a:303-310
      "Required evidence cannot be paged exactly: return
      ``state='unavailable'`` ... fail closed").

    **Approval contract** (doc-13a:273-275 + doc-13a:18-23):

    * ``completeness.state == "complete"`` -> ``approval_routing.should_approve_gate=True``
      (the consumer may approve the gate; the typed companion record
      carries the exact-evidence proof).
    * ``completeness.state == "paged"`` -> ``approval_routing.should_approve_gate=True``
      (per doc-13a:273-275 "model verifier input is either complete
      for the gate scope or exactly paged"; the consumer may approve
      the gate; the typed companion record carries the page-refs the
      consumer must traverse for the full content).

    **Proof rows** (doc-13a:276-278 sibling). The optional
    ``proof_rows`` argument carries the list of typed
    :class:`AuthoritativeGateProofRow` records attached to the gate
    companion record. Per doc-13a:276-278 each proof row carries the
    4 mandatory fields (``source_digest`` + ``page_refs`` +
    ``proof_algorithm`` + ``verification_time``) that allow a
    deterministic summary to satisfy a required gate. Empty list is
    valid (a gate scope may have NO deterministic-summary proof rows
    attached).
    """

    # --- Step 1: validate the gate-scope identity arguments ---------------
    validated_scope_id = _gate_scope_id_from_arg(gate_scope_id)
    validated_input_digest = _gate_input_digest_from_arg(gate_input_digest)

    # --- Step 2: classify the upstream completeness state ----------------
    bundle_completeness = authoritative_bundle.completeness
    state = bundle_completeness.state

    if state == "preview_only":
        # Doc-13a:273-275 + doc-13a:18-23 + doc-13a:111-115: a gate
        # MUST NOT approve from preview_only evidence after 13A is
        # enabled. Fail closed via the typed exception so the caller
        # can route the typed failure id
        # verifier_context/companion_record_unavailable.
        raise MissingGateCompanionFieldError(
            ["completeness.state"],
            unavailable_reason=(
                "upstream bundle completeness.state='preview_only' "
                "cannot satisfy gate scope per doc-13a:273-275"
            ),
            gate_scope_id=validated_scope_id,
        )

    if state == "unavailable":
        # Doc-13a:303-310: Required evidence cannot be paged exactly;
        # fail closed via the typed exception so the caller can route
        # the typed failure id verifier_context/companion_record_unavailable.
        raise MissingGateCompanionFieldError(
            ["completeness.state"],
            unavailable_reason=(
                bundle_completeness.unavailable_reason
                or "upstream bundle completeness.state='unavailable'"
            ),
            gate_scope_id=validated_scope_id,
        )

    # --- Step 3: build the gate-scope completeness record ---------------
    # The complete_for list names the gate scope this companion record
    # covers; per doc-13a:273-275 each gate has its own companion record.
    gate_complete_for = [validated_scope_id]

    # The gate-scope completeness mirrors the upstream bundle's
    # completeness but re-scopes the complete_for list to the gate.
    # Authority is GATE_AUTHORITY per doc-13a:135-141.
    gate_authority: EvidenceAuthority = "gate_authority"

    gate_completeness_digest = compute_completeness_digest(
        state=state,
        authority=gate_authority,
        complete_for=gate_complete_for,
        missing_required_refs=list(bundle_completeness.missing_required_refs),
        page_refs=list(bundle_completeness.page_refs),
        preview_ref=bundle_completeness.preview_ref,
        unavailable_reason=bundle_completeness.unavailable_reason,
    )

    gate_completeness = EvidenceCompleteness(
        state=state,
        authority=gate_authority,
        complete_for=gate_complete_for,
        missing_required_refs=list(bundle_completeness.missing_required_refs),
        page_refs=list(bundle_completeness.page_refs),
        preview_ref=bundle_completeness.preview_ref,
        unavailable_reason=bundle_completeness.unavailable_reason,
        completeness_digest=gate_completeness_digest,
    )

    # --- Step 4: build the gate-scope context manifest ref --------------
    # The manifest identity carries through from the upstream bundle;
    # the completeness_digest changes (gate-scoped) so consumers can
    # detect that the gate companion record is a re-scoped projection
    # of the upstream bundle's manifest.
    upstream_ref = authoritative_bundle.context_manifest_ref
    context_manifest_ref = AuthoritativeContextRef(
        manifest_id=upstream_ref.manifest_id,
        manifest_digest=upstream_ref.manifest_digest,
        completeness_digest=gate_completeness_digest,
        required_complete_for=gate_complete_for,
        authority=gate_authority,
    )

    # --- Step 5: build the approval routing (should_approve_gate=True) --
    # We reached this point because state in {"complete", "paged"};
    # both states allow gate approval per doc-13a:273-275 ("either
    # complete for the gate scope or exactly paged").
    approval_routing = AuthoritativeGateApprovalRouting(
        should_approve_gate=True,
        typed_failure_class=None,
        typed_failure_type=None,
        unavailable_reason=None,
        missing_field_names=(),
    )

    # --- Step 6: assemble the companion record --------------------------
    return AuthoritativeGateCompanionRecord(
        gate_scope_id=validated_scope_id,
        gate_input_digest=validated_input_digest,
        completeness=gate_completeness,
        context_manifest_ref=context_manifest_ref,
        approval_routing=approval_routing,
        proof_rows=list(proof_rows),
    )


# --- Step 6 helper (doc-13a:276-278) ----------------------------------------


_PROOF_ROW_MANDATORY_FIELDS: tuple[str, ...] = (
    "source_digest",
    "page_refs",
    "proof_algorithm",
    "verification_time",
)


def _missing_proof_row_fields(
    *,
    source_digest: str | None,
    page_refs: Sequence[EvidencePageRef] | None,
    proof_algorithm: str | None,
    verification_time: str | None,
) -> tuple[str, ...]:
    """Return the tuple of mandatory fields that are missing / empty.

    Per doc-13a:276-278 the 4 mandatory fields are ``source_digest``
    + ``page_refs`` + ``proof_algorithm`` + ``verification_time``.
    Empty / whitespace-only strings + None + empty lists are treated
    as missing per the fail-closed rule (a proof row with placeholder
    values cannot serve as the typed proof per doc-13a:276-278).
    """

    missing: list[str] = []
    if not isinstance(source_digest, str) or not source_digest.strip():
        missing.append("source_digest")
    if page_refs is None or not list(page_refs):
        missing.append("page_refs")
    if not isinstance(proof_algorithm, str) or not proof_algorithm.strip():
        missing.append("proof_algorithm")
    if not isinstance(verification_time, str) or not verification_time.strip():
        missing.append("verification_time")
    return tuple(missing)


def derive_proof_row(
    *,
    source_digest: str,
    page_refs: Sequence[EvidencePageRef],
    proof_algorithm: str,
    verification_time: str,
    summary_digest: str | None = None,
    proof_metadata: dict[str, Any] | None = None,
) -> AuthoritativeGateProofRow:
    """Derive an :class:`AuthoritativeGateProofRow` after validating
    the 4 mandatory fields per doc-13a:276-278.

    Implements **doc-13a Refactoring Steps step 6** verbatim
    (doc-13a:276-278): "Replace any deterministic-summary escape
    hatch in post-13A gates with explicit typed proof rows. A summary
    can satisfy a required gate only if the proof row states the
    exact source digest, page refs, proof algorithm, and verification
    time."

    **Fail-closed contract** (doc-13a:276-278 + auto-memory
    ``feedback_no_silent_degradation``):

    * Missing / empty ``source_digest`` ->
      :class:`MissingProofRowFieldError`.
    * Missing / empty ``page_refs`` ->
      :class:`MissingProofRowFieldError`.
    * Missing / empty ``proof_algorithm`` ->
      :class:`MissingProofRowFieldError`.
    * Missing / empty ``verification_time`` ->
      :class:`MissingProofRowFieldError`.
    * Multiple missing fields -> all reported in a single
      :class:`MissingProofRowFieldError` (NOT one-by-one; per the
      auto-memory ``feedback_never_truncate_decisions`` rule the
      caller receives ALL missing fields at once).

    The helper is the ONLY sanctioned constructor for
    :class:`AuthoritativeGateProofRow`; future consumers MUST go
    through this helper so the fail-closed validation runs.
    """

    missing = _missing_proof_row_fields(
        source_digest=source_digest,
        page_refs=page_refs,
        proof_algorithm=proof_algorithm,
        verification_time=verification_time,
    )
    if missing:
        raise MissingProofRowFieldError(
            missing,
            summary_digest=summary_digest,
        )

    return AuthoritativeGateProofRow(
        source_digest=source_digest,
        page_refs=list(page_refs),
        proof_algorithm=proof_algorithm,
        verification_time=verification_time,
        summary_digest=summary_digest,
        proof_metadata=dict(proof_metadata or {}),
    )


# --- Port Protocol --------------------------------------------------------


class AuthoritativeGateCompanionPort(Protocol):
    """The opt-in Protocol the gate consumer's NEW constructor port
    accepts.

    Mirrors the Slice 13A fourth sub-slice's
    :class:`~iriai_build_v2.execution_control.dispatcher_prompt_context.AuthoritativePromptBuilderPort`
    Protocol -- the new opt-in port is additive on top of the accepted
    Slice 06 gate boundary, NOT a replacement of any existing port.
    The legacy
    :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunner`
    constructor at ``gates.py:756-766`` remains BYTE-IDENTICAL; the
    new port wraps the legacy
    :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunResult`
    OUTSIDE the legacy boundary per the doc-13a:42-46 + 124-126
    change-control rule.

    Implementations:

    * :class:`LegacyGateCompanionAdapter` -- the reference adapter
      that derives an :class:`AuthoritativeGateCompanionRecord` from
      an :class:`~iriai_build_v2.execution_control.prompt_context_adapter.AuthoritativePromptContextBundle`
      (the upstream typed surface).
    * Test fakes / production implementations may implement this
      Protocol directly without wrapping
      :class:`AuthoritativePromptContextBundle` (e.g. when the gate
      scope's typed sources are already available without going
      through the upstream bundle).
    """

    def derive_companion(
        self,
        authoritative_bundle: AuthoritativePromptContextBundle,
        *,
        gate_scope_id: str,
        gate_input_digest: str,
        proof_rows: Sequence[AuthoritativeGateProofRow] = (),
    ) -> AuthoritativeGateCompanionRecord: ...


# --- Concrete adapter ------------------------------------------------------


class LegacyGateCompanionAdapter:
    """Concrete :class:`AuthoritativeGateCompanionPort` implementation
    that derives an :class:`AuthoritativeGateCompanionRecord` by
    calling :func:`derive_gate_companion` on the supplied
    :class:`~iriai_build_v2.execution_control.prompt_context_adapter.AuthoritativePromptContextBundle`.

    Per doc-13a:273-275 the adapter:

    1. Validates the gate-scope arguments (raises
       :class:`MissingGateCompanionFieldError` on empty/whitespace).
    2. Classifies the upstream bundle's
       ``completeness.state`` (raises
       :class:`MissingGateCompanionFieldError` on
       ``"preview_only"`` / ``"unavailable"``).
    3. Otherwise returns the typed
       :class:`AuthoritativeGateCompanionRecord` with
       ``approval_routing.should_approve_gate=True``.

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    adapter is **fail-closed**: the
    :class:`MissingGateCompanionFieldError` is the typed signal the
    gate consumer catches to route the typed failure id
    ``verifier_context/companion_record_unavailable`` and SKIP gate
    approval.

    Per the auto-memory ``feedback_no_refactor`` rule this adapter
    is a NEW opt-in code path; the legacy gate consumers continue to
    use the accepted Slice 06
    :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunner`
    unchanged.

    The adapter is intentionally LIGHTWEIGHT (no internal state) so
    it can be instantiated per-gate-call without surfaces that hold
    cross-call state. The future Slice 13A sub-slice that wires this
    adapter into the verifier / checkpoint consumer will pass the
    adapter as a constructor port (mirroring the fourth sub-slice's
    ``authoritative_prompt_builder: ... | None = None`` pattern).
    """

    def derive_companion(
        self,
        authoritative_bundle: AuthoritativePromptContextBundle,
        *,
        gate_scope_id: str,
        gate_input_digest: str,
        proof_rows: Sequence[AuthoritativeGateProofRow] = (),
    ) -> AuthoritativeGateCompanionRecord:
        """Derive an :class:`AuthoritativeGateCompanionRecord` from
        the supplied
        :class:`~iriai_build_v2.execution_control.prompt_context_adapter.AuthoritativePromptContextBundle`.

        Delegates to :func:`derive_gate_companion`. See that helper's
        docstring for the full fail-closed + approval contracts.
        """

        return derive_gate_companion(
            authoritative_bundle,
            gate_scope_id=gate_scope_id,
            gate_input_digest=gate_input_digest,
            proof_rows=proof_rows,
        )
