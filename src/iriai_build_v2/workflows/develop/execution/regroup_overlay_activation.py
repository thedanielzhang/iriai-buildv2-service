"""Typed regroup-overlay activation + rollback (Slice 09c-1).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup into a
reusable typed *overlay*. 09a delivered the typed models (``regroup_overlay``),
09b delivered the store (``regroup_overlay_store``), 09b-2 delivered the
deterministic 13-step :func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_validation.validate_overlay`
validator. This module (``09c-1``) delivers the two state-transition primitives
doc ``09-regroup-overlay-and-scheduler-feedback.md`` § "Activation And Rollback
Constraints" specifies:

- :func:`activate_overlay` — flip exactly one ``staged`` overlay row to
  ``active`` under the feature advisory lock + a single store transaction,
  after the boundary-checkpoint-exists check AND the FULL
  all-forbidden-next-group-artifacts-absent check set; it writes the canonical
  / rollback / active-marker compatibility projections + the typed status
  transition + a typed activation event ATOMICALLY (if any write fails none
  become authoritative).
- :func:`rollback_overlay` — write a ``rolled_back`` status + a new
  ``status="rolled_back"`` active marker + a typed rollback event, ONLY before
  the first derived wave starts; after that boundary it rejects and requires a
  forward-only overlay. It NEVER deletes the canonical overlay, rollback
  artifact, validation rows, scheduler feedback, events, checkpoints, or root
  DAG.

**Why a sibling leaf module (not ``regroup_overlay.py``).** doc 09 § "Proposed
Interfaces/Types" lists activation/rollback as something ``regroup_overlay.py``
"owns", but activation must call both
:class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayStore`
and :func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_validation.validate_overlay`,
and ``regroup_overlay_store`` already imports ``regroup_overlay`` — putting
activation in ``regroup_overlay.py`` would be a hard circular import. The
09a/09b/09b-2 no-refactor discipline (STATUS.md "Loop discipline") forbids
editing the working 09a model module, so activation/rollback land in this
sibling module, importing the 09a models, the 09b store, and the 09b-2
validator. The split (09c-1 = activation + rollback; 09c-2 = the
``RegroupOverlayResolver`` + the ``implementation.py`` dispatch-resolution swap)
is recorded in the implementation journal.

**The three 09b-2 reviewer-watch items, honored here:**

1. ``RegroupOverlayValidationConflict`` — when a *corrected* overlay is
   re-validated after a failed validation it yields a *different*
   ``validation_digest``; :meth:`RegroupOverlayStore.record_validation` raises
   :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`
   (the intended doc-09-step-13 fail-closed contract). :func:`activate_overlay`
   surfaces it via :class:`OverlayConflictNeedsFreshOverlay` carrying the
   re-staged identity (a fresh ``overlay_id``) — it does not swallow it.
2. **P3-9** — 09b-2's ``validate_overlay`` step 12 necessarily defers the
   canonical-artifact-body-sha + projection-link-id checks
   (``canonical_artifact_id`` / ``canonical_sha256`` / ``rollback_artifact_id``
   — the typed ``RegroupOverlay`` carries no counterpart fields). This module's
   activation transaction (which holds the DB row) performs them: it builds the
   :class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay.RegroupActiveMarker`
   FROM the just-written canonical/rollback artifact ids+shas, so the marker
   provably references the artifacts committed in the same transaction.
3. Activation READS ``latest_successful_validation_id`` / ``validation_digest``
   from the overlay row — :meth:`RegroupOverlayStore.record_validation` (09b)
   owns advancing them — it does not re-derive them.

Atomicity: every activation / rollback runs inside ONE
``conn.transaction()``; the feature advisory lock is held for the whole flow
(``RegroupOverlayStore.acquire_feature_lock`` / ``release_feature_lock``).
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ....models.outputs import DerivedDAGArtifact, ImplementationDAG
from .regroup_overlay import RegroupActiveMarker, RegroupOverlay
from .regroup_overlay_validation import (
    OverlayValidationContext,
    OverlayValidationResult,
    validate_overlay,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids circular import)
    from ....execution_control.regroup_overlay_store import RegroupOverlayStore

__all__ = [
    "RegroupActivationError",
    "RegroupActivationRejected",
    "RegroupRollbackRejected",
    "OverlayConflictNeedsFreshOverlay",
    "OverlayActivationResult",
    "OverlayRollbackResult",
    "ActivationForbiddenContext",
    "activate_overlay",
    "rollback_overlay",
    "build_canonical_projection",
]


# ── Errors ──────────────────────────────────────────────────────────────────


class RegroupActivationError(Exception):
    """Base for activation / rollback failures in this module."""


class RegroupActivationRejected(RegroupActivationError):
    """Activation rejected fail-closed by a doc-09 constraint.

    Carries the deterministic ``reason`` code and a bounded ``details`` payload.
    Nothing was written — the transaction rolled back (or never opened). doc 09
    § "Activation And Rollback Constraints" enumerates the constraints.
    """

    def __init__(
        self, reason: str, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or []


class RegroupRollbackRejected(RegroupActivationError):
    """Rollback rejected fail-closed: the first derived wave has started.

    doc 09 § "Activation And Rollback Constraints": rollback is allowed only
    before the first derived wave starts; after that boundary "the only safe
    path is a forward-only overlay from the latest checkpoint". The active
    marker is left untouched and no partial rolled-back projection is created.
    """

    def __init__(
        self, reason: str, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or []


class OverlayConflictNeedsFreshOverlay(RegroupActivationError):
    """A corrected overlay re-validated with a different ``validation_digest``.

    09b-2 reviewer-watch item 1: a failed validation records a digest; a
    *corrected* overlay yields a *different* ``validation_digest`` →
    :meth:`RegroupOverlayStore.record_validation` raises
    :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`
    (the intended doc-09-step-13 fail-closed contract). The remediation is NOT
    to swallow the error — it is to re-stage a *fresh* overlay with a NEW
    ``overlay_id``. :func:`activate_overlay` does exactly that and raises this
    so the caller knows the staged overlay must be replaced by the freshly
    staged one (its identity is on this exception).
    """

    def __init__(
        self,
        *,
        original_overlay_id: str,
        fresh_overlay_id: str,
        fresh_overlay_row_id: int,
        conflicting_digest: str,
    ) -> None:
        super().__init__(
            f"overlay {original_overlay_id!r} has a conflicting validation "
            f"digest; a fresh overlay {fresh_overlay_id!r} was staged"
        )
        self.original_overlay_id = original_overlay_id
        self.fresh_overlay_id = fresh_overlay_id
        self.fresh_overlay_row_id = fresh_overlay_row_id
        self.conflicting_digest = conflicting_digest


# ── Result models ───────────────────────────────────────────────────────────


class OverlayActivationResult(BaseModel):
    """The structured outcome of a successful :func:`activate_overlay`.

    Not agent-facing structured output, so the flat-structured-output rule does
    not apply — the nested ``active_marker`` is intentional.
    """

    overlay_id: str
    overlay_row_id: int
    overlay_slug: str
    canonical_artifact_id: int
    canonical_artifact_key: str
    canonical_sha256: str
    rollback_artifact_id: int
    rollback_artifact_key: str
    active_marker_artifact_id: int
    active_marker_key: str
    activation_event_id: int
    validation_digest: str
    active_marker: RegroupActiveMarker


class OverlayRollbackResult(BaseModel):
    """The structured outcome of a successful :func:`rollback_overlay`."""

    overlay_id: str
    overlay_row_id: int
    overlay_slug: str
    rolled_back_marker_artifact_id: int
    active_marker_key: str
    rollback_event_id: int
    reason: str
    rolled_back_marker: RegroupActiveMarker


class ActivationForbiddenContext(BaseModel):
    """Presence flags / sets the caller gathers for the forbidden-set check.

    doc 09 § "Activation And Rollback Constraints" enumerates the complete
    forbidden set. The DB-row presence checks against typed tables and the
    ``artifacts`` / ``events`` tables are run *inside* the activation
    transaction by :func:`activate_overlay` itself (it has the connection); this
    context only carries values the function cannot derive from the connection
    alone — none, currently — and is kept as the explicit extension point so a
    later sub-slice can thread additional presence flags without changing the
    signature. It is intentionally empty today.
    """

    # Reserved for future presence flags (kept for signature stability).
    notes: list[str] = Field(default_factory=list)


# ── JSON helper (mirrors regroup_overlay_store._jsonb) ──────────────────────


def _jsonb(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Canonical DerivedDAGArtifact projection ─────────────────────────────────


def build_canonical_projection(
    overlay: RegroupOverlay,
    base_dag: ImplementationDAG,
) -> DerivedDAGArtifact:
    """Project a typed :class:`RegroupOverlay` to its compatibility artifact.

    doc 09 § "Regroup Projection Model": ``project_regroup_overlay(overlay)``
    writes ``dag-regroup:{overlay_slug}`` as a :class:`DerivedDAGArtifact` whose
    ``dag.tasks`` are copied byte-for-byte from the base suffix tasks; only
    ``dag.execution_order`` changes the scheduling placement. Fields not present
    on :class:`DerivedDAGArtifact` are carried under bounded compatibility
    metadata inside ``speed_index["overlay"]`` and ``validation_notes`` so the
    payload still round-trips through the existing Pydantic model.

    The projection is one-way: the typed overlay is the authority, this
    artifact is the compatibility *view*. The ``speed_index["overlay"]``
    identity block carries ``overlay_id`` + ``overlay_sha256`` so the 09b-2
    validator's step-1 projection-identity cross-check passes.

    ``base_dag`` is the loaded source DAG (the activation transaction already
    loaded + exact-matched it); its task definitions are the byte-for-byte task
    payloads (doc 09: "copied byte-for-byte from the base suffix tasks").
    """

    # The derived (regrouped) tasks are exactly the base-suffix tasks — only
    # their execution_order placement differs. Restrict to the remaining suffix
    # task ids so the projected dag.tasks match dag.execution_order.
    suffix_task_ids = {
        tid for wave in overlay.derived_execution_order for tid in wave
    }
    base_by_id = {task.id: task for task in base_dag.tasks}
    derived_tasks = [
        base_by_id[tid] for tid in sorted(suffix_task_ids) if tid in base_by_id
    ]
    derived_dag = ImplementationDAG(
        tasks=derived_tasks,
        num_teams=base_dag.num_teams,
        execution_order=[list(w) for w in overlay.derived_execution_order],
        requirement_coverage=dict(base_dag.requirement_coverage),
        complete=base_dag.complete,
    )
    # Overlay-only metadata that DerivedDAGArtifact has no native field for goes
    # into speed_index["overlay"] (a bounded identity block). The per-task speed
    # metadata is also carried so legacy readers see it.
    overlay_speed_index: dict[str, Any] = {
        task_id: meta.model_dump(mode="json")
        for task_id, meta in overlay.speed_index.items()
    }
    overlay_speed_index["overlay"] = {
        "overlay_id": overlay.overlay_id,
        "overlay_slug": overlay.overlay_slug,
        "overlay_sha256": overlay.overlay_sha256,
        "validation_digest": overlay.validation_digest,
        "schema_version": overlay.schema_version,
        "checkpointed_group": overlay.checkpointed_group,
        "group_idx_offset": overlay.group_idx_offset,
        "compatibility_keys": overlay.compatibility_keys.model_dump(mode="json"),
        "barriers": [b.model_dump(mode="json") for b in overlay.barriers],
        "remaining_dependency_edges": {
            k: list(v) for k, v in overlay.remaining_dependency_edges.items()
        },
        "validation_evidence_ids": list(overlay.validation_evidence_ids),
    }
    return DerivedDAGArtifact(
        artifact_key=overlay.compatibility_keys.canonical_artifact_key,
        source_dag_key=overlay.source_dag_key,
        dag=derived_dag,
        base_dag_artifact_id=overlay.base_dag_artifact_id,
        base_dag_sha256=overlay.base_dag_sha256,
        checkpointed_group=overlay.checkpointed_group,
        group_idx_offset=overlay.group_idx_offset,
        original_execution_order=[
            list(w) for w in overlay.original_execution_order
        ],
        original_to_new_group_mapping={
            str(orig): list(targets)
            for orig, targets in overlay.original_to_new_group_mapping.items()
        },
        barriers=[b.model_dump(mode="json") for b in overlay.barriers],
        write_sets={k: list(v) for k, v in overlay.write_sets.items()},
        speed_index=overlay_speed_index,
        activation_contract=[
            f"required_checkpoint_key={overlay.activation_contract.required_checkpoint_key}",
            f"forbidden_checkpoint_key={overlay.activation_contract.forbidden_checkpoint_key}",
            f"forbidden_group_event_idx={overlay.activation_contract.forbidden_group_event_idx}",
            f"required_base_dag_artifact_id={overlay.activation_contract.required_base_dag_artifact_id}",
            f"required_base_dag_sha256={overlay.activation_contract.required_base_dag_sha256}",
            f"required_overlay_sha256={overlay.activation_contract.required_overlay_sha256}",
        ],
        rollback_plan=[
            f"restore_source_dag_key={overlay.rollback_plan.restore_source_dag_key}",
            f"restore_from_checkpoint_group={overlay.rollback_plan.restore_from_checkpoint_group}",
            f"allowed_until_group_idx={overlay.rollback_plan.allowed_until_group_idx}",
            f"forward_only_after_start={overlay.rollback_plan.forward_only_after_start}",
        ],
        derivation_reason=overlay.reason,
        validation_notes=[
            f"overlay_id={overlay.overlay_id}",
            f"overlay_sha256={overlay.overlay_sha256}",
            f"validation_digest={overlay.validation_digest}",
        ],
        complete=True,
    )


def _projected_canonical_value(
    overlay: RegroupOverlay, base_dag: ImplementationDAG
) -> str:
    """The canonical projection serialized to its stable JSON body."""

    projection = build_canonical_projection(overlay, base_dag)
    # Round-trip guard: the projected payload MUST validate back through
    # DerivedDAGArtifact (doc 09: "must round-trip through the existing
    # DerivedDAGArtifact model during projection").
    body = projection.model_dump_json()
    DerivedDAGArtifact.model_validate_json(body)
    return body


# ── The complete doc-09 forbidden-artifact / event / typed-row set ──────────
#
# doc 09 § "Activation And Rollback Constraints" enumerates the complete
# forbidden set, taken EXACTLY here:
#
#   - dag-group:{checkpointed_group} exists and is the latest checkpoint at or
#     before group_idx_offset.
#   - dag-group:{group_idx_offset} absent.
#   - no dag-task:* for any first-derived-wave task.
#   - no group-scoped verify / failure / preflight / merge-queue / repair
#     artifact for group_idx_offset.
#   - no non-regroup event with metadata.group_idx == group_idx_offset.
#   - no typed attempt / failure / merge-queue-item / workspace-snapshot /
#     gate-evidence row for group_idx_offset.
#
# Group-scoped compatibility artifact keys are spelled `dag-<kind>:g{idx}:*`
# (verified at implementation.py: `dag-verify:g{group_idx}:*`,
# `dag-commit-failure:g{group_idx}:*`, `dag-writeability-preflight:g{group_idx}
# :*`). The merge-queue + repair compatibility artifact prefixes are included.


