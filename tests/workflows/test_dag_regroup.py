from __future__ import annotations

import asyncio
import hashlib
import json
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import asyncpg
import pytest
import pytest_asyncio

from iriai_build_v2.execution_control.atomic_landing import InFlightAdoptionRecord
from iriai_build_v2.models.outputs import ImplementationDAG, ImplementationResult, ImplementationTask
from iriai_build_v2.workflows.develop import dag_regroup
from iriai_build_v2.workflows.develop.phases import implementation as implementation_module


def _empty_groups(count: int) -> list[list[str]]:
    return [[] for _ in range(count)]


def _strict_adoption_marker(
    feature,
    *,
    completed_range: tuple[int, int],
    next_group: int,
) -> str:
    """A valid execution-control adoption marker body for resume fixtures.

    The strict resume guard (``_execution_control_adoption_record_for_resume``,
    src/iriai_build_v2/workflows/develop/phases/implementation.py:2510) refuses
    to dispatch any feature with existing ``dag-group:*`` checkpoint state
    unless the artifact ``execution-control-adoption:{feature_id}`` holds a
    valid ``InFlightAdoptionRecord``. This mirrors the production writer
    ``adopt_in_flight_feature`` (src/iriai_build_v2/execution_control/
    adoption.py:348-372), which constructs an ``InFlightAdoptionRecord``
    (src/iriai_build_v2/execution_control/atomic_landing.py:742) and persists
    ``record.model_dump_json()`` under ``adoption_marker_artifact_key``
    (src/iriai_build_v2/execution_control/adoption.py:156 →
    ``execution-control-adoption:{feature_id}``). Same helper shape as
    tests/workflows/test_dag_expanded_verify.py::_strict_adoption_marker.
    """
    return InFlightAdoptionRecord(
        feature_id=str(feature.id),
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-artifact",
        legacy_root_dag_artifact_id=42,
        legacy_root_dag_sha256="f" * 64,
        completed_checkpoint_range=completed_range,
        next_effective_group_idx=next_group,
        projection_digest="p" * 64,
        adopted_at=datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc),
        pre_adoption_baseline={"test": "sealed"},
    ).model_dump_json()


# ── Real-Postgres fixtures for the rewired CLI suite (Slice 09e-1b) ─────────
#
# Slice 09e-1b rewires ``dag_regroup.command_activate`` / ``command_rollback``
# off the legacy artifact-table activation/rollback and onto the *typed*
# ``activate_overlay`` / ``rollback_overlay`` (09c-1) over the
# ``execution_regroup_overlays`` / ``execution_regroup_validations`` tables.
# That is a GENUINE behavior change — the CLI now writes typed overlay rows,
# not only ``artifacts`` rows. The 27-test ``_FakeConn`` suite has no
# typed-overlay tables, so the CLI activation/rollback tests are UPDATED to run
# against a real Postgres database (the typed path's correctness is in
# Postgres lease/transaction/FK/advisory-lock semantics the in-memory fake
# cannot exercise). The update is NOT a weakening: the activation/rollback
# safety checks, the not-started boundary, and the reason codes are all still
# asserted — against the real typed transaction — and typed-row assertions are
# ADDED. The pure-function tests + the resolver tests + ``command_status``
# (which is NOT rewired) keep the ``_FakeConn`` fast path unchanged.
#
# These fixtures mirror ``tests/workflows/develop/execution/conftest.py`` (that
# conftest is directory-scoped and not visible here). They skip cleanly when
# no Postgres is reachable, so the suite stays green offline.

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCHEMA_PATH = _REPO_ROOT / "schema.sql"
_PG_HOST = os.environ.get("IRIAI_TEST_PGHOST", "localhost")
_PG_PORT = os.environ.get("IRIAI_TEST_PGPORT", "5431")
_PG_USER = os.environ.get("IRIAI_TEST_PGUSER") or os.environ.get("USER") or "postgres"
_PG_PASSWORD = os.environ.get("IRIAI_TEST_PGPASSWORD", "")


def _regroup_dsn(database: str) -> str:
    auth = _PG_USER if not _PG_PASSWORD else f"{_PG_USER}:{_PG_PASSWORD}"
    return f"postgresql://{auth}@{_PG_HOST}:{_PG_PORT}/{database}"


@pytest.fixture(scope="session")
def regroup_database() -> Iterator[str]:
    """A throwaway Postgres database with ``schema.sql`` loaded.

    Yields a DSN; skips dependent tests when no Postgres is reachable.
    """

    db_name = f"iriai_regroup_cli_test_{uuid.uuid4().hex[:12]}"

    async def _probe() -> None:
        conn = await asyncpg.connect(_regroup_dsn("postgres"))
        await conn.close()

    async def _create() -> None:
        admin = await asyncpg.connect(_regroup_dsn("postgres"))
        try:
            await admin.execute(f'CREATE DATABASE "{db_name}"')
        finally:
            await admin.close()
        conn = await asyncpg.connect(_regroup_dsn(db_name))
        try:
            await conn.execute(_SCHEMA_PATH.read_text())
        finally:
            await conn.close()

    async def _drop() -> None:
        admin = await asyncpg.connect(_regroup_dsn("postgres"))
        try:
            await admin.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = $1 AND pid <> pg_backend_pid()",
                db_name,
            )
            await admin.execute(f'DROP DATABASE IF EXISTS "{db_name}"')
        finally:
            await admin.close()

    try:
        asyncio.run(_probe())
    except (OSError, asyncpg.PostgresError) as exc:  # pragma: no cover - env
        pytest.skip(f"Postgres unavailable for regroup CLI tests: {exc}")

    asyncio.run(_create())
    try:
        yield _regroup_dsn(db_name)
    finally:
        asyncio.run(_drop())


@pytest_asyncio.fixture
async def regroup_dsn(regroup_database: str) -> str:
    """DSN of a clean-slate test database (every table truncated)."""

    conn = await asyncpg.connect(regroup_database)
    try:
        rows = await conn.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        if rows:
            names = ", ".join(f'"{row["tablename"]}"' for row in rows)
            await conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
    finally:
        await conn.close()
    return regroup_database


def _patch_real_pool(monkeypatch, dsn: str) -> None:
    """Make ``dag_regroup.create_pool`` open a pool against the real test DB.

    The rewired ``command_activate`` / ``command_rollback`` acquire ONE
    connection from the pool and drive the typed store + ``activate_overlay`` /
    ``rollback_overlay`` over it, so a normal asyncpg pool is exactly what the
    CLI needs (no fake).
    """

    async def _create_pool(_dsn):
        return await asyncpg.create_pool(dsn, min_size=1, max_size=4)

    monkeypatch.setattr(dag_regroup, "create_pool", _create_pool)


async def _seed_feature(conn: asyncpg.Connection, feature_id: str) -> None:
    """Insert the ``features`` row the typed overlay tables reference."""

    await conn.execute(
        "INSERT INTO features (id, name, slug, workflow_name, workspace_id) "
        "VALUES ($1, $2, $3, $4, $5)",
        feature_id,
        feature_id,
        feature_id,
        "develop",
        "ws-regroup",
    )


async def _seed_artifact(
    conn: asyncpg.Connection, feature_id: str, key: str, value: str
) -> int:
    return int(
        await conn.fetchval(
            "INSERT INTO artifacts (feature_id, key, value) VALUES ($1,$2,$3) "
            "RETURNING id",
            feature_id,
            key,
            value,
        )
    )


class _RealArtifacts:
    """An artifact store backed by a real asyncpg pool.

    The typed regroup resolver path (``_resolve_active_regroup_before_group_
    dispatch``) reads the active marker through ``runner.artifacts.get`` and
    writes the observation through ``runner.artifacts.put``; it ALSO resolves
    an :class:`ExecutionControlStore` from the runner — when no
    ``services``-level pool exists it falls back to ``runner.artifacts._pool``.
    Exposing ``_pool`` here lets the typed resolver build the
    :class:`RegroupOverlayStore` over the SAME test database.
    """

    def __init__(self, pool: asyncpg.Pool, feature_id: str) -> None:
        self._pool = pool
        self.feature_id = feature_id

    async def get(self, key: str, *, feature) -> str:
        del feature
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
                "ORDER BY id DESC LIMIT 1",
                self.feature_id,
                key,
            )
        return str(value) if value is not None else ""

    async def get_record(self, key: str, *, feature):
        del feature
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT id, value, created_at FROM artifacts "
                "WHERE feature_id = $1 AND key = $2 ORDER BY id DESC LIMIT 1",
                self.feature_id,
                key,
            )
        if row is None:
            return None
        return {"id": row["id"], "value": row["value"], "created_at": row["created_at"]}

    async def put(self, key: str, value: str, *, feature) -> None:
        del feature
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO artifacts (feature_id, key, value) VALUES ($1,$2,$3)",
                self.feature_id,
                key,
                value,
            )


def _base_dag() -> ImplementationDAG:
    tasks = [
        ImplementationTask(
            id="BACKEND-A",
            name="Backend bridge foundation",
            description="Build the bridge API adapter foundation",
            files=["backend/bridge.py"],
        ),
        ImplementationTask(
            id="UI-B",
            name="Implementation UI panel",
            description="Build implementation UI controls",
            files=["frontend/implementation.tsx"],
        ),
        ImplementationTask(
            id="UI-C",
            name="Review UI panel",
            description="Build review UI controls",
            files=["frontend/review.tsx"],
        ),
        ImplementationTask(
            id="BACKEND-D",
            name="Backend artifact API",
            description="Wire backend artifact endpoint after bridge foundation",
            files=["backend/artifacts.py"],
            dependencies=["BACKEND-A"],
        ),
    ]
    return ImplementationDAG(
        tasks=tasks,
        execution_order=[
            *_empty_groups(45),
            ["BACKEND-A"],
            ["UI-B"],
            ["UI-C"],
            ["BACKEND-D"],
        ],
        num_teams=4,
        complete=True,
    )


def _base_hash(dag: ImplementationDAG) -> str:
    text = dag.model_dump_json()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class _FakeRow(dict):
    def __getitem__(self, key):
        return self.get(key)


class _FakeTransaction:
    def __init__(self, conn) -> None:
        self.conn = conn

    async def start(self):
        self.conn.transaction_started = True

    async def commit(self):
        self.conn.transaction_committed = True

    async def rollback(self):
        self.conn.transaction_rolled_back = True


