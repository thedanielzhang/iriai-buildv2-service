from __future__ import annotations

from datetime import datetime, timezone
import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.app import SupervisorApp, compact_observation_digest
from iriai_build_v2.supervisor.evidence import (
    KEY_PREFIXES,
    _candidate_keys,
    _load_control_plane_failure_events,
    build_current_workflow_snapshot,
    collect_evidence,
    probe_bridge,
    probe_worktree,
)
from iriai_build_v2.supervisor.models import (
    ArtifactRecord,
    BridgeProbe,
    EventRecord,
    FailureClass,
    SupervisorObservation,
)


class _FeatureStore:
    def __init__(self) -> None:
        self.feature = SimpleNamespace(
            id="feat-1",
            name="Feature",
            slug="feature",
            workflow_name="develop",
            workspace_id="main",
            metadata={"_db_phase": "implementation"},
        )
        self.events = [
            {"id": 10, "event_type": "phase_start", "source": "runner", "content": "old"},
            {"id": 11, "event_type": "dag_commit_failed", "source": "runner", "content": "new"},
        ]

    async def get_feature(self, feature_id: str):
        assert feature_id == "feat-1"
        return self.feature

    async def get_events(self, feature_id: str):
        assert feature_id == "feat-1"
        return list(self.events)

    async def list_event_summaries(
        self,
        feature_id: str,
        *,
        after_id: int = 0,
        limit: int = 50,
        order: str = "asc",
        group_idx: int | None = None,
        preview_chars: int = 700,
    ):
        assert feature_id == "feat-1"
        del group_idx, preview_chars
        rows = [row for row in self.events if row["id"] > after_id]
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return rows[:limit]


class _ArtifactStore:
    def __init__(self) -> None:
        self.writes = []

    async def list_records(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ):
        assert feature_id == "feat-1"
        rows = [
            {
                "id": 9,
                "key": "dag-verify:g1:initial",
                "value": {"status": "failed", "summary": "old"},
            },
            {
                "id": 12,
                "key": "dag-commit-failure:g1:retry-0",
                "value": "WorkflowCommitError in repos/app/src/App.test.tsx",
            },
            {"id": 13, "key": "unrelated", "value": "ignored-by-fake"},
        ]
        rows = [row for row in rows if row["id"] > after_id]
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return rows[:limit]

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ):
        rows = await self.list_records(
            feature_id=feature_id,
            prefixes=prefixes,
            after_id=after_id,
            limit=limit,
            order=order,
        )
        summaries = []
        for row in rows:
            value = row.get("value", "")
            preview = value if isinstance(value, str) else str(value)
            summaries.append(
                {
                    "id": row["id"],
                    "key": row["key"],
                    "created_at": row.get("created_at"),
                    "stored_bytes": len(preview.encode("utf-8")),
                    "value_preview": preview[:700],
                    "summary_only": True,
                }
            )
        return summaries

    async def put(self, key: str, value: str, *, feature):
        self.writes.append((key, value, feature))


class _SummaryArtifactStore(_ArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.list_records_called = False
        self.list_summaries_called = False

    async def list_records(self, **kwargs):
        self.list_records_called = True
        raise AssertionError("background evidence should use summary rows")

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "asc",
    ):
        self.list_summaries_called = True
        rows = [
            {
                "id": 12,
                "key": "dag-commit-failure:g1:retry-0",
                "stored_bytes": 155 * 1024 * 1024,
                "summary_only": True,
            }
        ]
        return [row for row in rows if row["id"] > after_id][:limit]


class _DashboardClient:
    async def get_json(self, path: str, params=None):
        if path == "/api/bridge/status":
            return {"state": "running", "pid": 123}
        if path == "/api/bridge/logs":
            assert params == {"after": 4}
            return {"cursor": 6, "lines": ["ok", "Traceback: reconnect failed"]}
        raise AssertionError(path)

    async def post_json(self, path: str, payload=None):
        raise AssertionError("post should not be used by evidence collection")


def test_supervisor_evidence_prefixes_include_repair_and_rca_artifacts():
    assert "dag-authority-gate:" in KEY_PREFIXES
    assert "dag-direct-repair-route:" in KEY_PREFIXES
    assert "dag-repair-expanded-verify:" in KEY_PREFIXES
    assert "dag-repair-lens:" in KEY_PREFIXES
    assert "dag-verify-rca:" in KEY_PREFIXES
    assert "dag-repair-dispatch:" in KEY_PREFIXES
    assert "dag-fix:" in KEY_PREFIXES
    assert "dag-runtime-failure:" in KEY_PREFIXES
    assert "dag-task-contract:" in KEY_PREFIXES
    assert "dag-contract-verdict:" in KEY_PREFIXES
    assert "dag-sandbox-patch:" in KEY_PREFIXES
    assert "dag-worktree-alias-preflight:" in KEY_PREFIXES
    assert "dag-worktree-alias-canonicalization:" in KEY_PREFIXES
    assert "dag-workspace-acl-normalization:" in KEY_PREFIXES
    assert "dag-workspace-permission-repair:" in KEY_PREFIXES
    assert "runtime-workspace-binding:" in KEY_PREFIXES
    assert "dag-runtime-workspace-binding:" in KEY_PREFIXES
    assert "workspace-authority-" in KEY_PREFIXES
    assert "workflow-blocker:" in KEY_PREFIXES