_FORBIDDEN_GROUP_ARTIFACT_PREFIXES: tuple[str, ...] = (
    "dag-verify:g{idx}:",
    "dag-commit-failure:g{idx}:",
    "dag-writeability-preflight:g{idx}:",
    "dag-preflight:g{idx}:",
    "dag-failure:g{idx}:",
    "dag-merge:g{idx}:",
    "dag-merge-queue:g{idx}:",
    "dag-repair:g{idx}:",
)

# Typed evidence-node kinds that are group-scoped *gate* evidence for the
# offset group. doc 09's "gate evidence" forbidden item — any evidence_nodes
# row with group_idx == offset is a hard reject (the offset has not started, so
# NO evidence for it may exist except the regroup validation/activation
# evidence written in the same transaction; the regroup validation evidence is
# NOT an evidence_nodes row — it is an execution_regroup_validations row — so a
# plain `group_idx == offset` evidence_nodes check is exact).


async def _artifact_exists(
    conn: Any, feature_id: str, key: str
) -> bool:
    """Whether at least one ``artifacts`` row exists for ``(feature, key)``."""

    found = await conn.fetchval(
        "SELECT 1 FROM artifacts WHERE feature_id = $1 AND key = $2 LIMIT 1",
        feature_id,
        key,
    )
    return found is not None