class _FakeConn:
    def __init__(self, *, feature_id: str, dag: ImplementationDAG, draft=None) -> None:
        self.feature_id = feature_id
        self.dag = dag
        self.dag_json = dag.model_dump_json()
        self.dag_hash = hashlib.sha256(self.dag_json.encode("utf-8")).hexdigest()
        self.artifacts: list[dict[str, object]] = [{
            "id": 123,
            "feature_id": feature_id,
            "key": "dag",
            "value": self.dag_json,
            "created_at": "now",
        }]
        if draft is not None:
            self.artifacts.append({
                "id": 124,
                "feature_id": feature_id,
                "key": dag_regroup.DRAFT_KEY,
                "value": draft.model_dump_json(),
                "created_at": "now",
            })
        self.events: list[dict[str, object]] = []
        self.transaction_started = False
        self.transaction_committed = False
        self.transaction_rolled_back = False
        self.outbox_table_exists = True

    def transaction(self):
        return _FakeTransaction(self)

    async def fetchrow(self, query, *args):
        del query
        feature_id, key = args[:2]
        matches = [
            artifact
            for artifact in self.artifacts
            if artifact["feature_id"] == feature_id and artifact["key"] == key
        ]
        if not matches:
            return None
        row = max(matches, key=lambda artifact: int(artifact["id"]))
        return _FakeRow(row)

    async def fetch(self, query, *args):
        del query
        feature_id, keys = args[:2]
        return [
            _FakeRow({
                "id": artifact["id"],
                "key": artifact["key"],
                "created_at": artifact["created_at"],
                "bytes": len(str(artifact["value"])),
            })
            for artifact in sorted(self.artifacts, key=lambda item: int(item["id"]), reverse=True)
            if artifact["feature_id"] == feature_id and artifact["key"] in set(keys)
        ]

    async def fetchval(self, query, *args):
        normalized = " ".join(str(query).split()).lower()
        if "to_regclass" in normalized:
            return "public_dashboard_outbox" if self.outbox_table_exists else None
        if "pg_advisory_xact_lock" in normalized:
            return None
        if "pg_database_size" in normalized:
            return 1_000
        if "pg_stat_activity" in normalized:
            return 1
        if "sum(pg_column_size(value))" in normalized:
            return sum(
                len(str(artifact["value"]))
                for artifact in self.artifacts
                if artifact["feature_id"] == args[0]
            )
        if "public_dashboard_outbox" in normalized:
            return 0
        if "select 1 from artifacts" in normalized:
            feature_id, key = args[:2]
            return 1 if any(
                artifact["feature_id"] == feature_id and artifact["key"] == key
                for artifact in self.artifacts
            ) else None
        if "insert into artifacts" in normalized:
            feature_id, key, value = args[:3]
            new_id = max(int(artifact["id"]) for artifact in self.artifacts) + 1
            self.artifacts.append({
                "id": new_id,
                "feature_id": feature_id,
                "key": key,
                "value": value,
                "created_at": "now",
            })
            return new_id
        if "select count(*) from artifacts" in normalized:
            feature_id = args[0]
            if "key = any" in normalized:
                keys = set(args[1])
                return sum(
                    1
                    for artifact in self.artifacts
                    if artifact["feature_id"] == feature_id and artifact["key"] in keys
                )
            patterns = tuple(str(arg).replace("%", "") for arg in args[1:])
            return sum(
                1
                for artifact in self.artifacts
                if artifact["feature_id"] == feature_id
                and any(str(artifact["key"]).startswith(pattern.rstrip(":")) for pattern in patterns)
            )
        if "select count(*) from events" in normalized:
            feature_id, group_idx = args[:2]
            return sum(
                1
                for event in self.events
                if event["feature_id"] == feature_id
                and event.get("source") != "dag-regroup"
                and str((event.get("metadata") or {}).get("group_idx")) == str(group_idx)
            )
        raise AssertionError(f"unexpected fetchval query: {query}")

    async def execute(self, query, *args):
        normalized = " ".join(str(query).split()).lower()
        if "pg_advisory_xact_lock" in normalized:
            return "SELECT 1"
        if "insert into events" in normalized:
            feature_id, event_type, content, metadata_json = args[:4]
            self.events.append({
                "feature_id": feature_id,
                "event_type": event_type,
                "source": "dag-regroup",
                "content": content,
                "metadata": json.loads(metadata_json),
            })
            return "INSERT 0 1"
        raise AssertionError(f"unexpected execute query: {query}")


class _FakePool:
    def __init__(self, conn) -> None:
        self.conn = conn
        self.closed = False

    def acquire(self):
        pool = self

        class _Acquire:
            async def __aenter__(self):
                return pool.conn

            async def __aexit__(self, exc_type, exc, tb):
                return False

        return _Acquire()

    async def close(self):
        self.closed = True


def _patch_fake_pool(monkeypatch, pool: _FakePool) -> None:
    async def _create_pool(_dsn):
        return pool

    monkeypatch.setattr(dag_regroup, "create_pool", _create_pool)


def test_speed_index_is_deterministic():
    dag = _base_dag()

    first = dag_regroup.build_speed_index(dag, from_group=45, to_group=48)
    second = dag_regroup.build_speed_index(dag, from_group=45, to_group=48)

    assert first == second
    assert first["tasks"]["BACKEND-A"]["critical_path_depth"] > first["tasks"]["BACKEND-D"]["critical_path_depth"]


def test_operator_json_output_handles_database_datetimes():
    payload = {"created_at": datetime(2026, 5, 11, tzinfo=timezone.utc)}

    text = dag_regroup._json_dumps(payload)

    assert "2026-05-11" in text


def _ts(hours: float) -> datetime:
    return datetime(2026, 5, 15, tzinfo=timezone.utc) + timedelta(hours=hours)


def test_sizing_metrics_maps_effective_groups_and_excludes_active_from_baseline():
    dag = _base_dag()
    candidate = dag_regroup.build_staged_regroup(
        dag,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(dag),
        from_group=45,
        to_group=48,
    )
    artifacts = [
        {"id": 1, "key": "dag-group:44", "created_at": _ts(0), "bytes": 10},
        {"id": 2, "key": "dag-group:45", "created_at": _ts(2), "bytes": 10},
        {"id": 3, "key": "dag-group:46", "created_at": _ts(5), "bytes": 10},
        {"id": 4, "key": "dag-verify:g45:initial", "created_at": _ts(1.8), "bytes": 10},
        {"id": 5, "key": "dag-workspace-acl-normalization:g46:initial-dispatch", "created_at": _ts(3), "bytes": 10},
        {"id": 6, "key": "dag-worktree-alias-preflight:g47:initial-dispatch", "created_at": _ts(5.5), "bytes": 10},
    ]
    events = [
        {"id": 10, "created_at": _ts(0.1), "event_type": "dag_task_dispatch", "source": "implementation", "content": "group 45", "metadata": {"group_idx": 45}},
        {"id": 11, "created_at": _ts(1.9), "event_type": "dag_verify_finish", "source": "implementation", "content": "g45:initial", "metadata": {"group_idx": 45}},
        {"id": 12, "created_at": _ts(3.0), "event_type": "dag_repair_cycle_start", "source": "implementation", "content": "g46:retry-0", "metadata": {"group_idx": 46}},
        {"id": 13, "created_at": _ts(5.5), "event_type": "dag_task_dispatch", "source": "implementation", "content": "group 47", "metadata": {"group_idx": 47}},
    ]

    metrics = dag_regroup.collect_sizing_metrics(
        feature_id="feat-sizing",
        base_dag=dag,
        regroup_candidate=candidate,
        events=events,
        artifact_summaries=artifacts,
        from_group=45,
    )

    assert metrics["latest_checkpoint_group"] == 46
    assert metrics["active_group"] == 47
    assert metrics["baselines"]["post_change_completed"]["completed_group_count"] == 2
    assert metrics["baselines"]["post_change_completed"]["task_count"] == 2
    active = next(group for group in metrics["groups"] if group["group_idx"] == 47)
    assert active["active"] is True
    assert active["checkpointed"] is False
    assert active["worktree_alias_events"] == 1


def test_adaptive_sizing_widens_low_risk_ui_work_when_data_supports_it():
    tasks = [
        ImplementationTask(
            id=f"UI-{idx}",
            name=f"Implementation UI task {idx}",
            description="implementation ui isolated component",
            files=[f"frontend/ui_{idx}.tsx"],
        )
        for idx in range(8)
    ]
    dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[*_empty_groups(45), *[[task.id] for task in tasks]],
        complete=True,
    )
    metrics = {
        "feature_id": "feat-sizing",
        "active_group": 44,
        "latest_checkpoint_group": 44,
        "baselines": {
            "post_change_completed": {
                "repair_cycles_per_task": 0.5,
                "commit_failures_per_task": 0.25,
                "hours_per_task": 0.5,
            }
        },
        "lane_stats": {
            "lane:implementation-ui": {
                "sample_count": 3,
                "hours_per_task": 0.4,
                "repair_cycles_per_task": 0.1,
                "commit_failures_per_task": 0.0,
            },
            "barrier:implementation-ui": {
                "sample_count": 3,
                "hours_per_task": 0.4,
                "repair_cycles_per_task": 0.1,
                "commit_failures_per_task": 0.0,
            },
        },
    }

    recommendation = dag_regroup.recommend_adaptive_sizing(
        base_dag=dag,
        regroup_candidate=None,
        metrics=metrics,
        from_group=45,
    )

    assert recommendation["remaining_task_count"] == 8
    assert recommendation["current_remaining_wave_count"] == 8
    assert recommendation["recommended_wave_count"] == 1
    assert recommendation["recommended_wave_size_summary"]["max"] == 8


def test_adaptive_sizing_preserves_dependencies_and_write_conflicts():
    tasks = [
        ImplementationTask(
            id="A",
            name="Implementation UI A",
            description="implementation ui",
            files=["frontend/a.tsx"],
        ),
        ImplementationTask(
            id="B",
            name="Implementation UI B",
            description="implementation ui",
            files=["frontend/b.tsx"],
            dependencies=["A"],
        ),
        ImplementationTask(
            id="C",
            name="Implementation UI C",
            description="implementation ui",
            files=["frontend/shared.tsx"],
        ),
        ImplementationTask(
            id="D",
            name="Implementation UI D",
            description="implementation ui",
            files=["frontend/shared.tsx"],
        ),
    ]
    dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[*_empty_groups(45), ["A"], ["B"], ["C"], ["D"]],
        complete=True,
    )
    metrics = {
        "feature_id": "feat-sizing",
        "active_group": 44,
        "latest_checkpoint_group": 44,
        "baselines": {"post_change_completed": {"repair_cycles_per_task": 1.0, "commit_failures_per_task": 1.0, "hours_per_task": 0.5}},
        "lane_stats": {
            "lane:implementation-ui": {"sample_count": 4, "hours_per_task": 0.2, "repair_cycles_per_task": 0.1, "commit_failures_per_task": 0.0},
            "barrier:implementation-ui": {"sample_count": 4, "hours_per_task": 0.2, "repair_cycles_per_task": 0.1, "commit_failures_per_task": 0.0},
        },
    }

    recommendation = dag_regroup.recommend_adaptive_sizing(
        base_dag=dag,
        regroup_candidate=None,
        metrics=metrics,
        from_group=45,
    )
    wave_by_task = {
        task_id: idx
        for idx, wave in enumerate(recommendation["recommended_waves"])
        for task_id in wave["task_ids"]
    }

    assert wave_by_task["A"] < wave_by_task["B"]
    assert wave_by_task["C"] != wave_by_task["D"]