def test_candidate_keys_include_worktree_alias_fallback_artifacts():
    feature = SimpleNamespace(
        metadata={
            "supervisor_groups": [48],
            "supervisor_retries": ["retry-1"],
            "supervisor_task_ids": ["TASK-contract"],
            "supervisor_repo_ids": ["app"],
        }
    )

    keys = _candidate_keys(feature)

    assert "dag-task-contract:TASK-contract" in keys
    assert "dag-contract-verdict:g48:TASK-contract:retry-1" in keys
    assert "dag-sandbox-patch:g48:retry-1:repo-app" in keys
    assert "dag-verify-graph:g48:retry-1" in keys
    assert "dag-worktree-alias-preflight:g48:initial-dispatch" in keys
    assert "dag-worktree-alias-preflight:g48:retry-1" in keys
    assert "dag-worktree-alias-canonicalization:g48:retry-1" in keys
    assert "dag-runtime-failure:g48:verify-retry-1" in keys
    assert "dag-runtime-failure:g48:rca-retry-1" in keys
    assert "workspace-authority-registry:g48" in keys
    assert "workspace-authority-preflight:g48:initial-dispatch" in keys
    assert "workspace-authority-routes:g48:retry-1" in keys
    assert "workspace-authority-snapshot:g48:retry-1" in keys
    assert "workflow-blocker:verify" in keys
    assert "dag-runtime-failure:source-push" in keys


@pytest.mark.asyncio
async def test_runtime_failure_sql_fallback_preserves_nested_route_and_product_evidence():
    now = datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc)

    class RuntimeFailurePool:
        def __init__(self) -> None:
            self.query = ""

        async def fetch(self, query: str, *args: object):
            self.query = query
            assert args == ("feat-1", 40)
            return [
                {
                    "id": 7001,
                    "attempt_id": 17,
                    "group_idx": 48,
                    "stage": "verify",
                    "name": "contract_violation/product_assertion",
                    "status": "blocked",
                    "deterministic": True,
                    "source_ref": "attempt:17",
                    "summary": "canonical product file assertion failed",
                    "summary_length": 40,
                    "summary_bytes": 40,
                    "route_decision": json.dumps(
                        {
                            "route": "product_repair",
                            "failure_class": "contract_violation",
                            "canonical_product_files": ["src/product/Widget.tsx"],
                            "deterministic": False,
                            "operator_required": True,
                            "product_evidence": True,
                            "retryable": False,
                        }
                    ),
                    "retry_budget": json.dumps(
                        {
                            "route": "retry_verifier",
                            "remaining_attempts": 2,
                        }
                    ),
                    "canonical_product_files": json.dumps(["src/product/Widget.tsx"]),
                    "product_evidence": True,
                    "failure_class": "contract_violation",
                    "failure_type": "product_assertion",
                    "route": "product_repair",
                    "operator_required": False,
                    "retryable": True,
                    "created_at": now,
                }
            ]

    pool = RuntimeFailurePool()
    artifact_store = SimpleNamespace(_pool=pool)

    events = await _load_control_plane_failure_events(
        artifact_store,
        "feat-1",
    )

    normalized_query = " ".join(pool.query.split())
    assert "payload, payload->>'failure_class'" not in normalized_query
    assert "payload->'route_decision' AS route_decision" in normalized_query
    assert "payload->'retry_budget' AS retry_budget" in normalized_query
    assert "COALESCE((payload->>'operator_required')::boolean, FALSE)" not in normalized_query
    assert len(events) == 1
    metadata = events[0].metadata
    assert metadata["route_decision"]["canonical_product_files"] == [
        "src/product/Widget.tsx"
    ]
    assert metadata["retry_budget"]["remaining_attempts"] == 2
    assert metadata["deterministic"] is False
    assert metadata["operator_required"] is True
    assert metadata["retryable"] is False
    assert metadata["canonical_product_files"] == ["src/product/Widget.tsx"]
    assert metadata["product_evidence"] is True


@pytest.mark.asyncio
async def test_collect_evidence_uses_fake_stores_and_bridge_client():
    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        cursor=10,
        dashboard_client=_DashboardClient(),
        bridge_log_cursor=4,
    )

    assert observation.feature is not None
    assert observation.phase == "implementation"
    assert [event.id for event in observation.events] == [11]
    assert [artifact.id for artifact in observation.artifacts] == [9, 12]
    assert observation.next_event_cursor == 11
    assert observation.next_artifact_cursor == 12
    assert observation.next_cursor == 12
    assert observation.bridge is not None
    assert observation.bridge.ok is True
    assert observation.bridge.log_cursor == 6
    assert observation.bridge.errors == ["Traceback: reconnect failed"]


@pytest.mark.asyncio
async def test_collect_evidence_uses_summary_artifact_rows_for_background_scans():
    store = _SummaryArtifactStore()

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=store,
        cursor=10,
    )

    assert store.list_summaries_called is True
    assert store.list_records_called is False
    assert observation.artifacts[0].summary_only is True
    assert observation.artifacts[0].value == ""
    assert observation.artifacts[0].stored_bytes == 155 * 1024 * 1024


