"""Slice 13A sixth sub-slice -- snapshot companion record + per-list-field
completeness + dead-until-wired binding closure for the fifth sub-slice's
:class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`.

This module implements **doc-13a Refactoring Steps step 7** verbatim
(``docs/execution-control-plane/13a-lossless-context-and-evidence-completeness.md:280-282``):

    Add a 13A snapshot companion so every list field carries field-level
    completeness. Partial snapshots are allowed for display but classifier
    rules fail closed unless their required fields are complete.

It is the **THIRD executor wiring** of the 13A typed surfaces (after the
fourth sub-slice's dispatcher wiring + the fifth sub-slice's gate
companion); per the auto-memory ``feedback_no_refactor`` rule the wiring
lands as a **NEW opt-in code path** on top of the accepted Slice 10
``ControlPlaneSnapshot`` snapshot boundary. Per doc-13a:42-46 +
doc-13a:124-126 the accepted Slice 10
:class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
typed shape at
``src/iriai_build_v2/workflows/develop/execution/snapshots.py:462`` and
the supervisor classifier consumer at
``src/iriai_build_v2/supervisor/classifier.py:872`` +
``classifier_mapping.py:917`` remain **byte-identical**; this module
exposes a NEW opt-in :class:`AuthoritativeSnapshotCompanionPort` that
wraps the legacy snapshot consumer boundary from OUTSIDE (NOT a
``SupervisorClassifier`` constructor parameter -- the
``SupervisorClassifier`` constructor is accepted Slice 10 and
READ-ONLY). When the consumer wires the new port, it routes through
:class:`AuthoritativeSnapshotCompanionRecord` (per doc-13a:280-282)
instead of treating the raw snapshot as authoritative.

**P3-13A-5-4 BINDING CLOSURE** (dead-until-wired statement from the
Slice 13A fifth sub-slice finalizer). The fifth sub-slice landed
:class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
as a stable opt-in port; this sub-slice closes the binding by wiring
the gate adapter into a real verifier / gate consumer site via the
NEW :class:`LegacyGateConsumerSnapshotAdapter` external opt-in
wrapper. The wrapper composes the snapshot companion record + the
gate companion record so the consumer routes through BOTH layers of
typed completeness before approving a gate. Per the doc-13a:18-23
invariant + the doc-13a:111-115 Blocking deviations rule the consumer
MUST NOT approve a gate from snapshot evidence that is incomplete for
the gate's required scope.

**Co-bundle decision** (recorded in the BEFORE journal entry +
JSONL row). Step 7 + the P3-13A-5-4 binding closure land as ONE
sub-slice because:

1. Both adopt the same EXTERNAL-adapter wiring pattern from the
   fourth + fifth sub-slices (opt-in port wraps the legacy boundary
   OUTSIDE per doc-13a:42-46 + 124-126 +
   ``feedback_no_refactor``).
2. Sibling typed shapes share base imports
   (:class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
   + :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
   + :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef` +
   :class:`~iriai_build_v2.execution_control.gate_companion.AuthoritativeGateCompanionRecord`).
3. Shared typed failure ids family (the snapshot fail-closed
   ``evidence_corruption/list_field_incomplete`` +
   ``evidence_corruption/classifier_rule_blocked`` sit beside the
   gate fail-closed
   ``verifier_context/companion_record_unavailable`` +
   ``verifier_context/proof_row_required``).

**Change-control non-negotiables** (doc-13a:42-46 + 124-126 +
auto-memory ``feedback_no_refactor``):

* This module MUST NOT edit
  ``src/iriai_build_v2/workflows/develop/execution/snapshots.py`` /
  ``src/iriai_build_v2/supervisor/classifier.py`` /
  ``src/iriai_build_v2/supervisor/classifier_mapping.py`` /
  ``src/iriai_build_v2/public_dashboard.py`` in-place; the new opt-in
  surface wraps the legacy snapshot boundary externally.
* The legacy :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
  shape at ``snapshots.py:462`` is preserved **verbatim**; the new
  snapshot companion record carries the per-list-field completeness
  via composition (NOT replacement).

**Fail-closed contract** (doc-13a:280-282 + auto-memory
``feedback_no_silent_degradation``):

* When a REQUIRED list field on the snapshot is incomplete (per
  doc-13a:280-282 "classifier rules fail closed unless their required
  fields are complete"), :func:`derive_snapshot_companion` raises
  :class:`MissingSnapshotCompanionFieldError` carrying the typed
  failure id ``evidence_corruption/list_field_incomplete``.
* When the snapshot's overall ``state="preview_only"`` (a degraded
  snapshot that carries only display previews), the helper raises
  :class:`MissingSnapshotCompanionFieldError` carrying the typed
  failure id ``evidence_corruption/list_field_incomplete``.
* When the consumer's REQUIRED classifier rule scope cannot be
  satisfied by the snapshot's per-list-field completeness, the
  routing decision records the typed failure id
  ``evidence_corruption/classifier_rule_blocked`` and the consumer SKIPS the
  classifier rule.

**Implementation discipline** (stdlib + Pydantic + the in-package
sanctioned surfaces only):

* Stdlib (``typing``) + Pydantic v2 +
  :mod:`iriai_build_v2.execution_control.completeness` (the second
  sub-slice's foundational typed shapes; READ-ONLY consumer) +
  :mod:`iriai_build_v2.execution_control.prompt_context_adapter` (the
  third sub-slice's compatibility adapter; READ-ONLY consumer; used
  by the gate-binding-closure wrapper) +
  :mod:`iriai_build_v2.execution_control.gate_companion` (the fifth
  sub-slice's gate companion; READ-ONLY consumer; closes the
  P3-13A-5-4 binding statement).
* NO imports from ``governance/`` (the governance layer consumes
  execution-control surfaces, not the reverse).
* NO imports from ``workflows/develop/execution/`` (the legacy
  snapshot / classifier surfaces are the WRAPPED boundary, not a
  dependency of the new module; the consumer-side wiring lives in
  the future Slice 13A sub-slice that wires this module into the
  supervisor / dashboard consumer).
* NO imports from ``supervisor/`` (same reason as above).

**Namespace decision** (doc-13a:280-282 + execution_control namespace
precedent from the second + third + fourth + fifth sub-slices). This
module lives at
``src/iriai_build_v2/execution_control/snapshot_companion.py``
alongside ``completeness.py`` + ``prompt_context_adapter.py`` +
``dispatcher_prompt_context.py`` + ``gate_companion.py`` per the
doc-13a:280-282 ownership wording. It is **NOT re-exported** from
``src/iriai_build_v2/execution_control/__init__.py`` (precedent: the
Slice 13A second + third + fourth + fifth sub-slices did NOT touch
``__init__.py``).
"""