def test_adaptive_sizing_keeps_backend_bridge_high_risk_cap():
    tasks = [
        ImplementationTask(
            id=f"BACKEND-{idx}",
            name=f"Backend bridge task {idx}",
            description="bridge adapter backend foundation",
            files=[f"backend/bridge_{idx}.py"],
        )
        for idx in range(9)
    ]
    dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[*_empty_groups(45), *[[task.id] for task in tasks]],
        complete=True,
    )
    metrics = {
        "feature_id": "feat-sizing",
        "active_group": 44,
        "latest_checkpoint_group": 44,
        "baselines": {"post_change_completed": {"repair_cycles_per_task": 1.0, "commit_failures_per_task": 1.0, "hours_per_task": 0.5}},
        "lane_stats": {
            "lane:backend.bridge/phases": {"sample_count": 4, "hours_per_task": 0.2, "repair_cycles_per_task": 0.1, "commit_failures_per_task": 0.0},
            "barrier:bridge-api-adapter": {"sample_count": 4, "hours_per_task": 0.2, "repair_cycles_per_task": 0.1, "commit_failures_per_task": 0.0},
        },
    }

    recommendation = dag_regroup.recommend_adaptive_sizing(
        base_dag=dag,
        regroup_candidate=None,
        metrics=metrics,
        from_group=45,
    )

    assert max(wave["task_count"] for wave in recommendation["recommended_waves"]) <= 4


def test_adaptive_sizing_allows_low_risk_test_only_waves_to_widen():
    tasks = [
        ImplementationTask(
            id=f"TEST-{idx}",
            name=f"Regression test task {idx}",
            description="workflow test coverage",
            files=[f"tests/unit/test_feature_{idx}.py"],
        )
        for idx in range(12)
    ]
    dag = ImplementationDAG(
        tasks=tasks,
        execution_order=[*_empty_groups(45), *[[task.id] for task in tasks]],
        complete=True,
    )
    metrics = {
        "feature_id": "feat-sizing",
        "active_group": 44,
        "latest_checkpoint_group": 44,
        "baselines": {"post_change_completed": {"repair_cycles_per_task": 1.0, "commit_failures_per_task": 1.0, "hours_per_task": 0.5}},
        "lane_stats": {
            "lane:perf/ci": {"sample_count": 3, "hours_per_task": 0.1, "repair_cycles_per_task": 0.0, "commit_failures_per_task": 0.0},
            "barrier:ci-perf": {"sample_count": 3, "hours_per_task": 0.1, "repair_cycles_per_task": 0.0, "commit_failures_per_task": 0.0},
        },
    }

    recommendation = dag_regroup.recommend_adaptive_sizing(
        base_dag=dag,
        regroup_candidate=None,
        metrics=metrics,
        from_group=45,
    )

    assert recommendation["recommended_wave_count"] == 1
    assert recommendation["recommended_wave_size_summary"]["max"] == 12


def test_process_improvements_rank_observed_drag_classes():
    metrics = {
        "feature_id": "feat-sizing",
        "groups": [
            {
                "group_idx": 50,
                "checkpointed": True,
                "task_count": 4,
                "checkpoint_duration_h": 8.0,
                "commit_failures": 3,
                "acl_normalizations": 2,
                "worktree_alias_events": 1,
                "stale_projection_repairs": 3,
                "rca_count": 0,
                "expanded_verify_count": 0,
                "repair_cycles": 2,
                "dominant_barrier": "implementation-ui",
                "fix_count": 1,
                "verify_count": 1,
                "agent_errors": 0,
                "agent_stalls": 0,
                "tail_risks": [],
                "evidence": {
                    "event_ids": [1],
                    "artifact_ids_by_category": {
                        "commit_failure": [10],
                        "acl_norm": [11],
                        "worktree_alias": [12],
                        "task_reconcile": [13],
                    },
                },
            },
            {
                "group_idx": 51,
                "checkpointed": True,
                "task_count": 4,
                "checkpoint_duration_h": 10.0,
                "commit_failures": 0,
                "acl_normalizations": 0,
                "worktree_alias_events": 0,
                "stale_projection_repairs": 0,
                "rca_count": 3,
                "expanded_verify_count": 3,
                "repair_cycles": 7,
                "dominant_barrier": "backend-foundation",
                "fix_count": 3,
                "verify_count": 4,
                "agent_errors": 1,
                "agent_stalls": 0,
                "tail_risks": ["retry_oscillation_suspected"],
                "evidence": {
                    "event_ids": [2],
                    "artifact_ids_by_category": {
                        "rca": [20],
                        "expanded_verify": [21],
                        "repair_lens": [22],
                        "fix": [23],
                        "verify": [24],
                    },
                },
            },
        ],
    }

    process = dag_regroup.identify_process_improvements(metrics)
    classes = [finding["class"] for finding in process["findings"]]

    assert "commit_hygiene_loops" in classes
    assert "acl_workability_normalization" in classes
    assert "worktree_alias_canonical_path_drift" in classes
    assert "stale_dag_task_projection" in classes
    assert "product_contract_catalog_drift" in classes
    assert process["findings"][0]["estimated_lost_hours"] >= process["findings"][-1]["estimated_lost_hours"]


def test_regroup_planner_preserves_dependency_before_speed():
    dag = _base_dag()
    candidate = dag_regroup.build_staged_regroup(
        dag,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(dag),
        from_group=45,
        to_group=48,
    )
    wave_by_task = {
        task_id: wave_idx
        for wave_idx, wave in enumerate(candidate.dag.execution_order)
        for task_id in wave
    }

    assert wave_by_task["BACKEND-A"] < wave_by_task["BACKEND-D"]
    assert candidate.original_execution_order == dag.execution_order[45:49]


def test_regroup_planner_rejects_partial_remaining_suffix():
    dag = _base_dag()
    dag.execution_order.append(["TAIL"])
    dag.tasks.append(
        ImplementationTask(
            id="TAIL",
            name="Tail task",
            description="Post-regroup tail task",
            files=["tail.py"],
        )
    )

    with pytest.raises(ValueError, match="full remaining DAG suffix"):
        dag_regroup.build_staged_regroup(
            dag,
            base_dag_artifact_id=123,
            base_dag_sha256=_base_hash(dag),
            from_group=45,
            to_group=48,
        )


def test_unknown_write_tasks_do_not_merge_across_original_groups():
    dag = ImplementationDAG(
        tasks=[
            ImplementationTask(id="UNKNOWN-A", name="Task A", description="misc work"),
            ImplementationTask(id="UNKNOWN-B", name="Task B", description="misc work"),
        ],
        execution_order=[*_empty_groups(45), ["UNKNOWN-A"], ["UNKNOWN-B"]],
        complete=True,
    )

    candidate = dag_regroup.build_staged_regroup(
        dag,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(dag),
        from_group=45,
        to_group=46,
    )

    assert candidate.write_sets == {}
    assert candidate.dag.execution_order == [["UNKNOWN-A"], ["UNKNOWN-B"]]


def test_regroup_validator_rejects_cross_barrier_merge():
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="BACKEND-A",
                name="Backend bridge",
                description="bridge adapter",
                files=["backend/bridge.py"],
            ),
            ImplementationTask(
                id="UI-B",
                name="Implementation UI",
                description="implementation ui",
                files=["frontend/implementation.tsx"],
            ),
        ],
        execution_order=[*_empty_groups(45), ["BACKEND-A"], ["UI-B"]],
        complete=True,
    )
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        from_group=45,
        to_group=46,
    )
    candidate = candidate.model_copy(
        deep=True,
        update={
            "dag": candidate.dag.model_copy(
                deep=True,
                update={"execution_order": [["BACKEND-A", "UI-B"]]},
            ),
            "original_to_new_group_mapping": {"45": [45], "46": [45]},
        },
    )

    derived, reason, validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        require_regroup_context=True,
    )

    assert derived is None
    assert reason == "dag_regroup_barrier_violation"
    assert validation[0]["new_group"] == 45


def test_regroup_validator_rejects_lied_cross_barrier_metadata():
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="BACKEND-A",
                name="Backend bridge",
                description="bridge adapter",
                files=["backend/bridge.py"],
            ),
            ImplementationTask(
                id="UI-B",
                name="Implementation UI",
                description="implementation ui",
                files=["frontend/implementation.tsx"],
            ),
        ],
        execution_order=[*_empty_groups(45), ["BACKEND-A"], ["UI-B"]],
        complete=True,
    )
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        from_group=45,
        to_group=46,
    )
    speed_index = candidate.speed_index.copy()
    speed_index["tasks"] = {
        task_id: {**metadata, "barrier": "fake-shared"}
        for task_id, metadata in candidate.speed_index["tasks"].items()
    }
    candidate = candidate.model_copy(
        deep=True,
        update={
            "dag": candidate.dag.model_copy(
                deep=True,
                update={"execution_order": [["BACKEND-A", "UI-B"]]},
            ),
            "original_to_new_group_mapping": {"45": [45], "46": [45]},
            "barriers": [{"id": "fake-shared", "hard": True, "task_ids": ["BACKEND-A", "UI-B"]}],
            "speed_index": speed_index,
        },
    )

    derived, reason, _validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        require_regroup_context=True,
    )

    assert derived is None
    assert reason == "dag_regroup_barrier_violation"


def test_regroup_validator_rejects_lied_file_scope_write_sets():
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-A",
                name="Task A",
                description="misc work",
                file_scope=[{"path": "src/shared.py", "action": "modify"}],
            ),
            ImplementationTask(
                id="TASK-B",
                name="Task B",
                description="misc work",
                file_scope=[{"path": "src/shared.py", "action": "modify"}],
            ),
        ],
        execution_order=[*_empty_groups(45), ["TASK-A"], ["TASK-B"]],
        complete=True,
    )
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        from_group=45,
        to_group=46,
    )
    candidate = candidate.model_copy(
        deep=True,
        update={
            "dag": candidate.dag.model_copy(
                deep=True,
                update={"execution_order": [["TASK-A", "TASK-B"]]},
            ),
            "original_to_new_group_mapping": {"45": [45], "46": [45]},
            "write_sets": {"TASK-A": ["fake/a.py"], "TASK-B": ["fake/b.py"]},
        },
    )

    derived, reason, validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        require_regroup_context=True,
    )

    assert derived is None
    assert reason == "derived_dag_write_set_conflict"
    assert validation[0]["overlap"] == ["src/shared.py"]


