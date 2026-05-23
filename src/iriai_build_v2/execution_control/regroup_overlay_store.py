"""Typed regroup-overlay persistence (Slice 09b).

Slice 09 generalizes the one-off ``G45-G73`` derived-DAG regroup into a
reusable typed *overlay* (``regroup_overlay.py``, Slice 09a delivered the
models). This module (``09b``) is the **store layer** for the 3 Slice-09a
schema tables:

- ``execution_regroup_overlays`` — the canonical typed regroup record. One row
  per :class:`~iriai_build_v2.workflows.develop.execution.regroup_overlay.RegroupOverlay`.
- ``execution_regroup_validations`` — typed ``validate_overlay`` attempts. One
  row per validation run; re-validating the same ``(overlay_id,
  validation_digest)`` is idempotent, a *different* digest for the same
  ``overlay_id`` is rejected fail-closed.
- ``execution_scheduler_feedback`` — typed lane/barrier sizing windows +
  recommendation payloads. Advisory evidence only.

The 13-step deterministic ``validate_overlay`` algorithm (doc 09
§ "Validation Algorithm") is Slice 09b-2; it persists *through* this store
(it loads ``source_dag_key`` and writes its validation row + compatibility
artifact atomically via :meth:`RegroupOverlayStore.record_validation`). 09b is
split out as the dependency-free foundation per the loop-discipline rule
("ONE verified sub-deliverable per iteration"); see the implementation
journal's "Slice 09b START — split decision" entry.

``RegroupOverlayStore`` is **connection-bound** and mirrors the Slice 08
``MergeQueueStore`` conventions exactly: async, real-Postgres, advisory-lock
helpers, idempotency keys, and typed transitions. ``schema.sql`` carries the 3
tables (Slice 09a); this module never alters schema.

Doc 09 references verified at file:line while writing this module:

- The 3 table column lists / constraints / indexes: ``schema.sql`` Slice-09
  section (``execution_regroup_overlays`` / ``execution_regroup_validations`` /
  ``execution_scheduler_feedback``), incl. the ``uniq_regroup_overlay_active``
  partial unique index.
- ``ExecutionControlError`` — ``execution_control/models.py:95``. (The store's
  idempotency keys are stable string compositions, mirroring
  ``merge_queue_store.idempotency_key`` — no digest helper is needed here.)
- The connection-bound async store + advisory-lock + idempotency conventions —
  ``execution_control/merge_queue_store.py`` (``MergeQueueStore``).
- ``artifacts`` table (the compatibility validation artifact target) —
  ``schema.sql:24`` (``id``, ``feature_id``, ``key``, ``value``,
  ``created_at``).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import asyncpg

from ..workflows.develop.execution.regroup_overlay import (
    RegroupOverlay,
    SchedulerFeedback,
)
from .models import ExecutionControlError

__all__ = [
    "RegroupOverlayStoreError",
    "RegroupOverlayValidationConflict",
    "OverlayValidationRecord",
    "SchedulerFeedbackRecord",
    "LoadedDagArtifact",
    "RegroupOverlayStore",
    "overlay_idempotency_key",
    "validation_idempotency_key",
    "scheduler_feedback_idempotency_key",
]


# ── Errors ──────────────────────────────────────────────────────────────────


class RegroupOverlayStoreError(ExecutionControlError):
    """Raised when a regroup-overlay store request violates an invariant."""


class RegroupOverlayValidationConflict(RegroupOverlayStoreError):
    """Raised when an overlay id is re-validated with a different digest.

    doc 09 § "Validation Algorithm" step 13: "Re-validating the same overlay id
    with the same digest is idempotent; a different digest for the same overlay
    id is rejected." This is the fail-closed rejection — a validation run whose
    ``validation_digest`` does not match the overlay's already-recorded digest
    is an evidence inconsistency, not a silent overwrite.
    """


# ── Stored (row) types ──────────────────────────────────────────────────────


class OverlayValidationRecord:
    """An ``execution_regroup_validations`` row (a ``validate_overlay`` attempt).

    Plain attribute carrier (not a Pydantic model) — it mirrors the row 1:1 and
    is never agent-facing structured output, so the flat-structured-output rule
    does not apply. ``details_json`` is the bounded validator detail payload;
    ``evidence_ids`` are the typed evidence node ids the validation cited.
    """

    __slots__ = (
        "id",
        "feature_id",
        "overlay_id",
        "overlay_row_id",
        "valid",
        "reason",
        "validation_digest",
        "details_json",
        "evidence_ids",
        "idempotency_key",
        "compatibility_artifact_id",
        "created_at",
    )

    def __init__(
        self,
        *,
        id: int,
        feature_id: str,
        overlay_id: str,
        overlay_row_id: int,
        valid: bool,
        reason: str,
        validation_digest: str,
        details_json: dict[str, Any],
        evidence_ids: list[int],
        idempotency_key: str,
        compatibility_artifact_id: int | None,
        created_at: Any,
    ) -> None:
        self.id = id
        self.feature_id = feature_id
        self.overlay_id = overlay_id
        self.overlay_row_id = overlay_row_id
        self.valid = valid
        self.reason = reason
        self.validation_digest = validation_digest
        self.details_json = details_json
        self.evidence_ids = evidence_ids
        self.idempotency_key = idempotency_key
        self.compatibility_artifact_id = compatibility_artifact_id
        self.created_at = created_at


class SchedulerFeedbackRecord:
    """An ``execution_scheduler_feedback`` row.

    Plain attribute carrier. ``payload_json`` is the full :class:`SchedulerFeedback`
    body; the scalar columns mirror the doc-09 indexed feedback fields.
    """

    __slots__ = (
        "id",
        "feedback_id",
        "feature_id",
        "window_start_group",
        "window_end_group",
        "lane",
        "barrier",
        "sample_count",
        "recommended_cap",
        "current_cap",
        "data_quality",
        "confidence",
        "metric_ids",
        "evidence_ids",
        "payload_json",
        "idempotency_key",
        "created_at",
    )

    def __init__(
        self,
        *,
        id: int,
        feedback_id: str,
        feature_id: str,
        window_start_group: int,
        window_end_group: int,
        lane: str,
        barrier: str,
        sample_count: int,
        recommended_cap: int,
        current_cap: int,
        data_quality: str,
        confidence: str,
        metric_ids: list[str],
        evidence_ids: list[int],
        payload_json: dict[str, Any],
        idempotency_key: str,
        created_at: Any,
    ) -> None:
        self.id = id
        self.feedback_id = feedback_id
        self.feature_id = feature_id
        self.window_start_group = window_start_group
        self.window_end_group = window_end_group
        self.lane = lane
        self.barrier = barrier
        self.sample_count = sample_count
        self.recommended_cap = recommended_cap
        self.current_cap = current_cap
        self.data_quality = data_quality
        self.confidence = confidence
        self.metric_ids = metric_ids
        self.evidence_ids = evidence_ids
        self.payload_json = payload_json
        self.idempotency_key = idempotency_key
        self.created_at = created_at


class LoadedDagArtifact:
    """A DAG ``artifacts`` row loaded by key, with its canonical SHA-256.

    Plain attribute carrier. ``sha256`` is ``sha256(value)`` over the *raw*
    artifact ``value`` string — the identical computation the legacy regroup
    code uses (``dag_regroup._latest_dag_record``: ``hashlib.sha256(
    value.encode("utf-8")).hexdigest()``), so a base-DAG hash projected by the
    legacy path and the hash this store computes for the same row agree
    byte-for-byte. The Slice 09b-2 validator (step 2) loads ``source_dag_key``
    through :meth:`RegroupOverlayStore.load_dag_artifact` and rejects an overlay
    unless ``(id, sha256)`` exactly match the overlay's
    ``base_dag_artifact_id`` / ``base_dag_sha256``.
    """

    __slots__ = ("id", "feature_id", "key", "value", "sha256")

    def __init__(
        self,
        *,
        id: int,
        feature_id: str,
        key: str,
        value: str,
        sha256: str,
    ) -> None:
        self.id = id
        self.feature_id = feature_id
        self.key = key
        self.value = value
        self.sha256 = sha256


# ── Idempotency-key helpers ─────────────────────────────────────────────────
#
# All three keys are deterministic (no clock / random). They mirror the
# `merge_queue_store` `idempotency_key` convention: a stable, human-legible
# prefix plus the identity fields. The overlay key carries `overlay_sha256` so
# two materially different overlays for the same `(feature, overlay_id)` would
# get distinct keys; the validation key is `(overlay_id, validation_digest)` so
# a re-validation with the same digest collides (idempotent reuse) and a
# different digest does NOT collide on the key (the store then rejects it
# fail-closed in `record_validation`).


def overlay_idempotency_key(overlay: RegroupOverlay) -> str:
    """Stable insert key for an ``execution_regroup_overlays`` row."""

    return (
        f"regroup-overlay:{overlay.feature_id}:{overlay.overlay_id}:"
        f"{overlay.overlay_sha256}"
    )


def validation_idempotency_key(
    *, feature_id: str, overlay_id: str, validation_digest: str
) -> str:
    """Stable key for an ``execution_regroup_validations`` row.

    Keyed on ``(feature_id, overlay_id, validation_digest)`` — doc 09
    § "Validation Algorithm" step 13 makes re-validating the same overlay id
    with the same digest idempotent.
    """

    return f"regroup-validation:{feature_id}:{overlay_id}:{validation_digest}"


def scheduler_feedback_idempotency_key(feedback: SchedulerFeedback) -> str:
    """Stable insert key for an ``execution_scheduler_feedback`` row."""

    return (
        f"scheduler-feedback:{feedback.feature_id}:{feedback.feedback_id}:"
        f"{feedback.window_start_group}:{feedback.window_end_group}"
    )


def _validation_artifact_key(overlay: RegroupOverlay) -> str:
    """Compatibility validation artifact key for an overlay.

    doc 09 § "Validation Algorithm" step 13 requires a "compatibility validation
    artifact" alongside the typed validation row. It is keyed per overlay slug
    so legacy readers can locate the latest validation view; the artifact body
    carries the typed row id + overlay sha so it never becomes a writer.
    """

    return f"dag-regroup-validation:{overlay.overlay_slug}"


# ── JSON helpers (mirror merge_queue_store._jsonb / _loads) ──────────────────


def _jsonb(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value


# ── Store ───────────────────────────────────────────────────────────────────


class RegroupOverlayStore:
    """Connection-bound persistence for typed regroup overlays (Slice 09b).

    Each caller holds its own store over its own asyncpg connection (the Slice
    08 ``MergeQueueStore`` model). Activation / rollback callers (Slice 09c)
    hold the feature advisory lock for the duration via
    :meth:`acquire_feature_lock` / :meth:`release_feature_lock`.
    """

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ── feature advisory lock (mirrors MergeQueueStore) ─────────────────────

    async def acquire_feature_lock(self, feature_id: str) -> None:
        """Acquire the session-level feature advisory lock.

        Session-scoped (not xact-scoped) so it can be held across the multi-step
        validation / activation / rollback flows. Must be released with
        :meth:`release_feature_lock`. Uses the same ``hashtext(feature_id)``
        lock key as :class:`~iriai_build_v2.execution_control.merge_queue_store.MergeQueueStore`
        so regroup mutation and merge-queue mutation for one feature serialize
        against the *same* advisory lock.
        """

        if not feature_id:
            raise RegroupOverlayStoreError(
                "acquire_feature_lock requires a feature_id"
            )
        await self._conn.execute(
            "SELECT pg_advisory_lock(hashtext($1))", feature_id
        )

    async def release_feature_lock(self, feature_id: str) -> None:
        """Release the session-level feature advisory lock."""

        if not feature_id:
            raise RegroupOverlayStoreError(
                "release_feature_lock requires a feature_id"
            )
        await self._conn.execute(
            "SELECT pg_advisory_unlock(hashtext($1))", feature_id
        )

    # ── execution_regroup_overlays ──────────────────────────────────────────

    async def insert_overlay(self, overlay: RegroupOverlay) -> int:
        """Idempotently insert a typed regroup overlay row. Returns the row id.

        A duplicate idempotency key (same feature/overlay_id/overlay_sha256)
        returns the existing row id — the insert is a no-op. The
        ``uniq_regroup_overlay_active`` partial unique index still rejects a
        2nd ``status='active'`` overlay per feature at the DB level; this method
        is the typical *staged*-insert entry point and does not pre-validate the
        active count (activation in Slice 09c owns that transition).
        """

        self._validate_overlay_for_insert(overlay)
        key = overlay_idempotency_key(overlay)
        payload = overlay.model_dump(mode="json")
        compatibility_artifact_ids: list[int] = []

        try:
            async with self._conn.transaction():
                existing = await self._conn.fetchval(
                    "SELECT id FROM execution_regroup_overlays "
                    "WHERE idempotency_key = $1",
                    key,
                )
                if existing is not None:
                    return int(existing)
                row_id = await self._conn.fetchval(
                    "INSERT INTO execution_regroup_overlays "
                    "(feature_id, overlay_id, overlay_slug, status, "
                    " artifact_key, source_dag_key, base_dag_artifact_id, "
                    " base_dag_sha256, checkpointed_group, group_idx_offset, "
                    " last_original_group, overlay_sha256, validation_digest, "
                    " latest_successful_validation_id, "
                    " active_marker_projection_id, payload_json, "
                    " compatibility_artifact_ids, activated_at, "
                    " rolled_back_at, idempotency_key) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,"
                    " NULL,NULL,$14::jsonb,$15::jsonb,$16,$17,$18) "
                    "RETURNING id",
                    overlay.feature_id,
                    overlay.overlay_id,
                    overlay.overlay_slug,
                    overlay.status,
                    overlay.artifact_key,
                    overlay.source_dag_key,
                    overlay.base_dag_artifact_id,
                    overlay.base_dag_sha256,
                    overlay.checkpointed_group,
                    overlay.group_idx_offset,
                    overlay.last_original_group,
                    overlay.overlay_sha256,
                    overlay.validation_digest,
                    _jsonb(payload),
                    _jsonb(compatibility_artifact_ids),
                    overlay.activated_at,
                    overlay.rolled_back_at,
                    key,
                )
                return int(row_id)
        except asyncpg.UniqueViolationError:
            # A concurrent insert created the row first. Resolve idempotently:
            # the duplicate idempotency key is the same overlay.
            existing = await self._conn.fetchval(
                "SELECT id FROM execution_regroup_overlays "
                "WHERE idempotency_key = $1",
                key,
            )
            if existing is None:  # pragma: no cover - non-idem-key race
                raise
            return int(existing)

    async def get_overlay(self, overlay_row_id: int) -> RegroupOverlay | None:
        """Load a typed overlay by its row id, or None.

        The typed model is rebuilt from ``payload_json`` (the canonical body),
        not the denormalized scalar columns — the scalar columns exist for
        indexing/constraints, ``payload_json`` is the source of truth.
        """

        row = await self._conn.fetchrow(
            "SELECT payload_json FROM execution_regroup_overlays WHERE id = $1",
            overlay_row_id,
        )
        if row is None:
            return None
        return RegroupOverlay.model_validate(_loads(row["payload_json"], {}))

    async def get_overlay_by_overlay_id(
        self, feature_id: str, overlay_id: str
    ) -> RegroupOverlay | None:
        """Load a typed overlay by ``(feature_id, overlay_id)``, or None.

        ``(feature_id, overlay_id)`` is uniquely constrained so at most one row
        matches.
        """

        row = await self._conn.fetchrow(
            "SELECT payload_json FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND overlay_id = $2",
            feature_id,
            overlay_id,
        )
        if row is None:
            return None
        return RegroupOverlay.model_validate(_loads(row["payload_json"], {}))

    async def get_overlay_row_id(
        self, feature_id: str, overlay_id: str
    ) -> int | None:
        """Return the row id for ``(feature_id, overlay_id)``, or None."""

        row = await self._conn.fetchval(
            "SELECT id FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND overlay_id = $2",
            feature_id,
            overlay_id,
        )
        return None if row is None else int(row)

    async def get_active_overlay(
        self, feature_id: str
    ) -> RegroupOverlay | None:
        """Return the single ``active`` overlay for a feature, or None.

        The ``uniq_regroup_overlay_active`` partial unique index guarantees at
        most one ``active`` row per feature, so this never has to disambiguate.
        """

        row = await self._conn.fetchrow(
            "SELECT payload_json FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND status = 'active'",
            feature_id,
        )
        if row is None:
            return None
        return RegroupOverlay.model_validate(_loads(row["payload_json"], {}))

    # ── source DAG artifact load (Slice 09b-2 validate_overlay step 2) ──────

    async def load_dag_artifact(
        self, feature_id: str, dag_key: str
    ) -> LoadedDagArtifact | None:
        """Load the latest ``artifacts`` row for ``dag_key``, or None.

        doc 09 § "Validation Algorithm" step 2: "Load ``source_dag_key`` through
        the store. Reject unless the loaded artifact id and SHA-256 exactly
        match ``base_dag_artifact_id`` and ``base_dag_sha256``." The DAG is a
        plain ``artifacts`` row (``key='dag'`` for the root DAG); the latest row
        for the key wins (highest ``id``), matching ``dag_regroup._latest_dag_
        record``. The returned :class:`LoadedDagArtifact` carries the canonical
        SHA-256 over the raw ``value``.

        This is a *read-only* helper added for the 09b-2 validator — it is
        purely additive and does not alter the 09b store contract.
        """

        if not feature_id or not dag_key:
            raise RegroupOverlayStoreError(
                "load_dag_artifact requires feature_id and dag_key"
            )
        row = await self._conn.fetchrow(
            "SELECT id, feature_id, key, value FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
            feature_id,
            dag_key,
        )
        if row is None:
            return None
        value = str(row["value"] or "")
        return LoadedDagArtifact(
            id=int(row["id"]),
            feature_id=row["feature_id"],
            key=row["key"],
            value=value,
            sha256=hashlib.sha256(value.encode("utf-8")).hexdigest(),
        )

    # ── execution_regroup_validations ───────────────────────────────────────

    async def record_validation(
        self,
        *,
        feature_id: str,
        overlay_id: str,
        overlay_row_id: int,
        valid: bool,
        validation_digest: str,
        reason: str = "",
        details: dict[str, Any] | None = None,
        evidence_ids: list[int] | None = None,
        compatibility_artifact: RegroupOverlay | None = None,
    ) -> OverlayValidationRecord:
        """Persist a ``validate_overlay`` attempt + compatibility artifact.

        doc 09 § "Validation Algorithm" step 13: "Emit a typed validation record
        and compatibility validation artifact in the same transaction.
        Re-validating the same overlay id with the same digest is idempotent; a
        different digest for the same overlay id is rejected."

        Behavior:

        - **Same ``(overlay_id, validation_digest)``** — idempotent reuse: the
          existing :class:`OverlayValidationRecord` is returned, no second row
          and no second artifact are written.
        - **Different ``validation_digest`` for an ``overlay_id`` that already
          has a recorded validation** — :class:`RegroupOverlayValidationConflict`
          (fail-closed). The overlay's ``validation_digest`` is part of its
          identity; a validation run carrying a different digest is an evidence
          inconsistency, never a silent overwrite.
        - Otherwise — a new ``execution_regroup_validations`` row plus, when
          ``compatibility_artifact`` is supplied, a ``dag-regroup-validation:*``
          ``artifacts`` row are written in **one transaction**. If the artifact
          write fails the typed validation row rolls back with it.

        ``compatibility_artifact`` is the typed overlay whose normalized
        :class:`~iriai_build_v2.models.outputs.DerivedDAGArtifact`-shaped body
        the 09b-2 validator projects; it is optional so unit tests and the
        not-yet-built validator can record a typed row without a projection.
        """

        if not feature_id or not overlay_id:
            raise RegroupOverlayStoreError(
                "record_validation requires feature_id and overlay_id"
            )
        if not validation_digest:
            raise RegroupOverlayStoreError(
                "record_validation requires a non-empty validation_digest "
                "(the digest must be deterministic — doc 09)"
            )
        details_json = dict(details or {})
        evidence_list = sorted(int(e) for e in (evidence_ids or []))
        key = validation_idempotency_key(
            feature_id=feature_id,
            overlay_id=overlay_id,
            validation_digest=validation_digest,
        )

        try:
            async with self._conn.transaction():
                # Fail-closed digest check: any prior validation row for this
                # overlay id whose digest differs is a hard reject. This is the
                # idempotency-on-(overlay_id, validation_digest) contract.
                conflicting = await self._conn.fetchrow(
                    "SELECT validation_digest FROM execution_regroup_validations "
                    "WHERE feature_id = $1 AND overlay_id = $2 "
                    "AND validation_digest <> $3 LIMIT 1",
                    feature_id,
                    overlay_id,
                    validation_digest,
                )
                if conflicting is not None:
                    raise RegroupOverlayValidationConflict(
                        f"overlay {overlay_id!r} already has a validation "
                        f"record with digest "
                        f"{conflicting['validation_digest']!r}; a different "
                        f"digest {validation_digest!r} is rejected fail-closed"
                    )

                existing = await self._load_validation_by_key(key)
                if existing is not None:
                    return existing

                artifact_id: int | None = None
                if compatibility_artifact is not None:
                    artifact_id = await self._write_validation_artifact(
                        feature_id, compatibility_artifact, overlay_row_id
                    )

                details_for_row = dict(details_json)
                if artifact_id is not None:
                    # Bounded projection-link metadata: legacy readers can find
                    # the compatibility artifact from the typed row.
                    details_for_row.setdefault(
                        "compatibility_artifact_id", artifact_id
                    )

                row_id = await self._conn.fetchval(
                    "INSERT INTO execution_regroup_validations "
                    "(feature_id, overlay_id, overlay_row_id, valid, reason, "
                    " validation_digest, details_json, evidence_ids, "
                    " idempotency_key) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9) "
                    "RETURNING id",
                    feature_id,
                    overlay_id,
                    overlay_row_id,
                    valid,
                    reason,
                    validation_digest,
                    _jsonb(details_for_row),
                    _jsonb(evidence_list),
                    key,
                )
                # When the validation passes, advance the overlay's
                # latest_successful_validation_id so activation (Slice 09c) can
                # cheaply confirm "the digest matches the latest successful
                # validation record" without re-scanning.
                if valid:
                    await self._conn.execute(
                        "UPDATE execution_regroup_overlays "
                        "SET latest_successful_validation_id = $2, "
                        "validation_digest = $3, updated_at = now() "
                        "WHERE id = $1",
                        overlay_row_id,
                        int(row_id),
                        validation_digest,
                    )
                loaded = await self._load_validation(int(row_id))
                assert loaded is not None
                return loaded
        except asyncpg.UniqueViolationError:
            # A concurrent record_validation with the same key landed first.
            existing = await self._load_validation_by_key(key)
            if existing is None:  # pragma: no cover - non-idem-key race
                raise
            return existing

    async def get_validation(
        self, validation_row_id: int
    ) -> OverlayValidationRecord | None:
        """Load an ``execution_regroup_validations`` row by id, or None."""

        return await self._load_validation(validation_row_id)

    async def latest_successful_validation(
        self, feature_id: str, overlay_id: str
    ) -> OverlayValidationRecord | None:
        """Return the most recent ``valid=true`` validation for an overlay.

        Used by Slice 09c activation to confirm the overlay's
        ``validation_digest`` matches its latest successful validation record.
        """

        row = await self._conn.fetchrow(
            "SELECT id FROM execution_regroup_validations "
            "WHERE feature_id = $1 AND overlay_id = $2 AND valid = true "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            feature_id,
            overlay_id,
        )
        if row is None:
            return None
        return await self._load_validation(int(row["id"]))

    async def list_validations(
        self, feature_id: str, overlay_id: str
    ) -> list[OverlayValidationRecord]:
        """Every validation attempt for an overlay, newest first."""

        rows = await self._conn.fetch(
            "SELECT id FROM execution_regroup_validations "
            "WHERE feature_id = $1 AND overlay_id = $2 "
            "ORDER BY created_at DESC, id DESC",
            feature_id,
            overlay_id,
        )
        records: list[OverlayValidationRecord] = []
        for row in rows:
            loaded = await self._load_validation(int(row["id"]))
            if loaded is not None:
                records.append(loaded)
        return records

    # ── execution_scheduler_feedback ────────────────────────────────────────

    async def insert_scheduler_feedback(
        self, feedback: SchedulerFeedback
    ) -> SchedulerFeedbackRecord:
        """Idempotently insert a typed scheduler-feedback row.

        A duplicate idempotency key (same feature/feedback_id/window) returns
        the existing record. Scheduler feedback is advisory evidence only — this
        store never writes a ``dag-regroup-active:*`` marker (doc 09).
        """

        if not feedback.feature_id or not feedback.feedback_id:
            raise RegroupOverlayStoreError(
                "scheduler feedback requires feature_id and feedback_id"
            )
        key = scheduler_feedback_idempotency_key(feedback)
        payload = feedback.model_dump(mode="json")

        try:
            async with self._conn.transaction():
                existing = await self._load_feedback_by_key(key)
                if existing is not None:
                    return existing
                row_id = await self._conn.fetchval(
                    "INSERT INTO execution_scheduler_feedback "
                    "(feedback_id, feature_id, window_start_group, "
                    " window_end_group, lane, barrier, sample_count, "
                    " recommended_cap, current_cap, data_quality, confidence, "
                    " metric_ids, evidence_ids, payload_json, idempotency_key) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,"
                    " $12::jsonb,$13::jsonb,$14::jsonb,$15) RETURNING id",
                    feedback.feedback_id,
                    feedback.feature_id,
                    feedback.window_start_group,
                    feedback.window_end_group,
                    feedback.lane,
                    feedback.barrier,
                    feedback.sample_count,
                    feedback.recommended_cap,
                    feedback.current_cap,
                    feedback.data_quality,
                    feedback.confidence,
                    _jsonb([str(m) for m in feedback.metric_ids]),
                    _jsonb(sorted(int(e) for e in feedback.evidence_ids)),
                    _jsonb(payload),
                    key,
                )
                loaded = await self._load_feedback(int(row_id))
                assert loaded is not None
                return loaded
        except asyncpg.UniqueViolationError:
            existing = await self._load_feedback_by_key(key)
            if existing is None:  # pragma: no cover - non-idem-key race
                raise
            return existing

    async def get_scheduler_feedback(
        self, feedback_row_id: int
    ) -> SchedulerFeedbackRecord | None:
        """Load an ``execution_scheduler_feedback`` row by id, or None."""

        return await self._load_feedback(feedback_row_id)

    async def list_scheduler_feedback(
        self, feature_id: str, *, lane: str | None = None,
        barrier: str | None = None,
    ) -> list[SchedulerFeedbackRecord]:
        """Scheduler-feedback rows for a feature, newest first.

        Optionally filtered by ``lane`` and/or ``barrier`` — the
        ``idx_scheduler_feedback_lane_barrier`` index serves this read.
        """

        clauses = ["feature_id = $1"]
        params: list[Any] = [feature_id]
        if lane is not None:
            params.append(lane)
            clauses.append(f"lane = ${len(params)}")
        if barrier is not None:
            params.append(barrier)
            clauses.append(f"barrier = ${len(params)}")
        rows = await self._conn.fetch(
            "SELECT id FROM execution_scheduler_feedback "
            f"WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, id DESC",
            *params,
        )
        records: list[SchedulerFeedbackRecord] = []
        for row in rows:
            loaded = await self._load_feedback(int(row["id"]))
            if loaded is not None:
                records.append(loaded)
        return records

    # ── validation ──────────────────────────────────────────────────────────

    def _validate_overlay_for_insert(self, overlay: RegroupOverlay) -> None:
        """Reject an overlay row that cannot satisfy the schema invariants.

        These are fail-fast guards (a clear error, not a cryptic schema CHECK or
        FK violation) for the few cross-field constraints the typed model alone
        does not enforce.
        """

        if not overlay.feature_id:
            raise RegroupOverlayStoreError("overlay requires a feature_id")
        if not overlay.overlay_id:
            raise RegroupOverlayStoreError("overlay requires an overlay_id")
        if not overlay.overlay_slug:
            raise RegroupOverlayStoreError("overlay requires an overlay_slug")
        if not overlay.overlay_sha256:
            raise RegroupOverlayStoreError("overlay requires an overlay_sha256")
        if not overlay.validation_digest:
            raise RegroupOverlayStoreError(
                "overlay requires a validation_digest"
            )
        if not overlay.source_dag_key:
            raise RegroupOverlayStoreError("overlay requires a source_dag_key")
        if not overlay.artifact_key:
            raise RegroupOverlayStoreError("overlay requires an artifact_key")
        if not overlay.base_dag_sha256:
            raise RegroupOverlayStoreError("overlay requires a base_dag_sha256")

    # ── load helpers ────────────────────────────────────────────────────────

    async def _load_validation(
        self, validation_row_id: int
    ) -> OverlayValidationRecord | None:
        row = await self._conn.fetchrow(
            "SELECT id, feature_id, overlay_id, overlay_row_id, valid, "
            "reason, validation_digest, details_json, evidence_ids, "
            "idempotency_key, created_at "
            "FROM execution_regroup_validations WHERE id = $1",
            validation_row_id,
        )
        return None if row is None else self._validation_from_row(row)

    async def _load_validation_by_key(
        self, key: str
    ) -> OverlayValidationRecord | None:
        row = await self._conn.fetchrow(
            "SELECT id, feature_id, overlay_id, overlay_row_id, valid, "
            "reason, validation_digest, details_json, evidence_ids, "
            "idempotency_key, created_at "
            "FROM execution_regroup_validations WHERE idempotency_key = $1",
            key,
        )
        return None if row is None else self._validation_from_row(row)

    @staticmethod
    def _validation_from_row(row: Any) -> OverlayValidationRecord:
        details = _loads(row["details_json"], {})
        return OverlayValidationRecord(
            id=int(row["id"]),
            feature_id=row["feature_id"],
            overlay_id=row["overlay_id"],
            overlay_row_id=int(row["overlay_row_id"]),
            valid=bool(row["valid"]),
            reason=row["reason"] or "",
            validation_digest=row["validation_digest"],
            details_json=details,
            evidence_ids=[int(e) for e in _loads(row["evidence_ids"], [])],
            idempotency_key=row["idempotency_key"],
            compatibility_artifact_id=(
                int(details["compatibility_artifact_id"])
                if isinstance(details, dict)
                and details.get("compatibility_artifact_id") is not None
                else None
            ),
            created_at=row["created_at"],
        )

    async def _load_feedback(
        self, feedback_row_id: int
    ) -> SchedulerFeedbackRecord | None:
        row = await self._conn.fetchrow(
            "SELECT id, feedback_id, feature_id, window_start_group, "
            "window_end_group, lane, barrier, sample_count, recommended_cap, "
            "current_cap, data_quality, confidence, metric_ids, evidence_ids, "
            "payload_json, idempotency_key, created_at "
            "FROM execution_scheduler_feedback WHERE id = $1",
            feedback_row_id,
        )
        return None if row is None else self._feedback_from_row(row)

    async def _load_feedback_by_key(
        self, key: str
    ) -> SchedulerFeedbackRecord | None:
        row = await self._conn.fetchrow(
            "SELECT id, feedback_id, feature_id, window_start_group, "
            "window_end_group, lane, barrier, sample_count, recommended_cap, "
            "current_cap, data_quality, confidence, metric_ids, evidence_ids, "
            "payload_json, idempotency_key, created_at "
            "FROM execution_scheduler_feedback WHERE idempotency_key = $1",
            key,
        )
        return None if row is None else self._feedback_from_row(row)

    @staticmethod
    def _feedback_from_row(row: Any) -> SchedulerFeedbackRecord:
        return SchedulerFeedbackRecord(
            id=int(row["id"]),
            feedback_id=row["feedback_id"],
            feature_id=row["feature_id"],
            window_start_group=int(row["window_start_group"]),
            window_end_group=int(row["window_end_group"]),
            lane=row["lane"],
            barrier=row["barrier"],
            sample_count=int(row["sample_count"]),
            recommended_cap=int(row["recommended_cap"]),
            current_cap=int(row["current_cap"]),
            data_quality=row["data_quality"],
            confidence=row["confidence"],
            metric_ids=[str(m) for m in _loads(row["metric_ids"], [])],
            evidence_ids=[int(e) for e in _loads(row["evidence_ids"], [])],
            payload_json=_loads(row["payload_json"], {}),
            idempotency_key=row["idempotency_key"],
            created_at=row["created_at"],
        )

    # ── compatibility artifact write ────────────────────────────────────────

    async def _write_validation_artifact(
        self,
        feature_id: str,
        overlay: RegroupOverlay,
        overlay_row_id: int,
    ) -> int:
        """Write (or reuse) the ``dag-regroup-validation:*`` compatibility row.

        doc 09 § "Persistence And Artifact Compatibility": "Projection payloads
        include typed row ids and overlay sha so legacy readers can report
        precise evidence without becoming writers." The artifact body carries
        the overlay sha + typed overlay row id alongside the projected overlay
        payload.

        Idempotent on identical body: an existing ``artifacts`` row with the
        same ``(feature_id, key, value)`` is reused (mirrors the store's
        ``_insert_or_reuse_compatibility_artifact`` convention). This write
        happens inside the caller's ``record_validation`` transaction, so a
        failure here rolls the typed validation row back too.
        """

        key = _validation_artifact_key(overlay)
        body = {
            "kind": "regroup_overlay_validation",
            "overlay_id": overlay.overlay_id,
            "overlay_slug": overlay.overlay_slug,
            "overlay_sha256": overlay.overlay_sha256,
            "validation_digest": overlay.validation_digest,
            "typed_overlay_row_id": overlay_row_id,
            "overlay": overlay.model_dump(mode="json"),
        }
        value = _jsonb(body)
        existing = await self._conn.fetchval(
            "SELECT id FROM artifacts "
            "WHERE feature_id = $1 AND key = $2 AND value = $3 "
            "ORDER BY id DESC LIMIT 1",
            feature_id,
            key,
            value,
        )
        if existing is not None:
            return int(existing)
        artifact_id = await self._conn.fetchval(
            "INSERT INTO artifacts (feature_id, key, value) "
            "VALUES ($1, $2, $3) RETURNING id",
            feature_id,
            key,
            value,
        )
        return int(artifact_id)