@pytest.mark.asyncio
async def test_collect_evidence_fails_closed_for_legacy_list_artifacts_store():
    class _LegacyListArtifactsStore:
        async def list_artifacts(self, *args, **kwargs):
            raise AssertionError("summary evidence must not call legacy value-hydrating list_artifacts")

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_LegacyListArtifactsStore(),
        cursor=10,
    )

    assert observation.artifacts == []


@pytest.mark.asyncio
async def test_collect_evidence_marks_unbounded_legacy_list_records_store_blocking():
    class _LegacyListRecordsOnlyStore:
        async def list_records(self, **kwargs):
            raise AssertionError("unbounded legacy list_records must not hydrate artifact bodies")

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_LegacyListRecordsOnlyStore(),
        cursor=10,
    )

    assert len(observation.artifacts) == 1
    artifact = observation.artifacts[0]
    assert artifact.key == "workflow-blocker:artifact-evidence-unsupported"
    assert artifact.summary_only is True
    assert artifact.value == ""
    assert artifact.value_preview is not None
    assert "legacy_artifact_projection_unbounded" in artifact.value_preview


@pytest.mark.asyncio
async def test_collect_evidence_ignores_missing_control_plane_failure_relation():
    class _MissingRelationPool:
        async def fetch(self, sql: str, *args):
            del sql, args
            raise RuntimeError("relation evidence_nodes does not exist")

    class _PoolOnlyArtifactStore(_ArtifactStore):
        def __init__(self) -> None:
            super().__init__()
            self._pool = _MissingRelationPool()

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_PoolOnlyArtifactStore(),
        cursor=10,
    )

    assert all(event.event_type != "control_plane_runtime_failure" for event in observation.events)


@pytest.mark.asyncio
async def test_collect_evidence_pool_summary_fallback_is_bounded():
    class _Pool:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, sql: str, *args):
            self.calls.append((sql, args))
            if "FROM evidence_nodes" in sql:
                return []
            return [
                {
                    "id": 12,
                    "key": "dag-runtime-failure:source-push",
                    "created_at": datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc),
                    "stored_bytes": 52,
                    "value_preview": '{"failure_type":"source_push_failed"}',
                    "summary_only": True,
                }
            ]

    class _PoolOnlyArtifactStore:
        def __init__(self) -> None:
            self._pool = _Pool()

    store = _PoolOnlyArtifactStore()

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=store,
        cursor=10,
    )

    sql, args = store._pool.calls[0]
    normalized_query = " ".join(sql.split())
    assert "SELECT id, key, created_at, value FROM artifacts" not in normalized_query
    assert "pg_column_size(value)::bigint AS stored_bytes" in normalized_query
    assert "substring(value from 1 for 2000) AS value_preview" in normalized_query
    assert "TRUE AS summary_only" in normalized_query
    assert "LIMIT $3" in sql
    assert args[0] == "feat-1"
    assert args[1] == 10
    assert args[2] == 500
    assert len(args) > 3
    assert observation.artifacts[0].key == "dag-runtime-failure:source-push"
    assert observation.artifacts[0].summary_only is True
    assert observation.artifacts[0].value_preview == '{"failure_type":"source_push_failed"}'


@pytest.mark.asyncio
async def test_collect_evidence_control_plane_failures_do_not_advance_event_cursor():
    class _ControlPlaneStore(_ArtifactStore):
        async def list_control_plane_failure_summaries(self, *, feature_id: str, limit: int):
            assert feature_id == "feat-1"
            assert limit == 40
            return [
                {
                    "id": 999,
                    "attempt_id": 12,
                    "group_idx": 3,
                    "name": "runtime_provider/provider_rate_limited",
                    "summary": "provider limited",
                    "summary_length": 16,
                    "summary_bytes": 16,
                    "payload": (
                        '{"failure_class":"runtime_provider",'
                        '"failure_type":"provider_rate_limited",'
                        '"route":"retry_verifier",'
                        '"status":"resolved",'
                        '"deterministic":true,'
                        '"operator_required":false,"retryable":true}'
                    ),
                }
            ]

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_ControlPlaneStore(),
        cursor=11,
    )

    assert observation.next_event_cursor == 11
    assert observation.events
    control_plane_event = observation.events[0]
    assert control_plane_event.id is None
    assert control_plane_event.metadata["evidence_node_id"] == 999
    assert control_plane_event.metadata["failure_class"] == "runtime_provider"
    assert control_plane_event.metadata["deterministic"] is True
    assert control_plane_event.metadata["summary_length"] == 16
    assert control_plane_event.metadata["summary_bytes"] == 16
    assert control_plane_event.metadata["summary_truncated"] is False
    assert control_plane_event.metadata["failure_type"] == "provider_rate_limited"
    assert control_plane_event.metadata["route"] == "retry_verifier"
    assert control_plane_event.metadata["status"] == "resolved"