def test_regroup_validator_rejects_stale_base_hash():
    base = _base_dag()
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256="stale",
        from_group=45,
        to_group=48,
    )

    derived, reason, validation = implementation_module._validate_derived_dag_artifact_update(
        candidate.artifact_key,
        candidate.model_dump_json(),
        base_dag=base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        require_regroup_context=True,
    )

    assert derived is None
    assert reason == "dag_regroup_base_dag_hash_mismatch"
    assert validation[0]["actual_base_dag_sha256"] == "stale"


# ── WB-1: absolute repo_path normalization tests ────────────────────────────

def test_wb1_absolute_repo_path_treated_as_unset_in_task_paths():
    """_task_paths treats absolute repo_path as unset (N-17 tolerance).

    When a task carries an absolute repo_path (e.g. /Users/.../repos), it must
    be treated as unset so it does not produce a ``Users/...`` top-level
    directory prefix in the write-set that would poison barrier and commit-risk
    heuristics and cause the validator's step-10 to reject.
    """
    import warnings

    task_abs = ImplementationTask(
        id="TASK-ABS",
        name="Absolute path task",
        description="task with absolute repo_path",
        files=["src/service.py"],
        file_scope=[{"path": "src/service.py", "action": "modify"}],
        repo_path="/Users/someone/workspaces/kaya-main/.iriai/.../repos",
    )
    task_rel = ImplementationTask(
        id="TASK-REL",
        name="Relative path task",
        description="task with no repo_path",
        files=["src/service.py"],
        file_scope=[{"path": "src/service.py", "action": "modify"}],
    )

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        paths_abs = dag_regroup._task_paths(task_abs)
        assert any("absolute" in str(w.message).lower() for w in caught), (
            "_task_paths must warn when repo_path is absolute"
        )

    paths_rel = dag_regroup._task_paths(task_rel)

    # Absolute repo_path → same result as no repo_path (just the bare path)
    assert paths_abs == paths_rel, (
        "_task_paths with absolute repo_path must equal _task_paths with no repo_path; "
        f"got abs={paths_abs} rel={paths_rel}"
    )
    # Must NOT contain the stripped absolute prefix as a top-level dir
    assert not any(p.startswith("Users/") for p in paths_abs), (
        "paths must not gain a Users/ top-level prefix when repo_path is absolute"
    )


def test_wb1_absolute_repo_path_write_sets_superset_of_validator():
    """Builder write_sets with absolute repo_path pass validate_overlay step 10.

    When tasks have absolute repo_path, the builder's _task_write_paths_for_overlay
    (which treats absolute as unset) must produce write_sets that are a superset of
    what _task_declared_write_paths (the validator) expects — so step 10
    (dag_regroup_write_set_removes_authoritative_path) does not reject.
    """
    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _task_declared_write_paths,
    )

    abs_path = "/Users/danielzhang/src/kaya/kaya-main/.iriai/.../repos"
    task = ImplementationTask(
        id="TASK-WB1",
        name="WB1 task",
        description="absolute repo_path task",
        files=["shared_libs/kaya_db/kaya_db/permissions.py"],
        file_scope=[{"path": "supply-chain/app/config/permissions.py", "action": "modify"}],
        repo_path=abs_path,
    )

    import warnings
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        builder_paths = dag_regroup._task_write_paths_for_overlay(task)
        declared_paths = _task_declared_write_paths(task)

    # The overlay write_set (builder) must be a superset of the declared paths (validator).
    # With N-17 applied to both, both see repo_path="" and produce identical bare paths.
    assert declared_paths.issubset(builder_paths), (
        f"Validator declared paths {declared_paths} are not a subset of "
        f"builder write_paths {builder_paths}: step 10 would reject"
    )
    # Neither should contain the stripped 'Users/...' prefix
    all_paths = builder_paths | declared_paths
    assert not any(p.startswith("Users/") for p in all_paths), (
        "No path should start with Users/ when repo_path is absolute"
    )


def test_wb1_build_staged_regroup_write_sets_pass_step10_with_absolute_repo_path():
    """build_staged_regroup write_sets pass validate_overlay step 10 when tasks
    have absolute repo_path (regression test for the live DAG 2248266 pattern).
    """
    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _task_declared_write_paths,
    )
    import warnings

    abs_path = "/Users/danielzhang/src/kaya/kaya-main/.iriai/.../repos"
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="TASK-PERMS",
                name="Permissions",
                description="add permission rows",
                files=["supply-chain/app/config/permissions.py"],
                file_scope=[{"path": "supply-chain/app/config/permissions.py", "action": "modify"}],
                repo_path=abs_path,
            ),
            ImplementationTask(
                id="TASK-ROUTER",
                name="Router",
                description="add router endpoints",
                files=["supply-chain/app/supply_chain/api/v2/routers/knowledge/submittal_management.py"],
                file_scope=[{"path": "supply-chain/app/supply_chain/api/v2/routers/knowledge/submittal_management.py", "action": "modify"}],
                repo_path=abs_path,
            ),
        ],
        execution_order=[*_empty_groups(45), ["TASK-PERMS"], ["TASK-ROUTER"]],
        complete=True,
    )

    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        candidate = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=1,
            base_dag_sha256=_base_hash(base),
            from_group=45,
            to_group=46,
        )

    # For each task, builder write_sets must be a superset of declared paths.
    tasks_by_id = {t.id: t for t in base.tasks}
    for task_id, overlay_paths in candidate.write_sets.items():
        task = tasks_by_id[task_id]
        with warnings.catch_warnings(record=True):
            warnings.simplefilter("always")
            declared = _task_declared_write_paths(task)
        overlay_set = set(overlay_paths)
        assert declared.issubset(overlay_set), (
            f"task {task_id}: validator declared {declared - overlay_set} not covered "
            f"by builder write_sets {overlay_set} — step 10 would reject"
        )


# ── WB-2: conversion sha normalization tests ────────────────────────────────

def test_wb2_derived_artifact_to_regroup_overlay_sha_is_normalization_fixed_point():
    """derived_artifact_to_regroup_overlay produces a sha that matches after renormalization.

    The builder orders waves by sort_key (not lexicographically). If the sha is
    stamped BEFORE normalize_overlay re-sorts within-wave task ids, step 11
    (required_overlay_sha256 == overlay_sha256 after renormalization) rejects.
    WB-2 fix: normalize first, stamp sha from the normalized form.
    """
    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _canonical_overlay_sha,
        _normalize_overlay,
    )

    # Build a DAG where sort_key ordering != lex ordering to force a non-trivial wave.
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="ZZZZZ-UI",
                name="Z-named UI task",
                description="small ui polish",
                files=["frontend/ui.tsx"],
            ),
            ImplementationTask(
                id="AAAA-UI",
                name="A-named UI task",
                description="other ui work",
                files=["frontend/other.tsx"],
            ),
        ],
        execution_order=[*_empty_groups(45), ["ZZZZZ-UI", "AAAA-UI"]],
        complete=True,
    )
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=1,
        base_dag_sha256=_base_hash(base),
        from_group=45,
        to_group=45,
    )

    overlay = dag_regroup.derived_artifact_to_regroup_overlay(
        candidate,
        base,
        feature_id="wb2-test",
    )

    # After renormalization the sha must not change (fixed-point invariant)
    renormalized = _normalize_overlay(overlay)
    assert renormalized.overlay_sha256 == overlay.overlay_sha256, (
        "WB-2: overlay_sha256 is not a normalization fixed-point — step 11 would "
        f"reject. pre={overlay.overlay_sha256!r} post={renormalized.overlay_sha256!r}"
    )
    # required_overlay_sha256 must equal overlay_sha256
    assert overlay.activation_contract.required_overlay_sha256 == overlay.overlay_sha256, (
        "WB-2: activation_contract.required_overlay_sha256 must equal overlay_sha256"
    )


def test_wb2_sha_fixed_point_with_sort_key_ordered_wave():
    """When sort_key order differs from lex order, WB-2 fix keeps sha fixed-point."""
    from iriai_build_v2.workflows.develop.execution.regroup_overlay_validation import (
        _normalize_overlay,
    )

    # Tasks named so that sort_key order (by barrier/lane/cost) differs from lex order.
    # BACKEND task has higher priority (lane rank 0 for artifacts) → sorts first by sort_key
    # even though "ZFRONTEND" < "ABACKEND" in reverse-lex might flip.
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="ZFRONTEND-UI",
                name="Z frontend task",
                description="small ui polish",
                files=["frontend/zzz.tsx"],
            ),
            ImplementationTask(
                id="ABACKEND-ART",
                name="A backend artifact",
                description="artifact backend foundation work items",
                files=["backend/aaa-artifacts.py"],
            ),
        ],
        execution_order=[*_empty_groups(45), ["ZFRONTEND-UI"], ["ABACKEND-ART"]],
        complete=True,
    )
    base_sha = _base_hash(base)
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=99,
        base_dag_sha256=base_sha,
        from_group=45,
        to_group=46,
    )
    overlay = dag_regroup.derived_artifact_to_regroup_overlay(
        candidate,
        base,
        feature_id="wb2-sort-key-test",
    )

    renormalized = _normalize_overlay(overlay)
    assert renormalized.overlay_sha256 == overlay.overlay_sha256, (
        "WB-2: overlay sha changed after renormalization (sort_key vs lex order mismatch)"
    )


@pytest.mark.asyncio
async def test_executor_applies_persisted_active_regroup_marker():
    base = _base_dag()
    base_json = base.model_dump_json()
    base_sha = hashlib.sha256(base_json.encode("utf-8")).hexdigest()
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=base_sha,
        from_group=45,
        to_group=48,
    )
    canonical_text = candidate.model_dump_json()
    canonical_sha = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    feature = SimpleNamespace(id="feat-regroup-executor", slug="regroup-executor")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag": base_json,
                "dag-group:44": json.dumps({"group_idx": 44}),
                dag_regroup.CANONICAL_KEY: canonical_text,
                dag_regroup.ACTIVE_KEY: json.dumps({
                    "status": "active",
                    "canonical_artifact_key": dag_regroup.CANONICAL_KEY,
                    "regroup_sha256": canonical_sha,
                    "rollback_artifact_key": dag_regroup.ROLLBACK_KEY,
                    "base_dag_artifact_id": 123,
                    "base_dag_sha256": base_sha,
                }),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 123, "value": value, "created_at": "now"}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    events: list[dict[str, object]] = []

    async def _log_event(feature_id, event_type, source, content=None, metadata=None):
        events.append({
            "feature_id": feature_id,
            "event_type": event_type,
            "source": source,
            "content": content,
            "metadata": metadata,
        })

    runner = SimpleNamespace(
        artifacts=_Artifacts(),
        services={},
        feature_store=SimpleNamespace(log_event=_log_event),
    )

    effective, failure, observation = (
        await implementation_module._resolve_active_regroup_before_group_dispatch(
            runner,
            feature,
            base,
            group_idx=45,
        )
    )

    assert failure == ""
    assert effective is not None
    assert effective.execution_order[:45] == base.execution_order[:45]
    assert effective.execution_order[45:] == candidate.dag.execution_order
    assert observation["status"] == "applied"
    assert dag_regroup.OBSERVATION_KEY in runner.artifacts.store
    assert events[0]["event_type"] == "dag_regroup_overlay_applied"


