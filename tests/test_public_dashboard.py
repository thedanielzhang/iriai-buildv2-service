from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from iriai_compose import Feature

from iriai_build_v2.public_dashboard import (
    CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS,
    CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT,
    DisplayJobSpec,
    PublicDashboardOutbox,
    control_plane_snapshot_changed_payload,
    control_plane_snapshot_event_id,
    project_control_plane_snapshot_if_changed,
)
from iriai_build_v2.storage.artifacts import PostgresArtifactStore
from iriai_build_v2.storage.features import PostgresFeatureStore
from iriai_build_v2.workflows.develop.execution.snapshots import (
    ControlPlaneSnapshot,
    EvidenceRef,
    ExecutionAttemptSummary,
    TypedFailureSummary,
)


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


class _PendingSummaryPool:
    def __init__(self) -> None:
        self.fetchrows: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, sql: str, *args: object):
        self.fetchrows.append((sql, args))
        return {
            "pending_count": 2,
            "pending_payload_bytes": 123,
            "oldest_created_at": None,
            "newest_created_at": None,
        }


class _SlowDashboard:
    def __init__(self) -> None:
        self.event_started = False
        self.artifact_started = False

    async def mirror_private_event(self, **_kwargs: object) -> None:
        self.event_started = True
        await asyncio.sleep(1)

    async def mirror_artifact_write(self, **_kwargs: object) -> None:
        self.artifact_started = True
        await asyncio.sleep(1)


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
async def test_public_dashboard_outbox_defaults_off(feature: Feature, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("IRIAI_PUBLIC_DASHBOARD_OUTBOX", raising=False)
    monkeypatch.delenv("IRIAI_PUBLIC_DASHBOARD_CONSUMER_ENABLED", raising=False)
    monkeypatch.delenv("IRIAI_PUBLIC_DISPLAY_JOBS", raising=False)
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool)

    event_id = await outbox.emit_event(
        feature_id=feature.id,
        event_type="workflow.agent_start",
        payload={"agent": "codex"},
        event_id="evt-default-off",
    )
    job_id = await outbox.enqueue_display_job(
        feature,
        DisplayJobSpec(
            job_type="public-summary",
            reason="default-off",
            source_artifact_keys=("public-summary",),
        ),
    )

    assert event_id is None
    assert job_id is None
    assert outbox.display_jobs_enabled is False
    assert pool.executes == []


