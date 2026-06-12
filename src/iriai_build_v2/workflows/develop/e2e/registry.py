"""Durable artifact I/O for the e2e subsystem.

Wraps the existing ``PostgresArtifactStore`` (append-only, latest-wins, atomic
single-row inserts) with typed get/put for each e2e artifact, keyed per feature.

During STANDALONE PROOF, all writes target a SCRATCH database + a scratch
feature id so the live ``8ac124d6`` artifacts/backlog are never touched. The
live checkpoint READ path (``checkpoint.py``) is separate and read-only.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg
from iriai_compose import Feature

from iriai_build_v2.db import ensure_schema
from iriai_build_v2.storage.artifacts import PostgresArtifactStore

from .models import (
    E2EGreenPointer,
    E2ESpecRecord,
    E2EStatus,
    E2ETrackCursor,
    E2EVerdictRecord,
    ProjectProfile,
)

PROFILE_KEY = "project-profile"
CURSOR_KEY = "e2e-track-cursor"
STATUS_KEY = "e2e-status"
GREEN_KEY = "e2e-green-checkpoint"
BLOCKER_KEY = "e2e-blocker"
# Durable AUTH-BLOCKED lane record (operator standing rule, 17:2x item 5):
# DISTINCT from BLOCKER_KEY on purpose — the tier-i critical-quiesce hook
# (IRIAI_E2E_CRITICAL_QUIESCE) consumes BLOCKER_KEY, and a broken e2e
# CREDENTIAL must never quiesce dispatch. Written only as the fallback when
# the workspace OPERATOR-ACTIONS.md is unreachable from the e2e layer.
AUTH_BLOCKED_KEY = "e2e-auth-blocked"
ENHANCEMENT_BACKLOG_KEY = "enhancement-backlog"


def spec_key(spec_id: str) -> str:
    return f"e2e-spec:{spec_id}"


def verdict_key(spec_id: str, commit: str) -> str:
    return f"e2e-verdict:{spec_id}:{commit}"


def _as_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value)
    return value


class E2ERegistry:
    """Typed artifact read/write bound to one (artifacts store, feature)."""

    def __init__(self, artifacts: Any, feature: Feature) -> None:
        self.artifacts = artifacts
        self.feature = feature

    async def _get(self, key: str) -> dict[str, Any] | None:
        return _as_dict(await self.artifacts.get(key, feature=self.feature))

    async def _put(self, key: str, model: Any) -> None:
        await self.artifacts.put(key, model, feature=self.feature)

    # ---------------------------------------------------------------- profile
    async def get_profile(self) -> ProjectProfile | None:
        d = await self._get(PROFILE_KEY)
        return ProjectProfile.model_validate(d) if d else None

    async def put_profile(self, profile: ProjectProfile) -> None:
        await self._put(PROFILE_KEY, profile)

    # ------------------------------------------------------------------ specs
    async def get_spec(self, spec_id: str) -> E2ESpecRecord | None:
        d = await self._get(spec_key(spec_id))
        return E2ESpecRecord.model_validate(d) if d else None

    async def put_spec(self, spec: E2ESpecRecord) -> None:
        await self._put(spec_key(spec.spec_id), spec)

    # --------------------------------------------------------------- verdicts
    async def get_verdict(self, spec_id: str, commit: str) -> E2EVerdictRecord | None:
        d = await self._get(verdict_key(spec_id, commit))
        return E2EVerdictRecord.model_validate(d) if d else None

    async def put_verdict(self, verdict: E2EVerdictRecord) -> None:
        await self._put(
            verdict_key(verdict.spec_id, verdict.source_commit), verdict
        )

    # ----------------------------------------------------------------- cursor
    async def get_cursor(self) -> E2ETrackCursor | None:
        d = await self._get(CURSOR_KEY)
        return E2ETrackCursor.model_validate(d) if d else None

    async def put_cursor(self, cursor: E2ETrackCursor) -> None:
        await self._put(CURSOR_KEY, cursor)

    # ----------------------------------------------------------------- status
    async def get_status(self) -> E2EStatus | None:
        d = await self._get(STATUS_KEY)
        return E2EStatus.model_validate(d) if d else None

    async def put_status(self, status: E2EStatus) -> None:
        await self._put(STATUS_KEY, status)

    # ----------------------------------------------------------- green pointer
    async def get_green_pointer(self) -> E2EGreenPointer | None:
        d = await self._get(GREEN_KEY)
        return E2EGreenPointer.model_validate(d) if d else None

    async def put_green_pointer(self, pointer: E2EGreenPointer) -> None:
        """Atomic: a single append-only insert is the whole pointer."""
        await self._put(GREEN_KEY, pointer)

    # --------------------------------------------------------- raw passthrough
    async def get_raw(self, key: str) -> Any:
        return await self.artifacts.get(key, feature=self.feature)

    async def put_raw(self, key: str, value: Any) -> None:
        await self.artifacts.put(key, value, feature=self.feature)


def scratch_feature(
    real_feature_id: str, *, name: str = "e2e-scratch", workspace_id: str = "main"
) -> Feature:
    """A throwaway Feature for proof writes (never the live feature id)."""
    fid = f"{real_feature_id}-e2e-scratch"
    return Feature(
        id=fid,
        name=name,
        slug=f"{name}-{real_feature_id}",
        workflow_name="e2e-scratch",
        workspace_id=workspace_id,
    )


async def open_scratch_registry(
    dsn: str, feature: Feature, *, max_size: int = 3, command_timeout: float = 30.0
) -> tuple[Any, E2ERegistry]:
    """Lightweight scratch artifacts store (no heavy bootstrap).

    Returns ``(pool, registry)``; caller closes the pool. Ensures the schema on
    the scratch DB so write-only proofs work without the full agent bootstrap.
    """
    pool = await asyncpg.create_pool(
        dsn, min_size=1, max_size=max_size, command_timeout=command_timeout
    )
    await ensure_schema(pool)
    await ensure_feature_row(pool, feature)
    store = PostgresArtifactStore(pool)
    return pool, E2ERegistry(store, feature)


async def ensure_feature_row(pool: Any, feature: Feature, *, phase: str = "pm") -> None:
    """Idempotently insert the feature row (artifacts FK-references it)."""
    await pool.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id, phase, "
        "metadata) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb) "
        "ON CONFLICT (id) DO NOTHING",
        feature.id,
        feature.name,
        feature.slug,
        feature.workflow_name,
        feature.workspace_id,
        phase,
        json.dumps(feature.metadata),
    )