@pytest.mark.asyncio
async def test_executor_applies_active_regroup_marker_after_restart_past_g45():
    base = _base_dag()
    base_json = base.model_dump_json()
    base_sha = hashlib.sha256(base_json.encode("utf-8")).hexdigest()
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=base_sha,
        from_group=45,
        to_group=48,
    )
    canonical_text = candidate.model_dump_json()
    canonical_sha = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    feature = SimpleNamespace(id="feat-regroup-restart", slug="regroup-restart")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag": base_json,
                "dag-group:45": json.dumps({"group_idx": 45}),
                dag_regroup.CANONICAL_KEY: canonical_text,
                dag_regroup.ACTIVE_KEY: json.dumps({
                    "status": "active",
                    "canonical_artifact_key": dag_regroup.CANONICAL_KEY,
                    "regroup_sha256": canonical_sha,
                    "rollback_artifact_key": dag_regroup.ROLLBACK_KEY,
                    "base_dag_artifact_id": 123,
                    "base_dag_sha256": base_sha,
                }),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 123, "value": value, "created_at": "now"}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    effective, failure, observation = (
        await implementation_module._resolve_active_regroup_before_group_dispatch(
            runner,
            feature,
            base,
            group_idx=46,
        )
    )

    assert failure == ""
    assert effective is not None
    assert effective.execution_order[45:] == candidate.dag.execution_order
    assert observation["resume_group_idx"] == 46


@pytest.mark.asyncio
async def test_executor_rejects_active_marker_without_activation_hash():
    base = _base_dag()
    base_json = base.model_dump_json()
    base_sha = hashlib.sha256(base_json.encode("utf-8")).hexdigest()
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=base_sha,
        from_group=45,
        to_group=48,
    )
    feature = SimpleNamespace(id="feat-regroup-incomplete", slug="regroup-incomplete")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag": base_json,
                "dag-group:44": json.dumps({"group_idx": 44}),
                dag_regroup.CANONICAL_KEY: candidate.model_dump_json(),
                dag_regroup.ACTIVE_KEY: json.dumps({
                    "status": "active",
                    "canonical_artifact_key": dag_regroup.CANONICAL_KEY,
                    "rollback_artifact_key": dag_regroup.ROLLBACK_KEY,
                    "base_dag_artifact_id": 123,
                    "base_dag_sha256": base_sha,
                }),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    effective, failure, observation = (
        await implementation_module._resolve_active_regroup_before_group_dispatch(
            runner,
            feature,
            base,
            group_idx=45,
        )
    )

    assert effective is None
    assert "missing activation proof fields" in failure
    assert observation["reason"] == "active_regroup_marker_incomplete"
    assert observation["missing_fields"] == ["regroup_sha256"]


@pytest.mark.asyncio
async def test_executor_rejects_non_object_active_marker():
    base = _base_dag()
    feature = SimpleNamespace(id="feat-regroup-non-object", slug="regroup-non-object")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                dag_regroup.ACTIVE_KEY: "[]",
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    effective, failure, observation = (
        await implementation_module._resolve_active_regroup_before_group_dispatch(
            runner,
            feature,
            base,
            group_idx=45,
        )
    )

    assert effective is None
    assert "must be a JSON object" in failure
    assert observation["reason"] == "invalid_active_regroup_marker_type"
    assert observation["marker_type"] == "list"


@pytest.mark.asyncio
async def test_implement_dag_dispatches_regrouped_first_wave(monkeypatch):
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="LOW-UI",
                name="Low priority UI",
                description="small ui polish",
                files=["frontend/ui.tsx"],
            ),
            ImplementationTask(
                id="FAST-BACKEND",
                name="Backend artifact foundation",
                description="artifact backend foundation",
                files=["backend/artifacts.py"],
            ),
        ],
        execution_order=[*_empty_groups(45), ["LOW-UI"], ["FAST-BACKEND"]],
        complete=True,
    )
    base_json = base.model_dump_json()
    base_sha = hashlib.sha256(base_json.encode("utf-8")).hexdigest()
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=base_sha,
        from_group=45,
        to_group=46,
        artifact_key=dag_regroup.CANONICAL_KEY,
    )
    assert candidate.dag.execution_order[0] == ["FAST-BACKEND"]
    canonical_text = candidate.model_dump_json()
    canonical_sha = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    feature = SimpleNamespace(id="feat-implement-regroup", slug="implement-regroup")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag": base_json,
                **{
                    f"dag-group:{idx}": json.dumps({"group_idx": idx, "results": []})
                    for idx in range(45)
                },
                # The strict resume guard (implementation.py:2510 + dispatch
                # gate ~:20988) blocks any feature with pre-existing
                # dag-group:* checkpoints unless a valid adoption marker
                # exists; mirror the production writer's key + body (see
                # _strict_adoption_marker above) for the seeded 0..44 range.
                f"execution-control-adoption:{feature.id}": _strict_adoption_marker(
                    feature,
                    completed_range=(0, 44),
                    next_group=45,
                ),
                dag_regroup.CANONICAL_KEY: canonical_text,
                dag_regroup.ACTIVE_KEY: json.dumps({
                    "status": "active",
                    "canonical_artifact_key": dag_regroup.CANONICAL_KEY,
                    "regroup_sha256": canonical_sha,
                    "rollback_artifact_key": dag_regroup.ROLLBACK_KEY,
                    "base_dag_artifact_id": 123,
                    "base_dag_sha256": base_sha,
                }),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 123, "value": value, "created_at": "now"}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    verified_groups: list[tuple[int, list[str]]] = []

    async def _fake_verify_and_fix_group(
        runner,
        feature,
        group_idx,
        group_tasks,
        *args,
        **kwargs,
    ):
        del runner, feature, args, kwargs
        verified_groups.append((group_idx, [task.id for task in group_tasks]))
        return True, ""

    async def _noop_async(*args, **kwargs):
        del args, kwargs
        return None

    async def _no_enhancement(*args, **kwargs):
        del args, kwargs
        return ""

    # The Slice 04/05 dispatcher binds a per-task sandbox before invoking the
    # runtime; this test exercises regroup overlay wave ORDERING, not the
    # sandbox/merge-queue machinery (covered by its own suites). Stub the
    # dispatcher to invoke the runtime directly and return a bare result (no
    # `pending_durable_merge_queue` note) so the legacy verify+checkpoint path
    # the test stubs (`_verify_and_fix_group`) runs.
    async def _fake_dispatch(*, runner, feature, task, **kwargs):
        del kwargs
        result = await runner.run(None, feature)
        result.task_id = task.id
        return result, SimpleNamespace(status="succeeded", attempt_id=1)

    # The pre-seeded `dag-group:*` artifacts stand in for already-checkpointed
    # groups; the post-Slice-06 freshness gate also requires durable proof
    # artifacts, which this regroup-focused test does not construct. Treat any
    # pre-seeded `dag-group:*` body as fresh so the resume scan skips it.
    async def _fake_checkpoint_fresh(runner, feature, *, group_idx, **kwargs):
        del kwargs
        return bool(
            await runner.artifacts.get(f"dag-group:{group_idx}", feature=feature)
        )

    # The Slice 03 task-deliverable-contract compiler needs a workspace
    # registry; this regroup-focused test runs without a workspace manager.
    async def _fake_compile_contracts(*args, **kwargs):
        del args, kwargs
        return implementation_module.TaskContractCompileOutcome()

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_async)
    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_async)
    monkeypatch.setattr(implementation_module, "_run_enhancement_group", _no_enhancement)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _fake_verify_and_fix_group)
    monkeypatch.setattr(
        implementation_module,
        "_dispatch_task_attempt_via_runtime_dispatcher",
        _fake_dispatch,
    )
    # `_run_task` resolves a workspace-authority repo binding before invoking
    # the (stubbed) dispatcher; this test has no workspace manager/registry, so
    # stub the binding the same way test_dag_expanded_verify.py does for its
    # `_implement_dag` dispatch test (sandbox binding correctness is covered by
    # the workspace-authority adapter suite).
    monkeypatch.setattr(
        implementation_module,
        "_resolve_task_dispatch_repo_binding",
        lambda **_kwargs: implementation_module._TaskDispatchRepoBinding(
            repo_id="",
            repo_path="",
            ws_path="",
            source="test",
        ),
    )
    monkeypatch.setattr(
        implementation_module, "_dag_group_checkpoint_is_fresh", _fake_checkpoint_fresh
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _fake_compile_contracts,
    )

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}

        async def run(self, ask, feature, phase_name=None):
            del ask, feature, phase_name
            return ImplementationResult(
                task_id="FAST-BACKEND",
                summary="done",
                files_modified=["backend/artifacts.py"],
            )

    outcome = await implementation_module._implement_dag(_Runner(), feature, base)

    assert outcome.terminal_state == "complete"
    assert verified_groups[0] == (45, ["FAST-BACKEND"])
    assert verified_groups[1] == (46, ["LOW-UI"])


