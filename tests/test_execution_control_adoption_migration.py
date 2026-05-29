from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from iriai_build_v2.execution_control.adoption_migration import (
    build_in_flight_adoption_preflight,
    build_post_adoption_repo_identity_bulk_repair_plan,
    build_post_adoption_repo_identity_repair_plan,
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


def _task(
    task_id: str,
    *,
    repo_path: str = "",
    path: str = "iriai-studio/src/app.ts",
) -> dict[str, Any]:
    return {
        "id": task_id,
        "name": task_id,
        "description": f"Implement {task_id}",
        "repo_path": repo_path,
        "file_scope": [{"path": path, "action": "modify"}],
        "acceptance_criteria": [{"description": f"{task_id} works"}],
    }


def _post_adoption_store() -> _ReadOnlyArtifacts:
    store = _ReadOnlyArtifacts()
    root_dag = {
        "tasks": [
            _task("TASK-old", repo_path="iriai-studio"),
            _task("TASK-9-3"),
            _task("TASK-explicit", repo_path="iriai-studio"),
            _task("TASK-future", path="iriai-studio/src/future.ts"),
        ],
        "execution_order": [["TASK-old"], ["TASK-9-3", "TASK-explicit"], ["TASK-future"]],
    }
    regroup_dag = {
        "tasks": json.loads(json.dumps(root_dag["tasks"])),
        "execution_order": [["TASK-9-3", "TASK-explicit"], ["TASK-future"]],
    }
    store.add("dag", root_dag, artifact_id=10)
    store.add(
        "execution-control-adoption:8ac124d6",
        {
            "status": "adopted",
            "feature_id": "8ac124d6",
            "completed_checkpoint_range": [0, 1],
            "next_effective_group_idx": 2,
        },
        artifact_id=20,
    )
    store.add(
        "dag-regroup:test",
        {
            "dag": regroup_dag,
            "group_idx_offset": 2,
            "base_dag_artifact_id": 10,
            "base_dag_sha256": "sha-10",
        },
        artifact_id=30,
    )
    store.add(
        "dag-regroup-rollback:test",
        {
            "group_idx_offset": 2,
            "base_dag_artifact_id": 10,
            "base_dag_sha256": "sha-10",
            "dag": {"execution_order": root_dag["execution_order"]},
        },
        artifact_id=31,
    )
    store.add(
        "dag-regroup-active:test",
        {
            "status": "active",
            "canonical_artifact_key": "dag-regroup:test",
            "canonical_artifact_id": 30,
            "regroup_sha256": "sha-30",
            "rollback_artifact_key": "dag-regroup-rollback:test",
            "rollback_artifact_id": 31,
            "base_dag_artifact_id": 10,
            "base_dag_sha256": "sha-10",
            "group_idx_offset": 2,
        },
        artifact_id=32,
    )
    store.summaries = [{"id": 32, "key": "dag-regroup-active:test"}]
    store.add(
        "workspace-authority-registry:g2",
        {
            "registry": {
                "registry_digest": "registry-digest",
                "repos": [
                    {
                        "repo_id": "repo-studio",
                        "workspace_relative_path": "iriai-studio",
                        "writable_task_ids": ["TASK-9-3", "TASK-explicit"],
                        "read_only_task_ids": [],
                    },
                    {
                        "repo_id": "repo-docs",
                        "workspace_relative_path": "docs",
                        "writable_task_ids": [],
                        "read_only_task_ids": [],
                    },
                ],
            },
        },
        artifact_id=40,
    )
    return store


async def _prefix_registry_builder(
    *,
    feature_id: str,
    group_idx: int,
    tasks: list[Any],
    seed_registry: dict[str, Any],
    feature_root: str,
    workspace_root: str,
) -> dict[str, Any]:
    del group_idx, feature_root, workspace_root
    repos = json.loads(json.dumps(seed_registry["repos"]))
    for repo in repos:
        repo["writable_task_ids"] = []
        repo["read_only_task_ids"] = []
    for task in tasks:
        task_id = str(task.get("id"))
        paths = [
            str(scope.get("path"))
            for scope in task.get("file_scope", [])
            if isinstance(scope, dict)
        ]
        for repo in repos:
            repo_path = repo["workspace_relative_path"]
            if any(path == repo_path or path.startswith(f"{repo_path}/") for path in paths):
                repo["writable_task_ids"].append(task_id)
                break
    return {
        "feature_id": feature_id,
        "feature_root": seed_registry.get("feature_root", "/tmp/feature/repos"),
        "repos": repos,
        "registry_digest": f"registry-{feature_id}",
    }


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


@pytest.mark.asyncio
async def test_post_adoption_repo_identity_repair_resolves_missing_repo_path() -> None:
    store = _post_adoption_store()

    plan = await build_post_adoption_repo_identity_repair_plan(
        feature=_feature(),
        artifact_store=store,
        group_idx=2,
        boundary_group_idx=1,
        regroup_active_key="dag-regroup-active:test",
    )

    assert plan.ready is True
    assert plan.changed_task_ids == ("TASK-9-3",)
    assert plan.resolved_repo_paths_by_task == {"TASK-9-3": "iriai-studio"}
    assert plan.current_group_task_ids == ("TASK-9-3", "TASK-explicit")
    updated_tasks = {task["id"]: task for task in plan.root_dag["tasks"]}
    assert updated_tasks["TASK-9-3"]["repo_path"] == "iriai-studio"
    assert updated_tasks["TASK-explicit"]["repo_path"] == "iriai-studio"
    assert {item["task_id"] for item in plan.post_boundary_missing_repo_identity_before} == {
        "TASK-9-3",
        "TASK-future",
    }
    assert {item["task_id"] for item in plan.post_boundary_missing_repo_identity_after} == {
        "TASK-future",
    }
    assert store.put_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_post_adoption_repo_identity_repair_plan_uses_read_only_registry_claims() -> None:
    store = _post_adoption_store()
    registry = json.loads(store.records["workspace-authority-registry:g2"]["value"])
    registry["registry"]["repos"][0]["writable_task_ids"] = ["TASK-explicit"]
    registry["registry"]["repos"][0]["read_only_task_ids"] = ["TASK-9-3"]
    store.add("workspace-authority-registry:g2", registry, artifact_id=41)

    plan = await build_post_adoption_repo_identity_repair_plan(
        feature=_feature(),
        artifact_store=store,
        group_idx=2,
        boundary_group_idx=1,
        regroup_active_key="dag-regroup-active:test",
    )

    assert plan.ready is True
    assert plan.resolved_repo_paths_by_task == {"TASK-9-3": "iriai-studio"}


@pytest.mark.asyncio
async def test_post_adoption_repo_identity_repair_plan_materializes_append_only_values() -> None:
    store = _post_adoption_store()
    old_root_value = store.records["dag"]["value"]
    old_regroup_value = store.records["dag-regroup:test"]["value"]

    plan = await build_post_adoption_repo_identity_repair_plan(
        feature=_feature(),
        artifact_store=store,
        group_idx=2,
        boundary_group_idx=1,
        regroup_active_key="dag-regroup-active:test",
    )

    root_value = plan.root_dag_value()
    root_sha = plan.root_dag_sha256()
    regroup_value = plan.regroup_value(
        new_root_dag_artifact_id=1000,
        new_root_dag_sha256=root_sha,
    )
    regroup_sha = "sha-new-regroup"
    rollback_value = plan.rollback_value(
        new_root_dag_artifact_id=1000,
        new_root_dag_sha256=root_sha,
        created_at="2026-05-28T12:00:00Z",
    )
    active_value = plan.active_marker_value(
        new_root_dag_artifact_id=1000,
        new_root_dag_sha256=root_sha,
        new_regroup_artifact_id=1001,
        new_regroup_sha256=regroup_sha,
        new_rollback_artifact_id=1002,
        created_at="2026-05-28T12:00:00Z",
    )
    audit_value = plan.audit_value(
        new_root_dag_artifact_id=1000,
        new_regroup_artifact_id=1001,
        new_rollback_artifact_id=1002,
        new_active_marker_artifact_id=1003,
        created_at="2026-05-28T12:00:00Z",
    )

    assert store.records["dag"]["value"] == old_root_value
    assert store.records["dag-regroup:test"]["value"] == old_regroup_value
    assert json.loads(root_value)["tasks"][1]["repo_path"] == "iriai-studio"
    regroup_body = json.loads(regroup_value)
    assert regroup_body["base_dag_artifact_id"] == 1000
    assert regroup_body["base_dag_sha256"] == root_sha
    assert regroup_body["dag"]["tasks"][1]["repo_path"] == "iriai-studio"
    assert json.loads(rollback_value)["base_dag_artifact_id"] == 1000
    active_body = json.loads(active_value)
    assert active_body["canonical_artifact_id"] == 1001
    assert active_body["regroup_sha256"] == regroup_sha
    assert active_body["rollback_artifact_id"] == 1002
    audit_body = json.loads(audit_value)
    assert audit_body["old_artifacts"]["dag"] == 10
    assert audit_body["new_artifacts"] == {
        "dag": 1000,
        "regroup": 1001,
        "rollback": 1002,
        "active_marker": 1003,
    }
    assert audit_body["policy"]["append_only"] is True


@pytest.mark.asyncio
async def test_post_adoption_repo_identity_repair_plan_blocks_unmapped_registry_claim() -> None:
    store = _post_adoption_store()
    registry = json.loads(store.records["workspace-authority-registry:g2"]["value"])
    registry["registry"]["repos"][0]["writable_task_ids"] = ["TASK-explicit"]
    store.add("workspace-authority-registry:g2", registry, artifact_id=41)

    plan = await build_post_adoption_repo_identity_repair_plan(
        feature=_feature(),
        artifact_store=store,
        group_idx=2,
        boundary_group_idx=1,
        regroup_active_key="dag-regroup-active:test",
    )

    assert plan.ready is False
    assert "unmapped_repo_identity" in {blocker.code for blocker in plan.blockers}
    assert store.put_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_post_adoption_repo_identity_repair_plan_blocks_ambiguous_registry_claim() -> None:
    store = _post_adoption_store()
    registry = json.loads(store.records["workspace-authority-registry:g2"]["value"])
    registry["registry"]["repos"][1]["writable_task_ids"] = ["TASK-9-3"]
    store.add("workspace-authority-registry:g2", registry, artifact_id=41)

    plan = await build_post_adoption_repo_identity_repair_plan(
        feature=_feature(),
        artifact_store=store,
        group_idx=2,
        boundary_group_idx=1,
        regroup_active_key="dag-regroup-active:test",
    )

    assert plan.ready is False
    assert "ambiguous_repo_identity" in {blocker.code for blocker in plan.blockers}
    assert store.put_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_post_adoption_repo_identity_repair_requires_adoption_marker() -> None:
    store = _post_adoption_store()
    store.records.pop("execution-control-adoption:8ac124d6")

    plan = await build_post_adoption_repo_identity_repair_plan(
        feature=_feature(),
        artifact_store=store,
        group_idx=2,
        boundary_group_idx=1,
        regroup_active_key="dag-regroup-active:test",
    )

    assert plan.ready is False
    assert "missing_adoption_marker" in {blocker.code for blocker in plan.blockers}


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_resolves_multiple_future_groups() -> None:
    store = _post_adoption_store()

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        registry_builder=_prefix_registry_builder,
    )

    assert plan.ready is True
    assert set(plan.changed_task_ids) == {"TASK-9-3", "TASK-future"}
    assert set(plan.deterministic_task_ids) == {"TASK-9-3", "TASK-future"}
    assert plan.reviewed_task_ids == ()
    assert plan.post_boundary_missing_repo_identity_after == ()
    tasks = {task["id"]: task for task in plan.root_dag["tasks"]}
    assert tasks["TASK-9-3"]["repo_path"] == "iriai-studio"
    assert tasks["TASK-future"]["repo_path"] == "iriai-studio"
    assert set(plan.group_registry_projections) == {2, 3}
    assert store.put_calls == []
    assert store.delete_calls == []


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_requires_review_for_zero_claim_task() -> None:
    store = _post_adoption_store()
    root = json.loads(store.records["dag"]["value"])
    root["tasks"][3]["file_scope"] = [{"path": "src/future.py", "action": "modify"}]
    store.add("dag", root, artifact_id=11)

    blocked = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        registry_builder=_prefix_registry_builder,
    )

    assert blocked.ready is False
    assert "repo_identity_review_required" in {blocker.code for blocker in blocked.blockers}

    repaired = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        reviewed_records=[
            {
                "task_id": "TASK-future",
                "repo_path": "iriai-studio",
                "evidence_type": "reviewed_unprefixed_repo_path",
                "evidence_paths": ["src/future.py"],
                "reviewer_id": "unit-test",
                "confidence": 0.95,
            }
        ],
        registry_builder=_prefix_registry_builder,
    )

    assert repaired.ready is True
    assert "TASK-future" in repaired.reviewed_task_ids
    assert repaired.resolved_repo_paths_by_task["TASK-future"] == "iriai-studio"


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_rejects_ambiguous_claims() -> None:
    store = _post_adoption_store()

    async def ambiguous_builder(**kwargs: Any) -> dict[str, Any]:
        registry = await _prefix_registry_builder(**kwargs)
        for repo in registry["repos"]:
            repo["writable_task_ids"].append("TASK-9-3")
        return registry

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=2,
        regroup_active_key="dag-regroup-active:test",
        registry_builder=ambiguous_builder,
    )

    assert plan.ready is False
    assert "ambiguous_repo_identity" in {blocker.code for blocker in plan.blockers}


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_splits_cross_repo_task() -> None:
    store = _post_adoption_store()
    root = json.loads(store.records["dag"]["value"])
    cross_task = _task(
        "TASK-cross",
        path="iriai-studio/src/ui.ts",
    )
    cross_task["file_scope"].append(
        {"path": "docs/reference.md", "action": "modify"}
    )
    cross_task["files"] = ["iriai-studio/src/ui.ts", "docs/reference.md"]
    root["tasks"].append(cross_task)
    root["execution_order"].append(["TASK-cross"])
    store.add("dag", root, artifact_id=11)
    regroup = json.loads(store.records["dag-regroup:test"]["value"])
    regroup["dag"]["tasks"].append(cross_task)
    regroup["dag"]["execution_order"].append(["TASK-cross"])
    store.add("dag-regroup:test", regroup, artifact_id=33)

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=4,
        regroup_active_key="dag-regroup-active:test",
        reviewed_records=[
            {
                "task_id": "TASK-cross",
                "reviewer_id": "unit-test",
                "confidence": 1.0,
                "evidence_type": "cross_repo_file_scope",
                "evidence_paths": [
                    "iriai-studio/src/ui.ts",
                    "docs/reference.md",
                ],
                "split_tasks": [
                    {
                        "task_id": "TASK-cross-studio",
                        "repo_path": "iriai-studio",
                        "file_scope_paths": ["iriai-studio/src/ui.ts"],
                    },
                    {
                        "task_id": "TASK-cross-docs",
                        "repo_path": "docs",
                        "file_scope_paths": ["docs/reference.md"],
                    },
                ],
            }
        ],
        registry_builder=_prefix_registry_builder,
    )

    assert plan.ready is True
    assert plan.split_original_task_ids == ("TASK-cross",)
    assert set(plan.created_split_task_ids) == {"TASK-cross-studio", "TASK-cross-docs"}
    task_ids = {task["id"] for task in plan.root_dag["tasks"]}
    assert "TASK-cross" not in task_ids
    assert {"TASK-cross-studio", "TASK-cross-docs"}.issubset(task_ids)
    local_group = plan.regroup_projection["dag"]["execution_order"][2]
    assert local_group == ["TASK-cross-studio", "TASK-cross-docs"]


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_moves_task_in_active_regroup_order() -> None:
    store = _post_adoption_store()

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        reviewed_records=[
            {
                "task_id": "TASK-9-3",
                "target_group_idx": 3,
                "evidence_type": "same_wave_read_write_conflict_break",
                "evidence_paths": ["iriai-studio/src/app.ts"],
                "reviewer_id": "unit-test",
                "confidence": 1.0,
            }
        ],
        registry_builder=_prefix_registry_builder,
    )

    assert plan.ready is True
    assert plan.moved_task_ids == ("TASK-9-3",)
    assert plan.regroup_projection["dag"]["execution_order"] == [
        ["TASK-explicit"],
        ["TASK-future", "TASK-9-3"],
    ]
    assert plan.root_dag["execution_order"] == [
        ["TASK-old"],
        ["TASK-9-3", "TASK-explicit"],
        ["TASK-future"],
    ]


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_rejects_group_move_that_mixes_hard_barriers() -> None:
    store = _post_adoption_store()
    regroup = json.loads(store.records["dag-regroup:test"]["value"])
    regroup["barriers"] = [
        {"id": "studio", "hard": True, "task_ids": ["TASK-9-3", "TASK-explicit"]},
        {"id": "future", "hard": True, "task_ids": ["TASK-future"]},
    ]
    store.add("dag-regroup:test", regroup, artifact_id=33)

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        reviewed_records=[
            {
                "task_id": "TASK-9-3",
                "target_group_idx": 3,
                "evidence_type": "same_wave_read_write_conflict_break",
                "evidence_paths": ["iriai-studio/src/app.ts"],
                "reviewer_id": "unit-test",
                "confidence": 1.0,
            }
        ],
        registry_builder=_prefix_registry_builder,
    )

    assert plan.ready is False
    blocker_codes = {blocker.code for blocker in plan.blockers}
    assert "dag_regroup_barrier_violation" in blocker_codes
    [barrier_blocker] = [
        blocker for blocker in plan.blockers
        if blocker.code == "dag_regroup_barrier_violation"
    ]
    assert barrier_blocker.details["violations"][0]["new_group"] == 3
    assert barrier_blocker.details["violations"][0]["barriers"] == ["future", "studio"]


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_recomputes_original_to_new_mapping_after_move() -> None:
    store = _post_adoption_store()
    adoption = json.loads(store.records["execution-control-adoption:8ac124d6"]["value"])
    adoption["completed_checkpoint_range"] = [0, 0]
    adoption["next_effective_group_idx"] = 1
    store.add("execution-control-adoption:8ac124d6", adoption, artifact_id=21)
    regroup = json.loads(store.records["dag-regroup:test"]["value"])
    regroup["group_idx_offset"] = 1
    regroup["dag"]["execution_order"].append([])
    store.add("dag-regroup:test", regroup, artifact_id=33)
    rollback = json.loads(store.records["dag-regroup-rollback:test"]["value"])
    rollback["group_idx_offset"] = 1
    store.add("dag-regroup-rollback:test", rollback, artifact_id=34)
    active = json.loads(store.records["dag-regroup-active:test"]["value"])
    active["group_idx_offset"] = 1
    store.add("dag-regroup-active:test", active, artifact_id=35)
    seed = json.loads(store.records["workspace-authority-registry:g2"]["value"])
    store.add("workspace-authority-registry:g1", seed, artifact_id=41)

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=0,
        first_group_idx=1,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        reviewed_records=[
            {
                "task_id": "TASK-9-3",
                "target_group_idx": 3,
                "evidence_type": "same_wave_read_write_conflict_break",
                "evidence_paths": ["iriai-studio/src/app.ts"],
                "reviewer_id": "unit-test",
                "confidence": 1.0,
            }
        ],
        registry_builder=_prefix_registry_builder,
    )

    assert plan.ready is True
    assert plan.regroup_projection["original_execution_order"] == [
        ["TASK-9-3", "TASK-explicit"],
        ["TASK-future"],
    ]
    assert plan.regroup_projection["original_to_new_group_mapping"] == {
        "1": [1, 3],
        "2": [2],
    }
    assert plan.regroup_validation["approved"] is True


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_normalizes_legacy_files_for_structured_scope() -> None:
    store = _post_adoption_store()
    root = json.loads(store.records["dag"]["value"])
    root["tasks"][1]["files"] = ["iriai-studio/src/app.ts", "iriai-studio/src/extra.ts"]
    store.add("dag", root, artifact_id=11)
    regroup = json.loads(store.records["dag-regroup:test"]["value"])
    regroup["dag"]["tasks"][1]["files"] = ["iriai-studio/src/app.ts", "iriai-studio/src/extra.ts"]
    store.add("dag-regroup:test", regroup, artifact_id=33)

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        registry_builder=_prefix_registry_builder,
    )

    assert plan.ready is True
    assert plan.normalized_legacy_files_task_ids == ("TASK-9-3",)
    tasks = {task["id"]: task for task in plan.root_dag["tasks"]}
    regroup_tasks = {task["id"]: task for task in plan.regroup_projection["dag"]["tasks"]}
    assert tasks["TASK-9-3"]["files"] == []
    assert regroup_tasks["TASK-9-3"]["files"] == []