@pytest.mark.asyncio
async def test_public_dashboard_outbox_caps_payload_bytes(feature: Feature, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IRIAI_PUBLIC_DASHBOARD_MAX_PAYLOAD_BYTES", "1024")
    monkeypatch.setenv("IRIAI_PUBLIC_DASHBOARD_MAX_CONTENT_BYTES", "10")
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    await outbox.mirror_artifact_write(
        source_artifact_id=123,
        feature=feature,
        key="public-summary",
        value="x" * 5000,
    )

    payload = json.loads(str(pool.executes[0][1][5]))
    assert payload["artifact_key"] == "public-summary"
    assert payload["size_bytes"] == 5000
    assert len(json.dumps(payload).encode("utf-8")) < 1024
    assert "content" not in payload or len(payload["content"]) < 200


@pytest.mark.asyncio
async def test_public_dashboard_delete_pending_before_is_feature_scoped_and_bounded() -> None:
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    cutoff = datetime(2026, 5, 11, tzinfo=timezone.utc)

    deleted = await outbox.delete_pending_before(
        cutoff,
        feature_id="feature-1",
        limit=999_999,
    )

    sql, args = pool.fetchvals[0]
    assert deleted == 42
    assert "WHERE status = 'pending' AND feature_id = $1 AND created_at < $2" in sql
    assert "ORDER BY id" in sql
    assert "LIMIT $3" in sql
    assert args == ("feature-1", cutoff, 100_000)


@pytest.mark.asyncio
async def test_public_dashboard_delete_pending_before_global_cleanup_is_bounded() -> None:
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    cutoff = datetime(2026, 5, 11, tzinfo=timezone.utc)

    await outbox.delete_pending_before(cutoff, limit=-5)

    sql, args = pool.fetchvals[0]
    assert "WHERE status = 'pending' AND created_at < $1" in sql
    assert "feature_id" not in sql
    assert "ORDER BY id" in sql
    assert "LIMIT $2" in sql
    assert args == (cutoff, 1)


@pytest.mark.asyncio
async def test_public_dashboard_pending_summary_is_feature_scoped_and_bounded() -> None:
    pool = _PendingSummaryPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    summary = await outbox.pending_summary(feature_id="feature-1")

    sql, args = pool.fetchrows[0]
    normalized = " ".join(sql.split())
    assert summary["pending_count"] == 2
    assert "SELECT payload" not in normalized
    assert "pg_column_size(payload)" in normalized
    assert "WHERE status = 'pending' AND feature_id = $1" in normalized
    assert args == ("feature-1",)


@pytest.mark.asyncio
async def test_public_dashboard_pending_summary_global_is_pending_only_and_bounded() -> None:
    pool = _PendingSummaryPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    await outbox.pending_summary()

    sql, args = pool.fetchrows[0]
    normalized = " ".join(sql.split())
    assert "SELECT payload" not in normalized
    assert "pg_column_size(payload)" in normalized
    assert "WHERE status = 'pending'" in normalized
    assert "feature_id = $1" not in normalized
    assert args == ()


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


@pytest.mark.asyncio
async def test_store_public_dashboard_mirror_timeout_does_not_block_primary_writes(
    feature: Feature,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_PUBLIC_DASHBOARD_MIRROR_TIMEOUT_SECONDS", "0.001")
    pool = _RecordingPool()
    dashboard = _SlowDashboard()
    feature_store = PostgresFeatureStore(pool, public_dashboard=dashboard)  # type: ignore[arg-type]
    artifact_store = PostgresArtifactStore(pool, public_dashboard=dashboard)  # type: ignore[arg-type]

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    await feature_store.log_event(feature.id, "agent_start", "implementer")
    await artifact_store.put("public-summary", {"hello": "world"}, feature=feature)
    elapsed = loop.time() - started_at

    assert elapsed < 0.5
    assert dashboard.event_started is True
    assert dashboard.artifact_started is True
    assert any("INSERT INTO events" in sql for sql, _args in pool.fetchvals)
    assert any("INSERT INTO artifacts" in sql for sql, _args in pool.fetchvals)


# ── Slice 10f (doc 10 step 9) — control_plane.snapshot_changed projection ───


def _typed_snapshot(
    *,
    feature_id: str = "feature-1",
    snapshot_version: str = "v" * 64,
    source: str = "typed",
    degraded: bool = False,
    degradation_reasons: list[str] | None = None,
    with_bodies: bool = False,
) -> ControlPlaneSnapshot:
    """Build a typed `ControlPlaneSnapshot` with summary rows for projection
    tests.

    When ``with_bodies`` is set, the summary rows carry long ``summary`` /
    digest strings so a test can prove the bounded display event NEVER copies
    them across the public boundary.
    """

    now = datetime(2026, 5, 22, 12, tzinfo=timezone.utc)
    long_text = "SECRET-BODY-" + ("z" * 40_000) if with_bodies else "short"
    attempt = ExecutionAttemptSummary(
        attempt_id=11,
        feature_id=feature_id,
        dag_sha256="d" * 64,
        group_idx=3,
        task_id="task-a",
        attempt_kind="merge",
        stage="merge",
        retry=1,
        status="started",
        actor="implementer",
        runtime="claude",
        input_digest=long_text,
        workspace_snapshot_id=2,
        latest_evidence_ids=[5, 6],
        started_at=now,
        finished_at=None,
        updated_at=now,
    )
    failure = TypedFailureSummary(
        failure_id=21,
        attempt_id=11,
        evidence_id=5,
        failure_class="merge_conflict",
        failure_type="rebase_failed",
        severity="error",
        deterministic=True,
        operator_required=False,
        retryable=True,
        status="routed",
        route="retry_merge",
        signature_hash="s" * 16,
        summary=long_text,
        created_at=now,
        resolved_at=None,
    )
    ref = EvidenceRef(
        table="evidence_nodes",
        id=5,
        citation="evidence_nodes#5",
        kind="typed_failure",
        summary=long_text,
        artifact_key="dag-commit-failure:3",
    )
    return ControlPlaneSnapshot(
        feature_id=feature_id,
        snapshot_version=snapshot_version,
        generated_at=now,
        source=source,  # type: ignore[arg-type]
        degraded=degraded,
        degradation_reasons=degradation_reasons or [],
        active_group_idx=3,
        active_attempts=[attempt],
        latest_failures=[failure],
        merge_queue=[],
        recommended_route="retry_merge",
        recommended_action="recommend",
        evidence_refs=[ref],
    )


class _SnapshotStore:
    """Fake typed `ExecutionControlStore` exposing the two Slice-10a reads."""

    def __init__(
        self,
        versions: list[str],
        snapshot: ControlPlaneSnapshot,
    ) -> None:
        self._versions = list(versions)
        self._snapshot = snapshot
        self.version_calls = 0
        self.snapshot_calls = 0

    async def get_control_plane_snapshot_version(self, feature_id: str) -> str:
        self.version_calls += 1
        if len(self._versions) > 1:
            return self._versions.pop(0)
        return self._versions[0]

    async def get_control_plane_snapshot(self, query: object) -> ControlPlaneSnapshot:
        self.snapshot_calls += 1
        return self._snapshot


def test_control_plane_snapshot_changed_payload_is_summary_only() -> None:
    """doc 10 step 9: the display payload carries counts/route/citations only —
    never an artifact body, a prompt, stdout/stderr, or an unbounded list."""

    snapshot = _typed_snapshot(with_bodies=True)
    payload = control_plane_snapshot_changed_payload(snapshot)

    # Visible counters are present and reflect the typed summary list lengths.
    assert payload["counters"]["active_attempts"] == 1
    assert payload["counters"]["latest_failures"] == 1
    assert payload["counters"]["merge_queue"] == 0
    assert payload["counters"]["evidence_refs"] == 1
    # Route + version + feature id are carried.
    assert payload["feature_id"] == "feature-1"
    assert payload["snapshot_version"] == "v" * 64
    assert payload["recommended_route"] == "retry_merge"
    assert payload["recommended_action"] == "recommend"

    # No typed summary list body crosses the boundary — only counts.
    for body_field in (
        "active_attempts",
        "latest_failures",
        "workspace_snapshots",
        "merge_queue",
        "retry_budgets",
        "sandbox_leases",
        "runtime_bindings",
        "gates",
        "checkpoints",
    ):
        assert body_field not in payload, body_field

    # The 40k-char body text appears NOWHERE in the serialized payload.
    serialized = json.dumps(payload, default=str)
    assert "SECRET-BODY" not in serialized
    assert "z" * 200 not in serialized
    # `EvidenceRef`s are cited (id + citation + kind) but the body summary and
    # the artifact key are NOT projected.
    assert payload["evidence_refs"] == [
        {"table": "evidence_nodes", "id": 5, "citation": "evidence_nodes#5", "kind": "typed_failure"}
    ]
    assert "artifact_key" not in payload["evidence_refs"][0]
    assert "summary" not in payload["evidence_refs"][0]


def test_control_plane_snapshot_changed_payload_bounds_evidence_refs() -> None:
    """The cited-evidence list is bounded — a large typed snapshot cannot push
    an unbounded ref list onto the external surface."""

    snapshot = _typed_snapshot()
    many_refs = [
        EvidenceRef(table="evidence_nodes", id=i, citation=f"evidence_nodes#{i}")
        for i in range(CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS + 25)
    ]
    snapshot = snapshot.model_copy(update={"evidence_refs": many_refs})
    payload = control_plane_snapshot_changed_payload(snapshot)

    assert len(payload["evidence_refs"]) == CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS
    # The count still reports the true total even though the list is bounded.
    assert payload["counters"]["evidence_refs"] == CONTROL_PLANE_EVENT_MAX_EVIDENCE_REFS + 25


def test_control_plane_snapshot_event_id_is_keyed_on_feature_and_version() -> None:
    """doc 10: the idempotency key is (feature_id, snapshot_version,
    "control_plane.snapshot_changed")."""

    key_a = control_plane_snapshot_event_id("feature-1", "ver-a")
    key_a_again = control_plane_snapshot_event_id("feature-1", "ver-a")
    key_b = control_plane_snapshot_event_id("feature-1", "ver-b")
    key_other_feature = control_plane_snapshot_event_id("feature-2", "ver-a")

    assert key_a == key_a_again
    assert key_a != key_b
    assert key_a != key_other_feature


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_emits_bounded_public_event() -> None:
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)

    event_id = await outbox.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(with_bodies=True),
    )

    assert event_id == control_plane_snapshot_event_id("feature-1", "v" * 64)
    assert len(pool.executes) == 1
    sql, args = pool.executes[0]
    assert "INSERT INTO public_dashboard_outbox" in sql
    assert "ON CONFLICT (event_id) DO NOTHING" in sql
    assert args[0] == event_id
    assert args[1] == "feature-1"
    assert args[2] == CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT
    assert args[4] == "public"
    payload = json.loads(str(args[5]))
    assert payload["snapshot_version"] == "v" * 64
    assert payload["counters"]["active_attempts"] == 1
    # The bounded display event carries no artifact body.
    assert "SECRET-BODY" not in str(args[5])


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_is_noop_when_outbox_disabled() -> None:
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=False)

    event_id = await outbox.project_control_plane_snapshot_changed(
        feature_id="feature-1",
        snapshot=_typed_snapshot(),
    )

    assert event_id is None
    assert pool.executes == []


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_fails_closed_on_enqueue_error() -> None:
    """doc 10 § "Edge Cases": a configured-outbox enqueue failure is NOT
    ignorable — it RAISES so the projection transaction cannot half-commit."""

    outbox = PublicDashboardOutbox(_FailingPool(), outbox_enabled=True)

    with pytest.raises(RuntimeError, match="dashboard table missing"):
        await outbox.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=_typed_snapshot(),
        )