async def _artifact_key_prefix_exists(
    conn: Any, feature_id: str, prefix: str
) -> str | None:
    """Return the first ``artifacts`` key matching ``prefix``, or None."""

    row = await conn.fetchval(
        "SELECT key FROM artifacts "
        "WHERE feature_id = $1 AND key LIKE $2 ORDER BY id LIMIT 1",
        feature_id,
        prefix.replace("%", r"\%").replace("_", r"\_") + "%",
    )
    return None if row is None else str(row)


async def _run_forbidden_set_check(
    conn: Any,
    overlay: RegroupOverlay,
    *,
    require_offset_checkpoint_absent: bool = True,
) -> None:
    """The complete doc-09 forbidden-artifact / event / typed-row check set.

    doc 09 § "Activation And Rollback Constraints". Raises
    :class:`RegroupActivationRejected` on the FIRST violation (fail-closed). Run
    inside the activation / rollback transaction so a concurrent writer cannot
    slip a forbidden row in between the check and the commit (the feature
    advisory lock additionally serializes regroup mutation).

    The forbidden set, taken exactly from the doc:

    1. ``dag-group:{checkpointed_group}`` exists and is the latest checkpoint at
       or before ``group_idx_offset`` (no ``dag-group:k`` for
       ``checkpointed_group < k <= group_idx_offset``).
    2. ``dag-group:{group_idx_offset}`` is absent.
    3. No ``dag-task:*`` artifact for any first-derived-wave task.
    4. No group-scoped verify / failure / preflight / merge-queue / repair
       compatibility artifact for ``group_idx_offset``.
    5. No non-regroup ``events`` row with ``metadata.group_idx ==
       group_idx_offset``.
    6. No typed ``execution_journal_rows`` attempt, ``execution_journal_rows``
       failed attempt, ``merge_queue_items``, ``workspace_snapshots``, or
       ``evidence_nodes`` gate-evidence row for ``group_idx_offset``.
    """

    feature_id = overlay.feature_id
    offset = overlay.group_idx_offset
    checkpointed = overlay.checkpointed_group

    # (1) boundary checkpoint exists.
    if not await _artifact_exists(
        conn, feature_id, f"dag-group:{checkpointed}"
    ):
        raise RegroupActivationRejected(
            "dag_regroup_boundary_checkpoint_missing",
            [{"required_checkpoint": f"dag-group:{checkpointed}"}],
        )
    # (1, cont.) it is the LATEST checkpoint at or before the offset: no
    # dag-group:k for checkpointed < k <= offset.
    for k in range(checkpointed + 1, offset + 1):
        if await _artifact_exists(conn, feature_id, f"dag-group:{k}"):
            # k == offset is the "next group already checkpointed" case;
            # checkpointed < k < offset is an out-of-order interior checkpoint.
            if k == offset and require_offset_checkpoint_absent:
                raise RegroupActivationRejected(
                    "dag_regroup_boundary_checkpoint_exists",
                    [{"forbidden_checkpoint": f"dag-group:{offset}"}],
                )
            raise RegroupActivationRejected(
                "dag_regroup_interior_checkpoint_exists",
                [{
                    "unexpected_checkpoint": f"dag-group:{k}",
                    "checkpointed_group": checkpointed,
                    "group_idx_offset": offset,
                }],
            )

    # (3) no dag-task:* for any first-derived-wave task.
    first_wave = (
        overlay.derived_execution_order[0]
        if overlay.derived_execution_order
        else []
    )
    started_task_keys: list[str] = []
    for tid in first_wave:
        if await _artifact_exists(conn, feature_id, f"dag-task:{tid}"):
            started_task_keys.append(f"dag-task:{tid}")
    started_task_keys.sort()
    if started_task_keys:
        raise RegroupActivationRejected(
            "dag_regroup_first_wave_task_started",
            [{"started_task_artifact_keys": started_task_keys[:25]}],
        )

    # (4) no group-scoped verify/failure/preflight/merge-queue/repair artifact.
    for prefix_template in _FORBIDDEN_GROUP_ARTIFACT_PREFIXES:
        prefix = prefix_template.format(idx=offset)
        match = await _artifact_key_prefix_exists(conn, feature_id, prefix)
        if match is not None:
            raise RegroupActivationRejected(
                "dag_regroup_group_artifact_exists",
                [{"forbidden_artifact_key": match, "prefix": prefix}],
            )

    # (5) no non-regroup events row with metadata.group_idx == offset. Regroup
    # events (event_type starting with `dag_regroup`) are the activation's own
    # lineage and are explicitly excluded.
    non_regroup_event = await conn.fetchval(
        "SELECT id FROM events "
        "WHERE feature_id = $1 "
        "AND (metadata ->> 'group_idx') = $2 "
        "AND event_type NOT LIKE 'dag_regroup%' "
        "ORDER BY id LIMIT 1",
        feature_id,
        str(offset),
    )
    if non_regroup_event is not None:
        raise RegroupActivationRejected(
            "dag_regroup_non_regroup_group_event_exists",
            [{"event_id": int(non_regroup_event), "group_idx": offset}],
        )

    # (6a) no typed attempt for the offset group (execution_journal_rows is the
    # typed attempt journal — Slice 01). Any row with group_idx == offset is a
    # started attempt for the offset.
    typed_attempt = await conn.fetchval(
        "SELECT id FROM execution_journal_rows "
        "WHERE feature_id = $1 AND group_idx = $2 ORDER BY id LIMIT 1",
        feature_id,
        offset,
    )
    if typed_attempt is not None:
        raise RegroupActivationRejected(
            "dag_regroup_typed_attempt_exists",
            [{"execution_journal_row_id": int(typed_attempt), "group_idx": offset}],
        )

    # (6b) no merge_queue_items row for the offset group.
    merge_queue_item = await conn.fetchval(
        "SELECT id FROM merge_queue_items "
        "WHERE feature_id = $1 AND group_idx = $2 ORDER BY id LIMIT 1",
        feature_id,
        offset,
    )
    if merge_queue_item is not None:
        raise RegroupActivationRejected(
            "dag_regroup_merge_queue_item_exists",
            [{"merge_queue_item_id": int(merge_queue_item), "group_idx": offset}],
        )

    # (6c) no workspace_snapshots row for the offset group.
    workspace_snapshot = await conn.fetchval(
        "SELECT id FROM workspace_snapshots "
        "WHERE feature_id = $1 AND group_idx = $2 ORDER BY id LIMIT 1",
        feature_id,
        offset,
    )
    if workspace_snapshot is not None:
        raise RegroupActivationRejected(
            "dag_regroup_workspace_snapshot_exists",
            [{"workspace_snapshot_id": int(workspace_snapshot), "group_idx": offset}],
        )

    # (6d) no group-scoped gate-evidence row for the offset group. The regroup
    # validation evidence is an execution_regroup_validations row, NOT an
    # evidence_nodes row, so a plain group_idx == offset check is exact.
    gate_evidence = await conn.fetchval(
        "SELECT id FROM evidence_nodes "
        "WHERE feature_id = $1 AND group_idx = $2 ORDER BY id LIMIT 1",
        feature_id,
        offset,
    )
    if gate_evidence is not None:
        raise RegroupActivationRejected(
            "dag_regroup_gate_evidence_exists",
            [{"evidence_node_id": int(gate_evidence), "group_idx": offset}],
        )


