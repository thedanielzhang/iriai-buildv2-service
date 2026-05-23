from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import asyncpg

from iriai_compose import Feature

logger = logging.getLogger(__name__)

PUBLIC_DASHBOARD_OUTBOX_ENV = "IRIAI_PUBLIC_DASHBOARD_OUTBOX"
PUBLIC_DISPLAY_JOBS_ENV = "IRIAI_PUBLIC_DISPLAY_JOBS"
PUBLIC_DASHBOARD_CONSUMER_ENV = "IRIAI_PUBLIC_DASHBOARD_CONSUMER_ENABLED"
PUBLIC_DASHBOARD_MAX_PAYLOAD_BYTES_ENV = "IRIAI_PUBLIC_DASHBOARD_MAX_PAYLOAD_BYTES"
PUBLIC_DASHBOARD_MAX_CONTENT_BYTES_ENV = "IRIAI_PUBLIC_DASHBOARD_MAX_CONTENT_BYTES"
PUBLIC_DASHBOARD_DEFAULT_MAX_PAYLOAD_BYTES = 64_000
PUBLIC_DASHBOARD_DEFAULT_MAX_CONTENT_BYTES = 32_000
PUBLIC_DASHBOARD_SCHEMA_VERSION = 1

# Slice 10f (doc 10 step 9) — the public-dashboard typed control-plane display
# event. doc 10 § "Dashboard Integration Points": "Public dashboard mirroring
# emits a bounded `control_plane.snapshot_changed` outbox event containing
# `feature_id`, `snapshot_version`, visible counters, route, and cited evidence
# refs. It does not publish private evidence bodies."
CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT = "control_plane.snapshot_changed"
# Bounded sample caps for the SUMMARY-ONLY display payload. The public
# dashboard is an external-facing surface — the event NEVER carries an artifact
# body, a prompt, stdout/stderr, or an unbounded list. Only counts, the route,
# and a small number of bounded `EvidenceRef` citations (id + short citation)
# are projected.
CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS = 12
CONTROL_PLANE_EVENT_MAX_DEGRADATION_REASONS = 8
# Hard ceiling on any single bounded text field copied into the event (a
# citation / kind / route string). Defends the external surface even if an
# upstream summary string is unexpectedly long.
CONTROL_PLANE_EVENT_MAX_TEXT_CHARS = 240