@pytest.mark.asyncio
async def test_supervisor_app_persists_compact_observation_digest():
    store = _SummaryArtifactStore()
    app = SupervisorApp(
        feature_store=_FeatureStore(),
        artifact_store=store,
    )

    await app.run_once(feature_id="feat-1", cursor=10)

    observation_write = next(
        (value for key, value, _feature in store.writes if key.startswith("supervisor-observation:")),
        None,
    )
    assert observation_write is not None
    assert len(observation_write.encode("utf-8")) < 64 * 1024
    assert '"kind":"supervisor-observation-digest"' in observation_write
    assert '"artifacts":' not in observation_write
    assert '"artifact_refs":' in observation_write


def test_compact_observation_digest_caps_large_raw_evidence():
    large_observation = SupervisorObservation(
        feature_id="feat-1",
        artifacts=[
            ArtifactRecord(
                id=idx,
                key=f"dag-fix:g1:retry-{idx}",
                value="x" * 1_000_000,
            )
            for idx in range(120)
        ],
        bridge=BridgeProbe(
            ok=True,
            log_cursor=99,
            log_lines=["line " + ("x" * 2000)] * 1000,
            errors=["error " + ("x" * 2000)] * 300,
        ),
    )

    digest = compact_observation_digest(large_observation)
    payload = digest.model_dump_json()

    assert len(payload.encode("utf-8")) < 64 * 1024
    assert digest.source_observation_artifact_count == 120
    assert digest.truncated is True
    assert "x" * 1000 not in payload


@pytest.mark.asyncio
async def test_collect_evidence_keeps_event_and_artifact_cursors_separate():
    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        event_cursor=10,
        artifact_cursor=1_358_000,
    )

    assert [event.id for event in observation.events] == [11]
    assert [artifact.id for artifact in observation.artifacts] == [9, 12]
    assert observation.next_event_cursor == 11
    assert observation.next_artifact_cursor == 1_358_000
    assert observation.next_cursor == 1_358_000


@pytest.mark.asyncio
async def test_collect_evidence_fetches_latest_artifacts_on_fresh_restart():
    class FreshRestartStore(_ArtifactStore):
        async def list_record_summaries(
            self,
            *,
            feature_id: str,
            prefixes,
            after_id: int,
            limit: int = 500,
            order: str = "asc",
        ):
            assert feature_id == "feat-1"
            rows = [
                {
                    "id": artifact_id,
                    "key": "dag-verify:g34:retry-0",
                    "value": {"approved": True},
                }
                for artifact_id in range(1, 601)
            ]
            rows.append(
                {
                    "id": 700,
                    "key": "dag-writeability-preflight:g39:initial",
                    "value_preview": '{"status":"ok"}',
                    "stored_bytes": 15,
                    "summary_only": True,
                }
            )
            rows = [row for row in rows if row["id"] > after_id]
            rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
            return [
                {
                    "id": row["id"],
                    "key": row["key"],
                    "value_preview": row.get("value_preview", '{"approved": true}'),
                    "stored_bytes": row.get("stored_bytes", 17),
                    "summary_only": True,
                }
                for row in rows[:limit]
            ]

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=FreshRestartStore(),
        cursor=0,
    )

    assert any(artifact.key == "dag-writeability-preflight:g39:initial" for artifact in observation.artifacts)
    assert observation.current is not None
    assert observation.current.group_idx == 39
    assert observation.current.source == "artifact"


@pytest.mark.asyncio
async def test_collect_evidence_surfaces_workspace_runtime_summary_previews():
    class WorkspaceRuntimeSummaryStore(_ArtifactStore):
        async def list_records(self, **kwargs):
            raise AssertionError("summary-capable evidence path must not hydrate values")

        async def list_record_summaries(
            self,
            *,
            feature_id: str,
            prefixes,
            after_id: int,
            limit: int = 500,
            order: str = "asc",
        ):
            assert feature_id == "feat-1"
            del limit
            rows = [
                {
                    "id": 700,
                    "key": "dag-workspace-acl-normalization:g48:repair-dispatch-retry-0",
                    "value_preview": '{"status":"blocked","operator_required":true}',
                    "stored_bytes": 45,
                    "summary_only": True,
                },
                {
                    "id": 701,
                    "key": "runtime-workspace-binding:g48:lease-1",
                    "value_preview": '{"status":"bound","cwd":"/tmp/ws"}',
                    "stored_bytes": 34,
                    "summary_only": True,
                },
                {
                    "id": 702,
                    "key": "dag-runtime-failure:g48:verify-initial",
                    "value_preview": (
                        '{"failure_class":"verifier_provider",'
                        '"blocked_before_product_repair":true}'
                    ),
                    "stored_bytes": 82,
                    "summary_only": True,
                },
                {
                    "id": 703,
                    "key": "workflow-blocker:verify",
                    "value_preview": (
                        '{"reason":"SANDBOX_WORKFLOW_BLOCKER: verifier sandbox blocked"}'
                    ),
                    "stored_bytes": 67,
                    "summary_only": True,
                },
                {
                    "id": 704,
                    "key": "unrelated-control-plane",
                    "value_preview": "ignored",
                    "stored_bytes": 7,
                    "summary_only": True,
                },
            ]
            filtered = [
                row
                for row in rows
                if row["id"] > after_id
                and any(row["key"].startswith(prefix) for prefix in prefixes)
            ]
            return sorted(
                filtered,
                key=lambda row: row["id"],
                reverse=(order == "desc"),
            )

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=WorkspaceRuntimeSummaryStore(),
        cursor=0,
    )

    previews = {artifact.key: artifact.value_preview for artifact in observation.artifacts}
    assert previews[
        "dag-workspace-acl-normalization:g48:repair-dispatch-retry-0"
    ] == '{"status":"blocked","operator_required":true}'
    assert previews["runtime-workspace-binding:g48:lease-1"] == '{"status":"bound","cwd":"/tmp/ws"}'
    assert previews["dag-runtime-failure:g48:verify-initial"] == (
        '{"failure_class":"verifier_provider","blocked_before_product_repair":true}'
    )
    assert previews["workflow-blocker:verify"] == (
        '{"reason":"SANDBOX_WORKFLOW_BLOCKER: verifier sandbox blocked"}'
    )
    assert "unrelated-control-plane" not in previews