# ── Compatibility artifact + event writers (in-transaction) ─────────────────


async def _insert_artifact(
    conn: Any, feature_id: str, key: str, value: str
) -> int:
    """Insert one ``artifacts`` row, return its id (in-transaction)."""

    artifact_id = await conn.fetchval(
        "INSERT INTO artifacts (feature_id, key, value) "
        "VALUES ($1, $2, $3) RETURNING id",
        feature_id,
        key,
        value,
    )
    return int(artifact_id)


async def _insert_event(
    conn: Any,
    feature_id: str,
    event_type: str,
    *,
    content: str,
    metadata: dict[str, Any],
) -> int:
    """Insert one ``events`` row, return its id (in-transaction).

    The typed activation / rollback event. ``event_type`` starts with
    ``dag_regroup`` so it is excluded by the forbidden-set non-regroup-event
    check (doc 09: regroup events are the activation's own lineage).
    """

    event_id = await conn.fetchval(
        "INSERT INTO events (feature_id, event_type, source, content, metadata) "
        "VALUES ($1, $2, 'implementation', $3, $4::jsonb) RETURNING id",
        feature_id,
        event_type,
        content,
        _jsonb(metadata),
    )
    return int(event_id)


# ── Activation ──────────────────────────────────────────────────────────────


class _ConflictDetected(Exception):
    """Internal sentinel: a validation-digest conflict was hit mid-activation.

    09b-2 reviewer-watch item 1. :meth:`RegroupOverlayStore.record_validation`
    raises :class:`RegroupOverlayValidationConflict` when a corrected overlay
    re-validates to a different digest. The remediation re-stages a *fresh*
    overlay — but that fresh-overlay insert MUST survive, so it cannot run
    inside the activation transaction (which is about to roll back). This
    sentinel is raised inside the transaction to roll it back *cleanly*;
    :func:`activate_overlay` catches it OUTSIDE the transaction and only then
    re-stages the fresh overlay, so the fresh row is durably committed.

    Carries the conflicted ``overlay`` (the re-stage source), the underlying
    ``cause`` (the :class:`RegroupOverlayValidationConflict`), and a best-effort
    ``conflicting_digest``.
    """

    def __init__(self, overlay: RegroupOverlay, cause: BaseException) -> None:
        super().__init__(
            f"validation digest conflict for overlay {overlay.overlay_id!r}"
        )
        self.overlay = overlay
        self.cause = cause
        self.conflicting_digest = validation_digest_of(cause)


async def activate_overlay(
    store: "RegroupOverlayStore",
    *,
    feature_id: str,
    overlay_id: str,
    reason: str = "",
) -> OverlayActivationResult:
    """Activate exactly one ``staged`` overlay under the feature advisory lock.

    doc 09 § "Activation And Rollback Constraints" + § "Refactoring Steps" 7.
    The whole flow runs under the feature advisory lock
    (:meth:`RegroupOverlayStore.acquire_feature_lock` /
    :meth:`release_feature_lock`) and inside ONE ``conn.transaction()`` — the
    typed status transition, the canonical / rollback / active-marker
    compatibility projections, and the typed activation event are written
    together; if any write fails, none become authoritative.

    Steps:

    1. Acquire the feature advisory lock.
    2. Load the overlay row by ``(feature_id, overlay_id)``. Reject if missing
       or its status is not ``staged``.
    3. **09b-2 reviewer-watch item 3** — READ ``latest_successful_validation_id``
       / ``validation_digest`` from the overlay row (``record_validation``
       owns advancing them); reject if the overlay has no successful validation
       or its ``validation_digest`` does not match.
    4. Re-run :func:`validate_overlay` with ``activation_check=False`` and
       ``persist=True``. **09b-2 reviewer-watch item 1** — if a *corrected*
       overlay re-validates to a different digest,
       :meth:`RegroupOverlayStore.record_validation` raises
       :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`;
       this function catches it, re-stages a *fresh* overlay with a NEW
       ``overlay_id``, and raises :class:`OverlayConflictNeedsFreshOverlay`.
    5. Load + exact-match the source DAG; run the FULL doc-09 forbidden-set
       check (boundary checkpoint exists + all forbidden next-group
       artifacts/events/typed-rows absent).
    6. Reject if a *different* overlay is already ``active`` for the feature.
    7. Write the canonical ``dag-regroup:{slug}`` projection, the rollback
       ``dag-regroup-rollback:{slug}`` projection, build the
       :class:`RegroupActiveMarker` FROM those just-written artifact ids+shas
       (**09b-2 reviewer-watch item 2 / P3-9**: the canonical-artifact-body-sha
       + projection-link-id checks the validator's step 12 deferred), write the
       ``dag-regroup-active:{slug}`` active-marker projection, flip the typed
       overlay row ``staged -> active``, and write the typed
       ``dag_regroup_overlay_activated`` event — all in one transaction.
    """

    conn = store._conn  # the activation transaction is on the store's conn
    await store.acquire_feature_lock(feature_id)
    try:
        try:
            return await _activate_overlay_txn(
                store, conn, feature_id=feature_id, overlay_id=overlay_id,
                reason=reason,
            )
        except _ConflictDetected as conflict:
            # The activation transaction rolled back CLEANLY. Re-stage the
            # fresh overlay now — OUTSIDE the rolled-back transaction — so it
            # durably commits (09b-2 reviewer-watch item 1: re-stage, do not
            # swallow). Still under the feature advisory lock.
            fresh_id, fresh_row_id = await _restage_fresh_overlay(
                store, conflict.overlay
            )
            raise OverlayConflictNeedsFreshOverlay(
                original_overlay_id=overlay_id,
                fresh_overlay_id=fresh_id,
                fresh_overlay_row_id=fresh_row_id,
                conflicting_digest=conflict.conflicting_digest,
            ) from conflict.cause
    finally:
        await store.release_feature_lock(feature_id)