def _flag_enabled(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _outbox_default_enabled() -> bool:
    return _flag_enabled(PUBLIC_DASHBOARD_CONSUMER_ENV, default="0")


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def _jsonable(value: Any) -> Any:
    try:
        json.dumps(value, default=str)
        return value
    except Exception:
        return json.loads(json.dumps(value, default=str))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_public_artifact_key(key: str) -> bool:
    return key == "public-summary" or key.startswith("public-")


def _idempotency_key(*parts: Any) -> str:
    raw = "\n".join(json.dumps(part, sort_keys=True, default=str) for part in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DisplayJobSpec:
    job_type: str
    reason: str
    group_idx: int | None = None
    source_artifact_keys: tuple[str, ...] = ()
    source_digests: dict[str, str] | None = None
    payload: dict[str, Any] | None = None
    priority: int = 100


class PublicDashboardOutbox:
    """Best-effort projection outbox for the public dashboard service.

    This class intentionally swallows and logs write failures. The workflow DB
    and artifacts remain canonical; public dashboard delivery must never block
    planning, implementation, verification, or gate approval.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        outbox_enabled: bool | None = None,
        display_jobs_enabled: bool | None = None,
    ) -> None:
        self._pool = pool
        self.outbox_enabled = (
            _flag_enabled(
                PUBLIC_DASHBOARD_OUTBOX_ENV,
                default="1" if _outbox_default_enabled() else "0",
            )
            if outbox_enabled is None else outbox_enabled
        )
        self.display_jobs_enabled = (
            _flag_enabled(
                PUBLIC_DISPLAY_JOBS_ENV,
                default="1" if _outbox_default_enabled() else "0",
            )
            if display_jobs_enabled is None else display_jobs_enabled
        )
        self.max_payload_bytes = max(
            1024,
            _int_env(
                PUBLIC_DASHBOARD_MAX_PAYLOAD_BYTES_ENV,
                PUBLIC_DASHBOARD_DEFAULT_MAX_PAYLOAD_BYTES,
            ),
        )
        self.max_content_bytes = max(
            0,
            _int_env(
                PUBLIC_DASHBOARD_MAX_CONTENT_BYTES_ENV,
                PUBLIC_DASHBOARD_DEFAULT_MAX_CONTENT_BYTES,
            ),
        )

    async def emit_event(
        self,
        *,
        feature_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str | None = None,
        visibility: str = "internal",
    ) -> str | None:
        if not self.outbox_enabled:
            return None
        event_id = event_id or uuid4().hex
        payload = self._bounded_payload(payload)
        try:
            await self._pool.execute(
                """
                INSERT INTO public_dashboard_outbox
                    (event_id, feature_id, event_type, schema_version, visibility, payload)
                VALUES ($1, $2, $3, $4, $5, $6::jsonb)
                ON CONFLICT (event_id) DO NOTHING
                """,
                event_id,
                feature_id,
                event_type,
                PUBLIC_DASHBOARD_SCHEMA_VERSION,
                visibility,
                json.dumps(_jsonable(payload), default=str),
            )
            return event_id
        except Exception:
            logger.warning(
                "Failed to enqueue public dashboard event feature=%s type=%s",
                feature_id,
                event_type,
                exc_info=True,
            )
            return None

    async def mirror_private_event(
        self,
        *,
        source_event_id: int | str | None,
        feature_id: str,
        event_type: str,
        source: str,
        content: str | None,
        metadata: dict[str, Any] | None,
    ) -> str | None:
        payload = {
            "source_event_id": source_event_id,
            "event_type": event_type,
            "source": source,
            "content": _bounded_text(content, self.max_content_bytes) if content else None,
            "metadata": metadata or {},
            "occurred_at": _utc_now(),
        }
        event_id = (
            f"workflow-event:{source_event_id}"
            if source_event_id is not None else None
        )
        return await self.emit_event(
            feature_id=feature_id,
            event_type=f"workflow.{event_type}",
            payload=payload,
            event_id=event_id,
            visibility="internal",
        )

    async def mirror_artifact_write(
        self,
        *,
        source_artifact_id: int | str | None,
        feature: Feature,
        key: str,
        value: str,
        visibility: str = "internal",
    ) -> str | None:
        payload = {
            "source_artifact_id": source_artifact_id,
            "artifact_key": key,
            "sha256": _digest_text(value),
            "size_bytes": len(value.encode("utf-8")),
            "content_type": _guess_content_type(key, value),
            "visibility": visibility,
            "created_at": _utc_now(),
            "publish_artifact_candidate": _is_public_artifact_key(key),
        }
        if _is_public_artifact_key(key) and self.max_content_bytes:
            payload["content"] = _bounded_text(value, self.max_content_bytes)
        event_id = (
            f"artifact-write:{source_artifact_id}"
            if source_artifact_id is not None else None
        )
        return await self.emit_event(
            feature_id=feature.id,
            event_type="artifact.written",
            payload=payload,
            event_id=event_id,
            visibility=visibility,
        )

    async def project_control_plane_snapshot_changed(
        self,
        *,
        feature_id: str,
        snapshot: Any,
        conn: Any | None = None,
    ) -> str | None:
        """Project a BOUNDED `control_plane.snapshot_changed` display event.

        doc 10 step 9 / § "Dashboard Integration Points". The event payload is
        SUMMARY-ONLY — `feature_id`, the typed `snapshot_version`, visible
        counters, route, and bounded cited `EvidenceRef`s; it NEVER carries an
        artifact body, a prompt, stdout/stderr, or an unbounded list.

        Idempotency: doc 10 keys the write on `(feature_id, snapshot_version,
        "control_plane.snapshot_changed")`. `ON CONFLICT (event_id) DO NOTHING`
        means re-projecting the SAME typed snapshot version never enqueues a
        duplicate public notification — a new event is enqueued only when the
        typed `snapshot_version` advances.

        FAIL-CLOSED (doc 10 § "Edge Cases"): unlike :meth:`emit_event` (a
        best-effort mirror that swallows write failures), a control-plane
        snapshot projection ENQUEUE failure while the outbox is configured is
        NOT ignorable — it RAISES so the caller's projection transaction aborts
        and the typed snapshot, the cursor/ETag compatibility state, and the
        outbox row cannot diverge. (Async DELIVERY failure AFTER a committed
        enqueue is still log-and-continue — that is the consumer's concern, not
        this enqueue.) When the outbox is disabled this is a no-op returning
        ``None``: there is no configured outbox to keep consistent.

        ``conn`` lets the caller run the enqueue inside its own projection
        transaction so the fail-closed guarantee is transactional; when omitted
        the enqueue runs on the pool.
        """

        if not self.outbox_enabled:
            return None
        payload = control_plane_snapshot_changed_payload(snapshot)
        snapshot_version = str(payload.get("snapshot_version") or "")
        if not snapshot_version:
            # No typed version digest — there is nothing to key idempotency on
            # and nothing advanced. Fail closed rather than enqueue an
            # un-deduplicable public event.
            raise ValueError(
                "control_plane.snapshot_changed projection requires a "
                "non-empty snapshot_version"
            )
        event_id = control_plane_snapshot_event_id(feature_id, snapshot_version)
        bounded = self._bounded_payload(payload)
        executor = conn if conn is not None else self._pool
        # NOTE: no `except` here — a failure must propagate (fail closed).
        await executor.execute(
            """
            INSERT INTO public_dashboard_outbox
                (event_id, feature_id, event_type, schema_version, visibility, payload)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event_id,
            feature_id,
            CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
            PUBLIC_DASHBOARD_SCHEMA_VERSION,
            "public",
            json.dumps(_jsonable(bounded), default=str),
        )
        return event_id

    async def enqueue_display_job(
        self,
        feature: Feature,
        spec: DisplayJobSpec,
    ) -> str | None:
        if not self.display_jobs_enabled:
            return None
        source_digests = dict(spec.source_digests or {})
        payload = dict(spec.payload or {})
        idempotency = _idempotency_key(
            feature.id,
            spec.job_type,
            spec.reason,
            spec.group_idx,
            tuple(spec.source_artifact_keys),
            source_digests,
            payload,
        )
        job_id = f"pdj-{idempotency[:24]}"
        try:
            await self._pool.execute(
                """
                INSERT INTO public_display_jobs
                    (
                        job_id, feature_id, job_type, reason, group_idx, priority,
                        source_artifact_keys, source_digests, payload, idempotency_key
                    )
                VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                job_id,
                feature.id,
                spec.job_type,
                spec.reason,
                spec.group_idx,
                spec.priority,
                json.dumps(list(spec.source_artifact_keys)),
                json.dumps(source_digests, sort_keys=True),
                json.dumps(_jsonable(payload), default=str),
                idempotency,
            )
            await self.emit_event(
                feature_id=feature.id,
                event_type="public_display.job_queued",
                payload={
                    "job_id": job_id,
                    "job_type": spec.job_type,
                    "reason": spec.reason,
                    "group_idx": spec.group_idx,
                    "source_artifact_keys": list(spec.source_artifact_keys),
                },
                event_id=f"public-display-job:{job_id}",
                visibility="internal",
            )
            return job_id
        except Exception:
            logger.warning(
                "Failed to enqueue public display job feature=%s type=%s reason=%s",
                feature.id,
                spec.job_type,
                spec.reason,
                exc_info=True,
            )
            return None

    async def pending_summary(self, *, feature_id: str | None = None) -> dict[str, Any]:
        """Return pending outbox backlog metrics without loading payload bodies."""

        if feature_id:
            row = await self._pool.fetchrow(
                """
                SELECT count(*)::bigint AS pending_count,
                       COALESCE(sum(pg_column_size(payload)), 0)::bigint AS pending_payload_bytes,
                       min(created_at) AS oldest_created_at,
                       max(created_at) AS newest_created_at
                FROM public_dashboard_outbox
                WHERE status = 'pending' AND feature_id = $1
                """,
                feature_id,
            )
        else:
            row = await self._pool.fetchrow(
                """
                SELECT count(*)::bigint AS pending_count,
                       COALESCE(sum(pg_column_size(payload)), 0)::bigint AS pending_payload_bytes,
                       min(created_at) AS oldest_created_at,
                       max(created_at) AS newest_created_at
                FROM public_dashboard_outbox
                WHERE status = 'pending'
                """
            )
        return _jsonable(dict(row)) if row is not None else {
            "pending_count": 0,
            "pending_payload_bytes": 0,
            "oldest_created_at": None,
            "newest_created_at": None,
        }

    async def delete_pending_before(
        self,
        cutoff: datetime,
        *,
        feature_id: str | None = None,
        limit: int = 10_000,
    ) -> int:
        """Bounded backlog cleanup for explicitly approved stale pending rows."""

        limit = max(1, min(100_000, int(limit or 10_000)))
        if feature_id:
            result = await self._pool.fetchval(
                """
                WITH doomed AS (
                    SELECT id
                    FROM public_dashboard_outbox
                    WHERE status = 'pending' AND feature_id = $1 AND created_at < $2
                    ORDER BY id
                    LIMIT $3
                ),
                deleted AS (
                    DELETE FROM public_dashboard_outbox
                    WHERE id IN (SELECT id FROM doomed)
                    RETURNING id
                )
                SELECT count(*)::int FROM deleted
                """,
                feature_id,
                cutoff,
                limit,
            )
        else:
            result = await self._pool.fetchval(
                """
                WITH doomed AS (
                    SELECT id
                    FROM public_dashboard_outbox
                    WHERE status = 'pending' AND created_at < $1
                    ORDER BY id
                    LIMIT $2
                ),
                deleted AS (
                    DELETE FROM public_dashboard_outbox
                    WHERE id IN (SELECT id FROM doomed)
                    RETURNING id
                )
                SELECT count(*)::int FROM deleted
                """,
                cutoff,
                limit,
            )
        return int(result or 0)

    def _bounded_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        bounded = _jsonable(payload)
        encoded = json.dumps(bounded, default=str).encode("utf-8")
        if len(encoded) <= self.max_payload_bytes:
            return bounded
        compact: dict[str, Any] = {
            "truncated": True,
            "original_payload_bytes": len(encoded),
            "keys": sorted(str(key) for key in payload.keys()),
        }
        for key in (
            "source_event_id",
            "source_artifact_id",
            "event_type",
            "artifact_key",
            "sha256",
            "size_bytes",
            "content_type",
            "visibility",
            "created_at",
            "occurred_at",
            "publish_artifact_candidate",
            "job_id",
            "job_type",
            "reason",
            "group_idx",
        ):
            if key in payload:
                compact[key] = payload[key]
        return self._enforce_payload_cap(compact)

    def _enforce_payload_cap(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, default=str).encode("utf-8")
        if len(encoded) <= self.max_payload_bytes:
            return payload
        compact = {
            "truncated": True,
            "original_payload_bytes": payload.get("original_payload_bytes") or len(encoded),
            "payload_too_large": True,
        }
        encoded = json.dumps(compact, default=str).encode("utf-8")
        if len(encoded) <= self.max_payload_bytes:
            return compact
        return {"truncated": True}


def _guess_content_type(key: str, value: str) -> str:
    key_lower = key.lower()
    stripped = value.lstrip()
    if key_lower.endswith(".json") or stripped.startswith(("{", "[")):
        return "application/json"
    if key_lower.endswith(".html") or stripped.startswith("<!doctype html") or stripped.startswith("<html"):
        return "text/html"
    if key_lower.endswith(".md") or stripped.startswith("#"):
        return "text/markdown"
    return "text/plain"


def _bounded_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    clipped = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{clipped}\n[truncated public dashboard payload: {len(encoded)} bytes]"


# ── Slice 10f (doc 10 step 9) — typed control-plane snapshot display event ──


def _snapshot_as_dict(snapshot: Any) -> dict[str, Any]:
    """Normalise a typed `ControlPlaneSnapshot` (Pydantic model OR dict).

    The Slice-10a typed `ControlPlaneSnapshot` is a Pydantic model; the
    dashboard already serialises it through `model_dump(mode="json")`. Accept
    either form so the projection can be driven by the store, the dashboard, or
    a test without coupling to the model import.
    """

    if isinstance(snapshot, dict):
        return snapshot
    dump = getattr(snapshot, "model_dump", None)
    if callable(dump):
        return dump(mode="json")
    raise TypeError(
        "control-plane snapshot projection requires a ControlPlaneSnapshot "
        f"model or dict, got {type(snapshot).__name__}"
    )


def _clip_event_text(value: Any) -> str:
    """Clip a single bounded text field for the external display event."""

    text = "" if value is None else str(value)
    if len(text) <= CONTROL_PLANE_EVENT_MAX_TEXT_CHARS:
        return text
    return text[:CONTROL_PLANE_EVENT_MAX_TEXT_CHARS] + "…"


def _summary_only_evidence_ref(ref: Any) -> dict[str, Any]:
    """Reduce one `EvidenceRef` to a SUMMARY-ONLY citation for the public event.

    doc 10 § "Dashboard Integration Points": the event carries "cited evidence
    refs" — it "does not publish private evidence bodies". `EvidenceRef` itself
    is already bounded (id + citation + short summary, never a body); this
    additionally clips every text field so a long upstream summary cannot bloat
    the external payload.
    """

    ref_dict = ref if isinstance(ref, dict) else _snapshot_as_dict(ref)
    raw_id = ref_dict.get("id")
    try:
        ref_id = int(raw_id) if raw_id is not None else None
    except (TypeError, ValueError):
        ref_id = None
    return {
        "table": _clip_event_text(ref_dict.get("table")),
        "id": ref_id,
        "citation": _clip_event_text(ref_dict.get("citation")),
        "kind": _clip_event_text(ref_dict.get("kind")),
    }


def control_plane_snapshot_changed_payload(snapshot: Any) -> dict[str, Any]:
    """Build the BOUNDED `control_plane.snapshot_changed` display payload.

    doc 10 § "Dashboard Integration Points": the event carries `feature_id`,
    `snapshot_version`, "visible counters", `route`, and "cited evidence refs".
    It is a DISPLAY event for an external-facing surface — it is SUMMARY-ONLY:
    ids / versions / counts / route / bounded citations. It NEVER carries an
    artifact body, a prompt, stdout/stderr, a verifier body, a full dirty-path
    list, or any unbounded list. This builder takes the typed
    `ControlPlaneSnapshot` and projects ONLY the bounded fields — every typed
    summary list is reduced to its `count`; only a small bounded set of
    `EvidenceRef` citations is carried.
    """

    data = _snapshot_as_dict(snapshot)

    def _count(field: str) -> int:
        value = data.get(field)
        return len(value) if isinstance(value, (list, tuple)) else 0

    # "Visible counters" — the typed summary lists reduced to counts only. No
    # list body crosses the public boundary.
    counters = {
        "active_attempts": _count("active_attempts"),
        "workspace_snapshots": _count("workspace_snapshots"),
        "latest_failures": _count("latest_failures"),
        "merge_queue": _count("merge_queue"),
        "retry_budgets": _count("retry_budgets"),
        "sandbox_leases": _count("sandbox_leases"),
        "runtime_bindings": _count("runtime_bindings"),
        "gates": _count("gates"),
        "checkpoints": _count("checkpoints"),
        "evidence_refs": _count("evidence_refs"),
    }

    raw_refs = data.get("evidence_refs")
    refs_list = raw_refs if isinstance(raw_refs, (list, tuple)) else ()
    evidence_refs = [
        _summary_only_evidence_ref(ref)
        for ref in list(refs_list)[:CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS]
    ]

    raw_reasons = data.get("degradation_reasons")
    reasons_list = raw_reasons if isinstance(raw_reasons, (list, tuple)) else ()
    degradation_reasons = [
        _clip_event_text(reason)
        for reason in list(reasons_list)[:CONTROL_PLANE_EVENT_MAX_DEGRADATION_REASONS]
    ]

    omitted = data.get("omitted_counts")
    active_group = data.get("active_group_idx")
    return {
        "feature_id": _clip_event_text(data.get("feature_id")),
        "snapshot_version": _clip_event_text(data.get("snapshot_version")),
        "generated_at": data.get("generated_at"),
        "source": _clip_event_text(data.get("source")),
        "degraded": bool(data.get("degraded")),
        "degradation_reasons": degradation_reasons,
        "truncated": bool(data.get("truncated")),
        "omitted_counts": omitted if isinstance(omitted, dict) else {},
        "active_group_idx": (
            active_group if isinstance(active_group, int) else None
        ),
        "recommended_route": _clip_event_text(data.get("recommended_route")),
        "recommended_action": _clip_event_text(
            data.get("recommended_action") or "observe"
        ),
        "counters": counters,
        "evidence_refs": evidence_refs,
        "projected_at": _utc_now(),
    }


def control_plane_snapshot_event_id(feature_id: str, snapshot_version: str) -> str:
    """Return the doc-10 idempotency key for a snapshot-changed outbox event.

    doc 10 § "Dashboard Integration Points": "Public outbox writes use
    `(feature_id, snapshot_version, "control_plane.snapshot_changed")` as the
    idempotency key. Re-projecting the same snapshot updates delivery metadata
    or coalesced counters; it does not enqueue duplicate public notifications."
    """

    return _idempotency_key(
        feature_id,
        snapshot_version,
        CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
    )


async def project_control_plane_snapshot_if_changed(
    outbox: PublicDashboardOutbox | None,
    snapshot_store: Any,
    feature_id: str,
    *,
    previous_snapshot_version: str | None,
    scope: str = "dashboard",
    conn: Any | None = None,
) -> str | None:
    """Project a `control_plane.snapshot_changed` event iff the typed version
    advanced (doc 10 step 9 — "fires when the typed snapshot version advances").

    The trigger is the Slice-10a typed
    ``ExecutionControlStore.get_control_plane_snapshot_version`` — a stable
    digest over typed table cursors that advances on a budget-only or
    sandbox-only update, NEVER on artifact-body churn. When the freshly-read
    version equals ``previous_snapshot_version`` this is a no-op returning
    ``None``: no public notification is enqueued for an unchanged snapshot.
    When it differs, the BOUNDED typed snapshot is read via
    ``get_control_plane_snapshot`` and projected through
    :meth:`PublicDashboardOutbox.project_control_plane_snapshot_changed` (which
    is itself idempotent on ``(feature_id, snapshot_version)``, so a racing
    double-projection of the same advanced version still enqueues exactly one
    event).

    FAIL-CLOSED: a configured-outbox enqueue failure propagates (doc 10 §
    "Edge Cases"). When ``outbox`` is ``None`` or disabled this is a no-op.

    ``snapshot_store`` must expose the Slice-10a
    ``get_control_plane_snapshot_version`` / ``get_control_plane_snapshot``
    coroutines (the typed ``ExecutionControlStore``).
    """

    if outbox is None or not getattr(outbox, "outbox_enabled", False):
        return None
    # Slice 10g-1 P2 fix: when the helper is invoked from inside a projection
    # transaction (``conn is not None``), the snapshot-version read MUST
    # execute on THAT connection so the digest reflects the caller's
    # uncommitted typed inserts under Postgres READ-COMMITTED isolation. A
    # cross-connection version-read would return the PRE-transaction digest,
    # collide with an already-emitted outbox ``event_id``, and silently drop
    # the new emission via ``ON CONFLICT (event_id) DO NOTHING`` — a doc-10
    # contract violation. When ``conn is None`` (the dashboard / supervisor
    # / MCP route), we omit the kwarg so the store opens its own pool — that
    # path has nothing uncommitted to miss.
    if conn is not None:
        current_version = str(
            await snapshot_store.get_control_plane_snapshot_version(
                feature_id, conn=conn
            )
        )
    else:
        current_version = str(
            await snapshot_store.get_control_plane_snapshot_version(feature_id)
        )
    if current_version and current_version == (previous_snapshot_version or ""):
        # The typed snapshot version is unchanged — no display event.
        return None
    from .workflows.develop.execution.snapshots import (
        ControlPlaneSnapshotQuery,
        SnapshotBudget,
    )

    if scope not in ("dashboard", "supervisor", "mcp"):
        scope = "dashboard"
    query = ControlPlaneSnapshotQuery(
        feature_id=feature_id,
        scope=scope,  # type: ignore[arg-type]
        budget=SnapshotBudget(),
    )
    # Same Slice 10g-1 P2 rule for the bounded snapshot read — must
    # participate in the caller's transaction when one exists.
    if conn is not None:
        snapshot = await snapshot_store.get_control_plane_snapshot(query, conn=conn)
    else:
        snapshot = await snapshot_store.get_control_plane_snapshot(query)
    return await outbox.project_control_plane_snapshot_changed(
        feature_id=feature_id,
        snapshot=snapshot,
        conn=conn,
    )


async def enqueue_public_display_jobs(
    runner: Any,
    feature: Feature,
    *,
    reason: str,
    job_types: list[str] | tuple[str, ...],
    group_idx: int | None = None,
    source_artifact_keys: list[str] | tuple[str, ...] = (),
    source_digests: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
    priority: int = 100,
) -> list[str]:
    service = getattr(runner, "services", {}).get("public_dashboard") if runner else None
    if service is None or not hasattr(service, "enqueue_display_job"):
        return []
    job_ids: list[str] = []
    for job_type in job_types:
        job_id = await service.enqueue_display_job(
            feature,
            DisplayJobSpec(
                job_type=job_type,
                reason=reason,
                group_idx=group_idx,
                source_artifact_keys=tuple(source_artifact_keys),
                source_digests=source_digests or {},
                payload=payload or {},
                priority=priority,
            ),
        )
        if job_id:
            job_ids.append(job_id)
    return job_ids