@pytest.mark.asyncio
async def test_collect_evidence_uses_latest_event_window_on_long_history():
    class LongHistoryFeatureStore(_FeatureStore):
        def __init__(self) -> None:
            super().__init__()
            self.events = [
                {
                    "id": event_id,
                    "event_type": "dag_verify_finish",
                    "source": "runner",
                    "content": f"event {event_id}",
                }
                for event_id in range(1, 601)
            ]

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=LongHistoryFeatureStore(),
        artifact_store=_ArtifactStore(),
        cursor=595,
    )

    assert [event.id for event in observation.events] == [596, 597, 598, 599, 600]
    assert observation.next_event_cursor == 600


@pytest.mark.asyncio
async def test_collect_evidence_fresh_restart_uses_latest_event_window_on_long_history():
    class LongHistoryFeatureStore(_FeatureStore):
        def __init__(self) -> None:
            super().__init__()
            self.events = [
                {
                    "id": event_id,
                    "event_type": "dag_task_start",
                    "source": f"implementer-g{1 if event_id < 600 else 44}-t1",
                    "metadata": {"group_idx": 1 if event_id < 600 else 44},
                    "content": f"event {event_id}",
                }
                for event_id in range(1, 601)
            ]

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=LongHistoryFeatureStore(),
        artifact_store=_ArtifactStore(),
        cursor=0,
    )

    assert observation.events[0].id == 101
    assert observation.events[-1].id == 600
    assert observation.next_event_cursor == 600
    assert observation.current is not None
    assert observation.current.latest_event_id == 600
    assert observation.current.group_idx == 44


@pytest.mark.asyncio
async def test_collect_evidence_legacy_get_events_fallback_preserves_latest_resume_window():
    class LegacyEventStore:
        def __init__(self) -> None:
            self.feature = SimpleNamespace(
                id="feat-1",
                name="Feature",
                slug="feature",
                workflow_name="develop",
                workspace_id="main",
                metadata={"_db_phase": "implementation"},
            )
            self.events = [
                {
                    "id": event_id,
                    "event_type": "dag_task_start",
                    "source": f"implementer-g{1 if event_id < 600 else 45}-t1",
                    "metadata": {"group_idx": 1 if event_id < 600 else 45},
                    "content": f"legacy event {event_id}",
                }
                for event_id in range(1, 601)
            ]

        async def get_feature(self, feature_id: str):
            assert feature_id == "feat-1"
            return self.feature

        async def get_events(self, feature_id: str):
            assert feature_id == "feat-1"
            return list(self.events)

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=LegacyEventStore(),
        artifact_store=_ArtifactStore(),
        cursor=0,
    )

    assert observation.events[0].id == 101
    assert observation.events[-1].id == 600
    assert observation.current is not None
    assert observation.current.group_idx == 45


@pytest.mark.asyncio
async def test_probe_bridge_reports_client_errors():
    class BrokenClient:
        async def get_json(self, path: str, params=None):
            raise RuntimeError("dashboard unavailable")

        async def post_json(self, path: str, payload=None):
            raise AssertionError("unused")

    probe = await probe_bridge(client=BrokenClient())

    assert probe.ok is False
    assert probe.process_state == "unreachable"
    assert probe.errors == ["RuntimeError: dashboard unavailable"]


@pytest.mark.asyncio
async def test_probe_bridge_caps_logs_and_errors_at_probe_time():
    class LargeLogClient:
        async def get_json(self, path: str, params=None):
            if path == "/api/bridge/status":
                return {"state": "running", "pid": 123}
            if path == "/api/bridge/logs":
                assert params == {"after": 0}
                lines = [
                    f"info {idx} " + ("x" * 2000)
                    for idx in range(450)
                ] + [
                    f"Traceback error {idx} " + ("y" * 2000)
                    for idx in range(150)
                ]
                return {"cursor": 999, "lines": lines}
            raise AssertionError(path)

        async def post_json(self, path: str, payload=None):
            raise AssertionError("unused")

    probe = await probe_bridge(client=LargeLogClient())

    assert probe.ok is True
    assert probe.log_cursor == 999
    assert len(probe.log_lines) == 500
    assert len(probe.errors) == 100
    assert probe.truncated_log_line_count == 100
    assert probe.truncated_error_count == 50
    assert all(len(line) <= 1000 for line in probe.log_lines)
    assert all(len(line) <= 1000 for line in probe.errors)