@pytest.mark.asyncio
async def test_implement_dag_resume_scans_expanded_regroup_checkpoints(monkeypatch):
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="BACKEND-A",
                name="Backend artifact foundation",
                description="artifact backend foundation",
                files=["backend/artifacts.py"],
            ),
            ImplementationTask(
                id="IMPLEMENT-UI",
                name="Implementation UI",
                description="implementation ui panel",
                files=["frontend/implementation.tsx"],
            ),
            ImplementationTask(
                id="REVIEW-UI",
                name="Review UI",
                description="review ui panel",
                files=["frontend/review.tsx"],
            ),
        ],
        execution_order=[
            *_empty_groups(45),
            ["IMPLEMENT-UI", "BACKEND-A"],
            ["REVIEW-UI"],
        ],
        complete=True,
    )
    base_json = base.model_dump_json()
    base_sha = hashlib.sha256(base_json.encode("utf-8")).hexdigest()
    candidate = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=base_sha,
        from_group=45,
        to_group=46,
        artifact_key=dag_regroup.CANONICAL_KEY,
    )
    assert len(candidate.dag.execution_order) + 45 > len(base.execution_order)
    canonical_text = candidate.model_dump_json()
    canonical_sha = hashlib.sha256(canonical_text.encode("utf-8")).hexdigest()
    feature = SimpleNamespace(id="feat-implement-resume", slug="implement-resume")

    class _Artifacts:
        def __init__(self) -> None:
            self.store = {
                "dag": base_json,
                **{
                    f"dag-group:{idx}": json.dumps({"group_idx": idx, "results": []})
                    for idx in range(48)
                },
                # Strict resume guard (see _strict_adoption_marker): valid
                # adoption marker covering the fully checkpointed expanded
                # regroup range 0..47, resuming at the first post-baseline
                # group (48 == effective group count → nothing to dispatch).
                f"execution-control-adoption:{feature.id}": _strict_adoption_marker(
                    feature,
                    completed_range=(0, 47),
                    next_group=48,
                ),
                dag_regroup.CANONICAL_KEY: canonical_text,
                dag_regroup.ACTIVE_KEY: json.dumps({
                    "status": "active",
                    "canonical_artifact_key": dag_regroup.CANONICAL_KEY,
                    "regroup_sha256": canonical_sha,
                    "rollback_artifact_key": dag_regroup.ROLLBACK_KEY,
                    "base_dag_artifact_id": 123,
                    "base_dag_sha256": base_sha,
                }),
            }

        async def get(self, key: str, *, feature):
            del feature
            return self.store.get(key, "")

        async def get_record(self, key: str, *, feature):
            del feature
            value = self.store.get(key)
            if value is None:
                return None
            return {"id": 123, "value": value, "created_at": "now"}

        async def put(self, key: str, value: str, *, feature):
            del feature
            self.store[key] = value

    verified_groups: list[int] = []
    enhancement_seen: list[bool] = []

    async def _fake_verify_and_fix_group(*args, **kwargs):
        del kwargs
        verified_groups.append(args[2])
        return True, ""

    async def _no_enhancement(*args, **kwargs):
        del args, kwargs
        enhancement_seen.append(True)
        return ""

    # Production `_implement_dag` `await`s `_ensure_task_worktrees` /
    # `_commit_repos`, so the stubs must be async coroutines (a sync `lambda`
    # returning None raises `TypeError: object NoneType can't be used in
    # 'await' expression`).
    async def _noop_async(*args, **kwargs):
        del args, kwargs
        return None

    # The post-Slice-06 resume freshness gate also requires durable proof
    # artifacts; this regroup-resume test pre-seeds only `dag-group:*` bodies.
    # Treat any pre-seeded body as fresh so the resume scan skips a fully
    # expanded+checkpointed DAG (the case under test).
    async def _fake_checkpoint_fresh(runner, feature, *, group_idx, **kwargs):
        del kwargs
        return bool(
            await runner.artifacts.get(f"dag-group:{group_idx}", feature=feature)
        )

    # Contract compilation needs a workspace registry absent here; stubbing it
    # keeps the "must not dispatch" assertion meaningful — were a group wrongly
    # not skipped, dispatch would reach `_Runner.run` and raise.
    async def _fake_compile_contracts(*args, **kwargs):
        del args, kwargs
        return implementation_module.TaskContractCompileOutcome()

    monkeypatch.setattr(implementation_module, "_ensure_task_worktrees", _noop_async)
    monkeypatch.setattr(implementation_module, "_commit_repos", _noop_async)
    monkeypatch.setattr(implementation_module, "_verify_and_fix_group", _fake_verify_and_fix_group)
    monkeypatch.setattr(implementation_module, "_run_enhancement_group", _no_enhancement)
    monkeypatch.setattr(
        implementation_module, "_dag_group_checkpoint_is_fresh", _fake_checkpoint_fresh
    )
    monkeypatch.setattr(
        implementation_module,
        "_compile_task_contracts_for_group",
        _fake_compile_contracts,
    )

    class _Runner:
        def __init__(self) -> None:
            self.artifacts = _Artifacts()
            self.services = {}

        async def run(self, *args, **kwargs):
            raise AssertionError("resume after completed expanded regroup must not dispatch tasks")

    outcome = await implementation_module._implement_dag(_Runner(), feature, base)

    assert outcome.terminal_state == "complete"
    assert verified_groups == []
    assert enhancement_seen == [True]


@pytest.mark.asyncio
async def test_executor_quiesces_when_active_regroup_marker_missing():
    base = _base_dag()
    feature = SimpleNamespace(id="feat-regroup-missing", slug="regroup-missing")

    class _Artifacts:
        async def get(self, key: str, *, feature):
            del key, feature
            return ""

    runner = SimpleNamespace(artifacts=_Artifacts(), services={})

    effective, failure, observation = (
        await implementation_module._resolve_active_regroup_before_group_dispatch(
            runner,
            feature,
            base,
            group_idx=45,
        )
    )

    assert effective is None
    assert "dag-regroup-active:g45-g73 is missing" in failure
    assert observation["reason"] == "missing_active_regroup_marker"


# ── Rewired CLI activation / rollback tests (Slice 09e-1b, real Postgres) ───
#
# These exercise the rewired ``command_activate`` / ``command_rollback``, which
# now drive the typed ``activate_overlay`` / ``rollback_overlay`` (09c-1) over
# the ``execution_regroup_overlays`` tables. The behavior change is genuine:
# the CLI writes a typed overlay row + the canonical / rollback / active-marker
# compatibility projections + a typed event, in ONE transaction under the
# feature advisory lock. The tests are UPDATED (not weakened): every
# safety-check, the not-started boundary, and the reason codes are still
# asserted — now against the real typed transaction — and typed-row assertions
# are ADDED.


async def _seed_regroup_base(
    conn: asyncpg.Connection, feature_id: str, base: ImplementationDAG
) -> tuple[int, str]:
    """Seed the ``features`` row + the base ``dag`` artifact; return (id, sha).

    The sha is ``sha256`` over the raw stored ``dag`` value — the identical
    computation ``RegroupOverlayStore.load_dag_artifact`` uses, so the draft's
    ``base_dag_sha256`` matches what ``validate_overlay`` step 2 will load.
    """

    await _seed_feature(conn, feature_id)
    dag_json = base.model_dump_json()
    dag_id = await _seed_artifact(conn, feature_id, "dag", dag_json)
    return dag_id, hashlib.sha256(dag_json.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_activate_writes_typed_overlay_and_compatibility_projections(
    monkeypatch, regroup_dsn
):
    """command_activate persists a typed overlay row + the compatibility
    projections atomically, and the typed resolver then applies it.

    Behavior-change update of the legacy ``test_activate_writes_restart_safe_
    artifacts``: the legacy assertion was "the CANONICAL/ROLLBACK/ACTIVE
    *artifacts* exist"; the rewired CLI ALSO writes a typed
    ``execution_regroup_overlays`` row (``status='active'``) and the typed
    ``dag_regroup_overlay_activated`` event. The compatibility-artifact
    assertions are KEPT; the typed-row + typed-resolver assertions are ADDED.
    """

    feature_id = "feat-activate"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        # The boundary checkpoint dag-group:44 must exist before activation.
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    result = await dag_regroup.command_activate(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            from_group=45,
            skip_safety=False,
        )
    )

    assert result["ok"] is True
    # The typed overlay slug is derived from the base DAG shape (offset 45,
    # last group 48) — NOT the hard-coded g45-g73.
    assert result["overlay_slug"] == "g45-g48"
    canonical_key = "dag-regroup:g45-g48"
    active_marker_key = "dag-regroup-active:g45-g48"
    rollback_key = "dag-regroup-rollback:g45-g48"
    assert result["canonical_artifact_key"] == canonical_key
    assert result["active_marker_key"] == active_marker_key
    assert result["rollback_artifact_key"] == rollback_key

    conn = await asyncpg.connect(regroup_dsn)
    try:
        # The typed overlay row exists and is `active` (the rewire's new
        # behavior — the legacy facade wrote NO typed row).
        row = await conn.fetchrow(
            "SELECT status, group_idx_offset, active_marker_projection_id "
            "FROM execution_regroup_overlays WHERE feature_id = $1",
            feature_id,
        )
        assert row is not None
        assert row["status"] == "active"
        assert row["group_idx_offset"] == 45
        assert row["active_marker_projection_id"] == result["active_artifact_id"]

        # The three compatibility projections were written (legacy-compat
        # assertions, preserved).
        async def _latest(key: str) -> str | None:
            return await conn.fetchval(
                "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
                "ORDER BY id DESC LIMIT 1",
                feature_id,
                key,
            )

        assert await _latest(canonical_key) is not None
        assert await _latest(rollback_key) is not None
        marker = json.loads(await _latest(active_marker_key))
        assert marker["status"] == "active"
        assert marker["base_dag_artifact_id"] == dag_id
        assert marker["base_dag_sha256"] == base_sha
        assert marker["canonical_artifact_key"] == canonical_key
        assert marker["canonical_artifact_id"] == result["canonical_artifact_id"]
        assert marker["rollback_artifact_id"] == result["rollback_artifact_id"]

        # The typed activation event was written.
        event_type = await conn.fetchval(
            "SELECT event_type FROM events WHERE id = $1",
            result["activation_event_id"],
        )
        assert event_type == "dag_regroup_overlay_activated"

        # A typed validation row was recorded by the in-activation
        # validate_overlay (the CLI activation routes through validate_overlay).
        validation_count = await conn.fetchval(
            "SELECT count(*) FROM execution_regroup_validations "
            "WHERE feature_id = $1 AND valid = true",
            feature_id,
        )
        assert int(validation_count) >= 1
    finally:
        await conn.close()

    # The typed RegroupOverlayResolver applies the just-activated overlay.
    feature = SimpleNamespace(id=feature_id, slug="activate")
    resolver_pool = await asyncpg.create_pool(regroup_dsn, min_size=1, max_size=4)
    try:
        runner = SimpleNamespace(
            artifacts=_RealArtifacts(resolver_pool, feature_id),
            services={},
        )
        effective, failure, observation = (
            await implementation_module._resolve_active_regroup_before_group_dispatch(
                runner,
                feature,
                base,
                group_idx=45,
            )
        )
    finally:
        await resolver_pool.close()
    assert failure == ""
    assert effective is not None
    assert effective.execution_order[45:] == draft.dag.execution_order
    assert observation["status"] == "applied"


@pytest.mark.asyncio
async def test_activate_g45_g73_shape_keeps_legacy_projection_key(
    monkeypatch, regroup_dsn
):
    """A base DAG whose suffix is exactly groups 45..73 still produces the
    legacy ``g45-g73`` compatibility projection keys.

    doc 09 § "Proposed Interfaces/Types": "The existing ``g45-g73`` spelling
    remains a compatibility projection for that exact suffix." This preserves
    the legacy-compatibility assertion for the one shape the literal applies
    to (the rewire only generalizes the slug — it does not drop the g45-g73
    spelling for the 74-group DAG).
    """

    feature_id = "feat-activate-g73"
    tasks = [
        ImplementationTask(
            id=f"T{idx}",
            name=f"task {idx}",
            description="backend artifact foundation work",
            files=[f"backend/mod_{idx}.py"],
        )
        for idx in range(45, 74)
    ]
    # 74 groups total (indexes 0..73); the regroup suffix is groups 45..73.
    base = ImplementationDAG(
        tasks=tasks,
        execution_order=[*_empty_groups(45), *[[task.id] for task in tasks]],
        num_teams=1,
        complete=True,
    )
    assert len(base.execution_order) == 74

    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=73,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    result = await dag_regroup.command_activate(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            from_group=45,
            skip_safety=False,
        )
    )

    assert result["ok"] is True
    # The legacy g45-g73 spelling, preserved for the exact 45..73 suffix.
    assert result["overlay_slug"] == "g45-g73"
    assert result["canonical_artifact_key"] == dag_regroup.CANONICAL_KEY
    assert result["active_marker_key"] == dag_regroup.ACTIVE_KEY
    assert result["rollback_artifact_key"] == dag_regroup.ROLLBACK_KEY
    assert dag_regroup.CANONICAL_KEY == "dag-regroup:g45-g73"