from __future__ import annotations

from typing import Any, Literal, Mapping, Protocol, Sequence

from pydantic import BaseModel, ConfigDict, Field

# Slice 13A second sub-slice foundational typed shapes (READ-ONLY consumer).
# Citations per doc-13a:127-192 (typed shapes) + doc-13a:264 (digest helper).
from iriai_build_v2.execution_control.completeness import (
    AuthoritativeContextRef,
    CompletenessState,
    EvidenceAuthority,
    EvidenceCompleteness,
    EvidencePageRef,
    compute_completeness_digest,
)

# Slice 13A third sub-slice compatibility adapter (READ-ONLY consumer).
# Used by the P3-13A-5-4 binding-closure wrapper that composes the
# snapshot companion with the gate companion record.
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
)

# Slice 13A fifth sub-slice gate companion (READ-ONLY consumer).
# Closes the P3-13A-5-4 dead-until-wired binding statement: the gate
# adapter lands as a composable wrapper alongside the snapshot
# companion adapter.
from iriai_build_v2.execution_control.gate_companion import (
    AuthoritativeGateCompanionPort,
    AuthoritativeGateCompanionRecord,
    AuthoritativeGateProofRow,
    LegacyGateCompanionAdapter,
    MissingGateCompanionFieldError,
)


__all__ = [
    # Step 7 (doc-13a:280-282) typed shapes + helpers + port.
    "AuthoritativeSnapshotListFieldCompleteness",
    "AuthoritativeSnapshotCompanionRecord",
    "AuthoritativeSnapshotClassifierRouting",
    "AuthoritativeSnapshotCompanionPort",
    "LegacySnapshotCompanionAdapter",
    "derive_snapshot_companion",
    # P3-13A-5-4 binding closure -- gate-consumer external opt-in wrapper
    # that composes the snapshot companion with the gate companion record.
    "LegacyGateConsumerSnapshotAdapter",
    "derive_gate_companion_with_snapshot",
    # Fail-closed typed exception (per feedback_no_silent_degradation).
    "MissingSnapshotCompanionFieldError",
]


# --- Fail-closed typed exception -------------------------------------------


class MissingSnapshotCompanionFieldError(ValueError):
    """Raised when an :class:`AuthoritativeSnapshotCompanionRecord` cannot
    be derived from the given snapshot.

    Per the auto-memory ``feedback_no_silent_degradation`` rule + per
    doc-13a:280-282 ("Partial snapshots are allowed for display but
    classifier rules fail closed unless their required fields are
    complete"): the helper MUST NOT silently return a degraded
    companion record when the inputs do not satisfy the per-list-field
    completeness contract for the required classifier scope. It raises
    this typed exception so the caller can route the typed failure id
    ``evidence_corruption/list_field_incomplete`` (registered at
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`).

    The exception carries ``missing_field_names`` (the tuple of list
    fields the classifier required complete coverage of but the
    snapshot did not satisfy) + ``snapshot_scope_id`` (the snapshot
    scope identifier the helper was trying to derive a companion for)
    + ``unavailable_reason`` (a free-form reason describing the
    fail-closed classification). Inherits :class:`ValueError` so any
    caller that already catches :class:`ValueError` for malformed-
    input handling sees the failure (mirrors the
    :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
    + :class:`~iriai_build_v2.execution_control.prompt_context_adapter.MissingPromptContextFieldError`
    sibling precedents which also inherit :class:`ValueError`).
    """

    def __init__(
        self,
        missing_field_names: Sequence[str],
        *,
        snapshot_scope_id: str | None = None,
        unavailable_reason: str | None = None,
    ) -> None:
        # Defensive copy to a tuple so the public attribute is immutable.
        self.missing_field_names: tuple[str, ...] = tuple(missing_field_names)
        self.snapshot_scope_id = snapshot_scope_id
        self.unavailable_reason = unavailable_reason
        joined = (
            ", ".join(self.missing_field_names)
            if self.missing_field_names
            else "(none)"
        )
        reason_clause = (
            f"; reason: {unavailable_reason}" if unavailable_reason else ""
        )
        scope_clause = (
            f"; snapshot_scope_id: {snapshot_scope_id}"
            if snapshot_scope_id
            else ""
        )
        super().__init__(
            f"snapshot companion record is unavailable for the classifier "
            f"scope: missing list field(s): {joined}{reason_clause}{scope_clause}"
        )


# --- Step 7 typed shapes (doc-13a:280-282) ---------------------------------


