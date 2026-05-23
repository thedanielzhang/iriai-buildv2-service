from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from iriai_compose import Feature

from iriai_build_v2.storage.artifacts import (
    ArtifactSpillHydrationError,
    ArtifactValueHydrationLimitExceeded,
    PostgresArtifactStore,
    _PREVIEW_PREFIXES,
)


class _ListRecordsPool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        if "pg_column_size(value)::bigint AS stored_bytes" in sql:
            return [
                {
                    "id": 12,
                    "key": "dag-verify:g38:retry-0",
                    "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
                    "stored_bytes": len(b'{"status":"failed"}'),
                    "content_ref": None,
                }
            ]
        return [
            {
                "id": 12,
                "key": "dag-verify:g38:retry-0",
                "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
                "value": '{"status":"failed"}',
            }
        ]


class _SummaryProjectionPool:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        return [
            {
                "id": 12,
                "key": "dag-verify:g38:retry-0",
                "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
                "stored_bytes": 123,
                "content_ref": None,
                "value_preview": '{"status":"failed"}',
            }
        ]


class _SpilledSummaryProjectionPool:
    def __init__(self, envelope: str, keys: list[str]) -> None:
        self.envelope = envelope
        self.keys = keys
        self.fetches: list[tuple[str, tuple[object, ...]]] = []
        self.fetchrows: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object):
        self.fetches.append((sql, args))
        return [
            self._row(artifact_id=artifact_id, key=key)
            for artifact_id, key in enumerate(self.keys, start=1)
        ]

    async def fetchrow(self, sql: str, *args: object):
        self.fetchrows.append((sql, args))
        key = str(args[1])
        if key not in self.keys:
            return None
        return self._row(artifact_id=self.keys.index(key) + 1, key=key)

    def _row(self, *, artifact_id: int, key: str) -> dict[str, object]:
        return {
            "id": artifact_id,
            "key": key,
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "stored_bytes": len(self.envelope.encode("utf-8")),
            "content_ref": self.envelope,
            "value_preview": self.envelope[:2000],
        }


class _InMemoryArtifactPool:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []
        self.next_id = 0

    async def fetchval(self, sql: str, *args: object) -> int:
        assert "INSERT INTO artifacts" in sql
        self.next_id += 1
        self.rows.append({
            "id": self.next_id,
            "feature_id": args[0],
            "key": args[1],
            "value": args[2],
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
        })
        return self.next_id

    async def fetchrow(self, sql: str, *args: object):
        feature_id = args[0]
        if "key = $2" in sql:
            key = args[1]
            matches = [
                row for row in self.rows
                if row["feature_id"] == feature_id and row["key"] == key
            ]
            if not matches:
                return None
            row = matches[-1]
            if "pg_column_size" in sql:
                return {
                    "id": row["id"],
                    "key": row["key"],
                    "created_at": row["created_at"],
                    "stored_bytes": len(str(row["value"]).encode("utf-8")),
                    "content_ref": row["value"] if "__iriai_spill_v1__" in str(row["value"]) else None,
                }
            return {
                "id": row["id"],
                "created_at": row["created_at"],
                "value": row["value"],
            }
        artifact_id = args[1]
        matches = [
            row for row in self.rows
            if row["feature_id"] == feature_id and row["id"] == artifact_id
        ]
        if not matches:
            return None
        row = matches[0]
        value = str(row["value"])
        start = int(args[2])
        chars = int(args[3])
        return {
            "id": row["id"],
            "key": row["key"],
            "created_at": row["created_at"],
            "stored_chars": len(value),
            "stored_bytes": len(value.encode("utf-8")),
            "value_slice": value[start:start + chars],
            "content_ref": value if "__iriai_spill_v1__" in value else None,
        }