def test_probe_worktree_finds_hygiene_and_forbidden_paths(tmp_path: Path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test User")
    legacy = tmp_path / "legacy/chat/Old.tsx"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("old", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src/_pending_fix.ts").write_text("pending", encoding="utf-8")
    (tmp_path / "src/New.tsx.PROPOSED").write_text("proposed", encoding="utf-8")
    embedded = tmp_path / "packages/embedded/.git"
    embedded.mkdir(parents=True)
    _git(tmp_path, "add", "legacy/chat/Old.tsx")
    _git(tmp_path, "commit", "-m", "initial")
    legacy.write_text("dirty", encoding="utf-8")

    probe = probe_worktree(tmp_path, forbidden_paths=["legacy/chat/Old.tsx"])

    assert probe.ok is True
    assert "packages/embedded/.git" in probe.embedded_git_paths
    assert "src/_pending_fix.ts" in probe.pending_paths
    assert "src/New.tsx.PROPOSED" in probe.proposed_paths
    assert any(fact.path == "legacy/chat/Old.tsx" for fact in probe.forbidden_paths)
    assert any(fact.path == "legacy/chat/Old.tsx" for fact in probe.dirty_paths)


def test_current_workflow_snapshot_scopes_active_agents_to_current_group():
    snapshot = build_current_workflow_snapshot(
        events=[
            EventRecord(
                id=100,
                event_type="agent_invocation_start",
                source="architect-subfeature:backend-foundation-setup-architecture",
            ),
            EventRecord(
                id=200,
                event_type="agent_invocation_start",
                source="implementer-g39-t15-a0",
            ),
        ],
        artifacts=[],
        bridge=None,
        phase="implementation",
    )

    assert snapshot.group_idx == 39
    assert snapshot.state == "implementing"
    assert snapshot.active_agents == ["implementer-g39-t15-a0"]


def test_current_workflow_snapshot_orders_idless_control_plane_events_by_recency():
    snapshot = build_current_workflow_snapshot(
        events=[
            EventRecord(
                id=600,
                event_type="dag_task_start",
                source="implementer-g39-t1-a0",
                metadata={"group_idx": 39},
                created_at=datetime(2026, 5, 20, 15, 0, tzinfo=timezone.utc),
            ),
            EventRecord(
                id=None,
                event_type="control_plane_runtime_failure",
                source="execution_control",
                content="workspace permission blocked",
                metadata={"group_idx": 48, "evidence_node_id": 999},
                created_at=datetime(2026, 5, 20, 15, 5, tzinfo=timezone.utc),
            ),
        ],
        artifacts=[],
        bridge=None,
        phase="implementation",
    )

    assert snapshot.group_idx == 48
    assert snapshot.state == "workflow_blocked"
    assert snapshot.latest_event_id == 600


def test_current_workflow_snapshot_counts_invocations_not_queued_agent_starts():
    snapshot = build_current_workflow_snapshot(
        events=[
            EventRecord(
                id=100,
                event_type="agent_start",
                source="implementer-g39-t1-a0",
                metadata={"group_idx": 39},
            ),
            EventRecord(
                id=101,
                event_type="agent_invocation_start",
                source="implementer-g39-t2-a0",
                metadata={"group_idx": 39},
            ),
            EventRecord(
                id=102,
                event_type="agent_invocation_start",
                source="implementer-g39-t3-a0",
                metadata={"group_idx": 39},
            ),
            EventRecord(
                id=103,
                event_type="agent_error",
                source="implementer-g39-t3-a0",
                metadata={"group_idx": 39},
            ),
        ],
        artifacts=[],
        bridge=None,
        phase="implementation",
    )

    assert snapshot.group_idx == 39
    assert snapshot.state == "implementing"
    assert snapshot.active_agents == ["implementer-g39-t2-a0"]


@pytest.mark.asyncio
async def test_supervisor_app_persists_observation_and_decision_artifacts():
    feature_store = _FeatureStore()
    artifact_store = _ArtifactStore()
    app = SupervisorApp(
        feature_store=feature_store,
        artifact_store=artifact_store,
        dashboard_client=_DashboardClient(),
    )

    packet = await app.run_once(feature_id="feat-1", cursor=10, bridge_log_cursor=4)

    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK
    keys = [write[0] for write in artifact_store.writes]
    assert keys[0].startswith("supervisor-observation:feat-1:e11:a12:b6:")
    assert keys[1].startswith("supervisor-decision:feat-1:e11:a12:b6:")
    assert keys[0] != keys[1]


# ── Slice 10e — the doc-10 step-4 `evidence_mode` wiring ────────────────────
#
# doc 10 § "Refactoring Steps" step 4: `collect_evidence` /
# `SupervisorEvidenceMcpService.get_current_snapshot` read the Slice-10a
# bounded typed `ControlPlaneSnapshot` FIRST and set the typed
# `SupervisorObservation.control_plane` + `evidence_mode`. `evidence_mode ==
# "typed"` takes the Slice-10c-2 typed-PRIMARY classifier path LIVE; a missing
# / degraded typed snapshot is the FAIL-SAFE `legacy_fallback` / `mixed`.


def _typed_failure_summary(
    failure_class: str,
    route: str,
    *,
    failure_id: int = 7700,
):
    from iriai_build_v2.workflows.develop.execution.snapshots import (
        TypedFailureSummary,
    )

    return TypedFailureSummary(
        failure_id=failure_id,
        attempt_id=12,
        evidence_id=failure_id,
        failure_class=failure_class,
        failure_type="x",
        severity="fatal",
        deterministic=False,
        operator_required=False,
        retryable=False,
        status="open",
        route=route,
        signature_hash="sig-10e",
        created_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
        resolved_at=None,
    )


def _typed_snapshot(
    *,
    source: str = "typed",
    degraded: bool = False,
    failures=None,
):
    from iriai_build_v2.workflows.develop.execution.snapshots import (
        ControlPlaneSnapshot,
    )

    return ControlPlaneSnapshot(
        feature_id="feat-1",
        snapshot_version="snap-10e",
        generated_at=datetime(2026, 5, 22, tzinfo=timezone.utc),
        source=source,
        degraded=degraded,
        degradation_reasons=["typed query timeout: latest_failures"]
        if degraded
        else [],
        active_group_idx=1,
        latest_failures=failures or [],
    )


class _PoolBackedArtifactStore(_ArtifactStore):
    """An artifact store with a `_pool` — the typed snapshot read path."""

    def __init__(self) -> None:
        super().__init__()
        self._pool = SimpleNamespace()


def test_evidence_mode_for_snapshot_follows_source():
    """`evidence_mode` follows the typed snapshot `source` (doc-10 step 4).

    A `None` snapshot (FAIL-SAFE — typed read unavailable) is never `typed`.
    """
    from iriai_build_v2.supervisor.evidence import evidence_mode_for_snapshot

    # the normal typed path
    assert evidence_mode_for_snapshot(_typed_snapshot(source="typed")) == "typed"
    # a partially-degraded typed query is `mixed`, NEVER typed-primary
    assert (
        evidence_mode_for_snapshot(
            _typed_snapshot(source="mixed", degraded=True)
        )
        == "mixed"
    )
    # a typed snapshot that flagged itself degraded but kept source="typed" is
    # still demoted to `mixed` — fail-safe, never typed-primary
    assert (
        evidence_mode_for_snapshot(
            _typed_snapshot(source="typed", degraded=True)
        )
        == "mixed"
    )
    # an old feature with no typed rows
    assert (
        evidence_mode_for_snapshot(_typed_snapshot(source="legacy_fallback"))
        == "legacy_fallback"
    )
    # FAIL-SAFE: no typed snapshot at all -> legacy_fallback, NOT typed
    assert evidence_mode_for_snapshot(None) == "legacy_fallback"


@pytest.mark.asyncio
async def test_collect_evidence_legacy_fallback_when_no_typed_pool():
    """FAIL-SAFE: an artifact store with no `_pool` yields no typed snapshot.

    `evidence_mode` is `legacy_fallback` (NOT `typed`) and the legacy artifact
    classifier still fires — the typed-primary path stays dormant.
    """
    from iriai_build_v2.supervisor.classifier import classify_observation

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        cursor=10,
    )

    assert observation.control_plane is None
    assert observation.evidence_mode == "legacy_fallback"
    # the legacy artifact classifier still produces a verdict
    packet = classify_observation(observation)
    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK


@pytest.mark.asyncio
async def test_collect_evidence_typed_mode_activates_typed_primary_classifier(
    monkeypatch,
):
    """A typed snapshot with `source="typed"` -> `evidence_mode == "typed"`.

    The Slice-10c-2 typed-PRIMARY classifier path then goes LIVE: a typed
    `checkpoint_contradiction` failure classifies `pipeline_bug_suspected`,
    overriding the legacy `dag-commit-failure` artifact (which would otherwise
    classify `deterministic_unblock`).
    """
    from iriai_build_v2 import supervisor as _supervisor_pkg  # noqa: F401
    from iriai_build_v2.execution_control.store import ExecutionControlStore
    from iriai_build_v2.supervisor.classifier import classify_observation
    from iriai_build_v2.supervisor.models import ActionLevel

    typed_snapshot = _typed_snapshot(
        source="typed",
        failures=[
            _typed_failure_summary("checkpoint_contradiction", "quiesce")
        ],
    )

    async def _fake_get_control_plane_snapshot(self, query):
        assert query.feature_id == "feat-1"
        # doc-10 "supervisor" scope; the budget is the bounded ceiling
        assert query.scope == "supervisor"
        return typed_snapshot

    monkeypatch.setattr(
        ExecutionControlStore,
        "get_control_plane_snapshot",
        _fake_get_control_plane_snapshot,
    )

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_PoolBackedArtifactStore(),
        cursor=10,
    )

    assert observation.evidence_mode == "typed"
    assert observation.control_plane is typed_snapshot
    assert "control_plane_typed" in observation.query_labels

    # the typed-PRIMARY classifier path is LIVE: the typed checkpoint
    # contradiction outranks the legacy dag-commit-failure artifact.
    packet = classify_observation(observation)
    assert packet.classification == FailureClass.PIPELINE_BUG_SUSPECTED
    assert packet.recommended_action == ActionLevel.STOP_ESCALATE
    assert packet.facts["mapping_row"] == "checkpoint_contradiction"