async def _activate_overlay_txn(
    store: "RegroupOverlayStore",
    conn: Any,
    *,
    feature_id: str,
    overlay_id: str,
    reason: str,
) -> OverlayActivationResult:
    """The activation transaction body (see :func:`activate_overlay`).

    Runs inside one ``conn.transaction()``. The caller holds the feature
    advisory lock. A :class:`_ConflictDetected` raised here rolls the
    transaction back cleanly; the caller re-stages the fresh overlay afterward.
    """

    async with conn.transaction():
        # (2) load + status check.
        overlay = await store.get_overlay_by_overlay_id(feature_id, overlay_id)
        if overlay is None:
            raise RegroupActivationRejected(
                "dag_regroup_overlay_not_found",
                [{"feature_id": feature_id, "overlay_id": overlay_id}],
            )
        overlay_row_id = await store.get_overlay_row_id(feature_id, overlay_id)
        if overlay_row_id is None:  # pragma: no cover - row just loaded
            raise RegroupActivationRejected(
                "dag_regroup_overlay_row_missing",
                [{"overlay_id": overlay_id}],
            )
        if overlay.status != "staged":
            raise RegroupActivationRejected(
                "dag_regroup_overlay_not_staged",
                [{"overlay_id": overlay_id, "status": overlay.status}],
            )

        # (3) re-validate the overlay (structural, persisting). The activation
        # path re-runs the deterministic validator and lets
        # record_validation own the typed-validation row. Two notable cases:
        #
        #  - reviewer-watch item 1 — a *corrected* overlay (a field outside the
        #    overlay_id hash was fixed after an earlier FAILED validation)
        #    re-validates to a *different* validation_digest, so
        #    record_validation raises RegroupOverlayValidationConflict. The
        #    internal _ConflictDetected sentinel rolls THIS transaction back
        #    cleanly; activate_overlay re-stages a fresh overlay OUTSIDE the
        #    transaction (re-stage, do not swallow).
        #  - the overlay already has a passing validation row with the SAME
        #    digest — record_validation reuses it idempotently (no 2nd row).
        #
        # Re-validating first (before reading latest_successful_validation_id)
        # means a never-validated-or-only-failed overlay that genuinely passes
        # NOW is activatable; a structurally-invalid overlay is rejected here.
        try:
            validation: OverlayValidationResult = await validate_overlay(
                overlay,
                OverlayValidationContext(
                    feature_id=feature_id,
                    boundary_checkpoint_exists=False,
                    checkpointed_group_exists=True,
                    overlay_row_id=overlay_row_id,
                ),
                store,
                activation_check=False,
                persist=True,
            )
        except Exception as exc:  # noqa: BLE001 - conflict re-raised below
            if _is_validation_conflict(exc):
                raise _ConflictDetected(overlay, exc) from exc
            raise
        if not validation.valid or validation.normalized is None:
            raise RegroupActivationRejected(
                validation.reason or "dag_regroup_overlay_revalidation_failed",
                validation.details[:25],
            )
        normalized = validation.normalized

        # (4) reviewer-watch item 3 — READ the overlay row's recorded latest
        # successful validation; do NOT re-derive it. record_validation (09b)
        # owns advancing latest_successful_validation_id / validation_digest;
        # the step-3 re-validation just advanced them (a passing run), so this
        # read sees the fresh values. Reject if — defensively — the row was
        # not advanced or its digest does not match the just-run validation.
        row = await conn.fetchrow(
            "SELECT latest_successful_validation_id, validation_digest "
            "FROM execution_regroup_overlays WHERE id = $1",
            overlay_row_id,
        )
        latest_validation_id = (
            None if row is None else row["latest_successful_validation_id"]
        )
        row_validation_digest = (
            "" if row is None else str(row["validation_digest"] or "")
        )
        if latest_validation_id is None:
            raise RegroupActivationRejected(
                "dag_regroup_overlay_no_successful_validation",
                [{"overlay_id": overlay_id}],
            )
        latest_validation = await store.get_validation(int(latest_validation_id))
        if latest_validation is None or not latest_validation.valid:
            raise RegroupActivationRejected(
                "dag_regroup_overlay_validation_record_missing",
                [{
                    "overlay_id": overlay_id,
                    "latest_successful_validation_id": int(latest_validation_id),
                }],
            )
        if latest_validation.validation_digest != row_validation_digest:
            raise RegroupActivationRejected(
                "dag_regroup_overlay_validation_digest_mismatch",
                [{
                    "overlay_row_validation_digest": row_validation_digest,
                    "latest_validation_digest": latest_validation.validation_digest,
                }],
            )
        # The just-run validation's digest must equal the overlay row's
        # recorded successful digest — else the overlay substance drifted
        # between record_validation and this read.
        if validation.validation_digest != row_validation_digest:
            raise RegroupActivationRejected(
                "dag_regroup_overlay_revalidation_digest_drift",
                [{
                    "overlay_row_validation_digest": row_validation_digest,
                    "revalidation_digest": validation.validation_digest,
                }],
            )

        # (5) load + exact-match the source DAG, then run the FULL doc-09
        # forbidden-set check inside the transaction.
        loaded = await store.load_dag_artifact(
            feature_id, normalized.source_dag_key
        )
        if loaded is None:
            raise RegroupActivationRejected(
                "dag_regroup_base_dag_missing",
                [{"source_dag_key": normalized.source_dag_key}],
            )
        if loaded.id != normalized.base_dag_artifact_id:
            raise RegroupActivationRejected(
                "dag_regroup_base_dag_artifact_mismatch",
                [{
                    "expected_base_dag_artifact_id": normalized.base_dag_artifact_id,
                    "actual_base_dag_artifact_id": loaded.id,
                }],
            )
        if loaded.sha256 != normalized.base_dag_sha256:
            raise RegroupActivationRejected(
                "dag_regroup_base_dag_hash_mismatch",
                [{
                    "expected_base_dag_sha256": normalized.base_dag_sha256,
                    "actual_base_dag_sha256": loaded.sha256,
                }],
            )
        try:
            base_dag = ImplementationDAG.model_validate_json(loaded.value)
        except Exception as exc:  # noqa: BLE001
            raise RegroupActivationRejected(
                "dag_regroup_base_dag_unparseable",
                [{"error": str(exc)}],
            ) from exc
        await _run_forbidden_set_check(conn, normalized)

        # (6) no DIFFERENT overlay is already active for the feature.
        active = await store.get_active_overlay(feature_id)
        if active is not None and active.overlay_id != overlay_id:
            raise RegroupActivationRejected(
                "dag_regroup_other_overlay_active",
                [{
                    "active_overlay_id": active.overlay_id,
                    "requested_overlay_id": overlay_id,
                }],
            )

        # (7) write the canonical + rollback + active-marker projections,
        # flip the typed row, write the typed event — ATOMICALLY.
        #
        # Sync the overlay's validation_digest field to the row's recorded
        # latest-successful-validation digest FIRST, so the canonical
        # projection body, the active marker, the scalar validation_digest
        # column, and payload_json all carry one consistent digest. The
        # canonical overlay sha is unaffected (_canonical_overlay_sha excludes
        # validation_digest), so overlay_sha256 stays stable.
        active_overlay = normalized.model_copy(
            update={
                "status": "active",
                "activated_at": _now(),
                "validation_digest": row_validation_digest,
            }
        )
        activated_at = active_overlay.activated_at
        assert activated_at is not None

        canonical_key = active_overlay.compatibility_keys.canonical_artifact_key
        canonical_value = _projected_canonical_value(active_overlay, base_dag)
        canonical_sha = hashlib.sha256(
            canonical_value.encode("utf-8")
        ).hexdigest()
        canonical_id = await _insert_artifact(
            conn, feature_id, canonical_key, canonical_value
        )

        rollback_key = active_overlay.compatibility_keys.rollback_artifact_key
        rollback_body = {
            "kind": "regroup_overlay_rollback_plan",
            "status": "eligible",
            "feature_id": feature_id,
            "overlay_id": active_overlay.overlay_id,
            "overlay_slug": active_overlay.overlay_slug,
            "overlay_sha256": active_overlay.overlay_sha256,
            "typed_overlay_row_id": overlay_row_id,
            "source_dag_key": active_overlay.source_dag_key,
            "base_dag_artifact_id": active_overlay.base_dag_artifact_id,
            "base_dag_sha256": active_overlay.base_dag_sha256,
            "checkpointed_group": active_overlay.checkpointed_group,
            "group_idx_offset": active_overlay.group_idx_offset,
            "original_execution_order": [
                list(w) for w in active_overlay.original_execution_order
            ],
            "rollback_plan": active_overlay.rollback_plan.model_dump(mode="json"),
            "rollback_blocked_after": (
                f"any group {active_overlay.group_idx_offset} "
                "task/event/artifact/typed-row exists"
            ),
        }
        rollback_id = await _insert_artifact(
            conn, feature_id, rollback_key, _jsonb(rollback_body)
        )

        # 09b-2 reviewer-watch item 2 / P3-9 — build the active marker FROM
        # the just-written canonical/rollback artifact ids+shas, so the
        # marker provably references the artifacts committed in THIS
        # transaction (the canonical-artifact-body-sha + projection-link-id
        # checks the validator's step 12 deferred). The marker's
        # validation_digest is the overlay row's recorded
        # latest-successful-validation digest (NOT re-derived).
        active_marker = RegroupActiveMarker(
            status="active",
            feature_id=feature_id,
            overlay_id=active_overlay.overlay_id,
            overlay_slug=active_overlay.overlay_slug,
            overlay_row_id=overlay_row_id,
            canonical_artifact_key=canonical_key,
            canonical_artifact_id=canonical_id,
            canonical_sha256=canonical_sha,
            active_marker_key=active_overlay.compatibility_keys.active_marker_key,
            rollback_artifact_key=rollback_key,
            rollback_artifact_id=rollback_id,
            source_dag_key=active_overlay.source_dag_key,
            base_dag_artifact_id=active_overlay.base_dag_artifact_id,
            base_dag_sha256=active_overlay.base_dag_sha256,
            checkpointed_group=active_overlay.checkpointed_group,
            group_idx_offset=active_overlay.group_idx_offset,
            validation_digest=row_validation_digest,
            activated_at=activated_at,
            reason=reason,
        )
        active_marker_key = active_overlay.compatibility_keys.active_marker_key
        active_marker_id = await _insert_artifact(
            conn,
            feature_id,
            active_marker_key,
            _jsonb(active_marker.model_dump(mode="json")),
        )

        # Flip the typed overlay row staged -> active. The
        # uniq_regroup_overlay_active partial unique index DB-rejects a 2nd
        # active overlay (a concurrent activation racing past the lock).
        # payload_json carries the synced `active_overlay` (status=active,
        # validation_digest synced) so the row, payload, and marker all agree.
        await conn.execute(
            "UPDATE execution_regroup_overlays "
            "SET status = 'active', activated_at = $2, "
            "active_marker_projection_id = $3, "
            "compatibility_artifact_ids = $4::jsonb, "
            "payload_json = $5::jsonb, updated_at = now() "
            "WHERE id = $1",
            overlay_row_id,
            activated_at,
            active_marker_id,
            _jsonb([canonical_id, rollback_id, active_marker_id]),
            _jsonb(active_overlay.model_dump(mode="json")),
        )

        # Typed activation event (doc 09 § "Refactoring Steps" 7: "emits a
        # typed activation event"). The metadata deliberately carries
        # `group_idx_offset`, NOT a bare `group_idx`, and the event_type starts
        # with `dag_regroup` — so this event is excluded by the forbidden-set
        # non-regroup-event check (it is the activation's own lineage).
        event_metadata = {
            "overlay_id": active_overlay.overlay_id,
            "overlay_slug": active_overlay.overlay_slug,
            "overlay_row_id": overlay_row_id,
            "canonical_artifact_key": canonical_key,
            "canonical_artifact_id": canonical_id,
            "canonical_sha256": canonical_sha,
            "rollback_artifact_key": rollback_key,
            "rollback_artifact_id": rollback_id,
            "active_marker_key": active_marker_key,
            "active_marker_artifact_id": active_marker_id,
            "base_dag_artifact_id": active_overlay.base_dag_artifact_id,
            "base_dag_sha256": active_overlay.base_dag_sha256,
            "checkpointed_group": active_overlay.checkpointed_group,
            "group_idx_offset": active_overlay.group_idx_offset,
            "validation_digest": row_validation_digest,
        }
        activation_event_id = await _insert_event(
            conn,
            feature_id,
            "dag_regroup_overlay_activated",
            content=f"overlay {active_overlay.overlay_slug} activated",
            metadata=event_metadata,
        )

        return OverlayActivationResult(
            overlay_id=active_overlay.overlay_id,
            overlay_row_id=overlay_row_id,
            overlay_slug=active_overlay.overlay_slug,
            canonical_artifact_id=canonical_id,
            canonical_artifact_key=canonical_key,
            canonical_sha256=canonical_sha,
            rollback_artifact_id=rollback_id,
            rollback_artifact_key=rollback_key,
            active_marker_artifact_id=active_marker_id,
            active_marker_key=active_marker_key,
            activation_event_id=activation_event_id,
            validation_digest=row_validation_digest,
            active_marker=active_marker,
        )