@pytest.mark.asyncio
async def test_artifact_store_list_records_streams_repeated_keys_by_row_id() -> None:
    pool = _ListRecordsPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    records = await store.list_records(
        feature_id="8ac124d6",
        prefixes=("dag-verify:", "dag-task-reconcile:"),
        after_id=10,
        limit=50,
    )

    sql, args = pool.fetches[0]
    assert "id > $2" in sql
    assert "key LIKE $4" in sql
    assert "key LIKE $5" in sql
    assert args == (
        "8ac124d6",
        10,
        50,
        "dag-verify:%",
        "dag-task-reconcile:%",
    )
    full_sql, full_args = pool.fetches[1]
    assert "id = ANY($2::bigint[])" in full_sql
    assert full_args == ("8ac124d6", [12])
    assert records == [
        {
            "id": 12,
            "key": "dag-verify:g38:retry-0",
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "value": '{"status":"failed"}',
            "sha256": hashlib.sha256(b'{"status":"failed"}').hexdigest(),
        }
    ]


@pytest.mark.asyncio
async def test_artifact_store_list_records_supports_descending_latest_window() -> None:
    pool = _ListRecordsPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    await store.list_records(
        feature_id="8ac124d6",
        prefixes=("dag-verify:",),
        after_id=0,
        limit=10,
        order="desc",
    )

    summary_sql, summary_args = pool.fetches[0]
    assert "ORDER BY id DESC" in summary_sql
    assert summary_args == ("8ac124d6", 0, 10, "dag-verify:%")
    full_sql, full_args = pool.fetches[1]
    assert "ORDER BY id DESC" in full_sql
    assert full_args == ("8ac124d6", [12])


@pytest.mark.asyncio
async def test_artifact_store_list_records_blocks_oversized_full_hydration() -> None:
    class _LargePool(_ListRecordsPool):
        async def fetch(self, sql: str, *args: object):
            self.fetches.append((sql, args))
            if "pg_column_size(value)::bigint AS stored_bytes" in sql:
                return [
                    {
                        "id": 12,
                        "key": "dag-verify:g38:retry-0",
                        "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
                        "stored_bytes": 20_000_000,
                        "content_ref": None,
                    }
                ]
            raise AssertionError("full value query should not run")

    pool = _LargePool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    with pytest.raises(ArtifactValueHydrationLimitExceeded):
        await store.list_records(
            feature_id="8ac124d6",
            prefixes=("dag-verify:",),
            after_id=0,
            limit=10,
            max_total_value_bytes=1_000_000,
        )

    assert len(pool.fetches) == 1


@pytest.mark.asyncio
async def test_artifact_store_list_record_summaries_uses_bounded_projection_query() -> None:
    pool = _SummaryProjectionPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    summaries = await store.list_record_summaries(
        feature_id="8ac124d6",
        prefixes=("dag-verify:",),
        after_id=-1,
        limit=999,
        order="desc",
    )

    sql, args = pool.fetches[0]
    normalized = " ".join(sql.split())
    assert "SELECT id, key, created_at, value FROM artifacts" not in normalized
    assert "pg_column_size(value)::bigint AS stored_bytes" in normalized
    assert "AS content_ref" in normalized
    assert "AS value_preview" in normalized
    assert 'CASE WHEN value LIKE \'{"__iriai_spill_v1__"%' in normalized
    assert "THEN NULL WHEN" in normalized
    assert "ORDER BY id DESC" in normalized
    assert "LIMIT $3" in normalized
    assert args == ("8ac124d6", 0, 500, "dag-verify:%")
    assert summaries == [
        {
            "id": 12,
            "key": "dag-verify:g38:retry-0",
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
            "stored_bytes": 123,
            "content_ref": None,
            "value_preview": '{"status":"failed"}',
            "value": "",
            "summary_only": True,
        }
    ]


