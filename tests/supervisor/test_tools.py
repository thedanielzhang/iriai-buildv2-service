from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from iriai_build_v2.supervisor.models import SupervisorInvestigationRequest
from iriai_build_v2.supervisor.mcp_server import SupervisorEvidenceMcpService
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
        del group_idx, preview_chars
        rows = [row for row in self.events if row["id"] > after_id]
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return rows[:limit]


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
        # Any other query (e.g. the Slice-10a typed-snapshot SELECTs the Slice-10e
        # evidence-mode wiring drives through this fake pool) gets an empty row
        # set — that correctly emulates a real empty-feature Postgres read, so
        # the typed snapshot's `source` is `legacy_fallback` and `evidence_mode`
        # never spuriously upgrades to `"typed"` against a degenerate fake.
        return []


class _ArtifactStore:
    def __init__(self) -> None:
        self._pool = _Pool()

    async def latest_summary(self, key: str, *, feature):
        del feature
        if key == "dag-verify:g38:retry-0":
            return {
                "id": 13,
                "created_at": None,
                "stored_bytes": 19,
                "value_preview": '{"status":"latest"}',
                "summary_only": True,
            }
        return None

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

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "desc",
    ):
        rows = await self.list_records(
            feature_id=feature_id,
            prefixes=prefixes,
            after_id=after_id,
            limit=limit,
        )
        rows = sorted(rows, key=lambda row: row["id"], reverse=(order == "desc"))
        return [
            {
                "id": row["id"],
                "key": row["key"],
                "created_at": row.get("created_at"),
                "stored_bytes": len(str(row.get("value", "")).encode("utf-8")),
                "value_preview": str(row.get("value", ""))[:700],
                "summary_only": True,
            }
            for row in rows[:limit]
        ]


class _DatabaseSummaryPool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple[object, ...], float | None]] = []

    async def fetch(self, sql: str, *args, timeout: float | None = None):
        self.fetches.append((sql, args, timeout))
        if "split_part(key, ':', 1)" in sql:
            return [
                {
                    "key_family": "implementation",
                    "rows": 1,
                    "db_stored_bytes": 200,
                    "stored_bytes": 150_000,
                    "logical_stored_bytes": 150_000,
                }
            ]
        if "SELECT id, key, created_at" in sql:
            return [
                {
                    "id": 12,
                    "key": "implementation",
                    "created_at": None,
                    "db_stored_bytes": 200,
                    "stored_bytes": 150_000,
                    "logical_stored_bytes": 150_000,
                }
            ]
        return []


class _DatabaseSummaryArtifactStore(_ArtifactStore):
    def __init__(self, pool: _DatabaseSummaryPool) -> None:
        super().__init__()
        self._pool = pool


class _SummaryOnlyArtifactStore(_ArtifactStore):
    def __init__(self) -> None:
        super().__init__()
        self.summary_calls = 0
        self.record_calls = 0

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "desc",
    ):
        self.summary_calls += 1
        return [
            {
                "id": 14,
                "key": f"{prefixes[0]}g38:retry-0",
                "created_at": None,
                "stored_bytes": 123456,
                "summary_only": True,
            }
        ]

    async def list_records(self, **kwargs):
        self.record_calls += 1
        raise AssertionError("broad supervisor evidence scans should not select values")


class _WorkspaceRuntimeSnapshotArtifactStore:
    def __init__(self) -> None:
        self.summary_calls: list[tuple[tuple[str, ...], str]] = []

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes,
        after_id: int,
        limit: int = 500,
        order: str = "desc",
    ):
        assert feature_id == "feat-1"
        assert after_id == 0
        del limit
        self.summary_calls.append((tuple(prefixes), order))
        rows = [
            {
                "id": 21,
                "key": "dag-workspace-acl-normalization:g48:repair-dispatch-retry-0",
                "created_at": None,
                "stored_bytes": 68,
                "value": "",
                "value_preview": (
                    '{"status":"blocked","summary":"ACL normalization needs operator"}'
                ),
                "summary_only": True,
            },
            {
                "id": 22,
                "key": "runtime-workspace-binding:g48:lease-1",
                "created_at": None,
                "stored_bytes": 44,
                "value": "",
                "value_preview": '{"status":"bound","summary":"runtime cwd bound"}',
                "summary_only": True,
            },
            {
                "id": 23,
                "key": "unrelated-control-plane",
                "created_at": None,
                "stored_bytes": 15,
                "value": "",
                "value_preview": "ignored",
                "summary_only": True,
            },
        ]
        filtered = [
            row
            for row in rows
            if any(row["key"].startswith(prefix) for prefix in prefixes)
        ]
        return sorted(filtered, key=lambda row: row["id"], reverse=(order == "desc"))

    async def list_records(self, **kwargs):
        raise AssertionError("MCP current snapshot must not hydrate artifact bodies")