@pytest.mark.asyncio
async def test_project_control_plane_snapshot_changed_rejects_empty_version() -> None:
    """A snapshot with no typed version digest has nothing to key idempotency
    on — fail closed rather than enqueue an un-deduplicable public event."""

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    empty_version = _typed_snapshot().model_copy(update={"snapshot_version": ""})

    with pytest.raises(ValueError, match="non-empty snapshot_version"):
        await outbox.project_control_plane_snapshot_changed(
            feature_id="feature-1",
            snapshot=empty_version,
        )
    assert pool.executes == []


@pytest.mark.asyncio
async def test_project_snapshot_if_changed_emits_event_when_version_advances() -> None:
    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    snapshot = _typed_snapshot(snapshot_version="new-version-digest")
    store = _SnapshotStore(["new-version-digest"], snapshot)

    event_id = await project_control_plane_snapshot_if_changed(
        outbox,
        store,
        "feature-1",
        previous_snapshot_version="old-version-digest",
    )

    assert event_id == control_plane_snapshot_event_id(
        "feature-1", "new-version-digest"
    )
    assert store.version_calls == 1
    assert store.snapshot_calls == 1
    assert len(pool.executes) == 1
    assert pool.executes[0][1][2] == CONTROL_PLANE_SNAPSHOT_CHANGED_EVENT