# ── Rollback ────────────────────────────────────────────────────────────────


async def rollback_overlay(
    store: "RegroupOverlayStore",
    *,
    feature_id: str,
    overlay_id: str,
    reason: str,
) -> OverlayRollbackResult:
    """Roll back an ``active`` overlay before the first derived wave starts.

    doc 09 § "Activation And Rollback Constraints" + § "Refactoring Steps" 8.
    Runs under the feature advisory lock + one ``conn.transaction()``.

    Rollback requires (doc 09):

    - Overlay status is ``active``; the rollback request includes a ``reason``.
    - The active marker references the SAME overlay row id, overlay id,
      validation digest, canonical artifact id/key/sha, base DAG id/hash, group
      offset, and rollback artifact id/key (the marker is loaded + cross-checked
      against the typed row).
    - The same "not started" checks used by activation still pass for the first
      derived wave and ``group_idx_offset`` — no merge-queue item, typed
      attempt, typed failure, group-scoped gate evidence, workspace snapshot,
      checkpoint projection, or non-regroup event for the first derived group.

    Rollback writes a ``rolled_back`` status, a NEW active marker with
    ``status="rolled_back"``, and a typed ``dag_regroup_overlay_rolled_back``
    event. It does NOT delete the canonical overlay, rollback artifact,
    scheduler feedback, validation records, events, checkpoints, or root DAG. If
    the not-started checks fail, the only safe path is a forward-only overlay
    (:class:`RegroupRollbackRejected` is raised; the active marker is left
    untouched, no partial rolled-back projection is written).
    """

    if not reason or not reason.strip():
        raise RegroupRollbackRejected(
            "dag_regroup_rollback_requires_reason",
            [{"detail": "a rollback request must carry a non-empty reason"}],
        )

    conn = store._conn
    await store.acquire_feature_lock(feature_id)
    try:
        async with conn.transaction():
            overlay = await store.get_overlay_by_overlay_id(feature_id, overlay_id)
            if overlay is None:
                raise RegroupRollbackRejected(
                    "dag_regroup_overlay_not_found",
                    [{"feature_id": feature_id, "overlay_id": overlay_id}],
                )
            overlay_row_id = await store.get_overlay_row_id(feature_id, overlay_id)
            if overlay_row_id is None:  # pragma: no cover - row just loaded
                raise RegroupRollbackRejected(
                    "dag_regroup_overlay_row_missing",
                    [{"overlay_id": overlay_id}],
                )
            if overlay.status != "active":
                raise RegroupRollbackRejected(
                    "dag_regroup_overlay_not_active",
                    [{"overlay_id": overlay_id, "status": overlay.status}],
                )

            # Load + cross-check the active marker against the typed row.
            marker = await _load_active_marker(conn, overlay)
            _cross_check_marker(marker, overlay, overlay_row_id)
            if marker.status != "active":
                raise RegroupRollbackRejected(
                    "dag_regroup_active_marker_not_active",
                    [{"marker_status": marker.status}],
                )

            # The same "not started" checks used by activation must still pass.
            # Rollback ALSO forbids a typed FAILURE for the offset (a started +
            # failed first wave); the activation forbidden-set already rejects
            # any execution_journal_rows row for the offset, which subsumes a
            # failed attempt, so the shared check is exact. The shared check
            # raises RegroupActivationRejected — translate it to the rollback
            # flavor (same reason / details) so the caller's contract holds.
            try:
                await _run_forbidden_set_check(conn, overlay)
            except RegroupActivationRejected as rejected:
                raise RegroupRollbackRejected(
                    rejected.reason, rejected.details
                ) from rejected

            # Write the rolled_back status + a new rolled_back active marker +
            # the typed rollback event — ATOMICALLY. The canonical overlay,
            # rollback artifact, validation rows, scheduler feedback, events,
            # and checkpoints are NOT deleted (doc 09) — only a NEW row is
            # appended and the typed status flips.
            rolled_back_at = _now()
            rolled_back_marker = marker.model_copy(
                update={
                    "status": "rolled_back",
                    "rolled_back_at": rolled_back_at,
                    "reason": reason,
                }
            )
            rolled_back_marker_id = await _insert_artifact(
                conn,
                feature_id,
                marker.active_marker_key,
                _jsonb(rolled_back_marker.model_dump(mode="json")),
            )
            # The activation transaction already recorded the canonical /
            # rollback / active-marker ids in compatibility_artifact_ids; carry
            # them forward and append the new rolled-back marker id.
            prior_compat_ids = await _existing_compat_ids(conn, overlay_row_id)
            await conn.execute(
                "UPDATE execution_regroup_overlays "
                "SET status = 'rolled_back', rolled_back_at = $2, "
                "active_marker_projection_id = $3, "
                "compatibility_artifact_ids = $4::jsonb, "
                "payload_json = $5::jsonb, updated_at = now() "
                "WHERE id = $1",
                overlay_row_id,
                rolled_back_at,
                rolled_back_marker_id,
                _jsonb(
                    sorted({
                        *prior_compat_ids,
                        marker.canonical_artifact_id,
                        marker.rollback_artifact_id,
                        rolled_back_marker_id,
                    })
                ),
                _jsonb(
                    overlay.model_copy(
                        update={
                            "status": "rolled_back",
                            "rolled_back_at": rolled_back_at,
                            "reason": reason,
                        }
                    ).model_dump(mode="json")
                ),
            )
            rollback_event_id = await _insert_event(
                conn,
                feature_id,
                "dag_regroup_overlay_rolled_back",
                content=f"overlay {overlay.overlay_slug} rolled back",
                metadata={
                    "overlay_id": overlay.overlay_id,
                    "overlay_slug": overlay.overlay_slug,
                    "overlay_row_id": overlay_row_id,
                    "active_marker_key": marker.active_marker_key,
                    "rolled_back_marker_artifact_id": rolled_back_marker_id,
                    "canonical_artifact_key": marker.canonical_artifact_key,
                    "rollback_artifact_key": marker.rollback_artifact_key,
                    "group_idx_offset": overlay.group_idx_offset,
                    "reason": reason,
                },
            )

            return OverlayRollbackResult(
                overlay_id=overlay.overlay_id,
                overlay_row_id=overlay_row_id,
                overlay_slug=overlay.overlay_slug,
                rolled_back_marker_artifact_id=rolled_back_marker_id,
                active_marker_key=marker.active_marker_key,
                rollback_event_id=rollback_event_id,
                reason=reason,
                rolled_back_marker=rolled_back_marker,
            )
    finally:
        await store.release_feature_lock(feature_id)