@pytest.mark.asyncio
async def test_collect_evidence_fails_safe_when_typed_snapshot_read_raises(
    monkeypatch,
):
    """FAIL-SAFE: a typed-read store error yields no typed snapshot.

    `evidence_mode` is `legacy_fallback` (NOT `typed`); the typed-primary
    classifier stays dormant and the legacy artifact classifier fires.
    """
    from iriai_build_v2.execution_control.store import ExecutionControlStore
    from iriai_build_v2.supervisor.classifier import classify_observation

    async def _raise(self, query):
        raise RuntimeError("typed control-plane store unavailable")

    monkeypatch.setattr(
        ExecutionControlStore, "get_control_plane_snapshot", _raise
    )

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_PoolBackedArtifactStore(),
        cursor=10,
    )

    assert observation.control_plane is None
    assert observation.evidence_mode == "legacy_fallback"
    # the legacy artifact classifier still fires — never a typed verdict
    packet = classify_observation(observation)
    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK


@pytest.mark.asyncio
async def test_collect_evidence_degraded_typed_snapshot_is_mixed_not_typed(
    monkeypatch,
):
    """A partially-degraded typed query -> `evidence_mode == "mixed"`.

    `mixed` is NOT typed-primary: the legacy artifact classifier runs as the
    fallback even though a (degraded) typed snapshot is attached. The typed
    snapshot, here carrying a `checkpoint_contradiction` failure, must NOT
    drive the verdict — a degraded typed query is not trustworthy as primary.
    """
    from iriai_build_v2.execution_control.store import ExecutionControlStore
    from iriai_build_v2.supervisor.classifier import classify_observation

    degraded_snapshot = _typed_snapshot(
        source="mixed",
        degraded=True,
        failures=[
            _typed_failure_summary("checkpoint_contradiction", "quiesce")
        ],
    )

    async def _fake(self, query):
        return degraded_snapshot

    monkeypatch.setattr(
        ExecutionControlStore, "get_control_plane_snapshot", _fake
    )

    observation = await collect_evidence(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_PoolBackedArtifactStore(),
        cursor=10,
    )

    assert observation.evidence_mode == "mixed"
    assert observation.control_plane is degraded_snapshot
    # the legacy artifact classifier runs (NOT the typed-primary path) — the
    # degraded typed checkpoint_contradiction does not produce the verdict.
    packet = classify_observation(observation)
    assert packet.classification == FailureClass.DETERMINISTIC_UNBLOCK


