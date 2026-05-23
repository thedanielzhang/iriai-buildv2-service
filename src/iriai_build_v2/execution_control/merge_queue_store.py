"""Durable merge queue persistence (Slice 08c).

The merge queue is the only canonical product-mutation path. This module owns
the typed queue rows: ``merge_queue_items`` (the lane state machine) plus the
``merge_queue_task_coverage`` and ``merge_queue_repo_targets`` child ledgers.

``MergeQueueStore`` is connection-bound — each worker holds its own store over
its own asyncpg connection, so concurrency is modelled as separate stores on
separate connections (the lease/claim fencing lives in later 08c sub-steps).

08c-2 added the queue models and transactional ``enqueue`` (none -> ``queued``);
08c-3 added the lease layer (``claim`` / ``heartbeat``); 08c-4 added
``recover_expired`` and the lease-fenced ``transition``. Queue evidence and the
checkpoint projection land in a later 08c iteration.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, Field

from .models import ExecutionControlError, IdempotencyConflict, stable_digest

MergeQueueStatus = Literal[
    "queued",
    "leased",
    "applying",
    "verifying",
    "committing",
    "integrated",
    "checkpointing",
    "done",
    "failed",
    "poisoned",
    "cancelled",
]

RepoTargetStatus = Literal[
    "pending",
    "pre_apply_recorded",
    "applied",
    "committed",
    "clean",
    "failed",
    "poisoned",
]

_TERMINAL_STATUSES = frozenset({"done", "failed", "poisoned", "cancelled"})


class MergeQueueError(ExecutionControlError):
    """Raised when an enqueue request violates a queue invariant."""


class LeaseFencedError(MergeQueueError):
    """Raised when a lease-fenced operation affects zero rows.

    The worker's ``(item_id, lease_owner, lease_version)`` is no longer current
    — a newer lease was granted or the row is terminal. The worker must stop.
    """


DEFAULT_LEASE_TTL_SECONDS = 300

# After this many expired-active recoveries of one row, recovery poisons it.
MAX_RECOVERIES = 3
_MAX_RECOVERY_HISTORY = 12

# Lease-fenced worker transitions. The forward integrated -> checkpointing ->
# done transitions are owned by the group coordinator (feature-lock fenced),
# not a worker lease, and are not in this table.
#
# `checkpointing` carries ONLY the two terminal failure transitions
# (`-> failed` / `-> poisoned`). The doc 08 "Rollback And Recovery Table"
# permits a crashed `checkpointing` lane to terminate at `done` (rerun the
# idempotent group checkpoint) OR `poisoned`; `done` is reached exclusively via
# the coordinator-owned `complete_checkpoint` (status IN
# ('integrated','checkpointing')), never a lease-fenced `transition`. Admitting
# `checkpointing -> failed`/`poisoned` here is what lets crash recovery drive a
# group whose coordinator-rerun cannot complete to a deterministic terminal
# instead of silently raising `MergeQueueError` and re-recovering the row every
# drain pass until `MAX_RECOVERIES` poisons it.
_LEASE_TRANSITIONS: dict[str, frozenset[str]] = {
    "leased": frozenset({"applying", "failed", "poisoned", "cancelled"}),
    "applying": frozenset({"verifying", "failed", "poisoned"}),
    "verifying": frozenset({"committing", "failed", "poisoned"}),
    "committing": frozenset({"integrated", "failed", "poisoned"}),
    "checkpointing": frozenset({"failed", "poisoned"}),
}
_RECOVERABLE_STATUSES = ("applying", "verifying", "committing", "checkpointing")

# merge_queue_repo_targets advance monotonically through their own status
# machine (doc 08): pending -> pre_apply_recorded -> applied -> committed
# -> clean, or to failed/poisoned.
_REPO_TARGET_TRANSITIONS: dict[str, frozenset[str]] = {
    "pending": frozenset({"pre_apply_recorded", "failed", "poisoned"}),
    "pre_apply_recorded": frozenset({"applied", "failed", "poisoned"}),
    "applied": frozenset({"committed", "failed", "poisoned"}),
    "committed": frozenset({"clean", "failed", "poisoned"}),
}


# ── Create (input) types ────────────────────────────────────────────────────


class TaskCoverageCreate(BaseModel):
    task_id: str
    contract_id: int


class RepoTargetCreate(BaseModel):
    repo_id: str
    repo_path: str
    base_commit: str
    expected_head: str = ""


class MergeQueueItemCreate(BaseModel):
    feature_id: str
    dag_sha256: str
    group_idx: int
    base_commit: str
    repo_id: str = ""
    repo_path: str = ""
    head_commit: str = ""
    attempt_id: int | None = None
    contract_ids: list[int] = Field(default_factory=list)
    patch_evidence_ids: list[int] = Field(default_factory=list)
    gate_evidence_ids: list[int] = Field(default_factory=list)
    pre_queue_gate_evidence_id: int | None = None
    priority: int = 100
    integration_lane: str = "group"
    retry_of_queue_item_id: int | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    task_coverage: list[TaskCoverageCreate] = Field(default_factory=list)
    repo_targets: list[RepoTargetCreate] = Field(default_factory=list)


# ── Stored (row) types ──────────────────────────────────────────────────────


class MergeQueueTaskCoverage(BaseModel):
    id: int
    queue_item_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    task_id: str
    contract_id: int
    coverage_digest: str
    idempotency_key: str


class MergeQueueRepoTarget(BaseModel):
    id: int
    queue_item_id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    repo_id: str
    repo_path: str
    base_commit: str
    expected_head: str = ""
    pre_apply_head: str = ""
    applied_head: str = ""
    result_commit: str = ""
    tree_sha: str = ""
    no_dirty_snapshot_id: int | None = None
    status: RepoTargetStatus
    target_digest: str
    idempotency_key: str


class MergeQueueItem(BaseModel):
    id: int
    feature_id: str
    dag_sha256: str
    group_idx: int
    repo_id: str = ""
    repo_path: str = ""
    attempt_id: int | None = None
    contract_ids: list[int] = Field(default_factory=list)
    patch_evidence_ids: list[int] = Field(default_factory=list)
    gate_evidence_ids: list[int] = Field(default_factory=list)
    pre_queue_gate_evidence_id: int | None = None
    post_apply_gate_evidence_id: int | None = None
    base_commit: str
    head_commit: str = ""
    status: MergeQueueStatus
    priority: int = 100
    lease_owner: str | None = None
    leased_until: datetime | None = None
    lease_version: int = 0
    result_commit: str = ""
    merge_proof_evidence_id: int | None = None
    commit_proof_evidence_id: int | None = None
    checkpoint_gate_evidence_id: int | None = None
    checkpoint_evidence_id: int | None = None
    checkpoint_projection_id: int | None = None
    checkpoint_coverage_digest: str = ""
    checkpoint_body_sha256: str = ""
    retry_of_queue_item_id: int | None = None
    failure_id: int | None = None
    request_digest: str
    idempotency_key: str
    payload: dict[str, Any] = Field(default_factory=dict)
    task_coverage: list[MergeQueueTaskCoverage] = Field(default_factory=list)
    repo_targets: list[MergeQueueRepoTarget] = Field(default_factory=list)


class MergeProof(BaseModel):
    """Apply-step proof recorded before a queue item enters ``verifying``."""

    base_commit: str
    pre_apply_heads: dict[str, str] = Field(default_factory=dict)
    applied_heads: dict[str, str] = Field(default_factory=dict)
    patch_digest: str = ""
    patch_path_set: list[str] = Field(default_factory=list)
    rebased: bool = False
    tree_shas: dict[str, str] = Field(default_factory=dict)


class RepoCommitProof(BaseModel):
    """Per-repo commit + no-dirty proof recorded before ``integrated``."""

    repo_id: str
    repo_path: str
    pre_apply_head: str
    applied_head: str
    result_commit: str
    tree_sha: str
    changed_paths: list[str] = Field(default_factory=list)
    status_before: str = ""
    status_after: str = ""
    no_dirty_snapshot_id: int


# ── Digest / key helpers ────────────────────────────────────────────────────


def _task_ids_digest(task_ids: list[str]) -> str:
    return stable_digest(sorted(task_ids))


def request_digest(create: MergeQueueItemCreate) -> str:
    """Stable digest over the full normalized enqueue request.

    Duplicate enqueue compares this digest, never the raw payload JSON.
    """

    return stable_digest(
        {
            "feature_id": create.feature_id,
            "dag_sha256": create.dag_sha256,
            "group_idx": create.group_idx,
            "repo_id": create.repo_id,
            "repo_path": create.repo_path,
            "base_commit": create.base_commit,
            "head_commit": create.head_commit,
            "integration_lane": create.integration_lane,
            "attempt_id": create.attempt_id,
            "priority": create.priority,
            "pre_queue_gate_evidence_id": create.pre_queue_gate_evidence_id,
            "retry_of_queue_item_id": create.retry_of_queue_item_id,
            "contract_ids": sorted(create.contract_ids),
            "patch_evidence_ids": sorted(create.patch_evidence_ids),
            "gate_evidence_ids": sorted(create.gate_evidence_ids),
            "task_coverage": sorted(
                (c.task_id, c.contract_id) for c in create.task_coverage
            ),
            "repo_targets": sorted(
                (t.repo_id, t.repo_path, t.base_commit, t.expected_head)
                for t in create.repo_targets
            ),
            "payload": create.payload,
        }
    )


def idempotency_key(create: MergeQueueItemCreate) -> str:
    """Stable enqueue key.

    Intentionally coarser than :func:`request_digest`: two materially different
    requests may share a key, but enqueue then compares the full request digest
    and raises ``IdempotencyConflict`` on a mismatch, so the coarse key never
    causes a silent collision.
    """

    task_digest = _task_ids_digest([c.task_id for c in create.task_coverage])
    head = create.head_commit or str(create.payload.get("patch_digest", ""))
    return (
        f"merge:{create.feature_id}:{create.dag_sha256}:g{create.group_idx}:"
        f"{create.integration_lane}:{task_digest}:{create.repo_id}:"
        f"{create.base_commit}:{head}"
    )


def _coverage_digest(
    queue_item_id: int,
    feature_id: str,
    dag_sha256: str,
    group_idx: int,
    task_id: str,
    contract_id: int,
) -> str:
    return stable_digest(
        [queue_item_id, feature_id, dag_sha256, group_idx, task_id, contract_id]
    )


def _target_digest(
    queue_item_id: int,
    feature_id: str,
    dag_sha256: str,
    group_idx: int,
    target: RepoTargetCreate,
) -> str:
    return stable_digest(
        [
            queue_item_id,
            feature_id,
            dag_sha256,
            group_idx,
            target.repo_id,
            target.repo_path,
            target.base_commit,
            target.expected_head,
        ]
    )


def _jsonb(value: Any) -> str:
    return json.dumps(value)


def _loads(value: Any, default: Any) -> Any:
    if value is None:
        return default
    if isinstance(value, (str, bytes)):
        return json.loads(value)
    return value


# ── Store ───────────────────────────────────────────────────────────────────


_ITEM_COLUMNS = (
    "id, feature_id, dag_sha256, group_idx, repo_id, repo_path, attempt_id, "
    "contract_ids, patch_evidence_ids, gate_evidence_ids, "
    "pre_queue_gate_evidence_id, post_apply_gate_evidence_id, base_commit, "
    "head_commit, status, priority, lease_owner, leased_until, lease_version, "
    "result_commit, merge_proof_evidence_id, commit_proof_evidence_id, "
    "checkpoint_gate_evidence_id, checkpoint_evidence_id, "
    "checkpoint_projection_id, checkpoint_coverage_digest, "
    "checkpoint_body_sha256, retry_of_queue_item_id, failure_id, "
    "request_digest, idempotency_key, payload"
)


class MergeQueueStore:
    """Connection-bound persistence for the durable merge queue."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def enqueue(self, create: MergeQueueItemCreate) -> MergeQueueItem:
        """Idempotently enqueue an integration lane (none -> ``queued``).

        Inserts the parent ``merge_queue_items`` row plus its
        ``merge_queue_task_coverage`` and ``merge_queue_repo_targets`` rows in
        one transaction. A duplicate idempotency key with a matching
        ``request_digest`` returns the existing item; a mismatch raises
        :class:`IdempotencyConflict`.
        """

        self._validate_create(create)
        digest = request_digest(create)
        key = idempotency_key(create)

        try:
            async with self._conn.transaction():
                resolved = await self._resolve_existing(key, digest)
                if resolved is not None:
                    return resolved

                await self._validate_contracts(create)
                await self._reject_competing_lanes(create, key)
                if create.retry_of_queue_item_id is not None:
                    await self._validate_retry_source(create)

                item_id = await self._insert_item(create, digest, key)
                await self._insert_coverage(item_id, create)
                await self._insert_repo_targets(item_id, create)
                loaded = await self._load(item_id)
                assert loaded is not None
                return loaded
        except asyncpg.UniqueViolationError:
            # A concurrent enqueue created the row first. Resolve idempotently
            # in a fresh query: an identical request returns the existing item;
            # only a different digest is an IdempotencyConflict.
            resolved = await self._resolve_existing(key, digest)
            if resolved is None:  # pragma: no cover - non-idempotency-key race
                raise
            return resolved

    async def _resolve_existing(
        self, key: str, digest: str
    ) -> MergeQueueItem | None:
        """Return the existing item for an idempotency key, or None.

        A matching ``request_digest`` is an idempotent hit; a mismatch raises
        :class:`IdempotencyConflict`.
        """

        existing = await self._conn.fetchrow(
            "SELECT id, request_digest FROM merge_queue_items "
            "WHERE idempotency_key = $1",
            key,
        )
        if existing is None:
            return None
        if existing["request_digest"] != digest:
            raise IdempotencyConflict(
                f"merge queue idempotency key {key!r} reused with a "
                f"different request digest"
            )
        loaded = await self._load(existing["id"])
        assert loaded is not None
        return loaded

    async def get(self, item_id: int) -> MergeQueueItem | None:
        return await self._load(item_id)

    async def list_group_items(
        self, feature_id: str, dag_sha256: str, group_idx: int
    ) -> list[MergeQueueItem]:
        """Every queue lane for one ``(feature, dag, group)``, ordered by id.

        Used by the group merge coordinator to compute checkpoint coverage.
        """

        rows = await self._conn.fetch(
            "SELECT id FROM merge_queue_items "
            "WHERE feature_id = $1 AND dag_sha256 = $2 AND group_idx = $3 "
            "ORDER BY id",
            feature_id,
            dag_sha256,
            group_idx,
        )
        items: list[MergeQueueItem] = []
        for row in rows:
            loaded = await self._load(row["id"])
            if loaded is not None:
                items.append(loaded)
        return items

    async def complete_checkpoint(
        self,
        queue_item_ids: list[int],
        *,
        checkpoint_gate_evidence_id: int,
        checkpoint_evidence_id: int,
        checkpoint_projection_id: int,
        checkpoint_coverage_digest: str,
        checkpoint_body_sha256: str,
    ) -> list[int]:
        """Advance covered lanes integrated/checkpointing -> ``done``.

        One transaction sets the checkpoint columns on every covered lane and
        clears its lease fields. Idempotent: a lane already ``done`` is not in
        the ``integrated``/``checkpointing`` claim set, so a re-run advances
        nothing and returns ``[]``. The group merge coordinator owns the
        feature advisory lock for the duration. Returns the ids advanced.
        """

        if not queue_item_ids:
            raise MergeQueueError("complete_checkpoint requires queue item ids")
        if not checkpoint_coverage_digest or not checkpoint_body_sha256:
            raise MergeQueueError(
                "complete_checkpoint requires non-empty coverage and body "
                "digests"
            )
        async with self._conn.transaction():
            rows = await self._conn.fetch(
                "UPDATE merge_queue_items SET status = 'done', "
                "checkpoint_gate_evidence_id = $2, "
                "checkpoint_evidence_id = $3, checkpoint_projection_id = $4, "
                "checkpoint_coverage_digest = $5, "
                "checkpoint_body_sha256 = $6, lease_owner = NULL, "
                "leased_until = NULL, updated_at = now() "
                "WHERE id = ANY($1::bigint[]) "
                "AND status IN ('integrated', 'checkpointing') "
                "RETURNING id",
                queue_item_ids,
                checkpoint_gate_evidence_id,
                checkpoint_evidence_id,
                checkpoint_projection_id,
                checkpoint_coverage_digest,
                checkpoint_body_sha256,
            )
        return sorted(int(row["id"]) for row in rows)

    async def acquire_feature_lock(self, feature_id: str) -> None:
        """Acquire the session-level feature advisory lock.

        Session-scoped (not xact-scoped) so it can be held across git mutation,
        commit, and checkpoint. It serializes canonical repo mutation for one
        feature. Must be released with :meth:`release_feature_lock`.
        """

        await self._conn.execute(
            "SELECT pg_advisory_lock(hashtext($1))", feature_id
        )

    async def release_feature_lock(self, feature_id: str) -> None:
        """Release the session-level feature advisory lock."""

        await self._conn.execute(
            "SELECT pg_advisory_unlock(hashtext($1))", feature_id
        )

    async def claim(
        self,
        feature_id: str,
        lease_owner: str,
        *,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> MergeQueueItem | None:
        """Atomically claim one claimable lane for *feature_id*.

        A single ``UPDATE ... WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED
        LIMIT 1)`` so two workers never claim the same row. Claimable means
        ``queued`` or a ``leased`` row whose lease has expired. The claimed row
        gets a bumped ``lease_version`` (the fencing token). Returns the claimed
        item, or None when nothing is claimable.
        """

        row = await self._conn.fetchrow(
            "UPDATE merge_queue_items SET "
            "status = 'leased', lease_owner = $2, "
            "leased_until = now() + make_interval(secs => $3), "
            "lease_version = lease_version + 1, updated_at = now() "
            "WHERE id IN ("
            "  SELECT id FROM merge_queue_items "
            "  WHERE feature_id = $1 "
            "  AND (status = 'queued' "
            "       OR (status = 'leased' AND leased_until < now())) "
            "  ORDER BY priority, id "
            "  FOR UPDATE SKIP LOCKED LIMIT 1"
            ") RETURNING id",
            feature_id,
            lease_owner,
            ttl_seconds,
        )
        if row is None:
            return None
        return await self._load(row["id"])

    async def heartbeat(
        self,
        item_id: int,
        lease_owner: str,
        lease_version: int,
        *,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> MergeQueueItem:
        """Extend the lease deadline for a still-owned, non-terminal lane.

        Fenced: the update matches only when ``(id, lease_owner, lease_version)``
        is still current and the row is non-terminal. Zero rows affected raises
        :class:`LeaseFencedError`. ``lease_version`` is not bumped — the worker
        keeps its fencing token across heartbeats.
        """

        row = await self._conn.fetchrow(
            "UPDATE merge_queue_items SET "
            "leased_until = now() + make_interval(secs => $4), "
            "updated_at = now() "
            "WHERE id = $1 AND lease_owner = $2 AND lease_version = $3 "
            "AND status NOT IN ('done', 'failed', 'poisoned', 'cancelled') "
            "RETURNING id",
            item_id,
            lease_owner,
            lease_version,
            ttl_seconds,
        )
        if row is None:
            raise LeaseFencedError(
                f"heartbeat for queue item {item_id} is fenced — "
                f"(owner {lease_owner!r}, lease_version {lease_version}) is no "
                f"longer current or the row is terminal"
            )
        loaded = await self._load(item_id)
        assert loaded is not None
        return loaded

    async def recover_expired(
        self,
        feature_id: str,
        lease_owner: str,
        *,
        ttl_seconds: int = DEFAULT_LEASE_TTL_SECONDS,
    ) -> MergeQueueItem | None:
        """Recover one expired in-flight lane under the feature advisory lock.

        Normal :meth:`claim` never takes an ``applying``/``verifying``/
        ``committing``/``checkpointing`` row — once canonical mutation has
        started, only this method may take over. It acquires the feature
        advisory lock, picks one expired in-flight row, bumps ``lease_version``,
        and records the recovery in ``payload``. After :data:`MAX_RECOVERIES`
        recoveries the row is poisoned instead. Returns the recovered (or
        poisoned) item, or None when nothing needs recovery.
        """

        async with self._conn.transaction():
            await self._conn.execute(
                "SELECT pg_advisory_xact_lock(hashtext($1))", feature_id
            )
            statuses = list(_RECOVERABLE_STATUSES)
            row = await self._conn.fetchrow(
                "SELECT id, status, lease_owner, leased_until, payload "
                "FROM merge_queue_items "
                "WHERE feature_id = $1 AND status = ANY($2::text[]) "
                "AND leased_until < now() "
                "ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1",
                feature_id,
                statuses,
            )
            if row is None:
                return None

            payload = _loads(row["payload"], {})
            recovery_count = int(payload.get("recovery_count", 0) or 0)
            history = list(payload.get("recovery_history", []) or [])
            history.append(
                {
                    "lease_owner": row["lease_owner"],
                    "leased_until": (
                        row["leased_until"].isoformat()
                        if row["leased_until"] is not None
                        else None
                    ),
                    "recovered_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            payload["recovery_history"] = history[-_MAX_RECOVERY_HISTORY:]

            if recovery_count >= MAX_RECOVERIES:
                # Count this attempt too, so recovery_count and recovery_history
                # stay consistent on the terminal row.
                payload["recovery_count"] = recovery_count + 1
                payload.setdefault("poison_reason", "recovery limit exceeded")
                await self._conn.execute(
                    "UPDATE merge_queue_items SET status = 'poisoned', "
                    "payload = $2::jsonb, updated_at = now() WHERE id = $1",
                    row["id"],
                    _jsonb(payload),
                )
            else:
                payload["recovery_count"] = recovery_count + 1
                await self._conn.execute(
                    "UPDATE merge_queue_items SET lease_owner = $2, "
                    "leased_until = now() + make_interval(secs => $3), "
                    "lease_version = lease_version + 1, payload = $4::jsonb, "
                    "updated_at = now() WHERE id = $1",
                    row["id"],
                    lease_owner,
                    ttl_seconds,
                    _jsonb(payload),
                )

        loaded = await self._load(row["id"])
        assert loaded is not None
        return loaded

    async def transition(
        self,
        item_id: int,
        lease_owner: str,
        lease_version: int,
        to_status: MergeQueueStatus,
        *,
        merge_proof_evidence_id: int | None = None,
        post_apply_gate_evidence_id: int | None = None,
        commit_proof_evidence_id: int | None = None,
        result_commit: str | None = None,
        failure_id: int | None = None,
        last_error: str | None = None,
    ) -> MergeQueueItem:
        """Lease-fenced worker status transition.

        The change is gated on ``(id, lease_owner, lease_version)`` — a fenced
        worker (stale lease) affects zero rows and gets :class:`LeaseFencedError`.
        Only transitions in :data:`_LEASE_TRANSITIONS` are allowed, and the proof
        evidence each target status requires must be supplied (a clear error,
        not a cryptic schema CHECK failure).
        """

        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                "SELECT status, payload FROM merge_queue_items "
                "WHERE id = $1 AND lease_owner = $2 AND lease_version = $3 "
                "FOR UPDATE",
                item_id,
                lease_owner,
                lease_version,
            )
            if row is None:
                raise LeaseFencedError(
                    f"transition for queue item {item_id} is fenced — "
                    f"(owner {lease_owner!r}, lease_version {lease_version}) is "
                    f"not current"
                )
            from_status = row["status"]
            if to_status not in _LEASE_TRANSITIONS.get(from_status, frozenset()):
                raise MergeQueueError(
                    f"transition {from_status!r} -> {to_status!r} is not an "
                    f"allowed lease-fenced transition"
                )
            if to_status == "verifying" and merge_proof_evidence_id is None:
                raise MergeQueueError(
                    "transition to verifying requires merge_proof_evidence_id"
                )
            if (
                to_status == "committing"
                and post_apply_gate_evidence_id is None
            ):
                raise MergeQueueError(
                    "transition to committing requires "
                    "post_apply_gate_evidence_id"
                )
            if to_status == "integrated" and (
                commit_proof_evidence_id is None or not result_commit
            ):
                raise MergeQueueError(
                    "transition to integrated requires commit_proof_evidence_id "
                    "and result_commit"
                )

            sets = ["status = $2", "updated_at = now()"]
            params: list[Any] = [item_id, to_status]

            def _set(column: str, value: Any) -> None:
                params.append(value)
                sets.append(f"{column} = ${len(params)}")

            if merge_proof_evidence_id is not None:
                _set("merge_proof_evidence_id", merge_proof_evidence_id)
            if post_apply_gate_evidence_id is not None:
                _set("post_apply_gate_evidence_id", post_apply_gate_evidence_id)
            if commit_proof_evidence_id is not None:
                _set("commit_proof_evidence_id", commit_proof_evidence_id)
            if result_commit is not None:
                _set("result_commit", result_commit)
            if failure_id is not None:
                _set("failure_id", failure_id)
            if last_error is not None:
                payload = _loads(row["payload"], {})
                payload["last_error"] = last_error
                params.append(_jsonb(payload))
                sets.append(f"payload = ${len(params)}::jsonb")

            await self._conn.execute(
                f"UPDATE merge_queue_items SET {', '.join(sets)} WHERE id = $1",
                *params,
            )

        loaded = await self._load(item_id)
        assert loaded is not None
        return loaded

    async def record_merge_proof(
        self,
        queue_item_id: int,
        *,
        feature_id: str,
        group_idx: int,
        proof: MergeProof,
    ) -> int:
        """Persist apply-step proof as an ``evidence_nodes`` row.

        Returns the evidence id the worker passes to
        ``transition(..., 'verifying', merge_proof_evidence_id=...)``. Idempotent
        on identical proof content.
        """

        return await self._record_proof_evidence(
            queue_item_id,
            feature_id,
            group_idx,
            "merge_proof",
            proof.model_dump(mode="json"),
        )

    async def record_commit_proof(
        self,
        queue_item_id: int,
        *,
        feature_id: str,
        group_idx: int,
        repo_proofs: list[RepoCommitProof],
    ) -> int:
        """Persist per-repo commit + no-dirty proof as an ``evidence_nodes`` row.

        Returns the evidence id the worker passes to
        ``transition(..., 'integrated', commit_proof_evidence_id=...)``.
        Idempotent on identical proof content.
        """

        if not repo_proofs:
            raise MergeQueueError("commit proof requires at least one repo proof")
        return await self._record_proof_evidence(
            queue_item_id,
            feature_id,
            group_idx,
            "commit_proof",
            {"repo_proofs": [p.model_dump(mode="json") for p in repo_proofs]},
        )

    async def _record_proof_evidence(
        self,
        queue_item_id: int,
        feature_id: str,
        group_idx: int,
        kind: str,
        payload: dict[str, Any],
    ) -> int:
        if not feature_id:
            raise MergeQueueError("proof evidence requires a feature_id")
        content_hash = stable_digest(payload)
        idem = f"{kind}:{queue_item_id}:{content_hash}"
        row = await self._conn.fetchrow(
            "INSERT INTO evidence_nodes "
            "(feature_id, idempotency_key, kind, content_hash, group_idx, "
            " source_ref, summary, payload) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb) "
            "ON CONFLICT (feature_id, idempotency_key) DO NOTHING RETURNING id",
            feature_id,
            idem,
            kind,
            content_hash,
            group_idx,
            f"merge_queue_item:{queue_item_id}",
            f"{kind} for merge queue item {queue_item_id}",
            _jsonb(payload),
        )
        if row is not None:
            return int(row["id"])
        existing = await self._conn.fetchval(
            "SELECT id FROM evidence_nodes "
            "WHERE feature_id = $1 AND idempotency_key = $2",
            feature_id,
            idem,
        )
        assert existing is not None
        return int(existing)

    async def advance_repo_target(
        self,
        queue_item_id: int,
        repo_id: str,
        lease_owner: str,
        lease_version: int,
        to_status: RepoTargetStatus,
        *,
        pre_apply_head: str | None = None,
        applied_head: str | None = None,
        result_commit: str | None = None,
        tree_sha: str | None = None,
        no_dirty_snapshot_id: int | None = None,
    ) -> MergeQueueRepoTarget:
        """Advance one ``merge_queue_repo_targets`` row through its status
        machine, lease-fenced through the parent queue item.

        A stale ``(lease_owner, lease_version)`` matches zero rows and raises
        :class:`LeaseFencedError`. Each target status requires its proof
        column(s): ``pre_apply_recorded`` -> ``pre_apply_head``, ``applied`` ->
        ``applied_head``, ``committed`` -> ``result_commit`` + ``tree_sha``,
        ``clean`` -> ``no_dirty_snapshot_id``.
        """

        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                "SELECT t.id, t.status FROM merge_queue_repo_targets t "
                "JOIN merge_queue_items i ON i.id = t.queue_item_id "
                "WHERE t.queue_item_id = $1 AND t.repo_id = $2 "
                "AND i.lease_owner = $3 AND i.lease_version = $4 "
                "FOR UPDATE OF t, i",
                queue_item_id,
                repo_id,
                lease_owner,
                lease_version,
            )
            if row is None:
                raise LeaseFencedError(
                    f"repo target {repo_id!r} of queue item {queue_item_id} is "
                    f"fenced — (owner {lease_owner!r}, lease_version "
                    f"{lease_version}) is not current, or the target is missing"
                )
            from_status = row["status"]
            if to_status not in _REPO_TARGET_TRANSITIONS.get(
                from_status, frozenset()
            ):
                raise MergeQueueError(
                    f"repo target transition {from_status!r} -> {to_status!r} "
                    f"is not allowed"
                )
            if to_status == "pre_apply_recorded" and not pre_apply_head:
                raise MergeQueueError(
                    "advance to pre_apply_recorded requires pre_apply_head"
                )
            if to_status == "applied" and not applied_head:
                raise MergeQueueError("advance to applied requires applied_head")
            if to_status == "committed" and (not result_commit or not tree_sha):
                raise MergeQueueError(
                    "advance to committed requires result_commit and tree_sha"
                )
            if to_status == "clean" and no_dirty_snapshot_id is None:
                raise MergeQueueError(
                    "advance to clean requires no_dirty_snapshot_id"
                )

            sets = ["status = $3", "updated_at = now()"]
            params: list[Any] = [queue_item_id, repo_id, to_status]

            def _set(column: str, value: Any) -> None:
                params.append(value)
                sets.append(f"{column} = ${len(params)}")

            if pre_apply_head is not None:
                _set("pre_apply_head", pre_apply_head)
            if applied_head is not None:
                _set("applied_head", applied_head)
            if result_commit is not None:
                _set("result_commit", result_commit)
            if tree_sha is not None:
                _set("tree_sha", tree_sha)
            if no_dirty_snapshot_id is not None:
                _set("no_dirty_snapshot_id", no_dirty_snapshot_id)

            await self._conn.execute(
                f"UPDATE merge_queue_repo_targets SET {', '.join(sets)} "
                f"WHERE queue_item_id = $1 AND repo_id = $2",
                *params,
            )

        target = await self._load_repo_target(queue_item_id, repo_id)
        assert target is not None
        return target

    async def _load_repo_target(
        self, queue_item_id: int, repo_id: str
    ) -> MergeQueueRepoTarget | None:
        row = await self._conn.fetchrow(
            "SELECT id, queue_item_id, feature_id, dag_sha256, group_idx, "
            "repo_id, repo_path, base_commit, expected_head, pre_apply_head, "
            "applied_head, result_commit, tree_sha, no_dirty_snapshot_id, "
            "status, target_digest, idempotency_key "
            "FROM merge_queue_repo_targets "
            "WHERE queue_item_id = $1 AND repo_id = $2",
            queue_item_id,
            repo_id,
        )
        return None if row is None else MergeQueueRepoTarget(**dict(row))

    async def load_proof(self, evidence_id: int) -> dict[str, Any] | None:
        """Return the bounded payload of a merge_proof / commit_proof row."""

        row = await self._conn.fetchrow(
            "SELECT payload FROM evidence_nodes WHERE id = $1 "
            "AND kind IN ('merge_proof', 'commit_proof')",
            evidence_id,
        )
        if row is None:
            return None
        return _loads(row["payload"], {})

    # ── validation ──────────────────────────────────────────────────────────

    def _validate_create(self, create: MergeQueueItemCreate) -> None:
        if not create.feature_id or not create.dag_sha256:
            raise MergeQueueError("enqueue requires feature_id and dag_sha256")
        if not create.base_commit:
            raise MergeQueueError("enqueue requires a base_commit")
        if create.pre_queue_gate_evidence_id is None:
            raise MergeQueueError(
                "enqueue requires pre_queue_gate_evidence_id — the queue is "
                "entered only after pre-queue gates approve"
            )
        if not create.task_coverage:
            raise MergeQueueError(
                "enqueue requires task coverage rows (no-op groups are not "
                "supported by this path)"
            )
        if not create.patch_evidence_ids:
            raise MergeQueueError("enqueue requires immutable patch evidence ids")
        if not create.contract_ids:
            raise MergeQueueError("enqueue requires active contract ids")
        seen_tasks: set[str] = set()
        for coverage in create.task_coverage:
            if coverage.task_id in seen_tasks:
                raise MergeQueueError(
                    f"duplicate task coverage for {coverage.task_id!r}"
                )
            seen_tasks.add(coverage.task_id)
        seen_repos: set[str] = set()
        for target in create.repo_targets:
            if target.repo_id in seen_repos:
                raise MergeQueueError(
                    f"duplicate repo target for {target.repo_id!r}"
                )
            seen_repos.add(target.repo_id)
        lane = create.integration_lane
        if lane.startswith("task:") and len(create.task_coverage) != 1:
            raise MergeQueueError(
                "a task: integration lane must cover exactly one task id"
            )

    async def _validate_contracts(self, create: MergeQueueItemCreate) -> None:
        for coverage in create.task_coverage:
            row = await self._conn.fetchrow(
                "SELECT feature_id, dag_sha256, group_idx, task_id, status "
                "FROM task_deliverable_contracts WHERE id = $1 FOR UPDATE",
                coverage.contract_id,
            )
            if row is None:
                raise MergeQueueError(
                    f"contract {coverage.contract_id} for task "
                    f"{coverage.task_id!r} does not exist"
                )
            if row["status"] != "active":
                raise MergeQueueError(
                    f"contract {coverage.contract_id} for task "
                    f"{coverage.task_id!r} is not active (status "
                    f"{row['status']!r})"
                )
            if (
                row["feature_id"] != create.feature_id
                or row["dag_sha256"] != create.dag_sha256
                or row["group_idx"] != create.group_idx
                or row["task_id"] != coverage.task_id
            ):
                raise MergeQueueError(
                    f"contract {coverage.contract_id} scope does not match "
                    f"the lane scope for task {coverage.task_id!r}"
                )

    async def _reject_competing_lanes(
        self, create: MergeQueueItemCreate, key: str
    ) -> None:
        """No two live lanes may cover the same task id.

        The one exception — a single authorized ``retry_merge`` replacement of a
        terminal failed source — is handled by :meth:`_validate_retry_source`.
        A lane sharing this enqueue's idempotency key is the same request
        (a concurrent duplicate) and is excluded, not treated as a competitor.
        """

        if create.retry_of_queue_item_id is not None:
            return
        for coverage in create.task_coverage:
            competing = await self._conn.fetchrow(
                "SELECT c.queue_item_id, i.status "
                "FROM merge_queue_task_coverage c "
                "JOIN merge_queue_items i ON i.id = c.queue_item_id "
                "WHERE c.feature_id = $1 AND c.dag_sha256 = $2 "
                "AND c.group_idx = $3 AND c.task_id = $4 "
                "AND i.status <> 'cancelled' AND i.idempotency_key <> $5 "
                "ORDER BY c.queue_item_id FOR UPDATE OF i",
                create.feature_id,
                create.dag_sha256,
                create.group_idx,
                coverage.task_id,
                key,
            )
            if competing is not None:
                raise MergeQueueError(
                    f"task {coverage.task_id!r} is already covered by queue "
                    f"item {competing['queue_item_id']} "
                    f"(status {competing['status']!r}); a second lane requires "
                    f"an authorized retry_merge replacement"
                )

    async def _validate_retry_source(
        self, create: MergeQueueItemCreate
    ) -> None:
        source_id = create.retry_of_queue_item_id
        source = await self._conn.fetchrow(
            "SELECT id, feature_id, dag_sha256, group_idx, status, "
            "result_commit, head_commit FROM merge_queue_items "
            "WHERE id = $1 FOR UPDATE",
            source_id,
        )
        if source is None:
            raise MergeQueueError(
                f"retry_of_queue_item_id {source_id} does not exist"
            )
        if (
            source["feature_id"] != create.feature_id
            or source["dag_sha256"] != create.dag_sha256
            or source["group_idx"] != create.group_idx
        ):
            raise MergeQueueError(
                f"retry source {source_id} is outside the lane "
                f"feature/DAG/group scope"
            )
        if source["status"] != "failed":
            raise MergeQueueError(
                f"retry source {source_id} is not terminal failed "
                f"(status {source['status']!r})"
            )
        if source["result_commit"]:
            raise MergeQueueError(
                f"retry source {source_id} already produced a result commit"
            )
        if source["head_commit"] == create.head_commit:
            # A retry must carry a fresh sandbox patch. An identical head would
            # also collide with the source on idempotency_key.
            raise MergeQueueError(
                f"retry replacement must carry a fresh patch — head_commit "
                f"{create.head_commit!r} matches retry source {source_id}"
            )
        replacement = await self._conn.fetchrow(
            "SELECT id FROM merge_queue_items "
            "WHERE retry_of_queue_item_id = $1 AND status <> 'cancelled'",
            source_id,
        )
        if replacement is not None:
            raise MergeQueueError(
                f"retry source {source_id} already has a non-cancelled "
                f"replacement (queue item {replacement['id']})"
            )
        source_tasks = {
            row["task_id"]
            for row in await self._conn.fetch(
                "SELECT task_id FROM merge_queue_task_coverage "
                "WHERE queue_item_id = $1",
                source_id,
            )
        }
        new_tasks = {c.task_id for c in create.task_coverage}
        if source_tasks != new_tasks:
            raise MergeQueueError(
                f"retry replacement coverage {sorted(new_tasks)} does not "
                f"match retry source coverage {sorted(source_tasks)}"
            )

    # ── inserts ─────────────────────────────────────────────────────────────

    async def _insert_item(
        self,
        create: MergeQueueItemCreate,
        digest: str,
        key: str,
    ) -> int:
        # A duplicate idempotency_key raises asyncpg.UniqueViolationError, which
        # enqueue() catches and resolves idempotently.
        return await self._conn.fetchval(
            "INSERT INTO merge_queue_items "
            "(feature_id, dag_sha256, group_idx, repo_id, repo_path, "
            " attempt_id, contract_ids, patch_evidence_ids, "
            " gate_evidence_ids, pre_queue_gate_evidence_id, base_commit, "
            " head_commit, status, priority, retry_of_queue_item_id, "
            " request_digest, idempotency_key, payload) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,$9::jsonb,$10,"
            " $11,$12,'queued',$13,$14,$15,$16,$17::jsonb) RETURNING id",
            create.feature_id,
            create.dag_sha256,
            create.group_idx,
            create.repo_id,
            create.repo_path,
            create.attempt_id,
            _jsonb(sorted(create.contract_ids)),
            _jsonb(sorted(create.patch_evidence_ids)),
            _jsonb(sorted(create.gate_evidence_ids)),
            create.pre_queue_gate_evidence_id,
            create.base_commit,
            create.head_commit,
            create.priority,
            create.retry_of_queue_item_id,
            digest,
            key,
            _jsonb(create.payload),
        )

    async def _insert_coverage(
        self, item_id: int, create: MergeQueueItemCreate
    ) -> None:
        for coverage in create.task_coverage:
            digest = _coverage_digest(
                item_id,
                create.feature_id,
                create.dag_sha256,
                create.group_idx,
                coverage.task_id,
                coverage.contract_id,
            )
            await self._conn.execute(
                "INSERT INTO merge_queue_task_coverage "
                "(queue_item_id, feature_id, dag_sha256, group_idx, task_id, "
                " contract_id, coverage_digest, idempotency_key) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8)",
                item_id,
                create.feature_id,
                create.dag_sha256,
                create.group_idx,
                coverage.task_id,
                coverage.contract_id,
                digest,
                f"merge-coverage:{item_id}:{coverage.task_id}:"
                f"{coverage.contract_id}:{digest}",
            )

    async def _insert_repo_targets(
        self, item_id: int, create: MergeQueueItemCreate
    ) -> None:
        for target in create.repo_targets:
            digest = _target_digest(
                item_id,
                create.feature_id,
                create.dag_sha256,
                create.group_idx,
                target,
            )
            await self._conn.execute(
                "INSERT INTO merge_queue_repo_targets "
                "(queue_item_id, feature_id, dag_sha256, group_idx, repo_id, "
                " repo_path, base_commit, expected_head, status, "
                " target_digest, idempotency_key) "
                "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'pending',$9,$10)",
                item_id,
                create.feature_id,
                create.dag_sha256,
                create.group_idx,
                target.repo_id,
                target.repo_path,
                target.base_commit,
                target.expected_head,
                digest,
                f"merge-repo-target:{item_id}:{target.repo_id}:"
                f"{target.base_commit}",
            )

    # ── load ────────────────────────────────────────────────────────────────

    async def _load(self, item_id: int) -> MergeQueueItem | None:
        row = await self._conn.fetchrow(
            f"SELECT {_ITEM_COLUMNS} FROM merge_queue_items WHERE id = $1",
            item_id,
        )
        if row is None:
            return None
        coverage_rows = await self._conn.fetch(
            "SELECT id, queue_item_id, feature_id, dag_sha256, group_idx, "
            "task_id, contract_id, coverage_digest, idempotency_key "
            "FROM merge_queue_task_coverage WHERE queue_item_id = $1 "
            "ORDER BY task_id",
            item_id,
        )
        target_rows = await self._conn.fetch(
            "SELECT id, queue_item_id, feature_id, dag_sha256, group_idx, "
            "repo_id, repo_path, base_commit, expected_head, pre_apply_head, "
            "applied_head, result_commit, tree_sha, no_dirty_snapshot_id, "
            "status, target_digest, idempotency_key "
            "FROM merge_queue_repo_targets WHERE queue_item_id = $1 "
            "ORDER BY repo_id",
            item_id,
        )
        data = dict(row)
        data["contract_ids"] = _loads(data.get("contract_ids"), [])
        data["patch_evidence_ids"] = _loads(data.get("patch_evidence_ids"), [])
        data["gate_evidence_ids"] = _loads(data.get("gate_evidence_ids"), [])
        data["payload"] = _loads(data.get("payload"), {})
        data["task_coverage"] = [
            MergeQueueTaskCoverage(**dict(c)) for c in coverage_rows
        ]
        data["repo_targets"] = [
            MergeQueueRepoTarget(**dict(t)) for t in target_rows
        ]
        return MergeQueueItem(**data)


__all__ = [
    "MergeQueueStatus",
    "RepoTargetStatus",
    "MergeQueueError",
    "LeaseFencedError",
    "DEFAULT_LEASE_TTL_SECONDS",
    "TaskCoverageCreate",
    "RepoTargetCreate",
    "MergeQueueItemCreate",
    "MergeQueueTaskCoverage",
    "MergeQueueRepoTarget",
    "MergeQueueItem",
    "MergeProof",
    "RepoCommitProof",
    "MergeQueueStore",
    "request_digest",
    "idempotency_key",
]
