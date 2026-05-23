"""The fail-closed typed regroup-overlay dispatch resolver (Slice 09c-2).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup into a
reusable typed *overlay*. 09a delivered the typed models (``regroup_overlay``),
09b the store (``regroup_overlay_store``), 09b-2 the deterministic 13-step
:func:`~iriai_build_v2.workflows.develop.execution.regroup_overlay_validation.validate_overlay`
validator, and 09c-1 the activation / rollback state-transition primitives
(``regroup_overlay_activation``). This module (``09c-2``) delivers the
**resolver** doc ``09-regroup-overlay-and-scheduler-feedback.md`` § "Refactoring
Steps" 2 / 9 + § "Persistence And Artifact Compatibility" specifies:

:meth:`RegroupOverlayResolver.resolve` reads typed overlay state FIRST
(:meth:`RegroupOverlayStore.get_active_overlay`), validates the
active-marker / projection pair, and quiesces **FAIL-CLOSED** on ANY id / hash /
status / digest / projection-link mismatch. A stale or orphaned compatibility
marker may ONLY produce a diagnostic ``regroup_invalid`` quiesce reason — it can
NEVER make an invalid overlay executable.

The resolver loads, for the single ``active`` typed overlay row:

- the active typed row (``get_active_overlay``),
- the ``dag-regroup-active:{slug}`` active-marker projection (the latest
  ``artifacts`` row for the key),
- the canonical ``dag-regroup:{slug}`` regroup projection,
- the source DAG record by key,

and applies the overlay only when ALL ids, hashes, status values, validation
digest, group offset, and projection links match. It funnels through
:func:`validate_overlay` with ``activation_check=True`` so the validator's
step-12 :class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay.RegroupActiveMarker`
field checks run.

**The carried P3-A id/sha cross-check.** 09c-1's rollback ``_cross_check_marker``
(``regroup_overlay_activation.py``) cross-checks the loaded marker's overlay
id/slug/row-id/feature/source-dag-key/base-DAG id+hash/checkpointed-group/
group-offset/validation-digest + the 3 artifact KEYS, but NOT the marker's
``canonical_artifact_id`` / ``canonical_sha256`` / ``rollback_artifact_id`` —
the typed :class:`RegroupOverlay` carries no counterpart fields, and 09c-1's
rollback only writes a terminal ``rolled_back`` marker (it never re-reads the
canonical artifact body). The resolver DOES consume the canonical artifact body,
so it performs the FULL id/sha cross-check: the marker's
``canonical_artifact_id`` / ``canonical_sha256`` / ``rollback_artifact_id`` must
match the typed overlay row's ``compatibility_artifact_ids`` /
``active_marker_projection_id`` columns AND the canonical artifact body sha
computed over the loaded ``dag-regroup:{slug}`` artifact.

**Why a sibling leaf module (not ``regroup_overlay.py``).** doc 09 § "Proposed
Interfaces/Types" lists the resolver as something ``regroup_overlay.py`` "owns",
but the resolver must call :class:`RegroupOverlayStore` and
:func:`validate_overlay`, and ``regroup_overlay_store`` already imports
``regroup_overlay`` — putting the resolver in ``regroup_overlay.py`` would be a
hard circular import. The 09a/09b/09b-2/09c-1 no-refactor discipline (STATUS.md
"Loop discipline") forbids editing the working 09a model module, so the resolver
lands in this sibling leaf module, importing the 09a models, the 09b store, the
09b-2 validator, and the 09c-1 ``build_canonical_projection``. The split is
recorded in the implementation journal.

**Fail-closed is the core property.** Any path where a mismatched / stale /
orphaned overlay state produces an EXECUTABLE result (a non-None
``effective_execution_order``) rather than an ``effective=False`` quiesce is a
P1. Every reject path here returns
:class:`RegroupOverlayResolution` with ``effective_execution_order=None`` and a
``regroup_invalid`` (or more specific diagnostic) ``quiesce_reason``.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from ....models.outputs import ImplementationDAG
from .regroup_overlay import RegroupActiveMarker, RegroupOverlay
from .regroup_overlay_activation import build_canonical_projection
from .regroup_overlay_validation import (
    OverlayValidationContext,
    OverlayValidationResult,
    validate_overlay,
)

if TYPE_CHECKING:  # pragma: no cover - typing only (avoids circular import)
    from ....execution_control.regroup_overlay_store import RegroupOverlayStore

__all__ = [
    "RegroupOverlayResolution",
    "RegroupOverlayResolver",
]


# ── Result model ────────────────────────────────────────────────────────────


class RegroupOverlayResolution(BaseModel):
    """The structured outcome of :meth:`RegroupOverlayResolver.resolve`.

    There are exactly three mutually-exclusive shapes:

    1. **No typed active overlay.** ``has_typed_overlay=False``,
       ``applied=False``, ``effective_execution_order=None``,
       ``quiesce_reason=""``. The feature has no ``active`` typed overlay row, so
       the resolver makes no decision — the caller may fall through to its
       non-overlay behavior (a feature with no active regroup dispatches exactly
       as before).
    2. **Applied.** ``has_typed_overlay=True``, ``applied=True``,
       ``effective_execution_order`` is the derived execution order to dispatch
       (base prefix waves + the overlay's derived suffix waves),
       ``quiesce_reason=""``. The active typed overlay passed every check.
    3. **Quiesce.** ``has_typed_overlay=True``, ``applied=False``,
       ``effective_execution_order=None``, ``quiesce_reason`` is the diagnostic
       (``regroup_invalid`` or a more specific code). A stale / orphaned /
       mismatched overlay state — dispatch must FAIL-CLOSED.

    This is not agent-facing structured output, so the flat-structured-output
    rule does not apply — the nested ``observation`` dict is intentional.

    Fields:

    - ``has_typed_overlay`` — whether an ``active`` typed overlay row exists.
    - ``applied`` — whether the overlay passed all checks and is executable.
    - ``effective_execution_order`` — the derived execution order
      (``list[list[str]]``) when ``applied``; ``None`` otherwise.
    - ``quiesce_reason`` — the deterministic diagnostic code when the resolver
      quiesces dispatch; ``""`` otherwise. ``regroup_invalid`` is the umbrella
      reason; ``details`` carries the specific mismatch.
    - ``overlay_id`` — the active overlay's id (``""`` when none).
    - ``observation`` — a bounded diagnostic projection payload (selected
      overlay id, resume group, effective/derived group counts, validation
      evidence ids) for the ``dag-regroup-observation:{slug}`` artifact and
      restart diagnostics. Diagnostic only — it can never make an invalid
      overlay executable (doc 09 § "Refactoring Steps" 9).
    - ``details`` — bounded structured detail of the quiesce cause / success
      summary.
    """

    has_typed_overlay: bool
    applied: bool
    effective_execution_order: list[list[str]] | None = None
    quiesce_reason: str = ""
    overlay_id: str = ""
    observation: dict[str, Any] = Field(default_factory=dict)
    details: list[dict[str, Any]] = Field(default_factory=list)


# ── Internal: a check failure quiesces dispatch fail-closed ──────────────────


class _Quiesce(Exception):
    """An internal control-flow signal that a resolver check failed closed.

    Carries the diagnostic ``reason`` code and the bounded detail payload.
    :meth:`RegroupOverlayResolver.resolve` catches it and converts it to a
    quiesce :class:`RegroupOverlayResolution`. Using an exception keeps each
    check a straight-line function that can ``raise`` on the first violation.

    The ``reason`` is the *specific* diagnostic; the umbrella ``regroup_invalid``
    reason doc 09 § "Persistence And Artifact Compatibility" names is applied by
    :meth:`RegroupOverlayResolver.resolve` (it sets ``quiesce_reason`` to a
    ``regroup_invalid``-prefixed code so dashboards can both group all resolver
    quiesces and read the precise cause).
    """

    def __init__(
        self, reason: str, details: list[dict[str, Any]] | None = None
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details or []


_DETAIL_CAP = 25  # bounded detail payload (mirrors the validator's [:25])


# ── JSON helpers ────────────────────────────────────────────────────────────


def _sha256(value: str) -> str:
    """SHA-256 hex over a raw string (the canonical artifact body hash)."""

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _compat_ids(raw: Any) -> list[int]:
    """Coerce an ``execution_regroup_overlays.compatibility_artifact_ids`` value.

    The column is JSONB; asyncpg may surface it as a ``list`` or a JSON string.
    A non-list / unparseable value yields ``[]`` (the caller then fails closed
    on the absent expected ids).
    """

    parsed: Any = raw
    if isinstance(raw, (str, bytes)):
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return []
    if not isinstance(parsed, list):
        return []
    out: list[int] = []
    for value in parsed:
        try:
            out.append(int(value))
        except (TypeError, ValueError):
            continue
    return out


# ── The resolver ────────────────────────────────────────────────────────────


class RegroupOverlayResolver:
    """Fail-closed typed-overlay dispatch resolver (Slice 09c-2).

    Connection-bound: it holds a :class:`RegroupOverlayStore` over the caller's
    asyncpg connection (the Slice 08 ``MergeQueueStore`` / Slice 09b store
    model). The resolver does *not* acquire the feature advisory lock — it is a
    pure read-path resolver (it makes no writes), and the doc-09 invariant that
    activation / rollback run under the lock keeps the typed row + projections
    mutually consistent for the resolver to read. A read under no lock can at
    worst observe an overlay mid-transition; every observed inconsistency
    fails the resolver CLOSED (a ``regroup_invalid`` quiesce), never open.
    """

    def __init__(self, store: "RegroupOverlayStore") -> None:
        self._store = store

    async def resolve(
        self, feature_id: str, group_idx: int
    ) -> RegroupOverlayResolution:
        """Resolve the active typed regroup overlay for a dispatch group.

        doc 09 § "Refactoring Steps" 2 + § "Persistence And Artifact
        Compatibility". Reads typed overlay state FIRST, validates the
        active-marker / projection pair, and quiesces FAIL-CLOSED on ANY id /
        hash / status / digest / projection-link mismatch.

        Returns a :class:`RegroupOverlayResolution` in one of three shapes (see
        that class): **no typed overlay** (the caller may fall through to its
        non-overlay behavior), **applied** (``effective_execution_order`` is the
        derived order to dispatch), or **quiesce** (``quiesce_reason`` is the
        diagnostic — dispatch must fail closed).

        ``group_idx`` is the absolute group index dispatch is about to run; it
        is checked against the overlay's ``group_idx_offset`` (the resolver only
        applies an overlay at, or after, its offset — a probe BEFORE the offset
        is not the overlay's concern).
        """

        if not feature_id:
            # No feature id — the resolver cannot read typed state. This is not
            # a regroup decision; report "no typed overlay" so the caller keeps
            # its non-overlay behavior.
            return RegroupOverlayResolution(
                has_typed_overlay=False, applied=False
            )

        # ── Step A — read the single active typed overlay row FIRST ─────────
        # doc 09 § "Persistence And Artifact Compatibility": "The resolver
        # reads typed overlay state first". The uniq_regroup_overlay_active
        # partial unique index guarantees at most one `active` row.
        active_overlay = await self._store.get_active_overlay(feature_id)
        if active_overlay is None:
            # No active typed overlay — the resolver makes no decision. A
            # feature with no active regroup dispatches exactly as before.
            return RegroupOverlayResolution(
                has_typed_overlay=False, applied=False
            )

        # From here on a typed overlay EXISTS; every failure is a fail-closed
        # quiesce (effective_execution_order stays None).
        try:
            return await self._resolve_active(
                feature_id, group_idx, active_overlay
            )
        except _Quiesce as quiesce:
            return RegroupOverlayResolution(
                has_typed_overlay=True,
                applied=False,
                effective_execution_order=None,
                quiesce_reason=quiesce.reason,
                overlay_id=active_overlay.overlay_id,
                observation={
                    "status": "quiesced",
                    "overlay_id": active_overlay.overlay_id,
                    "overlay_slug": active_overlay.overlay_slug,
                    "quiesce_reason": quiesce.reason,
                    "resume_group_idx": group_idx,
                },
                details=quiesce.details[:_DETAIL_CAP],
            )

    async def _resolve_active(
        self,
        feature_id: str,
        group_idx: int,
        active_overlay: RegroupOverlay,
    ) -> RegroupOverlayResolution:
        """Validate the active typed overlay and derive its execution order.

        Runs only when an ``active`` typed overlay row exists. Raises
        :class:`_Quiesce` on the FIRST fail-closed violation; on success it
        returns the **applied** :class:`RegroupOverlayResolution`.
        """

        conn = self._store._conn  # the resolver's read connection

        # ── Step B — the row's typed status must be `active` ────────────────
        # get_active_overlay already filters status='active', but assert it
        # defensively — a payload_json whose embedded status disagrees with the
        # row's status column is a corrupt row.
        if active_overlay.status != "active":  # pragma: no cover - defensive
            raise _Quiesce(
                "regroup_invalid_overlay_status",
                [{
                    "overlay_id": active_overlay.overlay_id,
                    "payload_status": active_overlay.status,
                }],
            )

        # ── Step C — the overlay applies at, or after, its group offset ─────
        # The resolver only resolves an overlay for a dispatch group at/after
        # the overlay's group_idx_offset. A probe BEFORE the offset is not this
        # overlay's concern — report "no typed overlay" so the caller keeps its
        # non-overlay behavior for the pre-offset groups (those groups dispatch
        # from the base DAG unchanged).
        if group_idx < active_overlay.group_idx_offset:
            return RegroupOverlayResolution(
                has_typed_overlay=False, applied=False
            )

        # ── Step D — load the active typed overlay row id ───────────────────
        overlay_row_id = await self._store.get_overlay_row_id(
            feature_id, active_overlay.overlay_id
        )
        if overlay_row_id is None:  # pragma: no cover - row just loaded
            raise _Quiesce(
                "regroup_invalid_overlay_row_missing",
                [{"overlay_id": active_overlay.overlay_id}],
            )

        # ── Step E — read the row's projection-link columns ─────────────────
        # active_marker_projection_id + compatibility_artifact_ids are row
        # columns the 09c-1 activation transaction populated. The typed
        # RegroupOverlay model carries no counterpart fields, so they are read
        # directly from the row for the carried P3-A id cross-check.
        row = await conn.fetchrow(
            "SELECT active_marker_projection_id, compatibility_artifact_ids, "
            "latest_successful_validation_id, validation_digest, status "
            "FROM execution_regroup_overlays WHERE id = $1",
            overlay_row_id,
        )
        if row is None:  # pragma: no cover - row id just resolved
            raise _Quiesce(
                "regroup_invalid_overlay_row_missing",
                [{"overlay_row_id": overlay_row_id}],
            )
        if str(row["status"]) != "active":  # pragma: no cover - defensive
            raise _Quiesce(
                "regroup_invalid_overlay_row_status",
                [{"overlay_row_id": overlay_row_id, "row_status": row["status"]}],
            )
        active_marker_projection_id = row["active_marker_projection_id"]
        compat_ids = _compat_ids(row["compatibility_artifact_ids"])
        latest_validation_id = row["latest_successful_validation_id"]
        row_validation_digest = str(row["validation_digest"] or "")

        # ── Step F — the row must have a recorded successful validation ─────
        # doc 09 § "Validation Algorithm" step 13 / § "Activation And Rollback
        # Constraints": the validation digest must match the latest successful
        # validation record. record_validation (09b) owns advancing
        # latest_successful_validation_id / validation_digest — the resolver
        # READS them, never re-derives (carried P3-7).
        if latest_validation_id is None:
            raise _Quiesce(
                "regroup_invalid_overlay_no_successful_validation",
                [{"overlay_id": active_overlay.overlay_id}],
            )
        latest_validation = await self._store.get_validation(
            int(latest_validation_id)
        )
        if latest_validation is None or not latest_validation.valid:
            raise _Quiesce(
                "regroup_invalid_overlay_validation_record_missing",
                [{
                    "overlay_id": active_overlay.overlay_id,
                    "latest_successful_validation_id": int(latest_validation_id),
                }],
            )
        if latest_validation.validation_digest != row_validation_digest:
            raise _Quiesce(
                "regroup_invalid_overlay_validation_digest_mismatch",
                [{
                    "overlay_row_validation_digest": row_validation_digest,
                    "latest_validation_digest": (
                        latest_validation.validation_digest
                    ),
                }],
            )
        # The typed overlay payload's own validation_digest must agree with the
        # row's recorded successful digest (the 09c-1 activation synced them).
        if active_overlay.validation_digest != row_validation_digest:
            raise _Quiesce(
                "regroup_invalid_overlay_payload_digest_mismatch",
                [{
                    "payload_validation_digest": active_overlay.validation_digest,
                    "row_validation_digest": row_validation_digest,
                }],
            )

        # ── Step G — load + parse the active-marker projection ──────────────
        # The latest dag-regroup-active:{slug} artifact row (highest id) is the
        # current marker — 09c-1 rollback writes a new row, so the latest wins.
        marker = await self._load_active_marker(conn, active_overlay)

        # ── Step H — the marker's status must be `active` ───────────────────
        # A `rolled_back` marker (the latest row after a 09c-1 rollback) means
        # the overlay is NOT executable — but a `rolled_back` marker over an
        # `active` typed row is itself an inconsistency (rollback flips the row
        # to `rolled_back` in the same transaction). Either way: fail closed.
        if marker.status != "active":
            raise _Quiesce(
                "regroup_invalid_active_marker_inactive",
                [{
                    "marker_status": marker.status,
                    "overlay_id": active_overlay.overlay_id,
                }],
            )

        # ── Step I — load + exact-match the source DAG by id+hash ───────────
        loaded = await self._store.load_dag_artifact(
            feature_id, active_overlay.source_dag_key
        )
        if loaded is None:
            raise _Quiesce(
                "regroup_invalid_source_dag_missing",
                [{"source_dag_key": active_overlay.source_dag_key}],
            )
        if loaded.id != active_overlay.base_dag_artifact_id:
            raise _Quiesce(
                "regroup_invalid_base_dag_artifact_mismatch",
                [{
                    "expected_base_dag_artifact_id": (
                        active_overlay.base_dag_artifact_id
                    ),
                    "actual_base_dag_artifact_id": loaded.id,
                }],
            )
        if loaded.sha256 != active_overlay.base_dag_sha256:
            raise _Quiesce(
                "regroup_invalid_base_dag_hash_mismatch",
                [{
                    "expected_base_dag_sha256": active_overlay.base_dag_sha256,
                    "actual_base_dag_sha256": loaded.sha256,
                }],
            )
        try:
            base_dag = ImplementationDAG.model_validate_json(loaded.value)
        except Exception as exc:  # noqa: BLE001
            raise _Quiesce(
                "regroup_invalid_source_dag_unparseable",
                [{"error": str(exc)}],
            ) from exc

        # ── Step J — load the canonical regroup projection by key ───────────
        canonical_key = active_overlay.compatibility_keys.canonical_artifact_key
        canonical = await self._load_canonical_projection(
            conn, feature_id, canonical_key
        )
        canonical_id = int(canonical["id"])
        canonical_value = str(canonical["value"])
        canonical_body_sha = _sha256(canonical_value)

        # ── Step K — the carried P3-A marker id/sha cross-check ─────────────
        # 09c-1's rollback _cross_check_marker deferred the marker's
        # canonical_artifact_id / canonical_sha256 / rollback_artifact_id checks
        # (the typed RegroupOverlay carries no counterpart fields). The resolver
        # DOES consume the canonical artifact body, so it does the FULL id/sha
        # cross-check: the marker's id/sha fields must match the typed row's
        # active_marker_projection_id / compatibility_artifact_ids columns AND
        # the canonical artifact body sha computed over the loaded artifact.
        self._cross_check_marker_ids_and_sha(
            marker,
            overlay_row_id=overlay_row_id,
            active_marker_projection_id=active_marker_projection_id,
            compat_ids=compat_ids,
            canonical_artifact_id=canonical_id,
            canonical_body_sha=canonical_body_sha,
        )

        # ── Step L — the marker's full field set vs the typed overlay row ───
        # The id/key/hash/digest/offset cross-check (the same field set 09c-1
        # rollback's _cross_check_marker enforces, PLUS the canonical/rollback
        # artifact KEYS); step K already cross-checked the artifact ids+sha.
        self._cross_check_marker_fields(marker, active_overlay, overlay_row_id)

        # ── Step M — funnel through validate_overlay(activation_check=True) ─
        # doc 09 § "Validation Algorithm" step 12 + the resolver brief: the
        # resolver "funnels through validate_overlay(activation_check=True) so
        # the validator's step-12 RegroupActiveMarker field checks run". This
        # re-runs the full deterministic 13-step validator over the active
        # typed overlay (steps 1-11 re-prove dependency / write-set / barrier /
        # mapping preservation against the freshly-loaded base DAG; step 12
        # re-validates the marker). persist=False — the resolver is a read path
        # and must not write a validation row on every dispatch probe.
        validation = await validate_overlay(
            active_overlay,
            OverlayValidationContext(
                feature_id=feature_id,
                boundary_checkpoint_exists=False,
                checkpointed_group_exists=True,
                active_marker=marker,
                overlay_row_id=overlay_row_id,
                latest_successful_validation_digest=row_validation_digest,
            ),
            self._store,
            activation_check=True,
            persist=False,
        )
        if not validation.valid or validation.normalized is None:
            raise _Quiesce(
                "regroup_invalid_overlay_validation_failed",
                self._validation_quiesce_details(validation),
            )
        normalized = validation.normalized

        # The validator recomputes overlay_sha256 from the normalized substance
        # (step 1). It must equal the typed row's recorded overlay_sha256 — a
        # disagreement means the persisted overlay substance drifted.
        if normalized.overlay_sha256 != active_overlay.overlay_sha256:
            raise _Quiesce(
                "regroup_invalid_overlay_sha_mismatch",
                [{
                    "row_overlay_sha256": active_overlay.overlay_sha256,
                    "revalidated_overlay_sha256": normalized.overlay_sha256,
                }],
            )

        # ── Step N — the canonical projection must match the typed overlay ──
        # doc 09 § "Regroup Projection Model" / § "Persistence And Artifact
        # Compatibility": the canonical dag-regroup:{slug} artifact is the
        # one-way projection of the typed overlay. The resolver re-derives the
        # canonical projection from the typed overlay (build_canonical_
        # projection — the SAME function 09c-1 activation used to WRITE it) and
        # requires its body sha to equal the stored canonical artifact body sha.
        # This proves the stored compatibility artifact has not drifted from the
        # typed authority. Use the active typed row (status='active') so the
        # projected status matches what activation wrote.
        try:
            rederived_canonical = build_canonical_projection(
                active_overlay, base_dag
            )
            rederived_body = rederived_canonical.model_dump_json()
        except Exception as exc:  # noqa: BLE001 - any projection error -> reject
            raise _Quiesce(
                "regroup_invalid_canonical_projection_unbuildable",
                [{"error": str(exc)}],
            ) from exc
        if _sha256(rederived_body) != canonical_body_sha:
            raise _Quiesce(
                "regroup_invalid_canonical_projection_drift",
                [{
                    "stored_canonical_sha256": canonical_body_sha,
                    "rederived_canonical_sha256": _sha256(rederived_body),
                    "canonical_artifact_id": canonical_id,
                }],
            )

        # ── Step O — derive the effective execution order ───────────────────
        # doc 09: the root `dag` is NEVER overwritten. The effective execution
        # order is the base DAG's prefix waves (groups 0..offset-1, unchanged)
        # followed by the overlay's derived suffix waves. group_idx_offset is in
        # [0, len(base_dag.execution_order)] (the validator's step 3 proved it),
        # so the prefix slice is well-formed.
        offset = active_overlay.group_idx_offset
        if offset > len(base_dag.execution_order):  # pragma: no cover - step3
            raise _Quiesce(
                "regroup_invalid_offset_out_of_range",
                [{
                    "group_idx_offset": offset,
                    "base_group_count": len(base_dag.execution_order),
                }],
            )
        effective_execution_order = [
            list(wave) for wave in base_dag.execution_order[:offset]
        ] + [list(wave) for wave in normalized.derived_execution_order]

        observation = {
            "status": "applied",
            "active_marker_key": (
                active_overlay.compatibility_keys.active_marker_key
            ),
            "canonical_artifact_key": canonical_key,
            "canonical_artifact_id": canonical_id,
            "canonical_sha256": canonical_body_sha,
            "overlay_id": active_overlay.overlay_id,
            "overlay_slug": active_overlay.overlay_slug,
            "overlay_row_id": overlay_row_id,
            "overlay_sha256": active_overlay.overlay_sha256,
            "source_dag_key": active_overlay.source_dag_key,
            "base_dag_artifact_id": active_overlay.base_dag_artifact_id,
            "base_dag_sha256": active_overlay.base_dag_sha256,
            "group_idx_offset": offset,
            "checkpointed_group": active_overlay.checkpointed_group,
            "resume_group_idx": group_idx,
            "validation_digest": row_validation_digest,
            "latest_successful_validation_id": int(latest_validation_id),
            "validation_evidence_ids": sorted(
                {*active_overlay.validation_evidence_ids,
                 *latest_validation.evidence_ids}
            ),
            "derived_group_count": len(normalized.derived_execution_order),
            "effective_group_count": len(effective_execution_order),
        }
        return RegroupOverlayResolution(
            has_typed_overlay=True,
            applied=True,
            effective_execution_order=effective_execution_order,
            quiesce_reason="",
            overlay_id=active_overlay.overlay_id,
            observation=observation,
            details=[{
                "derived_group_count": len(normalized.derived_execution_order),
                "effective_group_count": len(effective_execution_order),
                "group_idx_offset": offset,
            }],
        )

    # ── marker load + cross-check helpers ───────────────────────────────────

    async def _load_active_marker(
        self, conn: Any, overlay: RegroupOverlay
    ) -> RegroupActiveMarker:
        """Load the latest ``dag-regroup-active:{slug}`` marker for an overlay.

        The marker is the compatibility projection of the typed active state.
        The latest ``artifacts`` row for the active-marker key wins (highest
        id) — 09c-1 rollback writes a new row, so the latest is the current
        marker. A missing or unparseable marker over an ``active`` typed row is
        an orphaned-typed-row inconsistency — fail closed (doc 09 § "Edge Cases
        And Failure Handling": "Active typed row exists without active marker
        projection ... fail closed").
        """

        key = overlay.compatibility_keys.active_marker_key
        row = await conn.fetchrow(
            "SELECT value FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
            overlay.feature_id,
            key,
        )
        if row is None:
            raise _Quiesce(
                "regroup_invalid_active_marker_missing",
                [{
                    "active_marker_key": key,
                    "overlay_id": overlay.overlay_id,
                    "detail": (
                        "active typed overlay row has no active-marker "
                        "projection — orphaned typed row, fail closed"
                    ),
                }],
            )
        try:
            return RegroupActiveMarker.model_validate_json(str(row["value"]))
        except Exception as exc:  # noqa: BLE001
            raise _Quiesce(
                "regroup_invalid_active_marker_unparseable",
                [{"active_marker_key": key, "error": str(exc)}],
            ) from exc

    async def _load_canonical_projection(
        self, conn: Any, feature_id: str, canonical_key: str
    ) -> dict[str, Any]:
        """Load the latest ``dag-regroup:{slug}`` canonical projection row.

        The latest ``artifacts`` row for the canonical key wins (highest id) —
        the activation transaction wrote it. A missing canonical projection
        over an ``active`` typed row is an orphaned-typed-row inconsistency —
        fail closed.
        """

        row = await conn.fetchrow(
            "SELECT id, value FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
            feature_id,
            canonical_key,
        )
        if row is None:
            raise _Quiesce(
                "regroup_invalid_canonical_projection_missing",
                [{
                    "canonical_artifact_key": canonical_key,
                    "detail": (
                        "active typed overlay row has no canonical projection "
                        "— orphaned typed row, fail closed"
                    ),
                }],
            )
        return {"id": int(row["id"]), "value": str(row["value"] or "")}

    def _cross_check_marker_ids_and_sha(
        self,
        marker: RegroupActiveMarker,
        *,
        overlay_row_id: int,
        active_marker_projection_id: Any,
        compat_ids: list[int],
        canonical_artifact_id: int,
        canonical_body_sha: str,
    ) -> None:
        """The carried P3-A cross-check: marker artifact ids + canonical sha.

        09c-1's rollback ``_cross_check_marker`` cross-checked the marker's
        overlay id/slug/row-id/feature/source-dag-key/base-DAG id+hash/
        checkpointed-group/group-offset/validation-digest + the 3 artifact KEYS,
        but NOT the marker's ``canonical_artifact_id`` / ``canonical_sha256`` /
        ``rollback_artifact_id`` — the typed :class:`RegroupOverlay` carries no
        counterpart fields, and 09c-1's rollback never re-reads the canonical
        artifact body. The resolver DOES consume the canonical artifact body, so
        it performs the FULL id/sha cross-check (doc 09 § "Persistence And
        Artifact Compatibility": "applies the overlay only when all ids,
        hashes, ... and projection links match").

        Checks:

        - ``marker.overlay_row_id`` == the typed overlay's row id.
        - ``marker.canonical_artifact_id`` == the just-loaded canonical
          ``dag-regroup:{slug}`` artifact row id, AND that id is in the typed
          row's ``compatibility_artifact_ids``.
        - ``marker.canonical_sha256`` == the SHA-256 of the loaded canonical
          artifact body (the marker provably references THIS artifact body).
        - ``marker.rollback_artifact_id`` is in the typed row's
          ``compatibility_artifact_ids``.
        - ``marker.active_marker_artifact_id`` link — the typed row's
          ``active_marker_projection_id`` is in ``compatibility_artifact_ids``
          (the activation transaction recorded all three projection ids).

        Any disagreement is ``regroup_invalid_marker_id_sha_mismatch``
        (fail-closed).
        """

        mismatches: list[dict[str, Any]] = []

        if marker.overlay_row_id != overlay_row_id:
            mismatches.append({
                "field": "overlay_row_id",
                "marker": marker.overlay_row_id,
                "expected": overlay_row_id,
            })
        # The marker's canonical artifact id must be the just-loaded canonical
        # row id (the resolver loaded the latest dag-regroup:{slug} row).
        if marker.canonical_artifact_id != canonical_artifact_id:
            mismatches.append({
                "field": "canonical_artifact_id",
                "marker": marker.canonical_artifact_id,
                "loaded_canonical_artifact_id": canonical_artifact_id,
            })
        # The marker's canonical sha must be the SHA-256 of the loaded canonical
        # artifact body — the marker provably references THIS body.
        if marker.canonical_sha256 != canonical_body_sha:
            mismatches.append({
                "field": "canonical_sha256",
                "marker": marker.canonical_sha256,
                "loaded_canonical_body_sha256": canonical_body_sha,
            })
        # The marker's canonical / rollback / active-marker artifact ids must
        # ALL be tracked in the typed row's compatibility_artifact_ids column
        # (the 09c-1 activation transaction recorded exactly those three ids).
        compat_set = set(compat_ids)
        if marker.canonical_artifact_id not in compat_set:
            mismatches.append({
                "field": "canonical_artifact_id_in_compat_ids",
                "marker_canonical_artifact_id": marker.canonical_artifact_id,
                "compatibility_artifact_ids": sorted(compat_set)[:_DETAIL_CAP],
            })
        if marker.rollback_artifact_id not in compat_set:
            mismatches.append({
                "field": "rollback_artifact_id_in_compat_ids",
                "marker_rollback_artifact_id": marker.rollback_artifact_id,
                "compatibility_artifact_ids": sorted(compat_set)[:_DETAIL_CAP],
            })
        # The typed row's active_marker_projection_id link must also be present
        # in compatibility_artifact_ids (it is one of the three ids the
        # activation transaction wrote). active_marker_projection_id is the
        # *latest* active-marker row id; after a rollback it would point at the
        # rolled_back marker, but a rollback also flips the row off `active`, so
        # for an `active` row this is the activation's active marker id.
        if active_marker_projection_id is None:
            mismatches.append({
                "field": "active_marker_projection_id",
                "detail": (
                    "active typed overlay row has a NULL "
                    "active_marker_projection_id — fail closed"
                ),
            })
        elif int(active_marker_projection_id) not in compat_set:
            mismatches.append({
                "field": "active_marker_projection_id_in_compat_ids",
                "active_marker_projection_id": int(active_marker_projection_id),
                "compatibility_artifact_ids": sorted(compat_set)[:_DETAIL_CAP],
            })

        if mismatches:
            raise _Quiesce(
                "regroup_invalid_marker_id_sha_mismatch",
                mismatches[:_DETAIL_CAP],
            )

    def _cross_check_marker_fields(
        self,
        marker: RegroupActiveMarker,
        overlay: RegroupOverlay,
        overlay_row_id: int,
    ) -> None:
        """Cross-check the marker's identity fields against the typed overlay.

        doc 09 § "Persistence And Artifact Compatibility": the resolver "applies
        the overlay only when all ids, hashes, status values, validation
        digest, group offset, and projection links match". This mirrors 09c-1's
        rollback ``_cross_check_marker`` field set (overlay id/slug/feature/
        source-dag-key/base-DAG id+hash/checkpointed-group/group-offset/
        validation-digest + the canonical / rollback / active-marker artifact
        KEYS). The artifact ids + canonical sha are cross-checked separately by
        :meth:`_cross_check_marker_ids_and_sha`. Any disagreement is
        ``regroup_invalid_marker_field_mismatch`` (fail-closed).
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
        _check(
            "base_dag_sha256", marker.base_dag_sha256, overlay.base_dag_sha256
        )
        _check(
            "checkpointed_group",
            marker.checkpointed_group,
            overlay.checkpointed_group,
        )
        _check(
            "group_idx_offset",
            marker.group_idx_offset,
            overlay.group_idx_offset,
        )
        _check(
            "validation_digest",
            marker.validation_digest,
            overlay.validation_digest,
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
            raise _Quiesce(
                "regroup_invalid_marker_field_mismatch",
                mismatches[:_DETAIL_CAP],
            )

    @staticmethod
    def _validation_quiesce_details(
        validation: OverlayValidationResult,
    ) -> list[dict[str, Any]]:
        """Bounded quiesce-detail payload for a failed re-validation.

        Carries the validator's own ``reason`` / ``failed_step`` so a reviewer
        / dashboard can see exactly which of the 13 steps rejected the active
        overlay, plus the validator's bounded ``details``.
        """

        head: dict[str, Any] = {
            "validation_reason": validation.reason,
            "failed_step": validation.failed_step,
        }
        return [head, *validation.details][:_DETAIL_CAP]