@pytest.mark.asyncio
async def test_project_snapshot_if_changed_is_noop_when_version_unchanged() -> None:
    """No public event is enqueued when the typed snapshot version is the same
    — and the bounded snapshot read is skipped entirely."""

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    snapshot = _typed_snapshot(snapshot_version="stable-version")
    store = _SnapshotStore(["stable-version"], snapshot)

    event_id = await project_control_plane_snapshot_if_changed(
        outbox,
        store,
        "feature-1",
        previous_snapshot_version="stable-version",
    )

    assert event_id is None
    assert store.version_calls == 1
    assert store.snapshot_calls == 0  # the bounded snapshot read is skipped
    assert pool.executes == []


@pytest.mark.asyncio
async def test_project_snapshot_if_changed_is_idempotent_on_same_version() -> None:
    """doc 10: re-projecting the same advanced version does not enqueue a
    duplicate public notification (ON CONFLICT (event_id) DO NOTHING)."""

    pool = _RecordingPool()
    outbox = PublicDashboardOutbox(pool, outbox_enabled=True)
    snapshot = _typed_snapshot(snapshot_version="advanced-version")
    store = _SnapshotStore(["advanced-version"], snapshot)

    first = await project_control_plane_snapshot_if_changed(
        outbox, store, "feature-1", previous_snapshot_version="prior",
    )
    # A caller that does not yet know the new version re-drives the projection.
    second = await project_control_plane_snapshot_if_changed(
        outbox, store, "feature-1", previous_snapshot_version="prior",
    )

    assert first == second
    # Both writes use the SAME idempotency event_id — Postgres
    # ON CONFLICT DO NOTHING collapses them to a single public notification.
    assert pool.executes[0][1][0] == pool.executes[1][1][0]
    assert all(
        "ON CONFLICT (event_id) DO NOTHING" in sql for sql, _ in pool.executes
    )


@pytest.mark.asyncio
async def test_project_snapshot_if_changed_is_noop_when_outbox_is_none() -> None:
    snapshot = _typed_snapshot()
    store = _SnapshotStore(["any-version"], snapshot)

    event_id = await project_control_plane_snapshot_if_changed(
        None, store, "feature-1", previous_snapshot_version=None,
    )

    assert event_id is None
    assert store.version_calls == 0


@pytest.mark.asyncio
async def test_project_snapshot_if_changed_fails_closed_on_enqueue_error() -> None:
    """A configured-outbox enqueue failure propagates through the driver too,
    so the caller's projection transaction aborts (doc 10 § "Edge Cases")."""

    outbox = PublicDashboardOutbox(_FailingPool(), outbox_enabled=True)
    snapshot = _typed_snapshot(snapshot_version="advanced")
    store = _SnapshotStore(["advanced"], snapshot)

    with pytest.raises(RuntimeError, match="dashboard table missing"):
        await project_control_plane_snapshot_if_changed(
            outbox, store, "feature-1", previous_snapshot_version="prior",
        )