def test_artifact_store_preview_prefixes_cover_workspace_runtime_control_plane() -> None:
    for prefix in (
        "dag-verify-graph:",
        "dag-task-contract:",
        "dag-contract-verdict:",
        "dag-sandbox-patch:",
        "dag-worktree-alias-preflight:",
        "dag-worktree-alias-canonicalization:",
        "dag-workspace-acl-normalization:",
        "dag-workspace-permission-repair:",
        "runtime-workspace-binding:",
        "dag-runtime-workspace-binding:",
        "dag-runtime-failure:",
        "workflow-blocker:",
        "workspace-authority-",
    ):
        assert prefix in _PREVIEW_PREFIXES


def _write_spill_envelope(tmp_path: Path, *, feature_id: str, body: str) -> str:
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    rel_path = f"{feature_id}/{digest}.txt"
    spill_path = tmp_path / rel_path
    spill_path.parent.mkdir(parents=True)
    spill_path.write_text(body, encoding="utf-8")
    return json.dumps(
        {
            "__iriai_spill_v1__": True,
            "feature_id": feature_id,
            "path": rel_path,
            "sha256": digest,
            "bytes": len(body.encode("utf-8")),
            "chars": len(body),
            "content_type": "application/json",
            "policy": "lossless_spill",
            "created_at": "2026-05-06T00:00:00+00:00",
        },
        sort_keys=True,
    )


@pytest.mark.asyncio
async def test_artifact_store_spilled_workflow_blocker_dag_summaries_drop_envelope_preview(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    body = json.dumps(
        {
            "status": "failed",
            "failure_class": "runtime_context",
            "deterministic_workflow_blocker": True,
        }
    )
    envelope = _write_spill_envelope(
        tmp_path,
        feature_id="feature-runtime_context",
        body=body,
    )
    pool = _SpilledSummaryProjectionPool(
        envelope,
        keys=["workflow-blocker:verify", "dag-runtime-failure:source-push"],
    )
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]

    summaries = await store.list_record_summaries(
        feature_id="feature-runtime_context",
        prefixes=("workflow-blocker:", "dag-runtime-failure:"),
    )

    assert [summary["key"] for summary in summaries] == [
        "workflow-blocker:verify",
        "dag-runtime-failure:source-push",
    ]
    for summary in summaries:
        assert summary["content_ref"]["bytes"] == len(body.encode("utf-8"))
        assert summary["value_preview"] == body
        assert summary["value"] == ""
        assert summary["summary_only"] is True
        semantic_signal = (
            summary["value_preview"]
            if summary["summary_only"] and summary["value_preview"]
            else summary["value"]
        )
        assert semantic_signal == body
        assert "__iriai_spill_v1__" not in str(semantic_signal)


@pytest.mark.asyncio
async def test_artifact_store_spilled_latest_summary_drops_envelope_preview(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    body = json.dumps({"status": "failed", "failure_class": "runtime_context"})
    envelope = _write_spill_envelope(
        tmp_path,
        feature_id="feature-runtime_context",
        body=body,
    )
    pool = _SpilledSummaryProjectionPool(
        envelope,
        keys=["dag-runtime-failure:source-push"],
    )
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-runtime_context",
        name="Runtime Context",
        slug="runtime-context",
        workflow_name="full-develop",
        workspace_id="main",
    )

    summary = await store.latest_summary("dag-runtime-failure:source-push", feature=feature)

    assert summary is not None
    assert summary["content_ref"]["bytes"] == len(body.encode("utf-8"))
    assert summary["value_preview"] == body
    assert summary["value"] == ""
    assert "__iriai_spill_v1__" not in str(summary["value_preview"])


@pytest.mark.asyncio
async def test_artifact_store_spills_large_noncheckpoint_artifact_losslessly(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_MAX_BYTES", "100000")
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )

    body = json.dumps({"payload": "x" * 150_000})
    await store.put("implementation", body, feature=feature)

    stored_value = str(pool.rows[0]["value"])
    assert "__iriai_spill_v1__" in stored_value
    assert await store.get("implementation", feature=feature) == body

    summary = await store.latest_summary("implementation", feature=feature)
    assert summary is not None
    assert summary["content_ref"]["bytes"] == len(body.encode("utf-8"))
    assert summary["stored_bytes"] == len(body.encode("utf-8"))

    slice_row = await store.get_slice(
        feature_id=feature.id,
        artifact_id=1,
        start=0,
        chars=20,
    )
    assert slice_row is not None
    assert slice_row["text"] == body[:20]
    assert slice_row["total_chars"] == len(body)
    assert slice_row["stored_bytes"] == len(body.encode("utf-8"))