@pytest.mark.asyncio
async def test_bulk_repo_identity_repair_materializes_append_only_values() -> None:
    store = _post_adoption_store()
    old_root_value = store.records["dag"]["value"]

    plan = await build_post_adoption_repo_identity_bulk_repair_plan(
        feature=_feature(),
        artifact_store=store,
        boundary_group_idx=1,
        first_group_idx=2,
        last_group_idx=3,
        regroup_active_key="dag-regroup-active:test",
        registry_builder=_prefix_registry_builder,
    )

    root_value = plan.root_dag_value()
    root_sha = plan.root_dag_sha256()
    regroup_value = plan.regroup_value(
        new_root_dag_artifact_id=1000,
        new_root_dag_sha256=root_sha,
    )
    active_value = plan.active_marker_value(
        new_root_dag_artifact_id=1000,
        new_root_dag_sha256=root_sha,
        new_regroup_artifact_id=1001,
        new_regroup_sha256="sha-regroup",
        new_rollback_artifact_id=1002,
        created_at="2026-05-28T12:00:00Z",
    )
    audit_value = plan.audit_value(
        new_root_dag_artifact_id=1000,
        new_regroup_artifact_id=1001,
        new_rollback_artifact_id=1002,
        new_active_marker_artifact_id=1003,
        review_artifact_id=1004,
        registry_artifact_ids_by_group={2: 1005, 3: 1006},
        created_at="2026-05-28T12:00:00Z",
    )

    assert store.records["dag"]["value"] == old_root_value
    assert json.loads(root_value)["tasks"][1]["repo_path"] == "iriai-studio"
    assert json.loads(regroup_value)["base_dag_artifact_id"] == 1000
    assert json.loads(active_value)["canonical_artifact_id"] == 1001
    audit = json.loads(audit_value)
    assert audit["repair_kind"] == "post_adoption_repo_identity_bulk"
    assert audit["new_artifacts"]["registries_by_group"] == {"2": 1005, "3": 1006}
    assert audit["regroup_validation"]["approved"] is True
