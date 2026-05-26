"""Slice 13A eighth sub-slice (13An-2) -- P3-13A-6-3 binding closure wiring.

This module CLOSES the P3-13A-6-3 dead-until-wired binding statement carried
through the 6th sub-slice finalizer (per the reframed P2-V-1 finding) and the
7th sub-slice deferral + the 8th sub-slice 13An-1 SPLIT decision. The
binding statement at
``docs/execution-control-plane/13a-acceptance.md:222-227`` says:

    The wiring target is likely either the supervisor classifier consumer
    site (`src/iriai_build_v2/supervisor/classifier.py`) OR the dashboard
    snapshot consumer site (`src/iriai_build_v2/public_dashboard.py`).
    Per doc-13a:42-46 + 124-126 + ``feedback_no_refactor``, the wiring
    **must land as a NEW external opt-in code path** (not an in-place
    edit of either accepted Slice 10 module).

**Wiring target chosen this iteration:**
:mod:`iriai_build_v2.public_dashboard` (the dashboard snapshot consumer
site). The supervisor classifier site is deferred to a future Slice 13A
sub-slice or the Slice 17 policy interface per
``13a-acceptance.md:222-227``.

Rationale (recorded in the BEFORE journal entry per the user-prompt
non-negotiable):

1. **Discrete clean call site**:
   :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.project_control_plane_snapshot_changed`
   (``public_dashboard.py:243-308``) takes a
   :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
   and emits a bounded display event -- exactly the snapshot whose
   per-list-field completeness our 6th-sub-slice
   :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacySnapshotCompanionAdapter`
   projects. A wrapper around this method is materially cleaner than
   the 2699-line
   :class:`~iriai_build_v2.supervisor.classifier.SupervisorClassifier.classify_typed`
   consumer.

2. **Smaller surface for byte-identical verification**.
   ``public_dashboard.py`` is 786 lines vs ``supervisor/classifier.py``
   2699 lines. The gate-5 byte-identical Slice 10 baseline proof is
   materially easier to verify.

3. **Existing env-flag opt-in pattern**. ``public_dashboard.py``
   already uses env-flag opt-in toggles
   (:data:`~iriai_build_v2.public_dashboard.PUBLIC_DASHBOARD_CONSUMER_ENV` +
   :func:`~iriai_build_v2.public_dashboard._flag_enabled` at line 46).
   The new wrapper follows the same pattern with a NEW env flag
   :data:`DASHBOARD_COMPANION_WIRING_ENV`, keeping the opt-in
   discipline uniform.

4. **Per-list-field name alignment**. The snapshot's list-field names
   on :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
   (``latest_failures`` / ``merge_queue`` / ``retry_budgets`` /
   ``active_attempts`` / ``workspace_snapshots`` / ``sandbox_leases``
   / ``runtime_bindings`` / ``gates`` / ``checkpoints`` /
   ``evidence_refs`` / ``cursors``) match VERBATIM the
   ``_SLICE_10A_LIST_FIELD_NAMES`` tuple in
   ``snapshot_companion.py:568-580``. The dashboard's ``counters`` dict
   already projects these list fields to counts; the companion-record
   derivation is the natural per-field completeness extension.

5. **``feedback_no_refactor`` discipline**. The wrapper lives in this
   NEW module under ``execution_control/`` -- it does NOT in-place-edit
   ``public_dashboard.py``. The accepted Slice 10 byte-identical
   baseline is preserved (gate 5 proof).

**Change-control non-negotiables** (doc-13a:42-46 + 124-126 +
auto-memory ``feedback_no_refactor``):

* This module MUST NOT edit ``src/iriai_build_v2/public_dashboard.py``
  / ``src/iriai_build_v2/supervisor/classifier.py`` /
  ``src/iriai_build_v2/workflows/develop/execution/snapshots.py`` /
  the 5 Slice 13A modules in-place; the new opt-in surface wraps the
  legacy ``PublicDashboardOutbox`` boundary externally.
* The legacy
  :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
  + :func:`~iriai_build_v2.public_dashboard.control_plane_snapshot_changed_payload`
  + :func:`~iriai_build_v2.public_dashboard.control_plane_snapshot_event_id`
  shapes are preserved **verbatim**; the new wrapper carries the
  legacy outbox via composition (NOT replacement).

**Fail-closed contract** (doc-13a:18-23 + 111-115 + 280-282 +
auto-memory ``feedback_no_silent_degradation``):

* When the env flag :data:`DASHBOARD_COMPANION_WIRING_ENV` is OFF
  (the default), the wrapper delegates byte-identical to the legacy
  :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`.
* When the env flag is ON and the snapshot companion record's
  required list-field scopes are incomplete, the wrapper raises a
  :class:`MissingSnapshotCompanionFieldError` AFTER recording the
  typed failure id ``evidence_corruption/list_field_incomplete``
  (per doc-13a:280-282 "classifier rules fail closed unless their
  required fields are complete") via the
  :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`
  port.
* When the env flag is ON and the per-list-field completeness rule
  ``should_invoke_classifier`` is ``False``, the wrapper records the
  typed failure id ``evidence_corruption/classifier_rule_blocked``
  and raises (per the doc-13a:280-282 "classifier rule blocked"
  fail-closed rule).
* When the caller supplies a gate scope and the gate companion record
  is incomplete (state="preview_only" / state="unavailable"), the
  wrapper records
  ``verifier_context/companion_record_unavailable`` and raises (per
  doc-13a:273-275 "A gate may not approve from preview_only evidence
  after 13A is enabled").
* When the caller's typed proof rows are structurally incomplete
  (missing source_digest / page_refs / proof_algorithm /
  verification_time), the wrapper records
  ``verifier_context/proof_row_required`` and raises (per
  doc-13a:276-278).

The 4 typed failure ids registered by the 5th + 6th sub-slices
(``verifier_context/companion_record_unavailable`` +
``verifier_context/proof_row_required`` +
``evidence_corruption/list_field_incomplete`` +
``evidence_corruption/classifier_rule_blocked``) ALL route to
``quiesce`` per doc-13a:307-310. NO new failure ids are added in
this sub-slice (the 4 pre-existing typed failure ids cover ALL
fail-closed cases for the composite chain).

**Implementation discipline** (stdlib + Pydantic + the in-package
sanctioned surfaces only):

* Stdlib (``os`` + ``typing``) + Pydantic v2 +
  :mod:`iriai_build_v2.execution_control.completeness` (the second
  sub-slice's foundational typed shapes; READ-ONLY consumer) +
  :mod:`iriai_build_v2.execution_control.snapshot_companion` (the
  sixth sub-slice's snapshot companion record + composite adapter
  chain; READ-ONLY consumer) +
  :mod:`iriai_build_v2.execution_control.gate_companion` (the fifth
  sub-slice's gate companion + typed exceptions; READ-ONLY
  consumer) +
  :mod:`iriai_build_v2.execution_control.prompt_context_adapter` (the
  third sub-slice's compatibility adapter; READ-ONLY consumer) +
  :mod:`iriai_build_v2.public_dashboard` (the accepted Slice 10
  dashboard module; READ-ONLY consumer -- the wrapper composes the
  legacy outbox, does NOT in-place-edit it).
* NO imports from ``governance/`` (the governance layer consumes
  execution-control surfaces, not the reverse).
* NO imports from ``workflows/develop/execution/`` beyond the
  ``failure_router`` typed surfaces (the legacy snapshot / classifier
  / dispatcher surfaces are wrapped by the upstream Slice 13A
  adapters, NOT by this wrapper directly).
* NO imports from ``supervisor/`` (not the wiring target).

**Namespace decision.** This module lives at
``src/iriai_build_v2/execution_control/dashboard_wrapper.py``
alongside the 5 prior Slice 13A modules
(``completeness.py`` + ``prompt_context_adapter.py`` +
``dispatcher_prompt_context.py`` + ``gate_companion.py`` +
``snapshot_companion.py``) per the
``execution_control/`` namespace precedent established by the
2nd-6th sub-slices. It is **NOT re-exported** from
``src/iriai_build_v2/execution_control/__init__.py`` (precedent: the
Slice 13A 2nd-6th sub-slices did NOT touch ``__init__.py``).
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Protocol, Sequence

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
# Used by the composite chain when the caller supplies a gate scope.
from iriai_build_v2.execution_control.prompt_context_adapter import (
    AuthoritativePromptContextBundle,
)

# Slice 13A fifth sub-slice gate companion (READ-ONLY consumer).
# The composite chain invokes the gate companion record when the caller
# supplies a gate scope.
from iriai_build_v2.execution_control.gate_companion import (
    AuthoritativeGateCompanionRecord,
    AuthoritativeGateProofRow,
    LegacyGateCompanionAdapter,
    MissingGateCompanionFieldError,
    MissingProofRowFieldError,
)

# Slice 13A sixth sub-slice snapshot companion (READ-ONLY consumer).
# The composite chain invokes the snapshot companion record + the
# composite gate-consumer wrapper per doc-13a:280-282.
from iriai_build_v2.execution_control.snapshot_companion import (
    AuthoritativeSnapshotCompanionRecord,
    AuthoritativeSnapshotListFieldCompleteness,
    LegacyGateConsumerSnapshotAdapter,
    LegacySnapshotCompanionAdapter,
    MissingSnapshotCompanionFieldError,
    derive_snapshot_companion,
)

# Accepted Slice 10 dashboard module (READ-ONLY consumer).
# Per `feedback_no_refactor` the wrapper composes the legacy outbox; it
# does NOT in-place-edit `public_dashboard.py`.
from iriai_build_v2.public_dashboard import (
    PublicDashboardOutbox,
    control_plane_snapshot_changed_payload,
    control_plane_snapshot_event_id,
)


__all__ = [
    # Env flag name + default (the opt-in toggle).
    "DASHBOARD_COMPANION_WIRING_ENV",
    "dashboard_companion_wiring_enabled",
    # Composite chain typed-failure-id taxonomy (per doc-13a:18-23 +
    # 111-115 + 280-282).
    "DashboardCompanionFailureClass",
    "DashboardCompanionFailureType",
    "DashboardCompanionFailureRecord",
    # Composite-chain failure-recording port (the opt-in Protocol the
    # wrapper accepts).
    "DashboardCompanionFailurePort",
    "InMemoryDashboardCompanionFailurePort",
    # The composite-chain external opt-in wrapper.
    "CompletenessAwareDashboardOutbox",
    "derive_snapshot_list_field_completeness_from_snapshot",
]


# --- Env flag + opt-in toggle (mirrors public_dashboard.py:46-52) ----------


DASHBOARD_COMPANION_WIRING_ENV = "IRIAI_EXEC_CONTROL_DASHBOARD_COMPANION_WIRING_ENABLED"
"""Env flag name that enables the P3-13A-6-3 binding closure wiring.

