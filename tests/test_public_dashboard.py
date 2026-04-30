from __future__ import annotations

import json

import pytest
from iriai_compose import Feature

from iriai_build_v2.public_dashboard import (
    DisplayJobSpec,
    PublicDashboardOutbox,
)
from iriai_build_v2.storage.artifacts import PostgresArtifactStore
from iriai_build_v2.storage.features import PostgresFeatureStore


class _RecordingPool:
    def __init__(self) -> None:
        self.executes: list[tuple[str, tuple[object, ...]]] = []
        self.fetchvals: list[tuple[str, tuple[object, ...]]] = []
        self.next_id = 41

    async def execute(self, sql: str, *args: object) -> str:
        self.executes.append((sql, args))
        return "INSERT 0 1"

    async def fetchval(self, sql: str, *args: object) -> int:
        self.fetchvals.append((sql, args))
        self.next_id += 1
        return self.next_id


class _FailingPool:
    async def execute(self, *_args: object) -> None:
        raise RuntimeError("dashboard table missing")

    async def fetchval(self, *_args: object) -> int:
        raise RuntimeError("primary table missing")


@pytest.fixture
def feature() -> Feature:
    return Feature(
        id="feature-1",
        name="Public Dashboard",
        slug="public-dashboard-feature-1",
        workflow_name="full-develop",
        workspace_id="main",
    )


@pytest.mark.asyncio
async def test_public_dashboard_outbox_is_non_blocking_when_tables_are_missing(feature: Feature) -> None:
    outbox = PublicDashboardOutbox(_FailingPool(), outbox_enabled=True)

    event_id = await outbox.emit_event(
        feature_id=feature.id,
        event_type="workflow.agent_start",
        payload={"agent": "codex"},
        event_id="evt-1",
    )

    assert event_id is None


@pytest.mark.asyncio
async def test_feature_and_artifact_stores_mirror_public_dashboard_events(feature: Feature) -> None:
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True, display_jobs_enabled=True)
    feature_store = PostgresFeatureStore(pool, public_dashboard=outbox)
    artifact_store = PostgresArtifactStore(pool, public_dashboard=outbox)

    await feature_store.log_event(
        feature.id,
        "agent_start",
        "implementer",
        metadata={"phase_name": "implementation"},
    )
    await artifact_store.put("public-summary", {"hello": "world"}, feature=feature)
    await outbox.enqueue_display_job(
        feature,
        DisplayJobSpec(
            job_type="public-summary",
            reason="test",
            source_artifact_keys=("public-summary",),
            source_digests={"public-summary": "abc"},
        ),
    )

    executed_sql = "\n".join(sql for sql, _args in pool.executes)
    artifact_sql = "\n".join(sql for sql, _args in pool.fetchvals)
    assert "public_dashboard_outbox" in executed_sql
    assert "public_display_jobs" in executed_sql
    assert "INSERT INTO artifacts" in artifact_sql

    artifact_event_args = [
        args for sql, args in pool.executes
        if "public_dashboard_outbox" in sql and args[2] == "artifact.written"
    ][0]
    payload = json.loads(str(artifact_event_args[5]))
    assert payload["artifact_key"] == "public-summary"
    assert payload["content_type"] == "application/json"
    assert payload["publish_artifact_candidate"] is True
    assert json.loads(payload["content"]) == {"hello": "world"}