@pytest.mark.asyncio
async def test_get_current_snapshot_populates_typed_evidence_mode(monkeypatch):
    """`SupervisorEvidenceMcpService.get_current_snapshot` wires step 4 too.

    A typed snapshot -> the dict carries `control_plane_typed` + `evidence_mode
    == "typed"`.
    """
    from iriai_build_v2.execution_control.store import ExecutionControlStore
    from iriai_build_v2.supervisor.mcp_server import SupervisorEvidenceMcpService

    typed_snapshot = _typed_snapshot(source="typed")

    async def _fake(self, query):
        assert query.feature_id == "feat-1"
        return typed_snapshot

    monkeypatch.setattr(
        ExecutionControlStore, "get_control_plane_snapshot", _fake
    )

    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_PoolBackedArtifactStore(),
        allowed_feature_id="feat-1",
    )

    snapshot = await service.get_current_snapshot(
        feature_id="feat-1", include_bridge=False
    )

    assert snapshot["evidence_mode"] == "typed"
    assert snapshot["control_plane_typed"] is not None
    assert snapshot["control_plane_typed"]["snapshot_version"] == "snap-10e"
    assert snapshot["control_plane_typed"]["source"] == "typed"


@pytest.mark.asyncio
async def test_get_current_snapshot_fails_safe_without_typed_pool():
    """FAIL-SAFE: an MCP service whose artifact store has no `_pool`.

    `control_plane_typed` is `None` and `evidence_mode` is `legacy_fallback`
    (NOT `typed`).
    """
    from iriai_build_v2.supervisor.mcp_server import SupervisorEvidenceMcpService

    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        allowed_feature_id="feat-1",
    )

    snapshot = await service.get_current_snapshot(
        feature_id="feat-1", include_bridge=False
    )

    assert snapshot["control_plane_typed"] is None
    assert snapshot["evidence_mode"] == "legacy_fallback"


def _git(root: Path, *args: str) -> None:
    env = dict(os.environ)
    env.update(
        {
            "GIT_AUTHOR_DATE": "2024-01-01T00:00:00Z",
            "GIT_COMMITTER_DATE": "2024-01-01T00:00:00Z",
        }
    )
    subprocess.run(
        ["git", *args],
        cwd=root,
        env=env,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