Per the auto-memory ``feedback_no_refactor`` rule + per the
``public_dashboard.py:46-52`` opt-in env-flag precedent: the wiring is
OFF by default. When unset (or any of the off-truthy values ``0`` /
``false`` / ``no`` / ``off``), the wrapper delegates byte-identical to
the legacy :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`.

When set to a truthy value (``1`` / ``true`` / ``yes`` / ``on`` --
anything not in the off-truthy set), the wrapper invokes the composite
:class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
chain BEFORE delegating to the legacy outbox. A composite-chain failure
records the typed failure id via the
:class:`DashboardCompanionFailurePort` and raises a typed exception so
the caller's projection transaction aborts (mirrors the legacy
``project_control_plane_snapshot_changed`` fail-closed enqueue rule at
``public_dashboard.py:263-271``).
"""


def dashboard_companion_wiring_enabled() -> bool:
    """Return True iff the wrapper is opt-in enabled via the env flag.

    Default OFF (byte-identical legacy path when not set). Mirrors the
    :func:`~iriai_build_v2.public_dashboard._flag_enabled` discipline:
    any of the off-truthy values (``0`` / ``false`` / ``no`` / ``off``)
    disables the wiring; anything else enables it.

    Per the auto-memory ``feedback_no_refactor`` rule: the default OFF
    guarantees the legacy path is byte-identical to the Slice 10
    ACCEPTED baseline when the wrapper is imported but not enabled.
    """

    return os.environ.get(DASHBOARD_COMPANION_WIRING_ENV, "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


# --- Composite-chain typed-failure taxonomy (doc-13a:18-23 + 111-115 +
# 280-282) ------------------------------------------------------------------


# Per the BEFORE journal entry: NO new failure ids are added in this
# sub-slice. The 4 pre-existing typed failure ids cover ALL fail-closed
# cases for the composite chain:
#
#   * ``evidence_corruption/list_field_incomplete`` (6th sub-slice;
#     doc-13a:280-282 fail-closed on structurally-incomplete required
#     list fields).
#   * ``evidence_corruption/classifier_rule_blocked`` (6th sub-slice;
#     doc-13a:280-282 fail-closed when the classifier rule's required
#     scope cannot be satisfied by the snapshot's per-list-field
#     completeness).
#   * ``verifier_context/companion_record_unavailable`` (5th sub-slice;
#     doc-13a:273-275 fail-closed on ``state="preview_only"`` gate
#     companion record).
#   * ``verifier_context/proof_row_required`` (5th sub-slice;
#     doc-13a:276-278 fail-closed when a typed proof row is required
#     but the 4 mandatory fields are missing / empty).
#
# All 4 route to ``quiesce`` per the
# ``failure_router._ROUTE_ROWS`` at
# ``src/iriai_build_v2/workflows/develop/execution/failure_router.py:1376-1434``.

DashboardCompanionFailureClass = (
    "verifier_context",
    "evidence_corruption",
)
"""The 2 EXISTING failure classes the dashboard companion wrapper's
composite chain may emit. Mirrors the 5th + 6th sub-slice typed failure
class assignments + the doc-13a:273-275 + doc-13a:280-282 fail-closed
contracts.

* ``verifier_context`` (per doc-13a:273-275 + doc-13a:276-278): used
  for gate companion record fail-closed + proof row fail-closed.
* ``evidence_corruption`` (per doc-13a:280-282 + the 6th-sub-slice
  P3-13A-6-1 compromise): used for snapshot companion record
  fail-closed cases (list field incomplete + classifier rule blocked).

NO new failure_class is added this sub-slice -- a new failure_class
would have triggered the supervisor classifier mapping coverage gate
(READ-ONLY per doc-13a:42-46 + 124-126).
"""


DashboardCompanionFailureType = (
    "list_field_incomplete",
    "classifier_rule_blocked",
    "companion_record_unavailable",
    "proof_row_required",
)
"""The 4 EXISTING failure types the dashboard companion wrapper's
composite chain may emit. All 4 are pre-existing typed failure ids on
:mod:`iriai_build_v2.workflows.develop.execution.failure_router`
(``FailureType`` Literal + ``FAILURE_TYPES`` tuple +
``_DETERMINISTIC_FAILURE_TYPES`` set + ``_ROUTE_ROWS`` rows routing
to ``quiesce``). NO new failure type is added this sub-slice.

Per the user-prompt non-negotiable: the 4 typed failure ids ALL route
to ``quiesce`` per doc-13a:307-310 ("Required evidence cannot be paged
exactly: return ``state='unavailable'`` ... route ``runtime_context/
context_incomplete`` or ``verifier_context/context_incomplete``").
"""


class DashboardCompanionFailureRecord(BaseModel):
    """Doc-13a:18-23 + 280-282 + 307-310 -- the typed-failure record the
    dashboard companion wrapper produces on a composite-chain fail-closed.

    Carries the typed (failure_class, failure_type) tuple + the snapshot
    scope identifier + the snapshot digest + the missing field names +
    an optional gate scope identifier + an optional reason. The record
    is the typed signal the
    :class:`DashboardCompanionFailurePort` consumes to route the
    fail-closed via the underlying typed-failure-router port.

    Per the auto-memory ``feedback_no_silent_degradation`` rule the
    record carries EVERY field the caller needs to route a typed-
    failure-router observation without going back to the snapshot;
    the wrapper records this BEFORE raising the typed exception so
    the typed observation is durable even when the caller's
    projection transaction aborts.
    """

    # ``extra="forbid"`` aligns with the sibling Slice 13A typed shapes
    # -- unknown fields fail closed (a malformed failure record cannot
    # smuggle an undocumented field through the typed signal).
    model_config = ConfigDict(extra="forbid")

    failure_class: str
    """The pre-existing failure_class the composite chain routes to.
    Per the user-prompt non-negotiable: MUST be one of the 2 entries
    in :data:`DashboardCompanionFailureClass` (``verifier_context`` /
    ``evidence_corruption``). NO new failure_class is added this
    sub-slice."""

    failure_type: str
    """The pre-existing failure_type the composite chain routes to.
    Per the user-prompt non-negotiable: MUST be one of the 4 entries
    in :data:`DashboardCompanionFailureType`. NO new failure_type is
    added this sub-slice."""

    snapshot_scope_id: str
    """The snapshot scope identifier the composite chain failed on.
    Mirrors :data:`AuthoritativeSnapshotCompanionRecord.snapshot_scope_id`
    so the consumer can join the typed-failure record with the
    snapshot companion record."""

    snapshot_digest: str
    """The snapshot digest the composite chain failed on. Per the
    Slice 13A invariant doc-13a:298-301 the digest is the cross-process
    freshness contract: re-deriving the wrapper fails consistently for
    the same snapshot input (or fails as stale/corrupt)."""

    missing_field_names: tuple[str, ...] = Field(default_factory=tuple)
    """The missing-required-field names the composite chain failed on.
    Empty tuple is valid (e.g. a ``state="preview_only"`` aggregate that
    fails closed without naming individual missing fields)."""

    gate_scope_id: str | None = None
    """Optional gate scope identifier (when the caller supplied a gate
    scope to the composite). ``None`` when the failure is snapshot-only
    (i.e. doc-13a:280-282 list-field-completeness fail-closed)."""

    unavailable_reason: str | None = None
    """Optional human-readable reason for the composite-chain failure
    (rendered into dashboard / debug surfaces for cross-process
    observability)."""


# --- Composite-chain failure-recording port (the opt-in Protocol) ----------


class DashboardCompanionFailurePort(Protocol):
    """The opt-in Protocol the dashboard companion wrapper accepts.

    Per the
    :class:`~iriai_build_v2.execution_control.snapshot_companion.AuthoritativeSnapshotCompanionPort`
    + :class:`~iriai_build_v2.execution_control.gate_companion.AuthoritativeGateCompanionPort`
    Protocol precedents: the wrapper accepts an opt-in port for the
    typed-failure recording instead of importing the
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`
    directly (the failure_router lives in a different package; the
    Protocol is the structural-typing seam).

    Implementations:

    * :class:`InMemoryDashboardCompanionFailurePort` -- the reference
      in-memory implementation (test seam + default).
    * Test fakes / production implementations may implement this
      Protocol directly (e.g. wrapping a real
      :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`
      via a thin adapter that constructs a
      :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureObservation`
      from the
      :class:`DashboardCompanionFailureRecord`).
    """

    def record(self, failure_record: DashboardCompanionFailureRecord) -> None: ...


class InMemoryDashboardCompanionFailurePort:
    """Concrete :class:`DashboardCompanionFailurePort` reference
    implementation that records failures into an in-memory list.

    The reference implementation is the default the wrapper uses when
    the caller does not supply a port; it preserves the typed failure
    records so the caller can inspect them after a fail-closed
    (e.g. for cross-process observability + debug rendering). The
    in-memory list is intentionally NOT thread-safe -- the wrapper is
    invoked from a single-threaded ``asyncio`` coroutine context so a
    lock would be redundant.

    Production callers MAY supply a port that wraps a real
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`
    via a thin adapter (the adapter constructs a
    :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureObservation`
    from the :class:`DashboardCompanionFailureRecord` and calls
    :meth:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter.record`).
    """

    def __init__(self) -> None:
        self.records: list[DashboardCompanionFailureRecord] = []

    def record(self, failure_record: DashboardCompanionFailureRecord) -> None:
        """Append the failure record to the in-memory list.

        Per the auto-memory ``feedback_no_silent_degradation`` rule the
        port records the typed observation BEFORE the wrapper raises
        the typed exception so the observation is durable even when
        the caller's projection transaction aborts.
        """

        self.records.append(failure_record)


# --- Snapshot-to-per-list-field-completeness derivation helper -------------


# Per the BEFORE journal entry + the doc-13a:280-282 + snapshot_companion.py:
# 568-580 _SLICE_10A_LIST_FIELD_NAMES contract: the helper derives a
# per-list-field completeness dict from the snapshot's list-fields. The
# wrapper uses this dict to call derive_snapshot_companion (per
# snapshot_companion.py:683 helper) which returns the typed
# AuthoritativeSnapshotCompanionRecord.

# The Slice 10a ControlPlaneSnapshot's list-field names + the
# completeness state mapping. Listed in the same order as the typed
# ControlPlaneSnapshot model fields at snapshots.py:483-493.
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


def _snapshot_list_field_count(snapshot: Any, field_name: str) -> int:
    """Read the per-list-field count off a typed snapshot (Pydantic model
    or dict).

    Mirrors :func:`~iriai_build_v2.public_dashboard._snapshot_as_dict`
    discipline: accept either a typed
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    Pydantic model OR a dict (the dashboard module already serialises
    via ``model_dump(mode="json")``).

    Returns 0 when the field is absent or not a list/tuple. The 0 count
    is consistent with the dashboard's
    :func:`~iriai_build_v2.public_dashboard.control_plane_snapshot_changed_payload`
    ``_count`` helper at ``public_dashboard.py:599-601``.
    """

    if isinstance(snapshot, dict):
        value = snapshot.get(field_name)
    else:
        value = getattr(snapshot, field_name, None)
    return len(value) if isinstance(value, (list, tuple)) else 0


def derive_snapshot_list_field_completeness_from_snapshot(
    snapshot: Any,
    *,
    truncated: bool | None = None,
    omitted_counts: Mapping[str, int] | None = None,
    required_list_field_scopes: Sequence[str] = (),
    page_ref_factory: (
        Any  # callable: (field_name, item_count) -> EvidencePageRef | None
    ) = None,
) -> dict[str, AuthoritativeSnapshotListFieldCompleteness]:
    """Derive the per-list-field completeness dict from a typed snapshot.

    Per doc-13a:236-256 + doc-13a:280-282 the helper projects each of
    the 11 Slice 10a list fields on
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    to a typed
    :class:`~iriai_build_v2.execution_control.snapshot_companion.AuthoritativeSnapshotListFieldCompleteness`
    record carrying the per-field
    :class:`~iriai_build_v2.execution_control.completeness.EvidenceCompleteness`.

    The per-field completeness state is derived from the snapshot's
    truncation + omitted_counts metadata (per
    :class:`~iriai_build_v2.workflows.develop.execution.snapshots.ControlPlaneSnapshot`
    ``truncated`` + ``omitted_counts`` fields at ``snapshots.py:474-478``):

    * Field absent or count 0 + scope NOT in
      ``required_list_field_scopes`` -> ``state="complete"`` (the
      empty list is fully present in a single page).
    * Field present + ``omitted_counts[field_name] > 0`` (some items
      truncated) -> ``state="paged"`` (the field is bounded; the
      consumer can re-fetch via the cursor).
    * Field present + no truncation -> ``state="complete"``.

    The helper does NOT introspect the snapshot's per-item content
    (the bounded snapshot per doc-10 spec carries summary-only rows;
    body hydration is out of Slice 13A scope per doc-13a:48-54).

    ``page_ref_factory`` (optional): callable that returns an optional
    next-page :class:`EvidencePageRef` for the field. When ``None`` (the
    default), no next-page refs are emitted (the dashboard wrapper does
    not need next-page refs; supervisor / governance consumers may
    supply this in future Slice 13A sub-slices).

    Per the auto-memory ``feedback_no_silent_degradation`` rule: when
    ``required_list_field_scopes`` is non-empty AND the helper cannot
    satisfy the required scopes, the downstream
    :func:`~iriai_build_v2.execution_control.snapshot_companion.derive_snapshot_companion`
    raises
    :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
    -- this helper itself does NOT raise (it just returns the
    derived dict; the fail-closed lives at the derive_snapshot_companion
    helper).
    """

    # Resolve truncation + omitted_counts from the snapshot when not
    # explicitly supplied by the caller (mirrors the dashboard module's
    # _snapshot_as_dict discipline).
    if isinstance(snapshot, dict):
        snapshot_data: Mapping[str, Any] = snapshot
    else:
        dump = getattr(snapshot, "model_dump", None)
        snapshot_data = dump(mode="json") if callable(dump) else {}

    resolved_truncated = (
        bool(snapshot_data.get("truncated"))
        if truncated is None
        else bool(truncated)
    )
    resolved_omitted = (
        snapshot_data.get("omitted_counts") or {}
        if omitted_counts is None
        else dict(omitted_counts)
    )
    if not isinstance(resolved_omitted, dict):
        resolved_omitted = {}

    out: dict[str, AuthoritativeSnapshotListFieldCompleteness] = {}
    for field_name in _SLICE_10A_LIST_FIELD_NAMES:
        item_count = _snapshot_list_field_count(snapshot, field_name)
        is_required_scope = field_name in tuple(required_list_field_scopes)
        omitted_for_field = resolved_omitted.get(field_name)
        omitted_int = (
            int(omitted_for_field)
            if isinstance(omitted_for_field, (int, float)) and omitted_for_field
            else 0
        )

        # Field-level state derivation:
        #   * omitted > 0 -> "paged" (some items truncated; consumer
        #     can re-fetch via the cursor).
        #   * truncated=True + required scope + omitted=0 -> "paged"
        #     (snapshot-wide truncation flag, conservative degrade).
        #   * else -> "complete".
        if omitted_int > 0:
            field_state = "paged"
        elif resolved_truncated and is_required_scope:
            field_state = "paged"
        else:
            field_state = "complete"

        next_page_ref: EvidencePageRef | None = None
        if page_ref_factory is not None and field_state == "paged":
            maybe_ref = page_ref_factory(field_name, item_count)
            if isinstance(maybe_ref, EvidencePageRef):
                next_page_ref = maybe_ref

        # Build a per-field completeness record. The complete_for is the
        # single-element list naming this field's scope; the consumer's
        # required_list_field_scopes tests against this list per the
        # snapshot_companion._classify_routing rule.
        completeness_digest = compute_completeness_digest(
            state=field_state,  # type: ignore[arg-type]
            authority="routing_authority",
            complete_for=[field_name],
            missing_required_refs=[],
            page_refs=[next_page_ref] if next_page_ref is not None else [],
            preview_ref=None,
            unavailable_reason=None,
        )
        per_field_completeness = EvidenceCompleteness(
            state=field_state,  # type: ignore[arg-type]
            authority="routing_authority",
            complete_for=[field_name],
            missing_required_refs=[],
            page_refs=[next_page_ref] if next_page_ref is not None else [],
            preview_ref=None,
            unavailable_reason=None,
            completeness_digest=completeness_digest,
        )

        out[field_name] = AuthoritativeSnapshotListFieldCompleteness(
            field_name=field_name,
            completeness=per_field_completeness,
            item_count=item_count,
            next_page_ref=next_page_ref,
        )

    return out


# --- The P3-13A-6-3 binding closure: external opt-in wrapper ---------------


class CompletenessAwareDashboardOutbox:
    """The P3-13A-6-3 binding closure -- external opt-in wrapper around
    :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
    that composes the 6th-sub-slice
    :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
    composite chain BEFORE delegating to the legacy outbox.

    **Wires the composite chain into a real production caller.** This
    closes the P3-13A-6-3 dead-until-wired binding statement (per
    ``13a-acceptance.md:193-227`` + the
    6th-sub-slice finalizer reframing of P2-V-1):

    > The wiring closes:
    >
    > * The **doc-13a:18-23 + 111-115** invariant that gates may NOT
    >   approve from `preview_only` evidence.
    > * The **doc-13a:280-282** invariant that classifier rules MUST
    >   fail closed when their required snapshot fields are
    >   incomplete.

    The wrapper composes two adapters:

    1. The 6th-sub-slice
       :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacySnapshotCompanionAdapter`
       -- derives the per-list-field completeness from the snapshot.
    2. The 6th-sub-slice
       :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
       -- composes the snapshot companion record with the gate
       companion record (per doc-13a:18-23 + 111-115 + 273-275 +
       276-278 + 280-282).

    **Opt-in env flag.** Per the auto-memory ``feedback_no_refactor``
    rule + the ``public_dashboard.PUBLIC_DASHBOARD_CONSUMER_ENV`` opt-in
    precedent: the wrapper defaults OFF
    (:data:`DASHBOARD_COMPANION_WIRING_ENV` unset or off-truthy). When
    OFF, the wrapper delegates byte-identical to the legacy
    :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
    method. When ON, the wrapper invokes the composite chain BEFORE
    delegating to the legacy outbox.

    **Fail-closed contract** (doc-13a:18-23 + 111-115 + 280-282 +
    auto-memory ``feedback_no_silent_degradation``): when the composite
    chain raises
    :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
    OR
    :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
    OR
    :class:`~iriai_build_v2.execution_control.gate_companion.MissingProofRowFieldError`,
    the wrapper:

    1. Records the typed
       :class:`DashboardCompanionFailureRecord` via the
       :class:`DashboardCompanionFailurePort` (the typed-failure
       observation is durable BEFORE the wrapper raises).
    2. Re-raises the typed exception (the caller's projection
       transaction aborts -- mirrors the legacy
       :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.project_control_plane_snapshot_changed`
       fail-closed enqueue rule at ``public_dashboard.py:263-271``).
    3. DOES NOT delegate to the legacy outbox (the bounded display
       event is NEVER enqueued when the composite chain fails).

    **Use case.** The wrapper is the FIRST real production caller of
    the composite chain. It satisfies the P3-13A-6-3 binding statement:
    a future Slice 14-19 governance slice can now claim gate execution
    authority because the composite chain has a real production caller
    (this wrapper). The supervisor classifier site is deferred to a
    future sub-slice or the Slice 17 policy interface per
    ``13a-acceptance.md:222-227``.

    **NO change to the legacy outbox.** Per the auto-memory
    ``feedback_no_refactor`` rule the wrapper composes the legacy
    :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox` via
    instance composition (NOT inheritance / monkey-patch / in-place
    edit). The legacy outbox's
    :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.project_control_plane_snapshot_changed`
    + every other legacy method is preserved verbatim; the wrapper
    delegates to them via ``self._outbox.<method>(...)`` calls.

    Constructor signature:

    * ``outbox`` -- the wrapped legacy
      :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
      instance. The wrapper composes it; it does NOT replace it.
    * ``failure_port`` -- the
      :class:`DashboardCompanionFailurePort` used for typed-failure
      recording. Defaults to a fresh
      :class:`InMemoryDashboardCompanionFailurePort`. Production
      callers SHOULD supply a port that wraps a real
      :class:`~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`.
    * ``snapshot_adapter`` -- the
      :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacySnapshotCompanionAdapter`
      (or a Protocol-compatible implementation). Defaults to a fresh
      :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacySnapshotCompanionAdapter`.
    * ``gate_consumer_adapter`` -- the
      :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
      composite. Defaults to a fresh
      :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`.
    * ``wiring_enabled`` -- optional explicit override for the env-flag
      opt-in. When None (the default), the wrapper reads the env flag
      :data:`DASHBOARD_COMPANION_WIRING_ENV` via
      :func:`dashboard_companion_wiring_enabled`. When explicitly
      True / False, the wrapper uses that value (the test seam for
      asserting the wiring ON path explicitly).
    """

    def __init__(
        self,
        outbox: PublicDashboardOutbox,
        *,
        failure_port: DashboardCompanionFailurePort | None = None,
        snapshot_adapter: LegacySnapshotCompanionAdapter | None = None,
        gate_consumer_adapter: LegacyGateConsumerSnapshotAdapter | None = None,
        wiring_enabled: bool | None = None,
    ) -> None:
        # Per the auto-memory `feedback_no_refactor` rule the wrapper
        # composes the legacy outbox via instance composition -- NOT
        # inheritance, NOT monkey-patch, NOT in-place edit. The legacy
        # outbox is preserved byte-identical.
        self._outbox = outbox

        # Per P3-13A-6-2 the LegacySnapshotCompanionAdapter is a
        # stateless wrapper; per P3-13A-5-1 the LegacyGateCompanionAdapter
        # is stateless too. Instantiating per-wrapper is cheap.
        self._snapshot_adapter = snapshot_adapter or LegacySnapshotCompanionAdapter()
        self._gate_consumer_adapter = (
            gate_consumer_adapter or LegacyGateConsumerSnapshotAdapter()
        )

        # Default to an in-memory port so the wrapper is usable in tests
        # without external dependencies. Production callers should
        # supply a port that wraps a real
        # `~iriai_build_v2.workflows.develop.execution.failure_router.FailureRouter`.
        self._failure_port: DashboardCompanionFailurePort = (
            failure_port or InMemoryDashboardCompanionFailurePort()
        )

        # Per the BEFORE journal entry the explicit override is the test
        # seam for asserting the wiring ON path; when None, the wrapper
        # reads the env flag (default OFF).
        self._wiring_enabled_override = wiring_enabled

    # --- Wrapper introspection helpers (test seams) ----------------------

    @property
    def wiring_enabled(self) -> bool:
        """True iff the wrapper's P3-13A-6-3 binding closure wiring is
        opt-in enabled (via the env flag OR the explicit constructor
        override).

        Per the auto-memory ``feedback_no_refactor`` rule: when False
        (the default), the wrapper delegates byte-identical to the
        legacy
        :class:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox`
        method.
        """

        if self._wiring_enabled_override is not None:
            return self._wiring_enabled_override
        return dashboard_companion_wiring_enabled()

    @property
    def failure_port(self) -> DashboardCompanionFailurePort:
        """Expose the failure port for test introspection of the
        typed-failure observations recorded by the composite chain.
        """

        return self._failure_port

    @property
    def outbox(self) -> PublicDashboardOutbox:
        """Expose the wrapped legacy outbox for test introspection /
        composition assertions.
        """

        return self._outbox

    @property
    def outbox_enabled(self) -> bool:
        """Delegate to the wrapped legacy outbox's ``outbox_enabled`` flag.

        Production callers of the dashboard projection driver
        (:func:`~iriai_build_v2.public_dashboard.project_control_plane_snapshot_if_changed`
        at ``public_dashboard.py:705``) check
        ``getattr(outbox, "outbox_enabled", False)`` as an early-return
        guard, and the production callsite at ``dashboard.py:1542``
        checks ``outbox.outbox_enabled`` directly. The
        :class:`CompletenessAwareDashboardOutbox` wrapper is used as a
        drop-in replacement for :class:`PublicDashboardOutbox` at the
        production callsite per the P2-13An-2-1 remediation; the wrapper
        MUST expose the same ``outbox_enabled`` flag so the
        early-return guard preserves byte-identical Slice 10 behaviour.

        Per the auto-memory ``feedback_no_refactor`` rule: this property
        is a PURE FORWARD-ADD; the wrapper composes the legacy outbox's
        ``outbox_enabled`` attribute via delegation (NOT in-place edit
        of either side).
        """

        return self._outbox.outbox_enabled

    # --- Composite-chain helper ------------------------------------------

    def _derive_snapshot_companion(
        self,
        snapshot: Any,
        *,
        feature_id: str,
        snapshot_version: str,
        required_list_field_scopes: Sequence[str] = (),
    ) -> AuthoritativeSnapshotCompanionRecord:
        """Derive the snapshot companion record from the typed snapshot.

        Per doc-13a:280-282 the helper:

        1. Derives the per-list-field completeness dict from the
           snapshot (per
           :func:`derive_snapshot_list_field_completeness_from_snapshot`).
        2. Invokes
           :meth:`~iriai_build_v2.execution_control.snapshot_companion.LegacySnapshotCompanionAdapter.derive_companion`
           with the per-list-field completeness dict + the snapshot
           scope identifiers.
        3. Returns the typed
           :class:`~iriai_build_v2.execution_control.snapshot_companion.AuthoritativeSnapshotCompanionRecord`.

        Raises
        :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
        on any of the fail-closed cases (per
        :func:`~iriai_build_v2.execution_control.snapshot_companion.derive_snapshot_companion`
        docstring).
        """

        list_field_completeness = (
            derive_snapshot_list_field_completeness_from_snapshot(
                snapshot,
                required_list_field_scopes=required_list_field_scopes,
            )
        )

        # Build the snapshot scope id from the dashboard scope +
        # feature id. The pattern follows the doc-13a:280-282
        # "snapshot:<scope>:<feature_id>" convention recorded in the
        # snapshot_companion docstring at lines 412-415.
        snapshot_scope_id = f"snapshot:dashboard:{feature_id}"
        # The snapshot digest carries through verbatim from the
        # ControlPlaneSnapshot.snapshot_version digest (per the
        # snapshot_companion docstring at lines 469-475).
        snapshot_digest = snapshot_version

        # Per the doc-13a:280-282 + snapshot_companion contract the
        # caller-supplied manifest identifiers are required for the
        # AuthoritativeContextRef. For the dashboard wrapper the
        # manifest is itself the snapshot (no upstream manifest); we
        # use the snapshot_digest as the manifest_id + manifest_digest
        # to preserve the freshness contract per doc-13a:298-301.
        manifest_id = f"dashboard-snapshot:{feature_id}:{snapshot_version}"
        manifest_digest = snapshot_digest

        return self._snapshot_adapter.derive_companion(
            list_field_completeness,
            snapshot_scope_id=snapshot_scope_id,
            snapshot_digest=snapshot_digest,
            manifest_id=manifest_id,
            manifest_digest=manifest_digest,
            required_list_field_scopes=required_list_field_scopes,
        )

    # --- Composite-chain wrapper method ----------------------------------

    async def project_control_plane_snapshot_changed(
        self,
        *,
        feature_id: str,
        snapshot: Any,
        conn: Any | None = None,
        required_list_field_scopes: Sequence[str] = (),
        gate_scope_id: str | None = None,
        gate_input_digest: str | None = None,
        gate_authoritative_bundle: (
            AuthoritativePromptContextBundle | None
        ) = None,
        gate_proof_rows: Sequence[AuthoritativeGateProofRow] = (),
        required_snapshot_list_field_scopes_for_gate: Sequence[str] = (),
    ) -> str | None:
        """The P3-13A-6-3 binding closure wrapper method.

        When :attr:`wiring_enabled` is False (the default), delegates
        byte-identical to
        :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.project_control_plane_snapshot_changed`.

        When :attr:`wiring_enabled` is True, invokes the composite
        :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter`
        chain BEFORE delegating:

        1. Phase 1 (snapshot companion): derive the snapshot companion
           record from the snapshot's per-list-field completeness +
           the caller's required_list_field_scopes. Raises
           :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
           on incomplete required list fields (per doc-13a:280-282).
        2. Phase 2 (classifier routing): check the snapshot's
           classifier_routing.should_invoke_classifier signal. Raises
           :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
           when should_invoke_classifier is False (per
           doc-13a:280-282 classifier rule blocked).
        3. Phase 3 (gate composite -- OPTIONAL): when the caller
           supplies a gate scope (gate_scope_id +
           gate_input_digest + gate_authoritative_bundle), invoke the
           :class:`~iriai_build_v2.execution_control.snapshot_companion.LegacyGateConsumerSnapshotAdapter.derive_gate_with_snapshot`
           composite to validate the snapshot completeness covers the
           gate's required snapshot scope. Raises
           :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
           on incomplete snapshot scope for the gate (per
           doc-13a:18-23 + 111-115). Raises
           :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
           on incomplete gate companion record (per doc-13a:273-275).
        4. Phase 4 (legacy delegation): when ALL composite checks
           pass, delegate to the legacy outbox method byte-identical.

        On ANY composite-chain failure: record the typed
        :class:`DashboardCompanionFailureRecord` via the
        :class:`DashboardCompanionFailurePort` (the typed-failure
        observation is durable BEFORE the wrapper raises) and re-raise
        the typed exception. The legacy outbox is NEVER invoked when
        the composite chain fails.

        Returns the legacy outbox method's return value (the event_id
        string or None) on success. Raises the typed
        :class:`~iriai_build_v2.execution_control.snapshot_companion.MissingSnapshotCompanionFieldError`
        / :class:`~iriai_build_v2.execution_control.gate_companion.MissingGateCompanionFieldError`
        / :class:`~iriai_build_v2.execution_control.gate_companion.MissingProofRowFieldError`
        on composite-chain failures.
        """

        if not self.wiring_enabled:
            # Per the auto-memory `feedback_no_refactor` rule + the
            # gate-5 byte-identical Slice 10 baseline proof: when the
            # wiring is OFF, the wrapper delegates byte-identical to
            # the legacy method.
            return await self._outbox.project_control_plane_snapshot_changed(
                feature_id=feature_id,
                snapshot=snapshot,
                conn=conn,
            )

        # ── Wiring ON path: invoke the composite chain BEFORE delegating ──

        # Per the doc-13a:280-282 spec the snapshot_version digest is
        # required for the snapshot companion record's snapshot_digest
        # (the freshness contract). When the caller passes a snapshot
        # with an empty snapshot_version, the legacy outbox method
        # itself raises (per public_dashboard.py:286-289); we let the
        # composite-chain helper's own fail-closed signal carry the
        # typed failure (a ValueError -> MissingSnapshotCompanionFieldError
        # -> typed failure record).
        payload = control_plane_snapshot_changed_payload(snapshot)
        snapshot_version = str(payload.get("snapshot_version") or "")
        if not snapshot_version:
            # Mirror the legacy outbox's fail-closed enqueue rule;
            # also record the typed failure so the observation is
            # durable.
            failure_record = DashboardCompanionFailureRecord(
                failure_class="evidence_corruption",
                failure_type="list_field_incomplete",
                snapshot_scope_id=f"snapshot:dashboard:{feature_id}",
                snapshot_digest="",
                missing_field_names=("snapshot_version",),
                unavailable_reason=(
                    "snapshot.snapshot_version is empty; the composite "
                    "chain cannot tie the companion record to a "
                    "specific snapshot input per doc-13a:298-301"
                ),
            )
            self._failure_port.record(failure_record)
            raise MissingSnapshotCompanionFieldError(
                ["snapshot_version"],
                snapshot_scope_id=f"snapshot:dashboard:{feature_id}",
                unavailable_reason=(
                    "snapshot.snapshot_version is empty; the composite "
                    "chain cannot tie the companion record to a "
                    "specific snapshot input"
                ),
            )

        # ── Phase 1 + 2: snapshot companion record + classifier routing ──
        try:
            snapshot_companion = self._derive_snapshot_companion(
                snapshot,
                feature_id=feature_id,
                snapshot_version=snapshot_version,
                required_list_field_scopes=required_list_field_scopes,
            )
        except MissingSnapshotCompanionFieldError as exc:
            self._record_snapshot_failure(
                exc,
                feature_id=feature_id,
                snapshot_version=snapshot_version,
                gate_scope_id=gate_scope_id,
            )
            raise

        # Per doc-13a:280-282 + snapshot_companion.py classifier_routing
        # rule: should_invoke_classifier=False signals the snapshot
        # cannot drive the classifier rule (e.g. required scope is
        # paged + unsatisfied). The composite chain fails closed.
        if not snapshot_companion.classifier_routing.should_invoke_classifier:
            failure_record = DashboardCompanionFailureRecord(
                failure_class="evidence_corruption",
                failure_type="classifier_rule_blocked",
                snapshot_scope_id=snapshot_companion.snapshot_scope_id,
                snapshot_digest=snapshot_companion.snapshot_digest,
                missing_field_names=snapshot_companion.classifier_routing.missing_field_names,
                gate_scope_id=gate_scope_id,
                unavailable_reason=snapshot_companion.classifier_routing.unavailable_reason,
            )
            self._failure_port.record(failure_record)
            raise MissingSnapshotCompanionFieldError(
                snapshot_companion.classifier_routing.missing_field_names,
                snapshot_scope_id=snapshot_companion.snapshot_scope_id,
                unavailable_reason=(
                    snapshot_companion.classifier_routing.unavailable_reason
                    or "snapshot classifier rule is blocked per doc-13a:280-282"
                ),
            )

        # ── Phase 3: gate composite (OPTIONAL) ──
        # Per the 6th-sub-slice LegacyGateConsumerSnapshotAdapter
        # composite: when the caller supplies a gate scope (the 3 gate
        # arguments are all non-None), invoke the composite to validate
        # the snapshot completeness covers the gate's required snapshot
        # scope + the gate companion record is complete.
        if (
            gate_scope_id is not None
            and gate_input_digest is not None
            and gate_authoritative_bundle is not None
        ):
            try:
                self._gate_consumer_adapter.derive_gate_with_snapshot(
                    snapshot_companion,
                    gate_authoritative_bundle,
                    gate_scope_id=gate_scope_id,
                    gate_input_digest=gate_input_digest,
                    required_snapshot_list_field_scopes=(
                        required_snapshot_list_field_scopes_for_gate
                    ),
                    proof_rows=gate_proof_rows,
                )
            except MissingSnapshotCompanionFieldError as exc:
                self._record_snapshot_failure(
                    exc,
                    feature_id=feature_id,
                    snapshot_version=snapshot_version,
                    gate_scope_id=gate_scope_id,
                )
                raise
            except MissingGateCompanionFieldError as exc:
                self._record_gate_failure(
                    exc,
                    snapshot_companion=snapshot_companion,
                    gate_scope_id=gate_scope_id,
                )
                raise
            except MissingProofRowFieldError as exc:
                self._record_proof_row_failure(
                    exc,
                    snapshot_companion=snapshot_companion,
                    gate_scope_id=gate_scope_id,
                )
                raise

        # ── Phase 4: legacy delegation ──
        # The composite chain passed -- delegate to the legacy outbox
        # method. Per the auto-memory `feedback_no_refactor` rule the
        # legacy method is invoked byte-identical (we DO NOT alter the
        # arguments; the wrapper passes them through verbatim).
        return await self._outbox.project_control_plane_snapshot_changed(
            feature_id=feature_id,
            snapshot=snapshot,
            conn=conn,
        )

    # --- Fail-closed recorder helpers ------------------------------------

    def _record_snapshot_failure(
        self,
        exc: MissingSnapshotCompanionFieldError,
        *,
        feature_id: str,
        snapshot_version: str,
        gate_scope_id: str | None,
    ) -> None:
        """Record a typed snapshot-companion failure on the port."""

        failure_record = DashboardCompanionFailureRecord(
            failure_class="evidence_corruption",
            failure_type="list_field_incomplete",
            snapshot_scope_id=(
                exc.snapshot_scope_id
                or f"snapshot:dashboard:{feature_id}"
            ),
            snapshot_digest=snapshot_version,
            missing_field_names=exc.missing_field_names,
            gate_scope_id=gate_scope_id,
            unavailable_reason=exc.unavailable_reason,
        )
        self._failure_port.record(failure_record)

    def _record_gate_failure(
        self,
        exc: MissingGateCompanionFieldError,
        *,
        snapshot_companion: AuthoritativeSnapshotCompanionRecord,
        gate_scope_id: str,
    ) -> None:
        """Record a typed gate-companion failure on the port."""

        failure_record = DashboardCompanionFailureRecord(
            failure_class="verifier_context",
            failure_type="companion_record_unavailable",
            snapshot_scope_id=snapshot_companion.snapshot_scope_id,
            snapshot_digest=snapshot_companion.snapshot_digest,
            missing_field_names=exc.missing_field_names,
            gate_scope_id=exc.gate_scope_id or gate_scope_id,
            unavailable_reason=exc.unavailable_reason,
        )
        self._failure_port.record(failure_record)

    def _record_proof_row_failure(
        self,
        exc: MissingProofRowFieldError,
        *,
        snapshot_companion: AuthoritativeSnapshotCompanionRecord,
        gate_scope_id: str,
    ) -> None:
        """Record a typed proof-row failure on the port."""

        failure_record = DashboardCompanionFailureRecord(
            failure_class="verifier_context",
            failure_type="proof_row_required",
            snapshot_scope_id=snapshot_companion.snapshot_scope_id,
            snapshot_digest=snapshot_companion.snapshot_digest,
            missing_field_names=exc.missing_field_names,
            gate_scope_id=gate_scope_id,
            unavailable_reason=(
                f"typed proof row required but mandatory field(s) missing: "
                f"{exc.missing_field_names}"
            ),
        )
        self._failure_port.record(failure_record)

    # --- Legacy passthrough methods --------------------------------------

    # Per the auto-memory `feedback_no_refactor` rule: the wrapper
    # exposes legacy outbox methods unchanged so existing callers can
    # swap in the wrapper without code changes. The wrapper only
    # intercepts `project_control_plane_snapshot_changed` for the
    # composite chain; every other method delegates byte-identical.

    async def emit_event(self, *args: Any, **kwargs: Any) -> str | None:
        """Delegate byte-identical to
        :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.emit_event`.
        """

        return await self._outbox.emit_event(*args, **kwargs)

    async def enqueue_display_job(self, *args: Any, **kwargs: Any) -> str | None:
        """Delegate byte-identical to
        :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.enqueue_display_job`.
        """

        return await self._outbox.enqueue_display_job(*args, **kwargs)

    async def pending_summary(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """Delegate byte-identical to
        :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.pending_summary`.
        """

        return await self._outbox.pending_summary(*args, **kwargs)

    async def delete_pending_before(self, *args: Any, **kwargs: Any) -> int:
        """Delegate byte-identical to
        :meth:`~iriai_build_v2.public_dashboard.PublicDashboardOutbox.delete_pending_before`.
        """

        return await self._outbox.delete_pending_before(*args, **kwargs)