@pytest.mark.asyncio
async def test_supervisor_evidence_toolbox_reads_bounded_sources(tmp_path: Path):
    request = SupervisorInvestigationRequest(
        reason="inspect g38",
        artifact_keys=["dag-verify:g38:retry-0"],
        artifact_prefixes=["dag-task-reconcile:"],
        artifact_ids=[12],
        artifact_chunks=["12:0"],
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

    assert [artifact.id for artifact in bundle.artifacts] == [12]
    assert [summary.id for summary in bundle.artifact_summaries] == [12, 13, 14]
    assert bundle.artifact_summaries[-1].citation == "artifact:dag-task-reconcile:g38:retry-0 id=14"
    assert bundle.artifact_chunks[0].chunk_ref == "12:0"
    assert bundle.artifact_chunks[0].text == '{"approved": true}'
    assert [event.id for event in bundle.events] == [11]
    assert bundle.worktrees[0].root == str(tmp_path.resolve())
    assert bundle.sql_results == []
    assert bundle.rejected_sql == [
        {
            "sql": "select count(*) from artifacts",
            "reason": "operator SQL is disabled; use named supervisor evidence APIs",
        },
        {
            "sql": "delete from artifacts",
            "reason": "operator SQL is disabled; use named supervisor evidence APIs",
        },
    ]


@pytest.mark.asyncio
async def test_supervisor_evidence_toolbox_uses_summary_rows_for_prefix_scans():
    artifact_store = _SummaryOnlyArtifactStore()
    request = SupervisorInvestigationRequest(
        reason="index only",
        artifact_prefixes=["dag-verify:"],
    )
    toolbox = SupervisorEvidenceToolbox(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=artifact_store,
    )

    bundle = await toolbox.gather(request)

    assert artifact_store.summary_calls == 1
    assert artifact_store.record_calls == 0
    assert bundle.artifact_summaries[0].size_chars == 123456
    assert bundle.artifact_summaries[0].chunk_refs == ["14:0"]


def test_validate_read_only_sql_rejects_unbounded_or_mutating_sql():
    assert validate_read_only_sql("select * from artifacts") == (
        "wildcard selection is not allowed; use named summary APIs"
    )
    assert validate_read_only_sql("update artifacts set value = '{}'") == (
        "only SELECT/WITH statements are allowed"
    )
    assert validate_read_only_sql("select value from artifacts limit 1") == (
        "raw artifact value selection is not allowed; use artifact slice APIs"
    )
    assert validate_read_only_sql("select * from artifacts limit 500") == (
        "wildcard selection is not allowed; use named summary APIs"
    )
    assert validate_read_only_sql("select id from artifacts limit 10") == ""
    assert validate_read_only_sql(
        "select substring(value from 1 for 10) from artifacts limit 1"
    ) == ""


@pytest.mark.asyncio
async def test_supervisor_evidence_mcp_service_exposes_read_only_tools(tmp_path: Path):
    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        allowed_feature_id="feat-1",
        worktree_roots=[tmp_path],
    )

    snapshot = await service.get_current_snapshot(feature_id="feat-1", include_bridge=False)
    assert snapshot["feature_id"] == "feat-1"
    assert snapshot["snapshot"]["latest_event_id"] == 11
    assert snapshot["latest_material_artifacts"][0]["citation"] == (
        "artifact:dag-verify:g38:retry-0 id=14"
    )
    assert snapshot["recommended_detail_artifact_ids"] == [14]
    assert "Avoid readonly_sql" in snapshot["tool_usage_hint"]

    index = await service.list_artifact_index(
        feature_id="feat-1",
        prefixes=["dag-task-reconcile:"],
        limit=5,
    )
    assert index["artifacts"][0]["citation"] == "artifact:dag-task-reconcile:g38:retry-0 id=14"
    assert "summary_preview" in index["artifacts"][0]

    detail = await service.get_artifact_detail(feature_id="feat-1", artifact_id=12)
    assert detail["found"] is True
    assert detail["artifact"]["citation"] == "artifact:dag-verify:g38:retry-0 id=12"

    chunk = await service.get_artifact_chunk(feature_id="feat-1", chunk_ref="12:0")
    assert chunk["found"] is True
    assert chunk["chunk"]["chunk_ref"] == "12:0"

    events = await service.list_events(feature_id="feat-1", after_id=10)
    assert [event["id"] for event in events["events"]] == [11]

    worktree = await service.probe_worktree()
    assert worktree["count"] == 1

    sql = await service.readonly_sql(
        feature_id="feat-1",
        sql="select 1 as value from events where feature_id = $1 limit 1",
    )
    assert sql["ok"] is False
    assert "operator SQL is disabled" in sql["reason"]

    rejected = await service.readonly_sql(
        feature_id="feat-1",
        sql="select 1 as value from events limit 1",
    )
    assert rejected["ok"] is False
    assert "operator SQL is disabled" in rejected["reason"]