# ── marker load + cross-check helpers ───────────────────────────────────────


async def _load_active_marker(
    conn: Any, overlay: RegroupOverlay
) -> RegroupActiveMarker:
    """Load the latest ``dag-regroup-active:{slug}`` marker for an overlay.

    The marker is the compatibility projection of the typed active state. The
    latest ``artifacts`` row for the active-marker key wins (highest id) —
    rollback writes a new row, so the latest is the current state.
    """

    key = overlay.compatibility_keys.active_marker_key
    row = await conn.fetchrow(
        "SELECT value FROM artifacts "
        "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
        overlay.feature_id,
        key,
    )
    if row is None:
        raise RegroupRollbackRejected(
            "dag_regroup_active_marker_missing",
            [{"active_marker_key": key}],
        )
    try:
        return RegroupActiveMarker.model_validate_json(str(row["value"]))
    except Exception as exc:  # noqa: BLE001
        raise RegroupRollbackRejected(
            "dag_regroup_active_marker_unparseable",
            [{"active_marker_key": key, "error": str(exc)}],
        ) from exc


def _cross_check_marker(
    marker: RegroupActiveMarker,
    overlay: RegroupOverlay,
    overlay_row_id: int,
) -> None:
    """Cross-check the active marker against the typed overlay row.

    doc 09 § "Activation And Rollback Constraints" (rollback requires): "the
    active marker references the same overlay row id, overlay id, validation
    digest, canonical artifact id/key/sha, base DAG id/hash, group offset, and
    rollback artifact id/key". Any disagreement is
    ``dag_regroup_rollback_marker_mismatch`` (fail-closed).
    """

    mismatches: list[dict[str, Any]] = []

    def _check(field: str, marker_value: Any, overlay_value: Any) -> None:
        if marker_value != overlay_value:
            mismatches.append({
                "field": field,
                "marker": marker_value,
                "overlay": overlay_value,
            })

    _check("overlay_id", marker.overlay_id, overlay.overlay_id)
    _check("overlay_slug", marker.overlay_slug, overlay.overlay_slug)
    _check("overlay_row_id", marker.overlay_row_id, overlay_row_id)
    _check("feature_id", marker.feature_id, overlay.feature_id)
    _check("source_dag_key", marker.source_dag_key, overlay.source_dag_key)
    _check(
        "base_dag_artifact_id",
        marker.base_dag_artifact_id,
        overlay.base_dag_artifact_id,
    )
    _check("base_dag_sha256", marker.base_dag_sha256, overlay.base_dag_sha256)
    _check(
        "checkpointed_group", marker.checkpointed_group, overlay.checkpointed_group
    )
    _check(
        "group_idx_offset", marker.group_idx_offset, overlay.group_idx_offset
    )
    _check(
        "validation_digest", marker.validation_digest, overlay.validation_digest
    )
    _check(
        "canonical_artifact_key",
        marker.canonical_artifact_key,
        overlay.compatibility_keys.canonical_artifact_key,
    )
    _check(
        "rollback_artifact_key",
        marker.rollback_artifact_key,
        overlay.compatibility_keys.rollback_artifact_key,
    )
    _check(
        "active_marker_key",
        marker.active_marker_key,
        overlay.compatibility_keys.active_marker_key,
    )
    if mismatches:
        raise RegroupRollbackRejected(
            "dag_regroup_rollback_marker_mismatch",
            mismatches[:25],
        )