@pytest.mark.asyncio
async def test_artifact_store_spill_slice_uses_metadata_without_full_hash(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_MAX_BYTES", "100000")
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )
    body = json.dumps({"payload": "x" * 150_000})
    await store.put("implementation", body, feature=feature)
    monkeypatch.setattr(
        "iriai_build_v2.storage.artifacts._hash_file_sha256",
        lambda _path: (_ for _ in ()).throw(AssertionError("slice hashed whole spill")),
    )

    slice_row = await store.get_slice(
        feature_id=feature.id,
        artifact_id=1,
        start=len(body) + 100,
        chars=20,
    )

    assert slice_row is not None
    assert slice_row["text"] == ""
    assert slice_row["total_chars"] == len(body)


@pytest.mark.asyncio
async def test_artifact_store_keeps_canonical_dag_keys_in_database(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_MAX_BYTES", "100000")
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-canonical",
        name="Canonical",
        slug="canonical",
        workflow_name="full-develop",
        workspace_id="main",
    )

    body = json.dumps({"payload": "x" * 150_000})
    for key in (
        "dag",
        "dag-group:1",
        "dag-task:TASK-1",
        "dag-verify:g1:initial",
        "dag-commit-failure:g1:retry-0",
    ):
        await store.put(key, body, feature=feature)

    for row in pool.rows:
        stored_value = str(row["value"])
        assert "__iriai_spill_v1__" not in stored_value
        assert stored_value == body
        assert await store.get(str(row["key"]), feature=feature) == body


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "envelope_update",
    [
        {"sha256": ""},
        {"path": "/tmp/outside.txt"},
        {"path": "feature-spill/../outside.txt"},
        {"feature_id": ""},
        {"content_type": ""},
        {"created_at": ""},
    ],
)
async def test_artifact_store_rejects_invalid_spill_envelopes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    envelope_update: dict[str, object],
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    body = "valid spill body"
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    rel_path = "feature-spill/manual.txt"
    spill_path = tmp_path / rel_path
    spill_path.parent.mkdir(parents=True)
    spill_path.write_text(body, encoding="utf-8")
    envelope = {
        "__iriai_spill_v1__": True,
        "feature_id": "feature-spill",
        "path": rel_path,
        "sha256": digest,
        "bytes": len(body.encode("utf-8")),
        "chars": len(body),
        "content_type": "text/plain",
        "created_at": "2026-05-06T00:00:00+00:00",
    }
    envelope.update(envelope_update)
    pool.rows.append(
        {
            "id": 1,
            "feature_id": "feature-spill",
            "key": "implementation",
            "value": json.dumps(envelope),
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
        }
    )
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )

    summary = await store.latest_summary("implementation", feature=feature)
    assert summary is not None
    assert summary["content_ref"] is None


@pytest.mark.asyncio
async def test_artifact_store_missing_spill_sidecar_fails_closed_on_full_read(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    body = "missing spill body"
    envelope = {
        "__iriai_spill_v1__": True,
        "feature_id": "feature-spill",
        "path": "feature-spill/missing.txt",
        "sha256": hashlib.sha256(body.encode("utf-8")).hexdigest(),
        "bytes": len(body.encode("utf-8")),
        "chars": len(body),
        "content_type": "text/plain",
        "created_at": "2026-05-06T00:00:00+00:00",
    }
    pool.rows.append(
        {
            "id": 1,
            "feature_id": "feature-spill",
            "key": "implementation",
            "value": json.dumps(envelope),
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
        }
    )
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )

    with pytest.raises(ArtifactSpillHydrationError):
        await store.get("implementation", feature=feature)
    with pytest.raises(ArtifactSpillHydrationError):
        await store.get_record("implementation", feature=feature)


