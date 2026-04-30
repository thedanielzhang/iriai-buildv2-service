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
PUBLIC_DASHBOARD_SCHEMA_VERSION = 1


def _flag_enabled(name: str, *, default: str = "1") -> bool:
    return os.environ.get(name, default).strip().lower() not in {"0", "false", "no", "off"}


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
            _flag_enabled(PUBLIC_DASHBOARD_OUTBOX_ENV)
            if outbox_enabled is None else outbox_enabled
        )
        self.display_jobs_enabled = (
            _flag_enabled(PUBLIC_DISPLAY_JOBS_ENV)
            if display_jobs_enabled is None else display_jobs_enabled
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
            "content": content,
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
        if _is_public_artifact_key(key):
            payload["content"] = value
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
