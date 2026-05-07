from __future__ import annotations

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.app import SupervisorApp
from iriai_build_v2.supervisor.evidence import (
    KEY_PREFIXES,
    build_current_workflow_snapshot,
    collect_evidence,
    probe_bridge,
    probe_worktree,
)
from iriai_build_v2.supervisor.models import EventRecord, FailureClass


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

    async def put(self, key: str, value: str, *, feature):
        self.writes.append((key, value, feature))


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
                    "value": {"status": "ok"},
                }
            )
            rows = [row for row in rows if row["id"] > after_id]
            rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
            return rows[:limit]

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
