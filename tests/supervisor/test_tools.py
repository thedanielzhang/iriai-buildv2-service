from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.models import SupervisorInvestigationRequest
from iriai_build_v2.supervisor.tools import SupervisorEvidenceToolbox, validate_read_only_sql


class _FeatureStore:
    def __init__(self) -> None:
        self.feature = SimpleNamespace(id="feat-1")
        self.events = [
            {"id": 10, "event_type": "dag_verify_start", "source": "runner"},
            {"id": 11, "event_type": "dag_verify_finish", "source": "runner"},
        ]

    async def get_feature(self, feature_id: str):
        return self.feature

    async def get_events(self, feature_id: str):
        return list(self.events)


class _Pool:
    async def fetch(self, sql: str, *args):
        if "FROM artifacts" in sql:
            return [
                {
                    "id": 12,
                    "key": "dag-verify:g38:retry-0",
                    "created_at": None,
                    "value": '{"approved":true}',
                }
            ]
        return [{"value": 1}]


class _ArtifactStore:
    def __init__(self) -> None:
        self._pool = _Pool()

    async def get_record(self, key: str, *, feature):
        return {
            "id": 13,
            "created_at": None,
            "value": '{"status":"latest"}',
        }

    async def list_records(self, *, feature_id: str, prefixes, after_id: int, limit: int = 500):
        return [
            {
                "id": 14,
                "key": f"{prefixes[0]}g38:retry-0",
                "created_at": None,
                "value": '{"status":"prefix"}',
            }
        ]


@pytest.mark.asyncio
async def test_supervisor_evidence_toolbox_reads_bounded_sources(tmp_path: Path):
    request = SupervisorInvestigationRequest(
        reason="inspect g38",
        artifact_keys=["dag-verify:g38:retry-0"],
        artifact_prefixes=["dag-task-reconcile:"],
        artifact_ids=[12],
        event_after_id=10,
        include_worktrees=True,
        sql=[
            "select count(*) from artifacts",
            "delete from artifacts",
        ],
    )
    toolbox = SupervisorEvidenceToolbox(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        worktree_roots=[tmp_path],
    )

    bundle = await toolbox.gather(request)

    assert [artifact.id for artifact in bundle.artifacts] == [12, 13, 14]
    assert [event.id for event in bundle.events] == [11]
    assert bundle.worktrees[0].root == str(tmp_path.resolve())
    assert bundle.sql_results[0]["row_count"] == 1
    assert bundle.rejected_sql == [
        {"sql": "delete from artifacts", "reason": "only SELECT/WITH statements are allowed"}
    ]


def test_validate_read_only_sql_rejects_unbounded_or_mutating_sql():
    assert validate_read_only_sql("select * from artifacts") == (
        "read-only SQL must include LIMIT or COUNT"
    )
    assert validate_read_only_sql("update artifacts set value = '{}'") == (
        "only SELECT/WITH statements are allowed"
    )
    assert validate_read_only_sql("select * from artifacts limit 10") == ""