@pytest.mark.asyncio
async def test_activate_fails_closed_when_overlay_fails_validation(
    monkeypatch, regroup_dsn
):
    """A draft that fails the 13-step validate_overlay does NOT activate.

    The rewired ``command_activate`` routes the CLI activation safety checks
    through ``validate_overlay`` (doc 09 item 3). A draft whose
    ``original_to_new_group_mapping`` was tampered (an original suffix group
    dropped) is rejected by ``validate_overlay`` step 8
    (``dag_regroup_mapping_group_coverage_mismatch``), and NO typed overlay
    row flips to ``active`` (fail-closed). Replaces the legacy facade's
    hand-rolled ``validate_candidate`` rejection path.

    NOTE: the tamper targets a field the conversion layer transcribes verbatim
    from the candidate (``original_to_new_group_mapping``) — the conversion
    layer derives ``remaining_dependency_edges`` / fingerprints from the BASE
    DAG, so tampering the draft's task list alone would be silently corrected;
    tampering the mapping produces a genuinely invalid typed overlay.
    """

    feature_id = "feat-activate-invalid"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        # Tamper: drop original suffix group 48 from the mapping. The base DAG
        # suffix is groups 45..48, so validate_overlay step 8 rejects with
        # dag_regroup_mapping_group_coverage_mismatch (a missing original
        # group). The conversion layer transcribes the mapping verbatim, so the
        # tamper survives into the typed overlay.
        tampered_mapping = {
            k: v
            for k, v in draft.original_to_new_group_mapping.items()
            if k != "48"
        }
        assert "48" in draft.original_to_new_group_mapping
        tampered = draft.model_copy(
            update={"original_to_new_group_mapping": tampered_mapping}
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, tampered.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    with pytest.raises(
        RuntimeError, match="dag_regroup_mapping_group_coverage_mismatch"
    ):
        await dag_regroup.command_activate(
            SimpleNamespace(
                database_url=regroup_dsn,
                feature_id=feature_id,
                from_group=45,
                skip_safety=False,
            )
        )

    # Fail-closed: no overlay row flipped to active.
    conn = await asyncpg.connect(regroup_dsn)
    try:
        active_count = await conn.fetchval(
            "SELECT count(*) FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND status = 'active'",
            feature_id,
        )
        assert int(active_count) == 0
        # And no canonical / active-marker compatibility projection was written.
        canonical = await conn.fetchval(
            "SELECT 1 FROM artifacts WHERE feature_id = $1 AND key = $2 LIMIT 1",
            feature_id,
            "dag-regroup:g45-g48",
        )
        assert canonical is None
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_activate_rejects_when_boundary_checkpoint_missing(
    monkeypatch, regroup_dsn
):
    """Activation fails closed when ``dag-group:{checkpointed_group}`` is absent.

    doc 09 § "Activation And Rollback Constraints": the boundary checkpoint
    must exist. The rewired CLI delegates this to ``activate_overlay``'s
    in-transaction forbidden-set check. (Replaces the legacy facade's
    hand-rolled ``dag-group:{from_group-1} is required`` check — the not-started
    boundary coverage is preserved, now enforced by the typed path.)
    """

    feature_id = "feat-activate-no-ckpt"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        # NOTE: dag-group:44 is deliberately NOT seeded.
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    # validate_overlay step 3 rejects with dag_regroup_boundary_checkpoint_
    # missing (the checkpointed group does not exist) — the CLI surfaces it.
    with pytest.raises(RuntimeError, match="dag_regroup_boundary_checkpoint_missing"):
        await dag_regroup.command_activate(
            SimpleNamespace(
                database_url=regroup_dsn,
                feature_id=feature_id,
                from_group=45,
                skip_safety=False,
            )
        )

    conn = await asyncpg.connect(regroup_dsn)
    try:
        active_count = await conn.fetchval(
            "SELECT count(*) FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND status = 'active'",
            feature_id,
        )
        assert int(active_count) == 0
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_activate_rejects_a_different_overlay_over_active_suffix(
    monkeypatch, regroup_dsn
):
    """A second, DIFFERENT overlay over a feature with an active overlay is
    rejected fail-closed.

    Behavior-change update of the legacy ``test_activate_rejects_existing_
    active_marker``: the legacy facade rejected on an ``ACTIVE_KEY`` *artifact*
    being present; the rewired CLI rejects because ``activate_overlay``'s
    ``get_active_overlay`` check finds a different ``active`` typed overlay row
    (``dag_regroup_other_overlay_active``). The "cannot re-activate over an
    already-active suffix" coverage is preserved.
    """

    feature_id = "feat-activate-active"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    # First activation succeeds and flips the overlay row to active.
    first = await dag_regroup.command_activate(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            from_group=45,
            skip_safety=False,
        )
    )
    assert first["ok"] is True

    # Stage a DIFFERENT overlay (a different derived order ⇒ a different
    # overlay_id) as the draft, then re-activate. activate_overlay rejects
    # because a different overlay is already active.
    conn = await asyncpg.connect(regroup_dsn)
    try:
        # A draft whose first wave is the SAME shape but whose derived order
        # places BACKEND-A alone vs the canonical: re-seed the draft as the
        # canonical regroup but with a hand-edited single-task-per-wave order
        # so the derived order — and thus overlay_id — differs.
        alt_draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        # Reverse two independent waves to force a distinct derived order.
        order = [list(w) for w in alt_draft.dag.execution_order]
        order.reverse()
        alt = alt_draft.model_copy(
            update={"dag": alt_draft.dag.model_copy(update={"execution_order": order})}
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, alt.model_dump_json()
        )
    finally:
        await conn.close()

    with pytest.raises(RuntimeError):
        await dag_regroup.command_activate(
            SimpleNamespace(
                database_url=regroup_dsn,
                feature_id=feature_id,
                from_group=45,
                skip_safety=False,
            )
        )

    # Exactly one overlay is active (the first); the rejection changed nothing.
    conn = await asyncpg.connect(regroup_dsn)
    try:
        active_rows = await conn.fetch(
            "SELECT overlay_id FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND status = 'active'",
            feature_id,
        )
        assert len(active_rows) == 1
        assert active_rows[0]["overlay_id"] == first["overlay_id"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_activate_re_activation_of_active_overlay_is_rejected(
    monkeypatch, regroup_dsn
):
    """Re-running ``command_activate`` over an already-active overlay is
    rejected fail-closed.

    The typed ``activate_overlay`` (09c-1) requires the overlay row be
    ``staged`` — re-activating an already-``active`` overlay raises
    ``dag_regroup_overlay_not_staged`` (the 09c-1 contract, asserted by that
    slice's ``test_activation_idempotent_for_same_overlay``). The rewired CLI
    inherits that fail-closed behavior; the first activation's typed overlay
    row is unchanged by the rejected second run.
    """

    feature_id = "feat-activate-reactivate"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    args = SimpleNamespace(
        database_url=regroup_dsn,
        feature_id=feature_id,
        from_group=45,
        skip_safety=False,
    )
    first = await dag_regroup.command_activate(args)
    assert first["ok"] is True

    with pytest.raises(RuntimeError, match="dag_regroup_overlay_not_staged"):
        await dag_regroup.command_activate(args)

    conn = await asyncpg.connect(regroup_dsn)
    try:
        active_rows = await conn.fetch(
            "SELECT overlay_id, status FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND status = 'active'",
            feature_id,
        )
        assert len(active_rows) == 1
        assert active_rows[0]["overlay_id"] == first["overlay_id"]
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rollback_blocks_after_regrouped_first_wave_task_started(
    monkeypatch, regroup_dsn
):
    """Rollback is rejected fail-closed after a regrouped first-wave task
    started.

    Behavior-change update of the legacy ``_FakeConn`` test: the rewired
    ``command_rollback`` routes to the typed ``rollback_overlay``, which re-runs
    the doc-09 not-started forbidden-set check. A ``dag-task:*`` artifact for a
    first-derived-wave task makes the rollback reject; the typed overlay row
    stays ``active`` (no partial rolled-back projection). The not-started
    boundary coverage is preserved — now enforced by the typed transaction.
    """

    feature_id = "feat-rollback-block"
    base = ImplementationDAG(
        tasks=[
            ImplementationTask(
                id="LOW-UI",
                name="Low priority UI",
                description="small ui polish",
                files=["frontend/ui.tsx"],
            ),
            ImplementationTask(
                id="FAST-BACKEND",
                name="Backend artifact foundation",
                description="artifact backend foundation",
                files=["backend/artifacts.py"],
            ),
        ],
        execution_order=[*_empty_groups(45), ["LOW-UI"], ["FAST-BACKEND"]],
        num_teams=1,
        complete=True,
    )
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=46,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        # The regroup moves FAST-BACKEND ahead of LOW-UI; the effective first
        # derived wave is [FAST-BACKEND].
        first_wave_task = draft.dag.execution_order[0][0]
        assert first_wave_task == "FAST-BACKEND"
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    # Activate the overlay first.
    activation = await dag_regroup.command_activate(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            from_group=45,
            skip_safety=False,
        )
    )
    assert activation["ok"] is True

    # Seed a dag-task:* artifact for the moved first-derived-wave task — the
    # regrouped first wave has started.
    conn = await asyncpg.connect(regroup_dsn)
    try:
        await _seed_artifact(conn, feature_id, "dag-task:FAST-BACKEND", "{}")
    finally:
        await conn.close()

    with pytest.raises(RuntimeError, match="dag_regroup_first_wave_task_started"):
        await dag_regroup.command_rollback(
            SimpleNamespace(
                database_url=regroup_dsn,
                feature_id=feature_id,
                reason="test",
            )
        )

    # Fail-closed: the typed overlay row is still active (rollback wrote
    # nothing).
    conn = await asyncpg.connect(regroup_dsn)
    try:
        status = await conn.fetchval(
            "SELECT status FROM execution_regroup_overlays WHERE feature_id = $1",
            feature_id,
        )
        assert status == "active"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rollback_writes_rolled_back_marker_before_first_wave_starts(
    monkeypatch, regroup_dsn
):
    """Rollback before the first derived wave starts writes a rolled-back
    typed status + a rolled-back active marker + a typed rollback event.

    Behavior-change update of the legacy ``_FakeConn`` test: the rewired
    ``command_rollback`` routes to the typed ``rollback_overlay``. The legacy
    assertion ("the latest ACTIVE_KEY artifact has status rolled_back") is
    KEPT; the typed-row assertion (``execution_regroup_overlays.status =
    'rolled_back'``) and the typed-event assertion are ADDED.
    """

    feature_id = "feat-rollback-ok"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        await _seed_artifact(conn, feature_id, "dag-group:44", '{"group_idx":44}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=45,
            to_group=48,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    activation = await dag_regroup.command_activate(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            from_group=45,
            skip_safety=False,
        )
    )
    assert activation["ok"] is True

    result = await dag_regroup.command_rollback(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            reason="operator test rollback",
        )
    )

    assert result["ok"] is True
    assert result["reason"] == "operator test rollback"

    conn = await asyncpg.connect(regroup_dsn)
    try:
        # The typed overlay row flipped active -> rolled_back.
        status = await conn.fetchval(
            "SELECT status FROM execution_regroup_overlays WHERE feature_id = $1",
            feature_id,
        )
        assert status == "rolled_back"

        # The latest active-marker artifact carries status='rolled_back' (the
        # legacy-compat assertion, preserved).
        marker_value = await conn.fetchval(
            "SELECT value FROM artifacts WHERE feature_id = $1 AND key = $2 "
            "ORDER BY id DESC LIMIT 1",
            feature_id,
            activation["active_marker_key"],
        )
        marker = json.loads(marker_value)
        assert marker["status"] == "rolled_back"
        assert marker["reason"] == "operator test rollback"

        # The typed rollback event was written.
        event_type = await conn.fetchval(
            "SELECT event_type FROM events WHERE id = $1",
            result["rollback_event_id"],
        )
        assert event_type == "dag_regroup_overlay_rolled_back"
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_rollback_rejects_when_no_active_overlay(monkeypatch, regroup_dsn):
    """``command_rollback`` fails fast when no active typed overlay exists.

    The rewired facade looks up the active typed overlay first; with none, it
    raises before touching ``rollback_overlay``. Preserves the legacy facade's
    "no active regroup marker exists" rejection in typed terms.
    """

    feature_id = "feat-rollback-none"
    base = _base_dag()
    conn = await asyncpg.connect(regroup_dsn)
    try:
        await _seed_regroup_base(conn, feature_id, base)
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)

    with pytest.raises(RuntimeError, match="no active typed regroup overlay"):
        await dag_regroup.command_rollback(
            SimpleNamespace(
                database_url=regroup_dsn,
                feature_id=feature_id,
                reason="nothing to roll back",
            )
        )