@pytest.mark.asyncio
async def test_artifact_store_rejects_spill_envelope_with_mismatched_hash(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    rel_path = "feature-spill/manual.txt"
    spill_path = tmp_path / rel_path
    spill_path.parent.mkdir(parents=True)
    spill_path.write_text("corrupt", encoding="utf-8")
    envelope = {
        "__iriai_spill_v1__": True,
        "feature_id": "feature-spill",
        "path": rel_path,
        "sha256": hashlib.sha256(b"expected").hexdigest(),
        "bytes": len(b"expected"),
        "chars": len("expected"),
        "content_type": "text/plain",
        "created_at": "2026-05-06T00:00:00+00:00",
    }
    pool.rows.append(
        {
            "id": 1,
            "feature_id": "feature-spill",
            "key": "implementation",
            "value": json.dumps(envelope),
            "created_at": datetime(2026, 5, 6, tzinfo=timezone.utc),
        }
    )
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _self: (_ for _ in ()).throw(AssertionError("summary read whole spill")),
    )

    summary = await store.latest_summary("implementation", feature=feature)
    assert summary is not None
    assert summary["content_ref"] is not None
    assert summary["content_ref"]["hash_verified"] is False

    slice_row = await store.get_slice(
        feature_id=feature.id,
        artifact_id=1,
        start=0,
        chars=20,
    )
    assert slice_row is not None
    assert slice_row["text"] == ""
    assert slice_row["total_chars"] == 0


@pytest.mark.asyncio
async def test_artifact_store_rewrites_corrupt_existing_spill_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_MAX_BYTES", "100000")
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )
    body = "payload-" + ("x" * 150_000)
    digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
    spill_path = tmp_path / feature.id / f"{digest}.txt"
    spill_path.parent.mkdir(parents=True)
    spill_path.write_text("corrupt", encoding="utf-8")

    await store.put("implementation", body, feature=feature)

    assert spill_path.read_text(encoding="utf-8") == body
    assert await store.get("implementation", feature=feature) == body


@pytest.mark.asyncio
async def test_artifact_store_spill_slices_use_character_offsets_for_utf8(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_DIR", str(tmp_path))
    monkeypatch.setenv("IRIAI_ARTIFACT_SPILL_MAX_BYTES", "100000")
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-spill",
        name="Spill",
        slug="spill",
        workflow_name="full-develop",
        workspace_id="main",
    )
    body = "αβ🙂漢字" * 25_000
    await store.put("implementation", body, feature=feature)

    slice_row = await store.get_slice(
        feature_id=feature.id,
        artifact_id=1,
        start=1,
        chars=4,
    )

    assert slice_row is not None
    assert slice_row["text"] == body[1:5]
    assert slice_row["char_end"] == 5


@pytest.mark.asyncio
async def test_write_artifact_bytes_returns_real_artifact_id() -> None:
    pool = _InMemoryArtifactPool()
    store = PostgresArtifactStore(pool)  # type: ignore[arg-type]
    feature = Feature(
        id="feature-sandbox",
        name="Sandbox",
        slug="sandbox",
        workflow_name="full-develop",
        workspace_id="main",
    )

    artifact_id = await store.write_artifact_bytes(
        "dag-sandbox-patch:g0:attempt-0:repo-app.patch",
        b"diff --git a/a b/a\n",
        {"sandbox_id": "sandbox-1"},
        feature=feature,
    )

    assert artifact_id == 1
    assert pool.rows[0]["id"] == artifact_id
    assert pool.rows[0]["feature_id"] == feature.id
    assert pool.rows[0]["key"] == "dag-sandbox-patch:g0:attempt-0:repo-app.patch"
    assert pool.rows[0]["value"] == "diff --git a/a b/a\n"