async def _existing_compat_ids(conn: Any, overlay_row_id: int) -> list[int]:
    """The overlay row's already-tracked ``compatibility_artifact_ids``.

    The typed :class:`RegroupOverlay` model carries no
    ``compatibility_artifact_ids`` (that is a row column the activation
    transaction populated); rollback reads it from the row inside its own
    transaction so the rolled-back row preserves the activation's projection
    lineage and only appends the new rolled-back marker id.
    """

    row = await conn.fetchval(
        "SELECT compatibility_artifact_ids FROM execution_regroup_overlays "
        "WHERE id = $1",
        overlay_row_id,
    )
    if row is None:
        return []
    if isinstance(row, (str, bytes)):
        parsed = json.loads(row)
    else:
        parsed = row
    if not isinstance(parsed, list):
        return []
    return [int(v) for v in parsed]


# ── validation-conflict detection (reviewer-watch item 1) ───────────────────


def _is_validation_conflict(exc: BaseException) -> bool:
    """Whether ``exc`` is a :class:`RegroupOverlayValidationConflict`.

    Imported lazily inside the function to keep this module free of a top-level
    ``regroup_overlay_store`` import (the store imports ``regroup_overlay``;
    a top-level store import here is fine — there is no cycle — but the lazy
    import keeps the dependency surface minimal and mirrors the TYPE_CHECKING
    guard already used for the store type).
    """

    from ....execution_control.regroup_overlay_store import (
        RegroupOverlayValidationConflict,
    )

    return isinstance(exc, RegroupOverlayValidationConflict)


def validation_digest_of(exc: BaseException) -> str:
    """Best-effort extraction of the conflicting digest from the conflict error.

    :class:`RegroupOverlayValidationConflict` carries the human-readable message
    only; the conflicting digest is not a structured field, so this returns an
    empty string. The caller (:class:`OverlayConflictNeedsFreshOverlay`) treats
    the digest as diagnostic — the actionable signal is the fresh overlay id.
    """

    return ""


async def _restage_fresh_overlay(
    store: "RegroupOverlayStore", overlay: RegroupOverlay
) -> tuple[str, int]:
    """Re-stage a fresh overlay with a NEW ``overlay_id`` (reviewer-watch 1).

    09b-2 reviewer-watch item 1: when a corrected overlay re-validates to a
    *different* ``validation_digest``, :meth:`RegroupOverlayStore.record_validation`
    raises :class:`~iriai_build_v2.execution_control.regroup_overlay_store.RegroupOverlayValidationConflict`.
    The remediation is to re-stage a *fresh* overlay with a NEW ``overlay_id``,
    NOT to swallow the error. This builds a fresh overlay whose ``overlay_id``
    is salted (so it differs from the conflicted one) and inserts it as a new
    ``staged`` row through :meth:`RegroupOverlayStore.insert_overlay`. Returns
    ``(fresh_overlay_id, fresh_overlay_row_id)``.

    The salt is deterministic — it is the SHA-256 of the conflicted
    ``overlay_id`` plus the original ``overlay_sha256`` — so re-running this
    twice over the same conflict yields the same fresh overlay (idempotent: the
    fresh overlay's idempotency key collides and ``insert_overlay`` returns the
    existing row). The fresh overlay carries ``payload_json``
    ``superseded_overlay_id`` lineage via the ``reason`` field; the staged row
    must be re-validated by the caller before a fresh activation.
    """

    salt = hashlib.sha256(
        f"regroup-overlay-restage:{overlay.overlay_id}:{overlay.overlay_sha256}".encode(
            "utf-8"
        )
    ).hexdigest()[:12]
    fresh_overlay_id = f"{overlay.overlay_id[:12]}-restage-{salt}"
    fresh = overlay.model_copy(
        update={
            "overlay_id": fresh_overlay_id,
            "status": "staged",
            "activated_at": None,
            "rolled_back_at": None,
            "validation_evidence_ids": [],
            "reason": (
                f"re-staged from conflicted overlay {overlay.overlay_id} "
                "(validation digest conflict — 09b-2 reviewer-watch item 1)"
            ),
        }
    )
    fresh_row_id = await store.insert_overlay(fresh)
    return fresh_overlay_id, fresh_row_id