# ── P3-D re-gating verification (Slice 09e-1b Chunk B, real Postgres) ───────
#
# P3-D: the 3 _implement_dag regroup call sites were gated on the legacy
# dag-regroup-active:g45-g73 artifact marker, so the typed RegroupOverlay-
# Resolver was reachable ONLY for an offset-45 overlay. Slice 09e-1b Chunk B
# re-gates those call sites on an active TYPED overlay row at ANY
# group_idx_offset. These tests exercise the typed path at a NON-45 offset.


def _small_dag(group_count: int) -> ImplementationDAG:
    """A DAG of ``group_count`` single-task groups (one task per group)."""

    tasks = [
        ImplementationTask(
            id=f"S{idx}",
            name=f"small task {idx}",
            description="isolated ui component work",
            files=[f"frontend/s_{idx}.tsx"],
        )
        for idx in range(group_count)
    ]
    return ImplementationDAG(
        tasks=tasks,
        execution_order=[[task.id] for task in tasks],
        num_teams=1,
        complete=True,
    )


@pytest.mark.asyncio
async def test_typed_overlay_at_non_45_offset_resolves(monkeypatch, regroup_dsn):
    """A typed regroup overlay at ``group_idx_offset != 45`` activates and the
    typed resolver applies it — the P3-D non-45 gap is closed.

    The legacy ``dag-regroup-active:g45-g73`` marker is ABSENT; the regroup is
    "in play" ONLY because an ``active`` typed overlay row exists (here at
    offset 3). Before Slice 09e-1b Chunk B the ``_implement_dag`` call sites
    would never reach the resolver for this overlay (they gated on the
    offset-45 legacy marker); the re-gating + ``_typed_regroup_active_overlay_
    offset`` make a typed overlay at any offset resolvable. This test confirms
    the typed activation + resolution work end-to-end at a non-45 offset (the
    resolver IS the component the re-gated call sites reach).
    """

    feature_id = "feat-non45"
    base = _small_dag(5)  # groups 0..4; regroup the suffix [3, 4].
    conn = await asyncpg.connect(regroup_dsn)
    try:
        dag_id, base_sha = await _seed_regroup_base(conn, feature_id, base)
        # The boundary checkpoint dag-group:2 must exist (offset 3 resumes
        # after checkpointed group 2). dag-regroup-active:g45-g73 is NOT seeded.
        await _seed_artifact(conn, feature_id, "dag-group:2", '{"group_idx":2}')
        draft = dag_regroup.build_staged_regroup(
            base,
            base_dag_artifact_id=dag_id,
            base_dag_sha256=base_sha,
            from_group=3,
            to_group=4,
            artifact_key=dag_regroup.DRAFT_KEY,
        )
        await _seed_artifact(
            conn, feature_id, dag_regroup.DRAFT_KEY, draft.model_dump_json()
        )
    finally:
        await conn.close()

    _patch_real_pool(monkeypatch, regroup_dsn)
    monkeypatch.setattr(dag_regroup, "_rss_mb_by_command", lambda _tokens: 0)

    result = await dag_regroup.command_activate(
        SimpleNamespace(
            database_url=regroup_dsn,
            feature_id=feature_id,
            from_group=3,
            skip_safety=False,
        )
    )
    assert result["ok"] is True
    # The slug is g3-g4 — a NON-45 offset; the legacy g45-g73 keys are absent.
    assert result["overlay_slug"] == "g3-g4"
    assert result["canonical_artifact_key"] == "dag-regroup:g3-g4"

    conn = await asyncpg.connect(regroup_dsn)
    try:
        # The legacy g45-g73 active marker artifact was never written — the
        # only thing making regroup "in play" is the typed overlay row.
        legacy_marker = await conn.fetchval(
            "SELECT 1 FROM artifacts WHERE feature_id = $1 AND key = $2 LIMIT 1",
            feature_id,
            dag_regroup.ACTIVE_KEY,
        )
        assert legacy_marker is None
        offset = await conn.fetchval(
            "SELECT group_idx_offset FROM execution_regroup_overlays "
            "WHERE feature_id = $1 AND status = 'active'",
            feature_id,
        )
        assert offset == 3
    finally:
        await conn.close()

    # _typed_regroup_active_overlay_offset (the P3-D re-gating probe) returns
    # the typed overlay's offset — proving the _implement_dag gates would now
    # trigger for this non-45 overlay (regroup_in_play=True, regroup_offset=3).
    probe_pool = await asyncpg.create_pool(regroup_dsn, min_size=1, max_size=4)
    try:
        runner = SimpleNamespace(
            artifacts=_RealArtifacts(probe_pool, feature_id),
            services={},
        )
        probed_offset = (
            await implementation_module._typed_regroup_active_overlay_offset(
                runner, feature_id
            )
        )
        assert probed_offset == 3

        # The typed resolver applies the overlay at its offset (group_idx=3) —
        # the component the re-gated _implement_dag call sites reach.
        feature = SimpleNamespace(id=feature_id, slug="non45")
        effective, failure, observation = (
            await implementation_module._resolve_active_regroup_before_group_dispatch(
                runner,
                feature,
                base,
                group_idx=3,
            )
        )
    finally:
        await probe_pool.close()
    assert failure == ""
    assert effective is not None
    assert observation["status"] == "applied"
    # The effective order is the base prefix [0, 3) + the overlay's derived
    # suffix; the root DAG is never overwritten.
    assert effective.execution_order[:3] == base.execution_order[:3]
    assert effective.execution_order[3:] == draft.dag.execution_order


@pytest.mark.asyncio
async def test_typed_overlay_offset_probe_none_without_overlay(
    monkeypatch, regroup_dsn
):
    """``_typed_regroup_active_overlay_offset`` returns None when no typed
    overlay is active — so a feature with no regroup keeps non-overlay
    behavior EXACTLY.

    P3-D re-gating: ``regroup_in_play`` is False and ``regroup_offset`` falls
    back to the legacy ``DAG_REGROUP_FROM_GROUP`` constant when this probe
    returns None, so the ``_implement_dag`` regroup gates are skipped exactly
    as before.
    """

    feature_id = "feat-no-overlay"
    base = _small_dag(5)
    conn = await asyncpg.connect(regroup_dsn)
    try:
        await _seed_regroup_base(conn, feature_id, base)
    finally:
        await conn.close()

    del monkeypatch  # this test does not patch create_pool
    probe_pool = await asyncpg.create_pool(regroup_dsn, min_size=1, max_size=4)
    try:
        runner = SimpleNamespace(
            artifacts=_RealArtifacts(probe_pool, feature_id),
            services={},
        )
        probed = await implementation_module._typed_regroup_active_overlay_offset(
            runner, feature_id
        )
    finally:
        await probe_pool.close()
    assert probed is None


@pytest.mark.asyncio
async def test_status_reports_rollback_blocked_without_dag_group_45(monkeypatch):
    feature_id = "feat-status-blocked"
    base = _base_dag()
    canonical = dag_regroup.build_staged_regroup(
        base,
        base_dag_artifact_id=123,
        base_dag_sha256=_base_hash(base),
        from_group=45,
        to_group=48,
        artifact_key=dag_regroup.CANONICAL_KEY,
    )
    conn = _FakeConn(feature_id=feature_id, dag=base, draft=canonical)
    conn.artifacts.extend([
        {
            "id": 125,
            "feature_id": feature_id,
            "key": dag_regroup.CANONICAL_KEY,
            "value": canonical.model_dump_json(),
            "created_at": "now",
        },
        {
            "id": 126,
            "feature_id": feature_id,
            "key": f"dag-task:{canonical.dag.execution_order[0][0]}",
            "value": "{}",
            "created_at": "now",
        },
    ])
    pool = _FakePool(conn)
    _patch_fake_pool(monkeypatch, pool)

    result = await dag_regroup.command_status(
        SimpleNamespace(database_url="postgresql://test", feature_id=feature_id)
    )

    assert result["dag_group_45_exists"] is False
    assert result["rollback_blocked"] is True