@pytest.mark.asyncio
async def test_supervisor_evidence_mcp_snapshot_does_not_fallback_to_list_records():
    class _ListRecordsOnlyArtifactStore:
        async def list_records(self, *args, **kwargs):
            raise AssertionError("MCP snapshot must not call value-hydrating list_records")

    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_ListRecordsOnlyArtifactStore(),
        allowed_feature_id="feat-1",
    )

    snapshot = await service.get_current_snapshot(feature_id="feat-1", include_bridge=False)

    assert snapshot["latest_material_artifacts"] == []
    assert snapshot["recommended_detail_artifact_ids"] == []


@pytest.mark.asyncio
async def test_supervisor_evidence_mcp_snapshot_surfaces_workspace_runtime_summaries():
    artifact_store = _WorkspaceRuntimeSnapshotArtifactStore()
    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=artifact_store,
        allowed_feature_id="feat-1",
    )

    snapshot = await service.get_current_snapshot(feature_id="feat-1", include_bridge=False)

    material = snapshot["latest_material_artifacts"]
    assert [entry["key"] for entry in material[:2]] == [
        "runtime-workspace-binding:g48:lease-1",
        "dag-workspace-acl-normalization:g48:repair-dispatch-retry-0",
    ]
    assert snapshot["recommended_detail_artifact_ids"] == [21, 22]
    assert material[0]["summary_preview"] == "runtime cwd bound"
    assert material[1]["summary_preview"] == "ACL normalization needs operator"
    assert artifact_store.summary_calls


@pytest.mark.asyncio
async def test_operator_sql_stays_disabled_when_legacy_env_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("IRIAI_SUPERVISOR_ENABLE_OPERATOR_SQL", "1")
    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        allowed_feature_id="feat-1",
        worktree_roots=[tmp_path],
    )

    sql = await service.readonly_sql(
        feature_id="feat-1",
        sql="select id from events where feature_id = $1 or true limit 1",
    )

    assert sql["ok"] is False
    assert "operator SQL is disabled" in sql["reason"]

    toolbox = SupervisorEvidenceToolbox(
        feature_id="feat-1",
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
    )
    bundle = await toolbox.gather(
        SupervisorInvestigationRequest(
            reason="sql should not execute",
            sql=["select id from events where feature_id = $1 limit 1"],
        )
    )

    assert bundle.sql_results == []
    assert bundle.rejected_sql == [
        {
            "sql": "select id from events where feature_id = $1 limit 1",
            "reason": "operator SQL is disabled; use named supervisor evidence APIs",
        }
    ]


@pytest.mark.asyncio
async def test_supervisor_database_summary_reports_spill_logical_bytes():
    pool = _DatabaseSummaryPool()
    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_DatabaseSummaryArtifactStore(pool),
        allowed_feature_id="feat-1",
    )

    counts = await service.database_summary(
        feature_id="feat-1",
        query_name="artifact_counts",
    )
    largest = await service.database_summary(
        feature_id="feat-1",
        query_name="largest_artifacts",
    )

    assert counts["rows"][0]["db_stored_bytes"] == 200
    assert counts["rows"][0]["stored_bytes"] == 150_000
    assert counts["rows"][0]["logical_stored_bytes"] == 150_000
    assert largest["rows"][0]["db_stored_bytes"] == 200
    assert largest["rows"][0]["stored_bytes"] == 150_000
    assert len(pool.fetches) == 2
    for sql, _args, _timeout in pool.fetches:
        assert "value LIKE '{\"__iriai_spill_v1__\"%'" in sql
        assert 'substring(value from \'"bytes"\\s*:\\s*([0-9]+)\')' in sql
        assert "ELSE pg_column_size(value)::bigint END" in sql
        assert "db_stored_bytes" in sql
        assert "logical_stored_bytes" in sql


@pytest.mark.asyncio
async def test_supervisor_evidence_mcp_service_enforces_feature_scope():
    service = SupervisorEvidenceMcpService(
        feature_store=_FeatureStore(),
        artifact_store=_ArtifactStore(),
        allowed_feature_id="feat-1",
    )

    with pytest.raises(ValueError):
        await service.list_events(feature_id="other-feature")
