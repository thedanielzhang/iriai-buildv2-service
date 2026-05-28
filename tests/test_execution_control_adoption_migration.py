from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from iriai_build_v2.execution_control.adoption_migration import (
    build_in_flight_adoption_preflight,
)
from iriai_build_v2.execution_control.atomic_landing import InFlightAdoptionRecord


class _ReadOnlyArtifacts:
    def __init__(self) -> None:
        self.records: dict[str, dict[str, Any]] = {}
        self.summaries: list[dict[str, Any]] = []
        self.put_calls: list[Any] = []
        self.delete_calls: list[Any] = []

    def add(self, key: str, value: Any, *, artifact_id: int) -> None:
        text = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        self.records[key] = {
            "id": artifact_id,
            "key": key,
            "value": text,
            "sha256": f"sha-{artifact_id}",
            "created_at": "2026-05-28T00:00:00Z",
        }

    async def get(self, key: str, *, feature: Any) -> Any | None:
        del feature
        record = self.records.get(key)
        return None if record is None else record["value"]

    async def get_record(self, key: str, *, feature: Any) -> dict[str, Any] | None:
        del feature
        return self.records.get(key)

    async def list_record_summaries(
        self,
        *,
        feature_id: str,
        prefixes: tuple[str, ...],
        limit: int,
        order: str,
    ) -> list[dict[str, Any]]:
        del feature_id, limit, order
        return [
            summary
            for summary in self.summaries
            if any(str(summary.get("key", "")).startswith(prefix) for prefix in prefixes)
        ]

    async def put(self, *args: Any, **kwargs: Any) -> None:
        self.put_calls.append((args, kwargs))
        raise AssertionError("migration preflight must not write artifacts")

    async def delete(self, *args: Any, **kwargs: Any) -> None:
        self.delete_calls.append((args, kwargs))
        raise AssertionError("migration preflight must not delete artifacts")


def _feature() -> SimpleNamespace:
    return SimpleNamespace(id="8ac124d6")


def _checkpoint(group_idx: int, task_id: str, *, status: str = "completed") -> dict[str, Any]:
    return {
        "group_idx": group_idx,
        "task_ids": [task_id],
        "results": [
            {
                "task_id": task_id,
                "summary": f"completed {task_id}",
                "status": status,
            }
        ],
        "verdict": "approved",
        "commit_hash": f"commit-{group_idx}",
    }


def _store_with_completed_boundary(boundary: int = 2) -> _ReadOnlyArtifacts:
    store = _ReadOnlyArtifacts()
    store.add("dag", {"tasks": [], "execution_order": [], "complete": True}, artifact_id=10)
    for group_idx in range(boundary + 1):
        store.add(
            f"dag-group:{group_idx}",
            _checkpoint(group_idx, f"TASK-{group_idx}"),
            artifact_id=100 + group_idx,
        )
    return store


@pytest.mark.asyncio
async def test_adoption_migration_preflight_produces_record_fields_for_completed_boundary() -> None:
    store = _store_with_completed_boundary(boundary=2)
    when = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)

    preflight = await build_in_flight_adoption_preflight(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=2,
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-1",
        active_regroup_artifact_ids=[501],
        active_regroup_metadata={"overlay_id": "overlay-1"},
        adopted_by="operator@example.test",
        now=when,
    )

    assert preflight.ready is True
    assert preflight.blockers == ()
    assert preflight.snapshot.completed_checkpoint_range == (0, 2)
    assert preflight.snapshot.next_effective_group_idx == 3
    record = InFlightAdoptionRecord(**preflight.adoption_record_fields)
    assert record.status == "adopted"
    assert record.feature_id == "8ac124d6"
    assert record.completed_checkpoint_range == (0, 2)
    assert record.next_effective_group_idx == 3
    assert record.active_regroup_artifact_ids == [501]
    assert store.put_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_adoption_migration_preflight_reports_missing_boundary_group() -> None:
    store = _store_with_completed_boundary(boundary=1)

    preflight = await build_in_flight_adoption_preflight(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=2,
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-1",
    )

    assert preflight.ready is False
    assert "missing_boundary_group" in {blocker.code for blocker in preflight.blockers}
    assert preflight.adoption_record_fields == {}


@pytest.mark.asyncio
async def test_adoption_migration_preflight_reports_non_approved_boundary() -> None:
    store = _store_with_completed_boundary(boundary=1)
    checkpoint = _checkpoint(1, "TASK-1")
    checkpoint["verdict"] = "rejected"
    store.add("dag-group:1", checkpoint, artifact_id=101)

    preflight = await build_in_flight_adoption_preflight(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-1",
    )

    assert "non_approved_boundary" in {blocker.code for blocker in preflight.blockers}


@pytest.mark.asyncio
async def test_adoption_migration_preflight_reports_incomplete_task_results() -> None:
    store = _store_with_completed_boundary(boundary=1)
    store.add("dag-group:1", _checkpoint(1, "TASK-1", status="partial"), artifact_id=101)

    preflight = await build_in_flight_adoption_preflight(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-1",
    )

    assert "incomplete_task_results" in {blocker.code for blocker in preflight.blockers}


@pytest.mark.asyncio
async def test_adoption_migration_preflight_reports_missing_root_dag_identity() -> None:
    store = _store_with_completed_boundary(boundary=1)
    store.records["dag"] = {
        "id": None,
        "key": "dag",
        "value": json.dumps({"tasks": []}),
        "sha256": "",
    }

    preflight = await build_in_flight_adoption_preflight(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-1",
    )

    assert "missing_root_dag_identity" in {blocker.code for blocker in preflight.blockers}


@pytest.mark.asyncio
async def test_adoption_migration_preflight_reports_ambiguous_regroup_state() -> None:
    store = _store_with_completed_boundary(boundary=1)
    store.summaries = [
        {"id": 201, "key": "dag-regroup-active:g45-g73"},
        {"id": 202, "key": "dag-regroup-active:g46-g90"},
    ]

    preflight = await build_in_flight_adoption_preflight(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        candidate_commit="candidate-commit",
        deploy_artifact_id="deploy-1",
    )

    assert "ambiguous_regroup_state" in {blocker.code for blocker in preflight.blockers}