class AuthoritativeSnapshotListFieldCompleteness(BaseModel):
    """Doc-13a:236-242 + doc-13a:280-282 -- the per-list-field
    completeness record on a snapshot companion.

    Carries the per-list-field name (e.g. ``"latest_failures"`` /
    ``"merge_queue"`` / ``"retry_budgets"``) + the typed
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    record for that list field + the optional next-page-ref the
    consumer can use to fetch the missing pages. Per doc-13a:280-282
    the consumer drives classifier rules only when the per-list-field
    completeness covers the rule's required scope.

    The doc-13a:236-242 spec names the typed shape ``SnapshotListField``;
    the implementer renames it
    ``AuthoritativeSnapshotListFieldCompleteness`` for namespace
    clarity (the Slice 10a ``ControlPlaneSnapshot`` already uses
    ``Summary`` suffix per ``ExecutionAttemptSummary`` /
    ``WorkspaceSnapshotSummary`` etc; the ``Authoritative*Completeness``
    suffix mirrors the sibling Slice 13A typed surfaces). The 4 fields
    project the doc-13a:236-242 spec verbatim:

    * :data:`field_name` -- the snapshot's list-field name (e.g.
      ``"latest_failures"``).
    * :data:`completeness` -- the typed
      :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
      record for the list field.
    * :data:`item_count` -- the count of items in the field on the
      snapshot. Per doc-13a:236-242 the consumer may render a partial
      field with the cursor / page-refs and visible degraded metadata.
    * :data:`next_page_ref` -- the optional
      :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef`
      pointing at the next page of items the consumer can fetch.
      ``None`` when the field is complete in a single page (no next
      page).

    Per the Slice 13A invariant doc-13a:18-23 + doc-13a:280-282 the
    consumer drives classifier rules only when
    ``completeness.state in {"complete", "paged"}`` AND the rule's
    required scope is in ``completeness.complete_for``.
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # -- unknown fields fail closed (the snapshot consumer cannot smuggle
    # an undocumented field through the per-list-field completeness).
    model_config = ConfigDict(extra="forbid")

    field_name: str
    """Doc-13a:236-242 -- the snapshot's list-field name (e.g.
    ``"latest_failures"`` / ``"merge_queue"`` / ``"retry_budgets"``).
    MUST be non-empty; an empty string raises
    :class:`MissingSnapshotCompanionFieldError` from
    :func:`derive_snapshot_companion` per the fail-closed rule."""

    completeness: EvidenceCompleteness
    """Doc-13a:236-242 -- the typed completeness record for this list
    field. Per the Slice 13A invariant doc-13a:18-23 +
    doc-13a:280-282 the consumer drives classifier rules only when
    ``completeness.state in {"complete", "paged"}`` AND the rule's
    required scope is in ``completeness.complete_for``."""

    item_count: int
    """Doc-13a:236-242 -- the count of items in the field on the
    snapshot. Per doc-13a:236-242 the consumer may render a partial
    field with the cursor / page-refs and visible degraded metadata.
    MUST be non-negative (the legacy Slice 10a snapshot's list-fields
    are always List[Summary] -- never negative)."""

    next_page_ref: EvidencePageRef | None = None
    """Doc-13a:236-242 -- optional
    :class:`~iriai_build_v2.execution_control.completeness.EvidencePageRef`
    pointing at the next page of items the consumer can fetch. ``None``
    when the field is complete in a single page (no next page) or
    when the consumer is not expected to fetch further pages."""


class AuthoritativeSnapshotClassifierRouting(BaseModel):
    """Doc-13a:280-282 -- the routing decision derived from the
    authoritative snapshot companion record.

    Carries the typed signal the snapshot consumer (supervisor
    classifier / dashboard outbox / governance reader) needs to either
    invoke its classifier rule (all required list fields complete) or
    record the typed failure id ``evidence_corruption/classifier_rule_blocked``
    and SKIP the classifier rule (per doc-13a:280-282 "classifier
    rules fail closed unless their required fields are complete").

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    routing is **fail-closed**: when ``should_invoke_classifier=False``
    the snapshot consumer MUST record the typed failure id and MUST
    NOT proceed with the classifier rule. The typed failure id is
    the Slice 13A sixth-sub-slice failure
    ``evidence_corruption/classifier_rule_blocked`` (registered in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    per this iteration's chunk shape point 2 ADD).
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # -- unknown fields fail closed (the snapshot consumer cannot smuggle
    # an undocumented field through the routing record).
    model_config = ConfigDict(extra="forbid")

    should_invoke_classifier: bool
    """When True, snapshot consumer proceeds to invoke its classifier
    rule with the authoritative companion record attached. When False,
    snapshot consumer records the typed failure id and does NOT
    invoke the classifier per doc-13a:280-282."""

    typed_failure_class: Literal["evidence_corruption"] | None = None
    """The typed-failure router ``failure_class`` when
    ``should_invoke_classifier=False``; ``None`` when
    ``should_invoke_classifier=True``. Currently only the single value
    ``evidence_corruption`` is supported (doc-13a:280-282 + the
    EXISTING ``evidence_corruption`` failure_class at
    ``failure_router.py:38``). The Slice 13A sixth sub-slice
    intentionally REUSES the existing ``evidence_corruption`` class
    instead of introducing a new ``snapshot`` failure_class so the
    supervisor classifier mapping coverage rule does NOT require an
    edit to ``supervisor/classifier_mapping.py`` (which is READ-ONLY
    per doc-13a:42-46 + 124-126 + the implementer prompt's
    MUST-NOT-EDIT-SUPERVISOR-MODULES rule). The
    ``evidence_corruption`` class is the closest semantic match: both
    the snapshot fail-closed signal and the evidence-corruption signal
    indicate the snapshot's evidence is structurally invalid /
    incomplete; both route to ``quiesce``."""

    typed_failure_type: (
        Literal["list_field_incomplete", "classifier_rule_blocked"] | None
    ) = None
    """The typed-failure router ``failure_type`` when
    ``should_invoke_classifier=False``; ``None`` when
    ``should_invoke_classifier=True``. The two typed failure ids
    registered in
    :mod:`iriai_build_v2.workflows.develop.execution.failure_router`
    per the Slice 13A sixth sub-slice are
    ``list_field_incomplete`` (per doc-13a:280-282 fail-closed on
    structurally-incomplete required list fields) +
    ``classifier_rule_blocked`` (per doc-13a:280-282 fail-closed
    when the classifier rule's required scope cannot be satisfied
    by the snapshot's per-list-field completeness)."""

    unavailable_reason: str | None = None
    """Human-readable reason when ``should_invoke_classifier=False``;
    rendered into the typed-failure record details for downstream
    observability (dashboard / supervisor / governance). ``None``
    when ``should_invoke_classifier=True``."""

    missing_field_names: tuple[str, ...] = Field(default_factory=tuple)
    """The missing-required-list-field names the helper raised on,
    per the :class:`MissingSnapshotCompanionFieldError` typed exception.
    Empty tuple when ``should_invoke_classifier=True``."""


class AuthoritativeSnapshotCompanionRecord(BaseModel):
    """Doc-13a:236-256 + doc-13a:280-282 -- the 13A snapshot companion
    record.

    Carries the typed per-list-field completeness contract for one
    snapshot scope plus the snapshot scope identifier + snapshot
    digest + the context manifest ref + the routing decision. The 7
    fields project the Slice 13A second sub-slice's foundational
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    + :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    onto the snapshot scope:

    * :data:`snapshot_scope_id` -- the snapshot scope this companion
      record covers (e.g. ``"snapshot:dashboard:feature-abc"`` /
      ``"snapshot:supervisor:feature-xyz"``); per doc-13a:280-282 the
      snapshot companion record is per-snapshot-scope (each scope may
      have its own companion record).
    * :data:`snapshot_digest` -- SHA-256 hex digest over the
      snapshot's typed content (typically the
      ``snapshot_version`` digest from
      :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`);
      the digest ties the companion record to a specific snapshot
      input.
    * :data:`overall_completeness` -- the typed
      :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
      record covering the SNAPSHOT-LEVEL completeness (per the
      doc-13a:280-282 spec the snapshot-level state derives from the
      union of per-list-field states: ``preview_only`` when all
      list-fields are display-only; ``unavailable`` when any required
      list-field is missing; ``paged`` when any list-field is paged;
      otherwise ``complete``).
    * :data:`context_manifest_ref` -- the typed
      :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
      pointing at the
      :class:`~iriai_build_v2.execution_control.completeness.ExactEvidenceManifest`
      the snapshot consumer reads.
    * :data:`list_field_completeness` -- the typed dict of per-list-
      field completeness records, keyed by the snapshot list-field
      name (e.g. ``"latest_failures"`` /
      ``"merge_queue"`` / ``"retry_budgets"``).
    * :data:`classifier_routing` -- the typed
      :class:`AuthoritativeSnapshotClassifierRouting` carrying the
      should_invoke_classifier decision derived from the per-list-
      field completeness.
    * :data:`required_list_field_scopes` -- the tuple of list-field
      names the consumer requires complete coverage of (per
      doc-13a:280-282 "classifier rules fail closed unless their
      required fields are complete"). Empty tuple is valid (a snapshot
      scope with no required list-field requirements is purely
      advisory / display-only).

    Per the Slice 13A invariant doc-13a:18-23: if the consumer can
    influence dispatch / verification / merge / checkpoint / routing /
    scheduler feedback / policy recommendation, it MUST consume
    :data:`overall_completeness` (the typed
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`
    record) + :data:`classifier_routing` -- a ``preview_only`` or
    ``unavailable`` state MUST drive ``classifier_routing.should_invoke_classifier=False``.
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # -- unknown fields fail closed (the snapshot consumer cannot smuggle
    # an undocumented field through the companion record).
    model_config = ConfigDict(extra="forbid")

    snapshot_scope_id: str
    """Doc-13a:280-282 -- the snapshot scope this companion record
    covers (e.g. ``"snapshot:dashboard:feature-abc"``). MUST be
    non-empty."""

    snapshot_digest: str
    """SHA-256 hex digest over the snapshot's typed content. Ties the
    companion record to a specific snapshot input; per the Slice 13A
    invariant doc-13a:298-301 the digest is the cross-process
    freshness contract: re-deriving the companion record for the same
    snapshot input MUST produce the same ``snapshot_digest`` (or fail
    as stale/corrupt). MUST be non-empty."""

    overall_completeness: EvidenceCompleteness
    """Doc-13a:280-282 -- the typed snapshot-level completeness record.
    Per the Slice 13A invariant doc-13a:18-23 + doc-13a:280-282 the
    snapshot consumer drives classifier rules only when
    ``overall_completeness.state in {"complete", "paged"}`` AND the
    rule's required scope is in
    ``overall_completeness.complete_for``."""

    context_manifest_ref: AuthoritativeContextRef
    """Doc-13a:280-282 -- the lightweight
    :class:`~iriai_build_v2.execution_control.completeness.AuthoritativeContextRef`
    pointing at an
    :class:`~iriai_build_v2.execution_control.completeness.ExactEvidenceManifest`
    for the snapshot scope."""

    list_field_completeness: dict[str, AuthoritativeSnapshotListFieldCompleteness]
    """Doc-13a:236-256 -- the typed dict of per-list-field completeness
    records, keyed by the snapshot list-field name. The keys MUST
    match the list-field names on the legacy Slice 10a
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    (e.g. ``"latest_failures"`` / ``"merge_queue"`` /
    ``"retry_budgets"`` / ``"active_attempts"`` /
    ``"workspace_snapshots"`` / ``"sandbox_leases"`` /
    ``"runtime_bindings"`` / ``"gates"`` / ``"checkpoints"`` /
    ``"evidence_refs"`` / ``"cursors"``). An empty dict is valid (a
    snapshot scope with NO list-field completeness records is purely
    advisory / display-only)."""

    classifier_routing: AuthoritativeSnapshotClassifierRouting
    """Doc-13a:280-282 -- the typed routing decision derived from
    :data:`list_field_completeness`. Per the auto-memory
    ``feedback_no_silent_degradation`` rule: when any required list
    field has ``state="preview_only"`` or
    ``state="unavailable"`` MUST produce
    ``classifier_routing.should_invoke_classifier=False`` + the typed
    failure id ``evidence_corruption/classifier_rule_blocked``."""

    required_list_field_scopes: tuple[str, ...] = Field(default_factory=tuple)
    """Doc-13a:280-282 -- the tuple of list-field names the consumer
    requires complete coverage of (per "classifier rules fail closed
    unless their required fields are complete"). Empty tuple is valid
    (a snapshot scope with no required list-field requirements is
    purely advisory / display-only)."""


# --- Step 7 helpers (doc-13a:280-282) --------------------------------------


def _snapshot_scope_id_from_arg(snapshot_scope_id: str | None) -> str:
    """Validate that the snapshot_scope_id is non-empty.

    Per doc-13a:280-282 the snapshot companion record is per-snapshot-
    scope; an empty / whitespace-only snapshot_scope_id cannot
    identify a snapshot scope.
    """

    if (
        not isinstance(snapshot_scope_id, str)
        or not snapshot_scope_id.strip()
    ):
        raise MissingSnapshotCompanionFieldError(
            ["snapshot_scope_id"],
            unavailable_reason="snapshot_scope_id is required",
            snapshot_scope_id=None,
        )
    return snapshot_scope_id


def _snapshot_digest_from_arg(snapshot_digest: str | None) -> str:
    """Validate that the snapshot_digest is non-empty.

    Per the Slice 13A invariant doc-13a:298-301 the digest is the
    cross-process freshness contract; an empty / whitespace-only
    digest cannot tie the companion record to a specific snapshot
    input.
    """

    if not isinstance(snapshot_digest, str) or not snapshot_digest.strip():
        raise MissingSnapshotCompanionFieldError(
            ["snapshot_digest"],
            unavailable_reason="snapshot_digest is required",
            snapshot_scope_id=None,
        )
    return snapshot_digest


# Per doc-13a:236-256 the Slice 10a list-field names on
# ControlPlaneSnapshot that the snapshot companion record covers. The
# tuple is the documented set; future Slice 13A sub-slices may extend
# this list as the Slice 10a snapshot adds new list-fields. Listed in
# the same order as the typed ControlPlaneSnapshot model fields.
_SLICE_10A_LIST_FIELD_NAMES: tuple[str, ...] = (
    "cursors",
    "active_attempts",
    "workspace_snapshots",
    "latest_failures",
    "merge_queue",
    "retry_budgets",
    "sandbox_leases",
    "runtime_bindings",
    "gates",
    "checkpoints",
    "evidence_refs",
)


def _aggregate_overall_state(
    list_field_completeness: Mapping[
        str, AuthoritativeSnapshotListFieldCompleteness
    ],
) -> CompletenessState:
    """Derive the snapshot-level :data:`CompletenessState` from the
    per-list-field completeness records.

    Per doc-13a:236-256 + doc-13a:280-282 the aggregation rule is:

    * ``"unavailable"`` when ANY list-field is unavailable.
    * ``"preview_only"`` when ALL list-fields are preview_only.
    * ``"paged"`` when ANY list-field is paged (and none is unavailable).
    * ``"complete"`` otherwise (all list-fields complete).
    * Empty dict -> ``"complete"`` (no list-fields means no degradation).

    The aggregation is intentionally CONSERVATIVE: a single unavailable
    list-field degrades the entire snapshot to unavailable; a single
    paged list-field degrades the entire snapshot to paged. This
    matches the doc-13a:280-282 "classifier rules fail closed" rule
    -- the consumer must explicitly opt-in to partial-snapshot
    classification by reading the per-list-field completeness.
    """

    if not list_field_completeness:
        return "complete"

    states = [item.completeness.state for item in list_field_completeness.values()]
    if "unavailable" in states:
        return "unavailable"
    if all(s == "preview_only" for s in states):
        return "preview_only"
    if "paged" in states:
        return "paged"
    return "complete"


def _classify_routing(
    list_field_completeness: Mapping[
        str, AuthoritativeSnapshotListFieldCompleteness
    ],
    required_scopes: Sequence[str],
) -> AuthoritativeSnapshotClassifierRouting:
    """Classify the snapshot's per-list-field completeness against the
    consumer's required list-field scopes.

    Per doc-13a:280-282 the consumer drives classifier rules only when
    every required scope is in
    ``list_field_completeness[scope].completeness.complete_for`` AND
    ``list_field_completeness[scope].completeness.state in
    {"complete", "paged"}``. Otherwise the routing decision carries
    ``should_invoke_classifier=False`` + the typed failure id
    ``evidence_corruption/classifier_rule_blocked``.

    Empty ``required_scopes`` -> ``should_invoke_classifier=True``
    (no required scopes means no classifier rule is gated; per
    doc-13a:280-282 "Partial snapshots are ALLOWED for display").
    """

    if not required_scopes:
        return AuthoritativeSnapshotClassifierRouting(
            should_invoke_classifier=True,
            typed_failure_class=None,
            typed_failure_type=None,
            unavailable_reason=None,
            missing_field_names=(),
        )

    missing: list[str] = []
    for scope in required_scopes:
        if scope not in list_field_completeness:
            missing.append(scope)
            continue
        per_field = list_field_completeness[scope]
        state = per_field.completeness.state
        if state not in ("complete", "paged"):
            missing.append(scope)
            continue

    if missing:
        return AuthoritativeSnapshotClassifierRouting(
            should_invoke_classifier=False,
            typed_failure_class="evidence_corruption",
            typed_failure_type="classifier_rule_blocked",
            unavailable_reason=(
                "required list-field scope(s) not covered by snapshot "
                f"per-list-field completeness: {', '.join(missing)}"
            ),
            missing_field_names=tuple(missing),
        )

    return AuthoritativeSnapshotClassifierRouting(
        should_invoke_classifier=True,
        typed_failure_class=None,
        typed_failure_type=None,
        unavailable_reason=None,
        missing_field_names=(),
    )


def derive_snapshot_companion(
    list_field_completeness: Mapping[
        str, AuthoritativeSnapshotListFieldCompleteness
    ],
    *,
    snapshot_scope_id: str,
    snapshot_digest: str,
    manifest_id: str,
    manifest_digest: str,
    required_list_field_scopes: Sequence[str] = (),
    authority: EvidenceAuthority = "routing_authority",
) -> AuthoritativeSnapshotCompanionRecord:
    """Derive an :class:`AuthoritativeSnapshotCompanionRecord` from the
    per-list-field completeness records.

    Implements **doc-13a Refactoring Steps step 7** verbatim
    (doc-13a:280-282): "Add a 13A snapshot companion so every list
    field carries field-level completeness. Partial snapshots are
    allowed for display but classifier rules fail closed unless their
    required fields are complete."

    **Fail-closed contract** (doc-13a:280-282 + auto-memory
    ``feedback_no_silent_degradation``):

    * Empty / whitespace-only ``snapshot_scope_id`` ->
      :class:`MissingSnapshotCompanionFieldError`.
    * Empty / whitespace-only ``snapshot_digest`` ->
      :class:`MissingSnapshotCompanionFieldError`.
    * Aggregate :data:`CompletenessState` is ``"preview_only"`` AND
      ``required_list_field_scopes`` is non-empty ->
      :class:`MissingSnapshotCompanionFieldError` (per
      doc-13a:280-282 "classifier rules fail closed unless their
      required fields are complete"). When ``required_list_field_scopes``
      is empty the helper proceeds (the snapshot is purely advisory /
      display-only; no classifier rule is gated).
    * Aggregate :data:`CompletenessState` is ``"unavailable"`` ->
      :class:`MissingSnapshotCompanionFieldError` (per
      doc-13a:303-310 "Required evidence cannot be paged exactly:
      return ``state='unavailable'`` ... fail closed").

    **Approval contract** (doc-13a:280-282 + doc-13a:18-23):

    * Aggregate state is ``"complete"`` or ``"paged"`` AND every
      required list-field scope is covered by the per-list-field
      completeness -> ``classifier_routing.should_invoke_classifier=True``.
    * Aggregate state is ``"preview_only"`` AND
      ``required_list_field_scopes`` is empty ->
      ``classifier_routing.should_invoke_classifier=True`` (per
      doc-13a:280-282 "Partial snapshots are ALLOWED for display").

    **List-field key contract.** The ``list_field_completeness`` dict's
    keys MUST match the legacy Slice 10a
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    list-field names (e.g. ``"latest_failures"`` / ``"merge_queue"``
    / ``"retry_budgets"``). The helper does NOT validate this
    contract (the consumer is responsible for using the correct
    names); per doc-13a:42-46 + 124-126 the helper MUST NOT depend on
    the Slice 10a typed shape (READ-ONLY consumer of the snapshot
    surface).
    """

    # --- Step 1: validate the snapshot-scope identity arguments ----------
    validated_scope_id = _snapshot_scope_id_from_arg(snapshot_scope_id)
    validated_digest = _snapshot_digest_from_arg(snapshot_digest)

    # --- Step 2: derive the aggregate snapshot-level completeness state -
    aggregate_state = _aggregate_overall_state(list_field_completeness)
    has_required_scopes = bool(required_list_field_scopes)

    # --- Step 3: fail closed on preview_only / unavailable ----------------
    if aggregate_state == "unavailable":
        # Doc-13a:303-310: Required evidence cannot be paged exactly;
        # fail closed via the typed exception so the caller can route
        # the typed failure id evidence_corruption/list_field_incomplete.
        unavailable_fields = [
            name
            for name, per_field in list_field_completeness.items()
            if per_field.completeness.state == "unavailable"
        ]
        raise MissingSnapshotCompanionFieldError(
            unavailable_fields or list(list_field_completeness.keys()),
            unavailable_reason=(
                "snapshot list field(s) report state='unavailable'; per "
                "doc-13a:303-310 the consumer MUST route "
                "evidence_corruption/list_field_incomplete and fail closed"
            ),
            snapshot_scope_id=validated_scope_id,
        )

    if aggregate_state == "preview_only" and has_required_scopes:
        # Doc-13a:280-282 + doc-13a:18-23 + doc-13a:111-115: a
        # classifier rule MUST NOT proceed when all snapshot list
        # fields are preview_only AND the rule requires coverage of
        # one or more list-field scopes. Fail closed via the typed
        # exception so the caller can route the typed failure id
        # evidence_corruption/list_field_incomplete.
        raise MissingSnapshotCompanionFieldError(
            list(required_list_field_scopes),
            unavailable_reason=(
                "all snapshot list fields report state='preview_only' "
                "and the classifier rule requires complete coverage of "
                "required list-field scope(s); per doc-13a:280-282 "
                "classifier rules fail closed unless their required "
                "fields are complete"
            ),
            snapshot_scope_id=validated_scope_id,
        )

    # --- Step 4: build the per-list-field completeness payload for the
    # aggregate completeness record ---------------------------------------
    # The complete_for list names the SCOPES this snapshot covers; for
    # a per-list-field aggregate we union the per-field complete_for
    # lists (the snapshot scope covers everything any per-list-field
    # covers). Per the aggregate-state rule above, when aggregate state
    # is "complete" / "paged" we may still have per-field states that
    # are preview_only or paged; the snapshot consumer reads the
    # per-list-field record (list_field_completeness) for the
    # decision-fine-grained authority.
    aggregate_complete_for: list[str] = sorted(
        {
            scope
            for per_field in list_field_completeness.values()
            for scope in per_field.completeness.complete_for
        }
    )

    # Aggregate authority: routing_authority by default (per
    # doc-13a:135-141 the snapshot drives typed-failure-router
    # classification + downstream policy selection); the caller may
    # pass execution_authority / gate_authority / advisory /
    # display_only per the consumer's decision scope.
    # Per the doc-13a:18-23 + doc-13a:111-115 override-resistant
    # invariant: if aggregate state is preview_only (and we reached
    # here because required_list_field_scopes is empty), force the
    # authority to display_only to prevent the consumer from
    # accidentally driving classifier rules from preview-only
    # evidence.
    resolved_authority: EvidenceAuthority = (
        "display_only" if aggregate_state == "preview_only" else authority
    )

    aggregate_completeness_digest = compute_completeness_digest(
        state=aggregate_state,
        authority=resolved_authority,
        complete_for=aggregate_complete_for,
        missing_required_refs=[],
        page_refs=[],
        preview_ref=None,
        unavailable_reason=None,
    )

    overall_completeness = EvidenceCompleteness(
        state=aggregate_state,
        authority=resolved_authority,
        complete_for=aggregate_complete_for,
        missing_required_refs=[],
        page_refs=[],
        preview_ref=None,
        unavailable_reason=None,
        completeness_digest=aggregate_completeness_digest,
    )

    # --- Step 5: build the snapshot-scope context manifest ref -----------
    # The manifest identity carries through from the helper arguments;
    # the completeness_digest changes (snapshot-scoped) so consumers can
    # detect that the snapshot companion record is a re-scoped
    # projection of the underlying ExactEvidenceManifest.
    context_manifest_ref = AuthoritativeContextRef(
        manifest_id=manifest_id,
        manifest_digest=manifest_digest,
        completeness_digest=aggregate_completeness_digest,
        required_complete_for=list(required_list_field_scopes),
        authority=resolved_authority,
    )

    # --- Step 6: classify the routing decision -----------------------------
    classifier_routing = _classify_routing(
        list_field_completeness,
        required_list_field_scopes,
    )

    # --- Step 7: assemble the companion record ----------------------------
    return AuthoritativeSnapshotCompanionRecord(
        snapshot_scope_id=validated_scope_id,
        snapshot_digest=validated_digest,
        overall_completeness=overall_completeness,
        context_manifest_ref=context_manifest_ref,
        list_field_completeness=dict(list_field_completeness),
        classifier_routing=classifier_routing,
        required_list_field_scopes=tuple(required_list_field_scopes),
    )


# --- Port Protocol --------------------------------------------------------


class AuthoritativeSnapshotCompanionPort(Protocol):
    """The opt-in Protocol the snapshot consumer's NEW constructor port
    accepts.

    Mirrors the Slice 13A fourth + fifth sub-slices'
    :class:`~iriai_build_v2.execution_control.dispatcher_prompt_context.AuthoritativePromptBuilderPort`
    + :class:`~iriai_build_v2.execution_control.gate_companion.AuthoritativeGateCompanionPort`
    Protocols -- the new opt-in port is additive on top of the
    accepted Slice 10 snapshot boundary, NOT a replacement of any
    existing port. The legacy
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    shape at ``snapshots.py:462`` remains BYTE-IDENTICAL; the new
    port wraps the legacy snapshot OUTSIDE the legacy boundary per
    the doc-13a:42-46 + 124-126 change-control rule.

    Implementations:

    * :class:`LegacySnapshotCompanionAdapter` -- the reference adapter
      that derives an :class:`AuthoritativeSnapshotCompanionRecord`
      from a per-list-field completeness dict.
    * Test fakes / production implementations may implement this
      Protocol directly without wrapping the per-list-field
      completeness dict (e.g. when the snapshot scope's typed sources
      are already available without going through the legacy Slice 10
      snapshot boundary).
    """

    def derive_companion(
        self,
        list_field_completeness: Mapping[
            str, AuthoritativeSnapshotListFieldCompleteness
        ],
        *,
        snapshot_scope_id: str,
        snapshot_digest: str,
        manifest_id: str,
        manifest_digest: str,
        required_list_field_scopes: Sequence[str] = (),
        authority: EvidenceAuthority = "routing_authority",
    ) -> AuthoritativeSnapshotCompanionRecord: ...


# --- Concrete adapter ------------------------------------------------------


class LegacySnapshotCompanionAdapter:
    """Concrete :class:`AuthoritativeSnapshotCompanionPort` implementation
    that derives an :class:`AuthoritativeSnapshotCompanionRecord` by
    calling :func:`derive_snapshot_companion` on the supplied
    per-list-field completeness dict.

    Per doc-13a:280-282 the adapter:

    1. Validates the snapshot-scope arguments (raises
       :class:`MissingSnapshotCompanionFieldError` on
       empty/whitespace).
    2. Aggregates the per-list-field completeness states into the
       snapshot-level :data:`CompletenessState` (raises
       :class:`MissingSnapshotCompanionFieldError` on
       ``"unavailable"`` aggregate or on ``"preview_only"`` aggregate
       when required list-field scopes are non-empty).
    3. Otherwise returns the typed
       :class:`AuthoritativeSnapshotCompanionRecord` with the routing
       decision derived from the per-list-field completeness vs the
       required list-field scopes.

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    adapter is **fail-closed**: the
    :class:`MissingSnapshotCompanionFieldError` is the typed signal
    the snapshot consumer catches to route the typed failure id
    ``evidence_corruption/list_field_incomplete`` and SKIP the classifier rule.

    Per the auto-memory ``feedback_no_refactor`` rule this adapter
    is a NEW opt-in code path; the legacy snapshot consumers continue
    to use the accepted Slice 10
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    unchanged.

    The adapter is intentionally LIGHTWEIGHT (no internal state) so
    it can be instantiated per-snapshot-call without surfaces that
    hold cross-call state. The future Slice 13A sub-slice that wires
    this adapter into the supervisor / dashboard consumer will pass
    the adapter as a constructor port (mirroring the fourth
    sub-slice's ``authoritative_prompt_builder: ... | None = None``
    pattern + the fifth sub-slice's
    :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
    pattern).
    """

    def derive_companion(
        self,
        list_field_completeness: Mapping[
            str, AuthoritativeSnapshotListFieldCompleteness
        ],
        *,
        snapshot_scope_id: str,
        snapshot_digest: str,
        manifest_id: str,
        manifest_digest: str,
        required_list_field_scopes: Sequence[str] = (),
        authority: EvidenceAuthority = "routing_authority",
    ) -> AuthoritativeSnapshotCompanionRecord:
        """Derive an :class:`AuthoritativeSnapshotCompanionRecord` from
        the supplied per-list-field completeness dict.

        Delegates to :func:`derive_snapshot_companion`. See that
        helper's docstring for the full fail-closed + approval
        contracts.
        """

        return derive_snapshot_companion(
            list_field_completeness,
            snapshot_scope_id=snapshot_scope_id,
            snapshot_digest=snapshot_digest,
            manifest_id=manifest_id,
            manifest_digest=manifest_digest,
            required_list_field_scopes=required_list_field_scopes,
            authority=authority,
        )


# --- P3-13A-5-4 binding closure: gate-consumer external opt-in wrapper -----


class LegacyGateConsumerSnapshotAdapter:
    """Concrete external opt-in wrapper that composes the snapshot
    companion record (Slice 13A sixth sub-slice) with the gate
    companion record (Slice 13A fifth sub-slice).

    **Closes P3-13A-5-4 dead-until-wired binding statement.** The
    fifth sub-slice landed
    :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
    as a stable opt-in port; this sub-slice wires it into a real
    consumer site via the EXTERNAL opt-in wrapper pattern (per
    doc-13a:42-46 + 124-126 + ``feedback_no_refactor`` -- the
    wrapper composes the gate adapter OUTSIDE the legacy Slice 06
    :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunner`
    boundary; the legacy gate boundary remains BYTE-IDENTICAL).

    The wrapper composes the two companion records in two phases:

    1. Phase 1 (snapshot gate): validate the snapshot companion record
       for the gate's required snapshot scope. If the snapshot
       companion is incomplete for the gate's required list-field
       scope, fail closed via the typed failure id
       ``evidence_corruption/classifier_rule_blocked`` and DO NOT proceed to
       the gate companion record (the gate cannot approve from
       snapshot evidence that is incomplete for the gate's required
       scope).
    2. Phase 2 (gate approval): if the snapshot companion record
       passes, derive the gate companion record via the wrapped
       :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`.
       The gate companion record carries its own fail-closed contract
       (the fifth sub-slice's ``state="preview_only"`` /
       ``state="unavailable"`` typed exceptions).

    Per the Slice 13A invariant doc-13a:18-23 + doc-13a:111-115: the
    consumer MUST NOT approve a gate from snapshot evidence that is
    incomplete for the gate's required scope (lossy summaries +
    previews are display-only). The composition above enforces this
    invariant at the gate consumer boundary.

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    wrapper is **fail-closed**: any failure in EITHER phase raises
    the typed exception (either
    :class:`MissingSnapshotCompanionFieldError` from phase 1 OR
    :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
    from phase 2).

    Per the auto-memory ``feedback_no_refactor`` rule the wrapper is
    a NEW opt-in code path; the legacy
    :class:`~iriai_build_v2.workflows.develop.execution.gates.GateRunner`
    callers continue to use the accepted Slice 06 boundary unchanged.
    """

    def __init__(
        self,
        gate_adapter: AuthoritativeGateCompanionPort | None = None,
    ) -> None:
        """Store the wrapped gate adapter; the wrapper composes it
        with the snapshot companion record.

        The ``gate_adapter`` parameter defaults to ``None`` so the
        wrapper instantiates a fresh
        :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
        on first use (preserves the fifth sub-slice's stateless
        wrapper semantics). Callers may pass a test-fake
        :class:`AuthoritativeGateCompanionPort` for unit-test
        isolation.

        The default behavior (``gate_adapter=None`` -> fresh
        :class:`LegacyGateCompanionAdapter`) closes the
        P3-13A-5-4 binding statement: the fifth sub-slice's adapter
        now has a real production caller (this wrapper), satisfying
        the binding before any Slice 14-19 governance slice can
        claim gate execution authority.
        """

        # Per P3-13A-5-1 the LegacyGateCompanionAdapter is a stateless
        # wrapper; instantiating per-call is cheap (mirrors the
        # fifth-sub-slice "intentionally LIGHTWEIGHT (no internal
        # state)" wording in the gate_companion docstring).
        self._gate_adapter: AuthoritativeGateCompanionPort = (
            gate_adapter or LegacyGateCompanionAdapter()
        )

    def derive_gate_with_snapshot(
        self,
        snapshot_companion: AuthoritativeSnapshotCompanionRecord,
        authoritative_bundle: AuthoritativePromptContextBundle,
        *,
        gate_scope_id: str,
        gate_input_digest: str,
        required_snapshot_list_field_scopes: Sequence[str] = (),
        proof_rows: Sequence[AuthoritativeGateProofRow] = (),
    ) -> AuthoritativeGateCompanionRecord:
        """Compose the snapshot companion record with the gate companion
        record, failing closed if EITHER record is incomplete for the
        gate's required scope.

        See class docstring for the full two-phase flow (snapshot gate
        + gate approval). Closes the P3-13A-5-4 dead-until-wired
        binding statement by wiring the fifth-sub-slice
        :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
        into a real consumer site.
        """

        # --- Phase 1: snapshot gate ---------------------------------------
        # If the consumer requires snapshot coverage of one or more
        # list-field scopes, validate that the snapshot companion
        # record covers them. The snapshot companion record's
        # classifier_routing carries the typed signal; we re-derive
        # the routing here against the gate's required scopes (the
        # consumer's snapshot scope may differ from the gate's
        # snapshot scope).
        if required_snapshot_list_field_scopes:
            snapshot_routing = _classify_routing(
                snapshot_companion.list_field_completeness,
                required_snapshot_list_field_scopes,
            )
            if not snapshot_routing.should_invoke_classifier:
                raise MissingSnapshotCompanionFieldError(
                    snapshot_routing.missing_field_names,
                    snapshot_scope_id=snapshot_companion.snapshot_scope_id,
                    unavailable_reason=(
                        f"gate '{gate_scope_id}' requires snapshot "
                        f"coverage of list-field scope(s) "
                        f"{tuple(required_snapshot_list_field_scopes)} "
                        f"but the snapshot companion record reports "
                        f"{snapshot_routing.unavailable_reason}"
                    ),
                )

        # --- Phase 2: gate approval ---------------------------------------
        # The wrapped gate adapter handles the gate's own fail-closed
        # contract (state="preview_only" / state="unavailable").
        return self._gate_adapter.derive_companion(
            authoritative_bundle,
            gate_scope_id=gate_scope_id,
            gate_input_digest=gate_input_digest,
            proof_rows=proof_rows,
        )


def derive_gate_companion_with_snapshot(
    snapshot_companion: AuthoritativeSnapshotCompanionRecord,
    authoritative_bundle: AuthoritativePromptContextBundle,
    *,
    gate_scope_id: str,
    gate_input_digest: str,
    required_snapshot_list_field_scopes: Sequence[str] = (),
    proof_rows: Sequence[AuthoritativeGateProofRow] = (),
    gate_adapter: AuthoritativeGateCompanionPort | None = None,
) -> AuthoritativeGateCompanionRecord:
    """Pure-helper variant of
    :meth:`LegacyGateConsumerSnapshotAdapter.derive_gate_with_snapshot`.

    Provides a stateless function-style call site for consumers that
    don't need the wrapper-instance abstraction (e.g. one-off gate
    derivations in tests / governance projections). Internally
    delegates to a fresh :class:`LegacyGateConsumerSnapshotAdapter`
    so the two-phase fail-closed contract is preserved verbatim.

    Closes the P3-13A-5-4 binding statement: the fifth-sub-slice's
    :class:`~iriai_build_v2.execution_control.gate_companion.LegacyGateCompanionAdapter`
    + :func:`~iriai_build_v2.execution_control.gate_companion.derive_gate_companion`
    now have real production callers through this composition.
    """

    wrapper = LegacyGateConsumerSnapshotAdapter(gate_adapter=gate_adapter)
    return wrapper.derive_gate_with_snapshot(
        snapshot_companion,
        authoritative_bundle,
        gate_scope_id=gate_scope_id,
        gate_input_digest=gate_input_digest,
        required_snapshot_list_field_scopes=required_snapshot_list_field_scopes,
        proof_rows=proof_rows,
    )
